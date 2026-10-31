from collections import deque
from pathlib import Path
import time
from types import SimpleNamespace as NS

import pytest

from openpilot.cereal import log, messaging
from openpilot.common.params import Params
from openpilot.starpilot.controllers.cruise_action import CruiseActionPublisher
from openpilot.starpilot.controllers.mode_actions import ModeActionPublisher, ModeIntent, publish_switchback
from openpilot.starpilot.controllers.tests.test_wheel_actions import Publisher, candidate, services, DRIVE, NOW
from openpilot.starpilot.controllers.wheel_actions import WheelPublisher, WheelConsumer
from openpilot.starpilot.favorites.actions import BOOKMARK, SET_SPEED, TRAFFIC, FORCE_COAST
from openpilot.starpilot.favorites.owner import FavoritesOwner, default_slots
from openpilot.starpilot.ui.onroad_state import OnroadInput, OnroadState, SpeedLimitObservation, personality_bounds
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.settings_state import Destination
from openpilot.starpilot.ui.shell import ShellMode, ShellRequest


@pytest.fixture
def native_session(tmp_path, monkeypatch):
  from openpilot.starpilot.ui import runtime_app
  from openpilot.selfdrive.ui import ui_state as module
  params = Params(str(tmp_path))
  _, cp = candidate(False)
  sm = services(cp)
  sm['selfdriveState'] = NS(enabled=True)
  status = publish_switchback(None, ModeIntent(DRIVE, False, False), session='b' * 32,
                             sequence=1, now_ns=NOW, source_car_control_ns=NOW)
  sm['slcState'] = status.slcState
  for key in ('selfdriveState', 'slcState'):
    sm.logMonoTime[key], sm.recv_time[key] = NOW, NOW / 1e9
    sm.seen[key] = sm.alive[key] = sm.valid[key] = True
  ui = NS(CP=cp, sm=sm, params=params, has_longitudinal_control=True, personality=0, started_frame=1, started=True)
  sm.recv_frame = dict.fromkeys(sm, 2)
  monkeypatch.setattr(runtime_app, 'ui_state', ui)
  monkeypatch.setattr(runtime_app.time, 'monotonic_ns', lambda: NOW)
  monkeypatch.setattr(module, 'device', NS(awake=True, toggle_screen_off=lambda: True))
  sender = Publisher()
  monkeypatch.setattr(runtime_app, '_slc_action_publisher', lambda: sender)
  session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
  session._mode = ShellMode.ONROAD
  state = OnroadState(True, True, 15, 80, SpeedLimitObservation())
  vars(session)['snapshot'] = lambda *_: NS(onroad=state)
  vars(session)['_favorite_authority'] = lambda **_: True
  vars(session)['_conditional_favorite_active'] = lambda: False
  session.mode_actions, session.cruise_actions = ModeActionPublisher(), CruiseActionPublisher()
  session._wheel_personality = None
  session._wheel_consumer, session._wheel_pending = WheelConsumer(), deque(maxlen=32)
  session._wheel_sock = object()
  session.slc_actions = None
  calls = []
  session._native_favorite_actions = lambda: session.native_favorite_actions(lambda: calls.append('bookmark') or True,
                                                                           lambda value: calls.append(value), lambda: True)
  session.favorites_owner = FavoritesOwner(params, session._native_favorite_actions, lambda: True)
  return runtime_app, session, ui, sender, calls


def test_actual_favorite_value_callback_reads_units_and_publishes_absolute_receipt(native_session):
  _, session, ui, sender, _ = native_session
  ui.params.put('IsMetric', True, block=True)
  slots = default_slots()
  slots[0].update(enabled=True, key=SET_SPEED, label='Set speed', value=55)
  owner = session.favorites_owner
  owner.save(slots, owner.snapshot().revision)
  request = owner.snapshot().slots[0].request
  assert owner.invoke(request).success
  event = messaging.log_from_bytes(sender.events[-1][1])
  assert str(event.slcAction.kind) == 'cruiseSet'
  assert event.slcAction.controllerCruise.targetSpeedMps == pytest.approx(55 / 3.6)
  vars(session)['_favorite_authority'] = lambda **_: False
  assert not owner.invoke(owner.snapshot().slots[0].request).success
  assert len(sender.events) == 1


