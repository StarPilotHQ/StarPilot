"""Versioned observations of Controls' real consumed CO feedback.

Capture only; no native/current/profile authority. Original source and native
replay must independently verify every apply before creating a verified tape.
"""
from dataclasses import asdict, dataclass, replace, fields, Field, _FIELD
import math
import re
import struct
import uuid
from types import GetSetDescriptorType
from capnp.lib.capnp import _DynamicStructReader

PROTOCOL = 'original-consumed-co-feedback-v1'
MAX_ROWS = 12000
MAX_AGE_NS = 100_000_000
_VERIFIED = object()
_LOWER_HEX = re.compile('[0-9a-f]*')


def f32(value):
  if type(value) not in (float, int) or not math.isfinite(value):
    raise ValueError('finite feedback operand')
  return struct.unpack('<f', struct.pack('<f', value))[0]


def _positive(value):
  return type(value) is int and 0 < value < 2**64


def _hex(value, size=64):
  return type(value) is str and len(value) == size and _LOWER_HEX.fullmatch(value) is not None


def _digest(value):
  from openpilot.starpilot.flm.physical_input_observation import body_digest
  return body_digest(value)


def co_body(header_ns, valid, actuators):
  """Entire semantic CO body; preserve all actuator fields and float bits."""
  if type(header_ns) is not int or not 0 <= header_ns < 2**64 or type(valid) is not bool or type(actuators) is not dict:
    raise ValueError('actual CO body')
  return _digest({'header_ns':header_ns, 'valid':valid, 'actuators':actuators})


def applied_digest(applied):
  from openpilot.starpilot.flm.fullrate_collector import AppliedEvidence
  from opendbc.car.steering_projection import TorqueProjection, projection_to_dict
  if type(applied) is not AppliedEvidence or type(applied.projection) is not TorqueProjection:
    raise ValueError('exact typed held applied evidence')
  witness = applied.projection
  projection = projection_to_dict(witness)
  if projection is None:
    raise ValueError('bounded actual projection body')
  body = {field.name: getattr(applied, field.name) for field in fields(applied) if field.name != 'projection'}
  # Validated primitive projections need no discarded recursive copy. Preserve
  # original deepcopy behavior for custom operands or limit-name callbacks.
  if (applied.projection is not witness or any(type(row[0]) is not str for row in witness.limits) or
      any(value is not None and type(value) not in (bool, int, float, str, bytes) for value in body.values())):
    body = asdict(applied)
  body['projection'] = projection
  return _digest(body)


@dataclass(frozen=True)
class CoPublication:
  version: int
  session: str
  sequence: int
  publication_ns: int
  source_epoch: str
  cp_sha256: str
  output_known: bool
  output_control_ns: int
  output_state_ns: int
  output_apply_ns: int
  output_carrier_sequence: int
  reported_torque: float
  valid: bool
  co_body_sha256: str
  applied_sha256: str
  native_identity: str
  carrier_observed_ns: int


@dataclass(frozen=True)
class CoConsumption:
  version: int
  control_ns: int
  publication_ns: int
  recv_frame: int
  receipt_ns: int
  seen: bool
  valid: bool
  co_body_sha256: str
  reported_torque: float
  requested_torque: float
  lat_active: bool
  input_limited: bool
  published_limited: bool
  prior_publish_ns: int
  source_epoch: str
  cp_sha256: str


def validate_publication(row):
  if type(row) is not CoPublication or type(row.version) is not int or row.version != 1:
    raise ValueError('typed feedback publication version')
  if (not _hex(row.session, 32) or not all(_positive(v) for v in (row.sequence,row.publication_ns)) or
      not all(_hex(v) for v in (row.source_epoch,row.cp_sha256,row.co_body_sha256)) or
      not (row.native_identity == '' or _hex(row.native_identity)) or
      any(type(v) is not bool for v in (row.output_known,row.valid)) or f32(row.reported_torque) != row.reported_torque):
    raise ValueError('feedback publication fields')
  if row.output_known:
    if (not all(_positive(v) for v in (row.output_control_ns,row.output_state_ns,row.output_apply_ns,row.output_carrier_sequence,row.carrier_observed_ns)) or
        not row.output_state_ns <= row.output_apply_ns <= row.carrier_observed_ns <= row.publication_ns or
        row.carrier_observed_ns > row.output_apply_ns+MAX_AGE_NS or
        not row.output_control_ns <= row.output_apply_ns or not _hex(row.applied_sha256)):
      raise ValueError('feedback known actual apply')
  elif (row.output_control_ns,row.output_state_ns,row.output_apply_ns,row.output_carrier_sequence,row.applied_sha256,row.carrier_observed_ns) != (0,0,0,0,'',0):
    raise ValueError('unknown output cannot claim an apply')
  return row


