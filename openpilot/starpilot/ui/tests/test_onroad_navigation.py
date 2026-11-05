from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.cereal import messaging
from openpilot.starpilot.ui.navigation_state import NavigationDisplay, distance_text, navigation_display
from openpilot.starpilot.ui.onroad_navigation import ASSETS, MANIFEST, NavigationCard, arrival_time, icon_name
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert, OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.presentation import FontRole, Profile

NOW = 10_000_000_000
DRIVE = 1_000_000_000


def message():
  result = messaging.new_message('starpilotNavigation').starpilotNavigation.navigation
  result.version = 2
  result.enabled = True
  result.status = 'guiding'
  result.startedMonoTime = DRIVE
  result.frameMonoTime = NOW
  result.locationMonoTime = NOW
  result.sessionId = 'session'
  result.revision = 'rev4'
  result.instruction = {'text': 'Turn left onto Main Street', 'maneuverType': 'turn', 'maneuverModifier': 'left',
                        'distanceMeters': 120, 'remainingDistanceMeters': 4300, 'remainingDurationSeconds': 510}
  return result


def state():
  return OnroadState(False, False, 10., 50., SpeedLimitObservation(), navigation=navigation_display(message(), now_ns=NOW, drive_id=DRIVE))


def test_large_navigation_optional_widget_controls_draw_and_touch_bounds():
  card, observed = NavigationCard(Mock(profile=Profile.LARGE)), replace(state(), viewport_width=1860)
  placed = observed.customization['layouts']['large']['nav_card']
  placed.update(x=900, y=650)
  bounds = card.bounds(observed)
  assert (bounds.x, bounds.y, bounds.width, bounds.height) == (900, 650, 560, 195)
  assert card.bounds(replace(observed, viewport_width=2160)).x == 1200
  assert card.press(920, 670, observed)
  placed['enabled'] = False
  assert card.bounds(observed) is None
  assert not card.press(920, 670, observed)


def test_actual_instruction_does_not_require_control_authority():
  nav = message()
  nav.controlValid = False
  observed = navigation_display(nav, now_ns=NOW, drive_id=DRIVE)
  assert observed == NavigationDisplay(('session', DRIVE), 'Turn left onto Main Street', 'turn', 'left', 120., 4300., 510.)


@pytest.mark.parametrize('field,value', [('enabled', False), ('status', 'routing'), ('startedMonoTime', 2),
                                        ('frameMonoTime', NOW + 1), ('frameMonoTime', NOW - 3_000_000_001),
                                        ('locationMonoTime', NOW - 3_000_000_001), ('sessionId', '')])
def test_invalid_or_stale_navigation_does_not_render(field, value):
  nav = message()
  setattr(nav, field, value)
  assert navigation_display(nav, now_ns=NOW, drive_id=DRIVE) is None


def test_bad_distance_and_empty_instruction_do_not_render():
  nav = message()
  nav.instruction.distanceMeters = float('nan')
  assert navigation_display(nav, now_ns=NOW, drive_id=DRIVE) is None
  nav.instruction.distanceMeters = 0
  nav.instruction.text = ' '
  assert navigation_display(nav, now_ns=NOW, drive_id=DRIVE) is None
  nav.status = 'arrived'
  assert navigation_display(nav, now_ns=NOW, drive_id=DRIVE).text == 'You have arrived'


@pytest.mark.parametrize('meters,metric,expected', [(120, False, '400 ft'), (120, True, '125 m'),
                                                   (1609.344, False, '1.0 mi'), (2000, True, '2.0 km')])
def test_distance_units(meters, metric, expected):
  assert distance_text(meters, metric) == expected


def test_arrival_time_uses_remaining_route_duration():
  assert arrival_time(18 * 60, datetime(2026, 10, 7, 15, 42)) == '4:00 PM'


