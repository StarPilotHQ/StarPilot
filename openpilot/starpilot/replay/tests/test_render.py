from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
import uuid

import pytest

from openpilot.starpilot.replay import render
from openpilot.starpilot.replay.render import Compositor, Engine, camera_options, frame_matches, parse_wall_offset
from openpilot.starpilot.ui.presentation import Profile
from openpilot.tools.replay.display_clock import ClockSample


def test_camera_selection_uses_selected_sources_and_current_preferences():
  sources = {'fcamera.hevc', 'ecamera.hevc', 'dcamera.hevc'}
  assert camera_options('compact', {}, sources) == (False, False)
  assert camera_options('large', {}, sources) == (True, False)
  assert camera_options('compact', {'CameraView': b'0'}, sources) == (True, False)
  assert camera_options('compact', {'CameraView': b'1'}, sources) == (False, True)
  assert camera_options('compact', {'CameraView': b'3', 'DriverCamera': b'1'}, sources) == (True, True)
  assert camera_options('compact', {'CameraView': b'4', 'DriverCamera': b'1'}, sources) == (False, False)
  assert camera_options('large', {'PIPPreviewEnabled': b'1'}, sources) == (True, True)
  assert camera_options('compact', {'CameraView': b'1', 'PIPPreviewEnabled': b'1'}, {'qcamera.ts'}) == (False, False)


@pytest.mark.parametrize('profile', ('large', 'compact'))
@pytest.mark.parametrize('target', ('native', 'web'))
@pytest.mark.parametrize('device,pinned', (('tici', True), ('tizi', True), ('mici', True), ('pc', False), ('unknown', False)))
def test_native_worker_leaves_ui_cpu_before_starting_replay(tmp_path, monkeypatch, profile, target, device, pinned):
  from openpilot.common import hardware, realtime

  pinned = pinned and target == 'native'
  for key in ('STARPILOT_PROJECTION_READ_ONLY', 'BIG', 'SCALE'):
    monkeypatch.setenv(key, '')
  calls = []
  monkeypatch.setattr(render, 'validate_context', lambda path: calls.append('validate') or path)
  monkeypatch.setattr(hardware.HARDWARE, 'get_device_type', lambda: device)
  affinity = Mock(side_effect=lambda cores: calls.append('affinity'))
  monkeypatch.setattr(realtime, 'set_core_affinity', affinity)

  def prepare(path):
    calls.append('prepare')
    raise RuntimeError('stop before worker preparation')
  monkeypatch.setattr(render, 'seed_display', prepare)
  with pytest.raises(RuntimeError, match='stop before worker preparation'):
    render.run(tmp_path, 'recorded-route', tmp_path, target, profile, Mock())
  assert calls == (['validate', 'affinity', 'prepare'] if pinned else ['validate', 'prepare'])
  if pinned:
    affinity.assert_called_once_with([0, 1, 2, 3, 4, 5])
  else:
    affinity.assert_not_called()


@pytest.mark.parametrize('preload', (False, True))
def test_engine_passes_selected_segment_to_machine_replay(tmp_path, monkeypatch, preload):
  process = Mock()
  monkeypatch.setattr(render.subprocess, 'Popen', Mock(return_value=process))
  monkeypatch.setattr(render.threading, 'Thread', Mock())
  Engine('recorded-route', tmp_path, wide=False, cabin=False, start_segment=2, preload_seek=preload)
  command = render.subprocess.Popen.call_args.args[0]
  assert command[command.index('--start-segment') + 1] == '2'
  assert ('--preload-seek' in command) is preload


def test_camera_matches_recorded_identity_across_distinct_clock_domains():
  state = SimpleNamespace(frameId=5, timestampSof=9_000_000_000, timestampEof=9_010_000_000)
  client = SimpleNamespace(frame_id=5, timestamp_sof=9_000_000_000, timestamp_eof=9_010_000_000)
  frame = SimpleNamespace(frame_id=5)
  stamp, boot_now = 9_020_000_000, 9_050_000_000
  assert frame_matches(client, frame, state, stamp, boot_now)
  assert not frame_matches(client, frame, state, stamp, 1_050_000_000)  # MONOTONIC is the wrong domain.
  assert not frame_matches(client, frame, state, stamp, stamp - 1)
  assert not frame_matches(client, frame, state, stamp, stamp + 500_000_001)
  assert not frame_matches(client, None, state, stamp, boot_now)
  assert not frame_matches(client, frame, SimpleNamespace(frameId=5), stamp, boot_now)
  assert not frame_matches(client, frame, state, stamp, None)
  client.frame_id = 4
  assert not frame_matches(client, frame, state, stamp, boot_now)
  client.frame_id, client.timestamp_sof = 5, 9_000_000_001
  assert not frame_matches(client, frame, state, stamp, boot_now)


def test_paused_same_route_clock_never_advances_filters_or_gpu():
  core: Any = object.__new__(Compositor)
  core.ui = SimpleNamespace(replay_sample=ClockSample(100, 100, 100, epoch=2, paused=True, valid=True))
  core.last_route_frame = (2, 100)
  core.context = Mock()
  assert core.draw(2, 0, 9_000_000_000) is False
  core.context.begin_frame.assert_not_called()


@pytest.mark.parametrize('sample', [None, SimpleNamespace(now_ns=None)])
def test_missing_route_clock_never_submits_a_gpu_frame(sample):
  core: Any = object.__new__(Compositor)
  core.ui = SimpleNamespace(replay_sample=sample)
  core.context = Mock()
  assert core.draw(1, 0., 100) is False
  core.context.begin_frame.assert_not_called()


@pytest.mark.parametrize('frame_ready', (False, True))
def test_recorded_camera_preview_does_not_require_initial_device_state(frame_ready):
  @dataclass
  class Onroad:
    camera_available: bool = False
    viewport_width: int = 0

  core: Any = object.__new__(Compositor)
  core.ui = SimpleNamespace(replay_sample=ClockSample(100, 100, 100, epoch=2, valid=True),
                            sm=SimpleNamespace(seen={'extrinsicsCalibration': False}))
  core.last_route_frame = None
  core.context = Mock()
  core.adapter = Mock()
  core.adapter.build.return_value = SimpleNamespace(onroad=Onroad())
  core._primary_matches = Mock(return_value=frame_ready)
  core._optional_placeholder_ready = Mock(return_value=False)
  core.rl = Mock()
  core.gui = SimpleNamespace(_render_prepare=[], _frame=0)
  core.content = Mock()
  core.scale = 1
  core.profile = Profile.LARGE
  core.view = Mock()
  core.view.render.side_effect = lambda state: setattr(core, 'primary_ready', state.camera_available)
  core.output = None
  core.converter = Mock()
  core.readback = Mock()
  assert core.draw(2, 0, 9_000_000_000) is frame_ready
  if not frame_ready:
    core.context.begin_frame.assert_not_called()
    core.adapter.build.assert_not_called()
    core.view.render.assert_not_called()
    core.readback.start.assert_not_called()
    return
  drawn = core.view.render.call_args.args[0]
  assert drawn.camera_available is frame_ready
  assert core.adapter.build.return_value.onroad.camera_available is False
  assert core.readback.start.call_count == int(frame_ready)


