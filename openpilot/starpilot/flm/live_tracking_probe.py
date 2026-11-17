"""Bounded original-prefix controller/native secants; numerical evidence only.

No live controller is changed and no driving permission or causal plant model
is produced. Legacy model mode uses measured physical units. Source-context mode uses
normalized native-command secants solely for proposal direction/visibility;
it does NOT claim a plant prediction or physical effect magnitude.
"""

from dataclasses import dataclass, fields
import math
import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from openpilot.starpilot.flm.fullrate_collector import ControlEvidence

from openpilot.starpilot.flm.canonical_surface_adapter import GROUPS, PROBE_GROUPS, probe_names, coordinate_bounds
from openpilot.starpilot.flm.live_trial_contract import MAX_STEP, SOURCE_EXPERIMENT_STEP

_OWNER = object()
MAX_FRESH_NS = 100_000_000


def _finite(value):
  return (type(value) is int or type(value) is float) and math.isfinite(value)


@dataclass(frozen=True)
class ProbeCoordinate:
  name: str
  baseline_coordinate: float
  minus_coordinate: float
  plus_coordinate: float
  baseline_delivered: float
  minus_delivered: float
  plus_delivered: float
  physical_derivative: float  # (m/s^2)/canonical coordinate, with measured sign.
  native_separated: bool
  max_step_physical_delta: float  # m/s^2 at MAX_STEP, NOT a derivative.
  status: str


@dataclass(frozen=True)
class ProbeResult:
  binding: object
  control_ns: int
  apply_ns: int
  profile_sha256: str
  model_revision: str
  rows: tuple[ProbeCoordinate, ...]
  controller_domain: str
  group: str
  _seal: object


def validate_probe_result(value, binding=None):
  from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding
  from openpilot.starpilot.flm.pid_stages import SUPPORTED

  if (
    type(value) is not ProbeResult
    or type(value._seal) is not tuple
    or len(value._seal) != 2
    or value._seal[0] is not _OWNER
    or type(value.binding) is not LiveTrialBinding
  ):
    raise ValueError('owned actual-controller probe result required')
  value.binding.validate()
  if (
    binding is not None
    and value.binding != binding
    or type(value.controller_domain) is not str
    or value.controller_domain not in ('torque', 'torque_pid')
    or type(value.rows) is not tuple
    or any(type(row) is not ProbeCoordinate or type(row.name) is not str for row in value.rows)
    or type(value.group) is not str
    or value.group not in PROBE_GROUPS
    or not 1 <= len(value.rows) <= len(PROBE_GROUPS[value.group])
    or tuple(row.name for row in value.rows) != probe_names(tuple(row.name for row in value.rows))
    or any(row.name not in PROBE_GROUPS[value.group] or value.controller_domain == 'torque_pid' and row.name not in SUPPORTED for row in value.rows)
    or any(type(stamp) is not int for stamp in (value.control_ns, value.apply_ns))
    or not 0 < value.control_ns <= value.apply_ns <= value.control_ns + 30_000_000
    or any(type(text) is not str or len(text) != 64 or any(c not in '0123456789abcdef' for c in text) for text in (value.profile_sha256, value.model_revision))
    or value.model_revision != value.binding.physical_model_revision
  ):
    raise ValueError('owned actual-controller probe result required')
  for row in value.rows:
    if (
      type(row) is not ProbeCoordinate
      or type(row.status) is not str
      or row.status not in ('separated', 'quantized', 'native_unavailable')
      or type(row.native_separated) is not bool
      or any(
        not _finite(number)
        for number in (
          row.baseline_coordinate,
          row.minus_coordinate,
          row.plus_coordinate,
          row.baseline_delivered,
          row.minus_delivered,
          row.plus_delivered,
          row.physical_derivative,
          row.max_step_physical_delta,
        )
      )
    ):
      raise ValueError('finite exact native coordinate probe required')
    low, high = coordinate_bounds(row.name)
    if (
      not low <= row.minus_coordinate <= row.baseline_coordinate <= row.plus_coordinate <= high
      or not row.minus_coordinate < row.plus_coordinate
      or max(row.baseline_coordinate - row.minus_coordinate, row.plus_coordinate - row.baseline_coordinate) > MAX_STEP + 1e-15
      or row.plus_coordinate - row.minus_coordinate > 2 * MAX_STEP + 1e-15
      or row.max_step_physical_delta != abs(row.physical_derivative) * MAX_STEP
      or row.native_separated is not (row.status == 'separated')
      or row.native_separated is not (row.minus_delivered != row.plus_delivered)
      or not row.native_separated
      and (row.physical_derivative != 0 or row.max_step_physical_delta != 0)
    ):
      raise ValueError('bounded separated native physical secant required')
  if value._seal[1] != _probe_key(value):
    raise ValueError('owned probe result contents changed')
  return value


def _probe_key(value):
  from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding

  return (
    _record(value.binding, LiveTrialBinding),
    value.control_ns,
    value.apply_ns,
    value.profile_sha256,
    value.model_revision,
    value.controller_domain,
    value.group,
    tuple(_record(row, ProbeCoordinate) for row in value.rows),
  )


