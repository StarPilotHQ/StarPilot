"""One bounded numerical probe child; results never carry current permission."""

from dataclasses import fields
import hashlib

from openpilot.starpilot.flm.fit_process import FitProcess

VERSION = 'live-probe-numerical-job-v1'
MAX_PACKED = 3 * 1024 * 1024


def _digest(payload):
  return hashlib.sha256(payload).hexdigest()


def _validate_primitive(value):
  # Validation-only callers need no allocated type-key tree. This checks the
  # same closed graph and bounds afresh; both mutation-comparison keys below
  # still materialize every original field and exact float bit independently.
  from openpilot.starpilot.flm.native_primitives import try_native
  if try_native('probe_source_valid', value) is not True:
    from openpilot.starpilot.flm.live_tracking_probe import _primitive
    _primitive(value)


def _plain(value, cls):
  from openpilot.starpilot.flm.live_tracking_probe import _record

  _record(value, cls)
  return {field.name: getattr(value, field.name) for field in fields(cls)}


def _literal_control(row):
  from openpilot.starpilot.flm.fullrate_collector import ControlEvidence
  from openpilot.starpilot.flm.live_tracking_probe import _trace_key

  if type(row) is not ControlEvidence:
    raise ValueError('exact original control receipt required')
  _trace_key(row.effect_trace)
  result = {field.name: _literal_trace(row.effect_trace) if field.name == 'effect_trace' else getattr(row, field.name)
            for field in fields(ControlEvidence)}
  # Close every field together before any packing or source comparison. This
  # avoids a separate native stack-budget walk for each scalar in every tick.
  _validate_primitive(result)
  return result


def _literal_trace(value):
  from openpilot.starpilot.flm.live_tracking_probe import _trace_key

  _trace_key(value)
  if value is None:
    return None
  trace = {item.name: getattr(value, item.name) for item in fields(type(value))}
  trace['envelope'] = {item.name: getattr(value.envelope, item.name) for item in fields(type(value.envelope))}
  _validate_primitive(trace)
  return trace


