"""Private opt-in unpaired ORIGINAL observations; no plant join or admission.

Only pose rows are new retained state. Commands/encoders remain in the existing
actual replay engine and emission recorder. Export does bounded work explicitly
in the optional learner, never in Controls/Card. This is not fit qualification.
"""
from openpilot.starpilot.flm.producer_clock_equivalence import clock_uncertainty_limit
from dataclasses import asdict, dataclass
import math

MAX_SOURCE_POSES = 5120  # Shared raw retention, including legacy rows + pending.
# Existing raw collector can retain 24*80 sealed +80 partial rows; reserve its
# full allowance and four pending original poses even while legacy keeps growing.
MAX_RETAINED_SOURCE_POSES = MAX_SOURCE_POSES - (24 + 1) * 80 - 4
MAX_PACKET_FRAMES = 12000
MAX_PACKET_WINDOWS = 24
# Reuse existing classless transport limits; no silent private cap relaxation.
from openpilot.starpilot.flm.controller_replay import (
  MAX_FIT_SNAPSHOT_BYTES, MAX_FIT_RAW_BYTES, pack_primitive_snapshot, unpack_primitive_snapshot,
)
SOURCE_PACKET_VERSION = 'unpaired-original-source-v8'
SOURCE_PACKET_ENCODING = 'primitive-snapshot-v1'
MAX_PACKET_NS = 600_000_000_000
MAX_CAPTURE_SPAN_NS = 100_000_000_000  # <=10000 fullrate frames, leaves prefix budget.
MAX_GAP_NS = 100_000_000


@dataclass(frozen=True)
class OriginalSourceClockContract:
  """External producer provenance, never inferred from timestamp proximity.

  Construction is not verification. Production caller must independently pin
  the producer revision and proof; fixture contracts qualify only source tests.
  """
  source_domain: str
  producer_revision: str
  proof_sha256: str
  scope: str

  def validate(self):
    if (self.source_domain not in ('monotonic', 'boottime', 'unknown-equivalent-at-receipt', 'mono_boot_equivalent_interval_v1') or
        not _hex(self.producer_revision) or not _hex(self.proof_sha256) or
        self.scope not in ('independently-pinned-producer', 'synthetic-fixture', 'pinned-source-abi')):
      raise ValueError('explicit original producer-clock proof required')
    if self.scope == 'pinned-source-abi':
      from openpilot.starpilot.flm.producer_clock_equivalence import CLOSURE_SHA256, CONVENTION
      if (self.source_domain, self.producer_revision, self.proof_sha256) != (CONVENTION, CLOSURE_SHA256, CLOSURE_SHA256):
        raise ValueError('unsupported pinned mixed producer closure')


@dataclass(frozen=True)
class OriginalSourcePose:
  mono_ns: int
  received_ns: int
  source_boot_ns: int
  clock_offset_ns: int
  clock_uncertainty_ns: int
  calibration: str
  calibration_estimate: str
  context: tuple
  yaw_rate: float
  roll_rad: float
  native_identity: str
  native_allowed: bool
  valid: bool
  source_epoch: str
  identity: str
  baseline_revision: str
  controller_revision: str
  source_state_ns: int
  native_apply_ns: int
  observed_ns: int = 0
  completed_ns: int = 0
  native_required: bool = True
  native_control_ns: int = 0
  native_observed_ns: int = 0
  native_expires_ns: int = 0
  ordinary_safety: tuple = ()
  clock_equivalence: tuple = ()
  clock_generation: int = 0
  source_lower_ns: int = 0
  source_upper_ns: int = 0
  source_timestamp_ns: int = 0
  source_clock_domain: str = ''
  source_clock_contract: tuple = ()
  boot_mono_offset_ns: int = 0
  angular_velocity_device: tuple = ()
  orientation_ned: tuple = ()
  calibration_rpy: tuple = ()
  speed_mps: float = 0.0
  steering_angle_deg: float = 0.0
  left_state_ns: int = 0
  right_state_ns: int = 0
  left_state: tuple = ()
  right_state: tuple = ()


def _positive_int(value):
  return type(value) is int and value > 0


