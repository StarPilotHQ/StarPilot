import os
import unittest
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests import test_tesla_screen_button as screen


class TestTeslaOrdinaryAol(unittest.TestCase):
  start = screen.TestTeslaScreenButton.start
  feed = screen.TestTeslaScreenButton.feed
  permission = screen.TestTeslaScreenButton.permission
  touch = screen.TestTeslaScreenButton.touch
  tearDown = screen.TestTeslaScreenButton.tearDown

  def setUp(self):
    self.lib = libsafety_py.libsafety
    self.packer = CANPackerSafety('tesla_model3_party')
    self.start(2048)

  def test_standard_harness_brake_choice_and_real_cruise_rearm(self):
    for word in (2048, 2304, 3072, 3328):
      with self.subTest(word=word):
        self.start(word)
        self.touch(0)
        self.touch(3)
        self.assertEqual(self.permission(), 0, 'optional bus1 gestures cannot arm ordinary AOL')
        self.feed(state=1)
        self.assertEqual(self.permission(3), 1)
        self.touch(0)
        self.touch(3)
        self.assertEqual(self.permission(), 1, 'optional bus1 gestures cannot cancel ordinary AOL')
        self.feed(state=1, brake=True)
        self.assertEqual(self.permission(), 0 if word & 1024 else 1)
        self.feed(state=1)
        self.assertEqual(self.permission(), 0 if word & 1024 else 1)
        self.feed(state=2)
        self.assertEqual(self.permission(), 1)
        self.feed(state=0)
        self.assertEqual(self.permission(), 0)
        self.feed(state=1)
        self.assertEqual(self.permission(), 0, 'availability cannot undo a cancellation')

  def test_physical_faults_heartbeat_and_request_expiry_still_withdraw(self):
    for fault in ({'hands': 3}, {'gear': 0}, {'door': True}, {'eps': 3}, {'autopark': 3}, {'state': 5}):
      with self.subTest(fault=fault):
        self.start(2048)
        self.feed(state=1)
        self.assertEqual(self.permission(), 1)
        self.feed(**({'state': 1} | fault))
        self.assertEqual(self.permission(), 0)
        self.feed(state=1)
        self.assertEqual(self.permission(), 0)
    self.start(2048)
    self.feed(state=1)
    self.assertEqual(self.permission(), 1)
    self.lib.set_aol_test_heartbeat(False)
    self.assertEqual(self.permission(), 0)
    self.lib.set_aol_test_heartbeat(True)
    self.assertEqual(self.permission(), 0)
    self.feed(state=2)
    self.assertEqual(self.permission(), 1)
    self.time += 1_100_000
    self.lib.set_timer(self.time)
    self.assertEqual(self.lib.aol_get_permission_mask(), 0)

  def test_longitudinal_stays_independent_and_release_debug_limits_remain(self):
    positive = self.packer.make_can_msg_safety('DAS_control', 0, {'DAS_accelMin': 1, 'DAS_accelMax': 1})
    for word in (2048, 3072, 2049, 3073):
      if word & 1 and os.environ.get('TESLA_SCREEN_NATIVE_RELEASE') == '1':
        self.assertEqual(self.lib.set_safety_hooks(10, word), -1)
        continue
      self.start(word)
      self.feed(state=1)
      self.assertEqual(self.permission(3), 1)
      self.assertFalse(self.lib.safety_tx_hook(positive))
      self.feed(state=2)
      self.assertEqual(self.permission(3), 3 if word & 1 else 1)
      if word & 1:
        self.assertTrue(self.lib.safety_tx_hook(positive))
        self.feed(state=2, brake=True)
        self.assertFalse(self.lib.safety_tx_hook(positive))

  def test_profile_must_be_exact_and_saved_aol_opt_in_is_required(self):
    for word in (2048 | 512, 2048 | 16, 2048 | 2, 2048 | 4096, 3072 | 512):
      self.assertEqual(self.lib.set_safety_hooks(10, word), -1)
    self.lib.set_alternative_experience(0)
    self.assertEqual(self.lib.set_safety_hooks(10, 2048), 0)
    self.feed(state=1)
    self.assertEqual(self.permission(), 0)
