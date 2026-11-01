"""Typed field builder retains the original complete wire and rejection contract."""

from dataclasses import replace
import math

import pytest
from openpilot.cereal import messaging
from openpilot.starpilot.flm import learning_wire as wire
from openpilot.starpilot.flm.tests.test_fullrate_pipeline import snapshots, BASE


def reference_write_control(parent, evidence):
  if (
    not isinstance(evidence, wire.ControlEvidence)
    or not wire.identity(evidence.identity)
    or (not 1 <= len(evidence.features) <= wire.MAX_FIELDS)
    or (not all(wire.finite(v) for v in (*evidence.features, evidence.requested, evidence.baseline_prediction)))
    or (not wire.valid_inputs(evidence.replay_inputs))
    or (len(evidence.live_applied_coordinates) not in (0, 30))
    or (not all(wire.finite(v) for v in evidence.live_applied_coordinates))
    or (len(evidence.vehicle_model_anchor) not in (0, 3))
    or (not all(wire.finite(v) and v != 0.0 for v in evidence.vehicle_model_anchor))
  ):
    raise ValueError('control evidence')
  parent.learningEvidence = {
    'version': wire.VERSION,
    'identity': evidence.identity,
    'generation': evidence.generation,
    'controlMonoTime': evidence.control_ns,
    'sourceStateMonoTime': evidence.state_ns,
    'domain': wire._string(evidence.domain),
    'stageRevision': wire._string(evidence.stage_revision),
    'responseRevision': wire._string(evidence.response_revision),
    'requested': evidence.requested,
    'features': evidence.features,
    'baselinePrediction': evidence.baseline_prediction,
    'stageSchemaRevision': wire._string(evidence.stage_schema_revision),
    'stageValues': [{'name': wire._string(name, 64), 'value': v} for name, v in evidence.stage_values],
    'appliedCoordinates': evidence.applied_coordinates,
    'sourceEpoch': evidence.source_epoch,
    'sourceReceiptMonoTime': evidence.source_receipt_ns,
    'controllerMode': wire._string(evidence.controller_mode),
    'policy': wire._string(evidence.policy),
    'baselineRevision': evidence.baseline_revision,
    'baselineValues': evidence.baseline_values,
    'controllerRevision': wire._string(evidence.controller_revision),
    'replaySnapshot': wire._named_values(evidence.replay_snapshot),
    'replayInputs': wire._named_values(evidence.replay_inputs),
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
  if evidence.effect_trace is not None:
    parent.learningEvidence.effectTrace = wire._write_effect_trace(evidence.effect_trace)


def packet(writer, row):
  event = messaging.new_message('starpilotLateralState', valid=True, logMonoTime=BASE)
  writer(event.starpilotLateralState, row)
  return event


def row_with_fields():
  row = snapshots(BASE)[0]
  return replace(
    row,
    replay_snapshot=(('number', 1.25), ('signed_zero', -0.0), ('truth', True), ('text', 'original')),
    replay_inputs=(('vEgo', 20.0), ('control_frame', 32.0), ('boolean', False), ('text', 'value')),
    snapshot_sequence=17,
    vehicle_model_anchor=(1.2, 1.3, 1.4),
    live_applied_coordinates=(0.001,) * 30,
    native_required=True,
    native_observed_ns=BASE - 1_000_000,
    native_expires_ns=BASE + 1_000_000,
    native_allowed=True,
    native_identity='4' * 64,
    applied_profile_sequence=9,
    applied_profile_observed_ns=BASE - 2_000_000,
    applied_profile_expires_ns=BASE + 2_000_000,
    applied_parameter_names=('left', 'right'),
    effect_prepare_ns=BASE - 2_000_000,
  )


@pytest.mark.parametrize(
  'field',
  [
    'enabled',
    'manual',
    'active',
    'valid',
    'override',
    'fault',
    'saturated',
    'limited',
    'model_wobble',
    'controller_chatter',
    'turn_assist',
    'native_required',
    'native_allowed',
  ],
)
@pytest.mark.parametrize('value', [False, True])
def test_all_health_and_authority_flags_keep_identical_wire(field, value):
  row = replace(row_with_fields(), **{field: value})
  before, after = packet(reference_write_control, row), packet(wire.write_control, row)
  assert after.to_bytes() == before.to_bytes()
  assert wire.read_control(after.as_reader()) == wire.read_control(before.as_reader())


@pytest.mark.parametrize('count', [0, 1, 8, 32])
def test_original_nested_lists_and_union_values_keep_identical_wire(count):
  row = replace(
    row_with_fields(),
    replay_snapshot=tuple((f's{index}', -0.0 if index % 3 == 0 else True if index % 3 == 1 else 'x') for index in range(count)),
    replay_inputs=tuple((f'i{index}', float(index)) for index in range(count)),
    stage_values=tuple((f'stage{index}', float(index)) for index in range(count)),
  )
  assert packet(wire.write_control, row).to_bytes() == packet(reference_write_control, row).to_bytes()


def test_trace_keeps_identical_wire_and_roundtrip():
  from openpilot.starpilot.flm.tests.test_full_effect_trace import current_trace

  owner, profile, _, _ = current_trace()
  row = replace(row_with_fields(), effect_trace=owner.trace, baseline_revision=profile.effect_envelope.selected_baseline_revision)
  before, after = packet(reference_write_control, row), packet(wire.write_control, row)
  assert after.to_bytes() == before.to_bytes()
  assert wire.read_control(after.as_reader()) == wire.read_control(before.as_reader())


@pytest.mark.parametrize(
  ('field', 'value'),
  [
    ('identity', 'bad'),
    ('features', ()),
    ('features', (math.nan,)),
    ('features', (1.0,) * 9),
    ('requested', math.inf),
    ('baseline_prediction', math.nan),
    ('domain', ''),
    ('domain', 'x' * 97),
    ('replay_inputs', (('same', 1.0), ('same', 2.0))),
    ('replay_inputs', tuple((str(i), 0.0) for i in range(34))),
    ('replay_inputs', (('bad', math.nan),)),
    ('live_applied_coordinates', (0.0,)),
    ('live_applied_coordinates', (math.nan,) * 30),
    ('vehicle_model_anchor', (1.0,)),
    ('vehicle_model_anchor', (1.0, 0.0, 1.0)),
    ('vehicle_model_anchor', (1.0, math.nan, 1.0)),
    ('replay_snapshot', (('', 1.0),)),
    ('stage_values', (('x' * 65, 1.0),)),
  ],
)
def test_invalid_fields_remain_rejected_by_same_exception(field, value):
  row = replace(row_with_fields(), **{field: value})
  with pytest.raises(Exception) as before:
    packet(reference_write_control, row)
  with pytest.raises(type(before.value)):
    packet(wire.write_control, row)


def reference_read_named_values(values, bound):
  if len(values) > bound:
    raise ValueError('replay scalar bounds')
  result = []
  for row in values:
    name = wire._evidence_field_name(wire._string(str(row.name), 96))
    kind = row.which()
    value = bool(row.boolean) if kind == 'boolean' else wire._string(str(row.text), 96) if kind == 'text' else float(row.number)
    result.append((name, value))
  return tuple(result)


@pytest.mark.parametrize('points', [
  (), (('zero', 0.0),), (('negative_zero', -0.0),), (('true', True),),
  (('false', False),), (('text', 'value'),), (('unicode', 'λ'),),
  tuple((f'field{index}', index / 10) for index in range(32)),
])
def test_native_named_scalar_reader_keeps_exact_types_and_bits(points):
  import struct
  row = replace(row_with_fields(), replay_snapshot=points)
  event = packet(wire.write_control, row)
  reader = messaging.log_from_bytes(event.to_bytes())
  actual = wire._read_named_values(reader.starpilotLateralState.learningEvidence.replaySnapshot, 1024)
  original = reference_read_named_values(reader.starpilotLateralState.learningEvidence.replaySnapshot, 1024)
  assert actual == original
  assert tuple(type(value) for _, value in actual) == tuple(type(value) for _, value in original)
  for (_, value), (_, previous) in zip(actual, original, strict=True):
    if type(value) is float:
      assert struct.pack('!d', value) == struct.pack('!d', previous)


@pytest.mark.parametrize('points,bound', [
  ([{'name': '', 'number': 1.}], 1024),
  ([{'name': 'x' * 97, 'number': 1.}], 1024),
  ([{'name': 'λ' * 49, 'number': 1.}], 1024),
  ([{'name': 'text', 'text': ''}], 1024),
  ([{'name': 'text', 'text': 'x' * 97}], 1024),
  ([{'name': 'number', 'number': 1.}], 0),
])
def test_native_named_scalar_reader_preserves_refusal(points, bound):
  event = packet(wire.write_control, row_with_fields())
  event.starpilotLateralState.learningEvidence.replaySnapshot = points
  reader = event.as_reader().starpilotLateralState.learningEvidence.replaySnapshot
  with pytest.raises(Exception) as original:
    reference_read_named_values(reader, bound)
  with pytest.raises(type(original.value)):
    wire._read_named_values(reader, bound)


def test_native_named_scalar_reader_foreign_schema_keeps_original_refusal():
  event = packet(wire.write_control, row_with_fields())
  event.starpilotLateralState.learningEvidence.stageValues = [{'name': 'x', 'value': 1.}]
  rows = event.as_reader().starpilotLateralState.learningEvidence.stageValues
  with pytest.raises(Exception) as original:
    reference_read_named_values(rows, 1024)
  with pytest.raises(type(original.value)):
    wire._read_named_values(rows, 1024)


def test_native_named_scalar_reader_matches_complete_control_roundtrip(monkeypatch):
  event = packet(wire.write_control, row_with_fields())
  reader = event.as_reader()
  actual = wire.read_control(reader)
  monkeypatch.setattr(wire, '_read_named_values', reference_read_named_values)
  assert actual == wire.read_control(reader)


def test_native_named_scalar_reader_unknown_discriminant_keeps_refusal():
  import struct
  from openpilot.cereal import custom

  event = custom.LearningControlEvidence.new_message(replaySnapshot=[{'name': 'x', 'number': 1.}])
  data = bytearray(event.to_bytes())
  assert struct.unpack_from('<I', data)[0] == 0

  def word(offset):
    return struct.unpack_from('<Q', data, offset)[0]

  def displacement(value):
    offset = (value >> 2) & 0x3fffffff
    return offset - (1 << 30) if offset & (1 << 29) else offset

  root = word(8)
  fields = custom.LearningControlEvidence.schema.fields
  pointer = 16 + displacement(root) * 8 + ((root >> 32) & 0xffff) * 8 + fields['replaySnapshot'].proto.slot.offset * 8
  values = word(pointer)
  assert values & 3 == 1 and (values >> 32) & 7 == 7
  first = pointer + 16 + displacement(values) * 8
  discriminant = custom.LearningControlEvidence.NamedValue.schema.node.struct.discriminantOffset
  struct.pack_into('<H', data, first + 2 * discriminant, 0xffff)
  with custom.LearningControlEvidence.from_bytes(data) as reader:
    with pytest.raises(Exception) as original:
      reference_read_named_values(reader.replaySnapshot, 1024)
    with pytest.raises(type(original.value)):
      wire._read_named_values(reader.replaySnapshot, 1024)


def test_native_evidence_fields_foreign_and_custom_operands_keep_fallback():
  from types import SimpleNamespace
  from openpilot.cereal import custom

  value = SimpleNamespace(version=1)
  assert wire._read_fields(value, custom.LearningControlEvidence.schema) is value
  foreign = custom.LearningAppliedEvidence.new_message(version=1).as_reader()
  assert wire._read_fields(foreign, custom.LearningControlEvidence.schema) is foreign


def test_native_evidence_fields_keep_all_actual_scalar_and_list_values():
  from openpilot.cereal import custom

  event = packet(wire.write_control, row_with_fields())
  reader = event.as_reader().starpilotLateralState.learningEvidence
  values = wire._read_fields(reader, custom.LearningControlEvidence.schema)
  assert vars(values).keys() == custom.LearningControlEvidence.schema.fields.keys()
  for name in custom.LearningControlEvidence.schema.fields:
    actual, original = getattr(values, name), getattr(reader, name)
    if hasattr(original, 'to_dict'):
      assert actual.to_dict() == original.to_dict()
    elif name in ('features', 'stageValues', 'appliedCoordinates', 'baselineValues', 'replaySnapshot',
                  'replayInputs', 'appliedParameterNames', 'vehicleModelAnchor', 'liveAppliedCoordinates'):
      assert str(actual) == str(original)
    else:
      assert actual == original