def validate_consumption(row):
  if type(row) is not CoConsumption or type(row.version) is not int or row.version != 1:
    raise ValueError('typed feedback consumption version')
  if (not _positive(row.control_ns) or
      any(type(v) is not int or not 0 <= v < 2**64 for v in (row.publication_ns,row.recv_frame,row.receipt_ns)) or
      row.seen and (not all(_positive(v) for v in (row.publication_ns,row.recv_frame,row.receipt_ns)) or
        not row.publication_ns <= row.receipt_ns <= row.control_ns <= row.publication_ns+MAX_AGE_NS) or
      type(row.prior_publish_ns) is not int or not 0 <= row.prior_publish_ns < row.control_ns or
      any(type(v) is not bool for v in (row.seen,row.valid,row.lat_active,row.input_limited,row.published_limited)) or
      not all(_hex(v) for v in (row.co_body_sha256,row.source_epoch,row.cp_sha256)) or
      any(f32(v) != v for v in (row.reported_torque,row.requested_torque))):
    raise ValueError('feedback consumption fields/clock')
  expected = bool(row.lat_active and abs(row.requested_torque-row.reported_torque) > .01)
  if row.published_limited is not expected:
    raise ValueError('original published feedback equation')
  return row


class CardFeedbackCapture:
  """Owner-local association, invalidated BEFORE every output setter."""
  def __init__(self, source_epoch, cp_sha256):
    if not _hex(source_epoch) or not _hex(cp_sha256):
      raise ValueError('feedback source context')
    self.source_epoch,self.cp_sha256 = source_epoch,cp_sha256
    self.session = uuid.uuid4().hex
    self.sequence = self.last_header = 0
    self.output = self.applied = None
    self.assignment_serial = self.associated_serial = 0

  def begin_assignment(self):
    self.assignment_serial += 1
    self.invalidate()

  def invalidate(self):
    self.output = self.applied = None
    self.associated_serial = 0

  def committed(self, actual, applied):
    from openpilot.starpilot.flm.fullrate_collector import AppliedEvidence
    from opendbc.car.steering_projection import TorqueProjection
    # This is an observation association, never an applied/native verifier.
    if (self.assignment_serial <= 0 or type(applied) is not AppliedEvidence or applied.domain != 'normalized_torque' or
        type(applied.projection) is not TorqueProjection or
        f32(float(actual.torque)) != f32(applied.applied) or f32(applied.projection.reported_torque) != f32(applied.applied)):
      self.invalidate()
      return
    self.output,self.applied = actual,applied
    self.associated_serial = self.assignment_serial

  def publication(self, event):
    header = int(event.logMonoTime)
    if header <= self.last_header:
      self.invalidate()
      raise ValueError('nonincreasing CO owner publication')
    self.last_header = header
    self.sequence += 1
    actual = event.carOutput.actuatorsOutput
    # Object identity at setter establishes ownership; body equality only checks
    # it survived assignment, never discovers which apply supplied this output.
    applied = self.applied
    known = self.output is not None and applied is not None and self.associated_serial == self.assignment_serial
    actual_body = actual.to_dict()
    if known and actual_body != self.output.to_dict():
      self.invalidate()
      raise ValueError('held owner output changed before CO publication')
    row = CoPublication(1,self.session,self.sequence,header,self.source_epoch,self.cp_sha256,known,
      0 if not known else applied.control_ns,0 if not known else applied.state_ns,
      0 if not known else applied.apply_ns,0 if not known else applied.carrier_frame,
      f32(float(actual.torque)),bool(event.valid),co_body(header,bool(event.valid),actual_body),
      '' if not known else applied_digest(applied),'',0 if not known else applied.carrier_observed_ns)
    return validate_publication(row)


