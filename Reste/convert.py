"""Conversion image/video/audio via ffmpeg. Un seul binaire externe requis: ffmpeg (dans le PATH)."""
import json
import os
import pathlib
import shutil
import subprocess
import threading
import time

from PIL import Image, ImageFilter, ImageOps

# les fichiers viennent de l'utilisateur ou de nous, pas du web: le garde-fou "bombe zip" de
# Pillow (89 Mpx) refusait de relire une image qu'on venait nous-memes d'agrandir. On ne le
# supprime pas pour autant: 1 Gpx (~3 Go en RVB) laisse passer tout agrandissement realiste
# et arrete encore un fichier fabrique pour saturer la memoire.
Image.MAX_IMAGE_PIXELS = 1_000_000_000

FORMATS: dict[str, list[str]] = {
    "image": ["png", "jpg", "jpeg", "webp", "avif", "gif", "bmp", "tiff"],
    "video": ["mp4", "webm", "mkv", "mov", "avi", "gif"],
    "audio": ["mp3", "wav", "flac", "ogg", "m4a", "aac", "opus"],
}

# formats image incapables de contenir plusieurs frames. Une source animee doit y etre reduite
# a sa 1re image, sinon ffmpeg refuse d'ecrire: "-update option or a sequence pattern?"
FIXES = ("png", "jpg", "jpeg", "bmp", "tiff")

TIMEOUT = 3600


class ConversionError(Exception):
    pass


class Annule(ConversionError):
    """L'utilisateur a demandé l'arrêt : ce n'est pas une panne, l'interface ne l'affiche
    pas comme un échec."""


_FFMPEG = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]

_PROCS: set = set()           # ffmpeg en cours, pour pouvoir les tuer
_ANNULE = threading.Event()
# ponytail: drapeau global, l'appli ne lance qu'un traitement a la fois (garde par self.occupe).
# A passer en jeton par tache le jour ou plusieurs conversions tourneraient en parallele.


def annuler():
    """Arrête le traitement en cours: tue les ffmpeg lancés et fait échouer l'étape suivante."""
    _ANNULE.set()
    for p in list(_PROCS):
        try:
            p.kill()
        except Exception:
            pass


def reprendre():
    _ANNULE.clear()


def annulation_demandee() -> bool:
    return _ANNULE.is_set()


def _stop():
    if _ANNULE.is_set():
        raise Annule("annulé")


def anime(path) -> bool:
    """Ce fichier contient-il plus d'une image ? seek(1) ne decode qu'une frame, la ou
    n_frames oblige Pillow a parcourir tout le GIF."""
    try:
        with Image.open(path) as im:
            im.seek(1)
        return True
    except Exception:
        return False


def media_kind(path) -> str | None:
    """Famille d'un fichier. Un GIF anime compte comme une VIDEO: c'est ce qui decide des
    formats cibles proposes et des options ffmpeg. Traite comme une image fixe, il perdait
    toutes ses frames sauf la premiere des qu'on le redimensionnait."""
    ext = pathlib.Path(path).suffix.lower().lstrip(".")
    if ext == "gif" and anime(path):
        return "video"
    for kind, exts in FORMATS.items():
        if ext in exts:
            return kind
    return None


def a_du_son(path) -> bool:
    """Y a-t-il une piste audio ? Sans ca l'interface proposait « transformer en mp3 » sur une
    video muette, et ffmpeg repondait « Output file does not contain any stream »."""
    if shutil.which("ffprobe") is None:
        return True          # dans le doute on propose: le message d'erreur reste explicite
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                            "stream=index", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return True
    # returncode != 0 = fichier illisible ou absent, pas « pas de son »: on propose quand meme
    return r.returncode != 0 or bool(r.stdout.strip())


def targets_for(path) -> list[str]:
    kind = media_kind(path)
    if kind == "image":
        return list(FORMATS["image"])
    if kind == "video":
        if pathlib.Path(path).suffix.lower() == ".gif":
            # un gif anime n'a pas de piste son, mais garde ses cibles image en plus des
            # cibles video (on en tire alors la 1re frame)
            return list(dict.fromkeys(FORMATS["image"] + FORMATS["video"]))
        return FORMATS["video"] + (FORMATS["audio"] if a_du_son(path) else [])
    if kind == "audio":
        return list(FORMATS["audio"])
    return []


