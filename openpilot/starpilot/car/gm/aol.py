"""GM-owned AOL capabilities; exact CP admission lives with the vehicle."""

import math
from opendbc.car.gm.cancel_remap import aol_enabled, enabled as cancel_remap_enabled, supported as cancel_remap_supported

from opendbc.car.structs import CarParams
from opendbc.car.gm.values import CAR, GMFlags, control_flags, camera_acc_pedal_profile, is_volt_ascm_longitudinal, is_volt_flashed_profile
from opendbc.car.gm.aol import GM_AOL_ALTERNATIVE_EXPERIENCE, GM_AOL_WORDS, qualified_gm
from openpilot.starpilot.aol.policy import AolVehiclePolicy
from openpilot.starpilot.aol.intent import AOL_TOGGLE, PAUSE_LATERAL, AolCardIntent


def policy_for(cp) -> AolVehiclePolicy:
  qualified = qualified_gm(cp)
  return AolVehiclePolicy(intent_supported=qualified, settings_supported=qualified,
                         runtime_supported=qualified, normal_runtime_supported=qualified,
                         lkas_button_supported=qualified and is_volt_flashed_profile(cp),
                         full_axis_runtime_required=qualified and aol_enabled(cp),
                         alternative_experience_addition=GM_AOL_ALTERNATIVE_EXPERIENCE if qualified else 0)


def native_profile_supported(model: int, param: int) -> bool:
  return model == int(CarParams.SafetyModel.gm) and param in GM_AOL_WORDS


def native_accepts_cp(cp, model: int, param: int) -> bool:
  return (qualified_gm(cp) and aol_enabled(cp) and
          native_profile_supported(model, param))


def cc_independent_lateral_below_engage(cp, intent, *, enabled, active):
  """An armed conventional-cruise LAT request uses its steering speed gate."""
  from opendbc.car.gm.values import is_bolt_cc_profile, is_volt_cc_profile
  return bool(not enabled and not active and qualified_gm(cp) and aol_enabled(cp) and
              (is_bolt_cc_profile(cp) or is_volt_cc_profile(cp)) and intent is not None and intent.settingsQualified and
              intent.allowedLatch and intent.lateralArmed and not intent.pauseLateral)


def f1_conventional_lateral_owner(cp) -> bool:
  """Exact development F1 conventional-cruise owner; no pedal or ACC profile."""
  from opendbc.car.gm.values import VOLT_CC_F1_WORD, is_volt_cc_profile
  return bool(is_volt_cc_profile(cp) and qualified_gm(cp) and aol_enabled(cp) and
              int(cp.safetyConfigs[0].safetyParam) == VOLT_CC_F1_WORD)


def sdgm_alpha_lateral_owner(cp) -> bool:
  from opendbc.car.gm.values import is_volt_sdgm_accepted_envelope
  return bool(qualified_gm(cp) and aol_enabled(cp) and is_volt_sdgm_accepted_envelope(cp))


def steering_raw_projection_owner(cp) -> bool:
  """Qualified non-pedal Volt owners consume their exact raw steering receipt."""
  from opendbc.car.gm.steering_authority import monitored_profile
  return bool(qualified_gm(cp) and monitored_profile(cp))



def steering_source_gap_intent_only(owner, cs, companion, now_ns: int) -> bool:
  """Recognize lost Card publication; never supply a current steering receipt."""
  from openpilot.starpilot.aol.runtime import INTENT_MAX_AGE_NS
  service = 'starpilotCarState'
  sdgm = sdgm_alpha_lateral_owner(owner.CP)
  if (not (is_volt_ascm_longitudinal(owner.CP) or sdgm) or not steering_raw_projection_owner(owner.CP) or
      getattr(owner, 'steering_authority_unavailable', False) or
      getattr(owner, 'car_state_samples', ())):
    return False
  anchor = getattr(owner, 'aol_ordinary_source_anchor', None)
  accepted = anchor[2] if anchor is not None else None
  held = bool(accepted is not None and
    getattr(owner, 'aol_publication_retained_intent', None) is accepted and
    getattr(owner, 'aol_published_fault_reason', '') == 'availabilityPause')
  if not (getattr(owner, 'aol_expired_healthy_source', False) or held):
    return False
  source = getattr(owner, 'car_state_retained_source_ns', 0)
  if not source and held:
    source = int(accepted.carStateLogMonoTime)
  latest = companion.latest
  sm = owner.sm
  if (latest is None or not latest[1] or latest[2] != latest[0] or latest[3] or
      not (latest[0] == source and not sdgm or sdgm and source < latest[0] <= now_ns and
           now_ns - latest[0] <= INTENT_MAX_AGE_NS) or
      not 0 < source < now_ns or now_ns - source <= INTENT_MAX_AGE_NS or
      not sm.seen.get(service, False) or not sm.valid.get(service, False) or not sm.alive.get(service, False)):
    return False
  if sdgm and any(not valid or stamp != observed_source or unavailable
      for stamp, valid, observed_source, unavailable in companion.samples.values() if stamp > source):
    return False
  cached = sm[service]
  stamp = int(sm.logMonoTime[service])
  if (not 0 < stamp <= (latest[0] if sdgm else source) or cached.sourceCarStateMonoTime != stamp or
      cached.lateralAuthorityUnavailable):
    return False
  # The existing classifier owns every original 200ms intent/native, identity,
  # physical, fault and host-health veto. The 30ms source/output cutoff remains
  # independent. No old receipt becomes current and no fault latch is cleared.
  return owner.ordinary_source_availability(cs, now_ns)


