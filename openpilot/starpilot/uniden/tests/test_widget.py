import time
import pytest
from types import SimpleNamespace as NS
from typing import Any, cast
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.onroad_uniden import UnidenWidget
from openpilot.starpilot.uniden.protocol import parse_alerts



@pytest.fixture(autouse=True)
def outline_guard(monkeypatch):
  outline = Mock()
  monkeypatch.setattr('openpilot.starpilot.ui.onroad_uniden.outline', outline)
  return outline


def fonts_mock():
  fonts = Mock()
  fonts.measure.side_effect = lambda text, role, size: NS(width=len(text) * size * .55, height=size * 1.2)
  return fonts


def test_touch_requires_visible_fresh_card_and_cancels_drag_or_changed_alert():
  feed = Mock()
  data = {'connected': True, 'can_mute': True, 'address': 'detector', 'alerts': parse_alerts(b'1,00,KA,3,0,33.785,R,1')}
  feed.snapshot.return_value = data
  widget = UnidenWidget(Mock(), feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  assert not widget.touch('down', 40, 60, state)
  widget.visible_until = time.monotonic() + 10
  assert widget.touch('down', 40, 60, state)
  assert widget.touch('up', 42, 62, state)
  feed.mute.assert_called_once_with(data)
  feed.mute.reset_mock()
  widget.touch('down', 40, 60, state)
  widget.touch('move', 200, 60, state)
  widget.touch('up', 40, 60, state)
  feed.mute.assert_not_called()
  widget.touch('down', 40, 60, state)
  feed.snapshot.return_value = {**data, 'address': 'another'}
  widget.touch('up', 40, 60, state)
  feed.mute.assert_not_called()
  feed.snapshot.return_value = data
  widget.touch('down', 40, 60, state)
  state.alert.size = 'full'
  widget.touch('up', 40, 60, state)
  feed.mute.assert_not_called()


def test_preview_does_not_start_detector_io_and_r4_has_no_arrows():
  feed, fonts = Mock(), fonts_mock()
  feed.snapshot.return_value = {'config': {'enabled': True}, 'connected': True, 'model': 'R4',
                                'alerts': parse_alerts(b'1,00,K,3,0,24.150,F,1', 'R4')}
  widget = UnidenWidget(fonts, feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle') as arrows:
    widget.render(state)
    feed.set_enabled.assert_called_once_with(False)
    arrows.assert_not_called()
    feed.snapshot.return_value['model'] = 'R8'
    feed.snapshot.return_value['alerts'][0]['direction'] = 'front'
    widget.render(state)
    assert arrows.call_count == 1


def test_transparency_only_changes_background_and_quiet_toggle_controls_visibility():
  feed, fonts = Mock(), fonts_mock()
  widget = UnidenWidget(fonts, feed)
  data = {'config': {'enabled': True, 'transparency': 100, 'hide_when_no_alert': True}, 'connected': True, 'model': 'R8',
          'alerts': parse_alerts(b'1,00,KA,3,0,33.785,R,1'), 'can_mute': True}
  feed.snapshot.return_value = data
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded') as rounded, \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle') as arrows:
    for transparency, alpha in ((0, 255), (50, 128), (100, 0)):
      rounded.reset_mock()
      data['config']['transparency'] = transparency
      widget.render(state)
      assert rounded.call_args_list[0].args[-1].a == alpha
      assert all(call.args[-1].a == 255 for call in fonts.draw.call_args_list)
      assert all(call.args[-1].a == 255 for call in arrows.call_args_list)
    data['alerts'] = []
    for connected in (True, False):
      data['connected'] = connected
      for hide in (True, False):
        data['config']['hide_when_no_alert'] = hide
        fonts.reset_mock()
        rounded.reset_mock()
        widget.render(state)
        if hide:
          fonts.draw.assert_not_called()
          rounded.assert_not_called()
          assert widget.visible_until == 0
        else:
          labels = [call.args[0] for call in fonts.draw.call_args_list]
          assert ('Detector ready' if connected else 'Detector disconnected') in labels
        assert not widget.touch('down', 40, 60, state)
    feed.mute.assert_not_called()


def test_one_row_per_threat_overflow_arrows_and_touch_bounds():
  from openpilot.starpilot.ui.onroad_uniden import card_size
  feed, fonts = Mock(), fonts_mock()
  alerts = parse_alerts(b'1,00,KA,6,0,35.500,R,1&1,00,K,3,0,24.150,F,2')
  data = {'config': {'enabled': True}, 'connected': True, 'can_mute': True, 'model': 'R8', 'alerts': alerts}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts, feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  assert card_size(alerts[:1]) == (460, 76), 'a single threat is one slim row'
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle') as arrows:
    widget.render(state)
    draws = {call.args[0]: call.args for call in fonts.draw.call_args_list}
    assert '35.500 GHz' in draws and '24.150 GHz' in draws
    assert '1 Manual mute' in draws and not any('THREATS' in text or 'Tap' in text for text in draws)
    assert draws['1 Manual mute'][4] < 50 + 2, 'the mute note sits in the top edge of the card'
    band, frequency = draws['KA'], draws['35.500 GHz']
    assert abs((band[4] + 36 * .6) - (frequency[4] + 28 * .6)) < 1, 'band and frequency share one centre line'
    assert arrows.call_count == 2
    assert widget.touch('down', 480, 170, state)
    assert widget.touch('up', 480, 170, state)
    feed.mute.assert_called_once_with(data)
    assert not widget.touch('down', 480, 190, state)
    data['alerts'] = alerts * 3
    fonts.reset_mock()
    widget.render(state)
    labels = [call.args[0] for call in fonts.draw.call_args_list]
    assert any(label.startswith('3 Manual mute') for label in labels)
    assert '3 MUTED' in labels
    assert labels.count('35.500 GHz') == 2
    assert card_size(data['alerts']) == (460, 256)
    data['model'] = 'R4'
    arrows.reset_mock()
    widget.render(state)
    arrows.assert_not_called()


def test_all_muted_says_so_in_the_outline(outline_guard):
  feed, fonts = Mock(), fonts_mock()
  data = {'config': {'enabled': True}, 'connected': True, 'model': 'R8', 'alerts': parse_alerts(b'1,00,KA,3,0,33.785,R,2')}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts, feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle'):
    widget.render(state)
    assert 'Manual mute' in [call.args[0] for call in fonts.draw.call_args_list]
    gap = outline_guard.call_args.args[5][1]
    assert gap[0] < 30 + 24 < gap[1], 'the outline opens around the note'
    data['alerts'] = parse_alerts(b'1,00,KA,3,0,33.785,R,1')
    fonts.reset_mock()
    widget.render(state)
    assert 'MUTED' not in [call.args[0] for call in fonts.draw.call_args_list]
    assert outline_guard.call_args.args[5] == []


def test_theme_display_settings_override_legacy_bluetooth_settings():
  feed, fonts = Mock(), Mock()
  feed.snapshot.return_value = {'config': {'enabled': True, 'transparency': 0, 'hide_when_no_alert': True},
                                'connected': True, 'alerts': []}
  widget = UnidenWidget(fonts, feed)
  placement = {'enabled': True, 'x': 30, 'y': 50, 'transparency': 75, 'hide_when_no_alert': False}
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': placement}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded') as rounded:
    widget.render(state)
    assert rounded.call_args_list[0].args[-1].a == 64
    assert 'Detector ready' in [call.args[0] for call in fonts.draw.call_args_list]
    placement['hide_when_no_alert'] = True
    rounded.reset_mock()
    widget.render(state)
    rounded.assert_not_called()


