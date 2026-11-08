"""Upload logs on request to Discord, reusing a thread per device."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from openpilot.common.utils import atomic_write
from openpilot.starpilot.system.starport import identity as identity_store

MESSAGE_LIMIT = 60
DISCORD_HANDLE = re.compile(r"[A-Za-z0-9_.]{2,32}(#[0-9]{4})?")
WEBHOOK_OVERRIDE_PATH = identity_store.DATA_DIR / "webhook_url"
THREAD_DIR = identity_store.DATA_DIR / "discord_threads"
# Obfuscation only; device-local configuration overrides this built-in webhook.
_WEBHOOK_KEY = b"starport-logs"
_WEBHOOK_DATA = "".join(("GwAVAgNVXVtJBRwEHAEQTxEfAl0VXQVAEBYRHA4dGxxdRRhaWlJLREBYRkZcS0UcXl1QQVwAEEIzAydBSA",
                         "4KIjQjDShfJj0ZRkA6OjEbMi4JRzE6Rh1sOxo/HCVCDRMlCzRBHyIhUgQFTDAfChwdAGkpWR0HES0ZJw=="))
DISCORD_FILE_LIMIT = 10 * 1024 * 1024 - 64 * 1024  # 10 MiB attachment limit, with room for the form fields
THREAD_CACHE_WARNING = "The forum thread could not be remembered on this device. The next upload may create another post."
CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class SendRejected(Exception):
  pass


class SendBusy(Exception):
  pass


def webhook_url(override_path: Path = WEBHOOK_OVERRIDE_PATH) -> str:
  try:
    override = override_path.read_text().strip()
  except (OSError, UnicodeError):
    override = ""
  if override:
    return override
  if not _WEBHOOK_DATA:
    return ""
  data = base64.b64decode(_WEBHOOK_DATA)
  return bytes(byte ^ _WEBHOOK_KEY[index % len(_WEBHOOK_KEY)] for index, byte in enumerate(data)).decode()


def clean_message(value) -> str:
  if not isinstance(value, str):
    raise SendRejected("The message must be text.")
  message = CONTROL.sub(" ", value).strip()
  if len(message) > MESSAGE_LIMIT:
    raise SendRejected(f"Keep the message to {MESSAGE_LIMIT} characters.")
  return message


def clean_discord(value) -> str:
  """An optional Discord username for a follow-up, without the leading @; "" when none was given."""
  if not isinstance(value, str):
    raise SendRejected("The Discord username must be text.")
  handle = value.strip().removeprefix("@")
  if handle and not DISCORD_HANDLE.fullmatch(handle):
    raise SendRejected("That doesn't look like a Discord username.")
  return handle


def _car_fingerprint() -> str:
  from openpilot.common.params import Params
  from openpilot.starpilot.saved_source import read_saved
  raw, readable = read_saved(Params(), "CarParamsPersistent", 1 << 20)
  if not readable or not raw:
    return ""
  from openpilot.cereal import messaging
  from opendbc.car.structs import car
  from openpilot.starpilot.schema_cache import inspect_cache
  inspected = inspect_cache("CarParamsPersistent", raw)
  if inspected.status != "valid" or inspected.payload is None:
    return ""
  fingerprint = str(messaging.log_from_bytes(inspected.payload, car.CarParams).carFingerprint or "").strip()
  return "" if fingerprint == "MOCK" else fingerprint


def _head_unit(log_dir: Path | None, logs_to_check: int = 3) -> str:
  """The car's head unit from the newest session log that identified it, or ''."""
  from openpilot.starpilot.system.starport import compat_report
  for path in compat_report.session_logs(log_dir or identity_store.LOG_DIR)[:logs_to_check]:
    car = compat_report.summarize(compat_report.load_events(path))["car"]
    name = " ".join(part for part in (car.get("head_unit_make"), car.get("head_unit_model")) if part)
    name = name or car.get("display_name") or car.get("bluetooth_name") or ""
    if name:
      return name
  return ""


def _setup(log_dir: Path | None, logs_to_check: int = 3) -> str:
  """The setup the owner chose (built-in / adapter / usb / app) and its profile, from the newest log that recorded one."""
  from openpilot.starpilot.system.starport import compat_report
  from openpilot.starpilot.system.starport.bugsink_report import session_setup
  for path in compat_report.session_logs(log_dir or identity_store.LOG_DIR)[:logs_to_check]:
    setup = session_setup(compat_report.load_events(path))
    if setup:
      return " / ".join(str(setup[key]) for key in ("type", "profile", "profile_source") if setup.get(key))
  return ""


