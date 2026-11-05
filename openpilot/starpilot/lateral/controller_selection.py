"""Startup-only selection between the standard torque controller and exact vehicle policies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import math
from pathlib import Path

from opendbc.car import structs
from opendbc.car.values import PLATFORMS


DOCUMENT_KEY = 'LateralControllerSelection'
DOCUMENT_VERSION = 1
LEARNING_OFF_KEY = 'ForceAutoTuneOff'
MAX_DOCUMENT_BYTES = 4096
KNOWN_POLICIES = {'HYUNDAI_IONIQ_6': 'hyundai', 'GENESIS_G70_2020': 'hyundai', 'GENESIS_GV70_ELECTRIFIED_1ST_GEN': 'hyundai', 'TOYOTA_COROLLA_TSS2': 'toyota'}
KNOWN_POLICIES.update({'HYUNDAI_SONATA_HYBRID': 'hyundai', 'TOYOTA_RAV4_TSS2': 'toyota'})
KNOWN_POLICIES.update(dict.fromkeys(('HYUNDAI_PALISADE', 'HYUNDAI_PALISADE_2023'), 'hyundai'))
KNOWN_POLICIES.update(dict.fromkeys((
  'CHEVROLET_BOLT_CC_2017', 'CHEVROLET_BOLT_CC_2018_2021', 'CHEVROLET_BOLT_CC_2022_2023',
  'CHEVROLET_BOLT_ACC_2022_2023', 'CHEVROLET_BOLT_ACC_2022_2023_PEDAL',
), 'gm'))

KNOWN_POLICIES.update(dict.fromkeys((
  'CHEVROLET_VOLT', 'CHEVROLET_VOLT_ASCM', 'CHEVROLET_VOLT_CAMERA', 'CHEVROLET_VOLT_CC', 'CHEVROLET_VOLT_2019',
), 'gm'))

KNOWN_POLICIES.update(dict.fromkeys((
  'CHEVROLET_MALIBU_ASCM', 'BUICK_LACROSSE_ASCM', 'BUICK_LACROSSE_ASCM_19US',
  'CADILLAC_ESCALADE_ASCM', 'CADILLAC_ESCALADE_ESV_2019_ASCM', 'CHEVROLET_SUBURBAN_ASCM',
), 'gm'))

KNOWN_POLICIES.update(dict.fromkeys((
  'CADILLAC_XT4', 'CHEVROLET_TRAVERSE', 'CADILLAC_XT5', 'CADILLAC_XT6',
  'BUICK_BABYENCLAVE', 'CHEVROLET_BLAZER', 'CHEVROLET_MALIBU_SDGM',
), 'gm'))

KNOWN_POLICIES.update(dict.fromkeys((
  'CADILLAC_CT6_CC', 'CADILLAC_XT4_CC', 'CADILLAC_XT5_CC', 'CHEVROLET_EQUINOX_CC',
  'CHEVROLET_MALIBU_CC', 'CHEVROLET_MALIBU_HYBRID_CC', 'CHEVROLET_SUBURBAN_CC', 'CHEVROLET_TRAILBLAZER_CC', 'GMC_YUKON_CC',
), 'gm'))

KNOWN_POLICIES.update(dict.fromkeys((
  'CHEVROLET_SILVERADO', 'CHEVROLET_EQUINOX', 'CHEVROLET_TRAILBLAZER', 'CHEVROLET_TRAX',
  'GMC_YUKON', 'CHEVROLET_SUBURBAN_CAMERA', 'CHEVROLET_SILVERADO_CC', 'CHEVROLET_SUBURBAN',
), 'gm'))

KNOWN_POLICIES.update(dict.fromkeys((
  'HYUNDAI_IONIQ_5',
  'HYUNDAI_IONIQ_EV_LTD', 'HYUNDAI_IONIQ_EV_2020',
  'HYUNDAI_SONATA',
  'HYUNDAI_ELANTRA_2022_NON_SCC', 'HYUNDAI_ELANTRA_HEV_2022_NON_SCC',
  'KIA_XCEED_PHEV',
  'KIA_NIRO_EV', 'KIA_NIRO_PHEV_2022',
  'KIA_STINGER_2022',
  'KIA_FORTE', 'KIA_FORTE_2019_NON_SCC', 'KIA_FORTE_2021_NON_SCC',
  'KIA_EV6',
  'KIA_CARNIVAL_2025', 'KIA_CARNIVAL_HEV_4TH_GEN',
  'HYUNDAI_TUCSON_4TH_GEN',
  'HYUNDAI_KONA_NON_SCC',
  'HYUNDAI_KONA_EV_2022',
), 'hyundai'))

KNOWN_POLICIES.update({str(identity): platform.__class__.__module__.split('.')[-2]
                       for identity, platform in PLATFORMS.items()})


def generic_torque_supported(cp) -> bool:
  try:
    platform = PLATFORMS.get(str(cp.carFingerprint))
    if (platform is None or platform.__class__.__module__.split('.')[-2] != str(cp.brand) or
        cp.notCar or cp.passive or cp.dashcamOnly or
        cp.steerControlType != structs.CarParams.SteerControlType.torque or cp.lateralTuning.which() != 'torque'):
      return False
    tune = cp.lateralTuning.torque
    return (math.isfinite(tune.latAccelFactor) and tune.latAccelFactor > 0 and
            math.isfinite(tune.latAccelOffset) and math.isfinite(tune.friction) and tune.friction >= 0 and
            math.isfinite(tune.steeringAngleDeadzoneDeg) and tune.steeringAngleDeadzoneDeg >= 0)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return False


class ControllerMode(StrEnum):
  STANDARD = 'standard'
  STARPILOT = 'starpilot'


@dataclass(frozen=True)
class ControllerSelection:
  mode: ControllerMode
  policy: str | None
  source: str


def policy_for(CP) -> str | None:
  """Prefer exact vehicle policies, otherwise use the shared torque law."""
  if CP.notCar:
    return None
  from openpilot.starpilot.lateral.hkg_vehicle_policy import policy_for as hkg_vehicle_policy
  profile = hkg_vehicle_policy(CP)
  if profile is not None:
    return profile
  from openpilot.starpilot.lateral.corolla_tss2_policy import supported_cp as corolla_supported
  from openpilot.starpilot.lateral.genesis_g70_policy import supported_cp as g70_supported
  from openpilot.starpilot.lateral.genesis_gv70_policy import supported_cp as gv70_supported
  from openpilot.starpilot.lateral.bolt_policy import supported_cp as bolt_supported
  from openpilot.starpilot.lateral.volt_policy import supported_cp as volt_supported

  from openpilot.starpilot.lateral.ascm_policy import supported_cp as ascm_supported

  from openpilot.starpilot.lateral.sdgm_policy import supported_cp as sdgm_supported

  from openpilot.starpilot.lateral.ordinary_cc_policy import supported_cp as cc_supported

  from openpilot.starpilot.lateral.camera_policy import supported_cp as camera_supported

  from openpilot.starpilot.lateral.suburban_policy import supported_cp as suburban_supported

  from opendbc.car.gm.values import is_silverado_cc_pedal_profile
  if suburban_supported(CP):
    return 'suburban'
  if camera_supported(CP):
    return 'silverado_cc' if is_silverado_cc_pedal_profile(CP) else 'ordinary_camera'
  if cc_supported(CP):
    return 'ordinary_cc'
  if sdgm_supported(CP):
    return 'ordinary_sdgm'
  if ascm_supported(CP):
    return 'ordinary_ascm'
  if volt_supported(CP):
    return 'volt'
  if bolt_supported(CP):
    return 'bolt'
  if corolla_supported(CP):
    return 'corolla_tss2'
  from openpilot.starpilot.lateral.prius_policy import supported_cp as prius_supported
  if prius_supported(CP):
    return 'prius'
  from openpilot.starpilot.lateral.rav4_tss2_policy import supported_cp as rav4_supported
  if rav4_supported(CP):
    return 'rav4_tss2'
  from openpilot.starpilot.lateral.palisade_policy import supported_cp as palisade_supported
  from openpilot.starpilot.lateral.sonata_hybrid_policy import supported_cp as sonata_hybrid_supported
  if sonata_hybrid_supported(CP):
    return 'sonata_hybrid'
  if palisade_supported(CP):
    return 'palisade'
  if gv70_supported(CP):
    return 'genesis_gv70_electrified'
  if g70_supported(CP):
    return 'genesis_g70_2020'
  if (str(CP.carFingerprint) == 'HYUNDAI_IONIQ_6' and str(CP.brand) == 'hyundai' and
      CP.steerControlType == structs.CarParams.SteerControlType.torque and
      CP.lateralTuning.which() == 'torque' and not CP.dashcamOnly and not CP.passive):
    return 'ioniq6'
  return "generic" if generic_torque_supported(CP) else None


def turn_assist_supported(CP) -> bool:
  return bool(CP.steerControlType == structs.CarParams.SteerControlType.torque and
              CP.lateralTuning.which() == 'torque' and not CP.notCar and not CP.passive and not CP.dashcamOnly and
              any(config.safetyModel not in (structs.CarParams.SafetyModel.noOutput,
                  structs.CarParams.SafetyModel.silent, structs.CarParams.SafetyModel.allOutput)
                  for config in CP.safetyConfigs))


def model_turn_assist_supported(CP) -> bool:
  return turn_assist_supported(CP) and policy_for(CP) == 'ioniq6'


def default_selection(CP) -> ControllerSelection:
  policy = policy_for(CP)
  mode = ControllerMode.STARPILOT if policy and policy != 'generic' else ControllerMode.STANDARD
  return ControllerSelection(mode, policy, 'default')


def _unique_pairs(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError('Duplicate controller selection field')
    result[key] = value
  return result


def parse_document(raw: bytes) -> dict:
  """Reject unknown, ambiguous or partial documents before any saved edit."""
  if len(raw) > MAX_DOCUMENT_BYTES:
    raise ValueError('Oversize controller selection')
  document = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs)
  if (not isinstance(document, dict) or set(document) != {'version', 'vehicles'} or
      type(document['version']) is not int or document['version'] != DOCUMENT_VERSION):
    raise ValueError('Invalid controller selection version')
  vehicles = document['vehicles']
  if not isinstance(vehicles, dict) or len(vehicles) > len(KNOWN_POLICIES):
    raise ValueError('Invalid controller selection vehicles')
  for fingerprint, choice in vehicles.items():
    if (fingerprint not in KNOWN_POLICIES or not isinstance(choice, dict) or set(choice) != {'brand', 'mode'} or
        choice['brand'] != KNOWN_POLICIES[fingerprint] or type(choice['mode']) is not str or
        choice['mode'] not in (ControllerMode.STANDARD, ControllerMode.STARPILOT)):
      raise ValueError('Invalid controller selection choice')
  return document


def replace_mode(raw: bytes | None, CP, mode: ControllerMode) -> bytes:
  """Change only one exact platform's saved choice, preserving other entries."""
  if policy_for(CP) is None:
    raise ValueError('No exact torque policy for CarParams')
  mode = ControllerMode(mode)
  document = {'version': DOCUMENT_VERSION, 'vehicles': {}} if raw is None else parse_document(raw)
  vehicles = dict(document['vehicles'])
  vehicles[str(CP.carFingerprint)] = {'brand': str(CP.brand), 'mode': mode.value}
  encoded = json.dumps({'version': DOCUMENT_VERSION, 'vehicles': vehicles},
                       sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
  if len(encoded) > MAX_DOCUMENT_BYTES:
    raise ValueError('Oversize controller selection')
  return encoded


def selection_from_bytes(CP, raw: bytes | None) -> ControllerSelection:
  default = default_selection(CP)
  if raw is None or default.policy is None:
    return default
  try:
    choice = parse_document(raw)['vehicles'].get(str(CP.carFingerprint))
    if choice is None or choice['brand'] != str(CP.brand):
      return default
    return ControllerSelection(ControllerMode(choice['mode']), default.policy, 'saved')
  except (UnicodeError, ValueError, TypeError, OverflowError):
    return ControllerSelection(default.mode, default.policy, 'invalid')


def read_selection(params, CP) -> ControllerSelection:
  """Read once at controls startup; a later settings write cannot change this controller."""
  if policy_for(CP) is None:
    return default_selection(CP)
  try:
    with Path(params.get_param_path(DOCUMENT_KEY)).open('rb') as source:
      return selection_from_bytes(CP, source.read(MAX_DOCUMENT_BYTES + 1))
  except FileNotFoundError:
    return default_selection(CP)
  except OSError:
    default = default_selection(CP)
    return ControllerSelection(default.mode, default.policy, 'invalid')


def controller_uses_native_learning(selection: ControllerSelection) -> bool:
  return selection.mode in (ControllerMode.STANDARD, ControllerMode.STARPILOT) and selection.source != 'invalid'


def learning_allowed(params, CP, *, selection: ControllerSelection | None = None) -> bool:
  """Latch the exact controller's learning policy at each process startup.

  An unrelated vehicle must not inherit another platform's learning preference.
  Callers that already chose their controller supply that same startup selection.
  """
  from openpilot.starpilot.lateral.learning_preferences import read_preference
  preference = read_preference(params, CP)
  if not preference.valid or not preference.enabled:
    return False
  policy = policy_for(CP)
  if policy is None:
    return True
  selected = read_selection(params, CP) if selection is None else selection
  if policy == 'generic':
    return selected.source != 'invalid'
  from openpilot.starpilot.lateral.gm_geometry_runtime import read_geometry
  geometry = read_geometry(params, CP)
  if geometry.force_off:
    return False
  if geometry.force_auto:
    return selected.source != 'invalid'
  from openpilot.starpilot.lateral.torque_runtime import manual_overrides_present
  if manual_overrides_present(CP, params):
    return False
  # Reuse the native estimator only in its existing brand scope. Actual
  # controller parameter ownership still belongs to Controls or TorqueHost.
  native_brand = str(CP.brand) in ('toyota', 'hyundai', 'rivian', 'honda', 'volkswagen')
  return native_brand and controller_uses_native_learning(selected) and CP.lateralTuning.which() == 'torque'
