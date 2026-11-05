"""Child-only raw torque model/normalization/fit/replay. No heartbeat caller."""

from dataclasses import asdict
from openpilot.starpilot.flm.torque_eligibility import exact_unclipped_pair
from openpilot.starpilot.flm.torque_emission_replay import delivered, projection
from openpilot.starpilot.flm.physical_response import PhysicalRow, fit_physical
from openpilot.starpilot.flm.adaptive_profile import Block, Sample, evaluate
from openpilot.starpilot.flm.fullrate_collector import ControlEvidence, AppliedEvidence
from openpilot.starpilot.flm.runtime_adapter_registry import canonical_physical_context, physical_model_revision


def control_from_dict(value, *, trace_archive=None):
  value = dict(value)
  for key in ('features', 'applied_coordinates', 'applied_parameter_names', 'baseline_values', 'vehicle_model_anchor', 'live_applied_coordinates'):
    if key in value:
      value[key] = tuple(value[key])
  for key in ('stage_values', 'replay_inputs', 'replay_snapshot', 'route_applied_coordinates'):
    if key in value:
      value[key] = tuple(tuple(row) for row in value[key])
  if value.get('effect_trace') is not None:
    from openpilot.starpilot.flm.effect_trace import trace_from_dict

    if trace_archive is None:
      value['effect_trace'] = trace_from_dict(value['effect_trace'])
    else:
      from openpilot.starpilot.flm.normalized_trace_archive import read_ref

      value['effect_trace'] = read_ref(value['effect_trace'], trace_archive)
  control = ControlEvidence(**value)
  import math
  from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, coordinate_bounds

  if (
    type(control.vehicle_model_anchor) is not tuple
    or len(control.vehicle_model_anchor) not in (0, 3)
    or any(type(v) is not float or not math.isfinite(v) or v == 0.0 for v in control.vehicle_model_anchor)
    or type(control.live_applied_coordinates) is not tuple
    or len(control.live_applied_coordinates) not in (0, 30)
  ):
    raise ValueError('explicit actual live source metadata shape')
  if control.live_applied_coordinates:
    if control.effect_trace is not None:
      raise ValueError('distinct live and replay effect sources')
    for name, coordinate in zip(NAMES, control.live_applied_coordinates, strict=True):
      low, high = coordinate_bounds(name)
      if type(coordinate) not in (int, float) or not math.isfinite(coordinate) or not low <= coordinate <= high:
        raise ValueError('actual live coordinate domain')
  return control


def output_from_dict(value):
  value = dict(value)
  value['projection'] = projection(value['projection'])
  return AppliedEvidence(**value)


