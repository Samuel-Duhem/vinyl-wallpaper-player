# Idées d'évolutions — vinyl wallpaper player

Liste non exhaustive, sans ordre de priorité.

---

## Visuels

- **Point de lecture interactif sur le vinyle** — marqueur lumineux qui se déplace le long des rainures au fil de l'album (distinct du bras, visible sur le disque lui-même) ; clic pour seeker
- **Égaliseur / spectre audio** — barres ou ondes réactives autour du vinyle ou en bas d'écran
- **Paroles synchronisées** — affichage des lyrics en temps réel (LRCLib, Apple Music lyrics API)
- **Barre de progression** — arc ou ligne sous le vinyle indiquant la position dans la piste
- **Pochette animée sur le fond** — fond qui "respire" ou pulse sur le beat plutôt que flou fixe
- **Thèmes visuels** — dark/light, couleur dominante extraite de la pochette (palette)
- **Mode nuit** — assombrir automatiquement selon l'heure
- **Particules** — petites étincelles ou poussière depuis l'aiguille quand elle tourne
- **Ombre portée dynamique** — ombre du bras et du vinyle selon une "source de lumière" fixe
- **Plusieurs vinyles** — afficher une pile de disques, le nouveau arrive par dessus
- **Album personnalisé pour les playlists** — si la piste vient d'une playlist, utiliser une pochette custom définie par l'utilisateur plutôt que celle de l'album

## Animations

- **Entrée type jukebox (ref. Arcane)** — animation d'arrivée cinématique au démarrage ou au premier play : bras mécanique, éclairage qui s'allume, disque qui tombe en place
- **Pause musique entre les changements d'album** — mettre la lecture en pause automatiquement pendant la séquence bras-levé + swap + bras-baissé, reprendre une fois l'aiguille posée
- **Transition "flip"** — le vinyle se retourne (effet 3D) pour changer d'album
- **Vitesse de rotation variable** — accélère/décélère selon le tempo BPM de la piste
- **Bras qui "cherche" la rainure** — micro-mouvement de recherche avant de se poser
- **Rebond à la pause** — le disque ralentit avec une légère inertie avant de s'arrêter
- **Warp/voile** — légère déformation du disque à chaud (effet vinyle usagé)

## Configuration

- **Fichier de config** (`config.toml`) — position, taille, opacité, vitesses, thème
- **Panneau de réglages** — fenêtre GTK4 de settings accessible via tray ou keybind Hyprland
- **Raccourcis clavier** — play/pause, suivant, précédent depuis le wallpaper
- **Bind Hyprland** — déclencher des actions depuis `hyprctl dispatch`

## Sources média

- **Mode vidéo** — lire des fichiers MP4 ou des streams YouTube comme fond animé (remplace le fond flouté par la vidéo) ; synchronisé avec la piste ou indépendant
- **Spotify** — via MPRIS natif Linux (pas besoin de bridge Windows)
- **YouTube Music / Tidal / Deezer** — bridges MPRIS similaires à mpris_bridge.py
- **Support multi-players** — détecter automatiquement quel player est actif
- **Radio/streams** — affichage adapté quand pas de pochette ou durée inconnue

## Intégrations

- **Discord Rich Presence** — afficher la piste en cours dans le statut Discord
- **Last.fm** — scrobbling ou affichage du "loved track"
- **Notifications desktop** — notif au changement de piste (libnotify)
- **OBS overlay** — mode fenêtre transparente utilisable comme source de capture

## Technique

- **Base de données d'albums incrémentale** — SQLite locale qui stocke pochette + métadonnées au fur et à mesure des écoutes ; évite de re-fetcher depuis Windows à chaque démarrage, accélère le chargement et permet un historique
- **Support multi-écrans** — un vinyle par moniteur, ou choix de l'écran cible
- **Rendu OpenGL/Vulkan** — remplacer Cairo par du GPU pour les effets coûteux
- **Cache surfaces** — ne pas re-générer le flou si la pochette n'a pas changé entre relances
- **Hot-reload config** — recharger `config.toml` sans redémarrer
- **Tests de non-régression** — screenshots automatiques des états clés de l'animation

## Décors 3D

- **Modélisation d'un décor (TV rétro / jukebox)** — scène 3D (Blender) représentant le meuble qui contient le vinyle ; plusieurs angles de caméra pré-rendus en PNG/séquence
- **Changement de scène animé** — lors d'un changement d'album, switcher entre les images du modèle (fondu enchaîné ou cut) pour donner l'illusion de bouger autour du jukebox
- **Intégration des renders dans le fond** — le vinyle 2D Cairo est composité sur le rendu 3D statique (ou animé) en arrière-plan
- **Mode "cinématique"** — séquence complète à l'ouverture : travelling sur le jukebox, zoom sur le disque, puis transition vers l'état normal
