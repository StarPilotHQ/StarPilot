"""Raw parent recordings, one bounded child fit, real selected-controller replay."""

from typing import TypedDict, cast


import base64
from openpilot.starpilot.flm.physical_response import request_force_accel
from itertools import islice
from dataclasses import asdict
from openpilot.starpilot.flm.runtime_adapter_registry import (
  selected_descriptor,
  selected_identity,
  baseline_revision,
  baseline_values,
  canonical_physical_context,
  physical_model_revision,
)
from openpilot.starpilot.flm.torque_adapter import Objective
from openpilot.starpilot.flm.controller_replay import (
  SelectedControllerFactory,
  ControllerReplay,
  ReplayFrame,
  snapshot_from_values,
  pack_primitive_snapshot,
  unpack_primitive_snapshot,
  factory_from_snapshot,
)


class _EffectObjectiveFields(TypedDict):
  group: str
  names: tuple[str, ...]
  anchor_digest: str
  context_key: str


RAW_TORQUE = 'torque-selected-stage-snapshot-v1'
FF_KEYS = tuple(('feedforward', speed, phase) for speed in ('low', 'high') for phase in ('steady', 'turn_in', 'unwind'))


def event_capable(evidence):
  return bool(getattr(evidence, 'effect_prepare_ns', 0) > 0 or getattr(evidence, 'effect_trace', None) is not None)


def objective_key(evidence):
  if evidence.stage_schema_revision == 'pid-selected-stage-snapshot-v2':
    from openpilot.starpilot.flm.pid_collection import event_key

    return event_key(evidence)
  if event_capable(evidence):
    from openpilot.starpilot.flm.event_collection import event_key

    return event_key(evidence)
  stage = dict(evidence.stage_values)
  phase = stage['setpoint'] * stage['jerk']
  return ('feedforward', 'low' if stage['speed'] < 25 else 'high', 'steady' if abs(phase) < 0.03 else 'turn_in' if phase > 0 else 'unwind')


def collection_keys(evidence):
  if evidence.stage_schema_revision == 'pid-selected-stage-snapshot-v2':
    from openpilot.starpilot.flm.pid_collection import event_keys

    return event_keys()
  if event_capable(evidence):
    from openpilot.starpilot.flm.event_collection import event_keys

    return event_keys(evidence)
  key = objective_key(evidence)
  # Ten bounded fit metadata entries cover six FF cells and four centre bands;
  # they all share this single primitive recorder and child process.
  return FF_KEYS if key[0] == 'feedforward' else (key,)


def _frame(evidence):
  if getattr(evidence, 'route_effect_revision', ''):
    from dataclasses import replace
    from openpilot.starpilot.flm.route_recording import SCHEMAS, _valid
    from openpilot.starpilot.flm.offroad_surface import REVISION as ROUTE_REVISION

    if evidence.stage_schema_revision not in SCHEMAS or evidence.route_effect_revision != ROUTE_REVISION:
      raise ValueError('Explicit route recording schema required')
    _valid(evidence.route_applied_coordinates, evidence.route_configuration_scope)
    original = replace(
      evidence,
      stage_schema_revision=SCHEMAS[evidence.stage_schema_revision],
      route_applied_coordinates=(),
      route_effect_revision='',
      route_configuration_scope='',
    )
    return replace(
      _frame(original),
      route_applied_coordinates=evidence.route_applied_coordinates,
      route_effect_revision=evidence.route_effect_revision,
      route_configuration_scope=evidence.route_configuration_scope,
    )
  if evidence.stage_schema_revision == 'pid-selected-stage-snapshot-v2':
    from openpilot.starpilot.flm.pid_controller_replay import pid_frame

    return pid_frame(evidence)
  stage = dict(evidence.stage_values)
  return ReplayFrame(
    evidence.control_ns,
    evidence.replay_inputs,
    evidence.requested,
    evidence.applied_coordinates,
    frame_sequence=int(dict(evidence.replay_inputs)['control_frame']),
    actual_lac_command=stage['selected_command'],
    torque_values=tuple((key, stage[key]) for key in ('effective_factor', 'offset', 'friction', 'pid_pos_limit', 'pid_neg_limit')),
    actual_stage_residual=(stage['actual_delta_ff'], stage['actual_delta_threshold']),
    centre_values=(stage.get('centre_threshold_delta', 0.0), stage.get('centre_extra_deadband_deg', 0.0)),
    desired_physical_accel=request_force_accel(evidence.replay_inputs),
    desired_kinematic_accel=stage['setpoint'],
    healthy=evidence.valid and not evidence.fault and not evidence.override,
    manual=evidence.manual,
    limited=evidence.limited,
    applied_parameter_names=tuple(evidence.applied_parameter_names),
    effect_trace=evidence.effect_trace,
    effect_prepare_ns=evidence.effect_prepare_ns,
    vehicle_model_anchor=evidence.vehicle_model_anchor,
    live_applied_coordinates=evidence.live_applied_coordinates,
  )


