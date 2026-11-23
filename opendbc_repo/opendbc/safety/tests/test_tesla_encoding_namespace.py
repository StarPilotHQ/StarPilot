"""Legacy cached words cannot select or acquire modern Tesla actuation."""
import unittest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.tesla.values import CAR, DBC
from opendbc.car.tesla.teslacan import TeslaCAN
from opendbc.car.tesla.teslacan_legacy import ModelSHW1CAN
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.test_tesla_hw1 import packet

lib = libsafety_py.libsafety


def check_deprecated_and_unknown_namespace_words_reject_even_forced_controls():
  modern = TeslaCAN(None, CANPacker(DBC[CAR.TESLA_MODEL_3][Bus.party]))
  hw1 = ModelSHW1CAN(CANPacker(DBC[CAR.TESLA_MODEL_S_HW1][Bus.party]))
  for word in (2, 3, 4, 8, 18, 19, 32, 0x8000, 0xFFFF):
    assert lib.set_safety_hooks(10, word) == -1
    assert not lib.get_controls_allowed()
    lib.set_controls_allowed(True)
    assert not lib.safety_tx_hook(packet(modern.create_steering_control(0., False)))
    assert not lib.safety_tx_hook(packet(hw1.create_steering_control(0., 0, False)))


def check_exact_current_and_hw1_profiles_keep_distinct_steering_encoding():
  modern = TeslaCAN(None, CANPacker(DBC[CAR.TESLA_MODEL_3][Bus.party]))
  hw1 = ModelSHW1CAN(CANPacker(DBC[CAR.TESLA_MODEL_S_HW1][Bus.party]))
  assert modern.create_steering_control(0., True)[1][2] & 0xE0 == 0x20
  assert hw1.create_steering_control(0., 0, True)[1][2] & 0xC0 == 0x40
  for word, codec in ((0, modern), (1, modern), (16, hw1), (17, hw1)):
    assert lib.set_safety_hooks(10, word) == 0
    lib.init_tests()  # Clear the separate startup Autopark assumption for this encoding test.
    lib.set_controls_allowed(False)
    message = codec.create_steering_control(0., False) if word < 16 else codec.create_steering_control(0., 0, False)
    assert lib.safety_tx_hook(packet(message))


def check_speed_sync_bit_extends_only_modern_long_control_words():
  modern = TeslaCAN(None, CANPacker(DBC[CAR.TESLA_MODEL_3][Bus.party]))
  hw1 = ModelSHW1CAN(CANPacker(DBC[CAR.TESLA_MODEL_S_HW1][Bus.party]))
  debug = lib.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) == 0
  # Alone, on a stock or HW1 word, or beside an unknown flag, the bit selects nothing.
  for word in (4096, 4096 | 256, 4096 | 512, 4096 | 2048, 4096 | 16, 4096 | 17, 4096 | 3, 4096 | 1 | 0x8000, 8192 | 1):
    assert lib.set_safety_hooks(10, word) == -1
    assert not lib.get_controls_allowed()
    lib.set_controls_allowed(True)
    assert not lib.safety_tx_hook(packet(modern.create_steering_control(0., False)))
    assert not lib.safety_tx_hook(packet(hw1.create_steering_control(0., 0, False)))
  # The ten exact words keep the modern profile; RELEASE firmware has no such word.
  for base in (1, 257, 513, 769, 1537, 1793, 2049, 2305, 3073, 3329):
    assert lib.set_safety_hooks(10, 4096 | base) == (0 if debug else -1)
    lib.init_tests()  # Clear the separate startup Autopark assumption for this encoding test.
    lib.set_controls_allowed(False)
    assert lib.safety_tx_hook(packet(modern.create_steering_control(0., False))) == debug
    assert lib.get_current_safety_param() == (4096 | base if debug else 0)


class TestTeslaEncodingNamespace(unittest.TestCase):
  def tearDown(self):
    lib.init_tests()
    lib.set_alternative_experience(0)
    lib.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  def test_deprecated_and_unknown_namespace_words_reject_even_forced_controls(self):
    check_deprecated_and_unknown_namespace_words_reject_even_forced_controls()

  def test_exact_current_and_hw1_profiles_keep_distinct_steering_encoding(self):
    check_exact_current_and_hw1_profiles_keep_distinct_steering_encoding()

  def test_speed_sync_bit_extends_only_modern_long_control_words(self):
    check_speed_sync_bit_extends_only_modern_long_control_words()
