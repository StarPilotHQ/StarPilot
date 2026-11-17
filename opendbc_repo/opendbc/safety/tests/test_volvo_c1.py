import unittest
import pytest
from opendbc.can import CANPacker
from opendbc.safety import LEN_TO_DLC
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.car.volvo import volvocan as codec

ffi = libsafety_py.ffi
lib = libsafety_py.libsafety
packer = CANPacker('volvo_v40_2017_pt')


def packet(message):
  address, data, bus = message
  p = libsafety_py.new_CANPacket()
  p.addr = address
  p.bus = bus
  p.data_len_code = LEN_TO_DLC[len(data)]
  p.data = bytes(data)
  return p


def reset():
  assert lib.set_safety_hooks(36, 2) == 0
  lib.set_timer(0)


def rx(name, bus, values):
  return lib.safety_rx_hook(packet(packer.make_can_msg(name, bus, values)))


class TestVolvoC1Safety(unittest.TestCase):
  def test_exhaustive_only_assigned_profiles_are_admitted(self):
    for param in range(65536):
      status = lib.set_safety_hooks(36, param)
      assert status == (0 if param in (0, 1, 2, 5) else -1)
      if param not in (0, 1, 2, 5):
        lib.set_controls_allowed(True)
        assert not lib.safety_tx_hook(packet(codec.create_c1_cancel(packer)))

  def test_wrong_bus_and_length(self):
    for kind in ['steer', 'relay', 'cancel']:
      for bus in range(8):
        with self.subTest(kind=kind, bus=bus):
          reset()
          lib.set_angle_meas(0, 0)
          message = (
            codec.create_c1_steering_control(packer, 0, False)
            if kind == 'steer'
            else codec.create_c1_pscm_message(packer, {'SteeringAngleServo': 0, 'byte0': 0, 'byte3': 0, 'byte4': 0, 'byte7': 0, 'LKAActive': 0})
            if kind == 'relay'
            else codec.create_c1_cancel(packer)
          )
          address, data, expected_bus = message
          assert bool(lib.safety_tx_hook(packet((address, data, bus)))) == (bus == expected_bus)
          for length in range(8):
            assert not lib.safety_tx_hook(packet((address, data[:length], bus)))

  def test_literal_rx_speed_angle_gas_brake_cruise(self):
    reset()
    assert rx('VehicleSpeed1', 0, {'VehicleSpeed': 72})
    assert lib.get_vehicle_speed_max() == pytest.approx(20)
    assert rx('PSCM1', 0, {'SteeringAngleServo': 12.5})
    assert lib.get_angle_meas_max() == 284
    rx('PedalandBrake', 0, {'AccPedal': 5.1})
    assert lib.get_gas_pressed_prev()
    rx('PedalandBrake', 0, {'AccPedal': 5})
    assert not lib.get_gas_pressed_prev()
    for name in ('BrakePedalActive', 'BrakePedalActive2'):
      rx('PedalandBrake', 0, {name: 1})
      assert lib.get_brake_pressed_prev()
      rx('PedalandBrake', 0, {})
      assert not lib.get_brake_pressed_prev()
    rx('FSM0', 2, {'ACCStatusActive': 1})
    assert lib.get_controls_allowed()
    rx('FSM0', 2, {'ACCStatusActive': 0})
    assert not lib.get_controls_allowed()

  def test_positive_feed_then_required_rx_loss(self):
    for missing in ['PSCM1', 'FSM0', 'PedalandBrake', 'VehicleSpeed1']:
      with self.subTest(missing=missing):
        reset()
        for index in range(1, 221):
          lib.set_timer(index * 10000)
          for name, bus, values in [
            ('PSCM1', 0, {'SteeringAngleServo': 0}),
            ('FSM0', 2, {'ACCStatusActive': 1}),
            ('PedalandBrake', 0, {}),
            ('VehicleSpeed1', 0, {'VehicleSpeed': 72}),
          ]:
            if index <= 20 or name != missing:
              assert rx(name, bus, values)
          lib.safety_tick()
          if index == 20:
            assert lib.safety_config_valid() and lib.get_controls_allowed()
        assert not lib.safety_config_valid() and (not lib.get_controls_allowed())

  def test_stock_forwarding_and_cancel_only_fields(self):
    reset()
    for bus in (0, 2):
      for address in (16, 48, 208, 293, 85, 336):
        blocked = bus == 2 and address == 208 or (bus == 0 and address == 293)
        assert lib.safety_fwd_hook(bus, address) == (-1 if blocked else 2 - bus)
    assert lib.safety_tx_hook(packet(codec.create_c1_cancel(packer)))
    for name in ['ACCOnOffBtn', 'ACCSetBtn', 'ACCResumeBtn', 'ACCMinusBtn', 'TimeGapIncreaseBtn', 'TimeGapDecreaseBtn']:
      assert not lib.safety_tx_hook(packet(packer.make_can_msg('CCButtons', 0, {name: 1})))

  def test_fixed_payload_checksum_direction_and_inactive_neutral(self):
    reset()
    lib.set_angle_meas(0, 0)
    good = codec.create_c1_steering_control(packer, 0, False)
    assert lib.safety_tx_hook(packet(good))
    assert not lib.safety_tx_hook(packet(codec.create_c1_steering_control(packer, 10, False)))
    for index in range(8):
      reset()
      lib.set_angle_meas(0, 0)
      changed = bytearray(good[1])
      changed[index] ^= 64
      assert not lib.safety_tx_hook(packet((good[0], changed, good[2])))
    for direction in (1, 2):
      data = bytearray(good[1])
      data[7] = data[7] & ~3 | direction
      data[6] = codec.create_c1_checksum(data)
      assert not lib.safety_tx_hook(packet((good[0], data, good[2])))

  def test_measured_angle_error_converges_with_original_envelope(self):
    reset()
    lib.set_controls_allowed(True)
    lib.set_angle_meas(0, 0)
    for _ in range(6):
      rx('VehicleSpeed1', 0, {'VehicleSpeed': 72})
    lib.set_controls_allowed(True)
    for degrees in (1, 10, 20):
      lib.set_desired_angle_last(round(degrees * 22.753128))
      assert lib.safety_tx_hook(packet(codec.create_c1_steering_control(packer, degrees, True)))
    lib.set_desired_angle_last(round(30 * 22.753128))
    assert not lib.safety_tx_hook(packet(codec.create_c1_steering_control(packer, 30, True)))
    lib.set_controls_allowed(True)
    lib.set_desired_angle_last(round(30 * 22.753128))
    assert lib.safety_tx_hook(packet(codec.create_c1_steering_control(packer, 29.8, True)))
    lib.set_controls_allowed(True)
    lib.set_desired_angle_last(8189)
    assert not lib.safety_tx_hook(packet(codec.create_c1_steering_control(packer, 360, True)))

  def test_relay_malfunction_blocks_tx_and_forward(self):
    for bus, address in [(0, 208), (2, 293)]:
      with self.subTest(bus=bus, address=address):
        reset()
        lib.init_tests()
        assert not lib.get_relay_malfunction()
        lib.safety_rx_hook(packet((address, bytes(8), bus)))
        assert lib.get_relay_malfunction()
        assert not lib.safety_tx_hook(packet(codec.create_c1_cancel(packer)))
        assert lib.safety_fwd_hook(0, 336) == -1
        assert lib.safety_fwd_hook(2, 336) == -1

  def test_relay_angle_cannot_depart_from_measured_sample(self):
    reset()
    lib.set_angle_meas(100, 100)
    for raw in (98, 100, 102):
      data = bytearray(8)
      data[1] = 7
      data[2] = 208
      encoded = raw + 32768
      data[5] = encoded >> 8
      data[6] = encoded & 255
      assert lib.safety_tx_hook(packet((293, data, 2)))
    for raw in (97, 103):
      data = bytearray(8)
      data[1] = 7
      data[2] = 208
      encoded = raw + 32768
      data[5] = encoded >> 8
      data[6] = encoded & 255
      assert not lib.safety_tx_hook(packet((293, data, 2)))

  def test_signed_measurement_and_neutral_command_share_scale(self):
    for degrees, raw in [(12.5, 284), (-12.5, -284)]:
      with self.subTest(degrees=degrees, raw=raw):
        reset()
        for _ in range(6):
          assert rx('PSCM1', 0, {'SteeringAngleServo': degrees})
        assert lib.get_angle_meas_min() == lib.get_angle_meas_max() == raw
        assert lib.safety_tx_hook(packet(codec.create_c1_steering_control(packer, degrees, False)))

  def test_original_pedal_permission_contract(self):
    for values, allowed in [({'AccPedal': 5.1}, True), ({'BrakePedalActive': 1}, False), ({'BrakePedalActive2': 1}, False)]:
      with self.subTest(values=values, allowed=allowed):
        reset()
        rx('VehicleSpeed1', 0, {'VehicleSpeed': 72})
        lib.set_controls_allowed(True)
        rx('PedalandBrake', 0, values)
        assert bool(lib.get_controls_allowed()) == allowed

  def test_relay_zero_torque_and_mask_are_enforced(self):
    for active in range(16):
      with self.subTest(active=active):
        reset()
        lib.set_angle_meas(0, 0)
        stock = {'SteeringAngleServo': 0, 'byte0': 17, 'byte3': 29, 'byte4': 43, 'byte7': 57, 'LKAActive': active}
        message = codec.create_c1_pscm_message(packer, stock)
        assert lib.safety_tx_hook(packet(message))
        for torque in (1999, 2001, 0, 4095):
          data = bytearray(message[1])
          data[1] = data[1] & 240 | torque >> 8
          data[2] = torque & 255
          assert not lib.safety_tx_hook(packet((293, data, 2)))
        data = bytearray(message[1])
        data[1] |= 32
        assert not lib.safety_tx_hook(packet((293, data, 2)))
        assert message[1][0] == 17 and message[1][3] == 29 and (message[1][4] == 43) and (message[1][7] == 57)

  def test_cancel_opaque_prefix_is_pinned_to_host_zero_template(self):
    for index in range(6):
      with self.subTest(index=index):
        reset()
        message = codec.create_c1_cancel(packer)
        assert message[1] == bytes([0, 0, 0, 0, 0, 0, 0, 16])
        assert lib.safety_tx_hook(packet(message))
        data = bytearray(message[1])
        data[index] = 1
        assert not lib.safety_tx_hook(packet((message[0], data, message[2])))

  def test_negative_measured_error_requires_convergence_with_same_envelope(self):
    reset()
    lib.set_angle_meas(0, 0)
    for _ in range(6):
      rx('VehicleSpeed1', 0, {'VehicleSpeed': 72})
    lib.set_controls_allowed(True)
    lib.set_desired_angle_last(round(-30 * 22.753128))
    self.assertFalse(lib.safety_tx_hook(packet(codec.create_c1_steering_control(packer, -30, True))))
    lib.set_controls_allowed(True)
    lib.set_desired_angle_last(round(-30 * 22.753128))
    self.assertTrue(lib.safety_tx_hook(packet(codec.create_c1_steering_control(packer, -29.8, True))))
