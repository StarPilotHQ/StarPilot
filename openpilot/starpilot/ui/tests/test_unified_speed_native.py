"""One set speed and a shared, latest-limit sign in the large and compact UIs."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.common.constants import CV
from openpilot.starpilot.ui.raylib import rl
from openpilot.starpilot.ui.onroad_customization import default_document
from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
from openpilot.starpilot.ui.onroad import OnroadView
from openpilot.starpilot.ui.onroad_state import ObservationKind, OnroadState, SpeedLimitObservation, slc_controls, speed_limit_bounds
from openpilot.starpilot.ui.presentation import FontRole, Profile
from openpilot.starpilot.ui.speed_limit_widget import INK, IN_USE, MUTED, REJECT, SpeedLimitWidget, draw_text
from openpilot.starpilot.ui.speed_limit_shapes import SpeedLimitShapes
from openpilot.starpilot.ui.unified_speed_presentation import displayed_limit_mps, resolve_unified_speed


@pytest.fixture(autouse=True)
def drawing(monkeypatch):
  calls = {}
  for name in ('draw_rectangle_rounded_lines_ex', 'draw_rectangle_rounded', 'draw_line_ex', 'draw_circle',
               'draw_ring', 'draw_circle_gradient', 'draw_rectangle_rec', 'draw_circle_v', 'draw_rectangle_gradient_ex'):
    calls[name] = Mock()
    monkeypatch.setattr(rl, name, calls[name])
  for name in ('circle', 'arrow', 'outline', 'wheel_key', 'button'):
    calls[name] = Mock()
    monkeypatch.setattr(SpeedLimitShapes, name, calls[name])
  monkeypatch.setattr('openpilot.starpilot.ui.onroad_large_widgets.draw_control_card', Mock())
  return calls


def state(**changes):
  from openpilot.starpilot.speed_limits.speed_domain import OffsetBand, OffsetSchedule
  observation = SpeedLimitObservation(ObservationKind.VALID, 'map', 50 / 3.6,
    accepted_speed_limit_mps=40 / 3.6, accepted_adjusted_limit_mps=45 / 3.6,
    pending_speed_limit_mps=50 / 3.6, pending_adjusted_limit_mps=55 / 3.6,
    presentation_adjusted_limit_mps=55 / 3.6, accepted_source='dashboard', action_enabled=True,
    session_id='drive', presentation_id=2, decision_id=1, pending_since_ns=10_000_000_000)
  return replace(OnroadState(True, False, 45 / 3.6, 100., observation, metric=True, longitudinal_active=True,
                              slc_offset_schedule=OffsetSchedule((OffsetBand(0., None, 5 / 3.6),)),
                              observed_ns=10_000_000_000), **changes)


def fonts(profile=Profile.LARGE):
  return SimpleNamespace(profile=profile, draw=Mock(), vertical_ink=lambda text, role, size: (0, size * .75),
                         horizontal_ink=lambda text, role, size: (0, len(text) * size * .5),
                         measure=lambda text, role, size: SimpleNamespace(width=len(text) * size * .5, height=size))


def slc_view(font):
  view = OnroadView.__new__(OnroadView)
  view.personality = Mock()
  view.fonts = font
  if font.profile == Profile.LARGE:
    view.unified_speed = UnifiedSpeedWidget(font)
  else:
    view.compact_hud = CompactHudRenderer(font, Mock())
  return view


def render_sign(sign, current):
  sign.render(current)


def foreground_calls(draw):
  return [call for call in draw.call_args_list if (call.args[-1].r, call.args[-1].g, call.args[-1].b) not in ((6, 14, 10), (7, 16, 12))]


def rgba_tuple(color):
  return (color.r, color.g, color.b, color.a)


def test_latest_limit_and_offset_replace_accepted_sign_while_waiting():
  shown = resolve_unified_speed(state())
  assert (shown.max_text, shown.posted_text, shown.offset_text, shown.source) == ('100', '50', '+5', 'map')
  assert displayed_limit_mps(state().speed_limit) == 50 / 3.6


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('vienna', [False, True])
def test_sign_keeps_distinct_slc_target_when_set_widget_is_hidden(profile, vienna, drawing):
  current = state()
  customization = default_document()
  customization['showMaxSpeed'] = False
  customization['layouts']['compact']['max_speed']['enabled'] = False
  current = replace(current, appearance=replace(current.appearance, use_vienna_sign=vienna), customization=customization,
                    speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None, limiting_max_set=True,
                                        effective_cluster_target_mps=45 / 3.6))
  font = fonts(profile)
  render_sign(SpeedLimitWidget(font, profile), current)
  assert [call.args[0] for call in foreground_calls(font.draw)] == ['LIMIT', '45', 'OSM 50 +5']
  drawing['outline'].assert_called_once()
  drawing['circle'].assert_not_called()


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('stage', ['live', 'retained', 'prompt', 'expanded'])
def test_set_and_binding_slc_stay_visible_beside_sign_or_prompt(profile, stage):
  current = state(metric=False, cruise_kph=35 * CV.MPH_TO_KPH, drive_frame=1, slc_prompt_open=stage == 'expanded')
  pending = stage in ('prompt', 'expanded')
  gap = stage != 'live'
  speed = 30 * CV.MPH_TO_MS
  observation = replace(current.speed_limit, kind=ObservationKind.ABSENT if gap else ObservationKind.VALID,
    source='none' if gap else 'dashboard', speed_limit_mps=None if gap else speed,
    presentation_adjusted_limit_mps=speed, last_received_speed_limit_mps=speed, last_received_source='dashboard',
    pending_speed_limit_mps=25 * CV.MPH_TO_MS if pending else None,
    pending_adjusted_limit_mps=25 * CV.MPH_TO_MS if pending else None, pending_source='map',
    effective_cluster_target_mps=speed, effective_cap_mps=speed, limiting_max_set=True,
    accepted_speed_limit_mps=speed, accepted_adjusted_limit_mps=speed, accepted_source='dashboard')
  current = replace(current, speed_limit=observation)
  font = fonts(profile)
  view = slc_view(font)
  if profile == Profile.LARGE:
    view.unified_speed.render(rl.Rectangle(0, 0, 2160, 1080), current)
  else:
    for now in range(current.observed_ns, current.observed_ns + 4_000_000_000, 100_000_000):
      view.compact_hud.render_max_speed(replace(current, observed_ns=now))
    font.draw.reset_mock()
    view.compact_hud.render_max_speed(replace(current, observed_ns=current.observed_ns + 4_000_000_000))
    view.compact_hud._speed_limit_sign(current)
  view._slc_actions(current)
  texts = [call.args[0] for call in foreground_calls(font.draw)]
  assert ('SET' if profile == Profile.LARGE else 'MAX') in texts and '35' in texts and not any(text.startswith('SLC ') for text in texts)
  assert ('LIMIT 30' in texts) == (stage == 'prompt' and profile == Profile.LARGE)
  if stage == 'prompt' and profile == Profile.COMPACT:
    assert 'NEW LIMIT' in texts
  assert ('Now: LIMIT 30' in texts) == (stage == 'expanded')
  assert ('LIMIT' in texts) == (stage in ('live', 'retained'))
  if pending:
    assert '25' in texts and not any(text.startswith('LAST') for text in texts)
    assert ('OSM' in texts) == (stage != 'expanded')
  else:
    assert 'DASH' in texts and '30' in texts and not any(text.startswith('LAST') for text in texts)


@pytest.mark.parametrize('gap', [False, True])
def test_compact_max_fades_while_the_restricted_limit_keeps_its_warning(gap, drawing):
  current = state(cruise_kph=45, drive_frame=1)
  current = replace(current, speed_limit=replace(current.speed_limit, source='dashboard', speed_limit_mps=55 / 3.6,
    accepted_source='dashboard', accepted_speed_limit_mps=55 / 3.6, accepted_adjusted_limit_mps=55 / 3.6,
    presentation_adjusted_limit_mps=55 / 3.6, effective_cluster_target_mps=55 / 3.6, effective_cap_mps=55 / 3.6,
    pending_speed_limit_mps=None, limiting_max_set=False))
  if gap:
    current = replace(current, speed_limit=replace(current.speed_limit, kind=ObservationKind.STALE,
                                                  source='unknown', speed_limit_mps=None))
  font = fonts(Profile.COMPACT)
  hud = CompactHudRenderer(font, Mock())
  for elapsed in range(0, 6_000_000_000, 100_000_000):
    hud.render_max_speed(replace(current, observed_ns=current.observed_ns + elapsed))
  assert hud._set_speed_alpha.x < .01
  assert {call.args[0] for call in foreground_calls(font.draw)} == {'MAX', '45'}
  font.draw.reset_mock()
  hud._speed_limit_sign(current)
  assert [call.args[0] for call in foreground_calls(font.draw)] == ['LIMIT', '55', 'SET IS LOW', 'RAISE SET']


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('vienna', [False, True])
@pytest.mark.parametrize('offset', [0, 5, 10])
@pytest.mark.parametrize('selected', ['below', 'equal', 'above'])
def test_accepted_sign_keeps_text_geometry_and_styling_through_source_gaps(profile, vienna, offset, selected, drawing):
  current = state(cruise_kph={'below': 45, 'equal': 55 + offset, 'above': 70}[selected])
  obs = replace(current.speed_limit, source='dashboard', speed_limit_mps=55 / 3.6,
    accepted_source='dashboard', accepted_speed_limit_mps=55 / 3.6, accepted_adjusted_limit_mps=(55 + offset) / 3.6,
    presentation_adjusted_limit_mps=(55 + offset) / 3.6, effective_cluster_target_mps=(55 + offset) / 3.6,
    effective_cap_mps=(55 + offset) / 3.6, pending_speed_limit_mps=None, limiting_max_set=selected == 'above')
  current = replace(current, appearance=replace(current.appearance, use_vienna_sign=vienna))
  font = fonts(profile)
  sign = SpeedLimitWidget(font, profile)
  gap = replace(obs, kind=ObservationKind.STALE, source='unknown', speed_limit_mps=None,
                last_received_source='vision', last_received_speed_limit_mps=99 / 3.6,
                last_received_adjusted_limit_mps=109 / 3.6)
  rendered = []
  for observation in (obs, gap, replace(obs, sign_recovering=True)):
    font.draw.reset_mock()
    for shape in ('outline', 'circle'):
      drawing[shape].reset_mock()
    render_sign(sign, replace(current, speed_limit=observation))
    text = [(*call.args[:-1], rgba_tuple(call.args[-1])) for call in foreground_calls(font.draw)]
    shapes = [(shape, *call.args[:2], rgba_tuple(call.args[2]), call.kwargs)
              for shape in ('outline', 'circle') for call in drawing[shape].call_args_list]
    rendered.append((text, shapes))
  assert rendered[0] == rendered[1] == rendered[2]
  texts = [row[0] for row in rendered[0][0]]
  assert not any(text.startswith('LAST') for text in texts)
  assert ('SET IS LOW' in texts) == (selected == 'below')
  assert ('RAISE SET' in texts) == (selected == 'below')


@pytest.mark.parametrize('profile', list(Profile))
def test_confirmation_arrow_uses_upcoming_target_while_current_reading_stays_current(profile, drawing):
  current = state()
  current = replace(current, speed_limit=replace(current.speed_limit,
    pending_speed_limit_mps=30 / 3.6, pending_adjusted_limit_mps=35 / 3.6))
  font = fonts(profile)
  SpeedLimitWidget(font, profile).render_confirmation(current)
  assert drawing['arrow'].call_args.args[3] == 1
  assert '30' in [call.args[0] for call in foreground_calls(font.draw)]
  assert current.speed_limit.speed_limit_mps == 50 / 3.6


@pytest.mark.parametrize('profile', list(Profile))
def test_both_renderers_show_small_latest_sign_and_offset(profile):
  font = fonts(profile)
  current = state()
  if profile == Profile.LARGE:
    widget = UnifiedSpeedWidget(font)
    widget.render(rl.Rectangle(30, 30, 1800, 1020), current)
    widget.sign.render_confirmation(current)
    texts = [call.args[0] for call in foreground_calls(font.draw)]
    assert texts.count('100') == 1
    assert 'SET' in texts
    assert 'SLC LIMIT' not in texts
  else:
    widget = CompactHudRenderer(font, Mock())
    widget._speed_limit_sign(current)
    widget._speed_limit_widget.render_confirmation(current)
  texts = [call.args[0] for call in foreground_calls(font.draw)]
  assert '50' in texts and '+5' in texts and '40' not in texts


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('pending', [False, True])
@pytest.mark.parametrize('vienna', [False, True])
@pytest.mark.parametrize('offset', [False, True])
@pytest.mark.parametrize('source, heading', [
  ('dashboard', 'DASH'), ('map', 'OSM'), ('vision', 'VISION'), ('mapbox', 'NAV'),
  ('online', 'SPEED'), ('unknown', 'SPEED'), ('none', 'SPEED'),
])
def test_sign_heading_identifies_the_displayed_source(profile, pending, vienna, offset, source, heading):
  current = state(show_slc_offset=offset)
  current = replace(current, appearance=replace(current.appearance, use_vienna_sign=vienna))
  current = replace(current, speed_limit=replace(current.speed_limit, source=source,
    accepted_source='vision' if source == 'dashboard' else 'dashboard',
    pending_speed_limit_mps=current.speed_limit.pending_speed_limit_mps if pending else None))
  font = fonts(profile)
  render_sign(SpeedLimitWidget(font, profile), current)
  expected = [heading, '50']
  assert [call.args[0] for call in foreground_calls(font.draw)] == expected + (['+5'] if offset else []) + (['NEW LIMIT', 'Tap to review'] if pending else [])
  if not pending:
    label, speed = foreground_calls(font.draw)[:2]
    assert rgba_tuple(label.args[-1]) == rgba_tuple(speed.args[-1])
  if vienna and not pending:
    assert rgba_tuple(foreground_calls(font.draw)[0].args[-1]) == (244, 247, 245, 255)


@pytest.mark.parametrize('metric', [False, True])
@pytest.mark.parametrize('vienna', [False, True])
@pytest.mark.parametrize('offset', [None, 0., .01, -.01])
def test_compact_sign_omits_missing_zero_and_rounded_zero_offsets(metric, vienna, offset):
  current = state(metric=metric)
  factor = 3.6 if metric else 2.2369362921
  current = replace(current, appearance=replace(current.appearance, use_vienna_sign=vienna),
    speed_limit=SpeedLimitObservation(ObservationKind.VALID, 'map', 65 / factor, offset_mps=offset,
                                    action_enabled=True))
  assert resolve_unified_speed(current).offset_text is None
  font = fonts(Profile.COMPACT)
  CompactHudRenderer(font, Mock())._speed_limit_sign(current)
  assert [call.args[0] for call in foreground_calls(font.draw)] == ['OSM', '65']


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('vienna', [False, True])
def test_pending_notice_pulses_without_changing_ink_and_arrow_follows_speed(profile, vienna, drawing):
  font = fonts(profile)
  sign = SpeedLimitWidget(font, profile)
  current = state()
  current = replace(current, appearance=replace(current.appearance, use_vienna_sign=vienna))
  render_sign(sign, current)
  before = {call.args[0]: call.args for call in foreground_calls(font.draw)}
  assert drawing['arrow'].call_args.args[0] == 'compact'
  assert drawing['arrow'].call_args.args[3] == -1
  render_sign(sign, replace(current, observed_ns=10_350_000_000, speed_mps=60 / 3.6))
  after = {call.args[0]: call.args for call in foreground_calls(font.draw)}
  assert set(before) == {'OSM', '50', '+5', 'NEW LIMIT', 'Tap to review'}
  assert all(after[text][3:5] == args[3:5] for text, args in before.items())
  assert all(rgba_tuple(after[text][-1]) == rgba_tuple(args[-1]) for text, args in before.items() if text != 'NEW LIMIT')
  assert rgba_tuple(after['NEW LIMIT'][-1]) != rgba_tuple(before['NEW LIMIT'][-1])
  drawing['circle'].assert_not_called()
  drawing['outline'].assert_not_called()
  assert drawing['arrow'].call_args.args[3] == 1


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('vienna', [False, True])
@pytest.mark.parametrize('recovering', [False, True])
def test_regular_sign_keeps_the_outlined_arrow(profile, vienna, recovering, drawing):
  current = state()
  current = replace(current, appearance=replace(current.appearance, use_vienna_sign=vienna),
                    speed_limit=replace(current.speed_limit, decision_id=0, sign_recovering=recovering))
  font = fonts(profile)
  SpeedLimitWidget(font, profile).render(current)
  assert foreground_calls(font.draw)[0].args[0] == ('LAST OSM' if recovering and profile == Profile.LARGE else 'OSM')
  assert drawing['arrow'].call_args.args[0] == 'compact'


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('available', [False, True])
def test_wheel_cues_follow_confirmation_availability(profile, available, drawing):
  from openpilot.starpilot.ui.large_speed_geometry import CONFIRMATION_SCALE
  current = state(slc_wheel_confirmation_available=available)
  sign = SpeedLimitWidget(fonts(profile), profile)
  sign.render_confirmation(current)
  drawing['draw_rectangle_rounded_lines_ex'].assert_not_called()
  drawing['button'].assert_not_called()
  texts = [call.args[0] for call in foreground_calls(sign.fonts.draw)]
  assert 'NEW LIMIT' in texts
  assert ('ACCEPT' in texts) == available
  assert ('REJECT' in texts) == available
  calls = drawing['wheel_key'].call_args_list
  assert [call.args[0] for call in calls] == ([True, False] if available else [])
  if available:
    bounds = speed_limit_bounds(profile, current)
    assert bounds is not None
    hint_y = bounds[1] + (12 if profile == Profile.LARGE else 8) + (142 if profile == Profile.LARGE else 136.75) * CONFIRMATION_SCALE[profile]
    assert all(call.args[2] == pytest.approx(hint_y) for call in calls)
    assert bounds[0] < calls[0].args[1] < calls[1].args[1] < bounds[2]


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('vienna', [False, True])
@pytest.mark.parametrize('reason', ['steady', 'automatic', 'equal_speed', 'unknown_speed'])
def test_sign_text_stays_centered_without_an_arrow(profile, vienna, reason, drawing):
  current = state()
  current = replace(current, appearance=replace(current.appearance, use_vienna_sign=vienna))
  if reason == 'steady':
    current = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None))
  elif reason == 'automatic':
    current = replace(current, speed_limit=replace(current.speed_limit, automatic_adoption_pending=True))
  else:
    current = replace(current, speed_mps=55 / 3.6 if reason == 'equal_speed' else None)
  font = fonts(profile)
  render_sign(SpeedLimitWidget(font, profile), current)
  drawing['arrow'].assert_not_called()
  for call in foreground_calls(font.draw):
    text, role, size, x, _, _ = call.args
    if reason in ('equal_speed', 'unknown_speed'):
      continue
    left, right = font.horizontal_ink(text, role, size)
    from openpilot.starpilot.ui.large_speed_geometry import LIMIT_RECTS, LARGE_SIGN_SCALE
    center = LIMIT_RECTS[profile][0] + 64 * (1 if profile == Profile.COMPACT else LARGE_SIGN_SCALE)
    assert abs(x + (left + right) / 2 - center) <= .5


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('vienna', [False, True])
@pytest.mark.parametrize('retained', [False, True])
def test_override_sign_uses_cluster_target_and_restores_normal_sign(profile, vienna, retained, drawing):
  font = fonts(profile)
  sign = SpeedLimitWidget(font, profile)
  current = state(cruise_kph=140)
  current = replace(current, appearance=replace(current.appearance, use_vienna_sign=vienna),
                    speed_limit=replace(current.speed_limit, speed_limit_mps=100 / 3.6,
                      presentation_adjusted_limit_mps=100 / 3.6, pending_speed_limit_mps=None,
                      effective_cluster_target_mps=128 / 3.6))
  if retained:
    current = replace(current, speed_limit=replace(current.speed_limit, kind=ObservationKind.ABSENT, source='none',
                      speed_limit_mps=None, last_received_speed_limit_mps=100 / 3.6, last_received_source='map'))
  overridden = replace(current, speed_limit=replace(current.speed_limit, driver_override_active=True))
  sign.render(replace(overridden, longitudinal_overridden=True))
  assert 'Override' not in [call.args[0] for call in foreground_calls(font.draw)]
  assert 'Pedal' in [call.args[0] for call in foreground_calls(font.draw)]
  font.draw.reset_mock()
  sign.render(overridden)
  rows = [call.args for call in foreground_calls(font.draw)]
  assert [row[0] for row in rows] == (['Override', '128', 'LAST OSM', '100'] if retained and profile == Profile.LARGE else ['Override', '128', 'OSM 100'])
  assert rows[1][2] > rows[-1][2]
  for first, second in zip(rows, rows[1:], strict=False):
    assert first[4] + font.vertical_ink(*first[:3])[1] <= second[4] + font.vertical_ink(*second[:3])[0] + 1
  drawing['outline'].assert_called()
  font.draw.reset_mock()
  sign.render(current)
  assert foreground_calls(font.draw)[0].args[0] == ('LAST OSM' if retained and profile == Profile.LARGE else 'OSM')
  drawing['circle' if vienna else 'outline'].assert_called()


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('vienna', [False, True])
@pytest.mark.parametrize('latest_received', [False, True])
@pytest.mark.parametrize('offset', [False, True])
@pytest.mark.parametrize('source,heading', [('map', 'LAST OSM'), ('vision', 'LAST VISION'), ('dashboard', 'LAST DASH'),
                                             ('mapbox', 'LAST NAV'), ('online', 'LAST'), ('unknown', 'LAST')])
def test_retained_limits_keep_original_source_and_have_no_actions(profile, vienna, latest_received, offset, source, heading, drawing):
  current = state(show_slc_offset=offset)
  observation = replace(current.speed_limit, kind=ObservationKind.STALE, source='none', speed_limit_mps=None,
    pending_speed_limit_mps=None, last_received_speed_limit_mps=50/3.6 if latest_received else None,
    last_received_source=source, accepted_source=('vision' if source == 'dashboard' else 'dashboard') if latest_received else source,
    last_received_adjusted_limit_mps=55/3.6 if latest_received else None, effective_cluster_target_mps=45/3.6)
  current = replace(current, speed_limit=observation, appearance=replace(current.appearance, use_vienna_sign=vienna))
  font = fonts(profile)
  render_sign(SpeedLimitWidget(font, profile), current)
  texts = [call.args[0] for call in foreground_calls(font.draw)]
  expected_heading = heading if profile == Profile.LARGE else heading.removeprefix('LAST ').replace('LAST', 'SPEED')
  assert texts.count(expected_heading) == 1
  if profile == Profile.COMPACT:
    assert not any(text.startswith('LAST') for text in texts)
  assert ('50' if latest_received else '40') in texts
  drawing['arrow'].assert_not_called()
  assert slc_controls(profile, current) == ()


@pytest.mark.parametrize('profile', list(Profile))
def test_retained_sign_has_no_proposal_arrow_when_actions_are_hidden(profile, drawing):
  current = state()
  current.customization['layouts'][profile]['speed_limit_actions']['enabled'] = False
  observation = replace(current.speed_limit, kind=ObservationKind.ABSENT, source='none', speed_limit_mps=None,
                        last_received_speed_limit_mps=50 / 3.6, last_received_source='map')
  font = fonts(profile)
  render_sign(SpeedLimitWidget(font, profile), replace(current, speed_limit=observation))
  assert foreground_calls(font.draw)[0].args[0] == ('LAST OSM' if profile == Profile.LARGE else 'OSM')
  drawing['arrow'].assert_not_called()


def test_compact_set_speed_fades_after_adjustment_and_reappears_on_change():
  renderer = CompactHudRenderer(fonts(), Mock())
  current = state(drive_frame=1)
  current = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None))
  for now in range(10_000_000_000, 20_000_000_000, 100_000_000):
    alpha = renderer._set_speed_opacity(current, now)
  assert alpha < .01
  changed = replace(current, cruise_kph=current.cruise_kph + 10)
  assert renderer._set_speed_opacity(changed, 20_000_000_000) < .01
  assert renderer._set_speed_opacity(changed, 20_020_000_000) > .01
  assert renderer._set_speed_opacity(replace(current, longitudinal_active=False), 20_000_000_000) == 0


def test_compact_set_speed_stays_visible_during_confirmation_then_fades():
  renderer = CompactHudRenderer(fonts(Profile.COMPACT), Mock())
  current = state(drive_frame=1)
  for now in range(10_000_000_000, 20_000_000_000, 100_000_000):
    alpha = renderer._set_speed_opacity(current, now)
  assert alpha > .99
  resolved = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None))
  for now in range(20_000_000_000, 21_000_000_000, 16_666_667):
    alpha = renderer._set_speed_opacity(resolved, now)
  assert alpha < .01


def test_compact_max_fades_while_pedal_status_remains_in_the_sign():
  font = fonts(Profile.COMPACT)
  renderer = CompactHudRenderer(font, Mock())
  current = state(drive_frame=1, longitudinal_overridden=True)
  current = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None,
                    driver_override_active=True, override_basis='pedal', limiting_max_set=False))
  for now in range(10_000_000_000, 20_000_000_000, 100_000_000):
    renderer.render_max_speed(replace(current, observed_ns=now))
  assert renderer._set_speed_alpha.x < .01
  assert 'MAX' in [call.args[0] for call in foreground_calls(font.draw)]
  assert 'PEDAL' not in [call.args[0] for call in foreground_calls(font.draw)]
  font.draw.reset_mock()
  renderer._speed_limit_sign(current)
  assert 'Pedal' in [call.args[0] for call in foreground_calls(font.draw)]


def test_compact_max_restores_dinish_number_label_position_and_gradient(drawing):
  from openpilot.starpilot.ui.presentation import font_filename
  font = fonts(Profile.COMPACT)
  hud = CompactHudRenderer(font, Mock())
  current = state(drive_frame=1, cruise_kph=80)
  current = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None))
  hud.render_max_speed(current)
  hud.render_max_speed(replace(current, observed_ns=current.observed_ns + 20_000_000))
  assert [(call.args[0], call.args[1]) for call in foreground_calls(font.draw)] == [
    ('80', FontRole.SET_SPEED), ('MAX', FontRole.SEMI_BOLD)]
  assert font_filename(Profile.COMPACT, FontRole.SET_SPEED) == 'DINish-Speed.fnt'
  number, label = (call.args for call in foreground_calls(font.draw))
  assert number[2:5] == (112, 17, -4)
  assert label[2:5] == (36, 25, 109)
  drawing['draw_circle_gradient'].assert_called()


@pytest.mark.parametrize('metric', [False, True])
@pytest.mark.parametrize('engaged', [False, True])
def test_compact_unset_max_stays_hidden(metric, engaged, drawing):
  font = fonts(Profile.COMPACT)
  hud = CompactHudRenderer(font, Mock())
  current = state(metric=metric, engaged=engaged, longitudinal_active=engaged, cruise_kph=None, drive_frame=1)
  hud.render_max_speed(current)
  hud.render_max_speed(replace(current, observed_ns=current.observed_ns + 20_000_000))
  font.draw.assert_not_called()
  drawing['draw_circle_gradient'].assert_not_called()


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('metric', [False, True])
def test_limit_has_no_unit_when_set_speed_is_hidden(profile, metric):
  current = state(metric=metric)
  current = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None))
  current.customization['showMaxSpeed'] = False
  current.customization['layouts']['compact']['max_speed']['enabled'] = False
  font = fonts(profile)
  if profile == Profile.LARGE:
    UnifiedSpeedWidget(font).render(rl.Rectangle(30, 30, 1800, 1020), current)
  else:
    CompactHudRenderer(font, Mock()).render(current)
  texts = [call.args[0] for call in foreground_calls(font.draw)]
  assert texts and not any('mph' in text or 'km/h' in text for text in texts)


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('pressed_index', (0, 1))
def test_transparent_chooser_keeps_touch_targets_and_pressed_feedback(profile, pressed_index, drawing, monkeypatch):
  view = slc_view(fonts(profile))
  current = state(slc_prompt_open=True)
  controls = slc_controls(profile, current)
  view._slc_actions(current)
  rows = [call.args for call in foreground_calls(view.fonts.draw)]
  assert [row[0] for row in rows] == ['NEW LIMIT', 'ACCEPT', '55', 'REJECT', 'NEW LIMIT']
  drawing['draw_rectangle_rounded'].assert_not_called()
  drawing['draw_line_ex'].assert_not_called()
  drawing['draw_rectangle_rounded_lines_ex'].assert_not_called()
  outlines = drawing['button'].call_args_list
  assert [rgba_tuple(call.args[-1]) for call in outlines] == [rgba_tuple(IN_USE), rgba_tuple(REJECT)]
  for control, content in zip(controls, (rows[1:3], rows[3:]), strict=True):
    for row in content:
      left, top, right, bottom = control.bounds
      a, b = view.fonts.horizontal_ink(*row[:3])
      c, d = view.fonts.vertical_ink(*row[:3])
      assert left < row[3] + a < row[3] + b < right
      assert top < row[4] + c < row[4] + d < bottom
  original = [rgba_tuple(row[-1]) for row in rows if row[0] in ('ACCEPT', 'REJECT')]
  view.pressed_action = lambda: controls[pressed_index].request
  view.fonts.draw.reset_mock()
  drawing['button'].reset_mock()
  view._slc_actions(current)
  assert [call.args[:5] for call in foreground_calls(view.fonts.draw)] == [row[:5] for row in rows]
  pressed = [rgba_tuple(call.args[-1]) for call in foreground_calls(view.fonts.draw) if call.args[0] in ('ACCEPT', 'REJECT')]
  assert pressed[pressed_index] != original[pressed_index] and pressed[1-pressed_index] == original[1-pressed_index]
  pressed_outlines = drawing['button'].call_args_list
  assert [call.kwargs['pressed'] for call in pressed_outlines] == [pressed_index == 0, pressed_index == 1]
  assert not any(call.kwargs['pressed'] for call in outlines)
  for before, after in zip(outlines, pressed_outlines, strict=True):
    assert before.args[:2] == after.args[:2]
    assert before.kwargs['scale'] == after.kwargs['scale']
    assert rgba_tuple(before.args[-1]) == rgba_tuple(after.args[-1])
  assert slc_controls(profile, current) == controls
  monkeypatch.setattr('openpilot.starpilot.ui.speed_limit_widget.slc_controls',
                      lambda *_: tuple(replace(control, enabled=False) for control in controls))
  view.fonts.draw.reset_mock()
  drawing['button'].reset_mock()
  view._slc_actions(current)
  assert all(rgba_tuple(call.args[-1]) == rgba_tuple(MUTED) for call in foreground_calls(view.fonts.draw)[1:])
  assert all(rgba_tuple(call.args[-1]) == rgba_tuple(MUTED)
             for call in drawing['button'].call_args_list)
  assert not any(call.kwargs['pressed'] for call in drawing['button'].call_args_list)


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('alpha', [96, 255])
def test_text_contour_is_bounded_and_preserves_foreground_opacity(profile, alpha):
  from openpilot.starpilot.ui.presentation import FontRole
  font = fonts(profile)
  color = rl.Color(188, 132, 255, alpha)
  draw_text(font, '65 +5', FontRole.MEDIUM, 36, 100, 50, color)
  calls = [call.args for call in font.draw.call_args_list]
  assert len(calls) == 5
  assert all(call[:3] == ('65 +5', FontRole.MEDIUM, 36) for call in calls)
  assert calls[-1][3:5] == (100, 50) and calls[-1][-1] is color
  radius = 1.15 if profile == Profile.LARGE else 1.
  assert {(round(call[3]-100, 2), round(call[4]-50, 2)) for call in calls[:4]} == {
    (-radius, 0), (radius, 0), (0, -radius), (0, radius)}
  assert all(call[-1].a == round(alpha*.95) for call in calls[:4])


def test_full_coverage_contour_protects_base_muted_and_peak_purple_on_white():
  from openpilot.starpilot.ui.presentation import FontRole
  def luminance(channels):
    linear = [((x/255+.055)/1.055)**2.4 if x/255 > .04045 else x/255/12.92 for x in channels]
    return sum(x*weight for x, weight in zip(linear, (.2126, .7152, .0722), strict=True))
  for color in (INK, MUTED, IN_USE, REJECT, rl.Color(188, 132, 255, 255), rl.Color(170, 115, 235, 255)):
    font = fonts()
    draw_text(font, '65', FontRole.BOLD, 70, 100, 50, color)
    contour = font.draw.call_args_list[0].args[-1]
    background = [value*contour.a/255+255-contour.a for value in (contour.r, contour.g, contour.b)]
    assert (luminance((color.r, color.g, color.b))+.05)/(luminance(background)+.05) >= 4.5


def test_contour_accepts_raylib_standard_color_tuples():
  from openpilot.starpilot.ui.presentation import FontRole
  font = fonts()
  draw_text(font, 'Dash', FontRole.NORMAL, 22, 100, 50, rl.WHITE)
  color = font.draw.call_args.args[-1]
  assert (color.r, color.g, color.b, color.a) == (255, 255, 255, 255)


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('automatic', [False, True])
def test_purple_sign_renders_three_full_cycles_across_retention(profile, automatic):
  font = fonts(profile)
  sign = SpeedLimitWidget(font, profile)
  current = state(observed_ns=11_400_000_000, drive_frame=1)
  obs = replace(current.speed_limit, action_enabled=automatic, automatic_adoption_pending=automatic,
                pending_speed_limit_mps=50 / 3.6 if automatic else None)
  current = replace(current, speed_limit=obs)
  sign.render(current)
  # The source can expire/adopt while the same posted sign stays visible.
  retained = replace(current, speed_limit=replace(obs, kind=ObservationKind.STALE, speed_limit_mps=None,
                     last_received_speed_limit_mps=50 / 3.6, last_received_source='map',
                     pending_speed_limit_mps=None, automatic_adoption_pending=False))
  for frame in range(1, 181):
    elapsed = frame / 60
    font.draw.reset_mock()
    sign.render(replace(retained, observed_ns=11_400_000_000 + round(elapsed * 1e9)))
    colors = [rgba_tuple(call.args[-1]) for call in foreground_calls(font.draw)]
    assert colors
    if frame in (30, 90, 150):
      assert (188, 132, 255, 255) in colors
    if frame in (60, 120, 180):
      assert (188, 132, 255, 255) not in colors
  font.draw.reset_mock()
  sign.render(replace(current, observed_ns=14_900_000_000))
  assert all(rgba_tuple(call.args[-1]) != (188, 132, 255, 255) for call in foreground_calls(font.draw))
