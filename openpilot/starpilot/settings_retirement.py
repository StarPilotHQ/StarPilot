"""Retire redundant switches once, preserving their effective saved behavior."""

import base64
import json
import hashlib
import math

from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.longitudinal.profile_document import (
  PERSONALITY_PROFILES_PARAM, STOCK_JERK, category_curve, default_personality_profiles, migrate_profile_document,
  is_unconfigured_profile_document, profile_document, strict_profile_document,
)
from openpilot.starpilot.longitudinal.profile_preferences import SCALARS, TRAFFIC_JERK_SUFFIXES
from openpilot.starpilot.ui.appearance_preferences import RETIRED_DISPLAY_KEYS, read_visibility
from openpilot.starpilot.ui.onroad_customization import (
  MAX_BYTES as LAYOUT_MAX_BYTES, PARAM_KEY as LAYOUT_KEY, STOPPED_TIMER_WIDGET, decode_document, default_document, validate_document,
)

STATE_KEY = "SettingsRetirementState"
EVIDENCE_KEY = "RetiredSettingsEvidence"
PROFILE_FLAGS = tuple(name.title() + "PersonalityProfile" for name in ("aggressive", "standard", "relaxed", "traffic"))
GROUPS = ("cruise", "profiles", "display", "following")
# The Custom Following and Jerk switch, folded into each personality's Following choice.
FOLLOWING_SWITCH = "CustomPersonalities"
FOLLOWING_JERK_KEYS = {
  **{name: SCALARS[name][2:] for name in SCALARS},
  "traffic": tuple(prefix + suffix for prefix in ("Traffic", "Relaxed") for suffix in TRAFFIC_JERK_SUFFIXES),
}