@pytest.mark.parametrize('metric', [False, True])
def test_galaxy_speed_edit_reaches_native_callback_without_activation_or_stale_overwrite(native_session, metric):
  from openpilot.starpilot.galaxy.favorites import FavoritesGateway
  from openpilot.starpilot.galaxy.settings import AuthorityContext
  _, session, ui, sender, _ = native_session
  ui.params.put_bool('IsMetric', metric, block=True)
  slots = default_slots()
  slots[0].update(enabled=True, show_onroad=False, key=SET_SPEED, label='Cruise', value=30)
  slots[1].update(enabled=True, show_onroad=True, key=BOOKMARK, label='Mark')
  owner = session.favorites_owner
  owner.save(slots, owner.snapshot().revision)
  old_request = owner.snapshot().slots[0].request
  gateway = FavoritesGateway(ui.params, NS(sample=lambda: AuthorityContext(False, None, None)))
  try:
    data = gateway.snapshot()
    data['slots'][0]['value'] = 45
    saved = gateway.save({'revision': data['revision'], 'slots': data['slots']}, session_valid=lambda: True)
    assert saved['slots'] == [{**slots[0], 'value': 45}, *slots[1:]]
    assert gateway.snapshot()['slots'] == saved['slots']
    assert sender.events == []
    assert not owner.invoke(old_request).success
    assert sender.events == []
    assert owner.invoke(owner.snapshot().slots[0].request).success
    event = messaging.log_from_bytes(sender.events[-1][1])
    assert str(event.slcAction.kind) == 'cruiseSet'
    assert event.slcAction.controllerCruise.targetSpeedMps == pytest.approx(45 / 3.6 if metric else 45 * .44704)
  finally:
    gateway.close()


def test_card_receipt_invokes_real_native_bookmark_and_quick_select_callback(native_session, monkeypatch):
  runtime, session, ui, _, calls = native_session
  source, publisher = WheelPublisher(ui.params), Publisher()
  queue = []
  monkeypatch.setattr(runtime.messaging, 'recv_one_or_none', lambda _: queue.pop(0) if queue else None)
  for sequence, code in enumerate((8, 11), start=1):
    ui.params.put('DistanceButtonControl', code, block=True)
    if code == 11:
      owner = session.favorites_owner
      assert owner.configure_index(0, BOOKMARK, owner.snapshot().revision)
    source.publish((('DistanceButtonControl', code),), ui.CP, publisher, now_ns=NOW, drive_id=DRIVE,
                   source_car_ns=NOW, source_control_ns=NOW)
    queue.append(messaging.log_from_bytes(publisher.events[-1][1]))
    session._poll_wheel(NOW)
    assert len(calls) == sequence
  assert calls == ['bookmark', 'bookmark']


def test_actual_native_traffic_and_coast_callbacks_emit_owner_requests(native_session):
  _, session, _, sender, _ = native_session
  actions = session._native_favorite_actions()
  assert actions[TRAFFIC].invoke()
  session.mode_actions.last_ns = 0
  assert actions[FORCE_COAST].invoke()
  assert [str(messaging.log_from_bytes(raw).slcAction.kind) for _, raw in sender.events] == [
    'trafficModeToggle', 'forceCoastToggle']

@pytest.mark.parametrize('vehicle,filtered', [('TOYOTA_PRIUS', True), ('TOYOTA_SIENNA_4TH_GEN', False)])
def test_toyota_default_gap_callback_saves_personality_once(native_session, monkeypatch, vehicle, filtered):
  from opendbc.car import structs
  from opendbc.car.toyota.interface import CarInterface
  from opendbc.car.toyota.values import CAR
  runtime, session, ui, _, _ = native_session
  rack = structs.CarParams.CarFw.new_message(ecu=structs.CarParams.Ecu.eps,
                                            fwVersion=b'8965B47070\x00\x00\x00\x00\x00\x00')
  ui.CP = CarInterface.get_params(getattr(CAR, vehicle), {0: {0x2FF: 8} if filtered else {}, 1: {}, 2: {}},
                                  [rack] if filtered else [], False, False, False)
  ui.has_longitudinal_control = ui.CP.openpilotLongitudinalControl
  ui.personality = 1
  def save_personality(value):
    ui.params.put('LongitudinalPersonality', value, block=True)
    ui.personality = value
    return True
  session._native_favorite_actions = lambda: session.native_favorite_actions(lambda: True, save_personality, lambda: True)
  queue = []
  monkeypatch.setattr(runtime.messaging, 'recv_one_or_none', lambda _: queue.pop(0) if queue else None)
  owner, publisher = WheelPublisher(), Publisher()
  from openpilot.starpilot.controllers.tests.test_wheel_actions import state
  owner.observe(ui.params, ui.CP, state(), now_ns=NOW - 20_000_000, drive_id=DRIVE)
  owner.observe(ui.params, ui.CP, state(True), now_ns=NOW - 10_000_000, drive_id=DRIVE)
  commands = owner.observe(ui.params, ui.CP, state(False), now_ns=NOW, drive_id=DRIVE)
  assert commands == (('DistanceButtonControl', 1),)
  assert owner.suppress_distance_release
  owner.publish(commands, ui.CP, publisher, now_ns=NOW, drive_id=DRIVE, source_car_ns=NOW, source_control_ns=NOW)
  queue.append(messaging.log_from_bytes(publisher.events[-1][1]))
  session._poll_wheel(NOW)
  assert ui.params.get('LongitudinalPersonality') == 0
  session._poll_wheel(NOW)
  assert ui.params.get('LongitudinalPersonality') == 0


