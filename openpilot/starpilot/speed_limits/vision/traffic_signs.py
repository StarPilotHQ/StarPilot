"""Joint speed/STOP model; the existing speed producer retains its frozen core."""
from dataclasses import dataclass
import hashlib
import time

import numpy as np

from openpilot.starpilot.speed_limits.vision import model
from openpilot.starpilot.speed_limits.vision.speed_face import (SpeedFaceVerifier, SharedSpeedFaceVerifier, StopFaceVerifier,
                                                              MODEL_FILE, MODEL_SHA256)
from openpilot.starpilot.speed_limits.vision.stop_signs import StopDetection, CapturedSignDetectorOutputs, CapturedStopDetectorOutputs, decode_stop_signs


def left_panel_region(image):
  height, width = image.shape[:2]
  if image.dtype != np.uint8 or height < 32 or width < 128:
    return None
  left, right = int(width * .35), int(width * .45)
  top, bottom = int(height * .15), int(height * .70)
  hsv = model.cv2.cvtColor(image[top:bottom, left:right], model.cv2.COLOR_BGR2HSV)
  gray = model.cv2.cvtColor(image[top:bottom, left:right], model.cv2.COLOR_BGR2GRAY)
  edges = model.cv2.morphologyEx(model.cv2.Canny(gray, 20, 60), model.cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
  contours, _ = model.cv2.findContours(edges, model.cv2.RETR_LIST, model.cv2.CHAIN_APPROX_SIMPLE)
  matches = []
  for contour in contours:
    x, y, w, h = model.cv2.boundingRect(contour)
    if not (14 <= w <= width * .08 and 18 <= h <= height * .20 and .4 <= w / h <= 1.5):
      continue
    contour = model.cv2.convexHull(contour)
    if top + y + h / 2 > height * .55 or model.cv2.contourArea(contour) < w * h * .75:
      continue
    quad = model.cv2.approxPolyDP(contour, .02 * model.cv2.arcLength(contour, True), True)
    if not 4 <= len(quad) <= 6 or not model.cv2.isContourConvex(quad):
      continue
    patch = hsv[y:y + h, x:x + w]
    dark = (patch[:, :, 2] <= model.REGULATORY_DARK_VALUE_MAX) & (patch[:, :, 1] <= model.REGULATORY_DARK_SAT_MAX)
    if (float(dark.mean()) < model.REGULATORY_MIN_DARK_RATIO or
        float((patch[:, :, 1] <= model.REGULATORY_DARK_SAT_MAX).mean()) < .6 or
        float(np.percentile(patch[:, :, 2], 90) - np.percentile(patch[:, :, 2], 10)) < 15):
      continue
    side = min(256, int(width * .45), height)
    cx, cy = left + x + w // 2, top + y + h // 2
    x1, y1 = max(0, min(width - side, cx - side // 2)), max(0, min(height - side, cy - side // 2))
    box = (left + x, top + y, left + x + w, top + y + h)
    for previous, _ in matches:
      overlap = max(0, min(previous[2], box[2]) - max(previous[0], box[0])) * max(0, min(previous[3], box[3]) - max(previous[1], box[1]))
      union = w * h + (previous[2] - previous[0]) * (previous[3] - previous[1]) - overlap
      if overlap >= .75 * union:
        break
    else:
      matches.append((box, (x1, y1, x1 + side, y1 + side)))
  return matches[0][1] if len(matches) == 1 else None


@dataclass(frozen=True)
class SignDetections:
  speed: model.Detection | None
  stops: tuple[StopDetection, ...]


class TrafficSignModelCore(model.VisionModelCore):
  DETECTOR_SHA256 = 'fe54ea8d47cb89cd44697e0a7e85ce8814a12f285c811ccd89ac2bba341c4228'
  PROPOSAL_LOGIT_CALIBRATION = .16
  PUBLISHED_SCORE_MARGIN = .001
  STOP_PROJECTION_SHA256 = "a744df1b585bfd80a2a983229509f52bf2f09714d9df011109ad1b8b875aaecc"
  ASSET_MANIFEST = (DETECTOR_SHA256, model.VisionModelCore.CLASSIFIER_SHA256, MODEL_SHA256, STOP_PROJECTION_SHA256)
  MODEL_ID = (hashlib.sha256(("vision_joint_stop_projection_identity_v1\n" + "\n".join(ASSET_MANIFEST) + "\n").encode()).hexdigest() +
              ":" + model.VisionModelCore.CLASSIFIER_SHA256)
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

  def _detect_sign_from_detector_classifier(self, frame_bgr):
    detection = super()._detect_sign_from_detector_classifier(frame_bgr)
    if detection is not None or self.net is None or model.DETECTOR_CLASSIFIER_REGION_MODE != 'right_roi':
      return detection
    region = left_panel_region(frame_bgr)
    if region is None:
      return None
    face = self._speed_face_filter
    count, elapsed = face.last_forward_count, face.last_forward_duration_s
    face.begin_frame()
    try:
      x1, y1, x2, y2 = region
      proposals = model.VisionModelCore._collect_detector_classifier_proposals_from_region(
        self, frame_bgr[y1:y2, x1:x2], x1, y1, frame_bgr.shape[1], frame_bgr.shape[0], model.US_DETECTOR_MIN_CONFIDENCE)
      return super()._detect_sign_from_detector_classifier(frame_bgr, proposals=proposals)
    finally:
      face.last_forward_count += count
      face.last_forward_duration_s += elapsed

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