def card_capture_for(owner, now_ns):
  """Only actual enabled/current settings receipt enables observational work."""
  from types import SimpleNamespace
  from openpilot.starpilot.flm.learning_wire import read_source_receipt
  from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
  sm = owner.sm
  try:
    # This service is optional for native authority; all_checks deliberately
    # ignores it there. Observational capture must check its REAL bits directly.
    service = 'autoSteeringLearning'
    stamp = int(sm.logMonoTime[service])
    receipt = int(round(sm.recv_time[service]*1e9))
    if (type(now_ns) is not int or not now_ns > 0 or
        not all((sm.seen[service],sm.valid[service],sm.alive[service],sm.freq_ok[service])) or
        not 0 < stamp <= now_ns <= stamp+500_000_000 or
        not 0 < receipt <= now_ns <= receipt+500_000_000):
      raise ValueError('feedback settings source/receipt invalid or stale')
    digest = getattr(owner,'_feedback_cp_sha256',None)
    if digest is None:
      digest = owner._feedback_cp_sha256 = car_params_digest(owner.CP)
    packet = sm[service]
    cached = getattr(owner, '_feedback_source', None)
    if cached is not None and cached[0] is packet and cached[1].cp_sha256 == digest:
      source = cached[1]
      if not source.observed_ns <= now_ns <= source.expires_ns:
        raise ValueError('feedback settings source expired')
    else:
      source = read_source_receipt(SimpleNamespace(valid=True, autoSteeringLearning=packet), now_ns=now_ns, cp_sha256=digest)
      # SubMaster keeps the same immutable reader until it receives another packet.
      owner._feedback_source = (packet, source) if source is not None and type(packet) is _DynamicStructReader else None
    if source is None or not source.enabled or source.manual:
      owner.learning_feedback_capture = None
      return None
    capture = getattr(owner,'learning_feedback_capture',None)
    if type(capture) is not CardFeedbackCapture or (capture.source_epoch,capture.cp_sha256) != (source.source_epoch,digest):
      capture = owner.learning_feedback_capture = CardFeedbackCapture(source.source_epoch,digest)
    return capture
  except (AttributeError,KeyError,TypeError,ValueError,OverflowError):
    owner._feedback_source = None
    owner.learning_feedback_capture = None
    return None


def capture_controls(owner, cc, *, control_ns, input_limited):
  """Freeze actual consumption only; no association guessed by torque equality."""
  automatic = getattr(owner,'global_learning',None)
  source = None if automatic is None else automatic.source
  if (automatic is None or not automatic.eligible or source is None or not source.enabled or source.manual or
      not source.observed_ns <= control_ns <= source.expires_ns or str(owner.CP.steerControlType) != 'torque'):
    owner.learning_feedback_prior_publish_ns = 0
    return None
  context = (source.source_epoch,source.cp_sha256)
  if getattr(owner,'learning_feedback_context',None) != context:
    owner.learning_feedback_prior_publish_ns = 0
    owner.learning_feedback_context = context
  sm = owner.sm
  header = int(sm.logMonoTime['carOutput'])
  output = sm['carOutput'].actuatorsOutput
  row = CoConsumption(1,control_ns,header,int(sm.recv_frame['carOutput']),int(round(sm.recv_time['carOutput']*1e9)),
    bool(sm.seen['carOutput']),bool(sm.valid['carOutput']),co_body(header,bool(sm.valid['carOutput']),output.to_dict()),
    f32(float(output.torque)),f32(float(cc.actuators.torque)),bool(cc.latActive),bool(input_limited),
    bool(owner.steer_limited_by_safety),int(getattr(owner,'learning_feedback_prior_publish_ns',0)),
    source.source_epoch,source.cp_sha256)
  owner.learning_feedback_prior_publish_ns = control_ns
  return validate_consumption(row)


