"""Shared actual-selected controller identities; physical fitting stays in learner."""

from typing import cast

from dataclasses import dataclass, field, replace
import hashlib
import json
import math
import time
from functools import cache
from pathlib import Path
import importlib
from opendbc.car.car_helpers import interfaces
from openpilot.starpilot.lateral.learning_preferences import read_preference, KEY as PREFERENCE_KEY
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, LEGACY_KEYS, MAX_DOCUMENT_BYTES, parse_document, interpret_legacy
from openpilot.starpilot.saved_source import read_saved

REVISION = 'actual-controller-registry-v2'


@cache
def implementation_revision(controller_type, interface_type):
  """Startup/optional-process source identity; never invoked by Controls.tick.

  Bind shaping and conversion implementation changes as well as numeric CP
  values. Formatting changes conservatively discard stored priors too.
  """
  here = Path(__file__).resolve().parent
  controller_root = Path(cast(str, importlib.import_module(controller_type.__module__).__file__)).resolve().parent
  brand_root = Path(cast(str, importlib.import_module(interface_type.__module__).__file__)).resolve().parent
  common_root = here.parents[1] / 'common'
  inherited_roots = tuple(
    Path(cast(str, importlib.import_module(base.__module__).__file__)).resolve().parent
    for base in controller_type.__mro__ if base.__module__.startswith('openpilot.')
  )
  roots = (here, here.parent / 'lateral', controller_root, brand_root, brand_root.parent, common_root, *inherited_roots)
  files = tuple(sorted({path for root in roots for path in root.glob('*.py')}))
  if not files or len(files) > 512:
    raise ValueError('controller implementation identity bound')
  result = hashlib.sha256()
  result.update((controller_type.__module__ + '.' + controller_type.__qualname__).encode('utf-8'))
  result.update((interface_type.__module__ + '.' + interface_type.__qualname__).encode('utf-8'))
  for path in files:
    data = path.read_bytes()
    if len(data) > 2 * 1024 * 1024:
      raise ValueError('implementation source bound')
    result.update(path.name.encode('utf-8'))
    result.update(hashlib.sha256(data).digest())
  from openpilot.starpilot.flm.native_primitives import native_identity
  result.update(native_identity().encode('utf-8'))
  return REVISION + ':' + result.hexdigest()


SOURCE_LEASE_NS = 500_000_000
BASELINE_MAX = 32


def digest(value):
  # Called at startup or by the separate learner, never JSON in Controls.tick.
  return hashlib.sha256(
    json.dumps(
      value,
      sort_keys=True,
      separators=(',', ':'),
      allow_nan=False,
      default=lambda item: {'data_hex': item.hex()} if isinstance(item, bytes) else (_ for _ in ()).throw(TypeError('unsupported identity value')),
    ).encode()
  ).hexdigest()


def car_params_digest(cp):
  """Bind all CP values, including legitimate infinity sentinels, without JSON NaN tokens."""
  import struct

  def typed(value):
    kind = type(value)
    if kind is dict:
      if not all(type(key) is str for key in value):
        raise TypeError('non-string CarParams field')
      return ['dict', [[key, typed(value[key])] for key in sorted(value)]]
    if kind in (list, tuple):
      return [kind.__name__, [typed(item) for item in value]]
    if kind is float:
      return ['float64', struct.pack('>d', value).hex()]
    if kind is bytes:
      return ['bytes', value.hex()]
    if value is None or kind in (bool, int, str):
      return [kind.__name__, value]
    raise TypeError('unsupported CarParams identity value')

  return digest(typed(cp.to_dict()))


def canonical_physical_context(value):
  return digest(value)


def physical_model_revision(model):
  from dataclasses import asdict

  return digest(asdict(model))


def native_identity_key(native):
  if native is None:
    return None
  return (
    str(native.axisSessionId),
    str(native.pandaSerial),
    int(native.safetyModel),
    int(native.safetyParam),
    tuple(
      (int(slot.slotIndex), str(slot.hardwareSerial), int(slot.safetyModel), int(slot.safetyParam), int(slot.alternativeExperience))
      for slot in native.pandaInventory
    ),
  )


def native_identity_digest(key):
  return '' if key is None else hashlib.sha256(repr(key).encode('utf-8')).hexdigest()


def current_authority(sm, cp, *, now_ns, required):
  """Once per optional daemon iteration; reuse existing native verifier."""
  cache_key = (now_ns, bool(required), id(cp))
  cached = getattr(sm, '_flm_authority_cache', None)
  if cached is not None and cached[0] == cache_key:
    return cached[1:]
  allowed, native, identity = False, None, ''
  try:
    if required:
      from openpilot.starpilot.aol.runtime import current_native
      from openpilot.starpilot.aol.transport_pause import _healthy_transport_native
      from openpilot.starpilot.aol.wire import decode_safety

      raw = sm['aolSafetyWire']
      announced = decode_safety(bytes(raw) if isinstance(raw, (bytes, bytearray, memoryview)) else raw)
      healthy = None
      if announced is not None and announced.requestedLateral and announced.lateralAllowed:
        # This existing path performs current_native itself, validating the
        # packet configuration once while keeping every live check per call.
        healthy = _healthy_transport_native(sm, cp, sm['carState'], str(announced.axisSessionId), now_ns,
          require_longitudinal=False)
      native = healthy if healthy is not None else current_native(sm, cp, now_ns=now_ns)
      identity = native_identity_digest(native_identity_key(native))
      # Typed LAT grants are independent of ordinary LONG controlsAllowed.
      # Reuse existing exact current configuration/health semantics, preserving
      # every Panda/RX/heartbeat/fault/serial guard and original native lease.
      allowed = bool(
        native is not None
        and native.requestedLateral
        and native.lateralAllowed
        and healthy is not None
      )
    else:
      healthy = bool(
        sm.seen['pandaStates']
        and sm.alive['pandaStates']
        and sm.valid['pandaStates']
        and sm.freq_ok['pandaStates']
        and 0 <= now_ns - int(round(sm.recv_time['pandaStates'] * 1e9)) <= 1_000_000_000
      )
      pandas = sm['pandaStates']
      if len(pandas) != len(cp.safetyConfigs):
        healthy = False
      active = 0
      for panda, config in zip(pandas, cp.safetyConfigs, strict=True):
        healthy &= bool(
          panda.safetyModel == config.safetyModel
          and panda.safetyParam == config.safetyParam
          and panda.alternativeExperience == cp.alternativeExperience
          and not panda.safetyRxChecksInvalid
          and not panda.heartbeatLost
          and not panda.faults
        )
        if str(config.safetyModel) not in ('silent', 'noOutput'):
          active += 1
          healthy &= bool(panda.controlsAllowed)
      allowed = bool(healthy and active >= 1)
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    allowed, native, identity = False, None, ''
  sm._flm_authority_cache = (cache_key, allowed, native, identity)
  return allowed, native, identity


