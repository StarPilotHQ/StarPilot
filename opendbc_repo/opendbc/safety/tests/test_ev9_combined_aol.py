import unittest

from opendbc.car.structs import CarParams
from opendbc.safety.tests.test_hyundai_ev9_long import Ev9LongFixture


class CombinedFixture(Ev9LongFixture, unittest.TestCase):
  NONZERO_ANGLE = 1.1

  def setUp(self):
    super().setUp()
    self.safety.set_aol_test_heartbeat(False)
    self.safety.set_alternative_experience(32)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd, 0x5c95), 0)
    self.safety.set_timer(self.now)

  def tick(self, *, lkas=0, button=0, main=False, gas=False, brake=False, gear=5, fault=0):
    self.safety.set_timer(self.now)
    entries = [('ACCELERATOR', {'GEAR': gear, 'ACCELERATOR_PEDAL': 4 if gas else 0}),
               ('TCS', {'DriverBraking': int(brake)}),
               ('WHEEL_SPEEDS', dict.fromkeys(('WHL_SpdFLVal', 'WHL_SpdFRVal', 'WHL_SpdRLVal', 'WHL_SpdRRVal'), 40.)),
               ('MDPS', {'MDPS_ADAS_AciFltSig_Lv2': fault}),
               ('CRUISE_BUTTONS', {'LDA_BTN': lkas, 'CRUISE_BUTTONS': button, 'ADAPTIVE_CRUISE_MAIN_BTN': int(main)})]
    for name, values in entries:
      self.assertTrue(self.safety.safety_rx_hook(self.packet(name, 1, values)), name)
    self.now += 10_000

  def warm(self):
    for _ in range(6):
      self.tick()

  def arm(self):
    self.tick(lkas=1)
    self.tick()
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(1)

  def engage(self):
    self.tick(main=True)
    self.tick()
    self.tick(button=2)
    self.tick()
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(3)
    self.assertTrue(self.safety.get_controls_allowed())

  def scc(self, accel, selected=None):
    return self.packet('SCC_CONTROL', 1, {'ACCMode': 1, 'aReqRaw': accel,
                                         'aReqValue': accel if selected is None else selected})


class TestEV9CombinedDebug(CombinedFixture, unittest.TestCase):
  def test_fresh_host_request_needs_physical_latch_and_never_grants_longitudinal(self):
    self.warm()
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(3)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertFalse(self.safety.safety_tx_hook(self.angle()))
    self.arm()
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertTrue(self.safety.safety_tx_hook(self.angle(value=self.NONZERO_ANGLE)))
    self.assertFalse(self.safety.safety_tx_hook(self.scc(1.)))
    self.assertTrue(self.safety.safety_tx_hook(self.packet('CAM_0x362', 0, {})))
    self.assertTrue(self.safety.safety_test_selected_fwd(2, 0x362))
    self.safety.aol_set_host_request(0)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertFalse(self.safety.safety_tx_hook(self.angle()))
    self.assertFalse(self.safety.safety_test_selected_fwd(2, 0x362))

  def test_physical_main_set_and_cancel_keep_axes_separate_and_acceleration_limits(self):
    self.warm()
    self.arm()
    self.engage()
    self.assertEqual(self.safety.aol_get_permission_mask(), 3)
    self.assertTrue(self.safety.safety_tx_hook(self.angle(value=self.NONZERO_ANGLE)))
    for value in (3.5, -3.5):
      for raw, selected in ((value, value), (value, 0.), (0., value)):
        self.assertTrue(self.safety.safety_tx_hook(self.scc(raw, selected)), (raw, selected))
    for value in (3.51, -3.51):
      for raw, selected in ((value, value), (value, 0.), (0., value)):
        self.assertFalse(self.safety.safety_tx_hook(self.scc(raw, selected)), (raw, selected))
    self.tick(button=4)
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertFalse(self.safety.safety_tx_hook(self.scc(1.)))
    self.assertTrue(self.safety.safety_tx_hook(self.angle(value=self.NONZERO_ANGLE)))

  def test_temporary_gear_and_eps_restrictions_withhold_then_resume_lateral_without_a_new_button(self):
    for restriction in ({'gear': 6}, {'fault': 2}):
      with self.subTest(restriction=restriction):
        self.setUp()
        self.warm()
        self.arm()
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        self.assertTrue(self.safety.safety_tx_hook(self.angle(value=self.NONZERO_ANGLE)))
        self.tick(**restriction)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.assertFalse(self.safety.safety_tx_hook(self.angle()))
        self.assertFalse(self.safety.safety_tx_hook(self.packet('CAM_0x362', 0, {})))
        self.tick()
        self.safety.aol_set_host_request(1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        self.assertTrue(self.safety.safety_tx_hook(self.angle(value=0.)))

  def test_native_lease_and_request_withdrawal_cannot_be_refreshed_by_actuator_messages(self):
    self.warm()
    self.arm()
    self.assertTrue(self.safety.safety_tx_hook(self.angle()))
    self.now += 300_000
    self.safety.set_timer(self.now)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertFalse(self.safety.safety_tx_hook(self.angle()))
    self.warm()
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertFalse(self.safety.safety_tx_hook(self.angle()))


class TestEV9CombinedReleaseDenied(CombinedFixture, unittest.TestCase):
  def test_combined_profile_has_no_transmit_authority_in_release(self):
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(3)
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(self.angle()))
    self.assertFalse(self.safety.safety_tx_hook(self.scc(1.)))
    self.assertFalse(self.safety.safety_tx_hook(self.packet('CAM_0x362', 0, {})))

  def test_full_engagement_cannot_grant_any_reported_alpha_profile_in_release(self):
    for word in (0x815, 0x895, 0x5c95, 0x5495):
      with self.subTest(word=hex(word)):
        self.safety.init_tests()
        self.safety.set_alternative_experience(32)
        self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd, word), 0)
        self.safety.set_controls_allowed(True)
        self.safety.set_aol_test_heartbeat(True)
        self.safety.aol_set_host_request(3)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.assertFalse(self.safety.safety_tx_hook(self.angle()))
        self.assertFalse(self.safety.safety_tx_hook(self.scc(1.)))
