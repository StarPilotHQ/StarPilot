"""Bounded analytics line protocol and the fixed StarPilot write endpoint."""
from __future__ import annotations

import json
import math
import re
from urllib.parse import quote

import requests

from openpilot.starpilot.analytics.credentials import CLIENT_TOKEN

WRITE_URL = "https://stats.firestar.link/api/v2/write"
COMMITS_URL = "https://api.github.com/repos/StarPilotHQ/StarPilot/commits/"
MAX_PAYLOAD = 128 * 1024
MAX_SETTINGS = 2051
MAX_TEXT_BYTES = 256
TAGS = ("branch", "car_make", "car_model", "city", "country", "device", "device_generation", "driving_model",
        "state", "theme", "dongle_id")
METRICS = frozenset(("blocked_user", "current_months_kilometers", "event", "starpilot_drives", "starpilot_hours",
                    "starpilot_miles", "goat_scream", "has_cc_long", "has_openpilot_longitudinal", "has_pedal",
                    "has_sdsu", "has_sascm", "has_zss", "lagd_applied_delay_seconds", "lagd_calibration_percent",
                    "lagd_learned_delay_seconds", "lagd_learned_delay_std_seconds", "lagd_valid_blocks", "rainbow_path",
                    "random_events", "total_aol_seconds", "total_lateral_seconds", "total_longitudinal_seconds",
                    "total_tracked_seconds", "tuning_level", "up_to_date", "using_stock_acc", "model_sha256",
                    "model_variant", "commit", "software_major"))
INT_FIELDS = frozenset(("current_months_kilometers", "event", "starpilot_drives",
                        "lagd_calibration_percent", "lagd_valid_blocks", "tuning_level", "software_major"))
FLOAT_FIELDS = frozenset(("starpilot_hours", "starpilot_miles", "lagd_applied_delay_seconds", "lagd_learned_delay_seconds",
                         "lagd_learned_delay_std_seconds", "total_aol_seconds", "total_lateral_seconds",
                         "total_longitudinal_seconds", "total_tracked_seconds"))
STRING_FIELDS = frozenset(("model_sha256", "model_variant", "commit"))

SECRET_NAMES = ("token", "password", "secret", "credential", "authorization", "privatekey", "apikey",
                "latitude", "longitude", "gps", "location", "ssh", "cookie", "certificate", "secoc", "pairing", "jwt",
                "accesskey", "encryptionkey")


def _text(value):
  if not isinstance(value, str):
    return "unknown"
  # Influx line protocol cannot carry literal newlines, including quoted fields.
  clean = " ".join(value.split())
  return clean.encode("utf-8")[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore") or "unknown"


def _identifier(value):
  return _text(value).replace("\\", "\\\\").replace(",", "\\,").replace("=", "\\=").replace(" ", "\\ ")


def _scalar(value):
  if type(value) is bool:
    return "true" if value else "false"
  if type(value) is int:
    return f"{value}i" if -(1 << 63) <= value < (1 << 63) else None
  if type(value) is float:
    return repr(value) if math.isfinite(value) else None
  if isinstance(value, str):
    return '"' + (_text(value) if value else "").replace("\\", "\\\\").replace('"', '\\"') + '"'
  return None


def _safe_setting(key):
  normalized = re.sub(r"[^a-z0-9]", "", key.lower())
  return (bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,159}", key)) and
          not normalized.startswith("vin") and not normalized.endswith("vin") and
          not any(name in normalized for name in SECRET_NAMES))


def _number(value):
  return type(value) in (int, float) and -(1 << 63) <= value < (1 << 63) and math.isfinite(value)


def _fields(snapshot, settings):
  supplied = snapshot.get("metrics")
  fields = {key: value for key, value in supplied.items() if key in METRICS} if isinstance(supplied, dict) else {}
  for key, value in list(fields.items()):
    if key in INT_FIELDS:
      valid = type(value) is int and _scalar(value) is not None
      if key == "software_major":
        valid = valid and 0 < value < 100
    elif key in FLOAT_FIELDS:
      valid = _number(value)
      if valid:
        fields[key] = float(value)
    elif key in STRING_FIELDS:
      valid = isinstance(value, str)
      if key == "commit":
        valid = valid and re.fullmatch(r"[0-9a-f]{40}", value) is not None
      elif key == "model_sha256":
        valid = valid and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    else:
      valid = type(value) is bool
    if not valid:
      fields.pop(key)
  fields.setdefault("event", 1)
  fields.setdefault("blocked_user", False)
  counters = snapshot.get("counters")
  if isinstance(counters, dict):
    direct = {"CurrentMonthsKilometers": "current_months_kilometers", "StarPilotDrives": "starpilot_drives",
              "AOLTime": "total_aol_seconds", "LateralTime": "total_lateral_seconds",
              "LongitudinalTime": "total_longitudinal_seconds", "TrackedTime": "total_tracked_seconds",
              "drives": "starpilot_drives", "total_aol_seconds": "total_aol_seconds",
              "total_lateral_seconds": "total_lateral_seconds", "total_longitudinal_seconds": "total_longitudinal_seconds",
              "total_tracked_seconds": "total_tracked_seconds"}
    for old, field in direct.items():
      value = counters.get(old)
      if _number(value) and value >= 0:
        fields.setdefault(field, int(value) if old in ("CurrentMonthsKilometers", "StarPilotDrives", "drives") else float(value))
    for old, field, factor in (("StarPilotSeconds", "starpilot_hours", 1 / 3600),
                               ("StarPilotMeters", "starpilot_miles", 0.000621371192237334),
                               ("seconds", "starpilot_hours", 1 / 3600),
                               ("meters", "starpilot_miles", 0.000621371192237334),
                               ("current_months_meters", "current_months_kilometers", 1 / 1000)):
      value = counters.get(old)
      if _number(value) and value >= 0:
        fields.setdefault(field, int(value * factor) if field == "current_months_kilometers" else value * factor)
  # Only the coarse geocoder's explicitly named output may carry coordinates.
  coarse = snapshot.get("coarse_location")
  if isinstance(coarse, dict):
    for key, limit in (("latitude", 90), ("longitude", 180)):
      value = coarse.get(key)
      if _number(value) and abs(value) <= limit:
        fields[key] = float(value)
  for key, value in sorted((key, value) for key, value in settings.items() if isinstance(key, str))[:MAX_SETTINGS]:
    if isinstance(key, str) and _safe_setting(key) and _scalar(value) is not None:
      field = key if key.startswith(("setting_", "default_", "valid_", "settings_")) else "setting_" + key
      fields[field] = value
  return fields