def _hex(value):
  return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def validate_pose(row, now_ns):
  if not isinstance(row, OriginalSourcePose):
    raise ValueError('typed original pose required')
  if (not all(_positive_int(x) for x in (row.mono_ns, row.received_ns, row.source_timestamp_ns,
        row.source_state_ns, row.native_apply_ns, row.left_state_ns, row.right_state_ns, row.observed_ns, row.completed_ns, now_ns)) or
      not 0 < row.mono_ns <= row.received_ns <= row.observed_ns <= row.completed_ns <= now_ns or now_ns - row.mono_ns > MAX_GAP_NS or
      type(row.clock_offset_ns) is not int or type(row.clock_uncertainty_ns) is not int or
      not 0 <= row.clock_uncertainty_ns <= clock_uncertainty_limit(row.source_clock_domain) or
      row.source_timestamp_ns - row.clock_offset_ns != row.mono_ns or
      type(row.source_boot_ns) is not int or type(row.boot_mono_offset_ns) is not int or
      row.source_clock_domain not in ('monotonic', 'boottime', 'unknown-equivalent-at-receipt', 'mono_boot_equivalent_interval_v1') or
      row.source_boot_ns != (row.source_timestamp_ns if row.source_clock_domain == 'boottime' else 0) or
      type(row.source_clock_contract) is not tuple or len(row.source_clock_contract) != 4 or
      not row.left_state_ns <= row.mono_ns <= row.right_state_ns <= row.completed_ns or
      max(row.mono_ns - row.left_state_ns, row.right_state_ns - row.mono_ns) > MAX_GAP_NS or
      not 0 <= row.observed_ns - row.native_apply_ns <= MAX_GAP_NS or
      not 0 <= now_ns - row.native_apply_ns <= MAX_GAP_NS or
      not 0 <= row.observed_ns - row.source_state_ns <= MAX_GAP_NS or
      not 0 <= now_ns - row.source_state_ns <= MAX_GAP_NS or
      not all(_hex(x) for x in (row.source_epoch, row.identity, row.calibration, row.calibration_estimate)) or
      not row.baseline_revision or not row.controller_revision or type(row.context) is not tuple or not row.context or
      row.valid is not True or row.native_allowed is not True or
      any(type(x) not in (int, float) or not math.isfinite(x) for x in
        (row.speed_mps, row.steering_angle_deg, row.yaw_rate, row.roll_rad, *row.left_state, *row.right_state)) or
      type(row.left_state) is not tuple or type(row.right_state) is not tuple or
      len(row.left_state) != 2 or len(row.right_state) != 2):
    raise ValueError('original source clock/context/brackets/validity')
  if type(row.native_required) is not bool:
    raise ValueError('original required/ordinary authority branch missing')
  if row.native_required:
    if (not _hex(row.native_identity) or
        not all(_positive_int(value) for value in (row.native_control_ns,row.native_observed_ns,row.native_expires_ns)) or
        not row.native_observed_ns <= row.native_control_ns <= row.native_apply_ns <= row.observed_ns <= row.native_expires_ns or
        row.native_expires_ns-row.native_observed_ns > 200_000_000):
      raise ValueError('required original native identity/lease unavailable')
  else:
    if (row.native_identity != '' or type(row.native_observed_ns) is not int or
        type(row.native_expires_ns) is not int or row.native_observed_ns != 0 or row.native_expires_ns != 0):
      raise ValueError('ordinary source must preserve empty native identity and zero axis clocks')
    witness = row.ordinary_safety
    if (type(witness) is not tuple or len(witness) != 5 or witness[0] != 'ordinary-panda-current-v1' or
        not _hex(witness[1]) or not _positive_int(witness[2]) or not 0 <= row.observed_ns-witness[2] <= 1_000_000_000 or
        type(witness[3]) is not int or witness[3] < 0 or type(witness[4]) is not tuple or not witness[4]):
      raise ValueError('ordinary original CP/Panda current witness unavailable')
  contract = OriginalSourceClockContract(*row.source_clock_contract)
  contract.validate()
  if contract.source_domain != row.source_clock_domain:
    raise ValueError('original source clock contract mismatch')
  if row.source_clock_domain == 'monotonic' and row.clock_offset_ns != 0:
    raise ValueError('MONO source cannot be BOOT adjusted')
  if row.source_clock_domain == 'boottime' and row.clock_offset_ns != row.boot_mono_offset_ns:
    raise ValueError('BOOT source bridge mismatch')
  if row.source_clock_domain == 'unknown-equivalent-at-receipt' and (
      row.clock_offset_ns != 0 or abs(row.boot_mono_offset_ns) > row.clock_uncertainty_ns):
    raise ValueError('unknown source requires receipt-specific clock equivalence')
  if row.source_clock_domain == 'mono_boot_equivalent_interval_v1':
    from openpilot.starpilot.flm.producer_clock_equivalence import ClockEquivalence, source_interval
    if type(row.clock_equivalence) is not tuple or len(row.clock_equivalence) != 6:
      raise ValueError('original three-sample clock witness missing')
    witness = ClockEquivalence(*row.clock_equivalence)
    lower, upper = source_interval(row.source_timestamp_ns, row.received_ns, row.observed_ns, witness)
    source_interval(row.source_timestamp_ns, row.received_ns, row.completed_ns, witness)
    if (row.clock_offset_ns != 0 or row.mono_ns != row.source_timestamp_ns or row.source_boot_ns != 0 or
        row.clock_uncertainty_ns != witness.uncertainty_ns or
        (row.source_lower_ns, row.source_upper_ns) != (lower, upper) or
        not row.left_state_ns <= lower <= upper <= row.right_state_ns or
        type(row.clock_generation) is not int or row.clock_generation < 0):
      raise ValueError('original clock interval/source bracket mismatch')
  for vector in (row.angular_velocity_device, row.orientation_ned, row.calibration_rpy):
    if type(vector) is not tuple or len(vector) != 3 or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
      raise ValueError('original pose/vehicle-frame primitives')
  weight = 0.0 if row.left_state_ns == row.right_state_ns else (row.mono_ns - row.left_state_ns) / (row.right_state_ns - row.left_state_ns)
  expected = tuple(a + (b - a) * weight for a, b in zip(row.left_state, row.right_state, strict=True))
  if expected != (row.speed_mps, row.steering_angle_deg):
    raise ValueError('original state interpolation mismatch')


