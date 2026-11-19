"""Galaxy device and longitudinal preference admission, exact saves and drive-start selection."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
import tempfile
import unittest
from unittest.mock import Mock, patch

from openpilot.starpilot.galaxy.device_settings import (CALIBRATION_RESET, DRIVER_MONITORING_RESET, STARPORT_EXPERIMENT_PREFIX,
                                                          STARPORT_EXPERIMENTS_KEY,
                                                          STARPORT_HEVC_KEY, STARPORT_KEYFRAME_TEST_KEY,
                                                          STARPORT_TALL_HEVC_KEY, STARPORT_UNACKED_VIDEO_KEY, STARPORT_PATIENT_TCP_KEY,
                                                          STARPORT_WIFI_KEY, DeviceSettingsOwner)
from openpilot.starpilot.galaxy.settings import AuthorityContext, ContextSource, SettingsChanged, SettingsGateway, PAGES
from openpilot.starpilot.longitudinal.voacc import KEY, selected
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest


class FileParams:
  def __init__(self, root):
    self.root = Path(root) / "d"
    self.root.mkdir()

  def get_param_path(self, key):
    return str(self.root / key)

  def get_bool(self, key):
    try:
      return Path(self.get_param_path(key)).read_bytes() == b"1"
    except FileNotFoundError:
      return False

  def remove(self, key):
    try:
      Path(self.get_param_path(key)).unlink()
    except FileNotFoundError:
      pass

  def put_bool(self, key, value, block=False):
    Path(self.get_param_path(key)).write_bytes(b"1" if value else b"0")


class FakeStarPort:
  def __init__(self, enabled=False, running=True):
    self.enabled, self.running, self.hevc = enabled, running, False
    self.keyframe_test = self.tall_hevc = self.unacked_video = self.patient_tcp = False
    self.experiments, self.rules = False, []

  def call(self, command, **payload):
    if not self.running:
      raise RuntimeError("Android Auto service is not running (turn on Bluetooth)")
    if command == "set_dedicated_wifi":
      self.enabled = payload["enabled"]
      return {"ok": True}
    if command == "set_allow_hevc":
      self.hevc = payload["enabled"]
      return {"ok": True}
    if command == "set_keyframe_test":
      self.keyframe_test = payload["enabled"]
      return {"ok": True}
    if command == "set_tall_hevc":
      self.tall_hevc = payload["enabled"]
      return {"ok": True}
    if command == "set_patient_tcp":
      self.patient_tcp = payload["enabled"]
      return {"ok": True}
    if command == "set_unacked_video":
      self.unacked_video = payload["enabled"]
      return {"ok": True}
    if command == "set_head_unit_experiments":
      self.experiments = payload["enabled"]
      return {"ok": True}
    if command == "set_head_unit_experiment_rule":
      self.rules = sorted(set(self.rules) | {payload["rule"]} if payload["enabled"] else set(self.rules) - {payload["rule"]})
      return {"ok": True, "changed": True}
    return {"ok": True, "status": {"dedicated_wifi_interface": self.enabled, "allow_hevc": self.hevc, "keyframe_test": self.keyframe_test,
                                   "tall_hevc": self.tall_hevc, "unacked_video": self.unacked_video, "patient_tcp": self.patient_tcp,
                                   "head_unit_experiments": self.experiments, "head_unit_experiment_rules": self.rules}}


class DeviceSettingsTest(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.params = FileParams(directory.name)
    self.path = Path(self.params.get_param_path(KEY))
    self.cp = SimpleNamespace(openpilotLongitudinalControl=True, notCar=False, dashcamOnly=False, passive=False)
    self.parked = True
    self.owner = DeviceSettingsOwner(self.params, lambda: self.parked)

  def test_pages_separate_driving_controls_services_and_resets(self):
    from openpilot.starpilot.longitudinal.enhanced_stop_hold import KEY as HOLD_KEY
    service = SimpleNamespace(call=Mock(side_effect=RuntimeError("off")))
    owner = DeviceSettingsOwner(self.params, lambda: self.parked, starport=lambda: service)
    self.assertEqual([row.key for row in owner.snapshot("driving_options").rows], [KEY, HOLD_KEY])
    self.assertEqual([row.key for row in owner.snapshot("resets").rows], [DRIVER_MONITORING_RESET, CALIBRATION_RESET])
    service.call.assert_not_called()
    self.assertEqual(owner.snapshot("starport_options").rows, ())
    self.assertIn("developer", PAGES)

  def test_always_hold_default_registry_exact_save_and_parked_authority(self):
    from openpilot.common.params import Params, ParamKeyType
    from openpilot.starpilot.longitudinal.enhanced_stop_hold import KEY as HOLD_KEY, LABEL, DESCRIPTION, selected as hold_selected
    params = Params(str(self.params.root.parent / "hold-params"))
    self.assertTrue(params.check_key(HOLD_KEY))
    self.assertEqual(params.get_type(HOLD_KEY), ParamKeyType.BOOL)
    self.assertIs(params.get_default_value(HOLD_KEY), False)
    owner = DeviceSettingsOwner(params, lambda: self.parked)
    row = next(row for row in owner.snapshot("driving_options").rows if row.key == HOLD_KEY)
    self.assertEqual((row.label, row.value, row.reason), (LABEL, "Off", DESCRIPTION))
    self.assertTrue(owner.apply(FeatureSettingsRequest(HOLD_KEY, row.source, "On")))
    self.assertTrue(hold_selected(params))
    self.assertFalse(owner.apply(FeatureSettingsRequest(HOLD_KEY, row.source, "Off")))
    self.parked = False
    self.assertFalse(next(row for row in owner.snapshot("driving_options").rows if row.key == HOLD_KEY).available)
    self.assertFalse(owner.apply(FeatureSettingsRequest(HOLD_KEY, b"1", "Off")))
    self.parked = True
    self.assertTrue(owner.apply(FeatureSettingsRequest(HOLD_KEY, b"1", "Off")))
    self.assertFalse(hold_selected(params))
    path = Path(params.get_param_path(HOLD_KEY))
    for raw in (b"", b"true", b"1\n", b"1" * 9):
      path.write_bytes(raw)
      self.assertFalse(hold_selected(params))
      self.assertFalse(owner.apply(FeatureSettingsRequest(HOLD_KEY, raw, "On")))

  def test_default_off_strict_selection_and_vehicle_admission(self):
    self.assertEqual(self.owner.snapshot("driving_options").rows[0].value, "Off")
    self.assertFalse(selected(self.params, self.cp))
    for raw in (b"0", b"", b"true", b"1\n", b"1" * 9):
      with self.subTest(raw=raw):
        self.path.write_bytes(raw)
        self.assertFalse(selected(self.params, self.cp))
    self.path.write_bytes(b"1")
    self.assertTrue(selected(self.params, self.cp))
    self.assertFalse(selected(self.params, None))
    self.assertFalse(selected(self.params, SimpleNamespace()))
    for field, value in (("openpilotLongitudinalControl", False), ("notCar", True),
                         ("dashcamOnly", True), ("passive", True)):
      with self.subTest(field=field):
        cp = SimpleNamespace(**{**vars(self.cp), field: value})
        self.assertFalse(selected(self.params, cp))

  def test_built_native_registry_and_actual_params_save(self):
    from openpilot.common.params import Params, ParamKeyType
    params = Params(str(self.params.root.parent / "native-params"))
    self.assertTrue(params.check_key(KEY))
    self.assertEqual(params.get_type(KEY), ParamKeyType.BOOL)
    self.assertIs(params.get_default_value(KEY), False)
    owner = DeviceSettingsOwner(params, lambda: True)
    row = owner.snapshot("driving_options").rows[0]
    self.assertEqual(row.value, "Off")
    self.assertTrue(owner.apply(FeatureSettingsRequest(KEY, row.source, "On")))
    self.assertTrue(params.get_bool(KEY))
    self.assertTrue(selected(params, self.cp))

  def test_exact_save_rejects_races_invalid_values_and_onroad(self):
    self.assertTrue(self.owner.apply(FeatureSettingsRequest(KEY, None, "On")))
    self.assertEqual(self.path.read_bytes(), b"1")
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(KEY, None, "Off")))
    self.parked = False
    self.assertFalse(self.owner.snapshot("driving_options").rows[0].available)
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(KEY, b"1", "Off")))
    self.parked = True
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(KEY, b"1", "yes")))
    self.assertFalse(self.owner.apply(FeatureSettingsRequest("AlwaysOnLateral", None, "On")))
    self.assertTrue(self.owner.apply(FeatureSettingsRequest(KEY, b"1", "Off")))
    self.assertEqual(self.path.read_bytes(), b"0")
    self.path.write_bytes(b"garbage")
    self.assertFalse(self.owner.snapshot("driving_options").rows[0].available)
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(KEY, b"garbage", "On")))

  def test_real_gateway_preview_save_default_and_authority_recheck(self):
    context = SimpleNamespace(value=AuthorityContext(True, None, None))
    context.sample = lambda: context.value
    gateway = SettingsGateway(self.params, cast(ContextSource, context), clock=lambda: 10.)
    token, generation = "session", b"generation"

    def preview(value):
      page = gateway.page("developer", token, generation)
      self.assertEqual(page["rows"][0]["label"], "Use VOACC")
      self.assertEqual(page["rows"][0]["defaultValue"], "Off")
      self.assertIn("next drive", page["rows"][0]["reason"])
      return gateway.preview(page["view"], 0, 0, token, generation, value=value)["intent"]

    intent = preview("On")
    self.assertTrue(gateway.confirm(intent, token, generation))
    self.assertEqual(self.path.read_bytes(), b"1")
    intent = preview("Off")
    context.value = replace(context.value, parked=False)
    with self.assertRaises(SettingsChanged):
      gateway.confirm(intent, token, generation)
    self.assertEqual(self.path.read_bytes(), b"1")
    context.value = replace(context.value, parked=True)
    page = gateway.page("developer", token, generation)
    intent = gateway.preview(page["view"], 0, 0, token, generation, reset_default=True)
    self.assertTrue(gateway.confirm(intent["intent"], token, generation))
    self.assertEqual(self.path.read_bytes(), b"0")
    intent = preview("On")
    with self.assertRaises(SettingsChanged):
      gateway.confirm(intent, token, generation, session_valid=lambda: False)

  def test_developer_driving_rows_keep_owner_and_parked_guards(self):
    from openpilot.starpilot.longitudinal.enhanced_stop_hold import KEY as HOLD_KEY
    from openpilot.starpilot.galaxy.settings import SettingsUnavailable, _onroad_preference
    context = SimpleNamespace(value=AuthorityContext(True, None, None))
    context.sample = lambda: context.value
    gateway = SettingsGateway(self.params, cast(ContextSource, context), clock=lambda: 10.)
    token, generation = "session", b"generation"
    with self.assertRaises(SettingsUnavailable):
      gateway.page("driving_options", token, generation)
    for index, key in enumerate((KEY, HOLD_KEY)):
      with self.subTest(key=key):
        self.assertFalse(_onroad_preference("developer", key))
        page = gateway.page("developer", token, generation)
        row = page["rows"][index]
        self.assertEqual(row["defaultValue"], "Off")
        intent = gateway.preview(page["view"], index, 0, token, generation, value="On")["intent"]
        self.assertTrue(gateway.confirm(intent, token, generation))
        self.assertEqual(Path(self.params.get_param_path(key)).read_bytes(), b"1")
        page = gateway.page("developer", token, generation)
        intent = gateway.preview(page["view"], index, 0, token, generation, value="Off")["intent"]
        context.value = replace(context.value, parked=False)
        with self.assertRaises(SettingsChanged):
          gateway.confirm(intent, token, generation)
        self.assertFalse(gateway.page("developer", token, generation)["rows"][index]["available"])
        context.value = replace(context.value, parked=True, editing_saved_vehicle=True)
        self.assertFalse(gateway.page("developer", token, generation)["rows"][index]["available"])
        context.value = replace(context.value, editing_saved_vehicle=False)
        page = gateway.page("developer", token, generation)
        intent = gateway.preview(page["view"], index, 0, token, generation, reset_default=True)["intent"]
        self.assertTrue(gateway.confirm(intent, token, generation))
        self.assertEqual(Path(self.params.get_param_path(key)).read_bytes(), b"0")
    self.assertEqual(gateway.page("developer", token, generation)["rows"][2]["label"], "Speed display adjustment")

  def test_starport_stay_on_wifi_toggle(self):
    self.assertNotIn(STARPORT_WIFI_KEY, [row.key for row in self.owner.snapshot("starport_options").rows])
    service = FakeStarPort()
    owner = DeviceSettingsOwner(self.params, lambda: self.parked, starport=lambda: service)
    row = next(row for row in owner.snapshot("starport_options").rows if row.key == STARPORT_WIFI_KEY)
    self.assertEqual((row.value, row.choices, row.available), ("Off", ("Off", "On"), True))
    self.assertTrue(owner.apply(FeatureSettingsRequest(STARPORT_WIFI_KEY, row.source, "On")))
    self.assertTrue(service.enabled)
    self.assertFalse(owner.apply(FeatureSettingsRequest(STARPORT_WIFI_KEY, row.source, "Off")))  # stale view
    self.assertFalse(owner.apply(FeatureSettingsRequest(STARPORT_WIFI_KEY, b"1", "maybe")))
    self.parked = False
    self.assertFalse(next(row for row in owner.snapshot("starport_options").rows if row.key == STARPORT_WIFI_KEY).available)
    self.assertFalse(owner.apply(FeatureSettingsRequest(STARPORT_WIFI_KEY, b"1", "Off")))
    self.parked = True
    self.assertTrue(owner.apply(FeatureSettingsRequest(STARPORT_WIFI_KEY, b"1", "Off")))
    self.assertFalse(service.enabled)
    service.running = False
    self.assertNotIn(STARPORT_WIFI_KEY, [row.key for row in owner.snapshot("starport_options").rows])
    self.assertFalse(owner.apply(FeatureSettingsRequest(STARPORT_WIFI_KEY, b"0", "On")))

  def test_driver_monitoring_reset_matches_native_semantics_and_guards_preview(self):
    self.params.put_bool("IsRhdDetected", True, block=True)
    row = next(row for row in self.owner.snapshot("resets").rows if row.key == DRIVER_MONITORING_RESET)
    self.assertTrue(row.available)
    self.assertTrue(self.owner.apply(FeatureSettingsRequest(DRIVER_MONITORING_RESET, b"1", "confirm", confirmation=True)))
    self.assertFalse(Path(self.params.get_param_path("IsRhdDetected")).exists())
    self.assertEqual(Path(self.params.get_param_path("OnroadCycleRequested")).read_bytes(), b"1")

    self.params.put_bool("IsRhdDetected", True, block=True)
    self.params.put_bool("IsDriverViewEnabled", True, block=True)
    row = next(row for row in self.owner.snapshot("resets").rows if row.key == DRIVER_MONITORING_RESET)
    self.assertFalse(row.available)
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(DRIVER_MONITORING_RESET, b"1", "confirm", confirmation=True)))
    self.assertTrue(Path(self.params.get_param_path("IsRhdDetected")).exists())

  def test_starport_section_has_no_legacy_setup_switch(self):
    self.assertEqual(self.owner.snapshot("starport_options").rows, ())
    service = FakeStarPort()
    owner = DeviceSettingsOwner(self.params, lambda: self.parked, starport=lambda: service)
    rows = owner.snapshot("starport_options").rows
    keys = [row.key for row in rows]
    self.assertEqual(keys[0], STARPORT_WIFI_KEY)
    self.assertNotIn("StarPortNewSetup", keys)

  def test_allow_h265_toggle_defaults_off_and_follows_the_daemon(self):
    service = FakeStarPort()
    owner = DeviceSettingsOwner(self.params, lambda: self.parked, starport=lambda: service)
    rows = owner.snapshot("starport_options").rows
    keys = [row.key for row in rows]
    self.assertEqual(keys[:keys.index(STARPORT_HEVC_KEY) + 1],
                     [STARPORT_WIFI_KEY, STARPORT_HEVC_KEY])
    row = rows[keys.index(STARPORT_HEVC_KEY)]
    self.assertEqual((row.value, row.default_value, row.available), ("Off", "Off", True))
    self.assertTrue(owner.apply(FeatureSettingsRequest(STARPORT_HEVC_KEY, row.source, "On")))
    self.assertTrue(service.hevc)
    self.assertFalse(owner.apply(FeatureSettingsRequest(STARPORT_HEVC_KEY, row.source, "Off")))  # stale view
    self.parked = False
    self.assertFalse(owner.apply(FeatureSettingsRequest(STARPORT_HEVC_KEY, b"1", "Off")))
    self.parked = True
    service.running = False
    self.assertFalse(owner.apply(FeatureSettingsRequest(STARPORT_HEVC_KEY, b"1", "Off")))

  def test_keyframe_test_defaults_off_and_follows_the_daemon(self):
    service = FakeStarPort()
    owner = DeviceSettingsOwner(self.params, lambda: self.parked, starport=lambda: service)
    rows = owner.snapshot("starport_options").rows
    keys = [row.key for row in rows]
    self.assertEqual(keys.index(STARPORT_KEYFRAME_TEST_KEY), keys.index(STARPORT_HEVC_KEY) + 1)
    row = rows[keys.index(STARPORT_KEYFRAME_TEST_KEY)]
    self.assertEqual((row.value, row.default_value, row.available), ("Off", "Off", True))
    self.assertTrue(owner.apply(FeatureSettingsRequest(STARPORT_KEYFRAME_TEST_KEY, row.source, "On")))
    self.assertTrue(service.keyframe_test)
    self.assertFalse(service.hevc)
    self.assertFalse(owner.apply(FeatureSettingsRequest(STARPORT_KEYFRAME_TEST_KEY, row.source, "Off")))  # stale view
    self.parked = False
    self.assertFalse(owner.apply(FeatureSettingsRequest(STARPORT_KEYFRAME_TEST_KEY, b"1", "Off")))
    self.assertNotIn(STARPORT_KEYFRAME_TEST_KEY, [row.key for row in DeviceSettingsOwner(self.params, lambda: True).snapshot("starport_options").rows])

  def test_tall_hevc_defaults_off_and_follows_the_daemon(self):
    service = FakeStarPort()
    owner = DeviceSettingsOwner(self.params, lambda: self.parked, starport=lambda: service)
    rows = owner.snapshot("starport_options").rows
    keys = [row.key for row in rows]
    self.assertEqual(keys.index(STARPORT_TALL_HEVC_KEY), keys.index(STARPORT_KEYFRAME_TEST_KEY) + 1)
    row = rows[keys.index(STARPORT_TALL_HEVC_KEY)]
    self.assertEqual((row.value, row.default_value, row.available), ("Off", "Off", True))
    self.assertTrue(owner.apply(FeatureSettingsRequest(STARPORT_TALL_HEVC_KEY, row.source, "On")))
    self.assertTrue(service.tall_hevc)
    self.assertFalse(service.keyframe_test or service.hevc)
    self.assertFalse(owner.apply(FeatureSettingsRequest(STARPORT_TALL_HEVC_KEY, row.source, "Off")))  # stale view

  def test_unacked_video_defaults_off_and_follows_the_daemon(self):
    service = FakeStarPort()
    owner = DeviceSettingsOwner(self.params, lambda: self.parked, starport=lambda: service)
    rows = owner.snapshot("starport_options").rows
    keys = [row.key for row in rows]
    row = rows[keys.index(STARPORT_UNACKED_VIDEO_KEY)]
    self.assertEqual((row.value, row.default_value, row.available), ("Off", "Off", True))
    self.assertTrue(owner.apply(FeatureSettingsRequest(STARPORT_UNACKED_VIDEO_KEY, row.source, "On")))
    self.assertTrue(service.unacked_video)
    self.assertFalse(service.hevc or service.tall_hevc or service.experiments)

  def test_patient_connect_defaults_off_and_follows_the_daemon(self):
    service = FakeStarPort()
    owner = DeviceSettingsOwner(self.params, lambda: self.parked, starport=lambda: service)
    rows = owner.snapshot("starport_options").rows
    keys = [row.key for row in rows]
    row = rows[keys.index(STARPORT_PATIENT_TCP_KEY)]
    self.assertEqual((row.value, row.default_value, row.available), ("Off", "Off", True))
    self.assertTrue(owner.apply(FeatureSettingsRequest(STARPORT_PATIENT_TCP_KEY, row.source, "On")))
    self.assertTrue(service.patient_tcp)
    self.assertFalse(service.unacked_video)

  def test_try_experimental_fixes_defaults_off_and_is_the_only_master_switch(self):
    service = FakeStarPort()
    owner = DeviceSettingsOwner(self.params, lambda: self.parked, starport=lambda: service)
    rows = {row.key: row for row in owner.snapshot("starport_options").rows}
    self.assertEqual((rows[STARPORT_EXPERIMENTS_KEY].value, rows[STARPORT_EXPERIMENTS_KEY].default_value), ("Off", "Off"))
    self.assertNotIn("StarPortHeadUnitFixes", rows)
    self.assertTrue(owner.apply(FeatureSettingsRequest(STARPORT_EXPERIMENTS_KEY, b"0", "On")))
    self.assertTrue(service.experiments)
    self.assertFalse(owner.apply(FeatureSettingsRequest(STARPORT_EXPERIMENTS_KEY, b"0", "On")))  # stale view
    self.assertTrue(owner.apply(FeatureSettingsRequest(STARPORT_EXPERIMENTS_KEY, b"1", "Off")))
    self.assertFalse(service.experiments)

  def test_experiment_toggles_appear_only_under_try_experimental_fixes(self):
    service = FakeStarPort()
    owner = DeviceSettingsOwner(self.params, lambda: self.parked, starport=lambda: service)
    keys = [row.key for row in owner.snapshot("starport_options").rows]
    self.assertIn(STARPORT_EXPERIMENTS_KEY, keys)
    self.assertFalse([key for key in keys if key.startswith(STARPORT_EXPERIMENT_PREFIX)])
    self.assertNotIn("heading:starport_experiments", keys)
    self.assertTrue(owner.apply(FeatureSettingsRequest(STARPORT_EXPERIMENTS_KEY, b"0", "On")))
    rows = owner.snapshot("starport_options").rows
    keys = [row.key for row in rows]
    self.assertEqual(keys[keys.index(STARPORT_EXPERIMENTS_KEY) + 1:],
                     [STARPORT_EXPERIMENT_PREFIX + "gm_mychevrolet_w28e", STARPORT_EXPERIMENT_PREFIX + "polestar_ps2_aar_301433653",
                      STARPORT_EXPERIMENT_PREFIX + "phone_ping"])
    rule_key = STARPORT_EXPERIMENT_PREFIX + "gm_mychevrolet_w28e"
    row = rows[keys.index(rule_key)]
    self.assertEqual((row.value, row.default_value, row.available), ("Off", "Off", True))
    self.assertIn("shutdown", row.reason)
    self.assertTrue(owner.apply(FeatureSettingsRequest(rule_key, row.source, "On")))
    self.assertEqual(service.rules, ["gm_mychevrolet_w28e"])
    self.assertFalse(owner.apply(FeatureSettingsRequest(rule_key, b"0", "On")))  # stale view
    self.assertTrue(owner.apply(FeatureSettingsRequest(rule_key, b"1", "Off")))
    self.assertEqual(service.rules, [])
    mavlou = STARPORT_EXPERIMENT_PREFIX + "phone_ping"
    self.assertEqual(rows[keys.index(mavlou)].value, "Off")
    self.assertTrue(owner.apply(FeatureSettingsRequest(mavlou, b"0", "On")))
    self.assertEqual(service.rules, ["phone_ping"])
    self.assertTrue(owner.apply(FeatureSettingsRequest(mavlou, b"1", "Off")))
    self.assertFalse(owner.apply(FeatureSettingsRequest(STARPORT_EXPERIMENT_PREFIX + "unknown", b"0", "On")))
    self.parked = False
    self.assertFalse(owner.apply(FeatureSettingsRequest(rule_key, b"0", "On")))
    self.assertFalse(next(r for r in owner.snapshot("starport_options").rows if r.key == rule_key).available)

  def test_experiment_rows_are_hidden_when_the_service_is_off(self):
    keys = [row.key for row in self.owner.snapshot("starport_options").rows]
    self.assertNotIn(STARPORT_EXPERIMENTS_KEY, keys)
    self.assertNotIn(STARPORT_EXPERIMENTS_KEY, keys)



class CalibrationResetSettingsTest(unittest.TestCase):
  setUp = DeviceSettingsTest.setUp
  def test_calibration_reset_adjacent_and_uses_existing_reset_service(self):
    with patch("openpilot.starpilot.galaxy.device_settings.calibration_reset") as reset:
      reset.pending = False
      rows = self.owner.snapshot("resets").rows
      keys = [row.key for row in rows]
      self.assertEqual(keys[keys.index(DRIVER_MONITORING_RESET) + 1], CALIBRATION_RESET)
      self.assertTrue(rows[keys.index(CALIBRATION_RESET)].available)
      reset.request.return_value = True
      self.assertTrue(self.owner.apply(FeatureSettingsRequest(CALIBRATION_RESET, None, "confirm", confirmation=True)))
      reset.request.assert_called_once_with(self.params)

  def test_calibration_reset_refuses_onroad_pending_stale_and_wrong_value(self):
    with patch("openpilot.starpilot.galaxy.device_settings.calibration_reset") as reset:
      reset.pending = False
      request = FeatureSettingsRequest(CALIBRATION_RESET, None, "confirm", confirmation=True)
      self.assertFalse(self.owner.apply(replace(request, value="On")))
      self.parked = False
      self.assertFalse(self.owner.apply(request))
      self.assertFalse(next(row for row in self.owner.snapshot("resets").rows if row.key == CALIBRATION_RESET).available)
      self.parked = True
      reset.pending = True
      self.assertFalse(self.owner.apply(request))
      reset.pending = False
      self.params.put_bool("OnroadCycleRequested", True)
      self.assertFalse(self.owner.apply(request))
      self.params.remove("OnroadCycleRequested")
      Path(self.params.get_param_path("CalibrationParams")).write_bytes(b"changed")
      self.assertFalse(self.owner.apply(request))
      reset.request.assert_not_called()

  def test_calibration_reset_gateway_requires_confirmation_and_fresh_parked_state(self):
    with patch("openpilot.starpilot.galaxy.device_settings.calibration_reset") as reset:
      reset.pending = False
      reset.request.return_value = True
      context = SimpleNamespace(value=AuthorityContext(True, None, None))
      context.sample = lambda: context.value
      gateway = SettingsGateway(self.params, cast(ContextSource, context), clock=lambda: 10.)
      def preview():
        page = gateway.page("resets", "session", b"generation")
        i = next(i for i,row in enumerate(page["rows"]) if row["label"] == "Reset Calibration")
        self.assertTrue(page["rows"][i]["confirm"])
        return gateway.preview(page["view"], i, 0, "session", b"generation")
      pending = preview()
      self.assertIn("recalibrate", pending["question"])
      reset.request.assert_not_called()
      context.value = replace(context.value, parked=False)
      with self.assertRaises(SettingsChanged):
        gateway.confirm(pending["intent"], "session", b"generation")
      reset.request.assert_not_called()
      context.value = replace(context.value, parked=True)
      pending = preview()
      self.assertTrue(gateway.confirm(pending["intent"], "session", b"generation"))
      reset.request.assert_called_once()

