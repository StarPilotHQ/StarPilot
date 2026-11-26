import unittest

from opendbc.car.structs import CarParams
from opendbc.safety.tests.common import CANPackerSafety, make_msg
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.test_hyundai import checksum


class TestHyundaiKonaEvLabel(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.packer = CANPackerSafety("hyundai_can_generated")
    self.addCleanup(self.safety.set_alternative_experience, 0)

  def reset(self, word):
    self.safety.init_tests()
    self.safety.set_alternative_experience(32 if word & 0x400 else 0)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hyundai, word), 0)
    self.safety.set_aol_test_heartbeat(True)
    self.safety.set_timer(1000)
    self.counter = 0

  def rx(self, name, values, bus=0, fix_checksum=None):
    return self.safety.safety_rx_hook(self.packer.make_can_msg_safety(name, bus, values, fix_checksum=fix_checksum))

  def label(self, available, engaged, bus=0):
    self.assertTrue(self.rx("LABEL11", {"CC_React": available, "CC_Engaged": engaged}, bus))

  def button(self, button, main=False):
    self.assertTrue(self.rx("CLU11", {"CF_Clu_CruiseSwState": button, "CF_Clu_CruiseSwMain": main,
                                     "CF_Clu_AliveCnt1": self.counter % 16}))
    self.counter += 1

  def brake(self, pressed, counter):
    return self.rx("TCS13", {"DriverOverride": 2 if pressed else 0, "AliveCounterTCS": counter}, fix_checksum=checksum)

  def feed(self, *, with_label=True):
    self.assertTrue(self.rx("E_EMS11", {"Accel_Pedal_Pos": 0}))
    self.assertTrue(self.rx("MDPS12", {"CR_Mdps_StrColTq": 0}))
    self.assertTrue(self.rx("WHL_SPD11", {"WHL_SPD_FL": 40, "WHL_SPD_RR": 40}, fix_checksum=checksum))
    self.assertTrue(self.brake(False, 0))
    self.assertTrue(self.rx("BCM_PO_11", {"LDA_BTN": 0}))
    self.button(0)
    if with_label:
      self.label(True, False)

  def engage(self, button=2):
    self.button(button)
    self.button(0)
    self.label(True, True)
    self.assertTrue(self.safety.get_controls_allowed())

  def tx(self, name, values, bus=0):
    return self.safety.safety_tx_hook(self.packer.make_can_msg_safety(name, bus, values))

  def steer(self):
    return self.tx("LKAS11", {"CR_Lkas_StrToqReq": 1, "CF_Lkas_ActToi": 1})

  def software_set(self):
    return self.tx("CLU11", {"CF_Clu_CruiseSwState": 2})

  def test_exact_variants_and_physical_button_and_brake_release(self):
    for word in (0x1851, 0x1871, 0x1c51, 0x1c71):
      with self.subTest(word=hex(word)):
        self.reset(word)
        self.feed()
        self.label(True, True)
        self.assertFalse(self.steer())
        self.label(True, False)
        self.button(2)
        self.label(False, True)
        self.label(True, True, bus=1)
        self.assertTrue(self.rx("EMS12", {"ACC_ACT": 1}))
        self.assertFalse(self.steer())
        self.engage()
        self.safety.aol_set_host_request(1)
        self.assertTrue(self.steer())
        self.assertEqual(self.software_set(), bool(word & 0x20))
        for address, length in ((0x200, 6), (0x38D, 8), (0x420, 8), (0x421, 8), (0x50A, 8), (0x7D0, 8)):
          self.assertFalse(self.safety.safety_tx_hook(make_msg(0, address, length)))
        self.button(4)
        self.label(True, True)
        self.assertFalse(self.safety.get_controls_allowed())
        self.safety.aol_set_host_request(0)
        self.assertFalse(self.steer())
        self.assertFalse(self.software_set())
        self.label(True, False)
        self.engage(button=1)
        self.assertTrue(self.brake(True, 1))
        self.label(True, True)
        self.assertFalse(self.steer())
        self.assertFalse(self.software_set())

  def test_stale_label_and_bad_counter_deny_steering_and_buttons(self):
    self.reset(0x1871)
    self.feed()
    self.engage()
    self.safety.set_timer(302000)
    self.feed(with_label=False)
    self.assertFalse(self.steer())
    self.assertFalse(self.software_set())
    self.label(True, True)
    self.assertFalse(self.steer())
    self.label(True, False)
    self.engage()
    for _ in range(5):
      self.brake(False, 5)
    self.assertFalse(self.steer())
    self.assertFalse(self.software_set())

  def test_aol_uses_physical_lkas_with_main_off_and_requires_fresh_label(self):
    self.reset(0x1C71)
    self.feed(with_label=False)
    self.label(False, False)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.aol_set_host_request(0)
    self.assertTrue(self.rx("BCM_PO_11", {"LDA_BTN": 0}))
    self.assertTrue(self.rx("BCM_PO_11", {"LDA_BTN": 1}))
    self.safety.aol_set_host_request(3)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertTrue(self.steer())
    self.assertFalse(self.software_set())
    self.safety.set_timer(302000)
    self.feed(with_label=False)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertFalse(self.steer())
    self.label(False, False)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
