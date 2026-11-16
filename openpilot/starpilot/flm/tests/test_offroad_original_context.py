"""Recorded-clock regressions; declared envelopes earn no tuning qualification."""

from types import SimpleNamespace as NS

import pytest

from openpilot.starpilot.flm.offroad_checks import needed_original, original_checkpoint


def test_carrier_clock_retains_in_range_evidence_with_later_publication():
  end = 22999949027276
  row = NS(controlMonoTime=22999962308740, appliedMonoTime=22999971296402,
           carrierObservedMonoTime=22999971296402)
  event = NS(logMonoTime=22999979412855, starpilotCarState=NS(appliedEvidence=row))
  assert event.logMonoTime > end + 30_000_000
  assert needed_original(event, ((end - 2_000_000_000, end),), 'applied')
  row.carrierObservedMonoTime = event.logMonoTime + 1
  assert not needed_original(event, ((end - 2_000_000_000, end),), 'applied')


@pytest.mark.parametrize('kind', ('control', 'applied'))
def test_context_selection_keeps_original_source_and_time_bounds(kind):
  row = NS(controlMonoTime=2_020_000_000, appliedMonoTime=2_025_000_000,
           carrierObservedMonoTime=2_025_000_000)
  event = NS(logMonoTime=2_050_000_000, starpilotLateralState=NS(learningEvidence=row),
             starpilotCarState=NS(appliedEvidence=row))
  assert needed_original(event, ((1_000_000_000, 2_000_000_000),), kind)
  row.controlMonoTime = event.logMonoTime + 1
  assert not needed_original(event, ((1_000_000_000, 2_000_000_000),), kind)
  row.controlMonoTime = 2_040_000_000
  row.appliedMonoTime = row.carrierObservedMonoTime = 2_045_000_000
  assert not needed_original(event, ((1_000_000_000, 2_000_000_000),), kind)


def test_checkpoint_keeps_real_held_command_source_in_original_preroll():
  start = 23069871371113
  old, latest = 23069334312624, 23069839623537
  controls = [NS(control_ns=n, replay_snapshot=(1,) if n < start else ()) for n in (old, latest, start)]
  outputs = [NS(control_ns=23069832746006, carrier_observed_ns=start - 1_000_000)]
  assert original_checkpoint(controls, outputs, start) == old
  # A fully emitted first scored command needs no earlier hold context.
  outputs.append(NS(control_ns=start, carrier_observed_ns=start + 1_000_000))
  assert original_checkpoint(controls, outputs, start) == latest


def test_checkpoint_never_fabricates_or_extends_preroll():
  controls = [NS(control_ns=2_000_000_001, replay_snapshot=(1,)), NS(control_ns=3_000_000_000, replay_snapshot=())]
  outputs = [NS(control_ns=1_999_999_999, carrier_observed_ns=2_990_000_000)]
  assert original_checkpoint(controls, outputs, 3_000_000_000) is None
  assert original_checkpoint([], outputs, 3_000_000_000) is None
