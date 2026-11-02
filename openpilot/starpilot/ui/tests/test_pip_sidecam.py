"""PiP stream lifetime and side selection without a camera or graphics context."""

from dataclasses import dataclass
from contextlib import nullcontext
from types import SimpleNamespace
from openpilot.starpilot.ui.onroad_customization import default_document
from unittest.mock import patch
from unittest.mock import Mock
import gc
import time
import unittest
import uuid
import weakref

from openpilot.starpilot.ui.pip_sidecam import Crop, Mask, PiPStream, Rect, Signals, bubble_rect, curved_crop, selected_sides
from openpilot.starpilot.ui.pip_shaders import PIP_CURVED_FRAGMENT_SHADER, PIP_FRAGMENT_SHADER
from openpilot.starpilot.ui.pip_render import PiPRenderer, _external_shader
from openpilot.starpilot.ui import pip_render
from openpilot.starpilot.ui.pip_preferences import SavedPiP, starting_mask
from openpilot.starpilot.ui.pip_warning import PiPWarningSource
from openpilot.starpilot.spot_monitor.inference import MODEL_SHA256
from openpilot.starpilot.spot_monitor.preferences import SavedPreferences
from openpilot.cereal import log
from openpilot.system.ui.lib.egl import EGLImage


def test_mask_maps_image_left_to_vehicle_right_and_rejects_unbounded_crop():
  raw = {"width": 1928, "height": 1208, "center_left": [315, 548],
         "center_right": [1571, 539], "crop_size": 580}
  mask = Mask.parse(raw)
  assert mask is not None
  assert mask.crop("right") == Crop(25, 258, 580)
  assert mask.crop("left") == Crop(1281, 249, 580)
  assert mask.crop("unknown") is None
  for invalid in ({**raw, "crop_size": float("nan")}, {**raw, "center_left": [2, 548]},
                  {**raw, "height": True}, {**raw, "extra": 1}, {**raw, "crop_size": 10 ** 350}):
    assert Mask.parse(invalid) is None


def test_selected_sides_use_pip_triggers_including_vasm():
  mask = Mask.parse({"width": 100, "height": 100, "center_left": [25, 50],
                     "center_right": [75, 50], "crop_size": 20})
  assert mask is not None
  signals = Signals(True, left_blinker=True, right_blinker=False, left_blindspot=False,
                    right_blindspot=False, vasm_right=True)
  active = {"started": True, "enabled": True, "on_blinker": True, "on_bsm": True}
  assert selected_sides(mask, signals, **active) == ("right", "left")
  assert selected_sides(mask, Signals(True, False, False, False, False, True, True), **active) == ("right", "left")
  assert selected_sides(mask, signals, **{**active, "on_bsm": False}) == ("left",)
  assert selected_sides(mask, signals, **{**active, "started": False}) == ()
  assert selected_sides(mask, Signals(False, True, True, True, True), **active) == ()


def test_visual_warning_event_expires_without_refresh_and_preserves_oem_bsm():
  million = 1_000_000
  fingerprint = "b" * 64
  event = log.Event.new_message()
  event.init("spotMonitorState")
  event.valid = True
  event.logMonoTime = 1000 * million
  observation = event.spotMonitorState.init("observation")
  observation.version = 1
  observation.producerSessionId = "a" * 32
  observation.sequence = 1
  observation.modelSha256 = MODEL_SHA256
  observation.settingsFingerprint = fingerprint
  observation.observedMonoTime = 1000 * million
  observation.observedBootTime = 1000 * million
  observation.sourceFrameId = 10
  observation.sourceFrameEofBootTime = 900 * million
  observation.validUntilBootTime = 1400 * million
  side = observation.init("left")
  side.status = "warning"
  side.confidence = 0.96
  side.warning = True
  side.sourceFrameId = 10
  side.sourceFrameEofBootTime = 900 * million
  side.sourceObservedMonoTime = 900 * million
  side.validUntilBootTime = 3900 * million

  class Socket:
    closed = False
    def close(self):
      self.closed = True

  socket = Socket()
  received = [event, None]
  source = PiPWarningSource(subscribe=lambda: socket, receive=lambda _: received.pop(0) if received else None)
  first = source.sample(enabled=True, settings_fingerprint=fingerprint,
                        now_mono_ns=1100 * million, now_boot_ns=1100 * million)
  assert first == (True, False), first
  assert source.sample(enabled=True, settings_fingerprint=fingerprint,
                       now_mono_ns=1401 * million, now_boot_ns=1401 * million) == (True, False)
  assert source.sample(enabled=True, settings_fingerprint=fingerprint,
                       now_mono_ns=3901 * million, now_boot_ns=3901 * million) == (False, False)
  mask = Mask.parse({"width": 100, "height": 100, "center_left": [25, 50],
                     "center_right": [75, 50], "crop_size": 20})
  assert selected_sides(mask, Signals(True, False, False, True, False), started=True, enabled=True,
                        on_blinker=False, on_bsm=True) == ("left",)
  source.close()
  assert socket.closed


