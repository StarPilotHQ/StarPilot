"""Default-off onroad cabin observation; never starts cameras or controls a car."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import math
import os
from pathlib import Path
import signal
import threading
import time
from typing import Any
import uuid

import numpy as np

from openpilot.common.params import Params
from openpilot.common.realtime import set_core_affinity
from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.feature_runtime import requested as feature_requested
from openpilot.starpilot.speed_limits.vision.observation import clock_pair_ns
from openpilot.starpilot.spot_monitor.inference import DEFAULT_MODEL_PATH, VASMInference
from openpilot.starpilot.spot_monitor.policy import WarningPolicy, WarningState
from openpilot.starpilot.spot_monitor.preferences import SavedPreferences, read_preferences
from openpilot.starpilot.vision_cpu import LITTLE_CORES, VASM_WITH_VISION


TICK_SECONDS = 0.1
SETTINGS_REFRESH_NS = 2_000_000_000
SOURCE_TTL_NS = 500_000_000
DEVICE_TTL_NS = 1_500_000_000  # hardwared publishes at 2 Hz; allow one missed sample.
RESUME_SKEW_NS = 5_000_000
BASE_INTERVAL_NS = 1_000_000_000
FOLLOWUP_INTERVAL_NS = 300_000_000
FOLLOWUP_WINDOW_NS = 1_000_000_000
MODEL_RETRY_NS = 5_000_000_000
MAX_BUFFER_BYTES = 32 * 1024 * 1024


class CpuBudget:
  """Frozen V-ASM cadence scaling over the fresh deviceState reported cores."""

  def __init__(self):
    self.factor = 1.0
    self.last_mono_ns: int | None = None

  def update(self, usage: Iterable[int | float] | None, *, now_mono_ns: int,
             cores: tuple[int, ...] | None = None) -> float:
    try:
      values = list(usage) if usage is not None and not isinstance(usage, (str, bytes)) else []
      if cores is not None:
        values = [values[core] for core in cores if core < len(values)]
    except TypeError:
      values = []
    if (type(now_mono_ns) is not int or now_mono_ns < 0 or
        (self.last_mono_ns is not None and now_mono_ns < self.last_mono_ns) or
        not values or len(values) > 32 or
        any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100 for value in values)):
      self.factor = 4.0
      self.last_mono_ns = now_mono_ns if type(now_mono_ns) is int and now_mono_ns >= 0 else None
      return self.factor
    average = sum(values) / len(values)
    hot = sum(value >= 89.0 for value in values)
    average_target = 1.0 if average < 74.0 else 1.0 + (average - 74.0) / 8.0
    hot_target = 1.0 + max(0, hot - 4 + 1) * 0.5
    target = min(max(average_target, hot_target), 4.0)
    dt = (now_mono_ns - self.last_mono_ns) / 1e9 if self.last_mono_ns is not None else 0.0
    alpha = min(1.0 - math.exp(-0.8 * dt), 1.0)
    self.factor = min(max(target * alpha + self.factor * (1.0 - alpha), 1.0), 4.0)
    self.last_mono_ns = now_mono_ns
    return self.factor


@dataclass(frozen=True)
class Observation:
  session: str
  sequence: int
  status: str
  settings_fingerprint: str
  model_sha256: str
  observed_mono_ns: int
  observed_boot_ns: int
  camera_eof_boot_ns: int
  frame_id: int
  side: str
  display_left: bool
  display_right: bool
  display_left_confidence: float
  display_right_confidence: float
  left_frame_id: int
  right_frame_id: int
  left_eof_boot_ns: int
  right_eof_boot_ns: int
  left_source_mono_ns: int
  right_source_mono_ns: int


def copy_cabin_nv12(client: Any) -> tuple[np.ndarray, int, int] | None:
  """Copy the complete visible NV12 planes before releasing the VisionBuf."""
  buffer = client.recv(0)
  if buffer is None:
    return None
  try:
    width, height = int(client.width), int(client.height)
    stride, uv_offset = int(client.stride), int(client.uv_offset)
    eof_boot_ns, frame_id = int(client.timestamp_eof), int(client.frame_id)
    if (width < 32 or height < 32 or width > 8192 or height > 8192 or width % 2 or height % 2 or
        stride < width or stride > 16384 or stride % 2 or uv_offset < stride * height or uv_offset % stride or
        uv_offset + stride * (height // 2) > MAX_BUFFER_BYTES or eof_boot_ns <= 0 or frame_id < 0):
      return None
    raw = np.frombuffer(buffer.data, dtype=np.uint8)
    required = uv_offset + stride * (height // 2)
    if raw.size < required or raw.size > MAX_BUFFER_BYTES:
      return None
    y = raw[:stride * height].reshape((height, stride))[:, :width]
    uv = raw[uv_offset:required].reshape((height // 2, stride))[:, :width]
    return np.concatenate((y, uv), axis=0), eof_boot_ns, frame_id
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None
  finally:
    del buffer


def publish_observation(pm: Any, observation: Observation) -> None:
  """Send the historical slot-10 compatible observation; never curvature."""
  from openpilot.cereal import messaging

  valid = observation.status == "valid" and observation.sequence > 0
  event = messaging.new_message("spotMonitorState", valid=valid)
  event.logMonoTime = observation.observed_mono_ns
  wire = event.spotMonitorState.init("observation")
  wire.version = 1
  wire.producerSessionId = observation.session
  wire.sequence = observation.sequence
  wire.modelSha256 = observation.model_sha256
  wire.settingsFingerprint = observation.settings_fingerprint
  wire.observedMonoTime = observation.observed_mono_ns
  wire.observedBootTime = observation.observed_boot_ns
  wire.sourceFrameId = observation.frame_id
  wire.sourceFrameEofBootTime = observation.camera_eof_boot_ns
  wire.validUntilBootTime = observation.camera_eof_boot_ns + SOURCE_TTL_NS if valid else 0
  # Camera-left appears on display-right; opposite-side frames must not renew its expiry.
  for display, camera in (("left", "right"), ("right", "left")):
    side = getattr(wire, display)
    frame_id = getattr(observation, f"{camera}_frame_id")
    eof_ns = getattr(observation, f"{camera}_eof_boot_ns")
    source_mono_ns = getattr(observation, f"{camera}_source_mono_ns")
    warning = bool(getattr(observation, f"display_{display}")) if valid else False
    side.status = "warning" if warning else "clear" if valid and eof_ns > 0 and \
                  observation.observed_boot_ns - eof_ns <= 3_000_000_000 else "unknown"
    side.warning = warning
    side.confidence = float(getattr(observation, f"display_{display}_confidence")) if side.status != "unknown" else 0.0
    side.sourceFrameId = frame_id
    side.sourceFrameEofBootTime = eof_ns
    side.sourceObservedMonoTime = source_mono_ns
    side.validUntilBootTime = eof_ns + 3_000_000_000 if eof_ns > 0 else 0
  pm.send("spotMonitorState", event)


def cabin_client_factory(client_type: Any, stream_type: Any) -> Any:
  """Attach only to camerad's existing cabin stream; no camera startup."""
  return client_type("camerad", stream_type.VISION_STREAM_CABIN, True)


