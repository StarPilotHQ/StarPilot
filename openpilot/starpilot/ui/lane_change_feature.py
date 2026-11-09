"""Native saved Lane Change Assist rows and source-bound edits."""

from collections.abc import Callable
from dataclasses import replace
import json
import math
import os
import stat

from openpilot.common.constants import CV
from openpilot.starpilot.lateral.auto_lane_change import steering_capable
from openpilot.starpilot.lateral.lane_change_preferences import (
  KEY, MAX_BYTES, MAX_SPEED_MPS, MINIMUM_SPEED_MPS, effective, LaneChangePolicy, SavedLaneChange, read_saved, to_value,
)
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest


GUIDANCE = "lane_change:turn_signal_guidance"
ENABLED = "lane_change:enabled"
SPEED = "lane_change:speed"
ONE = "lane_change:one"
AUTO = "lane_change:auto"
REQUIRE_CRUISE = "lane_change:require_cruise"
DELAY = "lane_change:delay"
WIDTH = "lane_change:width"
CLOSE = "lane_change:close_gap"
GAP = "lane_change:close_gap_seconds"
PACE = "lane_change:pace"
RESET = "lane_change:reset"


def confirm_question(request: FeatureSettingsRequest) -> str:
  if request.key == RESET:
    return "Restore the default lane-change assistance, signal timing, pace and gap settings?"
  if request.key == ENABLED and request.value == "Off":
    return "Turn off lane-change assistance? The current assistance cancels after the preference refresh."
  if request.key == AUTO and request.value == "Off":
    return "Turn off automatic lane changes? New automatic triggers stop after the preference refresh."
  label = {GUIDANCE: "turn signal guidance", ENABLED: "lane-change assistance", AUTO: "automatic lane changes",
           REQUIRE_CRUISE: "cruise requirement for automatic lane changes", SPEED: "minimum lane-change speed",
           ONE: "one lane change per signal", PACE: "steering pace", DELAY: "signal delay",
           WIDTH: "minimum adjacent lane width", CLOSE: "Close lane-change gap", GAP: "lane-change following time"}[request.key]
  unit = request.display_unit
  if not unit:
    unit = "seconds" if request.key in (DELAY, GAP) else "feet" if request.key == WIDTH else ""
  value = f"{request.value} {unit}".rstrip()
  timing = ("Changes refresh during this drive." if request.key in (CLOSE, GAP) else
            "Changes apply after the current signal and maneuver end.")
  checks = ' Lane and blindspot checks remain required.' if request.key == AUTO and request.value == 'On' else ''
  return f"Set {label} to {value}? {timing}{checks}"
KEYS = frozenset((GUIDANCE, ENABLED, SPEED, ONE, AUTO, REQUIRE_CRUISE, DELAY, WIDTH, CLOSE, GAP, PACE, RESET))


