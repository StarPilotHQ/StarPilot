import unittest
from types import SimpleNamespace

from opendbc.car import structs
from opendbc.car.gm import gmcan
from opendbc.car.gm.carcontroller import CarController
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.tests.test_bolt_pedal import params
from opendbc.car.gm.values import CAR, DBC, PEDAL_BOLT_CAR


class TestBoltPedalRejections(unittest.TestCase):
  def setup_controller(self, candidate=CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL):
    cp = params(candidate, pedal=True)
    ci = CarInterface(cp)
    controller = ci.CC
    controller.frame = controller.last_steer_frame = 4
    out = structs.CarState(vEgo=1.5, aEgo=0.)
    out.gearShifter = structs.CarState.GearShifter.low
    out.cruiseState.available = True
    cs = SimpleNamespace(stock_acc_status_ts_nanos=1_000_000_000, out=out, pedal_sensor_healthy=True, pedal_sensor_ts_nanos=1_000_000_000,
                         bolt_pedal_standstill_ts_nanos=1_000_000_000,
                         bolt_pedal_main_ts_nanos=1_000_000_000, bolt_pedal_gear_ts_nanos=1_000_000_000,
                         cam_lka_steering_cmd_counter=0, loopback_lka_steering_cmd_updated=False,
                         loopback_lka_steering_cmd_ts_nanos=1_000_000_000,
                         pt_lka_steering_cmd_counter=0, buttons_counter=0)
    cc = structs.CarControl(enabled=True, longActive=True)
    cc.actuators.accel = .3
    cc.actuators.longControlState = structs.CarControl.Actuators.LongControlState.pid
    return ci, controller, cs, cc

  def test_actual_caller_retries_rejected_active_counter_as_zero(self):
    for candidate in PEDAL_BOLT_CAR:
      with self.subTest(candidate=candidate):
        ci, controller, cs, cc = self.setup_controller(candidate)
        _, commands = controller.update(cc.as_reader(), cs, 1_000_000_000)
        command = next(m for m in commands if m[0] == 0x200)
        self.assertTrue(command.dat[4] & 0x80)
        ci.update([(1_004_000_000, [(command.address, command.dat, 192)])])
        _, commands = controller.update(cc.as_reader(), cs, 1_010_000_000)
        pedal = [m for m in commands if m[0] == 0x200]
        self.assertEqual(len(pedal), 1)
        self.assertEqual(pedal[0].dat, gmcan.create_pedal_command(controller.packer_pt, 0., command.dat[4] & 15).dat)
        _, commands = controller.update(cc.as_reader(), cs, 1_020_000_000)
        self.assertFalse(any(m[0] == 0x200 for m in commands))

  def test_receipts_require_exact_current_rejected_command(self):
    for kind in ('accepted', 'bus2', 'different', 'older', 'expired', 'future', 'superseded'):
      with self.subTest(kind=kind):
        ci, controller, cs, cc = self.setup_controller()
        _, commands = controller.update(cc.as_reader(), cs, 1_000_000_000)
        command = next(m for m in commands if m[0] == 0x200)
        raw = bytearray(command.dat)
        bus, stamp = 192, 1_004_000_000
        if kind == 'accepted':
          bus = 128
        elif kind == 'bus2':
          bus = 194
        elif kind == 'different':
          raw[0] ^= 1
        elif kind == 'older':
          stamp = 999_999_999
        elif kind == 'expired':
          stamp = 1_040_000_001
        elif kind == 'future':
          stamp = 1_030_000_000
        ci.update([(stamp, [(0x200, bytes(raw), bus)])])
        if kind == 'superseded':
          controller.bolt_pedal_last_command = None
        _, commands = controller.update(cc.as_reader(), cs, 1_010_000_000)
        self.assertFalse(any(m[0] == 0x200 for m in commands))

  def test_recovery_expiry_and_disabled_commands_do_not_retry(self):
    for disabled, now in ((False, 1_040_000_001), (True, 1_010_000_000)):
      ci, controller, cs, cc = self.setup_controller()
      cc.longActive = not disabled
      _, commands = controller.update(cc.as_reader(), cs, 1_000_000_000)
      command = next(m for m in commands if m[0] == 0x200)
      ci.update([(1_004_000_000, [(0x200, command.dat, 192)])])
      _, commands = controller.update(cc.as_reader(), cs, now)
      self.assertFalse(any(m[0] == 0x200 for m in commands))

  def test_recovery_precedes_next_scheduled_counter(self):
    ci, controller, cs, cc = self.setup_controller()
    _, commands = controller.update(cc.as_reader(), cs, 1_000_000_000)
    command = next(m for m in commands if m[0] == 0x200)
    ci.update([(1_004_000_000, [(0x200, command.dat, 192)])])
    controller.frame = controller.last_steer_frame = 8
    _, commands = controller.update(cc.as_reader(), cs, 1_040_000_000)
    pedal = [m for m in commands if m[0] == 0x200]
    self.assertEqual(len(pedal), 2)
    self.assertFalse(pedal[0].dat[4] & 128)
    self.assertEqual(pedal[0].dat[4] & 15, command.dat[4] & 15)
    self.assertTrue(pedal[1].dat[4] & 128)
    self.assertEqual(pedal[1].dat[4] & 15, (command.dat[4] + 1) & 15)

  def test_stock_and_volt_owners_do_not_admit_recovery(self):
    for candidate in (CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_2019, CAR.CHEVROLET_BOLT_EUV):
      cp = params(candidate, pedal=False)
      self.assertFalse(getattr(CarController(DBC[candidate], cp), 'bolt_pedal_recovery', False))


if __name__ == '__main__':
  unittest.main()
