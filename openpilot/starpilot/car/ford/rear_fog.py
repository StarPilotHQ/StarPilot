"""Receive-only Ford rear-fog telltale, independent of control authority."""
import time

MAX_AGE_NS = 1_500_000_000


def boot_time_ns():
  return time.clock_gettime_ns(time.CLOCK_BOOTTIME)


def main_bus(cp):
  try:
    if cp.brand != "ford" or not cp.carFingerprint:
      return None
    configs = tuple(cp.safetyConfigs)
    if len(configs) == 1 and str(configs[0].safetyModel) == "ford":
      return 0
    if (len(configs) == 2 and str(configs[0].safetyModel) == "noOutput" and
        int(configs[0].safetyParam) == 0 and str(configs[1].safetyModel) == "ford"):
      return 4
  except (AttributeError, TypeError, ValueError):
    pass
  return None


def fresh(source_ns, now_ns):
  return source_ns > 0 and 0 <= now_ns - source_ns <= MAX_AGE_NS


class RearFogReceiver:
  def __init__(self):
    self.socket = None
    self.identity = None
    self.on = False
    self.source_ns = 0

  def close(self):
    self.socket = None
    self.identity = None
    self.on = False
    self.source_ns = 0

  def observe(self, event, bus, now_ns):
    stamp = int(event.logMonoTime)
    if not event.valid or not fresh(stamp, now_ns) or stamp < self.source_ns:
      return
    for frame in event.can:
      if frame.address == 0x3B3 and frame.src == bus and len(frame.dat) == 8:
        self.on = bool(frame.dat[0] & 0x02)
        self.source_ns = stamp

  def sample(self, cp, *, active):
    from openpilot.cereal import messaging
    bus = main_bus(cp)
    if not active or bus is None:
      self.close()
      return False, 0
    identity = (str(cp.carFingerprint), bus)
    if identity != self.identity:
      self.close()
      self.identity = identity
    now = boot_time_ns()
    try:
      if self.socket is None:
        self.socket = messaging.sub_sock("can", conflate=False)
      for _ in range(32):
        event = messaging.recv_one_or_none(self.socket)
        if event is None:
          break
        self.observe(event, bus, now)
    except (OSError, RuntimeError, ValueError):
      self.close()
    return self.on and fresh(self.source_ns, now), self.source_ns
