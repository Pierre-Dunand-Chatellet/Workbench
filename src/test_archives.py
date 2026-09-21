"""Tests (assert) pour archives.py. Lancer: python test_archives.py

Pas besoin de ffmpeg : les « médias » sont des octets quelconques, seule l'extension compte
pour extraire_medias(). Aucun fichier dangereux n'est créé : les zips « piégés » ne
contiennent que du texte, c'est leur NOM de membre qui est piégé.
"""
import pathlib
import tempfile
import zipfile

import archives
import convert


def _zip(chemin, membres, methode=zipfile.ZIP_STORED):
    """membres : {nom: contenu}. zipfile écrit les noms tels quels, « ../ » compris."""
    with zipfile.ZipFile(chemin, "w", methode) as z:
        for nom, contenu in membres.items():
            z.writestr(nom, contenu)
    return chemin


def _marquer_chiffre(chemin, nom):
    """zipfile ne sait pas écrire un membre chiffré : on lève le bit 0 des drapeaux à la main
    (en-tête local : drapeaux à +6, nom à +30 ; répertoire central : +8 et +46)."""
    data = bytearray(chemin.read_bytes())
    cible = nom.encode()
    for sig, off_drap, off_long, off_nom in ((b"PK\x03\x04", 6, 26, 30), (b"PK\x01\x02", 8, 28, 46)):
        i = data.find(sig)
        while i != -1:
            n = int.from_bytes(data[i + off_long:i + off_long + 2], "little")
            if data[i + off_nom:i + off_nom + n] == cible:
                data[i + off_drap] |= 0x1
            i = data.find(sig, i + 4)
    chemin.write_bytes(bytes(data))


def test_zip_slip_refuse(tmpdir):
    """Écrire chaque membre avec open(dest / nom) posait « ../evil.txt » HORS du dossier
    choisi (faille « zip slip »), « C:/... » sur un autre lecteur, « CON.txt » sur la console.
    Chaque forme doit être refusée ET signalée, rien ne doit sortir de la destination."""
    tmp = pathlib.Path(tmpdir)
    pieges = ["../evil.txt", "a/../../esc.txt", "/abs.txt", "C:/lecteur.txt", "C:relatif.txt",
              "flux.txt:cache", "CON.txt", "nul", "... /x.txt"]
    z = _zip(tmp / "piege.zip", {**{n: b"piege" for n in pieges}, "ok/fichier.txt": b"sain"})
    dest = tmp / "dest"
    r = archives.extraire(z, dest)
    assert [p.relative_to(dest.resolve()).as_posix() for p in r.extraits] == ["ok/fichier.txt"], r.extraits
    refuses = {nom for nom, _raison in r.refuses}
    assert refuses == set(pieges), refuses ^ set(pieges)
    # rien n'a fui a cote de la destination
    assert sorted(p.name for p in tmp.iterdir()) == ["dest", "piege.zip"], list(tmp.iterdir())
    # formes inoffensives acceptees
    for sain in ("photo.jpg", "./photo.jpg", "a//b/photo.jpg", "dossier/"):
        assert archives.raison_refus(sain) is None, sain
    # et lister() le dit, pour que l'interface puisse l'afficher
    assert all(e["refus"] for e in archives.lister(z) if e["nom"] in pieges)


def test_plafond_taille(tmpdir):
    """Une bombe zip (quelques Ko qui en font des Go) remplissait le disque : la taille
    décompressée totale est plafonnée, et rien n'est écrit si on la dépasse."""
    tmp = pathlib.Path(tmpdir)
    z = _zip(tmp / "gros.zip", {"a.bin": b"\0" * 800, "b.bin": b"\0" * 800}, zipfile.ZIP_DEFLATED)
    ancien = archives.MAX_TOTAL
    archives.MAX_TOTAL = 1000
    try:
        try:
            archives.extraire(z, tmp / "dest")
            raise AssertionError("plafond de taille non applique")
        except archives.ArchiveError as e:
            assert "Extraction refusée" in str(e), e
        assert not (tmp / "dest").exists() or not any((tmp / "dest").iterdir())
        # sous le plafond, ca passe
        assert len(archives.extraire(z, tmp / "dest2", membres=["a.bin"]).extraits) == 1
    finally:
        archives.MAX_TOTAL = ancien


