from unittest.mock import Mock

import pytest

from openpilot.starpilot.ui.actuation_recovery import KEY, acknowledge


@pytest.fixture
def state():
  params = Mock()
  alert = {'text': 'Excessive actuation', 'extra': 'longitudinal'}
  params.get.return_value = alert
  params.get_bool.return_value = True
  physical = Mock()
  physical.status.return_value = {'allowed': True}
  physical.effective.return_value = False
  return params, physical, alert


def test_explicit_offroad_ack_clears_only_latch(state):
  params, physical, alert = state
  assert acknowledge(params, physical, alert)
  physical.status.assert_called_once_with(require_ignition_off=True)
  params.remove.assert_called_once_with(KEY)
  params.put.assert_not_called()


@pytest.mark.parametrize('case', ['onroad', 'physical', 'absent', 'changed'])
def test_refuses_unsafe_or_superseded_confirmation(state, case):
  params, physical, alert = state
  if case == 'onroad':
    params.get_bool.return_value = False
  elif case == 'physical':
    physical.status.return_value = {'allowed': False}
  elif case == 'absent':
    alert = None
  else:
    params.get.return_value = {'extra': 'lateral'}
  assert not acknowledge(params, physical, alert)
  params.remove.assert_not_called()


@pytest.mark.parametrize('change', ['mode', 'alert'])
def test_rechecks_after_physical_admission(state, change):
  params, physical, alert = state
  if change == 'mode':
    params.get_bool.side_effect = [True, False]
  else:
    params.get.side_effect = [alert, {'extra': 'lateral'}]
  assert not acknowledge(params, physical, alert)
  params.remove.assert_not_called()


def test_repeated_ack_does_not_clear_new_event(state):
  params, physical, alert = state
  assert acknowledge(params, physical, alert)
  params.get.return_value = None
  assert not acknowledge(params, physical, alert)
  assert params.remove.call_count == 1


@pytest.mark.parametrize('started', [True, None])
def test_requires_fresh_device_offroad(state, started):
  params, physical, alert = state
  physical.effective.return_value = started
  assert not acknowledge(params, physical, alert)
  params.remove.assert_not_called()


@pytest.mark.parametrize('fault', [None, 'ignition', 'controls', 'stale_panda', 'stale_device', 'started'])
def test_real_physical_evidence_after_driving_processes_stop(state, fault):
  from types import SimpleNamespace as NS
  from openpilot.starpilot.drive_state.evidence import PhysicalSource
  from openpilot.starpilot.drive_state.tests.test_evidence import Messages

  params, _, alert = state
  messages = Messages()
  messages.values['deviceState'] = NS(started=fault == 'started')
  for table in (messages.seen, messages.alive, messages.valid):
    table['deviceState'] = True
    # These processes are deliberately absent offroad. Recovery must not need them.
    table['carState'] = table['selfdriveState'] = False
  messages.logMonoTime['deviceState'] = 2_000_000_000
  messages.recv_time['deviceState'] = 2.0
  now = [1_000_000_000]
  physical = PhysicalSource(messages, mono=lambda: now[0], boot=lambda: now[0] + 10_000_000_000)
  now[0] = 2_100_000_000
  panda = messages.values['pandaStates'][0]
  if fault == 'ignition':
    panda.ignitionLine = True
  elif fault == 'controls':
    panda.controlsAllowed = True
  elif fault == 'stale_panda':
    messages.recv_time['pandaStates'] = 1.0
  elif fault == 'stale_device':
    messages.recv_time['deviceState'] = 1.0
  assert acknowledge(params, physical, alert) is (fault is None)
  assert params.remove.call_count == int(fault is None)
