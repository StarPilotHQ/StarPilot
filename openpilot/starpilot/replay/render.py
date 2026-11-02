"""Passive offscreen camera/HUD replay worker. Never opens or controls a device display."""
from contextlib import ExitStack, contextmanager
from dataclasses import replace
import argparse
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING, cast

from openpilot.starpilot.replay.client import MAX_WORKER_STATUS
from openpilot.starpilot.replay.frames import ReplayFramePublisher, SESSION
from openpilot.starpilot.replay.media import MediaMux, MediaStore, web_media_dir

if TYPE_CHECKING:
  from openpilot.selfdrive.ui.mici.onroad.augmented_road_view import AugmentedRoadView as CompactRoadView

SIZES = {'native': {'large': (1600, 800), 'compact': (536, 240)},
         'web': {'large': (1280, 640), 'compact': (536, 240)}}
FRAME_INTERVAL = 0.05
STARTUP_TIMEOUT = 40.0


def validate_context(session_dir: Path):
  from openpilot.tools.replay.display_clock import DisplayClockReader
  session_dir = Path(session_dir)
  info = session_dir.lstat()
  if (not session_dir.is_absolute() or SESSION.fullmatch(session_dir.name) is None or
      not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700 or
      os.environ.get('SP_REPLAY_CONTEXT') != '1' or not DisplayClockReader.enabled() or
      os.environ.get('OPENPILOT_PREFIX') != 'replay-' + session_dir.name[:24] or
      os.environ.get('PARAMS_ROOT') != str(session_dir / 'params') or
      os.environ.get('COMMA_CACHE') != str(session_dir / 'cache')):
    raise ValueError('Replay worker requires an owned private session')
  return session_dir


def frame_matches(client, frame, state, stamp: int, now_ns: int | None) -> bool:
  # Camera capture timestamps use BOOTTIME on devices; match the recorded
  # CameraState identity rather than subtracting that clock from log MONOTONIC.
  sof, eof = getattr(state, 'timestampSof', None), getattr(state, 'timestampEof', None)
  identity = getattr(state, 'frameId', None)
  return bool(type(now_ns) is int and type(stamp) is int and frame is not None and
              type(sof) is int and type(eof) is int and 0 < sof <= eof and type(identity) is int and identity >= 0 and
              0 < stamp <= now_ns and now_ns - stamp <= 500_000_000 and
              getattr(client, 'frame_id', None) == identity and
              getattr(frame, 'frame_id', None) == identity and
              getattr(client, 'timestamp_sof', None) == sof and getattr(client, 'timestamp_eof', None) == eof)


def recorded_wall_clock(sample, wall_minus_mono_ns: int | None):
  if sample is None or not sample.valid or sample.now_ns is None or type(wall_minus_mono_ns) is not int:
    return None
  try:
    return datetime.fromtimestamp((sample.now_ns + wall_minus_mono_ns) / 1e9).astimezone()
  except (OSError, OverflowError, ValueError):
    return None


def parse_wall_offset(value):
  # Decimal strings preserve nanoseconds across json11's floating-point JSON model.
  if type(value) is not str or not value.isdecimal() or not 0 < len(value) <= 19:
    return None
  offset = int(value)
  return offset if 0 < offset < 2**63 else None


def camera_options(profile: str, values: dict, sources: set[str]) -> tuple[bool, bool]:
  choice = values.get('CameraView', b'2')
  wide = 'ecamera.hevc' in sources and (profile == 'large' or choice in (b'0', b'3'))
  cabin = 'dcamera.hevc' in sources and (values.get('PIPPreviewEnabled') == b'1' or
            profile == 'compact' and (choice == b'1' or choice != b'4' and values.get('DriverCamera') == b'1'))
  return wide, cabin


class ReplayCameraStream:
  # PiPRenderer consumes only this passive, private VisionIPC reader. Its
  # ordinary availability writer and host BOOTTIME freshness never run here.
  def __init__(self, ui):
    from openpilot.starpilot.ui.pip_sidecam import cabin_client
    self.ui, self.factory = ui, cabin_client
    self._client = self.frame = None
    self.active, self.generation = False, 0
    self.retired = []

  def set_active(self, active):
    self.active = active
    if not active:
      self.close()

  def release_retired(self):
    self.retired.clear()

  def close(self):
    if self._client is not None:
      self.retired.append(self._client)
      self.generation += 1
    self._client = self.frame = None

  def reset(self):
    self.frame = None

  def poll(self, now):
    if not self.active:
      return None
    if self._client is None:
      client = self.factory()
      if not client.connect(False) or not client.num_buffers:
        return None
      self._client = client
      self.generation += 1
    if not self._client.is_connected():
      self.close()
      return None
    frame = self._client.recv(timeout_ms=0)
    if frame is not None:
      self.frame = frame
    sample = self.ui.replay_sample
    sm = self.ui.sm
    state = sm['cabinEncodeIdx'] if sm.seen['cabinEncodeIdx'] and sm.valid['cabinEncodeIdx'] else None
    return self.frame if sample and sample.valid and frame_matches(
      self._client, self.frame, state, sm.logMonoTime['cabinEncodeIdx'], sample.boot_ns) else None


