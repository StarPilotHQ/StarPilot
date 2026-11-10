"""A bounded, current-frame visual check before numeric sign reading."""
import hashlib
import math
import time

import numpy as np

try:
  import cv2
except ImportError:
  cv2 = None

MODEL_FILE = 'traffic_sign_identity.onnx'
MODEL_SHA256 = '4d6aba6ad2e6531752f838572e613d34a07dbc9c395ec5d23509ca17dcfb125d'
SIZE = 64
MIN_RESOLVED_EXTENT = 4.
KEEP_SCORE = .05
KEEP_LOGIT = math.log(KEEP_SCORE / (1 - KEEP_SCORE))


def bundle_model_id(detector_sha256, reader_sha256):
  for value in (detector_sha256, reader_sha256):
    if len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
      raise ValueError('Invalid vision asset identity')
  manifest = 'vision_detector_with_speed_stop_identity_v1\n' + detector_sha256 + '\n' + MODEL_SHA256 + '\n'
  return hashlib.sha256(manifest.encode()).hexdigest() + ':' + reader_sha256


def bounds(np, box):
  value = np.asarray(box, np.float32)
  if value.shape != (4,) or not np.isfinite(value).all() or not (value[2:] > value[:2]).all():
    raise ValueError('Invalid selected proposal box')
  clipped = np.clip(value, 0, 256)
  if not (clipped[2:] - clipped[:2] >= MIN_RESOLVED_EXTENT).all():
    return None
  lo = np.floor(clipped[:2]).astype(int)
  hi = np.ceil(clipped[2:]).astype(int)
  return lo, hi


def crop(np, cv2, rgb256, box):
  if (rgb256.shape != (3, 256, 256) or rgb256.dtype != np.float32 or not np.isfinite(rgb256).all() or
      rgb256.min() < 0 or rgb256.max() > 1):
    raise ValueError('Expected owned detector RGB256 float32 unit tensor')
  region = bounds(np, box)
  if region is None:
    return None
  lo, hi = region
  image = rgb256[:, lo[1]:hi[1], lo[0]:hi[0]].transpose(1, 2, 0)
  h, w = image.shape[:2]
  scale = SIZE / max(w, h)
  nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
  resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
  left, top = (SIZE - nw) // 2, (SIZE - nh) // 2
  result = np.full((SIZE, SIZE, 3), np.float32(114 / 255), np.float32)
  result[top:top + nh, left:left + nw] = resized
  return np.ascontiguousarray(np.clip(result, 0, 1).transpose(2, 0, 1))


class SpeedFaceVerifier:
  def __init__(self, path):
    self.net = None
    if cv2 is None:
      raise RuntimeError('OpenCV DNN is unavailable')
    with path.open('rb') as stream:
      if hashlib.file_digest(stream, 'sha256').hexdigest() != MODEL_SHA256:
        raise ValueError('Vision speed-face model identity mismatch')
    try:
      self.net = cv2.dnn.readNetFromONNX(str(path))
      self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
      self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
      self.net.enableFusion(True)
      if set(self.net.getUnconnectedOutLayersNames()) != {'object_features', 'speed_face_logit', 'stop_face_logit'}:
        raise ValueError('Unsupported speed-face model output names')
    except BaseException:
      self.net = None
      raise
    self.begin_frame()

  def begin_frame(self):
    self._proposals = {}
    self._blob = None
    self._regions = 0
    self._checked = False
    self.last_forward_count = 0
    self.last_forward_duration_s = 0.
    self.last_rejected = False
    self.last_dense_id = None

  def end_frame(self):
    self._blob = None
    self._proposals.clear()

  def close(self):
    self.end_frame()
    self.net = None

  def capture_blob(self, blob):
    if (blob.shape != (1, 3, 256, 256) or blob.dtype != np.float32 or not np.isfinite(blob).all() or
        blob.min() < 0 or blob.max() > 1 or self._regions >= 2):
      raise ValueError('Invalid current detector region')
    self._regions += 1
    self._blob = np.ascontiguousarray(blob[0]).copy()
    self._blob.flags.writeable = False

  def remember(self, candidate, raw_xywh, dense_id):
    if self._blob is None or not 0 <= int(dense_id) < 1344:
      raise ValueError('Missing detector region binding')
    raw = np.asarray(raw_xywh, np.float32)
    if raw.shape != (4,) or not np.isfinite(raw).all() or not (raw[2:] > 0).all():
      raise ValueError('Invalid detector proposal geometry')
    box = np.concatenate([raw[:2] - raw[2:] / 2, raw[:2] + raw[2:] / 2])
    self._proposals.setdefault(candidate, []).append((self._blob, box, int(dense_id)))

  def reject_once(self, candidate):
    if self._checked or candidate[1] != 0:
      return False
    self._checked = True
    matches = self._proposals.get(candidate)
    if not matches:
      raise ValueError('Missing current-frame speed proposal binding')
    blob, box, self.last_dense_id = matches[0]
    patch = crop(np, cv2, blob, box)
    if patch is None:
      return False
    started = time.monotonic()
    self.last_forward_count += 1
    try:
      self.net.setInput(patch[None])
      output = self.net.forward(['speed_face_logit'])[0]
      if output.shape != (1, 1) or output.dtype != np.float32 or not np.isfinite(output).all():
        raise ValueError('Invalid speed-face output')
      self.last_rejected = bool(output[0, 0] < KEEP_LOGIT)
      return self.last_rejected
    finally:
      self.last_forward_duration_s += time.monotonic() - started


def validate_logit(output):
  if not isinstance(output, np.ndarray) or output.shape != (1, 1) or output.dtype != np.float32 or (not np.isfinite(output).all()):
    raise ValueError('Invalid selected-object verifier logit; invalidate current observation')
  return float(output[0, 0])


def xyxy_from_raw(raw, index):
  if (not isinstance(raw, np.ndarray) or raw.shape != (1, 5, 4096) or raw.dtype != np.float32 or
      not np.isfinite(raw).all() or type(index) is not int or not 0 <= index < 4096):
    raise ValueError('Invalid original STOP anchor')
  box = raw[0, :4, index]
  if not (box[2:] > 0).all():
    raise ValueError('Invalid selected STOP anchor geometry')
  return np.concatenate([box[:2] - box[2:] / 2, box[:2] + box[2:] / 2])


class SharedSpeedFaceVerifier(SpeedFaceVerifier):

  def __init__(self, net):
    self.net = net
    self.begin_frame()


class StopFaceVerifier:

  def __init__(self, net):
    self.net = net
    self.begin_frame()

  def begin_frame(self):
    self.last_forward_count = 0
    self.last_forward_duration_s = 0.0
    self.last_rows = []

  def accept(self, rgb256, raw, dense_id):
    patch = crop(np, cv2, rgb256, xyxy_from_raw(raw, dense_id))
    if patch is None:
      self.last_rows.append({'dense_id': dense_id, 'unresolved_keep': True, 'logit': None, 'keep': True})
      return True
    started = time.monotonic()
    self.last_forward_count += 1
    try:
      self.net.setInput(patch[None])
      outputs = self.net.forward(['stop_face_logit'])
      if not isinstance(outputs, (tuple, list)) or len(outputs) != 1:
        raise ValueError('Expected one STOP output request')
      logit = validate_logit(outputs[0])
      keep = logit >= KEEP_LOGIT
      self.last_rows.append({'dense_id': dense_id, 'unresolved_keep': False, 'logit': logit, 'keep': keep})
      return keep
    finally:
      self.last_forward_duration_s += time.monotonic() - started
