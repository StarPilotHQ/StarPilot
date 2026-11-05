"""Joint speed/STOP model; the existing speed producer retains its frozen core."""
from dataclasses import dataclass

import numpy as np

from openpilot.starpilot.speed_limits.vision import model
from openpilot.starpilot.speed_limits.vision.stop_signs import StopDetection, SignDetectorOutputs, decode_stop_signs


@dataclass(frozen=True)
class SignDetections:
  speed: model.Detection | None
  stops: tuple[StopDetection, ...]


class TrafficSignModelCore(model.VisionModelCore):
  DETECTOR_SHA256 = 'ac735518a3d32108dee9a112ec2f740c2ed573daac22798525ecbbbbc0ed6606'
  MODEL_ID = DETECTOR_SHA256 + ':' + model.VisionModelCore.CLASSIFIER_SHA256
  STOP_THRESHOLD = .6

  def __init__(self, *, is_metric: bool, speed_enabled: bool = True):
    self.stop_detections: tuple[StopDetection, ...] = ()
    self.speed_enabled = speed_enabled
    super().__init__(is_metric=is_metric, load_classifier=speed_enabled)

  def set_speed_enabled(self, enabled: bool) -> None:
    if enabled == self.speed_enabled:
      return
    self.classifier_net = (self._load_net('speed_limit_us_value_classifier.onnx', self.CLASSIFIER_SHA256)
                           if enabled else None)
    self.classifier_logits_layer = ('onnx_node!/model.10/linear/Gemm'
                                    if self.classifier_net is not None and self.classifier_net.getLayerId('onnx_node!advisory/AddReject') > 0 else None)
    if enabled:
      self._validate_model_abi()
    self.speed_enabled = enabled
    self.reset()

  @classmethod
  def _load_net(cls, name: str, expected_sha256: str):
    if name == 'speed_limit_us_detector.onnx':
      return SignDetectorOutputs(super()._load_net('traffic_signs_us_detector.onnx', expected_sha256))
    return super()._load_net(name, expected_sha256)

  def reset(self) -> None:
    super().reset()
    self.stop_detections = ()

  def infer(self, frame_bgr: np.ndarray) -> model.Detection | None:
    self.stop_detections = ()
    if not getattr(self, 'speed_enabled', True):
      if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3 or frame_bgr.size == 0:
        raise ValueError('Expected a nonempty BGR frame')
      self._collect_detector_classifier_proposals(frame_bgr)
      return None
    return super().infer(frame_bgr)

  def infer_signs(self, frame_bgr: np.ndarray) -> SignDetections:
    """Return both outputs for this frame; STOP boxes are unconfirmed proposals."""
    speed = self.infer(frame_bgr)
    return SignDetections(speed, self.stop_detections)

  def _collect_detector_classifier_proposals_from_region(self, frame_bgr, origin_x, origin_y,
                                                        full_frame_width, full_frame_height, min_confidence):
    if getattr(self, 'speed_enabled', True):
      speed = super()._collect_detector_classifier_proposals_from_region(
        frame_bgr, origin_x, origin_y, full_frame_width, full_frame_height, min_confidence)
    else:
      speed = []
      if self.net is not None:
        self._forward_detector_region(frame_bgr)
    if self.net is not None:
      height, width = frame_bgr.shape[:2]
      ratio = min(self.detector_input_size / height, self.detector_input_size / width)
      self.stop_detections += decode_stop_signs(
        self.net.stops, ratio=ratio,
        pad_width=(self.detector_input_size - round(width * ratio)) / 2,
        pad_height=(self.detector_input_size - round(height * ratio)) / 2,
        region_width=width, region_height=height, origin_x=origin_x, origin_y=origin_y,
        full_frame_width=full_frame_width, full_frame_height=full_frame_height, threshold=self.STOP_THRESHOLD)
    return speed
