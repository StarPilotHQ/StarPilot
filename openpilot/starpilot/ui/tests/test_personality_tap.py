from dataclasses import replace
from types import SimpleNamespace as NS
from typing import cast
from unittest.mock import Mock

import pytest

from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
from openpilot.starpilot.ui.onroad_customization import widget_size
from openpilot.starpilot.ui.onroad_personality import PersonalityWidget
from openpilot.starpilot.ui.onroad_state import (
  AlertSize, ObservationKind, OnroadAlert, OnroadInput, OnroadRequest, OnroadState, SlcUiRequest, SpeedLimitObservation,
  personality_bounds, slc_controls, speed_limit_bounds,
)
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile
from openpilot.starpilot.ui.runtime_app import StarShellSession
from openpilot.starpilot.ui.settings_state import Destination
from openpilot.starpilot.ui.shell import ShellInput, ShellMode


@pytest.fixture(params=list(Profile))
def gesture(request):
  requests = []
  profile = request.param
  state = OnroadState(True, True, 15, 80, SpeedLimitObservation(), longitudinal_active=True, drive_frame=1)
  return profile, OnroadInput(requests.append, profile), requests, state


def center(bounds):
  left, top, right, bottom = bounds
  return (left + right) / 2, (top + bottom) / 2


def test_tap_emits_once_on_release_and_never_changes_the_supplied_state(gesture):
  profile, touch, requests, state = gesture
  point = center(personality_bounds(profile, state))
  touch.press(*point, state)
  assert touch.claimed and not requests
  touch.release(*point, state)
  touch.release(*point, state)
  assert requests == [OnroadRequest('cycle_personality')]
  assert not touch.claimed and state.personality == 1


@pytest.mark.parametrize('moved', (False, True))
@pytest.mark.parametrize('rhd', (False, True))
def test_drawn_bounds_and_tap_target_agree_for_both_layouts(gesture, moved, rhd):
  profile, touch, requests, state = gesture
  if moved:
    state.customization['layouts'][profile]['following_distance'].update(x=260, y=20)
  state = replace(state, right_hand_drive=rhd)
  bounds = personality_bounds(profile, state)
  assert bounds is not None
  if profile == Profile.LARGE and rhd and not moved:
    assert bounds[0] == 1280
  widget = PersonalityWidget(cast(BitmapFonts, NS(profile=profile)))
  widget.draw = Mock()
  widget.render(state)
  rect = widget.draw.call_args.args[0]
  assert (rect.x, rect.y, rect.x + rect.width, rect.y + rect.height) == bounds
  touch.press(*center(bounds), state)
  touch.release(*center(bounds), state)
  assert requests == [OnroadRequest('cycle_personality')]


@pytest.mark.parametrize('change', ('drag', 'exit', 'disabled', 'layout', 'drive', 'camera', 'driver', 'reverse_driver', 'alert', 'menu', 'cancel'))
def test_interrupted_gesture_cannot_activate_or_reactivate(gesture, change):
  profile, touch, requests, state = gesture
  bounds = personality_bounds(profile, state)
  assert bounds is not None
  x, y = bounds[0] + 1, (bounds[1] + bounds[3]) / 2
  touch.press(x, y, state)
  if change == 'drag':
    touch.move(x + 6, y, state)
  elif change == 'exit':
    touch.move(x - 2, y, state)
  elif change in ('disabled', 'layout'):
    state.customization['layouts'][profile]['following_distance'].update({'enabled': False} if change == 'disabled' else {'x': bounds[0] + 1})
  elif change == 'drive':
    state = replace(state, drive_frame=2)
  elif change == 'camera':
    state = replace(state, camera_available=False)
  elif change == 'driver':
    state = replace(state, appearance=replace(state.appearance, camera_view=CameraViewChoice.DRIVER))
  elif change == 'reverse_driver':
    state = replace(state, reverse_driver_camera=True)
  elif change == 'alert':
    state = replace(state, alert=OnroadAlert(AlertSize.FULL, 'Take control', critical=True))
  elif change == 'menu':
    state = replace(state, quick_select_open=True)
  else:
    touch.cancel()
  touch.release(x, y, state)
  assert not requests and not touch.claimed


def test_personality_notice_and_incoming_acknowledgement_do_not_block_taps(gesture):
  profile, touch, requests, state = gesture
  state = replace(state, alert=OnroadAlert(AlertSize.MID, 'Standard', 'Driving Personality', alert_type='personalityChanged/warning'))
  point = center(personality_bounds(profile, state))
  for _ in range(3):
    touch.press(*point, state)
    touch.release(*point, replace(state, personality=2))
  assert requests == [OnroadRequest('cycle_personality')] * 3


def test_critical_alert_cannot_impersonate_a_personality_notice(gesture):
  profile, touch, requests, state = gesture
  point = center(personality_bounds(profile, state))
  state = replace(state, alert=OnroadAlert(AlertSize.MID, critical=True, alert_type='personalityChanged/warning'))
  touch.press(*point, state)
  touch.release(*point, state)
  assert not requests and not touch.claimed


def test_speed_limit_controls_keep_priority_over_an_overlapping_widget(gesture):
  profile, touch, requests, state = gesture
  state = replace(state, speed_limit=SpeedLimitObservation(
    kind=ObservationKind.VALID, speed_limit_mps=20, pending_speed_limit_mps=22, action_enabled=True,
    session_id='drive', decision_id=1, presentation_id=1))
  point = center(speed_limit_bounds(profile, state))
  touch.press(*point, state)
  touch.release(*point, state)
  state = touch.present(state)
  control = slc_controls(profile, state)[0]
  x, y = center(control.bounds)
  width, height = widget_size(state.customization, profile, 'following_distance')
  state.customization['layouts'][profile]['following_distance'].update(x=x - width / 2, y=y - height / 2)
  touch.press(x, y, state)
  touch.release(x, y, state)
  assert requests == [control.request] and isinstance(requests[0], SlcUiRequest)


@pytest.mark.parametrize('overlay', (False, True))
def test_session_consumes_widget_tap_without_home_or_favorite_fallthrough(gesture, overlay):
  profile, _, requests, state = gesture
  state.customization['layouts'][profile]['following_distance'].update(x=260, y=20)
  session = StarShellSession.__new__(StarShellSession)
  session.profile = profile
  vars(session)['favorites'] = NS(is_open=overlay, claims_corner=lambda *_: False, press=Mock(return_value=overlay), release=Mock())
  vars(session)['snapshot'] = vars(session)['_input_snapshot'] = lambda *_: NS(mode=ShellMode.ONROAD, selected=Destination.STAR, onroad=state)
  session._update_favorites = Mock()
  session.input = ShellInput(profile, requests.append)
  vars(session)['view'] = NS(onroad=NS(navigation=NS(press=Mock(return_value=False))))
  point = center(personality_bounds(profile, state))
  session.press(ShellMode.ONROAD, *point)
  assert session.release(ShellMode.ONROAD, *point)
  if overlay:
    assert not requests
    session.favorites.release.assert_called_once()
  else:
    assert len(requests) == 1 and requests[0].action == OnroadRequest('cycle_personality')
    session.favorites.press.assert_not_called()
    session.view.onroad.navigation.press.assert_not_called()
