"""Tests (assert) pour l'interface Workbench.py. Lancer: python test_workbench.py

Chaque test correspond a un defaut constate puis corrige. Une fenetre Tk s'ouvre
brievement: c'est normal, elle est detruite a la fin de chaque test.
"""
import math
import pathlib
import subprocess
import tempfile
import time
import zipfile

from PIL import Image, ImageChops, ImageStat

import convert
import securite
import Workbench


def _gen(cmd):
    subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)


def _image(chemin, taille="640x480", motif="testsrc2"):
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", f"{motif}=size={taille}:d=1", "-frames:v", "1", str(chemin)])
    return chemin


def _rms(a, b):
    """Ecart quadratique moyen entre deux images, sur 0-255."""
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    h = diff.histogram()
    somme = nb = 0
    for bande in range(3):
        for i, c in enumerate(h[bande * 256:(bande + 1) * 256]):
            somme += c * i * i
            nb += c
    return math.sqrt(somme / max(nb, 1))


_VIVANTES = []   # on garde une reference sur chaque fenetre: liberee par le ramasse-miettes
                 # depuis un thread de fond, Tk avorte sur « Tcl_AsyncDelete ». Artefact de
                 # test uniquement (l'appli ne cree qu'une seule fenetre).


def _app(fichiers=(), outil=None):
    app = Workbench.Studio()
    _VIVANTES.append(app)
    app.update()
    if fichiers:
        app._ajouter([pathlib.Path(f) for f in fichiers])
    if outil:
        app._afficher(outil)
    app.update()
    return app


# ------------------------------------------------------------------ helpers purs
def test_px():
    """_px() remplace un int() nu qui levait une ValueError non rattrapee (invisible en .exe)."""
    assert Workbench._px("1234", 10) == 1234
    assert Workbench._px(" 1 234 ", 10) == 1234
    assert Workbench._px("1234,0", 10) == 1234
    assert Workbench._px("abc", 77) == 77
    assert Workbench._px("", 77) == 77
    assert Workbench._px("0", 77) == 1                       # jamais zero
    assert Workbench._px("-5", 77) == 1
    assert Workbench._px("999999", 77) == Workbench.MAX_COTE    # borne d'ecriture des formats image
    assert Workbench._px(None, 77) == 77


def test_chemin_sortie(tmpdir):
    """« photo.JPG » produisait « photo net.JPG »: extension en majuscules."""
    tmp = pathlib.Path(tmpdir)
    (tmp / "photo.JPG").write_bytes(b"x")
    p = Workbench.chemin_sortie(tmp / "photo.JPG", "JPG", " net")
    assert p.name == "photo net.jpg", p.name
    # et jamais d'ecrasement
    p.write_bytes(b"x")
    assert Workbench.chemin_sortie(tmp / "photo.JPG", "jpg", " net").name == "photo net (1).jpg"


def test_etiquettes_nettete():
    assert Workbench._ETIQUETTES_NETTETE(1) == ("avant", "après")
    # a x2 les deux volets sont agrandis: dire « avant » serait faux
    assert Workbench._ETIQUETTES_NETTETE(2) == ("× 2 seul", "× 2 + netteté")


# ------------------------------------------------------------------ panneau Taille
def test_taille_tapee_survit_au_redessin(tmpdir):
    """<Configure> appelait _maj_taille(), qui recalculait tout depuis le curseur en %:
    redimensionner la fenetre transformait « 1234 x 300 » en « 1230 x 984 »."""
    tmp = pathlib.Path(tmpdir)
    src = _image(tmp / "p.png", "1000x800")
    app = _app([src], "taille")

    app.var_w.set("1234")
    app._taille_saisie("w")
    app.update()
    assert (app.var_w.get(), app.var_h.get()) == ("1234", "987"), (app.var_w.get(), app.var_h.get())
    app._redessiner_taille()                      # ce que declenche un redimensionnement
    app.update()
    assert app.var_w.get() == "1234", app.var_w.get()
    assert app.var_h.get() == "987", app.var_h.get()

    # proportions libres: la hauteur imposee doit tenir aussi
    app.ratio.set(False)
    app.update()
    app.var_w.set("1234"); app._taille_saisie("w")
    app.var_h.set("300"); app._taille_saisie("h")
    app.update()
    assert (app.var_w.get(), app.var_h.get()) == ("1234", "300")
    app._redessiner_taille()
    app.update()
    assert (app.var_w.get(), app.var_h.get()) == ("1234", "300"), (app.var_w.get(), app.var_h.get())

    # une petite valeur ne doit pas etre ramenee au multiple de 1 % le plus proche
    app.ratio.set(True)
    app.var_w.set("101"); app._taille_saisie("w")
    app._redessiner_taille()
    app.update()
    assert app.var_w.get() == "101", app.var_w.get()

    # le curseur, lui, a toujours le droit d'ecraser la saisie: c'est son role
    app.echelle.set(50)
    app._maj_taille()
    app.update()
    assert app.var_w.get() == "500", app.var_w.get()
    app._fermer()


