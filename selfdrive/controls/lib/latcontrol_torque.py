import math
import numpy as np
from collections import deque

from cereal import custom, log
from opendbc.car.gm.values import CAR as GM_CAR
from opendbc.car.honda.values import CAR as HONDA_CAR, HondaFlags
from opendbc.car.hyundai.values import CAR as HYUNDAI_CAR, HyundaiFlags
from opendbc.car.lateral import get_friction
from opendbc.car.toyota.values import CAR as TOYOTA_CAR
from openpilot.common.constants import ACCELERATION_DUE_TO_GRAVITY
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.pid import PIDController
from openpilot.selfdrive.controls.lib.drive_helpers import MIN_SPEED
from openpilot.selfdrive.controls.lib.latcontrol import LatControl
from openpilot.selfdrive.controls.lib.latcontrol_vehicle_tunes import *  # noqa: F403
from openpilot.selfdrive.controls.lib.latcontrol_vehicle_tunes import (
  KIA_NIRO_EV_CARS,
  get_genesis_g70_center_measurement_damping_gain,
  get_genesis_g70_traffic_friction_increment,
  get_kia_niro_ev_friction_threshold,
)

# At higher speeds (25+mph) we can assume:
# Lateral acceleration achieved by a specific car correlates to
# torque applied to the steering rack. It does not correlate to
# wheel slip, or to speed.

# This controller applies torque to achieve desired lateral
# accelerations. To compensate for the low speed effects the
# proportional gain is increased at low speeds by the PID controller.
# Additionally, there is friction in the steering wheel that needs
# to be overcome to move it at all, this is compensated for too.

KP = 0.6
KI = 0.35

INTERP_SPEEDS = [1, 1.5, 2.0, 3.0, 5, 7.5, 10, 15, 30]
KP_INTERP = [250, 120, 65, 30, 11.5, 5.5, 3.5, 2.0, KP]

LOW_SPEED_X = [0, 10, 20, 30]
LOW_SPEED_Y = [12, 10.5, 8, 5]
MAX_LAT_JERK_UP = 2.5            # m/s^3

LP_FILTER_CUTOFF_HZ = 1.2
JERK_LOOKAHEAD_SECONDS = 0.19
JERK_GAIN = 0.22
LAT_ACCEL_REQUEST_BUFFER_SECONDS = 1.0
VERSION = 2
DEBUG_TORQUE_TUNE = False
FF_SCALE_BLEND_LAT_ACCEL = 0.05
DEADZONE_BOOST_LAT_ACCEL = 0.15
UNWIND_D_DES_THRESHOLD = -1.0
UNWIND_LAT_ACCEL_NEAR_ZERO = 0.3
MIN_LATERAL_CONTROL_SPEED = 0.3

# Small planner jerk changes around the lane center can repeatedly re-trigger the
# friction compensation term. Keep this correction out of the center band while
# leaving actual turn-in and unwind commands unchanged.
CENTER_CHATTER_JERK_DEADZONE_SPEED_BP = [0.0, 5.0, 12.0, 25.0]  # m/s
CENTER_CHATTER_JERK_DEADZONE_SPEED_V = [0.08, 0.12, 0.18, 0.18]  # m/s^3
CENTER_CHATTER_JERK_DEADZONE_LAT_ACCEL_BP = [0.0, 0.18, 0.35]  # m/s^2
CENTER_CHATTER_JERK_DEADZONE_LAT_ACCEL_V = [1.0, 1.0, 0.0]


def get_center_chatter_friction_jerk_deadzone(v_ego, setpoint, vehicle_deadzone=0.0):
  """Return the small-signal jerk deadzone without changing turn commands."""
  speed_deadzone = np.interp(max(v_ego, 0.0), CENTER_CHATTER_JERK_DEADZONE_SPEED_BP,
                             CENTER_CHATTER_JERK_DEADZONE_SPEED_V)
  center_weight = np.interp(abs(setpoint), CENTER_CHATTER_JERK_DEADZONE_LAT_ACCEL_BP,
                            CENTER_CHATTER_JERK_DEADZONE_LAT_ACCEL_V)
  return max(float(vehicle_deadzone), float(speed_deadzone * center_weight))

# Roll compensation and latAccelOffset are lateral-accel-domain corrections; below
# walking pace the desired lateral accel is ~0 so an unfaded road-crown term dominates
# the whole feedforward and actively unwinds a held wheel at pull-away (newturn rlog
# 18.3-18.7s: ff pinned at -0.5 against a correct right-turn hold at 0.3 m/s).
FF_ROLL_OFFSET_FADE_BP = [0.5, 2.5]  # m/s
FF_ROLL_OFFSET_FADE_V = [0.0, 1.0]

# Envision C4 self-constraint
ENVISION_MAX_TORQUE_RATE = 5.0  # normalized torque units per second; output_torque is -1..1, NOT Nm
# Narrow center band (0.18 m/s^2) with mild damping (0.80) suppresses straight-line
# micro-chatter without starving turn-in torque or draining integral bias needed for
# road crown / minor wheel-speed deltas.
ENVISION_CENTER_DEADBAND_LAT_ACCEL = 0.18  # m/s^2
ENVISION_CENTER_MIN_SCALE = 0.80  # dimensionless
# 1.0 = verified default, matches the version road-tested for hundreds of km.
# i *= center_scale every frame, same rate as output taper, so the integrator
# cannot wind up in the center band. <1.0 is a future A/B knob, not enabled.
ENVISION_CENTER_I_DECAY_POWER = 1.0
ENVISION_UNWIND_OUTPUT_SCALE = 0.8  # dimensionless
ENVISION_UNWIND_MAX_SPEED_MPS = 25.0  # m/s; exactly 90 km/h (25 * 3.6)


