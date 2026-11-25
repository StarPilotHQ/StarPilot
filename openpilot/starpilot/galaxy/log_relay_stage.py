"""Stage diagnostic log archives on the StarPilot log relay for one-click Discord confirmation."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from io import BytesIO
from urllib.parse import quote
from zipfile import BadZipFile, ZipFile

from openpilot.starpilot.galaxy.starport_send import DISCORD_FILE_LIMIT, install_repo, official_install

STAGE_URL = "https://starpilot-log-relay.nathan-g-hubbard.workers.dev/report/galaxy/stage"
CLAIM_RE = re.compile(r"^https://starpilot-log-relay\.nathan-g-hubbard\.workers\.dev/report/galaxy/claim/[a-f0-9]{64}$")
NOTE_LIMIT = 200
MODEL_LIMIT = 80
KINDS = ("starport", "connection", "controller")
PHONES = ("ios", "android")
CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class StageRejected(Exception):
  pass


class StageBusy(Exception):
  pass


def _clean_text(value: object) -> str:
  if not isinstance(value, str):
    return ""
  return CONTROL.sub(" ", value).strip()


def clean_stage_request(payload: object) -> tuple[str, str, str]:
  """Validate and normalize a staging request; return (kind, platform, formatted_note)."""
  if not isinstance(payload, dict):
    raise StageRejected("Invalid log report request.")
  kind = payload.get("kind")
  if kind not in KINDS:
    raise StageRejected("Choose what you are sending logs for.")
  allowed = {"kind", "note"} | ({"phone"} if kind == "connection" else {"model"} if kind == "controller" else set())
  if set(payload) - allowed:
    raise StageRejected("Invalid log report request.")
  note = payload.get("note")
  if not isinstance(note, str):
    raise StageRejected("Add a short note about what happened.")
  note = _clean_text(note)
  if not note:
    raise StageRejected("Add a short note about what happened.")
  if len(note) > NOTE_LIMIT:
    raise StageRejected(f"Keep the note to {NOTE_LIMIT} characters.")

  if kind == "connection":
    phone = payload.get("phone")
    if phone not in PHONES:
      raise StageRejected("Choose iPhone or Android.")
    label = "iPhone" if phone == "ios" else "Android"
    return kind, phone, f"[{label}] {note}"

  if kind == "controller":
    raw_model = payload.get("model")
    if not isinstance(raw_model, str):
      raise StageRejected("Enter your controller make and model.")
    model = _clean_text(raw_model)
    if not model:
      raise StageRejected("Enter your controller make and model.")
    if len(model) > MODEL_LIMIT:
      raise StageRejected(f"Keep the controller model to {MODEL_LIMIT} characters.")
    return kind, "ios", f"Controller ({model}): {note}"

  return kind, "android", note


def _capture_summary(kind: str, data: bytes) -> str:
  """Summarize the staged capture without collecting a second snapshot."""
  member = {"starport": "SUMMARY.txt", "connection": "connection-logs.txt", "controller": "bluetooth-log.json"}[kind]
  try:
    with ZipFile(BytesIO(data)) as archive, archive.open(member) as stream:
      raw = stream.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
      return ""
    if kind == "controller":
      capture = json.loads(raw)
      rows = capture["externalInputInterfaces"]
      counts = [sum(row["discovery"] == state for row in rows) for state in ("eligible", "filtered", "duplicate identity")]
      suffix = " (inventory truncated)" if capture.get("truncated") else ""
      header = f"External input interfaces: {len(rows)}{suffix}\n"
      return header + f"Eligible: {counts[0]} | Filtered: {counts[1]} | Duplicate identity: {counts[2]}"
    text = raw.decode("utf-8", errors="replace").strip()
    if kind == "starport":
      return text[:600]
    labels = ("Comma uptime: ", "Parked configuration allowed: ", "Bonjour advertiser: ", "Bluetooth bridge: ")
    lines = text.splitlines()
    return "\n".join(line[:220] for line in lines if line.startswith(labels))[:600]
  except (BadZipFile, KeyError, ValueError, TypeError, AttributeError, OSError, EOFError):
    # Missing or older summary formats should still deliver the original ZIP.
    return ""


def _response_error(response) -> str:
  try:
    body = response.json()
  except (AttributeError, ValueError):
    return ""
  if isinstance(body, dict) and isinstance(body.get("error"), str):
    return body["error"][:240]
  return ""


class LogRelayStager:
  """One diagnostic upload at a time, off the request thread; the page polls status()."""

  def __init__(
    self,
    starport_bundle: Callable[..., tuple[str, bytes]],
    connection_zip: Callable[[], bytes],
    controller_zip: Callable[[], bytes],
    *,
    post=None,
    stage_url: Callable[[], str] | str = STAGE_URL,
    official: Callable[[], bool] = official_install,
    repo: Callable[[], str] = install_repo,
  ):
    self._starport_bundle = starport_bundle
    self._connection_zip = connection_zip
    self._controller_zip = controller_zip
    self._post = post
    self._stage_url: Callable[[], str] | str = stage_url
    self._official, self._repo_source = official, repo
    self._lock = threading.Lock()
    self._thread: threading.Thread | None = None
    self._state = {"state": "idle", "message": "", "claimUrl": "", "kind": ""}
    self._session = None
    self._fork: bool | None = None
    self._repo = ""

  def _url(self) -> str:
    stage_url = self._stage_url
    return stage_url if isinstance(stage_url, str) else stage_url()

  def status(self, session=None) -> dict:
    with self._lock:
      if self._fork is None:
        self._fork = not self._official()
        self._repo = self._repo_source() if self._fork else ""
      return {
        **(self._state if session == self._session else {"state": "idle", "message": "", "claimUrl": "", "kind": ""}),
        "available": bool(self._url()),
        "fork": self._fork,
        "repo": self._repo,
        "noteLimit": NOTE_LIMIT,
        "modelLimit": MODEL_LIMIT,
      }

  def start(self, payload: object, session=None) -> dict:
    kind, platform, formatted_note = clean_stage_request(payload)
    if not self._url():
      raise StageRejected("Online log relay is unavailable. Use Download ZIP instead.")
    with self._lock:
      if self._thread is not None and self._thread.is_alive():
        raise StageBusy("Logs are already being prepared.")
      self._session = session
      self._state = {"state": "staging", "message": "Collecting logs", "claimUrl": "", "kind": kind}
      self._thread = threading.Thread(
        target=self._run,
        args=(kind, platform, formatted_note),
        name="galaxy_log_stage",
        daemon=True,
      )
      self._thread.start()
    return self.status(session)

  def _set(self, **values) -> None:
    with self._lock:
      self._state.update(values)

  def _build_zip(self, kind: str) -> bytes:
    if kind == "starport":
      _, data = self._starport_bundle()
      if len(data) > DISCORD_FILE_LIMIT:
        _, data = self._starport_bundle(system_snapshot=False)
      return data
    if kind == "connection":
      return self._connection_zip()
    return self._controller_zip()

  def _run(self, kind: str, platform: str, formatted_note: str) -> None:
    try:
      data = self._build_zip(kind)
      if len(data) > DISCORD_FILE_LIMIT:
        raise StageRejected("The log archive exceeds the upload limit. Use Download ZIP instead.")
      self._set(message="Uploading bundle")
      if self._post is None:
        import requests
        post = requests.post
      else:
        post = self._post
      summary = _capture_summary(kind, data)
      response = post(
        self._url(),
        data=data,
        headers={
          "Content-Type": "application/zip",
          "X-Galaxy-Kind": kind,
          **({"X-Galaxy-Summary": quote(summary, safe="")} if summary else {}),
          "X-Galaxy-Platform": platform,
          "X-Galaxy-Note": quote(formatted_note, safe=""),
        },
        allow_redirects=False,
        timeout=(5, 60),
      )
      if response.status_code == 404:
        raise StageRejected("Online log relay is updating. Use Download ZIP instead.")
      if response.status_code == 413:
        raise StageRejected("The log archive exceeds the upload limit. Use Download ZIP instead.")
      if response.status_code != 200:
        detail = _response_error(response)
        raise StageRejected(detail or "Could not stage logs for Discord. Use Download ZIP instead.")
      try:
        body = response.json()
      except (AttributeError, ValueError) as error:
        raise StageRejected("Invalid response from log relay. Use Download ZIP instead.") from error
      claim_url = body.get("claimUrl") if isinstance(body, dict) else None
      if not isinstance(claim_url, str) or not CLAIM_RE.fullmatch(claim_url):
        raise StageRejected("Invalid relay claim link. Use Download ZIP instead.")
      self._set(state="ready", claimUrl=claim_url, message="Logs uploaded. Your report is not posted until you finish on Discord.")
    except StageRejected as error:
      self._set(state="failed", claimUrl="", message=str(error)[:400])
    except Exception:
      self._set(state="failed", claimUrl="", message="Your comma could not reach the internet. Use Download ZIP instead.")
