import numpy as np
"""State freshness and native uniform wiring for the selected Technicolor grade."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from openpilot.selfdrive.ui.onroad import cameraview as large
from openpilot.selfdrive.ui.mici.onroad import cameraview as compact
from openpilot.starpilot.ui.camera_color import road_color_strength
from openpilot.starpilot.ui.tests.test_runtime_snapshot import NOW, ui_fake


def camera_ui(lateral=False, longitudinal=False, enabled=False):
  ui = ui_fake()
  ui.display_time_ns = lambda: NOW
  ui.status = large.ui_state.status
  ui.sm.put('carControl', NS(latActive=lateral, longActive=longitudinal))
  ui.sm.put('selfdriveState', NS(enabled=enabled))
  return ui


@pytest.mark.parametrize('lateral,longitudinal,enabled,expected', [
  (False, False, False, 0.2), (True, False, False, 0.45),
  (False, True, False, 1.0), (True, True, False, 1.0),
  (False, False, True, 1.0), (True, False, True, 1.0),
  (False, True, True, 1.0), (True, True, True, 1.0),
])
def test_off_lateral_only_longitudinal_and_enabled_modes(lateral, longitudinal, enabled, expected):
  # Enabled covers stock longitudinal control and temporary overrides, too.
  assert road_color_strength(camera_ui(lateral, longitudinal, enabled)) == expected


@pytest.mark.parametrize('failure', ['offroad', 'stale', 'future', 'invalid', 'dead', 'previous_drive', 'unseen'])
def test_old_or_unavailable_control_falls_back_to_faint_color(failure):
  ui = camera_ui(True, True, True)
  if failure == 'offroad':
    ui.started = False
  elif failure == 'stale':
    ui.sm.logMonoTime['carControl'] = NOW - 200_000_001
  elif failure == 'future':
    ui.sm.logMonoTime['carControl'] = NOW + 1
  elif failure == 'invalid':
    ui.sm.valid['carControl'] = False
  elif failure == 'dead':
    ui.sm.alive['carControl'] = False
  elif failure == 'previous_drive':
    ui.sm.recv_frame['carControl'] = ui.started_frame
  else:
    del ui.sm.logMonoTime['carControl']
  assert road_color_strength(ui) == 0.2


def test_stale_enabled_status_cannot_promote_lateral_only_to_full_color():
  ui = camera_ui(True, False, True)
  assert road_color_strength(ui) == 1.0
  ui.sm.logMonoTime['selfdriveState'] = NOW - 200_000_001
  assert road_color_strength(ui) == 0.45
  ui.sm['carControl'].latActive = False
  assert road_color_strength(ui) == 0.2


def test_replay_uses_recorded_clock_and_rejects_invalid_seek_sample():
  from openpilot.tools.replay.display_clock import ClockSample
  ui = camera_ui(True)
  ui.sm.replay_sample = ClockSample(NOW, None, NOW + 10_000_000_000, epoch=1, valid=True, paused=True)
  assert road_color_strength(ui) == 0.45
  ui.sm.replay_sample = ClockSample(None, None, NOW + 10_000_000_000, epoch=2, valid=False)
  assert road_color_strength(ui) == 0.2


@pytest.mark.parametrize('module', [large, compact])
@pytest.mark.parametrize('stream', [large.VisionStreamType.VISION_STREAM_NARROW_ROAD,
                                   large.VisionStreamType.VISION_STREAM_WIDE_ROAD,
                                   large.VisionStreamType.VISION_STREAM_CABIN])
@pytest.mark.parametrize('lateral,longitudinal,expected', [(False, False, 0.2), (True, False, 0.45), (True, True, 1.0)])
def test_both_native_renderers_send_float_blend_and_current_stream_flags(module, stream, lateral, longitudinal, expected, monkeypatch):
  ui = camera_ui(lateral, longitudinal)
  monkeypatch.setattr(module, 'ui_state', ui)
  camera = object.__new__(module.CameraView)
  camera.close = Mock()
  camera.shader = object()
  camera._stream_type = stream
  camera._color_strength_loc = 10
  camera._color_strength_val = np.zeros(1, dtype=np.float32)
  camera._road_camera_loc, camera._enhance_driver_loc, camera._engaged_loc = 11, 12, 13
  camera._road_camera_val = np.zeros(1, dtype=np.int32)
  camera._enhance_driver_val = np.zeros(1, dtype=np.int32)
  camera._engaged_val = np.zeros(1, dtype=np.int32)
  calls = {}
  monkeypatch.setattr(module.rl, 'set_shader_value', lambda _shader, location, value, kind: calls.update({location: (value[0], kind)}))
  camera._update_texture_color_filtering()
  assert calls[10] == (pytest.approx(expected), module.rl.ShaderUniformDataType.SHADER_UNIFORM_FLOAT)
  if module is large:
    assert calls[11][0] == int(stream in large.ROAD_STREAMS)
  else:
    assert calls[12][0] == int(stream == large.VisionStreamType.VISION_STREAM_CABIN)
