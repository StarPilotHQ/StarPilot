"""The compiled custom messages must continue to read their earlier layouts."""

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import capnp

from openpilot.cereal import custom
from tools.ci.custom_schema_contract import CONTRACT, describe, validate


class CustomSchemaEvolutionTest(unittest.TestCase):
  def setUp(self):
    self.baseline = json.loads(CONTRACT.read_text())

  def test_native_custom_messages_preserve_committed_layout(self):
    self.assertEqual(validate(describe(custom, self.baseline['roots']), self.baseline), [])

  def test_historical_map_roots_restored_and_development_axes_use_separate_roots(self):
    self.assertEqual(set(self.baseline['roots']), {
      'SlcState', 'SlcAction', 'SlcCruiseEvent', 'SlcDashboardObservation',
      'SlcCruiseCommand', 'AolAxisState', 'AolAxisState.SafetyWire', 'AolAxisState.IntentWire',
      'MapdOut', 'MapdExtendedOut', 'MapdIn',
      'AolAxisState.LaneChangeStatusWire', 'StarPilotModelDataV2', 'StarPilotRadarState',
      'StarPilotSelfdriveState', 'SpotMonitorState', 'StarPilotLateralState', 'StarPilotCarState',
      'StarPilotCarParams', 'StarPilotLongitudinalPlan',
      'LearningControlEvidence', 'LearningAppliedEvidence', 'AutomaticRuntimeProfile',
      'AutomaticLearningSourceReceipt', 'LearningApplication', 'FordLearningEvidence',
      'FordLearningEvent', 'CentreApplicationWitness', 'LearningTorqueProjection',
    })
    self.assertEqual(self.baseline['roots']['MapdExtendedOut'], str(0xa30662f84033036c))
    self.assertEqual(self.baseline['roots']['MapdIn'], str(0xc86a3d38d13eb3ef))
    self.assertEqual(self.baseline['roots']['SpotMonitorState'], str(0xcb9fd56c7057593a))
    self.assertNotIn(str(0xa30662f84033036c), (self.baseline['roots']['AolAxisState.SafetyWire'],
                                           self.baseline['roots']['AolAxisState.IntentWire']))

  def test_lane_status_has_an_independent_wire_without_changing_aol_state(self):
    wire_id = self.baseline['roots']['AolAxisState.LaneChangeStatusWire']
    self.assertEqual(wire_id, str(0xa042ddb70aba3ea9))
    aol_fields = self.baseline['nodes'][self.baseline['roots']['AolAxisState']]['fields']
    self.assertEqual(set(aol_fields), {str(n) for n in range(12)})
    self.assertFalse(any(field['slot']['type'] == {'struct': {'typeId': int(wire_id)}} for field in aol_fields.values()))

  def test_historical_lateral_fields_and_new_feedback_are_append_compatible(self):
    from openpilot.cereal import log
    source = """@0xbbaa06d64a5a9f42;
struct HistoricalLateral @0xc2243c65e0340384 {
  active @0 :Bool;
  frictionThreshold @1 :Float32;
  frictionScale @2 :Float32;
  feedforward @3 :Float32;
  frictionJerk @4 :Float32;
  frictionJerkDeadzone @5 :Float32;
  lowSpeedFactor @6 :Float32;
  unwindDetected @7 :Bool;
}
"""
    with TemporaryDirectory() as directory:
      path = Path(directory) / 'historical.capnp'
      path.write_text(source)
      historical = capnp.SchemaParser().load(str(path))
      old_layout = describe(historical, ['HistoricalLateral'])
      current_layout = describe(custom, ['StarPilotLateralState'])
      old_layout['roots'] = current_layout['roots']
      self.assertEqual(validate(current_layout, old_layout), [])
      values = {'active': True, 'frictionThreshold': .25, 'frictionScale': .5, 'feedforward': .125,
                'frictionJerk': .75, 'frictionJerkDeadzone': .0625, 'lowSpeedFactor': 1.5, 'unwindDetected': True}
      old = historical.HistoricalLateral.new_message(**values)
      with custom.StarPilotLateralState.from_bytes(old.to_bytes()) as current:
        self.assertEqual(current.laneCentering.version, 0)
        self.assertTrue(all(getattr(current, key) == value for key, value in values.items()))
      new = custom.StarPilotLateralState.new_message(**values)
      new.laneCentering.version = 1
      with historical.HistoricalLateral.from_bytes(new.to_bytes()) as prior:
        self.assertEqual(prior.to_dict(), values)
    self.assertEqual(log.Event.schema.fields['starpilotLateralState'].proto.ordinal.explicit, 137)

  def test_historical_selfdrive_alert_fields_and_new_ack_are_append_compatible(self):
    from openpilot.cereal import log

    source = '''using Car = import "/car.capnp";
@0xbbaa06d64a5a9f42;
struct HistoricalSelfdrive @0xf416ec09499d9d19 {
  alertText1 @0 :Text;
  alertText2 @1 :Text;
  alertStatus @2 :AlertStatus;
  alertSize @3 :AlertSize;
  alertType @4 :Text;
  alertSound @5 :Car.CarControl.HUDControl.AudibleAlert;
  vEgo @6 :Float32;
  enum AlertStatus @0xc0f486ad93ed68c9 {
    normal @0; userPrompt @1; critical @2; starpilot @3;
  }
  enum AlertSize @0xe22723d973fc2afb {
    none @0; small @1; mid @2; full @3;
  }
}
'''
    with TemporaryDirectory() as directory:
      path = Path(directory) / 'historical.capnp'
      path.write_text(source)
      historical = capnp.SchemaParser().load(str(path), imports=[str(Path(__file__).parents[3] / 'opendbc_repo/opendbc/car')])
      old_layout = describe(historical, ['HistoricalSelfdrive'])
      current_layout = describe(custom, ['StarPilotSelfdriveState'])
      old_layout['roots'] = current_layout['roots']
      self.assertEqual(validate(current_layout, old_layout), [])
      values = {'alertText1': 'Keep hands on wheel', 'alertText2': 'Prompt', 'alertStatus': 'userPrompt',
                'alertSize': 'mid', 'alertType': 'test', 'alertSound': 'prompt', 'vEgo': 15.0}
      old = historical.HistoricalSelfdrive.new_message(**values)
      with custom.StarPilotSelfdriveState.from_bytes(old.to_bytes()) as current:
        self.assertEqual(current.alertText1, values['alertText1'])
        self.assertEqual(str(current.alertStatus), values['alertStatus'])
        self.assertEqual(str(current.alertSound), values['alertSound'])
        self.assertEqual(current.vEgo, values['vEgo'])
        self.assertEqual(current.conditionalModeAck.version, 0)
      new = custom.StarPilotSelfdriveState.new_message(**values)
      new.conditionalModeAck.version = 1
      with historical.HistoricalSelfdrive.from_bytes(new.to_bytes()) as prior:
        self.assertEqual(prior.to_dict(), values)
    self.assertEqual(log.Event.schema.fields['starpilotSelfdriveState'].proto.ordinal.explicit, 115)

  def test_conditional_proposal_and_manual_receipt_are_appended_to_existing_roots(self):
    slc = custom.SlcState.new_message()
    slc.conditionalMode.version = 1
    slc.conditionalMode.choice = 'conditionalChill'
    slc.conditionalMode.settingsRevision = 7
    with custom.SlcState.from_bytes(slc.to_bytes()) as decoded:
      self.assertEqual(str(decoded.conditionalMode.choice), 'conditionalChill')
      self.assertEqual(decoded.conditionalMode.settingsRevision, 7)

    event = custom.SlcCruiseEvent.new_message()
    event.kind = 'conditionalMode'
    event.manualMode.version = 1
    event.manualMode.choice = 'conditionalExperimental'
    event.manualMode.button = 'lkas'
    event.manualMode.press = 'long'
    event.manualMode.sourceCarStateMonoTime = 100
    event.manualMode.validUntilMonoTime = 200
    with custom.SlcCruiseEvent.from_bytes(event.to_bytes()) as decoded:
      self.assertEqual(str(decoded.kind), 'conditionalMode')
      self.assertEqual((str(decoded.manualMode.choice), str(decoded.manualMode.button), str(decoded.manualMode.press)),
                       ('conditionalExperimental', 'lkas', 'long'))
      self.assertEqual((decoded.manualMode.sourceCarStateMonoTime, decoded.manualMode.validUntilMonoTime), (100, 200))

  def test_vision_appends_to_restored_model_slot_without_reinterpreting_history(self):
    from openpilot.cereal import log
    model_id = self.baseline['roots']['StarPilotModelDataV2']
    self.assertEqual(model_id, str(0x80ae746ee2596b11))
    field = log.Event.schema.fields['slcVisionObservation'].proto
    self.assertEqual(field.ordinal.explicit, 111)
    self.assertEqual(field.slot.type.struct.typeId, int(model_id))
    legacy_enum = self.baseline['nodes'][str(0xab5928774e6e64fc)]
    self.assertEqual(legacy_enum['values'], ['none', 'turnLeft', 'turnRight'])
    source = '''@0xeea51a25b1b2c3d4;
struct HistoricalModel @0x80ae746ee2596b11 {
  turnDirection @0 :TurnDirection;
  enum TurnDirection @0xab5928774e6e64fc { none @0; turnLeft @1; turnRight @2; }
}
'''
    with TemporaryDirectory() as directory:
      path = Path(directory) / 'historical.capnp'
      path.write_text(source)
      historical = capnp.SchemaParser().load(str(path))
      old = historical.HistoricalModel.new_message(turnDirection='turnRight')
      with custom.StarPilotModelDataV2.from_bytes(old.to_bytes()) as current:
        self.assertEqual(str(current.turnDirection), 'turnRight')
        self.assertEqual(str(current.vision.status), 'unknown')
        self.assertEqual(current.vision.producerSessionId, '')
      new = custom.StarPilotModelDataV2.new_message(turnDirection='turnLeft')
      new.vision.status = 'valid'
      new.vision.speedMps = 20
      with historical.HistoricalModel.from_bytes(new.to_bytes()) as prior:
        self.assertEqual(str(prior.turnDirection), 'turnLeft')

  def test_slc_preserves_historical_model_status_without_activating_a_limit(self):
    source = '''@0xbbaa06d64a5a9f41;
struct HistoricalModelStatus @0xa1680744031fdb2d {
  slotId @0 :Text;
  slotName @1 :Text;
  variant @2 :Text;
  variantLabel @3 :Text;
  reason @4 :Text;
  wallTimeNanos @5 :UInt64;
}
'''
    with TemporaryDirectory() as directory:
      path = Path(directory) / 'historical.capnp'
      path.write_text(source)
      historical = capnp.SchemaParser().load(str(path))
      old_layout = describe(historical, ['HistoricalModelStatus'])
      current_layout = describe(custom, ['SlcState'])
      # Root renaming is permitted for a reserved slot; its wire meaning is not.
      old_layout['roots'] = current_layout['roots']
      self.assertEqual(validate(current_layout, old_layout), [])
      values = {'slotId': 'model-a', 'slotName': 'Model A', 'variant': 'small',
                'variantLabel': 'Small', 'reason': 'selected', 'wallTimeNanos': 123456789}
      old = historical.HistoricalModelStatus.new_message(**values)
      with custom.SlcState.from_bytes(old.to_bytes()) as current:
        for name, value in values.items():
          self.assertEqual(getattr(current, name), value)
        self.assertEqual(current.sessionId, '')
        self.assertEqual(current.frameMonoTime, 0)
        self.assertFalse(current.enabled)
        self.assertFalse(current.hasAccepted)
        self.assertFalse(current.hasCeiling)
      new = custom.SlcState.new_message(**values, sessionId='drive-a', frameMonoTime=987654321,
                                        enabled=True, hasCeiling=True, effectiveCap=20)
      with historical.HistoricalModelStatus.from_bytes(new.to_bytes()) as prior:
        self.assertEqual(prior.to_dict(), values)

  def test_new_fields_and_enum_values_are_allowed(self):
    current = copy.deepcopy(self.baseline)
    for node in current['nodes'].values():
      if node['kind'] == 'enum':
        node['values'].append('additionalValue')
      else:
        node['fields']['999'] = {'name': 'additionalField'}
        node['data_words'] += 1
        node['pointers'] += 1
    self.assertEqual(validate(current, self.baseline), [])

  def test_same_ids_cannot_hide_changed_field_meanings(self):
    struct_id = next(key for key, value in self.baseline['nodes'].items() if value['kind'] == 'struct')
    ordinal = next(iter(self.baseline['nodes'][struct_id]['fields']))
    for change in ('name', 'type', 'offset', 'default', 'union', 'removed'):
      with self.subTest(change=change):
        current = copy.deepcopy(self.baseline)
        fields = current['nodes'][struct_id]['fields']
        field = fields[ordinal]
        if change == 'name':
          field['name'] = 'unrelatedMeaning'
        elif change == 'type':
          field['slot']['type'] = {'bool': None}
        elif change == 'offset':
          field['slot']['offset'] += 1
        elif change == 'default':
          field['slot']['defaultValue'] = {'text': 'new default'}
        elif change == 'union':
          field['discriminantValue'] = 0
        else:
          del fields[ordinal]
        self.assertTrue(validate(current, self.baseline))

  def test_reassigned_enum_values_or_custom_roots_are_rejected(self):
    current = copy.deepcopy(self.baseline)
    enum = next(node for node in current['nodes'].values() if node['kind'] == 'enum')
    enum['values'][0], enum['values'][1] = enum['values'][1], enum['values'][0]
    self.assertTrue(validate(current, self.baseline))
    current = copy.deepcopy(self.baseline)
    current['roots']['SlcState'] = '0'
    self.assertTrue(validate(current, self.baseline))

  def test_actual_pointer_defaults_are_serializable_and_protected(self):
    source = '''@0xedeedaceefbcfbca;
struct Child @0xb889677671233ef2 { number @0 :UInt32; }
struct Sample @0xc889677671233ef3 {
  child @0 :Child = (number = CHILD_VALUE);
  values @1 :List(UInt16) = [1, LIST_VALUE];
}
'''
    with TemporaryDirectory() as directory:
      path = Path(directory) / 'fixture.capnp'
      contracts = []
      for child_value, list_value in ((7, 2), (8, 2), (7, 3), (7, 2)):
        path.write_text(source.replace('CHILD_VALUE', str(child_value)).replace('LIST_VALUE', str(list_value)))
        module = capnp.SchemaParser().load(str(path))
        # A separate parser permits independent revisions of the same type IDs.
        contracts.append(json.loads(json.dumps(describe(module, ['Sample']))))
      self.assertEqual(validate(contracts[3], contracts[0]), [])
      self.assertTrue(any('.child:' in issue for issue in validate(contracts[1], contracts[0])))
      self.assertTrue(any('.values:' in issue for issue in validate(contracts[2], contracts[0])))

  def test_binary_data_defaults_are_serializable_lossless_and_protected(self):
    source = '''@0xedeedaceefbcfbca;
struct DataDefaults @0xc889677671233ef4 {
  payload @0 :Data = 0x"DATA_DEFAULT";
  implicitPayload @1 :Data;
}
'''
    with TemporaryDirectory() as directory:
      path = Path(directory) / 'data-defaults.capnp'
      contracts = []
      parsers = []
      for hex_value in ('0001feff', '0002feff', '0001feff'):
        path.write_text(source.replace('DATA_DEFAULT', hex_value))
        parser = capnp.SchemaParser()
        parsers.append(parser)
        module = parser.load(str(path))
        contract = describe(module, ['DataDefaults'])
        # Neither an implicit empty Data default nor an explicit binary default
        # may leak raw bytes into the committed JSON contract.
        contract = json.loads(json.dumps(contract))
        fields = contract['nodes'][str(0xc889677671233ef4)]['fields']
        for ordinal in ('0', '1'):
          self.assertEqual(set(fields[ordinal]['slot']['defaultValue']), {'schemaValueHex'})
        contracts.append(contract)
        message = module.DataDefaults.new_message()
        with module.DataDefaults.from_bytes(message.to_bytes()) as reader:
          self.assertEqual(reader.payload, bytes.fromhex(hex_value))
          self.assertEqual(reader.implicitPayload, b'')
      self.assertEqual(validate(contracts[2], contracts[0]), [])
      self.assertTrue(any('.payload:' in issue for issue in validate(contracts[1], contracts[0])))

  def test_preexisting_payload_helper_root_ids_are_preserved(self):
    expected = {
      'StarPilotLongitudinalPlan': 0xda96579883444c35,
      'LearningControlEvidence': 0xf2447170bb68f358,
      'LearningAppliedEvidence': 0xbab0f51a74ffee6e,
      'AutomaticRuntimeProfile': 0xef38d302825641e3,
      'AutomaticLearningSourceReceipt': 0x9fe2d23e75805149,
      'LearningApplication': 0x82b6c8ea6a6efabd,
      'FordLearningEvidence': 0xd1dfbadb6a811fee,
      'FordLearningEvent': 0xb276dede9e6b2cc4,
      'CentreApplicationWitness': 0xab0b33cac682523e,
      'LearningTorqueProjection': 0xf078f562cbce49ad,
    }
    for name, type_id in expected.items():
      self.assertEqual(self.baseline['roots'][name], str(type_id))
      self.assertEqual(getattr(custom, name).schema.node.id, type_id)


if __name__ == '__main__':
  unittest.main()
