import json
import math
import threading
from unittest.mock import patch

import pytest

from openpilot.common.hardware.comma import hardware
from openpilot.starpilot.ui import network_signal
from openpilot.starpilot.ui.network_signal import Signal, SignalFeed, read_signal
from openpilot.starpilot.ui.onroad_customization import SIGNAL_WIDGET, default_document, validate_document


def read(tmp_path, modem, rssi, iface, bt=('off', ())):
  path = tmp_path / 'modem'
  if modem is not None:
    path.write_text(json.dumps(modem))
  poll = {'RSSI': str(rssi)} if rssi is not None else {}
  with patch.object(hardware, 'MODEM_STATE_PATH', str(path)), patch.object(hardware, 'wpa_supplicant_cmd', return_value=poll), \
       patch.object(hardware, 'get_default_route_iface', return_value=iface), \
       patch('openpilot.starpilot.ui.bluetooth_status.read_bluetooth_details', return_value=bt):
    return read_signal()


def test_reads_both_radios_and_the_link_in_use(tmp_path):
  modem = {'connected': True, 'signal_quality': 60, 'network_type': 'nr'}
  assert read(tmp_path, modem, -45, 'wlan0', ('connected', ('iPhone 15',))) == Signal(3, 4, 'wifi', '5G', 'connected', ('iPhone 15',))
  assert read(tmp_path, modem, None, 'ppp0', ('on', ())) == Signal(3, None, 'cell', '5G', 'on', ())
  assert read(tmp_path, {'connected': False, 'signal_quality': 60}, -88, 'wlan0') == Signal(None, 2, 'wifi')
  assert read(tmp_path, None, None, None) == Signal()
  assert read(tmp_path, {'connected': True, 'signal_quality': '60'}, None, 'ppp0') == Signal()


def test_feed_clears_a_failed_read_and_recovers():
  signal = Signal(4, None, 'cell')
  feed = SignalFeed(read=lambda: signal)
  feed.poll()
  with patch.object(feed, 'read', side_effect=OSError('modem temporarily unavailable')):
    feed.poll()
  assert feed._signal == Signal()
  feed.poll()
  assert feed._signal == signal


def test_feed_close_wakes_and_stops_the_worker():
  polled = threading.Event()
  feed = SignalFeed(read=lambda: polled.set() or Signal(), period=3600)
  try:
    feed.snapshot()
    assert polled.wait(2)
  finally:
    feed.close()
  assert feed._thread is not None and not feed._thread.is_alive()


def test_feed_goes_blank_when_the_reading_is_old():
  feed = SignalFeed(read=lambda: Signal(4, None, 'cell'), period=3600)
  feed._thread = threading.Thread(target=lambda: None)  # never started: no background polling
  feed.poll()
  assert feed.snapshot() == Signal(4, None, 'cell')
  feed._read_at -= network_signal.STALE_S + 1
  assert feed.snapshot() == Signal()


def test_signal_is_an_opt_in_big_widget_with_background_opacity():
  document = default_document()
  placed = document['layouts']['large'][SIGNAL_WIDGET]
  assert placed['enabled'] is False and placed['opacity'] == 85
  assert SIGNAL_WIDGET not in document['layouts']['compact']
  del document['layouts']['large'][SIGNAL_WIDGET]
  assert validate_document(document)['layouts']['large'][SIGNAL_WIDGET]['enabled'] is False


def test_signal_style_picks_size_and_older_layouts_default_to_ring():
  from openpilot.starpilot.ui.onroad_customization import SIGNAL_SIZES, widget_size
  document = default_document()
  placed = document['layouts']['large'][SIGNAL_WIDGET]
  assert placed['size'] == 'ring'
  del placed['size']
  assert validate_document(document)['layouts']['large'][SIGNAL_WIDGET]['size'] == 'ring'
  placed.update(size='vertical')
  document = validate_document(document)
  assert widget_size(document, 'large', SIGNAL_WIDGET) == SIGNAL_SIZES['vertical']
  document['layouts']['large'][SIGNAL_WIDGET]['size'] = 'square'
  with pytest.raises(ValueError):
    validate_document(document)


