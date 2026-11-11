from dataclasses import replace
from functools import lru_cache

from opendbc.car.hyundai.ev9_long_aol import qualified as qualified_ev9_long_aol, WORDS as EV9_LONG_AOL_WORDS
from opendbc.car.hyundai.pe_long_aol import qualified as qualified_pe_long_aol, WORDS as PE_LONG_AOL_WORDS

from opendbc.car.hyundai.blended_stock_aol import (
  qualified as qualified_blended_stock, qualified_alpha as qualified_blended_alpha,
  WORDS as BLENDED_STOCK_WORDS, ALPHA_WORDS as BLENDED_ALPHA_WORDS,
)
from opendbc.car.hyundai.canfd_angle_aol import qualified as qualified_angle_aol, temporary_restriction, ANGLE_AOL_WORDS
from opendbc.car.hyundai.classic_long_aol import (
  qualified as qualified_classic_long,
  independent_buttons_qualified as qualified_sonata_independent,
  aol_word as classic_long_word,
  native_accepts as classic_long_accepts,
  LONG_AOL_WORDS,
)
from opendbc.car.hyundai.classic_scc_aol import (
  qualified as qualified_classic_scc,
  aol_word as classic_aol_word,
  native_accepts as classic_native_accepts,
  native_profile_supported as classic_native_profile_supported,
)
from opendbc.car.hyundai.canfd_owner import config_index
from opendbc.car.hyundai.canfd_stock_aol import (
  qualified as qualified_canfd_stock, qualified_long as qualified_canfd_long,
  LONG_AOL_WORDS as CANFD_LONG_AOL_WORDS, STOCK_AOL_MARKER, STOCK_AOL_WORDS,
)
from opendbc.car.hyundai.non_scc_aol import qualified as qualified_non_scc, aol_word, AOL_MARKER, AOL_EXPERIENCE, AOL_WORDS
from opendbc.car.hyundai.kona_aol import allow_lateral_onset as kona_lateral_onset
from opendbc.car.hyundai.left_paddle import IONIQ_5_CARS, has_left_paddle
from opendbc.car.structs import car
from opendbc.car.hyundai.values import CAR as HyundaiCAR, HyundaiFlags
from opendbc.car.hyundai.ccnc_ev_stock import qualified as qualified_ccnc_ev_stock, request_allowed, STOCK_SAFETY_PARAMS
from opendbc.car.hyundai.ioniq6_handoff import IONIQ6_LONG_PREARM_ENABLED
from openpilot.starpilot.aol.policy import AolVehiclePolicy
from openpilot.starpilot.longitudinal.ioniq6_start import eligible as ioniq6_long_eligible


IONIQ6_STOCK_AOL_SAFETY_PARAMS = frozenset((0x0811, 0x0891))
IONIQ6_AOL_SAFETY_PARAMS = frozenset((0x0811, 0x0891, 0x8815, 0x8895))
IONIQ6_STOCK_SAFETY_PARAMS = frozenset((0x11, 0x91))
_REQUIRED_FLAGS = int(HyundaiFlags.CANFD | HyundaiFlags.EV | HyundaiFlags.CANFD_LKA_STEER_MSG)
_FORBIDDEN_FLAGS = int(HyundaiFlags.CANFD_ALT_BUTTONS | HyundaiFlags.CANFD_ANGLE_STEERING | HyundaiFlags.CANFD_CAMERA_SCC)


def _ioniq6_hda2_base(CP, *, require_radar=True) -> bool:
  if (
    str(CP.carFingerprint) != str(HyundaiCAR.HYUNDAI_IONIQ_6)
    or CP.brand != 'hyundai'
    or (require_radar and CP.radarUnavailable)
    or CP.notCar
    or CP.passive
    or CP.dashcamOnly
    or CP.steerControlType != car.CarParams.SteerControlType.torque
    or config_index(CP) is None
  ):
    return False
  flags = int(CP.flags)
  return flags & _REQUIRED_FLAGS == _REQUIRED_FLAGS and not flags & _FORBIDDEN_FLAGS


def _stock_ioniq6(CP, *, marked_only=False, require_radar=True) -> bool:
  if not _ioniq6_hda2_base(CP, require_radar=require_radar) or CP.openpilotLongitudinalControl or not CP.pcmCruise:
    return False
  stock = 0x91 if int(CP.flags) & int(HyundaiFlags.CANFD_LKA_STEER_MSG_ALT) else 0x11
  raw = int(CP.safetyConfigs[config_index(CP)].safetyParam)
  return raw == (stock | 0x0800) or (not marked_only and raw == stock)