def test_visual_warning_invalid_message_clears_and_inactive_unsubscribes():
  class Reader:
    def read(self, event, **kwargs):
      raise ValueError("invalid visual message")
  class Socket:
    def __init__(self):
      self.closed = False
    def close(self):
      self.closed = True
  socket = Socket()
  source = PiPWarningSource(subscribe=lambda: socket, receive=lambda _: object(), reader=Reader())
  assert source.sample(enabled=True, settings_fingerprint="b" * 64,
                       now_mono_ns=1, now_boot_ns=1) == (False, False)
  assert socket.closed
  assert source.sample(enabled=False, settings_fingerprint="", now_mono_ns=2, now_boot_ns=2) == (False, False)


def test_visual_warning_close_failure_still_drops_source():
  class Socket:
    def close(self):
      raise OSError("socket already gone")
  source = PiPWarningSource(subscribe=Socket, receive=lambda _: None)
  assert source.sample(enabled=True, settings_fingerprint="b" * 64,
                       now_mono_ns=1, now_boot_ns=1) == (False, False)
  source.close()
  assert source._socket is None and source._reader is None


def test_native_pip_passes_only_qualified_visual_sides_without_replacing_oem_bsm():
  from openpilot.starpilot.ui import runtime_app
  from openpilot.starpilot.ui.presentation import Profile
  shell = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
  shell.slc_actions = None
  shell._pip_read_ns = None
  shell._pip_saved = None
  shell._vasm_saved = None
  shell.profile = Profile.LARGE
  shell.pip_renderer = Mock()
  shell.pip_renderer.render.return_value = "inactive"
  shell.pip_warning = Mock()
  shell.pip_warning.sample.return_value = True, False
  mask = starting_mask(1928, 1208)
  saved = SimpleNamespace(enabled=True, mask=mask, invert=False, on_blinker=False, on_bsm=True)
  vasm = SimpleNamespace(readable=True, valid=True, preferences=SimpleNamespace(enabled=True),
                         fingerprint="b" * 64)
  car = SimpleNamespace(leftBlinker=False, rightBlinker=False, leftBlindspot=False, rightBlindspot=True,
                        canValid=True, gearShifter=runtime_app.car_schema.CarState.GearShifter.drive)
  state = SimpleNamespace(customization=default_document(), alert=SimpleNamespace(size=None))
  with patch.object(runtime_app, "ui_state", SimpleNamespace(params=object(), sm=object(), started_frame=0)), \
       patch.object(runtime_app, "read_pip", return_value=saved), \
       patch.object(runtime_app, "read_vasm_preferences", return_value=vasm), \
       patch.object(runtime_app, "display_message", return_value=car), \
       patch.object(runtime_app, "clock_pair_ns", return_value=(100, 100)), \
       patch.object(runtime_app.time, "monotonic_ns", return_value=100), \
       patch.dict("os.environ", {"STARPILOT_PIP_DEV": "1", "STARPILOT_VASM_DEVELOPMENT": "1"}):
    shell._render_pip(SimpleNamespace(x=0), state)
    signals = shell.pip_renderer.render.call_args.args[2]
    assert signals.vasm_left and not signals.vasm_right
    assert signals.right_blindspot and not signals.left_blindspot
    assert shell.pip_warning.sample.call_args.kwargs["settings_fingerprint"] == "b" * 64
  shell.pip_warning.sample.return_value = False, False
  with patch.object(runtime_app, "ui_state", SimpleNamespace(params=object(), sm=object(), started_frame=0)), \
       patch.object(runtime_app, "display_message", return_value=car), \
       patch.object(runtime_app, "clock_pair_ns", return_value=(101, 101)), \
       patch.object(runtime_app.time, "monotonic_ns", return_value=101), \
       patch.dict("os.environ", {"STARPILOT_PIP_DEV": "1", "STARPILOT_VASM_DEVELOPMENT": "0"}):
    shell._render_pip(SimpleNamespace(x=0), state)
    assert shell.pip_warning.sample.call_args.kwargs["enabled"] is True
    assert shell.pip_renderer.render.call_args.args[2].right_blindspot
  with patch.object(runtime_app, "ui_state", SimpleNamespace(params=object(), sm=object(), started_frame=0)), \
       patch.object(runtime_app, "display_message", return_value=None), \
       patch.object(runtime_app.time, "monotonic_ns", return_value=102), \
       patch.dict("os.environ", {"STARPILOT_PIP_DEV": "1", "STARPILOT_VASM_DEVELOPMENT": "1"}):
    shell._render_pip(SimpleNamespace(x=0), state)
    assert shell.pip_warning.sample.call_args.kwargs["enabled"] is False
  rendered_before = shell.pip_renderer.render.call_count
  with patch.object(runtime_app, "ui_state", SimpleNamespace(params=object(), sm=object(), started_frame=0)), \
       patch.object(runtime_app, "display_message", return_value=car), \
       patch.object(runtime_app, "clock_pair_ns", return_value=(102, 102)), \
       patch.dict("os.environ", {"STARPILOT_PIP_DEV": "0", "STARPILOT_VASM_DEVELOPMENT": "1"}), \
       patch.object(runtime_app.time, "monotonic_ns", return_value=102):
    shell._render_pip(SimpleNamespace(x=0), state)
    assert shell.pip_renderer.render.call_count == rendered_before + 1
    assert shell.pip_renderer.render.call_args.args[2].right_blindspot
    shell.pip_warning.close.assert_not_called()
    shell.pip_renderer.deactivate.assert_not_called()
  for valid, gear in ((False, runtime_app.car_schema.CarState.GearShifter.drive),
                      (True, runtime_app.car_schema.CarState.GearShifter.park)):
    car.canValid, car.gearShifter = valid, gear
    with patch.object(runtime_app, "ui_state", SimpleNamespace(params=object(), sm=object(), started_frame=0)), \
         patch.object(runtime_app, "display_message", return_value=car), \
         patch.object(runtime_app, "clock_pair_ns", return_value=(103, 103)), \
         patch.object(runtime_app.time, "monotonic_ns", return_value=103), \
         patch.dict("os.environ", {"STARPILOT_PIP_DEV": "1", "STARPILOT_VASM_DEVELOPMENT": "1"}):
      shell._render_pip(SimpleNamespace(x=0), state)
      assert shell.pip_warning.sample.call_args.kwargs["enabled"] is False
      assert shell.pip_renderer.render.call_args.args[2].right_blindspot

  saved.enabled = False
  with patch.object(runtime_app.time, "monotonic_ns", return_value=104):
    rendered_before = shell.pip_renderer.render.call_count
    shell._render_pip(SimpleNamespace(x=0), state)
    shell.pip_warning.close.assert_not_called()
    shell.pip_renderer.deactivate.assert_called_once()
    assert shell.pip_renderer.render.call_count == rendered_before


