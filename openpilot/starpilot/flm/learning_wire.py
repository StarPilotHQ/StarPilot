"""Typed custom child snapshots; legacy Event fields and linear fit unchanged."""

from typing import TypedDict, cast


from types import SimpleNamespace
from functools import lru_cache
import struct

from capnp.lib.capnp import _DynamicListReader, _DynamicStructReader
from openpilot.cereal import custom, messaging
from openpilot.starpilot.flm.adaptive_profile import Parameter, Profile, ReplayValidation, replay_accepts
from openpilot.starpilot.flm.fullrate_collector import ControlEvidence, AppliedEvidence, identity, finite
from openpilot.starpilot.flm.native_refinement_wire import valid_inputs, EXTENDED_INPUTS


class _FullSurfaceFields(TypedDict):
  version: int
  manifest_sha256: str
  full_original_anchor: str
  trial_anchor: str
  context_key: str
  cp_sha256: str
  controller_revision: str
  source_epoch: str
  reset_generation: str
  selected_baseline_revision: str
  prior_full_profile_sha256: str
  full_profile_sha256: str
  prior_coordinates: tuple[tuple[str, float], ...]
  coordinates: tuple[tuple[str, float], ...]
  trial_names: tuple[str, ...]
  persistent_source_epoch: str
  operation: str
  history_full_profile_sha256: str
  restore_transaction_sha256: str


VERSION = 1
MAX_FIELDS = 8
MAX_IDS = 24


def _string(value, limit=96):
  if type(value) is not str or not value or len(value.encode()) > limit:
    raise ValueError('bounded string')
  return value


def _named_values(values):
  return [{'name': _string(name, 96), 'boolean' if type(value) is bool else 'text' if type(value) is str else 'number': value} for name, value in values]


def _named_pairs(values, limit=96):
  return [(_string(name, limit), value) for name, value in values]


@lru_cache(maxsize=2048)
def _evidence_field_name(name):
  # Receiver-only immutable key sharing. Ten minutes of100Hz actual replay
  # otherwise retains a separate identical Python string per field/frame.
  # Never cache numeric values, source identity, clocks, health or permission.
  return name


def _read_named_values(values, bound):
  if len(values) > bound:
    raise ValueError('replay scalar bounds')
  result = []
  if (type(values) is _DynamicListReader and len(values) and
      type(values[0]) is _DynamicStructReader and values[0].schema == custom.LearningControlEvidence.NamedValue.schema):
    fields = custom.LearningControlEvidence.NamedValue.schema.fields
    name_field = fields['name']
    for row in values:
      name = _evidence_field_name(_string(str(row._get_by_field(name_field)), 96))
      kind = row._which_str()
      value = row._get_by_field(fields[kind])
      value = bool(value) if kind == 'boolean' else _string(str(value), 96) if kind == 'text' else float(value)
      result.append((name, value))
    return tuple(result)
  for row in values:
    name = _evidence_field_name(_string(str(row.name), 96))
    kind = row.which()
    value = bool(row.boolean) if kind == 'boolean' else _string(str(row.text), 96) if kind == 'text' else float(row.number)
    result.append((name, value))
  return tuple(result)


def _read_original_inputs(values):
  result = _read_named_values(values, EXTENDED_INPUTS)
  if not valid_inputs(result):
    raise ValueError('versioned original replay input bounds')
  return result


def _write_effect_trace(trace):
  from openpilot.starpilot.flm.effect_trace import FullEffectTrace
  if not isinstance(trace, FullEffectTrace):
    raise ValueError('typed full effect trace')
  trace.validate()
  full = trace.envelope
  envelope = {'version':full.version, **{wire:getattr(full,field) for wire,field in _FULL_FIELDS},
    'priorCoordinates':[{'name':n,'value':v} for n,v in full.prior_coordinates],
    'coordinates':[{'name':n,'value':v} for n,v in full.coordinates], 'trialNames':full.trial_names}
  return {'version':1, 'envelope':envelope, 'controlMonoTime':trace.control_ns,
    'previousMonoTime':trace.previous_ns, 'qualified':trace.qualified,
    'before':[{'name':n,'value':v} for n,v in trace.before],
    'target':[{'name':n,'value':v} for n,v in trace.target],
    'applied':[{'name':n,'value':v} for n,v in trace.applied],
    'appliedFullProfileSha256':trace.applied_full_profile_sha256}