def _handshake(log_dir: Path | None, logs_to_check: int = 3) -> str:
  """What the setup ladder tried in the newest log that recorded it, e.g. "standard (advanced) > other_reply (worked)"."""
  from openpilot.starpilot.system.starport import compat_report
  from openpilot.starpilot.system.starport.bugsink_report import session_ladder
  for path in compat_report.session_logs(log_dir or identity_store.LOG_DIR)[:logs_to_check]:
    tried = session_ladder(compat_report.load_events(path))
    if tried:
      return " > ".join(f"{item['rung']}{' learned' if item.get('learned') else ''} ({item.get('outcome', 'tried')})" for item in tried[-6:])
  return ""


def device_summary(log_dir: Path | None = None) -> dict:
  from openpilot.starpilot.system.starport.bugsink_transport import device_metadata
  summary = device_metadata()
  for key, read in (("car_fingerprint", _car_fingerprint), ("head_unit", lambda: _head_unit(log_dir)), ("setup", lambda: _setup(log_dir)),
                    ("handshake", lambda: _handshake(log_dir))):
    try:
      summary[key] = read()
    except Exception as error:
      summary[f"{key}_error"] = str(error)
  return summary


def device_tag(summary: dict) -> str:
  """8 characters of a hash of the dongle ID: tells devices apart without showing the ID."""
  dongle_id = summary.get("device_id") or ""
  return hashlib.sha256(f"starpilot-auto:{dongle_id}".encode()).hexdigest()[:8]


def thread_title(summary: dict) -> str:
  return f"{summary.get('car_fingerprint') or 'Device'} {device_tag(summary)}"[:100]


def _response_json(response) -> dict:
  try:
    value = response.json()
  except (AttributeError, ValueError):
    return {}
  return value if isinstance(value, dict) else {}


