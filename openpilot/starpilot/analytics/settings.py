"""Read-only scalar settings, resolved by the same owners as the settings UI."""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable

MAX_SETTINGS = 512
MAX_BYTES = 128 * 1024
MAX_FIELDS = 2048
MAX_ENUM = 80
KEY = re.compile(r"[A-Za-z][A-Za-z0-9_:/.-]{0,95}\Z")
NUMBER = re.compile(r"-?(?:\d+(?:\.\d*)?|\.\d+)\Z")
EXCLUDED_KEYS = frozenset(("SoundPack", "torque_prepare_firestar"))
EXCLUDED_PREFIXES = ("pip:mask:", "pip:format:")
COMMAND = re.compile(r"(?:^|[:_])(?:reset|repair|adopt|editor|annotation|auto)(?:_|:|$)")
Scalar = bool | float | str


class _ReadOnlyParams:
  def __init__(self, params):
    self._params = params

  def __getattr__(self, name):
    if name not in ("get", "get_bool", "get_int", "get_float", "get_type", "get_default_value", "get_param_path", "get_params_path"):
      raise AttributeError(f"Analytics cannot use Params.{name}")
    return getattr(self._params, name)


def _number(value, low, high):
  if not isinstance(value, str) or NUMBER.fullmatch(value) is None:
    return None
  number = float(value)
  return number if math.isfinite(number) and low <= number <= high else None


def _value(row, value):
  if not isinstance(value, str) or len(value) > MAX_ENUM:
    return None
  if row.choices == ("Off", "On") or not row.choices and row.default_value in ("Off", "On"):
    return {"Off": False, "On": True}.get(value)
  if (math.isfinite(row.minimum) and math.isfinite(row.maximum) and row.maximum > row.minimum and
      (row.step > 0 or row.default_value is not None)):
    numeric = _number(value, row.minimum, row.maximum)
    if numeric is not None:
      return numeric
  if isinstance(value, str) and value in row.choices and len(value) <= MAX_ENUM:
    return value
  return None


def _eligible(row):
  return (isinstance(row.key, str) and KEY.fullmatch(row.key) is not None and
          not row.page and row.key not in EXCLUDED_KEYS and COMMAND.search(row.key) is None and
          not row.key.startswith(EXCLUDED_PREFIXES) and
          not row.actions and bool(row.choices or row.default_value in ("Off", "On") or row.step > 0 or
                                  row.default_value is not None and row.maximum > row.minimum))


def _fields(rows: Iterable[tuple[str, object]]) -> dict[str, Scalar]:
  records = {}
  identities = {}
  collisions = set()
  for page, row in rows:
    if not _eligible(row):
      continue
    identity = (page, row.key)
    suffix = re.sub(r"[^A-Za-z0-9_]", "_", f"{page}_{row.key}")
    if suffix in identities and identities[suffix] != identity:
      collisions.add(suffix)
      continue
    identities[suffix] = identity
    value, default = _value(row, row.value), _value(row, row.default_value)
    fields = {f"valid_{suffix}": value is not None, f"default_known_{suffix}": default is not None}
    if value is not None:
      fields[f"setting_{suffix}"] = value
    if default is not None:
      fields[f"default_{suffix}"] = default
    records[suffix] = fields
  result: dict[str, Scalar] = {}
  truncated = False
  count = 0
  for suffix in sorted(records):
    fields = {f"valid_{suffix}": False, f"default_known_{suffix}": False} if suffix in collisions else records[suffix]
    candidate = {**result, **fields}
    if (count >= MAX_SETTINGS or len(candidate) > MAX_FIELDS or
        len(json.dumps(candidate, allow_nan=False).encode()) > MAX_BYTES - 128):
      truncated = True
      break
    result = candidate
    count += 1
  return {**result, "settings_schema_version": 1.0, "settings_count": float(count), "settings_truncated": truncated}


def _owner_rows(params, CP, profile):
  from openpilot.common.hardware import HARDWARE
  from openpilot.starpilot.galaxy.settings import PAGES, AuthorityContext, _qualified
  from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
  from openpilot.starpilot.ui.display_owner import DisplayOwner
  from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
  from openpilot.starpilot.ui.feature_settings_state import FeaturePage
  from openpilot.starpilot.ui.presentation import Profile
  from openpilot.starpilot.ui.pip_owner import PiPOwner
  from openpilot.starpilot.ui.sentry_owner import SentryOwner
  from openpilot.starpilot.ui.sounds_owner import SoundsOwner
  from openpilot.starpilot.ui.vasm_owner import VASMOwner

  params = _ReadOnlyParams(params)
  parked = params.get("IsOffroad") is True
  profile = Profile(profile) if profile is not None else Profile.COMPACT if HARDWARE.get_device_type() == "mici" else Profile.LARGE
  context = AuthorityContext(parked, CP, None)
  owner = FeatureSettingsOwner(params, lambda group: _qualified(context, group, params=params),
                               vehicle_fingerprint=lambda: str(CP.carFingerprint) if CP is not None else None,
                               vehicle_params=lambda: CP, show_cruise_intervals=True)
  system_long = bool(CP is not None and CP.openpilotLongitudinalControl)
  lateral_context = _qualified(context, "lane")
  separate = {"hub", "appearance", "display", "sounds", "pip", "sentry", "vasm"}
  pages = sorted(({str(page) for page in FeaturePage} | PAGES) - separate)
  for page in pages:
    state = owner.snapshot(page, parked=parked, system_long=system_long, lateral_context=lateral_context, metric=False)
    yield from ((page, row) for row in state.rows)
  for state in (AppearanceOwner(params, lambda: parked).snapshot(profile),
                DisplayOwner(params, lambda: parked).snapshot(profile), SoundsOwner(params, lambda: parked).snapshot(),
                PiPOwner(params, lambda: parked, lambda: False).snapshot(), SentryOwner(params, lambda: parked).snapshot(),
                VASMOwner(params, lambda: parked).snapshot()):
    yield from ((state.page, row) for row in state.rows)


def collect_settings(params, CP, *, profile=None) -> dict[str, Scalar]:
  """Saved/default choices only; no live actuation, source blobs, or mutations."""
  return _fields(_owner_rows(params, CP, profile))
