"""Bounded ORIGINAL owner receipts for context replay only.

This archive deliberately precedes healthy collection gates. Nothing here is an
EmissionHistory entry, a plant observation, a profile or a native permission.
Child verification consumes full original bodies, never a declared ready/hash.
"""
from collections import OrderedDict
from dataclasses import asdict, dataclass, fields
import math
import hashlib
import json
import struct

MAX_CONTEXT_RECORDS = 12000
LEGACY_REVISION = 'original-context-owner-receipts-v1'
REVISION = 'original-context-owner-receipts-v1-emission-v2'


def _content_bytes(value):
  def typed(item):
    kind = type(item)
    if item is None or kind in (bool,int,str):
      return [kind.__name__,item]
    if kind is float and math.isfinite(item):
      return ['float64',struct.pack('!d',item).hex()]
    if kind is bytes:
      return ['bytes',item.hex()]
    if kind in (tuple,list):
      return [kind.__name__,[typed(child) for child in item]]
    if kind is dict and all(type(key) is str for key in item):
      return ['dict',[[key,typed(item[key])] for key in sorted(item)]]
    raise ValueError('original context exact primitive body')
  return json.dumps(typed(value),separators=(',',':'),allow_nan=False).encode()


# Equality shortcut only; the original JSON digest/codec remains unchanged.
# Every call rereads all fields into independent type-tagged immutable snapshots.
_FRAME_NAMES = ('control_ns','inputs','selected_command','applied_coordinates','block_id','cohort',
  'desired_physical_accel','healthy','manual','limited','actual_lac_command','frame_sequence',
  'torque_values','centre_values','actual_stage_residual','applied_parameter_names','effect_trace',
  'effect_prepare_ns','controller_domain','desired_kinematic_accel','vehicle_model_anchor','live_applied_coordinates')


class _OriginalFrameFallback(Exception):
  pass


def _original_frame_snapshot(frame, frame_type):
  if (type(frame) is not frame_type or frame_type.__getattribute__ is not object.__getattribute__ or
      tuple(field.name for field in fields(frame_type)) != _FRAME_NAMES):
    raise _OriginalFrameFallback
  # A replaced class descriptor could run callbacks unlike the closed graph.
  namespace = vars(frame_type)
  if '__getattr__' in namespace or any(type(namespace[name]) not in (type(None),bool,int,float,str,bytes,tuple,list,dict)
         for name in _FRAME_NAMES if name in namespace):
    raise _OriginalFrameFallback
  remaining = 4096
  def snapshot(item, depth):
    nonlocal remaining
    remaining -= 1
    if remaining < 0 or depth > 32:
      raise _OriginalFrameFallback
    kind = type(item)
    if item is None:
      return (0,)
    if kind is bool:
      return (1,item)
    if kind is int and item.bit_length() <= 512:
      return (2,item)
    if kind is float and math.isfinite(item):
      return (3,struct.pack('!d',item))
    # ensure_ascii JSON identifies some non-BMP and literal-surrogate strings.
    # Restrict this shortcut; whole-original fallback preserves those aliases.
    if kind is str and len(item) <= 65536 and item.isascii():
      return (4,item)
    if kind is bytes and len(item) <= 65536:
      return (5,item)
    if kind in (tuple,list):
      return (6 if kind is tuple else 7,tuple(snapshot(child,depth+1) for child in item))
    if kind is dict and all(type(key) is str and len(key) <= 65536 and key.isascii() for key in item):
      return (8,tuple((key,snapshot(item[key],depth+1)) for key in sorted(item)))
    raise _OriginalFrameFallback
  return tuple((name,snapshot(getattr(frame,name),0)) for name in _FRAME_NAMES)


def _same_original_frame(left, right):
  from openpilot.starpilot.flm.controller_replay import ReplayFrame
  try:
    left_body = _original_frame_snapshot(left,ReplayFrame)
    right_body = _original_frame_snapshot(right,ReplayFrame)
  except _OriginalFrameFallback:
    # The entire original expression, including left deepcopy/serialization
    # before right deepcopy, is retained for every unsupported graph.
    return _content_bytes(asdict(left)) == _content_bytes(asdict(right))
  return left_body == right_body


