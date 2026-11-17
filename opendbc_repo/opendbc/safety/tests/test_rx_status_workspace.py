import pytest

from opendbc.safety.tests.libsafety import libsafety_py

libsafety_py.ffi.cdef('extern safety_config current_safety_config;', override=True)


@pytest.mark.parametrize('release', (False, True))
def test_largest_rx_configuration_and_mode_switch_reset(release):
  assert libsafety_py.libsafety is not None
  previous = libsafety_py.libpath
  native = libsafety_py.ffi.dlopen(libsafety_py._build_libsafety(release=release))
  try:
    assert native.set_safety_hooks(36, 0) == 0
    assert native.current_safety_config.rx_checks_len == 17
    assert len(native.rx_status) >= native.current_safety_config.rx_checks_len
    for mode, word in ((28, 0), (6, 0), (36, 0)):
      for status in native.rx_status:
        status.msg_seen = True
        status.index = 2
        status.valid_checksum = True
        status.valid_quality_flag = True
        status.wrong_counters = 5
        status.last_counter = 7
        status.last_timestamp = 123456
        status.lagging = True
      assert native.set_safety_hooks(mode, word) == 0
      for index in range(native.current_safety_config.rx_checks_len):
        status = native.rx_status[index]
        assert not status.msg_seen and status.index == 0
        assert not status.valid_checksum and not status.valid_quality_flag
        assert status.wrong_counters == status.last_counter == status.last_timestamp == 0
        assert not status.lagging
  finally:
    native.set_safety_hooks(19, 0)
    libsafety_py.load(previous)
