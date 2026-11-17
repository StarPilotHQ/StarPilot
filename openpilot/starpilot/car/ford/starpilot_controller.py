"""
StarPilot angle-primary Ford candidate, adapted from BluePilot v0.2.1.

Source: BluePilot 2b9dc6cc784faac47a1885d0bda0561f7b76f74d, core/lateral_angle_ext.py.
Copyright (c) 2020, Comma.ai, Inc.; (c) 2024-2026, BluePilot contributors;
(c) 2026, firestar5683 and StarPilot contributors. MIT terms and contributor
credits remain in THIRD_PARTY_NOTICES.md and the pinned BluePilot LICENSE.
StarPilot owns this adaptation; BluePilot does not maintain it.

The planner owns desired curvature and optional lane centering. StarPilot smooths
small corrections without averaging that demand with a second model trajectory.
Ford path angle remains kappa_cmd * speed * the BluePilot platform gain, not a
steering-wheel angle. Native measurement envelopes, manual-turn handling, reactive
recovery, saturation, rate limits and zero curvature/offset wire fields are retained.
No additional feedback loop, integral, torque field or wire authority is introduced.
"""
import math

from numpy import clip, interp

from opendbc.car import DT_CTRL
from opendbc.car.lateral import apply_std_steer_angle_limits
from opendbc.car.ford.values import CarControllerParams
from opendbc.bluepilot_lateral.core.lateral_curv_ext import LateralResult
from opendbc.bluepilot_lateral.core.values_ext import (
  ANGLE_GAIN_CAN, ANGLE_GAIN_CANFD_BOF, ANGLE_GAIN_CANFD_BOF_CARS, ANGLE_GAIN_CANFD_SUV, ANGLE_GAIN_CANFD_SUV_CARS,
  BP_ANGLE_LIMITS, LANE_CHANGE_FACTOR_BP, LANE_CHANGE_FACTOR_LOW,
)
from opendbc.bluepilot_lateral.params.manifest import default

# The host negates path angle before packing the DBC range [-0.5, 0.5235] rad.
# Keep saturation and clamping in the internal convention to prevent unsigned wrap.
FORD_PATH_ANGLE_MIN = -0.5235
FORD_PATH_ANGLE_MAX = 0.5


# Rate cap on path_angle magnitude DECREASE during PSCM LimitReached (rad/call = 0.40 rad/s).
# Both model and planner naturally drop path_angle ~0.36 rad/s at a sharp 90° apex, while the PSCM is
# physically pinned and cannot execute the rapidly falling desired angle. The resulting actual-vs-desired
# gap (up to 47° observed) causes a snap correction the moment the PSCM is released. This cap limits
# the desired-angle drop rate to what the PSCM can reasonably track, at the cost of holding the car
# slightly more in the curve during saturation.
# BluePilot: this strategy runs once per STEER_STEP (CarControllerParams.STEER_STEP=5), i.e. once
# every 5th 100Hz control tick = 20Hz, not every tick. The original 0.004 rad/call value (and its
# "50Hz" comment, corrected above) was authored 2026-05-11 on bp-sid-simple, which had already
# switched STEER_STEP 5->1 (true 100Hz) on 2026-04-22 -- so it was tuned at 100Hz real cadence
# even though its own comment mistakenly said 50Hz. Scaled x5 here to restore the same real-world
# 0.40 rad/s (23 deg/s) unwind rate on this branch's actual 20Hz cadence.
_PSCM_SAT_UNWIND_RATE = 0.02        # rad/call (0.02 * 20Hz = 0.40 rad/s)