def qualified_ioniq6(CP) -> bool:
  if _stock_ioniq6(CP, marked_only=True):
    return True
  if not _ioniq6_hda2_base(CP) or not CP.openpilotLongitudinalControl or CP.pcmCruise:
    return False
  flags = int(CP.flags)
  raw = int(CP.safetyConfigs[config_index(CP)].safetyParam)
  return raw == (0x8895 if flags & int(HyundaiFlags.CANFD_LKA_STEER_MSG_ALT) else 0x8815)


def native_admission_key(CP):
  """Complete mutable CP inputs to exact selected-owner Ioniq native admission.

  Keep this in sync with qualified_ioniq6 and native inventory qualification.
  This key is not an admission decision and never contains live authority.
  """
  if CP.brand != 'hyundai' or CP.carFingerprint != HyundaiCAR.HYUNDAI_IONIQ_6 or config_index(CP) is None:
    return None
  return (str(CP.brand), str(CP.carFingerprint), int(CP.flags), bool(CP.radarUnavailable),
          bool(CP.notCar), bool(CP.passive), bool(CP.dashcamOnly),
          bool(CP.openpilotLongitudinalControl), bool(CP.pcmCruise), int(CP.alternativeExperience), str(CP.steerControlType),
          tuple((int(c.safetyModel.raw), int(c.safetyParam)) for c in CP.safetyConfigs))


def ioniq6_settings_capable(CP) -> bool:
  # Stock ACC lateral settings are independent of the experimental LONG opt-in.
  return _stock_ioniq6(CP) or (IONIQ6_LONG_PREARM_ENABLED and ioniq6_long_eligible(CP))


def ioniq6_alpha_long_available(CP) -> bool:
  # Cached radar discovery may predate the external-bus repair. This saves only
  # an opt-in; the takeover still requires fresh physical radar and ECU evidence.
  return _stock_ioniq6(CP, require_radar=False) or (IONIQ6_LONG_PREARM_ENABLED and ioniq6_long_eligible(CP))


def qualified_ccnc_long_aol(CP, *, marked_only=False):
  return qualified_ev9_long_aol(CP, marked_only=marked_only) or qualified_pe_long_aol(CP, marked_only=marked_only)


def _policy_for(CP) -> AolVehiclePolicy:
  if qualified_ccnc_long_aol(CP):
    return AolVehiclePolicy(cruise_main_required=False, intent_supported=True, settings_supported=True, runtime_supported=True,
                            normal_runtime_supported=True, explicit_latch=True, alternative_experience_addition=32)
  if qualified_blended_alpha(CP):
    return AolVehiclePolicy(intent_supported=True, settings_supported=True, runtime_supported=True,
                            normal_runtime_supported=True, ordinary_axis_ack_required=qualified_blended_alpha(CP, marked_only=True),
                            full_axis_runtime_required=qualified_blended_alpha(CP, marked_only=True),
                            explicit_latch=True, alternative_experience_addition=32)
  if qualified_blended_stock(CP):
    return AolVehiclePolicy(intent_supported=True, settings_supported=True, runtime_supported=True,
                            normal_runtime_supported=True, explicit_latch=True, alternative_experience_addition=32)
  if qualified_angle_aol(CP):
    return AolVehiclePolicy(
      intent_supported=True,
      settings_supported=True,
      runtime_supported=True,
      normal_runtime_supported=True,
      ordinary_axis_ack_required=qualified_ccnc_ev_stock(CP),
      explicit_latch=True,
      alternative_experience_addition=32,
    )
  if qualified_classic_long(CP):
    return AolVehiclePolicy(
      intent_supported=True,
      settings_supported=True,
      runtime_supported=True,
      normal_runtime_supported=True,
      explicit_latch=True,
      safety_param_addition=classic_long_word(CP) ^ int(CP.safetyConfigs[0].safetyParam),
      alternative_experience_addition=AOL_EXPERIENCE,
    )
  if qualified_classic_scc(CP):
    return AolVehiclePolicy(
      cruise_main_required=False,
      intent_supported=True,
      settings_supported=True,
      runtime_supported=True,
      normal_runtime_supported=True,
      explicit_latch=True,
      safety_param_addition=classic_aol_word(CP) ^ int(CP.safetyConfigs[0].safetyParam),
      alternative_experience_addition=AOL_EXPERIENCE,
    )
  if qualified_canfd_long(CP):
    return AolVehiclePolicy(intent_supported=True, settings_supported=True, runtime_supported=True,
                            normal_runtime_supported=True, explicit_latch=True, safety_param_addition=0x0800,
                            alternative_experience_addition=32)
  if qualified_canfd_stock(CP):
    return AolVehiclePolicy(
      intent_supported=True,
      settings_supported=True,
      runtime_supported=True,
      normal_runtime_supported=True,
      explicit_latch=True,
      safety_param_addition=STOCK_AOL_MARKER,
    )
  if qualified_non_scc(CP):
    return AolVehiclePolicy(
      cruise_main_required=False,
      intent_supported=True,
      settings_supported=True,
      runtime_supported=True,
      normal_runtime_supported=True,
      explicit_latch=True,
      safety_param_addition=AOL_MARKER | (aol_word(CP) & 0x0800),
      alternative_experience_addition=AOL_EXPERIENCE,
    )
  if qualified_ccnc_ev_stock(CP):
    return AolVehiclePolicy(ordinary_axis_ack_required=True)
  stock = _stock_ioniq6(CP)
  qualified = qualified_ioniq6(CP) or stock
  settings = ioniq6_settings_capable(CP) or qualified
  distance_personality = ioniq6_long_eligible(CP)
  return AolVehiclePolicy(
    intent_supported=qualified,
    settings_supported=settings,
    runtime_supported=qualified,
    normal_runtime_supported=qualified and (stock or distance_personality),
    explicit_latch=qualified,
    distance_personality=distance_personality,
    safety_param_addition=0x0800 if stock else 0,
    distance_pause_only=settings,
    paddle_pause=settings,
  )