# Receiver histories retain up to 60,000 frames. Share exact immutable map
# content instead of allocating five full30 maps for every unchanged frame.
# Source timestamps and the per-frame transition are still validated below.
@lru_cache(maxsize=128)
def _effect_coordinate_rows(packed):
  from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
  return tuple(zip(NAMES, struct.unpack('!30d', packed), strict=True))


def _shared_effect_rows(rows):
  from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
  if tuple(name for name, _ in rows) != NAMES:
    return rows  # Preserve the original validator's rejection of foreign names.
  return _effect_coordinate_rows(struct.pack('!30d', *(value for _, value in rows)))


def _read_effect_trace(row):
  from openpilot.starpilot.flm.effect_trace import FullEffectTrace
  if row.version == 0:
    return None
  if row.version != 1 or any(len(getattr(row,n)) != 30 for n in ('before','target','applied')):
    raise ValueError('full effect trace version/bounds')
  envelope=_read_full_surface(row.envelope)
  if envelope is None:
    raise ValueError('effect trace original envelope')
  values=[_shared_effect_rows(tuple((str(v.name),float(v.value)) for v in getattr(row,n)))
          for n in ('before','target','applied')]
  return FullEffectTrace(1,envelope,int(row.controlMonoTime),int(row.previousMonoTime),bool(row.qualified),
    *cast(tuple[tuple[tuple[str, float], ...], tuple[tuple[str, float], ...], tuple[tuple[str, float], ...]], values),
    str(row.appliedFullProfileSha256)).validate()


