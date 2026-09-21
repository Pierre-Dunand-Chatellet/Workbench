"""Tests manuels (assert) pour convert.py. Lancer: python test_convert.py"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

import convert as _c
from convert import (FORMATS, ConversionError, affiner, convert, enhance, media_kind,
                      modele_pret, probe, remove_background, resize, targets_for, thumbnail)


def test_media_kind():
    assert media_kind("a.PNG") == "image"
    assert media_kind("a.mp4") == "video"
    assert media_kind(pathlib.Path("a.mp3")) == "audio"
    assert media_kind("a.txt") is None
    assert media_kind("a") is None


def test_targets_for():
    assert set(targets_for("x.png")) == set(FORMATS["image"])
    assert set(targets_for("x.mp4")) == set(FORMATS["video"]) | set(FORMATS["audio"])
    assert set(targets_for("x.mp3")) == set(FORMATS["audio"])
    assert targets_for("x.txt") == []


def _gen(cmd):
    subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)


def test_real_conversions(tmpdir):
    tmp = pathlib.Path(tmpdir)
    video_src = tmp / "src.mp4"
    audio_src = tmp / "src.mp3"

    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
          "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
          "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
          "-shortest", str(video_src)])
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "sine=frequency=440:duration=1", str(audio_src)])

    def check(src, dst):
        out = convert(src, dst)
        assert out == dst
        assert dst.exists() and dst.stat().st_size > 0

    check(video_src, tmp / "out.mp4")
    check(video_src, tmp / "out.mp3")
    check(audio_src, tmp / "out.wav")

    # image extraite d'une frame vidéo (hors contrat convert, juste pour fabriquer le test)
    frame_png = tmp / "frame.png"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_src),
          "-frames:v", "1", str(frame_png)])
    check(frame_png, tmp / "out.webp")

    # cible invalide -> ConversionError
    try:
        convert(video_src, tmp / "out.png")
        assert False, "devrait lever ConversionError"
    except ConversionError:
        pass



def test_probe_resize_enhance_thumbnail(tmpdir):
    tmp = pathlib.Path(tmpdir)
    video_src = tmp / "src2.mp4"
    audio_src = tmp / "src2.mp3"
    image_src = tmp / "src2.png"

    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
          "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
          "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
          "-shortest", str(video_src)])
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "sine=frequency=440:duration=1", str(audio_src)])
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "color=c=blue:size=100x80:d=1", "-frames:v", "1", str(image_src)])

    # probe: vidéo -> dimensions + durée, audio -> pas de dimensions mais une durée
    info_v = probe(video_src)
    assert info_v["width"] == 320 and info_v["height"] == 240, info_v
    assert info_v["duration"] is not None and info_v["duration"] > 0

    info_a = probe(audio_src)
    assert info_a["width"] is None and info_a["height"] is None, info_a
    assert info_a["duration"] is not None and info_a["duration"] > 0

    # resize image: largeur imposée
    out_img = resize(image_src, tmp / "resized.png", width=40)
    assert probe(out_img)["width"] == 40

    # resize image AGRANDISSEMENT: 100x80 -> 400 % , dimensions exactes (pas d'arrondi au pair)
    out_up = resize(image_src, tmp / "up.png", width=400)
    i_up = probe(out_up)
    assert (i_up["width"], i_up["height"]) == (400, 320), i_up

    # largeur impaire: Pillow doit la rendre telle quelle (ffmpeg -2 l'aurait arrondie)
    out_odd = resize(image_src, tmp / "odd.png", width=333)
    assert probe(out_odd)["width"] == 333

    # sans garder les proportions: les deux dimensions imposées, agrandies
    out_libre = resize(image_src, tmp / "libre.png", width=500, height=120, keep_ratio=False)
    i_libre = probe(out_libre)
    assert (i_libre["width"], i_libre["height"]) == (500, 120), i_libre

    # resize vidéo: hauteur -2 doit rester paire
    out_vid = resize(video_src, tmp / "resized.mp4", width=101)
    h = probe(out_vid)["height"]
    assert h is not None and h % 2 == 0, h

    # enhance image: dimensions doublées (100x80 -> 200x160)
    out_enh = enhance(image_src, tmp / "enhanced.png", scale=2)
    info_e = probe(out_enh)
    assert info_e["width"] == 200 and info_e["height"] == 160, info_e

    # enhance sur audio -> ConversionError
    try:
        enhance(audio_src, tmp / "bad.png")
        assert False, "devrait lever ConversionError"
    except ConversionError:
        pass

    # thumbnail vidéo: PNG existant, largeur <= max_px
    thumb = thumbnail(video_src, tmp / "thumb.png", max_px=100)
    assert thumb.exists() and thumb.stat().st_size > 0
    assert probe(thumb)["width"] <= 100

    # thumbnail sur audio -> ConversionError
    try:
        thumbnail(audio_src, tmp / "thumb2.png")
        assert False, "devrait lever ConversionError"
    except ConversionError:
        pass


def test_convert_progress(tmpdir):
    tmp = pathlib.Path(tmpdir)
    video_src = tmp / "src3.mp4"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
          "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
          "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
          "-shortest", str(video_src)])

    fractions = []
    out = convert(video_src, tmp / "out3.mp4", progress=fractions.append)
    assert out.exists() and out.stat().st_size > 0
    assert fractions, "aucune fraction reçue"
    assert all(0.0 <= f <= 1.0 for f in fractions), fractions
    assert all(b >= a for a, b in zip(fractions, fractions[1:])), fractions
    assert fractions[-1] == 1.0, fractions


def test_enhance_scale1_et_pillow(tmpdir):
    tmp = pathlib.Path(tmpdir)
    image_src = tmp / "src4.png"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "color=c=red:size=100x80:d=1", "-frames:v", "1", str(image_src)])

    out_same = enhance(image_src, tmp / "same.png", scale=1)
    info = probe(out_same)
    assert info["width"] == 100 and info["height"] == 80, info

    audio_src = tmp / "src4.mp3"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "sine=frequency=440:duration=1", str(audio_src)])
    try:
        enhance(audio_src, tmp / "bad2.png")
        assert False, "devrait lever ConversionError"
    except ConversionError:
        pass


def test_enhance_timing(tmpdir):
    import time
    tmp = pathlib.Path(tmpdir)
    image_src = tmp / "big.png"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "color=c=green:size=2400x1600:d=1", "-frames:v", "1", str(image_src)])
    t0 = time.perf_counter()
    out = enhance(image_src, tmp / "big_enhanced.png", scale=2)
    elapsed = time.perf_counter() - t0
    info = probe(out)
    assert info["width"] == 4800 and info["height"] == 3200, info
    print(f"enhance 2400x1600 -> 4800x3200: {elapsed:.2f}s")
    assert elapsed < 5, elapsed


def test_couleurs_preservees(tmpdir):
    """Le bug le plus vicieux: sans profil ICC recopie, une photo Display P3 relue en sRGB
    change de couleurs. Et une PNG en palette faisait planter la nettete."""
    from PIL import Image, ImageCms
    tmp = pathlib.Path(tmpdir)
    profil = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = tmp / "icc.jpg"
    Image.new("RGB", (200, 150), (200, 60, 40)).save(src, icc_profile=profil)

    for sortie in (resize(src, tmp / "r.jpg", width=400),
                   enhance(src, tmp / "e.jpg", scale=1)):
        im = Image.open(sortie)
        assert im.info.get("icc_profile"), f"profil ICC perdu: {sortie.name}"
        r, v, b = im.getpixel((5, 5))
        assert abs(r - 200) <= 3 and abs(v - 60) <= 3 and abs(b - 40) <= 3, (sortie.name, (r, v, b))

    # PNG en palette: doit passer, et garder sa couleur
    pal = tmp / "pal.png"
    Image.new("RGB", (200, 150), (30, 120, 200)).convert("P", palette=Image.ADAPTIVE).save(pal)
    for sortie in (resize(pal, tmp / "rp.png", width=400),
                   enhance(pal, tmp / "ep.png", scale=2)):
        assert Image.open(sortie).convert("RGB").getpixel((5, 5)) == (30, 120, 200), sortie.name


def test_affiner():
    """affiner() est deterministe et la force change vraiment quelque chose.
    (Que l'apercu de l'interface montre bien CE fichier-la se verifie dans test_workbench.py:
    c'est la taille des pixels donnes a affiner() qui compte, pas affiner() seule.)"""
    from PIL import Image
    src = Image.new("RGB", (60, 40), "blue")
    assert affiner(src, 1, 60).size == (60, 40)
    assert affiner(src, 4, 60).size == (240, 160)
    # deux appels identiques -> pixels identiques (sinon l'apercu mentirait sur le resultat)
    assert affiner(src, 2, 80).tobytes() == affiner(src, 2, 80).tobytes()
    # la force change vraiment quelque chose
    detail = Image.effect_noise((60, 40), 40).convert("RGB")
    assert affiner(detail, 1, 10).tobytes() != affiner(detail, 1, 150).tobytes()


def test_modele_pret():
    assert isinstance(modele_pret(), bool)


def test_remove_background_rejects(tmpdir):
    tmp = pathlib.Path(tmpdir)
    image_src = tmp / "src5.png"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "color=c=blue:size=50x50:d=1", "-frames:v", "1", str(image_src)])
    audio_src = tmp / "src5.mp3"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "sine=frequency=440:duration=1", str(audio_src)])

    # extension de sortie non-.png -> rejet rapide, pas de téléchargement du modèle
    try:
        remove_background(image_src, tmp / "out.jpg")
        assert False, "devrait lever ConversionError"
    except ConversionError:
        pass

    # source audio -> rejet rapide
    try:
        remove_background(audio_src, tmp / "out.png")
        assert False, "devrait lever ConversionError"
    except ConversionError:
        pass