def test_starport_signal_style_is_validated_and_pulled_on_screen():
  from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, validate_layout_for_viewport
  from openpilot.starpilot.ui.onroad_customization import SIGNAL_SIZES
  layout = default_layout_for_viewport((1920, 1080))
  del layout['widgets'][SIGNAL_WIDGET]['size']
  assert validate_layout_for_viewport(layout, (1920, 1080))['widgets'][SIGNAL_WIDGET]['size'] == 'ring'
  layout['widgets'][SIGNAL_WIDGET].update(size='horizontal', x=1920 - 30 - SIGNAL_SIZES['ring'][0])
  placed = validate_layout_for_viewport(layout, (1920, 1080))['widgets'][SIGNAL_WIDGET]
  assert placed['x'] == 1920 - 30 - SIGNAL_SIZES['horizontal'][0]
  layout['widgets'][SIGNAL_WIDGET]['size'] = 'big'
  with pytest.raises(ValueError):
    validate_layout_for_viewport(layout, (1920, 1080))


@pytest.mark.parametrize('style', ['ring', 'horizontal', 'vertical'])
@pytest.mark.parametrize('scale', [30, 50, 100])
def test_every_signal_style_scales_like_the_editor(style, scale):
  from openpilot.starpilot.ui.onroad_customization import SIGNAL_SIZES, widget_size
  document = default_document()
  placed = document['layouts']['large'][SIGNAL_WIDGET]
  assert placed['scale'] == 100
  placed.update(size=style, scale=scale)
  document = validate_document(document)
  width, height = SIGNAL_SIZES[style]
  assert widget_size(document, 'large', SIGNAL_WIDGET) == (math.floor(width * scale / 100 + .5), math.floor(height * scale / 100 + .5))
  for bad in (29, 101, 50.0):
    document['layouts']['large'][SIGNAL_WIDGET]['scale'] = bad
    with pytest.raises(ValueError):
      validate_document(document)


def test_older_signal_layouts_default_to_full_size():
  document = default_document()
  del document['layouts']['large'][SIGNAL_WIDGET]['scale']
  assert validate_document(document)['layouts']['large'][SIGNAL_WIDGET]['scale'] == 100


def test_starport_signal_scale_is_validated():
  from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, validate_layout_for_viewport
  from openpilot.starpilot.ui.onroad_customization import SIGNAL_SIZES
  layout = default_layout_for_viewport((1920, 1080))
  del layout['widgets'][SIGNAL_WIDGET]['scale']
  assert validate_layout_for_viewport(layout, (1920, 1080))['widgets'][SIGNAL_WIDGET]['scale'] == 100
  # At 30% the vertical card fits where the full-size one would not.
  layout['widgets'][SIGNAL_WIDGET].update(size='vertical', scale=30, y=1080 - 30 - round(SIGNAL_SIZES['vertical'][1] * .3))
  assert validate_layout_for_viewport(layout, (1920, 1080))['widgets'][SIGNAL_WIDGET]['y'] == layout['widgets'][SIGNAL_WIDGET]['y']
  layout['widgets'][SIGNAL_WIDGET]['scale'] = 20
  with pytest.raises(ValueError):
    validate_layout_for_viewport(layout, (1920, 1080))


def test_signal_draws_at_its_size_percentage():
  from types import SimpleNamespace as NS
  from unittest.mock import MagicMock
  from openpilot.starpilot.ui.onroad_customization import SIGNAL_SCALE
  from openpilot.starpilot.ui import onroad_signal
  fonts = MagicMock()
  fonts.vertical_ink.return_value = (0, 10)
  fonts.measure.return_value = NS(width=20)
  widget = onroad_signal.SignalWidget(fonts)
  widget.preview = Signal(2, 4, 'wifi', 'LTE', 'connected', ('iPhone 15',))
  document = default_document()
  document['layouts']['large'][SIGNAL_WIDGET].update(enabled=True, scale=30)
  with patch.object(onroad_signal, 'rl') as rl:
    rl.Rectangle = lambda *args: args
    widget.render(NS(customization=document, alert=NS(size='none')))
  assert rl.draw_rectangle_rounded.call_args_list[0].args[0][2:] == pytest.approx((112 * SIGNAL_SCALE * .3, 122 * SIGNAL_SCALE * .3))
  assert all(call.args[2] == pytest.approx(15 * SIGNAL_SCALE * .3)
             for call in fonts.draw.call_args_list)


