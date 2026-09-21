"""Workbench - atelier images/vidéos/sons, archives zip et analyse de sécurité.
Tkinter + ffmpeg/Pillow/onnxruntime (convert.py), zipfile (archives.py), Defender (securite.py).

Mise en page :
barre du haut (logo = retour a l'accueil), panneau central en deux colonnes (aperçu / réglages).
"""
import glob
import hashlib
import os
import pathlib
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

_DEPS = ("PIL", "tkinterdnd2")


def _relancer_avec_le_bon_python():
    """Les dépendances ne sont pas forcément installées sur le Python qui lance ce fichier
    (VS Code peut prendre un 3.14 tout neuf) : on repart sur celui qui les a."""
    if getattr(sys, "frozen", False):
        return                      # .exe: tout est embarque, il n'y a pas de Python a chercher
    try:
        __import__("PIL")
        __import__("tkinterdnd2")
        return
    except ImportError:
        pass
    candidats = sorted(glob.glob(r"C:\Program Files\Python3*\python.exe") +
                       glob.glob(os.path.expandvars(
                           r"%LOCALAPPDATA%\Programs\Python\Python3*\python.exe")))
    moi = os.path.abspath(sys.executable)
    for exe in candidats:
        if os.path.abspath(exe) == moi:
            continue
        try:
            ok = subprocess.run([exe, "-c", "import " + ", ".join(_DEPS)],
                                capture_output=True, timeout=60).returncode == 0
        except Exception:
            ok = False
        if ok:
            print(f"Dépendances absentes de {sys.executable}\nRelance avec {exe}")
            # pas os.execv: sur Windows il recolle les arguments et casse sur « Program Files »
            sys.exit(subprocess.run([exe, os.path.abspath(__file__), *sys.argv[1:]]).returncode)
    sys.exit(f"Pillow et tkinterdnd2 sont introuvables. Installe-les :\n"
             f'  "{sys.executable}" -m pip install pillow tkinterdnd2 onnxruntime numpy')


_relancer_avec_le_bon_python()

if getattr(sys, "frozen", False):
    # .exe: ffmpeg/ffprobe sont livres dans dist\Workbench\ffmpeg (build.ps1). Mis en tete du
    # PATH: convert.py les appelle par leur nom, c'est ce ffmpeg-la qui doit repondre.
    _FF = pathlib.Path(sys.executable).parent / "ffmpeg"
    if _FF.is_dir():
        os.environ["PATH"] = f"{_FF}{os.pathsep}{os.environ.get('PATH', '')}"

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat, ImageTk   # noqa: E402
from tkinterdnd2 import DND_FILES, TkinterDnD             # noqa: E402

import archives                                           # noqa: E402
import convert                                            # noqa: E402
import securite                                           # noqa: E402

# Palette
BG, CARD, LINE = "#F2F2F3", "#FFFFFF", "#DADBDF"
SOFT, SOFT_LINE = "#F7F7F8", "#E6E7EA"
# accent violet + jaune (2026-09-21). Variante essayee: orange #FC6C26 + creme #FFF8DF, mais
# un texte blanc sur cet orange tombe a 2,9:1 de contraste (illisible sur les boutons).
ACCENT, ACCENT_DARK, ACCENT_PALE = "#4B3FCF", "#3A2FB0", "#ECEAFB"
ACCENT2 = "#FFCE6B"   # touche jaune : poignees du logo, rond de la zone de depot
TXT, TXT2, MUTED = "#121316", "#44474F", "#6E717A"
VERT, VERT_PALE = "#2F7D55", "#DCF0E6"
PECHE, PECHE_LINE, BRUN = "#F8E6D8", "#F0D6C2", "#8A5A2E"     # outil Archives (maquette)
ROUGE_PALE, ROUGE, ROUGE_TXT = "#FBE3E1", "#B03A32", "#8C2F28"  # outil Sécurité (maquette)
AMBRE_PALE, AMBRE = "#FBECC4", "#8A6410"
TIRET = "#C4C6CC"
DAMIER = "#E4E5E8"

OUTILS = [
    ("convert", "Convertir un fichier", "Changer le format", "#E7E3FA", "#5B4FC7", "⇄"),
    ("fond", "Enlever le fond", "Garder le sujet seul", "#E3E9FA", "#3F5BC7", "✂"),
    ("taille", "Redimensionner", "Agrandir ou réduire", "#DCF0E6", "#2F7D55", "⤡"),
    ("nettete", "Améliorer la netteté", "Rendre l'image plus nette", "#FBECC4", "#B8862B", "✦"),
    ("archives", "Archives", "Extraire ou créer un zip", PECHE, BRUN, "▤"),
    ("securite", "Analyse de sécurité", "Vérifier un dossier", ROUGE_PALE, ROUGE, "⛨"),
]
# page d'accueil : ce que chaque outil accepte, et la raison affichee quand sa tuile est grisee.
# Un GIF anime compte comme video (convert.media_kind), il n'allume donc que Convertir.
_est_zip = lambda p: p.suffix.lower() == ".zip" and p.is_file()
_est_image = lambda p: p.is_file() and convert.media_kind(p) == "image"
COMPAT = {
    "convert": (lambda p: _est_zip(p) or (p.is_file() and convert.media_kind(p) is not None),
                "Images, vidéos ou sons"),
    "fond": (_est_image, "Images seulement"),
    "taille": (_est_image, "Images seulement"),
    "nettete": (_est_image, "Images seulement"),
    "archives": (lambda p: p.exists(), ""),    # un zip s'extrait, tout le reste s'archive
    "securite": (lambda p: p.is_dir(), "Dossier seulement"),
}
# puces de la zone de dépôt : (texte, encre, fond, bordure)
PUCES = [("JPG · PNG · HEIC", MUTED, SOFT, SOFT_LINE), ("MP4 · MOV", MUTED, SOFT, SOFT_LINE),
         ("MP3 · WAV", MUTED, SOFT, SOFT_LINE), ("ZIP", BRUN, PECHE, PECHE_LINE)]
GRAVITES = {securite.MENACE: (ROUGE_PALE, ROUGE), securite.SUSPECT: (AMBRE_PALE, AMBRE),
            securite.A_VERIFIER: (SOFT_LINE, TXT2)}
ZIP_TEMP = pathlib.Path(tempfile.gettempdir()) / "workbench_zip"
MAX_COTE = 65500   # limite d'un JPEG; au-dela plus aucun visualiseur courant n'ouvre le fichier
QUALITES = [("Rapide", "low"), ("Normale", "normal"), ("Haute", "high")]
FONDS = [("Transparent", "transparent"), ("Blanc", "blanc"), ("Crème", "creme")]

REGLAGES = 280     # largeur de la colonne de reglages a droite
MARGES = 130       # paddings cumules entre les trois colonnes

_FAM = {}


# ---------------------------------------------------------------- petits outils
def police(size, weight="normal"):
    return (_FAM.get("ui", "Segoe UI"), size, weight)


def mono(size):
    return (_FAM.get("mono", "Consolas"), size)


def poids(n):
    for unite in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unite == "Go":
            return f"{n:.0f} {unite}" if unite == "o" else f"{n:.1f} {unite}".replace(".", ",")
        n /= 1024


def carte(parent, bg=CARD, bordure=LINE, **kw):
    return tk.Frame(parent, bg=bg, highlightbackground=bordure, highlightthickness=1, **kw)


class _Primaire(tk.Button):
    """Tk grise le texte d'un bouton desactive mais pas son fond: « Exporter » affichait du
    gris sur l'accent, illisible. Desactive, il passe sur un fond neutre."""
    def configure(self, cnf=None, **kw):
        if "state" in kw:
            kw.setdefault("bg", SOFT_LINE if kw["state"] == "disabled" else ACCENT)
        return super().configure(cnf, **kw)
    config = configure


def bouton(parent, texte, cmd, primaire=False, petit=False, **kw):
    return (_Primaire if primaire else tk.Button)(
        parent, text=texte, command=cmd, relief="flat", bd=0, cursor="hand2",
        bg=ACCENT if primaire else SOFT, fg="#FFFFFF" if primaire else TXT,
        activebackground=ACCENT_DARK if primaire else SOFT_LINE,
        activeforeground="#FFFFFF" if primaire else TXT,
        disabledforeground=MUTED, highlightthickness=0 if primaire else 1,
        highlightbackground=LINE,
        font=police(10 if petit else 11, "bold" if primaire else "normal"),
        padx=12 if petit else 18, pady=6 if petit else 10, **kw)


def label(parent, texte, size=11, couleur=TXT, weight="normal", bg=CARD, **kw):
    return tk.Label(parent, text=texte, font=police(size, weight), fg=couleur, bg=bg, **kw)


def boite_apercu(fenetre, larg=560, haut=360):
    """Place disponible pour un apercu, une fois la sidebar et la colonne de reglages servies."""
    dispo = (fenetre.winfo_width() or 1280) - REGLAGES - MARGES
    return max(240, min(larg, dispo)), haut


def rond_rect(cv, x1, y1, x2, y2, r=14, **kw):
    """Rectangle a coins arrondis sur un Canvas (Tk ne sait pas arrondir un Frame)."""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def outils_pour(chemins):
    """{outil: nombre d'elements deposes qu'il accepte}, sans les outils a zero."""
    compte = {cle: sum(1 for p in chemins if ok(p)) for cle, (ok, _) in COMPAT.items()}
    return {cle: n for cle, n in compte.items() if n}


