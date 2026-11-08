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
        self._pending_main_ns = 0
    if not main:
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


def temporary_eps_brake_recovery(cp, state):
  return bool(qualified(cp, marked_only=True) and state.canValid and not state.canTimeout and
              not state.steerFaultPermanent and not state.accFaulted and not state.doorOpen and
              not state.vehicleSensorsInvalid and not state.steeringDisengage and not state.buttonEvents and
              state.cruiseState.available and not state.cruiseState.enabled and
              state.gearShifter == structs.CarState.GearShifter.drive and
              state.brakePressed and not state.regenBraking and not state.gasPressed)