def write_control(parent, evidence):
  if (
    not isinstance(evidence, ControlEvidence)
    or not identity(evidence.identity)
    or not 1 <= len(evidence.features) <= MAX_FIELDS
    or not all(finite(v) for v in (*evidence.features, evidence.requested, evidence.baseline_prediction))
    or not valid_inputs(evidence.replay_inputs)
    or len(evidence.live_applied_coordinates) not in (0, 30)
    or not all(finite(v) for v in evidence.live_applied_coordinates)
    or len(evidence.vehicle_model_anchor) not in (0, 3)
    or not all(finite(v) and v != 0.0 for v in evidence.vehicle_model_anchor)
  ):
    raise ValueError('control evidence')
  payload = {
    'version': VERSION,
    'identity': evidence.identity,
    'generation': evidence.generation,
    'controlMonoTime': evidence.control_ns,
    'sourceStateMonoTime': evidence.state_ns,
    'domain': _string(evidence.domain),
    'stageRevision': _string(evidence.stage_revision),
    'responseRevision': _string(evidence.response_revision),
    'requested': evidence.requested,
    'features': evidence.features,
    'baselinePrediction': evidence.baseline_prediction,
    'stageSchemaRevision': _string(evidence.stage_schema_revision),
    'stageValues': _named_pairs(evidence.stage_values, 64),
    'appliedCoordinates': evidence.applied_coordinates,
    'sourceEpoch': evidence.source_epoch,
    'sourceReceiptMonoTime': evidence.source_receipt_ns,
    'controllerMode': _string(evidence.controller_mode),
    'policy': _string(evidence.policy),
    'baselineRevision': evidence.baseline_revision,
    'baselineValues': evidence.baseline_values,
    'controllerRevision': _string(evidence.controller_revision),
    'replaySnapshot': _named_pairs(evidence.replay_snapshot),
    'replayInputs': _named_pairs(evidence.replay_inputs),
    'snapshotSequence': evidence.snapshot_sequence,
    'vehicleModelAnchor': evidence.vehicle_model_anchor,
    'liveAppliedCoordinates': evidence.live_applied_coordinates,
    'turnAssist': evidence.turn_assist,
    'appliedParameterNames': evidence.applied_parameter_names,
    'nativeRequired': evidence.native_required,
    'nativeObservedMonoTime': evidence.native_observed_ns,
    'nativeValidUntilMonoTime': evidence.native_expires_ns,
    'nativeAllowed': evidence.native_allowed,
    'nativeIdentity': evidence.native_identity,
    'appliedProfileSequence': evidence.applied_profile_sequence,
    'appliedProfileObservedMonoTime': evidence.applied_profile_observed_ns,
    'appliedProfileValidUntilMonoTime': evidence.applied_profile_expires_ns,
    'effectPrepareMonoTime': evidence.effect_prepare_ns,
    **{
      name: getattr(evidence, field)
      for name, field in (
        ('enabled', 'enabled'),
        ('manual', 'manual'),
        ('active', 'active'),
        ('valid', 'valid'),
        ('override', 'override'),
        ('fault', 'fault'),
        ('saturated', 'saturated'),
        ('limited', 'limited'),
        ('modelWobble', 'model_wobble'),
        ('controllerChatter', 'controller_chatter'),
      )
    },
  }

  # Construct typed nested rows directly. Recursive Python-dict conversion in
  # pycapnp is costly on the Controls publisher; every original field/value
  # and the validation above remain unchanged.
  wire = parent.init('learningEvidence')
  for name, value in payload.items():
    if name in ('replaySnapshot', 'replayInputs', 'stageValues'):
      nested_values = cast(list[tuple[str, object]], value)
      rows = wire.init(name, len(nested_values))
      for row, (field, item) in zip(rows, nested_values, strict=True):
        row.name = field
        kind = ('value' if name == 'stageValues' else
                'boolean' if type(item) is bool else 'text' if type(item) is str else 'number')
        setattr(row, kind, item)
    else:
      setattr(wire, name, value)

  if evidence.effect_trace is not None:
    wire.effectTrace = _write_effect_trace(evidence.effect_trace)


def _read_fields(row, schema):
  if type(row) is _DynamicStructReader and row.schema == schema:
    return SimpleNamespace(**{name: row._get_by_field(field) for name, field in schema.fields.items()})
  return row


def read_control(event):
  if not event.valid:
    raise ValueError('invalid envelope')
  row = event.starpilotLateralState.learningEvidence
  if (
    row.version != VERSION
    or len(row.features) > MAX_FIELDS
    or len(row.appliedCoordinates) > MAX_FIELDS
    or len(row.stageValues) > 40
    or len(row.baselineValues) > 32
    or len(row.appliedParameterNames) > 8
    or len(row.liveAppliedCoordinates) not in (0, 30)
    or not all(finite(float(v)) for v in row.liveAppliedCoordinates)
    or len(row.vehicleModelAnchor) not in (0, 3)
    or not all(finite(float(v)) and float(v) != 0.0 for v in row.vehicleModelAnchor)
  ):
    raise ValueError('control version or bound')
  row = _read_fields(row, custom.LearningControlEvidence.schema)
  return ControlEvidence(
    str(row.identity),
    int(row.generation),
    int(row.controlMonoTime),
    int(row.sourceStateMonoTime),
    str(row.domain),
    str(row.stageRevision),
    str(row.responseRevision),
    float(row.requested),
    tuple(float(v) for v in row.features),
    float(row.baselinePrediction),
    bool(row.enabled),
    bool(row.manual),
    bool(row.active),
    bool(row.valid),
    bool(row.override),
    bool(row.fault),
    bool(row.saturated),
    bool(row.limited),
    bool(row.modelWobble),
    bool(row.controllerChatter),
    str(row.stageSchemaRevision),
    tuple((str(v.name), float(v.value)) for v in row.stageValues),
    tuple(float(v) for v in row.appliedCoordinates),
    str(row.sourceEpoch),
    int(row.sourceReceiptMonoTime),
    str(row.controllerMode),
    str(row.policy),
    str(row.baselineRevision),
    tuple(float(v) for v in row.baselineValues),
    str(row.controllerRevision),
    _read_named_values(row.replaySnapshot, 1024),
    _read_original_inputs(row.replayInputs),
    int(row.snapshotSequence),
    bool(row.turnAssist),
    tuple(_string(str(name)) for name in row.appliedParameterNames),
    bool(row.nativeRequired),
    int(row.nativeObservedMonoTime),
    int(row.nativeValidUntilMonoTime),
    bool(row.nativeAllowed),
    str(row.nativeIdentity),
    int(row.appliedProfileSequence),
    int(row.appliedProfileObservedMonoTime),
    int(row.appliedProfileValidUntilMonoTime),
    _read_effect_trace(row.effectTrace),
    int(row.effectPrepareMonoTime),
    tuple(float(v) for v in row.vehicleModelAnchor),
    tuple(float(v) for v in row.liveAppliedCoordinates),
  )


