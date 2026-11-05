"""Numerical controller checks on immutable historical input, never a permission."""

import math
from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES as NAMES, RouteSurfaceEffects


def check_candidate(cp, controls, values, *, score_start_ns=0, score_end_ns=2**64 - 1, require_change=True):
  from openpilot.starpilot.flm.controller_replay import (
    SelectedControllerFactory,
    ControllerReplay,
    snapshot_from_values,
    restore_snapshot,
    _run,
  )
  from openpilot.starpilot.flm.learner_factory import _frame
  from openpilot.starpilot.flm.offroad_surface import replay_source
  from openpilot.starpilot.flm.torque_adapter import Objective

  if type(controls) is not tuple or not 1 <= len(controls) <= 12000 or type(values) is not dict or set(values) != set(NAMES):
    raise ValueError('Complete bounded actual controller history and full map required')
  first = controls[0]
  if cp.lateralTuning.which() not in ('torque', 'pid') or str(cp.steerControlType) != 'torque':
    raise ValueError('Registered torque or torque-actuated PID required')
  baseline = first.baseline_revision
  RouteSurfaceEffects({NAMES[0]: values[NAMES[0]]}, baseline, accumulated=values)
  if cp.lateralTuning.which() == 'pid':
    from openpilot.starpilot.flm.pid_controller_replay import PidControllerFactory

    factory = PidControllerFactory(cp, first.controller_mode, turn_assist=first.turn_assist)
  else:
    factory = SelectedControllerFactory(cp, first.controller_mode, turn_assist=first.turn_assist)
  actual = ControllerReplay(
    factory, Objective('feedforward', 'high', 'steady'), identity=first.identity, baseline_revision=baseline, model_revision='historical-numerical-only'
  )
  probe, probe_vm = factory.create()
  verified = scored = changed = checkpoints = 0
  minimum = maximum = 0.0
  commands = []
  last_ns = last_sequence = 0
  for evidence in controls:
    if (
      evidence.identity != first.identity
      or evidence.baseline_revision != baseline
      or evidence.controller_revision != first.controller_revision
      or evidence.controller_mode != first.controller_mode
    ):
      raise ValueError('No cross-context controller reconstruction')
    if evidence.manual or evidence.override or evidence.fault or not evidence.valid:
      raise ValueError('Original controller input is ineligible')
    frame = _frame(evidence)
    gap = bool(last_ns and (frame.control_ns - last_ns > 50_000_000 or frame.frame_sequence != last_sequence + 1))
    if gap or actual.failed:
      # Only the original pre-frame snapshot can start a reconstruction.
      if not evidence.replay_snapshot:
        return {'qualified': False, 'reason': 'original_preframe_snapshot_missing', 'verified_frames': verified}
      actual.checkpoint(frame.control_ns, snapshot_from_values(evidence.replay_snapshot))
      checkpoints += 1
    elif evidence.replay_snapshot:
      actual.checkpoint(frame.control_ns, snapshot_from_values(evidence.replay_snapshot))
      checkpoints += 1
    actual.raw_mode = True
    neutral = actual.normalize(frame, _verify_only=True)
    if neutral is None or not neutral.verified:
      return {'qualified': False, 'reason': 'original_neutral_reconstruction_failed', 'verified_frames': verified, 'failed_original_ns': frame.control_ns}
    before = actual.recent_states[frame.control_ns]
    if not score_start_ns <= frame.control_ns <= score_end_ns:
      verified += 1
      last_ns, last_sequence = frame.control_ns, frame.frame_sequence
      continue
    if evidence.limited or evidence.saturated or not dict(evidence.replay_inputs)['active']:
      return {'qualified': False, 'reason': 'original_scored_frame_not_eligible', 'verified_frames': verified, 'failed_original_ns': frame.control_ns}
    restore_snapshot(probe, probe_vm, before)
    trial = replay_source(values, baseline)
    candidate, _ = _run(probe, probe_vm, frame.inputs, actual.objective, (0.0,) * len(frame.applied_coordinates), frame.torque_values, effect_trial=trial)
    # Existing selected source checks retain per-stage sign/envelopes and limits.
    if not math.isfinite(candidate) or abs(candidate) > probe.steer_max + 1e-12:
      return {'qualified': False, 'reason': 'actual_controller_output_bound', 'verified_frames': verified}
    reference = frame.actual_lac_command
    if reference is None:
      return {'qualified': False, 'reason': 'original_full_precision_controller_output_missing', 'verified_frames': verified}
    # Compare the same Float32 actuator field, never raw double versus Float32.
    from openpilot.starpilot.flm.controller_replay import final_command

    delta = final_command(candidate) - final_command(reference)
    commands.append((frame.control_ns, final_command(reference), final_command(candidate)))
    changed += bool(delta != 0.0)
    verified += 1
    scored += 1
    minimum = min(minimum, delta)
    maximum = max(maximum, delta)
    last_ns, last_sequence = frame.control_ns, frame.frame_sequence
  return {
    'qualified': bool(scored >= 50 and (changed > 0 or not require_change)),
    'reason': 'same_state_controller_checks_only',
    'verified_frames': verified,
    'scored_frames': scored,
    'changed_frames': changed,
    'original_checkpoints': checkpoints,
    'minimum_command_delta': minimum,
    'maximum_command_delta': maximum,
    'commands': tuple(commands),
    'counterfactual_rollout_proven': False,
    'physical_improvement_proven': False,
    'native_encoder_application_proven': False,
    'may_save_automatically': False,
  }


