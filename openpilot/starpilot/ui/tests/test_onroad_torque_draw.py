import numpy as np
from contextlib import contextmanager
from types import SimpleNamespace as NS
from dataclasses import replace
from unittest.mock import patch

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.ui import onroad_torque_draw
from openpilot.starpilot.ui.onroad_torque_draw import TorqueBarPainter
from openpilot.starpilot.ui.onroad_torque import TorqueBarWidget
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation


@contextmanager
def record_gpu():
  data = NS(uniforms={}, uploads=[], frames=[], loads=[], unloads=[])
  names, vertices, coordinates = {}, [], []

  def upload(shader, location, values, kind):
    name = next(name for name, value in names.items() if value == location)
    data.uniforms[name] = tuple(values[0:4])
    data.uploads.append((name, int(np.asarray(values).ctypes.data)))

  def finish():
    data.frames.append(NS(uniforms=dict(data.uniforms), vertices=tuple(vertices), coordinates=tuple(coordinates)))
    vertices.clear()
    coordinates.clear()

  with patch.object(rl, 'load_shader_from_memory', side_effect=lambda *_: data.loads.append(1) or NS(id=len(data.loads))), \
       patch.object(rl, 'get_shader_location', side_effect=lambda _, name: names.setdefault(name, len(names))), \
       patch.object(rl, 'set_shader_value', side_effect=upload), \
       patch.object(rl, 'begin_shader_mode'), patch.object(rl, 'end_shader_mode'), \
       patch.object(rl, 'rl_set_texture'), patch.object(rl, 'rl_begin'), \
       patch.object(rl, 'rl_tex_coord2f', side_effect=lambda x, y: coordinates.append((x, y))), \
       patch.object(rl, 'rl_vertex2f', side_effect=lambda x, y: vertices.append((x, y))), \
       patch.object(rl, 'rl_end', side_effect=finish), \
       patch.object(rl, 'unload_shader', side_effect=lambda shader: data.unloads.append(shader.id)), \
       patch.object(rl, 'is_window_ready', return_value=True), patch.object(rl, 'rl_get_shader_id_default', return_value=0):
    native = NS(begin_shader_mode=rl.begin_shader_mode, end_shader_mode=rl.end_shader_mode,
                set_shader_value=rl.set_shader_value, rl_set_texture=rl.rl_set_texture,
                rl_begin=rl.rl_begin, rl_tex_coord2f=rl.rl_tex_coord2f, rl_vertex2f=rl.rl_vertex2f, rl_end=rl.rl_end)
    with patch.object(onroad_torque_draw, 'native_rl', native):
      yield data


def test_small_torque_changes_reach_the_screen_without_angle_steps():
  painter = TorqueBarPainter()
  with record_gpu() as gpu:
    rect = rl.Rectangle(0, 0, 476, 240)
    painter.render(rect, .3, 1, 536, 7)
    painter.render(rect, .3001, 1, 536, 7)
  endpoints = [frame.uniforms['indicatorNormals'][2] for frame in gpu.frames]
  assert 0 < endpoints[1] - endpoints[0] < .00002
  assert all(frame.vertices == frame.coordinates for frame in gpu.frames)


@pytest.mark.parametrize('rect,width,cap,torque,expected', [
  ((0, 0, 476, 240), 536, 7, .3, (246, 1286.134328, 1071.888060, 6.216418)),
  ((0, 0, 476, 240), 536, 7, 1, (246, 1282.582090, 1090.537313, 24.865672)),
  ((30, 30, 2100, 1020), 2160, 21, .3, (1088, 5917.430556, 4987.256944, 28.923611)),
  ((30, 30, 2100, 1020), 2160, 21, 1, (1088, 5900.902778, 5074.027778, 115.694444)),
  ((30, 30, 1800, 1020), 2160, 21, .3, (938, 5222.083333, 4274.791667, 24.791667)),
  ((30, 30, 1800, 1020), 2160, 21, 1, (938, 5207.916667, 4349.166667, 99.166667)),
])
def test_native_arc_dimensions_and_opacities_match_the_original_renderer(rect, width, cap, torque, expected):
  with record_gpu() as gpu:
    TorqueBarPainter().render(rl.Rectangle(*rect), torque, 1, width, cap)
  frame, = gpu.frames
  assert frame.uniforms['geometry'] == pytest.approx(expected, abs=.001)
  assert tuple(round(value * 255) for value in frame.uniforms['style'][1:]) == ((63, 229, 229) if torque == .3 else (127, 229, 0))


