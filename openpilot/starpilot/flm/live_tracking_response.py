"""Retained numerical response identification plus fresh real-pose residuals.

A certificate is never a current/native permission. No desired-model trajectory
is used as measured vehicle response. This code belongs to the optional learner.
"""

from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, replace
import hashlib
import struct
from types import GetSetDescriptorType
import math
from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding

VERSION = 'live-tracking-response-v1'
SOURCE_VERSION = 'live-tracking-source-context-v3'
MAX_FRESH_NS = 100_000_000
WINDOW_NS = 2_000_000_000


def _hex(value):
  return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


@dataclass(frozen=True)
class ResponseCertificate:
  version: str
  binding: LiveTrialBinding
  model_revision: str
  lag_ns: int
  slope: float
  intercept: float
  command_low: float
  command_high: float
  heldout_rmse: float
  train_block_ids: tuple[str, ...]
  heldout_block_ids: tuple[str, ...]
  source_inventory_sha256: str
  identification_sha256: str
  identified_ns: int
  provenance: str = "source-calibration-eight-v1"
  parent_model_revision: str = ""
  requalification_sha256: str = ""


@dataclass(frozen=True)
class TrackingSourceContext:
  """Current controller/source identity, NOT a vehicle response model.

  lag_ns is the delay consumed by the selected controller, not an identified
  plant delay. No slope, residual accuracy or training/heldout blocks exist.
  This context never grants native permission or validates a knob correction.
  """

  version: str
  binding: LiveTrialBinding
  model_revision: str  # Legacy binding slot; v3 contains a SOURCE context hash.
  lag_ns: int
  source_inventory_sha256: str
  observed_ns: int
  provenance: str = 'current-tracking-source-v3'
  parent_model_revision: str = ''
  requalification_sha256: str = ''


_SOURCE_FIELDS = (
  'version',
  'binding',
  'model_revision',
  'lag_ns',
  'source_inventory_sha256',
  'observed_ns',
  'provenance',
  'parent_model_revision',
  'requalification_sha256',
)


def source_context(binding, control, pose, *, now_ns):
  """Called with the actual collector's current pair and original raw pose.

  Fresh independent pose/native/source checks remain in ResponseMonitor; the
  optimizer receives no offer until the full current observation window passes.
  """
  from openpilot.starpilot.flm.fullrate_collector import ControlEvidence
  from openpilot.starpilot.flm.original_source_capture import OriginalSourcePose, validate_pose
  from openpilot.starpilot.flm.runtime_adapter_registry import digest

  if type(control) is not ControlEvidence or type(pose) is not OriginalSourcePose:
    raise ValueError('actual current control and original pose required')
  binding.validate()
  validate_pose(pose, now_ns)
  if (
    not 0 < control.state_ns <= control.control_ns <= now_ns
    or control.native_required is not binding.native_required
    or (control.identity, control.source_epoch, control.baseline_revision, control.controller_revision, control.native_identity)
    != (binding.identity, binding.source_epoch, binding.baseline_revision, binding.controller_revision, binding.native_identity)
    or (pose.identity, pose.source_epoch, pose.baseline_revision, pose.controller_revision, pose.native_identity)
    != (binding.identity, binding.source_epoch, binding.baseline_revision, binding.controller_revision, binding.native_identity)
  ):
    raise ValueError('current source context identity differs')
  values = dict(control.replay_inputs)
  delay = values['lat_delay']
  if type(delay) not in (int, float) or not math.isfinite(delay) or not 0 <= delay <= 0.5:
    raise ValueError('actual consumed controller delay required')
  inventory = digest(
    ('current-tracking-source-v3', tuple(vars(binding).items()), control.control_ns, control.state_ns, control.source_receipt_ns, pose.mono_ns, pose.context)
  )
  value = TrackingSourceContext(SOURCE_VERSION, binding, '0' * 64, round(delay * 1e9), inventory, now_ns)
  revision = _revision(value)
  return validate_certificate(replace(value, model_revision=revision, binding=replace(binding, physical_model_revision=revision)))


