from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.ford.interface import CarInterface as FordInterface
from opendbc.car.ford.values import CAR as FORD
from opendbc.car.honda.interface import CarInterface as HondaInterface
from opendbc.car.honda.values import CAR as HONDA
from openpilot.cereal import messaging
from openpilot.starpilot.controllers.tests.test_native_wheel_callbacks import (
  native_session as native_session,
  personality_session as personality_session,
)
from openpilot.starpilot.controllers.tests.test_wheel_actions import Publisher, DRIVE
from openpilot.starpilot.controllers.wheel_actions import WheelPublisher
from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.shell import ShellMode


@pytest.fixture
def sleeping_wheel(personality_session, monkeypatch):
  from openpilot.selfdrive.ui import ui as caller
  from openpilot.selfdrive.ui.layouts.settings import common, toggles as large
  from openpilot.selfdrive.ui.mici.layouts.settings import toggles as compact
  from openpilot.selfdrive.ui.mici.widgets import button
  from openpilot.starpilot.ui.raylib import rl
  from openpilot.system.ui.lib.application import gui_app

  runtime, session, ui, _, clock = personality_session
  for module in (caller, common, large, compact):
    monkeypatch.setattr(module, 'ui_state', ui)
  monkeypatch.setattr(ui, 'update', lambda: None, raising=False)
  monkeypatch.setattr(large, 'Params', lambda: ui.params)
  monkeypatch.setattr(button, 'Params', lambda: ui.params)
  monkeypatch.setattr(gui_app, 'font', lambda *_: rl.Font())
  monkeypatch.setattr(gui_app, 'texture', lambda *_: NS(width=64, height=64))
  monkeypatch.setattr(gui_app, 'measure_frame_phase', lambda _name, callback: callback())
  device = NS(awake=False)
  monkeypatch.setattr(runtime, 'native_device', device)
  monkeypatch.setattr(caller, 'device', device, raising=False)
  session._mode = ShellMode.HOME  # No onroad frame need have rendered before sleep.
  session.profile = Profile.COMPACT
  del vars(session)['_favorite_authority']
  shown = [session.snapshot(ShellMode.ONROAD).onroad]
  session.snapshot = lambda *_: NS(onroad=shown[0])
  session._poll_projection_bookmark = session._poll_projection_slc = lambda _now: None
  queue = []
  monkeypatch.setattr(runtime.messaging, 'recv_one_or_none', lambda _: queue.pop(0) if queue else None)
  source, publisher = WheelPublisher(ui.params), Publisher()

  def emit(*, age=0, action=1):
    for name in ui.sm:
      ui.sm.logMonoTime[name] = clock[0]
      ui.sm.recv_time[name] = clock[0] / 1e9
    source.publish((('DistanceButtonControl', action),), ui.CP, publisher,
                   now_ns=clock[0] - age, drive_id=DRIVE,
                   source_car_ns=clock[0] - age, source_control_ns=clock[0] - age)
    event = messaging.log_from_bytes(publisher.events[-1][1])
    queue.append(event)
    return event

  def panel(profile):
    session.profile = Profile.COMPACT if profile == 'c4' else Profile.LARGE
    owner = compact.TogglesLayoutMici() if profile == 'c4' else large.TogglesLayout()
    owner.set_visible(False)
    session._native_favorite_actions = lambda: session.native_favorite_actions(lambda: True, owner.request_personality, lambda: True)
    return owner

  return NS(session=session, ui=ui, clock=clock, device=device, shown=shown, queue=queue, emit=emit,
            panel=panel, poll=lambda: caller.update_frame(star=session))


@pytest.mark.parametrize('profile', ['c3', 'c4'])
@pytest.mark.parametrize('vehicle', ['civic', 'ranger'])
@pytest.mark.parametrize('last_mode', [ShellMode.HOME, ShellMode.SETTINGS, ShellMode.ONROAD])
def test_sleeping_display_cycles_native_personality_without_rendering(sleeping_wheel, profile, vehicle, last_mode):
  test = sleeping_wheel
  fingerprint = gen_empty_fingerprint()
  if vehicle == 'civic':
    fingerprint[0].update({0x1a3: 8, 0x184: 8})
    cp = HondaInterface.get_params(HONDA.HONDA_CIVIC_2022, fingerprint, [], True, True, False)
  else:
    fingerprint[0][0x5a] = 8
    fingerprint[2].update({0x3d6: 8, 0x186: 8})
    cp = FordInterface.get_params(FORD.FORD_RANGER_MK2, fingerprint, [], True, True, False)
  test.ui.CP = cp
  owner = test.panel(profile)
  test.session._mode = last_mode
  for expected in (0, 2, 1):
    test.emit()
    test.poll()  # Actual before-frame entry point; no render call or authority stub.
    assert test.ui.params.get('LongitudinalPersonality') == expected
    assert not owner.is_visible
    assert test.session._mode == last_mode
    assert not test.session._screen_off_wheel
    test.clock[0] += 100_000_000
  if last_mode != ShellMode.ONROAD:
    assert not test.session._favorite_authority()  # No leaked touch/controller authority.


