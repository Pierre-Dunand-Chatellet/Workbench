"""Tests (assert) pour securite.py. Lancer: python test_securite.py

AUCUN échantillon malveillant ici, pas même le fichier de test EICAR : Defender le mettrait
en quarantaine au milieu du test. Chaque signal est simulé avec un fichier ANODIN (du texte)
qui porte le nom ou les premiers octets à repérer. Le seul en-tête « programme » écrit est un
squelette MZ/PE de 68 octets sans aucun code.
"""
import pathlib
import subprocess
import tempfile
import zipfile

import convert
import securite
from securite import A_VERIFIER, MENACE, SUSPECT


def _faux_pe():
    """« MZ », e_lfanew = 0x40, puis « PE\\0\\0 » : la signature, et rien d'exécutable."""
    return b"MZ" + b"\0" * 0x3A + (0x40).to_bytes(4, "little") + b"PE\0\0"


def _gravites(dossier):
    signaux, _n = securite.analyse_locale(dossier)
    return {(s.chemin.name, s.gravite) for s in signaux}


def test_extension_executable(tmpdir):
    """Un script .bat / .ps1 posé dans un dossier de téléchargements doit être montré."""
    tmp = pathlib.Path(tmpdir)
    for nom in ("outil.bat", "maj.ps1", "raccourci.lnk", "photo.jpg"):
        (tmp / nom).write_text("texte anodin")
    g = _gravites(tmp)
    assert g == {("outil.bat", A_VERIFIER), ("maj.ps1", A_VERIFIER), ("raccourci.lnk", A_VERIFIER)}, g


def test_double_extension(tmpdir):
    """« facture.pdf.exe » s'affiche « facture.pdf » quand Windows masque les extensions :
    c'est un piège, pas un simple programme. « jquery.min.js » n'en est pas un."""
    tmp = pathlib.Path(tmpdir)
    (tmp / "facture.pdf.exe").write_text("texte anodin")
    (tmp / "photo.JPG   .scr").write_text("texte anodin")      # espaces de remplissage
    (tmp / "jquery.min.js").write_text("texte anodin")
    g = _gravites(tmp)
    assert ("facture.pdf.exe", SUSPECT) in g and ("photo.JPG   .scr", SUSPECT) in g, g
    assert ("jquery.min.js", A_VERIFIER) in g and ("jquery.min.js", SUSPECT) not in g, g


def test_rlo(tmpdir):
    """Le caractère RLO (U+202E) retourne la fin du nom : « photo‮gpj.exe » s'affiche
    « photoexe.jpg »."""
    tmp = pathlib.Path(tmpdir)
    (tmp / "photo\u202egpj.exe").write_text("texte anodin")
    (tmp / "note\u202e.txt").write_text("texte anodin")         # pas exécutable, mais trompeur
    g = _gravites(tmp)
    assert ("photo\u202egpj.exe", SUSPECT) in g and ("note\u202e.txt", SUSPECT) in g, g


def test_programme_deguise(tmpdir):
    """Un programme Windows renommé en .jpg passe inaperçu à l'œil : l'en-tête MZ/PE le trahit.
    Un texte qui commence par « MZ » n'est pas un programme, un .pyd en est un normal."""
    tmp = pathlib.Path(tmpdir)
    (tmp / "vacances.jpg").write_bytes(_faux_pe())
    (tmp / "sans_extension").write_bytes(_faux_pe())
    (tmp / "module.pyd").write_bytes(_faux_pe())
    (tmp / "texte.txt").write_bytes(b"MZ ceci n'est qu'un texte" + b" " * 100)
    g = _gravites(tmp)
    assert g == {("vacances.jpg", SUSPECT), ("sans_extension", SUSPECT)}, g


def test_macros_office(tmpdir):
    """.xlsm annonce ses macros ; un .docx qui contient vbaProject.bin les cache."""
    tmp = pathlib.Path(tmpdir)
    (tmp / "budget.xlsm").write_text("texte anodin")
    with zipfile.ZipFile(tmp / "rapport.docx", "w") as z:
        z.writestr("word/document.xml", "<w/>")
        z.writestr("word/vbaProject.bin", b"texte anodin")
    with zipfile.ZipFile(tmp / "propre.docx", "w") as z:
        z.writestr("word/document.xml", "<w/>")
    g = _gravites(tmp)
    assert g == {("budget.xlsm", A_VERIFIER), ("rapport.docx", A_VERIFIER)}, g


