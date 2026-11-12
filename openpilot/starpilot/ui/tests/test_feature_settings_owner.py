"""Native saved-feature actions use real Params bytes and strict profile helpers."""

from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import os
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.longitudinal.profile_document import migrate_profile_document
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest, row_change, row_default
from openpilot.starpilot import saved_document
from openpilot.starpilot.ui import feature_settings_owner


def required_change(row: FeatureRow, direction: int = 1) -> FeatureSettingsRequest:
  request = row_change(row, direction)
  if request is None:
    raise AssertionError(f"Expected an editable row: {row.key}")
  return request


def required_document(raw: object) -> dict:
  document = migrate_profile_document(raw)
  if document is None:
    raise AssertionError("Expected a valid saved profile document")
  return document


class FeatureSettingsOwnerTests(unittest.TestCase):
  def test_bolt_wheel_menu_exposes_only_received_buttons(self):
    from opendbc.car.gm.tests.test_bolt_pedal import params
    from opendbc.car.gm.values import CAR
    cp = params(CAR.CHEVROLET_BOLT_CC_2018_2021, pedal=True, camera=True)
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    self.params.put('LKASButtonControl', 9, block=True)
    rows = self.owner.snapshot('wheel', parked=True, system_long=True, lateral_context=True, metric=False).rows
    self.assertFalse(any(row.key == 'wheel:LKASButtonControl' for row in rows))
    self.assertTrue(any(row.key == 'wheel:MainCruiseButtonControl' and row.available for row in rows))
    self.assertTrue(any(row.key == 'wheel:DistanceButtonControl' and row.available for row in rows))
    self.assertEqual(self.params.get('LKASButtonControl'), 9)

  def test_hybrid_manual_settings_and_automatic_pedal_owner(self):
    from opendbc.car import gen_empty_fingerprint
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import CAR
    from opendbc.car.gm.aol import qualified_gm
    from opendbc.car.gm.tests.test_ordinary_cc import malibu_hybrid_params
    from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal
    cp = CarInterface.get_params(CAR.CHEVROLET_MALIBU_HYBRID_CC, gen_empty_fingerprint(), [], False, False, False)
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    self.owner.configuration_vehicle = lambda: True
    original = cp.as_reader().as_builder().to_bytes()
    row = self._row('aol', 'AlwaysOnLateral')
    self.assertTrue(row.available)
    self.assertTrue(self.owner.apply(required_change(row)))
    self.assertFalse(qualified_gm(cp))
    self.assertEqual(cp.as_reader().as_builder().to_bytes(), original)
    self.owner.configuration_vehicle = lambda: False
    cp = malibu_hybrid_params(pedal=True, removed=True)
    row = self._row('vehicle', 'GMPedalLongitudinal')
    self.assertEqual(row.value, 'Automatic')
    self.assertFalse(row.available)
    self.assertIn('Activates automatically', row.reason)
    prepare_disable_longitudinal(cp, True)
    self.assertFalse(cp.openpilotLongitudinalControl)
    self.assertEqual(self._row('vehicle', 'GMPedalLongitudinal').value, 'Detected, inactive')

  def test_prius_retrofit_manual_settings_do_not_grant_runtime(self):
    from opendbc.car import gen_empty_fingerprint, structs
    from opendbc.car.toyota.interface import CarInterface
    from opendbc.car.toyota.values import CAR
    from openpilot.starpilot.aol.vehicle import policy_for
    self.params.put_bool('AlwaysOnLateral', False, block=True)
    cp = CarInterface.get_params(CAR.TOYOTA_PRIUS_RETROFIT, gen_empty_fingerprint(), [], False, False, False)
    # A manual choice can expose saved settings before any native configuration is available.
    cp.safetyConfigs = []
    original = cp.as_reader().as_builder().to_bytes()
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    self.owner.configuration_vehicle = lambda: True
    row = self._row('aol', 'AlwaysOnLateral')
    self.assertTrue(row.available)
    self.assertIn('next startup', row.reason)
    self.assertTrue(self.owner.apply(required_change(row)))
    self.assertTrue(self.params.get_bool('AlwaysOnLateral'))
    self.assertEqual(cp.as_reader().as_builder().to_bytes(), original)
    self.assertFalse(policy_for(cp).runtime_supported)
    self.assertFalse(policy_for(cp).intent_supported)
    self.owner.configuration_vehicle = lambda: False
    self.assertTrue(self.owner.apply(required_change(self._row('aol', 'AlwaysOnLateral'))))
    self.assertFalse(self.params.get_bool('AlwaysOnLateral'))
    self.assertFalse(self._row('aol', 'AlwaysOnLateral').available)
    ordinary = CarInterface.get_params(CAR.TOYOTA_PRIUS, gen_empty_fingerprint(), [], False, False, False)
    self.owner.vehicle_params = lambda: ordinary
    self.fingerprint = ordinary.carFingerprint
    self.owner.configuration_vehicle = lambda: True
    self.assertEqual(ordinary.safetyConfigs[0].safetyParam, 0x6242)
    self.assertTrue(self._row('aol', 'AlwaysOnLateral').available)
    self.assertTrue(policy_for(ordinary).runtime_supported)
    unconfigured = ordinary.as_reader().as_builder()
    unconfigured.safetyConfigs = []
    self.owner.vehicle_params = lambda: unconfigured
    self.assertFalse(self._row('aol', 'AlwaysOnLateral').available)
    self.assertFalse(policy_for(unconfigured).runtime_supported)
    self.assertFalse(policy_for(unconfigured).intent_supported)
    fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.eps, address=0x7A1, brand='toyota', bus=0,
          fwVersion=b'8965B47070\x00\x00\x00\x00\x00\x00')]
    actual = CarInterface.get_params(CAR.TOYOTA_PRIUS, {0: {0x2FF: 4}, 1: {}, 2: {}}, fw, False, False, False)
    self.owner.vehicle_params = lambda: actual
    self.fingerprint = actual.carFingerprint
    self.owner.configuration_vehicle = lambda: False
    self.assertTrue(self._row('aol', 'AlwaysOnLateral').available)
    self.assertTrue(policy_for(actual).runtime_supported)

  def test_prius_retrofit_stock_settings_preserve_existing_runtime_profile(self):
    from opendbc.car import gen_empty_fingerprint
    from opendbc.car.toyota.interface import CarInterface
    from opendbc.car.toyota.values import CAR
    from openpilot.starpilot.aol.vehicle import policy_for
    self.params.put_bool('AlwaysOnLateral', False, block=True)
    cp = CarInterface.get_params(CAR.TOYOTA_PRIUS_RETROFIT, gen_empty_fingerprint(), [], False, False, False)
    self.assertEqual(cp.safetyConfigs[0].safetyParam, 585)
    self.assertFalse(cp.openpilotLongitudinalControl)
    self.assertTrue(policy_for(cp).runtime_supported)
    self.assertTrue(policy_for(cp).intent_supported)
    original = cp.as_reader().as_builder().to_bytes()
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    for configuration in (True, False):
      with self.subTest(configuration=configuration):
        self.owner.configuration_vehicle = lambda configuration=configuration: configuration
        row = self._row('aol', 'AlwaysOnLateral')
        self.assertTrue(row.available)
        self.assertNotIn('next startup', row.reason)
        self.assertTrue(self.owner.apply(required_change(row)))
        self.assertEqual(self.params.get_bool('AlwaysOnLateral'), configuration)
        self.assertEqual(cp.as_reader().as_builder().to_bytes(), original)
        self.assertTrue(policy_for(cp).runtime_supported)
        self.assertTrue(policy_for(cp).intent_supported)

  def test_toyota_hold_is_reachable_from_hub_with_correct_stop_description(self):
    from opendbc.car import structs
    from opendbc.car.toyota.interface import CarInterface
    from opendbc.car.toyota.values import CAR
    for hybrid in (True, False):
      fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.hybrid)] if hybrid else []
      cp = CarInterface.get_params(CAR.TOYOTA_RAV4_TSS2, {0: {0x3F6: 8}, 1: {}, 2: {}}, fw, False, False, False)
      self.fingerprint = cp.carFingerprint
      self.owner.vehicle_params = lambda cp=cp: cp
      hub = self.owner.snapshot("hub", parked=True, system_long=True, lateral_context=True, metric=False)
      self.assertTrue(any(row.label == "Vehicle Settings" and row.available for row in hub.rows))
      row = self._row("vehicle", "ToyotaAutoHold")
      self.assertTrue(row.available)
      self.assertIn("cruise disengaged", row.reason)
      self.assertIn("over one second", row.reason)
      self.assertIn("normal cruise stop-and-go is unchanged", row.reason)
      request = replace(required_change(row), confirmation=True)
      self.assertTrue(self.owner.apply(request))
      self.params.put_bool("ToyotaAutoHold", False, block=True)

  def test_volt_tune_is_not_exposed_even_in_configuration(self):
    from opendbc.car.gm.tests.test_volt_transitions import volt_camera_pedal_params
    cp = volt_camera_pedal_params(release=True)
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    self.params.put("GMLongitudinalTune", 2, block=True)
    for configured in (False, True):
      self.owner.configuration_vehicle = lambda configured=configured: configured
      self.assertNotIn("GMLongitudinalTune", [row.key for row in self.owner.snapshot(
        "vehicle", parked=True, system_long=True, lateral_context=True, metric=False).rows])
    self.assertEqual(Path(self.params.get_param_path("GMLongitudinalTune")).read_bytes(), b"2")

  def test_gm_tune_invalid_saved_choice_repairs_only_vehicle_default(self):
    from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
    from opendbc.car.gm.values import CAR as GM_CAR
    cp = ordinary_params(GM_CAR.CHEVROLET_SUBURBAN, radar=True)
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    path = Path(self.params.get_param_path("GMLongitudinalTune"))
    path.write_bytes(b"2\n")
    row = self._row("vehicle", "GMLongitudinalTune")
    self.assertEqual(row.value, "Invalid saved choice")
    reset = row_default(row)
    assert reset is not None
    self.assertFalse(self.owner.apply(replace(reset, value="Volt Tune")))
    self.assertEqual(path.read_bytes(), b"2\n")
    self.assertTrue(self.owner.apply(reset))
    self.assertEqual(path.read_bytes(), b"0")

  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.params = Params(self.temp.name)
    self.allowed = True
    self.groups = []
    self.fingerprint = "TOYOTA COROLLA TSS2"
    self.owner = FeatureSettingsOwner(self.params, self._authority, vehicle_fingerprint=lambda: self.fingerprint)

  def _authority(self, group):
    self.groups.append(group)
    return self.allowed

  def _row(self, page, key):
    state = self.owner.snapshot(page, parked=True, system_long=True, lateral_context=True, metric=False)
    return next(row for row in state.rows if row.key == key)

  def test_camera_interceptor_setup_reports_actual_owner_and_preserves_preferences(self):
    from opendbc.car.gm.tests.test_camera_acc_pedal import params
    from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal
    cp = params()
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    self.params.put_bool('GMPedalLongitudinal', False, block=True)
    pedal = self._row('vehicle', 'GMPedalLongitudinal')
    self.assertEqual(pedal.value, 'Automatic')
    self.assertFalse(pedal.available)
    pitch = self._row('vehicle', 'LongPitch')
    self.assertTrue(pitch.available)
    request = replace(required_change(pitch), confirmation=True)
    self.assertTrue(self.owner.apply(request))
    self.assertFalse(self.params.get_bool('LongPitch'))
    prepare_disable_longitudinal(cp, True)
    self.assertEqual(self._row('vehicle', 'GMPedalLongitudinal').value, 'Detected, inactive')
    self.assertFalse(self.owner.apply(request))
    self.assertFalse(self.params.get_bool('GMPedalLongitudinal'))
    self.assertNotIn('LongPitch', [row.key for row in self.owner.snapshot(
      'vehicle', parked=True, system_long=False, lateral_context=True, metric=False).rows])

  def test_malformed_numeric_default_has_no_reset_target(self):
    self.params.put("StandardFollow", 2.8, block=True)
    with patch.object(self.owner, "_default", return_value="not a number"):
      row = self.owner._number_row("StandardFollow", "Follow", True)
    self.assertIsNone(row.default_value)
    self.assertIsNone(row_default(row))

  def test_volt_cc_pedal_setup_and_grade_compensation_follow_actual_owner(self):
    from opendbc.car.gm.tests.test_volt_transitions import volt_cc_pedal_params
    from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal
    cp = volt_cc_pedal_params()
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    self.assertEqual(self._row('vehicle', 'GMPedalLongitudinal').value, 'Automatic')
    pitch = self._row('vehicle', 'LongPitch')
    self.assertTrue(pitch.available)
    request = replace(required_change(pitch), confirmation=True)
    self.assertTrue(self.owner.apply(request))
    prepare_disable_longitudinal(cp, True)
    self.assertEqual(self._row('vehicle', 'GMPedalLongitudinal').value, 'Detected, inactive')
    self.assertFalse(self.owner.apply(request))

  def test_source_profile_defaults_and_single_numeric_reset(self):
    self.params.put("StandardFollow", 2.8, block=True)
    row = self._row("standard", "StandardFollow")
    self.assertEqual(float(row.default_value), float(self.params.get_default_value("StandardFollow")))
    request = row_default(row)
    assert request is not None
    self.assertTrue(self.owner.apply(request))
    self.assertFalse(self.owner.apply(request))
    preset = self._row("standard/acceleration", "profile:standard:acceleration")
    self.assertEqual(preset.default_value, "Selected Profile")
    self.assertEqual(self._row("profiles", "profile:global_braking").default_value, "Comfort")

  def test_strength_has_actual_runtime_admission_percent_range_and_source_guard(self):
    from openpilot.starpilot.lateral.tests.test_lane_runtime import ioniq_candidate
    _, tagged = ioniq_candidate()
    root = Path(self.params.get_param_path("LaneCentering")).parent
    registered = self.params
    class Files:
      def get_param_path(self, key):
        return str(root / key)
      def get_default_value(self, key):
        return 1.5 if key == "LaneCenteringStrength" else registered.get_default_value(key)
      def put(self, key, value, block=True):
        (root / key).write_bytes(str(value).encode())
    self.owner.params = Files()
    self.owner.vehicle_params = lambda: None
    row = self._row("lane", "LaneCenteringStrength")
    self.assertFalse(row.available)
    self.assertIn("vehicle configuration", row.reason)
    self.owner.vehicle_params = lambda: tagged
    self.fingerprint = tagged.carFingerprint
    row = self._row("lane", "LaneCenteringStrength")
    self.assertEqual((row.value, row.minimum, row.maximum, row.step), ("100", 33, 200, 1))
    self.assertTrue(row.available)
    request = FeatureSettingsRequest(row.key, row.source, "200", vehicle_fingerprint=row.vehicle_fingerprint)
    self.assertTrue(self.owner.apply(request))
    self.assertEqual((root / row.key).read_bytes(), b"3.0")
    self.assertFalse(self.owner.apply(request))
    reset_row = self._row("lane", "LaneCenteringStrength")
    self.assertEqual(reset_row.default_value, "100")
    self.assertTrue(self.owner.apply(FeatureSettingsRequest(reset_row.key, reset_row.source, reset_row.default_value,
                                                          vehicle_fingerprint=reset_row.vehicle_fingerprint)))
    self.assertEqual((root / row.key).read_bytes(), b"1.5")
    self.assertEqual(registered.get_default_value("LaneCenteringStrength"), 1.5)
    for percent, expected in ((33, b"0.5"), (67, b"1.005"), (100, b"1.5"), (105, b"1.575"), (150, b"2.25"), (200, b"3.0")):
      current = self._row("lane", "LaneCenteringStrength")
      self.assertTrue(self.owner.apply(FeatureSettingsRequest(current.key, current.source, str(percent),
                                                            vehicle_fingerprint=current.vehicle_fingerprint)))
      self.assertEqual((root / row.key).read_bytes(), expected)
    (root / row.key).write_bytes(b"1.0")
    preserved = self._row("lane", "LaneCenteringStrength")
    self.assertEqual(preserved.value, "67")
    self.assertEqual((root / row.key).read_bytes(), b"1.0")
    row = self._row("lane", "LaneCenteringStrength")
    for invalid in ("32", "201", "nan"):
      self.assertFalse(self.owner.apply(FeatureSettingsRequest(row.key, row.source, invalid,
                                                             vehicle_fingerprint=row.vehicle_fingerprint)))
    self.owner.vehicle_params = lambda: None
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(row.key, row.source, "100",
                                                           vehicle_fingerprint=row.vehicle_fingerprint)))
    self.assertIsNone(registered.get("LaneCenteringE2EAuthority"))

  def test_turn_assist_defaults_on_independent_of_tune_and_exact_source_guarded(self):
    from openpilot.starpilot.lateral.tests.test_lane_runtime import ioniq_candidate
    from openpilot.starpilot.lateral.controller_selection import DOCUMENT_KEY, ControllerMode, replace_mode
    _, cp = ioniq_candidate()
    self.owner.vehicle_params = lambda: cp
    self.owner.controller.vehicle_params = self.owner.vehicle_params
    self.fingerprint = cp.carFingerprint
    row = self._row("torque", "TurnAssist")
    self.assertEqual(row.value, "On")
    self.assertTrue(row.available)
    self.assertEqual(row.dependencies, ())
    request = required_change(row)
    self.assertTrue(self.owner.apply(request))
    self.assertFalse(self.params.get_bool("TurnAssist"))
    self.assertFalse(self.owner.apply(request))
    import json
    self.params.put(DOCUMENT_KEY, json.loads(replace_mode(None, cp, ControllerMode.STANDARD)), block=True)
    row = self._row("torque", "TurnAssist")
    self.assertTrue(row.available)
    self.assertEqual(row.value, "Off")
    self.assertFalse(self.params.get_bool("TurnAssist"))
    view = self.owner.snapshot("torque", parked=False, system_long=True, lateral_context=True, metric=False)
    self.assertFalse(next(row for row in view.rows if row.key == "TurnAssist").available)
    self.owner.vehicle_params = lambda: None
    view = self.owner.snapshot("torque", parked=True, system_long=True, lateral_context=True, metric=False)
    self.assertFalse(any(row.key == "TurnAssist" for row in view.rows))
    self.assertFalse(self.owner.apply(required_change(row)))
    self.assertFalse(self.params.get_bool("TurnAssist"))

  def test_turn_assist_nonpolicy_owner_saved_off_survives_restart(self):
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.mazda.values import CAR
    from openpilot.starpilot.lateral.controller_selection import policy_for
    from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
    cp = interfaces[CAR.MAZDA_CX5_2022].get_non_essential_params(CAR.MAZDA_CX5_2022)
    self.assertEqual(policy_for(cp), 'generic')
    self.owner.vehicle_params = lambda: cp
    self.owner.controller.vehicle_params = self.owner.vehicle_params
    self.fingerprint = cp.carFingerprint
    row = self._row("torque", "TurnAssist")
    self.assertTrue(row.available)
    self.assertEqual(row.value, "On")
    self.assertTrue(self.owner.apply(required_change(row)))
    self.assertFalse(VehicleStartupPreferences.read(self.params, enabled=True).turn_assist)
    row = self._row("torque", "TurnAssist")
    self.assertEqual(row.value, "Off")
    cp.minSteerSpeed += 1.
    self.assertFalse(self.owner.apply(required_change(row)))
    cp.minSteerSpeed -= 1.
    self.assertTrue(self.owner.apply(required_change(self._row("torque", "TurnAssist"))))
    self.assertTrue(VehicleStartupPreferences.read(self.params, enabled=True).turn_assist)

  def test_lane_centering_default_and_saved_off_are_distinct(self):
    self.assertIsNone(self.params.get("LaneCentering"))
    row = self._row("lane", "LaneCentering")
    self.assertEqual(row.value, "On")
    self.assertEqual(row.default_value, "On")
    self.assertIsNone(self.params.get("LaneCentering"))
    self.params.put_bool("LaneCentering", False, block=True)
    row = self._row("lane", "LaneCentering")
    self.assertEqual(row.value, "Off")
    self.assertEqual(row.default_value, "On")
    self.assertFalse(self.params.get_bool("LaneCentering"))

  def test_live_lane_owner_maps_only_explicit_keys(self):
    self.params.put_bool("LaneCentering", False, block=True)
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.toyota.values import CAR
    cp = interfaces[CAR.TOYOTA_COROLLA_TSS2].get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    self.owner.authority = lambda group: group == "lane_live"
    view = self.owner.snapshot("lane", parked=False, system_long=False, lateral_context=False, metric=False)
    row = next(row for row in view.rows if row.key == "LaneCentering")
    self.assertTrue(row.available)
    self.assertTrue(self.owner.apply(required_change(row)))
    self.assertTrue(self.params.get("LaneCentering"))
    view = self.owner.snapshot("lane", parked=False, system_long=False, lateral_context=False, metric=False)
    pause = next(row for row in view.rows if row.key == "LaneCenteringPauseOnSignal")
    self.assertTrue(pause.available)
    self.assertTrue(self.owner.apply(required_change(pause)))
    for key in ("AlwaysOnLateral", "CustomPersonalities", "LongPitch"):
      self.assertFalse(self.owner.apply(FeatureSettingsRequest(key, None, "On", vehicle_fingerprint=self.fingerprint)))

  def test_force_stop_disable_onroad_retains_source_vehicle_and_enable_gates(self):
    self.params.put_bool("ForceStops", True, block=True)
    self.owner.authority = lambda group: group == "preferences"
    view = self.owner.snapshot("profiles", parked=False, system_long=False, lateral_context=True, metric=False)
    row = next(row for row in view.rows if row.key == "ForceStops")
    self.assertTrue(row.available)
    request = required_change(row)
    self.assertEqual(request.value, "Off")
    self.fingerprint = "different vehicle"
    self.assertFalse(self.owner.apply(request))
    self.fingerprint = row.vehicle_fingerprint
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(self.params.get("ForceStops"), False)
    self.assertFalse(self.owner.apply(request))
    enable = FeatureSettingsRequest("ForceStops", b"0", "On", vehicle_fingerprint=self.fingerprint)
    self.assertFalse(self.owner.apply(enable))
    self.assertIsNone(self.params.get("QOLLongitudinal"))

  def test_force_stop_preserves_cruise_master_and_offset_bounds(self):
    self.params.put_bool("ForceStops", False, block=True)
    self.assertTrue(self._row("profiles", "ForceStops").available)
    self.params.put_bool("QOLLongitudinal", True, block=True)
    force = self._row("profiles", "ForceStops")
    self.assertTrue(force.available)
    self.assertEqual(force.value, "Off")
    self.params.put_bool("ForceStops", True, block=True)
    force = self._row("profiles", "ForceStops")
    self.assertEqual(force.value, "On")
    offset = self._row("profiles", "ForceStopDistanceOffset")
    self.assertTrue(self.owner.apply(FeatureSettingsRequest(offset.key, offset.source, "4",
                                                          vehicle_fingerprint=offset.vehicle_fingerprint)))
    self.assertEqual(self.params.get("ForceStopDistanceOffset"), 4)
    offset = self._row("profiles", "ForceStopDistanceOffset")
    for invalid in ("21", "-21", "0.5"):
      self.assertFalse(self.owner.apply(FeatureSettingsRequest(offset.key, offset.source, invalid,
                                                             vehicle_fingerprint=offset.vehicle_fingerprint)))
    self.assertTrue(self.owner.apply(required_change(force)))
    self.assertFalse(self._row("profiles", "ForceStopDistanceOffset").available)

  def test_corrupt_bytes_remain_visible_and_untouched(self):
    for key, page, raw in (("LaneCenterOffset", "lane", b"nan"),
                           ("SpeedLimitController", "slc", b"maybe")):
      with self.subTest(key=key):
        path = Path(self.params.get_param_path(key))
        path.write_bytes(raw)
        row = self._row(page, key)
        self.assertFalse(row.available)
        self.assertIsNone(row_change(row))
        self.assertEqual(path.read_bytes(), raw)

  def test_readable_presets_keep_saved_tokens_and_numeric_help(self):
    preset = self._row("aggressive/acceleration", "profile:aggressive:acceleration")
    self.assertEqual(preset.value, "Selected Profile")
    self.assertEqual(preset.choices, ("Selected Profile", "StarPilot Default", "Normal", "Comfort", "Sport", "Sport+"))
    self.assertTrue(self.owner.apply(FeatureSettingsRequest(preset.key, preset.source, "Sport+",
                                                          vehicle_fingerprint=preset.vehicle_fingerprint)))
    document = required_document(Path(self.params.get_param_path("LongitudinalPersonalityProfiles")).read_bytes())
    self.assertEqual(document["profiles"]["aggressive"]["acceleration"]["preset"], "sport_plus")
    self.assertIn("Higher values", self._row("aggressive", "AggressiveJerkAcceleration").reason)
    self.assertFalse(any(row.key == "AggressivePersonalityProfile" for row in self.owner.snapshot(
      "aggressive", parked=True, system_long=True, lateral_context=True, metric=False).rows))
    self.assertIn("Sport+", self._row("aggressive/acceleration", "profile:aggressive:acceleration").value)
    self.assertIsNone(self.params.get("CustomPersonalities"))

  def test_release_rechecks_parked_authority_and_source(self):
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.toyota.values import CAR
    cp = interfaces[CAR.TOYOTA_COROLLA_TSS2].get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    row = self._row("lane", "LaneCentering")
    request = required_change(row)
    self.assertIsNotNone(request)
    self.allowed = False
    self.assertFalse(self.owner.apply(request))
    self.allowed = True
    self.params.put_bool("LaneCentering", True, block=True)
    self.assertFalse(self.owner.apply(request))
    self.assertTrue(self.params.get_bool("LaneCentering"))
    self.assertIn("lane", self.groups)
    fresh = self._row("lane", "LaneCentering")
    self.fingerprint = "OTHER CAR"
    self.assertFalse(self.owner.apply(required_change(fresh)))

  def test_approaching_lead_setting_independent_and_exactly_guarded(self):
    self.fingerprint = None
    Path(self.params.get_param_path("LongitudinalPersonalityProfiles")).write_bytes(b"{invalid")
    Path(self.params.get_param_path("CustomPersonalities")).write_bytes(b"invalid")
    row = self._row("profiles", "LeadApproachBuffer")
    self.assertEqual((row.label, row.value), ("Approaching lead buffer", "Off"))
    self.assertTrue(row.available)
    self.assertIsNone(row.vehicle_fingerprint)
    self.assertIsNone(row.capability)
    request = required_change(row)
    self.allowed = False
    self.assertFalse(self.owner.apply(request))
    self.allowed = True
    self.assertTrue(self.owner.apply(request))
    saved = Path(self.params.get_param_path("LeadApproachBuffer"))
    self.assertEqual(saved.read_bytes(), b"1")
    self.assertFalse(self.owner.apply(request))
    off = required_change(self._row("profiles", "LeadApproachBuffer"))
    saved.write_bytes(b"0")
    self.assertFalse(self.owner.apply(off))
    self.assertEqual(saved.read_bytes(), b"0")
    self.assertTrue(self._row("profiles", "LeadApproachBuffer").available)

  def test_confirmation_is_one_guarded_choice(self):
    from openpilot.starpilot.speed_limits.runtime_settings import read_params
    self.assertEqual(self._row('slc', 'SLCConfirmation').value, 'Off')
    for expected, label, lower, higher in (
        (1, 'Both', True, True), (3, 'Higher only', False, True),
        (4, 'Lower only', True, False), (0, 'Off', False, False),
    ):
      request = required_change(self._row('slc', 'SLCConfirmation'))
      self.assertTrue(self.owner.apply(request))
      self.assertFalse(self.owner.apply(request))
      self.assertEqual(self.params.get('SLCConfirmation'), expected)
      self.assertEqual(self._row('slc', 'SLCConfirmation').value, label)
      policy = read_params(self.params).acceptance
      self.assertEqual((policy.confirm_lower, policy.confirm_higher), (lower, higher))

  def test_invalid_confirmation_can_be_repaired_to_both(self):
    Path(self.params.get_param_path('SLCConfirmation')).write_bytes(b'2')
    row = self._row('slc', 'SLCConfirmation')
    self.assertEqual(row.repair_value, 'Both')
    self.assertTrue(self.owner.apply(required_change(row)))
    self.assertEqual(self.params.get('SLCConfirmation'), 1)

  def test_parent_and_units_guards(self):
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.toyota.values import CAR
    cp = interfaces[CAR.TOYOTA_COROLLA_TSS2].get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    self.assertFalse(any(row.key in ('SLCConfirmationLower', 'SLCConfirmationHigher') for row in
                         self.owner.snapshot('slc', parked=True, system_long=True, lateral_context=True, metric=False).rows))
    self.assertFalse(self.owner.apply(FeatureSettingsRequest('SLCConfirmationLower', None, 'On', vehicle_fingerprint=self.fingerprint)))
    parent = self._row("slc", "SLCConfirmation")
    self.assertTrue(self.owner.apply(required_change(parent)))
    self.assertTrue(self.params.get('SLCConfirmation'))
    authority = self._row("lane", "LaneCenteringE2EAuthority")
    self.assertEqual(authority.value, "50.0")
    self.assertTrue(self.owner.apply(required_change(authority, -1)))
    self.assertAlmostEqual(self.params.get("LaneCenteringE2EAuthority"), 0.45)
    authority = self._row("lane", "LaneCenteringE2EAuthority")
    self.assertEqual(authority.default_value, "50.0")
    reset = row_default(authority)
    assert reset is not None
    self.assertTrue(self.owner.apply(reset))
    self.assertEqual(self.params.get("LaneCenteringE2EAuthority"), 0.5)
    offset = self._row("slc", "slc:offset:1")
    self.assertTrue(offset.available)
    self.assertFalse(self.owner.apply(FeatureSettingsRequest("slc:offset:1", offset.source, "5", vehicle_fingerprint=offset.vehicle_fingerprint)))
    Path(self.params.get_param_path("IsMetric")).write_bytes(b"bad")
    offset = self._row("slc", "slc:offset:1")
    self.assertIn("invalid saved units", offset.label)
    self.assertEqual(Path(self.params.get_param_path("IsMetric")).read_bytes(), b"bad")

  def test_retired_fallback_is_hidden_and_cannot_be_written(self):
    path = Path(self.params.get_param_path('SLCFallback'))
    for raw in (b'0', b'1', b'2', b'broken'):
      with self.subTest(raw=raw):
        path.write_bytes(raw)
        rows = self.owner.snapshot('slc', parked=True, system_long=True, lateral_context=True, metric=False).rows
        self.assertFalse(any(row.key == 'SLCFallback' for row in rows))
        self.assertFalse(self.owner.apply(FeatureSettingsRequest('SLCFallback', raw, 'Off',
                                                                  vehicle_fingerprint=self.fingerprint)))
        self.assertEqual(path.read_bytes(), raw)

  def test_match_speed_limit_on_engage_uses_the_software_cruise_setting(self):
    from opendbc.car.structs import car
    cp = car.CarParams(carFingerprint=self.fingerprint, openpilotLongitudinalControl=True, pcmCruise=False)
    self.owner.vehicle_params = lambda: cp
    self.params.put_bool('SpeedLimitController', True, block=True)
    row = self._row('slc', 'SLCOnEngage')
    self.assertEqual((row.label, row.value, row.default_value), ('Match Speed Limit on Engage', 'Off', 'Off'))
    request = required_change(row)
    cp.pcmCruise = True
    self.assertFalse(self.owner.apply(request))
    cp.pcmCruise = False
    self.assertTrue(self.owner.apply(request))
    self.assertTrue(self.params.get_bool('SLCOnEngage'))
    self.assertEqual(self._row('slc', 'SLCOnEngage').value, 'On')

  def test_gas_pedal_override_uses_the_shared_slc_settings_owner(self):
    row = self._row('slc', 'SLCOverrideMode')
    choices = ('Resume the adjusted limit', 'Hold the speed you reached', 'Use your SET speed')
    self.assertEqual((row.label, row.value), ('After an accelerator override', choices[1]))
    self.assertEqual(row.choices, choices)
    self.allowed = False
    self.assertFalse(self.owner.apply(required_change(row)))
    self.allowed = True
    for mode in (2, 0, 1):
      self.assertTrue(self.owner.apply(required_change(row)))
      self.assertEqual(self.params.get('SLCOverrideMode'), mode)
      row = self._row('slc', 'SLCOverrideMode')
      self.assertEqual(row.value, choices[mode])

    Path(self.params.get_param_path('SLCOverrideMode')).write_bytes(b'9')
    repair = required_change(self._row('slc', 'SLCOverrideMode'))
    self.assertEqual(repair.value, choices[0])
    self.assertFalse(self.owner.apply(replace(repair, value=choices[2])))
    self.assertTrue(self.owner.apply(repair))
    self.assertEqual(self.params.get('SLCOverrideMode'), 0)

  def test_wheel_override_is_off_and_only_configurable_for_software_set(self):
    from opendbc.car.structs import car
    cp = car.CarParams(carFingerprint=self.fingerprint, openpilotLongitudinalControl=True, pcmCruise=False)
    self.owner.vehicle_params = lambda: cp
    self.params.put_bool('SpeedLimitController', True, block=True)
    row = self._row('slc', 'SLCWheelOverride')
    self.assertEqual((row.label, row.value, row.default_value), ('Override speed limit with +', 'Off', 'Off'))
    request = required_change(row)
    cp.pcmCruise = True
    self.assertFalse(self.owner.apply(request))
    cp.pcmCruise = False
    self.params.put_bool('SpeedLimitController', False, block=True)
    self.assertFalse(self.owner.apply(request))
    self.params.put_bool('SpeedLimitController', True, block=True)
    self.assertTrue(self.owner.apply(request))
    self.assertTrue(self.params.get_bool('SLCWheelOverride'))

  def test_limit_sign_preference_needs_parked_authority_not_long_control_or_vehicle(self):
    self.fingerprint = None
    self.owner = FeatureSettingsOwner(self.params, lambda group: self.allowed and group == "preferences",
                                      vehicle_fingerprint=lambda: self.fingerprint)
    page = self.owner.snapshot("slc", parked=True, system_long=False, lateral_context=False, metric=False)
    fresh_sign = next(row for row in page.rows if row.key == "ShowSpeedLimits")
    self.assertEqual((fresh_sign.value, fresh_sign.default_value), ('On', 'On'))
    self.params.put_bool('ShowSpeedLimits', False, block=True)
    self.params.put('SLCPriority1', 'Dashboard', block=True)
    page = self.owner.snapshot("slc", parked=True, system_long=False, lateral_context=False, metric=False)
    sign = next(row for row in page.rows if row.key == "ShowSpeedLimits")
    self.assertTrue(sign.available)
    self.assertIsNone(sign.vehicle_fingerprint)
    for key in ("SpeedLimitController", "SLCConfirmation", "SLCPriority1"):
      self.assertFalse(next(row for row in page.rows if row.key == key).available)
    request = required_change(sign)
    self.allowed = False
    self.assertFalse(self.owner.apply(request))
    self.allowed = True
    self.assertTrue(self.owner.apply(request))
    self.assertTrue(self.params.get_bool("ShowSpeedLimits"))
    self.assertFalse(self.params.get_bool("SpeedLimitController"))
    self.assertFalse(self.owner.apply(request))

  def test_curve_saved_choices_and_invalid_learning(self):
    hub = self.owner.snapshot("hub", parked=True, system_long=True, lateral_context=True, metric=False)
    self.assertIn("curve", [row.page for row in hub.rows])
    master = self._row("curve", "CurveSpeedController")
    self.assertEqual(master.value, "Off")
    self.assertTrue(self.owner.apply(required_change(master)))
    self.assertIn("long", self.groups)
    no_lead = self._row("curve", "CurveSpeedControllerNoLead")
    self.assertTrue(self.owner.apply(required_change(no_lead)))
    self.assertTrue(self.params.get_bool("CurveSpeedControllerNoLead"))
    display = self._row("curve", "ShowCSCStatus")
    self.assertTrue(self.owner.apply(required_change(display)))
    self.assertTrue(self.params.get_bool("ShowCSCStatus"))
    document = Path(self.params.get_param_path("CurveComfortData"))
    document.write_bytes(b"{invalid")
    self.assertFalse(self.owner.apply(required_change(master)))  # Old document evidence cannot authorize another action.
    enabled = self._row("curve", "CurveSpeedController")
    self.assertTrue(enabled.available)
    self.assertTrue(self.owner.apply(required_change(enabled)))  # Off remains available, preserving corrupt bytes.
    self.assertEqual(document.read_bytes(), b"{invalid")
    self.assertFalse(self._row("curve", "CurveSpeedControllerNoLead").available)
    self.assertFalse(self._row("curve", "CurveSpeedController").available)
    self.assertTrue(self._row("curve", "ShowCSCStatus").available)
    Path(self.params.get_param_path("CurveSpeedController")).write_bytes(b"maybe")
    invalid_master = self._row("curve", "CurveSpeedController")
    self.assertFalse(invalid_master.available)
    self.assertEqual(invalid_master.reason, "Invalid saved value")

  def test_curve_requires_fresh_long_authority_and_legacy_source(self):
    legacy = Path(self.params.get_param_path("CurvatureData"))
    legacy.write_bytes(b"{}")
    master = self._row("curve", "CurveSpeedController")
    request = required_change(master)
    legacy.write_bytes(b'{"0.001":{"average":2.0,"count":2}}')
    self.assertFalse(self.owner.apply(request))
    self.allowed = False
    self.assertFalse(self._row("curve", "CurveSpeedController").available)
    self.allowed = True
    fresh = self._row("curve", "CurveSpeedController")
    self.fingerprint = "OTHER CAR"
    self.assertFalse(self.owner.apply(required_change(fresh)))

  def test_curve_learning_automatic_readout_and_confirmed_reset(self):
    legacy = Path(self.params.get_param_path("CurvatureData"))
    legacy_raw = b'{"0.001":{"average":2.4,"count":2}}'
    legacy.write_bytes(legacy_raw)
    state = self.owner.snapshot("curve", parked=True, system_long=True, lateral_context=True, metric=False)
    self.assertFalse(any(row.key == "curve_adopt" for row in state.rows))
    progress = next(row for row in state.rows if row.label == "Learning progress")
    self.assertNotEqual(progress.value, "Unavailable")
    comfort = next(row for row in state.rows if row.label == "Saved cornering comfort")
    self.assertIn("m/s²", comfort.value)
    canonical = Path(self.params.get_param_path("CurveComfortData"))
    self.assertFalse(canonical.exists())
    self.assertEqual(legacy.read_bytes(), legacy_raw)
    reset = self._row("curve", "curve_reset")
    request = FeatureSettingsRequest(reset.key, reset.source, "confirm", confirmation=True,
      vehicle_fingerprint=reset.vehicle_fingerprint, dependencies=reset.dependencies)
    self.assertFalse(self.owner.apply(replace(request, confirmation=False)))
    legacy.write_bytes(b'{"0.001":{"average":2.0,"count":2}}')
    self.assertFalse(self.owner.apply(request))
    reset = self._row("curve", "curve_reset")
    request = FeatureSettingsRequest(reset.key, reset.source, "confirm", confirmation=True,
      vehicle_fingerprint=reset.vehicle_fingerprint, dependencies=reset.dependencies)
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(canonical.read_bytes(), b'{"version":1,"buckets":{}}')
    self.assertEqual(legacy.read_bytes(), b'{"0.001":{"average":2.0,"count":2}}')

  def test_curve_learning_action_rechecks_parked_vehicle(self):
    Path(self.params.get_param_path("CurvatureData")).write_bytes(b'{"0.001":{"average":2.0,"count":2}}')
    adopt = self._row("curve", "curve_reset")
    request = FeatureSettingsRequest(adopt.key, adopt.source, "confirm", confirmation=True,
                                     vehicle_fingerprint=adopt.vehicle_fingerprint, dependencies=adopt.dependencies)
    self.allowed = False
    self.assertFalse(self.owner.apply(request))
    self.allowed = True
    self.fingerprint = "OTHER CAR"
    self.assertFalse(self.owner.apply(request))
    self.assertIsNone(self.params.get("CurveComfortData"))

  def test_profile_document_round_trip_and_explicit_invalid_reset(self):
    self.params.put("LongitudinalPersonalityProfiles", {}, block=True)
    preset = self._row("aggressive/acceleration", "profile:aggressive:acceleration")
    self.assertTrue(self.owner.apply(FeatureSettingsRequest(preset.key, preset.source, "custom", vehicle_fingerprint=preset.vehicle_fingerprint)))
    raw = Path(self.params.get_param_path("LongitudinalPersonalityProfiles")).read_bytes()
    doc = required_document(raw)
    self.assertEqual(doc["profiles"]["aggressive"]["acceleration"]["preset"], "custom")
    point = self._row("aggressive/acceleration", "profile:aggressive:acceleration:0")
    self.assertTrue(self.owner.apply(required_change(point, -1)))
    self.assertNotEqual(Path(self.params.get_param_path("LongitudinalPersonalityProfiles")).read_bytes(), raw)
    path = Path(self.params.get_param_path("LongitudinalPersonalityProfiles"))
    path.write_bytes(b"{invalid")
    self.params.put_bool("CustomPersonalities", True, block=True)
    reset = self._row("profiles", "reset_profiles")
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(reset.key, reset.source, "reset", vehicle_fingerprint=reset.vehicle_fingerprint)))
    self.assertEqual(path.read_bytes(), b"{invalid")
    self.assertTrue(self.owner.apply(FeatureSettingsRequest(reset.key, reset.source, "reset", confirmation=True,
                                                            vehicle_fingerprint=reset.vehicle_fingerprint)))
    restored = required_document(path.read_bytes())
    self.assertIsNotNone(restored)
    self.assertFalse(restored["enabled"])
    self.assertFalse(self.params.get_bool("CustomPersonalities"))

  def test_profile_enable_lost_authority_keeps_boolean_disabled(self):
    self.owner.vehicle_params = lambda: SimpleNamespace(
      carFingerprint=self.fingerprint, openpilotLongitudinalControl=True, pcmCruise=False,
      passive=False, notCar=False, dashcamOnly=False, carVin="VIN1")
    self.params.put("LongitudinalPersonalityProfiles", {}, block=True)
    row = self._row("profiles", "CustomPersonalities")
    request = required_change(row)
    original_replace = os.replace
    replacements = []

    def lose_authority_after_document(source, destination):
      original_replace(source, destination)
      replacements.append(destination)
      self.allowed = False

    with patch.object(saved_document.os, "replace", side_effect=lose_authority_after_document):
      self.assertFalse(self.owner.apply(request))
    self.assertEqual(len(replacements), 1)
    self.assertFalse(self.params.get_bool("CustomPersonalities"))

  def test_staged_profile_edit_rechecks_master_vehicle_and_parked_authority(self):
    original_fsync = os.fsync
    for changed in ("master", "vehicle", "authority"):
      with self.subTest(changed=changed):
        self.allowed = True
        self.fingerprint = "TOYOTA COROLLA TSS2"
        self.params.put_bool("CustomPersonalities", False, block=True)
        request = required_change(self._row("profiles", "profile:global_braking"))
        staged = []
        def invalidate_after_stage(fd, changed=changed, staged=staged):
          original_fsync(fd)
          if not staged:
            staged.append(True)
            if changed == "master":
              self.params.put_bool("CustomPersonalities", True, block=True)
            elif changed == "vehicle":
              self.fingerprint = "OTHER CAR"
            else:
              self.allowed = False
        with patch.object(saved_document.os, "fsync", side_effect=invalidate_after_stage):
          self.assertFalse(self.owner.apply(request))
        self.assertTrue(staged)
        self.assertIsNone(self.params.get("LongitudinalPersonalityProfiles"))

  def test_profile_enable_does_not_adopt_a_competing_document(self):
    self.owner.vehicle_params = lambda: SimpleNamespace(
      carFingerprint=self.fingerprint, openpilotLongitudinalControl=True, pcmCruise=False,
      passive=False, notCar=False, dashcamOnly=False, carVin="VIN1")
    request = required_change(self._row("profiles", "CustomPersonalities"))
    writes = []
    def replace_after_verified_commit(*args, **kwargs):
      result = saved_document.commit_exact(*args, **kwargs)
      self.assertTrue(result.committed and result.verified)
      competing = required_document(self.params.get("LongitudinalPersonalityProfiles"))
      competing["globalBrakingResponse"] = "sport"
      self.params.put("LongitudinalPersonalityProfiles", competing, block=True)
      writes.append(True)
      return result
    with patch.object(feature_settings_owner, "commit_exact", side_effect=replace_after_verified_commit):
      self.assertFalse(self.owner.apply(request))
    self.assertEqual(writes, [True])
    self.assertFalse(self.params.get_bool("CustomPersonalities"))
    self.assertEqual(self.params.get("LongitudinalPersonalityProfiles")["globalBrakingResponse"], "sport")

  def test_profile_enable_rechecks_document_while_staging_switch(self):
    self.owner.vehicle_params = lambda: SimpleNamespace(
      carFingerprint=self.fingerprint, openpilotLongitudinalControl=True, pcmCruise=False,
      passive=False, notCar=False, dashcamOnly=False, carVin="VIN1")
    request = required_change(self._row("profiles", "CustomPersonalities"))
    original_fsync = os.fsync
    calls = []
    def competing_edit(fd):
      original_fsync(fd)
      calls.append(fd)
      if len(calls) == 3:
        document = self.params.get("LongitudinalPersonalityProfiles")
        document["globalBrakingResponse"] = "sport"
        self.params.put("LongitudinalPersonalityProfiles", document, block=True)
    with patch.object(saved_document.os, "fsync", side_effect=competing_edit):
      self.assertFalse(self.owner.apply(request))
    self.assertEqual(len(calls), 3)
    self.assertFalse(self.params.get_bool("CustomPersonalities"))
    self.assertEqual(self.params.get("LongitudinalPersonalityProfiles")["globalBrakingResponse"], "sport")

  def test_global_braking_saved_choice_is_parked_source_bound_and_preserved_by_profile_edit(self):
    row = self._row("profiles", "profile:global_braking")
    self.assertEqual((row.value, row.choices), ("Comfort", ("StarPilot Default", "Normal", "Comfort", "Sport")))
    self.assertFalse(self.params.get_bool("CustomPersonalities"))
    change = required_change(row)
    assert change is not None
    self.allowed = False
    self.assertFalse(self.owner.apply(change))
    self.allowed = True
    self.assertTrue(self.owner.apply(change))
    saved = required_document(self.params.get("LongitudinalPersonalityProfiles"))
    self.assertEqual(saved["selectedDecelerationProfile"], "sport")
    self.assertFalse(saved["enabled"])
    self.assertFalse(self.owner.apply(change))
    category = self._row("standard/braking", "profile:standard:braking")
    self.assertTrue(self.owner.apply(required_change(category)))
    self.assertEqual(required_document(self.params.get("LongitudinalPersonalityProfiles"))["selectedDecelerationProfile"], "sport")
    unparked = self.owner.snapshot("profiles", parked=False, system_long=True, lateral_context=True, metric=False)
    self.assertFalse(next(row for row in unparked.rows if row.key == "profile:global_braking").available)


