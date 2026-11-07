"""Strict saved Long Planner health shared by the runtime and native editor."""

from __future__ import annotations

from dataclasses import dataclass
import math

from openpilot.starpilot.longitudinal.profile_document import (
  PERSONALITY_PROFILES_PARAM, default_personality_profiles, profile_document,
  is_unconfigured_profile_document, migrate_profile_document,
)
from openpilot.starpilot.saved_source import read_saved

NAMES = ("aggressive", "standard", "relaxed")
TRAFFIC_JERK_SUFFIXES = ("JerkAcceleration", "JerkDeceleration", "JerkSpeed",
                         "JerkSpeedDecrease", "JerkDanger")
TRAFFIC_NUMBER_KEYS = ("TrafficFollow", "RelaxedFollow",
                       *("Traffic" + suffix for suffix in TRAFFIC_JERK_SUFFIXES),
                       *("Relaxed" + suffix for suffix in TRAFFIC_JERK_SUFFIXES))
SUFFIXES = ("Follow", "FollowHigh", "JerkAcceleration", "JerkDeceleration",
            "JerkSpeed", "JerkSpeedDecrease", "JerkDanger")
SCALARS = {name: tuple(name.title() + suffix for suffix in SUFFIXES) for name in NAMES}
FLAGS = ()
KEYS = ("CustomPersonalities", PERSONALITY_PROFILES_PARAM, *FLAGS,
        *(key for name in NAMES for key in SCALARS[name]))
FOLLOW_MIN_SECONDS = 0.5
TRAFFIC_SAVED_MIN_SECONDS = 0.5
FOLLOW_MAX_SECONDS = 3.0
JERK_MIN_PERCENT = 25.0
JERK_MAX_PERCENT = 200.0


@dataclass(frozen=True)
class SavedValue:
  raw: bytes | None
  value: bool | float | dict | None
  valid: bool
  readable: bool


@dataclass(frozen=True)
class ProfileHealth:
  values: dict[str, SavedValue]

  def number(self, key: str) -> float:
    saved = self.values[key]
    if saved.valid and isinstance(saved.value, float):
      return saved.value
    raise ValueError(f"Invalid saved longitudinal setting: {key}")

  @property
  def document(self) -> dict | None:
    saved = self.values[PERSONALITY_PROFILES_PARAM]
    return saved.value if saved.valid and isinstance(saved.value, dict) else None

  @property
  def dependencies_valid(self) -> bool:
    return all(value.valid for key, value in self.values.items() if key != "CustomPersonalities")

  @property
  def master_on(self) -> bool:
    return self.values["CustomPersonalities"].value is True and self.values["CustomPersonalities"].valid


@dataclass(frozen=True)
class TrafficHealth:
  values: dict[str, SavedValue]
  reason: str

  @property
  def valid(self) -> bool:
    return self.reason == "valid"

  @property
  def master_on(self) -> bool:
    return self.values["CustomPersonalities"].value is True

  def number(self, key: str) -> float:
    saved = self.values[key]
    if saved.valid and isinstance(saved.value, float):
      return saved.value
    raise ValueError(f"Invalid saved Traffic setting: {key}")

  @property
  def document(self) -> dict | None:
    saved = self.values.get(PERSONALITY_PROFILES_PARAM)
    return saved.value if saved is not None and saved.valid and isinstance(saved.value, dict) else None

  @property
  def profile_enabled(self) -> bool:
    return True


def _read(params, key: str) -> tuple[bytes | None, bool]:
  limit = 65536 if key == PERSONALITY_PROFILES_PARAM else 128
  return read_saved(params, key, limit)


def _bool_value(raw: bytes | None, default: object) -> SavedValue:
  if raw is None:
    return SavedValue(None, default if isinstance(default, bool) else None, isinstance(default, bool), True)
  valid = raw in (b"0", b"1")
  return SavedValue(raw, raw == b"1" if valid else None, valid, True)


def read_master_value(params) -> SavedValue:
  raw, readable = _read(params, "CustomPersonalities")
  return (_bool_value(raw, params.get_default_value("CustomPersonalities")) if readable else
          SavedValue(raw, None, False, False))


def read_document_value(params) -> SavedValue:
  raw, readable = _read(params, PERSONALITY_PROFILES_PARAM)
  if not readable:
    return SavedValue(raw, None, False, False)
  if raw is None:
    default = params.get_default_value(PERSONALITY_PROFILES_PARAM)
    valid = default in (None, {})
    document = profile_document(default_personality_profiles(False), enabled=False) if valid else None
    return SavedValue(None, document, valid, True)
  try:
    document = migrate_profile_document(raw)
    valid = document is not None or is_unconfigured_profile_document(raw)
  except (RecursionError, ValueError, OverflowError):
    document, valid = None, False
  return SavedValue(raw, document, valid, True)


