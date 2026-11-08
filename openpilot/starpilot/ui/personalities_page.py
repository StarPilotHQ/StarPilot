"""Driving Personalities pages, built for FeatureSettingsOwner.

The hub lists the personalities. A personality page has Acceleration and Braking (each with a Custom toggle; Off uses
Long Planner's profile) and Following (StarPilot Default, a named curve, or Custom). Following = Custom opens the
personality's custom-following page: Simple (low- and high-speed gap) or Advanced (a gap every 10 mph), then smoothness.
"""
from dataclasses import replace

from openpilot.starpilot.longitudinal.profile_document import (CURVE_BOUNDS, CUSTOM_FOLLOWING_PRESETS, DEFAULT_DECELERATION_PROFILE,
                                                               FOLLOWING_SPEEDS_MPH, default_personality_profiles)
from openpilot.starpilot.longitudinal.profile_preferences import read_profile_health
from openpilot.starpilot.speed_limits.runtime_settings import MPH_TO_MPS
from openpilot.starpilot.ui.feature_settings_state import FeatureRow
from openpilot.starpilot.ui.long_profile_feature import CATEGORY_CHOICE_HELP, VALUE_HELP, preset_label, preset_value

LONG_PREFIX = "profile:"
# The personality page's Following list: "Custom" covers Simple (custom_simple) and Advanced (custom).
FOLLOWING_CHOICES = ("StarPilot Default", "Close", "Medium", "Far", "Traffic", "Custom", "Legacy Close", "Legacy Medium", "Legacy Far")
FOLLOWING_HELP = ("StarPilot Default uses openpilot's standard gap and smoothness. Close, Medium, Far and Traffic are set gaps by speed. " +
                  "Custom uses your own gap and smoothness.")


def personality_summary(profile: dict) -> str:
  def shown(category):
    preset = profile[category]["preset"]
    return ("Acceleration & Braking" if preset == "selected_profile" else "Custom" if preset in CUSTOM_FOLLOWING_PRESETS else preset_label(preset))
  return f"Following: {shown('following')} · Acceleration: {shown('acceleration')} · Braking: {shown('braking')}"


def personality_page_rows(owner, name: str, **context) -> list[FeatureRow]:
  """Acceleration, braking (each with a Custom toggle) and following; custom values open on their own pages."""
  document, raw, valid = owner._document()
  profile = (document["profiles"][name] if document is not None else default_personality_profiles(False, False)[name]) if valid else None
  rows = []
  for category, general in (("acceleration", "Acceleration Profile"), ("braking", "Deceleration Profile")):
    sub = owner._profile_sub_rows(name, category, **context)
    preset_row = sub[f"{LONG_PREFIX}{name}:{category}"]
    preset = profile[category]["preset"] if profile else None
    custom_on = preset not in (None, "selected_profile")
    shared = (document["selectedAccelerationProfile" if category == "acceleration" else "selectedDecelerationProfile"]
              if document is not None else ("dom_default" if category == "acceleration" else DEFAULT_DECELERATION_PROFILE))
    rows.append(FeatureRow(f"{LONG_PREFIX}{name}:{category}:custom", f"Custom {category}", "On" if custom_on else "Off", raw,
                           ("Off", "On") if profile else (), available=preset_row.available and profile is not None,
                           capability=preset_row.capability, dependencies=preset_row.dependencies, default_value="Off",
                           reason=f"Off uses the {general} set in Acceleration & Braking ({preset_label(shared)}). On lets {name.title()} use its own."))
    if custom_on:
      rows.append(replace(preset_row, label=category.title(), choices=tuple(c for c in preset_row.choices if c != "Selected Profile"),
                          default_value=None, reason=CATEGORY_CHOICE_HELP[category]))
      if preset == "custom":
        rows.append(FeatureRow("", f"Edit custom {category}", "Set the limit at each speed", page=f"{name}/{category}", available=True))
  sub = owner._profile_sub_rows(name, "following", **context)
  preset_row = sub[f"{LONG_PREFIX}{name}:following"]
  preset = profile["following"]["preset"] if profile else None
  custom = preset in CUSTOM_FOLLOWING_PRESETS
  rows.append(FeatureRow(f"{LONG_PREFIX}{name}:following:choice", "Following", "Custom" if custom else preset_label(preset) if preset else
                         "Invalid document", raw, FOLLOWING_CHOICES if profile else (), available=preset_row.available and profile is not None,
                         capability=preset_row.capability, dependencies=preset_row.dependencies, default_value="StarPilot Default",
                         reason=FOLLOWING_HELP))
  if custom:
    rows.append(FeatureRow("", "Edit custom following", ("Advanced" if preset == "custom" else "Simple") + ": gap and smoothness",
                           page=f"{name}/custom_following", available=True))
  return rows