def test_gif_video_downscale():
    # video -> gif doit passer par la branche video (fps/scale), pas la branche image
    cmd = _c._build_cmd("a.mp4", "b.gif", "video", "gif", "normal")
    assert "-vf" in cmd and "fps=12" in " ".join(cmd), cmd
    assert "-vf" not in _c._build_cmd("a.png", "b.gif", "image", "gif", "normal")


def _psnr(a, b):
    """dB entre deux fichiers image, via ffmpeg. Plus haut = plus fidele a la source."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b),
                          "-lavfi", "psnr", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=120).stderr
    return float(out.split("average:")[1].split()[0])


def test_webp_qualite_dans_le_bon_sens(tmpdir):
    """libwebp prend une echelle 0-100 ou GRAND = meilleur. On y envoyait _Q_IMG (2 = meilleur):
    « Haute » produisait le pire fichier, et les trois reglages tenaient dans 2-12 sur 100."""
    tmp = pathlib.Path(tmpdir)
    src = tmp / "src.png"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "testsrc2=size=1280x720:d=1", "-frames:v", "1", str(src)])
    mesures = {}
    for q in ("low", "normal", "high"):
        out = convert(src, tmp / f"q_{q}.webp", quality=q)
        mesures[q] = (out.stat().st_size, _psnr(src, out))
    assert mesures["high"][1] > mesures["normal"][1] > mesures["low"][1], mesures
    assert mesures["high"][0] > mesures["normal"][0] > mesures["low"][0], mesures
    # et « Haute » doit etre vraiment bon, pas juste le moins mauvais des trois
    assert mesures["high"][1] > 45, mesures

    # jpg et avif gardent leur sens a eux (petit = meilleur), verifions qu'on ne l'a pas casse
    for ext in ("jpg", "avif"):
        gros = convert(src, tmp / f"h.{ext}", quality="high").stat().st_size
        petit = convert(src, tmp / f"l.{ext}", quality="low").stat().st_size
        assert gros > petit, (ext, gros, petit)


def _frames(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, timeout=60).stdout.strip()
    return int(out.rstrip(","))


def test_gif_anime(tmpdir):
    """Un GIF anime etait classe « image »: 5 des 7 conversions proposees plantaient, le
    redimensionner n'en gardait qu'une frame sur 20, et gif -> mp4 n'existait pas."""
    tmp = pathlib.Path(tmpdir)
    gif, fixe = tmp / "anim.gif", tmp / "fixe.gif"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "testsrc=size=120x90:d=2:rate=10", str(gif)])
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "color=c=red:size=120x90:d=1", "-frames:v", "1", str(fixe)])

    assert _c.anime(gif) and not _c.anime(fixe)
    assert media_kind(gif) == "video", media_kind(gif)
    assert media_kind(fixe) == "image", media_kind(fixe)
    assert media_kind(tmp / "absent.gif") == "image"      # fichier inexistant: pas de plantage

    cibles = targets_for(gif)
    assert "mp4" in cibles and "png" in cibles, cibles
    assert not (set(cibles) & set(FORMATS["audio"])), cibles   # un gif n'a pas de son
    assert len(cibles) == len(set(cibles)), cibles            # pas de doublon "gif"

    # TOUTES les cibles proposees doivent marcher, pas 2 sur 7
    for ext in cibles:
        if ext == "gif":
            continue
        out = convert(gif, tmp / f"out.{ext}")
        assert out.exists() and out.stat().st_size > 0, ext
    assert _frames(tmp / "out.png") == 1                       # 1re frame seulement
    assert _frames(tmp / "out.mp4") > 10, _frames(tmp / "out.mp4")

    # redimensionner garde l'animation ET la taille exacte
    depart = _frames(gif)
    assert depart == 20, depart
    petit = resize(gif, tmp / "petit.gif", width=61)
    assert _frames(petit) == depart, (_frames(petit), depart)
    assert probe(petit)["width"] == 61, probe(petit)

    # la nettete refuse, au lieu de jeter 19 frames sur 20 en silence
    try:
        enhance(gif, tmp / "net.gif")
        assert False, "devrait lever ConversionError"
    except ConversionError as e:
        assert "animée" in str(e), e
    try:
        remove_background(gif, tmp / "fond.png")
        assert False, "devrait lever ConversionError"
    except ConversionError as e:
        assert "animée" in str(e), e

    # un gif FIXE reste une image: Pillow, taille exacte, aucun changement de comportement
    assert probe(resize(fixe, tmp / "f2.gif", width=33))["width"] == 33