@dataclass(frozen=True)
class FeedbackJoin:
  consumption: CoConsumption
  publication: CoPublication


@dataclass(frozen=True)
class VerifiedFeedbackTape:
  """Child-local result. Never decode this type from an untrusted wire."""
  protocol: str
  rows: tuple[FeedbackJoin, ...]
  binding_digest: str
  digest: str
  context_only: tuple[CoConsumption, ...]
  parent_digest: str
  _seal: object


def join_original(publications, consumptions):
  """Pure bounded exact join. NOT independent native/source execution proof."""
  if (type(publications) is not tuple or type(consumptions) is not tuple or
      not 1 <= len(publications) <= MAX_ROWS or not 1 <= len(consumptions) <= MAX_ROWS):
    raise ValueError('bounded complete feedback tape')
  by_header = {}
  prior = None
  for pub in publications:
    validate_publication(pub)
    if pub.publication_ns in by_header or prior is not None and (
        pub.session != prior.session or pub.sequence <= prior.sequence or pub.publication_ns <= prior.publication_ns or
        (pub.source_epoch,pub.cp_sha256) != (prior.source_epoch,prior.cp_sha256)):
      raise ValueError('ambiguous/reordered/reset CO publication')
    if prior is not None and pub.output_known and prior.output_known and pub.output_apply_ns == prior.output_apply_ns and (
        pub.output_carrier_sequence < prior.output_carrier_sequence or pub.carrier_observed_ns < prior.carrier_observed_ns):
      raise ValueError('held publication carrier chronology')
    by_header[pub.publication_ns] = pub
    prior = pub
  rows = []
  previous = None
  for row in consumptions:
    validate_consumption(row)
    if not row.seen or not row.valid:
      raise ValueError('unseen/invalid consumed output is observation only')
    pub = by_header.get(row.publication_ns)
    if pub is None or not pub.output_known or (
        pub.co_body_sha256,pub.reported_torque,pub.valid,pub.source_epoch,pub.cp_sha256) != (
        row.co_body_sha256,row.reported_torque,row.valid,row.source_epoch,row.cp_sha256):
      raise ValueError('exact consumed CO/apply provenance unavailable')
    if previous is not None and (row.control_ns <= previous.control_ns or
        row.recv_frame < previous.recv_frame or row.publication_ns < previous.publication_ns or
        row.prior_publish_ns != previous.control_ns or row.input_limited is not previous.published_limited):
      raise ValueError('original publish to next-controller feedback linkage')
    rows.append(FeedbackJoin(row,pub))
    previous = row
  return tuple(rows)


def _plain_record_values(item, kind):
  """Read only a plain exact class's complete owned, callback-free dictionary."""
  if type(item) is not kind or type(kind) is not type or kind.__bases__ != (object,):
    return None
  namespace = kind.__dict__
  descriptor = namespace.get('__dict__')
  metadata = namespace.get('__dataclass_fields__')
  if ('__getattribute__' in namespace or '__getattr__' in namespace or
      type(descriptor) is not GetSetDescriptorType or descriptor.__objclass__ is not kind or
      type(metadata) is not dict):
    return None
  declared = []
  for name, field in metadata.items():
    if (type(name) is not str or type(field) is not Field or type(field.name) is not str or field.name != name or
        field._field_type is not _FIELD or name in namespace):
      return None
    declared.append(name)
  owned = object.__getattribute__(item, '__dict__')
  if (type(owned) is not dict or any(type(key) is not str for key in owned) or
      any(name not in owned for name in declared)):
    return None
  return tuple((name, owned[name]) for name in declared)


