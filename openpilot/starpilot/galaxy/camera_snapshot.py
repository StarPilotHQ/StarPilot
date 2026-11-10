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

  def capture(self, camera: str, *, permitted, parked: bool = False) -> bytes:
    result = self.capture_many((camera,), permitted=permitted, parked=parked)[camera]
    if isinstance(result, SnapshotUnavailable):
      raise result
    return result

  def capture_many(self, cameras, *, permitted, parked: bool = False) -> dict:
    """Read several cameras from one camera start; each result is JPEG bytes or SnapshotUnavailable."""
    if any(camera not in CAMERAS for camera in cameras):
      raise ValueError("Unknown camera")
    if not permitted():
      raise SnapshotDenied
    if not _CAPTURE_LOCK.acquire(blocking=False):
      raise SnapshotUnavailable("Another snapshot is in progress")
    results, processes, deadlines = {}, {}, {}
    try:
      for camera in cameras:
        if self.clock() - self.last_request.get(camera, float('-inf')) < 1:
          results[camera] = SnapshotUnavailable("Wait before another snapshot")
        else:
          self.last_request[camera] = self.clock()
      from openpilot.starpilot.galaxy.camera_request import request, boot_ns
      request()
      frame_after = boot_ns()
      for camera in cameras:
        if camera not in results:
          deadlines[camera] = self.clock() + CAPTURE_TIMEOUT_SECONDS[camera]
          processes[camera] = self.spawn([sys.executable, '-m', 'openpilot.starpilot.galaxy.camera_snapshot_worker', camera] +
                                         (['parked'] if parked else []),
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True)
      while len(results) < len(cameras):
        if not permitted():
          raise SnapshotDenied
        for camera, process in processes.items():
          if camera in results:
            continue
          if self.clock() >= deadlines[camera]:
            results[camera] = SnapshotUnavailable("Camera timed out")
            continue
          try:
            body, _ = process.communicate(timeout=min(.1 / len(processes), max(.001, deadlines[camera] - self.clock())))
          except subprocess.TimeoutExpired:
            continue
          if (process.returncode != 0 or not 4 <= len(body) <= MAX_JPEG_BYTES or
              not body.startswith(b'\xff\xd8') or not body.endswith(b'\xff\xd9')):
            results[camera] = SnapshotUnavailable("No fresh camera frame")
          else:
            results[camera] = body
      if not permitted():
        raise SnapshotDenied
      if isinstance(results.get("cabin"), bytes):
        from openpilot.starpilot.galaxy.camera_request import mark_frame
        mark_frame(clock=lambda: frame_after)
      return results
    except OSError as error:
      raise SnapshotUnavailable("Camera unavailable") from error
    finally:
      for process in processes.values():
        if process.poll() is None:
          process.kill()
        process.wait()
        if process.stdout is not None:
          process.stdout.close()
      _CAPTURE_LOCK.release()
