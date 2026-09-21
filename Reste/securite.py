"""Analyse de sécurité d'un dossier : Windows Defender, puis quelques signaux locaux.

Couche 1 - Windows Defender (MpCmdRun.exe), sans remédiation : il signale, il ne touche à rien.
Couche 2 - signaux locaux, des HEURISTIQUES : extension exécutable, double extension
trompeuse, caractère RLO, programme Windows déguisé, macros Office, zip qui contient des
programmes. Ils ne remplacent pas un antivirus : ils disent « regardez ce fichier ».

Ce module ne supprime, ne déplace et ne met en quarantaine RIEN.
"""
import dataclasses
import os
import pathlib
import re
import stat
import subprocess
import threading
import time
import zipfile

import convert

MENACE, SUSPECT, A_VERIFIER = "Menace (Defender)", "Suspect", "À vérifier"
ORDRE = {MENACE: 0, SUSPECT: 1, A_VERIFIER: 2}

DELAI_DEFENDER = 1800          # s. Un disque entier peut prendre longtemps ; au-delà on abandonne
_LECTURE = 4096                # on ne lit que le début des fichiers

EXT_EXEC = {".exe", ".scr", ".com", ".pif", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe",
            ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".msi", ".msp", ".dll", ".cpl",
            ".jar", ".reg", ".scf", ".inf", ".gadget", ".application", ".appref-ms"}
# extensions sous lesquelles un programme Windows (en-tête MZ/PE) est NORMAL : sinon chaque
# module Python (.pyd) ou pilote d'un dossier de dev passerait pour « Suspect »
EXT_PE = EXT_EXEC | {".sys", ".ocx", ".drv", ".efi", ".mui", ".pyd", ".node", ".ax",
                     ".winmd", ".tlb", ".mun", ".acm", ".tsp", ".cpl", ".bin", ".ime", ".rll"}
# ce qu'un fichier piégé prétend être : « facture.pdf.exe »
EXT_LEURRES = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "rtf", "txt",
               "csv", "jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "mp3", "wav", "mp4",
               "mov", "avi", "mkv", "zip", "rar", "7z", "html", "htm"}
EXT_MACROS = {".docm", ".dotm", ".xlsm", ".xltm", ".xlam", ".pptm", ".potm", ".ppsm", ".sldm"}
EXT_OOXML = {".docx", ".dotx", ".xlsx", ".xltx", ".pptx", ".potx", ".ppsx"}
RLO = "\u202e"
MAX_NOMS_ZIP = 10_000


@dataclasses.dataclass
class Signal:
    gravite: str
    chemin: pathlib.Path
    raison: str


@dataclasses.dataclass
class Resultat:
    dossier: pathlib.Path
    signaux: list
    nb_fichiers: int
    duree: float
    defender: str              # "rien", "menaces" ou "indisponible"
    defender_detail: str = ""

    def resume(self) -> str:
        return (f"{self.nb_fichiers} fichier{'s' if self.nb_fichiers > 1 else ''} analysé"
                f"{'s' if self.nb_fichiers > 1 else ''} · {_duree(self.duree)} · "
                f"{sum(1 for s in self.signaux if s.gravite != MENACE)} signal(aux) local(aux)")


def _duree(s):
    if s < 10:
        return f"{s:.1f} s".replace(".", ",")
    if s < 60:
        return f"{int(s)} s"
    return f"{int(s // 60)} min {int(s % 60):02d} s"


# ------------------------------------------------------------------ couche 2 : signaux locaux
def _extension(nom):
    return os.path.splitext(nom)[1].lower()


def signaux_nom(nom: str) -> list:
    """Signaux qui ne demandent que le NOM du fichier : [(gravité, raison)]."""
    res = []
    if RLO in nom:
        res.append((SUSPECT, "caractère d'inversion d'écriture (RLO) dans le nom : "
                             "l'extension affichée n'est pas la vraie"))
    ext = _extension(nom.replace(RLO, ""))
    morceaux = nom.replace(RLO, "").split(".")
    if ext in EXT_EXEC and len(morceaux) >= 3 and morceaux[-2].strip().lower() in EXT_LEURRES:
        res.append((SUSPECT, f"double extension : se fait passer pour un .{morceaux[-2].strip().lower()} "
                             f"mais c'est un {ext}"))
    elif ext in EXT_EXEC:
        res.append((A_VERIFIER, f"programme ou script ({ext})"))
    if ext in EXT_MACROS:
        res.append((A_VERIFIER, f"document Office avec macros ({ext})"))
    return res


def _est_pe(debut: bytes) -> bool:
    """En-tête MZ ET signature PE à l'offset annoncé : un simple texte commençant par « MZ »
    ne suffit pas à faire un programme."""
    if len(debut) < 0x40 or debut[:2] != b"MZ":
        return False
    decalage = int.from_bytes(debut[0x3C:0x40], "little")
    return 0x40 <= decalage <= len(debut) - 4 and debut[decalage:decalage + 4] == b"PE\0\0"


