"""Tesla input worker and its receiver in the UI: messages, lifecycle and hand-off of one decoded control at a time."""
import json
import os
import shutil
import time
from types import SimpleNamespace as NS

import pytest
from openpilot.cereal import messaging
from openpilot.common.prefix import OpenpilotPrefix
from opendbc.car.structs import car
from opendbc.car.tesla.values import TeslaFlags
from openpilot.starpilot.common.car_control_config import (NO_LIMIT_REASON, PARAM, SPEED_DOWN, SPEED_RESUME, SPEED_UP, STALK_HOLD, STALK_TAP,
                                                           comma_speed, vehicle_supported)
from openpilot.starpilot.controllers.tesla_receiver import TeslaReceiver, WorkerChannel
from openpilot.starpilot.controllers.tesla_input_protocol import encode_event, decode_event, MAX_PACKET
from openpilot.starpilot.controllers.tests.tesla_worker_fixture import CUSTOM, Channel, Messages, build, save, selected_rig, stalk
from openpilot.starpilot.controllers.tests.test_tesla_decoder import wheel
from openpilot.starpilot.tests.port_settings_fixture import Saved
from openpilot.starpilot.tests.tesla_set_speed_fixture import switch, tesla


@pytest.fixture
def setup(tmp_path):
  return selected_rig(tmp_path)


@pytest.mark.parametrize('ticks', [1, 2, 3, 6])
def test_end_to_end_release_preserves_speed_action(setup, ticks):
  s = setup
  s.feed(wheel())
  s.feed(wheel(right_scroll=ticks))
  assert not s.calls
  s.feed(wheel())
  s.feed(wheel())
  assert s.calls == [0 if ticks == 1 else 1]


def test_scroll_down_runs_the_decrease_action(setup):
  s = setup
  s.actions[SPEED_DOWN] = NS(available=True, invoke_value=lambda fast: s.calls.append(('down', fast)), invoke=None)
  s.feed(wheel())
  s.feed(wheel(right_scroll=-1))
  s.feed(wheel())
  s.feed(wheel(right_scroll=-3))
  s.feed(wheel())
  assert s.calls == [('down', 0), ('down', 1)]


@pytest.mark.parametrize('saved', [None, SPEED_DOWN, CUSTOM, '__starpilot_controller_action__:none'])
def test_scroll_is_never_assignable(setup, saved):
  # Whatever was saved for the scroll (an old build could save one), a selected car's scroll sets the comma speed.
  s = setup
  if saved is not None:
    s.doc['bindings']['right_wheel_up'] = {'key': saved, 'value': None, 'label': 'Saved'}
    save(s)
  s.actions[SPEED_DOWN] = NS(available=True, invoke_value=lambda fast: s.calls.append('down'), invoke=None)
  s.feed(wheel())
  s.feed(wheel(right_scroll=1))
  s.feed(wheel())
  assert s.calls == [0]


@pytest.mark.parametrize('selected,comma', [(True, False), (False, True), (False, False)])
@pytest.mark.parametrize('saved', [None, SPEED_UP, CUSTOM])
def test_without_the_switch_the_scroll_is_left_to_the_tesla(tmp_path, selected, comma, saved):
  # The switch off, or on for a car that started without the scroll frame admitted: the scroll is the Tesla's own.
  s = build(tmp_path, tesla(selected=selected), comma=comma)
  assert vehicle_supported(s.cp) and not comma_speed(s.params, s.cp)
  if saved is not None:
    s.doc['bindings']['right_wheel_up'] = {'key': saved, 'value': None, 'label': 'Saved'}
    save(s)
  for ticks in (1, 3, -1, -3):
    s.feed(wheel())
    s.feed(wheel(right_scroll=ticks))
    s.feed(wheel())
  assert not s.calls and not s.channel.queue, 'the worker sends nothing for a scroll'
  s.feed(wheel(right_wheel_left=2))
  s.feed(wheel())
  assert s.calls == [CUSTOM], 'the other controls still work'