def test_native_pip_onroad_exit_closes_warning_and_invalidates_saved_snapshot():
  from openpilot.starpilot.ui import runtime_app
  from openpilot.starpilot.ui.presentation import Profile
  from openpilot.starpilot.ui.settings_state import Destination
  from openpilot.starpilot.ui.shell import ShellMode
  shell = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
  shell.slc_actions = None
  shell._pip_read_ns = 42
  shell._pip_saved = SavedPiP((), False, None, False, False, False)
  shell._vasm_saved = SavedPreferences(None, True, True)
  shell.pip_warning = Mock()
  shell.pip_renderer = Mock()
  shell.favorites = Mock()
  shell.profile = Profile.LARGE
  shell.notice = ""
  shell.view = Mock()
  shell.snapshot = Mock(return_value=SimpleNamespace(
    onroad=SimpleNamespace(camera_available=False), selected=Destination.STAR))
  with patch.object(runtime_app, "placed_at", return_value=nullcontext()):
    shell.render(ShellMode.SETTINGS, SimpleNamespace())
  shell.pip_warning.close.assert_called_once()
  shell.pip_renderer.deactivate.assert_called_once()
  assert shell._pip_read_ns is None and shell._pip_saved is None and shell._vasm_saved is None


def test_frozen_c3_and_c4_geometry_without_stretch():
  content = Rect(30, 30, 1800, 1020)
  assert bubble_rect(content, "left") == Rect(54, 414, 612, 612)
  assert bubble_rect(content, "right") == Rect(1194, 414, 612, 612)
  assert curved_crop(Crop(50, 150, 580), Rect(0, 0, 1200, 600)) == Rect(50, 295, 580, 290)
  assert curved_crop(Crop(50, 150, 580), Rect(0, 0, 600, 1200)) == Rect(195, 150, 290, 580)


def test_frozen_shape_shaders_crop_and_mask_before_camera_reads():
  for shader, discard in ((PIP_FRAGMENT_SHADER, "if (radius > 1.0 + aa)"),
                          (PIP_CURVED_FRAGMENT_SHADER, "if (dist > aa)")):
    assert shader.index(discard) < shader.index("texture(texture0")
    assert shader.count("texture(texture0") == 1
    assert shader.count("texture(texture1") == 1
    assert "cropCoord.x = 1.0 - cropCoord.x" in shader
    assert "uCropMin" in shader and "uCropSize" in shader
    external = _external_shader(shader)
    assert "samplerExternalOES texture0" in external
    assert "texture(texture1" not in external


