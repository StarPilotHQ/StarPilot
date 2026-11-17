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


def advisory_candidate(owner, now):
  if not owner.canfd:
    return owner.command(now)[0]
  assert owner.command(now) == []
  data = bytearray(owner.data)
  if (data[7] >> 3) & 7 == 1:
    data[7] &= ~0x38
  data[:2] = hkg_can_fd_checksum(owner.address, None, data).to_bytes(2, 'little')
  return owner.address, bytes(data), owner.target_bus


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
  addr, output, bus = advisory_candidate(owner, BASE)
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
  addr, output, bus = advisory_candidate(owner, BASE)
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
  _, output, _ = advisory_candidate(owner, BASE)
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
  _, prior_output, _ = advisory_candidate(owner, BASE + 10_000_000)
  assert safety.safety_tx_hook(packet(0x11A, prior_output, 1))
  hands_off = bytearray(newer)
  hands_off[2] += 1
  hands_off[7] = (hands_off[7] & ~0x38) | (2 << 3)
  hands_off[:2] = hkg_can_fd_checksum(0x11A, None, hands_off).to_bytes(2, 'little')
  safety.set_timer(1_020_000)
  safety.safety_rx_hook(packet(0x11A, hands_off, 1))
  assert not safety.safety_tx_hook(packet(0x11A, prior_output, 1))
  owner.update([(BASE + 20_000_000, [(0x11A, bytes(newer), 1)])])
  _, prior_output, _ = advisory_candidate(owner, BASE + 20_000_000)
  assert not safety.safety_tx_hook(packet(0x11A, prior_output, 1))
  owner.update([(BASE + 30_000_000, [(0x11A, bytes(hands_off), 1)])])
  _, current_output, _ = advisory_candidate(owner, BASE + 30_000_000)
  assert safety.safety_tx_hook(packet(0x11A, current_output, 1))


@pytest.mark.parametrize('mode,word', [(structs.CarParams.SafetyModel.hyundai, 0),
  (structs.CarParams.SafetyModel.hyundai, 0xC02), (structs.CarParams.SafetyModel.hyundaiLegacy, 0)])
@pytest.mark.parametrize('status', range(8))
def test_classic_advisory_forwarding_uses_only_current_accepted_replacement(mode, word, status):
  safety, _ = setup(False, word)
  assert safety.set_safety_hooks(mode, word) == 0
  safety.init_tests()
  safety.set_timer(1_000_000)
  owner = DawSource(params(False))
  original = source(False, 0)
  incoming = packet(0x53E, original, 2)
  assert safety.safety_fwd_packet_hook(incoming) == 0
  safety.safety_rx_hook(incoming)
  owner.update([(BASE, [(0x53E, original, 2)])])
  addr, output, bus = advisory_candidate(owner, BASE)
  assert safety.safety_tx_hook(packet(addr, output, bus))
  safety.set_timer(1_010_000)
  incoming = packet(0x53E, source(False, status), 2)
  expected = -1 if 1 <= status <= 3 else 0
  # Both Panda drivers forward before their RX hook sees the current packet.
  assert safety.safety_fwd_packet_hook(incoming) == expected
  safety.safety_rx_hook(incoming)
  assert safety.safety_fwd_packet_hook(incoming) == expected
  assert safety.safety_fwd_packet_hook(packet(0x53E, source(False, status), 0)) == 2
  safety.set_timer(1_100_001)
  assert safety.safety_fwd_packet_hook(incoming) == 0


@pytest.mark.parametrize('change', ['opaque', 'length', 'reset', 'no_replacement', 'expired_source', 'relay', 'hands_off', 'unknown'])
def test_classic_advisory_forwarding_fails_open_without_exact_live_replacement(change):
  safety, _ = setup(False, 0xC02)
  owner = DawSource(params(False))
  original = source(False, 1)
  safety.safety_rx_hook(packet(0x53E, original, 2))
  owner.update([(BASE, [(0x53E, original, 2)])])
  addr, output, bus = advisory_candidate(owner, BASE)
  if change != 'no_replacement':
    assert safety.safety_tx_hook(packet(addr, output, bus))
  safety.set_timer(1_010_000)
  incoming = bytearray(source(False, 1))
  if change == 'opaque':
    incoming[0] ^= 1
  elif change == 'length':
    incoming.pop()
  elif change == 'reset':
    setup(False, 0xC02)
  elif change == 'expired_source':
    safety.set_timer(1_100_001)
  elif change == 'relay':
    safety.set_relay_malfunction(True)
  elif change in ('hands_off', 'unknown'):
    incoming = source(False, 4 if change == 'hands_off' else 7)
  message = packet(0x53E, bytes(incoming), 2)
  expected = -1 if change == 'relay' else 0
  assert safety.safety_fwd_packet_hook(message) == expected
  safety.safety_rx_hook(message)
  assert safety.safety_fwd_packet_hook(message) == expected


def test_packet_forwarding_preserves_existing_other_profile_rules():
  safety, _ = setup(False, 0)
  data = source(False, 1)
  for mode, word in ((structs.CarParams.SafetyModel.hyundaiCanfd, 0),
                     (structs.CarParams.SafetyModel.toyota, 0),
                     (structs.CarParams.SafetyModel.hyundai, 0x8800)):
    assert safety.set_safety_hooks(mode, word) == 0
    safety.init_tests()
    for bus in range(3):
      for addr in (0x53E, 0x340, 0x4F1, 0x421, 0x110, 0x11A):
        assert safety.safety_fwd_packet_hook(packet(addr, data, bus)) == safety.safety_fwd_hook(bus, addr)
