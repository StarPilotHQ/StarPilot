"""Disposable Params and real shared-owner conditional editor requests."""
from unittest.mock import Mock
from openpilot.starpilot.ui.presentation import BitmapFonts


from pathlib import Path
import json
from dataclasses import replace
from types import SimpleNamespace
import subprocess
import sys
import os
import tempfile
import unittest
import warnings
from unittest.mock import patch

from openpilot.common.params import Params
from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.starpilot.conditional_mode.button_actions import MEDIA_KEYS
from openpilot.starpilot.ui.wheel_feature import PREFIX as BUTTON_PREFIX
from openpilot.starpilot.conditional_mode.preferences import MPH_TO_MPS, SavedPreferences, decode_preferences, encode_preferences
from openpilot.starpilot.conditional_mode.actions import commit, commit_manual, commit_mode, stock_document
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner, read_mode_sources
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.manual_saved import KEY as MANUAL_KEY, SavedCodes, decode as decode_manual, encode as encode_manual
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureInput, FeaturePage, FeatureRow, FeatureSettingsRequest, row_change
from openpilot.starpilot.ui import feature_settings_compact as compact
from openpilot.starpilot.ui import feature_settings as large
from openpilot.starpilot.ui.presentation import Profile


def required_change(row: FeatureRow, direction: int = 1) -> FeatureSettingsRequest:
  request = row_change(row, direction)
  assert request is not None
  return request


class ConditionalFeatureTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.parked = True
    from opendbc.car.honda.values import CAR as HONDA
    self.cp = interfaces[HONDA.HONDA_CIVIC_BOSCH].get_non_essential_params(HONDA.HONDA_CIVIC_BOSCH)
    self.cp.openpilotLongitudinalControl = True
    self.cp.carVin = "VIN1"
    self.owner = FeatureSettingsOwner(self.params, lambda group: self.parked and group in
                                      ("conditional", "preferences", "parked_preferences"),
                                      vehicle_fingerprint=lambda: getattr(self.cp, "carFingerprint", None),
                                      vehicle_params=lambda: self.cp)

  def test_three_saved_modes_round_trip_through_actual_baseline_owner(self):
    original = SavedPreferences(cem=replace(SavedPreferences().cem, speed_mps=13.0))
    self.params.put("ConditionalModeConfig", json.loads(encode_preferences(original)), block=True)
    manual = encode_manual(SavedCodes())
    Path(self.params.get_param_path(MANUAL_KEY)).write_bytes(manual)
    drive = SelfdriveD.__new__(SelfdriveD)
    drive.params, drive.CP = self.params, self.cp
    drive.conditional_settings = ConditionalSettingsOwner(self.params)
    drive.conditional_replay = False
    now = 1_000_000_000
    for value, choice, baseline in (("Experimental", ModeChoice.STOCK, True), ("Chill", ModeChoice.STOCK, False),
                                    ("Automatic", ModeChoice.CEM, False)):
      row = self.row("conditional:mode")
      self.assertEqual(row.choices, ("Automatic", "Chill", "Experimental"))
      self.assertTrue(self.owner.apply(replace(required_change(row), value=value)))
      self.assertEqual(self.row("conditional:mode").value, value)
      saved = decode_preferences(Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes())
      self.assertEqual(saved.mode, choice)
      self.assertEqual(saved.cem, original.cem)
      self.assertEqual(saved.ccm, original.ccm)
      self.assertEqual(Path(self.params.get_param_path(MANUAL_KEY)).read_bytes(), manual)
      drive.refresh_saved_driving_mode(now)
      drive.update_conditional_mode(None)
      self.assertEqual(drive.experimental_mode, baseline)
      now += 100_000_000
    self.assertTrue(self.owner.apply(replace(required_change(self.row("conditional:mode")), value="Experimental")))
    self.params.put_bool("SafeMode", True, block=True)
    drive.refresh_saved_driving_mode(now)
    drive.update_conditional_mode(None)
    self.assertFalse(drive.experimental_mode)

  def test_actual_firmware_data_round_trip_and_changed_firmware_rejects_request(self):
    from opendbc.car.honda.fingerprints import FW_VERSIONS
    from opendbc.car.honda.values import CAR as HONDA
    (ecu, address, subaddress), versions = next(iter(FW_VERSIONS[HONDA.HONDA_CIVIC_BOSCH].items()))
    firmware = self.cp.init("carFw", 1)[0]
    firmware.ecu, firmware.address, firmware.subAddress = ecu, address, subaddress or 0
    firmware.fwVersion, firmware.brand = versions[0], "honda"
    self.assertIsInstance(self.cp.to_dict()["carFw"][0]["fwVersion"], bytes)
    row = self.row("conditional:mode")
    self.assertIsNotNone(row.capability)
    for malformed in ((), (self.cp.carFingerprint,), (self.cp.carFingerprint, "invalid")):
      self.assertFalse(self.owner.apply(replace(required_change(row), value="Experimental", capability=malformed)))
    self.assertTrue(self.owner.apply(replace(required_change(row), value="Experimental")))
    self.assertEqual(self.row("conditional:mode").value, "Experimental")
    self.assertTrue(self.owner.apply(replace(required_change(self.row("conditional:mode")), value="Chill")))
    request = replace(required_change(self.row("conditional:mode")), value="Experimental")
    firmware.fwVersion = versions[0] + b"changed"
    self.assertFalse(self.owner.apply(request))
    self.assertFalse(self.params.get_bool("ExperimentalMode"))
    # Hashing copies the full CP instead of marking the live builder serialized.
    with warnings.catch_warnings():
      warnings.simplefilter("error", UserWarning)
      self.cp.to_bytes()

  def test_mode_request_rejects_changed_baseline_safe_mode_and_final_cp(self):
    cp = self.cp
    assert cp is not None and not isinstance(cp, SimpleNamespace)
    for change in ("baseline", "safe", "cp"):
      self.params.put_bool("ExperimentalMode", False, block=True)
      self.params.put_bool("SafeMode", False, block=True)
      cp.carVin = "VIN1"
      request = replace(required_change(self.row("conditional:mode")), value="Experimental")
      if change == "baseline":
        self.params.put_bool("ExperimentalMode", True, block=True)
      elif change == "safe":
        self.params.put_bool("SafeMode", True, block=True)
      else:
        cp.carVin = "VIN2"
      before = self.params.get("ConditionalModeConfig")
      self.assertFalse(self.owner.apply(request), change)
      self.assertEqual(self.params.get("ConditionalModeConfig"), before)
    self.params.put_bool("SafeMode", False, block=True)
    cp.openpilotLongitudinalControl = False
    row = self.row("conditional:mode")
    self.assertNotIn("Experimental", row.choices)
    self.assertFalse(self.owner.apply(replace(required_change(row), value="Experimental")))
    Path(self.params.get_param_path("ExperimentalMode")).write_bytes(b"01")
    self.assertFalse(self.row("conditional:mode").available)
    self.assertEqual(Path(self.params.get_param_path("ExperimentalMode")).read_bytes(), b"01")

  def test_mode_second_write_failure_restores_both_sources_and_reports_rollback_failure(self):
    self.params.put_bool("ExperimentalMode", False, block=True)
    old = self.params.get("ConditionalModeConfig")
    baseline_path = Path(self.params.get_param_path("ExperimentalMode"))
    original_replace = os.replace
    attempts = 0

    def fail_second(source, destination):
      nonlocal attempts
      attempts += 1
      if attempts == 2:
        self.assertEqual(read_mode_sources(self.params), ((b"", False), (b"", False), (b"", False)))
        raise OSError("second replacement failed")
      return original_replace(source, destination)

    with patch("openpilot.starpilot.conditional_mode.actions.os.replace", side_effect=fail_second):
      result = commit_mode(self.params, stock_document(), b"1", expected=old, expected_baseline=b"0",
                           expected_units=None, expected_safe=None, authorized=lambda: True)
    self.assertFalse(result.committed)
    self.assertFalse(result.verified)
    self.assertFalse(result.rollback_failed)
    self.assertEqual(self.params.get("ConditionalModeConfig"), old)
    self.assertEqual(baseline_path.read_bytes(), b"0")
    attempts = 0

    def fail_write_and_rollback(source, destination):
      nonlocal attempts
      attempts += 1
      if attempts >= 2:
        raise OSError("replacement and restoration failed")
      return original_replace(source, destination)

    self.params.put("ConditionalModeConfig", json.loads(encode_preferences(SavedPreferences())), block=True)
    old = Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes()
    with patch("openpilot.starpilot.conditional_mode.actions.os.replace", side_effect=fail_write_and_rollback):
      result = commit_mode(self.params, stock_document(), b"1", expected=old, expected_baseline=b"0",
                           expected_units=None, expected_safe=None, authorized=lambda: True)
    self.assertFalse(result.committed)
    self.assertFalse(result.verified)
    self.assertTrue(result.rollback_failed)

  def test_saved_mode_and_conditions_can_change_during_drive_with_exact_source(self):
    self.parked = False
    self.owner.authority = lambda group: group == "preferences"
    mode = self.row("conditional:mode")
    self.assertTrue(mode.available)
    self.assertTrue(self.owner.apply(required_change(mode)))
    threshold = self.row("conditional:cem:speed_mps", FeaturePage.CONDITIONAL_CEM)
    self.assertTrue(threshold.available)
    request = required_change(threshold)
    self.params.put_bool("IsMetric", True, block=True)
    self.assertFalse(self.owner.apply(request))

  def test_repair_and_manual_choice_reset_stay_parked(self):
    self.parked = False
    self.owner.authority = lambda group: group == "preferences"
    persist = self.row("conditional:cem:persist_manual", FeaturePage.CONDITIONAL_CEM)
    self.assertFalse(persist.available)
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(persist.key, persist.source, "On",
                                                             dependencies=persist.dependencies)))
    invalid = b'{"version":2}'
    Path(self.params.get_param_path("ConditionalModeConfig")).write_bytes(invalid)
    reset = self.row("conditional:reset")
    self.assertFalse(reset.available)
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(reset.key, reset.source, "confirm", confirmation=True,
                                                             dependencies=reset.dependencies)))
    self.assertEqual(Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes(), invalid)

  def test_supported_wheel_assignment_can_change_during_drive_but_rechecks_sources(self):
    cp = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
    cp.flags = int(cp.flags | HyundaiFlags.CANFD_LKA_STEER_MSG)
    cp.openpilotLongitudinalControl = True
    self.cp = cp
    self.parked = False
    self.owner.authority = lambda group: group == "preferences"
    row = self.row(BUTTON_PREFIX + MEDIA_KEYS[0])
    self.assertTrue(row.available)
    request = required_change(row)
    self.assertTrue(self.owner.apply(request))
    self.assertFalse(self.owner.apply(request))
    other = required_change(self.row(BUTTON_PREFIX + MEDIA_KEYS[1]))
    cp.flags = int(cp.flags & ~HyundaiFlags.CANFD_LKA_STEER_MSG)
    self.assertFalse(self.owner.apply(other))

  def rows(self, page=FeaturePage.CONDITIONAL):
    return self.owner.snapshot(page, parked=self.parked, system_long=False,
                               lateral_context=False, metric=False).rows

  def row(self, key, page=FeaturePage.CONDITIONAL):
    return next(row for row in self.rows(FeaturePage.WHEEL if key.startswith(BUTTON_PREFIX) else page) if row.key == key)

  def test_factory_cem_and_honda_pcm_cruise_does_not_veto(self):
    self.assertIsNone(self.params.get("ConditionalModeConfig"))
    self.assertIn(FeaturePage.CONDITIONAL, [row.page for row in self.rows(FeaturePage.HUB)])
    mode = self.row("conditional:mode")
    self.assertEqual(mode.value, "Automatic")
    self.assertTrue(mode.available)
    self.assertTrue(self.owner.apply(required_change(mode)))
    saved = decode_preferences(Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes())
    self.assertEqual(saved.mode.value, "stock")
    self.assertFalse(saved.cem.persist_manual)

  def test_retired_chill_mode_uses_automatic_and_has_no_editable_menu(self):
    from openpilot.starpilot.ui.conditional_status import configured_choice
    path = Path(self.params.get_param_path("ConditionalModeConfig"))
    raw = encode_preferences(replace(SavedPreferences(), mode=ModeChoice.CCM))
    path.write_bytes(raw)
    self.assertEqual(self.row("conditional:mode").value, "Automatic")
    self.assertEqual(configured_choice(self.params), ModeChoice.CEM)
    settings = ConditionalSettingsOwner(self.params)
    snapshot = settings.refresh(1_000_000_000)
    verdict = settings.verdict(snapshot, now_mono_ns=1_000_000_000, drive_id=1)
    assert verdict.selection is not None
    self.assertEqual(verdict.selection.choice, ModeChoice.CEM)
    self.assertFalse(any(row.page == FeaturePage.CONDITIONAL_CCM for row in self.rows()))
    self.assertEqual(self.rows(FeaturePage.CONDITIONAL_CCM), ())
    retired = FeatureSettingsRequest("conditional:ccm:launch_assist", raw, "On", confirmation=True)
    self.assertFalse(self.owner.apply(retired))
    self.assertEqual(path.read_bytes(), raw)

  def test_chill_label_and_signal_rows_preserve_existing_consumers(self):
    path = Path(self.params.get_param_path("ConditionalModeConfig"))
    path.write_bytes(stock_document())
    original = path.read_bytes()
    mode = self.row("conditional:mode")
    self.assertEqual(mode.value, "Chill")
    self.assertNotIn("Stock", mode.choices)
    self.assertEqual(path.read_bytes(), original)
    self.assertFalse(any(row.key.endswith((":signal_lane_detection", ":signal_lane_width_m"))
                         for row in self.rows(FeaturePage.CONDITIONAL_CEM)))
    row = self.row("conditional:cem:signal_lane_detection", FeaturePage.LANE_CHANGE)
    self.assertIn("requests Experimental", row.reason)
    self.assertTrue(self.owner.apply(required_change(row)))
    saved = decode_preferences(path.read_bytes())
    self.assertEqual(saved.mode, ModeChoice.STOCK)
    self.assertNotEqual(saved.cem.signal_lane_detection, decode_preferences(original).cem.signal_lane_detection)
    mode = self.row("conditional:mode")
    self.assertTrue(self.owner.apply(replace(required_change(mode), value="Chill")))
    self.assertEqual(decode_preferences(path.read_bytes()).mode, ModeChoice.STOCK)

  def test_core_configuration_without_car_params_keeps_media_vehicle_bound(self):
    self.cp = None
    mode = self.row("conditional:mode")
    self.assertTrue(mode.available)
    self.assertIsNone(mode.vehicle_fingerprint)
    self.assertTrue(self.owner.apply(required_change(mode)))
    self.assertFalse(any(row.key.startswith(BUTTON_PREFIX) for row in self.rows(FeaturePage.WHEEL)))
    self.cp = SimpleNamespace(carFingerprint="UNSUPPORTED", openpilotLongitudinalControl=False,
                              pcmCruise=True, notCar=False, dashcamOnly=False, passive=False)
    threshold = self.row("conditional:cem:speed_mps", FeaturePage.CONDITIONAL_CEM)
    self.assertTrue(threshold.available)
    self.assertTrue(self.owner.apply(required_change(threshold)))
    pending = required_change(self.row("conditional:cem:open_road", FeaturePage.CONDITIONAL_CEM))
    self.parked = False
    self.assertFalse(self.owner.apply(pending))

  def test_six_ioniq_media_rows_and_exact_source_guards(self):
    cp = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
    cp.flags = int(cp.flags | HyundaiFlags.CANFD_LKA_STEER_MSG)
    cp.openpilotLongitudinalControl = True
    self.cp = cp
    rows = [self.row(BUTTON_PREFIX + key) for key in MEDIA_KEYS]
    self.assertTrue(all(row.available and row.value == "Off" for row in rows))
    first = rows[0]
    request = required_change(first)
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.parked = True
    self.params.put(MEDIA_KEYS[0], 5, block=True)
    self.assertFalse(self.owner.apply(request))
    row = self.row(first.key)
    self.assertEqual(row.value, "Experimental override")
    self.assertTrue(self.owner.apply(required_change(row)))
    self.assertEqual(self.params.get(MEDIA_KEYS[0]), 6)
    self.cp = None
    self.assertFalse(any(row.key.startswith(BUTTON_PREFIX) for row in self.rows(FeaturePage.WHEEL)))

  def test_captured_wheel_request_stays_vehicle_bound_with_generic_preferences(self):
    cp = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
    cp.flags = int(cp.flags | HyundaiFlags.CANFD_LKA_STEER_MSG)
    cp.openpilotLongitudinalControl = True
    self.cp = cp
    request = required_change(self.row(BUTTON_PREFIX + MEDIA_KEYS[0]))
    destination = Path(self.params.get_param_path(MEDIA_KEYS[0]))
    for replacement in (None, interfaces["HONDA_CIVIC_BOSCH"].get_non_essential_params("HONDA_CIVIC_BOSCH")):
      self.cp = replacement
      self.assertTrue(self.parked)
      self.assertFalse(self.owner.apply(request))
      self.assertFalse(destination.exists())

  def test_native_authority_uses_system_long_without_pcm_cruise_veto(self):
    from openpilot.starpilot.ui import runtime_app
    shell = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    shell._mode = runtime_app.ShellMode.SETTINGS
    with patch.object(shell, "confirmed_offroad", return_value=True), \
         patch.object(runtime_app.ui_state, "CP", self.cp):
      self.assertTrue(shell._feature_authority("conditional"))
      self.assertTrue(shell._feature_authority("long"))
      assert self.cp is not None
      self.cp.openpilotLongitudinalControl = False
      self.assertFalse(shell._feature_authority("conditional"))
      self.assertFalse(shell._feature_authority("long"))
      self.assertTrue(shell._feature_authority("preferences"))
      self.cp.openpilotLongitudinalControl = True
      for field in ('passive', 'dashcamOnly', 'notCar'):
        with self.subTest(field=field):
          setattr(self.cp, field, True)
          self.assertFalse(shell._feature_authority("conditional"))
          self.assertFalse(shell._feature_authority("long"))
          self.assertTrue(shell._feature_authority("preferences"))
          setattr(self.cp, field, False)

  def test_native_saved_actions_use_settings_mode_and_supported_current_car(self):
    from openpilot.starpilot.ui import runtime_app
    shell = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    shell._mode = runtime_app.ShellMode.SETTINGS
    with patch.object(shell, "confirmed_offroad", return_value=False), \
         patch.object(runtime_app.ui_state, "CP", self.cp):
      self.assertTrue(shell._feature_authority("lane_change"))
      self.assertTrue(shell._feature_authority("conditional_wheel"))
      self.assertTrue(shell._feature_authority("conditional"))
      self.assertTrue(shell._feature_authority("aol"))
      shell._mode = runtime_app.ShellMode.ONROAD
      self.assertFalse(shell._feature_authority("lane_change"))
      self.assertFalse(shell._feature_authority("conditional_wheel"))

  def test_threshold_units_and_exact_source_guard(self):
    speed = self.row("conditional:cem:speed_mps", FeaturePage.CONDITIONAL_CEM)
    self.assertEqual(speed.unit, "mph")
    request = required_change(speed)
    self.params.put_bool("IsMetric", True, block=True)
    self.assertFalse(self.owner.apply(request))
    self.assertIsNone(self.params.get("ConditionalModeConfig"))
    speed = self.row("conditional:cem:speed_mps", FeaturePage.CONDITIONAL_CEM)
    self.assertEqual(speed.unit, "km/h")
    self.assertTrue(self.owner.apply(required_change(speed)))
    saved = decode_preferences(Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes())
    self.assertAlmostEqual(saved.cem.speed_mps, 1 / 3.6)
    stale = required_change(self.row("conditional:cem:open_road", FeaturePage.CONDITIONAL_CEM))
    Path(self.params.get_param_path("ConditionalModeConfig")).write_bytes(b'{"bad":true}')
    self.assertFalse(self.owner.apply(stale))

  def test_bounds_and_manual_persistence_clears_only_selected_code(self):
    row = self.row("conditional:cem:model_stop_s", FeaturePage.CONDITIONAL_CEM)
    forged = FeatureSettingsRequest(row.key, row.source, "100", vehicle_fingerprint=row.vehicle_fingerprint,
                                    capability=row.capability, dependencies=row.dependencies, display_unit=row.display_unit)
    self.assertFalse(self.owner.apply(forged))
    self.assertIsNone(self.params.get("ConditionalModeConfig"))
    manual_path = Path(self.params.get_param_path("ConditionalManualState"))
    manual_path.write_bytes(encode_manual(SavedCodes(cem=2, ccm=1)))
    persist = self.row("conditional:cem:persist_manual", FeaturePage.CONDITIONAL_CEM)
    self.assertEqual(persist.value, "Off")
    self.assertTrue(self.owner.apply(required_change(persist)))
    self.assertTrue(decode_preferences(Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes()).cem.persist_manual)
    self.assertEqual(decode_manual(manual_path.read_bytes()), SavedCodes(cem=0, ccm=1))
    manual_path.write_bytes(encode_manual(SavedCodes(cem=2, ccm=1)))
    self.assertTrue(self.owner.apply(required_change(self.row("conditional:cem:persist_manual", FeaturePage.CONDITIONAL_CEM))))
    self.assertFalse(decode_preferences(Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes()).cem.persist_manual)
    self.assertEqual(decode_manual(manual_path.read_bytes()), SavedCodes(cem=0, ccm=1))

  def test_manual_source_stale_and_partial_clear_cannot_resurrect_override(self):
    from openpilot.starpilot.conditional_mode import actions
    manual_path = Path(self.params.get_param_path("ConditionalManualState"))
    config_path = Path(self.params.get_param_path("ConditionalModeConfig"))
    manual_path.write_bytes(encode_manual(SavedCodes(cem=2, ccm=1)))
    request = required_change(self.row("conditional:cem:persist_manual", FeaturePage.CONDITIONAL_CEM))
    manual_path.write_bytes(encode_manual(SavedCodes(cem=1, ccm=1)))
    self.assertFalse(self.owner.apply(request))
    self.assertFalse(config_path.exists())
    request = required_change(self.row("conditional:cem:persist_manual", FeaturePage.CONDITIONAL_CEM))
    original = actions.os.replace
    def fail_config(source, destination):
      if Path(destination) == config_path:
        raise OSError("simulated config rename failure")
      return original(source, destination)
    with patch.object(actions.os, "replace", side_effect=fail_config):
      self.assertFalse(self.owner.apply(request))
    self.assertEqual(decode_manual(manual_path.read_bytes()), SavedCodes(cem=0, ccm=1))
    self.assertFalse(config_path.exists())

  def test_failed_manual_clear_never_changes_config(self):
    from openpilot.starpilot.conditional_mode import actions
    manual_path = Path(self.params.get_param_path("ConditionalManualState"))
    config_path = Path(self.params.get_param_path("ConditionalModeConfig"))
    manual_path.write_bytes(encode_manual(SavedCodes(cem=2, ccm=1)))
    request = required_change(self.row("conditional:cem:persist_manual", FeaturePage.CONDITIONAL_CEM))
    original = actions.os.replace
    def fail_manual(source, destination):
      if Path(destination) == manual_path:
        raise OSError("simulated manual rename failure")
      return original(source, destination)
    with patch.object(actions.os, "replace", side_effect=fail_manual):
      self.assertFalse(self.owner.apply(request))
    self.assertEqual(decode_manual(manual_path.read_bytes()), SavedCodes(cem=2, ccm=1))
    self.assertFalse(config_path.exists())

  def test_parked_authority_loss_after_clear_leaves_config_off(self):
    from openpilot.starpilot.conditional_mode.preferences import encode_preferences
    manual_path = Path(self.params.get_param_path("ConditionalManualState"))
    config_path = Path(self.params.get_param_path("ConditionalModeConfig"))
    original = encode_manual(SavedCodes(cem=2, ccm=1))
    manual_path.write_bytes(original)
    desired = SavedPreferences()
    desired = replace(desired, cem=replace(desired.cem, persist_manual=True))
    calls = 0
    def authority():
      nonlocal calls
      calls += 1
      return calls < 3
    result = commit_manual(self.params, expected_config=None, expected_units=None, expected_safe=None,
                           expected_manual=original, authorized=authority, choice=ModeChoice.CEM,
                           config_raw=encode_preferences(desired))
    self.assertTrue(result.manual_cleared)
    self.assertFalse(result.committed)
    self.assertEqual(decode_manual(manual_path.read_bytes()), SavedCodes(cem=0, ccm=1))
    self.assertFalse(config_path.exists())

  def test_stock_is_not_a_manual_saved_family(self):
    path = Path(self.params.get_param_path("ConditionalManualState"))
    raw = encode_manual(SavedCodes(cem=2, ccm=1))
    path.write_bytes(raw)
    result = commit_manual(self.params, expected_config=None, expected_units=None, expected_safe=None,
                           expected_manual=raw, authorized=lambda: True, choice=ModeChoice.STOCK,
                           config_raw=stock_document())
    self.assertFalse(result.committed)
    self.assertEqual(path.read_bytes(), raw)
    malformed_commit = Mock(wraps=commit_manual)
    result = malformed_commit(self.params, expected_config=None, expected_units=None, expected_safe=None,
                              expected_manual=raw, authorized=lambda: True, choice="conditional_experimental",
                              config_raw=stock_document())
    malformed_commit.assert_called_once()
    self.assertFalse(result.committed)
    self.assertEqual(path.read_bytes(), raw)

  def test_invalid_manual_bytes_need_explicit_confirmed_reset(self):
    manual_path = Path(self.params.get_param_path("ConditionalManualState"))
    manual_path.write_bytes(b'{"version":9}')
    persist = self.row("conditional:cem:persist_manual", FeaturePage.CONDITIONAL_CEM)
    self.assertFalse(persist.available)
    reset = self.row("conditional:manual_reset")
    request = FeatureSettingsRequest(reset.key, reset.source, "confirm", vehicle_fingerprint=reset.vehicle_fingerprint,
                                     capability=reset.capability, dependencies=reset.dependencies)
    self.assertFalse(self.owner.apply(request))
    self.assertEqual(manual_path.read_bytes(), b'{"version":9}')
    request = replace(request, confirmation=True)
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.parked = True
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(decode_manual(manual_path.read_bytes()), SavedCodes())

  def test_active_manual_writer_lease_defers_persist_toggle(self):
    from openpilot.starpilot.conditional_mode.manual_saved import manual_lock_path
    path = Path(self.params.get_param_path("ConditionalManualState"))
    path.write_bytes(encode_manual(SavedCodes(cem=2, ccm=1)))
    request = required_change(self.row("conditional:cem:persist_manual", FeaturePage.CONDITIONAL_CEM))
    script = "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.read(1)"
    child = subprocess.Popen([sys.executable, "-c", script, str(manual_lock_path(self.params))],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
      self.assertEqual(child.stdout.readline().strip(), "ready")
      self.assertFalse(self.owner.apply(request))
      self.assertEqual(decode_manual(path.read_bytes()), SavedCodes(cem=2, ccm=1))
      self.assertIsNone(self.params.get("ConditionalModeConfig"))
    finally:
      child.communicate(input="x", timeout=5)
    self.assertTrue(self.owner.apply(request))

  def test_safe_mode_source_change_invalidates_pending_toggle(self):
    manual_path = Path(self.params.get_param_path("ConditionalManualState"))
    manual_path.write_bytes(encode_manual(SavedCodes(cem=2, ccm=1)))
    request = required_change(self.row("conditional:cem:persist_manual", FeaturePage.CONDITIONAL_CEM))
    self.params.put_bool("SafeMode", True, block=True)
    self.assertFalse(self.owner.apply(request))
    self.assertEqual(decode_manual(manual_path.read_bytes()), SavedCodes(cem=2, ccm=1))
    self.assertIsNone(self.params.get("ConditionalModeConfig"))

  def test_valid_other_unit_value_can_step_down_without_clamping_on_render(self):
    preferences = SavedPreferences()
    preferences = replace(preferences, cem=replace(preferences.cem, speed_mps=99 * MPH_TO_MPS))
    path = Path(self.params.get_param_path("ConditionalModeConfig"))
    path.write_bytes(encode_preferences(preferences))
    self.params.put_bool("IsMetric", True, block=True)
    row = self.row("conditional:cem:speed_mps", FeaturePage.CONDITIONAL_CEM)
    self.assertGreater(float(row.value), 150.0)
    self.assertEqual(row.maximum, float(row.value))
    self.assertIsNone(row_change(row, 1))
    self.assertTrue(self.owner.apply(required_change(row, -1)))
    self.assertLess(decode_preferences(path.read_bytes()).cem.speed_mps, 99 * MPH_TO_MPS)
    preferences = SavedPreferences()
    preferences = replace(preferences, cem=replace(preferences.cem, signal_lane_width_m=15.0),
                          ccm=replace(preferences.ccm, set_speed_margin_mps=30 / 3.6))
    path.write_bytes(encode_preferences(preferences))
    self.params.put_bool("IsMetric", False, block=True)
    for key, page, old in (("conditional:cem:signal_lane_width_m", FeaturePage.LANE_CHANGE, 15.0),):
      row = self.row(key, page)
      self.assertGreater(float(row.value), 15.0)
      self.assertTrue(self.owner.apply(required_change(row, -1)))
      current = decode_preferences(path.read_bytes())
      section, field = key.split(":")[1:]
      self.assertLess(getattr(getattr(current, section), field), old)

  def test_corrupt_bytes_preserved_until_confirmed_reset(self):
    path = Path(self.params.get_param_path("ConditionalModeConfig"))
    path.write_bytes(b'{"version":2}')
    self.assertFalse(any(row.key == "conditional:mode" for row in self.rows(FeaturePage.WHEEL)))
    reset = self.row("conditional:reset")
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(reset.key, reset.source, "confirm",
                                                            vehicle_fingerprint=reset.vehicle_fingerprint,
                                                            capability=reset.capability, dependencies=reset.dependencies)))
    self.assertEqual(path.read_bytes(), b'{"version":2}')
    request = FeatureSettingsRequest(reset.key, reset.source, "confirm", confirmation=True,
                                     vehicle_fingerprint=reset.vehicle_fingerprint, capability=reset.capability,
                                     dependencies=reset.dependencies)
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.parked = True
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(decode_preferences(path.read_bytes()).mode.value, "stock")

  def test_core_configuration_survives_car_change_and_params_lock_contention(self):
    request = required_change(self.row("conditional:mode"))
    assert self.cp is not None
    self.cp.carVin = "VIN2"
    self.assertTrue(self.owner.apply(request))
    self.cp.carVin = "VIN1"
    Path(self.params.get_param_path("ConditionalModeConfig")).unlink()
    request = required_change(self.row("conditional:mode"))
    root = Path(self.params.get_param_path("ConditionalModeConfig")).parent.parent
    script = "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.read(1)"
    child = subprocess.Popen([sys.executable, "-c", script, str(root / ".lock")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
      self.assertEqual(child.stdout.readline().strip(), "ready")
      self.assertFalse(self.owner.apply(request))
      self.assertIsNone(self.params.get("ConditionalModeConfig"))
    finally:
      child.communicate(input="x", timeout=5)
    self.assertTrue(self.owner.apply(request))

  def test_oversized_document_never_repaired_by_render(self):
    path = Path(self.params.get_param_path("ConditionalModeConfig"))
    path.write_bytes(b'x' * 4097)
    self.assertFalse(any(row.key == "conditional:reset" for row in self.rows(FeaturePage.WHEEL)))
    self.assertEqual(path.read_bytes(), b'x' * 4097)

  def test_nonregular_document_does_not_block_or_offer_repair(self):
    path = Path(self.params.get_param_path("ConditionalModeConfig"))
    os.mkfifo(path)
    self.assertFalse(any(row.key == "conditional:reset" for row in self.rows(FeaturePage.WHEEL)))
    path.unlink()
    outside = path.parent.parent / "outside-conditional"
    outside.write_bytes(b'{"version":2}')
    path.symlink_to(outside)
    self.assertFalse(any(row.key == "conditional:reset" for row in self.rows(FeaturePage.WHEEL)))
    self.assertEqual(outside.read_bytes(), b'{"version":2}')

  def test_final_locked_guard_rejects_new_editor_bytes_and_revoked_authority(self):
    path = Path(self.params.get_param_path("ConditionalModeConfig"))
    calls = 0
    def changed_source():
      nonlocal calls
      calls += 1
      if calls == 2:
        path.write_bytes(b'other editor')
      return True
    result = commit(self.params, stock_document(), None, None, changed_source)
    self.assertFalse(result.committed)
    self.assertEqual(path.read_bytes(), b'other editor')
    path.unlink()
    calls = 0
    def revoked():
      nonlocal calls
      calls += 1
      return calls == 1
    self.assertFalse(commit(self.params, stock_document(), None, None, revoked).committed)
    self.assertFalse(path.exists())

  def test_large_hit_target_and_compact_parent_child_reset(self):
    state = self.owner.snapshot(FeaturePage.CONDITIONAL, parked=True, system_long=False,
                                lateral_context=False, metric=False)
    hit = FeatureInput.target(2000, 285, state)
    assert hit is not None and hit.row is not None
    self.assertEqual(hit.row.key, "conditional:mode")
    class Button:
      def __init__(self, text, value):
        self.text, self.value, self.click = text, value, None
      def set_click_callback(self, callback):
        self.click = callback
      def set_enabled(self, enabled):
        self.enabled = enabled
    class Scroller:
      def __init__(self, *_args):
        self.items = []
        self._scroller = self
      def add_widgets(self, widgets):
        self.items.extend(widgets)
      def dismiss(self, callback):
        callback()
    class Session:
      def __init__(self, owner):
        self.owner = owner
      def feature_snapshot(self, page):
        return self.owner.snapshot(page, parked=True, system_long=False, lateral_context=False, metric=False)
      def feature_request(self, request):
        return self.owner.apply(request)
    stack = []
    with patch.object(compact, "BigButton", Button), patch.object(compact, "GreyBigButton", Button), \
         patch.object(compact, "NavScroller", Scroller), \
         patch.object(compact, "SettingDescriptionDialog", Scroller), patch.object(compact.gui_app, "texture"), \
         patch.object(compact, "BigConfirmationCircleButton", side_effect=lambda _title, _icon, callback: SimpleNamespace(callback=callback)), \
         patch.object(compact.gui_app, "push_widget", stack.append), \
         patch.object(compact.gui_app, "get_active_widget", side_effect=lambda: stack[-1]):
      adapter = compact.FeatureSettingsCompact(Session(self.owner))
      adapter.open(FeaturePage.HUB)
      next(card for card in stack[-1].items if card.text == "driving modes").click()
      child = stack[-1]
      next(card for card in child.items if card.text == "driving mode").click()
      self.assertEqual(decode_preferences(Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes()).mode.value,
                       "stock")
      next(card for card in child.items if card.text == "automatic conditions").click()
      self.assertTrue(any(card.text.startswith("speed threshold") for card in stack[-1].items))
      stack.pop()
      Path(self.params.get_param_path("ConditionalModeConfig")).write_bytes(b'{"version":2}')
      adapter.open(FeaturePage.CONDITIONAL)
      next(card for card in stack[-1].items if card.text == "restore chill defaults").click()
      dialog = stack.pop()
      dialog.items[-1].callback()
      self.assertEqual(decode_preferences(Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes()).mode.value,
                       "stock")
      manual_path = Path(self.params.get_param_path("ConditionalManualState"))
      manual_path.write_bytes(b'{"version":9}')
      adapter.open(FeaturePage.CONDITIONAL)
      next(card for card in stack[-1].items if card.text == "reset remembered manual choices").click()
      dialog = stack.pop()
      dialog.items[-1].callback()
      self.assertEqual(decode_manual(manual_path.read_bytes()), SavedCodes())

  def test_large_pane_renders_mode_and_child_labels_in_existing_geometry(self):
    labels = []
    fonts = SimpleNamespace(profile=Profile.LARGE,
                            draw=lambda value, *_args: labels.append(value),
                            measure=lambda value, _role, size: SimpleNamespace(width=len(value) * size * .5),
                            vertical_ink=lambda _value, _role, size: (size * .2, size * .8))
    state = self.owner.snapshot(FeaturePage.CONDITIONAL, parked=True, system_long=False,
                                lateral_context=False, metric=False)
    with patch.object(large.rl, "draw_rectangle_rounded"), patch.object(large.rl, "draw_rectangle_rounded_lines_ex"), \
         patch.object(large.rl, "draw_line"), patch.object(large.rl, "draw_line_ex"), patch.object(large.rl, "draw_circle"), \
         patch.object(large.clip, "begin_scissor_mode"), \
         patch.object(large.clip, "end_scissor_mode"):
      large.FeatureSettingsView(Mock(spec=BitmapFonts, **vars(fonts))).render(state)
    self.assertIn("Driving Modes", labels)
    self.assertIn("Driving Mode", labels)
    self.assertIn("Automatic conditions", labels)
    self.assertNotIn("Chill conditions", labels)

  def test_large_manual_reset_confirmation_revoked_on_navigation(self):
    from openpilot.starpilot.ui.runtime_app import StarShellSession
    from openpilot.starpilot.ui.settings_state import Destination
    from openpilot.starpilot.ui.shell import ShellMode
    from openpilot.system.ui.widgets import DialogResult
    manual_path = Path(self.params.get_param_path("ConditionalManualState"))
    manual_path.write_bytes(b'{"version":9}')
    row = self.row("conditional:manual_reset")
    request = FeatureSettingsRequest(row.key, row.source, "confirm", confirmation=True,
                                     vehicle_fingerprint=row.vehicle_fingerprint, capability=row.capability,
                                     dependencies=row.dependencies)
    session = StarShellSession.__new__(StarShellSession)
    session._mode = ShellMode.SETTINGS
    session.selected = Destination.DRIVING_CONTROLS
    session.feature_page = FeaturePage.CONDITIONAL
    session._lane_change_request_epoch = 0
    dialogs = []
    with patch.object(StarShellSession, "feature_request", side_effect=self.owner.apply), \
         patch("openpilot.system.ui.widgets.confirm_dialog.ConfirmDialog",
               side_effect=lambda *args, **kwargs: SimpleNamespace(callback=kwargs["callback"])), \
         patch("openpilot.starpilot.ui.runtime_app.gui_app.push_widget", dialogs.append):
      session._confirm_conditional(request)
      session._lane_change_request_epoch += 1
      dialogs[-1].callback(DialogResult.CONFIRM)
      self.assertEqual(manual_path.read_bytes(), b'{"version":9}')
      session._confirm_conditional(request)
      dialogs[-1].callback(DialogResult.CONFIRM)
      self.assertEqual(decode_manual(manual_path.read_bytes()), SavedCodes())


if __name__ == "__main__":
  unittest.main()