def held_gm_lateral_pedal(owner, cs) -> bool:
  """An already accepted LAT-only pedal state remains intent, never output."""
  from openpilot.starpilot.nostalgia import physical_cancel
  anchor = getattr(owner, 'aol_ordinary_source_anchor', None)
  if not qualified_gm(owner.CP) or not aol_enabled(owner.CP) or anchor is None:
    return False
  proof, native, accepted, _, accepted_state = anchor
  settings = getattr(owner, 'aol_settings', None)
  return bool(cs is accepted_state and not getattr(owner, 'car_state_samples', ()) and
    proof[8] and not proof[9] and not owner.enabled and not owner.active and
    native is not None and native.requestedLateral and native.lateralAllowed and
    accepted is not None and accepted.allowedLatch and accepted.lateralArmed and
    accepted.settingsQualified and not accepted.pauseLateral and not accepted.pauseLongitudinal and
    settings is not None and settings.enabled and
    not getattr(owner, 'aol_dm_lateral_inhibit', False) and
    cs.cruiseState.available and (cs.brakePressed or cs.regenBraking) and
    not cs.steeringDisengage and not cs.gasPressed and
    not cs.seatbeltUnlatched and not physical_cancel(cs, owner.CP) and
    (not cs.brakePressed or cs.standstill or cs.vEgo >= settings.pause_brake_mps))



def gm_lateral_pedal_source_zero(feedback, cp, cs, axis, native, now_ns, *, producer):
  """Fresh physical braking cannot turn an accepted LAT-only source wait fatal."""
  from openpilot.starpilot.aol.runtime import SAFETY_MAX_AGE_NS
  from openpilot.starpilot.nostalgia import physical_cancel
  from opendbc.car.structs import car
  prior = feedback.signature
  return bool(qualified_gm(cp) and aol_enabled(cp) and feedback.ever_lateral_active and
    not feedback.failed and not feedback.pending_event and prior is not None and
    prior[0] == feedback.session and prior[7] == feedback.session and
    prior[14:16] == (producer, feedback.availability_epoch) and
    prior[11] and prior[13] and not prior[10] and not prior[12] and
    (prior[5] == 'none' and prior[9] or feedback.retention_verified and
      prior[5] == 'availabilityPause' and prior[8] == 0 and not prior[9]) and
    0 < feedback.lateral_source_ns <= now_ns <= feedback.lateral_source_ns + SAFETY_MAX_AGE_NS and
    str(axis.faultReason) == 'availabilityPause' and axis.sourceCarStateMonoTime == 0 and
    axis.desiredLateral and axis.nativeAcknowledged and not axis.desiredLongitudinal and
    not axis.lateralActive and not axis.longitudinalActive and
    cs.canValid and not cs.canTimeout and cs.cruiseState.available and
    not cs.steerFaultTemporary and not cs.steerFaultPermanent and not cs.accFaulted and
    cs.gearShifter in (car.CarState.GearShifter.drive, car.CarState.GearShifter.low,
                      car.CarState.GearShifter.sport, car.CarState.GearShifter.brake,
                      car.CarState.GearShifter.manumatic) and
    (cs.brakePressed or cs.regenBraking) and not cs.doorOpen and not cs.steeringDisengage and
    not cs.gasPressed and not cs.seatbeltUnlatched and
    not physical_cancel(cs, cp) and native is not None and
    native.requestedLateral and native.lateralAllowed)


