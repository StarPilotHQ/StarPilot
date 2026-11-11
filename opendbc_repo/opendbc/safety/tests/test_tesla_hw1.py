import unittest
import pytest
from opendbc.can import CANPacker
from opendbc.car.structs import CarParams
from opendbc.safety import LEN_TO_DLC
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.car.tesla import teslacan_legacy as module

ffi = libsafety_py.ffi
lib = libsafety_py.libsafety
packer = CANPacker('tesla_can')


def packet(message):
  address, data, bus = message
  p = libsafety_py.new_CANPacket()
  p.addr = address
  p.bus = bus
  p.data_len_code = LEN_TO_DLC[len(data)]
  p.data = bytes(data)
  return p


def rx(name, bus, values):
  return lib.safety_rx_hook(packet(packer.make_can_msg(name, bus, values)))


def reset(param=16):
  assert lib.set_safety_hooks(10, param) == 0
  lib.set_timer(0)


class TestModelSHW1Safety(unittest.TestCase):
  def test_all_16bit_profile_words_are_isolated(self):
    debug = lib.set_safety_hooks(CarParams.SafetyModel.allOutput, 0) == 0
    supported = {0, 1, 16, 17, 256, 257, 512, 768, 1536, 1792, 2048, 2304, 3072, 3328} | ({513, 769, 1537, 1793, 2049, 2305, 3073, 3329} if debug else set())
    for param in range(65536):
      result = lib.set_safety_hooks(10, param)
      expected = 0 if param in supported else -1
      assert result == expected, (param, result)
      if expected == -1:
        lib.set_controls_allowed(True)
        assert not lib.safety_tx_hook(packet(module.ModelSHW1CAN(packer).create_steering_control(0, 0, False)))

  def test_tx_wrong_bus_and_length(self):
    for param in [16, 17]:
      for address, length in [(1160, 4), (697, 8)]:
        for bus in range(8):
          with self.subTest(param=param, address=address, length=length, bus=bus):
            reset(param)
            codec = module.ModelSHW1CAN(packer)
            good = codec.create_steering_control(0, 0, False) if address == 1160 else codec.create_longitudinal_command(13, 0, 0, 10, False, False)
            assert lib.safety_tx_hook(packet((address, good[1], bus))) == (bus == 0)
            for size in range(9):
              if size != length:
                assert not lib.safety_tx_hook(packet((address, bytes(size), bus)))

  def test_stock_long_and_aeb_forwarding_ownership(self):
    for param in [16, 17]:
      with self.subTest(param=param):
        reset(param)
        assert lib.safety_fwd_hook(2, 697) == (0 if param == 16 else -1)
        assert lib.safety_fwd_hook(0, 697) == 2
        assert lib.safety_fwd_hook(1, 697) == -1
        codec = module.ModelSHW1CAN(packer)
        stock = codec.create_longitudinal_command(4, 0, 0, 10, False, False)
        address, data, _ = stock
        changed = bytearray(data)
        changed[2] = changed[2] & ~3 | 1
        changed[-1] = codec.checksum(address, changed[:-1])
        assert lib.safety_rx_hook(packet((address, changed, 2)))
        assert lib.safety_fwd_hook(2, 697) == 0
        assert not lib.safety_tx_hook(packet(codec.create_longitudinal_command(13, 0, 0, 10, False, False)))

  def test_native_long_accel_cancel_and_inactive_permission(self):
    for param in [16, 17]:
      for allowed in [False, True]:
        for accel in [-4.0, -3.48, -1.0, 0.0, 1.0, 2.0, 3.0]:
          for cancel in [False, True]:
            with self.subTest(param=param, allowed=allowed, accel=accel, cancel=cancel):
              reset(param)
              lib.set_controls_allowed(allowed)
              codec = module.ModelSHW1CAN(packer)
              state = 13 if cancel else 4
              msg = codec.create_longitudinal_command(state, accel, 0, 10, True, False)
              expected = cancel or (param == 17 and (allowed or accel == 0))
              assert lib.safety_tx_hook(packet(msg)) == expected

  def test_known_das_checksum_counter_and_malformed_rejection(self):
    reset()
    codec = module.ModelSHW1CAN(packer)
    for _name, address, size in [('DAS_steeringControl', 1160, 4), ('DAS_control', 697, 8)]:
      reset()
      for index in range(16):
        message = codec.create_steering_control(index, 0, False) if size == 4 else codec.create_longitudinal_command(4, 0, index, 10, False, False)
        assert lib.safety_rx_hook(packet((address, message[1], 2)))
      address, data, _ = message
      damaged = bytearray(data)
      damaged[-1] ^= 1
      assert not lib.safety_rx_hook(packet((address, damaged, 2)))
      assert lib.safety_rx_hook(packet((address, bytes(size - 1), 2))), 'unlisted length is ignored by shared RX wrapper'
      assert not lib.get_controls_allowed(), 'malformed length cannot recover controls after checksum failure'

  def test_rx_literal_speed_gas_brake_and_steering_fields(self):
    reset()
    assert rx('ESP_B', 0, {'ESP_vehicleSpeed': 72})
    assert lib.get_vehicle_speed_max() == pytest.approx(20.0)
    for angle in (-360.0, -12.5, 0.0, 12.5, 360.0):
      for _ in range(6):
        assert rx('EPAS_sysStatus', 0, {'EPAS_internalSAS': angle})
      assert lib.get_angle_meas_min() == round(angle * 10)
      assert lib.get_angle_meas_max() == round(angle * 10)
    rx('DI_torque1', 0, {'DI_pedalPos': 5})
    assert lib.get_gas_pressed_prev()
    rx('DI_torque1', 0, {'DI_pedalPos': 0})
    assert not lib.get_gas_pressed_prev()
    rx('BrakeMessage', 0, {'driverBrakeStatus': 2})
    assert lib.get_brake_pressed_prev()
    rx('BrakeMessage', 0, {'driverBrakeStatus': 1})
    assert not lib.get_brake_pressed_prev()

  def test_disabled_steering_is_neutral_and_stock_lkas_keeps_ownership(self):
    reset()
    codec = module.ModelSHW1CAN(packer)
    lib.set_angle_meas(0, 0)
    assert lib.safety_tx_hook(packet(codec.create_steering_control(0, 0, False)))
    assert not lib.safety_tx_hook(packet(codec.create_steering_control(1, 10, False)))
    for angle in (-360.0, -12.5, 12.5, 360.0):
      lib.set_angle_meas(round(-angle * 10), round(-angle * 10))
      assert lib.safety_tx_hook(packet(codec.create_steering_control(0, angle, False)))
    lib.set_angle_meas(0, 0)
    stock = codec.create_steering_control(0, 0, False)
    address, data, _ = stock
    changed = bytearray(data)
    changed[2] = changed[2] & 63 | 128
    changed[-1] = codec.checksum(address, changed[:-1])
    assert lib.safety_rx_hook(packet((address, changed, 2)))
    assert lib.safety_fwd_hook(2, 1160) == 0
    lib.set_controls_allowed(True)
    assert not lib.safety_tx_hook(packet(codec.create_steering_control(1, 0, True)))

  def test_native_driver_and_angle_rate_fault_revoke_controls(self):
    for name, values in [('EPAS_sysStatus', {'EPAS_handsOnLevel': 3}), ('EPAS_sysStatus', {'EPAS_eacStatus': 0, 'EPAS_eacErrorCode': 9})]:
      with self.subTest(name=name, values=values):
        reset()
        lib.set_controls_allowed(True)
        assert rx(name, 0, values)
        assert not lib.get_controls_allowed()

  def test_longitudinal_state_whitelist(self):
    for param in [16, 17]:
      for state in range(16):
        with self.subTest(param=param, state=state):
          reset(param)
          lib.set_controls_allowed(True)
          codec = module.ModelSHW1CAN(packer)
          msg = codec.create_longitudinal_command(state, 0, 0, 10, True, False)
          assert lib.safety_tx_hook(packet(msg)) == (state == 13 or (state == 4 and param == 17))

  def test_native_jerk_boundaries(self):
    for field, value, expected in [
      ('DAS_jerkMin', -4.9, True),
      ('DAS_jerkMin', -5.0, False),
      ('DAS_jerkMin', 0.08, False),
      ('DAS_jerkMax', 4.9, True),
      ('DAS_jerkMax', 5.0, False),
    ]:
      with self.subTest(field=field, value=value, expected=expected):
        reset(17)
        lib.set_controls_allowed(True)
        values = {'DAS_accState': 4, 'DAS_accelMin': 0, 'DAS_accelMax': 0, 'DAS_jerkMin': 0, 'DAS_jerkMax': 0, field: value}
        address, data, bus = packer.make_can_msg('DAS_control', 0, values)
        changed = bytearray(data)
        changed[-1] = module.ModelSHW1CAN.checksum(address, changed[:-1])
        assert lib.safety_tx_hook(packet((address, changed, bus))) == expected

  def test_native_steering_rate_and_absolute_limit(self):
    reset()
    for _ in range(6):
      rx('ESP_B', 0, {'ESP_vehicleSpeed': 72})
    lib.set_controls_allowed(True)
    lib.set_angle_meas(0, 0)
    lib.set_desired_angle_last(0)
    codec = module.ModelSHW1CAN(packer)
    assert lib.safety_tx_hook(packet(codec.create_steering_control(0, 0, True)))
    assert not lib.safety_tx_hook(packet(codec.create_steering_control(1, 100, True)))
    assert not lib.safety_tx_hook(packet(codec.create_steering_control(1, 400, True)))

  def test_das_replay_reaches_counter_rejection(self):
    for steering in [False, True]:
      with self.subTest(steering=steering):
        reset()
        codec = module.ModelSHW1CAN(packer)
        msg = codec.create_steering_control(0, 0, False) if steering else codec.create_longitudinal_command(4, 0, 0, 10, False, False)
        address, data, _ = msg
        assert lib.safety_rx_hook(packet((address, data, 2)))
        result = True
        for _ in range(8):
          result = lib.safety_rx_hook(packet((address, data, 2)))
        assert not result
        assert not lib.get_controls_allowed()

  def test_required_rx_omission_revokes_controls(self):
    for missing in ['DI_torque1', 'DAS_control', 'EPAS_sysStatus', 'ESP_B', 'BrakeMessage', 'DI_state', 'DAS_steeringControl']:
      with self.subTest(missing=missing):
        reset()
        codec = module.ModelSHW1CAN(packer)
        for index in range(1, 221):
          lib.set_timer(index * 10000)
          messages = [
            packer.make_can_msg('DI_torque1', 0, {}),
            packer.make_can_msg('EPAS_sysStatus', 0, {}),
            packer.make_can_msg('ESP_B', 0, {}),
            packer.make_can_msg('BrakeMessage', 0, {'driverBrakeStatus': 1}),
            packer.make_can_msg('DI_state', 0, {'DI_cruiseState': 2}),
          ]
          for message, name in zip(messages, ['DI_torque1', 'EPAS_sysStatus', 'ESP_B', 'BrakeMessage', 'DI_state'], strict=True):
            if index <= 20 or name != missing:
              assert lib.safety_rx_hook(packet(message))
          steer = codec.create_steering_control(index, 0, False)
          long = codec.create_longitudinal_command(4, 0, index, 10, False, False)
          if index <= 20 or missing != 'DAS_steeringControl':
            assert lib.safety_rx_hook(packet((steer[0], steer[1], 2)))
          if index <= 20 or missing != 'DAS_control':
            assert lib.safety_rx_hook(packet((long[0], long[1], 2)))
          lib.safety_tick()
          if index == 20:
            assert lib.safety_config_valid()
            assert lib.get_controls_allowed(), 'complete positive feed must arm before omission'
        assert not lib.safety_config_valid()
        assert not lib.get_controls_allowed()

  def test_native_all_cruise_state_nibbles(self):
    for state in range(16):
      with self.subTest(state=state):
        reset()
        rx('DI_state', 0, {'DI_cruiseState': state})
        assert bool(lib.get_controls_allowed()) == (state in (2, 3, 4, 6, 7))
        assert bool(lib.get_acc_main_on()) == (state in (1, 2, 3, 4, 6, 7))
