"""Opt-in onroad camera observation producer; no Params mailbox or control writes."""

from __future__ import annotations

import math
import os
import time
import uuid

import numpy as np

from openpilot.common.constants import CV
from openpilot.starpilot.speed_limits.vision.model import cv2
from openpilot.starpilot.speed_limits.vision.traffic_signs import TrafficSignModelCore as VisionModelCore
from openpilot.starpilot.speed_limits.vision.stop_observation import StopEvidence, StopBox, STOP_MAX_AGE_NS
from openpilot.starpilot.speed_limits.vision.observation import MODEL_ID, MAX_FRAME_AGE_NS, MAX_RESULT_AGE_NS, MAX_PAIR_SKEW_NS, clock_pair_ns
from openpilot.starpilot.spot_monitor.preferences import enabled as vasm_enabled
from openpilot.starpilot.vision_cpu import LITTLE_CORES, VISION_WITH_VASM

UNAVAILABLE_RETRY_SECONDS = 1.0
INFERENCE_INTERVAL_NS = 166_666_667
FOLLOWUP_INFERENCE_INTERVAL_NS = 100_000_000
FOLLOWUP_WINDOW_NS = 2_000_000_000
# Retain a cooldown even when processing reaches the result-age budget.
BUSY_INFERENCE_INTERVAL_NS = MAX_RESULT_AGE_NS * 3 // 2


def _requested_tasks(params) -> tuple[bool, bool]:
  from openpilot.starpilot.feature_runtime import requested

  speed = ((os.environ.get('SLC_REPLAY_RUNTIME') == '1' and os.environ.get('SLC_VISION_DEVELOPMENT') == '1') or
           requested(params, 'vision'))
  return speed, requested(params, 'vision_stop')


def _inference_interval_ns(processing_interval_ns: int, last_detection_ns: int, now_ns: int) -> int:
  # Original Dom follow-up after a usable candidate, still bounded by measured
  # processing cost. Holding a published sign alone does not renew this window.
  followup = last_detection_ns > 0 and 0 <= now_ns - last_detection_ns < FOLLOWUP_WINDOW_NS
  return max(processing_interval_ns, FOLLOWUP_INFERENCE_INTERVAL_NS if followup else INFERENCE_INTERVAL_NS)


def _configure_process() -> None:
  from openpilot.common.hardware import PC
  from openpilot.common.realtime import drop_realtime, set_core_affinity

  if PC:
    return
  for configure in (drop_realtime, lambda: set_core_affinity(list(LITTLE_CORES))):
    try:
      configure()
    except OSError:
      pass
  try:
    os.nice(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0)))
  except (AttributeError, OSError):
    pass


def _place_inference(params) -> None:
  from openpilot.common.realtime import set_core_affinity
  try:
    set_core_affinity(list(VISION_WITH_VASM if vasm_enabled(params, development=True) else LITTLE_CORES))
  except OSError:
    pass


def _driving_model_ready(sm, now_ns: int) -> bool:
  service = 'drivingModelData'
  if not sm.seen[service] or not sm.valid[service]:
    return False
  stamp_ns = sm.logMonoTime[service]
  drop = float(sm[service].frameDropPerc)
  return (stamp_ns > 0 and 0 <= now_ns - stamp_ns <= MAX_FRAME_AGE_NS and
          math.isfinite(drop) and 0 <= drop <= 1)


def _camera_buffer(client):
  buffer = client.recv(100)
  if buffer is None:
    return None
  eof_boot_ns, frame_id = int(client.timestamp_eof), int(client.frame_id)
  stride, width, height = int(client.stride), int(client.width), int(client.height)
  uv_offset = int(client.uv_offset)
  if (stride < width or width <= 0 or height <= 0 or width % 2 or height % 2 or
      uv_offset < stride * height or uv_offset % stride):
    return None
  return buffer, eof_boot_ns, frame_id, stride, width, height, uv_offset