class OriginalSourceCapture:
  def __init__(self):
    self.rows = []
    self.context = None
    self.reason = 'empty'
    self.rejected = 0
    self.sealed = False
    self.epoch = 0
    self.segments = []
    self.epoch_start_ns = self.epoch_cutoff_ns = 0
    self.parent_epoch_receipt = None

  def reset(self, reason, *, hard=False):
    self.reason = reason
    if hard:
      self.rows.clear()
      self.context = None
      self.sealed = False
      self.segments.clear()
      self.epoch += 1

  def begin_epoch(self, receipt):
    """Consume the shared parent's fixed experiment boundary, never fit result.

    The parent must authenticate this boundary from original source records;
    this bookkeeping receipt itself grants no native/plant/current authority.
    """
    required = {'epoch_id', 'start_ns', 'cutoff_ns', 'identity', 'source_epoch', 'baseline_revision'}
    if type(receipt) is not dict or set(receipt) != required or not all(_positive_int(receipt[x]) for x in ('start_ns', 'cutoff_ns')):
      raise ValueError('original shared epoch receipt shape')
    if (type(receipt['epoch_id']) is not str or not 1 <= len(receipt['epoch_id']) <= 96 or
        not 0 < receipt['cutoff_ns'] - receipt['start_ns'] <= MAX_CAPTURE_SPAN_NS or
        not _hex(receipt['identity']) or not _hex(receipt['source_epoch']) or not receipt['baseline_revision']):
      raise ValueError('original shared epoch receipt bounds')
    if self.parent_epoch_receipt == receipt:
      return
    if self.epoch_cutoff_ns and receipt['start_ns'] < self.epoch_cutoff_ns:
      raise ValueError('cannot reselect an existing original experiment')
    self.reset('previous original experiment closed unqualified at fixed cutoff', hard=True)
    self.epoch_start_ns, self.epoch_cutoff_ns = receipt['start_ns'], receipt['cutoff_ns']
    self.parent_epoch_receipt = dict(receipt)

  def add(self, row, now_ns, *, retained_raw_rows=0):
    try:
      validate_pose(row, now_ns)
      if self.context is not None and row.context != self.context:
        self.reset('original source binding changed', hard=True)
      if self.rows and row.mono_ns <= self.rows[-1].mono_ns:
        raise ValueError('original source duplicate or reorder')
      if not self.epoch_start_ns:
        self.epoch_start_ns = row.mono_ns
        self.epoch_cutoff_ns = row.mono_ns + MAX_CAPTURE_SPAN_NS
      if row.mono_ns >= self.epoch_cutoff_ns:
        # Outcome-independent source clock cutoff. Do not preserve an expired
        # prefix forever after tape eviction or a row-budget freeze. A shared
        # parent receipt requires its NEXT authenticated future boundary.
        cutoff = self.epoch_cutoff_ns
        if self.parent_epoch_receipt is not None:
          self.sealed = True
          self.reason = 'awaiting next original shared parent epoch receipt'
          return False
        start = cutoff + ((row.mono_ns - cutoff) // MAX_CAPTURE_SPAN_NS) * MAX_CAPTURE_SPAN_NS
        self.reset('expired original experiment discarded unqualified at fixed cutoff', hard=True)
        self.epoch_start_ns, self.epoch_cutoff_ns = start, start + MAX_CAPTURE_SPAN_NS
      if row.mono_ns < self.epoch_start_ns:
        raise ValueError('pose predates original experiment boundary')
      receipt = self.parent_epoch_receipt
      if receipt is not None and (row.identity, row.source_epoch, row.baseline_revision) != (
          receipt['identity'], receipt['source_epoch'], receipt['baseline_revision']):
        raise ValueError('original shared epoch source mismatch')
      if self.sealed:
        return False
      if type(retained_raw_rows) is not int or not 0 <= retained_raw_rows <= MAX_SOURCE_POSES:
        raise ValueError('shared raw-row budget count')
      if len(self.rows) >= MAX_RETAINED_SOURCE_POSES or len(self.rows) + retained_raw_rows >= MAX_SOURCE_POSES:
        # Freeze the original prefix. Never silently rotate/drop rows.
        self.sealed = True
        self.reason = 'original source row budget frozen'
        return False
      if not self.rows or row.mono_ns - self.rows[-1].mono_ns > MAX_GAP_NS:
        if len(self.segments) >= MAX_PACKET_WINDOWS:
          self.sealed = True
          self.reason = 'original source segment budget frozen'
          return False
        self.segments.append(row.mono_ns)
      self.context = row.context
      self.rows.append(row)
      self.reason = 'unqualified original source captured'
      return True
    except (AttributeError, TypeError, ValueError, OverflowError) as error:
      self.rejected += 1
      self.reset(str(error))
      return False


def export_packet(adapter, collector, windows, now_ns):
  """Whole selected chronology, explicitly bounded and never thinned.

  Native decoding/replay and train/heldout lag qualification remain required in
  a separate child. The command tape carries the original native leases and CAN
  witnesses; merely exporting those witnesses does not verify the plant.
  """
  capture = getattr(collector, 'original_source_capture', None)
  if capture is None or not capture.rows:
    raise ValueError('original source capture unavailable')
  retained_legacy = len(getattr(collector, 'raw_points', ())) + sum(len(block.samples) for block in getattr(collector, 'blocks', ()))
  retained_pending = len(getattr(collector, '_original_source_pose_queue', ()))
  if len(capture.rows) + retained_legacy + retained_pending > MAX_SOURCE_POSES:
    raise ValueError('shared 5120 raw-row retention bound')
  if type(windows) is not tuple or len(windows) != 1 or not _positive_int(now_ns):
    raise ValueError('original source window bound')
  previous = 0
  for window in windows:
    if type(window) is not tuple or len(window) != 2:
      raise ValueError('original source window shape')
    a, b = window
    if not all(_positive_int(x) for x in window) or not previous < a < b <= now_ns or now_ns - a > MAX_PACKET_NS:
      raise ValueError('original source window clock/order/duration')
    previous = b
  if windows[-1][1] - windows[0][0] > MAX_PACKET_NS:
    raise ValueError('original source span bound')
  engine = adapter.replay
  if engine.raw_mode is not True:
    raise ValueError('original independent capture requires actual raw replay mode')
  if engine.failed:
    raise ValueError('actual replay recorder failed')
  # Single bounded pass over existing records; no new command history buffer.
  if len(engine.records) > 60000 or len(engine.emissions.events) > 60000:
    raise ValueError('actual recorder retention bound')
  frames = tuple(frame for frame in engine.records if any(a <= frame.control_ns <= b for a, b in windows))
  if not 1 <= len(frames) <= MAX_PACKET_FRAMES:
    raise ValueError('original source frame bound')
  frame_stamps = {frame.control_ns for frame in frames}
  if len(frame_stamps) != len(frames):
    raise ValueError('actual duplicate frame clock')
  for a, b in windows:
    selected = tuple(frame for frame in frames if a <= frame.control_ns <= b)
    if not selected or selected[0].control_ns != a or selected[-1].control_ns != b:
      raise ValueError('original whole source window missing boundary')
    for left, right in zip(selected, selected[1:], strict=False):
      if not 0 < right.control_ns - left.control_ns <= MAX_GAP_NS or right.frame_sequence != left.frame_sequence + 1:
        raise ValueError('actual source frame gap')
  checkpoints = tuple((stamp, checkpoint.revision, checkpoint.values) for stamp, checkpoint in engine.checkpoints
    if any(a <= stamp <= b for a, b in windows))
  if any(a not in {stamp for stamp, _, _ in checkpoints} for a, _ in windows):
    raise ValueError('original source checkpoint prefix required')
  cp_bytes = engine.factory.cp.as_builder().to_bytes()
  if not 0 < len(cp_bytes) <= 131072:
    raise ValueError('original source CarParams bound')
  tape = engine.emissions.snapshot(windows)
  if not tape['events'] or len(tape['events']) > MAX_PACKET_FRAMES or len(tape['carriers']) > MAX_PACKET_FRAMES:
    raise ValueError('actual command tape bound')
  last_apply = 0
  for event in tape['events']:
    if (event['control_ns'] not in frame_stamps or event['identity'] != adapter.identity or
        event['source_epoch'] != adapter.source_epoch or event['baseline_revision'] != adapter.baseline_revision or
        event['native_allowed'] is not True or type(event['native_required']) is not bool or
        event['native_required'] and not _hex(event['native_identity']) or
        not event['native_required'] and (event['native_identity'] != '' or event['native_observed_ns'] != 0 or event['native_expires_ns'] != 0) or
        event['valid'] is not True or event['active'] is not True or event['override'] or event['fault'] or
        not event['control_ns'] <= event['apply_ns'] <= now_ns or
        event['native_required'] and not 0 < event['native_observed_ns'] <= event['control_ns'] <= event['apply_ns'] <= event['native_expires_ns']):
      raise ValueError('actual command tape source/native invalid')
    if last_apply and event['apply_ns'] <= last_apply:
      raise ValueError('actual command tape clock reorder')
    last_apply = event['apply_ns']
  rows = tuple(row for row in capture.rows if any(a <= row.mono_ns <= b for a, b in windows))
  if not rows or len(rows) > MAX_SOURCE_POSES:
    raise ValueError('original pose row bound')
  for row in rows:
    # Historical receipt must remain original; validate at bracket completion,
    # then enforce separate job age rather than falsely renewing that receipt.
    validate_pose(row, row.completed_ns)
    if row.completed_ns > now_ns:
      raise ValueError('original completion stamp is future to packet export')
    if (row.identity != adapter.identity or row.source_epoch != adapter.source_epoch or
        row.baseline_revision != adapter.baseline_revision or now_ns - row.mono_ns > MAX_PACKET_NS or
        (not row.native_required and row.ordinary_safety[1] != adapter.cp_sha256)):
      raise ValueError('original pose source binding')
  archive = getattr(adapter, 'original_context_receipts', None)
  if archive is None:
    raise ValueError('original context-only receipt archive required')
  from openpilot.starpilot.flm.normalized_trace_archive import ArchiveBuilder,SOURCE_VERSION
  trace_builder = ArchiveBuilder()
  context_receipts = archive.export(frames,trace_builder=trace_builder)
  payload = {'version': SOURCE_PACKET_VERSION, 'encoding': SOURCE_PACKET_ENCODING, 'capture_epoch': capture.epoch,
    'capture_epoch_start_ns': capture.epoch_start_ns, 'capture_epoch_cutoff_ns': capture.epoch_cutoff_ns,
    'parent_epoch_receipt': capture.parent_epoch_receipt, 'source_segment_starts': tuple(capture.segments), 'qualified': False,
    'plant_lag_ns': None, 'source_windows': (), 'replay_prefix': windows[0], 'source_poses': tuple(asdict(row) for row in rows),
    'context_receipts': context_receipts,
    'actual_frames': tuple(trace_builder.record(frame) for frame in frames), 'actual_command_tape': tape,
    'original_replay_source': {'cp_bytes_hex': cp_bytes.hex(), 'cp_sha256': adapter.cp_sha256,
      'controller_revision': adapter.controller_revision, 'model_revision': engine.model_revision,
      'raw_mode': engine.raw_mode, 'mode': str(engine.factory.mode),
      'turn_assist': engine.factory.turn_assist, 'manual_baseline': engine.factory.manual_baseline,
      'manual_source_epoch': engine.factory.manual_source_epoch, 'checkpoints': checkpoints,
       **({'manual_kp': engine.factory.manual_kp} if engine.factory.manual_kp is not None else {})},
    'counts': {'source_poses': len(rows), 'frames': len(frames), 'emissions': len(tape['events']),
      'carriers': len(tape['carriers']), 'windows': len(windows), 'span_ns': windows[-1][1] - windows[0][0]},
    'retained_raw_counts': {'legacy': retained_legacy, 'pending': retained_pending, 'original': len(capture.rows)},
    'limits': {'source_poses': MAX_RETAINED_SOURCE_POSES, 'shared_raw_rows': MAX_SOURCE_POSES, 'frames': MAX_PACKET_FRAMES, 'windows': MAX_PACKET_WINDOWS,
      'compressed_bytes': MAX_FIT_SNAPSHOT_BYTES, 'raw_bytes': MAX_FIT_RAW_BYTES, 'duration_ns': MAX_PACKET_NS}}
  if trace_builder.rows:
    payload['version'] = SOURCE_VERSION
    payload['trace_archive'] = trace_builder.literal()
  feedback_archive = getattr(adapter,'original_feedback_archive',None)
  if trace_builder.rows and feedback_archive is None:
    raise ValueError('normalized traces require explicit feedback source protocol')
  if feedback_archive is not None:
    payload['feedback_observations'] = feedback_archive.export(payload)
  return pack_source_packet(payload)


def _source_primitives(value):
  """Lossless known witness schema; classless primitive transport only.

  Context archive asdict preserves TorqueProjection NamedTuple. Its explicit
  field dictionary is the existing reversible projection schema; CAN bytes,
  Float64 values, tuple/list distinctions and checkpoint tuples remain exact.
  No arbitrary class/tag/object serializer is accepted.
  """
  from opendbc.car.steering_projection import TorqueProjection, projection_to_dict
  kind = type(value)
  if isinstance(value, TorqueProjection):
    primitive = projection_to_dict(value)
    if primitive is None:
      raise ValueError('invalid original native projection schema')
    return _source_primitives(primitive)
  if value is None or kind in (bool,int,str,bytes):
    return value
  if kind is float:
    if not math.isfinite(value):
      raise ValueError('nonfinite original source packet')
    return value
  if kind in (tuple,list):
    rows = (_source_primitives(child) for child in value)
    return tuple(rows) if kind is tuple else list(rows)
  if kind is dict and all(type(key) is str for key in value):
    return {key:_source_primitives(child) for key,child in value.items()}
  raise ValueError('original source packet requires known primitive/witness types')


from openpilot.starpilot.flm.normalized_trace_archive import SOURCE_VERSION as NORMALIZED_SOURCE_VERSION


def pack_source_packet(payload):
  if (type(payload) is not dict or payload.get('version') not in (SOURCE_PACKET_VERSION, NORMALIZED_SOURCE_VERSION) or
      payload.get('encoding') != SOURCE_PACKET_ENCODING or payload.get('qualified') is not False):
    raise ValueError('explicit original source packet codec version required')
  primitive = _source_primitives(payload)
  if 'feedback_observations' in primitive:
    from openpilot.starpilot.flm.primitive_source_memo import intern_primitive_graph
    from openpilot.starpilot.flm.normalized_trace_archive import reserve_for_packet
    reserve = reserve_for_packet(primitive)
    primitive = intern_primitive_graph((primitive,bytes(reserve)))[0] if reserve else intern_primitive_graph(primitive)
  return pack_primitive_snapshot(primitive)


def unpack_source_packet(wire):
  payload = unpack_primitive_snapshot(wire)
  if (type(payload) is not dict or payload.get('version') not in (SOURCE_PACKET_VERSION, NORMALIZED_SOURCE_VERSION) or
      payload.get('encoding') != SOURCE_PACKET_ENCODING or payload.get('qualified') is not False):
    raise ValueError('unsupported original source packet codec/version')
  if 'feedback_observations' in payload:
    from openpilot.starpilot.flm.primitive_source_memo import independent_primitive_graph
    from openpilot.starpilot.flm.normalized_trace_archive import reserve_for_packet
    return independent_primitive_graph(payload,reserved_bytes=reserve_for_packet(payload))
  return _source_primitives(payload)


def source_packet_cost(wire):
  """Explicit source-only costs; outer payload+derived replay counts separately."""
  import zlib
  unpack_source_packet(wire)
  decoder = zlib.decompressobj()
  remaining, raw_bytes = wire, 0
  while not decoder.eof:
    chunk = decoder.decompress(remaining, 65536)
    raw_bytes += len(chunk)
    remaining = decoder.unconsumed_tail
    if raw_bytes > MAX_FIT_RAW_BYTES or not chunk and not remaining and not decoder.eof:
      raise ValueError('original source raw cost bound')
  return {'primitive_raw_bytes':raw_bytes,
    'compressed_bytes':len(wire), 'base64_bytes':4*((len(wire)+2)//3),
    'compressed_cap':MAX_FIT_SNAPSHOT_BYTES, 'raw_cap':MAX_FIT_RAW_BYTES}


def associate_original_command(events, row, lag_ns):
  """Frozen-lag child join at BOTH clock bounds, never a zero-lag plant claim.

  This pure association does no native replay/fit/admission. The child must
  reject an ambiguous row/experiment wholesale under its frozen protocol.
  """
  if not isinstance(row, OriginalSourcePose) or type(lag_ns) is not int or not 0 <= lag_ns <= 500_000_000:
    raise ValueError('typed original pose and frozen plant lag required')
  validate_pose(row, row.completed_ns)
  if row.source_clock_domain == 'mono_boot_equivalent_interval_v1':
    lower, upper = row.source_lower_ns - lag_ns, row.source_upper_ns - lag_ns
  else:
    lower = upper = row.mono_ns - lag_ns
  if type(events) not in (tuple, list) or not 1 <= len(events) <= MAX_PACKET_FRAMES:
    raise ValueError('bounded original command tape required')
  previous = 0
  left = right = None
  for event in events:
    stamp = event['apply_ns'] if type(event) is dict else event.apply_ns
    if type(stamp) is not int or stamp <= previous:
      raise ValueError('original command tape clock order')
    previous = stamp
    if stamp <= lower:
      left = event
    if stamp <= upper:
      right = event
  if left is None or right is None:
    raise ValueError('original command tape source interval unavailable')
  stamp_left = left['apply_ns'] if type(left) is dict else left.apply_ns
  stamp_right = right['apply_ns'] if type(right) is dict else right.apply_ns
  if stamp_left != stamp_right or not 0 <= upper - stamp_right <= 30_000_000:
    raise ValueError('ambiguous or stale original command at source interval bounds')
  return left