class OriginalContextReceipts:
  def __init__(self):
    self.controls = OrderedDict()
    self.outputs = OrderedDict()
    self.failed = False

  def record_control(self, value):
    from openpilot.starpilot.flm.fullrate_collector import ControlEvidence
    if not isinstance(value, ControlEvidence):
      self.failed = True
      return
    prior = self.controls.get(value.control_ns)
    if prior is not None:
      if prior != value:
        self.failed = True
      return
    if self.controls and value.control_ns <= next(reversed(self.controls)):
      self.failed = True
      return
    self.controls[value.control_ns] = value
    while len(self.controls) > MAX_CONTEXT_RECORDS:
      stamp, _ = self.controls.popitem(last=False)
      while self.outputs and next(iter(self.outputs.values())).control_ns <= stamp:
        self.outputs.popitem(last=False)

  def record_output(self, value):
    from openpilot.starpilot.flm.fullrate_collector import AppliedEvidence
    if not isinstance(value, AppliedEvidence):
      self.failed = True
      return
    # Repeated carrier observations may refer to one consumed command. Preserve
    # the original emission, not an invented new command/application instant.
    prior = self.outputs.get(value.apply_ns)
    if prior is not None:
      from dataclasses import replace
      if replace(value, carrier_frame=prior.carrier_frame,
                 carrier_observed_ns=prior.carrier_observed_ns) != prior:
        self.failed = True
      return
    if self.outputs and value.apply_ns <= next(reversed(self.outputs)):
      self.failed = True
      return
    self.outputs[value.apply_ns] = value
    while len(self.outputs) > MAX_CONTEXT_RECORDS:
      self.outputs.popitem(last=False)

  def export(self, frames, *, trace_builder=None):
    if self.failed or not frames:
      raise ValueError('original context archive unavailable')
    stamps = tuple(frame.control_ns for frame in frames)
    if any(stamp not in self.controls for stamp in stamps):
      raise ValueError('original context receipt omitted')
    selected_stamps=set(stamps)
    return {'revision': REVISION, 'qualified': False,
      'controls': tuple(asdict(self.controls[stamp]) if trace_builder is None else trace_builder.record(self.controls[stamp]) for stamp in stamps),
      'outputs': tuple(asdict(value) for value in self.outputs.values() if value.control_ns in selected_stamps)}


def _same_context_frame(left, right, engine):
  from openpilot.starpilot.flm.normalized_trace_archive import TraceRef,record_body
  if type(left.effect_trace) is TraceRef or type(right.effect_trace) is TraceRef:
    return _content_bytes(record_body(left,engine)) == _content_bytes(record_body(right,engine))
  return _same_original_frame(left,right)