@pytest.mark.parametrize('profile', [Profile.COMPACT, Profile.LARGE])
def test_tap_collapses_and_swipe_never_toggles(profile):
  card, observed = NavigationCard(Mock(profile=profile)), state()
  original = card.bounds(observed)
  assert card.press(original.x + 20, original.y + 20, observed)
  card.release(original.x + 20, original.y + 20, observed)
  assert card.collapsed
  chip = card.bounds(observed)
  assert chip.width < original.width
  assert card.press(chip.x + 20, chip.y + 20, observed)
  card.move(chip.x + 30, chip.y + 20, observed)
  card.release(chip.x + 20, chip.y + 20, observed)
  assert card.collapsed
  # New runtime sessions start expanded; the prior tap cannot act on a restarted owner.
  assert card.press(chip.x + 20, chip.y + 20, observed)
  next_route = replace(observed, navigation=replace(observed.navigation, key=('new-session', DRIVE)))
  card.release(chip.x + 20, chip.y + 20, next_route)
  assert not card.collapsed


@pytest.mark.parametrize('size', [AlertSize.SMALL, AlertSize.MID, AlertSize.FULL])
def test_native_alert_owns_space(size):
  card = NavigationCard(Mock(profile=Profile.COMPACT))
  assert card.bounds(replace(state(), alert=OnroadAlert(size, 'warning'))) is None
  assert not card.press(120, 40, replace(state(), alert=OnroadAlert(size, 'warning')))


def test_slc_decision_keeps_its_touch_priority_only_where_it_overlaps():
  from openpilot.starpilot.ui.onroad_state import ObservationKind
  card = NavigationCard(Mock(profile=Profile.COMPACT))
  pending = SpeedLimitObservation(kind=ObservationKind.VALID, source='map', speed_limit_mps=22., pending_speed_limit_mps=22.,
                                  session_id='s', decision_id=1, presentation_id=1, action_enabled=True)
  observed = replace(state(), slc_prompt_open=True, longitudinal_active=True, speed_limit=pending)
  observed.customization['layouts']['compact']['speed_limit_actions']['y'] = 180
  assert card.bounds(observed) is None  # The chooser now follows the sign, not this old action placement.
  observed.customization['layouts']['compact']['speed_limit_actions']['y'] = 4
  observed.customization['layouts']['compact']['max_speed']['y'] = 58
  assert card.bounds(observed) is None
  assert card.bounds(replace(observed, longitudinal_active=False)) is not None  # No confirmation chooser while LONG is disengaged.
  # SLC being on, with no decision on screen, leaves the directions up.
  assert card.bounds(replace(state(), longitudinal_active=True, speed_limit=SpeedLimitObservation(action_enabled=True))) is not None
  large = NavigationCard(Mock(profile=Profile.LARGE))
  assert large.bounds(replace(state(), longitudinal_active=True, speed_limit=pending)) is not None


@pytest.mark.parametrize('profile', [Profile.COMPACT, Profile.LARGE])
def test_renderer_keeps_actual_instruction_and_distance(profile):
  fonts = Mock(profile=profile)
  fonts.measure.side_effect = lambda text, role, size: NS(width=len(text) * size * .48)
  card = NavigationCard(fonts, clock=lambda: datetime(2026, 10, 7, 15, 42))
  with patch.object(card, '_icon', return_value=NS(width=100, height=100)), \
       patch.object(rl, 'draw_rectangle_rounded'), patch.object(rl, 'draw_rectangle_rounded_lines_ex'), patch.object(rl, 'draw_texture_pro'), \
       patch.object(rl, 'draw_line_ex'), patch.object(rl, 'draw_rectangle_rec'):
    card.render(state())
  text = ' '.join(call.args[0] for call in fonts.draw.call_args_list)
  assert 'Turn left onto Main Street' in text
  assert '400 ft' in text
  assert 'ETA 3:50 PM' in text


