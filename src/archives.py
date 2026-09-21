"""Archives zip : lister, extraire, n'extraire que les médias, créer un zip. Bibliothèque standard seulement.

Un zip vient souvent d'ailleurs (mail, téléchargement) : on ne lui fait pas confiance.
  - chemin absolu, lecteur (« C: »), « .. », nom réservé Windows (CON, NUL...) : refusé ;
    et le chemin final, une fois résolu, doit rester dans le dossier de destination ;
  - plafond sur la taille décompressée totale et sur le nombre d'éléments (bombe zip) ;
  - membres chiffrés ignorés et signalés (on ne demande pas de mot de passe) ;
  - jamais d'écrasement : un fichier déjà présent reçoit un numéro, comme chemin_sortie().
"""
import dataclasses
import datetime
import os
import pathlib
import re
import zipfile
import zlib

import convert

MAX_TOTAL = 4 * 1024 ** 3          # 4 Go décompressés au plus, sur l'ensemble de l'extraction
MAX_ENTREES = 10_000               # éléments au plus dans l'archive
_BLOC = 1 << 20

EXT_MEDIAS = {e for exts in convert.FORMATS.values() for e in exts}

_RESERVES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}
_LECTEUR = re.compile(r"^[A-Za-z]:")


class ArchiveError(Exception):
    """Erreur sur une archive, avec un message en français lisible tel quel par l'utilisateur."""


@dataclasses.dataclass
class Rapport:
    """Ce qu'une extraction a fait. Rien n'est passé sous silence."""
    dest: pathlib.Path
    extraits: list = dataclasses.field(default_factory=list)     # chemins écrits
    chiffres: list = dataclasses.field(default_factory=list)     # noms ignorés (mot de passe)
    refuses: list = dataclasses.field(default_factory=list)      # (nom, raison) : chemins piégés
    endommages: list = dataclasses.field(default_factory=list)   # noms illisibles (CRC, zlib)

    def resume(self) -> str:
        n = len(self.extraits)
        morceaux = [f"{n} fichier{'s' if n > 1 else ''} extrait{'s' if n > 1 else ''}"]
        if self.chiffres:
            morceaux.append(f"{len(self.chiffres)} chiffré(s) ignoré(s)")
        if self.refuses:
            morceaux.append(f"{len(self.refuses)} chemin(s) dangereux refusé(s)")
        if self.endommages:
            morceaux.append(f"{len(self.endommages)} endommagé(s)")
        return " · ".join(morceaux)


def _ouvrir(chemin) -> zipfile.ZipFile:
    chemin = pathlib.Path(chemin)
    try:
        return zipfile.ZipFile(chemin)
    except FileNotFoundError:
        raise ArchiveError(f"« {chemin.name} » est introuvable.")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, ValueError, OSError) as e:
        raise ArchiveError(f"« {chemin.name} » n'est pas une archive zip valide "
                           f"(fichier corrompu ou incomplet) : {e}")


def chiffre(info) -> bool:
    return bool(info.flag_bits & 0x1)


def _date(info):
    try:
        return datetime.datetime(*info.date_time)
    except (ValueError, TypeError):
        return None


def raison_refus(nom: str):
    """Pourquoi ce nom de membre ne doit pas être écrit sur le disque (None s'il est sain).

    Ne suffit pas seul : extraire() vérifie EN PLUS que le chemin résolu reste dans la
    destination (Windows retire les points et espaces finaux, « a/.. . » peut remonter)."""
    brut = nom.replace("\\", "/")
    if not brut.strip("/"):
        return "nom vide"
    if brut.startswith("/"):
        return "chemin absolu"
    if _LECTEUR.match(brut):
        return "désigne un lecteur"
    if ":" in brut:
        return "caractère « : » interdit (flux caché NTFS)"
    for morceau in brut.rstrip("/").split("/"):
        if morceau in ("", "."):              # « a//b », « ./a » : inoffensifs
            continue
        if morceau.rstrip(". ") == "":        # « .. », et « ... » / « .. » que Windows ramène à « .. »
            return "remonte hors du dossier (« .. »)"
        if morceau.split(".")[0].strip().lower() in _RESERVES:
            return f"nom réservé par Windows (« {morceau} »)"
    return None


def _relatif(nom: str) -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(*[m for m in nom.replace("\\", "/").split("/") if m])


