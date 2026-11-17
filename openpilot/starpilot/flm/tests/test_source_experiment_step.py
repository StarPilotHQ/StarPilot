"""A measured experiment must fit its ramp; source/native guards stay binding."""
from dataclasses import replace

from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
from openpilot.starpilot.flm.effect_trace import step_full
from openpilot.starpilot.flm.live_trial_contract import MAX_STEP, SOURCE_EXPERIMENT_STEP, SLEW_PER_SECOND
from openpilot.starpilot.flm.tests.test_live_tracking import episode
from openpilot.starpilot.flm.tests.test_tracking_source_context import owner, refresh


def test_source_experiment_reaches_native_visible_target_at_the_existing_slew():
  o = owner()
  for index in range(6):
    row = episode(o, index, 'ff_gain_left', error=.2)
    row = replace(row, normalization_accel=max(.1, row.goal_rms))
    refresh(o, row.end_ns)
    o.observe_episode(row, now_ns=row.end_ns)
  offer = o.offer(row.end_ns)
  assert offer is not None
  change = abs(dict(offer.target)['ff_gain_left'] - dict(offer.baseline)['ff_gain_left'])
  assert change == SOURCE_EXPERIMENT_STEP == 2 * SLEW_PER_SECOND < MAX_STEP
  current = dict(offer.baseline)
  previous = row.end_ns
  for tick in range(1, 202):
    now = row.end_ns + tick * 10_000_000
    before = current
    current = step_full(current, dict(offer.target), now_ns=now, previous_ns=previous, qualified=True)
    assert all(abs(current[n] - before[n]) <= SLEW_PER_SECOND * .01 + 1e-15 for n in NAMES)
    previous = now
    if tick == 100:
      assert current != dict(offer.target)
  assert current == dict(offer.target)
  assert o.settled_ns is None and not any(o.accepted.values())


def test_no_native_visibility_still_prevents_smaller_trial():
  o = owner()
  for index in range(10):
    row = episode(o, index, 'ff_gain_left', error=.2)
    f = replace(row.sensitivity[0], native_separated=0, max_step_physical_rms=0.)
    row = replace(row, sensitivity=(f,), normalization_accel=max(.1, row.goal_rms))
    refresh(o, row.end_ns)
    o.observe_episode(row, now_ns=row.end_ns)
  assert o.offer_state is None and o.discovery is None and not any(o.accepted.values())


def test_old_witness_does_not_grant_a_smaller_trial():
  o = owner()
  for index in range(6):
    row = episode(o, index, 'ff_gain_left', error=.2)
    row = replace(row, normalization_accel=max(.1, row.goal_rms))
    refresh(o, row.end_ns)
    o.observe_episode(row, now_ns=row.end_ns)
  assert o.offer(row.end_ns + 100_000_001) is None
  assert not any(o.accepted.values())