def _primitive(value, depth=0):
  """Fresh exact bytes for source mutation detection; never invokes reducers."""
  if depth == 0:
    from openpilot.starpilot.flm.native_primitives import FALLBACK, try_native
    exact = try_native('probe_source_key', value)
    if exact is not FALLBACK:
      return exact
  if depth > 40:
    raise ValueError('bounded probe source graph')
  kind = type(value)
  if value is None:
    return ('none',)
  if kind is bool or kind is int or kind is str or kind is bytes:
    if kind is int and value.bit_length() > 512:
      raise ValueError('bounded probe source integer')
    if (kind is str or kind is bytes) and len(value) > 131072:
      raise ValueError('bounded probe source scalar')
    return kind.__name__, value
  if kind is float and math.isfinite(value):
    return 'float64', struct.pack('!d', value)
  if kind is tuple or kind is list:
    if len(value) > 8192:
      raise ValueError('bounded probe source sequence')
    return kind.__name__, tuple(_primitive(child, depth + 1) for child in value)
  if kind is dict and len(value) <= 8192 and all(type(key) is str for key in value):
    return 'dict', tuple((key, _primitive(value[key], depth + 1)) for key in sorted(value))
  raise ValueError('exact primitive probe source required')


def _record(value, cls):
  if type(value) is not cls:
    raise ValueError('exact source record required')
  # No asdict/deepcopy/custom reducer. Fields are individually type-checked by
  # the enclosing domain validation before this complete fresh key is formed.
  return tuple((field.name, _primitive(getattr(value, field.name))) for field in fields(cls))


def _physical_row(name, current, minus, plus, base, low, high, slope, status):
  derivative = -slope * (high - low) / (plus - minus) if status == 'separated' else 0.0
  return ProbeCoordinate(name, current, minus, plus, base, low, high, derivative, status == 'separated', abs(derivative) * MAX_STEP, status)


def _projection_literal(witness):
  from opendbc.car.steering_projection import TorqueProjection, projection_to_dict

  if type(witness) is not TorqueProjection:
    raise ValueError('exact actual native projection required')
  # TorqueProjection is an exact NamedTuple, not a dataclass. Read builtin
  # tuple slots before the stock converter can examine unknown field values.
  _primitive(tuple(tuple.__iter__(witness)))
  value = projection_to_dict(witness)
  if type(value) is not dict:
    raise ValueError('bounded actual native projection required')
  _primitive(value)
  return value


def _factory_configuration(factory):
  """Close mutable constructor metadata BEFORE coercions or native CP access."""
  from capnp.lib.capnp import _DynamicStructReader
  from opendbc.car import structs
  from openpilot.starpilot.flm.controller_replay import SelectedControllerFactory
  from openpilot.starpilot.flm.pid_controller_replay import PidControllerFactory
  from openpilot.starpilot.lateral.controller_selection import ControllerMode

  if type(factory) is SelectedControllerFactory:
    mode, policy = factory.mode, factory.policy
    if type(mode) is not ControllerMode or type(mode.value) is not str or type(policy) not in (str, type(None)):
      raise ValueError('exact selected constructor mode/policy required')
    mode, policy = mode.value, str(policy)
  elif type(factory) is PidControllerFactory:
    mode, policy = factory.mode, None
    if type(mode) is not str or mode != 'native':
      raise ValueError('exact stock PID constructor mode required')
  else:
    raise ValueError('exact registered constructor required')
  if (
    type(factory.cp) is not _DynamicStructReader
    or factory.cp.schema != structs.CarParams.schema
    or type(factory.turn_assist) is not bool
    or type(factory.manual_baseline) is not tuple
    or type(factory.manual_source_epoch) is not str
  ):
    raise ValueError('closed native constructor inputs required')
  _primitive(factory.manual_baseline)
  return mode, policy


# Optional worker only: exact complete bytes are reconstructed on EVERY lookup.
# Values are immutable snapshot (revision, numeric tuple) only, never permission.
_PREFIX_CACHE = {}
_PREFIX_CACHE_MAX_KEY = 2 * 1024 * 1024


def _trace_key(trace):
  from openpilot.starpilot.flm.effect_trace import FullEffectTrace
  from openpilot.starpilot.flm.full_surface_profile import FullSurfaceEnvelope

  if trace is None:
    return None
  if type(trace) is not FullEffectTrace or type(trace.envelope) is not FullSurfaceEnvelope:
    raise ValueError('exact actual full30 trace required')
  value = tuple(
    (field.name, _record(trace.envelope, FullSurfaceEnvelope) if field.name == 'envelope' else _primitive(getattr(trace, field.name)))
    for field in fields(FullEffectTrace)
  )
  trace.validate()
  return value


