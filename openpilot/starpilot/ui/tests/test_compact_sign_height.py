from dataclasses import replace
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.ui.large_speed_geometry import LARGE_SIGN_SCALE, SIGN_WIDTH, SIGN_HEIGHT
from openpilot.starpilot.ui.onroad_state import speed_limit_bounds, slc_controls, SpeedLimitObservation, ObservationKind
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.speed_limit_widget import SpeedLimitWidget
from openpilot.starpilot.ui.tests.test_unified_speed_native import foreground_calls, fonts, state as pending_state
from openpilot.starpilot.ui.unified_speed_presentation import resolve_unified_speed


def state(**changes):
  current = pending_state(**changes)
  return replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None))


@pytest.fixture(autouse=True)
def historical_outline():
  with patch.object(rl, 'draw_rectangle_rounded_lines_ex') as outline:
    yield outline


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('offset', [False, True])
@pytest.mark.parametrize('vienna', [False, True])
def test_sign_layout_and_tap_area_stay_fixed_with_or_without_offset(profile, offset, vienna, historical_outline):
  current = state(show_slc_offset=offset)
  current = replace(current, appearance=replace(current.appearance, use_vienna_sign=vienna))
  widget = SpeedLimitWidget(fonts(profile), profile)
  widget.shapes = Mock()
  with patch('openpilot.starpilot.ui.speed_limit_widget.rl.draw_rectangle_rounded') as fill:
    widget.render(current)
  fill.assert_not_called()
  scale = LARGE_SIGN_SCALE if profile == Profile.LARGE else 1
  bounds = speed_limit_bounds(profile, current)
  tall = speed_limit_bounds(profile, replace(current, show_slc_offset=True))
  assert bounds is not None and tall is not None
  assert bounds == tall
  if vienna:
    widget.shapes.outline.assert_not_called()
    widget.shapes.circle.assert_called_once()
    historical_outline.assert_not_called()
  else:
    historical_outline.assert_not_called()
    widget.shapes.outline.assert_called_once()
    sign_scale = (160 if offset else 150) / SIGN_HEIGHT
    assert widget.shapes.outline.call_args.args[:2] == (bounds[0]+12+SIGN_WIDTH/2*scale,
                                                     bounds[1]+12+(SIGN_HEIGHT*sign_scale/2-9)*scale)
    assert widget.shapes.outline.call_args.kwargs == {'scale': scale * sign_scale, 'width_scale': .9}
  assert ('+5' in [call.args[0] for call in foreground_calls(widget.fonts.draw)]) == offset


@pytest.mark.parametrize('value', (65, 120))
@pytest.mark.parametrize('metric', (False, True))
@pytest.mark.parametrize('show', (False, True))
@pytest.mark.parametrize('offset', (None, 0., .01, -.01, 5., -10.))
def test_small_sign_keeps_its_footprint_for_every_offset(value, metric, show, offset, historical_outline):
  factor = 3.6 if metric else 2.2369362921
  current = state(metric=metric, show_slc_offset=show)
  current = replace(current, appearance=replace(current.appearance, show_speed_limit_sign=True, use_vienna_sign=False),
    speed_limit=SpeedLimitObservation(ObservationKind.VALID, 'vision', value / factor,
    offset_mps=None if offset is None else offset / factor, action_enabled=False))
  shown = resolve_unified_speed(current)
  widget = SpeedLimitWidget(fonts(Profile.COMPACT), Profile.COMPACT)
  widget.shapes = Mock()
  widget.render(current)
  bounds = speed_limit_bounds(Profile.COMPACT, current)
  assert bounds is not None
  historical_outline.assert_not_called()
  widget.shapes.outline.assert_called_once()
  assert bounds[3] - bounds[1] == SIGN_HEIGHT + 20
  texts = [call.args[0] for call in foreground_calls(widget.fonts.draw)]
  assert texts == ['VISION', str(value)] + ([shown.offset_text] if shown.offset_text else [])


def test_small_offset_layout_transitions_reuse_the_same_widget(historical_outline):
  widget = SpeedLimitWidget(fonts(Profile.COMPACT), Profile.COMPACT)
  widget.shapes = Mock()
  for enabled in (False, True, False):
    current = state(show_slc_offset=enabled)
    widget.render(current)
    historical_outline.assert_not_called()
    bounds = speed_limit_bounds(Profile.COMPACT, current)
    assert bounds is not None
    assert bounds[3] - bounds[1] == SIGN_HEIGHT + 20


@pytest.mark.parametrize('y', [20, 50, 108])
def test_attached_compact_chooser_never_hides_its_own_sign(y):
  current = pending_state(slc_prompt_open=True)
  current.customization['layouts']['compact']['speed_limit'].update(x=280 if y == 108 else 330, y=y)
  assert speed_limit_bounds(Profile.COMPACT, current) is not None
  assert slc_controls(Profile.COMPACT, current)


