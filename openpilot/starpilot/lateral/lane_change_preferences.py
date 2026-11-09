"""Optional, session-scoped preferences for driver-nudged lane changes."""

from dataclasses import dataclass, replace
import json
import math
import os
import stat
from typing import Any

from openpilot.common.constants import CV


KEY = "LaneChangePreferences"
MAX_BYTES = 512
MAX_SPEED_MPS = 100 * CV.MPH_TO_MS
MINIMUM_SPEED_MPS = 5 * CV.MPH_TO_MS
DEFAULT_SPEED_MPS = MINIMUM_SPEED_MPS


@dataclass(frozen=True)
class LaneChangePolicy:
  enabled: bool = True
  minimum_speed_mps: float = DEFAULT_SPEED_MPS
  one_per_signal: bool = True
  auto_lane_change: bool = False
  auto_delay_s: float = 1.0
  minimum_lane_width_m: float = 0.0
  close_gap: bool = False
  close_gap_seconds: float = 0.75
  duration_s: float = 3.0 + 25.0 / 9.0
  turn_signal_guidance: bool = True
  auto_requires_cruise: bool = False


@dataclass(frozen=True)
class SavedLaneChange:
  raw: bytes | None
  policy: LaneChangePolicy = LaneChangePolicy()
  valid: bool = True
  readable: bool = True


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
  value: dict[str, object] = {}
  for key, item in pairs:
    if key in value:
      raise ValueError("duplicate lane-change field")
    value[key] = item
  return value


