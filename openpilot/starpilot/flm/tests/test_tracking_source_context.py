"""Source-context and numerical optimization contracts; NOT native/road proof."""

from dataclasses import replace
import json
import pytest
from openpilot.starpilot.flm.live_tracking_response import (
  TrackingSourceContext,
  SOURCE_VERSION,
  ResponseWitness,
  _revision,
  validate_certificate,
  prepare_response_witness,
  certificate_to_dict,
  certificate_from_dict,
  source_context,
)
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, coordinate_bounds
from openpilot.starpilot.flm.live_trial_contract import MAX_STEP, SOURCE_EXPERIMENT_STEP
from openpilot.starpilot.flm.tests.test_live_tracking import fixture_certificate, episode
from openpilot.starpilot.flm.live_tracking import LiveTrackingOptimizer


def context(binding=None):
  b = binding or fixture_certificate().binding
  c = TrackingSourceContext(SOURCE_VERSION, b, '0' * 64, 170_000_000, 'a' * 64, 1)
  revision = _revision(c)
  return validate_certificate(replace(c, model_revision=revision, binding=replace(b, physical_model_revision=revision)))


def owner():
  c = context()
  return LiveTrackingOptimizer(c.binding, tuple((n, 0.0) for n in NAMES), controller_domain='torque', response_certificate=c)


def refresh(o, now):
  w = ResponseWitness(o.certificate, o.binding, now - o.lag_ns, now - o.lag_ns, now, max(1, now - 350_000_000), 8, None, now + 100_000_000)
  assert o.response_current(w, now_ns=now)


def test_source_context_has_no_fabricated_model_precision_or_training_results():
  c = context()
  assert not any(hasattr(c, n) for n in ('slope', 'intercept', 'heldout_rmse', 'train_block_ids', 'heldout_block_ids'))
  raw = certificate_to_dict(c)
  assert certificate_from_dict(json.loads(json.dumps(raw))) == c
  assert raw['version'] == SOURCE_VERSION
  assert not {'profile', 'permission', 'current_native', 'heldout_rmse'} & raw.keys()


def test_legacy_point_precision_is_unchanged():
  old = fixture_certificate()
  bad = replace(old, heldout_rmse=0.1)
  revision = _revision(bad)
  bad = replace(bad, model_revision=revision, binding=replace(bad.binding, physical_model_revision=revision))
  with pytest.raises(ValueError):
    validate_certificate(bad)