def retire_settings(params):
  """Run parked after state admission and before default creation/consumers.

  Write child reconciliation before retiring each parent. Corrupt switches are
  archived in an optional document and treated as Off; this never enables a
  dormant feature. Returns concrete reconciliation issues for manager logging.
  """
  issues = []
  evidence = {}
  evidence_writable = True
  raw, readable = read_saved(params, EVIDENCE_KEY, 65536)
  if not readable:
    evidence_writable = False
    issues.append("Unreadable retired switch evidence retained")
  elif raw is not None:
    try:
      prior = json.loads(raw)
      valid = (isinstance(prior, dict) and len(prior) <= 6 and
               all(key in (*PROFILE_FLAGS, "QOLLongitudinal", "AdvancedLateralTune") and isinstance(value, dict) and
                   set(value) <= {"rawBase64", "reason", "sha256"} and
                   (value.get("rawBase64") is None or isinstance(value.get("rawBase64"), str) and len(value["rawBase64"]) <= 2048) and
                   isinstance(value.get("reason"), str) and len(value["reason"]) <= 512
                   for key, value in prior.items()))
      if valid:
        evidence = prior
      else:
        evidence_writable = False
        issues.append("Invalid retired switch evidence retained")
    except (ValueError, UnicodeError, RecursionError):
      evidence_writable = False
      issues.append("Invalid retired switch evidence retained")

  def archive(key, raw, reason, readable=True):
    item = {"rawBase64": base64.b64encode(raw).decode() if readable and raw is not None else None,
            "reason": reason + ("; original file could not be read safely" if not readable else "")}
    if readable and raw is not None:
      item["sha256"] = hashlib.sha256(raw).hexdigest()
    amended = {**evidence, key: item}
    if evidence_writable and len(json.dumps(amended).encode()) <= 65536:
      params.put(EVIDENCE_KEY, amended, block=True)
      evidence.update(amended)
    else:
      issues.append(f"{key}: new retirement evidence not saved; existing evidence retained")
    issues.append(reason)

  state_raw, state_readable = read_saved(params, STATE_KEY, 256)
  try:
    state = json.loads(state_raw) if state_readable and state_raw is not None else {"version": 1, "completed": []}
    state_valid = (isinstance(state, dict) and set(state) == {"version", "completed"} and state["version"] == 1 and
                   isinstance(state["completed"], list) and len(state["completed"]) <= len(GROUPS) and
                   all(group in GROUPS for group in state["completed"]))
  except (ValueError, UnicodeError):
    state, state_valid = {"version": 1, "completed": []}, False
  if not state_readable or not state_valid:
    issues.append("Invalid settings retirement completion state; safe first-migration reconciliation used")
    state = {"version": 1, "completed": []}
  completed_raw = state["completed"]
  assert isinstance(completed_raw, list)
  completed = set(completed_raw)
  def finish(group):
    completed.add(group)
    # Separate from raw evidence, so corrupt older evidence cannot prevent a
    # completed migration from preserving the user's subsequent edits.
    params.put(STATE_KEY, {"version": 1, "completed": sorted(completed)}, block=True)

  if "cruise" not in completed:
    master, readable = read_saved(params, "QOLLongitudinal", 8)
    if not readable or master not in (None, b"0", b"1"):
      archive("QOLLongitudinal", master, "Invalid cruise switch; inactive cruise features restored to defaults", readable)
    if master != b"1":
      for key in ("ForceStops", "ReverseCruise"):
        params.put_bool(key, False, block=True)
      for key, value in (("CustomCruise", 1.0), ("CustomCruiseLong", 5.0)):
        params.put(key, value, block=True)
    params.remove("QOLLongitudinal")
    finish("cruise")

  if "profiles" not in completed:
    flags = {}
    for key in PROFILE_FLAGS:
      raw, readable = read_saved(params, key, 8)
      if not readable or raw not in (None, b"0", b"1"):
        archive(key, raw, "Invalid personality curve switch; its legacy curves disabled", readable)
        flags[key] = b"0"
      else:
        flags[key] = raw if raw is not None else b"0"
    raw, readable = read_saved(params, PERSONALITY_PROFILES_PARAM, 65536)
    try:
      document = migrate_profile_document(raw) if readable and raw is not None else None
    except (ValueError, TypeError, UnicodeError, RecursionError):
      document = None
    if not readable or raw is not None and document is None and not is_unconfigured_profile_document(raw):
      issues.append("Invalid personality curves retained; supplied profiles remain in use")
    if document is not None:
      for key, enabled in flags.items():
        if enabled != b"0":
          continue
        name = key.removesuffix("PersonalityProfile").lower()
        profile = document["profiles"][name]
        profile["following"] = {"preset": "dom_default", "curve": []}
        for category in ("acceleration", "braking"):
          if profile[category].get("legacyActivation", False):
            profile[category] = {"preset": "selected_profile", "curve": []}
      params.put(PERSONALITY_PROFILES_PARAM, document, block=True)
    for key in PROFILE_FLAGS:
      params.remove(key)
    finish("profiles")

  if "following" not in completed:
    if _retire_following_switch(params, issues, archive):
      finish("following")

  if "display" not in completed:
    if _retire_display_switches(params, issues):
      finish("display")
  params.remove("AdvancedLateralTune")
  return tuple(issues)


def _retire_display_switches(params, issues):
  """Fold widget visibility switches into the onroad layout; the layout becomes their only switch.

  Unreadable or corrupt switches keep the visible baseline, matching how the renderer treated them.
  Returns False, leaving every switch in place, when the saved layout file cannot be read.
  """
  raw, readable = read_saved(params, LAYOUT_KEY, LAYOUT_MAX_BYTES)
  if not readable:
    issues.append("Unreadable onroad layout; display switches retained until the next startup")
    return False
  try:
    document = decode_document(raw) if raw is not None else default_document()
  except (ValueError, TypeError, UnicodeError, RecursionError):
    # Possibly a newer branch's document; never overwrite it.
    issues.append("Invalid onroad layout kept; display switches were not carried over")
    document = None
  if document is None:
    return False
  saved = {key: read_visibility(params, key) for key in RETIRED_DISPLAY_KEYS}
  def chosen(key, value):
    return saved[key].readable and saved[key].value is value
  before = json.dumps(document, sort_keys=True)
  layouts = document["layouts"]
  # Speed and wheel switches only ever applied to the large UI.
  if chosen("HideSpeed", True):
    layouts["large"]["current_speed"]["enabled"] = False
  if chosen("HideSteeringWheel", True):
    layouts["large"]["steering_wheel"]["enabled"] = False
  if chosen("HideDMIcon", True):
    for profile in layouts:
      layouts[profile]["driver_monitor"]["enabled"] = False
  if chosen("EnableTorqueBarWidget", False):
    for profile in layouts:
      layouts[profile]["torque_bar"]["enabled"] = False
  # Only the large cruise card ever hid its MAX line.
  if chosen("HideMaxSpeed", True):
    document["showMaxSpeed"] = False
  if chosen("StoppedTimer", True):
    layouts["compact"][STOPPED_TIMER_WIDGET]["enabled"] = True
  if chosen("RainbowPath", True):
    for colors in document["roadColors"].values():
      if colors.get("pathMode", "default") == "default":
        colors["pathMode"] = "rainbow"
  if json.dumps(document, sort_keys=True) != before:
    params.put(LAYOUT_KEY, validate_document(document), block=True)
  for key in RETIRED_DISPLAY_KEYS:
    params.remove(key)
  return True


