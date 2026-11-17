from unittest.mock import Mock, patch
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