def ford_public_identity_key(cp, pandas):
  # Public configuration provenance only; PandaState has no hardware serial.
  return (
    'ford-public-config-v1',
    str(cp.carFingerprint),
    int(cp.flags),
    int(cp.alternativeExperience),
    tuple(
      (index, int(panda.pandaType.raw), int(panda.safetyModel.raw), int(panda.safetyParam), int(panda.alternativeExperience))
      for index, panda in enumerate(pandas)
    ),
  )


FORD_PANDA_AGE_NS = 300_000_000  # a pandaStates sample older than this is not current


def current_ford_authority(sm, cp, *, now_ns):
  """Optional parent20Hz mirror of the existing successful Ford verifier."""
  key = (now_ns, id(cp))
  cached = getattr(sm, '_flm_ford_authority_cache', None)
  if cached is not None and cached[0] == key:
    return cached[1:]
  allowed, identity = False, ''
  try:
    if not ford_downstream(cp):
      raise ValueError('Ford downstream role')
    pandas = sm['pandaStates']
    stamp = int(sm.logMonoTime['pandaStates'])
    receipt = int(round(sm.recv_time['pandaStates'] * 1e9))
    boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    allowed = bool(
      sm.seen['pandaStates']
      and sm.all_checks(['pandaStates'])
      and 0 < stamp <= boot <= stamp + FORD_PANDA_AGE_NS
      and 0 < receipt <= now_ns <= receipt + FORD_PANDA_AGE_NS
      and len(pandas) == len(cp.safetyConfigs)
    )
    for index, (panda, config) in enumerate(zip(pandas, cp.safetyConfigs, strict=True)):
      allowed &= bool(
        panda.safetyModel == config.safetyModel
        and panda.safetyParam == config.safetyParam
        and panda.alternativeExperience == cp.alternativeExperience
        and not panda.safetyRxChecksInvalid
        and not panda.heartbeatLost
        and not panda.faults
        and str(panda.faultStatus) == 'none'
        and bool(panda.controlsAllowed) == (index == len(pandas) - 1)
      )
    if allowed:
      identity = native_identity_digest(ford_public_identity_key(cp, pandas))
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    allowed, identity = False, ''
  sm._flm_ford_authority_cache = (key, allowed, identity)
  return allowed, identity


@dataclass(frozen=True)
class SourceState:
  enabled: bool
  manual: bool
  reset: str
  source_epoch: str
  observed_ns: int
  expires_ns: int
  cp_sha256: str
  calibration: tuple[float, ...] = ()
  calibration_generation: str = ''
  persistent_source_epoch: str = ''
  # Optional-process-only selected configuration; never added to controls IPC.
  manual_baseline: tuple[float, ...] = field(default=(), compare=False, repr=False)
  # Internal only: absent current configuration proof is not an explicit OFF.
  configuration_known: bool = True