def decode(raw: bytes) -> LaneChangePolicy | None:
  if len(raw) > MAX_BYTES:
    return None
  try:
    data = json.loads(raw, object_pairs_hook=_unique_object,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite")))
  except (UnicodeDecodeError, ValueError, TypeError, RecursionError, OverflowError):
    return None
  if not isinstance(data, dict):
    return None
  version = data.get("version")
  fields = {"version", "enabled", "minimumSpeedMps", "onePerSignal"}
  if version in (2, 3, 4, 5, 6):
    fields |= {"autoLaneChange", "autoDelayS", "minimumLaneWidthM"}
  if version in (3, 4, 5, 6):
    fields |= {"closeGap", "closeGapSeconds"}
  if version in (4, 5, 6):
    fields |= {"durationS"}
  if version in (5, 6):
    fields |= {"turnSignalGuidance"}
  if version == 6:
    fields |= {"autoRequiresCruise"}
  if set(data) != fields:
    return None
  enabled = data["enabled"]
  speed, once = data["minimumSpeedMps"], data["onePerSignal"]
  if (type(version) is not int or version not in (1, 2, 3, 4, 5, 6) or type(enabled) is not bool or type(once) is not bool or
      type(speed) not in (int, float)):
    return None
  try:
    finite_speed = float(speed)
  except (ValueError, OverflowError):
    return None
  if not math.isfinite(finite_speed) or not 0 <= finite_speed <= MAX_SPEED_MPS:
    return None
  if version == 1:
    return LaneChangePolicy(enabled, finite_speed, once)
  auto, delay, width = data["autoLaneChange"], data["autoDelayS"], data["minimumLaneWidthM"]
  if type(auto) is not bool or type(delay) not in (int, float) or type(width) not in (int, float):
    return None
  try:
    delay, width = float(delay), float(width)
  except (ValueError, OverflowError):
    return None
  if not (math.isfinite(delay) and math.isfinite(width) and 0 <= delay <= 5 and 0 <= width <= 15 * 0.3048):
    return None
  if version == 2:
    return LaneChangePolicy(enabled, finite_speed, once, auto, delay, width)
  close_gap, seconds = data["closeGap"], data["closeGapSeconds"]
  if type(close_gap) is not bool or type(seconds) not in (int, float):
    return None
  try:
    seconds = float(seconds)
  except (ValueError, OverflowError):
    return None
  if not math.isfinite(seconds) or not 0.75 <= seconds <= 1.0:
    return None
  duration = data.get("durationS", LaneChangePolicy().duration_s)
  if type(duration) not in (int, float) or not math.isfinite(duration) or not 3.0 <= duration <= 8.0:
    return None
  guidance = data.get("turnSignalGuidance", True)
  requires_cruise = data.get("autoRequiresCruise", False)
  if type(guidance) is not bool or type(requires_cruise) is not bool:
    return None
  return LaneChangePolicy(enabled, finite_speed, once, auto, delay, width, close_gap, seconds, float(duration), guidance, requires_cruise)


def to_value(policy: LaneChangePolicy) -> dict[str, int | bool | float]:
  if (type(policy.enabled) is not bool or type(policy.one_per_signal) is not bool or
      type(policy.minimum_speed_mps) not in (int, float)):
    raise ValueError("invalid lane-change policy")
  try:
    finite_speed = float(policy.minimum_speed_mps)
  except (ValueError, OverflowError) as error:
    raise ValueError("invalid lane-change speed") from error
  if not math.isfinite(finite_speed) or not 0 <= finite_speed <= MAX_SPEED_MPS:
    raise ValueError("invalid lane-change speed")
  if type(policy.auto_lane_change) is not bool or type(policy.auto_delay_s) not in (int, float) or type(policy.minimum_lane_width_m) not in (int, float):
    raise ValueError("invalid automatic lane-change policy")
  try:
    delay, width = float(policy.auto_delay_s), float(policy.minimum_lane_width_m)
  except (ValueError, OverflowError) as error:
    raise ValueError("invalid automatic lane-change bounds") from error
  if not (math.isfinite(delay) and math.isfinite(width) and 0 <= delay <= 5 and 0 <= width <= 15 * 0.3048):
    raise ValueError("invalid automatic lane-change bounds")
  if type(policy.close_gap) is not bool or type(policy.close_gap_seconds) not in (int, float):
    raise ValueError("invalid lane-change close gap")
  try:
    seconds = float(policy.close_gap_seconds)
  except (ValueError, OverflowError) as error:
    raise ValueError("invalid lane-change close gap") from error
  if not math.isfinite(seconds) or not 0.75 <= seconds <= 1.0:
    raise ValueError("invalid lane-change close gap")
  if type(policy.duration_s) not in (int, float) or not math.isfinite(policy.duration_s) or not 3.0 <= policy.duration_s <= 8.0:
    raise ValueError("invalid lane-change duration")
  if type(policy.turn_signal_guidance) is not bool or type(policy.auto_requires_cruise) is not bool:
    raise ValueError("invalid turn signal guidance")
  return {"version": 6, "enabled": policy.enabled, "minimumSpeedMps": finite_speed,
          "onePerSignal": policy.one_per_signal, "autoLaneChange": policy.auto_lane_change,
          "autoDelayS": delay, "minimumLaneWidthM": width,
          "closeGap": policy.close_gap, "closeGapSeconds": seconds, "durationS": policy.duration_s, "turnSignalGuidance": policy.turn_signal_guidance,
          "autoRequiresCruise": policy.auto_requires_cruise}


def read_saved(params: Any) -> SavedLaneChange:
  try:
    fd = os.open(params.get_param_path(KEY), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
  except FileNotFoundError:
    return SavedLaneChange(None)
  except (AttributeError, OSError, TypeError, ValueError):
    return SavedLaneChange(None, valid=False, readable=False)
  try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      return SavedLaneChange(None, valid=False, readable=False)
    raw = os.read(fd, MAX_BYTES + 1)
  except OSError:
    return SavedLaneChange(None, valid=False, readable=False)
  finally:
    os.close(fd)
  if len(raw) > MAX_BYTES:
    return SavedLaneChange(raw, valid=False, readable=False)
  policy = decode(raw)
  return SavedLaneChange(raw, policy if policy is not None else LaneChangePolicy(), valid=policy is not None)


def effective(saved: SavedLaneChange) -> LaneChangePolicy:
  policy = saved.policy if saved.readable and saved.valid else LaneChangePolicy()
  return replace(policy, minimum_speed_mps=max(MINIMUM_SPEED_MPS, policy.minimum_speed_mps))


class LiveLaneChangePolicy:
  """One canonical document, bounded refresh, safe per-maneuver adoption.

  Disabling assistance is immediate on the next successful refresh. Enabling
  it, or changing maneuver parameters, cannot turn a held signal into a newly
  authorized maneuver or alter an already committed maneuver's pace.
  """
  REFRESH_NS = 1_000_000_000

  def __init__(self, params, *, now_ns=0):
    self.params = params
    saved = read_saved(params)
    self.requested = effective(saved) if saved.readable and saved.valid else self._disabled()
    self.applied = self.requested
    self.last_read_ns = now_ns if type(now_ns) is int and now_ns > 0 else 0

  @staticmethod
  def _disabled():
    return replace(LaneChangePolicy(), enabled=False, auto_lane_change=False, turn_signal_guidance=False)

  def sample(self, now_ns, *, signaling=False, maneuver_active=False):
    if type(now_ns) is not int or now_ns <= 0 or now_ns < self.last_read_ns:
      self.last_read_ns = 0
      self.requested = self.applied = self._disabled()
      return self.applied
    if not self.last_read_ns or now_ns - self.last_read_ns >= self.REFRESH_NS:
      saved = read_saved(self.params)
      self.requested = effective(saved) if saved.readable and saved.valid else self._disabled()
      self.last_read_ns = now_ns
    if not self.requested.enabled:
      self.applied = replace(self.applied, enabled=False, auto_lane_change=False)
    elif not self.requested.auto_lane_change and self.applied.auto_lane_change:
      self.applied = replace(self.applied, auto_lane_change=False)
    if not self.requested.turn_signal_guidance:
      self.applied = replace(self.applied, turn_signal_guidance=False)
    if not signaling and not maneuver_active:
      self.applied = self.requested
    return self.applied
