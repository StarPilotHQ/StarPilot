"""Tells the comma's own screen that StarPort's drive view is reaching the car screen right now.

The supervisor touches the lease while frames go to a focused session; the comma's screen stats it at most once a
second and, with "Black screen while StarPort streams" on, draws black with alerts only. A supervisor that stops or
crashes stops touching it, so the normal screen returns on its own.
"""
import os
import time

from openpilot.starpilot.system.starport.projection_map_lease import MapLeaseWriter

DEFAULT_PATH = '/tmp/starpilot-projection-stream.lease'
MAX_AGE_S = 2.0  # touched every second; a stopped stream gives the screen back within about 3 s even without release()
CHECK_EVERY_S = 1.0


class StreamLeaseWriter(MapLeaseWriter):
  def __init__(self, path=DEFAULT_PATH):
    super().__init__(path)


def projection_stream_active(path=DEFAULT_PATH, now=None) -> bool:
  try:
    # Filesystem mtimes use the realtime clock; monotonic time has a different epoch.
    age = (time.time() if now is None else now) - os.stat(path).st_mtime  # noqa: TID251
  except OSError:
    return False
  return 0 <= age <= MAX_AGE_S


class StreamLeaseReader:
  """Callable answering projection_stream_active(), cached so the render loop stats the file at most once a second."""

  def __init__(self, path=DEFAULT_PATH, clock=time.monotonic):
    self.path, self._clock = str(path), clock
    self._checked: float | None = None
    self._active = False

  def __call__(self) -> bool:
    now = self._clock()
    if self._checked is None or not 0 <= now - self._checked < CHECK_EVERY_S:
      self._checked = now
      self._active = projection_stream_active(self.path)
    return self._active