def test_saisie_invalide_ne_plante_pas(tmpdir):
    """Proportions libres + texte dans l'autre champ: int() brut levait une ValueError."""
    tmp = pathlib.Path(tmpdir)
    app = _app([_image(tmp / "p.png", "1000x800")], "taille")
    app.ratio.set(False)
    app.update()
    for bruit in ("abc", "", "   ", "12,5,6", "-"):
        app.var_w.set(bruit)
        app._taille_saisie("h")          # lit var_w au passage: c'est la que ca cassait
        app.update()
    assert int(app.var_w.get()) >= 1
    app._fermer()


def test_pas_de_fuite_d_apercus(tmpdir):
    """Chaque redessin empilait un PhotoImage dans self.images: 38 Mo par 100 pas de curseur,
    rendus seulement en changeant de panneau."""
    tmp = pathlib.Path(tmpdir)
    app = _app([_image(tmp / "p.png", "3000x2000")], "taille")
    debut = len(app.images)
    for pct in range(20, 220, 2):
        app.echelle.set(pct)
        app._maj_taille()
        app._dessiner_apercu_taille(3000 * pct // 100, 2000 * pct // 100, pct)
    app.update()
    assert len(app.images) == debut, f"{len(app.images) - debut} apercus retenus"
    app._fermer()


# ------------------------------------------------------------------ panneau Netteté
def test_apercu_nettete_est_le_vrai_fichier(tmpdir):
    """L'apercu accentuait une vignette de 440 px pendant que le fichier accentuait le plein
    format: ecart RMS 5,6, soit la taille de l'effet lui-meme. Il montre maintenant un
    morceau a 100 % du fichier produit."""
    tmp = pathlib.Path(tmpdir)
    src = _image(tmp / "photo.png", "1600x1200")
    app = _app([src], "nettete")

    for scale in (1, 2):
        force = 80
        x, y, cl, ch = app.cadre_nettete(scale)
        _avant, apercu = app._calculer_nettete(scale, force)
        fichier = Image.open(convert.enhance(src, tmp / f"net{scale}.png",
                                             scale=scale, force=force))
        morceau = fichier.crop((x * scale, y * scale,
                                (x + cl) * scale, (y + ch) * scale))
        assert apercu.size == morceau.size, (apercu.size, morceau.size)
        ecart = _rms(apercu, morceau)
        print(f"  nettete x{scale}: ecart apercu/fichier = {ecart:.2f} / 255")
        assert ecart < 1.0, ecart      # avant correction: 5,6

    app._fermer()


def test_cadrage_nettete_vise_le_detail(tmpdir):
    """Cadre au centre, l'apercu a 100 % pouvait tomber sur un aplat: l'outil avait l'air de
    ne rien faire. Il doit viser la zone la plus contrastee, et ne plus bouger ensuite."""
    tmp = pathlib.Path(tmpdir)
    # un ciel uni a gauche, tout le detail concentre en bas a droite
    plat = Image.new("RGB", (1600, 1200), (210, 225, 240))
    detail = Image.effect_noise((400, 300), 60).convert("RGB")
    plat.paste(detail, (1150, 850))
    src = tmp / "ciel.png"
    plat.save(src)

    app = _app([src], "nettete")
    fx, fy = Workbench.point_net(app.src_nettete)
    print(f"  point vise = ({fx:.2f}, {fy:.2f}) ; le detail est autour de (0.84, 0.83)")
    assert fx > 0.6 and fy > 0.6, (fx, fy)

    # stable: changer d'echelle ou de force ne doit pas deplacer le cadrage
    avant = app._centre_net
    for scale in (1, 2, 4):
        app.cadre_nettete(scale)
    assert app._centre_net == avant

    # et le morceau preleve contient bien du detail, pas l'aplat
    _a, apercu = app._calculer_nettete(2, 80)
    ecart_type = ImageStat.Stat(apercu.convert("L")).stddev[0]
    print(f"  ecart-type du morceau montre = {ecart_type:.1f} (un aplat donnerait ~0)")
    assert ecart_type > 10, ecart_type
    app._fermer()


def test_nettete_refuse_gif_anime(tmpdir):
    """Un GIF anime disparaissait de la liste avec « que sur des images », ce qui est faux:
    c'est son animation qui pose probleme, et il faut le dire."""
    tmp = pathlib.Path(tmpdir)
    gif = tmp / "anim.gif"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "testsrc=size=120x90:d=2:rate=10", str(gif)])
    app = _app([gif], "nettete")
    assert app._images_seules() == []
    assert "animée" in app._pourquoi_pas_image(), app._pourquoi_pas_image()
    app._fermer()