def _frame_key(frame, binding):
  from openpilot.starpilot.flm.controller_replay import ReplayFrame

  if type(frame) is not ReplayFrame:
    raise ValueError('exact original prefix frame required')
  if frame.effect_trace is not None:
    _trace_key(frame.effect_trace)
    envelope = frame.effect_trace.envelope
    if (
      envelope.cp_sha256,
      envelope.controller_revision,
      envelope.source_epoch,
      envelope.reset_generation,
      envelope.selected_baseline_revision,
      envelope.persistent_source_epoch,
    ) != (
      binding.cp_sha256,
      binding.controller_revision,
      binding.source_epoch,
      binding.reset_generation,
      binding.baseline_revision,
      binding.persistent_source_epoch,
    ):
      raise ValueError('original full30 prefix source binding differs')
  return tuple(
    (field.name, _trace_key(frame.effect_trace) if field.name == 'effect_trace' else _primitive(getattr(frame, field.name))) for field in fields(ReplayFrame)
  )


def _consumed_coordinates(control, binding):
  """Actual same-frame owner map; never substitute a target or stored prior."""
  from openpilot.starpilot.flm.live_trial_contract import full_coordinates
  from openpilot.starpilot.flm.canonical_surface_adapter import NAMES

  live = getattr(control, 'live_applied_coordinates', ())
  if type(live) is not tuple:
    raise ValueError('exact actual consumed live full30 required')
  trace = control.effect_trace
  if trace is not None:
    _trace_key(trace)
    envelope = trace.envelope
    if (
      envelope.cp_sha256,
      envelope.controller_revision,
      envelope.source_epoch,
      envelope.reset_generation,
      envelope.selected_baseline_revision,
      envelope.persistent_source_epoch,
    ) != (
      binding.cp_sha256,
      binding.controller_revision,
      binding.source_epoch,
      binding.reset_generation,
      binding.baseline_revision,
      binding.persistent_source_epoch,
    ):
      raise ValueError('actual consumed full30 source differs')
  if live:
    if len(live) != len(NAMES) or any(not _finite(value) for value in live):
      raise ValueError("exact finite actual consumed live vector30 required")
    rows = tuple(zip(NAMES, live, strict=True))
    full_coordinates(rows, binding.baseline_revision)
    if trace is not None and _primitive(rows) != _primitive(trace.applied):
      raise ValueError('two actual full30 owner receipts disagree')
    return rows, True
  if trace is not None:
    return trace.applied, True
  return tuple((name, 0.0) for name in NAMES), False


def _consumed_source(rows, binding):
  from openpilot.starpilot.flm.selected_effects import EffectReplaySource

  values = dict(rows)
  name = GROUPS['ff.left'][0]
  return EffectReplaySource.from_coordinates({name: values[name]}, binding.baseline_revision, accumulated=values)


def _control_key(control, binding):
  from openpilot.starpilot.flm.fullrate_collector import ControlEvidence
  from openpilot.starpilot.flm.runtime_adapter_registry import vm_regime, digest

  if type(control) is not ControlEvidence:
    raise ValueError('exact original100Hz control receipt required')
  key = tuple(
    (field.name, _trace_key(control.effect_trace) if field.name == 'effect_trace' else _primitive(getattr(control, field.name)))
    for field in fields(ControlEvidence)
  )
  if (
    any(
      type(stamp) is not int
      for stamp in (
        control.state_ns,
        control.control_ns,
        control.source_receipt_ns,
        control.native_observed_ns,
        control.native_expires_ns,
        control.snapshot_sequence,
      )
    )
    or any(type(flag) is not bool for flag in (control.native_required, control.native_allowed))
    or not 0 < control.state_ns <= control.control_ns <= control.state_ns + 30_000_000
    or not 0 < control.source_receipt_ns <= control.control_ns <= control.source_receipt_ns + 500_000_000
    or (control.identity, control.source_epoch, control.baseline_revision, control.controller_revision, control.native_identity, control.native_required)
    != (binding.identity, binding.source_epoch, binding.baseline_revision, binding.controller_revision, binding.native_identity, binding.native_required)
  ):
    raise ValueError('original100Hz source/controller/native receipt differs')
  anchor = getattr(control, 'vehicle_model_anchor', None)
  if (
    type(anchor) is not tuple
    or len(anchor) != 3
    or any(not _finite(value) or value == 0 for value in anchor)
    or digest(vm_regime(dict(control.replay_inputs), anchor)) != binding.vehicle_model_generation
  ):
    raise ValueError('actual original owner vehicle-model anchor differs')
  if control.native_allowed and (
    not binding.native_required
    or not 0 < control.native_observed_ns <= control.control_ns <= control.native_expires_ns <= control.native_observed_ns + 200_000_000
  ):
    raise ValueError('actual original native receipt clock differs')
  if (
    binding.native_required
    and control.enabled
    and control.active
    and control.valid
    and not control.manual
    and not control.override
    and not control.fault
    and not control.native_allowed
  ):
    raise ValueError('actual healthy original native permission unavailable')
  _consumed_coordinates(control, binding)
  return key


