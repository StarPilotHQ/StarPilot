"""Tesla input failure paths: a failing receiver or worker never takes the other controllers down, never repeats an
input, and recovers a bounded number of times."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.common.car_control_config import MAX_ASSIGNMENT_BYTES, PARAM
from openpilot.starpilot.controllers import tesla_receiver
from openpilot.starpilot.controllers.runtime import ControllerRuntime
from openpilot.starpilot.controllers.tests.test_tesla_decoder import wheel
from openpilot.starpilot.controllers.tests.tesla_worker_fixture import Channel, selected_rig


@pytest.fixture
def setup(tmp_path):
  return selected_rig(tmp_path)


def bare_runtime(presses=(), capture=None):
  r = ControllerRuntime.__new__(ControllerRuntime)
  vars(r).update(_closed=False, _next_warm=float('inf'), _warm_until=0, input_capture=lambda: capture,
                 reader=NS(poll=lambda _: presses, devices=list, close=Mock()),
                 owner=NS(set_devices=Mock(), tick=Mock(), feed=Mock()),
                 tesla=NS(poll=Mock(), close=Mock()), service=NS(poll=Mock(), close=Mock()), authority=NS(close=Mock()))
  return r


def test_tesla_exception_without_button_press_does_not_close_all_controllers():
  r = bare_runtime()
  r.tesla.poll.side_effect = ValueError('injected dispatch failure')
  r.poll()
  assert not r._closed and r.tesla.next_start > 0
  r.tesla.close.assert_called_once()
  r.service.poll.assert_called_once()
  r.reader.close.assert_not_called()


def test_the_tesla_receiver_is_polled_first_and_whatever_the_gamepad_path_does():
  order = []
  r = bare_runtime()
  r.tesla.poll.side_effect = lambda: order.append('tesla')
  r.reader.poll = lambda _: order.append('gamepad') or []
  r.service.poll.side_effect = lambda: order.append('settings')
  r.poll()
  assert order == ['tesla', 'gamepad', 'settings']
  failing = bare_runtime()
  failing.reader.poll = Mock(side_effect=OSError('gamepad unplugged'))
  failing.poll()
  failing.tesla.poll.assert_called_once()
  assert failing._closed, 'the gamepad failure still stops the runtime, after the Tesla input was read'


def test_closing_the_runtime_closes_the_tesla_receiver_once():
  r = bare_runtime()
  r.close()
  r.close()
  r.tesla.close.assert_called_once()
  r.poll()
  r.tesla.poll.assert_not_called()


def test_modal_capture_receives_each_button_once():
  capture = Mock()
  press = object()
  r = bare_runtime([press], capture)
  r.poll()
  r.owner.feed.assert_called_once_with(press, capture=capture)


def test_a_real_runtime_starts_no_worker_without_a_supported_tesla():
  spawned = []
  with patch('openpilot.starpilot.controllers.runtime.ControllerOwner', return_value=Mock()), \
       patch('openpilot.starpilot.controllers.runtime.ControllerService', return_value=Mock()), \
       patch.object(tesla_receiver.subprocess, 'Popen', side_effect=lambda *a, **kw: spawned.append(a)):
    reader = Mock()
    reader.poll.return_value = []
    reader.devices.return_value = []
    runtime = ControllerRuntime(None, actions=Mock(), favorites=Mock(), invoke_favorite=Mock(), authority=Mock(), reader=reader)
    for _ in range(5):
      runtime.poll()
    runtime.close()
  assert not spawned and runtime.tesla.channel is None and not runtime.tesla.failed


def test_identical_periodic_carparams_does_not_drop_held_scroll(setup):
  s = setup
  s.feed(wheel())
  s.feed(wheel(right_scroll=1))
  s.pump.set_vehicle(s.cp.as_reader())
  s.feed(wheel())
  assert s.calls == [0]


def test_changed_carparams_drop_a_held_scroll(setup):
  s = setup
  s.feed(wheel())
  s.feed(wheel(right_scroll=1))
  changed = s.cp.as_reader().as_builder()
  changed.safetyConfigs[0].safetyParam = 1  # no longer selected
  s.pump.set_vehicle(changed.as_reader())
  s.feed(wheel())
  assert s.calls == []


def test_speed_input_does_not_build_unrelated_action_catalog(setup):
  s = setup
  s.receiver.speed_actions = lambda: s.actions
  s.receiver.actions = Mock(side_effect=ValueError('expensive or failing UI page'))
  s.feed(wheel())
  s.feed(wheel(right_scroll=1))
  s.feed(wheel())
  assert s.calls == [0]
  s.receiver.actions.assert_not_called()


def test_worker_recovery_is_bounded_and_requires_fresh_drive(setup):
  s = setup
  created = []

  def factory():
    created.append(Channel())
    return created[-1]
  s.receiver.channel_factory = factory
  s.receiver.close()
  s.clock[0] += 3
  s.sm.refresh(s.clock[0])
  s.receiver.poll()
  assert len(created) == 1
  for _ in range(3):
    s.receiver.close()
    s.clock[0] += 3
    s.sm.refresh(s.clock[0])
    s.receiver.poll()
  assert len(created) == 3 and s.receiver.channel is None
  s.clock[0] += 10
  s.receiver.poll()
  assert len(created) == 3


def test_a_worker_that_cannot_start_is_retried_after_a_pause_not_every_frame(setup):
  s = setup
  attempts = []

  def factory():
    attempts.append(s.clock[0])
    raise OSError('no process')
  s.receiver.channel_factory = factory
  s.receiver.close()
  s.clock[0] += 3
  s.sm.refresh(s.clock[0])
  for _ in range(100):
    s.receiver.poll()
  assert len(attempts) == 1 and s.receiver.failed and s.receiver.channel is None
  s.clock[0] += 2.1
  s.sm.refresh(s.clock[0])
  s.receiver.poll()
  assert len(attempts) == 2


def test_a_large_saved_document_is_read_alike_by_the_worker_and_the_receiver(setup):
  s = setup
  raw = (' ' * 16000 + json.dumps(s.doc)).encode()
  assert 16000 < len(raw) < MAX_ASSIGNMENT_BYTES == 16384, 'the size the Wheel Controls editor may write'
  s.params.put(PARAM, raw, block=True)
  s.feed(wheel())
  s.feed(wheel(right_scroll=1))
  s.feed(wheel())
  assert s.calls == [0]
  s.params.put(PARAM, (' ' * MAX_ASSIGNMENT_BYTES + json.dumps(s.doc)).encode(), block=True)
  s.feed(wheel())
  s.feed(wheel(right_scroll=1))
  s.feed(wheel())
  assert s.calls == [0], 'too large to read: no input acts'


def test_recovered_worker_must_see_neutral_before_a_new_gesture(setup):
  s = setup
  s.feed(wheel(right_scroll=2))
  s.feed(wheel())
  assert not s.calls
  s.feed(wheel(right_scroll=1))
  s.feed(wheel())
  assert s.calls == [0]
