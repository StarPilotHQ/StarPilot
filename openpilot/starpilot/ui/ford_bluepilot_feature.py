"""BluePilot lateral settings for Fords, shared by Galaxy and the native feature presentation.

Ranges and the controller's defaults come from the library's manifest, so a setting can't drift from what the
controller reads. BluePilot Lateral, Primary Control and Use Pinion Yaw Sensor change while parked; the first and
last apply at the next vehicle startup. The tuning rows change any time: the library reads them every frame."""
import math
from dataclasses import dataclass

from opendbc.bluepilot_lateral.core.values_ext import FORD_PINION_GEOMETRY_INDEX
from opendbc.bluepilot_lateral.hosts.starpilot.safety_param import lateral_inputs_required
from opendbc.bluepilot_lateral.params.manifest import SETTINGS
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.ui.feature_settings_state import FeatureRow

MANIFEST = {setting.key: setting for setting in SETTINGS}
ENABLE = "disable_BP_lat_UI"  # stored inverted: "0" is on
MODE = "FordPrefLateralControl"
PINION = "FordPrefSteerAngleCurvature"
SWITCH = ("Off", "On")
MODES = ("Curvature", "Angle")
CURVATURE, ANGLE = 0, 1


@dataclass(frozen=True)
class Tuning:
  key: str
  label: str
  mode: int  # shown in this primary control mode only
  needs: tuple[str, ...] = ()  # switches that must be on for the row to change anything
  step: float = 0.05


TUNING = (
  Tuning("enable_human_turn_detection_curv", "Human Turn Detection", CURVATURE),
  Tuning("lane_change_factor_high_curv", "Lane Change Factor", CURVATURE),
  Tuning("enable_lane_positioning_curv", "Lane Positioning", CURVATURE),
  Tuning("custom_path_offset_curv", "In-Lane Offset", CURVATURE, ("enable_lane_positioning_curv",)),
  Tuning("enable_lane_full_mode_curv", "Lanefull Mode", CURVATURE, ("enable_lane_positioning_curv",)),
  Tuning("LC_PID_gain_UI_curv", "Centering Gain", CURVATURE, ("enable_lane_positioning_curv",), step=0.5),
  Tuning("custom_profile_curv", "Custom Tuning Profile", CURVATURE),
  Tuning("pc_blend_ratio_low_C_UI_curv", "Predicted Blend, Low Curvature", CURVATURE, ("custom_profile_curv",)),
  Tuning("pc_blend_ratio_high_C_UI_curv", "Predicted Blend, High Curvature", CURVATURE, ("custom_profile_curv",)),
  Tuning("FordLowSpeedFactor_ang", "Low Speed Factor", ANGLE, step=0.01),
  Tuning("FordHighSpeedFactor_ang", "High Speed Factor", ANGLE, step=0.01),
  Tuning("FordHighSpeedDampening_ang", "High Speed Straight Factor", ANGLE, step=0.01),
  Tuning("lane_change_factor_high_ang", "Lane Change Factor", ANGLE),
  Tuning("enable_lane_positioning_ang", "Lane Positioning", ANGLE),
  Tuning("custom_path_offset_ang", "In-Lane Offset", ANGLE, ("enable_lane_positioning_ang",), step=0.01),
  Tuning("lane_centering_strength_ang", "Lane Centering Strength", ANGLE, ("enable_lane_positioning_ang",)),
)
TUNING_BY_KEY = {row.key: row for row in TUNING}
KEYS = frozenset((ENABLE, MODE, PINION, *TUNING_BY_KEY))


def is_switch(key):
  return MANIFEST[key].type is bool or key == "custom_profile_curv"


