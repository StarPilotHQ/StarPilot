"""Compiled incumbent IDs/layout and wire regressions for the nested startup type."""
from importlib.resources import files
from pathlib import Path
import hashlib
import json
import re

import capnp
import pytest
from openpilot.cereal import custom, CEREAL_PATH
from openpilot.starpilot.car.tesla.tests.schema_fixture import layout
from openpilot.starpilot.car.tesla.tests.test_radar_schema_evolution import populated, assert_occupied_equal

FIXTURE = Path(__file__).with_name('fixtures') / 'custom_before_preap_pedal.capnp'
REVIEW = json.loads(FIXTURE.with_name('custom_reviewed_schema_additions.json').read_text())


@pytest.fixture
def historical():
  parser = capnp.SchemaParser()
  yield parser.load(str(FIXTURE), imports=[CEREAL_PATH, str(files('opendbc') / 'car')])


def compare_node(old, new):
  assert old.schema.node.id == new.schema.node.id
  kind = old.schema.node.which()
  assert kind == new.schema.node.which()
  approved = REVIEW['types'].get(hex(old.schema.node.id))
  if kind == 'struct':
    old_layout, new_layout = layout(old.schema), layout(new.schema)
    if approved is None:
      assert old_layout == new_layout
    else:
      assert approved['kind'] == 'struct'
      old_fields = old_layout.pop('fields', [])
      assert len(old_fields) == approved['old_field_count']
      assert old_layout == approved['old_storage']
      expected = {**approved['new_storage'], 'fields': old_fields + approved['added_fields']}
      assert new_layout == expected
  elif kind == 'enum':
    old_layout, new_layout = old.schema.node.enum.to_dict(), new.schema.node.enum.to_dict()
    if approved is None:
      assert old_layout == new_layout
    else:
      assert approved['kind'] == 'enum'
      old_values = old_layout.pop('enumerants')
      assert len(old_values) == approved['old_field_count']
      assert old_layout == approved['old_storage']
      assert new_layout == {**approved['new_storage'], 'enumerants': old_values + approved['added_enumerants']}
  for child in old.schema.node.nestedNodes:
    compare_node(getattr(old, child.name), getattr(new, child.name))


def test_all_incumbent_custom_type_ids_fields_and_layouts_are_preserved(historical):
  assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == REVIEW['incumbent_fixture_sha256']
  for name in re.findall(r'^(?:struct|enum) (\w+)', FIXTURE.read_text(), re.MULTILINE):
    old = getattr(historical, name)
    approved = REVIEW['types'].get(hex(old.schema.node.id))
    current_name = approved['after_name'] if approved else name
    compare_node(old, getattr(custom, current_name))
  # Startup capsules have no parent slots; published phone GPS@3 stays occupied.
  assert set(custom.StarPilotCarControl.schema.fields) == {
    'hudControl', 'steeringLimitInfo', 'navigation', 'phoneGps'}
  assert custom.StarPilotCarControl.PreapPedalStartup.schema.node.id not in {
    getattr(historical, name).schema.node.id for name in
    re.findall(r'^struct (\w+)', FIXTURE.read_text(), re.MULTILINE)}


def test_standalone_startup_does_not_modify_parent_and_old_relay_preserves_navigation(historical):
  parent = populated(custom.StarPilotCarControl)
  original = parent.to_bytes()
  capsule = custom.StarPilotCarControl.PreapPedalStartup.new_message(
    version=1, kind=5, carParamsSha256=bytes(range(32)), safetyModel=39,
    safetyParam=7, factor=1.25, zero=-0.5, pedalBus=2, sourceMonoTime=1234)
  with custom.StarPilotCarControl.PreapPedalStartup.from_bytes(capsule.to_bytes()) as recovered:
    assert recovered.to_dict() == capsule.to_dict()
  parent.clear_write_flag()
  assert parent.to_bytes() == original
  with historical.StarPilotCarControl.from_bytes(original) as old:
    assert_occupied_equal(parent, old)
    relayed = old.as_builder().to_bytes()
  with custom.StarPilotCarControl.from_bytes(relayed) as recovered:
    assert_occupied_equal(parent, recovered)


