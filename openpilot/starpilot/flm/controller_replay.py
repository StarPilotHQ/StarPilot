from openpilot.starpilot.flm.normalized_trace_archive import guarded_archive

"""Exact selected torque controller replay, exclusively in the learner process.

Only capture_snapshot/frame_input belong in controls. They copy bounded numeric
primitives, never clone/advance a controller, read Params, fit, or encode JSON.
A transport gap, changed state shape, manual overlay, or neutral command mismatch
invalidates evidence. Conversion tables belong to the registered actual CI/policy.
"""

from openpilot.starpilot.flm.torque_eligibility import replay_context
from openpilot.starpilot.flm.native_refinement_wire import valid_inputs

from collections import deque, OrderedDict
from openpilot.starpilot.flm.normalized_trace_archive import trace_for, archive_key, stored_record, TraceArchive, read_ref
from dataclasses import dataclass
import math
import struct
from types import SimpleNamespace

SNAPSHOT_REVISION = 'selected-torque-frozen-state-v1'
TRACE_REVISION = 'torque-normalized-post-policy-trace-v1'
MAX_SNAPSHOT_VALUES = 1024
MAX_FRAME_VALUES = 32
MAX_CHECKPOINT_NS = 1_000_000_000
MAX_FRAME_GAP_NS = 50_000_000
MAX_REPLAY_FRAMES = 60_000  # Ten minutes, the independent evidence history bound.
_ROOT_FIELDS = (
  'dt',
  'sat_limit',
  'sat_time',
  'sat_check_min_speed',
  'steer_max',
  'steering_angle_deadzone_deg',
  'lat_accel_request_buffer_len',
  'lookahead_frames',
  'hkg_canfd_torque',
  'native_learning_baseline',
  'native_learning_provenance',
)
_TUNE_FIELDS = ('latAccelFactor', 'latAccelOffset', 'friction', 'steeringAngleDeadzoneDeg')
_VM_FIELDS = ('m', 'j', 'l', 'aF', 'aR', 'chi', 'cF_orig', 'cR_orig', 'cF', 'cR', 'sR')
_CS_FIELDS = ('vEgo', 'steeringAngleDeg', 'steeringRateDeg', 'steeringPressed', 'standstill', 'steerFaultTemporary', 'steerFaultPermanent')


def _numeric(value):
  # numpy numeric state must become a builtin scalar before crossing the wire.
  if isinstance(value, bool):
    return float(value)
  if isinstance(value, (int, float)) or type(value).__module__.startswith('numpy') and hasattr(value, 'item'):
    value = float(value)
    if math.isfinite(value):
      return value
  raise ValueError('snapshot requires finite numeric state')


def _tree(values, path, value):
  """Container shape markers prevent stale factory shape from dropping gains."""
  kind = type(value)
  if kind is float or kind is int or kind is bool:
    # Exact builtin leaves only. Preserve the original coercion, Float64 bits,
    # nonfinite error and post-append cardinality guard. Subclasses/NumPy/native
    # containers continue through the original generic traversal below.
    number = float(value)
    if not math.isfinite(number):
      raise ValueError('snapshot requires finite numeric state')
    values.append((path, number))
    if len(values) > MAX_SNAPSHOT_VALUES:
      raise ValueError('snapshot exceeds bounded primitive budget')
    return
  if type(value).__module__.startswith('capnp') and 'List' in type(value).__name__:
    # Native PID gains contain Cap'n Proto list readers. Only scalar contents
    # cross the wire; reconstructing a builtin list preserves interpolation.
    value = list(value)
  if isinstance(value, (tuple, list, deque)):
    values.append((path + '.kind', float(3 if isinstance(value, deque) else 2 if isinstance(value, tuple) else 1)))
    values.append((path + '.length', float(len(value))))
    if isinstance(value, deque):
      values.append((path + '.maxlen', float(value.maxlen or 0)))
    for i, item in enumerate(value):
      _tree(values, path + '.' + str(i), item)
  else:
    values.append((path, _numeric(value)))
  if len(values) > MAX_SNAPSHOT_VALUES:
    raise ValueError('snapshot exceeds bounded primitive budget')


def _object(values, prefix, obj, *, ignored=()):
  from openpilot.common.filter_simple import FirstOrderFilter

  for name, value in vars(obj).items():
    if name in ignored:
      continue
    path = prefix + '.' + name
    if isinstance(value, FirstOrderFilter):
      _object(values, path, value)
    elif value is None:
      values.append((path + '.none', 1.0))
    elif isinstance(value, str):
      # Constructor-only profile names are derived from CP and checked separately.
      continue
    elif callable(value):
      # Policy conversion/ratio callbacks are reconstructed from registered CP.
      continue
    else:
      _tree(values, path, value)


@dataclass(frozen=True)
class Snapshot:
  revision: str
  values: tuple[tuple[str, float], ...]


def capture_snapshot(controller, vm=None):
  """At most once per second, immediately BEFORE the actual update.

  A manual surface is authoritative; automatic learning may not replay it as an
  implicit default. Refuse this checkpoint rather than erase manual provenance.
  """
  from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID

  if type(controller) is LatControlPID:
    from openpilot.starpilot.flm.pid_controller_replay import capture_pid_snapshot

    return capture_pid_snapshot(controller, vm)
  policy = None if controller.starpilot_extension is None else controller.starpilot_extension.policy
  if controller.flm_surface is not None or policy is not None and getattr(policy, 'surface', None) is not None:
    raise ValueError('manual surface cannot qualify automatic replay')
  values = []
  for name in _ROOT_FIELDS:
    _tree(values, 'controller.' + name, getattr(controller, name))
  for name in _TUNE_FIELDS:
    _tree(values, 'tune.' + name, getattr(controller.torque_params, name))
  _object(values, 'pid', controller.pid)
  _tree(values, 'controller.lat_accel_request_buffer', controller.lat_accel_request_buffer)
  _object(values, 'controller.jerk_filter', controller.jerk_filter)
  if policy is not None:
    _object(values, 'policy', policy, ignored=('parent', 'surface', 'surface_math'))
  if controller.turn_assist is not None:
    _object(values, 'turn_assist', controller.turn_assist)
  if vm is not None:
    for name in _VM_FIELDS:
      _tree(values, 'vm.' + name, getattr(vm, name))
  if len(values) > MAX_SNAPSHOT_VALUES or len({key for key, _ in values}) != len(values):
    raise ValueError('invalid snapshot cardinality')
  return Snapshot(SNAPSHOT_REVISION, tuple(values))


def snapshot_from_values(values):
  values = tuple((str(key), _numeric(value)) for key, value in values)
  if not values or len(values) > MAX_SNAPSHOT_VALUES or len({key for key, _ in values}) != len(values):
    raise ValueError('invalid checkpoint wire')
  return Snapshot(SNAPSHOT_REVISION, values)


def _decode(values, path, old=None):
  if path + '.none' in values:
    return None
  if path + '.kind' in values:
    kind, length = int(values[path + '.kind']), int(values[path + '.length'])
    if kind not in (1, 2, 3) or not 0 <= length <= MAX_SNAPSHOT_VALUES:
      raise ValueError('invalid state container')
    seq = [_decode(values, path + '.' + str(i)) for i in range(length)]
    return deque(seq, maxlen=int(values[path + '.maxlen']) or None) if kind == 3 else tuple(seq) if kind == 2 else seq
  value = values[path]
  # Constructor zeros can be ints even for continuous PID/filter state. Never
  # truncate an original fractional checkpoint to that initial Python type.
  # Exact integral counters retain their integer representation.
  return bool(value) if isinstance(old, bool) else int(value) if isinstance(old, int) and value.is_integer() else value


def restore_snapshot(controller, vm, snapshot):
  from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID

  if type(controller) is LatControlPID:
    from openpilot.starpilot.flm.pid_controller_replay import restore_pid_snapshot

    return restore_pid_snapshot(controller, vm, snapshot)
  if not isinstance(snapshot, Snapshot) or snapshot.revision != SNAPSHOT_REVISION:
    raise ValueError('checkpoint revision')
  values = dict(snapshot.values)
  # Comparing canonical paths rejects dropped filters, unknown fields, and schema
  # drift before restoring. Container sizes may legitimately change PID curves.
  reference = capture_snapshot(controller, vm)
  expected = {key for key, _ in reference.values if not any(piece.isdigit() for piece in key.split('.'))}
  actual = {key for key in values if not any(piece.isdigit() for piece in key.split('.'))}
  # Optional previous_feedforward changes None -> scalar during normal operation.
  expected = {key.removesuffix('.none') for key in expected}
  actual = {key.removesuffix('.none') for key in actual}
  if expected != actual:
    raise ValueError('incomplete or foreign state checkpoint')
  roots = {
    'controller': controller,
    'pid': controller.pid,
    'tune': controller.torque_params,
    'vm': vm,
    'policy': None if controller.starpilot_extension is None else controller.starpilot_extension.policy,
    'turn_assist': controller.turn_assist,
  }
  top = set()
  for key in values:
    bits = key.split('.')
    index = next((i for i, bit in enumerate(bits) if bit.isdigit() or bit in ('kind', 'length', 'maxlen', 'none')), len(bits))
    top.add('.'.join(bits[:index]))
  for path in top:
    bits = path.split('.')
    obj = roots[bits[0]]
    for bit in bits[1:-1]:
      obj = getattr(obj, bit)
    name = bits[-1]
    old = getattr(obj, name)
    setattr(obj, name, _decode(values, path, old))