def test_zip_qui_contient_des_programmes(tmpdir):
    tmp = pathlib.Path(tmpdir)
    with zipfile.ZipFile(tmp / "cadeau.zip", "w") as z:
        z.writestr("lisez-moi.txt", "bonjour")
        z.writestr("dossier/setup.exe", "texte anodin")
    with zipfile.ZipFile(tmp / "photos.zip", "w") as z:
        z.writestr("a.jpg", "x")
    (tmp / "casse.zip").write_bytes(b"PK\x03\x04 tronque")        # illisible: ignore sans bruit
    signaux, _n = securite.analyse_locale(tmp)
    assert [(s.chemin.name, s.gravite) for s in signaux] == [("cadeau.zip", A_VERIFIER)], signaux
    assert "setup.exe" in signaux[0].raison


def test_liens_non_suivis(tmpdir):
    """os.walk suit les jonctions en Python 3.11 : une jonction vers C:\\Windows faisait
    analyser tout le système, et une jonction circulaire tournait sans fin."""
    tmp = pathlib.Path(tmpdir)
    (tmp / "ailleurs").mkdir()
    (tmp / "ailleurs" / "prog.exe").write_text("texte anodin")
    (tmp / "analyse").mkdir()
    (tmp / "analyse" / "a.txt").write_text("x")
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(tmp / "analyse" / "jonction"),
                        str(tmp / "ailleurs")], capture_output=True)
    assert r.returncode == 0, r
    signaux, n = securite.analyse_locale(tmp / "analyse")
    assert n == 1 and signaux == [], (n, signaux)


def test_fichier_illisible_ignore(tmpdir):
    """Un fichier verrouillé ou disparu entre le listage et la lecture arrêtait tout : il
    garde ses signaux de nom, et l'analyse continue."""
    tmp = pathlib.Path(tmpdir)
    (tmp / "b.bat").write_text("x")
    fantome = tmp / "disparu.exe"
    original = securite.fichiers
    securite.fichiers = lambda d: iter([fantome, tmp / "b.bat"])
    try:
        signaux, n = securite.analyse_locale(tmp)
    finally:
        securite.fichiers = original
    assert n == 2 and {s.chemin.name for s in signaux} == {"disparu.exe", "b.bat"}, signaux


def test_tri_par_gravite(tmpdir):
    """Menace > Suspect > À vérifier, et un fichier = une ligne (avec toutes ses raisons)."""
    tmp = pathlib.Path(tmpdir)
    a, b = tmp / "a.bat", tmp / "z.pdf.exe"
    tries = securite.trier([
        securite.Signal(A_VERIFIER, a, "script"),
        securite.Signal(SUSPECT, b, "double extension"),
        securite.Signal(MENACE, a, "Windows Defender : Faux"),
        securite.Signal(A_VERIFIER, b, "programme"),
    ])
    assert [(s.chemin.name, s.gravite) for s in tries] == [("a.bat", MENACE), ("z.pdf.exe", SUSPECT)]
    assert tries[0].raison == "Windows Defender : Faux ; script", tries[0].raison


def test_defender_absent(tmpdir):
    """Sans Defender (désinstallé, remplacé par un autre antivirus), l'analyse ne doit pas
    échouer : elle le dit, et garde les signaux locaux."""
    tmp = pathlib.Path(tmpdir)
    assert securite.trouver_mpcmdrun(tmp / "pas_de_platform", tmp / "pas_de_pf") is None
    (tmp / "outil.bat").write_text("x")
    r = securite.analyser(tmp, exe=tmp / "nexiste" / "MpCmdRun.exe")
    assert r.defender == "indisponible", r
    assert securite.message_defender(r) == "Defender indisponible, seule l'analyse locale a été faite"
    assert [s.chemin.name for s in r.signaux] == ["outil.bat"]


def test_version_la_plus_recente(tmpdir):
    """Platform\\4.18.10-0 est plus récent que 4.18.9-0 : un tri de texte prenait le 9."""
    tmp = pathlib.Path(tmpdir)
    for v in ("4.18.9-0", "4.18.10-0", "4.18.11-0"):
        (tmp / "Platform" / v).mkdir(parents=True)
    for v in ("4.18.9-0", "4.18.10-0"):          # la plus récente n'a pas d'exe: on la saute
        (tmp / "Platform" / v / "MpCmdRun.exe").write_text("")
    trouve = securite.trouver_mpcmdrun(tmp / "Platform", tmp / "pf")
    assert trouve == tmp / "Platform" / "4.18.10-0" / "MpCmdRun.exe", trouve