def test_theme_settings_roundtrip_migration_and_projection_isolation():
  import pytest
  from openpilot.starpilot.ui.onroad_customization import default_document, validate_document
  from openpilot.starpilot.system.starport.projection_layout import (
    default_layout_for_viewport, validate_layout_for_viewport, projection_customization)
  native = default_document()
  pos = native['layouts']['large']['uniden_alerts']
  pos.update(transparency=41, hide_when_no_alert=False)
  assert validate_document(native)['layouts']['large']['uniden_alerts'] == pos
  viewport = (1860, 1080)
  projected = default_layout_for_viewport(viewport)
  other = projected['widgets']['uniden_alerts']
  other.update(transparency=82, hide_when_no_alert=True)
  saved = validate_layout_for_viewport(projected, viewport)
  assert projection_customization(saved, native)['layouts']['large']['uniden_alerts']['transparency'] == 82
  assert native['layouts']['large']['uniden_alerts']['transparency'] == 41
  for document, placement, validate in ((native, pos, validate_document),
                                        (projected, other, lambda value: validate_layout_for_viewport(value, viewport))):
    for field, invalid in (('transparency', True), ('transparency', 101), ('hide_when_no_alert', 1),
                           ('scale', 49), ('scale', 151), ('scale', 1.0)):
      old = placement[field]
      placement[field] = invalid
      with pytest.raises(ValueError):
        validate(document)
      placement[field] = old
    for field in ('transparency', 'hide_when_no_alert', 'scale'):
      placement.pop(field)
    placement.update(hide_quiet_ride=True, hide_mute_memory=False)
    result = validate(document)
    migrated = result['widgets']['uniden_alerts'] if 'widgets' in result else result['layouts']['large']['uniden_alerts']
    assert (migrated['transparency'], migrated['hide_when_no_alert'], migrated['scale']) == (8, True, 100)
    assert 'hide_quiet_ride' not in migrated and 'hide_mute_memory' not in migrated, 'retired fields in old layouts are dropped'