class FordBluePilotFeature:
  def __init__(self, owner):
    self.owner = owner

  def capability(self):
    cp = self.owner.vehicle_params()
    try:
      if cp is None or not lateral_inputs_required(cp) or cp.passive or cp.dashcamOnly or cp.notCar:
        return None
      return (str(cp.carFingerprint), int(cp.flags), int(cp.alternativeExperience), bool(cp.openpilotLongitudinalControl),
              tuple((str(c.safetyModel), int(c.safetyParam)) for c in cp.safetyConfigs))
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None

  def read(self, key):
    """(number or None if invalid, saved raw bytes, readable). An unset key reads its params_keys.h default."""
    raw, readable = read_saved(self.owner.params, key, 16)
    if not readable:
      return None, raw, False
    try:
      if raw is None:
        number = self.default(key)
      else:
        number = float(raw.decode())
    except (TypeError, ValueError, UnicodeDecodeError):
      return None, raw, True
    low, high = MANIFEST[key].range or (0.0, 1.0)
    valid = math.isfinite(number) and low <= number <= high and (not is_switch(key) and key != MODE or number in (0.0, 1.0))
    return (number if valid else None), raw, True

  def on(self):
    number, _, _ = self.read(ENABLE)
    return number == 0.0

  def mode(self):
    number, _, _ = self.read(MODE)
    return ANGLE if number == 1.0 else CURVATURE

  def dependencies(self, key):
    safe, safe_readable = read_saved(self.owner.params, "SafeMode", 8)
    if not safe_readable:
      return None
    sources = [("SafeMode", safe)]
    if key != ENABLE:
      sources.append((ENABLE, read_saved(self.owner.params, ENABLE, 16)[0]))
    if key in TUNING_BY_KEY:
      sources.append((MODE, read_saved(self.owner.params, MODE, 16)[0]))
      sources += [(need, read_saved(self.owner.params, need, 16)[0]) for need in TUNING_BY_KEY[key].needs]
    return tuple(sources)

  def row(self, key, parked):
    capability = self.capability()
    if capability is None or key not in KEYS:
      return None
    if key == PINION and capability[0] not in FORD_PINION_GEOMETRY_INDEX:
      return None
    number, raw, readable = self.read(key)
    dependencies = self.dependencies(key)
    safe_off = dependencies is not None and dependencies[0][1] in (None, b"0")
    parked_ok = parked and self.owner.authority("parked_preferences")
    default = self.default(key)

    if key == ENABLE:
      value = SWITCH[1] if number == 0.0 else SWITCH[0]
      return FeatureRow(key, "BluePilot Lateral", value if number is not None else "Invalid saved value", raw,
                        choices=SWITCH, available=parked_ok and readable and safe_off, capability=capability,
                        dependencies=dependencies or (), default_value=SWITCH[0] if default != 0.0 else SWITCH[1],
                        repair_value="" if number is not None else SWITCH[0],
                        reason="" if safe_off else "Safe Mode must be off")
    gate = "" if self.on() else "Turn on BluePilot Lateral first"
    if key in (MODE, PINION):
      labels = MODES if key == MODE else SWITCH
      return FeatureRow(key, "Primary Control" if key == MODE else "Use Pinion Yaw Sensor",
                        labels[int(number)] if number is not None else "Invalid saved value", raw, choices=labels,
                        available=parked_ok and readable and safe_off and not gate, capability=capability,
                        dependencies=dependencies or (), default_value=labels[int(default)],
                        repair_value="" if number is not None else labels[int(default)],
                        reason=gate or ("" if parked else "Change while parked"))

    tuning = TUNING_BY_KEY[key]
    if tuning.mode != self.mode():
      return None
    needs = next((TUNING_BY_KEY[need].label for need in tuning.needs if self.read(need)[0] != 1.0), None)
    reason = gate or (f"Turn on {needs} first" if needs else "")
    # an invalid saved value can still be repaired to the default
    available = self.owner.authority("preferences") and readable and not reason
    if is_switch(key):
      shown = SWITCH[int(default)]
      return FeatureRow(key, tuning.label, SWITCH[int(number)] if number is not None else "Invalid saved value", raw,
                        choices=SWITCH, available=available, capability=capability, dependencies=dependencies or (),
                        default_value=shown, repair_value="" if number is not None else shown, reason=reason)
    low, high = MANIFEST[key].range
    shown = str(round(default, 4))
    return FeatureRow(key, tuning.label, str(round(number, 4)) if number is not None else "Invalid saved value", raw,
                      step=tuning.step if number is not None else 0.0, minimum=low, maximum=high, available=available,
                      capability=capability, dependencies=dependencies or (), default_value=shown,
                      repair_value="" if number is not None else shown, reason=reason)

  def default(self, key):
    """The params_keys.h default as a number (StarPilot's own: BluePilot lateral is off by default)."""
    default = self.owner.params.get_default_value(key)
    return float(int(default)) if isinstance(default, bool) else float(default)

  def rows(self, parked):
    if self.capability() is None:
      return ()
    rows = (self.row(ENABLE, parked), self.row(MODE, parked), self.row(PINION, parked),
            *(self.row(tuning.key, parked) for tuning in TUNING))
    return tuple(row for row in rows if row is not None)

  def encode(self, key, value):
    """The bytes to save for a requested display value, or None if it isn't one of the row's values."""
    if key == ENABLE:
      return {SWITCH[1]: b"0", SWITCH[0]: b"1"}.get(value)
    if key == MODE:
      return {MODES[0]: b"0", MODES[1]: b"1"}.get(value)
    if is_switch(key):
      return {SWITCH[0]: b"0", SWITCH[1]: b"1"}.get(value)
    try:
      number = float(value)
    except ValueError:
      return None
    low, high = MANIFEST[key].range
    step = TUNING_BY_KEY[key].step
    if not math.isfinite(number) or not low <= number <= high or abs((number - low) / step - round((number - low) / step)) > 1e-6:
      return None
    return str(round(number, 4)).encode()

  def apply(self, request):
    key = request.key
    if key not in KEYS or request.related_source is not None or request.display_unit or request.direction:
      return False
    raw = self.encode(key, request.value)
    if raw is None:
      return False

    def authorized():
      row = self.row(key, parked=True)
      return (row is not None and row.available and bool(request.vehicle_fingerprint) and
              self.owner.vehicle_fingerprint() == request.vehicle_fingerprint and
              row.capability == request.capability and row.dependencies == request.dependencies and
              row.source == request.expected)

    result = commit_exact(self.owner.params, key=key, max_bytes=16, raw=raw, expected=request.expected,
                          authorized=authorized, temp_prefix=".ford-bluepilot-")
    return result.committed and result.verified