def _source_key(context):
  owned = _owned(context, TrackingSourceContext, _SOURCE_FIELDS)
  if (
    any(
      type(owned[name]) is not str
      for name in ('version', 'model_revision', 'source_inventory_sha256', 'provenance', 'parent_model_revision', 'requalification_sha256')
    )
    or type(owned['lag_ns']) is not int
    or type(owned['observed_ns']) is not int
  ):
    raise ValueError('exact source context scalar types')
  return (
    owned['version'],
    _binding_values(owned['binding']),
    owned['model_revision'],
    owned['lag_ns'],
    owned['source_inventory_sha256'],
    owned['observed_ns'],
    owned['provenance'],
    owned['parent_model_revision'],
    owned['requalification_sha256'],
  )


_VALID_SOURCE_CONTEXTS = OrderedDict()


def _validated_source_key(context):
  key = _source_key(context)
  if key in _VALID_SOURCE_CONTEXTS:
    _VALID_SOURCE_CONTEXTS.move_to_end(key)
    return key
  context.binding.validate()
  if (
    context.version != SOURCE_VERSION
    or not 0 <= context.lag_ns <= 500_000_000
    or context.observed_ns <= 0
    or not all(_hex(value) for value in (context.model_revision, context.source_inventory_sha256))
    or context.binding.physical_model_revision != context.model_revision
    or _revision(context) != context.model_revision
  ):
    raise ValueError('current source context identity required')
  if context.provenance == 'current-tracking-source-v3':
    if context.parent_model_revision or context.requalification_sha256:
      raise ValueError('initial source context cannot claim restoration')
  elif context.provenance == 'retained-current-source-v3':
    if not _hex(context.parent_model_revision) or not _hex(context.requalification_sha256):
      raise ValueError('retained source context needs fresh observations')
  else:
    raise ValueError('unsupported source context provenance')
  # Only immutable identity content; witness clocks and current binding stay live.
  _VALID_SOURCE_CONTEXTS[key] = None
  if len(_VALID_SOURCE_CONTEXTS) > 8:
    _VALID_SOURCE_CONTEXTS.popitem(last=False)
  return key


def validate_source_context(context):
  _validated_source_key(context)
  return context


_CERT_FIELDS = (
  'version',
  'binding',
  'model_revision',
  'lag_ns',
  'slope',
  'intercept',
  'command_low',
  'command_high',
  'heldout_rmse',
  'train_block_ids',
  'heldout_block_ids',
  'source_inventory_sha256',
  'identification_sha256',
  'identified_ns',
  'provenance',
  'parent_model_revision',
  'requalification_sha256',
)
_BIND_FIELDS = (
  'identity',
  'cp_sha256',
  'controller_revision',
  'baseline_revision',
  'source_epoch',
  'reset_generation',
  'calibration_generation',
  'vehicle_model_generation',
  'native_identity',
  'physical_model_revision',
  'persistent_source_epoch',
  'native_required',
)
_WITNESS_FIELDS = (
  'certificate',
  'binding',
  'last_control_ns',
  'last_apply_ns',
  'last_pose_ns',
  'window_start_ns',
  'independent_poses',
  'residual_rmse',
  'valid_until_ns',
)
_VALIDATED = object()


def physical_calibration_generation(binding):
  from openpilot.starpilot.flm.runtime_adapter_registry import digest

  token = binding.calibration_generation
  if type(token) is not str or len(token) != 32 or any(char not in '0123456789abcdef' for char in token):
    raise ValueError('actual raw calibrated vehicle-frame lifecycle token required')
  return digest(('live_vehicle_frame_v2', token))


def _owned(value, cls, names):
  if type(value) is not cls or type(cls) is not type or cls.__bases__ != (object,) or cls.__getattribute__ is not object.__getattribute__:
    raise ValueError('exact plain physical witness class')
  namespace = vars(cls)
  if (
    '__getattr__' in namespace
    or type(namespace.get('__dict__')) is not GetSetDescriptorType
    or any(name in namespace and type(namespace[name]) not in (type(None), bool, int, float, str, tuple) for name in names)
  ):
    raise ValueError('changed physical witness field accessor')
  owned = object.__getattribute__(value, '__dict__')
  if type(owned) is not dict or any(type(key) is not str for key in owned) or any(name not in owned for name in names):
    raise ValueError('exact physical witness field dictionary')
  return owned


