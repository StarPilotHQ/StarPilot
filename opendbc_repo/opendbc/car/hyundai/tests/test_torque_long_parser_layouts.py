import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.canfd_stock_aol import qualified_long
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC, HyundaiFlags


def sportage(hybrid, layout, offset, gear, alpha):
  lka, alternate = layout
  fp = gen_empty_fingerprint()
  pt = offset + (1 if lka else 0)
  if lka:
    fp[offset + 2][0x110 if lka == 2 else 0x50] = 32 if lka == 2 else 16
  fp[pt][0x1AA if alternate else 0x1CF] = 16 if alternate else 8
  fp[pt][gear] = 16 if gear == 0x130 else 32
  fp[pt][0x105 if hybrid else 0x100] = 32
  if hybrid:
    fp[pt][0xFA] = 8
  fw = [structs.CarParams.CarFw(ecu='adas')] if lka else []
  cp = CarInterface.get_params(CAR.KIA_SPORTAGE_5TH_GEN, fp, fw, alpha, False, False)
  assert cp.openpilotLongitudinalControl == alpha
  assert not alpha or qualified_long(cp)
  return cp


def frames(ci, packer, tick, omitted=None):
  cp = ci.CP
  buses = CanBus(cp)
  names = [ci.CS.accelerator_msg_canfd, ci.CS.gear_msg_canfd, ci.CS.cruise_btns_msg_canfd,
           'TCS', 'WHEEL_SPEEDS', 'MDPS', 'STEERING_SENSORS', 'DOORS_SEATBELTS', 'BLINKERS']
  packets = [packer.make_can_msg(name, buses.ECAN, {}) for name in dict.fromkeys(names) if name != omitted]
  camera = ('CAM_0x362' if cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT else 'CAM_0x2a4') if cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG else 'LFA'
  if camera != omitted:
    packets.append(packer.make_can_msg(camera, buses.CAM, {}))
  if not cp.openpilotLongitudinalControl:
    packets.append(packer.make_can_msg('SCC_CONTROL', buses.CAM if cp.flags & HyundaiFlags.CANFD_CAMERA_SCC else buses.ECAN, {}))
  return [(10_000_000_000 + tick * 10_000_000, packets)]


@pytest.mark.parametrize('hybrid', [False, True])
@pytest.mark.parametrize('layout', [(0, False), (0, True), (1, False), (2, False)])
@pytest.mark.parametrize('offset', [0, 4])
@pytest.mark.parametrize('gear', [0x130, 0x40, 0x70])
@pytest.mark.parametrize('alpha', [False, True])
def test_long_parser_matches_physical_fuel_gear_and_camera(hybrid, layout, offset, gear, alpha):
  cp = sportage(hybrid, layout, offset, gear, alpha)
  original = cp.to_dict()
  ci = CarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  for tick in range(130):
    state = ci.update(frames(ci, packer, tick))
  assert state.canValid and not state.canTimeout
  assert cp.to_dict() == original
  if alpha:
    pedal = ci.can_parsers[Bus.pt].message_states[0x105 if hybrid else 0x100]
    assert pedal.frequency == 100 and not pedal.ignore_alive
    assert 0x35 not in ci.can_parsers[Bus.pt].message_states
    camera = ci.can_parsers[Bus.cam].message_states
    assert (0x362 if layout[0] == 2 else 0x2A4) in camera if layout[0] else 0x2A4 not in camera


@pytest.mark.parametrize('omitted', ['ACCELERATOR_BRAKE_ALT', 'TCS', 'WHEEL_SPEEDS', 'MDPS', 'GEAR_ALT', 'CAM_0x2a4'])
def test_long_parser_still_rejects_missing_required_physical_input(omitted):
  cp = sportage(False, (1, False), 4, 0x40, True)
  ci = CarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  for tick in range(130):
    state = ci.update(frames(ci, packer, tick, omitted))
  assert not state.canValid
