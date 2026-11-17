"""Registry -> typed interface/parser -> actual controller -> native hook.
Run under root's coordinated shared runtime; no controller/state stand-ins.
"""
from types import SimpleNamespace
import struct
import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus, STD_CARGO_KG, gen_empty_fingerprint, structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.fingerprints import all_legacy_fingerprint_cars, eliminate_incompatible_cars
from opendbc.car.volvo.fingerprints import FINGERPRINTS
from opendbc.car.volvo.values import CAR, DBC
from opendbc.safety.tests.libsafety import libsafety_py

CMA = ['VOLVO_XC40_RECHARGE', 'VOLVO_S60_RECHARGE', 'POLESTAR_2']
EXPECTED = {'VOLVO_V40': (2, 1610, 2.647, 14.7, .2),
            'VOLVO_XC40_RECHARGE': (0, 2170, 2.702, 15.8, .3),
            'VOLVO_S60_RECHARGE': (1, 2020, 2.872, 16.2, .3),
            'POLESTAR_2': (5, 2123, 2.735, 15.8, .3)}


def interface(identity, alpha=False, release=False):
  cls = interfaces[identity]
  cp = cls.get_params(identity, gen_empty_fingerprint(), [], alpha, release, False)
  return cp, cls(cp)


def packet(message):
  address, data, bus = message
  return libsafety_py.make_CANPacket(address, bus, data)