def signaux_contenu(chemin: pathlib.Path, debut: bytes) -> list:
    """Signaux qui regardent le contenu (début du fichier, ou répertoire d'un zip)."""
    res = []
    ext = _extension(chemin.name.replace(RLO, ""))
    if ext not in EXT_PE and _est_pe(debut):
        sous = f"l'extension {ext}" if ext else "un nom sans extension"
        res.append((SUSPECT, f"programme Windows déguisé : contenu exécutable sous {sous}"))
    if debut[:4] == b"PK\x03\x04" and (ext in EXT_OOXML or ext == ".zip"):
        try:
            with zipfile.ZipFile(chemin) as z:
                noms = [i.filename for i in z.infolist()[:MAX_NOMS_ZIP]]
        except Exception:
            noms = []                          # zip illisible : pas notre affaire ici
        if ext in EXT_OOXML and any(n.lower().endswith("vbaproject.bin") for n in noms):
            res.append((A_VERIFIER, f"document Office qui contient des macros (vbaProject.bin) "
                                    f"sous une extension {ext} censée n'en avoir aucune"))
        if ext == ".zip":
            dedans = [n for n in noms if _extension(n.rstrip("/")) in EXT_EXEC]
            if dedans:
                exemple = pathlib.PurePosixPath(dedans[0].replace("\\", "/")).name
                res.append((A_VERIFIER, f"archive qui contient {len(dedans)} programme(s) ou "
                                        f"script(s), dont « {exemple} »"))
    return res


def _lien(chemin) -> bool:
    """Lien symbolique ou jonction : on ne les suit pas (os.walk suit les jonctions en 3.11)."""
    try:
        st = os.lstat(chemin)
    except OSError:
        return True
    tag = getattr(st, "st_reparse_tag", 0)
    return stat.S_ISLNK(st.st_mode) or tag in (getattr(stat, "IO_REPARSE_TAG_SYMLINK", -1),
                                               getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -2))


def fichiers(dossier):
    """Tous les fichiers du dossier, sans suivre liens ni jonctions, illisibles ignorés."""
    for racine, dossiers, noms in os.walk(dossier, followlinks=False, onerror=lambda _e: None):
        dossiers[:] = [d for d in dossiers if not _lien(os.path.join(racine, d))]
        for n in noms:
            p = os.path.join(racine, n)
            if not _lien(p):
                yield pathlib.Path(p)


def analyse_locale(dossier, progress=None) -> tuple:
    """(signaux, nombre de fichiers examinés)."""
    liste = []
    for p in fichiers(dossier):
        liste.append(p)
        if len(liste) % 500 == 0 and convert.annulation_demandee():
            raise convert.Annule("annulé")
    signaux = []
    for n, p in enumerate(liste):
        if n % 200 == 0:
            if convert.annulation_demandee():
                raise convert.Annule("annulé")
            if progress:
                progress(n / max(1, len(liste)))
        trouves = signaux_nom(p.name)
        try:
            with open(p, "rb") as f:
                debut = f.read(_LECTURE)
        except OSError:
            debut = b""                        # verrouillé, droits : on garde les signaux du nom
        try:
            trouves += signaux_contenu(p, debut)
        except Exception:
            pass                               # un fichier bizarre ne doit pas arrêter l'analyse
        for gravite, raison in trouves:
            signaux.append(Signal(gravite, p, raison))
    if progress:
        progress(1.0)
    return signaux, len(liste)


# ------------------------------------------------------------------ couche 1 : Windows Defender
def _version(nom):
    return tuple(int(x) for x in re.findall(r"\d+", nom))


def trouver_mpcmdrun(plateforme=None, program_files=None):
    """MpCmdRun.exe le plus récent de Platform\\<version>\\ (mis à jour par Windows), sinon
    celui de Program Files. None si aucun."""
    plateforme = pathlib.Path(plateforme or os.path.join(
        os.environ.get("ProgramData", r"C:\ProgramData"), "Microsoft", "Windows Defender", "Platform"))
    try:
        versions = sorted((d for d in plateforme.iterdir() if d.is_dir()),
                          key=lambda d: _version(d.name), reverse=True)
    except OSError:
        versions = []
    for d in versions:
        if (d / "MpCmdRun.exe").is_file():
            return d / "MpCmdRun.exe"
    secours = pathlib.Path(program_files or os.environ.get("ProgramFiles", r"C:\Program Files")) \
        / "Windows Defender" / "MpCmdRun.exe"
    return secours if secours.is_file() else None


_RE_MENACE = re.compile(r"^\s*Threat\s*:\s*(.+?)\s*$", re.I)
_RE_RESSOURCE = re.compile(r"^\s*(?:file|containerfile|archive)\s*:\s*(.+?)\s*$", re.I)


