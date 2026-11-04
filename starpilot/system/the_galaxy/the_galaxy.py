#!/usr/bin/env python3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from functools import wraps

import importlib
import math
import numbers
import os
import platform
import sys
import sysconfig
import tarfile

import io
import tokenize
from io import BytesIO
from pathlib import Path

import base64
import errno
import hashlib
import json
import re
import requests
import secrets
import selectors
import shutil
import signal
import subprocess
import numpy as np
from msgq.visionipc import VisionIpcClient, VisionStreamType
from PIL import Image
import threading
import time
import traceback
from urllib.parse import quote

from cereal import car, custom, log, messaging
from opendbc.can.parser import CANParser
from opendbc.car.gm.values import GMFlags
from opendbc.car.toyota.carcontroller import LOCK_CMD, UNLOCK_CMD
from opendbc.car.toyota.values import ToyotaStarPilotFlags
from openpilot.common.constants import CV
from openpilot.common.file_chunker import file_chunked_exists, get_chunk_name, get_manifest_path
from openpilot.common.params import ParamKeyFlag, ParamKeyType, Params
from openpilot.common.realtime import DT_HW
from openpilot.common.swaglog import cloudlog
from openpilot.common.time_helpers import system_time_valid
from openpilot.selfdrive.pandad.panda_firmware import firmware_flags_conflict, supports_tesla_can_wake, validate_tesla_can_wake_firmware
from openpilot.system.hardware import HARDWARE, PC
from openpilot.system.hardware.hw import Paths
from openpilot.system.loggerd.deleter import PRESERVE_ATTR_NAME, PRESERVE_ATTR_VALUE, PRESERVE_COUNT
from openpilot.system.version import get_build_metadata
from openpilot.tools.longitudinal_maneuvers.capabilities import get_longitudinal_maneuver_support
from panda import Panda

from openpilot.starpilot.assets.model_manager import (
  MODEL_LAB_DOWNLOAD_PARAM,
  canonical_model_key,
  disable_big_model_profile,
  external_gpu_available,
  get_model_profile,
  is_builtin_model_key,
  model_accelerator_artifact_filename,
  model_key_aliases,
  model_uses_external_gpu,
  set_model_profile,
)
from openpilot.starpilot.common.model_lab import (
  MODEL_LAB_CONFIG_PARAM,
  MODEL_LAB_RUNTIME_PARAM,
  is_small_model_metadata,
  model_lab_manifest_eligible,
  model_lab_pair_display_name,
  normalize_model_lab_config,
  validate_model_lab_selection,
)
from openpilot.starpilot.assets.theme_manager import HOLIDAY_THEME_PATH, THEME_COMPONENT_PARAMS
from openpilot.starpilot.common import param_profiles
from openpilot.starpilot.common.car_params_capability import capability_car_params_bytes
from openpilot.starpilot.system.the_galaxy import version_history, version_install
from openpilot.starpilot.common.accel_profile import (
  A_CRUISE_MAX_BP_CUSTOM,
  CUSTOM_ACCEL_PROFILE_BREAKPOINT_PARAM_KEYS,
  CUSTOM_ACCEL_PROFILE_BREAKPOINTS_INITIALIZED_KEY,
  CUSTOM_ACCEL_PROFILE_CURVE_PARAM_KEYS,
  CUSTOM_ACCEL_PROFILE_DEFAULT_BREAKPOINTS_MPH,
  CUSTOM_ACCEL_PROFILE_DEFAULT_POINT_COUNT,
  CUSTOM_ACCEL_PROFILE_INITIALIZED_KEY,
  CUSTOM_ACCEL_PROFILE_PARAM_KEYS,
  CUSTOM_ACCEL_PROFILE_POINT_COUNT_KEY,
  CUSTOM_ACCEL_PROFILE_POINT_VALUE_PARAM_KEYS,
  CUSTOM_ACCEL_PROFILE_VALUE_MAX,
  CUSTOM_ACCEL_PROFILE_VALUE_MIN,
  build_custom_accel_profile_defaults,
  custom_accel_profile_is_initialized,
  get_accel_profile_curve_values,
  get_custom_accel_profile_curve_defaults,
  interpolate_accel_profile,
  normalize_acceleration_profile,
  normalize_deceleration_profile,
  parse_custom_accel_profile_curve,
)
from openpilot.starpilot.common.maps_catalog import (
  MAPS_CATALOG,
  MAP_SCHEDULE_OPTIONS,
  get_selected_map_entries,
  sanitize_selected_locations_csv,
  schedule_label,
  schedule_param_value,
)
from openpilot.starpilot.common.maps_download_progress import (
  MAPS_STORAGE_CACHE_PARAM,
  load_maps_storage_cache,
  nonnegative_int,
  selection_key,
)
from openpilot.starpilot.common.experimental_state import sync_persist_chill_state, sync_persist_experimental_state
from openpilot.starpilot.system.the_galaxy.longitudinal_mode import (
  MODE_KEYS as LONGITUDINAL_MODE_KEYS, ModeError, WRITE_LOCK as LONGITUDINAL_MODE_LOCK,
  set_mode as set_longitudinal_mode, snapshot as longitudinal_mode_snapshot,
)
from openpilot.starpilot.common.favorite_slots import (
  FAVORITE_SLOTS_PARAM,
  SETTINGS_CATALOG_PATH,
  build_favorite_slot_options,
  filter_favorite_slot_options,
  get_favorite_values,
  is_favorite_action_key,
  load_settings_catalog,
  normalize_favorite_slots,
  trigger_favorite_action,
)
from openpilot.starpilot.common.lateral_delay import full_lateral_delay
from openpilot.starpilot.common.longitudinal_personality_profiles import (
  ACCELERATION_PRESETS,
  ACCELERATION_SPEEDS_MPH,
  BRAKING_PRESETS,
  BRAKING_SPEEDS_MPH,
  CURVE_BOUNDS,
  FOLLOWING_PRESETS,
  FOLLOWING_SPEEDS_MPH,
  PERSONALITY_PROFILES_PARAM,
  PERSONALITY_ADVANCED_PARAM_KEYS,
  PERSONALITY_FOLLOW_PARAM_KEYS,
  PERSONALITY_PARKED_PARAM_KEYS,
  PERSONALITY_PROFILE_ENABLE_PARAM_KEYS,
  PROFILE_SCHEMA_VERSION,
  default_personality_profiles,
  is_unconfigured_profile_document,
  initial_custom_curve,
  is_truck_fingerprint,
  migrate_profile_document,
  profile_document,
  strict_profile_document,
  synchronise_profile_document_enabled,
  update_personality_profile,
  validate_personality_advanced_value,
  validate_personality_follow_value,
)
from openpilot.starpilot.common.starpilot_utilities import delete_file, get_lock_status, run_cmd
from openpilot.starpilot.common.starpilot_variables import ACTIVE_THEME_PATH, BUTTON_FUNCTIONS, ERROR_LOGS_PATH, EXCLUDED_KEYS, LEGACY_STARPILOT_PARAM_RENAMES, MAPS_PATH, MODELS_PATH, RESOURCES_REPO, SCREEN_RECORDINGS_PATH, STOCK_THEME_PATH, THEME_SAVE_PATH, TOGGLE_BACKUPS,\
                                                           default_ev_tuning_enabled, migrate_cancel_button_controls, update_starpilot_toggles
from openpilot.starpilot.common.testing_grounds import (
  DEFAULT_TESTING_GROUND_VARIANT as SHARED_DEFAULT_TESTING_GROUND_VARIANT,
  TESTING_GROUND_VARIANT_LABELS as SHARED_TESTING_GROUND_VARIANT_LABELS,
  TESTING_GROUND_VARIANTS as SHARED_TESTING_GROUND_VARIANTS,
  TESTING_GROUNDS_SCHEMA_VERSION as SHARED_TESTING_GROUNDS_SCHEMA_VERSION,
  TESTING_GROUNDS_SLOT_DEFINITIONS as SHARED_TESTING_GROUNDS_SLOT_DEFINITIONS,
  TESTING_GROUNDS_STATE_PATH as SHARED_TESTING_GROUNDS_STATE_PATH,
)
from openpilot.starpilot.navigation.destination_store import normalize_destination_payload, routing_configured, update_recent_destinations
from openpilot.starpilot.system.the_galaxy.factory_reset import remove_path as _run_factory_reset_delete
from openpilot.starpilot.system.the_galaxy import flm_workspace, utilities
from openpilot.starpilot.system.the_galaxy.update_recovery import inspect_interrupted_update, public_recovery_status, recover_interrupted_update
from openpilot.starpilot.system.bluetooth import BluetoothClient
from openpilot.starpilot.system.wheel_controls import (
  CONTROLLER_ACTION_OPTIONS,
  CONTROLLER_ACTION_SET_SPEED,
  CONTROLLER_ACTION_SLOT_COUNT,
  FAVORITE_SLOT_COUNT,
  cancel_learning as cancel_wheel_control_learning,
  clear_mappings as clear_wheel_control_mappings,
  controller_speed_bounds,
  delete_mapping as delete_wheel_control_mapping,
  load_controller_action_slots,
  public_status as wheel_control_status,
  set_controller_action_slot,
  set_joystick_device,
  start_learning as start_wheel_control_learning,
  start_testing as start_wheel_control_testing,
  stop_testing as stop_wheel_control_testing,
)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
# Keep Galaxy independent of opendbc's generated car bindings while matching RivianFlags.ANGLE_HARNESS.
RIVIAN_ANGLE_HARNESS_FLAG = 1 << 0

GITLAB_API = "https://gitlab.com/api/v4"
GITLAB_SUBMISSIONS_PROJECT_ID = "71992109"
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")
LEGACY_LATERAL_METHOD_API_PREFIX = "/api/" + "".join(("f", "t", "m"))
VASM_CONFIGURATION_KEYS = {"VASMEnabled", "VASMConfidenceThreshold", "VASMSmoothSeconds", "VASMAnnotationConfig"}
PIP_PREVIEW_CONFIGURATION_KEYS = {"PIPPreviewEnabled", "PIPPreviewMask", "PIPPreviewShowOnBlinker", "PIPPreviewShowOnBSM", "PIPPreviewInvert"}
MODEL_SMOOTHING_KEYS = {"LatSmoothSeconds", "LongSmoothSeconds"}
GALAXY_DEVELOPER_ONLY_KEYS = {"TurnSteeringLimitMuteSpeed"}
PULSE_GLIDE_BUTTON_KEYS = {
  "CancelButtonControl", "DistanceButtonControl",
  "LongCancelButtonControl", "LongDistanceButtonControl",
  "VeryLongCancelButtonControl", "VeryLongDistanceButtonControl",
  "LKASButtonControl", "ModeButtonControl", "LongModeButtonControl", "VeryLongModeButtonControl",
  "StarButtonControl", "LongStarButtonControl", "VeryLongStarButtonControl",
}
SENTRY_NUMERIC_PARAM_BOUNDS = {
  "SentryModeSensitivity": (0.005, 1.0),
  "SentryModeWarningTime": (0.1, 10.0),
}
SENTRY_NOTIFICATION_RATE_LIMIT_SECONDS = 180.0

GALAXY_DEPS_PATH = "/data/galaxy_deps"
LEGACY_GALAXY_DEPS_PATH = "/data/" + "".join(chr(code) for code in (112, 111, 110, 100)) + "_deps"
GALAXY_DEPS_PATHS = (GALAXY_DEPS_PATH, LEGACY_GALAXY_DEPS_PATH)


def _galaxy_runtime_dependency_paths() -> tuple[str, ...]:
  """Return existing dependency locations used by Galaxy on-device and in builds."""
  repo_root = REPO_THIRD_PARTY_PATH.parent.parent
  candidates = [
    sysconfig.get_paths().get("purelib", ""),
    "/usr/local/venv/lib/python3.12/site-packages",
  ]

  is_arm = platform.machine().lower() in ("aarch64", "arm64")
  venv_names = (".venv-linux-arm64", ".venv") if is_arm else (".venv",)
  for venv_name in venv_names:
    venv_path = repo_root / venv_name / "lib"
    if venv_path.is_dir():
      candidates.extend(str(path) for path in venv_path.glob("python*/site-packages"))

  return tuple(dict.fromkeys(path for path in candidates if path and os.path.isdir(path)))


REPO_THIRD_PARTY_PATH = Path(__file__).resolve().parents[2] / "third_party"
GALAXY_RUNTIME_DEPENDENCY_PATHS = _galaxy_runtime_dependency_paths()
for deps_path in GALAXY_DEPS_PATHS:
  if os.path.isdir(deps_path) and deps_path not in sys.path:
    sys.path.insert(0, deps_path)

for deps_path in GALAXY_RUNTIME_DEPENDENCY_PATHS:
  if os.path.isdir(deps_path) and deps_path not in sys.path:
    sys.path.append(deps_path)

if REPO_THIRD_PARTY_PATH.is_dir() and str(REPO_THIRD_PARTY_PATH) not in sys.path:
  sys.path.insert(0, str(REPO_THIRD_PARTY_PATH))

Flask = None
Response = None
jsonify = None
make_response = None
render_template = None
request = None
send_file = None
send_from_directory = None

_GALAXY_WEB_DEPS_READY = False
_GALAXY_WEB_DEPS_ERROR = None

_TESTING_GROUND_CUSTOM_RESERVED_SERVICE = "customReserved9"
_TESTING_GROUND_CUSTOM_RESERVED_INTERVAL_S = 15.0
_TESTING_GROUND_CUSTOM_RESERVED_PM = None
_TESTING_GROUND_CUSTOM_RESERVED_LOCK = threading.Lock()
_TESTING_GROUND_CUSTOM_RESERVED_LAST_PUBLISH_MONO = 0.0
PANDA_FIRMWARE_TOGGLE_KEYS = {"IgnoreIgnitionLine", "RemoteStartBootsComma", "HKGRemoteStartBootsComma", "TeslaWakeOnCAN"}
PANDA_FIRMWARE_CONFIRMATION_FIELD = "confirmedPandaFirmwareFlash"
_PANDA_FLASH_REBOOT_LOCK = threading.Lock()


def _flash_panda_then_reboot() -> None:
  with _PANDA_FLASH_REBOOT_LOCK:
    params_memory.put_bool("FlashPanda", True)
    while params_memory.get_bool("FlashPanda"):
      time.sleep(0.1)
    HARDWARE.reboot()


def _is_comma_device_runtime() -> bool:
  """Robust runtime device check.

  `PC` is derived from `/TICI`, which can be missing in edge boot/update states.
  For Galaxy routing we must keep on-device Galaxy on 8082.
  """
  if not PC:
    return True

  if os.path.isfile("/TICI") or os.path.isfile("/AGNOS"):
    return True

  model_path = "/sys/firmware/devicetree/base/model"
  try:
    with open(model_path) as f:
      model = f.read().strip("\x00").lower()
    return "comma " in model
  except Exception:
    return False


def _get_param_key_type(params_obj, key):
  getter = getattr(params_obj, "get_key_type", None)
  if getter is None:
    getter = getattr(params_obj, "get_type", None)
  if getter is None:
    return ParamKeyType.STRING
  return getter(key)


def _import_galaxy_web_symbols():
  global Flask, Response, jsonify, make_response, render_template, request, send_file, send_from_directory, _GALAXY_WEB_DEPS_ERROR

  try:
    from flask import Flask as _Flask
    from flask import Response as _Response
    from flask import jsonify as _jsonify
    from flask import make_response as _make_response
    from flask import render_template as _render_template
    from flask import request as _request
    from flask import send_file as _send_file
    from flask import send_from_directory as _send_from_directory
  except ModuleNotFoundError as error:
    _GALAXY_WEB_DEPS_ERROR = error
    return False

  Flask = _Flask
  Response = _Response
  jsonify = _jsonify
  make_response = _make_response
  render_template = _render_template
  request = _request
  send_file = _send_file
  send_from_directory = _send_from_directory
  _GALAXY_WEB_DEPS_ERROR = None
  return True


def _install_galaxy_web_deps():
  global _GALAXY_WEB_DEPS_ERROR

  if not _is_comma_device_runtime():
    return False

  # Local-only dependency policy: prefer bundled repo deps, then existing local deps.
  # Do not hit pip/network at runtime.
  if REPO_THIRD_PARTY_PATH.is_dir() and str(REPO_THIRD_PARTY_PATH) not in sys.path:
    sys.path.insert(0, str(REPO_THIRD_PARTY_PATH))
  for deps_path in GALAXY_DEPS_PATHS:
    if os.path.isdir(deps_path) and deps_path not in sys.path:
      sys.path.insert(0, deps_path)

  importlib.invalidate_caches()
  if _import_galaxy_web_symbols():
    return True

  _GALAXY_WEB_DEPS_ERROR = RuntimeError(
    "Missing local Flask deps (expected in starpilot/third_party or local Galaxy deps)."
  )
  return False


def _ensure_galaxy_web_deps():
  global _GALAXY_WEB_DEPS_READY

  if _GALAXY_WEB_DEPS_READY:
    return True
  if _import_galaxy_web_symbols():
    _GALAXY_WEB_DEPS_READY = True
    return True
  if _install_galaxy_web_deps() and _import_galaxy_web_symbols():
    _GALAXY_WEB_DEPS_READY = True
    return True
  return False


def extract_tar(archive_path, destination):
  destination_path = Path(destination).resolve()

  with tarfile.open(archive_path, "r:gz") as tar:
    for member in tar.getmembers():
      member_path = (destination_path / member.name).resolve()
      if destination_path not in member_path.parents and member_path != destination_path:
        raise RuntimeError(f"Unsafe tar member path: {member.name}")
    tar.extractall(destination_path)

class ParamsCompat:
  MODEL_KEY_ALIASES = {
    "Model": "DrivingModel",
    "ModelVersion": "DrivingModelVersion",
    "SecOCKey": "SecOCKeys",
  }
  MIRRORED_PARAM_GROUPS = {
    "Model": ("Model", "DrivingModel"),
    "DrivingModel": ("Model", "DrivingModel"),
    "ModelVersion": ("ModelVersion", "DrivingModelVersion"),
    "DrivingModelVersion": ("ModelVersion", "DrivingModelVersion"),
  }

  def __init__(self, params_obj):
    self._params = params_obj

  def _key(self, key):
    return self.MODEL_KEY_ALIASES.get(key, key)

  @staticmethod
  def _to_text(value):
    if value is None:
      return ""
    if isinstance(value, bytes):
      return value.decode("utf-8", errors="replace")
    return str(value)

  def _default_text(self, key):
    try:
      return self._to_text(self._params.get_default_value(key)).strip()
    except Exception:
      return ""

  def _get_raw(self, key, block=False):
    try:
      return self._params.get(key, block=block)
    except TypeError:
      try:
        return self._params.get(key)
      except Exception:
        return None
    except Exception:
      return None

  def _resolve_mirrored_text(self, primary_key, secondary_key, block=False):
    primary_raw = self._get_raw(primary_key, block=block)
    secondary_raw = self._get_raw(secondary_key, block=block)

    if primary_raw is None and secondary_raw is None:
      return None

    primary_val = self._to_text(primary_raw).strip()
    secondary_val = self._to_text(secondary_raw).strip()

    if primary_val == secondary_val:
      return secondary_val or primary_val

    primary_default = self._default_text(primary_key)
    secondary_default = self._default_text(secondary_key)
    primary_non_default = bool(primary_val) and primary_val != primary_default
    secondary_non_default = bool(secondary_val) and secondary_val != secondary_default

    if secondary_non_default and not primary_non_default:
      return secondary_val
    if primary_non_default and not secondary_non_default:
      return primary_val

    # Prefer DrivingModel* values on conflicts since runtime reads those keys.
    return secondary_val or primary_val

  def _put_single(self, key, value):
    expected_type = _get_param_key_type(self._params, key)

    typed_value = value
    if expected_type == ParamKeyType.BOOL:
      if isinstance(value, bool):
        typed_value = value
      elif isinstance(value, (int, float)):
        typed_value = value != 0
      else:
        typed_value = str(value).strip().lower() in ("1", "true", "yes", "on")
    elif expected_type == ParamKeyType.INT:
      typed_value = int(float(value)) if value not in (None, "", b"") else 0
    elif expected_type == ParamKeyType.FLOAT:
      typed_value = float(value) if value not in (None, "", b"") else 0.0
    elif expected_type == ParamKeyType.STRING:
      if isinstance(value, bytes):
        typed_value = value.decode("utf-8", errors="replace")
      elif value is None:
        typed_value = ""
      else:
        typed_value = str(value)
    elif expected_type == ParamKeyType.JSON:
      if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")

      if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
          typed_value = self._params.get_default_value(key)
        else:
          typed_value = json.loads(stripped)
      elif isinstance(value, tuple):
        typed_value = list(value)
      else:
        typed_value = value

    self._params.put(key, typed_value)

  @staticmethod
  def _coerce_legacy(value, encoding=None):
    # Preserve legacy Params.get behavior:
    # - no encoding => bytes-like payload
    # - encoding set => decoded string payload
    if isinstance(value, bytes):
      if encoding is None:
        return value
      return value.decode(encoding, errors="replace")

    if isinstance(value, (dict, list)):
      serialized = json.dumps(value, separators=(",", ":"))
      if encoding is None:
        return serialized.encode("utf-8")
      return serialized

    if isinstance(value, str):
      if encoding is None:
        return value.encode("utf-8")
      return value

    if isinstance(value, (bool, int, float)):
      text = str(value)
      if encoding is None:
        return text.encode("utf-8")
      return text

    return value

  def get(self, key, encoding=None, default=None, block=False):
    mirrored_keys = self.MIRRORED_PARAM_GROUPS.get(key)
    if mirrored_keys:
      value = self._resolve_mirrored_text(mirrored_keys[0], mirrored_keys[1], block=block)
      if value is None:
        return default
      return self._coerce_legacy(value, encoding)

    resolved_key = self._key(key)
    value = self._get_raw(resolved_key, block=block)

    if value is None:
      return default
    return self._coerce_legacy(value, encoding)

  def get_bool(self, key):
    return self._params.get_bool(self._key(key))

  def put(self, key, value):
    mirrored_keys = self.MIRRORED_PARAM_GROUPS.get(key)
    if mirrored_keys:
      for mirrored_key in dict.fromkeys(mirrored_keys):
        self._put_single(mirrored_key, value)
      return

    self._put_single(self._key(key), value)

  def put_bool(self, key, value):
    if key == "LeadIndicator":
      enabled = bool(value)
      self._params.put_bool("LeadIndicator", enabled)
      self._params.put_bool("HideLeadMarker", not enabled)
      return

    if key == "HideLeadMarker":
      hidden = bool(value)
      self._params.put_bool("HideLeadMarker", hidden)
      self._params.put_bool("LeadIndicator", not hidden)
      return

    self._params.put_bool(self._key(key), bool(value))

  def remove(self, key):
    mirrored_keys = self.MIRRORED_PARAM_GROUPS.get(key)
    if mirrored_keys:
      for mirrored_key in dict.fromkeys(mirrored_keys):
        self._params.remove(mirrored_key)
      return

    self._params.remove(self._key(key))

  def __getattr__(self, attr):
    return getattr(self._params, attr)

_params_raw = Params(return_defaults=True)
_params_live_raw = Params()
_params_memory_raw = Params(memory=True)

def _normalize_default_value(value):
  if isinstance(value, bytes):
    try:
      return value.decode("utf-8")
    except Exception:
      return value
  return value

def _sanitize_json_value(value):
  if value is None or isinstance(value, bool):
    return value

  if isinstance(value, dict):
    return {key: _sanitize_json_value(inner_value) for key, inner_value in value.items()}

  if isinstance(value, (list, tuple)):
    return [_sanitize_json_value(item) for item in value]

  if isinstance(value, datetime):
    return value.isoformat()

  if isinstance(value, bytes):
    try:
      return value.decode("utf-8")
    except Exception:
      return value.decode("utf-8", errors="replace")

  if isinstance(value, numbers.Integral):
    return int(value)

  # Flask emits invalid JSON for NaN/inf, so normalize them before jsonify.
  if isinstance(value, numbers.Real):
    numeric_value = float(value)
    return numeric_value if math.isfinite(numeric_value) else None

  return value

def _build_default_params():
  defaults = []
  for raw_key in _params_raw.all_keys():
    key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
    defaults.append((
      key,
      _normalize_default_value(_params_raw.get_default_value(raw_key)),
      _get_param_key_type(_params_raw, raw_key),
      _params_raw.get_tuning_level(raw_key),
    ))
  return defaults

starpilot_default_params = _build_default_params()


def _sentry_event_roots() -> tuple[Path, ...]:
  roots = [Path("/data/media/0/sentryd")]
  if PC:
    roots.insert(0, Path(Paths.comma_home()) / "starpilot" / "data" / "sentryd")
  return tuple(root.resolve() for root in roots)


_SENTRY_EVENT_INDEX_NAME = "events.json"
_SENTRY_EVENT_INDEX_LOCK = threading.Lock()


def _sentry_event_index_path() -> Path:
  return _sentry_event_roots()[0] / _SENTRY_EVENT_INDEX_NAME


def _load_sentry_event_catalog_unlocked() -> list[dict]:
  index_path = _sentry_event_index_path()
  try:
    raw_events = json.loads(index_path.read_text())
  except (OSError, TypeError, ValueError, json.JSONDecodeError):
    return []

  if not isinstance(raw_events, list):
    return []

  events = []
  for raw_event in raw_events:
    event = _normalize_sentry_event(raw_event)
    if event is not None:
      events.append(event)
  return events


def _save_sentry_event_catalog_unlocked(events: list[dict]) -> None:
  index_path = _sentry_event_index_path()
  index_path.parent.mkdir(parents=True, exist_ok=True)
  temporary_path = index_path.with_suffix(".tmp")
  temporary_path.write_text(json.dumps(events, separators=(",", ":")))
  temporary_path.chmod(0o600)
  temporary_path.replace(index_path)


def _stored_sentry_event() -> dict | None:
  raw_event = params.get("SentryModeLastEvent", encoding="utf-8") or "{}"
  try:
    payload = raw_event if isinstance(raw_event, dict) else json.loads(raw_event)
  except (TypeError, ValueError, json.JSONDecodeError):
    return None
  return _normalize_sentry_event(payload)


def _discover_legacy_sentry_events(known_event_ids: set[str]) -> list[dict]:
  discovered = []
  for root in _sentry_event_roots():
    try:
      directories = sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
      )
    except OSError:
      continue

    for directory in directories:
      event_id = directory.name
      if event_id == _SENTRY_LIVE_EVENT_ID or event_id in known_event_ids or len(event_id) > 96:
        continue

      image_paths = [
        str(path) for path in (directory / "wide.jpg", directory / "driver.jpg")
        if path.is_file()
      ]
      if not image_paths:
        continue

      try:
        detected_at = datetime.fromtimestamp(directory.stat().st_mtime, timezone.utc).isoformat()
      except OSError:
        detected_at = ""
      is_test = event_id.startswith("test-")
      discovered.append({
        "eventId": event_id,
        "kind": "alarm" if is_test else "warning",
        "detectedAt": detected_at,
        "message": "Test sentry event." if is_test else "Movement detected while parked.",
        "imagePaths": image_paths,
      })
      known_event_ids.add(event_id)
  return discovered


def _sentry_event_catalog() -> list[dict]:
  with _SENTRY_EVENT_INDEX_LOCK:
    events = _load_sentry_event_catalog_unlocked()
    known_event_ids = {event["eventId"] for event in events}
    legacy_events = _discover_legacy_sentry_events(known_event_ids)
    if legacy_events:
      events.extend(legacy_events)
    latest_event = _stored_sentry_event()
    if latest_event is not None and latest_event["eventId"] not in known_event_ids:
      events.insert(0, latest_event)
      _save_sentry_event_catalog_unlocked(events)
    elif legacy_events:
      _save_sentry_event_catalog_unlocked(events)
    return events


def _record_sentry_event(event: dict) -> None:
  with _SENTRY_EVENT_INDEX_LOCK:
    events = _load_sentry_event_catalog_unlocked()
    known_event_ids = {existing["eventId"] for existing in events}
    events.extend(_discover_legacy_sentry_events(known_event_ids))
    events = [existing for existing in events if existing.get("eventId") != event["eventId"]]
    events.insert(0, event)
    _save_sentry_event_catalog_unlocked(events)


def _safe_sentry_image_paths(raw_paths) -> list[str]:
  if not isinstance(raw_paths, list):
    return []

  roots = _sentry_event_roots()
  safe_paths = []
  for raw_path in raw_paths:
    try:
      path = Path(str(raw_path)).resolve()
      if path.is_file() and any(path.is_relative_to(root) for root in roots):
        safe_paths.append(str(path))
    except (OSError, TypeError, ValueError):
      continue
  return safe_paths


def _normalize_sentry_event(payload) -> dict | None:
  if not isinstance(payload, dict):
    return None

  event_id = str(payload.get("eventId") or "").strip()
  kind = str(payload.get("kind") or "").strip().lower()
  if not event_id or kind not in {"warning", "alarm", "power_off", "selfie"}:
    return None

  event = {
    "eventId": event_id[:96],
    "kind": kind,
    "detectedAt": str(payload.get("detectedAt") or ""),
    "message": str(payload.get("message") or "Movement detected while parked.")[:500],
    "imagePaths": _safe_sentry_image_paths(payload.get("imagePaths")),
  }
  reason = str(payload.get("reason") or "").strip()
  if reason:
    event["reason"] = reason[:96]
  return event


def _sentry_image_path(event_id: str, filename: str) -> Path | None:
  if not event_id or Path(event_id).name != event_id:
    return None
  if filename not in {"wide.jpg", "driver.jpg"} or Path(filename).name != filename:
    return None

  for root in _sentry_event_roots():
    path = (root / event_id / filename).resolve()
    if root in path.parents and path.is_file():
      return path
  return None


def _public_sentry_event(event: dict) -> dict:
  public_event = dict(event)
  public_event.pop("imagePaths", None)
  public_event["imageUrls"] = []
  event_id = str(public_event.get("eventId") or "")
  for raw_path in event.get("imagePaths", []):
    path = Path(str(raw_path)).resolve()
    if path.parent.name != event_id:
      continue
    if _sentry_image_path(event_id, path.name) == path:
      public_event["imageUrls"].append(
        f"/api/sentry/images/{quote(event_id, safe='')}/{quote(path.name, safe='')}"
      )
  return public_event


def _capture_sentry_test_images(event_id: str) -> list[str]:
  from openpilot.system.camerad.snapshot import jpeg_write, snapshot

  params.put_bool("SentryModeCapture", True)
  try:
    rear, front = snapshot(allow_existing=True)
  except Exception:
    cloudlog.exception("Galaxy: sentry test snapshot failed")
    return []
  finally:
    params.put_bool("SentryModeCapture", False)

  if rear is None and front is None:
    return []

  directory = _sentry_event_roots()[0] / event_id
  directory.mkdir(parents=True, exist_ok=True)
  paths = []
  if rear is not None:
    path = directory / "wide.jpg"
    jpeg_write(str(path), rear)
    paths.append(str(path))
  if front is not None:
    path = directory / "driver.jpg"
    jpeg_write(str(path), front)
    paths.append(str(path))
  return paths


_SENTRY_LIVE_CAPTURE_LOCK = threading.Lock()
_SENTRY_LIVE_EVENT_ID = "live"


def _capture_sentry_live_images() -> list[str]:
  from openpilot.system.camerad.snapshot import jpeg_write, snapshot

  params.put_bool("SentryModeCapture", True)
  try:
    rear, front = snapshot(allow_existing=True, include_front=True)
  except Exception:
    cloudlog.exception("Galaxy: live Sentry snapshot failed")
    return []
  finally:
    params.put_bool("SentryModeCapture", False)

  if rear is None and front is None:
    return []

  directory = _sentry_event_roots()[0] / _SENTRY_LIVE_EVENT_ID
  directory.mkdir(parents=True, exist_ok=True)
  paths = []
  if rear is not None:
    path = directory / "wide.jpg"
    jpeg_write(str(path), rear)
    paths.append(str(path))
  if front is not None:
    path = directory / "driver.jpg"
    jpeg_write(str(path), front)
    paths.append(str(path))
  return paths


def _get_live_driver_jpeg():
  from openpilot.system.manager.process_config import managed_processes
  started = False
  try:
    try:
      subprocess.check_call(["pgrep", "camerad"])
    except subprocess.CalledProcessError:
      managed_processes['camerad'].start()
      started = True

    client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_DRIVER, True)
    if not client.connect(True):
      return None

    if started:
      settle_deadline = time.monotonic() + 4.0
      while time.monotonic() < settle_deadline:
        client.recv(timeout_ms=100)

    buf = client.recv(timeout_ms=5000)
    if buf is None:
      return None

    y = np.array(buf.data[:buf.uv_offset], dtype=np.uint8).reshape((-1, buf.stride))[:buf.height, :buf.width]
    u = np.array(buf.data[buf.uv_offset::2], dtype=np.uint8).reshape((-1, buf.stride // 2))[:buf.height // 2, :buf.width // 2]
    v = np.array(buf.data[buf.uv_offset + 1::2], dtype=np.uint8).reshape((-1, buf.stride // 2))[:buf.height // 2, :buf.width // 2]

    ul = np.repeat(np.repeat(u, 2).reshape(u.shape[0], y.shape[1]), 2, axis=0).reshape(y.shape)
    vl = np.repeat(np.repeat(v, 2).reshape(v.shape[0], y.shape[1]), 2, axis=0).reshape(y.shape)

    yuv = np.dstack((y, ul, vl)).astype(np.int16)
    yuv[:, :, 1:] -= 128

    m = np.array([
      [1.00000, 1.00000, 1.00000],
      [0.00000, -0.39465, 2.03211],
      [1.13983, -0.58060, 0.00000],
    ])
    rgb = np.dot(yuv, m).clip(0, 255).astype(np.uint8)

    img = Image.fromarray(rgb)
    buf_io = BytesIO()
    img.save(buf_io, format="JPEG", quality=85)
    return buf_io.getvalue()
  except Exception:
    return None
  finally:
    if started:
      managed_processes['camerad'].stop()


_SENTRY_PUSH_LOCK = threading.Lock()
_SENTRY_NOTIFICATION_RATE_LIMIT_LOCK = threading.Lock()
_SENTRY_NOTIFICATION_LAST_AT: float | None = None
_SENTRY_PUSH_PRIVATE_KEY_NAME = "sentry_vapid_private.pem"
_SENTRY_PUSH_SUBSCRIPTIONS_NAME = "sentry_push_subscriptions.json"
_SENTRY_PUSH_SUBJECT = os.getenv("STARPILOT_VAPID_SUBJECT", "mailto:galaxy@firestar.link")


def _sentry_push_paths() -> tuple[Path, Path]:
  galaxy_dir = _get_galaxy_dir()
  return galaxy_dir / _SENTRY_PUSH_PRIVATE_KEY_NAME, galaxy_dir / _SENTRY_PUSH_SUBSCRIPTIONS_NAME


def _load_sentry_push_subscriptions() -> list[dict]:
  _, subscriptions_path = _sentry_push_paths()
  try:
    payload = json.loads(subscriptions_path.read_text())
  except (OSError, TypeError, ValueError, json.JSONDecodeError):
    return []

  if not isinstance(payload, list):
    return []
  return [subscription for subscription in payload if isinstance(subscription, dict)]


def _save_sentry_push_subscriptions(subscriptions: list[dict]) -> None:
  _, subscriptions_path = _sentry_push_paths()
  subscriptions_path.parent.mkdir(parents=True, exist_ok=True)
  temporary_path = subscriptions_path.with_suffix(".tmp")
  temporary_path.write_text(json.dumps(subscriptions, separators=(",", ":")))
  temporary_path.chmod(0o600)
  temporary_path.replace(subscriptions_path)


def _normalize_sentry_push_subscription(payload) -> dict | None:
  if not isinstance(payload, dict):
    return None

  subscription = payload.get("subscription", payload)
  if not isinstance(subscription, dict):
    return None

  endpoint = str(subscription.get("endpoint") or "").strip()
  keys = subscription.get("keys")
  if not endpoint.startswith("https://") or len(endpoint) > 4096 or not isinstance(keys, dict):
    return None

  p256dh = str(keys.get("p256dh") or "").strip()
  auth = str(keys.get("auth") or "").strip()
  if not p256dh or not auth or len(p256dh) > 512 or len(auth) > 512:
    return None

  return {
    "endpoint": endpoint,
    "expirationTime": subscription.get("expirationTime"),
    "keys": {"p256dh": p256dh, "auth": auth},
  }


def _get_sentry_vapid():
  try:
    from py_vapid import Vapid
  except ModuleNotFoundError as error:
    raise RuntimeError("pywebpush is not installed") from error

  with _SENTRY_PUSH_LOCK:
    private_key_path, _ = _sentry_push_paths()
    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    if private_key_path.is_file():
      try:
        if private_key_path.stat().st_size > 0:
          return Vapid.from_file(str(private_key_path))
      except Exception as error:
        cloudlog.warning("Galaxy: Existing Sentry VAPID private key was invalid, regenerating: %s", error)

    vapid = Vapid()
    vapid.generate_keys()
    temporary_path = private_key_path.with_suffix(".tmp")
    vapid.save_key(str(temporary_path))
    temporary_path.chmod(0o600)
    temporary_path.replace(private_key_path)
    return vapid


def _sentry_vapid_public_key(vapid) -> str:
  from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

  raw_key = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
  return base64.urlsafe_b64encode(raw_key).rstrip(b"=").decode("ascii")


def _sentry_push_subscription_count() -> int:
  with _SENTRY_PUSH_LOCK:
    return len(_load_sentry_push_subscriptions())


def _sentry_public_base_url() -> str:
  configured_url = os.getenv("STARPILOT_GALAXY_PUBLIC_URL", "").strip().rstrip("/")
  if configured_url:
    return configured_url

  slug = _read_galaxy_text(_get_galaxy_dir() / "glxyslug")
  return f"https://galaxy.firestar.link/{slug}" if slug else ""


def _sentry_external_image_urls(event: dict) -> list[str]:
  base_url = _sentry_public_base_url()
  if not base_url:
    return []

  public_event = _public_sentry_event(event)
  return [f"{base_url}{image_url}" for image_url in public_event["imageUrls"]]


def _sentry_first_image(event: dict) -> tuple[str, bytes] | None:
  for raw_path in event.get("imagePaths", []):
    path = Path(str(raw_path))
    try:
      return path.name, path.read_bytes()
    except OSError:
      continue
  return None


def _sentry_notification_channels() -> dict[str, bool]:
  return {
    "webPush": _sentry_push_subscription_count() > 0,
    "webhook": bool((params.get("SentryModeWebhook", encoding="utf-8") or "").strip()),
    "ntfy": bool((params.get("SentryModeNtfyUrl", encoding="utf-8") or "").strip()),
  }


def _sentry_notification_rate_limit_path() -> Path:
  return _get_galaxy_dir() / "sentry_notification_rate_limit.json"


def _load_sentry_notification_last_at() -> float | None:
  try:
    payload = json.loads(_sentry_notification_rate_limit_path().read_text())
    value = float(payload.get("lastNotificationAt")) if isinstance(payload, dict) else None
  except (OSError, TypeError, ValueError, json.JSONDecodeError):
    return None
  return value if value is not None and math.isfinite(value) else None


def _claim_sentry_notification_slot(event: dict) -> bool:
  """Reserve the shared notification slot for a real Sentry event."""
  global _SENTRY_NOTIFICATION_LAST_AT

  now = time.time()
  with _SENTRY_NOTIFICATION_RATE_LIMIT_LOCK:
    persisted_last_at = _load_sentry_notification_last_at()
    last_at = max(
      (value for value in (_SENTRY_NOTIFICATION_LAST_AT, persisted_last_at) if value is not None),
      default=None,
    )
    if last_at is not None:
      elapsed = max(0.0, now - last_at)
      if elapsed < SENTRY_NOTIFICATION_RATE_LIMIT_SECONDS:
        remaining = SENTRY_NOTIFICATION_RATE_LIMIT_SECONDS - elapsed
        cloudlog.info(
          "Galaxy: Sentry notification suppressed by rate limit (%.0f seconds remaining; event=%s)",
          remaining,
          event.get("eventId", ""),
        )
        return False

    _SENTRY_NOTIFICATION_LAST_AT = now
    rate_limit_path = _sentry_notification_rate_limit_path()
    temporary_path = rate_limit_path.with_suffix(".tmp")
    try:
      rate_limit_path.parent.mkdir(parents=True, exist_ok=True)
      temporary_path.write_text(json.dumps({
        "lastNotificationAt": now,
        "eventId": str(event.get("eventId") or ""),
      }, separators=(",", ":")))
      temporary_path.chmod(0o600)
      temporary_path.replace(rate_limit_path)
    except OSError:
      cloudlog.warning("Galaxy: unable to persist Sentry notification rate-limit state")
      try:
        temporary_path.unlink(missing_ok=True)
      except OSError:
        pass
    return True


def _sentry_test_notification_event() -> dict:
  return {
    "eventId": f"notification-test-{int(time.time())}-{secrets.token_hex(4)}",
    "kind": "warning",
    "detectedAt": datetime.now(timezone.utc).isoformat(),
    "message": "This is a test StarPilot Sentry notification.",
    "imagePaths": [],
  }


def _dispatch_sentry_push(event: dict) -> None:
  try:
    from openpilot.starpilot.system.the_galaxy.web_push import webpush

    vapid = _get_sentry_vapid()
  except Exception:
    cloudlog.exception("Galaxy: Sentry Web Push is unavailable")
    return

  event_id = str(event.get("eventId") or "")
  payload = {
    "title": "StarPilot Sentry Mode",
    "body": str(event.get("message") or "Movement detected while parked."),
    "eventId": event_id,
    "url": f"/sentry?event={quote(event_id, safe='')}",
  }
  image_urls = _sentry_external_image_urls(event)
  if image_urls:
    payload["image"] = image_urls[0]

  with _SENTRY_PUSH_LOCK:
    subscriptions = _load_sentry_push_subscriptions()

  expired_endpoints = set()
  for subscription in subscriptions:
    endpoint = subscription.get("endpoint")
    try:
      webpush(
        subscription_info=subscription,
        data=json.dumps(payload, separators=(",", ":")),
        vapid_private_key=vapid,
        vapid_claims={"sub": _SENTRY_PUSH_SUBJECT},
        ttl=300,
        timeout=10,
      )
    except Exception as error:
      response = getattr(error, "response", None)
      if getattr(response, "status_code", None) in {404, 410}:
        expired_endpoints.add(endpoint)
      cloudlog.warning("Galaxy: Sentry Web Push delivery failed: %s", error)

  if expired_endpoints:
    with _SENTRY_PUSH_LOCK:
      current = _load_sentry_push_subscriptions()
      _save_sentry_push_subscriptions([
        subscription for subscription in current
        if subscription.get("endpoint") not in expired_endpoints
      ])


def _dispatch_sentry_event(event: dict, *, bypass_rate_limit: bool = False) -> None:
  if not any(_sentry_notification_channels().values()):
    return
  if not bypass_rate_limit and not _claim_sentry_notification_slot(event):
    return

  _dispatch_sentry_push(event)
  message = f"🚨 StarPilot Sentry Mode: {event['message']}"
  webhook = (params.get("SentryModeWebhook", encoding="utf-8") or "").strip()
  if webhook:
    files = []
    handles = []
    try:
      for image_path in event.get("imagePaths", []):
        handle = open(image_path, "rb")
        handles.append(handle)
        files.append(("file", (Path(image_path).name, handle, "image/jpeg")))

      body = {"content": message, "event": json.dumps(event, separators=(",", ":"))}
      response = requests.post(webhook, data=body, files=files or None, timeout=10)
      response.raise_for_status()
    except Exception:
      cloudlog.exception("Galaxy: sentry webhook notification failed")
    finally:
      for handle in handles:
        try:
          handle.close()
        except OSError:
          pass

  ntfy_url = (params.get("SentryModeNtfyUrl", encoding="utf-8") or "").strip()
  if ntfy_url:
    try:
      headers = {"Title": "StarPilot Sentry Mode", "Priority": "urgent", "Tags": "warning,car"}
      image = _sentry_first_image(event)
      if image is None:
        response = requests.post(ntfy_url, data=message.encode("utf-8"), headers=headers, timeout=10)
      else:
        filename, image_data = image
        headers.update({
          "Content-Type": "image/jpeg",
          "Filename": filename,
          "Message": f"StarPilot Sentry Mode: {event['message']}",
        })
        response = requests.put(ntfy_url, data=image_data, headers=headers, timeout=10)
      response.raise_for_status()
    except Exception:
      cloudlog.exception("Galaxy: ntfy notification failed")

TOGGLE_BACKUP_FORMAT = "starpilot-toggle-backup"
TOGGLE_BACKUP_VERSION = 1
TOGGLE_BACKUP_MAX_ENCODED_BYTES = 2_000_000
TOGGLE_BACKUP_NO_DEFAULT_KEYS = param_profiles.PROFILE_NO_DEFAULT_KEYS


def _get_toggle_backup_keys():
  keys = set()
  for key, default_value, _, _ in starpilot_default_params:
    if key in EXCLUDED_KEYS:
      continue
    if default_value is None and key not in TOGGLE_BACKUP_NO_DEFAULT_KEYS:
      continue

    try:
      flags = _params_raw.get_key_flag(key)
    except Exception:
      continue

    if not flags & ParamKeyFlag.PERSISTENT or flags & ParamKeyFlag.DONT_LOG:
      continue

    keys.add(key)

  return keys


def _route_log_files(name):
  """Full logs for a route as [(segment, filename, path, size)], oldest segment first."""
  if not utilities.ROUTE_RE.fullmatch(str(name or "")):
    return []

  for footage_path in FOOTAGE_PATHS:
    logs = []
    try:
      segments = utilities.get_segments_in_route(name, footage_path)
    except OSError:
      continue
    for segment in sorted(segments, key=lambda s: int(s.rsplit("--", 1)[1])):
      for filename in ROUTE_LOG_CANDIDATES:
        path = os.path.join(footage_path, segment, filename)
        if os.path.isfile(path):
          logs.append((segment, filename, path, os.path.getsize(path)))
          break
    if logs:
      return logs
  return []


def _coerce_toggle_restore_value(key, value):
  value_type = _get_param_key_type(_params_raw, key)

  if value_type == ParamKeyType.BOOL:
    if isinstance(value, bool):
      return value
    if isinstance(value, numbers.Real) and value in (0, 1):
      return bool(value)
    if isinstance(value, str):
      normalized = value.strip().lower()
      if normalized in {"1", "true", "yes", "on"}:
        return True
      if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"Invalid boolean value for {key}")

  if value_type == ParamKeyType.INT:
    if isinstance(value, bool):
      raise ValueError(f"Invalid integer value for {key}")
    return int(float(value))

  if value_type == ParamKeyType.FLOAT:
    if isinstance(value, bool):
      raise ValueError(f"Invalid numeric value for {key}")
    result = float(value)
    if not math.isfinite(result):
      raise ValueError(f"Invalid numeric value for {key}")
    return result

  if value_type == ParamKeyType.JSON:
    if isinstance(value, str):
      value = json.loads(value)
    if not isinstance(value, (dict, list)):
      raise ValueError(f"Invalid JSON value for {key}")
    return value

  if value_type == ParamKeyType.BYTES:
    if isinstance(value, bytes):
      return value
    if isinstance(value, str):
      return value.encode("utf-8")
    raise ValueError(f"Invalid byte value for {key}")

  if value_type == ParamKeyType.TIME:
    if isinstance(value, datetime):
      return value
    if isinstance(value, str):
      return datetime.fromisoformat(value)
    raise ValueError(f"Invalid time value for {key}")

  if value is None or isinstance(value, (dict, list)):
    raise ValueError(f"Invalid string value for {key}")
  return str(value)

params = ParamsCompat(_params_raw)
params_memory = ParamsCompat(_params_memory_raw)
STATS_RESPONSE_CACHE_SECONDS = 2.0
_STATS_RESPONSE_CACHE = {
  "updated_at": 0.0,
  "payload": None,
}
_STATS_RESPONSE_LOCK = threading.Lock()

try:
  FOOTAGE_PATHS = [
    Paths.log_root(HD=True, raw=True),
    Paths.log_root(konik=True, raw=True),
    Paths.log_root(raw=True),
  ]
except TypeError:
  FOOTAGE_PATHS = [
    "/data/media/0/realdata_HD/",
    "/data/media/0/realdata_konik/",
    str(Paths.log_root()),
  ]

# Full drive logs, newest format first. comma only accepts qlog/qcamera uploads, so these come off the device directly.
ROUTE_LOG_CANDIDATES = ("rlog.zst", "rlog.bz2", "rlog")
ROUTE_METADATA_WORKERS = 4
ROUTE_METADATA_BATCH_SIZE = 8
ROUTE_THUMBNAIL_CACHE_SECONDS = 7 * 24 * 60 * 60
# Browsers only allow a handful of connections per origin, so a request must never
# park on the preview queue: give up and let the card fall back, the job keeps running.
ROUTE_THUMBNAIL_WAIT_SECONDS = 25
# One minute per segment, matching loggerd's segment length.
SEGMENT_DURATION_SECONDS = 60
# Only ever remux one segment at a time; the driving stack needs the headroom. The
# subprocess timeout is the hard bound, with a small allowance for executor handoff.
VIDEO_REMUX_WAIT_SECONDS = utilities.VIDEO_REMUX_TIMEOUT_SECONDS + 5
# Segment media never changes once loggerd has closed it, so let the browser keep it.
VIDEO_CACHE_SECONDS = 7 * 24 * 60 * 60
_VIDEO_REMUX_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-remux")
_VIDEO_REMUX_FUTURES = {}
_VIDEO_REMUX_LOCK = threading.Lock()
_ROUTE_THUMBNAIL_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="route-thumbnail")
_ROUTE_THUMBNAIL_FUTURES = {}
_ROUTE_THUMBNAIL_LOCK = threading.Lock()


def _route_scan_entries(footage_paths):
  """Route scan entries in footage-root priority order, deduplicated by route id."""
  entries = []
  seen_names = set()
  for footage_path in footage_paths:
    try:
      route_details = utilities.get_routes_with_segment_details(footage_path)
    except OSError:
      continue
    for name, details in route_details:
      if name in seen_names:
        continue
      seen_names.add(name)
      entries.append((
        footage_path,
        name,
        max(0, int(details.get("segmentCount", 0))),
        max(0, int(details.get("firstSegmentNum", 0))),
      ))
  return entries


def _route_metadata_events(entries, connect_dongle_id="", process_route=None):
  """Yield SSE payloads while keeping queued metadata work cancellable."""
  route_processor = process_route or utilities.process_route
  total = len(entries)
  yield {"routes": [], "progress": 0, "total": total, "connectDongleId": connect_dongle_id}
  if total == 0:
    return

  executor = ThreadPoolExecutor(max_workers=ROUTE_METADATA_WORKERS, thread_name_prefix="route-metadata")
  futures = []
  try:
    futures = [
      executor.submit(route_processor, path, name, segment_count, first_segment_num)
      for path, name, segment_count, first_segment_num in entries
    ]
    batch = []
    for processed, future in enumerate(as_completed(futures), start=1):
      try:
        batch.append(future.result())
      except Exception as exception:
        print(f"Error processing route: {exception}")

      if len(batch) >= ROUTE_METADATA_BATCH_SIZE or processed == total:
        yield {"routes": batch, "progress": processed, "total": total}
        batch = []
  finally:
    for future in futures:
      future.cancel()
    executor.shutdown(wait=False, cancel_futures=True)


def _route_first_segment_path(name, footage_path):
  """Oldest surviving segment of a route. loggerd ages out --0 first, so it is not always --0."""
  try:
    segments = utilities.get_segments_in_route(name, footage_path)
  except OSError:
    return None
  return os.path.join(footage_path, segments[0]) if segments else None


def _resolve_route_thumbnail(file_path, footage_paths=None):
  """Resolve only <segment>/preview.png below a configured footage root."""
  parts = Path(str(file_path or "")).parts
  if len(parts) != 2 or parts[1] != "preview.png" or not utilities.SEGMENT_RE.fullmatch(parts[0]):
    return None

  for footage_path in footage_paths if footage_paths is not None else FOOTAGE_PATHS:
    footage_root = Path(footage_path).resolve()
    segment_path = (footage_root / parts[0]).resolve()
    if segment_path.parent != footage_root or not segment_path.is_dir():
      continue
    preview_path = segment_path / "preview.png"
    if preview_path.is_symlink():
      continue
    if preview_path.exists():
      resolved_preview = preview_path.resolve()
      if resolved_preview.parent != segment_path:
        continue
      return resolved_preview
    return preview_path
  return None


def _generate_route_thumbnail(preview_path):
  if preview_path.is_file():
    return preview_path

  for filename in ("qcamera.ts", "fcamera.hevc"):
    source_path = preview_path.parent / filename
    if source_path.resolve().parent == preview_path.parent and source_path.is_file() and utilities.video_to_png(source_path, preview_path) and preview_path.is_file():
      return preview_path
  return None


def _remove_video_remux_future(key, future):
  with _VIDEO_REMUX_LOCK:
    if _VIDEO_REMUX_FUTURES.get(key) is future:
      _VIDEO_REMUX_FUTURES.pop(key, None)


def _get_or_create_segment_mp4(source_path):
  """Remuxed mp4 for one segment, or None if it is not ready in time.

  Concurrent requests share one ffmpeg run instead of racing to write the same file.
  """
  key = str(source_path)
  created = False
  with _VIDEO_REMUX_LOCK:
    future = _VIDEO_REMUX_FUTURES.get(key)
    if future is None:
      future = _VIDEO_REMUX_EXECUTOR.submit(utilities.ffmpeg_mp4_wrap_to_path, source_path)
      _VIDEO_REMUX_FUTURES[key] = future
      created = True

  if created:
    future.add_done_callback(lambda completed: _remove_video_remux_future(key, completed))

  try:
    return future.result(timeout=VIDEO_REMUX_WAIT_SECONDS)
  except TimeoutError:
    # The callback keeps the running job deduplicated, then evicts it when done.
    return None


def _remove_route_thumbnail_future(key, future):
  with _ROUTE_THUMBNAIL_LOCK:
    if _ROUTE_THUMBNAIL_FUTURES.get(key) is future:
      _ROUTE_THUMBNAIL_FUTURES.pop(key, None)


def _get_or_create_route_thumbnail(file_path, footage_paths=None):
  preview_path = _resolve_route_thumbnail(file_path, footage_paths)
  if preview_path is None:
    return None
  if preview_path.is_file():
    return preview_path

  key = str(preview_path)
  created = False
  with _ROUTE_THUMBNAIL_LOCK:
    future = _ROUTE_THUMBNAIL_FUTURES.get(key)
    if future is None:
      future = _ROUTE_THUMBNAIL_EXECUTOR.submit(_generate_route_thumbnail, preview_path)
      _ROUTE_THUMBNAIL_FUTURES[key] = future
      created = True

  if created:
    future.add_done_callback(lambda completed: _remove_route_thumbnail_future(key, completed))

  try:
    return future.result(timeout=ROUTE_THUMBNAIL_WAIT_SECONDS)
  except TimeoutError:
    # The completion callback keeps the running job deduplicated, then evicts it when done.
    return None


class _TarBuffer(io.RawIOBase):
  """Collects tarfile output so a route archive can be streamed out instead of built on disk."""

  def __init__(self):
    self._chunks = []

  def writable(self):
    return True

  def write(self, data):
    self._chunks.append(bytes(data))
    return len(data)

  def pop(self):
    data = b"".join(self._chunks)
    self._chunks.clear()
    return data


KEYS = {
  "amap1": ("amap1", "", "AMapKey1", "AMap / Gaode key #1", 39),
  "amap2": ("amap2", "", "AMapKey2", "AMap / Gaode key #2", 39),
  "public": ("public", "pk.", "MapboxPublicKey", "Public key", 80),
  "secret": ("secret", "sk.", "MapboxSecretKey", "Secret key", 80),
}

GALAXY_COOKIE_NAME = "galaxy_session"
GALAXY_PLAY_STORE_URL = "https://play.google.com/store/apps/details?id=com.embaucha.galaxynav&hl=en-US&ah=9FldHJ99kxL8oNbSlO5F4sQqwC4"

NAVIGATION_MEMORY_LOCATION_STALE_SECONDS = 10.0
NAVIGATION_PERSISTED_LOCATION_FUTURE_SKEW_SECONDS = 60.0
NAVIGATION_PERSISTED_LOCATION_MAX_AGE_SECONDS = 24 * 60 * 60

TMUX_LOGS_PATH = Path("/data/tmux_logs")

MODEL_DOWNLOAD_PARAM = "ModelToDownload"
MODEL_DOWNLOAD_ALL_PARAM = "DownloadAllModels"
ALLOW_GPU_DOWNLOAD_WITHOUT_GPU_PARAM = "AllowGpuModelDownloadWithoutGpu"
MODEL_DOWNLOAD_PROGRESS_PARAM = "ModelDownloadProgress"
MODEL_CANCEL_DOWNLOAD_PARAM = "CancelModelDownload"
MODEL_SORT_MODE_PARAM = "ModelSortMode"
DEFAULT_MODEL_SORT_MODE = "release_date"
MODEL_USER_FAVORITES_PARAM = "UserFavorites"
MAPS_DOWNLOAD_PARAM = "DownloadMaps"
MAPS_CANCEL_DOWNLOAD_PARAM = "CancelDownloadMaps"
MAPS_DOWNLOAD_PROGRESS_PARAM = "MapsDownloadProgress"
MAPS_DOWNLOAD_SIZE_CACHE_PARAM = MAPS_STORAGE_CACHE_PARAM


def _get_galaxy_dir():
  if override := os.getenv("SP_GALAXY_DIR"):
    return Path(override)
  return Path(Paths.comma_home()) / "starpilot" / "data" / "galaxy" if PC else Path("/data/galaxy")


def _read_galaxy_text(path):
  try:
    return path.read_text().strip() if path.is_file() else ""
  except Exception:
    return ""


def _build_galaxy_session_value(slug, token):
  if not slug or not token:
    return ""
  return quote(f"{slug}:{token}", safe="")


def _parse_last_gps_position(raw_value):
  if not raw_value:
    return None

  try:
    payload = json.loads(raw_value)
  except (TypeError, ValueError, json.JSONDecodeError):
    return None

  if not isinstance(payload, dict):
    return None

  latitude = payload.get("latitude")
  longitude = payload.get("longitude")
  has_fix = payload.get("hasFix", False)
  try:
    latitude = float(latitude or 0.0)
    longitude = float(longitude or 0.0)
  except (TypeError, ValueError):
    return None

  if not has_fix or (abs(latitude) < 1e-6 and abs(longitude) < 1e-6):
    return None

  payload["latitude"] = latitude
  payload["longitude"] = longitude
  return payload


def _last_gps_position_is_live(payload):
  if not isinstance(payload, dict):
    return False

  try:
    updated_at_monotonic = float(payload.get("updatedAtMonotonic", 0.0) or 0.0)
  except (TypeError, ValueError):
    updated_at_monotonic = 0.0

  if updated_at_monotonic <= 0.0:
    return False

  return (time.monotonic() - updated_at_monotonic) <= NAVIGATION_MEMORY_LOCATION_STALE_SECONDS


def _last_gps_position_is_recent(payload):
  if not isinstance(payload, dict):
    return False

  try:
    updated_at_sec = float(payload.get("updatedAtSec", 0.0) or 0.0)
  except (TypeError, ValueError):
    updated_at_sec = 0.0

  if updated_at_sec <= 0.0:
    return False

  now_sec = time.time()
  if updated_at_sec > (now_sec + NAVIGATION_PERSISTED_LOCATION_FUTURE_SKEW_SECONDS):
    return False

  if not system_time_valid():
    return True

  age_sec = now_sec - updated_at_sec
  return 0.0 <= age_sec <= NAVIGATION_PERSISTED_LOCATION_MAX_AGE_SECONDS


def _get_navigation_last_position():
  memory_position = _parse_last_gps_position(params_memory.get("LastGPSPosition", encoding="utf8") or "")
  if _last_gps_position_is_live(memory_position):
    return memory_position

  persisted_position = _parse_last_gps_position(params.get("LastGPSPosition", encoding="utf8") or "")
  if _last_gps_position_is_recent(persisted_position):
    return persisted_position

  return None

FINGERPRINT_MAKE_LABELS = [
  "Acura",
  "Audi",
  "Buick",
  "Cadillac",
  "Chevrolet",
  "Chrysler",
  "CUPRA",
  "Dodge",
  "Ford",
  "Genesis",
  "GMC",
  "Holden",
  "Honda",
  "Hyundai",
  "Jeep",
  "Kia",
  "Lexus",
  "Lincoln",
  "MAN",
  "Mazda",
  "Nissan",
  "Peugeot",
  "Ram",
  "Rivian",
  "SEAT",
  "\u0160koda",
  "Subaru",
  "Tesla",
  "Toyota",
  "Volkswagen",
]

FINGERPRINT_MAKE_TO_VALUES_DIR = {
  "acura": "honda",
  "audi": "volkswagen",
  "buick": "gm",
  "cadillac": "gm",
  "chevrolet": "gm",
  "chrysler": "chrysler",
  "cupra": "volkswagen",
  "dodge": "chrysler",
  "ford": "ford",
  "genesis": "hyundai",
  "gmc": "gm",
  "holden": "gm",
  "honda": "honda",
  "hyundai": "hyundai",
  "jeep": "chrysler",
  "kia": "hyundai",
  "lexus": "toyota",
  "lincoln": "ford",
  "man": "volkswagen",
  "mazda": "mazda",
  "nissan": "nissan",
  "peugeot": "psa",
  "ram": "chrysler",
  "rivian": "rivian",
  "seat": "volkswagen",
  "\u0161koda": "volkswagen",
  "subaru": "subaru",
  "tesla": "tesla",
  "toyota": "toyota",
  "volkswagen": "volkswagen",
}

_FINGERPRINT_CARDOCS_RE = re.compile(r'\w*CarDocs\w*\(\s*"([^"]+)"')
_FINGERPRINT_PLATFORM_RE = re.compile(r'(\w+)\s*=\s*\w+\s*\(\s*\[([\s\S]*?)\]\s*,')
_FINGERPRINT_PLATFORM_NAME_RE = re.compile(r'^[A-Z0-9_]+$')
_FINGERPRINT_VALID_NAME_RE = re.compile(r'^[A-Za-z0-9 \u0160.(),&\-]+$')

_openpilot_root_cache = None
_fingerprint_catalog_cache = None

_fast_update_lock = threading.Lock()
_FAST_UPDATE_TOTAL_STEPS = 5
_FAST_UPDATE_PROGRESS_UPDATE_INTERVAL_S = 5.0
_FAST_UPDATE_REBOOT_NOTICE_SECONDS = 6.0
_FAST_UPDATE_FETCH_TIMEOUT_S = 60
_FAST_BRANCH_SWITCH_FETCH_TIMEOUT_S = 60
_FAST_ROLLBACK_FETCH_TIMEOUT_S = 60
_AGNOS_MANIFEST_PATH = "system/hardware/tici/agnos.json"
_AGNOS_REMOTE_MANIFEST_TIMEOUT_S = 8
_AGNOS_UPDATE_ESTIMATED_DOWNLOAD_MB = 900
_GIT_PROGRESS_PERCENT_RE = re.compile(r'([A-Za-z][A-Za-z /_-]+):\s*([0-9]{1,3})%')
_GIT_SUBMODULE_SECTION_RE = re.compile(r'^\s*\[submodule\s+"[^"]+"\]\s*$', re.MULTILINE)
_ROLLBACK_REF = "refs/starpilot/rollback"
_ROLLBACK_BRANCH_CONFIG_KEY = "starpilot.rollbackbranch"
_ROLLBACK_RECORDED_AT_CONFIG_KEY = "starpilot.rollbackrecordedat"
_TESTING_GROUNDS_SCHEMA_VERSION = SHARED_TESTING_GROUNDS_SCHEMA_VERSION
_TESTING_GROUNDS_SLOT_COUNT = len(SHARED_TESTING_GROUNDS_SLOT_DEFINITIONS)
_TESTING_GROUNDS_DEFAULT_VARIANT = SHARED_DEFAULT_TESTING_GROUND_VARIANT
_TESTING_GROUNDS_VARIANTS = set(SHARED_TESTING_GROUND_VARIANTS) or {_TESTING_GROUNDS_DEFAULT_VARIANT}
_TESTING_GROUNDS_LOCK = threading.Lock()
_TESTING_GROUNDS_STATE_PATH = SHARED_TESTING_GROUNDS_STATE_PATH
# Slot labels live in starpilot/common/testing_grounds.py.
_TESTING_GROUNDS_SLOT_DEFINITIONS = [dict(slot) for slot in SHARED_TESTING_GROUNDS_SLOT_DEFINITIONS]
_TESTING_GROUNDS_VARIANT_LABELS_BY_SLOT = {
  str(slot_id or "").strip(): dict(labels or {})
  for slot_id, labels in SHARED_TESTING_GROUND_VARIANT_LABELS.items()
}
_fast_update_state = {
  "running": False,
  "stage": "idle",
  "message": "",
  "lastError": "",
  "lastBranch": "",
  "lastMode": "",
  "startedAt": 0.0,
  "finishedAt": 0.0,
  "progressStep": 0,
  "progressTotalSteps": _FAST_UPDATE_TOTAL_STEPS,
  "progressStepPercent": 0.0,
  "progressPercent": 0.0,
  "progressLabel": "Idle",
  "progressDetail": "",
}
_ROUTE_DELETE_LOCK = threading.Lock()

_FACTORY_RESET_WIPE_PATHS = [
  "/data/params",
  "/cache/starpilot/params",
  "/cache/params",
  "/data/media/0/realdata",
  "/data/media/0/realdata_HD",
  "/data/media/0/realdata_konik",
  "/data/models",
  "/data/toggle_backups",
  "/data/backups",
  "/data/themes",
  "/data/media/0/osm/offline",
  "/cache/use_HD",
  "/cache/use_konik",
]

_PLOTS_POLL_INTERVAL_S = 0.75
_PLOTS_BOOT_STABILIZATION_WINDOW_S = 45.0
_PLOTS_BOOT_POLL_INTERVAL_S = 1.0
_PLOTS_CLIENT_IDLE_TIMEOUT_S = 6.0
_PLOTS_SAMPLE_STALE_AFTER_S = 1.5

_plots_lock = threading.Lock()
_plots_worker_thread = None
_plots_last_client_request_ts = 0.0
_plots_state = {
  "timestamp": 0.0,
  "desiredLateralAccel": 0.0,
  "actualLateralAccel": 0.0,
  "desiredLongitudinalAccel": 0.0,
  "actualLongitudinalAccel": 0.0,
  "controlsActive": False,
  "longitudinalControlActive": False,
  "lateralP": 0.0,
  "lateralI": 0.0,
  "lateralD": 0.0,
  "lateralF": 0.0,
  "longitudinalUpAccelCmd": 0.0,
  "longitudinalUiAccelCmd": 0.0,
  "longitudinalUfAccelCmd": 0.0,
  "speed": 0.0,
  "lateralSource": "curvature",
  "longitudinalSource": "controlsState + livePose",
  "lateralTermsSource": "unknown",
  "longitudinalTermsSource": "controlsState",
  "sampleIndex": 0,
  "lastError": "",
}

_TROUBLESHOOT_PERSONALITY_KEYS = [
  "CustomPersonalities",
  "TrafficPersonalityProfile",
  "TrafficFollow",
  "TrafficJerkAcceleration",
  "TrafficJerkDeceleration",
  "TrafficJerkDanger",
  "TrafficJerkSpeedDecrease",
  "TrafficJerkSpeed",
  "AggressivePersonalityProfile",
  "AggressiveFollow",
  "AggressiveFollowHigh",
  "AggressiveJerkAcceleration",
  "AggressiveJerkDeceleration",
  "AggressiveJerkDanger",
  "AggressiveJerkSpeedDecrease",
  "AggressiveJerkSpeed",
  "StandardPersonalityProfile",
  "StandardFollow",
  "StandardFollowHigh",
  "StandardJerkAcceleration",
  "StandardJerkDeceleration",
  "StandardJerkDanger",
  "StandardJerkSpeedDecrease",
  "StandardJerkSpeed",
  "RelaxedPersonalityProfile",
  "RelaxedFollow",
  "RelaxedFollowHigh",
  "RelaxedJerkAcceleration",
  "RelaxedJerkDeceleration",
  "RelaxedJerkDanger",
  "RelaxedJerkSpeedDecrease",
  "RelaxedJerkSpeed",
]

_TROUBLESHOOT_CEM_KEYS = [
  "ConditionalExperimental",
  "CESpeed",
  "CESpeedLead",
  "CECurves",
  "CELead",
  "CESlowerLead",
  "CEStoppedLead",
  "CEOpenRoad",
  "CEModelStopTime",
  "CESignalSpeed",
  "ShowCEMStatus",
]

_TROUBLESHOOT_ADVANCED_LATERAL_KEYS = [
  "AdvancedLateralTune",
  "UseAutoSteerDelay",
  "SteerDelay",
  "SteerFriction",
  "SteerOffset",
  "SteerKP",
  "SteerLatAccel",
  "SteerRatio",
  "ForceAutoTune",
  "ForceAutoTuneOff",
  "ForceTorqueController",
  "CameraOffset",
  "LaneCentering",
  "LaneCenteringPauseOnSignal",
  "LaneCenteringE2EAuthority",
  "LaneCenterOffset",
]

_TROUBLESHOOT_ADVANCED_LONGITUDINAL_KEYS = [
  "AdvancedLongitudinalTune",
  "EVTuning",
  "TruckTuning",
  "TrailerLoad",
  "CustomAccelProfile",
  *CUSTOM_ACCEL_PROFILE_PARAM_KEYS,
  CUSTOM_ACCEL_PROFILE_POINT_COUNT_KEY,
  *CUSTOM_ACCEL_PROFILE_BREAKPOINT_PARAM_KEYS,
  *CUSTOM_ACCEL_PROFILE_POINT_VALUE_PARAM_KEYS,
  "LongitudinalActuatorDelay",
  "StartAccel",
  "VEgoStarting",
  "StopAccel",
  "StoppingDecelRate",
  "VEgoStopping",
]

_RUNTIME_DEFAULT_STOCK_KEYS = {
  "SteerDelay": "SteerDelayStock",
  "SteerFriction": "SteerFrictionStock",
  "SteerOffset": "SteerOffsetStock",
  "SteerKP": "SteerKPStock",
  "SteerLatAccel": "SteerLatAccelStock",
  "SteerRatio": "SteerRatioStock",
  "LongitudinalActuatorDelay": "LongitudinalActuatorDelayStock",
  "StartAccel": "StartAccelStock",
  "StopAccel": "StopAccelStock",
  "StoppingDecelRate": "StoppingDecelRateStock",
  "VEgoStarting": "VEgoStartingStock",
  "VEgoStopping": "VEgoStoppingStock",
}

_RUNTIME_DEFAULT_ZERO_OK_KEYS = {
  "SteerOffset",
}

_TROUBLESHOOT_SECTION_DEFINITIONS = [
  {
    "id": "personality_settings",
    "title": "Longitudinal (Speed & Following) › Driving Personalities",
    "keys": _TROUBLESHOOT_PERSONALITY_KEYS,
  },
  {
    "id": "cem_settings",
    "title": "Longitudinal (Speed & Following) › Longitudinal control mode",
    "keys": _TROUBLESHOOT_CEM_KEYS,
  },
  {
    "id": "advanced_lateral_tuning",
    "title": "Lateral (Steering) › Advanced Lateral Tuning",
    "keys": _TROUBLESHOOT_ADVANCED_LATERAL_KEYS,
  },
  {
    "id": "advanced_longitudinal_tuning",
    "title": "Longitudinal (Speed & Following) › Advanced Longitudinal Tuning",
    "keys": _TROUBLESHOOT_ADVANCED_LONGITUDINAL_KEYS,
  },
]

_TROUBLESHOOT_SECTION_BY_ID = {
  section["id"]: section
  for section in _TROUBLESHOOT_SECTION_DEFINITIONS
}

_TROUBLESHOOT_NON_RESETTABLE_SECTION_KEYS = {
  "CustomPersonalities",
  "TrafficPersonalityProfile",
  "AggressivePersonalityProfile",
  "StandardPersonalityProfile",
  "RelaxedPersonalityProfile",
}

def _normalize_fingerprint_make_key(make_value):
  return str(make_value or "").strip().lower()

def _safe_float(value, default=0.0):
  try:
    return float(value)
  except Exception:
    return float(default)

def _get_param_int_value(key, default=0):
  try:
    raw_value = params.get(key)
    if isinstance(raw_value, bytes):
      raw_value = raw_value.decode("utf-8", errors="replace")
    return int(float(str(raw_value or default)))
  except Exception:
    return int(default)

def _get_system_uptime_seconds():
  try:
    with open("/proc/uptime", "r", encoding="utf-8") as uptime_file:
      return _safe_float((uptime_file.read().split() or ["0"])[0], 0.0)
  except Exception:
    return 0.0

def _is_plots_boot_stabilizing():
  if not params.get_bool("IsOnroad"):
    return False
  return _get_system_uptime_seconds() < _PLOTS_BOOT_STABILIZATION_WINDOW_S

def _extract_plots_speed_mps(controls_state, live_pose):
  try:
    velocity_device = getattr(live_pose, "velocityDevice", None)
    if velocity_device and getattr(velocity_device, "valid", False):
      # Use forward device-frame velocity for plot gating without adding any new subscriptions.
      return abs(_safe_float(getattr(velocity_device, "x", 0.0), 0.0))
  except Exception:
    pass

  return abs(_safe_float(getattr(controls_state, "vPid", 0.0), 0.0))

def _extract_lateral_accel_values(controls_state, speed_mps):
  v_ego = max(0.0, _safe_float(speed_mps))
  speed_sq = v_ego * v_ego

  try:
    lateral_state = controls_state.lateralControlState
    if lateral_state.which() == "torqueState":
      torque_state = lateral_state.torqueState
      desired = _safe_float(getattr(torque_state, "desiredLateralAccel", 0.0))
      actual = _safe_float(getattr(torque_state, "actualLateralAccel", 0.0))
      if abs(desired) > 1e-3 or abs(actual) > 1e-3:
        return desired, actual, "torqueState"
  except Exception:
    pass

  desired_curvature = _safe_float(getattr(controls_state, "desiredCurvature", 0.0))
  actual_curvature = _safe_float(getattr(controls_state, "curvature", 0.0))
  return desired_curvature * speed_sq, actual_curvature * speed_sq, "curvature"

def _extract_longitudinal_accel_values(controls_state, live_pose):
  desired = _safe_float(getattr(controls_state, "aTarget", 0.0))
  source = "controlsState.aTarget + livePose"

  actual = 0.0
  try:
    acceleration_device = getattr(live_pose, "accelerationDevice", None)
    if acceleration_device and getattr(acceleration_device, "valid", False):
      actual = _safe_float(getattr(acceleration_device, "x", 0.0), 0.0)
  except Exception:
    source = "controlsState.aTarget"

  # Fallback only if aTarget is unavailable/legacy-zero while PID terms are present.
  if abs(desired) < 1e-6:
    up = _safe_float(getattr(controls_state, "upAccelCmd", 0.0))
    ui = _safe_float(getattr(controls_state, "uiAccelCmd", 0.0))
    uf = _safe_float(getattr(controls_state, "ufAccelCmd", 0.0))
    pid_sum = up + ui + uf
    if abs(pid_sum) > 1e-6:
      desired = pid_sum
      source = "controlsState PID sum + livePose"

  return desired, actual, source

def _extract_lateral_controller_terms(controls_state):
  terms = {
    "lateralP": 0.0,
    "lateralI": 0.0,
    "lateralD": 0.0,
    "lateralF": 0.0,
  }
  source = "unknown"

  try:
    lateral_state = controls_state.lateralControlState
    which = lateral_state.which()
    if which == "torqueState":
      torque_state = lateral_state.torqueState
      terms["lateralP"] = _safe_float(getattr(torque_state, "p", 0.0))
      terms["lateralI"] = _safe_float(getattr(torque_state, "i", 0.0))
      terms["lateralD"] = _safe_float(getattr(torque_state, "d", 0.0))
      terms["lateralF"] = _safe_float(getattr(torque_state, "f", 0.0))
      source = "torqueState"
    elif which == "pidState":
      pid_state = lateral_state.pidState
      terms["lateralP"] = _safe_float(getattr(pid_state, "p", 0.0))
      terms["lateralI"] = _safe_float(getattr(pid_state, "i", 0.0))
      terms["lateralF"] = _safe_float(getattr(pid_state, "f", 0.0))
      source = "pidState"
    elif which:
      source = which
  except Exception:
    pass

  return terms, source

def _extract_longitudinal_controller_terms(controls_state):
  terms = {
    "longitudinalUpAccelCmd": _safe_float(getattr(controls_state, "upAccelCmd", 0.0)),
    "longitudinalUiAccelCmd": _safe_float(getattr(controls_state, "uiAccelCmd", 0.0)),
    "longitudinalUfAccelCmd": _safe_float(getattr(controls_state, "ufAccelCmd", 0.0)),
  }
  return terms, "controlsState"

def _plots_worker():
  global _plots_worker_thread

  try:
    sm = messaging.SubMaster(["controlsState", "livePose"], poll="controlsState")
  except Exception as exception:
    with _plots_lock:
      _plots_state["lastError"] = str(exception)
      _plots_worker_thread = None
    return

  while True:
    with _plots_lock:
      idle_for = time.monotonic() - _plots_last_client_request_ts

    if idle_for >= _PLOTS_CLIENT_IDLE_TIMEOUT_S:
      break

    try:
      sm.update(0)

      controls_state = sm["controlsState"]
      live_pose = sm["livePose"]
      speed = _extract_plots_speed_mps(controls_state, live_pose)
      controls_active = bool(getattr(controls_state, "active", False))
      long_control_state = int(_safe_float(getattr(controls_state, "longControlState", 0)))
      longitudinal_control_active = controls_active and long_control_state != 0

      desired_lateral, actual_lateral, lateral_source = _extract_lateral_accel_values(controls_state, speed)
      desired_longitudinal, actual_longitudinal, longitudinal_source = _extract_longitudinal_accel_values(controls_state, live_pose)
      lateral_terms, lateral_terms_source = _extract_lateral_controller_terms(controls_state)
      longitudinal_terms, longitudinal_terms_source = _extract_longitudinal_controller_terms(controls_state)

      with _plots_lock:
        _plots_state.update({
          "timestamp": time.time(),
          "desiredLateralAccel": round(desired_lateral, 4),
          "actualLateralAccel": round(actual_lateral, 4),
          "desiredLongitudinalAccel": round(desired_longitudinal, 4),
          "actualLongitudinalAccel": round(actual_longitudinal, 4),
          "controlsActive": controls_active,
          "longitudinalControlActive": longitudinal_control_active,
          "lateralP": round(lateral_terms["lateralP"], 4),
          "lateralI": round(lateral_terms["lateralI"], 4),
          "lateralD": round(lateral_terms["lateralD"], 4),
          "lateralF": round(lateral_terms["lateralF"], 4),
          "longitudinalUpAccelCmd": round(longitudinal_terms["longitudinalUpAccelCmd"], 4),
          "longitudinalUiAccelCmd": round(longitudinal_terms["longitudinalUiAccelCmd"], 4),
          "longitudinalUfAccelCmd": round(longitudinal_terms["longitudinalUfAccelCmd"], 4),
          "speed": round(speed, 4),
          "lateralSource": lateral_source,
          "longitudinalSource": longitudinal_source,
          "lateralTermsSource": lateral_terms_source,
          "longitudinalTermsSource": longitudinal_terms_source,
          "sampleIndex": int(_plots_state.get("sampleIndex", 0)) + 1,
          "lastError": "",
        })
    except Exception as exception:
      with _plots_lock:
        _plots_state["lastError"] = str(exception)

    sleep_interval = _PLOTS_POLL_INTERVAL_S
    if _is_plots_boot_stabilizing():
      sleep_interval = max(_PLOTS_POLL_INTERVAL_S, _PLOTS_BOOT_POLL_INTERVAL_S)
    time.sleep(sleep_interval)

  with _plots_lock:
    _plots_worker_thread = None

def _ensure_plots_worker():
  global _plots_worker_thread, _plots_last_client_request_ts

  with _plots_lock:
    _plots_last_client_request_ts = time.monotonic()
    if _plots_worker_thread and _plots_worker_thread.is_alive():
      return
    _plots_worker_thread = threading.Thread(target=_plots_worker, daemon=True)
    _plots_worker_thread.start()

def _set_fast_update_state(**kwargs):
  with _fast_update_lock:
    _fast_update_state.update(kwargs)

def _get_fast_update_state():
  with _fast_update_lock:
    return dict(_fast_update_state)

def _get_interrupted_update_recovery(repo_path, state_data):
  recovery_status = inspect_interrupted_update(
    repo_path,
    is_onroad=_safe_params_get_bool("IsOnroad"),
    update_running=bool(state_data.get("running")),
    updater_state=_safe_params_get("UpdaterState", encoding="utf-8", default=""),
  )
  return public_recovery_status(recovery_status)

def _set_fast_update_progress(step, label, step_percent=0.0, detail=""):
  safe_step = max(1, min(_FAST_UPDATE_TOTAL_STEPS, int(step)))
  safe_step_percent = float(max(0.0, min(100.0, step_percent)))
  overall_percent = (((safe_step - 1) + (safe_step_percent / 100.0)) / _FAST_UPDATE_TOTAL_STEPS) * 100.0

  _set_fast_update_state(
    progressStep=safe_step,
    progressTotalSteps=_FAST_UPDATE_TOTAL_STEPS,
    progressStepPercent=round(safe_step_percent, 1),
    progressPercent=round(overall_percent, 1),
    progressLabel=label,
    progressDetail=detail,
  )

def _parse_git_progress_line(raw_line):
  text = str(raw_line or "").replace("\x1b", "").strip()
  while text.startswith("remote:"):
    text = text[len("remote:"):].strip()

  if not text:
    return None, "", ""

  match = _GIT_PROGRESS_PERCENT_RE.search(text)
  if not match:
    return None, text, ""

  try:
    percent = float(match.group(2))
  except Exception:
    percent = None

  phase = str(match.group(1) or "").strip().lower()
  return percent, text, phase

def _normalize_git_phase_percent(phase, percent):
  safe_percent = max(0.0, min(100.0, float(percent)))
  phase_text = str(phase or "").strip().lower()

  # Git progress lines are per-phase and can hit 100% multiple times before the
  # command actually exits. Map known phases to a monotonic 0..99% envelope.
  if "counting objects" in phase_text:
    return min(20.0, safe_percent * 0.20)
  if "compressing objects" in phase_text:
    return min(45.0, 20.0 + (safe_percent * 0.25))
  if "receiving objects" in phase_text:
    return min(85.0, 45.0 + (safe_percent * 0.40))
  if "resolving deltas" in phase_text:
    return min(99.0, 85.0 + (safe_percent * 0.14))

  # Unknown phase: keep below 100 until the process exits.
  return min(99.0, safe_percent)

_GIT_CA_BUNDLE_CANDIDATES = (
  "/etc/ssl/certs/ca-certificates.crt",
  "/etc/ssl/cert.pem",
  "/usr/local/etc/openssl/cert.pem",
  "/opt/homebrew/etc/openssl@3/cert.pem",
)

def _resolve_git_ca_bundle():
  for env_key in ("GIT_SSL_CAINFO", "SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE"):
    candidate = os.environ.get(env_key, "")
    if candidate and os.path.isfile(candidate):
      return candidate

  for candidate in _GIT_CA_BUNDLE_CANDIDATES:
    if os.path.isfile(candidate):
      return candidate

  try:
    import certifi  # type: ignore
    candidate = certifi.where()
    if candidate and os.path.isfile(candidate):
      return candidate
  except Exception:
    pass

  return ""

def _git_base_cmd():
  cmd = ["git"]
  ca_bundle = _resolve_git_ca_bundle()
  if ca_bundle:
    cmd += ["-c", f"http.sslCAInfo={ca_bundle}", "-c", "http.sslVerify=true"]
  return cmd

def _git_command_env():
  env = os.environ.copy()
  env["GIT_TERMINAL_PROMPT"] = "0"
  env["GIT_ASKPASS"] = "/bin/false"
  env["SSH_ASKPASS"] = "/bin/false"
  env["GCM_INTERACTIVE"] = "Never"
  if not env.get("GIT_SSH_COMMAND"):
    env["GIT_SSH_COMMAND"] = "ssh -oBatchMode=yes"
  ca_bundle = _resolve_git_ca_bundle()
  if ca_bundle:
    env["GIT_SSL_CAINFO"] = ca_bundle
    env["SSL_CERT_FILE"] = ca_bundle
    env["CURL_CA_BUNDLE"] = ca_bundle
  return env

def _remote_git_check_allowed():
  try:
    return system_time_valid()
  except Exception:
    return True

def _is_deferred_tls_error(exception):
  error_text = str(exception or "").lower()
  if not error_text:
    return False

  tls_markers = (
    "certificate verification failed",
    "ssl certificate problem",
    "x509",
    "cafile",
    "ca cert",
  )
  if any(marker in error_text for marker in tls_markers):
    return not _remote_git_check_allowed()

  return False

def _get_remote_branch_commit(repo_path, branch):
  remote_commit = ""
  remote_error = ""
  branch_name = str(branch or "").strip()
  if not branch_name or not _remote_git_check_allowed():
    return remote_commit, remote_error

  try:
    remote_raw = _git_stdout(repo_path, ["ls-remote", "--heads", "origin", branch_name], timeout=20)
    if remote_raw:
      remote_commit = remote_raw.split()[0]
  except Exception as exception:
    if not _is_deferred_tls_error(exception):
      remote_error = str(exception)

  return remote_commit, remote_error

def _base_agnos_update_status(target_branch="", local_commit="", remote_commit=""):
  return {
    "available": False,
    "checked": False,
    "targetBranch": str(target_branch or "").strip(),
    "manifestPath": _AGNOS_MANIFEST_PATH,
    "localCommit": str(local_commit or "").strip(),
    "remoteCommit": str(remote_commit or "").strip(),
    "localManifestHash": "",
    "remoteManifestHash": "",
    "changedPartitions": [],
    "estimatedDownloadMb": _AGNOS_UPDATE_ESTIMATED_DOWNLOAD_MB,
    "warnings": [
      "This AGNOS firmware update will take much longer than a normal software update.",
      "You must be able to physically access the device to press the on-device update button.",
      "It downloads about 900 MB of data, so Wi-Fi is recommended.",
    ],
    "source": "",
    "error": "",
  }

def _canonical_agnos_manifest_text(manifest_text):
  text = str(manifest_text or "").strip()
  if not text:
    return ""

  try:
    return json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
  except Exception:
    return text

def _agnos_manifest_hash(manifest_text):
  canonical = _canonical_agnos_manifest_text(manifest_text)
  if not canonical:
    return ""
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def _agnos_partition_fingerprints(manifest_text):
  try:
    manifest = json.loads(str(manifest_text or ""))
  except Exception:
    return {}

  if not isinstance(manifest, list):
    return {}

  partitions = {}
  for item in manifest:
    if not isinstance(item, dict):
      continue

    name = str(item.get("name") or "").strip()
    if not name:
      continue

    partitions[name] = {
      "hash": str(item.get("hash") or ""),
      "hashRaw": str(item.get("hash_raw") or ""),
      "url": str(item.get("url") or ""),
      "size": str(item.get("size") or ""),
    }

  return partitions

def _agnos_changed_partitions(local_manifest_text, remote_manifest_text):
  local_partitions = _agnos_partition_fingerprints(local_manifest_text)
  remote_partitions = _agnos_partition_fingerprints(remote_manifest_text)
  partition_names = sorted(set(local_partitions) | set(remote_partitions))
  return [
    name for name in partition_names
    if local_partitions.get(name) != remote_partitions.get(name)
  ]

def _git_show_file_text(repo_path, ref, file_path):
  safe_ref = str(ref or "").strip()
  if not safe_ref:
    raise RuntimeError("Missing git ref")
  return _git_stdout(repo_path, ["show", f"{safe_ref}:{file_path}"], timeout=10)

def _github_raw_file_url(origin_remote, ref, file_path):
  remote = utilities.normalize_github_remote(origin_remote)
  if not remote:
    return ""

  slug = remote.split("https://github.com/", 1)[1]
  parts = slug.split("/", 1)
  if len(parts) != 2 or not parts[0] or not parts[1]:
    return ""

  owner = quote(parts[0], safe="")
  repo = quote(parts[1], safe="")
  quoted_ref = quote(str(ref or "").strip(), safe="")
  quoted_path = "/".join(quote(part, safe="") for part in str(file_path or "").split("/") if part)
  if not quoted_ref or not quoted_path:
    return ""

  return f"https://raw.githubusercontent.com/{owner}/{repo}/{quoted_ref}/{quoted_path}"

def _fetch_remote_file_text(origin_remote, ref, file_path):
  raw_url = _github_raw_file_url(origin_remote, ref, file_path)
  if not raw_url:
    raise RuntimeError("AGNOS manifest comparison is only available for GitHub remotes when the remote commit is not available locally.")

  response = requests.get(raw_url, timeout=_AGNOS_REMOTE_MANIFEST_TIMEOUT_S)
  response.raise_for_status()
  return response.text, raw_url

def _build_agnos_update_status(repo_path, origin_remote, local_commit, remote_commit, target_branch=""):
  status = _base_agnos_update_status(target_branch, local_commit, remote_commit)
  safe_local_commit = str(local_commit or "").strip()
  safe_remote_commit = str(remote_commit or "").strip()

  if not safe_local_commit or not safe_remote_commit:
    status["error"] = "Missing commit information for AGNOS manifest comparison."
    return status

  if safe_local_commit == safe_remote_commit:
    status["checked"] = True
    status["source"] = "same-commit"
    return status

  try:
    local_manifest_text = _git_show_file_text(repo_path, safe_local_commit, _AGNOS_MANIFEST_PATH)
  except Exception as exception:
    status["error"] = f"Unable to read local AGNOS manifest: {exception}"
    return status

  remote_manifest_text = ""
  if _git_has_commit(repo_path, safe_remote_commit):
    try:
      remote_manifest_text = _git_show_file_text(repo_path, safe_remote_commit, _AGNOS_MANIFEST_PATH)
      status["source"] = "git"
    except Exception as exception:
      status["error"] = f"Unable to read remote AGNOS manifest from git: {exception}"
      return status
  else:
    try:
      remote_manifest_text, raw_url = _fetch_remote_file_text(origin_remote, safe_remote_commit, _AGNOS_MANIFEST_PATH)
      status["source"] = raw_url
    except Exception as exception:
      status["error"] = f"Unable to fetch remote AGNOS manifest: {exception}"
      return status

  local_hash = _agnos_manifest_hash(local_manifest_text)
  remote_hash = _agnos_manifest_hash(remote_manifest_text)
  changed_partitions = _agnos_changed_partitions(local_manifest_text, remote_manifest_text)
  status.update({
    "checked": True,
    "available": bool(local_hash and remote_hash and local_hash != remote_hash),
    "localManifestHash": local_hash,
    "remoteManifestHash": remote_hash,
    "changedPartitions": changed_partitions,
    "error": "",
  })
  return status

def _build_shallow_fetch_args(branch):
  return [
    "-c", "gc.auto=0",
    "-c", "maintenance.auto=false",
    "fetch",
    "--progress",
    "--depth=1",
    "--no-recurse-submodules",
    "origin",
    branch,
  ]

def _build_shallow_fetch_commit_args(commit):
  return [
    "-c", "gc.auto=0",
    "-c", "maintenance.auto=false",
    "fetch",
    "--progress",
    "--depth=1",
    "--no-recurse-submodules",
    "origin",
    commit,
  ]

def _run_git_with_progress(repo_path, args, timeout, step, label):
  cmd = [*_git_base_cmd(), *args]

  process = subprocess.Popen(
    cmd,
    cwd=repo_path,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    env=_git_command_env(),
  )

  if process.stdout is None:
    raise RuntimeError("Failed to open git output stream")

  fd = process.stdout.fileno()
  os.set_blocking(fd, False)

  selector = selectors.DefaultSelector()
  selector.register(fd, selectors.EVENT_READ)

  started_at = time.monotonic()
  last_activity_at = started_at
  last_emit_at = 0.0
  last_percent = None
  last_detail = ""
  output_tail = []
  buffer = ""

  def consume_text(text):
    nonlocal buffer
    for char in text:
      if char in ("\r", "\n"):
        if buffer:
          handle_line(buffer)
          buffer = ""
      else:
        buffer += char

  def append_tail(text):
    if not text:
      return
    output_tail.append(text)
    if len(output_tail) > 180:
      del output_tail[:-180]

  def handle_line(text):
    nonlocal last_activity_at, last_emit_at, last_percent, last_detail

    percent, detail, phase = _parse_git_progress_line(text)
    append_tail(detail or text)

    now = time.monotonic()
    last_activity_at = now
    should_emit = False

    if percent is not None:
      safe_percent = _normalize_git_phase_percent(phase, percent)
      if safe_percent in (0.0, 99.0):
        should_emit = True
      elif now - last_emit_at >= _FAST_UPDATE_PROGRESS_UPDATE_INTERVAL_S:
        should_emit = last_percent is None or abs(safe_percent - last_percent) >= 1.0

      if should_emit:
        _set_fast_update_progress(step, label, safe_percent, detail or label)
        last_emit_at = now
        last_percent = safe_percent
        last_detail = detail or label
      else:
        if last_percent is None:
          last_percent = safe_percent
    else:
      if detail and now - last_emit_at >= _FAST_UPDATE_PROGRESS_UPDATE_INTERVAL_S and detail != last_detail:
        fallback_percent = last_percent if last_percent is not None else 0.0
        _set_fast_update_progress(step, label, fallback_percent, detail)
        last_emit_at = now
        last_detail = detail

  try:
    while True:
      now = time.monotonic()
      if timeout and (now - last_activity_at) > timeout:
        try:
          process.kill()
        except Exception:
          pass
        tail = output_tail[-1] if output_tail else ""
        suffix = f" (last output: {tail})" if tail else ""
        raise TimeoutError(f"git {' '.join(args)} stalled for {int(timeout)}s without output{suffix}")

      events = selector.select(timeout=0.5)
      if not events:
        if process.poll() is not None:
          try:
            trailing = os.read(fd, 4096)
          except BlockingIOError:
            trailing = b""
          if trailing:
            last_activity_at = time.monotonic()
            consume_text(trailing.decode("utf-8", errors="replace"))
            continue
          break

        # Heartbeat: if git is quiet (no progress lines), still surface activity.
        now = time.monotonic()
        if now - last_emit_at >= _FAST_UPDATE_PROGRESS_UPDATE_INTERVAL_S:
          if timeout:
            inferred_percent = min(95.0, max(0.0, ((now - started_at) / timeout) * 95.0))
          else:
            inferred_percent = min(95.0, (last_percent or 0.0) + 1.0)
          if last_percent is None or inferred_percent > last_percent:
            last_percent = inferred_percent
          heartbeat_detail = last_detail or f"{label}..."
          _set_fast_update_progress(step, label, last_percent or 0.0, heartbeat_detail)
          last_emit_at = now
        continue

      reached_eof = False
      for _, _ in events:
        try:
          chunk = os.read(fd, 4096)
        except BlockingIOError:
          chunk = b""

        if not chunk:
          # Selector can keep reporting readability on EOF; exit once process ended.
          if process.poll() is not None:
            reached_eof = True
            break
          continue

        last_activity_at = time.monotonic()
        consume_text(chunk.decode("utf-8", errors="replace"))

      if reached_eof:
        break

    if buffer:
      handle_line(buffer)

    return_code = process.wait(timeout=2)
  finally:
    try:
      selector.unregister(fd)
    except Exception:
      pass
    selector.close()
    try:
      process.stdout.close()
    except Exception:
      pass

  if return_code == 0:
    _set_fast_update_progress(step, label, 100.0, last_detail or "Done")

  detail = "\n".join(output_tail[-40:])
  remaining = max(0.0, timeout - (time.monotonic() - started_at)) if timeout else timeout
  if (return_code != 0 and "fetch" in args and "http.version=HTTP/1.1" not in args and
      re.search(r"curl 92|HTTP/2 stream", detail, re.IGNORECASE) and (not timeout or remaining > 0)):
    _set_fast_update_progress(step, label, last_percent or 0.0,
                             "Download connection interrupted. Retrying once using HTTP/1.1...")
    return _run_git_with_progress(repo_path, ["-c", "http.version=HTTP/1.1", *args],
                                  timeout=remaining, step=step, label=label)
  return return_code, detail

def _run_git(repo_path, args, timeout=30):
  return subprocess.run(
    [*_git_base_cmd(), *args],
    cwd=repo_path,
    capture_output=True,
    text=True,
    timeout=timeout,
    check=False,
    env=_git_command_env(),
  )

def _git_stdout(repo_path, args, timeout=15):
  result = _run_git(repo_path, args, timeout=timeout)
  if result.returncode != 0:
    stderr = (result.stderr or "").strip() or (result.stdout or "").strip() or "git command failed"
    raise RuntimeError(stderr)
  return (result.stdout or "").strip()

def _clear_generated_build_state(repo_path):
  """Drop ignored build metadata that is unsafe to carry across revisions."""
  root = Path(repo_path)
  for path in (root / ".sconsign.dblite", root / "cereal" / "gen"):
    try:
      if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
      else:
        path.unlink(missing_ok=True)
    except OSError as exception:
      raise RuntimeError(f"Unable to clear stale build state at {path}: {exception}") from exception

def _git_config_get(repo_path, key):
  try:
    return _git_stdout(repo_path, ["config", "--local", "--get", key], timeout=10)
  except Exception:
    return ""

def _git_config_set(repo_path, key, value):
  result = _run_git(repo_path, ["config", "--local", "--replace-all", key, str(value)], timeout=15)
  if result.returncode != 0:
    raise RuntimeError((result.stderr or result.stdout or f"git config failed for {key}").strip())

def _git_config_unset(repo_path, key):
  result = _run_git(repo_path, ["config", "--local", "--unset-all", key], timeout=15)
  if result.returncode not in (0, 5):
    raise RuntimeError((result.stderr or result.stdout or f"git config unset failed for {key}").strip())

def _git_update_ref(repo_path, ref_name, commit):
  result = _run_git(repo_path, ["update-ref", ref_name, commit], timeout=15)
  if result.returncode != 0:
    raise RuntimeError((result.stderr or result.stdout or f"git update-ref failed for {ref_name}").strip())

def _git_delete_ref(repo_path, ref_name):
  result = _run_git(repo_path, ["update-ref", "-d", ref_name], timeout=15)
  if result.returncode not in (0, 1):
    raise RuntimeError((result.stderr or result.stdout or f"git update-ref delete failed for {ref_name}").strip())

def _git_has_commit(repo_path, commit):
  result = _run_git(repo_path, ["cat-file", "-e", f"{commit}^{{commit}}"], timeout=15)
  return result.returncode == 0

def _save_rollback_target(repo_path, branch, commit):
  safe_commit = str(commit or "").strip()
  if not safe_commit:
    raise RuntimeError("Missing rollback commit")

  safe_branch = str(branch or "").strip()
  if safe_branch and not _is_valid_git_branch_name(repo_path, safe_branch):
    raise RuntimeError(f"Invalid rollback branch '{safe_branch}'")

  _git_update_ref(repo_path, _ROLLBACK_REF, safe_commit)
  if safe_branch:
    _git_config_set(repo_path, _ROLLBACK_BRANCH_CONFIG_KEY, safe_branch)
  else:
    _git_config_unset(repo_path, _ROLLBACK_BRANCH_CONFIG_KEY)
  _git_config_set(repo_path, _ROLLBACK_RECORDED_AT_CONFIG_KEY, datetime.now(timezone.utc).isoformat())

def _clear_rollback_target(repo_path):
  _git_delete_ref(repo_path, _ROLLBACK_REF)
  _git_config_unset(repo_path, _ROLLBACK_BRANCH_CONFIG_KEY)
  _git_config_unset(repo_path, _ROLLBACK_RECORDED_AT_CONFIG_KEY)

def _load_rollback_target(repo_path):
  commit = ""
  try:
    commit = _git_stdout(repo_path, ["rev-parse", "--verify", f"{_ROLLBACK_REF}^{{commit}}"], timeout=10)
  except Exception:
    pass

  branch = _git_config_get(repo_path, _ROLLBACK_BRANCH_CONFIG_KEY)
  recorded_at = _git_config_get(repo_path, _ROLLBACK_RECORDED_AT_CONFIG_KEY)

  current_branch = ""
  current_commit = ""
  try:
    current_branch = _git_stdout(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=10)
    current_commit = _git_stdout(repo_path, ["rev-parse", "HEAD"], timeout=10)
  except Exception:
    pass

  available = bool(commit) and (commit != current_commit or (branch and branch != current_branch))
  return {
    "rollbackBranch": branch,
    "rollbackCommit": commit,
    "rollbackRecordedAt": recorded_at,
    "rollbackAvailable": available,
  }

def _is_valid_git_branch_name(repo_path, branch_name):
  branch = str(branch_name or "").strip()
  if not branch or branch.startswith("-") or "\x00" in branch:
    return False

  result = _run_git(repo_path, ["check-ref-format", "--branch", branch], timeout=10)
  return result.returncode == 0

def _list_origin_branches(repo_path, include_remote=True):
  branches = set()
  remote_error = ""

  if include_remote and _remote_git_check_allowed():
    try:
      remote_heads = _git_stdout(repo_path, ["ls-remote", "--heads", "origin"], timeout=25)
      for line in remote_heads.splitlines():
        parts = line.split()
        if len(parts) < 2:
          continue

        ref = parts[1].strip()
        if not ref.startswith("refs/heads/"):
          continue

        branch = ref[len("refs/heads/"):].strip()
        if branch:
          branches.add(branch)
    except Exception as exception:
      if not _is_deferred_tls_error(exception):
        remote_error = str(exception)

  if not branches:
    try:
      local_refs = _git_stdout(
        repo_path,
        ["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"],
        timeout=15,
      )
      for line in local_refs.splitlines():
        ref = line.strip()
        if not ref or ref in ("origin/HEAD",):
          continue
        if ref.startswith("origin/"):
          ref = ref[len("origin/"):]
        if ref.endswith("/HEAD"):
          continue
        if ref:
          branches.add(ref)
    except Exception as exception:
      if not remote_error:
        remote_error = str(exception)

  return sorted(branches, key=lambda branch: branch.lower()), remote_error

def _repo_has_submodule_entries(repo_path):
  gitmodules_path = Path(repo_path) / ".gitmodules"
  if not gitmodules_path.is_file():
    return False

  try:
    content = gitmodules_path.read_text(encoding="utf-8", errors="replace")
  except Exception:
    # If we cannot inspect .gitmodules, stay conservative and try syncing.
    return True

  return bool(_GIT_SUBMODULE_SECTION_RE.search(content))

def _run_submodule_update_if_needed(repo_path, step=4):
  if not _repo_has_submodule_entries(repo_path):
    _set_fast_update_progress(step, "Updating submodules", 100.0, "No submodules configured.")
    return

  _set_fast_update_progress(step, "Updating submodules", 0.0, "Syncing submodules...")
  submodule_rc, submodule_output = _run_git_with_progress(
    repo_path,
    ["submodule", "update", "--init", "--recursive", "--depth=1", "--progress"],
    timeout=240,
    step=step,
    label="Updating submodules",
  )
  if submodule_rc != 0:
    raise RuntimeError(submodule_output.strip() or "git submodule update failed")

def _finish_update_and_reboot(message):
  _set_fast_update_progress(5, "Rebooting device", 100.0, "Update complete. Please wait for device to reboot.")
  _set_fast_update_state(
    running=False,
    stage="rebooting",
    message=message,
    finishedAt=time.time(),
  )
  # Keep the service online briefly so the UI can fetch and render the reboot notice.
  time.sleep(_FAST_UPDATE_REBOOT_NOTICE_SECONDS)
  HARDWARE.reboot()

def _set_fast_update_error_state(message, exception):
  error_text = str(exception).strip() or "Unknown error"
  if re.search(r"curl 92|HTTP/2 stream|Could not resolve host|Failed to connect", error_text, re.IGNORECASE):
    message = "Download connection interrupted. Check your network connection and retry the update."
  _set_fast_update_state(
    running=False,
    stage="error",
    message=message,
    lastError=error_text,
    finishedAt=time.time(),
    progressStep=0,
    progressTotalSteps=_FAST_UPDATE_TOTAL_STEPS,
    progressStepPercent=0.0,
    progressPercent=0.0,
    progressLabel="Failed",
    progressDetail="Update failed. See Last Error below.",
  )

def _factory_reset_worker():
  started_at = time.time()

  try:
    _set_fast_update_progress(1, "Preparing factory reset", 10.0, "Cleaning up legacy device state...")
    _set_fast_update_state(
      running=True,
      stage="factory-resetting",
      message="Factory reset started. Wiping device state...",
      lastError="",
      lastMode="factory-reset",
      startedAt=started_at,
      finishedAt=0.0,
    )
    _set_fast_update_progress(1, "Preparing factory reset", 100.0, "Factory reset initialized.")

    total_paths = max(1, len(_FACTORY_RESET_WIPE_PATHS))
    for index, path in enumerate(_FACTORY_RESET_WIPE_PATHS, start=1):
      step_percent = ((index - 1) / total_paths) * 100.0
      _set_fast_update_progress(2, "Wiping device data", step_percent, f"Removing {path}...")
      _run_factory_reset_delete(path)
      _set_fast_update_progress(2, "Wiping device data", (index / total_paths) * 100.0, f"Removed {path}.")

    _set_fast_update_progress(3, "Resetting factory state", 100.0, "Legacy device state removed.")

    _set_fast_update_progress(4, "Finalizing reset", 50.0, "Syncing filesystem before reboot...")
    subprocess.run(["sync"], capture_output=True, text=True, timeout=60, check=False)
    _set_fast_update_progress(4, "Finalizing reset", 100.0, "Filesystem sync complete.")

    _finish_update_and_reboot(
      "Factory reset complete. Device is rebooting now. Please wait for reconnection."
    )
  except Exception as exception:
    _set_fast_update_error_state("Factory reset failed.", exception)

def _collect_fast_update_info(include_remote=True):
  repo_path = str(_get_openpilot_root())

  branch = ""
  local_commit = ""
  remote_commit = ""
  update_available = False
  remote_error = ""
  origin_remote = ""
  commits_url = ""
  rollback_data = _load_rollback_target(repo_path)
  agnos_update = _base_agnos_update_status()

  try:
    branch = _git_stdout(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    local_commit = _git_stdout(repo_path, ["rev-parse", "HEAD"])
    try:
      origin_remote = _git_stdout(repo_path, ["config", "--get", "remote.origin.url"])
    except Exception:
      origin_remote = ""
  except Exception as exception:
    return {
      "repoPath": repo_path,
      "branch": branch,
      "localCommit": local_commit,
      "remoteCommit": remote_commit,
      "updateAvailable": False,
      "remoteError": str(exception),
      "originRemote": origin_remote,
      "commitsUrl": commits_url,
      **rollback_data,
      "agnosUpdate": agnos_update,
    }

  agnos_update["targetBranch"] = branch
  agnos_update["localCommit"] = local_commit

  if origin_remote:
    remote = origin_remote.strip()
    if remote.startswith("git@github.com:"):
      remote = "https://github.com/" + remote.split(":", 1)[1]
    elif remote.startswith("ssh://git@github.com/"):
      remote = "https://github.com/" + remote.split("ssh://git@github.com/", 1)[1]
    elif remote.startswith("http://github.com/"):
      remote = "https://github.com/" + remote.split("http://github.com/", 1)[1]

    if remote.startswith("https://github.com/"):
      remote = remote.rstrip("/")
      if remote.endswith(".git"):
        remote = remote[:-4]
      if branch:
        commits_url = f"{remote}/commits/{quote(branch, safe='')}/"

  if branch and include_remote and _remote_git_check_allowed():
    remote_commit, remote_error = _get_remote_branch_commit(repo_path, branch)
    update_available = bool(local_commit and remote_commit and local_commit != remote_commit)
    if remote_commit:
      agnos_update = _build_agnos_update_status(repo_path, origin_remote, local_commit, remote_commit, branch)
  elif not include_remote:
    agnos_update = _base_agnos_update_status(branch, local_commit, "")

  return {
    "repoPath": repo_path,
    "branch": branch,
    "localCommit": local_commit,
    "remoteCommit": remote_commit,
    "updateAvailable": update_available,
    "remoteError": remote_error,
    "originRemote": origin_remote,
    "commitsUrl": commits_url,
    "agnosUpdate": agnos_update,
    **rollback_data,
  }

def _version_install_worker(branch, selection):
  """Resolve, back up and install one immutable revision after explicit confirmation."""
  repo_path = str(_get_openpilot_root())
  installed = None
  try:
    version_install.require_parked()
    if selection == "latest":
      # Preserve the existing branch updater, including boot-time AGNOS handling.
      # Historical compatibility and GitHub-history restrictions apply only to
      # exact revisions, never to an ordinary update to the latest branch head.
      _branch_switch_worker(branch)
      return
    _set_fast_update_progress(1, "Resolving selected version", 10.0, branch)
    target = version_history.resolve_version(repo_path, branch, selection)
    version_install.require_parked()
    # The normal updater owns staging for its whole lifetime. Pause only that
    # daemon and its children, then refuse any still-held Git locks.
    with version_install.suspend_updater() as updater:
      version_install.require_parked()
      version_install.check_repository_idle(repo_path)
      rc, detail = _run_git_with_progress(
        repo_path, _build_shallow_fetch_commit_args(target["commit"]),
        timeout=240, step=1, label="Fetching selected version",
      )
      if rc:
        raise version_install.InstallError(detail or "Unable to fetch the selected revision")
      if _git_stdout(repo_path, ["rev-parse", "FETCH_HEAD^{commit}"]) != target["commit"]:
        raise version_install.InstallError("Fetched revision does not match the selected version")
      version_install.require_parked()
      previous_branch = _git_stdout(repo_path, ["branch", "--show-current"])
      previous_commit = _git_stdout(repo_path, ["rev-parse", "HEAD"])
      result = version_install.install(
        repo_path, target, check_parked=version_install.require_parked,
        progress=_set_fast_update_progress,
      )
      installed = result
      updater.restart_after_install()
      _save_rollback_target(repo_path, previous_branch, previous_commit)
      update_starpilot_toggles()
      version_install.require_parked()
      _set_fast_update_progress(5, "Rebooting device", 100.0, "Selected revision installed. Automatic updates remain paused.")
      # Keep the action locked until shutdown. Another request must not start
      # during the reboot notice, or if the hardware reboot call returns.
      _set_fast_update_state(
        running=True, stage="rebooting", finishedAt=time.time(),
        message=f"Installed {branch} @ {target['commit'][:10]}. Rebooting now.",
        recoveryBackup=result["backup"],
      )
      time.sleep(_FAST_UPDATE_REBOOT_NOTICE_SECONDS)
      version_install.require_parked()
      HARDWARE.reboot()
  except Exception as exception:
    if installed is not None:
      _set_fast_update_error_state(
        "The selected version is installed, but the device could not finish restarting. Park and reboot to activate it.", exception,
      )
      _set_fast_update_state(recoveryBackup=installed["backup"])
    else:
      _set_fast_update_error_state("Selected version installation failed.", exception)

def _fast_update_worker():
  started_at = time.time()
  repo_path = str(_get_openpilot_root())

  try:
    _set_fast_update_progress(1, "Preparing update", 10.0, "Resolving active branch...")
    branch = _git_stdout(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    current_commit = _git_stdout(repo_path, ["rev-parse", "HEAD"])
    try:
      _save_rollback_target(repo_path, branch, current_commit)
    except Exception as exception:
      print(f"Fast update rollback target save failed: {exception}")
    _set_fast_update_progress(1, "Preparing update", 100.0, f"Branch: {branch}")
    _set_fast_update_state(
      running=True,
      stage="updating",
      message=f"Applying shallow update on '{branch}'...",
      lastError="",
      lastBranch=branch,
      lastMode="fetch-reset",
      startedAt=started_at,
      finishedAt=0.0,
    )

    _set_fast_update_progress(2, "Fetching branch snapshot", 0.0, "Fetching latest shallow commit...")
    fetch_rc, fetch_output = _run_git_with_progress(
      repo_path,
      _build_shallow_fetch_args(branch),
      timeout=_FAST_UPDATE_FETCH_TIMEOUT_S,
      step=2,
      label="Fetching branch snapshot",
    )
    if fetch_rc != 0:
      raise RuntimeError(fetch_output.strip() or "git fetch failed")

    _set_fast_update_progress(3, "Applying fetched commit", 20.0, "Resetting repository to fetched head...")
    reset = _run_git(repo_path, ["reset", "--hard", "FETCH_HEAD"], timeout=120)
    if reset.returncode != 0:
      raise RuntimeError((reset.stderr or reset.stdout or "git reset failed").strip())
    _clear_generated_build_state(repo_path)
    _set_fast_update_progress(3, "Applying fetched commit", 100.0, "Repository reset complete.")

    _run_submodule_update_if_needed(repo_path, step=4)
    _finish_update_and_reboot(
      "Update successful. Device is rebooting now. Please wait for reconnection."
    )
  except Exception as exception:
    _set_fast_update_error_state("Fast update failed.", exception)

def _branch_switch_worker(target_branch):
  started_at = time.time()
  repo_path = str(_get_openpilot_root())

  try:
    _set_fast_update_progress(1, "Preparing branch switch", 10.0, f"Target: {target_branch}")
    current_branch = _git_stdout(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    current_commit = _git_stdout(repo_path, ["rev-parse", "HEAD"])
    try:
      _save_rollback_target(repo_path, current_branch, current_commit)
    except Exception as exception:
      print(f"Branch switch rollback target save failed: {exception}")
    _set_fast_update_progress(1, "Preparing branch switch", 100.0, f"{current_branch} -> {target_branch}")
    _set_fast_update_state(
      running=True,
      stage="switching",
      message=f"Switching to '{target_branch}' with shallow fetch...",
      lastError="",
      lastBranch=target_branch,
      lastMode="branch-switch",
      startedAt=started_at,
      finishedAt=0.0,
    )

    _set_fast_update_progress(2, "Fetching branch snapshot", 0.0, f"Fetching '{target_branch}' from origin...")
    fetch_rc, fetch_output = _run_git_with_progress(
      repo_path,
      _build_shallow_fetch_args(target_branch),
      timeout=_FAST_BRANCH_SWITCH_FETCH_TIMEOUT_S,
      step=2,
      label="Fetching branch snapshot",
    )
    if fetch_rc != 0:
      raise RuntimeError(fetch_output.strip() or f"git fetch failed for '{target_branch}'")

    _set_fast_update_progress(3, "Switching branch", 20.0, f"Checking out '{target_branch}'...")
    checkout = _run_git(repo_path, ["checkout", "--force", "-B", target_branch, "FETCH_HEAD"], timeout=120)
    if checkout.returncode != 0:
      raise RuntimeError((checkout.stderr or checkout.stdout or "git checkout failed").strip())

    reset = _run_git(repo_path, ["reset", "--hard", "FETCH_HEAD"], timeout=120)
    if reset.returncode != 0:
      raise RuntimeError((reset.stderr or reset.stdout or "git reset failed").strip())
    _clear_generated_build_state(repo_path)

    _run_git(repo_path, ["branch", "--set-upstream-to", f"origin/{target_branch}", target_branch], timeout=30)
    _set_fast_update_progress(3, "Switching branch", 100.0, f"Now on '{target_branch}'.")

    _run_submodule_update_if_needed(repo_path, step=4)
    version_install.clear_pin()
    _finish_update_and_reboot(
      f"Switched to '{target_branch}'. Device is rebooting now. Please wait for reconnection."
    )
  except Exception as exception:
    _set_fast_update_error_state("Fast branch switch failed.", exception)

def _rollback_worker():
  started_at = time.time()
  repo_path = str(_get_openpilot_root())

  try:
    rollback_state = _load_rollback_target(repo_path)
    target_branch = str(rollback_state.get("rollbackBranch") or "").strip()
    target_commit = str(rollback_state.get("rollbackCommit") or "").strip()
    if not target_commit:
      raise RuntimeError("No previous installed version has been recorded yet.")

    current_branch = _git_stdout(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    current_commit = _git_stdout(repo_path, ["rev-parse", "HEAD"])
    if target_commit == current_commit and (not target_branch or target_branch == current_branch):
      raise RuntimeError("Current install already matches the saved rollback target.")

    if not target_branch:
      raise RuntimeError("Saved rollback branch is missing.")
    if not _is_valid_git_branch_name(repo_path, target_branch):
      raise RuntimeError(f"Saved rollback branch '{target_branch}' is invalid.")

    short_commit = target_commit[:10]
    _set_fast_update_progress(1, "Preparing rollback", 10.0, f"Target: {target_branch} @ {short_commit}")
    _set_fast_update_state(
      running=True,
      stage="rolling-back",
      message=f"Rolling back to the previous installed version on '{target_branch}'...",
      lastError="",
      lastBranch=target_branch,
      lastMode="rollback",
      startedAt=started_at,
      finishedAt=0.0,
    )
    _set_fast_update_progress(1, "Preparing rollback", 100.0, f"{current_branch} -> {target_branch} @ {short_commit}")

    if _git_has_commit(repo_path, target_commit):
      _set_fast_update_progress(2, "Resolving rollback target", 100.0, "Previous installed version is already available locally.")
    else:
      _set_fast_update_progress(2, "Resolving rollback target", 0.0, f"Fetching saved version {short_commit} from origin...")
      fetch_rc, fetch_output = _run_git_with_progress(
        repo_path,
        _build_shallow_fetch_commit_args(target_commit),
        timeout=_FAST_ROLLBACK_FETCH_TIMEOUT_S,
        step=2,
        label="Resolving rollback target",
      )
      if fetch_rc != 0 or not _git_has_commit(repo_path, target_commit):
        raise RuntimeError(fetch_output.strip() or f"Unable to fetch rollback commit {short_commit}")

    _set_fast_update_progress(3, "Applying rollback target", 20.0, f"Checking out {target_branch} @ {short_commit}...")
    checkout = _run_git(repo_path, ["checkout", "--force", "-B", target_branch, target_commit], timeout=120)
    if checkout.returncode != 0:
      raise RuntimeError((checkout.stderr or checkout.stdout or "git checkout failed").strip())

    reset = _run_git(repo_path, ["reset", "--hard", target_commit], timeout=120)
    if reset.returncode != 0:
      raise RuntimeError((reset.stderr or reset.stdout or "git reset failed").strip())
    _clear_generated_build_state(repo_path)

    _run_git(repo_path, ["branch", "--set-upstream-to", f"origin/{target_branch}", target_branch], timeout=30)
    _set_fast_update_progress(3, "Applying rollback target", 100.0, f"Now on {target_branch} @ {short_commit}.")

    _run_submodule_update_if_needed(repo_path, step=4)
    version_install.clear_pin()
    try:
      _clear_rollback_target(repo_path)
    except Exception as exception:
      print(f"Rollback target clear failed: {exception}")
    _finish_update_and_reboot(
      f"Rolled back to the previous installed version on '{target_branch}'. Automatic updates were disabled and the device is rebooting now."
    )
  except Exception as exception:
    _set_fast_update_error_state("Rollback failed.", exception)

def _get_openpilot_root():
  global _openpilot_root_cache
  if _openpilot_root_cache is not None:
    return _openpilot_root_cache

  for parent in Path(__file__).resolve().parents:
    if (parent / "opendbc" / "car").is_dir() or (parent / "selfdrive" / "car").is_dir():
      _openpilot_root_cache = parent
      return _openpilot_root_cache

  # Fallback to repo root shape used in this tree.
  _openpilot_root_cache = Path(__file__).resolve().parents[3]
  return _openpilot_root_cache

def _extract_fingerprint_models_for_make(make_key):
  source_make = FINGERPRINT_MAKE_TO_VALUES_DIR.get(make_key, make_key)
  root = _get_openpilot_root()
  values_candidates = [
    root / "opendbc" / "car" / source_make / "values.py",
    root / "selfdrive" / "car" / source_make / "values.py",
  ]
  values_path = next((path for path in values_candidates if path.is_file()), None)
  if values_path is None:
    return []

  try:
    content = values_path.read_text(encoding="utf-8", errors="replace")
  except Exception:
    return []

  lines = content.splitlines(keepends=True)
  for token in tokenize.generate_tokens(io.StringIO(content).readline):
    if token.type == tokenize.COMMENT:
      line_index, start = token.start[0] - 1, token.start[1]
      end = token.end[1]
      lines[line_index] = lines[line_index][:start] + " " * (end - start) + lines[line_index][end:]
  content = "".join(lines)
  content = re.sub(r'footnotes=\[[^\]]*\],\s*', "", content)

  models = []
  seen = set()

  for platform_match in _FINGERPRINT_PLATFORM_RE.finditer(content):
    platform_name = platform_match.group(1)
    if not _FINGERPRINT_PLATFORM_NAME_RE.match(platform_name):
      continue

    platform_section = platform_match.group(2)
    for name_match in _FINGERPRINT_CARDOCS_RE.finditer(platform_section):
      car_name = name_match.group(1).strip()
      if " " not in car_name:
        continue
      if not _FINGERPRINT_VALID_NAME_RE.match(car_name):
        continue

      if car_name.split(" ", 1)[0].lower() != make_key:
        continue

      dedupe_key = (car_name, platform_name)
      if dedupe_key in seen:
        continue
      seen.add(dedupe_key)
      models.append({"value": platform_name, "label": car_name})

  models.sort(key=lambda entry: entry["label"].lower())
  return models

def _get_fingerprint_catalog():
  global _fingerprint_catalog_cache
  if _fingerprint_catalog_cache is not None:
    return _fingerprint_catalog_cache

  make_options = [{"value": label, "label": label} for label in FINGERPRINT_MAKE_LABELS]
  make_keys = [_normalize_fingerprint_make_key(label) for label in FINGERPRINT_MAKE_LABELS]
  make_label_by_key = {key: label for key, label in zip(make_keys, FINGERPRINT_MAKE_LABELS)}

  models_by_make = {}
  all_models = []
  seen_all = set()
  model_to_label = {}
  labels_by_model = {}
  model_to_make = {}
  label_to_model = {}

  for make_key in make_keys:
    make_label = make_label_by_key.get(make_key, make_key.title())
    entries = _extract_fingerprint_models_for_make(make_key)

    models_by_make[make_key] = entries
    for entry in entries:
      model_value = entry["value"]
      model_label = entry["label"]

      model_to_label.setdefault(model_value, model_label)
      labels_by_model.setdefault(model_value, set()).add(model_label)
      model_to_make.setdefault(model_value, make_label)
      label_to_model.setdefault(model_label, model_value)

      dedupe_key = (model_label, model_value)
      if dedupe_key in seen_all:
        continue
      seen_all.add(dedupe_key)

      all_models.append({
        "value": model_value,
        "label": model_label,
        "make": make_label,
      })

  all_models.sort(key=lambda entry: entry["label"].lower())

  for model_value, labels in labels_by_model.items():
    if len(labels) > 1:
      model_to_label[model_value] = None

  _fingerprint_catalog_cache = {
    "makes": make_options,
    "models_by_make": models_by_make,
    "all_models": all_models,
    "make_label_by_key": make_label_by_key,
    "model_to_label": model_to_label,
    "model_to_make": model_to_make,
    "label_to_model": label_to_model,
  }
  return _fingerprint_catalog_cache

def read_legacy_param_file(key, default_value=""):
  try:
    value_path = Path(params.get_param_path(key))
    if value_path.is_file():
      return value_path.read_text(encoding="utf-8").strip() or default_value
  except Exception:
    pass
  return default_value

def write_legacy_param_file(key, value):
  value_path = Path(params.get_param_path(key))
  value_path.parent.mkdir(parents=True, exist_ok=True)
  tmp_path = value_path.with_name(f".tmp_{value_path.name}")
  tmp_path.write_text(str(value), encoding="utf-8")
  os.replace(tmp_path, value_path)

_layout_type_overrides = None
_layout_param_metadata = None
_favorite_slot_options = None

def _get_layout_param_metadata():
  global _layout_param_metadata
  if _layout_param_metadata is None:
    layout_data = load_settings_catalog()
    if layout_data is None:
      _layout_param_metadata = {}
    else:
      _layout_param_metadata = {
        p["key"]: p
        for section in layout_data
        for p in section.get("params", [])
        if isinstance(p, dict) and "key" in p
      }
  return _layout_param_metadata

def _get_layout_type_overrides():
  global _layout_type_overrides
  if _layout_type_overrides is None:
    layout_param_metadata = _get_layout_param_metadata()
    _layout_type_overrides = {
      key: param_data.get("data_type")
      for key, param_data in layout_param_metadata.items()
      if param_data.get("data_type")
    }
  return _layout_type_overrides

def _get_favorite_slot_options():
  global _favorite_slot_options
  if _favorite_slot_options is not None:
    return _favorite_slot_options

  allowed_keys, _value_types = _get_param_type_info()
  _favorite_slot_options = build_favorite_slot_options(
    lambda key: key in allowed_keys,
    alpha_longitudinal_available=_get_alpha_longitudinal_available(),
  )
  return _favorite_slot_options

def _get_available_favorite_slot_options():
  return filter_favorite_slot_options(
    _get_favorite_slot_options(),
    {"HasRivianAngleHarness": _get_has_rivian_angle_harness()},
  )


def _get_available_controller_action_options():
  return _get_available_favorite_slot_options()


def _favorite_slot_values(options):
  return get_favorite_values(options, params)

def _configured_favorite_slot_values(slots):
  return get_favorite_values(slots, params)

_cached_allowed_keys = None
_cached_param_types = None
_cached_default_values = None
_cached_static_default_values = None

GALAXY_MANUAL_BOOL_PARAM_KEYS = {"IsRHD", "IsRHDOverride"}

def _get_param_type_info():
  global _cached_allowed_keys, _cached_param_types
  if _cached_allowed_keys is None:
    _cached_allowed_keys = {k for k, _, _, _ in starpilot_default_params if k not in EXCLUDED_KEYS}

    types = {}
    for k, default_val, _, _ in starpilot_default_params:
      if k in _cached_allowed_keys:
        if default_val in ("0", "1", b"0", b"1") or isinstance(default_val, bool):
          types[k] = bool
        elif isinstance(default_val, float) or (isinstance(default_val, str) and "." in default_val and default_val.replace(".", "", 1).isdigit()):
          types[k] = float
        elif isinstance(default_val, int) or (isinstance(default_val, str) and default_val.isdigit()):
          types[k] = int
        else:
          types[k] = str

    for k, dt in _get_layout_type_overrides().items():
      if k in types and dt in ("int", "float") and types[k] == bool:
        types[k] = float if dt == "float" else int
      elif k in types and dt == "bool":
        types[k] = bool

    # Zero-valued offsets must stay numeric; legacy inference treats "0" as bool.
    from openpilot.starpilot.common.screen_settings import SCREEN_INT_KEYS
    for k in SCREEN_INT_KEYS:
      if k in _cached_allowed_keys:
        types[k] = int

    for k in GALAXY_MANUAL_BOOL_PARAM_KEYS:
      if k in _cached_allowed_keys:
        types[k] = bool

    # Keep legacy aliases editable for older payloads/UI clients.
    alias_to_key = {
      "Model": "DrivingModel",
      "ModelVersion": "DrivingModelVersion",
      "SecOCKey": "SecOCKeys",
    }
    for alias_key, real_key in alias_to_key.items():
      if real_key in _cached_allowed_keys:
        _cached_allowed_keys.add(alias_key)
        types[alias_key] = types.get(real_key, str)

    _cached_param_types = types
  return _cached_allowed_keys, _cached_param_types

def _get_static_default_param_values():
  global _cached_static_default_values
  if _cached_static_default_values is None:
    _cached_static_default_values = {
      key: default_val
      for key, default_val, _, _ in starpilot_default_params
      if key not in EXCLUDED_KEYS
    }
  return _cached_static_default_values

def _get_default_param_values():
  default_values = dict(_get_static_default_param_values())
  default_values.update(_get_runtime_default_param_overrides())
  return default_values

def _coerce_param_value(raw_value, value_type):
  safe_type = value_type or str

  if safe_type == bool:
    if isinstance(raw_value, bool):
      return raw_value
    if isinstance(raw_value, bytes):
      raw_value = raw_value.decode("utf-8", errors="replace")
    return str(raw_value or "").strip() in ("1", "true", "True")

  if safe_type == float:
    if raw_value in (None, "", b""):
      return 0.0
    try:
      if isinstance(raw_value, bytes):
        raw_value = raw_value.decode("utf-8", errors="replace")
      return float(str(raw_value).strip())
    except Exception:
      return 0.0

  if safe_type == int:
    if raw_value in (None, "", b""):
      return 0
    try:
      if isinstance(raw_value, bytes):
        raw_value = raw_value.decode("utf-8", errors="replace")
      return int(float(str(raw_value).strip()))
    except Exception:
      return 0

  if isinstance(raw_value, bytes):
    return raw_value.decode("utf-8", errors="replace")
  return str(raw_value or "")

def _safe_params_get(key, encoding=None, default=None):
  try:
    if encoding is not None:
      return params.get(key, encoding=encoding)
    return params.get(key)
  except Exception:
    return default

def _safe_params_get_live_raw(key, default=None, block=False):
  try:
    return _params_live_raw.get(key, block=block)
  except Exception:
    return default

def _safe_params_get_bool(key, default=False):
  try:
    return params.get_bool(key)
  except Exception:
    return bool(default)

def _personality_settings_write_locked():
  return _safe_params_get_bool("IsOnroad", default=True) or not _safe_params_get_bool("IsOffroad", default=False)

def _personality_editor_write_locked():
  def road_state(value):
    if isinstance(value, bytes):
      value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
      normalized = value.strip().lower()
      if normalized in ("1", "true"):
        return True
      if normalized in ("0", "false"):
        return False
      return None
    if isinstance(value, bool):
      return value
    if isinstance(value, int) and value in (0, 1):
      return bool(value)
    return None

  is_onroad = road_state(_safe_params_get_live_raw("IsOnroad"))
  is_offroad = road_state(_safe_params_get_live_raw("IsOffroad"))
  return is_onroad is None or is_offroad is None or is_onroad == is_offroad

def _normalize_vasm_config(data):
  if not isinstance(data, dict):
    raise ValueError("Configuration must be a JSON object.")

  try:
    width = int(data.get("width", 0))
    height = int(data.get("height", 0))
  except (TypeError, ValueError) as exc:
    raise ValueError("Invalid camera dimensions.") from exc
  if not (1 <= width <= 8192 and 1 <= height <= 8192):
    raise ValueError("Camera dimensions are out of range.")

  def normalize_polygon(key):
    polygon = data.get(key, [])
    if not isinstance(polygon, list) or len(polygon) > 64:
      raise ValueError(f"{key} must contain at most 64 points.")
    if polygon and len(polygon) < 3:
      raise ValueError(f"{key} requires at least 3 points.")

    normalized = []
    for point in polygon:
      if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise ValueError(f"{key} contains an invalid point.")
      try:
        x, y = float(point[0]), float(point[1])
      except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} contains a non-numeric point.") from exc
      if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= width and 0 <= y <= height):
        raise ValueError(f"{key} contains a point outside the camera frame.")
      normalized.append([round(x), round(y)])
    return normalized

  config = {
    "width": width,
    "height": height,
    "poly_left": normalize_polygon("poly_left"),
    "poly_right": normalize_polygon("poly_right"),
  }
  if not config["poly_left"] and not config["poly_right"]:
    raise ValueError("At least one window polygon is required.")
  return config


def _normalize_pip_preview_config(data):
  if not isinstance(data, dict):
    raise ValueError("Configuration must be a JSON object.")

  try:
    width = int(data.get("width", 0))
    height = int(data.get("height", 0))
  except (TypeError, ValueError) as exc:
    raise ValueError("Invalid camera dimensions.") from exc
  if not (1 <= width <= 8192 and 1 <= height <= 8192):
    raise ValueError("Camera dimensions are out of range.")

  try:
    crop_size = int(data.get("crop_size", 0))
  except (TypeError, ValueError) as exc:
    raise ValueError("Invalid crop size.") from exc
  if not (10 <= crop_size <= 8192):
    raise ValueError("Crop size is out of range.")

  def normalize_center(key):
    point = data.get(key)
    if not point:
      return []
    if not isinstance(point, (list, tuple)) or len(point) != 2:
      raise ValueError(f"{key} requires an (x, y) center point.")
    try:
      x, y = float(point[0]), float(point[1])
    except (TypeError, ValueError) as exc:
      raise ValueError(f"{key} contains a non-numeric point.") from exc
    if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= width and 0 <= y <= height):
      raise ValueError(f"{key} center is outside the camera frame.")
    return [round(x), round(y)]

  config = {
    "width": width,
    "height": height,
    "center_left": normalize_center("center_left"),
    "center_right": normalize_center("center_right"),
    "crop_size": crop_size,
  }
  if not config["center_left"] and not config["center_right"]:
    raise ValueError("At least one window center is required.")
  return config


def _decode_json_object(value):
  if isinstance(value, bytes):
    value = value.decode("utf-8", errors="replace")
  if isinstance(value, str):
    try:
      value = json.loads(value)
    except json.JSONDecodeError:
      return {}
  return value if isinstance(value, dict) else {}


def _is_blank_param_raw(raw_value):
  if raw_value is None:
    return True
  if isinstance(raw_value, bytes):
    return len(raw_value.strip()) == 0
  if isinstance(raw_value, str):
    return len(raw_value.strip()) == 0
  return False

def _has_runtime_default_value(key, raw_value):
  if _is_blank_param_raw(raw_value):
    return False

  try:
    if isinstance(raw_value, bytes):
      raw_value = raw_value.decode("utf-8", errors="replace")
    numeric_value = float(str(raw_value).strip())
    if not math.isfinite(numeric_value):
      return False
    if key in _RUNTIME_DEFAULT_ZERO_OK_KEYS:
      return True
    return numeric_value != 0.0
  except Exception:
    return True

_PERSONALITY_PROFILES_WRITE_LOCK = threading.Lock()


def _serialize_personality_profile_writes(view):
  @wraps(view)
  def wrapped(*args, **kwargs):
    if request.method not in ("PUT", "POST"):
      return view(*args, **kwargs)
    with _PERSONALITY_PROFILES_WRITE_LOCK:
      return view(*args, **kwargs)
  return wrapped


def _get_detected_ev_tuning():
  cp_bytes = _safe_params_get_live_raw("CarParamsPersistent")
  if not cp_bytes:
    return False
  try:
    with car.CarParams.from_bytes(cp_bytes) as cp:
      return default_ev_tuning_enabled(cp)
  except Exception:
    return False


def _get_detected_truck_tuning():
  cp_bytes = _safe_params_get_live_raw("CarParamsPersistent")
  if not cp_bytes:
    return False
  try:
    with car.CarParams.from_bytes(cp_bytes) as cp:
      return is_truck_fingerprint(cp.carFingerprint)
  except Exception:
    return False


def _get_effective_car_make(stored_make):
  if isinstance(stored_make, bytes):
    stored_make = stored_make.decode("utf-8", errors="replace")
  if str(stored_make or "").strip().lower() not in ("", "mock"):
    return stored_make
  cp_bytes = _safe_params_get_live_raw("CarParamsPersistent")
  if cp_bytes:
    try:
      with car.CarParams.from_bytes(cp_bytes) as cp:
        if cp.brand and cp.brand != "mock":
          make_key = str(cp.carFingerprint).split("_", 1)[0]
          return next((make for make in FINGERPRINT_MAKE_LABELS if make.lower() == make_key.lower()), cp.brand.title())
    except Exception:
      pass
  return stored_make


def _get_effective_legacy_custom_accel_curve(
  ev_tuning: bool, truck_tuning: bool, *, acceleration_profile=None, custom_enabled: bool | None = None,
) -> list[float]:
  target_axis = np.array(ACCELERATION_SPEEDS_MPH, dtype=float) * 0.44704

  def sample(values, breakpoints):
    return [round(interpolate_accel_profile(float(speed), values, breakpoints), 4) for speed in target_axis]

  preset_curve = get_accel_profile_curve_values(
    normalize_acceleration_profile(_safe_params_get_live_raw("AccelerationProfile") if acceleration_profile is None else acceleration_profile),
    ev_tuning,
    truck_tuning,
  )
  if not (_safe_params_get_bool("CustomAccelProfile") if custom_enabled is None else custom_enabled):
    return sample(preset_curve, A_CRUISE_MAX_BP_CUSTOM)

  raw_legacy = {key: _safe_params_get_live_raw(key) for key in CUSTOM_ACCEL_PROFILE_PARAM_KEYS}
  if custom_accel_profile_is_initialized(_safe_params_get_live_raw(CUSTOM_ACCEL_PROFILE_INITIALIZED_KEY), raw_legacy):
    try:
      legacy_values = [float(raw_legacy[key]) for key in CUSTOM_ACCEL_PROFILE_PARAM_KEYS]
      if all(math.isfinite(value) and CUSTOM_ACCEL_PROFILE_VALUE_MIN <= value <= CUSTOM_ACCEL_PROFILE_VALUE_MAX for value in legacy_values):
        preset_curve = legacy_values
    except (TypeError, ValueError):
      pass

  if _get_custom_accel_profile_breakpoints_initialized():
    try:
      breakpoints, values = parse_custom_accel_profile_curve(
        _safe_params_get_live_raw(CUSTOM_ACCEL_PROFILE_POINT_COUNT_KEY),
        [_safe_params_get_live_raw(key) for key in CUSTOM_ACCEL_PROFILE_BREAKPOINT_PARAM_KEYS],
        [_safe_params_get_live_raw(key) for key in CUSTOM_ACCEL_PROFILE_POINT_VALUE_PARAM_KEYS],
      )
      return sample(values, breakpoints)
    except (TypeError, ValueError):
      pass
  return sample(preset_curve, A_CRUISE_MAX_BP_CUSTOM)


def _get_effective_legacy_following_curve(profile_id: str) -> list[float]:
  builtin_follow = {
    "aggressive": 1.25,
    "standard": 1.45,
    "relaxed": 1.75,
  }
  if profile_id in builtin_follow and not _safe_params_get_bool("CustomPersonalities"):
    return [builtin_follow[profile_id]] * len(FOLLOWING_SPEEDS_MPH)

  defaults = {
    "TrafficFollow": 0.75,
    "AggressiveFollow": 1.25,
    "AggressiveFollowHigh": 1.0,
    "StandardFollow": 1.45,
    "StandardFollowHigh": 1.2,
    "RelaxedFollow": 1.6,
    "RelaxedFollowHigh": 1.4,
  }

  def follow_value(key: str) -> float:
    try:
      parsed = float(_safe_params_get_live_raw(key, defaults[key]) if _safe_params_get_bool("CustomPersonalities") else defaults[key])
    except (TypeError, ValueError):
      parsed = defaults[key]
    if not math.isfinite(parsed):
      parsed = defaults[key]
    return float(np.clip(parsed, 0.5 if key == "TrafficFollow" else CURVE_BOUNDS["following"][0], CURVE_BOUNDS["following"][1]))

  if profile_id == "traffic":
    breakpoints = (0.0, 25.0 / CV.MPH_TO_MS)
    values = (follow_value("TrafficFollow"), follow_value("RelaxedFollow"))
  elif profile_id in ("aggressive", "standard", "relaxed"):
    prefix = profile_id.capitalize()
    breakpoints = (45.0, 70.0)
    values = (follow_value(f"{prefix}Follow"), follow_value(f"{prefix}FollowHigh"))
  else:
    raise ValueError(f"Unknown personality: {profile_id}")
  return [round(float(point), 4) for point in np.interp(FOLLOWING_SPEEDS_MPH, breakpoints, values)]


def _get_dom_personality_reference_curves(ev_tuning: bool) -> dict[str, dict[str, list[float]]]:
  """Sample Dom's configured base curves, without live driving modifiers.

  Use global powertrain/tuning switches for Dom default, just as the runtime
  does. Named personality presets have a separate detected-powertrain policy.
  """
  from openpilot.starpilot.common.accel_profile import A_CRUISE_MAX_VALS_TRAFFIC_ALL

  truck_tuning = _safe_params_get_bool("TruckTuning")
  raw_ev = _safe_params_get_live_raw("EVTuning")
  ev_tuning = (ev_tuning if raw_ev in (None, b"", "") else _safe_params_get_bool("EVTuning")) and not truck_tuning
  tuning = _safe_params_get_bool("LongitudinalTune")
  custom_accel = _safe_params_get_bool("AdvancedLongitudinalTune") and _safe_params_get_bool("CustomAccelProfile")
  acceleration_profile = normalize_acceleration_profile(_safe_params_get_live_raw("AccelerationProfile")) if tuning or custom_accel else 0
  deceleration_profile = normalize_deceleration_profile(_safe_params_get_live_raw("DecelerationProfile", 1)) if tuning else 1
  map_gears = _safe_params_get_bool("QOLLongitudinal") and _safe_params_get_bool("MapGears")
  # A static speed graph uses the normal-gear base; live Eco/Sport, weather and
  # overspeed/lead modifiers remain on Dom's existing controller paths.
  if map_gears and _safe_params_get_bool("MapAcceleration") and not custom_accel:
    acceleration_profile = 0
  if map_gears and _safe_params_get_bool("MapDeceleration"):
    deceleration_profile = 0
  acceleration = _get_effective_legacy_custom_accel_curve(
    ev_tuning, truck_tuning, acceleration_profile=acceleration_profile, custom_enabled=custom_accel,
  )
  traffic_acceleration = [round(interpolate_accel_profile(speed * CV.MPH_TO_MS, A_CRUISE_MAX_VALS_TRAFFIC_ALL), 4)
                          for speed in ACCELERATION_SPEEDS_MPH]
  return {
    profile: {
      "acceleration": list(traffic_acceleration if profile == "traffic" else acceleration),
      "braking": [0.35 if profile == "traffic" else {0: 1.0, 1: 0.5, 2: 2.0}[deceleration_profile]] * len(BRAKING_SPEEDS_MPH),
      "following": _get_effective_legacy_following_curve(profile),
    }
    for profile in ("traffic", "aggressive", "standard", "relaxed")
  }


def _get_runtime_default_param_overrides():
  overrides = {}
  static_defaults = _get_static_default_param_values()

  for key, stock_key in _RUNTIME_DEFAULT_STOCK_KEYS.items():
    stock_raw = _safe_params_get_live_raw(stock_key)
    if _has_runtime_default_value(key, stock_raw):
      overrides[key] = stock_raw
      overrides[stock_key] = stock_raw

  cp_bytes = _safe_params_get_live_raw("CarParamsPersistent")
  if cp_bytes:
    try:
      with car.CarParams.from_bytes(cp_bytes) as cp:
        overrides["EVTuning"] = default_ev_tuning_enabled(cp)

        car_param_defaults = {
          "SteerDelay": full_lateral_delay(getattr(cp, "steerActuatorDelay", 0.0)),
          "SteerRatio": getattr(cp, "steerRatio", None),
          "LongitudinalActuatorDelay": getattr(cp, "longitudinalActuatorDelay", None),
          "StartAccel": getattr(cp, "startAccel", None),
          "StopAccel": getattr(cp, "stopAccel", None),
          "StoppingDecelRate": getattr(cp, "stoppingDecelRate", None),
          "VEgoStarting": getattr(cp, "vEgoStarting", None),
          "VEgoStopping": getattr(cp, "vEgoStopping", None),
        }

        for key, value in car_param_defaults.items():
          if key in overrides or value is None:
            continue
          numeric_value = float(value)
          if not math.isfinite(numeric_value):
            continue
          if key not in _RUNTIME_DEFAULT_ZERO_OK_KEYS and numeric_value == 0.0:
            continue
          overrides[key] = value
    except Exception:
      pass

  ev_tuning_raw = _safe_params_get_live_raw("EVTuning")
  truck_tuning_raw = _safe_params_get_live_raw("TruckTuning")
  acceleration_profile_raw = _safe_params_get_live_raw("AccelerationProfile")

  ev_tuning = _coerce_param_value(
    ev_tuning_raw if not _is_blank_param_raw(ev_tuning_raw) else overrides.get("EVTuning", static_defaults.get("EVTuning", "0")),
    bool,
  )
  truck_tuning = _coerce_param_value(
    truck_tuning_raw if not _is_blank_param_raw(truck_tuning_raw) else static_defaults.get("TruckTuning", "0"),
    bool,
  )
  if truck_tuning:
    ev_tuning = False

  acceleration_profile = normalize_acceleration_profile(
    acceleration_profile_raw if not _is_blank_param_raw(acceleration_profile_raw) else static_defaults.get("AccelerationProfile", "0")
  )
  overrides.update(build_custom_accel_profile_defaults(acceleration_profile, ev_tuning, truck_tuning))
  overrides.update(get_custom_accel_profile_curve_defaults(acceleration_profile, ev_tuning, truck_tuning))

  return overrides

def _get_current_param_value(key, value_type, defaults_lookup=None):
  if key == CUSTOM_ACCEL_PROFILE_INITIALIZED_KEY:
    return _get_custom_accel_profile_initialized()
  if key == CUSTOM_ACCEL_PROFILE_BREAKPOINTS_INITIALIZED_KEY:
    return _get_custom_accel_profile_breakpoints_initialized()

  if key == "LeadIndicator":
    return _get_lead_indicator_enabled(defaults_lookup)

  if key == "IsRHD" and not _safe_params_get_bool("IsRHDOverride"):
    return _safe_params_get_bool("IsRhdDetected")

  if key in CUSTOM_ACCEL_PROFILE_PARAM_KEYS and not _get_custom_accel_profile_initialized():
    if defaults_lookup is None:
      defaults_lookup = _get_default_param_values()
    return _coerce_param_value(defaults_lookup.get(key), value_type)

  if key in CUSTOM_ACCEL_PROFILE_CURVE_PARAM_KEYS and not _get_custom_accel_profile_breakpoints_initialized():
    if defaults_lookup is None:
      defaults_lookup = _get_default_param_values()
    return _coerce_param_value(_get_legacy_compatible_curve_value(key, defaults_lookup), value_type)

  raw_value = _safe_params_get_live_raw(key)
  if _is_blank_param_raw(raw_value):
    if defaults_lookup is None:
      defaults_lookup = _get_default_param_values()
    raw_value = defaults_lookup.get(key)
  value = _coerce_param_value(raw_value, value_type)
  if key in ("Model", "DrivingModel") and isinstance(value, str):
    return canonical_model_key(value)
  return value


def _get_lead_indicator_enabled(defaults_lookup=None):
  if defaults_lookup is None:
    defaults_lookup = _get_default_param_values()

  hide_raw = _safe_params_get_live_raw("HideLeadMarker")
  if _is_blank_param_raw(hide_raw):
    hide_raw = defaults_lookup.get("HideLeadMarker", "0")

  return not _coerce_param_value(hide_raw, bool)


def _get_custom_accel_profile_initialized():
  raw_values = {
    key: _safe_params_get_live_raw(key)
    for key in CUSTOM_ACCEL_PROFILE_PARAM_KEYS
  }
  return custom_accel_profile_is_initialized(
    _safe_params_get_live_raw(CUSTOM_ACCEL_PROFILE_INITIALIZED_KEY),
    raw_values,
  )


def _get_custom_accel_profile_breakpoints_initialized():
  return _coerce_param_value(_safe_params_get_live_raw(CUSTOM_ACCEL_PROFILE_BREAKPOINTS_INITIALIZED_KEY), bool)


def _get_legacy_compatible_curve_value(key, defaults_lookup):
  if key == CUSTOM_ACCEL_PROFILE_POINT_COUNT_KEY:
    return CUSTOM_ACCEL_PROFILE_DEFAULT_POINT_COUNT

  if key in CUSTOM_ACCEL_PROFILE_BREAKPOINT_PARAM_KEYS:
    index = CUSTOM_ACCEL_PROFILE_BREAKPOINT_PARAM_KEYS.index(key)
    return CUSTOM_ACCEL_PROFILE_DEFAULT_BREAKPOINTS_MPH[index]

  if key in CUSTOM_ACCEL_PROFILE_POINT_VALUE_PARAM_KEYS:
    index = CUSTOM_ACCEL_PROFILE_POINT_VALUE_PARAM_KEYS.index(key)
    if index < len(CUSTOM_ACCEL_PROFILE_PARAM_KEYS):
      legacy_key = CUSTOM_ACCEL_PROFILE_PARAM_KEYS[index]
      return _get_current_param_value(legacy_key, float, defaults_lookup)

  return defaults_lookup.get(key)


def _seed_custom_accel_profile_curve(defaults_lookup):
  seeded = {}
  for key in CUSTOM_ACCEL_PROFILE_CURVE_PARAM_KEYS:
    value = _get_legacy_compatible_curve_value(key, defaults_lookup)
    params.put(key, _serialize_param_write_value(value))
    seeded[key] = value
  params.put_bool(CUSTOM_ACCEL_PROFILE_BREAKPOINTS_INITIALIZED_KEY, True)
  return seeded

def _serialize_param_write_value(raw_value):
  if isinstance(raw_value, bool):
    return "1" if raw_value else "0"
  if isinstance(raw_value, bytes):
    return raw_value.decode("utf-8", errors="replace")
  return "" if raw_value is None else str(raw_value)

def _offroad_excessive_actuation_type():
  alert = _safe_params_get_live_raw("Offroad_ExcessiveActuation")
  if not alert:
    return ""

  if isinstance(alert, bytes):
    try:
      alert = json.loads(alert.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
      return ""
  elif isinstance(alert, str):
    try:
      alert = json.loads(alert)
    except json.JSONDecodeError:
      return ""

  if not isinstance(alert, dict):
    return ""

  extra = alert.get("extra", "")
  if isinstance(extra, bytes):
    extra = extra.decode("utf-8", errors="replace")
  return str(extra).strip().lower()

def _apply_cellular_metered_setting(metered_enabled):
  """Apply GsmMetered changes to active NetworkManager GSM profiles."""
  if not shutil.which("nmcli"):
    return {"profiles": [], "warnings": ["nmcli not found; parameter saved but modem profile was not updated."]}

  metered_mode = "unknown" if bool(metered_enabled) else "no"
  updated_profiles = []
  warnings = []

  try:
    list_result = subprocess.run(
      ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
      capture_output=True, text=True, timeout=10, check=False
    )
  except Exception as error:
    return {"profiles": [], "warnings": [f"Failed to list network profiles: {error}"]}

  if list_result.returncode != 0:
    stderr = (list_result.stderr or "").strip()
    return {"profiles": [], "warnings": [f"Failed to list network profiles: {stderr or 'unknown error'}"]}

  gsm_profiles = []
  for line in (list_result.stdout or "").splitlines():
    line = line.strip()
    if not line:
      continue

    try:
      name, conn_type = line.rsplit(":", 1)
    except ValueError:
      continue

    if conn_type.strip() == "gsm" and name.strip():
      gsm_profiles.append(name.strip())

  for profile_name in gsm_profiles:
    try:
      result = subprocess.run(
        ["nmcli", "connection", "modify", profile_name, "connection.metered", metered_mode],
        capture_output=True, text=True, timeout=10, check=False
      )
      if result.returncode == 0:
        updated_profiles.append(profile_name)
      else:
        stderr = (result.stderr or "").strip()
        warnings.append(f"Failed to update '{profile_name}': {stderr or 'unknown error'}")
    except Exception as error:
      warnings.append(f"Failed to update '{profile_name}': {error}")

  # Re-activate active GSM profiles so the new metered setting takes effect immediately.
  try:
    active_result = subprocess.run(
      ["nmcli", "-t", "-f", "NAME,TYPE,STATE", "connection", "show", "--active"],
      capture_output=True, text=True, timeout=10, check=False
    )

    if active_result.returncode == 0:
      for line in (active_result.stdout or "").splitlines():
        line = line.strip()
        if not line:
          continue
        try:
          name, conn_type, state = line.rsplit(":", 2)
        except ValueError:
          continue

        if conn_type.strip() != "gsm" or state.strip() != "activated":
          continue

        profile_name = name.strip()
        if not profile_name:
          continue

        subprocess.run(["nmcli", "connection", "down", profile_name], capture_output=True, text=True, timeout=10, check=False)
        subprocess.run(["nmcli", "connection", "up", profile_name], capture_output=True, text=True, timeout=20, check=False)
  except Exception as error:
    warnings.append(f"Failed to cycle active GSM connection: {error}")

  return {"profiles": updated_profiles, "warnings": warnings}

def _format_longitudinal_personality(value):
  mapping = {
    "0": "Aggressive",
    "1": "Standard",
    "2": "Relaxed",
  }
  text = str(value or "").strip()
  if text in mapping:
    return mapping[text]
  return f"Unknown ({text})" if text else "Unknown"

def _resolve_troubleshoot_current_value(key, value_type, default_values):
  safe_type = value_type or str

  if safe_type == bool:
    raw_value = _safe_params_get_live_raw(key)
    if not _is_blank_param_raw(raw_value):
      return _coerce_param_value(raw_value, safe_type)

    default_raw_value = default_values.get(key)
    if not _is_blank_param_raw(default_raw_value):
      return _coerce_param_value(default_raw_value, safe_type)

    return _safe_params_get_bool(key)

  raw_value = _safe_params_get_live_raw(key)
  if not _is_blank_param_raw(raw_value):
    return _coerce_param_value(raw_value, safe_type)

  stock_key = f"{key}Stock"
  if stock_key in default_values:
    stock_raw_value = _safe_params_get_live_raw(stock_key)
    if not _is_blank_param_raw(stock_raw_value):
      return _coerce_param_value(stock_raw_value, safe_type)

  default_raw_value = default_values.get(key)
  if not _is_blank_param_raw(default_raw_value):
    return _coerce_param_value(default_raw_value, safe_type)

  return _coerce_param_value(raw_value, safe_type)

def _resolve_troubleshoot_default_value(key, value_type, default_values):
  safe_type = value_type or str
  default_raw_value = default_values.get(key)
  if not _is_blank_param_raw(default_raw_value):
    return _coerce_param_value(default_raw_value, safe_type)

  stock_key = f"{key}Stock"
  if stock_key in default_values:
    stock_current_raw = _safe_params_get_live_raw(stock_key)
    if not _is_blank_param_raw(stock_current_raw):
      return _coerce_param_value(stock_current_raw, safe_type)

    stock_default_raw = default_values.get(stock_key)
    if not _is_blank_param_raw(stock_default_raw):
      return _coerce_param_value(stock_default_raw, safe_type)

  return _coerce_param_value(default_raw_value, safe_type)

def _normalize_troubleshoot_current_display_value(key, current_value, default_value):
  if key != "SteerDelay":
    return current_value

  try:
    full_current_delay = full_lateral_delay(float(current_value))
    numeric_default = float(default_value)
  except (TypeError, ValueError):
    return current_value

  if math.isfinite(full_current_delay) and math.isfinite(numeric_default) and math.isclose(full_current_delay, numeric_default, abs_tol=1e-6):
    return default_value
  return current_value

def _normalize_live_delay_status(status):
  status_text = str(status or "").strip().lower()
  if status_text in {"estimated", "unestimated", "invalid"}:
    return status_text

  try:
    if status == log.LiveDelayData.Status.estimated:
      return "estimated"
    if status == log.LiveDelayData.Status.unestimated:
      return "unestimated"
    if status == log.LiveDelayData.Status.invalid:
      return "invalid"
  except Exception:
    pass

  return status_text

def _get_steer_delay_learned_text():
  live_delay_bytes = _safe_params_get_live_raw("LiveDelay")
  if not live_delay_bytes:
    return "Unavailable"

  current_cp_bytes = _safe_params_get_live_raw("CarParamsPersistent")
  previous_cp_bytes = _safe_params_get_live_raw("CarParamsPrevRoute")
  if current_cp_bytes and previous_cp_bytes:
    try:
      with car.CarParams.from_bytes(current_cp_bytes) as current_cp, car.CarParams.from_bytes(previous_cp_bytes) as previous_cp:
        current_fingerprint = str(getattr(current_cp, "carFingerprint", "") or "")
        previous_fingerprint = str(getattr(previous_cp, "carFingerprint", "") or "")
        if current_fingerprint and previous_fingerprint and current_fingerprint != previous_fingerprint:
          return "Unavailable"
    except Exception:
      pass

  try:
    live_delay = messaging.log_from_bytes(live_delay_bytes, log.Event).liveDelay
  except Exception:
    return "Unavailable"

  estimate = _safe_float(getattr(live_delay, "lateralDelayEstimate", 0.0), 0.0)
  cal_perc = int(max(0, min(100, _safe_float(getattr(live_delay, "calPerc", 0), 0))))
  status = _normalize_live_delay_status(getattr(live_delay, "status", ""))

  if status == "estimated":
    return f"Complete ({estimate:.2f}s)"
  if status == "invalid":
    return f"Invalid ({estimate:.2f}s)"
  if status == "unestimated":
    return f"Learning {cal_perc}% ({estimate:.2f}s)"

  return f"{estimate:.2f}s"

def _get_troubleshoot_learned_values():
  return {
    "SteerDelay": _get_steer_delay_learned_text(),
  }

def _get_safety_snapshot_text():
  cp_bytes = params.get("CarParamsPersistent")
  if not cp_bytes:
    return "Unavailable"

  try:
    with car.CarParams.from_bytes(cp_bytes) as cp:
      safety_configs = list(getattr(cp, "safetyConfigs", []))
      if not safety_configs:
        return "Unavailable"

      entries = []
      for config in safety_configs:
        model = str(getattr(config, "safetyModel", "unknown"))
        safety_param = int(getattr(config, "safetyParam", 0))
        entries.append(f"{model} ({safety_param} / 0x{safety_param:X})")

      return ", ".join(entries) if entries else "Unavailable"
  except Exception:
    return "Unavailable"

def _get_fingerprint_snapshot_text():
  cp_bytes = params.get("CarParamsPersistent")
  cp_fingerprint = ""
  try:
    if cp_bytes:
      with car.CarParams.from_bytes(cp_bytes) as cp:
        cp_fingerprint = str(getattr(cp, "carFingerprint", "") or "").strip()
  except Exception:
    cp_fingerprint = ""

  model_name = str(params.get("CarModelName", encoding="utf-8") or "").strip()
  model_value = str(params.get("CarModel", encoding="utf-8") or "").strip()

  if model_name and model_value:
    catalog = _get_fingerprint_catalog()
    if model_value in catalog["model_to_make"] and not any(
      entry["value"] == model_value and entry["label"] == model_name
      for entry in catalog["all_models"]
    ):
      return f"Mismatch: {model_name} vs {model_value}; reselect your vehicle"
    return f"{model_name} ({model_value})"
  if model_name:
    return model_name
  if model_value:
    return model_value
  if cp_fingerprint:
    return cp_fingerprint
  return "Unknown"

def _snapshot_bool_text(value):
  if value is True:
    return "Yes"
  if value is False:
    return "No"
  return "Unavailable"

def _build_vehicle_fault_status():
  unavailable_items = [
    {"label": "Cruise Fault", "value": "Unavailable", "severity": "neutral"},
    {"label": "LKAS Fault", "value": "Unavailable", "severity": "neutral"},
    {"label": "CAN Valid", "value": "Unavailable", "severity": "neutral"},
    {"label": "Cruise Available", "value": "Unavailable", "severity": "neutral"},
    {"label": "Cruise Engaged", "value": "Unavailable", "severity": "neutral"},
  ]

  is_onroad = params.get_bool("IsOnroad")
  unavailable_summary = "Vehicle fault status is unavailable while offroad."
  unavailable_severity = "neutral"
  if is_onroad:
    unavailable_summary = "Waiting for live vehicle fault status..."
    unavailable_severity = "warn"

  try:
    sm = messaging.SubMaster(["carState"], poll="carState")
    sm.update(100)
    has_live_car_state = sm.seen["carState"] and sm.alive["carState"] and sm.valid["carState"]
    if not has_live_car_state:
      return {
        "available": False,
        "summary": unavailable_summary,
        "summarySeverity": unavailable_severity,
        "items": unavailable_items,
      }

    car_state = sm["carState"]
    cruise_state = getattr(car_state, "cruiseState", None)

    cruise_faulted = bool(getattr(car_state, "accFaulted", False))
    steer_fault_temporary = bool(getattr(car_state, "steerFaultTemporary", False))
    steer_fault_permanent = bool(getattr(car_state, "steerFaultPermanent", False))
    can_valid = bool(getattr(car_state, "canValid", False))
    cruise_available = bool(getattr(cruise_state, "available", False)) if cruise_state is not None else None
    cruise_enabled = bool(getattr(cruise_state, "enabled", False)) if cruise_state is not None else None

    if steer_fault_permanent:
      lkas_fault_value = "Permanent"
      lkas_fault_severity = "fault"
    elif steer_fault_temporary:
      lkas_fault_value = "Temporary"
      lkas_fault_severity = "warn"
    else:
      lkas_fault_value = "Clear"
      lkas_fault_severity = "ok"

    active_statuses = []
    if cruise_faulted:
      active_statuses.append("cruise fault")
    if steer_fault_permanent:
      active_statuses.append("permanent LKAS fault")
    elif steer_fault_temporary:
      active_statuses.append("temporary LKAS fault")
    if not can_valid:
      active_statuses.append("CAN invalid")

    if active_statuses:
      summary = "Active status: " + ", ".join(active_statuses) + "."
      summary_severity = "fault" if ("cruise fault" in active_statuses or "permanent LKAS fault" in active_statuses or "CAN invalid" in active_statuses) else "warn"
    else:
      summary = "No active cruise or LKAS faults detected."
      summary_severity = "ok"

    return {
      "available": True,
      "summary": summary,
      "summarySeverity": summary_severity,
      "items": [
        {"label": "Cruise Fault", "value": "Faulted" if cruise_faulted else "Clear", "severity": "fault" if cruise_faulted else "ok"},
        {"label": "LKAS Fault", "value": lkas_fault_value, "severity": lkas_fault_severity},
        {"label": "CAN Valid", "value": "Yes" if can_valid else "No", "severity": "ok" if can_valid else "fault"},
        {"label": "Cruise Available", "value": "Yes" if cruise_available else "No", "severity": "ok" if cruise_available else "neutral"},
        {"label": "Cruise Engaged", "value": "Yes" if cruise_enabled else "No", "severity": "ok" if cruise_enabled else "neutral"},
      ],
    }
  except Exception:
    return {
      "available": False,
      "summary": unavailable_summary,
      "summarySeverity": unavailable_severity,
      "items": unavailable_items,
    }

def _get_starpilot_toggles_snapshot():
  raw_toggles = _safe_params_get_live_raw("StarPilotToggles")
  if not raw_toggles:
    return {}

  try:
    if isinstance(raw_toggles, bytes):
      raw_toggles = raw_toggles.decode("utf-8", errors="replace")
    parsed = json.loads(str(raw_toggles) or "{}")
    return parsed if isinstance(parsed, dict) else {}
  except Exception:
    return {}

def _get_has_radar():
  cp_bytes = _safe_params_get_live_raw("CarParamsPersistent")
  if not cp_bytes:
    return False

  try:
    with car.CarParams.from_bytes(cp_bytes) as cp:
      return not bool(getattr(cp, "radarUnavailable", False))
  except Exception:
    return False

def _get_vehicle_parked():
  try:
    sm = messaging.SubMaster(["carState"], poll="carState")
    sm.update(100)
    if not sm.seen["carState"] or not sm.alive["carState"] or not sm.valid["carState"]:
      return False

    gear_shifter = getattr(getattr(car, "CarState", None), "GearShifter", None)
    park_value = getattr(gear_shifter, "park", None)
    return park_value is not None and getattr(sm["carState"], "gearShifter", None) == park_value
  except Exception:
    return False

def _get_longitudinal_mode_capable():
  # Do not authorize from a default or a stale toggle snapshot. Pending disable
  # also blocks selection until the driving stack has regenerated CarParams.
  if _safe_params_get_bool("DisableOpenpilotLongitudinal", default=True):
    return False
  cp_bytes = _safe_params_get_live_raw("CarParamsPersistent")
  if not cp_bytes:
    return False
  try:
    with car.CarParams.from_bytes(cp_bytes) as cp:
      if cp.alphaLongitudinalAvailable and not _safe_params_get_bool("AlphaLongitudinalEnabled", default=False):
        return False
      return bool(cp.openpilotLongitudinalControl)
  except Exception:
    return False

def _get_is_tici_or_tizi():
  return HARDWARE.get_device_type() in ("tici", "tizi")

def _get_alpha_longitudinal_available():
  cp_bytes = capability_car_params_bytes(params)
  if not cp_bytes:
    return False

  try:
    with car.CarParams.from_bytes(cp_bytes) as cp:
      return bool(getattr(cp, "alphaLongitudinalAvailable", False))
  except Exception:
    return False

def _get_has_rivian_angle_harness():
  cp_bytes = _safe_params_get_live_raw("CarParamsPersistent")
  if not cp_bytes:
    return False

  try:
    with car.CarParams.from_bytes(cp_bytes) as cp:
      return cp.brand == "rivian" and bool(int(getattr(cp, "flags", 0)) & RIVIAN_ANGLE_HARNESS_FLAG)
  except Exception:
    return False

def _get_hardware_snapshot_items():
  starpilot_toggles = _get_starpilot_toggles_snapshot()

  has_bsm = None
  has_openpilot_longitudinal = None
  has_pedal = False
  has_sascm = bool(starpilot_toggles.get("has_sascm", False))
  has_radar = None
  has_sdsu = bool(starpilot_toggles.get("has_sdsu", False))
  has_sng = None
  has_zss = bool(starpilot_toggles.get("has_zss", False))
  can_use_pedal = None
  can_use_sdsu = None
  is_bolt = False

  cp_bytes = _safe_params_get_live_raw("CarParamsPersistent")
  if cp_bytes:
    try:
      with car.CarParams.from_bytes(cp_bytes) as cp:
        car_fingerprint = str(getattr(cp, "carFingerprint", "") or "")
        car_make = str(getattr(cp, "brand", "") or getattr(cp, "carName", "") or "")

        has_bsm = bool(getattr(cp, "enableBsm", False))
        has_openpilot_longitudinal = bool(getattr(cp, "openpilotLongitudinalControl", False))
        has_pedal = bool(getattr(cp, "enableGasInterceptorDEPRECATED", False))
        has_sascm = car_make == "gm" and bool(getattr(cp, "flags", 0) & GMFlags.SASCM.value)
        has_radar = not bool(getattr(cp, "radarUnavailable", False))
        has_sdsu = bool(starpilot_toggles.get("has_sdsu", has_sdsu))
        has_sng = bool(getattr(cp, "autoResumeSng", False))
        has_zss = bool(starpilot_toggles.get("has_zss", has_zss))
        is_bolt = car_fingerprint.startswith("CHEVROLET_BOLT")

        if PC and (car_make == "mock" or car_fingerprint == "MOCK"):
          fallback_make = str(starpilot_toggles.get("car_make", "") or "gm")
          fallback_model = str(starpilot_toggles.get("car_model", "") or "CHEVROLET_BOLT_ACC_2022_2023")
          has_pedal = bool(starpilot_toggles.get("has_pedal", True))
          has_sascm = bool(starpilot_toggles.get("has_sascm", has_sascm))
          has_sdsu = bool(starpilot_toggles.get("has_sdsu", has_sdsu))
          has_zss = bool(starpilot_toggles.get("has_zss", has_zss))
          is_bolt = fallback_model.startswith("CHEVROLET_BOLT")
          if fallback_make == "gm" and has_openpilot_longitudinal is None:
            has_openpilot_longitudinal = True
    except Exception:
      pass
  elif PC:
    fallback_model = str(starpilot_toggles.get("car_model", "") or "CHEVROLET_BOLT_ACC_2022_2023")
    has_pedal = bool(starpilot_toggles.get("has_pedal", True))
    has_sascm = bool(starpilot_toggles.get("has_sascm", has_sascm))
    has_sdsu = bool(starpilot_toggles.get("has_sdsu", has_sdsu))
    has_zss = bool(starpilot_toggles.get("has_zss", has_zss))
    is_bolt = fallback_model.startswith("CHEVROLET_BOLT")

  if can_use_pedal is None:
    can_use_pedal = has_pedal or is_bolt
  if can_use_sdsu is None:
    can_use_sdsu = has_sdsu

  fpcp_bytes = _safe_params_get_live_raw("StarPilotCarParamsPersistent")
  if fpcp_bytes:
    try:
      fpcp = messaging.log_from_bytes(fpcp_bytes, custom.StarPilotCarParams)
      fpcp_flags = int(getattr(fpcp, "flags", 0))
      has_sdsu = bool(fpcp_flags & ToyotaStarPilotFlags.SMART_DSU.value)
      has_zss = bool(fpcp_flags & ToyotaStarPilotFlags.ZSS.value)
      can_use_pedal = bool(getattr(fpcp, "canUsePedal", can_use_pedal))
      can_use_sdsu = bool(getattr(fpcp, "canUseSDSU", can_use_sdsu))
    except Exception:
      pass

  detected = []
  if has_pedal:
    detected.append("comma Pedal")
  if has_sascm:
    detected.append("SASCM")
  if has_sdsu:
    detected.append("SDSU")
  if has_zss:
    detected.append("ZSS")

  return [
    {"id": "hardware_detected", "label": "Hardware Detected", "value": ", ".join(detected) if detected else "None", "resettable": False},
    {"id": "pedal_detected", "label": "Pedal Detected", "value": _snapshot_bool_text(has_pedal), "resettable": False},
    {"id": "sascm_detected", "label": "SASCM Detected", "value": _snapshot_bool_text(has_sascm), "resettable": False},
    {"id": "sdsu_detected", "label": "SDSU Detected", "value": _snapshot_bool_text(has_sdsu), "resettable": False},
    {"id": "zss_detected", "label": "ZSS Detected", "value": _snapshot_bool_text(has_zss), "resettable": False},
    {"id": "blind_spot_support", "label": "Blind Spot Support", "value": _snapshot_bool_text(has_bsm), "resettable": False},
    {"id": "openpilot_longitudinal_support", "label": "openpilot Longitudinal Support", "value": _snapshot_bool_text(has_openpilot_longitudinal), "resettable": False},
    {"id": "pedal_support", "label": "comma Pedal Support", "value": _snapshot_bool_text(can_use_pedal), "resettable": False},
    {"id": "radar_support", "label": "Radar Support", "value": _snapshot_bool_text(has_radar), "resettable": False},
    {"id": "sdsu_support", "label": "SDSU Support", "value": _snapshot_bool_text(can_use_sdsu), "resettable": False},
    {"id": "sng_support", "label": "Stop-and-Go Support", "value": _snapshot_bool_text(has_sng), "resettable": False},
  ]

def _build_troubleshoot_section_payload(section_definition, value_types, default_values, layout_metadata, learned_values):
  section_keys = [str(key).strip() for key in section_definition.get("keys", []) if str(key).strip()]
  items = []

  for key in section_keys:
    param_metadata = layout_metadata.get(key, {}) if isinstance(layout_metadata.get(key, {}), dict) else {}
    value_type = value_types.get(key, str)
    data_type = str(param_metadata.get("data_type") or "").strip().lower()
    if data_type == "float":
      value_type = float
    elif data_type == "int":
      value_type = int
    elif data_type == "bool":
      value_type = bool

    label = str(param_metadata.get("label") or key)
    try:
      current_value = _resolve_troubleshoot_current_value(key, value_type, default_values)
      default_value = _resolve_troubleshoot_default_value(key, value_type, default_values)
      current_value = _normalize_troubleshoot_current_display_value(key, current_value, default_value)
    except Exception:
      current_value = "Unavailable"
      default_value = "n/a"

    items.append({
      "key": key,
      "label": label,
      "value": _sanitize_json_value(current_value),
      "defaultValue": _sanitize_json_value(default_value),
      "learnedValue": _sanitize_json_value(learned_values.get(key)),
    })

  return {
    "id": section_definition["id"],
    "title": section_definition["title"],
    "resettable": True,
    "hasLearnedColumn": any(item.get("learnedValue") not in (None, "") for item in items),
    "items": items,
  }

def _build_troubleshoot_payload():
  _, value_types = _get_param_type_info()
  default_values = _get_default_param_values()
  learned_values = _get_troubleshoot_learned_values()
  layout_metadata = _get_layout_param_metadata()

  longitudinal_personality_raw = _safe_params_get("LongitudinalPersonality", encoding="utf-8", default="") or ""
  snapshot_items = [
    {
      "id": "safety_param",
      "label": "Safety Param",
      "value": _get_safety_snapshot_text(),
      "resettable": False,
    },
    {
      "id": "fingerprint",
      "label": "Fingerprint",
      "value": _get_fingerprint_snapshot_text(),
      "resettable": False,
    },
    {
      "id": "lan_ip",
      "label": "LAN IP",
      "value": utilities.get_current_lan_ip() or "Unavailable",
      "resettable": False,
    },
    *_get_hardware_snapshot_items(),
    {
      "id": "driving_model",
      "label": "Current Driving Model",
      "value": str(_safe_params_get("Model", encoding="utf-8", default="") or "Unknown"),
      "resettable": False,
    },
    {
      "id": "selected_personality_profile",
      "label": "Selected Personality Profile",
      "value": _format_longitudinal_personality(longitudinal_personality_raw),
      "resettable": False,
    },
  ]

  sections = [
    _build_troubleshoot_section_payload(section_definition, value_types, default_values, layout_metadata, learned_values)
    for section_definition in _TROUBLESHOOT_SECTION_DEFINITIONS
  ]

  shown = {item['key'] for section in sections for item in section['items']}
  registered = {key for key, *_ in starpilot_default_params}
  for category in load_settings_catalog() or []:
    groups = {}
    for entry in category.get('params', []):
      key = entry.get('key')
      if key in shown or key not in registered or key.startswith('LaneCentering') or key == 'LaneCenterOffset':
        continue
      if (entry.get("requires_capability") == "HasRivianAngleHarness" and not _get_has_rivian_angle_harness()):
        continue
      if key == "TeslaWakeOnCAN" and not supports_tesla_can_wake(params):
        continue
      if _params_raw.get_key_flag(key) & ParamKeyFlag.DONT_LOG:
        continue
      parent = entry.get('parent_key')
      title = category['name']
      if parent:
        title += ' › ' + str(layout_metadata.get(parent, {}).get('label', parent))
      groups.setdefault(title, []).append(key)
      shown.add(key)
    for title, keys in groups.items():
      section = _build_troubleshoot_section_payload({'id': 'catalog_' + keys[0], 'title': title, 'keys': keys},
                                                    value_types, default_values, layout_metadata, learned_values)
      section['resettable'] = False
      sections.append(section)
  for title, keys in [
      ('Bluetooth Controllers', ['BluetoothEnabled', 'BluetoothDisconnectControllersOffroad', 'WheelControlsEnabled', 'ControllerActionSlots', 'WheelControlMappings']),
      ('Longitudinal (Speed & Following) › Longitudinal control mode', ['ExperimentalMode', 'ConditionalExperimental', 'ConditionalChill', 'LongitudinalPersonality']),
      ('Model Manager', ['Model', 'ActiveBigModel', 'ActiveSmallModel', 'ModelSortMode', 'UserFavorites'])]:
    keys = [key for key in keys if key in registered and key not in shown
            and not (_params_raw.get_key_flag(key) & ParamKeyFlag.DONT_LOG)]
    if keys:
      section = _build_troubleshoot_section_payload({'id': 'extra_' + keys[0], 'title': title, 'keys': keys},
                                                    value_types, default_values, layout_metadata, learned_values)
      section['resettable'] = False
      sections.append(section)
      shown.update(keys)
  sections.sort(key=lambda section: section["title"])

  return _sanitize_json_value({
    "vehicleStatus": _build_vehicle_fault_status(),
    "snapshot": snapshot_items,
    "sections": sections,
    "isOnroad": params.get_bool("IsOnroad"),
  })

def _reset_troubleshoot_section(section_id):
  section_definition = _TROUBLESHOOT_SECTION_BY_ID.get(str(section_id or "").strip())
  if section_definition is None:
    raise ValueError("Unknown troubleshoot section.")

  allowed_keys, _ = _get_param_type_info()
  default_values = _get_default_param_values()
  is_onroad = params.get_bool("IsOnroad")
  blocked_onroad_keys = {
    "Model", "AlwaysOnLateral", "ForceTorqueController", "NNFF", "NNFFLite",
  }
  personality_writes_locked = _personality_settings_write_locked()

  updated_keys = []
  skipped_keys = []

  for key in section_definition.get("keys", []):
    if key in _TROUBLESHOOT_NON_RESETTABLE_SECTION_KEYS:
      skipped_keys.append({"key": key, "reason": "preserved by design"})
      continue

    if key not in allowed_keys:
      skipped_keys.append({"key": key, "reason": "not editable"})
      continue

    if ((is_onroad and key in blocked_onroad_keys) or
        (personality_writes_locked and key in PERSONALITY_PARKED_PARAM_KEYS)):
      skipped_keys.append({"key": key, "reason": "blocked until required off-road state is confirmed"})
      continue

    if key not in default_values:
      skipped_keys.append({"key": key, "reason": "default unavailable"})
      continue

    params.put(key, _serialize_param_write_value(default_values[key]))
    updated_keys.append(key)

  if updated_keys:
    update_starpilot_toggles()

  return {
    "sectionId": section_definition["id"],
    "sectionTitle": section_definition["title"],
    "updatedKeys": updated_keys,
    "skippedKeys": skipped_keys,
    "updatedCount": len(updated_keys),
    "skippedCount": len(skipped_keys),
  }

def _extract_testing_ground_variant_labels(slot_data, include_default=True):
  labels = {}
  if not isinstance(slot_data, dict):
    slot_data = {}

  raw_variant_labels = slot_data.get("variantLabels")
  if isinstance(raw_variant_labels, dict):
    for raw_variant, raw_label in raw_variant_labels.items():
      variant = str(raw_variant or "").strip().upper()
      label = str(raw_label or "").strip()
      if len(variant) == 1 and variant.isalpha() and label:
        labels[variant] = label

  for key, value in slot_data.items():
    if not isinstance(key, str) or not key.endswith("Label"):
      continue
    variant = key[:-5].strip().upper()
    if len(variant) != 1 or not variant.isalpha():
      continue
    label = str(value or "").strip()
    if label:
      labels[variant] = label

  if include_default and _TESTING_GROUNDS_DEFAULT_VARIANT not in labels:
    labels[_TESTING_GROUNDS_DEFAULT_VARIANT] = _TESTING_GROUNDS_DEFAULT_VARIANT

  return dict(sorted(labels.items()))

def _get_testing_ground_variant_labels(slot_id, slot=None):
  normalized_slot_id = str(slot_id or "").strip()
  labels = {}

  shared_labels = _TESTING_GROUNDS_VARIANT_LABELS_BY_SLOT.get(normalized_slot_id, {})
  if shared_labels:
    labels.update({
      str(variant or "").strip().upper(): str(label or "").strip()
      for variant, label in shared_labels.items()
      if len(str(variant or "").strip().upper()) == 1 and str(variant or "").strip().upper().isalpha() and str(label or "").strip()
    })
  else:
    labels.update(_extract_testing_ground_variant_labels(slot if isinstance(slot, dict) else {}, include_default=False))

  if _TESTING_GROUNDS_DEFAULT_VARIANT not in labels:
    labels[_TESTING_GROUNDS_DEFAULT_VARIANT] = _TESTING_GROUNDS_DEFAULT_VARIANT

  return dict(sorted(labels.items()))

def _normalize_testing_ground_variant(slot_id, variant, slot=None):
  allowed_variants = set(_get_testing_ground_variant_labels(slot_id, slot).keys()) or set(_TESTING_GROUNDS_VARIANTS)
  normalized_variant = str(variant or "").strip().upper()
  return normalized_variant if normalized_variant in allowed_variants else _TESTING_GROUNDS_DEFAULT_VARIANT

def _set_testing_ground_variant_fields(slot, variant_labels):
  for key in list(slot.keys()):
    if not isinstance(key, str) or not key.endswith("Label"):
      continue
    variant = key[:-5].strip()
    if len(variant) == 1 and variant.isalpha():
      slot.pop(key, None)

  slot["variantLabels"] = variant_labels
  for variant, label in variant_labels.items():
    slot[f"{variant.lower()}Label"] = label

  return slot

def _get_first_selectable_testing_ground_slot_id(slots):
  for slot in slots:
    if _is_unused_testing_ground_slot(slot):
      continue

    slot_id = str(slot.get("id") or "").strip()
    if slot_id:
      return slot_id

  return "1"

def _build_testing_ground_fallback_slots():
  definitions_by_id = {}

  for definition in _TESTING_GROUNDS_SLOT_DEFINITIONS:
    if not isinstance(definition, dict):
      continue

    slot_id = str(definition.get("id") or "").strip()
    if not slot_id:
      continue

    variant_labels = _get_testing_ground_variant_labels(slot_id, definition)
    slot = {
      "id": slot_id,
      "name": str(definition.get("name") or "Unused").strip() or "Unused",
      "description": str(definition.get("description") or "").strip(),
    }
    definitions_by_id[slot_id] = _set_testing_ground_variant_fields(slot, variant_labels)

  slots = []
  for slot_number in range(1, _TESTING_GROUNDS_SLOT_COUNT + 1):
    slot_id = str(slot_number)
    fallback_slot = definitions_by_id.get(slot_id, {
      "id": slot_id,
      "name": "Unused",
      "description": "",
    })
    slot = dict(fallback_slot)
    slot_variant_labels = _get_testing_ground_variant_labels(slot_id, slot)
    slots.append(_set_testing_ground_variant_fields(slot, slot_variant_labels))

  return slots

def _default_testing_grounds_state():
  slots = _build_testing_ground_fallback_slots()
  return {
    "schemaVersion": _TESTING_GROUNDS_SCHEMA_VERSION,
    "activeSlot": _get_first_selectable_testing_ground_slot_id(slots),
    "activeVariant": _TESTING_GROUNDS_DEFAULT_VARIANT,
    "slots": slots,
  }

def _normalize_testing_ground_slot(raw_slot, fallback_slot):
  slot = dict(fallback_slot)
  if not isinstance(raw_slot, dict):
    return slot

  # Slot metadata (name/description) should always come from shared definitions.
  # Persisted state only owns active selection and per-slot variant labels.
  slot["name"] = str(slot.get("name") or "Unused").strip() or "Unused"
  slot["description"] = str(slot.get("description") or "").strip()

  variant_labels = _get_testing_ground_variant_labels(slot.get("id"), raw_slot)
  if not variant_labels:
    variant_labels = _get_testing_ground_variant_labels(slot.get("id"), slot)
  return _set_testing_ground_variant_fields(slot, variant_labels)

def _load_testing_grounds_state_unlocked():
  state = _default_testing_grounds_state()
  fallback_slots = state["slots"]
  fallback_slot_ids = {slot["id"] for slot in fallback_slots}
  needs_write = False

  raw_state = {}
  try:
    raw_state = json.loads(_TESTING_GROUNDS_STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw_state, dict):
      raw_state = {}
      needs_write = True
  except FileNotFoundError:
    needs_write = True
  except Exception:
    needs_write = True

  if raw_state.get("schemaVersion") != _TESTING_GROUNDS_SCHEMA_VERSION:
    needs_write = True

  raw_slots = raw_state.get("slots")
  if isinstance(raw_slots, list):
    raw_by_id = {}
    for index, raw_slot in enumerate(raw_slots, start=1):
      if not isinstance(raw_slot, dict):
        needs_write = True
        continue

      slot_id = str(raw_slot.get("id") or "").strip() or str(index)
      if slot_id not in fallback_slot_ids:
        needs_write = True
        continue

      raw_by_id[slot_id] = raw_slot

    normalized_slots = []
    for fallback_slot in fallback_slots:
      slot_id = fallback_slot["id"]
      normalized_slots.append(_normalize_testing_ground_slot(raw_by_id.get(slot_id), fallback_slot))
      if slot_id not in raw_by_id:
        needs_write = True

    state["slots"] = normalized_slots
  else:
    needs_write = True

  selectable_slot_ids = {
    str(slot.get("id") or "").strip()
    for slot in state["slots"]
    if not _is_unused_testing_ground_slot(slot)
  }
  default_slot_id = _get_first_selectable_testing_ground_slot_id(state["slots"])
  active_slot = str(raw_state.get("activeSlot") or "").strip()
  active_slot_migrated = active_slot not in fallback_slot_ids or active_slot not in selectable_slot_ids
  if active_slot_migrated:
    active_slot = default_slot_id
    needs_write = True
  state["activeSlot"] = active_slot

  active_slot_data = _find_testing_ground_slot(state, active_slot)
  raw_active_variant = str(raw_state.get("activeVariant") or "").strip().upper()
  if active_slot_migrated:
    active_variant = _TESTING_GROUNDS_DEFAULT_VARIANT
  else:
    active_variant = _normalize_testing_ground_variant(active_slot, raw_active_variant, active_slot_data)
  if raw_active_variant != active_variant:
    needs_write = True
  state["activeVariant"] = active_variant

  return state, needs_write

def _write_testing_grounds_state_unlocked(state):
  _TESTING_GROUNDS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
  tmp_path = _TESTING_GROUNDS_STATE_PATH.with_name(f".tmp_{_TESTING_GROUNDS_STATE_PATH.name}")
  tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
  os.replace(tmp_path, _TESTING_GROUNDS_STATE_PATH)

def _get_testing_grounds_state():
  with _TESTING_GROUNDS_LOCK:
    state, needs_write = _load_testing_grounds_state_unlocked()
    if needs_write:
      try:
        _write_testing_grounds_state_unlocked(state)
      except Exception:
        pass
    return state

def _is_unused_testing_ground_slot(slot):
  name = str(slot.get("name") or "").strip().lower()
  return name == "unused" or name.startswith("unused ")

def _find_testing_ground_slot(state, slot_id):
  for slot in state.get("slots", []):
    if str(slot.get("id") or "").strip() == slot_id:
      return slot
  return {}

def _serialize_testing_grounds_state(state):
  slots = state.get("slots", [])
  active_slot_id = str(state.get("activeSlot") or "").strip()
  active_slot = _find_testing_ground_slot(state, active_slot_id)
  active_variant = _normalize_testing_ground_variant(active_slot_id, state.get("activeVariant"), active_slot)
  active_variant_labels = _get_testing_ground_variant_labels(active_slot_id, active_slot)

  return {
    "schemaVersion": state.get("schemaVersion", _TESTING_GROUNDS_SCHEMA_VERSION),
    "activeSlot": active_slot_id,
    "activeVariant": active_variant,
    "activeVariantLabel": active_variant_labels.get(active_variant, active_variant),
    "activeSlotName": active_slot.get("name", active_slot_id),
    "slots": slots,
    "slotSummaryLines": [f"{slot.get('id', '?')}. {slot.get('name', 'Unused')}" for slot in slots],
    "selectableSlots": [slot for slot in slots if not _is_unused_testing_ground_slot(slot)],
  }

def _get_testing_ground_custom_reserved_pm():
  global _TESTING_GROUND_CUSTOM_RESERVED_PM

  with _TESTING_GROUND_CUSTOM_RESERVED_LOCK:
    if _TESTING_GROUND_CUSTOM_RESERVED_PM is None:
      _TESTING_GROUND_CUSTOM_RESERVED_PM = messaging.PubMaster([_TESTING_GROUND_CUSTOM_RESERVED_SERVICE])
    return _TESTING_GROUND_CUSTOM_RESERVED_PM

def _build_testing_ground_custom_reserved_payload(state, reason):
  serialized = _serialize_testing_grounds_state(state)
  return {
    "slotId": serialized["activeSlot"],
    "slotName": serialized["activeSlotName"],
    "variant": serialized["activeVariant"],
    "variantLabel": serialized["activeVariantLabel"],
    "reason": reason,
    "wallTimeNanos": time.time_ns(),
  }

def _publish_testing_ground_custom_reserved(state, reason):
  global _TESTING_GROUND_CUSTOM_RESERVED_LAST_PUBLISH_MONO

  payload = _build_testing_ground_custom_reserved_payload(state, reason)
  message_valid = str(payload.get("variant") or "").strip().upper() == "B"

  try:
    message = messaging.new_message(_TESTING_GROUND_CUSTOM_RESERVED_SERVICE, valid=message_valid)
    message.customReserved9.slotId = payload["slotId"]
    message.customReserved9.slotName = payload["slotName"]
    message.customReserved9.variant = payload["variant"]
    message.customReserved9.variantLabel = payload["variantLabel"]
    message.customReserved9.reason = payload["reason"]
    message.customReserved9.wallTimeNanos = payload["wallTimeNanos"]
    _get_testing_ground_custom_reserved_pm().send(_TESTING_GROUND_CUSTOM_RESERVED_SERVICE, message)
  except Exception:
    return

  with _TESTING_GROUND_CUSTOM_RESERVED_LOCK:
    _TESTING_GROUND_CUSTOM_RESERVED_LAST_PUBLISH_MONO = time.monotonic()

def _testing_ground_custom_reserved_worker():
  while True:
    with _TESTING_GROUND_CUSTOM_RESERVED_LOCK:
      last_publish_mono = _TESTING_GROUND_CUSTOM_RESERVED_LAST_PUBLISH_MONO

    sleep_s = _TESTING_GROUND_CUSTOM_RESERVED_INTERVAL_S - (time.monotonic() - last_publish_mono)
    if sleep_s > 0:
      time.sleep(min(sleep_s, 1.0))
      continue

    try:
      _publish_testing_ground_custom_reserved(_get_testing_grounds_state(), "heartbeat")
    except Exception:
      time.sleep(1.0)

def _set_testing_ground_selection(slot_id, variant):
  normalized_slot_id = str(slot_id or "").strip()
  requested_variant = str(variant or "").strip().upper()

  with _TESTING_GROUNDS_LOCK:
    state, _ = _load_testing_grounds_state_unlocked()
    previous_slot_id = str(state.get("activeSlot") or "").strip()
    previous_variant = _normalize_testing_ground_variant(previous_slot_id, state.get("activeVariant"), _find_testing_ground_slot(state, previous_slot_id))
    slot_ids = {slot["id"] for slot in state["slots"]}
    if normalized_slot_id not in slot_ids:
      raise ValueError(f"Unknown testing ground slot '{normalized_slot_id}'.")

    slot = _find_testing_ground_slot(state, normalized_slot_id)
    if _is_unused_testing_ground_slot(slot):
      raise ValueError(f"Testing ground slot '{normalized_slot_id}' is unavailable.")

    allowed_variant_labels = _get_testing_ground_variant_labels(normalized_slot_id, slot)
    if requested_variant not in allowed_variant_labels:
      allowed_variants = ", ".join(sorted(allowed_variant_labels.keys()))
      raise ValueError(f"Variant must be one of: {allowed_variants}.")

    normalized_variant = _normalize_testing_ground_variant(normalized_slot_id, requested_variant, slot)
    changed = normalized_slot_id != previous_slot_id or normalized_variant != previous_variant
    state["activeSlot"] = normalized_slot_id
    state["activeVariant"] = normalized_variant
    if changed:
      _write_testing_grounds_state_unlocked(state)
    return state, changed

def _default_longitudinal_maneuver_status():
  return {
    "state": "idle",
    "phase": "",
    "paddleMode": "auto",
    "maneuver": "",
    "runIndex": 0,
    "runTotal": 0,
    "stepIndex": 0,
    "stepTotal": 0,
    "phaseStepIndex": 0,
    "phaseStepTotal": 0,
    "uiShow": False,
    "uiSize": "small",
    "uiText1": "Long Maneuvers",
    "uiText2": "",
    "updatedAtSec": 0.0,
    "support": {},
    "caveats": [],
    "skippedManeuvers": [],
    "history": [],
  }

def _load_longitudinal_maneuver_status():
  status = _default_longitudinal_maneuver_status()
  raw = params.get("LongitudinalManeuverStatus", encoding="utf-8") or ""
  if raw:
    try:
      payload = json.loads(raw)
      if isinstance(payload, dict):
        status.update(payload)
    except Exception:
      pass

  history = status.get("history")
  if not isinstance(history, list):
    history = []
  status["history"] = [str(line) for line in history if str(line).strip()][-120:]

  try:
    status["updatedAtSec"] = float(status.get("updatedAtSec") or 0.0)
  except Exception:
    status["updatedAtSec"] = 0.0

  return status

def _save_longitudinal_maneuver_status(status):
  status_copy = dict(status)
  history = status_copy.get("history")
  if not isinstance(history, list):
    history = []
  status_copy["history"] = [str(line) for line in history if str(line).strip()][-120:]
  status_copy["updatedAtSec"] = float(status_copy.get("updatedAtSec") or time.monotonic())
  params.put("LongitudinalManeuverStatus", status_copy)
  return status_copy

def _append_longitudinal_maneuver_history(status, line):
  if not line:
    return status
  history = list(status.get("history", []))
  history.append(str(line))
  status["history"] = history[-120:]
  return status

def _get_longitudinal_maneuver_support_snapshot():
  cp_bytes = _safe_params_get_live_raw("CarParamsPersistent")
  if not cp_bytes:
    return None

  try:
    with car.CarParams.from_bytes(cp_bytes) as cp:
      return get_longitudinal_maneuver_support(cp).to_dict()
  except Exception:
    return None

def _serialize_longitudinal_maneuver_status(status):
  updated_at = _safe_float(status.get("updatedAtSec"), 0.0)
  age_seconds = max(0.0, time.monotonic() - updated_at) if updated_at > 0 else None
  mode_enabled = params.get_bool("LongitudinalManeuverMode")
  paddle_mode = params.get("LongitudinalManeuverPaddleMode", encoding="utf-8") or str(status.get("paddleMode") or "auto")
  support = _get_longitudinal_maneuver_support_snapshot() or status.get("support") or {}
  caveats = support.get("caveats", status.get("caveats") or [])
  skipped_maneuvers = support.get("skippedManeuvers", status.get("skippedManeuvers") or [])
  return {
    **status,
    "modeEnabled": mode_enabled,
    "paddleMode": paddle_mode,
    "support": support,
    "caveats": list(caveats),
    "skippedManeuvers": list(skipped_maneuvers),
    "isOnroad": params.get_bool("IsOnroad"),
    "isEngaged": params.get_bool("IsEngaged"),
    "updatedAgeSec": age_seconds,
  }

def _set_longitudinal_maneuver_mode(enabled):
  status = _load_longitudinal_maneuver_status()
  if enabled:
    params.put_bool("LongitudinalManeuverMode", True)
    params.put("LongitudinalManeuverPaddleMode", "auto")
    status.update({
      "state": "armed",
      "phase": "",
      "maneuver": "",
      "runIndex": 0,
      "runTotal": 0,
      "stepIndex": 0,
      "phaseStepIndex": 0,
      "uiShow": True,
      "uiSize": "small",
      "uiText1": "Long Maneuvers Armed",
      "uiText2": "Engage with SET to start the test suite.",
      "updatedAtSec": time.monotonic(),
    })
    _append_longitudinal_maneuver_history(status, "Armed from The Galaxy. Engage with SET to start.")
  else:
    params.put_bool("LongitudinalManeuverMode", False)
    params.put("LongitudinalManeuverPaddleMode", "auto")
    status.update({
      "state": "stopped",
      "uiShow": True,
      "uiSize": "small",
      "uiText1": "Long Maneuvers Stopped",
      "uiText2": "Test mode disabled.",
      "updatedAtSec": time.monotonic(),
    })
    _append_longitudinal_maneuver_history(status, "Stopped from The Galaxy.")

  return _save_longitudinal_maneuver_status(status)


def _default_lateral_maneuver_status():
  return {
    "state": "idle",
    "phase": "",
    "maneuver": "",
    "runIndex": 0,
    "runTotal": 0,
    "stepIndex": 0,
    "stepTotal": 0,
    "phaseStepIndex": 0,
    "phaseStepTotal": 0,
    "uiShow": False,
    "uiSize": "small",
    "uiText1": "Lateral Maneuvers",
    "uiText2": "",
    "updatedAtSec": 0.0,
    "history": [],
  }


def _load_lateral_maneuver_status():
  status = _default_lateral_maneuver_status()
  raw = params.get("LateralManeuverStatus", encoding="utf-8") or ""
  if raw:
    try:
      payload = json.loads(raw)
      if isinstance(payload, dict):
        status.update(payload)
    except Exception:
      pass

  history = status.get("history")
  if not isinstance(history, list):
    history = []
  status["history"] = [str(line) for line in history if str(line).strip()][-120:]

  try:
    status["updatedAtSec"] = float(status.get("updatedAtSec") or 0.0)
  except Exception:
    status["updatedAtSec"] = 0.0

  return status


def _save_lateral_maneuver_status(status):
  status_copy = dict(status)
  history = status_copy.get("history")
  if not isinstance(history, list):
    history = []
  status_copy["history"] = [str(line) for line in history if str(line).strip()][-120:]
  status_copy["updatedAtSec"] = float(status_copy.get("updatedAtSec") or time.monotonic())
  params.put("LateralManeuverStatus", status_copy)
  return status_copy


def _append_lateral_maneuver_history(status, line):
  if not line:
    return status
  history = list(status.get("history", []))
  history.append(str(line))
  status["history"] = history[-120:]
  return status


def _serialize_lateral_maneuver_status(status):
  updated_at = _safe_float(status.get("updatedAtSec"), 0.0)
  age_seconds = max(0.0, time.monotonic() - updated_at) if updated_at > 0 else None
  return {
    **status,
    "modeEnabled": params.get_bool("LateralManeuverMode"),
    "isOnroad": params.get_bool("IsOnroad"),
    "isEngaged": params.get_bool("IsEngaged"),
    "updatedAgeSec": age_seconds,
  }


def _set_lateral_maneuver_mode(enabled):
  status = _load_lateral_maneuver_status()
  if enabled:
    params.put_bool("LateralManeuverMode", True)
    params.put_bool("LongitudinalManeuverMode", False)
    status.update({
      "state": "armed",
      "phase": "",
      "maneuver": "",
      "runIndex": 0,
      "runTotal": 0,
      "stepIndex": 0,
      "stepTotal": 0,
      "phaseStepIndex": 0,
      "phaseStepTotal": 0,
      "uiShow": True,
      "uiSize": "small",
      "uiText1": "Lateral Maneuvers Armed",
      "uiText2": "Stabilize on a straight, flat road to start.",
      "updatedAtSec": time.monotonic(),
    })
    _append_lateral_maneuver_history(status, "Armed from The Galaxy. Stabilize on a straight, flat road to start.")
  else:
    params.put_bool("LateralManeuverMode", False)
    status.update({
      "state": "stopped",
      "uiShow": True,
      "uiSize": "small",
      "uiText1": "Lateral Maneuvers Stopped",
      "uiText2": "Test mode disabled.",
      "updatedAtSec": time.monotonic(),
    })
    _append_lateral_maneuver_history(status, "Stopped from The Galaxy.")

  return _save_lateral_maneuver_status(status)


_SLUG_PREFIX_RE = re.compile(r"^/([A-Za-z0-9]{16})(/.*)?$")


class GalaxySlugMiddleware:
  """WSGI middleware to normalize reverse-proxy requests prefixed with a 16-character tunnel slug."""

  def __init__(self, wsgi_app):
    self.wsgi_app = wsgi_app

  def __call__(self, environ, start_response):
    path_info = environ.get("PATH_INFO", "")
    match = _SLUG_PREFIX_RE.match(path_info)
    if match:
      environ["HTTP_X_GALAXY_SLUG"] = match.group(1)
      remainder = match.group(2)
      environ["PATH_INFO"] = remainder if remainder else "/"
    return self.wsgi_app(environ, start_response)


def setup(app):
  from openpilot.starpilot.assets.model_sizes import ModelSizes
  model_sizes = ModelSizes()
  if not isinstance(app.wsgi_app, GalaxySlugMiddleware):
    app.wsgi_app = GalaxySlugMiddleware(app.wsgi_app)

  model_status_debug = {
    "last_signature": None,
    "last_log_time": 0.0,
    "last_empty_catalog_log_time": 0.0,
  }

  @app.after_request
  def disable_device_settings_asset_cache(response):
    if request.path in {
      "/assets/components/router.js",
      "/assets/components/sentry_notifications.js",
      "/assets/js/utils.js",
      "/assets/components/settings.js",
      "/assets/components/home/home.js",
      "/assets/components/home/home.css",
      "/assets/mobile/js/params.js",
      "/assets/mobile/js/components/ScreenBrightnessControl.js",
      "/assets/components/tools/device_settings.js",
      "/assets/components/tools/device_settings.css",
      "/assets/components/tools/device_settings_layout.json",
      "/assets/components/tools/galaxy.js",
      "/assets/components/tools/galaxy.css",
      "/assets/components/tools/sentry.js",
      "/assets/components/tools/sentry.css",
      "/assets/components/tools/v_asm.js",
      "/assets/components/tools/v_asm.css",
      "/assets/components/tools/pip_sidecam.js",
      "/assets/components/tools/pip_sidecam.css",
      "/assets/components/tools/toggles.js",
      "/assets/components/tools/model_laboratory.js",
      "/assets/components/tools/model_laboratory.css",
      "/assets/components/tools/bluetooth.js",
      "/assets/components/tools/bluetooth.css",
      "/assets/components/tools/wheel_controls.js",
      "/assets/components/tools/wheel_controls.css",
    }:
      response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
      response.headers["Pragma"] = "no-cache"
      response.headers["Expires"] = "0"
    if request.path == "/api/bluetooth/status" or request.path.startswith("/api/bluetooth/"):
      response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
      response.headers["Pragma"] = "no-cache"
      response.headers["Expires"] = "0"
    return response

  @app.errorhandler(404)
  def not_found(_):
    is_api = (
      request.path == "/api"
      or request.path.startswith("/api/")
      or "/api/" in request.path
      or request.is_json
      or (request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html)
    )
    if is_api or request.method not in ("GET", "HEAD"):
      return jsonify({"error": "Not found"}), 404

    if request.path.startswith(("/assets/", "/screen_recordings/", "/thumbnails/", "/video/")):
      return "Not found", 404

    response = make_response(render_template("index.html"))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

  def _no_store_response(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

  def _serve_new_ui():
    ui_index_path = Path(app.static_folder) / "mobile" / "index.html"
    if not ui_index_path.is_file():
      return "Galaxy UI not found", 404
    return _no_store_response(make_response(send_file(str(ui_index_path))))

  @app.route("/", methods=["GET"])
  def index():
    if params.get_bool("GalaxyMobileDefault"):
      return _serve_new_ui()
    response = make_response(render_template("index.html"))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

  @app.route("/classic", methods=["GET"])
  @app.route("/classic/", methods=["GET"])
  def classic_index():
    return _no_store_response(make_response(render_template("index.html")))

  @app.route("/mobile", methods=["GET"])
  @app.route("/mobile/", methods=["GET"])
  def mobile_index():
    return _serve_new_ui()

  @app.route("/api/bluetooth/status", methods=["GET"])
  def bluetooth_status():
    try:
      status = BluetoothClient(timeout=10.0).status()
      return jsonify(BluetoothClient.serialize_status(status)), 200
    except Exception as error:
      return jsonify({
        "available": False,
        "enabled": params.get_bool("BluetoothEnabled"),
        "offroad": params.get_bool("IsOffroad"),
        "selected_audio": params.get("BluetoothAudioAddress", encoding="utf-8") or "",
        "devices": [],
        "error": str(error),
      }), 503

  @app.route("/api/bluetooth/<operation>", methods=["POST"])
  def bluetooth_operation(operation):
    commands = {
      "power": "set_power",
      "scan": "start_scan",
      "stop_scan": "stop_scan",
      "pair": "pair",
      "connect": "connect",
      "disconnect": "disconnect",
      "forget": "forget",
      "select_audio": "select_audio",
      "test_audio": "test_audio",
      "pairing_response": "pairing_response",
    }
    command = commands.get(operation)
    if command is None:
      return jsonify({"error": "Unknown Bluetooth operation."}), 404
    offroad_only = {"power", "scan", "stop_scan", "pair", "forget", "test_audio", "pairing_response"}
    if operation in offroad_only and not params.get_bool("IsOffroad"):
      return jsonify({"error": "Bluetooth settings can only be changed offroad."}), 409

    data = request.get_json(silent=True) or {}
    payload = {}
    if command == "set_power":
      payload["enabled"] = bool(data.get("enabled", False))
    elif command == "pairing_response":
      payload = {
        "prompt_id": str(data.get("prompt_id", "")),
        "accepted": bool(data.get("accepted", False)),
        "value": str(data.get("value", "")),
      }
    elif command not in {"start_scan", "stop_scan"}:
      payload["address"] = str(data.get("address", ""))
      if not payload["address"] and command != "select_audio":
        return jsonify({"error": "Bluetooth device address is required."}), 400
    try:
      client = BluetoothClient(timeout=10.0)
      if command == "set_power":
        client.set_power(payload["enabled"])
        result = {}
      else:
        result = client.call(command, **payload)
      return jsonify({"message": "Bluetooth operation started.", **result}), 200
    except Exception as error:
      return jsonify({"error": str(error)}), 503

  @app.route("/api/wheel-controls/status", methods=["GET"])
  def wheel_controls_status():
    status = wheel_control_status(params, params_memory)
    favorite_options = _get_available_favorite_slot_options()
    favorite_option_by_key = {option["key"]: option for option in favorite_options}
    controller_options = _get_available_controller_action_options()
    controller_option_by_key = {option["key"]: option for option in controller_options}
    slots = normalize_favorite_slots(
      params.get(FAVORITE_SLOTS_PARAM),
      params=params,
      eligible_keys=set(favorite_option_by_key),
    )
    for slot in slots:
      key = slot.get("key")
      if key in favorite_option_by_key:
        slot["label"] = favorite_option_by_key[key]["label"]
    controller_slots = load_controller_action_slots(params, set(controller_option_by_key))
    for slot in controller_slots:
      key = slot.get("key")
      if key in controller_option_by_key:
        slot["label"] = controller_option_by_key[key]["label"]
    status["slots"] = slots
    status["controller_slots"] = controller_slots
    status["controller_options"] = controller_options
    status["disconnect_controllers_offroad"] = params.get_bool("BluetoothDisconnectControllersOffroad")
    is_metric = params.get_bool("IsMetric")
    speed_minimum, speed_maximum = controller_speed_bounds(is_metric)
    status["speed_unit"] = "km/h" if is_metric else "mph"
    status["speed_minimum"] = speed_minimum
    status["speed_maximum"] = speed_maximum
    return jsonify(status), 200

  @app.route("/api/wheel-controls/<operation>", methods=["POST"])
  def wheel_controls_operation(operation):
    if operation not in {"action", "learn", "cancel", "delete", "clear", "test", "test-stop", "joystick", "offroad-disconnect"}:
      return jsonify({"error": "Unknown wheel control operation."}), 404
    if not params.get_bool("IsOffroad"):
      return jsonify({"error": "Wheel controls can only be configured offroad."}), 409

    data = request.get_json(silent=True) or {}
    try:
      if operation == "offroad-disconnect":
        params.put_bool("BluetoothDisconnectControllersOffroad", bool(data.get("enabled", False)))
        return jsonify({"message": "Offroad controller disconnect updated."}), 200
      if operation == "action":
        slot_index = int(data.get("slot", -1))
        key = str(data.get("key") or "").strip()
        options = _get_available_controller_action_options()
        option_by_key = {option["key"]: option for option in options}
        if not 0 <= slot_index < CONTROLLER_ACTION_SLOT_COUNT:
          return jsonify({"error": f"Controller action must be between 1 and {CONTROLLER_ACTION_SLOT_COUNT}."}), 400
        if key and key not in option_by_key:
          return jsonify({"error": "That controller action is not available."}), 400
        value = None
        if key == CONTROLLER_ACTION_SET_SPEED:
          try:
            value = float(data.get("value"))
          except (TypeError, ValueError):
            return jsonify({"error": "Enter a valid set speed."}), 400
          speed_minimum, speed_maximum = controller_speed_bounds(params.get_bool("IsMetric"))
          if not math.isfinite(value) or not speed_minimum <= value <= speed_maximum:
            unit = "km/h" if params.get_bool("IsMetric") else "mph"
            return jsonify({"error": f"Set speed must be between {speed_minimum} and {speed_maximum} {unit}."}), 400
        cancel_wheel_control_learning(params_memory, params)
        set_controller_action_slot(
          slot_index,
          key or None,
          str(option_by_key.get(key, {}).get("label") or ""),
          params,
          value=value,
          eligible_keys=set(option_by_key),
        )
        return jsonify({"message": f"Controller Action #{slot_index + 1} updated."}), 200
      if operation == "joystick":
        device_id = str(data.get("device_id") or "").strip()
        enabled = bool(data.get("enabled", False))
        if enabled:
          devices = wheel_control_status(params, params_memory).get("devices", [])
          device = next((item for item in devices if item.get("device_id") == device_id), None)
          if device is None:
            return jsonify({"error": "Controller is not connected."}), 404
          if not device.get("joystick_capable"):
            return jsonify({"error": "This device does not expose joystick axes."}), 400
        set_joystick_device(device_id, enabled, params)
        return jsonify({"message": "Joystick controller updated."}), 200
      if operation == "learn":
        stop_wheel_control_testing(params_memory)
        slot_index = int(data.get("slot", -1))
        favorite_options = _get_available_favorite_slot_options()
        favorite_eligible_keys = {option["key"] for option in favorite_options}
        controller_options = _get_available_controller_action_options()
        controller_eligible_keys = {option["key"] for option in controller_options}
        slots = normalize_favorite_slots(
          params.get(FAVORITE_SLOTS_PARAM),
          params=params,
          eligible_keys=favorite_eligible_keys,
        )
        if 0 <= slot_index < FAVORITE_SLOT_COUNT:
          target = slots[slot_index]
          target_name = f"Favorite #{slot_index + 1}"
        elif FAVORITE_SLOT_COUNT <= slot_index < FAVORITE_SLOT_COUNT + CONTROLLER_ACTION_SLOT_COUNT:
          controller_index = slot_index - FAVORITE_SLOT_COUNT
          target = load_controller_action_slots(params, controller_eligible_keys)[controller_index]
          target_name = f"Controller Action #{controller_index + 1}"
        else:
          return jsonify({"error": "Unknown controller mapping target."}), 400
        if not target.get("enabled") or not target.get("key"):
          return jsonify({"error": f"Configure {target_name} before learning a button."}), 400
        start_wheel_control_learning(slot_index, params_memory, params)
        return jsonify({"message": f"Press a button for {target_name}."}), 200
      if operation == "cancel":
        cancel_wheel_control_learning(params_memory, params)
        return jsonify({"message": "Button learning cancelled."}), 200
      if operation == "test":
        cancel_wheel_control_learning(params_memory, params)
        start_wheel_control_testing(params_memory, params)
        return jsonify({"message": "Button testing enabled."}), 200
      if operation == "test-stop":
        stop_wheel_control_testing(params_memory)
        return jsonify({"message": "Button testing disabled."}), 200
      if operation == "clear":
        clear_wheel_control_mappings(params)
        cancel_wheel_control_learning(params_memory, params)
        stop_wheel_control_testing(params_memory)
        return jsonify({"message": "Wheel control mappings cleared."}), 200

      identifier = str(data.get("id") or "").strip()
      if not identifier:
        return jsonify({"error": "Mapping id is required."}), 400
      if not delete_wheel_control_mapping(identifier, params):
        return jsonify({"error": "Wheel control mapping was not found."}), 404
      return jsonify({"message": "Wheel control mapping removed."}), 200
    except (TypeError, ValueError) as error:
      return jsonify({"error": str(error)}), 400
    except Exception as error:
      return jsonify({"error": str(error)}), 503

  @app.route("/assets/components/tools/device_settings_layout.json", methods=["GET"])
  def device_settings_layout_asset():
    if not SETTINGS_CATALOG_PATH.is_file():
      return "Settings catalog not found", 404
    return send_file(str(SETTINGS_CATALOG_PATH), mimetype="application/json")

  @app.route("/assets/mobile/manifest.json", methods=["GET"])
  def mobile_manifest():
    manifest_path = Path(app.static_folder) / "mobile" / "manifest.json"
    if not manifest_path.is_file():
      return jsonify({"error": "Galaxy manifest not found"}), 404

    try:
      manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
      return jsonify({"error": "Galaxy manifest is invalid"}), 500

    slug = _read_galaxy_text(_get_galaxy_dir() / "glxyslug")
    if re.fullmatch(r"[A-Za-z0-9]{16}", slug):
      manifest_data["start_url"] = f"https://galaxy.firestar.link/{slug}"
    else:
      manifest_data["start_url"] = "/mobile/"

    response = jsonify(manifest_data)
    response.mimetype = "application/manifest+json"
    return _no_store_response(response)

  @app.route("/manifest.json", methods=["GET"])
  @app.route("/assets/manifest.json", methods=["GET"])
  def manifest():
    manifest_path = Path(app.static_folder) / "manifest.json"
    if manifest_path.is_file():
      return send_file(str(manifest_path), mimetype="application/manifest+json")

    # Fallback so the browser doesn't keep logging noisy 404s.
    return jsonify({
      "name": "Galaxy",
      "short_name": "Galaxy",
      "display": "standalone",
      "start_url": "/",
      "background_color": "#000000",
      "theme_color": "#8b6cc5",
      "icons": [],
    }), 200

  @app.route("/api/car_features_check", methods=["GET"])
  def car_features_check():
    tool = request.args.get("tool")
    try:
      with car.CarParams.from_bytes(params.get("CarParamsPersistent")) as cp:
        if tool == "doors":
          car_brand = getattr(cp, "brand", getattr(cp, "carName", ""))
          return jsonify({"result": car_brand == "toyota"})
        elif tool == "tsk":
          return jsonify({"result": getattr(cp, "secOcRequired", False)})
    except Exception:
      pass
    return jsonify({"result": False})

  def _send_door_command(command, should_be_locked, success_message, action):
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Door controls are unavailable while driving."}), 409

    try:
      can_parser = CANParser("toyota_nodsu_pt_generated", [("DOOR_LOCKS", 3)], bus=0)
      can_sock = messaging.sub_sock("can", timeout=100)

      for _ in range(6):
        if params.get_bool("IsOnroad"):
          return jsonify({"error": "Door controls are unavailable while driving."}), 409
        try:
          with Panda(disable_checks=True) as panda:
            panda.set_safety_mode(car.CarParams.SafetyModel.toyota)
            panda.can_send(0x750, command, 0)
            panda.can_send(0x750, command, 1)
        except Exception as error:
          cloudlog.warning("Galaxy door %s attempt failed: %s", action, error)
          continue

        time.sleep(1)

        lock_status = get_lock_status(can_parser, can_sock)
        if (lock_status == 0) == should_be_locked:
          return {"message": success_message}, 200
    except Exception as error:
      cloudlog.exception("Galaxy door %s failed: %s", action, error)

    return jsonify({"error": f"Unable to confirm that the doors were {action}ed."}), 502

  @app.route("/api/doors/lock", methods=["POST"])
  def lock_doors():
    return _send_door_command(LOCK_CMD, True, "Doors locked!", "lock")

  @app.route("/api/doors/unlock", methods=["POST"])
  def unlock_doors():
    return _send_door_command(UNLOCK_CMD, False, "Doors unlocked!", "unlock")

  @app.route("/api/error_logs", methods=["GET"])
  def get_error_logs():
    if request.accept_mimetypes["text/html"]:
      return render_template("v2/error-logs.jinja", active="error_logs")

    if request.accept_mimetypes["application/json"]:
      files = utilities.list_file(ERROR_LOGS_PATH)
      filtered = [file for file in files if not file.startswith("error")]
      return filtered, 200

  @app.route("/api/error_logs/delete_all", methods=["DELETE"])
  def delete_all_error_logs():
    for f in os.listdir(ERROR_LOGS_PATH):
      delete_file(os.path.join(ERROR_LOGS_PATH, f))
    return {"message": "All error logs deleted!"}, 200

  @app.route("/api/error_logs/<filename>", methods=["DELETE"])
  def delete_error_log(filename):
    delete_file(os.path.join(ERROR_LOGS_PATH, filename))
    return {"message": "Error log deleted!"}

  @app.route("/api/error_logs/<filename>", methods=["GET"])
  def get_error_log(filename):
    with open(os.path.join(ERROR_LOGS_PATH, filename)) as file:
      return file.read(), 200, {"Content-Type": "text/plain; charset=utf-8"}

  @app.route("/api/navigation", methods=["DELETE"])
  def clear_navigation():
    params.remove("NavDestination")
    params_memory.remove("NavInstructionState")
    params_memory.remove("NavInstructionCollapsed")
    return {"message": "Destination cleared"}

  @app.route("/api/navigation", methods=["GET"])
  def navigation():
    last_position = _get_navigation_last_position() or {}

    return {
      "amap1Key": params.get("AMapKey1", encoding="utf8") or "",
      "amap2Key": params.get("AMapKey2", encoding="utf8") or "",
      "destination": params.get("NavDestination", encoding="utf8") or "",
      "isMetric": params.get_bool("IsMetric"),
      "language": params.get("LanguageSetting", encoding="utf8") or "",
      "lastPosition": {
        "latitude": str(last_position.get("latitude", "")),
        "longitude": str(last_position.get("longitude", ""))
      },
      "mapboxPublic": params.get("MapboxPublicKey", encoding="utf8") or "",
      "mapboxSecret": params.get("MapboxSecretKey", encoding="utf8") or "",
      "previousDestinations": params.get("ApiCache_NavDestinations", encoding="utf8") or "",
    }

  @app.route("/api/navigation", methods=["POST"])
  def set_navigation():
    if not routing_configured(params):
      return {
        "message": "A Mapbox secret key is required to calculate the on-device route and provide navigation turn desires. Add it in App Keys first."
      }, 400

    destination = normalize_destination_payload(request.json)
    if destination is None:
      return {"message": "Invalid destination payload"}, 400

    recent_destinations = update_recent_destinations(
      params.get("ApiCache_NavDestinations", encoding="utf8") or "",
      destination,
    )
    params.put("NavDestination", json.dumps(destination))
    params.put("ApiCache_NavDestinations", recent_destinations)
    return {"message": "Destination set"}

  @app.route("/api/navigation/favorite", methods=["DELETE"])
  def remove_favorite_destination():
    to_remove = request.json or {}

    existing = json.loads(params.get("FavoriteDestinations", encoding="utf8") or "[]")
    fid = to_remove.get("id")
    if fid:
      favorites = [f for f in existing if f.get("id") != fid]
    else:
      favorites = [
        f for f in existing
        if not (
          f.get("routeId") == to_remove.get("routeId") and
          f.get("latitude") == to_remove.get("latitude") and
          f.get("longitude") == to_remove.get("longitude") and
          f.get("name") == to_remove.get("name")
        )
      ]

    params.put("FavoriteDestinations", favorites)
    return jsonify(message="Destination removed from favorites!")

  @app.route("/api/navigation/favorite", methods=["GET"])
  def list_favorite_destinations():
    favorites = json.loads(params.get("FavoriteDestinations", encoding="utf8") or "[]")
    changed = False
    for f in favorites:
      if "id" not in f:
        raw = f"{f.get('longitude')},{f.get('latitude')}|{f.get('routeId') or ''}|{f.get('name') or ''}"
        f["id"] = hashlib.sha1(raw.encode()).hexdigest()
        changed = True
    if changed:
      params.put("FavoriteDestinations", favorites)
    return jsonify(favorites=favorites)

  @app.route("/api/navigation/favorite", methods=["POST"])
  def add_favorite_destination():
    new_fav = request.json or {}

    if "id" not in new_fav:
      raw = f"{new_fav.get('longitude')},{new_fav.get('latitude')}|{new_fav.get('routeId') or ''}|{new_fav.get('name') or ''}"
      new_fav["id"] = hashlib.sha1(raw.encode()).hexdigest()

    existing = json.loads(params.get("FavoriteDestinations", encoding="utf8") or "[]")
    if not any(f.get("id") == new_fav["id"] for f in existing):
      existing.append(new_fav)

    params.put("FavoriteDestinations", existing)
    return {"message": "Destination added to favorites!"}

  @app.route("/api/navigation/favorite/rename", methods=["POST"])
  def rename_favorite_destination():
    data = request.json or {}
    fid = data.get("id")
    route_id_to_rename = data.get("routeId")
    new_name = data.get("name")
    is_home = data.get("is_home")
    is_work = data.get("is_work")

    if not fid and not route_id_to_rename:
      return jsonify({"error": "Missing id or routeId"}), 400

    existing_favorites = json.loads(params.get("FavoriteDestinations", encoding="utf8") or "[]")

    if is_home:
      for favorite in existing_favorites:
        favorite.pop("is_home", None)
    if is_work:
      for favorite in existing_favorites:
        favorite.pop("is_work", None)

    found = False
    for favorite in existing_favorites:
      if (fid and favorite.get("id") == fid) or (not fid and favorite.get("routeId") == route_id_to_rename):
        if new_name:
          favorite["name"] = new_name

        if is_home is not None:
          if is_home:
            favorite["is_home"] = True
            favorite.pop("is_work", None)
          else:
            favorite.pop("is_home", None)

        if is_work is not None:
          if is_work:
            favorite["is_work"] = True
            favorite.pop("is_home", None)
          else:
            favorite.pop("is_work", None)

        found = True
        break

    if not found:
      return jsonify({"error": "Favorite not found"}), 404

    params.put("FavoriteDestinations", existing_favorites)
    return jsonify(message="Favorite updated successfully!")

  @app.route("/api/navigation_key", methods=["DELETE"])
  def delete_navigation_key():
    meta = KEYS.get(request.args.get("type"))
    params.remove(meta[2])
    return jsonify(message=f"{meta[3]} deleted successfully!")

  @app.route("/api/navigation_key", methods=["POST"])
  def set_navigation_keys():
    data = request.get_json() or {}

    saved = []
    for meta in KEYS.values():
      raw = (data.get(meta[0]) or "").strip()
      if not raw:
        continue

      full = raw if raw.startswith(meta[1]) else meta[1] + raw
      if len(full) < meta[4]:
        return jsonify(error=f"{meta[3]} is invalid or too short..."), 400

      params.put(meta[2], full)
      saved.append(meta[3])

    if not saved:
      return jsonify(error="Nothing to update..."), 400

    return jsonify(message=f"{', '.join(saved)} saved successfully!")

  @app.route("/api/fingerprints/makes", methods=["GET"])
  def get_fingerprint_makes():
    return jsonify(_get_fingerprint_catalog()["makes"]), 200

  @app.route("/api/fingerprints/models", methods=["GET"])
  def get_fingerprint_models():
    catalog = _get_fingerprint_catalog()
    make_key = _normalize_fingerprint_make_key(
      request.args.get("make") or params.get("CarMake", encoding="utf-8") or ""
    )

    models = catalog["models_by_make"].get(make_key) if make_key else catalog["all_models"]
    if not models:
      models = catalog["all_models"]

    return jsonify(models), 200

  @app.route("/api/favorites/slots", methods=["GET", "PUT"])
  def favorite_slots():
    options = _get_available_favorite_slot_options()
    option_by_key = {option["key"]: option for option in options}
    eligible_keys = set(option_by_key)

    if request.method == "PUT":
      data = request.get_json() or {}
      raw_slots = data.get("slots", data) if isinstance(data, dict) else data
      if isinstance(raw_slots, dict):
        raw_slots = raw_slots.get("slots", [])
      if not isinstance(raw_slots, list):
        return jsonify(error="Favorite slots payload must be a list."), 400

      for idx, raw_slot in enumerate(raw_slots[:3]):
        if not isinstance(raw_slot, dict):
          continue
        key = str(raw_slot.get("key") or "").strip()
        if key == CONTROLLER_ACTION_SET_SPEED:
          from openpilot.starpilot.common.controller_actions import controller_speed_bounds
          minimum, maximum = controller_speed_bounds(params.get_bool("IsMetric"))
          value = raw_slot.get("value")
          if type(value) not in (int, float) or not minimum <= value <= maximum:
            return jsonify(error=f"Favorite #{idx + 1} speed must be between {minimum} and {maximum}."), 400
        if key and key not in eligible_keys:
          return jsonify(error=f"Favorite #{idx + 1} must use a Galaxy-exposed toggle or action."), 400

      slots = normalize_favorite_slots(raw_slots, params=params, eligible_keys=eligible_keys)

      for slot in slots:
        key = slot.get("key")
        if not key:
          continue
        slot["label"] = option_by_key[key]["label"]

      params.put(FAVORITE_SLOTS_PARAM, slots)
      update_starpilot_toggles()
      return jsonify({
        "message": "Favorite slots saved.",
        "slots": slots,
        "options": options,
        "values": _favorite_slot_values(options),
        "is_metric": params.get_bool("IsMetric"),
      }), 200

    slots = normalize_favorite_slots(params.get(FAVORITE_SLOTS_PARAM), params=params, eligible_keys=eligible_keys)
    for slot in slots:
      key = slot.get("key")
      if key in option_by_key:
        slot["label"] = option_by_key[key]["label"]

    return jsonify({
      "slots": slots,
      "options": options,
      "values": _favorite_slot_values(options),
        "is_metric": params.get_bool("IsMetric"),
    }), 200

  @app.route("/api/favorites/values", methods=["GET"])
  def favorite_values():
    options = _get_available_favorite_slot_options()
    eligible_keys = {option["key"] for option in options}
    slots = normalize_favorite_slots(params.get(FAVORITE_SLOTS_PARAM), params=params, eligible_keys=eligible_keys)
    return jsonify({"values": _configured_favorite_slot_values(slots)}), 200

  @app.route("/api/favorites/action", methods=["POST"])
  def favorite_action():
    data = request.get_json() or {}
    key = str(data.get("key") or "").strip()
    if not is_favorite_action_key(key):
      return jsonify({"error": "Unknown favorite action."}), 400
    if not trigger_favorite_action(key, params_memory, params=params, value=data.get("value")):
      return jsonify({"error": "Favorite action failed."}), 400
    return jsonify({"message": "Favorite action sent."}), 200

  @app.route("/api/personality_profiles/migrate", methods=["POST"])
  @_serialize_personality_profile_writes
  def migrate_personality_profiles():
    if _personality_settings_write_locked():
      return jsonify({"error": "Longitudinal personality profiles can only be migrated while off-road."}), 403

    raw_profiles = _safe_params_get_live_raw(PERSONALITY_PROFILES_PARAM)
    if raw_profiles is None:
      return jsonify({"error": "No stored longitudinal personality profiles require migration."}), 404
    if strict_profile_document(raw_profiles) is not None:
      return jsonify({"message": "Longitudinal personality profiles are already current.", "migration_required": False}), 200

    migrated_document = migrate_profile_document(raw_profiles)
    if migrated_document is None or strict_profile_document(migrated_document) is None:
      return jsonify({"error": "Stored longitudinal personality profiles are malformed and were not overwritten."}), 409

    params.put(PERSONALITY_PROFILES_PARAM, migrated_document)
    installed_document = strict_profile_document(_safe_params_get_live_raw(PERSONALITY_PROFILES_PARAM))
    if installed_document != migrated_document:
      return jsonify({"error": "Migrated longitudinal personality profiles did not verify after installation."}), 500
    update_starpilot_toggles()
    return jsonify({
      "message": "Longitudinal personality profiles migrated successfully.",
      "migration_required": False,
      "schema_version": PROFILE_SCHEMA_VERSION,
    }), 200

  @app.route("/api/personality_profiles", methods=["GET", "PUT"])
  @_serialize_personality_profile_writes
  def personality_profiles():
    ev_tuning = _get_detected_ev_tuning()
    truck_tuning = (_get_detected_truck_tuning() or params.get_bool("TruckTuning")) and not ev_tuning
    raw_profiles = _safe_params_get_live_raw(PERSONALITY_PROFILES_PARAM)
    current_document = strict_profile_document(raw_profiles)
    stored_document = migrate_profile_document(raw_profiles)
    configured = stored_document is not None
    migration_required = configured and current_document is None
    enabled = params.get_bool("CustomPersonalities")
    if not is_unconfigured_profile_document(raw_profiles) and stored_document is None:
      return jsonify({"error": "Stored longitudinal personality profiles are malformed and were not overwritten."}), 409
    profiles = stored_document["profiles"] if configured else default_personality_profiles(ev_tuning, truck_tuning)

    if request.method == "PUT":
      if _personality_editor_write_locked():
        return jsonify({"error": "Driving state is unavailable or inconsistent. Refresh before editing personalities."}), 403
      if current_document is None and stored_document is not None:
        return jsonify({"error": "Stored longitudinal personality profiles require a verified migration before editing."}), 409
      data = request.get_json(silent=True)
      required_fields = {"profile", "category", "preset", "curve"}
      if not isinstance(data, dict) or not required_fields <= set(data) or set(data) - required_fields - {"expected", "reset"}:
        return jsonify({"error": "Expected profile, category, preset, curve, and optional expected category or reset."}), 400
      reset = data.get("reset", False)
      if type(reset) is not bool or (reset and (data["preset"] != "custom" or data["curve"] != [])):
        return jsonify({"error": "Reset requires Custom and an empty curve; defaults are resolved by the server."}), 400

      try:
        current_config = profiles[data["profile"]][data["category"]]
        if "expected" in data and (data["expected"] != current_config or any(
          isinstance(value, bool) for key in ("curve", "legacyCurve") for value in data["expected"].get(key, [])
        )):
          return jsonify({"error": "Saved profile changed. Reload and review it before editing again."}), 409
        curve = data["curve"]
        initialize_default = data["preset"] == "custom" and current_config["preset"] == "dom_default" and not current_config["curve"]
        if reset:
          curve = _get_dom_personality_reference_curves(ev_tuning)[data["profile"]][data["category"]]
        elif data["preset"] == "custom" and current_config.get("preset") != "custom":
          if curve != []:
            update_personality_profile(
              profiles, data["profile"], data["category"], "custom", curve, ev_tuning, truck_tuning
            )
          legacy_curve = None
          if current_config.get("preset") == "dom_default" and not current_config.get("curve"):
            legacy_curve = _get_dom_personality_reference_curves(ev_tuning)[data["profile"]][data["category"]]
          curve = initial_custom_curve(
            data["category"], current_config, ev_tuning, truck_tuning, legacy_curve=legacy_curve
          )
        elif data["preset"] != "custom":
          curve = []
        profiles = update_personality_profile(
          profiles,
          data["profile"],
          data["category"],
          data["preset"],
          curve,
          ev_tuning,
          truck_tuning,
          reset=reset or initialize_default,
        )
      except (KeyError, TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400

      if _personality_editor_write_locked():
        return jsonify({"error": "Driving state is unavailable or inconsistent. Refresh before editing personalities."}), 403
      params.put(PERSONALITY_PROFILES_PARAM, profile_document(profiles, enabled=enabled))
      configured = True
      migration_required = False
      update_starpilot_toggles()

    return jsonify({
      "bounds": {key: list(value) for key, value in CURVE_BOUNDS.items()},
      "configured": configured,
      "default_profiles": default_personality_profiles(ev_tuning, truck_tuning),
      "enabled": enabled,
      "migration_required": migration_required,
      "options": {
        "acceleration": list(ACCELERATION_PRESETS),
        "braking": list(BRAKING_PRESETS),
        "following": list(FOLLOWING_PRESETS),
      },
      "profiles": profiles,
      "reference_curves": _get_dom_personality_reference_curves(ev_tuning),
      "schema_version": PROFILE_SCHEMA_VERSION,
      "speed_breakpoints_mph": {
        "acceleration": list(ACCELERATION_SPEEDS_MPH),
        "braking": list(BRAKING_SPEEDS_MPH),
        "following": list(FOLLOWING_SPEEDS_MPH),
      },
    }), 200
  @app.route("/api/longitudinal_mode", methods=["GET", "PUT"])
  def longitudinal_mode():
    with LONGITUDINAL_MODE_LOCK:
      try:
        if request.method == "GET":
          return jsonify(longitudinal_mode_snapshot(params, _get_longitudinal_mode_capable())), 200
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
          return jsonify({"error": "Expected a JSON object."}), 400
        try:
          result = set_longitudinal_mode(params, data.get("mode"), data.get("expected"), _get_longitudinal_mode_capable, data.get("acknowledged") is True)
        finally:
          update_starpilot_toggles()
        return jsonify(result), 200
      except ModeError as error:
        return jsonify({"error": str(error)}), error.status
      except Exception:
        return jsonify({"error": "Longitudinal mode state is unavailable. Refresh before retrying."}), 503

  @app.route("/api/params", methods=["GET", "PUT"])
  def get_param():
    if request.method == "PUT":
      data = request.get_json()
      if not data or "key" not in data or "value" not in data:
        return jsonify({"error": "Missing 'key' or 'value' in request body."}), 400

      key = str(data["key"]).strip()
      if key.startswith(("ScreenBrightness", "StandbyWake")):
        from openpilot.common.params import UnknownKeyName
        from openpilot.starpilot.common.screen_settings import write_screen_setting
        try:
          updated = write_screen_setting(params, key, data["value"])
        except ValueError as error:
          return jsonify({"error": str(error)}), 400
        except (OSError, KeyError, UnknownKeyName):
          return jsonify({"error": "Screen setting could not be saved."}), 503
        update_starpilot_toggles()
        return jsonify({"updated": updated, "message": "Screen setting saved."}), 200

      if key.lower() == PERSONALITY_PROFILES_PARAM.lower():
        return jsonify({"error": "Longitudinal personality profiles must be changed with the Driving Personalities editor."}), 403
      if key in PERSONALITY_PARKED_PARAM_KEYS and _personality_editor_write_locked():
        return jsonify({"error": "Driving state is unavailable or inconsistent. Refresh before editing personalities."}), 403
      if key in PERSONALITY_PROFILE_ENABLE_PARAM_KEYS and type(data["value"]) is not bool:
        return jsonify({"error": f"{key} must be a JSON boolean."}), 400
      if key in LONGITUDINAL_MODE_KEYS:
        if type(data["value"]) is not bool:
          return jsonify({"error": "Mode settings require a JSON boolean."}), 400
        with LONGITUDINAL_MODE_LOCK:
          try:
            before = longitudinal_mode_snapshot(params, _get_longitudinal_mode_capable())
            candidate = {**before["values"], key: data["value"]}
            if data["value"] and key in {"ConditionalExperimental", "ConditionalChill"}:
              candidate["ConditionalChill" if key == "ConditionalExperimental" else "ConditionalExperimental"] = False
            target = ("conditional_experimental" if candidate["ConditionalExperimental"] else
                      "conditional_chill" if candidate["ConditionalChill"] else
                      "experimental" if candidate["ExperimentalMode"] else "chill")
            try:
              result = set_longitudinal_mode(params, target, before["values"], _get_longitudinal_mode_capable, data.get("acknowledged") is True)
            finally:
              update_starpilot_toggles()
            return jsonify({"updated": result["values"], "message": "Longitudinal control mode updated."}), 200
          except ModeError as error:
            return jsonify({"error": str(error)}), error.status
          except Exception:
            return jsonify({"error": "Longitudinal mode state is unavailable."}), 503
      if key.lower() == FAVORITE_SLOTS_PARAM.lower():
        key = FAVORITE_SLOTS_PARAM
        raw_slots = data["value"]
        if isinstance(raw_slots, dict):
          raw_slots = raw_slots.get("slots", raw_slots)
        if not isinstance(raw_slots, list):
          return jsonify({"error": "Favorite slots must be configured with the Favorites editor."}), 400

        options = _get_available_favorite_slot_options()
        option_by_key = {option["key"]: option for option in options}
        eligible_keys = set(option_by_key)
        slots = normalize_favorite_slots(raw_slots, params=params, eligible_keys=eligible_keys)
        for slot in slots:
          slot_key = slot.get("key")
          if slot_key in option_by_key:
            slot["label"] = option_by_key[slot_key]["label"]

        params.put(FAVORITE_SLOTS_PARAM, slots)
        update_starpilot_toggles()
        return jsonify({
          "message": "Favorite slots saved.",
          "updated": {FAVORITE_SLOTS_PARAM: slots},
        }), 200

      key = {
        "model": "Model",
        "modelversion": "ModelVersion",
        "drivingmodel": "DrivingModel",
        "drivingmodelversion": "DrivingModelVersion",
      }.get(key.lower(), key)
      if key in MODEL_SMOOTHING_KEYS:
        if not params.get_bool("DeveloperUI"):
          return jsonify({"error": "Model smoothing is available only with Developer UI enabled."}), 403
        try:
          numeric = float(data["value"])
        except (TypeError, ValueError):
          return jsonify({"error": f"{key} must be numeric."}), 400
        if not math.isfinite(numeric) or numeric < 0.005 or numeric > 2.0:
          return jsonify({"error": f"{key} must be between 0.005 and 2.0 seconds."}), 400
        data["value"] = round(numeric / 0.005) * 0.005
      if key in PERSONALITY_ADVANCED_PARAM_KEYS:
        try:
          data["value"] = validate_personality_advanced_value(data["value"])
        except ValueError as error:
          return jsonify({"error": str(error)}), 400
      elif key in PERSONALITY_FOLLOW_PARAM_KEYS:
        try:
          data["value"] = validate_personality_follow_value(data["value"])
        except ValueError as error:
          return jsonify({"error": str(error)}), 400
      val = data["value"]
      selected_label_input = str(data.get("label") or "").strip()

      # Python json parses true/false as boolean
      if isinstance(val, bool):
        str_val = "1" if val else "0"
      else:
        str_val = str(val)

      allowed_keys, _ = _get_param_type_info()
      if key not in allowed_keys:
        return jsonify({"error": f"Parameter '{key}' is not editable."}), 403

      if key == "CustomPersonalities":
        if type(data["value"]) is not bool:
          return jsonify({"error": "CustomPersonalities must be a JSON boolean."}), 400
        enabled = data["value"]
        with _PERSONALITY_PROFILES_WRITE_LOCK:
          if _personality_editor_write_locked():
            return jsonify({"error": "Driving state is unavailable or inconsistent. Refresh before editing personalities."}), 403
          ev_tuning = _get_detected_ev_tuning()
          truck_tuning = (_get_detected_truck_tuning() or params.get_bool("TruckTuning")) and not ev_tuning
          raw_document = _safe_params_get_live_raw(PERSONALITY_PROFILES_PARAM)
          if not is_unconfigured_profile_document(raw_document) and strict_profile_document(raw_document) is None:
            return jsonify({"error": "Stored longitudinal personality profiles require a verified migration before changing the master control."}), 409
          document = synchronise_profile_document_enabled(
            raw_document, enabled, ev_tuning, truck_tuning,
          )
          if _personality_editor_write_locked():
            return jsonify({"error": "Driving state is unavailable or inconsistent. Refresh before editing personalities."}), 403
          updated = {"CustomPersonalities": enabled}
          if enabled:
            if document is None:
              return jsonify({"error": "Longitudinal personality profiles could not be prepared for enabling."}), 500
            params.put(PERSONALITY_PROFILES_PARAM, document)
            if strict_profile_document(_safe_params_get_live_raw(PERSONALITY_PROFILES_PARAM)) != document:
              return jsonify({"error": "Longitudinal personality profiles could not be verified after writing."}), 500
            updated[PERSONALITY_PROFILES_PARAM] = document
            params.put_bool("CustomPersonalities", True)
          else:
            params.put_bool("CustomPersonalities", False)
            if document is not None:
              params.put(PERSONALITY_PROFILES_PARAM, document)
              updated[PERSONALITY_PROFILES_PARAM] = document
        update_starpilot_toggles()
        return jsonify({
          "message": "Driving personalities updated.",
          "updated": updated,
        }), 200

      if key == "PulseGlideSpeedDelta" or (key in PULSE_GLIDE_BUTTON_KEYS and str_val.strip() == str(BUTTON_FUNCTIONS["PULSE_AND_GLIDE"])):
        if not params.get_bool("GalaxyDeveloperMode"):
          return jsonify({"error": "Pulse and Glide is available only with Galaxy Developer Mode enabled."}), 403

      if key in GALAXY_DEVELOPER_ONLY_KEYS and not params.get_bool("GalaxyDeveloperMode"):
        return jsonify({"error": f"{key} is available only with Galaxy Developer Mode enabled."}), 403

      if key in SENTRY_NUMERIC_PARAM_BOUNDS:
        minimum, maximum = SENTRY_NUMERIC_PARAM_BOUNDS[key]
        try:
          numeric = float(data["value"])
        except (TypeError, ValueError):
          return jsonify({"error": f"{key} must be numeric."}), 400
        if not math.isfinite(numeric) or numeric < minimum or numeric > maximum:
          return jsonify({"error": f"{key} must be between {minimum} and {maximum}."}), 400
        str_val = str(numeric)

      if key in CUSTOM_ACCEL_PROFILE_CURVE_PARAM_KEYS:
        try:
          numeric = float(data["value"])
          if key == CUSTOM_ACCEL_PROFILE_POINT_COUNT_KEY:
            if not math.isfinite(numeric) or not numeric.is_integer():
              raise ValueError("Breakpoint count must be a whole number")
            numeric = int(numeric)
          elif not math.isfinite(numeric):
            raise ValueError(f"{key} must be numeric")

          defaults_lookup = _get_default_param_values()
          initialized = _get_custom_accel_profile_breakpoints_initialized()
          candidate = {}
          for curve_key in CUSTOM_ACCEL_PROFILE_CURVE_PARAM_KEYS:
            if initialized:
              candidate[curve_key] = _safe_params_get(curve_key, encoding="utf-8")
            else:
              candidate[curve_key] = _get_legacy_compatible_curve_value(curve_key, defaults_lookup)
          candidate[key] = numeric

          parse_custom_accel_profile_curve(
            candidate[CUSTOM_ACCEL_PROFILE_POINT_COUNT_KEY],
            [candidate[curve_key] for curve_key in CUSTOM_ACCEL_PROFILE_BREAKPOINT_PARAM_KEYS],
            [candidate[curve_key] for curve_key in CUSTOM_ACCEL_PROFILE_POINT_VALUE_PARAM_KEYS],
          )
        except (TypeError, ValueError) as exc:
          return jsonify({"error": str(exc)}), 400

        updated = _seed_custom_accel_profile_curve(defaults_lookup) if not initialized else {}
        params.put(key, _serialize_param_write_value(numeric))
        params.put_bool(CUSTOM_ACCEL_PROFILE_BREAKPOINTS_INITIALIZED_KEY, True)
        updated.update({key: numeric, CUSTOM_ACCEL_PROFILE_BREAKPOINTS_INITIALIZED_KEY: True})
        update_starpilot_toggles()
        return jsonify({
          "message": "Custom acceleration curve updated.",
          "updated": updated,
        }), 200

      if key == "AlphaLongitudinalEnabled":
        if not _get_alpha_longitudinal_available():
          return jsonify({"error": "Alpha Longitudinal is not available for the detected vehicle."}), 403
        if params.get_bool("IsOnroad"):
          return jsonify({"error": "Cannot change Alpha Longitudinal while driving."}), 403

        enabled = str_val.strip() in ("1", "true", "True")
        params.put_bool(key, enabled)
        params.put_bool("OnroadCycleRequested", True)
        update_starpilot_toggles()
        return jsonify({
          "message": f"Parameter '{key}' updated successfully. The driving stack will restart shortly.",
          "updated": {key: enabled},
        }), 200

      if key == "ForceOffroad":
        enabled = str_val.strip() in ("1", "true", "True")
        if enabled and not _get_vehicle_parked():
          return jsonify({"error": "Force Offroad is only available while the vehicle is in Park."}), 403

        params.put_bool("ForceOffroad", enabled)
        params.put_bool("ForceOnroad", False)
        update_starpilot_toggles()
        return jsonify({
          "message": f"Force Offroad {'enabled' if enabled else 'disabled'}.",
          "updated": {"ForceOffroad": enabled, "ForceOnroad": False},
        }), 200

      # 1. Prevent changing the model or reboot-required toggles while the car is actively driving
      reboot_keys = {"Model", "DrivingModel", "AlwaysOnLateral", "DisableOpenpilotLongitudinal", "ForceTorqueController", "NNFF", "NNFFLite"}
      if key in reboot_keys and params.get_bool("IsOnroad"):
        friendly_names = {
          "Model": "Driving Model",
          "DrivingModel": "Driving Model",
          "AlwaysOnLateral": "Always On Lateral",
          "DisableOpenpilotLongitudinal": "Disable openpilot Longitudinal",
          "ForceTorqueController": "Force Torque Controller",
          "NNFF": "NNFF",
          "NNFFLite": "NNFF-Lite"
        }
        name = friendly_names.get(key, key)
        return jsonify({"error": f"Cannot change {name} while the car is driving. A reboot is required."}), 403

      if key == "AutomaticUpdates" and params.get_bool("IsOnroad"):
        return jsonify({"error": "Cannot change Automatic Updates while driving."}), 403

      if key in VASM_CONFIGURATION_KEYS and params.get_bool("IsOnroad"):
        return jsonify({"error": "Cannot change V-ASM configuration while driving."}), 403

      if key in PIP_PREVIEW_CONFIGURATION_KEYS:
        if not params.get_bool("GalaxyDeveloperMode"):
          return jsonify({"error": "PiP Side Camera is available only with Galaxy Developer Mode enabled."}), 403
        if params.get_bool("IsOnroad"):
          return jsonify({"error": "Cannot change PiP Side Camera configuration while driving."}), 403

      if key == "TeslaWakeOnCAN" and not supports_tesla_can_wake(params):
        return jsonify({"error": "Wake on CAN is available only for a detected Tesla Model 3, Y or X."}), 403

      if key in PANDA_FIRMWARE_TOGGLE_KEYS and params.get_bool("IsOnroad"):
        return jsonify({"error": "Cannot flash Panda firmware while driving."}), 403
      if key in PANDA_FIRMWARE_TOGGLE_KEYS and data.get(PANDA_FIRMWARE_CONFIRMATION_FIELD) is not True:
        return jsonify({"error": "Panda firmware changes require confirmation before flashing."}), 409

      enabled = str_val.strip() in ("1", "true", "True")
      if key in PANDA_FIRMWARE_TOGGLE_KEYS and firmware_flags_conflict(params, key, enabled):
        return jsonify({"error": "Tesla wake cannot be combined with remote-start firmware."}), 409

      if key == "TeslaWakeOnCAN":
        try:
          validate_tesla_can_wake_firmware(params, enabled)
        except RuntimeError as exc:
          return jsonify({"error": str(exc)}), 409

      if key in PERSONALITY_PARKED_PARAM_KEYS and _personality_editor_write_locked():
        return jsonify({"error": "Driving state is unavailable or inconsistent. Refresh before editing personalities."}), 403

      if key in {"LeadIndicator", "HideLeadMarker"}:
        enabled = str_val.strip() in ("1", "true", "True")
        if key == "LeadIndicator":
          params.put_bool("LeadIndicator", enabled)
          updated = {"LeadIndicator": enabled, "HideLeadMarker": not enabled}
        else:
          params.put_bool("HideLeadMarker", enabled)
          updated = {"HideLeadMarker": enabled, "LeadIndicator": not enabled}

        update_starpilot_toggles()
        return jsonify({
          "message": f"Parameter '{key}' updated successfully.",
          "updated": updated,
        }), 200

      if key == "AllowImpossibleAcceleration":
        enabled = str_val.strip() in ("1", "true", "True")
        params.put_bool(key, enabled)
        if enabled and _offroad_excessive_actuation_type() == "longitudinal":
          params.remove("Offroad_ExcessiveActuation")

        update_starpilot_toggles()
        return jsonify({
          "message": f"Parameter '{key}' updated successfully.",
          "updated": {key: enabled},
        }), 200

      if key in {"EVTuning", "TruckTuning"}:
        enabled = str_val.strip() in ("1", "true", "True")
        params.put_bool(key, enabled)

        updated = {key: enabled}
        if enabled:
          other_key = "TruckTuning" if key == "EVTuning" else "EVTuning"
          params.put_bool(other_key, False)
          updated[other_key] = False

        update_starpilot_toggles()
        return jsonify({
          "message": f"Parameter '{key}' updated successfully.",
          "updated": updated,
        }), 200

      if key in {"DynamicPedalsOnUI", "StaticPedalsOnUI"}:
        enabled = str_val.strip() in ("1", "true", "True")
        params.put_bool(key, enabled)

        updated = {key: enabled}
        if enabled:
          other_key = "StaticPedalsOnUI" if key == "DynamicPedalsOnUI" else "DynamicPedalsOnUI"
          params.put_bool(other_key, False)
          updated[other_key] = False

        update_starpilot_toggles()
        return jsonify({
          "message": f"Parameter '{key}' updated successfully.",
          "updated": updated,
        }), 200

      if key == "CustomAccelProfile":
        enabled = str_val.strip() in ("1", "true", "True")
        params.put_bool(key, enabled)

        updated = {key: enabled}
        defaults_lookup = _get_default_param_values()
        if enabled and not _get_custom_accel_profile_initialized():
          for custom_key in CUSTOM_ACCEL_PROFILE_PARAM_KEYS:
            custom_value = defaults_lookup[custom_key]
            params.put(custom_key, _serialize_param_write_value(custom_value))
            updated[custom_key] = float(custom_value)
          params.put_bool(CUSTOM_ACCEL_PROFILE_INITIALIZED_KEY, True)
        if enabled and not _get_custom_accel_profile_breakpoints_initialized():
          updated.update(_seed_custom_accel_profile_curve(defaults_lookup))
          updated[CUSTOM_ACCEL_PROFILE_BREAKPOINTS_INITIALIZED_KEY] = True

        update_starpilot_toggles()
        return jsonify({
          "message": f"Parameter '{key}' updated successfully.",
          "updated": updated,
        }), 200

      if key == "PersistExperimentalState":
        enabled = str_val.strip() in ("1", "true", "True")
        sync_persist_experimental_state(params, params_memory, enabled)
        update_starpilot_toggles()
        return jsonify({
          "message": f"Parameter '{key}' updated successfully.",
          "updated": {
            "PersistExperimentalState": enabled,
            "PersistedCEStatus": params.get_int("PersistedCEStatus", default=0),
          },
        }), 200

      if key == "PersistChillState":
        enabled = str_val.strip() in ("1", "true", "True")
        sync_persist_chill_state(params, params_memory, enabled)
        update_starpilot_toggles()
        return jsonify({
          "message": f"Parameter '{key}' updated successfully.",
          "updated": {
            "PersistChillState": enabled,
            "PersistedCCStatus": params.get_int("PersistedCCStatus", default=0),
          },
        }), 200

      if key == "IsRHD":
        enabled = str_val.strip() in ("1", "true", "True")
        params.put_bool("IsRHD", enabled)
        params.put_bool("IsRHDOverride", True)
        return jsonify({
          "message": "Right Hand Driving override updated successfully.",
          "updated": {
            "IsRHD": enabled,
            "IsRHDOverride": True,
          },
        }), 200

      if key == "IsRHDOverride":
        enabled = str_val.strip() in ("1", "true", "True")
        params.put_bool("IsRHDOverride", enabled)
        updated = {"IsRHDOverride": enabled}
        if not enabled:
          auto_rhd = params.get_bool("IsRhdDetected")
          params.put_bool("IsRHD", auto_rhd)
          updated["IsRHD"] = auto_rhd

        return jsonify({
          "message": "Right Hand Driving auto detection restored." if not enabled else "Right Hand Driving override enabled.",
          "updated": updated,
        }), 200

      if key == "CarMake":
        catalog = _get_fingerprint_catalog()
        normalized_make = _normalize_fingerprint_make_key(str_val)
        stored_make = catalog["make_label_by_key"].get(normalized_make, str_val.strip())
        params.put("CarMake", stored_make)
        update_starpilot_toggles()
        return jsonify({
          "message": "Car make updated successfully.",
          "updated": {"CarMake": stored_make},
        }), 200

      if key == "CarModel":
        selected_model = str_val.strip()
        if not selected_model:
          return jsonify({"error": "Car model cannot be empty."}), 400

        catalog = _get_fingerprint_catalog()
        if selected_label_input:
          labelled_models = {
            entry["value"] for entry in catalog["all_models"]
            if entry["label"] == selected_label_input
          }
          if labelled_models and selected_model not in labelled_models:
            return jsonify({"error": "Vehicle label and model do not match; refresh and reselect your vehicle."}), 400
        if selected_label_input and any(
          entry["value"] == selected_model and entry["label"] == selected_label_input
          for entry in catalog["all_models"]
        ):
          model_label = selected_label_input
        else:
          model_label = catalog["model_to_label"].get(selected_model)
        make_label = catalog["model_to_make"].get(selected_model)

        params.put("CarModel", selected_model)
        updated = {"CarModel": selected_model}

        if model_label:
          params.put("CarModelName", model_label)
          updated["CarModelName"] = model_label
        else:
          params.remove("CarModelName")
          updated["CarModelName"] = ""

        if make_label:
          params.put("CarMake", make_label)
          updated["CarMake"] = make_label

        update_starpilot_toggles()
        return jsonify({
          "message": f"Fingerprint set to '{model_label or selected_model}'.",
          "updated": updated,
        }), 200

      if key in ("Model", "DrivingModel"):
        selected_model = canonical_model_key(str_val.strip())
        if not selected_model:
          return jsonify({"error": "Driving model cannot be empty."}), 400
        if model_uses_external_gpu(selected_model) and not external_gpu_available():
          return jsonify({"error": "This model requires a detected external GPU."}), 409

        lab_config = normalize_model_lab_config(params.get(MODEL_LAB_CONFIG_PARAM, encoding="utf-8") or "")
        if lab_config["enabled"]:
          lab_config["enabled"] = False
          params.put(MODEL_LAB_CONFIG_PARAM, lab_config)
          params.remove(MODEL_LAB_RUNTIME_PARAM)

        params.put("Model", selected_model)
        params.put("DrivingModel", selected_model)

        available_models = [entry.strip() for entry in (params.get("AvailableModels", encoding="utf-8") or "").split(",")]
        available_names = [entry.strip() for entry in (params.get("AvailableModelNames", encoding="utf-8") or "").split(",")]
        model_versions = [entry.strip() for entry in (params.get("ModelVersions", encoding="utf-8") or "").split(",")]

        selected_index = next((i for i, model_key in enumerate(available_models) if canonical_model_key(model_key) == selected_model), -1)
        if selected_index != -1:
          if selected_index < len(available_names) and available_names[selected_index]:
            params.put("DrivingModelName", available_names[selected_index])
          elif is_builtin_model_key(selected_model):
            params.put("DrivingModelName", _default_model_name())

          if selected_index < len(model_versions) and model_versions[selected_index]:
            resolved_version = model_versions[selected_index]
            params.put("ModelVersion", resolved_version)
            params.put("DrivingModelVersion", resolved_version)
          elif is_builtin_model_key(selected_model):
            resolved_version = _default_model_version()
            params.put("ModelVersion", resolved_version)
            params.put("DrivingModelVersion", resolved_version)
        elif is_builtin_model_key(selected_model):
          params.put("DrivingModelName", _default_model_name())
          resolved_version = _default_model_version()
          params.put("ModelVersion", resolved_version)
          params.put("DrivingModelVersion", resolved_version)
        else:
          # Fallback to cached version map if this model isn't in the current manifest list yet.
          try:
            with open(MODELS_PATH / ".model_versions.json", "r") as f:
              versions = json.load(f)
              for alias in model_key_aliases(selected_model):
                if alias not in versions:
                  continue

                resolved_version = str(versions[alias]).strip()
                if resolved_version:
                  params.put("ModelVersion", resolved_version)
                  params.put("DrivingModelVersion", resolved_version)
                  break
          except Exception:
            pass

        profile = "big" if model_uses_external_gpu(selected_model) else "small"
        set_model_profile(params, profile, selected_model)
      elif key in ("ModelVersion", "DrivingModelVersion"):
        params.put("ModelVersion", str_val)
        params.put("DrivingModelVersion", str_val)
      elif key in CUSTOM_ACCEL_PROFILE_PARAM_KEYS:
        params.put(key, str_val)
        params.put_bool(CUSTOM_ACCEL_PROFILE_INITIALIZED_KEY, True)
      else:
        params.put(key, str_val)

      gsm_metered_apply_result = None
      if key == "GsmMetered":
        metered_enabled = str_val.strip() in ("1", "true", "True")
        gsm_metered_apply_result = _apply_cellular_metered_setting(metered_enabled)

      migrated_cancel_buttons = migrate_cancel_button_controls(params)
      update_starpilot_toggles()

      response = {"message": f"Parameter '{key}' updated successfully."}
      if key == "RivianAngleControl":
        response["message"] = "Rivian steering mode updated. The safe channel handoff is in progress."
      updated = {}
      if key in {
        "BelowSteerSpeedVolume", "DisengageVolume", "EngageVolume", "PromptVolume",
        "PromptDistractedVolume", "RefuseVolume", "WarningImmediateVolume", "WarningSoftVolume",
      }:
        _, value_types = _get_param_type_info()
        updated[key] = _get_current_param_value(key, value_types.get(key, int), _get_default_param_values())
      if key in PANDA_FIRMWARE_TOGGLE_KEYS:
        threading.Thread(target=_flash_panda_then_reboot, daemon=True).start()
        response["message"] = f"Parameter '{key}' updated successfully. Panda flashing started; device will reboot when finished."
      if key == "RemapCancelToDistance" and params.get_bool("RemapCancelToDistance"):
        updated["RemapCancelToDistance"] = True
        response["message"] = "Remap Cancel Button enabled."
      if migrated_cancel_buttons:
        if key == "RemapCancelToDistance" and params.get_bool("RemapCancelToDistance"):
          response["message"] = "Remap Cancel Button enabled. Existing distance mappings were copied to the new cancel button."
        for source_key, target_key in (
          ("DistanceButtonControl", "CancelButtonControl"),
          ("LongDistanceButtonControl", "LongCancelButtonControl"),
          ("VeryLongDistanceButtonControl", "VeryLongCancelButtonControl"),
        ):
          updated[target_key] = _get_param_int_value(target_key, _get_param_int_value(source_key, 0))

      if gsm_metered_apply_result is not None:
        updated["GsmMetered"] = str_val.strip() in ("1", "true", "True")
        response["networkProfilesUpdated"] = gsm_metered_apply_result.get("profiles", [])
        warnings = gsm_metered_apply_result.get("warnings", [])
        if warnings:
          response["warning"] = " ".join(warnings)
      if updated:
        response["updated"] = updated

      return jsonify(response), 200

    request_key = request.args.get("key")
    if request_key in CUSTOM_ACCEL_PROFILE_PARAM_KEYS and not _get_custom_accel_profile_initialized():
      defaults_lookup = _get_default_param_values()
      return _serialize_param_write_value(defaults_lookup.get(request_key)), 200
    if request_key == CUSTOM_ACCEL_PROFILE_INITIALIZED_KEY:
      return _serialize_param_write_value(_get_custom_accel_profile_initialized()), 200
    if request_key in CUSTOM_ACCEL_PROFILE_CURVE_PARAM_KEYS and not _get_custom_accel_profile_breakpoints_initialized():
      defaults_lookup = _get_default_param_values()
      return _serialize_param_write_value(_get_legacy_compatible_curve_value(request_key, defaults_lookup)), 200
    if request_key == CUSTOM_ACCEL_PROFILE_BREAKPOINTS_INITIALIZED_KEY:
      return _serialize_param_write_value(_get_custom_accel_profile_breakpoints_initialized()), 200
    if request_key == "LeadIndicator":
      return _serialize_param_write_value(_get_lead_indicator_enabled()), 200
    if request_key == "IsRHD" and not params.get_bool("IsRHDOverride"):
      return ("1" if params.get_bool("IsRhdDetected") else "0"), 200
    value = params.get(request_key) or ""
    if request_key == "CarMake":
      return _get_effective_car_make(value), 200
    if request_key in ("Model", "DrivingModel"):
      if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
      return canonical_model_key(str(value).strip()), 200
    return value, 200

  @app.route("/api/curve_speed_controller/reset", methods=["POST"])
  def reset_curve_speed_controller_data():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Curve Speed Controller data can only be reset while parked."}), 403

    params.put("CalibratedLateralAcceleration", 2.0)
    params.remove("CalibrationProgress")
    params.remove("CurvatureData")
    params_memory.put("CalibratedLateralAcceleration", 2.0)
    params_memory.put("CalibrationProgress", 0.0)
    params_memory.remove("CurvatureData")

    return jsonify({
      "message": "Curve Speed Controller data reset. Training will restart on the next drive.",
      "updated": {
        "CalibratedLateralAcceleration": 2.0,
        "CalibrationProgress": 0.0,
      },
    }), 200

  @app.route("/api/params/all", methods=["GET"])
  def get_all_params():
    migrate_cancel_button_controls(params)
    allowed_keys, types = _get_param_type_info()
    defaults_lookup = _get_default_param_values()

    result = {}
    for key in allowed_keys:
      t = types.get(key, str)
      try:
        result[key] = _get_current_param_value(key, t, defaults_lookup)
      except Exception:
        result[key] = None

    result["CarMake"] = _get_effective_car_make(result.get("CarMake"))
    result["TeslaCANWakeAvailable"] = supports_tesla_can_wake(params)
    result["HasRadar"] = _get_has_radar()
    result["VehicleParked"] = _get_vehicle_parked()
    result["AlphaLongitudinalAvailable"] = _get_alpha_longitudinal_available()
    result["HasRivianAngleHarness"] = _get_has_rivian_angle_harness()
    result["IsTiciOrTizi"] = _get_is_tici_or_tizi()

    for key in ("CalibratedLateralAcceleration", "CalibrationProgress"):
      try:
        result[key] = _get_current_param_value(key, float, defaults_lookup)
      except Exception:
        result[key] = None

    return jsonify(_sanitize_json_value(result)), 200

  @app.route("/api/params/defaults", methods=["GET"])
  def get_default_params():
    allowed_keys, types = _get_param_type_info()
    defaults_lookup = _get_default_param_values()

    result = {}
    for key in allowed_keys:
      t = types.get(key, str)
      default_val = defaults_lookup.get(key)

      try:
        if t == bool:
          if isinstance(default_val, bytes):
            default_str = default_val.decode("utf-8", errors="replace")
          else:
            default_str = str(default_val or "")
          result[key] = default_str in ("1", "true", "True")
        elif t == float:
          result[key] = float(default_val)
        elif t == int:
          result[key] = int(float(default_val))
        else:
          if isinstance(default_val, bytes):
            result[key] = default_val.decode("utf-8", errors="replace")
          else:
            result[key] = str(default_val or "")
      except Exception:
        result[key] = None

    return jsonify(_sanitize_json_value(result)), 200

  @app.route("/api/system/monitor", methods=["GET"])
  def system_monitor_snapshot():
    from openpilot.starpilot.system.the_galaxy.system_monitor import monitor
    try:
      # Telemetry is an optional companion; process monitoring works on its own.
      try:
        from openpilot.starpilot.system.the_galaxy.external_gpu_vitals import external_gpu_vitals
      except ImportError:
        vitals = {}
      else:
        vitals = external_gpu_vitals(include_onboard=True)
      response = jsonify({**monitor.sample(), 'vitals': vitals})
      response.headers['Cache-Control'] = 'no-store'
      return response
    except (OSError, ValueError, IndexError):
      return jsonify({'error': 'System activity is temporarily unavailable.'}), 503

  @app.route("/api/troubleshoot", methods=["GET"])
  def get_troubleshoot_data():
    try:
      return jsonify(_build_troubleshoot_payload()), 200
    except Exception as exception:
      return jsonify({"error": str(exception)}), 500

  @app.route("/api/troubleshoot/reset", methods=["POST"])
  def reset_troubleshoot_section():
    request_data = request.get_json() or {}
    section_id = str(request_data.get("sectionId") or "").strip()
    if not section_id:
      return jsonify({"error": "Missing 'sectionId' in request body."}), 400

    try:
      result = _reset_troubleshoot_section(section_id)
      message = f"{result['sectionTitle']} reset to defaults."
      if result["skippedCount"] > 0:
        message += f" Updated {result['updatedCount']} setting(s), skipped {result['skippedCount']}."
      return jsonify({
        "message": message,
        **result,
      }), 200
    except ValueError as exception:
      return jsonify({"error": str(exception)}), 400
    except Exception as exception:
      return jsonify({"error": str(exception)}), 500

  @app.route("/api/models/installed", methods=["GET"])
  def get_installed_models():
    catalog = get_model_catalog()
    installed = [{"value": model["value"], "label": model["label"]} for model in catalog if model["installed"]]

    # Keep current model selectable even if local files are currently inconsistent.
    current_model = _current_model_key()
    if current_model and all(model["value"] != current_model for model in installed):
      for model in catalog:
        if model["value"] == current_model:
          installed.append({"value": model["value"], "label": model["label"]})
          break

    return jsonify(installed), 200

  @app.route("/api/models/catalog", methods=["GET"])
  def get_models_catalog():
    models = get_model_catalog()
    return jsonify({
      "models": models,
      "currentModel": _current_model_key(),
      "activeSmallModel": _active_model_key("small"),
      "activeBigModel": _active_model_key("big"),
      "summary": {
        "installed": sum(1 for model in models if model["installed"]),
        "missing": sum(1 for model in models if not model["installed"]),
        "total": len(models),
      },
    }), 200

  def _model_lab_status_payload():
    models = get_model_catalog()
    model_by_key = {model["value"]: model for model in models}
    config = normalize_model_lab_config(params.get(MODEL_LAB_CONFIG_PARAM, encoding="utf-8") or "")
    config["lateralModel"] = canonical_model_key(config["lateralModel"])
    config["longitudinalModel"] = canonical_model_key(config["longitudinalModel"])
    chestnut_ready = external_gpu_available()
    runtime = {}
    try:
      runtime_value = params.get(MODEL_LAB_RUNTIME_PARAM, encoding="utf-8") or ""
      runtime = json.loads(runtime_value) if isinstance(runtime_value, str) and runtime_value else runtime_value
      if not isinstance(runtime, dict):
        runtime = {}
    except (TypeError, ValueError):
      runtime = {}

    eligible_models = [model for model in models if model.get("modelLabEligible")]
    ready_models = [model for model in eligible_models if model.get("modelLabArtifactInstalled")]
    lab_model_to_download = params_memory.get(MODEL_LAB_DOWNLOAD_PARAM, encoding="utf-8") or ""
    configuration_error = validate_model_lab_selection(
      config,
      model_by_key,
      chestnut_ready=chestnut_ready,
      require_installed=True,
    )
    return {
      "chestnutReady": chestnut_ready,
      "isOnroad": params.get_bool("IsOnroad"),
      "configuration": config,
      "configurationError": configuration_error or "",
      "runtime": runtime,
      "download": {
        "model": lab_model_to_download,
        "progress": params_memory.get(MODEL_DOWNLOAD_PROGRESS_PARAM, encoding="utf-8") or "",
      },
      "models": eligible_models,
      "summary": {
        "eligible": len(eligible_models),
        "ready": len(ready_models),
        "published": sum(1 for model in eligible_models if model.get("modelLabArtifactAvailable")),
        "declaredSize": sum(1 for model in eligible_models if model.get("manifestDeclaredSize")),
      },
      "manifest": {
        "version": params.get("ModelManifestVersion", encoding="utf-8") or "unknown",
      },
    }

  def _activate_preferred_model_profile():
    """Restore the model that the normal small/big profile system would run."""
    profile = "big" if external_gpu_available() and _active_model_key("big") else "small"
    model_key, model_name, model_version = get_model_profile(params, profile)
    if not model_key:
      model_key, model_name, model_version = _default_model_key(), _default_model_name(), _default_model_version()

    params.put("Model", model_key)
    params.put("DrivingModel", model_key)
    params.put("DrivingModelName", model_name or model_key)
    if model_version:
      params.put("ModelVersion", model_version)
      params.put("DrivingModelVersion", model_version)
    return model_name or model_key

  @app.route("/api/model-laboratory", methods=["GET", "PUT"])
  def model_laboratory():
    if request.method == "GET":
      return jsonify(_model_lab_status_payload()), 200

    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Model Laboratory can only be configured while parked."}), 403

    data = request.get_json(silent=True) or {}
    config = normalize_model_lab_config({
      "enabled": data.get("enabled", False),
      "lateralModel": canonical_model_key(str(data.get("lateralModel") or "")),
      "longitudinalModel": canonical_model_key(str(data.get("longitudinalModel") or "")),
    })
    models = get_model_catalog()
    model_by_key = {model["value"]: model for model in models}
    error = validate_model_lab_selection(
      config,
      model_by_key,
      chestnut_ready=external_gpu_available(),
      require_installed=True,
    )
    if error:
      return jsonify({"error": error}), 409

    params.put(MODEL_LAB_CONFIG_PARAM, config)
    params.remove(MODEL_LAB_RUNTIME_PARAM)
    if config["enabled"]:
      lateral = model_by_key[config["lateralModel"]]
      params.put("Model", lateral["value"])
      params.put("DrivingModel", lateral["value"])
      longitudinal = model_by_key[config["longitudinalModel"]]
      params.put("DrivingModelName", model_lab_pair_display_name(lateral["label"], longitudinal["label"]))
      if lateral.get("version"):
        params.put("ModelVersion", lateral["version"])
        params.put("DrivingModelVersion", lateral["version"])
      message = "Model Laboratory enabled. The pair will load on the next drive."
    else:
      restored_model = _activate_preferred_model_profile()
      message = f"Model Laboratory disabled. {restored_model} will be used next."

    return jsonify({"message": message, **_model_lab_status_payload()}), 200

  @app.route("/api/model-laboratory/download", methods=["POST"])
  def download_model_laboratory_artifact():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Model Laboratory artifacts can only be downloaded while parked."}), 403
    if (
      params_memory.get_bool(MODEL_DOWNLOAD_ALL_PARAM)
      or (params_memory.get(MODEL_DOWNLOAD_PARAM, encoding="utf-8") or "")
      or (params_memory.get(MODEL_LAB_DOWNLOAD_PARAM, encoding="utf-8") or "")
    ):
      return jsonify({"error": "A model download is already in progress."}), 409

    data = request.get_json(silent=True) or {}
    model_key = canonical_model_key(str(data.get("model") or "").strip())
    model = next((entry for entry in get_model_catalog() if entry["value"] == model_key), None)
    if model is None:
      return jsonify({"error": f"Unknown model '{model_key}'."}), 404
    if not model.get("modelLabEligible"):
      return jsonify({"error": "Only compatible small models have Model Laboratory eGPU variants."}), 409
    if not model.get("modelLabArtifactAvailable"):
      return jsonify({"error": "The manifest does not publish a precompiled AMD artifact for this model."}), 409
    if model.get("modelLabArtifactInstalled"):
      return jsonify({"message": f"The eGPU variant for \"{model['label']}\" is already downloaded."}), 200

    params_memory.remove(MODEL_CANCEL_DOWNLOAD_PARAM)
    params_memory.put(MODEL_LAB_DOWNLOAD_PARAM, model_key)
    params_memory.put(MODEL_DOWNLOAD_PROGRESS_PARAM, "Starting eGPU variant download...")
    return jsonify({"message": f"Started downloading the eGPU variant for \"{model['label']}\"."}), 200

  @app.route("/api/model-laboratory/artifact", methods=["DELETE"])
  def delete_model_laboratory_artifact():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Model Laboratory eGPU variants can only be deleted while parked."}), 403
    if (
      params_memory.get_bool(MODEL_DOWNLOAD_ALL_PARAM)
      or (params_memory.get(MODEL_DOWNLOAD_PARAM, encoding="utf-8") or "")
      or (params_memory.get(MODEL_LAB_DOWNLOAD_PARAM, encoding="utf-8") or "")
    ):
      return jsonify({"error": "Cannot delete an eGPU variant while a model download is in progress."}), 409

    data = request.get_json(silent=True) or {}
    model_key = canonical_model_key(str(data.get("model") or "").strip())
    model = next((entry for entry in get_model_catalog() if entry["value"] == model_key), None)
    if model is None:
      return jsonify({"error": f"Unknown model '{model_key}'."}), 404
    if not model.get("modelLabArtifactInstalled"):
      return jsonify({"message": f"No eGPU variant is downloaded for \"{model['label']}\"."}), 200

    config = normalize_model_lab_config(params.get(MODEL_LAB_CONFIG_PARAM, encoding="utf-8") or "")
    if config["enabled"] and model_key in (config["lateralModel"], config["longitudinalModel"]):
      return jsonify({"error": "Disable Model Laboratory or choose a different pair before deleting this eGPU variant."}), 409

    artifact_path = MODELS_PATH / model_accelerator_artifact_filename(model_key)
    try:
      artifact_path.unlink(missing_ok=True)
      Path(get_manifest_path(artifact_path)).unlink(missing_ok=True)
      for chunk_path in artifact_path.parent.glob(f"{artifact_path.name}.chunk*of*"):
        chunk_path.unlink(missing_ok=True)
    except Exception as exception:
      return jsonify({"error": f"Failed deleting the eGPU variant: {exception}"}), 500

    return jsonify({"message": f"Deleted the eGPU variant for \"{model['label']}\".", **_model_lab_status_payload()}), 200

  @app.route("/api/models/preferences", methods=["GET", "PUT"])
  def get_or_set_models_preferences():
    if request.method == "GET":
      return jsonify({
        "sortMode": read_legacy_param_file(MODEL_SORT_MODE_PARAM, DEFAULT_MODEL_SORT_MODE),
        "userFavorites": [entry for entry in (params.get(MODEL_USER_FAVORITES_PARAM, encoding="utf-8") or "").split(",") if entry],
      }), 200

    data = request.get_json() or {}
    changed = []

    if "sortMode" in data:
      sort_mode = str(data.get("sortMode") or DEFAULT_MODEL_SORT_MODE).strip() or DEFAULT_MODEL_SORT_MODE
      write_legacy_param_file(MODEL_SORT_MODE_PARAM, sort_mode)
      changed.append("sort mode")

    if "userFavorites" in data:
      incoming = data.get("userFavorites")
      if isinstance(incoming, list):
        favorites = ",".join(entry.strip() for entry in incoming if str(entry).strip())
      else:
        favorites = ",".join(entry.strip() for entry in str(incoming or "").split(",") if entry.strip())
      params.put(MODEL_USER_FAVORITES_PARAM, favorites)
      changed.append("favorites")

    if not changed:
      return jsonify({"error": "No preferences provided."}), 400

    return jsonify({"message": f"Updated model {' and '.join(changed)}."}), 200

  @app.route("/api/models/active", methods=["PUT"])
  def set_active_model_profile():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot change active models while driving."}), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("model"), str):
      return jsonify({"error": "An explicit model string is required."}), 400
    profile = str(data.get("profile") or "").strip().lower()
    if profile not in ("small", "big"):
      return jsonify({"error": "Model profile must be 'small' or 'big'."}), 400

    model_key = canonical_model_key(str(data.get("model") or "").strip())
    if not model_key:
      if profile != "big":
        return jsonify({"error": "Active Small cannot be disabled."}), 400

      lab_config = normalize_model_lab_config(params.get(MODEL_LAB_CONFIG_PARAM, encoding="utf-8") or "")
      if lab_config["enabled"]:
        lab_config["enabled"] = False
        params.put(MODEL_LAB_CONFIG_PARAM, lab_config)
        params.remove(MODEL_LAB_RUNTIME_PARAM)

      disable_big_model_profile(params)
      restored_model = _activate_preferred_model_profile()
      return jsonify({
        "message": f"Active Big disabled. {restored_model} will be used even when Chestnut is connected.",
        "profile": profile,
        "model": "",
      }), 200

    catalog = {model["value"]: model for model in get_model_catalog()}
    model = catalog.get(model_key)
    if model is None:
      return jsonify({"error": f"Unknown model '{model_key}'."}), 404
    if not model["installed"]:
      return jsonify({"error": f"Download '{model['label']}' before selecting it."}), 409
    if bool(model["requiresGpu"]) != (profile == "big"):
      expected = "an eGPU model" if profile == "big" else "an on-device model"
      return jsonify({"error": f"Active {profile.title()} must be {expected}."}), 409

    lab_config = normalize_model_lab_config(params.get(MODEL_LAB_CONFIG_PARAM, encoding="utf-8") or "")
    if lab_config["enabled"]:
      lab_config["enabled"] = False
      params.put(MODEL_LAB_CONFIG_PARAM, lab_config)
      params.remove(MODEL_LAB_RUNTIME_PARAM)

    set_model_profile(params, profile, model_key, model["label"], model["version"])
    active_model = _activate_preferred_model_profile()
    return jsonify({
      "message": f"Active {profile.title()} set to '{model['label']}'. {active_model} will be used next.",
      "profile": profile,
      "model": model_key,
    }), 200

  @app.route("/api/models/status", methods=["GET"])
  def get_models_status():
    models = get_model_catalog()
    model_to_download = canonical_model_key(params_memory.get(MODEL_DOWNLOAD_PARAM, encoding="utf-8") or "")
    lab_model_to_download = canonical_model_key(params_memory.get(MODEL_LAB_DOWNLOAD_PARAM, encoding="utf-8") or "")
    download_all = params_memory.get_bool(MODEL_DOWNLOAD_ALL_PARAM)
    progress = params_memory.get(MODEL_DOWNLOAD_PROGRESS_PARAM, encoding="utf-8") or ""
    cancelling = params_memory.get_bool(MODEL_CANCEL_DOWNLOAD_PARAM)

    downloading = bool(model_to_download or lab_model_to_download) or download_all
    current_model = _current_model_key()
    active_small_model = _active_model_key("small")
    active_big_model = _active_model_key("big")
    sort_mode = read_legacy_param_file(MODEL_SORT_MODE_PARAM, DEFAULT_MODEL_SORT_MODE)
    terminal = progress in ("Downloaded!", "All models downloaded!") or bool(re.search(r"cancelled|exists|failed|offline|invalid|error", progress, re.IGNORECASE))
    summary = {
      "installed": sum(1 for model in models if model["installed"]),
      "missing": sum(1 for model in models if not model["installed"]),
      "total": len(models),
    }

    now = time.monotonic()
    signature = (
      summary["total"],
      summary["installed"],
      summary["missing"],
      model_to_download,
      lab_model_to_download,
      download_all,
      downloading,
      cancelling,
      progress,
      current_model,
      active_small_model,
      active_big_model,
      sort_mode,
      terminal,
      bool(params.get_bool("IsOnroad")),
    )
    if model_status_debug["last_signature"] != signature or now - model_status_debug["last_log_time"] >= 15:
      print(
        f"[ModelStatus] addr={request.remote_addr or 'unknown'} total={summary['total']} "
        f"installed={summary['installed']} missing={summary['missing']} downloading={downloading} "
        f"download_all={download_all} model='{model_to_download or '-'}' current='{current_model or '-'}' "
        f"progress='{progress or 'Idle'}' cancelling={cancelling} onroad={params.get_bool('IsOnroad')} terminal={terminal}"
      )
      model_status_debug["last_signature"] = signature
      model_status_debug["last_log_time"] = now

    if summary["total"] == 0 and now - model_status_debug["last_empty_catalog_log_time"] >= 15:
      available_models = params.get("AvailableModels", encoding="utf-8") or ""
      available_names = params.get("AvailableModelNames", encoding="utf-8") or ""
      available_models_count = len([item for item in available_models.split(",") if item.strip()])
      available_names_count = len([item for item in available_names.split(",") if item.strip()])
      print(
        f"[ModelStatus] WARNING empty catalog available_models={available_models_count} "
        f"available_names={available_names_count} raw_available_models='{available_models[:120]}'"
      )
      model_status_debug["last_empty_catalog_log_time"] = now

    return jsonify({
      "modelToDownload": model_to_download,
      "modelLabModelToDownload": lab_model_to_download,
      "downloadAll": download_all,
      "downloading": downloading,
      "cancelling": cancelling,
      "progress": progress,
      "isOnroad": params.get_bool("IsOnroad"),
      "terminal": terminal,
      "models": models,
      "currentModel": current_model,
      "activeSmallModel": active_small_model,
      "activeBigModel": active_big_model,
      "summary": summary,
      "sortMode": sort_mode,
    }), 200

  @app.route("/api/models/refresh_manifest", methods=["POST"])
  def refresh_models_manifest():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot refresh model manifest while driving."}), 403

    if (
      params_memory.get_bool(MODEL_DOWNLOAD_ALL_PARAM)
      or (params_memory.get(MODEL_DOWNLOAD_PARAM, encoding="utf-8") or "")
      or (params_memory.get(MODEL_LAB_DOWNLOAD_PARAM, encoding="utf-8") or "")
    ):
      return jsonify({"error": "Cannot refresh model manifest while a download is in progress."}), 409

    try:
      from openpilot.starpilot.assets.model_manager import ModelManager

      # ModelManager expects raw Params semantics (encoding-less get -> str, not legacy bytes).
      manager = ModelManager(_params_raw, _params_memory_raw)
      manager.update_models(False)
    except Exception as exception:
      return jsonify({"error": f"Failed to refresh model manifest: {exception}"}), 500

    return jsonify({"message": "Model manifest refreshed."}), 200

  @app.route("/api/models/download", methods=["POST"])
  def start_model_download():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot download models while driving."}), 403

    if (
      params_memory.get_bool(MODEL_DOWNLOAD_ALL_PARAM)
      or (params_memory.get(MODEL_DOWNLOAD_PARAM, encoding="utf-8") or "")
      or (params_memory.get(MODEL_LAB_DOWNLOAD_PARAM, encoding="utf-8") or "")
    ):
      return jsonify({"error": "A model download is already in progress."}), 409

    data = request.get_json() or {}
    model_key = canonical_model_key((data.get("model") or "").strip())
    if not model_key:
      return jsonify({"error": "Missing model key."}), 400

    catalog = {model["value"]: model for model in get_model_catalog()}
    model = catalog.get(model_key)
    if model is None:
      return jsonify({"error": f"Unknown model '{model_key}'."}), 404

    if model["installed"]:
      return jsonify({"message": f"\"{model['label']}\" is already installed."}), 200
    allow_gpu_without_gpu = data.get("allowGpuWithoutGpu") is True
    if model["requiresGpu"] and not model["gpuAvailable"] and not allow_gpu_without_gpu:
      return jsonify({"error": "This model requires a detected external GPU."}), 409

    params_memory.remove(MODEL_CANCEL_DOWNLOAD_PARAM)
    params_memory.remove(MODEL_DOWNLOAD_ALL_PARAM)
    params_memory.put_bool(ALLOW_GPU_DOWNLOAD_WITHOUT_GPU_PARAM, allow_gpu_without_gpu)
    params_memory.put(MODEL_DOWNLOAD_PARAM, model_key)
    params_memory.put(MODEL_DOWNLOAD_PROGRESS_PARAM, "Downloading...")

    return jsonify({"message": f"Started downloading \"{model['label']}\"."}), 200

  @app.route("/api/models/download_all", methods=["POST"])
  def start_models_download_all():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot download models while driving."}), 403

    if (
      params_memory.get_bool(MODEL_DOWNLOAD_ALL_PARAM)
      or (params_memory.get(MODEL_DOWNLOAD_PARAM, encoding="utf-8") or "")
      or (params_memory.get(MODEL_LAB_DOWNLOAD_PARAM, encoding="utf-8") or "")
    ):
      return jsonify({"error": "A model download is already in progress."}), 409

    data = request.get_json(silent=True) or {}
    allow_gpu_without_gpu = data.get("allowGpuWithoutGpu") is True
    missing_models = [
      model for model in get_model_catalog()
      if not model["installed"] and (not model["requiresGpu"] or model["gpuAvailable"] or allow_gpu_without_gpu)
    ]
    if not missing_models:
      return jsonify({"message": "All models are already installed."}), 200

    params_memory.remove(MODEL_CANCEL_DOWNLOAD_PARAM)
    params_memory.remove(MODEL_DOWNLOAD_PARAM)
    params_memory.put_bool(ALLOW_GPU_DOWNLOAD_WITHOUT_GPU_PARAM, allow_gpu_without_gpu)
    params_memory.put_bool(MODEL_DOWNLOAD_ALL_PARAM, True)
    params_memory.put(MODEL_DOWNLOAD_PROGRESS_PARAM, "Downloading...")

    return jsonify({"message": f"Started downloading {len(missing_models)} model(s)."}), 200

  @app.route("/api/models/cancel", methods=["POST"])
  def cancel_model_download():
    model_to_download = params_memory.get(MODEL_DOWNLOAD_PARAM, encoding="utf-8") or ""
    lab_model_to_download = params_memory.get(MODEL_LAB_DOWNLOAD_PARAM, encoding="utf-8") or ""
    download_all = params_memory.get_bool(MODEL_DOWNLOAD_ALL_PARAM)
    if not model_to_download and not lab_model_to_download and not download_all:
      return jsonify({"message": "No active model download to cancel."}), 200

    params_memory.put_bool(MODEL_CANCEL_DOWNLOAD_PARAM, True)
    return jsonify({"message": "Cancellation requested."}), 200

  @app.route("/api/models/delete", methods=["POST"])
  def delete_model_files():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot delete model files while driving."}), 403

    if (
      params_memory.get_bool(MODEL_DOWNLOAD_ALL_PARAM)
      or (params_memory.get(MODEL_DOWNLOAD_PARAM, encoding="utf-8") or "")
      or (params_memory.get(MODEL_LAB_DOWNLOAD_PARAM, encoding="utf-8") or "")
    ):
      return jsonify({"error": "Cannot delete model files while a download is in progress."}), 409

    data = request.get_json() or {}
    model_key = canonical_model_key((data.get("model") or "").strip())
    if not model_key:
      return jsonify({"error": "Missing model key."}), 400

    current_model = _current_model_key()
    active_models = {current_model, _active_model_key("small"), _active_model_key("big")}
    if model_key in active_models:
      return jsonify({"error": "Cannot delete the currently active model."}), 409

    catalog = {model["value"]: model for model in get_model_catalog()}
    model = catalog.get(model_key)
    if model is None:
      return jsonify({"error": f"Unknown model '{model_key}'."}), 404
    if model.get("builtin"):
      return jsonify({"error": "Cannot delete the built-in default model."}), 409

    models_dir = MODELS_PATH
    if not models_dir.is_dir():
      return jsonify({"message": "No model directory exists yet."}), 200

    deleted = []
    for item in models_dir.iterdir():
      name = item.name
      is_match = (
        name == f"{model_key}.thneed" or
        name == f"{model_key}.pkl" or
        name.startswith(f"{model_key}_")
      )

      if not is_match:
        continue

      try:
        if item.is_dir():
          shutil.rmtree(item)
        else:
          item.unlink(missing_ok=True)
        deleted.append(name)
      except Exception as exception:
        return jsonify({"error": f"Failed deleting '{name}': {exception}"}), 500

    if not deleted:
      return jsonify({"message": f"No files found for \"{model['label']}\"."}), 200

    return jsonify({"message": f"Deleted {len(deleted)} file(s) for \"{model['label']}\"."}), 200

  def _get_maps_status_payload():
    current_selected = params.get("MapsSelected", encoding="utf-8") or ""
    selected_raw = sanitize_selected_locations_csv(current_selected)
    if selected_raw != current_selected:
      params.put("MapsSelected", selected_raw)

    selected_entries = get_selected_map_entries(selected_raw)
    selected_locations = [entry["token"] for entry in selected_entries]
    size_cache = load_maps_storage_cache(params.get(MAPS_DOWNLOAD_SIZE_CACHE_PARAM, encoding="utf-8") or "")
    storage_known = size_cache.storage_known
    storage_bytes = size_cache.storage_bytes if storage_known else 0
    maps_present = bool(size_cache.maps_present)

    selected_key = selection_key(selected_locations)
    raw_progress = params_memory.get(MAPS_DOWNLOAD_PROGRESS_PARAM, encoding="utf-8") or ""
    try:
      download_progress = json.loads(raw_progress) if raw_progress else {}
    except (TypeError, ValueError):
      download_progress = {}
    if not isinstance(download_progress, dict):
      download_progress = {}

    if params_memory.get_bool(MAPS_DOWNLOAD_PARAM) and "storageBytes" in download_progress:
      storage_known = bool(download_progress.get("storageKnown", True))
      storage_bytes = nonnegative_int(download_progress.get("storageBytes", 0)) if storage_known else 0
      maps_present = storage_bytes > 0 if storage_known else False

    if not params_memory.get_bool(MAPS_DOWNLOAD_PARAM) and selected_key and download_progress.get("selectedKey") != selected_key:
      cached_bytes = size_cache.selection_estimate_bytes(selected_key)
      if cached_bytes > 0:
        download_progress = {
          "active": False,
          "cancelled": False,
          "completed": False,
          "downloadedBytes": 0,
          "downloadedFiles": 0,
          "estimatedDownloadBytes": cached_bytes,
          "estimateSource": "previous_additional_storage",
          "etaSeconds": 0,
          "percent": 0,
          "phase": "idle",
          "primaryLocation": "",
          "selectedKey": selected_key,
          "selectedLocations": selected_locations,
          "storageBytes": storage_bytes,
          "storageKnown": storage_known,
          "totalFiles": size_cache.selection_total_files(selected_key),
          "updatedAt": size_cache.selection_updated_at(selected_key),
          "bytesPerSecond": 0,
        }
      else:
        download_progress = {
          "active": False,
          "cancelled": False,
          "completed": False,
          "downloadedBytes": 0,
          "downloadedFiles": 0,
          "estimatedDownloadBytes": 0,
          "estimateSource": "",
          "etaSeconds": 0,
          "percent": 0,
          "phase": "idle",
          "primaryLocation": "",
          "selectedKey": selected_key,
          "selectedLocations": selected_locations,
          "storageBytes": storage_bytes,
          "storageKnown": storage_known,
          "totalFiles": 0,
          "updatedAt": "",
          "bytesPerSecond": 0,
        }

    return {
      "selectedLocations": selected_locations,
      "selectedEntries": selected_entries,
      "selectedCount": len(selected_locations),
      "hasSelection": bool(selected_locations),
      "downloading": params_memory.get_bool(MAPS_DOWNLOAD_PARAM),
      "cancelling": params_memory.get_bool(MAPS_CANCEL_DOWNLOAD_PARAM),
      "isOnroad": params.get_bool("IsOnroad"),
      "lastUpdate": params.get("LastMapsUpdate", encoding="utf-8") or "Never",
      "mapsPresent": maps_present,
      "storageKnown": storage_known,
      "scheduleLabel": schedule_label(params.get("PreferredSchedule")),
      "scheduleOptions": MAP_SCHEDULE_OPTIONS,
      "scheduleValue": schedule_param_value(params.get("PreferredSchedule")),
      "storageBytes": storage_bytes,
      "downloadProgress": download_progress,
    }

  @app.route("/api/maps/catalog", methods=["GET"])
  def get_maps_catalog():
    return jsonify({
      "sections": MAPS_CATALOG,
      "scheduleOptions": MAP_SCHEDULE_OPTIONS,
    }), 200

  @app.route("/api/maps/status", methods=["GET"])
  def get_maps_status():
    return jsonify(_get_maps_status_payload()), 200

  @app.route("/api/maps/selection", methods=["POST"])
  def set_maps_selection():
    payload = request.get_json(silent=True) or {}
    selected_raw = sanitize_selected_locations_csv(payload.get("selectedLocations"))
    params.put("MapsSelected", selected_raw)
    return jsonify({
      "message": f"Saved {len([entry for entry in selected_raw.split(',') if entry])} selected map region(s).",
      "status": _get_maps_status_payload(),
    }), 200

  @app.route("/api/maps/schedule", methods=["POST"])
  def set_maps_schedule():
    payload = request.get_json(silent=True) or {}
    schedule_value = schedule_param_value(payload.get("schedule"))
    params.put("PreferredSchedule", schedule_value)
    return jsonify({
      "message": f"Map auto-update schedule set to {schedule_label(schedule_value)}.",
      "status": _get_maps_status_payload(),
    }), 200

  @app.route("/api/maps/download", methods=["POST"])
  def start_maps_download():
    payload = request.get_json(silent=True) or {}

    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot download maps while driving."}), 403

    if params_memory.get_bool(MAPS_DOWNLOAD_PARAM):
      return jsonify({"error": "A map download is already in progress."}), 409

    if "selectedLocations" in payload:
      params.put("MapsSelected", sanitize_selected_locations_csv(payload.get("selectedLocations")))

    if "schedule" in payload:
      params.put("PreferredSchedule", schedule_param_value(payload.get("schedule")))

    selected_raw = sanitize_selected_locations_csv(params.get("MapsSelected", encoding="utf-8") or "")
    if not selected_raw:
      return jsonify({"error": "No map regions are selected."}), 400

    params.put("MapsSelected", selected_raw)
    params_memory.remove(MAPS_CANCEL_DOWNLOAD_PARAM)
    params_memory.put_bool(MAPS_DOWNLOAD_PARAM, True)

    return jsonify({
      "message": f"Started downloading {len([entry for entry in selected_raw.split(',') if entry])} selected map region(s).",
      "status": _get_maps_status_payload(),
    }), 200

  @app.route("/api/maps/cancel", methods=["POST"])
  def cancel_maps_download():
    if not params_memory.get_bool(MAPS_DOWNLOAD_PARAM):
      return jsonify({"message": "No active map download to cancel.", "status": _get_maps_status_payload()}), 200

    params_memory.put_bool(MAPS_CANCEL_DOWNLOAD_PARAM, True)
    return jsonify({"message": "Map download cancellation requested.", "status": _get_maps_status_payload()}), 200

  @app.route("/api/maps/remove", methods=["POST"])
  def remove_maps_data():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot remove maps while driving."}), 403

    if params_memory.get_bool(MAPS_DOWNLOAD_PARAM):
      return jsonify({"error": "Cannot remove maps while a download is in progress."}), 409

    if MAPS_PATH.exists():
      shutil.rmtree(MAPS_PATH, ignore_errors=True)

    size_cache = load_maps_storage_cache(params.get(MAPS_DOWNLOAD_SIZE_CACHE_PARAM, encoding="utf-8") or "")
    size_cache.clear()
    params.put(MAPS_DOWNLOAD_SIZE_CACHE_PARAM, size_cache.to_json())
    params_memory.remove(MAPS_DOWNLOAD_PROGRESS_PARAM)

    return jsonify({"message": "Maps removed.", "status": _get_maps_status_payload()}), 200

  @app.route("/api/params_memory", methods=["GET"])
  def get_param_memory():
    return params_memory.get(request.args.get("key")) or "", 200

  def _param_text(value):
    if value is None:
      return ""
    if isinstance(value, bytes):
      return value.decode("utf-8", errors="ignore").strip()
    return str(value).strip()

  def _default_model_key():
    default_key = _param_text(params.get_default_value("Model") or params.get_default_value("DrivingModel"))
    return canonical_model_key(default_key) or "rdf43"

  def _default_model_name():
    return _param_text(params.get_default_value("DrivingModelName")) or "Regret Driven Framework V4"

  def _default_model_version():
    default_version = _param_text(params.get_default_value("ModelVersion") or params.get_default_value("DrivingModelVersion"))
    return default_version or "v15"

  def _current_model_key():
    current_model = _param_text(params.get("Model", encoding="utf-8") or params.get("DrivingModel", encoding="utf-8"))
    return canonical_model_key(current_model) or _default_model_key()

  def _active_model_key(profile):
    model_key, _, _ = get_model_profile(params, profile)
    return canonical_model_key(model_key)

  def is_model_installed(model_key, model_version, on_disk_files):
    del model_version
    if is_builtin_model_key(model_key):
      return True

    filename = f"{model_key}_driving_tinygrad.pkl"
    if filename in on_disk_files:
      return True

    manifest = get_manifest_path(filename)
    if manifest not in on_disk_files:
      return False

    try:
      num_chunks = int((MODELS_PATH / manifest).read_text().strip())
    except (OSError, ValueError):
      return False

    return num_chunks > 0 and all(
      get_chunk_name(filename, index, num_chunks) in on_disk_files
      for index in range(num_chunks)
    )

  def get_model_catalog():
    available = [model.strip() for model in (params.get("AvailableModels", encoding="utf-8") or "").split(",")]
    names = [name.strip() for name in (params.get("AvailableModelNames", encoding="utf-8") or "").split(",")]
    series = [entry.strip() for entry in (params.get("AvailableModelSeries", encoding="utf-8") or "").split(",")]
    versions = [entry.strip() for entry in (params.get("ModelVersions", encoding="utf-8") or "").split(",")]
    artifact_formats = [entry.strip() for entry in (params.get("AvailableModelArtifactFormats", encoding="utf-8") or "").split(",")]
    released_dates = [entry.strip() for entry in (params.get("ModelReleasedDates", encoding="utf-8") or "").split(",")]

    community_favorites = {canonical_model_key(entry.strip()) for entry in (params.get("CommunityFavorites", encoding="utf-8") or "").split(",") if entry.strip()}
    user_favorites = {canonical_model_key(entry.strip()) for entry in (params.get(MODEL_USER_FAVORITES_PARAM, encoding="utf-8") or "").split(",") if entry.strip()}

    try:
      on_disk_files = {entry.name for entry in MODELS_PATH.iterdir()} if MODELS_PATH.is_dir() else set()
    except Exception:
      on_disk_files = set()

    try:
      metadata_payload = json.loads((MODELS_PATH / ".model_artifacts.json").read_text())
      artifact_metadata = metadata_payload if isinstance(metadata_payload, dict) else {}
    except (OSError, TypeError, ValueError):
      artifact_metadata = {}

    external_gpu_present = external_gpu_available()
    models_by_key = {}
    for i, key in enumerate(available):
      canonical_key = canonical_model_key(key)
      if not canonical_key:
        continue

      label = names[i] if i < len(names) and names[i] else key
      model_version = versions[i] if i < len(versions) else ""
      artifact_format = artifact_formats[i] if i < len(artifact_formats) else ""
      model_series = series[i] if i < len(series) and series[i] else "Custom Series"
      released = released_dates[i] if i < len(released_dates) else ""
      requires_external_gpu = model_uses_external_gpu(canonical_key)
      gpu_available = not requires_external_gpu or external_gpu_present
      metadata = artifact_metadata.get(canonical_key, {})
      metadata = metadata if isinstance(metadata, dict) else {}
      small_model = is_small_model_metadata({**metadata, "uses_external_gpu": requires_external_gpu})
      lab_eligible = model_lab_manifest_eligible({**metadata, "uses_external_gpu": requires_external_gpu}, model_version)
      accelerator_artifacts = metadata.get("accelerator_artifacts", {})
      accelerator_artifacts = accelerator_artifacts if isinstance(accelerator_artifacts, dict) else {}
      chestnut_artifact = accelerator_artifacts.get("chestnut", {})
      chestnut_artifact = chestnut_artifact if isinstance(chestnut_artifact, dict) else {}
      lab_artifact_available = (
        bool(chestnut_artifact)
        and str(chestnut_artifact.get("execution_device") or chestnut_artifact.get("device") or "").strip().upper() == "AMD"
      )
      lab_artifact_path = MODELS_PATH / model_accelerator_artifact_filename(canonical_key)
      lab_artifact_installed = lab_artifact_available and file_chunked_exists(lab_artifact_path)
      existing = models_by_key.get(canonical_key)
      if existing is None:
        models_by_key[canonical_key] = {
          "value": canonical_key,
          "label": label,
          "series": model_series,
          "version": model_version,
          "artifactFormat": artifact_format,
          "requiresGpu": requires_external_gpu,
          "gpuAvailable": gpu_available,
          "small": small_model,
          "modelSize": str(metadata.get("model_size") or ("small (inferred)" if small_model else "chestnut (inferred)")),
          "manifestDeclaredSize": bool(metadata.get("model_size_declared", metadata.get("size_class"))),
          "modelLabEligible": lab_eligible,
          "modelLabArtifactAvailable": lab_artifact_available,
          "modelLabArtifactInstalled": lab_artifact_installed,
          "released": released,
          "builtin": is_builtin_model_key(canonical_key),
          "communityFavorite": canonical_key in community_favorites,
          "userFavorite": canonical_key in user_favorites,
        }
        continue

      if (not existing["label"] or existing["label"] == existing["value"]) and label:
        existing["label"] = label
      if (not existing["series"] or existing["series"] == "Custom Series") and model_series:
        existing["series"] = model_series
      if not existing["version"] and model_version:
        existing["version"] = model_version
      if not existing.get("artifactFormat") and artifact_format:
        existing["artifactFormat"] = artifact_format
      if not existing["released"] and released:
        existing["released"] = released
      existing["builtin"] = existing["builtin"] or is_builtin_model_key(canonical_key)
      existing["communityFavorite"] = existing["communityFavorite"] or canonical_key in community_favorites
      existing["userFavorite"] = existing["userFavorite"] or canonical_key in user_favorites
      existing["requiresGpu"] = existing["requiresGpu"] or requires_external_gpu
      existing["gpuAvailable"] = not existing["requiresGpu"] or external_gpu_present
      existing["small"] = existing["small"] and small_model
      existing["modelLabEligible"] = existing["modelLabEligible"] and lab_eligible
      existing["modelLabArtifactAvailable"] = existing["modelLabArtifactAvailable"] and lab_artifact_available
      existing["modelLabArtifactInstalled"] = existing["modelLabArtifactInstalled"] and lab_artifact_installed

    default_key = _default_model_key()
    default_entry = models_by_key.setdefault(default_key, {
      "value": default_key,
      "label": _default_model_name(),
      "series": "Custom Series",
      "version": _default_model_version(),
      "artifactFormat": "tinygrad_single_v1",
      "requiresGpu": False,
      "gpuAvailable": True,
      "small": True,
      "modelSize": "small (inferred)",
      "manifestDeclaredSize": False,
      "modelLabEligible": model_lab_manifest_eligible(artifact_metadata.get(default_key, {}), _default_model_version()),
      "modelLabArtifactAvailable": False,
      "modelLabArtifactInstalled": False,
      "released": "",
      "builtin": True,
      "communityFavorite": default_key in community_favorites,
      "userFavorite": default_key in user_favorites,
    })
    default_entry["builtin"] = True
    if not default_entry["label"] or default_entry["label"] == default_entry["value"]:
      default_entry["label"] = _default_model_name()
    if not default_entry["version"]:
      default_entry["version"] = _default_model_version()

    models = []
    for key, model in models_by_key.items():
      installed = is_model_installed(key, model["version"], on_disk_files)
      partial = (not model["builtin"]) and (not installed) and any(file.startswith(f"{key}.") or file.startswith(f"{key}_") for file in on_disk_files)
      models.append({
        **model,
        "installed": installed,
        "partial": partial,
      })

    models.sort(key=lambda model: (model["series"].lower(), model["label"].lower()))
    return model_sizes.annotate(models, MODELS_PATH,
                                Path(__file__).resolve().parents[3] / "selfdrive/modeld/models/driving_tinygrad.pkl",
                                artifact_metadata, model_accelerator_artifact_filename)

  @app.route("/api/routes", methods=["GET"])
  def list_routes():
    def generate():
      routes = _route_scan_entries(FOOTAGE_PATHS)
      connect_dongle_id = params.get("StockDongleId", encoding="utf-8") or params.get("DongleId", encoding="utf-8") or ""
      for payload in _route_metadata_events(routes, connect_dongle_id):
        yield f"data: {json.dumps(payload)}\n\n"

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response

  def _valid_route_name(name):
    return bool(utilities.ROUTE_RE.fullmatch(str(name or "")))

  @app.route("/api/routes/<name>", methods=["DELETE"])
  def delete_route(name):
    if not _valid_route_name(name):
      return jsonify({"error": "Invalid route name."}), 400

    segment_prefix = f"{name}--"
    for footage_path in FOOTAGE_PATHS:
      if not os.path.isdir(footage_path):
        continue
      for segment in os.listdir(footage_path):
        if utilities.SEGMENT_RE.fullmatch(segment) and segment.startswith(segment_prefix):
          delete_file(os.path.join(footage_path, segment))
    return {"message": "Route deleted!"}, 200

  @app.route("/api/routes/delete_all", methods=["DELETE", "POST"])
  def delete_all_routes():
    if _safe_params_get_bool("IsOnroad"):
      return jsonify({"error": "Cannot delete driving routes while driving."}), 409

    if not _ROUTE_DELETE_LOCK.acquire(blocking=False):
      return jsonify({"error": "Route deletion is already in progress."}), 409

    try:
      utilities.stop_dashboard_background_analysis()
      include_preserved = request.args.get("include_preserved", "true").strip().lower() not in ("0", "false", "no", "off")

      route_paths = []
      seen_paths = set()
      for footage_path in FOOTAGE_PATHS:
        path = str(footage_path).rstrip("/")
        if path and path not in seen_paths:
          seen_paths.add(path)
          route_paths.append(path)

      preserved_route_names = set()
      deleted_route_names = set()
      if include_preserved:
        for route_path in route_paths:
          _run_factory_reset_delete(route_path)
      else:
        # The preserve xattr lives on one segment, but preservation applies to the
        # whole route in every footage root.
        for route_path in route_paths:
          if not os.path.isdir(route_path):
            continue
          for segment in os.listdir(route_path):
            if utilities.SEGMENT_RE.fullmatch(segment) and utilities.has_preserve_attr(os.path.join(route_path, segment)):
              preserved_route_names.add(segment.rsplit("--", 1)[0])

        for route_path in route_paths:
          if not os.path.isdir(route_path):
            continue
          for segment in os.listdir(route_path):
            if not utilities.SEGMENT_RE.fullmatch(segment):
              continue
            route_name = segment.rsplit("--", 1)[0]
            if route_name in preserved_route_names:
              continue
            delete_file(os.path.join(route_path, segment))
            deleted_route_names.add(route_name)

      persisted_route_count = utilities.clear_dashboard_route_history(
        params,
        retained_route_names=preserved_route_names if not include_preserved else None,
      )
      _STATS_RESPONSE_CACHE.update({
        "updated_at": 0.0,
        "payload": None,
      })
      return jsonify({
        "success": True,
        "message": (
          "All local driving routes deleted, including preserved routes. Saved personal records were kept."
          if include_preserved else
          "All non-preserved local driving routes deleted. Preserved routes were kept."
        ),
        "deletedPaths": len(route_paths) if include_preserved else 0,
        "deletedRoutes": len(deleted_route_names) if not include_preserved else None,
        "preservedRoutes": len(preserved_route_names) if not include_preserved else 0,
        "clearedDashboardRoutes": persisted_route_count,
      }), 200
    except Exception as exception:
      return jsonify({"error": f"Failed to delete driving routes: {exception}"}), 500
    finally:
      _ROUTE_DELETE_LOCK.release()

  @app.route("/api/routes/<name>/preserve", methods=["POST"])
  def preserve_route(name):
    if not _valid_route_name(name):
      return jsonify({"error": "Invalid route name."}), 400

    preserved_routes = set()
    for footage_path in FOOTAGE_PATHS:
      if not os.path.isdir(footage_path):
        continue
      for segment in os.listdir(footage_path):
        if utilities.SEGMENT_RE.fullmatch(segment) and utilities.has_preserve_attr(os.path.join(footage_path, segment)):
          preserved_routes.add(segment.rsplit("--", 1)[0])

    if name not in preserved_routes and len(preserved_routes) >= PRESERVE_COUNT:
      return {"error": f"Maximum of {PRESERVE_COUNT} preserved routes reached..."}, 400

    for footage_path in FOOTAGE_PATHS:
      segment_path = _route_first_segment_path(name, footage_path)
      if segment_path is not None:
        os.setxattr(segment_path, PRESERVE_ATTR_NAME, PRESERVE_ATTR_VALUE)
        return {"message": "Route preserved!!"}, 200

    return {"error": "Route not found"}, 404

  @app.route("/api/routes/<name>/preserve", methods=["DELETE"])
  def un_preserve_route(name):
    if not _valid_route_name(name):
      return jsonify({"error": "Invalid route name."}), 400

    for footage_path in FOOTAGE_PATHS:
      segment_path = _route_first_segment_path(name, footage_path)
      if segment_path is not None and utilities.has_preserve_attr(segment_path):
        os.removexattr(segment_path, PRESERVE_ATTR_NAME)
        return {"message": "Route unpreserved!"}, 200
    return {"error": "Route not found"}, 404

  @app.route("/video/<name>/combined", methods=["GET"])
  def get_combined_route_video(name):
    if not _valid_route_name(name):
      return jsonify({"error": "Invalid route name."}), 400

    camera = request.args.get("camera", "forward")
    for footage_path in FOOTAGE_PATHS:
      try:
        segments = utilities.get_segments_in_route(name, footage_path)
      except OSError:
        continue
      if segments:
        cam_file = {
          "forward": "fcamera.hevc",
          "wide": "ecamera.hevc",
          "driver": "dcamera.hevc",
        }.get(camera, "fcamera.hevc")

        input_files = [
          os.path.join(footage_path, seg, cam_file)
          for seg in segments
          if os.path.exists(os.path.join(footage_path, seg, cam_file))
        ]

        if not input_files:
          return {"error": "No video files found"}, 404

        response = Response(utilities.ffmpeg_stream_concatenated_mp4(input_files), mimetype="video/mp4")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    return {"error": "Route not found"}, 404

  @app.route("/api/routes/<name>", methods=["GET"])
  def get_route(name):
    if not _valid_route_name(name):
      return jsonify({"error": "Invalid route name."}), 400

    for footage_path in FOOTAGE_PATHS:
      try:
        segments = utilities.get_segments_in_route(name, footage_path)
      except OSError:
        continue
      if segments:
        base_path = os.path.join(footage_path, segments[0])
        segment_urls = [f"/video/{segment}" for segment in segments]
        # Probing each segment cost an ffprobe before playback could even start,
        # and segments are a fixed minute anyway.
        total_duration = len(segments) * SEGMENT_DURATION_SECONDS
        return {
          "name": name,
          "segment_urls": segment_urls,
          "total_duration": total_duration,
          "date": utilities.get_route_start_time(base_path),
          "available_cameras": utilities.get_available_cameras(base_path),
        }, 200
    return {"error": "Route not found"}, 404

  @app.route("/api/routes/<name>/logs", methods=["GET"])
  def list_route_logs(name):
    logs = _route_log_files(name)
    if not logs:
      return jsonify({"error": "No full logs are stored on the device for this route."}), 404

    return jsonify({
      "name": name,
      "totalBytes": sum(size for *_, size in logs),
      "segments": [
        {
          "segment": segment,
          "segmentNum": int(segment.rsplit("--", 1)[1]),
          "filename": filename,
          "bytes": size,
          "url": f"/api/routes/{name}/logs/{int(segment.rsplit('--', 1)[1])}",
        }
        for segment, filename, _, size in logs
      ],
    }), 200

  @app.route("/api/routes/<name>/logs/<int:segment_num>", methods=["GET"])
  def download_route_log(name, segment_num):
    for segment, filename, path, _ in _route_log_files(name):
      if int(segment.rsplit("--", 1)[1]) == segment_num:
        return send_file(path, as_attachment=True, download_name=f"{segment}-{filename}")
    return jsonify({"error": "No full log is stored on the device for this segment."}), 404

  @app.route("/api/routes/<name>/logs/download", methods=["GET"])
  def download_route_logs_archive(name):
    logs = _route_log_files(name)
    if not logs:
      return jsonify({"error": "No full logs are stored on the device for this route."}), 404

    def generate():
      buffer = _TarBuffer()
      # streamed a file at a time so a long route never needs its whole archive in memory
      with tarfile.open(fileobj=buffer, mode="w|") as archive:
        for segment, filename, path, _ in logs:
          try:
            archive.add(path, arcname=f"{segment}/{filename}")
          except OSError:
            continue
          chunk = buffer.pop()
          if chunk:
            yield chunk
      chunk = buffer.pop()
      if chunk:
        yield chunk

    response = Response(generate(), mimetype="application/x-tar")
    response.headers["Content-Disposition"] = f'attachment; filename="{name}-logs.tar"'
    return response

  @app.route("/api/routes/clear_name", methods=["POST"])
  @app.route("/api/routes/reset_name", methods=["POST"])
  def clear_route_name():
    data = request.get_json()
    route_name = data.get("name")

    if not _valid_route_name(route_name):
      return jsonify({"error": "Invalid route name"}), 400

    cleared = False
    original_timestamp = None
    for footage_path in FOOTAGE_PATHS:
      if not os.path.exists(footage_path):
        continue

      segments_to_process = [s for s in os.listdir(footage_path) if s.startswith(route_name) and os.path.isdir(os.path.join(footage_path, s))]
      if not segments_to_process:
        continue

      for segment in segments_to_process:
        segment_dir = os.path.join(footage_path, segment)
        for item in os.listdir(segment_dir):
          if utilities.is_route_marker_file(item):
            try:
              os.remove(os.path.join(segment_dir, item))
              cleared = True
            except OSError:
              pass

        if cleared:
          route_timestamp_dt = utilities.get_route_start_time(segment_dir)
          original_timestamp = route_timestamp_dt.isoformat() if route_timestamp_dt else None

    if cleared:
      return jsonify({"message": "Route name cleared successfully!", "timestamp": original_timestamp}), 200
    else:
      return jsonify({"error": "Route not found or no custom name to clear"}), 404

  @app.route("/api/routes/rename", methods=["POST"])
  def rename_route():
    data = request.get_json()
    old_name = data.get("old")
    new_name_raw = data.get("new")

    if not _valid_route_name(old_name) or not new_name_raw:
      return jsonify({"error": "Missing or invalid route name"}), 400

    new_name = utilities.secure_filename(new_name_raw)
    renamed = False

    for footage_path in FOOTAGE_PATHS:
      if not os.path.exists(footage_path):
        continue

      segments_to_process = [s for s in os.listdir(footage_path) if s.startswith(old_name) and os.path.isdir(os.path.join(footage_path, s))]
      if not segments_to_process:
        continue

      for segment in segments_to_process:
        segment_dir = os.path.join(footage_path, segment)
        for item in os.listdir(segment_dir):
          if utilities.is_route_marker_file(item):
            try:
              os.remove(os.path.join(segment_dir, item))
            except OSError:
              pass

      for segment in segments_to_process:
        segment_dir = os.path.join(footage_path, segment)
        new_name_file_path = os.path.join(segment_dir, new_name)

        try:
          with open(new_name_file_path, "a"):
            os.utime(new_name_file_path, None)
          renamed = True
        except OSError as e:
          return jsonify({"error": f"Error creating new name file: {e}"}), 500

    if renamed:
      return jsonify({"message": "Route renamed successfully!", "name": new_name}), 200
    else:
      return jsonify({"error": "Route not found"}), 404

  @app.route("/api/screen_recordings/delete/<path:filename>", methods=["DELETE"])
  def delete_screen_recording(filename):
    mp4_path = SCREEN_RECORDINGS_PATH / filename
    if not mp4_path.exists():
      return {"error": "File not found"}, 404

    delete_file(str(mp4_path))

    for ext in (".png", ".gif"):
      thumb = mp4_path.with_suffix(ext)
      if thumb.exists():
        delete_file(str(thumb))

    return {"message": "Deleted"}, 200

  @app.route("/api/screen_recordings/delete_all", methods=["DELETE"])
  def delete_all_screen_recordings():
    files_to_delete = [f for f in os.listdir(SCREEN_RECORDINGS_PATH) if f.endswith(".mp4")]
    for filename in files_to_delete:
      delete_file(os.path.join(SCREEN_RECORDINGS_PATH, filename))
      for ext in (".png", ".gif"):
        thumb = os.path.join(SCREEN_RECORDINGS_PATH, filename.replace(".mp4", ext))
        if os.path.exists(thumb):
          delete_file(thumb)
    return {"message": "All screen recordings deleted!"}, 200

  @app.route("/api/screen_recordings/download/<path:filename>", methods=["GET"])
  def download_screen_recording(filename):
    return send_from_directory(SCREEN_RECORDINGS_PATH, filename, as_attachment=True)

  @app.route("/api/screen_recordings/list", methods=["GET"])
  def list_screen_recordings():
    def generate():
      recordings = sorted(
        [recording for recording in SCREEN_RECORDINGS_PATH.glob("*.mp4") if not Path(f"{recording}.lock").exists()],
        key=lambda p: p.stat().st_mtime,
        reverse=True
      )
      total = len(recordings)

      yield f"data: {json.dumps({'progress': 0, 'total': total})}\n\n"

      with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(utilities.process_screen_recording, mp4): mp4 for mp4 in recordings}
        for processed, future in enumerate(as_completed(futures), start=1):
          try:
            result = future.result()
            yield f"data: {json.dumps({'recordings': [result]})}\n\n"
          except Exception as exception:
            print(f"Error processing recording: {exception}")

          yield f"data: {json.dumps({'progress': processed, 'total': total})}\n\n"

    return Response(generate(), mimetype="text/event-stream")

  @app.route("/screen_recordings/<path:filename>", methods=["GET"])
  def serve_screen_recording_asset(filename):
    return send_from_directory(SCREEN_RECORDINGS_PATH, filename)

  @app.route("/api/screen_recordings/rename", methods=["POST"])
  def rename_screen_recording():
    data = request.get_json() or {}
    old = data.get("old")
    new_raw = data.get("new")

    if not old or not new_raw:
      return {"error": "Missing filenames"}, 400

    new = utilities.secure_filename(new_raw)
    old_path = SCREEN_RECORDINGS_PATH / old
    new_path = SCREEN_RECORDINGS_PATH / new

    if not old_path.exists():
      return {"error": "Original file not found"}, 404

    if new_path.exists():
      return {"error": "Target file already exists"}, 400

    old_path.rename(new_path)
    for extension in (".png", ".gif"):
      old_thumb = old_path.with_suffix(extension)
      new_thumb = new_path.with_suffix(extension)

      if old_thumb.exists():
        old_thumb.rename(new_thumb)

    return {"message": "Renamed"}, 200

  @app.route("/api/speed_limits", methods=["GET"])
  def speed_limits():
    data = json.loads(params.get("SpeedLimitsFiltered") or "[]")

    buffer = BytesIO(json.dumps(data, indent=2).encode())
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="speed_limits.json", mimetype="application/json")

  def _speed_limits_status_payload():
    status = params_memory.get("UpdateSpeedLimitsStatus", encoding="utf-8") or ""
    processing = bool(status and status != "Completed!")
    enabled = params.get_bool("SpeedLimitFiller")
    is_onroad = params.get_bool("IsOnroad")
    time_valid = system_time_valid()
    is_metric = params.get_bool("IsMetric")

    vision_enabled = params.get_bool("VisionSpeedLimitDetection")
    vision_speed_limit = params_memory.get_float("VisionSpeedLimit") if vision_enabled else 0
    vision_confidence = params_memory.get_float("VisionSpeedLimitConfidence") if vision_enabled else 0
    vision_bookmark_count = params_memory.get_int("VisionSpeedLimitBookmarkCount") if vision_enabled else 0
    vision_debug_session = params_memory.get("VisionSpeedLimitDebugSession", encoding="utf-8") if vision_enabled else ""
    vision_last_event = params_memory.get("VisionSpeedLimitLastEvent", encoding="utf-8") if vision_enabled else ""
    vision_status = params_memory.get("VisionSpeedLimitStatus", encoding="utf-8") or ("Idle" if vision_enabled else "Disabled")
    vision_stream = params_memory.get("VisionSpeedLimitStream", encoding="utf-8") if vision_enabled else ""
    vision_speed_unit = "km/h" if is_metric else "mph"
    vision_display_speed = round(vision_speed_limit * (CV.MS_TO_KPH if is_metric else CV.MS_TO_MPH)) if vision_speed_limit > 0 else 0

    network_connected = True
    try:
      network_connected = HARDWARE.get_network_type() != log.DeviceState.NetworkType.none
    except Exception:
      pass

    overpass_requests = {}
    try:
      overpass_requests = json.loads(params.get("OverpassRequests", encoding="utf-8") or "{}")
    except Exception:
      pass

    current_day = datetime.now(timezone.utc).day
    saved_day = int(overpass_requests.get("day", current_day) or current_day)
    total_requests = int(overpass_requests.get("total_requests", 0) or 0)
    max_requests = int(overpass_requests.get("max_requests", 10000) or 10000)
    if saved_day != current_day:
      total_requests = 0
    api_limit_hit = total_requests >= max_requests

    reason = ""
    if not enabled:
      reason = "Enable Speed Limit Filler on the device first."
    elif processing:
      reason = status
    elif is_onroad:
      reason = "Processing is only available while parked."
    elif not time_valid:
      reason = "System time is not valid yet."
    elif not network_connected:
      reason = "Connect the device to the internet first."
    elif api_limit_hit:
      reason = "Today's Overpass API request limit has been reached."

    return {
      "apiLimitHit": api_limit_hit,
      "canProcessNow": enabled and not processing and not is_onroad and time_valid and network_connected and not api_limit_hit,
      "enabled": enabled,
      "isOnroad": is_onroad,
      "networkConnected": network_connected,
      "processing": processing,
      "reason": reason,
      "status": status or "Idle",
      "timeValid": time_valid,
      "totalRequests": total_requests,
      "maxRequests": max_requests,
      "visionConfidence": vision_confidence,
      "visionBookmarkCount": vision_bookmark_count,
      "visionDebugSession": vision_debug_session,
      "visionDisplaySpeed": vision_display_speed,
      "visionEnabled": vision_enabled,
      "visionLastEvent": vision_last_event,
      "visionSpeedUnit": vision_speed_unit,
      "visionStatus": vision_status,
      "visionStream": vision_stream,
    }

  @app.route("/api/speed_limits/status", methods=["GET"])
  def speed_limits_status():
    return jsonify(_speed_limits_status_payload()), 200

  @app.route("/api/speed_limits/process", methods=["POST"])
  def process_speed_limits():
    payload = _speed_limits_status_payload()
    if not payload["canProcessNow"]:
      return jsonify({"error": payload["reason"] or "Speed limit processing is unavailable right now."}), 409

    params_memory.put("UpdateSpeedLimitsStatus", "Calculating...")
    params_memory.put_bool("UpdateSpeedLimits", True)

    return jsonify({"message": "Speed limit processing started.", "status": "Calculating..."}), 202

  def _get_stats_locked():
    cache_now = time.monotonic()
    cached_payload = _STATS_RESPONSE_CACHE.get("payload")
    if cached_payload is not None and cache_now - _STATS_RESPONSE_CACHE.get("updated_at", 0.0) < STATS_RESPONSE_CACHE_SECONDS:
      return cached_payload

    build_metadata = get_build_metadata()

    short_branch = build_metadata.channel
    if short_branch == "StarPilot":
      galaxy_label = "Stable"
    elif short_branch == "Dom":
      galaxy_label = "Testing"
    else:
      galaxy_label = "Experimental"

    software_info = {
      "branchName": build_metadata.channel,
      "buildEnvironment": galaxy_label,
      "changelogUrl": utilities.get_github_changelog_url(build_metadata.openpilot.git_normalized_origin, build_metadata.channel),
      "commitHash": build_metadata.openpilot.git_commit,
      "commitUrl": utilities.get_github_commit_url(build_metadata.openpilot.git_normalized_origin, build_metadata.openpilot.git_commit),
      "forkMaintainer": utilities.get_repo_owner(build_metadata.openpilot.git_normalized_origin),
      "updateAvailable": "Yes" if params.get_bool("UpdaterFetchAvailable") else "No",
      "versionDate": utilities.format_git_date(build_metadata.openpilot.git_commit_date),
    }

    try:
      dashboard_stats = utilities.get_dashboard_stats(FOOTAGE_PATHS, params)
    except Exception:
      dashboard_stats = utilities.get_dashboard_stats([], params)

    payload = {
      "diskUsage": utilities.get_disk_usage(),
      "driveStats": utilities.get_drive_stats(),
      "softwareInfo": software_info,
      "dashboard": dashboard_stats,
    }
    _STATS_RESPONSE_CACHE.update({
      "updated_at": time.monotonic(),
      "payload": payload,
    })
    return payload

  @app.route("/api/stats", methods=["GET"])
  def get_stats():
    cache_now = time.monotonic()
    cached_payload = _STATS_RESPONSE_CACHE.get("payload")
    if cached_payload is not None and cache_now - _STATS_RESPONSE_CACHE.get("updated_at", 0.0) < STATS_RESPONSE_CACHE_SECONDS:
      return cached_payload

    # Flask serves requests concurrently. Serialize cache misses so a slow
    # storage scan cannot be multiplied by repeated homepage polling.
    with _STATS_RESPONSE_LOCK:
      return _get_stats_locked()

  @app.route("/api/device/status", methods=["GET"])
  def device_status():
    return jsonify({
      "status": "Driving" if params.get_bool("IsOnroad") else "Parked",
      "online": True,
      "lanIp": utilities.get_current_lan_ip(),
      "networkName": utilities.get_current_network_name(),
    }), 200

  @app.route("/api/stats/ignore_drive", methods=["POST"])
  def ignore_drive_stats():
    request_data = request.get_json() or {}
    route_names = request_data.get("routeNames", [])
    if not isinstance(route_names, list):
      return jsonify({"error": "routeNames must be a list."}), 400

    try:
      ignored_routes = utilities.ignore_dashboard_routes(params, route_names)
    except ValueError as exception:
      return jsonify({"error": str(exception)}), 400

    _STATS_RESPONSE_CACHE.update({
      "updated_at": 0.0,
      "payload": None,
    })
    return jsonify({
      "message": "Drive statistics ignored.",
      "routeNames": ignored_routes,
    }), 200

  @app.route("/api/stats/include_drive", methods=["POST"])
  def include_drive_stats():
    request_data = request.get_json() or {}
    route_names = request_data.get("routeNames", [])
    if not isinstance(route_names, list):
      return jsonify({"error": "routeNames must be a list."}), 400

    try:
      included_routes = utilities.include_dashboard_routes(params, route_names)
    except ValueError as exception:
      return jsonify({"error": str(exception)}), 400

    _STATS_RESPONSE_CACHE.update({
      "updated_at": 0.0,
      "payload": None,
    })
    return jsonify({
      "message": "Drive statistics included.",
      "routeNames": included_routes,
    }), 200

  @app.route("/api/plots/live", methods=["GET"])
  def get_live_plots():
    _ensure_plots_worker()
    with _plots_lock:
      payload = dict(_plots_state)

    timestamp = _safe_float(payload.get("timestamp", 0.0), 0.0)
    age_seconds = max(0.0, time.time() - timestamp) if timestamp else 999.0

    return jsonify({
      **payload,
      "isOnroad": params.get_bool("IsOnroad"),
      "bootStabilizing": _is_plots_boot_stabilizing(),
      "sampleAgeSeconds": round(age_seconds, 3),
      "stale": age_seconds > _PLOTS_SAMPLE_STALE_AFTER_S,
    }), 200

  @app.route("/api/testing_grounds", methods=["GET"])
  def get_testing_grounds():
    state = _get_testing_grounds_state()
    return jsonify({
      **_serialize_testing_grounds_state(state),
      "isOnroad": params.get_bool("IsOnroad"),
    }), 200

  @app.route("/api/testing_grounds/select", methods=["POST"])
  def select_testing_ground():
    request_data = request.get_json() or {}
    slot_id = str(request_data.get("slotId") or "").strip()
    variant = str(request_data.get("variant") or "").strip().upper()

    if not slot_id:
      return jsonify({"error": "Missing 'slotId' in request body."}), 400

    try:
      state, changed = _set_testing_ground_selection(slot_id, variant)
    except ValueError as exception:
      return jsonify({"error": str(exception)}), 400
    except Exception as exception:
      return jsonify({"error": str(exception)}), 500

    if changed:
      _publish_testing_ground_custom_reserved(state, "manual_change")

    slot = _find_testing_ground_slot(state, slot_id)
    slot_name = slot.get("name", f"Testing Ground {slot_id}")
    selected_variant = str(state.get("activeVariant") or _TESTING_GROUNDS_DEFAULT_VARIANT)
    return jsonify({
      "message": f"{slot_name} set to variant {selected_variant}.",
      **_serialize_testing_grounds_state(state),
      "isOnroad": params.get_bool("IsOnroad"),
    }), 200

  @app.route("/api/longitudinal_maneuvers/status", methods=["GET"])
  def get_longitudinal_maneuvers_status():
    status = _load_longitudinal_maneuver_status()
    return jsonify(_serialize_longitudinal_maneuver_status(status)), 200

  @app.route("/api/longitudinal_maneuvers/start", methods=["POST"])
  def start_longitudinal_maneuvers():
    if params.get_bool("LateralManeuverMode"):
      _set_lateral_maneuver_mode(False)
    status = _set_longitudinal_maneuver_mode(True)
    return jsonify({
      "message": "Longitudinal maneuver mode armed. Engage with SET to start.",
      **_serialize_longitudinal_maneuver_status(status),
    }), 200

  @app.route("/api/longitudinal_maneuvers/stop", methods=["POST"])
  def stop_longitudinal_maneuvers():
    status = _set_longitudinal_maneuver_mode(False)
    return jsonify({
      "message": "Longitudinal maneuver mode disabled.",
      **_serialize_longitudinal_maneuver_status(status),
    }), 200

  @app.route("/api/lateral_maneuvers/status", methods=["GET"])
  def get_lateral_maneuvers_status():
    status = _load_lateral_maneuver_status()
    return jsonify(_serialize_lateral_maneuver_status(status)), 200

  @app.route("/api/lateral_maneuvers/start", methods=["POST"])
  def start_lateral_maneuvers():
    if params.get_bool("LongitudinalManeuverMode"):
      _set_longitudinal_maneuver_mode(False)
    status = _set_lateral_maneuver_mode(True)
    return jsonify({
      "message": "Lateral maneuver mode armed. Stabilize on a straight, flat road to start.",
      **_serialize_lateral_maneuver_status(status),
    }), 200

  @app.route("/api/lateral_maneuvers/stop", methods=["POST"])
  def stop_lateral_maneuvers():
    status = _set_lateral_maneuver_mode(False)
    return jsonify({
      "message": "Lateral maneuver mode disabled.",
      **_serialize_lateral_maneuver_status(status),
    }), 200

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/status", methods=["GET"])
  @app.route("/api/flm/status", methods=["GET"])
  def get_flm_status():
    is_onroad = params.get_bool("IsOnroad")
    lane_centering = params.get_bool("LaneCentering")
    if is_onroad:
      flm_workspace.cancel_flm_if_onroad()
    workspace = flm_workspace.list_workspace()
    return jsonify({
      "isOnroad": is_onroad,
      "laneCentering": lane_centering,
      "status": flm_workspace.read_flm_status(),
      "activeTrial": workspace.get("activeTrial"),
      "reports": workspace.get("reports", [])[:10],
      "savedTunes": workspace.get("savedTunes", []),
    }), 200

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/analyze", methods=["POST"])
  @app.route("/api/flm/analyze", methods=["POST"])
  def start_flm_analysis():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "FLM analysis can only run offroad."}), 409
    if params.get_bool("LaneCentering"):
      return jsonify({
        "error": "Turn Lane Centering off before running FLM. Its correction must not be mixed into lateral-tuning analysis."
      }), 409

    data = request.get_json(silent=True) or {}
    route_names = [str(route).strip() for route in data.get("routes", []) if str(route).strip()]
    if not route_names:
      return jsonify({"error": "No routes were selected."}), 400

    try:
      segment_ranges = flm_workspace.normalize_segment_ranges(route_names, data.get("segmentRanges", {}))
    except (TypeError, ValueError) as error:
      return jsonify({"error": str(error)}), 400

    try:
      started = flm_workspace.start_flm_background_analysis(route_names, FOOTAGE_PATHS, segment_ranges)
    except (TypeError, ValueError) as error:
      return jsonify({"error": str(error)}), 400
    if not started:
      return jsonify({"error": "Failed to start FLM analysis."}), 500

    return jsonify({
      "message": f"Started FLM analysis for {len(route_names[:flm_workspace.FLM_ANALYZER_ROUTE_LIMIT])} route(s).",
      "status": flm_workspace.read_flm_status(),
    }), 200

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/analyze/stop", methods=["POST"])
  @app.route("/api/flm/analyze/stop", methods=["POST"])
  def stop_flm_analysis():
    stopped = flm_workspace.stop_flm_background_analysis()
    return jsonify({
      "message": "Stopped FLM analysis." if stopped else "No active FLM analysis was running.",
      "stopped": bool(stopped),
      "status": flm_workspace.read_flm_status(),
    }), 200

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/report/<report_id>", methods=["GET"])
  @app.route("/api/flm/report/<report_id>", methods=["GET"])
  def get_flm_report(report_id):
    try:
      return jsonify(flm_workspace.load_report(report_id)), 200
    except FileNotFoundError:
      return jsonify({"error": "FLM report not found."}), 404

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/report/<report_id>", methods=["DELETE"])
  @app.route("/api/flm/report/<report_id>", methods=["DELETE"])
  def delete_flm_report(report_id):
    try:
      return jsonify(flm_workspace.delete_report(report_id)), 200
    except FileNotFoundError:
      return jsonify({"error": "FLM report not found."}), 404
    except RuntimeError as error:
      return jsonify({"error": str(error)}), 409

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/report/<report_id>/path", methods=["POST"])
  @app.route("/api/flm/report/<report_id>/path", methods=["POST"])
  def select_flm_report_path(report_id):
    data = request.get_json(silent=True) or {}
    path_key = str(data.get("pathKey") or "").strip()
    if not path_key:
      return jsonify({"error": "pathKey is required."}), 400

    try:
      return jsonify(flm_workspace.select_report_path(report_id, path_key)), 200
    except FileNotFoundError:
      return jsonify({"error": "FLM report not found."}), 404
    except ValueError as error:
      return jsonify({"error": str(error)}), 400

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/workspace", methods=["GET"])
  @app.route("/api/flm/workspace", methods=["GET"])
  def get_flm_workspace():
    return jsonify(flm_workspace.list_workspace()), 200

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/workspace/clear", methods=["POST"])
  @app.route("/api/flm/workspace/clear", methods=["POST"])
  def clear_flm_workspace():
    try:
      return jsonify(flm_workspace.clear_workspace()), 200
    except RuntimeError as error:
      return jsonify({"error": str(error)}), 409

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/saved-tunes", methods=["POST"])
  @app.route("/api/flm/saved-tunes", methods=["POST"])
  def save_flm_tune():
    data = request.get_json(silent=True) or {}
    try:
      return jsonify(flm_workspace.save_active_trial_as_tune(str(data.get("name") or ""))), 200
    except ValueError as error:
      return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
      return jsonify({"error": str(error)}), 409

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/saved-tunes/<tune_id>/apply", methods=["POST"])
  @app.route("/api/flm/saved-tunes/<tune_id>/apply", methods=["POST"])
  def apply_flm_saved_tune(tune_id):
    try:
      return jsonify(flm_workspace.apply_saved_tune(tune_id)), 200
    except FileNotFoundError:
      return jsonify({"error": "Saved FLM tune not found."}), 404
    except RuntimeError as error:
      return jsonify({"error": str(error)}), 409

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/saved-tunes/<tune_id>/submit", methods=["POST"])
  @app.route("/api/flm/saved-tunes/<tune_id>/submit", methods=["POST"])
  def submit_flm_saved_tune(tune_id):
    data = request.get_json(silent=True) or {}
    try:
      return jsonify(flm_workspace.submit_saved_tune(tune_id, str(data.get("discordUsername") or ""))), 200
    except FileNotFoundError:
      return jsonify({"error": "Saved FLM tune not found."}), 404
    except ValueError as error:
      return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
      return jsonify({"error": str(error)}), 409

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/saved-tunes/<tune_id>", methods=["PATCH"])
  @app.route("/api/flm/saved-tunes/<tune_id>", methods=["PATCH"])
  def rename_flm_saved_tune(tune_id):
    data = request.get_json(silent=True) or {}
    try:
      return jsonify(flm_workspace.rename_saved_tune(tune_id, str(data.get("name") or ""))), 200
    except FileNotFoundError:
      return jsonify({"error": "Saved FLM tune not found."}), 404
    except ValueError as error:
      return jsonify({"error": str(error)}), 400

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/saved-tunes/<tune_id>", methods=["DELETE"])
  @app.route("/api/flm/saved-tunes/<tune_id>", methods=["DELETE"])
  def delete_flm_saved_tune(tune_id):
    try:
      return jsonify(flm_workspace.delete_saved_tune(tune_id)), 200
    except FileNotFoundError:
      return jsonify({"error": "Saved FLM tune not found."}), 404
    except RuntimeError as error:
      return jsonify({"error": str(error)}), 409

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/trials/apply", methods=["POST"])
  @app.route("/api/flm/trials/apply", methods=["POST"])
  def apply_flm_trial():
    data = request.get_json(silent=True) or {}
    report_id = str(data.get("reportId") or "").strip()
    profile_id = str(data.get("profileId") or "").strip()
    if not report_id or not profile_id:
      return jsonify({"error": "Both reportId and profileId are required."}), 400

    try:
      result = flm_workspace.apply_trial_profile(report_id, profile_id)
    except FileNotFoundError:
      return jsonify({"error": "FLM profile not found."}), 404
    except RuntimeError as error:
      return jsonify({"error": str(error)}), 409

    return jsonify(result), 200

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/trials/revert", methods=["POST"])
  @app.route("/api/flm/trials/revert", methods=["POST"])
  def revert_flm_trial():
    try:
      result = flm_workspace.revert_trial_profile()
    except FileNotFoundError:
      return jsonify({"error": "No active FLM trial snapshot was found."}), 404
    except Exception as error:
      return jsonify({"error": f"{type(error).__name__}: {error}"}), 500

    return jsonify(result), 200

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/trials/accept", methods=["POST"])
  @app.route("/api/flm/trials/accept", methods=["POST"])
  def accept_flm_trial():
    try:
      result = flm_workspace.accept_trial_as_baseline()
    except FileNotFoundError:
      return jsonify({"error": "No active FLM trial was found."}), 404

    return jsonify(result), 200

  @app.route(f"{LEGACY_LATERAL_METHOD_API_PREFIX}/feedback", methods=["POST"])
  @app.route("/api/flm/feedback", methods=["POST"])
  def save_flm_feedback():
    data = request.get_json(silent=True) or {}
    report_id = str(data.get("reportId") or "").strip()
    if not report_id:
      return jsonify({"error": "reportId is required."}), 400

    feedback = {
      "acceptedDimensions": data.get("acceptedDimensions", []),
      "ignoredDimensions": data.get("ignoredDimensions", []),
      "notes": data.get("notes", ""),
    }
    try:
      result = flm_workspace.record_feedback(report_id, feedback)
    except FileNotFoundError:
      return jsonify({"error": "FLM report not found."}), 404

    return jsonify(result), 200

  @app.route("/api/update/fast/status", methods=["GET"])
  def get_fast_update_status():
    state_data = _get_fast_update_state()
    repo_path = str(_get_openpilot_root())
    git_data = _collect_fast_update_info(include_remote=not state_data.get("running", False))
    return jsonify({
      **state_data,
      **git_data,
      "isOnroad": _safe_params_get_bool("IsOnroad"),
      "automaticUpdates": _safe_params_get_bool("AutomaticUpdates"),
      "versionPin": version_install.read_pin(repo_path),
      "interruptedUpdateRecovery": _get_interrupted_update_recovery(repo_path, state_data),
      "warning": "Fast update skips backup creation and finalization safeguards.",
    }), 200

  @app.route("/api/update/recover", methods=["POST"])
  def recover_update():
    if _safe_params_get_bool("IsOnroad"):
      return jsonify({"error": "Cannot recover an interrupted update while driving."}), 409

    repo_path = str(_get_openpilot_root())
    with _fast_update_lock:
      if _fast_update_state.get("running"):
        return jsonify({"error": "An update action is still in progress."}), 409

      recovered, recovery_status = recover_interrupted_update(
        repo_path,
        is_onroad=False,
        update_running=False,
        updater_state=_safe_params_get("UpdaterState", encoding="utf-8", default=""),
      )
      if not recovered:
        return jsonify({
          "error": recovery_status.get("reason") or "The interrupted update could not be recovered safely.",
          "interruptedUpdateRecovery": recovery_status,
        }), 409

      _fast_update_state.update({
        "running": False,
        "stage": "idle",
        "message": "Interrupted update recovered. Ready to retry.",
        "lastError": "",
        "finishedAt": time.time(),
        "progressStep": 0,
        "progressTotalSteps": _FAST_UPDATE_TOTAL_STEPS,
        "progressStepPercent": 0.0,
        "progressPercent": 0.0,
        "progressLabel": "Ready",
        "progressDetail": "Abandoned update lock cleared safely.",
      })

    return jsonify({
      "message": "Interrupted update recovered. Retrying now...",
      "interruptedUpdateRecovery": recovery_status,
    }), 200

  @app.route("/api/update/branches", methods=["GET"])
  def get_update_branches():
    state_data = _get_fast_update_state()
    repo_path = str(_get_openpilot_root())

    try:
      current_branch = _git_stdout(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    except Exception as exception:
      return jsonify({"error": str(exception)}), 500

    branches, remote_error = _list_origin_branches(repo_path, include_remote=not state_data.get("running", False))
    if current_branch and current_branch not in branches:
      branches = sorted([*branches, current_branch], key=lambda branch: branch.lower())

    return jsonify({
      "currentBranch": current_branch,
      "branches": branches,
      "remoteError": remote_error,
      "isOnroad": _safe_params_get_bool("IsOnroad"),
      "running": state_data.get("running", False),
    }), 200

  @app.route("/api/update/versions", methods=["GET"])
  def get_update_versions():
    branch = request.args.get("branch", "")
    repo_path = str(_get_openpilot_root())
    if not branch or len(branch) > 255 or not _is_valid_git_branch_name(repo_path, branch):
      return jsonify({"error": "Choose a valid branch to browse its versions."}), 400
    try:
      page = int(request.args.get("page", "1"))
    except (TypeError, ValueError):
      return jsonify({"error": "Invalid history page."}), 400
    head = request.args.get("head") or None
    if page < 1 or (head is not None and not re.fullmatch(r"[0-9a-f]{40}", head)) or (page > 1 and head is None):
      return jsonify({"error": "Invalid history page or history revision."}), 400
    if not _remote_git_check_allowed():
      return jsonify({"error": "Version history will be available once the device clock is synchronized."}), 503
    try:
      return jsonify(version_history.list_versions(repo_path, branch, page=page, head=head)), 200
    except version_history.VersionHistoryError as exception:
      return jsonify({"error": str(exception)}), 422
    except Exception:
      return jsonify({"error": "Unable to load version history. Please try again."}), 503

  @app.route("/api/update/version", methods=["POST"])
  def run_version_install():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload.get("confirmed") is not True:
      return jsonify({"error": "Confirm the selected version before installing."}), 400
    branch, commit = payload.get("branch"), payload.get("commit")
    repo_path = str(_get_openpilot_root())
    if not isinstance(branch, str) or not branch or len(branch) > 255 or not _is_valid_git_branch_name(repo_path, branch):
      return jsonify({"error": "Invalid branch name."}), 400
    if not isinstance(commit, str) or (commit != "latest" and not re.fullmatch(r"[0-9a-f]{40}", commit)):
      return jsonify({"error": "Choose Latest or an exact version from this branch's history."}), 400
    try:
      version_install.require_parked()
    except version_install.InstallError as exception:
      return jsonify({"error": str(exception)}), 409
    with _fast_update_lock:
      if _fast_update_state.get("running"):
        return jsonify({"error": "Another update action is already in progress."}), 409
      _fast_update_state.update({
        "running": True, "stage": "starting", "message": "Preparing selected version...",
        "lastError": "", "lastBranch": branch, "lastMode": "version-install",
        "startedAt": time.time(), "finishedAt": 0.0,
        "progressStep": 1, "progressTotalSteps": _FAST_UPDATE_TOTAL_STEPS,
        "progressStepPercent": 0.0, "progressPercent": 0.0,
        "progressLabel": "Preparing selected version", "progressDetail": "Checking branch history and compatibility...",
      })
    try:
      threading.Thread(target=_version_install_worker, args=(branch, commit), daemon=True).start()
    except Exception as exception:
      _set_fast_update_error_state("Unable to start version installation.", exception)
      return jsonify({"error": "Unable to start version installation."}), 503
    return jsonify({"message": "Version installation started. The device will reboot when complete."}), 202

  @app.route("/api/update/agnos_status", methods=["GET"])
  def get_agnos_update_status():
    state_data = _get_fast_update_state()
    if state_data.get("running", False):
      return jsonify({"error": "Cannot check AGNOS update status while an update action is running."}), 409

    repo_path = str(_get_openpilot_root())
    try:
      current_branch = _git_stdout(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
      local_commit = _git_stdout(repo_path, ["rev-parse", "HEAD"])
      origin_remote = _git_stdout(repo_path, ["config", "--get", "remote.origin.url"])
    except Exception as exception:
      return jsonify({"error": str(exception)}), 500

    target_branch = str(request.args.get("branch") or current_branch or "").strip()
    if not target_branch:
      return jsonify({"error": "Missing target branch."}), 400
    if not _is_valid_git_branch_name(repo_path, target_branch):
      return jsonify({"error": "Invalid branch name."}), 400
    if not _remote_git_check_allowed():
      agnos_update = _base_agnos_update_status(target_branch, local_commit, "")
      agnos_update["error"] = "Remote checks are deferred until system time is valid."
      return jsonify({
        "currentBranch": current_branch,
        "targetBranch": target_branch,
        "localCommit": local_commit,
        "remoteCommit": "",
        "agnosUpdate": agnos_update,
      }), 200

    remote_commit, remote_error = _get_remote_branch_commit(repo_path, target_branch)
    if not remote_commit:
      agnos_update = _base_agnos_update_status(target_branch, local_commit, "")
      agnos_update["error"] = remote_error or f"Remote branch '{target_branch}' was not found."
    else:
      agnos_update = _build_agnos_update_status(repo_path, origin_remote, local_commit, remote_commit, target_branch)

    return jsonify({
      "currentBranch": current_branch,
      "targetBranch": target_branch,
      "localCommit": local_commit,
      "remoteCommit": remote_commit,
      "agnosUpdate": agnos_update,
    }), 200

  @app.route("/api/update/fast", methods=["POST"])
  def run_fast_update():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot run a fast update while driving."}), 409
    if version_install.read_pin(str(_get_openpilot_root())):
      return jsonify({"error": "A historical version is installed. Choose Latest in the version selector to update."}), 409

    with _fast_update_lock:
      if _fast_update_state.get("running"):
        return jsonify({"error": "Fast update already in progress."}), 409
      _fast_update_state.update({
        "running": True,
        "stage": "starting",
        "message": "Starting fast update...",
        "lastError": "",
        "startedAt": time.time(),
        "finishedAt": 0.0,
        "progressStep": 1,
        "progressTotalSteps": _FAST_UPDATE_TOTAL_STEPS,
        "progressStepPercent": 0.0,
        "progressPercent": 0.0,
        "progressLabel": "Preparing update",
        "progressDetail": "Initializing update process...",
      })

    threading.Thread(target=_fast_update_worker, daemon=True).start()

    return jsonify({
      "message": "Fast update started. Device will reboot when complete.",
      "warning": "Fast update skips backup creation and finalization safeguards.",
    }), 202

  @app.route("/api/update/branch", methods=["POST"])
  def run_branch_switch():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot switch branches while driving."}), 409

    request_data = request.get_json() or {}
    target_branch = str(request_data.get("branch") or "").strip()
    if not target_branch:
      return jsonify({"error": "Missing 'branch' in request body."}), 400

    repo_path = str(_get_openpilot_root())
    if not _is_valid_git_branch_name(repo_path, target_branch):
      return jsonify({"error": "Invalid branch name."}), 400

    with _fast_update_lock:
      if _fast_update_state.get("running"):
        return jsonify({"error": "Another update action is already in progress."}), 409
      _fast_update_state.update({
        "running": True,
        "stage": "starting",
        "message": f"Starting branch switch to '{target_branch}'...",
        "lastError": "",
        "lastBranch": target_branch,
        "lastMode": "branch-switch",
        "startedAt": time.time(),
        "finishedAt": 0.0,
        "progressStep": 1,
        "progressTotalSteps": _FAST_UPDATE_TOTAL_STEPS,
        "progressStepPercent": 0.0,
        "progressPercent": 0.0,
        "progressLabel": "Preparing branch switch",
        "progressDetail": "Initializing branch switch...",
      })

    threading.Thread(target=_branch_switch_worker, args=(target_branch,), daemon=True).start()

    return jsonify({
      "message": f"Branch switch started for '{target_branch}'. Device will reboot when complete.",
      "warning": "Fast update skips backup creation and finalization safeguards.",
    }), 202

  @app.route("/api/update/rollback", methods=["POST"])
  def run_update_rollback():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot roll back while driving."}), 409

    repo_path = str(_get_openpilot_root())
    rollback_state = _load_rollback_target(repo_path)
    target_branch = str(rollback_state.get("rollbackBranch") or "").strip()
    target_commit = str(rollback_state.get("rollbackCommit") or "").strip()
    rollback_available = bool(rollback_state.get("rollbackAvailable"))

    if not target_commit:
      return jsonify({"error": "No previous installed version has been recorded yet."}), 409
    if not target_branch:
      return jsonify({"error": "Saved rollback branch is missing."}), 409
    if not rollback_available:
      return jsonify({"error": "Current install already matches the saved previous version."}), 409

    with _fast_update_lock:
      if _fast_update_state.get("running"):
        return jsonify({"error": "Another update action is already in progress."}), 409
      _fast_update_state.update({
        "running": True,
        "stage": "starting",
        "message": f"Starting rollback to '{target_branch}'...",
        "lastError": "",
        "lastBranch": target_branch,
        "lastMode": "rollback",
        "startedAt": time.time(),
        "finishedAt": 0.0,
        "progressStep": 1,
        "progressTotalSteps": _FAST_UPDATE_TOTAL_STEPS,
        "progressStepPercent": 0.0,
        "progressPercent": 0.0,
        "progressLabel": "Preparing rollback",
        "progressDetail": "Initializing rollback...",
      })

    params.put_bool("AutomaticUpdates", False)
    threading.Thread(target=_rollback_worker, daemon=True).start()

    return jsonify({
      "message": f"Rollback started for the previous installed version on '{target_branch}'. Automatic updates were disabled and the device will reboot when complete.",
      "warning": "Rollback restores the previously installed version recorded before the last Galaxy update.",
    }), 202

  @app.route("/api/update/factory_reset", methods=["POST"])
  def run_factory_reset():
    if _safe_params_get_bool("IsOnroad"):
      return jsonify({"error": "Cannot run a factory reset while driving."}), 409

    with _fast_update_lock:
      if _fast_update_state.get("running"):
        return jsonify({"error": "Another update action is already in progress."}), 409
      _fast_update_state.update({
        "running": True,
        "stage": "starting",
        "message": "Starting factory reset...",
        "lastError": "",
        "lastBranch": "",
        "lastMode": "factory-reset",
        "startedAt": time.time(),
        "finishedAt": 0.0,
        "progressStep": 1,
        "progressTotalSteps": _FAST_UPDATE_TOTAL_STEPS,
        "progressStepPercent": 0.0,
        "progressPercent": 0.0,
        "progressLabel": "Preparing factory reset",
        "progressDetail": "Initializing factory reset...",
      })

    threading.Thread(target=_factory_reset_worker, daemon=True).start()

    return jsonify({
      "message": "Factory reset started. Device will reboot when complete.",
      "warning": "This wipes local params, backups, themes, models, maps, and route data.",
    }), 202

  @app.route("/service-worker.js", methods=["GET"])
  def sentry_service_worker():
    response = send_from_directory(app.static_folder, "service-worker.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Service-Worker-Allowed"] = "/"
    return response

  @app.route("/api/sentry/push/config", methods=["GET"])
  def sentry_push_config():
    try:
      public_key = _sentry_vapid_public_key(_get_sentry_vapid())
    except (RuntimeError, ModuleNotFoundError) as error:
      cloudlog.warning("Galaxy: Sentry Web Push dependencies unavailable: %s", error)
      return jsonify({"enabled": False, "error": "Web Push dependencies are unavailable."}), 503
    except Exception as error:
      cloudlog.exception("Galaxy: Failed to initialize Sentry Web Push: %s", error)
      return jsonify({"enabled": False, "error": f"Push notification service error: {error}"}), 500

    return jsonify({
      "enabled": True,
      "publicKey": public_key,
      "subscriptionCount": _sentry_push_subscription_count(),
    })

  @app.route("/api/sentry/push/subscribe", methods=["POST"])
  def sentry_push_subscribe():
    subscription = _normalize_sentry_push_subscription(request.get_json(silent=True))
    if subscription is None:
      return jsonify({"error": "Invalid browser push subscription."}), 400

    try:
      _get_sentry_vapid()
    except (RuntimeError, ModuleNotFoundError) as error:
      cloudlog.warning("Galaxy: Sentry Web Push dependencies unavailable: %s", error)
      return jsonify({"error": "Web Push dependencies are unavailable."}), 503
    except Exception as error:
      cloudlog.exception("Galaxy: Failed to initialize Sentry Web Push for subscription: %s", error)
      return jsonify({"error": f"Push notification service error: {error}"}), 500

    with _SENTRY_PUSH_LOCK:
      subscriptions = _load_sentry_push_subscriptions()
      subscriptions = [
        existing for existing in subscriptions
        if existing.get("endpoint") != subscription["endpoint"]
      ]
      subscriptions.append(subscription)
      _save_sentry_push_subscriptions(subscriptions)

    return jsonify({"subscribed": True, "subscriptionCount": len(subscriptions)})

  @app.route("/api/sentry/push/unsubscribe", methods=["POST"])
  def sentry_push_unsubscribe():
    payload = request.get_json(silent=True) or {}
    endpoint = str(payload.get("endpoint") or "").strip()
    if not endpoint:
      return jsonify({"error": "Missing browser push endpoint."}), 400

    with _SENTRY_PUSH_LOCK:
      subscriptions = [
        subscription for subscription in _load_sentry_push_subscriptions()
        if subscription.get("endpoint") != endpoint
      ]
      _save_sentry_push_subscriptions(subscriptions)

    return jsonify({"unsubscribed": True, "subscriptionCount": len(subscriptions)})

  @app.route("/api/sentry/push/test", methods=["POST"])
  def sentry_push_test():
    if _sentry_push_subscription_count() == 0:
      return jsonify({"error": "Enable browser notifications first."}), 409

    event = _sentry_test_notification_event()
    threading.Thread(target=_dispatch_sentry_push, args=(event,), name="galaxy-sentry-push-test", daemon=True).start()
    return jsonify({"accepted": True, "eventId": event["eventId"]}), 202

  @app.route("/api/sentry/test-notification", methods=["POST"])
  def sentry_test_notification():
    channels = _sentry_notification_channels()
    if not any(channels.values()):
      return jsonify({
        "error": "Configure browser notifications, ntfy, or a webhook before sending a test notification.",
        "channels": channels,
      }), 409

    event = _sentry_test_notification_event()
    threading.Thread(
      target=_dispatch_sentry_event,
      args=(event,),
      kwargs={"bypass_rate_limit": True},
      name="galaxy-sentry-notification-test",
      daemon=True,
    ).start()
    return jsonify({
      "accepted": True,
      "eventId": event["eventId"],
      "channels": channels,
    }), 202

  @app.route("/api/sentry/status", methods=["GET"])
  def sentry_status():
    raw_status = params.get("SentryModeStatus", encoding="utf-8") or "{}"
    try:
      status = json.loads(raw_status)
    except (TypeError, ValueError, json.JSONDecodeError):
      status = {}

    events = _sentry_event_catalog()
    last_event = events[0] if events else {}

    return jsonify({
      "enabled": params.get_bool("SentryModeEnabled"),
      "status": status if isinstance(status, dict) else {},
      "lastEvent": _public_sentry_event(last_event),
    })

  @app.route("/api/sentry/events", methods=["GET"])
  def get_sentry_events():
    return jsonify({
      "events": [_public_sentry_event(event) for event in _sentry_event_catalog()],
    })

  @app.route("/api/sentry/events/<event_id>", methods=["DELETE"])
  def delete_sentry_event(event_id):
    if not params.get_bool("IsOffroad"):
      return jsonify({"error": "Sentry events can only be deleted while parked."}), 409
    if not event_id or event_id in {".", ".."} or Path(event_id).name != event_id:
      return jsonify({"error": "Invalid Sentry event ID."}), 400

    _sentry_event_catalog()

    deleted_storage = False
    for root in _sentry_event_roots():
      directory = (root / event_id).resolve()
      if root not in directory.parents or not directory.is_dir():
        continue
      shutil.rmtree(directory)
      deleted_storage = True

    current_event = _stored_sentry_event()
    current_event_deleted = current_event is not None and current_event.get("eventId") == event_id
    with _SENTRY_EVENT_INDEX_LOCK:
      events = _load_sentry_event_catalog_unlocked()
      retained_events = [event for event in events if event.get("eventId") != event_id]
      catalog_deleted = len(retained_events) != len(events)
      if catalog_deleted:
        _save_sentry_event_catalog_unlocked(retained_events)

    if not deleted_storage and not catalog_deleted:
      return jsonify({"error": "Sentry event not found."}), 404

    if current_event_deleted:
      if retained_events:
        params.put("SentryModeLastEvent", json.dumps(retained_events[0], separators=(",", ":")))
      else:
        params.remove("SentryModeLastEvent")

    return jsonify({"deleted": True, "eventId": event_id})

  @app.route("/api/sentry/images/<event_id>/<filename>", methods=["GET"])
  def sentry_image(event_id, filename):
    image_path = _sentry_image_path(event_id, filename)
    if image_path is None:
      return jsonify({"error": "Sentry image not found."}), 404
    return send_file(image_path, mimetype="image/jpeg", max_age=0)

  @app.route("/api/sentry/live", methods=["GET"])
  def sentry_live():
    if not params.get_bool("IsOffroad"):
      return jsonify({"error": "Live Sentry view is only available while parked."}), 409

    with _SENTRY_LIVE_CAPTURE_LOCK:
      image_paths = _capture_sentry_live_images()
    if not image_paths:
      return jsonify({"error": "Unable to capture the Sentry cameras."}), 503

    captured_at = datetime.now(timezone.utc).isoformat()
    event = _public_sentry_event({
      "eventId": _SENTRY_LIVE_EVENT_ID,
      "imagePaths": image_paths,
    })
    return jsonify({"capturedAt": captured_at, "imageUrls": event["imageUrls"]})

  @app.route("/api/sentry/selfie", methods=["POST"])
  def sentry_selfie():
    if request.remote_addr not in {None, "127.0.0.1", "::1"}:
      return jsonify({"error": "Comma Selfies must originate on the device."}), 403

    with _SENTRY_LIVE_CAPTURE_LOCK:
      jpeg = _get_live_driver_jpeg()
    if jpeg is None:
      return jsonify({"error": "Unable to capture the driver camera."}), 503

    captured_at = datetime.now(timezone.utc).isoformat()
    event_id = f"selfie-{int(time.time())}-{secrets.token_hex(4)}"
    directory = _sentry_event_roots()[0] / event_id
    directory.mkdir(parents=True, exist_ok=True)
    image_path = directory / "driver.jpg"
    image_path.write_bytes(jpeg)
    event = {
      "eventId": event_id,
      "kind": "selfie",
      "detectedAt": captured_at,
      "imagePaths": [str(image_path)],
      "message": "Comma Selfie",
    }
    _record_sentry_event(event)
    params.put("SentryModeLastEvent", json.dumps(event, separators=(",", ":")))
    return jsonify({"accepted": True, "capturedAt": captured_at, "eventId": event_id}), 201

  @app.route("/api/sentry/test", methods=["POST"])
  def sentry_test():
    if request.remote_addr not in {None, "127.0.0.1", "::1"}:
      return jsonify({"error": "Sentry tests must originate on the device."}), 403
    if not params.get_bool("IsOffroad"):
      return jsonify({"error": "Sentry tests are only available while parked."}), 409

    event_id = f"test-{int(time.time())}-{secrets.token_hex(4)}"
    event = {
      "eventId": event_id,
      "kind": "alarm",
      "detectedAt": datetime.now(timezone.utc).isoformat(),
      "imagePaths": [],
      "message": "Test sentry event.",
    }

    def capture_and_publish():
      event["imagePaths"] = _capture_sentry_test_images(event_id)
      _record_sentry_event(event)
      params.put("SentryModeLastEvent", json.dumps(event, separators=(",", ":")))
      threading.Thread(
        target=_dispatch_sentry_event,
        args=(event,),
        kwargs={"bypass_rate_limit": True},
        name="galaxy-sentry-test-notify",
        daemon=True,
      ).start()

    threading.Thread(target=capture_and_publish, name="galaxy-sentry-test-capture", daemon=True).start()
    return jsonify({"accepted": True, "eventId": event_id}), 202

  @app.route("/api/sentry/events", methods=["POST"])
  def sentry_event():
    if request.remote_addr not in {None, "127.0.0.1", "::1"}:
      return jsonify({"error": "Sentry events must originate on the device."}), 403

    event = _normalize_sentry_event(request.get_json(silent=True))
    if event is None:
      return jsonify({"error": "Invalid sentry event."}), 400

    _record_sentry_event(event)
    params.put("SentryModeLastEvent", json.dumps(event, separators=(",", ":")))
    if request.args.get("blocking") == "1":
      _dispatch_sentry_event(event)
    else:
      threading.Thread(target=_dispatch_sentry_event, args=(event,), name="galaxy-sentry-notify", daemon=True).start()
    return jsonify({"accepted": True, "eventId": event["eventId"]}), 202

  # ── Galaxy pairing (mirrors settings.cc L262-282) ──────────────────
  GALAXY_DIR = _get_galaxy_dir()
  GALAXY_AUTH_FILE = GALAXY_DIR / "glxyauth"
  GALAXY_SESSION_FILE = GALAXY_DIR / "glxysession"
  GALAXY_SLUG_FILE = GALAXY_DIR / "glxyslug"

  @app.route("/api/galaxy/status", methods=["GET"])
  def galaxy_status():
    paired = len(_read_galaxy_text(GALAXY_AUTH_FILE)) == 64
    slug = _read_galaxy_text(GALAXY_SLUG_FILE)
    return jsonify({
      "paired": paired,
      "url": f"https://galaxy.firestar.link/{slug}" if slug else "",
    })

  @app.route("/api/galaxy/session", methods=["GET"])
  def galaxy_session():
    slug = _read_galaxy_text(GALAXY_SLUG_FILE)
    token = _read_galaxy_text(GALAXY_SESSION_FILE)
    paired = len(_read_galaxy_text(GALAXY_AUTH_FILE)) == 64 and bool(slug and token)
    return jsonify({
      "appUrl": GALAXY_PLAY_STORE_URL,
      "cookieName": GALAXY_COOKIE_NAME,
      "paired": paired,
      "sessionToken": _build_galaxy_session_value(slug, token),
    })

  @app.route("/api/galaxy/pair", methods=["POST"])
  def galaxy_pair():
    data = request.get_json() or {}
    password = (data.get("password") or "").strip()
    if len(password) < 6:
      return jsonify({"error": "Password must be at least 6 characters."}), 400

    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    GALAXY_DIR.mkdir(parents=True, exist_ok=True)
    GALAXY_AUTH_FILE.write_text(pw_hash)

    # Generate 256-bit secure session token
    GALAXY_SESSION_FILE.write_text(secrets.token_hex(32))

    # Generate 16-character alphanumeric routing slug
    charset = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    slug = ''.join(secrets.choice(charset) for _ in range(16))
    GALAXY_SLUG_FILE.write_text(slug)

    return jsonify({
      "message": "Pairing successful!",
      "url": f"https://galaxy.firestar.link/{slug}",
    })

  @app.route("/api/galaxy/unpair", methods=["POST"])
  def galaxy_unpair():
    for f in ["glxyauth", "glxysession", "glxyslug"]:
      file_path = GALAXY_DIR / f
      if file_path.is_file():
        file_path.unlink()
    return jsonify({"message": "Galaxy unpaired successfully."})

  @app.route("/api/tailscale/installed", methods=["GET"])
  def tailscale_installed():
    base = "/data/tailscale"
    tailscale_binary = f"{base}/tailscale"
    tailscaled_binary = f"{base}/tailscaled"

    systemd_unit = "/etc/systemd/system/tailscaled.service"

    if os.path.exists(tailscale_binary) and os.path.exists(tailscaled_binary) and os.path.exists(systemd_unit):
      return jsonify({"installed": True})

    result = subprocess.run(["which", "tailscale"], capture_output=True, text=True)
    if result.returncode == 0:
      return jsonify({"installed": True})

    return jsonify({"installed": False})

  @app.route("/api/tailscale/setup", methods=["POST"])
  def tailscale_setup():
    arch = "arm64"
    base = "/data/tailscale"

    result = subprocess.run(
      "curl -s https://pkgs.tailscale.com/stable/ | grep -oP 'tailscale_\\K[0-9]+\\.[0-9]+\\.[0-9]+' | sort -V | tail -1",
      shell=True, capture_output=True, text=True
    )

    version = result.stdout.strip() or "1.84.0"

    bin_dir = f"{base}/tailscale_{version}_{arch}"
    state = f"{base}/state"
    socket = f"{base}/tailscaled.sock"
    tgz_path = f"{base}/tailscale.tgz"

    tgz_url = f"https://pkgs.tailscale.com/stable/tailscale_{version}_{arch}.tgz"

    os.makedirs(state, exist_ok=True)

    run_cmd(["curl", "-fsSL", tgz_url, "-o", tgz_path], "Downloaded Tailscale archive.", "Failed to download Tailscale archive.")

    extract_tar(tgz_path, base)

    run_cmd(["cp", f"{bin_dir}/tailscale", f"{base}/tailscale"], "Copied tailscale binary.", "Failed to copy tailscale binary.")
    run_cmd(["cp", f"{bin_dir}/tailscaled", f"{base}/tailscaled"], "Copied tailscaled binary.", "Failed to copy tailscaled binary.")
    run_cmd(["chmod", "+x", f"{base}/tailscale", f"{base}/tailscaled"], "Made binaries executable.", "Failed to chmod binaries.")

    systemd_unit = f"""[Unit]
    Description=Tailscale node agent
    After=network.target

    [Service]
    ExecStart={base}/tailscaled \\
      --tun=userspace-networking \\
      --socks5-server=localhost:1055 \\
      --state={state}/tailscaled.state \\
      --socket={socket} \\
      --statedir={state}
    Restart=on-failure
    RestartSec=5

    [Install]
    WantedBy=multi-user.target
    """
    unit_tmp = f"{base}/tailscaled.service"
    with open(unit_tmp, "w") as f:
      f.write(systemd_unit)

    run_cmd(["sudo", "mount", "-o", "remount,rw", "/"], "Remounted / as read-write.", "Failed to remount / as read-write.")
    run_cmd(["sudo", "install", "-m", "644", unit_tmp, "/etc/systemd/system/tailscaled.service"], "Installed systemd unit.", "Failed to install systemd unit.")
    run_cmd(["sudo", "systemctl", "daemon-reload"], "Reloaded systemd daemon.", "Failed to reload systemd daemon.")
    run_cmd(["sudo", "systemctl", "enable", "/etc/systemd/system/tailscaled.service"], "Enabled tailscaled service.", "Failed to enable tailscaled service.")
    run_cmd(["sudo", "systemctl", "restart", "tailscaled"], "Started tailscaled service.", "Failed to start tailscaled service.")

    proc = subprocess.Popen(
      ["sudo", f"{base}/tailscale", "--socket", socket, "up", "--hostname", f"{HARDWARE.get_device_type()}-the-galaxy"],
      stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT,
      text=True,
      preexec_fn=os.setsid
    )

    auth_url = None
    for line in proc.stdout:
      match = re.search(r"https://login\.tailscale\.com/\S+", line)
      if match and not auth_url:
        auth_url = match.group(0)
        run_cmd(["sudo", "kill", "-TERM", f"-{proc.pid}"], "Sent SIGTERM to Tailscale setup process.", "Failed to send SIGTERM to Tailscale setup process.")
        proc.wait(timeout=5)
        break

    return jsonify({
      "message": "Tailscale setup started. Please authenticate in your browser.",
      "auth_url": auth_url
    }), 200

  @app.route("/api/tailscale/uninstall", methods=["POST"])
  def tailscale_uninstall():
    base = "/data/tailscale"
    state = f"{base}/state"
    unit_path = "/etc/systemd/system/tailscaled.service"
    local_unit = f"{base}/tailscaled.service"

    run_cmd(["sudo", "mount", "-o", "remount,rw", "/"], "Remounted / as read-write.", "Failed to remount /.")
    run_cmd(["sudo", "systemctl", "stop", "tailscaled"], "Stopped tailscaled.", "Failed to stop tailscaled.")
    run_cmd(["sudo", "systemctl", "disable", "tailscaled"], "Disabled tailscaled.", "Failed to disable tailscaled.")

    if os.path.exists(unit_path):
      run_cmd(["sudo", "rm", unit_path], "Removed systemd unit file.", "Failed to remove systemd unit file.")
      run_cmd(["sudo", "systemctl", "daemon-reload"], "Reloaded systemd daemon.", "Failed to reload systemd.")

    delete_file(local_unit)

    for filename in ["tailscale", "tailscaled", "tailscale.tgz"]:
      delete_file(os.path.join(base, filename))

    for item in os.listdir(base):
      if item.startswith("tailscale_"):
        item_path = os.path.join(base, item)
        if os.path.isdir(item_path):
          run_cmd(["sudo", "rm", "-rf", item_path], f"Removed {item_path}.", f"Failed to remove {item_path}.")

    if os.path.exists(state):
      run_cmd(["sudo", "rm", "-rf", state], "Removed tailscale state dir.", "Failed to remove tailscale state dir.")

    if os.path.exists(base):
      run_cmd(["sudo", "rm", "-rf", base], "Removed tailscale dir.", "Failed to remove tailscale dir.")

    return jsonify({"message": "Tailscale uninstalled!"}), 200

  @app.route("/api/themes", methods=["POST"])
  def save_theme_route():
    theme_path, error = utilities.create_theme(request.form, request.files)
    if error:
      return jsonify({"message": error}), 400
    return jsonify({"message": f'Theme "{request.form.get("themeName")}" saved!'}), 200

  @app.route("/api/themes/download_asset", methods=["POST"])
  def start_download_asset():
    data = request.get_json() or {}
    raw_component = (data.get("component") or "").strip()
    display_name = (data.get("name") or "").strip()
    if not raw_component or not display_name:
      return jsonify({"error": "Missing component or name"}), 400

    component = "steering_wheels" if raw_component == "steering_wheel" else ("signals" if raw_component == "turn_signals" else raw_component)
    mem_key = THEME_COMPONENT_PARAMS.get(component)
    if not mem_key:
      return jsonify({"error": "Unknown component"}), 400

    slug = display_name.lower().replace("(", "").replace(")", "").replace(" ", "_")

    params_memory.put(mem_key, slug)
    params_memory.put("ThemeDownloadProgress", "Downloading...")

    return jsonify({"message": "Download started", "component": component, "param": mem_key, "slug": slug}), 200

  @app.route("/api/themes/apply", methods=["POST"])
  def apply_theme():
    try:
      form_data = request.form.to_dict(flat=True)
      files = request.files

      if not form_data.get("themeName"):
        form_data["themeName"] = f"tmp_{secrets.token_hex(8)}"

      temp_path, error = utilities.create_theme(form_data, files, temporary=True)
      if error:
        return jsonify({"error": error}), 400

      save_checklist = json.loads(form_data.get("saveChecklist", "{}"))
      selected_theme_sources = json.loads(form_data.get("selectedThemeSources", "{}"))

      theme_param_map = {
        "colors": "ColorScheme",
        "distance_icons": "DistanceIconPack",
        "icons": "IconPack",
        "sounds": "SoundPack",
        "turn_signals": "SignalAnimation",
        "steering_wheel": "WheelIcon",
      }

      def get_selected_theme_value(asset_type):
        source = selected_theme_sources.get(asset_type)
        if not isinstance(source, dict):
          return None

        source_type = str(source.get("type") or "").strip().lower()
        source_path = str(source.get("path") or "").strip()
        if not source_path:
          return None

        if source_type == "stock":
          return "stock"
        if source_type == "stock_none":
          return "none"
        if source_path == "__stock__":
          return "stock"
        if source_path == "__stock_none__":
          return "none"
        if asset_type == "steering_wheel":
          return Path(source_path).stem
        return source_path

      for asset_type, param_key in theme_param_map.items():
        if save_checklist.get(asset_type):
          selected_value = get_selected_theme_value(asset_type)
          if selected_value is not None:
            params.put(param_key, selected_value)

      if save_checklist.get("colors") and temp_path is not None:
        asset_location = temp_path / "colors"
        save_location = ACTIVE_THEME_PATH / "colors"
        if save_location.exists() or save_location.is_symlink():
          delete_file(save_location)
        if asset_location.exists():
          save_location.parent.mkdir(parents=True, exist_ok=True)
          save_location.symlink_to(asset_location, target_is_directory=True)

      if save_checklist.get("distance_icons") and temp_path is not None:
        asset_location = temp_path / "distance_icons"
        save_location = ACTIVE_THEME_PATH / "distance_icons"
        if save_location.exists() or save_location.is_symlink():
          delete_file(save_location)
        if asset_location.exists():
          save_location.parent.mkdir(parents=True, exist_ok=True)
          save_location.symlink_to(asset_location, target_is_directory=True)

      if save_checklist.get("icons") and temp_path is not None:
        asset_location = temp_path / "icons"
        save_location = ACTIVE_THEME_PATH / "icons"
        if save_location.exists() or save_location.is_symlink():
          delete_file(save_location)
        if asset_location.exists():
          save_location.parent.mkdir(parents=True, exist_ok=True)
          save_location.symlink_to(asset_location, target_is_directory=True)

      if save_checklist.get("sounds") and temp_path is not None:
        asset_location = temp_path / "sounds"
        save_location = ACTIVE_THEME_PATH / "sounds"
        if save_location.exists() or save_location.is_symlink():
          delete_file(save_location)
        if asset_location.exists():
          save_location.parent.mkdir(parents=True, exist_ok=True)
          save_location.symlink_to(asset_location, target_is_directory=True)

      if save_checklist.get("turn_signals") and temp_path is not None:
        asset_location = temp_path / "signals"
        save_location = ACTIVE_THEME_PATH / "signals"
        if save_location.exists() or save_location.is_symlink():
          delete_file(save_location)
        if asset_location.exists():
          save_location.parent.mkdir(parents=True, exist_ok=True)
          save_location.symlink_to(asset_location, target_is_directory=True)

      wheel_location = (temp_path / "WheelIcon") if temp_path is not None else None
      wheel_save_location = ACTIVE_THEME_PATH / "steering_wheel"
      if wheel_location is not None and wheel_location.exists():
        if wheel_save_location.exists():
          delete_file(wheel_save_location)

        wheel_save_location.mkdir(parents=True, exist_ok=True)
        for file in wheel_location.iterdir():
          destination_file = wheel_save_location / file.name
          delete_file(destination_file)
          destination_file.symlink_to(file)

      params.put_bool("CustomThemes", True)
      params_memory.put_bool("UseActiveTheme", True)

      update_starpilot_toggles()
      return jsonify({"message": "Theme applied successfully!"}), 200
    except Exception as e:
      return jsonify({"error": f"Failed to apply theme: {e}"}), 500

  def _resolve_stock_theme_asset_path(asset_path):
    stock_asset_path = STOCK_THEME_PATH / asset_path
    if stock_asset_path.exists():
      return stock_asset_path

    stock_fallbacks = {
      "icons/button_home.png": STOCK_THEME_PATH.parents[2] / "selfdrive" / "assets" / "images" / "button_home.png",
      "icons/button_settings.png": STOCK_THEME_PATH.parents[2] / "selfdrive" / "assets" / "images" / "button_settings.png",
      "sounds/disengage.wav": STOCK_THEME_PATH.parents[2] / "selfdrive" / "assets" / "sounds" / "disengage.wav",
      "sounds/engage.wav": STOCK_THEME_PATH.parents[2] / "selfdrive" / "assets" / "sounds" / "engage.wav",
      "sounds/prompt.wav": STOCK_THEME_PATH.parents[2] / "selfdrive" / "assets" / "sounds" / "prompt.wav",
      # Stock openpilot has no dedicated startup clip; runtime falls back to engage.
      "sounds/startup.wav": STOCK_THEME_PATH.parents[2] / "selfdrive" / "assets" / "sounds" / "engage.wav",
      "steering_wheel/wheel.png": STOCK_THEME_PATH.parents[2] / "selfdrive" / "assets" / "icons" / "chffr_wheel.png",
    }

    fallback_path = stock_fallbacks.get(asset_path)
    if fallback_path is not None and fallback_path.exists():
      return fallback_path

    return stock_asset_path

  @app.route("/api/themes/asset/<path:theme>/<path:asset_path>")
  def get_theme_asset(theme, asset_path):
    theme_type = request.args.get("type", "")

    if theme_type == "active" or theme == "__active__":
      file_path = ACTIVE_THEME_PATH / asset_path
    elif theme_type == "stock" or theme == "__stock__":
      file_path = _resolve_stock_theme_asset_path(asset_path)
    elif asset_path.startswith("steering_wheels/"):
      file_path = THEME_SAVE_PATH / asset_path
    elif asset_path.startswith("steering_wheel/") and "holiday" in theme_type:
      file_path = HOLIDAY_THEME_PATH / theme / asset_path
    else:
      base_dir = HOLIDAY_THEME_PATH / theme if "holiday" in theme_type else THEME_SAVE_PATH / "theme_packs" / theme
      file_path = base_dir / asset_path

    if not file_path.exists():
      return "File not found", 404

    return send_file(file_path, as_attachment=False)

  @app.route("/api/themes/delete/<path:theme_path_str>", methods=["DELETE"])
  def delete_theme(theme_path_str):
    theme_type = request.args.get("type", "user")
    component = (request.args.get("component") or "").strip()

    if theme_type == "holiday":
      return jsonify({"message": "Cannot delete holiday themes."}), 403

    if theme_type == "steering_wheel":
      wheel_path = THEME_SAVE_PATH / "steering_wheels" / theme_path_str
      if wheel_path.exists():
        delete_file(wheel_path)
        return jsonify({"message": f'Steering wheel "{utilities.normalize_theme_name(wheel_path.stem)}" deleted!'}), 200
      return jsonify({"message": "Steering wheel not found..."}), 404

    theme_path = THEME_SAVE_PATH / "theme_packs" / theme_path_str
    if not theme_path.is_dir():
      return jsonify({"message": "Theme not found..."}), 404

    if component:
      allowed = {"colors", "distance_icons", "icons", "sounds", "signals"}
      if component not in allowed:
        return jsonify({"message": "Unknown component..."}), 400

      target = theme_path / component
      if not target.exists():
        return jsonify({"message": f'Component "{component}" not found in theme...'}), 404

      delete_file(target)

      return jsonify({"message": f'Removed {component.replace("_", " ")} from "{utilities.normalize_theme_name(theme_path.name)}"!'}), 200

    delete_file(theme_path)
    return jsonify({"message": f'Theme "{utilities.normalize_theme_name(theme_path.name)}" deleted!'}), 200

  @app.route("/api/themes/default", methods=["GET"])
  def get_default_theme():
    theme_data = {
      "colors": {},
      "images": {},
      "sounds": {},
      "turnSignalLength": 100,
      "turnSignalType": "Single Image",
      "sequentialImages": [],
      "theme_names": {}
    }

    if not params.get_bool("CustomThemes"):
      theme_data["theme_names"] = {
        "colors": "Stock",
        "distanceIcons": "Stock",
        "icons": "Stock",
        "sounds": "Stock",
        "turnSignals": "Stock",
        "steeringWheel": "Stock"
      }
    else:
      theme_param_map = {
        "ColorScheme": "colors",
        "DistanceIconPack": "distanceIcons",
        "IconPack": "icons",
        "SoundPack": "sounds",
        "SignalAnimation": "turnSignals",
        "WheelIcon": "steeringWheel"
      }
      for param, theme_key in theme_param_map.items():
        param_value = params.get(param, encoding="utf-8")
        if param_value:
          theme_data["theme_names"][theme_key] = utilities.normalize_theme_name(param_value)

    colors_path = ACTIVE_THEME_PATH / "colors" / "colors.json"
    if colors_path.exists():
      with open(colors_path, "r") as f:
        theme_data["colors"] = json.load(f)

    signals_dir = ACTIVE_THEME_PATH / "signals"
    if signals_dir.exists():
      sequential_files = sorted([f.name for f in signals_dir.glob("turn_signal_*.png") if "blindspot" not in f.name.lower()])
      if sequential_files:
        theme_data["sequentialImages"] = sequential_files
        theme_data["turnSignalType"] = "Sequential"

      theme_data["turnSignalStyle"] = "Traditional"
      theme_data["turnSignalLength"] = 100

      for file in os.listdir(signals_dir):
        if not any(file.endswith(ext) for ext in [".png", ".gif", ".jpg", ".jpeg"]):
          parts = file.split("_")
          if len(parts) == 2:
            theme_data["turnSignalStyle"] = parts[0].capitalize()
            try:
              theme_data["turnSignalLength"] = int(parts[1])
            except ValueError:
              pass
          break

      exts = [".png", ".gif", ".jpg", ".jpeg"]
      for ext in exts:
        p = signals_dir / f"turn_signal{ext}"
        if p.exists():
          theme_data["images"]["turnSignal"] = f"turn_signal{ext}"
          break
      for ext in exts:
        p = signals_dir / f"turn_signal_blindspot{ext}"
        if p.exists():
          theme_data["images"]["turnSignalBlindspot"] = f"turn_signal_blindspot{ext}"
          break

    icons_path = ACTIVE_THEME_PATH / "icons"
    if icons_path.exists() and icons_path.is_dir():
      for file in os.listdir(icons_path):
        if Path(file).stem == "button_settings":
          theme_data["images"]["settingsButton"] = file
        elif Path(file).stem == "button_home":
          theme_data["images"]["homeButton"] = file

    wheel_path = ACTIVE_THEME_PATH / "steering_wheel"
    if wheel_path.exists() and wheel_path.is_dir():
      wheel_files = list(wheel_path.glob("wheel.*"))
      if wheel_files:
        theme_data["images"]["steeringWheel"] = wheel_files[0].name

    distance_icons_path = ACTIVE_THEME_PATH / "distance_icons"
    if distance_icons_path.exists() and distance_icons_path.is_dir():
      theme_data["images"]["distanceIcons"] = {}
      for file in os.listdir(distance_icons_path):
        key = Path(file).stem
        if key in ["traffic", "aggressive", "standard", "relaxed"]:
          theme_data["images"]["distanceIcons"][key] = file

    sounds_path = ACTIVE_THEME_PATH / "sounds"
    if sounds_path.exists() and sounds_path.is_dir():
      valid_sound_keys = ["engage", "disengage", "prompt", "startup"]
      for file in os.listdir(sounds_path):
        stem = Path(file).stem
        if stem in valid_sound_keys:
          theme_data["sounds"][stem] = file

    return jsonify(theme_data)

  @app.route("/api/themes/download", methods=["POST"])
  def download_theme_route():
    theme_path, error = utilities.create_theme(request.form, request.files, temporary=True)
    if error:
      return jsonify({"message": error}), 400

    sane_theme_name = utilities.normalize_theme_name(request.form.get("themeName"), for_path=True)

    archive_path = shutil.make_archive(str(theme_path.parent / sane_theme_name), "zip", theme_path.parent, sane_theme_name)

    memory_file = BytesIO()
    with open(archive_path, "rb") as f:
      memory_file.write(f.read())
    memory_file.seek(0)

    delete_file(theme_path.parent)

    return send_file(memory_file, download_name=f'{sane_theme_name}.zip', as_attachment=True)

  @app.route("/api/themes/list", methods=["GET"])
  def list_themes():
    all_themes = []
    themes_path = THEME_SAVE_PATH / "theme_packs"

    if themes_path.exists():
      for theme_dir in themes_path.iterdir():
        if theme_dir.is_dir():
          is_user_created = "-user_created" in theme_dir.name
          components = utilities.check_theme_components(theme_dir)
          all_themes.append({
            "name": utilities.normalize_theme_name(theme_dir.name),
            "path": theme_dir.name,
            "type": "user" if is_user_created else "standard",
            "is_user_created": is_user_created,
            **components
          })

    if HOLIDAY_THEME_PATH.exists():
      for theme_dir in HOLIDAY_THEME_PATH.iterdir():
        if theme_dir.is_dir():
          components = utilities.check_theme_components(theme_dir)
          all_themes.append({
            "name": utilities.normalize_theme_name(theme_dir.name),
            "path": theme_dir.name,
            "type": "holiday",
            "is_user_created": False,
            **components
          })

    wheels_path = THEME_SAVE_PATH / "steering_wheels"
    if wheels_path.exists():
      for wheel_file in wheels_path.iterdir():
        all_themes.append({
          "name": utilities.normalize_theme_name(wheel_file.stem),
          "path": wheel_file.name,
          "type": "steering_wheel",
          "is_user_created": "-user_created" in wheel_file.name,
          "hasSteeringWheel": True,
        })

    return jsonify({"themes": sorted(all_themes, key=lambda x: x['name'])})

  @app.route("/api/themes/load/<path:theme_path>")
  def load_theme(theme_path):
    theme_type = request.args.get("type", "")
    if theme_type == "stock" or theme_path == "__stock__":
      theme_dir = STOCK_THEME_PATH
    else:
      theme_dir = HOLIDAY_THEME_PATH / theme_path if "holiday" in theme_type else THEME_SAVE_PATH / "theme_packs" / theme_path

    response_data = {
      "colors": None,
      "images": {},
      "sounds": {},
      "sequentialImages": [],
      "turnSignalType": "Single Image",
      "turnSignalStyle": "Static",
      "turnSignalLength": 100
    }

    colors_file = theme_dir / "colors" / "colors.json"
    if colors_file.exists():
      with open(colors_file) as f:
        response_data["colors"] = json.load(f)

    icons_dir = theme_dir / "icons"
    if icons_dir.exists():
      for base_name, response_key in [("button_home", "homeButton"), ("button_settings", "settingsButton")]:
        for ext in [".gif", ".png", ".jpg", ".jpeg"]:
          filename = f"{base_name}{ext}"
          if (icons_dir / filename).exists():
            response_data["images"][response_key] = {
              "filename": filename,
              "path": f"icons/{filename}"
            }
            break
    elif theme_type == "stock" or theme_path == "__stock__":
      for filename, response_key in [("button_home.png", "homeButton"), ("button_settings.png", "settingsButton")]:
        if _resolve_stock_theme_asset_path(f"icons/{filename}").exists():
          response_data["images"][response_key] = {
            "filename": filename,
            "path": f"icons/{filename}",
          }

    distance_dir = theme_dir / "distance_icons"
    if distance_dir.exists():
      response_data["images"]["distanceIcons"] = {}
      exts = [".png", ".gif", ".jpg", ".jpeg"]
      for name in ["aggressive", "relaxed", "standard", "traffic"]:
        for ext in exts:
          p = distance_dir / f"{name}{ext}"
          if p.exists():
            response_data["images"]["distanceIcons"][name] = {
              "filename": f"{name}{ext}",
              "path": f"distance_icons/{name}{ext}"
            }
            break

    signals_dir = theme_dir / "signals"
    if signals_dir.exists():
      sequential_files = sorted([f.name for f in signals_dir.glob("turn_signal_*.png") if "blindspot" not in f.name.lower()])
      if sequential_files:
        response_data["sequentialImages"] = sequential_files
        response_data["turnSignalType"] = "Sequential"

      response_data["turnSignalStyle"] = "Traditional"
      response_data["turnSignalLength"] = 100

      for file in os.listdir(signals_dir):
        if not any(file.endswith(ext) for ext in [".png", ".gif", ".jpg", ".jpeg"]):
          parts = file.split("_")
          if len(parts) == 2:
            response_data["turnSignalStyle"] = parts[0].capitalize()
            try:
              response_data["turnSignalLength"] = int(parts[1])
            except ValueError:
              pass
            break

      exts = [".png", ".gif", ".jpg", ".jpeg"]
      for ext in exts:
        p = signals_dir / f"turn_signal{ext}"
        if p.exists():
          response_data["images"]["turnSignal"] = {
            "filename": f"turn_signal{ext}",
            "path": f"signals/turn_signal{ext}",
          }
          break
      for ext in exts:
        p = signals_dir / f"turn_signal_blindspot{ext}"
        if p.exists():
          response_data["images"]["turnSignalBlindspot"] = {
            "filename": f"turn_signal_blindspot{ext}",
            "path": f"signals/turn_signal_blindspot{ext}",
          }
          break

    sounds_dir = theme_dir / "sounds"
    if sounds_dir.exists():
      for name in ["engage", "disengage", "startup", "prompt"]:
        file_path = sounds_dir / f"{name}.wav"
        if file_path.exists():
          response_data["sounds"][name] = {
            "filename": f"{name}.wav",
            "path": f"sounds/{name}.wav"
          }
    elif theme_type == "stock" or theme_path == "__stock__":
      for name in ["engage", "disengage", "startup", "prompt"]:
        if _resolve_stock_theme_asset_path(f"sounds/{name}.wav").exists():
          response_data["sounds"][name] = {
            "filename": f"{name}.wav",
            "path": f"sounds/{name}.wav"
          }

    steering_wheel_path = None
    if theme_type == "stock" or theme_path == "__stock__":
      if _resolve_stock_theme_asset_path("steering_wheel/wheel.png").exists():
        steering_wheel_path = "steering_wheel/wheel.png"
    elif "holiday" in theme_type:
      steering_dir = theme_dir / "steering_wheel"
      if steering_dir.exists() and steering_dir.is_dir():
        for file in steering_dir.iterdir():
          if file.is_file() and file.suffix.lower() in [".png", ".jpg", ".jpeg", ".gif"]:
            steering_wheel_path = f"steering_wheel/{file.name}"
            break
    else:
      steering_wheels_dir = THEME_SAVE_PATH / "steering_wheels"
      if steering_wheels_dir.exists():
        for file in steering_wheels_dir.iterdir():
          if file.is_file() and file.stem.lower() == theme_path.lower() and file.suffix.lower() in [".png", ".jpg", ".jpeg", ".gif"]:
            steering_wheel_path = f"steering_wheels/{file.name}"
            break

    if steering_wheel_path:
      response_data["images"]["steeringWheel"] = {
        "filename": steering_wheel_path.split("/")[-1],
        "path": steering_wheel_path
      }

    return jsonify(response_data)

  @app.route("/api/themes/submit", methods=["POST"])
  def submit_theme():
    if not GITLAB_TOKEN:
      return jsonify({"error": "Missing GitLab token"}), 500

    try:
      theme_name = request.form.get("themeName")
      if not theme_name:
        return jsonify({"error": "Missing theme name"}), 400

      discord_username = request.form.get("discordUsername") or "Unknown"

      theme_path, error = utilities.create_theme(request.form, request.files, temporary=True)
      if error:
        return jsonify({"message": error}), 400

      safe_theme_name = utilities.normalize_theme_name(theme_name, for_path=True)
      combined_name = f"{safe_theme_name}~{discord_username}"
      timestamp = int(time.time())

      def gitlab_post(project_id, endpoint, payload):
        url = f"{GITLAB_API}/projects/{project_id}/{endpoint}"
        resp = requests.post(url, headers={"PRIVATE-TOKEN": GITLAB_TOKEN}, json=payload)
        if resp.status_code not in (200, 201):
          raise RuntimeError(f"GitLab API error {resp.status_code}: {resp.text}")
        return resp.json()

      def encode_file_base64(path):
        with open(path, "rb") as f:
          return base64.b64encode(f.read()).decode("utf-8")

      def send_discord_notification(username, theme_name, asset_types):
        if not DISCORD_WEBHOOK_URL:
          return

        message = (
          f"🎨 **New Theme Submission**\n"
          f"User: `{username}`\n"
          f"Theme: `{theme_name}`\n"
          f"Assets: {', '.join(asset_types)}\n"
          f"[View Submissions Repo](https://gitlab.com/{RESOURCES_REPO}-Submissions)\n"
          f"<@263565721336807424>"
        )
        payload = {"content": message}
        try:
          resp = requests.post(DISCORD_WEBHOOK_URL, json=payload)
          if resp.status_code not in (200, 204):
            print(f"Discord notification failed: {resp.status_code} {resp.text}")
        except Exception as exception:
          print(f"Error sending Discord message: {exception}")

      asset_types = []
      submission_urls = {}

      distance_icons_path = theme_path / "distance_icons"
      if distance_icons_path.exists() and any(distance_icons_path.iterdir()):
        zip_path = shutil.make_archive(str(distance_icons_path), "zip", distance_icons_path)
        encoded = encode_file_base64(zip_path)
        file_name = f"{combined_name}.zip"
        actions = [
          {
            "action": "create",
            "file_path": file_name,
            "content": encoded,
            "encoding": "base64"
          }
        ]
        commit_payload = {
          "branch": "Distance-Icons",
          "commit_message": f"Added Distance Icons: {combined_name}",
          "actions": actions
        }
        gitlab_post(GITLAB_SUBMISSIONS_PROJECT_ID, "repository/commits", commit_payload)
        asset_types.append("Distance Icons")
        submission_urls["distance_icons"] = f"https://gitlab.com/{RESOURCES_REPO}-Submissions/-/tree/Distance-Icons"

      theme_actions = []
      for folder in ["colors", "icons", "signals", "sounds"]:
        folder_path = theme_path / folder
        if folder_path.exists() and any(folder_path.iterdir()):
          zip_path = shutil.make_archive(str(folder_path), "zip", folder_path)
          encoded = encode_file_base64(zip_path)
          file_path = f"{combined_name}/{folder}.zip"
          theme_actions.append({
            "action": "create",
            "file_path": file_path,
            "content": encoded,
            "encoding": "base64"
          })

      if theme_actions:
        commit_payload = {
          "branch": "Themes",
          "commit_message": f"Added Theme: {combined_name}",
          "actions": theme_actions
        }
        gitlab_post(GITLAB_SUBMISSIONS_PROJECT_ID, "repository/commits", commit_payload)
        asset_types.append("Theme")
        submission_urls["theme"] = f"https://gitlab.com/{RESOURCES_REPO}-Submissions/-/tree/Themes"

      wheel_file = request.files.get("steeringWheel")
      if wheel_file and wheel_file.filename:
        suffix = Path(wheel_file.filename).suffix
        file_name = f"{combined_name}{suffix}"
        wheel_file.seek(0)
        encoded_wheel = base64.b64encode(wheel_file.read()).decode("utf-8")
        actions = [
          {
            "action": "create",
            "file_path": file_name,
            "content": encoded_wheel,
            "encoding": "base64"
          }
        ]
        commit_payload = {
          "branch": "Steering-Wheels",
          "commit_message": f"Added Steering Wheel: {combined_name}",
          "actions": actions
        }
        gitlab_post(GITLAB_SUBMISSIONS_PROJECT_ID, "repository/commits", commit_payload)
        asset_types.append("Steering Wheel")
        submission_urls["steering_wheel"] = f"https://gitlab.com/{RESOURCES_REPO}-Submissions/-/tree/Steering-Wheels"

      if not submission_urls:
        return jsonify({"error": "No valid theme data or steering wheel file provided"}), 400

      send_discord_notification(discord_username, theme_name, asset_types)

      return jsonify({
        "message": "Submission successful!",
        "branches": submission_urls
      }), 200

    except Exception as exception:
      return jsonify({"error": str(exception)}), 500

    finally:
      if "theme_path" in locals() and theme_path.parent.exists():
        delete_file(theme_path.parent)

  @app.route("/api/tmux_log/capture", methods=["POST"])
  def capture_tmux_log_route():
    TMUX_LOGS_PATH.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_filename = f"tmux_log_{timestamp}.json"
    log_path = TMUX_LOGS_PATH / log_filename

    run_cmd(["tmux", "capture-pane", "-J", "-S", "-"], "Captured tmux pane.", "Failed to capture tmux pane.")

    result = subprocess.run(["tmux", "show-buffer"], capture_output=True, text=True, check=True)
    log_path.write_text(result.stdout, encoding="utf-8")

    run_cmd(["tmux", "delete-buffer"], "Deleted tmux buffer.", "Failed to delete tmux buffer.")
    return jsonify({"message": "Captured console log successfully!", "log_file": log_filename}), 200

  @app.route("/api/tmux_log/delete/<filename>", methods=["DELETE"])
  def delete_tmux_log(filename):
    file_path = TMUX_LOGS_PATH / filename
    if file_path.exists():
      delete_file(file_path)
      return jsonify({"message": f"{filename} deleted!"}), 200

    return jsonify({"error": "File not found"}), 404

  @app.route("/api/tmux_log/delete_all", methods=["DELETE"])
  def delete_all_tmux_logs():
    if TMUX_LOGS_PATH.exists():

      delete_file(TMUX_LOGS_PATH)

    TMUX_LOGS_PATH.mkdir(parents=True, exist_ok=True)
    return jsonify({"message": "All tmux logs deleted!"}), 200

  @app.route("/api/tmux_log/download/<path:filename>", methods=["GET"])
  def download_tmux_log(filename):
    return send_from_directory(str(TMUX_LOGS_PATH), filename, as_attachment=True)

  @app.route("/api/tmux_log/list", methods=["GET"])
  def list_tmux_logs():
    TMUX_LOGS_PATH.mkdir(parents=True, exist_ok=True)
    files = sorted(TMUX_LOGS_PATH.glob("*.json"), key=lambda file: file.stat().st_mtime, reverse=True)
    return jsonify([{"filename": file.name, "timestamp": file.stat().st_mtime} for file in files])

  @app.route("/api/tmux_log/live", methods=["GET"])
  def stream_tmux_log():
    if subprocess.run(["tmux", "has-session", "-t", "comma"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
      run_cmd(["tmux", "new-session", "-d", "-s", "comma", "-x", "240", "-y", "70", "bash"], "Started tmux session", "Failed to start tmux session")
    else:
      run_cmd(["tmux", "resize-window", "-t", "comma:0", "-x", "240", "-y", "70"], "Resized tmux window", "Failed to resize tmux window")

    def generate():
      last_output = ""
      last_keepalive = 0.0
      while True:
        output = subprocess.check_output(["tmux", "capture-pane", "-t", "comma:0", "-p", "-S", "-1000"], text=True)

        if output != last_output:
          yield "data: " + "\n".join(reversed(output.splitlines())).replace("\n", "\ndata: ") + "\n\n"
          last_output = output
          last_keepalive = time.monotonic()
        elif (time.monotonic() - last_keepalive) >= 5.0:
          # Keep SSE alive through proxies/tunnels even when output is unchanged.
          yield ": keepalive\n\n"
          last_keepalive = time.monotonic()

        time.sleep(0.5)
    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response

  @app.route("/api/tmux_log/snapshot", methods=["GET"])
  def snapshot_tmux_log():
    try:
      output = subprocess.check_output(["tmux", "capture-pane", "-t", "comma:0", "-p", "-S", "-1000"], text=True)
    except subprocess.CalledProcessError:
      run_cmd(["tmux", "new-session", "-d", "-s", "comma", "-x", "240", "-y", "70", "bash"], "Started tmux session", "Failed to start tmux session")
      output = subprocess.check_output(["tmux", "capture-pane", "-t", "comma:0", "-p", "-S", "-1000"], text=True)
    except Exception as e:
      return jsonify({"error": str(e)}), 500

    try:
      live_text = "\n".join(reversed(output.splitlines()))
      return jsonify({"data": live_text}), 200
    except Exception as e:
      return jsonify({"error": str(e)}), 500

  @app.route("/api/tmux_log/rename/<old>/<new>", methods=["PUT"])
  def rename_tmux_log_path_params(old, new):
    old_path = TMUX_LOGS_PATH / old
    new_safe = utilities.secure_filename(new)
    new_path = TMUX_LOGS_PATH / new_safe

    if not old_path.exists():
      return jsonify({"error": "Original file not found"}), 404

    if new_path.exists():
      return jsonify({"error": "Target file already exists"}), 400

    old_path.rename(new_path)

    return jsonify({"message": f"Renamed {old} to {new_safe}!"}), 200


  @app.route("/api/tsk_keys", methods=["DELETE"])
  def delete_secoc_key():
    name = request.args.get("name")
    keys = json.loads(params.get("SecOCKeys") or "[]")
    keys = [key for key in keys if key.get("name") != name]
    params.put("SecOCKeys", json.dumps(keys))
    return jsonify(keys)

  @app.route("/api/tsk_keys", methods=["GET"])
  def get_secoc_keys():
    return jsonify(json.loads(params.get("SecOCKeys", encoding="utf-8", default="[]")))

  @app.route("/api/tsk_keys", methods=["POST"])
  def save_secoc_keys():
    keys = request.get_json() or []
    params.put("SecOCKeys", json.dumps(keys))

    return jsonify(keys)

  @app.route("/api/tsk_key_set", methods=["POST"])
  def set_secoc_key():
    data = request.get_json()
    if not data or "value" not in data:
      return jsonify({"error": "Missing key value"}), 400

    value = data["value"]
    if not isinstance(value, str):
      return jsonify({"error": "Key value must be a string"}), 400

    params.put("SecOCKey", value)

    return "", 204

  @app.route("/api/toggles/backup", methods=["POST"])
  def backup_toggle_values():
    toggle_values = {}
    default_values = _get_static_default_param_values()
    for key in sorted(_get_toggle_backup_keys()):
      raw_value = _params_raw.get(key)
      if raw_value is None:
        raw_value = default_values.get(key)
      if raw_value is None:
        continue
      value = _sanitize_json_value(raw_value)
      if not isinstance(value, (str, int, float, bool, dict, list)):
        value = str(value)

      toggle_values[key] = value

    encoded = utilities.encode_parameters(toggle_values)
    wrapped = json.dumps({
      "format": TOGGLE_BACKUP_FORMAT,
      "version": TOGGLE_BACKUP_VERSION,
      "createdAt": datetime.now(timezone.utc).isoformat(),
      "settingsCount": len(toggle_values),
      "data": encoded,
    }, indent=2)

    buffer = BytesIO(wrapped.encode("utf-8"))
    buffer.seek(0)

    return send_file(buffer, as_attachment=True, download_name="toggle_backup.json", mimetype="application/json")

  @app.route("/api/toggles/restore", methods=["POST"])
  def restore_toggle_values():
    request_data = request.get_json(silent=True)
    if not isinstance(request_data, dict):
      return jsonify({"success": False, "message": "Invalid toggle backup file."}), 400

    backup_format = request_data.get("format")
    if backup_format not in (None, TOGGLE_BACKUP_FORMAT):
      return jsonify({"success": False, "message": "This file is not a Galaxy toggle backup."}), 400

    backup_version = request_data.get("version", 1)
    if not isinstance(backup_version, int) or backup_version > TOGGLE_BACKUP_VERSION:
      return jsonify({"success": False, "message": "This toggle backup requires a newer Galaxy version."}), 400

    encoded_data = request_data.get("data")
    if not isinstance(encoded_data, str) or not encoded_data.strip():
      return jsonify({"success": False, "message": "Toggle backup data is missing."}), 400
    if len(encoded_data.encode("utf-8")) > TOGGLE_BACKUP_MAX_ENCODED_BYTES:
      return jsonify({"success": False, "message": "Toggle backup file is too large."}), 413

    try:
      toggle_values = utilities.decode_parameters(encoded_data)
    except Exception:
      return jsonify({"success": False, "message": "Toggle backup data is damaged or invalid."}), 400
    if not isinstance(toggle_values, dict):
      return jsonify({"success": False, "message": "Toggle backup does not contain settings."}), 400

    return _restore_toggle_values(toggle_values)

  def _restore_toggle_values(toggle_values, *, profile=None):
    parked_personality_keys = {
      LEGACY_STARPILOT_PARAM_RENAMES.get(key, key)
      for key in toggle_values
      if isinstance(key, str)
    } & PERSONALITY_PARKED_PARAM_KEYS
    if parked_personality_keys and _personality_settings_write_locked():
      return jsonify({
        "success": False,
        "message": "Driving personality settings can only be restored while parked with off-road state confirmed.",
      }), 403

    allowed_keys = _get_toggle_backup_keys()
    validated_personality_values = {}
    for key, value in toggle_values.items():
      if not isinstance(key, str):
        continue
      mapped_key = LEGACY_STARPILOT_PARAM_RENAMES.get(key, key)
      if mapped_key not in allowed_keys or mapped_key not in PERSONALITY_PARKED_PARAM_KEYS:
        continue
      try:
        if mapped_key in PERSONALITY_PROFILE_ENABLE_PARAM_KEYS or mapped_key == "CustomPersonalities":
          if type(value) is not bool:
            raise ValueError(f"{mapped_key} must be a JSON boolean")
        coerced_value = _coerce_toggle_restore_value(mapped_key, value)
        if mapped_key in PERSONALITY_ADVANCED_PARAM_KEYS:
          coerced_value = validate_personality_advanced_value(coerced_value)
        elif mapped_key in PERSONALITY_FOLLOW_PARAM_KEYS:
          coerced_value = validate_personality_follow_value(coerced_value)
        validated_personality_values[key] = coerced_value
      except (TypeError, ValueError, json.JSONDecodeError):
        return jsonify({
          "success": False,
          "message": f"Invalid driving personality setting in backup: {mapped_key}.",
        }), 400

    restored_count = 0
    skipped_count = profile["skippedCount"] if profile is not None else 0
    master_restore_requested = any(
      LEGACY_STARPILOT_PARAM_RENAMES.get(key, key) == "CustomPersonalities"
      for key in validated_personality_values
    )
    master_restore_enabled = next((
      value
      for key, value in validated_personality_values.items()
      if LEGACY_STARPILOT_PARAM_RENAMES.get(key, key) == "CustomPersonalities"
    ), None)

    with _PERSONALITY_PROFILES_WRITE_LOCK:
      if (profile is not None or parked_personality_keys) and _personality_settings_write_locked():
        return jsonify({"success": False, "message": "Settings can only be restored while parked with off-road state confirmed."}), 403
      if master_restore_requested:
        ev_tuning = _get_detected_ev_tuning()
        truck_tuning = (_get_detected_truck_tuning() or params.get_bool("TruckTuning")) and not ev_tuning
        raw_document = _params_raw.get(PERSONALITY_PROFILES_PARAM)
        if not is_unconfigured_profile_document(raw_document) and strict_profile_document(raw_document) is None:
          return jsonify({
            "success": False,
            "message": "Stored longitudinal personality profiles require a verified migration before restoring the master control.",
          }), 409
        document = synchronise_profile_document_enabled(
          raw_document, master_restore_enabled, ev_tuning, truck_tuning,
        )
        if master_restore_enabled:
          if document is None:
            return jsonify({"success": False, "message": "Driving personality profiles could not be prepared for enabling."}), 500
          params.put(PERSONALITY_PROFILES_PARAM, document)
          if strict_profile_document(_params_raw.get(PERSONALITY_PROFILES_PARAM)) != document:
            return jsonify({"success": False, "message": "Driving personality profiles could not be verified after writing."}), 500
          params.put_bool("CustomPersonalities", True)
        else:
          params.put_bool("CustomPersonalities", False)
          if document is not None:
            params.put(PERSONALITY_PROFILES_PARAM, document)
        restored_count += 1

      for key, value in toggle_values.items():
        if not isinstance(key, str):
          skipped_count += 1
          continue

        mapped_key = LEGACY_STARPILOT_PARAM_RENAMES.get(key, key)
        if mapped_key not in allowed_keys:
          skipped_count += 1
          continue
        if mapped_key == "CustomPersonalities":
          continue

        try:
          restore_value = validated_personality_values.get(key, value)
          # Slot values already use the native Params type (including BYTES and TIME).
          if profile is None or mapped_key in PERSONALITY_PARKED_PARAM_KEYS:
            restore_value = _coerce_toggle_restore_value(mapped_key, restore_value)
          _params_raw.put(mapped_key, restore_value)
          restored_count += 1
        except (KeyError, TypeError, ValueError, OverflowError):
          skipped_count += 1

    if restored_count == 0:
      return jsonify({"success": False, "message": "No compatible toggle settings were found in this backup."}), 400

    update_starpilot_toggles()
    if profile is not None:
      message = f"Loaded {profile['label']} ({restored_count} settings)."
      if skipped_count:
        message += f" Skipped {skipped_count} incompatible settings."
      return jsonify({
        "success": True, "message": message,
        "slot": profile["slot"], "label": profile["label"],
        "restoredCount": restored_count, "skippedCount": skipped_count,
      })
    message = f"Restored {restored_count} toggle settings."
    if skipped_count:
      message += f" Skipped {skipped_count} incompatible or unavailable settings."
    return jsonify({
      "success": True,
      "message": message,
      "restoredCount": restored_count,
      "skippedCount": skipped_count,
    })

  @app.route("/api/toggles/profiles", methods=["GET"])
  def get_toggle_profiles():
    return jsonify({
      "slots": param_profiles.list_profiles(profile_root=TOGGLE_BACKUPS),
      "isOnroad": _safe_params_get_bool("IsOnroad"),
    })

  @app.route("/api/toggles/profiles/<slot>/save", methods=["POST"])
  def save_toggle_profile(slot):
    if _safe_params_get_bool("IsOnroad"):
      return jsonify({"success": False, "message": "Settings profiles can only be saved while parked."}), 403
    try:
      status = param_profiles.save_profile(
        _params_raw,
        slot,
        allowed_keys=_get_toggle_backup_keys(),
        profile_root=TOGGLE_BACKUPS,
      )
    except param_profiles.ParamProfileError as error:
      return jsonify({"success": False, "message": str(error)}), 400
    return jsonify({
      "success": True,
      "message": f"Saved current settings to {status['label']}.",
      "profile": status,
    })

  @app.route("/api/toggles/profiles/<slot>/load", methods=["POST"])
  def load_toggle_profile(slot):
    if _personality_settings_write_locked():
      return jsonify({"success": False, "message": "Settings profiles can only be loaded while parked with off-road state confirmed."}), 403
    try:
      profile = param_profiles.prepare_profile(
        _params_raw,
        slot,
        allowed_keys=_get_toggle_backup_keys(),
        profile_root=TOGGLE_BACKUPS,
        legacy_renames=LEGACY_STARPILOT_PARAM_RENAMES,
      )
    except param_profiles.ParamProfileError as error:
      return jsonify({"success": False, "message": str(error)}), 400

    return _restore_toggle_values(profile["settings"], profile=profile)

  @app.route("/api/toggles/reset_default", methods=["POST"])
  def reset_toggle_values():
    if _personality_settings_write_locked():
      return jsonify({
        "success": False,
        "message": "Toggles can only be reset while parked.",
      }), 403

    for raw_key in _params_raw.all_keys():
      key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
      if key in EXCLUDED_KEYS or key in ("CarMake", "CarModel", "CarModelName"):
        continue

      default_value = _params_raw.get_default_value(raw_key)
      if default_value is not None:
        _params_raw.put(raw_key, default_value)

    update_starpilot_toggles()
    HARDWARE.reboot()
    return jsonify({"success": True, "message": "Toggles reset to default StarPilot values. Rebooting..."})

  @app.route("/api/v_asm/snapshot", methods=["GET"])
  def v_asm_snapshot():
    jpeg = _get_live_driver_jpeg()
    if jpeg is not None:
      return Response(jpeg, mimetype="image/jpeg")
    return jsonify({"error": "Unable to capture live frame from driver camera."}), 503


  @app.route("/api/v_asm/config", methods=["GET"])
  def v_asm_get_config():
    return jsonify(_decode_json_object(params.get("VASMAnnotationConfig")))

  @app.route("/api/v_asm/config", methods=["POST"])
  def v_asm_save_config():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot change V-ASM configuration while driving."}), 409
    try:
      config = _normalize_vasm_config(request.get_json(silent=True))
    except ValueError as exc:
      return jsonify({"error": str(exc)}), 400

    params.put("VASMAnnotationConfig", config)
    params.put_bool("VASMEnabled", True)
    update_starpilot_toggles()
    return jsonify({"success": True, "message": "Annotation config saved. V-ASM enabled."})

  @app.route("/api/v_asm/config", methods=["DELETE"])
  def v_asm_delete_config():
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot change V-ASM configuration while driving."}), 409
    params.put_bool("VASMEnabled", False)
    params.put("VASMAnnotationConfig", {})
    update_starpilot_toggles()
    return jsonify({"success": True, "message": "Annotation config cleared. V-ASM disabled."})

  @app.route("/api/pip_preview/snapshot", methods=["GET"])
  def pip_preview_snapshot():
    if not params.get_bool("GalaxyDeveloperMode"):
      return jsonify({"error": "PiP Side Camera is available only with Galaxy Developer Mode enabled."}), 403
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Camera snapshots are unavailable while driving."}), 403
    jpeg = _get_live_driver_jpeg()
    if jpeg is not None:
      return Response(jpeg, mimetype="image/jpeg")
    return jsonify({"error": "Unable to capture live frame from driver camera."}), 503

  @app.route("/api/pip_preview/config", methods=["GET"])
  def pip_preview_get_config():
    if not params.get_bool("GalaxyDeveloperMode"):
      return jsonify({"error": "PiP Side Camera is available only with Galaxy Developer Mode enabled."}), 403
    mask = _decode_json_object(params.get("PIPPreviewMask"))
    return jsonify({"device_type": HARDWARE.get_device_type(), "mask": mask})

  @app.route("/api/pip_preview/config", methods=["POST"])
  def pip_preview_save_config():
    if not params.get_bool("GalaxyDeveloperMode"):
      return jsonify({"error": "PiP Side Camera is available only with Galaxy Developer Mode enabled."}), 403
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot change PiP Side Camera configuration while driving."}), 403
    try:
      config = _normalize_pip_preview_config(request.get_json(silent=True))
    except ValueError as exc:
      return jsonify({"error": str(exc)}), 400

    params.put("PIPPreviewMask", config)
    update_starpilot_toggles()
    return jsonify({"success": True, "message": "PiP Preview mask saved."})

  @app.route("/api/pip_preview/config", methods=["DELETE"])
  def pip_preview_delete_config():
    if not params.get_bool("GalaxyDeveloperMode"):
      return jsonify({"error": "PiP Side Camera is available only with Galaxy Developer Mode enabled."}), 403
    if params.get_bool("IsOnroad"):
      return jsonify({"error": "Cannot change PiP Side Camera configuration while driving."}), 403
    params.put("PIPPreviewMask", {})
    params.put_bool("PIPPreviewEnabled", False)
    update_starpilot_toggles()
    return jsonify({"success": True, "message": "PiP Preview mask cleared."})

  @app.route("/mapbox-help/<path:filename>", methods=["GET"])
  def serve_mapbox_help(filename):
    return send_from_directory("/data/openpilot/starpilot/navigation/navigation_training", filename)

  @app.route("/playground", methods=["GET"])
  def playground():
    return render_template("playground.html")

  @app.route("/thumbnails/<path:file_path>", methods=["GET"])
  def get_thumbnail(file_path):
    preview_path = _get_or_create_route_thumbnail(file_path)
    if preview_path is None:
      return {"error": "Thumbnail not found"}, 404

    response = send_file(
      preview_path,
      mimetype="image/png",
      conditional=True,
      max_age=ROUTE_THUMBNAIL_CACHE_SECONDS,
    )
    response.headers["Cache-Control"] = f"public, max-age={ROUTE_THUMBNAIL_CACHE_SECONDS}"
    return response

  @app.route("/video/<path>", methods=["GET"])
  def get_video(path):
    if not utilities.SEGMENT_RE.fullmatch(path or ""):
      return {"error": "Invalid segment name"}, 400

    camera = request.args.get("camera")
    filename = {"driver": "dcamera.hevc", "wide": "ecamera.hevc"}.get(camera, "fcamera.hevc")

    # qcamera.ts is a 526x330 companion to the road camera, so wrapping it costs a
    # fraction of the full stream. It still needs the mp4 wrap - a bare MPEG-TS will
    # not play in a <video>. Anything missing falls through to the full stream.
    if request.args.get("quality") == "low" and filename == "fcamera.hevc":
      for footage_path in FOOTAGE_PATHS:
        preview_path = os.path.join(footage_path, path, "qcamera.ts")
        if not os.path.isfile(preview_path):
          continue
        try:
          preview_mp4 = _get_or_create_segment_mp4(preview_path)
        except (FileNotFoundError, ValueError):
          break
        if preview_mp4 is None:
          return {"error": "Preview video is still being prepared"}, 503
        return send_file(
          preview_mp4,
          mimetype="video/mp4",
          conditional=True,
          max_age=VIDEO_CACHE_SECONDS,
        )

    for footage_path in FOOTAGE_PATHS:
      filepath = os.path.join(footage_path, path, filename)
      if os.path.exists(filepath):
        try:
          cache_path = _get_or_create_segment_mp4(filepath)
        except (FileNotFoundError, ValueError) as error:
          return {"error": str(error)}, 409
        if cache_path is None:
          return {"error": "Video is still being prepared"}, 503

        # send_file streams from disk and handles Range and ETag itself.
        return send_file(
          cache_path,
          mimetype="video/mp4",
          conditional=True,
          max_age=VIDEO_CACHE_SECONDS,
        )
    return {"error": "Video not found"}, 404

def main():
  while not _ensure_galaxy_web_deps():
    print(f"The Galaxy waiting for Flask dependency ({_GALAXY_WEB_DEPS_ERROR}); retrying in 60s.")
    time.sleep(60)

  app = Flask(__name__, static_folder="assets", static_url_path="/assets")
  setup(app)
  threading.Thread(target=_testing_ground_custom_reserved_worker, daemon=True).start()

  # Desktop-only debug mode. On-device must stay on 8082 to match Galaxy FRP routing.
  on_device = _is_comma_device_runtime()
  debug = False if on_device else os.getenv("SP_GALAXY_DEBUG", "1").lower() in {"1", "true", "yes", "on"}
  port = 8082 if on_device else int(os.getenv("SP_GALAXY_PORT", "8083"))
  host = "0.0.0.0" if on_device else os.getenv("SP_GALAXY_HOST", "0.0.0.0")
  use_reloader = False if on_device else os.getenv("SP_GALAXY_RELOAD", "0" if not debug else "1").lower() in {"1", "true", "yes", "on"}

  if debug:
    print("\"The Galaxy\" is not running on a comma device, enabling debug mode")

  app.secret_key = secrets.token_hex(32)
  app.run(host=host, port=port, debug=debug, use_reloader=use_reloader, threaded=True)

if __name__ == "__main__":
  main()
