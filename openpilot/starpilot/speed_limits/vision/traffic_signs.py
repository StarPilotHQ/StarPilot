"""Joint speed/STOP model; the existing speed producer retains its frozen core."""
from dataclasses import dataclass
import hashlib
import time

import numpy as np

from openpilot.starpilot.speed_limits.vision import model
from openpilot.starpilot.speed_limits.vision.speed_face import (SpeedFaceVerifier, SharedSpeedFaceVerifier, StopFaceVerifier,
                                                              MODEL_FILE, MODEL_SHA256)
from openpilot.starpilot.speed_limits.vision.stop_signs import StopDetection, CapturedSignDetectorOutputs, CapturedStopDetectorOutputs, decode_stop_signs


@dataclass(frozen=True)
class SignDetections:
  speed: model.Detection | None
  stops: tuple[StopDetection, ...]


class TrafficSignModelCore(model.VisionModelCore):
  DETECTOR_SHA256 = '52bb30f73bf0d166342f33d1b32b29b726bf036004e0359fef10ef7bf1f5556f'
  STOP_PROJECTION_SHA256 = "a744df1b585bfd80a2a983229509f52bf2f09714d9df011109ad1b8b875aaecc"
  ASSET_MANIFEST = (DETECTOR_SHA256, model.VisionModelCore.CLASSIFIER_SHA256, MODEL_SHA256, STOP_PROJECTION_SHA256)
  MODEL_ID = hashlib.sha256(("vision_joint_stop_projection_identity_v1\n" + "\n".join(ASSET_MANIFEST) + "\n").encode()).hexdigest() + ":" + model.VisionModelCore.CLASSIFIER_SHA256
  STOP_THRESHOLD = .6
  _speed_face_filter: SpeedFaceVerifier | None = None
  _stop_face_filter: StopFaceVerifier | None = None

  def __init__(self, *, is_metric: bool, speed_enabled: bool = True):
    self.stop_detections: tuple[StopDetection, ...] = ()
    self.speed_enabled = speed_enabled
    self._speed_face_filter = None
    self._stop_face_filter = None
    super().__init__(is_metric=is_metric, load_classifier=speed_enabled)
    identity = SpeedFaceVerifier(self._verified_model_path(MODEL_FILE, MODEL_SHA256))
    self._stop_face_filter = StopFaceVerifier(identity.net)
    self._speed_face_filter = identity if speed_enabled else None

  def set_speed_enabled(self, enabled):
    if enabled == self.speed_enabled:
      return
    new_face = SharedSpeedFaceVerifier(self._stop_face_filter.net) if enabled else None
    try:
      detector = self._detector_for_mode(enabled)
      classifier = self._load_net("speed_limit_us_value_classifier.onnx", self.CLASSIFIER_SHA256) if enabled else None
      if not enabled:
        self._verified_model_path("speed_limit_us_value_classifier.onnx", self.CLASSIFIER_SHA256)
      self._validate_candidate(detector, classifier, enabled)
      logits = ("onnx_node!/model.10/linear/Gemm" if classifier is not None and
                classifier.getLayerId("onnx_node!advisory/AddReject") > 0 else None)
    except BaseException:
      if new_face is not None:
        new_face.close()
      self.reset()
      raise
    old_face = self._speed_face_filter
    self.net, self.classifier_net, self.classifier_logits_layer = detector, classifier, logits
    self._speed_face_filter = new_face
    self.speed_enabled = enabled
    self.reset()
    if old_face is not None:
      old_face.close()

  @classmethod
  def _load_net(cls, name: str, expected_sha256: str):
    if name == 'speed_limit_us_detector.onnx':
      return CapturedSignDetectorOutputs(super()._load_net('traffic_signs_us_detector.onnx', expected_sha256))
    return super()._load_net(name, expected_sha256)

  def reset(self) -> None:
    super().reset()
    self.stop_detections = ()
    if self._speed_face_filter is not None:
      self._speed_face_filter.begin_frame()
      self._speed_face_filter.end_frame()
    if self._stop_face_filter is not None:
      self._stop_face_filter.begin_frame()
    if isinstance(getattr(self, 'net', None), CapturedSignDetectorOutputs):
      self.net.rgb256 = None

  def infer(self, frame_bgr: np.ndarray) -> model.Detection | None:
    self._stop_face_filter.begin_frame()
    self.stop_detections = ()
    try:
      if not getattr(self, 'speed_enabled', True):
        if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3 or frame_bgr.size == 0:
          raise ValueError('Expected a nonempty BGR frame')
        self._collect_detector_classifier_proposals(frame_bgr)
        return None
      return super().infer(frame_bgr)
    except BaseException:
      self.reset()
      raise
    finally:
      self.net.rgb256 = None

  def infer_signs(self, frame_bgr: np.ndarray) -> SignDetections:
    """Return both outputs for this frame; STOP boxes are unconfirmed proposals."""
    speed = self.infer(frame_bgr)
    return SignDetections(speed, self.stop_detections)

  def _collect_detector_classifier_proposals(self, frame_bgr):
    proposals = super()._collect_detector_classifier_proposals(frame_bgr)
    callback = getattr(self, '_stop_ready_callback', None)
    if callback is not None and self.net is not None:
      callback(tuple(self.stop_detections))
    return proposals

  def _collect_detector_classifier_proposals_from_region(self, frame_bgr, origin_x, origin_y,
                                                        full_frame_width, full_frame_height, min_confidence):
    if getattr(self, 'speed_enabled', True):
      speed = super()._collect_detector_classifier_proposals_from_region(
        frame_bgr, origin_x, origin_y, full_frame_width, full_frame_height, min_confidence)
    else:
      speed = []
      if self.net is not None:
        self._forward_stop_detector_region(frame_bgr)
    if self.net is not None:
      height, width = frame_bgr.shape[:2]
      ratio = min(self.detector_input_size / height, self.detector_input_size / width)
      self.stop_detections += decode_stop_signs(
        self.net.stops, ratio=ratio,
        pad_width=(self.detector_input_size - round(width * ratio)) / 2,
        pad_height=(self.detector_input_size - round(height * ratio)) / 2,
        region_width=width, region_height=height, origin_x=origin_x, origin_y=origin_y,
        full_frame_width=full_frame_width, full_frame_height=full_frame_height, threshold=self.STOP_THRESHOLD,
        accept_dense_id=lambda index: self._stop_face_filter.accept(self.net.rgb256, self.net.stops, index))
    return speed

  def _load_detector_net(self):
    # Bind all four assets in either mode before selecting execution.
    for name, digest in (("traffic_signs_us_detector.onnx", self.DETECTOR_SHA256),
                         ("traffic_signs_us_stop_detector.onnx", self.STOP_PROJECTION_SHA256),
                         (MODEL_FILE, MODEL_SHA256)):
      self._verified_model_path(name, digest)
    return self._detector_for_mode(self.speed_enabled)

  def _detector_for_mode(self, enabled):
    if enabled:
      return self._load_net("speed_limit_us_detector.onnx", self.DETECTOR_SHA256)
    return CapturedStopDetectorOutputs(super()._load_net("traffic_signs_us_stop_detector.onnx", self.STOP_PROJECTION_SHA256))

  @classmethod
  def _validate_candidate(cls, detector, classifier, enabled):
    detector.setInput(np.zeros((1, 3, cls.DETECTOR_SIZE, cls.DETECTOR_SIZE), dtype=np.float32))
    if enabled:
      output = detector.forward()
      if output.shape != (1, 5, 1344) or not np.isfinite(output).all():
        raise ValueError("Unsupported Vision model output shape or nonfinite output")
    else:
      detector.forward_stops()
    if classifier is not None:
      classifier.setInput(np.zeros((1, 3, cls.CLASSIFIER_SIZE, cls.CLASSIFIER_SIZE), dtype=np.float32))
      output = classifier.forward()
      if output.shape != (1, 19) or not np.isfinite(output).all():
        raise ValueError("Unsupported Vision model output shape or nonfinite output")

  def _validate_model_abi(self):
    if self.speed_enabled:
      return super()._validate_model_abi()
    self._validate_candidate(self.net, self.classifier_net, False)

  def _forward_stop_detector_region(self, frame_bgr):
    shape = (self.detector_input_size, self.detector_input_size)
    letterboxed, _, _, _ = self._letterbox(frame_bgr, shape=shape)
    blob = model.cv2.dnn.blobFromImage(letterboxed, scalefactor=1 / 255.0, size=shape, swapRB=True, crop=False)
    self.net.setInput(blob)
    began = time.monotonic()
    self.net.forward_stops()
    self.last_detector_forward_count += 1
    self.last_detector_forward_duration_s += time.monotonic() - began
