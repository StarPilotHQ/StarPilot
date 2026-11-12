import math

import numpy as np
from opendbc.car import ACCEL_MAX_TAKEOFF

from opendbc.car import ACCELERATION_DUE_TO_GRAVITY, structs
from opendbc.car.honda.values import CAR, HondaFlags

LongCtrlState = structs.CarControl.Actuators.LongControlState
BOSCH_BRAKE_FORCE_ON = -0.12
BOSCH_BRAKE_FORCE_RELEASE = -0.02

CLASSIC_BOSCH_CARS = frozenset((
  CAR.HONDA_NBOX_2G,
  CAR.HONDA_ACCORD,
  CAR.HONDA_CIVIC_BOSCH,
  CAR.HONDA_CIVIC_BOSCH_DIESEL,
  CAR.HONDA_CIVIC_2022,
  CAR.HONDA_CRV_5G,
  CAR.HONDA_CRV_HYBRID,
  CAR.HONDA_HRV_3G,
  CAR.HONDA_CITY_7G,
  CAR.ACURA_RDX_3G,
  CAR.ACURA_RDX_3G_MMR,
  CAR.HONDA_INSIGHT,
  CAR.HONDA_E,
  CAR.HONDA_E_ADVANCE,
  CAR.ACURA_MDX_4G,
  CAR.HONDA_ODYSSEY_5G_MMR,
  CAR.ACURA_TLX_2G,
  CAR.HONDA_FIT_4G,
  CAR.ACURA_INTEGRA,
  CAR.ACURA_ADX,
))
HONDA_BOSCH_RADARLESS = frozenset((
  CAR.HONDA_CIVIC_2022,
  CAR.HONDA_HRV_3G,
  CAR.HONDA_CITY_7G,
  CAR.HONDA_FIT_4G,
  CAR.ACURA_INTEGRA,
  CAR.ACURA_ADX,
))

def update_honda_bosch_braking(braking: bool, gas_pedal_force: float, stopping: bool, long_active: bool) -> bool:
  if not long_active:
    return False
  if stopping:
    return True
  if braking:
    return gas_pedal_force <= BOSCH_BRAKE_FORCE_RELEASE
  return gas_pedal_force < BOSCH_BRAKE_FORCE_ON

def get_honda_bosch_wind_brake_mps2(v_ego: float) -> float:
  return float(np.interp(v_ego, [0.0, 13.4, 22.4, 31.3, 40.2], [0.0, 0.049, 0.136, 0.267, 0.441]))

def qualified(cp):
  return (cp.brand == "honda" and cp.carFingerprint in CLASSIC_BOSCH_CARS and cp.flags & HondaFlags.BOSCH and
          not cp.flags & HondaFlags.BOSCH_CANFD and cp.openpilotLongitudinalControl and
          not cp.passive and not cp.dashcamOnly and not cp.notCar and
          bool(cp.flags & HondaFlags.BOSCH_RADARLESS) == (cp.carFingerprint in HONDA_BOSCH_RADARLESS))


class BoschLongitudinal:
  def __init__(self, cp, params):
    if not qualified(cp):
      raise ValueError("Honda road load requires its admitted classic Bosch profile")
    self.CP = cp
    self.params = params
    self.pitch = 0.0
    self.accel = 0.0
    self.gas = 0.0
    self.bosch_braking = False
    self.bosch_last_gas = 0.0
    self.set_factors(1.0, 1.0)

  def set_factors(self, gas, wind):
    self.bosch_gas_factor = float(gas) if math.isfinite(gas) and 0.01 <= gas <= 3.0 else 1.0
    self.bosch_wind_factor = float(wind) if math.isfinite(wind) and 0.1 <= wind <= 5.0 else 1.0
    self.bosch_wind_factor_before_brake = self.bosch_wind_factor
    self.bosch_gas_factor_before_gasmax = self.bosch_gas_factor
    self.bosch_wind_factor_before_gasmax = self.bosch_wind_factor

  def observe_orientation(self, orientation):
    if len(orientation) == 3 and math.isfinite(orientation[1]):
      self.pitch = orientation[1]

  def update(self, CC, CS, accel):
    actuators = CC.actuators
    hill_brake = math.sin(self.pitch) * ACCELERATION_DUE_TO_GRAVITY
    wind_brake_mps2 = get_honda_bosch_wind_brake_mps2(CS.out.vEgo)
    min_gas = self.params.BOSCH_GAS_LOOKUP_BP[0]
    self.accel = float(np.clip(accel, self.params.BOSCH_ACCEL_MIN, ACCEL_MAX_TAKEOFF))
    gas_pedal_force = self.accel + hill_brake
    if self.CP.carFingerprint not in HONDA_BOSCH_RADARLESS:
      gas_pedal_force += wind_brake_mps2 * self.bosch_wind_factor
      if actuators.longControlState == LongCtrlState.pid and (not CS.out.gasPressed):
        gas_error = self.accel - CS.out.aEgo
        if gas_error != 0.0 and gas_pedal_force > min_gas:
          if self.CP.carFingerprint == CAR.HONDA_INSIGHT:
            gas_learn_speed = 150.0
          elif self.CP.carFingerprint in (CAR.ACURA_RDX_3G, CAR.ACURA_RDX_3G_MMR):
            gas_learn_speed = 300.0
          else:
            gas_learn_speed = 50.0
          gas_learn_force = gas_pedal_force
          self.bosch_gas_factor = float(np.clip(self.bosch_gas_factor + gas_error / gas_learn_speed * gas_learn_force, 0.1, 3.0))
        if gas_error != 0.0 and (not CS.out.brakePressed) and (CS.out.vEgo > 0.0):
          wind_learn_speed = 100.0 if self.CP.carFingerprint in (CAR.ACURA_RDX_3G, CAR.ACURA_RDX_3G_MMR) else 1000.0
          wind_adjust = 1.0 + wind_brake_mps2 / wind_learn_speed
          if gas_error > 0.0:
            self.bosch_wind_factor = float(np.clip(self.bosch_wind_factor * wind_adjust, 0.1, 3.0))
          else:
            self.bosch_wind_factor = float(np.clip(self.bosch_wind_factor / wind_adjust, 0.1, 3.0))
        if gas_pedal_force <= 0.0:
          self.bosch_wind_factor = max(self.bosch_wind_factor, self.bosch_wind_factor_before_brake)
        else:
          self.bosch_wind_factor_before_brake = self.bosch_wind_factor
        if gas_pedal_force >= ACCEL_MAX_TAKEOFF:
          self.bosch_gas_factor = min(self.bosch_gas_factor, self.bosch_gas_factor_before_gasmax)
          self.bosch_wind_factor = min(self.bosch_wind_factor, self.bosch_wind_factor_before_gasmax)
        else:
          self.bosch_gas_factor_before_gasmax = self.bosch_gas_factor
          self.bosch_wind_factor_before_gasmax = self.bosch_wind_factor
    gas_lookup_input = gas_pedal_force * self.bosch_gas_factor
    self.gas = float(np.interp(gas_lookup_input, self.params.BOSCH_GAS_LOOKUP_BP, self.params.BOSCH_GAS_LOOKUP_V))
    self.gas = min(self.gas, max(60.0, self.bosch_last_gas + 60.0))
    self.bosch_last_gas = self.gas
    stopping = actuators.longControlState == LongCtrlState.stopping
    self.bosch_braking = update_honda_bosch_braking(self.bosch_braking, gas_pedal_force, stopping, CC.longActive)
    return self.accel, self.gas, gas_pedal_force, self.bosch_braking