def test_annulation(tmpdir):
    """Sans bouton Annuler, une conversion video partie pour 20 min ne se reglait qu'en tuant
    l'appli. annuler() doit couper ffmpeg et ne pas laisser de fichier a moitie ecrit."""
    import threading
    tmp = pathlib.Path(tmpdir)
    src = tmp / "long.mp4"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
          "-f", "lavfi", "-i", "testsrc2=size=1920x1080:d=25:rate=30",
          "-f", "lavfi", "-i", "sine=frequency=440:duration=25",
          "-c:v", "libx264", "-preset", "ultrafast", "-shortest", str(src)])
    dst = tmp / "sortie.webm"        # vp9 lent: on a le temps de cliquer
    _c.reprendre()
    threading.Timer(1.5, _c.annuler).start()
    t0 = time.perf_counter()
    try:
        convert(src, dst, quality="high", progress=lambda _f: None)
        assert False, "aurait du etre annule"
    except _c.Annule:
        pass
    finally:
        _c.reprendre()
    assert time.perf_counter() - t0 < 20, "l'annulation n'a pas coupe ffmpeg"
    assert not dst.exists(), "un fichier tronque a ete laisse derriere"
    # apres reprendre(), tout doit remarcher
    assert convert(src, tmp / "ok.mp3").exists()