def sources(identity, cruise=True, *, frame=0):
  modulus = 15 if identity == 'POLESTAR_2' else 16
  mid = CANPacker('volvo_mid_1')
  pt = CANPacker(DBC[CAR(identity)][Bus.pt])
  result = [mid.make_can_msg(name, bus, values) for name, bus, values in [
    ('SPEED', 0, {'SPEED': 3000}), ('SPEED_2', 0, {}), ('GEAR_POSITION', 0, {'GEAR_POSITION': 3}),
    ('LCA', 0, {}), ('LCA_2', 0, {'COUNTER_1': ((frame // 2) * 2) % modulus, 'COUNTER_2': ((frame // 2) * 4) % modulus}), ('LCA_3', 0, {}), ('LCA_4', 0, {}),
    ('LCA_5', 0, {}), ('LCA_6', 0, {}), ('LCA_7', 0, {}),
    ('PSCM', 2, {}), ('DRIVER_INPUT', 2, {}), ('EGSM', 2, {}), ('PSCM_RELATED', 2, {'NEW_SIGNAL_2': 8}), ('SAS', 2, {}),
  ]]
  spa = identity != 'VOLVO_XC40_RECHARGE'
  result.append(pt.make_can_msg('ECM_1', 1, {'ACCELERATOR_PEDAL_POS': 0 if spa else 20}))
  result.append(pt.make_can_msg('BUS1_CRUISE_CONTROL', 1,
                              {'CRUISE_CONTROL_SPA_ENABLED' if spa else 'CRUISE_CONTROL_ENABLED': int(cruise)}))
  from opendbc.car.volvo.helpers import (checksum_lca_2_message, checksum_2_0x69_message,
    checksum_1_pscm_related_message, checksum_2_pscm_related_message, checksum_lca_5_message)
  checked = []
  for address, payload, bus in result:
    if address == 0x69 and frame % 2:
      continue  # This source is 50Hz in the actual receiver contract.
    d = bytearray(payload)
    if address == 0x69:
      d[6] = checksum_lca_2_message(d[0], d[5])
      d[2] = checksum_2_0x69_message(d[0], d[1], d[3], d[4])
    elif address == 0x17:
      d[0] = checksum_1_pscm_related_message(d[1], d[2])
      d[3] = checksum_2_pscm_related_message(d[2])
    elif address == 0x67:
      d[2] = checksum_lca_5_message(d[0], d[1], d[3], d[4], d[5])
    checked.append((address, bytes(d), bus))
  return checked


@pytest.mark.parametrize('identity', EXPECTED)
@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('release', [False, True])
def test_all_final_params_and_angle_factory(identity, alpha, release):
  from openpilot.selfdrive.controls.lib.lateral_controller_factory import create_lateral_controller
  from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle
  cp, ci = interface(identity, alpha, release)
  param, mass, wheelbase, ratio, delay = EXPECTED[identity]
  assert cp.mass == mass + STD_CARGO_KG
  # These declared CarParams fields are Float32; compare exact wire values.
  expected_geometry = struct.unpack('<3f', struct.pack('<3f', wheelbase, ratio, delay))
  assert (cp.wheelbase, cp.steerRatio, cp.steerActuatorDelay) == expected_geometry
  assert cp.safetyConfigs[0].safetyParam == param
  assert cp.steerControlType == structs.CarParams.SteerControlType.angle
  assert cp.pcmCruise and cp.radarUnavailable and not cp.dashcamOnly
  assert not cp.alphaLongitudinalAvailable and not cp.openpilotLongitudinalControl
  assert isinstance(create_lateral_controller(cp, ci, .01, mode='stock'), LatControlAngle)


@pytest.mark.parametrize('identity', EXPECTED)
def test_all_dom_captures_identify_through_real_eliminator(identity):
  for capture in FINGERPRINTS[CAR(identity)]:
    cars = all_legacy_fingerprint_cars()
    for address, size in capture.items():
      if address < 0x800 and address not in (0x7df, 0x7e0, 0x7e8):
        cars = eliminate_incompatible_cars(SimpleNamespace(address=address, dat=bytes(size)), cars)
    assert cars == [CAR(identity)]


@pytest.mark.parametrize('identity', CMA)
def test_ci_update_apply_native_engage_disengage_reengage(identity):
  cp, ci = interface(identity)
  lib = libsafety_py.libsafety
  assert lib.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  lib.init_tests()
  cc = structs.CarControl.new_message()
  cc.actuators.steeringAngleDeg = 0
  seen = set()
  for frame in range(250):
    now = 1_000_000_000 + frame * 10_000_000
    incoming = sources(identity, frame=frame)
    lib.set_timer(frame * 10000)
    for msg in incoming:
      assert lib.safety_rx_hook(packet(msg))
    lib.safety_tick()
    state = ci.update([(now, incoming)])
    assert state.canValid
    assert state.cruiseState.enabled and state.cruiseState.available
    assert state.gearShifter == structs.CarState.GearShifter.drive
    cc.latActive = frame >= 10 and not 150 <= frame < 180
    _, output = ci.apply(cc.as_reader(), now)
    for msg in output:
      assert lib.safety_tx_hook(packet(msg)), (identity, frame, msg)
      seen.add((msg[0], msg[2]))
  assert seen == {(a, 2) for a in (0x58, 0x57, 0x69, 0x90, 0x67, 0x97, 0x92)} | {(0x16, 0), (0x17, 0)}


@pytest.mark.parametrize('identity', CMA)
def test_native_withdrawal_exact_stock_only_and_no_lease_renewal(identity):
  cp, ci = interface(identity)
  lib = libsafety_py.libsafety
  assert lib.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  lib.init_tests()
  lib.set_timer(10000)
  incoming = sources(identity, cruise=False)
  for msg in incoming:
    lib.safety_rx_hook(packet(msg))
  assert not lib.get_controls_allowed()
  ci.update([(1_000_000_000, incoming)])
  cc = structs.CarControl.new_message()
  _, stock = ci.apply(cc.as_reader(), 1_000_000_000)
  for msg in stock:
    assert lib.safety_tx_hook(packet(msg))
    address, data, bus = msg
    forged = bytes([data[0] ^ 1]) + data[1:]
    assert not lib.safety_tx_hook(packet((address, forged, bus)))
    assert not lib.safety_tx_hook(packet((address, data, 1)))
  lib.set_timer(110001)
  assert all(not lib.safety_tx_hook(packet(msg)) for msg in stock)
  assert all(not lib.safety_tx_hook(packet(msg)) for msg in stock)


@pytest.mark.parametrize('identity', CMA)
def test_required_source_loss_and_no_fabricated_main(identity):
  _, ci = interface(identity)
  state = ci.update([(1_000_000_000, sources(identity, cruise=False))])
  assert not state.cruiseState.enabled and not state.cruiseState.available
  assert not state.buttonEvents
  for frame in range(1, 301):
    state = ci.update([(1_000_000_000 + frame * 10_000_000, [])])
  assert not state.canValid
