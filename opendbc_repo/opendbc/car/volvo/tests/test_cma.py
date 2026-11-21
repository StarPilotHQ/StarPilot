"""Registry -> typed interface/parser -> actual controller -> native hook for the
CMA/CMA receiver layout (XC40 Recharge, S60 Recharge, Polestar 2). C1 lives in test_c1.py.

Also the shared CAN fixtures used by the feature tests and the openpilot startup tests.
"""
from types import SimpleNamespace
import struct

import pytest
from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, CanData, STD_CARGO_KG, gen_empty_fingerprint, structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.fingerprints import all_legacy_fingerprint_cars, eliminate_incompatible_cars
from opendbc.car.volvo.carcontroller import CarController
from opendbc.car.volvo.carstate import CarState
from opendbc.car.volvo.fingerprints import FINGERPRINTS
from opendbc.car.volvo.helpers import (checksum_1_lca_2_message, checksum_1_pscm_related_message, checksum_2_lca_2_message,
                                      checksum_2_pscm_related_message, checksum_lca_5_message)
from opendbc.car.volvo.interface import CarInterface
from opendbc.car.volvo.values import CAR, DBC
from opendbc.safety.tests.libsafety import libsafety_py


CMA = ['VOLVO_XC40_RECHARGE', 'VOLVO_S60_RECHARGE', 'POLESTAR_2']


# identity: (safetyParam, mass, wheelbase, steerRatio, steerActuatorDelay)
EXPECTED = {'VOLVO_V40': (2, 1610, 2.647, 14.7, .2),
            'VOLVO_XC40_RECHARGE': (0, 2170, 2.702, 15.8, .3),
            'VOLVO_S60_RECHARGE': (0, 2020, 2.872, 16.2, .3),
            'POLESTAR_2': (0, 2123, 2.735, 15.8, .3)}
# The V60 (EUCD) only shares the params and fingerprint checks; the shared runtime fixtures model CMA/V40 frames
PARAMS = {**EXPECTED, 'VOLVO_V60': (8, 1750, 2.776, 15.0, .2)}


# Every address openpilot replaces while steering, with the bus it is sent on.
REPLACED = {(0x58, 2), (0x57, 2), (0x69, 2), (0x67, 2), (0x90, 2), (0x16, 0), (0x17, 0)}


# Stock source bus for each replaced address (volvo_cma.h VOLVO_REPLACED_MSGS).
STOCK_BUS = {0x58: 0, 0x57: 0, 0x69: 0, 0x67: 0, 0x90: 0, 0x16: 2, 0x17: 2}


def interface(identity, alpha=False, release=False):
  cls = interfaces[identity]
  cp = cls.get_params(identity, gen_empty_fingerprint(), [], alpha, release, False)
  return cp, cls(cp)


def packet(message):
  address, data, bus = message
  return libsafety_py.make_CANPacket(address, bus, data)


def _pscm_related_supported():
  # Status 8, matching rack counter/replica, valid checksums: a frame seen on the car.
  b1, b2 = 0x51, 0x85
  return bytes([checksum_1_pscm_related_message(b1, b2), b1, b2, checksum_2_pscm_related_message(b2), 0, 0, 0, 0])


def checksummed_lca_2(values=None):
  """An LCA_2 (0x69) frame as the car sends it, with both checksums panda validates."""
  address, data, bus = CANPacker('volvo_mid_1').make_can_msg('LCA_2', 0, {'BYTE_0': 0x18, **(values or {})})
  d = bytearray(data)
  d[6] = checksum_1_lca_2_message(d[0], d[5])
  d[2] = checksum_2_lca_2_message(d[0], d[1], d[3], d[4])
  return address, bytes(d), bus


