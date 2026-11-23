"""Regression tests: a Subaru paddle/manual-mode episode must pause AOL
output without revoking the driver's latched AOL intent.

In manual mode the transmission Gear signal reports the simulated ratio
("1"-"6") instead of "D". GEAR_SHIFTER_MAP has no entry for the ratios, so
CarState.gearShifter is GearShifter.unknown for the whole episode. Before
this fix, the generic disarming_fault() treated the resulting soft
wrongGear event at unknown gear as a disarming fault, clearing the AOL
latch; on the return to Drive no fresh MAIN edge occurs, so AOL stayed off
until the driver re-armed it manually (the pre-7.0 behavior was an
automatic resume).
"""
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.stock_aol import qualified
from opendbc.car.subaru.values import CAR
from openpilot.cereal import log
from openpilot.starpilot.aol.intent import AolSettings, disarming_fault
from openpilot.starpilot.aol.vehicle import intent_disarming_fault as vehicle_disarming_fault
from openpilot.starpilot.car.subaru.aol import SubaruStockCardIntent, intent_disarming_fault

Gear = structs.CarState.GearShifter
Name = log.OnroadEvent.EventName


def make_cp():
  cp = CarInterface.get_params(CAR.SUBARU_IMPREZA_2020, gen_empty_fingerprint(), [], False, False, False)
  assert qualified(cp)
  return cp


def make_state(gear):
  cs = structs.CarState(canValid=True, vEgo=15.0, gearShifter=gear)
  cs.cruiseState.available = True
  return cs


def wrong_gear_event():
  return SimpleNamespace(name=Name.wrongGear, softDisable=True, immediateDisable=False, noEntry=True)


def test_generic_classifier_still_disarms_on_unknown_gear():
  # The generic rule is unchanged: other brands (e.g. GM) rely on unknown
  # gear being disarming. The Subaru fix is scoped to the Subaru port.
  assert disarming_fault([wrong_gear_event()], make_state(Gear.unknown))


def test_subaru_manual_episode_is_not_a_disarming_fault():
  cp = make_cp()
  state = make_state(Gear.unknown)
  assert not intent_disarming_fault(cp, [wrong_gear_event()], state)
  assert not vehicle_disarming_fault(cp, [wrong_gear_event()], state)


def test_subaru_known_gears_were_never_disarming():
  cp = make_cp()
  for gear in (Gear.drive, Gear.park, Gear.neutral, Gear.reverse):
    assert not intent_disarming_fault(cp, [wrong_gear_event()], make_state(gear))


def test_subaru_real_faults_still_disarm_in_manual_mode():
  cp = make_cp()
  state = make_state(Gear.unknown)
  hard_event = SimpleNamespace(name=Name.controlsMismatch, softDisable=False, immediateDisable=True, noEntry=False)
  assert intent_disarming_fault(cp, [hard_event], state)
  state.steerFaultPermanent = True
  assert intent_disarming_fault(cp, [wrong_gear_event()], state)


def test_latch_survives_manual_episode_and_resumes_in_drive():
  cp = make_cp()
  settings = AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0))
  intent = SubaruStockCardIntent(settings)

  cs = make_state(Gear.drive)
  cs.cruiseState.enabled = True

  def observe(enabled, active, stamp):
    intent.observe_full_engagement(cs.as_reader(), enabled=enabled, active=active, source_ns=stamp,
                                   observation_valid=True, now_ns=stamp, fault_active=False)

  intent.update(cs.as_reader(), now_ns=1)
  observe(False, False, 10)
  observe(True, False, 20)
  observe(True, True, 30)
  assert intent.allowed_latch

  # Paddle downshift: manual mode, gear reads unknown, wrongGear soft event.
  cs.gearShifter = Gear.unknown
  cs.cruiseState.enabled = False
  fault = vehicle_disarming_fault(cp, [wrong_gear_event()], cs.as_reader())
  intent.update(cs.as_reader(), fault_active=fault, now_ns=40)
  assert intent.allowed_latch, "manual-mode episode revoked AOL intent"

  # Back to Drive with MAIN never cycled: no re-arm gesture, latch is enough.
  cs.gearShifter = Gear.drive
  intent.update(cs.as_reader(), fault_active=False, now_ns=50)
  assert intent.allowed_latch


if __name__ == "__main__":
  pytest.main([__file__, "-v"])