def migrate_retired_switch(params) -> bool:
  """One-time move from the retired Custom Following and Jerk switch (CustomPersonalities) to each personality's own
  Following = Custom, keeping every personality driving as before:
  switch On: a personality on StarPilot Default becomes Custom (Simple), so its saved follow and jerk still apply;
  switch Off: a following choice that never applied goes back to StarPilot Default.
  The switch is removed once done, so this runs once. Returns True when nothing is left to do."""
  import json
  from openpilot.starpilot.longitudinal.profile_document import strict_profile_document
  from openpilot.starpilot.saved_document import commit_exact
  try:
    raw, readable = _read(params, "CustomPersonalities")
    if not readable:
      return False
    if raw is None:
      return True
    saved = read_document_value(params)
    if not saved.valid:
      return False  # repair the document first; the switch stays until then
    document = saved.value if isinstance(saved.value, dict) else profile_document(default_personality_profiles(False), enabled=False)
    document = json.loads(json.dumps(document))
    master_on = raw == b"1"
    changed = False
    for profile in document["profiles"].values():
      preset = profile["following"]["preset"]
      target = ("custom_simple" if preset == "dom_default" else preset) if master_on else "dom_default"
      if target != preset:
        profile["following"]["preset"] = target
        changed = True
    if changed:
      canonical = strict_profile_document(document)
      if canonical is None:
        return False
      encoded = json.dumps(canonical, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()
      result = commit_exact(params, key=PERSONALITY_PROFILES_PARAM, max_bytes=65536, raw=encoded, expected=saved.raw,
                            authorized=lambda: _read(params, "CustomPersonalities") == (raw, True), temp_prefix=".following-switch-")
      if not (result.committed and result.verified):
        return False
    if _read(params, "CustomPersonalities") == (raw, True):
      params.remove("CustomPersonalities")
    return True
  except (OSError, RuntimeError, TypeError, ValueError, KeyError, RecursionError):
    return False


def _scalar_value(key: str, raw: bytes | None, default: object) -> SavedValue:
  try:
    number = float(raw) if raw is not None else default if isinstance(default, (int, float)) and not isinstance(default, bool) else None
    low, high = (((TRAFFIC_SAVED_MIN_SECONDS if key == "TrafficFollow" else FOLLOW_MIN_SECONDS), FOLLOW_MAX_SECONDS)
                 if "Follow" in key else
                 (JERK_MIN_PERCENT, JERK_MAX_PERCENT))
    valid = number is not None and math.isfinite(number) and low <= number <= high
  except (ValueError, OverflowError):
    number, valid = None, False
  return SavedValue(raw, float(number) if valid and number is not None else None, valid, True)


def read_profile_health(params, *, master: SavedValue | None = None) -> ProfileHealth:
  values: dict[str, SavedValue] = {}
  values["CustomPersonalities"] = master if master is not None else read_master_value(params)
  for key in KEYS:
    if key == "CustomPersonalities":
      continue
    if key == PERSONALITY_PROFILES_PARAM:
      values[key] = read_document_value(params)
      continue
    raw, readable = _read(params, key)
    if not readable:
      values[key] = SavedValue(raw, None, False, False)
      continue
    if raw is None:
      default = params.get_default_value(key)
      if key in FLAGS:
        values[key] = _bool_value(None, default)
      else:
        values[key] = _scalar_value(key, None, default)
      continue
    if key in FLAGS:
      values[key] = _bool_value(raw, None)
    else:
      values[key] = _scalar_value(key, raw, None)
  return ProfileHealth(values)


def read_traffic_health(params) -> TrafficHealth:
  """Read only Traffic dependencies; unrelated profile corruption stays local."""
  master = read_master_value(params)
  values = {"CustomPersonalities": master}
  if not master.valid:
    return TrafficHealth(values, "invalid_master")
  # Traffic's Following choice (in the document) now decides whether its saved values apply; always read both.
  values[PERSONALITY_PROFILES_PARAM] = read_document_value(params)
  for key in TRAFFIC_NUMBER_KEYS:
    raw, readable = _read(params, key)
    values[key] = (_scalar_value(key, raw, params.get_default_value(key))
                   if readable else SavedValue(raw, None, False, False))
  bad = next((key for key, value in values.items() if not value.valid), None)
  if bad is not None:
    return TrafficHealth(values, f"invalid_{bad}")
  health = TrafficHealth(values, "valid")
  return health
