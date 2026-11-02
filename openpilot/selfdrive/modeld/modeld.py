#!/usr/bin/env python3
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, cast
import base64
import ctypes
import gc
from functools import cached_property
import os
os.environ['GMMU'] = '0' # for chestnut fast loading, noop for qcom
os.environ.setdefault('AM_POWER_LIMIT', '100')
from openpilot.starpilot.models.runtime_bridge.runtime import prepare as prepare_model_runtime, selected_legacy
prepare_model_runtime('driving')
from tinygrad.device import Buffer, Device
from tinygrad.dtype import DType, dtypes
from tinygrad.engine.realize import lower_and_compile
from tinygrad.tensor import Tensor
from tinygrad.helpers import all_int, round_up
from tinygrad.uop.ops import UOp
if TYPE_CHECKING:
  from openpilot.starpilot.models.runtime_bridge.legacy_types import LegacyBuffer, LegacyUOpFactory
import math
import pickle
import threading
import time
import numpy as np
import openpilot.cereal.messaging as messaging
from openpilot.cereal import log
from opendbc.car.structs import car
from openpilot.cereal.messaging import PubMaster, SubMaster
from openpilot.cereal.services import SERVICE_LIST
from openpilot.cereal.visionipc import VisionStreamType
from msgq.visionipc import VisionIpcClient, VisionBuf
from opendbc.car.car_helpers import get_demo_car_params
from openpilot.common.swaglog import cloudlog
from openpilot.common.params import Params
from openpilot.common.hardware.usb import cable_connected
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import config_realtime_process, set_core_affinity, DT_MDL
from openpilot.common.transformations.camera import DEVICE_CAMERAS
from openpilot.system.camerad.cameras.nv12_info import get_nv12_info
from openpilot.common.transformations.model import get_warp_matrix
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
from openpilot.starpilot.lateral.lane_change_preferences import LiveLaneChangePolicy
from openpilot.starpilot.lateral.auto_lane_change import ClockEpochGuard, auto_evidence, paired_clocks_ns, session_policy, steering_capable
from openpilot.starpilot.lateral.lane_change_status_wire import (
  Direction as StatusDirection, LaneChangeStatus, Phase as StatusPhase, encode_optional as encode_lane_status,
)
from openpilot.starpilot.models.receipt import ModelReceiptOwner
from openpilot.starpilot.models.status import ModelVariant
from openpilot.starpilot.models.startup import report_load_timeout, wait_for_chestnut_power
from openpilot.starpilot.models.recovery import STARTUP_FALLBACK_EXIT
from openpilot.starpilot.navigation.intent import TurnIntent, matching_turn_signal
from openpilot.starpilot.models.catalog import BUNDLED_CURRENT, BY_ID, DEFAULT_SMALL, DEFAULT_SMALL_SHA256
from openpilot.starpilot.models.runner import CatalogModelState, load_verified_model
from openpilot.starpilot.models import jetlink_adapter
import uuid
from openpilot.selfdrive.controls.lib.drive_helpers import get_accel_from_plan, should_stop, smooth_value, get_curvature_from_plan
from openpilot.selfdrive.modeld.parse_model_outputs import Parser
from openpilot.selfdrive.modeld.fill_model_msg import fill_model_msg, fill_driving_model_data, fill_pose_msg, PublishState
from openpilot.selfdrive.modeld.constants import LAT_SMOOTH_SECONDS, ModelConstants, Plan
from openpilot.selfdrive.modeld.helpers import MODELS_DIR, chestnut_present, modeld_pkl_path, load_oob, wait_for_chestnut


SEND_RAW_PRED = os.getenv('SEND_RAW_PRED')

LONG_SMOOTH_SECONDS = 0.3
MIN_LAT_CONTROL_SPEED = 0.3
BIG_MODEL_TIMEOUT = 120


