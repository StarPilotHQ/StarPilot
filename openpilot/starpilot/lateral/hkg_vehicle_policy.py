import numpy as np

from opendbc.car.hyundai.values import CAR, HyundaiFlags

from openpilot.starpilot.lateral import hkg_vehicle_shaping as shaping

from openpilot.starpilot.lateral.hkg_torque_policy import HKGShapedTorquePolicy, supported_torque_cp

PROFILES = {
  'ioniq_5': (frozenset((CAR.HYUNDAI_IONIQ_5,)), shaping.IONIQ_5_BASE_LAT_ACCEL_FACTOR_MULT),
  'ioniq_ev_old': (
    frozenset(
      (
        CAR.HYUNDAI_IONIQ_EV_LTD,
        CAR.HYUNDAI_IONIQ_EV_2020,
      )
    ),
    shaping.IONIQ_EV_OLD_BASE_LAT_ACCEL_FACTOR_MULT,
  ),
  'sonata': (frozenset((CAR.HYUNDAI_SONATA,)), 1),
  'elantra_non_scc': (
    frozenset(
      (
        CAR.HYUNDAI_ELANTRA_2022_NON_SCC,
        CAR.HYUNDAI_ELANTRA_HEV_2022_NON_SCC,
      )
    ),
    1,
  ),
  'kia_xceed': (frozenset((CAR.KIA_XCEED_PHEV,)), 1),
  'kia_niro_ev': (frozenset((CAR.KIA_NIRO_EV,)), 1),
  'kia_niro_phev_2022': (frozenset((CAR.KIA_NIRO_PHEV_2022,)), 1),
  'kia_stinger_2022': (frozenset((CAR.KIA_STINGER_2022,)), 1),
  'kia_forte': (
    frozenset(
      (
        CAR.KIA_FORTE,
        CAR.KIA_FORTE_2019_NON_SCC,
        CAR.KIA_FORTE_2021_NON_SCC,
      )
    ),
    shaping.KIA_FORTE_BASE_LAT_ACCEL_FACTOR_MULT,
  ),
  'kia_ev6': (frozenset((CAR.KIA_EV6,)), 1),
  'kia_carnival': (
    frozenset(
      (
        CAR.KIA_CARNIVAL_2025,
        CAR.KIA_CARNIVAL_HEV_4TH_GEN,
      )
    ),
    1,
  ),
  'tucson_4th_gen': (frozenset((CAR.HYUNDAI_TUCSON_4TH_GEN,)), 1),
  'kona_non_scc': (frozenset((CAR.HYUNDAI_KONA_NON_SCC,)), 1),
  'kona_ev_2022': (frozenset((CAR.HYUNDAI_KONA_EV_2022,)), 1),
}


def policy_for(cp):
  return next((key for key, (cars, _) in PROFILES.items() if supported_torque_cp(cp, cars)), None)