def test_original_maneuver_assets_and_fallback_are_bounded():
  import hashlib
  assert icon_name('turn', 'slightRight') == 'direction_turn_slight_right.png'
  assert icon_name('roundabout', 'left') == 'direction_roundabout_left.png'
  assert icon_name('unknown/../../', 'anything') == 'direction_turn_straight.png'
  for name, row in MANIFEST.items():
    data = (ASSETS / name).read_bytes()
    assert len(data) == row['bytes']
    assert hashlib.sha256(data).hexdigest() == row['sha256']


def test_native_card_tap_is_consumed_before_favorites_or_background(monkeypatch):
  from openpilot.starpilot.ui.runtime_app import StarShellSession
  from openpilot.starpilot.ui.shell import ShellMode
  card, observed = NavigationCard(Mock(profile=Profile.COMPACT)), state()
  session = StarShellSession.__new__(StarShellSession)
  session.snapshot = Mock(return_value=NS(onroad=observed))
  session._input_snapshot = session.snapshot
  session._update_favorites = Mock()
  session.input = Mock(onroad=NS(claimed=False))
  session.favorites = Mock(is_open=False, claims_corner=Mock(return_value=False))
  monkeypatch.setattr(session, 'view', NS(onroad=NS(navigation=card)), raising=False)
  session.press(ShellMode.ONROAD, 120, 40)
  session.favorites.press.assert_not_called()
  assert session.release(ShellMode.ONROAD, 120, 40)
  assert card.collapsed
  session.input.release.assert_not_called()


def test_existing_slc_or_wheel_touch_owner_keeps_priority(monkeypatch):
  from openpilot.starpilot.ui.runtime_app import StarShellSession
  from openpilot.starpilot.ui.shell import ShellMode
  card = Mock()
  session = StarShellSession.__new__(StarShellSession)
  session.snapshot = Mock(return_value=NS(onroad=state()))
  session._input_snapshot = session.snapshot
  session._update_favorites = Mock()
  session.input = Mock(onroad=NS(claimed=True))
  session.favorites = Mock(is_open=False, claims_corner=Mock(return_value=False))
  monkeypatch.setattr(session, 'view', NS(onroad=NS(navigation=card)), raising=False)
  session.press(ShellMode.ONROAD, 120, 40)
  card.press.assert_not_called()
  session.favorites.press.assert_not_called()


def test_unchanged_instruction_reuses_measured_layout():
  fonts = Mock(profile=Profile.COMPACT)
  fonts.measure.side_effect = lambda text, role, size: NS(width=len(text) * size * .48)
  card = NavigationCard(fonts)
  first = card._lines('Turn left onto Main Street', 220, 28)
  measured = fonts.measure.call_count
  assert card._lines('Turn left onto Main Street', 220, 28) == first
  assert fonts.measure.call_count == measured
  card._lines('Turn right onto Broad Street', 220, 28)
  assert fonts.measure.call_count > measured


def test_single_line_instruction_marks_truncated_text_and_has_its_own_cache():
  fonts = Mock(profile=Profile.COMPACT)
  fonts.measure.side_effect = lambda text, role, size: NS(width=len(text) * size * .48)
  card = NavigationCard(fonts)
  text = 'Turn left onto West Charleston Boulevard'
  assert len(card._lines(text, 220, 20)) == 2
  line, = card._lines(text, 220, 20, max_lines=1)
  assert line.endswith('...') and fonts.measure(line, FontRole.SEMI_BOLD, 20).width <= 220