@pytest.fixture(params=(Profile.LARGE, Profile.COMPACT))
def draw_core(request):
  from openpilot.cereal.visionipc import VisionStreamType
  if request.param == Profile.COMPACT:
    from openpilot.selfdrive.ui.mici.onroad.cameraview import CameraView
  else:
    from openpilot.selfdrive.ui.onroad.cameraview import CameraView
  from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation

  class Messages(dict):
    pass

  idx = SimpleNamespace(frameId=5, timestampSof=9_000_000_000, timestampEof=9_010_000_000)
  sm = Messages(narrowRoadEncodeIdx=idx, wideRoadEncodeIdx=idx)
  sm.seen = dict.fromkeys(('narrowRoadEncodeIdx', 'wideRoadEncodeIdx', 'modelV2', 'radarState', 'carParams',
                           'carState', 'extrinsicsCalibration'), True)
  sm.valid = dict(sm.seen)
  sm.logMonoTime = dict.fromkeys(sm, 9_020_000_000)
  sm.logMonoTime['extrinsicsCalibration'] = 1_000_000_000
  sm.recv_frame = dict.fromkeys(sm.seen, 2)
  sm.updated = {'modelV2': True}
  client = Mock(frame_id=5, timestamp_sof=idx.timestampSof, timestamp_eof=idx.timestampEof)
  frame = SimpleNamespace(frame_id=5)
  client.recv.return_value = frame
  core: Any = object.__new__(Compositor)
  core.profile = request.param
  core.wide = core.cabin = False
  core.ui = SimpleNamespace(sm=sm, replay_sample=ClockSample(1_000_000_000, 9_050_000_000, 100, epoch=2, valid=True))
  core.camera = Mock(client=client, frame=frame, stream_type=VisionStreamType.VISION_STREAM_NARROW_ROAD)
  core.camera._switching = False
  core.camera._ensure_connection.return_value = True
  core.camera._poll_camera.side_effect = lambda: CameraView._poll_camera(core.camera)
  core.last_route_frame = None
  core.render_receipts = {'modelV2': 1}
  core.context, core.adapter = Mock(), Mock()
  core.adapter.build.return_value = SimpleNamespace(onroad=OnroadState(True, False, 15., 80., SpeedLimitObservation()))
  core.rl, core.content = Mock(), Mock()
  core.gui = SimpleNamespace(_render_prepare=[Mock()], _frame=0)
  core.scale = 1
  core.view, core.converter, core.readback = Mock(), Mock(), Mock()
  core.output = None

  def paint(*args, **kwargs):
    assert kwargs['poll_camera'] is False
  core.camera.render_camera_model_layer.side_effect = paint

  def render_view(state):
    # The real OnroadView enters its camera layer only with this availability flag.
    if state.camera_available:
      core._camera_layer(Mock(), state)
  core.view.render.side_effect = render_view
  return core


@pytest.mark.parametrize('change', ('ahead', 'behind', 'missing_frame', 'timestamp', 'stale_state', 'unseen', 'invalid'))
def test_known_camera_rejection_skips_preparation_and_preserves_updates(draw_core, change):
  core = draw_core
  if change in ('ahead', 'behind'):
    core.camera.client.frame_id = 6 if change == 'ahead' else 4
  elif change == 'missing_frame':
    core.camera.frame = None
  elif change == 'timestamp':
    core.camera.client.timestamp_sof += 1
  elif change == 'stale_state':
    core.ui.sm.logMonoTime['narrowRoadEncodeIdx'] = 8_000_000_000
  else:
    getattr(core.ui.sm, 'seen' if change == 'unseen' else 'valid')['narrowRoadEncodeIdx'] = False
  assert core.draw(2, 0., 100) is False
  core.context.begin_frame.assert_not_called()
  core.adapter.build.assert_not_called()
  core.gui._render_prepare[0].assert_not_called()
  core.view.render.assert_not_called()
  core.readback.start.assert_not_called()
  assert core.last_route_frame is None and core.gui._frame == 0
  assert core.render_receipts == {'modelV2': 1} and core.ui.sm.updated['modelV2']


@pytest.mark.parametrize('sample', (ClockSample(100, 100, 100, epoch=2),
                                  ClockSample(100, 100, 100, epoch=3, valid=True)))
def test_invalid_or_other_epoch_clock_never_advances_draw(draw_core, sample):
  core = draw_core
  core.ui.replay_sample = sample
  assert core.draw(2, 0., 100) is False
  core.context.begin_frame.assert_not_called()
  core.adapter.build.assert_not_called()


def test_camera_poll_precedes_message_snapshot():
  core: Any = object.__new__(Compositor)
  order = []
  core.ui = SimpleNamespace(update=lambda: order.append('messages'), replay_sample=ClockSample(1, 1, 1, epoch=2, valid=True))
  core.epoch, core.cabin = 2, False
  core._receive_primary = lambda: order.append('camera')
  assert core.update() is core.ui.replay_sample
  assert order == ['camera', 'messages']


@pytest.mark.parametrize('profile', ('onroad', 'mici.onroad'))
def test_camera_paint_does_not_receive_or_switch_selected_frame(monkeypatch, profile):
  import importlib
  import numpy as np
  from openpilot.starpilot.ui.raylib import rl
  from openpilot.cereal.visionipc import VisionStreamType
  module = importlib.import_module(f'openpilot.selfdrive.ui.{profile}.cameraview')
  monkeypatch.setattr(module, 'COMMA_HARDWARE', True)
  camera = object.__new__(module.CameraView)
  camera.close = Mock()
  camera.client = Mock()
  camera.frame = SimpleNamespace(frame_id=5, width=100, height=50)
  frame = camera.frame
  camera._stream_type = VisionStreamType.VISION_STREAM_NARROW_ROAD
  camera._switching = True
  camera._handle_switch = Mock()
  camera._ensure_connection = Mock()
  camera._calc_frame_matrix = Mock(return_value=np.eye(3))
  camera._render_egl = Mock()
  camera._render(rl.Rectangle(0, 0, 100, 50), poll_camera=False)
  assert camera.frame is frame
  camera.client.recv.assert_not_called()
  camera._handle_switch.assert_not_called()
  camera._ensure_connection.assert_not_called()
  camera._render_egl.assert_called_once()


def test_compact_driver_switch_waits_for_camera_poll(monkeypatch):
  from openpilot.selfdrive.ui.mici.onroad import augmented_road_view as module
  camera = object.__new__(module.AugmentedRoadView)
  camera.close = Mock()
  camera._stream_type = module.NARROW_ROAD_CAM
  camera._target_stream_type = module.DRIVER_CAM
  camera._switching = True
  camera.available_streams = [module.DRIVER_CAM]
  camera._ensure_connection = Mock()
  camera._handle_switch = Mock()
  camera._switch_stream_if_needed = Mock(return_value=module.DRIVER_CAM)
  monkeypatch.setattr(module.ui_state, 'started', True)
  monkeypatch.setattr(module.rl, 'draw_rectangle_rec', Mock())
  monkeypatch.setattr(module, 'gui_label', Mock())
  camera.render_camera_model_layer(Mock(), camera_view=module.CAMERA_VIEW_DRIVER, poll_camera=False)
  camera._handle_switch.assert_not_called()
  camera._ensure_connection.assert_not_called()


@pytest.mark.parametrize('replacement_id', (5, 6))
def test_selected_camera_is_painted_before_next_poll(draw_core, replacement_id):
  core = draw_core
  def receive(**kwargs):
    core.camera.client.frame_id = replacement_id
    return SimpleNamespace(frame_id=replacement_id)
  core.camera.client.recv.side_effect = receive
  assert core.draw(2, 0., 100)
  core.camera.client.recv.assert_not_called()
  core._receive_primary()
  core.ui.replay_sample = replace(core.ui.replay_sample, now_ns=1_050_000_000)
  assert core.draw(2, .05, 150) is (replacement_id == 5)
  core.camera.client.recv.assert_called_once_with(timeout_ms=0)
  assert core.readback.start.call_count == 1 + int(replacement_id == 5)