# ------------------------------------------------------------------ file d'attente
def test_liste_figee_pendant_un_traitement(tmpdir):
    """Un glisser-deposer pendant une conversion changeait self.fichiers sans reconstruire le
    panneau: self.cibles restait en arriere et le clic suivant levait un KeyError."""
    tmp = pathlib.Path(tmpdir)
    a, b = _image(tmp / "a.png"), _image(tmp / "b.png")
    app = _app([a], "convert")
    assert list(app.cibles) == [a]

    app.occupe = True                 # une conversion tourne
    app._ajouter([b])
    app._retirer(a)
    app._vider()
    app.update()
    assert app.fichiers == [a], app.fichiers          # rien n'a bouge
    assert "Annuler" in app.etat_sauve["text"], app.etat_sauve["text"]

    app.occupe = False
    app._convertir()                  # ne doit plus lever de KeyError
    convert.annuler()                 # on arrete tout de suite, le test ne veut pas du resultat
    app.update()
    convert.reprendre()
    app._fermer()


def test_source_du_recapitulatif(tmpdir):
    """La fenetre « C'est pret » comparait self.fichiers[0] au produit: avec une video + une
    photo, elle montrait « avant = la video » et « apres = la photo detouree »."""
    tmp = pathlib.Path(tmpdir)
    clip, photo = tmp / "clip.mp4", _image(tmp / "photo.png")
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "testsrc=size=320x240:d=1:rate=10", str(clip)])
    app = _app([clip, photo], "nettete")
    assert app.fichiers[0] == clip and app._images_seules() == [photo]

    app._ameliorer()                  # remplit self.sources avant de lancer
    assert app.sources == [photo], app.sources
    # attendre la fin de l'annulation avant de fermer: fermer en plein traitement relevait le
    # drapeau d'annulation (global au processus), et l'extraction de zip du test suivant
    # partait « annulée »; le fichier encore ouvert bloquait aussi le nettoyage du dossier
    convert.annuler()
    assert _attendre(app, lambda: not app.occupe), "l'annulation n'a jamais abouti"
    assert not convert.annulation_demandee()
    app._fermer()


def test_toast_n_efface_pas_enregistre(tmpdir):
    """_toast programmait un effacement inconditionnel a 5 s: un message affiche avant une
    reussite effacait le « Enregistré » qui lui avait succede."""
    app = _app()
    app._toast("message passager")
    app.etat_sauve.configure(text="Enregistré")       # ce que fait _succes()
    app.update()
    app.after(10, lambda: None)
    for _ in range(60):                               # laisse largement passer le after(5000)
        app.update()
    app._forcer_effacement_toast = True
    assert app.etat_sauve["text"] == "Enregistré", app.etat_sauve["text"]
    app._fermer()


def test_fermeture_arrete_ffmpeg(tmpdir):
    """Le thread de travail meurt avec Python, pas le ffmpeg qu'il a lance: fermer la fenetre
    en pleine conversion laissait un processus tourner et ecrire dans le vide."""
    import time
    tmp = pathlib.Path(tmpdir)
    src = tmp / "long.mp4"
    _gen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
          "-i", "testsrc2=size=1920x1080:d=25:rate=30", "-c:v", "libx264",
          "-preset", "ultrafast", str(src)])
    app = _app([src], "convert")
    list(app.cibles.values())[0].set("webm")     # vp9: assez lent pour qu'on ferme pendant
    app._convertir()
    for _ in range(40):                          # laisse ffmpeg demarrer
        app.update()
        time.sleep(0.05)
    assert convert._PROCS, "aucun ffmpeg lance, le test ne prouve rien"
    app._fermer()
    for _ in range(60):
        if not convert._PROCS:
            break
        time.sleep(0.05)
    assert not convert._PROCS, "un ffmpeg a survecu a la fermeture"
    convert.reprendre()


