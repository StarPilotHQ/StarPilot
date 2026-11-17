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
    cameras = tuple(cameras)
    if not cameras or any(not isinstance(camera, str) or camera not in CAMERAS for camera in cameras):
      raise ValueError("Expected one or more known cameras")
    if len(set(cameras)) != len(cameras):
      raise ValueError("Duplicate camera")
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
      if len(results) == len(cameras):
        return results
      from openpilot.starpilot.galaxy.camera_request import request, boot_ns
      capture_started = self.clock()
      request()
      frame_after = boot_ns()
      for index, camera in enumerate(cameras):
        if index and not permitted():
          raise SnapshotDenied
        now = self.clock()
        for expired in tuple(processes):
          if now >= deadlines[expired]:
            results[expired] = SnapshotUnavailable("Camera timed out")
            self._close_reader(processes[expired])
            del processes[expired]
        if camera not in results:
          deadlines[camera] = capture_started + CAPTURE_TIMEOUT_SECONDS[camera]
          if now >= deadlines[camera]:
            results[camera] = SnapshotUnavailable("Camera timed out")
            continue
          try:
            processes[camera] = self.spawn([sys.executable, '-m', 'openpilot.starpilot.galaxy.camera_snapshot_worker', camera] +
                                           (['parked'] if parked else []),
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True)
          except OSError:
            results[camera] = SnapshotUnavailable("Camera unavailable")
      while len(results) < len(cameras):
        if not permitted():
          raise SnapshotDenied
        for camera in tuple(processes):
          # Reap expired readers before waiting on any other camera. Bound every
          # poll by the earliest deadline, not just the selected reader's budget.
          now = self.clock()
          for expired in tuple(processes):
            if now >= deadlines[expired]:
              results[expired] = SnapshotUnavailable("Camera timed out")
              self._close_reader(processes[expired])
              del processes[expired]
          if camera not in processes:
            continue
          process = processes[camera]
          timeout = min(.1 / len(processes), min(deadlines[name] for name in processes) - now)
          try:
            body, _ = process.communicate(timeout=timeout)
          except subprocess.TimeoutExpired:
            continue
          if (process.returncode != 0 or not 4 <= len(body) <= MAX_JPEG_BYTES or
              not body.startswith(b'\xff\xd8') or not body.endswith(b'\xff\xd9')):
            results[camera] = SnapshotUnavailable("No fresh camera frame")
          else:
            results[camera] = body
          self._close_reader(processes[camera])
          del processes[camera]
      if not permitted():
        raise SnapshotDenied
      if isinstance(results.get("cabin"), bytes):
        from openpilot.starpilot.galaxy.camera_request import mark_frame
        mark_frame(clock=lambda: frame_after)
      return results
    except OSError as error:
      raise SnapshotUnavailable("Camera unavailable") from error
    finally:
      try:
        for process in processes.values():
          try:
            self._close_reader(process)
          except OSError:
            pass  # Still clean up the other reader and release ownership.
      finally:
        _CAPTURE_LOCK.release()

  @staticmethod
  def _close_reader(process):
    try:
      if process.poll() is None:
        try:
          process.kill()
        except ProcessLookupError:
          pass  # It exited after poll(); wait must still reap it.
      process.wait()
    finally:
      if process.stdout is not None:
        process.stdout.close()