def _prefix_before(replay, control, output, binding, factory, prefix_controls: tuple["ControlEvidence", ...] = ()):
  """Actual latest checkpoint through EVERY original tick, at most100 updates.

  The donor is the genuine optional learner recorder. A separate bounded owner
  reconstructs it; the donor actual/probe controllers are never advanced here.
  Native permission/packets are freshly decoded even on a snapshot cache hit.
  """
  from itertools import islice
  import pickle
  from openpilot.starpilot.flm.controller_replay import (
    ControllerReplay,
    Snapshot,
    MAX_FRAME_GAP_NS,
    restore_snapshot,
    capture_snapshot,
    _run,
    same_final_command,
  )
  from openpilot.starpilot.flm.torque_emission_replay import Emission, EmissionHistory
  from openpilot.starpilot.flm.learner_factory import _frame
  from openpilot.starpilot.flm.original_context_receipts import _same_original_frame
  from openpilot.starpilot.flm.torque_adapter import Objective
  from opendbc.car.steering_projection import projection_to_dict

  if (
    type(replay) is not ControllerReplay
    or type(replay.emissions) is not EmissionHistory
    or replay.factory is not factory
    or replay.failed
    or not replay.raw_mode
    or replay.identity != binding.identity
    or replay.baseline_revision != binding.baseline_revision
    or type(replay.objective) is not Objective
  ):
    raise ValueError('actual original recorder/selected constructor required')

  def source():
    checkpoints = [(stamp, snapshot) for stamp, snapshot in replay.checkpoints if stamp <= control.control_ns]
    if not checkpoints:
      raise ValueError('genuine original prefix checkpoint missing')
    start, checkpoint = checkpoints[-1]
    if type(checkpoint) is not Snapshot or not 0 <= control.control_ns - start <= 1_000_000_000:
      raise ValueError('bounded actual prefix checkpoint required')
    frames = tuple(reversed(tuple(row for row in islice(reversed(replay.records), 201) if start <= row.control_ns <= control.control_ns)))
    if (
      not 1 <= len(frames) <= 100
      or frames[0].control_ns != start
      or frames[-1].control_ns != control.control_ns
      or any(
        right.frame_sequence != left.frame_sequence + 1 or not 0 < right.control_ns - left.control_ns <= MAX_FRAME_GAP_NS
        for left, right in zip(frames, frames[1:], strict=False)
      )
    ):
      raise ValueError('complete bounded original100Hz prefix required')
    from opendbc.car.car_helpers import interfaces

    mode, policy = _factory_configuration(factory)
    interface = interfaces.get(str(factory.cp.carFingerprint))
    if factory.interface_class is not interface or interface is None:
      raise ValueError("exact current registered constructor interface required")
    constructor = (interface.__module__, interface.__qualname__, policy)
    domain = 'torque_pid' if factory.cp.lateralTuning.which() == 'pid' else 'torque'
    if any(row.controller_domain != domain for row in frames):
      raise ValueError('original controller prefix domain differs')
    frame_keys = tuple(_frame_key(row, binding) for row in frames)
    if type(prefix_controls) is not tuple or len(prefix_controls) != len(frames) or any(type(row) is not type(control) for row in prefix_controls):
      raise ValueError('every actual100Hz source control receipt required')
    control_keys = tuple(_control_key(row, binding) for row in prefix_controls)
    if any(
      type(row.turn_assist) is not bool
      or row.turn_assist is not factory.turn_assist
      or (row.controller_mode, row.policy) != (control.controller_mode, control.policy)
      for row in prefix_controls
    ):
      raise ValueError("actual prefix constructor/policy/assist configuration changed")
    if (
      _primitive(prefix_controls[0].replay_snapshot) != _primitive(checkpoint.values)
      or prefix_controls[0].snapshot_sequence <= 0
      or any(row.snapshot_sequence != prefix_controls[0].snapshot_sequence or row.generation != control.generation for row in prefix_controls)
      or any(row.replay_snapshot for row in prefix_controls[1:])
      or any(not _same_original_frame(frame, _frame(row)) for frame, row in zip(frames, prefix_controls, strict=True))
      or _control_key(prefix_controls[-1], binding) != _control_key(control, binding)
    ):
      raise ValueError('complete genuine checkpoint/source-control prefix differs')
    if not _same_original_frame(frames[-1], _frame(control)):
      raise ValueError('current endpoint differs from original recorded tick')
    events = tuple(reversed(tuple(row for row in islice(reversed(replay.emissions.events.values()), 201) if start <= row.control_ns <= control.control_ns)))
    if not events or events[-1].apply_ns != output.apply_ns or events[-1].control_ns != control.control_ns:
      raise ValueError('actual endpoint native chronology incomplete')
    native = []
    for event in events:
      if type(event) is not Emission:
        raise ValueError('exact actual native prefix emission required')
      if (event.identity, event.source_epoch, event.baseline_revision, event.native_identity, event.native_required) != (
        binding.identity,
        binding.source_epoch,
        binding.baseline_revision,
        binding.native_identity,
        binding.native_required,
      ):
        raise ValueError('original native prefix source differs')
      projection = projection_to_dict(event.witness)
      if projection is None:
        raise ValueError('original native prefix projection missing')
      body = {field.name: projection if field.name == 'witness' else getattr(event, field.name) for field in fields(Emission)}
      _primitive(body)
      native.append(body)
    first, last = events[0].witness.frame + 1, events[-1].witness.frame + 1
    if not 0 <= last - first <= 200:
      raise ValueError('bounded actual native carrier prefix required')
    carriers = tuple(reversed(tuple((stamp, *row) for stamp, row in islice(reversed(replay.emissions.carriers.items()), 301) if first <= stamp <= last)))
    native_data = {'events': tuple(native), 'carriers': carriers}
    if _primitive(projection_to_dict(events[-1].witness)) != _primitive(projection_to_dict(output.projection)):
      raise ValueError('endpoint emitted packet differs from recorded prefix')
    key = pickle.dumps(
      (
        _record(checkpoint, Snapshot),
        frame_keys,
        control_keys,
        _primitive(native_data),
        _record(binding, type(binding)),
        factory.cp.as_builder().to_bytes(),
        mode,
        constructor,
        tuple(factory.manual_baseline),
        factory.manual_source_epoch,
        factory.turn_assist,
        _record(replay.objective, Objective),
      ),
      protocol=5,
    )
    return key, start, checkpoint, frames, native_data

  key, start, checkpoint, frames, native_data = source()
  # The authoritative loader rechecks every actual CAN event, all Card carrier
  # ticks, source/native leases, hidden filter state and actual encoder chronology.
  native = EmissionHistory(factory.cp, identity=binding.identity, baseline_revision=binding.baseline_revision)
  native.load(native_data, ((start, control.control_ns),))
  requests = {row.control_ns: row.selected_command for row in frames}
  from openpilot.starpilot.flm.torque_emission_replay import EncoderBranch

  branch = EncoderBranch()
  for event in native.events.values():
    if event.control_ns not in requests:
      raise ValueError('native prefix has no actual controller tick')
    branch.step(event, requests[event.control_ns], actual=True)
  saved = _PREFIX_CACHE.get(key) if len(key) <= _PREFIX_CACHE_MAX_KEY else None
  if saved is None:
    owner = ControllerReplay(
      factory, replay.objective, identity=binding.identity, baseline_revision=binding.baseline_revision, model_revision='live-prefix-numerical-v1'
    )
    from openpilot.starpilot.flm.runtime_adapter_registry import baseline_values
    from openpilot.starpilot.flm.native_baseline import checkpoint_native_valid, static_values

    original = baseline_values(owner.probe)
    static = () if factory.cp.lateralTuning.which() == 'pid' else static_values(owner.probe)
    native_baseline = () if factory.cp.lateralTuning.which() == 'pid' else tuple(owner.probe.native_learning_baseline)
    restore_snapshot(owner.probe, owner.probe_vm, checkpoint)
    if factory.cp.lateralTuning.which() == 'pid':
      if baseline_values(owner.probe) != original:
        raise ValueError('PID prefix changed immutable constructor gains')
    elif (
      static_values(owner.probe) != static
      or tuple(owner.probe.native_learning_baseline) != native_baseline
      or (
        bool(baseline_values(owner.probe) != original or tuple(owner.probe.native_learning_provenance))
        if factory.manual_baseline
        else not checkpoint_native_valid(owner.probe, factory.cp, now_ns=start)
      )
    ):
      raise ValueError('actual native prefix checkpoint provenance failed')
    # Never forge an old FullEffectTrace for a new live-owner map. Replay
    # the actual transported consumed map on EVERY original100Hz controller tick.
    from openpilot.starpilot.flm.selected_effects import EffectReplayRecordedSource

    snapshot = None
    for frame, recorded in zip(frames, prefix_controls, strict=True):
      if any(frame.applied_coordinates) or any(frame.centre_values) or any(frame.actual_stage_residual or ()):
        raise ValueError('unsupported legacy residual in actual live prefix')
      if frame.control_ns == control.control_ns:
        snapshot = capture_snapshot(owner.probe, owner.probe_vm)
      rows, has_source = _consumed_coordinates(recorded, binding)
      effect = (
        (
          EffectReplayRecordedSource(recorded.effect_trace)
          if recorded.effect_trace is not None and not getattr(recorded, 'live_applied_coordinates', ())
          else _consumed_source(rows, binding)
        )
        if has_source
        else None
      )
      request, _ = _run(
        owner.probe, owner.probe_vm, frame.inputs, replay.objective, (0.0, 0.0), frame.torque_values, actual_stage_residual=(0.0, 0.0), effect_trial=effect
      )
      if not same_final_command(request, frame.selected_command):
        raise ValueError('actual original100Hz controller request differs')
    if type(snapshot) is not Snapshot:
      raise ValueError('actual original endpoint before-state missing')
    _record(snapshot, Snapshot)
    saved = snapshot.revision, snapshot.values
    if len(key) <= _PREFIX_CACHE_MAX_KEY:
      if len(_PREFIX_CACHE) >= 2:
        _PREFIX_CACHE.pop(next(iter(_PREFIX_CACHE)))
      _PREFIX_CACHE[key] = saved
  if source()[0] != key:
    raise ValueError('actual original prefix source changed during reconstruction')

  # Repeated enclosing before/after checks need fresh COMPLETE source keys,
  # not another controller reconstruction and native-prefix decoder traversal.
  # This closure rereads the original graphs on EVERY invocation; it caches
  # neither their content nor a permission/validation verdict.
  def fresh_source_key():
    return source()[0]

  return Snapshot(*saved), key, fresh_source_key


