"""Explicit optional producer/child integration for independent plant inputs.

The separately versioned feedback/mean path requires complete original witness
and candidate feedback-regime proof. Legacy protocol semantics remain separate.
"""
from dataclasses import asdict, dataclass
from typing import TypedDict, cast

from openpilot.starpilot.flm.source_plant_fit import PROTOCOL


class _EffectObjectiveFields(TypedDict):
  group: str
  names: tuple[str, ...]
  anchor_digest: str
  context_key: str


@dataclass(frozen=True)
class OriginalReplayTemplate:
  """Unverified selected-factory descriptor, never a replay/model certificate.

  Fresh ControllerReplay is intentionally not wrapped in EventControllerReplay:
  it has no original checkpoint yet and correctly starts failed. The archive
  verifier owns checkpoint/record lifecycle and returns the actual verified ECR.
  """
  engine: object
  objective: object
  original_anchor: dict

  def __getattr__(self, name):
    return getattr(self.engine,name)


def replay_data(packet, *, objective, identity, baseline_revision):
  original = packet['original_replay_source']
  frames = tuple(packet['actual_frames'])
  if not frames or original['raw_mode'] is not True:
    raise ValueError('plant original raw controller replay')
  first, last = frames[0]['control_ns'], frames[-1]['control_ns']
  if tuple(packet['replay_prefix']) != (first,last):
    raise ValueError('plant original complete replay prefix')
  from openpilot.starpilot.flm.normalized_trace_archive import normalized_packet,reserve_for_packet
  normalized = normalized_packet(packet)
  reserve_for_packet(packet)
  data = {'version':2 if normalized else 1,'raw_mode':True,
    'cp_bytes':bytes.fromhex(original['cp_bytes_hex']), 'mode':original['mode'],
    'turn_assist':original['turn_assist'], 'manual_baseline':tuple(original['manual_baseline']),
    'manual_source_epoch':original['manual_source_epoch'],
    **({'manual_kp': original['manual_kp']} if 'manual_kp' in original else {}), 'objective':asdict(objective),
    'identity':identity, 'baseline_revision':baseline_revision,'model_revision':original['model_revision'],
    'windows':((first,last),),'checkpoints':tuple((stamp,revision,tuple(tuple(pair) for pair in values)) for stamp,revision,values in original['checkpoints']),
    'frames':frames,'emissions':packet['actual_command_tape']}
  if normalized:
    data['trace_archive'] = packet['trace_archive']
  return data


def _template(payload, factory):
  from openpilot.starpilot.flm.controller_replay import ControllerReplay
  from openpilot.starpilot.flm.event_objective import EffectObjective
  from openpilot.starpilot.flm.full_surface_profile import envelope_from_dict
  from openpilot.starpilot.flm.torque_adapter import Objective
  legacy = Objective(**payload['objective'])
  objective = EffectObjective(**cast(_EffectObjectiveFields,
    {**payload['event_objective'],'names':tuple(payload['event_objective']['names'])}))
  envelope = envelope_from_dict(payload['effect_envelope'])
  envelope.validate()
  if (envelope.trial_anchor != objective.anchor_digest or envelope.trial_names != objective.names or
      envelope.context_key != objective.context_key or envelope.source_epoch != objective.context_key):
    raise ValueError('plant original full anchor/objective')
  engine = ControllerReplay(factory,legacy,identity=payload['identity'],
    baseline_revision=envelope.selected_baseline_revision,model_revision=payload['source_packet']['original_replay_source']['model_revision'])
  engine.raw_mode = True
  return OriginalReplayTemplate(engine,objective,envelope.replay_anchor()), objective, envelope


