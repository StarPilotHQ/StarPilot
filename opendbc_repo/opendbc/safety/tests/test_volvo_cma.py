"""Native CMA/SPA contracts, also collected by the safety unittest coverage gate.

The fixtures use the same DBCs and checksum encoders as the existing Volvo car
integration tests. Permissions come from physical RX, never a forced active flag.
"""
import unittest
from opendbc.safety.tests.native_callback import assert_native_callback_finishes

from opendbc.can import CANPacker
from opendbc.car.volvo import cma_can
from opendbc.car.volvo.helpers import (
  checksum_lca_2_message, checksum_2_0x69_message, checksum_lca_5_message,
  checksum_1_pscm_related_message, checksum_2_pscm_related_message,
)
from opendbc.safety.tests.libsafety import libsafety_py


RELAY_BUSES = {0x58: 2, 0x57: 2, 0x69: 2, 0x90: 2, 0x67: 2, 0x97: 2, 0x92: 2, 0x16: 0, 0x17: 0}


def checksummed(message):
  address, payload, bus = message
  data = bytearray(payload)
  if address == 0x69:
    data[6] = checksum_lca_2_message(data[0], data[5])
    data[2] = checksum_2_0x69_message(data[0], data[1], data[3], data[4])
  elif address == 0x67:
    data[2] = checksum_lca_5_message(data[0], data[1], data[3], data[4], data[5])
  elif address == 0x17:
    data[0] = checksum_1_pscm_related_message(data[1], data[2])
    data[3] = checksum_2_pscm_related_message(data[2])
  return address, bytes(data), bus


