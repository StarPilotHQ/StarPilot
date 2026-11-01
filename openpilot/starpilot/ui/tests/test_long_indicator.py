from dataclasses import replace
from types import SimpleNamespace as NS
from typing import cast
from unittest.mock import Mock

import pyray as rl
import pytest

from openpilot.selfdrive.ui.mici.onroad import long_indicator as native
from openpilot.starpilot.ui import onroad_personality as indicator
from openpilot.starpilot.ui.onroad_customization import default_document
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert, OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile
from openpilot.starpilot.ui.traffic_status import TrafficDisplay


def road(**changes):
  return replace(OnroadState(True, False, 15, 80, SpeedLimitObservation(),
                             longitudinal_active=True, lead_detected=True), **changes)


@pytest.fixture
def display(monkeypatch):
  monkeypatch.setattr(indicator.gui_app, 'texture', lambda path: NS(path=path, width=200, height=200))
  monkeypatch.setattr(rl, 'get_time', lambda: 1.)
  fonts = cast(BitmapFonts, NS(profile=Profile.COMPACT, measure=Mock(return_value=NS(width=20)), draw=Mock()))
  widget = indicator.PersonalityWidget(fonts)
  widget._draw_centered = Mock()
  return widget


def settle(widget, state):
  for _ in range(90):
    widget.render(state)
  return widget._draw_centered.call_args_list[-8:]


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('personality', range(3))
def test_same_selected_bars_and_lead_on_both_profiles(display, profile, personality):
  display.fonts.profile = profile
  draws = settle(display, road(personality=personality))
  assert draws[1].args[3] > .999
  assert all(draws[2 + 2*i].args[3] == pytest.approx(.9 if i <= personality else .35, abs=.001) for i in range(3))
  for has_lead, active, white, green in ((False, True, .35, 0), (True, False, 1, 0), (True, True, 0, 1)):
    draws = settle(display, road(lead_detected=has_lead, longitudinal_active=active))
    assert draws[0].args[3] == pytest.approx(white, abs=.001)
    assert draws[1].args[3] == pytest.approx(green, abs=.001)


@pytest.mark.parametrize('traffic', (False, True))
def test_art_scales_uniformly_without_clipping_or_downsized_source_textures(display, monkeypatch, traffic):
  def draw_all_art_opaque(art, rect, y, alpha, *args, **kwargs):
    return indicator.PersonalityWidget._draw_centered(art, rect, y, 1., *args, **kwargs)
  display._draw_centered = draw_all_art_opaque
  draw = Mock()
  monkeypatch.setattr(rl, 'draw_texture_pro', draw)
  state = road(traffic_mode=traffic, traffic_display=TrafficDisplay('active', 'TRAFFIC') if traffic else None)
  bounds = []
  for profile in (Profile.COMPACT, Profile.LARGE):
    display.fonts.profile = profile
    draw.reset_mock()
    display.render(state)
    x, y, width, height = (476, 160, 60, 80) if profile == Profile.COMPACT else (88, 804, 150, 200)
    destinations = [call.args[2] for call in draw.call_args_list]
    assert len(destinations) == 8
    assert all(x <= r.x < r.x + r.width <= x + width and y <= r.y < r.y + r.height <= y + height for r in destinations)
    bounds.append([(r.x-x, r.y-y, r.width, r.height) for r in destinations])
    assert all(call.args[1].width == 200 for call in draw.call_args_list)
  for compact, large in zip(*bounds, strict=True):
    assert large == pytest.approx(tuple(value * 2.5 for value in compact), abs=.0001)


def test_selection_highlight_waits_for_alert_and_then_expires(display, monkeypatch):
  settle(display, road(personality=1))
  monkeypatch.setattr(rl, 'get_time', lambda: 1.1)
  assert settle(display, road(personality=2))[7].args[3] > .999
  monkeypatch.setattr(rl, 'get_time', lambda: 2.)
  settle(display, road(personality=2, alert=OnroadAlert(AlertSize.MID)))
  monkeypatch.setattr(rl, 'get_time', lambda: 4.)
  assert settle(display, road(personality=2))[7].args[3] > .999
  monkeypatch.setattr(rl, 'get_time', lambda: 4.6)
  assert settle(display, road(personality=2))[7].args[3] < .001


def test_gas_override_blinks_only_selected_bars_and_clears_on_disengagement(display, monkeypatch):
  monkeypatch.setattr(rl, 'get_time', lambda: 1.1)
  state = road(personality=1, gas_pressed_override=True)
  draws = settle(display, state)
  assert draws[2].args[3] == pytest.approx(.35, abs=.001)
  assert draws[4].args[3] == pytest.approx(.35, abs=.001)
  assert draws[6].args[3] == pytest.approx(.35, abs=.001)
  display.render(replace(state, longitudinal_active=False))
  draws = display._draw_centered.call_args_list[-8:]
  assert draws[1].args[3] == 0
  assert draws[2].args[3] == pytest.approx(.9, abs=.001)
  monkeypatch.setattr(rl, 'get_time', lambda: 1.2)
  assert settle(display, state)[2].args[3] == pytest.approx(.9, abs=.001)