def get_action_from_model(model_output: dict[str, np.ndarray], prev_action: log.ModelDataV2.Action,
                          lat_action_t: float, long_action_t: float, v_ego: float) -> log.ModelDataV2.Action:
  if 'action' not in model_output:
    plan = model_output['plan'][0]
    desired_accel = get_accel_from_plan(plan[:,Plan.VELOCITY][:,0],
                                        plan[:,Plan.ACCELERATION][:,0],
                                        ModelConstants.T_IDXS,
                                        action_t=long_action_t)
    desired_curvature = get_curvature_from_plan(plan[:,Plan.T_FROM_CURRENT_EULER][:,2],
                                                plan[:,Plan.ORIENTATION_RATE][:,2],
                                                ModelConstants.T_IDXS,
                                                v_ego,
                                                lat_action_t)
  else:
    desired_accel = model_output['action'][0,1]
    desired_curvature = model_output['action'][0,0] / (max(2.5, v_ego))**2
  stop = should_stop(v_ego, desired_accel)
  desired_accel = smooth_value(desired_accel, prev_action.desiredAcceleration, LONG_SMOOTH_SECONDS)
  if v_ego > MIN_LAT_CONTROL_SPEED:
    desired_curvature = smooth_value(desired_curvature, prev_action.desiredCurvature, LAT_SMOOTH_SECONDS)
  else:
    desired_curvature = prev_action.desiredCurvature

  return log.ModelDataV2.Action(desiredCurvature=float(desired_curvature),
                                desiredAcceleration=float(desired_accel),
                                shouldStop=bool(stop))


def drop_chestnut() -> None:
  if "AMD" in Device._opened_devices:
    for d in Device._opened_devices:
      Device[d].pending.pop(Device["AMD"], None)


class ChestnutGpuState:
  # GPU metrics require modeld's GPU context
  def __init__(self, pm: PubMaster, big: bool):
    self.pm = pm
    self.big = big
    self.valid = True
    self.sends = 0
    self.metrics = {}

  @cached_property
  def power_limit(self) -> int:
    smu = Device["AMD"].iface.dev_impl.smu
    return smu._send_msg(smu.smu_mod.PPSMC_MSG_GetPptLimit, 0, read_back_arg=True, timeout=100)

  def send(self) -> None:
    msg = messaging.new_message('chestnutGpuState')
    state = msg.chestnutGpuState
    self.sends += 1
    if self.big and "AMD" in Device._opened_devices and self.sends % 100 == 1:
      try:
        smu = Device["AMD"].iface.dev_impl.smu
        metrics_t = smu.smu_mod.SmuMetricsExternal_t
        smu._send_msg(smu.smu_mod.PPSMC_MSG_TransferTableSmu2Dram, smu.smu_mod.TABLE_SMU_METRICS, timeout=100)
        metrics_buf = bytearray(smu.adev.vram.view(smu.driver_table_paddr, ctypes.sizeof(metrics_t))[:])
        metrics = metrics_t.from_buffer(metrics_buf).SmuMetrics
        self.metrics = {'tempC': metrics.AvgTemperature[smu.smu_mod.TEMP_HOTSPOT],
                        'memoryTempC': metrics.AvgTemperature[smu.smu_mod.TEMP_MEM],
                        'powerDrawW': metrics.AverageSocketPower,
                        'powerLimitW': self.power_limit,
                        'gpuUsagePercent': metrics.AverageGfxActivity,
                        'gpuClockMhz': metrics.AverageGfxclkFrequencyPostDs,
                        'fanSpeedRpm': metrics.AvgFanRpm}
        self.valid = True
      except Exception:
        if self.valid:
          cloudlog.exception("chestnut state read failed")
        self.valid = False
        self.metrics.clear()
    if self.big:
      for k, v in self.metrics.items():
        setattr(state, k, v)

    msg.valid = not self.big or (self.valid and bool(self.metrics))
    self.pm.send('chestnutGpuState', msg)


class FrameMeta:
  frame_id: int = 0
  timestamp_sof: int = 0
  timestamp_eof: int = 0

  def __init__(self, vipc=None):
    if vipc is not None:
      self.frame_id, self.timestamp_sof, self.timestamp_eof = vipc.frame_id, vipc.timestamp_sof, vipc.timestamp_eof


def input_view(buffer: Buffer, shape: tuple[int, ...], dtype: DType, offset: int) -> Tensor:
  from openpilot.starpilot.models.runtime_bridge.runtime import selected_legacy
  if selected_legacy():
    view = cast("LegacyBuffer", buffer).view(math.prod(shape), dtype, offset).ensure_allocated()
    return Tensor(cast("LegacyUOpFactory", UOp).from_buffer(view)).reshape(shape)
  view = buffer.view(math.prod(shape) * dtype.itemsize, offset).ensure_allocated()
  return Tensor(UOp.from_buffer(view, dtype)).reshape(shape)


