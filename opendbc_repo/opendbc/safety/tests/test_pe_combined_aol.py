"""PE independent LONG/LAT retains the existing Alpha release gate."""
import unittest
from opendbc.car.structs import CarParams
from opendbc.car.hyundai import ioniq5pe_long_sender as sender
from opendbc.safety.tests.test_ioniq5pe_paired_native import PEFixture
from opendbc.safety.tests import test_ev9_combined_aol as ev9_combined
from opendbc.safety.tests.test_hyundai_ev9_long import Ev9LongFixture


class TestPECombinedReleaseDenied(PEFixture, unittest.TestCase):
  RELEASE_NATIVE = True

  def test_combined_pe_profile_has_no_transmit_authority_in_release(self):
    self.safety.set_alternative_experience(32)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd, 0x5495), 0)
    self.safety.set_aol_test_heartbeat(False)
    rx = []

    def feed(*, lkas=0, button=0):
      self.safety.set_timer(self.now)
      values = [('ACCELERATOR', {'GEAR': 5, 'ACCELERATOR_PEDAL': 0}),
                ('TCS', {'DriverBraking': 0}),
                ('WHEEL_SPEEDS', dict.fromkeys(('WHL_SpdFLVal', 'WHL_SpdFRVal', 'WHL_SpdRLVal', 'WHL_SpdRRVal'), 40.)),
                ('MDPS', {'MDPS_EstStrAnglVal': 0., 'MDPS_ADAS_AciFltSig_Lv2': 0}),
                ('CRUISE_BUTTONS', {'LDA_BTN': lkas, 'CRUISE_BUTTONS': button})]
      for name, fields in values:
        rx.append(bool(self.safety.safety_rx_hook(self.packet(name, 1, fields))))
      self.now += 10_000

    for _ in range(6):
      feed()
    feed(lkas=1)
    feed()
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(1)
    feed(button=2)
    feed()
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(3)
    self.safety.set_controls_allowed(True)
    print(f'PE_RELEASE_BOUNDARY_PROOF rxAccepted={all(rx)} permission={self.safety.aol_get_permission_mask()}', flush=True)
    self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1)))
    self.assertFalse(self.safety.safety_tx_hook(self.packet('SCC_CONTROL', 1, {'ACCMode': 1, 'aReqRaw': 1., 'aReqValue': 1.})))
    self.assertFalse(self.safety.safety_tx_hook(self.can_packet(sender.create_radar_heartbeat(1, False, False))))
    self.assertFalse(self.safety.safety_tx_hook(self.packet('CAM_0x362', 0, {})))
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)


class TestPECombinedDebug(ev9_combined.TestEV9CombinedDebug):
  NONZERO_ANGLE = .1

  def setUp(self):
    Ev9LongFixture.setUp(self)
    self.safety.set_aol_test_heartbeat(False)
    self.safety.set_alternative_experience(32)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd, 0x5495), 0)
    self.safety.set_timer(self.now)

  def test_physical_main_set_and_cancel_keep_axes_separate_and_acceleration_limits(self):
    self.warm()
    self.arm()
    # PE MAIN withdraws cruise and is not an availability toggle.
    self.tick(button=2)
    self.tick()
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(3)
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertEqual(self.safety.aol_get_permission_mask(), 3)
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

  def test_all_pe_eps_fault_bits_and_legacy_fault_withdraw_independent_lateral(self):
    for fault in range(1, 8):
      with self.subTest(fault=fault):
        self.setUp()
        self.warm()
        self.arm()
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        self.tick(fault=fault)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.assertFalse(self.safety.safety_tx_hook(self.angle()))
    for fault in range(1, 4):
      with self.subTest(legacy_fault=fault):
        self.setUp()
        self.warm()
        self.arm()
        self.assertTrue(self.safety.safety_rx_hook(self.packet('MDPS', 1, {'MDPS_LkaFailSta': fault})))
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.assertFalse(self.safety.safety_tx_hook(self.angle()))

  def test_pe_geometry_does_not_borrow_ev9_nonzero_angle_rate(self):
    self.warm()
    self.arm()
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertFalse(self.safety.safety_tx_hook(self.angle(value=1.1)))
    self.assertTrue(self.safety.safety_tx_hook(self.angle(value=.1)))