# qualité: low/normal/high -> valeurs ffmpeg. -q:v (jpeg) et -crf (avif): petit = meilleur.
_Q_IMG = {"low": 12, "normal": 5, "high": 2}
# libwebp prend l'echelle INVERSE, de 0 a 100, grand = meilleur. Lui envoyer _Q_IMG donnait
# une qualite de 2/100 sur "Haute": le meilleur reglage produisait le pire fichier (35,1 dB
# de PSNR, contre 49,9 dB pour un webp normal) et "Rapide" (12) battait "Haute" (2).
_Q_WEBP = {"low": 65, "normal": 82, "high": 95}
_Q_VID = {"low": 32, "normal": 23, "high": 18}
_Q_AUD = {"low": "96k", "normal": "192k", "high": "320k"}


def _image_args(dst_ext, quality):
    if dst_ext in ("jpg", "jpeg"):
        return ["-q:v", str(_Q_IMG[quality])]
    if dst_ext == "webp":
        return ["-quality", str(_Q_WEBP[quality])]
    if dst_ext == "avif":
        return ["-crf", str(_Q_IMG[quality])]
    return []  # png/bmp/tiff/gif: pas de réglage qualité, lossless ou trivial


_PAIR = "scale=trunc(iw/2)*2:trunc(ih/2)*2"   # x264 et vp9 refusent un cote impair


def _video_args(dst_ext, quality):
    q = str(_Q_VID[quality])
    if dst_ext in ("mp4", "mkv", "mov", "avi", "webm"):
        # yuv420p + cotes pairs, toujours: un GIF se decode en bgra et peut avoir un cote
        # impair, que ni x264 ni vp9 n'acceptent (vp9 echouait sec sur « Could not open
        # encoder »), et un mp4 en yuv444p ne se lit pas sur un telephone.
        # _strip_vf() retire ce -vf quand resize() pose le sien.
        args = ["-vf", _PAIR, "-pix_fmt", "yuv420p"]
        if dst_ext == "webm":
            return args + ["-c:v", "libvpx-vp9", "-crf", q, "-b:v", "0", "-c:a", "libopus"]
        # de l'AAC dans un conteneur AVI n'est lu correctement quasiment nulle part
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", q,
                 "-c:a", "libmp3lame" if dst_ext == "avi" else "aac"]
        if dst_ext in ("mp4", "mov"):
            args += ["-movflags", "+faststart"]   # lisible sans attendre la fin du fichier
        return args
    if dst_ext == "gif":
        # ponytail: pas de palettegen 2 passes, fps/scale simples suffisent pour un usage courant
        return ["-vf", "fps=12,scale=480:-1:flags=lanczos", "-an"]
    return []


def _audio_args(dst_ext, quality):
    b = _Q_AUD[quality]
    if dst_ext == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", b]
    if dst_ext in ("aac", "m4a"):
        return ["-c:a", "aac", "-b:a", b]
    if dst_ext == "ogg":
        return ["-c:a", "libvorbis", "-b:a", b]
    if dst_ext == "opus":
        return ["-c:a", "libopus", "-b:a", b]
    if dst_ext == "wav":
        return ["-c:a", "pcm_s16le"]
    if dst_ext == "flac":
        return ["-c:a", "flac"]
    return []


def _build_cmd(src, dst, src_kind, dst_ext, quality):
    args = _FFMPEG + ["-i", str(src)]
    # audio d'abord, puis la cible decide: gif appartient aux deux familles image et video
    if dst_ext in FORMATS["audio"]:
        if src_kind == "video":
            args.append("-vn")
        args += _audio_args(dst_ext, quality)
    elif dst_ext in FIXES:
        if src_kind == "video":
            args += ["-update", "1", "-frames:v", "1"]   # 1re frame d'une video ou d'un gif anime
        args += _image_args(dst_ext, quality)
    elif src_kind == "video" and dst_ext in FORMATS["video"]:
        args += _video_args(dst_ext, quality)
    else:
        args += _image_args(dst_ext, quality)   # webp/avif animes: ffmpeg garde les frames
    args.append(str(dst))
    return args