def aol_safety_config(CP):
  if len(CP.safetyConfigs) == 1:
    return CP.safetyConfigs[0]
  owner = config_index(CP)
  return CP.safetyConfigs[owner] if owner is not None else None


def policy_for(CP) -> AolVehiclePolicy:
  policy = _policy_for(CP)
  if (CP.carFingerprint in IONIQ_5_CARS and has_left_paddle(CP) and
      CP.openpilotLongitudinalControl and policy.runtime_supported):
    policy = replace(policy, paddle_pause=True)
  marked = (
    qualified_ccnc_long_aol(CP, marked_only=True)
    or qualified_blended_alpha(CP, marked_only=True) or qualified_blended_stock(CP, marked_only=True)
    or qualified_angle_aol(CP, marked_only=True) or qualified_classic_long(CP, marked_only=True)
    or qualified_classic_scc(CP, marked_only=True) or qualified_canfd_long(CP, marked_only=True)
    or qualified_canfd_stock(CP, marked_only=True) or qualified_ioniq6(CP)
    or (qualified_non_scc(CP) and CP.alternativeExperience == AOL_EXPERIENCE
        and CP.safetyConfigs[0].safetyParam == aol_word(CP))
  )
  config = aol_safety_config(CP)
  if policy.runtime_supported and marked and config is not None:
    if native_accepts_cp(CP, int(config.safetyModel.raw), int(config.safetyParam)):
      policy = replace(policy, ordinary_axis_ack_required=True, full_axis_runtime_required=True)
  return policy


def native_profile_supported(model: int, param: int) -> bool:
  from opendbc.car.hyundai.legacy_long_aol import LEGACY_LONG_AOL_WORDS
  return (
    (model == int(car.CarParams.SafetyModel.hyundaiLegacy) and param in LEGACY_LONG_AOL_WORDS)
    or (model == int(car.CarParams.SafetyModel.hyundai) and param in LONG_AOL_WORDS | BLENDED_STOCK_WORDS | BLENDED_ALPHA_WORDS)
    or classic_native_profile_supported(model, param)
    or (
      model == int(car.CarParams.SafetyModel.hyundaiCanfd)
      and param in (IONIQ6_AOL_SAFETY_PARAMS | CANFD_LONG_AOL_WORDS | ANGLE_AOL_WORDS | EV9_LONG_AOL_WORDS |
                    PE_LONG_AOL_WORDS | STOCK_AOL_WORDS | set(STOCK_SAFETY_PARAMS.values()))
    )
    or (model == int(car.CarParams.SafetyModel.hyundai) and param in AOL_WORDS)
  )


def native_accepts_cp(CP, model: int, param: int) -> bool:
  # HDA-II retains its dedicated namespace; camera SCC uses the shared exact word.
  # Revalidate the complete contract on every call, without caching live authority.
  if CP.carFingerprint == HyundaiCAR.HYUNDAI_IONIQ_6 and not CP.flags & HyundaiFlags.CANFD_CAMERA_SCC:
    return qualified_ioniq6(CP)
  if qualified_ccnc_long_aol(CP, marked_only=True):
    return model == int(car.CarParams.SafetyModel.hyundaiCanfd) and param == CP.safetyConfigs[0].safetyParam
  if qualified_blended_alpha(CP, marked_only=True) or qualified_blended_stock(CP, marked_only=True):
    return model == int(car.CarParams.SafetyModel.hyundai) and param == CP.safetyConfigs[0].safetyParam
  if qualified_angle_aol(CP, marked_only=True):
    return model == int(car.CarParams.SafetyModel.hyundaiCanfd) and param == CP.safetyConfigs[0].safetyParam
  if qualified_classic_long(CP, marked_only=True):
    return classic_long_accepts(CP, model, param)
  if qualified_classic_scc(CP, marked_only=True):
    return classic_native_accepts(CP, model, param)
  if qualified_canfd_long(CP, marked_only=True):
    return model == int(car.CarParams.SafetyModel.hyundaiCanfd) and param == aol_safety_config(CP).safetyParam
  if qualified_canfd_stock(CP, marked_only=True):
    return model == int(car.CarParams.SafetyModel.hyundaiCanfd) and param == aol_safety_config(CP).safetyParam
  if qualified_non_scc(CP):
    return CP.alternativeExperience == AOL_EXPERIENCE and model == int(car.CarParams.SafetyModel.hyundai) and param == aol_word(CP)
  return (
    qualified_ccnc_ev_stock(CP) and model == int(car.CarParams.SafetyModel.hyundaiCanfd) and param == STOCK_SAFETY_PARAMS[CP.carFingerprint]
  ) or qualified_ioniq6(CP)


