from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.ui import onroad_alerts
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert
from openpilot.starpilot.ui.presentation import FontRole, Profile


@pytest.mark.parametrize('width', [1860, 2160])
@pytest.mark.parametrize('text', ['Reverse\nGear', 'Reverse Gear'])
def test_large_reverse_alert_centers_each_line_in_camera_view(width, text):
  fonts = Mock(profile=Profile.LARGE)
  def measure(text, role, size, **kwargs):
    lines = text.split('\n')
    return NS(width=max(len(line) for line in lines) * size * .5,
              height=len(lines) * size + (len(lines) - 1) * 2)
  fonts.measure.side_effect = measure
  renderer = onroad_alerts.AlertRenderer(fonts)
  bounds = rl.Rectangle(30, 30, width - 60, 1020)
  with patch.object(rl, 'draw_rectangle'), patch.object(rl, 'draw_rectangle_gradient_v'):
    renderer.render(bounds, OnroadAlert(AlertSize.FULL, text, alert_type='reverseGear/permanent'))
  title = [call.args for call in fonts.draw.call_args_list if call.args[0]]
  assert [args[0] for args in title] == text.split('\n')
  for line, role, size, x, _y, _color in title:
    assert x + measure(line, role, size).width / 2 == width / 2
  top = title[0][4]
  bottom = title[-1][4] + measure(title[-1][0], title[-1][1], title[-1][2]).height
  assert (top + bottom) / 2 == bounds.y + bounds.height / 2
  if len(title) > 1:
    assert title[1][4] - top == title[0][2] + 2


@pytest.mark.parametrize("personality", ["Relaxed", "Standard", "Aggressive"])
def test_personality_banner_uses_name_and_consistent_large_type(personality):
  fonts = Mock(profile=Profile.COMPACT)
  fonts.measure.side_effect = lambda text, role, size, **kwargs: NS(width=len(text) * size * .48, height=size)
  renderer = onroad_alerts.AlertRenderer(fonts)
  with patch.object(rl, "draw_rectangle"), patch.object(rl, "draw_rectangle_gradient_v"):
    renderer.render(rl.Rectangle(0, 0, 476, 240),
                    OnroadAlert(AlertSize.SMALL, "Driving Personality: " + personality,
                                alert_type="personalityChanged/warning"))
  title, subtitle = fonts.draw.call_args_list
  assert title.args[:3] == (personality.lower(), FontRole.DISPLAY, 82)
  assert subtitle.args[:3] == ("driving personality", FontRole.ROMAN, 36)


def test_unrelated_alert_text_is_preserved():
  fonts = Mock(profile=Profile.COMPACT)
  fonts.measure.return_value = NS(width=100, height=30)
  renderer = onroad_alerts.AlertRenderer(fonts)
  with patch.object(rl, "draw_rectangle"), patch.object(rl, "draw_rectangle_gradient_v"):
    renderer.render(rl.Rectangle(0, 0, 476, 240),
                    OnroadAlert(AlertSize.FULL, "TAKE CONTROL", "Communication Issue", True))
  assert fonts.draw.call_args_list[0].args[0] == "take control"


@pytest.mark.parametrize('direction', [-1, 1])
@pytest.mark.parametrize('event', ['preLaneChangeLeft', 'preLaneChangeRight', 'laneChange', 'laneChangeBlocked'])
def test_guidance_uses_whole_native_alert_owner(direction, event):
  from openpilot.selfdrive.ui.mici.onroad.alert_renderer import Alert

  fonts = Mock(profile=Profile.COMPACT)
  renderer = onroad_alerts.AlertRenderer(fonts)
  owner = Mock()
  owner.render_alert.return_value = True
  bounds = rl.Rectangle(0, 0, 476, 240)
  notice = OnroadAlert(AlertSize.SMALL, 'Steer Left to Start Lane Change Once Safe',
                       alert_type=event + '/warning')
  with patch.object(onroad_alerts, 'NativeAlertRenderer', return_value=owner) as constructor:
    for _ in range(20):
      renderer.render(bounds, notice, signal_direction=direction)
    constructor.assert_called_once()
    owner.render_alert.assert_called_with(bounds, Alert(notice.text1, notice.text2, size=1, alert_type=notice.alert_type),
                                         signal_direction=direction)
    renderer.render(bounds, OnroadAlert(), signal_direction=direction)
    owner.render_alert.assert_called_with(bounds, None, signal_direction=direction)
    fonts.draw.assert_not_called()
    owner.render_alert.return_value = False
    renderer.render(bounds, OnroadAlert())
    assert not renderer._lane_visible