@pytest.mark.parametrize('profile', list(Profile))
def test_traffic_accent_is_stable_without_labels_and_restores_personality(display, profile):
  display.fonts.profile = profile
  state = road(personality=2, traffic_mode=True, traffic_display=TrafficDisplay('active', 'TRAFFIC'))
  draws = settle(display, state)
  assert draws[2].args[4] == (200, 32, 48)
  assert draws[4].args[3] == pytest.approx(.35, abs=.001)
  assert all(draws[i].args[3] < .001 for i in (3, 5, 7))
  original_rect = draws[2].args[1]
  for status in ('paused', 'unavailable_source', 'unavailable_profile', None):
    draws = settle(display, replace(state, traffic_mode=False, traffic_selected=True,
                                    gas_pressed_override=True,
                                    traffic_display=TrafficDisplay(status, '') if status else None))
    assert draws[2].args[4] == (200, 32, 48)
    assert draws[2].args[3] == pytest.approx(.9, abs=.001)
    rect = draws[2].args[1]
    assert (rect.x, rect.y, rect.width, rect.height) == (original_rect.x, original_rect.y, original_rect.width, original_rect.height)
  draws = settle(display, replace(state, traffic_mode=False, traffic_selected=False, traffic_display=TrafficDisplay('off', 'OFF')))
  assert all(draws[i].args[3] == pytest.approx(.9, abs=.001) for i in (2, 4, 6))
  display.fonts.draw.assert_not_called()


def test_large_default_sits_left_of_driver_monitor_and_custom_positions_stay_put(display):
  display.fonts.profile = Profile.LARGE
  state = road(right_hand_drive=True)
  display.render(state)
  assert display._draw_centered.call_args.args[1].x == 1280
  document = default_document()
  document['layouts']['large']['following_distance'].update(x=600, y=300)
  display.render(replace(state, customization=document))
  rect = display._draw_centered.call_args.args[1]
  assert (rect.x, rect.y) == (600, 300)


@pytest.mark.parametrize('profile', list(Profile))
def test_hidden_widget_and_driver_camera_do_not_paint(display, profile):
  from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
  display.fonts.profile = profile
  document = default_document()
  document['layouts'][profile]['following_distance']['enabled'] = False
  display.render(road(customization=document))
  state = road()
  display.render(replace(state, appearance=replace(state.appearance, camera_view=CameraViewChoice.DRIVER)))
  display._draw_centered.assert_not_called()


@pytest.mark.parametrize('missing', ['longitudinal', 'engagement', 'drive'])
def test_native_recovery_indicator_keeps_its_original_availability_guard(monkeypatch, missing):
  class Messages:
    recv_frame = {'selfdriveState': 12}
    def __getitem__(self, key):
      return NS(enabled=missing != 'engagement')
  sm = Messages()
  ui = NS(sm=sm, started_frame=13 if missing == 'drive' else 10, has_longitudinal_control=missing != 'longitudinal')
  monkeypatch.setattr(native, 'ui_state', ui)
  widget = native.LongIndicator()
  widget.indicator = Mock()
  widget._render(rl.Rectangle(0, 0, 476, 240))
  widget.indicator.draw.assert_not_called()
  widget.indicator.reset.assert_called_once()


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('ordered', (False, True))
def test_shared_widget_is_in_both_layer_orders(display, profile, ordered):
  from openpilot.starpilot.ui.onroad import OnroadView
  from openpilot.starpilot.ui.onroad_compact_widgets import MiciSidebarWidgets
  from openpilot.starpilot.ui.onroad_customization import DEFAULT_WIDGET_ORDER
  display.fonts.profile = profile
  view = OnroadView.__new__(OnroadView)
  view.fonts, view.personality, view.projection_viewport = display.fonts, display, None
  view.compact_sidebar = MiciSidebarWidgets(display.fonts)
  view.compact_sidebar.personality = display
  document = default_document()
  if ordered:
    document['widgetOrder'] = {profile: list(reversed(DEFAULT_WIDGET_ORDER[profile]))}
  view._ordered_widgets(rl.Rectangle(0, 0, *profile.size), road(customization=document), only_widget='following_distance')
  assert display._draw_centered.call_count == 8


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('moved', (False, True))
def test_routine_lane_guidance_keeps_widget_and_urgent_alerts_hide_it(display, profile, moved):
  display.fonts.profile = profile
  document = default_document()
  if moved:
    document['layouts'][profile]['following_distance'].update(x=260, y=20)
  for kind, user_prompt, visible in (('preLaneChangeLeft', False, True), ('laneChange', False, True), ('laneChangeBlocked', True, False)):
    display._draw_centered.reset_mock()
    settle(display, road(customization=document, alert=OnroadAlert(AlertSize.SMALL, alert_type=kind + '/warning', user_prompt=user_prompt)))
    draws = display._draw_centered.call_args_list
    if profile == Profile.COMPACT and moved and not visible:
      assert not draws
    else:
      assert (draws[-7].args[3] > .99) if visible else (draws[-7].args[3] < .001)


@pytest.mark.parametrize(('alpha', 'visible_alpha'), ((0., None), (.0001, None), (.0019, None),
                                                       (.002, 1), (1/255, 1), (.5, 128), (1., 255)))
def test_invisible_art_avoids_gpu_submission_without_losing_faint_visible_art(display, monkeypatch, alpha, visible_alpha):
  display._prepare()
  draw = Mock()
  monkeypatch.setattr(rl, 'draw_texture_pro', draw)
  indicator.PersonalityWidget._draw_centered(display._lead_car[0], rl.Rectangle(476, 160, 60, 80), 100, alpha)
  if visible_alpha is None:
    draw.assert_not_called()
  else:
    draw.assert_called_once()
    assert draw.call_args.args[-1].a == visible_alpha