def probe_replay_snapshot(replay, prefix_controls, *, endpoint_output=None):
  """Copy one genuine bounded raw prefix, without blocks or recorder mutation.

  This is a restricted numerical transport, never an original/native acceptance.
  The child recomputes every actual controller and native chronology check.
  """
  from itertools import islice
  from collections import OrderedDict, deque
  from openpilot.starpilot.flm.controller_replay import (
    ControllerReplay,
    SelectedControllerFactory,
    Snapshot,
    ReplayFrame,
    pack_primitive_snapshot,
  )
  from openpilot.starpilot.flm.pid_controller_replay import PidControllerFactory
  from openpilot.starpilot.flm.torque_emission_replay import EmissionHistory, Emission
  from openpilot.starpilot.flm.torque_adapter import Objective
  from openpilot.starpilot.flm.live_tracking_probe import _primitive, _factory_configuration, _projection_literal
  from openpilot.starpilot.flm.learner_factory import _frame
  from openpilot.starpilot.flm.original_context_receipts import _same_original_frame
  from opendbc.car.steering_projection import TorqueProjection

  if (
    type(replay) is not ControllerReplay
    or type(replay.emissions) is not EmissionHistory
    or type(replay.factory) not in (SelectedControllerFactory, PidControllerFactory)
    or type(prefix_controls) is not tuple
    or not 1 <= len(prefix_controls) <= 100
  ):
    raise ValueError('genuine bounded raw prefix owner required')

  def source(*, verify_frames=True):
    if (
      type(replay.records) is not deque
      or type(replay.checkpoints) is not deque
      or type(replay.emissions.events) is not OrderedDict
      or type(replay.emissions.carriers) is not OrderedDict
      or type(replay.failed) is not bool
      or type(replay.raw_mode) is not bool
    ):
      raise ValueError('exact bounded recorder containers/flags required')
    controls = tuple(_literal_control(row) for row in prefix_controls)
    if any(type(row['control_ns']) is not int for row in controls):
      raise ValueError('exact actual prefix clocks required')
    first, last = controls[0]['control_ns'], controls[-1]['control_ns']
    endpoint_apply_ns = None if endpoint_output is None else endpoint_output.apply_ns
    if endpoint_output is not None and endpoint_output.control_ns != last:
      raise ValueError('exact original endpoint application required')
    if replay.failed or replay.raw_mode is not True or not 0 < first <= last <= first + 1_000_000_000:
      raise ValueError('genuine bounded raw prefix unavailable')
    candidates = []
    for row in islice(reversed(replay.checkpoints), 600):
      if type(row) is not tuple or len(row) != 2 or type(row[0]) is not int or row[0] <= 0 or type(row[1]) is not Snapshot:
        raise ValueError('exact genuine checkpoint row required')
      if row[0] <= last:
        candidates.append(row)
    latest = candidates[0] if candidates else None
    if latest is None or latest[0] != first:
      raise ValueError("latest genuine actual prefix checkpoint required")
    checkpoint = latest[1]
    if type(checkpoint) is not Snapshot or _primitive(checkpoint.values) != _primitive(prefix_controls[0].replay_snapshot):
      raise ValueError('genuine actual prefix checkpoint required')
    selected = []
    # The endpoint can be up to2s old and its ORIGINAL checkpoint up to1s
    # before it. Scan that already-retained context; the emitted job STILL
    # contains at most100 original ticks and all old freshness guards remain.
    for row in islice(reversed(replay.records), 301):
      if type(row) is not ReplayFrame or type(row.control_ns) is not int:
        raise ValueError('exact original prefix frame clock required')
      if first <= row.control_ns <= last:
        selected.append(row)
    frames = tuple(reversed(selected))
    stored = []
    for frame in frames:
      row = {field.name: _literal_trace(frame.effect_trace) if field.name == 'effect_trace' else getattr(frame, field.name) for field in fields(ReplayFrame)}
      _validate_primitive(row)
      stored.append(row)
    # The original comparator's unknown fallback may deepcopy. Only a fully
    # closed literal frame graph may reach it, including all tuning fields.
    if (
      len(frames) != len(controls)
      or not frames
      or frames[0].control_ns != first
      or frames[-1].control_ns != last
      or verify_frames and any(not _same_original_frame(frame, _frame(control)) for frame, control in zip(frames, prefix_controls, strict=True))
    ):
      raise ValueError('genuine complete original prefix differs')
    selected_events = []
    for event in islice(reversed(replay.emissions.events.values()), 301):
      if type(event) is not Emission or type(event.control_ns) is not int:
        raise ValueError('exact original native emission clock required')
      if first <= event.control_ns <= last and (endpoint_apply_ns is None or event.apply_ns <= endpoint_apply_ns):
        selected_events.append(event)
    events = tuple(reversed(selected_events))
    native = []
    for event in events:
      if type(event) is not Emission or type(event.witness) is not TorqueProjection:
        raise ValueError('exact actual native prefix event required')
      row = {field.name: _projection_literal(event.witness) if field.name == 'witness' else getattr(event, field.name) for field in fields(Emission)}
      _validate_primitive(row)
      native.append(row)
    if not events or events[-1].control_ns != last:
      raise ValueError('actual endpoint native chronology missing')
    first_frame, last_frame = events[0].witness.frame + 1, events[-1].witness.frame + 1
    selected_carriers = []
    for frame, row in islice(reversed(replay.emissions.carriers.items()), 301):
      if type(frame) is not int or type(row) is not tuple or len(row) != 2 or any(type(value) is not int for value in row):
        raise ValueError('exact actual native carrier row required')
      if first_frame <= frame <= last_frame:
        selected_carriers.append((frame, *row))
    carriers = tuple(reversed(selected_carriers))
    factory = replay.factory
    mode, policy = _factory_configuration(factory)
    from opendbc.car.car_helpers import interfaces

    interface = interfaces.get(str(factory.cp.carFingerprint))
    if type(factory) not in (SelectedControllerFactory, PidControllerFactory) or interface is None or factory.interface_class is not interface:
      raise ValueError("exact registered prefix factory required")
    constructor = (interface.__module__, interface.__qualname__, policy)
    data = {
      'version': 1,
      'raw_mode': True,
      'cp_bytes': factory.cp.as_builder().to_bytes(),
      'mode': mode,
      'turn_assist': factory.turn_assist,
      'manual_baseline': factory.manual_baseline,
      'manual_source_epoch': factory.manual_source_epoch,
      'objective': _plain(replay.objective, Objective),
      'identity': replay.identity,
      'baseline_revision': replay.baseline_revision,
      'model_revision': replay.model_revision,
      'windows': ((first, last),),
      'checkpoints': ((first, checkpoint.revision, checkpoint.values),),
      'frames': tuple(stored),
      'emissions': {'events': tuple(native), 'carriers': carriers},
    }
    # Includes genuine complete source receipts as well as transported raw data.
    return data, _primitive((data, controls, constructor)), factory

  data, key, factory = source()
  packed = pack_primitive_snapshot(data)
  if len(packed) > MAX_PACKED:
    raise ValueError('bounded raw probe snapshot byte limit')
  # The second complete key rereads every frame AND original control. If it
  # is unchanged, the first pass's exact frame-membership proof still holds.
  _, after, current_factory = source(verify_frames=False)
  if after != key or current_factory is not factory:
    raise ValueError('actual raw prefix source changed while copying')
  return packed


