"""Paddle ratios pause output without revoking Subaru AOL intent."""
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.stock_aol import qualified
from opendbc.car.subaru.values import CAR
from openpilot.cereal import log
from openpilot.starpilot.aol.intent import AolSettings, disarming_fault
from openpilot.starpilot.car.subaru.aol import SubaruStockCardIntent, known_paddle_pause

Gear = structs.CarState.GearShifter
Name = log.OnroadEvent.EventName


def make_cp():
  cp = CarInterface.get_params(CAR.SUBARU_IMPREZA_2020, gen_empty_fingerprint(), [], False, False, False)
  cp.alternativeExperience = 32
  assert qualified(cp, marked_only=True)
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


def test_subaru_manual_episode_is_a_known_pause():
  cp = make_cp()
  state = make_state(Gear.manumatic)
  assert known_paddle_pause(cp, state)
  assert not known_paddle_pause(cp, state, ((state, False),))
  assert not known_paddle_pause(cp, make_state(Gear.unknown))
  assert not disarming_fault([wrong_gear_event()], state)


def test_subaru_known_gears_were_never_disarming():
  for gear in (Gear.drive, Gear.park, Gear.neutral, Gear.reverse):
    assert not disarming_fault([wrong_gear_event()], make_state(gear))


def test_subaru_real_faults_still_disarm_in_manual_mode():
  state = make_state(Gear.manumatic)
  hard_event = SimpleNamespace(name=Name.controlsMismatch, softDisable=False, immediateDisable=True, noEntry=False)
  assert disarming_fault([hard_event], state)
  state.steerFaultPermanent = True
  assert disarming_fault([wrong_gear_event()], state)


def test_latch_survives_manual_episode_and_resumes_in_drive():
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

  # Paddle downshift: manual mode, gear reads manumatic, wrongGear soft event.
  cs.gearShifter = Gear.manumatic
  cs.cruiseState.enabled = False
  fault = disarming_fault([wrong_gear_event()], cs.as_reader())
  intent.update(cs.as_reader(), fault_active=fault, now_ns=40)
  assert intent.allowed_latch, "manual-mode episode revoked AOL intent"

  assert not intent.output(cs.as_reader())[0]

  # Back to Drive with MAIN never cycled: no re-arm gesture, latch is enough.
  cs.gearShifter = Gear.drive
  intent.update(cs.as_reader(), fault_active=False, now_ns=50)
  assert intent.allowed_latch


if __name__ == "__main__":
  pytest.main([__file__, "-v"])
