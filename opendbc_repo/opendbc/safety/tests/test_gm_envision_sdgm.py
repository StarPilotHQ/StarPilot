"""Only the retrofit owner accepts the three-byte analog brake source."""
import unittest
from opendbc.car.structs import CarParams
from opendbc.safety.tests.libsafety import libsafety_py


class TestGmEnvisionSdgm(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety

  def mode(self, word, experience=0):
    self.safety.set_alternative_experience(experience)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, word), 0)
    self.safety.init_tests()
    self.safety.set_timer(1_000_000)

  def feed(self, word, length, experience=0, brake=0):
    self.mode(word, experience)
    for addr, dat in ((0x184, bytes(8)), (0x34A, bytes(5)), (0x1E1, bytes(7)),
                      (0x1C4, bytes(8)), (0xBE, bytes([0, brake, 0]) + bytes(max(0, length - 3))),
                      (0xC9, bytes(8))):
      self.safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, 0, dat))
    self.safety.safety_tick()
    return self.safety.safety_config_valid()

  def test_new_length_does_not_change_existing_owners(self):
    for experience in (0, 32):
      self.assertTrue(self.feed(0xE900, 3, experience))
      for length in (6, 7, 8):
        self.assertFalse(self.feed(0xE900, length, experience))
        for word in (0x1001, 0x3001):
          self.assertTrue(self.feed(word, length, experience))
      for word in (0x1001, 0x3001):
        self.assertFalse(self.feed(word, 3, experience))

  def test_brake_staleness_and_longitudinal_rejection(self):
    self.assertTrue(self.feed(0xE900, 3, brake=10))
    self.assertTrue(self.safety.get_brake_pressed_prev())
    self.safety.set_timer(3_000_000)
    self.safety.safety_tick()
    self.assertFalse(self.safety.safety_config_valid())
    self.safety.set_controls_allowed(True)
    for addr, size in ((0x2CB, 8), (0x315, 5), (0x200, 6)):
      self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, 0, bytes(size))))


class TestGmEnvisionAuthority(unittest.TestCase):
  def setUp(self):
    from opendbc.can import CANPacker
    self.safety = libsafety_py.libsafety
    self.packer = CANPacker('gm_global_a_powertrain_envision')

  def tearDown(self):
    self.safety.set_alternative_experience(0)

  def reset(self, alternative=32):
    self.safety.set_alternative_experience(alternative)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, 0xE900), 0)
    self.safety.init_tests()
    self.safety.set_timer(1_000_000)
    self.safety.set_aol_test_heartbeat(True)

  @staticmethod
  def packet(frame):
    return libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])

  def feed(self, *, main=True, brake=False, active=False):
    for name, values in (
      ('PSCMStatus', {}), ('EBCMWheelSpdRear', {}), ('ASCMSteeringButton', {'ACCButtons': 1}),
      ('AcceleratorPedal2', {'CruiseState': int(active)}),
      ('ECMAcceleratorPos', {'BrakePedalPos': 10 if brake else 0}),
      ('ECMEngineStatus', {'CruiseMainOn': int(main)}),
    ):
      self.assertTrue(self.safety.safety_rx_hook(self.packet(self.packer.make_can_msg(name, 0, values))))
    self.safety.safety_tick()
    self.assertTrue(self.safety.safety_config_valid())

  def steer(self, torque):
    from opendbc.car.gm import gmcan
    return self.safety.safety_tx_hook(self.packet(gmcan.create_steering_control(self.packer, 0, torque, 0, True)))

  def test_main_brake_and_freshness_authority(self):
    self.reset()
    self.feed()
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertTrue(self.steer(1))
    self.feed(brake=True)
    self.assertTrue(self.safety.get_brake_pressed_prev())
    self.assertFalse(self.safety.get_controls_allowed())
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.feed(main=False)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertFalse(self.steer(1))
    self.feed()
    self.safety.aol_set_host_request(1)
    self.assertTrue(self.steer(1))
    self.safety.set_timer(1_300_001)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertFalse(self.steer(1))

  def test_steering_rate_and_absolute_limit_preserved(self):
    for torque in (11, -11, 301, -301):
      self.reset()
      self.feed()
      self.safety.aol_set_host_request(1)
      self.assertFalse(self.steer(torque), torque)
    self.reset()
    self.feed()
    self.safety.aol_set_host_request(1)
    self.assertTrue(self.steer(1))
    self.safety.set_aol_test_heartbeat(False)
    self.assertFalse(self.steer(1))

  def test_no_aol_without_exact_experience(self):
    for alternative in (0, 4, 33, 34):
      self.reset(alternative)
      self.feed()
      self.safety.aol_set_host_request(1)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.assertFalse(self.steer(1))

  def test_forwarding_and_relay_protection(self):
    self.reset()
    for bus, addr, expected in ((0, 0x123, 2), (0, 0x184, -1), (2, 0x123, 0),
                                (2, 0x180, -1), (2, 0x315, 0), (1, 0x123, -1)):
      self.assertEqual(self.safety.safety_fwd_hook(bus, addr), expected)
    self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x184, 2, bytes(8)))
    self.assertFalse(self.safety.get_relay_malfunction())
    self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x180, 0, bytes(4)))
    self.assertTrue(self.safety.get_relay_malfunction())
    self.assertFalse(self.steer(1))