def custom_following_rows(owner, name: str, *, parked: bool, configurable: bool, system_long: bool, **context) -> list[FeatureRow]:
  """Simple (low and high-speed gap) or Advanced (gap at each 10 mph), then the smoothness (jerk) values."""
  context = {"parked": parked, "system_long": system_long, **context}
  document, raw, valid = owner._document()
  profile = (document["profiles"][name] if document is not None else default_personality_profiles(False, False)[name]) if valid else None
  preset = profile["following"]["preset"] if profile else None
  advanced = preset == "custom"
  sub = owner._profile_sub_rows(name, "following", **context)
  preset_row = sub[f"{LONG_PREFIX}{name}:following"]
  rows = [FeatureRow(f"{LONG_PREFIX}{name}:following:mode", "Customization", "Advanced" if advanced else "Simple", raw,
                     ("Simple", "Advanced") if preset in CUSTOM_FOLLOWING_PRESETS else (),
                     available=preset_row.available and preset in CUSTOM_FOLLOWING_PRESETS,
                     capability=preset_row.capability, dependencies=preset_row.dependencies, default_value="Simple",
                     reason=("Simple: one gap at low speed and one at high speed. Advanced: a gap for every 10 mph." if preset in
                             CUSTOM_FOLLOWING_PRESETS else "Set Following to Custom first"))]
  if advanced:
    rows.extend(replace(row, label=f"Following at {row.label.removesuffix(' point')}") for row in sub.values()
                if row.label.endswith(" mph point"))
  if name == "traffic":
    traffic = owner.traffic_profiles.rows(parked, system_long, repair_allowed=parked)
    rows.extend(row for row in traffic if not (advanced and row.key in ("TrafficFollow", "")))
    return rows
  allowed = configurable and system_long
  health = read_profile_health(owner.params)
  title = name.title()
  for suffix, label in (
    ("Follow", "Low-speed follow"), ("FollowHigh", "High-speed follow"),
    ("JerkAcceleration", "Acceleration jerk"), ("JerkDeceleration", "Deceleration jerk"),
    ("JerkSpeed", "Speed jerk"), ("JerkSpeedDecrease", "Speed decrease jerk"), ("JerkDanger", "Danger jerk")):
    if advanced and suffix in ("Follow", "FollowHigh"):
      continue
    key = title + suffix
    number_row = owner._number_row(key, label, allowed)
    rows.append(replace(number_row, reason=VALUE_HELP[suffix] if number_row.available else number_row.reason)
                if health.values[key].readable else
                FeatureRow(key, label, "Invalid saved value", health.values[key].raw, reason="Saved source unreadable or too large"))
  rows.extend(owner.long_profiles.rows(name, health, allowed, repair_allowed=parked and allowed))
  return rows


def personality_control(category: str, control: str, value, config: dict, document) -> str | None:
  """The preset a personality-page control stands for, or None when the request does not fit the control."""
  if control == "custom" and category in ("acceleration", "braking") and value in ("On", "Off"):
    if value == "Off":
      return "selected_profile"
    if config["preset"] != "selected_profile":
      return config["preset"]
    field = "selectedAccelerationProfile" if category == "acceleration" else "selectedDecelerationProfile"
    return document[field] if document is not None else ("dom_default" if category == "acceleration" else DEFAULT_DECELERATION_PROFILE)
  if control == "choice" and category == "following" and value in FOLLOWING_CHOICES:
    chosen = preset_value(value)
    if chosen == "custom":
      return config["preset"] if config["preset"] in CUSTOM_FOLLOWING_PRESETS else "custom_simple"
    return chosen
  if control == "mode" and category == "following" and value in ("Simple", "Advanced") and config["preset"] in CUSTOM_FOLLOWING_PRESETS:
    return "custom_simple" if value == "Simple" else "custom"
  return None


def simple_following_curve(owner, name: str) -> list[float] | None:
  if name == "traffic":
    source = dict(owner.traffic_profiles._sources()[0])
    low, low_valid = owner.traffic_profiles._value("TrafficFollow", source["TrafficFollow"])
    high, high_valid = owner.traffic_profiles._value("RelaxedFollow", source["RelaxedFollow"])
    if not low_valid or not high_valid or low is None or high is None:
      return None
    return [round(low + (high - low) * min(speed * MPH_TO_MPS / 25.0, 1.0), 4) for speed in FOLLOWING_SPEEDS_MPH]
  try:
    low, high = float(owner._value(name.title() + "Follow")[0]), float(owner._value(name.title() + "FollowHigh")[0])
  except ValueError:
    return None
  bounds = CURVE_BOUNDS["following"]
  return [round(min(max(low + (high - low) * min(max((speed - 45.0) / 25.0, 0.0), 1.0), bounds[0]), bounds[1]), 4)
          for speed in FOLLOWING_SPEEDS_MPH]
