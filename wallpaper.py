#!/usr/bin/python3
import cairo
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gtk4LayerShell', '1.0')
from gi.repository import Gtk, Gdk, Gtk4LayerShell, GLib

import json
import os
import socket
import subprocess
import threading
import time

from constants import SOCKET_PATH, FRAME_MS
from desktop_watch import DesktopWatcher
from music_player import MusicChannel

PREFERRED_CONNECTOR = 'DP-2'    # écran du player sur defcon1 ; surchargeable via ~/.config/player/settings.json ("monitor")


def resolve_connector() -> str:
    """Écran cible : celui des réglages, sinon DP-2 s'il existe, sinon le premier écran (ex. eDP-1 du portable)."""
    want = PREFERRED_CONNECTOR
    try:
        want = json.load(open(os.path.join(os.path.expanduser('~'), '.config', 'player', 'settings.json'))).get('monitor', want)
    except Exception:
        pass
    names = []
    monitors = Gdk.Display.get_default().get_monitors()
    for i in range(monitors.get_n_items()):
        names.append(monitors.get_item(i).get_connector())
    if want in names:
        return want
    return names[0] if names else want


TARGET_CONNECTOR = None          # résolu au démarrage (voir resolve_connector)
FG_INTERVAL_S = 0.033   # disque/bras/texte : ~30 fps (le disque tourne à 33⅓ tr/min)
BG_INTERVAL_S = 0.100   # fond animé (basse résolution) : ~10 fps


