"""Local and authenticated presentation of the shared drive-state owner."""

import threading
import time

from .resolver import Mode
from .owner import Rejected


LABELS = {Mode.AUTO: 'Auto', Mode.OFFROAD: 'Offroad', Mode.ONROAD: 'Onroad'}
CYCLE = (Mode.OFFROAD, Mode.ONROAD, Mode.AUTO)


class DriveStateControl:
  def __init__(self, owner, physical, *, effective=None, clock=time.monotonic):
    self.owner, self.physical, self.effective, self.clock = owner, physical, effective or physical.effective, clock
    self.cached = None
    self.lock = threading.Lock()

  def snapshot(self):
    with self.lock:
      now = self.clock()
      if self.cached is None or not 0 <= now - self.cached[0] < 0.5:
        state = self.owner.snapshot()
        status = getattr(self.physical, 'status', None)
        admission = status() if callable(status) else {'allowed': self.physical.allowed(), 'reason': None}
        self.cached = now, state, bool(admission['allowed']), admission['reason']
      state = self.cached[1]
      actual = self.effective()
      return {'mode': state.mode.value, 'revision': state.revision, 'available': state.available,
              'effective': 'onroad' if actual is True else 'offroad' if actual is False else None,
              'overrideAllowed': self.cached[2], 'reason': self.cached[3]}

  def change(self, mode, revision, authorized):
    # Deliberate local requests may stop driving services while moving; Galaxy uses a separate Park-checked request.
    result = self.owner.request(mode, expected_revision=revision, authorized=authorized,
                                override_allowed=lambda: True)
    with self.lock:
      self.cached = None
    return result

  def change_galaxy(self, mode, revision, authorized):
    def offroad_allowed():
      status = self.physical.status(require_park=True)
      if not status['allowed']:
        raise Rejected(status.get('reason') or 'Park the vehicle and disengage before forcing Offroad')
      return True
    result = self.owner.request(mode, expected_revision=revision, authorized=authorized, override_allowed=offroad_allowed)
    with self.lock:
      self.cached = None
    return result

  @staticmethod
  def next_mode(status):
    mode = Mode(status['mode'])
    return CYCLE[(CYCLE.index(mode) + 1) % len(CYCLE)]

  def cycle(self, status, authorized):
    return self.change(self.next_mode(status).value, status['revision'], authorized)
