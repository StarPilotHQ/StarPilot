import unittest

from opendbc.car import structs
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py


class TestHondaRdxGasCap(unittest.TestCase):
  def test_exact_rdx_words_2200_other_words_2000(self):
    safety = libsafety_py.libsafety
    debug = safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) == 0
    for word in (3, 35, 131, 163, 2082, 2083, 2210, 2211):
      with self.subTest(word=word):
        safety.init_tests()
        safety.set_alternative_experience(0)
        self.assertEqual(safety.set_safety_hooks(structs.CarParams.SafetyModel.hondaBosch, word), 0)
        packer = CANPackerSafety("acura_rdx_2020_can_generated")
        for tick, button in enumerate((0, 3, 0), start=1):
          safety.set_timer(1_000_000 + tick * 10_000)
          for name, fields in (("SCM_FEEDBACK", {"MAIN_ON": 1}),
                               ("SCM_BUTTONS", {"CRUISE_BUTTONS": button, "CRUISE_SETTING": 0}),
                               ("ENGINE_DATA", {"XMISSION_SPEED": 30}),
                               ("POWERTRAIN_DATA", {"ACC_STATUS": 0, "PEDAL_GAS": 0, "BRAKE_PRESSED": 0}),
                               ("BRAKE_MODULE", {"BRAKE_PRESSED": 0})):
            self.assertTrue(safety.safety_rx_hook(packer.make_can_msg_safety(name, 1, fields)))
        self.assertEqual(safety.get_controls_allowed(), debug)
        if word in (35, 163):
          active = packer.make_can_msg_safety("ACC_CONTROL", 1,
                    {"GAS_COMMAND": 2000, "ACCEL_COMMAND": 0, "BRAKE_REQUEST": 0})
          self.assertFalse(safety.safety_tx_hook(active))
          safety.set_aol_test_heartbeat(True)
          safety.aol_set_host_request(2)
          self.assertEqual(bool(safety.aol_get_permission_mask() & 2), debug)
        maximum = 2200 if word in (131, 163, 2211) else 2000
        for gas in (2000, 2001, 2200, 2201):
          frame = packer.make_can_msg_safety("ACC_CONTROL", 1,
                    {"GAS_COMMAND": gas, "ACCEL_COMMAND": 0, "BRAKE_REQUEST": 0})
          self.assertEqual(safety.safety_tx_hook(frame), debug and gas <= maximum, (word, gas))