def test_renderer_uses_saved_widget_position_and_removal_deactivates_camera():
  mask = Mask.parse({"width": 100, "height": 100, "center_left": [25, 50],
                     "center_right": [75, 50], "crop_size": 20})
  signals = Signals(True, True, True, False, False)
  targets = {"left": Rect(100, 200, 300, 300), "right": Rect(900, 400, 300, 300)}
  stream = Mock(spec=PiPStream)
  stream.generation = 0
  stream.poll.return_value = SimpleNamespace(width=100, height=100, stride=100)
  renderer = PiPRenderer("bubble", stream, frame_availability=Mock())
  with patch.object(renderer, "_shader", return_value=object()), patch.object(renderer, "_texture", return_value=object()), \
       patch.object(renderer, "_draw") as draw:
    assert renderer.render(Rect(0, 0, 1800, 1020), mask, signals, enabled=True, on_blinker=True, on_bsm=True,
                           invert=False, now=1, placements=targets) == "rendered"
    assert [call.args[3] for call in draw.call_args_list] == [targets["right"], targets["left"]]
    draw.reset_mock()
    warning = Signals(True, True, True, False, False, True, False)
    renderer.render(Rect(0, 0, 1800, 1020), mask, warning, enabled=True, on_blinker=True, on_bsm=True,
                    invert=False, now=2, placements={"left": targets["left"]})
    assert draw.call_count == 1
    assert draw.call_args.args[3] == targets["left"]
    assert draw.call_args.kwargs["warning"] is True
    draw.reset_mock()
    renderer.render(Rect(0, 0, 1800, 1020), mask, signals, enabled=True, on_blinker=True, on_bsm=True,
                    invert=False, now=3, placements={"left": targets["left"]})
    assert draw.call_args.kwargs["warning"] is False
    count = stream.poll.call_count
    assert renderer.render(Rect(0, 0, 1800, 1020), mask, signals, enabled=True, on_blinker=True, on_bsm=True,
                           invert=False, now=4, placements={}) == "inactive"
    assert stream.poll.call_count == count
    stream.set_active.assert_called_with(False)



def test_curved_pip_draws_red_rim_only_for_warning():
  renderer = PiPRenderer("curved", Mock(spec=PiPStream), frame_availability=Mock())
  frame = SimpleNamespace(width=100, height=100, stride=100)
  with patch.object(pip_render, "rl") as graphics, patch.object(pip_render, "COMMA_HARDWARE", False):
    renderer._draw(Mock(), Mock(), frame, Rect(10, 20, 200, 100), 0, 0, 50, False, warning=True)
    graphics.draw_rectangle_rounded_lines_ex.assert_called_once()
    assert graphics.draw_rectangle_rounded_lines_ex.call_args.args[1:4] == (0.44, 16, 4)
    graphics.draw_rectangle_rounded_lines_ex.reset_mock()
    renderer._draw(Mock(), Mock(), frame, Rect(10, 20, 200, 100), 0, 0, 50, False)
    graphics.draw_rectangle_rounded_lines_ex.assert_not_called()


def test_renderer_c4_uses_recent_side_and_keeps_camera_warm_while_hidden():
  class Stream(PiPStream):
    def __init__(self):
      self.active = False
      self.polls = 0
      self._generation = 0
      self._retired_clients = []
    def set_active(self, active):
      self.active = active
    def poll(self, now, *, now_boot_ns=None):
      self.polls += 1
      return type("Frame", (), {"width": 100, "height": 100, "stride": 100})()
  mask = Mask.parse({"width": 100, "height": 100, "center_left": [25, 50],
                     "center_right": [75, 50], "crop_size": 20})
  assert mask is not None
  stream = Stream()
  renderer = PiPRenderer("curved", stream, frame_availability=Mock())
  content = type("Content", (), {"x": 0, "y": 0, "width": 476, "height": 240})()
  signals = Signals(True, False, False, False, False)
  assert renderer.render(content, mask, signals, enabled=True,
                         on_blinker=True, on_bsm=True, invert=False, now=1) == "inactive"
  # Hidden, the cabin stream stays connected and current for the next blinker.
  assert stream.active and stream.polls == 1
  assert renderer.render(content, mask, signals, enabled=False,
                         on_blinker=True, on_bsm=True, invert=False, now=1.5) == "inactive"
  assert not stream.active and stream.polls == 1
  assert renderer.render(content, None, signals, enabled=True,
                         on_blinker=True, on_bsm=True, invert=False, now=1.6) == "inactive"
  assert not stream.active and stream.polls == 1
  right = Signals(True, False, True, False, False)
  left = Signals(True, True, False, False, False)
  with patch.object(renderer, "_shader", return_value=object()), patch.object(renderer, "_texture", return_value=object()), \
       patch.object(renderer, "_draw") as draw:
    assert renderer.render(content, mask, right, enabled=True,
                           on_blinker=True, on_bsm=False, invert=False, now=2) == "rendered"
    assert draw.call_args.args[3].width == 476
    assert draw.call_args.args[4:6] == (15, 40)  # vehicle-right is image-left
    both = Signals(True, True, True, False, False)
    renderer.render(content, mask, both, enabled=True,
                    on_blinker=True, on_bsm=False, invert=False, now=3)
    assert draw.call_args.args[4:6] == (65, 40)  # newly active vehicle-left
    renderer.render(content, mask, left, enabled=True,
                    on_blinker=True, on_bsm=False, invert=False, now=4)
    assert draw.call_args.args[4:6] == (65, 40)