def write_applied(parent, evidence):
  if not isinstance(evidence, AppliedEvidence) or not finite(evidence.applied):
    raise ValueError('applied evidence')
  parent.appliedEvidence = {
    'version': VERSION,
    'controlMonoTime': evidence.control_ns,
    'sourceStateMonoTime': evidence.state_ns,
    'appliedMonoTime': evidence.apply_ns,
    'domain': _string(evidence.domain),
    'applied': evidence.applied,
    **{name: getattr(evidence, name) for name in ('valid', 'active', 'override', 'fault', 'limited')},
    'nativeAllowed': evidence.native_allowed,
    'carrierFrame': evidence.carrier_frame,
    'carrierObservedMonoTime': evidence.carrier_observed_ns,
  }
  if evidence.projection is not None:
    from openpilot.starpilot.flm.projection_wire import write_projection

    parent.appliedEvidence.projection = write_projection(evidence.projection)


def _read_projection(value):
  if value.version == 0:
    return None
  from openpilot.starpilot.flm.projection_wire import read_projection

  return read_projection(value)


def read_applied(event):
  if not event.valid:
    raise ValueError('invalid envelope')
  row = event.starpilotCarState.appliedEvidence
  if row.version != VERSION:
    raise ValueError('applied version')
  row = _read_fields(row, custom.LearningAppliedEvidence.schema)
  return AppliedEvidence(
    int(row.controlMonoTime),
    int(row.sourceStateMonoTime),
    int(row.appliedMonoTime),
    str(row.domain),
    float(row.applied),
    bool(row.valid),
    bool(row.active),
    bool(row.override),
    bool(row.fault),
    bool(row.limited),
    bool(row.nativeAllowed),
    _read_projection(row.projection),
    int(row.carrierFrame),
    int(row.carrierObservedMonoTime),
  )


_FULL_FIELDS = (
  ('manifestSha256', 'manifest_sha256'), ('fullOriginalAnchor', 'full_original_anchor'),
  ('trialAnchor', 'trial_anchor'), ('contextKey', 'context_key'), ('cpSha256', 'cp_sha256'),
  ('controllerRevision', 'controller_revision'), ('sourceEpoch', 'source_epoch'),
  ('resetGeneration', 'reset_generation'), ('selectedBaselineRevision', 'selected_baseline_revision'),
  ('priorFullProfileSha256', 'prior_full_profile_sha256'), ('fullProfileSha256', 'full_profile_sha256'),
  ('persistentSourceEpoch', 'persistent_source_epoch'),
  ('operation', 'operation'), ('historyFullProfileSha256', 'history_full_profile_sha256'),
  ('restoreTransactionSha256', 'restore_transaction_sha256'),
)