def test_timeout_applique(tmpdir):
    """TIMEOUT etait mort des qu'on suivait la progression: on n'atteignait proc.wait(timeout=)
    qu'une fois ffmpeg termine."""
    tmp = pathlib.Path(tmpdir)
    src = tmp / "t.mp4"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "testsrc2=size=1920x1080:d=30:rate=30", "-c:v", "libx264", "-preset",
          "ultrafast", str(src)])
    vrai = _c.TIMEOUT
    _c.TIMEOUT = 2
    dst = tmp / "t.webm"
    try:
        t0 = time.perf_counter()
        try:
            convert(src, dst, quality="high", progress=lambda _f: None)
            assert False, "aurait du expirer"
        except ConversionError as e:
            assert "trop longue" in str(e), e
        assert time.perf_counter() - t0 < 15, "le delai n'a pas ete tenu"
        assert not dst.exists()
    finally:
        _c.TIMEOUT = vrai


def test_modele_tronque(tmpdir):
    """Un .onnx coupe par une panne reseau devenait definitif: modele_pret() disait oui et
    le detourage restait casse sans moyen de s'en sortir."""
    tmp = pathlib.Path(tmpdir)
    faux = tmp / "u2net.onnx"
    faux.write_bytes(b"x" * 1024)
    assert not _c._entier(faux)
    assert not _c._entier(tmp / "jamais_cree.onnx")
    vrai = _c._fichier_modele()
    if vrai.exists():
        assert _c._entier(vrai) and modele_pret()


