"""Synthetic optimizer protocol tests; no physical/native/application proof."""
from dataclasses import replace
import hashlib
import math
import pytest

from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
from openpilot.starpilot.flm.live_tracking import LiveTrackingOptimizer
from openpilot.starpilot.flm.tests.test_live_tracking import episode
from openpilot.starpilot.flm.tests.test_tracking_source_context import context, refresh


def offer_state(owner):
  state = owner.offer_state
  assert state is not None
  return state


def selector_hint(owner):
  hint = owner.selector_hint
  assert hint is not None
  return hint


class Drive:
  """Declared whole episodes and typed ACK operands, never vehicle evidence."""
  def __init__(self, initial=.0):
    c = context()
    self.owner = LiveTrackingOptimizer(c.binding, tuple((n, initial) for n in NAMES),
      controller_domain='torque', response_certificate=c)
    self.now = 1_000_000_000

  def ack(self, offer=None, **changes):
    o = self.owner
    self.now += 1_000_000
    refresh(o, self.now)
    offer = offer or o.offer(self.now)
    assert offer is not None
    values = {'trial_id': offer.trial_id, 'target_sequence': offer.target_sequence, 'phase': offer.phase,
      'target_sha256': offer.target_sha256, 'applied_full30': offer.target, 'binding': offer.binding,
      'control_ns': self.now, 'now_ns': self.now, 'status': 'applied',
      'confirmed_target_sha256': offer.target_sha256 if offer.phase == 'confirmed' else ''}
    values.update(changes)
    return o.acknowledge_application(**values)

  def row(self, name='ff_gain_left', *, error=.045, goal=.09, profile=None, reversal=1., shape=None, renew=True, kind='subtle'):
    o = self.owner
    start = self.now + 1_000_000_000
    self.now = start + 2_000_000_000
    row = episode(o, int(start), name, kind='subtle', error=error, reversal=reversal, start=start)
    row = replace(row, start_ns=start, end_ns=self.now, kind=kind, direction=1 if kind == 'large' else 0, speed_low=26.7, speed_high=26.9,
      episode_id=hashlib.sha256(repr((start, name)).encode()).hexdigest(),
      profile_sha256=profile or o._hash(o.accepted), goal_rms=goal, normalization_accel=max(.1, goal),
      goal_shape=shape or (goal / max(.1, goal),) * 8)
    refresh(o, self.now)
    if renew and o.offer_state is not None and offer_state(o)['phase'] == 'rollback':
      assert self.ack()
    return row, o.observe_episode(row, now_ns=self.now)

  def trial(self, name='ff_gain_left', **row_values):
    o = self.owner
    for _ in range(20):
      self.row(name, **row_values)
      if o.offer_state is not None and offer_state(o)['phase'] == 'trial':
        offer = o.offer(self.now)
        assert offer is not None
        return offer
    raise AssertionError('No bounded source trial selected')

  def batch(self, offer, name='ff_gain_left', **row_values):
    rows = []
    for _ in range(3):
      row, _ = self.row(name, profile=offer.target_sha256, **row_values)
      rows.append(row)
    return rows


def return_phase(d, name='ff_gain_left', **values):
  trial = d.trial(name, **values)
  assert d.ack(trial)
  d.batch(trial, name, error=.03, goal=values.get('goal', .09), shape=values.get('shape'))
  o = d.owner
  assert offer_state(o)['phase'] == 'rollback', 'A first positive batch must return to A, not confirm B'
  assert o.source_recheck is not None
  assert not offer_state(o)['accepted'] and o.source_recheck.first_ack_ns == 0
  return trial


def reapply_phase(d, name='ff_gain_left'):
  trial = return_phase(d, name)
  assert d.ack()
  for _ in range(3):
    d.row(name, error=.045)
  o = d.owner
  reapplied = o.offer(d.now)
  assert o.source_recheck is None and reapplied is not None and reapplied.phase == 'trial'
  assert reapplied.trial_id != trial.trial_id and reapplied.target_sequence > trial.target_sequence
  assert reapplied.started_ns == trial.started_ns and reapplied.deadline_ns == trial.deadline_ns
  assert reapplied.target == trial.target and reapplied.baseline == trial.baseline
  return trial, reapplied


