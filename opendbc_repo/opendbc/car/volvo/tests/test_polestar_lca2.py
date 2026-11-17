"""Polestar route ec2cab66ba: source +2/+4 counters wrap modulo15."""
import pytest
from opendbc.car.volvo.tests.test_cma import interface, sources, packet
from opendbc.safety.tests.libsafety import libsafety_py

RECORDED = [
  '180d7e0000be6b00',
  '1800d60000b32400',
  '1802020000b76a00',
  '1804630000bbb800',
  '1806b70000b09e00',
  '1808a10000b4d000',
  '180a750000b80200',
  '180c140000bc4c00',
  '180ec00000b10300',
  '1801bc0000b54d00',
  '1803680000b99f00',
  '1805090000bdd100',
  '1807dd0000b2b900',
  '1809cb0000b6f700',
  '180b1f0000ba2500',
]


def test_actual_factory_and_recorded_counter_wraps_remain_native_healthy():
  cp, ci = interface('POLESTAR_2')
  lib = libsafety_py.libsafety
  assert cp.safetyConfigs[0].safetyParam == 5
  assert ci.CC.lca_2_modulus == 15
  assert lib.set_safety_hooks(36, cp.safetyConfigs[0].safetyParam) == 0
  lib.init_tests()
  for i, payload in enumerate(RECORDED * 2):
    lib.set_timer(10000 + i * 20000)
    for msg in sources('POLESTAR_2', frame=i * 2):
      if msg[0] == 0x69:
        msg = (0x69, bytes.fromhex(payload), 0)
      assert lib.safety_rx_hook(packet(msg))
    lib.safety_tick()
    assert lib.safety_config_valid() and lib.get_controls_allowed()


@pytest.mark.parametrize('param', [3, 4, 6, 7, 65535])
def test_unknown_volvo_parameter_stays_denied(param):
  assert libsafety_py.libsafety.set_safety_hooks(36, param) != 0


@pytest.mark.parametrize('counter', [1, 2])
def test_frozen_recorded_counter_cannot_keep_native_permission(counter):
  from opendbc.car.volvo.helpers import checksum_lca_2_message, checksum_2_0x69_message
  lib = libsafety_py.libsafety
  assert lib.set_safety_hooks(36, 5) == 0
  lib.init_tests()
  for i in range(20):
    lib.set_timer(10000 + i * 20000)
    for msg in sources('POLESTAR_2', frame=i * 2):
      if msg[0] == 0x69 and i > 5:
        d = bytearray(msg[1])
        byte = 1 if counter == 1 else 5
        d[byte] = (d[byte] & 0xF0) | 10
        d[6] = checksum_lca_2_message(d[0], d[5])
        d[2] = checksum_2_0x69_message(d[0], d[1], d[3], d[4])
        msg = (msg[0], bytes(d), msg[2])
      lib.safety_rx_hook(packet(msg))
    lib.safety_tick()
  assert not lib.safety_config_valid() and not lib.get_controls_allowed()


@pytest.mark.parametrize('byte', [1, 5])
def test_reserved_mod15_counter_is_rejected_even_with_valid_checksums(byte):
  from opendbc.car.volvo.helpers import checksum_lca_2_message, checksum_2_0x69_message
  lib = libsafety_py.libsafety
  assert lib.set_safety_hooks(36, 5) == 0
  lib.init_tests()
  msg = next(msg for msg in sources('POLESTAR_2') if msg[0] == 0x69)
  d = bytearray(msg[1])
  d[byte] |= 15
  d[6] = checksum_lca_2_message(d[0], d[5])
  d[2] = checksum_2_0x69_message(d[0], d[1], d[3], d[4])
  assert not lib.safety_rx_hook(packet((0x69, bytes(d), 0)))
