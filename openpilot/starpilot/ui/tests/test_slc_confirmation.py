"""Attached confirmation: live decisions, physical targets and safe saved layouts."""

from copy import deepcopy
from dataclasses import replace

import pytest

from openpilot.starpilot.ui.large_speed_geometry import CONFIRMATION_SCALE
from openpilot.starpilot.ui.onroad_customization import default_document, validate_document
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert, OnroadInput, manual_slc_confirmation, slc_controls, speed_limit_bounds
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.tests.test_slc_ui_actions import state, tap_sign, tap_control
from openpilot.starpilot.ui.onroad_state import SlcActionKind
from openpilot.starpilot.ui.unified_speed_presentation import resolve_unified_speed


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('source_available', [False, True])
@pytest.mark.parametrize('action', [SlcActionKind.ACCEPT, SlcActionKind.REJECT])
def test_source_gap_keeps_same_open_card_with_accept_and_reject(profile, source_available, action):
  from openpilot.starpilot.ui.onroad_state import ObservationKind
  current, emitted = state(), []
  touch = OnroadInput(emitted.append, profile)
  tap_sign(touch, current)
  before = speed_limit_bounds(profile, touch.present(current))
  missing = replace(current, speed_limit=replace(current.speed_limit,
    kind=ObservationKind.VALID if source_available else ObservationKind.UNKNOWN,
    speed_limit_mps=22. if source_available else None,
    last_received_speed_limit_mps=22., last_received_source='dashboard'))
  assert resolve_unified_speed(missing, confirmation=True).status == 'Confirm speed limit'
  assert manual_slc_confirmation(profile, missing)
  assert touch.present(missing).slc_prompt_open
  assert speed_limit_bounds(profile, touch.present(missing)) == before
  accept, reject = slc_controls(profile, touch.present(missing))
  assert accept.enabled and reject.enabled
  recovered = current
  assert touch.present(recovered).slc_prompt_open
  touch.present(missing)
  tap_control(touch, missing, action)
  assert [r.kind for r in emitted] == [action]


@pytest.mark.parametrize('profile', list(Profile))
def test_closed_and_open_card_share_anchor_and_physically_scaled_targets(profile):
  current, emitted = state(), []
  touch = OnroadInput(emitted.append, profile)
  before = speed_limit_bounds(profile, current)
  tap_sign(touch, current)
  assert not emitted
  opened = touch.present(current)
  after = speed_limit_bounds(profile, opened)
  assert before is not None and after is not None
  assert before[1] == after[1]
  assert before[3] - after[3] == pytest.approx(7 if profile == Profile.COMPACT else 0)
  assert after[0] <= before[0] < before[2] <= after[2]
  first, last = slc_controls(profile, opened)
  scale = CONFIRMATION_SCALE[profile]
  assert (first.bounds[2] - first.bounds[0]) / scale == pytest.approx(180 if profile == Profile.LARGE else 150)
  assert (first.bounds[3] - first.bounds[1]) / scale == pytest.approx(128 if profile == Profile.LARGE else 112)
  assert (last.bounds[0] - first.bounds[2]) / scale == pytest.approx(12)
  assert (first.bounds[0] - after[0]) / scale == pytest.approx(8)
  assert (after[2] - last.bounds[2]) / scale == pytest.approx(8)
  torque = current.customization['layouts'][str(profile)]['torque_bar']
  assert max(before[3], after[3]) <= torque['y']
  if profile == Profile.COMPACT:
    assert after == (148., 8, 476., 160.)
    assert after[0] > 146


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('wheel_confirmation', [False, True])
def test_notice_footer_opens_choices_without_accepting_or_rejecting(profile, wheel_confirmation):
  current = replace(state(), slc_wheel_confirmation_available=wheel_confirmation)
  emitted = []
  touch = OnroadInput(emitted.append, profile)
  bounds = speed_limit_bounds(profile, current)
  assert bounds is not None
  left, _, right, bottom = bounds
  x, y = (left + right) / 2, bottom - 2 * CONFIRMATION_SCALE[profile]
  touch.press(x, y, current)
  touch.release(x, y, current)
  assert touch.present(current).slc_prompt_open
  assert len(slc_controls(profile, touch.present(current))) == 2
  assert not emitted


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('lateral', [False, True])
def test_disengaged_and_aol_views_cancel_confirmation_but_keep_the_sign(profile, lateral):
  current, emitted = state(), []
  touch = OnroadInput(emitted.append, profile)
  tap_sign(touch, current)
  assert touch.present(current).slc_prompt_open
  inactive = replace(current, longitudinal_active=False, lateral_active=lateral)
  assert not manual_slc_confirmation(profile, inactive)
  assert not touch.present(inactive).slc_prompt_open
  assert not slc_controls(profile, inactive)
  assert speed_limit_bounds(profile, inactive) is not None
  assert not emitted