def _libre(p: pathlib.Path) -> pathlib.Path:
    """Même règle que chemin_sortie() de l'interface : « nom (1).ext », « nom (2).ext »..."""
    if not p.exists():
        return p
    i = 1
    while True:
        q = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not q.exists():
            return q
        i += 1


def _dans(cible: pathlib.Path, racine: pathlib.Path) -> bool:
    try:
        cible.resolve().relative_to(racine)
        return True
    except ValueError:
        return False


def lister(chemin) -> list:
    """Contenu de l'archive : une liste de dicts
    {nom, taille, taille_compressee, date, dossier, chiffre, refus}."""
    with _ouvrir(chemin) as z:
        infos = z.infolist()
        if len(infos) > MAX_ENTREES:
            raise ArchiveError(f"Cette archive contient {len(infos)} éléments : au-delà de "
                               f"{MAX_ENTREES}, on ne l'ouvre pas (possible bombe zip).")
        return [{"nom": i.filename, "taille": i.file_size, "taille_compressee": i.compress_size,
                 "date": _date(i), "dossier": i.is_dir(), "chiffre": chiffre(i),
                 "refus": raison_refus(i.filename)} for i in infos]


def est_media(nom: str) -> bool:
    return pathlib.PurePosixPath(nom.replace("\\", "/")).suffix.lower().lstrip(".") in EXT_MEDIAS


def extraire(chemin, dest, membres=None, progress=None) -> Rapport:
    """Extrait l'archive (ou seulement `membres`) dans `dest`. Un nom de dossier dans
    `membres` (« photos/ ») emporte tout ce qu'il contient. Annulable par convert.annuler()."""
    dest = pathlib.Path(dest)
    rapport = Rapport(dest=dest)
    with _ouvrir(chemin) as z:
        infos = z.infolist()
        if len(infos) > MAX_ENTREES:
            raise ArchiveError(f"Cette archive contient {len(infos)} éléments : au-delà de "
                               f"{MAX_ENTREES}, on ne l'extrait pas (possible bombe zip).")
        if membres is not None:
            voulus = set(membres)
            prefixes = tuple(m for m in voulus if m.endswith("/"))
            infos = [i for i in infos if i.filename in voulus
                     or (prefixes and i.filename.startswith(prefixes))]

        a_ecrire = []
        for i in infos:
            refus = raison_refus(i.filename)
            if refus:
                rapport.refuses.append((i.filename, refus))
            elif chiffre(i):
                rapport.chiffres.append(i.filename)
            else:
                a_ecrire.append(i)

        # la taille annoncée par l'en-tête peut mentir : on la vérifie ici, et on recompte
        # les octets réellement écrits pendant l'extraction
        annonce = sum(i.file_size for i in a_ecrire if not i.is_dir())
        if annonce > MAX_TOTAL:
            raise ArchiveError(f"Une fois décompressée, cette archive ferait {annonce / 1024**3:.1f} Go "
                               f"(plafond : {MAX_TOTAL / 1024**3:.0f} Go). Extraction refusée.")

        dest.mkdir(parents=True, exist_ok=True)
        racine = dest.resolve()
        ecrit = 0
        for i in a_ecrire:
            if convert.annulation_demandee():
                raise convert.Annule("annulé")
            rel = _relatif(i.filename)
            cible = racine.joinpath(*rel.parts)
            if not _dans(cible, racine):
                rapport.refuses.append((i.filename, "sort du dossier de destination"))
                continue
            if i.is_dir():
                cible.mkdir(parents=True, exist_ok=True)
                continue
            cible.parent.mkdir(parents=True, exist_ok=True)
            if not _dans(cible.parent, racine):     # un dossier-lien créé entre-temps
                rapport.refuses.append((i.filename, "sort du dossier de destination"))
                continue
            cible = _libre(cible)
            try:
                with z.open(i) as src, open(cible, "xb") as dst:   # x : jamais d'écrasement
                    while bloc := src.read(_BLOC):
                        ecrit += len(bloc)
                        if ecrit > MAX_TOTAL:
                            raise ArchiveError(
                                f"L'archive dépasse {MAX_TOTAL / 1024**3:.0f} Go une fois "
                                f"décompressée (taille annoncée mensongère). Extraction arrêtée.")
                        if convert.annulation_demandee():
                            raise convert.Annule("annulé")
                        dst.write(bloc)
                        if progress and annonce:
                            progress(min(1.0, ecrit / annonce))
            except (ArchiveError, convert.Annule):
                cible.unlink(missing_ok=True)        # pas de fichier à moitié écrit
                raise
            except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError):
                # CRC faux, flux deflate cassé, archive tronquée, méthode inconnue : ce membre
                # est perdu, les autres peuvent encore sortir
                cible.unlink(missing_ok=True)
                rapport.endommages.append(i.filename)
                continue
            except OSError as e:
                # disque plein, droits : ce n'est pas l'archive qui est en cause, on s'arrête
                cible.unlink(missing_ok=True)
                raise ArchiveError(f"Écriture impossible de « {rel.name} » : {e.strerror or e}")
            d = _date(i)
            if d:
                try:
                    t = d.timestamp()
                    os.utime(cible, (t, t))
                except (OSError, OverflowError, ValueError):
                    pass
            rapport.extraits.append(cible)
    if progress:
        progress(1.0)
    return rapport


