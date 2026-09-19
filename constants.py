import os

SOCKET_PATH       = '/tmp/player-wallpaper.sock'
DIR               = os.path.dirname(os.path.realpath(__file__))
DEFAULT_WALLPAPER = os.path.join(DIR, 'default.jpg')
LAST_TRACK_FILE   = os.path.join(DIR, 'last_track.json')

FRAME_MS           = 16       # ~60 fps
TRANSITION_MS      = 1200     # durée crossfade fond (ms)
SPIN_DEG_PER_FRAME = 1.0      # (ancien) rotation vinyle (degrés/frame)
SPIN_RPM           = 100 / 3  # 33⅓ tours/min (mettre 45 pour un 45 tours)
SPIN_DEG_PER_S     = SPIN_RPM * 6.0   # rotation vinyle : degrés par seconde (indépendant du taux d'images)
ARM_LIFT_SPEED     = 1.8      # degrés/frame — lever le bras
ARM_DROP_SPEED     = 0.9      # degrés/frame — baisser le bras
ARM_MAX_LIFT       = 38.0     # amplitude max du lever (degrés)
VINYL_SLIDE_STEP   = 0.030    # fraction/frame (~33 frames = 550 ms)
VINYL_IN_START     = 0.55     # progression à partir de laquelle le nouveau vinyle entre
MAX_ALBUM_S        = 2700.0   # 45 min — plage complète de l'aiguille

BG_SIZE          = (1920, 1080)
BG_BLUR_RADIUS   = 45
BG_OVERLAY_ALPHA = 120        # 0-255