def verify(packet, engine, frames):
  """Reconstruct original control frames and decode optional actual CAN bytes.

  Native disallowed/expired and unhealthy states are exclusions retained only as
  boundary context. They never satisfy native evidence or plant admission.
  """
  from openpilot.starpilot.flm.learner_factory import _frame
  from openpilot.starpilot.flm.torque_raw_fit import control_from_dict, output_from_dict
  from openpilot.starpilot.flm.torque_emission_replay import Emission, EmissionDecoder
  from opendbc.car.steering_projection import float32
  if (type(packet) is not dict or set(packet) != {'revision','qualified','controls','outputs'} or
      packet['revision'] not in (LEGACY_REVISION,REVISION) or packet['qualified'] is not False or
      type(packet['controls']) not in (tuple,list) or type(packet['outputs']) not in (tuple,list) or
      len(packet['controls']) != len(frames) or len(frames) > MAX_CONTEXT_RECORDS or
      len(packet['outputs']) > (len(frames) if packet['revision']==LEGACY_REVISION else MAX_CONTEXT_RECORDS)):
    raise ValueError('original context receipt packet')
  from openpilot.starpilot.flm.normalized_trace_archive import archive_of
  controls = tuple(control_from_dict(row, trace_archive=archive_of(engine)) for row in packet['controls'])
  if tuple(row.control_ns for row in controls) != tuple(row.control_ns for row in frames):
    raise ValueError('original context receipt original order/omission')
  native_identity = None
  original_configuration = None
  for control, frame in zip(controls, frames, strict=True):
    baseline = control.baseline_values
    if frame.controller_domain == 'torque' and not engine.factory.manual_baseline:
      if len(baseline) < 7:
        raise ValueError('original native baseline shape')
      # Exact existing torque baseline schema: steer_max, factor/offset/friction,
      # deadzone, derived neg/pos limits, immutable PID curves. Only the actual
      # native-owned tuple may vary; child native receipt checks follow below.
      baseline = (baseline[0],baseline[4],*baseline[7:])
    configuration = (control.generation,control.domain,control.stage_revision,control.response_revision,
      control.stage_schema_revision,control.policy,control.controller_mode,control.turn_assist,
      baseline,control.native_required)
    if original_configuration is None:
      original_configuration = configuration
    flags = (control.enabled, control.manual, control.active, control.valid, control.override,
      control.fault, control.saturated, control.limited, control.model_wobble,
      control.controller_chatter, control.turn_assist, control.native_required, control.native_allowed)
    if (any(type(flag) is not bool for flag in flags) or
        any(type(stamp) is not int for stamp in (control.control_ns,control.state_ns,control.source_receipt_ns,
          control.native_observed_ns,control.native_expires_ns)) or
        not 0 < control.state_ns <= control.control_ns <= control.state_ns+30_000_000 or
        not 0 < control.source_receipt_ns <= control.control_ns <= control.source_receipt_ns+500_000_000 or
        control.identity != engine.identity or control.baseline_revision != engine.baseline_revision or
        control.source_epoch != engine.objective.context_key or
        type(control.generation) is not int or control.generation < 0 or configuration != original_configuration or
        control.controller_revision != engine.original_anchor['controller_revision'] or
        control.controller_mode != str(engine.factory.mode) or control.turn_assist != engine.factory.turn_assist or
        type(control.native_identity) is not str or len(control.native_identity) not in (0,64) or
        any(char not in '0123456789abcdef' for char in control.native_identity) or
        not 0 <= control.native_observed_ns <= control.control_ns or
        not control.native_observed_ns <= control.native_expires_ns <= control.native_observed_ns+200_000_000 or
        not _same_context_frame(_frame(control),frame,engine)):
      raise ValueError('original context selected owner/body/source')
    if not control.native_required and control.native_allowed:
      raise ValueError('original optional native receipt cannot grant permission')
    if control.native_allowed and (len(control.native_identity) != 64 or
        not 0 < control.native_observed_ns <= control.control_ns <= control.native_expires_ns):
      raise ValueError('original context native status/clock inconsistent')
    if native_identity is None and control.native_identity:
      native_identity = control.native_identity
    if control.native_identity and control.native_identity != native_identity:
      raise ValueError('original context native identity changed')
    if control.native_required and frame.healthy and not frame.manual and (not control.native_allowed or len(control.native_identity) != 64 or
        not 0 < control.native_observed_ns <= control.control_ns <= control.native_expires_ns):
      raise ValueError('original healthy context native unavailable')
  by_control = {row.control_ns: row for row in controls}
  outputs = tuple(output_from_dict(row) for row in packet['outputs'])
  if ((packet['revision']==LEGACY_REVISION and len({row.control_ns for row in outputs}) != len(outputs)) or
      len({(row.control_ns,row.apply_ns) for row in outputs}) != len(outputs) or
      len({row.apply_ns for row in outputs}) != len(outputs) or
      any(right.control_ns < left.control_ns or right.apply_ns <= left.apply_ns or
          packet['revision']==LEGACY_REVISION and right.control_ns==left.control_ns
          for left,right in zip(outputs,outputs[1:], strict=False))):
    raise ValueError('original context duplicate actual application')
  decoder = EmissionDecoder(engine.factory.cp)
  for output in outputs:
    control = by_control.get(output.control_ns)
    if (control is None or any(type(flag) is not bool for flag in
        (output.valid,output.active,output.override,output.fault,output.limited,output.native_allowed)) or
        any(type(stamp) is not int for stamp in (output.state_ns,output.control_ns,output.apply_ns)) or
        not control.state_ns <= output.state_ns <= output.apply_ns or
        not output.control_ns <= output.apply_ns <= output.control_ns+100_000_000 or
        not math.isfinite(output.applied) or output.domain != control.domain):
      raise ValueError('original context actual applied owner/clock')
    witness = output.projection
    if witness is None:
      continue  # Original absence is retained, never usable for a plant pair.
    if (witness.family != str(engine.factory.cp.brand) or witness.fingerprint != str(engine.factory.cp.carFingerprint) or
        witness.flags != int(engine.factory.cp.flags) or witness.requested_torque != float32(control.requested) or
        witness.reported_torque != output.applied):
      raise ValueError('original context actual CAN/request source')
    # Decoder verifies actual packet bytes and steer-request state, including
    # inactive packets. No healthy record()/load() or trial EncoderBranch call.
    decoder.check(Emission(output.control_ns,output.state_ns,output.apply_ns,witness,
      control.identity,control.source_epoch,control.baseline_revision,output.valid,output.active,
      output.override,output.fault,output.limited,output.native_allowed,control.native_required,
      control.native_observed_ns,control.native_expires_ns,control.native_identity))
  return {row.control_ns:row for row in controls}


@dataclass(frozen=True)
class OriginalSourceWindow:
  window_id: str
  start_ns: int
  end_ns: int
  before_ns: int
  after_ns: int
  control_ns: tuple[int, ...]