def _write_full_surface(profile):
  full = profile.effect_envelope
  full.validate(profile)
  return {'version': full.version, **{wire: getattr(full, field) for wire, field in _FULL_FIELDS},
          'priorCoordinates': [{'name': name, 'value': value} for name, value in full.prior_coordinates],
          'coordinates': [{'name': name, 'value': value} for name, value in full.coordinates],
          'trialNames': full.trial_names}


def _read_full_surface(row):
  from openpilot.starpilot.flm.full_surface_profile import FullSurfaceEnvelope
  from openpilot.starpilot.flm.effect_admission_cache import validated_envelope
  try:
    if row.version not in (1, 2) or len(row.priorCoordinates) != 30 or len(row.coordinates) != 30 or not 1 <= len(row.trialNames) <= 4:
      return None
    values: dict[str, object] = {field: str(getattr(row, wire)) for wire, field in _FULL_FIELDS}
    values.update(version=int(row.version), prior_coordinates=tuple((str(point.name), float(point.value)) for point in row.priorCoordinates),
                  coordinates=tuple((str(point.name), float(point.value)) for point in row.coordinates),
                  trial_names=tuple(str(name) for name in row.trialNames))
    return validated_envelope(FullSurfaceEnvelope(**cast(_FullSurfaceFields, values)))
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    return None


def result_message(result, session, sequence, now_ns, *, replay_validation=None):
  event = messaging.new_message('autoSteeringLearning')
  value = event.autoSteeringLearning.learning
  value.version, value.session, value.sequence = VERSION, _string(session, 64), sequence
  value.observedMonoTime, value.validUntilMonoTime = now_ns, now_ns + 500_000_000
  value.status, value.reason, value.family = 'collecting', 'independent_evidence', 'runtimeProfile'
  profile = None if result is None else result.profile
  if profile is not None:
    if (
      not isinstance(profile, Profile)
      or not identity(profile.identity)
      or not 1 <= len(profile.parameters) == len(profile.coefficients) <= MAX_FIELDS
      or len(profile.train_ids) + len(profile.heldout_ids) > MAX_IDS
    ):
      raise ValueError('profile bound')
    if any(not finite(p.excitation_floor) or p.excitation_floor <= 0 for p in profile.parameters):
      raise ValueError('profile excitation floor')
    if any(not all(finite(v) for v in (p.low, p.high, p.max_step, p.slew_per_s, coefficient))
           or p.low > p.high or p.max_step <= 0 or p.slew_per_s <= 0
           or not p.low <= coefficient <= p.high or abs(coefficient) > p.max_step
           for p, coefficient in zip(profile.parameters, profile.coefficients, strict=True)):
      raise ValueError('profile parameter bound')
    if not replay_accepts(replay_validation, profile):
      raise ValueError('actual replay validation required')
    value.status = 'qualifiedFit' if result.status == 'qualified' else 'rejected'
    value.reason = _string(result.reason)
    # Profile's original observation/expiry is copied, never renewed by a4Hz
    # publication. Outer collecting metadata cannot extend its500ms lease.
    value.runtimeProfile = {
      'version': 2 if profile.effect_envelope is not None else VERSION,
      'identity': profile.identity,
      'sequence': profile.sequence,
      'observedMonoTime': profile.observed_ns,
      'validUntilMonoTime': profile.expires_ns,
      'policy': _string(profile.policy),
      'parameters': [
        {
          'name': _string(p.name),
          'stageRevision': _string(p.stage_revision),
          'low': p.low,
          'high': p.high,
          'maxStep': p.max_step,
          'slewPerS': p.slew_per_s,
          'excitationFloor': p.excitation_floor,
        }
        for p in profile.parameters
      ],
      'coefficients': profile.coefficients,
      'trainIds': [_string(v, 128) for v in profile.train_ids],
      'heldoutIds': [_string(v, 128) for v in profile.heldout_ids],
      'baselineLoss': profile.baseline_loss,
      'candidateLoss': profile.candidate_loss,
      'cohortImprovements': [{'name': _string(name), 'lowerBound': v} for name, v in profile.cohort_improvements],
      'replay': {
        'baselineRevision': _string(replay_validation.baseline_revision),
        'physicalModelRevision': _string(replay_validation.physical_model_revision),
        'noSaturationRegression': replay_validation.no_saturation_regression,
        'noSlewRegression': replay_validation.no_slew_regression,
        'worstCohortImprovement': replay_validation.worst_cohort_improvement,
        'nativeBaselineValues': replay_validation.native_baseline_values,
      },
    }

    if profile.policy in ('event_effect_coordinate_v2', 'event_effect_restore_v1'):
      if profile.effect_envelope is None:
        raise ValueError('explicit full surface envelope required')
      value.runtimeProfile.fullSurface = _write_full_surface(profile)
    elif profile.effect_envelope is not None:
      raise ValueError('full surface envelope on legacy policy')

  elif result is not None:
    value.status = 'rejected' if result.status == 'rejected' else 'collecting'
    value.reason = _string(result.reason)
  event.valid = True
  return event


