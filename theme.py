"""
Thème couleur d'un album, calibré sur le fond animé de l'app Apple Music (mesuré sur Sidra, 7 albums) :
  - le fond est la pochette floutée, assombrie (V' ~ 0.35 * V, plafond ~0.3) et saturée
    (pochette noire -> fond quasi noir, pochette blanche -> gris moyen, dorée -> or saturé)
  - il est animé par des couches de cette image qui tournent lentement (voir renderer.py)
  - texte blanc + blanc atténué
"""
import colorsys
import math

from PIL import Image, ImageFilter

BLUR    = 4       # flou fort avant la tonalité : les zones vives s'ajoutent aux zones sombres (moyenne)
S_MAX   = 1.00    # saturation maximale
S_KNEE  = 0.24    # en dessous, la saturation reste faible (les gris restent gris)
V_GAMMA = 2.0     # courbe très raide : sombre -> presque noir, vif -> reste vif (fort contraste local)
V_GAIN  = 1.5     # luminosité brute = GAIN * V^gamma
S_POW   = 3.0     # raideur de la courbe de saturation
PUSH_START = 0.40  # saturation à partir de laquelle les oranges glissent vers le rouge
PUSH_RANGE = 0.20
V_SGAIN = 0.60    # bonus de luminosité des zones colorées
S_ADAPT = 0.55     # >0 : normalise la saturation sur le p90 de la pochette (cible S_ADAPT)
V_FLOOR = 0.22     # plancher de luminosité des zones saturées : la couleur reste visible sur pochettes sombres
WARM_KNEE = 0.9    # les tons chauds (sépia, chair, crème : teintes 10-60°) exigent plus de saturation pour devenir vifs
TEXT_MIN_CONTRAST = 3.6   # sous ce contraste avec du blanc, on passe au texte foncé
V_CAP   = 0.50    # plafond doux : le fond d'Apple Music ne dépasse guère V ~0.3

def _warm_weight(deg):
    """1 pour les teintes chair/sépia/crème (18-52°), décroît linéairement jusqu'à 0 à 8° et 62°."""
    if deg < 8 or deg > 62:
        return 0.0
    if deg < 18:
        return (deg - 8) / 10
    if deg <= 52:
        return 1.0
    return (62 - deg) / 10


def _push_red(h):
    """Les oranges/bruns des pochettes ressortent en vrai rouge dans Apple Music (Metallica) :
    teintes < 30° ramenées vers 0°, retour progressif à l'identité vers 50° (l'or, ~48°, ne bouge pas)."""
    deg = h * 360
    if deg >= 50:
        return h
    k = 0.35 if deg <= 30 else 0.35 + 0.65 * (deg - 30) / 20
    return (deg * k) / 360


def _tone(r, g, b, gain=1.0):
    """Tonalité Apple Music (mesurée sur Sidra) : saturation en courbe raide (les gris restent gris,
    les couleurs deviennent très vives) et luminosité qui monte un peu avec la saturation."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    w = _warm_weight(h * 360)
    s = min(1.0, s * (1 + (gain - 1) * (1 - w)))     # pas de gain adaptatif sur les tons chauds
    # le glissement orange -> rouge ne concerne que les couleurs déjà vives (pas les tons chair/beige)
    k = min(1.0, max(0.0, (s - PUSH_START) / PUSH_RANGE))
    h = h * (1 - k) + _push_red(h) * k
    s2 = S_MAX * (1 - math.exp(-(s / (S_KNEE * (1 + WARM_KNEE * w))) ** S_POW))
    raw = V_GAIN * (v ** V_GAMMA) * (1 + V_SGAIN * s)   # les zones colorées sont un peu plus lumineuses
    v2 = raw / (1 + (raw / V_CAP) ** 4) ** 0.25
    v2 = max(v2, V_FLOOR * (s2 ** 1.5))
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s2, v2)
    return int(r2 * 255), int(g2 * 255), int(b2 * 255)


def apple_bg_image(img: Image.Image, size=64) -> Image.Image:
    """Pochette -> petite image floutée, sombre et saturée (base du fond animé)."""
    small = img.convert('RGB').resize((size, size), Image.BOX).filter(ImageFilter.GaussianBlur(BLUR))
    # on floute AVANT de saturer : le flou moyenne les teintes et délave, on rend la vivacité après
    gain = 1.0
    if S_ADAPT > 0:
        ss = sorted(colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[1] for r, g, b in small.getdata()
                    if max(r, g, b) > 40)
        if ss:
            p90 = ss[int(len(ss) * 0.9)]
            gain = min(2.2, max(1.0, S_ADAPT / max(p90, 0.25)))
    toned = Image.new('RGB', small.size)
    toned.putdata([_tone(*p, gain=gain) for p in small.getdata()])
    return toned.filter(ImageFilter.GaussianBlur(1))


def _lum(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def extract_theme(img: Image.Image):
    """Retourne (theme dict JSON-compatible, image de fond 64x64)."""
    bg = apple_bg_image(img)
    mean = bg.resize((1, 1), Image.BOX).getpixel((0, 0))

    # Texte blanc par défaut ; texte foncé si les zones claires du fond le rendraient illisible.
    lums = sorted(_lum(p) for p in bg.getdata())
    l75 = lums[int(len(lums) * 0.75)]
    white_contrast = 1.05 / (l75 + 0.05)
    dark_text = white_contrast < TEXT_MIN_CONTRAST and (l75 + 0.05) / 0.06 > white_contrast
    theme = {
        'dark': not dark_text,
        'base': tuple(int(c * 0.8) for c in mean),
        'text1': (18, 18, 22) if dark_text else (255, 255, 255),
        'text2': (70, 70, 80) if dark_text else (205, 205, 210),
    }
    return theme, bg
