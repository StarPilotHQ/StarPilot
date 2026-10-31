"""BluePilot lateral end to end, without a device: StarPilot's car-init hook sets the startup bits, the real Ford
CarController runs with BluePilot inputs, and every steering and LKA frame it sends goes through libsafety configured
with the same Ford param."""
import math

import pytest

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.ford.carcontroller import CarController
from opendbc.car.ford.carstate import CarState
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.values import CAR, DBC, FordFlags
from opendbc.bluepilot_lateral.core.carcontroller_ext import pinion_vehicle_model
from opendbc.bluepilot_lateral.core.values_ext import BP_LATERAL_PARAMS, FordSafetyFlagsSP
from opendbc.bluepilot_lateral.hosts.starpilot.lateral import FrameInputs
from opendbc.bluepilot_lateral.hosts.starpilot.safety_param import bp_safety_param, init_ford_safety_param_starpilot
from opendbc.bluepilot_lateral.params.manifest import value as manifest_value
from opendbc.bluepilot_lateral.params.param_store import ParamStore
from opendbc.safety.tests.libsafety import libsafety_py

SPEED = 20.0  # m/s: above the 10 m/s gate of the angle-mode deviation check
T_IDXS = [10.0 * (i / 32) ** 2 for i in range(33)]
INACTIVE_PATH_ANGLE = 1000
STARTUP = {
  "off": {"disable_BP_lat_UI": "1"},
  "on": {"disable_BP_lat_UI": "0"},
  "on+pinion": {"disable_BP_lat_UI": "0", "FordPrefSteerAngleCurvature": "1"},
  "pinion-only": {"disable_BP_lat_UI": "1", "FordPrefSteerAngleCurvature": "1"},
}


class StraightRoadInputs:
  """A road of constant curvature, refreshed every frame like StarPilot's BluePilotInputs."""
  def __init__(self, settings):
    self.settings = settings
    self.curvature = 0.0

  def update(self):
    frame = FrameInputs(params=[FrameInputs.Param(k, v) for k, v in self.settings.items()])
    li = frame.lateralInputs
    li.valid = True
    li.pathYawRate = [self.curvature * SPEED] * 33
    li.pathX = [SPEED * t for t in T_IDXS]
    li.pathY = [0.5 * self.curvature * (SPEED * t) ** 2 for t in T_IDXS]
    li.laneLineLeftX = li.laneLineRightX = list(li.pathX)
    li.laneLineLeftY = [y - 1.8 for y in li.pathY]
    li.laneLineRightY = [y + 1.8 for y in li.pathY]
    li.laneLineProbs = [0.1, 0.9, 0.9, 0.1]
    li.laneLineStds = [1.0, 0.1, 0.1, 1.0]
    li.steerRatio, li.stiffnessFactor, li.lateralDelay = 15.0, 1.0, 0.15
    return frame


def settings(startup, angle):
  defaults = {k: manifest_value(ParamStore([]), k) for k in BP_LATERAL_PARAMS}
  encoded = {k: (b"1" if v else b"0") if isinstance(v, bool) else str(v).encode() for k, v in defaults.items()}
  encoded["disable_BP_lat_UI"] = startup["disable_BP_lat_UI"].encode()
  encoded["FordPrefLateralControl"] = b"1" if angle else b"0"
  return encoded


def path_angle(address, data):
  return ((data[3] << 3) | (data[4] >> 5)) if address == 0x3D3 else (((data[3] & 0x1F) << 6) | (data[4] >> 2))


