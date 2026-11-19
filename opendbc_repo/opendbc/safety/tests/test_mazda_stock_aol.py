import unittest

from opendbc.car import structs
from opendbc.can.dbc import DBC
from opendbc.safety.tests.common import CANPackerSafety, make_msg
from opendbc.safety.tests.libsafety import libsafety_py


class TestMazdaStockAol(unittest.TestCase):
  def tearDown(self):
    self.safety.init_tests()
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  def configure(self, experience=32, word=0):
    self.safety = libsafety_py.libsafety
    self.safety.init_tests()
    self.safety.set_alternative_experience(experience)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.mazda, word)
    self.safety.set_timer(1_000_000)
    self.safety.set_aol_test_heartbeat(True)
    self.packer = CANPackerSafety('mazda_2017')
    self.dbc = DBC('mazda_2017')
    self.time = 1_000_000

  def packet(self, name, fields, bus=0):
    self.assertFalse(fields.keys() - self.dbc.name_to_msg[name].sigs.keys())
    return self.packer.make_can_msg_safety(name, bus, fields)

  def feed(self, main=True, reverse=False, temporary=False, permanent=False, buttons=0, omit=None, wrong_bus=None):
    sources = (
      ('CRZ_CTRL', 0, {'CRZ_AVAILABLE': int(main), 'CRZ_ACTIVE': 0}),
      ('CRZ_BTNS', 0, {'RES': int(buttons == 1), 'SET_M': int(buttons == 2), 'CAN_OFF': int(buttons == 3)}),
      ('STEER_TORQUE', 0, {'STEER_TORQUE_SENSOR': 0}),
      ('ENGINE_DATA', 0, {'SPEED': 80, 'PEDAL_GAS': 0}),
      ('PEDALS', 0, {'BRAKE_ON': 0}),
      ('GEAR', 0, {'GEAR': 2 if reverse else 4}),
      ('STEER_RATE', 0, {'LKAS_BLOCK': int(temporary)}),
      ('CAM_LKAS', 2, {'ERR_BIT_1': int(permanent), 'LKAS_REQUEST': 0}),
    )
    for name, bus, fields in sources:
      if name != omit:
        self.safety.safety_rx_hook(self.packet(name, fields, (bus + 1) % 3 if name == wrong_bus else bus))
    self.safety.safety_tick()
    self.safety.aol_set_host_request(1)

  def arm(self):
    for _ in range(10):
      self.feed(main=True)
    self.feed(main=False)
    self.feed(main=True)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)

  def test_actual_main_buttons_cancel_and_accepted_forwarding(self):
    self.configure()
    self.feed(main=True)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.arm()
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x243), 0)
    command = self.packet('CAM_LKAS', {'LKAS_REQUEST': 10})
    self.assertTrue(self.safety.safety_tx_hook(command))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x243), -1)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x440), -1)
    self.feed(buttons=3)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.safety.aol_set_host_request(0)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x243), 0)
    self.feed(buttons=0)
    self.feed(buttons=1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertFalse(self.safety.safety_tx_hook(self.packet('CAM_LKAS', {'LKAS_REQUEST': 801})))

  def test_temporary_restriction_and_lost_session_never_synthesizes_rearm(self):
    self.configure()
    self.arm()
    for fields in ({'reverse': True}, {'temporary': True}):
      self.feed(**fields)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.feed()
      self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.safety.set_aol_test_heartbeat(False)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.set_aol_test_heartbeat(True)
    self.feed()
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.arm()
    self.feed(permanent=True)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.feed()
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_camera_bus_gear_cannot_override_reverse(self):
    # Independent review P1: GEN1 GEAR is a bus-0 source. A Drive frame on the camera bus (accepted for
    # GEN2 only) must not overwrite a bus-0 Reverse.
    self.configure()
    self.arm()
    self.feed(reverse=True)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.safety_rx_hook(self.packet('GEAR', {'GEAR': 4}, 2))
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.feed(reverse=True, omit='GEAR')
    self.safety.safety_rx_hook(self.packet('GEAR', {'GEAR': 4}, 2))
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_each_required_source_missing_wrong_bus_or_length_expires(self):
    for name in ('CRZ_CTRL', 'CRZ_BTNS', 'STEER_TORQUE', 'ENGINE_DATA', 'PEDALS', 'GEAR', 'STEER_RATE', 'CAM_LKAS'):
      for fault in ('missing', 'bus', 'length'):
        with self.subTest(source=name, fault=fault):
          self.configure()
          self.arm()
          for _ in range(120):
            self.time += 10_000
            self.safety.set_timer(self.time)
            self.feed(omit=name if fault != 'bus' else None, wrong_bus=name if fault == 'bus' else None)
            if fault == 'length':
              message = self.dbc.name_to_msg[name]
              self.safety.safety_rx_hook(make_msg(2 if name == 'CAM_LKAS' else 0, message.address, dat=bytes(7)))
            self.safety.safety_tick()
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.feed()
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.arm()

  def test_request_expiry_config_and_ae0_preservation(self):
    self.configure()
    self.arm()
    self.time += 300_001
    self.safety.set_timer(self.time)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.feed()
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    for word in (1, 2, 32, 65535):
      self.configure(word=word)
      self.feed()
      self.assertFalse(self.safety.safety_tx_hook(self.packet('CAM_LKAS', {'LKAS_REQUEST': 0})))
    self.configure(experience=0)
    self.feed(main=False)
    self.feed(main=True)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertFalse(self.safety.safety_tx_hook(self.packet('CAM_LKAS', {'LKAS_REQUEST': 10})))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x243), -1)

  def test_accepted_forwarding_releases_on_zero_or_rejected_torque(self):
    for torque in (0, 801):
      with self.subTest(torque=torque):
        self.configure()
        self.arm()
        self.assertTrue(self.safety.safety_tx_hook(self.packet('CAM_LKAS', {'LKAS_REQUEST': 10})))
        for address in (0x243, 0x440):
          self.assertEqual(self.safety.safety_fwd_hook(2, address), -1)
        self.assertEqual(bool(self.safety.safety_tx_hook(self.packet('CAM_LKAS', {'LKAS_REQUEST': torque}))), torque == 0)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        for address in (0x243, 0x440):
          self.assertEqual(self.safety.safety_fwd_hook(2, address), 0)

  def test_unwhitelisted_commands_cannot_renew_forwarding_lease(self):
    for fault in ('bus', 'length'):
      with self.subTest(fault=fault):
        self.configure()
        self.arm()
        self.assertTrue(self.safety.safety_tx_hook(self.packet('CAM_LKAS', {'LKAS_REQUEST': 10})))
        invalid = (self.packet('CAM_LKAS', {'LKAS_REQUEST': 10}, bus=1) if fault == 'bus'
                   else make_msg(0, 0x243, dat=bytes(7)))
        for elapsed in (100_000, 300_001):
          target = 1_000_000 + elapsed
          while self.time < target:
            self.time = min(self.time + 10_000, target)
            self.safety.set_timer(self.time)
            self.feed()
          self.assertFalse(self.safety.safety_tx_hook(invalid))
          self.assertEqual(self.safety.aol_get_permission_mask(), 1)
          for address in (0x243, 0x440):
            self.assertEqual(self.safety.safety_fwd_hook(2, address), -1 if elapsed <= 300_000 else 0)

  def test_longitudinal_request_requires_actual_stock_cruise(self):
    self.configure()
    self.arm()
    self.safety.aol_set_host_request(3)
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertTrue(self.safety.safety_rx_hook(self.packet('CRZ_CTRL', {'CRZ_AVAILABLE': 1, 'CRZ_ACTIVE': 1})))
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertEqual(self.safety.aol_get_permission_mask(), 3)
    self.assertTrue(self.safety.safety_rx_hook(self.packet('CRZ_CTRL', {'CRZ_AVAILABLE': 1, 'CRZ_ACTIVE': 0})))
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)

  def test_cancel_requires_neutral_and_current_host_ack(self):
    self.configure()
    self.arm()
    self.feed(buttons=3, permanent=True)  # Consume startup eligibility; isolate fresh Cancel rearming.
    self.safety.set_aol_test_heartbeat(False)
    for _ in range(12):
      self.feed(buttons=3)
    self.safety.set_aol_test_heartbeat(True)
    self.feed(buttons=3)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.set_aol_test_heartbeat(False)
    self.feed(buttons=0)
    self.feed(buttons=3)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.set_aol_test_heartbeat(True)
    self.time += 1
    self.safety.set_timer(self.time)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertFalse(self.safety.get_controls_allowed())
    self.safety.aol_set_host_request(3)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)

  def test_cancel_held_after_fault_and_main_off_never_rearms(self):
    self.configure()
    self.arm()
    self.feed(buttons=3)
    self.feed(buttons=3, permanent=True)
    for _ in range(4):
      self.feed(buttons=3)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.feed(buttons=0, main=False)
    self.feed(buttons=3, main=False)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_boot_main_on_requires_strictly_later_ack_and_never_rearms_held_main(self):
    self.configure()
    self.safety.set_aol_test_heartbeat(False)
    for _ in range(12):
      self.feed()
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.time += 1
    self.safety.set_timer(self.time)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertTrue(self.safety.safety_tx_hook(self.packet('CAM_LKAS', {'LKAS_REQUEST': 10})))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x243), -1)
    self.safety.aol_set_host_request(3)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.safety.set_aol_test_heartbeat(False)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.set_aol_test_heartbeat(True)
    for _ in range(12):
      self.feed()
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_boot_permanent_camera_fault_consumes_main_on_eligibility(self):
    self.configure()
    self.feed(permanent=True)
    for _ in range(12):
      self.feed()
    self.time += 1
    self.safety.set_timer(self.time)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_boot_main_on_pending_ack_keeps_original_deadline(self):
    self.configure()
    self.safety.set_aol_test_heartbeat(False)
    for _ in range(12):
      self.feed()
    for _ in range(31):
      self.time += 10000
      self.safety.set_timer(self.time)
      self.feed()  # Fresh CAN and requests cannot renew the startup grant.
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.set_aol_test_heartbeat(True)
    self.time += 1
    self.safety.set_timer(self.time)
    self.feed()
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_boot_main_waits_for_fresh_host_readiness_without_starting_grant_deadline(self):
    self.configure()
    self.safety.set_aol_test_heartbeat(False)
    self.safety.aol_set_host_request(0)
    for _ in range(150):
      self.time += 10000
      self.safety.set_timer(self.time)
      self.feed()
      self.safety.aol_set_host_request(0)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.aol_set_host_request(1)
    self.feed()
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.set_aol_test_heartbeat(True)
    self.time += 1
    self.safety.set_timer(self.time)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertFalse(self.safety.get_controls_allowed())
