"""Bounded StarPort session logging."""

import json
import os
import queue
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from openpilot.starpilot.system.starport import identity as identity_store, system_slice

MAX_LOG_FILES = 20
LOG_QUEUE_MAX = 1000          # session log records waiting for the writer; past this they are dropped and counted
STALL_CAPTURE_S = 20.0       # device logs kept from before a shared-radio stall is reported


class EventLog:
  """Sanitized JSONL session log under /data/starport/logs, plus the recent tail in memory.

  The streaming loop logs too, so records are queued and a background thread writes them: a slow
  or full disk never stalls projection. If the writer falls behind, records are dropped and the
  next one that fits is preceded by a ``log_dropped`` count.
  """

  def __init__(self, directory: Path | None = None):
    self.directory = directory or identity_store.LOG_DIR
    self.recent: deque[dict] = deque(maxlen=40)
    self.lock = threading.Lock()
    self._queue: queue.Queue = queue.Queue(maxsize=LOG_QUEUE_MAX)
    self._writer: threading.Thread | None = None
    self._writer_stop: threading.Event | None = None
    self._dropped = 0
    self.path: Path | None = None
    self.started = 0.0

  def open(self) -> None:
    try:
      self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
      logs = sorted(self.directory.glob("session-*.jsonl"), key=identity_store.session_log_order)
      for old in logs[:max(0, len(logs) - MAX_LOG_FILES + 1)]:
        old.unlink(missing_ok=True)
      system_slice.prune_sidecars(self.directory)
      number = max([0, *(identity_store.session_log_order(log)[0] for log in logs)]) + 1
      path = self.directory / f"session-{number:06d}-{identity_store.timestamp()}.jsonl"
      fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
      handle = os.fdopen(fd, "a")
    except OSError:
      return
    with self.lock:
      self.path, self.started = path, datetime.now(UTC).timestamp()
      self._queue = queue.Queue(maxsize=LOG_QUEUE_MAX)
      self._dropped = 0
      self._writer_stop = threading.Event()
      self._writer = threading.Thread(target=self._write, args=(handle, self._queue, self._writer_stop),
                                      name="aa_session_log", daemon=True)
      self._writer.start()

  @staticmethod
  def _write(handle, records: queue.Queue, stopped: threading.Event) -> None:
    with handle:
      while not stopped.is_set() or not records.empty():
        try:
          line = records.get(timeout=0.1)
        except queue.Empty:
          continue
        try:
          handle.write(line)
          if records.empty():
            handle.flush()
        except OSError:
          pass

  def close(self) -> None:
    """Write what is queued, then stop the writer (bounded: a stuck disk is left to the daemon thread)."""
    with self.lock:
      writer, stopped = self._writer, self._writer_stop
      self._writer = self._writer_stop = None
    if writer is None:
      return
    assert stopped is not None
    stopped.set()
    writer.join(timeout=2.0)

  def capture_system(self) -> None:
    """Save what the device was doing during this session beside its log, off the session's thread."""
    if self.path is not None:
      system_slice.write_sidecar_async(self.path, self.started, datetime.now(UTC).timestamp())

  def capture_stall(self) -> None:
    """Save the last seconds of device logs now; the session's own capture keeps only its newest lines."""
    if self.path is not None:
      now = datetime.now(UTC).timestamp()
      system_slice.write_sidecar_async(self.path, now - STALL_CAPTURE_S, now, tag=".stall",
                                       on_firmware=lambda lines, reason: self("wifi_firmware_restart", lines=lines, reason=reason))

  def _line(self, record: dict) -> str:
    return json.dumps(record, default=str) + "\n"

  def __call__(self, name: str, **values) -> None:
    record = {"t": datetime.now(UTC).isoformat(timespec="milliseconds"), "event": name, "mono_s": time.monotonic(), **values}
    with self.lock:
      self.recent.append(record)
      if self._writer is None:
        return
      try:
        if self._dropped:
          self._queue.put_nowait(self._line({"t": record["t"], "event": "log_dropped", "count": self._dropped}))
          self._dropped = 0
        self._queue.put_nowait(self._line(record))
      except queue.Full:
        self._dropped += 1