class ModelState:
  lat_delay: float
  frame_drop_ratio: float
  prev_desire: np.ndarray  # for tracking the rising edge of the pulse
  get_action_from_model = staticmethod(get_action_from_model)

  def __init__(self, cam_w: int, cam_h: int, chestnut: bool):
    if not chestnut:
      raise ValueError("Small models require the verified catalog loader")
    jits = load_oob(modeld_pkl_path(chestnut), chestnut)
    self.model_device = jits['input_specs']['new_img'][2]
    self.input_shapes = {name: (shape, np.dtype(dtype)) for name, (shape, dtype, _) in jits['input_specs'].items()}
    self.state_pairs = {name: f'next_{name}' for name in self.input_shapes if f'next_{name}' in jits['metadata']['output_shapes']}
    self.vision_input_names = ('img', 'big_img')
    self.output_slices = pickle.loads(base64.b64decode(jits['metadata']['metadata']['output_slices']))

    self.prev_desire = np.zeros(ModelConstants.DESIRE_LEN, dtype=np.float32)
    self.chestnut = chestnut
    self.model_id = BUNDLED_CURRENT

    stride, y_height, uv_height, _ = get_nv12_info(cam_w, cam_h)
    self.frame_copy_size = stride * (y_height + uv_height)
    self.pack_inputs()
    with open(MODELS_DIR / f'{"big_" if chestnut else ""}driving_warp_{cam_w}x{cam_h}_tinygrad.pkl', 'rb') as f:
      self.run_warp = pickle.load(f)['run']
    self.run_warp.captured._linear = lower_and_compile(self.run_warp.captured._linear)
    self.run_model = jits['run']
    self.run_model.captured._linear = lower_and_compile(self.run_model.captured._linear)
    self.outputs = {name: Tensor(np.zeros(shape, dtype=dtype), device=device).realize() for name, (shape, dtype, device) in jits['output_specs'].items()}
    for name, next_name in self.state_pairs.items():
      state = self.input_queues[name]
      assert all_int(state.shape)
      self.outputs[next_name] = input_view(state._buffer(), state.shape, state.dtype, 0)
    self.parser = Parser()

  def pack_inputs(self) -> None:
    # Pack host inputs into one upload to reduce USB transfer overhead for the eGPU.
    self.input_queues = {name: Tensor(np.zeros(shape, dtype=dtype), device=self.model_device).realize()
                         for name, (shape, dtype) in self.input_shapes.items() if name in self.state_pairs}
    shapes = {'tfm': (2, 3, 3)} | {name: shape for name, (shape, _) in self.input_shapes.items()
                                   if name not in self.state_pairs and name != 'new_img'}
    npy_size = sum(round_up(math.prod(shape) * 4, 128) for shape in shapes.values())
    self.packed_input = np.zeros(npy_size + 2 * self.frame_copy_size, dtype=np.uint8)
    self.input_host = Tensor(self.packed_input, device='NPY')._buffer()
    self.input_device = Tensor(self.packed_input, device=self.model_device)._buffer()
    self.npy = {}
    offset = 0
    for name, shape in shapes.items():
      self.npy[name] = np.ndarray(shape, dtype=np.float32, buffer=self.packed_input, offset=offset)
      self.input_queues[name] = input_view(self.input_device, shape, dtypes.float32, offset)
      offset += round_up(self.npy[name].nbytes, 128)
    self.frames = self.packed_input[npy_size:].reshape(2, self.frame_copy_size)
    self.warp_inputs = {'input_frame': input_view(self.input_device, self.frames.shape, dtypes.uint8, npy_size), 'M_inv': self.input_queues.pop('tfm')}

  def slice_outputs(self, model_outputs: np.ndarray, output_slices: dict[str, slice]) -> dict[str, np.ndarray]:
    return {k: model_outputs[np.newaxis, v] for k,v in output_slices.items()}

  def run(self, bufs: Mapping[str, VisionBuf | np.ndarray], transforms: dict[str, np.ndarray],
          inputs: dict[str, np.ndarray], after_enqueue: Callable[[], None] | None = None) -> dict[str, np.ndarray]:
    for i, key in enumerate(self.vision_input_names):
      np.copyto(self.frames[i], np.frombuffer(bufs[key].data, dtype=np.uint8, count=self.frame_copy_size))
      self.npy['tfm'][i] = transforms[key]

    # Model decides when action is completed, so desire input is just a pulse triggered on rising edge
    inputs['desire_pulse'][0] = 0
    self.npy['desire'][:] = np.where(inputs['desire_pulse'] - self.prev_desire > .99, inputs['desire_pulse'], 0)
    self.prev_desire[:] = inputs['desire_pulse']
    self.npy['traffic_convention'][:] = inputs['traffic_convention']
    self.npy['action_t'][:] = inputs['action_t']

    self.input_device.copy_from(self.input_host)
    self.input_queues['new_img'] = self.run_warp(**self.warp_inputs)
    self.run_model(output_buffers=self.outputs, **self.input_queues)
    if (self.chestnut and not selected_legacy() and
        (err := Device[cast(str, self.outputs['outputs'].device)].error_state.host.view(fmt='q')[0])):
      raise RuntimeError(f"chestnut failed with {err}")
    if after_enqueue is not None:
      after_enqueue()
    model_output = self.outputs['outputs'].numpy()[0]
    if self.chestnut and not np.all(np.isfinite(model_output)):
      raise RuntimeError("model output not finite")
    outputs_dict = self.parser.parse_outputs(self.slice_outputs(model_output, self.output_slices))

    if SEND_RAW_PRED:
      outputs_dict['raw_pred'] = model_output.copy()
    return outputs_dict

  def warmup(self) -> None:
    dummy_frames = {k: np.zeros(self.frame_copy_size, dtype=np.uint8) for k in self.vision_input_names}
    eye = np.eye(3, dtype=np.float32)
    dims = {'desire_pulse': ModelConstants.DESIRE_LEN, 'traffic_convention': 2, 'action_t': 2}
    self.run(dummy_frames, dict.fromkeys(self.vision_input_names, eye), {k: np.zeros(v, dtype=np.float32) for k, v in dims.items()})
    self.packed_input[:] = 0
    for key in self.state_pairs:
      self.input_queues[key].assign(0).realize()
    self.prev_desire[:] = 0