def native_latch_rejected(CP, native) -> bool:
  # In this exact native policy a requested lateral axis remains permitted
  # through pedal override; denial means the physical authorization was lost.
  return (
    native.requestedLateral and not native.lateralAllowed and
    (
      qualified_angle_aol(CP, marked_only=True) or qualified_ccnc_long_aol(CP, marked_only=True)
      or qualified_blended_stock(CP, marked_only=True)
      or qualified_blended_alpha(CP, marked_only=True)
      or qualified_classic_long(CP, marked_only=True)
      or qualified_ioniq6(CP)
      or qualified_non_scc(CP)
      or qualified_canfd_long(CP, marked_only=True)
      or qualified_canfd_stock(CP, marked_only=True)
      or qualified_classic_scc(CP, marked_only=True)
    )
  )


def create_intent(CP, settings):
  from opendbc.car.hyundai.ev6_aol import qualified as ev6_qualified
  if CP.carFingerprint == HyundaiCAR.KIA_EV6 and qualified_canfd_stock(CP):
    from openpilot.starpilot.car.hyundai.ev6_intent import StockEv6CardIntent
    return StockEv6CardIntent(settings)
  if ev6_qualified(CP):
    from openpilot.starpilot.car.hyundai.ev6_intent import Ev6CardIntent
    return Ev6CardIntent(settings)
  if qualified_blended_alpha(CP) or qualified_classic_long(CP):
    from openpilot.starpilot.car.hyundai.classic_long_intent import ClassicLongCardIntent

    return ClassicLongCardIntent(CP, settings)
  if qualified_classic_scc(CP):
    from openpilot.starpilot.car.hyundai.classic_scc_intent import ClassicSccCardIntent

    return ClassicSccCardIntent(CP, settings)
  if qualified_non_scc(CP):
    from openpilot.starpilot.car.hyundai.forte_intent import ForteCardIntent

    return ForteCardIntent(CP, settings)
  from openpilot.starpilot.aol.intent import AolCardIntent

  independent_cruise_buttons = independent_cruise_buttons_supported(CP)
  return AolCardIntent(settings, explicit_latch=policy_for(CP).explicit_latch,
                       independent_cruise_buttons=independent_cruise_buttons)


def ordinary_axis_request_allowed(CP, CS) -> bool:
  return bool(request_allowed(CP, CS))


def allow_lateral_onset(CP, *, requested, normal_enabled, steering_pressed, previous_active):
  if not (qualified_non_scc(CP) and CP.carFingerprint == HyundaiCAR.HYUNDAI_KONA_NON_SCC):
    return bool(requested)
  return kona_lateral_onset(permitted=requested, normal_enabled=normal_enabled, steering_pressed=steering_pressed, previous_active=previous_active)


def retain_on_native_denial(CP, native, state):
  return bool((qualified_angle_aol(CP, marked_only=True) or qualified_ccnc_long_aol(CP, marked_only=True)) and
              native.requestedLateral and not native.lateralAllowed and temporary_restriction(CP, state))


def early_native_long_withdrawal_supported(CP) -> bool:
  """Exact LONG profiles whose native policy grants lateral independently."""
  return bool(CP.openpilotLongitudinalControl and
              (qualified_ioniq6(CP) or qualified_canfd_long(CP, marked_only=True) or
               qualified_classic_long(CP, marked_only=True) and
               CP.safetyConfigs[0].safetyModel == car.CarParams.SafetyModel.hyundai or
               CP.carFingerprint == HyundaiCAR.HYUNDAI_PALISADE_2023 and qualified_blended_alpha(CP, marked_only=True)))