def test_compact_two_line_instruction_clears_then_chip():
  fonts = Mock(profile=Profile.COMPACT)
  fonts.measure.side_effect = lambda text, role, size: NS(width=len(text) * size * .48)
  card = NavigationCard(fonts)
  nav = replace(state().navigation, text='Turn left onto West Charleston Boulevard',
                then_type='turn', then_modifier='right', then_distance_m=300.)
  with patch.object(card, '_icon', return_value=NS(width=100, height=100)), \
       patch.object(rl, 'draw_rectangle_rounded') as rounded, patch.object(rl, 'draw_rectangle_rounded_lines_ex'), \
       patch.object(rl, 'draw_texture_pro'):
    card.render(replace(state(), navigation=nav))
  instruction = [call.args for call in fonts.draw.call_args_list if call.args[1] == FontRole.SEMI_BOLD and call.args[2] == 20]
  assert len(instruction) == 2
  then_chip = rounded.call_args_list[1].args[0]
  assert max(args[4] + args[2] for args in instruction) < then_chip.y


def test_saved_favorite_revision_does_not_expand_navigation_card():
  card = NavigationCard(Mock(profile=Profile.COMPACT))
  observed = state()
  assert card.press(120, 40, observed)
  card.release(120, 40, observed)
  nav = message()
  nav.revision = 'favorite-added'
  refreshed = replace(observed, navigation=navigation_display(nav, now_ns=NOW, drive_id=DRIVE))
  assert refreshed.navigation.key == observed.navigation.key
  assert card.bounds(refreshed).width == 72
  assert card.collapsed


def test_replay_without_wall_anchor_omits_eta_but_keeps_recorded_navigation():
  fonts = Mock(profile=Profile.LARGE)
  fonts.measure.side_effect = lambda text, role, size: NS(width=len(text) * size * .48)
  card = NavigationCard(fonts, clock=lambda: None)
  with patch.object(card, '_icon', return_value=NS(width=100, height=100)), \
       patch.object(rl, 'draw_rectangle_rounded'), patch.object(rl, 'draw_rectangle_rounded_lines_ex'), patch.object(rl, 'draw_texture_pro'), \
       patch.object(rl, 'draw_line_ex'), patch.object(rl, 'draw_rectangle_rec'):
    card.render(state())
  text = ' '.join(call.args[0] for call in fonts.draw.call_args_list)
  assert 'Turn left onto Main Street' in text
  assert '400 ft' in text
  assert 'min' in text
  assert 'ETA' not in text


def test_replay_without_wall_anchor_omits_optional_hud_clock():
  from openpilot.starpilot.ui.onroad import OnroadView
  from openpilot.starpilot.ui.onroad_customization import CLOCK_WIDGET, default_document
  observed = state()
  document = default_document()
  document['layouts']['large'][CLOCK_WIDGET]['enabled'] = True
  observed = replace(observed, customization=document)
  view = OnroadView.__new__(OnroadView)
  view.personality = Mock()
  view.fonts = Mock(profile=Profile.LARGE)
  view.wall_clock = lambda: None
  view._clock(observed)
  view.fonts.draw.assert_not_called()


def test_then_maneuver_comes_from_the_next_instruction_and_card_shows_it_when_close():
  result = message()
  result.nextManeuver = {'maneuverType': 'turn', 'maneuverModifier': 'right', 'distanceMeters': 300}
  nav = navigation_display(result, now_ns=NOW, drive_id=DRIVE)
  assert nav is not None
  assert (nav.then_type, nav.then_modifier, nav.then_distance_m) == ('turn', 'right', 300.)
  assert navigation_display(message(), now_ns=NOW, drive_id=DRIVE).then_type == ''
  fonts = Mock(profile=Profile.LARGE)
  fonts.measure.side_effect = lambda text, role, size: NS(width=len(text) * size * .48)
  card = NavigationCard(fonts, clock=lambda: datetime(2026, 10, 7, 15, 42))
  with patch.object(card, '_icon', return_value=NS(width=100, height=100)) as icon, \
       patch.object(rl, 'draw_rectangle_rounded'), patch.object(rl, 'draw_rectangle_rounded_lines_ex'), \
       patch.object(rl, 'draw_texture_pro'), patch.object(rl, 'draw_line_ex'), patch.object(rl, 'draw_rectangle_rec'):
    card.render(replace(state(), navigation=nav))
    assert 'Then' in [call.args[0] for call in fonts.draw.call_args_list]
    assert icon.call_count == 2
    fonts.draw.reset_mock()
    card.render(replace(state(), navigation=replace(nav, then_distance_m=900.)))
    assert 'Then' not in [call.args[0] for call in fonts.draw.call_args_list]