class TorqueCollectionAdapter:
  command_domain = 'normalized_torque'
  raw_collection = True
  command_family = 'torque'
  raw_stage_revision = RAW_TORQUE
  factory_type = SelectedControllerFactory

  def __init__(self, cp, evidence, key=None, *, source=None, event_capture=False):
    self.event_capture = bool(event_capture)
    if evidence.effect_trace is not None and not self.event_capture:
      raise ValueError('full30 effects require event adapter; not legacy FF')
    if evidence.stage_schema_revision != self.raw_stage_revision:
      raise ValueError('actual raw stage trace')
    manual_baseline = ()
    if source is not None:
      from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest

      if not source.enabled or source.manual or source.source_epoch != evidence.source_epoch or source.cp_sha256 != car_params_digest(cp):
        raise ValueError('independent current manual configuration source')
      manual_baseline = source.manual_baseline
    factory = self.factory_type(
      cp,
      evidence.controller_mode,
      turn_assist=evidence.turn_assist,
      manual_baseline=manual_baseline,
      manual_source_epoch=evidence.source_epoch if manual_baseline else '',
    )
    controller, vm = factory.create()
    from openpilot.starpilot.flm.native_baseline import static_values

    is_pid = self.command_family == 'torque_pid'
    if is_pid and manual_baseline:
      raise ValueError('torque manual tuple cannot configure PID')
    vehicle_constructor_baseline = () if is_pid else tuple(controller.native_learning_baseline)
    constructor_baseline = baseline_values(controller)
    constructor_static = () if is_pid else static_values(controller)
    descriptor = selected_descriptor(cp, factory.interface_class(cp), controller)
    if (
      descriptor is None
      or descriptor.policy != evidence.policy
      or descriptor.controller_revision != evidence.controller_revision
      or selected_identity(descriptor, evidence.source_epoch, evidence.baseline_revision) != evidence.identity
      or baseline_revision(descriptor, constructor_baseline) != evidence.baseline_revision
    ):
      raise ValueError('actual selected constructor identity')
    if evidence.replay_snapshot:
      from openpilot.starpilot.flm.controller_replay import restore_snapshot

      if is_pid:
        from openpilot.starpilot.flm.pid_controller_replay import pid_snapshot_from_values

        restore_snapshot(controller, vm, pid_snapshot_from_values(evidence.replay_snapshot))
      else:
        restore_snapshot(controller, vm, snapshot_from_values(evidence.replay_snapshot))
    from openpilot.starpilot.flm.native_baseline import checkpoint_native_valid

    if is_pid:
      if baseline_values(controller) != constructor_baseline:
        raise ValueError('PID checkpoint altered immutable constructor gains')
    elif (
      tuple(controller.native_learning_baseline) != vehicle_constructor_baseline
      or static_values(controller) != constructor_static
      or (
        bool(baseline_values(controller) != constructor_baseline or tuple(controller.native_learning_provenance))
        if manual_baseline
        else not checkpoint_native_valid(controller, cp, now_ns=evidence.control_ns)
      )
    ):
      raise ValueError('actual native checkpoint provenance')
    self.identity, self.baseline_revision = evidence.identity, evidence.baseline_revision
    self.source_epoch, self.generation = evidence.source_epoch, evidence.generation
    self.factory = factory
    self.objective = Objective('feedforward', 'low', 'steady')
    self.centres = {}
    self.select_objective(key or objective_key(evidence))
    self.replay = ControllerReplay(
      factory,
      Objective('feedforward', 'low', 'steady'),
      identity=evidence.identity,
      baseline_revision=evidence.baseline_revision,
      model_revision='raw-child-v1',
    )
    self.replay.source_controller_revision = descriptor.controller_revision
    self.snapshot_sequence = 0
    self.model = None
    self.model_cutoff_ns = 0
    self.fit_profile = None
    self.current_physical_valid = False
    self.last_physical_ns = 0

  def select_objective(self, key):
    self.key = tuple(key)
    if key not in FF_KEYS:
      raise ValueError('selected FF objective')
    self.objective = Objective(*key)

  def prepare_control(self, evidence):
    if (
      evidence.identity != self.identity
      or evidence.source_epoch != self.source_epoch
      or evidence.generation != self.generation
      or evidence.stage_schema_revision != self.raw_stage_revision
    ):
      return None
    if evidence.replay_snapshot:
      if evidence.snapshot_sequence <= self.snapshot_sequence:
        return None
      if self.command_family == 'torque_pid':
        from openpilot.starpilot.flm.pid_controller_replay import pid_snapshot_from_values

        checkpoint = pid_snapshot_from_values(evidence.replay_snapshot)
      else:
        checkpoint = snapshot_from_values(evidence.replay_snapshot)
      self.replay.checkpoint(evidence.control_ns, checkpoint)
      self.snapshot_sequence = evidence.snapshot_sequence
    if not self.snapshot_sequence or evidence.snapshot_sequence != self.snapshot_sequence:
      return None
    # Primitive recording only. No controller updates, fitting or differences.
    if not self.replay.record_actual_frame(_frame(evidence)):
      return None
    return evidence

  def record_actual_carrier(self, output):
    return self.replay.emissions.record_carrier(output)

  def record_actual_emission(self, control, output):
    return self.replay.emissions.record(control, output)

  def parameters(self):
    return self.objective.parameters

  def prior_schemas(self):
    return (tuple(self.parameters()),)

  def select_prior_schema(self, parameters):
    return tuple(parameters) == tuple(self.parameters())

  def required_cohorts(self):
    if self.key[0] == 'centre':
      return (f'{self.key[1]}.mixed.center',)
    return self.objective.required_cohorts

  def physical_observable(self, value):
    return value.speed_mps >= (2 if self.key[0] == 'centre' else 15) and abs(value.roll_rad) < 0.3

  def cohort(self, control, physical):
    if self.model is not None and 'applied_command' in physical and 'response_ns' in physical:
      applied = -float(physical['applied_command'])
      observed = float(physical['lateral_accel'])
      predicted = self.model.slope * applied + self.model.intercept
      self.current_physical_valid = bool(
        self.model.command_low <= applied <= self.model.command_high and abs(predicted - observed) <= max(0.01, 3 * self.model.heldout_rmse)
      )
      self.last_physical_ns = int(physical['response_ns'])
    else:
      self.current_physical_valid = False
    stage = dict(control.stage_values)
    key = objective_key(control)
    if key[0] == 'centre':
      facade = self.centres.get(key[1])
      return facade.cohort(control, physical) if facade is not None else None
    if abs(stage['setpoint']) < 0.25:
      return None
    side = 'left' if stage['setpoint'] > 0 else 'right'
    return f'{key[1]}.{side}.{key[2]}'

  def raw_maneuver_complete(self, control, physical):
    return bool(
      control.domain == 'normalized_torque'
      and control.stage_schema_revision == RAW_TORQUE
      and control.identity == self.identity
      and control.generation == self.generation
      and control.source_epoch == self.source_epoch
      and control.baseline_revision == self.baseline_revision
      and self.key[0] == 'feedforward'
      and objective_key(control) == self.key
      and abs(dict(control.stage_values)['setpoint']) < 0.25
    )

  def can_seal_raw(self, points):
    return len(points) >= 80

  def fit_job_snapshot(self, blocks, now_ns, sequence, prior, *, control, output, physical_context):
    if self.event_capture:
      raise ValueError('event capture cannot enter legacy FF fitting')
    # Actual independent maneuvers remain whole; no row thinning to fit IPC.
    if self.key[0] == 'centre':
      # Item2 joins FF/PID only. Centre's raw-command physical gates cannot
      # borrow a delivered-encoder model until their separate join is qualified.
      return None
    selected = tuple(block for block in blocks if block.cohort in self.objective.required_cohorts)
    if len(selected) < 16:
      return None
    selected = selected[-24:]
    if any(any(point.control.applied_coordinates) for block in selected for point in block.samples):
      # Refit only genuine neutral-baseline evidence. A retained admitted fit
      # has its separate fresh physical-response/replay renewal path.
      return None
    from opendbc.car.steering_projection import projection_to_dict

    # asdict preserves TorqueProjection's NamedTuple class. Export only its
    # existing validated primitive wire mapping, including every original field.
    block_rows = tuple(asdict(block) for block in selected)
    for block in block_rows:
      for point in block['samples']:
        point['output']['projection'] = projection_to_dict(point['output']['projection'])
        if point['output']['projection'] is None:
          raise ValueError('fit job actual projection')
    output_row = asdict(output)
    output_row['projection'] = projection_to_dict(output.projection)
    if output_row['projection'] is None:
      raise ValueError('fit job current projection')
    payload = {
      'version': 1,
      'family': 'torque',
      'objective': asdict(self.objective),
      'now_ns': now_ns,
      'sequence': sequence,
      'prior': prior,
      'replay': self.replay.to_snapshot(selected),
      'blocks': block_rows,
      'control': asdict(control),
      'output': output_row,
      'physical_context': tuple(physical_context),
    }
    return {'version': 1, 'family': 'torque', 'snapshot_b64': base64.b64encode(pack_primitive_snapshot(payload)).decode('ascii')}

  def install_fit(self, report, result, validation, *, physical_context):
    from openpilot.starpilot.flm.physical_response import PhysicalModel

    if self.key[0] == 'centre':
      facade = self.centres.get(self.key[1])
      return bool(facade is not None and facade.install_fit(report, result, validation, physical_context=physical_context))
    try:
      if result.profile is None or result.profile.identity != self.identity or result.profile.parameters != self.parameters():
        return False
      values = dict(report['model'])
      for field in ('train_blocks', 'heldout_blocks'):
        values[field] = tuple(values[field])
      model = PhysicalModel(**values)
      if (
        model.identity != self.identity
        or model.context != canonical_physical_context(physical_context)
        or physical_model_revision(model) != validation.physical_model_revision
        or validation.baseline_revision != self.baseline_revision
      ):
        return False
      if self.model != model:
        self.current_physical_valid = False
        self.last_physical_ns = 0
      self.model, self.model_cutoff_ns = model, int(report['model_cutoff_ns'])
      self.fit_profile = result.profile
      self.replay.objective = self.objective
      return True
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return False

  def application_witness(self):
    return None

  def current_admission(self, profile, control, output, physical_context):
    if self.event_capture:
      raise ValueError('event capture requires full30 current probe')
    # FourHz current evidence guard. The per-frame parent remains primitive-only.
    from openpilot.starpilot.flm.evidence_noise import noisy_frames

    recent = tuple(
      {'control_ns': frame.control_ns, 'inputs': frame.inputs, 'selected_command': frame.selected_command}
      for frame in reversed(tuple(islice(reversed(self.replay.records), 101)))
    )
    try:
      flags = noisy_frames(recent, 'normalized_torque')
      noisy = any(model or chatter and self.key[0] != 'centre' for model, chatter in flags.values())
    except (KeyError, TypeError, ValueError, OverflowError):
      noisy = True
    if noisy:
      from openpilot.starpilot.flm.adaptive_profile import CurrentAdmission

      return CurrentAdmission(control.identity, control.baseline_revision, '', control.control_ns, False, control.enabled, control.manual, False)
    from openpilot.starpilot.flm.adaptive_profile import CurrentAdmission

    if self.key[0] == 'centre':
      return CurrentAdmission(control.identity, control.baseline_revision, '', control.control_ns, False, control.enabled, control.manual, False)
    model = self.model
    context_ok = bool(
      model is not None
      and model.context == canonical_physical_context(physical_context)
      and self.model_cutoff_ns < control.control_ns <= self.model_cutoff_ns + 600_000_000_000
    )
    self.replay.objective = self.objective
    healthy = bool(
      context_ok
      and self.current_physical_valid
      and 0 < self.last_physical_ns <= output.apply_ns + 500_000_000
      and 0 <= output.apply_ns - self.last_physical_ns <= 100_000_000
      and self.replay.current_probe(profile, control, output, physical_context, model)
    )
    return CurrentAdmission(
      control.identity,
      control.baseline_revision,
      physical_model_revision(model) if model is not None else '',
      min(control.control_ns, output.apply_ns),
      healthy,
      bool(control.enabled),
      bool(control.manual),
      context_ok,
      tuple(control.baseline_values) if healthy else (),
    )