def test_optional_placeholder_still_allows_later_camera_recovery(draw_core, monkeypatch):
  from openpilot.cereal.visionipc import VisionStreamType
  core = draw_core
  monkeypatch.setattr('openpilot.system.ui.widgets.label.gui_label', Mock())
  core.camera.stream_type = VisionStreamType.VISION_STREAM_WIDE_ROAD
  core.camera.client.frame_id = 4
  def receive(**kwargs):
    core.camera.client.frame_id = 5
    return SimpleNamespace(frame_id=5)
  core.camera.client.recv.side_effect = receive
  assert core.draw(2, 0., 100)
  core.camera.client.recv.assert_not_called()
  core._receive_primary()
  core.ui.replay_sample = replace(core.ui.replay_sample, now_ns=1_050_000_000)
  assert core.draw(2, .05, 150)
  core.camera.client.recv.assert_called_once_with(timeout_ms=0)
  assert core.readback.start.call_count == 2


def test_selected_buffer_reuse_still_fails_final_identity_check(draw_core):
  core = draw_core
  core.camera.render_camera_model_layer.side_effect = lambda *args, **kwargs: setattr(core.camera.frame, 'frame_id', 6)
  assert not core.draw(2, 0., 100)
  core.readback.start.assert_not_called()


def test_eligible_retained_camera_draws_fresh_hud_and_route_time(draw_core):
  core = draw_core
  frame = core.camera.frame
  core.camera.client.recv.return_value = None
  assert core.draw(2, 0., 100)
  core.ui.replay_sample = replace(core.ui.replay_sample, now_ns=1_050_000_000)
  core.adapter.build.return_value.onroad = replace(core.adapter.build.return_value.onroad, speed_mps=20.)
  assert core.draw(2, .05, 150)
  core.ui.replay_sample = replace(core.ui.replay_sample, now_ns=1_100_000_000)
  assert core.draw(2, .1, 200)  # Animation advances even without another source update.
  assert core.camera.frame is frame
  assert core.view.render.call_args.args[0].speed_mps == 20.
  assert [call.args[0] for call in core.context.begin_frame.call_args_list] == [1., 1.05, 1.1]
  assert core.gui._frame == 3


def test_rejected_camera_can_match_next_update_without_losing_model_receipt(draw_core):
  core = draw_core
  core.camera.client.frame_id = 6
  core.camera.frame = SimpleNamespace(frame_id=6)
  assert not core.draw(2, 0., 100)
  core.ui.sm['narrowRoadEncodeIdx'].frameId = 6
  def consume_model(*args, **kwargs):
    assert core.ui.sm.updated['modelV2']
    assert kwargs['poll_camera'] is False
  core.camera.client.recv.return_value = None
  core.camera.render_camera_model_layer.side_effect = consume_model
  assert core.draw(2, 0., 100)
  assert core.render_receipts['modelV2'] == core.ui.sm.recv_frame['modelV2']
  assert core.context.begin_frame.call_count == 1


def test_optional_missing_camera_still_publishes_existing_placeholder(draw_core, monkeypatch):
  from openpilot.cereal.visionipc import VisionStreamType
  core = draw_core
  label = Mock()
  monkeypatch.setattr('openpilot.system.ui.widgets.label.gui_label', label)
  core.camera.stream_type = VisionStreamType.VISION_STREAM_WIDE_ROAD
  core.camera.client.frame_id = 4
  core.camera.client.recv.return_value = None
  assert core.draw(2, 0., 100)
  label.assert_called_once()
  core.readback.start.assert_called_once()


@pytest.mark.parametrize('camera_view', (1, 3, 4))
@pytest.mark.parametrize('draw_core', (Profile.COMPACT,), indirect=True)
def test_compact_hidden_and_unrecorded_camera_keep_selected_frame(draw_core, camera_view, monkeypatch):
  core = draw_core
  core.adapter.build.return_value.onroad = replace(core.adapter.build.return_value.onroad, appearance=replace(
    core.adapter.build.return_value.onroad.appearance, camera_view=camera_view))
  monkeypatch.setattr('openpilot.system.ui.widgets.label.gui_label', Mock())
  core._receive_primary()
  assert core.draw(2, 0., 100)
  core.camera.client.recv.assert_called_once_with(timeout_ms=0)
  core.camera.render_camera_model_layer.assert_not_called()


@pytest.mark.parametrize('profile', ('onroad', 'mici.onroad'))
@pytest.mark.parametrize('replay', (False, True))
def test_camera_layers_allow_recorded_preview_and_preserve_live_start_gate(monkeypatch, profile, replay):
  import importlib
  from openpilot.cereal.visionipc import VisionStreamType
  module = importlib.import_module(f'openpilot.selfdrive.ui.{profile}.augmented_road_view')
  monkeypatch.setattr(module.ui_state, 'started', False)
  monkeypatch.setattr(module.ui_state, 'replay_clock', object() if replay else None)
  measure = Mock()
  monkeypatch.setattr(module.gui_app, 'measure_frame_phase', measure)
  camera = object.__new__(module.AugmentedRoadView)
  camera.close = Mock()
  camera._switch_stream_if_needed = Mock(return_value=VisionStreamType.VISION_STREAM_NARROW_ROAD)
  camera._update_calibration = Mock()
  camera._stream_type = VisionStreamType.VISION_STREAM_NARROW_ROAD
  camera._target_stream_type = None
  camera._switching = False
  camera.available_streams = set()
  camera.frame = None
  camera.model_renderer = Mock(road_style={})
  camera._model_renderer = Mock()
  camera.render_camera_model_layer(Mock())
  assert bool(measure.call_count) is replay
  assert camera._update_calibration.call_count == int(replay)


def test_worker_preserves_final_engine_error_after_exit(tmp_path, monkeypatch):
  session = tmp_path / ('a' * 32)
  session.mkdir(mode=0o700)
  monkeypatch.setenv('SP_REPLAY_CLOCK_PATH', str(tmp_path / 'display-clock'))
  for key in ('STARPILOT_PROJECTION_READ_ONLY', 'BIG', 'SCALE'):
    monkeypatch.setenv(key, '')
  monkeypatch.setattr(render.signal, 'signal', Mock())
  monkeypatch.setattr(render.threading, 'Thread', Mock())
  monkeypatch.setattr(render, 'validate_context', lambda path: path)
  monkeypatch.setattr(render, 'seed_display', lambda path: {})
  catalog = Mock()
  catalog.detail.return_value = {'playable': True, 'segments': []}
  monkeypatch.setattr('openpilot.starpilot.galaxy.route_inventory.RouteInventory', lambda path: catalog)
  monkeypatch.setattr(render, 'Compositor', lambda *args, **kwargs: Mock())
  engine = Mock(status={'state': 'preparing'}, error=None)
  engine.process.poll.return_value = 2
  def final_status(**kwargs):
    engine.status = {'state': 'failed', 'error': 'Recorded clock correlation is unavailable'}
  engine.reader.join.side_effect = final_status
  monkeypatch.setattr(render, 'Engine', lambda *args, **kwargs: engine)
  with pytest.raises(RuntimeError, match='Recorded clock correlation is unavailable'):
    render.run(session, 'recorded-route', tmp_path, 'native', 'large', Mock())
  engine.reader.join.assert_called_once_with(timeout=1)


