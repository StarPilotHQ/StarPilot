from contextlib import ExitStack
from unittest.mock import patch

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai import canfd_lfa_status, carcontroller
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.values import CAR, DBC, HyundaiFlags


EXPLICIT = (CAR.KIA_CARNIVAL_4TH_GEN, CAR.KIA_CARNIVAL_2025,
            CAR.KIA_CARNIVAL_HEV_4TH_GEN, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN)
ANGLES = tuple(car for car in CAR if car.config.flags & HyundaiFlags.CANFD_ANGLE_STEERING)


def params(car, alternate=None, alpha=False):
  fp = gen_empty_fingerprint()
  if alternate is not None:
    fp[2].update({0x110: 32, 0x362: 32} if alternate else {0x50: 16, 0x2A4: 24})
  bus = CanBus(None, fp, lka_steering=alternate is not None)
  fp[bus.ECAN].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1A0: 32})
  fp[bus.CAM][0x1A0] = 32
  return CarInterface.get_params(car, fp, [], alpha, False, False)


def frames(ci, now):
  control = structs.CarControl()
  with patch.object(carcontroller, 'clock_boot_ns', return_value=now):
    return ci.CC.update(control.as_reader(), ci.CS, now)[1]


@pytest.mark.parametrize('car', (CAR.KIA_EV6, CAR.KIA_NIRO_EV_2ND_GEN))
@pytest.mark.parametrize('alpha', (False, True))
def test_hda1_actual_factory_direct_lfa_damping_unchanged(car, alpha):
  with ExitStack() as stack:
    cp = params(car, alpha=alpha)
    if alpha and not cp.openpilotLongitudinalControl:
      from opendbc.car.hyundai.tests import test_torque_ev_startup as startup_fixture
      stock = params(car)
      startup, _ = startup_fixture.owner(stock)
      stack.callback(startup.close)
      with patch('opendbc.car.hyundai.torque_ev_startup.time.sleep'):
        cp = startup.prepare(admission=lambda: True)
    assert cp.openpilotLongitudinalControl == alpha
    ci = CarInterface(cp)
    assert ci.CS.stock_lkas_damping is None
    packer = CANPacker(DBC[car][Bus.pt])
    bus = CanBus(cp)
    now = 1_000_000_000
    packet = packer.make_can_msg('LFA', bus.CAM, {'COUNTER': 10, 'Damping_Gain': 3})
    with patch.object(canfd_lfa_status.time, 'clock_gettime_ns', return_value=now):
      ci.update([(now, [packet])])
    steering = next(frame for frame in frames(ci, now) if frame[0] == 0x12A)
    assert steering[2] == bus.ECAN
    assert steering[1][13] == 100


@pytest.mark.parametrize('car', EXPLICIT)
@pytest.mark.parametrize('alternate', (False, True))
def test_explicit_calibration_is_not_replaced(car, alternate):
  cp = params(car, alternate)
  ci = CarInterface(cp)
  assert ci.CS.stock_lkas_damping is None
  packer = CANPacker(DBC[car][Bus.pt])
  bus = CanBus(cp)
  now = 1_000_000_000
  packet = packer.make_can_msg('LKAS_ALT' if alternate else 'LKAS', bus.CAM, {
    'COUNTER': 10, 'Damping_Gain': 3,
  })
  with patch.object(canfd_lfa_status.time, 'clock_gettime_ns', return_value=now):
    ci.update([(now, [packet])])
  steering = next(frame for frame in frames(ci, now) if frame[0] in (0x50, 0x110))
  assert steering[1][8] == 100


@pytest.mark.parametrize('car', ANGLES)
def test_factory_angle_configuration_has_no_stock_torque_tracker(car):
  cp = params(car, None if car == CAR.KIA_EV6_2025 else True)
  assert cp.flags & HyundaiFlags.CANFD_ANGLE_STEERING
  assert not canfd_lfa_status.preserves_stock_lkas_damping(cp)
  assert CarInterface(cp).CS.stock_lkas_damping is None
