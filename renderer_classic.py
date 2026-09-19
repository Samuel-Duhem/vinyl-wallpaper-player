import math

import cairo

from constants import ARM_MAX_LIFT, VINYL_IN_START


def blit_surface(cr: cairo.Context, surface: cairo.ImageSurface, w: float, h: float):
    """Étire/centre une surface pour couvrir toute la zone (cover)."""
    sw, sh = surface.get_width(), surface.get_height()
    scale  = max(w / sw, h / sh)
    cr.save()
    cr.translate((w - sw * scale) / 2, (h - sh * scale) / 2)
    cr.scale(scale, scale)
    cr.set_source_surface(surface, 0, 0)
    cr.paint()
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


class Renderer:
    """
    Rendu Cairo du player vinyle.

    Reçoit l'état d'animation depuis AnimEngine (référence) et les surfaces
    Cairo (attributs publics mis à jour par WallpaperWindow).
    Point d'entrée : draw(cr, width, height) branché sur le draw_func GTK4.
    """

    def __init__(self, anim):
        self._anim = anim

        # Surfaces (mises à jour par WallpaperWindow._do_apply_surfaces)
        self.album_surface      = None
        self.bg_surface         = None
        self.prev_album_surface = None
        self.prev_bg_surface    = None
        self.default_surface    = None

        self.track = {}

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
            cr.set_source_rgb(0.08, 0.08, 0.12)
            cr.paint()

    def _draw_music(self, cr, w, h):
        self._draw_background(cr, w, h)

        cx, cy = w / 2, h / 2
        radius = min(w, h) * 0.34

        self._draw_motor(cr, cx, cy, radius)
        self._draw_vinyls(cr, cx, cy, radius)
        self._draw_tonearm(cr, cx, cy, radius)
        self._draw_track_info(cr, w, h)

    # ── Fond flouté ──────────────────────────────────────────────────────── #

    def _draw_background(self, cr, w, h):
        t = self._anim.transition
        if self.prev_bg_surface and t < 1.0:
            blit_surface(cr, self.prev_bg_surface, w, h)
            cr.push_group()
            if self.bg_surface:
                blit_surface(cr, self.bg_surface, w, h)
            cr.pop_group_to_source()
            cr.paint_with_alpha(t)
        elif self.bg_surface:
            blit_surface(cr, self.bg_surface, w, h)
        elif self.default_surface:
            blit_surface(cr, self.default_surface, w, h)
        else:
            cr.set_source_rgb(0.05, 0.05, 0.10)
            cr.paint()

    # ── Vinyles ──────────────────────────────────────────────────────────── #

    def _draw_vinyls(self, cr, cx, cy, radius):
        if self._anim.vinyl_state == 'swapping':
            # Ancien sort à gauche (ease-in sur toute la durée)
            t_out  = self._anim.vinyl_off ** 3
            out_cx = cx - t_out * (cx + radius * 1.6)

            # Nouveau entre seulement après VINYL_IN_START de progression
            p    = max(0.0, (self._anim.vinyl_off - VINYL_IN_START) / (1.0 - VINYL_IN_START))
            t_in  = p ** 2   # ease-in
            in_cy = cy - (1.0 - t_in) * (cy + radius * 1.6)

            self._draw_vinyl(cr, out_cx, cy,    radius, self.prev_album_surface)
            self._draw_vinyl(cr, cx,     in_cy, radius, self.album_surface)
        else:
            self._draw_vinyl(cr, cx, cy, radius)

    def _draw_vinyl(self, cr, cx, cy, radius, art_surf=None):
        label_r      = radius * 0.28
        groove_start = label_r + radius * 0.05
        groove_end   = radius * 0.96

        cr.save()
        cr.translate(cx, cy)
        cr.rotate(math.radians(self._anim.rotation))

        # Disque
        cr.arc(0, 0, radius, 0, 2 * math.pi)
        cr.set_source_rgb(0.06, 0.06, 0.08)
        cr.fill()

        # Rainures concentriques
        n = 90
        for i in range(n):
            r = groove_start + (i / n) * (groove_end - groove_start)
            if i % 5 == 0:
                alpha, lw = 0.22, 0.9
            elif i % 2 == 0:
                alpha, lw = 0.10, 0.5
            else:
                alpha, lw = 0.05, 0.4
            cr.arc(0, 0, r, 0, 2 * math.pi)
            cr.set_source_rgba(0, 0, 0, alpha)
            cr.set_line_width(lw)
            cr.stroke()

        # Bordure label/rainures
        cr.arc(0, 0, groove_start, 0, 2 * math.pi)
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.set_line_width(3)
        cr.stroke()

        # Label central — art direct, pas de crossfade
        cr.save()
        cr.arc(0, 0, label_r, 0, 2 * math.pi)
        cr.clip()
        surf = art_surf if art_surf is not None else self.album_surface
        if surf:
            blit_disc(cr, surf, label_r)
        else:
            cr.set_source_rgb(0.18, 0.09, 0.14)
            cr.paint()
        cr.restore()

        # Bordure label
        cr.arc(0, 0, label_r, 0, 2 * math.pi)
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.set_line_width(2)
        cr.stroke()

        # Reflet sweep
        grad = cairo.LinearGradient(-radius * 0.55, -radius * 0.70,
                                     radius * 0.40,  radius * 0.30)
        grad.add_color_stop_rgba(0.0, 1, 1, 1, 0.32)
        grad.add_color_stop_rgba(0.6, 1, 1, 1, 0.04)
        grad.add_color_stop_rgba(1.0, 1, 1, 1, 0.0)
        cr.arc(0, 0, radius, 0, 2 * math.pi)
        cr.set_source(grad)
        cr.fill()

        # Reflet spéculaire concentré
        spot = cairo.RadialGradient(-radius * 0.42, -radius * 0.52, 0,
                                    -radius * 0.42, -radius * 0.52, radius * 0.19)
        spot.add_color_stop_rgba(0.0, 1, 1, 1, 0.30)
        spot.add_color_stop_rgba(0.5, 1, 1, 1, 0.06)
        spot.add_color_stop_rgba(1.0, 1, 1, 1, 0.0)
        cr.arc(0, 0, radius, 0, 2 * math.pi)
        cr.set_source(spot)
        cr.fill()

        # Rim light bleuté
        cr.arc(0, 0, radius - 1.0, 0, 2 * math.pi)
        cr.set_source_rgba(0.55, 0.62, 0.85, 0.50)
        cr.set_line_width(2.0)
        cr.stroke()

        # Trou central
        cr.arc(0, 0, radius * 0.016, 0, 2 * math.pi)
        cr.set_source_rgb(0.84, 0.84, 0.88)
        cr.fill()

        cr.restore()

    # ── Moteur + courroie ────────────────────────────────────────────────── #

    def _draw_motor(self, cr, cx, cy, radius):
        mr = radius * 0.052
        mx = cx - radius * 1.05
        my = cy + radius * 1.10

        gear_ratio = (radius / mr) * 0.18
        motor_rot  = (-self._anim.rotation * gear_ratio) % 360

        dx, dy = mx - cx, my - cy
        dist   = math.hypot(dx, dy)
        ux, uy = dx / dist, dy / dist
        px, py = -uy, ux

        # Ombre courroie
        cr.set_source_rgba(0, 0, 0, 0.35)
        cr.set_line_width(5)
        for sign in (1, -1):
            cr.move_to(cx + sign * px * radius, cy + sign * py * radius)
            cr.line_to(mx + sign * px * mr,     my + sign * py * mr)
            cr.stroke()

        # Courroie
        cr.set_source_rgb(0.13, 0.10, 0.08)
        cr.set_line_width(3.5)
        for sign in (1, -1):
            cr.move_to(cx + sign * px * radius, cy + sign * py * radius)
            cr.line_to(mx + sign * px * mr,     my + sign * py * mr)
            cr.stroke()

        # Pignon cranté
        cr.save()
        cr.translate(mx, my)
        cr.rotate(math.radians(motor_rot))

        n_teeth, r_root, r_tip, tooth_w = 14, mr * 0.84, mr * 1.22, 0.13
        cr.new_path()
        for i in range(n_teeth):
            a   = i * 2 * math.pi / n_teeth
            nxt = (i + 1) * 2 * math.pi / n_teeth
            mid = (a + nxt) / 2
            cr.arc(0, 0, r_root, a, mid - tooth_w)
            cr.line_to(r_tip * math.cos(mid - tooth_w), r_tip * math.sin(mid - tooth_w))
            cr.arc(0, 0, r_tip, mid - tooth_w, mid + tooth_w)
            cr.line_to(r_root * math.cos(mid + tooth_w), r_root * math.sin(mid + tooth_w))
        cr.close_path()
        cr.set_source_rgb(0.38, 0.38, 0.44)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.5)
        cr.set_line_width(1)
        cr.stroke()

        for i in range(4):
            a = i * math.pi / 4
            cr.move_to(r_root * 0.6 * math.cos(a),           r_root * 0.6 * math.sin(a))
            cr.line_to(r_root * 0.6 * math.cos(a + math.pi), r_root * 0.6 * math.sin(a + math.pi))
        cr.set_source_rgba(0, 0, 0, 0.3)
        cr.set_line_width(1.5)
        cr.stroke()

        cr.arc(0, 0, mr * 0.38, 0, 2 * math.pi)
        cr.set_source_rgb(0.58, 0.58, 0.64)
        cr.fill()
        cr.arc(0, 0, mr * 0.14, 0, 2 * math.pi)
        cr.set_source_rgb(0.28, 0.28, 0.32)
        cr.fill()

        cr.restore()

    # ── Bras de lecture ──────────────────────────────────────────────────── #

    def _draw_tonearm(self, cr, cx, cy, radius):
        px = cx + radius * 1.22
        py = cy - radius * 0.62

        progress   = self._anim.get_needle_progress()
        groove_end = radius * 0.95
        groove_in  = radius * 0.33
        needle_r   = groove_end - progress * (groove_end - groove_in)
        needle_x   = cx + needle_r

        rest_angle = math.pi / 2
        play_angle = math.atan2(cy - py, needle_x - px)
        lift_t     = self._anim.arm_offset / ARM_MAX_LIFT
        arm_angle  = play_angle + lift_t * (rest_angle - play_angle)
        arm_len    = math.hypot(needle_x - px, cy - py) + radius * 0.06

        tip_x = px + arm_len * math.cos(arm_angle)
        tip_y = py + arm_len * math.sin(arm_angle)

        cr.save()

        # Ombre
        cr.set_source_rgba(0, 0, 0, 0.28)
        cr.set_line_width(6)
        cr.move_to(px + 2, py + 3)
        cr.line_to(tip_x + 2, tip_y + 3)
        cr.stroke()

        # Bras (dégradé métal)
        lg = cairo.LinearGradient(px, py, tip_x, tip_y)
        lg.add_color_stop_rgb(0.0, 0.82, 0.82, 0.87)
        lg.add_color_stop_rgb(0.5, 0.66, 0.66, 0.72)
        lg.add_color_stop_rgb(1.0, 0.50, 0.50, 0.56)
        cr.set_source(lg)
        cr.set_line_width(4.5)
        cr.move_to(px, py)
        cr.line_to(tip_x, tip_y)
        cr.stroke()

        # Headshell
        hs_angle = arm_angle + math.pi * 0.28
        hs_len   = radius * 0.08
        hs_x     = tip_x + hs_len * math.cos(hs_angle)
        hs_y     = tip_y + hs_len * math.sin(hs_angle)
        cr.set_source_rgb(0.28, 0.28, 0.32)
        cr.set_line_width(3.5)
        cr.move_to(tip_x, tip_y)
        cr.line_to(hs_x, hs_y)
        cr.stroke()

        # Stylet cyan (visible seulement quand posé)
        if lift_t < 0.25:
            alpha      = 1.0 - lift_t / 0.25
            needle_len = radius * 0.032
            n_x = hs_x + needle_len * math.cos(hs_angle)
            n_y = hs_y + needle_len * math.sin(hs_angle)
            cr.set_source_rgba(0.0, 0.88, 0.94, alpha)
            cr.set_line_width(1.8)
            cr.move_to(hs_x, hs_y)
            cr.line_to(n_x, n_y)
            cr.stroke()
            cr.arc(n_x, n_y, radius * 0.010, 0, 2 * math.pi)
            cr.set_source_rgba(0.6, 1.0, 1.0, alpha * 0.9)
            cr.fill()

        # Socle du pivot
        rg = cairo.RadialGradient(px - radius * 0.012, py - radius * 0.012, 0,
                                   px, py, radius * 0.046)
        rg.add_color_stop_rgb(0, 0.90, 0.90, 0.94)
        rg.add_color_stop_rgb(1, 0.40, 0.40, 0.46)
        cr.arc(px, py, radius * 0.046, 0, 2 * math.pi)
        cr.set_source(rg)
        cr.fill()
        cr.arc(px, py, radius * 0.046, 0, 2 * math.pi)
        cr.set_source_rgba(0, 0, 0, 0.50)
        cr.set_line_width(1.5)
        cr.stroke()
        cr.arc(px, py, radius * 0.014, 0, 2 * math.pi)
        cr.set_source_rgb(0.22, 0.22, 0.26)
        cr.fill()

        cr.restore()

    # ── Infos piste ──────────────────────────────────────────────────────── #

    def _draw_track_info(self, cr, w, h):
        title  = self.track.get('title', '')
        artist = self.track.get('artist', '')
        if not title:
            return

        y = h * 0.83

        def draw_text(text, size, bold, y_off, alpha):
            weight = cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL
            cr.select_font_face('Sans', cairo.FONT_SLANT_NORMAL, weight)
            cr.set_font_size(size)
            ext = cr.text_extents(text)
            x   = (w - ext.width) / 2 - ext.x_bearing
            cr.set_source_rgba(0, 0, 0, 0.55)
            cr.move_to(x + 2, y + y_off + 2)
            cr.show_text(text)
            cr.set_source_rgba(1, 1, 1, alpha)
            cr.move_to(x, y + y_off)
            cr.show_text(text)

        draw_text(title,  30, True,  0,  0.95)
        if artist:
            draw_text(artist, 20, False, 40, 0.72)
