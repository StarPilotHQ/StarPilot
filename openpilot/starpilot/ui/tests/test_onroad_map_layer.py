from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.onroad_customization import default_document
from openpilot.starpilot.ui.onroad_map_layer import NativeMapLayer


def test_native_map_prepares_outside_render_and_releases_when_hidden_disabled_or_offroad():
  with patch('openpilot.starpilot.ui.onroad_map_layer.gui_app') as app, \
       patch('openpilot.starpilot.ui.onroad_map.live_overlay') as overlay, \
       patch('openpilot.starpilot.ui.onroad_map.MapFeed') as feed:
    ui = NS(started=True, sm=Mock())
    layer = NativeMapLayer(Mock(), ui)
    state = NS(customization=default_document(), viewport_width=1860)
    app.add_render_prepare.assert_called_once_with(layer.prepare, layer.close)
    layer.render(None, state)
    layer.prepare()
    overlay.assert_not_called()
    placed = state.customization['layouts']['large']['nav_map']
    placed.update(enabled=True, x=100, y=200, width=600, height=400, opacity=35)
    layer.render(None, state)
    overlay.assert_not_called()  # Rendering only queues offscreen work.
    layer.prepare()
    overlay.return_value.prepare.assert_called_once_with(feed.return_value.read.return_value, 600, 400)
    feed.return_value.read.assert_called_once_with(ui.sm)
    layer.render(None, state)
    args = overlay.return_value.draw.call_args.args
    assert (args[0].x, args[0].y, args[0].width, args[0].height, args[1]) == (100, 200, 600, 400, .35)
    placed.update(width=700)
    overlay.return_value.draw.reset_mock()
    layer.render(None, state)
    overlay.return_value.draw.assert_not_called()  # Never stretch an old prepared map after a resize.
    layer.prepare()
    layer.prepare()  # No render request: the page is hidden.
    assert layer.map is None
    overlay.return_value.close.assert_called_once()
    layer.render(None, state)
    layer.prepare()
    placed['enabled'] = False
    layer.render(None, state)
    layer.prepare()
    assert layer.map is None
    placed['enabled'] = True
    layer.render(None, state)
    ui.started = False
    layer.prepare()
    assert layer.map is None
    layer.close()
    app.remove_render_prepare.assert_called_once_with(layer.prepare)


def test_native_map_yields_while_starport_draws_its_own():
  with patch('openpilot.starpilot.ui.onroad_map_layer.gui_app'), \
       patch('openpilot.starpilot.ui.onroad_map_layer.projection_map_active') as lease, \
       patch('openpilot.starpilot.ui.onroad_map.live_overlay') as overlay, \
       patch('openpilot.starpilot.ui.onroad_map.MapFeed'):
    ui = NS(started=True, sm=Mock())
    layer = NativeMapLayer(Mock(), ui)
    state = NS(customization=default_document(), viewport_width=1860)
    state.customization['layouts']['large']['nav_map'].update(enabled=True)
    lease.return_value = False
    layer.render(None, state)
    layer.prepare()
    assert layer.map is not None
    lease.return_value = True
    layer.render(None, state)
    assert layer.pending is None
    layer.prepare()
    assert layer.map is None
    overlay.return_value.close.assert_called_once()