@pytest.mark.parametrize(('direction', 'count'), [('front', 1), ('rear', 1), ('side', 2), ('', 0)])
def test_only_active_direction_arrows_are_drawn(direction, count):
  from openpilot.starpilot.ui.onroad_uniden import direction_arrows
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle') as arrows:
    direction_arrows(100, 100, direction, 'accent', 18)
    assert arrows.call_count == count
    assert all(call.args[-1] == 'accent' for call in arrows.call_args_list)


def test_quiet_ride_and_mute_memory_alerts_can_be_left_off_the_card():
  from openpilot.starpilot.ui.onroad_uniden import shown_alerts
  alerts = parse_alerts(b'1,00,KA,6,0,35.500,F,1&1,00,K,3,0,24.150,F,6&1,00,K,2,0,24.125,R,3&1,00,X,2,0,10.525,S,4&1,00,K,4,0,24.200,F,2')
  assert sorted(a['mute_mode'] for a in alerts) == ['', 'auto_memory', 'manual', 'memory', 'quiet_ride']
  assert shown_alerts(alerts, {}) == alerts
  assert {a['mute_mode'] for a in shown_alerts(alerts, {'hide_q_ride': True})} == {'', 'memory', 'auto_memory', 'manual'}
  assert {a['mute_mode'] for a in shown_alerts(alerts, {'hide_mute_memory': True})} == {'', 'quiet_ride', 'manual'}
  feed, fonts = Mock(), fonts_mock()
  quiet = parse_alerts(b'1,00,K,3,0,24.150,F,6')
  data = {'config': {'enabled': True, 'hide_q_ride': True}, 'connected': True, 'can_mute': True, 'model': 'R8', 'alerts': quiet}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts, feed)
  placement = {'enabled': True, 'x': 30, 'y': 50, 'hide_when_no_alert': True}
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': placement}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded') as rounded:
    widget.render(state)
    rounded.assert_not_called()
    assert widget.visible_until == 0 and not widget.touch('down', 40, 60, state), 'a hidden Quiet Ride alert takes no taps'
    placement['hide_when_no_alert'] = False
    widget.render(state)
    assert 'No active alerts' in [call.args[0] for call in fonts.draw.call_args_list]


def test_scale_grows_the_card_and_its_touch_area():
  from openpilot.starpilot.ui.onroad_uniden import card_scale
  assert (card_scale({}), card_scale({'scale': 150}), card_scale({'scale': 500}), card_scale({'scale': 'big'})) == (1., 1.5, 1.5, 1.)
  feed, fonts = Mock(), fonts_mock()
  data = {'config': {'enabled': True}, 'connected': True, 'can_mute': True, 'model': 'R8',
          'alerts': parse_alerts(b'1,00,KA,6,0,35.500,F,1')}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts, feed)
  placement = {'enabled': True, 'x': 30, 'y': 50, 'scale': 150}
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': placement}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl') as rl:
    widget.render(state)
    rl.rl_translatef.assert_called_once_with(30, 50, 0)
    rl.rl_scalef.assert_called_once_with(1.5, 1.5, 1)
    assert rl.rl_pop_matrix.call_count == 1
  assert widget.touch('down', 30 + 460 * 1.5 - 5, 50 + 76 * 1.5 - 5, state)
  widget.touch('cancel', 0, 0, state)
  placement['scale'] = 100
  assert not widget.touch('down', 30 + 460 * 1.5 - 5, 50 + 76 * 1.5 - 5, state)


def test_mute_note_names_quiet_ride_and_mute_memory():
  from openpilot.starpilot.ui.onroad_uniden import mute_note
  assert mute_note(parse_alerts(b'1,00,K,3,0,24.150,F,6')) == 'Q-Ride'
  assert mute_note(parse_alerts(b'1,00,K,3,0,24.150,F,3')) == 'Mute Memory'
  assert mute_note(parse_alerts(b'1,00,K,3,0,24.150,F,3&1,00,X,2,0,10.525,S,4')) == 'Mute Memory', 'learned and saved are both mute memory'
  assert mute_note(parse_alerts(b'1,00,K,3,0,24.150,F,2')) == 'Manual mute'
  assert mute_note(parse_alerts(b'1,00,K,3,0,24.150,F,6&1,00,X,2,0,10.525,S,3')) == 'Muted', 'mixed reasons read as plain muted'
  assert mute_note(parse_alerts(b'1,00,KA,6,0,35.500,F,1&1,00,K,3,0,24.150,F,6')) == '1 Q-Ride'
  assert mute_note(parse_alerts(b'1,00,KA,6,0,35.500,F,1')) == ''


