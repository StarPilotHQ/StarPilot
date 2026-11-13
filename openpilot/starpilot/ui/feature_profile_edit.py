"""Build saved longitudinal profile edits without applying them."""
from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING

from opendbc.car.structs import car
from openpilot.starpilot.longitudinal.accel_profile import A_CRUISE_MAX_VALS_TRAFFIC_ALL, interpolate_accel_profile
from openpilot.starpilot.longitudinal.profile_runtime import TRAFFIC_CRUISE_BRAKE_MAGNITUDE
from openpilot.starpilot.longitudinal.profile_document import (
  ACCELERATION_PRESETS, ACCELERATION_SPEEDS_MPH, BRAKING_PRESETS, FOLLOWING_PRESETS, CURVE_BOUNDS,
  DEFAULT_DECELERATION_PROFILE, MAX_EXTRA_STOPPED_GAP_M, default_personality_profiles, initial_custom_curve,
  FOLLOWING_SPEEDS_MPH, is_truck_fingerprint, personality_reference_curves, serialize_personality_profiles,
  update_personality_profile, profile_document,
)
from openpilot.starpilot.speed_limits.offset_document import MPH_TO_MPS
from openpilot.starpilot.ui.long_profile_feature import preset_value

if TYPE_CHECKING:
  from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner

PRESETS = {"acceleration": ACCELERATION_PRESETS, "braking": BRAKING_PRESETS, "following": FOLLOWING_PRESETS}
PROFILE_NAMES = ("aggressive", "standard", "relaxed")
DOCUMENT_PROFILE_NAMES = ("traffic", *PROFILE_NAMES)


def edit_document(self: FeatureSettingsOwner, key: str, value: str, *, seed_capability: tuple | None = None) -> str | None:
  parts = key.split(":")
  if key == "profile:stopped_gap":
    document, _, valid = self._document()
    if not valid:
      return None
    if document is None:
      document = profile_document(default_personality_profiles(False, is_truck_fingerprint(self.vehicle_fingerprint())), enabled=False)
    gap = float(value) * (1.0 if self._value("IsMetric")[0] == "1" else 0.3048)
    if not math.isfinite(gap) or not 0. <= gap <= MAX_EXTRA_STOPPED_GAP_M:
      return None
    document["extraStoppedGapM"] = round(gap, 6)
    return json.dumps(document, separators=(",", ":"), sort_keys=True, allow_nan=False)
  if key in ('profile:global_acceleration', 'profile:global_braking'):
    category = 'acceleration' if key.endswith('acceleration') else 'braking'
    selected = preset_value(value) if type(value) is str else None
    choices = ('dom_default', 'standard', 'eco', 'sport', 'sport_plus') if category == 'acceleration' else ('dom_default', 'standard', 'eco', 'sport')
    if selected not in choices:
      return None
    document, _, valid = self._document()
    if not valid:
      return None
    fingerprint = self.vehicle_fingerprint()
    if not fingerprint:
      return None
    truck = is_truck_fingerprint(fingerprint)
    profiles = document['profiles'] if document is not None else default_personality_profiles(False, truck)
    acceleration = document['selectedAccelerationProfile'] if document is not None else 'dom_default'
    braking = document['selectedDecelerationProfile'] if document is not None else DEFAULT_DECELERATION_PROFILE
    return serialize_personality_profiles(profiles, False, truck, enabled=document['enabled'] if document is not None else False,
                                          selected_acceleration_profile=selected if category == 'acceleration' else acceleration,
                                          selected_deceleration_profile=selected if category == 'braking' else braking,
                                          extra_stopped_gap_m=document.get("extraStoppedGapM", 0.) if document is not None else 0.)
  if len(parts) not in (3, 4) or parts[1] not in DOCUMENT_PROFILE_NAMES or parts[2] not in PRESETS:
    return None
  fingerprint = self.vehicle_fingerprint()
  if not fingerprint:
    return None
  document, _, valid = self._document()
  if not valid:
    return None
  name, category = parts[1], parts[2]
  cp = self.vehicle_params() if name == "traffic" else None
  ev = (bool(cp is not None and getattr(cp, "transmissionType", None) == car.CarParams.TransmissionType.direct)
        if name == "traffic" else
        seed_capability is not None and seed_capability[1] == car.CarParams.TransmissionType.direct)
  truck = is_truck_fingerprint(fingerprint) and not ev if name == "traffic" or seed_capability is not None else is_truck_fingerprint(fingerprint)
  profiles = document["profiles"] if document is not None else default_personality_profiles(ev, truck)
  config = profiles[name][category]
  seed_config = config
  if config['preset'] == 'selected_profile':
    selected = (document['selectedAccelerationProfile' if category == 'acceleration' else 'selectedDecelerationProfile']
                if document is not None else ('dom_default' if category == 'acceleration' else DEFAULT_DECELERATION_PROFILE))
    seed_config = {'preset': selected, 'curve': config['curve']}
  if len(parts) == 3:
    value = preset_value(value)
    if value not in PRESETS[category]:
      return None
    reference = None
    if seed_config["preset"] == "dom_default":
      if name == "traffic":
        if category == "acceleration":
          reference = [round(interpolate_accel_profile(speed * MPH_TO_MPS, A_CRUISE_MAX_VALS_TRAFFIC_ALL), 4)
                       for speed in ACCELERATION_SPEEDS_MPH]
        elif category == "braking":
          reference = [round(TRAFFIC_CRUISE_BRAKE_MAGNITUDE, 4)] * len(ACCELERATION_SPEEDS_MPH)
        else:
          source = dict(self.traffic_profiles._sources()[0])
          low, low_valid = self.traffic_profiles._value("TrafficFollow", source["TrafficFollow"])
          high, high_valid = self.traffic_profiles._value("RelaxedFollow", source["RelaxedFollow"])
          if not low_valid or not high_valid or low is None or high is None:
            return None
          reference = [round(low + (high - low) * min(speed * MPH_TO_MPS / 25.0, 1.0), 4)
                       for speed in FOLLOWING_SPEEDS_MPH]
      else:
        low, high = CURVE_BOUNDS[category]
        reference = [round(max(low, min(high, point)), 4)
                     for point in personality_reference_curves(ev, truck)[name][category]]
    if config['preset'] == 'selected_profile' and category == 'braking' and seed_config['preset'] != 'dom_default' and not config['curve']:
      magnitude = {'standard': 1.2, 'eco': 0.6, 'sport': 2.4}[seed_config['preset']]
      curve = [min(magnitude, CURVE_BOUNDS['braking'][1])] * len(ACCELERATION_SPEEDS_MPH) if value == 'custom' else []
    else:
      curve = initial_custom_curve(category, seed_config, ev, truck, legacy_curve=reference) if value == "custom" else []
    updated = update_personality_profile(profiles, name, category, value, curve, ev, truck)
  else:
    if config["preset"] != "custom":
      return None
    index = int(parts[3])
    if not 0 <= index < 10:
      return None
    curve = list(config["curve"])
    curve[index] = float(value)
    updated = update_personality_profile(profiles, name, category, "custom", curve, ev, truck)
  enabled, _, _ = self._value("CustomPersonalities")
  return serialize_personality_profiles(updated, ev, truck, enabled=enabled == "1",
                                        selected_acceleration_profile=document["selectedAccelerationProfile"] if document is not None else "dom_default",
                                        selected_deceleration_profile=(document["selectedDecelerationProfile"] if document is not None
                                                                       else DEFAULT_DECELERATION_PROFILE),
                                        extra_stopped_gap_m=document.get("extraStoppedGapM", 0.) if document is not None else 0.)
