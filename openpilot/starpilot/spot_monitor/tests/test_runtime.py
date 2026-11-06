"""Synthetic serialized cabin planes and fail-closed observation lifecycle."""

from pathlib import Path
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.spot_monitor.inference import VASMInference
from openpilot.starpilot.spot_monitor.policy import decode_annotation
from openpilot.starpilot.spot_monitor.preferences import Preferences, encode
from openpilot.starpilot.spot_monitor.runtime import CpuBudget, VASMRuntime, cabin_client_factory, copy_cabin_nv12, publish_observation


class State:
  def __init__(self, **kwargs):
    self.__dict__.update(kwargs)


class Messages:
  def __init__(self, clock):
    from opendbc.car.structs import car
    self.clock = clock
    self.seen = dict.fromkeys(("deviceState", "carState"), True)
    self.alive = self.seen.copy()
    self.valid = self.seen.copy()
    self.logMonoTime = dict.fromkeys(self.seen, clock[0])
    self.recv_time = dict.fromkeys(self.seen, clock[0] / 1e9)
    self.values = {"deviceState": State(started=True, cpuUsagePercent=[10.0] * 8),
                   "carState": State(canValid=True, gearShifter=car.CarState.GearShifter.drive)}

  def __getitem__(self, name):
    return self.values[name]

  def update(self, timeout):
    assert timeout == 0

  def refresh(self):
    for name in self.seen:
      self.logMonoTime[name] = self.clock[0]
      self.recv_time[name] = self.clock[0] / 1e9