def build_payload(snapshot: dict, settings: dict, now_ns: int) -> bytes:
  if type(now_ns) is not int or not 0 < now_ns < (1 << 63):
    raise ValueError("invalid analytics timestamp")
  if not isinstance(snapshot, dict) or not isinstance(settings, dict):
    raise ValueError("invalid analytics snapshot")
  if len(settings) > MAX_SETTINGS:
    raise ValueError("analytics settings exceed limit")
  tags = dict(snapshot)
  device = _text(tags.get("device"))
  tags.setdefault("device_generation", {"tici": "C3", "tizi": "C3X", "mici": "C4"}.get(device, "unknown"))
  make = tags.get("car_make")
  if isinstance(make, str):
    tags["car_make"] = "GM" if make.lower() == "gm" else make.title()
  fields = _fields(snapshot, settings)
  encoded_fields = []
  for key, value in sorted(fields.items()):
    encoded = _scalar(value)
    if encoded is not None:
      encoded_fields.append(f"{_identifier(key)}={encoded}")
  encoded_tags = ",".join(f"{key}={_identifier(tags.get(key))}" for key in TAGS)
  lines = [f"user_stats,{encoded_tags} {','.join(encoded_fields)} {now_ns}"]
  commit = snapshot.get("branch_commit")
  if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit):
    lines.append(f"branch_commits,branch={_identifier(tags.get('branch'))} commit={_scalar(commit)} {now_ns}")
  payload = ("\n".join(lines) + "\n").encode("utf-8")
  if len(payload) > MAX_PAYLOAD:
    raise ValueError("analytics payload exceeds limit")
  return payload


class ReportClient:
  def __init__(self, token, *, session=None):
    self._token = self._valid_token(token)
    self._owned_session = session is None
    self._session = session if session is not None else requests.Session()

  def close(self):
    if self._owned_session:
      self._session.close()

  @staticmethod
  def _valid_token(token):
    return token if isinstance(token, str) and 0 < len(token) <= 4096 and all(33 <= ord(c) <= 126 for c in token) else None

  def send(self, payload: bytes, token=None, *, allowed=None) -> bool:
    credential = self._token if token is None else self._valid_token(token)
    if credential is None or not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_PAYLOAD:
      return False
    if credential != CLIENT_TOKEN and (credential.encode() in payload or _scalar(credential).encode() in payload):
      return False
    try:
      if allowed is not None and not allowed():
        return False
    except Exception:
      return False
    try:
      response = self._session.post(WRITE_URL, params={"org": "StarPilot", "bucket": "StarPilot", "precision": "ns"},
                                    headers={"Authorization": "Token " + credential, "Content-Type": "text/plain; charset=utf-8"},
                                    data=payload, timeout=(3, 10), allow_redirects=False, stream=True)
      try:
        return response.status_code == 204
      finally:
        response.close()
    except requests.RequestException:
      return False


def fetch_branch_commit(branch: str, *, session=None, allowed=None) -> str | None:
  if not isinstance(branch, str) or not branch or len(branch) > 128 or any(c.isspace() for c in branch) or ".." in branch:
    return None
  client = session if session is not None else requests.Session()
  try:
    try:
      if allowed is not None and not allowed():
        return None
    except Exception:
      return None
    response = client.get(COMMITS_URL + quote(branch, safe=""), timeout=(3, 5), allow_redirects=False, stream=True)
    try:
      if response.status_code != 200:
        return None
      data = bytearray()
      for chunk in response.iter_content(chunk_size=4096):
        try:
          if allowed is not None and not allowed():
            return None
        except Exception:
          return None
        data.extend(chunk)
        if len(data) > 65_536:
          return None
      value = json.loads(data)
      commit = value.get("sha") if isinstance(value, dict) else None
      return commit if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit) else None
    finally:
      response.close()
  except (requests.RequestException, ValueError):
    return None
  finally:
    if session is None:
      client.close()
