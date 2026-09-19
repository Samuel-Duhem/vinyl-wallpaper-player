import json
import os
import subprocess
import threading

from PIL import Image
from gi.repository import GLib

from constants import DEFAULT_WALLPAPER, LAST_TRACK_FILE
from album_db import AlbumDB
from art_loader import ArtLoader, pil_to_cairo
from anim_engine import AnimEngine
from lyrics import Lyrics
from renderer import Renderer


class MusicChannel:
    """
    Canal "musique" : scène tourne-disque pilotée par les métadonnées MPRIS
    (Sidra via monitor.py).

    Interface attendue par l'orchestrateur (wallpaper.py) :
      tick() -> bool             redraw nécessaire ?
      draw(cr, w, h)
      handle_message(msg: dict)  statut/piste reçus du socket IPC
    """

    def __init__(self, on_redraw=None):
        self._on_redraw = on_redraw

        self._db   = AlbumDB()
        self._art  = ArtLoader(self._db, on_ready=self._on_art_ready)
        self._anim = AnimEngine()
        self._anim.on_apply_surfaces = self._do_apply_surfaces
        self.renderer = Renderer(self._anim)
        self.lyrics = Lyrics(on_update=self._request_redraw)
        self.renderer.lyrics = self.lyrics
        self.renderer.lyrics_enabled = self._load_setting('lyrics', True)

        self._track = {}

        self._load_default()
        self._load_last_track()

    # ── Chargement surfaces ──────────────────────────────────────────────── #

    def _load_default(self):
        if os.path.exists(DEFAULT_WALLPAPER):
            try:
                self.renderer.default_surface = pil_to_cairo(
                    Image.open(DEFAULT_WALLPAPER).convert('RGBA'))
            except Exception as e:
                print(f'[music] default: {e}')

    def _load_last_track(self):
        if os.path.exists(LAST_TRACK_FILE):
            try:
                with open(LAST_TRACK_FILE) as f:
                    track = json.load(f)
                track['status'] = 'Stopped'
                if track.get('art_url'):
                    self._track         = track
                    self.renderer.track = track
                    self._art.load(track)
            except Exception as e:
                print(f'[music] last_track: {e}')

    def _on_art_ready(self, album_surf, bg_surf, track, is_new_album):
        if is_new_album and self.renderer.album_surface:
            self._anim.request_swap(album_surf, bg_surf, track)
        elif self.renderer.album_surface and track.get('title') != self.renderer.track.get('title'):
            self._anim.request_hop(album_surf, bg_surf, track)      # même album, autre morceau
        else:
            first = not self.renderer.album_surface
            self._anim.request_apply(album_surf, bg_surf, track)
            if first:
                self._anim.reset_spiral()

    def _do_apply_surfaces(self, album_surf, bg_surf, track=None):
        self.renderer.prev_bg_surface    = self.renderer.bg_surface
        self.renderer.prev_theme         = (self.renderer.track or {}).get('theme')
        self.renderer.prev_album_surface = self.renderer.album_surface
        self.renderer.album_surface      = album_surf
        self.renderer.bg_surface         = bg_surf
        self._anim.start_transition()
        if track:
            self._track          = track
            self.renderer.track  = track
            try:
                with open(LAST_TRACK_FILE, 'w') as f:
                    json.dump(track, f)
            except Exception:
                pass
        self._request_redraw()

    def _request_redraw(self):
        if self._on_redraw:
            self._on_redraw()

    # ── Messages IPC (depuis monitor.py) ──────────────────────────────────── #

    # ── Réglages utilisateur ────────────────────────────────────────────────── #

    SETTINGS_FILE = os.path.join(os.path.expanduser('~'), '.config', 'player', 'settings.json')

    def _load_setting(self, key, default):
        try:
            return json.load(open(self.SETTINGS_FILE)).get(key, default)
        except Exception:
            return default

    def _save_setting(self, key, value):
        try:
            os.makedirs(os.path.dirname(self.SETTINGS_FILE), exist_ok=True)
            try:
                data = json.load(open(self.SETTINGS_FILE))
            except Exception:
                data = {}
            data[key] = value
            json.dump(data, open(self.SETTINGS_FILE, 'w'), indent=2)
        except Exception as e:
            print(f'[music] réglages: {e}')

    def toggle_lyrics(self):
        self.renderer.lyrics_enabled = not self.renderer.lyrics_enabled
        self._save_setting('lyrics', self.renderer.lyrics_enabled)
        print(f'[music] paroles {"activées" if self.renderer.lyrics_enabled else "désactivées"}', flush=True)
        self._request_redraw()

    def handle_message(self, msg: dict):
        if msg.get('cmd') == 'toggle_lyrics':      # envoyé par toggle-lyrics.sh (raccourci clavier)
            self.toggle_lyrics()
            return
        status = msg.get('status', '')

        if 'length' in msg and msg['length'] > 0:
            self._anim.song_duration = msg['length'] / 1_000_000

        if status in ('Playing', 'Paused'):
            self.lyrics.request(msg.get('title', ''), msg.get('artist', ''), msg.get('album', ''),
                                msg.get('length', 0) / 1_000_000)
            track_changed = (msg.get('title')  != self._track.get('title') or
                             msg.get('artist') != self._track.get('artist'))

            if track_changed:
                self._anim.on_track_change()
                self._track = msg
                self._art.load(msg)
            elif not self.renderer.album_surface and not self._art.is_loading:
                self._track = msg
                self._art.load(msg)

            if status == 'Playing':
                threading.Thread(target=self._fetch_position, daemon=True).start()
            else:
                self._anim.set_paused()
        else:
            self._anim.set_stopped()
            self._art.reset_hash()

        self._request_redraw()

    def _fetch_position(self):
        try:
            r = subprocess.run(
                ['playerctl', '--player=sidra', 'position'],
                capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                GLib.idle_add(self._anim.set_playing, float(r.stdout.strip()))
        except Exception:
            pass

    # ── Boucle ─────────────────────────────────────────────────────────────── #

    def tick(self) -> bool:
        return self._anim.tick() or self.renderer.lyrics_animating()

    def draw(self, cr, w, h):
        self.renderer.draw(cr, w, h)