def cold_gm_main_intent_current(feedback, sm, cp, cs, axis, native, now_ns, *, producer):
  """Cold MAIN may wait on a current SOFT occurrence without renewing output."""
  from opendbc.car.structs import car
  from openpilot.starpilot.nostalgia import physical_cancel
  from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS
  event_ns = int(sm.logMonoTime['onroadEvents'])
  return bool(qualified_gm(cp) and aol_enabled(cp) and
    not feedback.failed and not feedback.paused and not feedback.pending_event and
    not feedback.ever_lateral_active and str(axis.faultReason) == 'availabilityPause' and
    not axis.desiredLateral and not axis.desiredLongitudinal and
    not axis.lateralActive and not axis.longitudinalActive and
    producer and str(axis.intentProducerSessionId) == producer and
    (feedback.signature is None or
      (str(axis.intentProducerSessionId), int(axis.intentIntegrityEpoch)) == feedback.signature[14:16]) and
    0 < axis.sourceCarStateMonoTime <= axis.observedMonoTime <= now_ns and
    now_ns - axis.sourceCarStateMonoTime <= AXIS_MAX_AGE_NS and
    axis.faultEventMonoTime == event_ns and sm.seen['onroadEvents'] and
    sm.valid['onroadEvents'] and sm.alive['onroadEvents'] and
    0 < event_ns <= axis.observedMonoTime and now_ns - event_ns <= 1_500_000_000 and
    cs.gearShifter in (car.CarState.GearShifter.drive, car.CarState.GearShifter.low,
                      car.CarState.GearShifter.sport, car.CarState.GearShifter.brake,
                      car.CarState.GearShifter.manumatic) and
    not physical_cancel(cs, cp) and
    cold_main_readiness(cp, cs, native, sm['onroadEvents']))


def sdgm_alpha_pedal_withdrawal(cp, cs) -> bool:
  """Physical LONG withdrawal for the 2019 SASCM Alpha configuration."""
  from opendbc.car.gm.values import is_volt_sdgm_accepted_envelope
  from opendbc.car.structs import car
  return bool(qualified_gm(cp) and aol_enabled(cp) and
              is_volt_sdgm_accepted_envelope(cp) and
              cs.canValid and not cs.canTimeout and cs.cruiseState.available and
              not cs.steerFaultTemporary and not cs.steerFaultPermanent and not cs.accFaulted and
              not cs.doorOpen and not cs.steeringDisengage and
              cs.gearShifter in (car.CarState.GearShifter.drive, car.CarState.GearShifter.low,
                                car.CarState.GearShifter.sport, car.CarState.GearShifter.brake,
                                car.CarState.GearShifter.manumatic) and
              (cs.brakePressed or cs.regenBraking))


def gm_physical_pedal_withdrawal(cp, cs) -> bool:
  """A qualified GM physical pedal withdraws LONG without canceling MAIN intent."""
  from opendbc.car.structs import car
  return bool(qualified_gm(cp) and aol_enabled(cp) and
              cs.canValid and not cs.canTimeout and cs.cruiseState.available and
              not cs.steerFaultTemporary and not cs.steerFaultPermanent and not cs.accFaulted and
              not cs.doorOpen and not cs.steeringDisengage and
              cs.gearShifter in (car.CarState.GearShifter.drive, car.CarState.GearShifter.low,
                                car.CarState.GearShifter.sport, car.CarState.GearShifter.brake,
                                car.CarState.GearShifter.manumatic) and
              (cs.brakePressed or cs.regenBraking))


def stock_ascm_independent_lateral(cp) -> bool:
  """Stock cruise's engagement speed does not cancel independent lateral intent."""
  return bool(qualified_gm(cp) and cp.carFingerprint == CAR.CHEVROLET_VOLT_ASCM and
              cp.pcmCruise and not cp.openpilotLongitudinalControl and
              not cp.flags & GMFlags.PEDAL_LONG)


