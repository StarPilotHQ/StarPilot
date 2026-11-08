"""StarPilot demand uses the native pinion envelope before path-angle conversion."""
import math

import pytest

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.ford.carcontroller import CarController
from opendbc.car.ford.carstate import CarState
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.tests.test_bluepilot_lateral import StraightRoadInputs, STARTUP, settings
from opendbc.car.ford.values import CAR, DBC, CarControllerParams
from opendbc.bluepilot_lateral.hosts.starpilot.safety_param import init_ford_safety_param_starpilot
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.car.ford.bluepilot import FordBluePilotController
from openpilot.starpilot.car.ford.controller_selection import FordController


class LearnedGeometryInputs(StraightRoadInputs):
  controller_choice = FordController.STARPILOT
  current_inputs_valid = True

  def __init__(self, cp, ratio, roll):
    super().__init__(settings(STARTUP['on+pinion'], True))
    self.cp, self.ratio, self.roll = cp, ratio, roll

  def update(self):
    frame = super().update()
    frame.lateralInputs.steerRatio = self.cp.steerRatio * self.ratio
    frame.lateralInputs.roll = self.roll
    return frame


@pytest.mark.parametrize('car', [CAR.FORD_MUSTANG_MACH_E_MK1, CAR.FORD_EXPLORER_MK6, CAR.FORD_F_150_MK14])
@pytest.mark.parametrize('speed', [15., 30.])
@pytest.mark.parametrize('sign', [-1., 1.])
@pytest.mark.parametrize('ratio, refused', [(.85, False), (.5, True)])
def test_pinion_guard_preserves_host_and_native_bounds(car, speed, sign, ratio, refused):
  fp = gen_empty_fingerprint()
  fp[0][0x5A] = 8
  fp[2].update({0x3D6: 8, 0x186: 8})
  cp = CarInterface.get_params(car, fp, [], False, False, False)
  init_ford_safety_param_starpilot(cp, STARTUP['on+pinion'])
  controller = CarController(DBC[cp.carFingerprint], cp)
  inputs = LearnedGeometryInputs(cp, ratio, -sign * .04)
  controller.bluepilot_inputs = inputs
  controller.bp = FordBluePilotController(cp, controller.packer, controller.CAN, inputs)
  state = CarState(cp)
  state.update(state.get_can_parsers(cp))
  safety, packer = libsafety_py.libsafety, CANPacker('ford_lincoln_base_pt')
  safety.init_tests()
  safety.set_alternative_experience(0)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.ford, cp.safetyConfigs[-1].safetyParam) == 0
  safety.set_controls_allowed(True)
  blocked, sent = [], 0
  try:
    for frame in range(100):
      safety.set_timer(1_000_000 + frame * 10_000)
      frames = []
      def rx(name, values, frames=frames):
        address, data, bus = packer.make_can_msg(name, 0, values)
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
        frames.append((address, data, bus))
      if frame % 2 == 0:
        rx('BrakeSysFeatures', {'Veh_V_ActlBrk': speed * 3.6, 'VehVActlBrk_D_Qf': 3,
                                'VehVActlBrk_No_Cnt': frame // 2 % 16})
        rx('EngVehicleSpThrottle2', {'Veh_V_ActlEng': speed * 3.6, 'VehVActlEng_D_Qf': 3})
        rx('DesiredTorqBrk', {'VehStop_D_Stat': 0})
      rx('Yaw_Data_FD1', {'VehYaw_W_Actl': 0., 'VehYawWActl_D_Qf': 3, 'VehRollYaw_No_Cnt': frame % 256})
      rx('EngVehicleSpThrottle', {'ApedPos_Pc_ActlArb': 0})
      if frame % 10 == 0:
        rx('EngBrakeData', {'BpedDrvAppl_D_Actl': 1, 'CcStat_D_Actl': 5})
        rx('Steering_Data_FD1', {})
      rx('SteeringPinion_Data', {'StePinComp_An_Est': sign * 30., 'StePinCompAnEst_D_Qf': 3,
                                'StePinAn_No_Cnt': frame % 16})
      controller.bp.observe_can([(0, frames)])
      if frame >= 6 and hasattr(controller.bp, 'native_pinion_model'):
        assert min(controller.bp.measured_history) == safety.get_curvature_meas_min()
        assert max(controller.bp.measured_history) == safety.get_curvature_meas_max()
      cs = structs.CarState(vEgo=speed, vEgoRaw=speed, steeringAngleDeg=sign * 30., canValid=True,
                            gearShifter=structs.CarState.GearShifter.drive)
      cs.cruiseState.available = cs.cruiseState.enabled = True
      state.out = cs.as_reader()
      cc = structs.CarControl(enabled=True, latActive=True)
      cc.actuators.curvature = -sign * .02
      _, packets = controller.update(cc.as_reader(), state, (1_000_000 + frame * 10_000) * 1000)
      for address, data, bus in packets:
        if address in (0x3CA, 0x3D3, 0x3D6):
          sent += 1
          if not safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)):
            blocked.append((frame, hex(address)))
    assert sent > 40
    if refused:
      assert blocked
      assert controller.bp.angle.native_curvature_bounds is None
    else:
      assert not blocked, blocked
      bounds = controller.bp.angle.native_curvature_bounds
      assert bounds is not None
      low, high = bounds
      assert low <= controller.bp.angle.bp_kappa_cmd <= high
      measured = controller.bp.inputs.get_current_curvature(state)
      assert abs(controller.bp.angle.bp_kappa_cmd - measured) <= CarControllerParams.CURVATURE_ERROR + 1e-12
    assert math.isfinite(controller.bp.angle.bp_path_angle_final)
  finally:
    safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)


