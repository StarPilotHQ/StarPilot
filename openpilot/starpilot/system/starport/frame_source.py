"""Bounded shared-memory handoff of UI frames from the render thread to starportd.

One fixed ``/dev/shm`` file holds a small header and a single frame slot:

* The consumer (starportd) creates the file, writes the frame geometry it
  wants (the negotiated video size and the receiver's margins) and keeps
  refreshing a ``demand_until`` deadline while it streams. The producer knows
  the UI's own size and letterboxes it into the visible area (``fit_content``).
* The producer (the UI render thread) checks that deadline with one struct read
  per frame. Only while it is in the future, and at most at the requested rate,
  does it GPU-scale the UI into the requested geometry, read it back and publish
  it under a sequence lock (odd while writing).
* There is exactly one slot: an unconsumed frame is simply overwritten, so a
  slow encoder or head unit can never build a backlog or stall rendering.
* Frames are RGBA, or NV12 (the encoder's own layout, 1.5 bytes per pixel) when
  the consumer asks for it with ``FLAG_NV12`` and the producer can convert on
  the GPU. Each published frame records its format, so a producer that only
  does RGBA (the comma's own UI, for mirroring) still works.

Timestamps are ``time.monotonic_ns()`` (CLOCK_MONOTONIC), comparable across
processes on Linux. No locks are shared across processes; a torn read is
detected by the sequence number and retried by the consumer.
"""

from __future__ import annotations

import mmap
import os
import struct
import time
from dataclasses import dataclass

DEFAULT_PATH = "/dev/shm/starpilot_starport_frame"
MAGIC = 0x53464141  # "AAFS"
VERSION = 1
HEADER_SIZE = 4096
MAX_WIDTH, MAX_HEIGHT = 1920, 1080


def supported_size(width: int, height: int) -> bool:
  """Landscape up to 1920x1080, or the same bounds turned portrait."""
  return 0 < min(width, height) <= MAX_HEIGHT and 0 < max(width, height) <= MAX_WIDTH
FILE_SIZE = HEADER_SIZE + MAX_WIDTH * MAX_HEIGHT * 4

FORMAT_RGBA, FORMAT_NV12 = 0, 1
FLAG_CONTENT_ORIGIN = 4   # explicit content origin packed in the reserved header word
FLAG_NV12 = 1             # consumer: send NV12 if you can
FLAG_ASYNC_READBACK = 2   # consumer: read frames back without stalling on the GPU (one step later)

# magic, version, seq, width, height, captured_ns, frame_id, demand_until_ns,
# req_width, req_height, margin_w, margin_h, req_flags, frame_format, interval_us, reserved
_HEADER = struct.Struct("<IIQIIQQQIIIIIIII")
_SEQ_OFFSET = 8
_DEMAND_OFFSET = 40
_FORMAT_OFFSET = 68
_SEQ = struct.Struct("<Q")
_DEMAND = struct.Struct("<Q")
# Optional consumer heartbeat in unused header space; older consumers leave it zero.
_SENT_OFFSET = _HEADER.size
_SENT = struct.Struct("<Q")


def frame_bytes(width: int, height: int, pixel_format: int) -> int:
  return width * height * 3 // 2 if pixel_format == FORMAT_NV12 else width * height * 4


