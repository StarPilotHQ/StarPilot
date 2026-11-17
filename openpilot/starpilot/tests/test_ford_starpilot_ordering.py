"""Current-cycle StarPilot metadata precedes steering without changing CAN traffic."""

from collections import Counter
from types import SimpleNamespace as NS
from typing import Any, cast

import pytest

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.ford.carcontroller import CarController
from opendbc.car.ford.carstate import CarState
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.tests.test_bluepilot_lateral import STARTUP
from opendbc.car.ford.values import CAR, DBC, FordFlags
from opendbc.bluepilot_lateral.hosts.starpilot.safety_param import init_ford_safety_param_starpilot
from opendbc.bluepilot_lateral.core.lateral_curv_ext import PrimaryLateralControl
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.car.ford.bluepilot import FordBluePilotController
from openpilot.starpilot.car.ford.controller_selection import FordController
from openpilot.starpilot.controller_extensions import order_controller_can
from openpilot.starpilot.tests.test_ford_native_pinion_guard import LearnedGeometryInputs


def native_run(identity, direction, *, ordered, invalid_shadow=False, withdraw=False):
  fp = gen_empty_fingerprint()
  fp[0][0x5A] = 8
  fp[2].update({0x3D6: 8, 0x186: 8})
  cp = CarInterface.get_params(identity, fp, [], False, False, False)
  init_ford_safety_param_starpilot(cp, STARTUP['on+pinion'])
  controller = CarController(DBC[cp.carFingerprint], cp)
  inputs = LearnedGeometryInputs(cp, .85, -direction * .04)
  controller.bluepilot_inputs = inputs
  controller.bp = FordBluePilotController(cp, controller.packer, controller.CAN, inputs)
  ci = NS(CP=cp, CC=controller)
  state = CarState(cp)
  state.update(state.get_can_parsers(cp))
  safety, packer = libsafety_py.libsafety, CANPacker('ford_lincoln_base_pt')
  safety.init_tests()
  safety.set_alternative_experience(0)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.ford, cp.safetyConfigs[-1].safetyParam) == 0
  # Standalone native diagnostic; the actual factory/authority lifecycle is tested separately.
  safety.set_controls_allowed(True)
  blocked, traffic, changes = [], [], []
  try:
    for frame in range(45):
      safety.set_timer(1_000_000 + frame * 10_000)
      received = []

      def rx(name, values, received=received):
        address, data, bus = packer.make_can_msg(name, 0, values)
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
        received.append((address, data, bus))

      if frame % 2 == 0:
        rx('BrakeSysFeatures', {'Veh_V_ActlBrk': 108., 'VehVActlBrk_D_Qf': 3, 'VehVActlBrk_No_Cnt': frame // 2 % 16})
        rx('EngVehicleSpThrottle2', {'Veh_V_ActlEng': 108., 'VehVActlEng_D_Qf': 3})
        rx('DesiredTorqBrk', {'VehStop_D_Stat': 0})
      rx('Yaw_Data_FD1', {'VehYaw_W_Actl': 0., 'VehYawWActl_D_Qf': 3, 'VehRollYaw_No_Cnt': frame % 256})
      rx('EngVehicleSpThrottle', {'ApedPos_Pc_ActlArb': 0})
      if frame % 10 == 0:
        rx('EngBrakeData', {'BpedDrvAppl_D_Actl': 1, 'CcStat_D_Actl': 5})
        rx('Steering_Data_FD1', {})
      angle = direction * (30. - .5 * max(0, min(frame - 24, 6)))
      rx('SteeringPinion_Data', {'StePinComp_An_Est': angle, 'StePinCompAnEst_D_Qf': 3, 'StePinAn_No_Cnt': frame % 16})
      controller.bp.observe_can([(0, received)])
      cs = structs.CarState(vEgo=30., vEgoRaw=30., steeringAngleDeg=angle, canValid=True,
                            gearShifter=structs.CarState.GearShifter.drive)
      cs.cruiseState.available = cs.cruiseState.enabled = True
      state.out = cs.as_reader()
      cc = structs.CarControl(enabled=True, latActive=True)
      cc.actuators.curvature = -direction * .02 if 25 <= frame < 30 else 0.
      _, original = controller.update(cc.as_reader(), state, (1_000_000 + frame * 10_000) * 1000)
      packets = order_controller_can(ci, original) if ordered else original
      assert Counter(packets) == Counter(original)
      if packets != original:
        changes.append(frame)
      traffic.extend((frame, address, bus) for address, _, bus in packets)
      if frame == 30 and invalid_shadow:
        packets = [(a, d[:5] + b'\x7f\xff' + d[7:] if a == 0x3CA else d, b) for a, d, b in packets]
      if frame == 30 and withdraw:
        safety.set_controls_allowed(False)
      for address, data, bus in packets:
        if address in (0x3CA, 0x3D3, 0x3D6) and not safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)):
          blocked.append((frame, address))
      if frame == 30 and withdraw:
        assert not safety.get_controls_allowed()
    return blocked, traffic, changes, 0x3D6 if cp.flags & FordFlags.CANFD else 0x3D3
  finally:
    safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)