class LatControlTorque(LatControl):
  def __init__(self, CP, CI, dt):
    super().__init__(CP, CI, dt)
    self.torque_params = CP.lateralTuning.torque.as_builder()
    self.torque_from_lateral_accel = CI.torque_from_lateral_accel()
    self.lateral_accel_from_torque = CI.lateral_accel_from_torque()
    self.pid = PIDController([INTERP_SPEEDS, KP_INTERP], KI, rate=1/self.dt)
    self.update_limits()
    self.steering_angle_deadzone_deg = self.torque_params.steeringAngleDeadzoneDeg
    self.request_buffer_len = int(LAT_ACCEL_REQUEST_BUFFER_SECONDS / self.dt)
    # Stores requested CURVATURE, scaled by the current v^2 on read. Storing lateral
    # accel directly makes the delayed request lag the measurement whenever speed is
    # changing (both scale with v^2 but the buffered value used the old speed), which
    # at creep-speed gains reads as a phantom unwind error during every pull-away.
    self.curvature_request_buffer = deque([0.] * self.request_buffer_len, maxlen=self.request_buffer_len)
    self.lookahead_frames = int(JERK_LOOKAHEAD_SECONDS / self.dt)
    self.jerk_filter = FirstOrderFilter(0.0, 1 / (2 * np.pi * LP_FILTER_CUTOFF_HZ), self.dt)
    self.ioniq_6_directional_taper_filter = FirstOrderFilter(1.0, IONIQ_6_DIRECTIONAL_TAPER_FILTER_RC, self.dt)
    self.previous_measurement = 0.0
    self.measurement_rate_filter = FirstOrderFilter(0.0, 1 / (2 * np.pi * (MAX_LAT_JERK_UP - 0.5)), self.dt)
    self.low_speed_reset_threshold = max(CP.minSteerSpeed, MIN_LATERAL_CONTROL_SPEED)
    self.steer_release_i_decay = 0.8
    self.prev_steering_pressed = False
    self.prev_output_torque = 0.0
    self.gv70_previous_feedforward = None
    self.debug_counter = 0
    self.prev_desired_lateral_accel = 0.0
    self.starpilot_lateral_state = custom.StarPilotLateralState.new_message()

    self.is_bolt = CP.carFingerprint in BOLT_CARS
    self.is_bolt_2022_2023 = CP.carFingerprint in BOLT_2022_2023_CARS
    self.is_bolt_2018_2021 = CP.carFingerprint in BOLT_2018_2021_CARS
    self.is_bolt_2017 = CP.carFingerprint in BOLT_2017_CARS
    self.is_volt_standard = CP.carFingerprint in VOLT_STANDARD_CARS
    self.is_genesis_g90 = CP.carFingerprint in GENESIS_G90_CARS
    self.is_genesis_g70 = CP.carFingerprint in GENESIS_G70_CARS
    self.is_genesis_gv70 = CP.carFingerprint in GENESIS_GV70_CARS
    if self.is_genesis_gv70:
      self.pid._k_d = [GENESIS_GV70_MEASUREMENT_DAMPING_SPEED_BP, GENESIS_GV70_MEASUREMENT_DAMPING_V]
    self.is_palisade = CP.carFingerprint in PALISADE_CARS
    self.is_prius = CP.carFingerprint in PRIUS_CARS
    self.is_standard_prius = CP.carFingerprint == TOYOTA_CAR.TOYOTA_PRIUS
    self.is_camry = CP.carFingerprint in CAMRY_CARS
    self.is_rav4_tss2 = CP.carFingerprint in RAV4_TSS2_CARS
    self.is_rav4_prime = CP.carFingerprint in RAV4_PRIME_CARS
    self.is_sienna_4th_gen = CP.carFingerprint in SIENNA_4TH_GEN_CARS
    self.is_toyota_highlander_tss2 = CP.carFingerprint in TOYOTA_HIGHLANDER_TSS2_CARS
    self.is_toyota_corolla_tss2 = CP.carFingerprint in TOYOTA_COROLLA_TSS2_CARS
    self.is_lexus_is = CP.carFingerprint in LEXUS_IS_CARS
    self.is_ioniq_5 = CP.carFingerprint in IONIQ_5_CARS
    self.is_ioniq_ev_old = CP.carFingerprint in IONIQ_EV_OLD_CARS
    self.is_ioniq_6 = CP.carFingerprint in IONIQ_6_CARS
    self.is_ioniq_6_2025 = is_ioniq_6_2025_model(CP)
    self.is_sonata = CP.carFingerprint in SONATA_CARS
    self.is_sonata_hybrid = CP.carFingerprint in SONATA_HYBRID_CARS
    self.is_elantra_non_scc = CP.carFingerprint in ELANTRA_NON_SCC_CARS
    self.is_kia_xceed = CP.carFingerprint in KIA_XCEED_CARS
    self.is_kia_niro_ev = CP.carFingerprint in KIA_NIRO_EV_CARS
    self.is_kia_niro_phev_2022 = CP.carFingerprint in KIA_NIRO_PHEV_2022_CARS
    self.is_kia_stinger_2022 = CP.carFingerprint in KIA_STINGER_2022_CARS
    self.is_kia_forte = CP.carFingerprint in KIA_FORTE_CARS
    self.is_kona_non_scc = CP.carFingerprint in KONA_NON_SCC_CARS
    self.is_kona_ev_2022 = CP.carFingerprint in KONA_EV_2022_CARS
    self.is_kia_ev6 = CP.carFingerprint in KIA_EV6_CARS
    self.is_kia_carnival = CP.carFingerprint in KIA_CARNIVAL_CARS
    self.is_kia_carnival_hev = CP.carFingerprint == HYUNDAI_CAR.KIA_CARNIVAL_HEV_4TH_GEN
    self.is_tucson_4th_gen = CP.carFingerprint in TUCSON_4TH_GEN_CARS
    self.is_civic_bosch_modified = CP.carFingerprint == HONDA_CAR.HONDA_CIVIC_BOSCH and bool(CP.flags & HondaFlags.EPS_MODIFIED)
    self.is_honda_accord = CP.carFingerprint == HONDA_CAR.HONDA_ACCORD
    self.is_silverado = CP.carFingerprint in SILVERADO_CARS
    self.is_gmc_yukon_cc = CP.carFingerprint in GMC_YUKON_CC_CARS
    self.is_ram_1500 = CP.carFingerprint in RAM_1500_CARS
    self.is_gm = CP.brand == "gm"
    self.is_hkg_canfd_torque = CP.brand == "hyundai" and bool(CP.flags & HyundaiFlags.CANFD)
    self.flm_surface_profile_key = get_flm_surface_profile_key(CP.carFingerprint, torque_control=True)
    if self.is_ioniq_6:
      self.low_speed_reset_threshold = min(self.low_speed_reset_threshold, IONIQ_6_LOW_SPEED_PID_RESET_SPEED)
    self.use_bolt_ff_scaling = self.is_bolt_2022_2023 or self.is_bolt_2018_2021 or self.is_bolt_2017
    self.use_bolt_ki_multiplier = self.use_bolt_ff_scaling
    self.torque_ff_scale_pos = 1.0
    self.torque_ff_scale_neg = 1.0
    self.torque_deadzone_boost = float(getattr(self.torque_params, "kfDEPRECATED", 0.0))
    self.torque_ki_mult = 1.0
    if self.is_honda_accord:
      self.pid._k_p = [self.pid._k_p[0], [*self.pid._k_p[1][:-1], HONDA_ACCORD_TORQUE_KP]]
      self.pid._k_i = [self.pid._k_i[0], [HONDA_ACCORD_TORQUE_KI] * len(self.pid._k_i[1])]
    # ==================== Envision Specific Tuning ====================
    self.is_envision = CP.carFingerprint == GM_CAR.BUICK_ENVISION_SDGM
    if self.is_envision:
      # Below ~3 m/s lateral control is inactive, so early gains are placeholders.
      # The stock KP at 5-10 m/s over-amplifies IMU noise from SUV suspension rebound.
      self.pid._k_p = [
        [1, 1.5, 2.0, 3.0, 5, 7.5, 10, 15, 30],       # Speed grid (m/s)
        [25, 20, 18.0, 18.0, 8.0, 3.1, 1.3, 0.7, 0.58]  # Smoothed KP values
      ]
    # ================================================================
    if self.is_palisade:
      self.torque_params.latAccelFactor *= PALISADE_BASE_LAT_ACCEL_FACTOR_MULT
    if self.is_ioniq_5:
      self.torque_params.latAccelFactor *= IONIQ_5_BASE_LAT_ACCEL_FACTOR_MULT
    if self.is_ioniq_ev_old:
      self.torque_params.latAccelFactor *= IONIQ_EV_OLD_BASE_LAT_ACCEL_FACTOR_MULT
    if self.is_ioniq_6:
      self.torque_params.latAccelFactor *= IONIQ_6_BASE_LAT_ACCEL_FACTOR_MULT
    if self.is_sonata_hybrid:
      self.torque_params.latAccelFactor *= SONATA_HYBRID_BASE_LAT_ACCEL_FACTOR_MULT
    if self.is_kia_forte:
      self.torque_params.latAccelFactor *= KIA_FORTE_BASE_LAT_ACCEL_FACTOR_MULT
    if self.is_ram_1500:
      self.torque_params.latAccelFactor *= RAM_1500_BASE_LAT_ACCEL_FACTOR_MULT
      self.update_limits()
    if self.is_civic_bosch_modified:
      self.torque_params.latAccelFactor *= CIVIC_BOSCH_MODIFIED_B_LAT_ACCEL_FACTOR_MULT
      if civic_bosch_modified_a_lateral_testing_ground_active():
        self.torque_params.latAccelFactor *= CIVIC_BOSCH_MODIFIED_A_VARIANT_LAT_ACCEL_FACTOR_MULT
      if civic_bosch_modified_lateral_testing_ground_active():
        self.torque_params.latAccelFactor *= CIVIC_BOSCH_MODIFIED_B_VARIANT_LAT_ACCEL_FACTOR_MULT
    if self.is_bolt:
      kp_scale = getattr(self.torque_params, "kp", getattr(self.torque_params, "kpDEPRECATED", 1.0))
      ki_scale = getattr(self.torque_params, "ki", getattr(self.torque_params, "kiDEPRECATED", 1.0))
      kd_scale = getattr(self.torque_params, "kd", getattr(self.torque_params, "kdDEPRECATED", 1.0))
      self.torque_ff_scale_pos = float(kp_scale)
      self.torque_ff_scale_neg = float(ki_scale)
      self.torque_ki_mult = float(kd_scale)
      if self.use_bolt_ki_multiplier and self.torque_ki_mult > 0.0 and self.torque_ki_mult != 1.0:
        self.pid._k_i = [self.pid._k_i[0], [k * self.torque_ki_mult for k in self.pid._k_i[1]]]

  def _clear_starpilot_lateral_state(self):
    self.starpilot_lateral_state.active = False
    self.starpilot_lateral_state.frictionThreshold = 0.0
    self.starpilot_lateral_state.frictionScale = 0.0
    self.starpilot_lateral_state.feedforward = 0.0
    self.starpilot_lateral_state.frictionJerk = 0.0
    self.starpilot_lateral_state.frictionJerkDeadzone = 0.0
    self.starpilot_lateral_state.lowSpeedFactor = 0.0
    self.starpilot_lateral_state.unwindDetected = False

  def update_live_torque_params(self, latAccelFactor, latAccelOffset, friction):
    if self.is_palisade:
      latAccelFactor *= PALISADE_BASE_LAT_ACCEL_FACTOR_MULT
    if self.is_ioniq_5:
      latAccelFactor *= IONIQ_5_BASE_LAT_ACCEL_FACTOR_MULT
    if self.is_ioniq_ev_old:
      latAccelFactor *= IONIQ_EV_OLD_BASE_LAT_ACCEL_FACTOR_MULT
    if self.is_ioniq_6:
      latAccelFactor *= IONIQ_6_BASE_LAT_ACCEL_FACTOR_MULT
    if self.is_sonata_hybrid:
      latAccelFactor *= SONATA_HYBRID_BASE_LAT_ACCEL_FACTOR_MULT
    if self.is_kia_forte:
      latAccelFactor *= KIA_FORTE_BASE_LAT_ACCEL_FACTOR_MULT
    if self.is_ram_1500:
      latAccelFactor *= RAM_1500_BASE_LAT_ACCEL_FACTOR_MULT
    if self.is_civic_bosch_modified:
      latAccelFactor *= CIVIC_BOSCH_MODIFIED_B_LAT_ACCEL_FACTOR_MULT
      if civic_bosch_modified_a_lateral_testing_ground_active():
        latAccelFactor *= CIVIC_BOSCH_MODIFIED_A_VARIANT_LAT_ACCEL_FACTOR_MULT
      if civic_bosch_modified_lateral_testing_ground_active():
        latAccelFactor *= CIVIC_BOSCH_MODIFIED_B_VARIANT_LAT_ACCEL_FACTOR_MULT
    self.torque_params.latAccelFactor = latAccelFactor
    self.torque_params.latAccelOffset = latAccelOffset
    self.torque_params.friction = friction
    self.update_limits()

  def update_limits(self):
    self.pid.set_limits(self.lateral_accel_from_torque(self.steer_max, self.torque_params),
                        self.lateral_accel_from_torque(-self.steer_max, self.torque_params))

  def update(self, active, CS, VM, params, steer_limited_by_safety, desired_curvature, curvature_limited, lat_delay, calibrated_pose, model_data, starpilot_toggles):
    pid_log = log.ControlsState.LateralTorqueState.new_message()
    pid_log.version = VERSION
    flm_profile_active = bool(getattr(starpilot_toggles, "flm_trial_applied", False) and
                              getattr(starpilot_toggles, "flm_active_profile_id", ""))
    set_flm_runtime_overrides(getattr(starpilot_toggles, "flm_active_overrides", None) if flm_profile_active else None)
    flm_surface_active = flm_profile_active and flm_runtime_overrides_active()
    measured_curvature = -VM.calc_curvature(math.radians(CS.steeringAngleDeg - params.angleOffsetDeg), CS.vEgo, params.roll)
    measurement = measured_curvature * CS.vEgo ** 2
    future_desired_lateral_accel = desired_curvature * CS.vEgo ** 2
    if not active:
      output_torque = 0.0
      self.prev_output_torque = 0.0
      self.gv70_previous_feedforward = None
      pid_log.active = False
      self._clear_starpilot_lateral_state()
      self.pid.reset()
      # Keep the request buffer and rate state primed with the live command (which tracks
      # the measured curvature while inactive) instead of zeroing them. Re-engaging with a
      # wound wheel against a zeroed buffer puts the setpoint ~lat_delay behind the
      # measurement, and the low-speed gains turn that lag into a hard unwind shove
      # (turnn rlog 38.75s: +0.8 torque against a held right turn on pull-away).
      self.curvature_request_buffer.append(desired_curvature)
      self.previous_measurement = measurement
      self.measurement_rate_filter.x = 0.0
      self.jerk_filter.x = 0.0
      self.prev_desired_lateral_accel = future_desired_lateral_accel
      self.ioniq_6_directional_taper_filter.x = 1.0
    else:
      if self.prev_steering_pressed and not CS.steeringPressed:
        self.pid.i *= self.steer_release_i_decay

      roll_offset_fade = np.interp(CS.vEgo, FF_ROLL_OFFSET_FADE_BP, FF_ROLL_OFFSET_FADE_V)
      roll_compensation = params.roll * ACCELERATION_DUE_TO_GRAVITY * roll_offset_fade
      flm_center_deadband_deg = (
        get_flm_full_surface_center_deadband_deg(self.flm_surface_profile_key, CS.vEgo) if flm_surface_active else 0.0
      )
      effective_deadband_deg = self.steering_angle_deadzone_deg + flm_center_deadband_deg
      curvature_deadzone = abs(VM.calc_curvature(math.radians(effective_deadband_deg), CS.vEgo, 0.0))
      lateral_accel_deadzone = curvature_deadzone * CS.vEgo ** 2

      delay_frames = int(np.clip(lat_delay / self.dt, 1, self.request_buffer_len))
      expected_lateral_accel = self.curvature_request_buffer[-delay_frames] * CS.vEgo ** 2
      self.curvature_request_buffer.append(desired_curvature)
      lateral_jerk_limit = RAM_1500_MAX_LAT_JERK_UP if self.is_ram_1500 else MAX_LAT_JERK_UP
      raw_lateral_jerk = (future_desired_lateral_accel - expected_lateral_accel) / max(lat_delay, self.dt)
      raw_lateral_jerk = np.clip(raw_lateral_jerk, -lateral_jerk_limit, lateral_jerk_limit)
      desired_lateral_jerk = np.clip(self.jerk_filter.update(raw_lateral_jerk), -lateral_jerk_limit, lateral_jerk_limit)
      gravity_adjusted_future_lateral_accel = future_desired_lateral_accel - roll_compensation
      setpoint = expected_lateral_accel + desired_lateral_jerk * lat_delay
      desired_lateral_accel_rate = (setpoint - self.prev_desired_lateral_accel) / self.dt
      unwind_detected = (desired_lateral_accel_rate < UNWIND_D_DES_THRESHOLD and
                         abs(setpoint) < UNWIND_LAT_ACCEL_NEAR_ZERO)
      self.prev_desired_lateral_accel = setpoint

      measurement_rate = self.measurement_rate_filter.update((measurement - self.previous_measurement) / self.dt)
      measurement_rate = np.clip(measurement_rate, -MAX_LAT_JERK_UP, MAX_LAT_JERK_UP)
      self.previous_measurement = measurement

      low_speed_factor = (np.interp(CS.vEgo, LOW_SPEED_X, LOW_SPEED_Y) / max(CS.vEgo, MIN_SPEED)) ** 2
      current_kp = np.interp(CS.vEgo, self.pid._k_p[0], self.pid._k_p[1])
      error = setpoint - measurement
      error_with_lsf = error * (1 + low_speed_factor / max(current_kp, 1e-3))
      if self.is_ioniq_6_2025:
        error_with_lsf *= get_ioniq_6_2025_low_speed_center_error_scale(
          setpoint, desired_lateral_jerk, CS.vEgo,
        )

      # do error correction in lateral acceleration space, convert at end to handle non-linear torque responses correctly
      carnival_hev_center_weight = (
        get_kia_carnival_hev_center_weight(CS.vEgo, setpoint, measurement, desired_lateral_jerk, future_desired_lateral_accel)
        if self.is_kia_carnival_hev and not CS.steeringPressed and not self.prev_steering_pressed else 0.0
      )
      pid_log.error = float(error_with_lsf * (1.0 - KIA_CARNIVAL_HEV_CENTER_ERROR_REDUCTION * carnival_hev_center_weight))
      ff = gravity_adjusted_future_lateral_accel
      # latAccelOffset corrects roll compensation bias from device roll misalignment relative to car roll
      ff -= self.torque_params.latAccelOffset * roll_offset_fade
      ff_scale = 1.0
      if self.use_bolt_ff_scaling:
        ff_scale = np.interp(ff, [-FF_SCALE_BLEND_LAT_ACCEL, 0.0, FF_SCALE_BLEND_LAT_ACCEL],
                             [self.torque_ff_scale_neg, 1.0, self.torque_ff_scale_pos])
      ff *= ff_scale
      if self.is_ram_1500:
        ff *= get_ram_1500_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo)
      if self.is_gmc_yukon_cc:
        ff *= get_gmc_yukon_cc_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo)
      trailer_load_kg = float(max(getattr(starpilot_toggles, "trailer_load_kg", 0.0) or 0.0, 0.0))
      bolt_2022_2023_tuned_path_active = self.is_bolt_2022_2023
      bolt_2018_2021_tuned_path_active = self.is_bolt_2018_2021
      volt_standard_test_active = self.is_volt_standard and volt_standard_lateral_testing_ground_active()
      genesis_g90_test_active = self.is_genesis_g90 and genesis_g90_lateral_testing_ground_active()
      palisade_active = self.is_palisade
      genesis_g70_active = self.is_genesis_g70
      prius_active = self.is_prius
      camry_active = self.is_camry
      rav4_tss2_active = self.is_rav4_tss2
      rav4_prime_active = self.is_rav4_prime
      sienna_4th_gen_active = self.is_sienna_4th_gen
      toyota_highlander_tss2_active = self.is_toyota_highlander_tss2
      toyota_corolla_tss2_active = self.is_toyota_corolla_tss2
      lexus_is_active = self.is_lexus_is
      ioniq_5_active = self.is_ioniq_5
      ioniq_ev_old_active = self.is_ioniq_ev_old
      ioniq_6_active = self.is_ioniq_6
      sonata_active = self.is_sonata
      sonata_hybrid_active = self.is_sonata_hybrid
      genesis_g70_center_output_taper = get_genesis_g70_center_output_scale(setpoint, CS.vEgo) if genesis_g70_active else 1.0
      elantra_non_scc_active = self.is_elantra_non_scc
      kia_xceed_active = self.is_kia_xceed
      kia_niro_phev_2022_active = self.is_kia_niro_phev_2022
      kia_stinger_2022_active = self.is_kia_stinger_2022
      kia_forte_active = self.is_kia_forte
      kia_ev6_active = self.is_kia_ev6
      kia_carnival_active = self.is_kia_carnival
      tucson_4th_gen_active = self.is_tucson_4th_gen
      volt_plexy_test_active = self.is_volt_standard and volt_plexy_lateral_testing_ground_active()
      ioniq_5_center_taper = get_ioniq_5_center_taper_scale(setpoint, CS.vEgo) if ioniq_5_active else 1.0
      prius_center_taper = get_prius_center_taper_scale(setpoint, CS.vEgo) if prius_active else 1.0
      volt_standard_center_taper = get_volt_standard_center_taper_scale(setpoint, CS.vEgo) if volt_standard_test_active else 1.0
      volt_plexy_center_taper = get_volt_plexy_center_taper_scale(setpoint, CS.vEgo) if volt_plexy_test_active else 1.0
      ioniq_ev_old_center_taper = get_ioniq_ev_old_center_taper_scale(setpoint, CS.vEgo) if ioniq_ev_old_active else 1.0
      ioniq_6_center_taper = get_ioniq_6_center_taper_scale(setpoint, CS.vEgo) if ioniq_6_active else 1.0
      sonata_center_taper = get_sonata_center_taper_scale(setpoint, CS.vEgo) if sonata_active else 1.0
      sonata_hybrid_center_taper = get_sonata_hybrid_center_taper_scale(setpoint, CS.vEgo) if sonata_hybrid_active else 1.0
      sonata_hybrid_center_output_taper = get_sonata_hybrid_center_output_scale(setpoint, CS.vEgo) if sonata_hybrid_active else 1.0
      kia_xceed_center_taper = get_kia_xceed_center_taper_scale(setpoint, CS.vEgo) if kia_xceed_active else 1.0
      kia_niro_phev_2022_center_taper = get_kia_niro_phev_2022_center_taper_scale(setpoint, CS.vEgo) if kia_niro_phev_2022_active else 1.0
      kia_stinger_2022_center_taper = get_kia_stinger_2022_center_taper_scale(setpoint, CS.vEgo) if kia_stinger_2022_active else 1.0
      kia_forte_center_taper = get_kia_forte_center_taper_scale(setpoint, CS.vEgo) if kia_forte_active else 1.0
      kia_ev6_center_taper = get_kia_ev6_center_taper_scale(setpoint, CS.vEgo) if kia_ev6_active else 1.0
      kia_ev6_low_speed_center_taper = get_kia_ev6_low_speed_center_taper_scale(setpoint, CS.vEgo) if kia_ev6_active else 1.0
      kia_carnival_center_taper = get_kia_carnival_center_taper_scale(setpoint, CS.vEgo) if kia_carnival_active else 1.0
      tucson_4th_gen_center_taper = get_tucson_4th_gen_center_taper_scale(setpoint, CS.vEgo) if tucson_4th_gen_active else 1.0
      palisade_center_taper = get_palisade_center_taper_scale(setpoint, CS.vEgo) if palisade_active else 1.0
      silverado_center_taper = get_silverado_center_taper_scale(setpoint, CS.vEgo) if self.is_silverado else 1.0
      civic_bosch_modified_a_center_taper = get_civic_bosch_modified_a_center_taper_scale(setpoint, CS.vEgo) if (
        self.is_civic_bosch_modified and civic_bosch_modified_a_lateral_testing_ground_active()
      ) else 1.0
      if self.is_hkg_canfd_torque:
        friction_threshold = get_hkg_canfd_base_friction_threshold(CS.vEgo)
      elif self.is_gm:
        friction_threshold = get_gm_base_friction_threshold(CS.vEgo)
      else:
        friction_threshold = get_standard_friction_threshold(CS.vEgo)
      if self.is_genesis_g70:
        friction_threshold = get_genesis_g70_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
        if not CS.steeringPressed and not self.prev_steering_pressed:
          friction_threshold += get_genesis_g70_traffic_friction_increment(
            CS.vEgo, setpoint, desired_lateral_jerk, desired_curvature, measured_curvature,
          )
      elif self.is_genesis_gv70:
        friction_threshold = get_genesis_gv70_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
      elif self.is_sonata_hybrid:
        friction_threshold = get_sonata_hybrid_friction_threshold(CS.vEgo, setpoint)
      elif self.is_kia_niro_ev and not CS.steeringPressed:
        friction_threshold = get_kia_niro_ev_friction_threshold(
          CS.vEgo, setpoint, desired_lateral_jerk, future_desired_lateral_accel,
        )
      friction_scale = 1.0
      if bolt_2022_2023_tuned_path_active:
        ff *= get_bolt_2022_2023_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo)
        friction_threshold = get_bolt_2022_2023_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = get_bolt_2022_2023_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
      elif bolt_2018_2021_tuned_path_active:
        friction_threshold = get_bolt_2018_2021_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = get_bolt_2018_2021_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
      elif volt_standard_test_active:
        ff *= get_volt_standard_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo) * volt_standard_center_taper
        friction_threshold = get_volt_standard_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = get_volt_standard_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = 1.0 + ((friction_scale - 1.0) * volt_standard_center_taper)
      elif genesis_g90_test_active:
        ff *= get_genesis_g90_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo)
        friction_threshold = get_genesis_g90_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = get_genesis_g90_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
      elif palisade_active:
        ff *= get_palisade_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo) * palisade_center_taper
        friction_threshold = get_palisade_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = get_palisade_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = 1.0 + ((friction_scale - 1.0) * palisade_center_taper)
      elif prius_active:
        ff *= get_prius_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo) * prius_center_taper
        friction_threshold = get_prius_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = get_prius_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = 1.0 + ((friction_scale - 1.0) * prius_center_taper)
      elif camry_active:
        ff *= get_camry_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo)
        friction_threshold = get_camry_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
      elif rav4_tss2_active:
        friction_threshold = get_rav4_tss2_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
      elif rav4_prime_active:
        ff *= get_rav4_prime_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo)
        friction_threshold = get_rav4_prime_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = get_rav4_prime_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
      elif sienna_4th_gen_active:
        ff *= get_sienna_4th_gen_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo)
        friction_threshold = get_sienna_4th_gen_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
      elif toyota_highlander_tss2_active:
        ff *= get_toyota_highlander_tss2_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo)
        friction_threshold = get_toyota_highlander_tss2_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = get_toyota_highlander_tss2_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
      elif toyota_corolla_tss2_active:
        ff *= get_toyota_corolla_tss2_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo)
        friction_threshold = get_toyota_corolla_tss2_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
      elif lexus_is_active:
        ff *= get_lexus_is_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo)
      elif ioniq_5_active:
        ff *= get_ioniq_5_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo) * ioniq_5_center_taper
        friction_threshold = get_ioniq_5_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = get_ioniq_5_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = 1.0 + ((friction_scale - 1.0) * ioniq_5_center_taper)
      elif ioniq_ev_old_active:
        ff *= get_ioniq_ev_old_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo) * ioniq_ev_old_center_taper
        friction_scale = 1.0 + ((friction_scale - 1.0) * ioniq_ev_old_center_taper)
      elif ioniq_6_active:
        # smooth the directional taper so jerk-gated unwind cuts can't step the FF in one frame
        ioniq_6_directional_taper = self.ioniq_6_directional_taper_filter.update(
          get_ioniq_6_directional_taper_scale(setpoint, desired_lateral_jerk, CS.vEgo))
        ff *= get_ioniq_6_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo,
                                   directional_taper_scale=ioniq_6_directional_taper) * ioniq_6_center_taper
        if not self.is_ioniq_6_2025:
          ff *= get_ioniq_6_2023_unwind_ff_scale(setpoint, measurement, desired_lateral_jerk, CS.vEgo)
        friction_threshold = get_ioniq_6_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk) / max(ioniq_6_center_taper, 1e-3)
        if not self.is_ioniq_6_2025 and not CS.steeringPressed and not self.prev_steering_pressed:
          friction_threshold += get_ioniq_6_2023_center_friction_increment(
            CS.vEgo, setpoint, measurement, desired_lateral_jerk, future_desired_lateral_accel,
          ) / max(ioniq_6_center_taper, 1e-3)
        friction_scale = get_ioniq_6_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = 1.0 + ((friction_scale - 1.0) * ioniq_6_center_taper)
        friction_scale *= get_ioniq_6_friction_center_fade_scale(setpoint, CS.vEgo)
        if self.is_ioniq_6_2025:
          friction_scale *= IONIQ_6_2025_FRICTION_SCALE_MULT
      elif sonata_active:
        ff *= get_sonata_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo) * sonata_center_taper
      elif sonata_hybrid_active:
        ff *= get_sonata_hybrid_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo) * sonata_hybrid_center_taper
      elif elantra_non_scc_active:
        ff *= get_elantra_non_scc_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo)
      elif kia_xceed_active:
        ff *= get_kia_xceed_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo) * kia_xceed_center_taper
      elif kia_niro_phev_2022_active:
        friction_threshold = get_kia_niro_phev_2022_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
      elif kia_stinger_2022_active:
        friction_threshold = get_kia_stinger_2022_friction_threshold(
          CS.vEgo, setpoint, desired_lateral_jerk, future_desired_lateral_accel,
          CS.steeringPressed or self.prev_steering_pressed,
        )
      elif kia_forte_active:
        ff *= get_kia_forte_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo) * kia_forte_center_taper
        friction_threshold = get_kia_forte_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
      elif kia_ev6_active:
        ff *= get_kia_ev6_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo) * kia_ev6_center_taper
        friction_threshold = get_kia_ev6_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = get_kia_ev6_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = 1.0 + ((friction_scale - 1.0) * kia_ev6_center_taper)
      elif kia_carnival_active:
        friction_threshold = get_kia_carnival_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale *= get_kia_carnival_friction_center_fade_scale(setpoint, CS.vEgo)
      elif self.is_kona_non_scc:
        friction_threshold = get_kona_non_scc_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
      elif self.is_kona_ev_2022:
        friction_threshold = get_kona_ev_2022_friction_threshold(CS.vEgo, setpoint)
      elif tucson_4th_gen_active:
        friction_threshold = get_tucson_4th_gen_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
      elif self.is_silverado:
        ff *= silverado_center_taper
      elif volt_plexy_test_active:
        ff *= get_volt_plexy_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo) * volt_plexy_center_taper
        friction_threshold = get_volt_plexy_friction_threshold(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = get_volt_plexy_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = 1.0 + ((friction_scale - 1.0) * volt_plexy_center_taper)
      elif self.is_civic_bosch_modified:
        ff *= get_civic_bosch_modified_b_ff_scale(setpoint, desired_lateral_jerk, CS.vEgo) * civic_bosch_modified_a_center_taper
        friction_threshold = CIVIC_BOSCH_MODIFIED_B_FIXED_FRICTION_THRESHOLD
        friction_scale = get_civic_bosch_modified_b_friction_scale(CS.vEgo, setpoint, desired_lateral_jerk)
        friction_scale = 1.0 + ((friction_scale - 1.0) * civic_bosch_modified_a_center_taper)
      if self.is_honda_accord:
        ff *= get_honda_accord_ff_scale(setpoint)
      if flm_surface_active and self.flm_surface_profile_key and not ioniq_6_active:
        universal_flm_profile = self.flm_surface_profile_key == FLM_UNIVERSAL_PROFILE_KEY
        flm_full_surface_center_taper = get_flm_full_surface_center_taper_scale(self.flm_surface_profile_key, setpoint, CS.vEgo,
                                                                                include_base_center=universal_flm_profile)
        ff *= get_flm_full_surface_ff_scale(self.flm_surface_profile_key, setpoint, desired_lateral_jerk, CS.vEgo,
                                            include_base_ff=universal_flm_profile) * flm_full_surface_center_taper
        friction_threshold = get_flm_full_surface_friction_threshold(self.flm_surface_profile_key, friction_threshold, CS.vEgo,
                                                                     setpoint, desired_lateral_jerk,
                                                                     include_base_threshold=universal_flm_profile)
      if trailer_load_kg > 0.0:
        ff *= get_trailer_lateral_ff_scale(trailer_load_kg, CS.vEgo, setpoint)
        friction_scale *= get_trailer_lateral_friction_scale(trailer_load_kg, CS.vEgo, setpoint)
      if self.is_ioniq_6_2025:
        friction_scale *= get_ioniq_6_2025_low_speed_center_friction_scale(
          setpoint, desired_lateral_jerk, CS.vEgo,
        )
      if self.is_genesis_gv70:
        ff *= get_genesis_gv70_unwind_ff_scale(
          setpoint, measurement, desired_lateral_jerk, CS.vEgo,
        )
      if self.is_genesis_g70:
        ff *= get_genesis_g70_unwind_ff_scale(
          setpoint, measurement, desired_lateral_jerk, CS.vEgo,
        )
      if kia_carnival_active:
        ff *= get_kia_carnival_unwind_ff_scale(
          setpoint, measurement, desired_lateral_jerk, CS.vEgo,
        )
      if ioniq_6_active:
        vehicle_friction_jerk_deadzone = (
          IONIQ_6_2025_FRICTION_JERK_DEADZONE if self.is_ioniq_6_2025 else IONIQ_6_FRICTION_JERK_DEADZONE
        )
      elif ioniq_5_active:
        vehicle_friction_jerk_deadzone = get_ioniq_5_friction_jerk_deadzone(CS.vEgo, setpoint)
      elif prius_active:
        prius_deadzone_max = (PRIUS_STANDARD_FRICTION_JERK_DEADZONE_MAX if self.is_standard_prius
                              else PRIUS_FRICTION_JERK_DEADZONE_MAX)
        vehicle_friction_jerk_deadzone = get_prius_friction_jerk_deadzone(
          CS.vEgo, setpoint, prius_deadzone_max,
        )
      elif genesis_g70_active:
        vehicle_friction_jerk_deadzone = get_genesis_g70_friction_jerk_deadzone(
          CS.vEgo, setpoint, desired_lateral_jerk, measurement,
        )
      elif self.is_genesis_gv70:
        vehicle_friction_jerk_deadzone = get_genesis_gv70_friction_jerk_deadzone(CS.vEgo, setpoint)
      elif kia_carnival_active:
        vehicle_friction_jerk_deadzone = get_kia_carnival_friction_jerk_deadzone(
          CS.vEgo, setpoint, desired_lateral_jerk,
        )
      else:
        vehicle_friction_jerk_deadzone = 0.0
      friction_jerk_deadzone = get_center_chatter_friction_jerk_deadzone(
        CS.vEgo, setpoint, vehicle_friction_jerk_deadzone
      )
      friction_jerk = math.copysign(max(abs(desired_lateral_jerk) - friction_jerk_deadzone, 0.0),
                                    desired_lateral_jerk)
      friction_threshold += KIA_CARNIVAL_HEV_CENTER_FRICTION_INCREMENT * carnival_hev_center_weight
      ff += friction_scale * get_friction(error_with_lsf + JERK_GAIN * friction_jerk, lateral_accel_deadzone, friction_threshold, self.torque_params)
      deadzone_boost_active = False
      if self.torque_deadzone_boost > 0.0 and abs(gravity_adjusted_future_lateral_accel) < DEADZONE_BOOST_LAT_ACCEL:
        boost_scale = np.interp(abs(gravity_adjusted_future_lateral_accel), [0.0, DEADZONE_BOOST_LAT_ACCEL], [1.0, 0.0])
        ff += np.sign(gravity_adjusted_future_lateral_accel) * self.torque_deadzone_boost * boost_scale
        deadzone_boost_active = True

      if CS.vEgo < self.low_speed_reset_threshold:
        self.pid.reset()
      if self.is_genesis_gv70:
        ff *= get_genesis_gv70_center_output_scale(setpoint, CS.vEgo)
        ff *= get_genesis_gv70_low_speed_center_overshoot_scale(setpoint, measurement, CS.vEgo)
        ff *= get_genesis_gv70_high_speed_error_scale(setpoint, measurement, desired_lateral_jerk, CS.vEgo)
        ff *= get_genesis_gv70_reversal_output_scale(setpoint, measurement, desired_lateral_jerk, CS.vEgo)
        if not CS.steeringPressed and not self.prev_steering_pressed and self.gv70_previous_feedforward is not None:
          ff = get_genesis_gv70_stabilized_output(
            ff, self.gv70_previous_feedforward, setpoint, desired_lateral_jerk, CS.vEgo, self.dt,
          )
        self.gv70_previous_feedforward = ff
      if self.is_genesis_g70:
        damping_gain = 0.0 if CS.steeringPressed else get_genesis_g70_center_measurement_damping_gain(
          CS.vEgo, setpoint, measurement, desired_lateral_jerk,
        )
        self.pid._k_d = [[0.0], [damping_gain]]
      freeze_integrator = (steer_limited_by_safety or CS.steeringPressed or
                           CS.vEgo < self.low_speed_reset_threshold or unwind_detected)
      error_rate = 0.0 if self.is_genesis_gv70 and CS.steeringPressed else -measurement_rate
      output_lataccel = self.pid.update(pid_log.error, error_rate=error_rate, speed=CS.vEgo, feedforward=ff,
                                       freeze_integrator=freeze_integrator)
      output_torque = self.torque_from_lateral_accel(output_lataccel, self.torque_params)
      if bolt_2022_2023_tuned_path_active:
        output_torque *= get_bolt_2022_2023_center_output_scale(setpoint, CS.vEgo)
        low_speed_center_output_limit = get_bolt_2022_2023_low_speed_center_output_limit(setpoint, CS.vEgo)
        output_torque = float(np.clip(
          output_torque,
          -low_speed_center_output_limit,
          low_speed_center_output_limit,
        ))
        output_torque = get_bolt_2022_2023_low_speed_center_output(
          output_torque, self.prev_output_torque, setpoint, CS.vEgo,
        )
      elif self.is_bolt_2017:
        output_torque *= get_bolt_2017_torque_scale(setpoint, desired_lateral_jerk, CS.vEgo)
      elif bolt_2018_2021_tuned_path_active:
        output_torque *= get_bolt_2018_2021_dynamic_torque_scale(setpoint, desired_lateral_jerk, CS.vEgo)
      elif ioniq_6_active and not CS.steeringPressed:
        desired_angle_no_offset = math.degrees(VM.get_steer_from_curvature(-desired_curvature, CS.vEgo, params.roll))
        actual_angle_no_offset = CS.steeringAngleDeg - params.angleOffsetDeg
        output_torque = get_ioniq_6_low_speed_angle_assist_torque(desired_angle_no_offset, actual_angle_no_offset,
                                                                  output_torque, CS.vEgo)
      elif flm_surface_active and self.flm_surface_profile_key and not CS.steeringPressed:
        desired_angle_no_offset = math.degrees(VM.get_steer_from_curvature(-desired_curvature, CS.vEgo, params.roll))
        actual_angle_no_offset = CS.steeringAngleDeg - params.angleOffsetDeg
        output_torque = get_flm_full_surface_low_speed_angle_assist_torque(self.flm_surface_profile_key, desired_angle_no_offset,
                                                                           actual_angle_no_offset, output_torque, CS.vEgo)
      elif self.is_genesis_g70 and not CS.steeringPressed and CS.vEgo < GENESIS_G70_LOW_SPEED_ANGLE_DAMPING_SPEED + 2.0:
        desired_angle_no_offset = math.degrees(VM.get_steer_from_curvature(-desired_curvature, CS.vEgo, params.roll))
        actual_angle_no_offset = CS.steeringAngleDeg - params.angleOffsetDeg
        output_torque = get_genesis_g70_low_speed_angle_damping(desired_angle_no_offset, actual_angle_no_offset,
                                                                 output_torque, CS.vEgo)
      if ioniq_6_active:
        output_torque *= get_ioniq_6_highway_output_taper_scale(setpoint, CS.vEgo)
        output_torque *= get_ioniq_6_highway_transition_output_taper_scale(setpoint, desired_lateral_jerk, CS.vEgo)
        if self.is_ioniq_6_2025:
          output_torque *= get_ioniq_6_2025_center_output_scale(setpoint, CS.vEgo)
          low_speed_output_limit = get_ioniq_6_2025_low_speed_output_limit(setpoint, desired_lateral_jerk, CS.vEgo)
          output_torque = float(np.clip(
            output_torque,
            -low_speed_output_limit,
            low_speed_output_limit,
          ))
      elif ioniq_5_active:
        low_speed_output_limit = get_ioniq_5_low_speed_output_limit(
          setpoint, desired_lateral_jerk, CS.vEgo,
          desired_curvature if not CS.steeringPressed and not self.prev_steering_pressed else 0.0, output_torque,
        )
        output_torque = float(np.clip(
          output_torque,
          -low_speed_output_limit,
          low_speed_output_limit,
        ))
      elif self.is_ram_1500:
        output_torque *= get_ram_1500_center_output_scale(setpoint, CS.vEgo)
        if output_torque * setpoint > 0.0:
          output_torque *= get_ram_1500_transition_output_scale(setpoint, desired_lateral_jerk, CS.vEgo)
        if setpoint * desired_lateral_jerk < 0.0:
          output_torque *= get_ram_1500_unwind_output_scale(setpoint, desired_lateral_jerk, CS.vEgo)
      elif self.is_kona_non_scc:
        output_torque *= get_kona_non_scc_center_taper_scale(setpoint, CS.vEgo)
        rapid_reversal = setpoint * desired_lateral_jerk < 0.0
        if output_torque * setpoint > 0.0 or rapid_reversal:
          output_torque *= get_kona_non_scc_highway_transition_output_scale(setpoint, desired_lateral_jerk, CS.vEgo)
      elif self.is_kona_ev_2022:
        output_torque *= get_kona_ev_2022_center_output_scale(setpoint, CS.vEgo)
      elif rav4_tss2_active:
        output_torque *= get_rav4_tss2_center_output_scale(setpoint, CS.vEgo)
      elif rav4_prime_active:
        output_torque *= get_rav4_prime_output_taper_scale(setpoint, desired_lateral_jerk, CS.vEgo)
      elif sienna_4th_gen_active:
        output_torque *= get_sienna_4th_gen_center_taper_scale(setpoint, CS.vEgo)
        output_torque *= get_sienna_4th_gen_high_speed_output_taper_scale(CS.vEgo)
      elif toyota_highlander_tss2_active:
        output_torque *= get_toyota_highlander_tss2_output_taper_scale(setpoint, desired_lateral_jerk, CS.vEgo)
      elif toyota_corolla_tss2_active:
        output_torque *= get_toyota_corolla_tss2_center_output_scale(setpoint, CS.vEgo)
      elif prius_active:
        output_torque *= prius_center_taper
        prius_taper_max = (PRIUS_STANDARD_HIGH_SPEED_OUTPUT_TAPER_MAX if self.is_standard_prius
                           else PRIUS_HIGH_SPEED_OUTPUT_TAPER_MAX)
        output_torque *= get_prius_high_speed_output_taper_scale(setpoint, CS.vEgo, prius_taper_max)
      elif volt_standard_test_active:
        output_torque *= volt_standard_center_taper
      elif volt_plexy_test_active:
        output_torque *= volt_plexy_center_taper
      elif kia_ev6_active:
        output_torque *= kia_ev6_low_speed_center_taper
        output_torque *= get_kia_ev6_center_output_scale(setpoint, CS.vEgo)
      elif kia_carnival_active:
        output_torque *= kia_carnival_center_taper
        output_torque *= get_kia_carnival_unwind_output_scale(
          setpoint, measurement, desired_lateral_jerk, CS.vEgo,
          output_torque=output_torque if self.is_kia_carnival_hev and not CS.steeringPressed and not self.prev_steering_pressed else None,
        )
        output_torque *= get_kia_carnival_highway_transition_output_scale(setpoint, desired_lateral_jerk, CS.vEgo)
      elif palisade_active:
        output_torque *= get_palisade_center_output_scale(setpoint, CS.vEgo)
      elif tucson_4th_gen_active:
        output_torque *= tucson_4th_gen_center_taper
      elif genesis_g70_active:
        output_torque *= genesis_g70_center_output_taper
        output_torque *= get_genesis_g70_highway_turn_in_output_scale(
          output_torque, setpoint, measurement, desired_lateral_jerk, CS.vEgo,
        )
        output_torque *= get_genesis_g70_high_speed_error_scale(
          setpoint, measurement, desired_lateral_jerk, CS.vEgo,
        )
        output_torque *= get_genesis_g70_angle_output_scale(CS.steeringAngleDeg, output_torque)
        low_speed_output_limit = get_genesis_g70_low_speed_output_limit(setpoint, CS.vEgo)
        output_torque = float(np.clip(output_torque, -low_speed_output_limit, low_speed_output_limit))
        if not CS.steeringPressed:
          output_torque = get_genesis_g70_stabilized_output(
            output_torque, self.prev_output_torque, setpoint, measurement, desired_lateral_jerk, CS.vEgo, self.dt,
          )
      elif sonata_hybrid_active:
        output_torque *= sonata_hybrid_center_taper
        output_torque *= sonata_hybrid_center_output_taper
      elif self.is_silverado:
        output_torque *= silverado_center_taper
      elif kia_niro_phev_2022_active:
        output_torque *= kia_niro_phev_2022_center_taper
      elif kia_stinger_2022_active:
        output_torque *= kia_stinger_2022_center_taper
      elif self.is_civic_bosch_modified and civic_bosch_modified_a_lateral_testing_ground_active():
        output_torque *= civic_bosch_modified_a_center_taper
      # =========================================================================
      # ==================== Buick Envision (C4) self-constraint =================
      # 1) Slew-rate limit in normalized torque units. output_torque is -1..1,
      #    NOT Nm. GM CarController STEER_DELTA_UP/DOWN is much stricter, so this
      #    only smooths LatControl's internal prev_output_torque and logs; it does
      #    not change the final CAN command.
      # 2) Narrow center taper (0.18 m/s^2, min scale 0.80). Scales output and
      #    integrator by the same factor inside the straight-line band to filter
      #    surface chatter without delaying corner entry or dumping steady-state bias.
      # 3) Unwind damping: only when unwinding, driver holding wheel, and <90 km/h.
      # =========================================================================
      if self.is_envision:
        max_delta_torque = ENVISION_MAX_TORQUE_RATE * self.dt
        output_torque = float(np.clip(
          output_torque,
          self.prev_output_torque - max_delta_torque,
          self.prev_output_torque + max_delta_torque,
        ))

        if abs(setpoint) < ENVISION_CENTER_DEADBAND_LAT_ACCEL:
          center_scale = float(np.interp(
            abs(setpoint),
            [0.0, ENVISION_CENTER_DEADBAND_LAT_ACCEL],
            [ENVISION_CENTER_MIN_SCALE, 1.0],
          ))
          output_torque *= center_scale
          self.pid.i *= center_scale ** ENVISION_CENTER_I_DECAY_POWER

        if unwind_detected and CS.steeringPressed and CS.vEgo < ENVISION_UNWIND_MAX_SPEED_MPS:
          output_torque *= ENVISION_UNWIND_OUTPUT_SCALE
      # =========================================================================
      pid_log.active = True
      pid_log.p = float(self.pid.p)
      pid_log.i = float(self.pid.i)
      pid_log.d = float(self.pid.d)
      pid_log.f = float(self.pid.f)
      pid_log.output = float(-output_torque)  # TODO: log lat accel?
      pid_log.actualLateralAccel = float(measurement)
      pid_log.desiredLateralAccel = float(setpoint)
      pid_log.desiredLateralJerk = float(desired_lateral_jerk)
      self.starpilot_lateral_state.active = True
      self.starpilot_lateral_state.frictionThreshold = float(friction_threshold)
      self.starpilot_lateral_state.frictionScale = float(friction_scale)
      self.starpilot_lateral_state.feedforward = float(ff)
      self.starpilot_lateral_state.frictionJerk = float(friction_jerk)
      self.starpilot_lateral_state.frictionJerkDeadzone = float(friction_jerk_deadzone)
      self.starpilot_lateral_state.lowSpeedFactor = float(low_speed_factor)
      self.starpilot_lateral_state.unwindDetected = bool(unwind_detected)
      pid_log.saturated = bool(self._check_saturation(self.steer_max - abs(output_torque) < 1e-3, CS, steer_limited_by_safety, curvature_limited))
      self.prev_output_torque = float(output_torque)

      if DEBUG_TORQUE_TUNE and self.is_bolt:
        self.debug_counter += 1
        if self.debug_counter % 50 == 0:
          print(f"bolt_torque ff_scale={ff_scale:.3f} pos={self.torque_ff_scale_pos:.3f} "
                f"neg={self.torque_ff_scale_neg:.3f} deadzone_boost_active={deadzone_boost_active}")

    self.prev_steering_pressed = CS.steeringPressed

    # TODO left is positive in this convention
    return -output_torque, 0.0, pid_log