@pytest.mark.parametrize('target', ('native', 'web'))
def test_engine_starts_before_renderer_and_closes_if_renderer_fails(tmp_path, monkeypatch, target):
  for key in ('STARPILOT_PROJECTION_READ_ONLY', 'BIG', 'SCALE'):
    monkeypatch.setenv(key, '')
  monkeypatch.setattr(render.signal, 'signal', Mock())
  monkeypatch.setattr(render, 'validate_context', lambda path: path)
  monkeypatch.setattr(render, 'seed_display', lambda path: {})
  catalog = Mock()
  catalog.detail.return_value = {'playable': True, 'segments': []}
  monkeypatch.setattr('openpilot.starpilot.galaxy.route_inventory.RouteInventory', lambda path: catalog)
  calls = []
  engine = Mock()
  def start_engine(*args, **kwargs):
    assert kwargs['preload_seek'] is True
    calls.append('engine')
    return engine
  def start_renderer(*args, **kwargs):
    calls.append('renderer')
    raise RuntimeError('renderer unavailable')
  monkeypatch.setattr(render, 'Engine', start_engine)
  monkeypatch.setattr(render, 'Compositor', start_renderer)
  with pytest.raises(RuntimeError, match='renderer unavailable'):
    render.run(tmp_path, 'recorded-route', tmp_path, target, 'large', Mock())
  assert calls == ['engine', 'renderer']
  engine.close.assert_called_once()


@pytest.mark.parametrize('profile', ('large', 'compact'))
def test_web_media_uses_owned_ram_namespace(tmp_path, monkeypatch, profile):
  from openpilot.starpilot.system.starport import hw_encoder

  session = tmp_path / uuid.uuid4().hex
  session.mkdir(mode=0o700)
  ipc = Path('/dev/shm') / ('msgq_replay-' + session.name[:24])
  ipc.mkdir(mode=0o700)
  for key in ('STARPILOT_PROJECTION_READ_ONLY', 'BIG', 'SCALE'):
    monkeypatch.setenv(key, '')
  monkeypatch.setattr(render.signal, 'signal', Mock())
  monkeypatch.setattr(render.threading, 'Thread', Mock())
  monkeypatch.setattr(render, 'validate_context', lambda path: path)
  monkeypatch.setattr(render, 'seed_display', lambda path: {})
  catalog = Mock()
  catalog.detail.return_value = {'playable': True, 'segments': []}
  monkeypatch.setattr('openpilot.starpilot.galaxy.route_inventory.RouteInventory', lambda path: catalog)
  engine = Mock(error=None, status={})
  engine.process.poll.return_value = None
  engine.resume.return_value = False
  monkeypatch.setattr(render, 'Engine', lambda *args, **kwargs: engine)
  core = Mock()
  core.update.side_effect = RuntimeError('end fixture after media creation')
  monkeypatch.setattr(render, 'Compositor', lambda *args, **kwargs: core)
  monkeypatch.setattr(hw_encoder, 'HardwareH264Encoder', Mock())
  factory = Mock(wraps=render.MediaStore)
  monkeypatch.setattr(render, 'MediaStore', factory)
  try:
    with pytest.raises(RuntimeError, match='end fixture'):
      render.run(session, 'recorded-route', tmp_path, 'web', profile, Mock())
    assert factory.call_args.args[:2] == (ipc / session.name, session.name)
    assert (ipc / session.name / 'media').is_dir()
    assert not (session / 'media').exists()
  finally:
    shutil.rmtree(ipc)


@pytest.mark.parametrize('profile', ('large', 'compact'))
@pytest.mark.parametrize('handoff', (False, True))
def test_published_readiness_survives_buffering_but_rejects_seeks_and_old_epochs(tmp_path, monkeypatch, profile, handoff):
  session = tmp_path / ('a' * 32)
  session.mkdir(mode=0o700)
  for key in ('STARPILOT_PROJECTION_READ_ONLY', 'BIG', 'SCALE'):
    monkeypatch.setenv(key, '')
  frame_path = Path('/dev/shm') / ('msgq_replay-' + session.name[:24]) / 'frame'
  monkeypatch.setenv('SP_REPLAY_CLOCK_PATH', str(frame_path.with_name('display-clock')))
  monkeypatch.setattr(render.signal, 'signal', Mock())
  monkeypatch.setattr(render.threading, 'Thread', Mock())
  monkeypatch.setattr(render, 'validate_context', lambda path: path)
  monkeypatch.setattr(render, 'seed_display', lambda path: {})
  catalog = Mock()
  catalog.detail.return_value = {'playable': True, 'segments': []}
  monkeypatch.setattr('openpilot.starpilot.galaxy.route_inventory.RouteInventory', lambda path: catalog)
  publisher = Mock(path=frame_path)
  publisher_factory = Mock(return_value=publisher)
  monkeypatch.setattr(render, 'ReplayFramePublisher', publisher_factory)
  times, step = (1, 1.01, 1.11, 1.31, 1.61, 10, 10.2, 10.4, 10.6, 10.8), [0]
  monkeypatch.setattr(render.time, 'monotonic', lambda: times[step[0]])
  monkeypatch.setattr(render.time, 'monotonic_ns', lambda: round(times[step[0]] * 1e9))
  engine = Mock(error=None, status={'state': 'playing', 'paused': False, 'epoch': 1, 'position': 0,
                                   'routeStartNs': 100, 'start': 0, 'end': 20, 'lastRequestId': 'a'})
  engine.process.poll.return_value = None
  engine.seek_request = 'a'
  engine.resume.side_effect = lambda epoch: handoff and step[0] in (2, 3)
  monkeypatch.setattr(render, 'Engine', lambda *args, **kwargs: engine)
  core = Mock(cabin=False)
  core.camera = Mock(client=None, frame=object(), _target_client=None, _switching=False)
  core.pip_stream._client = None
  core.readback.pending = False
  core.ui.replay_needs_reprime = False
  available = dict.fromkeys(('modelV2', 'extrinsicsCalibration', 'longitudinalPlan'), True)
  core.ui.sm = SimpleNamespace(seen=available, valid=available, alive=available)
  core.update.side_effect = lambda: None if step[0] == 7 else SimpleNamespace(
    valid=step[0] not in (3, 6, 8), epoch=2 if step[0] == 6 else engine.status['epoch'], now_ns=100, boot_ns=100)
  core.finish.side_effect = lambda: nullcontext((b'pixels', (1, 0, 1_010_000_000)) if step[0] == 2 else None)
  core._primary_matches.return_value = core._optional_placeholder_ready.return_value = False
  attempts = []
  def draw(*args):
    attempts.append(times[step[0]])
    return step[0] == 1
  core.draw.side_effect = draw
  monkeypatch.setattr(render, 'Compositor', lambda *args, **kwargs: core)
  sleeps = []
  def next_loop(delay):
    sleeps.append(delay)
    if step[0] == len(times) - 1:
      engine.error = 'test complete'
      return
    step[0] += 1
    if step[0] == 5:
      engine.status.update(paused=True, state='paused')
    if step[0] == 8:
      engine.status['state'] = 'seeking'
    if step[0] == 9:
      engine.status.update(epoch=2, state='paused')
  monkeypatch.setattr(render.time, 'sleep', next_loop)
  states = []
  with pytest.raises(RuntimeError, match='test complete'):
    render.run(session, 'recorded-route', tmp_path, 'native', profile, lambda value: states.append(value))
  publisher_factory.assert_called_once_with(frame_path, session.name, profile, *render.SIZES['native'][profile])
  assert all(value['framePath'] == str(frame_path) for value in states)
  assert attempts[:2] == [1, 1.01]  # A failed draw retries before the 50 ms frame interval.
  assert [(value['state'], value['frameReady']) for value in states] == [
    ('preparing', False), ('seeking', False) if handoff else ('playing', True),
    ('seeking', False) if handoff else ('buffering', True), ('buffering', True), ('paused', True),
    ('seeking', False), ('seeking', False), ('seeking', False), ('seeking', False)]
  assert [value['lastRequestId'] for value in states[:4]] == ([None, None, None, 'a'] if handoff else [None, 'a', 'a', 'a'])
  publisher.publish.assert_called_once_with(b'pixels', epoch=1, position=0, captured_ns=1_010_000_000)
  assert sleeps == [.01] * len(times)  # Native playback keeps its original idle wait.


