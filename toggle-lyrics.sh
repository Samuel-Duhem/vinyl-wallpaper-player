#!/bin/bash
# Active / désactive l'affichage des paroles du player (à brancher sur un raccourci clavier).
python3 - <<'PY'
import json, socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect('/tmp/player-wallpaper.sock')
s.sendall(json.dumps({'cmd': 'toggle_lyrics'}).encode())
s.close()
PY
