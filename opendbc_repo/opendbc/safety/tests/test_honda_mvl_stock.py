import unittest

from opendbc.car import structs
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py


def check_physical_pcm_camera_override_cancel_and_validated_arrival_expiry(word):
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(0)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.hondaBosch, word) == 0
  packer = CANPackerSafety("honda_common_canfd_generated")

  def packet(name, values, bus=0):
    return packer.make_can_msg_safety(name, bus, values)

  def physical(cruise=0):
    messages = [packet("SCM_BUTTONS", {"CRUISE_BUTTONS": 0}),
                packet("SCM_FEEDBACK", {"MAIN_ON": 1}),
                packet("ENGINE_DATA", {"XMISSION_SPEED": 30}),
                packet("POWERTRAIN_DATA", {"ACC_STATUS": cruise, "PEDAL_GAS": 0, "BRAKE_PRESSED": 0})]
    if word == 81:
      messages.append(packet("BRAKE_MODULE", {"BRAKE_PRESSED": 0}))
    for message in messages:
      assert safety.safety_rx_hook(message)
  physical(0)
  assert not safety.get_controls_allowed()
  for bus in (0, 2):
    assert not safety.safety_tx_hook(packet("SCM_BUTTONS", {"CRUISE_BUTTONS": 0}, bus))
    assert safety.safety_tx_hook(packet("SCM_BUTTONS", {"CRUISE_BUTTONS": 2}, bus))
  assert safety.safety_fwd_hook(0, 0x296) == 2
  physical(1)
  assert safety.get_controls_allowed()
  assert safety.safety_tx_hook(packet("SCM_BUTTONS", {"CRUISE_BUTTONS": 0, "CRUISE_SETTING": 1}, 2))
  assert safety.safety_fwd_hook(0, 0x296) == -1
  for _ in range(10):
    safety.safety_rx_hook(packet("SCM_BUTTONS", {"CRUISE_BUTTONS": 0}, 1))
  assert safety.safety_fwd_hook(0, 0x296) == -1
  for remaining in range(9, -1, -1):
    assert safety.safety_rx_hook(packet("SCM_BUTTONS", {"CRUISE_BUTTONS": 0}))
    assert safety.safety_fwd_hook(0, 0x296) == (-1 if remaining else 2)
  assert safety.safety_fwd_hook(0, 0x296) == 2
  assert safety.safety_tx_hook(packet("SCM_BUTTONS", {"CRUISE_BUTTONS": 0}, 2))
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.hondaBosch, 16) == 0
  assert safety.safety_fwd_hook(0, 0x296) == 2
  assert not safety.safety_tx_hook(packet("SCM_BUTTONS", {"CRUISE_BUTTONS": 2}, 2))
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.hondaBosch, word) == 0
  physical(0)
  assert not safety.get_controls_allowed()
  assert safety.safety_fwd_hook(0, 0x296) == 2
  assert not safety.safety_tx_hook(packet("SCM_BUTTONS", {"CRUISE_BUTTONS": 4}, 2))


def check_unqualified_mvl_words_and_independent_axis_have_no_transmit_table(word, experience):
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(experience)
  safety.set_safety_hooks(structs.CarParams.SafetyModel.hondaBosch, word)
  packer = CANPackerSafety("honda_common_canfd_generated")
  for bus in (0, 2):
    assert not safety.safety_tx_hook(packer.make_can_msg_safety("SCM_BUTTONS", bus, {"CRUISE_BUTTONS": 2}))


class TestHondaMvlStock(unittest.TestCase):
  def tearDown(self):
    safety = libsafety_py.libsafety
    safety.init_tests()
    safety.set_alternative_experience(0)
    safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  def test_physical_pcm_camera_override_cancel_and_validated_arrival_expiry(self):
    for word in (80, 81):
      with self.subTest(word=word):
        check_physical_pcm_camera_override_cancel_and_validated_arrival_expiry(word)

  def test_unqualified_mvl_words_and_independent_axis_have_no_transmit_table(self):
    for word, experience in ((64, 0), (82, 0), (83, 0)):
      with self.subTest(word=word, experience=experience):
        check_unqualified_mvl_words_and_independent_axis_have_no_transmit_table(word, experience)


def test_camera_lkas_pulses_cannot_create_physical_aol_grant():
  from opendbc.safety.tests.test_honda_stock_aol import TestHondaStockAol
  for word in (80, 81):
    case = TestHondaStockAol(methodName='runTest')
    try:
      case.configure(('mvl-aol', 20, word, 0, 'honda_common_canfd_generated', False, 'GEARBOX_AUTO', 4))
      case.stream()
      assert case.safety.aol_get_permission_mask() == 0
      for _ in range(12):
        # Host-authored camera bus output is never a physical PT-bus gesture.
        case.safety.safety_rx_hook(case.packet('SCM_BUTTONS', {'CRUISE_BUTTONS': 0, 'CRUISE_SETTING': 1}, bus=2))
        assert case.safety.aol_get_permission_mask() == 0
      case.normal_engage()
      case.press_brake()
      assert not case.safety.get_controls_allowed()
      assert case.safety.aol_get_permission_mask() == 1
      # Independent LAT does not authorize camera button spam or ordinary LONG.
      assert not case.safety.safety_tx_hook(case.packet('SCM_BUTTONS', {'CRUISE_BUTTONS': 0, 'CRUISE_SETTING': 1}, bus=2))
    finally:
      case.tearDown()
