"""Frozen active US sign inference, isolated from camera/Params and debug collection."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
import hashlib
import math
import time

import numpy as np

try:
  import cv2
except ImportError:  # Keep inference unavailable when OpenCV is absent.
  cv2 = None

MIN_DETECTION_CONFIDENCE = 0.2
PUBLISHED_CHANGE_COOLDOWN_SECONDS = 1.4
PUBLISHED_REVERT_CONFIDENCE = 0.97

TEMPORAL_TRACKING_ENABLED = False

TRACK_MIN_PROPOSAL_CONFIDENCE = 0.10

TRACK_UNREADABLE_MIN_PROPOSAL_CONFIDENCE = 0.22

DETECTOR_CLASSIFIER_REGION_MODE = "right_roi"  # full, right_roi, full_and_right_roi

STRONG_DETECTION_CONFIDENCE = 0.72

HISTORY_SECONDS = 2.0

CONSISTENT_DETECTIONS = 2

CHANGE_CONSISTENT_DETECTIONS = 2

CHANGE_SINGLE_READ_MIN_CONFIDENCE = 0.83

CHANGE_REPEAT_MIN_CONFIDENCE = 0.70

LOW_SPEED_CHANGE_CONSISTENT_DETECTIONS = 2

LOW_SPEED_CHANGE_MIN_CONFIDENCE = 0.90

LOW_SPEED_CHANGE_ALLOW_STRONG_CONSENSUS = True

MODEL_DETECTION_SHORT_CIRCUIT_CONFIDENCE = 0.65

MODEL_PROPOSAL_MAX_COUNT = 4

MODEL_PROPOSAL_MAX_AREA_RATIO = 0.18

MODEL_PROPOSAL_MIN_WIDTH = 10

MODEL_PROPOSAL_MIN_HEIGHT = 18

MODEL_PROPOSAL_MIN_X_RATIO = 0.35

MODEL_PROPOSAL_MAX_Y_RATIO = 0.82

ROI_WINDOWS = (
  {"bounds": (0.48, 0.00, 0.98, 0.42), "min_confidence": MIN_DETECTION_CONFIDENCE},
  {"bounds": (0.52, 0.02, 0.97, 0.58), "min_confidence": 0.22},
  {"bounds": (0.62, 0.02, 0.99, 0.68), "min_confidence": 0.18},
  {"bounds": (0.45, 0.00, 1.00, 0.82), "min_confidence": 0.06},
)

REGULATORY_WHITE_VALUE_MIN = 135

REGULATORY_WHITE_SAT_MAX = 70

REGULATORY_DARK_VALUE_MAX = 115

REGULATORY_DARK_SAT_MAX = 110

REGULATORY_YELLOW_HUE_MIN = 12

REGULATORY_YELLOW_HUE_MAX = 45

REGULATORY_YELLOW_SAT_MIN = 70

REGULATORY_YELLOW_VALUE_MIN = 85

REGULATORY_RED_LOW_HUE_MAX = 12

REGULATORY_RED_HIGH_HUE_MIN = 168

REGULATORY_RED_SAT_MIN = 80

REGULATORY_RED_VALUE_MIN = 60

REGULATORY_GREEN_HUE_MIN = 45

REGULATORY_GREEN_HUE_MAX = 90

REGULATORY_BLUE_HUE_MIN = 90

REGULATORY_BLUE_HUE_MAX = 135

REGULATORY_COLORED_SAT_MIN = 70

REGULATORY_COLORED_VALUE_MIN = 70

REGULATORY_MIN_WHITE_RATIO = 0.08

REGULATORY_MIN_DARK_RATIO = 0.01

REGULATORY_MAX_YELLOW_RATIO = 0.12

REGULATORY_MAX_RED_RATIO = 0.10

REGULATORY_MAX_GREEN_RATIO = 0.35

REGULATORY_MAX_BLUE_RATIO = 0.35

REGULATORY_MIN_WHITE_COMPONENT_RATIO = 0.012

REGULATORY_MIN_COMPONENT_FILL = 0.36

REGULATORY_MIN_COMPONENT_HEIGHT_RATIO = 0.2

REGULATORY_MIN_COMPONENT_WIDTH_RATIO = 0.12

REGULATORY_MIN_ASPECT_RATIO = 0.28

REGULATORY_MAX_ASPECT_RATIO = 1.25

MIN_PUBLISHABLE_SPEED_LIMIT_MPH = 5

MAX_IMPERIAL_PUBLISHABLE_SPEED_LIMIT_MPH = 80

US_DETECTOR_CLASSES = {
  0: "regulatory_speed_limit",
  1: "advisory_speed_limit",
  2: "school_zone_speed_limit",
}

US_CLASSIFIER_SPEED_VALUES = (10, 100, 15, 20, 25, 30, 35, 40, 45, 5, 50, 55, 60, 65, 70, 75, 80, 90)

EXTENDED_CLASSIFIER_SPEED_VALUES = frozenset((5, 10, 80, 90, 100))

SCHOOL_ZONE_SPEED_VALUES = frozenset((15, 20, 25))

US_DETECTOR_MIN_CONFIDENCE = 0.06

US_CLASSIFIER_MIN_CONFIDENCE = 0.60

EXTENDED_CLASSIFIER_MIN_CONFIDENCE = 0.90

US_CLASSIFIER_REJECT_MIN_CONFIDENCE = 0.85

DETECTOR_CLASSIFIER_EXPANSIONS = (
  (0.00, 0.00, 0.00, 0.00, 1.10),
  (0.10, 0.06, 0.10, 0.12, 1.00),
  (0.00, 0.00, 0.18, 0.18, 0.55),
)

SCHOOL_ZONE_DIRECT_EXPANSIONS = (
  (0.00, 0.00, 0.18, 0.18),
  (0.00, 0.00, 0.22, 0.18),
)

SCHOOL_ZONE_READ_VARIANTS = (
  (0.00, 0.00, 1.00, 1.00, 0.80),
  (0.00, 0.35, 1.00, 1.00, 0.88),
  (0.00, 0.45, 1.00, 1.00, 0.96),
  (0.12, 0.38, 0.88, 1.00, 1.00),
)

DETECTOR_CLASSIFIER_SUPPORT_BONUS = 0.06

DETECTOR_CLASSIFIER_REGULATORY_BONUS = 0.05

DETECTOR_CLASSIFIER_NON_REGULATORY_PENALTY = 0.03

DETECTOR_CLASSIFIER_SMALL_BOX_AREA_RATIO = 0.004

DETECTOR_CLASSIFIER_TINY_LOW_CONF_AREA_RATIO = 0.002

DETECTOR_CLASSIFIER_TINY_LOW_CONF_MIN_CONFIDENCE = 0.16

DETECTOR_CLASSIFIER_MIN_ACCEPT_WIDTH = 28

DETECTOR_CLASSIFIER_MIN_ACCEPT_HEIGHT = 40

DETECTOR_CLASSIFIER_RESCUE_MIN_WIDTH = 14

DETECTOR_CLASSIFIER_RESCUE_MIN_HEIGHT = 18

DETECTOR_CLASSIFIER_RESCUE_MIN_X_RATIO = 0.52

DETECTOR_CLASSIFIER_RESCUE_MIN_SUPPORT = 2

DETECTOR_CLASSIFIER_RESCUE_MIN_CONFIDENCE = 0.90

DETECTOR_CLASSIFIER_RESCUE_MAX_SCORE = 0.64

DETECTOR_CLASSIFIER_STRONG_RESCUE_MIN_SUPPORT = 3

DETECTOR_CLASSIFIER_STRONG_RESCUE_MIN_PROPOSAL_CONFIDENCE = 0.60

DETECTOR_CLASSIFIER_STRONG_RESCUE_MIN_READ_CONFIDENCE = 0.995

DETECTOR_CLASSIFIER_STRONG_RESCUE_MAX_SCORE = 0.74

DETECTOR_CLASSIFIER_TRUSTED_MODEL_MAX_HEIGHT = 55

DETECTOR_CLASSIFIER_TRUSTED_MODEL_MAX_AREA_RATIO = 0.002

DETECTOR_CLASSIFIER_TRUSTED_MODEL_MIN_PROPOSAL_CONFIDENCE = 0.18

DETECTOR_CLASSIFIER_TRUSTED_MODEL_MIN_X_RATIO = 0.52

DETECTOR_CLASSIFIER_TRUSTED_MODEL_MIN_READ_CONFIDENCE = 0.65

DETECTOR_CLASSIFIER_TRUSTED_MODEL_MIN_SUPPORT = 2

DETECTOR_CLASSIFIER_STRONG_MODEL_MIN_PROPOSAL_CONFIDENCE = 0.60

DETECTOR_CLASSIFIER_STRONG_MODEL_MIN_READ_CONFIDENCE = 0.995

DETECTOR_CLASSIFIER_STRONG_MODEL_CONSENSUS_MIN_READ_CONFIDENCE = 0.95

DETECTOR_CLASSIFIER_STRONG_MODEL_CONSENSUS_ENABLED = True

DETECTOR_CLASSIFIER_STRONG_MODEL_CONSENSUS_MIN_SUPPORT = 2

DETECTOR_CLASSIFIER_MODEL_ONLY_CONSENSUS_MIN_CONFIDENCE = 0.90

DETECTOR_CLASSIFIER_MODEL_ONLY_CONSENSUS_MIN_SUPPORT = 2

SCHOOL_ZONE_SPEED_PRIOR = 0.12

SCHOOL_ZONE_SUPPORT_BONUS = 0.08

SCHOOL_ZONE_MIN_SUPPORT = 2

SCHOOL_ZONE_MIN_CONFIDENCE = 0.70

SCHOOL_ZONE_SINGLE_READ_CONFIDENCE = 0.975

SCHOOL_ZONE_SHORT_CIRCUIT_CONFIDENCE = 0.78

SCHOOL_ZONE_FALLBACK_MIN_CONFIDENCE = 0.35

NON_SCHOOL_LOW_SPEED_COMPETING_MIN_CONFIDENCE = 0.95


@dataclass(frozen=True)
class Detection:
  speed_limit_mph: int
  confidence: float
  strong_consensus: bool = False

@dataclass(frozen=True)
class DetectorProposal:
  confidence: float
  class_id: int
  bbox: tuple[int, int, int, int]
  speed_limit_mph: int = 0

@dataclass(frozen=True)
class HistoryEntry:
  speed_limit_mph: int
  confidence: float
  created_at: float
  strong_consensus: bool = False

def _primary_regulatory_panel(image):
  height, width = image.shape[:2]
  if image.dtype != np.uint8 or width < 24 or height < 1.8 * width:
    return image

  gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
  _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
  for foreground in (mask >= 128, mask < 128):
    occupied = foreground[:, int(width * .2):int(width * .8)].mean(1) > .8
    edges = np.diff(np.r_[False, occupied, False].astype(np.int8))
    runs = list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=False))
    for (top, bottom), (next_top, next_bottom) in zip(runs, runs[1:], strict=False):
      if (top <= .3 * width and .75 * width <= bottom - top <= 1.6 * width and
          next_bottom - next_top >= .5 * width and .02 * width <= next_top - bottom <= .15 * width):
        # Keep a separate lower plate, such as MINIMUM, out of the number reader.
        return image[:min(height, int(bottom + .03 * width))]
  return image


class VisionModelCore:
  """The frozen active detector/classifier path, with no camera or Params ownership."""

  ASSET_DIR = Path(__file__).resolve().parent / "assets"
  DETECTOR_SHA256 = "82408b68c79c269296f0af942130c5383cace4ee06c78e2a4690e8488720116a"
  CLASSIFIER_SHA256 = "5d861067ab8f50986488f983cbc24f78527775d37af0b9740c5545d5f3173902"
  DETECTOR_SIZE = 256
  CLASSIFIER_SIZE = 128

  def __init__(self, *, is_metric: bool, load_classifier: bool = True):
    if cv2 is None:
      raise RuntimeError("OpenCV DNN is unavailable")
    self.is_metric = is_metric
    self.last_classifier_reject_dominant = False
    self.net = self._load_net("speed_limit_us_detector.onnx", self.DETECTOR_SHA256)
    self.classifier_net = (self._load_net("speed_limit_us_value_classifier.onnx", self.CLASSIFIER_SHA256)
                           if load_classifier else None)
    if not load_classifier:
      self._verified_model_path("speed_limit_us_value_classifier.onnx", self.CLASSIFIER_SHA256)
    self._validate_model_abi()
    self.classifier_logits_layer = ("onnx_node!/model.10/linear/Gemm"
                                    if self.classifier_net is not None and self.classifier_net.getLayerId("onnx_node!advisory/AddReject") > 0 else None)
    self.detector_input_size = self.DETECTOR_SIZE
    self.classifier_input_size = self.CLASSIFIER_SIZE
    self.last_detector_forward_count = 0
    self.last_detector_forward_duration_s = 0.0
    self.last_classifier_forward_count = 0
    self.last_classifier_forward_duration_s = 0.0
    self.last_classifier_advisory_rejected = False
    self.temporal_tracking_enabled = False
    self.latest_detector_proposal = None
    self.history: deque[HistoryEntry] = deque()
    self.published_speed_limit_mph = 0
    self.published_confidence = 0.0
    self.previous_published_speed_limit_mph = 0
    self.last_publish_change_at = 0.0
    self.last_detection_at = 0.0
    self.last_published_support_at = 0.0
    self.episode = 0
    self.published_support_count = 0

  def reset(self) -> None:
    """Discard evidence from a previous camera, stream or sign-unit setting."""
    self.history.clear()
    self.last_classifier_advisory_rejected = False
    self.last_classifier_reject_dominant = False
    self.latest_detector_proposal = None
    self.published_speed_limit_mph = 0
    self.published_confidence = 0.0
    self.previous_published_speed_limit_mph = 0
    self.last_publish_change_at = 0.0
    self.last_detection_at = 0.0
    self.last_published_support_at = 0.0
    self.published_support_count = 0
    self.episode = 0

  @classmethod
  def _verified_model_path(cls, name: str, expected_sha256: str) -> Path:
    path = cls.ASSET_DIR / name
    with path.open("rb") as stream:
      actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected_sha256:
      raise ValueError(f"Vision model identity mismatch: {name}")
    return path

  @classmethod
  def _load_net(cls, name: str, expected_sha256: str):
    path = cls._verified_model_path(name, expected_sha256)
    net = cv2.dnn.readNetFromONNX(str(path))
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
    return net

  def _validate_model_abi(self) -> None:
    self.net.setInput(np.zeros((1, 3, self.DETECTOR_SIZE, self.DETECTOR_SIZE), dtype=np.float32))
    detector_output = self.net.forward()
    if detector_output.shape != (1, 5, 1344) or not np.isfinite(detector_output).all():
      raise ValueError("Unsupported Vision model output shape or nonfinite output")
    if self.classifier_net is not None:
      self.classifier_net.setInput(np.zeros((1, 3, self.CLASSIFIER_SIZE, self.CLASSIFIER_SIZE), dtype=np.float32))
      classifier_output = self.classifier_net.forward()
      if classifier_output.shape != (1, 19) or not np.isfinite(classifier_output).all():
        raise ValueError("Unsupported Vision model output shape or nonfinite output")

  def infer(self, frame_bgr: np.ndarray) -> Detection | None:
    if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3 or frame_bgr.size == 0:
      raise ValueError("Expected a nonempty BGR frame")
    face_filter = getattr(self, '_speed_face_filter', None)
    try:
      return self._publishable_detection(self._detect_sign_from_detector_classifier(frame_bgr))
    finally:
      if face_filter is not None:
        face_filter.end_frame()

  def observe(self, frame_bgr: np.ndarray, *, now: float) -> tuple[int, float, int, int] | None:
    """Return the confirmed sign numeral in the configured units until reset or replacement."""
    if not math.isfinite(now) or now <= 0:
      raise ValueError("Invalid observation time")
    detection = self.infer(frame_bgr)
    if detection is not None and math.isfinite(detection.confidence) and 0 <= detection.confidence <= 1:
      self.last_detection_at = now
      self.history.append(HistoryEntry(detection.speed_limit_mph, detection.confidence, now, detection.strong_consensus))
      self._prune_history(now)
      confirmed = self._confirm_detection()
      if confirmed is not None:
        speed, confidence = confirmed
        reverting_early = (speed != self.published_speed_limit_mph and
                           speed == self.previous_published_speed_limit_mph and
                           now - self.last_publish_change_at < PUBLISHED_CHANGE_COOLDOWN_SECONDS and
                           confidence < PUBLISHED_REVERT_CONFIDENCE)
        if reverting_early:
          return (
            self.published_speed_limit_mph, self.published_confidence, self.published_support_count, self.episode)
        support = sum(entry.speed_limit_mph == speed for entry in self.history)
        if speed != self.published_speed_limit_mph:
          self.previous_published_speed_limit_mph = self.published_speed_limit_mph
          self.published_speed_limit_mph = speed
          self.last_publish_change_at = now
          self.episode += 1
          self.history.clear()
          self.history.append(HistoryEntry(speed, confidence, now, detection.strong_consensus))
        self.published_confidence = confidence
        self.published_support_count = support
        if detection.speed_limit_mph == speed:
          self.last_published_support_at = now
    if self.published_speed_limit_mph:
      return self.published_speed_limit_mph, self.published_confidence, self.published_support_count, self.episode
    return None

  @staticmethod
  def _letterbox(image, shape=(640, 640), color=(114, 114, 114)):
    image_height, image_width = image.shape[:2]
    ratio = min(shape[0] / image_height, shape[1] / image_width)
    resized_width = int(round(image_width * ratio))
    resized_height = int(round(image_height * ratio))
    pad_width = (shape[1] - resized_width) / 2
    pad_height = (shape[0] - resized_height) / 2

    if (image_width, image_height) != (resized_width, resized_height):
      image = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)

    top = int(round(pad_height - 0.1))
    bottom = int(round(pad_height + 0.1))
    left = int(round(pad_width - 0.1))
    right = int(round(pad_width + 0.1))
    image = cv2.copyMakeBorder(image, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return image, ratio, pad_width, pad_height


  def _remember_detector_proposal(self, confidence, class_id, bbox, speed_limit_mph=0, preferred=False):
    min_confidence = TRACK_MIN_PROPOSAL_CONFIDENCE if speed_limit_mph else TRACK_UNREADABLE_MIN_PROPOSAL_CONFIDENCE
    if not getattr(self, "temporal_tracking_enabled", TEMPORAL_TRACKING_ENABLED) or class_id == 1 or confidence < min_confidence:
      return
    proposal = DetectorProposal(float(confidence), int(class_id), bbox, int(speed_limit_mph))
    latest_proposal = getattr(self, "latest_detector_proposal", None)
    if preferred or latest_proposal is None or proposal.confidence > latest_proposal.confidence:
      self.latest_detector_proposal = proposal


  def _publishable_detection(self, detection):
    if detection is None:
      return None
    if detection.speed_limit_mph < MIN_PUBLISHABLE_SPEED_LIMIT_MPH:
      return None
    if not getattr(self, "is_metric", False) and detection.speed_limit_mph > MAX_IMPERIAL_PUBLISHABLE_SPEED_LIMIT_MPH:
      return None
    return detection


  def _is_regulatory_speed_sign(self, sign_crop):
    if sign_crop.size == 0:
      return False

    crop_height, crop_width = sign_crop.shape[:2]
    crop_area = crop_height * crop_width
    if crop_area <= 0:
      return False

    hsv = cv2.cvtColor(sign_crop, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    white_mask = ((value >= REGULATORY_WHITE_VALUE_MIN) & (saturation <= REGULATORY_WHITE_SAT_MAX)).astype(np.uint8)
    dark_mask = ((value <= REGULATORY_DARK_VALUE_MAX) & (saturation <= REGULATORY_DARK_SAT_MAX)).astype(np.uint8)
    yellow_mask = (
      (hue >= REGULATORY_YELLOW_HUE_MIN) &
      (hue <= REGULATORY_YELLOW_HUE_MAX) &
      (saturation >= REGULATORY_YELLOW_SAT_MIN) &
      (value >= REGULATORY_YELLOW_VALUE_MIN)
    ).astype(np.uint8)
    red_mask = (
      (((hue <= REGULATORY_RED_LOW_HUE_MAX) | (hue >= REGULATORY_RED_HIGH_HUE_MIN))) &
      (saturation >= REGULATORY_RED_SAT_MIN) &
      (value >= REGULATORY_RED_VALUE_MIN)
    ).astype(np.uint8)
    green_mask = (
      (hue >= REGULATORY_GREEN_HUE_MIN) &
      (hue <= REGULATORY_GREEN_HUE_MAX) &
      (saturation >= REGULATORY_COLORED_SAT_MIN) &
      (value >= REGULATORY_COLORED_VALUE_MIN)
    ).astype(np.uint8)
    blue_mask = (
      (hue >= REGULATORY_BLUE_HUE_MIN) &
      (hue <= REGULATORY_BLUE_HUE_MAX) &
      (saturation >= REGULATORY_COLORED_SAT_MIN) &
      (value >= REGULATORY_COLORED_VALUE_MIN)
    ).astype(np.uint8)

    white_ratio = float(white_mask.mean())
    dark_ratio = float(dark_mask.mean())
    yellow_ratio = float(yellow_mask.mean())
    red_ratio = float(red_mask.mean())
    green_ratio = float(green_mask.mean())
    blue_ratio = float(blue_mask.mean())

    if white_ratio < REGULATORY_MIN_WHITE_RATIO or dark_ratio < REGULATORY_MIN_DARK_RATIO:
      return False
    if yellow_ratio > REGULATORY_MAX_YELLOW_RATIO and yellow_ratio > white_ratio * 0.45:
      return False
    if red_ratio > REGULATORY_MAX_RED_RATIO and red_ratio > white_ratio * 0.35:
      return False
    if green_ratio > REGULATORY_MAX_GREEN_RATIO and green_ratio > white_ratio * 0.60:
      return False
    if blue_ratio > REGULATORY_MAX_BLUE_RATIO and blue_ratio > white_ratio * 0.60:
      return False

    white_binary = (white_mask * 255).astype(np.uint8)
    contours, _ = cv2.findContours(white_binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_component_area = crop_area * REGULATORY_MIN_WHITE_COMPONENT_RATIO

    for contour in contours:
      area = cv2.contourArea(contour)
      if area < min_component_area:
        continue

      x, y, width, height = cv2.boundingRect(contour)
      if height < crop_height * REGULATORY_MIN_COMPONENT_HEIGHT_RATIO:
        continue
      if width < crop_width * REGULATORY_MIN_COMPONENT_WIDTH_RATIO:
        continue

      aspect_ratio = width / max(height, 1)
      if aspect_ratio < REGULATORY_MIN_ASPECT_RATIO or aspect_ratio > REGULATORY_MAX_ASPECT_RATIO:
        continue

      fill_ratio = area / max(width * height, 1)
      if fill_ratio < REGULATORY_MIN_COMPONENT_FILL:
        continue

      return True

    return False


  @staticmethod
  def _softmax(scores):
    scores = scores.astype(np.float32)
    scores = scores - np.max(scores)
    exp_scores = np.exp(scores)
    denominator = np.sum(exp_scores)
    if denominator <= 0:
      return exp_scores
    return exp_scores / denominator


  @staticmethod
  def _normalize_classifier_output(scores):
    scores = scores.astype(np.float32)
    if scores.size == 0:
      return scores

    # Ultralytics classifier ONNX exports may already emit normalized probabilities.
    if np.all(scores >= 0.0) and np.all(scores <= 1.0):
      total = float(np.sum(scores))
      if 0.99 <= total <= 1.01:
        return scores

    return VisionModelCore._softmax(scores)


  @staticmethod
  def _square_resize(image, size=128, color=(114, 114, 114)):
    image_height, image_width = image.shape[:2]
    ratio = min(size / max(image_height, 1), size / max(image_width, 1))
    resized_width = max(int(round(image_width * ratio)), 1)
    resized_height = max(int(round(image_height * ratio)), 1)
    image = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)

    canvas = np.full((size, size, image.shape[2]), color, dtype=image.dtype)
    offset_x = (size - resized_width) // 2
    offset_y = (size - resized_height) // 2
    canvas[offset_y:offset_y + resized_height, offset_x:offset_x + resized_width] = image
    return canvas


  @staticmethod
  def _crop_by_ratio(image, left_ratio, top_ratio, right_ratio, bottom_ratio):
    image_height, image_width = image.shape[:2]
    x1 = max(int(image_width * left_ratio), 0)
    y1 = max(int(image_height * top_ratio), 0)
    x2 = min(int(image_width * right_ratio), image_width)
    y2 = min(int(image_height * bottom_ratio), image_height)
    if x2 <= x1 or y2 <= y1:
      return None
    crop = image[y1:y2, x1:x2]
    return crop if crop.size > 0 else None


  def _iter_school_zone_read_crops(self, sign_crop):
    yielded = set()
    for left_ratio, top_ratio, right_ratio, bottom_ratio, weight in SCHOOL_ZONE_READ_VARIANTS:
      crop = self._crop_by_ratio(sign_crop, left_ratio, top_ratio, right_ratio, bottom_ratio)
      if crop is None:
        continue
      key = (crop.shape[1], crop.shape[0], left_ratio, top_ratio, right_ratio, bottom_ratio)
      if key in yielded:
        continue
      yielded.add(key)
      yield crop, weight


  def _forward_detector_region(self, frame_bgr):
    detector_shape = (self.detector_input_size, self.detector_input_size)
    letterboxed, ratio, pad_width, pad_height = self._letterbox(frame_bgr, shape=detector_shape)
    blob = cv2.dnn.blobFromImage(letterboxed, scalefactor=1 / 255.0, size=detector_shape, swapRB=True, crop=False)
    face_filter = getattr(self, '_speed_face_filter', None)
    if face_filter is not None:
      face_filter.capture_blob(blob)
    self.net.setInput(blob)

    forward_started_at = time.monotonic()
    predictions = np.squeeze(self.net.forward())
    self.last_detector_forward_count += 1
    self.last_detector_forward_duration_s += time.monotonic() - forward_started_at
    return predictions, ratio, pad_width, pad_height


  def _collect_detector_classifier_proposals_from_region(self, frame_bgr, origin_x, origin_y, full_frame_width, full_frame_height, min_confidence):
    if self.net is None:
      return []
    region_height, region_width = frame_bgr.shape[:2]
    predictions, ratio, pad_width, pad_height = self._forward_detector_region(frame_bgr)
    if predictions.ndim != 2:
      return []
    if predictions.shape[0] < predictions.shape[1]:
      predictions = predictions.T

    candidates = []
    class_scores = predictions[:, 4:]
    class_ids = np.argmax(class_scores, axis=1)
    confidences = class_scores[np.arange(len(predictions)), class_ids].astype(np.float64)
    selected = np.flatnonzero(np.isin(class_ids, tuple(US_DETECTOR_CLASSES)) & ~(confidences < min_confidence))
    for index in selected:
      prediction = predictions[index]
      class_id = int(class_ids[index])
      confidence = float(confidences[index])

      center_x, center_y, width, height = prediction[:4]
      x1 = max(int((center_x - width / 2 - pad_width) / ratio), 0)
      y1 = max(int((center_y - height / 2 - pad_height) / ratio), 0)
      x2 = min(int((center_x + width / 2 - pad_width) / ratio), region_width)
      y2 = min(int((center_y + height / 2 - pad_height) / ratio), region_height)
      if x2 <= x1 or y2 <= y1:
        continue

      x1 += origin_x
      y1 += origin_y
      x2 += origin_x
      y2 += origin_y

      box_width = x2 - x1
      box_height = y2 - y1
      if box_width < MODEL_PROPOSAL_MIN_WIDTH or box_height < MODEL_PROPOSAL_MIN_HEIGHT:
        continue
      if box_width * box_height > full_frame_width * full_frame_height * MODEL_PROPOSAL_MAX_AREA_RATIO:
        continue
      if (x1 + x2) / 2 < full_frame_width * MODEL_PROPOSAL_MIN_X_RATIO:
        continue
      if y1 > full_frame_height * MODEL_PROPOSAL_MAX_Y_RATIO:
        continue

      candidates.append((confidence, class_id, (x1, y1, x2, y2)))
      face_filter = getattr(self, '_speed_face_filter', None)
      if face_filter is not None:
        face_filter.remember((confidence, class_id, (x1, y1, x2, y2)), prediction[:4], index)

    return candidates


  def _collect_detector_classifier_proposals(self, frame_bgr):
    face_filter = getattr(self, '_speed_face_filter', None)
    if face_filter is not None:
      face_filter.begin_frame()
    if self.net is None:
      return []

    frame_height, frame_width = frame_bgr.shape[:2]
    candidates = []
    if DETECTOR_CLASSIFIER_REGION_MODE in ("full", "full_and_right_roi"):
      candidates.extend(self._collect_detector_classifier_proposals_from_region(
        frame_bgr,
        0,
        0,
        frame_width,
        frame_height,
        US_DETECTOR_MIN_CONFIDENCE,
      ))

    # A second pass on a focused right-side ROI materially improves small U.S. sign reads.
    if DETECTOR_CLASSIFIER_REGION_MODE in ("right_roi", "full_and_right_roi"):
      bounds = ROI_WINDOWS[-1]["bounds"]
      minimum = ROI_WINDOWS[-1]["min_confidence"]
      assert isinstance(bounds, tuple) and isinstance(minimum, (int, float))
      left_ratio, top_ratio, right_ratio, bottom_ratio = bounds
      left = int(frame_width * left_ratio)
      top = int(frame_height * top_ratio)
      right = int(frame_width * right_ratio)
      bottom = int(frame_height * bottom_ratio)
      roi = frame_bgr[top:bottom, left:right]
      if roi.size > 0:
        candidates.extend(self._collect_detector_classifier_proposals_from_region(
          roi,
          left,
          top,
          frame_width,
          frame_height,
          max(float(minimum), US_DETECTOR_MIN_CONFIDENCE),
        ))

    return sorted(candidates, reverse=True)[:MODEL_PROPOSAL_MAX_COUNT]


  def _classify_speed_limit_from_model(self, sign_crop):
    self.last_classifier_advisory_rejected = False
    self.last_classifier_reject_dominant = False
    if self.classifier_net is None or sign_crop.size == 0:
      return None

    speed_class_count = len(US_CLASSIFIER_SPEED_VALUES)

    input_size = self.classifier_input_size
    sign_crop = _primary_regulatory_panel(sign_crop)
    padded_crop = self._square_resize(sign_crop, size=input_size)
    blob = cv2.dnn.blobFromImage(padded_crop, scalefactor=1 / 255.0, size=(input_size, input_size), swapRB=True, crop=False)
    self.classifier_net.setInput(blob)

    forward_started_at = time.monotonic()
    logits_layer = getattr(self, "classifier_logits_layer", None)
    original_probabilities = None
    if logits_layer is not None:
      output, original_logits = self.classifier_net.forward(['output0', logits_layer])
      original_logits = np.asarray(original_logits).reshape(-1)
      if original_logits.size != speed_class_count + 1 or not np.isfinite(original_logits).all():
        raise ValueError("Invalid original classifier logits")
      original_probabilities = self._normalize_classifier_output(original_logits)
    else:
      output = self.classifier_net.forward()
    scores = np.asarray(output).reshape(-1)
    self.last_classifier_forward_count += 1
    self.last_classifier_forward_duration_s += time.monotonic() - forward_started_at
    has_reject_class = scores.size == speed_class_count + 1
    if scores.size != speed_class_count and not has_reject_class:
      return None

    probabilities = self._normalize_classifier_output(scores)
    self.last_classifier_reject_dominant = bool(
      has_reject_class and original_probabilities is not None and np.isfinite(probabilities).all() and
      int(np.argmax(original_probabilities)) == speed_class_count and int(np.argmax(probabilities)) == speed_class_count
    )
    speed_probabilities = probabilities[:speed_class_count]
    class_index = int(np.argmax(speed_probabilities))
    confidence = float(speed_probabilities[class_index])
    speed_limit = US_CLASSIFIER_SPEED_VALUES[class_index]
    if has_reject_class and float(probabilities[speed_class_count]) >= max(confidence, US_CLASSIFIER_REJECT_MIN_CONFIDENCE):
      self.last_classifier_advisory_rejected = bool(
        original_probabilities is not None and float(np.max(original_probabilities[:speed_class_count])) >= .98 and
        float(probabilities[speed_class_count]) >= .90
      )
      return None
    minimum_confidence = (
      EXTENDED_CLASSIFIER_MIN_CONFIDENCE if speed_limit in EXTENDED_CLASSIFIER_SPEED_VALUES else US_CLASSIFIER_MIN_CONFIDENCE
    )
    if confidence < minimum_confidence:
      return None

    return speed_limit, confidence


  def _detect_sign_from_detector_classifier(self, frame_bgr):
    frame_height, frame_width = frame_bgr.shape[:2]
    best_detection = None
    best_score = 0.0
    best_retry_conflict = False
    rejected_advisory_boxes: list[tuple[int, int, int, int]] = []

    for proposal_confidence, class_id, (x1, y1, x2, y2) in self._collect_detector_classifier_proposals(frame_bgr):
      if class_id == 1:
        continue
      if class_id == 0:
        duplicate_advisory = False
        for rx1, ry1, rx2, ry2 in rejected_advisory_boxes:
          overlap = max(0, min(x2, rx2) - max(x1, rx1)) * max(0, min(y2, ry2) - max(y1, ry1))
          union = (x2 - x1) * (y2 - y1) + (rx2 - rx1) * (ry2 - ry1) - overlap
          if union > 0 and overlap >= .75 * union:
            duplicate_advisory = True
            break
        if duplicate_advisory:
          continue

      box_width = x2 - x1
      box_height = y2 - y1
      if box_width <= 0 or box_height <= 0:
        continue
      is_small_box = box_width < DETECTOR_CLASSIFIER_MIN_ACCEPT_WIDTH or box_height < DETECTOR_CLASSIFIER_MIN_ACCEPT_HEIGHT
      # Tiny far-away proposals are the main nighttime false-positive source.
      # Keep a narrow rescue path for right-side high-confidence reads,
      # then let temporal confirmation decide whether they are real.
      if is_small_box and (
        box_width < DETECTOR_CLASSIFIER_RESCUE_MIN_WIDTH or
        box_height < DETECTOR_CLASSIFIER_RESCUE_MIN_HEIGHT or
        x1 < frame_width * DETECTOR_CLASSIFIER_RESCUE_MIN_X_RATIO
      ):
        continue

      proposal_area_ratio = (box_width * box_height) / max(frame_width * frame_height, 1)
      is_tiny_low_conf_box = (
        class_id != 2 and
        proposal_area_ratio < DETECTOR_CLASSIFIER_TINY_LOW_CONF_AREA_RATIO and
        proposal_confidence < DETECTOR_CLASSIFIER_TINY_LOW_CONF_MIN_CONFIDENCE
      )
      self._remember_detector_proposal(proposal_confidence, class_id, (x1, y1, x2, y2))

      if class_id == 2:
        school_scores: dict[int, float] = {}
        competing_scores: dict[int, float] = {}
        school_best_confidences: dict[int, float] = {}
        school_support_counts: dict[int, int] = {}
        for expand_left, expand_top, expand_right, expand_bottom in SCHOOL_ZONE_DIRECT_EXPANSIONS:
          expanded_x1 = max(int(x1 - box_width * expand_left), 0)
          expanded_y1 = max(int(y1 - box_height * expand_top), 0)
          expanded_x2 = min(int(x2 + box_width * expand_right), frame_width)
          expanded_y2 = min(int(y2 + box_height * expand_bottom), frame_height)
          sign_crop = frame_bgr[expanded_y1:expanded_y2, expanded_x1:expanded_x2]
          if sign_crop.size == 0:
            continue

          for school_crop, crop_weight in self._iter_school_zone_read_crops(sign_crop):
            read_result = self._classify_speed_limit_from_model(school_crop)
            if read_result is None:
              continue

            speed_limit_mph, read_confidence = read_result
            if speed_limit_mph not in SCHOOL_ZONE_SPEED_VALUES:
              competing_scores[speed_limit_mph] = competing_scores.get(speed_limit_mph, 0.0) + read_confidence * crop_weight
              continue

            school_scores[speed_limit_mph] = school_scores.get(speed_limit_mph, 0.0) + read_confidence * crop_weight
            school_best_confidences[speed_limit_mph] = max(school_best_confidences.get(speed_limit_mph, 0.0), read_confidence)
            school_support_counts[speed_limit_mph] = school_support_counts.get(speed_limit_mph, 0) + 1

        if school_scores:
          speed_limit_mph = max(
            school_scores,
            key=lambda speed: (
              school_scores[speed] + max(school_support_counts[speed] - 1, 0) * SCHOOL_ZONE_SUPPORT_BONUS,
              school_best_confidences[speed],
            ),
          )
          read_confidence = school_best_confidences[speed_limit_mph]
          support_count = school_support_counts[speed_limit_mph]
          if school_scores[speed_limit_mph] > max(competing_scores.values(), default=0.0):
            if (
              (support_count >= SCHOOL_ZONE_MIN_SUPPORT and read_confidence >= SCHOOL_ZONE_MIN_CONFIDENCE) or
              read_confidence >= SCHOOL_ZONE_SINGLE_READ_CONFIDENCE
            ):
              score = min(
                read_confidence * 0.72 +
                proposal_confidence * 0.22 +
                max(support_count - 1, 0) * SCHOOL_ZONE_SUPPORT_BONUS +
                0.04,
                0.95,
              )
              if score >= SCHOOL_ZONE_SHORT_CIRCUIT_CONFIDENCE:
                self._remember_detector_proposal(
                  proposal_confidence, class_id, (x1, y1, x2, y2), speed_limit_mph, preferred=True,
                )
                return Detection(speed_limit_mph, score)

      speed_scores: dict[int, float] = {}
      speed_best_confidences: dict[int, float] = {}
      speed_support_counts: dict[int, int] = {}
      speed_regulatory_support: dict[int, int] = {}
      speed_trusted_model_support: dict[int, int] = {}
      speed_model_only_rescue_support: dict[int, int] = {}
      speed_direct_model_support: dict[int, int] = {}
      speed_strong_model_support: dict[int, int] = {}
      expanded_regulatory_speeds: set[int] = set()
      primary_reject_dominant = False

      expansions = getattr(self, "detector_classifier_expansions", DETECTOR_CLASSIFIER_EXPANSIONS)
      for expand_left, expand_top, expand_right, expand_bottom, expansion_weight in expansions:
        expanded_x1 = max(int(x1 - box_width * expand_left), 0)
        expanded_y1 = max(int(y1 - box_height * expand_top), 0)
        expanded_x2 = min(int(x2 + box_width * expand_right), frame_width)
        expanded_y2 = min(int(y2 + box_height * expand_bottom), frame_height)
        sign_crop = frame_bgr[expanded_y1:expanded_y2, expanded_x1:expanded_x2]
        if sign_crop.size == 0:
          continue

        raw_is_regulatory = self._is_regulatory_speed_sign(sign_crop)
        is_regulatory = raw_is_regulatory
        if class_id == 2:
          is_regulatory = True

        face_filter = getattr(self, '_speed_face_filter', None)
        if face_filter is not None and face_filter.reject_once((proposal_confidence, class_id, (x1, y1, x2, y2))):
          self.latest_detector_proposal = None
          return None
        model_read = self._classify_speed_limit_from_model(sign_crop)
        if not any((expand_left, expand_top, expand_right, expand_bottom)):
          primary_reject_dominant = bool(
            not raw_is_regulatory and getattr(self, "last_classifier_reject_dominant", False)
          )
        if (class_id == 0 and not raw_is_regulatory and model_read is None and
            getattr(self, "last_classifier_advisory_rejected", False)):
          # A confident advisory veto applies to this object, not another crop of it.
          speed_scores.clear()
          rejected_advisory_boxes.append((x1, y1, x2, y2))
          break
        trusted_model_read = (
          class_id == 0 and
          model_read is not None and
          x1 >= frame_width * DETECTOR_CLASSIFIER_TRUSTED_MODEL_MIN_X_RATIO and
          box_height <= DETECTOR_CLASSIFIER_TRUSTED_MODEL_MAX_HEIGHT and
          proposal_area_ratio <= DETECTOR_CLASSIFIER_TRUSTED_MODEL_MAX_AREA_RATIO and
          proposal_confidence >= DETECTOR_CLASSIFIER_TRUSTED_MODEL_MIN_PROPOSAL_CONFIDENCE and
          model_read[1] >= DETECTOR_CLASSIFIER_TRUSTED_MODEL_MIN_READ_CONFIDENCE
        )
        strong_model_read = (
          class_id == 0 and
          model_read is not None and
          not is_small_box and
          proposal_confidence >= DETECTOR_CLASSIFIER_STRONG_MODEL_MIN_PROPOSAL_CONFIDENCE and
          model_read[1] >= DETECTOR_CLASSIFIER_STRONG_MODEL_MIN_READ_CONFIDENCE
        )
        strong_model_consensus_read = (
          class_id == 0 and
          model_read is not None and
          not is_small_box and
          proposal_confidence >= DETECTOR_CLASSIFIER_STRONG_MODEL_MIN_PROPOSAL_CONFIDENCE and
          model_read[1] >= DETECTOR_CLASSIFIER_STRONG_MODEL_CONSENSUS_MIN_READ_CONFIDENCE
        )
        model_only_consensus_read = (
          class_id == 0 and
          model_read is not None and
          not is_small_box and
          model_read[1] >= DETECTOR_CLASSIFIER_MODEL_ONLY_CONSENSUS_MIN_CONFIDENCE and
          (is_regulatory or proposal_confidence >= DETECTOR_CLASSIFIER_STRONG_MODEL_MIN_PROPOSAL_CONFIDENCE)
        )
        needs_ocr_confirmation = (
          class_id != 2 and
          (not is_regulatory or is_tiny_low_conf_box) and
          not trusted_model_read and
          not strong_model_read
        )
        read_result = model_read
        if read_result is None:
          continue

        if needs_ocr_confirmation and not trusted_model_read and not strong_model_read and not model_only_consensus_read:
          continue

        speed_limit_mph, read_confidence = read_result
        score_is_regulatory = is_regulatory or trusted_model_read or strong_model_read
        if (
          class_id == 2 and
          proposal_confidence < SCHOOL_ZONE_FALLBACK_MIN_CONFIDENCE and
          not raw_is_regulatory
        ):
          continue

        score = read_confidence * expansion_weight
        if score_is_regulatory:
          score += DETECTOR_CLASSIFIER_REGULATORY_BONUS
        elif proposal_area_ratio >= DETECTOR_CLASSIFIER_SMALL_BOX_AREA_RATIO:
          score -= DETECTOR_CLASSIFIER_NON_REGULATORY_PENALTY
        if class_id == 2:
          score += SCHOOL_ZONE_SPEED_PRIOR if speed_limit_mph in SCHOOL_ZONE_SPEED_VALUES else -SCHOOL_ZONE_SPEED_PRIOR

        speed_scores[speed_limit_mph] = speed_scores.get(speed_limit_mph, 0.0) + score
        speed_best_confidences[speed_limit_mph] = max(speed_best_confidences.get(speed_limit_mph, 0.0), read_confidence)
        speed_support_counts[speed_limit_mph] = speed_support_counts.get(speed_limit_mph, 0) + 1
        if raw_is_regulatory and any((expand_left, expand_top, expand_right, expand_bottom)):
          expanded_regulatory_speeds.add(speed_limit_mph)
        if is_regulatory or class_id == 2 or strong_model_read:
          speed_regulatory_support[speed_limit_mph] = speed_regulatory_support.get(speed_limit_mph, 0) + 1
        if trusted_model_read:
          speed_trusted_model_support[speed_limit_mph] = speed_trusted_model_support.get(speed_limit_mph, 0) + 1
        if strong_model_consensus_read:
          speed_strong_model_support[speed_limit_mph] = speed_strong_model_support.get(speed_limit_mph, 0) + 1
        if needs_ocr_confirmation and model_only_consensus_read:
          speed_model_only_rescue_support[speed_limit_mph] = speed_model_only_rescue_support.get(speed_limit_mph, 0) + 1
        elif model_read is not None:
          speed_direct_model_support[speed_limit_mph] = speed_direct_model_support.get(speed_limit_mph, 0) + 1

      if not speed_scores:
        continue

      speed_limit_mph = max(
        speed_scores,
        key=lambda speed: (
          speed_scores[speed] + max(speed_support_counts[speed] - 1, 0) * DETECTOR_CLASSIFIER_SUPPORT_BONUS,
          speed_best_confidences[speed],
        ),
      )
      if class_id == 2 and speed_limit_mph not in SCHOOL_ZONE_SPEED_VALUES:
        continue
      model_only_rescue_support = speed_model_only_rescue_support.get(speed_limit_mph, 0)
      if (
        speed_direct_model_support.get(speed_limit_mph, 0) < 1 and
        0 < model_only_rescue_support < DETECTOR_CLASSIFIER_MODEL_ONLY_CONSENSUS_MIN_SUPPORT
      ):
        continue
      if (
        speed_regulatory_support.get(speed_limit_mph, 0) < 1 and
        0 < speed_trusted_model_support.get(speed_limit_mph, 0) < DETECTOR_CLASSIFIER_TRUSTED_MODEL_MIN_SUPPORT
      ):
        continue
      if class_id != 2 and speed_limit_mph in SCHOOL_ZONE_SPEED_VALUES:
        competing_speed_limit_mph = max(
          (speed for speed in speed_scores if speed not in SCHOOL_ZONE_SPEED_VALUES),
          key=lambda speed: (speed_best_confidences[speed], speed_scores[speed]),
          default=None,
        )
        if competing_speed_limit_mph is not None:
          read_confidence = speed_best_confidences[speed_limit_mph]
          competing_confidence = speed_best_confidences[competing_speed_limit_mph]
          if (
            competing_confidence >= NON_SCHOOL_LOW_SPEED_COMPETING_MIN_CONFIDENCE and
            competing_confidence >= read_confidence
          ):
            speed_limit_mph = competing_speed_limit_mph
      read_confidence = speed_best_confidences[speed_limit_mph]
      support_count = speed_support_counts[speed_limit_mph]
      strong_rescue = (
        DETECTOR_CLASSIFIER_STRONG_MODEL_CONSENSUS_ENABLED and
        speed_strong_model_support.get(speed_limit_mph, 0) >= DETECTOR_CLASSIFIER_STRONG_MODEL_CONSENSUS_MIN_SUPPORT
      )
      score = min(
        read_confidence * 0.72 +
        proposal_confidence * 0.24 +
        max(support_count - 1, 0) * DETECTOR_CLASSIFIER_SUPPORT_BONUS,
        0.95,
      )
      selection_score = score
      published_score = score
      if class_id == 2:
        if speed_limit_mph in SCHOOL_ZONE_SPEED_VALUES:
          selection_score = min(score + 0.06, 0.95)
          published_score = selection_score
        else:
          selection_score = max(score - 0.06, 0.0)
          published_score = selection_score
      elif is_small_box:
        if (
          speed_regulatory_support.get(speed_limit_mph, 0) < 1 and
          speed_trusted_model_support.get(speed_limit_mph, 0) < DETECTOR_CLASSIFIER_TRUSTED_MODEL_MIN_SUPPORT
        ):
          continue
        if support_count < DETECTOR_CLASSIFIER_RESCUE_MIN_SUPPORT:
          continue
        if read_confidence < DETECTOR_CLASSIFIER_RESCUE_MIN_CONFIDENCE:
          continue
        strong_rescue = strong_rescue or (
          speed_trusted_model_support.get(speed_limit_mph, 0) >= DETECTOR_CLASSIFIER_STRONG_RESCUE_MIN_SUPPORT and
          proposal_confidence >= DETECTOR_CLASSIFIER_STRONG_RESCUE_MIN_PROPOSAL_CONFIDENCE and
          read_confidence >= DETECTOR_CLASSIFIER_STRONG_RESCUE_MIN_READ_CONFIDENCE
        )
        rescue_max_score = (
          DETECTOR_CLASSIFIER_STRONG_RESCUE_MAX_SCORE if strong_rescue else DETECTOR_CLASSIFIER_RESCUE_MAX_SCORE
        )
        published_score = min(score, rescue_max_score)
        if speed_trusted_model_support.get(speed_limit_mph, 0) < DETECTOR_CLASSIFIER_TRUSTED_MODEL_MIN_SUPPORT:
          selection_score = published_score
      if selection_score > best_score:
        best_score = selection_score
        best_detection = Detection(speed_limit_mph, published_score, strong_rescue)
        best_retry_conflict = (
          class_id == 0 and primary_reject_dominant and speed_limit_mph in expanded_regulatory_speeds and
          speed_support_counts[speed_limit_mph] < DETECTOR_CLASSIFIER_MODEL_ONLY_CONSENSUS_MIN_SUPPORT
        )
        self._remember_detector_proposal(
          proposal_confidence, class_id, (x1, y1, x2, y2), speed_limit_mph, preferred=True,
        )
      if best_detection is not None and best_detection.confidence >= MODEL_DETECTION_SHORT_CIRCUIT_CONFIDENCE:
        return None if best_retry_conflict else best_detection

    return None if best_retry_conflict else best_detection


  def _prune_history(self, now):
    while self.history and now - self.history[0].created_at > HISTORY_SECONDS:
      self.history.popleft()


  def _confirm_detection(self):
    if not self.history:
      return None

    counts = Counter(entry.speed_limit_mph for entry in self.history)
    candidate_speed_limit, candidate_count = counts.most_common(1)[0]
    matching_entries = [entry for entry in self.history if entry.speed_limit_mph == candidate_speed_limit]
    matching_confidences = sorted((entry.confidence for entry in matching_entries), reverse=True)
    best_confidence = max(entry.confidence for entry in matching_entries)
    has_strong_consensus = any(entry.strong_consensus for entry in matching_entries)
    current_speed_limit = self.published_speed_limit_mph
    current_count = counts.get(current_speed_limit, 0) if current_speed_limit > 0 else 0

    if current_speed_limit > 0 and candidate_speed_limit != current_speed_limit:
      required_count = CHANGE_CONSISTENT_DETECTIONS
      allow_single_frame_confirmation = (
        has_strong_consensus or best_confidence >= CHANGE_SINGLE_READ_MIN_CONFIDENCE
      )
      if current_speed_limit >= 30 and candidate_speed_limit < 30:
        required_count = LOW_SPEED_CHANGE_CONSISTENT_DETECTIONS
        allow_single_frame_confirmation = (
          best_confidence >= CHANGE_SINGLE_READ_MIN_CONFIDENCE or
          (has_strong_consensus and LOW_SPEED_CHANGE_ALLOW_STRONG_CONSENSUS)
        )
        if best_confidence < LOW_SPEED_CHANGE_MIN_CONFIDENCE:
          return None
      if candidate_count < required_count and not allow_single_frame_confirmation:
        return None
      if (
        not allow_single_frame_confirmation and
        matching_confidences[required_count - 1] < CHANGE_REPEAT_MIN_CONFIDENCE
      ):
        return None
      if candidate_count <= current_count:
        return None
      return candidate_speed_limit, best_confidence

    if has_strong_consensus or best_confidence >= STRONG_DETECTION_CONFIDENCE or candidate_count >= CONSISTENT_DETECTIONS:
      return candidate_speed_limit, best_confidence
    return None