def test_custom_filters_hide_only_matching_rows():
  from openpilot.starpilot.ui.onroad_uniden import shown_alerts
  alerts = parse_alerts(b'1,00,KA,3,0,34.313,F,1&1,00,KA,3,0,34.7,R,1')
  alerts[0]['filtered'] = True
  assert shown_alerts(alerts, {'hide_filtered': True}) == alerts[1:]
  assert shown_alerts(alerts, {'hide_filtered': False}) == alerts


def test_hidden_widget_keeps_runtime_feed_active():
  feed = Mock()
  widget = UnidenWidget(Mock(), feed)
  widget.runtime_active = True
  state = NS(alert=NS(size='full'), customization={'layouts': {'large': {}}})
  widget.render(state)
  feed.set_enabled.assert_called_once_with(True)


def test_activated_widget_leaves_the_feed_to_the_shell():
  feed = Mock()
  widget = UnidenWidget(Mock(), feed).activate(Mock())
  widget.render(NS(alert=NS(size='full'), customization={'layouts': {'large': {}}}))
  feed.set_enabled.assert_not_called()


@pytest.fixture
def detector_shell(monkeypatch):
  from openpilot.starpilot.ui import runtime_app
  saved = {'UnidenConfig': {'enabled': True, 'address': 'E0:00:00:00:0B:4D'}}
  state = NS(started=True, replay_clock=None, params=NS(get=lambda key: saved.get(key)))
  clock = [100.]
  monkeypatch.setattr(runtime_app, 'ui_state', state)
  monkeypatch.setattr(runtime_app.time, 'monotonic', lambda: clock[0])
  shell = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
  shell.detector_feed = Mock()
  return shell._poll_detector, shell, state, saved, clock


def test_detector_polls_whenever_onroad_even_with_the_screen_off(detector_shell):
  poll, shell, state, _, _ = detector_shell
  poll()
  shell.detector_feed.set_enabled.assert_called_with(True)
  state.started = False
  poll()
  shell.detector_feed.set_enabled.assert_called_with(False)
  state.started, state.replay_clock = True, object()
  poll()
  shell.detector_feed.set_enabled.assert_called_with(False)


@pytest.mark.parametrize('config', [None, {'enabled': False, 'address': 'E0:00:00:00:0B:4D'}, {'enabled': True, 'address': ''}, 'bad'])
def test_detector_stays_off_until_enabled_and_configured(detector_shell, config):
  poll, shell, _, saved, clock = detector_shell
  saved['UnidenConfig'] = config
  poll()
  shell.detector_feed.set_enabled.assert_called_with(False)
  saved['UnidenConfig'] = {'enabled': True, 'address': 'E0:00:00:00:0B:4D'}
  poll()
  shell.detector_feed.set_enabled.assert_called_with(False)  # settings are re-read on a slow cadence
  clock[0] += 5
  poll()
  shell.detector_feed.set_enabled.assert_called_with(True)


def border_state(**customization):
  return NS(alert=NS(size='full'), customization=customization)


def border_data(packet, **config):
  return {'config': {'enabled': True, 'border_bands': ['KA', 'LASER'], **config}, 'connected': True,
          'alerts': parse_alerts(packet)}


def test_warning_ignores_unchosen_absent_locked_out_and_quiet_alerts_and_prefers_laser():
  for packet, bands in ((b'1,00,K,3,0,24.150,F,1', ['KA', 'LASER']), (b'0', ['KA', 'K', 'LASER'])):
    _, drawn = edge_run(packet, bands=bands)
    assert not any(drawn.values())
  for flag, hide in (('filtered', 'hide_filtered'), ('quiet', 'hide_below_limit')):
    _, drawn = edge_run(b'1,00,K,3,0,24.150,F,1', flags={flag: True}, config={hide: True})
    assert not any(drawn.values()), flag
    _, shown = edge_run(b'1,00,K,3,0,24.150,F,1', flags={flag: True}, reduce_motion=True)
    assert shown['line'] and shown['line'][0][2].a == round(255 * .85 * .5 * 1.1), f'{flag} but not hidden: shown at the muted brightness'
  for mode, hide in ((b'6', 'hide_q_ride'), (b'3', 'hide_mute_memory')):
    _, drawn = edge_run(b'1,00,K,3,0,24.150,F,' + mode, config={hide: True})
    assert not any(drawn.values()), hide
  _, drawn = edge_run(b'1,00,K,3,0,24.150,F,1&1,00,LASER,8,0,5,F,1')
  assert all((c.r, c.g) == (0xFF, 0x5B) for _, c in drawn['rect']), 'laser wins, in its coral'


