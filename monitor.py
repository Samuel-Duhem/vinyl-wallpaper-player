#!/usr/bin/python3
"""
Watches playerctl for track/status changes and forwards them
to the wallpaper daemon via Unix socket.
"""
import subprocess
import json
import socket
import sys
import time
import os

SOCKET_PATH = '/tmp/player-wallpaper.sock'


def send(data: dict):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(SOCKET_PATH)
        s.sendall(json.dumps(data).encode())
        s.close()
    except Exception as e:
        print(f"[monitor] send error: {e}", file=sys.stderr)


def parse_line(line: str) -> dict | None:
    parts = line.strip().split('|', 5)
    if len(parts) < 4:
        return None
    msg = {
        'status':  parts[0],
        'title':   parts[1],
        'artist':  parts[2],
        'art_url': parts[3],
    }
    if len(parts) > 4:
        try:
            msg['length'] = int(parts[4])
        except ValueError:
            pass
    if len(parts) > 5:
        msg['album'] = parts[5].strip()
    return msg


def get_current() -> dict | None:
    try:
        r = subprocess.run(
            ['playerctl', '--player=sidra', 'metadata', '--format',
             '{{status}}|{{title}}|{{artist}}|{{mpris:artUrl}}|{{mpris:length}}|{{album}}'],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return parse_line(r.stdout)
    except Exception:
        pass
    return None


def wait_for_socket(timeout=120):  # au login wallpaper.py peut mettre >15 s
    for _ in range(timeout * 10):
        if os.path.exists(SOCKET_PATH):
            return True
        time.sleep(0.1)
    return False


def main():
    if not wait_for_socket():
        print("[monitor] wallpaper socket not found — is wallpaper.py running?",
              file=sys.stderr)
        sys.exit(1)

    # Push current state immediately
    meta = get_current()
    if meta:
        print(f"[monitor] initial: {meta['status']} — {meta['title']}")
        send(meta)

    # Follow changes indefinitely
    proc = subprocess.Popen(
        ['playerctl', '--player=sidra', '-F', 'metadata', '--format',
         '{{status}}|{{title}}|{{artist}}|{{mpris:artUrl}}|{{mpris:length}}|{{album}}'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    try:
        for line in proc.stdout:
            msg = parse_line(line)
            if msg:
                print(f"[monitor] {msg['status']} — {msg['title']}", flush=True)
                send(msg)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()


if __name__ == '__main__':
    main()