def test_old_gps_and_pedal_status_survive_current_reader_and_old_relay(historical):
  old = historical.StarPilotCarState.new_message(pedalMaxRegen=True, pedalLongActive=True,
    sourceCarStateMonoTime=123456, gps={'sourceMonoTime': 123456, 'latitude': 41.25,
      'longitude': -87.5, 'altitude': 180., 'speed': 20., 'bearingDeg': 90., 'hasFix': True,
      'unixTimestampMillis': 1700000000000, 'vNED': [1., 2., 3.]})
  with custom.StarPilotCarState.from_bytes(old.to_bytes()) as current:
    assert {name: current.to_dict()[name] for name in old.to_dict()} == old.to_dict()
    relayed = current.as_builder().to_bytes()
  with historical.StarPilotCarState.from_bytes(relayed) as recovered:
    assert recovered.to_dict() == old.to_dict()


def test_additive_feedback_survives_incumbent_reader_relay(historical):
  original = historical.StarPilotCarState.new_message(
    pedalMaxRegen=True, pedalLongActive=True, sourceCarStateMonoTime=123456,
    gps={'latitude': 41.25, 'longitude': -87.5, 'hasFix': True})
  incumbent = original.to_dict()
  with custom.StarPilotCarState.from_bytes(original.to_bytes()) as decoded:
    current = decoded.as_builder()
  feedback = {'version': 1, 'cardSession': bytes(range(16)), 'publicationSequence': 27,
              'sourceCarStateMonoTime': 123456, 'outputKnown': True,
              'reportedTorque': 0.25, 'coBodySha256': bytes(range(32))}
  current.feedbackPublication = feedback
  expected_feedback = current.feedbackPublication.to_dict()
  with historical.StarPilotCarState.from_bytes(current.to_bytes()) as old:
    assert old.to_dict() == incumbent
    relayed = old.as_builder().to_bytes()
  with custom.StarPilotCarState.from_bytes(relayed) as recovered:
    assert {name: recovered.to_dict()[name] for name in incumbent} == incumbent
    assert recovered.feedbackPublication.to_dict() == expected_feedback


def test_additive_steering_limits_preserve_defaults_and_survive_incumbent_relay(historical):
  original = historical.StarPilotCarState.new_message(
    pedalMaxRegen=True, pedalLongActive=True, sourceCarStateMonoTime=123456,
    gps={'latitude': 41.25, 'longitude': -87.5, 'hasFix': True})
  incumbent = original.to_dict()
  with custom.StarPilotCarState.from_bytes(original.to_bytes()) as decoded:
    assert not decoded.steeringLimitInfo.valid
    assert decoded.steeringLimitInfo.monoTime == 0
    current = decoded.as_builder()
  current.feedbackPublication = {'version': 1, 'publicationSequence': 27, 'reportedTorque': 0.25}
  current.toyotaCruise = {'version': 1, 'status': 'active', 'targetKph': 72., 'sessionId': 'incumbent'}
  current.steeringLimitInfo = {
    'valid': True, 'modelLimitErrorDeg': 1.25, 'resumeLimitErrorDeg': 2.5,
    'cooperativeLimitErrorDeg': 3.75, 'cooperativeOffsetDeg': -0.5,
    'monoTime': 123456789, 'combinedLimitErrorDeg': 4.0}
  expected = current.to_dict()
  with historical.StarPilotCarState.from_bytes(current.to_bytes()) as old:
    assert old.to_dict() == incumbent
    relayed = old.as_builder().to_bytes()
  with custom.StarPilotCarState.from_bytes(relayed) as recovered:
    assert recovered.to_dict() == expected


@pytest.mark.parametrize('detections', [[], [{'confidence': .875, 'x1Pixels': 11, 'y1Pixels': 22,
                                           'x2Pixels': 111, 'y2Pixels': 222}]])
