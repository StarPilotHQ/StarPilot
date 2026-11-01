"""Regression tests for Gen2 (TI2) steering-fault propagation in CarState.

Mazda TI2 safety remediation: update_gen2() maps the interceptor's CPU
violation bytes onto the steering-fault fields. These tests pin the required
mappings (they fail against a CarState that hardcodes both fields to False):

  - any CPU_x_VIOL != 0            -> steerFaultPermanent
  - no violation                   -> no steerFaultPermanent

TI2 operating-state (CPU_x_STATE) semantics are established by the Phase 2
capture contract; not-ready/override mappings are covered by Phase 4 tests.
Adapted to Dom's CarState conventions (CP-only constructor, no starpilot params).
"""
import pytest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, structs
from opendbc.car.mazda.values import CAR, DBC, MazdaSafetyFlags
from opendbc.car.mazda.carstate import CarState

PLATFORM = CAR.MAZDA_3_2019


def make_car_state():
  CP = structs.CarParams(carFingerprint=PLATFORM, brand="mazda", flags=int(MazdaSafetyFlags.GEN2))
  return CarState(CP), CP


def update_with_ti_feedback(CP, car_state, viol=(0, 0, 0, 0), cpu_state=(0, 0, 0, 0)):
  dbc = DBC[CP.carFingerprint][Bus.pt]
  parsers = {
    Bus.pt: CANParser(dbc, [], 0),
    Bus.body: CANParser(dbc, [("TI_FEEDBACK", 50)], 1),
    Bus.cam: CANParser(dbc, [], 2),
  }
  values = {f"CPU_{i}_VIOL": viol[i] for i in range(4)}
  values.update({f"CPU_{i}_STATE": cpu_state[i] for i in range(4)})
  msg = CANPacker(dbc).make_can_msg("TI_FEEDBACK", 1, values)
  parsers[Bus.body].update([0, [msg]])
  return car_state.update_gen2(parsers)


class TestGen2TI2FaultPropagation:
  @pytest.mark.parametrize("cpu", range(4))
  def test_ti_cpu_violation_sets_permanent_fault(self, cpu):
    car_state, CP = make_car_state()
    viol = [0, 0, 0, 0]
    viol[cpu] = 1
    ret = update_with_ti_feedback(CP, car_state, viol=viol)
    assert ret.steerFaultPermanent, f"CPU_{cpu}_VIOL must map to steerFaultPermanent"

  def test_ti_clean_feedback_has_no_steering_fault(self):
    # passes on unfixed code too; guards the fix against over-reporting faults.
    # steerFaultTemporary is intentionally not asserted: its mapping depends on
    # CPU_x_STATE semantics from the Phase 2 contract and is covered by Phase 4.
    car_state, CP = make_car_state()
    ret = update_with_ti_feedback(CP, car_state)
    assert not ret.steerFaultPermanent