@pytest.fixture
def personality_session(native_session, monkeypatch):
  from openpilot.selfdrive.ui import ui_state as module
  runtime, session, ui, _, _ = native_session
  ui.started = ui.engaged = ui._started_prev = ui._engaged_prev = True
  ui._engaged_transition_callbacks = []
  ui._offroad_transition_callbacks = []
  ui.add_engaged_transition_callback = ui._engaged_transition_callbacks.append
  ui.update_params = lambda: None
  ui.sm.frame = 100
  ui.sm.seen = dict(ui.sm.seen)
  ui.sm.alive = dict(ui.sm.alive)
  ui.sm.valid = dict(ui.sm.valid)
  ui.sm.updated = dict.fromkeys(ui.sm, True)
  ui.sm.recv_frame = dict.fromkeys(ui.sm, ui.sm.frame)
  ui.sm['selfdriveState'] = log.SelfdriveState.new_message(enabled=True, state='enabled', personality='standard')
  ui.params.put('LongitudinalPersonality', 1, block=True)
  ui.personality = 1
  clock = [NOW]
  monkeypatch.setattr(runtime.time, 'monotonic_ns', lambda: clock[0])
  return runtime, session, ui, module, clock


@pytest.mark.parametrize('profile', ['c4', 'c3'])
def test_hidden_native_personality_owner_completes_three_distance_cycles(personality_session, monkeypatch, profile):
  import pyray as rl

  from openpilot.selfdrive.ui.layouts.settings import common
  from openpilot.selfdrive.ui.layouts.settings import toggles as large
  from openpilot.selfdrive.ui.mici.layouts.settings import toggles as compact
  from openpilot.selfdrive.ui.mici.widgets import button
  from openpilot.starpilot.controllers.tests.test_wheel_actions import state
  from openpilot.system.ui.lib.application import gui_app

  runtime, session, ui, module, clock = personality_session
  monkeypatch.setattr(common, 'ui_state', ui)
  monkeypatch.setattr(large, 'ui_state', ui)
  monkeypatch.setattr(compact, 'ui_state', ui)
  monkeypatch.setattr(large, 'Params', lambda: ui.params)
  monkeypatch.setattr(button, 'Params', lambda: ui.params)
  monkeypatch.setattr(gui_app, 'font', lambda *_: rl.Font())
  monkeypatch.setattr(gui_app, 'texture', lambda *_: NS(width=64, height=64))
  panel = compact.TogglesLayoutMici() if profile == 'c4' else large.TogglesLayout()
  panel.set_visible(False)
  session._native_favorite_actions = lambda: session.native_favorite_actions(
    lambda: True, panel.request_personality, lambda: True)
  queue = []
  monkeypatch.setattr(runtime.messaging, 'recv_one_or_none', lambda _: queue.pop(0) if queue else None)
  source, publisher = WheelPublisher(ui.params), Publisher()
  source.observe(ui.params, ui.CP, state(), now_ns=NOW - 20_000_000, drive_id=DRIVE)

  for sequence, expected in enumerate((0, 2, 1), start=1):
    clock[0] = NOW + (sequence - 1) * 200_000_000
    ui.sm.frame += 1
    for name in ui.sm:
      ui.sm.logMonoTime[name] = clock[0]
      ui.sm.recv_time[name] = clock[0] / 1e9
      ui.sm.recv_frame[name] = ui.sm.frame
    source.observe(ui.params, ui.CP, state(True), now_ns=clock[0] - 10_000_000, drive_id=DRIVE)
    commands = source.observe(ui.params, ui.CP, state(False), now_ns=clock[0], drive_id=DRIVE)
    assert commands == (('DistanceButtonControl', 1),)
    assert source.suppress_distance_release
    source.publish(commands, ui.CP, publisher, now_ns=clock[0], drive_id=DRIVE,
                   source_car_ns=clock[0], source_control_ns=clock[0])
    wire = messaging.log_from_bytes(publisher.events[-1][1])
    assert wire.slcCruiseEvent.wheelAction.sequence == sequence
    queue.append(wire)
    session._poll_wheel(clock[0])
    deadline = time.monotonic() + 2
    while ui.params.get('LongitudinalPersonality', return_default=True) != expected and time.monotonic() < deadline:
      time.sleep(0.005)
    saved = ui.params.get('LongitudinalPersonality', return_default=True)
    assert saved == expected
    # Mirror Selfdrived.params_thread -> publish_selfdriveState, then run the
    # production UI update. The hidden settings panel never renders or updates.
    ui.sm['selfdriveState'].personality = saved
    module.UIState._update_status(ui)
    assert int(ui.personality) == expected
    assert not panel.is_visible
    session._poll_wheel(clock[0])
    assert ui.params.get('LongitudinalPersonality', return_default=True) == expected