def raw_longitudinal_withdrawal(CP, CS, sm, native, *, session, now_ns) -> bool:
  from openpilot.starpilot.aol.transport_pause import independent_lateral_health

  try:
    if (CP.brand != 'hyundai' or not CP.openpilotLongitudinalControl or native is None or
        not native.requestedLateral or not native.lateralAllowed or not native.requestedLongitudinal or
        not native.longitudinalAllowed or not native.pandaInventory or
        not 0 < native.sourcePandaStatesMonoTime < int(sm.logMonoTime['pandaStates'])):
      return False
    index = 0 if len(CP.safetyConfigs) == 1 else config_index(CP)
    pandas = sm['pandaStates']
    if (index is None or len(pandas) != len(CP.safetyConfigs) or len(native.pandaInventory) != len(CP.safetyConfigs) or
        pandas[index].controlsAllowed or not native.pandaInventory[index].controlsAllowed or
        not (early_native_long_withdrawal_supported(CP) or qualified_ccnc_long_aol(CP, marked_only=True))):
      return False
    return independent_lateral_health(sm, CP, CS, session, now_ns) == native
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return False

def classic_brake_withdrawal(CP, CS) -> bool:
  return bool(CP.brand == 'hyundai' and CS.brakePressed and not CS.gasPressed and not CS.regenBraking and
              CS.cruiseState.available and not CS.doorOpen and not CS.seatbeltUnlatched and not CS.vehicleSensorsInvalid and
              CS.gearShifter in (car.CarState.GearShifter.drive, car.CarState.GearShifter.sport,
                                 car.CarState.GearShifter.low, car.CarState.GearShifter.brake) and
              (qualified_classic_long(CP, marked_only=True) or
               CP.carFingerprint == HyundaiCAR.HYUNDAI_PALISADE_2023 and qualified_blended_alpha(CP, marked_only=True)) and
              CP.safetyConfigs[0].safetyModel == car.CarParams.SafetyModel.hyundai)


def _source_deadline_supported_uncached(CP) -> bool:
  """Admit only an already marked HKG axis owner to original-proof waits."""
  from openpilot.starpilot.aol.vehicle import policy_for as vehicle_policy_for

  config = aol_safety_config(CP)
  policy = vehicle_policy_for(CP)
  return bool(policy.runtime_supported and policy.full_axis_runtime_required and config is not None and
              native_accepts_cp(CP, int(config.safetyModel.raw), int(config.safetyParam)))


@lru_cache(maxsize=64)
def _source_deadline_for_bytes(key: bytes) -> bool:
  with car.CarParams.from_bytes(key) as CP:
    return _source_deadline_supported_uncached(CP)


def source_deadline_supported(CP) -> bool:
  """Reuse only exact static eligibility; live source/native leases stay current."""
  from openpilot.starpilot.aol.vehicle import _car_params_bytes

  if CP.brand != 'hyundai':
    return False
  key = _car_params_bytes(CP)
  if key is not None:
    try:
      return _source_deadline_for_bytes(key)
    except Exception:
      pass  # Snapshot/cache failures retain the complete original evaluation.
  return _source_deadline_supported_uncached(CP)


def independent_cruise_buttons_supported(CP, *, marked_only=False) -> bool:
  # Dedicated EV6 LONG raw-button/optional-release behavior stays unchanged.
  if CP.carFingerprint == HyundaiCAR.KIA_EV6 and CP.openpilotLongitudinalControl:
    return False
  return bool(qualified_ccnc_long_aol(CP, marked_only=marked_only) or qualified_sonata_independent(CP) or
              qualified_angle_aol(CP, marked_only=marked_only) or
              qualified_canfd_stock(CP, marked_only=marked_only) or
              qualified_canfd_long(CP, marked_only=marked_only) or
              qualified_ioniq6(CP) or _stock_ioniq6(CP, marked_only=marked_only))


