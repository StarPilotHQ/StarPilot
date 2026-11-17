"""Numerical selection regressions; no fabricated hardware/plant admission."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from openpilot.starpilot.flm.live_tracking import HookSensitivity
from openpilot.starpilot.flm.live_tracking_episodes import TrackingEpisodeBuilder
from openpilot.starpilot.flm.live_tracking_probe import _physical_row
from openpilot.starpilot.flm.live_trial_contract import MAX_STEP, SOURCE_EXPERIMENT_STEP
from openpilot.starpilot.flm.tests.test_tracking_source_context import owner, refresh
from openpilot.starpilot.flm.tests.test_live_tracking import episode

@pytest.mark.parametrize('minus,plus,low,high,negative,positive', [
  (-MAX_STEP, MAX_STEP, 0.1, 0.102, 0.0, 0.002),
  (-MAX_STEP, MAX_STEP, 0.098, 0.1, 0.002, 0.0),
  (-MAX_STEP, MAX_STEP, 0.098, 0.102, 0.002, 0.002),
  (0.0, MAX_STEP, 0.1, 0.102, 0.0, 0.002),
])
def test_directional_visibility_uses_actual_encoded_baseline(minus, plus, low, high, negative, positive):
  probe = _physical_row('ff_gain_right', 0.0, minus, plus, 0.1, low, high, 1.0, 'separated')
  points = [SimpleNamespace(control_ns=100 + i * 100, apply_ns=110 + i * 100, error=0.1) for i in range(4)]
  probes = {point.control_ns: SimpleNamespace(apply_ns=point.apply_ns, rows=(probe,)) for point in points}
  builder = object.__new__(TrackingEpisodeBuilder)
  feature = builder._sensitivities(points, probes)[0].validate()
  assert feature.native_gradient_complete
  count, effect = feature.native_effect(-MAX_STEP)
  assert count == (4 if negative else 0) and effect == pytest.approx(negative)
  count, effect = feature.native_effect(MAX_STEP)
  assert count == (4 if positive else 0) and effect == pytest.approx(positive)

def feature(name='ff_gain_right', error=-0.05):
  return HookSensitivity(name, error, 1.0, 20, 20, 0.01, True, 0, 20, 0.0, 0.02).validate()

def test_unfitted_preference_selects_the_actually_visible_opposite_experiment():
  o = owner()
  for i in range(9):
    e = episode(o, i, 'ff_gain_right', kind='large')
    e = replace(e, sensitivity=(feature(),), normalization_accel=max(0.1, e.goal_rms))
    refresh(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
  assert o.offer_state is not None
  assert o.offer_state['target']['ff_gain_right'] == SOURCE_EXPERIMENT_STEP
  assert not o.offer_state.get('accepted', False) and not any(o.accepted.values())

def test_visible_direction_still_gets_a_bounded_trial():
  o = owner()
  for i in range(20):
    e = episode(o, i, 'ff_gain_right', kind='large')
    e = replace(e, sensitivity=(feature(error=0.05),), normalization_accel=max(0.1, e.goal_rms))
    refresh(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
    if o.offer_state is not None:
      break
  assert o.offer_state is not None
  assert o.offer_state['target']['ff_gain_right'] == SOURCE_EXPERIMENT_STEP
  assert not o.offer_state.get('accepted', False) and (not any(o.accepted.values()))

def test_recovery_requires_visibility_in_the_already_selected_direction():
  o = owner()
  e = episode(o, 0, 'ff_gain_right', kind='large')
  e = replace(e, sensitivity=(feature(),))
  target = dict(o.accepted)
  target['ff_gain_right'] = -MAX_STEP
  assert o._fresh_selected_effect((e,), 'ff_gain_right', target) is None
  target['ff_gain_right'] = MAX_STEP
  assert o._fresh_selected_effect((e,), 'ff_gain_right', target) > 0

@pytest.mark.parametrize('changes', [
  {'minus_native_separated': None},
  {'plus_max_step_physical_rms': float('nan')},
  {'minus_max_step_physical_rms': 0.01},
  {'plus_native_separated': 21},
])
def test_partial_or_impossible_directional_support_is_rejected(changes):
  with pytest.raises(ValueError):
    replace(feature(), **changes).validate()


@pytest.mark.parametrize('outcome', ['worse', 'inconclusive'])
def test_visible_opposite_source_experiment_still_rejects_previous_failed_direction(outcome):
  from openpilot.starpilot.flm.live_tracking import _demand
  o = owner()
  for i in range(12):
    e = episode(o, i, 'ff_gain_right', kind='large')
    e = replace(e, sensitivity=(feature(),), normalization_accel=max(0.1, e.goal_rms))
    if i == 0:
      history = o.closed_loop_worse if outcome == 'worse' else o.inconclusive_trials
      history.append(('ff_gain_right', 1, e.profile_sha256, _demand(e), e.start_ns))
    refresh(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
  assert o.offer_state is None and o.discovery is None and not any(o.accepted.values())


def test_identified_plant_cannot_replace_quantized_direction_with_opposite_experiment():
  from openpilot.starpilot.flm.tests.test_live_tracking import optimizer, refresh as fitted_refresh
  o = optimizer()
  for i in range(20):
    e = episode(o, i, 'ff_gain_right', kind='large')
    e = replace(e, sensitivity=(feature(),), normalization_accel=max(0.1, e.goal_rms))
    fitted_refresh(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
  assert o.offer_state is None and o.discovery is None and not any(o.accepted.values())


def test_opposite_source_experiment_requires_three_new_uncensored_baseline_windows():
  o = owner()
  selection_ids = set()
  for i in range(12):
    e = episode(o, i, 'ff_gain_right', kind='large')
    e = replace(e, sensitivity=(feature(),), normalization_accel=max(0.1, e.goal_rms))
    refresh(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
    if o.discovery is not None and not selection_ids:
      selection_ids.update(o.selector_hint['source_episode_ids'])
      assert o.offer_state is None and not o.before
    if o.offer_state is not None:
      break
  assert o.offer_state is not None and len(o.before) == 3
  assert selection_ids and selection_ids.isdisjoint(row.episode_id for row in o.before)
  assert o.offer_state['target']['ff_gain_right'] == SOURCE_EXPERIMENT_STEP
  assert not o.offer_state.get('accepted', False) and not any(o.accepted.values())