def read_source(cp, params, *, now_ns, cp_sha256=None, calibration=(), vehicle_session="", calibration_generation=''):
  """Only startup/separate-process settings work; errors disable learning."""
  cp_sha256 = car_params_digest(cp) if cp_sha256 is None else cp_sha256
  try:
    keys = tuple(dict.fromkeys((PREFERENCE_KEY, 'ForceAutoTuneOff', DOCUMENT_KEY, 'AutoSteeringLearningReset', 'LateralControllerSelection', *LEGACY_KEYS)))
    snapshots = []
    for key in keys:
      raw, readable = read_saved(params, key, MAX_DOCUMENT_BYTES)
      if not readable:
        raise ValueError('unreadable source')
      snapshots.append((key, raw))
    raw_by_key = dict(snapshots)
    preference = read_preference(params, cp, preference_snapshot=(raw_by_key[PREFERENCE_KEY], True),
      legacy_snapshot=(raw_by_key['ForceAutoTuneOff'], True))
    if not preference.valid:
      raise ValueError('invalid learning preference')
    from openpilot.starpilot.lateral.controller_selection import selection_from_bytes

    if selection_from_bytes(cp, raw_by_key['LateralControllerSelection']).source == 'invalid':
      raise ValueError('invalid saved controller configuration')
    reset_raw = raw_by_key['AutoSteeringLearningReset']
    reset = '0' if reset_raw is None else reset_raw.decode('ascii')
    if not 1 <= len(reset) <= 64 or any(c not in '0123456789abcdef' for c in reset):
      raise ValueError('reset generation')
    document = raw_by_key[DOCUMENT_KEY]
    manual = False
    fixed_manual = ()
    if document is not None:
      profile = parse_document(document).get(str(cp.carFingerprint))
      if profile is not None:
        if cp.lateralTuning.which() == 'torque':
          tune = cp.lateralTuning.torque
          if profile.basis != (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction)):
            raise ValueError('incompatible saved tuning basis')
        if cp.lateralTuning.which() == 'torque':
          from openpilot.starpilot.lateral.torque_settings import resolve_document
          factor, friction, review = resolve_document({str(cp.carFingerprint): profile}, str(cp.carFingerprint),
            (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction)))
          if review:
            raise ValueError('manual baseline requires source review')
          if (factor is not None or friction is not None) and not (profile.geometry is not None and profile.geometry.learning == 'force_auto'):
            fixed_manual = (float(tune.latAccelFactor) if factor is None else factor, float(tune.latAccelOffset),
                            float(tune.friction) if friction is None else friction)
        # Scalar and geometry choices are immutable baseline inputs. Automatic
        # FF residuals never write them; a manually owned FLM surface stays fixed.
        manual = profile.flm is not None
        if profile.geometry is not None and profile.geometry.learning == 'force_off':
          preference = type(preference)(False, preference.valid, preference.source, preference.raw)
    else:
      # Validate legacy scalar choices and bind their exact bytes to the source
      # epoch. They stay fixed baseline inputs, not automatic profile ownership.
      if cp.lateralTuning.which() == 'torque':
        tune = cp.lateralTuning.torque
        legacy = interpret_legacy(
          {key: raw_by_key[key] for key in LEGACY_KEYS},
          (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction)),
          fingerprint=str(cp.carFingerprint),
        )
        if legacy.factor is not None or legacy.friction is not None:
          fixed_manual = (float(tune.latAccelFactor) if legacy.factor is None else legacy.factor, float(tune.latAccelOffset),
                          float(tune.friction) if legacy.friction is None else legacy.friction)
    if not preference.enabled or manual:
      # Honor an independently parsed explicit OFF/manual even when physical
      # calibration is unavailable. This cannot authorize collection or output.
      return SourceState(False, manual, reset, '', now_ns,
                         now_ns + SOURCE_LEASE_NS, cp_sha256, manual_baseline=fixed_manual)
    calibration = tuple(calibration)
    if len(calibration) != 3 or not all(math.isfinite(v) for v in calibration):
      raise ValueError("uncertain calibration")
    from opendbc.car.vin import is_valid_vin, VIN_UNKNOWN

    vin = str(cp.carVin)
    if not is_valid_vin(vin) or vin == VIN_UNKNOWN:
      if not vehicle_session or len(vehicle_session) > 64:
        raise ValueError('unknown vehicle needs a fresh drive session')
      unit = vehicle_session
    else:
      unit = vin
    if type(calibration_generation) is not str or (calibration_generation and (
        len(calibration_generation) != 32 or any(c not in '0123456789abcdef' for c in calibration_generation))):
      raise ValueError('invalid calibration lifecycle')
    frame_identity = calibration if not calibration_generation else ('live_vehicle_frame_v2', calibration_generation)
    epoch = digest((tuple((key, None if raw is None else raw.hex()) for key, raw in snapshots), frame_identity, unit))
    persistent_epoch = (digest(('persistent_vehicle_source_v1', cp_sha256,
      tuple((key, None if raw is None else raw.hex()) for key, raw in snapshots), vin))
      if is_valid_vin(vin) and vin != VIN_UNKNOWN and calibration_generation else '')
    return SourceState(bool(preference.valid and preference.enabled), manual, reset, epoch, now_ns,
                       now_ns + SOURCE_LEASE_NS, cp_sha256, calibration=calibration, calibration_generation=calibration_generation,
                       persistent_source_epoch=persistent_epoch, manual_baseline=fixed_manual)
  except (AttributeError, UnicodeError, TypeError, ValueError, OSError, OverflowError):
    return SourceState(False, False, '', '', now_ns, now_ns + SOURCE_LEASE_NS, cp_sha256, configuration_known=False)


@dataclass(frozen=True)
class ControllerDescriptor:
  domain: str
  controller_mode: str
  policy: str
  controller_revision: str
  stage_revision: str
  cp_sha256: str
  conversion_revision: str
  turn_assist: bool


