"""Vehicle-owned longitudinal behavior at the shared control boundary."""
from dataclasses import dataclass

from openpilot.common.realtime import DT_CTRL
from opendbc.car.structs import car
from openpilot.starpilot.longitudinal.toyota_output_policy import COROLLA, ToyotaOutputPolicy, development_enabled, production_enabled
from openpilot.starpilot.longitudinal.ioniq6_start import Ioniq6StartPolicy, StartEvidence, eligible as ioniq6_start_eligible
from openpilot.starpilot.longitudinal.vehicle_policy import policy_for as vehicle_policy_for, stopping_decel_rate, stopping_policy_for
from opendbc.car.gm.longitudinal import GMPedalStartPolicy, volt_sng_resume_policy_for
from opendbc.car.gm.bolt_mode import policy_for as bolt_mode_policy_for

LongCtrlState = car.CarControl.Actuators.LongControlState


@dataclass(frozen=True)
class LongitudinalContext:
  leads: object = None
  start_evidence: object = None
  gm_start_evidence: object = None
  has_lead: bool | None = None
  vehicle_stop_evidence: object = None
  experimental_mode: bool | None = None
  traffic_mode: bool | None = None
  custom_acceleration: bool | None = None
  profile_max_accel: float | None = None
  faster_takeoffs: bool = False


