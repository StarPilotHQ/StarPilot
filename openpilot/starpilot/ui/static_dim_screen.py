"""Burn In Protection on the real screen: reads the finished frame for the detector and draws its dimming on top.

Each frame, after everything else is drawn, `StaticDimmer.draw` runs on the UI thread:
  * a few times a second it copies the frame off the screen (before any dimming is drawn, so the detector
    always sees the true picture) and hands it to a background thread, where all the detector's work happens;
  * when that thread has a new result it is uploaded as one texture, black with a per-pixel opacity;
  * the texture is drawn over the whole frame. Black at opacity a leaves a pixel at (1 - a) of its brightness.

A touch, or an alert that needs the driver, brings everything back to full brightness at once.
"""

from __future__ import annotations

import ctypes
import os
import random
import threading
import time

import numpy as np

from openpilot.common.realtime import drop_realtime
from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.ui.raylib import native_library_path, rl
from openpilot.starpilot.ui.static_dim import SAMPLE_HZ, StaticPixelDetector

MAX_PIXELS = 400_000        # comma 4 only for now: a bigger screen is left alone (copying it whole costs too much)
RESTART_GAP_S = 1.0         # no frames for this long (screen was off): start over
REPORT_S = 60.0             # how often a line goes to the log while it runs
DEFAULT_STRENGTH = 40       # percent of brightness taken off a fully dimmed pixel
# Write this file on the device to save what the screen showed before and after dimming (see _capture).
CAPTURE_REQUEST = "/tmp/static_dim_capture"
CAPTURE_DIR = "/tmp/static_dim"


class ScreenReader:
  """Copies the frame being drawn out of the graphics library. Rows come back top first, 4 bytes a pixel."""

  def __init__(self):
    library = ctypes.CDLL(native_library_path)
    self._read = library.rlReadScreenPixels
    self._read.restype = ctypes.c_void_p
    self._read.argtypes = (ctypes.c_int, ctypes.c_int)
    self._free = library.MemFree
    self._free.restype = None
    self._free.argtypes = (ctypes.c_void_p,)
    self._width = library.rlGetFramebufferWidth
    self._width.restype = ctypes.c_int
    self._height = library.rlGetFramebufferHeight
    self._height.restype = ctypes.c_int

  def size(self) -> tuple[int, int]:
    return int(self._width()), int(self._height())

  def read(self, out: np.ndarray) -> bool:
    rl.rl_draw_render_batch_active()   # everything queued so far must reach the frame first
    height, width = out.shape[:2]
    pointer = self._read(width, height)
    if not pointer:
      return False
    try:
      ctypes.memmove(out.ctypes.data, pointer, out.nbytes)
    finally:
      self._free(pointer)
    return True