def test_small_rectangular_no_offset_regression(historical_outline):
  current = state(show_slc_offset=False)
  widget = SpeedLimitWidget(fonts(Profile.COMPACT), Profile.COMPACT)
  widget.shapes = Mock()
  widget.render(current)
  bounds = speed_limit_bounds(Profile.COMPACT, current)
  assert bounds is not None and bounds[3] - bounds[1] == SIGN_HEIGHT + 20
  historical_outline.assert_not_called()
  widget.shapes.outline.assert_called_once()


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('metric', (False, True))
@pytest.mark.parametrize('posted_mps', (1 / 3.6, 20., 40., 160 / 3.6))
@pytest.mark.parametrize('configured_offset', (0, 4, -4))
def test_only_the_displayed_ranges_configured_offset_expands_the_sign(profile, metric, posted_mps, configured_offset):
  from openpilot.starpilot.speed_limits.speed_domain import OffsetBand, OffsetSchedule, adjusted_limit
  factor = 3.6 if metric else 2.2369362921
  bands = ((0., 10.), (10., 40.), (40., None))
  schedule = OffsetSchedule(tuple(OffsetBand(lower, upper,
    (configured_offset if lower <= posted_mps and (upper is None or posted_mps < upper) else 7) / factor)
    for lower, upper in bands))
  target = adjusted_limit(posted_mps, schedule)
  assert target is not None
  current = state(metric=metric, speed_mps=5., slc_offset_schedule=schedule)
  current = replace(current, speed_limit=SpeedLimitObservation(ObservationKind.VALID, 'vision', posted_mps,
    offset_mps=target - posted_mps, presentation_adjusted_limit_mps=target, accepted_speed_limit_mps=15.))
  shown = resolve_unified_speed(current)
  assert shown.offset_configured == (configured_offset != 0)
  assert shown.limit_text == str(round(target * factor))
  if configured_offset == 0 and posted_mps in (1 / 3.6, 160 / 3.6):
    assert shown.offset_text is not None  # The effective adjustment is clamping, not a configured offset.
  widget = SpeedLimitWidget(fonts(profile), profile)
  widget.shapes = Mock()
  widget.render(current)
  visible_offset = configured_offset != 0 and shown.offset_text is not None
  assert widget.shapes.outline.call_args.kwargs['scale'] == pytest.approx(widget.scale * (160 if visible_offset else 150) / SIGN_HEIGHT)
  assert [call.args[0] for call in foreground_calls(widget.fonts.draw)] == (
    ['VISION', shown.posted_text] + ([shown.offset_text] if visible_offset else []))


@pytest.mark.parametrize('profile', list(Profile))
def test_mutcd_preserves_text_sizes_and_fits_both_sizes_with_real_fonts(profile):
  from itertools import product
  from openpilot.starpilot.speed_limits.speed_domain import OffsetBand, OffsetSchedule
  from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, default_font_directory
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.init_window(128, 128, 'MUTCD spacing')
  try:
    with BitmapFonts(profile, default_font_directory()) as font:
      widget = SpeedLimitWidget(font, profile)
      for value, adjustment, stage in product((65, 120, 199), (0, 4, 10, -10), ('live', 'pending', 'retained')):
        current = state(metric=True, slc_offset_schedule=OffsetSchedule((OffsetBand(0., None, adjustment / 3.6),)))
        current = replace(current, speed_mps=(value + adjustment + 2) / 3.6,
          speed_limit=SpeedLimitObservation(ObservationKind.VALID, 'vision', value / 3.6,
            offset_mps=adjustment / 3.6, action_enabled=False,
            pending_speed_limit_mps=value / 3.6 if stage == 'pending' else None))
        if stage == 'retained':
          current = replace(current, speed_limit=replace(current.speed_limit, kind=ObservationKind.STALE, speed_limit_mps=None,
            last_received_speed_limit_mps=value / 3.6, last_received_adjusted_limit_mps=(value + adjustment) / 3.6,
            last_received_source='vision', session_id='drive'))
        with patch.object(font, 'draw') as drawn, patch.object(widget.shapes, 'outline') as outline:
          widget.render(current)
        scale = widget.scale * (160 if adjustment else 150) / SIGN_HEIGHT
        cx, cy = outline.call_args.args[:2]
        half_width, half_height = (SIGN_WIDTH / 2 - 4) * .9 * scale, (SIGN_HEIGHT / 2 - 4) * scale
        boxes = []
        for call in foreground_calls(drawn):
          text, role, size, x, y, _ = call.args
          left, right = font.horizontal_ink(text, role, size)
          top, bottom = font.vertical_ink(text, role, size)
          if text.startswith('LAST '):
            assert right - left == pytest.approx(108 * widget.scale)
          else:
            assert size == pytest.approx({FontRole.SEMI_BOLD: 26, FontRole.SET_SPEED: 70, FontRole.MEDIUM: 27}[role] * widget.text_scale)
          boxes.append((x + left, y + top, x + right, y + bottom))
        assert cx - half_width + 1 <= min(box[0] for box in boxes)
        assert max(box[2] for box in boxes) <= cx + half_width - 1
        assert cy - half_height + 1 <= boxes[0][1]
        assert boxes[-1][3] <= cy + half_height - 1
        assert all(a[3] + 4 * widget.scale <= b[1] for a, b in zip(boxes, boxes[1:], strict=False))
        bounds = speed_limit_bounds(profile, current)
        outer_width, outer_height = (SIGN_WIDTH / 2 - 2) * .9 * scale + scale, (SIGN_HEIGHT / 2 - 2) * scale + scale
        assert bounds[0] <= cx - outer_width and cx + outer_width <= bounds[2]
        assert bounds[1] <= cy - outer_height and cy + outer_height <= bounds[3]
      widget.close()
  finally:
    if owns_context:
      rl.close_window()