def check_original_native(cp, controls, outputs, commands, *, require_change=True):
  """Decode originals then replay the registered encoder; no new native grant."""
  from openpilot.starpilot.flm.torque_emission_replay import EmissionHistory, delivered

  if (
    type(controls) is not tuple
    or type(outputs) is not tuple
    or type(commands) is not tuple
    or not 50 <= len(controls) <= 12000
    or len(outputs) > 12000
    or len(commands) != len(controls)
    or tuple(row[0] for row in commands) != tuple(c.control_ns for c in controls)
  ):
    raise ValueError('Complete bounded source controller/native history required')
  first = controls[0]
  history = EmissionHistory(cp, identity=first.identity, baseline_revision=first.baseline_revision)
  by_control = {c.control_ns: c for c in controls}
  originals = {}
  for output in outputs:
    control = by_control.get(output.control_ns)
    if control is None:
      continue
    if output.apply_ns in originals:
      if originals[output.apply_ns] != output:
        return {'qualified': False, 'reason': 'conflicting_original_native_delivery'}
      continue
    if not history.record(control, output):
      return {'qualified': False, 'reason': 'original_native_source_not_eligible'}
    originals[output.apply_ns] = output
  if len(originals) < 25:
    return {'qualified': False, 'reason': 'insufficient_original_native_delivery'}
  baseline = {stamp: before for stamp, before, _ in commands}
  candidate = {stamp: after for stamp, _, after in commands}
  start, end = commands[0][0], commands[-1][0]
  try:
    for output in originals.values():
      history.verify(by_control[output.control_ns], output)
    before = history.rollout(baseline, start, end)
    after = history.rollout(candidate, start, end)
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
    return {'qualified': False, 'reason': 'original_encoder_or_candidate_quality_refused', 'detail': str(error)[:160]}
  if set(before) != set(originals) or any(before[stamp] != delivered(originals[stamp]) for stamp in before):
    return {'qualified': False, 'reason': 'original_encoder_reconstruction_mismatch'}
  changed = sum(before[k] != after[k] for k in before)
  return {
    'qualified': bool(changed or not require_change),
    'reason': 'registered_encoder_trial_only' if require_change else 'original_registered_encoder_observed',
    'original_decoded_emissions': len(originals),
    'candidate_changed_emissions': changed,
    'physical_improvement_proven': False,
    'hardware_current_permission': False,
    'full_closed_loop_counterfactual_proven': False,
  }