@pytest.mark.parametrize('profile', list(Profile))
def test_accelerator_override_keeps_confirmation_visible(profile):
  current = replace(state(), longitudinal_active=False, longitudinal_overridden=True)
  assert manual_slc_confirmation(profile, current)


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('action', [SlcActionKind.ACCEPT, SlcActionKind.REJECT])
def test_touch_choice_remains_available_without_wheel_confirmation(profile, action):
  current = replace(state(), slc_wheel_confirmation_available=False)
  emitted = []
  touch = OnroadInput(emitted.append, profile)
  tap_sign(touch, current)
  tap_control(touch, current, action)
  assert [request.kind for request in emitted] == [action]


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('change', ['actions_hidden', 'sign_hidden', 'automatic', 'alert', 'stale'])
def test_unavailable_confirmation_has_no_empty_chooser_or_action(profile, change):
  from openpilot.starpilot.ui.onroad_state import ObservationKind
  current, emitted = state(), []
  touch = OnroadInput(emitted.append, profile)
  tap_sign(touch, current)
  if change in ('actions_hidden', 'sign_hidden'):
    doc = deepcopy(current.customization)
    key = 'speed_limit_actions' if change == 'actions_hidden' else 'cruise_limits' if profile == Profile.LARGE else 'speed_limit'
    doc['layouts'][str(profile)][key]['enabled'] = False
    current = replace(current, customization=doc)
  elif change == 'automatic':
    current = replace(current, speed_limit=replace(current.speed_limit, automatic_adoption_pending=True))
  elif change == 'alert':
    current = replace(current, alert=OnroadAlert(size=AlertSize.SMALL))
  else:
    current = replace(current, speed_limit=replace(current.speed_limit, kind=ObservationKind.STALE, speed_limit_mps=None,
      pending_speed_limit_mps=None, presentation_id=None))
  assert not manual_slc_confirmation(profile, current)
  assert not touch.present(current).slc_prompt_open
  assert not slc_controls(profile, current)
  assert not emitted


@pytest.mark.parametrize('profile', list(Profile))
def test_new_layouts_reject_torque_overlap_and_legacy_migration_is_targeted(profile):
  doc = default_document()
  owner = 'cruise_limits' if profile == Profile.LARGE else 'speed_limit'
  doc['layouts'][str(profile)][owner]['y'] = 700 if profile == Profile.LARGE else 50
  with pytest.raises(ValueError, match='confirmation'):
    validate_document(doc)
  doc['version'] = 11
  doc['layouts']['large']['current_speed']['x'] = 800
  doc['palette']['text'] = '#ABCDEFEE'
  original = deepcopy(doc)
  migrated = validate_document(doc)
  assert doc == original
  assert migrated['version'] == 14
  assert migrated['palette'] == doc['palette']
  assert migrated['layouts']['large']['current_speed'] == doc['layouts']['large']['current_speed']
  assert migrated['layouts'][str(profile)][owner]['y'] == default_document()['layouts'][str(profile)][owner]['y']
  assert validate_document(migrated) == migrated


def test_legacy_sources_keep_the_saved_action_area_below_the_row():
  doc = default_document()
  doc.update(version=11, speedSources=True, speedSourcesCard=True)
  doc['layouts']['large']['speed_limit_actions']['y'] = 351
  migrated = validate_document(doc)
  assert migrated['layouts']['large']['speed_limit_actions']['y'] == 389
  assert validate_document(migrated) == migrated


def test_large_confirmation_clears_the_taller_torque_arc_when_sidebar_is_hidden():
  doc = default_document()
  doc['layouts']['large']['cruise_limits']['y'] = 550
  with pytest.raises(ValueError, match='confirmation'):
    validate_document(doc)
  doc['layouts']['large']['speed_limit_actions']['enabled'] = False
  assert validate_document(doc) == doc


def test_large_sources_cannot_be_clipped_below_a_custom_confirmation():
  doc = default_document()
  doc['speedSources'] = True
  doc['layouts']['large']['cruise_limits'].update(x=1520, y=790)
  doc['layouts']['large']['speed_limit_actions'].update(x=250, y=400)
  with pytest.raises(ValueError, match='confirmation'):
    validate_document(doc)
  doc['version'] = 11
  migrated = validate_document(doc)
  assert validate_document(migrated) == migrated
  assert migrated['layouts']['large']['speed_limit_actions'] == doc['layouts']['large']['speed_limit_actions']


@pytest.mark.parametrize('reverse', [False, True])
def test_compact_driver_camera_has_no_hidden_confirmation_targets(reverse):
  from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
  current = state()
  current = replace(current, reverse_driver_camera=reverse,
                    appearance=replace(current.appearance, camera_view=CameraViewChoice.AUTO if reverse else CameraViewChoice.DRIVER))
  assert not manual_slc_confirmation(Profile.COMPACT, current)
  assert not slc_controls(Profile.COMPACT, replace(current, slc_prompt_open=True))
  def unexpected_action(_request):
    raise AssertionError('Hidden confirmation emitted an action')
  touch = OnroadInput(unexpected_action, Profile.COMPACT)
  touch.press(250, 90, current)
  assert not touch.claimed