def _binding_values(binding):
  from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding

  owned = _owned(binding, LiveTrialBinding, _BIND_FIELDS)
  values = tuple(owned[name] for name in _BIND_FIELDS)
  if any(type(value) is not str or not value.isascii() or len(value) > 192 for value in values[:-1]) or type(values[-1]) is not bool:
    raise ValueError('immutable primitive physical binding')
  return values


def _certificate_key(certificate):
  if type(certificate) is TrackingSourceContext:
    return _source_key(certificate)
  owned = _owned(certificate, ResponseCertificate, _CERT_FIELDS)
  binding = _binding_values(owned['binding'])
  if any(
    type(owned[name]) is not str
    for name in (
      'version',
      'model_revision',
      'source_inventory_sha256',
      'identification_sha256',
      'provenance',
      'parent_model_revision',
      'requalification_sha256',
    )
  ) or any(type(owned[name]) is not int for name in ('lag_ns', 'identified_ns')):
    raise ValueError('physical certificate scalar types')
  floats = tuple(owned[name] for name in ('slope', 'intercept', 'command_low', 'command_high', 'heldout_rmse'))
  if any(type(value) not in (int, float) or not math.isfinite(value) for value in floats):
    raise ValueError('finite physical certificate values')
  ids = tuple(owned[name] for name in ('train_block_ids', 'heldout_block_ids'))
  if any(type(rows) is not tuple or len(rows) != 4 or any(type(v) is not str or len(v) > 128 for v in rows) for rows in ids):
    raise ValueError('bounded independent identification blocks')
  return (
    owned['version'],
    binding,
    owned['model_revision'],
    owned['lag_ns'],
    struct.pack('!5d', *floats),
    *ids,
    owned['source_inventory_sha256'],
    owned['identification_sha256'],
    owned['identified_ns'],
    owned['provenance'],
    owned['parent_model_revision'],
    owned['requalification_sha256'],
  )


def _revision(certificate):
  key = _certificate_key(certificate)
  binding = key[1][:9] + key[1][10:]  # Own revision cannot recursively bind itself.
  return hashlib.sha256(repr((key[0], binding, *key[3:])).encode('utf-8', 'surrogatepass')).hexdigest()


def validate_certificate(certificate):
  if type(certificate) is TrackingSourceContext:
    return validate_source_context(certificate)
  from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding

  if (
    type(certificate) is not ResponseCertificate
    or type(certificate.binding) is not LiveTrialBinding
    or certificate.version != VERSION
    or type(certificate.lag_ns) is not int
    or not 0 <= certificate.lag_ns <= 500_000_000
    or any(
      type(v) not in (int, float) or not math.isfinite(v)
      for v in (certificate.slope, certificate.intercept, certificate.command_low, certificate.command_high, certificate.heldout_rmse)
    )
    or certificate.slope <= 0
    or certificate.command_low >= certificate.command_high
    or not 0 <= certificate.heldout_rmse <= 0.05
    or type(certificate.identified_ns) is not int
    or certificate.identified_ns <= 0
    or type(certificate.train_block_ids) is not tuple
    or len(certificate.train_block_ids) != 4
    or type(certificate.heldout_block_ids) is not tuple
    or len(certificate.heldout_block_ids) != 4
    or any(type(v) is not str or not 1 <= len(v) <= 128 for v in certificate.train_block_ids + certificate.heldout_block_ids)
    or len(set(certificate.train_block_ids + certificate.heldout_block_ids)) != 8
    or not all(_hex(v) for v in (certificate.model_revision, certificate.source_inventory_sha256, certificate.identification_sha256))
    or certificate.binding.physical_model_revision != certificate.model_revision
    or _revision(certificate) != certificate.model_revision
  ):
    raise ValueError('source-bound independently identified response certificate required')
  certificate.binding.validate()
  if certificate.provenance == 'source-calibration-eight-v1':
    if certificate.parent_model_revision or certificate.requalification_sha256:
      raise ValueError('initial model cannot claim retained requalification')
  elif certificate.provenance == 'retained-current-residual-v1':
    if not _hex(certificate.parent_model_revision) or not _hex(certificate.requalification_sha256):
      raise ValueError('retained model needs original parent and fresh requalification')
  else:
    raise ValueError('unsupported response provenance')
  return certificate


