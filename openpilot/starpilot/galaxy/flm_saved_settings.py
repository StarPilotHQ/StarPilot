"""Read-only saved settings for the current vehicle, never startup authority."""

from capnp import KjException
from opendbc.car.structs import car
from openpilot.starpilot.flm.learned_lifecycle import decode
from openpilot.starpilot.flm.offroad_store import KEY, MAX_BYTES, NAMESPACE
from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.schema_cache import inspect_cache


def snapshot(params, preference):
  result = {'status': 'unavailable', 'historicalOnly': True, 'profiles': []}
  if not preference.get('valid') or type(preference.get('parked')) is not bool:
    return result
  key = 'CarParamsPersistent' if preference['parked'] else 'CarParams'
  try:
    cp_raw, cp_readable = read_saved(params, key, 1024 * 1024)
    raw, readable = read_saved(params, KEY, MAX_BYTES)
    if not cp_readable or cp_raw is None or not readable:
      return result
    payload = inspect_cache(key, cp_raw).payload if preference['parked'] else cp_raw
    if payload is None:
      return result
    with car.CarParams.from_bytes(payload, traversal_limit_in_words=1024 * 1024 // 8) as cp:
      if cp.notCar or str(cp.carFingerprint) != preference.get('vehicle'):
        return result
      identity = car_params_digest(cp)
    rows = decode(raw).get(NAMESPACE, {}).values()
    profiles, seen = [], set()
    for row in sorted(rows, key=lambda value: value['completed_wall_ns'], reverse=True):
      if row['scope']['cp_sha256'] != identity:
        continue
      values = [[name, value] for name, value in row['values'].items() if value != 0]
      marker = (row['completed_wall_ns'], row['state'], tuple(tuple(pair) for pair in values))
      if not values or marker in seen:
        continue
      seen.add(marker)
      profiles.append({'completedAt': row['completed_wall_ns'] // 1_000_000, 'state': row['state'], 'values': values})
    if (read_saved(params, key, 1024 * 1024) != (cp_raw, cp_readable)
        or read_saved(params, KEY, MAX_BYTES) != (raw, readable)):
      return result
    return {'status': 'readable', 'historicalOnly': True, 'profiles': profiles}
  except (OSError, RuntimeError, AttributeError, KeyError, TypeError, ValueError, OverflowError, KjException):
    return result