def read_profile(event):
  if not event.valid:
    return None
  value = event.autoSteeringLearning.learning
  if value.version != VERSION or str(value.family) != 'runtimeProfile' or str(value.status) != 'qualifiedFit':
    return None
  row = value.runtimeProfile
  if (
    row.version not in (VERSION, 2)
    or not identity(str(row.identity))
    or not 1 <= len(row.parameters) == len(row.coefficients) <= MAX_FIELDS
    or not 4 <= len(row.trainIds) <= MAX_IDS
    or not 4 <= len(row.heldoutIds) <= MAX_IDS
    or len(row.trainIds) + len(row.heldoutIds) > MAX_IDS
    or len(row.cohortImprovements) > MAX_IDS
  ):
    return None
  parameters = tuple(
    Parameter(str(p.name), str(p.stageRevision), float(p.low), float(p.high), float(p.maxStep), float(p.slewPerS), float(p.excitationFloor))
    for p in row.parameters
  )
  if any(not finite(p.excitation_floor) or p.excitation_floor <= 0 for p in parameters):
    return None
  effect = _read_full_surface(row.fullSurface) if row.version == 2 else None
  if ((row.version == 2) != (str(row.policy) in ('event_effect_coordinate_v2', 'event_effect_restore_v1')) or
      row.version == 2 and effect is None or row.version == VERSION and row.fullSurface.version != 0):
    return None
  profile = Profile(
    str(row.identity),
    int(row.sequence),
    int(row.observedMonoTime),
    int(row.validUntilMonoTime),
    parameters,
    tuple(float(v) for v in row.coefficients),
    tuple(str(v) for v in row.trainIds),
    tuple(str(v) for v in row.heldoutIds),
    float(row.baselineLoss),
    float(row.candidateLoss),
    tuple((str(v.name), float(v.lowerBound)) for v in row.cohortImprovements),
    str(row.policy),
    effect,
  )
  try:
    if effect is not None:
      effect.validate(profile)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None
  return profile


def read_runtime_admission(event):
  profile = read_profile(event)
  if profile is None:
    return None
  row = event.autoSteeringLearning.learning.runtimeProfile.replay
  validation = ReplayValidation(
    profile,
    str(row.baselineRevision),
    str(row.physicalModelRevision),
    bool(row.noSaturationRegression),
    bool(row.noSlewRegression),
    float(row.worstCohortImprovement),
    tuple(float(value) for value in row.nativeBaselineValues) if len(row.nativeBaselineValues) <= 32 else (float('nan'),),
  )
  return (profile, validation) if replay_accepts(validation, profile) else None