@pytest.mark.parametrize('message,signal,value', [
  ('ACCDATA_3', 'FcwVisblWarn_B_Rq', 1), ('ACCDATA_3', 'FcwAudioWarn_B_Rq', 1),
  ('ACCDATA_3', 'TjaWarn_D_Rq', 3), ('ACCDATA_3', 'TjaMsgTxt_D_Dsply', 1),
  ('ACCDATA_3', 'AccMsgTxt_D2_Rq', 10), ('ACCDATA_3', 'AccWarn_D_Dsply', 1),
  ('ACCDATA_3', 'FcwMsgTxt_D_Rq', 2), ('ACCDATA_3', 'Tja_D_Stat', 5),
  ('IPMA_Data', 'LaHandsOff_D_Dsply', 1), ('IPMA_Data', 'LaDenyStats_B_Dsply', 1),
  ('IPMA_Data', 'CamraStats_D_Dsply', 3), ('IPMA_Data', 'DasWarn_D_Dsply', 2),
  ('IPMA_Data', 'LaActvStats_D_Dsply', 3),
])
def test_classic_pause_preserves_dashboard_warnings(message, signal, value):
  fp = gen_empty_fingerprint()
  fp[0][0x5A] = 8
  cp = CarInterface.get_params(CAR.FORD_EXPLORER_MK6, fp, [], True, False, False)
  cp.alternativeExperience = 32
  controller = cast(Any, FordBluePilotController.__new__(FordBluePilotController))
  controller.CP, controller.CAN = cp, NS(main=0)
  controller.lateral_active = controller.starpilot_selected = False
  packet = CANPacker('ford_lincoln_base_pt').make_can_msg(message, 0, {signal: value})
  assert controller.order_can_sends([packet]) == [packet]


@pytest.mark.parametrize('identity', [CAR.FORD_MUSTANG_MACH_E_MK1, CAR.FORD_EXPLORER_MK6, CAR.FORD_F_150_MK14])
@pytest.mark.parametrize('direction', [-1., 1.])
def test_current_shadow_prevents_coincident_native_refusal_without_extra_packets(identity, direction):
  previous, old_traffic, _, address = native_run(identity, direction, ordered=False)
  current, traffic, changes, current_address = native_run(identity, direction, ordered=True)
  assert address == current_address
  assert previous == [(30, address)]
  assert not current, current
  assert Counter(traffic) == Counter(old_traffic)
  assert changes and all(frame % 15 == 0 for frame in changes)


@pytest.mark.parametrize('negative', ['shadow', 'permission'])
def test_ordering_cannot_make_invalid_metadata_or_denied_authority_steer(negative):
  blocked, _, _, address = native_run(CAR.FORD_MUSTANG_MACH_E_MK1, 1., ordered=True,
                             invalid_shadow=negative == 'shadow', withdraw=negative == 'permission')
  assert (30, address) in blocked