@pytest.mark.parametrize('matching_frame', (False, True))
@pytest.mark.parametrize('streams,expected_streams', (({0, 2}, [0, 2]), ([2, 0], [2, 0])))
def test_epoch_reset_preserves_native_camera_stream_and_eligible_paused_frame(monkeypatch, matching_frame, streams, expected_streams):
  monkeypatch.setattr('openpilot.starpilot.ui.runtime_snapshot.RuntimeSnapshotAdapter', Mock())
  monkeypatch.setattr('openpilot.starpilot.ui.pip_render.PiPRenderer', Mock())
  core: Any = object.__new__(Compositor)
  client = Mock(frame_id=5 if matching_frame else 4, timestamp_sof=9_000_000_000, timestamp_eof=9_010_000_000)
  client.is_connected.return_value = True
  frame = SimpleNamespace(frame_id=5)
  core.camera = Mock(client=client, frame=frame, stream_type=0, available_streams=streams)
  callbacks = []
  replacement = Mock()
  def transition():
    replacement.client, replacement.frame = Mock(), None
  replacement._offroad_transition = transition
  def camera_class(**kwargs):
    callbacks.append(transition)
    return replacement
  core.camera_class = camera_class
  core._close_camera = Mock()
  core._new_view = Mock()
  core.readback = Mock(pending=False)
  core.pip_stream = Mock()
  core.pip = core.view = core.context = Mock()
  idx = SimpleNamespace(frameId=5, timestampSof=9_000_000_000, timestampEof=9_010_000_000)
  sm = Mock()
  sm.__getitem__ = Mock(return_value=idx)
  sm.seen = sm.valid = {'narrowRoadEncodeIdx': True}
  sm.logMonoTime = {'narrowRoadEncodeIdx': 9_020_000_000}
  core.ui = SimpleNamespace(replay_sample=SimpleNamespace(valid=True, boot_ns=9_050_000_000), sm=sm,
                            _offroad_transition_callbacks=callbacks)
  core.render_receipts = {'modelV2': 1}
  core.reset_epoch(3)
  for callback in callbacks:
    callback()
  assert core.render_receipts == {}
  assert core.camera.client is client
  assert core.camera.available_streams == expected_streams
  assert core.camera.available_streams is not streams
  core.camera._initialize_textures.assert_called_once()
  assert core.camera.frame is (frame if matching_frame else None)
  assert core.epoch == 3 and core.last_route_frame is None


@pytest.mark.parametrize('profile', ('large', 'compact'))
@pytest.mark.parametrize('rate,camera_ready', ((1, True), (.5, True), (2, True), (1, False)))
def test_native_playback_renders_each_camera_picture_once(tmp_path, monkeypatch, profile, rate, camera_ready):
  session = tmp_path / ('a' * 32)
  session.mkdir(mode=0o700)
  monkeypatch.setenv('SP_REPLAY_CLOCK_PATH', str(tmp_path / 'display-clock'))
  for key in ('STARPILOT_PROJECTION_READ_ONLY', 'BIG', 'SCALE'):
    monkeypatch.setenv(key, '')
  monkeypatch.setattr(render.signal, 'signal', Mock())
  monkeypatch.setattr(render.threading, 'Thread', Mock())
  monkeypatch.setattr(render, 'validate_context', lambda path: path)
  monkeypatch.setattr(render, 'seed_display', lambda path: {})
  catalog = Mock()
  catalog.detail.return_value = {'playable': True, 'segments': []}
  monkeypatch.setattr('openpilot.starpilot.galaxy.route_inventory.RouteInventory', lambda path: catalog)
  times = (0., .02, .051, .061, .09, .101, .13, .151, .161, .201, .211)
  identities = tuple(range(5, 16)) if rate > 1 else (5, 5, 5, 6, 6, 7, 7, 7, 8, 9, 9)
  step = [0]
  monkeypatch.setattr(render.time, 'monotonic', lambda: 1 + times[step[0]])
  monkeypatch.setattr(render.time, 'monotonic_ns', lambda: round((1 + times[step[0]]) * 1e9))
  engine = Mock(error=None, status={'state': 'playing', 'paused': False, 'rate': rate, 'epoch': 1,
                                   'position': 0, 'routeStartNs': 100, 'start': 0, 'end': 10})
  engine.process.poll.return_value = None
  engine.resume.return_value = False
  monkeypatch.setattr(render, 'Engine', lambda *args, **kwargs: engine)
  core = Mock(cabin=False)
  core.camera = Mock(client=Mock(frame_id=5, timestamp_sof=5), frame=object(), _target_client=None, _switching=False)
  core.pip_stream._client = None
  core.readback.pending = core.ui.replay_needs_reprime = False
  available = dict.fromkeys(('modelV2', 'extrinsicsCalibration', 'longitudinalPlan'), True)
  core.ui.sm = SimpleNamespace(seen=available, valid=available, alive=available)
  core._primary_matches.return_value = camera_ready
  core.finish.return_value = nullcontext(None)
  core.update.side_effect = lambda: SimpleNamespace(valid=True, epoch=engine.status['epoch'],
                                                   now_ns=100 + round(times[step[0]] * 1e9), boot_ns=100)
  drawn = []
  core.draw.side_effect = lambda epoch, position, now: drawn.append((epoch, identities[step[0]], now)) or True
  monkeypatch.setattr(render, 'Compositor', lambda *args, **kwargs: core)

  def next_loop(delay):
    if step[0] == len(times) - 1:
      engine.error = 'test complete'
      return
    step[0] += 1
    core.camera.client.frame_id = core.camera.client.timestamp_sof = identities[step[0]]
    if step[0] == len(times) - 1:
      engine.status['epoch'] = 2
  monkeypatch.setattr(render.time, 'sleep', next_loop)
  with pytest.raises(RuntimeError, match='test complete'):
    render.run(session, 'recorded-route', tmp_path, 'native', profile, Mock())
  if camera_ready and rate <= 1:
    assert [(epoch, identity) for epoch, identity, _ in drawn] == [(1, n) for n in range(5, 10)] + [(2, 9)]
  else:
    assert len(drawn) >= 3
    assert all(b[2] - a[2] >= 50_000_000 for a, b in zip(drawn[2:], drawn[3:], strict=False))


