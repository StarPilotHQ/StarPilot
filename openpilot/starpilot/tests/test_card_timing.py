"""Bounded diagnostic accounting and actual Card call order, without vehicle output."""

from types import SimpleNamespace as NS
from typing import cast
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.diagnostics.card_timing import CardTiming, Sample, PHASES, CAPTURE_NS


class Clock:
  def __init__(self, queue=True):
    self.wall = 0
    self.queue = queue

  def __call__(self):
    self.wall += 3_000_000
    return Sample(self.wall, self.wall // 3, self.wall // 6 if self.queue else None,
                  self.wall // 3_000_000, 0)


def cycle(timing):
  timing.begin()
  for phase in PHASES:
    timing.mark(phase)
  timing.finish()


def test_slow_cycle_separates_cpu_runqueue_and_unattributed_off_cpu():
  emit = Mock()
  timing = CardTiming(emit, Clock())
  cycle(timing)
  emit.assert_called_once()
  assert emit.call_args.args == ('card_cycle_timing',)
  data = emit.call_args.kwargs
  assert data['wall_ns'] == 24_000_000
  assert len(data['phases']) == len(PHASES)
  assert [p['phase'] for p in data['phases']] == list(PHASES)
  for phase in data['phases']:
    assert (phase['wall_ns'], phase['cpu_ns'], phase['runqueue_ns'], phase['other_off_cpu_ns']) == (
      3_000_000, 1_000_000, 500_000, 1_500_000)
    assert phase['voluntary_switches'] == 1
  assert not timing.samples


def test_unknown_runqueue_is_not_reported_as_zero_or_as_proven_blocking():
  emit = Mock()
  cycle(CardTiming(emit, Clock(queue=False)))
  assert all(p['runqueue_ns'] is None and p['other_off_cpu_ns'] is None for p in emit.call_args.kwargs['phases'])


def test_capture_and_report_rates_are_bounded_and_keep_suppression_count():
  clock, emit = Clock(), Mock()
  timing = CardTiming(emit, clock)
  for _ in range(30):
    cycle(timing)
    assert len(timing.samples) == 0
  assert emit.call_count == 1
  clock.wall += 1_000_000_000
  cycle(timing)
  assert emit.call_count == 2
  assert emit.call_args.kwargs['suppressed'] == 29
  clock.wall = CAPTURE_NS + 3_000_000
  timing.begin()
  assert not timing.enabled and not timing.samples
  timing.sample = Mock(side_effect=AssertionError('closed probe must do no work'))
  cycle(timing)
  timing.sample.assert_not_called()


@pytest.mark.parametrize('failure', ['sample', 'phase', 'thread', 'incomplete', 'logger'])
def test_diagnostic_failures_disable_only_the_probe(failure):
  timing = CardTiming(Mock(), Clock())
  if failure == 'sample':
    timing.sample = Mock(side_effect=OSError('unavailable'))
    timing.begin()
  elif failure == 'phase':
    timing.begin()
    timing.mark('arbitrary phase')
  elif failure == 'thread':
    timing.owner = -1
    timing.begin()
  elif failure == 'incomplete':
    timing.begin()
    timing.finish()
  else:
    timing.emit = Mock(side_effect=OSError('logger unavailable'))
    cycle(timing)
  assert not timing.enabled and not timing.samples


def test_schedstat_reads_are_bounded_and_descriptor_is_closed_once():
  with patch('builtins.open') as opened, patch('os.open', return_value=17) as fd_open, \
       patch('os.pread', return_value=b'100 200 3') as read, patch('os.close') as close:
    opened.return_value.__enter__.return_value.read.return_value = '0\n'
    timing = CardTiming(Mock())
    timing.begin()
    timing.mark('startup')
    assert timing.samples[0].runqueue is None
    fd_open.assert_called_once()
    assert read.call_args.args == (17, 128, 0)
    timing.close()
    timing.close()
    close.assert_called_once_with(17)


def test_missing_proc_counters_still_capture_wall_and_thread_cpu():
  with patch('builtins.open', side_effect=FileNotFoundError), patch('os.open', side_effect=FileNotFoundError):
    timing = CardTiming(Mock())
    timing.begin()
    timing.mark('startup')
    assert timing.enabled and len(timing.samples) == 2
    assert timing.samples[0].runqueue is None
    assert timing.samples[1].wall >= timing.samples[0].wall
    assert timing.samples[1].cpu >= timing.samples[0].cpu
    timing.close()


def test_actual_card_step_preserves_action_order_with_optional_marks():
  from openpilot.selfdrive.car.card import Car
  trace = []
  timing = NS(mark=lambda phase: trace.append(phase))
  fake = NS(_cycle_timing=timing, vehicle_startup=NS(owner=None), CP=NS(passive=False),
            sm={'onroadEvents': [], 'carControl': object()}, _deferred_radar_can=object())
  fake.sm = type('SM', (dict,), {'seen': {'onroadEvents': True}})(fake.sm)
  fake.state_update = lambda **_: trace.append('state_update') or (object(), None)
  fake.state_publish = lambda *_: trace.append('publish')
  fake.controls_update = lambda *_: trace.append('control')
  fake.radar_update = lambda _: trace.append('radar_update')
  fake.radar_publish = lambda _: trace.append('radar_publish')
  Car.step(cast(Car, fake))
  assert trace == ['state_update', 'state_decision', 'publish', 'publish_state', 'control',
                   'apply_control', 'radar_update', 'radar_publish', 'radar']
  del fake._cycle_timing
  trace.clear()
  Car.step(cast(Car, fake))
  assert trace == ['state_update', 'publish', 'control', 'radar_update', 'radar_publish']


@pytest.mark.parametrize('fail_startup', [False, True])
def test_actual_card_loop_keeps_control_errors_and_cleans_probe(fail_startup):
  from openpilot.selfdrive.car.card import Car

  class StopLoop(Exception):
    pass

  probe = Mock(enabled=True)
  startup = Mock(side_effect=StopLoop('startup')) if fail_startup else Mock()
  fake = NS(configure_conditional_settings_worker=Mock(), params_thread=Mock(),
            vehicle_startup=NS(check=startup), step=Mock(), rk=NS(monitor_time=Mock(side_effect=StopLoop('monitor'))),
            ioniq6_keepalive=None)
  with patch.dict('os.environ', {}, clear=True), \
       patch('threading.Thread'), patch('openpilot.starpilot.diagnostics.card_timing.CardTiming', return_value=probe) as factory:
    with pytest.raises(StopLoop, match='startup' if fail_startup else 'monitor'):
      Car.card_thread(cast(Car, fake))
  assert factory.call_count == 1
  assert fake.step.call_count == int(not fail_startup)
  assert probe.close.call_count == 1
  assert probe.begin.call_count == 1
  assert probe.finish.call_count == int(not fail_startup)


def test_actual_card_loop_removes_expired_capture_without_restarting_it():
  from openpilot.selfdrive.car.card import Car

  class StopLoop(Exception):
    pass

  sample = Mock(return_value=Sample(CAPTURE_NS, 0, None, None, None))
  probe = CardTiming(Mock(), sample)
  probe.deadline = CAPTURE_NS
  fake = NS(configure_conditional_settings_worker=Mock(), params_thread=Mock(), vehicle_startup=NS(check=Mock()),
            step=Mock(), rk=NS(monitor_time=Mock(side_effect=[None, StopLoop()])), ioniq6_keepalive=None)
  with patch('threading.Thread'), patch('openpilot.starpilot.diagnostics.card_timing.CardTiming', return_value=probe):
    with pytest.raises(StopLoop):
      Car.card_thread(cast(Car, fake))
  assert fake._cycle_timing is None
  assert not probe.enabled and not probe.samples and probe.fd is None
  assert fake.step.call_count == 2
  sample.assert_called_once()
