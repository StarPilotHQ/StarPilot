"""Acknowledged, retryable delivery to the device's Android Auto Bugsink project."""

import hashlib
import json
import os
from datetime import UTC, datetime
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from openpilot.starpilot.system.starport import identity
from openpilot.starpilot.system.starport.bugsink_diagnosis import sanitize

CONFIG_PATH = identity.DATA_DIR / "bugsink.json"
DEFAULT_DSN = "https://3afdea6603b94eb089ec84953413619b@bugsink.firestar.link/3"
HTTP_TIMEOUT_S = 5
MAX_EVENT_BYTES = 512 << 10


class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self, req, fp, code, msg, headers, newurl):
    return None


class Sender:
  def __init__(self, dsn: str, *, metadata=None, allow_http=False, opener=None, active=lambda: True,
               logger="starport", client="starpilot-aa/1", schema_tags=None):
    url = urllib.parse.urlsplit(dsn)
    path, _, project = url.path.rstrip("/").rpartition("/")
    if url.scheme not in ("https", "http") or not url.hostname or not url.username or not project.isdecimal() or url.query or url.fragment:
      raise ValueError("Invalid Android Auto Bugsink DSN")
    if url.scheme == "http" and not allow_http and url.hostname not in ("localhost", "127.0.0.1", "::1"):
      raise ValueError("Remote Android Auto Bugsink requires HTTPS")
    self.endpoint = urllib.parse.urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], f"{path}/api/{project}/store/", "", ""))
    self.key = url.username
    self.metadata = metadata or {}
    self.opener = opener or urllib.request.build_opener(NoRedirect())
    self.active = active
    self.logger, self.client = logger, client
    self.schema_tags = {"aa.schema": "3"} if schema_tags is None else schema_tags

  def __call__(self, **event) -> bool:
    if not self.active():
      return False
    event = sanitize(event)
    stable = json.dumps([self.metadata.get("device_id", "unknown"), event], sort_keys=True, separators=(",", ":"))
    event_id = hashlib.sha256(stable.encode()).hexdigest()[:32]
    payload = {**event, "event_id": event_id, "timestamp": datetime.now(UTC).timestamp(), "platform": "python",
               "logger": self.logger, "release": event.get("tags", {}).get("commit", "unknown"),
               "environment": self.metadata.get("environment", "device"),
               "user": {"id": self.metadata.get("device_id", "unknown")},
               "tags": {**self.metadata, **event.get("tags", {}), "reporter.commit": self.metadata.get("commit", "unknown"),
                        "reporter.branch": self.metadata.get("branch", "unknown"), **self.schema_tags}}
    data = json.dumps(payload, allow_nan=False).encode()
    if len(data) > MAX_EVENT_BYTES:
      print(json.dumps({"event": "bugsink_delivery_failed", "reason": "event_too_large"}), flush=True)
      return False
    request = urllib.request.Request(self.endpoint, data=data, headers={"Content-Type": "application/json", "User-Agent": self.client,
      "X-Sentry-Auth": f"Sentry sentry_version=7,sentry_client={self.client},sentry_key={self.key}"})
    try:
      with self.opener.open(request, timeout=HTTP_TIMEOUT_S) as response:
        accepted = json.loads(response.read(4096))
        return 200 <= response.status < 300 and accepted.get("id") == event_id
    except (OSError, ValueError, urllib.error.URLError) as error:
      print(json.dumps({"event": "bugsink_delivery_failed", "error_kind": type(error).__name__,
                        "status": getattr(error, "code", None)}), flush=True)
      return False


def device_metadata():
  values = {"device_id": os.getenv("DONGLE_ID", "unknown"), "commit": os.getenv("GIT_COMMIT", "unknown")}
  try:
    from openpilot.common.hardware import HARDWARE
    from openpilot.common.params import Params
    from openpilot.common.version import get_build_metadata
    build = get_build_metadata()
    values.update(commit=build.openpilot.git_commit, branch=build.channel)
    values.update(device_id=Params().get("DongleId") or values["device_id"], device=HARDWARE.get_device_type())
  except Exception:
    pass
  return {key: str(value) for key, value in values.items()}


def configured_sender(path: Path = CONFIG_PATH, *, active=lambda: True, **sender_options):
  try:
    config = json.loads(path.read_text())
  except FileNotFoundError:
    config = {}
  if not isinstance(config, dict) or config.get("enabled") is False:
    return None
  return Sender(config.get("dsn") or DEFAULT_DSN, metadata=device_metadata(), allow_http=config.get("allow_http") is True, active=active, **sender_options)
