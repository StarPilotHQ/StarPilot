"""Synthetic net fixture; real ONNX parsing/inference is forbidden."""

from pathlib import Path
import numpy as np
from openpilot.starpilot.speed_limits.vision import speed_face

COUNTS = {'loads': 0, 'forwards': 0}
LOADS = []


class Net:
  def __init__(self, kind):
    self.kind = kind
    self.stop_hits = False
    self.logit = 8.0

  def setPreferableBackend(self, *args):
    pass

  def setPreferableTarget(self, *args):
    pass

  def enableFusion(self, *args):
    pass

  def getLayerId(self, *args):
    return 1

  def getUnconnectedOutLayersNames(self):
    return ['object_features', 'speed_face_logit', 'stop_face_logit']

  def setInput(self, blob):
    self.blob = blob

  def forward(self, names=None):
    COUNTS['forwards'] += 1
    if self.kind == 'aux':
      return [np.array([[self.logit]], np.float32)]
    if self.kind == 'reader':
      probabilities = np.full((1, 19), 1 / 19, np.float32)
      logits = np.zeros((1, 19), np.float32)
      return [probabilities, logits] if names is not None else probabilities
    speed = np.zeros((1, 5, 1344), np.float32)
    if self.kind == 'speed':
      return speed
    stops = np.zeros((1, 5, 4096), np.float32)
    if self.stop_hits:
      stops[0, :, 0] = [128, 128, 20, 20, 0.9]
    return [stops] if self.kind == 'stop' else (speed, stops)


def load(path):
  name = Path(path).name
  known = {
    'traffic_sign_identity.onnx': 'aux',
    'traffic_signs_us_detector.onnx': 'joint',
    'traffic_signs_us_stop_detector.onnx': 'stop',
    'speed_limit_us_detector.onnx': 'speed',
    'speed_limit_us_value_classifier.onnx': 'reader',
  }
  if name not in known:
    raise AssertionError('Unqualified synthetic model request: ' + name)
  COUNTS['loads'] += 1
  LOADS.append(known[name])
  return Net(known[name])


def fake_identity():
  return speed_face.SharedSpeedFaceVerifier(Net('aux'))