def frame_input(cs, vm, params, *, active, safety_limited, desired_curvature, curvature_limited, lat_delay):
  values = [(name, _numeric(getattr(cs, name))) for name in _CS_FIELDS]
  values += [
    ('gearShifter', float(cs.gearShifter.raw if hasattr(cs.gearShifter, 'raw') else int(cs.gearShifter))),
    ('angleOffsetDeg', _numeric(params.angleOffsetDeg)),
    ('roll', _numeric(params.roll)),
    ('active', float(bool(active))),
    ('safety_limited', float(bool(safety_limited))),
    ('desired_curvature', _numeric(desired_curvature)),
    ('curvature_limited', float(bool(curvature_limited))),
    ('lat_delay', _numeric(lat_delay)),
  ]
  values += [('vm.' + name, _numeric(getattr(vm, name))) for name in _VM_FIELDS]
  if len(values) > MAX_FRAME_VALUES:
    raise ValueError('frame primitive budget')
  return tuple(values)


class _FrozenManualSource:
  """No Params constructor or clock dependency in learner replay."""

  def __init__(self, *args, **kwargs):
    pass

  def sample(self, **kwargs):
    return None


class CandidateSource:
  def __init__(self, objective, coordinates, fixed_residual=None):
    self.objective, self.coordinates = objective, tuple(coordinates)
    self.owner = SimpleNamespace(applied=self.coordinates)
    self.calls = 0
    self.fixed_residual = fixed_residual

  @property
  def applied_coordinates(self):
    return self.coordinates

  def torque_residual(self, frame, *, stage_revision):
    from openpilot.starpilot.flm.torque_adapter import gates
    from openpilot.starpilot.flm.torque_stages import REVISION

    if stage_revision != REVISION:
      raise ValueError('selected stage revision changed')
    self.calls += 1
    if self.fixed_residual is not None:
      if len(self.fixed_residual) != 2 or not all(math.isfinite(value) and abs(value) <= 0.03 for value in self.fixed_residual):
        raise ValueError('actual selected stage residual envelope')
      return self.fixed_residual
    delta = sum(a * b for a, b in zip(self.coordinates, gates(self.objective, frame.speed, frame.setpoint, frame.jerk), strict=True))
    return (delta, 0.0) if self.objective.stage == 'feedforward' else (0.0, delta)

  def torque_deadband(self, speed, *, stage_revision, setpoint=0.0, jerk=0.0):
    from openpilot.starpilot.flm.torque_adapter import gates

    if self.objective.stage != 'center':
      return 0.0
    self.calls += 1
    return sum(a * b for a, b in zip(self.coordinates, gates(self.objective, speed, setpoint, jerk), strict=True))


class _FixedCentreSource:
  def __init__(self, baseline_revision, values):
    self.baseline_revision = baseline_revision
    self.values = tuple(values)
    self.calls = 0
    self.observer_calls = 0
    self.stage = None
    self.friction_output = None

  def observe_centre_stage(self, **values):
    required = {'speed', 'setpoint', 'jerk', 'friction_error', 'baseline_threshold', 'baseline_deadzone', 'friction_scale', 'friction_parent_wrapper'}
    if set(values) != required or not all(math.isfinite(value) for value in values.values()):
      raise ValueError('actual centre friction stage schema')
    self.observer_calls += 1
    if self.observer_calls != 1 or values['friction_parent_wrapper'] not in (0.0, 1.0):
      raise ValueError('actual centre stage duplicated or unknown friction')
    self.stage = dict(values)

  def centre_residual(self, frame, *, stage_revision):
    if stage_revision != 'selected-centre-prefriction-v1' or frame.baseline_revision != self.baseline_revision:
      raise ValueError('selected centre stage identity')
    self.calls += 1
    if self.calls > 1:
      raise ValueError('duplicate selected centre stage')
    return self.values


class _ProfileCentreSource(_FixedCentreSource):
  """Actual selected-stage direction/band gates for chronological candidates."""

  def __init__(self, baseline_revision, objective, coefficients):
    super().__init__(baseline_revision, (0.0, 0.0))
    self.objective, self.coefficients = objective, tuple(coefficients)

  def centre_residual(self, frame, *, stage_revision):
    super().centre_residual(frame, stage_revision=stage_revision)
    from openpilot.starpilot.flm.centre_objective import band

    if band(frame.speed) != self.objective.speed_band or abs(frame.setpoint) > 0.18 or abs(frame.jerk) > 0.5:
      return 0.0, 0.0
    delta = self.coefficients[0 if frame.friction_error > 0 else 1]
    return (delta, 0.0) if self.objective.coordinate == 'threshold' else (0.0, delta)


class _FrictionRecorder:
  """Observe the already-required selected friction call, never call it again."""

  def __init__(self, source):
    self.source, self.calls = source, 0

  def _record(self, value, parent_wrapper):
    stage = self.source.stage
    if stage is not None and stage['friction_parent_wrapper'] == float(parent_wrapper):
      self.calls += 1
      if self.calls != 1:
        raise ValueError('multiple actual selected friction calls')
      scaled = float(value) * stage['friction_scale']
      if not math.isfinite(scaled):
        raise ValueError('nonfinite actual selected friction')
      self.source.friction_output = scaled
    return value

  def wrap(self, original, *, parent_wrapper):
    def observe(*args, **kwargs):
      return self._record(original(*args, **kwargs), parent_wrapper)

    return observe

  def install(self, stack, controller):
    from unittest.mock import patch

    stack.enter_context(patch.object(controller, 'friction', self.wrap(controller.friction, parent_wrapper=True)))
    # A selected update can be inherited. Use its actual function globals,
    # which is the lookup location of the direct imported get_friction call.
    functions = [controller.update_standard]
    extension = controller.starpilot_extension
    if extension is not None:
      functions.append(extension.policy.update)
    namespaces = []
    for function in functions:
      namespace = function.__func__.__globals__
      if all(namespace is not known for known in namespaces):
        namespaces.append(namespace)
        original = namespace.get('get_friction')
        if callable(original):
          stack.enter_context(patch.dict(namespace, {'get_friction': self.wrap(original, parent_wrapper=False)}))


class SelectedControllerFactory:
  """Frozen registered CP and selected mode; construct only in learner process."""

  def __init__(self, cp, mode, *, turn_assist=False, manual_baseline=(), manual_source_epoch=""):
    from opendbc.car.car_helpers import interfaces
    from openpilot.starpilot.lateral.controller_selection import ControllerMode, policy_for

    if cp.carFingerprint not in interfaces or cp.lateralTuning.which() != 'torque':
      raise ValueError('unregistered torque CP')
    from opendbc.car import structs

    self.cp = structs.CarParams.new_message(**cp.to_dict()).as_reader()
    self.mode, self.policy = ControllerMode(mode), policy_for(cp)
    self.turn_assist = bool(turn_assist)
    self.interface_class = interfaces[cp.carFingerprint]
    self.manual_baseline, self.manual_source_epoch = tuple(manual_baseline), manual_source_epoch
    if self.manual_baseline:
      from openpilot.starpilot.lateral.torque_settings import bounds

      tune = cp.lateralTuning.torque
      basis = (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction))
      low_factor, high_factor = bounds(basis, 'factor', fingerprint=str(cp.carFingerprint))
      low_friction, high_friction = bounds(basis, 'friction', fingerprint=str(cp.carFingerprint))
      if (
        len(self.manual_baseline) != 3
        or not all(type(value) in (int, float) and math.isfinite(value) for value in self.manual_baseline)
        or not low_factor <= self.manual_baseline[0] <= high_factor
        or self.manual_baseline[1] != basis[1]
        or not low_friction <= self.manual_baseline[2] <= high_friction
        or type(manual_source_epoch) is not str
        or len(manual_source_epoch) != 64
        or any(c not in '0123456789abcdef' for c in manual_source_epoch)
      ):
        raise ValueError('independent fixed manual constructor source')
    elif manual_source_epoch:
      raise ValueError('manual source epoch without selected manual configuration')

  def create(self):
    from unittest.mock import patch
    from opendbc.car.vehicle_model import VehicleModel
    from openpilot.selfdrive.controls.lib.lateral_controller_factory import create_lateral_controller
    from openpilot.starpilot.flm import live

    # Scoped constructor patch affects the independent learner process only.
    with patch.object(live, 'GmLiveSource', _FrozenManualSource):
      ci = self.interface_class(self.cp)
      controller = create_lateral_controller(self.cp, ci, 0.01, mode=self.mode, turn_assist=self.turn_assist)
    if controller.controller_policy != self.policy:
      raise ValueError('selected policy mismatch')
    if self.manual_baseline:
      controller.update_torque_parameters(*self.manual_baseline)
    return controller, VehicleModel(self.cp)


@dataclass(frozen=True)
class _PreparedCarState:
  inputs: tuple
  reader: object


