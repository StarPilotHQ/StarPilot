"""One lifecycle-owned message collector for Galaxy's fresh authority readers."""

import threading
import time
from types import MappingProxyType, SimpleNamespace

from openpilot.starpilot.parked_evidence import RESUME_SKEW_NS


class EvidenceSnapshot(SimpleNamespace):
  def __setattr__(self, name, value):
    if getattr(self, '_frozen', False):
      raise AttributeError('Evidence snapshot is immutable')
    super().__setattr__(name, value)

  def __getitem__(self, name):
    return self.data[name]


class EvidenceSource:
  SERVICES = ('deviceState', 'pandaStates', 'carState', 'selfdriveState', 'carParams', 'peripheralState')

  def __init__(self, messages=None, *, mono=time.monotonic_ns,
               boot=lambda: time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))):
    if messages is None:
      from openpilot.cereal import messaging
      messages = messaging.SubMaster(list(self.SERVICES))
    self.messages, self.mono, self.boot = messages, mono, boot
    self.after_mono_ns = mono()
    self.offset = self.device_offset_ns = None
    self.lock = threading.Lock()
    self.changed = threading.Condition(self.lock)
    self.wake = threading.Event()
    self.stop = threading.Event()
    self.thread = None
    self.closed = self.failed = False
    self.polls = 0
    self.observations = dict.fromkeys(('deviceState', 'pandaStates'), 0)

  def _clock_pair(self):
    # Discard preempted measurements before deciding whether the device resumed.
    for _ in range(3):
      before, boot, now = self.mono(), self.boot(), self.mono()
      if 0 <= now - before <= RESUME_SKEW_NS:
        break
    return before, boot, now

  def poll(self):
    with self.lock:
      if self.closed:
        return
      self.polls += 1
      try:
        self.messages.update(0)
      except (OSError, RuntimeError, AttributeError, KeyError, TypeError, ValueError):
        self.after_mono_ns, self.device_offset_ns, self.failed = self.mono(), None, True
        self.changed.notify_all()
        return
      self.failed = False
      before, boot, now = self._clock_pair()
      offset = boot - (before + now) // 2
      if not 0 <= now - before <= RESUME_SKEW_NS or self.offset is not None and abs(offset - self.offset) > RESUME_SKEW_NS:
        self.after_mono_ns, self.device_offset_ns = now, None
      self.offset = offset
      if self.messages.updated['deviceState'] and int(self.messages.logMonoTime['deviceState']) > self.after_mono_ns:
        self.device_offset_ns = offset
      for name in self.observations:
        if self.messages.updated[name]:
          self.observations[name] += 1
      self.changed.notify_all()

  def refresh(self, timeout_ms=750):
    deadline = time.monotonic() + timeout_ms / 1000
    with self.changed:
      if self.closed or self.thread is None or not self.thread.is_alive():
        return False
      before, boot, now = self._clock_pair()
      if not 0 <= now - before <= RESUME_SKEW_NS:
        return False
      offset = boot - (before + now) // 2
      barrier, polls = self.after_mono_ns, self.polls
      observations = dict(self.observations)
      self.wake.set()
      while True:
        if (self.closed or not self.thread.is_alive() or self.after_mono_ns != barrier or
            self.offset is not None and abs(self.offset - offset) > RESUME_SKEW_NS):
          return False
        if self.polls > polls:
          if self.failed:
            return False
          if (all(self.observations[name] > count for name, count in observations.items()) and
              int(self.messages.logMonoTime['deviceState']) > now and
              int(self.messages.logMonoTime['pandaStates']) > boot):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          return False
        self.changed.wait(remaining)

  def start(self):
    with self.lock:
      if self.closed:
        raise RuntimeError('Galaxy evidence reader is closed')
      if self.thread is not None:
        return self
    self.poll()
    def run():
      while not self.stop.is_set():
        self.wake.wait(0.1)
        self.wake.clear()
        if self.stop.is_set():
          break
        try:
          self.poll()
        except (OSError, RuntimeError, AttributeError, KeyError, TypeError, ValueError):
          with self.lock:
            self.after_mono_ns, self.device_offset_ns = self.mono(), None
            self.failed = True
            self.changed.notify_all()
    self.thread = threading.Thread(target=run, name='galaxy-evidence', daemon=True)
    try:
      self.thread.start()
    except BaseException:
      self.close()
      raise
    return self

  def snapshot(self):
    with self.lock:
      if self.closed or self.failed:
        return None
      before, boot, now = self._clock_pair()
      offset = boot - (before + now) // 2
      if (not 0 <= now - before <= RESUME_SKEW_NS or self.offset is None or
          abs(offset - self.offset) > RESUME_SKEW_NS):
        self.after_mono_ns, self.device_offset_ns, self.offset = now, None, offset
        return None
      if self.device_offset_ns is None:
        return None
      sm = self.messages
      values = {name: sm[name] for name in self.SERVICES}
      metadata = {name: MappingProxyType(dict(getattr(sm, name)))
                  for name in ('seen', 'alive', 'valid', 'updated', 'logMonoTime', 'recv_time')}
      captured = EvidenceSnapshot(**metadata,
                                 after_mono_ns=self.after_mono_ns, device_offset_ns=self.device_offset_ns)
      captured.data = MappingProxyType(values)
      captured._frozen = True
      return captured

  def close(self):
    self.stop.set()
    self.wake.set()
    with self.lock:
      if self.closed:
        return
      self.closed = True
      self.changed.notify_all()
    if self.thread is not None and self.thread.ident is not None:
      self.thread.join(timeout=1)
      if self.thread.is_alive():
        raise RuntimeError('Galaxy evidence reader did not stop')
    with self.lock:
      self.closed = True
      for socket in getattr(self.messages, 'sock', {}).values():
        close = getattr(socket, 'close', None)
        if close is not None:
          close()
