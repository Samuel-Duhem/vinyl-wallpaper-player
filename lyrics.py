"""
Paroles synchronisées via LRCLib (https://lrclib.net, API publique sans clé).

  - récupération en thread de fond (jamais bloquant pour l'affichage) ;
  - cache disque (~/.cache/player/lyrics) y compris les échecs, pour ne pas ré-interroger ;
  - on n'envoie à LRCLib que titre / artiste / album / durée du morceau en cours.
"""
import bisect
import hashlib
import json
import os
import re
import threading
import urllib.parse
import urllib.request

from gi.repository import GLib

API = 'https://lrclib.net/api'
UA = 'vinyl-wallpaper-player/1.0 (https://github.com/Samuel-Duhem/vinyl-wallpaper-player)'
CACHE_DIR = os.path.join(os.path.expanduser('~'), '.cache', 'player', 'lyrics')

_TS = re.compile(r'\[(\d+):(\d+(?:\.\d+)?)\]')
_NOISE = re.compile(
    r'\s*[\(\[][^)\]]*(remaster|remastered|version|deluxe|live|mono|stereo|bonus|edit|anniversary|explicit)[^)\]]*[\)\]]'
    r'|\s+-\s+(\d{4}\s+)?(remaster|remastered|live|mono|stereo|single version|radio edit).*$', re.I)


def clean_title(title: str) -> str:
    return _NOISE.sub('', title).strip()


def parse_lrc(text: str):
    """[(secondes, texte)] trié ; les lignes vides deviennent une pause musicale (♪)."""
    out = []
    for raw in text.splitlines():
        stamps = _TS.findall(raw)
        if not stamps:
            continue
        line = _TS.sub('', raw).strip() or '♪'
        for mm, ss in stamps:
            out.append((int(mm) * 60 + float(ss), line))
    out.sort(key=lambda x: x[0])
    return out


def _get(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode())


def fetch(title, artist, album, duration):
    """Retourne (lignes, source) ; lignes = [] si rien de synchronisé n'est trouvé."""
    def synced(d):
        return parse_lrc(d.get('syncedLyrics') or '') if d and not d.get('instrumental') else []

    for t in dict.fromkeys((title, clean_title(title))):
        q = {'track_name': t, 'artist_name': artist}
        if album:
            q['album_name'] = album
        if duration:
            q['duration'] = str(int(round(duration)))
        try:
            lines = synced(_get(f'{API}/get?{urllib.parse.urlencode(q)}'))
            if lines:
                return lines, 'get'
        except Exception:
            pass
    # Repli : recherche, on garde la version synchronisée dont la durée est la plus proche
    for t in dict.fromkeys((clean_title(title), title)):
        try:
            res = _get(f'{API}/search?{urllib.parse.urlencode({"track_name": t, "artist_name": artist})}')
        except Exception:
            continue
        cands = [r for r in res if r.get('syncedLyrics') and not r.get('instrumental')]
        if duration:
            cands = [r for r in cands if abs((r.get('duration') or 0) - duration) <= 6]
            cands.sort(key=lambda r: abs((r.get('duration') or 0) - duration))
        if cands:
            return parse_lrc(cands[0]['syncedLyrics']), 'search'
    return [], 'none'


class Lyrics:
    """État des paroles du morceau en cours. `state` : 'none' | 'loading' | 'ok' | 'missing'."""

    def __init__(self, on_update):
        self._on_update = on_update      # appelé sur le thread GLib principal
        self.state = 'none'
        self.lines = []
        self._times = []
        self._key = None
        os.makedirs(CACHE_DIR, exist_ok=True)

    def request(self, title, artist, album='', duration=0.0):
        """Idempotent : sans effet si c'est déjà le morceau demandé."""
        if not title or not artist:
            return
        key = hashlib.md5(f'{artist}|{title}|{album}|{int(duration)}'.encode()).hexdigest()
        if key == self._key:
            return
        self._key = key
        self.lines, self._times, self.state = [], [], 'loading'
        cached = os.path.join(CACHE_DIR, key + '.json')
        if os.path.exists(cached):
            try:
                self._set(key, [tuple(x) for x in json.load(open(cached))])
                return
            except Exception:
                pass
        threading.Thread(target=self._worker, args=(key, title, artist, album, duration, cached), daemon=True).start()

    def _worker(self, key, title, artist, album, duration, cached):
        try:
            lines, src = fetch(title, artist, album, duration)
            print(f'[lyrics] {artist} — {title}: {len(lines)} lignes ({src})', flush=True)
            json.dump(lines, open(cached, 'w'))
        except Exception as e:
            print(f'[lyrics] {e}', flush=True)
            lines = None          # erreur réseau : on ne met pas en cache, on réessaiera
        GLib.idle_add(self._done, key, lines)

    def _done(self, key, lines):
        if key == self._key and lines is not None:
            self._set(key, lines)
        elif key == self._key:
            self.state = 'missing'
            self._key = None      # autorise un nouvel essai
        return False

    def _set(self, key, lines):
        self.lines = lines
        self._times = [t for t, _ in lines]
        self.state = 'ok' if lines else 'missing'
        self._on_update()

    def index_at(self, pos: float) -> int:
        """Index de la ligne en cours à la position `pos` (-1 avant la première ligne)."""
        return bisect.bisect_right(self._times, pos) - 1