if __name__ == "__main__":
  unittest.main()


class TruckTuningFeatureTests(unittest.TestCase):
  setUp = FeatureSettingsOwnerTests.setUp
  _authority = FeatureSettingsOwnerTests._authority
  _row = FeatureSettingsOwnerTests._row

  def test_truck_saved_choice_exact_owner_and_stale_request(self):
    from opendbc.car.gm.tests.test_ordinary_camera import params
    from opendbc.car.gm.values import CAR
    cp = params(CAR.CHEVROLET_SILVERADO, alpha=True)
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    row = self._row('vehicle', 'TruckTuning')
    self.assertEqual((row.value, row.default_value), ('Off', 'Off'))
    request = required_change(row)
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(Path(self.params.get_param_path('TruckTuning')).read_bytes(), b'1')
    self.assertFalse(self.owner.apply(request))
    stale = required_change(self._row('vehicle', 'TruckTuning'))
    cp = params(CAR.CHEVROLET_SILVERADO, alpha=True, release=True)
    self.assertFalse(self.owner.apply(stale))
    self.assertFalse(any(row.key == 'TruckTuning' for row in self.owner.snapshot(
      'vehicle', parked=True, system_long=False, lateral_context=True, metric=False).rows))
    self.assertEqual(Path(self.params.get_param_path('TruckTuning')).read_bytes(), b'1')

  def test_truck_invalid_choice_repairs_only_off_and_respects_authority(self):
    from opendbc.car.gm.tests.test_ordinary_camera import params
    from opendbc.car.gm.values import CAR
    cp = params(CAR.CHEVROLET_SILVERADO, alpha=True, camera=False)
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    path = Path(self.params.get_param_path('TruckTuning'))
    path.write_bytes(b'1\n')
    row = self._row('vehicle', 'TruckTuning')
    request = row_default(row)
    assert request is not None
    self.assertFalse(self.owner.apply(replace(request, value='On')))
    self.allowed = False
    self.assertFalse(self.owner.apply(request))
    self.allowed = True
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(path.read_bytes(), b'0')


