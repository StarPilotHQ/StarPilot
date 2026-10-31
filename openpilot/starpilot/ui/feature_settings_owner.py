"""One allowlisted Params owner for saved driving and vehicle preferences.

All reads are side-effect free. A final native action must present the exact
bytes shown at press time and the matching vehicle configuration before writing.
Reset and migration actions additionally require a parked vehicle.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import replace
from typing import Any
import json
import math
from openpilot.starpilot.lateral.pause import KEYS as LATERAL_PAUSE_KEYS
from openpilot.starpilot.lateral.lane_centering import STRENGTH_BASELINE, STRENGTH_MAX

from opendbc.car.structs import car
from openpilot.common.params import UnknownKeyName
from openpilot.starpilot.longitudinal.accel_profile import A_CRUISE_MAX_VALS_TRAFFIC_ALL, interpolate_accel_profile
from openpilot.starpilot.longitudinal.profile_runtime import TRAFFIC_CRUISE_BRAKE_MAGNITUDE
from openpilot.starpilot.longitudinal.lead_approach_runtime import KEY as LEAD_APPROACH_KEY
from openpilot.starpilot.longitudinal.lead_takeoff_preferences import KEY as LEAD_TAKEOFF_KEY
from openpilot.starpilot.longitudinal.encouragement import KEY as NUDGE_KEY
from openpilot.starpilot.longitudinal.planner_selection import KEY as PLANNER_SELECTION_KEY, save_selection

from openpilot.starpilot.longitudinal.profile_document import (
  ACCELERATION_PRESETS, ACCELERATION_SPEEDS_MPH, BRAKING_PRESETS, FOLLOWING_PRESETS, CURVE_BOUNDS,
  DEFAULT_DECELERATION_PROFILE, MAX_EXTRA_STOPPED_GAP_M, PERSONALITY_PROFILES_PARAM, default_personality_profiles, initial_custom_curve,
  FOLLOWING_SPEEDS_MPH, is_truck_fingerprint,
  personality_reference_curves, serialize_personality_profiles,
  update_personality_profile, profile_document,
  synchronise_profile_document_enabled,
)
from openpilot.starpilot.ui.feature_settings_state import (
  FeaturePage, FeatureRow, FeatureSettingsRequest, FeatureSettingsState, SLC_CONFIRM_ACTIONS, SLC_OFFSET_KEYS, TORQUE_CONFIRM_ACTIONS,
)
from openpilot.starpilot.speed_limits.runtime_settings import MPH_TO_MPS
from openpilot.starpilot.aol.vehicle import policy_for as aol_policy_for
from openpilot.starpilot.feature_capabilities import longitudinal_supported as feature_longitudinal_supported
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, LEGACY_KEYS
from openpilot.starpilot.ui.torque_feature import TorqueFeature
from openpilot.starpilot.ui.controller_feature import ControllerFeature, SETUP_ACTION
from openpilot.starpilot.ui.slc_offset_feature import SlcOffsetOwner
from openpilot.starpilot.longitudinal.profile_preferences import read_document_value, read_profile_health
from openpilot.starpilot.ui.long_profile_feature import (LongProfileFeature, is_long_confirm_action, VALUE_HELP, CATEGORY_HELP,
                                                       preset_label, preset_value)
from openpilot.starpilot.ui.traffic_feature import TrafficFeature, EDIT_KEYS as TRAFFIC_EDIT_KEYS
from openpilot.starpilot.ui.lane_change_feature import LaneChangeFeature, KEYS as LANE_CHANGE_KEYS
from openpilot.starpilot.ui.conditional_feature import BUTTON_PREFIX, ConditionalFeature
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.wheel_feature import WheelFeature, PREFIX as WHEEL_PREFIX
from openpilot.starpilot.longitudinal.output_max import KEY as OUTPUT_MAX_KEY
from openpilot.starpilot.ui.tesla_screen_feature import TeslaScreenFeature, KEYS as TESLA_SCREEN_KEYS
from openpilot.starpilot.ui.ford_bluepilot_feature import FordBluePilotFeature, KEYS as FORD_BLUEPILOT_KEYS
from openpilot.starpilot.ui.ford_cluster_feature import FordClusterFeature
from openpilot.starpilot.ui.preap_radar_feature import PreAPRadarFeature, KEYS as PREAP_RADAR_KEYS, capability as preap_radar_capability
from openpilot.starpilot.ui.gm_tune_feature import GmTuneFeature, GmTruckFeature, GmEvPresetFeature
from openpilot.starpilot.car.gm.tune_preferences import KEY as GM_TUNE_KEY
from openpilot.starpilot.car.gm.radar_recovery import KEY as RADAR_RECOVERY_KEY
from openpilot.starpilot.ui.volt_radar_feature import VoltRadarFeature
from openpilot.starpilot.ui.panda_wake_feature import PandaWakeFeature
from openpilot.starpilot.panda_wake import KEYS as PANDA_WAKE_KEYS
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.curve_speed.preferences import (
  DOCUMENT_KEY as CURVE_DOCUMENT_KEY, LEGACY_KEY as CURVE_LEGACY_KEY,
  MASTER_KEY as CURVE_MASTER_KEY, NO_LEAD_KEY as CURVE_NO_LEAD_KEY,
  DOCUMENT_LIMIT as CURVE_DOCUMENT_LIMIT, read_learning,
)
from openpilot.starpilot.curve_speed.actions import ACTION_KEYS as CURVE_ACTION_KEYS, apply_learning, learning_snapshot


from openpilot.starpilot.controllers.toyota_cruise import capability as toyota_cruise_capability


GLOBAL_BOOL_PREFERENCES = frozenset((LEAD_APPROACH_KEY, LEAD_TAKEOFF_KEY, NUDGE_KEY, "DisengageOnAccelerator"))

BOOL_DEFAULTS = {
  "GMPedalLongitudinal": False, "ForceStops": False, "AlwaysAllowUploads": False, "TurnAssist": True,
  "SubaruAvhStartup": False,
  "SubaruStopStartOff": False,
  "SubaruSNG": False,
  "SubaruRedneckCruise": False,
  "ReverseCruise": False, "ToyotaAutoHold": False, "SNGHack": False, "JeepBrakeHold": False, "VoltSNG": False, "GMAutoHold": False, "VoltOnePedalMode": False,
  "RemapCancelToDistance": False, "LongPitch": True, "DisableOpenpilotLongitudinal": False,
  "SpeedLimitController": False, "ShowSpeedLimits": True,
  "SLCConfirmation": False,
  "PauseLateralOnSignal": False,
  "LaneCentering": True, "LaneCenteringPauseOnSignal": True,
  "CustomPersonalities": False, PLANNER_SELECTION_KEY: True,
  LEAD_APPROACH_KEY: False,
  LEAD_TAKEOFF_KEY: False,
  NUDGE_KEY: True,
  "DisengageOnAccelerator": False,
  CURVE_MASTER_KEY: False, CURVE_NO_LEAD_KEY: False,
  "ShowCSCStatus": False,
  "ForceAutoTuneOff": False, "AlwaysOnLateral": True, "NostalgiaMode": False,
}
FLOAT_SPECS = {
  "ForceStopDistanceOffset": (-20, 20, 1, "ft"),
  "LaneCenterOffset": (-0.3, 0.3, 0.01, "m"),
  "LaneCenteringE2EAuthority": (0.0, 1.0, 0.05, "fraction"),
  "LaneCenteringStrength": (0.5, STRENGTH_MAX, 0.015, "fraction"),
  **{f"{name}{suffix}": (0.5, 3.0, 0.05, "s")
     for name in ("Aggressive", "Standard", "Relaxed") for suffix in ("Follow", "FollowHigh")},
  **{f"{name}{suffix}": (25.0, 200.0, 5.0, "%")
     for name in ("Aggressive", "Standard", "Relaxed")
     for suffix in ("JerkAcceleration", "JerkDeceleration", "JerkDanger", "JerkSpeedDecrease", "JerkSpeed")},
}
SLC_PRIORITY = "SLCPriority1"
SLC_SECONDARY = "SLCPriority2"
SLC_ADVANCE_CHOICES = ('Off', *(f'{seconds} s' for seconds in range(1, 11)))
LONG_PREFIX = "profile:"
PRESETS = {"acceleration": ACCELERATION_PRESETS, "braking": BRAKING_PRESETS, "following": FOLLOWING_PRESETS}
PROFILE_NAMES = ("aggressive", "standard", "relaxed")
DOCUMENT_PROFILE_NAMES = ("traffic", *PROFILE_NAMES)
AOL_BUTTONS = ("LKASButtonControl", "MainCruiseButtonControl", "DistanceButtonControl",
               "LongDistanceButtonControl", "VeryLongDistanceButtonControl")
AOL_DISTANCE_BUTTONS = AOL_BUTTONS[2:]
AOL_ACTIONS = {"Off": 0, "Pause steering": 3, "Pause longitudinal": 4, "Toggle AOL": 9}
AOL_PAUSE_ACTIONS = {name: code for name, code in AOL_ACTIONS.items() if code != 9}
AOL_THRESHOLD = "AolBrakePauseSpeedMps"
AOL_LEGACY_THRESHOLD = "PauseAOLOnBrake"
TORQUE_NUMBERS = LEGACY_KEYS
LANE_LIVE_KEYS = frozenset(("LaneCentering", "LaneCenteringPauseOnSignal", "LaneCenterOffset",
                            "LaneCenteringE2EAuthority", "LaneCenteringStrength"))
CURVE_KEYS = frozenset((CURVE_MASTER_KEY, CURVE_NO_LEAD_KEY))
CURVE_PAGE_KEYS = CURVE_KEYS | {"ShowCSCStatus"}
CRUISE_KEYS = frozenset(("CustomCruise", "CustomCruiseLong"))


class FeatureSettingsOwner:
  def __init__(self, params, authority: Callable[[str], bool], *, vehicle_fingerprint: Callable[[], str | None],
               vehicle_params: Callable[[], object | None] | None = None,
               vision_development: Callable[[], bool] | None = None,
               show_cruise_intervals: bool = False,
               configuration_longitudinal: Callable[[], bool] = lambda: False,
               configuration_vehicle: Callable[[], bool] = lambda: False,
               galaxy: Callable[[], bool] = lambda: False,
               maps_available: Callable[[], bool] | None = None):
    self.params = params
    self._galaxy = galaxy
    self._snapshot_reads: ContextVar[dict[tuple[str, int], tuple[bytes | None, bool]] | None] = ContextVar(
      "feature_snapshot_reads", default=None)
    self.authority = authority
    self.vehicle_fingerprint = vehicle_fingerprint
    self._vehicle_params_source = vehicle_params or (lambda: None)
    self._snapshot_vehicle: ContextVar[tuple[Any] | None] = ContextVar("feature_snapshot_vehicle", default=None)
    self.vision_development = vision_development or (lambda: False)
    if maps_available is None:
      from openpilot.starpilot.maps.operation_owner import maps_slc_available
      maps_available = maps_slc_available
    self.maps_available = maps_available
    self.show_cruise_intervals = show_cruise_intervals
    self.configuration_longitudinal = configuration_longitudinal
    self.configuration_vehicle = configuration_vehicle
    self.torque = TorqueFeature(self)
    self.controller = ControllerFeature(self)
    self.slc_offsets = SlcOffsetOwner(params, lambda: self.authority("slc"), self.vehicle_params,
                                      repair_parked=lambda: self.authority("parked_preferences"),
                                      configuration_longitudinal=self.configuration_longitudinal)
    self.long_profiles = LongProfileFeature(self)
    self.tesla_screen = TeslaScreenFeature(self)
    from openpilot.starpilot.ui.gm_cancel_remap_feature import GmCancelRemapFeature
    self.gm_cancel_remap = GmCancelRemapFeature(self)

    self.preap_radar = PreAPRadarFeature(self)
    from openpilot.starpilot.ui.subaru_smoothing_feature import SubaruSmoothingFeature
    self.subaru_smoothing = SubaruSmoothingFeature(self)
    self.gm_tune = GmTuneFeature(self)
    self.gm_truck = GmTruckFeature(self)
    self.gm_ev_preset = GmEvPresetFeature(self)
    self.volt_radar = VoltRadarFeature(self, galaxy=galaxy)
    self.panda_wake = PandaWakeFeature(self, galaxy=galaxy)
    self.traffic_profiles = TrafficFeature(self)
    self.lane_changes = LaneChangeFeature(params, authority, vehicle_fingerprint, self.vehicle_params,
                                          configuration_longitudinal=self.configuration_longitudinal)
    self.conditional = ConditionalFeature(self)
    self.wheel = WheelFeature(self)

  def _source_capability(self) -> tuple[bool, bool]:
    return bool(self.vision_development()), bool(self.maps_available())

  def _source_choices(self, capability: tuple[bool, bool] | None = None) -> tuple[str, ...]:
    vision, maps = self._source_capability() if capability is None else capability
    return ("Dashboard",) + (("Vision",) if vision else ()) + (("Maps",) if maps else ())

  def longitudinal_available(self) -> bool:
    cp = self.vehicle_params()
    return bool(cp is not None and (cp.openpilotLongitudinalControl or self.configuration_longitudinal()))

  def aol_settings_policy(self, cp):
    if self.configuration_vehicle():
      from openpilot.starpilot.aol.vehicle import configuration_settings_policy
      configured = configuration_settings_policy(cp)
      if configured is not None:
        return configured
    return aol_policy_for(cp)

  def _alpha_long_capability(self):
    cp = self.vehicle_params()
    release, readable = read_saved(self.params, 'IsReleaseBranch', 8)
    try:
      from opendbc.car.values import PLATFORMS
      from openpilot.starpilot.car.hyundai.aol import ioniq6_settings_capable
      from openpilot.starpilot.car.ford.longitudinal_preferences import release_long_available as ford_release_long_available
      platform = PLATFORMS.get(str(cp.carFingerprint))
      if (not self._galaxy() or platform is None or platform.__class__.__module__.split('.')[-2] != str(cp.brand) or
          not readable or release not in (None, b'0', b'1') or cp.notCar or cp.passive or cp.dashcamOnly or
          not (cp.alphaLongitudinalAvailable or ioniq6_settings_capable(cp)) or
          (release == b'1' and not ford_release_long_available(cp))):
        return None
      return (str(cp.carFingerprint), str(cp.brand), int(cp.flags), int(cp.alternativeExperience),
              bool(cp.alphaLongitudinalAvailable), bool(cp.openpilotLongitudinalControl), bool(cp.pcmCruise),
              tuple((int(item.safetyModel.raw), int(item.safetyParam)) for item in cp.safetyConfigs), release)
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None

  def _alpha_long_rows(self, parked):
    capability = self._alpha_long_capability()
    if capability is None:
      return ()
    raw, readable = read_saved(self.params, 'AlphaLongitudinalEnabled', 8)
    valid = readable and raw in (None, b'0', b'1')
    return (FeatureRow('AlphaLongitudinalEnabled', 'Alpha Longitudinal',
      ('On' if raw == b'1' else 'Off') if valid else 'Invalid saved choice', raw,
      ('Off', 'On') if valid else (), default_value='Off', capability=capability, vehicle_fingerprint=self.vehicle_fingerprint(),
      available=valid and parked and self.authority('parked_preferences') and self.authority('preferences'),
      reason='Request StarPilot speed control instead of factory ACC. May disable Automatic Emergency Braking. ' +
             'Applies after the next startup; exact vehicle takeover checks remain required.'),)

  def _apply_alpha_long(self, request):
    from openpilot.starpilot.saved_document import commit_exact
    if (request.key != 'AlphaLongitudinalEnabled' or request.value not in ('Off', 'On') or
        request.expected not in (None, b'0', b'1') or not request.confirmation or request.dependencies or
        request.related_source is not None or request.display_unit or request.direction):
      return False
    def authorized():
      return (self.authority('parked_preferences') and self.authority('preferences') and
              request.capability is not None and request.capability == self._alpha_long_capability() and
              request.vehicle_fingerprint == self.vehicle_fingerprint())
    result = commit_exact(self.params, key=request.key, max_bytes=8, expected=request.expected,
                          raw=b'1' if request.value == 'On' else b'0', authorized=authorized,
                          temp_prefix='.alpha-long-')
    if not result.committed or not result.verified:
      return False
    if authorized():
      self.params.put_bool('OnroadCycleRequested', True, block=True)
    return True

  def _capability(self, group: str) -> tuple | None:
    cp = self.vehicle_params()
    if cp is None:
      return None
    try:
      from openpilot.starpilot.lateral.torque_runtime import production_supported_cp
      if group == "torque" and production_supported_cp(cp):
        tune = cp.lateralTuning.torque
        return (str(cp.carFingerprint), str(cp.brand), str(cp.steerControlType), str(cp.lateralTuning.which()),
                bool(cp.dashcamOnly), float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction),
                str(cp.carVin) if getattr(cp, "carVin", None) else None)
      from openpilot.starpilot.aol.vehicle import configuration_settings_policy
      configuration_aol = (group in ("aol", "aol_wheel") and self.configuration_vehicle() and
                           configuration_settings_policy(cp) is not None)
      if (group in ("aol", "aol_wheel") and self.aol_settings_policy(cp).settings_supported and
          not cp.passive and (not cp.dashcamOnly or configuration_aol) and not cp.notCar):
        return (str(cp.carFingerprint), bool(cp.openpilotLongitudinalControl), bool(cp.pcmCruise),
                tuple((str(config.safetyModel), int(config.safetyParam)) for config in cp.safetyConfigs))
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None
    return None

  def _selected_vehicle_params(self) -> Any:
    selected = self._snapshot_vehicle.get()
    return selected[0] if selected is not None else self._vehicle_params_source()

  @property
  def vehicle_params(self) -> Callable[[], Any]:
    return self._selected_vehicle_params

  @vehicle_params.setter
  def vehicle_params(self, provider: Callable[[], Any]) -> None:
    self._vehicle_params_source = provider

  def _pedal_setup_capability(self) -> tuple | None:
    from opendbc.car.gm.values import CAR, ORDINARY_CC_CAR, PEDAL_BOLT_CAR, CAMERA_ACC_PEDAL_CAR
    cp = self.vehicle_params()
    try:
      if (cp is None or cp.brand != "gm" or cp.notCar or
          cp.carFingerprint not in ORDINARY_CC_CAR | PEDAL_BOLT_CAR | CAMERA_ACC_PEDAL_CAR |
          {CAR.CHEVROLET_SILVERADO_CC, CAR.CHEVROLET_VOLT_CC, CAR.CHEVROLET_MALIBU_HYBRID_CC}):
        return None
      return (str(cp.carFingerprint), str(cp.brand), int(cp.flags), bool(cp.passive), bool(cp.dashcamOnly),
              bool(cp.openpilotLongitudinalControl),
              tuple((str(c.safetyModel), int(c.safetyParam)) for c in cp.safetyConfigs))
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None

  def _apply_pedal_setup(self, request: FeatureSettingsRequest) -> bool:
    return False

  def _bolt_disable_capability(self) -> tuple | None:
    from openpilot.starpilot.vehicle_preferences import bolt_disable_supported
    from opendbc.car.gm.startup_preferences import disable_long_supported
    cp = self.vehicle_params()
    try:
      from opendbc.car.volkswagen.meb_modes import supported as meb_stock_supported
      if cp is None or not (bolt_disable_supported(cp) or disable_long_supported(cp) or meb_stock_supported(cp)):
        return None
      return (str(cp.carFingerprint), str(cp.brand), bool(cp.openpilotLongitudinalControl),
              bool(cp.pcmCruise), int(cp.flags),
              tuple((str(c.safetyModel), int(c.safetyParam)) for c in cp.safetyConfigs))
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None

  def _vw_alpha_off(self) -> bool:
    from opendbc.car.volkswagen.meb_modes import supported as meb_stock_supported
    from openpilot.starpilot.car.volkswagen.preferences import stock_long_requested
    return meb_stock_supported(self.vehicle_params()) and stock_long_requested(self.params)

  def _apply_bolt_disable(self, request: FeatureSettingsRequest) -> bool:
    if not request.confirmation or request.value not in ("Off", "On") or request.dependencies:
      return False
    def authorized() -> bool:
      return (bool(request.vehicle_fingerprint) and self.vehicle_fingerprint() == request.vehicle_fingerprint and
              request.capability is not None and request.capability == self._bolt_disable_capability() and
              not (request.value == "Off" and self._vw_alpha_off()) and
              self.authority("parked_preferences") and request.expected in (None, b"0", b"1"))
    return commit_exact(self.params, key="DisableOpenpilotLongitudinal", max_bytes=8,
                        raw=b"1" if request.value == "On" else b"0", expected=request.expected,
                        authorized=authorized, temp_prefix=".bolt-disable-long-").verified

  def _long_pitch_capability(self) -> tuple | None:
    from opendbc.car.gm.suburban import stopping_decel_rate as suburban_stopping_decel_rate
    from opendbc.car.gm.values import is_bolt_euv_longitudinal, is_volt_longitudinal, camera_acc_pedal_profile, volt_cc_pedal_profile, CAMERA_ACC_PEDAL_CAR
    cp = self.vehicle_params()
    try:
      if cp is None or cp.notCar:
        return None
      configurable_pedal = bool(self.configuration_vehicle() and cp.brand == "gm" and cp.carFingerprint in CAMERA_ACC_PEDAL_CAR)
      if (cp.passive or cp.dashcamOnly) and not configurable_pedal:
        return None
      pedal = camera_acc_pedal_profile(cp) or volt_cc_pedal_profile(cp)
      if not (is_bolt_euv_longitudinal(cp) or is_volt_longitudinal(cp) or suburban_stopping_decel_rate(cp) is not None or
              pedal is not None and pedal.longitudinal or configurable_pedal):
        return None
      return (str(cp.carFingerprint), str(cp.brand), bool(cp.openpilotLongitudinalControl), bool(cp.pcmCruise),
              int(cp.flags), int(cp.alternativeExperience),
              tuple((str(c.safetyModel), int(c.safetyParam)) for c in cp.safetyConfigs),
              str(cp.carVin) if getattr(cp, "carVin", None) else None)
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None

  def _apply_long_pitch(self, request: FeatureSettingsRequest) -> bool:
    if not request.confirmation or request.value not in ("Off", "On") or request.dependencies:
      return False
    def authorized() -> bool:
      return (bool(request.vehicle_fingerprint) and self.vehicle_fingerprint() == request.vehicle_fingerprint and
              request.capability is not None and request.capability == self._long_pitch_capability() and
              self.authority("preferences") and request.expected in (None, b"0", b"1"))
    return commit_exact(self.params, key="LongPitch", max_bytes=128, raw=b"1" if request.value == "On" else b"0",
                        expected=request.expected, authorized=authorized, temp_prefix=".gm-long-pitch-").verified

  def _subaru_avh_capability(self):
    from openpilot.starpilot.car.subaru.avh_settings import capability
    return capability(self)

  def _subaru_startup_capability(self):
    from openpilot.starpilot.car.subaru.stop_start_settings import capability
    return capability(self)

  def _subaru_sng_capability(self):
    from openpilot.starpilot.car.subaru.sng_settings import capability
    return capability(self)

  def _subaru_software_capability(self):
    from openpilot.starpilot.car.subaru.software_cruise_settings import capability
    return capability(self)

  def _gm_stop_capability(self, key: str) -> tuple | None:
    from opendbc.car.gm.values import CAR
    from openpilot.starpilot.vehicle_selection import read_selection
    cp = self.vehicle_params()
    try:
      identities = {CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_CAMERA, CAR.CHEVROLET_VOLT_2019}
      if key == "GMAutoHold":
        identities.add(CAR.BUICK_LACROSSE)
      if key not in ("VoltSNG", "GMAutoHold", "VoltOnePedalMode") or cp is None or cp.brand != "gm" or cp.carFingerprint not in identities:
        return None
      selection = read_selection(self.params)
      if not selection.readable or not selection.valid:
        return None
      return (str(cp.carFingerprint), str(cp.brand), selection.raw)
    except (AttributeError, TypeError, ValueError):
      return None

  def _apply_gm_stop(self, request: FeatureSettingsRequest) -> bool:
    if not request.confirmation or request.value not in ("Off", "On") or request.dependencies:
      return False
    def authorized() -> bool:
      return (bool(request.vehicle_fingerprint) and self.vehicle_fingerprint() == request.vehicle_fingerprint and
              request.capability is not None and request.capability == self._gm_stop_capability(request.key) and
              self.authority("parked_preferences") and request.expected in (None, b"0", b"1"))
    return commit_exact(self.params, key=request.key, max_bytes=128, raw=b"1" if request.value == "On" else b"0",
                        expected=request.expected, authorized=authorized, temp_prefix=".gm-stop-").verified

  def _jeep_hold_capability(self):
    from opendbc.car.chrysler.brake_hold import brake_hold_supported
    cp = self.vehicle_params()
    try:
      if not brake_hold_supported(cp):
        return None
      return (str(cp.carFingerprint), int(cp.flags),
              tuple((str(config.safetyModel), int(config.safetyParam)) for config in cp.safetyConfigs),
              str(cp.carVin) if cp.carVin else None)
    except (AttributeError, TypeError, ValueError):
      return None

  def _apply_jeep_hold(self, request):
    if not request.confirmation or request.value not in ("Off", "On") or request.dependencies:
      return False
    def authorized():
      return (bool(request.vehicle_fingerprint) and self.vehicle_fingerprint() == request.vehicle_fingerprint and
              request.capability is not None and request.capability == self._jeep_hold_capability() and
              self.authority("vehicle") and self.authority("parked_preferences") and request.expected in (None, b"0", b"1"))
    return commit_exact(self.params, key="JeepBrakeHold", max_bytes=128, raw=b"1" if request.value == "On" else b"0",
                        expected=request.expected, authorized=authorized, temp_prefix=".jeep-hold-").verified

  def _toyota_sng_capability(self):
    from opendbc.car.toyota.sng import eligible
    cp = self.vehicle_params()
    if not eligible(cp):
      return None
    return (str(cp.carFingerprint), int(cp.flags), bool(cp.autoResumeSng),
            tuple((str(config.safetyModel), int(config.safetyParam)) for config in cp.safetyConfigs))

  def _apply_toyota_sng(self, request):
    if not request.confirmation or request.value not in ("Off", "On") or request.dependencies:
      return False
    def authorized():
      return (self.authority("parked_preferences") and self.authority("vehicle") and
              request.capability is not None and request.capability == self._toyota_sng_capability() and
              request.vehicle_fingerprint == self.vehicle_fingerprint() and request.expected in (None, b"0", b"1"))
    return commit_exact(self.params, key="SNGHack", max_bytes=8, expected=request.expected,
                        raw=b"1" if request.value == "On" else b"0", authorized=authorized,
                        temp_prefix=".toyota-sng-").verified

  def _auto_hold_capability(self) -> tuple | None:
    from opendbc.car.toyota.interface import toyota_auto_hold_supported
    cp = self.vehicle_params()
    try:
      if cp is None or not toyota_auto_hold_supported(cp):
        return None
      return (str(cp.carFingerprint), str(cp.brand), bool(cp.openpilotLongitudinalControl), bool(cp.pcmCruise),
              int(cp.flags), int(cp.alternativeExperience),
              tuple((str(config.safetyModel), int(config.safetyParam)) for config in cp.safetyConfigs),
              str(cp.carVin) if getattr(cp, "carVin", None) else None)
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None

  def _apply_auto_hold(self, request: FeatureSettingsRequest) -> bool:
    if not request.confirmation or request.value not in ("Off", "On") or request.dependencies:
      return False
    def authorized() -> bool:
      return (bool(request.vehicle_fingerprint) and self.vehicle_fingerprint() == request.vehicle_fingerprint and
              request.capability is not None and request.capability == self._auto_hold_capability() and
              self.authority("vehicle") and request.expected in (None, b"0", b"1"))
    return commit_exact(self.params, key="ToyotaAutoHold", max_bytes=128, raw=b"1" if request.value == "On" else b"0",
                        expected=request.expected, authorized=authorized, temp_prefix=".toyota-auto-hold-").verified

  def _toyota_radar_capability(self):
    from openpilot.starpilot.car.toyota.radar_preferences import capability
    return capability(self.vehicle_params())

  def _apply_toyota_radar(self, request: FeatureSettingsRequest) -> bool:
    from openpilot.starpilot.car.toyota.radar_preferences import CHOICES, KEY
    if not request.confirmation or request.value not in CHOICES or request.dependencies:
      return False
    def authorized():
      return (bool(request.vehicle_fingerprint) and self.vehicle_fingerprint() == request.vehicle_fingerprint and
              request.capability is not None and request.capability == self._toyota_radar_capability() and
              self.authority("toyota_radar") and request.expected in (None, b"0", b"1", b"2"))
    return commit_exact(self.params, key=KEY, max_bytes=8, raw=str(CHOICES.index(request.value)).encode(),
                        expected=request.expected, authorized=authorized, temp_prefix=".toyota-radar-").verified

  def _cruise_capability(self) -> tuple | None:
    cp = self.vehicle_params()
    if toyota_cruise_capability(cp) is not None:
      return None
    try:
      if (cp is None or not cp.carFingerprint or not self.longitudinal_available() or
          cp.notCar or cp.passive or cp.dashcamOnly):
        return None
      return (str(cp.carFingerprint), self.longitudinal_available(), bool(cp.pcmCruise and not self.configuration_longitudinal()),
              bool(cp.notCar), bool(cp.passive), bool(cp.dashcamOnly))
    except (AttributeError, TypeError, ValueError):
      return None

  def _apply_global_bool_preference(self, request: FeatureSettingsRequest) -> bool:
    if (request.key not in GLOBAL_BOOL_PREFERENCES or request.value not in ("On", "Off") or
        request.dependencies or request.capability is not None or request.vehicle_fingerprint is not None):
      return False
    if request.expected not in (None, b"0", b"1"):
      return False
    def authorized() -> bool:
      return self.authority("preferences")
    result = commit_exact(self.params, key=request.key, max_bytes=8,
                          raw=b"1" if request.value == "On" else b"0", expected=request.expected,
                          authorized=authorized, temp_prefix=".global-preference-")
    return result.committed and result.verified

  def _cruise_rows(self, configurable: bool) -> list[FeatureRow]:
    software_capability = self._cruise_capability()
    toyota_capability = toyota_cruise_capability(self.vehicle_params())
    capability = software_capability or toyota_capability
    group = "toyota_cruise" if toyota_capability is not None else "long"
    allowed = configurable and capability is not None and self.authority(group)
    unit_value, unit_raw, unit_valid = self._value("IsMetric")
    unit_valid = unit_valid and unit_value in ("0", "1") and self._readable("IsMetric")
    unit = "km/h" if unit_value == "1" else "mph"
    reason = "Requires StarPilot cruise speed control" if capability is None else "Requires a compatible cruise configuration"
    rows = []
    if toyota_capability is not None:
      value, raw, valid = self._value("ReverseCruise")
      rows.append(FeatureRow("ReverseCruise", "Swap Toyota Cruise Steps", "On" if value == "1" else "Off" if value == "0" else "Invalid saved value",
                             raw, ("Off", "On") if valid else (), available=allowed and valid,
                             reason="Short press changes set speed by 5; hold changes it by 1",
                             capability=toyota_capability, dependencies=()))
      return rows
    for key, label, default in (("CustomCruise", "Short press", 1.0), ("CustomCruiseLong", "Hold", 5.0)):
      value, raw, valid = self._value(key)
      try:
        number = float(value)
        valid = valid and self._readable(key) and math.isfinite(number) and 1.0 <= number <= 150.0
      except ValueError:
        valid = False
        number = default
      rows.append(FeatureRow(key, label, f"{number:g}" if valid else "Invalid saved value", raw,
                             step=1.0 if valid else 0.0, minimum=1.0, maximum=150.0 if unit_value == "1" else 99.0,
                             unit=unit if unit_valid else "", available=allowed and valid and unit_valid,
                             reason="Invalid saved value or units" if not valid or not unit_valid else
                                    "Used when StarPilot controls cruise speed" if allowed else reason,
                             capability=capability, dependencies=(("IsMetric", unit_raw),),
                             display_unit=unit if unit_valid else "", default_value=str(default)))
    return rows

  def _apply_cruise(self, request: FeatureSettingsRequest) -> bool:
    key = request.key
    if key not in CRUISE_KEYS or not request.confirmation:
      return False
    def authorized() -> bool:
      if (not request.vehicle_fingerprint or self.vehicle_fingerprint() != request.vehicle_fingerprint or
          request.capability is None or request.capability != (self._cruise_capability() or toyota_cruise_capability(self.vehicle_params())) or
          not self.authority("long")):
        return False
      raw, readable = read_saved(self.params, key, 128)
      if not readable or raw != request.expected:
        return False
      if self._cruise_capability() is None:
        return False
      units, units_readable = read_saved(self.params, "IsMetric", 8)
      return (units_readable and units in (None, b"0", b"1") and
              request.dependencies == (("IsMetric", units),) and
              request.display_unit == ("km/h" if units == b"1" else "mph"))
    if not authorized():
      return False
    try:
      number = float(request.value)
    except ValueError:
      return False
    upper = 150.0 if request.display_unit == "km/h" else 99.0
    if not math.isfinite(number) or not 1.0 <= number <= upper:
      return False
    encoded = str(number).encode()
    return commit_exact(self.params, key=key, max_bytes=128, raw=encoded, expected=request.expected,
                        authorized=authorized, temp_prefix=".cruise-interval-").verified

  def _apply_reverse_cruise(self, request):
    if request.value not in ("On", "Off"):
      return False
    def authorized():
      cap = toyota_cruise_capability(self.vehicle_params())
      return bool(cap is not None and request.capability == cap and self.authority("toyota_cruise") and
                  request.vehicle_fingerprint == self.vehicle_fingerprint() and request.dependencies == ())
    return commit_exact(self.params, key="ReverseCruise", max_bytes=8, expected=request.expected,
                        raw=b"1" if request.value == "On" else b"0", authorized=authorized,
                        temp_prefix=".toyota-cruise-").verified

  def _dependents(self, *keys: str) -> tuple[tuple[str, bytes | None], ...]:
    return tuple((key, self._raw(key)) for key in keys)

  @staticmethod
  def _finite(raw: bytes | None) -> float | None:
    if raw is None:
      return None
    try:
      value = float(raw)
      return value if math.isfinite(value) else None
    except (ValueError, OverflowError):
      return None

  def _aol_threshold(self) -> tuple[float | None, bool, bytes | None, bytes | None]:
    canonical, legacy = self._raw(AOL_THRESHOLD), self._raw(AOL_LEGACY_THRESHOLD)
    source = canonical if canonical is not None else legacy
    value = 0.0 if source is None else self._finite(source)
    return value, value is not None and 0.0 <= value <= 100.0, canonical, legacy

  def _read_saved(self, key: str, limit: int) -> tuple[bytes | None, bool]:
    # One assembly observes one complete source per key. Actions and the next
    # assembly always read the filesystem again, including unreadable sources.
    reads = self._snapshot_reads.get()
    def read():
      try:
        return read_saved(self.params, key, limit)
      except UnknownKeyName:
        return b"", False
    if reads is None:
      return read()
    identity = (key, limit)
    if identity not in reads:
      reads[identity] = read()
    return reads[identity]

  def _raw(self, key: str) -> bytes | None:
    limit = 65536 if key == PERSONALITY_PROFILES_PARAM else 16384 if key == DOCUMENT_KEY else \
      CURVE_DOCUMENT_LIMIT if key in (CURVE_DOCUMENT_KEY, CURVE_LEGACY_KEY) else 128
    return self._read_saved(key, limit)[0]

  def _readable(self, key: str) -> bool:
    limit = 65536 if key == PERSONALITY_PROFILES_PARAM else 16384 if key == DOCUMENT_KEY else \
      CURVE_DOCUMENT_LIMIT if key in (CURVE_DOCUMENT_KEY, CURVE_LEGACY_KEY) else 128
    return self._read_saved(key, limit)[1]

  def _default(self, key: str) -> str:
    if key == "AlwaysOnLateral":
      from openpilot.starpilot.aol.defaults import default_enabled
      return "1" if default_enabled(self.vehicle_params()) else "0"
    if key in BOOL_DEFAULTS:
      return "1" if BOOL_DEFAULTS[key] else "0"
    if key in ("CustomCruise", "CustomCruiseLong"):
      return "1.0" if key == "CustomCruise" else "5.0"
    if key == SLC_PRIORITY:
      return "Vision"
    if key == "IsMetric":
      return "0"
    if key in SLC_OFFSET_KEYS or key in ("ForceStopDistanceOffset", "SLCAdvanceSeconds"):
      return "0"
    value = self.params.get_default_value(key)
    return str(value) if value is not None else ""

  def _value(self, key: str) -> tuple[str, bytes | None, bool]:
    try:
      raw = self._raw(key)
      value = self._default(key) if raw is None else raw.decode("utf-8")
      return value, raw, True
    except UnicodeDecodeError:
      return "invalid saved value", raw, False
    except (OSError, ValueError, UnknownKeyName):
      return "invalid saved value", b"", False

  def _bool_row(self, key: str, label: str, allowed: bool, reason: str = "") -> FeatureRow:
    value, raw, valid = self._value(key)
    return FeatureRow(key, label, value if value not in ("0", "1") else ("On" if value == "1" else "Off"), raw,
                      ("Off", "On") if valid and value in ("0", "1") else (), available=allowed and valid and value in ("0", "1"),
                      default_value="On" if self._default(key) == "1" else "Off",
                      reason="Invalid saved value" if not valid or value not in ("0", "1") else
                      reason if allowed else "Unavailable with the current vehicle or feature settings")

  def _number_row(self, key: str, label: str, allowed: bool, *, scale: float = 1.0, unit: str | None = None) -> FeatureRow:
    low, high, step, stored_unit = FLOAT_SPECS[key]
    value, raw, valid = self._value(key)
    try:
      number = float(value)
      valid = valid and math.isfinite(number) and low <= number <= high
    except ValueError:
      valid = False
      number = 0.0
    try:
      default = float(self._default(key))
      default_value = str(round(default * scale, 3)) if math.isfinite(default) and low <= default <= high else None
    except (ValueError, TypeError, UnknownKeyName):
      default_value = None
    return FeatureRow(key, label, str(round(number * scale, 3)) if valid else f"Saved {value}", raw,
                      step=step * scale if valid else 0.0, minimum=low * scale, maximum=high * scale,
                      unit=unit or stored_unit, available=allowed and valid,
                      default_value=default_value,
                      reason="Invalid saved value" if not valid else "" if allowed else "Unavailable with the current vehicle or feature settings")

  def _with_default(self, row: FeatureRow) -> FeatureRow:
    if row.default_value is not None or not row.key or not (row.choices or row.step):
      return row
    try:
      value = self._default(row.key)
      if row.key in (SLC_PRIORITY, SLC_SECONDARY) and value not in self._source_choices():
        return row
      if value in ("0", "1") and row.choices == ("Off", "On"):
        value = "On" if value == "1" else "Off"
      if row.choices and value not in row.choices or not value:
        return row
      if row.step and not row.minimum <= float(value) <= row.maximum:
        return row
      return replace(row, default_value=value)
    except (ValueError, TypeError, UnknownKeyName):
      return row

  def _document(self) -> tuple[dict | None, bytes | None, bool]:
    saved = read_document_value(self.params)
    return saved.value if isinstance(saved.value, dict) else None, saved.raw, saved.valid

  def _lateral_pause_capability(self) -> tuple | None:
    try:
      cp = self.vehicle_params()
      if (cp is None or cp.notCar or cp.passive or cp.dashcamOnly or not cp.carFingerprint or
          not any(c.safetyModel not in (car.CarParams.SafetyModel.silent, car.CarParams.SafetyModel.noOutput,
                                       car.CarParams.SafetyModel.allOutput) for c in cp.safetyConfigs)):
        return None
      return (str(cp.carFingerprint), str(cp.steerControlType),
              tuple((str(c.safetyModel), int(c.safetyParam)) for c in cp.safetyConfigs))
    except (AttributeError, TypeError, ValueError):
      return None

  def _lateral_pause_rows(self, configurable: bool) -> list[FeatureRow]:
    capability = self._lateral_pause_capability()
    allowed = configurable and self.authority("preferences") and capability is not None
    units, unit_raw, unit_valid = self._value("IsMetric")
    unit_valid = unit_valid and units in ("0", "1") and self._readable("IsMetric")
    common = FeatureRow("", "", "", capability=capability, vehicle_fingerprint=self.vehicle_fingerprint(),
                        dependencies=(("IsMetric", unit_raw),))
    rows = []
    for key, label, maximum, step, unit in (("PauseLateralSpeed", "Pause steering below", 100., 1., "km/h" if units == "1" else "mph"),
                                            ("LateralResumeDelay", "Steering resume delay", 5., .1, "s")):
      value, raw, valid = self._value(key)
      try:
        number = float(value)
        valid = valid and self._readable(key) and math.isfinite(number) and 0 <= number <= maximum
      except ValueError:
        valid, number = False, 0.
      rows.append(replace(common, key=key, label=label, value=f"{number:g}" if valid else "Invalid saved value", source=raw,
                             step=step if valid else 0., minimum=0., maximum=maximum, unit=unit,
                             available=allowed and unit_valid and (valid or self.authority("parked_preferences") and self._readable(key)),
                             repair_value="0" if not valid else "", default_value="0", display_unit=unit,
                             reason="Zero speed disables steering pause. Applies within one second." if key == "PauseLateralSpeed" else
                                    "After signaling off, only when speed went below half the pause threshold. Applies within one second."))
    toggle = self._bool_row("PauseLateralOnSignal", "Pause only while signaling", allowed and unit_valid)
    if not toggle.choices and self._readable("PauseLateralOnSignal") and self.authority("parked_preferences"):
      toggle = replace(toggle, available=allowed and unit_valid, repair_value="Off")
    rows.insert(1, replace(toggle, capability=common.capability, vehicle_fingerprint=common.vehicle_fingerprint,
                           dependencies=common.dependencies,
                           reason="Otherwise pauses below the threshold with or without a signal. Applies within one second."))
    return rows

  def _apply_lateral_pause(self, request: FeatureSettingsRequest) -> bool:
    def authorized():
      row = next(row for row in self._lateral_pause_rows(self.authority("preferences")) if row.key == request.key)
      return (row.available and (not row.repair_value or request.value == row.repair_value) and
              row.capability == request.capability and row.vehicle_fingerprint == request.vehicle_fingerprint and
              row.dependencies == request.dependencies and row.source == request.expected and
              row.display_unit == request.display_unit and request.related_source is None and not request.direction)
    if not authorized():
      return False
    if request.key == "PauseLateralOnSignal":
      if request.value not in ("Off", "On"):
        return False
      raw = b"1" if request.value == "On" else b"0"
    else:
      try:
        number = float(request.value)
      except ValueError:
        return False
      if not math.isfinite(number) or not 0 <= number <= (100 if request.key == "PauseLateralSpeed" else 5):
        return False
      raw = str(number).encode()
    result = commit_exact(self.params, key=request.key, max_bytes=128, raw=raw, expected=request.expected,
                          authorized=authorized, temp_prefix=".lateral-pause-")
    return result.committed and result.verified

  def _turn_assist_row(self, configurable: bool) -> FeatureRow:
    from openpilot.starpilot.lateral.controller_selection import turn_assist_supported
    cp = self.vehicle_params()
    supported = cp is not None and turn_assist_supported(cp)
    capability = ((cp.carFingerprint, cp.lateralTuning.which(), str(cp.steerControlType), cp.passive,
                   cp.dashcamOnly, cp.notCar, float(cp.minSteerSpeed),
                   tuple((str(config.safetyModel), int(config.safetyParam)) for config in cp.safetyConfigs))
                  if supported else None)
    row = self._bool_row("TurnAssist", "Turn Assist", configurable and self.authority("torque") and supported and capability is not None)
    return replace(row, capability=capability, dependencies=(),
                   reason=row.reason if row.value not in ("On", "Off") else
                          ("Uses steering-angle feedback during rolling low-speed turns, above about 0.1 mph or the vehicle minimum. " +
                           "Never at standstill. Applies next drive.") if row.available else
                          "Turn Assist is available on supported vehicles when settings can be changed")

  def _apply_turn_assist(self, request: FeatureSettingsRequest) -> bool:
    raw = self._raw("TurnAssist")
    row = self._turn_assist_row(self.authority("preferences"))
    if (request.value not in ("Off", "On") or not row.available or not row.choices or request.expected != raw or
        request.vehicle_fingerprint != self.vehicle_fingerprint() or request.capability != row.capability or
        request.dependencies != row.dependencies or request.related_source is not None or request.display_unit or request.direction):
      return False
    def authorized():
      fresh = self._turn_assist_row(self.authority("preferences"))
      return (fresh.available and fresh.capability == request.capability and fresh.dependencies == request.dependencies and
              self.vehicle_fingerprint() == request.vehicle_fingerprint)
    result = commit_exact(self.params, key="TurnAssist", max_bytes=128,
                          raw=b"1" if request.value == "On" else b"0", expected=raw,
                          authorized=authorized, temp_prefix=".turn-assist-")
    return result.committed and result.verified

  def snapshot(self, page: str, *, parked: bool, system_long: bool, lateral_context: bool, metric: bool,
               configure_while_driving: bool = False) -> FeatureSettingsState:
    selected_vehicle = self._vehicle_params_source()
    token = self._snapshot_reads.set({})
    vehicle_token = self._snapshot_vehicle.set((selected_vehicle,))
    try:
      return self._build_snapshot(page, parked=parked, system_long=system_long, lateral_context=lateral_context,
                                  metric=metric, configure_while_driving=configure_while_driving)
    finally:
      self._snapshot_vehicle.reset(vehicle_token)
      self._snapshot_reads.reset(token)

  def _build_snapshot(self, page: str, *, parked: bool, system_long: bool, lateral_context: bool, metric: bool,
                      configure_while_driving: bool = False) -> FeatureSettingsState:
    del metric  # The persisted unit file, including invalid bytes, owns offset labels.
    configurable = parked or configure_while_driving
    title = {FeaturePage.HUB: "Driving Controls", FeaturePage.LANE: "Lane Centering"}.get(page, page.replace("_", " ").title())
    rows: list[FeatureRow] = []
    if page == "data":
      title = "Data Uploads"
      rows = [self._bool_row("AlwaysAllowUploads", "Always Allow Uploads", self.authority("preferences"),
                             "Allow queued full logs and videos on metered networks. Uses mobile data.")]
    elif page == FeaturePage.HUB:
      rows = [FeatureRow("", "Curve Speed Controller", "Adjust how speed is reduced for curves ahead", page=FeaturePage.CURVE, available=True),
              FeatureRow("", "Speed Limit Controller", "Choose speed-limit sources, offsets, and confirmation behavior", page=FeaturePage.SLC, available=True),
              FeatureRow("", "Lane Centering", "Adjust lane positioning, lane detection, and steering alerts", page=FeaturePage.LANE, available=True),
              FeatureRow("", "Lane Changes", "Configure lane-change speed, automatic timing, and driver nudges", page=FeaturePage.LANE_CHANGE, available=True),
              FeatureRow("", "Long Planner", "Tune acceleration, braking, and following distances", page=FeaturePage.PROFILES, available=True),
              FeatureRow("", "Conditional Driving Modes", "Choose when to switch between Chill and Experimental", page=FeaturePage.CONDITIONAL, available=True),
              FeatureRow("", "Steering and Torque", "Select the steering controller and tune its response", page=FeaturePage.TORQUE, available=True),
              FeatureRow("", "Always On Lateral", "Keep steering assistance active independently of cruise control", page=FeaturePage.AOL, available=True),
              FeatureRow("", "Wheel Controls", "Assign steering-wheel buttons and cruise behavior", page=FeaturePage.WHEEL, available=True)]
      if (self._long_pitch_capability() is not None or self._bolt_disable_capability() is not None or
          self._auto_hold_capability() is not None or self._toyota_sng_capability() is not None or self._jeep_hold_capability() is not None or
          self._toyota_radar_capability() is not None or self._pedal_setup_capability() is not None or
          self.subaru_smoothing.capability() is not None or
          self._subaru_avh_capability() is not None or self._subaru_startup_capability() is not None or self._subaru_sng_capability() is not None or
          self._subaru_software_capability() is not None or self._gm_stop_capability("VoltSNG") is not None or
          self._gm_stop_capability("GMAutoHold") is not None or self.tesla_screen.capability() is not None or
          self.gm_tune.capability() is not None or self.gm_truck.capability() is not None or
          self.gm_ev_preset.capability() is not None or self.volt_radar.capability() is not None or
          self.panda_wake.capability() is not None or preap_radar_capability(self.vehicle_params()) is not None):
        rows.append(FeatureRow("", "Vehicle Settings", "Configure features supported by your vehicle", page=FeaturePage.VEHICLE, available=True))
      rows.append(FeatureRow("", "Driving Personalities", "Adjust Aggressive, Standard, Relaxed and Traffic profiles",
                             page=FeaturePage.PERSONALITIES, available=True))
    elif page == FeaturePage.PERSONALITIES:
      title = "Driving Personalities"
      rows = [FeatureRow("", name.title() + " Personality", "", page=name, available=True)
              for name in (*PROFILE_NAMES, "traffic")]
    elif page == FeaturePage.VEHICLE:
      title = "Vehicle Settings"
      rows = list(self._alpha_long_rows(parked))
      capability = self._auto_hold_capability()
      allowed = configurable and capability is not None and self.authority("vehicle")
      row = self._bool_row("ToyotaAutoHold", "Automatic Brake Hold", allowed,
                           "Hold the brake for over one second at a complete stop with cruise main on and cruise disengaged. " +
                           "Holds until you press the gas; normal cruise stop-and-go is unchanged. Applies after the next startup.")
      rows.extend([replace(row, available=row.available and self._readable("ToyotaAutoHold"), capability=capability)] if capability is not None else [])
      sng_capability = self._toyota_sng_capability()
      if sng_capability is not None:
        row = self._bool_row("SNGHack", "Stop-and-Go Hack",
                             configurable and parked and self.authority("vehicle") and self.authority("parked_preferences"),
                             "Automatic resume for Toyota/Lexus vehicles without factory stop-and-go. Applies after the next startup.")
        rows.append(replace(row, available=row.available and self._readable("SNGHack"), capability=sng_capability))
      radar_capability = self._toyota_radar_capability()
      if radar_capability is not None:
        from openpilot.starpilot.car.toyota.radar_preferences import CHOICES, KEY
        raw, readable = read_saved(self.params, KEY, 8)
        valid = readable and raw in (None, b"0", b"1", b"2")
        rows.append(FeatureRow(KEY, "Continental Radar", CHOICES[int(raw or b"0")] if valid else "Invalid saved choice",
          raw, CHOICES if valid else (), available=bool(valid and configurable and self.authority("toyota_radar")),
          reason=("Optional RAV4 2022/2023 receiver. Off retains the original radar choice. Applies after startup; " +
                  "native safety and ECU configuration are unchanged."),
          capability=radar_capability, default_value="Off"))
      jeep_capability = self._jeep_hold_capability()
      if jeep_capability is not None:
        row = self._bool_row("JeepBrakeHold", "Jeep Brake Hold",
                             configurable and parked and self.authority("vehicle") and self.authority("parked_preferences"),
                             "Retains a cruise-controlled stop until you press a pedal or cancel cruise. Applies after the next startup")
        rows.append(replace(row, available=row.available and self._readable("JeepBrakeHold"), capability=jeep_capability))
      rows.extend(self.tesla_screen.rows(parked))
      rows.extend(self.gm_cancel_remap.rows(parked))

      rows.extend(self.preap_radar.rows())
      rows.extend(self.subaru_smoothing.rows(parked))
      rows.extend(self.gm_tune.rows(parked))
      rows.extend(self.gm_truck.rows(parked))
      rows.extend(self.gm_ev_preset.rows(parked))
      rows.extend(self.volt_radar.rows())
      rows.extend(row for row in self.panda_wake.rows(parked) if row.key != "IgnoreIgnitionLine")
      pedal_capability = self._pedal_setup_capability()
      if pedal_capability is not None:
        from opendbc.car.gm.values import GMFlags
        detected = bool(pedal_capability[2] & GMFlags.PEDAL_LONG)
        active = detected and pedal_capability[5] and not (pedal_capability[3] or pedal_capability[4])
        value = "Automatic" if active else "Detected, inactive" if detected else "Not detected"
        reason = ("StarPilot speed control is off in this configuration" if detected and not active else
                  "Activates automatically with a supported connected interceptor")
        rows.append(FeatureRow("GMPedalLongitudinal", "Pedal Speed Control", value,
                               available=False, reason=reason,
                               capability=pedal_capability))
      bolt_capability = self._bolt_disable_capability()
      if bolt_capability is not None:
        row = self._bool_row("DisableOpenpilotLongitudinal", "Disable StarPilot Speed Control",
                             parked and self.authority("parked_preferences"),
                             "StarPilot does not control speed in this mode. Steering remains available after the next startup")
        if self._vw_alpha_off():
          row = replace(row, value="On", available=False,
                        reason="Speed control is off in driving settings. Enable Alpha Longitudinal there to restore it")
        if bolt_capability[-1] == (("noOutput", 0),):
          row = replace(row, reason="Factory camera not detected. Turn this setting off and restart to restore StarPilot speed control.")
        rows.append(replace(row, available=row.available and self._readable("DisableOpenpilotLongitudinal"),
                            capability=bolt_capability))
      avh_capability = self._subaru_avh_capability()
      if avh_capability is not None:
        row = self._bool_row("SubaruAvhStartup", "AVH On at Startup", parked and self.authority("parked_preferences"),
                             "Requests AVH ON once in Park with the engine running after the next startup.")
        rows.append(replace(row, available=row.available and self._readable("SubaruAvhStartup"), capability=avh_capability))
      startup_capability = self._subaru_startup_capability()
      if startup_capability is not None:
        row = self._bool_row("SubaruStopStartOff", "Stop/Start Off at Startup", parked and self.authority("parked_preferences"),
                             "Requests Stop/Start OFF once while stationary in Park or Neutral after the next startup.")
        rows.append(replace(row, available=row.available and self._readable("SubaruStopStartOff"), capability=startup_capability))
      subaru_sng_capability = self._subaru_sng_capability()
      if subaru_sng_capability is not None:
        row = self._bool_row("SubaruSNG", "Stop-and-Go Assistance", parked and self.authority("parked_preferences"),
                             "Assists lead departure and standstill hold with factory ACC. Applies after the next startup.")
        rows.append(replace(row, available=row.available and self._readable("SubaruSNG"), capability=subaru_sng_capability))
      software_capability = self._subaru_software_capability()
      if software_capability is not None:
        row = self._bool_row("SubaruRedneckCruise", "Software Cruise Control", parked and self.authority("parked_preferences"),
                             "Adjust stock cruise speed using Resume and Set. Applies after the next startup; factory ACC keeps pedal control.")
        rows.append(replace(row, available=row.available and self._readable("SubaruRedneckCruise"), capability=software_capability))
      sng_capability = self._gm_stop_capability("VoltSNG")
      if sng_capability is not None:
        row = self._bool_row("VoltSNG", "Volt Stop-and-Go Assistance", parked and self.authority("parked_preferences"),
                             "Assists automatic resume from a cruise-controlled stop. Requires StarPilot speed control; applies after the next startup")
        rows.append(replace(row, available=row.available and self._readable("VoltSNG"), capability=sng_capability))
      one_pedal_capability = self._gm_stop_capability("VoltOnePedalMode")
      if one_pedal_capability is not None:
        row = self._bool_row("VoltOnePedalMode", "Volt One-Pedal Driving", parked and self.authority("parked_preferences"),
                             "With cruise main on and cruise disengaged, slows and holds when you lift off the accelerator in Low or single-pedal mode. " +
                             "Requires StarPilot speed control; applies after the next startup")
        rows.append(replace(row, available=row.available and self._readable("VoltOnePedalMode"), capability=one_pedal_capability))
      gm_hold_capability = self._gm_stop_capability("GMAutoHold")
      if gm_hold_capability is not None:
        from opendbc.car.gm.values import CAR
        release = "gas" if gm_hold_capability[0] == CAR.BUICK_LACROSSE else "gas or regen paddle"
        row = self._bool_row("GMAutoHold", "Automatic Brake Hold", parked and self.authority("parked_preferences"),
                             f"Holds the brakes at a stop with cruise main on. Press the {release} to release. " +
                             "Requires StarPilot speed control and applies after the next startup")
        rows.append(replace(row, available=row.available and self._readable("GMAutoHold"), capability=gm_hold_capability))
      pitch_capability = self._long_pitch_capability()
      if pitch_capability is not None:
        row = self._bool_row("LongPitch", "Grade Compensation", configurable and self.authority("preferences"),
                             "Compensates acceleration and braking for road grade. Refreshes during the drive.")
        rows.append(replace(row, available=row.available and self._readable("LongPitch"), capability=pitch_capability))
      cp = self.vehicle_params()
      policy = self.aol_settings_policy(cp)
      capability = self._capability("aol")
      if policy.paddle_pause:
        nostalgia, source, valid = self._value("NostalgiaMode")
        readable = self._readable("NostalgiaMode")
        nostalgia_allowed = configurable and self.authority("aol") and capability is not None and readable
        rows.append(FeatureRow("NostalgiaMode", "Left Paddle Longitudinal Pause",
                               "On" if nostalgia == "1" else "Off" if nostalgia == "0" else "Invalid saved value",
                               source, ("Off", "On") if valid and nostalgia in ("0", "1") else (),
                               available=nostalgia_allowed,
                               reason=("Pause acceleration and braking while Always On Lateral keeps steering"
                                       if valid and nostalgia in ("0", "1") else
                                       "Choose Off to repair the saved preference"),
                               capability=capability, repair_value="Off" if nostalgia not in ("0", "1") else ""))
    elif page in (FeaturePage.CONDITIONAL, FeaturePage.CONDITIONAL_CEM, FeaturePage.CONDITIONAL_CCM):
      title = "Conditional Driving Modes" if page == FeaturePage.CONDITIONAL else \
              "Experimental Conditions" if page == FeaturePage.CONDITIONAL_CEM else "Chill Conditions"
      rows.extend(self.conditional.rows(page, parked))
    elif page == FeaturePage.CURVE:
      title = "Curve Speed Controller"
      allowed = configurable and system_long and self.authority("long")
      vehicle_cp = self.vehicle_params()
      curve_supported = feature_longitudinal_supported(vehicle_cp)
      learning = learning_snapshot(self.params)
      document_raw = self._raw(CURVE_DOCUMENT_KEY)
      dependencies = ((CURVE_DOCUMENT_KEY, document_raw),)
      if document_raw is None:
        dependencies += ((CURVE_LEGACY_KEY, self._raw(CURVE_LEGACY_KEY)),)
      master, _, _ = self._value(CURVE_MASTER_KEY)
      master_row = self._bool_row(CURVE_MASTER_KEY, "Curve Speed Controller", allowed and (learning.valid or master == "1"))
      rows.append(replace(master_row, dependencies=dependencies,
                          reason=master_row.reason if master_row.value not in ("On", "Off") else
                                 "Invalid saved learning; turn off without changing it" if not learning.valid else
                                 "Adjusts speed for curves while StarPilot controls acceleration and braking" if allowed and curve_supported else
                                 "Curve Speed control is not supported for this vehicle yet" if allowed else master_row.reason))
      no_lead_row = self._bool_row(CURVE_NO_LEAD_KEY, "Pause while following a lead", allowed and learning.valid)
      rows.append(replace(no_lead_row, dependencies=dependencies,
                          reason=no_lead_row.reason if no_lead_row.value not in ("On", "Off") else
                                 "Invalid saved learning" if not learning.valid else
                                 "Pauses curve speed adjustments while following a lead vehicle" if allowed else
                                 "Unavailable with the current vehicle or feature settings"))
      rows.append(self._bool_row("ShowCSCStatus", "Show Curve status", allowed,
                                 reason="Display preference only; it does not enable control"))
      rows.append(FeatureRow("", "Saved learning", "Valid" if learning.valid else "Unavailable",
                             reason="Used automatically when Curve Speed Controller is on" if learning.valid else
                                    "Saved data remains untouched; runtime control is disabled"))
      rows.append(FeatureRow("", "Learning progress", f"{learning.progress:.1f}%" if learning.progress is not None else "Unavailable",
                             reason="Saved coverage across curve types, learned while you control speed without a lead vehicle"
                                    if learning.progress is not None else learning.reason))
      rows.append(FeatureRow("", "Saved cornering comfort",
                             f"{learning.comfort:.2f} m/s²" if learning.has_samples and learning.comfort is not None else
                             "No learned samples" if learning.valid else "Unavailable",
                             reason="Saved calibration, not a live reading" if learning.valid else learning.reason))
      rows.append(FeatureRow("curve_reset", "Reset Saved Curve Learning", "", learning.sources[0][1],
                             available=parked and allowed and learning.resettable,
                             reason="" if learning.resettable else "No saved learning" if learning.valid and not learning.has_samples else
                                    "Park to reset saved learning" if not parked else "Saved learning is currently in use",
                             vehicle_fingerprint=self.vehicle_fingerprint(), dependencies=learning.sources))
    elif page == FeaturePage.TORQUE:
      title = "Steering and Torque"
      rows.extend(FordBluePilotFeature(self).rows(parked))
      rows.extend(FordClusterFeature(self).rows(parked))
      controller_row = self.controller.row()
      if controller_row is not None:
        rows.append(controller_row)
      rows.append(self.lane_changes.guidance_row(parked))
      from openpilot.starpilot.lateral.controller_selection import turn_assist_supported
      cp = self.vehicle_params()
      if cp is not None and turn_assist_supported(cp):
        rows.append(self._turn_assist_row(configurable))
      setup_row = self.controller.setup_row()
      if setup_row is not None:
        rows.append(setup_row)
      capability = self._capability("torque")
      allowed = configurable and self.authority("torque") and capability is not None
      _, numeric_rows = self.torque.rows(capability, allowed, repair_allowed=parked and allowed)
      learning_row = self.controller.learning_row()
      if learning_row is not None:
        rows.append(learning_row)
      rows.extend(numeric_rows)
    elif page == FeaturePage.AOL:
      title = "Always On Lateral"
      rows.extend(self._lateral_pause_rows(configurable))
      capability = self._capability("aol")
      vehicle_cp = self.vehicle_params()
      policy = self.aol_settings_policy(vehicle_cp)
      pending = policy.fixed_cruise_buttons and not policy.runtime_supported
      threshold, threshold_valid, canonical, legacy = self._aol_threshold()
      units, _, unit_valid = self._value("IsMetric")
      unit_raw = self._raw("IsMetric")
      unit_valid = unit_valid and units in ("0", "1")
      unit = "km/h" if units == "1" else "mph"
      multiplier = 3.6 if units == "1" else 1 / MPH_TO_MPS
      master, _, master_valid = self._value("AlwaysOnLateral")
      allowed = configurable and self.authority("aol") and capability is not None
      rows.append(replace(self._bool_row("AlwaysOnLateral", "Enable Always On Lateral",
                                         (allowed and threshold_valid) or
                                         (master_valid and master == "1" and self.authority("preferences"))), capability=capability,
                          dependencies=self._dependents(AOL_THRESHOLD, AOL_LEGACY_THRESHOLD),
                          reason="Invalid saved master preference" if not master_valid or master not in ("0", "1") else
                                 "Connect a supported vehicle to configure Always On Lateral" if capability is None else
                                 "Invalid saved brake threshold" if not threshold_valid else
                                 "Applies after the next startup" if pending else
                                 "Keep steering assistance available independently of cruise control"))
      threshold_available = allowed and unit_valid and all(self._readable(key) for key in
                                                          (AOL_THRESHOLD, AOL_LEGACY_THRESHOLD, "IsMetric"))
      source_label = "Current saved threshold" if canonical is not None else "Prior saved threshold" if legacy is not None else "Default threshold"
      rows.append(FeatureRow(AOL_THRESHOLD, "Brake pause below", str(round((threshold or 0.0) * multiplier, 2)) if threshold_valid and unit_valid else
                             "Invalid saved threshold" if not threshold_valid else "Invalid saved units", canonical,
                             step=1.0 if threshold_valid and unit_valid else 0.0, minimum=0.0, maximum=round(100 * multiplier, 2),
                             unit=unit if unit_valid else "", available=threshold_available,
                             reason=source_label if threshold_valid else "Choose zero to repair the saved threshold",
                             capability=capability,
                             dependencies=(("IsMetric", unit_raw), (AOL_LEGACY_THRESHOLD, legacy),
                                           ("AlwaysOnLateral", self._raw("AlwaysOnLateral"))), display_unit=unit if unit_valid else "",
                             repair_value="0" if not threshold_valid and unit_valid else "", default_value="0"))
    elif page == FeaturePage.WHEEL:
      title = "Wheel Controls"
      if self.show_cruise_intervals or toyota_cruise_capability(self.vehicle_params()) is not None:
        rows.extend(self._cruise_rows(configurable))
      rows.extend(self.wheel.rows())
    elif page == FeaturePage.SLC:
      system_long = (system_long if self.vehicle_params() is None else self.longitudinal_available()) and self.authority("slc")
      allowed = configurable and system_long
      offsets_ready, offset_rows = self.slc_offsets.rows(allowed, repair_allowed=parked and allowed)
      control, _, _ = self._value("SpeedLimitController")
      if self.vehicle_params() is None:
        control_reason = "Select or connect a vehicle to configure speed control"
      elif not self.longitudinal_available():
        control_reason = "StarPilot speed control is unavailable; check the vehicle and longitudinal settings"
      elif not self.authority("slc"):
        control_reason = "Vehicle settings context is unavailable; refresh this page"
      elif not configurable:
        control_reason = "Park to configure Speed Limit Controller"
      elif not offsets_ready:
        control_reason = "Repair invalid saved speed-limit offsets or units before enabling control"
      else:
        control_reason = "On adjusts cruise speed to accepted limits; Off keeps speed control unchanged"
      control_row = self._bool_row("SpeedLimitController", "Speed Limit Controller",
                                   allowed and (offsets_ready or control == "1"), reason=control_reason)
      rows.append(replace(control_row, reason=control_reason) if control_row.choices else control_row)
      rows.append(self._bool_row("ShowSpeedLimits", "Show speed limit signs", self.authority("preferences"),
                                 reason="Display signs independently of Speed Limit Controller; does not change cruise speed"))
      confirmation = self._bool_row("SLCConfirmation", "Automatic adoption", allowed,
        reason="On adopts new limits immediately; Off asks for confirmation within 10 seconds")
      rows.append(replace(confirmation, value={"On": "Off", "Off": "On"}.get(confirmation.value, confirmation.value), default_value="On"))
      advance, raw_advance, readable_advance = self._value('SLCAdvanceSeconds')
      valid_advance = readable_advance and advance in tuple(str(seconds) for seconds in range(11))
      rows.append(FeatureRow('SLCAdvanceSeconds', 'Adopt limits early',
        SLC_ADVANCE_CHOICES[int(advance)] if valid_advance else f'Saved {advance}', raw_advance,
        SLC_ADVANCE_CHOICES if valid_advance else ('Off',), available=allowed, default_value='Off',
        reason='Offer or adopt mapped limits this many seconds early at the current speed; applies to increases and decreases'
          if valid_advance else 'Invalid saved advance time; restore Off'))
      source_capability = self._source_capability()
      vision_dev, maps_available = source_capability
      source_allowed = configurable and self.authority("preferences")
      choices = self._source_choices(source_capability)
      for key, label, help_text in (
          (SLC_PRIORITY, "Primary source", "Use this source first; try the secondary source when it is unavailable"),
          (SLC_SECONDARY, "Secondary source", "Use this source when the primary source is unavailable")):
        source, raw, valid = self._value(key)
        source = "Maps" if source == "Map Data" else source
        readable = self._readable(key)
        reason = "Saved source unreadable or too large" if not readable else help_text
        if source == "Maps" and not maps_available:
          reason = "Maps needs a verified provider and an installed offline map package"
        elif source == "Vision" and not vision_dev:
          reason = "Camera signs are unavailable; choose a connected source"
        rows.append(FeatureRow(key, label, source if readable else "Unavailable saved source", raw,
          (source, *choices) if valid and source not in choices else choices,
          available=source_allowed and valid and readable and (vision_dev or maps_available or source != "Dashboard"),
          reason=reason, capability=source_capability))
      rows.extend(offset_rows)
      title = "Speed Limit Controller"
    elif page == FeaturePage.LANE:
      from openpilot.starpilot.lateral.lane_runtime import runtime_supported
      strength_supported = runtime_supported(self.vehicle_params())
      allowed = strength_supported and ((configurable and lateral_context) or (not parked and self.authority("lane_live")))
      lane_enabled, _, _ = self._value("LaneCentering")
      rows.extend((self._bool_row("LaneCentering", "Enable Lane Centering", allowed),
                   replace(self._bool_row("LaneCenteringPauseOnSignal", "Pause on signal", allowed and lane_enabled == "1"),
                           default_reset_available=allowed and parked and self._value("LaneCenteringPauseOnSignal")[2] and
                           self._value("LaneCenteringPauseOnSignal")[0] in ("0", "1")),
                   self._number_row("LaneCenterOffset", "Lane offset", allowed),
                   replace(self._number_row("LaneCenteringE2EAuthority", "Model path preference", allowed, scale=100, unit="%"),
                           reason="Higher values let a confident model path reduce lane-centering correction when it differs from the lane center")))
      strength_row = self._number_row("LaneCenteringStrength", "Lane Centering Strength",
                                      allowed and strength_supported, scale=100 / STRENGTH_BASELINE, unit="%")
      rows.append(replace(strength_row,
                          value=str(round(float(strength_row.value))) if strength_row.step > 0 else strength_row.value,
                          default_value=str(round(float(strength_row.default_value))) if strength_row.default_value is not None else None,
                          minimum=33, maximum=round(STRENGTH_MAX / STRENGTH_BASELINE * 100), step=1 if strength_row.step > 0 else 0,
                          reason="100% is normal; lower values reduce centering and higher values strengthen it" if strength_supported else
                          "Not available with this vehicle configuration"))
    elif page == FeaturePage.LANE_CHANGE:
      title = "Lane Changes"
      rows.extend(self.lane_changes.rows(parked))
      rows.extend(self.conditional.rows(page, parked))
    elif page == FeaturePage.PROFILES:
      title = "Long Planner"
      rows.append(self._bool_row(PLANNER_SELECTION_KEY, "Use StarPilot Longitudinal Planner", self.authority("preferences"),
                                 reason="Off uses the upstream planner. Supported longitudinal control stays active. Changes apply on the next drive."))
      allowed = configurable and system_long
      force_enabled, _, _ = self._value("ForceStops")
      rows.append(self._bool_row("ForceStops", "Force Stop", allowed or
                                 (force_enabled == "1" and self.authority("preferences")),
                                 reason="Commit to detected model stops; press the accelerator to continue"))
      offset_row = self._number_row("ForceStopDistanceOffset", "Stop Distance Adjustment", allowed and force_enabled == "1")
      rows.append(replace(offset_row, default_reset_available=allowed and parked and offset_row.step > 0,
                          reason="Positive values stop farther forward; negative values stop earlier"))
      approach_row = self._bool_row(LEAD_APPROACH_KEY, "Approaching lead buffer",
                                    self.authority("preferences"),
                                    reason="Gradually adds following distance when approaching a slower or braking lead")
      rows.append(approach_row)
      rows.append(self._bool_row(LEAD_TAKEOFF_KEY, "Faster Takeoffs", self.authority("preferences"),
                                reason="Improves launches from a stop and responds sooner when a lead starts moving. " +
                                       "Selected acceleration profiles, stop detection and vehicle limits still apply."))
      rows.append(self._bool_row(NUDGE_KEY, "Experimental Speed Nudge", self.authority("preferences"),
                                reason="Gently recovers toward set speed when Experimental mode hesitates on a clear road. " +
                                       "Leads, stops, curve limits and driver overrides retain priority."))
      if self._galaxy():
        rows.append(self._bool_row("DisengageOnAccelerator", "Disengage on Accelerator Pedal", self.authority("preferences")))
      document, raw, valid = self._document()
      for category, field, label, choices in (
        ('acceleration', 'selectedAccelerationProfile', 'Selected Acceleration Profile',
         ('dom_default', 'standard', 'eco', 'sport', 'sport_plus')),
        ('braking', 'selectedDecelerationProfile', 'Selected Deceleration Profile',
         ('dom_default', 'standard', 'eco', 'sport')),
      ):
        selected = document[field] if document is not None else ('dom_default' if category == 'acceleration' else DEFAULT_DECELERATION_PROFILE)
        rows.append(FeatureRow('profile:global_' + category, label,
                               preset_label(selected) if valid else 'Invalid document', raw,
                               tuple(preset_label(choice) for choice in choices) if valid else (),
                               available=allowed and valid,
                               reason=(CATEGORY_HELP[category] if category == 'braking' else
                                       'Personalities set to Selected Profile follow this choice; explicit overrides stay unchanged.'),
                               default_value=preset_label('dom_default' if category == 'acceleration' else DEFAULT_DECELERATION_PROFILE)))
      unit_value, unit_raw, unit_valid = self._value("IsMetric")
      unit_valid = unit_valid and unit_value in ("0", "1") and self._readable("IsMetric")
      scale = 1.0 if unit_value == "1" else 1.0 / 0.3048
      gap = document.get("extraStoppedGapM", 0.0) if document is not None else 0.0
      rows.append(FeatureRow("profile:stopped_gap", "Extra stopped-lead distance", str(round(gap * scale, 3)), raw,
                             step=0.1 if unit_value == "1" else 0.5, minimum=0., maximum=round(MAX_EXTRA_STOPPED_GAP_M * scale, 3),
                             unit="m" if unit_value == "1" else "ft", available=allowed and valid and unit_valid,
                             dependencies=(("IsMetric", unit_raw),), display_unit="m" if unit_value == "1" else "ft",
                             default_value="0", reason="Adds space behind slow or stopped leads with the StarPilot planner; zero keeps the normal gap."))
      health = read_profile_health(self.params)
      master = health.values["CustomPersonalities"]
      master_row = self._bool_row("CustomPersonalities", "Custom Following and Jerk",
                                  allowed and (health.dependencies_valid or master.value is True))
      reason = ("Invalid saved profile settings; restore defaults below" if not health.dependencies_valid else
                "Enables saved following and jerk settings, plus preserved legacy curves. New acceleration/deceleration selections apply independently.")
      rows.append(replace(master_row, related_source=raw, dependencies=self.long_profiles.dependencies(health),
                          capability=self.long_profiles.capability(),
                          reason=reason if master.valid else "Invalid saved master preference"))
      if not master.valid:
        rows.append(FeatureRow("long_repair:CustomPersonalities", "Restore saved profiles switch to Off",
                               "Invalid saved switch", master.raw,
                               available=parked and allowed and master.readable and self.long_profiles.capability() is not None,
                               capability=self.long_profiles.capability(), dependencies=self.long_profiles.dependencies(health)))
      if not valid:
        rows.append(FeatureRow("reset_profiles", "Invalid profile document", "", raw,
                               available=parked and allowed and health.values[PERSONALITY_PROFILES_PARAM].readable,
                               reason="Resets saved curves and presets" if health.values[PERSONALITY_PROFILES_PARAM].readable else
                                      "Saved document unreadable or too large"))
    elif page == FeaturePage.TRAFFIC:
      title = "Traffic Profile"
      rows.extend(self.traffic_profiles.rows(configurable, system_long, repair_allowed=parked))
      for category in PRESETS:
        rows.append(FeatureRow("", category.title(), ""))
        rows.extend(self.snapshot(f"traffic/{category}", parked=parked, system_long=system_long, lateral_context=lateral_context, metric=False,
                                  configure_while_driving=configure_while_driving).rows)
    elif page in PROFILE_NAMES:
      allowed = configurable and system_long
      name = page.title()
      health = read_profile_health(self.params)
      for suffix, label in (
        ("Follow", "Low-speed follow"), ("FollowHigh", "High-speed follow"),
        ("JerkAcceleration", "Acceleration jerk"), ("JerkDeceleration", "Deceleration jerk"),
        ("JerkSpeed", "Speed jerk"), ("JerkSpeedDecrease", "Speed decrease jerk"), ("JerkDanger", "Danger jerk")):
        key = name + suffix
        number_row = self._number_row(key, label, allowed)
        rows.append(replace(number_row, reason=VALUE_HELP[suffix] if number_row.available else number_row.reason)
                    if health.values[key].readable else
                    FeatureRow(key, label, "Invalid saved value", health.values[key].raw,
                               reason="Saved source unreadable or too large"))
      rows.extend(self.long_profiles.rows(page, health, allowed, repair_allowed=parked and allowed))
      for category in PRESETS:
        rows.append(FeatureRow("", category.title(), ""))
        rows.extend(self.snapshot(f"{page}/{category}", parked=parked, system_long=system_long, lateral_context=lateral_context, metric=False,
                                  configure_while_driving=configure_while_driving).rows)
    elif "/" in page:
      name, category = page.split("/", 1)
      if name in DOCUMENT_PROFILE_NAMES and category in PRESETS:
        title = f"{name.title()} {category.title()}"
        document, raw, valid = self._document()
        seed_from_named_acceleration = (name in PROFILE_NAMES and category == "acceleration" and valid and
                                        document is not None and document["profiles"][name][category]["preset"] not in
                                        ("dom_default", "custom") and
                                        not document["profiles"][name][category]["curve"])
        capability = (self.traffic_profiles.capability() if name == "traffic" else
                      self.long_profiles.curve_capability() if seed_from_named_acceleration else None)
        traffic_dependencies, traffic_readable = self.traffic_profiles._sources() if name == "traffic" else ((), True)
        allowed = configurable and system_long and valid and (name != "traffic" or
                                                      capability is not None and self.authority("long") and traffic_readable)
        config = (document["profiles"][name][category] if document is not None else
                  default_personality_profiles(False, False)[name][category])
        choices = (tuple(choice for choice in PRESETS[category] if choice != "custom")
                   if seed_from_named_acceleration and capability is None else PRESETS[category])
        rows.append(FeatureRow(f"{LONG_PREFIX}{name}:{category}", "Preset", preset_label(config["preset"]) if valid else "Invalid document",
                               raw, tuple(preset_label(choice) for choice in choices) if valid else (),
                               available=allowed, capability=capability, dependencies=traffic_dependencies, reason=CATEGORY_HELP[category],
                               default_value=preset_label(default_personality_profiles(False, False)[name][category]["preset"])))
        if config["preset"] == "custom" and valid:
          curve = config["curve"]
          cp = self.vehicle_params()
          ev = cp is not None and cp.transmissionType == car.CarParams.TransmissionType.direct
          truck = is_truck_fingerprint(self.vehicle_fingerprint() or "") and not ev
          if name != "traffic":
            default_curve = personality_reference_curves(ev, truck)[name][category]
          elif category == "acceleration":
            default_curve = [interpolate_accel_profile(speed * MPH_TO_MPS, A_CRUISE_MAX_VALS_TRAFFIC_ALL)
                             for speed in ACCELERATION_SPEEDS_MPH]
          elif category == "braking":
            default_curve = [TRAFFIC_CRUISE_BRAKE_MAGNITUDE] * len(ACCELERATION_SPEEDS_MPH)
          else:
            low = float(self.params.get_default_value("TrafficFollow"))
            high = float(self.params.get_default_value("RelaxedFollow"))
            default_curve = [low + (high - low) * min(speed * MPH_TO_MPS / 25.0, 1.0)
                             for speed in FOLLOWING_SPEEDS_MPH]
          for index, speed in enumerate(range(0, 91, 10)):
            low, high = ((0.35, CURVE_BOUNDS[category][1]) if name == "traffic" and category == "braking" else
                         CURVE_BOUNDS[category])
            point = curve[index]
            unit = "s" if category == "following" else "m/s²"
            rows.append(FeatureRow(f"{LONG_PREFIX}{name}:{category}:{index}", f"{speed} mph point", str(point), raw,
                                   step=0.05, minimum=low, maximum=high, unit=unit,
                                   available=allowed,
                                   reason="Following time; higher values leave more space" if category == "following" else
                                          "Acceleration limit; higher values allow stronger acceleration" if category == "acceleration" else
                                          "Braking magnitude; higher values allow stronger braking",
                                   capability=capability, dependencies=traffic_dependencies,
                                   default_value=str(round(max(low, min(high, default_curve[index])), 4))))
    fingerprint = self.vehicle_fingerprint()
    rows = [replace(row, vehicle_fingerprint=None if row.key in GLOBAL_BOOL_PREFERENCES or
                    row.key in (PLANNER_SELECTION_KEY, "ShowSpeedLimits", "AlwaysAllowUploads") or
                    row.key.startswith('conditional:') and not row.key.startswith(BUTTON_PREFIX) else fingerprint)
            for row in rows]
    subtitle = (
        "Configure features supported by this vehicle. Restart requirements are shown with each setting."
        if page == FeaturePage.VEHICLE else
        "Configure automatic Chill and Experimental switching."
        if page in (
            FeaturePage.CONDITIONAL,
            FeaturePage.CONDITIONAL_CEM,
            FeaturePage.CONDITIONAL_CCM,
        ) else
        "Changes apply during this drive. Pace and trigger changes wait until the current signal and maneuver end."
        if page == FeaturePage.LANE_CHANGE else
        "Adjust these preferences for your vehicle."
    )
    return FeatureSettingsState(page, title, subtitle, tuple(self._with_default(row) for row in rows), parked)

  def apply(self, request: FeatureSettingsRequest, *, reset_default: bool = False) -> bool:
    if reset_default and not self.authority("parked_preferences"):
      return False
    # A request may arrive through a callback during assembly. Its compare and
    # authorization checks must still observe current sources independently.
    token = self._snapshot_reads.set(None)
    vehicle_token = self._snapshot_vehicle.set(None)
    try:
      return self._apply(request, reset_default=reset_default)
    finally:
      self._snapshot_vehicle.reset(vehicle_token)
      self._snapshot_reads.reset(token)

  def _apply(self, request: FeatureSettingsRequest, *, reset_default: bool = False) -> bool:
    key = request.key
    if key == "AlwaysOnLateral" and request.value == "Off":
      def authorized_off():
        return (self.authority("preferences") and self._readable(key) and
                self._raw(key) == request.expected and request.expected in (None, b"0", b"1"))
      result = commit_exact(self.params, key=key, max_bytes=8, raw=b"0", expected=request.expected,
                            authorized=authorized_off, temp_prefix=".aol-off-")
      return result.committed and result.verified
    if key.startswith(WHEEL_PREFIX):
      return self.wheel.apply(request)
    if key == OUTPUT_MAX_KEY:
      return False
    if key == "ReverseCruise":
      return self._apply_reverse_cruise(request)
    if key in LATERAL_PAUSE_KEYS:
      return self._apply_lateral_pause(request)
    if key == "FordHandsFreeCluster":
      return FordClusterFeature(self).apply(request)
    if key in FORD_BLUEPILOT_KEYS:
      return FordBluePilotFeature(self).apply(request)
    if key == "TurnAssist":
      return self._apply_turn_assist(request)
    if key == 'LateralControllerSelection':
      return self.controller.apply(request)
    if key == SETUP_ACTION:
      return self.controller.apply_setup(request)
    if key == 'ForceAutoTuneOff':
      return self.controller.apply_learning(request)
    if key == "RemapCancelToDistance":
      return self.gm_cancel_remap.apply(request)
    if key == "GMPedalLongitudinal":
      return self._apply_pedal_setup(request)
    if key in PREAP_RADAR_KEYS:
      return self.preap_radar.apply(request)
    if key in TESLA_SCREEN_KEYS:
      return self.tesla_screen.apply(request)
    if key == GM_TUNE_KEY:
      return self.gm_tune.apply(request)
    if key in PANDA_WAKE_KEYS:
      return self.panda_wake.apply(request)
    if key == RADAR_RECOVERY_KEY:
      return self.volt_radar.apply(request)
    if key == "EVTuning":
      return self.gm_ev_preset.apply(request)
    if key == "TruckTuning":
      return self.gm_truck.apply(request)
    if key == "DisableOpenpilotLongitudinal":
      return self._apply_bolt_disable(request)
    if key in ("DeveloperUI", "LatSmoothSeconds"):
      return self.subaru_smoothing.apply(request)
    if key == "SubaruAvhStartup":
      from openpilot.starpilot.car.subaru.avh_settings import apply
      return apply(self, request)
    if key == "SubaruStopStartOff":
      from openpilot.starpilot.car.subaru.stop_start_settings import apply
      return apply(self, request)
    if key == "SubaruSNG":
      from openpilot.starpilot.car.subaru.sng_settings import apply
      return apply(self, request)
    if key == "SubaruRedneckCruise":
      from openpilot.starpilot.car.subaru.software_cruise_settings import apply
      return apply(self, request)
    if key in ("VoltSNG", "GMAutoHold", "VoltOnePedalMode"):
      return self._apply_gm_stop(request)
    if key == "LongPitch":
      return self._apply_long_pitch(request)
    if key == "JeepBrakeHold":
      return self._apply_jeep_hold(request)
    if key == "AlphaLongitudinalEnabled":
      return self._apply_alpha_long(request)
    if key == "SNGHack":
      return self._apply_toyota_sng(request)
    if key == "ToyotaAutoHold":
      return self._apply_auto_hold(request)
    if key == "ToyotaRadarMode":
      return self._apply_toyota_radar(request)
    if key == PLANNER_SELECTION_KEY:
      if request.value not in ("Off", "On") or not self.authority("preferences"):
        return False
      return save_selection(self.params, request.value, request.expected,
                            authorized=lambda: self.authority("preferences"))
    if key in GLOBAL_BOOL_PREFERENCES:
      return self._apply_global_bool_preference(request)
    if key in CRUISE_KEYS:
      return self._apply_cruise(request)
    if key.startswith("conditional:"):
      return self.conditional.apply(request)
    if key in CURVE_ACTION_KEYS:
      if not request.confirmation or request.value != "confirm" or not request.vehicle_fingerprint:
        return False
      def authorized() -> bool:
        return (self.vehicle_fingerprint() == request.vehicle_fingerprint and
                self.authority("long") and self.authority("parked_preferences"))
      if not authorized():
        return False
      learning = learning_snapshot(self.params)
      if (not learning.readable or learning.sources != request.dependencies or
          learning.sources[0][1] != request.expected or
          not learning.resettable):
        return False
      result = apply_learning(self.params, key, request.dependencies, authorized=authorized)
      return result.committed and result.reason == "saved"
    if key in LANE_CHANGE_KEYS:
      return self.lane_changes.apply(request)
    if key in TRAFFIC_EDIT_KEYS:
      return self.traffic_profiles.apply(request)
    if key in TORQUE_CONFIRM_ACTIONS or key.startswith("torque:"):
      return self.torque.apply(request)
    if key in SLC_CONFIRM_ACTIONS or key in SLC_OFFSET_KEYS:
      return self.slc_offsets.apply(request)
    if is_long_confirm_action(key):
      return self.long_profiles.apply(request)
    if key == "reset_profiles":
      source_key = PERSONALITY_PROFILES_PARAM
      group = "long"
      if not self.authority("parked_preferences"):
        return False
    elif key == "ForceAutoTuneOff":
      source_key, group = key, "torque"
    elif key in CURVE_PAGE_KEYS:
      source_key, group = key, "long"
    elif key in AOL_BUTTONS:
      source_key, group = key, "aol_wheel"
    elif key in ("AlwaysOnLateral", "NostalgiaMode", AOL_THRESHOLD):
      source_key, group = key, "aol"
    elif key.startswith(LONG_PREFIX):
      source_key = PERSONALITY_PROFILES_PARAM
      group = "long"
    elif key in BOOL_DEFAULTS or key in FLOAT_SPECS or key in (SLC_PRIORITY, SLC_SECONDARY, 'SLCAdvanceSeconds'):
      source_key = key
      long_key = (key in ("CustomPersonalities", "ForceStops", "ForceStopDistanceOffset") or "Personality" in key or
                  key.endswith(("Follow", "FollowHigh")) or "Jerk" in key)
      if key in ("ShowSpeedLimits", "AlwaysAllowUploads", SLC_PRIORITY, SLC_SECONDARY) or (key == "ForceStops" and request.value == "Off"):
        group = "preferences"
      elif key in LANE_LIVE_KEYS and not self.authority("lane"):
        group = "lane_live"
      else:
        group = "lane" if key.startswith("LaneCenter") else "long" if long_key else "slc"
    else:
      return False
    try:
      profile_master_source = self._raw("CustomPersonalities") if source_key == PERSONALITY_PROFILES_PARAM else None
      if ((request.vehicle_fingerprint is not None if key in ("ShowSpeedLimits", "AlwaysAllowUploads") else
           not request.vehicle_fingerprint or self.vehicle_fingerprint() != request.vehicle_fingerprint) or
          not self.authority(group) or self._raw(source_key) != request.expected or
          not self._readable(source_key)):
        return False
      if key == "profile:stopped_gap":
        units, raw_units, units_valid = self._value("IsMetric")
        if (not units_valid or units not in ("0", "1") or not self._readable("IsMetric") or
            request.dependencies != (("IsMetric", raw_units),) or request.display_unit != ("m" if units == "1" else "ft")):
          return False
      if group in ("torque", "aol", "aol_wheel"):
        if request.capability is None or request.capability != self._capability(group) or \
           any(self._raw(name) != raw for name, raw in request.dependencies):
          return False
        if not self._readable(source_key) or (request.value != "Off" or key not in ("AlwaysOnLateral",)) and\
           any(not self._readable(name) for name, _ in request.dependencies):
          return False
      if key in CURVE_KEYS:
        current_document = self._raw(CURVE_DOCUMENT_KEY)
        expected_dependencies = ((CURVE_DOCUMENT_KEY, current_document),)
        if current_document is None:
          expected_dependencies += ((CURVE_LEGACY_KEY, self._raw(CURVE_LEGACY_KEY)),)
        if request.dependencies != expected_dependencies or not self._readable(source_key):
          return False
        if (key != CURVE_MASTER_KEY or request.value != "Off") and not read_learning(self.params).valid:
          return False
      if key in (SLC_PRIORITY, SLC_SECONDARY) and request.capability != self._source_capability():
        return False
      if key in LANE_LIVE_KEYS:
        from openpilot.starpilot.lateral.lane_runtime import runtime_supported
        if not runtime_supported(self.vehicle_params()):
          return False
      if (key == "LaneCenteringPauseOnSignal" and self._value("LaneCentering")[0] != "1" and
          not (reset_default and request.value == ("On" if self._default(key) == "1" else "Off"))):
        return False
      if key == "ForceStopDistanceOffset" and self._value("ForceStops")[0] != "1" and not (reset_default and float(request.value) == float(self._default(key))):
        return False
      if key == "reset_profiles":
        if not request.confirmation or self._document()[2]:
          return False
        fingerprint = self.vehicle_fingerprint()
        if not fingerprint:
          return False
        enabled, master_source, enabled_valid = self._value("CustomPersonalities")
        if not enabled_valid or enabled not in ("0", "1"):
          return False
        encoded = serialize_personality_profiles(default_personality_profiles(False, is_truck_fingerprint(fingerprint)), False,
                                                 is_truck_fingerprint(fingerprint), enabled=False)
      elif key == "CustomPersonalities":
        if request.value not in ("On", "Off") or (request.value == "On" and
                                                  self._raw(PERSONALITY_PROFILES_PARAM) != request.related_source):
          return False
        if request.value == "On":
          health = read_profile_health(self.params)
          if (not health.dependencies_valid or request.dependencies != self.long_profiles.dependencies(health) or
              request.capability is None or request.capability != self.long_profiles.capability()):
            return False
        fingerprint = self.vehicle_fingerprint()
        if not fingerprint:
          return False
        doc = (synchronise_profile_document_enabled(request.related_source, True, False,
                                                    is_truck_fingerprint(fingerprint)) if request.value == "On" else None)
        if doc is None and request.value == "On":
          return False
        current, _, valid = self._value(key)
        if not valid or current not in ("0", "1"):
          return False
        encoded = "1" if request.value == "On" else "0"
      elif key in ("ForceAutoTuneOff", "AlwaysOnLateral", "NostalgiaMode"):
        current, _, valid = self._value(key)
        if not valid or request.value not in ("On", "Off") or (current not in ("0", "1") and not (key == "NostalgiaMode" and request.value == "Off")):
          return False
        if key == "ForceAutoTuneOff":
          valid_numbers, _ = self.torque.rows(request.capability, False)
          force, _, force_valid = self._value("ForceAutoTuneOff")
          if request.value == "On" and (not valid_numbers or not force_valid or force not in ("0", "1")):
            return False
        elif key == "NostalgiaMode":
          cp = self.vehicle_params()
          if not self.aol_settings_policy(cp).paddle_pause:
            return False
        elif request.value == "On" and not self._aol_threshold()[1]:
          return False
        encoded = "1" if request.value == "On" else "0"
      elif key == AOL_THRESHOLD:
        units, _, valid_units = self._value("IsMetric")
        if not valid_units or units not in ("0", "1") or request.display_unit != ("km/h" if units == "1" else "mph"):
          return False
        number = float(request.value)
        mps = number / 3.6 if units == "1" else number * MPH_TO_MPS
        if not math.isfinite(mps) or not 0.0 <= mps <= 100.0:
          return False
        encoded = mps
      elif key in AOL_BUTTONS:
        cp = self.vehicle_params()
        policy = self.aol_settings_policy(cp)
        if (request.value not in AOL_ACTIONS or
            policy.fixed_cruise_buttons and key not in AOL_DISTANCE_BUTTONS or
            policy.distance_pause_only and request.value not in AOL_PAUSE_ACTIONS or
            policy.distance_pause_only and key in AOL_DISTANCE_BUTTONS and
            request.expected not in (None, b"0", b"3", b"4") and request.value != "Off"):
          return False
        encoded = AOL_ACTIONS[request.value]
      elif key.startswith(LONG_PREFIX):
        parts = key.split(":")
        current_document, _, _ = self._document()
        named_acceleration_seed = (len(parts) == 3 and parts[1] in PROFILE_NAMES and parts[2] == "acceleration" and
                                   preset_value(request.value) == "custom" and current_document is not None and
                                   current_document["profiles"][parts[1]]["acceleration"]["preset"] not in
                                   ("dom_default", "custom") and
                                   not current_document["profiles"][parts[1]]["acceleration"]["curve"])
        if named_acceleration_seed and (request.capability is None or
                                        request.capability != self.long_profiles.curve_capability()):
          return False
        if key.startswith("profile:traffic:") and (request.capability is None or
                                                      request.capability != self.traffic_profiles.capability() or
                                                      request.dependencies != self.traffic_profiles._sources()[0]):
          return False
        encoded = self._edit_document(key, request.value, seed_capability=request.capability if named_acceleration_seed else None)
        if encoded is None:
          return False
      elif key == 'SLCAdvanceSeconds':
        current, _, valid = self._value(key)
        if (request.value not in SLC_ADVANCE_CHOICES or request.value != 'Off' and
            (not valid or current not in tuple(str(seconds) for seconds in range(11)))):
          return False
        encoded = str(SLC_ADVANCE_CHOICES.index(request.value))
      elif key in BOOL_DEFAULTS:
        current, _, valid = self._value(key)
        if not valid or current not in ("0", "1") or request.value not in ("On", "Off"):
          return False
        if key == "SpeedLimitController" and request.value == "On" and not self.slc_offsets.ready_to_enable():
          return False
        encoded = ("0" if request.value == "On" else "1") if key == "SLCConfirmation" else ("1" if request.value == "On" else "0")
      elif key in FLOAT_SPECS:
        current, _, valid = self._value(key)
        low, high, _, _ = FLOAT_SPECS[key]
        number = float(request.value)
        if key == "LaneCenteringStrength":
          if not 33 <= number <= STRENGTH_MAX / STRENGTH_BASELINE * 100:
            return False
          number = max(low, number * STRENGTH_BASELINE / 100)
        elif key == "LaneCenteringE2EAuthority":
          number /= 100
        if not valid or not math.isfinite(float(current)) or not low <= float(current) <= high or not math.isfinite(number) or not low <= number <= high:
          return False
        if key == "ForceStopDistanceOffset" and not number.is_integer():
          return False
        encoded = str(int(number)) if key == "ForceStopDistanceOffset" else str(round(number, 4))
      else:
        if request.value not in self._source_choices():
          return False
        encoded = "Map Data" if request.value == "Maps" else request.value
      if key in LANE_LIVE_KEYS:
        from openpilot.starpilot.lateral.lane_runtime import runtime_supported
        if not runtime_supported(self.vehicle_params()):
          return False
      if ((request.vehicle_fingerprint is not None if key in ("ShowSpeedLimits", "AlwaysAllowUploads") else
           self.vehicle_fingerprint() != request.vehicle_fingerprint) or
          not self.authority(group) or self._raw(source_key) != request.expected or
          key == "reset_profiles" and not self.authority("parked_preferences") or
          not self._readable(source_key)):
        return False
      if key == "SpeedLimitController" and request.value == "On" and not self.slc_offsets.ready_to_enable():
        return False
      if key in (SLC_PRIORITY, SLC_SECONDARY) and request.capability != self._source_capability():
        return False
      if group in ("torque", "aol", "aol_wheel"):
        if request.capability != self._capability(group) or \
           any(self._raw(name) != raw for name, raw in request.dependencies):
          return False
        if not self._readable(source_key) or (request.value != "Off" or key not in ("AlwaysOnLateral",)) and\
           any(not self._readable(name) for name, _ in request.dependencies):
          return False
      if key == "reset_profiles":
        if self._raw("CustomPersonalities") != master_source or self._document()[2]:
          return False
        if enabled == "1":
          self.params.put_bool("CustomPersonalities", False, block=True)
        if (self.vehicle_fingerprint() != request.vehicle_fingerprint or not self.authority(group) or
            not self.authority("parked_preferences") or
            self._raw(source_key) != request.expected or self._value("CustomPersonalities")[0] != "0"):
          return False
        profile_master_source = b"0" if enabled == "1" else master_source
      if key == "CustomPersonalities":
        if request.value == "On":
          health = read_profile_health(self.params)
          if (not health.dependencies_valid or request.dependencies != self.long_profiles.dependencies(health) or
              request.capability != self.long_profiles.capability() or
              any(not health.values[name].readable for name, _ in request.dependencies) or
              not self.long_profiles._fresh(request, dict(request.dependencies))):
            return False
        if request.value == "On" and self._raw(PERSONALITY_PROFILES_PARAM) != request.related_source:
          return False
        if doc is not None:
          updated_document = json.dumps(doc).encode()
          if not self._save_profile_document(request, updated_document, request.related_source,
                                             request.expected, dependencies=dict(request.dependencies)):
            return False
          if (self.vehicle_fingerprint() != request.vehicle_fingerprint or not self.authority(group) or
              self._raw(source_key) != request.expected or
              self._raw(PERSONALITY_PROFILES_PARAM) != updated_document):
            return False
          if request.value == "On" and (request.capability != self.long_profiles.capability() or
                                          not read_profile_health(self.params).dependencies_valid or
                                          not self.long_profiles._fresh(request, {
                                            **dict(request.dependencies), PERSONALITY_PROFILES_PARAM: updated_document
                                          })):
            return False
      if key == "CustomPersonalities":
        dependencies = dict(request.dependencies)
        if doc is not None:
          dependencies[PERSONALITY_PROFILES_PARAM] = updated_document
        def authorized() -> bool:
          return (self.vehicle_fingerprint() == request.vehicle_fingerprint and self.authority("long") and
                  (request.value == "Off" or self.long_profiles._fresh(request, dependencies)))
        result = commit_exact(self.params, key=key, max_bytes=128, raw=encoded.encode(), expected=request.expected,
                              authorized=authorized, temp_prefix=".long-profile-switch-")
        return result.committed and result.verified
      if source_key == "AlwaysOnLateral":
        def authorized_aol() -> bool:
          return (self.authority("aol") and self.vehicle_fingerprint() == request.vehicle_fingerprint and
                  request.capability == self._capability("aol") and
                  all(self._readable(name) and self._raw(name) == raw for name, raw in request.dependencies))
        result = commit_exact(self.params, key=source_key, max_bytes=8, raw=encoded.encode(),
                              expected=request.expected, authorized=authorized_aol, temp_prefix=".aol-on-")
        return result.committed and result.verified
      if source_key in BOOL_DEFAULTS:
        self.params.put_bool(source_key, encoded == "1", block=True)
      elif source_key == 'SLCAdvanceSeconds':
        self.params.put(source_key, int(encoded), block=True)
      elif source_key == AOL_THRESHOLD:
        self.params.put(source_key, encoded, block=True)
      elif source_key in AOL_BUTTONS:
        if source_key in AOL_DISTANCE_BUTTONS:
          def authorized_distance() -> bool:
            cp = self.vehicle_params()
            policy = self.aol_settings_policy(cp)
            return (self.vehicle_fingerprint() == request.vehicle_fingerprint and self.authority("aol_wheel") and
                    request.capability is not None and request.capability == self._capability("aol") and
                    policy.settings_supported and
                    request.value in (AOL_PAUSE_ACTIONS if policy.distance_pause_only else AOL_ACTIONS) and
                    request.dependencies == self._dependents("AlwaysOnLateral", AOL_THRESHOLD, AOL_LEGACY_THRESHOLD) and
                    all(self._readable(name) and self._raw(name) == raw for name, raw in request.dependencies))
          result = commit_exact(self.params, key=source_key, max_bytes=128, raw=str(encoded).encode(),
                                expected=request.expected, authorized=authorized_distance,
                                temp_prefix=".aol-distance-")
          return result.committed and result.verified
        self.params.put(source_key, encoded, block=True)
      elif source_key in FLOAT_SPECS:
        self.params.put(source_key, int(encoded) if source_key == "ForceStopDistanceOffset" else float(encoded), block=True)
      elif source_key == PERSONALITY_PROFILES_PARAM:
        return self._save_profile_document(request, str(encoded).encode(), request.expected, profile_master_source,
                                           require_capability=key.startswith("profile:traffic:"),
                                           require_seed_capability=named_acceleration_seed if key.startswith(LONG_PREFIX) else False)
      else:
        self.params.put(source_key, encoded, block=True)
      if group in ("torque", "aol", "aol_wheel"):
        written = self._raw(source_key)
        if source_key in BOOL_DEFAULTS:
          return written == str(encoded).encode("utf-8")
        if source_key == AOL_THRESHOLD:
          observed = self._finite(written)
          return observed is not None and math.isclose(observed, float(encoded), rel_tol=0, abs_tol=1e-6)
        if source_key in AOL_BUTTONS:
          return written == str(encoded).encode("utf-8")
      return True
    except (OSError, ValueError, TypeError, OverflowError):
      return False

  def _save_profile_document(self, request: FeatureSettingsRequest, raw: bytes, expected: bytes | None,
                             master_source: bytes | None, *, dependencies: dict[str, bytes | None] | None = None,
                             require_capability: bool = False, require_seed_capability: bool = False) -> bool:
    def authorized() -> bool:
      return (self.vehicle_fingerprint() == request.vehicle_fingerprint and self.authority("long") and
              (not require_capability or request.capability is not None and
               self.traffic_profiles.capability() == request.capability and
               self.traffic_profiles._sources() == (request.dependencies, True)) and
              (not require_seed_capability or request.capability is not None and
               self.long_profiles.curve_capability() == request.capability) and
              (request.key != "profile:stopped_gap" or all(self._raw(key) == value and self._readable(key)
                                                         for key, value in request.dependencies)) and
              self._readable("CustomPersonalities") and self._raw("CustomPersonalities") == master_source and
              (dependencies is None or self.long_profiles._fresh(request, dependencies)))
    result = commit_exact(self.params, key=PERSONALITY_PROFILES_PARAM, max_bytes=65536, raw=raw, expected=expected,
                          authorized=authorized, temp_prefix=".long-profile-")
    return result.committed and result.verified

  def _edit_document(self, key: str, value: str, *, seed_capability: tuple | None = None) -> str | None:
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
