"""Parked editor for the installed longitudinal configuration."""

from openpilot.common.realtime import DT_CTRL
from openpilot.starpilot.longitudinal.developer_tuning import FIELDS, KEY, LIMIT, PREFIX, decode, replace_tuning, scope, scope_key
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.feature_settings_state import FeatureRow


def rows(owner):
  cp = owner.vehicle_params()
  try:
    vehicle = scope(cp)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return ()
  raw, readable = read_saved(owner.params, KEY, LIMIT)
  try:
    saved = decode(raw).get(scope_key(vehicle), {}) if readable else {}
  except (ValueError, TypeError, OverflowError):
    saved, readable = {}, False
  from openpilot.starpilot.longitudinal.vehicle_policy import policy_for, stopping_decel_rate, stopping_policy_for
  stop_owner = stopping_policy_for(cp, DT_CTRL)
  rate = stop_owner.stopping_decel_rate if stop_owner is not None else stopping_decel_rate(cp, policy_for(cp))
  defaults = {"delay": vehicle[-2], "max_accel": 3.5, "start_accel": 3.5, "stop_accel": vehicle[-1],
              "stopping_rate": rate, "start_speed": .3, "stop_speed": .3}
  allowed = readable and owner.authority("parked_preferences") and owner.authority("preferences")
  return tuple(
    FeatureRow(PREFIX + key, label, f"{saved.get(key, defaults[key]):.2f}", raw,
               available=allowed, step=step, minimum=minimum, maximum=maximum, unit=unit, capability=vehicle,
               vehicle_fingerprint=vehicle[1], default_value="Automatic", default_key=PREFIX + key + ":reset",
               reason="Saved settings are unreadable." if not readable else "Applies next drive." if key in saved else
                      "Automatic. Changes apply next drive.")
    for key, (label, unit, minimum, maximum, step, _) in FIELDS.items()
  )


def apply(owner, request):
  key = request.key.removeprefix(PREFIX)
  action = key.removesuffix(":reset")
  row = next((entry for entry in rows(owner) if entry.key == PREFIX + action), None)
  if (row is None or not row.available or request.expected != row.source or
      request.capability != row.capability or request.vehicle_fingerprint != row.vehicle_fingerprint or
      request.dependencies or request.related_source is not None or request.direction or request.display_unit):
    return False
  try:
    if key != action:
      if request.value != "Automatic":
        return False
      value = None
    else:
      value = float(request.value)
    encoded = replace_tuning(row.source, row.capability, action, value)
  except (ValueError, TypeError, OverflowError):
    return False

  def authorized():
    current = next((entry for entry in rows(owner) if entry.key == row.key), None)
    return current is not None and current.available and current.capability == row.capability

  return commit_exact(owner.params, key=KEY, max_bytes=LIMIT, raw=encoded, expected=row.source,
                      authorized=authorized, temp_prefix=".longitudinal-tuning-").verified