def make_probe_job(
  *,
  replay_snapshot,
  control,
  output,
  accepted_full30,
  binding,
  response_witness,
  source,
  evidence_ns,
  queued_ns,
  current_vehicle_model_anchor,
  group,
  names,
  prefix_controls,
  archived_clock=False,
  _payload_only=False,
):
  """Freeze genuine owner evidence, independently of later application authority.

  evidence_ns is the ACTUAL parent observation time, never a fabricated emission
  clock. Child wall time remains real; historical leases are checked at emission.
  """
  from openpilot.starpilot.flm.controller_replay import pack_primitive_snapshot
  from openpilot.starpilot.flm.fullrate_collector import AppliedEvidence
  from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding, full_coordinates
  from openpilot.starpilot.flm.live_tracking_response import witness_to_dict
  from openpilot.starpilot.flm.live_tracking_probe import _projection_literal
  from openpilot.starpilot.flm.runtime_adapter_registry import SourceState
  from opendbc.car.steering_projection import TorqueProjection

  if (
    type(_payload_only) is not bool
    or type(archived_clock) is not bool
    or type(evidence_ns) is not int
    or evidence_ns <= 0
    or type(queued_ns) is not int
    or queued_ns <= 0
    or not archived_clock
    and evidence_ns > queued_ns
    or type(replay_snapshot) is not bytes
    or not 0 < len(replay_snapshot) <= MAX_PACKED
    or type(prefix_controls) is not tuple
    or not 1 <= len(prefix_controls) <= 100
    or type(output) is not AppliedEvidence
    or type(output.projection) is not TorqueProjection
  ):
    raise ValueError('bounded genuine numerical probe evidence required')
  binding.validate()
  full_coordinates(accepted_full30, binding.baseline_revision)

  applied = {
    field.name: _projection_literal(output.projection) if field.name == 'projection' else getattr(output, field.name) for field in fields(AppliedEvidence)
  }
  _validate_primitive(applied)
  payload = {
    'version': VERSION,
    'replay': replay_snapshot,
    'control': _literal_control(control),
    'output': applied,
    'accepted_full30': accepted_full30,
    'binding': _plain(binding, LiveTrialBinding),
    'witness': witness_to_dict(response_witness, now_ns=evidence_ns),
    'source': _plain(source, SourceState),
    'evidence_ns': evidence_ns,
    'queued_ns': queued_ns,
    'archived_clock': archived_clock,
    'anchor': current_vehicle_model_anchor,
    'group': group,
    'names': names,
    'prefix_controls': tuple(_literal_control(row) for row in prefix_controls),
  }
  _validate_primitive(dict(payload, replay=None))
  if _payload_only:
    return payload
  packed = pack_primitive_snapshot(payload)
  if len(packed) > MAX_PACKED:
    raise ValueError('numerical probe job byte bound')
  return {'version': VERSION, 'payload': packed.hex(), 'sha256': _digest(packed)}


def decode_probe_job(job):
  from openpilot.starpilot.flm.controller_replay import unpack_primitive_snapshot

  if (
    type(job) is not dict
    or set(job) != {'version', 'payload', 'sha256'}
    or job['version'] != VERSION
    or type(job['payload']) is not str
    or not 0 < len(job['payload']) <= 2 * MAX_PACKED
    or type(job['sha256']) is not str
  ):
    raise ValueError('restricted numerical probe envelope required')
  packed = bytes.fromhex(job['payload'])
  if _digest(packed) != job['sha256']:
    raise ValueError('numerical job content changed')
  payload = unpack_primitive_snapshot(packed)
  if type(payload) is not dict or payload.get('version') != VERSION:
    raise ValueError('numerical job schema differs')
  batch = payload.get('batch')
  if batch is not None:
    if type(batch) is not tuple or not 1 <= len(batch) <= 4:
      raise ValueError('bounded genuine numerical endpoint batch required')
    _validate_primitive(dict(payload, batch=None, replay=None))
    rows = batch
  else:
    rows = (payload,)
  for row in rows:
    if (
      type(row) is not dict
      or row.get('version') != VERSION
      or 'batch' in row
      or type(row.get('replay')) is not bytes
      or not 0 < len(row['replay']) <= MAX_PACKED
    ):
      raise ValueError('bounded literal replay bytes required')
    _validate_primitive(dict(row, replay=None))
  return payload


