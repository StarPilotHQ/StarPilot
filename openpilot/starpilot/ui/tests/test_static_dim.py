"""Burn In Protection (dimming of pixels that stop changing): the detector on made-up pictures, and the screen part
with a made-up display."""
import numpy as np
import pytest

from openpilot.starpilot.ui import static_dim
from openpilot.starpilot.ui.static_dim import SAMPLE_HZ, StaticPixelDetector

W, H = 160, 96
LOOKS_FULL = int(static_dim.DIM_FULL_S * SAMPLE_HZ) + 24   # fully dimmed, with room for the 2 s search rhythm


def blank(value=0):
  frame = np.full((H, W, 4), value, np.uint8)
  frame[..., 3] = 255
  return frame


def moving_picture(rng, step):
  """A camera-like picture that changes everywhere on every look."""
  frame = blank()
  frame[..., :3] = rng.integers(20, 235, (H // 4, W // 4, 3), np.uint8).repeat(4, axis=0).repeat(4, axis=1)
  return frame


def draw_hud(frame, color=(255, 255, 255)):
  """Two thick strokes and a thin one, like a big digit and a line of small text."""
  frame[20:60, 30:38, :3] = color
  frame[20:28, 30:70, :3] = color
  frame[70:72, 20:120, :3] = color
  return frame


HUD = np.zeros((H, W), bool)
HUD[20:60, 30:38] = HUD[20:28, 30:70] = HUD[70:72, 20:120] = True
FAR = np.ones((H, W), bool)
FAR[14:78, 14:126] = False   # well away from the HUD strokes


def run(detector, looks, picture, rng=None):
  rng = rng or np.random.default_rng(7)
  for step in range(looks):
    detector.sample(picture(rng, step))


def test_still_shapes_over_a_moving_picture_dim_and_the_picture_does_not():
  detector = StaticPixelDetector(W, H)
  run(detector, LOOKS_FULL, lambda rng, step: draw_hud(moving_picture(rng, step)))
  assert detector.level[HUD].min() == 255
  assert detector.level[FAR].max() == 0
  mask = detector.mask(0.4)
  assert 98 <= mask[HUD].min() <= mask[HUD].max() <= 103      # 40 % of 255
  assert mask[FAR].max() == 0
  assert detector.veil == 0


def test_nothing_dims_before_fifteen_seconds_and_it_fades_in_gradually():
  detector = StaticPixelDetector(W, H)
  def picture(rng, step):
    return draw_hud(moving_picture(rng, step))

  rng = np.random.default_rng(3)
  run(detector, int(14 * SAMPLE_HZ), picture, rng)
  assert detector.level.max() == 0
  run(detector, int(18 * SAMPLE_HZ), picture, rng)            # 32 s in: part of the way
  assert 60 < np.median(detector.level[HUD]) < 200


def test_a_changed_pixel_is_at_full_brightness_on_the_next_look():
  detector = StaticPixelDetector(W, H)
  rng = np.random.default_rng(5)
  run(detector, LOOKS_FULL, lambda r, step: draw_hud(moving_picture(r, step)), rng)
  frame = draw_hud(moving_picture(rng, 0))
  frame[70:72, 20:120, :3] = (20, 20, 20)                    # the thin line goes away
  assert detector.sample(frame) is True
  assert detector.level[70:72, 20:120].max() == 0
  assert detector.level[20:60, 30:38].min() == 255            # the rest stays dimmed
  assert detector.mask(0.4)[70:72, 40:100].max() < 60         # only the soft rim of nothing remains


def test_a_new_stroke_touching_dimmed_ones_joins_them_quickly():
  detector = StaticPixelDetector(W, H)
  rng = np.random.default_rng(11)
  run(detector, LOOKS_FULL, lambda r, step: draw_hud(moving_picture(r, step)), rng)

  def changed_digit(r, step):
    frame = draw_hud(moving_picture(r, step))
    frame[52:60, 30:70, :3] = 255                             # a new bar, joined to the old upright
    return frame

  run(detector, int(4 * SAMPLE_HZ), changed_digit, rng)
  assert detector.level[52:60, 44:66].max() < 255             # still lit up shortly after the change
  run(detector, int(20 * SAMPLE_HZ), changed_digit, rng)      # well before the 45 s a new shape needs
  assert detector.level[53:59, 44:66].min() == 255


def test_a_flat_still_area_is_left_alone():
  """Bright sky that does not change has no drawn edge: it must not dim while the scenery below it moves.
  (The skyline itself moves, as it does on the road. A razor-straight edge that never moves is a drawn panel.)"""
  detector = StaticPixelDetector(W, H)

  def picture(rng, step):
    frame = moving_picture(rng, step)
    skyline = rng.integers(36, 48, W // 8).repeat(8)
    frame[:36, :, :3] = 215
    for column in range(0, W, 8):
      frame[36:skyline[column], column:column + 8, :3] = 215
    return frame

  run(detector, LOOKS_FULL, picture)
  assert detector.level[:30].max() == 0
  assert np.count_nonzero(detector.level[:36]) < 0.005 * 36 * W   # at most a stray speck at the skyline


def test_dark_pixels_are_ignored():
  detector = StaticPixelDetector(W, H)
  run(detector, LOOKS_FULL, lambda rng, step: draw_hud(moving_picture(rng, step), color=(30, 30, 30)))
  assert detector.level.max() == 0


def test_nothing_new_is_learned_at_a_standstill():
  """When the scene stops, its own sharp bright details are still too. They must not be taken for drawn shapes."""
  detector = StaticPixelDetector(W, H)
  rng = np.random.default_rng(13)
  run(detector, LOOKS_FULL, lambda r, step: draw_hud(moving_picture(r, step)), rng)
  stopped = draw_hud(moving_picture(rng, 0))
  stopped[:, 130:, :3] = 20
  stopped[30:70, 140:146, :3] = 240                           # a bright post in the stopped scene
  jitter = np.random.default_rng(1)

  def standstill(r, step):
    frame = stopped.copy()
    frame[80:96, :8, :3] = jitter.integers(0, 255, 3, np.uint8)   # a little movement, under 1 % of the picture
    return frame

  run(detector, int(60 * SAMPLE_HZ), standstill, rng)
  assert detector.level[30:70, 140:146].max() == 0
  assert detector.level[20:60, 30:38].min() == 255            # what was already dimmed stays dimmed
  assert detector.veil == 0


def test_a_frozen_picture_dims_evenly_and_recovers_when_it_moves():
  detector = StaticPixelDetector(W, H)
  still = draw_hud(blank(120))
  run(detector, int(14 * SAMPLE_HZ), lambda rng, step: still)
  assert detector.veil == 0
  run(detector, int(40 * SAMPLE_HZ), lambda rng, step: still)
  assert detector.veil == 1.0
  mask = detector.mask(0.4)
  assert mask.min() == mask.max() and 98 <= mask.max() <= 103
  rng = np.random.default_rng(2)
  run(detector, int(3 * SAMPLE_HZ), lambda r, step: draw_hud(moving_picture(r, step)), rng)
  assert detector.veil == 0
  assert detector.mask(0.4).max() == 0


def test_wake_puts_everything_back():
  detector = StaticPixelDetector(W, H)
  rng = np.random.default_rng(9)
  run(detector, LOOKS_FULL, lambda r, step: draw_hud(moving_picture(r, step)), rng)
  detector.wake()
  assert detector.mask(0.4).max() == 0
  run(detector, int(10 * SAMPLE_HZ), lambda r, step: draw_hud(moving_picture(r, step)), rng)
  assert detector.level.max() == 0                            # the 15 s start again


def test_shapes_that_go_away_stop_being_dimmed():
  detector = StaticPixelDetector(W, H)
  rng = np.random.default_rng(4)
  run(detector, LOOKS_FULL, lambda r, step: draw_hud(moving_picture(r, step)), rng)
  run(detector, int(5 * SAMPLE_HZ), lambda r, step: moving_picture(r, step), rng)
  assert detector.level.max() == 0
  assert detector.mask(0.4).max() == 0


def test_strength_scales_the_mask():
  detector = StaticPixelDetector(W, H)
  run(detector, LOOKS_FULL, lambda rng, step: draw_hud(moving_picture(rng, step)))
  assert 150 <= detector.mask(0.6)[25, 34] <= 155
  assert detector.mask(0.0).max() == 0


def test_odd_sizes_work():
  detector = StaticPixelDetector(131, 67)
  rng = np.random.default_rng(6)
  for _ in range(40):
    frame = np.zeros((67, 131, 4), np.uint8)
    frame[..., :3] = rng.integers(0, 255, (67, 131, 3), np.uint8)
    frame[..., 3] = 255
    detector.sample(frame)
  assert detector.mask(0.4).shape == (67, 131)
  with pytest.raises(ValueError):
    detector.sample(np.zeros((10, 10, 4), np.uint8))


# ---- the screen part, with a made-up display --------------------------------------------------------------------

class FakeReader:
  def __init__(self, frames):
    self.frames = frames
    self.reads = 0

  def size(self):
    return W, H

  def read(self, out):
    out[...] = self.frames(self.reads)
    self.reads += 1
    return True


class FakeGraphics:
  """Stands in for the drawing library: remembers what was uploaded and drawn."""
  WHITE = "white"

  class PixelFormat:
    PIXELFORMAT_UNCOMPRESSED_R8G8B8A8 = 7

  def __init__(self):
    self.uploads, self.draws = [], 0

  def Image(self, *args):
    return args

  def Rectangle(self, *args):
    return args

  def Vector2(self, *args):
    return args

  def load_texture_from_image(self, image):
    return "texture"

  def update_texture(self, texture, pixels):
    self.uploads.append(pixels.copy())

  def draw_texture_pro(self, *args):
    self.draws += 1

  def is_window_ready(self):
    return False

  def unload_texture(self, texture):
    pass


@pytest.fixture
def screen(monkeypatch):
  from openpilot.starpilot.ui import static_dim_screen
  graphics = FakeGraphics()
  monkeypatch.setattr(static_dim_screen, "rl", graphics)
  monkeypatch.setattr(static_dim_screen, "CAPTURE_REQUEST", "/nonexistent/static_dim_capture")
  monkeypatch.setattr(static_dim_screen.random, "uniform", lambda low, high: 1.0)   # even look spacing, so counts are exact
  clock = [100.0]
  rng = np.random.default_rng(21)
  reader = FakeReader(lambda index: draw_hud(moving_picture(rng, index)))
  dimmer = static_dim_screen.StaticDimmer(reader_factory=lambda: reader, clock=lambda: clock[0])

  def frames(count, step=1 / 20):
    for _ in range(count):
      clock[0] += step
      dimmer.draw(W, H)
      dimmer._idle.wait(2)     # let the background thread finish this look before the next frame

  return dimmer, graphics, reader, frames, clock


def test_screen_does_nothing_while_off(screen):
  dimmer, graphics, reader, frames, _ = screen
  frames(40)
  assert reader.reads == 0 and graphics.draws == 0


def test_screen_looks_four_times_a_second_and_draws_the_dimming(screen):
  dimmer, graphics, reader, frames, _ = screen
  dimmer.configure(True, 40)
  frames(20 * 50)                                             # 50 s at 20 frames a second
  assert 197 <= reader.reads <= 202
  assert not dimmer.failed
  assert graphics.draws > 0
  alpha = graphics.uploads[-1][..., 3]
  assert 98 <= alpha[HUD].min() and alpha[FAR].max() == 0
  assert graphics.uploads[-1][..., :3].max() == 0             # black, so it only ever darkens


def test_screen_wake_and_switching_off_stop_the_dimming_at_once(screen):
  dimmer, graphics, reader, frames, _ = screen
  dimmer.configure(True, 40)
  frames(20 * 50)
  drawn = graphics.draws
  dimmer.wake()
  frames(3)
  assert graphics.draws == drawn                              # nothing drawn after the touch
  frames(20 * 5)
  assert graphics.draws == drawn                              # and not for the next 15 s either
  dimmer.configure(False, 40)
  reads = reader.reads
  frames(20)
  assert reader.reads == reads


def test_screen_starts_over_after_the_display_was_off(screen):
  dimmer, graphics, reader, frames, clock = screen
  dimmer.configure(True, 40)
  frames(20 * 50)
  drawn = graphics.draws
  clock[0] += 30                                              # no frames for 30 s
  frames(20 * 5)
  assert graphics.draws == drawn


def test_screen_switches_itself_off_on_a_screen_it_cannot_handle(screen, monkeypatch):
  dimmer, graphics, reader, frames, _ = screen
  monkeypatch.setattr(reader, "size", lambda: (2160, 1080))
  dimmer.configure(True, 40)
  frames(3)
  assert dimmer.failed and not dimmer.enabled
  dimmer.configure(True, 40)
  assert not dimmer.enabled                                   # stays off until the UI restarts


def test_screen_survives_a_failing_read(screen, monkeypatch):
  dimmer, graphics, reader, frames, _ = screen
  monkeypatch.setattr(reader, "read", lambda out: (_ for _ in ()).throw(OSError("no display")))
  dimmer.configure(True, 40)
  frames(3)
  assert "OSError" in dimmer.failed and not dimmer.enabled
