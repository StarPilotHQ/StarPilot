"""GEN2 CRZ_STATE decode: only the defined engaged states (2 = CRUISE_ENABLED, 4 = GAS_OVERRIDE) are engaged.

panda decodes the same way (safety/tests/test_mazda.py test_only_defined_cruise_states_engage); undefined codes
3 and 5..7 must not report cruise enabled. Main stays on for every non-zero state.
"""
import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.mazda.carstate import CarState
from opendbc.car.mazda.values import CAR, DBC, MazdaSafetyFlags


@pytest.mark.parametrize("state", range(8))
def test_cruise_enabled_only_in_defined_engaged_states(state):
  CP = structs.CarParams(carFingerprint=CAR.MAZDA_3_2019, brand="mazda", flags=int(MazdaSafetyFlags.GEN2))
  car_state = CarState(CP)
  parsers = CarState.get_can_parsers(CP)
  packer = CANPacker(DBC[CP.carFingerprint][Bus.pt])
  frames = [packer.make_can_msg("CRUZE_STATE", 0, {"CRZ_STATE": state}),
            packer.make_can_msg("TI_FEEDBACK", 1, {f"CPU_{i}_STATE": 3 for i in range(4)})]
  for t in (1_000_000_000, 1_010_000_000):  # the parser takes a message from its second frame
    for parser in parsers.values():
      parser.update([t, frames])
    ret = car_state.update_gen2(parsers)
  assert ret.cruiseState.enabled == (state in (2, 4))
  assert ret.cruiseState.available == (state != 0)