def _saved_jerk_is_stock(params, name):
  """Whether the personality's saved smoothness equals openpilot's own, so a named curve drives the same without it."""
  for key in FOLLOWING_JERK_KEYS[name]:
    raw, readable = read_saved(params, key, 128)
    if not readable:
      return False
    try:
      percent = float(raw) if raw is not None else 100.0
    except (TypeError, ValueError):
      return False
    if not math.isfinite(percent) or not math.isclose(percent / 100.0, STOCK_JERK[name]):
      return False
  return True


def _retire_following_switch(params, issues, archive):
  """Fold the Custom Following and Jerk switch into each personality's Following choice, keeping what applied.

  Switch On: a personality drove on its saved follow and smoothness values, so StarPilot Default becomes Custom
  (Simple). A named curve that applied (curves enabled) keeps its name when the saved smoothness is stock and
  otherwise becomes Custom (Advanced) with the same points, so the smoothness still applies; a curve that did not
  apply (curves disabled) becomes Custom (Simple). Switch Off or absent: no saved following choice applied, so every
  Following returns to StarPilot Default. Returns False, leaving the switch in place, when the document cannot be read.
  """
  raw, readable = read_saved(params, FOLLOWING_SWITCH, 8)
  if not readable:
    issues.append("Unreadable following switch; retained until the next startup")
    return False
  if raw not in (None, b"0", b"1"):
    archive(FOLLOWING_SWITCH, raw, "Invalid following switch; treated as Off", readable)
  master_on = raw == b"1"
  raw_document, readable = read_saved(params, PERSONALITY_PROFILES_PARAM, 65536)
  if not readable:
    issues.append("Unreadable personality curves; following switch retained until the next startup")
    return False
  try:
    document = migrate_profile_document(raw_document) if raw_document is not None else None
  except (ValueError, TypeError, UnicodeError, RecursionError):
    document = None
  if raw_document is not None and document is None and not is_unconfigured_profile_document(raw_document):
    issues.append("Invalid personality curves retained; following switch retained until they are repaired")
    return False
  if document is None:
    document = profile_document(default_personality_profiles(False), enabled=False)
  changed = False
  for name, profile in document["profiles"].items():
    following = profile["following"]
    preset = following["preset"]
    if not master_on:
      target = {"preset": "dom_default", "curve": following["curve"]}
    elif preset == "custom":
      target = following if document["enabled"] else {"preset": "custom_simple", "curve": following["curve"]}
    elif preset in ("custom_simple", "dom_default") or not document["enabled"]:
      target = {"preset": "custom_simple", "curve": following["curve"]}
    elif _saved_jerk_is_stock(params, name):
      target = following
    else:
      target = {"preset": "custom", "curve": category_curve("following", following, False)}
    if target != following:
      profile["following"] = target
      changed = True
  if changed:
    canonical = strict_profile_document(document)
    if canonical is None:
      issues.append("Personality curves could not be carried over; following switch retained")
      return False
    params.put(PERSONALITY_PROFILES_PARAM, canonical, block=True)
  params.remove(FOLLOWING_SWITCH)
  return True