def _native_lease(control, output, binding, *, now_ns, historical):
  if binding.native_required:
    if (
      not control.native_allowed
      or not output.native_allowed
      or not 0 < control.native_observed_ns <= control.control_ns <= output.apply_ns <= control.native_expires_ns
      or not historical
      and now_ns > control.native_expires_ns
      or control.native_expires_ns > control.native_observed_ns + 200_000_000
    ):
      raise ValueError('fresh actual native lease required')
  elif control.native_allowed or control.native_identity or control.native_observed_ns or control.native_expires_ns:
    raise ValueError('ordinary permission cannot impersonate required-native lease')


def _evidence_clock(*, evidence_ns, queued_ns, now_ns, current_native, archived_clock=False):
  if type(archived_clock) is not bool:
    raise ValueError("explicit evidence clock domain required")
  historical = evidence_ns is not None
  if historical:
    if (
      type(evidence_ns) is not int
      or evidence_ns <= 0
      or type(queued_ns) is not int
      or not 0 < queued_ns <= now_ns <= queued_ns + 30_000_000_000
      or not archived_clock
      and evidence_ns > queued_ns
      or current_native
    ):
      raise ValueError("historical numerical job clock/authority invalid")
    validation_ns = evidence_ns
  else:
    if queued_ns is not None or archived_clock or not current_native:
      raise ValueError("current native owner permission required")
    validation_ns = now_ns
  return historical, validation_ns


