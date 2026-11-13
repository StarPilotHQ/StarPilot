"""Subaru physical cruise intent with independent lateral authority."""
from opendbc.car import structs
from opendbc.car.subaru.stock_aol import IMPREZA_MAIN_AOL, qualified, temporary_restriction
from opendbc.car.subaru.values import CAR
from openpilot.starpilot.aol.intent import AolCardIntent
from openpilot.starpilot.aol.policy import AolVehiclePolicy


class SubaruStockCardIntent(AolCardIntent):
  observe_stock_engagement = True

  def __init__(self, settings, *, main_arm=False):
    super().__init__(settings, explicit_latch=True, lkas_button_supported=False)
    self.main_arm = main_arm
    self._previous_main = None
    self._main_observed_ns = 0
    self._main_event_ns = 0
    self._pending_main_ns = 0
    self._pending_main_after_ns = 0
    self.main_arm_ns = 0

  def observe_startup_readiness(self, sm, CS, *, now_ns, **kwargs):
    if self.main_arm:
      stamp = int(sm.logMonoTime['onroadEvents'])
      self._main_event_ns = stamp if (sm.valid['onroadEvents'] and 0 < stamp <= now_ns and
                                     now_ns - stamp <= 200_000_000) else 0

  def update(self, CS, **kwargs):
    super().update(CS, **kwargs)
    now_ns = kwargs.get('now_ns', 0)
    main = bool(CS.cruiseState.available)
    fresh_main = self._previous_main is False and main
    physical_safe = (self.settings.enabled and CS.canValid and not CS.canTimeout and
                     not self._calibration_inhibit and not self._calibration_rearm_required and
                     not CS.steerFaultPermanent and not CS.steerFaultTemporary and not CS.accFaulted and
                     CS.gearShifter == structs.CarState.GearShifter.drive)
    if self.main_arm and fresh_main and physical_safe:
      self._pending_main_ns = now_ns
      self._pending_main_after_ns = self._main_observed_ns
    self._previous_main = main if CS.canValid and not CS.canTimeout else None
    self._main_observed_ns = now_ns
    if self._pending_main_ns:
      if not main or not physical_safe or not 0 <= now_ns - self._pending_main_ns <= 200_000_000:
        self._pending_main_ns = 0
      elif self._main_event_ns > self._pending_main_after_ns and kwargs.get('fault_active') is not None:
        # Wait for the event observation after MAIN; an earlier MAIN-off fault
        # cannot decide this edge. A new fault still consumes it without arming.
        if not kwargs['fault_active'] and not self._fault_inhibit:
          self.allowed_latch = True
          self.pause_lateral = False
          self._last_latch_edge_ns = self._pending_main_ns
          self.main_arm_ns = self._pending_main_ns
        self._pending_main_ns = 0
    if not main:
      self.main_arm_ns = 0
      self._record_latch_clear('subaru_main_off')
      self.allowed_latch = False
      self._withdraw_full_engagement()


def policy_for(cp):
  admitted = qualified(cp)
  return AolVehiclePolicy(intent_supported=admitted, settings_supported=admitted, runtime_supported=admitted,
                          normal_runtime_supported=admitted, full_axis_runtime_required=qualified(cp, marked_only=True),
                          ordinary_axis_ack_required=qualified(cp, marked_only=True), explicit_latch=admitted,
                          fixed_cruise_buttons=admitted, lkas_button_supported=False,
                          alternative_experience_addition=32 if admitted else 0,
                          safety_param_addition=IMPREZA_MAIN_AOL if admitted and cp.carFingerprint == CAR.SUBARU_IMPREZA_2020 else 0)


def native_profile_supported(model, param):
  return model == int(structs.CarParams.SafetyModel.subaru) and param in (
    0, 1, 8, 16, 17, 145, 177, 209, 401, 465, 512, 520, 1169, 1425, 2225, 4096, 4104, 4608, 4616)