def _feedback_asdict(record):
  """Fresh complete record values; unknown shapes retain whole asdict order."""
  def flat(item, kind):
    values = _plain_record_values(item, kind)
    if values is None:
      return None
    body = {}
    for name, value in values:
      if value is not None and type(value) not in (bool, int, float, str, bytes):
        return None
      body[name] = value
    return body

  kind = type(record)
  if kind is CoPublication or kind is CoConsumption:
    body = flat(record, kind)
    if body is not None:
      return body
  elif kind is FeedbackJoin:
    joined = _plain_record_values(record, FeedbackJoin)
    if joined is not None and tuple(name for name, _ in joined) == ('consumption', 'publication'):
      consumption = flat(joined[0][1], CoConsumption)
      if consumption is not None:
        publication = flat(joined[1][1], CoPublication)
        if publication is not None:
          return {'consumption': consumption, 'publication': publication}
  return asdict(record)


def _tape_digest_uncached(rows, context_only, binding_digest, parent_digest):
  return _digest({'binding':binding_digest,'parent':parent_digest,
    'rows':[_feedback_asdict(row) for row in rows],'context_only':[_feedback_asdict(row) for row in context_only]})


# Child-local cost cache only. Fresh exact full bytes are required on EVERY hit.
from collections import OrderedDict
_TAPE_DIGEST_CACHE=OrderedDict()
_TAPE_DIGEST_CACHE_BYTES=0
_TAPE_DIGEST_MAX_ENTRIES=4
_TAPE_DIGEST_MAX_KEY=4*1024*1024
_TAPE_DIGEST_MAX_BYTES=8*1024*1024


def _tape_digest_key(rows,context_only,binding_digest,parent_digest):
  if (type(rows) is not tuple or len(rows)>MAX_ROWS or type(context_only) is not tuple or len(context_only)>MAX_ROWS or
      type(binding_digest) is not str or type(parent_digest) is not str or len(binding_digest)>128 or len(parent_digest)>128):
    return None
  def primitive_record(record,kind):
    if type(record) is not kind:
      return None
    values=tuple(getattr(record,name) for name in kind.__dataclass_fields__)
    for value in values:
      leaf=type(value)
      if leaf is str:
        if len(value)>128:
          return None
      elif leaf is int:
        if abs(value).bit_length()>64:
          return None
      elif leaf is float:
        if not math.isfinite(value):
          return None
      elif leaf is not bool:
        return None
    return values
  pairs=[]
  for joined in rows:
    if type(joined) is not FeedbackJoin:
      return None
    c=primitive_record(joined.consumption,CoConsumption)
    p=primitive_record(joined.publication,CoPublication)
    if c is None or p is None:
      return None
    pairs.append((c,p))
  context=[]
  for record in context_only:
    values=primitive_record(record,CoConsumption)
    if values is None:
      return None
    context.append(values)
  import pickle
  # ONLY plain native scalar tuples cross pickle. Exact known record types and
  # every declared field were checked above; extra attrs/customreduce never run.
  key=pickle.dumps((tuple(pairs),tuple(context),binding_digest,parent_digest),protocol=5)
  return key if len(key)<=min(_TAPE_DIGEST_MAX_KEY,_TAPE_DIGEST_MAX_BYTES) else None


def _tape_digest_status(rows,context_only,binding_digest,parent_digest):
  global _TAPE_DIGEST_CACHE_BYTES
  key=_tape_digest_key(rows,context_only,binding_digest,parent_digest)
  if key is not None and key in _TAPE_DIGEST_CACHE:
    _TAPE_DIGEST_CACHE.move_to_end(key)
    return _TAPE_DIGEST_CACHE[key], True
  result=_tape_digest_uncached(rows,context_only,binding_digest,parent_digest)
  if key is not None:
    while _TAPE_DIGEST_CACHE and (len(_TAPE_DIGEST_CACHE)>=_TAPE_DIGEST_MAX_ENTRIES or
        _TAPE_DIGEST_CACHE_BYTES+len(key)>_TAPE_DIGEST_MAX_BYTES):
      old,_=_TAPE_DIGEST_CACHE.popitem(last=False)
      _TAPE_DIGEST_CACHE_BYTES-=len(old)
    _TAPE_DIGEST_CACHE[key]=result
    _TAPE_DIGEST_CACHE_BYTES+=len(key)
  return result, key is not None


def _tape_digest(rows,context_only,binding_digest,parent_digest):
  return _tape_digest_status(rows,context_only,binding_digest,parent_digest)[0]