class HKGVehicleTorquePolicy(HKGShapedTorquePolicy):
  def __init__(self, parent, cp):
    self.profile = policy_for(cp)
    if self.profile is None:
      raise ValueError("Unsupported HKG vehicle torque profile")
    self.FACTOR_MULT = PROFILES[self.profile][1]
    self.canfd = bool(cp.flags & HyundaiFlags.CANFD)
    self.carnival_hev = cp.carFingerprint == CAR.KIA_CARNIVAL_HEV_4TH_GEN
    self.requested_curvature = 0.0
    self.future_lateral_accel = 0.0
    self.steering_pressed = False
    self.hev_center_weight = 0.0
    super().__init__(parent, cp)

  supported_cp = staticmethod(lambda cp: policy_for(cp) is not None)

  def update(self, active, cs, vm, params, safety_limited, curvature, curvature_limited, delay):
    self.requested_curvature = curvature
    self.future_lateral_accel = curvature * cs.vEgo ** 2
    self.steering_pressed = bool(cs.steeringPressed)
    return super().update(active, cs, vm, params, safety_limited, curvature, curvature_limited, delay)

  def feedforward_context(self, value, setpoint, jerk, measurement, speed):
    if self.profile == 'ioniq_5':
      return value * shaping.get_ioniq_5_ff_scale(setpoint, jerk, speed) * shaping.get_ioniq_5_center_taper_scale(setpoint, speed)
    if self.profile == 'ioniq_ev_old':
      return value * shaping.get_ioniq_ev_old_ff_scale(setpoint, jerk, speed) * shaping.get_ioniq_ev_old_center_taper_scale(setpoint, speed)
    if self.profile == 'sonata':
      return value * shaping.get_sonata_ff_scale(setpoint, jerk, speed) * shaping.get_sonata_center_taper_scale(setpoint, speed)
    if self.profile == 'elantra_non_scc':
      return value * shaping.get_elantra_non_scc_ff_scale(setpoint, jerk, speed)
    if self.profile == 'kia_xceed':
      return value * shaping.get_kia_xceed_ff_scale(setpoint, jerk, speed) * shaping.get_kia_xceed_center_taper_scale(setpoint, speed)
    if self.profile == 'kia_forte':
      return value * shaping.get_kia_forte_ff_scale(setpoint, jerk, speed) * shaping.get_kia_forte_center_taper_scale(setpoint, speed)
    if self.profile == 'kia_ev6':
      return value * shaping.get_kia_ev6_ff_scale(setpoint, jerk, speed) * shaping.get_kia_ev6_center_taper_scale(setpoint, speed)
    if self.profile == 'kia_carnival':
      return value * shaping.get_kia_carnival_unwind_ff_scale(setpoint, measurement, jerk, speed)
    return value

  def friction_context(self, setpoint, jerk, measurement, speed):
    base = shaping.get_hkg_canfd_base_friction_threshold(speed) if self.canfd else shaping.get_standard_friction_threshold(speed)
    if self.profile == 'ioniq_5':
      return shaping.get_ioniq_5_friction_threshold(speed, setpoint, jerk), 1 + (
        shaping.get_ioniq_5_friction_scale(speed, setpoint, jerk) - 1
      ) * shaping.get_ioniq_5_center_taper_scale(setpoint, speed)
    if self.profile == 'kia_niro_ev' and not self.steering_pressed:
      return shaping.get_kia_niro_ev_friction_threshold(speed, setpoint, jerk, self.future_lateral_accel), 1
    if self.profile == 'kia_niro_phev_2022':
      return shaping.get_kia_niro_phev_2022_friction_threshold(speed, setpoint, jerk), 1
    if self.profile == 'kia_stinger_2022':
      return shaping.get_kia_stinger_2022_friction_threshold(
        speed, setpoint, jerk, self.future_lateral_accel, self.steering_pressed or self.previous_pressed), 1
    if self.profile == 'kia_forte':
      return shaping.get_kia_forte_friction_threshold(speed, setpoint, jerk), 1
    if self.profile == 'kia_ev6':
      return shaping.get_kia_ev6_friction_threshold(speed, setpoint, jerk), 1 + (
        shaping.get_kia_ev6_friction_scale(speed, setpoint, jerk) - 1
      ) * shaping.get_kia_ev6_center_taper_scale(setpoint, speed)
    if self.profile == 'kia_carnival':
      self.hev_center_weight = (shaping.get_kia_carnival_hev_center_weight(
        speed, setpoint, measurement, jerk, self.future_lateral_accel)
        if self.carnival_hev and not self.steering_pressed and not self.previous_pressed else 0.0)
      if not self.steering_pressed and not self.previous_pressed:
        self.hev_center_weight = max(self.hev_center_weight,
          shaping.get_kia_carnival_crawl_center_weight(speed, setpoint, self.future_lateral_accel))
      threshold = shaping.get_kia_carnival_friction_threshold(speed, setpoint, jerk)
      return (threshold + shaping.KIA_CARNIVAL_HEV_CENTER_FRICTION_INCREMENT * self.hev_center_weight,
              shaping.get_kia_carnival_friction_center_fade_scale(setpoint, speed))
    if self.profile == 'tucson_4th_gen':
      return shaping.get_tucson_4th_gen_friction_threshold(speed, setpoint, jerk), 1
    if self.profile == 'kona_non_scc':
      return shaping.get_kona_non_scc_friction_threshold(speed, setpoint, jerk), 1
    if self.profile == 'kona_ev_2022':
      return shaping.get_kona_ev_2022_friction_threshold(speed, setpoint), 1
    return (base, 1.0)

  def pid_error_context(self, error):
    return error * (1.0 - shaping.KIA_CARNIVAL_HEV_CENTER_ERROR_REDUCTION * self.hev_center_weight)

  def jerk_deadzone(self, setpoint, jerk, measurement, speed):
    if self.profile == 'ioniq_5':
      return shaping.get_ioniq_5_friction_jerk_deadzone(speed, setpoint)
    if self.profile == 'kia_carnival':
      return shaping.get_kia_carnival_friction_jerk_deadzone(speed, setpoint, jerk)
    return 0.0

  def output_context(self, value, setpoint, jerk, measurement, speed):
    if self.profile == 'ioniq_5':
      limit = shaping.get_ioniq_5_low_speed_output_limit(
        setpoint, jerk, speed, self.requested_curvature if not self.steering_pressed and not self.previous_pressed else 0.0, value)
      return float(np.clip(value, -limit, limit))
    if self.profile == 'kia_niro_phev_2022':
      return value * shaping.get_kia_niro_phev_2022_center_taper_scale(setpoint, speed)
    if self.profile == 'kia_stinger_2022':
      return value * shaping.get_kia_stinger_2022_center_taper_scale(setpoint, speed)
    if self.profile == 'kia_ev6':
      return (value * shaping.get_kia_ev6_low_speed_center_taper_scale(setpoint, speed) *
              shaping.get_kia_ev6_center_output_scale(setpoint, speed, self.future_lateral_accel))
    if self.profile == 'kia_carnival':
      return (
        value
        * shaping.get_kia_carnival_center_taper_scale(setpoint, speed)
        * shaping.get_kia_carnival_unwind_output_scale(
          setpoint, measurement, jerk, speed,
          output_torque=value if self.carnival_hev and not self.steering_pressed and not self.previous_pressed else None)
        * shaping.get_kia_carnival_highway_transition_output_scale(setpoint, jerk, speed)
      )
    if self.profile == 'tucson_4th_gen':
      return value * shaping.get_tucson_4th_gen_center_taper_scale(setpoint, speed)
    if self.profile == 'kona_non_scc':
      value *= shaping.get_kona_non_scc_center_taper_scale(setpoint, speed)
      if value * setpoint > 0 or setpoint * jerk < 0:
        value *= shaping.get_kona_non_scc_highway_transition_output_scale(setpoint, jerk, speed)
      return value
    if self.profile == 'kona_ev_2022':
      return value * shaping.get_kona_ev_2022_center_output_scale(setpoint, speed)
    return value
