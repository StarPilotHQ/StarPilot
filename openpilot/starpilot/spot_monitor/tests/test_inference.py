"""Exact-asset CPU wrapper with deterministic NV12/mask and fake-network tests."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from openpilot.starpilot.spot_monitor import inference
from openpilot.starpilot.spot_monitor.policy import decode_annotation


class FakeNet:
  def __init__(self):
    self.blob = None
    self.output = np.array([[0.05, 0.95, 0.0]], dtype=np.float32)
    self.backend = None
    self.target = None

  def setPreferableBackend(self, backend):
    self.backend = backend

  def setPreferableTarget(self, target):
    self.target = target

  def setInput(self, blob):
    self.blob = blob

  def forward(self):
    return self.output


class FakeDnn:
  DNN_BACKEND_OPENCV = 7
  DNN_TARGET_CPU = 8

  def __init__(self, net):
    self.net = net
    self.loaded = None

  def readNetFromONNX(self, raw):
    assert isinstance(raw, np.ndarray) and raw.dtype == np.uint8 and raw.ndim == 1
    self.loaded = raw
    return self.net


class FakeCV2:
  COLOR_YUV2RGB_NV12 = 1
  INTER_LINEAR = 2

  def __init__(self, net):
    self.dnn = FakeDnn(net)
    self.mask: np.ndarray | None = None
    self.threads = None

  def setNumThreads(self, count):
    self.threads = count

  def cvtColor(self, nv12, code):
    assert code == self.COLOR_YUV2RGB_NV12
    height = nv12.shape[0] * 2 // 3
    return np.repeat(nv12[:height, :, None], 3, axis=2)

  def fillPoly(self, mask, polygons, value):
    points = polygons[0]
    for y in range(mask.shape[0]):
      for x in range(mask.shape[1]):
        # A strict interior raster is enough to expose the wrapper's mask
        # application without requiring OpenCV in the host test environment.
        inside = False
        for a, b in zip(points, np.roll(points, -1, axis=0), strict=True):
          if (a[1] > y + 0.5) != (b[1] > y + 0.5):
            boundary = a[0] + (y + 0.5 - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x + 0.5 < boundary:
              inside = not inside
        if inside:
          mask[y, x] = value
    self.mask = mask.copy()

  def bitwise_and(self, left, right, *, mask):
    return np.where(mask[:, :, None] != 0, left, 0).astype(np.uint8)

  def resize(self, image, size, *, interpolation):
    assert interpolation == self.INTER_LINEAR
    width, height = size
    ys = (np.arange(height) * image.shape[0] // height).astype(int)
    xs = (np.arange(width) * image.shape[1] // width).astype(int)
    return image[np.ix_(ys, xs)]


@pytest.fixture
def tiny_model(tmp_path: Path, monkeypatch):
  raw = b"small reviewed ONNX fixture"
  monkeypatch.setattr(inference, "MODEL_SIZE_BYTES", len(raw))
  monkeypatch.setattr(inference, "MODEL_SHA256", hashlib.sha256(raw).hexdigest())
  path = tmp_path / "model.onnx"
  path.write_bytes(raw)
  return path


def test_exact_asset_cpu_mask_crop_class_one_and_loaded_bytes_pinned(tiny_model, monkeypatch):
  net = FakeNet()
  cv2 = FakeCV2(net)
  model = inference.VASMInference(tiny_model, cv2_module=cv2)
  assert model.load()
  assert cv2.threads == 1
  assert model.loaded_sha256 == inference.MODEL_SHA256
  assert cv2.dnn.loaded.tobytes() == tiny_model.read_bytes()
  assert (net.backend, net.target) == (cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU)
  annotation = decode_annotation(b'{"version":1,"width":32,"height":32,"poly_left":[[4,4],[20,4],[4,20]],"poly_right":[]}')
  frame = np.full((48, 32), 128, dtype=np.uint8)
  frame[:32, :] = 255
  assert model.infer_nv12(frame, width=32, height=32, annotation=annotation, side="left") == pytest.approx(0.95)
  assert cv2.mask is not None
  assert cv2.mask[2, 2] == 255
  assert cv2.mask[-2, -2] == 0
  assert net.blob.shape == (1, 3, 352, 352)
  assert net.blob.dtype == np.float32
  assert net.blob.max() == 1.0 and net.blob.min() == 0.0
  tiny_model.write_bytes(b"small tampered ONNX fixture")
  def fail_reopen(_):
    raise AssertionError("infer reopened model source")

  monkeypatch.setattr(inference, "_verified_model_bytes", fail_reopen)
  assert model.infer_nv12(frame, width=32, height=32, annotation=annotation, side="left") == pytest.approx(0.95)
  assert model.loaded_sha256 == inference.MODEL_SHA256


def test_changed_source_rejected_on_explicit_reload(tiny_model):
  model = inference.VASMInference(tiny_model, cv2_module=FakeCV2(FakeNet()))
  assert model.load()
  tiny_model.write_bytes(b"small tampered ONNX fixture")
  assert not model.load()
  assert model.net is None and model.loaded_sha256 is None


def test_bundled_model_loads_and_infers_with_real_opencv():
  pytest.importorskip("cv2")
  # Isolate the native binding: a regression here can segfault rather than
  # raise a Python exception, which the fake network cannot reproduce.
  result = subprocess.run([sys.executable, "-c", """