def native_accepts_cp(cp, model, param):
  return qualified(cp, marked_only=True) and native_profile_supported(model, param)


def native_latch_rejected(cp, native):
  return qualified(cp, marked_only=True) and native.requestedLateral and not native.lateralAllowed


def retain_on_native_denial(cp, native, state):
  return temporary_restriction(cp, state)


def ordinary_axis_request_allowed(cp, state):
  return (qualified(cp, marked_only=True) and state.canValid and not state.canTimeout and
          not state.steerFaultPermanent and not state.accFaulted)


def create_intent(cp, settings):
  main_arm = (cp.carFingerprint == CAR.SUBARU_CROSSTREK_2025 or
              (cp.carFingerprint in (CAR.SUBARU_IMPREZA_2020, CAR.SUBARU_OUTBACK_2023) and qualified(cp)))
  return SubaruStockCardIntent(settings, main_arm=main_arm)


def sng_standstill_lateral_inhibit(cp, state):
  # All stock Subaru native profiles require movement. Withdraw the request before its expected denial,
  # preserving ordinary stock ACC without granting either active axis.
  return bool(qualified(cp, marked_only=True) and state.standstill)


def temporary_eps_brake_recovery(cp, state, *, previous_brake=False):
  return bool(qualified(cp, marked_only=True) and state.canValid and not state.canTimeout and
              not state.steerFaultPermanent and not state.accFaulted and not state.doorOpen and
              not state.vehicleSensorsInvalid and not state.steeringDisengage and not state.buttonEvents and
              state.cruiseState.available and not state.cruiseState.enabled and
              state.gearShifter == structs.CarState.GearShifter.drive and
              (state.brakePressed or previous_brake) and not state.regenBraking and not state.gasPressed)


def retain_braked_main_intent(feedback, sm, cp, state, axis, now_ns, *, arm_ns, producer):
  from openpilot.cereal import log
  from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS
  from openpilot.starpilot.aol.transport_pause import _configured_transport_native
  pending = feedback.subaru_braked_main
  epoch = int(axis.intentIntegrityEpoch)
  if (cp.carFingerprint != CAR.SUBARU_IMPREZA_2020 or not qualified(cp, marked_only=True) or
      feedback.failed or feedback.paused or feedback.pending_event or feedback.ever_lateral_active or
      not producer or axis.intentProducerSessionId != producer or
      not 0 < arm_ns <= axis.sourceCarStateMonoTime <= now_ns or
      str(axis.faultReason) != 'none' or not axis.nativeAcknowledged or not axis.desiredLateral or axis.lateralActive or
      axis.desiredLongitudinal or axis.longitudinalActive or str(axis.mode) != 'off' or
      not state.canValid or state.canTimeout or state.steerFaultPermanent or state.steerFaultTemporary or
      state.accFaulted or state.vehicleSensorsInvalid or state.doorOpen or state.seatbeltUnlatched or
      state.steeringDisengage or state.buttonEvents or state.gasPressed or state.regenBraking or
      not state.cruiseState.available or state.cruiseState.enabled or
      state.gearShifter != structs.CarState.GearShifter.drive):
    return False
  if pending is None:
    if (not state.brakePressed or now_ns - arm_ns > SAFETY_MAX_AGE_NS or
        not 0 < feedback.healthy_feedback_ns <= now_ns or now_ns - feedback.healthy_feedback_ns > AXIS_MAX_AGE_NS):
      return False
  elif pending[:3] != (arm_ns, producer, epoch) or not state.brakePressed and now_ns - pending[3] > SAFETY_MAX_AGE_NS:
    return False
  event_ns = int(sm.logMonoTime['onroadEvents'])
  permitted_users = (log.OnroadEvent.EventName.pcmDisable, log.OnroadEvent.EventName.pedalPressed)
  if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
      not 0 < event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
      any(e.immediateDisable or e.softDisable or e.noEntry and e.name != log.OnroadEvent.EventName.pedalPressed or
          e.userDisable and e.name not in permitted_users for e in sm['onroadEvents'])):
    return False
  native = _configured_transport_native(sm, cp, str(axis.sessionId), now_ns, allow_restricted_lateral=True)
  if (native is None or not native.pandaInventory or not native.requestedLateral or native.lateralAllowed or
      native.requestedLongitudinal or native.longitudinalAllowed or feedback.healthy_inventory !=
      (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory))):
    return False
  # Accepted MAIN survives the brake veto; no permission or original transport lease is renewed.
  feedback.subaru_braked_main = (arm_ns, producer, epoch, now_ns if state.brakePressed else pending[3])
  feedback.subaru_main_retained_native_ns = int(native.observedMonoTime)
  return True