def image_logo(cote):
    """Le logo : un B fait de deux tiroirs d'etabli. Meme dessin que logo.svg (grille 64 x 64),
    a garder synchronises. Dessine 4x plus grand puis reduit, pour des bords lisses: sert a la
    barre du haut, a l'icone de la fenetre et au .ico du .exe (build.ps1)."""
    k = cote * 4 / 64
    im = Image.new("RGBA", (cote * 4, cote * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = lambda *v: [a * k for a in v]
    d.rounded_rectangle(s(0, 0, 64, 64), radius=16 * k, fill=ACCENT)
    for haut, bas, fin in ((14, 31, 33.5), (33, 50, 37.5)):    # tiroir du haut, du bas
        d.rectangle(s(18, haut, fin, bas), fill="#FFFFFF")
        d.ellipse(s(fin - 8.5, haut, fin + 8.5, bas), fill="#FFFFFF")
        m = (haut + bas) / 2                                        # poignee
        d.rounded_rectangle(s(fin - 10.5, m - 1.5, fin - 1.5, m + 1.5), radius=1.5 * k, fill=ACCENT2)
    return im.resize((cote, cote), Image.LANCZOS)


_ORIGINE_ZIP = {}   # fichier extrait d'un zip déposé -> ce zip


def dossier_sortie(src):
    """Où écrire le résultat d'un fichier : à côté de lui, sauf s'il sort d'un zip déposé.
    Il vit alors dans %TEMP%\\workbench_zip, effacé à la fermeture : le résultat y aurait
    disparu avec lui. On l'écrit dans « <dossier du zip>\\<nom du zip>_workbench »."""
    z = _ORIGINE_ZIP.get(pathlib.Path(src))
    return z.parent / f"{z.stem}_workbench" if z else pathlib.Path(src).parent


def dossier_extraction(zip_):
    """%TEMP%\\workbench_zip\\<nom>_<court hash> : deux zips du même nom ne se mélangent pas."""
    zip_ = pathlib.Path(zip_)
    try:
        st = zip_.stat()
        cle = f"{zip_.resolve()}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        cle = str(zip_)
    return ZIP_TEMP / f"{zip_.stem[:40]}_{hashlib.sha1(cle.encode()).hexdigest()[:8]}"


def chemin_sortie(src, ext, suffixe=""):
    """Ecrit a cote du fichier d'origine (a cote du zip s'il en sort), sans jamais l'ecraser."""
    dossier, tige, ext = dossier_sortie(src), src.stem + suffixe, ext.lower()   # « photo net.JPG » -> .jpg
    p = dossier / f"{tige}.{ext}"
    i = 1
    while p.exists():
        p = dossier / f"{tige} ({i}).{ext}"
        i += 1
    return p


def point_net(im, grille=9, taille=96):
    """Centre de la zone la plus contrastée de l'image, en fractions (0-1).

    L'aperçu de netteté montre un morceau à 100 % : cadré au centre, il pouvait tomber sur un
    ciel ou un mur uni, où l'accentuation ne se voit pas du tout — et l'outil avait l'air de
    ne rien faire. On vise donc là où il y a du détail à juger."""
    try:
        bords = im.convert("L").resize((taille, taille),
                                       Image.BILINEAR).filter(ImageFilter.FIND_EDGES)
    except Exception:
        return 0.5, 0.5
    fen = max(2, taille // 3)
    libre = taille - fen
    meilleur = (-1.0, 0.5, 0.5)
    for i in range(grille):
        x = libre * i // max(1, grille - 1)
        for j in range(grille):
            y = libre * j // max(1, grille - 1)
            s = ImageStat.Stat(bords.crop((x, y, x + fen, y + fen))).sum[0]
            if s > meilleur[0]:
                meilleur = (s, (x + fen / 2) / taille, (y + fen / 2) / taille)
    return meilleur[1], meilleur[2]


def _ETIQUETTES_NETTETE(scale):
    """A x1 on compare l'original et sa version accentuee; au-dela, les deux volets sont
    agrandis et seule l'accentuation les distingue: le dire, sinon l'etiquette ment."""
    return ("avant", "après") if scale == 1 else (f"× {scale} seul", f"× {scale} + netteté")


def _px(texte, defaut):
    """Un nombre de pixels lu dans un champ de saisie. Tolere « 1 234 », « 1234,0 », le vide."""
    try:
        brut = str(texte).strip().replace(",", ".").replace(" ", "")
        return max(1, min(MAX_COTE, int(round(float(brut)))))
    except (TypeError, ValueError):
        return max(1, int(defaut))


def montrer_dans_explorateur(path):
    subprocess.run(["explorer", "/select,", str(path)])  # explorer renvoie 1 meme quand ca marche


def ouvrir_dossier(path):
    subprocess.run(["explorer", str(path)])


def damier(w, h, case=11):
    im = Image.new("RGB", (w, h), "#FFFFFF")
    d = ImageDraw.Draw(im)
    for y in range(0, h, case):
        for x in range(0, w, case):
            if (x // case + y // case) % 2:
                d.rectangle([x, y, x + case - 1, y + case - 1], fill=DAMIER)
    return im


def ouvrir_image(chemin, boite=None):
    """Ouvre une image (ou la 1re frame d'une video) et la rentre dans une boite l x h."""
    chemin = pathlib.Path(chemin)
    if convert.media_kind(chemin) == "video":
        # dans le dossier temporaire, pas a cote de la source: celle-ci peut etre en lecture
        # seule (cle USB, partage reseau), et un aperçu rate y laissait un .apercu_*.png
        tmp = pathlib.Path(tempfile.gettempdir()) / f"workbench_apercu_{os.getpid()}.png"
        try:
            convert.thumbnail(chemin, tmp, max_px=900)
            im = Image.open(tmp).copy()
        except Exception:
            return None
        finally:
            tmp.unlink(missing_ok=True)
    elif convert.media_kind(chemin) == "image":
        im = Image.open(chemin)
        if boite:
            im.draft("RGB", boite)   # jpeg: decode au 1/2, 1/4 ou 1/8 -> une image de 500 Mpx passe
        im = ImageOps.exif_transpose(im)
    else:
        return None
    if boite:
        im = im.copy()
        im.thumbnail(boite, Image.LANCZOS)
    return im


def sur_damier(im):
    """Aplati une image RGBA sur un damier, pour montrer la transparence."""
    if im.mode != "RGBA":
        return im.convert("RGB")
    fond = damier(*im.size)
    fond.paste(im, (0, 0), im)
    return fond


# ---------------------------------------------------------------- zone défilante
class Defilant(tk.Frame):
    """Panneau qui defile: sans ca, un bouton sous un grand apercu devient inatteignable."""

    def __init__(self, parent, bg=CARD):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        barre = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.interieur = tk.Frame(self.canvas, bg=bg)
        self._fen = self.canvas.create_window((0, 0), window=self.interieur, anchor="nw")
        self.interieur.bind("<Configure>",
                            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._fen, width=e.width))
        self.canvas.configure(yscrollcommand=barre.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        barre.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", self._molette)

    def voir(self, widget):
        """Fait defiler le panneau pour que ce widget soit entierement visible."""
        self.update_idletasks()
        total = max(1, self.interieur.winfo_height())
        haut = widget.winfo_rooty() - self.interieur.winfo_rooty()
        bas = haut + widget.winfo_height()
        vue = self.canvas.winfo_height()
        if bas > self.canvas.canvasy(0) + vue:
            self.canvas.yview_moveto(max(0.0, (bas - vue) / total))

    def _molette(self, e):
        if isinstance(e.widget, ttk.Treeview):
            return        # la liste d'une archive defile seule: sinon page et liste bougeaient ensemble
        try:
            self.canvas.yview_scroll(int(-e.delta / 120), "units")
        except tk.TclError:
            pass  # panneau deja detruit


# ---------------------------------------------------------------- comparateur
class Comparateur(tk.Frame):
    """Avant / apres avec une poignee qu'on fait glisser, comme dans la maquette."""

    def __init__(self, parent, avant, apres, larg=560, haut=360, etiquettes=("avant", "après")):
        super().__init__(parent, bg=CARD)
        self.larg, self.haut = larg, haut
        self.avant = sur_damier(self._caler(avant, larg, haut))
        self.apres = sur_damier(self._caler(apres, larg, haut))
        self.l, self.h = self.avant.size
        self.etiquettes = etiquettes
        self.x = self.l // 2
        self.cv = tk.Canvas(self, width=self.l, height=self.h, bg=CARD,
                            highlightthickness=0, cursor="sb_h_double_arrow")
        self.cv.pack()
        self.cv.bind("<Button-1>", self._glisser)
        self.cv.bind("<B1-Motion>", self._glisser)
        self._dessiner()

    def maj(self, avant, apres, etiquettes=None):
        """Change les deux images en place (curseur de reglage: on redessine, on ne reconstruit pas)."""
        self.avant = sur_damier(self._caler(avant, self.larg, self.haut))
        self.apres = sur_damier(self._caler(apres, self.larg, self.haut))
        self.l, self.h = self.avant.size
        if etiquettes:
            self.etiquettes = etiquettes
        self.x = min(self.x, self.l)
        self.cv.configure(width=self.l, height=self.h)
        self._dessiner()

    @staticmethod
    def _caler(im, larg, haut):
        im = im.copy()
        im.thumbnail((larg, haut), Image.LANCZOS)
        return im

    def _glisser(self, e):
        self.x = max(0, min(self.l, e.x))
        self._dessiner()

    def _dessiner(self):
        compo = self.apres.copy()
        if self.x > 0:
            compo.paste(self.avant.crop((0, 0, self.x, self.h)), (0, 0))
        self._photo = ImageTk.PhotoImage(compo)
        self.cv.delete("all")
        self.cv.create_image(0, 0, image=self._photo, anchor="nw")
        self.cv.create_line(self.x, 0, self.x, self.h, fill="#FFFFFF", width=3)
        self.cv.create_oval(self.x - 15, self.h // 2 - 15, self.x + 15, self.h // 2 + 15,
                            fill="#FFFFFF", outline=LINE)
        self.cv.create_text(self.x, self.h // 2, text="‹›", font=police(11, "bold"), fill=ACCENT)
        for i, (txt, ancre, px) in enumerate(((self.etiquettes[0], "nw", 12), (self.etiquettes[1], "ne", self.l - 12))):
            self.cv.create_rectangle(px - (0 if i == 0 else 8 * len(txt) + 14), 10,
                                     px + (8 * len(txt) + 14 if i == 0 else 0), 32,
                                     fill="#FFFFFF", outline="")
            self.cv.create_text(px + (8 if i == 0 else -8), 21, text=txt, anchor="w" if i == 0 else "e",
                                font=mono(9), fill=TXT2)


# ---------------------------------------------------------------- application
class Studio(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title("Workbench")
        self.minsize(1000, 640)
        larg = min(1280, self.winfo_screenwidth() - 80)      # jamais plus grande que l'ecran
        haut = min(820, self.winfo_screenheight() - 120)
        self.geometry(f"{larg}x{haut}")
        self.configure(bg=BG)

        familles = set(tkfont.families())
        _FAM["ui"] = "Outfit" if "Outfit" in familles else "Segoe UI"
        _FAM["mono"] = "JetBrains Mono" if "JetBrains Mono" in familles else "Consolas"

        self.fichiers = []        # liste de pathlib.Path
        self.infos = {}           # probe du fichier affiche
        self.resultats = []       # fichiers produits par la derniere action
        self.sources = []         # fichiers reellement traites par la derniere action
        self.outil = "accueil"
        self.depot = []           # accueil : ce qui vient d'etre depose, pas encore confie a un outil
        self.images = []          # refs PhotoImage, sinon Tk les collecte
        self.msgs = queue.Queue()
        self.occupe = False
        self.chrono = False
        self.frac = 0.0
        self.angle = 90
        self.btn_action = None   # remis a zero a chaque panneau: le widget est detruit
        self.gen = 0             # numero du panneau: un message d'un panneau detruit est jete
        self._fin = None         # fin de traitement propre a un outil (archives, securite)
        self.extractions = set() # dossiers temporaires des zips deposes, effaces a la fermeture
        self.zips_en_cours = 0
        self.zip_ouvert = None   # outil Archives
        self.zip_contenu = []
        self.zip_dest = None
        self.a_archiver = []     # outil Archives, sens « creer un zip »
        self.zip_cree = None
        self.dossier_scan = None  # outil Securite
        self.resultat_scan = None
        self._styler_tableaux()

        self._barre_haut()
        corps = tk.Frame(self, bg=BG)
        corps.pack(fill="both", expand=True)
        self.centre = tk.Frame(corps, bg=BG)
        self.centre.pack(side="left", fill="both", expand=True, padx=20, pady=16)

        self.drop_target_register(DND_FILES)   # glisser-deposer sur toute la fenetre
        self.dnd_bind("<<Drop>>", self._deposer)

        self._afficher(self.outil)
        self._pompe = None
        self._pomper()
        self.protocol("WM_DELETE_WINDOW", self._fermer)

        # le detourage attendait l'ouverture de son panneau pour charger son modele: on le fait
        # des maintenant, pendant que l'utilisateur regarde la fenetre s'ouvrir
        threading.Thread(target=self._precharger_en_fond, daemon=True).start()
        self._centrer()

    def _centrer(self):
        """Au milieu de l'ecran, bordures comprises: winfo_width() ignore la barre de titre,
        centrer dessus laisserait la fenetre legerement trop basse."""
        self.update_idletasks()
        bord = self.winfo_rootx() - self.winfo_x()          # epaisseur de bordure
        titre = self.winfo_rooty() - self.winfo_y()         # barre de titre + bordure haute
        larg = self.winfo_width() + 2 * bord
        haut = self.winfo_height() + titre + bord
        self.geometry(f"+{max(0, (self.winfo_screenwidth() - larg) // 2)}"
                      f"+{max(0, (self.winfo_screenheight() - haut) // 2)}")

    @staticmethod
    def _precharger_en_fond():
        # seulement si le fichier est deja la: sinon on lancait 176 Mo de telechargement au
        # demarrage, en silence, sans que l'utilisateur ait ouvert le detourage
        if not convert.modele_pret():
            return
        try:
            convert.precharger_modele()
        except Exception:
            pass          # pas grave: le panneau retentera et affichera l'erreur, lui

    # ---------------- chrome
    def _barre_haut(self):
        barre = tk.Frame(self, bg=CARD, height=66)
        barre.pack(fill="x")
        barre.pack_propagate(False)

        self.logo = ImageTk.PhotoImage(image_logo(34))
        self.iconphoto(True, ImageTk.PhotoImage(image_logo(64)))   # icone de la fenetre
        tete = tk.Frame(barre, bg=CARD, cursor="hand2")
        tete.pack(side="left", padx=(22, 0))
        l1 = tk.Label(tete, image=self.logo, bg=CARD)
        l1.pack(side="left", padx=(0, 10))
        l2 = label(tete, "Workbench", 14, TXT, "bold")
        l2.pack(side="left")
        for w in (tete, l1, l2):                      # le logo ramene a l'accueil
            w.bind("<Button-1>", lambda _e: self._afficher("accueil"))
        # retour a la grille, cache sur l'accueil (_afficher): c'est la seule navigation
        self.btn_accueil = bouton(barre, "‹  Tous les outils", lambda: self._afficher("accueil"),
                                  petit=True)

        self.chip = tk.Frame(barre, bg=SOFT, highlightbackground=SOFT_LINE, highlightthickness=1)
        self.chip.pack(side="left", padx=22, pady=14)
        self.chip_icone = tk.Frame(self.chip, bg="#E7E3FA", width=24, height=24)
        self.chip_icone.pack(side="left", padx=(9, 10), pady=6)
        self.chip_icone.pack_propagate(False)
        self.chip_nom = label(self.chip, "Aucun fichier", 11, TXT, "normal", SOFT)
        self.chip_nom.pack(side="left")
        self.chip_dim = tk.Label(self.chip, text="", font=mono(9), fg=MUTED, bg=SOFT)
        self.chip_dim.pack(side="left", padx=(10, 14))

        droite = tk.Frame(barre, bg=CARD)
        droite.pack(side="right", padx=22)
        self.etat_sauve = label(droite, "", 10, MUTED, "normal", CARD)
        self.etat_sauve.pack(side="left", padx=(0, 14))
        self.btn_export = bouton(droite, "Exporter", self._exporter, primaire=True, petit=True)
        self.btn_export.configure(state="disabled")
        self.btn_export.pack(side="left")
        tk.Frame(self, bg=SOFT_LINE, height=1).pack(fill="x")

    # ---------------- fichiers
    def _styler_tableaux(self):
        """Les listes (ttk.Treeview) aux couleurs de l'appli : fond blanc, pas de cadre 3D,
        sélection bleu pâle comme dans la maquette."""
        s = ttk.Style(self)
        s.configure("Workbench.Treeview", background=CARD, fieldbackground=CARD, foreground=TXT,
                    rowheight=30, font=police(10), borderwidth=0, relief="flat")
        s.map("Workbench.Treeview", background=[("selected", ACCENT_PALE)],
              foreground=[("selected", TXT)])
        s.layout("Workbench.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    def _deposer(self, e):
        chemins = [pathlib.Path(p) for p in self.tk.splitlist(e.data)]
        if self.outil == "accueil":
            self._deposer_accueil(chemins)
            return
        if self.outil == "securite":      # on y depose un DOSSIER a analyser
            dossiers = [p for p in chemins if p.is_dir()]
            if dossiers:
                self._choisir_dossier_scan(dossiers[0])
            else:
                self._toast("Glissez un dossier : l'analyse porte sur un dossier entier.")
            return
        if self.outil == "archives":      # un zip s'ouvre, le reste s'archive
            self._recevoir_archives(chemins)
            return
        self._ajouter(chemins)

    def _choisir(self):
        motifs = " ".join(f"*.{e}" for e in sorted(archives.EXT_MEDIAS))
        chemins = filedialog.askopenfilenames(
            title="Choisir des images, des vidéos, des sons ou un zip",
            filetypes=[("Médias et archives zip", f"{motifs} *.zip"), ("Archives zip", "*.zip"),
                       ("Tous les fichiers", "*.*")])
        self._ajouter([pathlib.Path(p) for p in chemins])

    def _occupe(self) -> bool:
        """La liste de fichiers ne doit pas bouger pendant un traitement: _afficher() ne peut
        pas reconstruire le panneau, et les reglages (self.cibles) restaient alors sur l'ancienne
        liste — le clic suivant sur « Convertir » levait un KeyError, muet dans un .exe."""
        if self.occupe:
            self._toast("Traitement en cours — patientez ou cliquez sur Annuler.")
        return self.occupe

    def _ajouter(self, chemins, vers=None):
        """Ajoute des medias a la liste. Un .zip n'est pas refuse : ses medias sont extraits
        dans %TEMP% (en arriere-plan) puis ajoutes a leur tour."""
        if self._occupe():
            return
        zips = [p for p in chemins if p.suffix.lower() == ".zip" and p.is_file()]
        for z in zips:
            self._ouvrir_zip_medias(z, vers)
        chemins = [p for p in chemins if p not in zips]
        if zips and not chemins:
            return                        # _zip_pret() rappellera _ajouter avec les medias
        refuses = [p.name for p in chemins if convert.media_kind(p) is None]
        gardes = [p for p in chemins if convert.media_kind(p) is not None and p not in self.fichiers]
        self.fichiers.extend(gardes)
        self.resultats = []
        if self.fichiers:
            self.infos = convert.probe(self.fichiers[0])
        self._maj_chip()
        if refuses:
            self._toast(f"Ignoré(s) : {', '.join(refuses[:3])}")
        self._afficher(vers or ("convert" if self.outil == "accueil" else self.outil))

    # ---------------- accueil : deposer d'abord, choisir l'outil ensuite
    def _deposer_accueil(self, chemins):
        if self._occupe():
            return
        compat = outils_pour(chemins)
        if not compat:
            self._toast("Aucun outil ne s'applique à ce que vous avez déposé.")
            return
        self.depot = chemins
        self._afficher("accueil")

    def _choisir_accueil(self):
        motifs = " ".join(f"*.{e}" for e in sorted(archives.EXT_MEDIAS))
        chemins = filedialog.askopenfilenames(
            title="Choisir des images, des vidéos, des sons ou un zip",
            filetypes=[("Médias et archives zip", f"{motifs} *.zip"), ("Tous les fichiers", "*.*")])
        if chemins:
            self._deposer_accueil([pathlib.Path(p) for p in chemins])

    def _ouvrir_outil(self, cle):
        """Clic sur une tuile: l'outil recoit ce qui, dans le depot, le concerne."""
        ok, raison = COMPAT[cle]
        lot = [p for p in self.depot if ok(p)]
        if self.depot and not lot:
            self._toast(f"{raison} — déposez autre chose ou effacez le dépôt.")
            return
        self.depot = []
        if not lot:
            self._afficher(cle)            # rien de depose: l'outil s'ouvre avec sa zone de depot
        elif cle == "securite":
            self._choisir_dossier_scan(lot[0])
        elif cle == "archives":
            self._recevoir_archives(lot)
        else:
            self._ajouter(lot, vers=cle)

    def _vider_depot(self):
        self.depot = []
        self._afficher("accueil")

    # ---------------- zip deposes dans n'importe quel outil
    def _ouvrir_zip_medias(self, z, vers=None):
        """Extrait les medias du zip dans %TEMP%\\workbench_zip\\<nom>_<hash>, dans un thread:
        un zip de 2 Go figeait sinon la fenetre. Le resultat revient par self.msgs."""
        dest = dossier_extraction(z)
        deja = dest in self.extractions
        self.zips_en_cours += 1
        self._toast(f"Ouverture de « {z.name} »...")

        def travail():
            try:
                if deja:          # meme zip redepose: ses medias sont deja la, on ne double pas
                    rapport = archives.Rapport(dest=dest, extraits=sorted(
                        p for p in dest.rglob("*") if p.is_file()))
                else:
                    if dest.exists():    # reste d'une session interrompue: c'est notre dossier
                        shutil.rmtree(dest, ignore_errors=True)
                    rapport = archives.extraire_medias(z, dest)
                self.msgs.put(("zip", (z, dest, rapport, None, vers), None, None))
            except Exception as e:     # ArchiveError (corrompu, plafond) ou disque plein
                self.msgs.put(("zip", (z, dest, None, str(e), vers), None, None))

        threading.Thread(target=travail, daemon=True).start()

    def _zip_pret(self, charge):
        z, dest, rapport, erreur, vers = charge
        if self.occupe:                   # un traitement tourne: la liste est figee, on repasse
            self.after(400, lambda: self.msgs.put(("zip", charge, None, None)))
            return
        self.zips_en_cours -= 1
        self.extractions.add(dest)
        if erreur:
            self._toast(erreur)
            return
        medias = [p for p in rapport.extraits if convert.media_kind(p)]
        for p in medias:
            _ORIGINE_ZIP[p] = z
        if not medias:
            detail = f" ({len(rapport.chiffres)} fichier(s) chiffré(s) ignoré(s))" if rapport.chiffres else ""
            self._toast(f"Aucune image, vidéo ou son dans « {z.name} »{detail}.")
            return
        self._ajouter(medias, vers=vers)
        self._toast(f"« {z.name} » : {len(medias)} média{'s' if len(medias) > 1 else ''} ajouté"
                    f"{'s' if len(medias) > 1 else ''} · résultats dans « {z.stem}_workbench »")

    def _retirer(self, chemin):
        if self._occupe():
            return
        self.fichiers = [f for f in self.fichiers if f != chemin]
        self.infos = convert.probe(self.fichiers[0]) if self.fichiers else {}
        self._maj_chip()
        self._afficher(self.outil)

    def _vider(self):
        if self._occupe():
            return
        self.fichiers, self.resultats, self.infos = [], [], {}
        self._maj_chip()
        self._afficher(self.outil)

    def _images_seules(self):
        return [f for f in self.fichiers if convert.media_kind(f) == "image"]

    def _pourquoi_pas_image(self) -> str:
        """Dire lequel des deux cas on est dans: « que sur des images » laissait croire qu'un
        GIF n'en etait pas une, alors que c'est son animation qui pose probleme."""
        if any(convert.anime(f) for f in self.fichiers):
            return ("Une image animée (GIF) ne peut pas passer ici : il n'en resterait "
                    "que la première image. Utilisez Convertir ou Redimensionner.")
        return "Cet outil ne fonctionne que sur des images."

    def _maj_chip(self):
        if not self.fichiers:
            self.chip_nom.configure(text="Aucun fichier")
            self.chip_dim.configure(text="")
            return
        f = self.fichiers[0]
        tint = {"image": "#E7E3FA", "video": "#DCF0E6", "audio": "#FBECC4"}
        self.chip_icone.configure(bg=tint.get(convert.media_kind(f), SOFT))
        suite = f"  +{len(self.fichiers) - 1}" if len(self.fichiers) > 1 else ""
        self.chip_nom.configure(text=f.name + suite)
        w, h, d = self.infos.get("width"), self.infos.get("height"), self.infos.get("duration")
        if w and h:
            self.chip_dim.configure(text=f"{w} × {h}")
        elif d:
            self.chip_dim.configure(text=f"{int(d // 60)}:{int(d % 60):02d}")
        else:
            self.chip_dim.configure(text=poids(f.stat().st_size))

    # ---------------- navigation
    def _afficher(self, outil):
        if self.occupe:
            if outil != self.outil:    # clic dans la barre laterale: dire pourquoi rien ne bouge
                self._toast("Traitement en cours — patientez ou cliquez sur Annuler.")
            return
        self.outil = outil
        self.gen += 1
        if outil == "accueil":
            self.btn_accueil.pack_forget()
        else:
            self.btn_accueil.pack(side="left", padx=(18, 0), before=self.chip)
        self._maj_chip()
        for w in self.centre.winfo_children():
            w.destroy()
        self.images.clear()
        self.btn_action = None
        for att in ("_redessin", "_attente_nettete"):   # travail differe du panneau qu'on quitte
            if getattr(self, att, None):
                self.after_cancel(getattr(self, att))
                setattr(self, att, None)
        cadre = carte(self.centre)
        cadre.pack(fill="both", expand=True)
        self.page = Defilant(cadre)
        self.page.pack(fill="both", expand=True)
        p = self.page.interieur
        {"accueil": self._p_accueil, "convert": self._p_convertir, "fond": self._p_fond,
         "taille": self._p_taille, "nettete": self._p_nettete,
         "archives": self._p_archives, "securite": self._p_securite}[outil](p)

    def _entete(self, p, titre, sous):
        label(p, titre, 21, TXT, "bold").pack(anchor="w", padx=30, pady=(26, 3))
        label(p, sous, 11, MUTED).pack(anchor="w", padx=30, pady=(0, 18))

    def _zone_depot(self, p, titre="Glissez vos fichiers ici",
                    sous="ou cliquez pour les choisir sur votre ordinateur", puces=PUCES, action=None):
        zone = tk.Canvas(p, bg=CARD, height=210, highlightthickness=0, cursor="hand2")
        zone.pack(fill="x", padx=30)
        # largeur de chaque puce d'apres son texte: « ZIP » n'a pas besoin de 116 px
        largeurs = [max(50, 7 * len(t) + 22) for t, *_ in puces]

        def dessiner(_e=None):
            zone.delete("all")
            w = zone.winfo_width() or 700
            zone.create_rectangle(2, 2, w - 3, 205, outline=TIRET, dash=(7, 5), width=2)
            zone.create_oval(w // 2 - 26, 34, w // 2 + 26, 86, fill=ACCENT2, outline="")
            zone.create_text(w // 2, 60, text="↑", font=police(20, "bold"), fill=ACCENT)
            zone.create_text(w // 2, 112, text=titre, font=police(15, "bold"), fill=TXT)
            zone.create_text(w // 2, 138, text=sous, font=police(10), fill=MUTED)
            x = w // 2 - (sum(largeurs) + 8 * (len(puces) - 1)) // 2
            for (t, encre, fond, bord), larg in zip(puces, largeurs):
                zone.create_rectangle(x, 165, x + larg, 189, outline=bord, fill=fond)
                zone.create_text(x + larg // 2, 177, text=t, font=mono(8), fill=encre)
                x += larg + 8

        zone.bind("<Configure>", dessiner)
        zone.bind("<Button-1>", lambda _e: (action or self._choisir)())

    def _barre_fichiers(self, p, seulement_images=False):
        """Bandeau 'N fichiers' + boutons ajouter / tout effacer."""
        lot = self._images_seules() if seulement_images else self.fichiers
        ligne = tk.Frame(p, bg=CARD)
        ligne.pack(fill="x", padx=30, pady=(0, 12))
        n = len(lot)
        mot = "image" if seulement_images else "fichier"
        label(ligne, f"{n} {mot}{'s' if n > 1 else ''} en attente", 11, TXT2, "bold").pack(side="left")
        if n > 1:
            label(ligne, "· aperçu du premier, l'action s'applique à tous", 9, MUTED).pack(side="left", padx=8)
        bouton(ligne, "Tout effacer", self._vider, petit=True).pack(side="right")
        bouton(ligne, "Ajouter", self._choisir, petit=True).pack(side="right", padx=6)
        return lot

    def _deux_colonnes(self, p):
        d = tk.Frame(p, bg=CARD)
        d.pack(fill="both", expand=True, padx=30, pady=(0, 24))
        droite = tk.Frame(d, bg=CARD)
        droite.pack(side="right", fill="y", padx=(20, 0))   # servie en premier: sans ses reglages
        gauche = tk.Frame(d, bg=CARD)                       # et sa progression, l'outil est mort
        gauche.pack(side="left", fill="both", expand=True)
        # pack_propagate(False) figeait la largeur mais rendait la hauteur du contenu invisible
        # pour la mise en page: la zone de progression finissait rognee, voire hors ecran.
        tk.Frame(droite, bg=CARD, width=280, height=1).pack()   # cale la largeur, rien de plus
        return gauche, droite

    def _apercu(self, parent, chemin, legende=None, boite=None):
        im = ouvrir_image(chemin, boite or boite_apercu(self, 560, 330))
        if im is None:
            cadre = carte(parent, bg=SOFT, bordure=SOFT_LINE)
            cadre.pack(fill="x")
            label(cadre, "Pas d'aperçu pour un fichier audio", 10, MUTED, bg=SOFT).pack(pady=40)
            return None
        photo = ImageTk.PhotoImage(sur_damier(im))
        self.images.append(photo)
        cadre = carte(parent, bg=SOFT, bordure=SOFT_LINE)
        cadre.pack(fill="x")
        tk.Label(cadre, image=photo, bg=SOFT, bd=0).pack(padx=12, pady=12)
        if legende:
            label(cadre, legende, 9, MUTED, bg=SOFT).pack(pady=(0, 10))
        return im

    # ---------------- panneau : accueil
    def _p_accueil(self, p):
        self._entete(p, "Que voulez-vous faire ?",
                     "Déposez un fichier ou un dossier : seuls les outils qui s'y appliquent "
                     "restent allumés. Ou choisissez directement un outil.")
        compat = outils_pour(self.depot) if self.depot else None
        if self.depot:
            ligne = carte(p, bg=SOFT, bordure=SOFT_LINE)
            ligne.pack(fill="x", padx=30, pady=(0, 18))
            n = len(self.depot)
            suite = f"  +{n - 1}" if n > 1 else ""
            label(ligne, f"Déposé : {self.depot[0].name}{suite}", 11, TXT, "bold", SOFT
                  ).pack(side="left", padx=14, pady=10)
            label(ligne, "· cliquez sur un outil allumé", 10, MUTED, bg=SOFT).pack(side="left")
            bouton(ligne, "Effacer", self._vider_depot, petit=True).pack(side="right", padx=10)
        else:
            self._zone_depot(p, "Glissez un fichier ou un dossier ici",
                             "ou cliquez pour choisir des fichiers", action=self._choisir_accueil)
            tk.Frame(p, bg=CARD, height=18).pack()

        grille = tk.Frame(p, bg=CARD)
        grille.pack(fill="x", padx=30, pady=(0, 26))
        for col in range(3):
            grille.columnconfigure(col, weight=1, uniform="tuile")
        n = len(self.depot)
        for i, (cle, titre, sous, tint, encre, glyphe) in enumerate(OUTILS):
            eteint = compat is not None and cle not in compat
            fond = SOFT if eteint else CARD
            if eteint:
                sous = COMPAT[cle][1]
            elif compat and compat[cle] < n:        # depot melange: dire sur combien il agira
                sous = f"{compat[cle]}/{n} éléments"
            t = carte(grille, bg=fond, bordure=SOFT_LINE if eteint else LINE,
                      cursor="arrow" if eteint else "hand2")
            t.grid(row=i // 3, column=i % 3, sticky="nsew", padx=6, pady=6)
            ico = self._icone(t, glyphe, SOFT_LINE if eteint else tint,
                              TIRET if eteint else encre, cote=40, bg=fond)
            ico.pack(anchor="w", padx=16, pady=(16, 10))
            t1 = label(t, titre, 12, MUTED if eteint else TXT, "bold", fond)
            t1.pack(anchor="w", padx=16)
            t2 = label(t, sous, 9, MUTED, bg=fond)
            t2.pack(anchor="w", padx=16, pady=(2, 16))
            for w in (t, ico, t1, t2):   # une tuile grisee repond quand meme: _ouvrir_outil dit pourquoi
                w.bind("<Button-1>", lambda _e, k=cle: self._ouvrir_outil(k))
        # ne promettre que ce que l'appli fait: pas d'annulation apres coup, l'original jamais touche
        label(p, "Vos fichiers d'origine ne sont jamais modifiés : chaque outil écrit une "
                 "nouvelle copie à côté.", 9, MUTED).pack(anchor="w", padx=36, pady=(0, 24))

    # ---------------- panneau : convertir
    def _p_convertir(self, p):
        self._entete(p, "Convertir un fichier",
                     "Déposez une image, une vidéo ou un son. On s'occupe du reste.")
        if not self.fichiers:
            self._zone_depot(p)
            return
        self._barre_fichiers(p)

        self.cibles = {}
        for f in self.fichiers:
            self._ligne_conversion(p, f)

        reglages = tk.Frame(p, bg=CARD)
        reglages.pack(fill="x", padx=30, pady=(16, 0))
        label(reglages, "Qualité", 11, TXT2, "bold").pack(side="left", padx=(0, 12))
        self.qualite = tk.StringVar(value="normal")
        choix = {}
        for texte, val in QUALITES:
            choix[val] = tk.Radiobutton(
                reglages, text=texte, value=val, variable=self.qualite, indicatoron=False,
                font=police(10), bd=0, relief="flat", cursor="hand2", padx=14,
                bg=SOFT, fg=TXT2, selectcolor=ACCENT, activebackground=SOFT_LINE,
                highlightthickness=1, highlightbackground=SOFT_LINE)
            choix[val].pack(side="left", padx=3, ipady=4)

        def colorer(*_):   # Tk garde le meme texte, choisi ou non: gris fonce sur l'accent, illisible
            for val, rb in choix.items():
                rb.configure(fg="#FFFFFF" if val == self.qualite.get() else TXT2)
        self.qualite.trace_add("write", colorer)
        colorer()

        self.btn_action = bouton(p, "Convertir maintenant  →", self._convertir, primaire=True)
        self.btn_action.pack(anchor="w", padx=30, pady=(20, 0))
        self._zone_progression(p)

    def _ligne_conversion(self, p, f):
        ligne = carte(p, bg=SOFT, bordure=SOFT_LINE)
        ligne.pack(fill="x", padx=30, pady=3)
        tint = {"image": "#E7E3FA", "video": "#DCF0E6", "audio": "#FBECC4"}
        pastille = tk.Frame(ligne, bg=tint.get(convert.media_kind(f), SOFT), width=30, height=30)
        pastille.pack(side="left", padx=12, pady=11)
        pastille.pack_propagate(False)
        bloc = tk.Frame(ligne, bg=SOFT)
        bloc.pack(side="left")
        label(bloc, f.name, 11, TXT, "bold", SOFT).pack(anchor="w")
        label(bloc, poids(f.stat().st_size), 9, MUTED, bg=SOFT).pack(anchor="w")

        tk.Button(ligne, text="✕", command=lambda: self._retirer(f), relief="flat", bd=0,
                  bg=SOFT, fg=MUTED, activebackground=SOFT_LINE, cursor="hand2",
                  font=police(10)).pack(side="right", padx=10)

        choix = tk.Frame(ligne, bg=SOFT)
        choix.pack(side="right", padx=10)
        # « jpeg » et « jpg » sont le meme format: deux entrees dans la liste proposaient un
        # reencodage qui ne change rien
        src_ext = f.suffix.lower().lstrip(".").replace("jpeg", "jpg")
        cibles = [e for e in convert.targets_for(f) if e not in ("jpeg", src_ext)]
        var = tk.StringVar(value=cibles[0])
        self.cibles[f] = var
        label(choix, "Transformer en", 9, MUTED, bg=SOFT).pack(anchor="w")
        menu = ttk.Combobox(choix, textvariable=var, values=cibles, state="readonly",
                            width=8, font=police(10))
        menu.pack()

    def _convertir(self):
        qualite = self.qualite.get()
        taches = []
        for f in self.fichiers:
            dst = chemin_sortie(f, self.cibles[f].get())
            taches.append((f.name, f, lambda cb, f=f, dst=dst:
                           convert.convert(f, dst, quality=qualite, progress=cb)))
        self._lancer(taches, "On convertit vos fichiers...")

    # ---------------- panneau : redimensionner
    def _p_taille(self, p):
        self._entete(p, "Redimensionner",
                     "Déplacez le curseur pour agrandir ou réduire. Les proportions restent justes.")
        if not self._images_seules() and not self.fichiers:
            self._zone_depot(p)
            return
        lot = self._barre_fichiers(p)
        base = lot[0]
        w0, h0 = self.infos.get("width"), self.infos.get("height")
        if not w0:
            label(p, "Ce fichier n'a pas d'image à redimensionner.", 11, MUTED).pack(anchor="w", padx=30)
            return

        gauche, droite = self._deux_colonnes(p)
        self.apercu_taille = ouvrir_image(base, (1600, 1600))
        self.cv_taille = tk.Canvas(gauche, height=340, bg=SOFT, highlightbackground=SOFT_LINE,
                                   highlightthickness=1)
        self.cv_taille.pack(fill="x")
        # redessin seul: <Configure> appelait _maj_taille(), qui recalculait la taille depuis
        # le curseur en % — un simple redimensionnement de fenetre effacait la valeur tapee
        # (1234 x 300 redevenait 1230 x 984)
        self.cv_taille.bind("<Configure>", lambda _e: self._redessiner_taille())

        reg = carte(droite, bg=SOFT, bordure=SOFT_LINE)
        reg.pack(fill="x")
        haut = tk.Frame(reg, bg=SOFT)
        haut.pack(fill="x", padx=14, pady=(12, 0))
        label(haut, "Taille", 11, TXT, "bold", SOFT).pack(side="left")
        self.lbl_echelle = label(haut, "100 %", 12, ACCENT, "bold", SOFT)
        self.lbl_echelle.pack(side="right")
        self.echelle = tk.IntVar(value=100)
        # au-dela de 65 500 px de cote, aucun JPEG ne peut etre ecrit: on ne le propose pas
        pct_max = max(110, min(400, int(MAX_COTE * 100 / max(w0, h0))))
        self.echelle_max = pct_max
        tk.Scale(reg, from_=10, to=pct_max, orient="horizontal", variable=self.echelle, showvalue=False,
                 bg=ACCENT, fg=TXT, troughcolor=SOFT_LINE, activebackground=ACCENT_DARK,
                 highlightthickness=0, bd=0, sliderrelief="flat",
                 command=self._maj_taille).pack(fill="x", padx=14)
        reperes = tk.Frame(reg, bg=SOFT)
        reperes.pack(fill="x", padx=14, pady=(0, 12))
        label(reperes, "plus petit", 8, MUTED, bg=SOFT).pack(side="left")
        label(reperes, f"plus grand ({pct_max} %)" if pct_max < 400 else "plus grand",
              8, MUTED, bg=SOFT).pack(side="right")

        champs = carte(droite, bg=SOFT, bordure=SOFT_LINE)
        champs.pack(fill="x", pady=10)
        grille = tk.Frame(champs, bg=SOFT)
        grille.pack(fill="x", padx=14, pady=12)
        self.var_w, self.var_h = tk.StringVar(value=str(w0)), tk.StringVar(value=str(h0))
        for texte, var, cote in (("Largeur", self.var_w, "w"), ("Hauteur", self.var_h, "h")):
            bloc = tk.Frame(grille, bg=SOFT)
            bloc.pack(side="left", padx=(0, 12))
            label(bloc, texte, 9, MUTED, bg=SOFT).pack(anchor="w")
            e = tk.Entry(bloc, textvariable=var, width=7, font=mono(11), bg=CARD, fg=TXT, relief="flat",
                         highlightthickness=1, highlightbackground=SOFT_LINE, justify="center")
            e.pack(ipady=5)
            for evt in ("<Return>", "<KP_Enter>", "<FocusOut>"):   # taper une taille doit compter
                e.bind(evt, lambda _e, c=cote: self._taille_saisie(c))
        self.ratio = tk.BooleanVar(value=True)
        self.ratio.trace_add("write", lambda *_: self._taille_saisie("w"))
        tk.Checkbutton(champs, text="Garder les proportions", variable=self.ratio, bg=SOFT, fg=TXT2,
                       font=police(10), activebackground=SOFT, selectcolor=CARD,
                       highlightthickness=0, cursor="hand2").pack(anchor="w", padx=14, pady=(0, 10))

        if len(lot) > 1:   # en lot, ce sont les % qui s'appliquent, pas les pixels tapes
            label(droite, f"Les {len(lot)} fichiers sont redimensionnés au même\n"
                          f"pourcentage, pas à la même taille en pixels.",
                  8, MUTED, justify="left").pack(anchor="w", pady=(8, 0))

        bouton(droite, "Taille d'origine", self._taille_origine, petit=True).pack(fill="x", pady=(8, 0))
        self.btn_action = bouton(droite, "Appliquer la nouvelle taille", self._redimensionner,
                                 primaire=True)
        self.btn_action.pack(fill="x", pady=(8, 0))
        self._zone_progression(droite, compact=True)
        self._maj_taille()

    def _taille_saisie(self, cote):
        """Valide ce qui est tape dans Largeur/Hauteur : nombre correct, proportions recalculees,
        curseur et apercu remis d'accord. Sans ca, taper une taille ne faisait rien du tout."""
        if not hasattr(self, "var_w") or getattr(self, "_saisie", False):
            return
        w0, h0 = self.infos.get("width"), self.infos.get("height")
        if not w0:
            return
        var = self.var_w if cote == "w" else self.var_h
        brut = var.get().strip().replace(",", ".").replace(" ", "")
        try:
            v = int(round(float(brut)))
        except ValueError:
            self._toast("Largeur et hauteur : un nombre de pixels.")
            self._maj_taille()
            return
        v = max(1, min(MAX_COTE, v))     # limite d'ecriture des formats image
        avant = (self.var_w.get(), self.var_h.get())
        if self.ratio.get():
            if cote == "w":
                w, h = v, max(1, round(h0 * v / w0))
            else:
                h, w = v, max(1, round(w0 * v / h0))
        else:
            # l'autre champ peut contenir n'importe quoi (l'utilisateur y tape aussi):
            # int() brut levait une ValueError non rattrapee, invisible dans un .exe
            w = v if cote == "w" else _px(self.var_w.get(), w0)
            h = v if cote == "h" else _px(self.var_h.get(), h0)
        pct = w * 100 / w0

        self._saisie = True             # on ecrit dans les champs et le curseur: pas de retour de flamme
        try:
            self.var_w.set(str(w))
            self.var_h.set(str(h))
            self.echelle.set(max(10, min(int(self.echelle_max), round(pct))))
            self.lbl_echelle.configure(text="taille d'origine" if round(pct) == 100
                                       else f"{round(pct)} %")
        finally:
            self._saisie = False
        self._differer_apercu(w, h, pct)
        if (str(w), str(h)) != avant:    # pas de message quand on quitte un champ sans rien changer
            self._toast(f"Taille réglée : {w} × {h} px")

    def _taille_origine(self):
        self.echelle.set(100)
        self._maj_taille()

    def _differer_apercu(self, w, h, pct):
        """Le curseur envoie une rafale d'evenements: un LANCZOS par evenement fait ramer."""
        if getattr(self, "_redessin", None):
            self.after_cancel(self._redessin)
        self._redessin = self.after(90, lambda: self._dessiner_apercu_taille(w, h, pct))

    def _redessiner_taille(self):
        """Redessine SANS toucher aux chiffres. C'est ce que <Configure> doit appeler: la
        taille affichee est celle des champs, pas celle que le curseur recalculerait."""
        w0 = self.infos.get("width")
        if not w0 or not hasattr(self, "var_w"):
            return
        w, h = _px(self.var_w.get(), w0), _px(self.var_h.get(), self.infos.get("height") or 1)
        self._differer_apercu(w, h, w * 100 / w0)

    def _maj_taille(self, _v=None):
        """Rafraichit chiffres + apercu depuis le CURSEUR. A n'appeler que quand le curseur
        bouge: c'est lui qui fait autorite ici, et il ecrase ce qui a ete tape."""
        if getattr(self, "_saisie", False):
            return                      # c'est nous qui ecrivons dans les champs, pas l'utilisateur
        w0, h0 = self.infos.get("width"), self.infos.get("height")
        if not w0 or not h0:
            return          # « Tout effacer » pendant qu'un evenement etait en attente
        pct = self.echelle.get()
        w, h = max(1, round(w0 * pct / 100)), max(1, round(h0 * pct / 100))
        self.var_w.set(str(w))
        self.var_h.set(str(h))
        self.lbl_echelle.configure(text="taille d'origine" if pct == 100 else f"{pct} %")
        self._differer_apercu(w, h, pct)

    def _dessiner_apercu_taille(self, w, h, pct):
        """Apercu honnete: la photo ENTIERE, jamais recadree, et le cadre pointille de sa taille
        d'origine ancre au meme coin. C'est le rapport entre les deux qui montre l'agrandissement."""
        cv = self.cv_taille
        if not cv.winfo_exists():
            return                      # panneau change entre la demande et le redessin
        cv.delete("all")
        im = self.apercu_taille
        if im is None:
            return
        larg_cv = cv.winfo_width()
        larg_cv = larg_cv if larg_cv > 100 else 600   # pas encore affiche: Tk renvoie 1
        boite_l, boite_h = larg_cv - 24, 280
        w0, h0 = self.infos.get("width"), self.infos.get("height")

        # une seule echelle pour les deux rectangles, choisie pour que le plus grand remplisse
        # le cadre: la photo reste lisible, et c'est le pointille qui retrecit quand on agrandit
        ech = min(boite_l / max(w, w0), boite_h / max(h, h0))
        vl, vh = max(1, round(w * ech)), max(1, round(h * ech))
        ol, oh = max(1, round(w0 * ech)), max(1, round(h0 * ech))
        # une seule image a l'ecran: on remplace la precedente au lieu de l'empiler. Empilees,
        # 100 pas de curseur retenaient 38 Mo qui n'etaient rendus qu'en changeant de panneau.
        photo = self._photo_taille = ImageTk.PhotoImage(sur_damier(im.resize((vl, vh), Image.LANCZOS)))
        # les deux tailles partagent leur coin haut-gauche: on compare deux rectangles
        x0 = larg_cv // 2 - max(vl, ol) // 2
        y0 = 10 + boite_h // 2 - max(vh, oh) // 2
        cv.create_image(x0, y0, image=photo, anchor="nw")

        if pct != 100:   # cadre pointille = la taille d'origine, avec son etiquette: sans elle
            for couleur, larg in (("#FFFFFF", 3), (ACCENT, 1)):   # personne ne devine ce que c'est
                cv.create_rectangle(x0, y0, x0 + ol, y0 + oh, outline=couleur, width=larg,
                                    dash=(6, 4))
            txt = "taille d'origine"
            ty = y0 + oh + 11 if oh + 24 < max(vh, oh) else y0 - 11   # dedans si la place manque
            cv.create_rectangle(x0 + 3, ty - 9, x0 + 11 * len(txt), ty + 9, fill="#FFFFFF", outline="")
            cv.create_text(x0 + 8, ty, text=txt, anchor="w", font=mono(9), fill=ACCENT)

        legende = f"{w} × {h} px"
        if pct != 100:
            legende += f"   ·   pointillés : taille d'origine, {w0} × {h0}"
        cv.create_text(larg_cv // 2, 318, text=legende, font=mono(10), fill=MUTED)

    def _redimensionner(self):
        w0 = self.infos.get("width") or 1
        w, h = _px(self.var_w.get(), w0), _px(self.var_h.get(), self.infos.get("height") or 1)
        garder, pct = self.ratio.get(), self.echelle.get()
        taches = []
        for f in self.fichiers:
            inf = convert.probe(f)
            if not inf.get("width"):
                continue
            fw = max(1, round(inf["width"] * pct / 100)) if len(self.fichiers) > 1 else w
            fh = max(1, round(inf["height"] * pct / 100)) if len(self.fichiers) > 1 else h
            dst = chemin_sortie(f, f.suffix.lstrip("."), f" {fw}px")
            taches.append((f.name, f, lambda cb, f=f, dst=dst, fw=fw, fh=fh:
                           convert.resize(f, dst, width=fw, height=fh, keep_ratio=garder,
                                          progress=cb)))
        if not taches:
            self._toast("Aucun fichier redimensionnable.")
            return
        self._lancer(taches, "On redimensionne vos fichiers...")

    # ---------------- panneau : netteté
    def _p_nettete(self, p):
        self._entete(p, "Améliorer la netteté",
                     "Une seule action. Votre image d'origine n'est jamais modifiée.")
        if not self.fichiers:
            self._zone_depot(p)
            return
        lot = self._barre_fichiers(p, seulement_images=True)
        if not lot:
            label(p, self._pourquoi_pas_image(), 11, MUTED).pack(anchor="w", padx=30)
            return

        gauche, droite = self._deux_colonnes(p)
        self.src_nettete = ouvrir_image(lot[0])
        self._centre_net = None       # nouvelle image, nouveau point a viser
        cadre = carte(gauche, bg=SOFT, bordure=SOFT_LINE)
        cadre.pack(fill="x")
        avant, apres = self._calculer_nettete(2, 60)
        if avant is None:
            label(cadre, "Pas d'aperçu pour ce fichier", 10, MUTED, bg=SOFT).pack(pady=40)
            self.comp_nettete = None
        else:
            self.comp_nettete = Comparateur(cadre, avant, apres, *boite_apercu(self),
                                            etiquettes=_ETIQUETTES_NETTETE(2))
            self.comp_nettete.pack(padx=12, pady=(12, 4))
            self.lbl_nettete = label(cadre, "", 9, MUTED, bg=SOFT)
            self.lbl_nettete.pack(pady=(0, 10))

        reg = carte(droite, bg=SOFT, bordure=SOFT_LINE)
        reg.pack(fill="x")
        label(reg, "Agrandissement", 10, TXT, "bold", SOFT).pack(anchor="w", padx=14, pady=(12, 6))
        self.agrandir = tk.IntVar(value=2)
        for texte, val in (("Netteté seule", 1), ("× 2", 2), ("× 4", 4)):
            tk.Radiobutton(reg, text=texte, value=val, variable=self.agrandir, bg=SOFT, fg=TXT2,
                           font=police(10), activebackground=SOFT, selectcolor=CARD,
                           highlightthickness=0, cursor="hand2").pack(anchor="w", padx=12)
        label(reg, "Force", 10, TXT, "bold", SOFT).pack(anchor="w", padx=14, pady=(10, 0))
        self.force = tk.IntVar(value=60)
        tk.Scale(reg, from_=10, to=150, orient="horizontal", variable=self.force, showvalue=False,
                 bg=ACCENT, fg=TXT, troughcolor=SOFT_LINE, activebackground=ACCENT_DARK,
                 highlightthickness=0, bd=0, sliderrelief="flat").pack(fill="x", padx=14, pady=(0, 12))

        for var in (self.agrandir, self.force):
            var.trace_add("write", lambda *_: self._demander_nettete())
        self._maj_nettete()

        self.btn_action = bouton(droite, "✦  Améliorer mon image", self._ameliorer, primaire=True)
        self.btn_action.pack(fill="x", pady=(10, 4))
        label(droite, "Quelques secondes · rien n'est envoyé sur internet",
              8, MUTED).pack()
        self._zone_progression(droite, compact=True)

    def _calculer_nettete(self, scale, force):
        """Aperçu à 100 % du FICHIER PRODUIT : on découpe dans l'image d'origine le morceau
        qui remplira exactement le cadre une fois agrandi × scale, puis on lui applique
        affiner() — la fonction qu'utilise enhance(). Ce sont donc de vrais pixels de sortie.

        Une vignette de l'image entière ne pouvait pas dire la vérité : le rayon de
        l'UnsharpMask se compte en pixels, l'appliquer à 440 px de large donnait un résultat
        sans rapport avec le fichier (écart RMS 5,6 — la taille de l'effet lui-même)."""
        im = self.src_nettete
        if im is None:
            return None, None
        x, y, cl, ch = self.cadre_nettete(scale)
        morceau = convert._en_rvb(im.crop((x, y, x + cl, y + ch)))
        # « avant » = le meme morceau agrandi sans nettete: les deux volets ont la meme taille
        # et la meme echelle, seul l'accentuation les separe
        avant = morceau if scale == 1 else morceau.resize((cl * scale, ch * scale), Image.LANCZOS)
        return avant, convert.affiner(morceau, scale, force)

    def cadre_nettete(self, scale):
        """Le rectangle (x, y, largeur, hauteur) prélevé dans l'image d'origine pour l'aperçu.
        Le point visé est calculé une seule fois : sinon le cadrage sauterait d'un coin à
        l'autre dès qu'on bouge un curseur."""
        im = self.src_nettete
        bl, bh = boite_apercu(self)
        cl = min(im.width, max(16, bl // scale))
        ch = min(im.height, max(16, bh // scale))
        if getattr(self, "_centre_net", None) is None:
            self._centre_net = point_net(im)
        fx, fy = self._centre_net
        x = max(0, min(im.width - cl, round(fx * im.width - cl / 2)))
        y = max(0, min(im.height - ch, round(fy * im.height - ch / 2)))
        return x, y, cl, ch

    def _demander_nettete(self):
        if getattr(self, "_attente_nettete", None):
            self.after_cancel(self._attente_nettete)
        self._attente_nettete = self.after(150, self._maj_nettete)

    def _maj_nettete(self):
        self._attente_nettete = None
        comp = getattr(self, "comp_nettete", None)
        if not comp or not comp.winfo_exists():
            return
        scale, force = self.agrandir.get(), self.force.get()
        avant, apres = self._calculer_nettete(scale, force)
        if avant is None:
            return
        comp.maj(avant, apres, _ETIQUETTES_NETTETE(scale))
        w, h = self.src_nettete.size
        self.lbl_nettete.configure(
            text=f"détail à 100 % du fichier produit · force {force} · "
                 f"l'effet s'applique à toute l'image · sortie {w * scale} × {h * scale} px")

    def _ameliorer(self):
        echelle, force = self.agrandir.get(), self.force.get()
        taches = []
        for f in self._images_seules():
            dst = chemin_sortie(f, f.suffix.lstrip("."), " net")
            taches.append((f.name, f, lambda cb, f=f, dst=dst:
                           convert.enhance(f, dst, scale=echelle, force=force, progress=cb)))
        self._lancer(taches, "On améliore vos images...")

    # ---------------- panneau : enlever le fond
    def _p_fond(self, p):
        self._entete(p, "Enlever le fond", "Faites glisser la poignée pour comparer avant et après.")
        if not self.fichiers:
            self._zone_depot(p)
            return
        lot = self._barre_fichiers(p, seulement_images=True)
        if not lot:
            label(p, self._pourquoi_pas_image(), 11, MUTED).pack(anchor="w", padx=30)
            return

        gauche, droite = self._deux_colonnes(p)
        self._apercu(gauche, lot[0], "photo d'origine")

        reg = carte(droite, bg=SOFT, bordure=SOFT_LINE)
        reg.pack(fill="x")
        label(reg, "Le fond du résultat", 10, TXT, "bold", SOFT).pack(anchor="w", padx=14, pady=(12, 8))
        self.fond = tk.StringVar(value="transparent")
        for texte, val in FONDS:
            tk.Radiobutton(reg, text=texte, value=val, variable=self.fond, bg=SOFT, fg=TXT2,
                           font=police(10), activebackground=SOFT, selectcolor=CARD,
                           highlightthickness=0, cursor="hand2").pack(anchor="w", padx=12)
        label(reg, "Transparent, blanc, ou une couleur douce.", 8, MUTED, bg=SOFT).pack(
            anchor="w", padx=14, pady=(4, 10))

        bords = carte(droite, bg=SOFT, bordure=SOFT_LINE)
        bords.pack(fill="x", pady=10)
        label(bords, "Retouche des bords", 10, TXT, "bold", SOFT).pack(anchor="w", padx=14, pady=(12, 4))
        self.adoucir = tk.IntVar(value=0)
        tk.Scale(bords, from_=0, to=10, orient="horizontal", variable=self.adoucir, showvalue=False,
                 bg=ACCENT, fg=TXT, troughcolor=SOFT_LINE, activebackground=ACCENT_DARK,
                 highlightthickness=0, bd=0, sliderrelief="flat").pack(fill="x", padx=14)
        label(bords, "Adoucit le contour du sujet.", 8, MUTED, bg=SOFT).pack(
            anchor="w", padx=14, pady=(2, 12))

        if not convert.modele_pret():
            note = carte(droite, bg=ACCENT_PALE, bordure=ACCENT_PALE)
            note.pack(fill="x", pady=(0, 10))
            # le chiffre vient de convert.py: l'interface annoncait 4,7 Mo (reste du petit
            # modele u2netp, rejete) pour un fichier de 176 Mo, soit 37 fois moins que la realite
            label(note, f"Premier détourage : téléchargement\nautomatique du modèle "
                        f"({convert.TAILLE_MODELE} Mo).\nUne seule fois, puis c'est instantané.",
                  9, ACCENT_DARK, justify="left", bg=ACCENT_PALE).pack(padx=12, pady=10)

        self.btn_action = bouton(droite, "Enlever le fond", self._detourer, primaire=True)
        self.btn_action.pack(fill="x")
        self._zone_progression(droite, compact=True)
        self._prechauffer()

    def _detourer(self):
        fond, adoucir = self.fond.get(), self.adoucir.get()
        taches = []
        for f in self._images_seules():
            dst = chemin_sortie(f, "png", " sans fond")
            taches.append((f.name, f, lambda cb, f=f, dst=dst:
                           convert.remove_background(f, dst, fond=fond, adoucissement=adoucir, progress=cb)))
        self._lancer(taches, "On détoure vos images...")

    # ---------------- briques communes aux panneaux Archives et Sécurité
    def _icone(self, parent, glyphe, fond, encre, cote=46, bg=CARD):
        ico = tk.Canvas(parent, width=cote, height=cote, bg=bg, highlightthickness=0)
        rond_rect(ico, 0, 0, cote - 1, cote - 1, r=13, fill=fond, outline="")
        ico.create_text(cote // 2, cote // 2, text=glyphe, font=police(cote // 3, "bold"), fill=encre)
        return ico

    def _tableau(self, parent, colonnes, lignes_max=12, nb=0):
        """Liste ttk.Treeview dans une carte, avec un bandeau d'en-tête dessiné comme la
        maquette (les en-têtes natifs du thème Windows ignorent nos couleurs).
        colonnes : [(titre, largeur ou None pour celle qui s'étire, ancre « w »/« e »)] ;
        la 1re est la colonne #0 (l'arborescence)."""
        cadre = carte(parent, bg=CARD, bordure=LINE)
        cadre.pack(fill="both", expand=True, pady=(14, 0))
        tete = tk.Frame(cadre, bg=SOFT)
        tete.pack(fill="x")
        for i, (titre, larg, ancre) in enumerate(colonnes):
            tete.grid_columnconfigure(i, weight=0 if larg else 1, minsize=(larg or 0) + (6 if i == 0 else 0))
            label(tete, titre, 9, MUTED, "bold", SOFT).grid(
                row=0, column=i, sticky=ancre, padx=(18 if i == 0 else 4, 4), pady=7)
        tete.grid_columnconfigure(len(colonnes), minsize=18)       # place de la barre de defilement
        tk.Frame(cadre, bg=SOFT_LINE, height=1).pack(fill="x")
        corps = tk.Frame(cadre, bg=CARD)
        corps.pack(fill="both", expand=True)
        noms = [f"c{i}" for i in range(1, len(colonnes))]
        arbre = ttk.Treeview(corps, style="Workbench.Treeview", columns=noms, show="tree",
                             height=max(4, min(lignes_max, nb)), selectmode="extended")
        for i, (titre, larg, ancre) in enumerate(colonnes):
            col = "#0" if i == 0 else noms[i - 1]
            arbre.column(col, width=larg or 240, minwidth=60, stretch=not larg, anchor=ancre)
        barre = ttk.Scrollbar(corps, orient="vertical", command=arbre.yview)
        arbre.configure(yscrollcommand=barre.set)
        barre.pack(side="right", fill="y")
        arbre.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=4)
        arbre.tag_configure("attenue", foreground=MUTED)
        arbre.tag_configure("dossier", font=police(10, "bold"))
        return arbre

    # ---------------- panneau : archives
    def _p_archives(self, p):
        self._entete(p, "Archives",
                     "Ouvrez un .zip pour l'extraire, ou rassemblez des fichiers et dossiers "
                     "dans un nouveau zip.")
        z = self.zip_ouvert
        if not z and self.a_archiver:
            self._p_creer_zip(p)
            return
        if not z:
            self._zone_depot(p, "Glissez un .zip à extraire, ou des fichiers à archiver",
                             "ou cliquez pour choisir un zip", puces=[PUCES[-1]],
                             action=self._choisir_zip)
            ligne = tk.Frame(p, bg=CARD)
            ligne.pack(anchor="w", padx=30, pady=(12, 24))
            bouton(ligne, "Archiver des fichiers...", lambda: self._choisir_a_archiver(False),
                   petit=True).pack(side="left")
            bouton(ligne, "Archiver un dossier...", lambda: self._choisir_a_archiver(True),
                   petit=True).pack(side="left", padx=8)
            return
        contenu = self.zip_contenu
        fichiers = [e for e in contenu if not e["dossier"]]
        medias = [e for e in fichiers if archives.est_media(e["nom"])
                  and not e["chiffre"] and not e["refus"]]
        gauche, droite = self._deux_colonnes(p)

        tete = carte(gauche, bg=CARD, bordure=SOFT_LINE)
        tete.pack(fill="x")
        self._icone(tete, "▤", PECHE, BRUN).pack(side="left", padx=14, pady=12)
        bloc = tk.Frame(tete, bg=CARD)
        bloc.pack(side="left")
        label(bloc, z.name, 12, TXT, "bold").pack(anchor="w")
        n = len(fichiers)
        tk.Label(bloc, text=f"{n} élément{'s' if n > 1 else ''} · "
                            f"{poids(sum(e['taille'] for e in fichiers))} · "
                            f"{len(medias)} média{'s' if len(medias) > 1 else ''}",
                 font=mono(9), fg=MUTED, bg=CARD).pack(anchor="w")
        bouton(tete, "Ouvrir un autre zip", self._choisir_zip, petit=True).pack(side="right", padx=14)

        arbre = self.arbre_zip = self._tableau(gauche, [("Nom", None, "w"), ("Taille", 100, "e"), ("Date", 150, "e")],
                                               lignes_max=12, nb=len(contenu))
        self._remplir_arbre_zip(arbre, contenu)

        dest = carte(droite, bg=SOFT, bordure=SOFT_LINE)
        dest.pack(fill="x")
        label(dest, "Extraire vers", 11, TXT, "bold", SOFT).pack(anchor="w", padx=14, pady=(12, 6))
        self.lbl_dest = tk.Label(dest, text=str(self.zip_dest), font=mono(9), fg=TXT2, bg=CARD,
                                 wraplength=236, justify="left", anchor="w", padx=8, pady=6,
                                 highlightthickness=1, highlightbackground=SOFT_LINE)
        self.lbl_dest.pack(fill="x", padx=14)
        bouton(dest, "Changer de dossier", self._changer_dest_zip, petit=True).pack(
            fill="x", padx=14, pady=(8, 0))
        label(dest, "Rien n'est jamais écrasé : un fichier déjà\nprésent reçoit un numéro.",
              8, MUTED, bg=SOFT, justify="left").pack(anchor="w", padx=14, pady=(8, 12))

        self.btn_action = bouton(droite, "Tout extraire", lambda: self._extraire_zip(False),
                                 primaire=True)
        self.btn_action.pack(fill="x", pady=(12, 0))
        bouton(droite, "Extraire la sélection", lambda: self._extraire_zip(True)).pack(
            fill="x", pady=(8, 0))
        self._zone_progression(droite, compact=True)

        envoi = carte(droite, bg=SOFT, bordure=SOFT_LINE)
        envoi.pack(fill="x")
        m = len(medias)
        label(envoi, f"{m} média{'s' if m > 1 else ''} dans ce zip" if m else "Aucun média dans ce zip",
              11, TXT, "bold", SOFT).pack(anchor="w", padx=14, pady=(12, 4))
        label(envoi, "Images, vidéos et sons extraits puis ouverts\ndans Convertir. Les résultats iront "
                     "à côté\ndu zip." if m else "Pas d'image, de vidéo ni de son à envoyer\nvers un outil.",
              8, MUTED, bg=SOFT, justify="left").pack(anchor="w", padx=14)
        envoyer = tk.Button(envoi, text="Envoyer les médias vers un outil  →", command=self._envoyer_medias,
                            relief="flat", bd=0, cursor="hand2", bg=PECHE, fg=BRUN,
                            activebackground=PECHE_LINE, activeforeground=BRUN,
                            disabledforeground=MUTED, highlightthickness=1,
                            highlightbackground=PECHE_LINE, font=police(10, "bold"), pady=8)
        envoyer.pack(fill="x", padx=14, pady=(10, 14))
        if not m:
            envoyer.configure(state="disabled", bg=SOFT)
        entrees = f"{archives.MAX_ENTREES:,}".replace(",", " ")
        label(droite, "Chemins piégés (..\\, lecteurs) et fichiers\nchiffrés ignorés · "
                      f"{archives.MAX_TOTAL // 1024 ** 3} Go et {entrees} éléments au plus.",
              8, MUTED, justify="left").pack(anchor="w", pady=(10, 0))

    def _remplir_arbre_zip(self, arbre, contenu):
        """Arborescence dossiers / fichiers. Les iid sont des numéros : un nom de membre peut
        contenir n'importe quel caractère, Tcl n'a pas à l'interpréter."""
        self._membre_iid = {}
        noeuds = {"": ""}

        def dossier(parts):
            cle = ""
            for i in range(len(parts)):
                sous = "/".join(parts[:i + 1]) + "/"
                if sous not in noeuds:
                    iid = arbre.insert(noeuds[cle], "end", text=f"{parts[i]}/", open=True,
                                       tags=("dossier",))
                    noeuds[sous] = iid
                    self._membre_iid[iid] = sous     # un dossier emporte tout son contenu
                cle = sous
            return noeuds[cle]

        for e in contenu:
            parts = [m for m in e["nom"].replace("\\", "/").split("/") if m]
            if not parts:
                continue
            if e["refus"]:            # a la racine, nom complet: pas de faux dossier « ../ »
                iid = arbre.insert("", "end", text=f"{e['nom']}  · refusé : {e['refus']}",
                                   values=(poids(e["taille"]), ""), tags=("attenue",))
                self._membre_iid[iid] = e["nom"]
                continue
            if e["dossier"]:
                dossier(parts)
                continue
            texte, tags = parts[-1], ()
            if e["chiffre"]:
                texte, tags = f"{texte}  · chiffré, ignoré", ("attenue",)
            date = e["date"].strftime("%d/%m/%Y %H:%M") if e["date"] else ""
            iid = arbre.insert(dossier(parts[:-1]), "end", text=texte,
                               values=(poids(e["taille"]), date), tags=tags)
            self._membre_iid[iid] = e["nom"]

    def _membres_selectionnes(self):
        arbre = getattr(self, "arbre_zip", None)
        if not arbre or not arbre.winfo_exists():
            return []
        return [self._membre_iid[i] for i in arbre.selection() if i in self._membre_iid]

    def _choisir_zip(self):
        c = filedialog.askopenfilename(title="Choisir une archive zip",
                                       filetypes=[("Archives zip", "*.zip"), ("Tous les fichiers", "*.*")])
        if c:
            self._ouvrir_archive(pathlib.Path(c))

    def _ouvrir_archive(self, z):
        if self._occupe():
            return
        try:
            contenu = archives.lister(z)
        except archives.ArchiveError as e:
            self._toast(str(e))
            return
        self.zip_ouvert, self.zip_contenu = pathlib.Path(z), contenu
        self.a_archiver = []
        self.zip_dest = self.zip_ouvert.parent / self.zip_ouvert.stem
        self._afficher("archives")

    def _changer_dest_zip(self):
        d = filedialog.askdirectory(title="Où extraire l'archive ?", initialdir=str(self.zip_dest.parent))
        if d:
            self.zip_dest = pathlib.Path(d)
            self.lbl_dest.configure(text=str(self.zip_dest))

    def _extraire_zip(self, selection):
        membres = None
        if selection:
            membres = self._membres_selectionnes()
            if not membres:
                self._toast("Sélectionnez d'abord des fichiers dans la liste.")
                return
        z, dest = self.zip_ouvert, self.zip_dest
        self._lancer([(z.name, z, lambda cb: archives.extraire(z, dest, membres, progress=cb))],
                     "On extrait l'archive...", fin=self._fin_extraction)

    def _fin_extraction(self, produits):
        rapport = produits[0]
        self.txt_etat.configure(text="C'est prêt !" if rapport.extraits else "Rien n'a été extrait",
                                fg=TXT)
        self.sous_etat.configure(text=f"{rapport.resume()}\ndans « {rapport.dest} »", justify="left",
                                 wraplength=260)
        self.actions.pack(pady=(10, 0))
        if rapport.extraits:
            bouton(self.actions, "Ouvrir le dossier", lambda: montrer_dans_explorateur(rapport.extraits[0]),
                   petit=True).pack(side="left")
        self.etat_sauve.configure(text="Extrait" if rapport.extraits else "")

    # ---------------- archives : creer un zip
    def _recevoir_archives(self, chemins):
        """Uniquement des zips: on ouvre le premier pour l'extraire. Sinon on archive le tout
        (un zip parmi d'autres fichiers est archive avec eux)."""
        if self._occupe() or not chemins:
            return
        if all(p.suffix.lower() == ".zip" and p.is_file() for p in chemins):
            self._ouvrir_archive(chemins[0])
            return
        self.zip_ouvert, self.a_archiver = None, list(chemins)
        premier = chemins[0]
        nom = (premier.stem if premier.is_file() else premier.name) if len(chemins) == 1 else "Archive"
        self.zip_cree = premier.parent / f"{nom}.zip"
        self._afficher("archives")

    def _choisir_a_archiver(self, dossier):
        if dossier:
            d = filedialog.askdirectory(title="Quel dossier archiver ?")
            chemins = [pathlib.Path(d)] if d else []
        else:
            chemins = [pathlib.Path(c) for c in
                       filedialog.askopenfilenames(title="Quels fichiers archiver ?")]
        self._recevoir_archives(chemins)

    def _ajouter_a_archiver(self):
        plus = [pathlib.Path(c) for c in filedialog.askopenfilenames(title="Ajouter des fichiers")]
        if plus:
            dest = self.zip_cree
            self._recevoir_archives(self.a_archiver + [p for p in plus if p not in self.a_archiver])
            self.zip_cree = dest          # garder le nom de zip deja choisi
            self.lbl_dest.configure(text=str(dest))

    def _p_creer_zip(self, p):
        gauche, droite = self._deux_colonnes(p)
        lot = self.a_archiver
        tete = carte(gauche, bg=CARD, bordure=SOFT_LINE)
        tete.pack(fill="x")
        self._icone(tete, "▤", PECHE, BRUN).pack(side="left", padx=14, pady=12)
        bloc = tk.Frame(tete, bg=CARD)
        bloc.pack(side="left")
        label(bloc, "Nouvelle archive", 12, TXT, "bold").pack(anchor="w")
        n = len(lot)
        tk.Label(bloc, text=f"{n} élément{'s' if n > 1 else ''} à mettre dans le zip",
                 font=mono(9), fg=MUTED, bg=CARD).pack(anchor="w")
        bouton(tete, "Ajouter", self._ajouter_a_archiver, petit=True).pack(side="right", padx=14)
        arbre = self._tableau(gauche, [("Nom", None, "w"), ("Taille", 110, "e")], nb=n)
        for c in lot:     # pas de taille pour un dossier: le parcourir ici figerait la fenetre
            dossier = c.is_dir()
            arbre.insert("", "end", text=f"  {c.name}{os.sep if dossier else ''}",
                         values=("dossier" if dossier else poids(c.stat().st_size),),
                         tags=("dossier",) if dossier else ())

        dest = carte(droite, bg=SOFT, bordure=SOFT_LINE)
        dest.pack(fill="x")
        label(dest, "Enregistrer sous", 11, TXT, "bold", SOFT).pack(anchor="w", padx=14, pady=(12, 6))
        self.lbl_dest = tk.Label(dest, text=str(self.zip_cree), font=mono(9), fg=TXT2, bg=CARD,
                                 wraplength=236, justify="left", anchor="w", padx=8, pady=6,
                                 highlightthickness=1, highlightbackground=SOFT_LINE)
        self.lbl_dest.pack(fill="x", padx=14)
        bouton(dest, "Changer", self._changer_dest_creation, petit=True).pack(
            fill="x", padx=14, pady=(8, 0))
        label(dest, "Rien n'est jamais écrasé : un zip déjà\nprésent reçoit un numéro.",
              8, MUTED, bg=SOFT, justify="left").pack(anchor="w", padx=14, pady=(8, 12))
        self.btn_action = bouton(droite, "Créer le zip", self._creer_zip, primaire=True)
        self.btn_action.pack(fill="x", pady=(12, 0))
        self._zone_progression(droite, compact=True)

    def _changer_dest_creation(self):
        c = filedialog.asksaveasfilename(title="Enregistrer le zip sous", defaultextension=".zip",
                                         initialdir=str(self.zip_cree.parent),
                                         initialfile=self.zip_cree.name, confirmoverwrite=False,
                                         filetypes=[("Archives zip", "*.zip")])
        if c:     # nom deja pris: compresser() numerote, il n'ecrase jamais
            self.zip_cree = pathlib.Path(c)
            self.lbl_dest.configure(text=str(self.zip_cree))

    def _creer_zip(self):
        lot, dest = list(self.a_archiver), self.zip_cree
        self._lancer([(dest.name, lot[0], lambda cb: archives.compresser(lot, dest, progress=cb))],
                     "On crée l'archive...", fin=self._fin_creation)

    def _fin_creation(self, produits):
        z = produits[0]
        self.txt_etat.configure(text="C'est prêt !", fg=TXT)
        self.sous_etat.configure(text=f"« {z.name} » · {poids(z.stat().st_size)}\ndans « {z.parent} »",
                                 justify="left", wraplength=260)
        self.actions.pack(pady=(10, 0))
        bouton(self.actions, "Ouvrir le dossier", lambda: montrer_dans_explorateur(z),
               petit=True).pack(side="left")
        bouton(self.actions, "Voir le contenu", lambda: self._ouvrir_archive(z),
               petit=True).pack(side="left", padx=6)
        self.etat_sauve.configure(text="Archivé")

    def _envoyer_medias(self):
        if self.zip_ouvert and not self._occupe():
            self._ouvrir_zip_medias(self.zip_ouvert, vers="convert")

    # ---------------- panneau : analyse de sécurité
    def _p_securite(self, p):
        self._entete(p, "Analyse de sécurité", "Choisissez un dossier : Windows Defender l'analyse, "
                                               "puis quelques signaux simples sont vérifiés.")
        gauche, droite = self._deux_colonnes(p)
        r = self.resultat_scan
        self.arbre_scan = None
        if r is None:
            vide = carte(gauche, bg=SOFT, bordure=SOFT_LINE)
            vide.pack(fill="x")
            self._icone(vide, "⛨", ROUGE_PALE, ROUGE, cote=56, bg=SOFT).pack(pady=(34, 10))
            label(vide, "Aucune analyse pour l'instant", 13, TXT, "bold", SOFT).pack()
            label(vide, "Choisissez un dossier à droite, ou glissez-le sur la fenêtre,\n"
                        "puis lancez l'analyse.", 10, MUTED, bg=SOFT, justify="center").pack(pady=(4, 34))
        else:
            self._resume_scan(gauche, r)

        dossier = carte(droite, bg=SOFT, bordure=SOFT_LINE)
        dossier.pack(fill="x")
        label(dossier, "Dossier à analyser", 11, TXT, "bold", SOFT).pack(anchor="w", padx=14, pady=(12, 6))
        tk.Label(dossier, text=str(self.dossier_scan or "aucun dossier choisi"), font=mono(9),
                 fg=TXT2 if self.dossier_scan else MUTED, bg=CARD, wraplength=236, justify="left",
                 anchor="w", padx=8, pady=6, highlightthickness=1,
                 highlightbackground=SOFT_LINE).pack(fill="x", padx=14)
        bouton(dossier, "Choisir un dossier", self._choisir_dossier_scan, petit=True).pack(
            fill="x", padx=14, pady=(8, 0))
        label(dossier, "ou glissez un dossier sur la fenêtre.", 8, MUTED, bg=SOFT).pack(
            anchor="w", padx=14, pady=(8, 12))

        self.btn_action = bouton(droite, "⛨  Lancer l'analyse", self._analyser, primaire=True)
        self.btn_action.pack(fill="x", pady=(12, 0))
        self._zone_progression(droite, compact=True)

        niveaux = carte(droite, bg=SOFT, bordure=SOFT_LINE)
        niveaux.pack(fill="x")
        label(niveaux, "Niveaux", 11, TXT, "bold", SOFT).pack(anchor="w", padx=14, pady=(12, 6))
        for grav, texte in ((securite.MENACE, "trouvée par Defender"),
                            (securite.SUSPECT, "nom ou contenu trompeur"),
                            (securite.A_VERIFIER, "programme, script, macros")):
            ligne = tk.Frame(niveaux, bg=SOFT)
            ligne.pack(anchor="w", padx=14, pady=2)
            fond, encre = GRAVITES[grav]
            tk.Label(ligne, text=grav.split(" (")[0], font=police(9, "bold"), fg=encre, bg=fond,
                     padx=7, pady=1).pack(side="left")
            label(ligne, texte, 9, TXT2, bg=SOFT).pack(side="left", padx=(6, 0))
        tk.Frame(niveaux, bg=SOFT, height=10).pack()

        note = carte(droite, bg=ROUGE_PALE, bordure=ROUGE_PALE)
        note.pack(fill="x", pady=(12, 0))
        label(note, "Les signaux locaux sont des indices, pas\nun verdict : ils ne remplacent pas un\n"
                    "antivirus. Rien n'est supprimé ni déplacé.", 9, ROUGE_TXT, bg=ROUGE_PALE,
              justify="left").pack(anchor="w", padx=12, pady=10)

    def _resume_scan(self, gauche, r):
        if r.defender == "menaces":
            fond, encre, glyphe = ROUGE_PALE, ROUGE, "!"
        elif r.defender == "indisponible" or any(s.gravite == securite.SUSPECT for s in r.signaux):
            fond, encre, glyphe = AMBRE_PALE, AMBRE, "?" if r.defender == "indisponible" else "!"
        else:
            fond, encre, glyphe = VERT_PALE, VERT, "✓"
        tete = carte(gauche, bg=CARD, bordure=SOFT_LINE)
        tete.pack(fill="x")
        rond = tk.Canvas(tete, width=40, height=40, bg=CARD, highlightthickness=0)
        rond.pack(side="left", padx=14, pady=12)
        rond.create_oval(0, 0, 39, 39, fill=fond, outline="")
        rond.create_text(20, 20, text=glyphe, font=police(14, "bold"), fill=encre)
        bloc = tk.Frame(tete, bg=CARD)
        bloc.pack(side="left", fill="x", expand=True, pady=10)
        self.lbl_defender = label(bloc, securite.message_defender(r), 12, TXT, "bold")
        self.lbl_defender.pack(anchor="w")
        tk.Label(bloc, text=r.resume(), font=mono(9), fg=MUTED, bg=CARD).pack(anchor="w")
        if r.defender == "indisponible" and r.defender_detail:
            label(bloc, f"({r.defender_detail[:90]})", 8, MUTED).pack(anchor="w")

        if not r.signaux:
            ok = carte(gauche, bg=VERT_PALE, bordure=VERT_PALE)
            ok.pack(fill="x", pady=(14, 0))
            label(ok, "Aucun signal local : rien de suspect dans ce dossier.", 10, VERT,
                  bg=VERT_PALE).pack(anchor="w", padx=14, pady=12)
            return
        arbre = self.arbre_scan = self._tableau(
            gauche, [("Gravité", 130, "w"), ("Fichier", 200, "w"), ("Raison", None, "w")],
            lignes_max=12, nb=len(r.signaux))
        arbre.configure(selectmode="browse")
        # couleur de la gravite sur toute la ligne (un Treeview ne colore pas une seule cellule);
        # « A verifier » reste en noir: c'est le niveau le plus courant, il ne doit pas crier
        arbre.tag_configure(securite.MENACE, foreground=ROUGE)
        arbre.tag_configure(securite.SUSPECT, foreground=AMBRE)
        self._signal_iid = {}
        for s in r.signaux:
            try:
                rel = s.chemin.relative_to(r.dossier)
            except ValueError:
                rel = s.chemin
            iid = arbre.insert("", "end", text=f"● {s.gravite.split(' (')[0]}",
                               values=(str(rel), s.raison), tags=(s.gravite,))
            self._signal_iid[iid] = s
        arbre.bind("<Double-1>", lambda _e: self._ouvrir_emplacement())
        # la raison est souvent plus longue que sa colonne: la ligne choisie est lue en entier ici
        self.lbl_detail = label(gauche, "Cliquez sur une ligne pour lire le détail.", 9, TXT2,
                                justify="left", wraplength=560, anchor="w")
        self.lbl_detail.pack(fill="x", pady=(8, 0))
        arbre.bind("<<TreeviewSelect>>", lambda _e: self._detail_signal())

        bas = tk.Frame(gauche, bg=CARD)
        bas.pack(fill="x", pady=(12, 0))
        bouton(bas, "Ouvrir l'emplacement", self._ouvrir_emplacement, petit=True).pack(side="left")
        label(bas, "Rien n'est supprimé ni déplacé : vous décidez.", 9, MUTED).pack(side="left", padx=12)

    def _detail_signal(self):
        sel = self.arbre_scan.selection()
        if sel:
            s = self._signal_iid[sel[0]]
            self.lbl_detail.configure(text=f"{s.gravite} · {s.chemin}" + chr(10) + s.raison)

    def _choisir_dossier_scan(self, d=None):
        if self._occupe():
            return
        if d is None:
            d = filedialog.askdirectory(title="Quel dossier analyser ?")
            if not d:
                return
        self.dossier_scan, self.resultat_scan = pathlib.Path(d), None
        self._afficher("securite")

    def _analyser(self):
        d = self.dossier_scan
        if not d:
            self._toast("Choisissez d'abord un dossier à analyser.")
            return
        if not d.is_dir():
            self._toast(f"« {d} » n'existe plus.")
            return
        self._lancer([(d.name or str(d), d, lambda cb: securite.analyser(d, progress=cb))],
                     "On analyse le dossier...", fin=self._fin_analyse)

    def _fin_analyse(self, produits):
        self.resultat_scan = produits[0]
        self._afficher("securite")
        self._toast("Analyse terminée")

    def _ouvrir_emplacement(self):
        arbre = self.arbre_scan
        sel = arbre.selection() if arbre and arbre.winfo_exists() else ()
        if not sel:
            self._toast("Sélectionnez une ligne de la liste.")
            return
        chemin = self._signal_iid[sel[0]].chemin
        if chemin.is_file():
            montrer_dans_explorateur(chemin)
        else:
            ouvrir_dossier(chemin if chemin.is_dir() else chemin.parent)

    # ---------------- progression et file d'attente
    def _zone_progression(self, parent, compact=False):
        self.zone_etat = tk.Frame(parent, bg=CARD)
        self.zone_etat.pack(fill="x", padx=0 if compact else 30, pady=(14, 20))

        self.anneau = tk.Canvas(self.zone_etat, width=66, height=66, bg=CARD, highlightthickness=0)
        self.txt_etat = label(self.zone_etat, "", 12, TXT, "bold")
        self.sous_etat = label(self.zone_etat, "", 9, MUTED)
        self.jauge = tk.Canvas(self.zone_etat, height=8, bg=CARD, highlightthickness=0)
        self.btn_annuler = bouton(self.zone_etat, "Annuler", self._annuler, petit=True)
        self.actions = tk.Frame(self.zone_etat, bg=CARD)

    def _dessiner_anneau(self, f):
        self.frac = f
        self.anneau.delete("all")
        self.anneau.create_oval(6, 6, 60, 60, outline=SOFT_LINE, width=5)
        if f > 0:
            self.anneau.create_arc(6, 6, 60, 60, start=90, extent=-359.9 * f, style="arc",
                                   outline=ACCENT, width=5)
        # une seule instruction Pillow peut durer 30 s: un point qui tourne dit que ca travaille,
        # sinon l'anneau fige a 30 % donne l'impression que l'appli est plantee
        self.anneau.create_arc(6, 6, 60, 60, start=self.angle, extent=-26, style="arc",
                               outline=ACCENT_PALE if f else ACCENT, width=5)
        self.anneau.create_text(33, 33, text=f"{int(f * 100)} %", font=police(10, "bold"), fill=TXT)

    def _tourner(self):
        """Fait avancer le point qui tourne, tant qu'un traitement est en cours."""
        if not self.occupe:
            return
        self.angle = (self.angle - 11) % 360
        try:
            self._dessiner_anneau(self.frac)
        except tk.TclError:
            return
        self.after(60, self._tourner)

    def _dessiner_jauge(self, f):
        self.jauge.delete("all")
        w = self.jauge.winfo_width() or 240
        self.jauge.create_rectangle(0, 1, w, 7, fill=SOFT_LINE, outline="")
        if f > 0:
            self.jauge.create_rectangle(0, 1, max(4, w * f), 7, fill=ACCENT, outline="")

    def _prechauffer(self):
        """Filet de securite: normalement le modele est deja charge (demarrage de l'appli).
        S'il ne l'est pas encore, on montre l'attente au lieu d'un bouton qui ne repond pas."""
        if convert.modele_charge():
            return
        self.phase = ("téléchargement du modèle, une seule fois" if not convert.modele_pret()
                      else "préparation du modèle, une seule fois")
        self.debut = time.time()
        self.txt_etat.configure(text="Préparation du détourage...", fg=TXT2)
        self.txt_etat.pack()
        self.sous_etat.pack()
        # le bouton reste cliquable: un clic anticipe attend le modele dans la tache de fond,
        # au lieu de laisser l'utilisateur devant un bouton gris qui ne repond pas
        self.chrono = True
        self._tic()

        gen = self.gen

        def travail():
            def avance(frac):   # _tic() relit self.phase chaque seconde: 176 Mo sans chiffre
                self.phase = f"téléchargement du modèle · {int(frac * 100)} %"   # = barre morte
            try:
                convert.precharger_modele(avance)
                self.msgs.put(("pret", None, None, gen))
            except Exception as e:
                self.msgs.put(("erreur", str(e), "le modèle", gen))

        threading.Thread(target=travail, daemon=True).start()

    def _tic(self, gen=None):
        if not self.chrono or (gen is not None and gen != self.gen):
            return      # panneau change: ce chrono-la n'a plus lieu d'etre
        gen = self.gen
        try:
            self.sous_etat.configure(text=f"{self.phase} \u00b7 {int(time.time() - self.debut)} s")
        except tk.TclError:
            return  # panneau change pendant le chargement
        self.after(1000, lambda: self._tic(gen))

    def _annuler(self):
        """Arrête le traitement en cours. La maquette prévoit ce bouton et il n'existait pas :
        une conversion vidéo partie pour vingt minutes ne se réglait qu'en tuant l'appli."""
        if not self.occupe:
            return
        self.btn_annuler.configure(state="disabled", text="Annulation...")
        self.txt_etat.configure(text="Annulation en cours...", fg=TXT2)
        convert.annuler()

    def _lancer(self, taches, message, fin=None):
        """taches : liste de (nom affiché, fichier source, fonction(callback) -> chemin produit).
        fin(produits) remplace _succes() pour les outils qui ne produisent pas une image
        (Archives rend un archives.Rapport, Sécurité un securite.Resultat)."""
        if not taches or self.occupe:
            return
        convert.reprendre()                 # efface une annulation precedente
        self.occupe = True
        self._fin = fin
        self.sources = [src for _n, src, _f in taches]
        for w in self.actions.winfo_children():
            w.destroy()
        self.actions.pack_forget()
        self.anneau.pack(pady=(0, 6))
        self.txt_etat.configure(text=message, fg=TXT)
        self.txt_etat.pack()
        self.phase = taches[0][0]
        self.debut = time.time()
        self.chrono = True
        self.sous_etat.pack()
        self._tic()
        self.jauge.pack(fill="x", pady=(10, 0))
        self.btn_annuler.configure(state="normal", text="Annuler")
        self.btn_annuler.pack(pady=(10, 0))
        self.page.voir(self.zone_etat)      # sinon elle reste sous la ligne de flottaison
        self._dessiner_anneau(0)
        self._tourner()
        self.after(30, lambda: self._dessiner_jauge(0))
        if self.btn_action:
            self.btn_action.configure(state="disabled")

        gen = self.gen

        def travail():
            produits = []
            for i, (nom, _src, fn) in enumerate(taches):
                def cb(frac, i=i, nom=nom):
                    self.msgs.put(("avance", (i + min(max(frac, 0.0), 1.0)) / len(taches), nom, gen))
                try:
                    r = fn(cb)
                    produits.append(pathlib.Path(r) if isinstance(r, (str, os.PathLike)) else r)
                except convert.Annule:
                    self.msgs.put(("annule", produits, nom, gen))
                    return
                except Exception as e:
                    self.msgs.put(("erreur", str(e), nom, gen))
                    return
                if convert.annulation_demandee():   # arret demande entre deux fichiers
                    self.msgs.put(("annule", produits, nom, gen))
                    return
                self.msgs.put(("avance", (i + 1) / len(taches), nom, gen))
            self.msgs.put(("fini", produits, None, gen))

        threading.Thread(target=travail, daemon=True).start()

    def _pomper(self):
        """Increvable: une exception ici (widget detruit, bug dans un handler) ne doit jamais
        arreter la boucle, sinon plus aucune operation ne se termine jusqu'a redemarrage."""
        try:
            while True:
                genre, charge, nom, gen = self.msgs.get_nowait()
                if gen is not None and gen != self.gen:
                    continue          # message d'un panneau qu'on a quitte
                if genre == "zip":    # gen None: un zip depose vaut pour tous les panneaux
                    self._zip_pret(charge)
                elif genre == "avance":
                    self._dessiner_anneau(charge)
                    self._dessiner_jauge(charge)
                    if nom:
                        self.phase = nom
                elif genre == "pret":
                    self.chrono = False
                    self.txt_etat.configure(text="Prêt à détourer", fg=VERT)
                    self.sous_etat.configure(text="le modèle est chargé, le détourage prend moins d'une seconde")
                    if self.btn_action:
                        self.btn_action.configure(state="normal")
                elif genre == "erreur":
                    self._echec(charge, nom)
                elif genre == "annule":
                    self._annule_fini(charge)
                elif self._fin:
                    fin, self._fin = self._fin, None
                    self._ranger_progression()
                    fin(charge)
                else:
                    self._succes(charge)
        except queue.Empty:
            pass
        except tk.TclError:
            pass                      # panneau change pendant l'operation
        except Exception as e:
            self.occupe = self.chrono = False
            print("erreur dans la boucle de messages:", e)
        finally:
            self._pompe = self.after(60, self._pomper)

    def _fermer(self):
        """Fermer la fenetre doit arreter ffmpeg: le thread de travail meurt avec Python, pas
        le processus enfant, qui continuait a mouliner et a ecrire son fichier dans le vide."""
        self.chrono = False
        if self.occupe:
            convert.annuler()
        self._pompe = None
        # tous les rendez-vous en attente (pompe, chrono, toasts, redessins) meurent avec la
        # fenetre: sinon Tk crache un « invalid command name » par callback orphelin
        for ident in self.tk.splitlist(self.tk.call("after", "info")):
            try:
                self.after_cancel(ident)
            except Exception:
                pass
        self._nettoyer_zips()
        self.destroy()

    def _nettoyer_zips(self):
        """Les medias extraits des zips deposes n'ont plus de raison d'etre: les resultats ont
        ete ecrits a cote du zip (dossier_sortie), jamais dans %TEMP%."""
        for d in self.extractions:
            if ZIP_TEMP in d.parents:     # garde-fou: on n'efface QUE sous workbench_zip
                shutil.rmtree(d, ignore_errors=True)
        for p in [p for p in _ORIGINE_ZIP if ZIP_TEMP in p.parents and not p.exists()]:
            del _ORIGINE_ZIP[p]
        try:
            ZIP_TEMP.rmdir()              # seulement s'il est vide
        except OSError:
            pass

    def _ranger_progression(self):
        """Etat de repos commun a la fin, a l'echec et a l'annulation."""
        self.occupe = self.chrono = False
        self._fin = None
        convert.reprendre()
        self.anneau.pack_forget()
        self.jauge.pack_forget()
        self.btn_annuler.pack_forget()
        if self.btn_action:
            self.btn_action.configure(state="normal")

    def _echec(self, message, nom):
        self._ranger_progression()
        self.txt_etat.configure(text=f"Échec sur {nom}", fg=ACCENT_DARK)
        self.sous_etat.configure(text=message[:200])

    def _annule_fini(self, produits):
        """Une annulation n'est pas une panne: on dit ce qui a quand meme ete ecrit."""
        self._ranger_progression()
        self.txt_etat.configure(text="Traitement annulé", fg=TXT2)
        n = len(produits)
        self.sous_etat.configure(
            text=f"{n} fichier{'s' if n > 1 else ''} déjà enregistré{'s' if n > 1 else ''} avant l'arrêt"
                 if n else "aucun fichier n'a été écrit")
        if n:
            self.resultats = produits
            self.btn_export.configure(state="normal")
            self.actions.pack(pady=(10, 0))
            bouton(self.actions, "Ouvrir le dossier",
                   lambda: montrer_dans_explorateur(produits[0]), petit=True).pack(side="left")

    def _succes(self, produits):
        self._ranger_progression()
        self.resultats = produits
        self.btn_export.configure(state="normal")
        self.etat_sauve.configure(text="Enregistré")

        n = len(produits)
        total = sum(p.stat().st_size for p in produits)
        self.txt_etat.configure(text="C'est prêt !", fg=TXT)
        self.sous_etat.configure(
            text=f"{n} fichier{'s' if n > 1 else ''} · {poids(total)} · "
                 f"dans « {produits[0].parent.name} »")
        self.actions.pack(pady=(10, 0))
        bouton(self.actions, "Ouvrir le dossier",
               lambda: montrer_dans_explorateur(produits[0]), petit=True).pack(side="left")
        bouton(self.actions, "Recommencer", lambda: self._afficher(self.outil),
               petit=True).pack(side="left", padx=6)

        self._fenetre_resultat(produits)

    def _fenetre_resultat(self, produits):
        """Confirmation en grand, pour tous les outils. En petit dans la colonne de droite,
        on ne voyait pas que le nouveau fichier existait."""
        produit = produits[0]
        # la source du PREMIER produit, pas self.fichiers[0]: la netteté et le détourage ne
        # traitent que les images, et comparer une vidéo à une photo détourée n'a aucun sens
        sources = getattr(self, "sources", None) or self.fichiers
        src = sources[0] if sources else None
        f = tk.Toplevel(self, bg=CARD)
        f.title("C'est prêt !")
        f.configure(bg=CARD)
        f.transient(self)
        f.lift()
        f.focus_force()   # la fenetre principale peut etre en topmost, sinon celle-ci passe derriere

        n = len(produits)
        total = sum(p.stat().st_size for p in produits)
        label(f, "C'est prêt !", 18, TXT, "bold").pack(anchor="w", padx=20, pady=(16, 2))
        titre = produit.name if n == 1 else f"{n} fichiers, à commencer par « {produit.name} »"
        label(f, f"{titre} · {poids(total)}", 11, TXT2).pack(anchor="w", padx=20)
        label(f, f"enregistré dans « {produit.parent} »", 10, MUTED).pack(
            anchor="w", padx=20, pady=(2, 12))

        try:
            avant = ouvrir_image(src, (560, 360)) if src else None
            apres = ouvrir_image(produit, (560, 360))
        except Exception:
            avant = apres = None      # image demesuree, format exotique: la confirmation reste
        i0, i1 = (convert.probe(src) if src else {}), convert.probe(produit)
        if i0.get("width") and i1.get("width") and (i0["width"], i0["height"]) != (i1["width"], i1["height"]):
            label(f, f"Votre image est passée de {i0['width']} × {i0['height']} "
                     f"à {i1['width']} × {i1['height']} pixels.", 10, MUTED).pack(
                anchor="w", padx=20, pady=(0, 10))
        if avant is not None and apres is not None:
            Comparateur(f, avant, apres,
                        etiquettes=(f"avant · {i0.get('width')} px",
                                    f"après · {i1.get('width')} px")).pack(padx=20)
        elif apres is not None:
            # ref portee par la fenetre: dans self.images elle survivait a sa fermeture
            f._photo = ImageTk.PhotoImage(sur_damier(apres))
            tk.Label(f, image=f._photo, bg=CARD, bd=0).pack(padx=20)

        # recapitulatif de la maquette: Format / Dimensions / Poids
        fiche = tk.Frame(f, bg=CARD)
        fiche.pack(fill="x", padx=20, pady=(12, 0))
        dims = f"{i1['width']} × {i1['height']}" if i1.get("width") else "—"
        for titre, valeur in (("Format", produit.suffix.lstrip(".").upper()),
                              ("Dimensions", dims),
                              ("Poids", poids(produit.stat().st_size))):
            bloc = carte(fiche, bg=SOFT, bordure=SOFT_LINE)
            bloc.pack(side="left", fill="x", expand=True, padx=(0, 8))
            label(bloc, titre, 8, MUTED, bg=SOFT).pack(anchor="w", padx=10, pady=(8, 0))
            label(bloc, valeur, 11, TXT, "bold", SOFT).pack(anchor="w", padx=10, pady=(0, 8))

        note = carte(f, bg=VERT_PALE, bordure=VERT_PALE)
        note.pack(fill="x", padx=20, pady=12)
        label(note, "L'original reste intact dans votre dossier.", 9, VERT, bg=VERT_PALE).pack(
            padx=12, pady=8)
        ligne = tk.Frame(f, bg=CARD)
        ligne.pack(fill="x", padx=20, pady=(0, 16))
        bouton(ligne, "Ouvrir le dossier", lambda: montrer_dans_explorateur(produit),
               primaire=True).pack(side="left", fill="x", expand=True)
        bouton(ligne, "Fermer", f.destroy).pack(side="left", padx=(8, 0))

        f.update_idletasks()
        larg, haut = f.winfo_reqwidth(), f.winfo_reqheight()
        x = self.winfo_rootx() + max(20, (self.winfo_width() - larg) // 2)
        y = self.winfo_rooty() + max(20, (self.winfo_height() - haut) // 3)
        f.geometry(f"+{x}+{y}")       # toujours par-dessus la fenetre, jamais dans un coin

    # ---------------- divers
    def _exporter(self):
        # shutil.copy2 copie par blocs et garde les dates; write_bytes(read_bytes()) chargeait
        # le fichier entier en memoire — une video de 4 Go partait en MemoryError
        if not self.resultats:
            return
        if len(self.resultats) == 1:
            cible = filedialog.asksaveasfilename(initialfile=self.resultats[0].name,
                                                 defaultextension=self.resultats[0].suffix)
            if cible:
                try:
                    shutil.copy2(self.resultats[0], cible)
                except OSError as e:
                    self._toast(f"Export impossible : {e}")
                    return
                self.etat_sauve.configure(text="Exporté")
            return
        dossier = filedialog.askdirectory(title="Où exporter les fichiers ?")
        if not dossier:
            return
        dossier = pathlib.Path(dossier)
        deja = [p.name for p in self.resultats if (dossier / p.name).exists()]
        if deja and not messagebox.askyesno(
                "Fichiers déjà présents",
                f"{len(deja)} fichier(s) du même nom existent déjà dans ce dossier "
                f"({', '.join(deja[:3])}).\nLes remplacer ?"):
            return
        try:
            for p in self.resultats:
                shutil.copy2(p, dossier / p.name)
        except OSError as e:
            self._toast(f"Export impossible : {e}")
            return
        self.etat_sauve.configure(text=f"{len(self.resultats)} exportés")

    def _toast(self, message):
        """Le compte a rebours n'efface que SON propre message: sans le jeton, un toast lance
        avant une reussite effacait le « Enregistré » cinq secondes plus tard."""
        self.etat_sauve.configure(text=message)
        self._jeton_toast = jeton = object()

        def effacer():
            if getattr(self, "_jeton_toast", None) is jeton and self.etat_sauve["text"] == message:
                self.etat_sauve.configure(text="")

        self.after(5000, effacer)


def _smoke(app):
    """Verifie un build: les 6 panneaux se construisent, le modele se charge, un detourage passe,
    un zip s'extrait, une analyse de securite aboutit.
    Ecrit le resultat dans un fichier, car un .exe --windowed n'a pas de console."""
    import tempfile, traceback
    journal = pathlib.Path(tempfile.gettempdir()) / "workbench_smoke.txt"
    lignes = []
    try:
        for cle in ["accueil"] + [o[0] for o in OUTILS]:
            app._afficher(cle)
            lignes.append(f"panneau {cle} : ok")
        d = pathlib.Path(tempfile.mkdtemp())
        src = d / "test.png"
        Image.new("RGB", (200, 150), (90, 120, 60)).save(src)
        t = time.time()
        convert.precharger_modele()
        lignes.append(f"modele charge : {time.time() - t:.1f}s")
        t = time.time()
        convert.remove_background(src, d / "sortie.png")
        lignes.append(f"detourage : {time.time() - t:.1f}s")
        convert.resize(src, d / "grand.png", width=400)
        lignes.append("redimensionnement : ok")
        # ffmpeg n'est pas embarque dans le .exe: sans lui, video et son ne marchent pas
        son = d / "son.mp3"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=1", str(son)], timeout=60)
        convert.convert(son, d / "son.wav")
        lignes.append("ffmpeg (son) : ok")
        ff = shutil.which("ffmpeg")      # le build doit utiliser SON ffmpeg, pas celui du PATH
        assert ff and pathlib.Path(sys.executable).parent in pathlib.Path(ff).parents, ff
        lignes.append(f"ffmpeg embarque : {ff}")
        import zipfile
        with zipfile.ZipFile(d / "lot.zip", "w") as z:
            z.write(src, "photos/test.png")
            z.writestr("lisezmoi.txt", "bonjour")
        rapport = archives.extraire_medias(d / "lot.zip", d / "extrait")
        assert [p.name for p in rapport.extraits] == ["test.png"], rapport
        lignes.append("archives : ok")
        r = securite.analyser(d / "extrait")
        lignes.append(f"securite : {r.nb_fichiers} fichier(s), defender = {r.defender} "
                      f"{r.defender_detail}".rstrip())
        lignes.append("SMOKE OK")
    except Exception:
        lignes.append(traceback.format_exc())
    journal.write_text(chr(10).join(lignes), encoding="utf-8")
    app._fermer()


if __name__ == "__main__":
    app = Studio()
    if "--smoke" in sys.argv:  # check runnable, y compris sur un .exe fraichement construit
        app.after(600, lambda: _smoke(app))
    app.mainloop()