def _sealed_status(tape,binding_digest):
  if not (type(tape) is VerifiedFeedbackTape and tape._seal is _VERIFIED and tape.protocol == PROTOCOL and
      tape.binding_digest == binding_digest and _hex(binding_digest) and bool(tape.rows)):
    return False, False
  expected_digest = tape.digest
  digest, closed = _tape_digest_status(tape.rows,tape.context_only,binding_digest,tape.parent_digest)
  matched = expected_digest == digest
  return matched, closed and type(tape.protocol) is str and type(expected_digest) is str


def _sealed(tape,binding_digest):
  return _sealed_status(tape,binding_digest)[0]


def _control_matches(control,row,cp_digest):
  from openpilot.starpilot.flm.fullrate_collector import ControlEvidence
  if (type(control) is not ControlEvidence or row.cp_sha256 != cp_digest or control.source_epoch != row.source_epoch or
      f32(control.requested) != row.requested_torque or bool(control.active) != row.lat_active or
      bool(control.limited) != row.published_limited or dict(control.replay_inputs).get('safety_limited') != float(row.input_limited) or
      not control.enabled or control.manual or not control.valid or not control.active or control.override or control.fault or
      not 0 < control.source_receipt_ns <= control.control_ns <= control.source_receipt_ns+500_000_000):
    raise ValueError('actual original control/source feedback mismatch')
  if control.native_required:
    if (not control.native_allowed or not _hex(control.native_identity) or
        not 0 < control.native_observed_ns <= control.control_ns <= control.native_expires_ns <= control.native_observed_ns+200_000_000):
      raise ValueError('original feedback native control lease/identity')
  elif control.native_allowed or control.native_identity or (control.native_observed_ns,control.native_expires_ns) != (0,0):
    raise ValueError('ordinary feedback cannot fabricate native ACK')


