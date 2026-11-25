"""Exit-transition unknown-gear flicker must not revoke Subaru AOL intent.

At the manumatic->drive handoff the Subaru gear byte passes through an
unmapped value for ~50-80 ms, so carState reports GearShifter.unknown just
after a paddle episode exits. The generic classifier treats soft wrongGear
with gear unknown as disarming, which revoked the latch 10-19 ms into the
flicker on real drives (3 of 3 flicker episodes died; 10 of 10 clean exits
survived). The Subaru port tolerates a brief unknown run out of a drivable
gear; anything longer, or a run from a non-drivable gear, still disarms.
"""
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.stock_aol import qualified
from opendbc.car.subaru.values import CAR
from openpilot.cereal import log
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.aol.vehicle import intent_disarming_fault as vehicle_disarming_fault
from openpilot.starpilot.car.subaru.aol import SubaruStockCardIntent
from openpilot.starpilot.car.subaru.aol import intent_disarming_fault as subaru_disarming_fault

Gear = structs.CarState.GearShifter
Name = log.OnroadEvent.EventName
MS = 1_000_000


def make_cp():
  cp = CarInterface.get_params(CAR.SUBARU_IMPREZA_2020, gen_empty_fingerprint(), [], False, False, False)
  cp.alternativeExperience = 32
  assert qualified(cp, marked_only=True)
  return cp


def make_state(gear, v=3.0):
  cs = structs.CarState(canValid=True, vEgo=v, gearShifter=gear)
  cs.cruiseState.available = True
  return cs


def wrong_gear_event():
  return SimpleNamespace(name=Name.wrongGear, softDisable=True, immediateDisable=False, noEntry=True)


def classify(cp, feedback, state, now_ns, events=()):
  return subaru_disarming_fault(cp, list(events), state.as_reader(), now_ns=now_ns, feedback=feedback)


def test_brief_unknown_flicker_from_drive_is_tolerated():
  cp = make_cp()
  feedback = TransportPauseFeedback(cp)
  t = 1_000 * MS
  assert not classify(cp, feedback, make_state(Gear.drive), t)
  assert not classify(cp, feedback, make_state(Gear.manumatic), t + 10 * MS)
  assert not classify(cp, feedback, make_state(Gear.drive), t + 20 * MS)
  # The flicker: unknown for ~70 ms with wrongGear raised throughout.
  for step in range(8):
    assert not classify(cp, feedback, make_state(Gear.unknown), t + (30 + 10 * step) * MS, [wrong_gear_event()])
  assert not classify(cp, feedback, make_state(Gear.drive), t + 110 * MS)


def test_unknown_flicker_may_begin_from_manumatic():
  cp = make_cp()
  feedback = TransportPauseFeedback(cp)
  t = 1_500 * MS
  assert not classify(cp, feedback, make_state(Gear.manumatic), t)
  for step in range(6):
    assert not classify(cp, feedback, make_state(Gear.unknown), t + (10 + 10 * step) * MS, [wrong_gear_event()])
  assert not classify(cp, feedback, make_state(Gear.drive), t + 70 * MS)


def test_unknown_persisting_past_tolerance_disarms():
  cp = make_cp()
  feedback = TransportPauseFeedback(cp)
  t = 2_000 * MS
  assert not classify(cp, feedback, make_state(Gear.drive), t)
  start = t + 10 * MS
  for step in range(16):  # 0..150 ms into the run: still a transient
    assert not classify(cp, feedback, make_state(Gear.unknown), start + 10 * step * MS, [wrong_gear_event()])
  assert classify(cp, feedback, make_state(Gear.unknown), start + 160 * MS, [wrong_gear_event()])
  assert classify(cp, feedback, make_state(Gear.unknown), start + 500 * MS, [wrong_gear_event()])


@pytest.mark.parametrize('gear', (Gear.park, Gear.neutral, Gear.reverse))
def test_unknown_from_non_drivable_gear_disarms_immediately(gear):
  cp = make_cp()
  feedback = TransportPauseFeedback(cp)
  t = 3_000 * MS
  assert not classify(cp, feedback, make_state(gear), t)
  assert classify(cp, feedback, make_state(Gear.unknown), t + 10 * MS, [wrong_gear_event()])


def test_unknown_without_any_prior_gear_disarms_immediately():
  cp = make_cp()
  feedback = TransportPauseFeedback(cp)
  assert classify(cp, feedback, make_state(Gear.unknown), 10 * MS, [wrong_gear_event()])