def test_holding_an_alert_locks_it_out_once_and_a_tap_still_mutes():
  feed = Mock()
  data = {'config': {'enabled': True}, 'connected': True, 'can_mute': True, 'address': 'detector', 'model': 'R8',
          'alerts': parse_alerts(b'1,00,KA,3,0,33.785,R,1')}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts_mock(), feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  widget.visible_until = time.monotonic() + 10
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle'):
    widget.touch('down', 40, 60, state)
    widget.render(state)
    feed.lock_here.assert_not_called()
    pressed = widget.pressed
    assert pressed is not None
    widget.pressed = (pressed[0], pressed[1], pressed[2], time.monotonic() - 1)
    widget.render(state)
    widget.render(state)
    feed.lock_here.assert_called_once_with(data)
    widget.touch('up', 40, 60, state)
    feed.mute.assert_not_called()
    widget.touch('down', 40, 60, state)
    widget.touch('up', 40, 60, state)
    feed.mute.assert_called_once_with(data)


def test_offline_detector_shows_a_badge_even_when_idle_and_notices_replace_the_note():
  feed, fonts = Mock(), fonts_mock()
  data = {'config': {'enabled': True, 'hide_when_no_alert': True}, 'connected': False, 'alerts': [], 'offline': True}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts, feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle'):
    widget.render(state)
    assert [call.args[0] for call in fonts.draw.call_args_list] == ['DETECTOR OFFLINE']
    fonts.reset_mock()
    feed.snapshot.return_value = {**data, 'offline': False, 'connected': True, 'model': 'R8', 'can_mute': True, 'notice': 'Locked out here (1)',
                                  'alerts': parse_alerts(b'1,00,KA,3,0,33.785,R,1')}
    widget.render(state)
    assert 'Locked out here (1)' in [call.args[0] for call in fonts.draw.call_args_list]
    fonts.reset_mock()
    quiet = parse_alerts(b'1,00,K,3,0,24.150,F,1')
    quiet[0]['quiet'] = True
    feed.snapshot.return_value = {**data, 'offline': False, 'connected': True, 'model': 'R8', 'alerts': quiet}
    widget.render(state)
    assert 'Below limit' in [call.args[0] for call in fonts.draw.call_args_list]


def test_offline_badge_moves_to_the_top_of_the_view_when_the_widget_is_off_or_covered():
  feed, fonts = Mock(), fonts_mock()
  feed.snapshot.return_value = {'config': {'enabled': True}, 'connected': False, 'alerts': [], 'offline': True}
  widget = UnidenWidget(fonts, feed)
  covered = NS(alert=NS(size='full'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'):
    widget.render_border(covered, 1860, 1080)
    assert [call.args[0] for call in fonts.draw.call_args_list] == ['DETECTOR OFFLINE']
    assert fonts.draw.call_args.args[3] == (1860 - 330) / 2 + 24
    fonts.reset_mock()
    shown = NS(alert=NS(size='none'), customization=covered.customization)
    widget.render_border(shown, 1860, 1080)
    fonts.draw.assert_not_called()


def test_onroad_view_draws_the_border_after_the_rest_of_the_frame():
  from openpilot.starpilot.ui.onroad import OnroadView
  from openpilot.starpilot.ui.presentation import Profile
  order = []
  view = cast(Any, object.__new__(OnroadView))
  view.uniden = Mock(runtime_active=False)
  view.uniden.render_border.side_effect = lambda *args: order.append(('border', args))
  view.fonts, view.camera_layer, view.projection_viewport = NS(profile=Profile.LARGE), None, (1500, 1000)
  view._large = lambda state: order.append('large')
  view.render(NS(camera_available=False, viewport_width=1860))
  assert order[0] == 'large' and order[1][0] == 'border' and order[1][1][1:] == (1500, 1000)


def test_a_mute_silences_the_detector_but_keeps_the_frame_while_the_signal_lasts():
  from openpilot.starpilot.ui.onroad_uniden import threat
  alert = parse_alerts(b'1,00,K,7,0,24.150,F,2')[0]
  assert alert['muted']
  assert threat([alert], ['K']) is alert
  assert threat([{**alert, 'timed': True}], ['K'])
  assert threat([{**alert, 'breakthrough': True}], ['K'])
  assert threat([{**alert, 'quiet': True}], ['K']), 'hiding is up to the shared on-screen rules, not threat()'


def test_card_note_names_timed_mute_and_strong_signals():
  feed, fonts = Mock(), fonts_mock()
  widget = UnidenWidget(fonts, feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {'uniden_alerts': {'enabled': True, 'x': 30, 'y': 50}}}})
  def notes(**flags):
    items = parse_alerts(b'1,00,K,3,0,24.150,F,2')
    items[0].update(flags)
    feed.snapshot.return_value = {'config': {'enabled': True}, 'connected': True, 'model': 'R8', 'alerts': items}
    fonts.reset_mock()
    widget.render(state)
    return [call.args[0] for call in fonts.draw.call_args_list]
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle'):
    assert 'Timed mute' in notes(timed=True)
    assert 'Strong signal' in notes(breakthrough=True, timed=False)