class GmAolCardIntent(AolCardIntent):
  """Follow physical MAIN; a current fault inhibits intent until healthy again."""

  def __init__(self, settings, cp=None):
    super().__init__(settings, lkas_button_supported=is_volt_flashed_profile(cp))
    self.cp = cp
    self.requires_fault_observation = True
    self._main_cycle_required = False
    self._main_available = False
    self._main_arm_ns = 0
    self.startup_calibration_pending = True
    self._startup_incomplete = False

  def update_auxiliary(self, CS, *, media=None, media_eligible=False, fault_active=None):
    semantic = CS
    if (cancel_remap_supported(self.cp) and not cancel_remap_enabled(self.cp) and
        any(str(event.type) == 'cancel' for event in CS.buttonEvents)):
      semantic = CS.as_builder() if hasattr(CS, 'as_builder') else CS.as_reader().as_builder()
      semantic.buttonEvents = [event for event in CS.buttonEvents if str(event.type) != 'cancel']
    super().update_auxiliary(semantic, media=media, media_eligible=media_eligible, fault_active=fault_active)

  @property
  def lkas_action(self):
    # MAIN still owns automatic arming; a mapped LKAS toggle pauses steering.
    action = super().lkas_action
    return PAUSE_LATERAL if action == AOL_TOGGLE else action

  @property
  def main_action(self):
    # GM MAIN reports a persistent physical level, not an independent AOL toggle.
    return 0 if self.settings.main_action == AOL_TOGGLE else self.settings.main_action

  def update(self, CS, *, consumed_buttons=frozenset(), fault_active=None,
             now_ns=0, native_rejection_ns=0, standard_enabled=False):
    was_allowed = self.allowed_latch
    main_rise = False
    if CS.canValid and not CS.canTimeout:
      main_rise = bool(CS.cruiseState.available and not self._main_available)
      self._main_available = bool(CS.cruiseState.available)
    fault = bool(fault_active or CS.steerFaultPermanent)
    if fault:
      self._main_cycle_required = True
    elif fault_active is False or CS.canValid and not CS.canTimeout and not CS.cruiseState.available:
      # MAIN is a level on GM, including when already ON at startup. A fresh
      # healthy fault observation restores that request without a button cycle.
      self._main_cycle_required = False
    super().update(CS, consumed_buttons=consumed_buttons, fault_active=fault_active,
                   now_ns=now_ns, native_rejection_ns=native_rejection_ns,
                   standard_enabled=standard_enabled)
    if self._main_cycle_required:
      self.allowed_latch = False
      self.startup_calibration_pending = False
    if not self.allowed_latch:
      self._main_arm_ns = 0
    elif main_rise or not was_allowed:
      self._main_arm_ns = now_ns

  @property
  def main_arm_ns(self):
    return (self._main_arm_ns if self.settings.enabled and self.main_action != AOL_TOGGLE and
            not self.pause_lateral and not self.pause_longitudinal else 0)

  def _arm_from_full_engagement(self, CS, *, source_ns):
    self._main_cycle_required = False
    super()._arm_from_full_engagement(CS, source_ns=source_ns)

  def observe_calibration(self, CS, *, events=None, standard_enabled=False):
    from openpilot.cereal import log
    # Cold calibration readiness is not a previously revoked driver gesture.
    # The original calibration inhibit still withdraws all optional output.
    prior_incomplete = self._startup_incomplete
    cold_safe = True
    if events is not None:
      from openpilot.starpilot.aol.intent import disarming_fault
      remaining = [event for event in _intent_fault_events(self.cp, events) if
        event.name != log.OnroadEvent.EventName.calibrationIncomplete or event.immediateDisable]
      cold_safe = not disarming_fault(remaining, CS,
        temporary_selfdrive_lagging=qualified_gm(self.cp))
      self._startup_incomplete = bool(cold_safe and
        any(event.name == log.OnroadEvent.EventName.calibrationIncomplete for event in events))
    cold_readiness = bool(self.startup_calibration_pending and cold_safe and
      (prior_incomplete or self._startup_incomplete) and
      self.settings.enabled and self.main_action != AOL_TOGGLE and
      not CS.steerFaultPermanent and not self._main_cycle_required)
    if cold_readiness:
      self._calibration_rearm_required = False
    if (events is not None and cold_safe and not self._main_cycle_required and not any(
        event.name in (log.OnroadEvent.EventName.calibrationInvalid,
                       log.OnroadEvent.EventName.calibrationIncomplete,
                       log.OnroadEvent.EventName.calibrationRecalibrating) for event in events)):
      self._calibration_rearm_required = False
    super().observe_calibration(CS, events=events, standard_enabled=standard_enabled)
    if cold_readiness:
      self._calibration_rearm_required = False
    if (events is not None and CS.canValid and not CS.canTimeout and not self._startup_incomplete and
        not any(event.name == log.OnroadEvent.EventName.selfdriveInitializing for event in events)):
      self.startup_calibration_pending = False

  def output(self, CS):
    allowed, pause_lateral, pause_longitudinal = super().output(CS)
    return (bool(allowed and not CS.canTimeout and CS.cruiseState.available),
            pause_lateral, pause_longitudinal)


def create_intent(cp, settings):
  return GmAolCardIntent(settings, cp) if qualified_gm(cp) else None


def cold_park_readiness(cp, cs, native, events):
  """Retain cold MAIN intent while parked; acknowledge only idle native axes."""
  from opendbc.car.structs import car
  from openpilot.cereal import log
  names = (log.OnroadEvent.EventName.wrongGear, log.OnroadEvent.EventName.seatbeltNotLatched,
           log.OnroadEvent.EventName.commIssue, log.OnroadEvent.EventName.commIssueAvgFreq,
           log.OnroadEvent.EventName.locationdTemporaryError)
  return bool(qualified_gm(cp) and aol_enabled(cp) and
    cs.gearShifter == car.CarState.GearShifter.park and cs.standstill and
    math.isfinite(cs.vEgo) and abs(cs.vEgo) <= .3 and
    cs.canValid and not cs.canTimeout and cs.cruiseState.available and not cs.cruiseState.enabled and
    not cs.steerFaultTemporary and not cs.steerFaultPermanent and not cs.accFaulted and
    not cs.doorOpen and not cs.gasPressed and not cs.steeringDisengage and
    native is not None and native.pandaInventory and
    not native.requestedLateral and not native.lateralAllowed and
    not native.requestedLongitudinal and not native.longitudinalAllowed and
    not any(event.immediateDisable or event.softDisable and event.name not in names or
      event.userDisable and not (
        event.name == log.OnroadEvent.EventName.wrongGear and event.noEntry and event.softDisable or
        event.name == log.OnroadEvent.EventName.pedalPressed and (cs.brakePressed or cs.regenBraking) or
        event.name == log.OnroadEvent.EventName.pcmDisable and not cs.cruiseState.enabled)
      for event in events))