def test_urgent_notice_interrupts_lane_fade_immediately():
  fonts = Mock(profile=Profile.COMPACT)
  fonts.measure.return_value = NS(width=100, height=30)
  owner = Mock()
  renderer = onroad_alerts.AlertRenderer(fonts)
  with patch.object(onroad_alerts, 'NativeAlertRenderer', return_value=owner), \
       patch.object(rl, 'draw_rectangle'), patch.object(rl, 'draw_rectangle_gradient_v'):
    renderer.render(rl.Rectangle(0, 0, 476, 240),
                    OnroadAlert(AlertSize.SMALL, 'Steer Left', alert_type='preLaneChangeLeft/warning'))
    renderer.render(rl.Rectangle(0, 0, 476, 240), OnroadAlert(AlertSize.FULL, 'TAKE CONTROL', critical=True))
  assert not renderer._lane_visible
  assert owner._prev_alert is None
  assert fonts.draw.call_args.args[0] == 'take control'


def test_visible_pip_keeps_lane_text_without_the_full_width_banner():
  fonts = Mock(profile=Profile.LARGE)
  fonts.measure.return_value = NS(width=100, height=30)
  renderer = onroad_alerts.AlertRenderer(fonts)
  notice = OnroadAlert(AlertSize.SMALL, 'Steer Left', alert_type='preLaneChangeLeft/warning')
  renderer.text_only_alert_names = frozenset({'preLaneChangeLeft'})
  with patch.object(rl, 'draw_rectangle') as solid, patch.object(rl, 'draw_rectangle_gradient_v') as gradient:
    renderer.render(rl.Rectangle(0, 0, 1860, 1080), notice)
    solid.assert_not_called()
    gradient.assert_not_called()
    renderer.render(rl.Rectangle(0, 0, 1860, 1080), OnroadAlert())
    solid.assert_not_called()
    gradient.assert_not_called()
  labels = [call.args[0] for call in fonts.draw.call_args_list]
  assert len(labels) == 4 and set(labels) == {'Steer Left'}


@pytest.mark.parametrize('event,direction,icon', [
  ('preLaneChangeLeft', -1, 'turn_signal_left.png'),
  ('preLaneChangeRight', 1, 'turn_signal_left.png'),
  ('laneChange', 1, 'turn_signal_left.png'),
  ('laneChangeBlocked', -1, 'blind_spot_left.png'),
])
def test_native_guidance_icon_text_shadow_share_entrance_and_exit(event, direction, icon):
  from openpilot.selfdrive.ui.mici.onroad import alert_renderer as native

  labels = []
  def label(*args, **kwargs):
    item = Mock(rect=rl.Rectangle(0, 0, 0, 0))
    item.render.side_effect = lambda rect: setattr(item, 'rect', rect)
    item.get_content_height.return_value = 60
    labels.append(item)
    return item
  def texture(path, width, height, **kwargs):
    return NS(path=path, width=width, height=height, flipped=kwargs.get('flip_x', False))
  renderer = onroad_alerts.AlertRenderer(Mock(profile=Profile.COMPACT))
  bounds = rl.Rectangle(0, 0, 476, 240)
  notice = OnroadAlert(AlertSize.SMALL, 'Steer Left', 'Once Safe', alert_type=event + '/warning')
  with patch.object(native, 'UnifiedLabel', side_effect=label), patch.object(native.gui_app, 'texture', side_effect=texture), \
       patch.object(rl, 'draw_rectangle') as solid, patch.object(rl, 'draw_rectangle_gradient_v') as shadow, \
       patch.object(rl, 'draw_texture_ex') as draw:
    for frame in range(120):
      for item in labels:
        item.render.reset_mock()
      solid.reset_mock()
      shadow.reset_mock()
      draw.reset_mock()
      with patch.object(native.time, 'monotonic', return_value=10 + frame / 60):
        renderer.render(bounds, notice if frame < 60 else OnroadAlert(), signal_direction=direction)
      if draw.called:
        assert draw.call_args.args[0].path.endswith(icon)
        assert draw.call_args.args[0].flipped is (direction > 0)
        assert labels[0].set_text.call_args.args == ('steer left',)
        assert labels[1].set_text.call_args.args == ('once safe',)
        labels[0].render.assert_called_once()
        labels[1].render.assert_called_once()
        solid.assert_called_once()
        shadow.assert_called_once()
        assert draw.call_args.args[1].x == ((8 if icon.startswith('blind') else 2) if direction < 0 else 370)
      else:
        assert frame > 60
        labels[0].render.assert_not_called()
        labels[1].render.assert_not_called()
        shadow.assert_not_called()
    assert not renderer._lane_visible


def test_temporary_eps_uses_native_warning_color_text_and_visual():
  from openpilot.selfdrive.ui.mici.onroad.alert_renderer import Alert
  fonts = Mock(profile=Profile.COMPACT)
  renderer = onroad_alerts.AlertRenderer(fonts)
  owner = Mock()
  bounds = rl.Rectangle(0, 0, 476, 240)
  alert = OnroadAlert(AlertSize.SMALL, 'Steering Assist Temporarily Unavailable',
                       alert_type='steerTempUnavailableSilent/warning', user_prompt=True, visual_alert=1)
  with patch.object(onroad_alerts, 'NativeAlertRenderer', return_value=owner):
    renderer.render(bounds, alert)
  owner.render_alert.assert_called_once_with(bounds, Alert(alert.text1, '', size=1, status=1,
                                                          visual_alert=1, alert_type=alert.alert_type), signal_direction=0)
  fonts.draw.assert_not_called()