def test_renderer_shows_first_blinker_frame_without_reconnecting():
  frames = [SimpleNamespace(frame_id=i, width=100, height=100, stride=100) for i in range(1, 4)]
  client = _Client(frames)
  factory = Mock(return_value=client)
  renderer = PiPRenderer("bubble", PiPStream(factory, require_boot_eof=False), frame_availability=Mock())
  mask = Mask.parse({"width": 100, "height": 100, "center_left": [25, 50],
                     "center_right": [75, 50], "crop_size": 20})
  idle, blinker = Signals(True, False, False, False, False), Signals(True, True, False, False, False)
  with patch.object(renderer, "_shader", return_value=object()), patch.object(renderer, "_texture", return_value=object()), \
       patch.object(renderer, "_draw") as draw:
    for now in (1.0, 1.05):
      assert renderer.render(Rect(0, 0, 1800, 1020), mask, idle, enabled=True, on_blinker=True, on_bsm=True,
                             invert=False, now=now) == "inactive"
    assert renderer.render(Rect(0, 0, 1800, 1020), mask, blinker, enabled=True, on_blinker=True, on_bsm=True,
                           invert=False, now=1.1) == "rendered"
  assert draw.call_count == 1
  assert factory.call_count == 1 and client.connect_calls == 1 and client.recv_calls == 3


def test_padded_nv12_uses_actual_uv_offset_and_texture_stride():
  renderer = PiPRenderer("bubble", frame_availability=Mock())
  y = bytes([1, 2, 3, 4, 201, 202, 203, 204, 5, 6, 7, 8, 205, 206, 207, 208])
  aligned_y = bytes([209] * 8)
  uv = bytes([9, 10, 11, 12, 210, 211, 212, 213])
  frame = SimpleNamespace(width=4, height=2, stride=8, uv_offset=24, frame_id=1,
                          data=memoryview(y + aligned_y + uv))
  uploaded = []
  textures = iter((SimpleNamespace(id=1), SimpleNamespace(id=2)))
  def upload(texture, pointer):
    uploaded.append((texture.id, bytes(pointer[:16 if texture.id == 1 else 8])))
  with patch("openpilot.starpilot.ui.pip_render.COMMA_HARDWARE", False), \
       patch("openpilot.starpilot.ui.pip_render.rl.load_texture_from_image", side_effect=lambda image: next(textures)), \
       patch("openpilot.starpilot.ui.pip_render.rl.update_texture", side_effect=upload):
    assert renderer._texture(frame) is not None
  assert uploaded == [(1, y), (2, uv)]
  frame.data = memoryview(y + uv)  # UV at the wrong offset must fail closed.
  frame.frame_id = 2
  with patch("openpilot.starpilot.ui.pip_render.COMMA_HARDWARE", False):
    assert renderer._texture(frame) is None


def test_renderer_close_releases_every_gpu_and_ipc_resource_once():
  renderer = PiPRenderer("bubble", frame_availability=Mock())
  renderer.shader = SimpleNamespace(id=9)
  renderer.texture_y = SimpleNamespace(id=1)
  renderer.texture_uv = SimpleNamespace(id=2)
  renderer.egl_texture = SimpleNamespace(id=3)
  first, second = EGLImage("image-0", 0), EGLImage("image-1", 1)
  renderer.egl_images = {0: first, 1: second}
  textures, shaders, images = [], [], []
  with patch("openpilot.starpilot.ui.pip_render.rl.unload_texture", side_effect=lambda item: textures.append(item.id)), \
       patch("openpilot.starpilot.ui.pip_render.rl.unload_shader", side_effect=lambda item: shaders.append(item.id)), \
       patch("openpilot.starpilot.ui.pip_render.destroy_egl_image", side_effect=images.append):
    renderer.close()
    renderer.close()
  assert textures == [1, 2, 3]
  assert shaders == [9]
  assert images == [first, second]
  assert not renderer.stream.connected


@dataclass
class _Frame:
  frame_id: int