def _unit_source(params) -> tuple[bytes | None, bool]:
  try:
    fd = os.open(params.get_param_path("IsMetric"), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
  except FileNotFoundError:
    return None, True
  except (AttributeError, OSError, TypeError, ValueError):
    return None, False
  try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      return None, False
    raw = os.read(fd, 3)
  except OSError:
    return None, False
  finally:
    os.close(fd)
  return raw, raw in (b"0", b"1")


class LaneChangeFeature:
  def __init__(self, params, authority: Callable[[str], bool], fingerprint: Callable[[], str | None], vehicle_params: Callable[[], object | None],
               *, configuration_longitudinal: Callable[[], bool] = lambda: False):
    self.params = params
    self.authority = authority
    self.fingerprint = fingerprint
    self.vehicle_params = vehicle_params
    self.configuration_longitudinal = configuration_longitudinal

  def longitudinal_available(self):
    cp = self.vehicle_params()
    return bool(cp is not None and (cp.openpilotLongitudinalControl or self.configuration_longitudinal()))

  def capability(self) -> tuple | None:
    cp = self.vehicle_params()
    try:
      if not steering_capable(cp):
        return None
      return (str(cp.carFingerprint), str(cp.brand), str(cp.steerControlType),
              bool(cp.notCar), bool(cp.passive), bool(cp.dashcamOnly), self.longitudinal_available(),
              str(cp.carVin) if getattr(cp, "carVin", None) else None)
    except (AttributeError, TypeError, ValueError):
      return None

  def rows(self, parked: bool) -> list[FeatureRow]:
    saved = read_saved(self.params)
    unit_raw, unit_valid = _unit_source(self.params)
    metric = unit_raw == b"1"
    unit = "km/h" if metric else "mph"
    multiplier = 3.6 if metric else 1 / CV.MPH_TO_MS
    capable = self.capability()
    allowed = capable is not None and self.authority("lane_change") and saved.readable
    fingerprint = self.fingerprint()
    common = FeatureRow("", "", "", source=saved.raw, vehicle_fingerprint=fingerprint, capability=capable)
    if not saved.valid:
      reason = "Saved preference cannot be read" if not saved.readable else "Invalid saved preferences; restore defaults"
      return [replace(common, key=RESET, label="Restore Lane Change defaults", value="StarPilot driver-nudged behavior",
                      available=parked and allowed and self.authority("parked_preferences"), reason=reason)]
    policy = effective(saved)
    long_available = self.longitudinal_available()
    speed = round(policy.minimum_speed_mps * multiplier, 3)
    rows = [replace(common, key=PACE, label="Lane Change Speed", value=str(round(1.0 + (8.0 - policy.duration_s) * 9.0 / 5.0, 2)),
                    step=1.0, minimum=1.0, maximum=10.0, unit="", available=allowed,
                    reason="Sets how quickly steering moves into the adjacent lane. Higher values change lanes " +
                           "more quickly; lower values make steering gentler."),
            replace(common, key=ENABLED, label="Allow lane changes", value="On" if policy.enabled else "Off",
                    choices=("Off", "On"), available=allowed,
                    reason="Enables lane-change assistance after you signal. With Automatic Lane Changes off, give the wheel a nudge to begin."),
            replace(common, key=SPEED, label="Minimum lane-change speed", value=str(speed), step=1.0, minimum=MINIMUM_SPEED_MPS * multiplier,
                    maximum=math.floor(MAX_SPEED_MPS * multiplier * 1000) / 1000,
                    unit=unit if unit_valid else "", available=allowed and unit_valid,
                    reason="Sets the lowest speed at which lane-change assistance can begin." if unit_valid else "Saved speed units unavailable",
                    dependencies=(("IsMetric", unit_raw),), display_unit=unit if unit_valid else ""),
            replace(common, key=ONE, label="One change per signal", value="On" if policy.one_per_signal else "Off",
                    choices=("Off", "On"), available=allowed,
                    reason="Allows one assisted lane change until you release the turn signal."),
            replace(common, key=AUTO, label="Automatic Lane Changes", value="On" if policy.auto_lane_change else "Off",
                    choices=("Off", "On"), available=allowed,
                    reason="Starts an assisted lane change after the turn signal delay without a steering nudge, " +
                           "when the adjacent lane and blind spot are clear."),
            replace(common, key=REQUIRE_CRUISE, label="Require Cruise for Automatic Lane Changes",
                    value="On" if policy.auto_requires_cruise else "Off", choices=("Off", "On"), available=allowed,
                    reason="Requires factory cruise or openpilot speed control to be engaged for a nudgeless start. " +
                           "With cruise off, a wheel nudge still works. Engage cruise before signaling."),
            replace(common, key=DELAY, label="Automatic lane-change delay", value=str(round(policy.auto_delay_s, 1)),
                    step=0.1, minimum=0.0, maximum=5.0, unit="s", available=allowed,
                    reason="Time to wait after signaling before an automatic lane change; lane and blindspot checks still apply"),
            replace(common, key=WIDTH, label="Minimum adjacent lane width", value=str(round(policy.minimum_lane_width_m / 0.3048, 1)),
                    step=0.1, minimum=0.0, maximum=15.0, unit="ft", available=allowed,
                    reason="Minimum detected adjacent lane width for an automatic lane change; narrower or uncertain lanes require a driver nudge"),
            replace(common, key=CLOSE, label="Close lane-change gap", value="On" if policy.close_gap else "Off",
                    choices=("Off", "On"), available=allowed and long_available,
                    reason="Temporarily reduces following time during a lane change when the lane and lead gap are clear; requires StarPilot speed control"),
            replace(common, key=GAP, label="Lane-change follow gap", value=str(round(policy.close_gap_seconds, 2)),
                    step=0.05, minimum=0.75, maximum=1.0, unit="s", available=allowed and long_available,
                    reason="Following time while Close lane-change gap is active. Higher values leave more space; returns to the normal gap afterward.")]

    defaults = LaneChangePolicy()
    targets = {ENABLED: "On" if defaults.enabled else "Off", ONE: "On" if defaults.one_per_signal else "Off",
               AUTO: "On" if defaults.auto_lane_change else "Off", REQUIRE_CRUISE: "On" if defaults.auto_requires_cruise else "Off",
               CLOSE: "On" if defaults.close_gap else "Off",
               SPEED: str(round(defaults.minimum_speed_mps * multiplier, 3)), DELAY: str(defaults.auto_delay_s),
               WIDTH: str(defaults.minimum_lane_width_m), GAP: str(defaults.close_gap_seconds),
               PACE: str(round(1.0 + (8.0 - defaults.duration_s) * 9.0 / 5.0, 2))}
    return [replace(row, default_value=targets.get(row.key)) for row in rows]

  def guidance_row(self, parked):
    saved = read_saved(self.params)
    return FeatureRow(GUIDANCE, "Turn Signal Guidance", "On" if saved.policy.turn_signal_guidance else "Off",
                      source=saved.raw, choices=("Off", "On"),
                      available=saved.valid and saved.readable and self.capability() is not None and self.authority("lane_change"),
                      reason="Use your turn signal to help the model choose a turning path at intersections.",
                      vehicle_fingerprint=self.fingerprint(), capability=self.capability(), default_value="On")

  def _fresh(self, request: FeatureSettingsRequest) -> SavedLaneChange | None:
    if (not request.vehicle_fingerprint or self.fingerprint() != request.vehicle_fingerprint or
        request.capability is None or self.capability() != request.capability or not self.authority("lane_change")):
      return None
    if request.key == RESET and not self.authority("parked_preferences"):
      return None
    saved = read_saved(self.params)
    if not saved.readable or saved.raw != request.expected:
      return None
    if request.key == SPEED:
      raw, valid = _unit_source(self.params)
      if not valid or request.dependencies != (("IsMetric", raw),) or request.display_unit != ("km/h" if raw == b"1" else "mph"):
        return None
    return saved

  @staticmethod
  def _desired(saved: SavedLaneChange, request: FeatureSettingsRequest) -> LaneChangePolicy | None:
    if request.key == RESET:
      return LaneChangePolicy() if not saved.valid and request.value == "confirm" else None
    if not saved.valid:
      return None
    if request.key in (GUIDANCE, ENABLED, ONE, AUTO, REQUIRE_CRUISE, CLOSE) and request.value in ("On", "Off"):
      field = {GUIDANCE: "turn_signal_guidance", ENABLED: "enabled", ONE: "one_per_signal", AUTO: "auto_lane_change",
               REQUIRE_CRUISE: "auto_requires_cruise", CLOSE: "close_gap"}[request.key]
      return replace(saved.policy, **{field: request.value == "On"})
    if request.key == PACE:
      try:
        value = float(request.value)
      except ValueError:
        return None
      return replace(saved.policy, duration_s=8.0 - (value - 1.0) * 5.0 / 9.0) if math.isfinite(value) and 1.0 <= value <= 10.0 else None
    if request.key == GAP:
      try:
        value = float(request.value)
      except ValueError:
        return None
      return replace(saved.policy, close_gap_seconds=value) if math.isfinite(value) and 0.75 <= value <= 1.0 else None
    if request.key in (DELAY, WIDTH):
      try:
        value = float(request.value)
      except ValueError:
        return None
      if math.isfinite(value) and 0 <= value <= (5 if request.key == DELAY else 15):
        return replace(saved.policy, **{("auto_delay_s" if request.key == DELAY else "minimum_lane_width_m"):
                                        value if request.key == DELAY else value * 0.3048})
    if request.key == SPEED:
      try:
        value = float(request.value)
      except ValueError:
        return None
      mps = value / 3.6 if request.display_unit == "km/h" else value * CV.MPH_TO_MS
      if math.isfinite(mps) and MINIMUM_SPEED_MPS <= mps <= MAX_SPEED_MPS:
        return replace(saved.policy, minimum_speed_mps=mps)
    return None

  def apply(self, request: FeatureSettingsRequest) -> bool:
    if request.key not in KEYS or not request.confirmation:
      return False
    if request.key == RESET and not self.authority("parked_preferences"):
      return False
    def authorized() -> bool:
      if request.key in (CLOSE, GAP):
        cp = self.vehicle_params()
        if cp is None or not self.longitudinal_available():
          return False
      return self._fresh(request) is not None

    if not authorized():
      return False
    saved = self._fresh(request)
    if saved is None:
      return False
    desired = self._desired(saved, request)
    if desired is None:
      return False
    try:
      raw = json.dumps(to_value(desired), separators=(",", ":"), sort_keys=True, allow_nan=False).encode()
    except (OverflowError, TypeError, ValueError):
      return False
    return commit_exact(self.params, key=KEY, max_bytes=MAX_BYTES, raw=raw, expected=request.expected,
                        authorized=authorized, temp_prefix=".lane-change-").verified