def probe_endpoint(
  *,
  factory,
  control,
  output,
  accepted_full30,
  binding,
  response_witness,
  source,
  now_ns,
  current_native,
  current_vehicle_model_anchor,
  replay,
  group,
  names,
  prefix_controls,
  evidence_ns=None,
  queued_ns=None,
  archived_clock=False,
):
  """Probe one real endpoint after bounded actual100Hz prefix reconstruction.

  Caller obtains current_native from the actual native/ordinary Panda owner,
  independently of the response model. It is a gate, never a returned grant.
  A genuine <=1s checkpoint and every original tick are mandatory; a missing
  endpoint checkpoint is reconstructed, never filled with a default/model guess.
  """
  from openpilot.starpilot.flm.controller_replay import (
    SelectedControllerFactory,
    capture_snapshot,
    restore_snapshot,
    _run,
    same_final_command,
  )
  from openpilot.starpilot.flm.pid_controller_replay import PidControllerFactory
  from openpilot.starpilot.flm.fullrate_collector import ControlEvidence, AppliedEvidence, replay_values
  from openpilot.starpilot.flm.runtime_adapter_registry import (
    SourceState,
    car_params_digest,
    selected_descriptor,
    selected_identity,
    baseline_revision,
    baseline_values,
    vm_regime,
    digest,
  )
  from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding, full_coordinates
  from openpilot.starpilot.flm.live_tracking_response import validate_response_witness, _certificate_key, _witness_key
  from openpilot.starpilot.flm.torque_eligibility import consumed_sources_current, exact_unclipped_pair
  from openpilot.starpilot.flm.learner_factory import _frame
  from openpilot.starpilot.flm.torque_adapter import Objective
  from openpilot.starpilot.flm.torque_emission_replay import Emission, EmissionDecoder, EncoderBranch, delivered
  from opendbc.car.steering_projection import TorqueProjection, projection_to_dict
  from openpilot.starpilot.flm.pid_stages import SUPPORTED, coordinates_supported
  from openpilot.starpilot.flm.selected_effects import EffectReplaySource

  if (
    (type(factory) is not SelectedControllerFactory and type(factory) is not PidControllerFactory)
    or type(control) is not ControlEvidence
    or type(output) is not AppliedEvidence
    or type(binding) is not LiveTrialBinding
    or type(source) is not SourceState
    or type(current_native) is not bool
    or type(now_ns) is not int
  ):
    raise ValueError('actual selected checkpoint/source/native owners required')
  _factory_configuration(factory)
  historical, validation_ns = _evidence_clock(
    evidence_ns=evidence_ns, queued_ns=queued_ns, now_ns=now_ns, current_native=current_native, archived_clock=archived_clock
  )
  binding.validate()
  if any(
    type(flag) is not bool
    for flag in (
      control.enabled,
      control.active,
      control.valid,
      control.manual,
      control.override,
      control.fault,
      control.saturated,
      control.limited,
      control.model_wobble,
      control.native_required,
      control.native_allowed,
      control.turn_assist,
      output.valid,
      output.active,
      output.override,
      output.fault,
      output.limited,
      output.native_allowed,
      source.enabled,
      source.manual,
      source.configuration_known,
    )
  ) or any(
    type(stamp) is not int
    for stamp in (
      control.control_ns,
      control.state_ns,
      control.source_receipt_ns,
      control.native_observed_ns,
      control.native_expires_ns,
      output.control_ns,
      output.state_ns,
      output.apply_ns,
      source.observed_ns,
      source.expires_ns,
    )
  ):
    raise ValueError('exact actual source clocks and flags required')
  # Validate complete primitive input records before mapping, restoring or any
  # controller/native call. Effect trace has its own exact typed validator.
  for field in fields(ControlEvidence):
    if field.name != 'effect_trace':
      _primitive(getattr(control, field.name))
  for field in fields(AppliedEvidence):
    if field.name != 'projection':
      _primitive(getattr(output, field.name))
  _record(source, SourceState)
  witness = validate_response_witness(response_witness, binding, validation_ns)
  certificate = witness.certificate
  profile = full_coordinates(accepted_full30, binding.baseline_revision)
  prior = dict(accepted_full30)
  domain = 'torque_pid' if type(factory) is PidControllerFactory else 'torque'
  if (
    type(group) is not str
    or group not in PROBE_GROUPS
    or type(names) is not tuple
    or not 1 <= len(names) <= len(PROBE_GROUPS[group])
    or any(type(name) is not str for name in names)
    or probe_names(names) != names
    or any(name not in PROBE_GROUPS[group] for name in names)
    or domain == 'torque_pid'
    and any(name not in SUPPORTED for name in names)
  ):
    raise ValueError('canonical bounded supported numerical probe coordinates required')
  if domain == 'torque_pid' and not coordinates_supported(prior):
    raise ValueError('PID cannot probe unsupported friction thresholds')
  from opendbc.car.car_helpers import interfaces

  if factory.interface_class is not interfaces.get(str(factory.cp.carFingerprint)):
    raise ValueError("exact current registered constructor interface required")
  from openpilot.starpilot.flm.controller_replay import ControllerReplay

  if type(replay) is not ControllerReplay:
    raise ValueError('exact original numerical replay required')
  controller, vm = factory.create()
  descriptor = selected_descriptor(factory.cp, factory.interface_class(factory.cp), controller)
  if (
    descriptor is None
    or descriptor.domain != domain
    or descriptor.controller_mode != control.controller_mode
    or descriptor.policy != control.policy
    or descriptor.turn_assist is not control.turn_assist
    or descriptor.controller_revision != binding.controller_revision
    or selected_identity(descriptor, binding.source_epoch, binding.baseline_revision) != binding.identity
    or baseline_revision(descriptor, baseline_values(controller)) != binding.baseline_revision
  ):
    raise ValueError('actual constructor/source baseline differs')
  # This exact constructor revision is independently checked above. Original
  # CAN verification admits a known limited INPUT only to numeric replay;
  # EncoderBranch still applies the unchanged recorded limiter to every probe.
  replay.source_controller_revision = descriptor.controller_revision
  from openpilot.starpilot.flm.live_tracking_response import verified_clipped_input

  known_limited = bool(output.limited and verified_clipped_input(replay, control, output, binding))
  if (
    not source.enabled
    or source.manual
    or not source.configuration_known
    or not 0 < source.observed_ns <= validation_ns <= source.expires_ns <= source.observed_ns + 500_000_000
    or (source.source_epoch, source.reset, source.cp_sha256, source.calibration_generation, source.persistent_source_epoch)
    != (binding.source_epoch, binding.reset_generation, binding.cp_sha256, binding.calibration_generation, binding.persistent_source_epoch)
    or type(current_vehicle_model_anchor) is not tuple
    or len(current_vehicle_model_anchor) != 3
    or any(not _finite(number) or number == 0 for number in current_vehicle_model_anchor)
    or current_vehicle_model_anchor != getattr(control, 'vehicle_model_anchor', None)
    or digest(vm_regime(dict(control.replay_inputs), current_vehicle_model_anchor)) != binding.vehicle_model_generation
    or tuple(factory.manual_baseline) != tuple(source.manual_baseline)
    or car_params_digest(factory.cp) != binding.cp_sha256
    or (control.identity, control.source_epoch, control.baseline_revision, control.controller_revision, control.native_identity)
    != (binding.identity, binding.source_epoch, binding.baseline_revision, binding.controller_revision, binding.native_identity)
    or control.native_required is not binding.native_required
    or not control.enabled
    or not control.active
    or not control.valid
    or control.manual
    or control.override
    or control.fault
    or control.saturated
    or control.limited
    and not exact_unclipped_pair(control, output)
    and not known_limited
    or control.model_wobble
    or not output.valid
    or not output.active
    or output.override
    or output.fault
    or output.limited
    and not known_limited
    or control.domain != 'normalized_torque'
    or output.domain != control.domain
    or output.control_ns != control.control_ns
    or not consumed_sources_current(control, output)
    or not 0 < control.source_receipt_ns <= control.control_ns <= control.source_receipt_ns + 500_000_000
    or not control.control_ns <= output.apply_ns <= validation_ns <= output.apply_ns + (2_000_000_000 if historical else MAX_FRESH_NS)
    or not replay_values(control.replay_inputs, 96)
    or not replay_values(control.stage_values, 96)
    or type(control.snapshot_sequence) is not int
    or control.snapshot_sequence <= 0
  ):
    raise ValueError('current genuine checkpoint/control/source pair unavailable')
  _native_lease(control, output, binding, now_ns=now_ns, historical=historical)
  projection = output.projection
  if type(projection) is not TorqueProjection or projection_to_dict(projection) is None or not projection.steer_requested:
    raise ValueError('exact native prestate required')
  frame = _frame(control)
  if frame.controller_domain != domain or any(frame.applied_coordinates) or any(frame.centre_values) or any(frame.actual_stage_residual or ()):
    raise ValueError('legacy residuals cannot be erased by a full30 probe')
  actual_full30, _ = _consumed_coordinates(control, binding)
  if _primitive(actual_full30) != _primitive(accepted_full30):
    raise ValueError('probe baseline differs from actual same-frame consumed full30')
  snapshot, prefix_key, fresh_prefix_key = _prefix_before(replay, control, output, binding, factory, prefix_controls)
  event = Emission(
    output.control_ns,
    output.state_ns,
    output.apply_ns,
    projection,
    control.identity,
    control.source_epoch,
    control.baseline_revision,
    output.valid,
    output.active,
    output.override,
    output.fault,
    output.limited,
    output.native_allowed,
    control.native_required,
    control.native_observed_ns,
    control.native_expires_ns,
    control.native_identity,
  )
  EmissionDecoder(factory.cp).check(event)
  from openpilot.starpilot.flm.clipping_quality import QUALIFIED_EVENT_CLIPPING_QUALITY

  actual_delivered = EncoderBranch().step(event, control.requested, actual=True)
  if actual_delivered != delivered(output):
    raise ValueError('actual decoded CAN delivery differs')
  # The frame retains exact original tuning and source inputs; the cloned
  # controller is restored to this immutable before-state for EVERY run.

  def key():
    # effect_trace already validated above; convert its complete field graph
    # explicitly because _record intentionally rejects arbitrary objects.
    trace_key = _trace_key(control.effect_trace)
    mode, policy = _factory_configuration(factory)
    controls = tuple((field.name, _primitive(getattr(control, field.name))) for field in fields(ControlEvidence) if field.name != 'effect_trace')
    return (
      controls,
      trace_key,
      _primitive(projection_to_dict(output.projection)),
      tuple((field.name, _primitive(getattr(output, field.name))) for field in fields(AppliedEvidence) if field.name != 'projection'),
      _record(source, SourceState),
      factory.cp.as_builder().to_bytes(),
      mode,
      tuple(factory.manual_baseline),
      factory.manual_source_epoch,
      factory.interface_class,
      factory.turn_assist,
      policy,
      _primitive(accepted_full30),
      _primitive(current_vehicle_model_anchor),
      _record(binding, LiveTrialBinding),
      _certificate_key(certificate),
      _witness_key(witness),
      fresh_prefix_key(),
    )

  before = key()
  if before[-1] != prefix_key:
    raise ValueError('actual prefix changed before coordinate probing')

  def run(name, coordinate):
    restore_snapshot(controller, vm, snapshot)
    if capture_snapshot(controller, vm) != snapshot:
      raise ValueError('probe checkpoint restore differs')
    request, log = _run(
      controller,
      vm,
      frame.inputs,
      Objective('feedforward', 'low', 'steady'),
      (0.0, 0.0),
      frame.torque_values,
      actual_stage_residual=(0.0, 0.0),
      effect_trial=EffectReplaySource.from_coordinates({name: coordinate}, binding.baseline_revision, accumulated=prior),
    )
    if not _finite(request) or bool(log.saturated):
      raise ValueError('actual controller probe clipped or nonfinite')
    return request

  baseline = run(names[0], prior[names[0]])
  if not same_final_command(baseline, control.requested):
    raise ValueError('immutable actual checkpoint failed baseline replay')
  base = EncoderBranch().step(event, baseline)
  from openpilot.starpilot.flm.live_tracking_response import TrackingSourceContext

  source_only = type(certificate) is TrackingSourceContext
  if base != actual_delivered or not source_only and not certificate.command_low <= -base <= certificate.command_high:
    raise ValueError('actual baseline outside measured command support')
  rows = []
  probe_step = SOURCE_EXPERIMENT_STEP if source_only else MAX_STEP
  for name in names:
    lower, upper = coordinate_bounds(name)
    minus, plus = max(lower, prior[name] - probe_step), min(upper, prior[name] + probe_step)
    if not minus < plus:
      raise ValueError('nonempty original canonical probe interval required')
    try:
      low = EncoderBranch(quality_contract=QUALIFIED_EVENT_CLIPPING_QUALITY).step(event, run(name, minus))
      high = EncoderBranch(quality_contract=QUALIFIED_EVENT_CLIPPING_QUALITY).step(event, run(name, plus))
      if not source_only and not all(certificate.command_low <= -number <= certificate.command_high for number in (low, high)):
        raise ValueError('native secant outside measured physical model support')
      status = 'separated' if low != high else 'quantized'
    except (ValueError, OverflowError):
      low = high = base
      status = 'native_unavailable'
    rows.append(_physical_row(name, prior[name], minus, plus, base, low, high, 1.0 if source_only else certificate.slope, status))
  validate_response_witness(response_witness, binding, validation_ns)
  if key() != before:
    raise ValueError('actual probe source/controller/native inputs changed')
  result = ProbeResult(binding, control.control_ns, output.apply_ns, profile, certificate.model_revision, tuple(rows), domain, group, None)
  object.__setattr__(result, '_seal', (_OWNER, _probe_key(result)))
  return validate_probe_result(result, binding)
