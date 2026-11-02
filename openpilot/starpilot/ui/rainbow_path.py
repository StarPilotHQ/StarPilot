"""Speed-scrolling gradient for the onroad path."""

import colorsys
import math

from openpilot.starpilot.ui.raylib import rl

from openpilot.system.ui.lib.shader_polygon import Gradient


HUE_RANGE = 120.0
SCROLL_DEG_PER_SEC_PER_MPS = 5.0
NUM_STOPS = 12
MAX_SAMPLE_AGE_NS = 500_000_000
MAX_FRAME_GAP_NS = 250_000_000


class RainbowPath:
  def __init__(self) -> None:
    self.phase_deg = 0.0
    self.last_ns: int | None = None
    self.sample_ns: int | None = None
    self.speed_mps = 0.0

  def update(self, now_ns: int, speed_mps: float | None, *, source_alive: bool) -> None:
    if now_ns < 0 or self.last_ns is not None and now_ns < self.last_ns:
      self.sample_ns = None
      self.last_ns = now_ns
      return
    if not source_alive:
      self.sample_ns = None
    elif speed_mps is not None:
      if math.isfinite(speed_mps) and 0.0 <= speed_mps <= 80.0:
        self.speed_mps = speed_mps
        self.sample_ns = now_ns
      else:
        self.sample_ns = None
    dt_ns = 0 if self.last_ns is None else now_ns - self.last_ns
    self.last_ns = now_ns
    if (self.sample_ns is not None and now_ns - self.sample_ns <= MAX_SAMPLE_AGE_NS and
        0 < dt_ns <= MAX_FRAME_GAP_NS):
      self.phase_deg = (self.phase_deg + self.speed_mps * SCROLL_DEG_PER_SEC_PER_MPS * dt_ns / 1e9) % 360.0

  def gradient(self) -> Gradient:
    stops = [index / (NUM_STOPS - 1) for index in range(NUM_STOPS)]
    colors = []
    for stop in stops:
      hue = ((stop * HUE_RANGE + self.phase_deg) % 360.0) / 360.0
      red, green, blue = colorsys.hls_to_rgb(hue, 0.5, 1.0)
      alpha = 0.5 + (0.1 - 0.5) * stop
      colors.append(rl.Color(int(red * 255), int(green * 255), int(blue * 255), int(alpha * 255)))
    return Gradient(start=(0.0, 0.0), end=(0.0, 1.0), colors=colors, stops=stops)