def from_evidence(cp, evidence, key=None, *, source=None, event_replay_version=2, event_clipping_quality='recorded-debt-v1', event_restore_version=2):
  try:
    if cp.notCar or cp.passive or cp.dashcamOnly:
      return None
    key = tuple(key or objective_key(evidence))
    if evidence.stage_schema_revision == 'pid-selected-stage-snapshot-v2':
      if key[0] != 'event':
        return None
      from openpilot.starpilot.flm.pid_collection import PidEventCollectionAdapter

      return PidEventCollectionAdapter(
        cp, evidence, key, source=source, replay_version=event_replay_version, clipping_quality=event_clipping_quality, restore_version=event_restore_version
      )
    if evidence.stage_schema_revision == RAW_TORQUE:
      if key[0] == 'event':
        from openpilot.starpilot.flm.event_collection import EventCollectionAdapter

        return EventCollectionAdapter(
          cp, evidence, key, source=source, replay_version=event_replay_version, clipping_quality=event_clipping_quality, restore_version=event_restore_version
        )
      return TorqueCollectionAdapter(cp, evidence, key, source=source)
    return None  # PID/angle/curvature remain explicitly outside torque learning.
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    return None


def _profile_from_dict(value):
  from openpilot.starpilot.flm.adaptive_profile import Profile, Parameter
  from openpilot.starpilot.flm.full_surface_profile import envelope_from_dict

  return Profile(
    value['identity'],
    value['sequence'],
    value['observed_ns'],
    value['expires_ns'],
    tuple(Parameter(**parameter) for parameter in value['parameters']),
    tuple(value['coefficients']),
    tuple(value['train_ids']),
    tuple(value['heldout_ids']),
    value['baseline_loss'],
    value['candidate_loss'],
    tuple(tuple(row) for row in value['cohort_improvements']),
    value['policy'],
    None if value.get('effect_envelope') is None else envelope_from_dict(value['effect_envelope']),
  )