def cold_main_readiness(cp, cs, native, events, *, recovering=False):
  """A MAIN request may wait for host startup without acquiring either axis."""
  from openpilot.cereal import log
  names = (log.OnroadEvent.EventName.commIssue, log.OnroadEvent.EventName.commIssueAvgFreq,
           log.OnroadEvent.EventName.locationdTemporaryError, log.OnroadEvent.EventName.processNotRunning)
  faults = [event for event in events if event.softDisable or event.immediateDisable]
  host_wait = not faults if recovering else bool(faults and
    all(event.softDisable and not event.immediateDisable and event.name in names for event in faults))
  return bool(qualified_gm(cp) and aol_enabled(cp) and host_wait and
    not any(event.userDisable and not (gm_physical_pedal_withdrawal(cp, cs) and
      event.name == log.OnroadEvent.EventName.pedalPressed and (cs.brakePressed or cs.regenBraking) and
      not event.immediateDisable and not event.softDisable) for event in events) and
    cs.canValid and not cs.canTimeout and cs.cruiseState.available and
    not cs.steerFaultTemporary and not cs.steerFaultPermanent and not cs.accFaulted and
    not cs.doorOpen and not cs.seatbeltUnlatched and not cs.steeringDisengage and
    native is not None and native.pandaInventory and
    not native.requestedLateral and not native.lateralAllowed and
    not native.requestedLongitudinal and not native.longitudinalAllowed)


def main_off_idle_native_baseline(cp, cs, axis, native, events, *, producer):
  """Physical MAIN OFF can acknowledge zero native axes despite NO_ENTRY."""
  from opendbc.car.structs import car
  from openpilot.cereal import log
  return bool(qualified_gm(cp) and aol_enabled(cp) and cs.canValid and not cs.canTimeout and
    not cs.cruiseState.available and not cs.steerFaultTemporary and not cs.steerFaultPermanent and
    not cs.accFaulted and not cs.doorOpen and not cs.seatbeltUnlatched and not cs.steeringDisengage and
    cs.gearShifter in (car.CarState.GearShifter.drive, car.CarState.GearShifter.low,
                      car.CarState.GearShifter.sport, car.CarState.GearShifter.brake,
                      car.CarState.GearShifter.manumatic) and
    producer and str(axis.intentProducerSessionId) == producer and str(axis.faultReason) == 'none' and
    not axis.desiredLateral and not axis.desiredLongitudinal and
    not axis.lateralActive and not axis.longitudinalActive and
    native is not None and native.pandaInventory and
    not native.requestedLateral and not native.lateralAllowed and
    not native.requestedLongitudinal and not native.longitudinalAllowed and
    not any(event.immediateDisable or event.softDisable or event.userDisable and not (
      event.name in (log.OnroadEvent.EventName.wrongCarMode, log.OnroadEvent.EventName.pcmDisable) or
      event.name == log.OnroadEvent.EventName.pedalPressed and (cs.brakePressed or cs.regenBraking))
      for event in events))