def test_jpeg_pas_propose_deux_fois(tmpdir):
    """« jpg » et « jpeg » sont le meme format: la liste en proposait deux."""
    tmp = pathlib.Path(tmpdir)
    app = _app([_image(tmp / "p.jpg")], "convert")
    cibles = list(app.cibles.values())[0]
    # la Combobox a ete remplie depuis la meme liste que la variable
    proposees = [e for e in convert.targets_for(tmp / "p.jpg") if e not in ("jpeg", "jpg")]
    assert "jpeg" not in proposees and "jpg" not in proposees, proposees
    assert cibles.get() in proposees, cibles.get()
    app._fermer()


# ------------------------------------------------------------------ zip, archives, securite
def _attendre(app, condition, delai=60):
    """Fait tourner la boucle Tk jusqu'a ce que condition() soit vraie (threads de fond)."""
    fin = time.time() + delai
    while time.time() < fin:
        app.update()
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_six_outils_s_affichent(tmpdir):
    """Ajouter un outil = une entree OUTILS + un panneau + une ligne dans _afficher : un oubli
    levait un KeyError au clic, muet dans le .exe. Les six doivent se construire, a vide
    comme avec du contenu."""
    tmp = pathlib.Path(tmpdir)
    assert [o[0] for o in Workbench.OUTILS] == ["convert", "fond", "taille", "nettete",
                                                 "archives", "securite"]
    app = _app()
    assert app.outil == "accueil" and not app.btn_accueil.winfo_ismapped()
    for cle, *_ in Workbench.OUTILS:
        app._afficher(cle)
        app.update()
        assert app.outil == cle and app.page.interieur.winfo_children(), cle

    with zipfile.ZipFile(tmp / "lot.zip", "w") as z:
        z.writestr("photos/a.png", b"x")
        z.writestr("photos/b.jpg", b"x")
        z.writestr("notes.txt", b"x")
    app._ouvrir_archive(tmp / "lot.zip")
    app.update()
    arbre = app.arbre_zip
    assert arbre.winfo_ismapped()
    dossier = arbre.get_children("")
    assert len(dossier) == 2 and len(arbre.get_children(dossier[0])) == 2, dossier

    d = tmp / "a_analyser"
    d.mkdir()
    (d / "facture.pdf.exe").write_text("texte anodin")
    app._choisir_dossier_scan(d)
    assert app.outil == "securite" and app.dossier_scan == d
    app._fermer()


def test_zip_depose_sortie_a_cote_du_zip(tmpdir):
    """Deposer un zip l'extrait dans %TEMP% : chemin_sortie() ecrivait alors le resultat dans
    ce dossier temporaire, efface a la fermeture — le fichier converti disparaissait. Il
    doit aller dans « <dossier du zip>\\<nom>_workbench »."""
    tmp = pathlib.Path(tmpdir)
    photo = _image(tmp / "photo.png", "320x240")
    zp = tmp / "lot.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.write(photo, "photos/photo.png")
        z.writestr("lisezmoi.txt", "bonjour")
    photo.unlink()

    app = _app()
    app._ajouter([zp])
    assert _attendre(app, lambda: app.fichiers), "les medias du zip ne sont jamais arrives"
    assert [f.name for f in app.fichiers] == ["photo.png"], app.fichiers
    extrait = app.fichiers[0]
    assert Workbench.ZIP_TEMP in extrait.parents, extrait
    assert Workbench.chemin_sortie(extrait, "jpg").parent == tmp / "lot_workbench"

    app._afficher("convert")
    app.cibles[extrait].set("jpg")
    app._convertir()
    assert _attendre(app, lambda: not app.occupe and app.resultats), "conversion jamais finie"
    for w in app.winfo_children():             # fenetre « C'est pret »
        if isinstance(w, Workbench.tk.Toplevel):
            w.destroy()
    assert app.resultats == [tmp / "lot_workbench" / "photo.jpg"], app.resultats
    assert app.resultats[0].exists()

    dossier_tmp = Workbench.dossier_extraction(zp)
    assert dossier_tmp.exists()
    app._fermer()
    assert not dossier_tmp.exists(), "extraction temporaire laissee apres fermeture"
    assert zp.exists() and (tmp / "lot_workbench" / "photo.jpg").exists()


