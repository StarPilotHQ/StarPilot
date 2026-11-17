from unittest.mock import Mock, patch

import pytest

from openpilot.selfdrive.controls.lib.cycle_timing import ControlCycleTiming, PHASES
from openpilot.selfdrive.controls import controlsd


def test_wait_is_separate_from_cpu_and_phases_sum_to_whole():
  wall = iter([0, 127_000_000, 128_000_000, 129_000_000, 130_000_000, 131_000_000])
  cpu = iter([0, 4_000_000, 5_000_000, 6_000_000, 7_000_000, 8_000_000])
  timer = ControlCycleTiming(wall_clock=lambda: next(wall), cpu_clock=lambda: next(cpu))
  timer.start()
  for _ in PHASES:
    timer.mark()
  report = timer.snapshot()
  assert timer.complete
  assert report[PHASES[0]] == {'wall_ms': 127., 'cpu_ms': 4.}
  assert sum(r['wall_ms'] for r in report.values()) == 131.
  assert sum(r['cpu_ms'] for r in report.values()) == 8.


def test_new_cycle_does_not_report_previous_cycle_phases():
  wall = iter(range(20))
  cpu = iter(range(20))
  timer = ControlCycleTiming(wall_clock=lambda: next(wall), cpu_clock=lambda: next(cpu))
  timer.start()
  for _ in PHASES:
    timer.mark()
  timer.start()
  assert timer.snapshot() == {} and not timer.complete
  timer.mark()
  assert list(timer.snapshot()) == [PHASES[0]]
  assert not timer.complete


def test_instrumentation_boundary_overflow_is_explicit_and_bounded():
  timer = ControlCycleTiming(wall_clock=lambda: 1, cpu_clock=lambda: 1)
  timer.start()
  for _ in range(len(PHASES) + 10):
    timer.mark()
  assert not timer.complete
  assert timer.overflow
  assert len(timer.snapshot()) == len(PHASES)


def test_overrun_report_is_after_command_publication():
  owner = controlsd.Controls.__new__(controlsd.Controls)
  actions = []
  def calculate(**kwargs):
    actions.append('control')
    owner.control_cycle_timing.start()
    for _ in PHASES:
      owner.control_cycle_timing.mark()
    return object(), object()
  def report(*args, **kwargs):
    assert actions[-1] == 'publish'
    assert kwargs['control_phases_complete']
    assert set(kwargs['control_phases']) == set(PHASES)
    actions.append('report')
  rk = Mock()
  rk.monitor_time.side_effect = StopIteration
  stamps = iter((1, 10_000_001, 140_000_001, 141_000_001))
  with patch.object(owner, 'update', side_effect=lambda: actions.append('update')), \
       patch.object(owner, 'state_control', side_effect=calculate), \
       patch.object(owner, 'publish', side_effect=lambda *args: actions.append('publish')), \
       patch.object(controlsd, 'Ratekeeper', return_value=rk), \
       patch.object(controlsd.time, 'monotonic_ns', side_effect=lambda: next(stamps)), \
       patch.object(controlsd.cloudlog, 'event', side_effect=report), pytest.raises(StopIteration):
    owner.run()
  assert actions == ['update', 'control', 'publish', 'report']