class _Client:
  num_buffers = 4
  width = 1928
  height = 1208
  stride = 2048

  def __init__(self, frames=()):
    self.frames = list(frames)
    self.connected = False
    self.connect_calls = 0
    self.recv_calls = 0

  def is_connected(self):
    return self.connected

  def connect(self, block):
    assert block is False
    self.connect_calls += 1
    self.connected = True
    return True

  def recv(self, timeout_ms):
    assert timeout_ms == 0
    self.recv_calls += 1
    return self.frames.pop(0) if self.frames else None


def test_real_stream_factory_selects_camerad_cabin_only():
  with patch("msgq.visionipc.VisionIpcClient") as make:
    from openpilot.cereal.visionipc import VisionStreamType
    from openpilot.starpilot.ui.pip_sidecam import cabin_client
    cabin_client()
    make.assert_called_once_with("camerad", VisionStreamType.VISION_STREAM_CABIN, conflate=True)


def test_stream_lazy_connect_nonblocking_frame_expiry_and_release():
  client = _Client([_Frame(4)])
  stream = PiPStream(lambda: client, require_boot_eof=False)
  assert stream.poll(1.0) is None  # offroad means no IPC open
  stream.set_active(True)
  assert stream.poll(1.0) == _Frame(4)
  assert stream.frame_size == (1928, 1208)
  assert stream.poll(1.4) == _Frame(4)
  assert stream.poll(1.51) is None
  assert not stream.connected
  assert stream.poll(1.6) is None  # failed connection cannot revive stale frame
  stream.close()
  assert stream.poll(2.0) is None


def test_stream_keeps_new_client_until_first_frame_or_stale_window():
  client = _Client([None, None, _Frame(4)])
  stream = PiPStream(lambda: client, require_boot_eof=False)
  stream.set_active(True)
  assert stream.poll(1.0) is None  # nothing queued yet right after connect
  assert stream.connected
  assert stream.poll(1.2) is None
  assert stream.poll(1.3) == _Frame(4)
  assert client.connect_calls == 1

  silent = _Client()
  stream = PiPStream(lambda: silent, require_boot_eof=False)
  stream.set_active(True)
  assert stream.poll(1.0) is None
  assert stream.poll(1.5) is None and stream.connected
  assert stream.poll(1.51) is None
  assert not stream.connected


def test_stream_disconnect_reconnect_and_nonincreasing_frame_id():
  first = _Client([_Frame(10), _Frame(10)])
  second = _Client([_Frame(1)])
  clients = iter((first, second))
  stream = PiPStream(lambda: next(clients), require_boot_eof=False)
  stream.set_active(True)
  assert stream.poll(0.0) == _Frame(10)
  assert stream.poll(0.05) is None  # duplicate ID drops buffered image
  assert stream.poll(0.1) is None  # retry throttled
  assert stream.poll(0.21) == _Frame(1)  # producer reset is a new session
  second.connected = False
  assert stream.poll(0.22) is None  # loss never displays last frame


def test_renderer_same_poll_reconnect_rebinds_new_fd_before_releasing_old_client():
  first = _Client([SimpleNamespace(width=100, height=100, stride=100, uv_offset=10000,
                                   frame_id=10, idx=0, fd=11)])
  second = _Client([SimpleNamespace(width=100, height=100, stride=100, uv_offset=10000,
                                    frame_id=11, idx=0, fd=12)])
  clients = iter((first, second))
  stream = PiPStream(lambda: next(clients), require_boot_eof=False)
  renderer = PiPRenderer("bubble", stream, frame_availability=Mock())
  mask = Mask.parse({"width": 100, "height": 100, "center_left": [25, 50],
                     "center_right": [75, 50], "crop_size": 20})
  assert mask is not None
  content = SimpleNamespace(x=0, y=0, width=200, height=200)
  signal = Signals(True, True, False, False, False)
  events = []
  def destroy(image):
    if image.egl_image == 11:
      assert first in stream._retired_clients
    events.append(("destroy", image.egl_image))
  with patch("openpilot.starpilot.ui.pip_render.COMMA_HARDWARE", True), \
       patch.object(renderer, "_shader", return_value=object()), patch.object(renderer, "_draw"), \
       patch("openpilot.starpilot.ui.pip_render.rl.gen_image_color", return_value=object()), \
       patch("openpilot.starpilot.ui.pip_render.rl.load_texture_from_image", side_effect=lambda _: SimpleNamespace(id=7)), \
       patch("openpilot.starpilot.ui.pip_render.rl.unload_image"), \
       patch("openpilot.starpilot.ui.pip_render.rl.unload_texture"), \
       patch("openpilot.starpilot.ui.pip_render.create_egl_image",
             side_effect=lambda *args: events.append(("create", args[3])) or EGLImage(args[3], args[3])), \
       patch("openpilot.starpilot.ui.pip_render.bind_egl_image_to_texture",
             side_effect=lambda _, image: events.append(("bind", image.egl_image))), \
       patch("openpilot.starpilot.ui.pip_render.destroy_egl_image", side_effect=destroy):
    assert renderer.render(content, mask, signal, enabled=True,
                           on_blinker=True, on_bsm=False, invert=False, now=1.0) == "rendered"
    first.connected = False
    assert renderer.render(content, mask, signal, enabled=True,
                           on_blinker=True, on_bsm=False, invert=False, now=1.21) == "rendered"
    assert events == [("create", 11), ("bind", 11), ("destroy", 11), ("create", 12), ("bind", 12)]
    assert first not in stream._retired_clients
    assert renderer.render(content, mask, signal, enabled=True,
                           on_blinker=True, on_bsm=False, invert=False, now=1.8) == "no_frame"
    assert events[-1] == ("destroy", 12)
    assert not stream._retired_clients
    renderer.close()
    renderer.close()