def _save_thread_id(path: Path, key: str, thread_id: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  # A power loss during a write must not truncate the previous cache entry.
  with atomic_write(str(path), overwrite=True) as handle:
    json.dump({"key": key, "thread_id": thread_id}, handle)


def send(data: bytes, name: str, note: str, summary: dict, url: str | None = None, post=None,
         thread_dir: Path | None = None, discord: str = "") -> str | None:
  """Post the zip; return a cache warning after success, or raise SendRejected on an upload failure."""
  url = webhook_url() if url is None else url
  if not url:
    raise SendRejected("Sending isn't set up on this build. Use Download all logs and send the file instead.")
  if len(data) > DISCORD_FILE_LIMIT:
    raise SendRejected(f"The logs are {len(data) / 1e6:.1f} MB, over Discord's 10 MB limit. Use Download all logs instead.")
  if post is None:
    import requests
    post = requests.post
  dongle_id = summary.get("device_id") or "device"
  lines = [f"**StarPort logs from {device_tag(summary)}**",
           f"Car: {summary.get('car_fingerprint') or 'unknown'}",
           f"Head unit: {summary.get('head_unit') or 'unknown'}",
           f"Setup: {summary.get('setup') or 'unknown'}",
           f"Handshake: {summary.get('handshake') or 'unknown'}",
           f"Branch {summary.get('branch', '?')} @ {str(summary.get('commit', '?'))[:10]}",
           f"Note: {note.strip() or '(none)'}",
           f"Follow-up: Discord `{discord}`" if discord else "Follow-up: not requested"]
  payload = {"content": "\n".join(lines)[:1900], "username": "StarPilot diagnostics", "allowed_mentions": {"parse": []}}

  parts = urlsplit(url)
  query = dict(parse_qsl(parts.query, keep_blank_values=True))
  explicit_thread = bool(query.get("thread_id"))
  # Ignore query options when identifying a webhook, and never store its token in the cache.
  cache_key = hashlib.sha256(json.dumps([parts.scheme, parts.netloc, parts.path.rstrip("/"), dongle_id]).encode()).hexdigest()
  thread_path = (THREAD_DIR if thread_dir is None else thread_dir) / f"{cache_key}.json"
  cached_id = None
  if not explicit_thread:
    query.pop("thread_id", None)
    try:
      entry = json.loads(thread_path.read_text())
      if isinstance(entry, dict) and entry.get("key") == cache_key:
        candidate = entry.get("thread_id")
        if isinstance(candidate, str) and candidate.isascii() and candidate.isdigit():
          cached_id = candidate
    except (OSError, ValueError):
      pass

  # Always wait for confirmation, including uploads to an existing thread.
  query["wait"] = "true"
  for attempt in range(2):
    if not explicit_thread and cached_id:
      query["thread_id"] = cached_id
    elif not explicit_thread:
      query.pop("thread_id", None)
      payload["thread_name"] = thread_title(summary)
    post_url = urlunsplit(parts._replace(query=urlencode(query)))

    try:
      response = post(post_url, data={"payload_json": json.dumps(payload)}, files={"files[0]": (name, data, "application/zip")}, timeout=120)
    except Exception as error:
      raise SendRejected(f"Could not send the logs: {type(error).__name__}. Check the internet connection, or use Download all logs.") from error

    # Unknown Channel means the cached thread was deleted (Discord answers 400 for webhooks, 404 elsewhere).
    # Unknown Webhook does not.
    if attempt == 0 and cached_id and response.status_code in (400, 404) and _response_json(response).get("code") == 10003:
      cached_id = None  # Retry from memory even if the cache cannot be removed.
      try:
        thread_path.unlink(missing_ok=True)
      except OSError:
        pass
      continue
    break

  if response.status_code not in (200, 204):
    reason = str(_response_json(response).get("message") or "")[:120]
    detail = f"HTTP {response.status_code}{f': {reason}' if reason else ''}"
    raise SendRejected(f"Discord refused the logs ({detail}). Use Download all logs instead.")

  if not explicit_thread and not cached_id:
    new_thread_id = _response_json(response).get("channel_id")
    if not isinstance(new_thread_id, str) or not new_thread_id.isascii() or not new_thread_id.isdigit():
      return THREAD_CACHE_WARNING
    try:
      _save_thread_id(thread_path, cache_key, new_thread_id)
    except OSError:
      return THREAD_CACHE_WARNING
  return None


class StarPortLogSender:
  """One send at a time, off the request thread; the page polls status()."""

  def __init__(self, bundle: Callable[..., tuple[str, bytes]], *, sender: Callable[..., str | None] = send,
               summary: Callable[[], dict] = device_summary, url: Callable[[], str] = webhook_url):
    self._bundle, self._sender, self._summary, self._url = bundle, sender, summary, url
    self._lock = threading.Lock()
    self._thread: threading.Thread | None = None
    self._state = {"state": "idle", "message": ""}

  def status(self) -> dict:
    with self._lock:
      return {**self._state, "available": bool(self._url()), "messageLimit": MESSAGE_LIMIT}

  def start(self, message, discord="") -> dict:
    note, handle = clean_message(message), clean_discord(discord)
    with self._lock:
      if self._thread is not None and self._thread.is_alive():
        raise SendBusy("StarPort logs are already being sent.")
      self._state = {"state": "sending", "message": "Collecting logs"}
      self._thread = threading.Thread(target=self._run, args=(note, handle), name="starport_log_send", daemon=True)
      self._thread.start()
    return self.status()

  def _set(self, **values) -> None:
    with self._lock:
      self._state.update(values)

  def _run(self, note: str, discord: str = "") -> None:
    try:
      summary = self._summary()
      name, data = self._bundle()
      left_out = ""
      if len(data) > DISCORD_FILE_LIMIT:
        # The device snapshot is the optional, bulky part; the session logs are what matter.
        name, data = self._bundle(system_snapshot=False)
        left_out = " The device snapshot was left out to fit Discord's 10 MB limit."
      self._set(message="Sending logs")
      warning = self._sender(data, name, note, summary, discord=discord)
      self._set(state="sent", message=f"Logs sent.{left_out} {warning or 'Thanks!'}".strip(), sentAt=int(time.time()))  # noqa: TID251 shown as a wall-clock time
    except SendRejected as error:
      self._set(state="failed", message=str(error)[:400])
    except Exception as error:
      self._set(state="failed", message=f"Could not send the logs: {type(error).__name__}. Use Download all logs instead.")