class EvPresetFeatureTests(unittest.TestCase):
  setUp = FeatureSettingsOwnerTests.setUp
  _authority = FeatureSettingsOwnerTests._authority
  _row = FeatureSettingsOwnerTests._row

  def test_saved_ev_choice_is_atomic_and_stock_owner_cannot_change_it(self):
    from opendbc.car.gm.tests.test_ordinary_camera import params
    from opendbc.car.gm.values import CAR
    cp = params(CAR.CHEVROLET_SILVERADO, alpha=True)
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    row = self._row('vehicle', 'EVTuning')
    self.assertEqual(row.value, 'Off')
    self.assertIsNone(row.source)
    request = required_change(row)
    self.assertTrue(self.owner.apply(request))
    self.assertTrue(self.params.get_bool('EVTuning'))
    self.assertFalse(self.owner.apply(request))
    stale = required_change(self._row('vehicle', 'EVTuning'))
    cp = params(CAR.CHEVROLET_SILVERADO, alpha=True, release=True)
    self.assertFalse(self.owner.apply(stale))
    self.assertFalse(any(r.key == 'EVTuning' for r in self.owner.snapshot('vehicle', parked=True, system_long=False, lateral_context=True, metric=False).rows))
    self.assertTrue(self.params.get_bool('EVTuning'))

  def test_invalid_ev_choice_repairs_only_current_powertrain_default(self):
    from opendbc.car.gm.tests.test_ordinary_camera import params
    from opendbc.car.gm.values import CAR
    cp = params(CAR.CHEVROLET_SILVERADO, alpha=True)
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    path = Path(self.params.get_param_path('EVTuning'))
    path.write_bytes(b'1\n')
    request = row_default(self._row('vehicle','EVTuning'))
    assert request is not None
    self.assertFalse(self.owner.apply(replace(request,value='On')))
    self.allowed = False
    self.assertFalse(self.owner.apply(request))
    self.allowed = True
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(path.read_bytes(),b'0')


  def test_ev_default_row_follows_final_direct_powertrain_without_saving(self):
    from opendbc.car.gm.tests.test_bolt_pedal import params
    from opendbc.car.gm.values import CAR
    cp = params(CAR.CHEVROLET_BOLT_CC_2022_2023,pedal=True,camera=True)
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    row = self._row('vehicle','EVTuning')
    self.assertEqual((row.value,row.default_value),('On','On'))
    self.assertIsNone(row.source)
    self.assertFalse(Path(self.params.get_param_path('EVTuning')).exists())


  def test_current_vehicle_loss_during_ev_commit_denies_without_saving(self):
    from opendbc.car.gm.tests.test_ordinary_camera import params
    from opendbc.car.gm.values import CAR
    cp = params(CAR.CHEVROLET_SILVERADO, alpha=True)
    self.fingerprint = cp.carFingerprint
    self.owner.vehicle_params = lambda: cp
    request = required_change(self._row('vehicle', 'EVTuning'))
    reads = []

    def current_vehicle():
      reads.append(len(reads))
      return cp if len(reads) == 1 else None

    self.owner.vehicle_params = current_vehicle
    self.assertFalse(self.owner.apply(request))
    self.assertEqual(len(reads), 2)
    self.assertFalse(Path(self.params.get_param_path('EVTuning')).exists())