def test_the_switch_is_followed_within_a_second_without_a_restart(tmp_path):
  s = build(tmp_path, tesla(selected=True), comma=False)

  def scroll():
    s.feed(wheel())
    s.feed(wheel(right_scroll=1))
    s.feed(wheel())
    s.feed(wheel())
  scroll()
  assert s.calls == []
  switch(s.params, True)
  for _ in range(4):
    scroll()  # the worker reads the switch at most once a second
  assert s.calls and set(s.calls) == {0}, 'now the scroll sets the comma speed'
  taken = len(s.calls)
  switch(s.params, False)
  s.feed(wheel())
  s.feed(wheel(right_scroll=1))
  s.feed(wheel(), dispatch=False)
  queued = len(s.channel.queue)
  s.receiver.poll()
  assert len(s.calls) == taken, 'the screen reads the switch at once: an event the worker still sent is not acted on'
  for _ in range(4):
    scroll()
  assert len(s.calls) == taken and queued <= 1 and not s.channel.queue


def test_safe_mode_turns_the_comma_scroll_off_like_the_switch(setup):
  s = setup
  s.params.put('SafeMode', b'1', block=True)
  for _ in range(4):
    s.feed(wheel())
    s.feed(wheel(right_scroll=1))
    s.feed(wheel())
  assert s.calls == []


def test_end_to_end_stalk_hold_runs_its_action_instead_of_the_press(setup):
  s = setup
  s.doc['bindings'][STALK_HOLD] = {'key': SPEED_RESUME, 'value': None, 'label': 'Resume Speed Limit + Offset'}
  s.doc['bindings']['right_stalk_down_half'] = {'key': SPEED_UP, 'value': None, 'label': 'Up'}
  save(s)
  s.actions[SPEED_RESUME] = NS(available=True, invoke=lambda: s.calls.append('resume'), invoke_value=None)
  s.feed(stalk(0, 0), address=0x229)
  for counter in range(1, 10):
    s.feed(stalk(3, counter), address=0x229)
  assert s.calls == ['resume']
  s.feed(stalk(0, 10), address=0x229)
  assert s.calls == ['resume'], 'the release is not also a down press'
  s.feed(stalk(3, 11), address=0x229)
  s.feed(stalk(0, 12), address=0x229)
  assert s.calls == ['resume'], 'a plain down press (the engage press) has no action of its own'


def test_stalk_hold_is_fixed_whatever_is_saved_for_it(setup):
  s = setup
  s.doc['bindings'][STALK_HOLD] = {'key': CUSTOM, 'value': None, 'label': 'Custom'}
  save(s)
  s.actions[SPEED_RESUME] = NS(available=True, invoke=lambda: s.calls.append('resume'), invoke_value=None)
  s.feed(stalk(0, 0), address=0x229)
  for counter in range(1, 10):
    s.feed(stalk(3, counter), address=0x229)
  assert s.calls == ['resume']


def test_end_to_end_tap_while_engaged(setup):
  s = setup
  s.doc['bindings'][STALK_TAP] = {'key': SPEED_RESUME, 'value': None, 'label': 'Resume Speed Limit + Offset'}
  s.doc['bindings']['right_stalk_down_full'] = {'key': SPEED_UP, 'value': None, 'label': 'Up'}
  save(s)
  s.actions[SPEED_RESUME] = NS(available=True, invoke=lambda: s.calls.append('resume'), invoke_value=None)
  s.feed(stalk(0, 0), address=0x229)
  s.feed(stalk(4, 1), address=0x229)
  s.feed(stalk(0, 2), address=0x229)
  assert s.calls == [], 'the press that engages has no action of its own'
  s.sm.engaged = True
  s.feed(stalk(3, 3), address=0x229)
  s.feed(stalk(0, 4), address=0x229)
  assert s.calls == [], 'cruise only just came on: still part of engaging'
  for _ in range(6):
    s.feed(stalk(0, 4), address=0x229)  # cruise on for 0.6 s
  s.feed(stalk(3, 5), address=0x229)
  s.feed(stalk(0, 6), address=0x229)
  assert s.calls == ['resume']


