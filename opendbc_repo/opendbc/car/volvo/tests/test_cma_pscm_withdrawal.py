"""Unsupported rack status withdraws host commands without starving stock transport."""
import pytest
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.volvo.helpers import (checksum_1_pscm_related_message, checksum_2_pscm_related_message,
                                      pscm_status_validation_class)
from opendbc.car.volvo.interface import CarInterface
from opendbc.car.volvo.tests.test_cma_primary_angle import _sources
from opendbc.car.volvo.values import CAR


def status(b1, b2):
  return bytes([checksum_1_pscm_related_message(b1, b2), b1, b2, checksum_2_pscm_related_message(b2), 0, 0, 0xFF, 0xFF])


def interface():
  cp = CarInterface.get_params(CAR.POLESTAR_2, gen_empty_fingerprint(), [], False, False, False)
  ci = CarInterface(cp)
  for frame in range(40):
    now = 1_000_000_000 + frame * 10_000_000
    ci.update([(now, _sources('POLESTAR_2', frame=frame))])
    ci.apply(structs.CarControl.new_message().as_reader(), now + 1000)
  assert ci.CS.out.canValid and ci.CS.pscm_status_supported
  return ci


def step(ci, frame, active, rack_status=None):
  incoming = _sources('POLESTAR_2', frame=frame)
  if rack_status is not None:
    incoming = [(a, rack_status if a == 0x17 and b == 2 else d, b) for a, d, b in incoming]
  now = 1_000_000_000 + frame * 10_000_000
  ci.update([(now, incoming)])
  _, messages = ci.apply(structs.CarControl.new_message(latActive=active).as_reader(), now + 1000)
  return incoming, messages


def forwarded(frames):
  return [(a, d, 2 if b == 0 else 0) for a, d, b in frames
          if (b == 0 and a in (0x58, 0x57, 0x67, 0x69, 0x90, 0x97, 0x92)) or (b == 2 and a in (0x16, 0x17))]


GOOD = status(0x11, 0x81)
OFFSET = status(0x31, 0x81)


@pytest.mark.parametrize('unsupported', [
  OFFSET, status(0x18, 0x81), status(0xF0, 0x8F), status(0, 0xA0),
  bytes([GOOD[0] ^ 1]) + GOOD[1:], GOOD[:3] + bytes([GOOD[3] ^ 1]) + GOOD[4:],
])
def test_unsupported_status_uses_stock_until_request_cycles(unsupported):
  ci = interface()
  step(ci, 40, True)
  assert ci.CC.lat_active
  incoming, messages = step(ci, 41, True, unsupported)
  assert not ci.CC.lat_active and ci.CS.pscm_rearm_required
  assert messages == forwarded(incoming)
  incoming, messages = step(ci, 42, True, GOOD)
  assert ci.CS.pscm_status_supported and ci.CS.pscm_rearm_required and not ci.CC.lat_active
  assert messages == forwarded(incoming)
  step(ci, 43, False, GOOD)
  assert not ci.CS.pscm_rearm_required
  step(ci, 44, True, GOOD)
  assert ci.CC.lat_active


@pytest.mark.parametrize('recover_in_batch', [False, True])
def test_mixed_batch_cannot_hide_bad_status_or_replay_an_earlier_class(recover_in_batch):
  ci = interface()
  step(ci, 40, True)
  received = [(0x17, GOOD, 2), (0x17, OFFSET, 2)]
  if recover_in_batch:
    received.append((0x17, GOOD, 2))
  ci.update([(1_410_000_000, received)])
  assert ci.CS.pscm_rearm_required
  _, messages = ci.apply(structs.CarControl.new_message(latActive=True).as_reader(), 1_410_001_000)
  assert not ci.CC.lat_active
  expected = GOOD if recover_in_batch else OFFSET
  assert [m for m in messages if m[0] == 0x17] == [(0x17, expected, 0)]


def test_missing_status_cannot_grant_host_lateral():
  cp = CarInterface.get_params(CAR.POLESTAR_2, gen_empty_fingerprint(), [], False, False, False)
  ci = CarInterface(cp)
  ci.update([(1_000_000_000, [m for m in _sources('POLESTAR_2') if m[0] != 0x17])])
  ci.CS.out = structs.CarState.new_message(canValid=True, gearShifter=structs.CarState.GearShifter.drive).as_reader()
  ci.apply(structs.CarControl.new_message(latActive=True).as_reader(), 1_000_001_000)
  assert not ci.CC.lat_active
  assert pscm_status_validation_class(GOOD) == (8, True, True)
  assert pscm_status_validation_class(OFFSET) == (8, True, False)


@pytest.mark.parametrize('counter', range(15))
@pytest.mark.parametrize('lag', [0, 1])
def test_only_equal_counters_keep_host_active(counter, lag):
  ci = interface()
  step(ci, 40, True)
  packet = status((counter << 4) | 1, 0x80 | ((counter - lag) % 15))
  assert pscm_status_validation_class(packet) == (8, True, lag == 0)
  step(ci, 41, True, packet)
  assert ci.CC.lat_active == (lag == 0)
  assert ci.CS.pscm_rearm_required == (lag != 0)
  incoming, messages = step(ci, 42, False, packet)
  assert messages == forwarded(incoming)


@pytest.mark.parametrize('counter,replica', [(15, 14), (15, 0), (0, 1), (0, 13), (1, 14)])
def test_other_counter_relationships_still_withdraw(counter, replica):
  ci = interface()
  step(ci, 40, True)
  packet = status((counter << 4) | 1, 0x80 | replica)
  assert pscm_status_validation_class(packet) == (8, True, False)
  incoming, messages = step(ci, 41, True, packet)
  assert not ci.CC.lat_active and ci.CS.pscm_rearm_required
  assert messages == forwarded(incoming)
