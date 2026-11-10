"""One bounded parked snapshot from an already-running camera owner."""

import subprocess
import sys
import threading
import time


CAMERAS = frozenset(("cabin", "wide", "narrow"))
MAX_JPEG_BYTES = 1_000_000
# Worker budgets leave two seconds for process startup, encoding and cleanup.
CAPTURE_TIMEOUT_SECONDS = {"cabin": 24, "wide": 19, "narrow": 12}
_CAPTURE_LOCK = threading.Lock()


class SnapshotUnavailable(Exception):
  pass


class SnapshotDenied(SnapshotUnavailable):
  pass


class CameraSnapshot:
  def __init__(self, *, spawn=subprocess.Popen, clock=time.monotonic):
    self.spawn, self.clock = spawn, clock
    self.last_request = {}

  def capture(self, camera: str, *, permitted) -> bytes:
    if camera not in CAMERAS:
      raise ValueError("Unknown camera")
    if not permitted():
      raise SnapshotDenied
    if not _CAPTURE_LOCK.acquire(blocking=False):
      raise SnapshotUnavailable("Another snapshot is in progress")
    process = None
    try:
      if self.clock() - self.last_request.get(camera, float('-inf')) < 1:
        raise SnapshotUnavailable("Wait before another snapshot")
      self.last_request[camera] = self.clock()
      from openpilot.starpilot.galaxy.camera_request import request, boot_ns
      request()
      frame_after = boot_ns()
      deadline = self.clock() + CAPTURE_TIMEOUT_SECONDS[camera]
      process = self.spawn([sys.executable, '-m', 'openpilot.starpilot.galaxy.camera_snapshot_worker', camera],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True)
      while self.clock() < deadline:
        if not permitted():
          raise SnapshotDenied
        try:
          body, _ = process.communicate(timeout=min(.1, max(.001, deadline - self.clock())))
        except subprocess.TimeoutExpired:
          continue
        if not permitted():
          raise SnapshotDenied
        if (process.returncode != 0 or not 4 <= len(body) <= MAX_JPEG_BYTES or
            not body.startswith(b'\xff\xd8') or not body.endswith(b'\xff\xd9')):
          raise SnapshotUnavailable("No fresh camera frame")
        if camera == "cabin":
          from openpilot.starpilot.galaxy.camera_request import mark_frame
          mark_frame(clock=lambda: frame_after)
        return body
      raise SnapshotUnavailable("Camera timed out")
    except OSError as error:
      raise SnapshotUnavailable("Camera unavailable") from error
    finally:
      if process is not None:
        if process.poll() is None:
          process.kill()
        process.wait()
        if process.stdout is not None:
          process.stdout.close()
      _CAPTURE_LOCK.release()