def verified_closed_windows(packet, engine, frames):
  """Original command/source boundaries ONLY; no poses, seed lag or y access.

  Returns (tuple[OriginalSourceWindow,...], dict[int,ControlEvidence]). The
  independent plant caller owns pose membership/common-lag support and its fixed
  first-eight design. This helper confers no model or physical qualification.
  """
  from types import SimpleNamespace
  from openpilot.starpilot.flm.controller_replay import MAX_FRAME_GAP_NS
  from openpilot.starpilot.flm.event_storage import source_windows
  controls = verify(packet,engine,frames)
  if any(right.frame_sequence != left.frame_sequence+1 or
         not 0 < right.control_ns-left.control_ns <= MAX_FRAME_GAP_NS
         for left,right in zip(frames,frames[1:], strict=False)):
    raise ValueError('original context incomplete chronological prefix')
  view = SimpleNamespace(records=frames,replay_windows=(),_frame_context=engine._frame_context)
  indices = {frame.control_ns:index for index,frame in enumerate(frames)}
  if len(indices) != len(frames):
    raise ValueError('original context duplicate frame')
  result = []
  for start,end in source_windows(view):
    first,last = indices[start],indices[end]
    if first == 0 or last+1 == len(frames):
      continue  # Censored prefix/tail are never relabeled complete boundaries.
    stamps = tuple(frame.control_ns for frame in frames[first:last+1])
    before,after = frames[first-1].control_ns,frames[last+1].control_ns
    from openpilot.starpilot.flm.normalized_trace_archive import record_body
    digest = hashlib.sha256(_content_bytes((REVISION,engine.identity,engine.baseline_revision,
      engine.original_anchor,tuple(record_body(controls[stamp],engine) for stamp in (before,*stamps,after))))).hexdigest()
    result.append(OriginalSourceWindow('original-source-window:'+digest,start,end,before,after,stamps))
  return tuple(result),controls


