"""
Surveille si des fenêtres (clients Hyprland) couvrent le bureau actif d'un écran.
Événementiel : on écoute le socket d'événements de Hyprland et on ne ré-interroge `hyprctl`
qu'à l'ouverture/fermeture/déplacement d'une fenêtre ou au changement de bureau.
"""
import glob
import json
import os
import socket
import subprocess
import threading
import time

from gi.repository import GLib

EVENTS = ('openwindow', 'closewindow', 'movewindow', 'workspace', 'focusedmon',
          'moveworkspace', 'destroyworkspace', 'createworkspace', 'monitoradded',
          'monitorremoved', 'changefloatingmode', 'minimized', 'urgent', 'fullscreen')


def _hypr(*args):
    return json.loads(subprocess.run(['hyprctl', *args, '-j'], capture_output=True, text=True,
                                     timeout=3).stdout)


def _event_socket_path():
    runtime = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    sig = os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')
    if sig:
        return f'{runtime}/hypr/{sig}/.socket2.sock'
    found = sorted(glob.glob(f'{runtime}/hypr/*/.socket2.sock'), key=os.path.getmtime)
    return found[-1] if found else None


class DesktopWatcher:
    """covered == True si au moins une fenêtre est affichée sur le bureau actif de l'écran ;
    fullscreen == True si une fenêtre est en plein écran sur n'importe quel écran."""

    def __init__(self, connector, on_change):
        self._connector = connector
        self._on_change = on_change      # appelé sur le thread GLib principal
        self.covered = None              # None = inconnu : le premier résultat est toujours signalé
        self.fullscreen = None
        self._wake = threading.Event()

    def start(self):
        self._wake.set()                 # première vérification immédiate
        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()

    # -- interrogation de l'état -------------------------------------------- #

    def _query(self) -> tuple[bool, bool]:
        monitors, clients = _hypr('monitors'), _hypr('clients')
        active = {m['activeWorkspace']['id'] for m in monitors}
        # Plein écran sur n'importe quel écran (jeu) : sans explicit-sync, tous les écrans
        # partagent la même file GPU, un fond animé ailleurs suffit à faire clignoter le jeu.
        fullscreen = any(c['mapped'] and not c['hidden'] and c['fullscreen'] == 2
                         and c['workspace']['id'] in active for c in clients)
        mon = next((m for m in monitors if m['name'] == self._connector), None)
        if mon is None:
            return False, fullscreen
        ws = mon['activeWorkspace']['id']
        return any(c['mapped'] and not c['hidden'] and c['workspace']['id'] == ws
                   and c['monitor'] == mon['id'] for c in clients), fullscreen

    def _worker(self):
        while True:
            self._wake.wait(timeout=5.0)     # repli : re-vérifie au moins toutes les 5 s
            self._wake.clear()
            time.sleep(0.05)                 # regroupe les rafales d'événements
            self._wake.clear()
            try:
                covered, fullscreen = self._query()
            except Exception as e:
                print(f'[desktop] {e}')
                continue
            if (covered, fullscreen) != (self.covered, self.fullscreen):
                self.covered, self.fullscreen = covered, fullscreen
                GLib.idle_add(self._notify, covered, fullscreen)

    def _notify(self, covered, fullscreen):
        self._on_change(covered, fullscreen)
        return False

    # -- événements Hyprland --------------------------------------------------- #

    def _listen(self):
        while True:
            path = _event_socket_path()
            if not path or not os.path.exists(path):
                time.sleep(2)
                continue
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(path)
                for line in s.makefile('r', errors='replace'):
                    if line.split('>>', 1)[0] in EVENTS:
                        self._wake.set()
            except Exception as e:
                print(f'[desktop] socket: {e}')
            time.sleep(2)
