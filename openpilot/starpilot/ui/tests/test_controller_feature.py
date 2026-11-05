"""One shared saved steering-controller choice with exact source and CP guards."""

from unittest.mock import Mock
from openpilot.starpilot.galaxy.settings import ContextSource
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR as HYUNDAI
from opendbc.car.toyota.values import CAR as TOYOTA
from opendbc.car.honda.values import CAR as HONDA
from openpilot.common.params import Params
from openpilot.starpilot.lateral.controller_selection import (
  DOCUMENT_KEY, ControllerMode, parse_document, read_selection,
)
from openpilot.starpilot.lateral.learning_preferences import KEY as LEARNING_KEY, read_preference
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest, row_change
from openpilot.starpilot.galaxy.settings import (
  AuthorityContext, SettingsChanged, SettingsGateway, _onroad_preference, _projection,
)



def required_change(row: FeatureRow) -> FeatureSettingsRequest:
  request = row_change(row)
  assert request is not None
  return request


class TestControllerFeature(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.params = Params(self.temp.name)
    self.cp = interfaces[HYUNDAI.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_6)
    self.permitted = True
    self.owner = FeatureSettingsOwner(self.params, lambda group: self.permitted and group == 'preferences',
                                      vehicle_fingerprint=lambda: str(self.cp.carFingerprint),
                                      vehicle_params=lambda: self.cp)

  def row(self, key=DOCUMENT_KEY):
    state = self.owner.snapshot('torque', parked=False, system_long=False, lateral_context=False, metric=False)
    return next((row for row in state.rows if row.key == key), None)

  def require_row(self, key=DOCUMENT_KEY):
    row = self.row(key)
    assert row is not None
    return row

  def test_exact_choice_visible_without_manual_tuning_or_park_lock(self):
    for vehicle in (HYUNDAI.HYUNDAI_IONIQ_6, HYUNDAI.GENESIS_G70_2020, TOYOTA.TOYOTA_COROLLA_TSS2):
      with self.subTest(vehicle=vehicle):
        self.cp = interfaces[vehicle].get_non_essential_params(vehicle)
        row = self.require_row()
        self.assertIsNotNone(row)
        self.assertTrue(row.available)
        self.assertEqual((row.label, row.value), ('Steering Controller', 'StarPilot Controller'))
        self.assertEqual(row.choices, ('Stock Controller', 'StarPilot Controller'))
        self.assertIn('next drive', row.reason)
        self.assertNotIn('active', row.reason.lower())
        self.assertEqual(_projection(row)['choices'], list(row.choices))
    self.assertTrue(_onroad_preference('torque', DOCUMENT_KEY))
    self.assertFalse(self.params.get_bool('AdvancedLateralTune'))
    self.cp = interfaces[HYUNDAI.KIA_EV6].get_non_essential_params(HYUNDAI.KIA_EV6)
    self.assertEqual(self.require_row().value, "StarPilot Controller")
    self.cp = interfaces[HYUNDAI.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_6)
    self.cp.notCar = True
    self.assertIsNone(self.row())

  def test_registered_stock_torque_has_both_controllers_and_scoped_learning(self):
    self.cp = interfaces[HONDA.HONDA_FIT_4G].get_non_essential_params(HONDA.HONDA_FIT_4G)
    self.assertEqual(self.cp.lateralTuning.which(), 'torque')
    self.assertEqual(self.require_row().choices, ('Stock Controller', 'StarPilot Controller'))
    self.assertEqual(self.require_row().value, 'Stock Controller')
    learning = self.require_row('ForceAutoTuneOff')
    self.assertEqual(learning.label, 'Use StarPilot Auto Learn')
    self.assertEqual(learning.value, 'On')
    self.assertEqual(learning.reason, 'StarPilot’s FLM tuning will attempt to automatically correct torque profiles as you drive.')
    self.assertTrue(self.owner.apply(required_change(learning)))
    self.assertFalse(read_preference(self.params, self.cp).enabled)
    self.assertIsNone(self.params.get('ForceAutoTuneOff'))
    self.assertIsNone(self.params.get(DOCUMENT_KEY))

  def test_stock_pid_torque_uses_same_scoped_learning_preference_only(self):
    self.cp = interfaces[HONDA.HONDA_CIVIC].get_non_essential_params(HONDA.HONDA_CIVIC)
    self.assertEqual(self.cp.lateralTuning.which(), 'pid')
    original = self.cp.as_reader().as_builder().to_bytes()
    self.assertIsNone(self.row())  # No torque-only controller/manual editor.
    learning = self.require_row('ForceAutoTuneOff')
    self.assertEqual((learning.label, learning.value), ('Use StarPilot Auto Learn', 'On'))
    self.assertTrue(learning.available)
    self.assertEqual(_projection(learning)['choices'], ['Off', 'On'])
    self.assertTrue(self.owner.apply(required_change(learning)))
    self.assertFalse(read_preference(self.params, self.cp).enabled)
    self.assertIsNone(self.params.get('ForceAutoTuneOff'))
    self.assertIsNone(self.params.get(DOCUMENT_KEY))
    self.assertEqual(self.cp.as_reader().as_builder().to_bytes(), original)
    self.assertFalse(self.owner.apply(required_change(learning)))  # Original expected bytes are stale.

  def test_learning_capability_rejects_unknown_angle_and_passive_cp(self):
    original = self.cp.as_reader().as_builder()
    for failure in ('unknown', 'brand', 'angle', 'passive', 'dashcam', 'not_car', 'nonfinite'):
      with self.subTest(failure=failure):
        self.cp = original.as_reader().as_builder()
        if failure == 'unknown':
          self.cp.carFingerprint = 'UNREGISTERED_VEHICLE'
        elif failure == 'brand':
          self.cp.brand = 'toyota'
        elif failure == 'angle':
          self.cp.steerControlType = 'angle'
          self.cp.lateralTuning.init('pid')
        elif failure == 'nonfinite':
          self.cp.lateralTuning.torque.latAccelFactor = float('nan')
        else:
          setattr(self.cp, {'passive': 'passive', 'dashcam': 'dashcamOnly', 'not_car': 'notCar'}[failure], True)
        self.assertIsNone(self.owner.controller.learning_capability())
        self.assertIsNone(self.row('ForceAutoTuneOff'))
        self.assertFalse(self.owner.apply(FeatureSettingsRequest('ForceAutoTuneOff', None, 'Off')))
    self.assertIsNone(self.params.get('ForceAutoTuneOff'))
    self.assertIsNone(self.params.get(LEARNING_KEY))

  def test_write_preserves_other_vehicle_and_manual_preferences(self):
    self.params.put_bool('AdvancedLateralTune', True, block=True)
    self.params.put_bool('ForceAutoTuneOff', True, block=True)
    first = self.require_row()
    request = required_change(first)
    self.assertEqual(request.value, 'Stock Controller')
    self.assertTrue(self.owner.apply(request))
    raw = Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes()
    self.assertEqual(parse_document(raw)['vehicles']['HYUNDAI_IONIQ_6']['mode'], 'standard')
    self.assertEqual(read_selection(self.params, self.cp).mode, ControllerMode.STANDARD)
    self.cp = interfaces[TOYOTA.TOYOTA_COROLLA_TSS2].get_non_essential_params(TOYOTA.TOYOTA_COROLLA_TSS2)
    other = self.require_row()
    self.assertEqual(other.value, 'StarPilot Controller')
    self.assertTrue(self.owner.apply(required_change(other)))
    after = parse_document(Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes())['vehicles']
    self.assertEqual(after['HYUNDAI_IONIQ_6']['mode'], 'standard')
    self.assertEqual(after['TOYOTA_COROLLA_TSS2']['mode'], 'standard')
    self.assertTrue(self.params.get_bool('AdvancedLateralTune'))
    self.assertTrue(self.params.get_bool('ForceAutoTuneOff'))

  def test_stale_document_cp_and_authority_rejected(self):
    row = self.require_row()
    request = required_change(row)
    self.cp.lateralTuning.torque.latAccelFactor *= 1.05
    self.assertFalse(self.owner.apply(request))
    self.cp = interfaces[HYUNDAI.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_6)
    row = self.require_row()
    request = required_change(row)
    self.permitted = False
    self.assertFalse(self.owner.apply(request))
    self.permitted = True
    row = self.require_row()
    request = required_change(row)
    self.cp = interfaces[HYUNDAI.GENESIS_G70_2020].get_non_essential_params(HYUNDAI.GENESIS_G70_2020)
    self.assertFalse(self.owner.apply(request))
    self.cp = interfaces[HYUNDAI.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_6)
    request = required_change(self.require_row())
    Path(self.params.get_param_path(DOCUMENT_KEY)).write_bytes(b'{"version":1,"vehicles":{}}')
    self.assertFalse(self.owner.apply(request))

  def test_learning_choice_survives_controller_and_vehicle_changes(self):
    learning = self.require_row('ForceAutoTuneOff')
    self.assertTrue(learning.available)
    self.assertEqual(learning.value, 'On')
    self.assertEqual(learning.reason, 'StarPilot’s FLM tuning will attempt to automatically correct torque profiles as you drive.')
    self.assertTrue(self.owner.apply(required_change(learning)))  # Explicit Off for Ioniq.
    self.assertFalse(read_preference(self.params, self.cp).enabled)
    self.assertIsNone(self.params.get('ForceAutoTuneOff'))
    for _ in range(2):
      self.assertTrue(self.owner.apply(required_change(self.require_row())))
      self.assertEqual(self.require_row('ForceAutoTuneOff').value, 'Off')
    first_cp = self.cp
    self.cp = interfaces[TOYOTA.TOYOTA_COROLLA_TSS2].get_non_essential_params(TOYOTA.TOYOTA_COROLLA_TSS2)
    self.assertEqual(self.require_row('ForceAutoTuneOff').value, 'On')
    self.assertTrue(self.owner.apply(required_change(self.require_row('ForceAutoTuneOff'))))
    self.cp = first_cp
    self.assertEqual(self.require_row('ForceAutoTuneOff').value, 'Off')
    self.assertTrue(self.owner.apply(required_change(self.require_row('ForceAutoTuneOff'))))
    self.assertEqual(self.require_row('ForceAutoTuneOff').value, 'On')
    self.assertIsNone(self.params.get('ForceAutoTuneOff'))

  def test_learning_source_and_controller_changes_revoke_requests(self):
    self.assertTrue(self.owner.apply(required_change(self.require_row())))
    self.assertTrue(self.owner.apply(required_change(self.require_row('ForceAutoTuneOff'))))
    request = required_change(self.require_row())
    self.params.put_bool('ForceAutoTuneOff', True, block=True)
    self.assertFalse(self.owner.apply(request))
    learning = required_change(self.require_row('ForceAutoTuneOff'))
    self.assertTrue(self.owner.apply(required_change(self.require_row())))
    self.assertFalse(self.owner.apply(learning))
    Path(self.params.get_param_path(LEARNING_KEY)).write_bytes(b'invalid')
    self.assertFalse(self.require_row().available)
    self.assertFalse(self.require_row('ForceAutoTuneOff').available)

  def test_controller_save_never_writes_learning_preference(self):
    self.assertTrue(self.owner.apply(required_change(self.require_row('ForceAutoTuneOff'))))
    saved = self.params.get(LEARNING_KEY)
    from openpilot.starpilot.ui import controller_feature
    commit = controller_feature.commit_exact
    writes = []

    def recorded(params, **kwargs):
      writes.append(kwargs['key'])
      return commit(params, **kwargs)

    with mock.patch.object(controller_feature, 'commit_exact', side_effect=recorded):
      self.assertTrue(self.owner.apply(required_change(self.require_row())))
    self.assertEqual(writes, [DOCUMENT_KEY])
    self.assertEqual(self.params.get(LEARNING_KEY), saved)
    self.assertIsNone(self.params.get('ForceAutoTuneOff'))

  def test_corrupt_or_unreadable_document_fails_closed_without_overwrite(self):
    path = Path(self.params.get_param_path(DOCUMENT_KEY))
    for raw in (b'not-json', b'{"version":1,"vehicles":{},"vehicles":{}}',
                b'{"version":2,"vehicles":{}}', b'x' * 4097):
      with self.subTest(raw=raw[:30]):
        path.write_bytes(raw)
        row = self.require_row()
        self.assertFalse(row.available)
        self.assertEqual(row.value, 'Invalid saved controller choice')
        self.assertFalse(self.owner.apply(FeatureSettingsRequest(DOCUMENT_KEY, raw, 'Stock Controller',
                                                                 vehicle_fingerprint=str(self.cp.carFingerprint),
                                                                 capability=row.capability)))
        self.assertEqual(path.read_bytes(), raw)

  def test_galaxy_onroad_saved_choice_preview_confirm_and_revocation(self):
    current = SimpleNamespace(parked=False, cp=self.cp, raw=b'current-cp')
    context = Mock(spec=ContextSource, sample=lambda: AuthorityContext(current.parked, current.cp, current.raw))
    gateway = SettingsGateway(self.params, context, clock=lambda: 10)

    def preview():
      page = gateway.page('torque', 'session', b'generation')
      index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Steering Controller')
      return gateway.preview(page['view'], index, 0, 'session', b'generation', value='Stock Controller')

    intent = preview()
    self.assertIn('created when the driving process starts', intent['question'])
    self.assertTrue(gateway.confirm(intent['intent'], 'session', b'generation'))
    self.assertEqual(read_selection(self.params, self.cp).mode, ControllerMode.STANDARD)
    self.assertFalse(current.parked)  # Saved preference did not require a Park state.

    stale = preview()
    path = Path(self.params.get_param_path(DOCUMENT_KEY))
    path.write_bytes(b'{"version":1,"vehicles":{}}')
    self.assertFalse(gateway.confirm(stale['intent'], 'session', b'generation'))

    fresh = preview()
    current.raw = b'changed-cp'
    with self.assertRaises(SettingsChanged):
      gateway.confirm(fresh['intent'], 'session', b'generation')
    current.raw = b'current-cp'

    expired = preview()
    with self.assertRaises(SettingsChanged):
      gateway.confirm(expired['intent'], 'session', b'other-generation')
    current.cp.lateralTuning.torque.latAccelFactor *= 1.05
    self.assertFalse(gateway.confirm(expired['intent'], 'session', b'generation'))


  def test_native_and_galaxy_learning_help_names_actual_learning_controller(self):
    import json
    from openpilot.starpilot.lateral.controller_selection import replace_mode
    self.cp = interfaces[TOYOTA.TOYOTA_RAV4_TSS2].get_non_essential_params(TOYOTA.TOYOTA_RAV4_TSS2)
    for mode, _label in ((ControllerMode.STANDARD, "Stock Controller"),
                        (ControllerMode.STARPILOT, "StarPilot Controller")):
      with self.subTest(mode=mode):
        self.params.put(DOCUMENT_KEY, json.loads(replace_mode(None, self.cp, mode)), block=True)
        state = self.owner.snapshot('torque', parked=False, system_long=False, lateral_context=False, metric=False)
        learning_rows = [row for row in state.rows if row.key == 'ForceAutoTuneOff']
        self.assertEqual(len(learning_rows), 1)
        row = learning_rows[0]
        self.assertEqual(row.label, 'Use StarPilot Auto Learn')
        self.assertTrue(row.available)
        self.assertEqual(row.value, 'On')
        self.assertEqual(row.reason, 'StarPilot’s FLM tuning will attempt to automatically correct torque profiles as you drive.')
        from openpilot.starpilot.galaxy.setting_descriptions import description
        self.assertEqual(_projection(row)['reason'], description(row))

if __name__ == '__main__':
  unittest.main()
