"""Chrysler WK2 alpha-long DAS_3 TX envelopes and button engage (safetyParam LONG_CONTROL=16)."""

import unittest

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.chrysler.chryslercan import create_das_3_long_command, chrysler_checksum
from opendbc.car.chrysler.values import ChryslerSafetyFlags
from opendbc.safety.tests.libsafety import libsafety_py


class TestChryslerLongSafety(unittest.TestCase):
  SAFETY_PARAM = int(ChryslerSafetyFlags.LONG_CONTROL)

  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.safety.init_tests()
    self.safety.set_alternative_experience(0)
    self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.chrysler, self.SAFETY_PARAM), 0)
    self.packer = CANPacker('chrysler_pacifica_2017_hybrid_generated')
    self.stock = {
      'ACC_AVAILABLE': 1,
      'ACC_ACTIVE': 0,
      'ACC_STANDSTILL': 0,
      'ACC_GO': 0,
      'ACC_DECEL': 0.0,
      'ACC_DECEL_REQ': 0,
      'ENGINE_TORQUE_REQUEST': -52.0,
      'ENGINE_TORQUE_REQUEST_MAX': 0,
      'ACC_BRK_PREP': 0,
      'COUNTER': 0,
    }

  def packet(self, frame):
    return libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])

  def feed_stock(self, tick, *, gas=0.0, brake=0, speed=1.0):
    self.safety.set_timer(tick * 10_000)
    self.stock['COUNTER'] = tick % 16
    for name, values in [
      ('EPS_2', {}),
      ('ESP_1', {'Brake_Pedal_State': brake}),
      ('SPEED_1', {'SPEED_LEFT': speed, 'SPEED_RIGHT': speed}),
      ('ECM_5', {'Accelerator_Position': gas}),
      ('DAS_3', self.stock),
    ]:
      self.assertTrue(self.safety.safety_rx_hook(self.packet(self.packer.make_can_msg(name, 0, values))))
    self.safety.safety_tick()

  def warm(self, ticks=20):
    for tick in range(ticks):
      self.feed_stock(tick)
    return ticks

  def engage(self, start_tick=0):
    end = start_tick
    for tick in range(start_tick, start_tick + 20):
      self.feed_stock(tick)
      end = tick + 1
    button = self.packer.make_can_msg('CRUISE_BUTTONS', 0, {'ACC_Accel': 1})
    self.safety.safety_rx_hook(self.packet(button))
    self.assertTrue(self.safety.get_controls_allowed())
    return end

  def long_cmd(self, accel, offset=2, *, stopping=False, starting=False):
    return create_das_3_long_command(
      self.packer, offset, accel, self.stock, long_active=True, stopping=stopping, starting=starting,
    )

  def test_button_engage_and_cancel(self):
    self.warm()
    self.assertFalse(self.safety.get_controls_allowed())
    for signal in ('ACC_Accel', 'ACC_Decel', 'ACC_Resume'):
      self.safety.set_controls_allowed(False)
      self.safety.safety_rx_hook(self.packet(self.packer.make_can_msg('CRUISE_BUTTONS', 0, {signal: 1})))
      self.assertTrue(self.safety.get_controls_allowed(), signal)
    self.safety.safety_rx_hook(self.packet(self.packer.make_can_msg('CRUISE_BUTTONS', 0, {'ACC_Cancel': 1})))
    self.assertFalse(self.safety.get_controls_allowed())

  def test_pcm_cruise_does_not_engage_long_mode(self):
    self.warm()
    self.stock['ACC_ACTIVE'] = 1
    self.feed_stock(20)
    self.assertFalse(self.safety.get_controls_allowed())

  def test_accel_envelope_accepts_measured_range(self):
    tick = self.engage()
    for accel in (-1.8, -0.5, 0.0, 1.0, 4.0):
      self.feed_stock(tick)
      tick += 1
      self.assertTrue(self.safety.safety_tx_hook(self.packet(self.long_cmd(accel))), accel)

  def test_counter_and_checksum_denials(self):
    tick = self.engage()
    self.feed_stock(tick)
    for offset in (0, 1, 4):
      self.assertFalse(self.safety.safety_tx_hook(self.packet(self.long_cmd(0.5, offset=offset))), offset)
    addr, data, bus = self.long_cmd(0.5)
    bad = bytearray(data)
    bad[-1] ^= 1
    self.assertFalse(self.safety.safety_tx_hook(self.packet((addr, bytes(bad), bus))))

  def test_torque_over_envelope_denied(self):
    tick = self.engage()
    self.feed_stock(tick)
    addr, data, bus = self.long_cmd(0.5)
    bad = bytearray(data)
    # Force ENGINE_TORQUE_REQUEST raw above 3072 (+268 Nm): set high 5 bits + 0xFF
    bad[0] = (bad[0] & 0xE0) | 0x1F
    bad[1] = 0xFF  # raw 8191 >> max 3072
    bad[-1] = chrysler_checksum(addr, None, bad)
    self.assertFalse(self.safety.safety_tx_hook(self.packet((addr, bytes(bad), bus))))

  def test_requires_controls_allowed_and_fresh_stock(self):
    self.warm()
    self.assertFalse(self.safety.safety_tx_hook(self.packet(self.long_cmd(0.5))))
    tick = self.engage()
    self.feed_stock(tick)
    last_us = tick * 10_000
    self.assertTrue(self.safety.safety_tx_hook(self.packet(self.long_cmd(0.5))))
    self.safety.set_timer(last_us + 100_001)
    self.assertFalse(self.safety.safety_tx_hook(self.packet(self.long_cmd(0.5))))

  def test_hold_word_still_isolated(self):
    self.safety.init_tests()
    self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.chrysler, 4), 0)
    self.safety.safety_rx_hook(self.packet(self.packer.make_can_msg('CRUISE_BUTTONS', 0, {'ACC_Accel': 1})))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(self.long_cmd(0.5))))

  def test_stock_param_rejects_long_tx(self):
    self.safety.init_tests()
    self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.chrysler, 0), 0)
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(self.packet(self.long_cmd(0.5))))


if __name__ == '__main__':
  unittest.main()