@pytest.mark.parametrize('rect,width,cap', [((0, 0, 476, 240), 536, 7), ((30, 30, 2100, 1020), 2160, 21)])
@pytest.mark.parametrize('torque,warmth', [(.5, 0), (.75, 0), (.875, .5), (1, 1)])
def test_gradient_colors_and_direction_for_both_ui_sizes(rect, width, cap, torque, warmth):
  painter = TorqueBarPainter()
  with record_gpu() as gpu:
    painter.render(rl.Rectangle(*rect), torque, 1, width, cap)
    painter.render(rl.Rectangle(*rect), -torque, 1, width, cap)
  right, left = [frame.uniforms['gradient'] for frame in gpu.frames]
  assert right[0] > 0
  assert left[0] == pytest.approx(-right[0])
  assert left[1:] == pytest.approx(right[1:])
  assert (1, right[1], right[3]) == pytest.approx((1, 1 - warmth * 140 / 255, 1 - warmth))
  assert (1, right[1] + right[2], right[3]) == pytest.approx((1, 1 - warmth * 55 / 255, 1 - warmth))


def test_gradient_anchor_follows_the_bar_when_repositioned():
  painter = TorqueBarPainter()
  with record_gpu() as gpu:
    painter.render(rl.Rectangle(30, 30, 2100, 1020), 1, .6, 2160, 21)
    painter.render(rl.Rectangle(130, 80, 2100, 1020), 1, .6, 2160, 21, (7, -4))
  first, second = gpu.frames
  assert second.uniforms['gradient'] == pytest.approx(first.uniforms['gradient'])
  assert second.uniforms['geometry'][0] - first.uniforms['geometry'][0] == pytest.approx(107)
  assert second.uniforms['geometry'][1] - first.uniforms['geometry'][1] == pytest.approx(46)
  assert second.uniforms['style'] == pytest.approx(first.uniforms['style'])


def test_shader_and_uniform_buffers_are_reused_and_released_before_reload():
  painter = TorqueBarPainter()
  rect = rl.Rectangle(0, 0, 476, 240)
  with record_gpu() as gpu:
    painter.close()
    painter.render(rect, .3, 1, 536, 7)
    first_uploads = list(gpu.uploads)
    painter.render(rect, .3, 1, 536, 7)
    assert gpu.uploads == first_uploads
    painter.render(rect, .3001, 1, 536, 7)
    pointers = dict(first_uploads)
    assert all(pointer == pointers[name] for name, pointer in gpu.uploads)
    assert len(gpu.loads) == 1
    painter.close()
    painter.close()
    assert gpu.unloads == [1]
    painter.render(rect, .3, 1, 536, 7)
    assert len(gpu.loads) == 2
    assert len(gpu.uploads) == len(first_uploads) * 2 + 1
    painter.close()
    assert gpu.unloads == [1, 2]


def test_invisible_bar_does_not_allocate_graphics_resources():
  with record_gpu() as gpu:
    TorqueBarPainter().render(rl.Rectangle(0, 0, 476, 240), .3, 0, 536, 7)
  assert not gpu.loads and not gpu.frames


@pytest.mark.parametrize('default_shader_id', [0, 1])
def test_shader_failure_never_paints_the_bounding_rectangle(default_shader_id):
  with record_gpu() as gpu, patch.object(rl, 'get_shader_location', return_value=-1), \
       patch.object(rl, 'rl_get_shader_id_default', return_value=default_shader_id):
    with pytest.raises(RuntimeError, match='Torque bar shader unavailable'):
      TorqueBarPainter().render(rl.Rectangle(0, 0, 476, 240), .3, 1, 536, 7)
  assert not gpu.frames
  assert gpu.unloads == ([1] if default_shader_id == 0 else [])


@pytest.mark.parametrize('width,rect', [
  (536, (0, 0, 476, 240)), (2160, (30, 30, 1800, 1020)), (1860, (30, 30, 1800, 1800)),
])
@pytest.mark.parametrize('utilization', [-1, -.875, -.75, -.3, .3, .75, .875, 1])
def test_demand_preserves_original_colors_geometry_and_direction(width, rect, utilization):
  state = OnroadState(True, True, 15.0, 80.0, SpeedLimitObservation(), lateral_active=True, torque_source_available=True,
                      torque_utilization=utilization, torque_is_demand=True)
  frames = []
  for demand_only in (True, False):
    widget = TorqueBarWidget()
    with record_gpu() as gpu:
      for _ in range(120):
        widget.render(rl.Rectangle(*rect), replace(state, torque_is_demand=demand_only), width)
      frames.append(gpu.frames[-1])
  demand, torque = frames
  warmth = max(0.0, min(1.0, (abs(widget._torque_filter.x) - .75) * 4))
  assert demand.uniforms['gradient'][1:] == pytest.approx((1 - warmth * 140 / 255, warmth * 85 / 255, 1 - warmth))
  assert demand.uniforms == torque.uniforms
  assert demand.uniforms['indicatorNormals'][0 if utilization < 0 else 2] * utilization > 0
  assert demand.uniforms['indicatorNormals'] == torque.uniforms['indicatorNormals']
  assert demand.vertices == torque.vertices