def test_initial_hud_primer_required_even_when_camera_received_a_frame(tmp_path, monkeypatch):
  session = tmp_path / ('a' * 32)
  session.mkdir(mode=0o700)
  monkeypatch.setenv('SP_REPLAY_CLOCK_PATH', str(tmp_path / 'display-clock'))
  for key in ('STARPILOT_PROJECTION_READ_ONLY', 'BIG', 'SCALE'):
    monkeypatch.setenv(key, '')
  monkeypatch.setattr(render.signal, 'signal', Mock())
  monkeypatch.setattr(render.threading, 'Thread', Mock())
  monkeypatch.setattr(render, 'validate_context', lambda path: path)
  monkeypatch.setattr(render, 'seed_display', lambda path: {})
  catalog = Mock()
  catalog.detail.return_value = {'playable': True, 'segments': []}
  monkeypatch.setattr('openpilot.starpilot.galaxy.route_inventory.RouteInventory', lambda path: catalog)
  core = Mock(cabin=False)
  core.camera = Mock(client=Mock(), frame=object(), _target_client=None, _switching=False)
  core.pip_stream._client = None
  core.readback.pending = False
  core.ui.replay_needs_reprime = False
  available = dict.fromkeys(('modelV2', 'extrinsicsCalibration', 'longitudinalPlan'), True)
  core.ui.sm = SimpleNamespace(seen=available, valid=available, alive=available)
  core.update.return_value = SimpleNamespace(valid=True, epoch=2, now_ns=100, boot_ns=100)
  core.finish.return_value = nullcontext(None)
  monkeypatch.setattr(render, 'Compositor', lambda *args, **kwargs: core)
  engine = Mock(status={'state': 'paused', 'paused': True, 'epoch': 2, 'position': 0, 'routeStartNs': 100, 'start': 0, 'end': 1}, error=None)
  engine.process.poll.return_value = None
  engine.resume.return_value = False
  monkeypatch.setattr(render, 'Engine', lambda *args, **kwargs: engine)
  def next_loop(delay):
    engine.error = 'test complete'
  monkeypatch.setattr(render.time, 'sleep', next_loop)
  with pytest.raises(RuntimeError, match='test complete'):
    render.run(session, 'recorded-route', tmp_path, 'native', 'large', Mock())
  engine.command.assert_called_once_with({'op': 'seek', 'position': 0, 'requestId': '0' * 32}, reprime=True)
  core.draw.assert_not_called()


def test_early_camera_subscriber_is_polled_before_paused_reprime():
  from openpilot.cereal.visionipc import VisionStreamType
  from openpilot.selfdrive.ui.onroad.cameraview import CameraView
  sample = SimpleNamespace(valid=True, epoch=1, boot_ns=9_050_000_000)
  idx = SimpleNamespace(frameId=5, timestampSof=9_000_000_000, timestampEof=9_010_000_000)
  sm = Mock()
  sm.__getitem__ = Mock(return_value=idx)
  sm.seen = sm.valid = {'narrowRoadEncodeIdx': True}
  sm.logMonoTime = {'narrowRoadEncodeIdx': 9_020_000_000}
  frame = SimpleNamespace(frame_id=5)
  client = Mock(frame_id=5, timestamp_sof=idx.timestampSof, timestamp_eof=idx.timestampEof)
  # The first receive synchronizes with a publisher created after this client.
  client.recv.side_effect = [None, frame]
  core: Any = object.__new__(Compositor)
  core.ui = Mock(replay_sample=sample, sm=sm)
  core.epoch, core.cabin = 1, False
  core.camera = Mock(client=client, frame=None, stream_type=VisionStreamType.VISION_STREAM_NARROW_ROAD)
  core.camera._switching = False
  core.camera._ensure_connection.return_value = True
  core.camera._poll_camera.side_effect = lambda: CameraView._poll_camera(core.camera)
  core.update()
  assert not core._primary_matches()
  core.update()
  assert core.camera.frame is frame and core._primary_matches()


def test_recorded_wall_clock_uses_only_verified_route_pair():
  from openpilot.starpilot.replay.render import recorded_wall_clock
  sample = SimpleNamespace(valid=True, now_ns=10_000_000_000)
  assert recorded_wall_clock(sample, None) is None
  assert recorded_wall_clock(SimpleNamespace(valid=False), 1_000_000_000) is None
  assert recorded_wall_clock(SimpleNamespace(valid=True, now_ns=None), 1_000_000_000) is None
  instant = recorded_wall_clock(sample, 1_000_000_000_000_000_000)
  assert instant.timestamp() == 1_000_000_010


def test_private_wall_offset_protocol_preserves_full_nanoseconds():
  import json
  offset = 1_791_870_000_000_000_123
  value = json.loads('{"wallMinusMonoNs":"1791870000000000123"}')
  assert parse_wall_offset(value['wallMinusMonoNs']) == offset
  for invalid in (None, False, offset, float(offset), '0', '-1', '9' * 20, str(2**63), '1.79187e18'):
    assert parse_wall_offset(invalid) is None


def test_camera_reprime_cannot_overwrite_an_unacknowledged_user_action():
  import io
  import json
  import threading
  engine: Any = object.__new__(Engine)
  engine.write_lock = threading.Lock()
  engine.process = SimpleNamespace(stdin=io.StringIO())
  engine.last_request_id = engine.seek_request = None
  engine.status = engine.resume_request = None
  engine.resume_epoch = 0
  request = {'op': 'seek', 'position': 65, 'requestId': 'a' * 32}
  assert engine.command(request)
  assert not engine.command({'op': 'seek', 'position': 0, 'requestId': '0' * 32}, reprime=True)
  assert engine.seek_request == request['requestId']
  assert [json.loads(line) for line in engine.process.stdin.getvalue().splitlines()] == [request]
  assert engine.command(request, reprime=True)
  pause = {'op': 'pause', 'paused': True, 'requestId': 'b' * 32}
  assert engine.command(pause)
  assert not engine.command(request, reprime=True)
  assert engine.command({'op': 'seek', 'position': 65, 'requestId': pause['requestId']}, reprime=True)


@pytest.mark.parametrize('outcome', ('playing', 'ended', 'buffering', 'pause', 'stop', 'seek', 'paused_seek', 'rate_before', 'rate_after'))
def test_seek_waits_for_delivery_and_respects_new_commands(tmp_path, monkeypatch, outcome):
  import io
  import json
  process = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO())
  monkeypatch.setattr(render.subprocess, 'Popen', Mock(return_value=process))
  monkeypatch.setattr(render.threading, 'Thread', Mock())
  engine = Engine('recorded-route', tmp_path, wide=False, cabin=False)
  engine.status = {'event': 'status', 'state': 'playing', 'epoch': 2, 'paused': outcome == 'paused_seek', 'lastRequestId': '0'}
  seek = {'op': 'seek', 'position': 65, 'requestId': 'a'}
  engine.command(seek)
  def sent():
    return [json.loads(line) for line in process.stdin.getvalue().splitlines()]
  if outcome == 'paused_seek':
    assert sent() == [seek]
    assert not engine.resume(3)
    return
  assert sent() == [{'op': 'pause', 'paused': True, 'requestId': 'a'}, seek]
  # An old epoch or the pause acknowledgement cannot release a newer seek.
  assert engine.resume(2)
  engine.status = {**engine.status, 'state': 'paused', 'paused': True, 'epoch': 3}
  assert engine.resume(3)
  engine.status['lastRequestId'] = 'a'
  assert engine.resume(2)
  assert len(sent()) == 2
  if outcome == 'rate_before':
    engine.command({'op': 'rate', 'rate': 2, 'requestId': 'b'})
    engine.status['lastRequestId'] = 'b'
  if outcome in ('pause', 'stop', 'seek'):
    command = {'op': outcome, 'requestId': 'b', **({'paused': True} if outcome == 'pause' else
                                                {'position': 70} if outcome == 'seek' else {})}
    engine.command(command)
    before = sent()
    engine.resume(3)
    assert sent() == before
    if outcome == 'seek':
      engine.status.update(epoch=4, lastRequestId='b')
      assert engine.resume(4)
      assert sent()[-1] == {'op': 'pause', 'paused': False, 'requestId': 'b'}
  else:
    assert engine.resume(3)
    assert sent()[-1] == {'op': 'pause', 'paused': False, 'requestId': engine.last_request_id}
    count = len(sent())
    assert engine.resume(3)
    assert len(sent()) == count
    process.stdout = io.StringIO(json.dumps({**engine.status, 'state': 'buffering', 'paused': False}) + '\n')
    engine._read()
    assert engine.resume(3)  # Preloading still holds the recorded clock after accepting Play.
    assert len(sent()) == count
    if outcome == 'rate_after':
      engine.command({'op': 'rate', 'rate': 2, 'requestId': 'b'})
      engine.status['lastRequestId'] = 'b'
    state = 'playing' if outcome.startswith('rate_') else outcome
    process.stdout = io.StringIO(json.dumps({**engine.status, 'state': state, 'paused': state != 'playing',
                                           'error': 'recording_gap' if outcome == 'buffering' else None}) + '\n')
    engine._read()
    assert not engine.resume(3)


