"""Exact-asset CPU V-ASM inference from an explicit local NV12 frame."""

import hashlib
import os
from pathlib import Path
import stat

import numpy as np

from openpilot.starpilot.spot_monitor.policy import (
  Annotation, MODEL_INPUT_SIZE, class_one_confidence, crop_for_side, polygon_pixels,
)


# Frozen tri-class artifact. Embedded metadata names class 1 as 1_car and
# states AGPL-3.0/Ultralytics; the bundled bytes must match this identity.
MODEL_SIZE_BYTES = 6_166_229
MODEL_SHA256 = "5d20cdbb457ba18db51a537ee2e305bbe442264b1613956068d473e35d15900d"
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[3] / "starpilot/assets/vision_models/v_asm_model.onnx"


def _verified_model_bytes(path: Path) -> bytes:
  fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
  with os.fdopen(fd, "rb") as source:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size != MODEL_SIZE_BYTES:
      raise ValueError("Unsupported V-ASM model size or file type")
    raw = source.read(MODEL_SIZE_BYTES + 1)
    if len(raw) != MODEL_SIZE_BYTES or hashlib.sha256(raw).hexdigest() != MODEL_SHA256:
      raise ValueError("V-ASM model hash changed")
    return raw


def prepare_nv12(frame: np.ndarray, *, width: int, height: int, annotation: Annotation, side: str, cv2_module) -> np.ndarray:
  crop = crop_for_side(annotation, side, width, height)
  if crop is None or type(frame) is not np.ndarray or frame.dtype != np.uint8 or frame.ndim != 2 or \
     frame.shape[0] != height * 3 // 2 or frame.shape[1] < width:
    raise ValueError("Unsupported NV12 frame or unconfigured polygon")
  x, y, w, h = crop.x, crop.y, crop.width, crop.height
  nv12 = np.vstack((frame[y:y + h, x:x + w],
                    frame[height + y // 2:height + (y + h) // 2, x:x + w]))
  rgb = cv2_module.cvtColor(nv12, cv2_module.COLOR_YUV2RGB_NV12)
  if rgb.shape != (h, w, 3) or rgb.dtype != np.uint8:
    raise ValueError("Invalid RGB crop")
  mask = np.zeros((h, w), dtype=np.uint8)
  points = np.array([(px - x, py - y) for px, py in polygon_pixels(annotation, side, width, height)], dtype=np.int32)
  cv2_module.fillPoly(mask, [points], 255)
  rgb = cv2_module.bitwise_and(rgb, rgb, mask=mask)
  resized = cv2_module.resize(rgb, (crop.model_width, crop.model_height), interpolation=cv2_module.INTER_LINEAR)
  if resized.shape != (crop.model_height, crop.model_width, 3) or resized.dtype != np.uint8:
    raise ValueError("Invalid resized crop")
  square = np.zeros((MODEL_INPUT_SIZE, MODEL_INPUT_SIZE, 3), dtype=np.uint8)
  square[crop.pad_top:crop.pad_top + crop.model_height, crop.pad_left:crop.pad_left + crop.model_width] = resized
  return np.expand_dims(np.transpose(square.astype(np.float32) / 255.0, (2, 0, 1)), 0)


class VASMInference:
  def __init__(self, model_path: Path, *, cv2_module=None):
    self.model_path = model_path
    self.cv2 = cv2_module
    self.net = None
    self.loaded_sha256: str | None = None
    self.last_error = ""

  def load(self) -> bool:
    self.net = None
    self.loaded_sha256 = None
    try:
      raw = _verified_model_bytes(self.model_path)
      if self.cv2 is None:
        import cv2  # optional runtime dependency; no model load on import
        self.cv2 = cv2
      self.cv2.setNumThreads(1)
      # Select OpenCV's in-memory buffer overload explicitly; passing Python
      # bytes directly can crash in the native binding.
      net = self.cv2.dnn.readNetFromONNX(np.frombuffer(raw, dtype=np.uint8))
      net.setPreferableBackend(self.cv2.dnn.DNN_BACKEND_OPENCV)
      net.setPreferableTarget(self.cv2.dnn.DNN_TARGET_CPU)
      self.net = net
      self.loaded_sha256 = MODEL_SHA256
      self.last_error = ""
      return True
    except Exception as error:  # OpenCV raises its own extension exception type.
      self.last_error = f"V-ASM model unavailable: {error}"
      return False

  def infer_nv12(self, frame: np.ndarray, *, width: int, height: int, annotation: Annotation, side: str) -> float | None:
    if self.net is None:
      return None
    try:
      # The network was constructed from verified bytes, not a pathname.
      # Later file changes cannot alter this loaded network; reloading
      # requires a new exact-hash verification in load().
      blob = prepare_nv12(frame, width=width, height=height, annotation=annotation, side=side, cv2_module=self.cv2)
      self.net.setInput(blob)
      output = np.asarray(self.net.forward())
      if output.shape != (1, 3) or not np.isfinite(output).all():
        raise ValueError("Unexpected tri-class V-ASM output")
      scores = output[0].tolist()
      if any(not 0.0 <= score <= 1.0 for score in scores):
        raise ValueError("Invalid V-ASM class scores")
      return class_one_confidence(scores)
    except Exception as error:  # OpenCV raises its own extension exception type.
      self.net = None
      self.last_error = f"V-ASM inference unavailable: {error}"
      return None