SORTIE_FICTIVE = """Scan starting...
Scan finished.
Scanning C:\\Users\\x\\Downloads found 2 threats.

LIST OF DETECTED THREATS
----------------------------
Threat                  : Trojan:Win32/Faux.A
Resources               : 1 total
    file                : C:\\Users\\x\\Downloads\\facture.pdf.exe
----------------------------
Threat                  : Virus:DOS/Faux_Test
Resources               : 2 total
    containerfile       : C:\\Users\\x\\Downloads\\cadeau.zip
    file                : C:\\Users\\x\\Downloads\\cadeau.zip->setup.exe
----------------------------
"""


def test_lecture_sortie_defender():
    m = securite.lire_sortie_defender(SORTIE_FICTIVE)
    assert m == [("Trojan:Win32/Faux.A", r"C:\Users\x\Downloads\facture.pdf.exe"),
                 ("Virus:DOS/Faux_Test", r"C:\Users\x\Downloads\cadeau.zip"),
                 ("Virus:DOS/Faux_Test", r"C:\Users\x\Downloads\cadeau.zip->setup.exe")], m
    assert securite.lire_sortie_defender("Scan finished.\nfound no threats.") == []


def test_codes_de_retour_defender(tmpdir):
    """Code 2 = menaces (sortie lue), autre code = indisponible. On remplace MpCmdRun par un
    petit .bat qui imite sa sortie : rien de réel n'est détecté ni mis en quarantaine."""
    tmp = pathlib.Path(tmpdir)
    faux = tmp / "FauxMpCmdRun.bat"
    lignes = ["@echo off", "echo Scan starting...",
              "echo Threat                  : Trojan:Win32/Faux.A",
              "echo     file                : %~dp0facture.pdf.exe",
              "echo Threat                  : Virus:DOS/Faux_Test",
              "echo     file                : %~dp0cadeau.zip-^>setup.exe",
              "exit /b 2"]
    faux.write_text("\r\n".join(lignes) + "\r\n", encoding="ascii")
    (tmp / "facture.pdf.exe").write_text("texte anodin")
    r = securite.analyser(tmp, exe=faux)
    assert r.defender == "menaces", r
    menaces = [s for s in r.signaux if s.gravite == MENACE]
    assert {s.chemin.name for s in menaces} == {"facture.pdf.exe", "cadeau.zip"}, menaces
    # facture.pdf.exe: menace ET double extension, fusionnees sur une ligne
    ligne = next(s for s in r.signaux if s.chemin.name == "facture.pdf.exe")
    assert "Faux.A" in ligne.raison and "double extension" in ligne.raison, ligne
    assert "setup.exe" in next(s for s in menaces if s.chemin.name == "cadeau.zip").raison
    assert securite.message_defender(r) == "Defender : 2 menaces trouvées"

    faux.write_text("@echo off\r\necho Defender est desactive\r\nexit /b 5\r\n", encoding="ascii")
    etat, menaces, detail = securite.analyse_defender(tmp, exe=faux)
    assert etat == "indisponible" and "0x5" in detail, (etat, detail)


def test_vraie_analyse_defender(tmpdir):
    """Un vrai passage de MpCmdRun sur un dossier sain : rien trouvé."""
    exe = securite.trouver_mpcmdrun()
    if exe is None:
        print("  (Defender absent : test saute)")
        return
    tmp = pathlib.Path(tmpdir)
    (tmp / "note.txt").write_text("rien de dangereux")
    etat, menaces, detail = securite.analyse_defender(tmp)
    assert (etat, menaces) == ("rien", []), (etat, menaces, detail)


def test_annulation(tmpdir):
    tmp = pathlib.Path(tmpdir)
    (tmp / "a.txt").write_text("x")
    convert.annuler()
    try:
        securite.analyser(tmp)
        raise AssertionError("annulation ignoree")
    except convert.Annule:
        pass
    finally:
        convert.reprendre()


if __name__ == "__main__":
    test_lecture_sortie_defender()
    print("  test_lecture_sortie_defender : ok")
    for essai in (test_extension_executable, test_double_extension, test_rlo,
                  test_programme_deguise, test_macros_office, test_zip_qui_contient_des_programmes,
                  test_liens_non_suivis, test_fichier_illisible_ignore, test_tri_par_gravite,
                  test_defender_absent, test_version_la_plus_recente, test_codes_de_retour_defender,
                  test_vraie_analyse_defender, test_annulation):
        with tempfile.TemporaryDirectory() as d:
            essai(d)
        print(f"  {essai.__name__} : ok")
    print("OK")
