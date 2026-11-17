"""Versioned, fail-closed longitudinal acceleration/braking profiles."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import numbers

PERSONALITY_PROFILES_PARAM = "LongitudinalPersonalityProfiles"
PROFILE_SCHEMA_VERSION = 6
MAX_EXTRA_STOPPED_GAP_M = 3.048
GLOBAL_BRAKING_RESPONSES = ("standard", "eco", "sport")
DEFAULT_DECELERATION_PROFILE = "eco"
PERSONALITY_IDS = ("traffic", "aggressive", "standard", "relaxed")
TRUCK_FINGERPRINT_TOKENS = (
  " RAM 1500 ",
  " RAM HD ",
  " F 150 ",
  " MAVERICK ",
  " RANGER ",
  " SILVERADO ",
  " RIDGELINE ",
  " SANTA CRUZ ",
)

ACCELERATION_SPEEDS_MPH = tuple(range(0, 91, 10))
BRAKING_SPEEDS_MPH = ACCELERATION_SPEEDS_MPH
FOLLOWING_SPEEDS_MPH = ACCELERATION_SPEEDS_MPH
_NATIVE_ACCELERATION_SPEEDS_MS = (0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 40.0)
_V1_ACCELERATION_SPEEDS_MPH = (0.0, 11.184681, 22.369363, 33.554044, 44.738726, 55.923407, 89.477452)


def is_truck_fingerprint(fingerprint: object) -> bool:
  if not isinstance(fingerprint, str) or not fingerprint.strip():
    return False
  normalized = f" {fingerprint.strip().upper().replace('_', ' ').replace('-', ' ')} "
  return any(token in normalized for token in TRUCK_FINGERPRINT_TOKENS)

ACCELERATION_PRESETS = ("selected_profile", "dom_default", "standard", "eco", "sport", "sport_plus", "custom")
BRAKING_PRESETS = ("selected_profile", "dom_default", "standard", "eco", "sport", "custom")
_LEGACY_FOLLOWING_PRESETS = ("close", "medium", "far")
FOLLOWING_PRESETS = ("dom_default", "close", "medium", "far", "traffic", "custom", "legacy_close", "legacy_medium", "legacy_far")
CURVE_BOUNDS = {
  "acceleration": (0.0, 3.5),
  "braking": (0.5, 2.0),
  "following": (0.5, 3.0),
}
_V2_CURVE_BOUNDS = {
  "acceleration": (0.0, 6.0),
  "braking": (0.5, 2.0),
  "following": (0.75, 3.0),
}
_V1_CURVE_BOUNDS = dict(_V2_CURVE_BOUNDS)
# Dom's Traffic default is below the point editor's minimum. Keep it losslessly
# after initialization/reset, while applying CURVE_BOUNDS to newly edited points.
_V3_CURVE_BOUNDS = {**_V2_CURVE_BOUNDS, "braking": (0.35, 2.0), "following": (0.5, 3.0)}
PERSONALITY_ADVANCED_PARAM_KEYS = frozenset(
  f"{profile}{suffix}"
  for profile in ("Traffic", "Aggressive", "Standard", "Relaxed")
  for suffix in ("JerkAcceleration", "JerkDeceleration", "JerkDanger", "JerkSpeedDecrease", "JerkSpeed")
)
PERSONALITY_FOLLOW_PARAM_KEYS = frozenset({
  "TrafficFollow",
  "AggressiveFollow", "AggressiveFollowHigh",
  "StandardFollow", "StandardFollowHigh",
  "RelaxedFollow", "RelaxedFollowHigh",
})
PERSONALITY_PROFILE_ENABLE_RUNTIME_KEYS = (
  ("traffic_personality_profile", "TrafficPersonalityProfile"),
  ("aggressive_personality_profile", "AggressivePersonalityProfile"),
  ("standard_personality_profile", "StandardPersonalityProfile"),
  ("relaxed_personality_profile", "RelaxedPersonalityProfile"),
)
PERSONALITY_PROFILE_ENABLE_PARAM_KEYS = frozenset(
  param_key for _runtime_key, param_key in PERSONALITY_PROFILE_ENABLE_RUNTIME_KEYS
)
PERSONALITY_PARKED_PARAM_KEYS = (
  PERSONALITY_ADVANCED_PARAM_KEYS
  | PERSONALITY_FOLLOW_PARAM_KEYS
  | PERSONALITY_PROFILE_ENABLE_PARAM_KEYS
  | {"CustomPersonalities"}
)


def load_personality_profile_enable_values(get_value) -> dict[str, bool]:
  return {
    runtime_key: get_value(param_key)
    for runtime_key, param_key in PERSONALITY_PROFILE_ENABLE_RUNTIME_KEYS
  }


def validate_personality_follow_value(raw_value) -> float:
  if not isinstance(raw_value, numbers.Real) or isinstance(raw_value, bool):
    raise ValueError("Following values must be JSON numbers.")
  value = float(raw_value)
  if not math.isfinite(value) or value < 0.5 or value > 3.0:
    raise ValueError("Following values must be between 0.5 and 3.0.")
  return round(value, 4)


def validate_personality_advanced_value(raw_value) -> float:
  if not isinstance(raw_value, numbers.Real) or isinstance(raw_value, bool):
    raise ValueError("Advanced personality values must be JSON numbers.")
  value = float(raw_value)
  if not math.isfinite(value) or value < 25.0 or value > 200.0:
    raise ValueError("Advanced personality values must be between 25 and 200.")
  return round(value, 4)


_CATEGORY_SPECS = {
  "acceleration": (ACCELERATION_PRESETS, len(ACCELERATION_SPEEDS_MPH)),
  "braking": (BRAKING_PRESETS, len(BRAKING_SPEEDS_MPH)),
  "following": (FOLLOWING_PRESETS, len(FOLLOWING_SPEEDS_MPH)),
}

_BRAKING_PRESET_CURVES = {
  "eco": (0.5,) * len(BRAKING_SPEEDS_MPH),
  "standard": (1.0,) * len(BRAKING_SPEEDS_MPH),
  "sport": (2.0,) * len(BRAKING_SPEEDS_MPH),
}
# Named following presets use Dom's native speed breakpoints. Custom retains
# its editable 10 mph grid; sampling named presets onto that grid is only for
# first-use Custom conversion and graph previews, not runtime interpolation.
FOLLOWING_PRESET_CURVES = {
  "close": (1.25, 1.0),
  "medium": (1.45, 1.2),
  "far": (1.6, 1.4),
  "traffic": (0.75, 1.6),
  "legacy_close": (1.25, 1.25),
  "legacy_medium": (1.45, 1.45),
  "legacy_far": (1.75, 1.75),
}
_FOLLOWING_PRESET_SPEEDS_MPH = {
  preset: (0.0, 25.0 / 0.44704) if preset == "traffic" else (45.0, 70.0)
  for preset in FOLLOWING_PRESET_CURVES
}
PROFILE_AXES = {
  "acceleration": {
    "speed": {"unit": "mph", "values": list(ACCELERATION_SPEEDS_MPH)},
    "value": {"unit": "m/s^2", "meaning": "maximum_requested_acceleration"},
  },
  "braking": {
    "speed": {"unit": "mph", "values": list(BRAKING_SPEEDS_MPH)},
    "value": {"unit": "m/s^2", "meaning": "cruise_slc_deceleration_magnitude"},
  },
  "following": {
    "speed": {"unit": "mph", "values": list(FOLLOWING_SPEEDS_MPH)},
    "value": {"unit": "s", "meaning": "base_time_headway"},
  },
}
_CATEGORY_SPEEDS_MPH = {
  "acceleration": ACCELERATION_SPEEDS_MPH,
  "braking": BRAKING_SPEEDS_MPH,
  "following": FOLLOWING_SPEEDS_MPH,
}

_ACCELERATION_PROFILE_IDS = {
  "standard": 0,
  "eco": 1,
  "sport": 2,
  "sport_plus": 3,
}

_PERSONALITY_REFERENCE_PRESETS = {
  "traffic": {"acceleration": "eco", "braking": "standard", "following": "traffic"},
  "aggressive": {"acceleration": "sport_plus", "braking": "sport", "following": "close"},
  "standard": {"acceleration": "standard", "braking": "standard", "following": "medium"},
  "relaxed": {"acceleration": "eco", "braking": "eco", "following": "far"},
}

_V1_PROFILE_AXES = {
  "acceleration": {
    "speed": {"unit": "mph", "values": list(_V1_ACCELERATION_SPEEDS_MPH)},
    "value": {"unit": "m/s^2", "meaning": "maximum_requested_acceleration"},
  },
  "braking": {
    "speed": {"unit": "mph", "values": list(_V1_ACCELERATION_SPEEDS_MPH)},
    "value": {"unit": "m/s^2", "meaning": "cruise_slc_deceleration_magnitude"},
  },
  "following": {
    "speed": {"unit": "mph", "values": list(FOLLOWING_SPEEDS_MPH)},
    "value": {"unit": "s", "meaning": "base_time_headway"},
  },
}


def _acceleration_preset_curve(preset: str, ev_tuning: bool, truck_tuning: bool) -> list[float]:
  from openpilot.starpilot.longitudinal.accel_profile import get_accel_profile_curve_values

  return get_accel_profile_curve_values(
    _ACCELERATION_PROFILE_IDS[preset], bool(ev_tuning), bool(truck_tuning) and not bool(ev_tuning)
  )


def default_personality_profiles(ev_tuning: bool, truck_tuning: bool = False) -> dict[str, dict]:
  del ev_tuning, truck_tuning
  return {
    personality: {
      "acceleration": {"preset": "selected_profile", "curve": []},
      "braking": {"preset": "selected_profile", "curve": []},
      "following": {"preset": "dom_default", "curve": []},
    }
    for personality in PERSONALITY_IDS
  }


def profile_document(profiles: dict[str, dict], *, enabled: bool, global_braking_response: str = DEFAULT_DECELERATION_PROFILE,
                     selected_acceleration_profile: str = "dom_default", selected_deceleration_profile: str | None = None,
                     extra_stopped_gap_m: float = 0.0) -> dict:
  if type(enabled) is not bool:
    raise ValueError("enabled must be a JSON boolean")
  if global_braking_response not in GLOBAL_BRAKING_RESPONSES:
    raise ValueError("invalid global braking response")
  if selected_deceleration_profile is None:
    selected_deceleration_profile = global_braking_response
  if selected_acceleration_profile not in ("dom_default", "standard", "eco", "sport", "sport_plus"):
    raise ValueError("invalid selected acceleration profile")
  if selected_deceleration_profile not in ("dom_default", *GLOBAL_BRAKING_RESPONSES):
    raise ValueError("invalid selected deceleration profile")
  if type(extra_stopped_gap_m) not in (int, float) or not math.isfinite(extra_stopped_gap_m) or not 0 <= extra_stopped_gap_m <= MAX_EXTRA_STOPPED_GAP_M:
    raise ValueError("invalid extra stopped-lead gap")
  return {
    "extraStoppedGapM": float(extra_stopped_gap_m),
    "schemaVersion": PROFILE_SCHEMA_VERSION,
    "enabled": enabled,
    "selectedAccelerationProfile": selected_acceleration_profile,
    "selectedDecelerationProfile": selected_deceleration_profile,
    "axes": deepcopy(PROFILE_AXES),
    "profiles": deepcopy(profiles),
  }


def _decode_json(raw):
  if isinstance(raw, bytes):
    try:
      raw = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
      return None
  if isinstance(raw, str):
    try:
      raw = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
      return None
  return raw


def _validated_category_with_length(
  category: str,
  raw_category,
  expected_length: int,
  curve_bounds: dict[str, tuple[float, float]],
  legacy_curve_bounds: dict[str, tuple[float, float]] | None = None,
  *, retain_custom: bool = False,
) -> dict | None:
  if category not in _CATEGORY_SPECS or not isinstance(raw_category, dict):
    return None
  keys = set(raw_category)
  if "legacyActivation" in keys:
    if category not in ("acceleration", "braking") or raw_category["legacyActivation"] is not True:
      return None
    cleaned = dict(raw_category)
    cleaned.pop("legacyActivation")
    validated = _validated_category_with_length(category, cleaned, expected_length, curve_bounds,
                                                legacy_curve_bounds, retain_custom=retain_custom)
    if validated is None or validated["preset"] == "selected_profile":
      return None
    return {**validated, "legacyActivation": True}
  has_legacy_curve = "legacyCurve" in keys
  if keys != ({"preset", "curve", "legacyCurve"} if has_legacy_curve else {"preset", "curve"}):
    return None
  presets, _ = _CATEGORY_SPECS[category]
  preset = raw_category.get("preset")
  curve = raw_category.get("curve")
  if not isinstance(preset, str) or preset not in presets or not isinstance(curve, list):
    return None
  if preset != "custom" and (not retain_custom or not curve):
    return {"preset": preset, "curve": []} if not curve and not has_legacy_curve else None
  if len(curve) != expected_length:
    return None

  minimum, maximum = curve_bounds[category]
  values = []
  for raw_value in curve:
    if isinstance(raw_value, bool) or not isinstance(raw_value, numbers.Real):
      return None
    value = float(raw_value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
      return None
    values.append(round(value, 4))
  validated = {"preset": preset, "curve": values}
  if has_legacy_curve:
    legacy_curve = raw_category.get("legacyCurve")
    if category not in ("acceleration", "braking") or expected_length != len(ACCELERATION_SPEEDS_MPH) or not isinstance(legacy_curve, list):
      return None
    if len(legacy_curve) != len(_V1_ACCELERATION_SPEEDS_MPH):
      return None
    legacy_minimum, legacy_maximum = (legacy_curve_bounds or curve_bounds)[category]
    legacy_values = []
    for raw_value in legacy_curve:
      if isinstance(raw_value, bool) or not isinstance(raw_value, numbers.Real):
        return None
      value = float(raw_value)
      if not math.isfinite(value) or not legacy_minimum <= value <= legacy_maximum:
        return None
      legacy_values.append(round(value, 4))
    validated["legacyCurve"] = legacy_values
  return validated


def _validated_category(category: str, raw_category) -> dict | None:
  expected_length = _CATEGORY_SPECS.get(category, ((), 0))[1]
  return _validated_category_with_length(category, raw_category, expected_length, _V3_CURVE_BOUNDS, _V1_CURVE_BOUNDS, retain_custom=True)


def _schema_values_equal(actual, expected) -> bool:
  if type(actual) is not type(expected):
    return False
  if isinstance(expected, dict):
    return set(actual) == set(expected) and all(_schema_values_equal(actual[key], expected[key]) for key in expected)
  if isinstance(expected, list):
    return len(actual) == len(expected) and all(_schema_values_equal(value, reference) for value, reference in zip(actual, expected, strict=True))
  return actual == expected


def _strict_document(
  raw_document,
  schema_version: int,
  axes: dict,
  category_lengths: dict[str, int],
  curve_bounds: dict[str, tuple[float, float]],
  legacy_curve_bounds: dict[str, tuple[float, float]] | None = None,
) -> dict | None:
  decoded = _decode_json(raw_document)
  keys = {"schemaVersion", "enabled", "axes", "profiles"}
  if schema_version == 4:
    keys.add("globalBrakingResponse")
  elif schema_version >= 5:
    keys.update(("selectedAccelerationProfile", "selectedDecelerationProfile"))
  if schema_version >= 6:
    keys.add("extraStoppedGapM")
  if not isinstance(decoded, dict) or set(decoded) != keys:
    return None
  if type(decoded["schemaVersion"]) is not int or decoded["schemaVersion"] != schema_version:
    return None
  if type(decoded["enabled"]) is not bool or not _schema_values_equal(decoded["axes"], axes):
    return None
  if schema_version == 4 and decoded["globalBrakingResponse"] not in GLOBAL_BRAKING_RESPONSES:
    return None

  if schema_version >= 5 and (decoded["selectedAccelerationProfile"] not in ("dom_default", "standard", "eco", "sport", "sport_plus") or
                              decoded["selectedDecelerationProfile"] not in ("dom_default", *GLOBAL_BRAKING_RESPONSES)):
    return None
  if schema_version >= 6 and (type(decoded["extraStoppedGapM"]) not in (int, float) or
                              not math.isfinite(decoded["extraStoppedGapM"]) or
                              not 0 <= decoded["extraStoppedGapM"] <= MAX_EXTRA_STOPPED_GAP_M):
    return None
  raw_profiles = decoded["profiles"]
  if not isinstance(raw_profiles, dict) or set(raw_profiles) != set(PERSONALITY_IDS):
    return None
  profiles = {}
  for personality in PERSONALITY_IDS:
    raw_profile = raw_profiles.get(personality)
    if not isinstance(raw_profile, dict) or set(raw_profile) != set(_CATEGORY_SPECS):
      return None
    profile = {}
    for category in _CATEGORY_SPECS:
      raw_category = raw_profile.get(category)
      if (schema_version < 3 and category == "following" and isinstance(raw_category, dict)
          and raw_category.get("preset") not in ("dom_default", "custom", *_LEGACY_FOLLOWING_PRESETS)):
        return None
      if schema_version < 5 and isinstance(raw_category, dict) and ("legacyActivation" in raw_category or raw_category.get("preset") == "selected_profile"):
        return None
      validated = _validated_category_with_length(
        category, raw_profile.get(category), category_lengths[category], curve_bounds, legacy_curve_bounds,
        retain_custom=schema_version >= 3,
      )
      if validated is None:
        return None
      profile[category] = validated
    profiles[personality] = profile
  return {
    **({"extraStoppedGapM": float(decoded["extraStoppedGapM"])} if schema_version >= 6 else {}),
    "schemaVersion": schema_version,
    "enabled": decoded["enabled"],
    **({"globalBrakingResponse": decoded["globalBrakingResponse"]} if schema_version == 4 else {}),
    **({key: decoded[key] for key in ("selectedAccelerationProfile", "selectedDecelerationProfile")} if schema_version >= 5 else {}),
    "axes": deepcopy(axes),
    "profiles": profiles,
  }


def _preserve_legacy_following_presets(profiles: dict[str, dict]) -> None:
  for profile in profiles.values():
    config = profile["following"]
    if config["preset"] in _LEGACY_FOLLOWING_PRESETS:
      config["preset"] = "legacy_" + config["preset"]


def strict_profile_document(raw_document) -> dict | None:
  # V2 uses fixed-distance named following presets. Preserve their original
  # meaning; adopting a speed-dependent preset requires an explicit selection.
  decoded = _decode_json(raw_document)
  version = decoded.get("schemaVersion") if isinstance(decoded, dict) else None
  if type(version) is not int or version not in (2, 3, 4, 5, PROFILE_SCHEMA_VERSION):
    return None
  document = _strict_document(
    decoded,
    version,
    PROFILE_AXES,
    {category: expected_length for category, (_, expected_length) in _CATEGORY_SPECS.items()},
    _V2_CURVE_BOUNDS if version == 2 else _V3_CURVE_BOUNDS,
    _V1_CURVE_BOUNDS,
  )
  if document is not None:
    if version == 2:
      _preserve_legacy_following_presets(document["profiles"])
    if version < 5:
      for profile in document["profiles"].values():
        for category in ("acceleration", "braking"):
          profile[category]["legacyActivation"] = True
      document = profile_document(document["profiles"], enabled=document["enabled"],
                                  global_braking_response=document.get("globalBrakingResponse", "standard"))
  if document is not None and version == 5:
    document["schemaVersion"] = PROFILE_SCHEMA_VERSION
    document["extraStoppedGapM"] = 0.0
  return document


def migrate_profile_document(raw_document) -> dict | None:
  current = strict_profile_document(raw_document)
  if current is not None:
    return current

  legacy = _strict_document(
    raw_document,
    1,
    _V1_PROFILE_AXES,
    {"acceleration": len(_V1_ACCELERATION_SPEEDS_MPH), "braking": len(_V1_ACCELERATION_SPEEDS_MPH), "following": len(FOLLOWING_SPEEDS_MPH)},
    _V1_CURVE_BOUNDS,
  )
  if legacy is None:
    return None

  migrated_profiles = deepcopy(legacy["profiles"])
  _preserve_legacy_following_presets(migrated_profiles)
  for profile in migrated_profiles.values():
    for category in ("acceleration", "braking"):
      config = profile[category]
      if config["preset"] != "custom":
        continue
      legacy_curve = list(config["curve"])
      minimum, maximum = CURVE_BOUNDS[category]
      config["curve"] = [
        round(min(max(_linear_interp(float(speed_mph), _V1_ACCELERATION_SPEEDS_MPH, config["curve"]), minimum), maximum), 4)
        for speed_mph in ACCELERATION_SPEEDS_MPH
      ]
      config["legacyCurve"] = legacy_curve
  for profile in migrated_profiles.values():
    for category in ("acceleration", "braking"):
      profile[category]["legacyActivation"] = True
  return strict_profile_document(profile_document(migrated_profiles, enabled=legacy["enabled"], global_braking_response="standard"))


def is_unconfigured_profile_document(raw_document) -> bool:
  if raw_document is None:
    return True
  decoded = _decode_json(raw_document)
  return isinstance(decoded, dict) and not decoded


def synchronise_profile_document_enabled(
  raw_document, enabled: bool, ev_tuning: bool, truck_tuning: bool = False,
) -> dict | None:
  if type(enabled) is not bool:
    raise ValueError("enabled must be a JSON boolean")
  document = migrate_profile_document(raw_document)
  if document is None:
    if not is_unconfigured_profile_document(raw_document) or not enabled:
      return None
    return profile_document(default_personality_profiles(ev_tuning, truck_tuning), enabled=True)
  document["enabled"] = enabled
  return strict_profile_document(document)


def strict_personality_profiles(raw_document) -> dict[str, dict] | None:
  document = migrate_profile_document(raw_document)
  if document is None or not document["enabled"]:
    return None
  return deepcopy(document["profiles"])


def load_personality_profiles(raw_document, ev_tuning: bool, truck_tuning: bool = False) -> dict[str, dict]:
  document = migrate_profile_document(raw_document)
  return deepcopy(document["profiles"]) if document is not None else default_personality_profiles(ev_tuning, truck_tuning)


def serialize_personality_profiles(profiles, ev_tuning: bool, truck_tuning: bool = False, *, enabled: bool,
                                   global_braking_response: str = DEFAULT_DECELERATION_PROFILE,
                                   selected_acceleration_profile: str = "dom_default",
                                   selected_deceleration_profile: str | None = None, extra_stopped_gap_m: float = 0.0) -> str:
  del ev_tuning, truck_tuning
  document = profile_document(profiles, enabled=enabled, global_braking_response=global_braking_response,
                              selected_acceleration_profile=selected_acceleration_profile,
                              selected_deceleration_profile=selected_deceleration_profile, extra_stopped_gap_m=extra_stopped_gap_m)
  canonical = strict_profile_document(document)
  if canonical is None:
    raise ValueError("Longitudinal personality profiles must be complete and valid.")
  return json.dumps(canonical, separators=(",", ":"), sort_keys=True, allow_nan=False)


def update_personality_profile(
  profiles, personality: str, category: str, preset: str, curve, ev_tuning: bool, truck_tuning: bool = False,
  *, reset: bool = False,
) -> dict[str, dict]:
  if personality not in PERSONALITY_IDS:
    raise ValueError(f"Unknown personality: {personality}")
  if category not in _CATEGORY_SPECS:
    raise ValueError(f"Unknown profile category: {category}")
  base_document = profile_document(profiles, enabled=True)
  canonical = strict_profile_document(base_document)
  validated = _validated_category(category, {"preset": preset, "curve": curve})
  if preset != "custom" and (curve != [] or reset):
    validated = None
  if validated is not None and preset == "custom" and not reset:
    minimum, maximum = (_V3_CURVE_BOUNDS[category] if personality == "traffic" and category == "braking" else
                        CURVE_BOUNDS[category])
    previous = canonical["profiles"][personality][category] if canonical is not None else None
    for index, value in enumerate(curve):
      if not minimum <= value <= maximum and (
        previous is None or not previous["curve"] or value != previous["curve"][index]
      ):
        validated = None
        break
  if validated is None:
    minimum, maximum = (_V3_CURVE_BOUNDS[category] if personality == "traffic" and category == "braking" else
                        CURVE_BOUNDS[category])
    presets, expected_length = _CATEGORY_SPECS[category]
    message = f"Invalid {category} profile: preset must be one of {', '.join(presets)} and curve must contain "
    message += f"{expected_length} finite numeric values between {minimum} and {maximum}."
    raise ValueError(message)

  if canonical is None:
    base = default_personality_profiles(ev_tuning, truck_tuning)
  else:
    base = canonical["profiles"]
  updated = deepcopy(base)
  previous = updated[personality][category]
  previous.pop("legacyActivation", None)
  # A preset only changes which curve is active. Dormant Custom data, including
  # preserved v1 runtime interpolation, survives switching and serialization.
  if preset != "custom" or (not reset and validated["curve"] == previous["curve"]):
    previous["preset"] = preset
    return updated
  updated[personality][category] = validated
  return updated


def active_personality_id(traffic_mode: bool | None, personality) -> str | None:
  if type(traffic_mode) is not bool:
    return None
  if traffic_mode:
    return "traffic"
  if isinstance(personality, bool):
    return None
  raw = getattr(personality, "raw", personality)
  if isinstance(raw, bool) or not isinstance(raw, numbers.Integral):
    return None
  return {0: "aggressive", 1: "standard", 2: "relaxed"}.get(int(raw))


def resolve_personality_profile(raw_document, traffic_mode: bool, personality) -> dict | None:
  profiles = strict_personality_profiles(raw_document)
  personality_id = active_personality_id(traffic_mode, personality)
  if profiles is None or personality_id is None:
    return None
  return deepcopy(profiles[personality_id])


def resolve_personality_category(raw_document, traffic_mode: bool, personality, category: str) -> dict | None:
  profile = resolve_personality_profile(raw_document, traffic_mode, personality)
  if profile is None or category not in _CATEGORY_SPECS:
    return None
  config = profile[category]
  return None if config["preset"] == "dom_default" else deepcopy(config)


def category_curve(category: str, config: dict, ev_tuning: bool, truck_tuning: bool = False) -> list[float]:
  validated = _validated_category(category, config)
  if validated is None:
    raise ValueError(f"Invalid {category} profile configuration.")
  preset = validated["preset"]
  if preset in ("dom_default", "selected_profile"):
    raise ValueError("Dom default resolves through the legacy controller path")
  if preset == "custom":
    return list(validated["curve"])
  if category == "acceleration":
    return _acceleration_preset_curve(preset, ev_tuning, truck_tuning)
  if category == "braking":
    return list(_BRAKING_PRESET_CURVES[preset])
  return list(FOLLOWING_PRESET_CURVES[preset])


def _sample_config_on_custom_axis(
  category: str, config: dict, ev_tuning: bool, truck_tuning: bool,
) -> list[float]:
  return [
    round(interpolate_category_curve(category, speed_mph * 0.44704, config, ev_tuning, truck_tuning), 4)
    for speed_mph in _CATEGORY_SPEEDS_MPH[category]
  ]


def personality_reference_curves(ev_tuning: bool, truck_tuning: bool = False) -> dict[str, dict[str, list[float]]]:
  return {
    personality: {
      category: _sample_config_on_custom_axis(
        category,
        {"preset": preset, "curve": []},
        ev_tuning,
        truck_tuning,
      )
      for category, preset in presets.items()
    }
    for personality, presets in _PERSONALITY_REFERENCE_PRESETS.items()
  }


def initial_custom_curve(
  category: str,
  current_config: dict,
  ev_tuning: bool,
  truck_tuning: bool,
  *,
  legacy_curve: list[float] | None = None,
) -> list[float]:
  if category not in _CATEGORY_SPECS or not isinstance(current_config, dict):
    raise ValueError("Unknown or malformed profile category")
  preset = current_config.get("preset")
  if current_config.get("curve"):
    candidate = current_config["curve"]
  elif preset == "dom_default":
    candidate = legacy_curve
    if category in ("acceleration", "braking") and isinstance(candidate, list) and len(candidate) == len(_V1_ACCELERATION_SPEEDS_MPH):
      candidate = [
        round(_linear_interp(float(speed_mph), _V1_ACCELERATION_SPEEDS_MPH, candidate), 4)
        for speed_mph in _CATEGORY_SPEEDS_MPH[category]
      ]
  elif preset == "custom":
    candidate = current_config.get("curve")
  elif isinstance(preset, str):
    candidate = _sample_config_on_custom_axis(category, {"preset": preset, "curve": []}, ev_tuning, truck_tuning)
    minimum, maximum = CURVE_BOUNDS[category]
    candidate = [round(min(max(value, minimum), maximum), 4) for value in candidate]
  else:
    candidate = None
  validated = _validated_category(category, {"preset": "custom", "curve": candidate})
  if validated is None:
    raise ValueError(f"Cannot initialize Custom {category} from the current selection")
  return validated["curve"]


def _linear_interp(value: float, breakpoints: tuple[float, ...], values: list[float]) -> float:
  if value <= breakpoints[0]:
    return float(values[0])
  if value >= breakpoints[-1]:
    return float(values[-1])
  index = next(index for index, point in enumerate(breakpoints[1:], start=1) if point >= value) - 1
  t = (value - breakpoints[index]) / float(breakpoints[index + 1] - breakpoints[index])
  return float(values[index] + t * (values[index + 1] - values[index]))


def interpolate_category_curve(
  category: str, v_ego: float, config: dict, ev_tuning: bool, truck_tuning: bool = False,
) -> float:
  if not isinstance(v_ego, numbers.Real) or isinstance(v_ego, bool) or not math.isfinite(float(v_ego)):
    raise ValueError("Vehicle speed must be finite")
  validated = _validated_category(category, config)
  if validated is None:
    raise ValueError(f"Invalid {category} profile configuration.")
  values = category_curve(category, validated, ev_tuning, truck_tuning)
  if validated["preset"] == "custom" and "legacyCurve" in validated:
    return _linear_interp(float(v_ego), _NATIVE_ACCELERATION_SPEEDS_MS, validated["legacyCurve"])
  if category == "acceleration":
    from openpilot.starpilot.longitudinal.accel_profile import interpolate_accel_profile
    breakpoints = _NATIVE_ACCELERATION_SPEEDS_MS if validated["preset"] != "custom" else tuple(
      speed * 0.44704 for speed in ACCELERATION_SPEEDS_MPH
    )
    return interpolate_accel_profile(float(v_ego), values, breakpoints)
  breakpoints = (_FOLLOWING_PRESET_SPEEDS_MPH[validated["preset"]]
                 if category == "following" and validated["preset"] != "custom" else _CATEGORY_SPEEDS_MPH[category])
  return _linear_interp(float(v_ego) / 0.44704, breakpoints, values)
