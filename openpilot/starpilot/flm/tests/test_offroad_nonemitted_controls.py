"""Real registered GM encoding with declared carrier envelopes; no road permission."""

from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
from opendbc.car.gm.values import CAR
from opendbc.car.steering_projection import TorqueProjection
from openpilot.starpilot.flm.offroad_controller_checks import check_original_native, original_nonemitted_control
from openpilot.starpilot.flm.offroad_checks import fit_training_step, TRAINING_FRACTIONS
from openpilot.starpilot.flm.torque_emission_replay import EmissionHistory
from openpilot.starpilot.flm.torque_eligibility import consumed_sources_current
from openpilot.starpilot.flm.tests.test_torque_emission_join import BASE, cc, emitted, interface, settle


def _original_chain(tmp_path, emissions=2):
  ci = interface(CAR.GMC_ACADIA)
  command = cc(.01)
  settle(ci, command)
  chain, originals, count = [], {}, 0
  for _ in range(max(12, emissions * 3 + 3)):
    stamp = BASE + ci.CC.frame * 10_000_000
    result = emitted(ci, command, tmp_path)
    if result is not None:
      source, output = result
      originals[source.control_ns] = SimpleNamespace(**vars(source), state_ns=output.state_ns,
        controller_revision='declared-codec-source', controller_mode='native', source_receipt_ns=source.control_ns - 1)
      chain.append(output)
      count += 1
      if count == emissions:
        break
    elif chain:
      chain.append(replace(chain[-1], carrier_frame=ci.CC.frame, carrier_observed_ns=stamp))
  assert len(chain) > 2, 'Exercise genuine GM emissions separated by held carriers'
  stamp = chain[0].control_ns + 10_000_000
  original = originals[chain[0].control_ns]
  missing = SimpleNamespace(**dict(vars(original), control_ns=stamp, state_ns=stamp - 1, source_receipt_ns=stamp - 1))
  return ci.CP, missing, tuple(chain), originals


@pytest.fixture
def original_chain(tmp_path):
  return _original_chain(tmp_path)


def test_complete_original_hold_chain_proves_nonemission(original_chain):
  cp, control, chain, originals = original_chain
  assert original_nonemitted_control(cp, control, chain, originals)


@pytest.mark.parametrize('fault', ('missing', 'anchor', 'order', 'body', 'projection', 'duplicate', 'future', 'stale', 'source'))
def test_incomplete_or_changed_hold_chain_never_proves_nonemission(original_chain, fault):
  cp, control, chain, originals = original_chain
  if fault == 'missing':
    chain = chain[:1] + chain[2:]
  elif fault == 'anchor':
    chain = chain[1:]
  elif fault == 'order':
    chain = (chain[0], chain[2], chain[1], *chain[3:])
  elif fault == 'body':
    chain = (chain[0], replace(chain[1], applied=chain[1].applied + .01), *chain[2:])
  elif fault == 'projection':
    chain = (replace(chain[0], projection=cast(TorqueProjection, object())), *chain[1:])
  elif fault == 'duplicate':
    chain = (chain[0], replace(chain[1], carrier_frame=chain[0].carrier_frame), *chain[2:])
  elif fault == 'future':
    chain = tuple(replace(o, apply_ns=control.control_ns + 1) for o in chain[:-1]) + chain[-1:]
  elif fault == 'stale':
    control.source_receipt_ns = control.control_ns - 500_000_001
  elif fault == 'source':
    before = originals[chain[-1].control_ns]
    originals = dict(originals)
    originals[chain[-1].control_ns] = SimpleNamespace(**dict(vars(before), source_epoch='f' * 64))
  assert not original_nonemitted_control(cp, control, chain, originals)


@pytest.mark.parametrize('advance_ms', (0, 6))
def test_async_older_command_can_emit_after_target_without_becoming_its_command(tmp_path, advance_ms):
  cp, control, chain, originals = _original_chain(tmp_path, emissions=3)
  emitted_indices = [i for i, o in enumerate(chain) if o.projection.frame + 1 == o.carrier_frame]
  assert len(emitted_indices) == 3
  middle, last = emitted_indices[1:]
  previous = originals.pop(chain[middle].control_ns)
  apply_ns = chain[middle].apply_ns + 1_000_000
  source_ns = apply_ns - 12_000_000
  originals[source_ns] = SimpleNamespace(**dict(vars(previous), control_ns=source_ns,
                                               state_ns=source_ns - 1, source_receipt_ns=source_ns - 1))
  # Declared async envelope clocks; all original registered CAN bytes stay intact.
  chain = chain[:middle] + tuple(replace(o, control_ns=source_ns, state_ns=apply_ns - 1,
    apply_ns=apply_ns, carrier_observed_ns=o.carrier_observed_ns + 1_000_000) for o in chain[middle:last]) + chain[last:]
  advance = advance_ms * 1_000_000
  chain = chain[:-1] + (replace(chain[-1], apply_ns=chain[-1].apply_ns + advance,
    state_ns=chain[-1].state_ns + advance, carrier_observed_ns=chain[-1].carrier_observed_ns + advance),)
  stamp = apply_ns - 500_000
  control = SimpleNamespace(**dict(vars(control), control_ns=stamp, state_ns=stamp - 1, source_receipt_ns=stamp - 1))
  assert chain[0].apply_ns < stamp - 30_000_000 and apply_ns > stamp
  if advance:
    assert chain[-1].apply_ns > stamp + 30_000_000
  history = EmissionHistory(cp, identity=control.identity, baseline_revision=control.baseline_revision)
  for output in chain:
    source = originals[output.control_ns]
    assert consumed_sources_current(source, output)
    assert history.record_carrier(output) and history.record(source, output)
    history.verify(source, output)
  assert original_nonemitted_control(cp, control, chain, originals)
  assert not original_nonemitted_control(cp, control, chain[:middle] + chain[middle + 1:], originals)
  changed = chain[:middle + 1] + (replace(chain[middle + 1], applied=chain[middle + 1].applied + .01),) + chain[middle + 2:]
  assert not original_nonemitted_control(cp, control, changed, originals)
  stale_apply = chain[-1].control_ns + 31_000_000
  stale = chain[:-1] + (replace(chain[-1], apply_ns=stale_apply, state_ns=stale_apply - 1, carrier_observed_ns=stale_apply),)
  assert not original_nonemitted_control(cp, control, stale, originals)


