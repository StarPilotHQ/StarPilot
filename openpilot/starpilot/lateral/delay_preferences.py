"""Vehicle/controller-scoped total steering delay; the estimator remains independent."""
from dataclasses import dataclass
import json
import math

from openpilot.starpilot.saved_source import read_saved

KEY = 'SteeringDelayPreferences'
LIMIT = 16_384
MIN_DELAY, MAX_DELAY = .01, 1.


def scope(cp):
  from opendbc.car.values import PLATFORMS
  platform = PLATFORMS.get(str(cp.carFingerprint))
  kind, tuning = str(cp.steerControlType), str(cp.lateralTuning.which())
  delay = float(cp.steerActuatorDelay) + .2
  if (platform is None or platform.__class__.__module__.split('.')[-2] != str(cp.brand) or
      cp.passive or cp.dashcamOnly or cp.notCar or
      not (kind in ('angle', 'curvature') or kind == 'torque' and tuning in ('torque', 'pid')) or
      not math.isfinite(delay) or not MIN_DELAY <= delay <= MAX_DELAY):
    raise ValueError('Steering delay unavailable for this vehicle')
  return str(cp.brand), str(cp.carFingerprint), kind, tuning, delay


def _unique(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError('Duplicate steering delay field')
    result[key] = value
  return result


def decode(raw):
  if raw is None:
    return {'version': 1, 'vehicles': {}}
  if not raw or len(raw) > LIMIT:
    raise ValueError('Invalid steering delay document size')
  try:
    data = json.loads(raw, object_pairs_hook=_unique)
  except RecursionError as error:
    raise ValueError('Invalid steering delay nesting') from error
  if (not isinstance(data, dict) or set(data) != {'version', 'vehicles'} or
      type(data['version']) is not int or data['version'] != 1 or
      not isinstance(data['vehicles'], dict) or len(data['vehicles']) > 64):
    raise ValueError('Invalid steering delay document')
  for identity, entry in data['vehicles'].items():
    if (not isinstance(identity, str) or not 0 < len(identity) <= 256 or not isinstance(entry, dict) or
        set(entry) != {'scope', 'value'} or not isinstance(entry['scope'], list) or len(entry['scope']) != 5 or
        any(type(v) is not str or not 0 < len(v) <= 256 for v in entry['scope'][:4]) or entry['scope'][1] != identity):
      raise ValueError('Invalid steering delay identity')
    for value in (entry['scope'][4], entry['value']):
      if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not MIN_DELAY <= value <= MAX_DELAY):
        raise ValueError('Invalid total steering delay')
    if entry['scope'][4] is None:
      raise ValueError('Missing vehicle delay basis')
  return data


@dataclass(frozen=True)
class DelaySelection:
  value: float | None = None
  source: str = 'automatic'
  raw: bytes | None = None
  valid: bool = True


def read_delay(params, cp, *, snapshot=None):
  raw, readable = read_saved(params, KEY, LIMIT) if snapshot is None else snapshot
  try:
    current = scope(cp)
    if not readable:
      raise ValueError('Unreadable delay preferences')
    entry = decode(raw)['vehicles'].get(current[1])
    if entry is None:
      from openpilot.starpilot.lateral.gm_geometry_runtime import read_geometry
      legacy = read_geometry(params, cp).profile
      if legacy is not None and not legacy.automatic_delay:
        value = legacy.full_delay.custom_value if legacy.full_delay.mode == 'custom' else legacy.basis[1]
        return DelaySelection(value, 'legacy', raw)
      return DelaySelection(raw=raw)
    if tuple(entry['scope']) != current:
      return DelaySelection(source='changed', raw=raw)
    return DelaySelection(entry['value'], 'automatic' if entry['value'] is None else 'manual', raw)
  except (AttributeError, OSError, TypeError, ValueError, UnicodeError, OverflowError):
    return DelaySelection(source='invalid', raw=raw, valid=False)


def replace_delay(raw, cp, value):
  current = scope(cp)
  data = decode(raw)
  data['vehicles'][current[1]] = {'scope': list(current), 'value': value}
  encoded = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
  decode(encoded)
  return encoded
