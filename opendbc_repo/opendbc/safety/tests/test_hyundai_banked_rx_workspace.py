"""Banked CAN-FD RX definitions and status must survive warm profile switches."""
import unittest

from opendbc.safety.tests.libsafety import libsafety_py


libsafety_py.ffi.cdef('extern safety_config current_safety_config;', override=True)

# word, powertrain bus, fuel address, SCC bus, alternate buttons, separate gear
PROFILES = (
  (0x0810, 1, 0x100, 1, False, False),
  (0x0812, 1, 0x105, 1, False, False),
  (0x2830, 1, 0x100, 1, True, False),
  (0x2832, 1, 0x105, 1, True, False),
  (0x0808, 0, 0x100, 2, False, False),
  (0x0809, 0, 0x035, 2, False, False),
  (0x080A, 0, 0x105, 2, False, False),
  (0x0828, 0, 0x100, 2, True, False),
  (0x0829, 0, 0x035, 2, True, False),
  (0x082A, 0, 0x105, 2, True, False),
  (0x0800, 0, 0x100, 0, False, False),
  (0x0801, 0, 0x035, 0, False, False),
  (0x0802, 0, 0x105, 0, False, False),
  (0x0820, 0, 0x100, 0, True, False),
  (0x0821, 0, 0x035, 0, True, False),
  (0x0822, 0, 0x105, 0, True, False),
  (0x4010, 1, 0x100, 1, False, True),
  (0x4412, 1, 0x105, 1, False, True),
  (0x4811, 1, 0x035, 1, False, False),
  (0x4008, 0, 0x100, 2, False, True),
  (0x440A, 0, 0x105, 2, False, True),
  (0x4809, 0, 0x035, 2, False, False),
  (0x4028, 0, 0x100, 2, True, True),
  (0x442A, 0, 0x105, 2, True, True),
  (0x4829, 0, 0x035, 2, True, False),
)
PROFILE_BY_WORD = {profile[0]: profile for profile in PROFILES}


def _message(msg):
  return (msg.addr, msg.bus, msg.len, msg.frequency, msg.ignore_checksum,
          msg.ignore_counter, msg.max_counter, msg.ignore_quality_flag)


def _status(status):
  return (status.msg_seen, status.index, status.valid_checksum, status.wrong_counters,
          status.valid_quality_flag, status.last_counter, status.last_timestamp, status.lagging)


def _assert_profile(safety, profile):
  _, bus, fuel, scc_bus, alternate, gear = profile
  expected = [
    (fuel, bus, 32, 100, False, False, 255, True),
    (0x175, bus, 24, 50, False, False, 255, True),
    (0x0A0, bus, 24, 100, False, False, 255, True),
    (0x0EA, bus, 24, 100, False, False, 255, True),
    (0x1AA, bus, 16, 50, True, False, 255, True) if alternate else
    (0x1CF, bus, 8, 50, True, False, 15, True),
    (0x1A0, scc_bus, 32, 50, False, False, 255, True),
  ]
  if gear:
    expected.append((0x70, bus, 32, 100, False, False, 255, True))
  config = safety.current_safety_config
  assert config.rx_checks_len == len(expected)
  assert config.rx_checks != libsafety_py.ffi.NULL
  for index, message in enumerate(expected):
    check = config.rx_checks[index]
    assert _message(check.msg[0]) == message
    assert _message(check.msg[1]) == (0, 0, 0, 0, False, False, 0, False)
    assert _message(check.msg[2]) == (0, 0, 0, 0, False, False, 0, False)
    assert _status(safety.rx_status[index]) == (False, 0, False, 0, False, 0, 0, False)


def _dirty_status(safety):
  config = safety.current_safety_config
  for index in range(config.rx_checks_len):
    status = safety.rx_status[index]
    # Valid C values dirty every field, without relying on structure padding.
    status.msg_seen = True
    status.index = 2
    status.valid_checksum = True
    status.wrong_counters = 5
    status.valid_quality_flag = True
    status.last_counter = 7
    status.last_timestamp = 123_456 + index
    status.lagging = True
    assert _status(status) == (True, 2, True, 5, True, 7, 123_456 + index, True)


def _select(safety, word):
  safety.set_alternative_experience(32)
  assert safety.set_safety_hooks(safety.SAFETY_HYUNDAI_CANFD, word) == 0
  # Do not call init_tests: the production mode-init path must reset the status.
  _assert_profile(safety, PROFILE_BY_WORD[word])


class TestHyundaiBankedRxWorkspace(unittest.TestCase):
  RELEASE = False

  @classmethod
  def setUpClass(cls):
    # Separate real native libraries exercise both build policies.
    cls.safety = libsafety_py.ffi.dlopen(libsafety_py._build_libsafety(release=cls.RELEASE))

  @classmethod
  def tearDownClass(cls):
    cls.safety.set_alternative_experience(0)
    assert cls.safety.set_safety_hooks(cls.safety.SAFETY_SILENT, 0) == 0

  def test_same_mode_reinitialization_restores_every_status_field(self):
    for profile in PROFILES:
      with self.subTest(word=hex(profile[0])):
        _select(self.safety, profile[0])
        _dirty_status(self.safety)
        _select(self.safety, profile[0])

  def test_seven_six_seven_switches_replace_definitions_and_status(self):
    for words in ((0x4412, 0x4829, 0x4008, 0x0810, 0x442A),
                  (0x4028, 0x2832, 0x4010, 0x0801, 0x440A),
                  (0x4412, 0x0820, 0x442A, 0x4811, 0x4010)):
      with self.subTest(words=tuple(hex(word) for word in words)):
        for word in words:
          _select(self.safety, word)
          _dirty_status(self.safety)


class TestHyundaiBankedRxWorkspaceRelease(TestHyundaiBankedRxWorkspace):
  RELEASE = True


if __name__ == '__main__':
  unittest.main()