# Reactive recovery: a sustained demand/measurement gap can persist while the deviation
# clip binds. A bounded mode-0 pulse releases control, then the normal soft rate limit
# ramps demand back in. Ordinary driver-pressure release is not a recovery trigger.
_STEER_DT = CarControllerParams.STEER_STEP * DT_CTRL  # 20 Hz lateral tick (matches human_turn.py)
_STALL_GAP_MIN = 2.0 * CarControllerParams.CURVATURE_ERROR  # desired must lead measured by 2x the clip tolerance
_STALL_HOLD_S = 0.5          # accumulated clip-binding time before a pulse fires
_STALL_BLIP_FRAMES = 6       # mode-0 pulse length (6 frames @ 20 Hz = 300 ms)
_STALL_COOLDOWN_S = 2.0      # re-arm delay after a pulse (release ramp + PSCM response time)
_STALL_MAX_BLIPS = 3         # give up on a stuck episode; devLim telemetry keeps recording the stall
_STALL_PROGRESS_EPS = 0.00002  # one native CAN curvature unit
# The pulse releases steering for 300 ms; never fire it in a curve.
_BLIP_MAX_PATH_ANGLE = 0.10  # rad


# the settings angle mode reads (params/manifest.py): (attribute, key)
ANGLE_SETTINGS = (
  ("low_speed_curv_factor", "FordLowSpeedFactor_ang"),
  ("high_speed_curv_factor", "FordHighSpeedFactor_ang"),
  ("user_dampening_factor", "FordHighSpeedDampening_ang"),
  ("lane_change_factor_high_ang", "lane_change_factor_high_ang"),
  # Retained interface defaults; secondary lane-position control is disabled.
  ("enable_lane_positioning_ang", "enable_lane_positioning_ang"),
  ("custom_path_offset_ang", "custom_path_offset_ang"),
  ("lane_centering_strength_ang", "lane_centering_strength_ang"),
)


class CurvatureConditioner:
  """Symmetric first-order smoothing with bounded request error and no integral."""

  def __init__(self):
    self.curvature = None

  def reset(self):
    self.curvature = None

  def update(self, target, speed, bypass=False):
    previous = self.curvature
    speed_squared = max(speed, 1.0) ** 2
    magnitude = max(abs(target), abs(previous or 0.0)) * speed_squared
    reversing = previous is not None and previous * target <= 0.0 and magnitude >= 0.10
    if previous is None or bypass or reversing:
      self.curvature = target
    else:
      # A constant coefficient avoids treating each downward ripple as a curve exit.
      # At 20 Hz, alpha=0.5 gives 50 ms low-frequency group delay before bounding.
      filtered = previous + 0.5 * (target - previous)
      max_lag = 0.04 / speed_squared  # curvature * speed² request-error budget, not measured acceleration
      self.curvature = float(clip(filtered, target - max_lag, target + max_lag))
    return self.curvature