def _tail(stderr) -> str:
    lines = [l for l in (stderr or "").strip().splitlines() if l.strip()]
    return "\n".join(lines[-10:])[-400:]


def _verifier(dst, returncode, stderr) -> pathlib.Path:
    dst = pathlib.Path(dst)
    if _ANNULE.is_set():
        dst.unlink(missing_ok=True)      # pas de fichier a moitie ecrit apres une annulation
        raise Annule("annulé")
    if returncode != 0:
        raise ConversionError(f"ffmpeg a échoué (code {returncode}): {_tail(stderr)}")
    if not dst.exists() or dst.stat().st_size == 0:
        raise ConversionError(f"fichier de sortie absent ou vide: {_tail(stderr)}")
    return dst


def _ffmpeg(cmd, dst, duration=None, progress=None) -> pathlib.Path:
    """Lance ffmpeg, en suivant sa progression si on connaît la durée.

    stderr est vidé par un thread pendant qu'on lit stdout: sinon un ffmpeg bavard remplit
    le tuyau stderr (64 Ko), se bloque, et la boucle de lecture attend un stdout qui n'avance
    plus. Le délai maximum est tenu par un Timer, pas par proc.wait(timeout=...): on n'arrivait
    à ce wait() qu'une fois ffmpeg terminé, donc TIMEOUT ne protégeait de rien."""
    _stop()
    suivi = bool(duration and progress)
    if suivi:   # -progress pipe:1 juste avant le fichier de sortie (dernier élément de cmd)
        cmd = cmd[:-1] + ["-progress", "pipe:1", "-nostats"] + cmd[-1:]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, errors="replace")
    _PROCS.add(proc)
    err, expire = [], []
    lecteur = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)
    lecteur.start()
    alarme = threading.Timer(TIMEOUT, lambda: (expire.append(1), proc.kill()))
    alarme.start()
    try:
        for ligne in proc.stdout:
            if not suivi:
                continue
            ligne = ligne.strip()
            if ligne.startswith(("out_time_us=", "out_time_ms=")):   # les deux sont en µs
                val = ligne.split("=", 1)[1]
                if val not in ("N/A", ""):
                    progress(max(0.0, min(1.0, int(val) / (duration * 1_000_000))))
        code = proc.wait()
    finally:
        alarme.cancel()
        _PROCS.discard(proc)
        lecteur.join(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
    if expire and not _ANNULE.is_set():
        pathlib.Path(dst).unlink(missing_ok=True)
        raise ConversionError(f"opération trop longue (> {TIMEOUT}s)")
    dst = _verifier(dst, code, err[0] if err else "")
    if progress:
        progress(1.0)
    return dst


def convert(src, dst, quality="normal", progress=None) -> pathlib.Path:
    if quality not in ("low", "normal", "high"):
        raise ConversionError(f"qualité inconnue: {quality}")
    if shutil.which("ffmpeg") is None:
        raise ConversionError("ffmpeg introuvable dans le PATH")

    src, dst = pathlib.Path(src), pathlib.Path(dst)
    src_kind = media_kind(src)
    dst_ext = dst.suffix.lower().lstrip(".")
    if dst_ext not in targets_for(src):
        if src_kind == "video" and dst_ext in FORMATS["audio"]:
            raise ConversionError("cette vidéo n'a pas de piste son à extraire")
        raise ConversionError(f"format cible '.{dst_ext}' non supporté pour une source {src_kind or 'inconnue'}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = _build_cmd(src, dst, src_kind, dst_ext, quality)
    duration = probe(src).get("duration") if progress else None
    if progress and not duration:
        progress(0.5)   # pas de durée connue (image): pas de vrai pourcentage, juste avant/après
    return _ffmpeg(cmd, dst, duration, progress)


def probe(path) -> dict:
    """Inspecte un média via ffprobe. Ne lève jamais: renvoie des None si illisible/inconnu."""
    info = {"width": None, "height": None, "duration": None}
    if media_kind(path) == "image":
        # Pillow lit l'en-tete sans decoder: instantane, et ffprobe cale sur les tres grandes images
        try:
            with Image.open(path) as im:
                info["width"], info["height"] = im.size
            return info
        except Exception:
            pass
    if shutil.which("ffprobe") is None:
        return info
    cmd = ["ffprobe", "-v", "error", "-show_entries", "stream=width,height:format=duration",
           "-of", "json", str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return info
    if result.returncode != 0:
        return info
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return info
    for stream in data.get("streams", []):
        if "width" in stream and "height" in stream:
            info["width"], info["height"] = stream["width"], stream["height"]
            break
    duration = data.get("format", {}).get("duration")
    if duration is not None:
        try:
            info["duration"] = float(duration)
        except ValueError:
            pass
    return info


def _strip_vf(args):
    # retire un -vf déjà présent (ex: le fps/scale du gif de _video_args) pour ne garder que le nôtre
    args = list(args)
    if "-vf" in args:
        i = args.index("-vf")
        del args[i:i + 2]
    return args


def resize(src, dst, width=None, height=None, keep_ratio=True, progress=None) -> pathlib.Path:
    """Redimensionne une image, une vidéo ou un GIF animé.

    keep_ratio=True avec largeur ET hauteur : la LARGEUR l'emporte, la hauteur passée est
    ignorée (elle est recalculée). Pour imposer les deux, keep_ratio=False."""
    if width is None and height is None:
        raise ConversionError("resize: indiquer au moins une largeur ou une hauteur")
    if not keep_ratio and (width is None or height is None):
        raise ConversionError("resize: keep_ratio=False nécessite largeur ET hauteur")

    src, dst = pathlib.Path(src), pathlib.Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _stop()

    if media_kind(src) == "image":
        # Pillow: taille exacte demandee (ffmpeg -2 arrondit au pair), agrandissement inclus,
        # et lit les formats que ffmpeg refuse d'ecrire (heic/avif via pillow-heif)
        if progress:
            progress(0.05)
        try:
            img = Image.open(src)
            if keep_ratio:
                if width is not None:
                    height = max(1, round(img.height * width / img.width))
                else:
                    width = max(1, round(img.width * height / img.height))
            width, height = max(1, int(width)), max(1, int(height))
            if dst.suffix.lower() in (".jpg", ".jpeg") and max(width, height) > 65500:
                raise ConversionError(
                    f"un JPEG ne peut pas dépasser 65 500 pixels de côté (vous demandez "
                    f"{max(width, height)}). Réduisez l'agrandissement.")
            if width < img.width:
                img.draft("RGB", (width, height))   # jpeg: decode au 1/2, 1/4, 1/8. Enorme gain
            img = ImageOps.exif_transpose(img)
        except ConversionError:
            raise                        # notre propre message, ne pas le maquiller en "illisible"
        except Exception as e:
            raise ConversionError(f"image illisible: {e}")
        if progress:
            progress(0.3)
        source = img
        img = _en_rvb(img).resize((width, height), Image.LANCZOS, reducing_gap=2.0)
        if progress:
            progress(0.75)
        dst = _sauver(img, dst, source)
        if progress:
            progress(1.0)
        return dst

    if shutil.which("ffmpeg") is None:   # les images passent par Pillow, la video a besoin de ffmpeg
        raise ConversionError("ffmpeg introuvable dans le PATH")
    dst_ext = dst.suffix.lower().lstrip(".")
    # x264 exige des cotes pairs (-2); un gif n'a pas cette contrainte, -1 lui garde sa taille exacte
    auto = "-1" if dst_ext == "gif" else "-2"
    if auto == "-2":   # yuv420p impose les deux cotes pairs, pas seulement celui calcule
        width = width if width is None else max(2, width - width % 2)
        height = height if height is None else max(2, height - height % 2)
    if keep_ratio:
        # une seule dimension (ou largeur+hauteur -> la largeur gagne): l'autre est calculee
        vf = f"scale={width}:{auto}" if width is not None else f"scale={auto}:{height}"
    else:
        vf = f"scale={width}:{height}"

    cmd = _FFMPEG + ["-i", str(src), "-vf", vf]
    if media_kind(src) == "video" and dst_ext in FORMATS["video"]:
        cmd += _strip_vf(_video_args(dst_ext, "normal"))
    cmd.append(str(dst))

    return _ffmpeg(cmd, dst, probe(src).get("duration") if progress else None, progress)


def _en_rvb(img):
    """Ramene une image dans un mode ou l'on peut filtrer et reechantillonner proprement.
    Une PNG en palette ('P') resiste a UnsharpMask et se redimensionne en escalier."""
    if img.mode == "P":
        return img.convert("RGBA" if img.info.get("transparency") is not None else "RGB")
    if img.mode in ("RGB", "RGBA", "L"):
        return img
    return img.convert("RGB")


def _sauver(img, dst, source=None) -> pathlib.Path:
    """Ecrit une image PIL en conservant le profil ICC et l'EXIF de la source : sans le profil,
    une photo prise en Display P3 (iPhone) est relue comme du sRGB et les couleurs se decalent."""
    dst = pathlib.Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    info = (source.info if source is not None else img.info) or {}

    garde = {}
    # profil valide seulement si on n'a pas change d'espace de couleur (CMJN -> RVB l'invalide)
    if info.get("icc_profile") and (source is None or source.mode == img.mode or source.mode == "P"):
        garde["icc_profile"] = info["icc_profile"]
    if info.get("exif"):
        garde["exif"] = info["exif"]   # exif_transpose a deja retire l'orientation

    if dst.suffix.lower() in (".jpg", ".jpeg", ".bmp"):
        if img.mode != "RGB":
            fond = Image.new("RGB", img.size, (255, 255, 255))
            fond.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = fond
    if dst.suffix.lower() in (".jpg", ".jpeg", ".webp"):
        # sans ca un webp redimensionne repartait a la valeur par defaut de Pillow (80) et
        # perdait de la qualite alors qu'on n'avait rien demande de tel
        garde["quality"] = 95
    try:
        img.save(dst, **garde)
    except (OSError, ValueError) as e:
        if not garde.keys() & {"icc_profile", "exif"}:
            raise                        # rien a retirer: l'echec vient d'ailleurs (taille, disque)
        garde.pop("icc_profile", None)   # profil casse: mieux vaut l'image sans que pas d'image
        garde.pop("exif", None)
        img.save(dst, **garde)
    return dst


def affiner(img, scale=2, force=60):
    """L'effet « ameliorer la nettete » sur une image PIL. Un seul endroit le definit.

    Attention: appeler affiner() ne suffit pas a rendre un apercu honnete. Le rayon de
    l'UnsharpMask est en pixels: l'appliquer a une vignette de 440 px puis la comparer au
    fichier produit en pleine resolution donnait deux resultats sans rapport (ecart RMS 5,6,
    soit la taille de l'effet lui-meme). L'apercu doit donc lui donner un MORCEAU de l'image
    a sa resolution de sortie, pas l'image entiere reduite: cf. _calculer_nettete()."""
    img = _en_rvb(img)
    if scale != 1:
        img = img.resize((max(1, img.width * scale), max(1, img.height * scale)), Image.LANCZOS)
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=force, threshold=3))


def enhance(src, dst, scale=2, force=60, progress=None) -> pathlib.Path:
    # ponytail: "améliorer la netteté" = agrandissement lanczos (Pillow) + unsharp mask, PAS un upscale IA
    src = pathlib.Path(src)
    if media_kind(src) != "image":
        # ponytail: on refuse au lieu de traiter frame par frame. Avant, un GIF anime passait
        # par Pillow et ressortait avec UNE seule image sur 20, sans rien dire a personne.
        if anime(src):
            raise ConversionError("La netteté ne marche pas sur une image animée : "
                                  "elle ne garderait que la première image.")
        raise ConversionError("enhance: source doit être une image (pas vidéo/audio)")
    _stop()

    if progress:
        progress(0.15)
    try:
        img = Image.open(src)
        img.load()
        img = ImageOps.exif_transpose(img)
    except Exception as e:
        raise ConversionError(f"image illisible: {e}")

    source = img
    img = affiner(img, scale, force)
    if progress:
        progress(0.9)

    dst = _sauver(img, dst, source)
    if progress:
        progress(1.0)
    return dst


def thumbnail(src, dst_png, max_px=480) -> pathlib.Path:
    src = pathlib.Path(src)
    kind = media_kind(src)
    if kind == "audio":
        raise ConversionError("pas d'aperçu pour un fichier audio")
    if shutil.which("ffmpeg") is None:
        raise ConversionError("ffmpeg introuvable dans le PATH")

    dst_png = pathlib.Path(dst_png)
    dst_png.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale='min(iw,{max_px})':-2"  # jamais d'agrandissement
    cmd = _FFMPEG + ["-i", str(src)]
    if kind == "video":                  # video ou gif anime: une seule frame
        cmd += ["-update", "1", "-frames:v", "1"]
    cmd += ["-vf", vf, str(dst_png)]
    return _ffmpeg(cmd, dst_png)


_VERROU = threading.Lock()
_SESSION = None

MODELE = "u2net"       # 176 Mo, le modele fin. Sans l'import de rembg il se charge en ~1 s,
                       # et il est prechauffe des l'ouverture de la fenetre: le poids ne se voit plus
_URL = f"https://github.com/danielgatis/rembg/releases/download/v0.0.0/{MODELE}.onnx"
_MOYENNE, _ECART = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)   # normalisation attendue par u2net
_COTE = 320            # le reseau travaille en 320x320, quelle que soit la taille de la photo