def prior_main_off_intent_current(feedback, sm, cp, cs, axis, now_ns, *, latched, main_arm_ns, producer):
  """An older idle response cannot reject a newly armed cruise MAIN request."""
  from opendbc.car.structs import car
  from openpilot.cereal import log
  from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS
  from openpilot.starpilot.aol.transport_pause import _healthy_transport_native

  if (not qualified_gm(cp) or not latched or not 0 < main_arm_ns <= now_ns or
      now_ns - main_arm_ns > AXIS_MAX_AGE_NS or
      feedback.failed or feedback.paused or feedback.pending_event or
      feedback.retention_started_ns or feedback.source_retention_started_ns or
      feedback.longitudinal_cancel_ns or feedback.honda_brake_ns or feedback.gm_withdrawal_ns or
      not feedback.signature or not producer or
      (str(axis.intentProducerSessionId), int(axis.intentIntegrityEpoch)) != feedback.signature[14:16] or
      str(axis.intentProducerSessionId) != producer or
      not cs.canValid or cs.canTimeout or not cs.cruiseState.available or cs.accFaulted or cs.doorOpen or
      cs.steerFaultTemporary or cs.steerFaultPermanent or
      cs.gearShifter not in (car.CarState.GearShifter.drive, car.CarState.GearShifter.low,
                            car.CarState.GearShifter.sport, car.CarState.GearShifter.brake) or
      any(event.type == car.CarState.ButtonEvent.Type.cancel for event in cs.buttonEvents) or
      str(axis.faultReason) != 'none' or
      axis.desiredLateral or axis.desiredLongitudinal or axis.lateralActive or axis.longitudinalActive or
      not 0 < axis.sourceCarStateMonoTime <= axis.observedMonoTime or axis.sourceCarStateMonoTime >= main_arm_ns or
      now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS or
      not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
      not 0 < sm.logMonoTime['onroadEvents'] <= now_ns or
      now_ns - sm.logMonoTime['onroadEvents'] > 1_500_000_000):
    return False
  for event in sm['onroadEvents']:
    if event.immediateDisable or event.softDisable:
      return False
    if event.userDisable and not (
        event.name == log.OnroadEvent.EventName.pedalPressed and (cs.brakePressed or cs.regenBraking) or
        event.name == log.OnroadEvent.EventName.pcmDisable and not cs.cruiseState.enabled or
        event.name == log.OnroadEvent.EventName.wrongCarMode and sm.logMonoTime['onroadEvents'] < main_arm_ns):
      return False
  native = _healthy_transport_native(sm, cp, cs, str(axis.sessionId), now_ns)
  return bool(native is not None and native.pandaInventory and
              not native.requestedLateral and not native.requestedLongitudinal and
              not native.lateralAllowed and not native.longitudinalAllowed)


def configuration_settings_policy(cp):
  """Saving stock Silverado lateral intent does not establish its physical source graph."""
  try:
    if (cp.brand == 'gm' and cp.carFingerprint == CAR.CHEVROLET_MALIBU_HYBRID_CC and
        not cp.notCar and not cp.passive and cp.lateralTuning.which() == 'torque' and
        cp.steerControlType == CarParams.SteerControlType.torque and
        int(cp.alternativeExperience) in (0, GM_AOL_ALTERNATIVE_EXPERIENCE)):
      return AolVehiclePolicy(settings_supported=True, lkas_button_supported=False)
    if (cp.brand != 'gm' or cp.carFingerprint != CAR.CHEVROLET_SILVERADO_CC or
        cp.notCar or cp.passive or cp.networkLocation != CarParams.NetworkLocation.gateway or
        cp.transmissionType != CarParams.TransmissionType.automatic or not cp.radarUnavailable or
        not cp.pcmCruise or cp.openpilotLongitudinalControl or cp.alphaLongitudinalAvailable or
        control_flags(cp) != 0 or int(cp.alternativeExperience) not in (0, GM_AOL_ALTERNATIVE_EXPERIENCE) or
        len(cp.safetyConfigs) != 1 or cp.safetyConfigs[0].safetyModel != CarParams.SafetyModel.gm or
        int(cp.safetyConfigs[0].safetyParam) != 16 or cp.steerControlType != CarParams.SteerControlType.torque or
        cp.lateralTuning.which() != 'torque'):
      return None
    tune = cp.lateralTuning.torque
    if (not all(math.isfinite(value) for value in (tune.latAccelFactor, tune.latAccelOffset, tune.friction)) or
        tune.latAccelFactor <= 0 or tune.friction < 0):
      return None
    return AolVehiclePolicy(settings_supported=True, lkas_button_supported=False)
  except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
    return None


def _intent_fault_events(cp, events):
  from openpilot.cereal import log
  if qualified_gm(cp):
    availability = (log.OnroadEvent.EventName.commIssue, log.OnroadEvent.EventName.commIssueAvgFreq)
    events = [event for event in events if not
              (event.name in availability and event.softDisable and not event.immediateDisable)]
  if qualified_gm(cp) and camera_acc_pedal_profile(cp) is None and is_volt_ascm_longitudinal(cp):
    events = [event for event in events if event.name != log.OnroadEvent.EventName.accFaulted]
  return events


def intent_disarming_fault(cp, events, state, **context):
  """An ASCM Volt ACC fault withdraws axes without erasing main-armed intent."""
  from openpilot.cereal import log
  from openpilot.starpilot.aol.intent import disarming_fault
  cold_calibration = context.pop('startup_calibration_pending', False)
  if qualified_gm(cp) and cold_calibration and not state.steerFaultPermanent:
    events = [event for event in events if not (
      event.name == log.OnroadEvent.EventName.calibrationIncomplete and not event.immediateDisable)]
  return disarming_fault(_intent_fault_events(cp, events), state, **context)


