"""Volvo V60 (EUCD) AOL: lateral follows the car's LKA on/off when the LKAS button is mapped, else the ACC main."""

from opendbc.car import structs
from opendbc.car.volvo.values import CAR, VolvoSafetyFlags
from openpilot.starpilot.aol.intent import AolCardIntent, AOL_TOGGLE, ButtonType
from openpilot.starpilot.aol.policy import AolVehiclePolicy

VOLVO_EUCD = VolvoSafetyFlags.EUCD.value
_DRIVE = (structs.CarState.GearShifter.drive,)


def qualified(cp, *, marked_only=False):
  try:
    if (
      cp.brand != 'volvo'
      or cp.carFingerprint != CAR.VOLVO_V60
      or cp.flags != 0
      or cp.passive
      or cp.notCar
      or cp.dashcamOnly
      or not cp.pcmCruise
      or cp.steerControlType != structs.CarParams.SteerControlType.angle
      or len(cp.safetyConfigs) != 1
      or cp.alternativeExperience not in ((32,) if marked_only else (0, 32))
    ):
      return False
    config = cp.safetyConfigs[0]
    return config.safetyModel == structs.CarParams.SafetyModel.volvo and config.safetyParam == VOLVO_EUCD
  except (AttributeError, TypeError, ValueError, IndexError):
    return False


def policy_for(cp):
  admitted = qualified(cp)
  return AolVehiclePolicy(
    intent_supported=admitted,
    settings_supported=admitted,
    runtime_supported=admitted,
    normal_runtime_supported=admitted,
    ordinary_axis_ack_required=qualified(cp, marked_only=True),
    explicit_latch=admitted,
    alternative_experience_addition=32 if admitted else 0,
  )


def native_profile_supported(model, param):
  return model == int(structs.CarParams.SafetyModel.volvo) and param == VOLVO_EUCD


def native_accepts_cp(cp, model, param):
  return qualified(cp, marked_only=True) and native_profile_supported(model, param) and param == cp.safetyConfigs[0].safetyParam


def ordinary_axis_request_allowed(cp, state):
  return qualified(cp, marked_only=True) and state.canValid and not state.canTimeout and not state.steerFaultPermanent


def temporary_restriction(cp, state):
  return bool(
    state is not None
    and qualified(cp, marked_only=True)
    and state.canValid
    and not state.canTimeout
    and not state.steerFaultPermanent
    and (state.gearShifter not in _DRIVE or state.steerFaultTemporary or state.vehicleSensorsInvalid or state.invalidLkasSetting)
  )


def native_latch_rejected(cp, native):
  return qualified(cp, marked_only=True) and native.requestedLateral and not native.lateralAllowed


def retain_on_native_denial(cp, native, state):
  return temporary_restriction(cp, state)


def native_observation(native, *, latched, panda_ready, restricted, now_ns, pending_since_ns):
  lost = not panda_ready or native is None
  if not latched or lost or restricted:
    return 0, lost, False
  if native.requestedLateral and native.lateralAllowed:
    return 0, False, False
  pending_since_ns = pending_since_ns or now_ns
  return pending_since_ns, False, now_ns - pending_since_ns >= 200_000_000


class VolvoEucdCardIntent(AolCardIntent):
  observe_stock_engagement = True

  def __init__(self, settings):
    self.lka_on = False
    self.cruise_enabled = False
    super().__init__(settings, explicit_latch=True)

  def observe_vehicle_state(self, car_state):
    self.lka_on = bool(getattr(car_state, 'eucd_lka_on', False))

  def update(self, CS, **kwargs):
    saved = self.settings
    lka_mode = saved.lkas_action == AOL_TOGGLE
    healthy = bool(
      CS.canValid
      and not CS.canTimeout
      and not CS.steerFaultPermanent
      and not CS.accFaulted
      and kwargs.get('fault_active') is not True
      and not kwargs.get('native_rejection_ns', 0)
    )
    state = structs.CarState(**CS.to_dict())
    events = []
    for event in CS.buttonEvents:
      if event.type != ButtonType.lkas:
        events.append(structs.CarState.ButtonEvent(**event.to_dict()))
      elif lka_mode and event.pressed:
        # Each press flips the LKA, already read: turning it off while engaged pauses steering, turning it on resumes
        if CS.cruiseState.enabled or self.pause_lateral:
          self.pause_lateral = not self.lka_on
          if self.pause_lateral:
            self._withdraw_full_engagement()
      elif not lka_mode and event.pressed and healthy:
        self._perform(saved.lkas_action)
    state.buttonEvents = events
    # A pause by the LKA ends with the next ACC engage
    if lka_mode and CS.cruiseState.enabled and not self.cruise_enabled:
      self.pause_lateral = False
    self.cruise_enabled = bool(CS.cruiseState.enabled)
    super().update(state.as_reader(), **kwargs)

    # Latch follows the car's LKA, or an ACC engage until the LKA pauses it (route 00000007, 296 s)
    engaged_steering = lka_mode and CS.cruiseState.enabled and not self.pause_lateral
    desired = (self.lka_on or engaged_steering) if lka_mode else bool(CS.cruiseState.available)
    if not desired:
      if self.allowed_latch:
        self._record_latch_clear('vehicle_state_off')
      self.allowed_latch = False
    elif (healthy and saved.enabled and not self.pause_lateral and not self._fault_inhibit and
          not self._calibration_inhibit and not self._calibration_rearm_required):
      self.allowed_latch = True


def create_intent(cp, settings):
  return VolvoEucdCardIntent(settings)