def certificate_from_prepared(prepared, binding, *, original_packet):
  """Called only in the actual initial source/native/calibration worker.

  The PreparedPlant comes from existing prepare(), after original controller,
  native CAN and independent train/heldout physical identification. This is a
  numerical result, not a profile acceptance or transferable native verdict.
  """
  from openpilot.starpilot.flm.source_plant_fit import PreparedPlant
  from openpilot.starpilot.flm import plant_lag
  from openpilot.starpilot.flm.physical_response import PhysicalModel
  from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding

  if type(prepared) is not PreparedPlant or type(binding) is not LiveTrialBinding or type(prepared.model) is not PhysicalModel:
    raise ValueError('actual prepared physical experiment required')
  source, result, model = prepared.binding, prepared.identification, prepared.model
  from openpilot.starpilot.flm.runtime_adapter_registry import digest, canonical_physical_context
  from openpilot.starpilot.flm.source_plant_fit import experiment_context

  original_context = original_packet['source_poses'][0]['context']
  if (
    prepared.context != experiment_context(original_packet, source)
    or len(original_context) != 15
    or original_context[5] != binding.controller_revision
    or source.controller_revision != digest(binding.controller_revision)
    or source.vehicle_model_generation != digest((original_context[2], original_context[10], list(original_context[11:])))
    or binding.vehicle_model_generation != digest(tuple(original_context[10]))
    or source.native_required is not binding.native_required
  ):
    raise ValueError('actual raw controller/vehicle/source binding differs')
  if (
    result.status != 'physical_affine_test_passed'
    or result.decision is None
    or not plant_lag.heldout_supported(result)
    or (source.identity, source.cp_sha256, source.baseline_revision, source.source_epoch, source.calibration_generation, source.native_identity)
    != (binding.identity, binding.cp_sha256, binding.baseline_revision, binding.source_epoch, physical_calibration_generation(binding), binding.native_identity)
    or result.binding_digest != plant_lag.binding_digest(source)
    or result.decision.protocol != plant_lag.PHYSICAL_PROTOCOL
    or model.identity != binding.identity
    or model.context != canonical_physical_context(prepared.context)
    or model.command_low != result.decision.command_low
    or model.command_high != result.decision.command_high
    or model.heldout_rmse != max(error for _, error, _ in result.heldout_blocks)
    or model.lag_ns != result.decision.lag_ns
    or model.slope != result.decision.slope
    or model.intercept != result.decision.intercept
    or model.train_blocks != result.decision.train_block_ids
    or model.heldout_blocks != tuple(block for block, _, _ in result.heldout_blocks)
  ):
    raise ValueError('original independent physical identification differs')
  value = ResponseCertificate(
    VERSION,
    binding,
    '0' * 64,
    model.lag_ns,
    model.slope,
    model.intercept,
    model.command_low,
    model.command_high,
    model.heldout_rmse,
    model.train_blocks,
    model.heldout_blocks,
    prepared.context[2],
    plant_lag.digest(asdict(result)),
    max(row.mono_ns for row in prepared.source_poses),
  )
  revision = _revision(value)
  return validate_certificate(replace(value, model_revision=revision, binding=replace(binding, physical_model_revision=revision)))