def test_video_muette(tmpdir):
    """Une vidéo sans son se voyait quand même proposer mp3/wav/… et ffmpeg répondait
    « Output file does not contain any stream » en pleine interface."""
    tmp = pathlib.Path(tmpdir)
    muette, sonore = tmp / "muette.mp4", tmp / "sonore.mp4"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "testsrc=size=160x120:d=1:rate=10", str(muette)])
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
          "-f", "lavfi", "-i", "testsrc=size=160x120:d=1:rate=10",
          "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest", str(sonore)])

    assert not _c.a_du_son(muette) and _c.a_du_son(sonore)
    assert not (set(targets_for(muette)) & set(FORMATS["audio"])), targets_for(muette)
    assert set(FORMATS["audio"]) <= set(targets_for(sonore))
    assert _c.a_du_son(tmp / "absent.mp4")     # fichier illisible: on propose, on ne plante pas
    try:
        convert(muette, tmp / "x.mp3")
        assert False, "devrait lever ConversionError"
    except ConversionError as e:
        assert "piste son" in str(e), e
    assert convert(sonore, tmp / "ok.mp3").exists()


def test_conteneurs():
    """AAC dans un .avi n'est lu quasiment nulle part; un mp4 sans faststart doit etre
    telecharge en entier avant de demarrer."""
    avi = " ".join(_c._build_cmd("a.mp4", "b.avi", "video", "avi", "normal"))
    assert "libmp3lame" in avi and "aac" not in avi, avi
    assert "+faststart" in " ".join(_c._build_cmd("a.mp4", "b.mp4", "video", "mp4", "normal"))
    assert "+faststart" not in " ".join(_c._build_cmd("a.mp4", "b.mkv", "video", "mkv", "normal"))
    assert "-nostdin" in _c._build_cmd("a.mp4", "b.mp4", "video", "mp4", "normal")


def test_webp_redimensionne_garde_sa_qualite(tmpdir):
    """_sauver() ne fixait la qualite que pour le jpeg: un webp redimensionne repartait a la
    valeur par defaut de Pillow (80) et perdait de la qualite sans qu'on ait rien demande."""
    tmp = pathlib.Path(tmpdir)
    src = tmp / "s.webp"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "testsrc2=size=800x600:d=1", "-frames:v", "1", "-quality", "100", str(src)])
    out = resize(src, tmp / "r.webp", width=800)      # meme taille: seul le reencodage compte
    assert _psnr(src, out) > 40, _psnr(src, out)


if __name__ == "__main__":
    test_media_kind()
    test_targets_for()

    if shutil.which("ffmpeg") is None:
        print("ffmpeg introuvable dans le PATH, tests de conversion réelle sautés")
    else:
        with tempfile.TemporaryDirectory() as d:
            test_real_conversions(d)
        with tempfile.TemporaryDirectory() as d:
            test_probe_resize_enhance_thumbnail(d)
        with tempfile.TemporaryDirectory() as d:
            test_convert_progress(d)
        with tempfile.TemporaryDirectory() as d:
            test_enhance_scale1_et_pillow(d)
        with tempfile.TemporaryDirectory() as d:
            test_enhance_timing(d)
        with tempfile.TemporaryDirectory() as d:
            test_remove_background_rejects(d)
        with tempfile.TemporaryDirectory() as d:
            test_couleurs_preservees(d)
        with tempfile.TemporaryDirectory() as d:
            test_webp_qualite_dans_le_bon_sens(d)
        with tempfile.TemporaryDirectory() as d:
            test_gif_anime(d)
        with tempfile.TemporaryDirectory() as d:
            test_annulation(d)
        with tempfile.TemporaryDirectory() as d:
            test_timeout_applique(d)
        with tempfile.TemporaryDirectory() as d:
            test_webp_redimensionne_garde_sa_qualite(d)
        with tempfile.TemporaryDirectory() as d:
            test_modele_tronque(d)
        with tempfile.TemporaryDirectory() as d:
            test_video_muette(d)

    test_affiner()
    test_modele_pret()
    test_gif_video_downscale()
    test_conteneurs()
    print("OK")