@pytest.mark.parametrize('name', NAMES)
def test_each_coordinate_requires_return_baseline_and_fresh_reapplied_performance(name):
  d = Drive(initial=.002)
  o = d.owner
  initial = dict(o.accepted)
  first, second = reapply_phase(d, name)
  assert o.accepted == initial and o.settled_ns is None and not o.future
  assert len(o.before) == 3 and len(offer_state(o)['source_rechecked_before']) == 3
  assert not d.ack(first)  # Old B1 trial/sequence cannot acknowledge B2.
  early, admitted = d.row(name, profile=second.target_sha256, error=.03)
  assert not admitted and not o.future
  assert d.ack(second)
  for index in range(3):
    d.row(name, profile=second.target_sha256, error=.03)
    assert offer_state(o)['phase'] == ('confirmed' if index == 2 else 'trial')
    assert o.accepted == initial
  confirmed = o.offer(d.now)
  assert confirmed is not None and confirmed.phase == 'confirmed'
  assert confirmed.trial_id == second.trial_id and confirmed.deadline_ns == first.deadline_ns
  assert early.episode_id not in {row.episode_id for row in o.future}
  assert d.ack(confirmed)
  assert o.accepted == dict(second.target)
  assert [n for n in NAMES if o.accepted[n] != initial[n]] == [name]


def test_same_optimizer_can_earn_more_than_one_coordinate_without_losing_prior_values():
  d = Drive()
  o = d.owner
  for name in NAMES:
    previous = dict(o.accepted)
    _, target = reapply_phase(d, name)
    assert d.ack(target)
    d.batch(target, name, error=.03)
    confirmed = o.offer(d.now)
    assert confirmed is not None and confirmed.phase == 'confirmed' and d.ack(confirmed)
    assert all(o.accepted[n] == previous[n] for n in NAMES if n != name)
    assert o.accepted[name] > previous[name]
  assert all(o.accepted[n] > 0 for n in NAMES)


@pytest.mark.parametrize('case', ['easier_road_within_matching_tolerance', 'substantially_easier_road', 'slower_input_with_unchanged_lag'])
def test_null_response_cannot_confirm_when_fresh_baseline_has_same_apparent_benefit(case):
  d = Drive()
  if case == 'easier_road_within_matching_tolerance':
    old = {'goal': .087, 'error': .0435}
    new = {'goal': .075, 'error': .0375}
  elif case == 'substantially_easier_road':
    old = {'goal': .09, 'error': .045}
    new = {'goal': .02, 'error': .01}
  else:
    def demand(f):
      values = [math.sqrt(2)*.09*math.sin(2*math.pi*f*(i+.5)/1000) for i in range(2000)]
      measured = [math.sqrt(2)*.09*math.sin(2*math.pi*f*((i+.5)/1000-.12)) for i in range(2000)]
      rms = math.sqrt(sum(v*v for v in values)/len(values))
      return {'goal': rms, 'error': math.sqrt(sum((a-b)**2 for a,b in zip(values,measured,strict=True))/len(values)),
        'shape': tuple(sum(values[i:i+250])/250/max(.1,rms) for i in range(0,2000,250))}
    old,new = demand(.5),demand(.45)
  trial = d.trial(**old)
  assert d.ack(trial)
  d.batch(trial, **new)
  o = d.owner
  assert offer_state(o)['phase'] == 'rollback', 'An easier input cannot immediately confirm an unchanged-response B'
  assert o.source_recheck is not None
  assert not any(o.accepted.values()) and d.ack()
  for _ in range(3):
    d.row(**new)
  assert o.source_recheck is None and offer_state(o)['phase'] == 'rollback' and offer_state(o)['accepted']
  assert o.reason == 'return_baseline_did_not_confirm_improvement'
  assert not any(o.accepted.values())


def test_repeated_baseline_ack_does_not_move_first_boundary_or_erase_new_baseline():
  d = Drive()
  return_phase(d)
  o = d.owner
  assert d.ack()
  first = o.source_recheck.first_ack_ns
  row,_ = d.row(error=.045)
  assert [v.episode_id for v in o.source_recheck.baseline] == [row.episode_id]
  for _ in range(10):
    assert d.ack()
    assert o.source_recheck.first_ack_ns == first
    assert [v.episode_id for v in o.source_recheck.baseline] == [row.episode_id]