def membres_medias(chemin) -> list:
    """Noms des membres dont l'extension est un format géré par convert.FORMATS."""
    return [e["nom"] for e in lister(chemin) if not e["dossier"] and est_media(e["nom"])]


def extraire_medias(chemin, dest, progress=None) -> Rapport:
    """Extrait seulement les images, vidéos et sons de l'archive."""
    return extraire(chemin, dest, membres=membres_medias(chemin), progress=progress)


def _nom_libre(nom: str, pris: set) -> str:
    """« photo.jpg » deja pris dans le zip -> « photo (1).jpg » (meme regle que _libre)."""
    p, i = pathlib.PurePosixPath(nom), 1
    while nom in pris:
        nom = f"{p.stem} ({i}){p.suffix}"
        i += 1
    pris.add(nom)
    return nom


def compresser(chemins, dest, progress=None) -> pathlib.Path:
    """Crée un zip avec ces fichiers et dossiers (un dossier garde son nom et son contenu,
    dossiers vides compris). Jamais d'écrasement : `dest` déjà pris devient « nom (1).zip ».
    Annulable par convert.annuler(), sans laisser de zip à moitié écrit. Rend le chemin créé."""
    dest = _libre(pathlib.Path(dest))
    entrees, pris = [], set()         # (chemin, nom dans le zip); dossier: nom finissant par /
    for c in map(pathlib.Path, chemins):
        haut = _nom_libre(c.name, pris)   # deux « photo.jpg » venus de dossiers differents
        if c.is_file():
            entrees.append((c, haut))
        elif c.is_dir():
            entrees.append((c, haut + "/"))
            for racine, dossiers, fichiers in os.walk(c):
                dossiers.sort()
                for n in dossiers + sorted(fichiers):
                    p = pathlib.Path(racine, n)
                    nom = "/".join((haut, *p.relative_to(c).parts))
                    entrees.append((p, nom + "/" if n in dossiers else nom))
    entrees = [(p, n) for p, n in entrees if p.resolve() != dest.resolve()]  # pas lui-meme
    if not entrees:
        raise ArchiveError("Rien à archiver.")

    total = sum(p.stat().st_size for p, n in entrees if not n.endswith("/")) or 1
    lu = 0
    try:
        with zipfile.ZipFile(dest, "x", zipfile.ZIP_DEFLATED) as z:   # x : jamais d'écrasement
            for p, nom in entrees:
                if convert.annulation_demandee():
                    raise convert.Annule("annulé")
                info = zipfile.ZipInfo.from_file(p, nom)
                if nom.endswith("/"):
                    z.writestr(info, b"")
                    continue
                info.compress_type = zipfile.ZIP_DEFLATED
                # par blocs et non z.write(): une video de 4 Go doit rester annulable
                with open(p, "rb") as src, z.open(info, "w", force_zip64=info.file_size > 1 << 30) as dst:
                    while bloc := src.read(_BLOC):
                        if convert.annulation_demandee():
                            raise convert.Annule("annulé")
                        dst.write(bloc)
                        lu += len(bloc)
                        if progress:
                            progress(min(1.0, lu / total))
    except convert.Annule:
        dest.unlink(missing_ok=True)
        raise
    except OSError as e:
        # fichier verrouillé, droits, disque plein : message lisible, pas de zip incomplet
        dest.unlink(missing_ok=True)
        raise ArchiveError(f"Archive impossible : {e.strerror or e} "
                           f"({pathlib.Path(e.filename or dest).name})")
    if progress:
        progress(1.0)
    return dest
