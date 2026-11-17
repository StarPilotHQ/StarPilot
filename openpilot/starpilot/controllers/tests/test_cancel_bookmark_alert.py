"""A physical Cancel bookmark may capture an alert without changing control gates."""
from collections import deque
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from opendbc.car.gm.tests.test_bolt_pedal import params
from opendbc.car.gm.values import CAR
from openpilot.cereal import messaging
from openpilot.starpilot.controllers.tests.test_native_wheel_callbacks import native_session  # noqa: F401
from openpilot.starpilot.controllers.tests.test_wheel_actions import DRIVE, NOW, Publisher, TYPE, state
from openpilot.starpilot.controllers.wheel_actions import WheelPublisher
from openpilot.starpilot.favorites.actions import BOOKMARK, EXPERIMENTAL
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.shell import ShellMode


def setup(runtime, session, ui, alert):
  ui.CP = params(CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, pedal=True, camera=True)
  ui.CP.alternativeExperience = 64
  ui.params.put('CancelButtonControl', 8, block=True)
  ui.params.put_bool('RemapCancelToDistance', True, block=True)
  current = session.snapshot().onroad
  session.snapshot = lambda *_: NS(onroad=replace(current, alert=alert))
  session.profile = Profile.COMPACT
  session._favorite_authority = runtime.StarShellSession._favorite_authority.__get__(session)
  source = WheelPublisher(ui.params)
  source.observe(ui.params, ui.CP, state(), now_ns=NOW - 200_000_000, drive_id=DRIVE)
  source.observe(ui.params, ui.CP, state(True, button=TYPE.cancel), now_ns=NOW - 150_000_000, drive_id=DRIVE)
  released = state(False, button=TYPE.cancel)
  before = released.to_bytes()
  commands = source.observe(ui.params, ui.CP, released, now_ns=NOW, drive_id=DRIVE)
  assert released.copy().to_bytes() == before
  assert commands == (('CancelButtonControl', 8),)
  sender = Publisher()
  source.publish(commands, ui.CP, sender, now_ns=NOW, drive_id=DRIVE, source_car_ns=NOW, source_control_ns=NOW)
  return messaging.log_from_bytes(sender.events[-1][1])


@pytest.mark.parametrize('size', [AlertSize.SMALL, AlertSize.MID, AlertSize.FULL])
def test_cancel_bookmark_reaches_native_callback_during_alert_once(native_session, monkeypatch, size):  # noqa: F811
  runtime, session, ui, _, calls = native_session
  event = setup(runtime, session, ui, OnroadAlert(size=size, text1='Big Model Failed', alert_type='bigModelFailed/permanent'))
  assert not session._favorite_authority()
  actions = session._native_favorite_actions()
  assert actions[BOOKMARK].available
  assert not actions[EXPERIMENTAL].available
  queue = deque((event, event))
  monkeypatch.setattr(runtime.messaging, 'recv_one_or_none', lambda _: queue.popleft() if queue else None)
  session._poll_wheel(NOW)
  assert calls == ['bookmark']


@pytest.mark.parametrize('reason', ['offroad', 'stale', 'invalid_can', 'timeout', 'new_drive', 'replay'])
def test_bookmark_still_requires_current_onroad_source(native_session, monkeypatch, reason):  # noqa: F811
  runtime, session, ui, _, calls = native_session
  event = setup(runtime, session, ui, OnroadAlert(size=AlertSize.MID))
  if reason == 'offroad':
    ui.started = False
    session._mode = ShellMode.HOME
  elif reason == 'stale':
    ui.sm.logMonoTime['carState'] = NOW - 300_000_000
  elif reason == 'invalid_can':
    ui.sm['carState'].canValid = False
  elif reason == 'timeout':
    ui.sm['carState'].canTimeout = True
  elif reason == 'new_drive':
    ui.sm['deviceState'].startedMonoTime = DRIVE + 1
  else:
    ui.replay_clock = object()
  queue = deque((event,))
  monkeypatch.setattr(runtime.messaging, 'recv_one_or_none', lambda _: queue.popleft() if queue else None)
  session._poll_wheel(NOW)
  assert not calls