def selected_descriptor(cp, ci, controller):
  """Use the registered constructor plus the object actually selected by Controls."""
  from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
  from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle
  from openpilot.selfdrive.controls.lib.latcontrol_curvature import LatControlCurvature
  from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque

  if (
    cp.notCar
    or cp.passive
    or cp.dashcamOnly
    or str(cp.carFingerprint) not in interfaces
    or type(ci) is not interfaces[str(cp.carFingerprint)]
    or ci.CP.to_dict() != cp.to_dict()
  ):
    return None
  mode = str(getattr(controller, 'controller_mode', 'native'))
  policy = str(getattr(controller, 'controller_policy', None) or 'native_standard')
  conversion = ''
  if isinstance(controller, LatControlTorque) and str(cp.steerControlType) == "torque" and cp.lateralTuning.which() == "torque":
    domain, stage = 'torque', 'torque-post-baseline-prefriction-v1'
    forward = controller.torque_from_lateral_accel
    function = getattr(forward, '__func__', forward)
    conversion = function.__module__ + '.' + function.__qualname__
    extension = getattr(controller, 'starpilot_extension', None)
    if extension is not None:
      policy += ':' + type(extension).__module__ + '.' + type(extension).__qualname__
  elif (type(controller) is LatControlPID and str(cp.steerControlType) == 'torque' and cp.lateralTuning.which() == 'pid'):
    domain, stage = 'torque_pid', 'pid-selected-stage-snapshot-v2'
    forward = controller.get_steer_feedforward
    function = getattr(forward, '__func__', forward)
    conversion = function.__module__ + '.' + function.__qualname__
  elif isinstance(controller, (LatControlPID, LatControlAngle, LatControlCurvature)):
    return None  # Explicit non-torque disposition: preserve the selected baseline.
  else:
    return None
  turn_assist = bool(
    getattr(controller, 'turn_assist', None) or getattr(getattr(getattr(controller, 'starpilot_extension', None), 'policy', None), 'turn_assist', False)
  )
  return ControllerDescriptor(domain, mode, policy, implementation_revision(type(controller), type(ci)), stage, car_params_digest(cp), conversion, turn_assist)


def baseline_values(controller):
  """Bounded actual numeric controller token. No saved/default tune inference."""
  values = [float(controller.steer_max)]
  torque = getattr(controller, 'torque_params', None)
  if torque is not None:
    values.extend(float(getattr(torque, key)) for key in ('latAccelFactor', 'latAccelOffset', 'friction', 'steeringAngleDeadzoneDeg'))
  values.extend(float(getattr(controller, key)) for key in ('ff_factor', 'kf') if hasattr(controller, key))
  pid = getattr(controller, 'pid', None)
  if pid is not None:
    values.extend((float(pid.neg_limit), float(pid.pos_limit)))
    for key in ('_k_p', '_k_i'):
      for row in getattr(pid, key):
        values.extend(float(v) for v in row)
  if not 1 <= len(values) <= BASELINE_MAX or not all(math.isfinite(v) for v in values):
    raise ValueError('actual baseline envelope')
  return tuple(values)


def baseline_revision(descriptor, values):
  # Fixed bounded ASCII bytes, no JSON/Params/I/O/model work on a changed token.
  return hashlib.sha256(repr((descriptor, tuple(values))).encode('ascii')).hexdigest()


def selected_identity(descriptor, source_epoch, baseline=""):
  if len(source_epoch) != 64:
    raise ValueError('source epoch')
  return hashlib.sha256(repr((descriptor, source_epoch, baseline)).encode('ascii')).hexdigest()


def ford_downstream(cp):
  # The BluePilot lateral library owns Ford steering when its bit is in the final admitted CP safety word;
  # no settings read or generic angle-output inference establishes this role.
  from opendbc.car.ford.aol import BP_LATERAL, aol_safety_config

  config = aol_safety_config(cp) if cp.brand == 'ford' else None
  return bool(str(cp.carFingerprint) in interfaces and config is not None and config.safetyParam & BP_LATERAL)


def adapter_for_ford(cp, context):
  return None  # Ford's downstream angle/curvature controller is excluded.


def adapter_for_evidence(cp, evidence, key=None, *, source=None, event_replay_version=2, event_clipping_quality='recorded-debt-v1', event_restore_version=2):
  """Strict learner factory: consume actual descriptor, never read a mode default."""
  # Actual stage replay must be supplied by the family module; absent trace or
  # baseline reconstruction rejects the family rather than claiming validation.
  from openpilot.starpilot.flm.learner_factory import from_evidence

  return from_evidence(cp, evidence, key, source=source,
    event_replay_version=event_replay_version, event_clipping_quality=event_clipping_quality, event_restore_version=event_restore_version)


def collection_keys(evidence):
  from openpilot.starpilot.flm.learner_factory import collection_keys as keys

  return keys(evidence)


def objective_key(evidence):
  from openpilot.starpilot.flm.learner_factory import objective_key as key

  return key(evidence)


def vm_regime(values, anchor=None):
  """Learning-only geometry boundary; exact inputs continue into replay."""
  try:
    point = tuple(float(values['vm.' + name]) for name in ('sR', 'cF', 'cR'))
    if not all(math.isfinite(value) and value != 0.0 for value in point):
      return None
    if anchor is None:
      return point
    if len(anchor) != 3 or not all(math.isfinite(value) and value != 0.0 for value in anchor):
      return None
    return (
      anchor if all(min(base * 0.98, base * 1.02) <= current <= max(base * 0.98, base * 1.02) for current, base in zip(point, anchor, strict=True)) else point
    )
  except (KeyError, TypeError, ValueError, OverflowError):
    return None


def control_vm_regime(command, anchor=None):
  """Use genuine Controls anchor when delivered; preserve legacy empty shape."""
  from openpilot.starpilot.flm.fullrate_collector import ControlEvidence
  supplied = command.vehicle_model_anchor if type(command) is ControlEvidence else ()
  if type(supplied) is not tuple or len(supplied) not in (0, 3):
    return None
  if supplied:
    if any(type(value) is not float or not math.isfinite(value) or value == 0. for value in supplied):
      return None
    current = vm_regime(dict(command.replay_inputs), supplied)
    return supplied if current == supplied else None
  return vm_regime(dict(command.replay_inputs), anchor)