@dataclass(frozen=True)
class ResponseWitness:
  certificate: ResponseCertificate | TrackingSourceContext
  binding: LiveTrialBinding
  last_control_ns: int
  last_apply_ns: int
  last_pose_ns: int
  window_start_ns: int
  independent_poses: int
  residual_rmse: float | None
  valid_until_ns: int


def _validate_raw_witness(witness, binding, now_ns, *, content=True):
  if type(witness) is not ResponseWitness:
    raise ValueError('fresh source physical residual witness required')
  owned = _owned(witness, ResponseWitness, _WITNESS_FIELDS)
  cert = owned['certificate']
  source_key = _validated_source_key(cert) if content and type(cert) is TrackingSourceContext else None
  if content and source_key is None:
    cert = validate_certificate(cert)
  source_only = type(cert) is TrackingSourceContext
  # Share immutable values only within this call. Every new call still checks
  # the actual dictionaries/accessors, expected binding and original clocks.
  cert_binding = cert.binding
  cert_values = source_key[1] if source_key is not None else _binding_values(cert_binding)
  expected = cert_values if binding is cert_binding else _binding_values(binding)
  actual_binding = owned['binding']
  actual = (cert_values if actual_binding is cert_binding else
            expected if actual_binding is binding else _binding_values(actual_binding))
  if (
    actual != expected
    or cert_values != expected
    or any(
      type(v) is not int
      for v in (
        witness.last_control_ns,
        witness.last_apply_ns,
        witness.last_pose_ns,
        witness.window_start_ns,
        witness.independent_poses,
        witness.valid_until_ns,
        now_ns,
      )
    )
    or not 0 < witness.last_control_ns <= witness.last_apply_ns <= witness.last_pose_ns <= now_ns <= witness.valid_until_ns
    or not witness.valid_until_ns <= witness.last_pose_ns + MAX_FRESH_NS
    or not 0 <= witness.last_pose_ns - cert.lag_ns - witness.last_apply_ns <= 30_000_000
    or source_only
    and cert.provenance == 'current-tracking-source-v3'
    and cert.observed_ns > witness.last_pose_ns
    or not 0 < witness.window_start_ns <= witness.last_pose_ns
    or witness.last_pose_ns - witness.window_start_ns > WINDOW_NS
    or not 8 <= witness.independent_poses <= 80
    or (
      witness.residual_rmse is not None
      if source_only
      else type(witness.residual_rmse) not in (int, float)
      or not math.isfinite(witness.residual_rmse)
      or not 0 <= witness.residual_rmse <= max(0.01, 3 * cert.heldout_rmse)
    )
  ):
    raise ValueError('physical response witness stale, unqualified or mismatched')
  return witness


@dataclass(frozen=True)
class ValidatedResponseWitness:
  value: ResponseWitness
  certificate: ResponseCertificate | TrackingSourceContext
  binding: LiveTrialBinding
  witness_dictionary: object
  certificate_dictionary: object
  binding_dictionary: object
  certificate_binding: object
  certificate_binding_dictionary: object
  witness_key: tuple
  certificate_key: tuple
  _seal: object


def _witness_key(witness):
  owned = _owned(witness, ResponseWitness, _WITNESS_FIELDS)
  source_only = type(owned['certificate']) is TrackingSourceContext
  if any(type(owned[name]) is not int for name in _WITNESS_FIELDS[2:7] + ('valid_until_ns',)) or (
    owned['residual_rmse'] is not None if source_only else type(owned['residual_rmse']) not in (int, float) or not math.isfinite(owned['residual_rmse'])
  ):
    raise ValueError('exact immutable source witness scalars')
  return (
    *_binding_values(owned['binding']),
    *(owned[name] for name in _WITNESS_FIELDS[2:7]),
    None if source_only else struct.pack('!d', owned['residual_rmse']),
    owned['valid_until_ns'],
  )