@pytest.mark.parametrize('style', ['ring', 'horizontal', 'vertical'])
def test_all_styles_render_bluetooth_status_and_device_name(style):
  from types import SimpleNamespace as NS
  from unittest.mock import MagicMock
  from openpilot.starpilot.ui import onroad_signal
  fonts = MagicMock()
  fonts.vertical_ink.return_value = (0, 10)
  fonts.measure.side_effect = lambda text, _role, size: NS(width=len(text) * size * 0.5)
  widget = onroad_signal.SignalWidget(fonts)
  widget.preview = Signal(2, 4, 'wifi', '5G', 'connected', ('iPhone 15',))
  document = default_document()
  document['layouts']['large'][SIGNAL_WIDGET].update(enabled=True, size=style)
  with patch.object(onroad_signal, 'rl'):
    widget.render(NS(customization=document, alert=NS(size='none')))
  drawn = [call.args[0] for call in fonts.draw.call_args_list]
  assert 'iPhone 15' in drawn
  assert 'Connected' not in drawn

  fonts.draw.reset_mock()
  widget.preview = Signal(2, 4, 'wifi', '5G', 'connected', ('iPhone 15', 'AirPods'))
  with patch.object(onroad_signal, 'rl'):
    widget.render(NS(customization=document, alert=NS(size='none')))
  drawn_multi = [call.args[0] for call in fonts.draw.call_args_list]
  assert any('iPhone' in text and ('AirPods' in text or '+1' in text) for text in drawn_multi) or ('iPhone 15' in drawn_multi and 'AirPods' in drawn_multi)


def test_bluetooth_toggle_defaults_on_and_hides_bluetooth_when_off():
  from types import SimpleNamespace as NS
  from unittest.mock import MagicMock
  from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, validate_layout_for_viewport
  from openpilot.starpilot.ui import onroad_signal
  document = default_document()
  assert document['layouts']['large'][SIGNAL_WIDGET]['bluetooth'] is True
  del document['layouts']['large'][SIGNAL_WIDGET]['bluetooth']
  assert validate_document(document)['layouts']['large'][SIGNAL_WIDGET]['bluetooth'] is True
  document['layouts']['large'][SIGNAL_WIDGET]['bluetooth'] = 'yes'
  with pytest.raises(ValueError):
    validate_document(document)

  layout = default_layout_for_viewport((1920, 1080))
  assert layout['widgets'][SIGNAL_WIDGET]['bluetooth'] is True
  del layout['widgets'][SIGNAL_WIDGET]['bluetooth']
  assert validate_layout_for_viewport(layout, (1920, 1080))['widgets'][SIGNAL_WIDGET]['bluetooth'] is True

  fonts = MagicMock()
  fonts.vertical_ink.return_value = (0, 10)
  fonts.measure.side_effect = lambda text, _role, size: NS(width=len(text) * size * 0.5)
  widget = onroad_signal.SignalWidget(fonts)
  widget.preview = Signal(2, 4, 'wifi', '5G', 'connected', ('iPhone 15', 'AirPods'))
  for style in ('ring', 'horizontal', 'vertical'):
    fonts.draw.reset_mock()
    doc = default_document()
    doc['layouts']['large'][SIGNAL_WIDGET].update(enabled=True, size=style, bluetooth=False)
    with patch.object(onroad_signal, 'rl') as rl:
      rl.Rectangle = lambda *args: args
      widget.render(NS(customization=doc, alert=NS(size='none')))
    drawn = [call.args[0] for call in fonts.draw.call_args_list]
    assert 'iPhone 15' not in drawn
    assert 'Connected' not in drawn
    if style == 'ring':
      from openpilot.starpilot.ui.onroad_customization import SIGNAL_SCALE
      assert rl.draw_rectangle_rounded.call_args_list[0].args[0][3] == pytest.approx(102 * SIGNAL_SCALE)


@pytest.mark.parametrize('style,grid', [('ring', (88, 88)), ('horizontal', (268, 84)), ('vertical', (96, 160))])
@pytest.mark.parametrize('scale', [30, 100])
def test_legacy_signal_at_screen_edge_migrates_without_losing_layout(style, grid, scale):
  from openpilot.starpilot.ui.onroad_customization import SIGNAL_SCALE, signal_size, widget_bounds
  document = default_document()
  placed = document['layouts']['large'][SIGNAL_WIDGET]
  del placed['bluetooth']
  bounds = widget_bounds('large', SIGNAL_WIDGET)
  old_size = [round(round(d * SIGNAL_SCALE) * scale / 100) for d in grid]
  placed.update(size=style, scale=scale, enabled=True,
                x=bounds['x'] + bounds['width'] - old_size[0], y=bounds['y'] + bounds['height'] - old_size[1])
  migrated = validate_document(document)
  saved = migrated['layouts']['large'][SIGNAL_WIDGET]
  width, height = signal_size(saved)
  assert saved['x'] == bounds['x'] + bounds['width'] - width
  assert saved['y'] == bounds['y'] + bounds['height'] - height
  assert saved['enabled'] and saved['bluetooth']
  assert migrated['layouts']['compact'] == document['layouts']['compact']
  assert validate_document(migrated) == migrated
  placed['x'] += 1
  with pytest.raises(ValueError):
    validate_document(document)


