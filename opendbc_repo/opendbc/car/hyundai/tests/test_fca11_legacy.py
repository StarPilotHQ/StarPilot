from unittest.mock import patch

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car import car_helpers
from opendbc.car.hyundai import hyundaican
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC, HyundaiFlags

RADAR_FW = b'\xf1\x00OSev SCC F-CUP      1.00 1.01 99110-K4000         '


def firmware(version=RADAR_FW, address=0x7d0, ecu=structs.CarParams.Ecu.fwdRadar, subaddress=0):
  return structs.CarParams.CarFw(ecu=ecu, address=address, subAddress=subaddress, fwVersion=version)


def params(versions=None, car=CAR.HYUNDAI_KONA_EV, alpha=True):
  fp = gen_empty_fingerprint()
  fp[0][0x38d] = 8
  versions = [firmware()] if versions is None else versions
  cp = CarInterface.get_params(car, fp, versions, alpha, False, False)
  cp.carFw = versions
  return cp


def packets(cp, index, legacy=True, enabled=True, accel=-.5, override=False, stopping=False):
  hud = structs.CarControl.HUDControl(leadVisible=True, leadDistanceBars=2)
  return hyundaican.create_acc_commands(CANPacker(DBC[cp.carFingerprint][Bus.pt]), enabled, accel, 3., index,
                                      hud, 50., stopping, override, True, cp, legacy_fca11=legacy)


def fca_packet(commands):
  return next(packet for packet in commands if packet[0] == 0x38d)


@pytest.mark.parametrize('index,expected', [(0, '0000450000000070'), (1, '0000450000000061'),
                                            (14, '000045000000009e'), (15, '0000450000000070')])
def test_legacy_fca11_golden_bytes(index, expected):
  _, data, bus = fca_packet(packets(params(), index))
  assert bus == 0
  assert data.hex() == expected


@pytest.mark.parametrize('raw', ['0000cc0100000007', '0000cc01000000f8', '0000cc01000000e9',
                                 '000048000000007d', '000048000000006e', '0000480000000040'])
def test_factory_legacy_checksum_vectors(raw):
  data = bytearray.fromhex(raw)
  expected = data[-1] >> 4
  data[-1] &= 0xf
  assert hyundaican._legacy_fca11_checksum(data) == expected


@pytest.mark.parametrize('change', ['missing', 'date_only', 'different_version', 'different_part', 'different_ecu',
                                    'different_address', 'subaddress', 'conflicting_versions', 'other_car',
                                    'stock', 'passive', 'dashcam', 'not_car', 'no_fca', 'no_ev', 'canfd', 'camera'])
def test_legacy_fca11_requires_exact_reported_profile(change):
  cp = params()
  assert hyundaican.uses_legacy_fca11(cp)
  if change == 'missing':
    cp.carFw = []
  elif change == 'date_only':
    cp.carFw = [firmware(b'\xf1\x8b \x19\x01\x04')]
  elif change == 'different_version':
    cp.carFw = [firmware(RADAR_FW.replace(b'1.01', b'1.00'))]
  elif change == 'different_part':
    cp.carFw = [firmware(RADAR_FW.replace(b'K4000', b'K4100'))]
  elif change == 'different_ecu':
    cp.carFw = [firmware(ecu=structs.CarParams.Ecu.fwdCamera)]
  elif change == 'different_address':
    cp.carFw = [firmware(address=0x730)]
  elif change == 'subaddress':
    cp.carFw = [firmware(subaddress=1)]
  elif change == 'conflicting_versions':
    cp.carFw = [firmware(), firmware(RADAR_FW.replace(b'1.01', b'1.00'))]
  elif change == 'other_car':
    cp.carFingerprint = CAR.HYUNDAI_KONA_EV_2022
  elif change == 'stock':
    cp.openpilotLongitudinalControl = False
  elif change in ('passive', 'dashcam', 'not_car'):
    setattr(cp, {'passive': 'passive', 'dashcam': 'dashcamOnly', 'not_car': 'notCar'}[change], True)
  elif change == 'no_fca':
    cp.flags &= ~int(HyundaiFlags.USE_FCA)
  elif change == 'no_ev':
    cp.flags &= ~int(HyundaiFlags.EV)
  elif change == 'canfd':
    cp.flags |= int(HyundaiFlags.CANFD)
  else:
    cp.flags |= int(HyundaiFlags.CAMERA_SCC)
  assert not hyundaican.uses_legacy_fca11(cp)