def physical_observer(sm, cp, collector, now_ns, *, distinguish_pending=False):
  """Convert independently measured BOOT pose source into the evidence MONO clock."""
  from openpilot.starpilot.flm.fullrate_collector import PhysicalEvidence
  from openpilot.selfdrive.locationd.helpers import PoseCalibrator, Pose

  def reject():
    collector._physical_pose_queue = ()
    collector._physical_pose_context = None
    return None

  services = ('deviceMotion', 'extrinsicsCalibration', 'carState', 'lateralDelay', 'driverMonitoringState')
  if not sm.all_checks(list(services)):
    return reject()
  try:
    mono_before = time.monotonic_ns()
    boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    mono_after = time.monotonic_ns()
    offset = boot - (mono_before + mono_after) // 2
    cache = getattr(collector, '_physical_clock', None)
    if cache is None:
      collector._physical_clock = offset
    elif abs(offset - cache) > 1_000_000 + (mono_after - mono_before) // 2:
      collector.split('physical_clock_discontinuity', reset=True)
      collector._physical_clock = offset
      return reject()
    dm, calibration, cs = sm['deviceMotion'], sm['extrinsicsCalibration'], sm['carState']
    pose_ns = int(dm.timestamp) - collector._physical_clock
    if (
      not 0 < pose_ns <= now_ns
      or now_ns - pose_ns > 100_000_000
      or not dm.inputsOK
      or not dm.sensorsOK
      or not dm.posenetOK
      or not dm.angularVelocityDevice.valid
      or not dm.orientationNED.valid
      or str(calibration.calStatus) != 'calibrated'
    ):
      return reject()
    state_ns, delay_ns = int(sm.logMonoTime['carState']), int(sm.logMonoTime['lateralDelay'])
    if not 0 < state_ns <= now_ns or now_ns - state_ns > 100_000_000 or not 0 < delay_ns <= now_ns or now_ns - delay_ns > 500_000_000:
      return reject()
    from openpilot.starpilot.flm.calibration_lifecycle import calibrated_basis
    generation = digest(tuple(float(v) for v in calibration.rpyCalib))
    estimate = generation
    lifecycle = getattr(collector, '_calibration_generation', '')
    if lifecycle:
      if calibrated_basis(calibration) is None:
        return reject()
      generation = digest(('live_vehicle_frame_v2', lifecycle))
    cache = getattr(collector, '_physical_calibrator', None)
    if cache is None or cache[0] != estimate:
      calibrator = PoseCalibrator()
      calibrator.feed_extrinsics_calibration(calibration)
      collector._physical_calibrator = (estimate, calibrator)
    calibrator = collector._physical_calibrator[1]
    if not calibrator.calib_valid:
      return reject()
    pose = calibrator.build_calibrated_pose(Pose.from_device_motion(dm))
    live_lag = float(sm['lateralDelay'].lateralDelay)
    if not math.isfinite(live_lag) or not 0 <= live_lag <= 0.5:
      return reject()
    lag = getattr(collector, '_physical_lag', None)
    if lag is None:
      lag = collector._physical_lag = round(live_lag * 1e9)
    # Freeze an external lag before model/training/heldout; no optimization or
    # noisy numeric drift invents a new evidence identity on each publication.
    ready = getattr(collector, 'ready', ())
    history = {}
    if ready and getattr(getattr(collector, 'adapter', None), 'command_domain', None) != 'ford_path_angle':
      command = ready[-1][0]
      anchor = getattr(collector, '_physical_vm_anchor', None)
      regime = control_vm_regime(command, anchor)
      if regime is None:
        collector.split('vehicle_model_unavailable', reset=True)
        collector._physical_vm_anchor = None
        return reject()
      if anchor is not None and regime != anchor:
        collector.split('vehicle_model_material_change', reset=True)
        collector._physical_vm_anchor = regime
        return reject()
      collector._physical_vm_anchor = regime
      native_epoch = command.native_identity
      native = bool(
        getattr(collector, '_learning_current_native_allowed', False) and ready[-1][1].native_allowed and 0 <= now_ns - ready[-1][1].apply_ns <= 100_000_000
      )
      current_identity = (command.identity, command.generation, command.source_epoch, command.baseline_revision,
        command.controller_revision, command.native_identity)
      for command, _ in ready:
        if (command.identity, command.generation, command.source_epoch, command.baseline_revision,
            command.controller_revision, command.native_identity) != current_identity:
          continue  # State brackets cannot bridge a command/native epoch.
        values = dict(command.replay_inputs)
        if 'vEgo' in values and 'steeringAngleDeg' in values:
          history[command.state_ns] = (float(values['vEgo']), float(values['steeringAngleDeg']))
    else:
      return reject()
    if not native:
      return reject()
    override = bool(
      cs.steeringPressed
      or cs.brakePressed
      or cs.regenBraking
      or any(str(button.type) == 'cancel' for button in cs.buttonEvents)
      or sm['driverMonitoringState'].noResponseForceDecel
    )
    fault = bool(cs.canTimeout or not cs.canValid or cs.steerFaultTemporary or cs.steerFaultPermanent or cs.accFaulted)
    if override or fault:
      return reject()
    # Freeze converted primitives at their original pose receipt. A following
    # CarState may provide the right interpolation bracket, but may not provide
    # a replacement pose timestamp, transform, authority or source identity.
    command = ready[-1][0]
    context = (
      getattr(collector, 'session', ''), getattr(collector, 'model_block_sequence', 0),
      command.identity, command.generation, command.source_epoch,
      command.baseline_revision, command.controller_revision,
      generation, estimate, collector._physical_clock, native_epoch, regime, lag,
      float(cp.steerRatio), float(cp.wheelbase), float(cp.centerToFront), float(cp.mass),
    )
    queue = getattr(collector, '_physical_pose_queue', ())
    old_context = getattr(collector, '_physical_pose_context', None)
    context_changed = old_context is not None and old_context != context
    if old_context != context:
      queue = ()
    collector._physical_pose_context = context
    queue = tuple(value for value in queue if 0 <= now_ns - value.mono_ns <= 100_000_000)
    seen = getattr(collector, '_physical_seen_pose', 0)
    if pose_ns < seen:
      return reject()
    if pose_ns > seen:
      collector._physical_seen_pose = pose_ns
      evidence = PhysicalEvidence(
        pose_ns,
        int(round(sm.recv_time['deviceMotion'] * 1e9)),
        generation,
        digest((float(cp.steerRatio), float(cp.wheelbase), float(cp.centerToFront), float(cp.mass), collector._physical_clock, native_epoch, regime)),
        digest((lag,)), lag, 0.0,
        float(pose.angular_velocity.xyz[2]), float(pose.orientation.xyz[0]), 0.0,
        True, False, False, True,
        source_boot_ns=int(dm.timestamp),
        clock_offset_ns=collector._physical_clock,
        clock_uncertainty_ns=(mono_after - mono_before) // 2,
      )
      queue = (*queue, evidence)[-4:]
    collector._physical_pose_queue = queue
    for index, evidence in enumerate(queue):
      before = [stamp for stamp in history if stamp <= evidence.mono_ns]
      after = [stamp for stamp in history if stamp >= evidence.mono_ns]
      if not before or not after:
        continue
      left, right = max(before), min(after)
      if evidence.mono_ns - left > 100_000_000 or right - evidence.mono_ns > 100_000_000:
        continue
      weight = 0.0 if left == right else (evidence.mono_ns - left) / (right - left)
      speed, steering_angle = tuple(a + (b - a) * weight for a, b in zip(history[left], history[right], strict=True))
      # Consume once, in source order; downstream command/response joins keep
      # their original independent 30 ms actuation and 100 ms response limits.
      collector._physical_pose_queue = queue[index + 1:]
      return replace(evidence, speed_mps=speed, steering_angle_deg=steering_angle)
    if distinguish_pending:
      from openpilot.starpilot.flm.physical_observation import PendingPhysical
      # Healthy but no independently bracketed new pose. Retain original
      # clocks; this is NEVER a new physical sample or a current-fit grant.
      waiting = queue[0] if queue else None
      return PendingPhysical(pose_ns if waiting is None else waiting.mono_ns,
        int(round(sm.recv_time['deviceMotion'] * 1e9)) if waiting is None else waiting.received_ns, context_changed)
    return None
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    return reject()