@pytest.mark.parametrize('car', [CAR.FORD_MUSTANG_MACH_E_MK1, CAR.FORD_EXPLORER_MK6, CAR.FORD_F_150_MK14])
@pytest.mark.parametrize('speed', [15., 30.])
@pytest.mark.parametrize('sign', [-1., 1.])
@pytest.mark.parametrize('drifting,native_ahead', [(False, False), (True, False), (True, True)])
def test_yaw_guard_keeps_encoded_shadow_inside_native_measurements(car, speed, sign, drifting, native_ahead):
  from types import SimpleNamespace
  from openpilot.starpilot.controller_extensions import order_controller_can

  class YawInputs(StraightRoadInputs):
    controller_choice = FordController.STARPILOT
    current_inputs_valid = True

  cp = CarInterface.get_params(car, gen_empty_fingerprint(), [], False, False, False)
  init_ford_safety_param_starpilot(cp, STARTUP['on'])
  controller = CarController(DBC[cp.carFingerprint], cp)
  inputs = YawInputs(settings(STARTUP['on'], True))
  inputs.curvature = sign * .02
  controller.bluepilot_inputs = inputs
  controller.bp = FordBluePilotController(cp, controller.packer, controller.CAN, inputs)
  state = CarState(cp)
  state.update(state.get_can_parsers(cp))
  safety, packer = libsafety_py.libsafety, CANPacker('ford_lincoln_base_pt')
  safety.init_tests()
  safety.set_alternative_experience(0)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.ford, cp.safetyConfigs[-1].safetyParam) == 0
  safety.set_controls_allowed(True)
  sent, yaw_counter = 0, 0
  delayed = []
  try:
    for frame in range(70):
      safety.set_timer(1_000_000 + frame * 10_000)
      received, delayed = delayed, []

      def rx(name, values, received=received):
        address, data, bus = packer.make_can_msg(name, 0, values)
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
        received.append((address, data, bus))

      if frame % 2 == 0:
        rx('BrakeSysFeatures', {'Veh_V_ActlBrk': speed * 3.6, 'VehVActlBrk_D_Qf': 3,
                                'VehVActlBrk_No_Cnt': frame // 2 % 16})
        rx('EngVehicleSpThrottle2', {'Veh_V_ActlEng': speed * 3.6, 'VehVActlEng_D_Qf': 3})
        rx('DesiredTorqBrk', {'VehStop_D_Stat': 0})
      native_curvature = .004 - min(20, max(0, frame - 29)) * .0002 if drifting else .004
      rx('Yaw_Data_FD1', {'VehYaw_W_Actl': -sign * native_curvature * speed, 'VehYawWActl_D_Qf': 3,
                         'VehRollYaw_No_Cnt': yaw_counter % 256})
      yaw_counter += 1
      rx('EngVehicleSpThrottle', {'ApedPos_Pc_ActlArb': 0})
      if frame % 10 == 0:
        rx('EngBrakeData', {'BpedDrvAppl_D_Actl': 1, 'CcStat_D_Actl': 5})
        rx('Steering_Data_FD1', {})
      controller.bp.observe_can([(0, received)])
      host_curvature = .004 - min(20, max(0, frame - 30)) * .0002 if drifting else .00408
      cs = structs.CarState(vEgo=speed, vEgoRaw=speed, canValid=True, yawRate=-sign * host_curvature * speed)
      cs.cruiseState.available = cs.cruiseState.enabled = True
      state.out = cs.as_reader()
      cc = structs.CarControl(enabled=True, latActive=frame >= 15)
      cc.actuators.curvature = sign * .02
      _, original = controller.update(cc.as_reader(), state, (1_000_000 + frame * 10_000) * 1000)
      packets = order_controller_can(SimpleNamespace(CP=cp, CC=controller), original)
      if native_ahead and frame == 35:
        address, data, bus = packer.make_can_msg('Yaw_Data_FD1', 0, {
          'VehYaw_W_Actl': -sign * (native_curvature - .0002) * speed,
          'VehYawWActl_D_Qf': 3, 'VehRollYaw_No_Cnt': yaw_counter % 256,
        })
        yaw_counter += 1
        safety.set_timer(1_000_000 + frame * 10_000 + 1000)
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
        delayed.append((address, data, bus))
      for address, data, bus in packets:
        if address in (0x3CA, 0x3D3, 0x3D6):
          sent += 1
          assert safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)), (frame, hex(address))
    assert sent > 30
    bounds = controller.bp.angle.native_curvature_bounds
    assert bounds is not None
    assert bounds[0] <= controller.bp.angle.bp_kappa_cmd <= bounds[1]
  finally:
    safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