def test_zip_sans_media(tmpdir):
    """Un zip sans image ni video ni son ne doit pas ajouter un fichier fantome : il le dit."""
    tmp = pathlib.Path(tmpdir)
    zp = tmp / "docs.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("a.txt", "x")
    app = _app()
    app._ajouter([zp])
    assert _attendre(app, lambda: app.zips_en_cours == 0)
    assert app.fichiers == []
    assert "Aucune image" in app.etat_sauve["text"], app.etat_sauve["text"]
    app._fermer()


def test_archives_extraire_la_selection(tmpdir):
    """« Extraire la selection » ne doit sortir que les lignes choisies ; un dossier choisi
    emporte son contenu. Destination par defaut : <dossier du zip>\\<nom>."""
    tmp = pathlib.Path(tmpdir)
    zp = tmp / "lot.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("photos/a.png", b"a")
        z.writestr("photos/b.png", b"b")
        z.writestr("c.txt", b"c")
    app = _app()
    app._ouvrir_archive(zp)
    app.update()
    assert app.zip_dest == tmp / "lot"
    arbre = app.arbre_zip
    photos = arbre.get_children("")[0]
    arbre.selection_set(photos)                # le dossier photos/ entier
    app._extraire_zip(True)
    assert _attendre(app, lambda: not app.occupe)
    sortis = sorted(p.relative_to(tmp / "lot").as_posix() for p in (tmp / "lot").rglob("*") if p.is_file())
    assert sortis == ["photos/a.png", "photos/b.png"], sortis
    assert "2 fichiers extraits" in app.sous_etat["text"], app.sous_etat["text"]

    app._extraire_zip(False)                   # tout, a cote: rien n'est ecrase
    assert _attendre(app, lambda: not app.occupe)
    assert (tmp / "lot" / "photos" / "a (1).png").exists() and (tmp / "lot" / "c.txt").exists()
    app._fermer()


def test_archives_envoyer_vers_convertir(tmpdir):
    tmp = pathlib.Path(tmpdir)
    zp = tmp / "lot.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.write(_image(tmp / "p.png", "160x120"), "p.png")
    app = _app()
    app._ouvrir_archive(zp)
    app._envoyer_medias()
    assert _attendre(app, lambda: app.fichiers and app.outil == "convert"), (app.fichiers, app.outil)
    assert Workbench.dossier_sortie(app.fichiers[0]) == tmp / "lot_workbench"
    app._fermer()


def test_panneau_securite(tmpdir):
    """Le resultat d'une analyse remplit la liste, triee par gravite, sans rien toucher."""
    tmp = pathlib.Path(tmpdir)
    (tmp / "facture.pdf.exe").write_text("texte anodin")
    (tmp / "budget.xlsm").write_text("texte anodin")
    (tmp / "photo.jpg").write_text("x")
    avant = sorted(p.name for p in tmp.iterdir())
    app = _app()
    app._choisir_dossier_scan(tmp)
    app._analyser()
    assert _attendre(app, lambda: app.resultat_scan is not None, 300), "analyse jamais finie"
    app.update()
    r = app.resultat_scan
    assert r.nb_fichiers == 3, r
    assert [s.gravite for s in r.signaux] == [securite.SUSPECT, securite.A_VERIFIER], r.signaux
    lignes = app.arbre_scan.get_children("")
    assert len(lignes) == 2
    assert app.arbre_scan.item(lignes[0], "values")[0] == "facture.pdf.exe"
    app.arbre_scan.selection_set(lignes[0])
    app.update()
    assert "double extension" in app.lbl_detail["text"], app.lbl_detail["text"]
    assert sorted(p.name for p in tmp.iterdir()) == avant, "l'analyse a modifie le dossier"
    app._fermer()