@pytest.fixture(scope='module')
def native_history(tmp_path_factory):
  cp, _, chain, originals = _original_chain(tmp_path_factory.mktemp('native-holds'), emissions=26)
  controls = list(originals.values())
  # Additional source IDs earned no CAN command; they supply no emission credit.
  controls.extend(SimpleNamespace(**dict(vars(c), control_ns=c.control_ns + 5_000_000)) for c in controls[:24])
  controls = tuple(sorted(controls, key=lambda c: c.control_ns))
  commands = tuple((c.control_ns, c.requested, c.requested) for c in controls)
  return cp, controls, chain, commands


@pytest.mark.parametrize('fault', ('none', 'missing', 'body', 'order', 'renewed', 'projection'))
def test_native_history_counts_real_emissions_and_preserves_each_hold(native_history, fault):
  cp, controls, chain, commands = native_history
  if fault == 'missing':
    chain = chain[:1] + chain[2:]
  elif fault == 'body':
    chain = (chain[0], replace(chain[1], applied=chain[1].applied + .01), *chain[2:])
  elif fault == 'order':
    chain = (chain[0], chain[2], chain[1], *chain[3:])
  elif fault == 'renewed':
    chain = (chain[0], replace(chain[1], apply_ns=chain[1].apply_ns + 1), *chain[2:])
  elif fault == 'projection':
    chain = (replace(chain[0], projection=cast(TorqueProjection, object())), *chain[1:])
  result = check_original_native(cp, controls, chain, commands, require_change=False)
  assert result['qualified'] == (fault == 'none')
  if fault == 'none':
    assert result['original_decoded_emissions'] == 26
    assert len(chain) > 26 and result['candidate_changed_emissions'] == 0


@pytest.mark.parametrize('reason,scored,attempts', (
  ('original_hint_requires_exact_unclipped_native_pair', None, 1),
  ('same_state_controller_checks_only', 0, 1),
  ('same_state_controller_checks_only', 1, len(TRAINING_FRACTIONS)),
  ('same_state_controller_checks_only', 49, len(TRAINING_FRACTIONS)),
  ('same_state_controller_checks_only', 50, len(TRAINING_FRACTIONS)),
  ('candidate_dependent_quality_refusal', None, len(TRAINING_FRACTIONS)),
))
def test_only_original_source_refusal_ends_redundant_strength_search(reason, scored, attempts):
  # Declared search-policy fixture; it supplies no native or road qualification.
  cohort = {'training': {'windows': ('a', 'b', 'c')}, 'heldout': {'windows': ('d', 'e', 'f')}}
  rows = [{'window': key, 'qualified': False, 'numerical': {'reason': reason, 'scored_frames': scored}} for key in ('a', 'b', 'c')]
  proposal = {'values': {'declared': .005}, 'parameter': 'declared', 'delta': .005}
  with patch('openpilot.starpilot.flm.offroad_checks._original_results', return_value=rows) as checker:
    fitted, proof = fit_training_step(None, {'selected_original_windows': []}, cohort, proposal,
                                     {'declared': 0.}, '.', permitted=lambda: True)
  assert fitted is None and proof['selected_fraction'] is None
  assert len(proof['attempts']) == checker.call_count == attempts
  assert proof['heldout_windows_not_evaluated'] == ('d', 'e', 'f')


def test_sparse_numerical_credit_never_replaces_real_native_emissions(native_history):
  cp, controls, chain, commands = native_history
  retained = set(sorted({output.apply_ns for output in chain})[:24])
  fewer = tuple(output for output in chain if output.apply_ns in retained)
  result = check_original_native(cp, controls, fewer, commands, require_change=False)
  assert not result['qualified'] and result['reason'] == 'insufficient_original_native_delivery'
  unchanged = check_original_native(cp, controls, chain, commands, require_change=True)
  assert not unchanged['qualified'], 'A complete unchanged history cannot earn a changed tune'
