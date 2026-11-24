import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.toyota.carstate import CarState
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR, DBC


@pytest.mark.parametrize('stock_aol', (True, False))
def test_prius_gap_button_requires_the_source_only_during_a_press(stock_aol):
  fp = gen_empty_fingerprint()
  if not stock_aol:
    fp[2][0x343] = 8
  cp = CarInterface.get_params(CAR.TOYOTA_PRIUS, fp, [], False, False, False)
  assert not cp.openpilotLongitudinalControl
  assert cp.safetyConfigs[0].safetyParam == (0x6242 if stock_aol else 0x242)
  state = CarState(cp)
  parsers = state.get_can_parsers(cp)
  packer = CANPacker(DBC[CAR.TOYOTA_PRIUS][Bus.pt])
  gap = structs.CarState.ButtonEvent.Type.gapAdjustCruise

  def edges(stamp, value=None):
    frames = [] if value is None else [packer.make_can_msg('ACC_CONTROL', 0, {'DISTANCE': value})]
    for parser in parsers.values():
      parser.update([(stamp, frames)])
    return [event.pressed for event in state.update(parsers).buttonEvents if event.type == gap]

  assert edges(1_000_000_000) == []
  assert parsers[Bus.pt].message_states[0x343].ignore_alive
  assert not parsers[Bus.pt].message_states[0x1D3].ignore_alive
  assert not parsers[Bus.pt].message_states[0x262].ignore_alive
  assert edges(1_030_000_000, 1) == [True]
  gap_message = parsers[Bus.pt].message_states[0x343]
  assert not gap_message.ignore_alive
  assert edges(12_000_000_000) == []
  assert not gap_message.valid(12_000_000_000, False)
  assert edges(12_030_000_000, 0) == [False]
  assert gap_message.ignore_alive
  assert gap_message.valid(23_000_000_000, False)