@pytest.mark.parametrize('profile', ('onroad', 'mici.onroad'))
def test_replay_draw_retains_updates_until_stock_consumers_read_them(monkeypatch, profile):
  import importlib
  import numpy as np
  from openpilot.starpilot.ui.raylib import rl
  from openpilot.cereal import messaging
  from openpilot.cereal.visionipc import VisionStreamType
  from openpilot.selfdrive.ui.ui_state import UIStatus
  model_module = importlib.import_module(f'openpilot.selfdrive.ui.{profile}.model_renderer')
  camera_module = importlib.import_module(f'openpilot.selfdrive.ui.{profile}.augmented_road_view')
  sm = messaging.SubMaster(['modelV2', 'extrinsicsCalibration', 'radarState', 'carParams', 'carState',
                           'selfdriveState', 'carOutput', 'longitudinalPlan'])
  clock = [1_000_000_000]
  ui = SimpleNamespace(sm=sm, started=False, started_frame=0, replay_clock=object(), status=UIStatus.ENGAGED,
                       display_time_ns=lambda: clock[0], replay_sample=SimpleNamespace(valid=True, now_ns=clock[0]))
  monkeypatch.setattr(model_module, 'ui_state', ui)
  monkeypatch.setattr(camera_module, 'ui_state', ui)
  monkeypatch.setattr(model_module, 'Params', lambda: SimpleNamespace(get=lambda _: None))
  monkeypatch.setattr(camera_module.gui_app, 'measure_frame_phase',
                      lambda phase, fn, *args, **kwargs: None if phase == 'camera' else fn(*args, **kwargs))
  monkeypatch.setattr(model_module, 'draw_polygon', Mock())
  renderer = model_module.ModelRenderer()
  renderer._update_raw_points = Mock(wraps=renderer._update_raw_points)
  renderer._update_model = Mock()
  renderer._draw_lane_lines = renderer._draw_path = Mock()
  renderer.render = lambda rect: renderer._render(rect)
  camera = object.__new__(camera_module.AugmentedRoadView)
  camera.close = Mock()
  camera.device_camera = object()
  camera.view_from_calib = camera.view_from_wide_calib = np.eye(3)
  camera._switch_stream_if_needed = Mock(return_value=VisionStreamType.VISION_STREAM_NARROW_ROAD)
  camera._stream_type = VisionStreamType.VISION_STREAM_NARROW_ROAD
  camera._target_stream_type, camera._switching = None, False
  camera.available_streams, camera.frame = {camera.stream_type}, object()
  camera.model_renderer = camera._model_renderer = renderer
  core: Any = object.__new__(Compositor)
  core.ui, core.camera = ui, camera
  core.profile = Profile.COMPACT if profile == 'mici.onroad' else Profile.LARGE
  core.cabin = core.wide = False
  core.render_receipts = {}
  core._primary_matches = Mock(return_value=True)
  core._optional_placeholder_ready = Mock(return_value=False)
  core._receive_primary, core.rl = Mock(), Mock()
  state = SimpleNamespace(appearance=SimpleNamespace(camera_view=2, show_lead_indicator=False, lead_info_mode=0,
                                                    lead_info_metric=False), reverse_driver_camera=False,
                          lead_indicator_source_fresh=False, lateral_active=True,
                          customization={'roadColors': {'compact': {}, 'large': {}}})
  rect = rl.Rectangle(0, 0, 536, 240)
  model = messaging.new_message('modelV2', valid=True)
  model.modelV2.position.x = [5., 10., 15.]
  model.modelV2.position.y = model.modelV2.position.z = [0., 0., 0.]
  sm.update_msgs(1., [model])
  sm.update_msgs(1.01, [])
  core._camera_layer(rect, state)
  renderer._update_raw_points.assert_not_called()
  calib = messaging.new_message('extrinsicsCalibration', valid=True, logMonoTime=clock[0] + 50_000_000)
  calib.extrinsicsCalibration.rpyCalib = [0.1, 0., 0.]
  calib.extrinsicsCalibration.calStatus = camera_module.CALIBRATED
  sm.update_msgs(1.02, [calib])
  sm.update_msgs(1.03, [])
  sm.alive['extrinsicsCalibration'] = False
  core._camera_layer(rect, state)
  renderer._update_raw_points.assert_not_called()
  assert 'extrinsicsCalibration' not in core.render_receipts
  clock[0] += 50_000_000
  ui.replay_sample.now_ns = clock[0]
  sm.alive['extrinsicsCalibration'] = True
  original = sm.updated
  rotation = Mock(wraps=camera_module.rot_from_euler)
  monkeypatch.setattr(camera_module, 'rot_from_euler', rotation)
  core._camera_layer(rect, state)
  core._camera_layer(rect, state)
  assert sm.updated is original
  assert renderer._update_raw_points.call_count == 1
  np.testing.assert_array_equal(renderer._path.raw_points[:, 0], [5., 10., 15.])
  assert rotation.call_count == 1

  params = messaging.new_message('carParams', valid=True)
  params.carParams.openpilotLongitudinalControl = True
  sm.update_msgs(1.035, [params, messaging.new_message('radarState', valid=False)])
  sm.update_msgs(1.036, [])
  geometry_calls = renderer._update_model.call_count
  core._camera_layer(rect, state)
  assert renderer._longitudinal_control
  assert renderer._update_raw_points.call_count == 1
  assert renderer._update_model.call_count == geometry_calls + 1
  assert renderer._update_model.call_args.kwargs['update_lane_geometry'] is False

  renderer._path.projected_points = np.ones((3, 2), dtype=np.float32)
  renderer._rect = rect
  renderer._draw_path = model_module.ModelRenderer._draw_path.__get__(renderer)
  state.customization['roadColors'][core.profile] = {'pathMode': 'color'}
  car = messaging.new_message('carState', valid=True)
  car.carState.vEgo = 22.
  sm.update_msgs(1.037, [car])
  sm.update_msgs(1.038, [])
  core._camera_layer(rect, state)
  assert 'carState' not in core.render_receipts
  state.customization['roadColors'][core.profile] = {'pathMode': 'rainbow'}
  core._camera_layer(rect, state)
  assert renderer._rainbow_path.speed_mps == 22.
  sample_ns = renderer._rainbow_path.sample_ns
  clock[0] += 50_000_000
  core._camera_layer(rect, state)
  assert renderer._rainbow_path.sample_ns == sample_ns

  sm.update_msgs(1.04, [model])
  sm.update_msgs(1.05, [])
  if core.profile == 'compact':
    state.appearance.camera_view = 4
    core._camera_layer(rect, state)
    assert renderer._update_raw_points.call_count == 1
    state.appearance.camera_view = 2
  core._camera_layer(rect, state)
  assert renderer._update_raw_points.call_count == 2
  before, original = dict(core.render_receipts), sm.updated
  def failed_draw(*args, **kwargs):
    sm.updated['modelV2']
    raise RuntimeError('draw failed')
  camera.render_camera_model_layer = failed_draw
  with pytest.raises(RuntimeError, match='draw failed'):
    core._camera_layer(rect, state)
  assert sm.updated is original and core.render_receipts == before