def make_probe_batch(jobs, *, queued_ns):
  """One child, up to four REAL independently anchored original endpoints.

  Each original evidence clock and literal snapshot is retained. queued_ns is
  only the actual batch lifecycle clock; no stale evidence becomes a grant.
  """
  if type(jobs) is not tuple or not 1 <= len(jobs) <= 4 or type(queued_ns) is not int or queued_ns <= 0:
    raise ValueError('bounded actual probe batch required')
  rows = tuple(decode_probe_job(job) for job in jobs)
  if any('batch' in row or type(row.get('prefix_controls')) is not tuple or not row['prefix_controls'] for row in rows):
    raise ValueError("single genuine endpoint inputs required")
  return _pack_probe_batch(rows, queued_ns=queued_ns)


def _pack_probe_batch(rows, *, queued_ns):
  from openpilot.starpilot.flm.controller_replay import pack_primitive_snapshot
  from openpilot.starpilot.flm.live_tracking_probe import _primitive

  if type(rows) is not tuple or not 1 <= len(rows) <= 4 or type(queued_ns) is not int or queued_ns <= 0:
    raise ValueError('bounded actual probe batch required')
  for row in rows:
    if (type(row) is not dict or row.get('version') != VERSION or 'batch' in row or
        type(row.get('replay')) is not bytes or not 0 < len(row['replay']) <= MAX_PACKED or
        type(row.get('prefix_controls')) is not tuple or not row['prefix_controls']):
      raise ValueError('single genuine endpoint inputs required')
    _validate_primitive(dict(row, replay=None))
  common = ('binding', 'accepted_full30', 'anchor', 'group', 'names', 'archived_clock')
  first = rows[0]
  previous = previous_apply = 0
  from openpilot.starpilot.flm.controller_replay import MAX_FRAME_GAP_NS

  for row in rows:
    if (
      'batch' in row
      or any(_primitive(row[name]) != _primitive(first[name]) for name in common)
      or type(row['control']['control_ns']) is not int
      or not previous < row['control']['control_ns']
      or not 0 < row['queued_ns'] <= queued_ns <= row['queued_ns'] + 2_000_000_000
    ):
      raise ValueError('genuine same-checkpoint/profile/group probe batch required')
    prefix = row['prefix_controls']
    if any(type(item) is not dict for item in prefix) or type(row.get('control')) is not dict or type(row.get('output')) is not dict:
      raise ValueError('exact original endpoint dictionaries required')
    stamps = tuple(item.get('control_ns') for item in prefix)
    apply_ns = row.get('output', {}).get('apply_ns')
    if (
      not 1 <= len(prefix) <= 100
      or any(type(stamp) is not int for stamp in stamps)
      or not 0 < stamps[0] <= stamps[-1] <= stamps[0] + 1_000_000_000
      or any(not 0 < right - left <= MAX_FRAME_GAP_NS for left, right in zip(stamps, stamps[1:], strict=False))
      or _primitive(prefix[-1]) != _primitive(row['control'])
      or not prefix[0].get('replay_snapshot')
      or any(item.get('replay_snapshot') for item in prefix[1:])
      or type(apply_ns) is not int
      or not previous_apply < apply_ns
    ):
      raise ValueError('independent complete original prefix or application order differs')
    previous = row['control']['control_ns']
    previous_apply = apply_ns
  payload = {'version': VERSION, 'queued_ns': queued_ns, 'batch': rows}
  packed = pack_primitive_snapshot(payload)
  if len(packed) > MAX_PACKED:
    raise ValueError('numerical batch byte bound')
  return {'version': VERSION, 'payload': packed.hex(), 'sha256': _digest(packed)}