class VASMRuntime:
  def __init__(self, params: Params, messages: Any, client_factory: Callable[[], Any], emit: Callable[[Observation], None],
               *, model_path: Path | None = None, mono_clock: Callable[[], int] = time.monotonic_ns,
               pair_clock: Callable[[], tuple[int, int] | None] = clock_pair_ns,
               model_factory: Callable[[Path], VASMInference] = VASMInference):
    self.params, self.messages = params, messages
    self.client_factory, self.emit = client_factory, emit
    self.model_path = model_path
    self.mono_clock, self.pair_clock, self.model_factory = mono_clock, pair_clock, model_factory
    self.session = uuid.uuid4().hex
    self.sequence = 0
    self.saved = SavedPreferences(None, True, True)
    self.settings_read_ns = -SETTINGS_REFRESH_NS
    self.inference_cores = LITTLE_CORES
    self.model: VASMInference | None = None
    self.last_model_attempt_ns = -MODEL_RETRY_NS
    self.policy = WarningPolicy()
    self.cpu_budget = CpuBudget()
    self.client = None
    self.last_pair_offset_ns: int | None = None
    self.last_frame_id = -1
    self.last_eof_boot_ns = 0
    self.camera_after_boot_ns = 0
    self.last_processed_ns = 0
    self.last_side_mono_ns = {"left": 0, "right": 0}
    self.side_frame_id = {"left": 0, "right": 0}
    self.side_eof_boot_ns = {"left": 0, "right": 0}
    self.next_side = "left"
    self.followup_until_ns = 0
    self.status = "disabled"
    self.last_error_log_ns = -10_000_000_000

  def _reset(self, status: str, now_ns: int) -> None:
    changed = status != self.status or self.last_frame_id >= 0
    if changed and (self.last_frame_id >= 0 or self.client is not None):
      pair = self.pair_clock()
      if pair is not None:
        self.camera_after_boot_ns = max(self.camera_after_boot_ns, pair[1])
    self.policy.reset()
    self.client = None
    self.session = uuid.uuid4().hex if changed else self.session
    if changed:
      self.sequence = 0
    self.last_pair_offset_ns = None
    self.last_frame_id = -1
    self.last_eof_boot_ns = 0
    self.last_processed_ns = 0
    self.last_side_mono_ns = {"left": 0, "right": 0}
    self.side_frame_id = {"left": 0, "right": 0}
    self.side_eof_boot_ns = {"left": 0, "right": 0}
    self.followup_until_ns = 0
    self.status = status
    if changed:
      self.emit(self._observation(status, now_ns, 0, 0, 0, ""))

  def _observation(self, status: str, now_ns: int, boot_ns: int, eof_boot_ns: int, frame_id: int, side: str,
                   state: WarningState | None = None) -> Observation:
    return Observation(self.session, self.sequence, status, self.saved.fingerprint,
                       self.model.loaded_sha256 if self.model is not None and
                       self.model.loaded_sha256 is not None else "", now_ns, boot_ns, eof_boot_ns, frame_id, side,
                       bool(state.display_left) if state else False, bool(state.display_right) if state else False,
                       float(state.display_left_confidence) if state else 0.0,
                       float(state.display_right_confidence) if state else 0.0,
                       self.side_frame_id["left"], self.side_frame_id["right"],
                       self.side_eof_boot_ns["left"], self.side_eof_boot_ns["right"],
                       self.last_side_mono_ns["left"], self.last_side_mono_ns["right"])

  def _fresh_authority(self, now_ns: int) -> bool:
    from opendbc.car.structs import car

    sm = self.messages
    for name in ("deviceState", "carState"):
      ttl_ns = DEVICE_TTL_NS if name == "deviceState" else SOURCE_TTL_NS
      stamp = int(sm.logMonoTime[name])
      receipt = int(float(sm.recv_time[name]) * 1e9)
      if (not sm.seen[name] or not sm.alive[name] or not sm.valid[name] or
          not 0 < stamp <= now_ns or not 0 < receipt <= now_ns or
          now_ns - stamp > ttl_ns or now_ns - receipt > ttl_ns):
        return False
    return (bool(sm["deviceState"].started) and bool(sm["carState"].canValid) and
            sm["carState"].gearShifter == car.CarState.GearShifter.drive)

  def _settings_current(self) -> bool:
    current = read_preferences(self.params)
    return (current.readable and current.valid and current.raw is not None and
            current.raw == self.saved.raw and current.preferences.enabled)

  def _refresh_settings(self, now_ns: int) -> None:
    if now_ns - self.settings_read_ns < SETTINGS_REFRESH_NS:
      return
    self.settings_read_ns = now_ns
    self.inference_cores = VASM_WITH_VISION if (feature_requested(self.params, "vision") or feature_requested(self.params, "vision_stop")) else LITTLE_CORES
    try:
      set_core_affinity(list(self.inference_cores))
    except OSError:
      pass
    saved = read_preferences(self.params)
    if saved != self.saved:
      self.saved = saved
      self.model = None
      self.last_model_attempt_ns = -MODEL_RETRY_NS
      self._reset("disabled", now_ns)

  def tick(self) -> Observation | None:
    self.messages.update(0)
    now_ns = self.mono_clock()
    self._refresh_settings(now_ns)
    active = (self.saved.readable and self.saved.valid and
              self.saved.preferences.enabled and self.saved.preferences.annotation is not None and self.model_path is not None)
    if not active:
      self._reset("disabled", now_ns)
      return None
    try:
      if not self._fresh_authority(now_ns):
        self._reset("unavailable", now_ns)
        return None
      if self.model is None:
        if now_ns - self.last_model_attempt_ns < MODEL_RETRY_NS:
          return None
        self.last_model_attempt_ns = now_ns
        self.model = self.model_factory(self.model_path)
        if not self.model.load():
          self.model = None
          self._reset("unavailable", now_ns)
          return None
        self.messages.update(0)
        if not self._fresh_authority(self.mono_clock()) or not self._settings_current():
          self._reset("unavailable", self.mono_clock())
          return None
      if self.client is None:
        self.client = self.client_factory()
      if not self.client.is_connected():
        self._reset("unavailable", now_ns)
        self.client = self.client_factory()
        self.client.connect(False)
        return None
      base_interval_ns = FOLLOWUP_INTERVAL_NS if now_ns < self.followup_until_ns else BASE_INTERVAL_NS
      cpu_usage = getattr(self.messages["deviceState"], "cpuUsagePercent", None)
      interval_ns = int(base_interval_ns * self.cpu_budget.update(
        cpu_usage, now_mono_ns=now_ns, cores=self.inference_cores))
      if self.last_processed_ns and now_ns - self.last_processed_ns < interval_ns:
        return None
      frame = copy_cabin_nv12(self.client)
      if frame is None:
        return None
      image, eof_boot_ns, frame_id = frame
      pair = self.pair_clock()
      if pair is None:
        self._reset("stale", now_ns)
        return None
      offset_ns = pair[1] - pair[0]
      if (self.last_pair_offset_ns is not None and abs(offset_ns - self.last_pair_offset_ns) > RESUME_SKEW_NS or
          eof_boot_ns <= max(self.last_eof_boot_ns, self.camera_after_boot_ns) or frame_id <= self.last_frame_id or
          not 0 <= pair[1] - eof_boot_ns <= SOURCE_TTL_NS):
        self._reset("stale", now_ns)
        return None
      self.last_pair_offset_ns = offset_ns
      self.last_eof_boot_ns, self.last_frame_id = eof_boot_ns, frame_id
      sides = self.saved.preferences.annotation.configured_sides
      side = self.next_side if self.next_side in sides else sides[0]
      source_mono_ns = eof_boot_ns - offset_ns
      previous_side_ns = self.last_side_mono_ns[side]
      dt = (source_mono_ns - previous_side_ns) / 1e9 if previous_side_ns else interval_ns / 1e9
      score = self.model.infer_nv12(image, width=image.shape[1], height=image.shape[0] * 2 // 3,
                                    annotation=self.saved.preferences.annotation, side=side)
      if score is None:
        self.model = None
        self.last_model_attempt_ns = self.mono_clock()
        self._reset("stale", now_ns)
        return None
      self.messages.update(0)
      saved_current = self._settings_current()
      after = self.pair_clock()
      if (after is None or abs((after[1] - after[0]) - offset_ns) > RESUME_SKEW_NS or
          not 0 <= after[1] - eof_boot_ns <= SOURCE_TTL_NS):
        self._reset("stale", now_ns)
        return None
      if not saved_current or not self._fresh_authority(after[0]):
        self._reset("unavailable", after[0])
        return None
      if self.policy.threshold != self.saved.preferences.confidence or \
         self.policy.smooth_seconds != self.saved.preferences.smooth_seconds:
        self.policy = WarningPolicy(threshold=self.saved.preferences.confidence,
                                    smooth_seconds=self.saved.preferences.smooth_seconds)
      self.policy.update(side, [1.0 - score, score, 0.0], now=source_mono_ns / 1e9, dt=dt)
      self.last_processed_ns = after[0]
      self.last_side_mono_ns[side] = source_mono_ns
      self.side_frame_id[side], self.side_eof_boot_ns[side] = frame_id, eof_boot_ns
      self.next_side = sides[(sides.index(side) + 1) % len(sides)]
      state = self.policy.state(source_mono_ns / 1e9)
      if state.display_left or state.display_right:
        self.followup_until_ns = after[0] + FOLLOWUP_WINDOW_NS
      self.status = "valid"
      self.sequence += 1
      observation = self._observation("valid", after[0], after[1], eof_boot_ns, frame_id, side, state)
      self.emit(observation)
      return observation
    except Exception as error:
      if now_ns - self.last_error_log_ns >= 10_000_000_000:
        cloudlog.error("V-ASM observation unavailable: %s", error)
        self.last_error_log_ns = now_ns
      self._reset("unavailable", now_ns)
      return None

  def close(self) -> None:
    self._reset("disabled", self.mono_clock())
    for socket in getattr(self.messages, "sock", {}).values():
      close = getattr(socket, "close", None)
      if close is not None:
        close()


def main() -> None:
  os.nice(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0)))
  from openpilot.cereal import messaging
  from openpilot.cereal.visionipc import VisionStreamType
  from msgq.visionipc import VisionIpcClient

  stop = threading.Event()
  previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
  for number in previous:
    signal.signal(number, lambda _signal, _frame: stop.set())
  path = os.getenv("STARPILOT_VASM_MODEL_PATH", str(DEFAULT_MODEL_PATH))
  model_path = Path(path) if path and Path(path).is_absolute() else None
  messages = messaging.SubMaster(["deviceState", "carState"])
  pm = messaging.PubMaster(["spotMonitorState"])
  runtime = VASMRuntime(Params(), messages,
                        lambda: cabin_client_factory(VisionIpcClient, VisionStreamType),
                        lambda observation: publish_observation(pm, observation), model_path=model_path)
  try:
    while not stop.is_set():
      started = time.monotonic()
      runtime.tick()
      stop.wait(max(0.0, TICK_SECONDS - (time.monotonic() - started)))
  finally:
    runtime.close()
    for number, handler in previous.items():
      signal.signal(number, handler)


if __name__ == "__main__":
  main()