@pytest.mark.parametrize('phase', ['baseline_ack_pending', 'baseline_one_row', 'reapplied_ack_pending', 'reapplied_one_row', 'confirmed_ack_pending'])
def test_original_deadline_ends_every_recheck_transition(phase):
  d = Drive()
  o = d.owner
  if phase.startswith('baseline'):
    first = return_phase(d)
    if phase == 'baseline_one_row':
      assert d.ack()
      d.row(error=.045)
  else:
    first, target = reapply_phase(d)
    if phase != 'reapplied_ack_pending':
      assert d.ack(target)
      d.row(profile=target.target_sha256, error=.03)
      if phase == 'confirmed_ack_pending':
        d.row(profile=target.target_sha256, error=.03)
        d.row(profile=target.target_sha256, error=.03)
        assert offer_state(o)['phase'] == 'confirmed'
  d.now = first.deadline_ns + 1
  refresh(o,d.now)
  offer = o.offer(d.now)
  assert o.source_recheck is None and not any(o.accepted.values())
  assert offer is None or offer.phase == 'rollback'
  assert not d.ack(first)


@pytest.mark.parametrize('stage', ['first_trial', 'reapplied_trial'])
def test_actual_gross_oscillation_rejection_remains_terminal(stage):
  d = Drive()
  o = d.owner
  offer = d.trial() if stage == 'first_trial' else reapply_phase(d)[1]
  assert d.ack(offer)
  d.row(profile=offer.target_sha256, error=.03, reversal=10.)
  assert o.reason == 'trial_tracking_or_oscillation_worse' and offer_state(o)['phase'] == 'rollback'
  assert getattr(o,'source_recheck',None) is None and o.paused_intent is None and not any(o.accepted.values())


@pytest.mark.parametrize('reason,changed', [('manual_override',False), ('stale_control',False), ('changed_model',True)])
def test_interrupted_return_baseline_never_retains_provisional_candidate(reason, changed):
  d = Drive(initial=.002)
  return_phase(d)
  assert d.ack()
  d.row(error=.045)
  d.owner.interrupt(reason,context_changed=changed)
  assert d.owner.source_recheck is None and offer_state(d.owner)['phase'] == 'rollback'
  assert all(value == .002 for value in d.owner.accepted.values())


def test_pretrial_numbers_wait_for_original_demand_without_an_offer_or_expiry_renewal():
  d = Drive()
  o = d.owner
  for _ in range(20):
    d.row()
    if o.discovery is not None:
      break
  assert o.discovery is not None
  discovery = o.discovery
  selected,expires = selector_hint(o)['selected_ns'],selector_hint(o)['expires_ns']
  d.row()  # First whole independent baseline.
  ids = tuple(v.episode_id for v in o.comparison)
  assert len(ids) == 1
  d.row(goal=.4,kind='large')
  assert o.discovery is discovery and tuple(v.episode_id for v in o.comparison) == ids
  assert selector_hint(o)['selected_ns'] == selected and selector_hint(o)['expires_ns'] == expires
  assert o.offer(d.now) is None
  for _ in range(2):
    d.row()
  assert o.offer_state is not None and offer_state(o)['phase'] == 'trial'
  assert {v.episode_id for v in o.before}.issuperset(ids)


@pytest.mark.parametrize('bad', ['wrong_id', 'old_phase', 'wrong_hash', 'stale'])
def test_return_baseline_requires_its_own_current_exact_ack(bad):
  d = Drive()
  first = return_phase(d)
  o = d.owner
  offer = o.offer(d.now)
  changes = {'wrong_id': {'trial_id': 'f'*64}, 'old_phase': {'phase': 'trial'},
    'wrong_hash': {'target_sha256': first.target_sha256},
    'stale': {'control_ns': d.now-100_000_001}}[bad]
  assert not d.ack(offer, **changes)
  assert o.source_recheck.first_ack_ns == 0 and not offer_state(o)['accepted']
  row, admitted = d.row(error=.045, renew=False)
  assert not admitted and not o.source_recheck.baseline
  assert row.episode_id not in {v.episode_id for v in o.source_recheck.before}