def test_plafond_nombre(tmpdir):
    """Des centaines de milliers d'entrées vides bloquaient l'interface (une ligne de liste
    chacune) : au-delà du plafond, ni liste ni extraction, avec un message clair."""
    tmp = pathlib.Path(tmpdir)
    z = _zip(tmp / "nombreux.zip", {f"f{i}.txt": b"x" for i in range(6)})
    ancien = archives.MAX_ENTREES
    archives.MAX_ENTREES = 5
    try:
        for appel in (lambda: archives.lister(z), lambda: archives.extraire(z, tmp / "dest")):
            try:
                appel()
                raise AssertionError("plafond de nombre non applique")
            except archives.ArchiveError as e:
                assert "6 éléments" in str(e), e
    finally:
        archives.MAX_ENTREES = ancien


def test_seuls_les_medias(tmpdir):
    """extraire_medias() ne doit sortir que ce que convert.FORMATS sait traiter : un .exe ou
    un .txt glissé dans un zip de photos n'a rien à faire dans la liste des fichiers."""
    tmp = pathlib.Path(tmpdir)
    z = _zip(tmp / "lot.zip", {"photo.png": b"p", "sous/IMG.JPG": b"j", "clip.mp4": b"v",
                               "son.mp3": b"s", "notes.txt": b"t", "prog.exe": b"texte anodin",
                               "vide/": b""})
    r = archives.extraire_medias(z, tmp / "dest")
    noms = sorted(p.relative_to((tmp / "dest").resolve()).as_posix() for p in r.extraits)
    assert noms == ["clip.mp4", "photo.png", "son.mp3", "sous/IMG.JPG"], noms
    assert not (tmp / "dest" / "prog.exe").exists()


def test_jamais_d_ecrasement(tmpdir):
    """Extraire deux fois au même endroit, ou sur un fichier existant, écrasait l'original.
    Même règle que chemin_sortie() : « nom (1).ext »."""
    tmp = pathlib.Path(tmpdir)
    z = _zip(tmp / "a.zip", {"fichier.txt": b"du zip"})
    dest = tmp / "dest"
    dest.mkdir()
    (dest / "fichier.txt").write_bytes(b"original")
    archives.extraire(z, dest)
    archives.extraire(z, dest)
    assert (dest / "fichier.txt").read_bytes() == b"original"
    assert (dest / "fichier (1).txt").read_bytes() == b"du zip"
    assert (dest / "fichier (2).txt").read_bytes() == b"du zip"


def test_zip_corrompu(tmpdir):
    """Un zip abîmé levait une zipfile.BadZipFile brute, en anglais, et laissait parfois un
    fichier à moitié écrit. Il faut une ArchiveError en français, et aucun reste."""
    tmp = pathlib.Path(tmpdir)
    faux = tmp / "faux.zip"
    faux.write_bytes(b"ceci n'est pas un zip" * 10)
    bon = _zip(tmp / "bon.zip", {"a.txt": b"A" * 5000, "b.txt": b"B" * 5000}, zipfile.ZIP_DEFLATED)
    tronque = tmp / "tronque.zip"
    tronque.write_bytes(bon.read_bytes()[:60])
    for z in (faux, tronque, tmp / "absent.zip"):
        for appel in (lambda: archives.lister(z), lambda: archives.extraire(z, tmp / "d")):
            try:
                appel()
                raise AssertionError(f"{z.name} accepte")
            except archives.ArchiveError as e:
                assert z.name in str(e), e

    # un membre aux donnees abimees: il est signale, les autres sortent quand meme
    data = bytearray(bon.read_bytes())
    debut = 30 + len("a.txt")                      # donnees de a.txt, juste apres son en-tete
    for i in range(debut, debut + 8):
        data[i] ^= 0xFF
    abime = tmp / "abime.zip"
    abime.write_bytes(bytes(data))
    r = archives.extraire(abime, tmp / "dest")
    assert r.endommages == ["a.txt"], r
    assert [p.name for p in r.extraits] == ["b.txt"], r.extraits
    assert not (tmp / "dest" / "a.txt").exists(), "fichier a moitie ecrit laisse sur le disque"