def test_smaller_cabin_format_automatically_scales_saved_mask():
  frame = SimpleNamespace(width=1344, height=760, stride=1344, frame_id=1)
  client = _Client([frame])
  stream = PiPStream(lambda: client, require_boot_eof=False)
  renderer = PiPRenderer("bubble", stream, frame_availability=Mock())
  content = SimpleNamespace(x=0, y=0, width=200, height=200)
  signal = Signals(True, True, False, False, False)
  with patch.object(renderer, "_shader", return_value=object()), \
       patch.object(renderer, "_texture", return_value=object()), patch.object(renderer, "_draw"):
    assert renderer.render(content, starting_mask(1928, 1208), signal, enabled=True,
                           on_blinker=True, on_bsm=False, invert=False, now=1.0) == "rendered"
    assert renderer.render(content, starting_mask(1344, 760), signal, enabled=True,
                           on_blinker=True, on_bsm=False, invert=False, now=1.1) == "rendered"


def test_device_camera_eof_uses_boottime_across_suspend():
  fresh = _Client([_Frame(7)])
  fresh.timestamp_eof = 1_300_000_000
  stream = PiPStream(lambda: fresh, require_boot_eof=True)
  stream.set_active(True)
  assert stream.poll(1.0, now_boot_ns=1_400_000_000) == _Frame(7)
  assert stream.poll(1.1, now_boot_ns=2_000_000_000) is None  # MONO grew 100 ms, BOOT grew 600 ms
  old = _Client([_Frame(8)])
  old.timestamp_eof = 1_000_000_000
  stale = PiPStream(lambda: old, require_boot_eof=True)
  stale.set_active(True)
  assert stale.poll(1.0, now_boot_ns=1_600_000_001) is None  # queued pre-start frame
  for eof in (None, 0, 1_400_000_001):
    invalid = _Client([_Frame(9)])
    invalid.timestamp_eof = eof
    guarded = PiPStream(lambda invalid=invalid: invalid, require_boot_eof=True)
    guarded.set_active(True)
    assert guarded.poll(1.0, now_boot_ns=1_400_000_000) is None
    assert not guarded.connected  # missing, zero or future capture times fail closed


def test_device_camera_eof_samples_boot_after_connect_and_receive():
  class DelayedClient(_Client):
    def recv(self, timeout_ms):
      frame = super().recv(timeout_ms)
      # A new camerad frame arrived during connect/recv, after poll began.
      self.timestamp_eof = 1_300_000_000
      return frame

  client = DelayedClient([_Frame(9)])
  stream = PiPStream(lambda: client, require_boot_eof=True)
  stream.set_active(True)
  def boot_clock(_clock):
    # Before recv this is the earlier clock sample. After recv it reflects
    # the fresh camera timestamp and must be the one used for validation.
    return 1_000_000_000 if client.recv_calls == 0 else 1_400_000_000

  with patch.object(time, "CLOCK_BOOTTIME", 7, create=True), \
       patch.object(time, "clock_gettime_ns", side_effect=boot_clock):
    assert stream.poll(1.0) == _Frame(9)


def test_offroad_close_drops_client_and_borrowed_frame():
  client = _Client([_Frame(3)])
  ref = weakref.ref(client)
  holder = [client]
  stream = PiPStream(lambda: holder[0], require_boot_eof=False)
  stream.set_active(True)
  assert stream.poll(1.0) is not None
  stream.set_active(False)
  assert stream.frame_size is None
  assert stream.poll(1.1) is None
  holder.clear()
  del client
  gc.collect()
  assert ref() is None


def test_failed_connect_is_bounded_and_does_not_hold_client():
  class Refusing(_Client):
    def connect(self, block):
      self.connect_calls += 1
      return False
  attempts = []
  def factory():
    client = Refusing()
    attempts.append(weakref.ref(client))
    return client
  stream = PiPStream(factory, require_boot_eof=False)
  stream.set_active(True)
  assert stream.poll(0.0) is None
  assert stream.poll(0.1) is None
  assert len(attempts) == 1
  assert stream.poll(0.2) is None
  assert len(attempts) == 2
  assert all(ref() is None for ref in attempts)