def test_without_feedback_or_time_the_generic_rule_applies():
  cp = make_cp()
  state = make_state(Gear.unknown)
  assert subaru_disarming_fault(cp, [wrong_gear_event()], state.as_reader())
  feedback = TransportPauseFeedback(cp)
  assert subaru_disarming_fault(cp, [wrong_gear_event()], state.as_reader(), now_ns=0, feedback=feedback)


def test_hard_faults_still_disarm_during_a_tolerated_flicker():
  cp = make_cp()
  feedback = TransportPauseFeedback(cp)
  t = 3_500 * MS
  assert not classify(cp, feedback, make_state(Gear.drive), t)
  hard_event = SimpleNamespace(name=Name.controlsMismatch, softDisable=False, immediateDisable=True, noEntry=False)
  assert classify(cp, feedback, make_state(Gear.unknown), t + 10 * MS, [wrong_gear_event(), hard_event])
  state = make_state(Gear.unknown)
  state.steerFaultPermanent = True
  assert classify(cp, feedback, state, t + 20 * MS, [wrong_gear_event()])


def test_vehicle_dispatch_reaches_the_subaru_tolerance():
  cp = make_cp()
  feedback = TransportPauseFeedback(cp)
  t = 4_000 * MS
  assert not vehicle_disarming_fault(cp, [], make_state(Gear.drive).as_reader(), now_ns=t, feedback=feedback)
  assert not vehicle_disarming_fault(cp, [wrong_gear_event()], make_state(Gear.unknown).as_reader(),
                                     now_ns=t + 10 * MS, feedback=feedback)
  assert vehicle_disarming_fault(cp, [wrong_gear_event()], make_state(Gear.unknown).as_reader(),
                                 now_ns=t + 200 * MS, feedback=feedback)


def armed_intent(cp, t):
  settings = AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0))
  intent = SubaruStockCardIntent(settings)
  cs = make_state(Gear.drive, v=15.0)
  cs.cruiseState.enabled = True
  intent.update(cs.as_reader(), now_ns=t)
  for i, (enabled, active) in enumerate(((False, False), (True, False), (True, True))):
    intent.observe_full_engagement(cs.as_reader(), enabled=enabled, active=active, source_ns=t + 1 + i,
                                   observation_valid=True, now_ns=t + 1 + i, fault_active=False)
  assert intent.allowed_latch
  return intent, cs


def test_latch_survives_exit_flicker_and_resumes():
  cp = make_cp()
  feedback = TransportPauseFeedback(cp)
  t = 10_000 * MS
  intent, cs = armed_intent(cp, t)

  def step(now_ns, events=()):
    fault = subaru_disarming_fault(cp, list(events), cs.as_reader(), now_ns=now_ns, feedback=feedback)
    intent.update(cs.as_reader(), fault_active=fault, now_ns=now_ns)

  # Paddle episode: manumatic pauses output, intent stays latched.
  cs.gearShifter = Gear.manumatic
  cs.cruiseState.enabled = False
  step(t + 10 * MS)
  assert intent.allowed_latch
  assert not intent.output(cs.as_reader())[0]

  # Exit to Drive: no re-arm gesture, the latch resumes output.
  cs.gearShifter = Gear.drive
  step(t + 5_000 * MS)
  assert intent.allowed_latch
  assert intent.output(cs.as_reader())[0]

  # Mark 1's shape: ~70 ms of unknown with wrongGear just after the resume.
  cs.gearShifter = Gear.unknown
  for i in range(8):
    step(t + (5_300 + 10 * i) * MS, [wrong_gear_event()])
    assert intent.allowed_latch, "exit flicker revoked AOL intent"
  cs.gearShifter = Gear.drive
  step(t + 5_400 * MS)
  assert intent.allowed_latch
  assert intent.output(cs.as_reader())[0]


def test_latch_dies_when_unknown_persists_after_exit():
  cp = make_cp()
  feedback = TransportPauseFeedback(cp)
  t = 20_000 * MS
  intent, cs = armed_intent(cp, t)

  def step(now_ns, events=()):
    fault = subaru_disarming_fault(cp, list(events), cs.as_reader(), now_ns=now_ns, feedback=feedback)
    intent.update(cs.as_reader(), fault_active=fault, now_ns=now_ns)

  cs.gearShifter = Gear.drive
  step(t + 1_000 * MS)
  assert intent.allowed_latch
  cs.gearShifter = Gear.unknown
  for i in range(30):  # 300 ms of continuous unknown: a real gear loss
    step(t + (1_300 + 10 * i) * MS, [wrong_gear_event()])
  assert not intent.allowed_latch


if __name__ == "__main__":
  pytest.main([__file__, "-v"])