def physical_cancel_lateral_allowed(CP, CS, intent, native, *, settings, previous,
                                    source_ns, now_ns, axis_session_id) -> bool:
  from openpilot.starpilot.aol.runtime import (INTENT_MAX_AGE_NS, AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS,
                                               native_inventory_matches_cp)
  try:
    if (settings is None or not settings.enabled or any(settings.cancel_actions) or
        not any(e.type == car.CarState.ButtonEvent.Type.cancel for e in CS.buttonEvents)):
      return False
    ev6_long = CP.carFingerprint == HyundaiCAR.KIA_EV6 and qualified_canfd_long(CP, marked_only=True)
    return bool((ev6_long or qualified_blended_alpha(CP, marked_only=True) or
                 independent_cruise_buttons_supported(CP, marked_only=True)) and
      previous is not None and previous.lateral_active and previous.native_acknowledged and
      intent is not None and intent.settingsQualified and intent.allowedLatch and intent.lateralArmed and
      not intent.pauseLateral and intent.producerSessionId and intent.carStateLogMonoTime == source_ns and
      0 < source_ns <= intent.observedMonoTime <= now_ns <= intent.validUntilMonoTime and
      now_ns - source_ns <= AXIS_MAX_AGE_NS and now_ns - intent.observedMonoTime <= INTENT_MAX_AGE_NS and
      intent.validUntilMonoTime - intent.observedMonoTime <= SAFETY_MAX_AGE_NS and
      CS.canValid and not CS.canTimeout and not CS.steerFaultTemporary and not CS.steerFaultPermanent and
      not CS.accFaulted and not CS.doorOpen and
      any(e.type == car.CarState.ButtonEvent.Type.cancel for e in CS.buttonEvents) and
      native is not None and native.compatible and int(native.protocolVersion) == 1 and
      axis_session_id and native.axisSessionId == axis_session_id and
      native.requestedLateral and native.lateralAllowed and native.pandaInventory and
      native_inventory_matches_cp(native, CP) and
      0 < native.observedMonoTime <= now_ns <= native.validUntilMonoTime and
      now_ns - native.observedMonoTime <= SAFETY_MAX_AGE_NS and
      native.validUntilMonoTime - native.observedMonoTime <= SAFETY_MAX_AGE_NS)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return False


def retained_shared_long_cancel_intent(CP, CS, sm, feedback, owner, *, cancel_source_ns, now_ns, producer):
  """Keep existing mapped intent for one current physical CANCEL/FULL-proof join.

  This returns no axis grant and changes no feedback/native/source deadline.
  The next actual LAT-only publication must establish its own normal baseline.
  """
  from opendbc.car.hyundai.canfd_stock_aol import INDEPENDENT_LONG_BUTTON_CARS
  from openpilot.cereal import log
  from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS
  from openpilot.starpilot.aol.transport_pause import cp_identity, independent_lateral_health
  try:
    if not any(event.type == car.CarState.ButtonEvent.Type.cancel and event.pressed for event in CS.buttonEvents):
      return False
    ev6_long = False
    if CP.carFingerprint == HyundaiCAR.KIA_EV6 and qualified_canfd_long(CP, marked_only=True):
      from openpilot.starpilot.car.hyundai.ev6_intent import Ev6CardIntent
      ev6_long = bool(isinstance(owner, Ev6CardIntent) and owner._buttons_current and
                      owner.physical.authorized and owner._last_latch_edge_ns > 0)
    mixed_long = qualified_blended_alpha(CP, marked_only=True)
    if (not (ev6_long or mixed_long or qualified_sonata_independent(CP) or qualified_ccnc_long_aol(CP, marked_only=True) or
             CP.carFingerprint in INDEPENDENT_LONG_BUTTON_CARS and qualified_canfd_long(CP, marked_only=True)) or
        owner is None or not owner.explicit_latch or not (owner.independent_cruise_buttons or ev6_long or mixed_long) or
        not owner.allowed_latch or owner.pause_lateral or owner.pause_longitudinal or owner._fault_inhibit):
      return False
    settings = owner.settings
    if (not settings.enabled or any(settings.cancel_actions) or cp_identity(CP) != feedback.cp or
        feedback.failed or feedback.paused or feedback.pending_event or feedback.qualified or feedback.awaiting or
        feedback.retention_started_ns or feedback.retention_pending or feedback.retention_qualified or
        feedback.source_retention_started_ns or feedback.temporary_host_qualified or
        not feedback.session or not producer or producer != feedback.availability_producer or
        not CS.canValid or CS.canTimeout or CS.accFaulted or CS.steerFaultTemporary or CS.steerFaultPermanent or
        CS.steeringDisengage or CS.doorOpen or CS.seatbeltUnlatched or CS.brakePressed or CS.gasPressed or
        CS.gearShifter not in (car.CarState.GearShifter.drive, car.CarState.GearShifter.sport,
                              car.CarState.GearShifter.low, car.CarState.GearShifter.brake) or
        not any(event.type == car.CarState.ButtonEvent.Type.cancel and event.pressed for event in CS.buttonEvents) or
        not 0 < cancel_source_ns <= now_ns or now_ns - cancel_source_ns > AXIS_MAX_AGE_NS):
      return False
    prior = feedback.signature
    if (prior is None or len(prior) != 16 or prior[0] != feedback.session or prior[1] != feedback.sequence or
        prior[2] != prior[3] or prior[5] != 'none' or prior[7] != feedback.session or
        not all(prior[index] for index in (9, 10, 11, 12, 13)) or
        prior[14] != producer or prior[15] != feedback.availability_epoch or
        not 0 < prior[8] <= prior[3] <= feedback.lateral_baseline_ns <= now_ns <= prior[4] or
        prior[4] - prior[3] > AXIS_MAX_AGE_NS or prior[8] != feedback.lateral_source_ns or
        now_ns - feedback.lateral_baseline_ns > AXIS_MAX_AGE_NS or feedback.lateral_only_baseline or
        not 0 < feedback.healthy_feedback_ns <= now_ns or now_ns - feedback.healthy_feedback_ns > AXIS_MAX_AGE_NS or
        not 0 < feedback.lateral_native_ns <= feedback.healthy_native_ns <= now_ns or
        now_ns - feedback.healthy_native_ns > SAFETY_MAX_AGE_NS or
        feedback.lateral_inventory is None or feedback.lateral_inventory != feedback.healthy_inventory):
      return False
    axis = sm['aolAxisState']
    stamp = int(sm.logMonoTime['aolAxisState'])
    signature = (str(axis.sessionId), int(axis.sequence), stamp, int(axis.observedMonoTime),
                 int(axis.validUntilMonoTime), str(axis.faultReason), int(axis.faultEventMonoTime), str(axis.faultSessionId),
                 int(axis.sourceCarStateMonoTime), bool(axis.lateralActive), bool(axis.longitudinalActive),
                 bool(axis.desiredLateral), bool(axis.desiredLongitudinal), bool(axis.nativeAcknowledged),
                 str(axis.intentProducerSessionId), int(axis.intentIntegrityEpoch))
    if (not sm.seen['aolAxisState'] or not sm.valid['aolAxisState'] or not sm.alive['aolAxisState'] or
        not axis.qualified or axis.sessionId != feedback.session or axis.faultSessionId != feedback.session or
        str(axis.faultReason) != 'none' or str(axis.mode) != 'combined' or not axis.nativeAcknowledged or
        not axis.desiredLateral or not axis.lateralActive or not axis.desiredLongitudinal or not axis.longitudinalActive or
        axis.intentProducerSessionId != producer or axis.intentIntegrityEpoch != feedback.availability_epoch or
        axis.sequence < feedback.sequence or axis.sequence == feedback.sequence and
          signature != prior or
        not 0 < axis.sourceCarStateMonoTime <= stamp == axis.observedMonoTime <= cancel_source_ns <= now_ns <= axis.validUntilMonoTime or
        axis.validUntilMonoTime - stamp > AXIS_MAX_AGE_NS or now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS):
      return False
    event_ns = int(sm.logMonoTime['onroadEvents'])
    if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
        not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
        any(event.immediateDisable or event.softDisable or
            event.userDisable and event.name != log.OnroadEvent.EventName.buttonCancel for event in sm['onroadEvents'])):
      return False
    native = independent_lateral_health(sm, CP, CS, feedback.session, now_ns)
    owner_index = 0 if len(CP.safetyConfigs) == 1 else config_index(CP)
    if (native is None or not native.pandaInventory or not native.requestedLateral or not native.lateralAllowed or
        not native.requestedLongitudinal or native.longitudinalAllowed or
        native.observedMonoTime < feedback.healthy_native_ns or
        (native.pandaSerial, tuple(slot.hardwareSerial for slot in native.pandaInventory)) != feedback.healthy_inventory or
        owner_index is None or len(native.pandaInventory) != len(CP.safetyConfigs) or
        native.pandaInventory[owner_index].controlsAllowed):
      return False
    return True
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return False


