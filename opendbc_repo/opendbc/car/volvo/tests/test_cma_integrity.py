"""Actual native RX/TX callers reject corrupted known checksum domains."""
import pytest
from opendbc.car.volvo.helpers import checksum_1_pscm_related_message, checksum_2_pscm_related_message
from opendbc.car.volvo.tests.test_cma import CMA, interface, sources, packet
from opendbc.safety.tests.libsafety import libsafety_py


def feed(identity):
  cp, _ = interface(identity)
  lib = libsafety_py.libsafety
  assert lib.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  lib.init_tests()
  lib.set_timer(10000)
  incoming = sources(identity)
  for message in incoming:
    assert lib.safety_rx_hook(packet(message)), message
  lib.safety_tick()
  assert lib.safety_config_valid() and lib.get_controls_allowed()
  return lib, incoming


@pytest.mark.parametrize('identity', CMA)
@pytest.mark.parametrize('address,checksum_byte', [(0x69, 6), (0x69, 2), (0x67, 2), (0x17, 0), (0x17, 3)])
def test_real_native_rx_rejects_each_known_checksum(identity, address, checksum_byte):
  lib, incoming = feed(identity)
  address, data, bus = next(msg for msg in incoming if msg[0] == address)
  changed = bytearray(data)
  changed[checksum_byte] ^= 1
  assert not lib.safety_rx_hook(packet((address, bytes(changed), bus)))
  lib.safety_tick()
  assert not lib.safety_config_valid()
  assert not lib.get_controls_allowed()


@pytest.mark.parametrize('identity', CMA)
@pytest.mark.parametrize('address,checksum_byte', [(0x69, 6), (0x69, 2), (0x67, 2), (0x17, 0), (0x17, 3)])
def test_real_native_tx_rejects_each_known_checksum(identity, address, checksum_byte):
  lib, incoming = feed(identity)
  address, data, source_bus = next(msg for msg in incoming if msg[0] == address)
  output_bus = 2 if source_bus == 0 else 0
  assert lib.safety_tx_hook(packet((address, data, output_bus)))
  changed = bytearray(data)
  changed[checksum_byte] ^= 1
  assert not lib.safety_tx_hook(packet((address, bytes(changed), output_bus)))


@pytest.mark.parametrize('identity', CMA)
@pytest.mark.parametrize('kind', ['unreplicated', 'reserved_counter', 'unknown_domain', 'unknown_echo'])
def test_recomputed_crc_cannot_validate_unknown_pscm_counter_domain(identity, kind):
  lib, incoming = feed(identity)
  address, data, bus = next(msg for msg in incoming if msg[0] == 0x17)
  changed = bytearray(data)
  if kind == 'unreplicated':
    # A one-count replica lag is supported on Polestar; two counts is not.
    changed[1] ^= 0x20
  elif kind == 'reserved_counter':
    changed[1] = 0xF0
    changed[2] = 0x8F
  elif kind == 'unknown_echo':
    changed[1] |= 8
  else:
    changed[2] = 0
  changed[0] = checksum_1_pscm_related_message(changed[1], changed[2])
  changed[3] = checksum_2_pscm_related_message(changed[2])
  assert not lib.safety_tx_hook(packet((address, bytes(changed), 0)))
  assert not lib.safety_rx_hook(packet((address, bytes(changed), bus)))


@pytest.mark.parametrize('identity', CMA)
def test_every_documented_pscm_counter_value_is_accepted(identity):
  lib, incoming = feed(identity)
  address, data, bus = next(msg for msg in incoming if msg[0] == 0x17)
  for counter in range(15):
    changed = bytearray(data)
    changed[1] = (counter << 4) | (changed[1] & 0xF)
    changed[2] = 0x80 | counter
    changed[0] = checksum_1_pscm_related_message(changed[1], changed[2])
    changed[3] = checksum_2_pscm_related_message(changed[2])
    assert lib.safety_rx_hook(packet((address, bytes(changed), bus)))
    assert lib.safety_tx_hook(packet((address, bytes(changed), 0)))