def test_engaging_press_runs_no_action_while_engaged(setup):
  # The car turns cruise on during the press that engages it, so the press's first frames already see cruise on.
  s = setup
  s.doc['bindings'][STALK_TAP] = {'key': SPEED_RESUME, 'value': None, 'label': 'Resume Speed Limit + Offset'}
  save(s)
  s.actions[SPEED_RESUME] = NS(available=True, invoke=lambda: s.calls.append('resume'), invoke_value=None)
  s.feed(stalk(0, 0), address=0x229)
  s.sm.engaged = True
  s.feed(stalk(3, 1), address=0x229)
  s.feed(stalk(0, 2), address=0x229)
  assert s.calls == []
  for _ in range(6):
    s.feed(stalk(0, 2), address=0x229)
  s.feed(stalk(3, 3), address=0x229)
  s.feed(stalk(0, 4), address=0x229)
  assert s.calls == ['resume']


def nothing_saved(s, saved):
  """An empty saved document, or (None) an install where the Wheel Controls were never saved: no file at all."""
  if saved is None:
    os.remove(s.params.get_param_path(PARAM))
  else:
    s.params.put(PARAM, saved, block=True)


@pytest.mark.parametrize('saved', [b'{}', None])
def test_hold_and_half_press_work_with_nothing_saved(setup, saved):
  # The right stalk hold is fixed and the half press while engaged starts on Resume Speed Limit + Offset.
  s = setup
  nothing_saved(s, saved)
  s.actions[SPEED_RESUME] = NS(available=True, invoke=lambda: s.calls.append('resume'), invoke_value=None)
  s.sm.engaged = True
  for _ in range(6):
    s.feed(stalk(0, 0), address=0x229)  # cruise already on for 0.6 s
  s.feed(stalk(3, 1), address=0x229)
  s.feed(stalk(0, 2), address=0x229)
  assert s.calls == ['resume']
  for counter in range(3, 12):
    s.feed(stalk(3, counter), address=0x229)
  assert s.calls == ['resume', 'resume']


@pytest.mark.parametrize('saved', [b'{}', None])
def test_the_scroll_sets_the_comma_speed_with_nothing_saved(setup, saved):
  s = setup
  nothing_saved(s, saved)
  s.feed(wheel())
  s.feed(wheel(right_scroll=1))
  s.feed(wheel())
  assert s.calls == [0]


def test_the_file_removed_mid_drive_leaves_the_defaults(setup):
  s = setup
  s.feed(wheel())
  s.feed(wheel(right_wheel_left=2))
  s.feed(wheel())
  assert s.calls == [CUSTOM], 'the saved nudge left'
  os.remove(s.params.get_param_path(PARAM))
  for scroll in (0, 1, 0):
    s.feed(wheel(right_scroll=scroll))
  s.feed(wheel(right_wheel_left=2))
  s.feed(wheel())
  assert s.calls == [CUSTOM, 0], 'the scroll still acts; the nudge left has no default'


def test_assignments_saved_on_the_other_model_are_used(setup):
  # A Model 3 and a Model Y have the same controls, so a comma moved from one to the other keeps its assignments.
  s = setup
  assert s.cp.carFingerprint == 'TESLA_MODEL_3'
  s.doc['vehicle'] = 'TESLA_MODEL_Y'
  save(s)
  s.feed(wheel())
  s.feed(wheel(right_wheel_left=2))
  s.feed(wheel())
  s.feed(wheel(right_scroll=1))
  s.feed(wheel())
  assert s.calls == [CUSTOM, 0]


@pytest.mark.parametrize('fault', ['safe', 'config', 'invalid', 'wrong_bus', 'offroad', 'vehicle', 'stale'])
def test_held_gesture_cannot_cross_context(setup, fault):
  s = setup
  s.feed(wheel())
  s.feed(wheel(right_scroll=2))
  if fault == 'safe':
    s.params.put('SafeMode', b'1', block=True)
  if fault == 'config':
    s.doc['bindings']['right_wheel_left']['label'] = 'Changed'
    save(s)
  if fault == 'vehicle':
    s.cp.mass = 1234.
    s.pump.set_vehicle(s.cp)
  if fault == 'offroad':
    s.sm.refresh(s.clock[0], started=False)
    s.pump.poll(s.sm)
  s.feed(wheel(), valid=fault != 'invalid', bus=0 if fault == 'wrong_bus' else 1,
         delta=1 if fault == 'stale' else .1)
  assert not s.calls


