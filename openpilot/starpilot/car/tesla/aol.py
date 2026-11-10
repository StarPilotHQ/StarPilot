from opendbc.car import structs
from opendbc.car.tesla.preap.aol import qualified, temporary_restriction as preap_temporary_restriction
from openpilot.starpilot.aol.policy import AolVehiclePolicy
from openpilot.starpilot.car.tesla.stock_intent import TeslaPreapCardIntent
from opendbc.car.tesla.screen_button import modern_aol_supported, modern_aol_qualified, AOL_WORDS
from openpilot.starpilot.car.tesla.screen_intent import TeslaScreenCardIntent


def policy_for(cp):
  if modern_aol_supported(cp):
    admitted = modern_aol_qualified(cp)
    return AolVehiclePolicy(intent_supported=admitted, settings_supported=True,
                            runtime_supported=admitted, normal_runtime_supported=admitted,
                            ordinary_axis_ack_required=modern_aol_qualified(cp, marked_only=True),
                            full_axis_runtime_required=admitted,
                            cruise_main_required=not modern_aol_qualified(cp, marked_only=True),
                            explicit_latch=admitted, alternative_experience_addition=32 if admitted else 0)
  admitted = qualified(cp)
  return AolVehiclePolicy(intent_supported=admitted, settings_supported=admitted,
                          runtime_supported=admitted, normal_runtime_supported=admitted,
                          ordinary_axis_ack_required=qualified(cp, marked_only=True),
                          explicit_latch=admitted, physical_stalk_owner=admitted, fixed_cruise_buttons=admitted,
                          full_axis_runtime_required=admitted and cp.openpilotLongitudinalControl,
                          alternative_experience_addition=32 if admitted else 0)


def native_profile_supported(model, param):
  return (model == 39 and param in (0, 1, 2, 3, 6, 7)) or (model == 10 and param in AOL_WORDS)


def native_accepts_cp(cp, model, param):
  return bool((qualified(cp, marked_only=True) or modern_aol_qualified(cp, marked_only=True)) and
              native_profile_supported(model, param) and len(cp.safetyConfigs) == 1 and
              int(cp.safetyConfigs[0].safetyModel.raw) == model and int(cp.safetyConfigs[0].safetyParam) == param)


def native_latch_rejected(cp, native):
  return (qualified(cp, marked_only=True) or modern_aol_qualified(cp, marked_only=True)) and native.requestedLateral and not native.lateralAllowed


def ordinary_axis_request_allowed(cp, state):
  return bool((qualified(cp, marked_only=True) or modern_aol_qualified(cp, marked_only=True)) and
              state.canValid and not state.canTimeout and not state.steerFaultPermanent)


def retain_on_native_denial(cp, native, state):
  return preap_temporary_restriction(cp, state) or bool(
    modern_aol_qualified(cp, marked_only=True) and state is not None and state.canValid and not state.canTimeout and
    not state.steerFaultPermanent and not state.steeringDisengage and not state.doorOpen and not state.accFaulted and
    (state.gearShifter in (structs.CarState.GearShifter.reverse, structs.CarState.GearShifter.neutral) or
     state.gearShifter == structs.CarState.GearShifter.drive and state.steerFaultTemporary))


def create_intent(cp, settings):
  return TeslaScreenCardIntent(cp, settings) if modern_aol_qualified(cp) else TeslaPreapCardIntent(cp, settings)
