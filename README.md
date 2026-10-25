# Vinyl wallpaper player

Fond d'écran animé pour Hyprland : la piste en cours de lecture s'affiche sous forme de vinyle qui tourne sur une platine, avec la pochette, un fond flouté aux couleurs de l'album et les paroles synchronisées.

## Fonctionnement

- `monitor.py` suit le lecteur via `playerctl` (MPRIS) et envoie les changements de piste au fond d'écran par un socket Unix.
- `wallpaper.py` affiche le fond d'écran (GTK4 + gtk4-layer-shell, rendu Cairo) : rotation du vinyle à 33⅓ tours/min, bras de lecture, changement d'album animé.
- `lyrics.py` récupère les paroles synchronisées via l'API publique [LRCLib](https://lrclib.net).
- `theme.py` extrait les couleurs de la pochette ; `album_db.py` met en cache les pochettes traitées (SQLite).
- `desktop_watch.py` détecte, via le socket d'événements Hyprland, si des fenêtres recouvrent le bureau.

## Prérequis

- Linux avec Hyprland
- Python 3, PyGObject (GTK 4), gtk4-layer-shell, pycairo, Pillow
- `playerctl`

## Lancement

```bash
./start.sh            # démarre le fond d'écran et le moniteur
./toggle-lyrics.sh    # affiche / masque les paroles (à lier à un raccourci)
```

Le lecteur suivi est actuellement `sidra` (en dur dans `monitor.py`, `music_player.py` et `wallpaper.py`).
Pour un fond par défaut quand rien ne joue, placez une image `default.jpg` dans le dossier.

Pistes d'évolution : voir [IDEAS.md](IDEAS.md).

## Licence

GPL-3.0 — voir [LICENSE](LICENSE).