def _car_state(values, *, first_segment_words=None):
  # Keep the original Capnp conversions, including Float32 fields and enum.
  from opendbc.car import structs

  cs = structs.CarState.new_message() if first_segment_words is None else structs.CarState.new_message(num_first_segment_words=first_segment_words)
  for name in _CS_FIELDS:
    setattr(cs, name, bool(values[name]) if name in ('steeringPressed', 'standstill', 'steerFaultTemporary', 'steerFaultPermanent') else values[name])
  cs.gearShifter = int(values['gearShifter'])
  return cs


class _PreparedCarStates:
  """One child job only: immutable input tuple refs, never frame-object refs."""

  def __init__(self):
    self.__entries = {}

  def get(self, inputs):
    saved = self.__entries.get(id(inputs))
    if saved is not None and saved.inputs is inputs:
      return saved
    if (
      len(self.__entries) >= 12000
      or type(inputs) is not tuple
      or any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str or type(row[1]) not in (bool, int, float, str) for row in inputs)
    ):
      return None
    # Strongly retain the deeply immutable tuple: identity cannot be recycled.
    prepared = _PreparedCarState(inputs, _car_state(dict(inputs), first_segment_words=32).as_reader())
    self.__entries[id(inputs)] = prepared
    return prepared


def _replay_car_state(inputs, values, prepared):
  if type(prepared) is _PreparedCarState and prepared.inputs is inputs:
    return prepared.reader
  return _car_state(values)


def _run(
  controller, vm, inputs, objective, coordinates, torque_values=(), centre=None, actual_stage_residual=None, effect_trial=None, _prepared_car_state=None
):
  from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID

  if type(controller) is LatControlPID:
    from openpilot.starpilot.flm.pid_controller_replay import run_pid

    if torque_values or any(coordinates) or centre is not None and any(centre.values):
      raise ValueError('PID cannot consume torque tuning or legacy residual coordinates')
    kwargs = {} if _prepared_car_state is None else {'_prepared_car_state': _prepared_car_state}
    return run_pid(controller, vm, inputs, effect_trial=effect_trial, **kwargs)
  values = dict(inputs)
  if torque_values:
    tune = dict(torque_values)
    if set(tune) != {'effective_factor', 'offset', 'friction', 'pid_pos_limit', 'pid_neg_limit'}:
      raise ValueError('incomplete actual fullrate tuning state')
    if not all(math.isfinite(value) for value in tune.values()) or tune['effective_factor'] <= 0 or tune['friction'] < 0:
      raise ValueError('invalid actual fullrate tuning state')
    # Captured values already include native/manual policy parameter transforms;
    # invoking update_torque_parameters here would multiply1.22 a second time.
    controller.torque_params.latAccelFactor = tune['effective_factor']
    controller.torque_params.latAccelOffset = tune['offset']
    controller.torque_params.friction = tune['friction']
    controller.pid.set_limits(tune['pid_pos_limit'], tune['pid_neg_limit'])
  cs = _replay_car_state(inputs, values, _prepared_car_state)
  for name in _VM_FIELDS:
    setattr(vm, name, values['vm.' + name])
  params = SimpleNamespace(angleOffsetDeg=values['angleOffsetDeg'], roll=values['roll'])
  source = CandidateSource(objective, coordinates, actual_stage_residual)
  controller.automatic_flm_stages = source
  controller.automatic_flm_centre = centre
  effect_source = None
  if effect_trial is not None:
    from openpilot.starpilot.flm.selected_effects import EffectReplaySource

    effect_source = effect_trial if isinstance(effect_trial, EffectReplaySource) else EffectReplaySource(effect_trial)
    effect_source.begin_frame(int(values['control_frame']))
  controller.automatic_flm_effects = effect_source
  from contextlib import ExitStack

  with ExitStack() as stack:
    recorder = _FrictionRecorder(centre) if isinstance(centre, _FixedCentreSource) else None
    if recorder is not None:
      recorder.install(stack, controller)
    output, _, log = controller.update(
      bool(values['active']),
      cs,
      vm,
      params,
      bool(values['safety_limited']),
      values['desired_curvature'],
      bool(values['curvature_limited']),
      values['lat_delay'],
    )
  if values['active'] and not cs.steeringPressed and not values['safety_limited'] and source.calls != 1:
    raise ValueError('actual selected stage hook absent or duplicated')
  if effect_source is not None and values['active'] and not cs.steeringPressed and not values['curvature_limited']:
    if effect_source.stage_calls != 1 or effect_source.friction_calls != 1:
      raise ValueError('actual selected effect hook absent or duplicated')
  return float(output), log


@dataclass(frozen=True)
class ReplayFrame:
  control_ns: int
  inputs: tuple[tuple[str, float | str], ...]
  selected_command: float
  applied_coordinates: tuple[float, ...]
  block_id: str = ''
  cohort: str = ''
  desired_physical_accel: float = 0.0
  healthy: bool = True
  manual: bool = False
  limited: bool = False
  actual_lac_command: float | None = None
  frame_sequence: int = 0
  torque_values: tuple[tuple[str, float], ...] = ()
  centre_values: tuple[float, float] = (0.0, 0.0)
  actual_stage_residual: tuple[float, float] | None = None
  applied_parameter_names: tuple[str, ...] = ()
  effect_trace: object | None = None
  effect_prepare_ns: int = 0
  controller_domain: str = 'torque'
  desired_kinematic_accel: float | None = None
  vehicle_model_anchor: tuple[float, ...] = ()
  live_applied_coordinates: tuple[float, ...] = ()
  route_applied_coordinates: tuple[tuple[str, float], ...] = ()
  route_effect_revision: str = ''
  route_configuration_scope: str = ''


@dataclass(frozen=True)
class NormalizedTrace:
  baseline_command: float
  features: tuple[float, ...]
  selected_command: float
  verified: bool
  plus_commands: tuple[float, ...] = ()
  minus_commands: tuple[float, ...] = ()
  epsilon: float = 0.0


@dataclass(frozen=True)
class CentreTrialSnapshot:
  baseline_revision: str
  state: Snapshot
  frame: ReplayFrame


def same_float(a, b):
  return struct.pack('!d', float(a)) == struct.pack('!d', float(b))


def final_command(value):
  """CarControl.Actuators.torque is Float32; preserve its actual final precision."""
  return struct.unpack('!f', struct.pack('!f', float(value)))[0]


def same_final_command(a, b):
  return struct.pack('!f', float(a)) == struct.pack('!f', float(b))


