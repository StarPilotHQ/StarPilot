"""Utilities for reading real time clocks and keeping soft real time constraints."""
import ctypes
import gc
import os
import resource
import sys
import time

from setproctitle import getproctitle

from openpilot.common.utils import MovingAverage
from openpilot.common.hardware import PC


# time step for each process
DT_CTRL = 0.01  # controlsd
DT_MDL = 0.05  # model
DT_HW = 0.5  # hardwared and manager
DT_DMON = 0.05  # driver monitoring


class Priority:
  # CORE 2
  # - modeld = 55
  # - camerad = 54
  CTRL_LOW = 51 # plannerd & radard
  UI = 50

  # CORE 3
  # - pandad = 55
  CTRL_HIGH = 53


def drop_realtime() -> None:
  if sys.platform == 'linux' and not PC:
    os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))


def set_core_affinity(cores: list[int]) -> None:
  if sys.platform == 'linux' and not PC:
    os.sched_setaffinity(0, cores)


def config_realtime_process(cores: int | list[int], priority: int) -> None:
  gc.disable()
  if sys.platform == 'linux' and not PC:
    os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(priority))
  c = cores if isinstance(cores, list) else [cores, ]
  set_core_affinity(c)


MCL_CURRENT, MCL_FUTURE, MCL_ONFAULT = 1, 2, 4


def lock_memory() -> str:
  """Keep this process's pages resident, so a major page fault can't stall a control loop.

  On the device, card sat in uninterruptible sleep for ~27 ms re-reading an evicted page,
  which exceeded selfdrived's 30 ms freshness bound. MCL_ONFAULT locks pages as they are
  touched, so nothing extra is loaded: the cost is the working set, not the address space.
  Locking happens only under an unlimited RLIMIT_MEMLOCK. Under a finite limit the kernel
  counts whole mappings, and a later mapping that crossed it would fail inside the caller.
  Returns a short status for the log; any failure leaves the process unchanged.
  """
  if sys.platform != 'linux' or PC:
    return 'skipped: not device'
  try:
    soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    if hard != resource.RLIM_INFINITY:
      return f'skipped: RLIMIT_MEMLOCK {hard}'
    if soft != resource.RLIM_INFINITY:
      resource.setrlimit(resource.RLIMIT_MEMLOCK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.mlockall(MCL_CURRENT | MCL_FUTURE | MCL_ONFAULT) != 0:
      return f'failed: errno {ctypes.get_errno()}'
  except (OSError, ValueError, AttributeError) as error:
    return f'failed: {error!r}'
  return 'locked'


class Ratekeeper:
  def __init__(self, rate: float, print_delay_threshold: float | None = 0.0) -> None:
    """Rate in Hz for ratekeeping. print_delay_threshold must be nonnegative."""
    self._interval = 1. / rate
    self._print_delay_threshold = print_delay_threshold
    self._frame = 0
    self._remaining = 0.0
    self._process_name = getproctitle()
    self._last_monitor_time = -1.
    self._next_frame_time = -1.

    self.avg_dt = MovingAverage(100)
    self.avg_dt.add_value(self._interval)

  @property
  def frame(self) -> int:
    return self._frame

  @property
  def remaining(self) -> float:
    return self._remaining

  @property
  def lagging(self) -> bool:
    expected_dt = self._interval * (1 / 0.9)
    return self.avg_dt.get_average() > expected_dt

  # Maintain loop rate by calling this at the end of each loop
  def keep_time(self) -> bool:
    lagged = self.monitor_time()
    if self._remaining > 0:
      time.sleep(self._remaining)
    return lagged

  # Monitors the cumulative lag, but does not enforce a rate
  def monitor_time(self) -> bool:
    if self._last_monitor_time < 0:
      self._next_frame_time = time.monotonic() + self._interval
      self._last_monitor_time = time.monotonic()

    prev = self._last_monitor_time
    self._last_monitor_time = time.monotonic()
    self.avg_dt.add_value(self._last_monitor_time - prev)

    lagged = False
    now = time.monotonic()
    remaining = self._next_frame_time - now
    if remaining < -self._interval:
      # more than a frame behind (e.g. a stall): resync instead of running catch-up frames back-to-back
      self._next_frame_time = now + self._interval
    else:
      self._next_frame_time += self._interval
    if self._print_delay_threshold is not None and remaining < -self._print_delay_threshold:
      print(f"{self._process_name} lagging by {-remaining * 1000:.2f} ms")
      lagged = True
    self._frame += 1
    self._remaining = remaining
    return lagged