def test_real_local_cabin_visionipc_frame_and_offroad_release():
  from msgq.visionipc import VisionIpcClient, VisionIpcServer
  from openpilot.cereal.visionipc import VisionStreamType
  name = f"pip_test_{uuid.uuid4().hex}"
  stream_type = VisionStreamType.VISION_STREAM_CABIN
  server = VisionIpcServer(name)
  server.create_buffers(stream_type, 2, 64, 48)
  server.start_listener()
  client = VisionIpcClient(name, stream_type, conflate=True)
  assert client.connect(True)
  assert client.buffer_len is not None
  stream = PiPStream(lambda: client, require_boot_eof=True)
  stream.set_active(True)
  payload = bytes([87]) * client.buffer_len
  capture_eof = 1_000_000_000
  server.send(stream_type, payload, frame_id=7, timestamp_eof=capture_eof)
  frame = stream.poll(1.0, now_boot_ns=capture_eof + 100_000_000)
  assert frame is not None and frame.frame_id == 7
  assert client.timestamp_eof == capture_eof
  assert frame.data[0] == 87
  assert stream.frame_size == (64, 48)
  stream.close()
  assert stream.poll(1.1) is None


class TestPiPSidecam(unittest.TestCase):
  def test_mask_maps_raw_sides(self):
    test_mask_maps_image_left_to_vehicle_right_and_rejects_unbounded_crop()

  def test_selected_sides(self):
    test_selected_sides_use_pip_triggers_including_vasm()

  def test_visual_warning_expiry_and_oem_bsm(self):
    test_visual_warning_event_expires_without_refresh_and_preserves_oem_bsm()

  def test_visual_warning_invalid_and_close(self):
    test_visual_warning_invalid_message_clears_and_inactive_unsubscribes()

  def test_visual_warning_close_error(self):
    test_visual_warning_close_failure_still_drops_source()

  def test_native_visual_sides(self):
    test_native_pip_passes_only_qualified_visual_sides_without_replacing_oem_bsm()

  def test_native_onroad_exit(self):
    test_native_pip_onroad_exit_closes_warning_and_invalidates_saved_snapshot()

  def test_geometry(self):
    test_frozen_c3_and_c4_geometry_without_stretch()

  def test_shape_shaders(self):
    test_frozen_shape_shaders_crop_and_mask_before_camera_reads()

  def test_renderer_selection(self):
    test_renderer_c4_uses_recent_side_and_keeps_camera_warm_while_hidden()

  def test_renderer_warm_stream(self):
    test_renderer_shows_first_blinker_frame_without_reconnecting()

  def test_padded_nv12(self):
    test_padded_nv12_uses_actual_uv_offset_and_texture_stride()

  def test_renderer_close(self):
    test_renderer_close_releases_every_gpu_and_ipc_resource_once()

  def test_real_stream_factory(self):
    test_real_stream_factory_selects_camerad_cabin_only()

  def test_stream_expiry(self):
    test_stream_lazy_connect_nonblocking_frame_expiry_and_release()

  def test_stream_reconnect(self):
    test_stream_disconnect_reconnect_and_nonincreasing_frame_id()

  def test_renderer_same_poll_reconnect(self):
    test_renderer_same_poll_reconnect_rebinds_new_fd_before_releasing_old_client()

  def test_smaller_cabin_format(self):
    test_smaller_cabin_format_automatically_scales_saved_mask()

  def test_boot_eof(self):
    test_device_camera_eof_uses_boottime_across_suspend()

  def test_boot_eof_after_receive(self):
    test_device_camera_eof_samples_boot_after_connect_and_receive()

  def test_offroad_close(self):
    test_offroad_close_drops_client_and_borrowed_frame()

  def test_failed_connect(self):
    test_failed_connect_is_bounded_and_does_not_hold_client()

  def test_local_visionipc(self):
    test_real_local_cabin_visionipc_frame_and_offroad_release()


def test_camera_resolution_adaptation_preserves_saved_crop_and_side_identity():
  mask = starting_mask(1928, 1208)
  changed = mask.for_frame(1344, 760)
  assert changed is not None and changed.width == 1344 and changed.height == 760
  assert changed.center_left == (315 * 1344 / 1928, 548 * 760 / 1208)
  assert changed.center_right == (1571 * 1344 / 1928, 539 * 760 / 1208)
  assert changed.crop('right').x == changed.center_left[0] - changed.crop_size / 2
  assert mask.width == 1928 and mask.center_left == (315, 548)
  assert mask.for_frame(1928, 1208) is mask
  assert mask.for_frame(640, 480) is None
