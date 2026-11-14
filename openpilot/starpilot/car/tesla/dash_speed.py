"""Tesla dash speed: hold a true speed that the car's own speedometer shows as the set speed.

The Tesla port reads vEgo from DI_speed.DI_vehicleSpeed and the set speed from DI_state.DI_digitalSpeed, and the
planner holds vEgo at the set speed. The Tesla's dash shows DI_speed.DI_uiSpeed instead, which over-reads by about
1.2 percent (0.2 mph at 25, 0.5 at 60, 0.9 at 77, measured on a Model 3).
From roughly 70 mph that rounds up, so the dash sits one above the set speed while the planner is exactly on it.

planner_target() returns the speed to hold so the dash reads the set speed. It is applied only to the driver's or
the speed-limit controller's selected speed, not to curve, navigation or coast ceilings, and never raises a speed.
Pure Python, no openpilot imports, so it tests on the PC.
"""
from __future__ import annotations
import math

MPH_TO_MS = 0.44704
MS_TO_MPH = 1.0 / MPH_TO_MS

# Measured dash over-read (dash minus true, mph) against true speed (mph), steady cruise, 232k samples.
# Piecewise linear; flat-ratio extension above the last point (about 1.2 percent).
OVERREAD_BP_MPH = (0.0, 20.0, 30.0, 40.0, 50.0, 60.0, 68.0, 77.0, 80.0)
OVERREAD_V_MPH = (0.0, 0.15, 0.25, 0.30, 0.42, 0.55, 0.80, 0.93, 0.96)
OVERREAD_RATIO_ABOVE = 0.012
MIN_APPLY_MPS = 5.0 * MPH_TO_MS  # below this the correction is under 0.05 mph; leave stop-and-go alone
TESLA_PLATFORMS = ('TESLA_MODEL_3', 'TESLA_MODEL_Y')


def dash_overread_mps(true_mps: float) -> float:
  """How far the Tesla dash reads above the true speed, in m/s, for a true speed in m/s."""
  if not math.isfinite(true_mps) or true_mps <= 0.0:
    return 0.0
  v = true_mps * MS_TO_MPH
  if v >= OVERREAD_BP_MPH[-1]:
    over = OVERREAD_V_MPH[-1] + OVERREAD_RATIO_ABOVE * (v - OVERREAD_BP_MPH[-1])
  else:
    over = OVERREAD_V_MPH[-1]
    for i in range(1, len(OVERREAD_BP_MPH)):
      if v <= OVERREAD_BP_MPH[i]:
        x0, x1 = OVERREAD_BP_MPH[i - 1], OVERREAD_BP_MPH[i]
        y0, y1 = OVERREAD_V_MPH[i - 1], OVERREAD_V_MPH[i]
        over = y0 + (y1 - y0) * (v - x0) / (x1 - x0)
        break
  return max(0.0, over) * MPH_TO_MS


def dash_reading_mps(true_mps: float) -> float:
  """The unrounded speed the dash would show for a true speed (m/s)."""
  return true_mps + dash_overread_mps(true_mps)


def planner_target(selected_mps: float) -> float:
  """True speed to hold so the dash reads selected_mps. Fixed point of dash_reading(target) == selected."""
  if not math.isfinite(selected_mps) or selected_mps < MIN_APPLY_MPS:
    return selected_mps
  target = selected_mps
  for _ in range(4):  # the over-read is nearly flat in speed; two passes are already within 1e-4 m/s
    target = selected_mps - dash_overread_mps(target)
  return min(target, selected_mps)


def planner_target_kph(selected_kph: float) -> float:
  return planner_target(selected_kph / 3.6) * 3.6


def enabled(CP) -> bool:
  """Only the Model 3/Y port reads DI_speed, and only system long holds a target."""
  try:
    return str(CP.carFingerprint) in TESLA_PLATFORMS and bool(CP.openpilotLongitudinalControl)
  except AttributeError:
    return False


def ui_speed_mps(ui_speed: float, ui_units: float) -> float | None:
  """DI_speed.DI_uiSpeed to m/s. 255 is SNA. Units: 0 mph, 1 kph (DI_uiSpeedUnits)."""
  if ui_speed is None or ui_speed >= 255 or ui_speed < 0:
    return None
  return float(ui_speed) * (1.0 / 3.6 if int(ui_units) == 1 else MPH_TO_MS)