def physical_longitudinal_withdrawal_source(cp, cs, parser, observation, now_ns) -> int:
  """Bind a qualified GM physical withdrawal to parsed CAN and local receipt."""
  from opendbc.car.gm.values import CruiseButtons, is_bolt_present_no_acc_pedal_profile
  from opendbc.car.structs import car
  try:
    if (not qualified_gm(cp) or not (is_bolt_present_no_acc_pedal_profile(cp) or f1_conventional_lateral_owner(cp)) or
        not aol_enabled(cp) or
        observation is None or observation[0] is not cs or not cs.canValid or cs.canTimeout):
      return 0
    fields = []
    if cs.brakePressed and parser.vl['ECMEngineStatus']['BrakePressed']:
      fields.append(('ECMEngineStatus', 'BrakePressed'))
    if (f1_conventional_lateral_owner(cp) and cs.brakePressed and
        parser.vl['EBCMBrakePedalPosition']['BrakePedalPosition'] >= 6):
      fields.append(('EBCMBrakePedalPosition', 'BrakePedalPosition'))
    if cs.regenBraking and parser.vl['EBCMRegenPaddle']['RegenPaddle']:
      fields.append(('EBCMRegenPaddle', 'RegenPaddle'))
    if (parser.vl['ASCMSteeringButton']['ACCButtons'] == CruiseButtons.CANCEL and
        any(e.type == car.CarState.ButtonEvent.Type.cancel and e.pressed for e in cs.buttonEvents)):
      fields.append(('ASCMSteeringButton', 'ACCButtons'))
    _, received_ns, can_source_ns, boot_ns = observation
    if not fields or not 0 < received_ns <= now_ns or now_ns - received_ns > 30_000_000:
      return 0
    # Parser/CAN batch use BOOTTIME. The fixed deadline uses the original
    # local MONOTONIC receipt; neither clock is converted or restamped.
    stamps = [int(parser.ts_nanos[message][signal]) for message, signal in fields]
    if not all(0 < stamp <= can_source_ns <= boot_ns and boot_ns - stamp <= 30_000_000 and
               can_source_ns - stamp <= 30_000_000 for stamp in stamps):
      return 0
    return received_ns
  except (AttributeError, KeyError, IndexError, TypeError, ValueError):
    return 0


def retained_physical_longitudinal_intent(feedback, sm, cp, cs, axis, now_ns, *, latched, source_ns, producer):
  """Retain accepted LAT intent through a bounded physical LONG withdrawal."""
  from opendbc.car.gm.values import is_bolt_present_no_acc_pedal_profile
  from opendbc.car.structs import car
  from openpilot.cereal import log
  from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS
  from openpilot.starpilot.aol.transport_pause import independent_lateral_health
  occurrence = feedback.gm_withdrawal_ns or source_ns
  if (not qualified_gm(cp) or not (is_bolt_present_no_acc_pedal_profile(cp) or f1_conventional_lateral_owner(cp)) or
      not aol_enabled(cp) or
      not latched or feedback.failed or feedback.paused or feedback.pending_event or
      feedback.retention_started_ns or not feedback.session or str(axis.sessionId) != feedback.session or
      not feedback.healthy_feedback_ns or not 0 <= now_ns - feedback.healthy_feedback_ns <= AXIS_MAX_AGE_NS or
      not 0 < occurrence <= now_ns or now_ns - occurrence > SAFETY_MAX_AGE_NS or
      not feedback.gm_withdrawal_ns and now_ns - occurrence > AXIS_MAX_AGE_NS or
      not producer or producer != feedback.availability_producer or
      str(axis.intentProducerSessionId) != producer or int(axis.intentIntegrityEpoch) != feedback.availability_epoch or
      cs.accFaulted or cs.doorOpen or not cs.cruiseState.available or
      cs.gearShifter not in (car.CarState.GearShifter.drive, car.CarState.GearShifter.low,
                            car.CarState.GearShifter.sport, car.CarState.GearShifter.brake) or
      str(axis.faultReason) != 'none' or not axis.desiredLateral or not axis.lateralActive or
      not axis.nativeAcknowledged or
      not 0 < axis.sourceCarStateMonoTime <= axis.observedMonoTime <= now_ns or
      now_ns - axis.sourceCarStateMonoTime > AXIS_MAX_AGE_NS):
    return None
  # The saved LAT contract treats a parsed unlatched belt as a LONG lock.
  # After rebuckling, accept only a prior producer's matching belt event and
  # LAT-only proof within the already established physical occurrence. Events
  # publish sparsely, so their stamp binds to faultEventMonoTime, not the newer
  # car-state source. The original axis/native leases remain unchanged.
  event_ns = int(sm.logMonoTime['onroadEvents'])
  prior_belt_proof = bool(feedback.gm_withdrawal_ns and
    feedback.gm_withdrawal_ns <= event_ns <= axis.observedMonoTime < now_ns and
    event_ns == axis.faultEventMonoTime and str(axis.faultSessionId) == feedback.session and
    int(sm.logMonoTime['aolAxisState']) == axis.observedMonoTime and
    now_ns <= axis.validUntilMonoTime <= axis.observedMonoTime + AXIS_MAX_AGE_NS and
    not axis.desiredLongitudinal and not axis.longitudinalActive)
  if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
      not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
      any(e.immediateDisable or e.softDisable and not
          ((cs.seatbeltUnlatched or prior_belt_proof) and e.name == log.OnroadEvent.EventName.seatbeltNotLatched and
           e.noEntry and not e.userDisable) for e in sm['onroadEvents'])):
    return None
  native = independent_lateral_health(sm, cp, cs, feedback.session, now_ns)
  if (native is None or not native.pandaInventory or not native.requestedLongitudinal or
      not any(not p.controlsAllowed for p in sm['pandaStates']) or
      native.observedMonoTime < feedback.healthy_native_ns or
      (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) != feedback.healthy_inventory):
    return None
  # One occurrence spans the held native request. Held/released samples and
  # duplicate Card calls cannot renew it; output/native leases remain original.
  feedback.gm_withdrawal_ns = occurrence
  return native


