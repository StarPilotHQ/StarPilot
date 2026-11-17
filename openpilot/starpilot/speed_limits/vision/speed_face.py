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


def warning_context_box(rgb256, box):
  from openpilot.starpilot.speed_limits.vision import model

  image = np.rint(rgb256.transpose(1, 2, 0) * np.float32(255)).astype(np.uint8)
  hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
  hue, saturation, value = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
  mask = ((hue >= model.REGULATORY_YELLOW_HUE_MIN) & (hue <= model.REGULATORY_YELLOW_HUE_MAX) &
          (saturation >= model.REGULATORY_YELLOW_SAT_MIN) & (value >= model.REGULATORY_YELLOW_VALUE_MIN)).astype(np.uint8)
  contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
  center = (float((box[0] + box[2]) / 2), float((box[1] + box[3]) / 2))
  matches = []
  for contour in contours:
    hull = cv2.convexHull(contour)
    if (hull <= 0).any() or (hull >= 255).any():
      continue
    quad = cv2.approxPolyDP(hull, .02 * cv2.arcLength(hull, True), True)
    if len(quad) != 4 or not cv2.isContourConvex(quad):
      continue
    points = quad.reshape(4, 2)
    top, right = points[np.argmin(points[:, 1])], points[np.argmax(points[:, 0])]
    bottom, left = points[np.argmax(points[:, 1])], points[np.argmin(points[:, 0])]
    if (len({tuple(p) for p in (top, right, bottom, left)}) != 4 or
        not (top[1] < min(left[1], right[1]) and bottom[1] > max(left[1], right[1]) and
             left[0] < min(top[0], bottom[0]) and right[0] > max(top[0], bottom[0]))):
      continue
    x, y, width, height = cv2.boundingRect(hull)
    if (cv2.pointPolygonTest(quad, center, False) >= 0 and
        x < box[0] and y < box[1] and x + width > box[2] and y + height > box[3]):
      outline = cv2.approxPolyDP(contour, .02 * cv2.arcLength(contour, True), True)
      matches.append(np.asarray([x, y, x + width, y + height], np.float32)
                     if len(outline) == 4 and cv2.isContourConvex(outline) else None)
  return matches[0] if len(matches) == 1 else None


def inset_context_box(rgb256, box):
  image = np.rint(rgb256.transpose(1, 2, 0) * np.float32(255)).astype(np.uint8)
  edges = cv2.Canny(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), 30, 90)
  edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
  contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
  width, height = float(box[2] - box[0]), float(box[3] - box[1])
  matches = []
  for contour in contours:
    x, y, w, h = cv2.boundingRect(contour)
    if (x <= box[0] + 2 and y <= box[1] - height * .5 and
        x + w >= box[2] - 2 and y + h >= box[3] - 2 and
        w >= width * 1.5 and h >= height * 1.5 and
        width * height * 3 <= w * h <= min(width * height * 12, 256 * 256 * .18)):
      matches.append((w * h, contour, np.asarray([x, y, x + w, y + h], np.float32)))
  if not matches:
    return None
  _, contour, parent = min(matches, key=lambda item: item[0])
  points = contour
  if parent[2] == 256:
    # Close only the board edge clipped by the camera image.
    points = np.concatenate((points, np.asarray([[[255, int(parent[1])]], [[255, int(parent[3]) - 1]]], np.int32)))
  hull = cv2.convexHull(points)
  quad = cv2.approxPolyDP(hull, .02 * cv2.arcLength(hull, True), True)
  if len(quad) != 4 or cv2.contourArea(hull) < .75 * (parent[2] - parent[0]) * (parent[3] - parent[1]):
    return None
  return parent


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
    self.last_identity_logit: float | None = None
    self.last_verified_candidate = None
    self.last_isolated_face = False

  def end_frame(self):
    self.last_identity_logit = None
    self.last_verified_candidate = None
    self.last_isolated_face = False
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
    context_box = warning_context_box(blob, box)
    inset = None
    if context_box is None:
      inset = inset_context_box(blob, box)
      context_box = inset
    if context_box is not None:
      patch = crop(np, cv2, blob, context_box)
    started = time.monotonic()
    self.last_forward_count += 1
    try:
      self.net.setInput(patch[None])
      output = self.net.forward(['speed_face_logit'])[0]
      if output.shape != (1, 1) or output.dtype != np.float32 or not np.isfinite(output).all():
        raise ValueError('Invalid speed-face output')
      self.last_identity_logit = float(output[0, 0])
      self.last_verified_candidate = candidate
      self.last_isolated_face = context_box is None
      self.last_rejected = bool(output[0, 0] < (0.0 if inset is not None else KEEP_LOGIT))
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
