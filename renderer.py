import math
import time

import cairo
import gi

gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Pango, PangoCairo

from constants import ARM_MAX_LIFT, VINYL_IN_START, SPIN_DEG_PER_S

BG_BASE  = (246 / 255, 246 / 255, 249 / 255)

BG_CACHE_S = 0.10   # le fond bouge très lentement : recalculé ~10 fois par seconde

# Couches du fond animé : (période s [<0 = sens inverse], phase, échelle, opacité, dérive x, dérive y)
BG_LAYERS = (
    ( 80.0, 0.0, 1.20, 1.00,  0.06,  0.06),
    (-55.0, 2.1, 1.00, 0.40, -0.08,  0.05),
)
GROOVE_OUT = 0.94    # rayon extérieur du sillon (fraction du rayon du disque)
GROOVE_IN  = 0.53    # rayon intérieur (juste avant l'anneau de la pochette)
LYRICS_DROP = 0.09       # de combien la barre et les boutons descendent quand les paroles s'affichent (fraction de la hauteur)
LYRICS_OFFSET_S = 0.0   # décalage global des paroles (secondes ; positif = paroles plus tôt)
NEEDLE_ANG = 0.77    # angle (rad) du point de contact du stylet au milieu du sillon
STYLUS_LEN = 0.14    # distance tige → stylet (fraction du rayon)
STYLUS_TILT = 14.0   # inclinaison de la cellule (degrés)
DISC_SCALE = 0.36   # rayon du disque en fraction de la hauteur d'écran


def blit_surface(cr: cairo.Context, surface: cairo.ImageSurface, w: float, h: float, alpha: float = 1.0):
    """Étire/centre une surface pour couvrir toute la zone (cover)."""
    sw, sh = surface.get_width(), surface.get_height()
    scale  = max(w / sw, h / sh)
    cr.save()
    cr.translate((w - sw * scale) / 2, (h - sh * scale) / 2)
    cr.scale(scale, scale)
    cr.set_source_surface(surface, 0, 0)
    cr.paint_with_alpha(alpha)
    cr.restore()


def blit_disc(cr: cairo.Context, surface: cairo.ImageSurface, label_r: float):
    """Centre et rogne une surface dans un cercle de rayon label_r (déjà clipé)."""
    sw, sh = surface.get_width(), surface.get_height()
    scale  = (2 * label_r) / min(sw, sh)
    cr.save()
    cr.scale(scale, scale)
    cr.translate(-sw / 2, -sh / 2)
    cr.set_source_surface(surface, 0, 0)
    cr.paint()
    cr.restore()


_PANGO_CTX = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))


def make_layout(text, size, bold=True, max_w=None, max_lines=1):
    """Mise en page Pango : contrairement à l'API texte de Cairo, elle bascule sur une police de secours
    pour les caractères absents de la police principale (♪, japonais, coréen, arabe, cyrillique, emojis…)."""
    lay = PangoCairo.create_layout(_PANGO_CTX)
    fd = Pango.FontDescription()
    fd.set_family('Sans')
    fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    fd.set_absolute_size(size * Pango.SCALE)
    lay.set_font_description(fd)
    lay.set_text(text, -1)
    if max_w:
        lay.set_width(int(max_w * Pango.SCALE))
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay.set_ellipsize(Pango.EllipsizeMode.END)
        lay.set_height(-max_lines)                # nombre de lignes maximum (négatif = en lignes)
    return lay


def rounded_rect(cr: cairo.Context, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r,     r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0,            math.pi / 2)
    cr.arc(x + r,     y + h - r, r, math.pi / 2,  math.pi)
    cr.arc(x + r,     y + r,     r, math.pi,      3 * math.pi / 2)
    cr.close_path()


def arm_angle_for(px, py, cx, cy, needle_r, arm_len) -> float:
    """Angle du bras (pivot et longueur fixes) pour que la pointe soit à needle_r du centre du disque."""
    dx, dy = px - cx, py - cy
    d = math.hypot(dx, dy)
    a = (needle_r ** 2 - arm_len ** 2 + d ** 2) / (2 * d)
    hh = math.sqrt(max(0.0, needle_r ** 2 - a ** 2))
    ux, uy = dx / d, dy / d
    mx, my = cx + a * ux, cy + a * uy
    p1 = (mx - hh * uy, my + hh * ux)
    p2 = (mx + hh * uy, my - hh * ux)
    tx, ty = p1 if p1[1] > p2[1] else p2   # point le plus bas
    return math.atan2(ty - py, tx - px)