@pytest.mark.parametrize('fps', [15, 20, 30, 60])
@pytest.mark.parametrize('direction', [-1, 1])
def test_native_guidance_pulse_keeps_original_timing_when_frames_drop(fps, direction):
  from openpilot.selfdrive.ui.mici.onroad import alert_renderer as native
  from openpilot.starpilot.ui.onroad_lane_alerts import lateral_lane_alert

  labels = []
  def label(*args, **kwargs):
    item = Mock(rect=rl.Rectangle(0, 0, 0, 0))
    item.render.side_effect = lambda rect: setattr(item, 'rect', rect)
    item.get_content_height.return_value = 60
    labels.append(item)
    return item
  def texture(path, width, height, **kwargs):
    return NS(path=path, width=width, height=height, flipped=kwargs.get('flip_x', False))

  renderer = onroad_alerts.AlertRenderer(Mock(profile=Profile.COMPACT))
  bounds = rl.Rectangle(0, 0, 476, 240)
  sources = {'car': NS(canValid=True, canTimeout=False, leftBlindspot=False, rightBlindspot=False),
             'control': NS(latActive=True), 'selfdrive': NS(enabled=False),
             'model': NS(meta=NS(laneChangeState='preLaneChange', laneChangeDirection='left' if direction < 0 else 'right'))}
  notice = lateral_lane_alert(OnroadAlert(), **sources)
  alphas = []
  with patch.object(native, 'UnifiedLabel', side_effect=label), patch.object(native.gui_app, 'texture', side_effect=texture), \
       patch.object(rl, 'draw_rectangle'), patch.object(rl, 'draw_rectangle_gradient_v') as shadow, \
       patch.object(rl, 'draw_texture_ex') as draw:
    for frame in range(fps * 4):
      with patch.object(native.time, 'monotonic', return_value=10 + frame / fps):
        renderer.render(bounds, notice, signal_direction=direction)
      if frame >= fps:
        alphas.append(draw.call_args.args[-1].a)
      assert draw.call_args.args[0].flipped is (direction > 0)
      assert labels[0].set_text.call_args.args == (notice.text1.lower(),)
      if notice.text2:
        assert labels[1].set_text.call_args.args == (notice.text2.lower(),)
      else:
        labels[1].set_text.assert_not_called()
    assert shadow.call_count == fps * 4
    assert min(alphas) < 120
    assert max(alphas) >= 250
    assert sum(a < 150 <= b for a, b in zip(alphas, alphas[1:], strict=False)) >= 3
    owner = renderer._lane_renderer
    sources['control'].latActive = False
    for frame in range(fps * 2):
      with patch.object(native.time, 'monotonic', return_value=14 + frame / fps):
        renderer.render(bounds, lateral_lane_alert(OnroadAlert(), **sources), signal_direction=direction)
      if frame / fps >= .4:
        assert not renderer._lane_visible
    assert renderer._lane_renderer is owner
    assert not renderer._lane_visible


def test_native_guidance_pulse_matches_original_sixty_hz_waveform():
  from openpilot.selfdrive.ui.mici.onroad import alert_renderer as native
  from openpilot.common.filter_simple import FirstOrderFilter

  renderer = native.AlertRenderer.__new__(native.AlertRenderer)
  renderer._rect = rl.Rectangle(0, 0, 476, 240)
  renderer._alpha_filter = FirstOrderFilter(1, .05, 1 / 60)
  renderer._turn_signal_alpha_filter = FirstOrderFilter(0, .3, 1 / 60)
  renderer._turn_signal_timer = 0.
  renderer._txt_turn_signal_left = NS(width=104)
  renderer._txt_turn_signal_right = NS(width=104, flipped=True)
  layout = native.AlertLayout(rl.Rectangle(0, 0, 476, 240),
                              native.IconLayout(renderer._txt_turn_signal_left, native.IconSide.left, 2, 5))
  original = FirstOrderFilter(0, .3, 1 / 60)
  timer = 0.
  with patch.object(rl, 'draw_texture_ex') as draw:
    for frame in range(180):
      now = 10 + frame / 60
      if now - timer > native.TURN_SIGNAL_BLINK_PERIOD:
        timer = now
        original.x = 510
      else:
        original.update(51)
      with patch.object(native.time, 'monotonic', return_value=now):
        renderer._draw_icons(layout)
      assert abs(draw.call_args.args[-1].a - int(min(original.x, 255))) <= 1


def test_translated_cjk_and_long_words_wrap_within_alert_width():
  from openpilot.system.ui.lib.display_translation import DisplayText
  for text in ['直ちに運転を引き継いでください', 'Please take control immediately']:
    lines = onroad_alerts._wrap_display(DisplayText(text), len, 8)
    assert all(len(line) <= 8 and isinstance(line, DisplayText) for line in lines)
    assert ''.join(lines).replace(' ', '') == text.replace(' ', '')
