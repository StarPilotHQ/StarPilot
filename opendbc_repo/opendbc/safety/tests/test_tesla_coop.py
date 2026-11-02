import unittest
from opendbc.safety.tests import test_tesla_screen_button as screen


class TestCooperativeSafety(unittest.TestCase):
  def setUp(self):
    self.fixture = screen.TestTeslaScreenButton()
    self.fixture.setUp()

  def tearDown(self):
    self.fixture.tearDown()

  def test_driver_torque_cutoff_with_stock_and_screen_profiles(self):
    f = self.fixture
    for word in (0, 256, 512, 768, 1536, 1792):
      f.start(word)
      for torque in (-5.01, -5., 0., 5., 5.01):
        neutral = f.packer.make_can_msg_safety('EPAS3S_sysStatus', 0, {'EPAS3S_eacStatus': 1, 'EPAS3S_torsionBarTorque': 0.})
        self.assertTrue(f.lib.safety_rx_hook(neutral))
        f.lib.set_controls_allowed(True)
        msg = f.packer.make_can_msg_safety('EPAS3S_sysStatus', 0, {
          'EPAS3S_eacStatus': 1, 'EPAS3S_torsionBarTorque': torque})
        self.assertTrue(f.lib.safety_rx_hook(msg))
        self.assertEqual(bool(f.lib.get_controls_allowed()), not (bool(word & 256) and abs(torque) > 5.))

  def test_cooperative_screen_keeps_fault_and_heartbeat_withdrawal(self):
    f = self.fixture
    for word in (768, 1792):
      f.start(word)
      f.lib.set_controls_allowed(True)
      f.feed(hands=3)
      self.assertFalse(f.lib.get_controls_allowed())
      f.start(word)
      f.lib.set_aol_test_heartbeat(False)
      f.feed()
      self.assertFalse(f.lib.aol_get_permission_mask() & 1)