def physical_cancel_lateral_allowed(cp, cs, intent, native, *, source_ns, now_ns) -> bool:
  """Exact armed physical Cancel removes only the lateral NO_ENTRY restriction."""
  from opendbc.car.gm.values import is_bolt_present_no_acc_pedal_profile
  from opendbc.car.structs import car
  from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS, native_inventory_matches_cp
  try:
    return bool(qualified_gm(cp) and (is_bolt_present_no_acc_pedal_profile(cp) or f1_conventional_lateral_owner(cp)) and
      aol_enabled(cp) and
      intent is not None and intent.settingsQualified and intent.allowedLatch and intent.lateralArmed and
      intent.producerSessionId and intent.carStateLogMonoTime == source_ns and
      0 < source_ns <= intent.observedMonoTime <= now_ns <= intent.validUntilMonoTime and
      now_ns - source_ns <= AXIS_MAX_AGE_NS and
      # Card declares a 200ms wire lease; admission and this source remain 30ms fresh.
      intent.validUntilMonoTime - intent.observedMonoTime <= SAFETY_MAX_AGE_NS and
      cs.canValid and not cs.canTimeout and not cs.steerFaultTemporary and not cs.steerFaultPermanent and
      any(e.type == car.CarState.ButtonEvent.Type.cancel for e in cs.buttonEvents) and
      native is not None and native.requestedLateral and native.lateralAllowed and
      native.pandaInventory and native_inventory_matches_cp(native, cp) and
      0 < native.observedMonoTime <= now_ns <= native.validUntilMonoTime)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError):
    return False


def seatbelt_lateral_soft_event_allowed(cp, cs, intent, native, event, *, source_ns, now_ns, axis_session_id) -> bool:
  """The qualified saved LAT owner treats only its ordinary belt event as a LONG lock."""
  if not qualified_gm(cp):
    return False
  from opendbc.car.gm.values import is_bolt_present_no_acc_pedal_profile
  from opendbc.car.structs import car
  from openpilot.cereal import log
  from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS, native_inventory_matches_cp
  try:
    return bool(is_bolt_present_no_acc_pedal_profile(cp) and
      aol_enabled(cp) and
      event.name == log.OnroadEvent.EventName.seatbeltNotLatched and event.softDisable and
      event.noEntry and not event.immediateDisable and not event.userDisable and
      cs.seatbeltUnlatched and cs.canValid and not cs.canTimeout and not cs.accFaulted and not cs.doorOpen and
      not cs.steerFaultTemporary and not cs.steerFaultPermanent and cs.cruiseState.available and
      cs.gearShifter in (car.CarState.GearShifter.drive, car.CarState.GearShifter.low,
                        car.CarState.GearShifter.sport, car.CarState.GearShifter.brake) and
      intent is not None and intent.settingsQualified and intent.allowedLatch and intent.lateralArmed and
      intent.producerSessionId and intent.carStateLogMonoTime == source_ns and
      0 < source_ns <= intent.observedMonoTime <= now_ns <= intent.validUntilMonoTime and
      now_ns - source_ns <= AXIS_MAX_AGE_NS and
      intent.validUntilMonoTime - intent.observedMonoTime <= SAFETY_MAX_AGE_NS and
      native is not None and native.compatible and int(native.protocolVersion) == 1 and
      axis_session_id and native.axisSessionId == axis_session_id and
      native.requestedLateral and native.lateralAllowed and native.pandaInventory and
      native_inventory_matches_cp(native, cp) and
      0 < native.observedMonoTime <= now_ns <= native.validUntilMonoTime and
      native.validUntilMonoTime - native.observedMonoTime <= SAFETY_MAX_AGE_NS)
  except (AttributeError, KeyError, IndexError, TypeError, ValueError):
    return False