@pytest.mark.parametrize('fault', ['safe', 'config', 'vehicle', 'offroad', 'stale', 'can_invalid', 'availability', 'unselected'])
def test_ui_revalidates_worker_event(setup, fault):
  s = setup
  s.feed(wheel())
  s.feed(wheel(right_scroll=2))
  s.feed(wheel(), dispatch=False)
  assert len(s.channel.queue) == 1
  if fault == 'safe':
    s.params.put('SafeMode', b'1', block=True)
  if fault == 'config':
    s.doc['bindings']['right_wheel_left']['label'] = 'New'
    save(s)
  if fault == 'vehicle':
    s.cp.mass = 1234.
  if fault == 'offroad':
    s.sm['deviceState'].started = False
  if fault == 'stale':
    s.clock[0] += .251
  if fault == 'can_invalid':
    s.sm['carState'].canValid = False
  if fault == 'availability':
    s.actions[SPEED_UP].available = False
  if fault == 'unselected':
    s.cp.safetyConfigs[0].safetyParam = 1  # the comma no longer owns the set speed
  s.receiver.poll()
  assert not s.calls


def test_revalidation_after_action_discovery(setup):
  s = setup
  s.feed(wheel())
  s.feed(wheel(right_scroll=2))
  s.feed(wheel(), dispatch=False)

  def actions():
    s.params.put('SafeMode', b'1', block=True)
    return s.actions
  s.receiver.actions = actions
  s.receiver.poll()
  assert not s.calls


def test_duplicate_event_does_not_repeat(setup):
  s = setup
  s.feed(wheel())
  s.feed(wheel(right_scroll=2))
  s.feed(wheel(), dispatch=False)
  packet = s.channel.queue[0]
  s.channel.queue.append(packet)
  s.receiver.poll()
  assert s.calls == [1]


def test_no_can_or_settings_work_without_event(setup):
  # An idle poll only asks which car this is and drains the worker's channel.
  s = setup

  def forbidden(*a, **kw):
    raise AssertionError('idle UI accessed source context')
  s.receiver.params = NS(get_param_path=forbidden)
  s.receiver.messages = s.receiver.actions = forbidden
  for _ in range(1000):
    s.receiver.poll()
  assert s.channel.receives == 1000


def test_backlog_is_bounded_and_discarded(setup):
  s = setup
  s.channel.queue.extend([b'{}'] * 500)
  s.receiver.poll()
  assert s.channel.receives == 5
  assert s.channel.closed
  assert not s.calls
  # Never immediately replay a discarded backlog. Recovery has a backoff.
  s.receiver.poll()
  assert s.receiver.channel is None


@pytest.mark.parametrize('patch', [
  {'stamp': float('nan')}, {'stamp': float('inf')}, {'stamp': 1.3}, {'stamp': .7},
  {'sequence': True}, {'sequence': 0}, {'version': True}, {'version': 2}, {'fast': 1}, {'drive': 0},
  {'document': 'x' * 64}, {'vehicle': None}, {'extra': 'field'}, {'name': 3},
])
def test_protocol_rejects_malformed_or_stale(patch):
  d = json.loads(encode_event(1, 'right_wheel_up', True, 1., 100_000_000, 'a' * 64, 'b' * 64))
  d.update(patch)
  assert decode_event(json.dumps(d).encode(), 1.) is None


def test_protocol_size_and_boundary():
  packet = encode_event(1, 'right_wheel_up', True, 1., 100_000_000, 'a' * 64, 'b' * 64)
  assert decode_event(packet, 1.25) is not None
  assert decode_event(packet, 1.250001) is None
  assert decode_event(b' ' * (MAX_PACKET + 1), 1) is None


