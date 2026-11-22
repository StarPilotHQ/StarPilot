from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from openpilot.selfdrive.ui.mici.onroad import cameraview as compact
from openpilot.selfdrive.ui.onroad import cameraview as camera


class ViewWithoutResources(camera.CameraView):
  def close(self):
    pass


def test_constructor_caches_once_across_repeated_switches():
  with patch.object(camera, 'COMMA_HARDWARE', False), \
       patch.object(camera, 'VisionIpcClient', side_effect=lambda *a, **k: Mock()) as factory, \
       patch.object(camera.rl, 'load_shader_from_memory', return_value=Mock(id=0)), \
       patch.object(camera.rl, 'get_shader_location', return_value=1), \
       patch.object(camera.ui_state, 'add_offroad_transition_callback'):
    view = ViewWithoutResources('camera-cache-test', camera.VisionStreamType.VISION_STREAM_NARROW_ROAD)
  expected = len(list(camera.VisionStreamType))
  assert factory.call_count == expected
  identities = dict(view._clients)
  with patch.object(view, '_initialize_textures'):
    for _ in range(50):
      for stream in camera.VisionStreamType:
        view.switch_stream(stream)
        if view._switching:
          view._complete_switch()
        assert view.client is identities[stream]
  assert factory.call_count == expected


def test_new_drive_drains_cached_clients_and_cancels_pending_switch():
  view = ViewWithoutResources.__new__(ViewWithoutResources)
  view._clients = {stream: Mock() for stream in camera.VisionStreamType}
  view.client = view._clients[camera.VisionStreamType.VISION_STREAM_NARROW_ROAD]
  view.frame = Mock()
  view._target_client = Mock()
  view._target_stream_type = Mock()
  view._switching = True
  with patch.object(camera.ui_state, 'is_onroad', return_value=True):
    view._offroad_transition()
  assert view.frame is None and view._target_client is None
  assert view._target_stream_type is None and not view._switching
  for client in view._clients.values():
    client.recv.assert_called_once_with(timeout_ms=0)


@pytest.mark.parametrize('camera_type', [camera.CameraView, compact.CameraView])
def test_failed_egl_camera_startup_uses_padded_nv12_texture_uploads(camera_type):
  class View(camera_type):
    def close(self):
      pass

  with patch.object(camera, 'COMMA_HARDWARE', True), \
       patch.object(camera, 'init_egl', return_value=False), \
       patch.object(camera, 'VisionIpcClient', side_effect=lambda *a, **k: Mock(width=4, stride=8, height=2)), \
       patch.object(compact, 'VisionIpcClient', side_effect=lambda *a, **k: Mock(width=4, stride=8, height=2)), \
       patch.object(camera.rl, 'load_shader_from_memory', return_value=Mock(id=1)) as shader, \
       patch.object(camera.rl, 'get_shader_location', return_value=1), \
       patch.object(camera.ui_state, 'add_offroad_transition_callback'):
    view = View('camera-fallback-test', camera.VisionStreamType.VISION_STREAM_NARROW_ROAD)
  assert not view._use_egl
  assert 'sampler2D texture1' in shader.call_args.args[1]

  y = SimpleNamespace(width=8, height=2, id=1)
  uv = SimpleNamespace(width=4, height=1, id=2)
  data = memoryview(bytes(range(40)))
  with patch.object(camera.rl, 'load_texture_from_image', side_effect=[y, uv]):
    view._initialize_textures()
  view.frame = SimpleNamespace(uv_offset=24, data=data)
  with patch.object(camera.rl, 'update_texture') as upload, \
       patch.object(camera.rl, 'begin_shader_mode'), patch.object(camera.rl, 'end_shader_mode'), \
       patch.object(camera.rl, 'set_shader_value_texture'), patch.object(camera.rl, 'draw_texture_pro') as draw, \
       patch.object(view, '_update_texture_color_filtering'):
    view._render_textures(Mock(), Mock())
    assert upload.call_args_list[0].args == (y, data[:16])
    assert upload.call_args_list[1].args == (uv, data[24:32])
    draw.assert_called_once()

    upload.reset_mock()
    draw.reset_mock()
    view._texture_needs_update = True
    view.frame = SimpleNamespace(uv_offset=24, data=data[:31])
    view._render_textures(Mock(), Mock())
    upload.assert_not_called()
    draw.assert_not_called()


@pytest.mark.parametrize('camera_type', [camera.CameraView, compact.CameraView])
def test_successful_egl_keeps_external_texture_shader(camera_type):
  class View(camera_type):
    def close(self):
      pass

  placeholder = Mock(id=2)
  with patch.object(camera, 'COMMA_HARDWARE', True), \
       patch.object(camera, 'init_egl', return_value=True), \
       patch.object(camera, 'VisionIpcClient', return_value=Mock()), \
       patch.object(compact, 'VisionIpcClient', return_value=Mock()), \
       patch.object(camera.rl, 'load_shader_from_memory', return_value=Mock(id=1)) as shader, \
       patch.object(camera.rl, 'get_shader_location', return_value=1), \
       patch.object(camera.rl, 'gen_image_color'), \
       patch.object(camera.rl, 'unload_image'), \
       patch.object(camera.rl, 'load_texture_from_image', return_value=placeholder), \
       patch.object(camera.ui_state, 'add_offroad_transition_callback'):
    view = View('camera-egl-test', camera.VisionStreamType.VISION_STREAM_NARROW_ROAD)
  assert view._use_egl and view.egl_texture is placeholder
  assert 'samplerExternalOES texture0' in shader.call_args.args[1]