import numpy as np
from openpilot.starpilot.spot_monitor.inference import DEFAULT_MODEL_PATH, MODEL_SHA256, VASMInference
from openpilot.starpilot.spot_monitor.policy import decode_annotation
model = VASMInference(DEFAULT_MODEL_PATH)
assert model.load(), model.last_error
assert model.loaded_sha256 == MODEL_SHA256
annotation = decode_annotation(b'{"version":1,"width":32,"height":32,"poly_left":[[0,0],[32,0],[32,32],[0,32]],"poly_right":[]}')
score = model.infer_nv12(np.full((48, 32), 128, dtype=np.uint8), width=32, height=32,
                         annotation=annotation, side="left")
assert score is not None, model.last_error
assert 0 <= score <= 1
"""], capture_output=True, text=True, timeout=30)
  assert result.returncode == 0, f"OpenCV exited {result.returncode}: {result.stdout}{result.stderr}"


def test_asset_missing_wrong_size_symlink_and_bad_output_fail_unavailable(tiny_model, tmp_path):
  cv2 = FakeCV2(FakeNet())
  missing = inference.VASMInference(tmp_path / "missing.onnx", cv2_module=cv2)
  assert not missing.load()
  alias = tmp_path / "alias.onnx"
  alias.symlink_to(tiny_model)
  assert not inference.VASMInference(alias, cv2_module=cv2).load()
  tiny_model.write_bytes(b"wrong")
  assert not inference.VASMInference(tiny_model, cv2_module=cv2).load()
  tiny_model.write_bytes(b"small reviewed ONNX fixture")
  model = inference.VASMInference(tiny_model, cv2_module=cv2)
  assert model.load()
  frame = np.zeros((48, 32), dtype=np.uint8)
  annotation = decode_annotation(b'{"version":1,"width":32,"height":32,"poly_left":[[4,4],[20,4],[4,20]],"poly_right":[]}')
  cv2.dnn.net.output = np.array([[float("nan"), 0.95, 0.0]], dtype=np.float32)
  assert model.infer_nv12(frame, width=32, height=32, annotation=annotation, side="left") is None
  assert model.net is None


def test_fifo_asset_refused_without_waiting_for_writer(tmp_path):
  fifo = tmp_path / "model.fifo"
  os.mkfifo(fifo)
  assert not inference.VASMInference(fifo, cv2_module=FakeCV2(FakeNet())).load()


def test_wrong_frame_and_unconfigured_side_fail_without_model_output(tiny_model):
  net = FakeNet()
  model = inference.VASMInference(tiny_model, cv2_module=FakeCV2(net))
  assert model.load()
  annotation = decode_annotation(b'{"version":1,"width":32,"height":32,"poly_left":[[4,4],[20,4],[4,20]],"poly_right":[]}')
  assert model.infer_nv12(np.zeros((32, 32), dtype=np.uint8), width=32, height=32,
                          annotation=annotation, side="left") is None
  assert net.blob is None