class Renderer:
    """
    Rendu Cairo du player vinyle — style plat "MD Vinyl" (fond clair, grand disque
    coupé à gauche, grande pochette, bras blanc). L'ancien rendu réaliste est
    conservé dans renderer_classic.py.

    Reçoit l'état d'animation depuis AnimEngine (référence) et les surfaces
    Cairo (attributs publics mis à jour par WallpaperWindow).
    Point d'entrée : draw(cr, width, height) branché sur le draw_func GTK4.
    """

    def __init__(self, anim):
        self._anim = anim

        # Surfaces (mises à jour par MusicChannel)
        self.album_surface      = None
        self.bg_surface         = None
        self.prev_album_surface = None
        self.prev_bg_surface    = None
        self.default_surface    = None

        self.track = {}
        self.lyrics = None              # objet lyrics.Lyrics (assigné par MusicChannel)
        self.lyrics_enabled = True      # bascule utilisateur (bouton / commande), mémorisée entre les lancements
        self._lm = 0.0                  # 0 = mise en page normale, 1 = titre remonté + paroles affichées
        self._line_pos = 0.0            # ligne "au centre" (fractionnaire : défilement doux)
        self._ly_clock = None
        self._ly_layout = {}
        self.controls_visible = False   # affichés seulement si aucune fenêtre ne couvre le bureau
        self.hover = None               # bouton survolé : 'previous' | 'play_pause' | 'next'
        self.prev_theme = None      # thème de l'album précédent (fondu enchaîné)
        self.clock = time.monotonic
        self._last_bg_redraw = 0.0
        self._bg_cache = None
        self._bg_cache_key = None
        self._bg_cache_refs = None
        self._bg_cache_t = 0.0
        self._sprites = {}          # faces de disque pré-dessinées (clé : pochette, rayon)

    # ── Point d'entrée ───────────────────────────────────────────────────── #

    def draw(self, cr: cairo.Context, width: int, height: int):
        if (self.album_surface or self.bg_surface) and self.track:
            self._draw_music(cr, width, height)
        else:
            self._draw_default(cr, width, height)

    # ── Scènes ───────────────────────────────────────────────────────────── #

    def _draw_default(self, cr, w, h):
        if self.default_surface:
            blit_surface(cr, self.default_surface, w, h)
        else:
            cr.set_source_rgb(*BG_BASE)
            cr.paint()

    def layered(self) -> bool:
        """True si la scène musique est active et son fond peut être fourni séparément (render_bg_small)."""
        return bool(self.bg_surface and (self.album_surface or self.bg_surface) and self.track)

    def draw_fg(self, cr, w, h):
        """Avant-plan seul (disque, bras, texte), sur fond transparent ; le fond vient de render_bg_small()."""
        if self.layered():
            self._draw_music(cr, w, h, with_bg=False)
        else:
            self._draw_default(cr, w, h)

    def render_bg_small(self, w, h, ws=256):
        """Fond animé en basse résolution (agrandi par le GPU côté GTK), fondu enchaîné inclus."""
        hs = max(1, round(ws * h / w))
        out = cairo.ImageSurface(cairo.FORMAT_RGB24, ws, hs)
        c = cairo.Context(out)
        c.set_source_rgb(*BG_BASE)
        c.paint()
        t   = self._anim.transition
        now = self.clock()
        cur_base  = ((self.track or {}).get('theme') or {}).get('base')
        prev_base = (self.prev_theme or {}).get('base')
        if self.prev_bg_surface and t < 1.0:
            self._paint_layers(c, self.prev_bg_surface, prev_base, ws, hs, now, 1.0)
            self._paint_layers(c, self.bg_surface, cur_base, ws, hs, now, t)
        else:
            self._paint_layers(c, self.bg_surface, cur_base, ws, hs, now, 1.0)
        return out

    def control_layout(self, w, h):
        """Centres et rayon des trois boutons (sous le titre/artiste) : ([(nom, x, y)], rayon)."""
        radius = h * DISC_SCALE
        x0 = w * 0.33 + radius * 1.30
        y = h * 0.50 + radius * 0.66 + h * LYRICS_DROP * self._lm
        r = radius * 0.105
        gap = r * 2.9
        buttons = [('previous', x0 + r, y), ('play_pause', x0 + r + gap, y), ('next', x0 + r + 2 * gap, y)]
        if self.lyrics and self.lyrics.state == 'ok':
            buttons.append(('lyrics', x0 + r + 3.4 * gap, y))
        return buttons, r

    def bar_layout(self, w, h):
        """(x, y, largeur) de la barre de progression, alignée sur le titre."""
        radius = h * DISC_SCALE
        x = w * 0.33 + radius * 1.30
        # descend un peu quand les paroles sont affichées (elles ont besoin de plus de place)
        return x, h * 0.50 + radius * 0.40 + h * LYRICS_DROP * self._lm, min(w * 0.30, w - x - w * 0.04)

    def bar_fraction(self, x, w, h):
        bx, _by, bw = self.bar_layout(w, h)
        return min(1.0, max(0.0, (x - bx) / bw))

    def _draw_progress(self, cr, w, h):
        T = self._anim.song_duration
        if T <= 0:
            return
        elapsed = min(T, self._anim.track_elapsed_s())
        frac = elapsed / T
        radius = h * DISC_SCALE
        bx, by, bw = self.bar_layout(w, h)
        theme = (self.track or {}).get('theme') or {}
        c1 = tuple(v / 255 for v in theme.get('text1', (255, 255, 255)))
        c2 = tuple(v / 255 for v in theme.get('text2', (205, 205, 210)))
        hot = self.hover == 'bar'
        th = radius * (0.024 if hot else 0.016)

        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_width(th)
        cr.set_source_rgba(*c1, 0.22)
        cr.move_to(bx, by); cr.line_to(bx + bw, by); cr.stroke()
        if frac > 0:
            cr.set_source_rgba(*c1, 0.95)
            cr.move_to(bx, by); cr.line_to(bx + bw * frac, by); cr.stroke()
        if hot:
            cr.arc(bx + bw * frac, by, radius * 0.030, 0, 2 * math.pi)
            cr.set_source_rgba(*c1, 1.0)
            cr.fill()

        def mmss(sec):
            sec = int(sec)
            return f'{sec // 60}:{sec % 60:02d}'
        cr.select_font_face('Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(radius * 0.072)
        cr.set_source_rgba(*c2, 0.9)
        ty = by + radius * 0.10
        cr.move_to(bx, ty); cr.show_text(mmss(elapsed))
        right = '-' + mmss(T - elapsed)
        cr.move_to(bx + bw - cr.text_extents(right).x_advance, ty); cr.show_text(right)
        cr.restore()

    def control_at(self, x, y, w, h):
        if not self.controls_visible:
            return None
        bx, by, bw = self.bar_layout(w, h)
        if self._anim.song_duration > 0 and bx - 12 <= x <= bx + bw + 12 and abs(y - by) <= h * DISC_SCALE * 0.07:
            return 'bar'
        buttons, r = self.control_layout(w, h)
        for name, bx, by in buttons:
            if (x - bx) ** 2 + (y - by) ** 2 <= (r * 1.15) ** 2:
                return name
        return None

    def _draw_controls(self, cr, w, h):
        theme = (self.track or {}).get('theme') or {}
        fg = tuple(v / 255 for v in theme.get('text1', (255, 255, 255)))
        buttons, r = self.control_layout(w, h)
        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        for name, bx, by in buttons:
            hot = self.hover == name
            on = name == 'lyrics' and self.lyrics_enabled       # bouton "paroles" enfoncé quand activé
            cr.arc(bx, by, r, 0, 2 * math.pi)
            cr.set_source_rgba(*fg, 0.30 if (hot or on) else 0.12)
            cr.fill()
            cr.set_source_rgba(*fg, 0.95 if (hot or on) else 0.85)
            k = r * 0.36            # demi-taille des icônes
            if name == 'lyrics':
                # icône "paroles" : trois lignes de longueurs différentes
                cr.set_line_width(k * 0.30)
                for dy, length in ((-0.62, 1.7), (0.0, 1.25), (0.62, 1.55)):
                    cr.move_to(bx - 0.85 * k, by + dy * k * 1.05)
                    cr.line_to(bx - 0.85 * k + length * k, by + dy * k * 1.05)
                    cr.stroke()
            elif name == 'play_pause':
                if self._anim.is_playing:
                    for dx in (-0.55, 0.55):
                        rounded_rect(cr, bx + dx * k - 0.28 * k, by - k, 0.56 * k, 2 * k, 0.12 * k)
                        cr.fill()
                else:
                    cr.move_to(bx - 0.7 * k, by - k)
                    cr.line_to(bx + 1.0 * k, by)
                    cr.line_to(bx - 0.7 * k, by + k)
                    cr.close_path()
                    cr.fill()
            else:
                # Deux triangles accolés, centrés sur le bouton (boîte englobante centrée, +0.04k vers la pointe
                # pour compenser le poids visuel côté base).
                d = -1 if name == 'previous' else 1     # sens des pointes
                tw = 0.95 * k                           # largeur d'un triangle
                left = -tw + d * 0.04 * k               # bord gauche du groupe (largeur totale = 2·tw)
                for i in range(2):
                    x0 = bx + left + i * tw             # bord gauche du triangle i
                    base, tip = (x0, x0 + tw) if d > 0 else (x0 + tw, x0)
                    cr.move_to(base, by - 0.85 * k)
                    cr.line_to(tip, by)
                    cr.line_to(base, by + 0.85 * k)
                    cr.close_path()
                    cr.fill()
        cr.restore()

    # ── Paroles synchronisées ──────────────────────────────────────────────── #

    def _ly_target(self):
        ly = self.lyrics
        if not (ly and ly.state == 'ok' and self.lyrics_enabled and self.layered()):
            return 0.0, 0
        return 1.0, max(-1, ly.index_at(self._anim.track_elapsed_s() + LYRICS_OFFSET_S))

    def lyrics_animating(self) -> bool:
        """Vrai tant que le titre glisse ou que les paroles défilent (pour continuer à redessiner)."""
        target, idx = self._ly_target()
        return abs(self._lm - target) > 0.004 or (target > 0 and abs(self._line_pos - idx) > 0.01)

    def _ly_step(self):
        """Fait avancer les animations des paroles selon le temps écoulé (indépendant du taux d'images)."""
        now = self.clock()
        dt = 0.0 if self._ly_clock is None else min(0.1, now - self._ly_clock)
        self._ly_clock = now
        target, idx = self._ly_target()
        self._lm += (target - self._lm) * min(1.0, dt * 5)
        if abs(self._lm - target) < 0.004:
            self._lm = target
        if target > 0:
            if abs(idx - self._line_pos) > 3:            # saut (changement de morceau, déplacement) : on recale
                self._line_pos = float(idx)
            else:
                self._line_pos += (idx - self._line_pos) * min(1.0, dt * 8)
                if abs(idx - self._line_pos) < 0.01:
                    self._line_pos = float(idx)

    def _wrap(self, text, size, max_w, max_lines=4):
        key = (text, round(size), round(max_w))
        hit = self._ly_layout.get(key)
        if hit:
            return hit
        if len(self._ly_layout) > 400:
            self._ly_layout.clear()
        lay = make_layout(text, size, True, max_w, max_lines)
        self._ly_layout[key] = lay
        return lay

    def _draw_lyrics(self, cr, w, h, x, radius):
        ly = self.lyrics
        if self._lm < 0.01 or not ly or not ly.lines:
            return
        theme = (self.track or {}).get('theme') or {}
        c1 = tuple(v / 255 for v in theme.get('text1', (255, 255, 255)))
        size = radius * 0.115
        line_h = size * 1.28
        gap = size * 0.55
        max_w = w - x - w * 0.03
        y_c = h * (0.455 + LYRICS_DROP / 2)
        half = h * 0.155
        y_top, y_bot = h * 0.325, h * (0.595 + LYRICS_DROP)       # zone libre entre l'artiste et la barre de progression

        # hauteur de chaque parole (lignes retournées à la ligne) et position de son centre
        wraps = [self._wrap(text, size, max_w) for _t, text in ly.lines]
        centers, acc = [], 0.0
        for wr in wraps:
            hgt = wr.get_pixel_size()[1]
            centers.append(acc + hgt / 2)
            acc += hgt + gap
        n = len(wraps)
        lp = min(max(self._line_pos, 0.0), n - 1.0) if n else 0.0
        i0 = int(lp)
        i1 = min(i0 + 1, n - 1)
        center_now = centers[i0] + (centers[i1] - centers[i0]) * (lp - i0)
        if self._line_pos < 0:                      # avant la première ligne : tout est en dessous
            center_now = centers[0] + self._line_pos * (line_h + gap)

        band = h * 0.045                            # largeur du fondu en haut (artiste) et en bas (barre)
        cr.save()
        for i, wr in enumerate(wraps):
            dy = centers[i] - center_now
            near = max(0.0, 1.0 - abs(i - self._line_pos))            # 1 = parole en cours
            scale = 0.74 + 0.26 * near
            hgt = wr.get_pixel_size()[1]
            y0 = y_c + dy - hgt * scale / 2                            # haut du bloc à l'écran
            if y0 + hgt * scale < y_top - band or y0 > y_bot + band:
                continue                                              # bloc entièrement hors zone
            base_alpha = (0.46 + 0.54 * near) * self._lm

            # Chaque ligne de texte s'efface selon SA position : quand la parole en cours occupe 3-4 lignes,
            # on voit quand même la fin de la précédente et le début de la suivante.
            it = wr.get_iter()
            while True:
                line = it.get_line_readonly()
                _ink, logical = it.get_line_extents()
                ly_top = y0 + logical.y / Pango.SCALE * scale
                ly_bot = ly_top + logical.height / Pango.SCALE * scale
                vis = min(1.0, max(0.0, (ly_top - (y_top - band)) / band),
                          max(0.0, ((y_bot + band) - ly_bot) / band))
                alpha = base_alpha * vis
                if alpha >= 0.02:
                    cr.save()
                    cr.translate(x + logical.x / Pango.SCALE * scale, y0 + it.get_baseline() / Pango.SCALE * scale)
                    cr.scale(scale, scale)
                    cr.set_source_rgba(*c1, alpha)
                    cr.move_to(0, 0)
                    PangoCairo.show_layout_line(cr, line)
                    cr.restore()
                if not it.next_line():
                    break
        cr.restore()

    def _draw_music(self, cr, w, h, with_bg=True):
        self._ly_step()
        if with_bg:
            self._draw_background(cr, w, h)

        radius = h * DISC_SCALE
        cx, cy = w * 0.33, h * 0.50

        self._draw_vinyls(cr, cx, cy, radius)
        self._draw_tonearm(cr, cx, cy, radius)
        self._draw_track_info(cr, w, h, cx + radius * 1.30, radius)
        self._draw_lyrics(cr, w, h, cx + radius * 1.30, radius)
        self._draw_progress(cr, w, h)
        if self.controls_visible:
            self._draw_controls(cr, w, h)

    # ── Fond clair teinté par la pochette ────────────────────────────────── #

    def _draw_background(self, cr, w, h):
        cr.set_source_rgb(*BG_BASE)
        cr.paint()
        if not self.bg_surface:
            return

        t   = self._anim.transition
        now = self.clock()
        key = (id(self.bg_surface), id(self.prev_bg_surface), w, h)
        stale = (self._bg_cache is None or key != self._bg_cache_key or t < 1.0
                 or now - self._bg_cache_t >= BG_CACHE_S)
        if stale:
            if self._bg_cache is None or self._bg_cache.get_width() != w or self._bg_cache.get_height() != h:
                self._bg_cache = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
            c = cairo.Context(self._bg_cache)
            c.set_source_rgb(*BG_BASE)
            c.paint()
            cur_base  = ((self.track or {}).get('theme') or {}).get('base')
            prev_base = (self.prev_theme or {}).get('base')
            if self.prev_bg_surface and t < 1.0:
                self._paint_layers(c, self.prev_bg_surface, prev_base, w, h, now, 1.0)
                self._paint_layers(c, self.bg_surface, cur_base, w, h, now, t)
            else:
                self._paint_layers(c, self.bg_surface, cur_base, w, h, now, 1.0)
            self._bg_cache_key = key
            self._bg_cache_refs = (self.bg_surface, self.prev_bg_surface)   # garde les ids valides
            self._bg_cache_t = now
        cr.set_source_surface(self._bg_cache, 0, 0)
        cr.paint()

    def _paint_layers(self, cr, art, base, w, h, now, alpha):
        """
        Fond animé façon Apple Music : la pochette (floutée/assombrie) est empilée en plusieurs
        couches qui tournent lentement à des vitesses différentes. Dessiné en basse résolution
        puis agrandi (lisse et peu coûteux).
        """
        ws = 256
        hs = max(1, round(ws * h / w))
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, ws, hs)
        c = cairo.Context(surf)
        base = base or (20, 20, 20)
        c.set_source_rgb(base[0] / 255, base[1] / 255, base[2] / 255)
        c.paint()

        sw, sh = art.get_width(), art.get_height()
        diag = math.hypot(ws, hs)
        for period, phase, scale, layer_alpha, dx, dy in BG_LAYERS:
            c.save()
            c.translate(ws / 2 + dx * ws * math.sin(now / 41 + phase), hs / 2 + dy * hs * math.cos(now / 53 + phase))
            c.rotate(phase + 2 * math.pi * now / period)
            side = diag * scale
            c.scale(side / sw, side / sh)
            c.translate(-sw / 2, -sh / 2)
            c.set_source_surface(art, 0, 0)
            pat = c.get_source()
            pat.set_filter(cairo.FILTER_BILINEAR)
            pat.set_extend(cairo.EXTEND_PAD)
            c.paint_with_alpha(layer_alpha)
            c.restore()

        cr.save()
        cr.scale(w / ws, h / hs)
        cr.set_source_surface(surf, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_BILINEAR)
        cr.paint_with_alpha(alpha)
        cr.restore()

    def needs_bg_redraw(self, min_interval=BG_CACHE_S) -> bool:
        """Le fond bouge en continu : demande un redraw (~30 fps) tant qu'un fond est affiché."""
        if not self.bg_surface:
            return False
        now = self.clock()
        if now - self._last_bg_redraw >= min_interval:
            self._last_bg_redraw = now
            return True
        return False

    # ── Vinyles ──────────────────────────────────────────────────────────── #

    def _draw_vinyls(self, cr, cx, cy, radius):
        if self._anim.vinyl_state == 'swapping':
            # Ancien sort à gauche (ease-in sur toute la durée)
            t_out  = self._anim.vinyl_off ** 3
            out_cx = cx - t_out * (cx + radius * 1.15)

            # Nouveau entre seulement après VINYL_IN_START de progression
            p     = max(0.0, (self._anim.vinyl_off - VINYL_IN_START) / (1.0 - VINYL_IN_START))
            t_in  = p ** 2
            in_cy = cy - (1.0 - t_in) * (cy + radius * 1.15)

            self._draw_vinyl(cr, out_cx, cy,    radius, self.prev_album_surface)
            self._draw_vinyl(cr, cx,     in_cy, radius, self.album_surface)
        else:
            self._draw_vinyl(cr, cx, cy, radius)

    def _draw_vinyl(self, cr, cx, cy, radius, art_surf=None):
        # Ombre portée douce
        for dx, dy, k, a in ((0.025, 0.05, 1.02, 0.16),):
            cr.arc(cx + radius * dx, cy + radius * dy, radius * k, 0, 2 * math.pi)
            cr.set_source_rgba(0, 0, 0, a)
            cr.fill()

        surf = art_surf if art_surf is not None else self.album_surface
        sprite, half = self._vinyl_sprite(radius, surf, self._spiral_params())
        cr.save()
        cr.translate(cx, cy)
        cr.rotate(math.radians(self._anim.rotation))
        cr.set_source_surface(sprite, -half, -half)
        cr.get_source().set_filter(cairo.FILTER_BILINEAR)
        cr.paint()
        cr.restore()

    def _spiral_params(self):
        """(phase0, durée) de la spirale : le sillon est régénéré à chaque nouveau morceau."""
        T = self._anim.spiral_T if self._anim.spiral_T > 0 else (self._anim.song_duration or 240.0)
        return (round(self._anim.spiral_phase0 % 360.0, 1), round(T))

    def _vinyl_sprite(self, radius, art_surf, spiral):
        """Face du disque (sillon en spirale, pochette...) dessinée une fois par morceau, puis tournée."""
        key = (id(art_surf), round(radius), spiral)
        hit = self._sprites.get(key)
        if hit:
            return hit[0], hit[1]
        half = int(radius) + 3
        sprite = cairo.ImageSurface(cairo.FORMAT_ARGB32, 2 * half, 2 * half)
        c = cairo.Context(sprite)
        c.translate(half, half)
        self._draw_vinyl_face(c, radius, art_surf, spiral)
        if len(self._sprites) >= 4:
            self._sprites.pop(next(iter(self._sprites)))
        # on garde une référence à la pochette : empêche la réutilisation de son id() par une autre surface
        self._sprites[key] = (sprite, half, art_surf)
        return sprite, half

    def _draw_vinyl_face(self, cr, radius, art_surf, spiral):
        label_r = radius * 0.44

        # Disque
        cr.arc(0, 0, radius, 0, 2 * math.pi)
        cr.set_source_rgb(12 / 255, 12 / 255, 13 / 255)
        cr.fill()

        # Liseré clair sur le bord : garde le disque lisible sur les fonds très sombres
        cr.arc(0, 0, radius - 1.5, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 0.16)
        cr.set_line_width(3)
        cr.stroke()

        # Sillon en spirale : le disque tourne à vitesse constante et l'aiguille descend au même
        # rythme, donc le sillon qu'elle suit est une spirale dont le pas dépend de la durée du morceau.
        phase0, T = spiral
        total_deg = SPIN_DEG_PER_S * T
        turns = total_deg / 360.0
        r_out, r_in = radius * GROOVE_OUT, radius * GROOVE_IN
        pitch = (r_out - r_in) / max(turns, 1.0)
        n = int(min(7000, max(240, total_deg / 6)))
        phi = math.degrees(NEEDLE_ANG)
        cr.new_path()
        for i in range(n + 1):
            u = i / n
            ang = math.radians(phi - phase0 - total_deg * u)
            r = r_out - (r_out - r_in) * u
            (cr.move_to if i == 0 else cr.line_to)(r * math.cos(ang), r * math.sin(ang))
        cr.set_source_rgba(1, 1, 1, 0.20 if pitch > 2.5 else 0.12)
        cr.set_line_width(max(0.7, min(1.6, pitch * 0.4)))
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()

        # Anneau sombre autour de la pochette
        cr.arc(0, 0, label_r + radius * 0.03, 0, 2 * math.pi)
        cr.set_source_rgb(30 / 255, 30 / 255, 33 / 255)
        cr.set_line_width(radius * 0.05)
        cr.stroke()
        cr.arc(0, 0, label_r + radius * 0.057, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 0.23)
        cr.set_line_width(1.6)
        cr.stroke()

        # Pochette rognée en cercle
        cr.save()
        cr.arc(0, 0, label_r, 0, 2 * math.pi)
        cr.clip()
        if art_surf:
            blit_disc(cr, art_surf, label_r)
        else:
            cr.set_source_rgb(96 / 255, 70 / 255, 190 / 255)
            cr.paint()
        cr.restore()

        # Trou central
        cr.arc(0, 0, radius * 0.018, 0, 2 * math.pi)
        cr.set_source_rgb(240 / 255, 240 / 255, 244 / 255)
        cr.fill()


    # ── Bras de lecture blanc ────────────────────────────────────────────── #

    def _draw_tonearm(self, cr, cx, cy, radius):
        px = cx + radius * 0.95
        py = cy - radius * 0.72

        # Rayon de contact du stylet : du bord extérieur du sillon vers la pochette
        r_outer = radius * GROOVE_OUT
        r_inner = radius * GROOVE_IN
        progress = self._anim.get_needle_progress()
        needle_r = r_outer - progress * (r_outer - r_inner)

        # Géométrie du bras : le stylet est décalé du bout de la tige (cellule inclinée). On résout
        # pour que le STYLET, pas la tige, touche le disque au rayon voulu.
        r_mid = (r_outer + r_inner) / 2
        s_len = radius * STYLUS_LEN
        tilt  = math.radians(STYLUS_TILT)
        leff  = math.hypot(cx + r_mid * math.cos(NEEDLE_ANG) - px, cy + r_mid * math.sin(NEEDLE_ANG) - py)
        arm_len = -s_len * math.cos(tilt) + math.sqrt(leff ** 2 - (s_len * math.sin(tilt)) ** 2)
        gamma   = math.atan2(s_len * math.sin(tilt), arm_len + s_len * math.cos(tilt))

        play_angle = arm_angle_for(px, py, cx, cy, needle_r, leff) - gamma
        rest_angle = play_angle - 0.36          # pivote vers l'extérieur du disque
        lift_t     = self._anim.arm_offset / ARM_MAX_LIFT
        angle      = play_angle + lift_t * (rest_angle - play_angle)

        tip_x = px + arm_len * math.cos(angle)
        tip_y = py + arm_len * math.sin(angle)
        lift  = lift_t * radius * 0.03

        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        # Base ronde translucide
        base_r = radius * 0.17
        cr.arc(px, py, base_r, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 0.37)
        cr.fill_preserve()
        cr.set_source_rgba(150 / 255, 150 / 255, 165 / 255, 0.27)
        cr.set_line_width(2)
        cr.stroke()

        # Ombre du bras
        cr.set_source_rgba(0, 0, 0, 0.20)
        cr.set_line_width(radius * 0.028)
        cr.move_to(px + 4 + lift, py + 6 + lift)
        cr.line_to(tip_x + 4 + lift, tip_y + 6 + lift)
        cr.stroke()

        # Tige blanche/argent
        cr.set_source_rgb(236 / 255, 236 / 255, 240 / 255)
        cr.set_line_width(radius * 0.026)
        cr.move_to(px, py)
        cr.line_to(tip_x, tip_y)
        cr.stroke()
        cr.set_source_rgba(150 / 255, 150 / 255, 165 / 255, 0.35)
        cr.set_line_width(radius * 0.006)
        cr.move_to(px, py)
        cr.line_to(tip_x, tip_y)
        cr.stroke()

        # Bloc blanc au pivot
        cr.save()
        cr.translate(px, py)
        cr.rotate(angle - math.pi / 2)
        rounded_rect(cr, -radius * 0.075, -radius * 0.10, radius * 0.15, radius * 0.16, radius * 0.02)
        cr.set_source_rgb(248 / 255, 248 / 255, 250 / 255)
        cr.fill_preserve()
        cr.set_source_rgba(150 / 255, 150 / 255, 165 / 255, 0.27)
        cr.set_line_width(1.5)
        cr.stroke()
        cr.restore()

        # Cellule (headshell) blanche, légèrement inclinée + stylet
        cr.save()
        cr.translate(tip_x, tip_y)
        cr.rotate(angle - math.pi / 2 + tilt)
        rounded_rect(cr, -radius * 0.032, -radius * 0.01, radius * 0.064, radius * 0.125, radius * 0.012)
        cr.set_source_rgb(250 / 255, 250 / 255, 252 / 255)
        cr.fill_preserve()
        cr.set_source_rgba(150 / 255, 150 / 255, 165 / 255, 0.31)
        cr.set_line_width(1.5)
        cr.stroke()
        cr.set_source_rgb(190 / 255, 190 / 255, 200 / 255)
        cr.set_line_width(radius * 0.008)
        cr.move_to(0, radius * 0.115)
        cr.line_to(0, radius * 0.14)
        cr.stroke()
        cr.restore()

        cr.restore()

    # ── Infos piste ──────────────────────────────────────────────────────── #

    def _one_line(self, text, size, bold):
        key = ('1l', text, round(size), bold)
        lay = self._ly_layout.get(key)
        if lay is None:
            if len(self._ly_layout) > 400:
                self._ly_layout.clear()
            lay = self._ly_layout[key] = make_layout(text, size, bold)
        return lay

    def _draw_track_info(self, cr, w, h, x, radius):
        title  = self.track.get('title', '')
        artist = self.track.get('artist', '')
        if not title:
            return

        y     = h * (0.50 - 0.29 * self._lm)          # remonte quand les paroles s'affichent
        max_w = min(w * 0.30, w - x - w * 0.04)

        def draw_text(text, size, bold, y_off, rgb):
            sz = size
            lay = self._one_line(text, sz, bold)
            while lay.get_pixel_size()[0] > max_w and sz > size * 0.5:      # réduit la police pour tenir
                sz -= 1
                lay = self._one_line(text, sz, bold)
            cr.set_source_rgb(*rgb)
            cr.move_to(x, y + y_off - lay.get_baseline() / Pango.SCALE)       # ancré sur la ligne de base
            PangoCairo.show_layout(cr, lay)

        theme = self.track.get('theme') or {}
        c1 = tuple(v / 255 for v in theme.get('text1', (28, 28, 32)))
        c2 = tuple(v / 255 for v in theme.get('text2', (110, 110, 120)))
        draw_text(title, radius * 0.16, True, 0, c1)
        if artist:
            draw_text(artist, radius * 0.09, False, radius * 0.17, c2)