@pytest.mark.parametrize('valid,epoch,meta', [(True, 1, (1, 0., 100)), (False, 1, (1, 0., 100)),
                                            (True, 2, (1, 0., 100)), (True, 1, None)])
def test_finished_frame_is_borrowed_until_delivery_finishes(valid, epoch, meta):
  core: Any = object.__new__(Compositor)
  pixels = memoryview(bytearray(b'pixels'))
  core.readback = Mock(pending=True)
  core.readback.finish.return_value = pixels
  core.ui = SimpleNamespace(replay_clock=Mock())
  core.ui.replay_clock.sample.return_value = SimpleNamespace(valid=valid, epoch=epoch)
  core.meta = meta
  with pytest.raises(RuntimeError, match='delivery failed'):
    with core.finish() as finished:
      core.readback.release.assert_not_called()
      assert finished == ((pixels, meta) if meta is not None and valid and epoch == 1 else None)
      if finished:
        assert finished[0] is pixels
      raise RuntimeError('delivery failed')
  core.readback.release.assert_called_once()


def test_web_delivery_times_packets_and_flushes_pause_end_and_stall(tmp_path, monkeypatch):
  session = tmp_path / ('a' * 32)
  session.mkdir(mode=0o700)
  ram = tmp_path / 'ram'
  ram.mkdir()
  monkeypatch.setattr(render, 'web_media_dir', lambda _: ram / session.name)
  for key in ('STARPILOT_PROJECTION_READ_ONLY', 'BIG', 'SCALE'):
    monkeypatch.setenv(key, '')
  monkeypatch.setattr(render.signal, 'signal', Mock())
  monkeypatch.setattr(render.threading, 'Thread', Mock())
  monkeypatch.setattr(render, 'validate_context', lambda path: path)
  monkeypatch.setattr(render, 'seed_display', lambda path: {})
  catalog = Mock()
  catalog.detail.return_value = {'playable': True, 'segments': []}
  monkeypatch.setattr('openpilot.starpilot.galaxy.route_inventory.RouteInventory', lambda path: catalog)
  times = (1., 1.06, 1.125, 1.15, 10., 10.06, 10.12, 10.125, 10.23, 10.275, 11.3, 11.36, 11.42, 11.54)
  step = [0]
  monkeypatch.setattr(render.time, 'monotonic', lambda: times[step[0]])
  monkeypatch.setattr(render.time, 'monotonic_ns', lambda: round(times[step[0]] * 1e9))
  engine = Mock(error=None, status={'state': 'playing', 'paused': False, 'epoch': 1, 'position': 0,
                                   'routeStartNs': 100, 'start': 0, 'end': 20})
  engine.process.poll.return_value = None
  engine.resume.return_value = False
  monkeypatch.setattr(render, 'Engine', lambda *args, **kwargs: engine)
  core = Mock(cabin=False)
  core.camera = Mock(client=None, frame=object(), _target_client=None, _switching=False)
  core.pip_stream._client = None
  core.readback.pending = core.ui.replay_needs_reprime = False
  available = dict.fromkeys(('modelV2', 'extrinsicsCalibration', 'longitudinalPlan'), True)
  core.ui.sm = SimpleNamespace(seen=available, valid=available, alive=available)
  core.update.side_effect = lambda: SimpleNamespace(valid=step[0] != 4, epoch=engine.status['epoch'], now_ns=100, boot_ns=100)
  delivered = {0, 1, 2, 5, 6, 7, 9, 11, 12}
  core.finish.side_effect = lambda: nullcontext((b'pixels', (engine.status['epoch'], 0, round(times[step[0]] * 1e9)))
                                               if step[0] in delivered else None)
  core.draw.return_value = True
  monkeypatch.setattr(render, 'Compositor', lambda *args, **kwargs: core)
  encoder = Mock(supports_nv12=True)
  encoder.encode_nv12.side_effect = lambda pixels, keyframe: (b'packet', keyframe)
  monkeypatch.setattr('openpilot.starpilot.system.starport.hw_encoder.HardwareH264Encoder', lambda *args, **kwargs: encoder)
  muxes = []
  def new_mux(*args):
    mux = Mock(codec='avc1.42c01f', init=b'init')
    mux.frame.return_value = b'fragment'
    muxes.append(mux)
    return mux
  monkeypatch.setattr(render, 'MediaMux', new_mux)
  sleeps = []
  def next_loop(delay):
    sleeps.append(delay)
    if step[0] == len(times) - 1:
      engine.error = 'test complete'
      return
    step[0] += 1
    if step[0] in (3, 7):
      engine.status.update(paused=True, state='paused')
    if step[0] == 7:
      engine.status['epoch'] = 2
    if step[0] in (5, 9):
      engine.status.update(paused=False, state='playing', rate=2)
    if step[0] == 13:
      engine.status.update(paused=True, state='ended')
  monkeypatch.setattr(render.time, 'sleep', next_loop)
  states = []
  with pytest.raises(RuntimeError, match='test complete'):
    render.run(session, 'recorded-route', tmp_path, 'web', 'large', states.append)
  assert len(muxes) == 2
  assert [(call.kwargs['pts_us'], call.kwargs['duration_us']) for call in muxes[0].frame.call_args_list] == [
    (0, 60000), (60000, 65000), (125000, 50000), (175000, 60000)]
  assert [(call.kwargs['pts_us'], call.kwargs['duration_us']) for call in muxes[1].frame.call_args_list] == [
    (0, 50000), (50000, 50000), (100000, 60000), (160000, 50000)]
  assert states[0]['mediaReady'] is False
  buffering = [state for state in states if state['state'] == 'buffering']
  assert len(buffering) == 2  # An invalid clock and a stale publication do not invalidate existing media.
  assert all(state['frameReady'] and state['mediaReady'] and state['media'] for state in buffering)
  assert any(state['epoch'] == 2 and state['state'] == 'paused' and state['mediaReady'] for state in states)
  assert states[-1]['state'] == 'ended' and states[-1]['mediaReady']
  for mux in muxes:
    mux.close.assert_called_once()
  assert min(sleeps) == .002 and max(sleeps) == .01
  assert sleeps[9] == pytest.approx(.005)  # Processing leaves only 5 ms to the next draw.
  assert [sleeps[i] for i in (3, 4, 7, 8, 13)] == [.01] * 5  # Paused, invalid clock, ended.
  assert (ram / session.name / 'media.json').is_file() and not (session / 'media.json').exists()
