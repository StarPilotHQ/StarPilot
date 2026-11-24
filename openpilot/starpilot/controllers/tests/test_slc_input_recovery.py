"""Tesla input failure paths: a failing receiver or worker never takes the other controllers down, never repeats an
input, and is started again after a wait that grows with each failure in a row."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.common.car_control_config import MAX_ASSIGNMENT_BYTES, PARAM
from openpilot.starpilot.controllers import tesla_receiver
from openpilot.starpilot.controllers.runtime import ControllerRuntime
from openpilot.starpilot.controllers.tests.test_tesla_decoder import wheel
from openpilot.starpilot.controllers.tests.tesla_worker_fixture import Channel, selected_rig
from openpilot.starpilot.tests.port_settings_fixture import Saved
from openpilot.starpilot.tests.tesla_set_speed_fixture import tesla


@pytest.fixture
def setup(tmp_path):
  return selected_rig(tmp_path)


def bare_runtime(presses=(), capture=None):
  r = ControllerRuntime.__new__(ControllerRuntime)
  vars(r).update(_closed=False, _next_warm=float('inf'), _warm_until=0, input_capture=lambda: capture,
                 reader=NS(poll=lambda _: presses, devices=list, close=Mock()),
                 owner=NS(set_devices=Mock(), tick=Mock(), feed=Mock()),
                 tesla=NS(poll=Mock(), close=Mock(), fail=Mock()), service=NS(poll=Mock(), close=Mock()), authority=NS(close=Mock()))
  return r


def test_tesla_exception_without_button_press_does_not_close_all_controllers():
  r = bare_runtime()
  r.tesla.poll.side_effect = ValueError('injected dispatch failure')
  r.poll()
  assert not r._closed
  r.tesla.fail.assert_called_once()
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


def recovering(s):
  """Swap in a channel factory that counts, and return (created channels, fail-and-time-the-restart)."""
  created = []

  def factory():
    created.append(Channel())
    return created[-1]
  s.receiver.channel_factory = factory

  def restart_after_failure():
    s.receiver.fail()
    failed_at = s.clock[0]
    assert s.receiver.channel is None and s.receiver.failed
    while s.receiver.channel is None:
      s.clock[0] += .5
      s.sm.refresh(s.clock[0])
      s.receiver.poll()
    return s.clock[0] - failed_at
  return created, restart_after_failure


def test_worker_recovery_waits_longer_each_time_and_never_gives_up(setup):
  # With Comma can set speed on Tesla dashboard on, the scroll only sets the speed while the worker runs, so a drive
  # never ends up without it: the wait grows to 30 s and then repeats.
  s = setup
  created, restart_after_failure = recovering(s)
  assert [restart_after_failure() for _ in range(8)] == [2., 4., 8., 16., 30., 30., 30., 30.]
  assert len(created) == 8 and s.receiver.channel is created[-1] and not s.receiver.failed


def test_a_worker_that_ran_for_a_minute_starts_the_waits_again(setup):
  s = setup
  created, restart_after_failure = recovering(s)
  assert [restart_after_failure() for _ in range(3)] == [2., 4., 8.]
  s.clock[0] += tesla_receiver.RESTART_HEALTHY_SECONDS - 1
  s.sm.refresh(s.clock[0])
  assert restart_after_failure() == 16., 'not yet a minute'
  s.clock[0] += tesla_receiver.RESTART_HEALTHY_SECONDS
  s.sm.refresh(s.clock[0])
  assert restart_after_failure() == 2.


def test_a_stopped_worker_is_restarted_through_the_same_wait(setup):
  # A backlog (the screen stalled while the driver scrolled) closes the worker; it is back after the wait.
  s = setup
  s.channel.queue.extend([b'{}'] * 500)
  s.receiver.poll()
  assert s.channel.closed and s.receiver.channel is None and s.receiver.failed
  created = []
  s.receiver.channel_factory = lambda: created.append(Channel()) or created[-1]
  s.clock[0] += 1.9
  s.sm.refresh(s.clock[0])
  s.receiver.poll()
  assert not created
  s.clock[0] += .2
  s.sm.refresh(s.clock[0])
  s.receiver.poll()
  assert s.receiver.channel is created[0]


def test_a_first_start_that_fails_is_not_retried_every_frame(tmp_path):
  clock, attempts = [1.], []

  def factory():
    attempts.append(clock[0])
    raise OSError('no process')
  receiver = tesla_receiver.TeslaReceiver(Saved(tmp_path), dict, tesla, clock=lambda: clock[0], channel_factory=factory)
  for _ in range(100):
    receiver.poll()
  assert attempts == [1.] and receiver.failed and receiver.channel is None
  clock[0] += 2.
  receiver.poll()
  assert attempts == [1., 3.]


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