def original_source_pose_observer(sm, cp, collector, now_ns, *, distinguish_pending=False):
  """Capture original KF source with explicit producer clock provenance."""
  if getattr(collector, "original_source_capture", None) is None:
    return None
  from openpilot.starpilot.flm.original_source_capture import OriginalSourcePose, OriginalSourceClockContract
  contract = getattr(collector, 'original_source_clock_contract', None)
  if not isinstance(contract, OriginalSourceClockContract):
    return None
  from openpilot.selfdrive.locationd.helpers import PoseCalibrator, Pose

  def reject(reason, error=None):
    from openpilot.starpilot.flm.source_observation_report import record_refusal
    record_refusal(collector, sm, now_ns, reason, error=error)
    collector._original_source_pose_queue = ()
    collector.reset_original_source_capture('original_source_unavailable')
    return None

  services = ('deviceMotion', 'extrinsicsCalibration', 'carState', 'driverMonitoringState')
  if not sm.all_checks(list(services)):
    return reject('receiver_health')
  try:
    contract.validate()
    equivalence = None
    if contract.source_domain == 'mono_boot_equivalent_interval_v1':
      from openpilot.starpilot.flm.producer_clock_equivalence import sample_equivalence, source_interval
      try:
        equivalence = sample_equivalence()
      except (AttributeError, TypeError, ValueError, OverflowError, OSError) as error:
        collector.reset_original_source_capture('original_clock_equivalence_discontinuity', hard=True)
        return reject('clock_equivalence_sample', error)
      # This is a fresh receipt witness, never a cached/restamped old proof.
      collector._original_source_equivalence = equivalence
      boot_mono_offset = (equivalence.offset_lower_ns + equivalence.offset_upper_ns) // 2
      clock_offset = 0
      uncertainty = equivalence.uncertainty_ns
      mono_before, _, mono_after = equivalence.samples[-1]
    else:
      mono_before = time.monotonic_ns()
      boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
      mono_after = time.monotonic_ns()
      offset = boot - (mono_before + mono_after) // 2
      cache = getattr(collector, '_original_source_clock', None)
      if cache is None:
        collector._original_source_clock = offset
      elif abs(offset - cache) > 1_000_000 + (mono_after - mono_before) // 2:
        collector.reset_original_source_capture('physical_clock_discontinuity', hard=True)
        return reject('physical_clock_discontinuity')
      boot_mono_offset = collector._original_source_clock
      if contract.source_domain == 'unknown-equivalent-at-receipt' and abs(boot_mono_offset) > (mono_after - mono_before) // 2:
        return reject('clock_equivalence_offset')
      clock_offset = boot_mono_offset if contract.source_domain == 'boottime' else 0
      uncertainty = (mono_after - mono_before) // 2
    dm, calibration, cs = sm['deviceMotion'], sm['extrinsicsCalibration'], sm['carState']
    pose_ns = int(dm.timestamp) - clock_offset
    if (
      not max(0, getattr(collector, '_original_source_revoked_before_ns', 0)) < pose_ns <= now_ns
      or now_ns - pose_ns > 100_000_000
      or not dm.inputsOK
      or not dm.sensorsOK
      or not dm.posenetOK
      or not dm.angularVelocityDevice.valid
      or not dm.orientationNED.valid
      or str(calibration.calStatus) != 'calibrated'
    ):
      return reject('pose_source_or_calibration_flags')
    if equivalence is not None:
      source_lower, source_upper = source_interval(int(dm.timestamp), int(round(sm.recv_time['deviceMotion'] * 1e9)), now_ns, equivalence)
    else:
      source_lower = source_upper = pose_ns
    state_ns = int(sm.logMonoTime['carState'])
    if not 0 < state_ns <= now_ns or now_ns - state_ns > 100_000_000:
      return reject('car_state_source_clock')
    from openpilot.starpilot.flm.calibration_lifecycle import calibrated_basis
    generation = digest(tuple(float(v) for v in calibration.rpyCalib))
    estimate = generation
    lifecycle = getattr(collector, '_calibration_generation', '')
    if lifecycle:
      if calibrated_basis(calibration) is None:
        return reject('calibrated_basis')
      generation = digest(('live_vehicle_frame_v2', lifecycle))
    cache = getattr(collector, '_original_source_calibrator', None)
    if cache is None or cache[0] != estimate:
      calibrator = PoseCalibrator()
      calibrator.feed_extrinsics_calibration(calibration)
      collector._original_source_calibrator = (estimate, calibrator)
    calibrator = collector._original_source_calibrator[1]
    if not calibrator.calib_valid:
      return reject('calibrator_invalid')
    ready = getattr(collector, 'ready', ())
    history = {}
    if ready and getattr(getattr(collector, 'adapter', None), 'command_domain', None) != 'ford_path_angle':
      command = ready[-1][0]
      anchor = getattr(collector, '_original_source_vm_anchor', None)
      regime = control_vm_regime(command, anchor)
      if regime is None:
        collector.reset_original_source_capture('vehicle_model_unavailable')
        collector._original_source_vm_anchor = None
        return reject('vehicle_model_unavailable')
      if anchor is not None and regime != anchor:
        collector.reset_original_source_capture('vehicle_model_material_change', hard=True)
        collector._original_source_vm_anchor = regime
        return reject('vehicle_model_material_change')
      collector._original_source_vm_anchor = regime
      native_epoch = command.native_identity
      native_required = getattr(command, 'native_required', bool(native_epoch))
      if type(native_required) is not bool:
        return reject('native_required_type')
      if native_required and (
          type(native_epoch) is not str or len(native_epoch) != 64 or any(c not in '0123456789abcdef' for c in native_epoch) or
          not getattr(command, 'native_allowed', False) or
          not 0 < getattr(command, 'native_observed_ns', 0) <= getattr(command, 'control_ns', 0) <=
          ready[-1][1].apply_ns <= getattr(command, 'native_expires_ns', 0) or
          command.native_expires_ns-command.native_observed_ns > 200_000_000):
        return reject('original_native_lease')
      ordinary_safety = ()
      ordinary_allowed = True
      if not native_required:
        # Ordinary supported controllers have no axis ACK/lease/identity.
        # Reuse actual legacy Panda/CP/current permission guards unchanged.
        if native_epoch != '' or getattr(command, 'native_observed_ns', 0) != 0 or getattr(command, 'native_expires_ns', 0) != 0:
          return reject('ordinary_native_fields')
        ordinary_allowed, _, _ = current_authority(sm, cp, now_ns=now_ns, required=False)
        cp_sha = getattr(collector.adapter, 'cp_sha256', '')
        if not ordinary_allowed or type(cp_sha) is not str or len(cp_sha) != 64 or any(c not in '0123456789abcdef' for c in cp_sha):
          return reject('ordinary_current_permission')
        configuration = (
          int(cp.alternativeExperience),
          tuple((str(config.safetyModel), int(config.safetyParam)) for config in cp.safetyConfigs),
          tuple((index, str(panda.safetyModel), int(panda.safetyParam), int(panda.alternativeExperience),
            bool(panda.controlsAllowed), bool(panda.safetyRxChecksInvalid), bool(panda.heartbeatLost),
            tuple(str(fault) for fault in panda.faults)) for index, panda in enumerate(sm['pandaStates'])),
        )
        ordinary_safety = ('ordinary-panda-current-v1', cp_sha, int(round(sm.recv_time['pandaStates'] * 1e9)),
          int(sm.logMonoTime['pandaStates']), configuration)
      native = bool(
        ordinary_allowed and
        getattr(collector, '_learning_current_native_allowed', False) and ready[-1][1].native_allowed and 0 <= now_ns - ready[-1][1].apply_ns <= 100_000_000
      )
      current_identity = (command.identity, command.generation, command.source_epoch, command.baseline_revision,
        command.controller_revision, command.native_identity)
    else:
      return reject('ready_pair_unavailable')
    if not native:
      return reject('current_native_or_apply_age')
    override = bool(
      cs.steeringPressed
      or cs.brakePressed
      or cs.regenBraking
      or any(str(button.type) == 'cancel' for button in cs.buttonEvents)
      or sm['driverMonitoringState'].noResponseForceDecel
    )
    fault = bool(cs.canTimeout or not cs.canValid or cs.steerFaultTemporary or cs.steerFaultPermanent or cs.accFaulted)
    if override or fault:
      return reject('physical_override_or_fault')
    # Freeze converted primitives at their original pose receipt. A following
    # CarState may provide the right interpolation bracket, but may not provide
    # a replacement pose timestamp, transform, authority or source identity.
    command = ready[-1][0]
    def observation_context():
      return (
      getattr(collector, 'session', ''),
      command.identity, command.generation, command.source_epoch,
      command.baseline_revision, command.controller_revision,
      generation, ('original_source_clock_generation', getattr(collector, '_original_source_clock_generation', 0)),
      (contract.source_domain, contract.producer_revision, contract.proof_sha256, contract.scope), native_epoch, regime,
      float(cp.steerRatio), float(cp.wheelbase), float(cp.centerToFront), float(cp.mass),
    )
    context = observation_context()
    queue = getattr(collector, '_original_source_pose_queue', ())
    old_context = getattr(collector, '_original_source_pose_context', None)
    context_changed = old_context is not None and old_context != context
    if old_context != context:
      queue = ()
      if old_context is not None:
        collector.reset_original_source_capture('original_source_context_changed', hard=True)
        # Hard reset advances the clock generation. Bind the NEW epoch now;
        # saving the pre-reset context would reset again on every healthy poll.
        context = observation_context()
    collector._original_source_pose_context = context
    queue = tuple(value for value in queue if 0 <= now_ns - value.mono_ns <= 100_000_000)
    if pose_ns <= getattr(collector, '_original_source_revoked_before_ns', 0):
      return reject('pose_revoked')
    seen = getattr(collector, '_original_source_seen_pose', 0)
    if pose_ns < seen:
      return reject('pose_clock_regression')
    if pose_ns > seen:
      # Convert a new original pose once; bracket retries retain that exact row.
      pose = calibrator.build_calibrated_pose(Pose.from_device_motion(dm))
      collector._original_source_seen_pose = pose_ns
      evidence = OriginalSourcePose(
        mono_ns=pose_ns, received_ns=int(round(sm.recv_time['deviceMotion'] * 1e9)), observed_ns=now_ns,
        source_boot_ns=int(dm.timestamp) if contract.source_domain == 'boottime' else 0, clock_offset_ns=clock_offset,
        source_timestamp_ns=int(dm.timestamp), source_clock_domain=contract.source_domain,
        source_clock_contract=(contract.source_domain, contract.producer_revision, contract.proof_sha256, contract.scope),
        boot_mono_offset_ns=boot_mono_offset,
        clock_uncertainty_ns=uncertainty,
        clock_equivalence=() if equivalence is None else equivalence.primitive(),
        clock_generation=getattr(collector, '_original_source_clock_generation', 0),
        source_lower_ns=source_lower, source_upper_ns=source_upper,
        calibration=generation, calibration_estimate=estimate, context=context,
        yaw_rate=float(pose.angular_velocity.xyz[2]), roll_rad=float(pose.orientation.xyz[0]),
        native_identity=native_epoch, native_allowed=True, valid=True,
        native_required=native_required, native_control_ns=getattr(command, 'control_ns', 0),
        native_observed_ns=getattr(command, 'native_observed_ns', 0),
        native_expires_ns=getattr(command, 'native_expires_ns', 0), ordinary_safety=ordinary_safety,
        source_epoch=command.source_epoch, identity=command.identity,
        baseline_revision=command.baseline_revision, controller_revision=command.controller_revision,
        source_state_ns=state_ns, native_apply_ns=ready[-1][1].apply_ns,
        angular_velocity_device=tuple(float(v) for v in (dm.angularVelocityDevice.x, dm.angularVelocityDevice.y, dm.angularVelocityDevice.z)),
        orientation_ned=tuple(float(v) for v in (dm.orientationNED.x, dm.orientationNED.y, dm.orientationNED.z)),
        calibration_rpy=tuple(float(v) for v in calibration.rpyCalib),
      )
      queue = (*queue, evidence)[-4:]
    collector._original_source_pose_queue = queue
    if queue:
      # No original pose is waiting after a completed publication. Rebuild
      # state brackets only for retained/new rows, after all current guards.
      from openpilot.starpilot.flm.original_pose_brackets import state_brackets
      history = state_brackets(collector, current_identity, now_ns)
    for index, evidence in enumerate(queue):
      before = [stamp for stamp in history if stamp <= (evidence.source_lower_ns or evidence.mono_ns)]
      after = [stamp for stamp in history if stamp >= (evidence.source_upper_ns or evidence.mono_ns)]
      if not before or not after:
        continue
      left, right = max(before), min(after)
      if (evidence.source_upper_ns or evidence.mono_ns) - left > 100_000_000 or right - (evidence.source_lower_ns or evidence.mono_ns) > 100_000_000:
        continue
      weight = 0.0 if left == right else (evidence.mono_ns - left) / (right - left)
      speed, steering_angle = tuple(a + (b - a) * weight for a, b in zip(history[left], history[right], strict=True))
      # Consume once, in source order; downstream command/response joins keep
      # their original independent 30 ms actuation and 100 ms response limits.
      collector._original_source_pose_queue = queue[index + 1:]
      return replace(evidence, speed_mps=speed, steering_angle_deg=steering_angle, completed_ns=now_ns,
        left_state_ns=left, right_state_ns=right, left_state=history[left], right_state=history[right])
    if distinguish_pending:
      from openpilot.starpilot.flm.physical_observation import PendingPhysical
      # Healthy but no independently bracketed new pose. Retain original
      # clocks; this is NEVER a new physical sample or a current-fit grant.
      waiting = queue[0] if queue else None
      return PendingPhysical(pose_ns if waiting is None else waiting.mono_ns,
        int(round(sm.recv_time['deviceMotion'] * 1e9)) if waiting is None else waiting.received_ns, context_changed)
    return None
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
    return reject('source_observation_exception', error)
