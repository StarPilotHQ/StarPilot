"""Temporary bounded Card phase diagnostics; never changes control policy or scheduling."""

import os
import resource
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


PHASES = ("startup", "can_receive", "can_parse", "service_update", "state_decision", "publish_state", "apply_control", "radar")
SLOW_NS = 20_000_000
REPORT_INTERVAL_NS = 1_000_000_000
CAPTURE_NS = 3_600_000_000_000
MAX_REPORTS = 60
PUBLICATION_PHASES = ("car_output_create", "feedback_capture", "feedback_publication", "car_output_send", "car_state_create")


@dataclass(frozen=True, slots=True)
class Sample:
  wall: int
  cpu: int
  runqueue: int | None
  voluntary: int | None
  involuntary: int | None


class CardTiming:
  def __init__(self, emit: Callable, sample: Callable[[], Sample] | None = None):
    self.emit = emit
    self.sample = sample or self._sample
    self.owner = threading.get_ident()
    self.fd: int | None = None
    self.schedstats_enabled = False
    self.initialized = False
    self.enabled = True
    self.deadline: int | None = None
    self.last_report: int | None = None
    self.suppressed = 0
    self.reports = 0
    self.publication_samples: list[Sample] = []
    self.samples: list[Sample] = []

  def _sample(self) -> Sample:
    runqueue = None
    if not self.initialized:
      self.initialized = True
      try:
        with open('/proc/sys/kernel/sched_schedstats') as setting:
          self.schedstats_enabled = setting.read(8).strip() == '1'
      except OSError:
        pass
      try:
        self.fd = os.open('/proc/thread-self/schedstat', os.O_RDONLY | os.O_CLOEXEC)
      except OSError:
        pass
    if self.fd is not None:
      fields = os.pread(self.fd, 128, 0).split()
      if len(fields) != 3:
        raise ValueError('invalid thread schedstat')
      runqueue = int(fields[1]) if self.schedstats_enabled else None
    usage = resource.getrusage(resource.RUSAGE_THREAD) if hasattr(resource, 'RUSAGE_THREAD') else None
    return Sample(time.monotonic_ns(), time.thread_time_ns(), runqueue,
                  int(usage.ru_nvcsw) if usage is not None else None,
                  int(usage.ru_nivcsw) if usage is not None else None)

  def begin(self) -> None:
    if not self.enabled:
      return
    try:
      if threading.get_ident() != self.owner or self.samples:
        raise ValueError('wrong timing thread or unfinished cycle')
      sample = self.sample()
      if self.deadline is None:
        self.deadline = sample.wall + CAPTURE_NS
      if sample.wall >= self.deadline:
        self.close()
        return
      self.samples.append(sample)
    except Exception:
      self.close()

  def mark(self, phase: str) -> None:
    if not self.enabled or not self.samples:
      return
    try:
      index = len(self.samples) - 1
      if threading.get_ident() != self.owner or index >= len(PHASES) or phase != PHASES[index]:
        raise ValueError('invalid timing phase')
      self.samples.append(self.sample())
    except Exception:
      self.close()

  def publication_mark(self, phase: str) -> None:
    if not self.enabled or not self.samples:
      return
    try:
      names = ("begin", *PUBLICATION_PHASES)
      if (threading.get_ident() != self.owner or len(self.samples) - 1 != PHASES.index("publish_state") or
          len(self.publication_samples) >= len(names) or phase != names[len(self.publication_samples)]):
        raise ValueError('invalid publication phase')
      self.publication_samples.append(self.sample())
    except Exception:
      self.close()

  @staticmethod
  def _durations(names: tuple[str, ...], samples: list[Sample]) -> list[dict[str, str | int | None]]:
    phases = []
    for name, start, end in zip(names, samples[:-1], samples[1:], strict=True):
      elapsed, cpu = end.wall - start.wall, end.cpu - start.cpu
      queue = end.runqueue - start.runqueue if start.runqueue is not None and end.runqueue is not None else None
      if elapsed < 0 or cpu < 0 or queue is not None and queue < 0:
        raise ValueError('nonmonotonic timing sample')
      phases.append({'phase': name, 'wall_ns': elapsed, 'cpu_ns': cpu, 'runqueue_ns': queue,
                     'other_off_cpu_ns': max(0, elapsed - cpu - queue) if queue is not None else None,
                     'accounting_skew_ns': max(0, cpu + (queue or 0) - elapsed),
                     'voluntary_switches': end.voluntary - start.voluntary if start.voluntary is not None and
                       end.voluntary is not None else None,
                     'involuntary_switches': end.involuntary - start.involuntary if start.involuntary is not None and
                       end.involuntary is not None else None})
    return phases

  def finish(self) -> None:
    if not self.enabled or not self.samples:
      return
    try:
      if len(self.samples) != len(PHASES) + 1 or threading.get_ident() != self.owner:
        raise ValueError('incomplete timing cycle')
      first, last = self.samples[0], self.samples[-1]
      wall = last.wall - first.wall
      if wall > SLOW_NS:
        if self.last_report is None or last.wall - self.last_report >= REPORT_INTERVAL_NS:
          phases = self._durations(PHASES, self.samples)
          publication = self._durations(PUBLICATION_PHASES, self.publication_samples) if self.publication_samples else []
          self.emit('card_cycle_timing', wall_ns=wall, phases=phases, publication=publication,
                    suppressed=self.suppressed, report=self.reports + 1)
          self.reports += 1
          if self.reports >= MAX_REPORTS:
            self.close()
          self.last_report = last.wall
          self.suppressed = 0
        else:
          self.suppressed += 1
      self.samples.clear()
      self.publication_samples.clear()
    except Exception:
      self.close()

  def close(self) -> None:
    self.enabled = False
    self.samples.clear()
    self.publication_samples.clear()
    if self.fd is not None:
      try:
        os.close(self.fd)
      except OSError:
        pass
      self.fd = None
