"""Representable Ford path-angle commands across steering directions and buses."""
from types import SimpleNamespace

import pytest

from opendbc.can import CANPacker, CANParser
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.ford.fordcan import CanBus
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.tests.test_bluepilot_lateral import STARTUP, StraightRoadInputs, settings
from opendbc.car.ford.values import CAR, FordFlags
from opendbc.bluepilot_lateral.hosts.starpilot.safety_param import init_ford_safety_param_starpilot
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.car.ford.bluepilot import FordBluePilotController
from openpilot.starpilot.car.ford.controller_selection import FordController


@pytest.mark.parametrize('car', [CAR.FORD_ESCAPE_MK4, CAR.FORD_F_150_MK14, CAR.FORD_MUSTANG_MACH_E_MK1])
@pytest.mark.parametrize('sign', [-1., 1.])
@pytest.mark.parametrize('pinion', [False, True])
@pytest.mark.parametrize('approach', [.449, .469])
def test_path_angle_near_encoding_bounds_keeps_direction_and_passes_native(car, sign, pinion, approach):
  cp = CarInterface.get_params(car, gen_empty_fingerprint(), [], False, False, False)
  startup = STARTUP['on+pinion' if pinion else 'on']
  init_ford_safety_param_starpilot(cp, startup)
  packer, can = CANPacker('ford_lincoln_base_pt'), CanBus(cp)
  source = SimpleNamespace(controller_choice=FordController.STARPILOT, current_inputs_valid=True)
  host = FordBluePilotController(cp, packer, can, source)
  road = StraightRoadInputs(settings(startup, True))
  speed = 5.
  cc = structs.CarControl(latActive=True)
  cs = SimpleNamespace(out=structs.CarState(vEgo=speed, vEgoRaw=speed, canValid=True), lat_ctl_lim_stat=0)
  message = 'LateralMotionControl2' if cp.flags & FordFlags.CANFD else 'LateralMotionControl'
  parser = CANParser('ford_lincoln_base_pt', [(message, 0)], can.main)
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(0)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.ford, int(cp.safetyConfigs[-1].safetyParam)) == 0
  safety.set_controls_allowed(True)
  wire_angles = []
  try:
    for frame in range(200):
      safety.set_timer(1_000_000 + frame * 10_000)
      messages: list[tuple[str, dict[str, float]]] = [
        ('BrakeSysFeatures', {'Veh_V_ActlBrk': speed * 3.6, 'VehVActlBrk_D_Qf': 3, 'VehVActlBrk_No_Cnt': (frame // 2) % 16}),
        ('EngVehicleSpThrottle2', {'Veh_V_ActlEng': speed * 3.6, 'VehVActlEng_D_Qf': 3}),
        ('Yaw_Data_FD1', {'VehYaw_W_Actl': 0., 'VehYawWActl_D_Qf': 3, 'VehRollYaw_No_Cnt': frame % 256}),
        ('DesiredTorqBrk', {'VehStop_D_Stat': 0}),
        ('EngVehicleSpThrottle', {'ApedPos_Pc_ActlArb': 0}),
        ('EngBrakeData', {'BpedDrvAppl_D_Actl': 1, 'CcStat_D_Actl': 5}),
      ]
      if pinion:
        messages.append(('SteeringPinion_Data', {'StePinComp_An_Est': 0., 'StePinCompAnEst_D_Qf': 3, 'StePinAn_No_Cnt': frame % 16}))
      for name, fields in messages:
        if name in ('BrakeSysFeatures', 'EngVehicleSpThrottle2') and frame % 2:
          continue
        assert fields.keys() <= packer.dbc.name_to_msg[name].sigs.keys()
        address, data, bus = packer.make_can_msg(name, can.main, fields)
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
      # Settle just below the saturation boundary, then request a stronger turn.
      curvature = sign * (approach if frame < 125 else .8) / (speed * 1.3)
      cc.actuators.curvature = curvature
      inputs = road.update()
      inputs.lateralInputs.pathYawRate = [curvature * speed] * 33
      host.update(inputs, frame)
      cc.latActive = frame >= 15
      packets = []
      if frame % 5 == 0:
        lateral, _ = host.step_lateral(cc, cs, cc.actuators, 0.)
        packets.extend(lateral)
        for address, data, bus in lateral:
          parser.update([(1_000_000_000 + frame * 10_000_000, [(address, data, bus)])])
          wire = parser.vl[message]['LatCtlPath_An_Actl']
          assert -.5 <= wire <= 2047 * .0005 - .5
          assert wire == pytest.approx(-host.angle.bp_path_angle_final, abs=.00025)
          if cc.latActive:
            assert wire * sign <= 0.
            wire_angles.append(wire)
      if frame % 3 == 0:
        packets.append(host.step_lka(cc, cc.hudControl))
      for address, data, bus in packets:
        assert safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)), (frame, hex(address), data.hex())
    assert max(abs(value) for value in wire_angles) >= approach - .00025
    assert max(abs(b - a) for a, b in zip(wire_angles[:-1], wire_angles[1:], strict=True)) <= .0555
  finally:
    safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
