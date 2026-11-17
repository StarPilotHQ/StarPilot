"""Bounded STOP detections from the detector's independent second output."""
from dataclasses import dataclass

import numpy as np

try:
  import cv2
except ImportError:
  cv2 = None


@dataclass(frozen=True)
class StopDetection:
  confidence: float
  bbox: tuple[int, int, int, int]


class SignDetectorOutputs:
  def __init__(self, net):
    self.net = net
    self.stops = None

  def setInput(self, blob):
    self.stops = None
    self.net.setInput(blob)

  def forward(self):
    self.stops = None
    speed, stops = self.net.forward(['output0', 'stop/output0'])
    if (speed.shape != (1, 5, 1344) or stops.shape != (1, 5, 4096) or
        not np.isfinite(speed).all() or not np.isfinite(stops).all()):
      raise ValueError('Unsupported traffic sign output shape or nonfinite output')
    self.stops = stops
    return speed


def decode_stop_signs(predictions, *, ratio, pad_width, pad_height, region_width, region_height,
                      origin_x, origin_y, full_frame_width, full_frame_height, threshold, accept_dense_id=None):
  if predictions is None or predictions.shape != (1, 5, 4096) or not np.isfinite(predictions).all():
    raise ValueError('Invalid STOP output')
  selected = np.flatnonzero(predictions[0, 4] >= threshold)
  selected = selected[np.argsort(-predictions[0, 4, selected], kind='stable')[:32]]
  boxes, scores, ids = [], [], []
  for index in selected:
    cx, cy, width, height = predictions[0, :4, index]
    x1 = max(int((cx - width / 2 - pad_width) / ratio), 0)
    y1 = max(int((cy - height / 2 - pad_height) / ratio), 0)
    x2 = min(int((cx + width / 2 - pad_width) / ratio), region_width)
    y2 = min(int((cy + height / 2 - pad_height) / ratio), region_height)
    if x2 - x1 < 10 or y2 - y1 < 10 or (x2 - x1) * (y2 - y1) > full_frame_width * full_frame_height * .18:
      continue
    boxes.append([x1 + origin_x, y1 + origin_y, x2 - x1, y2 - y1])
    scores.append(float(predictions[0, 4, index]))
    ids.append(int(index))
  if not boxes:
    return ()
  keep = np.asarray(cv2.dnn.NMSBoxes(boxes, scores, threshold, .4)).reshape(-1)
  retained = keep[:4]
  if accept_dense_id is not None and len({ids[i] for i in retained}) != len(retained):
    raise ValueError('Expected unique retained STOP dense IDs')
  return tuple(StopDetection(scores[i], (boxes[i][0], boxes[i][1], boxes[i][0] + boxes[i][2], boxes[i][1] + boxes[i][3]))
               for i in retained if accept_dense_id is None or accept_dense_id(ids[i]))


class CapturedSignDetectorOutputs(SignDetectorOutputs):
  rgb256 = None

  speed = None
  tiny_scores = None

  def forward(self):
    self.speed = self.stops = self.tiny_scores = None
    outputs = self.net.forward(['output0', 'stop/output0', 'tiny/scores'])
    if not isinstance(outputs, (tuple, list)) or len(outputs) != 3:
      raise ValueError('Expected speed, STOP and small-sign outputs')
    speed, stops, tiny = outputs
    if (speed.shape != (1, 5, 1344) or stops.shape != (1, 5, 4096) or tiny.shape != (1, 1, 1024) or
        any(value.dtype != np.float32 or not np.isfinite(value).all() for value in outputs) or
        not (0 <= tiny).all() or not (tiny <= 1).all()):
      raise ValueError('Invalid speed, STOP or small-sign output')
    self.speed, self.stops, self.tiny_scores = speed, stops, tiny
    return speed

  def setInput(self, blob):
    self.speed = self.tiny_scores = None
    self.rgb256 = None
    if blob.shape != (1, 3, 256, 256) or blob.dtype != np.float32 or (not np.isfinite(blob).all()) or (blob.min() < 0) or (blob.max() > 1):
      raise ValueError('Invalid owned current detector tensor')
    self.rgb256 = np.ascontiguousarray(blob[0]).copy()
    self.rgb256.flags.writeable = False
    return super().setInput(blob)


class CapturedStopDetectorOutputs(CapturedSignDetectorOutputs):
  def forward(self):
    raise ValueError('STOP-only detector has no Speed output')

  def forward_stops(self):
    self.stops = None
    outputs = self.net.forward(['stop/output0'])
    if not isinstance(outputs, (tuple, list)) or len(outputs) != 1:
      raise ValueError('Expected one STOP output')
    stops = outputs[0]
    if stops.shape != (1, 5, 4096) or stops.dtype != np.float32 or not np.isfinite(stops).all():
      raise ValueError('Unsupported STOP output shape or nonfinite output')
    self.stops = stops
    return stops