class LongitudinalExtension:
  def __init__(self, CP, startup_preferences=None):
    self.resume_policy = volt_sng_resume_policy_for(CP)
    self.bolt_mode = bolt_mode_policy_for(CP)
    self.vehicle_policy = vehicle_policy_for(CP, startup_preferences)
    self.stopping_policy = stopping_policy_for(CP, DT_CTRL)
    self.stopping_decel_rate = (self.stopping_policy.stopping_decel_rate if self.stopping_policy is not None
                                else stopping_decel_rate(CP, self.vehicle_policy))
    self.gm_start = GMPedalStartPolicy() if self.vehicle_policy is not None and self.vehicle_policy.friction_variant else None
    self.vehicle_stop = self.vehicle_policy.stop_policy() if hasattr(self.vehicle_policy, "stop_policy") else None
    toyota_production = production_enabled(CP)
    self.toyota_stopped_lead = toyota_production and str(CP.carFingerprint) == COROLLA
    self.toyota_output = ToyotaOutputPolicy(CP) if toyota_production or development_enabled(CP) else None
    self.ioniq6_start = Ioniq6StartPolicy() if ioniq6_start_eligible(CP) else None
    self.vehicle_target = getattr(self.vehicle_policy, "target", None)
    self.kp = self.vehicle_policy.kp if self.vehicle_policy is not None else 0.0
    self.ki = getattr(self.vehicle_policy, "ki", None)
    from opendbc.car.toyota.sng import eligible as toyota_sng_eligible
    self.ignore_cruise_standstill = bool(getattr(self.vehicle_policy, "ignore_cruise_standstill", False) or
      getattr(startup_preferences, "toyota_sng", False) and toyota_sng_eligible(CP))

  def qualify_resume(self, default, *, preferences, inputs):
    if self.resume_policy is None or not preferences.volt_sng:
      return default
    return self.resume_policy(default, True, inputs.resume_sources_current())

  def reset(self, *, reset_start=True):
    if self.vehicle_policy is not None:
      self.vehicle_policy.reset()
    if self.toyota_output is not None:
      self.toyota_output.reset()
    if reset_start and self.stopping_policy is not None:
      self.stopping_policy.reset()
    if reset_start and self.ioniq6_start is not None:
      self.ioniq6_start.reset()
    if reset_start and self.gm_start is not None:
      self.gm_start.reset()
    if reset_start and self.vehicle_stop is not None:
      self.vehicle_stop.reset()

  def transition(self, native_state, previous_state, active, CS, a_target, should_stop, accel_limits, *,
                 context):
    start_evidence = context.start_evidence
    gm_start_evidence = context.gm_start_evidence
    vehicle_stop_evidence = context.vehicle_stop_evidence
    if self.ioniq6_start is None or context.faster_takeoffs:
      state = native_state
    else:
      evidence = start_evidence or StartEvidence(False, False, None, None, None)
      state = self.ioniq6_start.transition(
        native_state, previous_state, active=active, should_stop=should_stop,
        brake_pressed=CS.brakePressed, gas_pressed=CS.gasPressed,
        can_valid=CS.canValid, can_timeout=CS.canTimeout,
        car_fresh=evidence.car_fresh, plan_fresh=evidence.plan_fresh, lead_clear=evidence.lead_clear,
        drive_id=evidence.drive_id, observed_ns=evidence.observed_ns,
        speed_mps=CS.vEgo, target_mps2=a_target, accel_limits=accel_limits)
    if self.gm_start is not None:
      state = self.gm_start.transition(native_state, previous_state, active, CS, a_target,
                                       should_stop, gm_start_evidence)
    if self.vehicle_stop is not None:
      state = self.vehicle_stop.transition(native_state, previous_state, active, CS, a_target,
                                           should_stop, vehicle_stop_evidence)
    if self.stopping_policy is not None:
      state = self.stopping_policy.transition(state, previous_state, active, CS, a_target, should_stop,
                                              has_lead=context.has_lead)
    if (self.toyota_stopped_lead and active and previous_state == LongCtrlState.stopping and
        state != LongCtrlState.off and not should_stop and not CS.brakePressed and
        self.toyota_output.hold_stopped_lead(CS.vEgo, context.leads)):
      state = LongCtrlState.stopping
    return state

  def stopping_output(self, output_accel, a_target, should_stop, CS):
    if self.stopping_policy is not None:
      output_accel = self.stopping_policy.stopping_output(output_accel, a_target, should_stop, CS)
    if hasattr(self.vehicle_policy, "stopping_output"):
      return self.vehicle_policy.stopping_output(output_accel, a_target, should_stop, CS)
    return output_accel

  def starting_output(self, pid, a_target, accel_limits, context):
    pid.reset()
    if hasattr(self.stopping_policy, "starting_output"):
      return self.stopping_policy.starting_output(a_target, accel_limits, context)
    if self.ioniq6_start is not None:
      if context.faster_takeoffs:
        return max(0.0, min(a_target, accel_limits[1]))
      return self.ioniq6_start.starting_output(a_target, accel_limits)
    if hasattr(self.vehicle_stop, "starting_output"):
      return self.vehicle_stop.starting_output(a_target, accel_limits, context)
    self.vehicle_policy.reset()
    return self.gm_start.output(a_target, context.gm_start_evidence)

  @property
  def starting(self):
    return (self.ioniq6_start is not None or self.gm_start is not None or
            hasattr(self.stopping_policy, "starting_output") or hasattr(self.vehicle_stop, "starting_output"))

  def target(self, a_target, CS, should_stop, last_output, context):
    if self.toyota_output is not None:
      shaped = self.toyota_output.target(a_target, CS.vEgo, should_stop, last_output, leads=context.leads)
      if shaped is not None:
        return shaped
    return self.vehicle_target(a_target, CS.vEgo, should_stop) if self.vehicle_target is not None else a_target

  def prepare_pid(self, pid, a_target, error, CS, last_output, accel_limits, should_stop, context):
    if self.bolt_mode is not None:
      self.bolt_mode.update(context.experimental_mode, DT_CTRL)
    feedforward = (self.vehicle_policy.feedforward(a_target, CS.vEgo, last_output)
                   if self.vehicle_policy is not None else a_target)
    freeze_integrator = (self.vehicle_policy.prepare_pid(pid, a_target, error, CS.vEgo, last_output, accel_limits,
                                                        should_stop=should_stop, has_lead=context.has_lead)
                         if self.vehicle_policy is not None else False)
    if self.stopping_policy is not None:
      feedforward, stop_freeze = self.stopping_policy.prepare_pid(pid, a_target, error, CS, context)
      freeze_integrator |= stop_freeze
    freeze_integrator |= self.bolt_mode is not None and self.bolt_mode.leaving_experimental
    return feedforward, freeze_integrator

  def shape_output(self, output_accel, a_target, error, CS, last_output, previous_state, should_stop, context):
    if self.vehicle_policy is not None:
      output_accel = self.vehicle_policy.shape_output(float(output_accel), a_target, error, CS.vEgo)
    if self.gm_start is not None:
      output_accel = self.gm_start.handoff_output(output_accel, last_output, a_target, CS.vEgo,
                                                previous_state == LongCtrlState.starting, should_stop, context.gm_start_evidence)
    if self.stopping_policy is not None:
      output_accel = self.stopping_policy.shape_output(float(output_accel), a_target, error, CS, last_output)
    if self.bolt_mode is not None:
      output_accel = self.bolt_mode.shape(float(output_accel), last_output)
    return output_accel


def create_extension(cp, startup_preferences=None):
  extension = LongitudinalExtension(cp, startup_preferences)
  if (extension.vehicle_policy is None and extension.toyota_output is None and extension.ioniq6_start is None and
      extension.bolt_mode is None and extension.stopping_policy is None and extension.stopping_decel_rate == 1.0 and
      not extension.ignore_cruise_standstill):
    return None
  return extension