def prepare_response_witness(witness, binding, now_ns):
  """Content validation once on a new owned delivery, never permission cache."""
  value = _validate_raw_witness(witness, binding, now_ns)
  if type(value.certificate) is TrackingSourceContext:
    return value  # Small source context is revalidated on EVERY consumer tick.
  return ValidatedResponseWitness(
    value,
    value.certificate,
    value.binding,
    _owned(value, ResponseWitness, _WITNESS_FIELDS),
    _owned(value.certificate, ResponseCertificate, _CERT_FIELDS),
    _owned(value.binding, type(value.binding), _BIND_FIELDS),
    value.certificate.binding,
    _owned(value.certificate.binding, type(value.certificate.binding), _BIND_FIELDS),
    _witness_key(value),
    _certificate_key(value.certificate),
    _VALIDATED,
  )


def response_witness_value(value):
  if type(value) is ResponseWitness:
    return value
  if type(value) is not ValidatedResponseWitness:
    raise ValueError('exact owned physical response wrapper')
  _owned(
    value,
    ValidatedResponseWitness,
    (
      'value',
      'certificate',
      'binding',
      'witness_dictionary',
      'certificate_dictionary',
      'binding_dictionary',
      'certificate_binding',
      'certificate_binding_dictionary',
      'witness_key',
      'certificate_key',
      '_seal',
    ),
  )
  if (
    value._seal is not _VALIDATED
    or type(value.value) is not ResponseWitness
    or type(value.certificate) is not ResponseCertificate
    or value.value.certificate is not value.certificate
    or value.value.binding is not value.binding
    or value.certificate.binding is not value.certificate_binding
    or _owned(value.certificate_binding, type(value.certificate_binding), _BIND_FIELDS) is not value.certificate_binding_dictionary
    or _owned(value.value, ResponseWitness, _WITNESS_FIELDS) is not value.witness_dictionary
    or _owned(value.certificate, ResponseCertificate, _CERT_FIELDS) is not value.certificate_dictionary
    or _owned(value.binding, type(value.binding), _BIND_FIELDS) is not value.binding_dictionary
    or _witness_key(value.value) != value.witness_key
    or _certificate_key(value.certificate) != value.certificate_key
  ):
    raise ValueError('owned physical response delivery changed')
  return value.value


def validate_response_witness(witness, binding, now_ns):
  value = response_witness_value(witness)
  return _validate_raw_witness(value, binding, now_ns, content=type(witness) is not ValidatedResponseWitness)


PERSISTENT_RESIDUAL_NS = 3_000_000_000
PERSISTENT_RESIDUAL_POSES = 60


def verified_clipped_input(replay, control, output, binding):
  """Actual delivered physical input, never an unclipped command or grant.

  A measured/driver native limiter can change a known actuator input without
  invalidating the physical observation. Verify its original CAN and preserve
  every source flag. Numerical probing and current application remain strict.
  """
  from openpilot.starpilot.flm.controller_replay import ControllerReplay
  from openpilot.starpilot.flm.fullrate_collector import ControlEvidence, AppliedEvidence
  from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
  from openpilot.starpilot.flm.torque_eligibility import bounded_context_output, asynchronous_hint, consumed_sources_current

  try:
    if (
      type(replay) is not ControllerReplay
      or type(control) is not ControlEvidence
      or type(output) is not AppliedEvidence
      or replay.identity != binding.identity
      or replay.baseline_revision != binding.baseline_revision
      or car_params_digest(replay.factory.cp) != binding.cp_sha256
      or getattr(replay, 'source_controller_revision', None) != binding.controller_revision
      or control.controller_revision != binding.controller_revision
      or control.identity != binding.identity
      or control.source_epoch != binding.source_epoch
      or control.baseline_revision != binding.baseline_revision
      or control.native_identity != binding.native_identity
      or control.native_required is not binding.native_required
      or not control.enabled
      or not control.active
      or not control.valid
      or control.manual
      or control.override
      or control.fault
      or control.saturated
      or control.model_wobble
      or control.limited
      and not asynchronous_hint(control)
      or not bounded_context_output(output)
      or not consumed_sources_current(control, output)
      or control.native_required
      and (
        not control.native_allowed
        or not 0 < control.native_observed_ns <= control.control_ns <= output.apply_ns <= control.native_expires_ns <= control.native_observed_ns + 200_000_000
      )
    ):
      return False
    return replay.applied_matches(control, output) is True
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    return False


