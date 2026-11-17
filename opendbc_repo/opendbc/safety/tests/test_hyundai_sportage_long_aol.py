import unittest
from types import SimpleNamespace

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.hyundai.hyundaicanfd import create_steering_messages
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.car.hyundai.canfd_stock_aol import SPORTAGE_LONG_AOL_WORDS
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests import test_hyundai_shared_canfd_stock_aol as fixtures


class TestSportageLongAol(unittest.TestCase):
  packet = staticmethod(fixtures.TestHyundaiSharedCanfdStockAol.packet)
  healthy = fixtures.TestHyundaiSharedCanfdStockAol.healthy
  gesture = fixtures.TestHyundaiSharedCanfdStockAol.gesture

  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.packer = CANPacker('hyundai_canfd_generated')
    self.release = self.safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) != 0

  def tearDown(self):
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  def mode(self, word, experience=32):
    self.safety.set_alternative_experience(experience)
    assert self.safety.set_safety_hooks(structs.CarParams.SafetyModel.hyundaiCanfd, word) == 0
    self.safety.init_tests()
    self.safety.set_timer(1_000_000)

  def test_physical_arm_cancel_pedals_and_ungranted_longitudinal(self):
    for word in SPORTAGE_LONG_AOL_WORDS:
      with self.subTest(word=hex(word)):
        self.mode(word)
        if self.release:
          assert not self.safety.safety_config_valid()
          continue
        self.healthy(word)
        self.safety.set_aol_test_heartbeat(True)
        self.safety.aol_set_host_request(3)
        assert self.safety.aol_get_permission_mask() == 0
        self.gesture(word)
        assert self.safety.aol_get_permission_mask() == 1
        bus = 1 if word & 16 else 0
        flags = HyundaiFlags.CANFD
        if word & 16:
          flags |= HyundaiFlags.CANFD_LKA_STEER_MSG
        if word & 128:
          flags |= HyundaiFlags.CANFD_LKA_STEER_MSG_ALT
        cp = SimpleNamespace(carFingerprint=CAR.KIA_SPORTAGE_5TH_GEN, flags=int(flags), openpilotLongitudinalControl=True)
        frames = create_steering_messages(self.packer, cp, SimpleNamespace(ECAN=bus, ACAN=1-bus), True, True, 1)
        for frame in frames:
          assert self.safety.safety_tx_hook(self.packet(frame))
        excessive = create_steering_messages(self.packer, cp, SimpleNamespace(ECAN=bus, ACAN=1-bus), True, True, 410)
        assert not self.safety.safety_tx_hook(self.packet(excessive[0]))
        name = 'CRUISE_BUTTONS_ALT' if word & 32 else 'CRUISE_BUTTONS'
        frame = self.packer.make_can_msg(name, bus, {'COUNTER': 9, 'CRUISE_BUTTONS': 4})
        assert self.safety.safety_rx_hook(self.packet(frame))
        assert self.safety.aol_get_permission_mask() == 1
        for counter, brake in ((7, True), (8, False)):
          frame = self.packer.make_can_msg('TCS', bus, {'COUNTER': counter, 'DriverBraking': brake})
          assert self.safety.safety_rx_hook(self.packet(frame))
          assert self.safety.aol_get_permission_mask() == 1
        accel = self.packet(self.packer.make_can_msg('SCC_CONTROL', bus, {'ACCMode': 1, 'aReqRaw': .1, 'aReqValue': .1}))
        assert not self.safety.safety_tx_hook(accel)
        self.safety.set_timer(1_500_001)
        assert self.safety.aol_get_permission_mask() == 0

  def test_wrong_experience_missing_source_and_wrong_bus_deny(self):
    for word in SPORTAGE_LONG_AOL_WORDS:
      for experience in (0, 1, 33):
        self.mode(word, experience)
        assert not self.safety.safety_config_valid()
      if self.release:
        continue
      fuel = 'ACCELERATOR_ALT' if word & 2 else 'ACCELERATOR_BRAKE_ALT'
      buttons = 'CRUISE_BUTTONS_ALT' if word & 32 else 'CRUISE_BUTTONS'
      for omitted in (fuel, buttons, 'TCS', 'WHEEL_SPEEDS', 'MDPS'):
        self.mode(word)
        self.healthy(word, omit=omitted)
        if omitted != buttons:
          self.gesture(word)
        assert self.safety.aol_get_permission_mask() == 0

  def test_fresh_set_release_arms_lateral_through_cancel_and_brake(self):
    for word in SPORTAGE_LONG_AOL_WORDS:
      self.mode(word)
      if self.release:
        assert not self.safety.safety_config_valid()
        continue
      name = 'CRUISE_BUTTONS_ALT' if word & 32 else 'CRUISE_BUTTONS'
      maximum = 255 if word & 32 else 14
      bus = 1 if word & 16 else 0
      self.healthy(word, omit=name)
      self.safety.set_aol_test_heartbeat(True)
      self.safety.aol_set_host_request(3)
      for tick, counter, button in ((0, maximum - 1, 0), (1, maximum, 2), (2, 0, 0)):
        self.safety.set_timer(1_000_000 + tick * 20_000)
        frame = self.packer.make_can_msg(name, bus, {'COUNTER': counter, 'CRUISE_BUTTONS': button})
        assert self.safety.safety_rx_hook(self.packet(frame))
      self.safety.aol_set_host_request(3)
      assert self.safety.get_controls_allowed()
      assert self.safety.aol_get_permission_mask() == 3
      self.safety.set_timer(1_060_000)
      frame = self.packer.make_can_msg(name, bus, {'COUNTER': 1, 'CRUISE_BUTTONS': 4})
      assert self.safety.safety_rx_hook(self.packet(frame))
      assert not self.safety.get_controls_allowed()
      assert self.safety.aol_get_permission_mask() == 1, hex(word)
      frame = self.packer.make_can_msg('TCS', bus, {'COUNTER': 7, 'DriverBraking': True})
      assert self.safety.safety_rx_hook(self.packet(frame))
      assert self.safety.aol_get_permission_mask() == 1