def _prepared_context(data):
  """Small original request identity, independently recomputed by the worker."""
  rows = data.get('batch', (data,))
  return {
    'batch': tuple(
      {
        'binding': row['binding'],
        'control': {key: row['control'][key] for key in ('control_ns', 'stage_schema_revision')},
        'output': {'apply_ns': row['output']['apply_ns']},
        'accepted_full30': row['accepted_full30'],
        'group': row['group'],
        'names': row['names'],
        'source': row['source'],
        'anchor': row['anchor'],
        'evidence_ns': row['evidence_ns'],
        'queued_ns': row['queued_ns'],
        'prefix_first_ns': row['prefix_controls'][0]['control_ns'],
        'prefix_last_ns': row['prefix_controls'][-1]['control_ns'],
      }
      for row in rows
    )
  }


def _context_digest(context):
  import json

  raw = json.dumps(context, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
  if not 0 < len(raw) <= 65536:
    raise ValueError('bounded original numerical request context required')
  return _digest(raw)


def original_probe_context(selected, accepted, binding, source, *, group, names, now_ns):
  from dataclasses import asdict

  rows = tuple(
    {
      'binding': asdict(binding),
      'control': {'control_ns': control.control_ns, 'stage_schema_revision': control.stage_schema_revision},
      'output': {'apply_ns': output.apply_ns},
      'accepted_full30': accepted,
      'group': group,
      'names': names,
      'source': asdict(source),
      'anchor': control.vehicle_model_anchor,
      'evidence_ns': now_ns,
      'queued_ns': now_ns,
      'prefix_controls': ({'control_ns': prefix[0].control_ns}, {'control_ns': prefix[-1].control_ns}),
    }
    for control, output, prefix in selected
  )
  context = _prepared_context({'batch': rows})
  _context_digest(context)
  return context


def prepare_probe_wire(jobs, *, queued_ns, expected):
  """Called in the preparation CHILD; never walk large replay graphs in parent."""
  job = make_probe_batch(tuple(jobs), queued_ns=queued_ns)
  context = _prepared_context(decode_probe_job(job))
  return _prepared_wire(job, context, expected)


def prepare_probe_payloads(rows, *, queued_ns, expected):
  """Pack the child's original validated endpoints once for independent replay."""
  job = _pack_probe_batch(rows, queued_ns=queued_ns)
  return _prepared_wire(job, _prepared_context({'batch': rows}), expected)


def _prepared_wire(job, context, expected):
  import json

  digest = _context_digest(context)
  if digest != _context_digest(expected):
    raise ValueError('prepared numerical input differs from original request')
  wire = json.dumps(dict(job, preparedContextSha256=digest), separators=(',', ':'), allow_nan=False).encode()
  from openpilot.starpilot.flm.fit_process import MAX_INPUT

  if not 0 < len(wire) <= MAX_INPUT:
    raise ValueError('prepared numerical IPC byte bound')
  return {'wire': wire.decode('ascii'), 'wire_sha256': _digest(wire), 'job_sha256': job['sha256'], 'context_sha256': digest}


class ProbeProcess(FitProcess):
  worker_module = 'openpilot.starpilot.flm.live_tracking_probe_worker'
  wall_ns = 30_000_000_000
  interval_ns = 0

  def __init__(self, *, archived_diagnostic=False):
    if type(archived_diagnostic) is not bool:
      raise ValueError("explicit diagnostic clock mode required")
    super().__init__()
    self.expected = None
    self.prepared_context_sha = None
    self.archived_diagnostic = archived_diagnostic

  def submit_probe(self, job, *, token, now_ns):
    if self.child is not None:
      return False
    payload = decode_probe_job(job)
    if not 0 < payload['queued_ns'] <= now_ns <= payload['queued_ns'] + 100_000_000:
      return False
    from openpilot.starpilot.flm.live_tracking_probe import _primitive, _evidence_clock

    for row in payload.get('batch', (payload,)):
      archived = row.get('archived_clock', False)
      if archived and not self.archived_diagnostic:
        return False
      _evidence_clock(evidence_ns=row['evidence_ns'], queued_ns=row['queued_ns'], now_ns=now_ns, current_native=False, archived_clock=archived)
    if not super().submit(job, token=(_primitive(token), self.archived_diagnostic, job['sha256']), now_ns=now_ns):
      return False
    # Independent immutable exact content; callers may mutate their original job.
    self.expected = (job['sha256'], payload)
    self.prepared_context_sha = None
    return True

  def submit_prepared_probe(self, prepared, *, expected, token, now_ns):
    # Only immutable bytes and a small parent-owned original identity cross
    # here. The worker recomputes this identity from its fully decoded job.
    if self.child is not None:
      return False
    from openpilot.starpilot.flm.live_tracking_probe import _primitive

    if type(prepared) is not dict or set(prepared) != {'wire', 'wire_sha256', 'job_sha256', 'context_sha256'}:
      raise ValueError('prepared numerical envelope differs')
    from openpilot.starpilot.flm.fit_process import MAX_INPUT

    wire = prepared['wire']
    if type(wire) is str:
      if not 0 < len(wire) <= MAX_INPUT:
        raise ValueError('prepared numerical wire bound')
      try:
        wire = wire.encode('ascii')
      except UnicodeEncodeError as error:
        raise ValueError('prepared numerical wire is not ASCII') from error
    if (
      type(wire) is not bytes
      or not 0 < len(wire) <= MAX_INPUT
      or _digest(wire) != prepared['wire_sha256']
      or prepared['context_sha256'] != _context_digest(expected)
      or type(prepared['job_sha256']) is not str
      or len(prepared['job_sha256']) != 64
      or any(char not in '0123456789abcdef' for char in prepared['job_sha256'])
    ):
      raise ValueError('prepared numerical wire or original context differs')
    rows = expected['batch']
    if not 1 <= len(rows) <= 4 or any(not 0 < row['queued_ns'] <= now_ns <= row['queued_ns'] + 2_000_000_000 for row in rows):
      return False
    from copy import deepcopy

    owned_context = deepcopy(expected)
    digest = prepared['job_sha256']
    if not super().submit_bytes(wire, token=(_primitive(token), self.archived_diagnostic, digest), now_ns=now_ns):
      return False
    self.expected = digest, owned_context
    self.prepared_context_sha = prepared['context_sha256']
    return True

  def poll_probe(self, *, token, binding, now_ns):
    expected = self.expected
    prepared_sha = self.prepared_context_sha
    from openpilot.starpilot.flm.live_tracking_probe import _primitive

    digest = expected[0] if expected is not None else None
    report = super().poll(token=(_primitive(token), self.archived_diagnostic, digest), now_ns=now_ns)
    if self.child is None:
      self.expected = None
      self.prepared_context_sha = None
    if report is None or expected is None:
      return None
    return self.decode_report(report, expected=expected, prepared_sha=prepared_sha, binding=binding)

  @staticmethod
  def decode_report(report, *, expected, prepared_sha, binding):
    # Shared exact decoder for the existing numerical subprocess and the
    # already-forked snapshot child. Neither result carries live permission.
    from openpilot.starpilot.flm.live_tracking_probe import ProbeResult, ProbeCoordinate, _OWNER, _probe_key, validate_probe_result
    from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding, full_coordinates

    digest, payload = expected
    batch = payload.get('batch')
    key = 'results' if batch is not None else 'rows'
    if (
      type(report) is not dict
      or set(report) != ({'version', 'sha256', key, 'contextSha256'} if prepared_sha else {'version', 'sha256', key})
      or report['version'] != VERSION
      or report['sha256'] != digest
      or prepared_sha is not None
      and report.get('contextSha256') != prepared_sha
    ):
      raise ValueError('owned child numerical result/context differs')

    def seal(data, output):
      if (
        LiveTrialBinding(**data['binding']) != binding
        or type(output) is not list
        or not 1 <= len(output) <= 30
        or any(type(row) is not dict or set(row) != set(ProbeCoordinate.__dataclass_fields__) for row in output)
      ):
        raise ValueError('owned child numerical result/context differs')
      rows = tuple(ProbeCoordinate(**row) for row in output)
      if tuple(row.name for row in rows) != data['names']:
        raise ValueError('owned child coordinate group differs')
      result = ProbeResult(
        binding,
        data['control']['control_ns'],
        data['output']['apply_ns'],
        full_coordinates(data['accepted_full30'], binding.baseline_revision),
        binding.physical_model_revision,
        rows,
        'torque_pid' if data['control']['stage_schema_revision'] == 'pid-selected-stage-snapshot-v2' else 'torque',
        data['group'],
        None,
      )
      object.__setattr__(result, '_seal', (_OWNER, _probe_key(result)))
      return validate_probe_result(result, binding)

    if batch is not None:
      if type(report['results']) is not list or len(report['results']) != len(batch):
        raise ValueError('actual numerical endpoint batch shape differs')
      return tuple(seal(data, output) for data, output in zip(batch, report['results'], strict=True))
    return seal(payload, report['rows'])
