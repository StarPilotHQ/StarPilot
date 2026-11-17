from contextlib import ExitStack
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.ui.conditional_status import ConditionalDisplay
from openpilot.starpilot.ui.onroad import OnroadView, axis_status_color
from openpilot.starpilot.ui.onroad_cem_gauge import AetherGauge, IndicatorType
from openpilot.starpilot.ui.onroad_compact_widgets import MiciSidebarWidgets
from openpilot.starpilot.ui.onroad_conditional import stop_active
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui import onroad
from openpilot.starpilot.ui.presentation import Profile


def aol_light():
  return OnroadState(True, False, 10., 50., SpeedLimitObservation(), lateral_active=True,
                     conditional_configured=ModeChoice.CEM,
                     conditional_perception=ConditionalDisplay(ModeChoice.CEM, False, 'cem_stop', 8, 'planner', 1))


def test_compact_aol_light_is_informational_without_orange_border():
  state = aol_light()
  widget = MiciSidebarWidgets(Mock())
  with patch.object(widget, '_stop_icon') as light, patch.object(widget, '_chill_icon') as moon:
    widget._conditional(rl.Rectangle(476, 80, 60, 80), state)
  light.assert_called_once()
  moon.assert_not_called()
  assert not stop_active(state)
  assert (axis_status_color(state).r, axis_status_color(state).g, axis_status_color(state).b) == (10, 186, 181)


def test_large_aol_light_uses_same_perception_without_control_authority():
  view = OnroadView.__new__(OnroadView)
  view.personality = Mock()
  view.fonts = Mock(profile=Profile.LARGE)
  view.fonts.measure.return_value = NS(width=100, height=30)
  view.cem_gauge = AetherGauge(view.fonts)
  view.navigation_favorites = onroad.NavigationFavorites(view.fonts)
  view.camera_layer = view.background_layer = view.extra_overlays = None
  view.projection_viewport = None
  view._corner_cache = onroad.CornerHintCache()
  view.unified_speed = Mock()
  view.unified_speed.bounds.return_value = rl.Rectangle(88, 75, 176, 196)
  view.current_speed = view.steering_wheel = view.torque_bar = Mock()
  view.navigation = view.alert = Mock()
  with ExitStack() as stack:
    for name in dir(rl):
      if name.startswith('draw_'):
        stack.enter_context(patch.object(rl, name))
    stack.enter_context(patch.object(onroad.clip, 'begin_scissor_mode'))
    stack.enter_context(patch.object(onroad.clip, 'end_scissor_mode'))
    stack.enter_context(patch.object(onroad, 'render_corner_hint'))
    stack.enter_context(patch.object(onroad, 'render_glow'))
    light = stack.enter_context(patch.object(view.cem_gauge, '_render_unified_road'))
    state = aol_light()
    view._large(state)
  light.assert_called_once()
  assert light.call_args.args[3].indicator_type is IndicatorType.STOP_LIGHT
  assert not state.longitudinal_active and not stop_active(state)
  assert (axis_status_color(state).r, axis_status_color(state).g, axis_status_color(state).b) == (10, 186, 181)