def test_protocol_carries_exactly_the_event_and_its_context():
  packet = encode_event(7, 'right_wheel_down', False, 1., 100_000_000, 'a' * 64, 'b' * 64)
  assert decode_event(packet, 1.1) == {'version': 1, 'sequence': 7, 'name': 'right_wheel_down', 'fast': False, 'stamp': 1.,
                                       'drive': 100_000_000, 'document': 'a' * 64, 'vehicle': 'b' * 64}


def test_unrelated_frames_never_convert_payload(setup):
  s = setup

  class Payload:
    def __bytes__(self):
      raise AssertionError('unrelated payload converted')
  s.frames.append(NS(valid=True, logMonoTime=int(s.clock[0] * 1e9),
                     can=[NS(src=0, address=0x3c2, dat=Payload()),
                          NS(src=1, address=0x555, dat=Payload())]))
  s.pump.poll(s.sm)
  assert not s.calls


def test_worker_tick_is_bounded(setup):
  s = setup
  s.frames.extend(NS(valid=True, logMonoTime=int(s.clock[0] * 1e9), can=[]) for _ in range(1000))
  s.pump.poll(s.sm)
  assert len(s.frames) == 744


def test_transport_overflow_fails_without_replay(setup):
  s = setup
  s.feed(wheel())
  s.feed(wheel(right_scroll=2))

  def fail(_):
    raise BlockingIOError()
  s.pump.send = fail
  with pytest.raises(BlockingIOError):
    s.feed(wheel())
  assert s.pump.decoder is None
  assert not s.calls


def test_worker_real_process_lifecycle():
  channel = WorkerChannel()
  try:
    # Real imports, isolated Params/MSGQ, scheduler reset and socket closure.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
      assert channel.receive() is None
      if channel.ready:
        break
      time.sleep(.01)
    assert channel.ready, "worker did not complete native startup"
    pid = channel.process.pid
    assert pid != os.getpid()
    assert os.sched_getscheduler(pid) == os.SCHED_OTHER
    assert os.getpriority(os.PRIO_PROCESS, pid) == 10
    assert os.sched_getaffinity(pid).issubset({0, 1, 2, 3})
  finally:
    channel.close()
  assert channel.process.poll() is not None


def test_lost_telemetry_does_not_relaunch_failed_worker(setup):
  s = setup
  s.receiver.close()
  s.receiver.next_start = 0
  s.sm['carState'].canValid = False
  s.receiver.poll()
  assert s.receiver.channel is None
  s.sm.refresh(s.clock[0], started=False)
  s.receiver.poll()
  assert s.receiver.channel is s.channel


@pytest.mark.parametrize('value', [None, 0, 2.5])
def test_generic_assigned_actions_keep_value_semantics(setup, value):
  s = setup
  s.doc['bindings']['right_wheel_left'] = {'key': CUSTOM, 'value': value, 'label': 'Action'}
  save(s)
  s.actions[CUSTOM] = NS(available=True, invoke=lambda: s.calls.append('invoke'), invoke_value=s.calls.append)
  s.feed(wheel())
  s.feed(wheel(right_wheel_left=2))
  s.feed(wheel())
  assert s.calls == ['invoke' if value is None else value]


def test_peer_close_stops_worker_without_gesture():
  channel = WorkerChannel()
  try:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not channel.ready:
      assert channel.receive() is None
      time.sleep(.01)
    assert channel.ready
    channel.socket.close()
    assert channel.process.wait(timeout=5) == 0
  finally:
    channel.close()


