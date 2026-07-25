#!/bin/bash
DIR="$(dirname "$(realpath "$0")")"

pkill -f "/usr/bin/python3 $DIR/wallpaper.py" 2>/dev/null
pkill -f "/usr/bin/python3 $DIR/monitor.py"   2>/dev/null
rm -f /tmp/player-wallpaper.sock
sleep 0.5

LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so.0 \
    /usr/bin/python3 "$DIR/wallpaper.py" >/tmp/wallpaper.log 2>&1 &
disown

sleep 1

/usr/bin/python3 "$DIR/monitor.py" >/tmp/monitor.log 2>&1 &
disown

echo "[player] Démarré."
