from unittest.mock import patch

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai import canfd_lfa_status, carcontroller
from opendbc.car.hyundai.hyundaicanfd import CanBus, hkg_can_fd_checksum
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC, HyundaiFlags


EXPLICIT_DAMPING = (CAR.KIA_CARNIVAL_4TH_GEN, CAR.KIA_CARNIVAL_2025,
                    CAR.KIA_CARNIVAL_HEV_4TH_GEN, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN)
CARS = tuple(car for car in CAR if car.config.flags & HyundaiFlags.CANFD and
             not car.config.flags & (HyundaiFlags.CANFD_ANGLE_STEERING | HyundaiFlags.NON_SCC) and
             car not in EXPLICIT_DAMPING)


def interface(car, alternate):
  fingerprint = gen_empty_fingerprint()
  fingerprint[2][0x110 if alternate else 0x50] = 32 if alternate else 16
  fingerprint[1][0x1CF] = 8
  cp = CarInterface.get_params(car, fingerprint, [], False, False, False)
  assert not cp.openpilotLongitudinalControl
  ci = CarInterface(cp)
  return ci, CANPacker(DBC[car][Bus.pt]), CanBus(cp)


def camera(packer, bus, alternate, damping, counter):
  return packer.make_can_msg('LKAS_ALT' if alternate else 'LKAS', bus, {
    'COUNTER': counter, 'Damping_Gain': damping, 'StrTqReqVal': -137,
    'ActToiSta': 0, 'LKA_UsmMod': 3,
  })


def receive(ci, now, frames, stamp=None):
  with patch.object(canfd_lfa_status.time, 'clock_gettime_ns', return_value=now):
    return ci.update([(now if stamp is None else stamp, frames)])


def command(ci, now, active=True):
  control = structs.CarControl()
  control.enabled = control.latActive = active
  control.actuators.torque = 1 / 409 if active else 0
  with patch.object(carcontroller, "clock_boot_ns", return_value=now):
    _, frames = ci.CC.update(control.as_reader(), ci.CS, now)
  return next(frame for frame in frames if frame[0] in (0x50, 0x110))


@pytest.mark.parametrize('car', CARS)
@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('damping', (0, 3, 100, 255))
def test_checked_camera_damping_reaches_stock_controller(car, alternate, damping):
  ci, packer, bus = interface(car, alternate)
  now = 1_000_000_000
  receive(ci, now, [camera(packer, bus.CAM, alternate, damping, 10)])
  address, data, target = command(ci, now)
  assert target == bus.ACAN
  assert data[8] == damping
  assert int.from_bytes(data[:2], 'little') == hkg_can_fd_checksum(address, None, data)
  bits = int.from_bytes(data, 'little')
  assert (bits >> 41 & 0x7FF) - 1024 == 1
  assert bits >> 52 & 3 == 1
  assert bits >> 64 & 0xFF == damping
  assert bits >> 60 & 0xF == 0
  assert bits >> 53 & 0x7 == 0


@pytest.mark.parametrize('alternate', (False, True))
@pytest.mark.parametrize('bad', ('crc', 'counter', 'bus', 'length', 'future'))
def test_invalid_camera_packet_cannot_replace_damping(alternate, bad):
  ci, packer, bus = interface(CAR.HYUNDAI_IONIQ_6, alternate)
  now = 1_000_000_000
  receive(ci, now, [camera(packer, bus.CAM, alternate, 100, 10)])
  address, data, source = camera(packer, bus.CAM, alternate, 3, 12 if bad == 'counter' else 11)
  if bad == 'crc':
    data = bytes((data[0] ^ 1,)) + data[1:]
  elif bad == 'bus':
    source = bus.ECAN
  elif bad == 'length':
    data += b'\0'
  now += 10_000_000
  receive(ci, now, [(address, data, source)], stamp=now + 10_000_000 if bad == 'future' else now)
  assert command(ci, now)[1][8] == 100
  now += 10_000_000
  receive(ci, now, [camera(packer, bus.CAM, alternate, 3, 13 if bad == 'counter' else 11)])
  assert command(ci, now)[1][8] == 3


@pytest.mark.parametrize('alternate', (False, True))
def test_source_gap_keeps_existing_fallback_and_recovers(alternate):
  ci, packer, bus = interface(CAR.HYUNDAI_IONIQ_6, alternate)
  now = 1_000_000_000
  receive(ci, now, [])
  assert command(ci, now)[1][8] == 0
  now += 10_000_000
  receive(ci, now, [camera(packer, bus.CAM, alternate, 100, 10)])
  assert command(ci, now)[1][8] == 100
  assert command(ci, now + canfd_lfa_status.MAX_AGE_NS)[1][8] == 100
  now += canfd_lfa_status.MAX_AGE_NS + 1
  assert command(ci, now)[1][8] == 0
  receive(ci, now, [camera(packer, bus.CAM, alternate, 100, 11)])
  assert command(ci, now)[1][8] == 100
  assert command(ci, now, active=False)[1][8] == 100


@pytest.mark.parametrize('alternate', (False, True))
def test_native_authority_still_gates_actual_stock_damping_frame(alternate):
  from opendbc.safety.tests import test_hyundai_ioniq6_stock_aol as stock
  from opendbc.safety.tests.libsafety import libsafety_py

  ci, packer, bus = interface(CAR.HYUNDAI_IONIQ_6, alternate)
  now = 1_000_000_000
  receive(ci, now, [camera(packer, bus.CAM, alternate, 100, 10)])
  active = command(ci, now)
  assert active[1][8] == 100
  native = stock.TestHyundaiIoniq6StockAol()
  native.mode(0x891 if alternate else 0x811)
  native.healthy()
  native.safety.set_aol_test_heartbeat(True)
  native.safety.aol_set_host_request(3)

  def accepted(frame):
    return bool(native.safety.safety_tx_hook(libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])))

  assert not accepted(active)
  native.arm()
  assert accepted(active)
  native.safety.aol_set_host_request(0)
  assert not accepted(active)
  assert accepted(command(ci, now, active=False))
  native.safety.aol_set_host_request(3)
  native.safety.set_relay_malfunction(True)
  assert not accepted(active)


@pytest.mark.parametrize('invalid', ('canfd', 'lka', 'angle', 'long', 'passive', 'dashcam', 'notcar', 'brand', 'fingerprint'))
def test_damping_source_rejects_incompatible_params(invalid):
  from opendbc.car.hyundai.values import HyundaiFlags

  ci, _, _ = interface(CAR.HYUNDAI_IONIQ_6, True)
  cp = ci.CP.as_reader().as_builder()
  if invalid == 'canfd':
    cp.flags &= ~int(HyundaiFlags.CANFD)
  elif invalid == 'lka':
    cp.flags &= ~int(HyundaiFlags.CANFD_LKA_STEER_MSG)
  elif invalid == 'angle':
    cp.steerControlType = 'angle'
  elif invalid == 'long':
    cp.openpilotLongitudinalControl = True
  elif invalid == 'passive':
    cp.passive = True
  elif invalid == 'dashcam':
    cp.dashcamOnly = True
  elif invalid == 'notcar':
    cp.notCar = True
  elif invalid == 'brand':
    cp.brand = 'toyota'
  else:
    cp.carFingerprint = 'unsupported'
  assert not canfd_lfa_status.preserves_stock_lkas_damping(cp)
