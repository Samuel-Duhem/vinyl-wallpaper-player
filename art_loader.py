import hashlib
import threading
import urllib.request
from io import BytesIO

import cairo
from gi.repository import GLib
from PIL import Image, ImageFilter

from theme import extract_theme


def pil_to_cairo(pil_img: Image.Image) -> cairo.ImageSurface:
    buf = BytesIO()
    pil_img.save(buf, format='PNG')
    buf.seek(0)
    return cairo.ImageSurface.create_from_png(buf)


def png_to_cairo(png_bytes: bytes) -> cairo.ImageSurface:
    return cairo.ImageSurface.create_from_png(BytesIO(png_bytes))


class ArtLoader:
    """
    Charge la pochette d'album en thread de fond.

    Stratégie :
      1. Fetch des bytes bruts depuis l'URL (HTTP ou fichier local).
      2. Calcul du MD5 → détection changement d'album.
      3. Lookup dans AlbumDB : si hit, charge les PNG pré-traités directement.
      4. Sinon : traitement PIL (resize + blur), stockage en DB.
      5. Appel du callback on_ready(album_surf, bg_surf, track, is_new_album)
         sur le thread GLib principal.
    """

    def __init__(self, db, on_ready):
        self._db       = db
        self._on_ready = on_ready
        self._art_hash = None
        self._loading  = False
        self._pending  = None      # dernière demande reçue pendant un chargement (rejouée ensuite)

    @property
    def is_loading(self) -> bool:
        return self._loading

    def reset_hash(self):
        """Réinitialise la détection d'album (à appeler à l'arrêt de la lecture)."""
        self._art_hash = None

    def load(self, track: dict):
        if self._loading:
            self._pending = track      # ne pas perdre la demande : elle est rejouée à la fin du chargement
            return
        self._loading = True
        threading.Thread(target=self._fetch, args=(track,), daemon=True).start()

    def _replay(self, track):
        self.load(track)
        return False

    # ── Implémentation privée ─────────────────────────────────────────── #

    def _fetch(self, track: dict):
        try:
            raw = self._get_raw(track.get('art_url', '').strip())
            if raw is None:
                return

            art_hash       = hashlib.md5(raw).hexdigest()
            is_new         = art_hash != self._art_hash
            self._art_hash = art_hash

            cached = self._db.get(art_hash)
            if cached:
                album_png, bg_png = cached
            else:
                album_png, bg_png = self._process(raw)
                self._db.store(art_hash, album_png, bg_png, track)

            # Thème + fond dégradé calculés à partir de la pochette (voir theme.py)
            theme, bg_img = extract_theme(Image.open(BytesIO(album_png)))
            track = {**track, 'theme': theme}

            GLib.idle_add(
                self._on_ready,
                png_to_cairo(album_png),
                pil_to_cairo(bg_img.convert('RGBA')),
                track,
                is_new,
            )
        except Exception as e:
            print(f'[art] {e}')
        finally:
            self._loading = False
            pend, self._pending = self._pending, None
            if pend is not None and pend.get('art_url') != track.get('art_url'):
                GLib.idle_add(self._replay, pend)

    @staticmethod
    def _get_raw(url: str) -> bytes | None:
        if url.startswith('file://'):
            return open(url[7:], 'rb').read()
        if url.startswith('http'):
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read()
        return None

    @staticmethod
    def _process(raw: bytes) -> tuple[bytes, bytes]:
        """Traitement PIL : retourne (album_png, bg_png) en bytes."""
        img = Image.open(BytesIO(raw)).convert('RGBA')

        bg = Image.new('RGBA', (2, 2))   # le fond n'est plus mis en cache (recalculé via theme.py)

        album_buf, bg_buf = BytesIO(), BytesIO()
        img.save(album_buf, format='PNG')
        bg.save(bg_buf, format='PNG')
        return album_buf.getvalue(), bg_buf.getvalue()