@pytest.mark.parametrize('published', [True, False])
def test_real_worker_can_to_ui_dispatch(tmp_path, monkeypatch, published):
  # Explicit private Params root; the child inherits it. No live device settings.
  # published False: the worker starts mid-drive, between card's broadcasts of the car's parameters, and reads this
  # drive's saved copy.
  from openpilot.common.params import Params
  root = tmp_path / 'native-params'
  root.mkdir()
  monkeypatch.setenv('PARAMS_ROOT', str(root))
  cp = tesla(selected=True)
  # Its own messaging and settings namespace, which the worker inherits: nothing is shared with another test.
  prefix = OpenpilotPrefix(clean_dirs_on_exit=False)
  with prefix:
    params = Params(str(root))
    params.put(PARAM, {'version': 1, 'vehicle': 'TESLA_MODEL_3', 'bindings': {}}, block=True)
    params.put_bool('TeslaSpeedSync', True, block=True)
    if not published:
      params.put('CarParams', cp.as_reader().as_builder().to_bytes(), block=True)
    pm = messaging.PubMaster(['deviceState', 'carState', 'carParams', 'can'])
    channel = WorkerChannel()
    sm = Messages()
    calls = []
    receiver = TeslaReceiver(params, lambda: {SPEED_UP: NS(available=True, invoke=None, invoke_value=calls.append)},
                             lambda: cp, messages=lambda: sm, channel_factory=lambda: channel)
    try:
      deadline = time.monotonic() + 15
      while time.monotonic() < deadline and not channel.ready:
        receiver.poll()
        time.sleep(.01)
      assert channel.ready and receiver.channel is channel
      start = time.monotonic()
      drive = int((start - 1) * 1e9)
      # Fresh telemetry and real CAN neutral -> scroll -> neutral across processes.
      while time.monotonic() - start < 1.2:
        now = time.monotonic()
        elapsed = now - start
        stamp = int(now * 1e9)
        sm.refresh(now)
        sm['deviceState'].startedMonoTime = drive
        state = messaging.new_message('deviceState', valid=True, logMonoTime=stamp)
        state.deviceState.started = True
        state.deviceState.startedMonoTime = drive
        pm.send('deviceState', state)
        state = messaging.new_message('carState', valid=True, logMonoTime=stamp)
        state.carState.canValid = True
        pm.send('carState', state)
        # Publish CP only during startup, not continually resetting a held gesture.
        if published and elapsed < .2:
          state = messaging.new_message('carParams', valid=True, logMonoTime=stamp)
          state.carParams = cp
          pm.send('carParams', state)
        # CAN is stamped on the boot clock, which the worker translates to its own.
        event = messaging.new_message('can', 1, valid=True, logMonoTime=time.clock_gettime_ns(time.CLOCK_BOOTTIME))
        event.can[0] = {'address': 0x3c2, 'src': 1, 'dat': wheel(right_scroll=2 if .4 <= elapsed < .6 else 0)}
        pm.send('can', event)
        receiver.poll()
        time.sleep(.01)
      assert calls == [1]
    finally:
      receiver.close()
  shutil.rmtree(prefix.msgq_path, ignore_errors=True)


def test_stalk_resume_comes_from_the_comma_speed_lookup(setup):
  # Resume comes from the same quick lookup as the scroll wheel's comma-speed steps, not from the favorites catalog.
  s = setup
  s.doc['bindings'][STALK_TAP] = {'key': SPEED_RESUME, 'value': None, 'label': 'Resume Speed Limit + Offset'}
  save(s)
  resume = NS(available=True, invoke=lambda: s.calls.append('resume') or True, invoke_value=None)
  s.receiver.speed_actions = lambda: {SPEED_RESUME: resume}
  s.receiver.keyed_actions = lambda keys: {}
  s.sm.engaged = True
  for _ in range(6):
    s.feed(stalk(0, 0), address=0x229)  # cruise already on for 0.6 s
  s.feed(stalk(3, 1), address=0x229)
  s.feed(stalk(0, 2), address=0x229)
  assert s.calls == ['resume']


def _resume_during_dropout(s):
  """A half press while engaged bound to Resume, whose speed limit is missing until s.limit is set True."""
  s.doc['bindings'][STALK_TAP] = {'key': SPEED_RESUME, 'value': None, 'label': 'Resume Speed Limit + Offset'}
  save(s)
  s.limit = False
  s.reason = NO_LIMIT_REASON

  def lookup():
    return {SPEED_RESUME: NS(available=s.limit, reason='' if s.limit else s.reason,
                             invoke=lambda: s.calls.append('resume') or True, invoke_value=None)}
  s.receiver.speed_actions = lookup
  s.receiver.keyed_actions = lambda keys: {}
  s.sm.engaged = True
  for _ in range(6):
    s.feed(stalk(0, 0), address=0x229)
  s.feed(stalk(3, 1), address=0x229)
  s.feed(stalk(0, 2), address=0x229)  # the press, while the limit is missing
  assert s.calls == []