@pytest.mark.parametrize('case', ['fresh', 'stale', 'future', 'invalid', 'dead', 'before_drive', 'offroad', 'not_updated'])
def test_personality_cache_rejects_unusable_selfdrive_state(personality_session, case):
  _, _, ui, module, _ = personality_session
  ui.sm['selfdriveState'].personality = 'aggressive'
  if case == 'stale':
    ui.sm.logMonoTime['selfdriveState'] = NOW - 250_000_001
  elif case == 'future':
    ui.sm.logMonoTime['selfdriveState'] = NOW + 1
  elif case == 'invalid':
    ui.sm.valid['selfdriveState'] = False
  elif case == 'dead':
    ui.sm.alive['selfdriveState'] = False
  elif case == 'before_drive':
    ui.sm.recv_frame['selfdriveState'] = ui.started_frame
  elif case == 'offroad':
    ui.started = ui.engaged = False
  elif case == 'not_updated':
    ui.sm.updated['selfdriveState'] = False
  module.UIState._update_status(ui)
  assert int(ui.personality) == (0 if case == 'fresh' else 1)


@pytest.mark.parametrize('profile', ['c4', 'c3'])
@pytest.mark.parametrize('input_kind', ['wheel', 'tap'])
def test_rapid_personality_cycles_accept_own_notice_and_reject_safety_alert(personality_session, monkeypatch, profile, input_kind):
  import pyray as rl
  from dataclasses import replace
  from openpilot.selfdrive.ui.layouts.settings import common
  from openpilot.selfdrive.ui.layouts.settings import toggles as large
  from openpilot.selfdrive.ui.mici.layouts.settings import toggles as compact
  from openpilot.selfdrive.ui.mici.widgets import button
  from openpilot.starpilot.controllers.tests.test_wheel_actions import state
  from openpilot.starpilot.favorites.actions import CYCLE_PERSONALITY, EXPERIMENTAL
  from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert
  from openpilot.system.ui.lib.application import gui_app

  runtime, session, ui, _, clock = personality_session
  for module in (common, large, compact):
    monkeypatch.setattr(module, 'ui_state', ui)
  monkeypatch.setattr(large, 'Params', lambda: ui.params)
  monkeypatch.setattr(button, 'Params', lambda: ui.params)
  monkeypatch.setattr(gui_app, 'font', lambda *_: rl.Font())
  monkeypatch.setattr(gui_app, 'texture', lambda *_: NS(width=64, height=64))
  panel = compact.TogglesLayoutMici() if profile == 'c4' else large.TogglesLayout()
  panel.set_visible(False)
  session.profile = Profile.COMPACT if profile == 'c4' else Profile.LARGE
  session.selected = Destination.STAR
  del vars(session)['_favorite_authority']  # Exercise the real runtime admission.
  shown = [OnroadState(True, True, 15, 80, SpeedLimitObservation(),
                      alert=OnroadAlert(AlertSize.MID, 'Standard', 'Driving Personality',
                                        alert_type='personalityChanged/warning'))]
  vars(session)['snapshot'] = lambda *_: NS(onroad=shown[0])
  session._native_favorite_actions = lambda: session.native_favorite_actions(
    lambda: True, panel.request_personality, lambda: True)
  touch = OnroadInput(lambda action: session._emit(ShellRequest('onroad', action)), session.profile)
  actions = session._native_favorite_actions()
  assert actions[CYCLE_PERSONALITY].available and actions[CYCLE_PERSONALITY].invoke()
  assert ui.params.get('LongitudinalPersonality') == 2
  assert actions[BOOKMARK].available and not actions[EXPERIMENTAL].available
  assert actions[BOOKMARK].invoke() and not actions[EXPERIMENTAL].invoke()

  queue = []
  monkeypatch.setattr(runtime.messaging, 'recv_one_or_none', lambda _: queue.pop(0) if queue else None)
  source, publisher = WheelPublisher(ui.params), Publisher()
  source.observe(ui.params, ui.CP, state(), now_ns=NOW - 20_000_000, drive_id=DRIVE)
  for sequence, expected in enumerate((1, 0, 2), start=1):
    clock[0] = NOW + sequence * 5_000_000
    for name in ui.sm:
      ui.sm.logMonoTime[name], ui.sm.recv_time[name] = clock[0], clock[0] / 1e9
    if input_kind == 'wheel':
      source.observe(ui.params, ui.CP, state(True), now_ns=clock[0] - 1_000_000, drive_id=DRIVE)
      commands = source.observe(ui.params, ui.CP, state(False), now_ns=clock[0], drive_id=DRIVE)
      assert commands == (('DistanceButtonControl', 1),)
      source.publish(commands, ui.CP, publisher, now_ns=clock[0], drive_id=DRIVE,
                     source_car_ns=clock[0], source_control_ns=clock[0])
      queue.append(messaging.log_from_bytes(publisher.events[-1][1]))
      session._poll_wheel(clock[0])
    else:
      bounds = personality_bounds(session.profile, shown[0])
      assert bounds is not None
      left, top, right, bottom = bounds
      point = (left + right) / 2, (top + bottom) / 2
      before = ui.params.get('LongitudinalPersonality')
      touch.press(*point, shown[0])
      assert ui.params.get('LongitudinalPersonality') == before
      touch.release(*point, shown[0])
      assert session._request_emitted
    assert ui.params.get('LongitudinalPersonality') == expected
    assert ui.personality == 1  # No selfdriveState acknowledgement between presses.

  shown[0] = replace(shown[0], alert=OnroadAlert(AlertSize.FULL, 'Take control', critical=True,
                                               alert_type='controlsMismatch/immediateDisable'))
  assert not actions[CYCLE_PERSONALITY].invoke()
  source.publish((('DistanceButtonControl', 1),), ui.CP, publisher, now_ns=clock[0], drive_id=DRIVE,
                 source_car_ns=clock[0], source_control_ns=clock[0])
  queue.append(messaging.log_from_bytes(publisher.events[-1][1]))
  session._poll_wheel(clock[0])
  assert ui.params.get('LongitudinalPersonality') == 2
  shown[0] = replace(shown[0], alert=OnroadAlert())
  ui.params.put_bool('SafeMode', True, block=True)
  assert not actions[CYCLE_PERSONALITY].invoke()
  ui.params.put_bool('SafeMode', False, block=True)
  path = Path(ui.params.get_param_path('LongitudinalPersonality'))
  path.write_bytes(b'bad')
  assert not actions[CYCLE_PERSONALITY].invoke()
  assert path.read_bytes() == b'bad'


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('blocked', ['safe_mode', 'no_longitudinal', 'no_car', 'replay', 'offroad', 'invalid_saved'])
def test_personality_tap_rechecks_wheel_authority_on_release(native_session, profile, blocked):
  _, session, ui, _, calls = native_session
  session.profile, session.selected = profile, Destination.STAR
  del vars(session)['_favorite_authority']
  ui.params.put('LongitudinalPersonality', 1, block=True)
  state = session.snapshot(ShellMode.ONROAD).onroad
  touch = OnroadInput(lambda action: session._emit(ShellRequest('onroad', action)), profile)
  bounds = personality_bounds(profile, state)
  assert bounds is not None
  left, top, right, bottom = bounds
  point = (left + right) / 2, (top + bottom) / 2
  touch.press(*point, state)
  if blocked == 'safe_mode':
    ui.params.put_bool('SafeMode', True, block=True)
  elif blocked == 'no_longitudinal':
    ui.has_longitudinal_control = False
  elif blocked == 'no_car':
    ui.CP = None
  elif blocked == 'replay':
    ui.replay_clock = object()
  elif blocked == 'offroad':
    session._mode = ShellMode.HOME
  else:
    Path(ui.params.get_param_path('LongitudinalPersonality')).write_bytes(b'bad')
  touch.release(*point, state)
  assert session._request_emitted and calls == []
  path = Path(ui.params.get_param_path('LongitudinalPersonality'))
  assert path.read_bytes() == (b'bad' if blocked == 'invalid_saved' else b'1')