def retain_stopped_intent(feedback, sm, cp, state, axis, now_ns, *, latched, producer, signature):
  from openpilot.cereal import log
  from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, SAFETY_MAX_AGE_NS
  from openpilot.starpilot.aol.transport_pause import _healthy_transport_native, cp_identity

  previous = feedback.signature
  if (cp.carFingerprint != CAR.SUBARU_IMPREZA_2020 or not qualified(cp, marked_only=True) or
      not latched or not feedback.ever_lateral_active or feedback.failed or feedback.paused or
      feedback.pending_event or feedback.retention_started_ns or feedback.source_retention_started_ns or
      not feedback.session or cp_identity(cp) != feedback.cp or previous is None or signature != previous or
      previous[0] != feedback.session or previous[7] != feedback.session or previous[5] != 'none' or
      any(previous[9:13]) or not previous[13] or not producer or previous[14] != producer or
      not sm.seen['aolAxisState'] or not sm.valid['aolAxisState'] or not sm.alive['aolAxisState'] or
      not axis.qualified or str(axis.mode) != 'off' or
      not 0 < previous[8] <= previous[2] <= now_ns or not 0 < feedback.healthy_feedback_ns <= now_ns or
      previous[2] - previous[8] > AXIS_MAX_AGE_NS or previous[4] != previous[2] + AXIS_MAX_AGE_NS or
      not previous[4] < now_ns or now_ns - previous[8] > SAFETY_MAX_AGE_NS or
      not state.canValid or state.canTimeout or not state.standstill or not state.brakePressed or
      state.gearShifter != structs.CarState.GearShifter.drive or not state.cruiseState.available or
      state.cruiseState.enabled or state.gasPressed or state.regenBraking or state.buttonEvents or
      state.steerFaultTemporary or state.steerFaultPermanent or state.accFaulted or state.doorOpen or
      state.seatbeltUnlatched or state.vehicleSensorsInvalid or state.steeringDisengage):
    return False
  event_ns = int(sm.logMonoTime['onroadEvents'])
  allowed = (log.OnroadEvent.EventName.preEnableStandstill, log.OnroadEvent.EventName.pcmDisable,
             log.OnroadEvent.EventName.userBookmarkNotPaired)
  if (not sm.seen['onroadEvents'] or not sm.valid['onroadEvents'] or not sm.alive['onroadEvents'] or
      not 0 < axis.faultEventMonoTime <= event_ns <= now_ns or now_ns - event_ns > 1_500_000_000 or
      any(event.name not in allowed or event.immediateDisable or event.softDisable or
          event.userDisable and event.name != log.OnroadEvent.EventName.pcmDisable for event in sm['onroadEvents'])):
    return False
  native = _healthy_transport_native(sm, cp, state, feedback.session, now_ns)
  # Retain only the original OFF intent receipt; no source, output or native lease is renewed.
  return bool(native is not None and native.pandaInventory and
    not (native.requestedLateral or native.lateralAllowed or native.requestedLongitudinal or native.longitudinalAllowed) and
    native.observedMonoTime >= feedback.healthy_native_ns and feedback.healthy_inventory ==
      (native.pandaSerial, tuple(slot.hardwareSerial for slot in native.pandaInventory)))