@pytest.mark.parametrize("car", (CAR.FORD_BRONCO_SPORT_MK1, CAR.FORD_F_150_MK14))
@pytest.mark.parametrize("startup", STARTUP)
@pytest.mark.parametrize("angle", (False, True), ids=("curvature", "angle"))
def test_every_steering_frame_passes_panda_safety(car, startup, angle):
  fp = gen_empty_fingerprint()
  fp[0][0x5A] = 8
  fp[2].update({0x3D6: 8, 0x186: 8})
  cp = CarInterface.get_params(car, fp, [], False, False, False)
  init_ford_safety_param_starpilot(cp, STARTUP[startup])
  bits = bp_safety_param(cp)
  lateral = startup in ("on", "on+pinion")
  assert bool(bits & FordSafetyFlagsSP.BP_LATERAL) == lateral
  assert bool(bits & FordSafetyFlagsSP.STEER_ANGLE_CURVATURE) == (startup == "on+pinion")

  controller = CarController(DBC[cp.carFingerprint], cp)
  inputs = StraightRoadInputs(settings(STARTUP[startup], angle))
  controller.bluepilot_inputs = inputs
  state = CarState(cp)
  state.update(state.get_can_parsers(cp))
  vm = pinion_vehicle_model(cp)

  safety, packer = libsafety_py.libsafety, CANPacker("ford_lincoln_base_pt")
  safety.init_tests()
  safety.set_alternative_experience(0)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.ford, int(cp.safetyConfigs[-1].safetyParam)) == 0
  safety.set_controls_allowed(True)

  def rx(name, values):
    address, data, bus = packer.make_can_msg(name, 0, values)
    assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data)), name

  sent, blocked, path_angles = 0, [], set()
  timer = 1_000_000
  try:
    for frame in range(200):
      curvature = 0.0015 * math.sin(frame / 60)
      inputs.curvature = curvature
      timer += 10_000
      safety.set_timer(timer)
      # the car tracks the command: yaw (or the pinion angle) measures the commanded curvature
      angle_deg = math.degrees(vm.get_steer_from_curvature(-curvature, SPEED, 0.0))
      if frame % 2 == 0:
        rx("BrakeSysFeatures", {"Veh_V_ActlBrk": SPEED * 3.6, "VehVActlBrk_D_Qf": 3, "VehVActlBrk_No_Cnt": (frame // 2) % 16})
        rx("EngVehicleSpThrottle2", {"Veh_V_ActlEng": SPEED * 3.6, "VehVActlEng_D_Qf": 3})
        rx("DesiredTorqBrk", {"VehStop_D_Stat": 0})
      rx("Yaw_Data_FD1", {"VehYaw_W_Actl": -curvature * SPEED, "VehYawWActl_D_Qf": 3, "VehRollYaw_No_Cnt": frame % 256})
      rx("EngVehicleSpThrottle", {"ApedPos_Pc_ActlArb": 0})
      if frame % 10 == 0:
        rx("EngBrakeData", {"BpedDrvAppl_D_Actl": 1, "CcStat_D_Actl": 5})
        rx("Steering_Data_FD1", {})
      if bits & FordSafetyFlagsSP.STEER_ANGLE_CURVATURE:
        rx("SteeringPinion_Data", {"StePinComp_An_Est": angle_deg, "StePinCompAnEst_D_Qf": 3, "StePinAn_No_Cnt": frame % 16})

      cs = structs.CarState(vEgo=SPEED, vEgoRaw=SPEED, yawRate=-curvature * SPEED, steeringAngleDeg=angle_deg,
                            canValid=True, gearShifter=structs.CarState.GearShifter.drive)
      cs.cruiseState.available = cs.cruiseState.enabled = True
      state.out = cs.as_reader()
      cc = structs.CarControl(enabled=True, latActive=True)
      cc.actuators.curvature = curvature
      _, sends = controller.update(cc.as_reader(), state, timer * 1000)
      for address, data, bus in sends:
        if address in (0x3CA, 0x3D3, 0x3D6):
          sent += 1
          if address != 0x3CA:
            path_angles.add(path_angle(address, data))
          if not safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)):
            blocked.append((frame, hex(address), data.hex()))
  finally:
    safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  assert sent > 100 and not blocked, blocked[:5]
  assert controller.bp.disable_BP_lat_UI == (not lateral)
  # angle mode steers through the path angle; the stock path and curvature mode at this curvature don't
  assert (path_angles != {INACTIVE_PATH_ANGLE}) == (lateral and angle), sorted(path_angles)[:5]
  assert bool(cp.flags & FordFlags.CANFD) == (car == CAR.FORD_F_150_MK14)
