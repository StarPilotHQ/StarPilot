import math
from collections import deque

import numpy as np

from openpilot.common.constants import CV
from openpilot.common.realtime import DT_CTRL
from openpilot.starpilot.saved_source import read_saved

BASELINE_TAU = 1.5                        # s
SPEED_OFF = 30.0 * CV.MPH_TO_MS
SPEED_ON = 40.0 * CV.MPH_TO_MS
CURVE_LEVEL_TAU = 3.0                     # s
LAT_ACCEL_ON = 0.25                       # m/s^2
LAT_ACCEL_OFF = 0.6                       # m/s^2
CURVE_LEVEL_BLEND = (0.15, 0.35)          # m/s^2
PREDICTION_HORIZONS = (1.5, 2.0)          # s
PREDICTED_CURVE_ON = 0.20                 # m/s^2
PREDICTED_CURVE_OFF = 0.45                # m/s^2
CURVE_DEVIATION_ON = 0.12                 # m/s^2
CURVE_DEVIATION_OFF = 0.30                # m/s^2
TIGHT_CURVE_ON = 1.5                      # m/s^2
TIGHT_CURVE_OFF = 2.0                     # m/s^2
# held longer than the weave's peak spacing so the weave can't modulate its own gain
ENVELOPE_HOLD = 2.0                       # s
ENVELOPE_RELEASE_RATE = 0.3               # m/s^2 per second
BYPASS_FADE_RATE = 2.0                    # per second
MIN_SETTING = 1.0
MAX_SETTING = 10.0
KEY = "HighwaySmoothingLevel"


def _smoothstep(x: float, lo: float, hi: float) -> float:
  t = min(max((x - lo) / (hi - lo), 0.0), 1.0)
  return t * t * (3.0 - 2.0 * t)


def read_gain(params) -> float:
  raw, readable = read_saved(params, KEY, 16)
  if raw is None or not readable:
    return 1.0
  try:
    setting = float(raw)
  except ValueError:
    return 1.0
  return setting / MAX_SETTING if math.isfinite(setting) and MIN_SETTING <= setting <= MAX_SETTING else 1.0


def predicted_lateral_accels(t, velocity_x, yaw_rate) -> tuple[float, ...] | None:
  t, velocity_x, yaw_rate = (np.asarray(list(x), dtype=float) for x in (t, velocity_x, yaw_rate))
  if len(t) < 2 or len(t) != len(velocity_x) or len(t) != len(yaw_rate) or t[-1] < PREDICTION_HORIZONS[-1]:
    return None
  return tuple(float(np.interp(h, t, velocity_x) * np.interp(h, t, yaw_rate)) for h in PREDICTION_HORIZONS)


class _Envelope:
  def __init__(self):
    self.frame = 0
    self.peaks: deque[tuple[int, float]] = deque()
    self.value = 0.0

  def update(self, x: float) -> float:
    self.frame += 1
    while self.peaks and self.peaks[-1][1] <= x:
      self.peaks.pop()
    self.peaks.append((self.frame, x))
    while self.peaks[0][0] <= self.frame - ENVELOPE_HOLD / DT_CTRL:
      self.peaks.popleft()
    self.value = max(self.peaks[0][1], self.value - ENVELOPE_RELEASE_RATE * DT_CTRL)
    return self.value


class HighwayCorrectionGain:
  def __init__(self):
    self.alpha = DT_CTRL / (BASELINE_TAU + DT_CTRL)
    self.alpha_curve = DT_CTRL / (CURVE_LEVEL_TAU + DT_CTRL)
    self.reset()

  def reset(self, curvature: float = 0.0) -> None:
    self.baseline = curvature
    self.curve_level = curvature
    self.envelope = _Envelope()
    self.prediction_envelope = _Envelope()
    self.bypass_weight = 0.0
    self.weight = 0.0

  def update(self, curvature: float, v_ego: float, lat_active: bool, gain: float, bypass: bool,
             predicted: tuple[float, ...] | None = None) -> float:
    if not lat_active:
      self.reset(curvature)
      return curvature

    # tracked even when bypassed so fading in never steps the output
    self.baseline += self.alpha * (curvature - self.baseline)
    self.curve_level += self.alpha_curve * (curvature - self.curve_level)

    v2 = v_ego ** 2
    level = abs(self.curve_level) * v2
    in_curve = _smoothstep(level, *CURVE_LEVEL_BLEND)
    reference = in_curve * self.curve_level
    envelope = self.envelope.update(abs(curvature - reference) * v2)

    if predicted:
      gate = 1.0 - _smoothstep(envelope, LAT_ACCEL_ON, LAT_ACCEL_OFF)
      deviation = max(abs(p - reference * v2) for p in predicted)
      on = LAT_ACCEL_ON + in_curve * (PREDICTED_CURVE_ON - LAT_ACCEL_ON)
      off = LAT_ACCEL_OFF + in_curve * (PREDICTED_CURVE_OFF - LAT_ACCEL_OFF)
      gate *= 1.0 - _smoothstep(self.prediction_envelope.update(deviation), on, off)
    else:
      if self.prediction_envelope.frame:
        self.prediction_envelope = _Envelope()
      on = LAT_ACCEL_ON + in_curve * (CURVE_DEVIATION_ON - LAT_ACCEL_ON)
      off = LAT_ACCEL_OFF + in_curve * (CURVE_DEVIATION_OFF - LAT_ACCEL_OFF)
      gate = 1.0 - _smoothstep(envelope, on, off)
    gate *= 1.0 - _smoothstep(level, TIGHT_CURVE_ON, TIGHT_CURVE_OFF)

    step = BYPASS_FADE_RATE * DT_CTRL
    self.bypass_weight += min(max((0.0 if bypass else 1.0) - self.bypass_weight, -step), step)

    gain = min(max(gain, MIN_SETTING / MAX_SETTING), 1.0)
    self.weight = self.bypass_weight * _smoothstep(v_ego, SPEED_OFF, SPEED_ON) * gate
    k = 1.0 - self.weight * (1.0 - gain)
    if k >= 1.0:
      return curvature
    return self.baseline + k * (curvature - self.baseline)
