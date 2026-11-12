import unittest
from types import SimpleNamespace

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.hyundai.hyundaicanfd import create_steering_messages
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.car.hyundai.canfd_stock_aol import STOCK_EV_CARS
from openpilot.starpilot.aol.tests.test_canfd_stock_profiles import params
from opendbc.safety.tests.libsafety import libsafety_py


class TestHyundaiHda2LongAol(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.release = self.safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) != 0
    self.packer = CANPacker('hyundai_canfd_generated')

  def tearDown(self):
    self.safety.init_tests()
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  def mode(self, word, experience=32):
    self.safety.init_tests()
    self.safety.set_alternative_experience(experience)
    self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.hyundaiCanfd, word), 0)
    self.safety.set_timer(1_000_000)
    self.counts = {}
    self.word = word

  def packet(self, name, bus, values):
    frame = self.packer.make_can_msg(name, bus, values)
    return libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])

  def rx(self, name, values=None, bus=1):
    self.counts[name] = self.counts.get(name, 0) + 1
    values = {'COUNTER': self.counts[name] % (16 if name == 'CRUISE_BUTTONS' else 256), **(values or {})}
    return self.safety.safety_rx_hook(self.packet(name, bus, values))

  def healthy(self, omit=None, wrong_bus=None):
    for _ in range(6):
      for name in ('ACCELERATOR', 'TCS', 'WHEEL_SPEEDS', 'MDPS', 'CRUISE_BUTTONS'):
        if name != omit:
          values = {'GEAR': 5} if name == 'ACCELERATOR' else {'MDPS_StrTqSnsrVal': 0} if name == 'MDPS' else {}
          self.rx(name, values, 0 if name == wrong_bus else 1)
    self.safety.safety_tick()

  def arm(self):
    self.rx('CRUISE_BUTTONS', {'LDA_BTN': 1})
    self.rx('CRUISE_BUTTONS')
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(3)

  def steering(self, torque=1, bus=1):
    frames = self.steering_frames(torque)
    frame = frames[0]
    return libsafety_py.make_CANPacket(frame[0], bus, frame[1])

  def steering_frames(self, torque=1):
    flags = HyundaiFlags.CANFD_LKA_STEER_MSG
    if self.word & 0x80:
      flags |= HyundaiFlags.CANFD_LKA_STEER_MSG_ALT
    layout = SimpleNamespace(carFingerprint=CAR.HYUNDAI_IONIQ_5, flags=int(flags), openpilotLongitudinalControl=True)
    return create_steering_messages(self.packer, layout, SimpleNamespace(ECAN=1, ACAN=0), True, torque != 0, torque)

  def accel(self, raw=0., value=0., bus=1):
    return self.packet('SCC_CONTROL', bus, {'ACCMode': 1, 'aReqRaw': raw, 'aReqValue': value})

  def test_physical_lateral_grant_is_not_longitudinal_grant(self):
    for word in (0x0815, 0x0895):
      self.mode(word)
      self.healthy()
      self.safety.set_aol_test_heartbeat(True)
      self.safety.aol_set_host_request(3)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.rx('CRUISE_BUTTONS', {'CRUISE_BUTTONS': 4, 'LDA_BTN': 1})
      self.assertFalse(self.safety.get_controls_allowed())
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.assertFalse(self.safety.safety_tx_hook(self.steering()))
      self.rx('CRUISE_BUTTONS')
      self.arm()
      self.assertEqual(self.safety.aol_get_permission_mask(), 0 if self.release else 1)
      self.assertEqual(bool(self.safety.safety_tx_hook(self.steering())), not self.release)
      self.assertFalse(self.safety.safety_tx_hook(self.accel(.1, .1)))
      self.rx('CRUISE_BUTTONS', {'CRUISE_BUTTONS': 2})
      self.rx('CRUISE_BUTTONS')
      self.assertEqual(self.safety.aol_get_permission_mask(), 0 if self.release else 3)
      self.assertEqual(bool(self.safety.safety_tx_hook(self.accel(.1, .1))), not self.release)
      self.safety.aol_set_host_request(1)
      self.assertFalse(self.safety.safety_tx_hook(self.accel(.1, .1)))
      if not self.release:
        self.assertTrue(self.safety.safety_tx_hook(self.accel()))
      self.safety.aol_set_host_request(3)
      for raw, value in ((3.51, 0), (0, 3.51), (-3.51, 0), (0, -3.51)):
        self.assertFalse(self.safety.safety_tx_hook(self.accel(raw, value)))
      self.rx('CRUISE_BUTTONS', {'CRUISE_BUTTONS': 4})
      self.assertFalse(self.safety.get_controls_allowed())
      self.assertEqual(self.safety.aol_get_permission_mask(), 0 if self.release else 1)
      self.assertEqual(bool(self.safety.safety_tx_hook(self.steering())), not self.release)
      self.assertFalse(self.safety.safety_tx_hook(self.accel(.1, .1)))

  def test_required_sources_wrong_bus_expiry_and_fresh_rearm(self):
    for word in (0x0815, 0x0895):
      for name in ('ACCELERATOR', 'TCS', 'WHEEL_SPEEDS', 'MDPS', 'CRUISE_BUTTONS'):
        for wrong_bus in (False, True):
          self.mode(word)
          self.healthy(omit=None if wrong_bus else name, wrong_bus=name if wrong_bus else None)
          if name != 'CRUISE_BUTTONS':
            self.arm()
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.mode(word)
      self.healthy()
      self.arm()
      self.safety.set_timer(2_000_001)
      self.safety.safety_tick()
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.healthy()
      self.safety.set_aol_test_heartbeat(True)
      self.safety.aol_set_host_request(3)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.arm()
      self.assertEqual(self.safety.aol_get_permission_mask(), 0 if self.release else 1)
      self.assertFalse(self.safety.safety_tx_hook(self.steering(bus=0)))
      self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x12A, 1, bytes(8))))
      self.safety.set_aol_test_heartbeat(False)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_cold_held_button_and_wrong_experience_do_not_grant(self):
    for word in (0x0815, 0x0895):
      self.mode(word)
      self.rx('CRUISE_BUTTONS', {'LDA_BTN': 1})
      self.healthy(omit='CRUISE_BUTTONS')
      self.rx('CRUISE_BUTTONS', {'LDA_BTN': 1})
      self.safety.set_aol_test_heartbeat(True)
      self.safety.aol_set_host_request(3)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      for experience in (0, 1, 16, 33):
        self.mode(word, experience)
        self.healthy()
        self.arm()
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.assertFalse(self.safety.safety_tx_hook(self.steering()))

  def test_integrity_relay_and_request_expiry_revoke(self):
    for word in (0x0815, 0x0895):
      for cause in ('checksum', 'counter', 'relay', 'request'):
        self.mode(word)
        self.healthy()
        self.arm()
        if cause == 'checksum':
          frame = self.packer.make_can_msg('WHEEL_SPEEDS', 1, {'COUNTER': 7})
          data = bytes((frame[1][0] ^ 1,)) + frame[1][1:]
          self.assertEqual(bool(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(frame[0], 1, data))), self.release)
        elif cause == 'counter':
          for _ in range(10):
            self.safety.safety_rx_hook(self.packet('WHEEL_SPEEDS', 1, {'COUNTER': 6}))
        elif cause == 'relay':
          self.safety.set_relay_malfunction(True)
        else:
          self.safety.set_timer(1_200_001)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.assertFalse(self.safety.safety_tx_hook(self.steering()))
        self.assertFalse(self.safety.safety_tx_hook(self.accel(.1, .1)))

  def test_existing_cluster_icons_and_ordinary_long_namespace(self):
    for word in (0x0815, 0x0895):
      self.mode(word)
      self.healthy()
      self.arm()
      for icon in (0, 2, 3):
        packet = self.packet('LFAHDA_CLUSTER', 1, {'LFA_ICON': icon, 'HDA_ICON': 0})
        self.assertEqual(bool(self.safety.safety_tx_hook(packet)), not self.release)
        self.assertFalse(self.safety.safety_tx_hook(self.packet('LFAHDA_CLUSTER', 0, {'LFA_ICON': icon})))
      self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1E0, 1, bytes(8))))
    for word in (0x0015, 0x0095):
      self.mode(word, 0)
      self.healthy()
      self.rx('CRUISE_BUTTONS', {'CRUISE_BUTTONS': 2})
      self.rx('CRUISE_BUTTONS')
      self.assertEqual(bool(self.safety.safety_tx_hook(self.accel(.1, .1))), not self.release)

  def test_exact_normal_and_alternate_single_use_steering_mirror(self):
    for word in (0x0015, 0x0095, 0x0815, 0x0895):
      self.mode(word, 32 if word & 0x800 else 0)
      self.healthy()
      self.arm()
      self.rx('CRUISE_BUTTONS', {'CRUISE_BUTTONS': 2})
      self.rx('CRUISE_BUTTONS')
      frames = self.steering_frames()
      self.assertEqual([f[0] for f in frames], [0x12A, 0x110 if word & 0x80 else 0x50])
      for frame in frames:
        packet = libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])
        self.assertEqual(bool(self.safety.safety_tx_hook(packet)), not self.release)
      mirror = frames[1]
      self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(mirror[0], mirror[2], mirror[1])))
      wrong = self.packet('LKAS' if word & 0x80 else 'LKAS_ALT', 0, {'StrTqReqVal': 1, 'ActToiSta': 1})
      self.assertFalse(self.safety.safety_tx_hook(wrong))
      self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(mirror[0], 1, mirror[1])))
      if not self.release:
        frame = self.steering_frames(2)[0]
        self.assertTrue(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])))
        self.safety.set_timer(1_010_001)
        self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(mirror[0], mirror[2], mirror[1])))

  def test_rejected_replacement_and_invalid_mirror_cannot_reuse_permission(self):
    for word in (0x0015, 0x0095, 0x0815, 0x0895):
      for fault in ('replacement', 'torque', 'body', 'checksum'):
        with self.subTest(word=hex(word), fault=fault):
          self.mode(word, 32 if word & 0x800 else 0)
          self.healthy()
          self.arm()
          self.rx('CRUISE_BUTTONS', {'CRUISE_BUTTONS': 2})
          self.rx('CRUISE_BUTTONS')
          source, mirror = self.steering_frames()
          self.assertEqual(bool(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(*self.native_args(source)))), not self.release)
          if fault == 'replacement':
            self.assertFalse(self.safety.safety_tx_hook(self.steering(torque=270)))
          else:
            name = 'LKAS_ALT' if word & 0x80 else 'LKAS'
            if fault == 'torque':
              forged = self.steering_frames(2)[1]
            elif fault == 'body':
              forged = self.packer.make_can_msg(name, 0, {'StrTqReqVal': 1, 'ActToiSta': 1, 'Damping_Gain': 99})
            else:
              forged = (mirror[0], bytes((mirror[1][0] ^ 1,)) + mirror[1][1:], mirror[2])
            self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(*self.native_args(forged))))
          self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(*self.native_args(mirror))))

  @staticmethod
  def native_args(frame):
    return frame[0], frame[2], frame[1]

  def test_shared_ev_factory_sender_keeps_exact_mirror_body(self):
    for identity in STOCK_EV_CARS:
      for alternate in (False, True):
        cp = params(identity, alternate)
        cp.openpilotLongitudinalControl = True
        frames = create_steering_messages(self.packer, cp, SimpleNamespace(ECAN=1, ACAN=0), True, True, 1)
        self.assertEqual([(f[0], f[2]) for f in frames], [(0x12A, 1), (0x110 if alternate else 0x50, 0)])
        self.assertEqual(frames[0][1][3:7], frames[1][1][3:7])
        self.assertEqual(frames[0][1][7:], bytes(6) + b'\x64' + bytes(2))
        damping = 100 if identity == CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN else 0
        self.assertEqual(frames[1][1][7:], bytes((0, damping)) + bytes(len(frames[1][1]) - 9))