def verify_original(publications, consumptions, controls, outputs, engine, binding_digest, *,
                    expected_control_ids, context_packet, frames, allow_initial_context_only=False):
  """Complete owned original proof; optional FIRST q is explicit context only."""
  from openpilot.starpilot.flm.event_controller_replay import EventControllerReplay
  from openpilot.starpilot.flm.fullrate_collector import AppliedEvidence
  from openpilot.starpilot.flm.torque_eligibility import consumed_sources_current
  from openpilot.starpilot.flm.original_context_receipts import reuse_verified_context
  from openpilot.starpilot.flm.torque_raw_fit import output_from_dict
  from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
  if type(engine) is not EventControllerReplay or not _hex(binding_digest):
    raise ValueError('actual original replay and owned context binding required')
  verified_controls = reuse_verified_context(context_packet,engine,frames)
  if verified_controls is None or type(controls) is not dict or controls != verified_controls:
    raise ValueError('completed child-local exact original source/native replay proof required')
  owned_outputs = {v.apply_ns:v for v in (output_from_dict(body) for body in context_packet['outputs'])}
  if type(outputs) is not dict or outputs != owned_outputs:
    raise ValueError('exact original applied context receipt bodies required')
  _archive_records(publications,consumptions)
  frame_ids = tuple(frame.control_ns for frame in frames)
  if (type(expected_control_ids) is not tuple or expected_control_ids != frame_ids or
      expected_control_ids != tuple(row.control_ns for row in consumptions) or type(allow_initial_context_only) is not bool):
    raise ValueError('all original controller feedback membership required')
  cp_digest = car_params_digest(engine.factory.cp)
  for row in consumptions:
    _control_matches(controls.get(row.control_ns),row,cp_digest)
  by_header = {pub.publication_ns:pub for pub in publications}
  first = consumptions[0]
  first_pub = by_header.get(first.publication_ns)
  first_unclosed = (not first.seen or not first.valid or first_pub is None or not first_pub.output_known or
                    first_pub.output_control_ns not in controls or first_pub.output_apply_ns not in outputs)
  context_only = ()
  known = consumptions
  if first_unclosed:
    if not allow_initial_context_only or len(consumptions) < 2 or first.control_ns != frame_ids[0]:
      raise ValueError('initial original CO context is unclosed')
    context_only,known = (first,),consumptions[1:]
  # Retain raw archive digest, including unclosed first q and unconsumed COs.
  # Only known consumed headers are joined; ALL controller rows remain required.
  needed = {row.publication_ns for row in known}
  used_publications = tuple(pub for pub in publications if pub.publication_ns in needed)
  rows = join_original(used_publications,known)
  if context_only and (known[0].prior_publish_ns != first.control_ns or known[0].input_limited is not first.published_limited):
    raise ValueError('initial context to first known feedback linkage')
  for joined in rows:
    row,pub = joined.consumption,joined.publication
    producing = controls.get(pub.output_control_ns)
    output = outputs.get(pub.output_apply_ns)
    if producing is None or type(output) is not AppliedEvidence:
      raise ValueError('actual original producing apply unavailable')
    if (pub.output_carrier_sequence < output.carrier_frame or pub.carrier_observed_ns < output.carrier_observed_ns):
      raise ValueError('held original carrier receipt regressed')
    held = replace(output,carrier_frame=pub.output_carrier_sequence,carrier_observed_ns=pub.carrier_observed_ns)
    if (output.control_ns != pub.output_control_ns or output.state_ns != pub.output_state_ns or
        output.apply_ns != pub.output_apply_ns or applied_digest(held) != pub.applied_sha256 or
        not consumed_sources_current(producing,output) or not engine.applied_matches(producing,output) or
        producing.source_epoch != row.source_epoch or f32(output.applied) != pub.reported_torque or
        pub.native_identity and pub.native_identity != producing.native_identity or
        not pub.output_apply_ns <= row.control_ns <= pub.output_apply_ns+MAX_AGE_NS or
        not output.valid or not output.active or output.override or output.fault or not output.native_allowed or
        producing.native_required and output.apply_ns > producing.native_expires_ns):
      raise ValueError('original feedback source/native/actual CAN mismatch')
  parent = _digest({'publications':[_feedback_asdict(v) for v in publications],'consumptions':[_feedback_asdict(v) for v in consumptions]})
  digest = _tape_digest(rows,context_only,binding_digest,parent)
  return VerifiedFeedbackTape(PROTOCOL,rows,binding_digest,digest,context_only,parent,_VERIFIED)


def _checked_subset(tape, exact_control_ids, binding_digest):
  sealed, closed = _sealed_status(tape,binding_digest)
  if not sealed or type(exact_control_ids) is not tuple or not exact_control_ids:
    raise ValueError('owned verified feedback tape/exact window required')
  stamps = tuple(row.consumption.control_ns for row in tape.rows)
  try:
    start = stamps.index(exact_control_ids[0])
  except ValueError as error:
    raise ValueError('subset cannot include unknown/context-only feedback') from error
  selected = tape.rows[start:start+len(exact_control_ids)]
  if tuple(row.consumption.control_ns for row in selected) != exact_control_ids:
    raise ValueError('complete contiguous original feedback window required')
  return selected, closed and all(type(stamp) is int for stamp in exact_control_ids)


def _mint_subset(tape, selected, binding_digest):
  digest = _tape_digest(selected,(),binding_digest,tape.digest)
  return VerifiedFeedbackTape(PROTOCOL,selected,binding_digest,digest,(),tape.digest,_VERIFIED)


def subset_verified(tape, exact_control_ids, *, binding_digest):
  """Owner-minted contiguous original window; no wire-created subset seal."""
  selected, _ = _checked_subset(tape,exact_control_ids,binding_digest)
  return _mint_subset(tape,selected,binding_digest)


def validate_subset_verified(tape, exact_control_ids, *, binding_digest):
  """Fresh validation; discard mint only for the just-checked closed domain."""
  selected, closed = _checked_subset(tape,exact_control_ids,binding_digest)
  if not closed:
    _mint_subset(tape,selected,binding_digest)


