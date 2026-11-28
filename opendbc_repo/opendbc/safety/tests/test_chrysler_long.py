"""Chrysler WK2 alpha-long DAS_3 TX envelopes and button engage (safetyParam LONG_CONTROL=16)."""

import unittest

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.chrysler.chryslercan import create_das_3_long_command, chrysler_checksum
from opendbc.car.chrysler.values import WK2_2019_TORQUE_MIN, ChryslerSafetyFlags
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
      'COLLISION_BRK_PREP': 0,
      'ACC_FAULTED': 0,
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

  def long_cmd(self, accel, offset=2, *, torque=100.0, stopping=False, starting=False):
    """Braking frame for accel < 0, torque frame otherwise (as the WK2 controller sends)."""
    return create_das_3_long_command(
      self.packer, offset, self.stock, braking=accel < 0.0, accel=accel, torque=torque,
      stopping=stopping, starting=starting,
    )

  def resign(self, frame, edit):
    addr, data, bus = frame
    data = bytearray(data)
    edit(data)
    data[-1] = chrysler_checksum(addr, None, data)
    return addr, bytes(data), bus

  def send_fcw(self, active):
    msg = self.packer.make_can_msg('DAS_ALERTS', 0, {'FCW_ALERT': int(active)})
    self.safety.safety_rx_hook(self.packet(msg))

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

  def test_envelope_accepts_stock_measured_modes(self):
    tick = self.engage()
    requests = [(a, 0.0) for a in (-3.5, -2.0, -0.5, -0.001)]
    requests += [(0.0, t) for t in (WK2_2019_TORQUE_MIN, 0.0, 175.0, 380.0)]
    for accel, torque in requests:
      self.feed_stock(tick)
      tick += 1
      frame = self.long_cmd(accel, torque=torque)
      self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)), frame[1].hex())

  def test_envelope_denials(self):
    tick = self.engage()
    self.feed_stock(tick)
    denied = {
      'decel below -3.5': self.long_cmd(-3.6),
      'torque above 380 Nm': self.long_cmd(0.5, torque=400.0),
      'torque below -52 Nm': self.long_cmd(0.5, torque=-60.0),
      # torque-mode ACC_DECEL other than the stock idle value (pre-fix behaviour)
      'torque mode with real decel': self.resign(self.long_cmd(0.5), lambda d: d.__setitem__(3, d[3] - 1)),
      'braking with positive torque': self.resign(self.long_cmd(-1.0), lambda d: d.__setitem__(0, (d[0] & 0xE0) | 0x0A)),
      'braking with torque-max bit': self.resign(self.long_cmd(-1.0), lambda d: d.__setitem__(0, d[0] | 0x80)),
      'decel request 2': self.resign(self.long_cmd(-1.0), lambda d: d.__setitem__(4, (d[4] & 0x8F) | 0x20)),
      'collision prep set by OP': self.resign(self.long_cmd(-1.0), lambda d: d.__setitem__(6, d[6] | 1)),
    }
    for name, frame in denied.items():
      self.assertFalse(self.safety.safety_tx_hook(self.packet(frame)), name)

  def test_yields_to_oem_fcw_and_aeb(self):
    tick = self.engage()
    self.feed_stock(tick)
    self.assertTrue(self.safety.safety_tx_hook(self.packet(self.long_cmd(0.5))))
    self.send_fcw(True)
    self.assertFalse(self.safety.safety_tx_hook(self.packet(self.long_cmd(-1.0))))
    self.send_fcw(False)
    self.assertTrue(self.safety.safety_tx_hook(self.packet(self.long_cmd(-1.0))))
    for name, stock in (('collision prep', {'COLLISION_BRK_PREP': 1}),
                        ('acc fault', {'ACC_FAULTED': 1}),
                        ('stock braking beyond envelope', {'ACC_ACTIVE': 1, 'ACC_DECEL_REQ': 1, 'ACC_DECEL': -4.0}),
                        ('stock AEB-style braking without ACC_ACTIVE', {'ACC_DECEL_REQ': 1, 'ACC_DECEL': -4.0})):
      saved = dict(self.stock)
      self.stock.update(stock)
      tick += 1
      self.feed_stock(tick)
      self.assertFalse(self.safety.safety_tx_hook(self.packet(self.long_cmd(-1.0))), name)
      self.stock = saved
    tick += 1
    self.feed_stock(tick)
    self.assertTrue(self.safety.safety_tx_hook(self.packet(self.long_cmd(-1.0))))

  def test_counter_and_checksum_denials(self):
    tick = self.engage()
    self.feed_stock(tick)
    for offset in (0, 1, 4):
      self.assertFalse(self.safety.safety_tx_hook(self.packet(self.long_cmd(0.5, offset=offset))), offset)
    addr, data, bus = self.long_cmd(0.5)
    bad = bytearray(data)
    bad[-1] ^= 1
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
