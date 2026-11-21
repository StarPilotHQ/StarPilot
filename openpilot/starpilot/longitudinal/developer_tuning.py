"""Saved host longitudinal adjustments, scoped to the enrolled vehicle configuration."""

import json
import math

from openpilot.starpilot.saved_source import read_saved

KEY = "LongitudinalDeveloperPreferences"
LIMIT = 16_384
PREFIX = "longdev:"
FIELDS = {
  "delay": ("Actuator Delay", "s", .01, 1., .01, "Compensates for the delay between an acceleration request and vehicle response."),
  "max_accel": ("Maximum Acceleration", "m/s²", .1, 3.5, .1, "Caps positive acceleration. Vehicle and acceleration profile limits still apply."),
  "start_accel": ("Start Acceleration", "m/s²", .1, 3.5, .01, "Caps positive acceleration when pulling away below the Start Speed."),
  "stop_accel": ("Stop Acceleration", "m/s²", -4., -.05, .01, "Sets the braking target for the shared stopping ramp."),
  "stopping_rate": ("Stopping Rate", "m/s²/s", .05, 2., .01, "Sets how quickly the shared stopping ramp adds braking."),
  "start_speed": ("Start Speed", "m/s", .01, 1., .01, "Sets the speed below which the Start Acceleration cap applies."),
  "stop_speed": ("Stop Speed", "m/s", .01, 1., .01, "Sets the low-speed threshold for the planner's ordinary stop detection."),
}


def scope(cp):
  from opendbc.car.values import PLATFORMS
  platform = PLATFORMS.get(str(cp.carFingerprint))
  if (platform is None or platform.__class__.__module__.split('.')[-2] != str(cp.brand) or
      not cp.openpilotLongitudinalControl or cp.passive or cp.dashcamOnly or cp.notCar or
      not cp.safetyConfigs or not math.isfinite(cp.longitudinalActuatorDelay) or not math.isfinite(cp.stopAccel)):
    raise ValueError("No StarPilot longitudinal configuration")
  return (str(cp.brand), str(cp.carFingerprint), int(cp.flags), bool(cp.pcmCruise), int(cp.alternativeExperience),
          tuple((str(s.safetyModel), int(s.safetyParam)) for s in cp.safetyConfigs),
          float(cp.longitudinalActuatorDelay), float(cp.stopAccel))


def _unique(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError("Duplicate longitudinal preference")
    result[key] = value
  return result


def _number(key, value):
  _, _, low, high, _, _ = FIELDS[key]
  if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
    raise ValueError("Longitudinal adjustment is outside its range")
  return float(value)


def decode(raw):
  if raw is None:
    return {}
  if len(raw) > LIMIT:
    raise ValueError("Oversized longitudinal preferences")
  try:
    document = json.loads(raw, object_pairs_hook=_unique)
  except RecursionError as error:
    raise ValueError("Invalid longitudinal preferences nesting") from error
  if not isinstance(document, dict) or len(document) > 64:
    raise ValueError("Invalid longitudinal preferences")
  for identity, entry in document.items():
    if (not 0 < len(identity) <= 1024 or not isinstance(entry, dict) or not entry or
        any(key not in FIELDS for key in entry)):
      raise ValueError("Invalid longitudinal preferences entry")
    for key, value in entry.items():
      _number(key, value)
  return document


def scope_key(vehicle):
  return json.dumps(vehicle, separators=(",", ":"), allow_nan=False)


def read_tuning(params, cp):
  try:
    identity = scope_key(scope(cp))
    raw, readable = read_saved(params, KEY, LIMIT)
    return decode(raw).get(identity, {}) if readable else {}
  except (AttributeError, KeyError, OSError, TypeError, ValueError, OverflowError):
    return {}


def replace_tuning(raw, vehicle, key, value):
  document = decode(raw)
  identity = scope_key(vehicle)
  entry = document.get(identity, {})
  if value is None:
    entry.pop(key, None)
  else:
    entry[key] = _number(key, value)
  if entry:
    document[identity] = entry
  else:
    document.pop(identity, None)
  encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
  decode(encoded)
  return encoded
