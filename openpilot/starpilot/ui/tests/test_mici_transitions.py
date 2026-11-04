"""C4 transitions retire camera frames and cancel deferred onroad navigation."""

from collections import deque
from types import SimpleNamespace as NS
from unittest.mock import Mock
import weakref

import pytest

from openpilot.selfdrive.ui.mici.layouts import main
from openpilot.selfdrive.ui.mici.onroad import cameraview
from openpilot.selfdrive.ui.onroad import cameraview as base_cameraview


@pytest.fixture
def navigation(monkeypatch):
  clock = [0.0]
  callbacks = []
  ui = NS(started=False, is_body=False, sm={'carState': NS(standstill=True)})
  gui = NS(widget_in_stack=Mock(return_value=False),
           pop_widgets_to=Mock(side_effect=lambda _, callback: callbacks.append(callback)))
  monkeypatch.setattr(main, 'ui_state', ui)
  monkeypatch.setattr(main, 'gui_app', gui)
  monkeypatch.setattr(main.rl, 'get_time', lambda: clock[0])
  view = object.__new__(main.MiciMainLayout)
  view._prev_onroad = False
  view._prev_standstill = False
  view._onroad_time_delay = None
  monkeypatch.setattr(view, '_onboarding_window', object(), raising=False)
  monkeypatch.setattr(view, '_home_layout', object(), raising=False)
  monkeypatch.setattr(view, '_car_onroad_layout', object(), raising=False)
  monkeypatch.setattr(view, '_body_onroad_layout', object(), raising=False)
  view._scroll_to = Mock()
  return NS(view=view, ui=ui, gui=gui, clock=clock, callbacks=callbacks)


def test_quick_onroad_offroad_cancels_pending_navigation(navigation):
  n = navigation
  n.ui.started = True
  n.view._handle_transitions()
  n.clock[0] = 1.0
  n.ui.started = False
  n.view._handle_transitions()
  n.clock[0] = 3.0
  n.view._handle_transitions()
  assert n.view._onroad_time_delay is None
  n.view._scroll_to.assert_called_once_with(n.view._home_layout)
  n.gui.pop_widgets_to.assert_not_called()


def test_offroad_standstill_change_does_not_pop_settings(navigation):
  n = navigation
  n.view._handle_transitions()
  n.ui.sm['carState'].standstill = False
  n.view._handle_transitions()
  n.gui.pop_widgets_to.assert_not_called()
  n.view._scroll_to.assert_not_called()


def test_normal_onroad_delay_still_navigates_once(navigation):
  n = navigation
  n.ui.started = True
  n.view._handle_transitions()
  n.clock[0] = 2.0
  n.view._handle_transitions()
  n.gui.pop_widgets_to.assert_not_called()
  n.clock[0] = 3.0
  n.view._handle_transitions()
  assert len(n.callbacks) == 1
  n.callbacks.pop()()
  n.view._scroll_to.assert_called_once_with(n.view._car_onroad_layout)
  n.view._handle_transitions()
  assert n.gui.pop_widgets_to.call_count == 1


@pytest.mark.parametrize('trigger', ['delay', 'motion', 'timeout'])
def test_deferred_scroll_rechecks_started_after_pop_animation(navigation, trigger):
  n = navigation
  n.ui.started = True
  n.view._handle_transitions()
  if trigger == 'delay':
    n.clock[0] = 3.0
    n.view._handle_transitions()
  else:
    n.ui.sm['carState'].standstill = False
    if trigger == 'motion':
      n.view._handle_transitions()
    else:
      n.view._on_interactive_timeout()
  assert len(n.callbacks) == 1
  n.ui.started = False
  n.view._handle_transitions()
  n.callbacks.pop()()
  n.view._scroll_to.assert_called_once_with(n.view._home_layout)


def test_onboarding_retains_navigation_ownership(navigation):
  n = navigation
  n.gui.widget_in_stack.return_value = True
  n.ui.started = True
  n.view._handle_transitions()
  n.clock[0] = 3.0
  n.view._handle_transitions()
  n.view._on_interactive_timeout()
  n.gui.pop_widgets_to.assert_not_called()
  n.view._scroll_to.assert_not_called()


class CameraClient:
  def __init__(self, name, events, frames=()):
    self.name = name
    self.events = events
    self.frames = deque(frames)
    self.connected = False
    self.recv_calls = 0
    self.num_buffers = 2
    self.stride = 640
    self.height = 480

  def is_connected(self):
    return self.connected

  def connect(self, block):
    assert block is False
    self.connected = True
    return True

  def recv(self, timeout_ms):
    assert timeout_ms == 0
    self.recv_calls += 1
    self.events.append(('recv', self.name))
    return self.frames.popleft() if self.frames else None

  def available_streams(self, name, block):
    assert name == 'camerad' and block is False
    return [cameraview.VisionStreamType.VISION_STREAM_NARROW_ROAD]

  def __del__(self):
    self.events.append(('release', self.name))