def _stage_engine(original, cutoff, *, context_packet=None):
  """Use genuine healthy-window checkpoints; no synthesized boundary state."""
  from openpilot.starpilot.flm.controller_replay import ControllerReplay, pack_primitive_snapshot
  from openpilot.starpilot.flm.event_storage import source_windows
  from types import SimpleNamespace
  windows = source_windows(SimpleNamespace(records=original.records,replay_windows=(),_frame_context=original._frame_context))
  checkpoints = dict(original.checkpoints)
  chosen = []
  for start,end in windows:
    if end <= cutoff:
      continue
    # Preserve an original checkpoint BEFORE the cutoff when this same healthy
    # interval crosses it. A 500ms true lag can select a genuinely later command
    # before the next one-second checkpoint; dropping that original prefix
    # would falsely remove its controller/CAN support.
    before = tuple(stamp for stamp in checkpoints if start <= stamp <= cutoff)
    candidates = tuple(stamp for stamp in checkpoints if start <= stamp <= end)
    if candidates:
      chosen.append((max(before) if before else min(candidates),end))
  if not chosen or len(chosen) > 24:
    raise ValueError('plant later original healthy checkpoint unavailable')
  records = tuple(frame for frame in original.records if any(a <= frame.control_ns <= b for a,b in chosen))
  # This is a subset of an already bounded complete inventory, never another
  # extra budget of 12000 frames. It supplies original stage state only.
  if len({frame.control_ns for frame in original.records} | {frame.control_ns for frame in records}) > 12000:
    raise ValueError('plant union replay frame bound')
  if context_packet is not None:
    from openpilot.starpilot.flm.original_context_receipts import verified_replay_subset
    view = verified_replay_subset(context_packet,original,tuple(chosen))
    if view is not None:
      return view
  from openpilot.starpilot.flm.normalized_trace_archive import archive_key
  if archive_key(original) is not None:
    raise ValueError('normalized stage requires complete verified original subset')
  data = {'version':1,'raw_mode':True,'cp_bytes':original.factory.cp.as_builder().to_bytes(),
    'mode':str(original.factory.mode),'turn_assist':original.factory.turn_assist,
    'manual_baseline':original.factory.manual_baseline,'manual_source_epoch':original.factory.manual_source_epoch,
    **({'manual_kp': original.factory.manual_kp} if original.factory.manual_kp is not None else {}),
    'objective':asdict(original.engine.objective),'identity':original.identity,
    'baseline_revision':original.baseline_revision,'model_revision':original.engine.model_revision,
    'windows':tuple(chosen),'checkpoints':tuple((stamp,cp.revision,cp.values) for stamp,cp in original.checkpoints
      if any(a <= stamp <= b for a,b in chosen)),
    'frames':tuple(asdict(frame) for frame in records),'emissions':original.emissions.snapshot(tuple(chosen))}
  return ControllerReplay.from_snapshot(original.factory,original.engine.objective,pack_primitive_snapshot(data),rebuild=False)


def _feedback_summary(evidence,packet,protocol):
  from openpilot.starpilot.flm.candidate_feedback import LEGACY_POLICY,PROOF_VERSION
  body = {'protocol':protocol,'binding_digest':evidence.binding,
    'archive_digest':packet['feedback_observations']['digest'].hex(),'verified_digest':evidence.original.digest,
    'known_rows':len(evidence.original.rows),'context_rows':len(evidence.original.context_only)}
  if evidence.candidate_policy != LEGACY_POLICY:
    body.update(candidate_feedback_policy=evidence.candidate_policy,candidate_feedback_proof_version=PROOF_VERSION)
  return body


