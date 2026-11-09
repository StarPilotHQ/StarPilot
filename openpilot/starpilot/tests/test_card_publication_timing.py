"""Bounded numeric publication diagnostics preserve Card outputs and error propagation."""
import json
from types import SimpleNamespace as NS
from typing import cast
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.diagnostics import card_timing as module
from openpilot.starpilot.diagnostics.card_timing import CardTiming, Sample, PHASES


class Clock:
  def __init__(self):
    self.wall = 0

  def __call__(self):
    self.wall += 10_000
    return Sample(self.wall, self.wall // 4, None, None, None)


def cycle(timing, publish=None):
  timing.begin()
  for phase in PHASES:
    if phase == 'publish_state' and publish is not None:
      publish()
    timing.mark(phase)
  timing.finish()


def test_late_outlier_is_captured_after_initial_ten_minutes():
  clock, emit = Clock(), Mock()
  timing = CardTiming(emit, clock)
  cycle(timing)
  clock.wall += 735_000_000_000
  def stall():
    clock.wall += 78_000_000
  cycle(timing, stall)
  assert timing.enabled
  emit.assert_called_once()
  phase = next(p for p in emit.call_args.kwargs['phases'] if p['phase'] == 'publish_state')
  assert phase['wall_ns'] >= 78_000_000
  clock.wall = module.CAPTURE_NS + 10_000
  timing.begin()
  assert not timing.enabled and not timing.samples and timing.fd is None


def test_report_and_memory_budgets_stop_probe_without_restart():
  clock, emit = Clock(), Mock()
  timing = CardTiming(emit, clock)
  for _ in range(module.MAX_REPORTS):
    clock.wall += module.REPORT_INTERVAL_NS
    cycle(timing, lambda: setattr(clock, 'wall', clock.wall + 25_000_000))
  assert emit.call_count == module.MAX_REPORTS
  assert not timing.enabled and not timing.samples and not timing.publication_samples
  assert timing.fd is None
  timing.sample = Mock(side_effect=AssertionError('closed probe cannot sample'))
  cycle(timing)
  timing.sample.assert_not_called()


def card_fixture(clock, delay, fail_send=False):
  from openpilot.cereal import messaging
  from opendbc.car.structs import car
  from openpilot.selfdrive.car.card import Car
  sent = []
  trace = []
  def wait(phase):
    trace.append(phase)
    if delay == phase:
      clock.wall += 78_000_000
  def send(topic, message):
    if topic == 'carOutput':
      wait('car_output_send')
      if fail_send:
        raise RuntimeError('original send failure')
    payload = message.to_dict()
    payload.pop('logMonoTime')
    sent.append((topic, payload))
  def capture(_stamp):
    wait('feedback_capture')
    return NS(publication=lambda _message: wait('feedback_publication'), invalidate=Mock())
  fake = NS(CP=car.CarParams.new_message(brand='mock'), CI=NS(CS=NS()), car_params_published=True,
            sm=NS(frame=1, all_checks=lambda _names: True), pm=NS(send=send),
            last_actuators_output=messaging.new_message('carOutput').carOutput.actuatorsOutput,
            can_rcv_cum_timeout_counter=0, _feedback_capture=capture, rk=NS(remaining=.01), slc_replay=False, curve_replay=False,
            aol_replay=False, v_cruise_helper=NS(toyota=None, slc_cruise_change=None),
            car_gps_publisher=NS(update=lambda *_: None), publish_traffic_receipt=lambda _: None,
            radar_publish=lambda _: None)
  original = messaging.new_message
  def new_message(topic, *args, **kwargs):
    if topic in ('carOutput', 'carState'):
      wait('car_output_create' if topic == 'carOutput' else 'car_state_create')
    return original(topic, *args, **kwargs)
  def publish():
    with patch.object(messaging, 'new_message', side_effect=new_message):
      Car.state_publish(cast(Car, fake), car.CarState.new_message(canValid=True), None)
  return fake, publish, sent, trace


@pytest.mark.parametrize('phase', ('car_output_create', 'feedback_capture', 'feedback_publication',
                                   'car_output_send', 'car_state_create'))
def test_actual_card_publishing_subphases_identify_stall_and_keep_outputs(phase):
  clock, emit = Clock(), Mock()
  timing = CardTiming(emit, clock)
  fake, publish, sent, trace = card_fixture(clock, phase)
  fake._cycle_timing = timing
  cycle(timing, publish)
  assert timing.enabled
  data = emit.call_args.kwargs
  assert [p['phase'] for p in data['publication']] == list(module.PUBLICATION_PHASES)
  delayed = [p['phase'] for p in data['publication'] if p['wall_ns'] >= 78_000_000]
  assert delayed == [phase]
  assert all(p['runqueue_ns'] is None for p in data['publication'])
  assert len(json.dumps(data)) < 8192
  assert set(data) == {'wall_ns', 'phases', 'publication', 'suppressed', 'report'}
  assert all(set(p) == {'phase', 'wall_ns', 'cpu_ns', 'runqueue_ns', 'other_off_cpu_ns',
                       'accounting_skew_ns', 'voluntary_switches', 'involuntary_switches'}
             for p in data['phases'] + data['publication'])
  expected_trace = ['car_output_create', 'feedback_capture', 'feedback_publication',
                    'car_output_send', 'car_state_create']
  assert trace == expected_trace
  assert [topic for topic, _ in sent] == ['carOutput', 'carState']
  del fake._cycle_timing
  old = list(sent)
  sent.clear()
  trace.clear()
  publish()
  assert sent == old and trace == expected_trace
  assert not timing.samples and not timing.publication_samples


@pytest.mark.parametrize('probe_failure', (False, True))
def test_real_card_send_errors_propagate_with_enabled_or_failed_probe(probe_failure):
  clock = Clock()
  timing = CardTiming(Mock(), clock)
  fake, publish, _, _ = card_fixture(clock, '', fail_send=True)
  fake._cycle_timing = timing
  if probe_failure:
    timing.sample = Mock(side_effect=OSError('sample unavailable'))
  with pytest.raises(RuntimeError, match='original send failure'):
    cycle(timing, publish)
  timing.close()
  assert not timing.enabled and not timing.samples and not timing.publication_samples


@pytest.mark.parametrize('failure', ('order', 'thread', 'partial', 'nonmonotonic'))
def test_publication_probe_validation_never_invents_complete_measurements(failure):
  clock, emit = Clock(), Mock()
  timing = CardTiming(emit, clock)
  def publish():
    timing.publication_mark('begin')
    if failure == 'order':
      timing.publication_mark('arbitrary phase')
    elif failure == 'thread':
      timing.owner = -1
      timing.publication_mark('car_output_create')
    elif failure == 'nonmonotonic':
      clock.wall -= 1_000_000
      for name in module.PUBLICATION_PHASES:
        timing.publication_mark(name)
    clock.wall += 78_000_000
  cycle(timing, publish)
  assert not timing.enabled and not timing.samples and not timing.publication_samples
  emit.assert_not_called()