def test_resume_during_a_limit_dropout_runs_once_the_limit_is_back(setup):
  s = setup
  _resume_during_dropout(s)
  for _ in range(10):
    s.feed(stalk(0, 2), address=0x229)  # 1 s without a limit
  assert s.calls == []
  s.limit = True
  s.feed(stalk(0, 2), address=0x229)
  assert s.calls == ['resume']
  for _ in range(30):
    s.feed(stalk(0, 2), address=0x229)
  assert s.calls == ['resume'], 'once only'


def test_resume_is_refused_when_the_limit_stays_away(setup):
  s = setup
  _resume_during_dropout(s)
  for _ in range(21):
    s.feed(stalk(0, 2), address=0x229)  # 2.1 s without a limit
  s.limit = True
  for _ in range(10):
    s.feed(stalk(0, 2), address=0x229)
  assert s.calls == []


@pytest.mark.parametrize('change', ['reason', 'assignments', 'safe'])
def test_waiting_resume_is_dropped_when_things_change(setup, change):
  s = setup
  _resume_during_dropout(s)
  if change == 'reason':
    s.reason = 'Speed Limit Controller is not running'
  elif change == 'assignments':
    s.doc['bindings']['right_wheel_left']['label'] = 'Changed'
    save(s)
  else:
    s.params.put('SafeMode', b'1', block=True)
  s.feed(stalk(0, 2), address=0x229)
  s.limit = True
  for _ in range(10):
    s.feed(stalk(0, 2), address=0x229)
  assert s.calls == []


def test_two_presses_during_one_dropout_resume_once(setup):
  s = setup
  _resume_during_dropout(s)
  s.feed(stalk(3, 3), address=0x229)
  s.feed(stalk(0, 4), address=0x229)  # a second press, still without a limit, replaces the first
  s.limit = True
  for _ in range(10):
    s.feed(stalk(0, 4), address=0x229)
  assert s.calls == ['resume']


def _refusing_scroll(s, results):
  """Increase Comma Set Speed whose UI checks refuse the step (nothing published) for each False in results."""
  attempts = []

  def step(fast):
    attempts.append(fast)
    applied = results.pop(0) if results else True
    if applied:
      s.calls.append(fast)
    return applied
  s.actions[SPEED_UP] = NS(available=True, invoke_value=step, invoke=None)
  s.feed(wheel())
  s.feed(wheel(right_scroll=1))
  s.feed(wheel(), dispatch=False)
  return attempts


def test_a_refused_scroll_step_is_tried_once_more_on_the_next_frame(setup):
  s = setup
  attempts = _refusing_scroll(s, [False])
  s.receiver.poll()
  assert attempts == [0]
  s.feed(wheel())
  assert attempts == [0, 0] and s.calls == [0], 'one retry, one step'
  for _ in range(5):
    s.feed(wheel())
  assert s.calls == [0]


def test_a_scroll_step_is_retried_only_once(setup):
  s = setup
  attempts = _refusing_scroll(s, [False, False])
  s.receiver.poll()
  for _ in range(5):
    s.feed(wheel())
  assert attempts == [0, 0] and s.calls == []


def test_an_expired_scroll_step_is_not_retried(setup):
  s = setup
  attempts = _refusing_scroll(s, [False])
  s.receiver.poll()
  s.clock[0] += .3
  s.sm.refresh(s.clock[0])
  s.receiver.poll()
  assert attempts == [0] and s.calls == []


# The worker only runs on a supported Tesla: a Model 3/Y whose CAN add-on cable was detected.

def other_car(brand='hyundai', fingerprint='HYUNDAI_IONIQ_5'):
  return car.CarParams.new_message(carFingerprint=fingerprint, brand=brand, flags=int(TeslaFlags.HAS_VEHICLE_BUS),
                                   pcmCruise=True, openpilotLongitudinalControl=True)