def verify_original_replay(packet, template_engine, replay_bytes):
  """Rebuild actual original context, including nonadmissible boundary ticks.

  template_engine supplies factory/objective/full original anchor and the fresh
  underlying ControllerReplay. It need not falsely mark a fresh owner valid or
  construct an EventControllerReplay before actual checkpoints. Caller counts the union with any
  later stage replay once; this function enforces the original prefix bounds.
  No candidate coordinates, physical target or fitted model is consumed here.
  """
  from openpilot.starpilot.flm.controller_replay import (
    ControllerReplay,ReplayFrame,Snapshot,MAX_CHECKPOINT_NS,MAX_FRAME_GAP_NS,
    _VM_FIELDS,_run,capture_snapshot,snapshot_from_values,restore_snapshot,
    same_float,same_final_command,unpack_primitive_snapshot)
  from openpilot.starpilot.flm.effect_trace import trace_from_dict
  from openpilot.starpilot.flm.event_controller_replay import EventControllerReplay
  from openpilot.starpilot.flm.selected_effects import EffectReplayRecordedSource
  from openpilot.starpilot.flm.clipping_quality import ZERO_REFERENCE_QUALITY
  from openpilot.starpilot.flm.event_storage import source_windows
  from types import SimpleNamespace
  engine = template_engine
  data = unpack_primitive_snapshot(replay_bytes)
  from openpilot.starpilot.flm.normalized_trace_archive import TraceArchive,read_ref,trace_for,archive_key
  normalized = data.get('version') == 2
  if normalized:
    from openpilot.starpilot.flm.primitive_source_memo import independent_primitive_graph
    from openpilot.starpilot.flm.normalized_trace_archive import RESERVED_BYTES
    data = independent_primitive_graph(data,reserved_bytes=RESERVED_BYTES)
    engine.engine.trace_archive = TraceArchive(data['trace_archive'])
  elif 'trace_archive' in data:
    raise ValueError('legacy replay cannot implicitly adopt normalized traces')
  if (data.get('version') != (2 if normalized else 1) or data.get('raw_mode') is not True or
      data['cp_bytes'] != engine.factory.cp.as_builder().to_bytes() or
      data['identity'] != engine.identity or data['baseline_revision'] != engine.baseline_revision or
      data['mode'] != str(engine.factory.mode) or data['turn_assist'] != engine.factory.turn_assist or
      tuple(data.get('manual_baseline',())) != engine.factory.manual_baseline or
      data.get('manual_kp') != engine.factory.manual_kp or
      data.get('manual_source_epoch','') != engine.factory.manual_source_epoch or
      data['objective'] != asdict(engine.engine.objective)):
    raise ValueError('original context actual selected replay factory')
  frames = []
  for body in data['frames']:
    body = dict(body)
    for name in ('inputs','torque_values'):
      body[name] = tuple(tuple(row) for row in body.get(name,()))
    for name in ('applied_coordinates','centre_values','applied_parameter_names','vehicle_model_anchor','live_applied_coordinates'):
      if name in body:
        body[name] = tuple(body[name])
    if body.get('actual_stage_residual') is not None:
      body['actual_stage_residual'] = tuple(body['actual_stage_residual'])
    if body.get('effect_trace') is not None:
      body['effect_trace'] = read_ref(body['effect_trace'],engine.trace_archive) if normalized else trace_from_dict(body['effect_trace'])
    if any(body.get('live_applied_coordinates', ())):
      raise ValueError('live nonzero history requires explicit tracking prefix verifier')
    frames.append(ReplayFrame(**body))
  frames = tuple(frames)
  windows = tuple(data['windows'])
  if (not 1 <= len(frames) <= MAX_CONTEXT_RECORDS or not 1 <= len(windows) <= 24 or
      len(data['checkpoints']) > 600 or
      any(right.frame_sequence != left.frame_sequence+1 or
        not 0 < right.control_ns-left.control_ns <= MAX_FRAME_GAP_NS for left,right in zip(frames,frames[1:], strict=False)) or
      windows != ((frames[0].control_ns,frames[-1].control_ns),)):
    raise ValueError('original context complete prefix replay bound')
  normalized_key = hashlib.sha256(__import__('pickle').dumps(archive_key(engine),protocol=5)).digest() if normalized else None
  controls = verify(packet,engine,frames)
  owner = ControllerReplay(engine.factory,engine.engine.objective,identity=engine.identity,
    baseline_revision=engine.baseline_revision,model_revision=data['model_revision'])
  if normalized:
    owner.trace_archive = engine.trace_archive
  owner.raw_mode = True
  owner.emissions.load(data['emissions'],windows)  # Existing healthy gates unchanged.
  checkpoints = {stamp:Snapshot(revision,tuple(values)) for stamp,revision,values in data['checkpoints']}
  if (len(checkpoints) != len(data['checkpoints']) or frames[0].control_ns not in checkpoints or
      any(type(stamp) is not int or stamp not in controls for stamp in checkpoints)):
    raise ValueError('original context original checkpoint omitted/duplicate')
  previous_sequence = None
  for frame in frames:
    control = controls[frame.control_ns]
    if type(control.snapshot_sequence) is not int or control.snapshot_sequence <= 0:
      raise ValueError('original context owner checkpoint sequence')
    if frame.control_ns in checkpoints:
      if not control.replay_snapshot or previous_sequence is not None and control.snapshot_sequence != previous_sequence+1:
        raise ValueError('original context owner checkpoint receipt omitted/sequence')
      if frame.controller_domain == 'torque_pid':
        from openpilot.starpilot.flm.pid_controller_replay import pid_snapshot_from_values
        expected = pid_snapshot_from_values(control.replay_snapshot)
      else:
        expected = snapshot_from_values(control.replay_snapshot)
      if _content_bytes(asdict(expected)) != _content_bytes(asdict(checkpoints[frame.control_ns])):
        raise ValueError('original context owner checkpoint body/revision')
      previous_sequence = control.snapshot_sequence
    elif control.replay_snapshot or control.snapshot_sequence != previous_sequence:
      raise ValueError('original context undeclared owner checkpoint/sequence')
  last_checkpoint,requests = None,{}
  is_native_torque = engine.factory.cp.lateralTuning.which() == 'torque' and not engine.factory.manual_baseline
  from openpilot.starpilot.flm.runtime_adapter_registry import baseline_values
  if is_native_torque:
    from openpilot.starpilot.flm.native_baseline import checkpoint_native_valid,effective_native_valid,static_values
    native_probe,native_vm = engine.factory.create()
    native_anchor = tuple(native_probe.native_learning_baseline)
    native_static = static_values(native_probe)

  from openpilot.starpilot.flm.native_refinement_wire import FIELD,encode,decode,valid_inputs
  last_compact = None

  def advance_original_native(frame):
    nonlocal last_compact
    compact = dict(frame.inputs).get(FIELD)
    if not is_native_torque:
      if compact is not None:
        raise ValueError('original native receipt on immutable/manual configuration')
      return
    before,after = baseline_values(owner.actual),controls[frame.control_ns].baseline_values
    if before == after:
      # Old initial checkpoints may already contain a full original receipt.
      # Mirroring that SAME receipt adds no state transition or new authority.
      if compact != last_compact and compact != encode(tuple(owner.actual.native_learning_provenance)):
        raise ValueError('original native receipt without actual transition')
      last_compact = compact
      return
    if compact is None or compact == last_compact:
      raise ValueError('original native changed row missing new receipt')
    proof = decode(compact)
    if (_content_bytes(proof.before) != _content_bytes(tuple(before[1:4])) or
        _content_bytes(proof.after) != _content_bytes(tuple(after[1:4])) or
        not owner.last_ns < proof.applied_ns <= frame.control_ns):
      raise ValueError('original context native per-row transition continuity')
    for name,value in zip(('latAccelFactor','latAccelOffset','friction'),proof.after,strict=True):
      setattr(owner.actual.torque_params,name,value)
    owner.actual.pid.set_limits(after[6],after[5])
    def words(ns):
      return float(ns >> 32),float(ns & 0xffffffff)
    owner.actual.native_learning_provenance = (1.,*words(proof.source_ns),*words(proof.applied_ns),
      float(proof.host),proof.dt,float(len(before)),*proof.target,*before,*after)
    if (tuple(owner.actual.native_learning_baseline) != native_anchor or
        static_values(owner.actual) != native_static or
        not checkpoint_native_valid(owner.actual,engine.factory.cp,now_ns=frame.control_ns)):
      raise ValueError('original context native per-row receipt rejected')
    last_compact = compact
  def original_source_matches(frame):
    trace = trace_for(frame,engine)
    if trace is None:
      # The real owner clears automatic effects when unhealthy/manual. The
      # original body and exact baseline controller rerun establish that context;
      # it is never a normalized physical/trial or current-authority row.
      prior = dict(engine.original_anchor['accumulated'])
      return (not frame.healthy or frame.manual) or (not any(prior.values()) and not any(frame.applied_coordinates))
    try:
      from openpilot.starpilot.flm.effect_trace import FullEffectTrace
      if type(trace) is not FullEffectTrace:
        return False
      trace.validate()
      fields = ('cp_sha256','controller_revision','source_epoch','reset_generation',
        'selected_baseline_revision','full_original_anchor','context_key','persistent_source_epoch')
      envelope = trace.envelope
      return (envelope.source_binding() == tuple(engine.original_anchor[name] for name in fields) and
        envelope.manifest_sha256 == engine.original_anchor['manifest_sha256'] and
        envelope.selected_baseline_revision == engine.baseline_revision and
        envelope.source_epoch == engine.objective.context_key and
        type(frame.effect_prepare_ns) is int and 0 < frame.effect_prepare_ns == trace.control_ns <= frame.control_ns)
    except (AttributeError,KeyError,TypeError,ValueError,OverflowError):
      return False
  for frame in frames:
    if not valid_inputs(frame.inputs):
      raise ValueError('original context versioned input budget')
    if not is_native_torque and FIELD in dict(frame.inputs):
      raise ValueError('original native receipt on immutable/manual configuration')
    if last_checkpoint is not None:
      advance_original_native(frame)
    if frame.control_ns in checkpoints:
      if is_native_torque:
        restore_snapshot(native_probe,native_vm,checkpoints[frame.control_ns])
        if (tuple(native_probe.native_learning_baseline) != native_anchor or static_values(native_probe) != native_static or
            not checkpoint_native_valid(native_probe,engine.factory.cp,now_ns=frame.control_ns) or
            baseline_values(native_probe) != controls[frame.control_ns].baseline_values):
          raise ValueError('original context native checkpoint provenance')
        compact = dict(frame.inputs).get(FIELD)
        if compact is not None and compact != encode(tuple(native_probe.native_learning_provenance)):
          raise ValueError('original native compact/checkpoint receipt mismatch')
      if last_checkpoint is not None:
        # VM state is an original current-frame input set before producer
        # capture. No PID/gain/filter/jerk state may silently reset at a saved
        # checkpoint: require the ongoing original rerun to reach it exactly.
        values = dict(frame.inputs)
        for name in _VM_FIELDS:
          setattr(owner.vm,name,values['vm.'+name])
        ongoing = capture_snapshot(owner.actual,owner.vm)
        if _content_bytes(asdict(ongoing)) != _content_bytes(asdict(checkpoints[frame.control_ns])):
          raise ValueError('original context hidden intermediate state reset')
      owner.checkpoint(frame.control_ns,checkpoints[frame.control_ns])
      last_checkpoint = frame.control_ns
      if owner.last_sequence == 0:
        last_compact = dict(frame.inputs).get(FIELD)
    if (last_checkpoint is None or frame.control_ns-last_checkpoint > MAX_CHECKPOINT_NS or
        type(frame.frame_sequence) is not int or frame.frame_sequence <= 0 or
        not math.isfinite(frame.selected_command) or not valid_inputs(frame.inputs) or
        not original_source_matches(frame) or any(frame.applied_coordinates) or any(frame.centre_values) or
        frame.actual_stage_residual is not None and any(frame.actual_stage_residual)):
      raise ValueError('original context original source/checkpoint')
    trace = trace_for(frame,engine)
    source = None if trace is None else EffectReplayRecordedSource(trace)
    trace = None
    output,_ = _run(owner.actual,owner.vm,frame.inputs,owner.objective,frame.applied_coordinates,
      frame.torque_values,actual_stage_residual=frame.actual_stage_residual,effect_trial=source)
    if (not same_final_command(output,frame.selected_command) or
        frame.actual_lac_command is not None and not same_float(output,frame.actual_lac_command)):
      raise ValueError('original context actual Float64/controller output')
    if baseline_values(owner.actual) != controls[frame.control_ns].baseline_values:
      raise ValueError('original context exact current baseline body')
    if is_native_torque and not effective_native_valid(owner.actual,engine.factory.cp):
      raise ValueError('original context actual native parameter envelope')
    if not owner.record_actual_frame(frame):
      raise ValueError('original context actual record lifecycle rejected')
    requests[frame.control_ns] = frame.selected_command
  healthy = source_windows(SimpleNamespace(records=owner.records,replay_windows=(),_frame_context=owner._frame_context))
  for start,end in healthy:
    owner.emissions.rollout(requests,start,end,((start,end),),quality_contract=ZERO_REFERENCE_QUALITY)
  if normalized and hashlib.sha256(__import__('pickle').dumps(archive_key(owner),protocol=5)).digest() != normalized_key:
    raise ValueError('normalized original source changed during actual replay')
  result = EventControllerReplay(owner,engine.objective,engine.original_anchor)
  result.original_context_controls = controls
  result._original_verified_context = _VerifiedOriginalContext(
    _REUSE_OWNER,_verified_context_key(packet,result),tuple(controls.items()))
  return result