def _convert_camera_buffer(info) -> tuple[np.ndarray, int, int] | None:
  buffer, eof_boot_ns, frame_id, stride, width, height, uv_offset = info
  # VisionBuf borrows client-owned shared memory. Copy before another receive
  # or reconnect; only the owned array is used for conversion and inference.
  raw = np.frombuffer(buffer.data, dtype=np.uint8).copy()
  required = uv_offset + stride * (height // 2)
  if raw.size < required:
    return None
  y = raw[:stride * height].reshape((height, stride))[:, :width]
  uv = raw[uv_offset:required].reshape((height // 2, stride))[:, :width]
  nv12 = np.concatenate((y, uv), axis=0)
  frame = cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)
  return frame, eof_boot_ns, frame_id


def _camera_frame(client) -> tuple[np.ndarray, int, int] | None:
  info = _camera_buffer(client)
  return _convert_camera_buffer(info) if info is not None else None


def _send(pm, *, session: str, status: str, stream: str, observed_ns: int = 0,
          eof_boot_ns: int = 0, frame_id: int = 0, result: tuple[int, float, int, int] | None = None,
          valid_until_ns: int | None = None, is_metric: bool = False,
          stop_sample: StopEvidence | None = None) -> None:
  from openpilot.cereal import messaging

  msg = messaging.new_message('slcVisionObservation', valid=True)
  event = msg.slcVisionObservation.init('vision')
  event.producerSessionId = session
  event.status = status
  event.modelId = MODEL_ID
  event.stream = stream
  event.observedMonoTime = observed_ns
  event.cameraFrameEofBootTime = eof_boot_ns
  event.frameId = frame_id
  if result is not None:
    speed, confidence, support, episode = result
    event.speedMps = speed * (CV.KPH_TO_MS if is_metric else CV.MPH_TO_MS)
    event.confidence = confidence
    event.supportCount = support
    event.episode = episode
    event.validUntilMonoTime = observed_ns + MAX_RESULT_AGE_NS if valid_until_ns is None else valid_until_ns
  stops = event.stopSigns
  stops.status = status if status in ('stale', 'unavailable') else 'unknown'
  if stop_sample is not None and status in ('valid', 'unknown'):
    if stop_sample.session != session or stop_sample.stream != stream or len(stop_sample.boxes) > 4:
      raise ValueError('STOP source identity mismatch')
    stops.status = 'valid'
    stops.observedMonoTime = stop_sample.observed_ns
    stops.validUntilMonoTime = stop_sample.observed_ns + STOP_MAX_AGE_NS
    stops.cameraFrameEofBootTime = stop_sample.eof_boot_ns
    stops.frameId = stop_sample.frame_id
    stops.imageWidth, stops.imageHeight = stop_sample.width, stop_sample.height
    for item, box in zip(stops.init('detections', len(stop_sample.boxes)), stop_sample.boxes, strict=True):
      item.confidence = box.confidence
      item.x1Pixels, item.y1Pixels, item.x2Pixels, item.y2Pixels = box.bbox
  pm.send('slcVisionObservation', msg)


def main() -> None:
  import openpilot.cereal.messaging as messaging
  from openpilot.cereal.visionipc import VisionStreamType
  from msgq.visionipc import VisionIpcClient
  from openpilot.common.params import Params
  from openpilot.common.swaglog import cloudlog

  _configure_process()
  params = Params()
  pm = messaging.PubMaster(['slcVisionObservation'])
  sm = messaging.SubMaster(['drivingModelData'])
  session = uuid.uuid4().hex
  stream = 'unknown'
  client = None
  core = None
  last_unavailable = 0.0
  tasks = _requested_tasks(params)
  try:
    if cv2 is not None:
      cv2.setNumThreads(1)
    is_metric = params.get_bool('IsMetric')
    core = VisionModelCore(is_metric=is_metric, speed_enabled=tasks[0])
  except Exception as error:
    cloudlog.error('vision SLC model unavailable: %s', error)
  last_inference_ns = 0
  last_publish_ns = 0
  last_result = None
  last_stop_sample = None
  last_result_session = ''
  last_result_observed_ns = 0
  last_result_valid_until_ns = 0
  processing_interval_ns = 0
  last_placement_check = -2.0
  last_clock_offset_ns = None
  model_paused = False
  while True:
    now = time.monotonic()
    if now - last_placement_check >= 2.0:
      _place_inference(params)
      last_placement_check = now
      current_tasks = _requested_tasks(params)
      if current_tasks != tasks:
        tasks = current_tasks
        if core is not None:
          try:
            core.set_speed_enabled(tasks[0])
          except Exception as error:
            cloudlog.error('vision SLC model unavailable: %s', error)
            core = None
          else:
            core.reset()
        session = uuid.uuid4().hex
        last_result = last_stop_sample = None
        last_result_session = ''
        last_result_observed_ns = last_result_valid_until_ns = 0
        last_inference_ns = processing_interval_ns = 0
        last_clock_offset_ns = None
        _send(pm, session=session, status='unknown', stream=stream)
    if core is None:
      now = time.monotonic()
      if now - last_unavailable >= UNAVAILABLE_RETRY_SECONDS:
        _send(pm, session=session, status='unavailable', stream=stream)
        last_unavailable = now
      time.sleep(0.1)
      continue
    if not any(tasks):
      if now - last_unavailable >= UNAVAILABLE_RETRY_SECONDS:
        _send(pm, session=session, status='unavailable', stream=stream)
        last_unavailable = now
      time.sleep(0.1)
      continue
    try:
      sm.update(0)
      current_metric = params.get_bool('IsMetric')
      if current_metric != is_metric:
        # A retained numeral and its supporting reads belong to the old units.
        is_metric = core.is_metric = current_metric
        core.reset()
        session = uuid.uuid4().hex
        last_result = None
        _send(pm, session=session, status='unknown', stream=stream)
      available = VisionIpcClient.available_streams('camerad', block=False)
      wanted = (VisionStreamType.VISION_STREAM_NARROW_ROAD if VisionStreamType.VISION_STREAM_NARROW_ROAD in available else
                VisionStreamType.VISION_STREAM_WIDE_ROAD if VisionStreamType.VISION_STREAM_WIDE_ROAD in available else None)
      if wanted is None:
        if client is not None:
          core.reset()
          session = uuid.uuid4().hex
        client = None
        stream = 'unknown'
        _send(pm, session=session, status='unavailable', stream='unknown')
        time.sleep(UNAVAILABLE_RETRY_SECONDS)
        continue
      new_stream = 'road' if wanted == VisionStreamType.VISION_STREAM_NARROW_ROAD else 'wideRoad'
      if client is None or stream != new_stream:
        core.reset()
        session = uuid.uuid4().hex
        client = VisionIpcClient('camerad', wanted, True)
        stream = new_stream
      if not client.is_connected():
        # Even a successful reconnect of the same client starts a new camera
        # session. Earlier sign support cannot confirm a new producer's frame.
        core.reset()
        session = uuid.uuid4().hex
        last_inference_ns = 0
        processing_interval_ns = 0
        last_clock_offset_ns = None
        client.connect(False)
        if not client.is_connected():
          core.reset()
          client = None
          time.sleep(0.1)
          continue
      buffer_info = _camera_buffer(client)
      if buffer_info is None:
        continue
      eof_boot_ns, frame_id = buffer_info[1:3]
      admission_pair = clock_pair_ns()
      if (admission_pair is None or eof_boot_ns <= 0 or
          not 0 <= admission_pair[1] - eof_boot_ns <= MAX_FRAME_AGE_NS):
        del buffer_info
        _send(pm, session=session, status='stale', stream=stream, eof_boot_ns=eof_boot_ns, frame_id=frame_id)
        continue
      if not _driving_model_ready(sm, admission_pair[0]):
        del buffer_info
        if not model_paused:
          core.reset()
          session = uuid.uuid4().hex
          _send(pm, session=session, status='unavailable', stream=stream)
          last_unavailable = admission_pair[0] / 1e9
        elif admission_pair[0] / 1e9 - last_unavailable >= UNAVAILABLE_RETRY_SECONDS:
          _send(pm, session=session, status='unavailable', stream=stream)
          last_unavailable = admission_pair[0] / 1e9
        model_paused = True
        time.sleep(0.1)
        continue
      model_paused = False
      admission_offset_ns = admission_pair[1] - admission_pair[0]
      offset_stable = (last_clock_offset_ns is None or
                       abs(admission_offset_ns - last_clock_offset_ns) <= MAX_PAIR_SKEW_NS)
      inference_interval_ns = _inference_interval_ns(
        processing_interval_ns, int(core.last_detection_at * 1e9), admission_pair[0])
      if offset_stable and admission_pair[0] - last_inference_ns < inference_interval_ns:
        del buffer_info
        # Camera/model liveness is separate from costly sign inference. Keep
        # the existing road-limit confirmation and its real support timestamp.
        # No conversion or new detector evidence is needed for this heartbeat.
        speed_fresh = (last_result is not None and session == last_result_session and
                       0 < last_result_observed_ns <= admission_pair[0] <= last_result_valid_until_ns)
        stop_fresh = (last_stop_sample is not None and last_stop_sample.session == session and
                      last_stop_sample.stream == stream and
                      0 <= admission_pair[0] - last_stop_sample.observed_ns < STOP_MAX_AGE_NS)
        if admission_pair[0] - last_publish_ns >= INFERENCE_INTERVAL_NS and (speed_fresh or stop_fresh):
          sm.update(0)
          heartbeat_pair = clock_pair_ns()
          if (heartbeat_pair is None or
              abs((heartbeat_pair[1] - heartbeat_pair[0]) - admission_offset_ns) > MAX_PAIR_SKEW_NS or
              not 0 <= heartbeat_pair[1] - eof_boot_ns <= MAX_FRAME_AGE_NS):
            core.reset()
            session = uuid.uuid4().hex
            last_clock_offset_ns = None
            _send(pm, session=session, status='stale', stream=stream, eof_boot_ns=eof_boot_ns, frame_id=frame_id)
            continue
          speed_fresh = speed_fresh and heartbeat_pair[0] <= last_result_valid_until_ns
          stop_fresh = (stop_fresh and last_stop_sample is not None and
                        0 <= heartbeat_pair[0] - last_stop_sample.observed_ns < STOP_MAX_AGE_NS)
          if not (speed_fresh or stop_fresh):
            continue
          if not _driving_model_ready(sm, heartbeat_pair[0]):
            core.reset()
            session = uuid.uuid4().hex
            model_paused = True
            last_unavailable = heartbeat_pair[0] / 1e9
            _send(pm, session=session, status='unavailable', stream=stream)
            continue
          if params.get_bool('IsMetric') != is_metric:
            continue
          _send(pm, session=session, status='valid' if speed_fresh else 'unknown', stream=stream,
                observed_ns=last_result_observed_ns if speed_fresh else 0, eof_boot_ns=eof_boot_ns,
                frame_id=frame_id, result=last_result if speed_fresh else None,
                valid_until_ns=last_result_valid_until_ns if speed_fresh else None, is_metric=is_metric,
                stop_sample=last_stop_sample if stop_fresh else None)
          last_publish_ns = heartbeat_pair[0]
        continue
      try:
        frame_info = _convert_camera_buffer(buffer_info)
      finally:
        del buffer_info
      if frame_info is None:
        continue
      frame, eof_boot_ns, frame_id = frame_info
      pair = clock_pair_ns()
      if pair is None or eof_boot_ns <= 0 or not 0 <= pair[1] - eof_boot_ns <= MAX_FRAME_AGE_NS:
        _send(pm, session=session, status='stale', stream=stream, eof_boot_ns=eof_boot_ns, frame_id=frame_id)
        continue
      offset_ns = pair[1] - pair[0]
      if last_clock_offset_ns is not None and abs(offset_ns - last_clock_offset_ns) > MAX_PAIR_SKEW_NS:
        core.reset()
        session = uuid.uuid4().hex
        last_inference_ns = 0
        processing_interval_ns = 0
      last_clock_offset_ns = offset_ns
      inference_interval_ns = _inference_interval_ns(processing_interval_ns, int(core.last_detection_at * 1e9), pair[0])
      if pair[0] - last_inference_ns < inference_interval_ns:
        continue
      # Budget camera conversion together with inference, as in the original Dom loop.
      last_inference_ns = admission_pair[0]
      try:
        # The observation belongs to the original camera frame, not the end of
        # CPU inference. Completion never restamps an older camera observation.
        source_observed_ns = eof_boot_ns - offset_ns
        result = core.observe(frame, now=source_observed_ns / 1e9)
      except Exception as error:
        cloudlog.error('vision SLC inference unavailable: %s', error)
        _send(pm, session=session, status='unavailable', stream=stream)
        client = None
        core = None
        continue
      sm.update(0)
      observed_pair = clock_pair_ns()
      if observed_pair is not None:
        processing_ns = observed_pair[0] - last_inference_ns
        if processing_ns >= 0:
          # Original Dom processing-cost cooldown: keep slow CPU inference from
          # immediately starting another pass, including after stale rejection.
          processing_interval_ns = min(BUSY_INFERENCE_INTERVAL_NS, processing_ns * 5 // 2)
      if params.get_bool('IsMetric') != is_metric:
        continue
      if (observed_pair is None or
          abs((observed_pair[1] - observed_pair[0]) - offset_ns) > MAX_PAIR_SKEW_NS or
          not 0 <= observed_pair[1] - eof_boot_ns <= MAX_RESULT_AGE_NS):
        core.reset()
        session = uuid.uuid4().hex
        last_clock_offset_ns = None
        _send(pm, session=session, status='stale', stream=stream, eof_boot_ns=eof_boot_ns, frame_id=frame_id)
        continue
      if not _driving_model_ready(sm, observed_pair[0]):
        core.reset()
        session = uuid.uuid4().hex
        model_paused = True
        last_unavailable = observed_pair[0] / 1e9
        _send(pm, session=session, status='unavailable', stream=stream)
        continue
      height, width = frame.shape[:2]
      last_stop_sample = StopEvidence(session, stream, source_observed_ns, eof_boot_ns, frame_id, width, height,
                                     tuple(StopBox(box.confidence, box.bbox) for box in core.stop_detections))
      # A confirmed sign remains the known road limit after it leaves view.
      # Keep its actual support time separate from this fresh camera heartbeat.
      sign_observed_ns = int(core.last_published_support_at * 1e9) if result is not None else source_observed_ns
      _send(pm, session=session, status='valid' if result is not None else 'unknown', stream=stream,
            observed_ns=sign_observed_ns, eof_boot_ns=eof_boot_ns, frame_id=frame_id, result=result,
            valid_until_ns=source_observed_ns + MAX_RESULT_AGE_NS, is_metric=is_metric, stop_sample=last_stop_sample)
      last_publish_ns = observed_pair[0]
      last_result = result
      last_result_session = session
      last_result_observed_ns = sign_observed_ns
      last_result_valid_until_ns = source_observed_ns + MAX_RESULT_AGE_NS if result is not None else 0
    except Exception as error:
      cloudlog.error('vision SLC camera unavailable: %s', error)
      _send(pm, session=session, status='unavailable', stream=stream)
      client = None
      core.reset()
      session = uuid.uuid4().hex
      last_clock_offset_ns = None
      time.sleep(0.1)