class StaticDimmer:
  def __init__(self, reader_factory=ScreenReader, clock=time.monotonic):
    self._reader_factory = reader_factory
    self._clock = clock
    self._reader = None
    self.enabled = False
    self.strength = DEFAULT_STRENGTH / 100
    self.failed = ""                 # why it switched itself off, if it did
    self._detector: StaticPixelDetector | None = None
    self._frame: np.ndarray | None = None
    self._texture = None
    self._texture_size = (0, 0)
    self._pixels: np.ndarray | None = None   # what the texture holds: black, opacity in the fourth byte
    self._visible = False
    self._last_draw = 0.0
    self._next_look = 0.0
    self._generation = 0             # bumped by wake and restart: results from before are dropped
    self._lock = threading.Lock()
    self._result: tuple[int, np.ndarray] | None = None
    self._job = threading.Event()
    self._idle = threading.Event()
    self._idle.set()
    self._thread: threading.Thread | None = None
    self._wake_request = False
    self._redo_mask = False
    self._report_at = 0.0
    self._read_ms = self._work_ms = 0.0
    self._looks = 0
    self._work_looks = 0
    self.stats: dict = {}

  # ---- called from the UI thread -------------------------------------------------------------------------------

  def configure(self, enabled: bool, strength_percent: int = DEFAULT_STRENGTH) -> None:
    strength = max(0.0, min(1.0, strength_percent / 100))
    if strength != self.strength:
      self.strength = strength
      self._redo_mask = True
    enabled = bool(enabled) and not self.failed
    if enabled != self.enabled:
      self.enabled = enabled
      self._restart()

  def wake(self) -> None:
    """Full brightness now; dimming starts from scratch."""
    if self.enabled:
      self._generation += 1
      self._visible = False
      self._wake_request = True

  def _restart(self) -> None:
    self._generation += 1
    self._visible = False
    self._wake_request = True
    self._next_look = 0.0

  def _fail(self, reason: str) -> None:
    self.failed = reason
    self.enabled = False
    self._visible = False
    cloudlog.error(f"static dim switched off: {reason}")

  def draw(self, width: float, height: float) -> None:
    """Call once a frame after everything else is drawn. `width` x `height` is the frame in drawing units."""
    if not self.enabled:
      return
    try:
      self._draw(width, height)
    except Exception as error:   # a display nicety must never take the UI down
      self._fail(f"{type(error).__name__}: {error}")

  def _draw(self, width: float, height: float) -> None:
    now = self._clock()
    if now - self._last_draw > RESTART_GAP_S:
      self._restart()
    self._last_draw = now

    if self._reader is None:
      self._reader = self._reader_factory()
    frame_width, frame_height = self._reader.size()
    if frame_width <= 0 or frame_height <= 0 or frame_width * frame_height > MAX_PIXELS:
      self._fail(f"screen of {frame_width}x{frame_height} is not supported")
      return
    if abs(frame_width * height - frame_height * width) > 0.02 * frame_height * width:
      self._fail(f"frame is {frame_width}x{frame_height} but the picture is drawn {width:.0f}x{height:.0f}")
      return

    self._take_result()
    capture = False
    if now >= self._next_look and self._idle.is_set():
      if self._detector is None or (self._detector.width, self._detector.height) != (frame_width, frame_height):
        self._detector = StaticPixelDetector(frame_width, frame_height)
        self._frame = np.zeros((frame_height, frame_width, 4), np.uint8)
        self._visible = False
        self._generation += 1
      started = time.perf_counter()
      if not self._reader.read(self._frame):
        self._fail("the frame could not be read")
        return
      self._read_ms += (time.perf_counter() - started) * 1000
      self._looks += 1
      # Uneven spacing, so something that repeats in step with the looks (lane markings passing) is not seen as standing still.
      self._next_look = max(self._next_look, now - 0.5 / SAMPLE_HZ) + random.uniform(0.7, 1.3) / SAMPLE_HZ
      capture = os.path.exists(CAPTURE_REQUEST)
      if capture:
        self._capture("before", self._frame)
      self._idle.clear()
      if self._thread is None or not self._thread.is_alive():
        self._thread = threading.Thread(target=self._work, name="static_dim", daemon=True)
        self._thread.start()
      self._job.set()

    if self._visible and self._texture is not None:
      source = rl.Rectangle(0, 0, self._texture_size[0], self._texture_size[1])
      rl.draw_texture_pro(self._texture, source, rl.Rectangle(0, 0, width, height), rl.Vector2(0, 0), 0.0, rl.WHITE)
    if capture:
      shown = np.zeros_like(self._frame)
      if self._reader.read(shown):
        self._capture("after", shown)
      if self._pixels is not None and self._visible:
        self._capture("mask", self._pixels)
      try:
        os.remove(CAPTURE_REQUEST)
      except OSError:
        pass
    self._report(now)

  def _take_result(self) -> None:
    with self._lock:
      result, self._result = self._result, None
    if result is None or result[0] != self._generation:
      return
    alpha = result[1]
    size = (alpha.shape[1], alpha.shape[0])
    if self._pixels is None or self._texture_size != size:
      self._unload()
      self._pixels = np.zeros((size[1], size[0], 4), np.uint8)
      self._texture = rl.load_texture_from_image(rl.Image(None, size[0], size[1], 1, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8))
      self._texture_size = size
    self._pixels[..., 3] = alpha
    rl.update_texture(self._texture, self._pixels)
    self._visible = bool(alpha.any())

  def _unload(self) -> None:
    if self._texture is not None and rl.is_window_ready():
      rl.unload_texture(self._texture)
    self._texture = None
    self._texture_size = (0, 0)

  def close(self) -> None:
    self.enabled = False
    self._visible = False
    self._unload()

  def _capture(self, name: str, pixels: np.ndarray) -> None:
    """Save a frame for checking on a computer: raw bytes plus its size in the file name."""
    try:
      os.makedirs(CAPTURE_DIR, exist_ok=True)
      height, width = pixels.shape[:2]
      with open(os.path.join(CAPTURE_DIR, f"{name}-{width}x{height}.rgba"), "wb") as stream:
        stream.write(pixels.tobytes())
    except OSError:
      pass

  def _report(self, now: float) -> None:
    if now < self._report_at:
      return
    if self._report_at and self._looks:
      detector = self._detector
      self.stats = {
        "dimmed_share": round(detector.coverage(), 3) if detector else 0.0,
        "whole_picture": round(detector.veil, 2) if detector else 0.0,
        "motion": round(detector.motion, 3) if detector else 0.0,
        "strength": round(self.strength, 2),
        "read_ms": round(self._read_ms / self._looks, 2),
        "work_ms": round(self._work_ms / max(1, self._work_looks), 2),
        "looks": self._looks,
      }
      cloudlog.event("static_dim", **self.stats)
    self._read_ms = self._work_ms = 0.0
    self._looks = self._work_looks = 0
    self._report_at = now + REPORT_S

  # ---- the background thread ------------------------------------------------------------------------------------

  def _work(self) -> None:
    try:
      drop_realtime()   # the UI thread may be real-time; this work must never be
    except OSError:
      pass
    while True:
      self._job.wait()
      self._job.clear()
      try:
        generation = self._generation
        detector, frame = self._detector, self._frame
        if detector is not None and frame is not None:
          started = time.perf_counter()
          redo = self._redo_mask
          self._redo_mask = False
          if self._wake_request:
            self._wake_request = False
            detector.wake()
            redo = True
          if detector.sample(frame) or redo:
            alpha = detector.mask(self.strength)
            with self._lock:
              self._result = (generation, alpha)
          self._work_ms += (time.perf_counter() - started) * 1000
          self._work_looks += 1
      except Exception as error:
        self.failed = f"{type(error).__name__}: {error}"
        self.enabled = False
        self._visible = False
        cloudlog.error(f"static dim switched off: {self.failed}")
      finally:
        self._idle.set()


static_dimmer = StaticDimmer()