def test_auxiliary_firmware_date_does_not_replace_primary_identity():
  assert hyundaican.uses_legacy_fca11(params([firmware(), firmware(b'\xf1\x8b \x19\x01\x04')]))


def test_actual_controller_uses_factory_counter_and_checksum():
  fp = gen_empty_fingerprint()
  fp[0][0x38d] = 8
  fingerprint = (CAR.HYUNDAI_KONA_EV, fp, '0' * 17, [firmware()], structs.CarParams.FingerprintSource.fw, True)
  with patch.object(car_helpers, 'fingerprint', return_value=fingerprint):
    ci = car_helpers.get_car(lambda *args: [], lambda frames: None, lambda *args: None, True, False)
  assert bytes(ci.CP.carFw[0].fwVersion) == RADAR_FW
  ci.update([])
  command = structs.CarControl(enabled=True, longActive=True)
  command.actuators.longControlState = structs.CarControl.Actuators.LongControlState.pid
  command.actuators.accel = -.5
  observed = []
  for frame in range(96):
    _, messages = ci.CC.update(command.as_reader(), ci.CS, frame * 10_000_000)
    for address, data, bus in messages:
      if address == 0x38d:
        assert bus == 0
        assert sum(sum(divmod(byte, 16)) for byte in data) % 16 == 0
        assert (int.from_bytes(data, 'little') >> 35) & 15 == 0
        observed.append(data[-1] & 15)
  assert observed == [index % 15 for index in range(48)]


def test_static_firmware_selection_is_not_repeated_during_control_updates():
  ci = CarInterface(params())
  ci.update([])
  command = structs.CarControl(enabled=True, longActive=True)
  with patch.object(hyundaican, 'uses_legacy_fca11', side_effect=AssertionError('repeated static firmware validation')):
    for frame in range(4):
      ci.CC.update(command.as_reader(), ci.CS, frame * 10_000_000)


def test_legacy_integrity_never_changes_acceleration_or_aeb_requests():
  cp = params()
  for enabled, accel, override, stopping in ((True, -3.5, False, False), (True, 2., False, False),
                                            (True, -.5, True, False), (True, -1., False, True),
                                            (False, 0., False, False)):
    for index in range(31):
      before = packets(cp, index, False, enabled, accel, override, stopping)
      after = packets(cp, index, True, enabled, accel, override, stopping)
      for old, new in zip(before, after, strict=True):
        if old[0] != 0x38d:
          assert old == new
        else:
          old_bits, new_bits = int.from_bytes(old[1], 'little'), int.from_bytes(new[1], 'little')
          mutable = (15 << 35) | (255 << 56)
          assert old_bits & ~mutable == new_bits & ~mutable
          assert (new_bits >> 18) & 3 == 1
          assert (new_bits >> 20) & 1 == 0
          assert (new_bits >> 31) & 1 == 0
          assert (new_bits >> 8) & 255 == 0


def test_legacy_fca11_native_actuation_guards_remain_in_force():
  from opendbc.safety.tests.libsafety import libsafety_py
  from opendbc.safety.tests.libsafety.libsafety_py import make_CANPacket

  cp = params()
  config = cp.safetyConfigs[-1]
  native = libsafety_py.libsafety
  for controls in (False, True):
    for index in range(15):
      native.set_safety_hooks(config.safetyModel.raw, config.safetyParam)
      native.init_tests()
      native.set_controls_allowed(controls)
      modern = fca_packet(packets(cp, index, False))
      legacy = fca_packet(packets(cp, index, True))
      modern_allowed = native.safety_tx_hook(make_CANPacket(modern[0], modern[2], modern[1]))
      assert native.safety_tx_hook(make_CANPacket(legacy[0], legacy[2], legacy[1])) == modern_allowed
      for bit in (8, 20, 31):
        data = bytearray(legacy[1])
        data[bit // 8] |= 1 << (bit % 8)
        data[-1] &= 0xf
        data[-1] |= hyundaican._legacy_fca11_checksum(data) << 4
        assert not native.safety_tx_hook(make_CANPacket(legacy[0], legacy[2], bytes(data)))