def fit_raw_torque(payload, engine, objective):
  # Engine.from_snapshot already recomputed each actual controller frame and
  # finite-difference features in this child. Saved normalized flags never used.
  raw = payload.get('raw_blocks', payload.get('blocks'))
  if not 16 <= len(raw) <= 24:
    raise ValueError('raw model/stage block bound')
  context = canonical_physical_context(payload['physical_context'])
  lag = payload['physical_context'][-1]
  rows = []
  model = None
  cutoff = 0
  split = None
  previous = 0
  ids = set()
  for index, block in enumerate(raw):
    if block['identity'] != engine.identity or block['block_id'] in ids or not 40 <= len(block['samples']) <= 240:
      raise ValueError('raw block provenance')
    ids.add(block['block_id'])
    if index >= len(raw) - 8:
      break
    for point in block['samples']:
      if canonical_physical_context(point['physical_context']) != context or point['mono_ns'] <= previous:
        raise ValueError('raw response context/order')
      previous = point['mono_ns']
      command = control_from_dict(point['control'])
      output = output_from_dict(point['output'])
      if (
        command.identity != engine.identity
        or not command.enabled
        or command.manual
        or not command.active
        or not command.valid
        or command.override
        or command.fault
        or command.saturated
        or command.limited
        and not exact_unclipped_pair(command, output)
        or command.model_wobble
        or command.controller_chatter
        or output.control_ns != command.control_ns
        or point['apply_ns'] != output.apply_ns
        or point['control_ns'] != command.control_ns
        or not output.valid
        or not output.active
        or output.override
        or output.fault
        or output.limited
        or not output.native_allowed
      ):
        raise ValueError('raw consumed command')
      applied = delivered(output)
      if applied != point['physical']['applied_command'] or not engine.applied_matches(command, output):
        raise ValueError('actual torque delivered model coordinate')
      oriented = -applied
      rows.append(
        PhysicalRow(
          block['block_id'],
          output.apply_ns,
          point['mono_ns'],
          oriented,
          point['physical']['lateral_accel'],
          context,
          'left' if oriented > 0 else 'right',
          True,
          False,
          False,
        )
      )
    if index >= 7:
      fitted = fit_physical(engine.identity, 'normalized_torque', context, tuple(rows), lag_ns=lag, min_excitation=0.05, max_rmse=0.05, now_ns=point['mono_ns'])
      if fitted is not None:
        model = fitted
        cutoff = max(row.response_ns for row in rows)
        split = index + 1
        break
  if model is None:
    return {'status': 'collecting', 'reason': 'independent_physical_model', 'profile': None, 'validation': None}
  blocks = []
  previous = cutoff
  from openpilot.starpilot.flm.evidence_phases import stage_blocks

  stage, boundary_ids = stage_blocks(raw, split, cutoff)
  for rawblock in stage:
    if rawblock['cohort'] not in objective.required_cohorts:
      continue
    samples = []
    for point in rawblock['samples']:
      command = control_from_dict(point['control'])
      output = output_from_dict(point['output'])
      if (
        command.identity != engine.identity
        or not command.enabled
        or command.manual
        or not command.active
        or not command.valid
        or command.override
        or command.fault
        or command.saturated
        or command.limited
        and not exact_unclipped_pair(command, output)
        or command.model_wobble
        or command.controller_chatter
        or not output.valid
        or not output.active
        or output.override
        or output.fault
        or output.limited
        or not output.native_allowed
        or output.control_ns != command.control_ns
        or command.control_ns <= cutoff
        or point['mono_ns'] <= previous
        or canonical_physical_context(point['physical_context']) != context
      ):
        raise ValueError('model/stage overlap or context')
      previous = point['mono_ns']
      trace = engine.projected.get(output.apply_ns)
      if trace is None or model.inverse(point['physical']['lateral_accel']) is None:
        raise ValueError('actual stage/model envelope')
      actual = (
        engine.frame_for_control(command.control_ns)
        if hasattr(engine, 'frame_for_control')
        else next((f for f in engine.records if f.control_ns == command.control_ns), None)
      )
      if actual is None or actual.desired_physical_accel is None:
        raise ValueError('actual physical goal unavailable')
      target = model.inverse(actual.desired_physical_accel)
      if target is None:
        raise ValueError('physical desired command envelope')
      if not engine.applied_matches(command, output) or delivered(output) != point['physical']['applied_command']:
        raise ValueError('actual torque stage emission projection')
      samples.append(Sample(point['mono_ns'], command.control_ns, output.apply_ns, trace[1], -target - trace[0], 1.0))
    blocks.append(
      Block(rawblock['identity'], rawblock['block_id'], rawblock['cohort'], rawblock['stage_revision'], rawblock['response_revision'], tuple(samples))
    )
  if len(blocks) < 8:
    return {'status': 'collecting', 'reason': 'disjoint_stage_maneuvers', 'profile': None, 'validation': None, 'boundary_block_ids': boundary_ids}
  prior = None if payload.get('prior') is None else tuple(payload['prior'])
  result = evaluate(
    engine.identity,
    objective.parameters,
    tuple(blocks),
    now_ns=payload['now_ns'],
    sequence=payload['sequence'],
    response_revision=blocks[0].response_revision,
    required_cohorts=objective.required_cohorts,
    prior=prior,
  )
  validation = None
  if result.profile is not None:
    command = control_from_dict(payload['control'])
    output = output_from_dict(payload['output'])
    validation = engine.validate(result.profile, command, output, tuple(payload['physical_context']), tuple(blocks), model)
    if validation is None or validation.physical_model_revision != physical_model_revision(model):
      return {'status': 'rejected', 'reason': 'actual_controller_replay', 'profile': None, 'validation': None}
  return {
    'status': result.status,
    'reason': result.reason,
    'profile': None if result.profile is None else asdict(result.profile),
    'validation': None if validation is None else asdict(validation),
    'model': asdict(model),
    'model_cutoff_ns': cutoff,
    'objective_key': payload.get('objective_key'),
    'boundary_block_ids': boundary_ids,
  }