# Created only after verify_original_replay completes the ORIGINAL actual
# controller/checkpoint/native lifecycle. This object never crosses classless
# worker IPC and is not a certificate accepted from a caller or saved report.
_REUSE_OWNER = object()


@dataclass(frozen=True)
class _VerifiedOriginalContext:
  owner: object
  key: bytes
  controls: tuple


def _verified_context_key(packet, result):
  """Exact type/bit owned bytes; no identity-only or payload-qualified cache."""
  import hashlib
  import pickle
  from openpilot.starpilot.flm.normalized_trace_archive import archive_key
  from openpilot.starpilot.flm.controller_replay import ControllerReplay, SelectedControllerFactory, capture_snapshot
  from openpilot.starpilot.flm.pid_controller_replay import PidControllerFactory
  from openpilot.starpilot.flm.event_controller_replay import EventControllerReplay
  from openpilot.starpilot.flm.torque_emission_replay import EmissionHistory
  if (type(result) is not EventControllerReplay or type(result.engine) is not ControllerReplay or
      type(result.factory) not in (SelectedControllerFactory,PidControllerFactory) or
      type(result.emissions) is not EmissionHistory or result.failed or not result.raw_mode or
      type(result.original_context_controls) is not dict):
    raise ValueError('original replay reuse requires actual verified owners')
  owner,factory,emissions = result.engine,result.factory,result.emissions
  # Same-byte mutation remains equivalent; any changed frame, receipt, CP,
  # source, checkpoint, window or actual native packet invalidates the reuse.
  selected = (type(factory).__module__,type(factory).__qualname__,
    factory.cp.as_builder().to_bytes(),str(factory.mode),str(getattr(factory,'policy','stock-pid')),factory.turn_assist,
    factory.manual_baseline,factory.manual_source_epoch,
    factory.interface_class.__module__,factory.interface_class.__qualname__)
  native = (emissions.cp.as_builder().to_bytes(),emissions.identity,emissions.baseline_revision,
    tuple(emissions.events.items()),tuple(emissions.carriers.items()))
  value = (packet,owner.failed,owner.raw_mode,owner.identity,owner.baseline_revision,owner.model_revision,
    owner.objective,result.objective,result.original_anchor,selected,
    tuple(owner.records),tuple(owner.checkpoints),tuple(owner.replay_windows),
    owner.last_ns,owner.last_sequence,owner.checkpoint_ns,capture_snapshot(owner.actual,owner.vm),
    tuple(result.original_context_controls.items()),native)
  if archive_key(result) is not None:
    value = (*value,archive_key(result))
  return hashlib.sha256(pickle.dumps(value,protocol=5)).digest()