def test_progress_bar_tracks_the_longest_remaining_distance_per_trip():
  fonts = Mock(profile=Profile.LARGE)
  fonts.measure.side_effect = lambda text, role, size: NS(width=len(text) * size * .48)
  card = NavigationCard(fonts, clock=lambda: datetime(2026, 10, 7, 15, 42))
  observed = state()
  with patch.object(card, '_icon', return_value=NS(width=100, height=100)), \
       patch.object(rl, 'draw_rectangle_rounded') as rounded, patch.object(rl, 'draw_rectangle_rounded_lines_ex'), \
       patch.object(rl, 'draw_texture_pro'), patch.object(rl, 'draw_line_ex'):
    card.render(observed)
    card.render(replace(observed, navigation=replace(observed.navigation, remaining_distance_m=2150.)))
  track, fill = (call.args[0] for call in rounded.call_args_list[-2:])
  eta_width = len('ETA 3:50 PM') * 28 * .48
  assert track.width == pytest.approx(560 - 48 - eta_width - 18), 'the bar stops short of the ETA beside it'
  assert fill.width == pytest.approx(track.width * .5)
  card.bounds(replace(observed, navigation=replace(observed.navigation, key=('other', 1))))
  assert card._total == 0



LANES = ((False, ('straight',), ''), (True, ('straight', 'right'), 'right'), (True, ('right',), 'right'))


def test_lanes_are_read_from_the_instruction():
  result = message()
  result.instruction = {**result.instruction.to_dict(), 'lanes': [
    {'active': False, 'directions': ['straight']}, {'active': True, 'directions': ['straight', 'right'], 'activeDirection': 'right'}]}
  assert navigation_display(result, now_ns=NOW, drive_id=DRIVE).lanes == (
    (False, ('straight',), ''), (True, ('straight', 'right'), 'right'))
  assert navigation_display(message(), now_ns=NOW, drive_id=DRIVE).lanes == ()


def test_lane_strip_shows_near_the_turn_and_replaces_the_then_chip():
  fonts = Mock(profile=Profile.LARGE)
  fonts.measure.side_effect = lambda text, role, size: NS(width=len(text) * size * .48)
  card = NavigationCard(fonts, clock=lambda: datetime(2026, 10, 7, 15, 42))
  nav = replace(state().navigation, lanes=LANES, then_type='turn', then_modifier='right', then_distance_m=300.)
  with patch.object(card, '_icon', return_value=NS(width=100, height=100)) as icon, \
       patch.object(rl, 'draw_rectangle_rounded') as rounded, patch.object(rl, 'draw_rectangle_rounded_lines_ex'), \
       patch.object(rl, 'draw_texture_pro') as textures, patch.object(rl, 'draw_line_ex'), patch.object(rl, 'draw_rectangle_rec'):
    card.render(replace(state(), navigation=nav))
    assert rounded.call_count == 1 + len(LANES) + 1, 'the card, one cell per lane and the progress track; no Then chip'
    assert 'Then' not in [call.args[0] for call in fonts.draw.call_args_list]
    assert textures.call_count == len(LANES), 'the lanes carry the maneuver; no separate icon'
    assert [call.args[0] for call in icon.call_args_list[1:]] == ['direction_turn_straight.png', 'direction_turn_right.png', 'direction_turn_right.png']
    rounded.reset_mock()
    card.render(replace(state(), navigation=replace(nav, distance_m=900., then_distance_m=1200.)))
    assert rounded.call_count == 1 + 1 + 1, 'too far for lanes: the card, the Then chip and the progress track'