class Client:
  width, height, stride, uv_offset = 32, 32, 40, 1320

  def __init__(self):
    self.frames = []
    self.connected = True

  def is_connected(self):
    return self.connected

  def connect(self, blocking):
    assert blocking is False
    self.connected = True

  def recv(self, timeout):
    assert timeout == 0
    if not self.frames:
      return None
    self.timestamp_eof, self.frame_id, raw = self.frames.pop(0)
    return State(data=raw)

  def add(self, eof, frame_id):
    raw = bytearray(self.uv_offset + self.stride * self.height // 2)
    for y in range(self.height):
      raw[y * self.stride:y * self.stride + self.width] = bytes([y]) * self.width
    raw[self.uv_offset:] = bytes([128]) * (len(raw) - self.uv_offset)
    self.frames.append((eof, frame_id, bytes(raw)))


class Model(VASMInference):
  def __init__(self):
    super().__init__(Path("/external/model.onnx"))
    self.loaded_sha256 = "5d20cdbb457ba18db51a537ee2e305bbe442264b1613956068d473e35d15900d"

  def load(self):
    return True

  def infer_nv12(self, frame, **kwargs):
    assert frame.shape == (48, 32) and frame[31, 0] == 31 and frame[32, 0] == 128
    return 0.96


def test_stride_uv_offset_copy_and_short_buffer_refused():
  client = Client()
  client.add(2_000_000_000, 0)
  copied = copy_cabin_nv12(client)
  assert copied is not None
  image, eof, frame_id = copied
  assert image.shape == (48, 32) and eof == 2_000_000_000 and frame_id == 0
  assert image[31, 0] == 31 and image[32, 0] == 128
  client.frames.append((2_000_000_100, 1, b"short"))
  assert copy_cabin_nv12(client) is None


def test_real_vision_stream_enum_selects_only_existing_camerad_cabin():
  from openpilot.cereal.visionipc import VisionStreamType

  calls = []
  def constructor(*args):
    calls.append(args)
    return object()
  cabin_client_factory(constructor, VisionStreamType)
  assert calls == [("camerad", VisionStreamType.VISION_STREAM_CABIN, True)]


def test_cpu_budget_frozen_target_smoothing_and_invalid_conservative():
  budget = CpuBudget()
  assert budget.update([10.0] * 8, now_mono_ns=1_000_000_000) == 1.0
  busy = budget.update([90.0] * 8, now_mono_ns=6_000_000_000)
  assert 3.3 < busy < 3.6  # frozen hot-core target 3.5, smoothed
  recovered = budget.update([0.0] * 8, now_mono_ns=11_000_000_000)
  assert 1.0 < recovered < 1.1
  assert budget.update([], now_mono_ns=12_000_000_000) == 4.0
  assert budget.update([float("nan")], now_mono_ns=13_000_000_000) == 4.0
  assert budget.update([True], now_mono_ns=14_000_000_000) == 4.0


def test_vasm_uses_its_own_core_with_vision_and_restores_solo_cores(tmp_path):
  params = Params(str(tmp_path / "params"))
  clock = [1_000_000_000]
  runtime = VASMRuntime(params, Messages(clock), Client, lambda _event: None)
  with patch('openpilot.starpilot.spot_monitor.runtime.feature_requested', side_effect=[True, False, False]), \
       patch('openpilot.starpilot.spot_monitor.runtime.set_core_affinity') as affinity:
    runtime._refresh_settings(clock[0])
    assert runtime.inference_cores == (2,)
    clock[0] += 2_000_000_000
    runtime._refresh_settings(clock[0])
    assert runtime.inference_cores == (0, 1, 2)
  assert [call.args[0] for call in affinity.call_args_list] == [[2], [0, 1, 2]]



def test_vasm_reserves_own_core_for_speed_or_stop_vision_requests(tmp_path):
  params = Params(str(tmp_path / "params"))
  clock = [1_000_000_000]
  runtime = VASMRuntime(params, Messages(clock), Client, lambda _event: None)
  for vision, vision_stop in ((False, False), (True, False), (False, True), (True, True)):
    requests = {"vision": vision, "vision_stop": vision_stop}
    clock[0] += 2_000_000_000
    with patch('openpilot.starpilot.spot_monitor.runtime.feature_requested',
               side_effect=lambda _params, feature, requests=requests: requests[feature]), \
         patch('openpilot.starpilot.spot_monitor.runtime.set_core_affinity') as affinity:
      runtime._refresh_settings(clock[0])
      expected = (2,) if vision or vision_stop else (0, 1, 2)
      assert runtime.inference_cores == expected
      affinity.assert_called_once_with(list(expected))

def test_enabled_fresh_drive_new_frames_and_stale_reset(tmp_path):
  from opendbc.car.structs import car

  annotation = decode_annotation(b'{"version":1,"width":32,"height":32,"poly_left":[[0,0],[32,0],[32,32],[0,32]],"poly_right":[]}')
  params = Params(str(tmp_path / "params"))
  path = Path(params.get_param_path("VASMPreferences"))
  path.write_bytes(encode(Preferences(True, annotation)))
  clock = [1_000_000_000]
  messages = Messages(clock)
  client = Client()
  client.add(2_000_000_000, 0)
  events = []
  runtime = VASMRuntime(params, messages, lambda: client, events.append, model_path=Path("/external/model.onnx"),
                        mono_clock=lambda: clock[0], pair_clock=lambda: (clock[0], clock[0] + 1_000_000_000),
                        model_factory=lambda _: Model())
  event = runtime.tick()
  assert event is not None and event.status == "valid" and event.frame_id == 0
  assert event.display_right and not event.display_left
  assert event.left_frame_id == 0 and event.left_eof_boot_ns == 2_000_000_000
  assert event.observed_mono_ns == clock[0] and event.observed_boot_ns == clock[0] + 1_000_000_000
  assert event.sequence == 1
  assert len(event.settings_fingerprint) == 64 and len(event.session) == 32
  sent = []
  publish_observation(State(send=lambda service, message: sent.append((service, message))), event)
  service, envelope = sent[0]
  assert service == "spotMonitorState" and envelope.valid and envelope.logMonoTime == event.observed_mono_ns
  assert envelope.spotMonitorState.desiredCurvature == 0
  wire = envelope.spotMonitorState.observation
  assert wire.version == 1 and wire.sequence == 1 and wire.sourceFrameId == 0
  assert wire.right.status == "warning" and wire.right.sourceFrameEofBootTime == event.left_eof_boot_ns
  assert wire.left.status == "unknown" and wire.left.sourceFrameEofBootTime == 0
  clock[0] += 1_100_000_000
  messages.refresh()
  client.add(clock[0] + 1_000_000_000, 0)
  assert runtime.tick() is None  # duplicate frame cannot renew held warning
  assert events[-1].status == "stale" and not events[-1].display_right
  clock[0] += 600_000_000
  messages.refresh()
  messages.values["carState"].gearShifter = car.CarState.GearShifter.park
  assert runtime.tick() is None
  assert events[-1].status == "unavailable"
  assert not events[-1].display_right


def test_alternating_sides_do_not_renew_other_source_and_resume_resets(tmp_path):
  annotation = decode_annotation(b'{"version":1,"width":32,"height":32,"poly_left":[[0,0],[32,0],[32,32],[0,32]],"poly_right":[[0,0],[32,0],[32,32],[0,32]]}')
  params = Params(str(tmp_path / "params"))
  path = Path(params.get_param_path("VASMPreferences"))
  path.write_bytes(encode(Preferences(True, annotation)))
  clock = [1_000_000_000]
  offset = [1_000_000_000]
  messages = Messages(clock)
  client = Client()
  client.add(2_000_000_000, 0)
  events = []
  runtime = VASMRuntime(params, messages, lambda: client, events.append, model_path=Path("/external/model.onnx"),
                        mono_clock=lambda: clock[0], pair_clock=lambda: (clock[0], clock[0] + offset[0]),
                        model_factory=lambda _: Model())
  first = runtime.tick()
  assert first is not None and first.display_right and not first.display_left
  clock[0] += 350_000_000
  messages.refresh()
  client.add(clock[0] + offset[0], 1)
  second = runtime.tick()
  assert second is not None and second.display_right and second.display_left
  assert second.left_eof_boot_ns == first.left_eof_boot_ns
  assert second.right_eof_boot_ns > second.left_eof_boot_ns
  sent = []
  publish_observation(State(send=lambda service, message: sent.append(message)), second)
  wire = sent[0].spotMonitorState.observation
  assert wire.right.sourceFrameId == 0 and wire.left.sourceFrameId == 1
  assert wire.right.validUntilBootTime == first.left_eof_boot_ns + 3_000_000_000

  clock[0] += 1_100_000_000
  messages.refresh()
  offset[0] += 9_000_000_000  # suspend/resume clock-pair discontinuity
  client.add(clock[0] + offset[0], 2)
  assert runtime.tick() is None
  assert events[-1].status == "stale" and not events[-1].display_left and not events[-1].display_right


def test_authority_or_saved_doc_change_during_inference_denies_output(tmp_path):
  from opendbc.car.structs import car

  annotation = decode_annotation(b'{"version":1,"width":32,"height":32,"poly_left":[[0,0],[32,0],[32,32],[0,32]],"poly_right":[]}')
  params = Params(str(tmp_path / "params"))
  path = Path(params.get_param_path("VASMPreferences"))
  path.write_bytes(encode(Preferences(True, annotation)))
  clock = [1_000_000_000]
  messages = Messages(clock)
  client = Client()
  client.add(2_000_000_000, 0)
  events = []

  class ChangingModel(Model):
    def infer_nv12(self, frame, **kwargs):
      messages.values["carState"].gearShifter = car.CarState.GearShifter.park
      return super().infer_nv12(frame, **kwargs)

  runtime = VASMRuntime(params, messages, lambda: client, events.append, model_path=Path("/external/model.onnx"),
                        mono_clock=lambda: clock[0], pair_clock=lambda: (clock[0], clock[0] + 1_000_000_000),
                        model_factory=lambda _: ChangingModel())
  assert runtime.tick() is None
  assert all(event.status != "valid" for event in events)

  messages.values["carState"].gearShifter = car.CarState.GearShifter.drive
  client.add(2_000_000_100, 1)

  class SavingModel(Model):
    def infer_nv12(self, frame, **kwargs):
      path.write_bytes(encode(Preferences(False, annotation)))
      return super().infer_nv12(frame, **kwargs)

  runtime.model = SavingModel()
  assert runtime.tick() is None
  assert all(event.status != "valid" for event in events)


def test_reconnect_rejects_queued_preconnection_frame(tmp_path):
  annotation = decode_annotation(b'{"version":1,"width":32,"height":32,"poly_left":[[0,0],[32,0],[32,32],[0,32]],"poly_right":[]}')
  params = Params(str(tmp_path / "params"))
  path = Path(params.get_param_path("VASMPreferences"))
  path.write_bytes(encode(Preferences(True, annotation)))
  clock = [1_000_000_000]
  messages = Messages(clock)
  client = Client()
  client.add(2_000_000_000, 0)
  events = []
  runtime = VASMRuntime(params, messages, lambda: client, events.append, model_path=Path("/external/model.onnx"),
                        mono_clock=lambda: clock[0], pair_clock=lambda: (clock[0], clock[0] + 1_000_000_000),
                        model_factory=lambda _: Model())
  assert runtime.tick() is not None
  client.connected = False
  clock[0] += 100_000_000
  messages.refresh()
  client.add(2_050_000_000, 1)  # queued from before the new connection
  assert runtime.tick() is None
  clock[0] += 100_000_000
  messages.refresh()
  assert runtime.tick() is None and events[-1].status == "stale"
  assert all(event.sequence <= 1 for event in events)


def test_model_load_failure_retries_only_after_bounded_backoff(tmp_path):
  annotation = decode_annotation(b'{"version":1,"width":32,"height":32,"poly_left":[[0,0],[32,0],[32,32],[0,32]],"poly_right":[]}')
  params = Params(str(tmp_path / "params"))
  path = Path(params.get_param_path("VASMPreferences"))
  path.write_bytes(encode(Preferences(True, annotation)))
  clock = [1_000_000_000]
  messages = Messages(clock)
  attempts = []

  class BadModel(Model):
    def load(self):
      attempts.append(clock[0])
      return False

  runtime = VASMRuntime(params, messages, Client, lambda event: None,
                        model_path=Path("/external/model.onnx"), mono_clock=lambda: clock[0],
                        pair_clock=lambda: (clock[0], clock[0] + 1_000_000_000), model_factory=lambda _: BadModel())
  assert runtime.tick() is None
  clock[0] += 4_000_000_000
  messages.refresh()
  assert runtime.tick() is None
  assert attempts == [1_000_000_000]
  clock[0] += 1_000_000_000
  messages.refresh()
  assert runtime.tick() is None
  assert attempts == [1_000_000_000, 6_000_000_000]


def test_source_timestamp_advancing_during_post_inference_update_is_checked_with_final_clock(tmp_path):
  annotation = decode_annotation(b'{"version":1,"width":32,"height":32,"poly_left":[[0,0],[32,0],[32,32],[0,32]],"poly_right":[]}')
  params = Params(str(tmp_path / "params"))
  path = Path(params.get_param_path("VASMPreferences"))
  path.write_bytes(encode(Preferences(True, annotation)))
  clock = [1_000_000_000]

  class UpdatingMessages(Messages):
    calls = 0

    def update(self, timeout):
      super().update(timeout)
      self.calls += 1
      if self.calls == 3:  # final update after inference receives a newer state
        clock[0] += 100_000_000
        self.refresh()

  messages = UpdatingMessages(clock)
  client = Client()
  client.add(2_000_000_000, 0)
  runtime = VASMRuntime(params, messages, lambda: client, lambda event: None,
                        model_path=Path("/external/model.onnx"), mono_clock=lambda: clock[0],
                        pair_clock=lambda: (clock[0], clock[0] + 1_000_000_000), model_factory=lambda _: Model())
  event = runtime.tick()
  assert event is not None and event.status == "valid"
  assert event.observed_mono_ns == 1_100_000_000 and event.observed_boot_ns == 2_100_000_000