def fit_snapshot(payload):
  """Bounded FitProcess child entry; controller and CAN proofs precede OLS."""
  from openpilot.starpilot.flm.controller_replay import factory_from_snapshot
  from openpilot.starpilot.flm.original_context_receipts import verify_original_replay
  from openpilot.starpilot.flm.source_plant_fit import source_binding, experiment_context, prepare, _pose, rejoin_stage, native_output, compact_receipt
  from openpilot.starpilot.flm.event_controller_replay import EventControllerReplay
  from openpilot.starpilot.flm.event_activation import build_activation_plan
  from openpilot.starpilot.flm.event_windows import regroup_whole_events
  from openpilot.starpilot.flm.clipping_quality import event_clipping_quality
  from openpilot.starpilot.flm.event_raw_fit import fit_event_torque
  from openpilot.starpilot.flm.plant_lag import LAGS_NS

  from openpilot.starpilot.flm.feedback_replay import PROTOCOL as FEEDBACK_PROTOCOL,EVENT_VERSION
  feedback_job = payload.get('plant_protocol') == FEEDBACK_PROTOCOL
  from openpilot.starpilot.flm.candidate_feedback import LEGACY_POLICY,policy
  candidate_policy = policy(payload.get('candidate_feedback_policy',LEGACY_POLICY))
  if not feedback_job and 'candidate_feedback_policy' in payload:
    raise ValueError('candidate feedback policy requires explicit source feedback protocol')
  if feedback_job:
    from openpilot.starpilot.flm.primitive_source_memo import independent_primitive_graph
    from openpilot.starpilot.flm.normalized_trace_archive import reserve_for_packet
    payload = independent_primitive_graph(payload,reserved_bytes=reserve_for_packet(payload['source_packet']))
  protocol = FEEDBACK_PROTOCOL if feedback_job else PROTOCOL
  if (payload.get('plant_protocol') != protocol or payload.get('event_version') not in ((EVENT_VERSION,8) if feedback_job else (3,4,5,6)) or
      payload.get('event_operation') not in ('trial','restore')):
    raise ValueError('explicit source plant trial protocol')
  packet = payload['source_packet']
  from openpilot.starpilot.flm.controller_replay import pack_primitive_snapshot,MAX_FIT_RAW_BYTES
  from openpilot.starpilot.flm.torque_adapter import Objective
  from openpilot.starpilot.flm.full_surface_profile import envelope_from_dict
  import pickle
  envelope = envelope_from_dict(payload['effect_envelope'])
  replay = replay_data(packet,objective=Objective(**payload['objective']),identity=payload['identity'],
    baseline_revision=envelope.selected_baseline_revision)
  aggregate_raw = len(pickle.dumps(payload,protocol=5))+len(pickle.dumps(replay,protocol=5))
  if aggregate_raw > MAX_FIT_RAW_BYTES:
    raise ValueError('plant aggregate original raw worker byte bound')
  replay = pack_primitive_snapshot(replay)
  factory,_ = factory_from_snapshot(replay)
  template,objective,envelope = _template(payload,factory)
  actual = verify_original_replay(packet['context_receipts'],template,replay)
  binding = source_binding(packet,actual)
  original_feedback = None
  if feedback_job:
    from openpilot.starpilot.flm.feedback_replay import verify_archive
    from openpilot.starpilot.flm.event_mean_evidence import VERSION as MEAN_VERSION
    operation = payload.get('event_operation')
    if operation == 'trial':
      if payload.get('event_version') != EVENT_VERSION or payload.get('event_mean_version') != MEAN_VERSION:
        raise ValueError('explicit new feedback/mean trial version required')
    elif operation == 'restore':
      if payload.get('event_version') != 8:
        raise ValueError('explicit new feedback restore version required')
      event_clipping_quality(payload)
    else:
      raise ValueError('explicit original feedback operation')
    original_feedback = verify_archive(packet,actual)
  elif 'event_mean_version' in payload or 'feedback_observations' in packet:
    raise ValueError('legacy source packet cannot implicitly adopt feedback policy')
  # Key selection is original epoch metadata, never a fit/readiness counter.
  from openpilot.starpilot.flm.learner_factory import collection_keys
  from openpilot.starpilot.flm.source_plant_runtime import SCHEDULE_VERSION
  schedule = payload.get('fit_schedule')
  registered = tuple(collection_keys(next(iter(actual.original_context_controls.values()))))
  if (type(schedule) is not dict or schedule.get('version') != SCHEDULE_VERSION or
      type(packet.get('capture_epoch')) is not int or packet['capture_epoch'] < 0 or
      schedule.get('epoch') != packet['capture_epoch'] or
      schedule.get('start_ns') != packet['capture_epoch_start_ns'] or
      schedule.get('cutoff_ns') != packet['capture_epoch_cutoff_ns'] or
      not 0 < schedule['start_ns'] < schedule['cutoff_ns'] or
      schedule['cutoff_ns']-schedule['start_ns'] > 100_000_000_000 or
      tuple(tuple(key) for key in schedule.get('registered_keys',())) != registered or
      tuple(schedule.get('objective_key',())) != registered[packet['capture_epoch'] % len(registered)] or
      tuple(payload.get('objective_key',())) != tuple(schedule['objective_key']) or
      any(not schedule['start_ns'] <= row['mono_ns'] < schedule['cutoff_ns'] for row in packet['source_poses'])):
    raise ValueError('original source experiment objective binding')
  if tuple(payload['physical_context']) != experiment_context(packet,binding):
    raise ValueError('plant exact submitted source experiment context')
  prepared = prepare(packet,actual,binding)
  if type(prepared) is dict:
    return {**prepared,'plant_protocol':protocol,'physical_context':payload['physical_context']}
  later = _stage_engine(actual,prepared.identification.cutoff_ns,context_packet=packet['context_receipts'])
  restoring = payload['event_operation'] == 'restore'
  if (restoring and payload['event_version'] not in ((8,) if feedback_job else (4,5,6)) or
      not restoring and payload['event_version'] != (EVENT_VERSION if feedback_job else 3)):
    raise ValueError('plant exact trial/restore declaration')
  engine = EventControllerReplay(later,objective,envelope.replay_anchor(),
    restore_envelope=envelope if restoring else None)
  controls = actual.original_context_controls
  states = {}
  for control in controls.values():
    values = dict(control.replay_inputs)
    state = (float(values['vEgo']),float(values['steeringAngleDeg']))
    if control.state_ns in states and states[control.state_ns] != state:
      raise ValueError('plant changed original state bracket')
    states[control.state_ns] = state
  raw_outputs = packet['context_receipts']['outputs']
  receipt_outputs = {row['control_ns']:native_output(controls[row['control_ns']],row) for row in raw_outputs}
  poses = tuple(_pose(row,states,binding,controls,receipt_outputs,actual) for row in packet['source_poses'] if
    row['mono_ns'] > prepared.identification.cutoff_ns+LAGS_NS[-1])
  outputs = packet['context_receipts']['outputs']
  from openpilot.starpilot.flm.normalized_trace_archive import stored_record
  commands = tuple((stored_record(controls[row['control_ns']]),row) for row in outputs)
  try:
    rows = rejoin_stage(poses,commands,prepared,engine)
  except ValueError as error:
    # A verified physical fit is not a stage/application certificate. Preserve
    # its bounded receipt while the independently strict stage gate declines.
    return {'status':'collecting','reason':'original_later_stage_endpoint_unqualified',
      'detail':str(error)[:512],'profile':None,'validation':None,'plant_protocol':protocol,
      'physical_context':prepared.context,'plant_identification':compact_receipt(prepared.identification)}
  packets = ({'identity':engine.identity,'samples':rows},)
  events = regroup_whole_events(engine,packets,objective)
  if len(events) < 8:
    return {'status':'collecting','reason':'independent_source_later_whole_stage_support',
      'profile':None,'validation':None,'plant_protocol':protocol,
      'plant_identification':compact_receipt(prepared.identification),'stageBlocks':len(events)}
  if restoring:
    from openpilot.starpilot.flm.restore_activation import build_restore_plan
    from openpilot.starpilot.flm.event_restore_fit import fit_event_restore
    plan = build_restore_plan(engine,model=prepared.model,cutoff_ns=prepared.identification.cutoff_ns,
      stage_events=events,physical_context=prepared.model.context,envelope=envelope,restore_version=payload['event_version'])
    engine = EventControllerReplay(later,objective,envelope.replay_anchor(),restore_envelope=envelope,activation_plan=plan)
    if feedback_job:
      from openpilot.starpilot.flm.feedback_replay import attach_feedback_engine
      attach_feedback_engine(engine,original_feedback,candidate_policy=candidate_policy)
    result = fit_event_restore(payload,engine,objective,source_plant=(prepared,events))
    if feedback_job:
      from openpilot.starpilot.flm.feedback_replay import require_feedback_engine
      evidence = require_feedback_engine(engine)
      result = {**result,'feedback_evidence':_feedback_summary(evidence,packet,protocol)}
    return {**result,'plant_protocol':protocol,'physical_context':prepared.context,
      'plant_identification':compact_receipt(prepared.identification),'plant_binding':asdict(binding)}
  plan = build_activation_plan(engine,model=prepared.model,cutoff_ns=prepared.identification.cutoff_ns,
    stage_events=events,physical_context=prepared.model.context,quality_contract=event_clipping_quality(payload))
  engine = EventControllerReplay(later,objective,envelope.replay_anchor(),activation_plan=plan)
  if feedback_job:
    from openpilot.starpilot.flm.feedback_replay import attach_feedback_engine
    attach_feedback_engine(engine,original_feedback,candidate_policy=candidate_policy)
  engine.rebuild_causal_features()
  # The existing caller chooses identifiable coordinates only from actual
  # stage feature support. No calibration/heldout targets enter that choice.
  from openpilot.starpilot.flm.event_fit_factory import _identified
  selected = _identified(engine,events)
  if not selected:
    return {'status':'collecting','reason':'no_identifiable_canonical_coordinates','profile':None,'validation':None,
      'plant_protocol':protocol,'plant_identification':compact_receipt(prepared.identification)}
  if selected != objective.names:
    from openpilot.starpilot.flm.event_objective import EffectObjective
    previous = engine
    envelope = envelope.for_trial(selected)
    objective = EffectObjective(objective.group,selected,envelope.trial_anchor,objective.context_key)
    engine = EventControllerReplay(later,objective,envelope.replay_anchor(),activation_plan=plan)
    if feedback_job:
      attach_feedback_engine(engine,original_feedback,candidate_policy=candidate_policy)
    if engine.reuse_subset_causal_features(previous) is not True:
      engine.rebuild_causal_features()
  payload = {**payload,'effect_envelope':asdict(envelope),'event_objective':asdict(objective),
    'prior':tuple(dict(envelope.prior_coordinates)[name] for name in selected)}
  result = fit_event_torque(payload,engine,objective,source_plant=(prepared,events))
  if feedback_job:
    from openpilot.starpilot.flm.feedback_replay import require_feedback_engine
    evidence = require_feedback_engine(engine)
    result = {**result,'feedback_evidence':_feedback_summary(evidence,packet,protocol)}
  return {**result,'plant_protocol':protocol,'physical_context':prepared.context,
    'plant_identification':compact_receipt(prepared.identification),'plant_binding':asdict(binding)}