class ResponseMonitor:
  def __init__(self, certificate):
    self.certificate = validate_certificate(certificate)
    self.rows = deque(maxlen=80)
    self.reason = 'fresh_residual_samples_required'
    self.residual_failure_start_ns = self.residual_failure_count = 0

  def reset(self, reason):
    self.rows.clear()
    self.reason = reason
    self.residual_failure_start_ns = self.residual_failure_count = 0

  def observe(self, control, output, pose, *, now_ns, binding, current_native, replay=None):
    from openpilot.starpilot.flm.fullrate_collector import ControlEvidence, AppliedEvidence
    from openpilot.starpilot.flm.original_source_capture import OriginalSourcePose, validate_pose
    from openpilot.starpilot.flm.torque_eligibility import consumed_sources_current
    from openpilot.starpilot.flm.torque_eligibility import exact_unclipped_pair
    from openpilot.starpilot.flm.torque_emission_replay import delivered
    from openpilot.starpilot.flm.runtime_adapter_registry import digest, control_vm_regime

    c = self.certificate
    try:
      if (
        binding != c.binding
        or current_native is not True
        or type(control) is not ControlEvidence
        or type(output) is not AppliedEvidence
        or type(pose) is not OriginalSourcePose
      ):
        raise ValueError('response_current_source_or_native_unavailable')
      validate_pose(pose, now_ns)
      observed_clip = verified_clipped_input(replay, control, output, binding) if output.limited else False
      if (
        type(control.vehicle_model_anchor) is not tuple
        or len(control.vehicle_model_anchor) != 3
        or control_vm_regime(control) != control.vehicle_model_anchor
        or digest(control.vehicle_model_anchor) != binding.vehicle_model_generation
        or len(pose.context) != 15
        or tuple(pose.context[10]) != control.vehicle_model_anchor
        or control.generation != pose.context[2]
        or control.controller_revision != binding.controller_revision
        or pose.controller_revision != binding.controller_revision
        or control.native_required is not binding.native_required
        or pose.native_required is not binding.native_required
        or (control.identity, control.source_epoch, control.baseline_revision, control.native_identity)
        != (binding.identity, binding.source_epoch, binding.baseline_revision, binding.native_identity)
        or (pose.identity, pose.source_epoch, pose.baseline_revision, pose.calibration, pose.native_identity)
        != (binding.identity, binding.source_epoch, binding.baseline_revision, physical_calibration_generation(binding), binding.native_identity)
        or not control.enabled
        or not control.active
        or not control.valid
        or control.manual
        or control.override
        or control.fault
        or control.saturated
        or (control.limited and not exact_unclipped_pair(control, output) and not observed_clip)
        or control.model_wobble
        or not output.active
        or not output.valid
        or output.override
        or output.fault
        or output.limited
        and not observed_clip
        or control.native_required
        and (not control.native_allowed or not output.native_allowed)
        or output.control_ns != control.control_ns
        or not consumed_sources_current(control, output)
        or not 0 < control.control_ns <= output.apply_ns <= pose.source_lower_ns - c.lag_ns
        or not 0 <= pose.source_upper_ns - c.lag_ns - output.apply_ns <= 30_000_000
        or not 0 <= now_ns - pose.mono_ns <= MAX_FRESH_NS
      ):
        raise ValueError('response_original_source_pair_unqualified')
      command = -delivered(output)
      measured = pose.speed_mps * pose.yaw_rate - 9.81 * math.sin(pose.roll_rad)
      if not math.isfinite(measured):
        raise ValueError('finite measured source response required')
      source_only = type(c) is TrackingSourceContext
      error = None if source_only else measured - (c.slope * command + c.intercept)
      if not source_only and not c.command_low <= command <= c.command_high:
        raise ValueError('response_command_outside_identified_range')
      # Drift is assessed on a complete rolling window, not an isolated 3-sigma
      # Gaussian sample. Source/native/driver failures above still reset now.
      if self.rows and pose.mono_ns <= self.rows[-1][0]:
        return False  # Duplicate data neither renews freshness nor erases it.
      if self.rows and pose.mono_ns - self.rows[-1][0] > MAX_FRESH_NS:
        self.reset('response_source_gap')
      self.rows.append((pose.mono_ns, control.control_ns, output.apply_ns, error))
      while self.rows and pose.mono_ns - self.rows[0][0] > WINDOW_NS:
        self.rows.popleft()
      self.reason = 'qualified' if len(self.rows) >= 8 else 'fresh_residual_samples_required'
      # Count ONLY new, source/native/driver/range-qualified physical poses.
      # Missing evidence and interrupted data never count as model drift.
      residual_bad = not source_only and len(self.rows) >= 8 and sum(row[3] ** 2 for row in self.rows) / len(self.rows) > max(0.01, 3 * c.heldout_rmse) ** 2
      if residual_bad:
        if self.residual_failure_start_ns == 0:
          self.residual_failure_start_ns = pose.mono_ns
        self.residual_failure_count += 1
      else:
        self.residual_failure_start_ns = self.residual_failure_count = 0
      return True
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
      self.reset(str(error))
      return False

  def persistent_residual_failure(self, now_ns):
    return bool(
      type(self.certificate) is not TrackingSourceContext
      and self.rows
      and type(now_ns) is int
      and 0 <= now_ns - self.rows[-1][0] <= MAX_FRESH_NS
      and self.residual_failure_start_ns > 0
      and self.rows[-1][0] - self.residual_failure_start_ns >= PERSISTENT_RESIDUAL_NS
      and self.residual_failure_count >= PERSISTENT_RESIDUAL_POSES
    )

  def witness(self, now_ns):
    if len(self.rows) < 8:
      return None
    pose, control, apply, _ = self.rows[-1]
    value = ResponseWitness(
      self.certificate,
      self.certificate.binding,
      control,
      apply,
      pose,
      self.rows[0][0],
      len(self.rows),
      None if type(self.certificate) is TrackingSourceContext else math.sqrt(sum(row[3] ** 2 for row in self.rows) / len(self.rows)),
      pose + MAX_FRESH_NS,
    )
    try:
      return validate_response_witness(value, self.certificate.binding, now_ns)
    except ValueError:
      return None