UNSUPPORTED = {
  'no car yet': lambda: None,
  'hyundai': other_car,
  'toyota': lambda: other_car('toyota', 'TOYOTA_RAV4_TSS2'),
  'another brand with a tesla name': lambda: other_car('mock', 'TESLA_MODEL_3'),
  'model s': lambda: tesla('TESLA_MODEL_S_HW1'),
  'model x': lambda: tesla('TESLA_MODEL_X'),
  'model 3 without the cable': lambda: tesla(cable=False),
  'model y without the cable': lambda: tesla('TESLA_MODEL_Y', cable=False),
  'dashcam': lambda: car.CarParams.new_message(carFingerprint='TESLA_MODEL_3', brand='tesla', dashcamOnly=True,
                                               flags=int(TeslaFlags.HAS_VEHICLE_BUS)),
  'passive': lambda: car.CarParams.new_message(carFingerprint='TESLA_MODEL_3', brand='tesla', passive=True,
                                               flags=int(TeslaFlags.HAS_VEHICLE_BUS)),
  'not a car': lambda: car.CarParams.new_message(carFingerprint='TESLA_MODEL_3', brand='tesla', notCar=True,
                                                 flags=int(TeslaFlags.HAS_VEHICLE_BUS)),
}


def counting_receiver(tmp_path, vehicle):
  started = []

  def factory():
    started.append(Channel())
    return started[-1]
  sm = Messages()
  sm.refresh(1.)
  receiver = TeslaReceiver(Saved(tmp_path), dict, vehicle, clock=lambda: 1., messages=lambda: sm, channel_factory=factory)
  return receiver, started


@pytest.mark.parametrize('case', UNSUPPORTED)
def test_no_worker_is_ever_started_for_an_unsupported_car(tmp_path, case):
  cp = UNSUPPORTED[case]()
  assert not vehicle_supported(cp)
  receiver, started = counting_receiver(tmp_path, lambda: cp)
  assert receiver.channel is None and not started, 'creating the receiver starts nothing'
  for _ in range(50):
    receiver.poll()
  assert receiver.channel is None and not started
  receiver.close()


@pytest.mark.parametrize('candidate', ['TESLA_MODEL_3', 'TESLA_MODEL_Y'])
@pytest.mark.parametrize('selected', [False, True])
def test_the_worker_starts_once_a_supported_tesla_is_known(tmp_path, candidate, selected):
  # The cable is enough: the stalk, nudges and screen tap work with or without the set-speed switch.
  current = [None]
  receiver, started = counting_receiver(tmp_path, lambda: current[0])
  for _ in range(5):
    receiver.poll()
  assert not started, 'no car parameters yet'
  current[0] = tesla(candidate, selected=selected)
  receiver.poll()
  assert len(started) == 1 and receiver.channel is started[0]
  for _ in range(20):
    receiver.poll()
  assert len(started) == 1, 'one worker'


def test_the_worker_is_closed_when_the_car_is_no_longer_a_supported_tesla(tmp_path):
  current = [tesla()]
  receiver, started = counting_receiver(tmp_path, lambda: current[0])
  receiver.poll()
  assert receiver.channel is started[0] and not started[0].closed
  current[0] = other_car()
  receiver.poll()
  assert receiver.channel is None and started[0].closed
  for _ in range(20):
    receiver.poll()
  assert len(started) == 1
  current[0] = None
  receiver.poll()
  assert receiver.channel is None and len(started) == 1


def test_the_real_worker_process_is_not_spawned_for_another_car(tmp_path, monkeypatch):
  from openpilot.starpilot.controllers import tesla_receiver
  spawned = []
  monkeypatch.setattr(tesla_receiver.subprocess, 'Popen', lambda *a, **kw: spawned.append(a) or NS(poll=lambda: 0))
  cp = other_car()
  receiver = TeslaReceiver(Saved(tmp_path), dict, lambda: cp)
  for _ in range(10):
    receiver.poll()
  receiver.close()
  assert not spawned and receiver.channel is None
