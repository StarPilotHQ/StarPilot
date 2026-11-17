"""Actual ordinary-CC stock cancel timing and physical-credit boundary."""

import unittest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.gm import gmcan
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal
from opendbc.car.gm.tests.test_cc_gateway_stock import params, pt_frames
from opendbc.car.gm.values import DBC, ORDINARY_CC_CAR
from opendbc.safety.tests.libsafety import libsafety_py


class TestGmStockCancelDelay(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety

  def tearDown(self):
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  @staticmethod
  def packet(frame):
    return libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])

  def test_zero_delay_real_interface_and_expired_neutral_credit(self):
    for car in sorted(ORDINARY_CC_CAR):
      with self.subTest(car=car):
        cp = params(car)
        prepare_disable_longitudinal(cp, True)
        self.assertFalse(cp.openpilotLongitudinalControl)
        self.assertEqual(cp.safetyConfigs[0].safetyParam, 16)
        ci = CarInterface(cp)
        ci.update([])  # Register production lazy reads before the first stream.
        packer = CANPacker(DBC[car][Bus.pt])
        self.safety.init_tests()
        self.safety.set_alternative_experience(0)
        self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.gm, 16), 0)
        control = structs.CarControl()
        control.enabled = True
        control.longActive = True  # Caller demand cannot override stock ownership.
        control.latActive = False
        control.actuators.accel = 1.5
        self.assertEqual(ci.CC.cancel_counter, 0)

        def step(tick, *, cancel=False, button=None, packer=packer, ci=ci, control=control):
          stamp = 1_000_000_000 + tick * 10_000_000
          self.safety.set_timer(stamp // 1000)
          frames = [frame for frame in pt_frames(packer) if frame[0] not in (0x1E1, 0x34A)]
          frames.append(packer.make_can_msg('EBCMWheelSpdRear', 0, {
            'RLWheelSpd': 60, 'RRWheelSpd': 60, 'RLWheelDir': 1, 'RRWheelDir': 1,
          }))
          if button is not None:
            frames.append(gmcan.create_buttons(packer, 0, button, 1))
          cs = ci.update([(stamp, frames)])
          for frame in frames:
            self.assertTrue(self.safety.safety_rx_hook(self.packet(frame)), hex(frame[0]))
          self.safety.safety_tick()
          control.cruiseControl.cancel = cancel
          _, commands = ci.CC.update(control.as_reader(), ci.CS, stamp)
          return cs, [frame for frame in commands if frame[0] == 0x1E1]

        # Physical neutral buttons arrive every 30 ms, with a modulo-four
        # counter. Required PT sources remain current independently.
        for tick in range(15):
          cs, cancels = step(tick, button=(tick // 3) % 4 if tick % 3 == 0 else None)
          self.assertTrue(cs.canValid)
          self.assertFalse(cancels)
        accepted = None
        for elapsed in range(11):
          tick = 15 + elapsed
          cs, cancels = step(tick, cancel=True, button=(tick // 3) % 4 if tick % 3 == 0 else None)
          self.assertTrue(cs.canValid)
          self.assertTrue(self.safety.safety_config_valid())
          if elapsed < 10:  # Pinned Dom delay: ten complete 100-Hz frames.
            self.assertFalse(cancels, elapsed)
          else:
            self.assertEqual(len(cancels), 1)
            accepted = cancels[0]
            self.assertTrue(self.safety.safety_tx_hook(self.packet(accepted)))
            self.assertFalse(self.safety.safety_tx_hook(self.packet(accepted)))
        self.assertIsNotNone(accepted)
        _, cancels = step(26, cancel=True)  # Same physical observation: no second credit.
        self.assertFalse(cancels)
        # Renew unused credit, then stop only its physical source. Other PT
        # messages stay fresh; neither host nor native may use expired credit.
        _, cancels = step(27, cancel=False, button=1)
        self.assertFalse(cancels)
        for tick in range(28, 51):
          _, cancels = step(tick, cancel=True)
          self.assertFalse(cancels, tick)
        expired = gmcan.create_buttons(packer, 0, 2, 6)
        self.assertFalse(self.safety.safety_tx_hook(self.packet(expired)))


if __name__ == '__main__':
  unittest.main()