class PassiveAvailability:
  def observe(self):
    pass

  def close(self):
    pass


def seed_display(session_dir: Path):
  from openpilot.common.params import Params
  from openpilot.tools.replay.onroad_config import seed_param
  path = session_dir / 'display.json'
  fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
  with os.fdopen(fd, 'rb') as stream:
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 128 * 1024:
      raise ValueError('Unsafe replay display snapshot')
    value = json.loads(stream.read(128 * 1024 + 1))
  if value.get('version') != 1 or not isinstance(value.get('values'), dict) or len(value['values']) > 128:
    raise ValueError('Invalid replay display snapshot')
  params = Params()
  decoded = {}
  for key, raw in value['values'].items():
    if not isinstance(key, str) or not isinstance(raw, str) or len(raw) > 64 * 1024:
      raise ValueError('Invalid replay display preference')
    decoded[key] = base64.b64decode(raw, validate=True)
    if not seed_param(params, key, decoded[key]):
      raise ValueError('Unsupported replay display preference')
  return decoded


class Engine:
  def __init__(self, route_id: str, data_dir: Path, *, wide: bool, cabin: bool, start_segment: int | None = None,
               preload_seek: bool = False):
    from openpilot.common.basedir import BASEDIR
    command = [str(Path(BASEDIR) / 'openpilot/tools/replay/replay'), '--machine', '--data_dir', str(data_dir),
               '--no-loop', '--cache', '3']
    if wide:
      command.append('--wide-road')
    if cabin:
      command.append('--cabin')
    if preload_seek:
      command.append('--preload-seek')
    if start_segment is not None:
      command.extend(['--start-segment', str(start_segment)])
    command.append(route_id)
    environment = dict(os.environ, SP_REPLAY_PARENT_PID=str(os.getpid()))
    self.process = subprocess.Popen(command, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                                    text=True, bufsize=1, close_fds=True)
    assert self.process.stdin is not None and self.process.stdout is not None
    self.status = None
    self.error = None
    self.write_lock = threading.Lock()
    self.last_request_id = None
    self.seek_request = None
    self.resume_request = None
    self.resume_epoch = 0
    self.closed = threading.Event()
    self.reader = threading.Thread(target=self._read, daemon=True, name='replay-engine-status')
    self.reader.start()

  def _read(self):
    try:
      while not self.closed.is_set():
        line = self.process.stdout.readline(MAX_WORKER_STATUS + 1)
        if not line:
          break
        if len(line) > MAX_WORKER_STATUS or not line.endswith('\n'):
          raise ValueError('Invalid replay engine status')
        value = json.loads(line)
        if value.get('event') == 'status':
          with self.write_lock:
            self.status = value
            if (self.resume_epoch == value.get('epoch') and self.resume_request == value.get('lastRequestId') and
                (value.get('state') in ('playing', 'ended') or
                 value.get('state') == 'buffering' and value.get('error') == 'recording_gap')):
              self.resume_request = None
    except Exception as error:
      self.error = str(error)

  def command(self, value, *, reprime=False):
    with self.write_lock:
      if reprime and self.last_request_id not in (None, value.get('requestId')):
        return False
      self.last_request_id = value.get('requestId')
      if value.get('op') == 'seek':
        self.seek_request = self.last_request_id
        if self.resume_request is not None or self.status and not self.status['paused']:
          # Prepare the compositor's first frame before the recorded clock resumes.
          self.resume_request, self.resume_epoch = self.last_request_id, 0
          self._write({'op': 'pause', 'paused': True, 'requestId': self.last_request_id})
      elif value.get('op') in ('pause', 'stop'):
        self.resume_request = None
      elif self.resume_request is not None:
        self.resume_request = self.last_request_id
      self._write(value)
      return True

  def _write(self, value):
    self.process.stdin.write(json.dumps(value, allow_nan=False, separators=(',', ':')) + '\n')
    self.process.stdin.flush()

  def resume(self, epoch):
    with self.write_lock:
      status = self.status
      if (self.resume_request is not None and status and status.get('epoch') == epoch and
          status.get('lastRequestId') == self.resume_request == self.last_request_id and status.get('paused') is True and
          self.resume_epoch != epoch):
        self._write({'op': 'pause', 'paused': False, 'requestId': self.resume_request})
        self.resume_epoch = epoch
      return self.resume_request is not None

  def close(self):
    self.closed.set()
    if self.process.poll() is None:
      try:
        self.process.stdin.close()
        self.process.wait(timeout=2)
      except (OSError, subprocess.TimeoutExpired):
        self.process.terminate()
        try:
          self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
          self.process.kill()
          self.process.wait(timeout=2)
    self.process.stdout.close()


# A hidden model/path must retain its update until a later draw reads it.
class DrawUpdates(dict):
  def __init__(self, values):
    super().__init__(values)
    self.read = set()

  def __getitem__(self, key):
    value = super().__getitem__(key)
    if value:
      self.read.add(key)
    return value