def lire_sortie_defender(texte: str) -> list:
    """[(nom de la menace, chemin ou None)] depuis la sortie de MpCmdRun -Scan. Une menace
    sans ligne « file : » (rare) est gardée avec un chemin None."""
    res, courante, vue = [], None, False
    for ligne in texte.splitlines():
        m = _RE_MENACE.match(ligne)
        if m:
            if courante and not vue:
                res.append((courante, None))
            courante, vue = m.group(1), False
            continue
        m = _RE_RESSOURCE.match(ligne)
        if m and courante:
            res.append((courante, m.group(1)))
            vue = True
    if courante and not vue:
        res.append((courante, None))
    return res


def _decoder(brut: bytes) -> str:
    for enc in ("utf-8", "oem", "mbcs", "latin-1"):
        try:
            return brut.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return brut.decode("latin-1", errors="replace")


def analyse_defender(dossier, exe="auto", delai=None):
    """(état, [(menace, chemin)], détail). état : "rien", "menaces" ou "indisponible".

    Lancé comme ffmpeg dans convert._ffmpeg : sortie lue par un thread, délai tenu par un
    Timer, processus inscrit dans convert._PROCS pour que « Annuler » le tue, et aucune
    fenêtre console (CREATE_NO_WINDOW) dans le .exe fenêtré."""
    if exe == "auto":
        exe = trouver_mpcmdrun()
    if not exe or not pathlib.Path(exe).is_file():
        return "indisponible", [], "MpCmdRun.exe introuvable"
    convert._stop()
    cmd = [str(exe), "-Scan", "-ScanType", "3", "-File", str(dossier), "-DisableRemediation"]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        return "indisponible", [], f"lancement impossible : {e}"
    convert._PROCS.add(proc)
    sortie, expire = [], []
    lecteur = threading.Thread(target=lambda: sortie.append(proc.stdout.read()), daemon=True)
    lecteur.start()
    alarme = threading.Timer(delai or DELAI_DEFENDER, lambda: (expire.append(1), proc.kill()))
    alarme.start()
    try:
        code = proc.wait()
    finally:
        alarme.cancel()
        convert._PROCS.discard(proc)
        lecteur.join(timeout=5)
        proc.stdout.close()
    if convert.annulation_demandee():
        raise convert.Annule("annulé")
    texte = _decoder(sortie[0] if sortie else b"")
    if expire:
        return "indisponible", [], f"Defender n'a pas fini en {delai or DELAI_DEFENDER} s"
    if code == 0:
        return "rien", [], ""
    if code == 2:
        menaces = lire_sortie_defender(texte)
        return ("menaces", menaces, "") if menaces else \
            ("menaces", [("menace non détaillée", None)], texte.strip()[-300:])
    derniere = [l for l in texte.strip().splitlines() if l.strip()][-1:] or [""]
    return "indisponible", [], f"code {code & 0xFFFFFFFF:#x} {derniere[0]}".strip()


# ------------------------------------------------------------------ les deux ensemble
def trier(signaux) -> list:
    """Un fichier = une ligne, avec sa gravité la plus haute et toutes ses raisons ;
    triées Menace > Suspect > À vérifier, puis par nom."""
    groupes = {}
    for s in signaux:
        groupes.setdefault(os.path.normcase(str(s.chemin)), []).append(s)
    lignes = []
    for liste in groupes.values():
        liste.sort(key=lambda s: ORDRE[s.gravite])
        raisons = " ; ".join(dict.fromkeys(s.raison for s in liste))
        lignes.append(Signal(liste[0].gravite, liste[0].chemin, raisons))
    return sorted(lignes, key=lambda s: (ORDRE[s.gravite], s.chemin.name.lower()))


def analyser(dossier, progress=None, exe="auto", delai=None) -> Resultat:
    """Analyse complète. Locale d'abord (rapide, elle compte les fichiers), puis Defender."""
    dossier = pathlib.Path(dossier)
    if not dossier.is_dir():
        raise convert.ConversionError(f"« {dossier} » n'est pas un dossier.")
    t = time.time()
    signaux, n = analyse_locale(dossier, (lambda f: progress(0.3 * f)) if progress else None)
    if progress:
        progress(0.35)
    etat, menaces, detail = analyse_defender(dossier, exe=exe, delai=delai)
    for nom, chemin in menaces:
        # « C:\x\a.zip->b.exe » : le fichier à montrer dans l'explorateur est le zip
        conteneur, _, dedans = (chemin or "").partition("->")
        signaux.append(Signal(MENACE, pathlib.Path(conteneur) if conteneur else dossier,
                              f"Windows Defender : {nom}" + (f" (dans « {dedans} »)" if dedans else "")))
    if progress:
        progress(1.0)
    return Resultat(dossier, trier(signaux), n, time.time() - t, etat, detail)


def message_defender(r: Resultat) -> str:
    if r.defender == "rien":
        return "Defender : aucune menace trouvée"
    if r.defender == "menaces":
        n = sum(1 for s in r.signaux if s.gravite == MENACE)
        return f"Defender : {n} menace{'s' if n > 1 else ''} trouvée{'s' if n > 1 else ''}"
    return "Defender indisponible, seule l'analyse locale a été faite"