@pytest.mark.parametrize('name', NAMES)
def test_global_thirty_coordinates_require_new_unselected_baseline(name):
  o = owner()
  kind = 'subtle' if name.startswith('center_') or name == 'highway_center_taper_max' else 'large'
  count = 3
  discovery = set()
  for i in range(2 * count):
    e = episode(o, i, name, kind=kind, error=0.2)
    e = replace(e, normalization_accel=max(0.1, e.goal_rms))
    refresh(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
    if o.discovery is not None and not discovery:
      discovery.update(o.selector_hint['source_episode_ids'])
    if i < count:
      assert o.offer_state is None
  selected = o.offer(e.end_ns)
  assert selected is not None and selected.names == (name,) and len(o.before) == count
  assert discovery and discovery.isdisjoint(e.episode_id for e in o.before)
  assert o.offer_state['alpha'] == 0.0 and o.offer_state['critical'] == 0.0
  assert 'noise_prior' not in o.offer_state and len(selected.target) == 30 and not any(o.accepted.values())
  changes = [n for n, v in selected.target if v != dict(selected.baseline)[n]]
  assert changes == [name] and abs(dict(selected.target)[name]) <= MAX_STEP
  for knob, value in selected.target:
    low, high = coordinate_bounds(knob)
    assert low <= value <= high


def test_source_wire_roundtrip_and_model_field_smuggling_rejection():
  from openpilot.cereal import messaging
  from openpilot.starpilot.flm.tests.test_live_trial_owner import offer
  from openpilot.starpilot.flm.live_trial_wire import write_trial, read_trial

  old = offer(10_000_000_000)
  c = context(old.binding)
  w = ResponseWitness(
    c, c.binding, old.observed_ns - c.lag_ns, old.observed_ns - c.lag_ns, old.observed_ns, old.observed_ns - 350_000_000, 8, None, old.observed_ns + 100_000_000
  )
  new = replace(old, binding=c.binding, response_witness=prepare_response_witness(w, c.binding, old.observed_ns))
  event = messaging.new_message('starpilotSteeringTracking', valid=True, logMonoTime=old.observed_ns)
  write_trial(event, new)
  read = read_trial(messaging.log_from_bytes(event.to_bytes()).starpilotSteeringTracking)
  assert read.response_witness.certificate == c and read.response_witness.residual_rmse is None
  event = event.as_reader().as_builder()
  event.starpilotSteeringTracking.trial.responseWitness.certificate.slope = 1.0
  with pytest.raises(ValueError, match='cannot claim fitted'):
    read_trial(event.starpilotSteeringTracking)


def source_retainer():
  from openpilot.starpilot.flm.tests.test_live_tracking_requalification import certificate, basis, new_drive
  from openpilot.starpilot.flm.live_tracking_requalification import RetainedResponseRequalifier

  old = context(certificate().binding)
  return RetainedResponseRequalifier(old, basis(old), new_drive(old))


def test_new_drive_reuses_no_old_permission_and_needs_eight_original_sources():
  from openpilot.starpilot.flm.tests.test_live_tracking_requalification import feed

  retainer = source_retainer()
  assert retainer.qualified(1) is None
  for i in range(7):
    assert feed(retainer, i, command=0.4)
  assert retainer.qualified(10_300_000_000) is None
  assert feed(retainer, 7, command=0.4)
  c, w = retainer.qualified(10_350_000_000)
  assert c.provenance == 'retained-current-source-v3' and w.independent_poses == 8
  assert w.residual_rmse is None and not hasattr(c, 'slope')
  assert retainer.qualified(10_450_000_001) is None


@pytest.mark.parametrize('fault', ['native', 'source', 'override', 'fault', 'stale', 'hardware', 'controller'])
def test_source_context_never_bypasses_current_source_native_or_context_guards(fault):
  from openpilot.starpilot.flm.tests.test_live_tracking_requalification import synthetic_pair

  retainer = source_retainer()
  for i in range(8):
    c, o, p = synthetic_pair(retainer, i, command=0.4)
    if fault == 'source':
      c = replace(c, source_epoch='0' * 64)
    if fault == 'override':
      c = replace(c, override=True)
    if fault == 'fault':
      c = replace(c, fault=True)
    if fault == 'controller':
      c = replace(c, controller_revision='changed')
    now = p.mono_ns + (100_000_001 if fault == 'stale' else 0)
    assert not retainer.observe(
      c, o, p, now_ns=now, current_native=fault != 'native', hardware_inventory=('different',) if fault == 'hardware' else ('measured-hardware-type',)
    )
  assert retainer.qualified(now) is None


def test_construct_context_uses_actual_consumed_delay_not_a_fitted_or_default_value():
  from openpilot.starpilot.flm.tests.test_live_tracking_requalification import synthetic_pair

  r = source_retainer()
  c, o, p = synthetic_pair(r, 0)
  with pytest.raises(KeyError):
    source_context(r._binding, c, p, now_ns=p.mono_ns)
  c = replace(c, replay_inputs=(*c.replay_inputs, ('lat_delay', 0.21)))
  result = source_context(r._binding, c, p, now_ns=p.mono_ns)
  assert result.lag_ns == 210_000_000 and result.observed_ns == p.mono_ns
  assert not hasattr(result, 'heldout_rmse')


def test_direct_kinematic_target_does_not_mix_two_roll_estimates():
  from openpilot.starpilot.flm.live_tracking_episodes import tracking_reference_acceleration, tracking_reference_force
  from openpilot.starpilot.flm.tests.test_live_tracking_requalification import synthetic_pair

  r = source_retainer()
  c, _, _ = synthetic_pair(r, 0)
  c = replace(c, stage_values=(('setpoint', 0.2),), replay_inputs=(*c.replay_inputs, ('roll', 0.1)))
  assert tracking_reference_acceleration(c) == 0.2
  assert tracking_reference_force(c) < 0


def test_unbroken_long_curve_yields_input_scheduled_windows_without_turn_ending():
  from openpilot.starpilot.flm.live_tracking_episodes import TrackingEpisodeBuilder

  c = context()
  b = TrackingEpisodeBuilder(c.binding, c)
  for tick in range(601):
    stamp = 10_000_000_000 + tick * 10_000_000
    b._geometry(stamp, 0.3)
    b.last = stamp
  assert len(b.pending) == 3
  assert all(kind == 'large' and direction == 1 and end - start == 1_990_000_000 for start, end, kind, direction, _ in b.pending)
  # No independent response, native sample, probe or fit is invented.
  assert not b.endpoints and not b.ready and not b.probes


def test_straight_raw_goal_stays_zero_with_an_explicit_numerical_score_scale():
  from openpilot.starpilot.flm.live_tracking import _score, _matches

  o = owner()
  e = episode(o, 0, 'center_taper_max', kind='subtle', error=0.02)
  straight = replace(e, direction=0, goal_rms=0.0, goal_shape=(0.0,) * 8, normalization_accel=0.1)
  straight.validate()
  assert straight.goal_rms == 0.0 and _score(straight) == pytest.approx(0.2)
  assert _matches(straight, replace(straight, episode_id='f' * 64))
  with pytest.raises(ValueError):
    replace(straight, normalization_accel=0.001).validate()


def test_source_gap_recovers_on_fixed_input_slots_without_reanchoring_origin():
  from openpilot.starpilot.flm.live_tracking_episodes import TrackingEpisodeBuilder

  c = context()
  b = TrackingEpisodeBuilder(c.binding, c)
  origin = 10_000_000_000
  for tick in range(101):
    stamp = origin + tick * 10_000_000
    b._geometry(stamp, 0.3)
    b.last = stamp
  b.interrupt('genuine-source-gap')
  assert b.input_window_origin == origin
  for tick in range(110, 601):
    stamp = origin + tick * 10_000_000
    b._geometry(stamp, 0.3)
    b.last = stamp
  assert len(b.pending) == 2
  assert [row[0] for row in b.pending] == [origin + 1_100_000_000, origin + 3_100_000_000]
  assert all(end - start == 1_990_000_000 for start, end, *_ in b.pending)
  assert b.pending[0][1] < b.pending[1][0]
  assert not b.endpoints and not b.ready


def test_source_proposal_does_not_claim_instantaneous_gradient_is_plant_direction():
  o = owner()
  for i in range(100):
    e = episode(o, i, 'ff_gain_left', kind='large', error=0.2)
    factor = (1.0, -1.0, 2.0)[i % 3]
    e = replace(
      e, normalization_accel=max(0.1, e.goal_rms), sensitivity=tuple(replace(f, error_feature_sum=f.error_feature_sum * factor) for f in e.sensitivity)
    )
    refresh(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
    if o.offer_state is not None:
      break
  assert o.offer_state is not None and o.offer_state['phase'] == 'trial'
  assert not any(o.accepted.values()) and len(o.before) == 3
  assert o.offer_state['alpha'] == 0.0 and abs(o.offer_state['target']['ff_gain_left']) <= MAX_STEP
  assert not o.offer_state.get('accepted', False)


def test_direct_input_matching_uses_road_regime_not_waveform_phase():
  from openpilot.starpilot.flm.live_tracking import _matches

  o = owner()
  e = replace(episode(o, 0, 'ff_gain_left', kind='subtle'), normalization_accel=1.0, direction=0, goal_shape=(0.0, 0.2, 0.0, -0.2, 0.0, 0.2, 0.0, -0.2))
  shifted = replace(e, goal_shape=(0.2, 0.0, -0.2, 0.0, 0.2, 0.0, -0.2, 0.0))
  assert _matches(e, shifted)
  assert not _matches(e, replace(shifted, speed_low=2.0, speed_high=2.0))
  assert _matches(e, replace(shifted, normalization_accel=2.0))
  assert _matches(e, replace(shifted, goal_shape=(0.9,) * 8))
  assert _matches(e, replace(shifted, goal_shape=(1.0, -1.0) * 4))
  # The old physical whole-maneuver protocol retains exact waveform matching.
  assert not _matches(replace(e, normalization_accel=None), replace(shifted, normalization_accel=None))


def test_selected_input_class_does_not_silently_move_to_first_baseline():
  from openpilot.starpilot.flm.live_tracking import _matches, _demand, _matches_demand

  o = owner()

  def row(i, scale):
    return replace(episode(o, i, 'ff_gain_left', kind='large'), goal_rms=scale, normalization_accel=scale, goal_shape=(1.0,) * 8)

  anchor = row(0, 1.0)
  different = row(1, 0.81)
  before = tuple(row(i, 1.24) for i in range(2, 5))
  assert _matches(anchor, different) and _matches(anchor, before[0]) and _matches(before[0], different)
  o.selector_hint = {
    'binding': o.binding,
    'model_revision': o.certificate.model_revision,
    'profile_sha256': anchor.profile_sha256,
    'selected_ns': anchor.end_ns,
    'expires_ns': before[-1].end_ns + 1,
    'demand': _demand(anchor),
  }
  target = dict(o.accepted, ff_gain_left=0.005)
  plan = o._trial_plan(before, 0.001, target, minimum_episodes=3)
  assert plan is not None and o._launch('ff.left', 'ff_gain_left', target, before, plan, before[-1].end_ns)
  assert o.offer_state['demand'] == _demand(anchor) and _matches_demand(o.offer_state['demand'], different)
  assert not any(o.accepted.values())


def test_source_probe_window_keeps_reference_membership_and_original_native_clocks():
  from types import SimpleNamespace as S
  from openpilot.starpilot.flm.live_tracking_actor import _original_probe_windows

  points = tuple(S(control_ns=i, reference_ns=i + 170) for i in range(100, 801, 100))
  e = S(source_tracking=True, completed={}, pending=((200, 800, 'subtle', 0, 'profile'),), endpoints=points, probe_requests={100: ('all30', NAMES, 900)})
  assert _original_probe_windows(e, 'profile', 'all30', NAMES) == ((100, 600, frozenset({100})),)
  assert tuple((p.control_ns, p.reference_ns) for p in points) == tuple((i, i + 170) for i in range(100, 801, 100))
  e.source_tracking = False
  assert _original_probe_windows(e, 'profile', 'all30', NAMES) == ((200, 800, frozenset()),)


def test_old_history_eviction_cannot_block_the_new_fresh_baseline():
  o = owner()
  i = 0
  while o.discovery is None and i < 80:
    e = episode(o, i, 'ff_gain_left', kind='large', error=0.2)
    e = replace(e, normalization_accel=max(0.1, e.goal_rms))
    refresh(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
    i += 1
  assert o.discovery is not None
  o.planning_history.clear()
  for _ in range(3):
    e = episode(o, i, 'ff_gain_left', kind='large', error=0.2)
    e = replace(e, normalization_accel=max(0.1, e.goal_rms))
    refresh(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
    i += 1
  assert o.offer_state is not None and o.offer_state['phase'] == 'trial' and len(o.before) == 3
  assert 'noise_prior' not in o.offer_state and not any(o.accepted.values())


@pytest.mark.parametrize(
  'before,future,expected',
  [
    ([1.0] * 9, [0.99] * 8 + [1.01], 1),
    ([1.0] * 9, [1.0] * 9, 0),
    ([1.0] * 9, [0.9] * 7 + [1.1] * 2, 0),
    ([1.0] * 9, [0.1] + [1.01] * 8, 0),
    ([1.0] * 9, [1.02] * 9, -1),
    ([1.0] * 9, [0.9999] * 9, 0),
    ([1.0] * 3, [0.8] * 3, 1),
    ([1.0] * 3, [0.8, 0.8, 1.1], 0),
  ],
)
def test_empirical_feedback_needs_mean_median_and_repeated_later_improvement(before, future, expected):
  from openpilot.starpilot.flm.live_tracking import _feedback_result

  assert _feedback_result(before, future) == expected


@pytest.mark.parametrize(
  'before,future', [([1.0] * 2, [0.5] * 2), ([1.0] * 3, [0.5] * 9), ([1.0] * 3, [float('nan')] * 3), ([True] * 3, [0.5] * 3), ([-1.0] * 3, [0.5] * 3)]
)
def test_empirical_feedback_never_accepts_malformed_measurements(before, future):
  from openpilot.starpilot.flm.live_tracking import _feedback_result

  with pytest.raises(ValueError):
    _feedback_result(before, future)


def source_ack(o, offer, stamp):
  refresh(o, stamp)
  return o.acknowledge_application(
    trial_id=offer.trial_id,
    target_sequence=offer.target_sequence,
    phase=offer.phase,
    target_sha256=offer.target_sha256,
    applied_full30=offer.target,
    control_ns=stamp,
    binding=offer.binding,
    now_ns=stamp,
    status='applied',
    confirmed_target_sha256=offer.target_sha256 if offer.phase == 'confirmed' else '',
  )


def test_feedback_confirmation_still_needs_actual_target_ack_and_new_current_witness():
  o = owner()
  for i in range(6):
    e = replace(episode(o, i, 'ff_gain_left', error=0.2), normalization_accel=0.5)
    refresh(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
  offer = o.offer(e.end_ns)
  assert offer is not None and offer.phase == 'trial'
  assert not any(o.accepted.values()) and source_ack(o, offer, e.end_ns + 10_000_000)
  now = e.end_ns
  for i in range(3):
    e = replace(
      episode(o, 100 + i, 'ff_gain_left', profile=offer.target_sha256, error=0.15, start=now + 1_000_000_000 + i * 1_000_000_000), normalization_accel=0.5
    )
    refresh(o, e.end_ns)
    assert o.observe_episode(e, now_ns=e.end_ns)
  returned = o.offer(e.end_ns)
  assert returned is not None and returned.phase == 'rollback' and not any(o.accepted.values())
  assert source_ack(o, returned, e.end_ns + 10_000_000)
  now = e.end_ns
  for i in range(3):
    e = replace(episode(o, 200 + i, 'ff_gain_left', error=0.2,
      start=now + 1_000_000_000 + i * 1_000_000_000), normalization_accel=0.5)
    refresh(o, e.end_ns)
    assert source_ack(o, o.offer(e.end_ns), e.end_ns)
    assert o.observe_episode(e, now_ns=e.end_ns)
  reapplied = o.offer(e.end_ns)
  assert reapplied is not None and reapplied.phase == 'trial' and reapplied.trial_id != offer.trial_id
  assert reapplied.deadline_ns == offer.deadline_ns and not any(o.accepted.values())
  assert source_ack(o, reapplied, e.end_ns + 10_000_000)
  now = e.end_ns
  for i in range(3):
    e = replace(episode(o, 300 + i, 'ff_gain_left', error=0.15, profile=reapplied.target_sha256,
      start=now + 1_000_000_000 + i * 1_000_000_000), normalization_accel=0.5)
    refresh(o, e.end_ns)
    assert o.observe_episode(e, now_ns=e.end_ns)
  confirmed = o.offer(e.end_ns)
  assert confirmed is not None and confirmed.phase == 'confirmed' and not any(o.accepted.values())
  assert source_ack(o, confirmed, e.end_ns + 10_000_000)
  assert o.accepted['ff_gain_left'] == dict(offer.target)['ff_gain_left']


def test_small_step_can_improve_mean_and_median_with_normal_baseline_variation():
  from openpilot.starpilot.flm.live_tracking import _feedback_result

  before = [0.1246, 0.1275, 0.1264, 0.1251, 0.1256, 0.1239, 0.1234, 0.1297, 0.1278]
  future = [0.1243, 0.1209, 0.1240, 0.1207, 0.1221, 0.1256, 0.1208, 0.1275, 0.1251]
  assert _feedback_result(before, future) == 1
  # A lower average from one lucky point is still insufficient.
  assert _feedback_result([1.0] * 9, [0.1] + [1.01] * 8) == 0
  # Repeated new errors outside the original envelope are not kept.
  assert _feedback_result([1.0] * 9, [0.9] * 7 + [1.1] * 2) == 0


@pytest.mark.parametrize('upper', [False, True])
def test_source_boundary_tries_feasible_direction_without_claiming_plant_sign(upper):
  from openpilot.starpilot.flm.canonical_surface_adapter import coordinate_bounds

  o = owner()
  name = 'center_deadband_highway_deg'
  low, high = coordinate_bounds(name)
  o.accepted[name] = high if upper else low
  discovery_ids = set()
  for i in range(5):
    e = episode(o, i, name, kind='large', error=0.079)
    # Magnitudes retained from c0's original native probe: one of four
    # endpoints separates. This fixture tests selection, not native admission.
    f = replace(e.sensitivity[0], error_feature_sum=0.015966462570723775 if upper else -0.015966462570723775,
                feature_energy=0.2391186088079348, native_separated=1, native_endpoints=4, max_step_physical_rms=0.0012224938875305645)
    e = replace(e, goal_rms=0.77, normalization_accel=0.77, sensitivity=(f,))
    refresh(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
    if i == 1:
      assert o.discovery is not None and o.offer_state is None
      discovery_ids.update(o.selector_hint['source_episode_ids'])
  selected = o.offer(e.end_ns)
  assert selected is not None and selected.names == (name,)
  assert dict(selected.target)[name] == pytest.approx(o.accepted[name] + (-SOURCE_EXPERIMENT_STEP if upper else SOURCE_EXPERIMENT_STEP))
  assert len(o.before) == 3 and discovery_ids.isdisjoint(row.episode_id for row in o.before)
  assert not o.offer_state['accepted'] and o.offer_state['alpha'] == 0.0


@pytest.mark.parametrize('outcome', ['worse', 'inconclusive'])
def test_source_boundary_fallback_does_not_repeat_rejected_feasible_direction(outcome):
  from openpilot.starpilot.flm.live_tracking import _demand

  o = owner()
  name = 'center_deadband_highway_deg'
  for i in range(4):
    e = replace(episode(o, i, name, kind='large'), normalization_accel=0.5)
    e = replace(e, sensitivity=tuple(replace(f, error_feature_sum=-f.error_feature_sum) for f in e.sensitivity))
    if i == 0:
      history = o.closed_loop_worse if outcome == 'worse' else o.inconclusive_trials
      history.append((name, 1, e.profile_sha256, _demand(e), e.start_ns))
    refresh(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
  assert o.discovery is None and o.offer_state is None


def test_fitted_plant_does_not_reverse_identified_direction_at_boundary():
  from openpilot.starpilot.flm.tests.test_live_tracking import optimizer, refresh as fitted_refresh

  o = optimizer()
  for i in range(20):
    e = episode(o, i, 'center_deadband_highway_deg', kind='subtle')
    e = replace(e, sensitivity=tuple(replace(f, error_feature_sum=-f.error_feature_sum) for f in e.sensitivity))
    fitted_refresh(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
  assert o.discovery is None and o.offer_state is None


@pytest.mark.parametrize('source_only', [False, True])
def test_new_input_regime_does_not_erase_inert_pretrial_baseline(source_only):
  from openpilot.starpilot.flm.tests.test_live_tracking import optimizer, refresh as fitted_refresh

  o = owner() if source_only else optimizer()
  update = refresh if source_only else fitted_refresh
  for i in range(40):
    e = episode(o, i, 'center_taper_max', kind='subtle')
    if source_only:
      e = replace(e, normalization_accel=max(0.1, e.goal_rms))
    update(o, e.end_ns)
    o.observe_episode(e, now_ns=e.end_ns)
    if o.discovery is not None:
      break
  previous = o.discovery
  assert previous is not None and o.offer_state is None
  changed = episode(o, i + 1, 'center_taper_max', kind='large')
  if source_only:
    changed = replace(changed, normalization_accel=max(0.1, changed.goal_rms))
  update(o, changed.end_ns)
  o.observe_episode(changed, now_ns=changed.end_ns)
  assert o.discovery is previous and not o.comparison
  if source_only:
    assert o.selector_hint is not None
    assert o.selector_hint['expires_ns'] == previous['expires_ns']
  assert o.offer_state is None and not any(o.accepted.values())


@pytest.mark.parametrize('estimate', [.0002, -.0002])
def test_source_experiment_uses_measured_one_second_step_not_tiny_confidence_fraction(estimate):
  # Numerical proposal only; native visibility still needs actual controller/CAN evidence.
  o = owner()
  for index in range(6):
    row = episode(o, index, 'ff_gain_left', error=.2)
    feature = replace(row.sensitivity[0], error_feature_sum=estimate, feature_energy=1.)
    row = replace(row, sensitivity=(feature,), normalization_accel=max(.1, row.goal_rms))
    refresh(o, row.end_ns)
    o.observe_episode(row, now_ns=row.end_ns)
  selected = o.offer(row.end_ns)
  assert selected is not None and selected.names == ('ff_gain_left',)
  delta = dict(selected.target)['ff_gain_left'] - dict(selected.baseline)['ff_gain_left']
  assert delta == pytest.approx(SOURCE_EXPERIMENT_STEP if estimate > 0 else -SOURCE_EXPERIMENT_STEP)
  assert abs(delta) > .1 * abs(estimate)
  assert len(selected.target) == 30 and len(o.before) == 3
  assert not any(o.accepted.values()) and not o.future


def test_fitted_model_retains_confidence_sized_proposal():
  from openpilot.starpilot.flm.tests.test_live_tracking import optimizer, ready

  o = optimizer()
  # Preserve the fixture's original derivative: .01 physical delta per .005 coordinate.
  state, now = ready(o, effect=.01 * MAX_STEP / .005)
  assert state is not None
  selected = o.offer(now)
  assert selected is not None
  # Existing fixture estimate=.05; original confidence fraction gives .005.
  assert dict(selected.target)['ff_gain_left'] == pytest.approx(.005)


@pytest.mark.parametrize('goal', [0.0, 0.3, -0.3])
def test_recovery_slot_depends_only_on_original_input_clock(goal):
  from openpilot.starpilot.flm.live_tracking_episodes import TrackingEpisodeBuilder

  c = context()
  b = TrackingEpisodeBuilder(c.binding, c)
  origin = 10_000_000_000
  b._geometry(origin, goal)
  b.last = origin
  b.interrupt('genuine-source-gap')
  # An arbitrary arrival cannot pick its own window start. Wait for the next
  # predeclared50ms slot, regardless of the measured error or steering sign.
  for delta in (1_107_000_000, 1_117_000_000, 1_147_000_000):
    b._geometry(origin + delta, goal)
    b.last = origin + delta
    assert b.start == 0 and b.input_window_wait_until == origin + 1_150_000_000
    assert not b.pending
  b._geometry(origin + 1_157_000_000, goal)
  b.last = origin + 1_157_000_000
  assert b.start == origin + 1_157_000_000
  assert b.input_window_boundary == origin + 3_150_000_000
  assert b.input_window_origin == origin
  for delta in range(1_167_000_000, 3_148_000_000, 10_000_000):
    b._geometry(origin + delta, goal)
    b.last = origin + delta
    assert not b.pending
  b._geometry(origin + 3_157_000_000, goal)
  assert len(b.pending) == 1
  start, end, *_ = b.pending[0]
  assert start == origin + 1_157_000_000 and end == origin + 3_147_000_000
  assert end < b.start and b.input_window_boundary == origin + 5_150_000_000
  assert not b.endpoints and not b.ready and not b.probes


@pytest.mark.parametrize('source_tracking', [False, True])
def test_episode_closure_uses_the_same_time_axis_as_its_endpoints(monkeypatch, source_tracking):
  from types import SimpleNamespace
  from openpilot.starpilot.flm.live_tracking_episodes import TrackingEpisodeBuilder
  from openpilot.starpilot.flm.tests.test_live_tracking_episodes import (
    certificate, tick, pair, numeric_pose, numerical_join_validation_only,
  )

  c = context(certificate().binding) if source_tracking else certificate()
  b = TrackingEpisodeBuilder(c.binding, c)
  # Isolated join arithmetic only; these fixture witnesses prove no native or
  # road admission. The original-packet component replay checks real admission.
  numerical_join_validation_only(monkeypatch, b)
  for index in range(30):
    assert tick(b, index, 0.1)
  control, output, _ = pair(b, 0, 0.1)
  pose = numeric_pose(b, control)
  witness = SimpleNamespace(certificate=c, last_control_ns=control.control_ns,
                            last_apply_ns=output.apply_ns, last_pose_ns=pose.mono_ns)
  assert b.observe_pose(pose, control, output, witness=witness, now_ns=pose.mono_ns, current_native=True)
  point = b.endpoints[-1]
  assert b.watermark == (pose.source_lower_ns if source_tracking else pose.source_lower_ns - c.lag_ns)
  assert b.watermark == (point.reference_ns if source_tracking else point.control_ns)