def test_horizontal_bluetooth_name_does_not_overlap_status():
  from types import SimpleNamespace as NS
  from unittest.mock import MagicMock
  from openpilot.starpilot.ui import onroad_signal
  fonts = MagicMock()
  fonts.vertical_ink.return_value = (0, 10)
  fonts.measure.side_effect = lambda text, _role, size: NS(width=len(text) * size * .6)
  widget = onroad_signal.SignalWidget(fonts)
  widget.preview = Signal(2, 4, 'wifi', 'LTE', 'connected', ('A very long phone name', 'Headphones'))
  document = default_document()
  placed = document['layouts']['large'][SIGNAL_WIDGET]
  placed.update(enabled=True, size='horizontal')
  with patch.object(onroad_signal, 'rl'):
    widget.render(NS(customization=document, alert=NS(size='none')))
  drawn = [call.args for call in fonts.draw.call_args_list]
  assert 'Connected' not in [args[0] for args in drawn]
  name = next(args for args in drawn if '+1' in args[0])
  name_right = name[3] + fonts.measure(name[0], name[1], name[2]).width
  assert name_right <= placed['x'] + 244 * widget.s


def test_reads_the_joined_wifi_name(tmp_path):
  replies = {'SIGNAL_POLL': {'RSSI': '-45'}, 'STATUS': {'wpa_state': 'COMPLETED', 'ssid': r'Danny\xe2\x80\x99s \x43\x61\x66\xc3\xa9'}}
  with patch.object(hardware, 'MODEM_STATE_PATH', str(tmp_path / 'modem')), \
       patch.object(hardware, 'wpa_supplicant_cmd', side_effect=lambda cmd: replies[cmd]), \
       patch.object(hardware, 'get_default_route_iface', return_value='wlan0'), \
       patch('openpilot.starpilot.ui.bluetooth_status.read_bluetooth_details', return_value=('off', ())):
    assert read_signal().wifi_name == 'Danny’s Café'
    replies['STATUS'] = {'wpa_state': 'ASSOCIATING', 'ssid': 'Home'}
    assert read_signal().wifi_name == ''


def test_wifi_name_toggle_defaults_off_and_adds_a_header_row():
  from types import SimpleNamespace as NS
  from unittest.mock import MagicMock
  from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, validate_layout_for_viewport
  from openpilot.starpilot.ui import onroad_signal
  from openpilot.starpilot.ui.onroad_customization import SIGNAL_SIZES, SIGNAL_WIFI_NAME_HEIGHT, signal_size
  document = default_document()
  assert document['layouts']['large'][SIGNAL_WIDGET]['wifiName'] is False
  del document['layouts']['large'][SIGNAL_WIDGET]['wifiName']
  assert validate_document(document)['layouts']['large'][SIGNAL_WIDGET]['wifiName'] is False
  document['layouts']['large'][SIGNAL_WIDGET]['wifiName'] = 'yes'
  with pytest.raises(ValueError):
    validate_document(document)
  layout = default_layout_for_viewport((1920, 1080))
  del layout['widgets'][SIGNAL_WIDGET]['wifiName']
  assert validate_layout_for_viewport(layout, (1920, 1080))['widgets'][SIGNAL_WIDGET]['wifiName'] is False

  for style, (width, height) in SIGNAL_SIZES.items():
    assert signal_size({'size': style, 'scale': 100, 'wifiName': True}) == (width, height + SIGNAL_WIFI_NAME_HEIGHT)

  fonts = MagicMock()
  fonts.vertical_ink.return_value = (0, 10)
  fonts.measure.side_effect = lambda text, _role, size: NS(width=len(text) * size * 0.5)
  widget = onroad_signal.SignalWidget(fonts)
  for style in ('ring', 'horizontal', 'vertical'):
    for signal, shown in ((Signal(2, 4, 'wifi', 'LTE', 'on', (), 'Home Wi-Fi'), 'Home Wi-Fi'), (Signal(2, None, 'cell'), 'No Wi-Fi')):
      widget.preview = signal
      for enabled in (False, True):
        fonts.draw.reset_mock()
        doc = default_document()
        doc['layouts']['large'][SIGNAL_WIDGET].update(enabled=True, size=style, wifiName=enabled)
        with patch.object(onroad_signal, 'rl'):
          widget.render(NS(customization=doc, alert=NS(size='none')))
        assert (shown in [call.args[0] for call in fonts.draw.call_args_list]) is enabled
