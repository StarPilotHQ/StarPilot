import pytest

from opendbc.car import CanData, gen_empty_fingerprint, structs
from opendbc.car.hyundai.daw import DawSource
from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.tests.test_ioniq6_stock_parser import params as ioniq6_params
from opendbc.car.hyundai.values import CAR

BASE = 1_000_000_000


def params(canfd):
  return ioniq6_params(True)[0] if canfd else CarInterface.get_params(
    CAR.HYUNDAI_SONATA_HYBRID, gen_empty_fingerprint(), [], False, False, False)


def source(canfd, status):
  data = bytearray.fromhex('1234567898f8') if not canfd else bytearray(range(16))
  if canfd:
    data[7] = (data[7] & ~0x38) | (status << 3)
    data[:2] = hkg_can_fd_checksum(0x11A, None, data).to_bytes(2, 'little')
  else:
    data[5] = (data[5] & ~7) | status
  return bytes(data)


@pytest.mark.parametrize('canfd', (False, True))
@pytest.mark.parametrize('status', range(8))
def test_exact_current_advisory_only_transform(canfd, status):
  owner = DawSource(params(canfd))
  data = source(canfd, status)
  assert not owner.command(BASE)
  owner.update([(BASE, [CanData(owner.address, data, owner.source_bus)])])
  command, = owner.command(BASE)
  expected = bytearray(data)
  if canfd:
    if status == 1:
      expected[7] &= ~0x38
    expected[:2] = hkg_can_fd_checksum(owner.address, None, expected).to_bytes(2, 'little')
  elif 1 <= status <= 3:
    expected[5] &= ~7
  assert command == CanData(owner.address, bytes(expected), owner.target_bus)
  assert owner.data == data
  assert owner.command(BASE + 100_000_000)
  assert not owner.command(BASE + 100_000_001)


@pytest.mark.parametrize('canfd,fault', [(canfd, fault) for canfd in (False, True)
  for fault in ('future', 'old', 'reversed', 'length', 'returned', 'wrong_bus', 'wrong_address', 'crc')
  if canfd or fault != 'crc'])
def test_bad_or_missing_sources_cannot_create_replacements(canfd, fault):
  owner = DawSource(params(canfd))
  data = source(canfd, 1)
  stamp, bus, addr = BASE, owner.source_bus, owner.address
  if fault == 'length':
    data = data[:-1]
  elif fault == 'returned':
    bus += 128
  elif fault == 'wrong_bus':
    bus = 0 if canfd else 1
  elif fault == 'wrong_address':
    addr += 1
  elif fault == 'crc':
    data = bytes([data[0] ^ 1]) + data[1:]
  elif fault == 'reversed':
    owner.update([(BASE + 1, [CanData(addr, data, bus)])])
  owner.update([(stamp, [CanData(addr, data, bus)])])
  now = BASE - 1 if fault == 'future' else BASE + 100_000_001 if fault == 'old' else BASE
  assert not owner.command(now)


def test_actual_parser_and_controller_keep_optional_message_out_of_driving_health():
  cp = params(False)
  before = cp.to_dict()
  first, second = CarInterface(cp.as_reader().as_builder()), CarInterface(cp.as_reader().as_builder())
  data = bytes.fromhex('000000000883')
  plain = first.update([(BASE, [])])
  observed = second.update([(BASE, [CanData(0x53E, data, 2)])])
  assert observed.to_dict() == plain.to_dict()
  control = structs.CarControl()
  baseline = first.apply(control.as_reader(), BASE)[1]
  actual = second.apply(control.as_reader(), BASE)[1]
  assert [frame for frame in actual if frame[0] != 0x53E] == baseline
  assert [frame for frame in actual if frame[0] == 0x53E] == [CanData(0x53E, bytes.fromhex('000000000880'), 0)]
  assert cp.to_dict() == before


def test_unrelated_canfd_profiles_do_not_emit_new_frames():
  cp = CarInterface.get_params(CAR.KIA_EV6, gen_empty_fingerprint(), [], False, False, False)
  owner = DawSource(cp)
  assert not owner.enabled
  owner.update([(BASE, [CanData(0x11A, source(True, 1), 1)])])
  assert not owner.command(BASE)


@pytest.mark.parametrize('word', (0, 1, 9, 0x15, 0x5491, 0x5495))
def test_unsupported_ioniq6_native_profiles_cannot_emit_advisories(word):
  cp = params(True)
  cp.safetyConfigs[-1].safetyParam = word
  owner = DawSource(cp)
  assert not owner.enabled
  owner.update([(BASE, [CanData(0x11A, source(True, 1), owner.source_bus)])])
  assert not owner.command(BASE)
