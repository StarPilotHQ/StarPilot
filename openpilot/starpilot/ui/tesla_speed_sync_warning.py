"""Speed sync: display-only speed comparison, never an onroad alert or control input."""
import math

from opendbc.car.tesla.speed_sync import SYNC_FLAG


class SpeedSyncCaption:
  PERSIST_NS = 3_000_000_000
  MAX_GAP_NS = 1_000_000_000

  def __init__(self):
    self._since_ns = None
    self._last_ns = None
    self._identity = None

  def update(self, *, car_params, car, selfdrive, comma_kph, now_ns, drive_frame, metric,
             comma_owns_speed, alert_present):
    """comma_owns_speed: the comma sets the speed, through SLC or through Match the Tesla's set speed (SLC off)."""
    identity = (drive_frame, bool(metric))
    discontinuity = (identity != self._identity or self._last_ns is None or
                     not 0 <= now_ns - self._last_ns <= self.MAX_GAP_NS)
    self._identity, self._last_ns = identity, now_ns
    if discontinuity:
      self._since_ns = None
    try:
      selected = (car_params.brand == "tesla" and car_params.openpilotLongitudinalControl and
                  any(int(c.safetyParam) & SYNC_FLAG for c in car_params.safetyConfigs))
      available = (selected and comma_owns_speed and not alert_present and selfdrive is not None and
                   selfdrive.enabled and car is not None and car.canValid and not car.canTimeout and
                   car.cruiseState.enabled and now_ns > 0)
      comma_kph, tesla_kph = float(comma_kph), float(car.cruiseState.speed) * 3.6
      available = available and all(math.isfinite(v) and 0 < v < 255 for v in (comma_kph, tesla_kph))
      factor = 1.0 if metric else 0.621371
      matched = available and round(comma_kph * factor) == round(tesla_kph * factor)
      if not available or matched:
        self._since_ns = None
        return None
      if self._since_ns is None:
        self._since_ns = now_ns
      if now_ns - self._since_ns >= self.PERSIST_NS:
        return f"Tesla {round(tesla_kph * factor)}"
    except (AttributeError, TypeError, ValueError, OverflowError):
      self._since_ns = None
    return None