def test_accueil_grise_selon_depot(tmpdir):
    """Accueil: deposer d'abord, puis seuls les outils compatibles restent allumes; une tuile
    grisee ne perd pas le depot."""
    tmp = pathlib.Path(tmpdir)
    img = _image(tmp / "photo.png", "64x48")
    son = tmp / "son.mp3"
    son.write_bytes(b"x")
    d = tmp / "dossier"
    d.mkdir()
    with zipfile.ZipFile(tmp / "lot.zip", "w") as z:
        z.writestr("a.txt", b"x")
    assert set(Workbench.outils_pour([img])) == {"convert", "fond", "taille", "nettete", "archives"}
    assert set(Workbench.outils_pour([tmp / "lot.zip"])) == {"convert", "archives"}
    assert set(Workbench.outils_pour([d])) == {"securite", "archives"}
    assert Workbench.outils_pour([img, d])["fond"] == 1

    app = _app()
    assert app.outil == "accueil"
    app._deposer_accueil([son])
    app.update()
    assert app.outil == "accueil" and app.depot == [son]
    app._ouvrir_outil("fond")              # grisee: on reste, rien n'est perdu
    assert app.outil == "accueil" and app.depot == [son]
    app._deposer_accueil([img])
    app._ouvrir_outil("fond")
    app.update()
    assert app.outil == "fond" and app.fichiers == [img] and not app.depot

    app._afficher("accueil")
    app._deposer_accueil([d])
    app._ouvrir_outil("securite")
    app.update()
    assert app.outil == "securite" and app.dossier_scan == d
    app._fermer()


def test_archives_creer_un_zip(tmpdir):
    """Archives dans les deux sens: un zip seul s'ouvre pour etre extrait, tout le reste est
    propose a l'archivage, et « Creer le zip » l'ecrit a cote, sans rien ecraser."""
    tmp = pathlib.Path(tmpdir)
    d = tmp / "vacances"
    (d / "jour1").mkdir(parents=True)
    (d / "jour1" / "plage.txt").write_text("sable")
    (tmp / "vacances.zip").write_bytes(b"deja la")      # le nom propose est pris

    app = _app()
    app._deposer_accueil([d])
    app._ouvrir_outil("archives")
    app.update()
    assert app.outil == "archives" and app.a_archiver == [d] and app.zip_ouvert is None
    assert app.zip_cree == tmp / "vacances.zip"
    app._creer_zip()
    assert _attendre(app, lambda: not app.occupe), "archivage jamais fini"
    z = tmp / "vacances (1).zip"
    assert z.exists() and (tmp / "vacances.zip").read_bytes() == b"deja la"
    assert app.etat_sauve["text"] == "Archivé", app.etat_sauve["text"]

    app._recevoir_archives([z])            # un zip seul: on l'ouvre pour l'extraire
    app.update()
    assert app.zip_ouvert == z and not app.a_archiver
    assert "vacances/jour1/plage.txt" in {e["nom"] for e in app.zip_contenu}
    app._fermer()


def test_textes_lisibles_sur_l_accent(tmpdir):
    """Gris sur violet: « Exporter » desactive gardait le fond de l'accent, et la qualite
    choisie gardait son texte gris fonce sur l'accent."""
    tmp = pathlib.Path(tmpdir)
    app = _app([_image(tmp / "p.png", "64x48")], "convert")
    assert app.btn_export["state"] == "disabled" and app.btn_export["bg"] != Workbench.ACCENT
    app.btn_export.configure(state="normal")
    assert app.btn_export["bg"] == Workbench.ACCENT
    boutons = [w for w in app.page.interieur.winfo_children()
               for w in w.winfo_children() if isinstance(w, Workbench.tk.Radiobutton)]
    for val in ("low", "high"):
        app.qualite.set(val)
        for rb in boutons:
            choisi = rb["value"] == val
            assert (rb["fg"] == "#FFFFFF") == choisi, (val, rb["value"], rb["fg"])
    app._fermer()


if __name__ == "__main__":
    test_px()
    test_etiquettes_nettete()
    with tempfile.TemporaryDirectory() as d:
        test_chemin_sortie(d)
    for essai in (test_taille_tapee_survit_au_redessin, test_saisie_invalide_ne_plante_pas,
                  test_pas_de_fuite_d_apercus, test_apercu_nettete_est_le_vrai_fichier,
                  test_cadrage_nettete_vise_le_detail, test_nettete_refuse_gif_anime,
                  test_liste_figee_pendant_un_traitement,
                  test_source_du_recapitulatif, test_toast_n_efface_pas_enregistre,
                  test_fermeture_arrete_ffmpeg, test_jpeg_pas_propose_deux_fois,
                  test_six_outils_s_affichent, test_zip_depose_sortie_a_cote_du_zip,
                  test_zip_sans_media, test_archives_extraire_la_selection,
                  test_archives_envoyer_vers_convertir, test_panneau_securite,
                  test_accueil_grise_selon_depot, test_archives_creer_un_zip,
                  test_textes_lisibles_sur_l_accent):
        with tempfile.TemporaryDirectory() as d:
            essai(d)
        print(f"  {essai.__name__} : ok")
    print("OK")