@dataclass(frozen=True)
class FrameRequest:
  width: int
  height: int
  margin_w: int
  margin_h: int
  interval_us: int
  flags: int = 0
  content_left: int | None = None
  content_top: int | None = None

  @property
  def left(self) -> int:
    return self.margin_w // 2 if self.content_left is None else self.content_left

  @property
  def top(self) -> int:
    return self.margin_h - self.margin_h // 2 if self.content_top is None else self.content_top

  def content(self, source_w: int, source_h: int) -> tuple[int, int, int, int]:
    x, y, w, h = fit_content(source_w, source_h, self.width, self.height, self.margin_w, self.margin_h)
    if self.content_left is not None:
      x = (self.left + (self.width - self.margin_w - w) // 2) & ~1
      y = (self.top + (self.height - self.margin_h - h) // 2) & ~1
    return x, y, w, h


@dataclass(frozen=True)
class Frame:
  data: bytes
  width: int
  height: int
  captured_ns: int
  frame_id: int
  pixel_format: int = FORMAT_RGBA


def fit_content(source_w: int, source_h: int, width: int, height: int, margin_w: int, margin_h: int) -> tuple[int, int, int, int]:
  """Largest undistorted rectangle for the source inside the receiver's visible area.

  Android Auto margins are split evenly on both sides of the encoded frame; the
  visible area is centered. Returns even-aligned ``(x, y, w, h)``.
  """
  visible_w, visible_h = width - margin_w, height - margin_h
  scale = min(visible_w / source_w, visible_h / source_h)
  w = max(2, int(source_w * scale) & ~1)
  h = max(2, int(source_h * scale) & ~1)
  x = ((width - w) // 2) & ~1
  y = ((height - h) // 2) & ~1
  return x, y, w, h


class FrameConsumer:
  """starportd side: owns the file, publishes demand, reads the latest frame."""

  def __init__(self, path: str = DEFAULT_PATH):
    self.path = path
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
      os.ftruncate(fd, FILE_SIZE)
      self.mm = mmap.mmap(fd, FILE_SIZE)
    finally:
      os.close(fd)
    self.request: FrameRequest | None = None
    self.last_key = (0, 0)
    self._write_header(0)

  def _write_header(self, demand_until_ns: int) -> None:
    r = self.request or FrameRequest(0, 0, 0, 0, 0)
    seq = _SEQ.unpack_from(self.mm, _SEQ_OFFSET)[0] if self.mm[:4] == struct.pack("<I", MAGIC) else 0
    explicit = r.content_left is not None and r.content_top is not None
    flags = r.flags | FLAG_CONTENT_ORIGIN if explicit else r.flags & ~FLAG_CONTENT_ORIGIN
    origin = r.left | (r.top << 16) if explicit else 0
    _HEADER.pack_into(self.mm, 0, MAGIC, VERSION, seq & ~1, 0, 0, 0, 0, demand_until_ns,
                      r.width, r.height, r.margin_w, r.margin_h, flags, 0, r.interval_us, origin)
    _SENT.pack_into(self.mm, _SENT_OFFSET, 0)

  def configure(self, request: FrameRequest) -> None:
    if not supported_size(request.width, request.height) or request.width % 2 or request.height % 2:
      raise ValueError("Unsupported frame size")
    if not (0 <= request.margin_w < request.width and 0 <= request.margin_h < request.height) or request.interval_us <= 0:
      raise ValueError("Unsupported frame geometry")
    if ((request.content_left is None) != (request.content_top is None) or
        not 0 <= request.left <= request.margin_w or not 0 <= request.top <= request.margin_h):
      raise ValueError("Unsupported content origin")
    self.request = request
    self._write_header(0)

  def demand(self, seconds: float = 1.0) -> None:
    _DEMAND.pack_into(self.mm, _DEMAND_OFFSET, time.monotonic_ns() + int(seconds * 1e9))

  def release_demand(self) -> None:
    _DEMAND.pack_into(self.mm, _DEMAND_OFFSET, 0)

  def mark_sent(self, captured_ns: int) -> None:
    """Record a real frame accepted by the projection connection, not just produced."""
    _SENT.pack_into(self.mm, _SENT_OFFSET, captured_ns)

  def latest(self) -> Frame | None:
    """Return a frame newer than the last one returned, or None."""
    for _ in range(3):
      seq1 = _SEQ.unpack_from(self.mm, _SEQ_OFFSET)[0]
      if seq1 & 1:
        time.sleep(0.001)
        continue
      fields = _HEADER.unpack_from(self.mm, 0)
      width, height, captured_ns, frame_id, pixel_format = fields[3], fields[4], fields[5], fields[6], fields[13]
      if fields[0] != MAGIC or frame_id == 0 or (frame_id, captured_ns) == self.last_key:
        return None
      request = self.request
      if request is None or (width, height) != (request.width, request.height) or pixel_format not in (FORMAT_RGBA, FORMAT_NV12):
        return None
      size = frame_bytes(width, height, pixel_format)
      data = self.mm[HEADER_SIZE:HEADER_SIZE + size]
      if _SEQ.unpack_from(self.mm, _SEQ_OFFSET)[0] != seq1:
        continue
      self.last_key = (frame_id, captured_ns)
      return Frame(data, width, height, captured_ns, frame_id, pixel_format)
    return None

  def close(self) -> None:
    try:
      self.release_demand()
      self.mm.close()
    finally:
      try:
        os.unlink(self.path)
      except FileNotFoundError:
        pass


class FrameProducer:
  """Render-thread side. Every method is cheap when nobody is projecting."""

  REOPEN_INTERVAL = 1.0

  def __init__(self, path: str = DEFAULT_PATH):
    self.path = path
    self.mm: mmap.mmap | None = None
    self._inode = 0
    self._next_open_check = 0.0
    self._next_capture_ns = 0
    self.frame_id = 0
    self.captures = 0

  def _ensure_open(self, now: float) -> bool:
    if now < self._next_open_check:
      return self.mm is not None
    self._next_open_check = now + self.REOPEN_INTERVAL
    try:
      st = os.stat(self.path)
    except OSError:
      self._close()
      return False
    if self.mm is not None and st.st_ino == self._inode:
      return True
    self._close()
    if st.st_size < FILE_SIZE:
      return False
    try:
      fd = os.open(self.path, os.O_RDWR)
      try:
        self.mm = mmap.mmap(fd, FILE_SIZE)
      finally:
        os.close(fd)
      self._inode = st.st_ino
    except OSError:
      self.mm = None
      return False
    return True

  def _close(self) -> None:
    if self.mm is not None:
      try:
        self.mm.close()
      except (BufferError, ValueError):
        pass
    self.mm = None

  def close(self) -> None:
    self._close()

  def pending_request(self, now: float | None = None, *, require_demand: bool = True) -> FrameRequest | None:
    """Requested geometry; startup may inspect it before display focus is granted."""
    now = time.monotonic() if now is None else now
    if not self._ensure_open(now):
      return None
    mm = self.mm
    assert mm is not None
    fields = _HEADER.unpack_from(mm, 0)
    if fields[0] != MAGIC or fields[1] != VERSION or (require_demand and fields[7] <= int(now * 1e9)):
      return None
    explicit = bool(fields[12] & FLAG_CONTENT_ORIGIN)
    request = FrameRequest(fields[8], fields[9], fields[10], fields[11], fields[14], fields[12] & ~FLAG_CONTENT_ORIGIN,
                           fields[15] & 0xffff if explicit else None, fields[15] >> 16 if explicit else None)
    if not supported_size(request.width, request.height) or request.interval_us <= 0 or \
       request.margin_w >= request.width or request.margin_h >= request.height or \
       not 0 <= request.left <= request.margin_w or not 0 <= request.top <= request.margin_h:
      return None
    return request

  def demand_active(self, now: float | None = None) -> bool:
    return self.pending_request(now) is not None

  def recently_sent(self, now: float | None = None) -> bool:
    """Cheap screen-sleep gate: focused demand and a frame sent within one second."""
    now = time.monotonic() if now is None else now
    if not self.demand_active(now):
      return False
    if self.mm is None:
      return False
    sent = _SENT.unpack_from(self.mm, _SENT_OFFSET)[0]
    return sent > 0 and 0 <= int(now * 1e9) - sent < 1_000_000_000

  def due(self, request: FrameRequest, now_ns: int) -> bool:
    return self.capture_delay(request, now_ns) == 0.0

  # ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: Capture slack and advance() preserve cadence without building a frame backlog.
  # Altering this can degrade video smoothness or introduce lag on Android Auto.
  # Do not change it autonomously; require an explicit user request specifically for this video optimization.
  def capture_delay(self, request: FrameRequest, now_ns: int) -> float:
    """Seconds until capture is due, with the same 25% pacing slack as due()."""
    return max(0, self._next_capture_ns - request.interval_us * 250 - now_ns) / 1e9

  def publish(self, request: FrameRequest, pixels, captured_ns: int, pixel_format: int = FORMAT_RGBA,
              advance: bool = True) -> None:
    """Copy one tightly packed top-down frame (RGBA or NV12) of the requested size.

    ``advance=False`` when the caller already called ``advance`` at capture
    time (a frame read back asynchronously is published one step later).
    """
    mm = self.mm
    size = frame_bytes(request.width, request.height, pixel_format)
    if mm is None or len(pixels) < size:
      return
    seq = _SEQ.unpack_from(mm, _SEQ_OFFSET)[0]
    _SEQ.pack_into(mm, _SEQ_OFFSET, seq | 1)
    mm[HEADER_SIZE:HEADER_SIZE + size] = memoryview(pixels)[:size]
    self.frame_id += 1
    struct.pack_into("<IIQQ", mm, 16, request.width, request.height, captured_ns, self.frame_id)
    struct.pack_into("<I", mm, _FORMAT_OFFSET, pixel_format)
    _SEQ.pack_into(mm, _SEQ_OFFSET, (seq | 1) + 1)
    self.captures += 1
    if advance:
      self.advance(request, captured_ns)

  def advance(self, request: FrameRequest, captured_ns: int) -> None:
    """Move the capture schedule on by one frame captured at ``captured_ns``."""
    interval_ns = request.interval_us * 1000
    # Advance on the schedule so render jitter does not drift; after a gap, restart from now.
    self._next_capture_ns += interval_ns
    if self._next_capture_ns <= captured_ns:
      self._next_capture_ns = captured_ns + interval_ns


class SyntheticFrames:
  """Consumer-compatible moving test pattern, to prove the car link without the UI.

  Color bars that slide one step per frame plus a frame counter make frozen,
  torn or stale video obvious on the car's screen.
  """

  def __init__(self):
    self.request: FrameRequest | None = None
    self.index = 0
    self.next_ns = 0

  def configure(self, request: FrameRequest) -> None:
    self.request = request

  def demand(self, seconds: float = 1.0) -> None:
    pass

  def release_demand(self) -> None:
    pass

  def latest(self) -> Frame | None:
    request = self.request
    now_ns = time.monotonic_ns()
    if request is None or now_ns < self.next_ns:
      return None
    self.next_ns = now_ns + request.interval_us * 1000
    import numpy as np
    width, height = request.width, request.height
    colors = np.array([[255, 255, 255, 255], [255, 255, 0, 255], [0, 255, 255, 255], [0, 255, 0, 255],
                       [255, 0, 255, 255], [255, 0, 0, 255], [0, 0, 255, 255], [40, 40, 40, 255]], np.uint8)
    columns = ((np.arange(width) + self.index * 8) * len(colors) // width) % len(colors)
    image = np.ascontiguousarray(np.broadcast_to(colors[columns], (height, width, 4)))
    x, y, w, h = request.content(width, height)
    image[:y], image[y + h:], image[:, :x], image[:, x + w:] = 0, 0, 0, 0
    try:
      import cv2
      scale = height / 240
      cv2.putText(image, f"StarPilot AA {self.index:06d}", (x + int(20 * scale), y + int(60 * scale)), cv2.FONT_HERSHEY_SIMPLEX,
                  scale, (0, 0, 0, 255), max(2, int(3 * scale)), cv2.LINE_AA)
    except ImportError:
      pass
    self.index += 1
    return Frame(image.tobytes(), width, height, now_ns, self.index)

  def close(self) -> None:
    pass