_FONDS = {"blanc": (255, 255, 255), "creme": (0xFA, 0xF3, 0xE8)}


TAILLE_MODELE = 176          # Mo, verifie sur l'URL. C'est ce chiffre que l'interface annonce:
_TAILLE_MINI = 100 << 20     # elle affichait « 4,7 Mo », reste du petit modele u2netp rejete.


def _fichier_modele() -> pathlib.Path:
    """Ou est le .onnx ? On reprend les emplacements de rembg, qui l'a peut-etre deja telecharge."""
    maison = pathlib.Path(os.environ.get("U2NET_HOME") or pathlib.Path.home() / ".u2net")
    for chemin in (maison / f"{MODELE}.onnx",
                   pathlib.Path.home() / ".rembg" / "models" / MODELE / f"{MODELE}.onnx"):
        if _entier(chemin):
            return chemin
    return maison / f"{MODELE}.onnx"


def _entier(chemin) -> bool:
    """Un .onnx tronque par une coupure reseau fait planter onnxruntime avec un message
    incomprehensible, et rien ne le retelechargeait jamais. On refuse tout fichier trop petit."""
    try:
        return chemin.stat().st_size >= _TAILLE_MINI
    except OSError:
        return False


def modele_pret() -> bool:
    """Le modèle est-il déjà sur le disque, et entier ? Sinon le premier appel le télécharge."""
    return _entier(_fichier_modele())


