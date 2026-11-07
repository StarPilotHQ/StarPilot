"""Guarded, explicit repair of saved Long Planner scalar preferences."""

from __future__ import annotations

from openpilot.starpilot.longitudinal.profile_preferences import (
  FLAGS, KEYS, NAMES, SCALARS, ProfileHealth, read_profile_health,
)
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest, is_long_confirm_action

REPAIR_PREFIX = "long_repair:"
RESET_PREFIX = "long_reset:"
LABELS = {
  "PersonalityProfile": "profile switch", "Follow": "low-speed follow", "FollowHigh": "high-speed follow",
  "JerkAcceleration": "acceleration jerk", "JerkDeceleration": "deceleration jerk",
  "JerkSpeed": "speed jerk", "JerkSpeedDecrease": "speed decrease jerk", "JerkDanger": "danger jerk",
}
PRESET_LABELS = {"selected_profile": "Selected Profile","dom_default": "StarPilot Default", "standard": "Normal", "eco": "Comfort", "sport": "Sport",
                 "sport_plus": "Sport+", "custom": "Custom", "close": "Close", "medium": "Medium", "far": "Far",
                 "traffic": "Traffic", "legacy_close": "Legacy Close", "legacy_medium": "Legacy Medium", "legacy_far": "Legacy Far",
                 "custom_simple": "Custom (Simple)"}
VALUE_HELP = {
  "Follow": "Following time below 45 mph; higher values leave more space. Blends toward the high-speed value by 70 mph.",
  "FollowHigh": "Following time above 70 mph; higher values leave more space. A selected following curve takes priority.",
  "JerkAcceleration": "Resists changes in acceleration while speeding up. Higher values favor smoother changes; 100% is the base weight.",
  "JerkDeceleration": "Resists changes in acceleration while slowing down. Higher values favor smoother changes; 100% is the base weight.",
  "JerkSpeed": "Smooths acceleration changes while speeding up. Higher values favor gradual responses; 100% is the base weight.",
  "JerkSpeedDecrease": "Smooths acceleration changes while slowing down. Higher values favor gradual responses; 100% is the base weight.",
  "JerkDanger": "Weights the planner's penalty for entering a lead vehicle's buffer. Higher values discourage smaller gaps; not a safety limit.",
}
CATEGORY_HELP = {
  "acceleration": "Limits acceleration by speed. Comfort is gentler; Sport and Sport+ allow stronger acceleration. " +
                  "Selected Profile follows the global choice. " +
                  "An explicit choice overrides it; StarPilot Default keeps vehicle behavior.",
  "braking": "Comfort/Normal/Sport (m/s²): global 0.6/1.2/2.4; explicit personality 0.5/1.0/2.0. " +
             "Selected Profile inherits global values. These separate scales preserve existing tuning. " +
             "StarPilot Default keeps native cruise braking. Lead and stop braking retain priority.",
  "following": "Sets following time by speed and overrides the low/high-speed values. Default uses those values; higher times leave more space.",
}
# A personality page's own Acceleration / Braking choice (Driving Personalities).
CATEGORY_CHOICE_HELP = {
  "acceleration": "Comfort is gentler; Sport and Sport+ accelerate harder. Custom lets you set the limit at each speed.",
  "braking": "Comfort brakes more gently, Sport more firmly. Custom lets you set the braking at each speed.",
}


def preset_label(value: str) -> str:
  return PRESET_LABELS.get(value, value)


def preset_value(value: str) -> str:
  # Keep older clients' stored tokens valid while presenting readable choices.
  value = {"Standard": "Normal", "Eco": "Comfort"}.get(value, value)
  return next((key for key, label in PRESET_LABELS.items() if value == label), value)


def long_confirm_question(row: FeatureRow) -> str:
  if row.key == REPAIR_PREFIX + "CustomPersonalities":
    return "Restore the invalid saved profiles switch to Off? This will not enable tuning."
  if row.key.startswith(REPAIR_PREFIX):
    return f"{row.label}? If saved profiles are On, valid settings may resume. The saved switch will not change."
  return (f"Reset {row.label} to its seven default follow and jerk values? Saved curves, profile switches, " +
          "and other profiles stay as they are. Saved tuning may resume if its switch was On; " +
          "a failed reset leaves the switch Off.")