class StarPilotFordController:
  def __init__(self, CP, inputs, shared):
    self.CP = CP
    self.conditioner = CurvatureConditioner()
    self.native_curvature_bounds: tuple[float, float] | None = None
    # StarPilot uses the platform defaults, not hidden BluePilot user adjustment knobs.
    for attr, key in ANGLE_SETTINGS:
      setattr(self, attr, default(key))
    # BluePilotLateral's LateralInputs (read) and SharedLateralState (read and written), common to both strategies
    self.inputs = inputs
    self.shared = shared
    # Telemetry: final path_angle (rad) after limits (for the BluePilot UI's bp_card_publisher)
    self.bp_path_angle_final = 0.0
    # High-speed gain factors: set per-platform via carFingerprint in update_angle_params.
    self.path_angle_gain_lowC_highV = 1.0   # dampening at high speed, low curvature
    self.path_angle_gain_highC_highV = 1.0  # gain at high speed, high curvature
    self.bp_path_angle_gain_lowC_highV = 1.0
    self.bp_path_angle_gain_highC_highV = 1.0
    # Telemetry copies of the user "feel" multipliers (ANGLE_SETTINGS)
    self.bp_low_speed_curv_factor = 1.0
    self.bp_high_speed_curv_factor = 1.0
    # No additional forecast lookup; the planner owns prediction and centering.
    self.bp_curvature_lookup_time = 0.0
    # BluePilot: error-clipped kappa path_angle was derived from -- carcontroller_ext.py reads this as
    # shadow_curvature for ford.h's angle-mode deviation check. Actively consumed, not telemetry.
    self.bp_kappa_cmd = 0.0
    # BluePilot: rate-limit diagnostics (the BluePilot UI's controllerStateBP)
    self.bp_angle_rate_limited = False      # path_angle soft-ROC clip actually bit this frame
    self.bp_curvature_rate_limited = False  # equivalent curvature would be rate-limited by curv-mode logic (sim)
    self.sim_curvature_last = 0.0           # shadow curvature-mode last for the curvatureRateLimited sim
    # Manual turns release lateral instead of retaining stale path-angle demand.
    # One shared detector serves both modes; release ramps from zero.
    self.angle_human_turn_active = False  # read by BluePilotLateral.step_lateral to force mode 0
    # Post-override stall blip state (see module constants). angle_stall_blip_active is read by
    # BluePilotLateral.step_lateral to force mode 0, exactly like angle_human_turn_active.
    self.stall_blip_hold_s = 0.0      # accumulated deviation-clip-binding time toward a pulse
    self.stall_blip_frames_left = 0   # remaining pulse frames; > 0 -> mode 0 on the wire
    self.stall_blip_cooldown_s = 0.0  # re-arm delay after a pulse
    self.stall_blip_count = 0         # pulses fired this stall episode
    self.stall_measured_last: float | None = None
    self.angle_stall_blip_active = False

  def update_angle_params(self, params):
    """Keep platform gains; the separate BluePilot options do not retune StarPilot."""
    fp = getattr(self.CP, 'carFingerprint', '')
    if fp in ANGLE_GAIN_CANFD_BOF_CARS:
      low, high = ANGLE_GAIN_CANFD_BOF
    elif fp in ANGLE_GAIN_CANFD_SUV_CARS:
      low, high = ANGLE_GAIN_CANFD_SUV
    else:
      low, high = ANGLE_GAIN_CAN
    self.path_angle_gain_lowC_highV = low
    self.path_angle_gain_highC_highV = high
    for attr, key in ANGLE_SETTINGS:
      setattr(self, attr, default(key))
    self.enable_lane_positioning_ang = False

  def inputs_valid(self, CS, actuators):
    state = CS.out
    return (state.canValid and not state.canTimeout and not state.steerFaultTemporary and
            not state.steerFaultPermanent and not state.vehicleSensorsInvalid and
            self.inputs.model is not None and self.inputs.lp is not None and state.vEgoRaw >= 0.0 and
            all(map(math.isfinite, (state.vEgoRaw, self.inputs.get_current_curvature(CS), actuators.curvature,
                                   state.steeringAngleDeg, state.steeringTorque))))

  def update_angle_strategy(self, CC, CS, actuators):
    """
    Planner demand and bounded symmetric smoothing → Ford path_angle.
    Path offset, wire curvature and curvature rate stay zero; native limits are unchanged.
    κ is not passed through Ford c2 rate / DBC limits (those target the curvature actuator).
    """
    v_ego = float(CS.out.vEgoRaw)

    curvature_rate = 0.0
    path_offset = 0.0
    path_angle = 0.0
    ramp_type = 0
    lateral_uncertainty = 0.0
    precision = 1

    measured = self.inputs.get_current_curvature(CS)
    active = CC.latActive and self.inputs_valid(CS, actuators)
    if not active:
      self.conditioner.reset()
      self.shared.path_angle_last = 0.0
      self.bp_path_angle_final = 0.0
      self.bp_angle_rate_limited = False
      self.bp_curvature_rate_limited = False
      self.shared.bp_curvature_deviation_limited = False
      self.sim_curvature_last = 0.0
      # Publish the shadow curvature from the measured curvature while inactive. LKA keeps
      # carrying angle_mode_engaged whenever angle mode is configured (independent of
      # latActive), and ford.h latches the shadow from every LKA frame -- so the latched
      # value must track reality here, not sit at a stale zero. Otherwise the first enabled
      # LMC frame after (re-)engage races LKA's 33Hz latch against LMC's 20Hz enable bit and
      # ford.h's deviation check compares a zero shadow against real measured curvature.
      # (ford.h skips the check while steer_control_enabled is 0, so the value is free to
      # follow the measurement during the inactive period itself.)
      self.bp_kappa_cmd = measured if math.isfinite(measured) else 0.0
      self.shared.human_turn_detector.reset()
      self.angle_human_turn_active = False
      self.stall_blip_hold_s = 0.0
      self.stall_blip_frames_left = 0
      self.stall_blip_cooldown_s = 0.0
      self.stall_blip_count = 0
      self.stall_measured_last = None
      self.angle_stall_blip_active = False
      self.shared.precision_type = 1
      return LateralResult(
        apply_curvature=0.0,
        curvature_rate=0.0,
        path_offset=0.0,
        path_angle=0.0,
        ramp_type=0,
        precision_type=1,
        lateralUncertainty=0.0,
      )

    # Human-turn override: sustained driver press + large wheel angle → force lateral inactive
    # (BluePilotLateral.step_lateral drops mode to 0; all signals are zero on the wire) so path_angle can't wind
    # into a stale command while the driver turns. Always on in angle mode (no param gate) -- the
    # curv-suffixed human-turn toggle belongs to curvature mode's reset strategy, and the Mach-E
    # PSCM re-engage stall this prevents is not something a user should be able to opt out of.
    # On release, no jump seed: path_angle_last is 0, so the normal flow below ramps the command
    # back in through the soft ROC -- generous at human-turn speeds.
    self.angle_human_turn_active = self.shared.human_turn_detector.update(
      True, CS.out.steeringPressed, CS.out.steeringAngleDeg)
    if self.angle_human_turn_active:
      self.conditioner.reset()
      self.shared.path_angle_last = 0.0
      self.bp_path_angle_final = 0.0
      self.bp_angle_rate_limited = False
      self.bp_curvature_rate_limited = False
      self.shared.bp_curvature_deviation_limited = False
      self.sim_curvature_last = 0.0
      # Truthful shadow during the override (mirrors the inactive path -- see the comment
      # there): the driver is steering, so the honest command is the car's actual curvature,
      # and the panda-latched shadow stays current for the re-engage frame.
      self.bp_kappa_cmd = self.inputs.get_current_curvature(CS)
      # A human turn ends any prior recovery episode; its existing mode 0 supplies the reset.
      self.stall_blip_hold_s = 0.0
      self.stall_blip_frames_left = 0
      self.stall_blip_cooldown_s = 0.0
      self.stall_blip_count = 0
      self.stall_measured_last = None
      self.angle_stall_blip_active = False
      self.shared.precision_type = 1
      return LateralResult(
        apply_curvature=0.0,
        curvature_rate=0.0,
        path_offset=0.0,
        path_angle=0.0,
        ramp_type=0,
        precision_type=1,
        lateralUncertainty=0.0,
      )

    progress = 0.0 if self.stall_measured_last is None else measured - self.stall_measured_last
    steering_responding = (abs(progress) > _STALL_PROGRESS_EPS and
                          progress * (float(actuators.curvature) - measured) > 0.0)
    self.stall_measured_last = measured
    if steering_responding:
      # A clipped tracking correction is not a stall while the car is responding.
      self.stall_blip_hold_s = 0.0
      if self.stall_blip_frames_left > 0 and not self.angle_stall_blip_active:
        self.stall_blip_frames_left = 0
        self.stall_blip_count = max(0, self.stall_blip_count - 1)

    # Stall-blip pulse in progress: hold lateral inactive (mode 0, all-zero signals -- the same
    # wire pattern as the human-turn override, no ford.h involvement) for _STALL_BLIP_FRAMES so the
    # PSCM drops its post-override attenuation, then release; path_angle ramps back in from zero
    # through the soft ROC exactly like a human-turn release. Detection lives at the end of the
    # normal flow below.
    if self.stall_blip_frames_left > 0:
      self.conditioner.reset()
      self.stall_blip_frames_left -= 1
      self.angle_stall_blip_active = True
      self.shared.path_angle_last = 0.0
      self.bp_path_angle_final = 0.0
      self.bp_angle_rate_limited = False
      self.bp_curvature_rate_limited = False
      self.shared.bp_curvature_deviation_limited = False
      self.sim_curvature_last = 0.0
      # Truthful shadow during the blip (see the inactive-path comment).
      self.bp_kappa_cmd = self.inputs.get_current_curvature(CS)
      self.shared.precision_type = 1
      if self.stall_blip_frames_left <= 0:
        self.stall_blip_cooldown_s = _STALL_COOLDOWN_S
      return LateralResult(
        apply_curvature=0.0,
        curvature_rate=0.0,
        path_offset=0.0,
        path_angle=0.0,
        ramp_type=0,
        precision_type=1,
        lateralUncertainty=0.0,
      )
    self.angle_stall_blip_active = False

    self.shared.precision_type = 1
    precision = 1
    desired_curvature = float(actuators.curvature)

    # Keep the complete planner command, including any shared centering correction.
    # A separate absolute model-curvature blend would attenuate planner-only changes.
    requested_curvature = desired_curvature
    _pscm_lim = getattr(CS, 'lat_ctl_lim_stat', 0)
    _dbc_sat = (self.shared.path_angle_last >= FORD_PATH_ANGLE_MAX * 0.90 or
                self.shared.path_angle_last <= FORD_PATH_ANGLE_MIN * 0.90)
    _in_hard_sat = _pscm_lim >= 2 or _dbc_sat

    if self.inputs.model is not None:
      self.lane_change = self.inputs.model.meta.laneChangeState in (1, 2, 3)
    else:
      self.lane_change = False

    lane_change_factor = interp(
      v_ego, LANE_CHANGE_FACTOR_BP, [LANE_CHANGE_FACTOR_LOW, self.lane_change_factor_high_ang]
    )
    if self.lane_change and self.inputs.model is not None:
      if self.inputs.model.meta.laneChangeDirection == 1 and requested_curvature < 0:
        requested_curvature *= lane_change_factor
        precision = 0
      elif self.inputs.model.meta.laneChangeDirection == 2 and requested_curvature > 0:
        requested_curvature *= lane_change_factor
        precision = 0
    self.shared.precision_type = precision

    # Shared centering is applied upstream; there is no second lane-position controller.
    kappa_cmd = float(requested_curvature)

    kappa_cmd = self.conditioner.update(
      kappa_cmd, v_ego, bypass=bool(CS.out.steeringPressed or self.lane_change or _pscm_lim >= 1 or _in_hard_sat))

    # BluePilot: clip kappa_cmd to current_curvature (measured, from yaw rate) +- CURVATURE_ERROR,
    # mirroring lateral_curv_ext.py's apply_ford_curvature_limits_ext exactly (same formula, same
    # v_ego > 9 gate, same CarControllerParams.CURVATURE_ERROR tolerance). Without this, kappa_cmd
    # (and therefore path_angle, and the shadow_curvature sent to ford.h) can legitimately lead the
    # measured curvature by more than ford.h's angle-error tolerance during normal curve entry/exit
    # -- the shadow-curvature deviation check (ford_shadow_curvature_error_check) would then block
    # routinely, not just on genuine pothole/override divergence. Curvature mode has always clipped
    # here; this brings angle mode's actual steering intent in line with that proven behavior rather
    # than only clipping the value reported to panda (which would make the check a no-op).
    current_curvature = self.inputs.get_current_curvature(CS)
    self.shared.bp_curvature_deviation_limited = False
    if v_ego > 9:
      _kappa_cmd_pre_error_clip = kappa_cmd
      kappa_cmd = float(clip(kappa_cmd, current_curvature - CarControllerParams.CURVATURE_ERROR,
                            current_curvature + CarControllerParams.CURVATURE_ERROR))
      # BluePilot: did this clip actually constrain kappa_cmd this frame (deviation from measured,
      # not rate-of-change)? The BluePilot UI's deviation flag.
      self.shared.bp_curvature_deviation_limited = bool(abs(kappa_cmd - _kappa_cmd_pre_error_clip) > 1e-9)

    if self.native_curvature_bounds is not None:
      low, high = self.native_curvature_bounds
      constrained = float(clip(kappa_cmd, low, high))
      self.shared.bp_curvature_deviation_limited |= abs(constrained - kappa_cmd) > 1e-9
      kappa_cmd = constrained

    # Remember only demand admitted by the measured-curvature envelope, not a hidden backlog.
    self.conditioner.curvature = kappa_cmd
    if CS.out.steeringPressed:
      self.conditioner.reset()

    lateral_uncertainty = 0.0  # no curvature-limit ladder until angle-mode torque display is defined

    # Speed-interpolated gain: at low speed both curves use 1.0; at high speed the params take effect.
    self.low_gain_calc = interp(
      v_ego, [13.5, 26.82], [1.0, (self.path_angle_gain_lowC_highV * self.user_dampening_factor)]
    )
    self.high_gain_calc = interp(v_ego, [13.5, 26.82], [(1.30 * self.low_speed_curv_factor), (self.path_angle_gain_highC_highV * self.high_speed_curv_factor)])

    # As the curve gets bigger, we will need a little boost to the signal to to not understeer
    self.curvature_factor = interp(abs(kappa_cmd), [0.0007, 0.001], [self.low_gain_calc, self.high_gain_calc])

    path_angle_calc = kappa_cmd * v_ego * self.curvature_factor
    path_angle = path_angle_calc

    # PSCM authority limit clamp.
    # On CANFD Fords in angle mode, LatCtlLim_D_Stat does not fire, so _pscm_lim stays 0.
    # _in_hard_sat (computed above) combines _pscm_lim >= 2 with _dbc_sat (path_angle near ±0.5 rad limit).
    # LimitClose (_pscm_lim >= 1 only): block magnitude increases; planner demand provides unwind.
    # Hard saturation (_in_hard_sat): block increases AND rate-limit decreases to _PSCM_SAT_UNWIND_RATE.
    #   Without the decrease cap, model+planner drop path_angle at ~0.36 rad/s at a sharp apex,
    #   driving desired steering 30°+ ahead of actual while the PSCM is pinned, causing a snap when released.
    if _in_hard_sat:
      _last = self.shared.path_angle_last
      _last_mag = abs(_last)
      _curr_mag = abs(path_angle)
      if _curr_mag > _last_mag:  # magnitude growing — block
        path_angle = _last
      elif _last_mag - _curr_mag > _PSCM_SAT_UNWIND_RATE:  # decreasing too fast — rate-limit
        _limited_mag = _last_mag - _PSCM_SAT_UNWIND_RATE
        path_angle = float(_limited_mag if _last >= 0 else -_limited_mag)
    elif _pscm_lim >= 1:  # LimitClose (F150/non-angle-mode only): block increases only
      path_angle = float(clip(path_angle, -abs(self.shared.path_angle_last), abs(self.shared.path_angle_last)))

    path_angle = min(FORD_PATH_ANGLE_MAX, max(FORD_PATH_ANGLE_MIN, path_angle))

    # Soft ROC limit — unconditional, slightly tighter than ford.h, applied before the
    # hardware bypass in ford.h is re-enabled.  Lets us observe whether the limit would
    # suppress control and tune it, while the PSCM still receives the clipped value.
    # BluePilot: this strategy runs once per STEER_STEP (CarControllerParams.STEER_STEP=5), i.e.
    # once every 5th 100Hz control tick = 20Hz, not every tick -- ported "verbatim from bp-sid-simple"
    # (2026-06-13), which runs STEER_STEP=1 (true 100Hz, switched 2026-04-22). The y-values below are
    # scaled x5 from the original [0.011, 0.011, 0.0085, 0.0018] to restore the same real-world rate
    # (63/63/49/10 deg/s at v=9-10/15/25) on this branch's actual 20Hz cadence. See ford.h's
    # FORD_PATH_ANGLE_LIMITS, which must mirror this scaling (x1.02 looser) to stay a true backstop.
    _soft_roc = float(interp(v_ego, [9., 10., 15., 25.], [0.055, 0.055, 0.0425, 0.009]))
    _path_angle_pre_roc = path_angle
    path_angle = float(clip(path_angle,
                            self.shared.path_angle_last - _soft_roc,
                            self.shared.path_angle_last + _soft_roc))
    # BluePilot: did the soft ROC clip actually limit the path_angle we wanted to send this frame?
    self.bp_angle_rate_limited = bool(abs(path_angle - _path_angle_pre_roc) > 1e-9)

    # c0 always zero -- no centering trim in angle mode.
    path_offset = 0.0

    # Telemetry / state
    self.bp_path_angle_gain_lowC_highV = self.path_angle_gain_lowC_highV
    self.bp_path_angle_gain_highC_highV = self.path_angle_gain_highC_highV
    self.bp_low_speed_curv_factor = self.low_speed_curv_factor
    self.bp_high_speed_curv_factor = self.high_speed_curv_factor
    self.shared.path_angle_last = path_angle
    self.bp_path_angle_final = path_angle
    # BluePilot: the error-clipped kappa path_angle was derived from -- carcontroller_ext.py reads this
    # as shadow_curvature for ford.h's angle-mode deviation check (see fordcan_ext.create_lka_msg).
    # Not just telemetry: an actively-consumed value, unlike the removed *_kappa_cmd_raw stubs.
    # While the driver is pressing (before the human-turn override latches), the clipped planner
    # kappa can't follow the wheel: the driver moves the measured curvature faster than the
    # deviation clip tracks it, so the shadow can exit ford.h's error band mid-curve.
    # During a press, the shadow therefore follows the driver's actual curvature.
    self.bp_kappa_cmd = self.inputs.get_current_curvature(CS) if CS.out.steeringPressed else kappa_cmd

    # BluePilot: would the equivalent curvature (kappa_cmd) have been rate-limited by curvature-mode's
    # ROC (apply_std_steer_angle_limits)? kappa_cmd is already error-clipped above (same clip
    # curvature mode applies), so only the rate-of-change portion remains to simulate here.
    _equiv_curv_rl = apply_std_steer_angle_limits(kappa_cmd, self.sim_curvature_last, v_ego,
                                                  CS.out.steeringAngleDeg, CC.latActive, BP_ANGLE_LIMITS)
    self.bp_curvature_rate_limited = bool(abs(_equiv_curv_rl - kappa_cmd) > 1e-9)
    self.sim_curvature_last = float(_equiv_curv_rl)

    # Post-override stall detection (mechanism in the module constants' comment). Fires the mode-0
    # blip when, hands-free, desired curvature has led measured by more than 2x the deviation
    # clip's tolerance while the clip was actually binding for _STALL_HOLD_S accumulated seconds.
    # Intermittent clip binding holds the accumulator; a closed gap or driver press
    # ends the recovery episode.
    self.stall_blip_cooldown_s = max(0.0, self.stall_blip_cooldown_s - _STEER_DT)
    _stall_gap = desired_curvature - current_curvature
    _stalled = (not CS.out.steeringPressed and not self.lane_change and v_ego > 9.0
                and abs(_stall_gap) > _STALL_GAP_MIN
                and abs(desired_curvature) > abs(current_curvature))
    if _stalled:
      if (self.shared.bp_curvature_deviation_limited and self.stall_blip_cooldown_s <= 0.0 and
          not steering_responding):
        self.stall_blip_hold_s += _STEER_DT
      if (self.stall_blip_hold_s >= _STALL_HOLD_S and self.stall_blip_count < _STALL_MAX_BLIPS
          and abs(self.shared.path_angle_last) < _BLIP_MAX_PATH_ANGLE):
        self.stall_blip_frames_left = _STALL_BLIP_FRAMES
        self.stall_blip_hold_s = 0.0
        self.stall_blip_count += 1
    else:
      self.stall_blip_hold_s = 0.0
      if CS.out.steeringPressed or abs(_stall_gap) < 0.5 * _STALL_GAP_MIN:
        self.stall_blip_count = 0  # episode over: the car is tracking again or the driver took it

    ramp_type = 2

    return LateralResult(
      apply_curvature=0.0,
      curvature_rate=curvature_rate,
      path_offset=path_offset,
      path_angle=path_angle,
      ramp_type=ramp_type,
      precision_type=self.shared.precision_type,
      lateralUncertainty=lateral_uncertainty,
    )
