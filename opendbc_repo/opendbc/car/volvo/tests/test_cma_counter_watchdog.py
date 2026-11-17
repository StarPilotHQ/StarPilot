"""Real RX watchdog rejects a fresh-checksummed frozen source counter."""
import pytest
from opendbc.car.volvo.tests.test_cma import CMA, packet, sources, interface
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.car.volvo.tests.test_cma_integrity import feed
from opendbc.car.volvo.helpers import checksum_lca_2_message, checksum_2_0x69_message


def lca2(base, raw, modulus=16):
  address, payload, bus = base
  d = bytearray(payload)
  d[1] = (d[1] & 0xF0) | raw
  d[5] = (d[5] & 0xF0) | ((raw * 2) % 15 if modulus == 15 else (raw // 2 * 4) % 16)
  d[6] = checksum_lca_2_message(d[0], d[5])
  d[2] = checksum_2_0x69_message(d[0], d[1], d[3], d[4])
  return address, bytes(d), bus


@pytest.mark.parametrize('identity', CMA)
@pytest.mark.parametrize('parity', [0, 1])
def test_known_plus_two_counter_wraps_for_either_parity(identity, parity):
  modulus = 15 if identity == 'POLESTAR_2' else 16
  cp, _ = interface(identity)
  lib = libsafety_py.libsafety
  assert lib.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  lib.init_tests()
  lib.set_timer(10000)
  incoming = sources(identity)
  for msg in incoming:
    assert lib.safety_rx_hook(packet(lca2(msg, parity, modulus) if msg[0] == 0x69 else msg))
  base = next(msg for msg in incoming if msg[0] == 0x69)
  # Unrelated required receiver frames continue with valid original payloads.
  for sample in range(1, 41):
    lib.set_timer(10000 + sample * 20000)
    for msg in incoming:
      if msg[0] != 0x69:
        assert lib.safety_rx_hook(packet(msg))
    assert lib.safety_rx_hook(packet(lca2(base, (sample * 2 + parity) % modulus, modulus)))
    lib.safety_tick()
    assert lib.safety_config_valid() and lib.get_controls_allowed()


@pytest.mark.parametrize('identity', CMA)
def test_valid_crc_and_fresh_arrivals_cannot_hide_frozen_counter(identity):
  modulus = 15 if identity == 'POLESTAR_2' else 16
  lib, incoming = feed(identity)
  base = next(msg for msg in incoming if msg[0] == 0x69)
  for sample in range(1, 11):
    lib.set_timer(10000 + sample * 20000)
    for msg in incoming:
      if msg[0] != 0x69:
        lib.safety_rx_hook(packet(msg))
    assert lib.safety_rx_hook(packet(lca2(base, sample * 2 % modulus, modulus)))
  frozen = lca2(base, 4, modulus)
  for sample in range(11, 20):
    lib.set_timer(10000 + sample * 20000)
    for msg in incoming:
      if msg[0] != 0x69:
        lib.safety_rx_hook(packet(msg))
    lib.safety_rx_hook(packet(frozen))
    lib.safety_tick()
  assert not lib.safety_config_valid()
  assert not lib.get_controls_allowed()


@pytest.mark.parametrize('identity', ['VOLVO_XC40_RECHARGE', 'VOLVO_S60_RECHARGE'])
@pytest.mark.parametrize('kind', ['primary_low_phase', 'secondary_low_phase', 'secondary_relative_offset'])
def test_crc_recomputed_counter_phase_corruption_cannot_become_native_evidence(identity, kind):
  lib, incoming = feed(identity)
  address, payload, bus = next(msg for msg in incoming if msg[0] == 0x69)
  d = bytearray(payload)
  if kind == 'primary_low_phase':
    d[1] ^= 1
  elif kind == 'secondary_low_phase':
    d[5] ^= 1
  else:
    d[5] ^= 4
  d[6] = checksum_lca_2_message(d[0], d[5])
  d[2] = checksum_2_0x69_message(d[0], d[1], d[3], d[4])
  assert not lib.safety_rx_hook(packet((address, bytes(d), bus)))
  lib.safety_tick()
  assert not lib.safety_config_valid() and not lib.get_controls_allowed()