def write_source_receipt(event, source):
  # Shared registry SourceState; read only by separate learner/startup.
  if (
    not identity(source.cp_sha256) or type(source.observed_ns) is not int or not 0 < source.observed_ns <= source.expires_ns <= source.observed_ns + 500_000_000
  ):
    raise ValueError('source receipt bounds')
  if source.calibration_generation and not _calibration_generation(source.calibration_generation):
    raise ValueError('calibration lifecycle generation')
  if source.enabled and (not identity(source.source_epoch) or not source.reset):
    raise ValueError('enabled source receipt identity')
  event.autoSteeringLearning.learning.sourceReceipt = {
    'version': 3 if source.calibration_generation and getattr(source, 'persistent_source_epoch', '') else 2 if source.calibration_generation else VERSION,
    'enabled': source.enabled,
    'manual': source.manual,
    'resetGeneration': source.reset,
    'sourceEpoch': source.source_epoch,
    'observedMonoTime': source.observed_ns,
    'validUntilMonoTime': source.expires_ns,
    'cpSha256': source.cp_sha256,
    'calibrationBasis': source.calibration,
    'calibrationGeneration': source.calibration_generation,
    'persistentSourceEpoch': getattr(source, 'persistent_source_epoch', ''),
  }


def _read_native_source_receipt(row, now_ns, cp_sha256, source_type):
  version = row.version
  if version not in (VERSION, 2, 3):
    return None
  cp = str(row.cpSha256)
  if cp != cp_sha256 or not identity(cp):
    return None
  observed = row.observedMonoTime
  if not 0 < observed <= now_ns:
    return None
  expires = row.validUntilMonoTime
  if not now_ns <= expires <= observed + 500_000_000:
    return None
  enabled = bool(row.enabled)
  if enabled:
    epoch = str(row.sourceEpoch)
    if not identity(epoch):
      return None
    reset = str(row.resetGeneration)
    if not reset:
      return None
  else:
    reset = str(row.resetGeneration)
  if len(reset) > 64:
    return None
  basis = row.calibrationBasis
  if len(basis) != 3:
    return None
  calibration = []
  for item in basis:
    value = float(item)
    if not finite(value):
      return None
    calibration.append(value)
  manual = bool(row.manual)
  if not enabled:
    epoch = str(row.sourceEpoch)
  generation = str(row.calibrationGeneration) if version in (2, 3) else ''
  if version in (2, 3) and not _calibration_generation(generation):
    return None
  persistent_epoch = str(row.persistentSourceEpoch) if version == 3 else ''
  if version == 3 and not identity(persistent_epoch):
    return None
  return source_type(
    enabled, manual, reset, epoch, observed, expires, cp, calibration=tuple(calibration),
    calibration_generation=generation, persistent_source_epoch=persistent_epoch,
  )


def _calibration_generation(value):
  return type(value) is str and len(value) == 32 and all(c in '0123456789abcdef' for c in value)


def read_source_receipt(event, *, now_ns, cp_sha256):
  from openpilot.starpilot.flm.runtime_adapter_registry import SourceState

  if not event.valid:
    return None
  row = event.autoSteeringLearning.learning.sourceReceipt
  if (type(row) is _DynamicStructReader and row.schema == custom.AutomaticLearningSourceReceipt.schema
      and type(now_ns) is int and type(cp_sha256) is str):
    return _read_native_source_receipt(row, now_ns, cp_sha256, SourceState)
  version = row.version
  if version in (2, 3):
    return _read_native_source_receipt(row, now_ns, cp_sha256, SourceState)
  if (
    version != VERSION
    or str(row.cpSha256) != cp_sha256
    or not identity(str(row.cpSha256))
    or not 0 < row.observedMonoTime <= now_ns <= row.validUntilMonoTime <= row.observedMonoTime + 500_000_000
    or bool(row.enabled)
    and (not identity(str(row.sourceEpoch)) or not str(row.resetGeneration))
    or len(str(row.resetGeneration)) > 64
    or len(row.calibrationBasis) != 3
    or not all(finite(float(value)) for value in row.calibrationBasis)
  ):
    return None
  return SourceState(
    bool(row.enabled),
    bool(row.manual),
    str(row.resetGeneration),
    str(row.sourceEpoch),
    int(row.observedMonoTime),
    int(row.validUntilMonoTime),
    str(row.cpSha256),
    tuple(float(value) for value in row.calibrationBasis),
  )