@pytest.mark.parametrize('main', [0, 4])
@pytest.mark.parametrize('canfd', [False, True])
def test_ordering_preserves_buses_payloads_and_noncoincident_cadences(main, canfd):
  controller = cast(Any, FordBluePilotController.__new__(FordBluePilotController))
  controller.starpilot_selected = True
  controller.curv = NS(primary_lateral_control=PrimaryLateralControl.angle)
  controller.CAN = NS(main=main)
  controller.CP = structs.CarParams(flags=int(FordFlags.CANFD if canfd else 0)).as_reader()
  lmc = (0x3D6 if canfd else 0x3D3, bytes(8), main)
  lka = (0x3CA, bytes(8), main)
  unrelated = (0x165, b'12345678', main + 2)
  for frame in range(15):
    controller.frame = frame
    packets = [unrelated]
    if frame % 5 == 0:
      packets.append(lmc)
    if frame % 3 == 0:
      packets.append(lka)
    result = controller.order_can_sends(packets)
    assert Counter(result) == Counter(packets)
    assert result[0] == unrelated
    if frame == 0:
      assert result == [unrelated, lka, lmc]
    else:
      assert result is packets
  controller.frame = 0
  for packets in ([lmc, (lka[0], lka[1], main + 2)], [lmc, lka, lka], [lmc, lmc, lka], [lka, lmc]):
    assert controller.order_can_sends(packets) is packets
  controller.starpilot_selected = False
  assert controller.order_can_sends([lmc, lka]) == [lmc, lka]
  controller.starpilot_selected = True
  controller.curv.primary_lateral_control = PrimaryLateralControl.curvature
  assert controller.order_can_sends([lmc, lka]) == [lmc, lka]


def test_steering_ordering_is_required_in_both_native_ci_builds():
  from tools.ci.run_vehicle_tests import targets_for
  path = 'openpilot/starpilot/tests/test_ford_starpilot_ordering.py'
  assert targets_for('safety-debug').count(path) == 1
  assert targets_for('safety-release').count(path) == 1


@pytest.mark.parametrize('identity', [CAR.FORD_BRONCO_SPORT_MK1, CAR.FORD_ESCAPE_MK4, CAR.FORD_EXPLORER_MK6,
                                      CAR.FORD_FOCUS_MK4, CAR.FORD_MAVERICK_MK1,
                                      CAR.FORD_MUSTANG_MACH_E_MK1, CAR.FORD_F_150_MK14])
@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('choice', list(FordController))
@pytest.mark.parametrize('aol', [False, True])
def test_classic_aol_pause_returns_dashboard_to_camera_without_changing_actuators(identity, alpha, choice, aol):
  fp = gen_empty_fingerprint()
  fp[0][0x5A] = 8
  fp[2].update({0x3D3: 8, 0x186: 8})
  cp = CarInterface.get_params(identity, fp, [], alpha, False, False)
  cp.alternativeExperience = 32 if aol else 0
  init_ford_safety_param_starpilot(cp, STARTUP['on+pinion'])
  controller = CarController(DBC[cp.carFingerprint], cp)
  inputs = LearnedGeometryInputs(cp, 1., 0.)
  inputs.controller_choice = choice
  inputs.settings['FordPrefLateralControl'] = b'0' if choice == FordController.BLUEPILOT_CURVATURE else b'1'
  controller.bluepilot_inputs = inputs
  controller.bp = FordBluePilotController(cp, controller.packer, controller.CAN, inputs)
  ci = NS(CP=cp, CC=controller)
  state = CarState(cp)
  state.update(state.get_can_parsers(cp))
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(cp.alternativeExperience)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.ford, cp.safetyConfigs[-1].safetyParam) == 0
  try:
    for frame in range(11):
      active = frame < 5 or frame >= 10
      cs = structs.CarState(vEgo=10. if active else 0., vEgoRaw=10. if active else 0., standstill=not active,
                            canValid=True, gearShifter=structs.CarState.GearShifter.drive)
      cs.cruiseState.available = cs.cruiseState.enabled = True
      cs.cruiseState.standstill = not active
      state.out = cs.as_reader()
      cc = structs.CarControl(enabled=True, latActive=active, longActive=alpha)
      cc.actuators.longControlState = structs.CarControl.Actuators.LongControlState.stopping
      _, original = controller.update(cc.as_reader(), state, (1_000_000 + frame * 10_000) * 1000)
      actual = order_controller_can(ci, original)
      ui = [(a, d, b) for a, d, b in original if a in (0x18A, 0x3D8) and b == controller.CAN.main]
      if active or not aol or cp.flags & FordFlags.CANFD:
        assert Counter(actual) == Counter(original)
      else:
        assert Counter(actual) == Counter(original) - Counter(ui)
        if frame == 5:
          assert {a for a, _, _ in ui} == {0x18A, 0x3D8}
          assert any(a == 0x3D3 for a, _, _ in actual)
          # The native camera path stays available; no authority is seeded by this routing test.
          for address in (0x18A, 0x3D8):
            assert safety.safety_fwd_hook(2, address) == 0
        if frame == 6:
          assert any(a == 0x186 for a, _, _ in actual) == alpha
  finally:
    safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