class Compositor:
  def __init__(self, profile: str, width: int, height: int, *, nv12: bool, wide: bool, cabin: bool):
    from openpilot.starpilot.system.starport.headless_egl import HeadlessContext, FrameReadback
    from openpilot.starpilot.system.starport import gpu_nv12
    self.resources = ExitStack()
    self.context = HeadlessContext(width, height)
    self.resources.callback(self.context.close)
    try:
      from openpilot.starpilot.ui.raylib import rl
      from openpilot.system.ui.lib.application import gui_app
      from openpilot.selfdrive.ui.ui_state import ui_state
      from openpilot.starpilot.ui.presentation import BitmapFonts, Profile, default_font_directory
      from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter
      self.rl, self.gui, self.ui = rl, gui_app, ui_state
      self.wide, self.cabin = wide, cabin
      self.wall_minus_mono_ns = None
      rl.get_time = lambda: ui_state.display_time_ns() / 1e9
      self.profile = Profile(profile)
      self.width, self.height = width, height
      logical_w, logical_h = self.profile.size
      self.scale = width / logical_w
      if abs(height / logical_h - self.scale) > 0.0001:
        raise ValueError('Replay target must preserve the native profile aspect ratio')
      gui_app._width, gui_app._height = logical_w, logical_h
      gui_app._scaled_width, gui_app._scaled_height = width, height
      gui_app._scale = self.scale
      gui_app._target_fps = 20
      gui_app._patch_scissor_mode()
      gui_app._load_fonts()
      gui_app._patch_text_functions()
      self.resources.callback(self._close_gui)
      self.fonts = BitmapFonts(self.profile, default_font_directory(), headless_context=True)
      self.resources.callback(self.fonts.close)
      if self.profile == Profile.LARGE:
        from openpilot.selfdrive.ui.onroad.augmented_road_view import AugmentedRoadView
      else:
        from openpilot.selfdrive.ui.mici.onroad.augmented_road_view import AugmentedRoadView
      self.camera_class = AugmentedRoadView
      self.camera = self.camera_class()
      # Recorded onroad transitions must not reconnect the replay camera.
      ui_state._offroad_transition_callbacks.remove(self.camera._offroad_transition)
      self.resources.callback(self._close_camera)
      self.adapter = RuntimeSnapshotAdapter(ui_state)
      from openpilot.starpilot.ui.pip_preferences import read_pip
      from openpilot.starpilot.ui.pip_render import PiPRenderer
      self.pip_saved = read_pip(ui_state.params)
      self.pip_stream = ReplayCameraStream(ui_state)
      self.pip = PiPRenderer('bubble', self.pip_stream, frame_availability=PassiveAvailability())
      self.resources.callback(lambda: self.pip.close())
      self._new_view()
      self.resources.callback(lambda: self.view.close())
      self.content = rl.load_render_texture(width, height)
      if not self.content.id:
        raise RuntimeError('Replay content target unavailable')
      self.resources.callback(rl.unload_render_texture, self.content)
      self.converter = gpu_nv12.Nv12Converter(width, height, compose=True) if nv12 else None
      if self.converter is not None:
        self.resources.callback(self.converter.close)
      self.output = None if nv12 else rl.load_render_texture(width, height)
      if self.output is not None:
        if not self.output.id:
          raise RuntimeError('Replay RGBA target unavailable')
        self.resources.callback(rl.unload_render_texture, self.output)
      self.readback = FrameReadback(width * height * (3 if nv12 else 8) // 2, asynchronous=True)
      self.resources.callback(self.readback.close)
      self.meta: tuple[int, float, int] | None = None
      self.epoch = None
      self.last_route_frame = None
      self.primary_ready = False
      self.render_receipts = {}
    except BaseException:
      self.resources.close()
      raise

  def _new_view(self):
    from openpilot.starpilot.ui.onroad import OnroadView
    from openpilot.starpilot.ui.onroad_dm import DriverMonitorLayer
    self.view = OnroadView(self.fonts, Path(__file__).parents[2] / 'selfdrive/assets',
                           camera_layer=self._camera_layer, pip_layer=self._pip_layer,
                           wall_clock=lambda: recorded_wall_clock(self.ui.replay_sample, self.wall_minus_mono_ns))
    self.monitor = DriverMonitorLayer(self.profile)
    self.view.driver_monitor_layer = self._monitor_layer
    if self.profile == 'compact':
      self.view.stock_confidence_layer = lambda rect, state: self.camera.render_stock_confidence_layer(rect, lateral_active=state.lateral_active)
      self.view.stock_confidence_reset = self.camera.reset_stock_confidence_layer
      self.view.extra_overlays = lambda rect, state: self.camera.render_model_source_layer(rect, show_long_indicator=None)

  def _camera_layer(self, rect, state):
    self.primary_ready = self._primary_matches()
    if self.profile == 'compact':
      if state.appearance.camera_view == 4:
        self.rl.draw_rectangle_rec(rect, self.rl.BLACK)
        return
      if ((state.appearance.camera_view == 1 or state.reverse_driver_camera) and not self.cabin or
          state.appearance.camera_view == 3 and not self.wide):
        self.rl.draw_rectangle_rec(rect, self.rl.BLACK)
        from openpilot.system.ui.widgets.label import gui_label
        gui_label(rect, 'recorded camera unavailable', font_size=28)
        return
    sm = self.ui.sm
    receipts = {name: sm.recv_frame[name] for name in
                ('modelV2', 'radarState', 'carParams', 'carState', 'extrinsicsCalibration')}
    original = sm.updated
    updates = original | {name: sm.seen[name] and receipt != self.render_receipts.get(name)
                          for name, receipt in receipts.items()}
    sample = self.ui.replay_sample
    updates['extrinsicsCalibration'] = bool(updates['extrinsicsCalibration'] and sample and sample.valid and
                                         sample.now_ns is not None and
                                         sm.valid['extrinsicsCalibration'] and
                                         0 < sm.logMonoTime['extrinsicsCalibration'] <= sample.now_ns)
    flags = DrawUpdates(updates)
    sm.updated = flags
    try:
      if self.profile == 'compact':
        camera = cast('CompactRoadView', self.camera)
        camera.render_camera_model_layer(rect, camera_view=state.appearance.camera_view,
                                         reverse_driver=state.reverse_driver_camera,
                                         lead_indicator=state.appearance.show_lead_indicator and state.lead_indicator_source_fresh,
                                         lead_info_mode=state.appearance.lead_info_mode, lead_info_metric=state.appearance.lead_info_metric,
                                         lateral_active=state.lateral_active, road_style=state.customization['roadColors']['compact'],
                                         poll_camera=False)
      else:
        self.camera.render_camera_model_layer(rect, road_style=state.customization['roadColors']['large'], poll_camera=False)
    finally:
      sm.updated = original
    self.render_receipts.update({name: receipts[name] for name in flags.read if name in receipts})
    self.primary_ready = self._primary_matches()
    if not self.primary_ready and self._optional_placeholder_ready():
      from openpilot.system.ui.widgets.label import gui_label
      self.rl.draw_rectangle_rec(rect, self.rl.BLACK)
      gui_label(rect, 'recorded camera unavailable', font_size=28)
      self.primary_ready = True

  def _optional_placeholder_ready(self):
    from openpilot.cereal.visionipc import VisionStreamType
    if self.camera.stream_type == VisionStreamType.VISION_STREAM_NARROW_ROAD:
      return False
    sm, sample = self.ui.sm, self.ui.replay_sample
    stamp = sm.logMonoTime['narrowRoadEncodeIdx']
    return bool(sample and sample.valid and sample.boot_ns is not None and
                sm.seen['narrowRoadEncodeIdx'] and sm.valid['narrowRoadEncodeIdx'] and
                0 < stamp <= sample.boot_ns and sample.boot_ns - stamp <= 500_000_000)

  def _primary_matches(self):
    from openpilot.cereal.visionipc import VisionStreamType
    names = {VisionStreamType.VISION_STREAM_NARROW_ROAD: 'narrowRoadEncodeIdx',
             VisionStreamType.VISION_STREAM_WIDE_ROAD: 'wideRoadEncodeIdx',
             VisionStreamType.VISION_STREAM_CABIN: 'cabinEncodeIdx'}
    name = names.get(self.camera.stream_type)
    sm, sample = self.ui.sm, self.ui.replay_sample
    state = sm[name] if name is not None and sm.seen[name] and sm.valid[name] else None
    return bool(sample and sample.valid and name is not None and state is not None and frame_matches(
      self.camera.client, self.camera.frame, state, sm.logMonoTime[name], sample.boot_ns))

  def _receive_primary(self):
    self.camera._poll_camera()
    self.primary_ready = self._primary_matches()

  def _pip_layer(self, rect, state, *, submit=None):
    from openpilot.starpilot.ui.pip_sidecam import Signals, Rect
    from openpilot.starpilot.ui.onroad_customization import CAMERA_WIDGETS, placement, widget_size
    from openpilot.starpilot.ui.runtime_snapshot import display_message
    from openpilot.starpilot.ui.presentation import FontRole
    saved = self.pip_saved
    if (saved.enabled is not True or saved.mask is None or
        saved.on_blinker is None or saved.on_bsm is None or saved.invert is None):
      self.pip.deactivate()
      return
    now = state.observed_ns or 0
    car = display_message(self.ui.sm, 'carState', now, after_frame=self.ui.started_frame)
    signals = Signals(car is not None, bool(car.leftBlinker) if car else False, bool(car.rightBlinker) if car else False,
                      bool(car.leftBlindspot) if car else False, bool(car.rightBlindspot) if car else False)
    positions = {}
    for side, key in zip(('left', 'right'), CAMERA_WIDGETS, strict=True):
      position = placement(state.customization, self.profile, key)
      if position['enabled']:
        width, height = widget_size(state.customization, self.profile, key)
        positions[side] = Rect(position['x'], position['y'], width, height)
    status = self.pip.render(rect, saved.mask, signals, enabled=self.cabin, on_blinker=saved.on_blinker,
                             on_bsm=saved.on_bsm, invert=saved.invert, now=now / 1e9, placements=positions, submit=submit)
    if not self.cabin or status not in ('rendered', 'inactive'):
      self.fonts.draw('RECORDED SIDE CAMERA UNAVAILABLE', FontRole.MEDIUM, 18 if self.profile == 'compact' else 25,
                      18 if self.profile == 'compact' else rect.x + 30,
                      180 if self.profile == 'compact' else rect.y + rect.height - 62, self.rl.Color(245, 200, 155, 255))

  def _monitor_layer(self, rect, state):
    from openpilot.starpilot.ui.runtime_snapshot import display_message
    from openpilot.starpilot.ui.onroad_state import AlertSize
    from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
    now = state.observed_ns or 0
    monitor = display_message(self.ui.sm, 'driverMonitoringState', now, after_frame=self.ui.started_frame)
    driver = display_message(self.ui.sm, 'driverStateV2', now, after_frame=self.ui.started_frame)
    top_icons = (self.profile == 'compact' and state.alert.size == AlertSize.NONE and
                 state.appearance.camera_view != CameraViewChoice.DRIVER and not state.reverse_driver_camera and
                 self.view.compact_hud._set_speed_alpha.x > .01 and state.customization['layouts']['compact']['max_speed']['enabled'])
    self.monitor.render(state, monitor=monitor, driver=driver, fresh=monitor is not None and driver is not None,
                        onroad=self.ui.is_onroad(), top_icons=top_icons)

  def _close_camera(self):
    callbacks = self.ui._offroad_transition_callbacks
    if self.camera._offroad_transition in callbacks:
      callbacks.remove(self.camera._offroad_transition)
    self.camera.close()

  def _close_gui(self):
    for texture in self.gui._textures.values():
      self.rl.unload_texture(texture)
    self.gui._textures.clear()
    for font in (*self.gui._fonts.values(), *self.gui._fallback_fonts.values()):
      self.rl.unload_font(font)
    self.gui._fonts.clear()
    self.gui._fallback_fonts.clear()

  def update(self):
    # Select the camera before HUD messages and keep that selection through drawing.
    self._receive_primary()
    self.ui.update()
    sample = self.ui.replay_sample
    if sample is not None and sample.valid and sample.epoch != self.epoch:
      self.reset_epoch(sample.epoch)
    # Connect the optional PiP client before the one-shot paused primer.
    if self.cabin and self.pip_saved.enabled is True:
      self.pip_stream.set_active(True)
      self.pip_stream.poll(0)
    return sample

  def reset_epoch(self, epoch):
    from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter
    from openpilot.starpilot.ui.pip_render import PiPRenderer
    if self.readback.pending:
      self.readback.release()
    client, stream, available = self.camera.client, self.camera.stream_type, self.camera.available_streams.copy()
    frame = self.camera.frame if self._primary_matches() else None
    self._close_camera()
    self.camera = self.camera_class(stream_type=stream)
    self.ui._offroad_transition_callbacks.remove(self.camera._offroad_transition)
    # Preserve the subscriber to avoid repeated seeks while paused.
    self.camera.client = client
    self.camera.frame = frame
    self.camera.available_streams = available
    if client is not None and client.is_connected():
      self.camera._initialize_textures()
    pip_client = self.pip_stream._client
    self.pip.close()
    self.pip_stream._client = pip_client
    self.pip_stream.release_retired()
    self.pip_stream.reset()
    self.pip = PiPRenderer('bubble', self.pip_stream, frame_availability=PassiveAvailability())
    self.view.close()
    self.adapter = RuntimeSnapshotAdapter(self.ui)
    self._new_view()
    self.context.pause()
    self.epoch = epoch
    self.render_receipts = {}
    self.last_route_frame = None

  def _draw_rejection(self, epoch: int):
    sample = self.ui.replay_sample
    if sample is None or sample.now_ns is None or not sample.valid:
      return 'invalid_clock'
    if sample.epoch != epoch:
      return 'wrong_epoch'
    if (epoch, sample.now_ns) == self.last_route_frame:
      return 'unchanged_clock'
    # OnroadView skips the camera layer when unavailable, so this draw cannot publish.
    if not (self._primary_matches() or self._optional_placeholder_ready()):
      return 'camera_not_ready'
    return None

  def _prepare(self):
    for prepare in tuple(self.gui._render_prepare):
      prepare()

  def draw(self, epoch: int, position: float, now_ns: int):
    if self._draw_rejection(epoch) is not None:
      return False
    sample = self.ui.replay_sample
    key = epoch, sample.now_ns
    from openpilot.starpilot.system.starport import gpu_nv12
    from openpilot.starpilot.ui.shell import ShellMode
    rl = self.rl
    self.context.begin_frame(cast(int, sample.now_ns) / 1e9)
    snapshot = self.adapter.build(ShellMode.ONROAD)
    self.primary_ready = False
    self._prepare()
    rl.begin_texture_mode(self.content)
    try:
      rl.clear_background(rl.BLACK)
      rl.rl_push_matrix()
      try:
        rl.rl_scalef(self.scale, self.scale, 1)
        # Truncated recordings can start with video before their first saved deviceState.
        self.view.render(replace(snapshot.onroad, viewport_width=self.profile.size[0], camera_available=(
          self._primary_matches() or self._optional_placeholder_ready())))
      finally:
        rl.rl_pop_matrix()
    finally:
      rl.end_texture_mode()
    if not self.primary_ready:
      return False
    if self.output is not None:
      gpu_nv12.compose_rgba(self.content.texture, self.output, 0, 0)
      regions = [(self.output.id, self.width, self.height, 0)]
    else:
      regions = self.converter.convert(self.content.texture)
    self.readback.start(regions)
    self.meta = epoch, position, now_ns
    self.last_route_frame = key
    self.gui._frame += 1
    return True

  @contextmanager
  def finish(self):
    if not self.readback.pending or not self.readback.ready():
      yield None
      return
    try:
      pixels = self.readback.finish()
      sample = self.ui.replay_clock.sample()
      meta = self.meta
      yield (pixels, meta) if meta is not None and sample.valid and sample.epoch == meta[0] else None
    finally:
      self.readback.release()

  def close(self):
    self.resources.close()


def run(session_dir: Path, route_id: str, data_dir: Path, target: str, profile: str, emit,
        start_segment: int | None = None, export_budget: int = 0):
  session_dir = validate_context(session_dir)
  from openpilot.common.hardware import HARDWARE
  from openpilot.common.realtime import set_core_affinity
  if target == 'native' and HARDWARE.get_device_type() in ('tici', 'tizi', 'mici'):
    # Leave the isolated UI/GPU CPUs before the engine and renderer start threads.
    set_core_affinity([0, 1, 2, 3, 4, 5])
  os.environ['STARPILOT_PROJECTION_READ_ONLY'] = '1'
  os.environ['BIG'] = '1' if profile == 'large' else '0'
  os.environ['SCALE'] = '1'
  values = seed_display(session_dir)
  from openpilot.starpilot.galaxy.route_inventory import RouteInventory
  detail = RouteInventory(data_dir).detail(route_id)
  if not detail.get('playable'):
    raise ValueError(detail.get('reason') or 'Recorded route is unavailable')
  sources = {name for segment in detail['segments'] for name in segment['sourceFiles']}
  wide, cabin = camera_options(profile, values, sources)
  stopped = threading.Event()
  signal.signal(signal.SIGTERM, lambda *_: stopped.set())
  signal.signal(signal.SIGINT, lambda *_: stopped.set())
  width, height = SIZES[target][profile]
  began = time.monotonic()
  with ExitStack() as resources:
    engine = Engine(route_id, data_dir, wide=wide, cabin=cabin, start_segment=start_segment, preload_seek=not export_budget)
    resources.callback(engine.close)
    core = Compositor(profile, width, height, nv12=target == 'web', wide=wide, cabin=cabin)
    resources.callback(core.close)
    def commands():
      try:
        while not stopped.is_set():
          raw = sys.stdin.readline(4097)
          if not raw:
            stopped.set()
            return
          if len(raw) > 4096 or not raw.endswith('\n'):
            raise ValueError('Replay command exceeds its bound')
          value = json.loads(raw)
          if not isinstance(value, dict):
            raise ValueError('Invalid replay command')
          engine.command(value)
          if value.get('op') == 'stop':
            stopped.set()
      except Exception as error:
        engine.error = str(error)
        stopped.set()
    threading.Thread(target=commands, daemon=True, name='replay-commands').start()
    publisher = None
    media = None
    encoder = None
    mux = None
    exporter = None
    if export_budget:
      from openpilot.starpilot.replay.export import ExportVideo, MAX_EXPORT_BYTES, export_ranges
      if target != 'web' or not 0 < export_budget <= MAX_EXPORT_BYTES:
        raise ValueError('Invalid replay export budget')
    if target == 'native':
      # Keep frames in RAM to avoid disk writeback stalls.
      publisher = ReplayFramePublisher(Path(os.environ['SP_REPLAY_CLOCK_PATH']).with_name('frame'), session_dir.name, profile, width, height)
      resources.callback(publisher.close)
    else:
      from openpilot.starpilot.system.starport.hw_encoder import HardwareH264Encoder
      from openpilot.starpilot.replay.export import BITRATE_KBPS
      encoder = HardwareH264Encoder(width, height, bitrate_kbps=BITRATE_KBPS)
      if not encoder.supports_nv12:
        raise RuntimeError('Replay requires the existing NV12 hardware encoder ABI')
      resources.callback(encoder.close)
      if not export_budget:
        # Playback fragments are bounded and disposable; avoid flash writeback stalls.
        directory = web_media_dir(session_dir.name)
        directory.mkdir(mode=0o700)
        media = MediaStore(directory, session_dir.name, width, height)
      resources.callback(lambda: mux.close() if mux is not None else None)
    last_emit = last_draw = 0.0
    last_camera_frame = None
    published_at = 0.0
    published_epoch = 0
    clock_epoch = 0
    from openpilot.starpilot.replay.media import FrameTimeline
    timeline = FrameTimeline()
    pending_frame = None
    acknowledged = None
    primed_clients = set()
    while not stopped.is_set():
      status = engine.status
      if engine.error:
        raise RuntimeError(engine.error)
      if engine.process.poll() is not None:
        engine.reader.join(timeout=1)
        raise RuntimeError(engine.error or (engine.status or {}).get('error') or 'Replay engine exited unexpectedly')
      if status:
        core.wall_minus_mono_ns = parse_wall_offset(status.get('wallMinusMonoNs'))
        if export_budget and exporter is None and status.get('segmentRanges'):
          exporter = ExportVideo(session_dir, width, height, export_ranges(status, start_segment), export_budget)
          resources.callback(exporter.close)
      sample = core.update()
      if sample is not None and sample.epoch is not None:
        clock_epoch = max(clock_epoch, sample.epoch)
      now = time.monotonic()
      valid = bool(status and sample is not None and sample.valid and sample.now_ns is not None and sample.epoch == status.get('epoch'))
      ready_frames = []
      if pending_frame is not None and (not status or pending_frame[2] != status.get('epoch')):
        pending_frame = None
      with core.finish() as finished:
        if finished is not None and valid:
          pixels, (epoch, position, captured_ns) = finished
          if epoch == sample.epoch:
            if publisher is not None:
              publisher.publish(pixels, epoch=epoch, position=position, captured_ns=captured_ns)
            elif export_budget:
              if exporter is not None:
                packet, keyframe = encoder.encode_nv12(pixels, keyframe=not exporter.seen or epoch != published_epoch or exporter.frames % 10 == 0)
                exporter.frame(packet, position=position, keyframe=keyframe)
                published_epoch, published_at = epoch, captured_ns / 1e9
            else:
              new_epoch = epoch != media.epoch
              pts_us, keyframe = timeline.next(epoch, captured_ns)
              packet, actual_keyframe = encoder.encode_nv12(pixels, keyframe=keyframe)
              if new_epoch:
                if mux is not None:
                  mux.close()
                mux = MediaMux(width, height, packet)
                media.begin(epoch, mux.codec, mux.init)
              if pending_frame is not None:
                ready_frames.append((pending_frame, pts_us - pending_frame[5]))
              pending_frame = packet, actual_keyframe, epoch, position, captured_ns, pts_us
            if publisher is not None:
              published_epoch = epoch
              published_at = captured_ns / 1e9
      if pending_frame is not None and valid and (status['paused'] or status['state'] == 'ended' or
                                                  now - pending_frame[4] / 1e9 >= 1):
        ready_frames.append((pending_frame, 50000))
        pending_frame = None
        timeline.flush()
      for frame, duration in ready_frames:
        packet, keyframe, epoch, position, captured_ns, pts_us = frame
        fragment = mux.frame(packet, pts_us=pts_us, duration_us=duration, keyframe=keyframe)
        media.publish(fragment, position=position, keyframe=keyframe)
        published_epoch, published_at = epoch, captured_ns / 1e9
      if exporter is not None and status and exporter.advance(engine, status, core.readback.pending):
        final = {**status, 'error': None, 'frameReady': False, 'mediaReady': False, 'exportProgress': 1.}
        emit({**final, 'exportState': 'finalizing'})
        exporter.finalize()
        emit({**final, 'exportState': 'ready'})
        return
      clients = (core.camera.client, core.camera._target_client, core.pip_stream._client)
      primed_clients.intersection_update(client for client in clients if client is not None)
      needs_prime = (not published_epoch or core.camera.frame is None or core.camera._switching or
                     core.cabin and core.pip_saved.enabled is True and core.pip_stream.frame is None)
      unprimed = [client for client in clients if client is not None and client.is_connected() and client not in primed_clients]
      if valid and status.get('paused') is True and needs_prime and unprimed and not core.readback.pending:
        # Synchronize both HUD and camera subscribers before the initial paused
        # preview, then prime late VisionIPC clients once, including cabin/PiP.
        if engine.command({'op': 'seek', 'position': status['position'], 'requestId': status.get('lastRequestId') or '0' * 32}, reprime=True):
          primed_clients.update(unprimed)
          valid = False
      if valid and status is not None and getattr(core.ui, 'replay_needs_reprime', False):
        if engine.command({'op': 'seek', 'position': status['position'], 'requestId': status.get('lastRequestId') or '0' * 32}, reprime=True):
          core.ui.replay_needs_reprime = False
          valid = False
      handing_off = engine.resume(published_epoch if valid else 0)
      camera_frame = None
      interval = FRAME_INTERVAL
      client = core.camera.client
      if (target == 'native' and valid and sample is not None and status is not None and
          client is not None and core._primary_matches()):
        # Follow camera frames instead of sampling them on a second timer.
        camera_frame = (sample.epoch, core.camera.stream_type, client.frame_id, client.timestamp_sof)
        interval = 0 if status.get('rate', 1) <= 1 else FRAME_INTERVAL
      if (valid and status is not None and sample is not None and sample.now_ns is not None and
          (camera_frame is None or camera_frame != last_camera_frame) and
          not core.readback.pending and now - last_draw >= interval):
        origin = status.get('routeStartNs')
        if type(origin) is not int:
          raise RuntimeError('Replay engine did not expose its route clock origin')
        position = min(status['end'], max(status['start'], (sample.now_ns - origin) / 1e9))
        if core.draw(sample.epoch, position, time.monotonic_ns()):
          last_camera_frame = camera_frame
          # Keep the frame cadence after delivery jitter, without accumulating a catch-up queue.
          last_draw = max(last_draw + interval, now - interval)
      if status and now - last_emit >= .1:
        # A production stall does not invalidate already published media in the same epoch.
        ready = bool(not handing_off and published_epoch and published_epoch == status.get('epoch') and
                     status['state'] in ('playing', 'paused', 'buffering', 'ended') and
                     clock_epoch == published_epoch)
        fresh = ready and valid and (status['paused'] or now - published_at <= .5)
        value = {key: status.get(key) for key in ('event', 'state', 'position', 'start', 'end', 'rate', 'paused', 'epoch', 'lastRequestId', 'error')}
        if status['state'] not in ('failed', 'ended') and not fresh:
          value['state'] = 'buffering' if ready else 'seeking' if published_epoch else 'preparing'
        if ready:
          acknowledged = value.get('lastRequestId')
        elif engine.seek_request == value.get('lastRequestId'):
          value['lastRequestId'] = acknowledged
        if 'seekRanges' in status:
          value['seekRanges'] = status['seekRanges']
        if 'segmentRanges' in status:
          value['segmentRanges'] = status['segmentRanges']
        value['timelineState'] = status.get('timelineState', 'unavailable')
        warnings = [*detail.get('warnings', []), *status.get('sourceWarnings', [])]
        if recorded_wall_clock(sample, core.wall_minus_mono_ns) is None:
          warnings.append('Recorded wall-clock time is unavailable; clock and arrival time are hidden.')
        if not core.adapter._replay_stopped_history:
          warnings.append('Stop duration before this seek is unavailable until recorded movement re-establishes history.')
        if any(not (core.ui.sm.seen[name] and core.ui.sm.valid[name] and core.ui.sm.alive[name])
               for name in ('modelV2', 'extrinsicsCalibration')):
          warnings.append('Recorded model/calibration unavailable; model overlay is hidden.')
        if not (core.ui.sm.seen['longitudinalPlan'] and core.ui.sm.valid['longitudinalPlan'] and core.ui.sm.alive['longitudinalPlan']):
          warnings.append('Recorded longitudinal plan unavailable; throttle feedback is neutral.')
        value['sourceWarnings'] = list(dict.fromkeys(str(warning)[:160] for warning in warnings))[:32]
        value.update(frameReady=ready, mediaReady=bool(ready and media is not None),
                     framePath=str(publisher.path) if publisher is not None else None,
                     media=media.summary() if ready and media is not None else None)
        if export_budget:
          value.update(exportState='rendering', exportProgress=exporter.progress if exporter is not None else 0.)
        emit(value)
        last_emit = now
        if status['state'] == 'failed':
          raise RuntimeError(status.get('error') or 'Replay failed')
      if not published_epoch and now - began > STARTUP_TIMEOUT:
        raise TimeoutError('Replay camera and HUD did not become ready')
      delay = .002 if core.readback.pending else .01
      if target == 'web' and valid and status is not None and status['state'] == 'playing' and not status['paused']:
        # Wake before the next draw deadline without increasing paused/native polling.
        delay = min(delay, max(.002, last_draw + interval - time.monotonic()))
      time.sleep(delay)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--session-dir', type=Path, required=True)
  parser.add_argument('--route-id', required=True)
  parser.add_argument('--data-dir', type=Path, required=True)
  parser.add_argument('--target', choices=SIZES, required=True)
  parser.add_argument('--profile', choices=('large', 'compact'), required=True)
  parser.add_argument('--start-segment', type=int)
  parser.add_argument('--export-budget', type=int, default=0)
  args = parser.parse_args()
  # Keep native library traces and UI diagnostics off the JSON protocol descriptor.
  protocol = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
  os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
  sys.stdout = sys.stderr
  def emit(value):
    print(json.dumps(value, allow_nan=False, separators=(',', ':')), file=protocol, flush=True)
  try:
    run(args.session_dir, args.route_id, args.data_dir, args.target, args.profile, emit, args.start_segment, args.export_budget)
  except Exception as error:
    emit({'event': 'status', 'state': 'failed', 'position': 0, 'start': 0, 'end': 0, 'rate': 1,
          'paused': True, 'epoch': 0, 'lastRequestId': None, 'error': str(error)[:240],
          'frameReady': False, 'mediaReady': False, 'framePath': None, 'media': None})
    raise SystemExit(1) from error


if __name__ == '__main__':
  main()
