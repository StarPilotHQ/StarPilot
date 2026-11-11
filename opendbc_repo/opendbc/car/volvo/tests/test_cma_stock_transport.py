"""Inactive Polestar transport preserves received stock packets exactly once."""
import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.volvo.interface import CarInterface
from opendbc.car.volvo.values import CAR, DBC


def interface():
  cp = CarInterface.get_params(CAR.POLESTAR_2, gen_empty_fingerprint(), [], False, False, False)
  return CarInterface(cp)


def packets():
  packer = CANPacker(DBC[CAR.POLESTAR_2][Bus.main])
  return [packer.make_can_msg('LCA_5', 0, {'COUNTER': counter, 'WHEEL_SPEED_1': 200 + counter}) for counter in (1, 5, 9)]


def forwarded(messages):
  return [(address, data, 2 if bus == 0 else 0) for address, data, bus in messages]


def test_batch_and_multiple_updates_preserve_every_payload_once_in_order():
  for separate in (False, True):
    ci = interface()
    received = packets()
    if separate:
      for index, message in enumerate(received):
        ci.update([(1_000_000_000 + index * 1_000_000, [message])])
    else:
      ci.update([(1_000_000_000, received)])
    cc = structs.CarControl.new_message()
    _, output = ci.apply(cc.as_reader(), 1_010_000_000)
    assert output == forwarded(received)
    _, output = ci.apply(cc.as_reader(), 1_020_000_000)
    assert output == []


def test_unprotected_wrong_bus_and_wrong_length_are_not_relayed():
  ci = interface()
  data = packets()[0][1]
  ci.update([(1_000_000_000, [(0x67, data, 1), (0x17, data, 0), (0x123, data, 0), (0x67, data[:7], 0)])])
  _, output = ci.apply(structs.CarControl.new_message().as_reader(), 1_010_000_000)
  assert output == []


def test_pending_queue_is_bounded():
  ci = interface()
  received = packets() * 70
  ci.update([(1_000_000_000, received)])
  assert len(ci.stock_relay) == 128
  _, output = ci.apply(structs.CarControl.new_message().as_reader(), 1_010_000_000)
  assert output == forwarded(received[-128:])


@pytest.mark.parametrize('changes', [
  {'gearShifter': structs.CarState.GearShifter.park}, {'canValid': False}, {'canTimeout': True},
  {'steerFaultTemporary': True}, {'steerFaultPermanent': True},
])
def test_effective_withdrawal_preserves_only_received_stock(changes):
  ci = interface()
  received = packets()[:1]
  ci.update([(1_000_000_000, received)])
  state = structs.CarState.new_message(canValid=True, gearShifter=structs.CarState.GearShifter.drive)
  for key, value in changes.items():
    setattr(state, key, value)
  ci.CS.out = state.as_reader()
  _, output = ci.apply(structs.CarControl.new_message(latActive=True).as_reader(), 1_010_000_000)
  assert not ci.CC.lat_active
  assert output == forwarded(received)


def test_stock_received_while_active_cannot_leak_into_later_withdrawal():
  ci = interface()
  ci.update([(1_000_000_000, packets())])
  ci.CS.out = structs.CarState.new_message(canValid=True, gearShifter=structs.CarState.GearShifter.drive).as_reader()
  ci.apply(structs.CarControl.new_message(latActive=True).as_reader(), 1_010_000_000)
  assert ci.CC.lat_active
  assert not ci.stock_relay
  _, output = ci.apply(structs.CarControl.new_message().as_reader(), 1_020_000_000)
  assert output == []