class TestVolvoCMASafety(unittest.TestCase):
  PARAM = 0

  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.mid = CANPacker('volvo_mid_1')
    self.pt = CANPacker('volvo_front_1_spa' if self.PARAM else 'volvo_front_1_cma')
    self.reset()

  def test_native_initialization_and_angle_callback_finish(self):
    address, data, bus = self.mid.make_can_msg('PSCM', 2, {'PSCM_ANGLE_SENSOR': .05596})
    assert_native_callback_finishes(self, 36, self.PARAM, address, bus, data)

  def reset(self):
    self.assertEqual(self.safety.set_safety_hooks(36, self.PARAM), 0)
    self.safety.init_tests()
    self.safety.set_timer(10000)

  def rx(self, message):
    address, data, bus = message
    return self.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))

  def tx(self, message):
    address, data, bus = message
    return self.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data))

  def sources(self, cruise=True, gear=3, sample=0, parity=0):
    definitions: list[tuple[str, int, dict[str, float]]] = [
      ('SPEED', 0, {'SPEED': 3000}), ('SPEED_2', 0, {}), ('GEAR_POSITION', 0, {'GEAR_POSITION': gear}),
      ('LCA', 0, {}), ('LCA_2', 0, {'COUNTER_1': (sample * 2 + parity) % 16, 'COUNTER_2': sample * 4 % 16}),
      ('LCA_3', 0, {}), ('LCA_4', 0, {}), ('LCA_5', 0, {}), ('LCA_6', 0, {}), ('LCA_7', 0, {}),
      ('PSCM', 2, {}), ('DRIVER_INPUT', 2, {}), ('EGSM', 2, {}),
      ('PSCM_RELATED', 2, {'NEW_SIGNAL_2': 8}), ('SAS', 2, {}),
    ]
    messages = [self.mid.make_can_msg(name, bus, values) for name, bus, values in definitions]
    messages.append(self.pt.make_can_msg('ECM_1', 1, {'ACCELERATOR_PEDAL_POS': 0 if self.PARAM else 20}))
    cruise_signal = 'CRUISE_CONTROL_SPA_ENABLED' if self.PARAM else 'CRUISE_CONTROL_ENABLED'
    messages.append(self.pt.make_can_msg('BUS1_CRUISE_CONTROL', 1, {cruise_signal: int(cruise)}))
    return [checksummed(message) for message in messages]

  def feed(self, **kwargs):
    incoming = self.sources(**kwargs)
    for message in incoming:
      self.assertTrue(self.rx(message), message)
    self.safety.safety_tick()
    self.assertTrue(self.safety.safety_config_valid())
    self.assertEqual(bool(self.safety.get_controls_allowed()), kwargs.get('cruise', True))
    return {address: (address, payload, bus) for address, payload, bus in incoming}

  def relay(self, source):
    address, data, _ = source
    return address, data, RELAY_BUSES[address]

  def lca_command(self, positive=614, negative=-614):
    return cma_can.create_lca_message(self.mid, True, 0, {
      'LCA_STEER': 0, 'OPAQUE_BYTE_0': 0, 'OPAQUE_BYTE_2': 0, 'OPAQUE_BYTE_7': 0,
    }, authority_pos=positive, authority_neg=negative)

  def angle_command(self, raw):
    # LCA_5_STEER is signed 15-bit, 0.05596 degrees/count, in bytes 6-7.
    data = bytearray(8)
    data[6:8] = (raw & 0x7FFF).to_bytes(2, 'big')
    return checksummed((0x67, data, 2))

  def test_rx_sensor_scales_and_profile_pedals(self):
    self.feed()
    self.assertAlmostEqual(self.safety.get_vehicle_speed_max(), 3000 * .003977, places=5)
    for raw in (-100, 100):
      for _ in range(6):
        self.assertTrue(self.rx(self.mid.make_can_msg('PSCM', 2, {'PSCM_ANGLE_SENSOR': raw * .05596})))
      self.assertEqual(self.safety.get_angle_meas_min(), raw)
      self.assertEqual(self.safety.get_angle_meas_max(), raw)
    for torque in (-10, 10):
      for _ in range(6):
        self.assertTrue(self.rx(self.mid.make_can_msg('DRIVER_INPUT', 2, {'STEERING_DRIVER_INPUT': torque})))
      self.assertEqual(self.safety.get_torque_driver_min(), torque)
      self.assertEqual(self.safety.get_torque_driver_max(), torque)
    threshold = 1 if self.PARAM else 21
    step = 1 / 256 if self.PARAM else 1
    for value, pressed in ((threshold + step, True), (threshold, False)):
      self.assertTrue(self.rx(self.pt.make_can_msg('ECM_1', 1, {'ACCELERATOR_PEDAL_POS': value})))
      self.assertEqual(bool(self.safety.get_gas_pressed_prev()), pressed)
    for sample, pressed in enumerate((True, False), 1):
      message = self.mid.make_can_msg('LCA_2', 0, {'COUNTER_1': sample * 2, 'COUNTER_2': sample * 4,
                                                'BRAKE_PEDAL_PRESSED_B': int(pressed)})
      self.assertTrue(self.rx(checksummed(message)))
      self.assertEqual(bool(self.safety.get_brake_pressed_prev()), pressed)
    self.assertFalse(self.safety.get_controls_allowed())

  def test_stock_cruise_withdrawal_and_reengagement(self):
    for enabled in (False, True, False, True):
      signal = 'CRUISE_CONTROL_SPA_ENABLED' if self.PARAM else 'CRUISE_CONTROL_ENABLED'
      self.assertTrue(self.rx(self.pt.make_can_msg('BUS1_CRUISE_CONTROL', 1, {signal: int(enabled)})))
      self.assertEqual(bool(self.safety.get_controls_allowed()), enabled)
    if not self.PARAM:
      self.assertTrue(self.rx(self.pt.make_can_msg('BUS1_CRUISE_CONTROL', 1, {})))
      self.assertTrue(self.rx(self.pt.make_can_msg('BUS1_CRUISE_CONTROL', 1, {'CRUISE_CONTROL_ENABLED_IDLE_TRAFFIC': 1})))
      self.assertTrue(self.safety.get_controls_allowed())

  def test_stock_only_when_cruise_withdrawn_and_original_age_expires(self):
    incoming = self.feed(cruise=False)
    for address in RELAY_BUSES:
      message = self.relay(incoming[address])
      self.assertTrue(self.tx(message))
      data = bytearray(message[1])
      data[0] ^= 1
      self.assertFalse(self.tx((address, data, message[2])))
    self.safety.set_timer(110000)
    for address in RELAY_BUSES:
      self.assertTrue(self.tx(self.relay(incoming[address])))
    self.safety.set_timer(110001)
    for address in RELAY_BUSES:
      self.assertFalse(self.tx(self.relay(incoming[address])))

  def test_allowlist_pins_bus_and_length(self):
    incoming = self.feed(cruise=False)
    for address, expected_bus in RELAY_BUSES.items():
      for bus in range(8):
        self.assertEqual(bool(self.tx((address, incoming[address][1], bus))), bus == expected_bus)
        for length in range(8):
          self.assertFalse(self.tx((address, incoming[address][1][:length], bus)))
    for address in (0x15, 0x55, 0x60, 0x68, 0x80, 0x45):
      for bus in range(3):
        self.assertFalse(self.tx((address, bytes(8), bus)))

  def test_active_native_source_bound_commands(self):
    incoming = self.feed()
    for address in RELAY_BUSES:
      self.assertTrue(self.tx(self.relay(incoming[address])))
    self.assertTrue(self.tx(self.lca_command()))
    self.assertTrue(self.tx(self.angle_command(1)))
    # These two hands-on relay payloads are emitted by create_pscm_message.
    for hands_a, hands_b in ((195, 186), (249, 154)):
      message = self.mid.make_can_msg('PSCM', 0, {'HANDS_ON_STEERING_WHEEL_A': hands_a,
                                               'HANDS_ON_STEERING_WHEEL_B': hands_b})
      self.assertTrue(self.tx(message))
    # Counter replication may advance, but its observed echo must be preserved or zeroed.
    for counter in range(15):
      message = self.mid.make_can_msg('PSCM_RELATED', 0, {'NEW_SIGNAL_2': 8,
        'SIG1_BYTE_1_HI_NIBBLE': counter, 'SIG1_REPLICA_BYTE_2_LO_NIBLE': counter})
      self.assertTrue(self.tx(checksummed(message)))

  def test_opaque_stock_fields_cannot_be_forged_with_recomputed_checksums(self):
    incoming = self.feed()
    # Start from accepted active templates, so an opaque mutation cannot be
    # rejected merely because the stock frame lacks the fixed active fields.
    active = {
      0x58: self.lca_command(),
      0x57: cma_can.create_lca_3_message(self.mid, True, 0, {'SPEED_A': 0, 'SPEED_B': 0, 'NEW_SIGNAL_9': 0}, 0),
      0x69: checksummed(self.mid.make_can_msg('LCA_2', 2, {'BYTE_0': 24, 'PILOT_ASSIST_ENGAGED': 1, 'NEW_SIGNAL_3': 3})),
      0x90: self.mid.make_can_msg('LCA_4', 2, {'LCA_ENABLE': 3}),
      0x67: self.angle_command(1),
      0x16: self.mid.make_can_msg('PSCM', 0, {'HANDS_ON_STEERING_WHEEL_A': 195, 'HANDS_ON_STEERING_WHEEL_B': 186}),
      0x17: checksummed(self.mid.make_can_msg('PSCM_RELATED', 0, {'NEW_SIGNAL_2': 8,
        'SIG1_BYTE_1_HI_NIBBLE': 1, 'SIG1_REPLICA_BYTE_2_LO_NIBLE': 1})),
    }
    for address, byte, mask in ((0x58, 6, 1), (0x57, 2, 1), (0x69, 5, 0x40), (0x90, 4, 1),
                                (0x67, 0, 1), (0x97, 0, 1), (0x92, 0, 1), (0x16, 7, 1), (0x17, 6, 1)):
      with self.subTest(address=address):
        baseline = active.get(address, self.relay(incoming[address]))
        self.assertTrue(self.tx(baseline))
        data = bytearray(baseline[1])
        data[byte] ^= mask
        self.assertFalse(self.tx(checksummed((address, data, RELAY_BUSES[address]))))

  def test_torque_authority_limits(self):
    self.feed()
    for positive, negative, allowed in ((0, 0, True), (614, -614, True), (615, -614, False),
                                        (614, -615, False), (-1, 0, False), (0, 1, False)):
      self.assertEqual(bool(self.tx(self.lca_command(positive, negative))), allowed)

  def test_angle_absolute_and_rate_limits(self):
    for raw in (-9651, 9651):
      self.reset()
      self.feed()
      self.safety.set_desired_angle_last(raw)
      self.assertFalse(self.tx(self.angle_command(raw)))
    for raw in (-1, 1):
      self.reset()
      self.feed()
      self.assertTrue(self.tx(self.angle_command(raw)))
    self.reset()
    self.feed()
    self.assertFalse(self.tx(self.angle_command(1000)))

  def test_pscm_relay_cannot_invent_measured_angle(self):
    self.feed()
    for raw in (-55, -54, 54, 55):
      self.assertTrue(self.rx(self.mid.make_can_msg('PSCM', 2, {'PSCM_ANGLE_SENSOR': raw * .05596})))
      # Keep the source-bound angle intact and isolate the measurement guard.
      # The standard measurement fixture does not grant control permission.
      self.safety.set_angle_meas(0, 0)
      message = self.mid.make_can_msg('PSCM', 0, {'PSCM_ANGLE_SENSOR': raw * .05596,
        'HANDS_ON_STEERING_WHEEL_A': 195, 'HANDS_ON_STEERING_WHEEL_B': 186})
      self.assertEqual(bool(self.tx(message)), abs(raw) <= 54)
    for hands_a, hands_b in ((195, 154), (249, 186), (0, 1)):
      self.assertFalse(self.tx(self.mid.make_can_msg('PSCM', 0, {
        'HANDS_ON_STEERING_WHEEL_A': hands_a, 'HANDS_ON_STEERING_WHEEL_B': hands_b})))

  def test_gear_is_physical_current_and_drive_only(self):
    for gear in range(8):
      with self.subTest(gear=gear):
        self.reset()
        incoming = self.feed(gear=gear)
        self.assertEqual(bool(self.tx(self.lca_command())), gear in (3, 4))
        self.assertTrue(self.tx(self.relay(incoming[0x58])))
    self.reset()
    self.feed()
    self.safety.set_timer(110001)
    for message in self.sources(sample=1):
      if message[0] != 0x80:
        self.assertTrue(self.rx(message))
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertFalse(self.tx(self.lca_command()))

  def test_each_known_checksum_rejects_corrupt_rx_and_tx(self):
    for address, index in ((0x69, 6), (0x69, 2), (0x67, 2), (0x17, 0), (0x17, 3)):
      with self.subTest(address=address, index=index):
        self.reset()
        incoming = self.feed()
        data = bytearray(incoming[address][1])
        data[index] ^= 1
        self.assertFalse(self.tx((address, data, RELAY_BUSES[address])))
        self.assertFalse(self.rx((address, data, incoming[address][2])))
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertFalse(self.tx(self.lca_command()))
        self.assertTrue(self.tx(self.relay(incoming[0x58])))
        self.safety.safety_tick()
        self.assertFalse(self.safety.safety_config_valid())

  def test_unknown_pscm_counter_domains_rejected_despite_correct_checksum(self):
    for first, second in ((0x10, 0x80), (0xF0, 0x8F), (8, 0x80), (0, 0)):
      self.reset()
      self.feed()
      data = bytearray(8)
      data[1:3] = bytes((first, second))
      message = checksummed((0x17, data, 0))
      self.assertFalse(self.tx(message))
      self.assertFalse(self.rx((message[0], message[1], 2)))

  def test_documented_lca2_counter_wraps_but_frozen_counter_expires(self):
    for parity in (0, 1):
      self.reset()
      self.feed(parity=parity)
      for sample in range(1, 25):
        self.safety.set_timer(10000 + sample * 20000)
        self.feed(sample=sample, parity=parity)
      frozen = next(message for message in self.sources(sample=24, parity=parity) if message[0] == 0x69)
      for sample in range(25, 35):
        self.safety.set_timer(10000 + sample * 20000)
        for message in self.sources(sample=sample, parity=parity):
          if message[0] == 0x69:
            self.rx(frozen)  # The shared wrong-counter tolerance is eventually exhausted.
          else:
            self.assertTrue(self.rx(message))
        self.safety.safety_tick()
      self.assertFalse(self.safety.safety_config_valid())
      self.assertFalse(self.safety.get_controls_allowed())

  def test_lca2_phase_must_come_from_checksum_valid_physical_rx(self):
    for index, mask in ((1, 1), (5, 1), (5, 4)):
      self.reset()
      incoming = self.feed()
      data = bytearray(incoming[0x69][1])
      data[index] ^= mask
      self.assertFalse(self.rx(checksummed((0x69, data, 0))))
      self.assertFalse(self.safety.get_controls_allowed())
    self.reset()
    message = next(message for message in self.sources() if message[0] == 0x69)
    data = bytearray(message[1])
    data[6] ^= 1
    self.assertFalse(self.rx((0x69, data, 0)))
    self.assertFalse(self.tx(self.relay(message)))
    self.feed(parity=1)

  def test_existing_lca2_special_case_checksum(self):
    message = self.mid.make_can_msg('LCA_2', 0, {'BRAKE_PEDAL_PRESSED_A': 0})
    self.assertEqual(message[1][0], 0)
    self.assertEqual(message[1][5], 128)
    self.assertEqual(checksummed(message)[1][6], 0)
    self.assertTrue(self.rx(checksummed(message)))

  def test_every_required_receiver_needed_after_mode_reset(self):
    for missing in range(17):
      self.reset()
      incoming = self.feed()
      self.assertTrue(self.tx(self.lca_command()))
      self.reset()
      self.assertFalse(self.tx(self.relay(incoming[0x58])))
      messages = self.sources()
      for index, message in enumerate(messages):
        if index != missing:
          self.assertTrue(self.rx(message))
      self.assertFalse(self.safety.safety_config_valid())
      self.assertFalse(self.tx(self.lca_command()))
      self.assertTrue(self.rx(messages[missing]))
      self.assertTrue(self.safety.safety_config_valid())
      self.assertTrue(self.tx(self.lca_command()))

  def test_required_receiver_loss_rejects_modified_commands(self):
    for omitted in range(17):
      self.reset()
      self.feed()
      for sample in range(1, 57):
        self.safety.set_timer(10000 + sample * 20000)
        for index, message in enumerate(self.sources(sample=sample)):
          if index != omitted:
            self.assertTrue(self.rx(message))
        self.safety.safety_tick()
      self.assertFalse(self.safety.safety_config_valid())
      self.assertFalse(self.safety.get_controls_allowed())
      self.assertFalse(self.tx(self.lca_command()))

  def test_stock_relay_history_does_not_cross_mode_reset(self):
    incoming = self.feed(cruise=False)
    first = incoming[0x97]
    for value in range(1, 5):
      message = (0x97, bytes([value]) + bytes(7), 0)
      self.assertTrue(self.rx(message))
      self.assertTrue(self.tx(self.relay(message)))
    self.assertFalse(self.tx(self.relay(first)))
    self.reset()
    self.assertFalse(self.tx(self.relay(message)))

  def test_relay_malfunction_and_forwarding(self):
    self.feed()
    for bus in (0, 2):
      for address in (*RELAY_BUSES, 0x60):
        blocked = RELAY_BUSES.get(address) == 2 - bus
        self.assertEqual(self.safety.safety_fwd_hook(bus, address), -1 if blocked else 2 - bus)
    for address, bus in RELAY_BUSES.items():
      self.reset()
      self.assertTrue(self.rx((address, bytes(8), bus)))
      self.assertTrue(self.safety.get_relay_malfunction())
      self.assertFalse(self.tx(self.lca_command()))
      self.assertEqual(self.safety.safety_fwd_hook(0, 0x60), -1)


class TestVolvoSPASafety(TestVolvoCMASafety):
  PARAM = 1


if __name__ == '__main__':
  unittest.main()