def test_return_baseline_excludes_pre_ack_partial_duplicate_and_unmatched_windows():
  d = Drive()
  return_phase(d)
  o = d.owner
  assert d.ack()
  boundary = o.source_recheck.first_ack_ns
  row,_ = d.row(error=.045)
  assert len(o.source_recheck.baseline) == 1
  refresh(o,d.now)
  assert not o.observe_episode(row,now_ns=d.now)
  assert len(o.source_recheck.baseline) == 1
  unmatched, admitted = d.row(error=0.,goal=.4,kind='large')
  assert not admitted and len(o.source_recheck.baseline) == 1
  assert unmatched.episode_id not in {v.episode_id for v in o.source_recheck.baseline}
  # A late delivery crossing the first A ACK cannot be repurposed as A2.
  crossing = replace(row,episode_id='e'*64,start_ns=boundary-100_000_000,end_ns=boundary+1_000_000_000)
  refresh(o,d.now)
  assert not o.observe_episode(crossing,now_ns=d.now)
  assert len(o.source_recheck.baseline) == 1


@pytest.mark.parametrize('change', ['binding','certificate','domain','accepted'])
def test_recheck_numeric_state_does_not_survive_identity_model_or_baseline_change(change):
  d = Drive()
  return_phase(d)
  o = d.owner
  assert d.ack()
  if change == 'binding':
    o.binding = replace(o.binding,source_epoch='f'*64)
  elif change == 'certificate':
    o.certificate = replace(o.certificate,observed_ns=o.certificate.observed_ns+1)
  elif change == 'domain':
    o.domain = 'torque_pid'
  else:
    o.accepted['ff_gain_left'] = .002
  o.offer(d.now)
  assert o.source_recheck is None
  assert offer_state(o)['phase'] == 'rollback'


def test_zero_error_return_baseline_is_not_censored_to_keep_candidate():
  d = Drive()
  return_phase(d)
  assert d.ack()
  for _ in range(3):
    d.row(error=0.)
  o = d.owner
  assert o.source_recheck is None and not any(o.accepted.values())
  assert offer_state(o)['phase'] == 'rollback' and offer_state(o)['accepted']
  assert o.reason == 'return_baseline_did_not_confirm_improvement'


@pytest.mark.parametrize('stage', ['baseline_ack_pending','baseline_one_row','reapplied_ack_pending','reapplied_one_row','confirmed_ack_pending'])
def test_missing_current_response_witness_cannot_advance_any_recheck_transition(stage):
  d = Drive()
  o = d.owner
  if stage.startswith('baseline'):
    return_phase(d)
    if stage == 'baseline_one_row':
      assert d.ack()
      d.row(error=.045)
  else:
    _,target = reapply_phase(d)
    if stage != 'reapplied_ack_pending':
      assert d.ack(target)
      d.row(profile=target.target_sha256,error=.03)
      if stage == 'confirmed_ack_pending':
        d.row(profile=target.target_sha256,error=.03)
        d.row(profile=target.target_sha256,error=.03)
  d.now += 100_000_001
  assert o.offer(d.now) is None
  assert o.source_recheck is None and not any(o.accepted.values())
  assert o.offer_state is None or offer_state(o)['phase'] == 'rollback'


@pytest.mark.parametrize('cause',['expiry','context'])
def test_waiting_pretrial_comparison_cannot_renew_or_survive_changed_context(cause):
  d = Drive()
  o = d.owner
  for _ in range(20):
    d.row()
    if o.discovery is not None:
      break
  previous = o.discovery
  assert previous is not None
  d.row()
  old_ids = {v.episode_id for v in o.comparison}
  assert old_ids
  if cause == 'expiry':
    d.now = selector_hint(o)['expires_ns']+1
    d.row()
  else:
    o.interrupt('changed_context',context_changed=True)
  assert o.discovery is not previous and not o.comparison and o.selector_hint is None
  assert o.offer_state is None and not any(o.accepted.values())