@pytest.mark.parametrize('blocked', [
  'expired', 'future', 'duplicate', 'mapping_changed', 'cp_changed', 'safe_mode', 'no_longitudinal', 'no_car',
  'replay', 'offroad', 'invalid_can', 'can_timeout', 'stale_car', 'stale_control', 'stale_device',
  'critical_alert', 'reverse_camera', 'invalid_personality',
])
def test_sleeping_wheel_keeps_source_and_action_guards(sleeping_wheel, blocked):
  test = sleeping_wheel
  test.panel('c4')
  event = test.emit(age=250_000_001 if blocked == 'expired' else -1 if blocked == 'future' else 0)
  expected = 1
  if blocked == 'duplicate':
    test.poll()
    expected = 0
    test.queue.append(event)
  elif blocked == 'mapping_changed':
    test.ui.params.put('DistanceButtonControl', 0, block=True)
  elif blocked == 'cp_changed':
    test.ui.CP = test.ui.CP.as_builder() if hasattr(test.ui.CP, 'as_builder') else test.ui.CP
    test.ui.CP.flags ^= 1
  elif blocked == 'safe_mode':
    test.ui.params.put_bool('SafeMode', True, block=True)
  elif blocked == 'no_longitudinal':
    test.ui.has_longitudinal_control = False
  elif blocked == 'no_car':
    test.ui.CP = None
  elif blocked == 'replay':
    test.ui.replay_clock = object()
  elif blocked == 'offroad':
    test.ui.started = False
    test.ui.sm['deviceState'].started = False
  elif blocked == 'invalid_can':
    test.ui.sm['carState'].canValid = False
  elif blocked == 'can_timeout':
    test.ui.sm['carState'].canTimeout = True
  elif blocked.startswith('stale_'):
    service = {'stale_car': 'carState', 'stale_control': 'carControl', 'stale_device': 'deviceState'}[blocked]
    test.ui.sm.logMonoTime[service] -= 1_000_000_001
  elif blocked == 'critical_alert':
    test.shown[0] = replace(test.shown[0], alert=OnroadAlert(AlertSize.FULL, 'Take control', critical=True))
  elif blocked == 'reverse_camera':
    test.shown[0] = replace(test.shown[0], reversing=True,
                           appearance=replace(test.shown[0].appearance, camera_view=CameraViewChoice.STANDARD))
  elif blocked == 'invalid_personality':
    Path(test.ui.params.get_param_path('LongitudinalPersonality')).write_bytes(b'bad')
  test.poll()
  assert not test.session._screen_off_wheel
  if blocked == 'invalid_personality':
    assert Path(test.ui.params.get_param_path('LongitudinalPersonality')).read_bytes() == b'bad'
  else:
    assert test.ui.params.get('LongitudinalPersonality') == expected


def test_awake_display_keeps_rendered_admission_and_does_not_replay_on_wake(sleeping_wheel):
  test = sleeping_wheel
  test.panel('c4')
  event = test.emit()
  test.poll()
  assert test.ui.params.get('LongitudinalPersonality') == 0
  test.device.awake = True
  test.queue.append(event)
  test.poll()
  assert len(test.queue) == 1  # The awake render path still owns polling.
  test.session._mode = ShellMode.ONROAD
  test.session._poll_wheel(test.clock[0])
  assert test.ui.params.get('LongitudinalPersonality') == 0


@pytest.mark.parametrize('service', ['carState', 'carControl'])
@pytest.mark.parametrize('expires', [False, True])
def test_sleeping_wheel_waits_for_sources_without_extending_deadline(sleeping_wheel, service, expires):
  test = sleeping_wheel
  test.panel('c4')
  test.emit()
  test.ui.sm.logMonoTime[service] -= 10_000_000
  test.poll()
  assert test.ui.params.get('LongitudinalPersonality') == 1
  assert len(test.session._wheel_pending) == 1
  test.clock[0] += 250_000_001 if expires else 10_000_000
  for name in test.ui.sm:
    test.ui.sm.logMonoTime[name] = test.clock[0]
    test.ui.sm.recv_time[name] = test.clock[0] / 1e9
  test.poll()
  assert test.ui.params.get('LongitudinalPersonality') == (1 if expires else 0)
  assert not test.session._wheel_pending


@pytest.mark.parametrize('onroad', [False, True])
def test_sleeping_wheel_bookmark_uses_live_drive_not_last_render(sleeping_wheel, onroad):
  test = sleeping_wheel
  calls = []
  test.session._native_favorite_actions = lambda: test.session.native_favorite_actions(
    lambda: calls.append('bookmark') or True, lambda _index: False, lambda: False)
  test.ui.params.put('DistanceButtonControl', 8, block=True)
  test.emit(action=8)
  test.ui.started = test.ui.sm['deviceState'].started = onroad
  test.poll()
  assert calls == (['bookmark'] if onroad else [])
  assert test.session._mode == ShellMode.HOME
  assert not test.session._bookmark_authority()


def test_sleeping_wheel_context_clears_when_dispatch_raises(sleeping_wheel, monkeypatch):
  test = sleeping_wheel
  def failed(_now):
    assert test.session._screen_off_wheel
    raise RuntimeError('dispatch interrupted')
  monkeypatch.setattr(test.session, '_poll_wheel', failed)
  with pytest.raises(RuntimeError, match='dispatch interrupted'):
    test.poll()
  assert not test.session._screen_off_wheel
  assert not test.session._favorite_authority()
