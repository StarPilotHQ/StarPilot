import pytest

from opendbc.car import structs
from opendbc.car.hyundai.daw import DawSource
from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
from opendbc.car.hyundai.tests.test_daw import BASE, params, source
from opendbc.safety.tests.libsafety import libsafety_py


PROFILES = [(False, 0), (False, 0xC02), (True, 0x11), (True, 0x91), (True, 0x811), (True, 0x891),
            (True, 0x8015), (True, 0x8095), (True, 0x8815), (True, 0x8895)]


def packet(addr, data, bus):
  return libsafety_py.make_CANPacket(addr, bus, data)


def setup(canfd, word):
  safety = libsafety_py.libsafety
  debug = safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) == 0
  safety.set_alternative_experience(32 if word & 0x800 else 0)
  mode = structs.CarParams.SafetyModel.hyundaiCanfd if canfd else structs.CarParams.SafetyModel.hyundai
  assert safety.set_safety_hooks(mode, word) == 0
  safety.init_tests()
  safety.set_timer(1_000_000)
  return safety, debug or not (canfd and word & 0x8000)


@pytest.mark.parametrize('canfd,word', PROFILES)
@pytest.mark.parametrize('status', range(8))
def test_actual_host_commands_obey_native_source_payload_and_expiry(canfd, word, status):
  safety, supported = setup(canfd, word)
  owner = DawSource(params(canfd))
  data = source(canfd, status)
  owner.update([(BASE, [(owner.address, data, owner.source_bus)])])
  addr, output, bus = owner.command(BASE)[0]
  assert not safety.safety_tx_hook(packet(addr, output, bus))
  safety.safety_rx_hook(packet(addr, data, owner.source_bus))
  assert bool(safety.safety_tx_hook(packet(addr, output, bus))) == supported
  if not supported:
    return
  assert not safety.safety_tx_hook(packet(addr, output, bus))
  safety.set_timer(1_010_000)
  for bit in range(len(output) * 8):
    bad = bytearray(output)
    bad[bit // 8] ^= 1 << (bit % 8)
    assert not safety.safety_tx_hook(packet(addr, bad, bus)), bit
  assert safety.safety_tx_hook(packet(addr, output, bus))
  safety.set_timer(1_100_000)
  assert safety.safety_tx_hook(packet(addr, output, bus))
  safety.set_timer(1_100_001)
  assert not safety.safety_tx_hook(packet(addr, output, bus))
  assert not safety.get_controls_allowed()


@pytest.mark.parametrize('canfd,fault', [(canfd, fault) for canfd in (False, True)
  for fault in ('length', 'bus', 'crc', 'counter', 'reset', 'relay') if canfd or fault not in ('crc', 'counter')])
def test_source_rejection_reset_and_relay_are_preserved(canfd, fault):
  safety, _ = setup(canfd, 0x11 if canfd else 0)
  owner = DawSource(params(canfd))
  data = source(canfd, 1)
  owner.update([(BASE, [(owner.address, data, owner.source_bus)])])
  addr, output, bus = owner.command(BASE)[0]
  incoming = data[:-4] if fault == 'length' and canfd else data[:-1] if fault == 'length' else data
  rx_bus = owner.source_bus + 1 if fault == 'bus' else owner.source_bus
  if fault == 'crc':
    incoming = bytes([incoming[0] ^ 1]) + incoming[1:]
  safety.safety_rx_hook(packet(addr, incoming, rx_bus))
  if fault == 'counter':
    safety.set_timer(1_010_000)
    safety.safety_rx_hook(packet(addr, incoming, rx_bus))
  elif fault == 'reset':
    setup(canfd, 0x11 if canfd else 0)
  elif fault == 'relay':
    safety.set_relay_malfunction(True)
  assert not safety.safety_tx_hook(packet(addr, output, bus))


def test_forwarding_and_other_profiles_remain_unchanged():
  safety, _ = setup(False, 0)
  assert safety.safety_fwd_hook(2, 0x53E) == 0
  assert safety.safety_fwd_hook(0, 0x53E) == 2
  safety.set_safety_hooks(structs.CarParams.SafetyModel.hyundaiCanfd, 0)
  safety.init_tests()
  data = source(True, 1)
  safety.safety_rx_hook(packet(0x11A, data, 0))
  changed = bytearray(data)
  changed[7] &= ~0x38
  changed[:2] = hkg_can_fd_checksum(0x11A, None, changed).to_bytes(2, 'little')
  assert not safety.safety_tx_hook(packet(0x11A, changed, 0))


def test_one_genuine_counter_predecessor_cannot_hide_newer_hands_off_request():
  safety, _ = setup(True, 0x11)
  owner = DawSource(params(True))
  data = source(True, 1)
  owner.update([(BASE, [(0x11A, data, 1)])])
  _, output, _ = owner.command(BASE)[0]
  safety.safety_rx_hook(packet(0x11A, data, 1))
  following = bytearray(data)
  following[2] += 1
  following[:2] = hkg_can_fd_checksum(0x11A, None, following).to_bytes(2, 'little')
  safety.set_timer(1_010_000)
  safety.safety_rx_hook(packet(0x11A, following, 1))
  assert safety.safety_tx_hook(packet(0x11A, output, 1))
  safety.set_timer(1_015_000)
  newer = bytearray(following)
  newer[2] += 1
  newer[:2] = hkg_can_fd_checksum(0x11A, None, newer).to_bytes(2, 'little')
  safety.safety_rx_hook(packet(0x11A, newer, 1))
  assert not safety.safety_tx_hook(packet(0x11A, output, 1))
  owner.update([(BASE + 10_000_000, [(0x11A, bytes(following), 1)])])
  _, prior_output, _ = owner.command(BASE + 10_000_000)[0]
  assert safety.safety_tx_hook(packet(0x11A, prior_output, 1))
  hands_off = bytearray(newer)
  hands_off[2] += 1
  hands_off[7] = (hands_off[7] & ~0x38) | (2 << 3)
  hands_off[:2] = hkg_can_fd_checksum(0x11A, None, hands_off).to_bytes(2, 'little')
  safety.set_timer(1_020_000)
  safety.safety_rx_hook(packet(0x11A, hands_off, 1))
  assert not safety.safety_tx_hook(packet(0x11A, prior_output, 1))
  owner.update([(BASE + 20_000_000, [(0x11A, bytes(newer), 1)])])
  _, prior_output, _ = owner.command(BASE + 20_000_000)[0]
  assert not safety.safety_tx_hook(packet(0x11A, prior_output, 1))
  owner.update([(BASE + 30_000_000, [(0x11A, bytes(hands_off), 1)])])
  _, current_output, _ = owner.command(BASE + 30_000_000)[0]
  assert safety.safety_tx_hook(packet(0x11A, current_output, 1))