class WallpaperWindow(Gtk.Window):
    """
    Fenêtre GTK4 background (layer-shell) : boucle de tick, rendu, et serveur
    socket IPC. Ne fait aucun rendu elle-même — délègue au canal musique (MusicChannel).
    """

    def __init__(self):
        super().__init__()
        self.set_decorated(False)

        Gtk4LayerShell.init_for_window(self)
        Gtk4LayerShell.set_namespace(self, 'player-wallpaper')
        Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.BACKGROUND)
        Gtk4LayerShell.set_exclusive_zone(self, -1)
        Gtk4LayerShell.set_keyboard_mode(self, Gtk4LayerShell.KeyboardMode.NONE)
        for edge in (Gtk4LayerShell.Edge.TOP, Gtk4LayerShell.Edge.BOTTOM,
                     Gtk4LayerShell.Edge.LEFT, Gtk4LayerShell.Edge.RIGHT):
            Gtk4LayerShell.set_anchor(self, edge, True)

        global TARGET_CONNECTOR
        TARGET_CONNECTOR = resolve_connector()
        print(f'[wallpaper] écran cible : {TARGET_CONNECTOR}', flush=True)
        monitor = self._find_monitor(TARGET_CONNECTOR)
        if monitor is not None:
            Gtk4LayerShell.set_monitor(self, monitor)
        else:
            print(f'[wallpaper] moniteur {TARGET_CONNECTOR} introuvable, '
                  'fallback sur le choix par defaut du compositeur')

        # Rendu : on dessine nous-mêmes dans des surfaces Cairo puis on les envoie à GTK comme
        # textures (Gtk.Picture) — bien moins coûteux qu'un DrawingArea plein écran.
        #   - fond animé : petite texture (256 px de large, ~10 fps), agrandie par le GPU
        #   - avant-plan : disque, bras, texte, en pleine résolution sur fond transparent (~30 fps)
        self._bg_pic = Gtk.Picture()
        self._bg_pic.set_content_fit(Gtk.ContentFit.FILL)
        self._bg_pic.set_can_shrink(True)
        self._pic = Gtk.Picture()
        self._pic.set_content_fit(Gtk.ContentFit.FILL)
        self._pic.set_can_shrink(True)
        overlay = Gtk.Overlay()
        overlay.set_child(self._bg_pic)
        overlay.add_overlay(self._pic)
        self.set_child(overlay)
        self._surface = None
        self._last_bg = 0.0

        self._music = MusicChannel(on_redraw=self._request_redraw)

        # Boutons de contrôle : visibles uniquement si aucune fenêtre ne couvre ce bureau
        self._watcher = DesktopWatcher(TARGET_CONNECTOR, self._on_desktop_change)
        self._watcher.start()
        motion = Gtk.EventControllerMotion()
        motion.connect('motion', self._on_motion)
        motion.connect('leave', self._on_leave)
        self.add_controller(motion)
        click = Gtk.GestureClick()
        click.connect('released', self._on_click)
        self.add_controller(click)

        self._pending_draw = False
        self._last_draw = 0.0
        GLib.timeout_add(FRAME_MS, self._tick)
        threading.Thread(target=self._run_server, daemon=True).start()

    @staticmethod
    def _find_monitor(connector: str):
        monitors = Gdk.Display.get_default().get_monitors()
        for i in range(monitors.get_n_items()):
            m = monitors.get_item(i)
            if m.get_connector() == connector:
                return m
        return None

    # ── Tick / rendu ─────────────────────────────────────────────────────── #

    def _request_redraw(self):
        self._pending_draw = True

    # ── Boutons de contrôle ──────────────────────────────────────────────── #

    def _on_desktop_change(self, covered: bool, fullscreen: bool):
        print(f'[desktop] bureau {"couvert" if covered else "libre"} -> boutons {"masqués" if covered else "visibles"}', flush=True)
        renderer = self._music.renderer
        renderer.controls_visible = not covered
        if covered:
            renderer.hover = None
            self.set_cursor(None)
        self._request_redraw()

    def _button_at(self, x, y):
        w, h = self._pic.get_width(), self._pic.get_height()
        return self._music.renderer.control_at(x, y, w, h) if w > 0 and h > 0 else None

    def _on_motion(self, _ctrl, x, y):
        renderer = self._music.renderer
        if not renderer.controls_visible:
            return
        hit = self._button_at(x, y)
        if hit != renderer.hover:
            renderer.hover = hit
            self.set_cursor(Gdk.Cursor.new_from_name('pointer') if hit else None)
            self._request_redraw()

    def _on_leave(self, _ctrl):
        renderer = self._music.renderer
        if renderer.hover:
            renderer.hover = None
            self.set_cursor(None)
            self._request_redraw()

    def _seek(self, frac):
        """Va à la fraction `frac` du morceau (playerctl position) et recale l'animation."""
        anim = self._music._anim
        if anim.song_duration <= 0:
            return
        sec = frac * anim.song_duration
        subprocess.Popen(['playerctl', '-p', 'sidra', 'position', f'{sec:.2f}'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if anim.is_playing:
            anim.set_playing(sec)
        else:
            anim.frozen_position_s = sec
        anim.reset_spiral()          # l'aiguille se repose sur le sillon à la nouvelle position
        self._request_redraw()

    def _on_click(self, _gesture, _n_press, x, y):
        hit = self._button_at(x, y)
        if hit == 'bar':
            self._seek(self._music.renderer.bar_fraction(x, self._pic.get_width(), self._pic.get_height()))
        elif hit == 'lyrics':
            self._music.toggle_lyrics()
        elif hit:
            cmd = {'previous': 'previous', 'play_pause': 'play-pause', 'next': 'next'}[hit]
            subprocess.Popen(['playerctl', '-p', 'sidra', cmd],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @staticmethod
    def _texture(surface, w, h):
        surface.flush()
        return Gdk.MemoryTexture.new(
            w, h, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
            GLib.Bytes.new(bytes(surface.get_data())), surface.get_stride())

    def _render_bg_only(self):
        """Le disque est à l'arrêt : seul le fond dérive, on ne renvoie pas l'avant-plan à GTK."""
        w, h = self._pic.get_width(), self._pic.get_height()
        renderer = self._music.renderer
        if w <= 0 or h <= 0 or not renderer.layered():
            return
        self._last_bg = time.monotonic()
        small = renderer.render_bg_small(w, h)
        self._bg_pic.set_paintable(self._texture(small, small.get_width(), small.get_height()))

    def _render(self):
        w, h = self._pic.get_width(), self._pic.get_height()
        if w <= 0 or h <= 0:
            return
        if self._surface is None or self._surface.get_width() != w or self._surface.get_height() != h:
            self._surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(self._surface)
        # avant-plan : on repart d'une surface transparente
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        renderer = self._music.renderer
        if renderer.layered():
            now = time.monotonic()
            if now - self._last_bg >= BG_INTERVAL_S or renderer._anim.transition < 1.0:
                self._last_bg = now
                small = renderer.render_bg_small(w, h)
                self._bg_pic.set_paintable(self._texture(small, small.get_width(), small.get_height()))
            renderer.draw_fg(cr, w, h)
        else:
            self._bg_pic.set_paintable(None)
            self._draw(None, cr, w, h)
        self._pic.set_paintable(self._texture(self._surface, w, h))

    def _tick(self) -> bool:
        redraw = self._music.tick()
        self._pending_draw = self._pending_draw or redraw
        now = time.monotonic()
        # Les animations avancent à chaque tick (16 ms) mais on ne redessine l'avant-plan qu'à ~24 fps (CPU)
        if self._pending_draw and now - self._last_draw >= FG_INTERVAL_S:
            self._pending_draw = False
            self._last_draw = now
            self._render()
        elif now - self._last_bg >= BG_INTERVAL_S:
            self._render_bg_only()      # fond animé seul, ~10 fps
        return True

    def _draw(self, _area, cr, width, height):
        self._music.draw(cr, width, height)

    # ── Serveur IPC (socket Unix, alimenté par monitor.py) ─────────────────── #

    def _run_server(self):
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(SOCKET_PATH)
        srv.listen(5)
        while True:
            conn, _ = srv.accept()
            try:
                data = b''
                while chunk := conn.recv(4096):
                    data += chunk
                GLib.idle_add(self._dispatch_message, data.decode())
            except Exception as e:
                print(f'[wallpaper] socket: {e}')
            finally:
                conn.close()

    def _dispatch_message(self, raw: str):
        self._music.handle_message(json.loads(raw))
        return False


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    win = WallpaperWindow()
    win.present()
    GLib.MainLoop().run()
