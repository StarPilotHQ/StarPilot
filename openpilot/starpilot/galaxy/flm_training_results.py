"""Read-only optional-learner progress, separate from parked FLM operations."""
import time

from openpilot.starpilot.flm.training_report import read_report, report_path


class FlmTrainingResults:
  def __init__(self, path=None, *, clock=time.monotonic_ns, params=None):
    self.clock = clock
    self.params = params
    try:
      self.path = report_path() if path is None else path
    except (OSError, ValueError):
      self.path = None

  def snapshot(self):
    result = read_report(self.path, now_ns=self.clock()) if self.path is not None else {
      "version": 1, "current": None, "history": []}
    if self.params is not None:
      result['preference'] = self.preference()
    return result

  def preference(self):
    from capnp import KjException
    from opendbc.car.structs import car
    from openpilot.starpilot.lateral.learning_preferences import KEY, MAX_BYTES, LEGACY_KEY, read_preference
    from openpilot.starpilot.saved_source import read_saved
    from openpilot.starpilot.schema_cache import inspect_cache

    unknown = {'version': 1, 'valid': False, 'enabled': None, 'parked': None, 'vehicle': None}
    try:
      offroad = read_saved(self.params, 'IsOffroad', 1)
      if not offroad[1] or offroad[0] not in (b'0', b'1'):
        return unknown
      parked = offroad[0] == b'1'
      key = 'CarParamsPersistent' if parked else 'CarParams'
      snapshots = {name: read_saved(self.params, name, limit) for name, limit in (
        (key, 1024 * 1024), (KEY, MAX_BYTES), (LEGACY_KEY, 1))}
      raw, readable = snapshots[key]
      if raw is None or not readable:
        return {**unknown, 'parked': parked}
      payload = inspect_cache(key, raw).payload if parked else raw
      if payload is None:
        return {**unknown, 'parked': parked}
      with car.CarParams.from_bytes(payload, traversal_limit_in_words=1024 * 1024 // 8) as cp:
        choice = read_preference(self.params, cp, preference_snapshot=snapshots[KEY], legacy_snapshot=snapshots[LEGACY_KEY])
        vehicle, not_car = str(cp.carFingerprint), bool(cp.notCar)
      # A mode, vehicle or preference change invalidates this read as a whole.
      if (not_car or not choice.valid or offroad != read_saved(self.params, 'IsOffroad', 1) or
          any(snapshot != read_saved(self.params, name, 1024 * 1024 if name == key else MAX_BYTES if name == KEY else 1)
              for name, snapshot in snapshots.items())):
        return {**unknown, 'parked': parked}
      return {'version': 1, 'valid': True, 'enabled': choice.enabled, 'parked': parked, 'vehicle': vehicle}
    except (OSError, RuntimeError, AttributeError, TypeError, ValueError, OverflowError, KjException):
      return unknown
