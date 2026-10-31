from typing import cast
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from opendbc.car.gm.tests.test_ascm_intercept import params
from opendbc.car import structs
from opendbc.car.gm.values import CAR, PEDAL_BOLT_CAR
from openpilot.common.params import Params
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


class TestGMPitchPreferences(unittest.TestCase):
  def test_exact_off_and_default_on(self):
    with tempfile.TemporaryDirectory() as root:
      source = SimpleNamespace(get_param_path=lambda key: str(Path(root) / key),
                               get_default_value=Params().get_default_value)
      for raw in (None, b'0', b'1', b'', b'false', b'0\n', b'0' * 20):
        path = Path(root) / 'LongPitch'
        path.unlink(missing_ok=True)
        if raw is not None:
          path.write_bytes(raw)
        snapshot = VehicleStartupPreferences.read(source, enabled=True)
        self.assertEqual(snapshot.gm_long_pitch, raw != b'0')
      path.write_bytes(b'0')
      self.assertTrue(VehicleStartupPreferences.read(source, enabled=False).gm_long_pitch)
      (Path(root) / 'SafeMode').write_bytes(b'1')
      self.assertTrue(VehicleStartupPreferences.read(source, enabled=True).gm_long_pitch)

  def test_final_cp_owner_and_neighbors(self):
    preference = VehicleStartupPreferences(gm_long_pitch=False)
    for candidate in (CAR.CHEVROLET_BOLT_EUV, CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_CC,
                      CAR.CHEVROLET_BOLT_ACC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL):
      for alpha in (False, True):
        cp = params(candidate, alpha=alpha, radar=candidate == CAR.CHEVROLET_VOLT)
        controller = SimpleNamespace(long_pitch=True)
        preference.configure_controller(SimpleNamespace(CP=cp, CC=controller))
        admitted = candidate == CAR.CHEVROLET_VOLT or (alpha and candidate in (CAR.CHEVROLET_BOLT_EUV, CAR.CHEVROLET_BOLT_ACC_2022_2023))
        self.assertEqual(controller.long_pitch, not admitted)

  def test_controller_off_matches_flat_on_all_counters(self):
    from unittest.mock import patch
    from opendbc.car.gm.carcontroller import CarController
    from opendbc.car.gm.tests import test_volt_grade as fixture
    for candidate in (CAR.CHEVROLET_BOLT_EUV, CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_ASCM):
      for alpha in (False, True):
        cp = params(candidate, alpha=alpha, radar=candidate == CAR.CHEVROLET_VOLT,
                    sascm=candidate == CAR.CHEVROLET_VOLT_ASCM)
        for frame in (0, 4, 8, 12):
          for active in (False, True):
            for pitch in (-.03, .03):
              expected, expected_messages = fixture.command(cp, accel=-.5, speed=12., orientation=[0., 0., 0.],
                                                            active=active, frame=frame)
              def create(*args, cp=cp):
                controller = CarController(*args)
                VehicleStartupPreferences(gm_long_pitch=False).configure_controller(SimpleNamespace(CP=cp, CC=controller))
                return controller
              with patch.object(fixture, 'CarController', side_effect=create):
                actual, actual_messages = fixture.command(cp, accel=-.5, speed=12., orientation=[0., pitch, 0.],
                                                          active=active, frame=frame)
              self.assertEqual(actual_messages, expected_messages)
              self.assertEqual((actual.apply_gas, actual.apply_brake), (expected.apply_gas, expected.apply_brake))