def _telecharger_modele(dst, progress=None) -> pathlib.Path:
    """Télécharge les 176 Mo du modèle. urlretrieve() ne vérifiait ni la taille reçue ni le
    délai: une coupure laissait un fichier tronqué, que tmp.replace() rendait définitif."""
    import urllib.request
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part")
    try:
        with urllib.request.urlopen(_URL, timeout=60) as r:
            attendu = int(r.headers.get("Content-Length") or 0)
            recu = 0
            with open(tmp, "wb") as f:
                while bloc := r.read(1 << 20):
                    _stop()
                    f.write(bloc)
                    recu += len(bloc)
                    if progress and attendu:
                        progress(recu / attendu)
        if attendu and recu != attendu:
            raise ConversionError(f"téléchargement interrompu ({recu // 2**20} Mo reçus sur "
                                  f"{attendu // 2**20}). Vérifiez la connexion et réessayez.")
        if recu < _TAILLE_MINI:
            raise ConversionError("le fichier téléchargé est trop petit pour être le modèle")
        tmp.replace(dst)
    except Exception:
        tmp.unlink(missing_ok=True)    # jamais de .onnx tronque rendu definitif
        raise
    return dst


def precharger_modele(progress=None):
    """Charge le modèle. On parle directement à onnxruntime : passer par rembg importait
    1208 modules (pymatting → numba → scipy) dont aucun ne sert ici, soit ~13 s au démarrage."""
    global _SESSION
    with _VERROU:                # aller-retour sur le panneau: sinon deux chargements en parallele
        if _SESSION is None:
            import onnxruntime
            fichier = _fichier_modele()
            if not _entier(fichier):
                _telecharger_modele(fichier, progress)
            options = onnxruntime.SessionOptions()
            options.log_severity_level = 3
            _SESSION = onnxruntime.InferenceSession(str(fichier), options,
                                                    providers=["CPUExecutionProvider"])
    return _SESSION