def sources(identity, cruise=True, *, frame=0):
  mid = CANPacker('volvo_mid_1')
  pt = CANPacker(DBC[CAR(identity)][Bus.pt])
  result = [mid.make_can_msg(name, bus, values) for name, bus, values in [
    ('SPEED', 0, {'SPEED': 3000}), ('SPEED_2', 0, {}), ('GEAR_POSITION', 0, {'GEAR_POSITION': 3}),
    ('LCA', 0, {}), ('LCA_3', 0, {}), ('LCA_4', 0, {}),
    ('LCA_5', 0, {}), ('LCA_6', 0, {}), ('LCA_7', 0, {}),
    ('PSCM', 2, {}), ('DRIVER_INPUT', 2, {}), ('EGSM', 2, {}), ('SAS', 2, {}),
  ]]
  result.append(checksummed_lca_2())
  result.append((0x17, _pscm_related_supported(), 2))
  result.append(pt.make_can_msg('ECM_1', 1, {'ACCELERATOR_PEDAL_POS': 0}))
  result.append(pt.make_can_msg('BUS1_CRUISE_CONTROL', 1, {'CRUISE_CONTROL_SPA_ENABLED': int(cruise)}))
  return result


def _can_data(msg):
  address, data, bus = msg
  return CanData(address, data, bus)


def _make_state():
  cp = CarInterface.get_non_essential_params(CAR.VOLVO_XC40_RECHARGE)
  cs = CarState(cp)
  parsers = CarState.get_can_parsers(cp)
  packers = {bus: CANPacker(DBC[cp.carFingerprint][bus]) for bus in (Bus.main, Bus.pt, Bus.party)}
  return cs, parsers, packers


def _step(cs, parsers, packers, t_us, cruise_enabled, pscm_related=None):
  """One cycle of CarState.update(), with baseline traffic on all three buses plus the
  cruise-control message reflecting cruise_enabled on the PT bus. pscm_related optionally
  replaces the all-zero PSCM_RELATED frame with raw bytes."""
  messages = [
    packers[Bus.main].make_can_msg("SPEED", 0, {"SPEED": 0}),
    packers[Bus.main].make_can_msg("LCA_2", 0, {}),
    packers[Bus.main].make_can_msg("GEAR_POSITION", 0, {"GEAR_POSITION": 3}),
    packers[Bus.party].make_can_msg("PSCM", 2, {}),
    packers[Bus.party].make_can_msg("DRIVER_INPUT", 2, {}),
    packers[Bus.party].make_can_msg("EGSM", 2, {}),
    packers[Bus.party].make_can_msg("PSCM_RELATED", 2, {}),
    packers[Bus.pt].make_can_msg("ECM_1", 1, {}),
    packers[Bus.pt].make_can_msg("BUS1_CRUISE_CONTROL", 1, {"CRUISE_CONTROL_SPA_ENABLED": 1 if cruise_enabled else 0}),
  ]
  frames = [_can_data(msg) for msg in messages]
  if pscm_related is not None:
    frames = [f for f in frames if not (f.address == 0x17 and f.src == 2)] + [CanData(0x17, pscm_related, 2)]
  packets = [(t_us, frames)]
  for parser in parsers.values():
    parser.update(packets)
  # CarInterface.update hands CarState the newest raw PSCM_RELATED frame.
  for f in frames:
    if f.address == 0x17 and f.src == 2:
      cs.pscm_related_raw = bytes(f.dat)
  ret = cs.update(parsers)
  return ret


def _pscm_related(b1, b2):
  """A PSCM_RELATED frame with both checksums computed for the given bytes 1 and 2."""
  return bytes([checksum_1_pscm_related_message(b1, b2), b1, b2, checksum_2_pscm_related_message(b2), 0, 0, 0, 0])


def _stock_messages(dbc, names):
  """Message dicts as carstate passes them: a CANParser fills in every DBC signal (zero until a frame arrives)."""
  parser = CANParser(dbc, [(name, 0) for name in names], 0)
  return {name: parser.vl[name] for name in names}


