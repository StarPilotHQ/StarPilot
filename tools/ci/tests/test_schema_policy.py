"""Compatibility regressions the reserved-event policy must catch in CI."""

import copy
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.ci.schema_policy import read_inputs, validate


class SchemaPolicyTest(unittest.TestCase):
  def setUp(self):
    self.schemas, self.policy, self.sync = read_inputs()

  def test_current_fork_uses_only_reserved_schema_slots(self):
    self.assertEqual(validate(self.schemas, self.policy, self.sync), [])

  def test_adding_a_car_state_field_is_rejected(self):
    name = 'opendbc_repo/opendbc/car/car.capnp'
    self.schemas[name] = self.schemas[name].replace(b'struct CarState {', b'struct CarState {\n  forkValue @62 :Float32;')
    self.assertTrue(any('car.capnp' in error for error in validate(self.schemas, self.policy, self.sync)))

  def test_other_opendbc_schemas_are_pinned(self):
    for name in ('opendbc_repo/opendbc/car/rlog.capnp', 'opendbc_repo/opendbc/car/include/c++.capnp'):
      with self.subTest(name=name):
        schemas = self.schemas.copy()
        schemas[name] += b'\n# unexpected upstream schema edit\n'
        self.assertTrue(any(name in error for error in validate(schemas, self.policy, self.sync)))

  def test_new_schema_under_either_owned_root_requires_policy(self):
    for name in ('openpilot/cereal/new.capnp', 'opendbc_repo/opendbc/car/new.capnp'):
      with self.subTest(name=name):
        schemas = self.schemas.copy()
        schemas[name] = b'@0xdeadbeefdeadbeef;\n'
        self.assertTrue(any(name in error for error in validate(schemas, self.policy, self.sync)))

  def test_input_discovery_includes_new_schema(self):
    with TemporaryDirectory() as directory:
      root = Path(directory)
      for name, data in self.schemas.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
      (root / 'upstream-sync.json').write_text('{"dependencies": []}')
      new_schema = root / 'opendbc_repo/opendbc/car/new.capnp'
      new_schema.write_bytes(b'@0xdeadbeefdeadbeef;\n')
      discovered, _, _ = read_inputs(root)
      self.assertIn('opendbc_repo/opendbc/car/new.capnp', discovered)

  def test_reserved_custom_extension_and_alias_are_allowed(self):
    # This source-policy test permits extension/aliasing; the separate compiled
    # contract protects the existing message's API and field layout.
    original = b'struct AolAxisState @0xfc6241ed8877b611 {'
    self.assertIn(original, self.schemas['openpilot/cereal/custom.capnp'])
    self.assertIn(b'aolAxisState @142 :Custom.AolAxisState;', self.schemas['openpilot/cereal/log.capnp'])
    self.schemas['openpilot/cereal/custom.capnp'] = self.schemas['openpilot/cereal/custom.capnp'].replace(
      original, b'struct FixtureState @0xfc6241ed8877b611 {')
    # AolAxisState already occupies @0..17. Append a genuinely fresh ordinal;
    # the parser must continue rejecting the old duplicate-@12 fixture.
    occupied_tail = b'intentIntegrityEpoch @17 :UInt64;'
    self.assertIn(occupied_tail, self.schemas['openpilot/cereal/custom.capnp'])
    self.schemas['openpilot/cereal/custom.capnp'] = self.schemas['openpilot/cereal/custom.capnp'].replace(
      occupied_tail, occupied_tail + b'\n  note @18 :Text = "{fixture}";', 1)
    self.schemas['openpilot/cereal/log.capnp'] = self.schemas['openpilot/cereal/log.capnp'].replace(
      b'aolAxisState @142 :Custom.AolAxisState;', b'fixtureState @142 :Custom.FixtureState;')
    self.assertEqual(validate(self.schemas, self.policy, self.sync), [])

  def test_restored_map_event_cannot_be_retargeted(self):
    self.assertFalse({17, 18, 19}.intersection(self.policy['historical_empty_slots']))
    schemas = self.schemas.copy()
    schemas['openpilot/cereal/log.capnp'] = schemas['openpilot/cereal/log.capnp'].replace(
      b'mapdExtendedOut @143 :Custom.MapdExtendedOut;', b'mapdExtendedOut @143 :Custom.MapdIn;')
    self.assertTrue(any('event @143' in error for error in validate(schemas, self.policy, self.sync)))

  def test_aol_raw_slots_remain_data_with_original_ordinals(self):
    for old, new in ((b'aolSafetyWire @124 :Data;', b'aolSafetyWire @124 :Custom.AolAxisState;'),
                     (b'aolIntentWire @125 :Data;', b'aolIntentWire @126 :Data;')):
      with self.subTest(old=old):
        schemas = self.schemas.copy()
        self.assertIn(old, schemas['openpilot/cereal/log.capnp'])
        schemas['openpilot/cereal/log.capnp'] = schemas['openpilot/cereal/log.capnp'].replace(old, new)
        self.assertTrue(validate(schemas, self.policy, self.sync))

  def test_tracking_carrier_retains_reserved_identity_and_field_prefix(self):
    path = 'openpilot/cereal/custom.capnp'
    self.assertNotIn(5, self.policy['historical_empty_slots'])
    self.assertIn(b'struct StarPilotSteeringTracking @0xa5cd762cd951a455 {', self.schemas[path])
    for before, after in (
      (b'StarPilotSteeringTracking @0xa5cd762cd951a455', b'OtherTracking @0xa5cd762cd951a455'),
      (b'sourceReceipt @1 :AutomaticLearningSourceReceipt;', b'sourceReceipt @1 :Text;'),
      (b'trial @2 :LiveTrackingTrial;', b'trial @2 :Text;'),
      (b'restore @3 :LiveTrackingRestore;', b'restore @4 :LiveTrackingRestore;'),
    ):
      with self.subTest(after=after):
        schemas = self.schemas.copy()
        self.assertIn(before, schemas[path])
        schemas[path] = schemas[path].replace(before, after, 1)
        self.assertTrue(validate(schemas, self.policy, self.sync))
    log_path = 'openpilot/cereal/log.capnp'
    before = b'starpilotSteeringTracking @112 :Custom.StarPilotSteeringTracking;'
    self.assertIn(before, self.schemas[log_path])
    schemas = self.schemas.copy()
    schemas[log_path] = schemas[log_path].replace(before, b'starpilotSteeringTracking @113 :Custom.StarPilotSteeringTracking;')
    self.assertTrue(validate(schemas, self.policy, self.sync))

  def test_historical_slot_rename_and_field_are_rejected(self):
    path = 'openpilot/cereal/custom.capnp'
    reserved_by_name = {slot['struct']: slot for slot in self.policy['reserved']}
    for index in self.policy['historical_empty_slots']:
      name = f'CustomReserved{index}'
      type_id = reserved_by_name[name]['type_id']
      original = f'struct {name} @{type_id} {{'.encode()
      self.assertIn(original, self.schemas[path])
      for replacement in (f'struct DifferentEvent @{type_id} {{'.encode(), original + b'\n  newValue @0 :UInt32;'):
        with self.subTest(index=index, replacement=replacement):
          schemas = self.schemas.copy()
          schemas[path] = schemas[path].replace(original, replacement)
          self.assertTrue(any(f'historical slot {index}' in error for error in validate(schemas, self.policy, self.sync)))

    # Occupied historical carriers still retain their reviewed names and prefixes.
    for restored in self.policy['historical_prefixes'].values():
      original = f"struct {restored['name']} @{restored['type_id']} {{".encode()
      with self.subTest(occupied=restored['name']):
        schemas = self.schemas.copy()
        self.assertIn(original, schemas[path])
        schemas[path] = schemas[path].replace(original, original.replace(restored['name'].encode(), b'DifferentHistoricalCarrier'), 1)
        self.assertTrue(any('historical occupied prefix' in error for error in validate(schemas, self.policy, self.sync)))

  def test_upstream_plan_and_event_enums_cannot_be_extended_or_remapped(self):
    path = 'openpilot/cereal/log.capnp'
    self.assertIn(b'accelBoost @40 :Float32;', self.schemas[path])
    self.assertNotIn(b'forceStopHolding', self.schemas[path])
    self.assertNotIn(b'teslaCCEngaged', self.schemas[path])
    mutations = (
      (b'accelBoost @40 :Float32;', b'accelBoost @41 :Float32;'),
      (b'accelBoost @40 :Float32;', b'accelBoost @40 :Float32;\n  forceStopHolding @41 :Bool;'),
      (b'enum EventName @0x91f1992a1f77fb03 {', b'enum EventName @0x91f1992a1f77fb03 {\n    teslaCCEngaged @105;'),
    )
    for original, replacement in mutations:
      with self.subTest(replacement=replacement):
        schemas = self.schemas.copy()
        self.assertIn(original, schemas[path])
        schemas[path] = schemas[path].replace(original, replacement)
        self.assertTrue(validate(schemas, self.policy, self.sync))
        policy = copy.deepcopy(self.policy)
        policy['reviewed_log_additions'] = [{'scope': 'struct LongitudinalPlan', 'fields': ['forceStopHolding @41 :Bool;']}]
        self.assertTrue(validate(schemas, policy, self.sync))

  def test_historical_slot_comments_do_not_count_as_fields(self):
    path = 'openpilot/cereal/custom.capnp'
    schemas = self.schemas.copy()
    original = b'struct StarPilotSteeringTracking @0xa5cd762cd951a455 {'
    self.assertIn(original, schemas[path])
    schemas[path] = schemas[path].replace(
      original, original + b'\n  # Past wire use is reserved.\n')
    self.assertEqual(validate(schemas, self.policy, self.sync), [])

  def test_automatic_learning_keeps_reserved_identity_and_allows_append(self):
    self.assertNotIn(1, self.policy['historical_empty_slots'])
    slot = self.policy['reserved'][1]
    self.assertEqual(slot, {'struct': 'CustomReserved1', 'type_id': '0xaedffd8f31e7b55d',
                            'field': 'customReserved1', 'ordinal': 108})
    prefix = self.policy['historical_prefixes']['1']
    self.assertEqual((prefix['name'], prefix['type_id']), ('StarPilotCarParams', slot['type_id']))
    self.assertIn(b'struct StarPilotCarParams @0xaedffd8f31e7b55d {', self.schemas['openpilot/cereal/custom.capnp'])
    self.assertIn(b'autoSteeringLearning @108 :Custom.StarPilotCarParams;', self.schemas['openpilot/cereal/log.capnp'])
    self.assertIn(b'learning @9 :AutoSteeringLearning;', self.schemas['openpilot/cereal/custom.capnp'])
    self.assertIn(b'pcmCruiseSpeed @7 :Bool = true;', self.schemas['openpilot/cereal/custom.capnp'])
    schemas = self.schemas.copy()
    original = b'      controllerRevision @11 :Text;\n    }\n  }\n}'
    self.assertIn(original, schemas['openpilot/cereal/custom.capnp'])
    schemas['openpilot/cereal/custom.capnp'] = schemas['openpilot/cereal/custom.capnp'].replace(
      original, b'      controllerRevision @11 :Text;\n    }\n  }\n  futureStatus @10 :UInt32;\n}')
    self.assertEqual(validate(schemas, self.policy, self.sync), [])

  def test_automatic_learning_payload_and_event_cannot_be_reinterpreted(self):
    path = 'openpilot/cereal/custom.capnp'
    for before, after in (
      (b'struct StarPilotCarParams @0xaedffd8f31e7b55d {', b'struct UnrelatedFit @0xaedffd8f31e7b55d {'),
      (b'0xaedffd8f31e7b55d', b'0xaedffd8f31e7b55e'),
      (b'alternativeExperience @0 :Int16;', b'alternativeExperience @0 :UInt16;'),
      (b'canUsePedal @1 :Bool;', b'canUsePedal @2 :Bool;'),
      (b'flags @3 :UInt32;', b'flags @3 :Bool;'),
      (b'pcmCruiseSpeed @7 :Bool = true;', b'pcmCruiseSpeed @7 :Bool = false;'),
      (b'redneckCruiseAvailable @8 :Bool;', b''),
      (b'safetyParam @0 :UInt16;', b'safetyParam @0 :UInt32;'),
      (b'learning @9 :AutoSteeringLearning;', b'learning @10 :AutoSteeringLearning;'),
      (b'version @0 :UInt16;', b'version @0 :UInt32;'),
      (b'latAccelFactor @11 :Float64;', b'latAccelFactor @11 :Float32;'),
      (b'latAccelFactor @11 :Float64;', b'unrelatedFactor @11 :Float64;'),
      (b'heldoutCandidateLoss @18 :Float64;', b'heldoutCandidateLoss @19 :Float64;'),
      (b'acceptancePolicyId @12 :Text;', b''),
      (b'qualifiedFit @4;', b'qualifiedFit @5;'),
      (b'linearFactor @1;', b'linearFactor @2;'),
      (b'vehicle @0 :Text;', b'vehicle @0 :UInt64;'),
      (b'controllerRevision @11 :Text;', b'controllerRevision @11 :Text = "changed";'),
    ):
      with self.subTest(after=after):
        schemas = self.schemas.copy()
        head, marker, tail = schemas[path].partition(b'struct StarPilotCarParams @0xaedffd8f31e7b55d {')
        body, following, remainder = tail.partition(b'\nstruct StarPilotCarState')
        self.assertTrue(marker and following)
        occupied = marker + body
        self.assertIn(before, occupied)
        schemas[path] = head + occupied.replace(before, after) + following + remainder
        self.assertTrue(validate(schemas, self.policy, self.sync))
    path = 'openpilot/cereal/log.capnp'
    original = b'autoSteeringLearning @108 :Custom.StarPilotCarParams;'
    for replacement in (b'autoSteeringLearning @109 :Custom.StarPilotCarParams;',
                        b'autoSteeringLearning @108 :Custom.CustomReserved5;'):
      with self.subTest(replacement=replacement):
        schemas = self.schemas.copy()
        self.assertIn(original, schemas[path])
        schemas[path] = schemas[path].replace(original, replacement)
        self.assertTrue(any('event @108' in error for error in validate(schemas, self.policy, self.sync)))

  def test_restored_lateral_field_keeps_original_wire_meaning(self):
    self.assertNotIn(10, self.policy['historical_empty_slots'])
    path = 'openpilot/cereal/custom.capnp'
    original = b'desiredCurvature @0 :Float32;'
    self.assertIn(original, self.schemas[path])
    for changed in (b'desiredCurvature @0 :UInt32;', b'desiredCurvature @2 :Float32;'):
      with self.subTest(changed=changed):
        schemas = self.schemas.copy()
        schemas[path] = schemas[path].replace(original, changed)
        self.assertTrue(any('historical slot 10' in error for error in validate(schemas, self.policy, self.sync)))

  def test_custom_id_reuse_event_retargeting_and_core_edits_are_rejected(self):
    cases = (
      ('openpilot/cereal/custom.capnp', b'0xa4f1eb3323f5f582', b'0xc86a3d38d13eb3ef'),
      ('openpilot/cereal/log.capnp', b'@145 :Custom.MapdOut', b'@145 :Custom.MapdIn'),
      ('openpilot/cereal/log.capnp', b'@145 :Custom.MapdOut', b'@146 :Custom.MapdOut'),
      ('openpilot/cereal/log.capnp', b'struct Event {', b'struct Event {\n  forkValue @200 :UInt32;'),
    )
    for path, before, after in cases:
      with self.subTest(after=after):
        source = copy.copy(self.schemas)
        self.assertIn(before, source[path])
        source[path] = source[path].replace(before, after)
        self.assertTrue(validate(source, self.policy, self.sync))

  def test_new_upstream_pin_requires_schema_contract_review(self):
    self.sync['upstream']['commit'] = '0' * 40
    self.assertTrue(validate(self.schemas, self.policy, self.sync))

  def test_reviewed_native_enum_is_only_permitted_core_extension(self):
    path = 'opendbc_repo/opendbc/car/car.capnp'
    self.assertEqual(self.policy['reviewed_enum_additions'], {path: {'enum': 'SafetyModel', 'field': 'teslaPreap @39;'}})
    for before, after in ((b'teslaPreap @39;', b'teslaPreap @40;'),
                          (b'teslaPreap @39;', b'teslaPreap @39;\n    extraFork @40;'),
                          (b'teslaPreap @39;', b'teslaPreap @39;\n    extraBool @62 :Bool;')):
      changed = copy.copy(self.schemas)
      self.assertIn(before, changed[path])
      changed[path] = changed[path].replace(before, after)
      self.assertTrue(validate(changed, self.policy, self.sync))

  def test_navigation_keeps_full_historical_control_prefix(self):
    path = 'openpilot/cereal/custom.capnp'
    for before, after in ((b'hudControl @0 :HUDControl;', b'hudControl @0 :Text;'),
                          (b'cooperativeOffsetDeg @4 :Float32;', b'cooperativeOffsetDeg @4 :Bool;'),
                          (b'uwu @22;', b'uwu @23;')):
      changed = copy.copy(self.schemas)
      self.assertIn(before, changed[path])
      changed[path] = changed[path].replace(before, after)
      self.assertTrue(validate(changed, self.policy, self.sync))

  def test_longitudinal_companion_keeps_wire_prefix_and_allows_append(self):
    path = 'openpilot/cereal/custom.capnp'
    original = b'forceStopHolding @4 :Bool;'
    self.assertIn(original, self.schemas[path])
    appended = self.schemas.copy()
    appended[path] = appended[path].replace(original, original + b'\n  futureStatus @7 :UInt32;')
    self.assertEqual(validate(appended, self.policy, self.sync), [])
    for replacement in (b'', b'forceStopHolding @5 :Bool;', b'forceStopHolding @4 :UInt32;',
                        b'changedHold @4 :Bool;'):
      with self.subTest(replacement=replacement):
        changed = self.schemas.copy()
        changed[path] = changed[path].replace(original, replacement)
        self.assertTrue(any('occupied prefix changed' in error for error in validate(changed, self.policy, self.sync)))

  def test_replay_slc_prefix_preserves_fields_and_allows_append(self):
    self.assertNotIn(6, self.policy['historical_empty_slots'])
    path = 'openpilot/cereal/custom.capnp'
    original = b'slcSpeedLimitSource @26 :Text;'
    self.assertIn(original, self.schemas[path])
    appended = self.schemas.copy()
    appended[path] = appended[path].replace(original, original + b'\n  futureStatus @27 :UInt32;')
    self.assertEqual(validate(appended, self.policy, self.sync), [])
    for before, after in ((b'accelerationJerk @0 :Float32;', b'accelerationJerk @0 :UInt32;'),
                          (b'starpilotEvents @10 :AnyPointer;', b'starpilotEvents @10 :Text;'),
                          (b'slcSpeedLimit @24 :Float32;', b'slcSpeedLimit @25 :Float32;'),
                          (original, b''),
                          (b'struct CustomReserved6 @0xf98d843bfd7004a3 {',
                           b'struct DifferentEvent @0xf98d843bfd7004a3 {')):
      with self.subTest(after=after):
        changed = self.schemas.copy()
        self.assertIn(before, changed[path])
        changed[path] = changed[path].replace(before, after)
        self.assertTrue(any('occupied prefix changed' in error for error in validate(changed, self.policy, self.sync)))

  def test_replay_slc_event_keeps_original_type_id_and_ordinal(self):
    path = 'openpilot/cereal/log.capnp'
    original = b'customReserved6 @113 :Custom.CustomReserved6;'
    self.assertIn(original, self.schemas[path])
    for after in (b'customReserved6 @113 :Custom.CustomReserved5;',
                  b'customReserved6 @114 :Custom.CustomReserved6;'):
      with self.subTest(after=after):
        changed = self.schemas.copy()
        changed[path] = changed[path].replace(original, after)
        self.assertTrue(validate(changed, self.policy, self.sync))

  def test_recursive_historical_appends_preserve_existing_scopes(self):
    path = 'openpilot/cereal/custom.capnp'
    mutations = (
      (b'controllerRevision @11 :Text;', b'controllerRevision @11 :Text;\n      futureIdentity @12 :Text;'),
      (b'qualifiedFit @4;', b'qualifiedFit @4; futureStatus @5;'),
      (b'runtimeProfile @2;', b'runtimeProfile @2; futureFamily @3;'),
    )
    for before, after in mutations:
      with self.subTest(after=after):
        changed = self.schemas.copy()
        changed[path] = changed[path].replace(before, after, 1)
        self.assertEqual(validate(changed, self.policy, self.sync), [])

  def test_reviewed_payload_children_have_exact_existing_ids_and_bodies(self):
    expected = {
      'LearningControlEvidence': '0xf2447170bb68f358',
      'LearningAppliedEvidence': '0xbab0f51a74ffee6e',
      'AutomaticRuntimeProfile': '0xef38d302825641e3',
      'AutomaticLearningSourceReceipt': '0x9fe2d23e75805149',
      'LearningApplication': '0x82b6c8ea6a6efabd',
      'FordLearningEvidence': '0xd1dfbadb6a811fee',
      'FordLearningEvent': '0xb276dede9e6b2cc4',
      'CentreApplicationWitness': '0xab0b33cac682523e',
      'LearningTorqueProjection': '0xf078f562cbce49ad',
      'LiveTrackingBinding': '0x9f853f7eb4110c72',
      'LiveTrackingTrial': '0xe6f67224bc0ee1a3',
      'LiveTrackingApplication': '0xdb85b982067f1bc1',
      'LiveTrackingRestore': '0xef6257bed9318012',
      'LiveTrackingResponseCertificate': '0xbbb8f17940692312',
      'LiveTrackingResponseWitness': '0xdaa1a9f0d8968e31',
    }
    self.assertEqual({name: child['type_id'] for name, child in self.policy['reviewed_payload_children'].items()}, expected)
    path = 'openpilot/cereal/custom.capnp'
    for name, type_id in expected.items():
      declaration = f'struct {name} @{type_id} {{'.encode()
      original = self.policy['reviewed_payload_children'][name]['body'].encode()
      for body in (original.replace(b'@0', b'@99', 1), original + b'\n  futureChild @99 :Text;',
                   re.sub(rb'(:\s*)[A-Za-z_]\w*', rb'\1Void', original, count=1)):
        with self.subTest(name=name, body=body[:60]):
          changed = self.schemas.copy()
          changed[path] = changed[path].replace(declaration + original, declaration + body, 1)
          self.assertTrue(any('reviewed payload child' in error for error in validate(changed, self.policy, self.sync)))
      for replacement in (f'struct {name} {{'.encode(), f'struct {name} @0xf000000000000001 {{'.encode(),
                          f'struct Other{name} @{type_id} {{'.encode()):
        with self.subTest(name=name, replacement=replacement):
          changed = self.schemas.copy()
          changed[path] = changed[path].replace(declaration, replacement, 1)
          self.assertTrue(validate(changed, self.policy, self.sync))

  def test_helper_nested_union_defaults_and_field_names_are_immutable(self):
    path = 'openpilot/cereal/custom.capnp'
    for before, after in (
      (b'union { number @1 :Float64; text @2 :Text; boolean @3 :Bool; }',
       b'number @1 :Float64; text @2 :Text; boolean @3 :Bool;'),
      (b'excitationFloor @6 :Float64 = 0.01;', b'excitationFloor @6 :Float64 = 0.02;'),
      (b'struct FullEffectTrace {', b'struct FullEffectTrace @0xc9c9b325ca50aaf7 {'),
      (b'struct NamedValue {', b'struct OtherNamedValue {'),
      (b'effectPrepareMonoTime @45 :UInt64;', b''),
      (b'effectPrepareMonoTime @45 :UInt64;', b'unrelatedTime @45 :UInt64;'),
      (b'vehicleModelAnchor @46 :List(Float64);', b'vehicleModelAnchor @46 :List(Float32);'),
      (b'liveAppliedCoordinates @47 :List(Float64);', b''),
    ):
      with self.subTest(after=after):
        self.assertIn(before, self.schemas[path])
        changed = self.schemas.copy()
        changed[path] = changed[path].replace(before, after, 1)
        self.assertTrue(any('reviewed payload child' in error for error in validate(changed, self.policy, self.sync)))

  def test_helpers_cannot_gain_new_carriers_or_change_pointer_shape(self):
    path = 'openpilot/cereal/custom.capnp'
    cases = (
      (b'active @0 :Bool;\n  frictionThreshold', b'foreignChild @99 :LearningControlEvidence;\n  active @0 :Bool;\n  frictionThreshold'),
      (b'learningEvidence @9 :LearningControlEvidence;', b'learningEvidence @9 :List(LearningControlEvidence);'),
      (b'learningApplication @36 :LearningApplication;', b'learningApplication @36 :LearningControlEvidence;'),
      (b'learningEvidence @9 :LearningControlEvidence;', b''),
    )
    for before, after in cases:
      with self.subTest(after=after):
        self.assertIn(before, self.schemas[path])
        changed = self.schemas.copy()
        changed[path] = changed[path].replace(before, after, 1)
        self.assertTrue(any('carrier references changed' in error for error in validate(changed, self.policy, self.sync)))

  def test_unknown_helper_alias_and_helper_event_retargeting_are_rejected(self):
    path = 'openpilot/cereal/custom.capnp'
    for addition in (b'\nstruct UnknownHelper @0xf000000000000001 {}\n',
                     b'\nstruct UnknownImplicitHelper {}\n',
                     b'\nusing ExtraAlias = LearningControlEvidence;\n'):
      changed = self.schemas.copy()
      changed[path] += addition
      self.assertTrue(validate(changed, self.policy, self.sync))
    changed = self.schemas.copy()
    changed['openpilot/cereal/log.capnp'] = changed['openpilot/cereal/log.capnp'].replace(
      b'autoSteeringLearning @108 :Custom.StarPilotCarParams;',
      b'autoSteeringLearning @108 :Custom.AutomaticRuntimeProfile;')
    self.assertTrue(any('event @108' in error for error in validate(changed, self.policy, self.sync)))

  def test_recursive_historical_parser_rejects_shadowing_and_unknown_syntax(self):
    path = 'openpilot/cereal/custom.capnp'
    for after in (b'controllerRevision @11 :Text;\n      shadow @11 :UInt64;',
                  b'controllerRevision @11 :Text;\n      using Hidden = Text;',
                  b'controllerRevision @11 :Text;\n      payload @12 :union { x @13 :Text; }'):
      changed = self.schemas.copy()
      changed[path] = changed[path].replace(b'controllerRevision @11 :Text;', after, 1)
      self.assertTrue(validate(changed, self.policy, self.sync))

  def test_recursive_source_contract_preserves_quoted_default_content(self):
    from tools.ci.schema_policy import preserves_scope, source_scope

    original = 'label @0 :Text = "{# original value }"; enum Mode { first @0; }'
    historical = source_scope(original)
    appended = source_scope(original.replace('first @0;', 'first @0; second @1;'))
    self.assertTrue(preserves_scope(appended, historical))
    for replacement in ('{# originalvalue }', '{# changed value }', '{# original value}'):
      changed = source_scope(original.replace('{# original value }', replacement))
      self.assertFalse(preserves_scope(changed, historical))


  def test_helpers_in_unreferenced_nested_owner_are_not_reachable(self):
    path = 'openpilot/cereal/custom.capnp'
    original = b'learning @9 :AutoSteeringLearning;'
    self.assertIn(original, self.schemas[path])
    # Keep the existing nested owner and its exact three helper fields intact.
    # Its enclosing reserved root has no field pointing to that scope anymore;
    # merely visiting its lexical declaration must not grant helper ownership.
    for replacement in (b'', b'learning @9 :Text;'):
      with self.subTest(replacement=replacement):
        changed = self.schemas.copy()
        changed[path] = changed[path].replace(original, replacement, 1)
        errors = validate(changed, self.policy, self.sync)
        self.assertTrue(any('must remain reachable from reserved carriers' in error for error in errors))
        self.assertTrue(any('historical occupied prefix changed' in error for error in errors))

  def test_literal_learning_field_keeps_name_ordinal_and_nested_type(self):
    path = 'openpilot/cereal/custom.capnp'
    original = b'learning @9 :AutoSteeringLearning;'
    self.assertIn(original, self.schemas[path])
    for replacement in (b'otherLearning @9 :AutoSteeringLearning;',
                        b'learning @10 :AutoSteeringLearning;',
                        b'learning @9 :List(AutoSteeringLearning);',
                        b'learning @9 :Text;', b''):
      with self.subTest(replacement=replacement):
        changed = self.schemas.copy()
        changed[path] = changed[path].replace(original, replacement, 1)
        self.assertTrue(any('historical occupied prefix changed' in error
                            for error in validate(changed, self.policy, self.sync)))

  def test_duplicate_historical_ordinal_reports_occupied_prefix_violation(self):
    path = 'openpilot/cereal/custom.capnp'
    original = b'slcSpeedLimit @24 :Float32;'
    self.assertIn(original, self.schemas[path])
    self.assertIn(b'slcSpeedLimitOffset @25 :Float32;', self.schemas[path])
    changed = self.schemas.copy()
    changed[path] = changed[path].replace(original, b'slcSpeedLimit @25 :Float32;', 1)
    errors = validate(changed, self.policy, self.sync)
    self.assertTrue(any('historical occupied prefix changed: Duplicate member name or ordinal' in error for error in errors))

  def test_duplicate_existing_aol_ordinal_remains_rejected(self):
    path = 'openpilot/cereal/custom.capnp'
    original = b'struct AolAxisState @0xfc6241ed8877b611 {'
    self.assertIn(b'optionalSetRelease @12 :Bool;', self.schemas[path])
    changed = self.schemas.copy()
    changed[path] = changed[path].replace(original, original + b'\n  duplicate @12 :Text;', 1)
    self.assertTrue(any('Duplicate member name or ordinal' in error for error in validate(changed, self.policy, self.sync)))