@pytest.mark.parametrize('flags', [{}, {'breakthrough': True}, {'quiet': True, 'muted': False},
                                  {'timed': True, 'muted': False}, {'locked': True}, {'filtered': True}])
def test_mute_status_stays_red_on_the_right_with_strength_notices_and_long_notes(flags, outline_guard):
  fonts, feed = fonts_mock(), Mock()
  alerts = [{**parse_alerts(b'1,00,K,8,0,24.150,F,2')[0], **flags}]
  data = {'connected': True, 'model': 'R8', 'alerts': alerts}
  widget = UnidenWidget(fonts, feed)
  state = NS(customization={})
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle'), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_triangle'):
    for notice in ('', 'Locked out here (1)', 'A very long notice that must leave room for mute status'):
      fonts.reset_mock()
      widget._card(state, {**data, 'notice': notice}, alerts, {}, 30, 50)
      draws = {call.args[0]: call.args for call in fonts.draw.call_args_list}
      status = draws['MUTED']
      assert status[3] > 30 + 460 / 2
      assert (status[-1].r, status[-1].g, status[-1].b, status[-1].a) == (255, 91, 101, 255)
      if flags.get('breakthrough') and not notice:
        assert 'Strong signal' in draws
      gaps = sorted(outline_guard.call_args.args[5])
      assert len(gaps) == 2 and gaps[0][1] + 12 <= gaps[1][0]
      assert gaps[1][0] < status[3] < gaps[1][1]


def edge_run(packet, *, parts=None, muted_percent=50, moments=(5.,), model='R8', speed_x=640, reduce_motion=False,
             bands=('KA', 'K', 'LASER'), flags=None, config=None):
  """Render the Edge style and collect what it drew: rounded strokes, straight pieces, rings and chevron lines."""
  from openpilot.starpilot.uniden.protocol import DEFAULT_CONFIG
  feed = Mock()
  data = border_data(packet, border_bands=list(bands),
                     border_edge={**DEFAULT_CONFIG['border_edge'], **(parts or {})}, border_muted_percent=muted_percent, **(config or {}))
  data['alerts'] = [{**alert, **(flags or {})} for alert in parse_alerts(packet, model)]
  feed.snapshot.return_value = data
  widget = UnidenWidget(Mock(), feed)
  drawn = {'band': [], 'rect': [], 'ring': [], 'line': [], 'gradient': []}
  customization = {'reduceMotion': reduce_motion, 'layouts': {'large': {'current_speed': {'x': speed_x, 'y': 30}}}}
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rounded_lines_ex', side_effect=lambda r, k, n, t, c: drawn['band'].append((r, t, c))), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rec', side_effect=lambda r, c: drawn['rect'].append((r, c))), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_ring', side_effect=lambda *a: drawn['ring'].append(a)), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_line_ex', side_effect=lambda a, b, t, c: drawn['line'].append((a, b, c))), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_gradient_v', side_effect=lambda *a: drawn['gradient'].append(a)), \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_gradient_h', side_effect=lambda *a: drawn['gradient'].append(a)), \
       patch('openpilot.starpilot.ui.onroad_uniden.offset', return_value=(speed_x - 640, 0)), \
       patch('openpilot.starpilot.ui.onroad_uniden.time.monotonic', side_effect=list(moments)):
    for _ in moments:
      widget.render_border(NS(alert=NS(size='none'), customization=customization), 1860, 1080)
  return widget, drawn


def test_edge_draws_a_segmented_line_that_follows_the_rounded_corners_and_chevrons_by_strength():
  _, drawn = edge_run(b'1,00,K,8,0,24.150,F,1')
  assert drawn['rect'] and drawn['ring'], 'dashes along the straight sides and around the corners'
  assert min(r.x for r, _ in drawn['rect']) >= 30 + 12 and all(c.r == 0xFF and c.g == 0xBA for _, c in drawn['rect'])
  assert not drawn['band'] and not drawn['gradient'], 'no glow or veil unless chosen'
  assert len(drawn['line']) == 2 * 4 * 2, 'strength 8 is four chevrons in each of the two sets beside the speed'
  for strength, count in ((1, 1), (2, 1), (3, 2), (5, 3), (7, 4)):
    _, drawn = edge_run(f'1,00,K,{strength},0,24.150,F,1'.encode())
    assert len(drawn['line']) == 2 * count * 2


