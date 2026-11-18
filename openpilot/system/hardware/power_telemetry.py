"""Best-effort SoM power telemetry, never a dependency of the health publisher."""
import math
import threading
import time
from collections.abc import Callable


class PowerTelemetry:
  def __init__(self, read: Callable[[], float], interval: float, clock: Callable[[], float] = time.monotonic):
    self._read = read
    self._interval = interval
    self._clock = clock
    self._sample: tuple[float, float] | None = None

  def value(self) -> float:
    # Zero is the existing hardware API's unavailable-power sentinel. Do not
    # present indefinitely cached telemetry as a current measurement.
    sample = self._sample
    if sample is None or not 0 <= self._clock() - sample[0] <= 2 * self._interval:
      return 0.0
    return sample[1]

  def run(self, end_event: threading.Event) -> None:
    # One worker, not one thread per timed-out read. A stuck sysfs read cannot
    # spawn unbounded workers or prevent hardwared from publishing health.
    while not end_event.is_set():
      started = self._clock()
      try:
        value = float(self._read())
        # Age from the start of the read: a delayed result is not fresh merely
        # because the syscall finally returned. Assignment publishes one tuple.
        self._sample = (started, value) if math.isfinite(value) else None
      except Exception:
        self._sample = None
      if end_event.wait(self._interval):
        break