class ControllerReplay:
  """Stream actual inputs from a pre-frame checkpoint; never fills missing ticks."""

  def __init__(self, factory, objective, *, identity="", baseline_revision="", model_revision=""):
    self.factory, self.objective = factory, objective
    self.identity, self.baseline_revision, self.model_revision = identity, baseline_revision, model_revision
    self.actual, self.vm = factory.create()
    self.probe, self.probe_vm = factory.create()
    self.last_ns = self.checkpoint_ns = 0
    self.last_sequence = 0
    self.failed = True
    self.records = deque(maxlen=MAX_REPLAY_FRAMES)
    self.checkpoints = deque(maxlen=600)
    self.normalized = {}
    self.recent_states = OrderedDict()
    self.cached_validation = None
    self.cached_key = None
    self.replay_windows: tuple[tuple[int, int], ...] = ()
    self.raw_mode = False
    self.actual_centre_friction_values = OrderedDict()
    from openpilot.starpilot.flm.torque_emission_replay import EmissionHistory

    self.emissions = EmissionHistory(factory.cp, identity=identity, baseline_revision=baseline_revision)
    self.projected = {}

  def checkpoint(self, control_ns, snapshot):
    if type(control_ns) is not int or control_ns <= 0 or self.last_ns and control_ns <= self.last_ns:
      raise ValueError('checkpoint clock')
    restore_snapshot(self.actual, self.vm, snapshot)
    self.last_ns = 0
    self.last_sequence = 0
    self.checkpoint_ns = control_ns
    self.failed = False
    self.checkpoints.append((control_ns, snapshot))

  def _probe(self, snapshot, frame, coordinates):
    restore_snapshot(self.probe, self.probe_vm, snapshot)
    centre = _FixedCentreSource(self.baseline_revision, frame.centre_values) if any(frame.centre_values) else None
    return _run(self.probe, self.probe_vm, frame.inputs, self.objective, coordinates, frame.torque_values, centre)

  def centre_snapshot(self, control_ns):
    """Immutable token retained only for eligible centre evidence rows."""
    from itertools import islice

    state = self.recent_states.get(control_ns)
    if state is None and self.raw_mode:
      state = self._current_raw_state(control_ns)
    frame = next((frame for frame in islice(reversed(self.records), 60) if frame.control_ns == control_ns), None)
    if state is None or frame is None or self.failed:
      return None
    return CentreTrialSnapshot(self.baseline_revision, state, frame)

  def centre_trial(self, token, threshold_delta, extra_deadband_deg):
    """Actual selected update through the separate centre hook, offline only."""
    from openpilot.starpilot.flm.centre_objective import ReplayPoint

    if (
      not isinstance(token, CentreTrialSnapshot)
      or token.baseline_revision != self.baseline_revision
      or not math.isfinite(threshold_delta)
      or not -0.03 <= threshold_delta <= 0.03
      or not math.isfinite(extra_deadband_deg)
      or not 0 <= extra_deadband_deg <= 0.01
    ):
      raise ValueError('centre trial envelope or identity')
    frame = token.frame
    if not frame.healthy or frame.manual or frame.limited:
      raise ValueError('centre trial source provenance')
    restore_snapshot(self.probe, self.probe_vm, token.state)
    source = _FixedCentreSource(token.baseline_revision, (threshold_delta, extra_deadband_deg))
    output, state = _run(
      self.probe, self.probe_vm, frame.inputs, self.objective, frame.applied_coordinates, frame.torque_values, source, frame.actual_stage_residual
    )
    if source.calls != 1:
      raise ValueError('actual selected centre hook absent')
    if source.observer_calls != 1 or source.friction_output is None:
      raise ValueError('actual selected centre friction observation absent')
    return ReplayPoint(final_command(output), bool(state.saturated), source.friction_output)

  def _frame_context(self, frame):
    return bool(frame.healthy and not frame.manual and (not frame.limited or self.raw_mode and replay_context(frame)))

  def _recorded_cp_digest(self):
    """One successful canonical CP digest; reread ALL native bytes each lookup.

    Only owned registered CarParams readers share this replay-local derived
    value. No object identity shortcut, payload flag, global/current cache or
    cached error. Unknown schemas/objects keep the original canonical function.
    """
    from capnp.lib.capnp import _DynamicStructReader
    from opendbc.car import structs
    from openpilot.starpilot.flm.pid_controller_replay import PidControllerFactory
    from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest

    cp = self.factory.cp
    raw = None
    try:
      if (
        (type(self.factory) is SelectedControllerFactory or type(self.factory) is PidControllerFactory)
        and type(cp) is _DynamicStructReader
        and cp.schema == structs.CarParams.schema
      ):
        raw = cp.as_builder().to_bytes()
    except Exception:
      pass  # Original digest below retains its own errors and unsupported behavior.
    if type(raw) is not bytes:
      return car_params_digest(cp)
    saved = self.__dict__.get('_ControllerReplay__recorded_cp_digest_cache')
    if type(saved) is tuple and len(saved) == 3 and saved[0] == raw and saved[2] is car_params_digest:
      return saved[1]
    value = car_params_digest(cp)
    try:
      unchanged = cp.as_builder().to_bytes() == raw
    except Exception:
      unchanged = False
    if unchanged and type(value) is str and len(value) == 64 and all(char in '0123456789abcdef' for char in value):
      self.__dict__['_ControllerReplay__recorded_cp_digest_cache'] = (raw, value, car_params_digest)
    return value

  @guarded_archive()
  def normalize(self, frame, *, epsilon=0.0001, _verify_only=False):
    return self._normalize_frame(frame, epsilon=epsilon, _verify_only=_verify_only)

  def _normalize_frame(self, frame, *, epsilon=0.0001, _verify_only=False):
    # Called only by whole-archive guarded public/snapshot operations. Every
    # consumed row still resolves freshly; no per-frame success cache exists.
    if (
      self.failed
      or not isinstance(frame, ReplayFrame)
      or not valid_inputs(frame.inputs)
      or frame.controller_domain != ('torque_pid' if self.factory.cp.lateralTuning.which() == 'pid' else 'torque')
      or not math.isfinite(epsilon)
      or not 0 < epsilon <= 0.03
      or type(frame.frame_sequence) is not int
      or frame.frame_sequence <= 0
      or frame.control_ns < self.checkpoint_ns
      or frame.control_ns - self.checkpoint_ns > MAX_CHECKPOINT_NS
      or self.last_ns
      and not 0 < frame.control_ns - self.last_ns <= MAX_FRAME_GAP_NS
    ):
      self.failed = True
      return None

    try:
      if not self._frame_context(frame):
        raise ValueError('invalid replay provenance')
      if frame.frame_sequence and self.last_sequence and frame.frame_sequence != self.last_sequence + 1:
        raise ValueError('missing actual controller tick')
      before = capture_snapshot(self.actual, self.vm)
      centre = _FixedCentreSource(self.baseline_revision, frame.centre_values)
      recorded_effect = None
      trace = trace_for(frame, self)
      if trace is not None:
        from openpilot.starpilot.flm.selected_effects import EffectReplayRecordedSource

        if (
          trace.control_ns != frame.effect_prepare_ns
          or trace.envelope.selected_baseline_revision != self.baseline_revision
          or trace.envelope.cp_sha256 != self._recorded_cp_digest()
        ):
          raise ValueError('recorded full30 trace source/prepare clock mismatch')
        recorded_effect = EffectReplayRecordedSource(trace)
        trace = None
        _verify_only = True
      if frame.route_effect_revision:
        from openpilot.starpilot.flm.offroad_surface import REVISION as ROUTE_REVISION, replay_source
        from openpilot.starpilot.flm.route_recording import _valid

        if frame.route_effect_revision != ROUTE_REVISION or recorded_effect is not None or frame.live_applied_coordinates:
          raise ValueError('Distinct explicit route replay policy required')
        _valid(frame.route_applied_coordinates, frame.route_configuration_scope)
        if any(value for _, value in frame.route_applied_coordinates):
          recorded_effect = replay_source(dict(frame.route_applied_coordinates), self.baseline_revision)
        _verify_only = True
      actual, _ = _run(
        self.actual,
        self.vm,
        frame.inputs,
        self.objective,
        frame.applied_coordinates,
        frame.torque_values,
        centre,
        frame.actual_stage_residual,
        effect_trial=recorded_effect,
      )
      if not same_final_command(actual, frame.selected_command) or frame.actual_lac_command is not None and not same_float(actual, frame.actual_lac_command):
        raise ValueError('actual same-state replay mismatch')
      self._cache_actual_centre_friction(frame.control_ns, centre)
      zero = (0.0,) * len(frame.applied_coordinates)
      features, plus_commands, minus_commands = [], [], []
      if _verify_only:
        # Child reconstruction verifies actual provenance first. No fitting
        # consumes this temporary table before rebuild_causal_features below.
        baseline = final_command(actual)
      else:
        baseline, _ = self._probe(before, frame, zero)
        baseline = final_command(baseline)
        for i in range(len(zero)):
          plus, minus = list(zero), list(zero)
          plus[i], minus[i] = epsilon, -epsilon
          plus_out, _ = self._probe(before, frame, plus)
          minus_out, _ = self._probe(before, frame, minus)
          plus_commands.append(final_command(plus_out))
          minus_commands.append(final_command(minus_out))
          features.append((plus_commands[-1] - minus_commands[-1]) / (2 * epsilon))
      if not all(math.isfinite(value) for value in (baseline, *features)):
        raise ValueError('nonfinite normalized trace')
      self.last_ns = frame.control_ns
      self.last_sequence = frame.frame_sequence
      if len(self.records) == self.records.maxlen:
        self.normalized.pop(self.records[0].control_ns, None)
      self.records.append(frame)
      self.recent_states[frame.control_ns] = before
      while len(self.recent_states) > 60:
        self.recent_states.popitem(last=False)
      trace = NormalizedTrace(baseline, tuple(features), final_command(actual), True, tuple(plus_commands), tuple(minus_commands), epsilon)
      self.normalized[frame.control_ns] = trace
      return trace
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
      self.failed = True
      return None

  def rebuild_causal_features(self, *, epsilon=0.01):
    """Child-only sensitivities through complete controller AND encoder history.

    Seed each branch at the same genuine pre-maneuver checkpoint as validate.
    Intermediate 1 Hz checkpoints verify the actual stream; they never reset a
    counterfactual filter/integrator. No state crosses an omitted window gap.
    """
    if self.failed or not math.isfinite(epsilon) or not 0 < epsilon <= 0.03 or self.objective.stage == 'center':
      raise ValueError('causal selected feature provenance/envelope')
    frames = tuple(self.records)
    windows = self.replay_windows
    if not windows and frames:
      windows = ((frames[0].control_ns, frames[-1].control_ns),)
    if not frames or len(frames) > 12000 or not 1 <= len(windows) <= 24:
      raise ValueError('causal selected feature window bound')
    count = len(self.objective.parameters)
    if count != 2 or any(a > b or i and a <= windows[i - 1][1] for i, (a, b) in enumerate(windows)):
      raise ValueError('causal selected feature windows')
    zero = (0.0,) * count
    coordinates = [zero]
    for i in range(count):
      plus, minus = list(zero), list(zero)
      plus[i], minus[i] = epsilon, -epsilon
      coordinates.extend((tuple(plus), tuple(minus)))
    branches = [self.factory.create() for _ in coordinates]
    requests = [{} for _ in coordinates]
    checkpoints = dict(self.checkpoints)
    previous_ns = previous_sequence = 0
    window_index = 0
    for frame in frames:
      while window_index < len(windows) and frame.control_ns > windows[window_index][1]:
        window_index += 1
      if window_index >= len(windows) or not windows[window_index][0] <= frame.control_ns <= windows[window_index][1]:
        raise ValueError('causal frame outside exact windows')
      if frame.control_ns == windows[window_index][0]:
        checkpoint = checkpoints.get(frame.control_ns)
        if checkpoint is None:
          raise ValueError('causal pre-window checkpoint absent')
        for controller, vm in branches:
          restore_snapshot(controller, vm, checkpoint)
        previous_ns = previous_sequence = 0
      elif not previous_ns:
        raise ValueError('causal window missing first actual tick')
      actual = self.normalized.get(frame.control_ns)
      if (
        actual is None
        or not actual.verified
        or not self._frame_context(frame)
        or any(frame.applied_coordinates)
        or any(frame.centre_values)
        or frame.actual_stage_residual is not None
        and any(frame.actual_stage_residual)
        or previous_ns
        and not 0 < frame.control_ns - previous_ns <= MAX_FRAME_GAP_NS
        or previous_sequence
        and frame.frame_sequence != previous_sequence + 1
      ):
        raise ValueError('causal actual replay continuity')
      values = []
      for (controller, vm), coefficient, commands in zip(branches, coordinates, requests, strict=True):
        value, _ = _run(controller, vm, frame.inputs, self.objective, coefficient, frame.torque_values)
        # Neutral-only jobs must reproduce actual continuous state, not merely
        # each checkpoint interval. Never turn a hidden state jump into a
        # verified feature, even if its CarControl rounding would conceal it.
        if coefficient == zero and (
          not same_final_command(value, frame.selected_command) or frame.actual_lac_command is not None and not same_float(value, frame.actual_lac_command)
        ):
          raise ValueError('continuous neutral actual selected replay mismatch')
        value = final_command(value)
        if not math.isfinite(value) or abs(value) > 1.000001:
          raise ValueError('causal selected command envelope')
        commands[frame.control_ns] = value
        values.append(value)
      plus, minus = tuple(values[1::2]), tuple(values[2::2])
      self.normalized[frame.control_ns] = NormalizedTrace(
        values[0], tuple((a - b) / (2 * epsilon) for a, b in zip(plus, minus, strict=True)), actual.selected_command, True, plus, minus, epsilon
      )
      previous_ns, previous_sequence = frame.control_ns, frame.frame_sequence
    if window_index != len(windows) - 1 or previous_ns != windows[-1][1]:
      raise ValueError('causal final actual tick absent')
    encoded = [self.emissions.rollout(commands, windows[0][0], windows[-1][1], windows) for commands in requests]
    self.projected.clear()
    for apply_ns, baseline in encoded[0].items():
      self.projected[apply_ns] = (baseline, tuple((encoded[1 + 2 * i][apply_ns] - encoded[2 + 2 * i][apply_ns]) / (2 * epsilon) for i in range(count)))
    return len(self.projected)

  def record_actual_frame(self, frame):
    """Parent learner only: bounded primitive append, no controller steps/fit."""
    if (
      self.failed
      or not isinstance(frame, ReplayFrame)
      or frame.frame_sequence <= 0
      or self.last_sequence
      and frame.frame_sequence != self.last_sequence + 1
      or frame.control_ns < self.checkpoint_ns
      or frame.control_ns - self.checkpoint_ns > MAX_CHECKPOINT_NS
      or self.last_ns
      and not 0 < frame.control_ns - self.last_ns <= MAX_FRAME_GAP_NS
      or not math.isfinite(frame.selected_command)
      or not valid_inputs(frame.inputs)
    ):
      self.failed = True
      return False
    if frame.controller_domain != ('torque_pid' if self.factory.cp.lateralTuning.which() == 'pid' else 'torque'):
      self.failed = True
      return False
    self.raw_mode = True
    self.records.append(frame)
    self.last_ns, self.last_sequence = frame.control_ns, frame.frame_sequence
    return True

  def _cache_actual_centre_friction(self, control_ns, source):
    if source.calls == 1 and source.observer_calls == 1 and source.friction_output is not None:
      self.actual_centre_friction_values[control_ns] = source.friction_output
      while len(self.actual_centre_friction_values) > 60:
        self.actual_centre_friction_values.popitem(last=False)
    else:
      self.actual_centre_friction_values.pop(control_ns, None)

  def actual_centre_friction(self, control_ns):
    """Verified actual-ramp friction; this accessor performs no probe/call."""
    if self.failed or not 0 <= self.last_ns - control_ns <= 500_000_000:
      return None
    return self.actual_centre_friction_values.get(control_ns)

  def validate(self, profile, control, output, physical_context, blocks, model):
    """Compute validation from actual heldout frames and independently fit plant.

    No caller-supplied acceptance flags or digest can bypass controller replay.
    Plant command orientation is explicitly -1 for raw LaC/Card torque, matching
    the registered torque response adapter; the model is not a CI inverse.
    """
    from openpilot.starpilot.flm.runtime_adapter_registry import canonical_physical_context, physical_model_revision
    from openpilot.starpilot.flm.adaptive_profile import ReplayValidation

    if (
      self.failed
      or not self.baseline_revision
      or not self.model_revision
      or profile.identity != self.identity
      or model.identity != self.identity
      or model.domain != 'normalized_torque'
      or model.context != canonical_physical_context(physical_context)
      or len(profile.coefficients) != 2
      or profile.parameters != self.objective.parameters
      or set(profile.train_ids) & set(profile.heldout_ids)
      or len(profile.heldout_ids) < 4
      or control.identity != self.identity
      or control.baseline_revision != self.baseline_revision
      or output.override
      or output.fault
      or output.limited
      or not output.active
      or not output.native_allowed
      or not self.applied_matches(control, output)
    ):
      return None
    model_digest = physical_model_revision(model)
    stable_key = (
      profile.identity,
      profile.parameters,
      profile.coefficients,
      profile.train_ids,
      profile.heldout_ids,
      profile.baseline_loss,
      profile.candidate_loss,
      profile.cohort_improvements,
      profile.policy,
      self.baseline_revision,
      model_digest,
    )
    # A lease renewal cannot replace substantive fit evidence. Reuse only a
    # genuinely computed heldout result, accompanied by a current exact probe.
    if stable_key == self.cached_key and self.cached_validation is not None:
      from dataclasses import replace

      if not self._current_probe(profile, control, model, output):
        return None
      return replace(self.cached_validation, profile=profile)
    selected = [block for block in blocks if block.block_id in profile.heldout_ids]
    if {block.block_id for block in selected} != set(profile.heldout_ids):
      return None
    rows = {}
    for block in selected:
      if block.identity != self.identity or block.cohort not in self.objective.required_cohorts or len(block.samples) < 40:
        return None
      for row in block.samples:
        if (row.control_ns, row.applied_ns) in rows or not row.active or row.override or row.fault or row.saturated or not row.stage_valid:
          return None
        rows[(row.control_ns, row.applied_ns)] = (block, row)
    if not rows:
      return None
    scored_controls = {stamp for stamp, _ in rows}
    first, last = min(scored_controls), max(scored_controls)
    starts = [(stamp, snapshot) for stamp, snapshot in self.checkpoints if stamp <= first]
    if not starts:
      return None
    start, snapshot = starts[-1]
    # Features and validation must inherit the same counterfactual history.
    # An exported window can span a cohort boundary inside a 1 Hz interval;
    # resetting at a later actual checkpoint would erase prior candidate state.
    window_start = next((a for a, b in self.replay_windows if a <= first <= b), None)
    if window_start is not None:
      matching = [(stamp, state) for stamp, state in self.checkpoints if stamp == window_start]
      if not matching:
        return None
      start, snapshot = matching[-1]
    frames = [frame for frame in self.records if start <= frame.control_ns <= last]
    if not frames or frames[0].control_ns != start or not scored_controls.issubset({frame.control_ns for frame in frames}):
      return None
    baseline, base_vm = self.factory.create()
    candidate, candidate_vm = self.factory.create()
    restore_snapshot(baseline, base_vm, snapshot)
    restore_snapshot(candidate, candidate_vm, snapshot)
    losses = {block.block_id: [0.0, 0.0, 0.0] for block in selected}
    base_requests = {}
    candidate_requests = {}
    max_base_slew = max_candidate_slew = 0.0
    prior_base = prior_candidate = None
    prior_ns = 0
    prior_sequence = 0
    saturated_regression = False
    try:
      for frame in frames:
        if prior_ns and frame.control_ns in {start for start, _ in self.replay_windows}:
          checkpoint = next((snapshot for stamp, snapshot in self.checkpoints if stamp == frame.control_ns), None)
          if checkpoint is None:
            return None
          # Separate physical maneuver blocks begin at genuine actual frozen
          # state. Preserve every filter/integrator transition inside the block.
          restore_snapshot(baseline, base_vm, checkpoint)
          restore_snapshot(candidate, candidate_vm, checkpoint)
          prior_base = prior_candidate = None
          prior_ns = prior_sequence = 0
        if (
          prior_ns
          and not 0 < frame.control_ns - prior_ns <= MAX_FRAME_GAP_NS
          or prior_sequence
          and frame.frame_sequence != prior_sequence + 1
          or not self._frame_context(frame)
        ):
          return None
        base, base_log = _run(baseline, base_vm, frame.inputs, self.objective, (0.0, 0.0), frame.torque_values)
        proposed, candidate_log = _run(candidate, candidate_vm, frame.inputs, self.objective, profile.coefficients, frame.torque_values)
        base, proposed = final_command(base), final_command(proposed)
        if not all(math.isfinite(v) and abs(v) <= 1.000001 for v in (base, proposed)):
          return None
        saturated_regression |= bool(candidate_log.saturated and not base_log.saturated)
        if prior_base is not None:
          dt = (frame.control_ns - prior_ns) / 1e9
          max_base_slew = max(max_base_slew, abs(base - prior_base) / dt)
          max_candidate_slew = max(max_candidate_slew, abs(proposed - prior_candidate) / dt)
        prior_base, prior_candidate, prior_ns = base, proposed, frame.control_ns
        base_requests[frame.control_ns] = base
        candidate_requests[frame.control_ns] = proposed
        prior_sequence = frame.frame_sequence
      base_encoded = self.emissions.rollout(base_requests, start, last, self.replay_windows)
      candidate_encoded = self.emissions.rollout(candidate_requests, start, last, self.replay_windows)
      previous_base = previous_candidate = None
      max_emitted_base_slew = max_emitted_candidate_slew = 0.0
      prior_emitted_ns = 0
      for apply_ns in sorted(base_encoded):
        if apply_ns not in candidate_encoded:
          return None
        base, proposed = base_encoded[apply_ns], candidate_encoded[apply_ns]
        if previous_base is not None:
          dt = (apply_ns - prior_emitted_ns) / 1e9
          max_emitted_base_slew = max(max_emitted_base_slew, abs(base - previous_base) / dt)
          max_emitted_candidate_slew = max(max_emitted_candidate_slew, abs(proposed - previous_candidate) / dt)
        previous_base, previous_candidate, prior_emitted_ns = base, proposed, apply_ns
      for (_control_ns, apply_ns), (block, row) in rows.items():
        normalized = self.projected.get(apply_ns)
        if normalized is None or apply_ns not in base_encoded or apply_ns not in candidate_encoded:
          return None
        target_command = row.residual_target + normalized[0]
        base, proposed = base_encoded[apply_ns], candidate_encoded[apply_ns]
        if not all(model.command_low <= -value <= model.command_high for value in (target_command, base, proposed)):
          return None
        desired = model.slope * (-target_command) + model.intercept
        base_error = desired - (model.slope * (-base) + model.intercept)
        candidate_error = desired - (model.slope * (-proposed) + model.intercept)
        data = losses[block.block_id]
        data[0] += row.weight * base_error**2
        data[1] += row.weight * candidate_error**2
        data[2] += row.weight
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
      return None
    cohort_improvements = []
    for cohort in self.objective.required_cohorts:
      improvements = []
      for block in selected:
        if block.cohort == cohort:
          base_loss, candidate_loss, weight = losses[block.block_id]
          if weight <= 0:
            return None
          improvements.append((base_loss - candidate_loss) / weight)
      if len(improvements) < 2:
        return None
      mean = sum(improvements) / len(improvements)
      variance = sum((value - mean) ** 2 for value in improvements) / (len(improvements) - 1)
      # Same conservative two-block lower confidence bound as passive fit.
      lower = mean - 12.707 * math.sqrt(variance / len(improvements))
      if not math.isfinite(lower) or lower <= 0:
        return None
      cohort_improvements.append(lower)
    no_slew_regression = max_candidate_slew <= max_base_slew + 1e-6 and max_emitted_candidate_slew <= max_emitted_base_slew + 1e-6
    if saturated_regression or not no_slew_regression:
      return None
    result = ReplayValidation(profile, self.baseline_revision, model_digest, not saturated_regression, no_slew_regression, min(cohort_improvements))
    self.cached_key, self.cached_validation = stable_key, result
    return result

  def validate_centre(self, profile, blocks, model, physical_context):
    """Mandatory chronological centre gate after frozen-state grid proposal.

    Every100Hz input between a genuine pre-maneuver checkpoint and the last
    heldout row advances two real selected controllers. Candidate directional
    coordinates are chosen at the actual selected friction-error hook, including
    minority-side transitions. No row-by-row state resets or supplied proof flags
    can substitute for this rollout.
    """
    from openpilot.starpilot.flm.centre_objective import CentreObjective, band
    from openpilot.starpilot.flm.adaptive_profile import ReplayValidation
    from openpilot.starpilot.flm.runtime_adapter_registry import canonical_physical_context, physical_model_revision

    try:
      names = [parameter.name.split('.') for parameter in profile.parameters]
      if (
        len(names) != 2
        or any(len(name) != 4 or name[0] != 'centre' for name in names)
        or names[0][3] != 'left'
        or names[1][3] != 'right'
        or names[0][1:3] != names[1][1:3]
      ):
        return None
      objective = CentreObjective(names[0][1], names[0][2])
      if (
        self.failed
        or profile.identity != self.identity
        or model.identity != self.identity
        or model.domain != 'normalized_torque'
        or model.context != canonical_physical_context(physical_context)
        or profile.parameters != objective.parameters
        or len(profile.coefficients) != 2
        or any(
          not math.isfinite(value) or not parameter.low <= value <= parameter.high or abs(value) > parameter.max_step
          for parameter, value in zip(profile.parameters, profile.coefficients, strict=True)
        )
        or not model.command_low < model.command_high
        or model.slope <= 0
        or not math.isfinite(model.heldout_rmse)
        or model.heldout_rmse > 0.01
        or len(model.train_blocks) < 4
        or len(model.heldout_blocks) < 4
        or set(model.train_blocks) & set(model.heldout_blocks)
        or set(profile.train_ids) & set(profile.heldout_ids)
      ):
        return None
      selected = sorted((block for block in blocks if block.block_id in profile.heldout_ids), key=lambda block: min(row.control_ns for row in block.rows))
      if (
        not 4 <= len(selected) <= 12
        or {block.block_id for block in selected} != set(profile.heldout_ids)
        or len({block.block_id for block in blocks}) != len(blocks)
      ):
        return None
      calibration_ids = set(model.train_blocks + model.heldout_blocks)
      gains = {'left': [], 'right': []}
      points: dict[str, float] = {'left': 0, 'right': 0}
      baseline_controller, baseline_vm = self.factory.create()
      candidate_controller, candidate_vm = self.factory.create()
      total_frames = 0
      prior_response_ns = 0
      for block in selected:
        if (
          block.identity != self.identity
          or block.block_id in calibration_ids
          or block.speed_band != objective.speed_band
          or block.symptom_bucket != 'center_chatter'
          or not 40 <= len(block.rows) <= 240
        ):
          return None
        rows = {row.control_ns: row for row in block.rows}
        if len(rows) != len(block.rows):
          return None
        first, last = min(rows), max(rows)
        starts = [(stamp, state) for stamp, state in self.checkpoints if stamp <= first]
        if not starts:
          return None
        start, state = starts[-1]
        frames = [frame for frame in self.records if start <= frame.control_ns <= last]
        total_frames += len(frames)
        if (
          total_frames > 12000
          or not frames
          or frames[0].control_ns != start
          or frames[-1].control_ns != last
          or not set(rows).issubset({frame.control_ns for frame in frames})
        ):
          return None
        restore_snapshot(baseline_controller, baseline_vm, state)
        restore_snapshot(candidate_controller, candidate_vm, state)
        previous_sequence = 0
        previous_ns = 0
        previous_baseline = previous_candidate = None
        block_loss = {'left': [0.0, 0.0, 0], 'right': [0.0, 0.0, 0]}
        sampled_baseline = []
        sampled_candidate = []
        baseline_error = []
        candidate_error = []
        for frame in frames:
          if (
            previous_sequence
            and frame.frame_sequence != previous_sequence + 1
            or previous_ns
            and not 0 < frame.control_ns - previous_ns <= MAX_FRAME_GAP_NS
            or not frame.healthy
            or frame.manual
            or frame.limited
          ):
            return None
          previous_sequence, previous_ns = frame.frame_sequence, frame.control_ns
          baseline_source = _FixedCentreSource(self.baseline_revision, (0.0, 0.0))
          candidate_source = _ProfileCentreSource(self.baseline_revision, objective, profile.coefficients)
          # Keep the already-recorded FF stage exactly as applied; only centre
          # coordinates differ. Native effective tune/limits update every tick.
          base, base_log = _run(
            baseline_controller,
            baseline_vm,
            frame.inputs,
            self.objective,
            frame.applied_coordinates,
            frame.torque_values,
            baseline_source,
            frame.actual_stage_residual,
          )
          candidate, candidate_log = _run(
            candidate_controller,
            candidate_vm,
            frame.inputs,
            self.objective,
            frame.applied_coordinates,
            frame.torque_values,
            candidate_source,
            frame.actual_stage_residual,
          )
          base, candidate = final_command(base), final_command(candidate)
          if (
            not all(math.isfinite(value) and abs(value) <= 1.000001 for value in (base, candidate))
            or candidate_log.saturated
            and not base_log.saturated
            or _pid_clipped(candidate_controller)
            and not _pid_clipped(baseline_controller)
          ):
            return None
          if previous_baseline is not None and abs(candidate - previous_candidate) > abs(base - previous_baseline) + 1e-9:
            return None
          previous_baseline, previous_candidate = base, candidate
          if frame.control_ns not in rows:
            continue
          row = rows[frame.control_ns]
          trace = row.trace
          if (
            not trace.valid()
            or trace.baseline_revision != self.baseline_revision
            or band(trace.speed) != objective.speed_band
            or row.context != model.context
            or not 0 < row.control_ns <= row.applied_ns < row.response_ns
            or not 0 <= row.response_ns - model.lag_ns - row.applied_ns <= 30_000_000
            or row.response_ns <= prior_response_ns
            or not math.isfinite(row.reference_response)
            or not math.isfinite(row.physical_response)
            or row.snapshot.baseline_revision != self.baseline_revision
            or row.snapshot.frame != frame
            or any(frame.centre_values)
            or any(frame.applied_coordinates)
            or any(frame.actual_stage_residual or (0.0, 0.0))
          ):
            return None
          prior_response_ns = row.response_ns
          # The actual recorded neutral baseline must still match the complete
          # chronological branch, not only an independently reset row snapshot.
          if (
            not same_final_command(base, frame.selected_command)
            or not same_final_command(base, trace.selected_command)
            or baseline_source.calls != 1
            or candidate_source.calls != 1
            or baseline_source.friction_output is None
            or candidate_source.friction_output is None
          ):
            return None
          if not all(model.command_low <= -value <= model.command_high for value in (base, candidate)):
            return None
          base_response = model.slope * (-base) + model.intercept
          candidate_response = model.slope * (-candidate) + model.intercept
          if abs(base_response - row.physical_response) > max(0.01, 3 * model.heldout_rmse):
            return None
          side = trace.direction
          values = block_loss[side]
          values[0] += (base_response - row.reference_response) ** 2
          values[1] += (candidate_response - row.reference_response) ** 2
          values[2] += 1
          sampled_baseline.append(base)
          sampled_candidate.append(candidate)
          baseline_error.append(base_response - row.reference_response)
          candidate_error.append(candidate_response - row.reference_response)
        # Direction reversal means changes in motion, not crossings of zero.
        # These are the existing gm_evidence speed-band significance thresholds.
        output_step = max({'low': 0.055, 'mid': 0.045, 'fast': 0.035, 'highway': 0.025}[objective.speed_band] * 0.08, 0.002)
        error_step = max({'low': 0.16, 'mid': 0.12, 'fast': 0.09, 'highway': 0.07}[objective.speed_band] * 0.08, 0.006)
        if _reversal_regression(sampled_baseline, sampled_candidate, output_step) or _reversal_regression(baseline_error, candidate_error, error_step):
          return None
        for side, values in block_loss.items():
          if values[2]:
            gains[side].append((values[0] - values[1]) / values[2])
            points[side] += values[2]
      improvements = []
      for side in ('left', 'right'):
        values = gains[side]
        if len(values) < 2 or points[side] < 80:
          return None
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        lower = mean - 12.707 * math.sqrt(variance / len(values))
        if not math.isfinite(lower) or lower <= 0:
          return None
        improvements.append(lower)
      return ReplayValidation(profile, self.baseline_revision, physical_model_revision(model), True, True, min(improvements))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
      return None

  def _current_probe(self, profile, control, model, output=None):
    """Bounded fresh revalidation; at most two selected-controller steps."""
    state = self.recent_states.get(control.control_ns)
    if state is None and self.raw_mode:
      state = self._current_raw_state(control.control_ns)
    if state is None:
      return False

    from itertools import islice

    frame = next((frame for frame in islice(reversed(self.records), 60) if frame.control_ns == control.control_ns), None)
    if frame is None or not 0 <= self.last_ns - control.control_ns <= 500_000_000 or not self._frame_context(frame):
      return False
    try:
      base, base_log = self._probe(state, frame, (0.0, 0.0))
      candidate, candidate_log = self._probe(state, frame, profile.coefficients)
      base, candidate = final_command(base), final_command(candidate)
      event = self.emissions.events.get(output.apply_ns) if output is not None else self.emissions.event(control.control_ns)
      if event is None:
        return False
      base = self.emissions.one_apply(event.apply_ns, base)
      candidate = self.emissions.one_apply(event.apply_ns, candidate)
      return (
        math.isfinite(base)
        and math.isfinite(candidate)
        and abs(candidate) <= 1.000001
        and not (candidate_log.saturated and not base_log.saturated)
        and all(model.command_low <= -value <= model.command_high for value in (base, candidate))
      )
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
      return False

  @guarded_archive()
  def _current_raw_state(self, control_ns):
    """At4Hz, at most100 actual steps from the latest genuine1Hz checkpoint."""
    from itertools import islice

    starts = [(stamp, snapshot) for stamp, snapshot in self.checkpoints if stamp <= control_ns]
    if not starts:
      return None
    start, snapshot = starts[-1]
    frames = [frame for frame in islice(reversed(self.records), 101) if start <= frame.control_ns <= control_ns]
    frames.reverse()
    if not frames or frames[0].control_ns != start or frames[-1].control_ns != control_ns or len(frames) > 100:
      return None
    try:
      restore_snapshot(self.probe, self.probe_vm, snapshot)
      before = None
      previous = 0
      for frame in frames:
        if any(frame.live_applied_coordinates):
          return None  # New live history uses its explicit raw-evidence prefix verifier.
        if previous and frame.frame_sequence != previous + 1:
          return None
        previous = frame.frame_sequence
        if frame.control_ns == control_ns:
          before = capture_snapshot(self.probe, self.probe_vm)
        centre = _FixedCentreSource(self.baseline_revision, frame.centre_values)
        recorded_effect = None
        if frame.effect_trace is not None:
          from openpilot.starpilot.flm.selected_effects import EffectReplayRecordedSource

          recorded_effect = EffectReplayRecordedSource(trace_for(frame, self))
        actual, _ = _run(
          self.probe,
          self.probe_vm,
          frame.inputs,
          self.objective,
          frame.applied_coordinates,
          frame.torque_values,
          centre,
          frame.actual_stage_residual,
          effect_trial=recorded_effect,
        )
        if not same_final_command(actual, frame.selected_command):
          return None
        if frame.control_ns == control_ns:
          self._cache_actual_centre_friction(control_ns, centre)
      return before
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
      return None

  def current_probe(self, profile, control, output, physical_context, model):
    """Fresh primitive admission proof for the parent of a child fitter."""
    from openpilot.starpilot.flm.runtime_adapter_registry import canonical_physical_context

    if (
      self.failed
      or profile.identity != self.identity
      or model.identity != self.identity
      or model.context != canonical_physical_context(physical_context)
      or control.identity != self.identity
      or control.baseline_revision != self.baseline_revision
      or output.override
      or output.fault
      or output.limited
      or not output.active
      or not output.native_allowed
      or not self.applied_matches(control, output)
    ):
      return False
    return self._current_probe(profile, control, model, output)

  def applied_matches(self, control, output):
    try:
      self.emissions.verify(control, output)
      return True
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return False

  def to_snapshot(self, blocks=None, *, event_windows: tuple[tuple[int, int], ...] | None = None):
    """Primitive child-fitter payload, outside all100Hz controller processes."""
    from dataclasses import asdict

    windows: list[tuple[int, int]] = []
    if blocks is not None:
      if not 8 <= len(blocks) <= 24:
        raise ValueError('fit job maneuver bound')
      for block in blocks:
        if not 40 <= len(block.samples) <= 240:
          raise ValueError('fit job point bound')
        first, last = min(row.control_ns for row in block.samples), max(row.control_ns for row in block.samples)
        checkpoints = [stamp for stamp, _ in self.checkpoints if stamp <= first]
        if not checkpoints:
          raise ValueError('fit job maneuver checkpoint missing')
        start = checkpoints[-1]
        if windows and start <= windows[-1][1]:
          windows[-1] = (windows[-1][0], max(windows[-1][1], last))
        else:
          windows.append((start, last))
    else:
      # Parent export is always bounded to actual fit maneuvers, not10minutes
      # of arbitrary sparse gaps. Explicit blocks are mandatory for large rings.
      if len(self.records) > 12000:
        raise ValueError('large fit job requires exact maneuver windows')
      if self.records:
        windows = [(self.records[0].control_ns, self.records[-1].control_ns)]
    if event_windows is not None:
      if (
        not blocks
        or type(event_windows) is not tuple
        or not 1 <= len(event_windows) <= 24
        or any(type(a) is not int or type(b) is not int or not 0 < a <= b or i and a <= event_windows[i - 1][1] for i, (a, b) in enumerate(event_windows))
        or any(a not in dict(self.checkpoints) for a, _ in event_windows)
        or any(not any(a <= point.control_ns <= b for a, b in event_windows) for block in blocks for point in block.samples)
      ):
        raise ValueError('explicit complete event replay windows')
      windows = list(event_windows)
    frames = [frame for frame in self.records if any(start <= frame.control_ns <= end for start, end in windows)]
    if len(frames) > 12000:
      raise ValueError('fit job fullrate window budget')
    checkpoints = [(stamp, checkpoint) for stamp, checkpoint in self.checkpoints if any(start <= stamp <= end for start, end in windows)]
    data = {
      'version': 1,
      'raw_mode': bool(self.raw_mode),
      'cp_bytes': self.factory.cp.as_builder().to_bytes(),
      'mode': str(self.factory.mode),
      'turn_assist': self.factory.turn_assist,
      'manual_baseline': self.factory.manual_baseline,
      'manual_source_epoch': self.factory.manual_source_epoch,
      'objective': asdict(self.objective),
      'identity': self.identity,
      'baseline_revision': self.baseline_revision,
      'model_revision': self.model_revision,
      'windows': tuple(windows),
      'checkpoints': tuple((stamp, checkpoint.revision, checkpoint.values) for stamp, checkpoint in checkpoints),
      'frames': tuple(stored_record(frame) for frame in frames),
      'emissions': self.emissions.snapshot(windows),
    }
    if archive_key(self) is not None:
      data['version'] = 2
      data['trace_archive'] = self.trace_archive.literal
      from openpilot.starpilot.flm.primitive_source_memo import independent_primitive_graph
      from openpilot.starpilot.flm.normalized_trace_archive import RESERVED_BYTES

      data = independent_primitive_graph(data, reserved_bytes=RESERVED_BYTES)
    return pack_primitive_snapshot(data)

  @guarded_archive()
  def _load_snapshot_frames(self, frames, checkpoints, *, normalized):
    # A ring may retain a partial oldest checkpoint. Discard only that prefix;
    # missing heldout rows are detected by validate's exact sample join.
    started = False
    for row in frames:
      row = dict(row)
      for name in ('vehicle_model_anchor', 'live_applied_coordinates'):
        if name in row:
          row[name] = tuple(row[name])
      if row.get('effect_trace') is not None:
        from openpilot.starpilot.flm.effect_trace import trace_from_dict

        row = dict(row, effect_trace=read_ref(row['effect_trace'], self.trace_archive) if normalized else trace_from_dict(row['effect_trace']))
      frame = ReplayFrame(**row)
      if frame.control_ns in checkpoints:
        self.checkpoint(frame.control_ns, checkpoints[frame.control_ns])
        started = True
      if not started:
        continue
      normalize = self._normalize_frame if normalized and type(self) is ControllerReplay else self.normalize
      if normalize(frame, epsilon=0.01, _verify_only=True) is None:
        raise ValueError('child actual selected replay mismatch')
    if not started or self.failed:
      raise ValueError('child checkpoint unavailable')

  @classmethod
  def from_snapshot(cls, factory, objective, data, *, rebuild=True):
    """Recompute actual neutral verification/features; never trust saved flags."""
    from dataclasses import asdict

    if type(rebuild) is not bool:
      raise ValueError('explicit replay rebuild policy')
    values = unpack_primitive_snapshot(data)
    if values.get('version') == 2:
      from openpilot.starpilot.flm.primitive_source_memo import independent_primitive_graph
      from openpilot.starpilot.flm.normalized_trace_archive import RESERVED_BYTES

      values = independent_primitive_graph(values, reserved_bytes=RESERVED_BYTES)
    if (
      values.get('version') not in (1, 2)
      or values.get('objective') != asdict(objective)
      or values.get('mode') != str(factory.mode)
      or values.get('turn_assist') != factory.turn_assist
      or tuple(values.get('manual_baseline', ())) != factory.manual_baseline
      or values.get('manual_source_epoch', '') != factory.manual_source_epoch
      or values.get('cp_bytes') != factory.cp.as_builder().to_bytes()
    ):
      raise ValueError('fit job selected controller identity')
    engine = cls(factory, objective, identity=values['identity'], baseline_revision=values['baseline_revision'], model_revision=values['model_revision'])
    if values.get('version') == 2:
      engine.trace_archive = TraceArchive(values['trace_archive'])
    elif 'trace_archive' in values:
      raise ValueError('legacy replay cannot adopt normalized traces')
    engine.emissions.load(values['emissions'], tuple(values.get('windows', ())))
    if type(values.get('raw_mode', False)) is not bool:
      raise ValueError('raw replay mode')
    engine.raw_mode = values.get('raw_mode', False)
    checkpoints = {stamp: Snapshot(revision, tuple(rows)) for stamp, revision, rows in values['checkpoints']}
    frames = values['frames']
    if not frames or len(frames) > MAX_REPLAY_FRAMES or len(checkpoints) > 600:
      raise ValueError('fit job history bound')
    engine._load_snapshot_frames(frames, checkpoints, normalized=values.get('version') == 2)
    engine.replay_windows = tuple(values.get('windows', ()))
    if rebuild:
      engine.rebuild_causal_features(epsilon=0.01)
    return engine