class LongProfileFeature:
  def __init__(self, owner):
    self.owner = owner
    self.params = owner.params

  def capability(self) -> tuple | None:
    cp = self.owner.vehicle_params()
    try:
      if (cp is None or not cp.carFingerprint or not self.owner.longitudinal_available() or
          cp.passive or cp.notCar or cp.dashcamOnly):
        return None
      return (str(cp.carFingerprint), self.owner.longitudinal_available(), bool(cp.pcmCruise and not self.owner.configuration_longitudinal()),
              bool(cp.passive), bool(cp.notCar), bool(cp.dashcamOnly),
              str(cp.carVin) if getattr(cp, "carVin", None) else None)
    except (AttributeError, TypeError, ValueError):
      return None

  def curve_capability(self) -> tuple | None:
    base = self.capability()
    cp = self.owner.vehicle_params()
    transmission = getattr(cp, "transmissionType", None) if cp is not None else None
    return None if base is None or transmission is None else (base, transmission)

  def dependencies(self, health: ProfileHealth) -> tuple[tuple[str, bytes | None], ...]:
    return tuple((key, health.values[key].raw) for key in KEYS)

  def rows(self, name: str, health: ProfileHealth, allowed: bool, *, repair_allowed: bool | None = None) -> list[FeatureRow]:
    repair_allowed = allowed if repair_allowed is None else repair_allowed
    cap = self.capability()
    dependencies = self.dependencies(health)
    rows = []
    for key in SCALARS[name]:
      if not health.values[key].valid:
        suffix = key.removeprefix(name.title())
        readable = health.values[key].readable
        rows.append(FeatureRow(REPAIR_PREFIX + key, "Restore " + LABELS[suffix] + " default",
                               "Invalid saved value · restore registry default" if readable else
                               "Saved source unreadable or too large", health.values[key].raw,
                               available=repair_allowed and cap is not None and readable,
                               capability=cap, dependencies=dependencies))
    rows.append(FeatureRow(RESET_PREFIX + name, "Reset Profile to Default", "",
                           health.values[SCALARS[name][0]].raw,
                           available=repair_allowed and cap is not None and
                                     health.values["LongitudinalPersonalityProfiles"].valid and
                                     all(value.readable for value in health.values.values()),
                           reason="Repair invalid document or unreadable source first" if
                                  not health.values["LongitudinalPersonalityProfiles"].valid or
                                  not all(value.readable for value in health.values.values()) else "",
                           capability=cap, dependencies=dependencies))
    return rows

  def _fresh(self, request: FeatureSettingsRequest, expected: dict[str, bytes | None]) -> bool:
    if (not request.vehicle_fingerprint or self.owner.vehicle_fingerprint() != request.vehicle_fingerprint or
        request.capability is None or self.capability() != request.capability or not self.owner.authority("long") or
        is_long_confirm_action(request.key) and not self.owner.authority("parked_preferences")):
      return False
    health = read_profile_health(self.params)
    return all(health.values[key].readable and health.values[key].raw == raw for key, raw in expected.items())

  def apply(self, request: FeatureSettingsRequest) -> bool:
    if not request.confirmation or not is_long_confirm_action(request.key) or request.value != "confirm":
      return False
    if not self.owner.authority("parked_preferences"):
      return False
    if tuple(key for key, _ in request.dependencies) != KEYS:
      return False
    expected = dict(request.dependencies)
    if len(expected) != len(KEYS) or not self._fresh(request, expected):
      return False
    health = read_profile_health(self.params)
    try:
      if request.key.startswith(REPAIR_PREFIX):
        key = request.key[len(REPAIR_PREFIX):]
        if key not in ("CustomPersonalities", *FLAGS, *(k for name in NAMES for k in SCALARS[name])) or health.values[key].valid:
          return False
        if request.expected != expected[key]:
          return False
        default = self.params.get_default_value(key)
        if key == "CustomPersonalities":
          default = False
        if default is None or not self._fresh(request, expected):
          return False
        if key == "CustomPersonalities" or key in FLAGS:
          self.params.put_bool(key, bool(default), block=True)
        else:
          self.params.put(key, float(default), block=True)
        return read_profile_health(self.params).values[key].valid
      name = request.key[len(RESET_PREFIX):]
      if name not in NAMES or request.expected != expected[SCALARS[name][0]]:
        return False
      master = health.values["CustomPersonalities"]
      if not health.values["LongitudinalPersonalityProfiles"].valid:
        return False
      # The old global switch no longer gates tuning (Following = Custom does); it is only paused if it was On.
      was_on = master.valid and master.value is True
      if was_on:
        if not self._fresh(request, expected):
          return False
        self.params.put_bool("CustomPersonalities", False, block=True)
        expected["CustomPersonalities"] = b"0"
        if not self._fresh(request, expected):
          return False
      for key in SCALARS[name]:
        if not self._fresh(request, expected):
          return False
        self.params.remove(key)
        expected[key] = None
        if not self._fresh(request, expected):
          return False
      updated = read_profile_health(self.params)
      if not all(updated.values[key].valid and updated.values[key].raw is None for key in SCALARS[name]):
        return False
      if was_on:
        if not updated.dependencies_valid or not self._fresh(request, expected):
          return False
        self.params.put_bool("CustomPersonalities", True, block=True)
        return read_profile_health(self.params).master_on
      return True
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError):
      return False
