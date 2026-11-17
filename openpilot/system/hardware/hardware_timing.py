import time
from contextlib import suppress

from openpilot.common.swaglog import cloudlog


class HardwareLoopTiming:
  def __init__(self):
    self.phase = ''
    self.wall_ns = 0
    self.cpu_ns = 0

  def mark(self, phase: str) -> None:
    wall_ns, cpu_ns = time.monotonic_ns(), time.thread_time_ns()
    elapsed_ns = wall_ns - self.wall_ns
    if self.phase and elapsed_ns >= 500_000_000:
      with suppress(OSError):
        cloudlog.event('hardwared.slow_phase', phase=self.phase,
          elapsedMs=elapsed_ns / 1e6, threadCpuMs=(cpu_ns - self.cpu_ns) / 1e6, error=True)
    self.phase, self.wall_ns, self.cpu_ns = phase, wall_ns, cpu_ns