class TestGMPitchStartupAndSettings(unittest.TestCase):
  def setUp(self):
    from openpilot.common.params import Params
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.saved = Params(self.temp.name)
    self.saved.put_bool('OpenpilotEnabledToggle', True, block=True)
    self.path = Path(self.saved.get_param_path('LongPitch'))

  def test_stop_preferences_can_be_saved_before_physical_admission(self):
    from dataclasses import replace
    from opendbc.car import gen_empty_fingerprint
    from opendbc.car.gm.interface import CarInterface
    from openpilot.starpilot.vehicle_selection import encode
    from openpilot.starpilot.ui.feature_settings_state import FeaturePage, row_change, row_default
    from openpilot.starpilot.ui.vehicle_bool import confirmation_question
    settings = (("VoltOnePedalMode", "volt_one_pedal", (CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_ASCM,
                                                     CAR.CHEVROLET_VOLT_CAMERA, CAR.CHEVROLET_VOLT_2019)),
                ("VoltSNG", "volt_sng", (CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_ASCM,
                                        CAR.CHEVROLET_VOLT_CAMERA, CAR.CHEVROLET_VOLT_2019)),
                ("GMAutoHold", "gm_auto_hold", (CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_ASCM,
                                              CAR.CHEVROLET_VOLT_CAMERA, CAR.CHEVROLET_VOLT_2019, CAR.BUICK_LACROSSE)))
    for key, preference, identities in settings:
      for identity in identities:
        with self.subTest(key=key, identity=identity):
          Path(self.saved.get_param_path('VehicleSelection')).write_bytes(encode(str(identity)))
          cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
          current, owner, _ = self.settings(cp)
          def row(owner=owner, key=key):
            return next(r for r in owner.snapshot(FeaturePage.VEHICLE, parked=True, system_long=False,
                        lateral_context=False, metric=False).rows if r.key == key)
          self.assertTrue(row().available)
          self.assertTrue(any(r.page == FeaturePage.VEHICLE for r in owner.snapshot(
            FeaturePage.HUB, parked=True, system_long=False, lateral_context=False, metric=False).rows))
          if key == "GMAutoHold":
            self.assertEqual("regen paddle" in row().reason, identity != CAR.BUICK_LACROSSE)
          change = row_change(row())
          assert change is not None
          question = confirmation_question(change)
          if key == "GMAutoHold":
            self.assertEqual("regen paddle" in question, identity != CAR.BUICK_LACROSSE)
          elif key == "VoltOnePedalMode":
            self.assertIn("Lifting off the accelerator can apply the brakes", question)
          self.assertTrue(owner.apply(replace(change, confirmation=True)))
          self.assertEqual(self.saved.get_bool(key), change.value == 'On')
          self.assertTrue(owner.apply(row_default(row())))
          self.assertFalse(self.saved.get_bool(key))
          controller = SimpleNamespace(volt_sng=False, gm_auto_hold=False, volt_one_pedal=False, long_pitch=True)
          VehicleStartupPreferences(**cast(dict, {preference: True})).configure_controller(SimpleNamespace(CP=cp, CC=controller))
          self.assertFalse(getattr(controller, preference))
          fresh = row()
          pending = row_change(fresh)
          assert pending is not None
          pending = replace(pending, confirmation=True)
          path = Path(self.saved.get_param_path(key))
          path.write_bytes(b'0' if fresh.value == 'On' else b'1')
          self.assertFalse(owner.apply(pending))
          assert pending.expected is not None
          path.write_bytes(pending.expected)
          Path(self.saved.get_param_path('VehicleSelection')).write_bytes(encode(str(CAR.CHEVROLET_BOLT_EUV)))
          self.assertFalse(owner.apply(pending))
          current.parked = False
          self.assertFalse(owner.apply(replace(change, confirmation=True)))

  def test_native_typed_key_default_and_read_write(self):
    from openpilot.common.params import ParamKeyType
    self.assertEqual(self.saved.get_type('LongPitch'), ParamKeyType.BOOL)
    self.assertIs(self.saved.get_default_value('LongPitch'), True)
    self.assertIsNone(self.saved.get('LongPitch'))
    self.assertIs(self.saved.get('LongPitch', return_default=True), True)
    for enabled in (False, True):
      self.saved.put('LongPitch', enabled, block=True)
      self.assertIs(self.saved.get('LongPitch'), enabled)
      self.assertEqual(self.path.read_bytes(), b'1' if enabled else b'0')
      self.assertEqual(self.saved.get_bool('LongPitch'), enabled)
    self.saved.remove('LongPitch')
    self.assertFalse(self.path.exists())
    self.assertIs(self.saved.get('LongPitch', return_default=True), True)

  def cp(self, candidate=CAR.CHEVROLET_VOLT, **kwargs):
    return params(candidate, radar=candidate == CAR.CHEVROLET_VOLT, **kwargs)

  def row(self, owner, *, parked=True):
    from openpilot.starpilot.ui.feature_settings_state import FeaturePage
    return next(row for row in owner.snapshot(FeaturePage.VEHICLE, parked=parked, system_long=True,
                lateral_context=True, metric=False).rows if row.key == 'LongPitch')

  def change(self, row):
    from openpilot.starpilot.ui.feature_settings_state import row_change
    change = row_change(row)
    assert change is not None
    return change

  def settings(self, cp=None):
    from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
    from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway
    current = SimpleNamespace(parked=True, cp=self.cp() if cp is None else cp, raw=b'current-car')
    owner = FeatureSettingsOwner(self.saved, lambda group: current.parked and group in ('parked_preferences', 'preferences'),
      vehicle_fingerprint=lambda: current.cp.carFingerprint if current.cp is not None else None,
      vehicle_params=lambda: current.cp)
    class TestContext:
      def sample(self):
        return AuthorityContext(current.parked, current.cp, current.raw)

    gateway = SettingsGateway(self.saved, TestContext(),
                              clock=lambda: 10.)
    return current, owner, gateway

  def test_appended_snapshot_preserves_turn_assist_positional_contract(self):
    snapshot = VehicleStartupPreferences(True, True)
    self.assertTrue(snapshot.toyota_auto_hold)
    self.assertTrue(snapshot.turn_assist)
    self.assertTrue(snapshot.gm_long_pitch)
    self.path.write_bytes(b'0')
    Path(self.saved.get_param_path('TurnAssist')).write_bytes(b'1')
    actual = VehicleStartupPreferences.read(self.saved, enabled=True)
    self.assertFalse(actual.gm_long_pitch)
    self.assertTrue(actual.turn_assist)

  def test_missing_unreadable_and_unsafe_pitch_remain_default_on(self):
    self.path.write_bytes(b'0')
    for safe in (b'1', b'invalid', b'', b'0', None):
      path = Path(self.saved.get_param_path('SafeMode'))
      path.unlink(missing_ok=True)
      if safe is not None:
        path.write_bytes(safe)
      self.assertEqual(VehicleStartupPreferences.read(self.saved, enabled=True).gm_long_pitch, safe not in (None, b'0'))
    self.path.unlink()
    self.path.symlink_to('SafeMode')
    self.assertTrue(VehicleStartupPreferences.read(self.saved, enabled=True).gm_long_pitch)

  def test_native_owner_missing_default_and_exact_guarded_on_off(self):
    from dataclasses import replace
    from openpilot.starpilot.ui.feature_settings_state import FeaturePage
    current, owner, _ = self.settings()
    toggle = self.row(owner)
    self.assertEqual((toggle.value, toggle.source, toggle.available), ('On', None, True))
    self.assertIn('Refreshes during the drive', toggle.reason)
    self.assertTrue(any(row.page == FeaturePage.VEHICLE for row in owner.snapshot(
      FeaturePage.HUB, parked=True, system_long=True, lateral_context=True, metric=False).rows))
    request = replace(self.change(toggle), confirmation=True)
    self.assertTrue(owner.apply(request))
    self.assertEqual(self.path.read_bytes(), b'0')
    self.assertFalse(owner.apply(request))
    self.assertTrue(owner.apply(replace(self.change(self.row(owner)), confirmation=True)))
    self.assertEqual(self.path.read_bytes(), b'1')
    request = replace(self.change(self.row(owner)), confirmation=True)
    current.parked = False
    self.assertFalse(self.row(owner, parked=False).available)
    self.assertFalse(owner.apply(request))

  def test_exact_vehicle_and_saved_sources_guard_native_owner(self):
    from dataclasses import replace
    for change in ('vin', 'native', 'owner', 'bytes'):
      with self.subTest(change=change):
        self.path.unlink(missing_ok=True)
        current, owner, _ = self.settings()
        request = replace(self.change(self.row(owner)), confirmation=True)
        if change == 'vin':
          current.cp.carVin = 'different-car'
        elif change == 'native':
          current.cp.safetyConfigs[0].safetyParam = 0
        elif change == 'owner':
          current.cp.passive = True
        else:
          self.path.write_bytes(b'1')
        self.assertFalse(owner.apply(request))
        self.assertEqual(self.path.read_bytes() if self.path.exists() else None, b'1' if change == 'bytes' else None)

  def test_invalid_saved_bytes_remain_visible_and_untouched(self):
    from openpilot.starpilot.ui.feature_settings_state import row_change
    _, owner, _ = self.settings()
    for raw in (b'', b'false', b'0\n', b'\xff', b'1' * 129):
      self.path.write_bytes(raw)
      toggle = self.row(owner)
      self.assertFalse(toggle.available)
      self.assertIsNone(row_change(toggle))
      self.assertEqual(self.path.read_bytes(), raw)

  def test_gateway_direct_save_and_revalidation(self):
    from openpilot.starpilot.galaxy.settings import SettingsChanged
    for revoke in (None, 'parked', 'cp', 'session', 'bytes'):
      with self.subTest(revoke=revoke):
        self.path.unlink(missing_ok=True)
        current, _, gateway = self.settings()
        page = gateway.page('vehicle', 'session', b'generation')
        index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Grade Compensation')
        self.assertTrue(page['rows'][index]['available'])
        intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='Off')
        self.assertIn('refreshes during the drive', intent['question'])
        if revoke == 'parked':
          current.parked = False
        if revoke == 'cp':
          current.raw = b'other-car'
        if revoke == 'bytes':
          self.path.write_bytes(b'1')
        if revoke == 'parked':
          self.assertTrue(gateway.confirm(intent['intent'], 'session', b'generation'))
          self.assertEqual(self.path.read_bytes(), b'0')
        elif revoke in ('cp', 'session'):
          with self.assertRaises(SettingsChanged):
            gateway.confirm(intent['intent'], 'session', b'generation', session_valid=lambda revoke=revoke: revoke != 'session')
        else:
          self.assertEqual(gateway.confirm(intent['intent'], 'session', b'generation'), revoke is None)
        self.assertEqual(self.path.read_bytes() if self.path.exists() else None,
                         b'0' if revoke in (None, 'parked') else b'1' if revoke == 'bytes' else None)

  def test_suburban_gateway_setting_reaches_controller_and_rechecks_permission(self):
    from dataclasses import replace
    from opendbc.car.gm.interface import CarInterface

    cp = params(CAR.CHEVROLET_SUBURBAN, radar=True)
    current, owner, gateway = self.settings(cp)
    self.assertEqual((self.row(owner).value, self.row(owner).available), ('On', True))
    page = gateway.page('vehicle', 'session', b'generation')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Grade Compensation')
    intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='Off')
    self.assertTrue(gateway.confirm(intent['intent'], 'session', b'generation'))
    self.assertEqual(self.path.read_bytes(), b'0')
    ci = CarInterface(cp)
    VehicleStartupPreferences.read(self.saved, enabled=True).configure_controller(ci)
    self.assertFalse(ci.CC.long_pitch)
    request = replace(self.change(self.row(owner)), confirmation=True)
    for attribute in ('passive', 'dashcamOnly', 'notCar'):
      setattr(current.cp, attribute, True)
      self.assertIsNone(owner._long_pitch_capability())
      self.assertFalse(owner.apply(request))
      setattr(current.cp, attribute, False)
    current.cp.safetyConfigs[0].safetyParam = 1
    self.assertIsNone(owner._long_pitch_capability())
    self.assertFalse(owner.apply(request))
    self.assertEqual(self.path.read_bytes(), b'0')

  def test_stock_pedal_cc_release_and_passive_rows_remain_unavailable(self):
    from openpilot.starpilot.ui.feature_settings_state import FeaturePage
    for candidate, kwargs in ((CAR.CHEVROLET_BOLT_EUV, {}), (CAR.CHEVROLET_BOLT_EUV, {'alpha': True, 'release': True}),
      (CAR.CHEVROLET_VOLT_CC, {}), (CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, {})):
      current, owner, _ = self.settings(self.cp(candidate, **kwargs))
      self.assertFalse(any(row.key == 'LongPitch' for row in owner.snapshot(
        FeaturePage.VEHICLE, parked=True, system_long=True, lateral_context=True, metric=False).rows))
    cp = self.cp()
    cp.passive = True
    _, owner, _ = self.settings(cp)
    self.assertFalse(owner._long_pitch_capability())

  def test_actual_card_final_cp_binds_once_after_passive_decision(self):
    from unittest.mock import Mock, patch
    from opendbc.car.car_helpers import interfaces
    from openpilot.selfdrive.car import card
    cases = [(CAR.CHEVROLET_VOLT, {}, True),
             (CAR.CHEVROLET_VOLT, {'release': True}, True),
             (CAR.CHEVROLET_BOLT_EUV, {'alpha': True}, True),
             (CAR.CHEVROLET_VOLT_ASCM, {'alpha': True, 'sascm': True}, True),
             (CAR.CHEVROLET_BOLT_EUV, {}, False),
             (CAR.CHEVROLET_BOLT_EUV, {'alpha': True, 'release': True}, False),
             (CAR.CHEVROLET_VOLT_ASCM, {'sascm': True}, False),
             (CAR.CHEVROLET_VOLT_ASCM, {'alpha': True, 'sascm': True, 'release': True}, False),
             (CAR.CHEVROLET_BOLT_ACC_2022_2023, {}, False),
             (CAR.CHEVROLET_VOLT_CC, {}, False)]
    cases.extend((candidate, {}, False) for candidate in PEDAL_BOLT_CAR)
    for candidate, kwargs, admitted in cases:
      for enabled, raw in ((True, None), (True, b'0'), (True, b'1'), (False, b'0')):
        with self.subTest(candidate=candidate, kwargs=kwargs, enabled=enabled, raw=raw):
          self.path.unlink(missing_ok=True)
          if raw is not None:
            self.path.write_bytes(raw)
          self.saved.put_bool('OpenpilotEnabledToggle', enabled, block=True)
          cp = self.cp(candidate, **kwargs)
          expected_passive = not enabled or cp.dashcamOnly
          ci = interfaces[cp.carFingerprint](cp)
          ri = interfaces[cp.carFingerprint].RadarInterface(cp)
          def later_controller_setup(*args):
            # A later source change cannot change the startup-bound controller.
            self.path.write_bytes(b'1')
          with patch.object(card, 'Params', return_value=self.saved), \
            patch.object(card, 'feature_requested', return_value=False), \
            patch.object(card.messaging, 'sub_sock', return_value=Mock()), \
            patch.object(card.messaging, 'SubMaster', side_effect=lambda *_args, **kwargs: SimpleNamespace(
              ignore_alive=list(kwargs.get('ignore_alive', [])), ignore_valid=list(kwargs.get('ignore_valid', [])),
              ignore_average_freq=list(kwargs.get('ignore_average_freq', [])))), \
            patch.object(card.messaging, 'PubMaster', return_value=SimpleNamespace(sock={'sendcan': Mock()})), \
            patch.object(card, 'Ratekeeper', return_value=Mock()), \
            patch.object(card, 'get_cache', return_value=None), patch.object(card, 'put_cache'), \
            patch.object(card, 'configure_controller', side_effect=later_controller_setup):
            host = card.Car(CI=ci, RI=ri)
          self.assertEqual(host.CI.CC.long_pitch, not (admitted and enabled and raw == b'0'))
          self.assertEqual(host.CP.passive, expected_passive)
          with structs.CarParams.from_bytes(self.saved.get('CarParams')) as published:
            self.assertEqual(published.passive, expected_passive)
