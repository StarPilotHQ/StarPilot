"""Phone identity and configuration locations, and identity validation.

The identity (phone certificate, its key, and the Google Automotive Link root used
to verify the head unit) is extracted from the user's own Android Auto app by
``apk_identity`` into ``IDENTITY_DIR``. It is never
committed, never logged, and the key must be readable only by its owner.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import stat
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

def _data_dir() -> Path:
  if override := os.environ.get("STARPORT_DIR"):
    return Path(override)
  # Honor the old override for existing test and deployment configurations.
  if override := os.environ.get("ANDROID_AUTO_DIR"):
    return Path(override)
  current, legacy = Path("/data/starport"), Path("/data/android_auto")
  if not current.exists() and legacy.exists():
    try:
      legacy.rename(current)
    except OSError:
      return current if current.exists() else legacy
  return current


DATA_DIR = _data_dir()
IDENTITY_DIR = DATA_DIR / "identity"
CONFIG_PATH = DATA_DIR / "config.json"
LOG_DIR = DATA_DIR / "logs"
CERT_NAME, KEY_NAME, ROOT_NAME = "phone-cert.pem", "phone-key.pem", "root-cert.pem"
EXPIRY_WARNING_DAYS = 14


@dataclass(frozen=True)
class Identity:
  cert: str
  key: str
  root: str | None
  expires: str
  days_left: int


class IdentityError(RuntimeError):
  pass


def _not_after(cert_path: Path) -> datetime | None:
  try:
    from cryptography import x509
    certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    if hasattr(certificate, "not_valid_after_utc"):
      return certificate.not_valid_after_utc
    return certificate.not_valid_after.replace(tzinfo=UTC)  # cryptography < 42, as on device
  except ImportError:
    pass
  try:
    decoded = ssl._ssl._test_decode_cert(str(cert_path))  # type: ignore[attr-defined]
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(decoded["notAfter"]), UTC)
  except Exception:
    return None


def load_identity(directory: Path | None = None, now: datetime | None = None) -> Identity:
  directory = directory or IDENTITY_DIR
  cert, key, root = directory / CERT_NAME, directory / KEY_NAME, directory / ROOT_NAME
  missing = [path.name for path in (cert, key) if not path.is_file()]
  if missing:
    raise IdentityError(f"Android Auto identity missing ({', '.join(missing)} in {directory}); " +
                        "add it in The Galaxy: Toggles → Android Auto → Android Auto Certificate")
  if stat.S_IMODE(key.stat().st_mode) & 0o077:
    raise IdentityError(f"{key} must not be readable by other users (chmod 600)")
  try:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    if root.is_file():
      context.load_verify_locations(cafile=str(root))
  except (ssl.SSLError, OSError) as error:
    raise IdentityError(f"Android Auto identity is unusable: {error}") from error
  expires = _not_after(cert)
  now = now or datetime.now(UTC)
  if expires is not None and expires <= now:
    raise IdentityError(f"Android Auto phone certificate expired on {expires.date()}; " +
                        "renew it in The Galaxy: Toggles → Android Auto → Android Auto Certificate")
  days_left = (expires - now).days if expires is not None else -1
  return Identity(str(cert), str(key), str(root) if root.is_file() else None,
                  expires.isoformat() if expires is not None else "unknown", days_left)


CONFIG_VERSION = 7
# Before config versions, every save wrote these defaults, which pinned projection
# at 12 fps / 4000 kbps. Unversioned files holding exactly them get today's defaults.
LEGACY_DEFAULTS = {"fps": 12, "bitrate_kbps": 4000}
# Before version 3 the car view could only publish RGBA, and every save wrote these off.
# They were never a choice, so older files get today's defaults for them.
PIPELINE_RESET = ("gpu_nv12", "async_readback")
# Before version 5 every save wrote verify_head_unit on, and some cars send certificates the root cannot parse.
# Older files get today's default (off).
VERIFY_RESET = ("verify_head_unit",)
# Before version 6 every save wrote the old default (off) for staying on home Wi-Fi.
DEDICATED_WIFI_RESET = ("dedicated_wifi_interface",)

CONNECTIONS = ("wireless", "wired", "headunitpad", "openheadunit")

DEFAULT_CONFIG = {
  "config_version": CONFIG_VERSION,
  "receiver_address": "",      # Bluetooth address of the paired head unit
  "receiver_name": "",
  "companion_address": "",    # explicitly selected bonded car used before a separate wireless adapter
  "companion_name": "",
  "rfcomm_channel": 0,         # 0 = discover through SDP (normal); set only to work around a broken SDP record
  "verify_head_unit": False,   # verify the car's certificate against root-cert.pem when present
  "connection": "wireless",    # "wireless": Bluetooth + the car's Wi-Fi; "wired": USB cable from the car to the comma's USB-C port;
                               # "headunitpad": an iPad running HeadunitPad on the Galaxy hotspot or same LAN connects to the comma
                               # "openheadunit": an Android tablet running Open Headunit does the same
  "headunitpad_port": 5277,    # HeadunitPad's "Headunit Server" port
  "usb_mode": "auto",          # wired: "auto" waits for the car's accessory handshake, then presents as an accessory itself;
                               # "handshake" waits for it only; "direct" presents as an accessory at once
  "view": "car",               # Separate StarPilot road view; legacy mirror has no source
  # ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: The encoder, FPS, bitrate, rate control, NV12, and readback defaults below work together.
  # Altering this can degrade video smoothness or introduce lag on Android Auto.
  # Do not change it autonomously; require an explicit user request specifically for this video optimization.
  "encoder": "auto",           # comma always requires hardware; desktop tests may select software
  "fps": 0,                    # 0 = automatic (30 with hardware, 15 with software); otherwise a cap, 5-30
  "bitrate_kbps": 6000,
  "rate_control": "cbr",       # hardware encoder: "cbr" holds the bitrate (easier on the car's Wi-Fi); "vbr" as before
  "gpu_nv12": True,            # car view: convert to the encoder's NV12 on the GPU (a third of the RGBA readback)
  "async_readback": True,      # car view: read frames back without stalling the renderer on the GPU
  "render_profile": True,      # car view: always-on sampling profile in logs/render_profile.txt (and .1.txt)
  "render_profile_kb": 256,    # size cap per render_profile file
  "wifi_interface": "wlan0",
  "dedicated_wifi_interface": True,   # join the car on its own aa0 station so wlan0 stays on home Wi-Fi
  "device_name": "StarPilot",
  "version_status": 0,         # WifiVersionResponse status (0 = success) for receivers that negotiate a version
  "phone_class": True,         # while pairing/connecting, present as a phone: HFP gateway + smartphone Class of Device
  "auto_connect": True,        # start projection on its own when the chosen car is on (onroad, or it reaches the comma)
  "rfcomm_cache": {},          # car address -> Android Auto RFCOMM channel learned over SDP, to skip discovery next time
  "modern_reply_cache": {},    # car address -> True when its v4+ head unit only started after the phone-style version reply
  "car_type": "",              # wireless setup shown in Galaxy: "builtin" or "adapter"; "" = work it out from the saved companion car
  "car_profile_override": "",  # "" = pick by fingerprint (a wireless adapter must choose); "default" = standard; otherwise a car_profiles id
  "car_fingerprint": {},       # what the chosen car's head unit reported last time, used to pick a profile before it connects
}


def load_config(path: Path | None = None) -> dict:
  path = path or CONFIG_PATH
  config: dict[str, Any] = dict(DEFAULT_CONFIG)
  try:
    stored = json.loads(path.read_text())
    if isinstance(stored, dict):
      version = stored.get("config_version")
      if not isinstance(version, int) or version < 2:
        stored = {key: value for key, value in stored.items() if LEGACY_DEFAULTS.get(key, object()) != value}
      if not isinstance(version, int) or version < 3:
        stored = {key: value for key, value in stored.items() if key not in PIPELINE_RESET}
      if not isinstance(version, int) or version < 5:
        stored = {key: value for key, value in stored.items() if key not in VERIFY_RESET}
      if not isinstance(version, int) or version < 6:
        stored = {key: value for key, value in stored.items() if key not in DEDICATED_WIFI_RESET}
      if not isinstance(version, int) or version < 7:  # adapter owners from before the adapter model choice keep connecting
        on_adapter = stored.get("car_type") == "adapter" or (not stored.get("car_type") and stored.get("companion_address"))
        if on_adapter and not stored.get("car_profile_override"):
          stored = {**stored, "car_profile_override": "default"}
      config.update({key: value for key, value in stored.items() if key in DEFAULT_CONFIG and isinstance(value, type(DEFAULT_CONFIG[key]))})
  except (OSError, ValueError):
    pass
  config["config_version"] = CONFIG_VERSION
  config["fps"] = 0 if int(config["fps"]) <= 0 else max(5, min(30, int(config["fps"])))
  if config["view"] not in ("car", "mirror"):
    config["view"] = "car"
  if config["connection"] not in CONNECTIONS:
    config["connection"] = "wireless"
  if not 1024 <= int(config["headunitpad_port"]) <= 65535:
    config["headunitpad_port"] = DEFAULT_CONFIG["headunitpad_port"]
  if config["car_type"] not in ("", "builtin", "adapter"):
    config["car_type"] = ""
  config["car_fingerprint"] = {str(k): str(v)[:80] for k, v in config["car_fingerprint"].items()}
  if config["usb_mode"] not in ("auto", "handshake", "direct"):
    config["usb_mode"] = "auto"
  config["bitrate_kbps"] = max(1000, min(12000, int(config["bitrate_kbps"])))
  if config["rate_control"] not in ("cbr", "vbr"):
    config["rate_control"] = "cbr"
  config["render_profile_kb"] = max(16, min(4096, int(config["render_profile_kb"])))
  config["rfcomm_cache"] = {str(address).upper(): channel for address, channel in config["rfcomm_cache"].items()
                            if type(channel) is int and 1 <= channel <= 30}
  config["modern_reply_cache"] = {str(address).upper(): True for address, modern in config["modern_reply_cache"].items()
                                  if modern is True}
  return config


def save_config(config: dict, path: Path | None = None) -> None:
  path = path or CONFIG_PATH
  path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
  data = json.dumps({key: config[key] for key in DEFAULT_CONFIG if key in config}, indent=2) + "\n"
  fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config-")
  try:
    with os.fdopen(fd, "w") as handle:
      handle.write(data)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
  except BaseException:
    try:
      os.unlink(temporary)
    except FileNotFoundError:
      pass
    raise


def expiry_warning(identity: Identity) -> str:
  if 0 <= identity.days_left <= EXPIRY_WARNING_DAYS:
    return f"Android Auto identity expires in {identity.days_left} days ({identity.expires[:10]}); renew it in The Galaxy"
  return ""


def certificate_days_left(directory: Path | None = None, now: datetime | None = None) -> int | None:
  """Whole days until the installed certificate expires (as ``Identity.days_left``), or None if unreadable.

  Reads only the certificate, never the key, so the read-only car view can call it.
  """
  cert = (directory or IDENTITY_DIR) / CERT_NAME
  try:
    expires = _not_after(cert) if cert.is_file() else None
  except (OSError, ValueError):
    return None
  return None if expires is None else (expires - (now or datetime.now(UTC))).days


def session_log_order(path: Path) -> tuple[int, str]:
  """Sort key for session logs, oldest first.

  Files are numbered, because the clock can read a date from months ago until it syncs;
  ordering by the timestamp in the name put the newest session first. Unnumbered files
  are from before numbering, so they are the oldest.
  """
  match = re.fullmatch(r"session-(\d{6})-.*\.jsonl", path.name)
  return (int(match[1]), path.name) if match else (-1, path.name)


def timestamp() -> str:
  return time.strftime("%Y%m%d-%H%M%S")