def decode_fit_result(report):
  from openpilot.starpilot.flm.adaptive_profile import Result, ReplayValidation, replay_accepts

  try:
    if report['profile'] is None:
      return Result(report['status'], report['reason']), None
    profile = _profile_from_dict(report['profile'])
    value = report['validation']
    validation = ReplayValidation(
      _profile_from_dict(value['profile']),
      value['baseline_revision'],
      value['physical_model_revision'],
      value['no_saturation_regression'],
      value['no_slew_regression'],
      value['worst_cohort_improvement'],
    )
    return (Result(report['status'], report['reason'], profile), validation) if replay_accepts(validation, profile) else None
  except (KeyError, TypeError, ValueError, OverflowError):
    return None


def fit_snapshot(snapshot):
  """Fresh child: independent physical fit, stage fit, chronological replay."""
  if snapshot.get('version') != 1 or len(snapshot.get('snapshot_b64', '')) > 6 * 1024 * 1024:
    raise ValueError('fit wrapper bound')
  payload = unpack_primitive_snapshot(base64.b64decode(snapshot['snapshot_b64'], validate=True))
  if payload.get('version') != 1 or payload.get('family') != snapshot.get('family'):
    raise ValueError('fit family')
  if 'plant_protocol' in payload:
    if tuple(snapshot.get('objective_key', ())) != tuple(payload.get('objective_key', ())):
      raise ValueError('source experiment wrapper objective key')
    from openpilot.starpilot.flm.source_plant_pipeline import fit_snapshot as fit_source_plant

    return fit_source_plant(payload)
  event_version = payload.get('event_version')
  event_job = type(event_version) is int and event_version in (2, 3, 4, 5, 6)
  if 'event_version' in payload and not event_job:
    raise ValueError('unknown explicit torque event evidence version')
  from openpilot.starpilot.flm.clipping_quality import event_clipping_quality

  event_clipping_quality(payload)
  if event_version == 3 and payload.get('event_operation') != 'trial':
    raise ValueError('declared activation is an explicit trial experiment only')
  if payload['family'] not in ('torque', 'torque_pid') or payload['family'] == 'torque_pid' and not event_job:
    raise ValueError('only actual torque-actuator event evidence is admitted')
  from openpilot.starpilot.flm.evidence_noise import reject_noisy_job

  retain_chatter = False
  if event_job:
    from openpilot.starpilot.flm.event_objective import EffectObjective

    objective_row = payload['event_objective']
    event_objective = EffectObjective(**cast(_EffectObjectiveFields, {**objective_row, 'names': tuple(objective_row['names'])}))
    # A fixed historical full-map restore is not a friction-only trial. It
    # must retain independent qualification for every active group, including
    # FF; choosing a friction protocol marker cannot bypass its noise gate.
    retain_chatter = payload.get('event_operation') == 'trial' and event_objective.retains_controller_chatter
  reason = reject_noisy_job(payload, retain_centre_chatter=retain_chatter)
  if reason is not None:
    return {'status': 'collecting', 'reason': reason, 'profile': None, 'validation': None}
  factory, _ = factory_from_snapshot(payload['replay'])
  if (factory.cp.lateralTuning.which() == 'pid') != (payload['family'] == 'torque_pid'):
    raise ValueError('actual selected controller family mismatch')
  if event_job:
    from openpilot.starpilot.flm.event_fit_factory import fit_event_snapshot

    return fit_event_snapshot(payload, factory, unpack_primitive_snapshot(payload['replay']))
  objective = Objective(**payload['objective'])
  # Bind the recorded actual stream to the fitting objective only in the child;
  # actual applied residual scalars reproduce any previously selected objective.
  replay_payload = unpack_primitive_snapshot(payload['replay'])
  replay_payload['objective'] = asdict(objective)
  engine = ControllerReplay.from_snapshot(factory, objective, pack_primitive_snapshot(replay_payload))
  from openpilot.starpilot.flm.torque_raw_fit import fit_raw_torque

  return fit_raw_torque(payload, engine, objective)