def test_additive_vision_stop_signs_preserve_defaults_and_survive_incumbent_relay(historical, detections):
  old_type = historical.StarPilotModelDataV2.VisionObservation
  new_type = custom.StarPilotModelDataV2.VisionObservation
  original = old_type.new_message(
    producerSessionId='incumbent', status='valid', speedMps=22.5, confidence=.75, supportCount=3,
    observedMonoTime=123456, validUntilMonoTime=234567, cameraFrameEofBootTime=112233,
    episode=7, modelId='incumbent-model', frameId=42, stream='road')
  incumbent = original.to_dict()
  assert set(incumbent) == set(old_type.schema.fields)
  with new_type.from_bytes(original.to_bytes()) as decoded:
    assert not decoded._has('stopSigns')
    assert decoded.stopSigns.status == 'unknown'
    assert not decoded.stopSigns.detections
    current = decoded.as_builder()
  current.stopSigns = {
    'status': 'valid', 'observedMonoTime': 123456, 'validUntilMonoTime': 234567,
    'cameraFrameEofBootTime': 112233, 'frameId': 42, 'imageWidth': 1920, 'imageHeight': 1080,
    'detections': detections}
  expected = current.to_dict()
  with old_type.from_bytes(current.to_bytes()) as old:
    assert old.to_dict() == incumbent
    relayed = old.as_builder().to_bytes()
  with new_type.from_bytes(relayed) as recovered:
    assert recovered.to_dict() == expected


@pytest.mark.parametrize(('before', 'after'), [
  ('speedMps @2 :Float32;', 'speedMps @2 :Float64;'),
  ('stopSigns @12 :StopObservation;', 'stopSigns @12 :StarPilotCarState.Gps;'),
  ('stopSigns @12 :StopObservation;', 'stopSigns @12 :StopObservation; unreviewed @13 :Bool;'),
])
def test_reviewed_vision_addition_does_not_allow_other_schema_changes(historical, tmp_path, before, after):
  source = Path(CEREAL_PATH, 'custom.capnp').read_text()
  assert source.count(before) == 1
  path = tmp_path / 'unreviewed-vision.capnp'
  path.write_text(source.replace(before, after))
  parser = capnp.SchemaParser()
  changed = parser.load(str(path), imports=[CEREAL_PATH, str(files('opendbc') / 'car')])
  with pytest.raises(AssertionError):
    compare_node(historical.StarPilotModelDataV2, changed.StarPilotModelDataV2)


@pytest.mark.parametrize(('before', 'after'), [
  ('dashboardSpeedLimit @3 :Float32;', 'dashboardSpeedLimit @3 :Float64;'),
  ('toyotaCruise @38 :ToyotaCruise;', 'toyotaCruise @38 :StarPilotCarControl.SteeringLimitInfo;'),
  ('steeringLimitInfo @39 :StarPilotCarControl.SteeringLimitInfo;', 'steeringLimitInfo @39 :StarPilotCarState.Gps;'),
  ('steeringLimitInfo @39 :StarPilotCarControl.SteeringLimitInfo;',
   'steeringLimitInfo @39 :StarPilotCarControl.SteeringLimitInfo; unreviewed @40 :Bool;'),
])
def test_reviewed_steering_addition_does_not_allow_other_schema_changes(historical, tmp_path, before, after):
  source = Path(CEREAL_PATH, 'custom.capnp').read_text()
  assert source.count(before) == 1
  path = tmp_path / 'unreviewed.capnp'
  path.write_text(source.replace(before, after))
  parser = capnp.SchemaParser()
  changed = parser.load(str(path), imports=[CEREAL_PATH, str(files('opendbc') / 'car')])
  with pytest.raises(AssertionError):
    compare_node(historical.StarPilotCarState, changed.StarPilotCarState)


@pytest.mark.parametrize(('fields', 'compatible'), [
  ('value @0 :UInt32; label @1 :Text;', True),
  ('value @0 :UInt32; label @1 :Text; extra @2 :Bool;', False),
  ('value @0 :UInt64; label @1 :Text;', False),
  ('value @0 :UInt32 = 42; label @1 :Text;', False),
  ('value @1 :UInt32; label @0 :Text;', False),
  ('value @0 :UInt32;', False),
])
def test_compiled_compatibility_check_rejects_unreviewed_changes(tmp_path, fields, compatible):
  header = '@0x9db79da1a8afbd10; struct Sample @0x9713dcf4d2386e50 {'
  old_path, new_path = tmp_path / 'old.capnp', tmp_path / 'new.capnp'
  old_path.write_text(header + 'value @0 :UInt32; label @1 :Text; }')
  new_path.write_text(header + fields + '}')
  old_parser, new_parser = capnp.SchemaParser(), capnp.SchemaParser()
  old, new = old_parser.load(str(old_path)).Sample, new_parser.load(str(new_path)).Sample
  if compatible:
    compare_node(old, new)
  else:
    with pytest.raises(AssertionError):
      compare_node(old, new)
