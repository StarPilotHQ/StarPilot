"""Real native gesture dispatch in Big UI, including canceled and moving rows."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.ui.spacepilot import large, native
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.tests.test_spacepilot_state import mature_gate, parked_ui, read
from openpilot.system.ui.lib.application import MouseEvent, MousePos, gui_app


@pytest.fixture
def adapter(monkeypatch):
  monkeypatch.setattr(gui_app, 'font', lambda *_: rl.Font())
  fonts = NS(profile=Profile.LARGE, vertical_ink=lambda _t, _r, size: (0, size),
             measure=lambda text, _r, size: NS(width=len(text) * size / 2))
  panel = NS(_scroller=NS(_items=[]), show_event=Mock(), hide_event=Mock(), _update_state=Mock())
  ui = NS(params=NS(get=lambda _: '0.10.3'))
  monkeypatch.setattr(large, 'ui_state', ui)
  monkeypatch.setattr(native, 'ui_state', ui)
  monkeypatch.setattr(large, 'live_witness', lambda: read(parked_ui()))
  widget = large.SpacePilotDeveloperAdapter(panel, fonts, Mock(), Mock())
  widget.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  widget.show_event()
  widget.gate = mature_gate()
  yield widget
  widget.hide_event()


def deliver(widget, monkeypatch, index, *, drift=0, duration=.04):
  bounds = widget._version_bounds()
  pos = MousePos(bounds.x + 20, bounds.y + 10)
  now = 10 + index * .2
  monkeypatch.setattr(native.time, 'monotonic', lambda: now)
  events = [MouseEvent(pos, 0, True, False, True, now)]
  if drift:
    events.append(MouseEvent(MousePos(pos.x + drift, pos.y), 0, False, False, True, now + .01))
  events.append(MouseEvent(pos, 0, False, True, False, now + duration))
  monkeypatch.setattr(gui_app, '_mouse_events', events)
  widget._process_mouse_events()


def test_big_ui_five_version_value_taps_launch_same_native_game(adapter, monkeypatch):
  push = Mock()
  monkeypatch.setattr(gui_app, 'push_widget', push)
  for i in range(4):
    deliver(adapter, monkeypatch, i)
  push.assert_not_called()
  deliver(adapter, monkeypatch, 4)
  push.assert_called_once()
  assert isinstance(push.call_args.args[0], native.SpacePilotPage)
  assert push.call_args.args[0].gate is not adapter.gate


@pytest.mark.parametrize('cancel', ['drag', 'long', 'page', 'hide', 'unsafe', 'outside', 'scroll'])
def test_big_ui_cancel_resets_deliberate_gesture(adapter, monkeypatch, cancel):
  push = Mock()
  monkeypatch.setattr(gui_app, 'push_widget', push)
  for i in range(4):
    deliver(adapter, monkeypatch, i)
  if cancel == 'drag':
    deliver(adapter, monkeypatch, 4, drift=30)
  elif cancel == 'long':
    deliver(adapter, monkeypatch, 4, duration=.5)
  elif cancel == 'page':
    adapter._emit(NS(kind='scroll', direction=1))
  elif cancel == 'hide':
    adapter.hide_event()
  elif cancel == 'unsafe':
    monkeypatch.setattr(large, 'live_witness', lambda: None)
    adapter._update_state()
  elif cancel == 'outside':
    monkeypatch.setattr(gui_app, '_mouse_events', [MouseEvent(MousePos(10, 10), 0, True, False, True, 10.9)])
    adapter._process_mouse_events()
  else:
    adapter.set_touch_valid_callback(lambda: False)
    monkeypatch.setattr(gui_app, '_mouse_events', [])
    adapter._process_mouse_events()
  assert adapter.taps.count == 0
  push.assert_not_called()
