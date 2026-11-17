"""Saved Torque/AOL UI actions against real Params, CarParams and runtime parsers."""
from unittest.mock import Mock
from openpilot.starpilot.ui.presentation import BitmapFonts


from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.car import gen_empty_fingerprint
from opendbc.car.car_helpers import interfaces
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR as HONDA
from opendbc.car.hyundai.interface import CarInterface as HyundaiInterface
from opendbc.car.hyundai.ioniq6_handoff import build_ioniq6_hda2_long_candidate
from opendbc.car.hyundai.values import CAR as HYUNDAI
from openpilot.common.params import Params
from openpilot.starpilot.aol.intent import AolCardIntent, CRUISE_LONG_PRESS, independent_axis_requested, read_settings as read_aol_settings
from openpilot.starpilot.car.hyundai.aol import qualified_ioniq6
from openpilot.starpilot.aol.runtime import decide_axes
from openpilot.selfdrive.car.tests.test_hyundai_aol import candidate as ioniq6_candidate, state as ioniq6_state
from opendbc.car.structs import car
from openpilot.starpilot.lateral.torque_runtime import read_settings as read_torque_settings
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, parse_document
from openpilot.starpilot.lateral.torque_tuning import TorqueSource, TorqueTuning
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import (
  FEATURE_ROW_TOP, FeatureInput, FeatureRow, FeatureSettingsRequest, FeatureSettingsState, row_change, row_default,
)
from openpilot.starpilot.ui import feature_settings_compact as compact
from openpilot.starpilot.ui import feature_settings as large
from openpilot.starpilot.ui.presentation import Profile


def required_change(row: FeatureRow, direction: int = 1) -> FeatureSettingsRequest:
  request = row_change(row, direction)
  if request is None:
    raise AssertionError(f"Expected an editable row: {row.key}")
  return request


class LateralFeatureSettingsTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.params = Params(self.temp.name)
    # These action/repair cases start from an explicit saved Off preference.
    self.params.put_bool("AlwaysOnLateral", False, block=True)
    self.parked = True
    self.cp = interfaces[HYUNDAI.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_6)
    self.owner = FeatureSettingsOwner(self.params, lambda group: self.parked,
                                      vehicle_fingerprint=lambda: str(self.cp.carFingerprint), vehicle_params=lambda: self.cp)

  def row(self, page, key):
    state = self.owner.snapshot(page, parked=True, system_long=True, lateral_context=True, metric=False)
    return next(item for item in state.rows if item.key == key)

  def test_global_steering_pause_rows_units_defaults_and_angle_vehicle(self):
    from opendbc.car.ford.values import CAR as FORD
    for angle in (False, True):
      if angle:
        self.cp = interfaces[FORD.FORD_ESCAPE_MK4].get_non_essential_params(FORD.FORD_ESCAPE_MK4)
      self.params.put_bool('IsMetric', False, block=True)
      speed = self.row('aol', 'PauseLateralSpeed')
      steering = self.owner.snapshot('torque', parked=True, system_long=True, lateral_context=True, metric=False)
      self.assertFalse(any(row.key in ('PauseLateralSpeed', 'PauseLateralOnSignal', 'LateralResumeDelay') for row in steering.rows))
      self.assertTrue(speed.available)
      self.assertEqual((speed.value, speed.unit), ('0', 'mph'))
      self.assertTrue(self.owner.apply(required_change(speed)))
      stale = required_change(self.row('aol', 'PauseLateralSpeed'))
      self.params.put_bool('IsMetric', True, block=True)
      self.assertFalse(self.owner.apply(stale))
      self.assertEqual(self.row('aol', 'PauseLateralSpeed').unit, 'km/h')
      reset = row_default(self.row('aol', 'PauseLateralSpeed'))
      assert reset is not None
      self.assertTrue(self.owner.apply(reset))
      self.assertEqual(self.params.get('PauseLateralSpeed'), 0.)
      signal = self.row('aol', 'PauseLateralOnSignal')
      self.assertTrue(self.owner.apply(required_change(signal)))
      self.assertTrue(self.params.get_bool('PauseLateralOnSignal'))
      self.assertTrue(self.owner.apply(required_change(self.row('aol', 'PauseLateralOnSignal'), -1)))
      self.assertFalse(self.params.get_bool('PauseLateralOnSignal'))
      self.assertFalse(self.owner.apply(replace(required_change(self.row('aol', 'LateralResumeDelay')), value='nan')))
      Path(self.params.get_param_path('PauseLateralSpeed')).write_bytes(b'nan')
      repair = self.row('aol', 'PauseLateralSpeed')
      self.assertEqual(repair.repair_value, '0')
      request = required_change(repair)
      self.assertFalse(self.owner.apply(replace(request, value='5')))
      self.assertTrue(self.owner.apply(request))
      self.params.put('PauseLateralSpeed', 0., block=True)

  def test_default_clears_only_selected_torque_override_and_rejects_stale_source(self):
    self.assertTrue(self.owner.apply(required_change(self.row("torque", "torque:factor:value"))))
    self.assertTrue(self.owner.apply(required_change(self.row("torque", "torque:friction:value"))))
    row = self.row("torque", "torque:factor:value")
    request = row_default(row)
    assert request is not None
    self.assertEqual(request.key, "torque:factor:mode")
    self.assertEqual(request.value, "Vehicle/learned")
    tune = self.cp.lateralTuning.torque
    vehicle = TorqueTuning(TorqueSource.VEHICLE, str(self.cp.carFingerprint), tune.latAccelFactor,
                           tune.latAccelOffset, tune.friction)
    before = read_torque_settings(self.params, vehicle)
    self.assertTrue(self.owner.apply(request))
    after = read_torque_settings(self.params, vehicle)
    self.assertIsNone(after.user_factor)
    self.assertEqual(after.user_friction, before.user_friction)
    self.assertFalse(self.owner.apply(request))
    self.assertEqual(self.row("torque", "torque:factor:value").reason, "Uses the supplied vehicle tune")

  def test_torque_custom_values_and_learning_preference_are_independent(self):
    rows = self.owner.snapshot("torque", parked=True, system_long=True, lateral_context=True, metric=False).rows
    self.assertFalse(any(row.key in ("AdvancedLateralTune", "torque_adopt") for row in rows))
    factor = self.row("torque", "torque:factor:value")
    self.assertTrue(self.owner.apply(required_change(factor)))
    tune = self.cp.lateralTuning.torque
    vehicle = TorqueTuning(TorqueSource.VEHICLE, str(self.cp.carFingerprint), tune.latAccelFactor,
                           tune.latAccelOffset, tune.friction)
    saved = read_torque_settings(self.params, vehicle)
    self.assertTrue(saved.valid)
    self.assertIsNotNone(saved.user_factor)
    self.assertIsNone(saved.user_friction)
    self.assertIsNone(self.params.get("AdvancedLateralTune"))
    self.assertIsNone(self.params.get("ForceAutoTuneOff"))
    self.assertTrue(self.owner.apply(required_change(self.row("torque", "torque:factor:reset"))))
    self.assertIsNone(read_torque_settings(self.params, vehicle).user_factor)

  def test_onroad_saved_torque_edit_keeps_vehicle_binding_and_parked_repair(self):
    owner = FeatureSettingsOwner(self.params, lambda group: group != "parked_preferences",
                                 vehicle_fingerprint=lambda: str(self.cp.carFingerprint), vehicle_params=lambda: self.cp)
    page = owner.snapshot("torque", parked=False, system_long=False, lateral_context=True, metric=False,
                          configure_while_driving=True)
    factor = next(row for row in page.rows if row.key == "torque:factor:value")
    request = required_change(factor)
    self.assertTrue(factor.available)
    self.assertTrue(owner.apply(request))
    self.assertFalse(owner.apply(request))
    current = next(row for row in owner.snapshot("torque", parked=False, system_long=False,
                   lateral_context=True, metric=False, configure_while_driving=True).rows if row.key == factor.key)
    self.cp.carFingerprint = "TOYOTA_CAMRY"
    self.assertFalse(owner.apply(required_change(current)))

  def test_torque_corrupt_dependent_preserves_bytes_until_confirmed_reset(self):
    Path(self.params.get_param_path("SteerLatAccel")).write_bytes(b"nan")
    page = self.owner.snapshot("torque", parked=True, system_long=True, lateral_context=True, metric=False)
    self.assertFalse(any(row.key == "torque:factor:value" for row in page.rows))
    self.assertEqual(Path(self.params.get_param_path("SteerLatAccel")).read_bytes(), b"nan")
    reset = next(row for row in page.rows if row.key == "torque_reset")
    self.assertFalse(reset.available)
    Path(self.params.get_param_path(DOCUMENT_KEY)).write_bytes(b"bad")
    self.assertTrue(self.owner.apply(self.confirm(self.row("torque", "torque_reset"))))
    self.assertEqual(Path(self.params.get_param_path("SteerLatAccel")).read_bytes(), b"nan")
    self.assertTrue(self.row("torque", "torque:factor:value").available)

  def test_torque_corrupt_learning_preference_blocks_numeric_write(self):
    request = required_change(self.row("torque", "torque:factor:value"))
    Path(self.params.get_param_path("ForceAutoTuneOff")).write_bytes(b"maybe")
    self.assertFalse(self.owner.apply(request))
    self.assertEqual(Path(self.params.get_param_path("ForceAutoTuneOff")).read_bytes(), b"maybe")
    self.honda()
    page = self.owner.snapshot("torque", parked=True, system_long=True, lateral_context=True, metric=False)
    self.assertFalse(any(row.key == "torque:factor:value" for row in page.rows))

  def test_unreadable_saved_source_is_visible_and_not_writable(self):
    self.honda()
    path = Path(self.params.get_param_path("AolBrakePauseSpeedMps"))
    path.write_bytes(b"4")
    from openpilot.starpilot.ui import feature_settings_owner
    original = feature_settings_owner.read_saved

    def unreadable(params, key, limit):
      return (b"", False) if key == "AolBrakePauseSpeedMps" else original(params, key, limit)

    with patch.object(feature_settings_owner, "read_saved", side_effect=unreadable):
      threshold = self.row("aol", "AolBrakePauseSpeedMps")
      self.assertFalse(threshold.available)
      self.assertFalse(self.row("aol", "AlwaysOnLateral").available)
    self.assertEqual(path.read_bytes(), b"4")

  def test_torque_capability_tuple_and_parked_evidence_rechecked(self):
    request = required_change(self.row("torque", "torque:factor:value"))
    self.cp.lateralTuning.torque.latAccelFactor *= 1.1
    self.assertFalse(self.owner.apply(request))
    current = required_change(self.row("torque", "torque:factor:value"))
    self.parked = False
    self.assertFalse(self.owner.apply(current))

  @staticmethod
  def confirm(row):
    return FeatureSettingsRequest(row.key, row.source, "confirm", confirmation=True,
                                  vehicle_fingerprint=row.vehicle_fingerprint,
                                  capability=row.capability, dependencies=row.dependencies)

  def test_torque_inferred_custom_partial_and_reset_preserve_legacy(self):
    self.params.put("SteerLatAccel", self.cp.lateralTuning.torque.latAccelFactor * 1.1, block=True)
    old = Path(self.params.get_param_path("SteerLatAccel")).read_bytes()
    factor = self.row("torque", "torque:factor:value")
    self.assertEqual(factor.reason, "Uses your custom value")
    self.assertTrue(self.owner.apply(required_change(self.row("torque", "torque:friction:value"))))
    profile = parse_document(Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes())[str(self.cp.carFingerprint)]
    self.assertEqual(profile.factor.mode, "custom")
    self.assertEqual(profile.friction.mode, "custom")
    self.assertTrue(self.owner.apply(required_change(self.row("torque", "torque:factor:reset"))))
    profile = parse_document(Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes())[str(self.cp.carFingerprint)]
    self.assertEqual(profile.factor.mode, "source")
    self.assertEqual(profile.friction.mode, "custom")
    self.assertEqual(Path(self.params.get_param_path("SteerLatAccel")).read_bytes(), old)

  def test_torque_stale_source_capability_and_invalid_reset(self):
    request = required_change(self.row("torque", "torque:factor:value"))
    self.assertTrue(self.owner.apply(request))
    self.assertFalse(self.owner.apply(request))
    old_request = required_change(self.row("torque", "torque:factor:value"))
    Path(self.params.get_param_path(DOCUMENT_KEY)).write_bytes(b"bad")
    self.assertFalse(self.owner.apply(old_request))
    self.assertTrue(self.owner.apply(self.confirm(self.row("torque", "torque_reset"))))
    self.assertEqual(parse_document(Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes()), {})

  def test_torque_final_confirmation_rechecks_readability_and_fingerprint(self):
    Path(self.params.get_param_path(DOCUMENT_KEY)).write_bytes(b"bad")
    request = self.confirm(self.row("torque", "torque_reset"))
    original = self.owner.torque._readable
    with patch.object(self.owner.torque, "_readable", side_effect=lambda key: key != DOCUMENT_KEY and original(key)), \
         patch.object(self.params, "put", wraps=self.params.put) as put:
      self.assertFalse(self.owner.apply(request))
      put.assert_not_called()
    with patch.object(self.owner.torque, "vehicle_fingerprint", return_value="TOYOTA_CAMRY"), \
         patch.object(self.params, "put", wraps=self.params.put) as put:
      self.assertFalse(self.owner.apply(request))
      put.assert_not_called()

  def test_torque_basis_change_pauses_custom_until_confirmed_review(self):
    self.assertTrue(self.owner.apply(required_change(self.row("torque", "torque:factor:value"))))
    self.cp.lateralTuning.torque.latAccelFactor *= 1.05
    state = self.owner.snapshot("torque", parked=True, system_long=True, lateral_context=True, metric=False)
    self.assertTrue(any(row.key == "torque_rebase" for row in state.rows))
    review = self.row("torque", "torque_rebase")
    self.assertIn("Lateral acceleration", review.value)
    self.assertFalse(self.owner.apply(replace(self.confirm(review), confirmation=False)))
    self.assertTrue(self.owner.apply(self.confirm(review)))
    profile = parse_document(Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes())[str(self.cp.carFingerprint)]
    self.assertEqual(profile.factor.mode, "custom")
    self.cp.lateralTuning.torque.latAccelFactor *= 3
    self.assertFalse(self.owner.apply(self.confirm(self.row("torque", "torque_rebase"))))
    self.assertTrue(self.owner.apply(self.confirm(self.row("torque", "torque_reset_profile"))))
    profile = parse_document(Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes())[str(self.cp.carFingerprint)]
    self.assertEqual(profile.factor.mode, "source")

  def honda(self):
    self.cp = CarInterface.get_params(HONDA.HONDA_CIVIC_BOSCH, gen_empty_fingerprint(), [], True, False, False)
    self.cp.safetyConfigs[-1].safetyParam |= 0x20

  def test_aol_explicit_metric_to_si_write_preserves_legacy(self):
    self.honda()
    self.params.put_bool("IsMetric", True, block=True)
    self.params.put("PauseAOLOnBrake", 10.0, block=True)
    legacy = Path(self.params.get_param_path("PauseAOLOnBrake")).read_bytes()
    threshold = self.row("aol", "AolBrakePauseSpeedMps")
    self.assertEqual((threshold.value, threshold.unit), ("36.0", "km/h"))
    self.assertTrue(self.owner.apply(required_change(threshold)))
    self.assertEqual(Path(self.params.get_param_path("PauseAOLOnBrake")).read_bytes(), legacy)
    self.assertAlmostEqual(read_aol_settings(self.params).pause_brake_mps, 37 / 3.6)

  def test_aol_threshold_source_and_units_stale_release(self):
    self.honda()
    self.params.put("PauseAOLOnBrake", 10.0, block=True)
    request = required_change(self.row("aol", "AolBrakePauseSpeedMps"))
    self.params.put_bool("IsMetric", True, block=True)
    self.assertFalse(self.owner.apply(request))
    self.params.remove("IsMetric")
    self.params.put("PauseAOLOnBrake", 11.0, block=True)
    self.assertFalse(self.owner.apply(request))
    self.params.put("PauseAOLOnBrake", 10.0, block=True)
    self.params.put("AolBrakePauseSpeedMps", 1.0, block=True)
    self.assertFalse(self.owner.apply(request))

  def test_fresh_honda_without_native_bit_can_save_request_preference(self):
    self.cp = CarInterface.get_params(HONDA.HONDA_CIVIC_BOSCH, gen_empty_fingerprint(), [], True, False, False)
    self.assertEqual(self.cp.safetyConfigs[-1].safetyParam & 0x20, 0)
    master = self.row("aol", "AlwaysOnLateral")
    self.assertTrue(master.available)
    self.assertTrue(self.owner.apply(required_change(master)))
    saved = read_aol_settings(self.params)
    self.assertTrue(saved.enabled)
    self.assertTrue(independent_axis_requested(saved))
    self.cp.safetyConfigs[-1].safetyParam |= 0x20
    self.assertTrue(self.row("aol", "AlwaysOnLateral").available)

  def test_ioniq6_stock_cp_exposes_saved_requests_without_claiming_live_long(self):
    fp = gen_empty_fingerprint()
    fp[2].update({0x50: 16, 0x2A4: 24})
    fp[1].update({0x1CF: 8, 0x1AA: 16, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24,
                  0x1BA: 24, 0x1E5: 16, 0x36A: 16})
    fp[0][0x3A5] = 24
    self.cp = HyundaiInterface.get_params(HYUNDAI.HYUNDAI_IONIQ_6, fp, [], False, False, False)
    self.assertEqual(self.cp.safetyConfigs[0].safetyParam, 0x11)
    master = self.row("aol", "AlwaysOnLateral")
    self.assertTrue(master.available)
    self.assertIn("independently of cruise control", master.reason)
    self.assertIn("Toggle AOL", self.row("wheel", "wheel:LKASButtonControl").choices)
    nostalgia = self.row("vehicle", "NostalgiaMode")
    self.assertTrue(nostalgia.available)
    self.assertTrue(self.owner.apply(required_change(nostalgia)))
    self.assertTrue(self.params.get_bool("NostalgiaMode"))
    self.assertTrue(self.owner.apply(required_change(master)))
    self.assertTrue(read_aol_settings(self.params).enabled)
    self.assertFalse(self.cp.openpilotLongitudinalControl)
    tagged = build_ioniq6_hda2_long_candidate(self.cp, fp)
    self.assertIsNotNone(tagged)
    self.cp = tagged
    self.assertTrue(self.row("vehicle", "NostalgiaMode").available)
    tagged.safetyConfigs[0].safetyParam |= 0x800
    self.cp = tagged
    self.assertTrue(self.row("aol", "AlwaysOnLateral").available)
    self.assertIn("Toggle AOL", self.row("wheel", "wheel:LKASButtonControl").choices)

  def test_ioniq5_left_paddle_setting_uses_existing_long_configuration(self):
    from openpilot.starpilot.tests.test_nostalgia import _ioniq5_params
    self.cp = _ioniq5_params()
    row = self.row("vehicle", "NostalgiaMode")
    self.assertTrue(row.available)
    self.assertEqual(row.label, "Left Paddle Longitudinal Pause")
    self.assertTrue(self.owner.apply(required_change(row)))
    self.assertTrue(self.params.get_bool("NostalgiaMode"))

  def test_ioniq6_nostalgia_is_independent_of_aol_master_and_exact_source(self):
    fp = gen_empty_fingerprint()
    fp[2].update({0x50: 16, 0x2A4: 24})
    fp[1].update({0x1CF: 8, 0x1AA: 16, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24,
                  0x1BA: 24, 0x1E5: 16, 0x36A: 16})
    fp[0][0x3A5] = 24
    stock = HyundaiInterface.get_params(HYUNDAI.HYUNDAI_IONIQ_6, fp, [], False, False, False)
    self.cp = stock
    self.assertTrue(self.row("vehicle", "NostalgiaMode").available)
    tagged = build_ioniq6_hda2_long_candidate(stock, fp)
    self.assertIsNotNone(tagged)
    self.cp = tagged
    self.assertTrue(self.row("vehicle", "NostalgiaMode").available)
    tagged.safetyConfigs[0].safetyParam |= 0x800
    self.cp = tagged
    row = self.row("vehicle", "NostalgiaMode")
    self.assertEqual(row.value, "Off")
    self.assertTrue(row.available)
    self.assertFalse(self.params.get_bool("AlwaysOnLateral"))
    self.assertTrue(self.owner.apply(required_change(row)))
    self.assertTrue(self.params.get_bool("NostalgiaMode"))
    self.assertFalse(self.params.get_bool("AlwaysOnLateral"))
    old = self.row("vehicle", "NostalgiaMode")
    self.params.put_bool("NostalgiaMode", False, block=True)
    self.assertFalse(self.owner.apply(required_change(old)))
    path = Path(self.params.get_param_path("NostalgiaMode"))
    path.write_bytes(b"01")
    repair = self.row("vehicle", "NostalgiaMode")
    self.assertEqual(repair.repair_value, "Off")
    self.assertTrue(self.owner.apply(required_change(repair)))
    self.assertEqual(path.read_bytes(), b"0")
    on = required_change(self.row("vehicle", "NostalgiaMode"))
    self.parked = False
    self.assertFalse(self.owner.apply(on))

  def test_aol_invalid_source_explicit_repair_and_master_guard(self):
    self.honda()
    path = Path(self.params.get_param_path("AolBrakePauseSpeedMps"))
    path.write_bytes(b"bad")
    master = self.row("aol", "AlwaysOnLateral")
    self.assertFalse(master.available)
    self.params.put_bool("AlwaysOnLateral", True, block=True)
    self.assertTrue(self.owner.apply(required_change(self.row("aol", "AlwaysOnLateral"))))
    self.assertFalse(self.params.get_bool("AlwaysOnLateral"))
    repair = self.row("aol", "AolBrakePauseSpeedMps")
    self.assertEqual(repair.repair_value, "0")
    self.assertTrue(self.owner.apply(required_change(repair)))
    self.assertEqual(self.params.get("AolBrakePauseSpeedMps"), 0.0)
    master = self.row("aol", "AlwaysOnLateral")
    self.assertTrue(self.owner.apply(required_change(master)))
    self.assertTrue(read_aol_settings(self.params).enabled)

  def test_wheel_page_moves_assignments_without_moving_feature_settings(self):
    self.honda()
    def snapshot(page):
      return self.owner.snapshot(page, parked=True, system_long=True, lateral_context=True, metric=False)
    self.assertIn("wheel", [row.page for row in snapshot("hub").rows])
    self.assertEqual({row.key for row in snapshot("aol").rows},
                     {"AlwaysOnLateral", "AolBrakePauseSpeedMps", "PauseLateralSpeed", "PauseLateralOnSignal", "LateralResumeDelay"})
    self.assertIn("wheel:LKASButtonControl", {row.key for row in snapshot("wheel").rows})
    self.cp = interfaces["TOYOTA_COROLLA_TSS2"].get_non_essential_params("TOYOTA_COROLLA_TSS2")
    self.assertFalse(any(row.key.endswith("ModeButtonControl") for row in snapshot("wheel").rows))

  def test_aol_button_intent_and_safety_tuple(self):
    self.honda()
    self.params.put("LKASButtonControl", 0, block=True)
    row = self.row("wheel", "wheel:LKASButtonControl")
    request = FeatureSettingsRequest(row.key, row.source, "Pause steering", capability=row.capability,
                                     vehicle_fingerprint=row.vehicle_fingerprint, dependencies=row.dependencies)
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(read_aol_settings(self.params).lkas_action, 3)
    self.assertFalse(any(item.key == "CancelButtonControl" for item in
                         self.owner.snapshot("aol", parked=True, system_long=True, lateral_context=True, metric=False).rows))
    main = self.row("wheel", "wheel:MainCruiseButtonControl")
    self.cp.safetyConfigs[-1].safetyParam &= ~0x20
    self.assertFalse(self.owner.apply(required_change(main)))

  def test_wheel_assignment_can_be_saved_in_drive_without_enabling_aol_master(self):
    self.honda()
    self.params.put("LKASButtonControl", 0, block=True)
    self.parked = False
    self.owner = FeatureSettingsOwner(self.params, lambda group: group == "preferences",
                                      vehicle_fingerprint=lambda: str(self.cp.carFingerprint), vehicle_params=lambda: self.cp)
    state = self.owner.snapshot("wheel", parked=False, system_long=False, lateral_context=False, metric=False)
    row = next(row for row in state.rows if row.key == "wheel:LKASButtonControl")
    self.assertTrue(row.available)
    request = FeatureSettingsRequest(row.key, row.source, "Pause steering", capability=row.capability,
                                     vehicle_fingerprint=row.vehicle_fingerprint, dependencies=row.dependencies)
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(read_aol_settings(self.params).lkas_action, 3)
    self.assertFalse(self.owner.snapshot("aol", parked=False, system_long=False,
                                         lateral_context=False, metric=False).rows[0].available)
    main = next(row for row in self.owner.snapshot("wheel", parked=False, system_long=False,
                                                  lateral_context=False, metric=False).rows if row.key == "wheel:MainCruiseButtonControl")
    self.cp.safetyConfigs[-1].safetyParam &= ~0x20
    self.assertFalse(self.owner.apply(required_change(main)))

  def test_ioniq_distance_saved_pause_reaches_only_native_acknowledged_axes(self):
    stock, long_cp = ioniq6_candidate(False)
    self.cp = stock
    self.params.put_bool("AlwaysOnLateral", True, block=True)
    self.assertIn("Toggle AOL", self.row("wheel", "wheel:LKASButtonControl").choices)
    self.assertIn("Toggle AOL", self.row("wheel", "wheel:MainCruiseButtonControl").choices)
    cases = (("DistanceButtonControl", "Pause steering", 1, (False, True), True),
             ("LongDistanceButtonControl", "Pause longitudinal", CRUISE_LONG_PRESS, (True, False), True),
             ("VeryLongDistanceButtonControl", "Pause steering", CRUISE_LONG_PRESS * 5, (False, True), True),
             ("DistanceButtonControl", "Pause steering", 1, (False, True), False))
    for key, choice, ticks, expected, aol_on in cases:
      with self.subTest(key=key, aol_on=aol_on):
        self.params.put_bool("AlwaysOnLateral", aol_on, block=True)
        row = self.row("wheel", "wheel:" + key)
        self.assertTrue(row.available)
        self.assertTrue({"Off", "Pause steering", "Pause longitudinal"}.issubset(row.choices))
        request = FeatureSettingsRequest("wheel:" + key, row.source, choice, vehicle_fingerprint=row.vehicle_fingerprint,
                                         capability=row.capability, dependencies=row.dependencies)
        self.assertTrue(self.owner.apply(request))
        slot = ("DistanceButtonControl", "LongDistanceButtonControl", "VeryLongDistanceButtonControl").index(key)
        self.assertEqual(read_aol_settings(self.params).distance_actions[slot], 3 if choice == "Pause steering" else 4)
        self.cp = long_cp.as_reader().as_builder()
        self.cp.safetyConfigs[0].safetyParam |= 0x800
        self.assertTrue(qualified_ioniq6(self.cp))
        intent = AolCardIntent(read_aol_settings(self.params), explicit_latch=True)
        cs = ioniq6_state()
        intent.update(cs)
        if aol_on:
          cs.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=True)]
          intent.update(cs)
          cs.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=False)]
          intent.update(cs)
        cs.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.gapAdjustCruise, pressed=True)]
        intent.update(cs)
        cs.buttonEvents = []
        for _ in range(ticks - 1):
          intent.update(cs)
        cs.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.gapAdjustCruise, pressed=False)]
        intent.update(cs)
        latch, pause_lat, pause_long = intent.output(cs)
        self.assertEqual(latch, aol_on)
        self.assertEqual((not pause_lat, not pause_long), expected)
        wire = SimpleNamespace(allowedLatch=latch, pauseLateral=pause_lat, pauseLongitudinal=pause_long)
        def axes(native, aol_on=aol_on, wire=wire, cs=cs):
          return decide_axes(standard_lateral=not aol_on, standard_longitudinal=True, intent=wire,
                             native=native, car_state=cs, initialized=True, model_ready=True,
                             no_entry=False, immediate_disable=False, dm_lockout=False, pause_brake_mps=0.0)

        missing = axes(None)
        self.assertEqual((missing.desired_lateral, missing.desired_longitudinal), expected)
        self.assertEqual(missing.mode, "off")
        native = SimpleNamespace(requestedLateral=expected[0], requestedLongitudinal=expected[1],
                                 lateralAllowed=True, longitudinalAllowed=True)
        active = axes(native)
        self.assertEqual((active.lateral_active, active.longitudinal_active), expected)
        self.cp = stock
        reset = self.row("wheel", "wheel:" + key)
        self.assertTrue(self.owner.apply(FeatureSettingsRequest("wheel:" + key, reset.source, "Off",
                                                               vehicle_fingerprint=reset.vehicle_fingerprint,
                                                               capability=reset.capability, dependencies=reset.dependencies)))

  def test_ioniq_distance_canonical_repair_and_fixed_gestures(self):
    self.cp, _ = ioniq6_candidate(False)
    state = self.owner.snapshot("wheel", parked=True, system_long=True, lateral_context=True, metric=False)
    self.assertTrue({"wheel:LKASButtonControl", "wheel:MainCruiseButtonControl"} <= {row.key for row in state.rows})
    key = "DistanceButtonControl"
    path = Path(self.params.get_param_path(key))
    for raw in (b"03", b"99", b"bad"):
      path.write_bytes(raw)
      row = self.row("wheel", "wheel:" + key)
      self.assertEqual(row.value, "Invalid saved action")
      self.assertEqual(row.repair_value, "Off")
      self.assertTrue(self.owner.apply(required_change(row)))
      self.assertEqual(path.read_bytes(), b"0")

  def test_honda_distance_toggle_remains_editable_and_ioniq_stage_revokes(self):
    self.honda()
    key = "DistanceButtonControl"
    self.params.put(key, 9, block=True)
    row = self.row("wheel", "wheel:" + key)
    self.assertEqual(row.value, "Toggle AOL")
    request = FeatureSettingsRequest(row.key, row.source, "Pause steering", capability=row.capability)
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(Path(self.params.get_param_path(key)).read_bytes(), b"3")
    self.cp, _ = ioniq6_candidate(False)
    request = required_change(self.row("wheel", "wheel:" + key))
    self.cp.carVin = "changed"
    self.assertFalse(self.owner.apply(request))
    self.assertEqual(Path(self.params.get_param_path(key)).read_bytes(), b"3")

  def test_native_large_and_compact_child_actions(self):
    hub = self.owner.snapshot("hub", parked=True, system_long=True, lateral_context=True, metric=False)
    actions = []
    control = FeatureInput(actions.append)
    index = next(i for i, row in enumerate(hub.rows) if row.page == "torque")
    hub = replace(hub, scroll=index)
    control.press(650, 300, hub)
    control.release(650, 300, hub)
    self.assertEqual(actions[0].row.page, "torque")
    class Button:
      def __init__(self, text, value):
        self.text, self.value, self.click = text, value, None
      def set_click_callback(self, callback):
        self.click = callback
      def set_enabled(self, enabled):
        self.enabled = enabled
    row = self.row("torque", "torque:factor:value")
    refreshed = []
    session = Mock(feature_request=self.owner.apply)
    adapter = compact.FeatureSettingsCompact(session)
    with patch.object(compact, "BigButton", Button):
      buttons = adapter._editable(row, lambda: refreshed.append(True), Mock())
      next(button for button in buttons if button.text.endswith("+")).click()
    self.assertEqual(refreshed, [True])
    source = Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes()
    self.assertEqual(parse_document(source)[str(self.cp.carFingerprint)].factor.mode, "custom")
    row = self.row("torque", "torque:factor:reset")
    action = FeatureInput.target(1900, FEATURE_ROW_TOP + 77, FeatureSettingsState(page="torque", rows=(row,)))
    self.assertIsNotNone(action)
    assert action is not None
    self.assertEqual(action.kind, "change")
    self.assertEqual(action.row, row)
    request = required_change(row, action.direction)
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(parse_document(Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes())[
      str(self.cp.carFingerprint)].factor.mode, "source")

  def test_large_repair_labels_match_requested_values(self):
    text = []
    fonts = SimpleNamespace(profile=Profile.LARGE,
                            draw=lambda value, *_args: text.append(value),
                            measure=lambda value, _role, size: SimpleNamespace(width=len(value) * size * .5),
                            vertical_ink=lambda _value, _role, size: (size * .2, size * .8))
    state = FeatureSettingsState(rows=(
      FeatureRow("AolBrakePauseSpeedMps", "Brake pause below", "Invalid", available=True, repair_value="0"),
      FeatureRow("wheel:LKASButtonControl", "LKAS press", "Unsupported", available=True, repair_value="Off"),
    ))
    with patch.object(large.rl, "draw_rectangle_rounded"), patch.object(large.rl, "draw_rectangle_rounded_lines_ex"), \
         patch.object(large.rl, "draw_line"), patch.object(large.rl, "draw_line_ex"), patch.object(large.rl, "draw_circle"), \
         patch.object(large.clip, "begin_scissor_mode"), \
         patch.object(large.clip, "end_scissor_mode"):
      large.FeatureSettingsView(Mock(spec=BitmapFonts, **vars(fonts))).render(state)
    self.assertIn("Set 0", text)
    self.assertIn("Set Off", text)

  def test_starpilot_tuning_help_is_visible_in_large_native_settings(self):
    class Fonts:
      profile = Profile.LARGE

      def __init__(self):
        self.text = []

      def draw(self, value, *args, **kwargs):
        self.text.append(value)

      def measure(self, value, _role, size):
        return SimpleNamespace(width=len(value) * size * .5)

      def vertical_ink(self, _value, _role, size):
        return size * .2, size * .8

    self.assertTrue(self.owner.apply(required_change(self.row("torque", "LateralControllerSelection"))))
    fonts = Fonts()
    state = self.owner.snapshot("torque", parked=True, system_long=True, lateral_context=True, metric=False)
    state = replace(state, scroll=next(i for i, row in enumerate(state.rows) if row.label == "Use StarPilot Auto Learn"))
    with (patch.object(large.rl, "draw_rectangle_rounded"), patch.object(large.rl, "draw_line"),
          patch.object(large, "draw_rounded_stroke"), patch.object(large, "draw_aether_toggle"),
          patch.object(large, "draw_settings_header"),
          patch.object(large.clip, "begin_scissor_mode"), patch.object(large.clip, "end_scissor_mode")):
      large.FeatureSettingsView(Mock(spec=BitmapFonts, profile=Profile.LARGE, measure=fonts.measure,
                                     draw=fonts.draw, vertical_ink=fonts.vertical_ink)).render(state)
    self.assertIn("Use StarPilot Auto Learn", fonts.text)
    controller = self.row("torque", "LateralControllerSelection")
    self.assertIn("https://firestar.link/discord", controller.reason)
    self.assertEqual(state.rows[state.scroll].reason,
                     "StarPilot’s FLM tuning will attempt to automatically correct torque profiles as you drive.")
    self.assertTrue(state.rows[state.scroll].available)
    self.assertEqual(FeatureInput.target(650, 300, state).row, state.rows[state.scroll])
    self.assertTrue(self.owner.apply(required_change(self.row("torque", "LateralControllerSelection"))))
    standard = self.owner.snapshot("torque", parked=True, system_long=True, lateral_context=True, metric=False)
    self.assertTrue(any(row.label == "Use StarPilot Auto Learn" for row in standard.rows))


if __name__ == "__main__":
  unittest.main()
