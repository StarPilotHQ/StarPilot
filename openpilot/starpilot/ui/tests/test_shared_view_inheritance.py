from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest

from openpilot.selfdrive.ui.onroad import cameraview as large_camera
from openpilot.selfdrive.ui.mici.onroad import cameraview as small_camera
from openpilot.selfdrive.ui.onroad import model_renderer as large_model
from openpilot.selfdrive.ui.mici.onroad import model_renderer as small_model


@pytest.mark.parametrize("module", [large_camera, small_camera])
def test_shared_camera_constructor_preserves_display_specific_clients_and_uniforms(module, monkeypatch):
  ui = NS(add_offroad_transition_callback=Mock())
  clients = []
  def client(name, stream, conflate):
    clients.append((name, stream, conflate))
    return NS(stream=stream)
  for target in (large_camera, small_camera):
    monkeypatch.setattr(target, "COMMA_HARDWARE", False)
    monkeypatch.setattr(target, "ui_state", ui)
    monkeypatch.setattr(target, "VisionIpcClient", client)
  monkeypatch.setattr(module.CameraView, "close", lambda self: None)
  shader = Mock(return_value=NS(id=1))
  monkeypatch.setattr(module.rl, "load_shader_from_memory", shader)
  monkeypatch.setattr(module.rl, "get_shader_location", lambda *_args: 1)
  stream = module.VisionStreamType.VISION_STREAM_CABIN
  camera = module.CameraView("camerad", stream)
  assert shader.call_args.args[1] == module.FRAME_FRAGMENT_SHADER
  assert camera.stream_type == stream
  assert camera.frame is None and camera._color_strength_val.tolist() == [0.0]
  assert camera.texture_y is None and camera.texture_uv is None
  ui.add_offroad_transition_callback.assert_called_once_with(camera._offroad_transition)
  if module is small_camera:
    assert clients == [("camerad", stream, True)]
    assert camera._engaged_val.tolist() == [1]
    assert camera._enhance_driver_val.tolist() == [1]
  else:
    assert len(clients) == len(list(module.VisionStreamType))
    assert camera._road_camera_val.tolist() == [0]
  camera.shader.id = 0


def test_shared_model_constructor_keeps_distinct_lead_types_and_native_geometry():
  assert issubclass(small_model.ModelRenderer, large_model.ModelGeometry)
  assert issubclass(large_model.ModelRenderer, large_model.ModelGeometry)
  for module in (large_model, small_model):
    renderer = module.ModelRenderer()
    assert all(isinstance(lead, module.LeadVehicle) for lead in renderer._lead_vehicles)
    assert len(renderer._lane_lines) == 4 and len(renderer._road_edges) == 2
    assert renderer._path.raw_points.shape == (0, 3)
    assert renderer._path.raw_points.dtype == np.float32
    renderer.set_transform(np.eye(3))
    np.testing.assert_array_equal(renderer._car_space_transform, np.eye(3, dtype=np.float32))
  assert small_model.ModelRenderer.set_transform is large_model.ModelRenderer.set_transform
  assert small_model.ModelRenderer._update_raw_points is large_model.ModelRenderer._update_raw_points
  assert small_model.ModelRenderer._draw_lane_lines is not large_model.ModelRenderer._draw_lane_lines
