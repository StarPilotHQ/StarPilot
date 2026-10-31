"""Physical stock-resume permission never acquires openpilot steering authority."""
from opendbc.car.structs import CarParams
from opendbc.safety.tests import common
from opendbc.safety.tests.libsafety import libsafety_py
import unittest

from opendbc.safety.tests import test_ford as ford

Buttons = ford.Buttons


class TestFordStockSwitch(unittest.TestCase):
  SAFETY_PARAM = 2
  def setUp(self):
    libsafety_py.libsafety.set_alternative_experience(0)
    self._message_fixture = ford.TestFordCANFDStockSafety()
    self._message_fixture.SAFETY_PARAM = self.SAFETY_PARAM
    self._message_fixture.setUp()

  def tearDown(self):
    libsafety_py.libsafety.set_alternative_experience(0)

  def __getattr__(self, name):
    fixture = self.__dict__.get("_message_fixture")
    if fixture is None:
      raise AttributeError(name)
    return getattr(fixture, name)


  def select(self, word=2, ae=0):
    self.safety.set_alternative_experience(ae)
    self.safety.set_safety_hooks(CarParams.SafetyModel.ford, word)
    self.safety.set_timer(0)

  def cruise(self, state):
    return self.packer.make_can_msg_safety("EngBrakeData", 0, {"CcStat_D_Actl": state, "BpedDrvAppl_D_Actl": 1})

  def physical_switch(self, pressed, bus=0):
    return self.packer.make_can_msg_safety("Steering_Data_FD1", bus, {"CcAslButtnCnclResPress": int(pressed)})

  def healthy(self, word=2, button=True, main=True):
    for _ in range(6):
      for msg in (self._speed_msg(5.0), self._speed_msg_2(5.0), self._yaw_rate_msg(0.0, 5.0),
                  self._user_gas_msg(0.0), self._vehicle_moving_msg(5.0), self.cruise(3 if main else 0)):
        self.assertTrue(self._rx(msg))
    if word == 12:
      self.assertTrue(self._rx(common.make_msg(0, 0x3CC)))
    if button is not None:
      self.assertTrue(self._rx(self.physical_switch(button)))

  def test_physical_available_inactive_resume_does_not_enable_controls(self):
    for word in (0, 2, 8, 10, 12):
      with self.subTest(word=word):
        self.select(word)
        self.healthy(word)
        self.assertFalse(self.safety.get_controls_allowed())
        for bus in (0, 2):
          self.assertTrue(self._tx(self._acc_button_msg(Buttons.RESUME, bus)))
          self.assertFalse(self._tx(self._acc_button_msg(Buttons.CANCEL, bus)))
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertEqual(self.safety.safety_fwd_hook(0, 0x83), 2)
        self.assertEqual(self.safety.safety_fwd_hook(2, 0x83), 0)

  def test_release_main_off_missing_and_wrong_bus_deny_resume(self):
    for case in ("release", "main_off", "missing", "wrong_bus"):
      with self.subTest(case=case):
        self.select()
        self.healthy(button=None if case in ("missing", "wrong_bus") else True)
        if case == "release":
          self.assertTrue(self._rx(self.physical_switch(False)))
        elif case == "main_off":
          self.assertTrue(self._rx(self.cruise(0)))
        elif case == "wrong_bus":
          self.assertTrue(self._rx(self.physical_switch(True, bus=2)))
        self.assertFalse(self._tx(self._acc_button_msg(Buttons.RESUME, 0)))

  def test_stale_required_rx_and_profile_reset_revoke_exception(self):
    self.select()
    self.healthy()
    self.assertTrue(self._tx(self._acc_button_msg(Buttons.RESUME, 0)))
    self.safety.set_timer(1_000_001)
    self.healthy(button=None)
    self.assertFalse(self._tx(self._acc_button_msg(Buttons.RESUME, 0)))
    self.select()
    self.healthy(button=None)
    self.assertFalse(self._tx(self._acc_button_msg(Buttons.RESUME, 0)))

  def test_long_unknown_ae_and_unreached_words_cannot_use_exception(self):
    for word, ae in ((4, 0), (1, 0), (3, 0), (9, 0), (11, 0), (13, 0), (34, 0), (4, 32)):
      with self.subTest(word=word, ae=ae):
        self.select(word, ae)
        self.healthy(word)
        self.assertFalse(self._tx(self._acc_button_msg(Buttons.RESUME, 0)))
        self.assertFalse(self.safety.get_controls_allowed())