def expected_stopped_lateral_denial(CP, CS, native, *, sm, axis_session_id, now_ns):
  """A healthy angle stop retains intent while native steering stays withdrawn."""
  import math
  from openpilot.starpilot.aol.transport_pause import _configured_transport_native

  try:
    return bool(qualified_angle_aol(CP, marked_only=True) and
      CS.canValid and not CS.canTimeout and not CS.steerFaultTemporary and not CS.steerFaultPermanent and
      not CS.accFaulted and CS.gearShifter == car.CarState.GearShifter.drive and
      math.isfinite(CS.vEgoRaw) and 0.0 <= CS.vEgoRaw <= 0.3 and
      native is not None and native.requestedLateral and not native.lateralAllowed and
      native.pandaInventory and
      _configured_transport_native(sm, CP, axis_session_id, now_ns, native,
                                   allow_restricted_lateral=True) == native)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return False


def pending_stock_angle_arm(CP, CS, sm, owner, *, now_ns, producer, acknowledged_arm_ns=0):
  """Keep a fresh physical arm pending its original native acknowledgement."""
  from opendbc.car.hyundai.canfd_angle_aol import ANGLE_ACK_TIMEOUT_NS
  from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, current_axis
  from openpilot.starpilot.aol.transport_pause import _configured_transport_native

  try:
    arm = owner.physical_arm_ns
    if (not qualified_angle_aol(CP, marked_only=True) or CP.openpilotLongitudinalControl or not CP.pcmCruise or
        not owner.allowed_latch or not owner.settings.enabled or owner.pause_lateral or
        arm != owner._last_latch_edge_ns or arm == acknowledged_arm_ns or not 0 < arm <= now_ns < arm + ANGLE_ACK_TIMEOUT_NS or
        not CS.canValid or CS.canTimeout or CS.steerFaultTemporary or CS.steerFaultPermanent or CS.accFaulted or
        CS.gearShifter != car.CarState.GearShifter.drive or temporary_restriction(CP, CS)):
      return False
    axis = current_axis(sm, now_ns=now_ns)
    if (axis is None or str(axis.faultReason) != 'none' or axis.faultSessionId != axis.sessionId or
        not axis.desiredLateral or axis.lateralActive or axis.desiredLongitudinal or axis.longitudinalActive or
        axis.observedMonoTime != sm.logMonoTime['aolAxisState'] or
        not 0 < axis.sourceCarStateMonoTime <= axis.observedMonoTime <= now_ns or
        now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS or
        axis.validUntilMonoTime - axis.observedMonoTime > AXIS_MAX_AGE_NS or
        axis.intentProducerSessionId != producer or not producer):
      return False
    event_ns = int(sm.logMonoTime['onroadEvents'])
    if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
        not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
        any(event.immediateDisable or event.softDisable or event.userDisable for event in sm['onroadEvents'])):
      return False
    native = _configured_transport_native(sm, CP, axis.sessionId, now_ns, allow_restricted_lateral=True)
    return bool(native is not None and native.pandaInventory and not native.lateralAllowed and
                not native.requestedLongitudinal and not native.longitudinalAllowed)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return False