def certificate_to_dict(value):
  return asdict(validate_certificate(value))


def certificate_from_dict(value):
  from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding

  if type(value) is dict and value.get('version') == SOURCE_VERSION:
    if set(value) != set(TrackingSourceContext.__dataclass_fields__):
      raise ValueError('exact source context history shape')
    data = dict(value)
    if type(data['binding']) is not dict or set(data['binding']) != set(LiveTrialBinding.__dataclass_fields__):
      raise ValueError('exact source context binding history shape')
    data['binding'] = LiveTrialBinding(**data['binding'])
    return validate_source_context(TrackingSourceContext(**data))
  from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding

  if type(value) is not dict or set(value) != set(ResponseCertificate.__dataclass_fields__):
    raise ValueError('response certificate fields')
  copied = dict(value)
  copied['binding'] = LiveTrialBinding(**copied['binding'])
  copied['train_block_ids'], copied['heldout_block_ids'] = tuple(copied['train_block_ids']), tuple(copied['heldout_block_ids'])
  return validate_certificate(ResponseCertificate(**copied))


def witness_to_dict(value, *, now_ns):
  raw = response_witness_value(value)
  return asdict(validate_response_witness(value, raw.binding, now_ns))


def witness_from_dict(value, *, binding, now_ns):
  from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding

  if type(value) is not dict or set(value) != set(ResponseWitness.__dataclass_fields__):
    raise ValueError('response witness fields')
  copied = dict(value)
  copied['certificate'] = certificate_from_dict(copied['certificate'])
  copied['binding'] = LiveTrialBinding(**copied['binding'])
  return validate_response_witness(ResponseWitness(**copied), binding, now_ns)