def modele_charge() -> bool:
    return _SESSION is not None


def _masque_sujet(img):
    """Silhouette du sujet, en niveaux de gris a la taille de l'image (meme calcul que rembg)."""
    import numpy as np
    session = precharger_modele()
    petite = np.array(img.convert("RGB").resize((_COTE, _COTE), Image.LANCZOS), dtype=np.float32)
    petite /= max(petite.max(), 1e-6)
    for c in range(3):
        petite[:, :, c] = (petite[:, :, c] - _MOYENNE[c]) / _ECART[c]
    entree = np.expand_dims(petite.transpose(2, 0, 1), 0)

    sortie = session.run(None, {session.get_inputs()[0].name: entree})[0][:, 0, :, :]
    mini, maxi = sortie.min(), sortie.max()
    sortie = (sortie - mini) / max(maxi - mini, 1e-6)
    gris = Image.fromarray((np.squeeze(sortie) * 255).astype("uint8"), mode="L")
    return gris.resize(img.size, Image.LANCZOS)


def remove_background(src, dst_png, fond="transparent", adoucissement=0, progress=None) -> pathlib.Path:
    src = pathlib.Path(src)
    if media_kind(src) != "image":
        if anime(src):
            raise ConversionError("Le détourage ne marche pas sur une image animée : "
                                  "il ne garderait que la première image.")
        raise ConversionError("Le détourage ne marche que sur une image.")
    dst_png = pathlib.Path(dst_png)
    if dst_png.suffix.lower() != ".png":
        raise ConversionError("le détourage doit être enregistré en .png (seul format qui garde la transparence)")
    if fond not in ("transparent", *_FONDS):
        raise ConversionError(f"fond inconnu: {fond}")
    _stop()

    if progress:
        progress(0.05)
    try:
        precharger_modele()          # deja fait au demarrage de l'appli, sauf si on va trop vite
        if progress:
            progress(0.35)
        _stop()
        img = ImageOps.exif_transpose(Image.open(src)).convert("RGBA")
        result = Image.composite(img, Image.new("RGBA", img.size, 0), _masque_sujet(img))
    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError(f"détourage impossible: {e}")
    if progress:
        progress(0.85)

    if adoucissement:
        r, g, b, a = result.split()
        a = a.filter(ImageFilter.GaussianBlur(adoucissement / 5))
        result = Image.merge("RGBA", (r, g, b, a))

    if fond in _FONDS:
        fondimg = Image.new("RGB", result.size, _FONDS[fond])
        fondimg.paste(result, mask=result.split()[3])
        result = fondimg

    dst_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        result.save(dst_png)
    except Exception as e:
        raise ConversionError(f"écriture impossible: {e}")
    if progress:
        progress(1.0)
    return dst_png