@pytest.fixture
def camera(monkeypatch):
  events = []
  created = []
  def new_client(name, stream, conflate):
    assert name == 'camerad'
    assert stream == cameraview.VisionStreamType.VISION_STREAM_NARROW_ROAD
    assert conflate is True
    client = CameraClient(f'new-{len(created)}', events)
    created.append(client)
    return client

  monkeypatch.setattr(cameraview, 'VisionIpcClient', new_client)
  monkeypatch.setattr(cameraview, 'COMMA_HARDWARE', True)
  monkeypatch.setattr(base_cameraview, 'COMMA_HARDWARE', True)
  monkeypatch.setattr(base_cameraview, 'destroy_egl_image', lambda image: events.append(('egl', image)))
  monkeypatch.setattr(cameraview.rl, 'unload_texture', lambda texture: events.append(('texture', texture.id)))
  monkeypatch.setattr(cameraview.rl, 'unload_shader', lambda shader: events.append(('shader', shader.id)))
  monkeypatch.setattr(cameraview.rl, 'get_time', lambda: 1.0)
  view = object.__new__(cameraview.CameraView)
  view.close = Mock()
  view._name = 'camerad'
  view._stream_type = cameraview.VisionStreamType.VISION_STREAM_NARROW_ROAD
  view.client = CameraClient('active', events, frames=[object()] * 1000)
  view.client.connected = True
  view._target_client = CameraClient('target', events, frames=[object()])
  view._target_client.connected = True
  view._target_stream_type = cameraview.VisionStreamType.VISION_STREAM_WIDE_ROAD
  view._switching = True
  view.frame = NS(owner=view.client)
  view.available_streams = [view._stream_type, view._target_stream_type]
  view.texture_y = NS(id=1)
  view.texture_uv = NS(id=2)
  monkeypatch.setattr(view, 'egl_images', {0: 'old-image'}, raising=False)
  view.shader = NS(id=3)
  view.egl_texture = NS(id=4)
  view._texture_needs_update = False
  view.last_connection_attempt = 99.0
  view._draw_placeholder = Mock()
  view._render_egl = Mock()
  return NS(view=view, events=events, created=created)


def test_camera_transition_retires_resources_before_clients_without_draining(camera):
  c = camera
  old = weakref.ref(c.view.client)
  target = weakref.ref(c.view._target_client)
  c.view._offroad_transition()
  assert not any(event[0] == 'recv' for event in c.events)
  assert old() is None and target() is None
  released = [i for i, event in enumerate(c.events) if event[0] == 'release']
  destroyed = [i for i, event in enumerate(c.events) if event[0] in ('egl', 'texture')]
  assert len(destroyed) == 3 and max(destroyed) < min(released)
  assert c.view.frame is None
  assert c.view._target_client is None and c.view._target_stream_type is None
  assert not c.view._switching
  assert c.view.available_streams == []
  assert c.view.egl_images == {}
  assert c.view.texture_y is None and c.view.texture_uv is None
  assert c.view.shader.id == 3 and c.view.egl_texture.id == 4


def test_successive_camera_transitions_remain_empty_until_current_generation_frame(camera):
  c = camera
  c.view._offroad_transition()
  c.created[0].frames.append(NS(width=640, height=480))
  c.view._offroad_transition()
  assert len(c.created) == 2 and c.created[0].recv_calls == 0
  assert not any(event[0] == 'recv' for event in c.events)
  assert len([event for event in c.events if event[0] in ('egl', 'texture')]) == 3
  rect = cameraview.rl.Rectangle(0, 0, 640, 480)
  c.view._render(rect)
  assert c.view.frame is None
  c.view._draw_placeholder.assert_called_once_with(rect)
  c.view._render_egl.assert_not_called()
  current_frame = NS(width=640, height=480)
  c.created[-1].frames.append(current_frame)
  c.view._render(rect)
  assert c.view.frame is current_frame
  c.view._render_egl.assert_called_once()
  assert c.created[0].recv_calls == 0


def test_closed_camera_is_not_reopened_by_later_transition(camera):
  c = camera
  cameraview.CameraView.close(c.view)
  after_close = list(c.events)
  c.view._offroad_transition()
  assert c.view.client is None
  assert c.created == []
  assert c.events == after_close