def _state():
  msgs = _stock_messages(DBC[CAR.POLESTAR_2][Bus.party], ["LCA", "PSCM", "PSCM_RELATED", "LCA_2", "LCA_3", "LCA_4", "LCA_5"])
  return SimpleNamespace(
    out=SimpleNamespace(steeringAngleDeg=0.0, vEgoRaw=12.0, steeringTorque=0.0),
    msg_lca=msgs["LCA"],
    msg_pscm=msgs["PSCM"],
    msg_pscm_related=msgs["PSCM_RELATED"],
    msg_lca_3=msgs["LCA_3"],
    msg_lca_2=msgs["LCA_2"],
    msg_lca_5=msgs["LCA_5"],
    msg_lca_4=msgs["LCA_4"],
    pilot_assist_engaged=False,
  )


class _Actuators:
  steeringAngleDeg = 30.0

  def as_builder(self):
    return SimpleNamespace(steeringAngleDeg=self.steeringAngleDeg)


@pytest.mark.parametrize('identity', PARAMS)
@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('release', [False, True])
def test_all_final_params_and_angle_factory(identity, alpha, release):
  from openpilot.selfdrive.controls.lib.lateral_controller_factory import create_lateral_controller
  from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle
  cp, ci = interface(identity, alpha, release)
  param, mass, wheelbase, ratio, delay = PARAMS[identity]
  assert cp.mass == mass + STD_CARGO_KG
  # These declared CarParams fields are Float32; compare exact wire values.
  expected_geometry = struct.unpack('<3f', struct.pack('<3f', wheelbase, ratio, delay))
  assert (cp.wheelbase, cp.steerRatio, cp.steerActuatorDelay) == expected_geometry
  assert cp.safetyConfigs[0].safetyParam == param
  assert cp.steerControlType == structs.CarParams.SteerControlType.angle
  assert cp.pcmCruise and not cp.dashcamOnly
  assert cp.radarUnavailable == (identity != 'VOLVO_V60')  # the V60's Delphi ESR
  assert cp.alphaLongitudinalAvailable == (identity == 'VOLVO_V60')
  assert cp.openpilotLongitudinalControl == (alpha and identity == 'VOLVO_V60')
  assert isinstance(create_lateral_controller(cp, ci, .01, mode='stock'), LatControlAngle)


@pytest.mark.parametrize('identity', PARAMS)
def test_all_captures_identify_through_real_eliminator(identity):
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
    assert not state.steerFaultTemporary
    assert state.gearShifter == structs.CarState.GearShifter.drive
    cc.latActive = frame >= 10 and not 150 <= frame < 180
    _, output = ci.apply(cc.as_reader(), now)
    if not cc.latActive:
      assert output == []
    for msg in output:
      assert lib.safety_tx_hook(packet(msg)), (identity, frame, msg)
      seen.add((msg[0], msg[2]))
  assert seen == REPLACED


def test_available_is_always_true():
  """Without Always On Lateral, cruise availability stays True regardless of cruise taps."""
  cs, parsers, packers = _make_state()
  for t, enabled in ((0, False), (100_000, True), (120_000, False)):
    ret = _step(cs, parsers, packers, t, enabled)
    assert ret.cruiseState.available is True


def test_controller_emits_valid_eight_byte_messages_and_lca5_checksum():
  cp = CarInterface.get_non_essential_params("POLESTAR_2")
  controller = CarController(DBC[cp.carFingerprint], cp)
  cs = _state()
  cc = SimpleNamespace(latActive=True, actuators=_Actuators())

  actuators, can_sends = controller.update(cc, cs, 0)

  assert can_sends
  assert {msg[2] for msg in can_sends} == {0, 2}
  assert all(len(msg[1]) == 8 for msg in can_sends)
  assert 0.0 < actuators.steeringAngleDeg < 540.0

  lca5 = next(msg for msg in can_sends if msg[0] == 0x67)
  data = lca5[1]
  assert data[2] == checksum_lca_5_message(data[0], data[1], data[3], data[4], data[5])
