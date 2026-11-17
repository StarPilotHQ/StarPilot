from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from opendbc.car import car_helpers, gen_empty_fingerprint, structs
from opendbc.car.toyota.values import CAR, ToyotaFlags
from opendbc.safety import ALTERNATIVE_EXPERIENCE
from openpilot.common.params import Params
from openpilot.selfdrive.car import card
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


class TestVehicleStartupPreferences(unittest.TestCase):
  def test_gm_tune_startup_requires_exact_saved_selection(self):
    for raw, expected in ((None, 0), (b"0", 0), (b"1", 1), (b"2", 0),
                          (b"3", 0), (b"1\n", 0), (b"true", 0)):
      self.raw("GMLongitudinalTune", raw)
      self.raw("SafeMode", b"0")
      selected = VehicleStartupPreferences.read(self.params, enabled=True)
      self.assertEqual(selected.gm_longitudinal_tune, expected)
      self.assertEqual(VehicleStartupPreferences.read(self.params, enabled=False).gm_longitudinal_tune, 0)
      self.raw("SafeMode", b"1")
      self.assertEqual(VehicleStartupPreferences.read(self.params, enabled=True).gm_longitudinal_tune, 0)
      self.assertEqual(Path(self.params.get_param_path("GMLongitudinalTune")).read_bytes() if raw is not None else None, raw)

  def test_gm_acc_tune_provider_deadline_and_frozen_vehicle(self):
    from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
    from opendbc.car.gm.values import CAR as GM_CAR
    from openpilot.starpilot.car.gm.tune_preferences import AccTunePreference
    cp = ordinary_params(GM_CAR.CHEVROLET_SUBURBAN, radar=True)
    original = cp.to_dict()
    for key, raw in (("OpenpilotEnabledToggle", b"0"), ("SafeMode", b"1"),
                     ("DisableOpenpilotLongitudinal", b"1"), ("GMLongitudinalTune", b"2")):
      self.raw("OpenpilotEnabledToggle", b"1")
      self.raw("SafeMode", b"0")
      self.raw("DisableOpenpilotLongitudinal", b"0")
      self.raw("GMLongitudinalTune", b"1")
      preference = AccTunePreference(cp, self.params)
      self.assertTrue(preference.update(1_000_000_000))
      self.raw(key, raw)
      self.assertTrue(preference.update(1_499_999_999))
      self.assertFalse(preference.update(1_500_000_000))
      self.assertEqual(cp.to_dict(), original)
    self.assertFalse(preference.update(999_999_999))

  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)

  def raw(self, key, value):
    path = Path(self.params.get_param_path(key))
    if value is None:
      path.unlink(missing_ok=True)
    else:
      path.write_bytes(value)

  def test_prius_filter_card_rechecks_final_preferences_without_stock_promotion(self):
    observed = gen_empty_fingerprint()
    observed[0][0x2FF] = 8  # Legacy EPS73 smartDSU, not a four-byte advertisement.
    eps = structs.CarParams.CarFw.new_message(
      ecu=structs.CarParams.Ecu.eps, fwVersion=b'8965B47070\x00\x00\x00\x00\x00\x00')
    for key in ('OpenpilotEnabledToggle', 'SafeMode', 'DisableOpenpilotLongitudinal'):
      for initially_stock in (False, True):
        with self.subTest(key=key, initially_stock=initially_stock):
          self.raw('SafeMode', b'0')
          self.raw('DisableOpenpilotLongitudinal', b'1' if initially_stock else b'0')

          def change(ci, key=key, initially_stock=initially_stock):
            self.raw(key, b'0' if key == 'OpenpilotEnabledToggle' else b'1')
            if initially_stock:
              self.raw('OpenpilotEnabledToggle', b'1')
              self.raw('SafeMode', b'0')
              self.raw('DisableOpenpilotLongitudinal', b'0')

          host, constructed, published = self.start(
            CAR.TOYOTA_PRIUS_RETROFIT, observed=observed, car_fw=[eps],
            key='LongPitch', requested=True, after_construct=change,
            capture=lambda ci: int(ci.CP.safetyConfigs[0].safetyParam))
          self.assertEqual(constructed, [0x8249 if initially_stock else 0x8049])
          self.assertEqual(published.safetyConfigs[0].safetyParam, 0x8249)
          self.assertTrue(published.pcmCruise)
          self.assertFalse(published.openpilotLongitudinalControl)
          self.assertFalse(host.CI.CC.prius_longitudinal)
          self.assertIsNone(host.CI.CC.prius_filter_input)

  def test_prius_filter_live_preference_withdraws_at_read_deadline(self):
    from opendbc.car.toyota.interface import CarInterface
    from openpilot.starpilot.car.toyota.prius_preferences import PriusFilterPreference
    observed = gen_empty_fingerprint()
    observed[0][0x2FF] = 8  # Legacy EPS73 smartDSU, not a four-byte advertisement.
    eps = structs.CarParams.CarFw.new_message(
      ecu=structs.CarParams.Ecu.eps, fwVersion=b'8965B47070\x00\x00\x00\x00\x00\x00')
    cp = CarInterface.get_params(CAR.TOYOTA_PRIUS_RETROFIT, observed, [eps], False, False, False)
    self.assertEqual(cp.safetyConfigs[0].safetyParam, 0x8049)
    for key in ('OpenpilotEnabledToggle', 'SafeMode', 'DisableOpenpilotLongitudinal'):
      with self.subTest(key=key):
        self.raw('OpenpilotEnabledToggle', b'1')
        self.raw('SafeMode', b'0')
        self.raw('DisableOpenpilotLongitudinal', b'0')
        preference = PriusFilterPreference(cp, self.params)
        self.assertTrue(preference.update(1_000_000_000))
        self.raw(key, b'0' if key == 'OpenpilotEnabledToggle' else b'1')
        self.assertTrue(preference.update(1_249_999_999))
        self.assertFalse(preference.update(1_250_000_000))
        self.assertEqual(cp.safetyConfigs[0].safetyParam, 0x8049)

  def test_gm_stop_preferences_require_strict_saved_opt_in(self):
    from openpilot.common.params import ParamKeyType
    for key, name in (("VoltSNG", "volt_sng"), ("GMAutoHold", "gm_auto_hold"), ("VoltOnePedalMode", "volt_one_pedal")):
      self.raw("SafeMode", None)
      self.assertEqual(self.params.get_type(key), ParamKeyType.BOOL)
      self.assertIs(self.params.get_default_value(key), False)
      for raw in (None, b"0", b"", b"true", b"1\n", b"1" * 20, b"1"):
        self.raw(key, raw)
        self.assertEqual(getattr(VehicleStartupPreferences.read(self.params, enabled=True), name), raw == b"1")
      self.assertFalse(getattr(VehicleStartupPreferences.read(self.params, enabled=False), name))
      self.raw("SafeMode", b"1")
      self.assertFalse(getattr(VehicleStartupPreferences.read(self.params, enabled=True), name))
    self.assertTrue(VehicleStartupPreferences(True, True).turn_assist)

  def test_ordinary_camera_saved_disable_reaches_final_factory_before_controller(self):
    from opendbc.car.gm.values import CAR as GM_CAR, is_ordinary_camera_profile
    from opendbc.car.gm.startup_preferences import disable_long_supported
    for camera in (False, True):
      for state in ('absent', 'off', 'on', 'alpha_off', 'release', 'master_off'):
        with self.subTest(camera=camera, state=state):
          self.raw('SafeMode', b'0')
          self.raw('DisableOpenpilotLongitudinal', b'1' if state == 'on' else b'0' if state == 'off' else None)
          self.params.put_bool('AlphaLongitudinalEnabled', state != 'alpha_off', block=True)
          self.params.put_bool('IsReleaseBranch', state == 'release', block=True)
          observed = gen_empty_fingerprint()
          observed[0].update({0xF1: 6, 0xBE: 6, 0xC9: 8, 0x1C4: 8, 0x184: 8, 0x34A: 5})
          if camera:
            observed[2].update({0x320: 6, 0x180: 4, 0x370: 6})
          else:
            observed[0][0x1E1] = 7
          active = state in ('absent', 'off', 'master_off')
          word = (0xC170 if active else 0xC171) if camera else (0xC173 if active else 0xC172)
          host, constructed, published = self.start(
            GM_CAR.CHEVROLET_SILVERADO, observed=observed, key='LongPitch', requested=False,
            enabled=state != 'master_off', capture=lambda ci: int(ci.CS.CP.safetyConfigs[0].safetyParam))
          self.assertEqual(constructed, [word])
          if state == 'master_off':
            self.assertEqual(published.safetyConfigs[0].safetyModel, structs.CarParams.SafetyModel.noOutput)
            self.assertFalse(disable_long_supported(published))
            continue
          self.assertEqual(published.safetyConfigs[0].safetyParam, word)
          self.assertEqual(published.openpilotLongitudinalControl, active)
          self.assertEqual(published.pcmCruise, not active)
          expected_speed = structs.CarParams(minEnableSpeed=0. if active else 5. / 3.6).minEnableSpeed
          self.assertEqual(published.minEnableSpeed, expected_speed)
          self.assertFalse(published.autoResumeSng)
          self.assertTrue(is_ordinary_camera_profile(published, longitudinal=active))
          self.assertTrue(disable_long_supported(published))
          self.assertEqual(host.CI.CP.to_dict(), published.to_dict())
          self.assertEqual(Path(self.params.get_param_path('DisableOpenpilotLongitudinal')).read_bytes()
                           if state in ('on', 'off') else None, b'1' if state == 'on' else b'0' if state == 'off' else None)

  def test_ordinary_camera_disable_never_admits_invalid_owner(self):
    from opendbc.car.gm.tests.test_ordinary_camera import params
    from opendbc.car.gm.values import CAR as GM_CAR, GMFlags
    from opendbc.car.gm.startup_preferences import disable_long_supported, prepare_disable_longitudinal
    for field, value in (('brand', 'hyundai'), ('passive', True), ('dashcamOnly', True), ('notCar', True),
                         ('radarUnavailable', False), ('flags', int(GMFlags.PEDAL_LONG))):
      with self.subTest(field=field):
        cp = params(GM_CAR.CHEVROLET_SILVERADO, alpha=True)
        setattr(cp, field, value)
        before = cp.to_dict()
        self.assertFalse(disable_long_supported(cp))
        prepare_disable_longitudinal(cp, True)
        self.assertEqual(cp.to_dict(), before)

  def test_camera_interceptor_card_defaults_and_stock_reductions(self):
    from opendbc.car.gm.tests.test_camera_acc_pedal import fingerprint
    from opendbc.car.gm.values import CAR as GM_CAR, camera_acc_pedal_profile
    for camera, be, word in ((True, True, 0xE100), (True, False, 0xE101),
                             (False, True, 0xE102), (False, False, 0xE103)):
      for state in ('fresh', 'legacy_off', 'master_off', 'safe', 'invalid_safe', 'disable_long', 'release'):
        with self.subTest(camera=camera, be=be, state=state):
          self.raw('GMPedalLongitudinal', b'0' if state == 'legacy_off' else None)
          self.raw('SafeMode', b'bad' if state == 'invalid_safe' else b'1' if state == 'safe' else None)
          self.raw('DisableOpenpilotLongitudinal', b'1' if state == 'disable_long' else None)
          self.params.put_bool('IsReleaseBranch', state == 'release', block=True)
          self.params.put_bool('AlphaLongitudinalEnabled', False, block=True)
          host, constructed, published = self.start(
            GM_CAR.CHEVROLET_SILVERADO, observed=fingerprint(camera=camera, be=be),
            key='LongPitch', requested=False, enabled=state != 'master_off',
            capture=lambda ci: int(ci.CS.CP.safetyConfigs[0].safetyParam))
          active = state in ('fresh', 'legacy_off')
          expected = word if active else word + 0x10
          self.assertEqual(constructed, [expected])
          if state == 'master_off':
            self.assertEqual(published.safetyConfigs[0].safetyModel, structs.CarParams.SafetyModel.noOutput)
            self.assertIsNone(camera_acc_pedal_profile(published))
          else:
            self.assertEqual(published.safetyConfigs[0].safetyParam, expected)
            self.assertEqual(camera_acc_pedal_profile(published).longitudinal, active)
            self.assertEqual(published.pcmCruise, not active)
            self.assertEqual(published.openpilotLongitudinalControl, active)
            self.assertEqual(published.autoResumeSng, active)
            self.assertEqual(host.CI.CC.camera_pedal_profile.longitudinal, active)
            if active:
              self.assertFalse(host.CI.CC.long_pitch)
              self.assertTrue(host.CI.CC.camera_pedal_input.update(1_000_000_000))
            else:
              self.assertIsNone(host.CI.CC.camera_pedal_input)
          self.assertEqual(Path(self.params.get_param_path('GMPedalLongitudinal')).exists(), state == 'legacy_off')

  def test_camera_interceptor_unreadable_preference_reduces_without_promotion(self):
    from opendbc.car.gm.tests.test_camera_acc_pedal import params
    from opendbc.car.gm.values import camera_acc_pedal_profile
    for key in ('SafeMode', 'DisableOpenpilotLongitudinal'):
      self.raw(key, None)
      path = Path(self.params.get_param_path(key))
      path.symlink_to('missing-setting')
      cp = params()
      preferences = VehicleStartupPreferences.read(self.params, enabled=True)
      self.assertFalse(preferences.gm_camera_pedal)
      preferences.prepare(cp)
      self.assertFalse(camera_acc_pedal_profile(cp).longitudinal)
      path.unlink()
      VehicleStartupPreferences.read(self.params, enabled=True).finalize(cp)
      self.assertFalse(camera_acc_pedal_profile(cp).longitudinal)

  def test_volt_cc_interceptor_card_binds_live_preferences_and_stock_reductions(self):
    from opendbc.car.gm.tests.test_camera_acc_pedal import fingerprint
    from opendbc.car.gm.radar_interface import RADAR_HEADER_MSG
    from opendbc.car.gm.values import CAR as GM_CAR, volt_cc_pedal_profile
    from opendbc.car.gm.feature_capabilities import longitudinal_supported, display_supported
    for be in (False, True):
      for radar in (False, True):
        for removed in (False, True):
          for state in ('fresh', 'legacy_off', 'safe', 'invalid_safe', 'disable_long', 'release', 'master_off'):
            with self.subTest(be=be, radar=radar, removed=removed, state=state):
              observed = fingerprint(camera=not removed, be=be)
              observed[0].update({0xBD: 7, 0x232: 8, 0x3D1: 8})
              if radar:
                observed[1][RADAR_HEADER_MSG] = 8
              self.raw('GMPedalLongitudinal', b'0' if state == 'legacy_off' else None)
              self.raw('SafeMode', b'bad' if state == 'invalid_safe' else b'1' if state == 'safe' else None)
              self.raw('DisableOpenpilotLongitudinal', b'1' if state == 'disable_long' else None)
              self.params.put_bool('IsReleaseBranch', state == 'release', block=True)
              self.params.put_bool('AlphaLongitudinalEnabled', False, block=True)
              host, constructed, cp = self.start(GM_CAR.CHEVROLET_VOLT_CC, observed=observed,
                key='LongPitch', requested=False, enabled=state != 'master_off',
                capture=lambda ci: int(ci.CS.CP.safetyConfigs[0].safetyParam))
              active = state in ('fresh', 'legacy_off')
              word = (0xE600 if active else 0xE610) + 4 * int(radar) + 2 * int(removed) + int(not be)
              self.assertEqual(constructed, [word])
              if state == 'master_off':
                self.assertEqual(cp.safetyConfigs[0].safetyModel, structs.CarParams.SafetyModel.noOutput)
                self.assertIsNone(volt_cc_pedal_profile(cp))
                continue
              self.assertEqual(cp.safetyConfigs[0].safetyParam, word)
              self.assertEqual(volt_cc_pedal_profile(cp).longitudinal, active)
              self.assertFalse(cp.pcmCruise)
              self.assertEqual(cp.autoResumeSng, active)
              self.assertEqual(longitudinal_supported(cp), active)
              self.assertTrue(display_supported(cp))
              if active:
                self.assertFalse(host.CI.CC.long_pitch)
                self.assertTrue(host.CI.CC.camera_pedal_input.update(1_000_000_000))
                self.assertFalse(host.CI.CC.longitudinal_maneuver_input.update(1_000_000_000))
                self.raw('LongitudinalManeuverMode', b'1')
                self.assertTrue(host.CI.CC.longitudinal_maneuver_input.update(1_250_000_000))
                self.raw('OpenpilotEnabledToggle', b'0')
                self.assertFalse(host.CI.CC.camera_pedal_input.update(1_500_000_000))
                self.assertFalse(host.CI.CC.longitudinal_maneuver_input.update(1_500_000_000))
                self.raw('LongitudinalManeuverMode', None)
              else:
                self.assertIsNone(host.CI.CC.camera_pedal_input)

  def test_volt_interceptor_card_preserves_stop_preferences(self):
    from opendbc.car.gm.tests.test_camera_acc_pedal import fingerprint
    from opendbc.car.gm.values import CAR as GM_CAR, camera_acc_pedal_profile, is_gm_auto_hold, is_volt_one_pedal
    self.params.put_bool('AlphaLongitudinalEnabled', False, block=True)
    for camera, be, offset in ((True, True, 0), (True, False, 1), (False, True, 2), (False, False, 3)):
      observed = fingerprint(camera=camera, be=be)
      observed[0].update({0xBD: 7, 0x232: 8})
      for hold, one_pedal, base in ((False, False, 0xE200), (True, False, 0xE220),
                                   (False, True, 0xE240), (True, True, 0xE260)):
        with self.subTest(camera=camera, be=be, hold=hold, one_pedal=one_pedal):
          self.params.put_bool('GMAutoHold', hold, block=True)
          self.params.put_bool('VoltOnePedalMode', one_pedal, block=True)
          host, constructed, cp = self.start(GM_CAR.CHEVROLET_VOLT_CAMERA, observed=observed, key='LongPitch', requested=False,
                                              capture=lambda ci: int(ci.CS.CP.safetyConfigs[0].safetyParam))
          self.assertEqual(constructed, [base + offset])
          self.assertEqual(cp.safetyConfigs[0].safetyParam, base + offset)
          self.assertTrue(camera_acc_pedal_profile(cp).volt)
          self.assertEqual(is_gm_auto_hold(cp), hold or one_pedal)
          self.assertEqual(is_volt_one_pedal(cp), one_pedal)
          self.assertEqual(host.CI.CC.gm_auto_hold, hold)
          self.assertEqual(host.CI.CC.volt_one_pedal, one_pedal)
          self.assertFalse(host.CI.CC.longitudinal_maneuver_input.update(1_000_000_000))
          self.assertTrue(host.CI.CC.camera_pedal_input.update(1_000_000_000))

  def test_volt_interceptor_maneuver_is_live_opt_in_without_owner_rewrite(self):
    from opendbc.car.gm.tests.test_camera_acc_pedal import fingerprint, params
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import CAR as GM_CAR
    from openpilot.starpilot.car.gm.camera import CameraPedalPreference
    observed = fingerprint()
    observed[0].update({0xBD: 7, 0x232: 8})
    cp = CarInterface.get_params(GM_CAR.CHEVROLET_VOLT_CAMERA, observed, [], False, False, False)
    self.params.put_bool('OpenpilotEnabledToggle', True, block=True)
    owner = CameraPedalPreference(cp, self.params, maneuver=True)
    self.assertFalse(owner.update(1_000_000_000))
    self.raw('LongitudinalManeuverMode', b'1')
    self.assertFalse(owner.update(1_249_999_999))
    self.assertTrue(owner.update(1_250_000_000))
    self.raw('LongitudinalManeuverMode', b'0')
    self.assertFalse(owner.update(1_500_000_000))
    for index, raw in enumerate((b'', b'bad', b'1\n', None)):
      self.raw('LongitudinalManeuverMode', raw)
      self.assertFalse(owner.update(2_000_000_000 + index * 250_000_000))
    self.raw('LongitudinalManeuverMode', b'1')
    self.assertTrue(owner.update(3_000_000_000))
    self.params.put_bool('DisableOpenpilotLongitudinal', True, block=True)
    self.assertFalse(owner.update(3_250_000_000))
    self.assertFalse(owner.update(3_200_000_000))
    self.assertEqual(cp.safetyConfigs[0].safetyParam, 0xE200)
    self.params.put_bool('DisableOpenpilotLongitudinal', False, block=True)
    self.assertFalse(CameraPedalPreference(params(), self.params, maneuver=True).update(4_000_000_000))

  def test_camera_interceptor_live_preferences_withdraw_without_mutating_owner(self):
    from opendbc.car.gm.tests.test_camera_acc_pedal import params
    from openpilot.starpilot.car.gm.camera import CameraPedalPreference
    cp = params()
    for key in ('SafeMode', 'DisableOpenpilotLongitudinal', 'OpenpilotEnabledToggle'):
      for raw in (None, b'0', b'1', b'bad'):
        for clean_key, clean in (('SafeMode', b'0'), ('DisableOpenpilotLongitudinal', b'0'), ('OpenpilotEnabledToggle', b'1')):
          self.raw(clean_key, clean)
        owner = CameraPedalPreference(cp, self.params)
        self.assertTrue(owner.update(1_000_000_000))
        self.raw(key, raw)
        expected = raw == b'1' if key == 'OpenpilotEnabledToggle' else raw in (None, b'0')
        self.assertEqual(owner.update(1_250_000_000), expected)
        self.assertEqual(cp.safetyConfigs[0].safetyParam, 0xE100)
        self.assertFalse(owner.update(1_200_000_000))

  def test_gm_hold_runtime_withdraws_for_disabled_or_malformed_preferences(self):
    from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
    from opendbc.car.gm.values import CAR as GM_CAR
    from openpilot.starpilot.car.gm.auto_hold import AutoHoldPreference
    for preference in ("GMAutoHold", "VoltOnePedalMode"):
      cp = ordinary_params(GM_CAR.CHEVROLET_VOLT, radar=True)
      VehicleStartupPreferences(gm_auto_hold=preference == "GMAutoHold",
                                volt_one_pedal=preference == "VoltOnePedalMode").prepare(cp)
      now = 1_000_000_000
      for key in (preference, "OpenpilotEnabledToggle", "SafeMode", "DisableOpenpilotLongitudinal"):
        for raw in (b"0", b"1", b"", b"true", None):
          for clean_key, value in ((preference, b"1"), ("OpenpilotEnabledToggle", b"1"),
                                   ("SafeMode", b"0"), ("DisableOpenpilotLongitudinal", b"0")):
            self.raw(clean_key, value)
          owner = AutoHoldPreference(cp, self.params, preference)
          self.assertTrue(owner.update(now))
          self.raw(key, raw)
          now += 250_000_000
          expected = raw == b"1" if key in (preference, "OpenpilotEnabledToggle") else raw in (None, b"0")
          self.assertEqual(owner.update(now), expected, (preference, key, raw))
          self.assertFalse(owner.update(now - 1))
          cp.passive = True
          self.assertFalse(owner.update(now))
          cp.passive = False
      preferences = VehicleStartupPreferences(gm_auto_hold=True, volt_one_pedal=True, disable_bolt_long=True)
      preferences.prepare(cp)
      preferences.finalize(cp)
      self.assertFalse(cp.openpilotLongitudinalControl)
      self.assertEqual(cp.safetyConfigs[0].safetyParam, 0x4004)

  def test_suburban_saved_long_pitch_configures_exact_controller(self):
    from opendbc.car.gm.carcontroller import CarController
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import CAR as GM_CAR, DBC
    fingerprint = gen_empty_fingerprint()
    fingerprint[1][0x460] = 8
    cp = CarInterface.get_params(GM_CAR.CHEVROLET_SUBURBAN, fingerprint, [], False, False, False)
    for requested in (False, True):
      self.raw("LongPitch", b"1" if requested else b"0")
      preferences = VehicleStartupPreferences.read(self.params, enabled=True)
      controller = CarController(DBC[cp.carFingerprint], cp)
      preferences.configure_controller(SimpleNamespace(CP=cp, CC=controller))
      self.assertEqual(controller.long_pitch, requested)

  def test_only_exact_saved_opt_in_is_loaded(self):
    for raw in (None, b"0", b"", b"true", b"1\n", b"1" * 20, b"1"):
      with self.subTest(raw=raw):
        self.raw("ToyotaAutoHold", raw)
        preferences = VehicleStartupPreferences.read(self.params, enabled=True)
        self.assertEqual(preferences.toyota_auto_hold, raw == b"1")
    self.assertFalse(VehicleStartupPreferences.read(self.params, enabled=False).toyota_auto_hold)
    for safe in (b"1", b"invalid", b"", b"0", None):
      with self.subTest(safe=safe):
        self.raw("SafeMode", safe)
        self.assertEqual(VehicleStartupPreferences.read(self.params, enabled=True).toyota_auto_hold, safe in (None, b"0"))
    self.raw("SafeMode", None)
    path = Path(self.params.get_param_path("ToyotaAutoHold"))
    path.unlink()
    path.symlink_to("SafeMode")
    self.assertFalse(VehicleStartupPreferences.read(self.params, enabled=True).toyota_auto_hold)

  def start(self, candidate, *, enabled=True, requested=True, change_saved=False, key="ToyotaAutoHold", observed=None,
            capture=None, car_fw=None, after_construct=None):
    self.raw(key, b"1" if requested else b"0")
    self.params.put_bool("OpenpilotEnabledToggle", enabled, block=True)
    snapshots = []

    def fingerprint(*args, **kwargs):
      return candidate, observed if observed is not None else gen_empty_fingerprint(), "0" * 17, car_fw or [], structs.CarParams.FingerprintSource.can, True

    def create(*args, **kwargs):
      ci = car_helpers.get_car(*args, **kwargs)
      snapshots.append(capture(ci) if capture is not None else (bool(ci.CP.flags & ToyotaFlags.AUTO_BRAKE_HOLD), int(ci.CP.alternativeExperience)))
      if after_construct is not None:
        after_construct(ci)
      if change_saved:
        self.raw(key, b"0")
      return ci

    with patch.object(card, "Params", return_value=self.params), \
         patch.object(card, "feature_requested", return_value=False), \
         patch.object(card.messaging, "sub_sock", return_value=Mock()), \
         patch.object(card.messaging, "SubMaster", return_value=Mock(ignore_alive=[], ignore_valid=[], ignore_average_freq=[])), \
         patch.object(card.messaging, "PubMaster", return_value=SimpleNamespace(sock={"sendcan": Mock()})), \
         patch.object(card.messaging, "recv_one_retry", return_value=SimpleNamespace(can=[object()])), \
         patch.object(card, "Ratekeeper", return_value=Mock()), \
         patch.object(card, "get_cache", return_value=None), \
         patch.object(card, "put_cache"), \
         patch.object(card, "get_car", side_effect=create), \
         patch.object(car_helpers, "fingerprint", side_effect=fingerprint):
      host = card.Car()
    with structs.CarParams.from_bytes(self.params.get("CarParams")) as cp:
      published = cp.as_builder()
    return host, snapshots, published

  def test_gm_hold_card_publishes_admitted_configuration_and_honors_live_opt_out(self):
    from opendbc.car.gm.values import CAR as GM_CAR, is_gm_auto_hold
    for candidate, accelerator, radar in ((GM_CAR.CHEVROLET_VOLT, False, 0x460), (GM_CAR.CHEVROLET_VOLT, True, 0x460),
                                           (GM_CAR.BUICK_LACROSSE, True, 0x460), (GM_CAR.BUICK_LACROSSE, True, 0x420)):
      observed = gen_empty_fingerprint()
      observed[1][radar] = 8
      if accelerator:
        observed[0][0xBE] = 6
      base = (0x4004 if accelerator else 0xC004) if candidate == GM_CAR.CHEVROLET_VOLT else 0
      for enabled, requested, change_saved in ((True, True, False), (True, True, True), (True, False, False), (False, True, False)):
        with self.subTest(candidate=candidate, radar=radar, accelerator=accelerator, enabled=enabled, requested=requested, change_saved=change_saved):
          host, constructed, published = self.start(candidate, key="GMAutoHold", observed=observed,
            capture=lambda ci: int(ci.CS.CP.safetyConfigs[0].safetyParam), enabled=enabled,
            requested=requested, change_saved=change_saved)
          admitted = enabled and requested
          self.assertEqual(constructed, [base | (0x80 if admitted else 0)])
          self.assertEqual(is_gm_auto_hold(published), admitted)
          if enabled:
            self.assertEqual(published.safetyConfigs[0].safetyParam, constructed[0])
            self.assertEqual(host.CI.CC.gm_auto_hold, requested)
            if requested:
              self.assertEqual(host.CI.CC.gm_auto_hold_input.update(1_000_000_000), not change_saved)
            else:
              self.assertIsNone(host.CI.CC.gm_auto_hold_input)
          else:
            self.assertEqual(published.safetyConfigs[0].safetyModel, structs.CarParams.SafetyModel.noOutput)

  def test_volt_one_pedal_card_retains_exact_owner_and_withdrawal(self):
    from opendbc.car.gm.values import CAR as GM_CAR, is_volt_one_pedal, gm_control_word
    profiles = [
      (GM_CAR.CHEVROLET_VOLT, {0xBE: 6}, {0x460: 8}, {}, 0xD100),
      (GM_CAR.CHEVROLET_VOLT, {}, {0x460: 8}, {}, 0xD101),
      (GM_CAR.CHEVROLET_VOLT_ASCM, {0x2FF: 8, 0xBE: 6}, {}, {}, 0xD102),
      (GM_CAR.CHEVROLET_VOLT_ASCM, {0x2FF: 8}, {}, {}, 0xD103),
      (GM_CAR.CHEVROLET_VOLT_ASCM, {0x2FF: 8, 0xBE: 6}, {0x460: 8}, {}, 0xD104),
      (GM_CAR.CHEVROLET_VOLT_ASCM, {0x2FF: 8}, {0x460: 8}, {}, 0xD105),
      (GM_CAR.CHEVROLET_VOLT_CAMERA, {}, {}, {0x320: 6}, 0xD106),
      (GM_CAR.CHEVROLET_VOLT_2019, {0x2FF: 8, 0xBE: 6}, {}, {}, 0xD107),
      (GM_CAR.CHEVROLET_VOLT_2019, {0x2FF: 8}, {}, {}, 0xD108),
    ]
    for alternate in (False, True):
      observed = {0x184: 8, 0x34A: 5, 0x1C4: 8, 0xC9: 8, 0x1E1: 7, 0xF1 if alternate else 0xBE: 6}
      profiles.append((GM_CAR.CHEVROLET_VOLT_CAMERA, observed, {}, {}, 0xD109 + alternate))
    for candidate, pt, radar, camera, solo_word in profiles:
      for paired in (False, True):
        for state in ('on', 'off', 'live_off', 'master_off', 'safe', 'disable_long', 'release'):
          with self.subTest(candidate=candidate, word=hex(solo_word), paired=paired, state=state):
            observed = gen_empty_fingerprint()
            observed[0], observed[1], observed[2] = dict(pt), dict(radar), dict(camera)
            self.params.put_bool('AlphaLongitudinalEnabled', True, block=True)
            self.params.put_bool('GMAutoHold', paired, block=True)
            self.params.put_bool('SafeMode', state == 'safe', block=True)
            self.params.put_bool('DisableOpenpilotLongitudinal', state == 'disable_long', block=True)
            self.params.put_bool('IsReleaseBranch', state == 'release', block=True)
            host, constructed, published = self.start(candidate, key='VoltOnePedalMode', observed=observed,
              capture=lambda ci: int(ci.CS.CP.safetyConfigs[0].safetyParam), enabled=state != 'master_off',
              requested=state != 'off', change_saved=state == 'live_off')
            admitted = state in ('on', 'live_off') or state == 'release' and candidate == GM_CAR.CHEVROLET_VOLT
            self.assertEqual(is_volt_one_pedal(published), admitted)
            if admitted:
              self.assertEqual(constructed, [solo_word + (0x10 if paired else 0)])
              self.assertEqual(published.safetyConfigs[0].safetyParam, constructed[0])
              self.assertTrue(host.CI.CC.volt_one_pedal)
              self.assertEqual(host.CI.CC.volt_one_pedal_input.update(1_000_000_000), state != 'live_off')
              self.assertEqual(host.CI.CC.gm_auto_hold, paired)
            else:
              self.assertFalse(host.CI.CC.volt_one_pedal)
              self.assertIsNone(host.CI.CC.volt_one_pedal_input)
              self.assertEqual(gm_control_word(host.CI.CP), host.CI.CP.safetyConfigs[0].safetyParam)
            if state == 'master_off':
              self.assertEqual(published.safetyConfigs[0].safetyModel, structs.CarParams.SafetyModel.noOutput)
            if state == 'disable_long':
              self.assertFalse(published.openpilotLongitudinalControl)
            self.assertEqual(self.params.get_bool('VoltOnePedalMode'), state not in ('off', 'live_off'))

  def test_camera_volt_hold_startup_retains_release_and_disable_longitudinal_owners(self):
    from opendbc.car.gm.values import CAR as GM_CAR, is_gm_auto_hold
    for candidate in (GM_CAR.CHEVROLET_VOLT_ASCM, GM_CAR.CHEVROLET_VOLT_CAMERA, GM_CAR.CHEVROLET_VOLT_2019):
      for radar in (False, True):
        for brake_c9 in ((True,) if candidate == GM_CAR.CHEVROLET_VOLT_CAMERA else (False, True)):
          observed = gen_empty_fingerprint()
          if candidate == GM_CAR.CHEVROLET_VOLT_ASCM:
            observed[0][0x2FF] = 8
            stock_word = 0x205 | (0x400 if brake_c9 else 0) | (0x800 if radar else 0)
          elif candidate == GM_CAR.CHEVROLET_VOLT_2019:
            observed[0][0x2FF] = 8
            stock_word = 0x1005 | (0x400 if brake_c9 else 0)
          else:
            observed[2][0x320] = 6
            stock_word = 5
          if not brake_c9:
            observed[0][0xBE] = 6
          if radar:
            observed[1][0x460] = 8
          startup_choices = [(False, False, True), (True, False, True), (False, True, True)]
          if candidate == GM_CAR.CHEVROLET_VOLT_2019:
            startup_choices.append((False, False, False))
          for release, disable, sascm in startup_choices:
            if not sascm:
              observed[0].pop(0x2FF)
            with self.subTest(candidate=candidate, radar=radar, brake_c9=brake_c9, release=release, disable=disable, sascm=sascm):
              self.params.put_bool("IsReleaseBranch", release, block=True)
              self.params.put_bool("AlphaLongitudinalEnabled", True, block=True)
              self.params.put_bool("DisableOpenpilotLongitudinal", disable, block=True)
              host, constructed, published = self.start(candidate, key="GMAutoHold", observed=observed,
                capture=lambda ci: int(ci.CS.CP.safetyConfigs[0].safetyParam))
              admitted = not release and not disable and sascm
              self.assertEqual(constructed, [stock_word | (0x4082 if admitted else 0)])
              self.assertEqual(published.safetyConfigs[0].safetyParam, constructed[0])
              self.assertEqual(is_gm_auto_hold(published), admitted)
              self.assertEqual(published.openpilotLongitudinalControl, admitted)
              self.assertEqual(published.pcmCruise, not admitted)
              self.assertEqual(host.CI.CC.gm_auto_hold, admitted)
              self.assertEqual(host.CI.CC.gm_auto_hold_input is not None, admitted)
              self.assertTrue(self.params.get_bool("GMAutoHold"))
              self.assertTrue(self.params.get_bool("AlphaLongitudinalEnabled"))

  def test_removed_volt_and_lacrosse_hold_withdraw_before_interface_construction(self):
    from opendbc.car.gm.values import CAR as GM_CAR, is_gm_auto_hold
    for candidate, alternate in ((GM_CAR.CHEVROLET_VOLT_CAMERA, False), (GM_CAR.CHEVROLET_VOLT_CAMERA, True),
                                 (GM_CAR.BUICK_LACROSSE, False)):
      removed = candidate == GM_CAR.CHEVROLET_VOLT_CAMERA
      observed = gen_empty_fingerprint()
      observed[0] = {0x184: 8, 0x34A: 5, 0x1C4: 8, 0xC9: 8, 0x1E1: 7, 0xF1 if alternate else 0xBE: 6}
      observed[1][0x460] = 8
      for release in (False, True):
        for disable in (False, True):
          with self.subTest(candidate=candidate, alternate=alternate, release=release, disable=disable):
            self.params.put_bool("IsReleaseBranch", release, block=True)
            self.params.put_bool("AlphaLongitudinalEnabled", True, block=True)
            self.params.put_bool("DisableOpenpilotLongitudinal", disable, block=True)
            host, constructed, published = self.start(candidate, key="GMAutoHold", observed=observed,
              capture=lambda ci: int(ci.CS.CP.safetyConfigs[0].safetyParam))
            admitted = not disable and not (removed and release)
            expected = ((0xC1D3 if alternate else 0xC1D1) if admitted else 0xC150) if removed else (0x80 if admitted else 0)
            self.assertEqual(constructed, [expected])
            self.assertEqual(published.safetyConfigs[0].safetyParam, expected)
            self.assertEqual(published.openpilotLongitudinalControl, admitted)
            self.assertEqual(published.pcmCruise, removed and not admitted)
            self.assertEqual(is_gm_auto_hold(published), admitted)
            self.assertEqual(host.CI.CC.gm_auto_hold, admitted)
            self.assertEqual(host.CI.CC.gm_auto_hold_input is not None, admitted)
            self.assertTrue(self.params.get_bool("GMAutoHold"))

  def test_real_card_admits_before_construction_and_publishes_final_permission(self):
    for candidate, permission in ((CAR.TOYOTA_COROLLA_TSS2, ALTERNATIVE_EXPERIENCE.TOYOTA_AUTO_HOLD),
                                  (CAR.TOYOTA_CAMRY_TSS2, ALTERNATIVE_EXPERIENCE.TOYOTA_AEB_HOLD)):
      with self.subTest(candidate=candidate):
        host, constructed, published = self.start(candidate, change_saved=True)
        self.assertEqual(constructed, [(True, permission)])
        self.assertTrue(host.CI.CS.CP.flags & ToyotaFlags.AUTO_BRAKE_HOLD)
        self.assertEqual(published.alternativeExperience & ~32, permission)
        self.assertEqual(published.alternativeExperience & 32, 32)
        self.assertTrue(published.flags & ToyotaFlags.AUTO_BRAKE_HOLD)
        self.assertEqual(self.params.get("ToyotaAutoHold"), False)

  def test_default_disabled_and_master_off_never_admit_hold(self):
    for enabled, requested in ((True, False), (False, True)):
      with self.subTest(enabled=enabled, requested=requested):
        _, constructed, published = self.start(CAR.TOYOTA_CAMRY_TSS2, enabled=enabled, requested=requested)
        self.assertEqual(constructed, [(False, 0)])
        self.assertEqual(published.alternativeExperience & ~32, 0)
        self.assertEqual(published.alternativeExperience & 32, 32 if enabled else 0)
        self.assertFalse(published.flags & ToyotaFlags.AUTO_BRAKE_HOLD)
        if not enabled:
          self.assertTrue(published.passive)
          self.assertEqual(published.safetyConfigs[0].safetyModel, structs.CarParams.SafetyModel.noOutput)

  def test_finalize_cannot_late_enable_unprepared_parser(self):
    cp = car_helpers.interfaces[CAR.TOYOTA_CAMRY_TSS2].get_non_essential_params(CAR.TOYOTA_CAMRY_TSS2)
    preferences = VehicleStartupPreferences(True)
    preferences.finalize(cp)
    self.assertFalse(cp.flags & ToyotaFlags.AUTO_BRAKE_HOLD)
    self.assertEqual(cp.alternativeExperience, 0)

  def test_unrelated_vehicle_flags_are_untouched(self):
    cp = SimpleNamespace(brand="other", flags=4096, alternativeExperience=256)
    preferences = VehicleStartupPreferences(True)
    self.assertIs(preferences.prepare(cp), cp)
    preferences.finalize(cp)
    self.assertEqual((cp.flags, cp.alternativeExperience), (4096, 256))