def _pid_clipped(controller):
  pid = controller.pid
  total = float(pid.p + pid.i + pid.d + pid.f)
  if not math.isfinite(total):
    return True
  return total > pid.pos_limit + 1e-12 or total < pid.neg_limit - 1e-12


def _direction_reversals(values, min_step):
  steps = [b - a for a, b in zip(values, values[1:], strict=False) if abs(b - a) >= min_step]
  return sum((a > 0) != (b > 0) for a, b in zip(steps, steps[1:], strict=False))


def _reversal_regression(baseline, candidate, min_step):
  if len(baseline) != len(candidate):
    return True
  if _direction_reversals(candidate, min_step) > _direction_reversals(baseline, min_step):
    return True
  # Whole-block aggregation cannot disguise an extra local weave. Mirror the
  # existing20..40point centre symptom windows, including minority-side rows.
  for start in range(0, max(len(baseline) - 20, 1), 10):
    base, changed = baseline[start : start + 40], candidate[start : start + 40]
    if len(base) >= 20 and _direction_reversals(changed, min_step) > _direction_reversals(base, min_step):
      return True
  return False


MAX_FIT_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_FIT_RAW_BYTES = 128 * 1024 * 1024


def pack_primitive_snapshot(value):
  """Private pipe encoding. No executable classes or CFFI objects cross it."""
  import pickle
  import zlib

  # Pickle preserves shared primitive containers. Validate each actual
  # container once rather than expanding a shared source graph repeatedly.
  # This memo is local to this call; it never caches evidence or permissions.
  checked = set()
  active = set()

  def primitive(item, recurse):
    kind = type(item)
    if item is None or kind in (bool, int, float, str, bytes):
      if kind is float and not math.isfinite(item):
        raise ValueError('nonfinite job primitive')
      return
    if kind not in (tuple, list, dict):
      raise ValueError('fit job requires primitives')
    identity = id(item)
    if identity in active:
      raise ValueError('cyclic fit job primitive')
    if identity in checked:
      return
    if kind is dict and any(type(key) is not str for key in item):
      raise ValueError('fit job requires primitives')
    active.add(identity)
    try:
      for child in item.values() if kind is dict else item:
        recurse(child, recurse)
    finally:
      active.remove(identity)
    checked.add(identity)

  from openpilot.starpilot.flm.native_primitives import try_native

  if try_native('probe_source_valid', value) is not True:
    primitive(value, primitive)
  raw = pickle.dumps(value, protocol=5)
  if len(raw) > MAX_FIT_RAW_BYTES:
    raise ValueError('fit job raw byte bound')
  packed = zlib.compress(raw, 1)
  if len(packed) > MAX_FIT_SNAPSHOT_BYTES:
    raise ValueError('fit job IPC byte bound')
  return packed