def verify_candidate_flags(tape, candidate_requests, candidate_reported, *, activation_ns, binding_digest):
  """Narrow regime equality only. No modified inputs or native permission."""
  if (not _sealed(tape,binding_digest) or not _positive(activation_ns) or
      tape.rows[0].consumption.control_ns > activation_ns):
    raise ValueError('owned verified feedback tape/prefix required')
  return _flag_equations(tape.rows,candidate_requests,candidate_reported,activation_ns)


def _flag_equations(rows,candidate_requests,candidate_reported,activation_ns):
  """Pure arithmetic for differential tests; NEVER native/admission proof."""
  if type(candidate_requests) is not dict or type(candidate_reported) is not dict:
    raise ValueError('explicit candidate feedback maps')
  if set(candidate_requests) != {r.consumption.control_ns for r in rows}:
    raise ValueError('all candidate feedback frames required')
  for joined in rows:
    row,pub = joined.consumption,joined.publication
    request = f32(candidate_requests[row.control_ns])
    if row.control_ns < activation_ns:
      if request != row.requested_torque:
        raise ValueError('candidate changed original preactivation prefix')
      continue
    if pub.output_control_ns < activation_ns:
      reported = pub.reported_torque
    else:
      if pub.output_apply_ns not in candidate_reported:
        raise ValueError('candidate reported CO coordinate missing')
      reported = f32(candidate_reported[pub.output_apply_ns])
    hint = bool(row.lat_active and abs(request-reported) > .01)
    if hint is not row.published_limited:
      raise ValueError('candidate changed original consumed-feedback regime')
  return True


def _archive_records(publications,consumptions):
  if (type(publications) is not tuple or type(consumptions) is not tuple or
      not 1 <= len(publications) <= MAX_ROWS or not 1 <= len(consumptions) <= MAX_ROWS):
    raise ValueError('bounded complete feedback archive')
  for pub in publications:
    validate_publication(pub)
  for row in consumptions:
    validate_consumption(row)
  if (len({v.publication_ns for v in publications}) != len(publications) or
      any(b.publication_ns <= a.publication_ns for a,b in zip(publications,publications[1:], strict=False)) or
      any(b.control_ns <= a.control_ns for a,b in zip(consumptions,consumptions[1:], strict=False))):
    raise ValueError('duplicate/reordered original feedback archive')


def export_observations(publications, consumptions, *, binding_digest):
  """Separate bounded archive body; hashes bind content, never execute proof."""
  if not _hex(binding_digest):
    raise ValueError('explicit feedback archive binding')
  # Preserve unknown/context observations too; this is NEVER a verified type.
  _archive_records(publications,consumptions)
  body = {'protocol':PROTOCOL,'binding_digest':binding_digest,
          'publications':[_feedback_asdict(v) for v in publications],
          'consumptions':[_feedback_asdict(v) for v in consumptions]}
  return {**body,'digest':_digest(body)}


def decode_observations(packet, *, binding_digest):
  """Untrusted separate records only. Never returns VerifiedFeedbackTape."""
  from dataclasses import fields
  if type(packet) is not dict or set(packet) != {'protocol','binding_digest','publications','consumptions','digest'}:
    raise ValueError('explicit feedback archive version required; old packets ineligible')
  body = {k:v for k,v in packet.items() if k != 'digest'}
  if (packet['protocol'] != PROTOCOL or not _hex(binding_digest) or packet['binding_digest'] != binding_digest or
      not _hex(packet['digest'])):
    raise ValueError('feedback archive exact binding/body')
  values = []
  for name,kind,validator in (('publications',CoPublication,validate_publication),('consumptions',CoConsumption,validate_consumption)):
    raw = packet[name]
    if type(raw) is not list or not 1 <= len(raw) <= MAX_ROWS:
      raise ValueError('feedback archive row budget')
    keys = {v.name for v in fields(kind)}
    if any(type(v) is not dict or set(v) != keys for v in raw):
      raise ValueError('feedback archive exact typed fields')
    values.append(tuple(validator(kind(**v)) for v in raw))
  if packet['digest'] != _digest(body):
    raise ValueError('feedback archive exact binding/body')
  _archive_records(*values)
  return tuple(values)