def test_edge_chevrons_flank_the_speed_ahead_and_for_the_r4_and_point_toward_the_signal():
  _, ahead = edge_run(b'1,00,K,2,0,24.150,F,1', speed_x=700)
  apexes = sorted(round(b.x) for a, b, c in ahead['line'][::2])
  assert apexes == [930 + 60 - 300, 930 + 60 + 300], 'one set each side of the moved speed'
  assert all(b.y < a.y for a, b, _ in ahead['line'][::2]), 'pointing up, ahead'
  _, r4 = edge_run(b'1,00,K,2,0,24.150,F,1', model='R4')
  assert sorted(round(b.x) for a, b, c in r4['line'][::2]) == [630, 1230]
  _, rear = edge_run(b'1,00,K,2,0,24.150,R,1')
  assert [round(b.x) for a, b, c in rear['line'][::2]] == [930] and all(b.y > a.y for a, b, _ in rear['line'][::2])
  _, beside = edge_run(b'1,00,K,2,0,24.150,S,1')
  assert sorted(round(b.y) for a, b, c in beside['line'][::2]) == [540, 540]


def test_edge_parts_can_be_switched_and_muted_alerts_use_the_chosen_brightness():
  _, drawn = edge_run(b'1,00,K,4,0,24.150,F,1', parts={'line': False, 'chevrons': False, 'glow': True, 'veil': True})
  assert drawn['band'] and not drawn['rect'] and not drawn['line']
  assert any(c.r == 0 and c.g == 0 and c.b == 0 for _, _, c in drawn['band']), 'the veil is black'
  _, solid = edge_run(b'1,00,K,4,0,24.150,F,1', parts={'dashes': False, 'chevrons': False})
  assert len(solid['band']) == 1 and not solid['rect'], 'an unbroken line is one rounded stroke'
  _, loud = edge_run(b'1,00,K,4,0,24.150,F,1', reduce_motion=True)
  _, half = edge_run(b'1,00,K,4,0,24.150,F,2', reduce_motion=True)
  _, hidden = edge_run(b'1,00,K,4,0,24.150,F,2', muted_percent=0)
  assert loud['line'][0][2].a == round(255 * .85 * 1.1) and half['line'][0][2].a == round(255 * .85 * .5 * 1.1)
  assert not any(hidden.values()), '0% hides the frame while muted'


def test_edge_pulses_on_a_new_alert_and_a_two_bar_jump_and_fades_out():
  from openpilot.starpilot.ui.onroad_uniden import EdgeMotion
  motion = EdgeMotion()
  alert = parse_alerts(b'1,00,K,3,0,24.150,F,1')[0]
  motion.update(alert, 10., False)
  assert [p[1] for p in motion.pulses] == [2] and motion.boost(10.15) > .9
  motion.update({**alert, 'strength': 4}, 11., False)
  assert motion.boost(11.1) == 0, 'one bar is not worth a pulse'
  motion.update({**alert, 'strength': 5}, 12., False)
  assert motion.boost(12.15) > .9, 'two bars over the last pulse is'
  motion.update({**alert, 'band': 'LASER', 'frequency': None}, 13., False)
  assert motion.pulses[-1][1] == 3, 'laser gets three'
  motion.update(None, 14., False)
  assert motion.fading(14.25)[1] == pytest.approx(.5) and motion.fading(14.6) is None
  still = EdgeMotion()
  still.update(alert, 10., True)
  assert not still.pulses


def test_old_saved_colors_map_to_current_ones_and_styles_are_dropped():
  from openpilot.starpilot.uniden.protocol import DEFAULT_CONFIG, normalize_config
  old = {**DEFAULT_CONFIG, 'border_colors': {'KA': 'white', 'K': 'purple', 'LASER': 'yellow'},
         'border_styles': {'KA': 'direction', 'K': 'chase', 'LASER': 'pulse'}}
  del old['border_edge'], old['border_muted_percent']
  config = normalize_config(old)
  assert 'border_styles' not in config and config['border_muted_percent'] == 50
  assert config['border_colors'] == {'KA': 'white', 'K': 'ultraviolet', 'LASER': 'lime'}
  for change in ({'border_edge': {'line': True}}, {'border_edge': {**DEFAULT_CONFIG['border_edge'], 'line': 1}},
                 {'border_muted_percent': 101}, {'border_muted_percent': True}):
    with pytest.raises(ValueError):
      normalize_config({**DEFAULT_CONFIG, **change})