def reuse_verified_context(packet, result, frames):
  """Reuse only a completed child-local proof after full owned-byte rebinding.

  A missing proof takes the existing verifier path. A changed owned input is
  rejected rather than silently recertified by a cached/native boolean.
  """
  proof = result.__dict__.get('_original_verified_context')
  if proof is None:
    return None
  if (type(proof) is not _VerifiedOriginalContext or proof.owner is not _REUSE_OWNER or
      tuple(frames) != tuple(result.records) or _verified_context_key(packet,result) != proof.key):
    raise ValueError('original verified replay owned bytes changed')
  return dict(proof.controls)


def verified_replay_subset(packet, result, windows: tuple[tuple[int, int], ...]):
  """Child-only exact stage view of already replayed original source/state.

  This avoids serializing and rerunning the SAME original neutral controller a
  second time. It transfers no profile, feature, candidate/native verdict or
  admission. EventControllerReplay still executes every strict trial and CAN
  rollout from the original pre-frame checkpoints.
  """
  from collections import deque, OrderedDict
  from copy import copy
  from openpilot.starpilot.flm.controller_replay import capture_snapshot,restore_snapshot
  from openpilot.starpilot.flm.event_storage import source_windows
  from types import SimpleNamespace
  if reuse_verified_context(packet,result,result.records) is None:
    return None
  owner = result.engine
  frames,checkpoints = tuple(owner.records),dict(owner.checkpoints)
  healthy = source_windows(SimpleNamespace(records=frames,replay_windows=(),_frame_context=owner._frame_context))
  if (type(windows) is not tuple or not 1 <= len(windows) <= 24 or
      any(type(row) is not tuple or len(row) != 2 or row[0] not in checkpoints or row[0] > row[1] or
          i and row[0] <= windows[i-1][1] or
          not any(a <= row[0] <= row[1] <= b for a,b in healthy) for i,row in enumerate(windows))):
    raise ValueError('original verified replay subset windows')
  records = tuple(frame for frame in frames if any(a <= frame.control_ns <= b for a,b in windows))
  if (not records or len(records) > 12000 or records[-1].control_ns != owner.last_ns or
      tuple((next(frame.control_ns for frame in records if frame.control_ns >= a),
        max(frame.control_ns for frame in records if frame.control_ns <= b)) for a,b in windows) != windows):
    # A nonterminal view needs its own actual ending state; retain the existing
    # full reconstruction for that unsupported case, without inventing state.
    return None
  view = copy(owner)  # Preserve the completed real checkpoint/record lifecycle.
  view.records = deque(records,maxlen=owner.records.maxlen)
  view.checkpoints = deque(((stamp,state) for stamp,state in owner.checkpoints
    if any(a <= stamp <= b for a,b in windows)),maxlen=owner.checkpoints.maxlen)
  view.replay_windows = windows
  view.emissions = copy(owner.emissions)
  view.emissions.events = OrderedDict((stamp,event) for stamp,event in owner.emissions.events.items()
    if any(a <= event.control_ns <= b for a,b in windows))
  view.emissions.by_control = {event.control_ns:event for event in view.emissions.events.values()}
  carrier_ranges = []
  for a,b in windows:
    emitted = tuple(event for event in view.emissions.events.values() if a <= event.control_ns <= b)
    if emitted:
      carrier_ranges.append((emitted[0].witness.frame+1,emitted[-1].witness.frame+1))
  view.emissions.carriers = OrderedDict((frame,value) for frame,value in owner.emissions.carriers.items()
    if any(a <= frame <= b for a,b in carrier_ranges))
  # The existing exact-event decoder cache proves ORIGINAL bytes only. Every
  # counterfactual still creates/advances its own EncoderBranch in rollout.
  # Derived current/feature caches are never moved across this stage view.
  view.normalized,view.projected = {},{}
  view.recent_states,view.actual_centre_friction_values = OrderedDict(),OrderedDict()
  view.cached_validation = view.cached_key = None
  # Separate mutable controller objects. The terminal state is the exact state
  # already produced by the original full replay, never a reset/False flag.
  state = capture_snapshot(owner.actual,owner.vm)
  view.actual,view.vm = owner.factory.create()
  # PID.reset uses integer0, while update stores the continuous Float64 output.
  # Existing restore_snapshot preserves an old integer leaf's type. Seed this
  # exact continuously valued leaf before restoring ALL original state fields.
  view.actual.pid.control = float(owner.actual.pid.control)
  restore_snapshot(view.actual,view.vm,state)
  view.probe,view.probe_vm = owner.factory.create()
  return view
