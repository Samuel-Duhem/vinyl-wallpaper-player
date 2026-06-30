import os
import sqlite3
import threading
import time

DB_PATH = os.path.join(os.path.expanduser('~'), '.local', 'share', 'player', 'albums.db')


class AlbumDB:
    """
    Cache SQLite incrémental des pochettes d'album.

    Stocke les PNG traités (pochette + fond flouté) pour éviter le re-traitement
    PIL à chaque lecture. Clé primaire : MD5 des bytes bruts de l'art (art_hash).
    """

    def __init__(self, path: str = DB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.execute('''
                CREATE TABLE IF NOT EXISTS albums (
                    art_hash   TEXT PRIMARY KEY,
                    album_png  BLOB NOT NULL,
                    bg_png     BLOB NOT NULL,
                    title      TEXT,
                    artist     TEXT,
                    album      TEXT,
                    fetched_at INTEGER NOT NULL
                )
            ''')
            self._conn.commit()

    def get(self, art_hash: str) -> tuple[bytes, bytes] | None:
        """Retourne (album_png, bg_png) si en cache, sinon None."""
        with self._lock:
            row = self._conn.execute(
                'SELECT album_png, bg_png FROM albums WHERE art_hash = ?',
                (art_hash,),
            ).fetchone()
        return (bytes(row[0]), bytes(row[1])) if row else None

    def store(self, art_hash: str, album_png: bytes, bg_png: bytes, track: dict):
        """Stocke ou met à jour une entrée."""
        with self._lock:
            self._conn.execute(
                '''INSERT OR REPLACE INTO albums
                   (art_hash, album_png, bg_png, title, artist, album, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (
                    art_hash, album_png, bg_png,
                    track.get('title', ''),
                    track.get('artist', ''),
                    track.get('album', ''),
                    int(time.time()),
                ),
            )
            self._conn.commit()

    def close(self):
        self._conn.close()