def retain_stopped_eps_arm(feedback, sm, cp, cs, axis, now_ns, *, latched, arm_ns, producer):
  from openpilot.cereal import log
  from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS
  from openpilot.starpilot.aol.transport_pause import _configured_transport_native

  pending = getattr(feedback, '_stopped_eps_arm', None)
  feedback._stopped_eps_arm = None
  if not cs.steerFaultTemporary and pending is None:
    return False
  if (not latched or feedback.failed or feedback.paused or feedback.pending_event or
      feedback.retention_started_ns or cs.steerFaultPermanent or not cs.canValid or cs.canTimeout or
      cs.accFaulted or cs.doorOpen or cs.vehicleSensorsInvalid or cs.steeringDisengage or
      cs.gearShifter not in (car.CarState.GearShifter.drive, car.CarState.GearShifter.sport,
                            car.CarState.GearShifter.low, car.CarState.GearShifter.brake) or
      str(axis.faultReason) != 'none' or axis.longitudinalActive or axis.desiredLongitudinal or
      not producer or axis.intentProducerSessionId != producer or
      not 0 < axis.sourceCarStateMonoTime <= axis.observedMonoTime <= now_ns or
      now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS):
    return False
  binding = (int(arm_ns), str(axis.sessionId), producer, int(axis.intentIntegrityEpoch))
  if pending is None:
    if (not cs.steerFaultTemporary or not cs.standstill or not cs.brakePressed or
        not 0 < arm_ns <= now_ns <= arm_ns + AXIS_MAX_AGE_NS or
        not qualified_classic_scc(cp, marked_only=True) or
        axis.lateralActive or axis.desiredLateral):
      return False
  elif pending[:4] != binding or not 0 <= now_ns - pending[5] <= AXIS_MAX_AGE_NS:
    return False
  event_ns = int(sm.logMonoTime['onroadEvents'])
  temporary = (log.OnroadEvent.EventName.steerTempUnavailable,
               log.OnroadEvent.EventName.steerTempUnavailableSilent)
  allowed = (*temporary, log.OnroadEvent.EventName.wrongCarMode,
             log.OnroadEvent.EventName.preEnableStandstill, log.OnroadEvent.EventName.pcmDisable,
             log.OnroadEvent.EventName.pedalPressed, log.OnroadEvent.EventName.gasPressedOverride,
             log.OnroadEvent.EventName.steerOverride)
  events = sm['onroadEvents']
  if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
      not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
      any(e.name not in allowed or e.immediateDisable or e.softDisable and e.name not in temporary for e in events) or
      cs.steerFaultTemporary and not any(e.name in temporary for e in events)):
    return False
  native = _configured_transport_native(sm, cp, str(axis.sessionId), now_ns,
    require_longitudinal=False, allow_restricted_lateral=True)
  if native is None or not native.pandaInventory or native.requestedLongitudinal or native.longitudinalAllowed:
    return False
  inventory = (native.pandaSerial, tuple(slot.hardwareSerial for slot in native.pandaInventory))
  if pending is not None and inventory != pending[4]:
    return False
  if axis.lateralActive:
    return False
  feedback._stopped_eps_arm = (*binding, inventory, now_ns)
  return True
