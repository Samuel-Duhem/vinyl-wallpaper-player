import time

from constants import (
    FRAME_MS, TRANSITION_MS,
    SPIN_DEG_PER_S,
    ARM_LIFT_SPEED, ARM_DROP_SPEED, ARM_MAX_LIFT,
    VINYL_SLIDE_STEP, MAX_ALBUM_S,
)

DEFAULT_TRACK_S = 240.0   # durée supposée tant que le lecteur ne l'a pas fournie


class AnimEngine:
    """
    Machine à états d'animation du player vinyle.

    Gère : rotation du disque, bras de lecture (lever/baisser),
    swap de vinyle (changement d'album), crossfade du fond,
    et progression de l'aiguille dans les rainures.

    Callback obligatoire à assigner avant le premier tick :
        on_apply_surfaces(album_surf, bg_surf, track)
        — appelé sur le thread GLib principal pour committer
          les nouvelles surfaces Cairo.
    """

    def __init__(self):
        # Disque : la rotation totale (non bornée) sert aussi à placer l'aiguille sur la spirale
        self.spin_total = 0.0
        self.is_playing = False
        self._last_tick = time.monotonic()

        # Spirale du sillon : phase (degrés) et durée du morceau au moment de la pose de l'aiguille
        self.spiral_phase0 = 0.0
        self.spiral_T      = 0.0

        # Crossfade fond
        self.transition       = 1.0
        self._transition_step = FRAME_MS / TRANSITION_MS

        # Bras de lecture
        self.arm_offset = 0.0
        self.arm_state  = 'idle'   # 'idle' | 'lifting' | 'dropping'

        # Swap de vinyle
        self.vinyl_state        = 'idle'   # 'idle' | 'swapping'
        self.vinyl_off          = 0.0
        self.vinyl_swap_pending = False
        self.pending_album      = None
        self.pending_bg         = None
        self.pending_track      = None

        # Position aiguille / progression album
        self.song_duration      = 0.0
        self.play_start_time    = None
        self.frozen_position_s  = 0.0
        self.album_cumulative_s = 0.0

        # Callback assigné par WallpaperWindow
        self.on_apply_surfaces = None   # callable(album_surf, bg_surf, track)

    @property
    def rotation(self) -> float:
        return self.spin_total % 360.0

    @rotation.setter
    def rotation(self, value: float):
        self.spin_total = float(value)

    # ── API publique ────────────────────────────────────────────────────── #

    def track_elapsed_s(self) -> float:
        """Position dans le morceau en cours (secondes)."""
        if self.is_playing and self.play_start_time is not None:
            return max(0.0, time.time() - self.play_start_time)
        return self.frozen_position_s

    def reset_spiral(self):
        """Pose l'aiguille au début du sillon d'un morceau : fixe la phase de la spirale (rotation
        qu'aurait eue le disque à t = 0 du morceau) et sa durée."""
        self.spiral_phase0 = self.spin_total - SPIN_DEG_PER_S * self.track_elapsed_s()
        self.spiral_T      = self.song_duration if self.song_duration > 0 else DEFAULT_TRACK_S

    def request_hop(self, album_surf, bg_surf, track):
        """Même album, nouveau morceau : le bras se lève, revient au bord du disque, et se repose."""
        self.pending_album = album_surf
        self.pending_bg    = bg_surf
        self.pending_track = track
        if self.arm_state == 'idle' and self.arm_offset <= 0:
            self.arm_state = 'lifting'

    def request_swap(self, album_surf, bg_surf, track):
        """Déclenche la séquence bras-levé → swap vinyle → bras-baissé."""
        self.vinyl_swap_pending = True
        self.pending_album      = album_surf
        self.pending_bg         = bg_surf
        self.pending_track      = track
        if self.arm_state == 'idle' and self.arm_offset <= 0:
            self.arm_state = 'lifting'

    def request_apply(self, album_surf, bg_surf, track):
        """Applique directement (même album) ou met en attente si une animation tourne."""
        arm_busy   = self.arm_state in ('lifting', 'idle') and self.arm_offset > 0
        vinyl_busy = self.vinyl_state == 'swapping'
        if arm_busy or vinyl_busy:
            self.pending_album = album_surf
            self.pending_bg    = bg_surf
            self.pending_track = track
        elif self.on_apply_surfaces:
            self.on_apply_surfaces(album_surf, bg_surf, track)

    def start_transition(self):
        self.transition = 0.0

    def set_playing(self, initial_pos: float):
        self.is_playing      = True
        self.play_start_time = time.time() - initial_pos

    def set_paused(self):
        if self.play_start_time is not None:
            self.frozen_position_s = max(0.0, time.time() - self.play_start_time)
        self.play_start_time = None
        self.is_playing      = False

    def set_stopped(self):
        self.album_cumulative_s = 0.0
        self.frozen_position_s  = 0.0
        self.play_start_time    = None
        self.is_playing         = False

    def on_track_change(self):
        """À appeler quand le titre change : accumule la durée de la piste précédente."""
        if self.song_duration > 0 and self.play_start_time is not None:
            self.album_cumulative_s += max(0.0, time.time() - self.play_start_time)
        self.frozen_position_s = 0.0
        self.play_start_time   = None

    def get_needle_progress(self) -> float:
        """Fraction [0, 1] du sillon parcourue : (rotation depuis la pose) / (rotation totale du morceau).
        Le disque tourne à vitesse constante, donc l'aiguille suit exactement la spirale dessinée."""
        T = self.spiral_T if self.spiral_T > 0 else (self.song_duration or DEFAULT_TRACK_S)
        return min(1.0, max(0.0, (self.spin_total - self.spiral_phase0) / (SPIN_DEG_PER_S * T)))

    # ── Tick ─────────────────────────────────────────────────────────────── #

    def tick(self) -> bool:
        """Avance d'une frame. Retourne True si un redraw est nécessaire."""
        redraw = False

        now = time.monotonic()
        dt = min(0.1, now - self._last_tick)
        self._last_tick = now
        if self.is_playing:
            self.spin_total += SPIN_DEG_PER_S * dt
            redraw = True

        if self.transition < 1.0:
            self.transition = min(1.0, self.transition + self._transition_step)
            redraw = True

        # Bras de lecture
        if self.arm_state == 'lifting':
            self.arm_offset = min(ARM_MAX_LIFT, self.arm_offset + ARM_LIFT_SPEED)
            redraw = True
            if self.arm_offset >= ARM_MAX_LIFT:
                if self.vinyl_swap_pending and self.pending_album is not None:
                    if self.on_apply_surfaces:
                        self.on_apply_surfaces(
                            self.pending_album, self.pending_bg, self.pending_track)
                    self.pending_album = self.pending_bg = self.pending_track = None
                    self.vinyl_state = 'swapping'
                    self.vinyl_off   = 0.0
                    self.arm_state   = 'idle'
                elif not self.vinyl_swap_pending and self.pending_album is not None:
                    if self.on_apply_surfaces:
                        self.on_apply_surfaces(
                            self.pending_album, self.pending_bg, self.pending_track)
                    self.pending_album = self.pending_bg = self.pending_track = None
                    self.reset_spiral()          # nouveau morceau : l'aiguille repart du bord du disque
                    self.arm_state = 'dropping'

        elif self.arm_state == 'dropping':
            self.arm_offset = max(0.0, self.arm_offset - ARM_DROP_SPEED)
            redraw = True
            if self.arm_offset <= 0.0:
                self.arm_state = 'idle'

        # Swap de vinyle
        if self.vinyl_state == 'swapping':
            self.vinyl_off = min(1.0, self.vinyl_off + VINYL_SLIDE_STEP)
            redraw = True
            if self.vinyl_off >= 1.0:
                self.vinyl_state        = 'idle'
                self.vinyl_swap_pending = False
                self.reset_spiral()              # nouveau disque : l'aiguille repart du bord
                self.arm_state          = 'dropping'

        return redraw