def unpack_primitive_snapshot(data):
  import io
  import pickle
  import zlib

  if type(data) is not bytes or not 0 < len(data) <= MAX_FIT_SNAPSHOT_BYTES:
    raise ValueError('fit job IPC bound')
  decoder = zlib.decompressobj()
  raw = decoder.decompress(data, MAX_FIT_RAW_BYTES + 1)
  if len(raw) > MAX_FIT_RAW_BYTES or not decoder.eof or decoder.unused_data:
    raise ValueError('fit job decompression bound')

  class PrimitiveUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
      raise ValueError('executable fit job class forbidden')

    def persistent_load(self, pid):
      raise ValueError('external fit job reference forbidden')

  return PrimitiveUnpickler(io.BytesIO(raw)).load()


def factory_from_snapshot(data):
  """Actual registered CP factory from primitive worker transport."""
  from opendbc.car import structs
  from openpilot.starpilot.flm.torque_adapter import Objective

  values = unpack_primitive_snapshot(data)
  cp_bytes = values.get('cp_bytes')
  if type(cp_bytes) is not bytes or not 0 < len(cp_bytes) <= 256 * 1024:
    raise ValueError('fit job CP byte bound')
  with structs.CarParams.from_bytes(cp_bytes) as cp:
    from openpilot.starpilot.flm.pid_controller_replay import PidControllerFactory

    factory_type = PidControllerFactory if cp.lateralTuning.which() == 'pid' else SelectedControllerFactory
    factory = factory_type(
      cp,
      values['mode'],
      turn_assist=values['turn_assist'],
      manual_baseline=tuple(values.get('manual_baseline', ())),
      manual_source_epoch=values.get('manual_source_epoch', ''),
    )
  if factory.manual_baseline:
    from openpilot.starpilot.flm.runtime_adapter_registry import baseline_values, baseline_revision, selected_descriptor, selected_identity

    controller, _ = factory.create()
    descriptor = selected_descriptor(factory.cp, factory.interface_class(factory.cp), controller)
    revision = baseline_revision(descriptor, baseline_values(controller))
    if (
      descriptor is None
      or revision != values['baseline_revision']
      or selected_identity(descriptor, factory.manual_source_epoch, revision) != values['identity']
    ):
      raise ValueError('fixed manual child configuration identity')
  return factory, Objective(**values['objective'])
