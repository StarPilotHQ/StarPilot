from dataclasses import replace
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.ui.large_speed_geometry import LARGE_SIGN_SCALE
from openpilot.starpilot.ui.onroad_state import speed_limit_bounds, slc_controls, SpeedLimitObservation, ObservationKind
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.speed_limit_widget import SpeedLimitWidget
from openpilot.starpilot.ui.tests.test_unified_speed_native import fonts, state as pending_state
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
def test_both_sign_styles_keep_their_height_with_or_without_offset(profile, offset, vienna, historical_outline):
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
    assert widget.shapes.outline.call_args.args[:2] == (bounds[0]+12+59*scale, bounds[1]+12+66*scale)
    assert widget.shapes.outline.call_args.kwargs == {}
  assert ('+5' in [call.args[0] for call in widget.fonts.draw.call_args_list]) == offset


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
  assert bounds[3] - bounds[1] == 152
  texts = [call.args[0] for call in widget.fonts.draw.call_args_list]
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
    assert bounds[3] - bounds[1] == 152


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
  assert bounds is not None and bounds[3] - bounds[1] == 152
  historical_outline.assert_not_called()
  widget.shapes.outline.assert_called_once()