def test_mute_note_says_why_starpilot_or_the_detector_muted():
  from openpilot.starpilot.ui.onroad_uniden import mute_note
  def note(**flags):
    alert = parse_alerts(b'1,00,K,3,0,24.150,F,2')[0]   # the detector reports our own mutes as manual
    return mute_note([{**alert, **flags}])
  assert note(locked=True, filtered=True) == 'Lockout'
  assert note(ruled=True, filtered=True) == 'Frequency rule'
  assert note(quiet=True) == 'Below limit'
  assert note(timed=True) == 'Timed mute'
  assert note() == 'Manual mute'
  quiet = parse_alerts(b'1,00,K,3,0,24.150,F,1')[0]
  assert mute_note([{**quiet, 'quiet': True}]) == 'Below limit', 'quieted before the detector confirms the mute'


def test_never_warn_again_keeps_a_strong_signal_muted():
  from openpilot.starpilot.uniden.policy import apply
  def run(strength_setting):
    timed = {'bands': ['K'], 'seconds': 5, 'strength': strength_setting}
    state = None
    for step in range(13):   # read every half second, as the feed does, for six seconds
      alerts = parse_alerts(b'1,00,K,8,0,24.150,F,2')
      state = apply(alerts, [], {'timed': timed}, state, step * .5)
    return alerts[0]
  assert run(6)['breakthrough'] and not run(6)['timed']
  assert not run(0)['breakthrough'] and run(0)['timed'], 'never: a strength-8 signal stays on its timed mute'


def test_card_and_warning_share_the_on_screen_rules_and_work_without_each_other():
  feed, fonts = Mock(), fonts_mock()
  data = {'config': {'enabled': True, 'border_bands': ['K'], 'hide_below_limit': True}, 'connected': True, 'model': 'R8',
          'alerts': [{**parse_alerts(b'1,00,K,3,0,24.150,F,1')[0], 'quiet': True}]}
  feed.snapshot.return_value = data
  widget = UnidenWidget(fonts, feed)
  state = NS(alert=NS(size='none'), customization={'layouts': {'large': {}}})   # no card in this layout
  with patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_rectangle_rec') as dashes, \
       patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_ring'), patch('openpilot.starpilot.ui.onroad_uniden.rl.draw_line_ex'):
    widget.render_border(state, 1860, 1080)
    dashes.assert_not_called()
    data['config']['hide_below_limit'] = False
    widget.render_border(state, 1860, 1080)
    assert dashes.called, 'the warning works with no card on screen'


def test_shell_routes_a_claimed_touch_to_the_card_and_always_ends_it():
  from openpilot.starpilot.ui.shell import ShellMode
  widget = UnidenWidget(Mock(), Mock())
  session = NS(favorites=NS(is_open=False), input=Mock(), snapshot=Mock(return_value=NS(onroad='state')))
  assert widget.activate(session) is widget and widget.runtime_active
  widget.touch = Mock(return_value=True)
  assert widget.press(ShellMode.ONROAD, 1, 2)
  widget.touch.assert_called_with('down', 1, 2, 'state')
  session.input.cancel.assert_called_once()
  assert widget.move(ShellMode.SETTINGS, 3, 4)
  widget.touch.assert_called_with('move', 3, 4, None)
  assert widget.release(ShellMode.ONROAD, 5, 6) and not widget.claimed
  widget.touch.assert_called_with('up', 5, 6, 'state')
  assert not widget.release(ShellMode.ONROAD, 5, 6) and not widget.move(ShellMode.ONROAD, 5, 6)
  session.favorites.is_open = True
  widget.touch.reset_mock()
  session.snapshot.reset_mock()
  assert not widget.press(ShellMode.ONROAD, 1, 2) and not widget.touch.called and not session.snapshot.called
  session.favorites.is_open = False
  assert not widget.press(ShellMode.SETTINGS, 1, 2)
  widget.claimed = True
  widget.cancel()
  assert not widget.claimed
  widget.touch.assert_called_with('cancel', 0, 0, None)


def test_sizes_and_signal_colors_round_half_up_like_the_editor():
  from openpilot.starpilot.ui.onroad_customization import scaled_size
  from openpilot.starpilot.ui.onroad_uniden import signal_color
  assert scaled_size(460, 150, 50) == (230, 75)
  assert scaled_size(5, 3, 50) == (3, 2), '2.5 and 1.5 both round up, as Math.round does'
  assert scaled_size(229, 1, 50) == (115, 1)
  assert scaled_size(460, 260, 101) == (465, 263)
  color = signal_color(5, 1)
  assert (color.r, color.g, color.b) == (97, 203, 238), 'Math.round(202.5) is 203'
