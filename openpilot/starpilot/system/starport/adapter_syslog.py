"""Save the AAWireless adapter's own syslog while the comma is on its Wi-Fi, so it ships with the StarPort logs."""

from __future__ import annotations

import http.client
import time
import zlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from openpilot.common.utils import atomic_write
from openpilot.starpilot.system.starport import identity as identity_store

FILE_NAME = "adapter_syslog.txt"
PORT = 8000
PATH = "/syslog"
TIMEOUT_S = 4.0
DEADLINE_S = 6.0  # whole download, so a trickling adapter cannot hold session cleanup
MAX_DOWNLOAD_BYTES = 8 << 20
MAX_TEXT_BYTES = 2 << 20  # newest part kept; the bundle takes the last 1 MiB of it
MIN_INTERVAL_S = 30.0


def _text(body: bytes) -> bytes:
  if body[:2] != b"\x1f\x8b":
    return body[-MAX_TEXT_BYTES:]
  inflate = zlib.decompressobj(zlib.MAX_WBITS | 16)
  kept = b""
  data = body
  while data:
    kept = (kept + inflate.decompress(data, MAX_TEXT_BYTES))[-MAX_TEXT_BYTES:]
    data = inflate.unconsumed_tail
  return kept


def _download(host: str, local_ip: str) -> tuple[int, bytes]:
  connection = http.client.HTTPConnection(host, PORT, timeout=TIMEOUT_S, source_address=(local_ip, 0) if local_ip else None)
  try:
    connection.request("GET", PATH)
    response = connection.getresponse()
    deadline, body = time.monotonic() + DEADLINE_S, b""
    while len(body) <= MAX_DOWNLOAD_BYTES:
      if time.monotonic() > deadline:
        raise TimeoutError("syslog download took too long")
      chunk = response.read1(64 << 10)
      if not chunk:
        break
      body += chunk
    return response.status, body
  finally:
    connection.close()


class AdapterSyslog:
  def __init__(self, log: Callable[..., None], log_dir: Path | None = None, download=_download, clock=time.monotonic):
    self.log, self.download, self.clock = log, download, clock
    self.path = (log_dir or identity_store.LOG_DIR) / FILE_NAME
    self.last = -MIN_INTERVAL_S

  def fetch(self, host: str, local_ip: str = "") -> bool:
    if not host or self.clock() - self.last < MIN_INTERVAL_S:
      return False
    self.last = self.clock()
    started = self.clock()
    try:
      status, body = self.download(host, local_ip)
      if status != 200:
        raise OSError(f"HTTP {status}")
      if len(body) > MAX_DOWNLOAD_BYTES:
        raise OSError("syslog is larger than the download limit")
      text = _text(body)
      header = f"# AAWireless syslog from http://{host}:{PORT}{PATH} at {datetime.now(UTC).isoformat(timespec='seconds')}\n"
      self.path.parent.mkdir(parents=True, exist_ok=True)
      with atomic_write(str(self.path), mode="wb", overwrite=True) as handle:
        handle.write(header.encode() + text)
    except (OSError, EOFError, zlib.error, http.client.HTTPException) as error:
      self.log("adapter_syslog", ok=False, error=str(error)[:160], kind=type(error).__name__)
      return False
    self.log("adapter_syslog", ok=True, bytes=len(body), text_bytes=len(text), ms=round((self.clock() - started) * 1000))
    return True