def test_chiffres_ignores(tmpdir):
    """Un membre chiffré faisait planter l'extraction (RuntimeError « password required ») :
    il est ignoré et signalé, le reste sort."""
    tmp = pathlib.Path(tmpdir)
    z = _zip(tmp / "c.zip", {"secret.jpg": b"x", "public.jpg": b"y"})
    _marquer_chiffre(z, "secret.jpg")
    contenu = {e["nom"]: e for e in archives.lister(z)}
    assert contenu["secret.jpg"]["chiffre"] and not contenu["public.jpg"]["chiffre"]
    r = archives.extraire_medias(z, tmp / "dest")
    assert r.chiffres == ["secret.jpg"], r
    assert [p.name for p in r.extraits] == ["public.jpg"]
    assert "chiffré" in r.resume()


def test_annulation(tmpdir):
    """« Annuler » (convert.annuler) doit aussi arrêter une extraction, sans fichier partiel."""
    tmp = pathlib.Path(tmpdir)
    z = _zip(tmp / "a.zip", {f"{i}.png": b"x" * 100 for i in range(5)})
    convert.annuler()
    try:
        archives.extraire(z, tmp / "dest")
        raise AssertionError("annulation ignoree")
    except convert.Annule:
        pass
    finally:
        convert.reprendre()
    assert not any((tmp / "dest").rglob("*.png"))


def test_compresser(tmpdir):
    """Créer un zip : dossier avec son contenu (dossier vide compris), fichiers isolés au même
    nom renumérotés, aller-retour identique, jamais d'écrasement, annulation sans reste."""
    tmp = pathlib.Path(tmpdir)
    (tmp / "vacances" / "jour1").mkdir(parents=True)
    (tmp / "vacances" / "vide").mkdir()
    (tmp / "vacances" / "jour1" / "plage.jpg").write_bytes(b"sable" * 1000)
    (tmp / "vacances" / "note.txt").write_text("bonjour")
    (tmp / "a").mkdir(); (tmp / "b").mkdir()
    (tmp / "a" / "photo.jpg").write_bytes(b"A"); (tmp / "b" / "photo.jpg").write_bytes(b"B")

    z = archives.compresser([tmp / "vacances", tmp / "a" / "photo.jpg", tmp / "b" / "photo.jpg"],
                            tmp / "lot.zip")
    assert z == tmp / "lot.zip"
    noms = {e["nom"] for e in archives.lister(z)}
    assert noms == {"vacances/", "vacances/jour1/", "vacances/vide/", "vacances/jour1/plage.jpg",
                    "vacances/note.txt", "photo.jpg", "photo (1).jpg"}, noms
    r = archives.extraire(z, tmp / "sortie")
    assert (tmp / "sortie" / "vacances" / "jour1" / "plage.jpg").read_bytes() == b"sable" * 1000
    assert (tmp / "sortie" / "vacances" / "vide").is_dir()
    assert {(tmp / "sortie" / n).read_bytes() for n in ("photo.jpg", "photo (1).jpg")} == {b"A", b"B"}

    z2 = archives.compresser([tmp / "vacances"], tmp / "lot.zip")     # deja pris
    assert z2.name == "lot (1).zip" and z.exists()

    convert.annuler()
    try:
        archives.compresser([tmp / "vacances"], tmp / "annule.zip")
        assert False, "l'annulation n'a pas arrete la compression"
    except convert.Annule:
        pass
    finally:
        convert.reprendre()
    assert not (tmp / "annule.zip").exists(), "zip a moitie ecrit laisse sur le disque"

    try:
        archives.compresser([tmp / "rien"], tmp / "vide.zip")
        assert False
    except archives.ArchiveError:
        pass


if __name__ == "__main__":
    for essai in (test_zip_slip_refuse, test_plafond_taille, test_plafond_nombre,
                  test_seuls_les_medias, test_jamais_d_ecrasement, test_zip_corrompu,
                  test_chiffres_ignores, test_annulation, test_compresser):
        with tempfile.TemporaryDirectory() as d:
            essai(d)
        print(f"  {essai.__name__} : ok")
    print("OK")