def refresh_lane_change_policy(preferences, desire_helper, carstate, now_ns, vehicle_capable):
  policy = session_policy(preferences.sample(now_ns,
    signaling=bool(carstate.leftBlinker or carstate.rightBlinker),
    maneuver_active=desire_helper.lane_change_state != log.LaneChangeState.off), vehicle_capable)
  desire_helper.policy = policy
  return policy


def release_compile_pool() -> None:
  from tinygrad.engine.worker import terminate_worker_pool
  from tinygrad.helpers import PARALLEL
  terminate_worker_pool()
  PARALLEL.value = 0


def main(demo=False):
  cloudlog.warning("modeld init")

  receipt_owner = ModelReceiptOwner(report=lambda reason: cloudlog.warning("modeld load identity unavailable: %s", reason))
  receipt_owner.start()

  from openpilot.starpilot.models.manager import resolve_runtime, requested_runtime_id
  # a Jetson, PC or phone on the port looks like a chestnut to cable_connected(): the cable counts with Jetlink off only
  chestnut_available = chestnut_present() or (cable_connected() and not jetlink_adapter.link_on())
  recovery_small_only = os.getenv("STARPILOT_MODELD_RECOVERY_SMALL_ONLY") == "1"
  selection = resolve_runtime(chestnut_available=chestnut_available and not recovery_small_only, randomize=not recovery_small_only)
  requested_model_id = requested_runtime_id(chestnut_available)
  CHESTNUT = selection.allow_big and selection.big_path is not None
  if CHESTNUT:
    from tinygrad.runtime.support.am import startup_trace
    startup_trace.ENABLED = True
    startup_trace.REPORT = lambda packet: cloudlog.event("chestnut.startup", **packet)
    from tinygrad.runtime.ops_amd import AMDDevice
    AMDDevice.wait_timeout_ms = 3000
  params = Params()
  params.put_bool("ChestnutLoading", CHESTNUT)
  params.remove("ChestnutActive")

  gc.disable()

  # visionipc clients
  while True:
    available_streams = VisionIpcClient.available_streams("camerad", block=False)
    if available_streams:
      use_extra_client = VisionStreamType.VISION_STREAM_WIDE_ROAD in available_streams and VisionStreamType.VISION_STREAM_NARROW_ROAD in available_streams
      main_wide_camera = VisionStreamType.VISION_STREAM_NARROW_ROAD not in available_streams
      break
    time.sleep(.1)

  vipc_client_main_stream = VisionStreamType.VISION_STREAM_WIDE_ROAD if main_wide_camera else VisionStreamType.VISION_STREAM_NARROW_ROAD
  vipc_client_main = VisionIpcClient("camerad", vipc_client_main_stream, True)
  vipc_client_extra = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_WIDE_ROAD, False)
  cloudlog.warning(f"vision stream set up, main_wide_camera: {main_wide_camera}, use_extra_client: {use_extra_client}")

  while not vipc_client_main.connect(False):
    time.sleep(0.1)
  while use_extra_client and not vipc_client_extra.connect(False):
    time.sleep(0.1)

  cam_w, cam_h = vipc_client_main.width, vipc_client_main.height
  assert cam_w is not None and cam_h is not None
  cloudlog.warning(f"connected main cam with buffer size: {vipc_client_main.buffer_len} ({cam_w} x {cam_h})")
  if use_extra_client:
    cloudlog.warning(f"connected extra cam with buffer size: {vipc_client_extra.buffer_len} ({vipc_client_extra.width} x {vipc_client_extra.height})")

  if demo:
    CP = get_demo_car_params()
  else:
    CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  cloudlog.info("modeld got CarParams: %s", CP.brand)
  from openpilot.starpilot.car.subaru.smoothing import read_frozen, seconds as subaru_smooth_seconds, filter_action
  subaru_smoothing = read_frozen(CP, params)

  st = time.monotonic()
  cloudlog.warning("loading model")
  model = None
  big_prepared = None

  # with Jetlink on, the small model's warp output moves where the link sends it from, before warmup links it
  coherent_warp = jetlink_adapter.coherent_warp()

  def load_small():
    if selection.small_path is not None:
      try:
        selected, prepared = load_verified_model(cam_w, cam_h,
                                                 selection.small_path, selection.small_version, False,
                                                 selection.small_id, selection.small_sha256, coherent_warp=coherent_warp)
        return selected, prepared, False
      except Exception:
        cloudlog.exception("selected small model load failed, using shipped RDFv4")
    from openpilot.starpilot.models.manager import shipped_default
    path = shipped_default()
    if path is None:
      raise RuntimeError("Shipped RDFv4 is missing or corrupt")
    fallback, prepared = load_verified_model(cam_w, cam_h,
                                             path, BY_ID[DEFAULT_SMALL].version, False,
                                             DEFAULT_SMALL, DEFAULT_SMALL_SHA256, coherent_warp=coherent_warp)
    return fallback, prepared, selection.small_id != DEFAULT_SMALL

  # Preload a custom fallback before the GPU worker takes the catalog lock.
  # A timed-out GPU worker must never hold up access to the small runner.
  small_model = small_prepared = None
  selected_small_failed = False
  if selection.big_path is not None and selection.small_path is not None:
    small_model, small_prepared, selected_small_failed = load_small()
  if CHESTNUT:
    big_path = selection.big_path or modeld_pkl_path(True)
    big_artifact_identity = receipt_owner.capture(big_path)
    big_model = None
    def load_big():
      nonlocal big_model, big_prepared
      try:
        wait_for_chestnut()
        if not demo:
          wait_for_chestnut_power(CP, timeout=min(30., BIG_MODEL_TIMEOUT / 2))
        set_core_affinity([7])
        if selection.big_path is not None:
          m, prepared = load_verified_model(cam_w, cam_h,
                                            big_path, selection.big_version, True, selection.big_id, selection.big_sha256)
        else:
          m = ModelState(cam_w, cam_h, True)
          m.warmup()
          prepared = receipt_owner.prepare(big_path, big_artifact_identity)
        big_prepared = prepared
        big_model = m
      except Exception:
        cloudlog.exception("big model load failed")
    loader = threading.Thread(target=load_big, daemon=True)
    loader.start()
    loader.join(BIG_MODEL_TIMEOUT)
    if loader.is_alive():
      report_load_timeout(loader, BIG_MODEL_TIMEOUT)
    model = big_model
    params.put_bool("ChestnutActive", model is not None)
    if model is None:
      # A timed-out loader can still mutate tinygrad's shared device state.
      # The manager must confirm this process has exited before starting Small.
      cloudlog.error("Chestnut startup failed; requesting isolated Small restart")
      # Avoid Python/native finalizers retaining this failed GPU context.
      os._exit(STARTUP_FALLBACK_EXIT)

  if small_model is None:
    small_model, small_prepared, selected_small_failed = load_small()
  initial_chestnut_fallback = CHESTNUT and model is None
  if model is None:
    model = small_model
  receipt_owner.loaded(big_prepared if model.chestnut else small_prepared,
                       ModelVariant.CHESTNUT if model.chestnut else ModelVariant.SMALL,
                       "chestnut-run-stalled" if recovery_small_only else
                       "chestnut-load-failed" if initial_chestnut_fallback else
                       "selected-load-failed" if (selected_small_failed and not model.chestnut) or requested_model_id != model.model_id else None,
                       model_id=model.model_id)
  HOOKS = jetlink_adapter.hooks()
  jetlink_model = None
  # before going realtime: the GPU thread it starts would inherit SCHED_FIFO 54 on core 7
  if HOOKS.prepare(CHESTNUT or recovery_small_only):
    # the joining model drives as small_model until the big model is ready
    model = HOOKS.attach(model, small_model, vipc_client_main.width, vipc_client_main.height)
    jetlink_model = model if model is not small_model else None
    if jetlink_model is not None:
      from openpilot.starpilot.models.jetlink_status import start_latency_writer
      start_latency_writer(jetlink_model)
  params.put_bool("ChestnutLoading", False)
  cloudlog.warning(f"models loaded in {time.monotonic() - st:.1f}s, modeld starting")
  release_compile_pool()
  # gc stays off for the realtime loop, so drop the load's cyclic garbage once now
  gc.collect()

  config_realtime_process(7, 54)

  # messaging
  pub_socks = ["modelIdentity", "modelV2", "drivingModelData", "cameraOdometry", "laneChangeAssistWire"] + (["chestnutGpuState"] if CHESTNUT else [])
  pm = PubMaster(pub_socks)
  sm = SubMaster(["deviceState", "carState", "narrowRoadCameraState", "extrinsicsCalibration", "driverMonitoringState",
                  "carControl", "controlsState", "lateralDelay", "starpilotNavigation", "longitudinalPlan"])

  publish_state = PublishState()
  params = Params()
  chestnut_state = ChestnutGpuState(pm, model.chestnut) if CHESTNUT else None

  # setup filter to track dropped frames
  frame_dropped_filter = FirstOrderFilter(0., 10., 1. / ModelConstants.MODEL_RUN_FREQ)
  frame_id = 0
  last_vipc_frame_id = 0
  run_count = 0

  model_transform_main = np.zeros((3, 3), dtype=np.float32)
  model_transform_extra = np.zeros((3, 3), dtype=np.float32)
  extrinsics_calibration_seen = False
  buf_main, buf_extra = None, None
  meta_main = FrameMeta()
  meta_extra = FrameMeta()

  # TODO this needs more thought, use .2s extra for now to estimate other delays
  # TODO Move smooth seconds to action function
  long_delay = CP.longitudinalActuatorDelay + LONG_SMOOTH_SECONDS
  prev_action = log.ModelDataV2.Action()

  lane_preferences = LiveLaneChangePolicy(params, now_ns=time.monotonic_ns())
  saved_lane_policy = lane_preferences.applied
  auto_vehicle_capable = steering_capable(CP)
  runtime_lane_policy = session_policy(saved_lane_policy, auto_vehicle_capable)
  initial_mono_ns, initial_boot_ns, initial_skew_ns = paired_clocks_ns()
  lane_clock_guard = ClockEpochGuard(initial_mono_ns, initial_boot_ns, initial_skew_ns)
  DH = DesireHelper(runtime_lane_policy)
  navigation_turn = TurnIntent(params, CP)
  lane_status_session = uuid.uuid4().hex
  lane_status_sequence = 0

  while True:
    # Keep receiving frames until we are at least 1 frame ahead of previous extra frame
    while meta_main.timestamp_sof < meta_extra.timestamp_sof + 25000000:
      buf_main = vipc_client_main.recv()
      meta_main = FrameMeta(vipc_client_main)
      if buf_main is None:
        break

    if buf_main is None:
      cloudlog.debug("vipc_client_main no frame")
      continue

    if use_extra_client:
      # Keep receiving extra frames until frame id matches main camera
      while True:
        buf_extra = vipc_client_extra.recv()
        meta_extra = FrameMeta(vipc_client_extra)
        if buf_extra is None or meta_main.timestamp_sof < meta_extra.timestamp_sof + 25000000:
          break

      if buf_extra is None:
        cloudlog.debug("vipc_client_extra no frame")
        continue

      if abs(meta_main.timestamp_sof - meta_extra.timestamp_sof) > 10000000:
        cloudlog.error(f"frames out of sync! main: {meta_main.frame_id} ({meta_main.timestamp_sof / 1e9:.5f}),\
                         extra: {meta_extra.frame_id} ({meta_extra.timestamp_sof / 1e9:.5f})")

    else:
      # Use single camera
      buf_extra = buf_main
      meta_extra = meta_main

    sm.update(0)
    turn_supported = bool(model.npy.get('desire') is not None and model.npy['desire'].shape[-1] == 8)
    desire = navigation_turn.select(sm, time.monotonic_ns(), DH.desire,
                                     supported=turn_supported, model_stop=bool(prev_action.shouldStop))
    is_rhd = sm["driverMonitoringState"].isRHD
    frame_id = sm["narrowRoadCameraState"].frameId
    v_ego = max(sm["carState"].vEgo, 0.)
    subaru_tau = subaru_smooth_seconds(v_ego, subaru_smoothing)
    lat_delay = sm["lateralDelay"].lateralDelay + LAT_SMOOTH_SECONDS + subaru_tau
    if sm.updated["extrinsicsCalibration"] and sm.seen['narrowRoadCameraState'] and sm.seen['deviceState']:
      device_from_calib_euler = np.array(sm["extrinsicsCalibration"].rpyCalib, dtype=np.float32)
      dc = DEVICE_CAMERAS[(str(sm['deviceState'].deviceType), str(sm['narrowRoadCameraState'].sensor))]
      main_intrinsics = dc.wide_road.intrinsics if main_wide_camera else dc.narrow_road.intrinsics
      model_transform_main = get_warp_matrix(device_from_calib_euler, main_intrinsics, False).astype(np.float32)
      has_wide_camera = use_extra_client or main_wide_camera
      extra_intrinsics = dc.wide_road.intrinsics if has_wide_camera else dc.narrow_road.intrinsics
      model_transform_extra = get_warp_matrix(device_from_calib_euler, extra_intrinsics, True).astype(np.float32)
      extrinsics_calibration_seen = True

    traffic_convention = np.zeros(2)
    traffic_convention[int(is_rhd)] = 1

    vec_desire = np.zeros(ModelConstants.DESIRE_LEN, dtype=np.float32)
    if desire >= 0 and desire < ModelConstants.DESIRE_LEN:
      vec_desire[desire] = 1

    # tracked dropped frames
    vipc_dropped_frames = max(0, meta_main.frame_id - last_vipc_frame_id - 1)
    frames_dropped = frame_dropped_filter.update(min(vipc_dropped_frames, 10))
    if run_count < 10: # let frame drops warm up
      frame_dropped_filter.x = 0.
      frames_dropped = 0.
    run_count = run_count + 1

    frame_drop_ratio = frames_dropped / (1 + frames_dropped)

    bufs = {name: buf_extra if 'big' in name else buf_main for name in model.vision_input_names}
    transforms = {name: model_transform_extra if 'big' in name else model_transform_main for name in model.vision_input_names}
    frame_delay = DT_MDL # compensate for time passed since the frame was captured: current_time - timestamp_eof is 50ms on average
    action_delay = DT_MDL / 2 # middle of the interval between model output (current state) and next frame (expected state)
    lat_action_t = lat_delay + frame_delay + action_delay
    long_action_t = long_delay + frame_delay + action_delay
    inputs: dict[str, np.ndarray] = {
      'desire_pulse': vec_desire,
      'traffic_convention': traffic_convention,
      'action_t': np.array([lat_action_t, long_action_t], dtype=np.float32),
    }

    if not sm['carControl'].latActive:
      prev_action.desiredCurvature = sm['controlsState'].curvature

    if isinstance(model, CatalogModelState) or model is jetlink_model:
      inputs['prev_action'] = np.array([prev_action.desiredCurvature * max(1.0, v_ego) ** 2,
                                        prev_action.desiredAcceleration], dtype=np.float32)
      inputs['lateral_control_params'] = np.array([v_ego, lat_delay], dtype=np.float32)

    model.lat_delay = lat_delay
    model.frame_drop_ratio = frame_drop_ratio
    mt1 = time.perf_counter()
    try:
      send_chestnut = (chestnut_state is not None and
                       run_count % round(ModelConstants.MODEL_RUN_FREQ / SERVICE_LIST['chestnutGpuState'].frequency) == 0)
      model_output = model.run(bufs, transforms, inputs, chestnut_state.send if send_chestnut else None)
    except Exception:
      if not model.chestnut:
        raise
      # fallback to small model
      fallback_reason = "chestnut-load-failed"
      if model is jetlink_model:
        model.close()
        fallback_reason = None
      else:
        drop_chestnut()
        params.put_bool("ChestnutActive", False)
      cloudlog.exception("big model failed, fall back to small")
      model = small_model
      receipt_owner.loaded(small_prepared, ModelVariant.SMALL, fallback_reason, model_id=model.model_id)
      if chestnut_state is not None:
        chestnut_state.big = False
      run_count = 0
      model_output = None
    mt2 = time.perf_counter()
    model_execution_time = mt2 - mt1
    if model is jetlink_model and HOOKS.handed_over(model):
      frame_dropped_filter.x = 0.
      frame_drop_ratio = 0.
      if model.chestnut:
        receipt_owner.loaded(None, ModelVariant.CHESTNUT)
      else:
        receipt_owner.loaded(small_prepared, ModelVariant.SMALL, model_id=small_model.model_id)
      cloudlog.event("jetlinkHandover", state=HOOKS.state(model), handovers=model.handovers)

    if model_output is not None and vipc_dropped_frames == 0:
      modelv2_send = messaging.new_message('modelV2')
      drivingdata_send = messaging.new_message('drivingModelData')
      posenet_send = messaging.new_message('cameraOdometry')

      action = model.get_action_from_model(model_output, prev_action, lat_action_t, long_action_t, v_ego)
      filter_action(action, prev_action, v_ego, subaru_tau)
      prev_action = action
      fill_model_msg(modelv2_send, model_output, action,
                     publish_state, meta_main.frame_id, meta_extra.frame_id, frame_id,
                     frame_drop_ratio, meta_main.timestamp_eof, model_execution_time, extrinsics_calibration_seen)
      modelv2_send.modelV2.big = model.chestnut

      desire_state = modelv2_send.modelV2.meta.desireState
      l_lane_change_prob = desire_state[log.Desire.laneChangeLeft]
      r_lane_change_prob = desire_state[log.Desire.laneChangeRight]
      lane_change_prob = l_lane_change_prob + r_lane_change_prob
      car_state = sm['carState']
      direction = -1 if car_state.leftBlinker and not car_state.rightBlinker else (1 if car_state.rightBlinker and not car_state.leftBlinker else 0)
      mono_now_ns, boot_now_ns, clock_skew_ns = paired_clocks_ns()
      runtime_lane_policy = refresh_lane_change_policy(lane_preferences, DH, car_state, mono_now_ns, auto_vehicle_capable)
      auto_ready = (runtime_lane_policy.auto_lane_change and direction != 0 and
                    lane_clock_guard.ready(sm, mono_now_ns, boot_now_ns, clock_skew_ns) and
                    auto_evidence(sm, modelv2_send.modelV2, direction, runtime_lane_policy.minimum_lane_width_m,
                                  now_mono_ns=mono_now_ns, now_boot_ns=boot_now_ns,
                                  model_valid=bool(modelv2_send.valid), vehicle_capable=auto_vehicle_capable))
      engaged = bool(sm['carControl'].latActive)
      DH.update(car_state, sm['carControl'].latActive, lane_change_prob,
                auto_evidence=auto_ready, engaged=engaged,
                navigation_turn=matching_turn_signal(sm, mono_now_ns, supported=turn_supported))
      modelv2_send.modelV2.meta.laneChangeState = DH.lane_change_state
      modelv2_send.modelV2.meta.laneChangeDirection = DH.lane_change_direction

      phase = {"manualRequired": StatusPhase.MANUAL_REQUIRED, "waitingForDelay": StatusPhase.WAITING_FOR_DELAY,
               "laneUnavailable": StatusPhase.LANE_UNAVAILABLE, "blindspotBlocked": StatusPhase.BLINDSPOT_BLOCKED}[DH.auto_status]
      status_direction = (StatusDirection.LEFT if DH.lane_change_direction == log.LaneChangeDirection.left else
                          StatusDirection.RIGHT if DH.lane_change_direction == log.LaneChangeDirection.right else StatusDirection.NONE)
      lane_status = LaneChangeStatus(lane_status_session, lane_status_sequence,
                                     int(modelv2_send.modelV2.frameId), int(modelv2_send.modelV2.timestampEof),
                                     mono_now_ns, mono_now_ns + 150_000_000, phase, status_direction,
                                     runtime_lane_policy.auto_lane_change, engaged)
      lane_status_sequence += 1
      lane_status_msg = None
      status_bytes = encode_lane_status(lane_status)
      if status_bytes is not None:
        lane_status_msg = messaging.new_message('laneChangeAssistWire', 0)
        lane_status_msg.valid = True
        lane_status_msg.laneChangeAssistWire = status_bytes

      fill_driving_model_data(drivingdata_send, modelv2_send)
      fill_pose_msg(posenet_send, model_output, meta_main.frame_id, vipc_dropped_frames, meta_main.timestamp_eof, extrinsics_calibration_seen)
      receipt_owner.publish(pm, time.monotonic_ns())
      pm.send('modelV2', modelv2_send)
      if lane_status_msg is not None:
        pm.send('laneChangeAssistWire', lane_status_msg)
      pm.send('drivingModelData', drivingdata_send)
      pm.send('cameraOdometry', posenet_send)
    last_vipc_frame_id = meta_main.frame_id

if __name__ == "__main__":
  try:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true', help='A boolean for demo mode.')
    args = parser.parse_args()
    main(demo=args.demo)
  except KeyboardInterrupt:
    cloudlog.warning("got SIGINT")
