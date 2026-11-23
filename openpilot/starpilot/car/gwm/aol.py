"""GWM Haval H6 GT: the physical stalk owns engagement, so lateral follows the latch.

This port does not implement the intent/axis model, so intent_supported,
explicit_latch and ordinary_axis_ack_required stay off. Lateral authority comes
from the stalk latch (acc_main_on) held across a brake press, which the panda
side now enforces through the AolSafetyPolicy in opendbc/safety/modes/gwm_aol.h.
Host and panda have to be enabled together; see policy_for below.
"""
from opendbc.car import structs
from opendbc.car.gwm.values import CAR, GwmFlags
from openpilot.starpilot.aol.policy import AolVehiclePolicy


def _admitted(cp) -> bool:
  """One platform, one safety word. Anything else gets no authority."""
  try:
    return bool(cp.carFingerprint == CAR.GWM_HAVAL_H6 and
                len(cp.safetyConfigs) == 1 and
                int(cp.safetyConfigs[0].safetyParam) == int(GwmFlags.LONG_CONTROL) and
                cp.openpilotLongitudinalControl)
  except (AttributeError, TypeError, ValueError, IndexError):
    return False


def policy_for(cp) -> AolVehiclePolicy:
  admitted = _admitted(cp)
  # runtime_supported sets aol_replay, which makes controlsd list
  # 'aolAxisState' and 'aolSafetyWire' among its *required* services. pandad
  # only publishes a valid aolSafetyWire once the panda reports an axis status,
  # which needs an AolSafetyPolicy registered for this profile. That policy now
  # exists: opendbc/safety/modes/gwm_aol.h, registered from gwm_init.
  #
  # Turning this on without the panda side is what produced the measured
  # failure on route 00000005--3fb9b5466e: the wire never went valid, controlsd
  # stopped validating, selfdrived saw controlsState invalid and raised
  # commIssue as noEntry + softDisable - continuously from 13s to 195s, with
  # selfdriveState stuck at disabled the whole drive. Keep the two sides in
  # lockstep.
  #
  # alternative_experience_addition=32 is load-bearing: gwm_init only registers
  # the policy when alternative_experience == 32, so dropping it silently
  # returns the car to the no-policy fallback.
  #
  # physical_stalk_owner is the shortest path through feature_runtime.enabled()
  # that needs no per-axis opt-in, and it is literally true here.
  # distance_personality makes card.py build the follow-distance tracker that
  # consumes the gap buttonEvents.
  # intent_supported is load-bearing too, and not for the reason the name
  # suggests. card.py computes
  #   aol_qualified = settings is not None and (... or physical_stalk_owner or ...)
  #                   and intent_supported and not CP.passive
  # and only inside `if self.aol_qualified:` does it run
  #   CP.alternativeExperience |= policy.alternative_experience_addition
  # So with intent_supported off the 32 never reaches the panda, gwm_init sees
  # alternative_experience == 0, the policy never registers, pandad reports no
  # axis status, aolSafetyWire stays invalid and commIssue blocks engagement.
  # Measured exactly that on route 00000007--12a69c8247: panda in safety_mode 40
  # with param 1 but alternative_experience 0, and commIssue at 12.1s.
  #
  # It does not drag in a bespoke intent: aol/vehicle.create_intent falls back to
  # the generic AolCardIntent when the port exposes no create_intent, which is
  # the case here.
  return AolVehiclePolicy(intent_supported=admitted,
                          runtime_supported=admitted,
                          normal_runtime_supported=admitted,
                          physical_stalk_owner=admitted,
                          distance_personality=admitted,
                          lkas_button_supported=False,
                          alternative_experience_addition=32 if admitted else 0)


def native_profile_supported(model: int, param: int) -> bool:
  return model == int(structs.CarParams.SafetyModel.gwm) and param == int(GwmFlags.LONG_CONTROL)


def native_accepts_cp(cp, model: int, param: int) -> bool:
  return _admitted(cp) and native_profile_supported(model, param)
