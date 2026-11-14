import unittest

from opendbc.car import structs
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.values import CAR, DBC
from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
from opendbc.car.hyundai.tests.test_canfd_bsm_reserved import state_for, update


class TestIoniq6StockDashStatus(unittest.TestCase):
  def frame(self, alternate, longitudinal, enabled, active):
    topology = 'lka_alt' if alternate else 'lka'
    state, packer, bus = state_for(CAR.HYUNDAI_IONIQ_6, topology, longitudinal)
    state.CP.safetyConfigs[0].safetyParam = (0x8895 if alternate else 0x8815) if longitudinal else (0x0891 if alternate else 0x0811)
    parsers = state.get_can_parsers(state.CP)
    update(state, parsers, 1_000_000_000, [packer.make_can_msg('TCS', bus, {})])
    state.out.vEgo = state.out.vEgoRaw = 10.0
    state.out.gearShifter = structs.CarState.GearShifter.drive
    control = structs.CarControl()
    control.enabled, control.latActive = enabled, active
    controller = CarController(DBC[CAR.HYUNDAI_IONIQ_6], state.CP)
    _, frames = controller.update(control.as_reader(), state, 1_000_000_000)
    address = 0x110 if alternate else 0x50
    steering = next(frame for frame in frames if frame[0] == address)
    data = steering[1]
    self.assertEqual(int.from_bytes(data[:2], 'little'), hkg_can_fd_checksum(address, None, data))
    if not longitudinal:
      self.assertFalse({0x1ba, 0x1e5} & {frame[0] for frame in frames})
    bits = int.from_bytes(data, 'little')
    return (bits >> 38) & 7, (bits >> 52) & 3

  def test_aol_only_status_matches_stock_and_disabled_ecu(self):
    for alternate in (False, True):
      for longitudinal in (False, True):
        with self.subTest(alternate=alternate, longitudinal=longitudinal):
          self.assertEqual(self.frame(alternate, longitudinal, False, True), (2, 1))

  def test_inactive_status_and_request_stay_off(self):
    for alternate in (False, True):
      for longitudinal in (False, True):
        with self.subTest(alternate=alternate, longitudinal=longitudinal):
          self.assertEqual(self.frame(alternate, longitudinal, False, False), (1, 0))

  def test_full_engagement_status_is_unchanged(self):
    for alternate in (False, True):
      for longitudinal in (False, True):
        with self.subTest(alternate=alternate, longitudinal=longitudinal):
          self.assertEqual(self.frame(alternate, longitudinal, True, True), (2, 1))


if __name__ == '__main__':
  unittest.main()
