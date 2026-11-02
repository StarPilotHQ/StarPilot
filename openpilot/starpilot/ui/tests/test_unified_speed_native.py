"""One set speed and a shared, latest-limit sign in the large and compact UIs."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
from openpilot.starpilot.ui.onroad import OnroadView
from openpilot.starpilot.ui.onroad_state import ObservationKind, OnroadState, SpeedLimitObservation, slc_controls, speed_limit_bounds
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.speed_limit_widget import INK, MUTED, SpeedLimitWidget, draw_text
from openpilot.starpilot.ui.speed_limit_shapes import SpeedLimitShapes
from openpilot.starpilot.ui.unified_speed_presentation import displayed_limit_mps, resolve_unified_speed


@pytest.fixture(autouse=True)
def drawing(monkeypatch):
  calls = {}
  for name in ('draw_rectangle_rounded_lines_ex', 'draw_rectangle_rounded', 'draw_line_ex', 'draw_circle',
               'draw_ring', 'draw_circle_gradient', 'draw_rectangle_rec', 'draw_circle_v', 'draw_rectangle_gradient_ex'):
    calls[name] = Mock()
    monkeypatch.setattr(rl, name, calls[name])
  for name in ('circle', 'arrow', 'outline', 'confirmation', 'wheel_key'):
    calls[name] = Mock()
    monkeypatch.setattr(SpeedLimitShapes, name, calls[name])
  monkeypatch.setattr('openpilot.starpilot.ui.onroad_large_widgets.draw_control_card', Mock())
  return calls


def state(**changes):
  observation = SpeedLimitObservation(ObservationKind.VALID, 'map', 50 / 3.6,
    accepted_speed_limit_mps=40 / 3.6, accepted_adjusted_limit_mps=45 / 3.6,
    pending_speed_limit_mps=50 / 3.6, pending_adjusted_limit_mps=55 / 3.6,
    presentation_adjusted_limit_mps=55 / 3.6, accepted_source='dashboard', action_enabled=True,
    session_id='drive', presentation_id=2, decision_id=1, pending_since_ns=10_000_000_000)
  return replace(OnroadState(True, False, 45 / 3.6, 100., observation, metric=True, longitudinal_active=True,
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
def test_confirmation_arrow_uses_upcoming_target_while_current_reading_stays_current(profile, drawing):
  current = state()
  current = replace(current, speed_limit=replace(current.speed_limit,
    pending_speed_limit_mps=30 / 3.6, pending_adjusted_limit_mps=35 / 3.6))
  font = fonts(profile)
  SpeedLimitWidget(font, profile).render_confirmation(current)
  assert drawing['arrow'].call_args.args[3] == 1
  assert '30' in [call.args[0] for call in font.draw.call_args_list]
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
  assert [call.args[0] for call in font.draw.call_args_list] == expected + (['+5'] if offset else []) + (['New Limit'] if pending else [])
  if vienna and not pending:
    assert rgba_tuple(font.draw.call_args_list[0].args[-1]) == (0, 0, 0, 255)


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
  assert [call.args[0] for call in font.draw.call_args_list] == ['OSM', '65']


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('vienna', [False, True])
def test_pending_notice_pulses_without_changing_ink_and_arrow_follows_speed(profile, vienna, drawing):
  font = fonts(profile)
  sign = SpeedLimitWidget(font, profile)
  current = state()
  current = replace(current, appearance=replace(current.appearance, use_vienna_sign=vienna))
  render_sign(sign, current)
  before = {call.args[0]: call.args for call in foreground_calls(font.draw)}
  strength = drawing['confirmation'].call_args.kwargs['strength']
  assert drawing['arrow'].call_args.args[0] == 'confirmation'
  assert drawing['arrow'].call_args.args[3] == -1
  render_sign(sign, replace(current, observed_ns=10_350_000_000, speed_mps=60 / 3.6))
  after = {call.args[0]: call.args for call in foreground_calls(font.draw)}
  assert set(before) == {'OSM', '50', '+5', 'New Limit'}
  assert all(after[text][3:5] == args[3:5] and rgba_tuple(after[text][-1]) == rgba_tuple(args[-1]) for text, args in before.items())
  assert drawing['confirmation'].call_args.kwargs['strength'] != strength
  drawing['circle'].assert_not_called()
  drawing['outline'].assert_not_called()
  assert drawing['arrow'].call_args.args[3] == 1


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('vienna', [False, True])
def test_regular_sign_keeps_the_outlined_arrow(profile, vienna, drawing):
  current = state()
  current = replace(current, appearance=replace(current.appearance, use_vienna_sign=vienna),
                    speed_limit=replace(current.speed_limit, decision_id=0))
  SpeedLimitWidget(fonts(profile), profile).render(current)
  assert drawing['arrow'].call_args.args[0] == 'compact'


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('available', [False, True])
def test_wheel_cues_follow_confirmation_availability(profile, available, drawing):
  from openpilot.starpilot.ui.large_speed_geometry import CONFIRMATION_SCALE
  current = state(slc_wheel_confirmation_available=available)
  sign = SpeedLimitWidget(fonts(profile), profile)
  sign.render_confirmation(current)
  calls = drawing['wheel_key'].call_args_list
  assert [call.args[0] for call in calls] == ([True, False] if available else [])
  if available:
    bounds = speed_limit_bounds(profile, current)
    assert bounds is not None
    assert all(call.args[2] == pytest.approx(bounds[1] + 135.5 * CONFIRMATION_SCALE[profile]) for call in calls)
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
  for call in font.draw.call_args_list:
    text, role, size, x, _, _ = call.args
    if reason in ('equal_speed', 'unknown_speed'):
      continue
    left, right = font.horizontal_ink(text, role, size)
    from openpilot.starpilot.ui.large_speed_geometry import LIMIT_RECTS, LARGE_SIGN_SCALE
    center = LIMIT_RECTS[profile][0] + 59 * (1 if profile == Profile.COMPACT else LARGE_SIGN_SCALE)
    assert abs(x + (left + right) / 2 - center) <= .5


@pytest.mark.parametrize('profile', list(Profile))
def test_only_set_speed_override_draws_cross(profile, drawing):
  sign = SpeedLimitWidget(fonts(profile), profile)
  current = state()
  current = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None))
  sign.render(replace(current, longitudinal_overridden=True))
  assert drawing['draw_line_ex'].call_count == 0
  sign.render(replace(current, speed_limit=replace(current.speed_limit, driver_override_active=True)))
  assert len(foreground_calls(drawing['draw_line_ex'])) == 2


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('vienna', [False, True])
@pytest.mark.parametrize('latest_received', [False, True])
@pytest.mark.parametrize('offset', [False, True])
@pytest.mark.parametrize('source,heading', [('map', 'OSM'), ('vision', 'VISION'), ('dashboard', 'DASH'), ('mapbox', 'NAV')])
def test_retained_limits_keep_original_source_and_have_no_actions(profile, vienna, latest_received, offset, source, heading, drawing):
  current = state(show_slc_offset=offset)
  observation = replace(current.speed_limit, kind=ObservationKind.STALE, source='none', speed_limit_mps=None,
    pending_speed_limit_mps=None, last_received_speed_limit_mps=50/3.6 if latest_received else None,
    last_received_source=source, accepted_source=source,
    last_received_adjusted_limit_mps=55/3.6 if latest_received else None, effective_cluster_target_mps=45/3.6)
  current = replace(current, speed_limit=observation, appearance=replace(current.appearance, use_vienna_sign=vienna))
  font = fonts(profile)
  render_sign(SpeedLimitWidget(font, profile), current)
  texts = [call.args[0] for call in font.draw.call_args_list]
  assert texts.count(heading) == 1
  assert ('50' if latest_received else '40') in texts
  assert 'LAST' not in texts
  drawing['arrow'].assert_not_called()
  assert slc_controls(profile, current) == ()


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


def test_compact_max_restores_dinish_number_label_and_shadow(drawing):
  from openpilot.starpilot.ui.presentation import FontRole, font_filename
  font = fonts(Profile.COMPACT)
  hud = CompactHudRenderer(font, Mock())
  current = state(drive_frame=1, cruise_kph=80)
  current = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None))
  hud.render_max_speed(current)
  hud.render_max_speed(replace(current, observed_ns=current.observed_ns + 20_000_000))
  assert [(call.args[0], call.args[1]) for call in font.draw.call_args_list] == [
    ('80', FontRole.SET_SPEED), ('MAX', FontRole.SEMI_BOLD)]
  assert font_filename(Profile.COMPACT, FontRole.SET_SPEED) == 'DINish-Speed.fnt'
  assert font.draw.call_args_list[0].args[3:5] == (17, -4)
  assert font.draw.call_args_list[1].args[2:5] == (36, 25, 109)
  drawing['draw_circle_gradient'].assert_called_once()


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


def test_large_limit_keeps_a_unit_when_set_speed_is_hidden():
  profile = Profile.LARGE
  current = state()
  current = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None))
  current = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None))
  current.customization['showMaxSpeed'] = False
  current.customization['layouts']['compact']['max_speed']['enabled'] = False
  font = fonts(profile)
  UnifiedSpeedWidget(font).render(rl.Rectangle(30, 30, 1800, 1020), current)
  assert [call.args[0] for call in foreground_calls(font.draw)].count('km/h') == 1


@pytest.mark.parametrize('profile', list(Profile))
def test_chooser_has_separate_tonal_actions_and_immediate_pressed_feedback(profile, drawing):
  view = OnroadView.__new__(OnroadView)
  view.personality = Mock()
  view.fonts = fonts(profile)
  if profile == Profile.LARGE:
    view.unified_speed = UnifiedSpeedWidget(view.fonts)
  else:
    view.compact_hud = CompactHudRenderer(view.fonts, Mock())
  current = state(slc_prompt_open=True)
  view._slc_actions(current)
  assert [call.args[0] for call in foreground_calls(view.fonts.draw)] == ['55', 'km/h', 'OSM 50 +5', 'Accept', 'Reject']
  backgrounds = [rgba_tuple(call.args[-1]) for call in drawing['draw_rectangle_rounded'].call_args_list]
  assert backgrounds[1] == (35, 74, 57, 255)
  assert backgrounds[3] == (77, 46, 57, 255)
  bounds = speed_limit_bounds(profile, current)
  drawing['confirmation'].assert_called_once_with(bounds, opened=True)
  assert bounds is not None
  x, y, right, bottom = bounds
  panel = rl.Rectangle(x, y, right - x, bottom - y)
  accept, _, reject, _ = [call.args[0] for call in drawing['draw_rectangle_rounded'].call_args_list]
  margin = accept.x - panel.x
  assert margin > 0
  assert reject.x - accept.x - accept.width == pytest.approx(margin, abs=.001)
  assert panel.x + panel.width - reject.x - reject.width == pytest.approx(margin, abs=.001)
  assert panel.y + panel.height - accept.y - accept.height == pytest.approx(margin, abs=.001)
  assert accept.y == reject.y and accept.height == reject.height
  drawing['draw_line_ex'].assert_not_called()
  view.pressed_action = lambda: slc_controls(profile, current)[0].request
  drawing['draw_rectangle_rounded'].reset_mock()
  view._slc_actions(current)
  pressed = [rgba_tuple(call.args[-1]) for call in drawing['draw_rectangle_rounded'].call_args_list]
  assert pressed[1] != backgrounds[1] and pressed[3] == backgrounds[3]


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('alpha', [96, 255])
def test_text_contour_is_bounded_and_preserves_foreground_opacity(profile, alpha):
  from openpilot.starpilot.ui.presentation import FontRole
  font = fonts(profile)
  color = rl.Color(188, 132, 255, alpha)
  draw_text(font, '65 +5', FontRole.MEDIUM, 36, 100, 50, color)
  calls = [call.args for call in font.draw.call_args_list]
  assert len(calls) == 6
  assert all(call[:3] == ('65 +5', FontRole.MEDIUM, 36) for call in calls)
  assert calls[-1][3:5] == (100, 50) and calls[-1][-1] is color
  radius = 1.15 if profile == Profile.LARGE else 1.
  assert {(round(call[3]-100, 2), round(call[4]-50, 2)) for call in calls[:4]} == {
    (-radius, 0), (radius, 0), (0, -radius), (0, radius)}
  assert all(call[-1].a == round(alpha*.90) for call in calls[:4])
  assert calls[-2][3:5] == (100, 50+(1.5 if profile == Profile.LARGE else 1.2))
  assert calls[-2][-1].a == round(alpha*.32)


def test_full_coverage_contour_protects_base_muted_and_peak_purple_on_white():
  from openpilot.starpilot.ui.presentation import FontRole
  def luminance(channels):
    linear = [((x/255+.055)/1.055)**2.4 if x/255 > .04045 else x/255/12.92 for x in channels]
    return sum(x*weight for x, weight in zip(linear, (.2126, .7152, .0722), strict=True))
  for color in (INK, MUTED, rl.Color(188, 132, 255, 255), rl.Color(170, 115, 235, 255)):
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


def test_retained_compact_fade_still_validates_before_decoding(tmp_path, monkeypatch):
  from openpilot.starpilot.ui.onroad import _load_fade
  directory = Path(__file__).parents[3] / 'selfdrive/assets'
  path = directory / 'icons_mici/onroad/onroad_fade.png'
  decoder = Mock(return_value=SimpleNamespace(id=1))
  monkeypatch.setattr(rl, 'load_texture', decoder)
  assert _load_fade(path) is decoder.return_value
  decoder.reset_mock()
  invalid = tmp_path / path.name
  invalid.write_bytes(b'invalid PNG')
  with pytest.raises(ValueError, match='Unreviewed'):
    _load_fade(invalid)
  decoder.assert_not_called()
