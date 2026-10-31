"""Disposable saved-feature gateway and mixed publisher-clock authority tests."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from opendbc.car.structs import car
from opendbc.car.toyota.interface import CarInterface as ToyotaInterface
from opendbc.car.toyota.values import CAR as ToyotaCar
from openpilot.common.params import Params
from openpilot.starpilot.galaxy.settings import (
  AuthorityContext, LiveContextSource, PAGES, SettingsChanged, SettingsGateway,
)
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.lateral.controller_selection import learning_allowed
from openpilot.starpilot.lateral.learning_preferences import KEY as LEARNING_KEY, read_preference, replace_preference
from openpilot.starpilot import saved_document


class MutableContext:
  def __init__(self, cp):
    self.value = AuthorityContext(True, cp, b"verified-cp")

  def sample(self):
    return self.value


class SettingsGatewayTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    native = ToyotaInterface.get_non_essential_params(ToyotaCar.TOYOTA_COROLLA_TSS2)
    cp = SimpleNamespace(carFingerprint="TOYOTA_COROLLA_TSS2", openpilotLongitudinalControl=True,
      pcmCruise=False, notCar=False, dashcamOnly=False, passive=False,
      brand="toyota", steerControlType=native.steerControlType, carVin="",
      lateralTuning=native.lateralTuning, minSteerSpeed=native.minSteerSpeed, safetyConfigs=native.safetyConfigs,
      transmissionType=car.CarParams.TransmissionType.automatic)
    self.context = MutableContext(cp)
    self.gateway = SettingsGateway(self.params, self.context, clock=lambda: 100.0)
    self.session, self.generation = "local-session", b"generation"

  def page(self, name):
    return self.gateway.page(name, self.session, self.generation)

  def test_aol_steering_pause_actual_angle_gateway_save_and_default(self):
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.ford.values import CAR as FORD
    self.context.value = replace(self.context.value,
                                 cp=interfaces[FORD.FORD_ESCAPE_MK4].get_non_essential_params(FORD.FORD_ESCAPE_MK4))
    self.params.put_bool('IsMetric', False, block=True)
    self.assertFalse(any(row['label'] == 'Pause steering below' for row in self.page('torque')['rows']))
    page = self.page('aol')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Pause steering below')
    self.assertEqual(page['rows'][index]['unit'], 'mph')
    intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, value=20)
    self.assertTrue(self.gateway.confirm(intent['intent'], self.session, self.generation))
    self.assertEqual(self.params.get('PauseLateralSpeed'), 20.)
    page = self.page('aol')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Pause steering below')
    intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, value=0)
    self.assertTrue(self.gateway.confirm(intent['intent'], self.session, self.generation))
    self.assertEqual(self.params.get('PauseLateralSpeed'), 0.)

  def test_manual_silverado_aol_preview_saves_intent_without_runtime_admission(self):
    from opendbc.car.gm.values import CAR
    from opendbc.car.gm.aol import qualified_gm
    from openpilot.starpilot.aol.vehicle import policy_for
    from openpilot.starpilot.galaxy.vehicle_configuration import configuration_context
    from openpilot.starpilot.vehicle_selection import encode
    self.params.put('VehicleSelection', json.loads(encode(str(CAR.CHEVROLET_SILVERADO_CC))), block=True)
    self.params.put_bool('OpenpilotEnabledToggle', True, block=True)
    cp, token = configuration_context(self.params, None, None)
    self.assertIsNotNone(cp)
    self.assertTrue(cp.dashcamOnly)
    self.assertFalse(cp.openpilotLongitudinalControl)
    self.assertEqual(cp.safetyConfigs[0].safetyParam, 16)
    self.assertFalse(qualified_gm(cp))
    self.assertFalse(policy_for(cp).runtime_supported)
    raw = cp.as_builder().to_bytes()
    self.context.value = AuthorityContext(True, cp, token, configuration_vehicle=True)
    page = self.page('aol')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Enable Always On Lateral')
    self.assertTrue(page['rows'][index]['available'])
    intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, value='On')
    self.assertTrue(self.gateway.confirm(intent['intent'], self.session, self.generation))
    self.assertTrue(self.params.get_bool('AlwaysOnLateral'))
    self.assertEqual(cp.as_builder().to_bytes(), raw)
    for key in ('CarParams', 'CarParamsPersistent', 'CarParamsCache'):
      self.assertIsNone(self.params.get(key))
    self.params.put_bool('AlwaysOnLateral', False, block=True)
    for context in (AuthorityContext(True, cp, token),
                    AuthorityContext(False, cp, token, configuration_vehicle=True)):
      self.context.value = context
      page = self.page('aol')
      index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Enable Always On Lateral')
      self.assertFalse(page['rows'][index]['available'])

  def test_enabled_aol_can_be_disabled_without_supported_vehicle(self):
    self.params.put('AlwaysOnLateral', True, block=True)
    self.context.value = AuthorityContext(True, None, None)
    page = self.page('aol')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Enable Always On Lateral')
    self.assertTrue(page['rows'][index]['available'])
    intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, value='Off')
    self.assertTrue(self.gateway.confirm(intent['intent'], self.session, self.generation))
    self.assertFalse(self.params.get_bool('AlwaysOnLateral'))
    row = next(row for row in self.page('aol')['rows'] if row['label'] == 'Enable Always On Lateral')
    self.assertFalse(row['available'], 'Enabling still requires an actual supported capability')

  def test_aol_off_rechecks_session_authority_after_parameter_lock(self):
    self.params.put('AlwaysOnLateral', True, block=True)
    self.context.value = AuthorityContext(True, None, None)
    page = self.page('aol')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Enable Always On Lateral')
    intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, value='Off')
    acquire = saved_document.acquire_native_lock
    def revoke(fd):
      acquire(fd)
      self.context.value = AuthorityContext(True, None, b'changed-vehicle-context')
    with mock.patch('openpilot.starpilot.saved_document.acquire_native_lock', side_effect=revoke):
      self.assertFalse(self.gateway.confirm(intent['intent'], self.session, self.generation))
    self.assertTrue(self.params.get_bool('AlwaysOnLateral'))

  def test_pcm_cruise_long_owner_can_configure_slc_without_custom_cruise_authority(self):
    cp = self.context.value.cp
    cp.carFingerprint = "TOYOTA_HIGHLANDER_TSS2"
    cp.pcmCruise = True
    page = self.page("slc")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"].endswith("offset"))
    self.assertTrue(page["rows"][index]["available"])
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=3)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    row = next(row for row in self.page("slc")["rows"] if row["label"] == "Speed Limit Controller")
    self.assertTrue(row["available"])
    cp.openpilotLongitudinalControl = False
    row = next(row for row in self.page("slc")["rows"] if row["label"] == "Speed Limit Controller")
    self.assertFalse(row["available"])

  def test_confirmed_toyota_swap_saves_on_off_without_custom_cruise_rows(self):
    from opendbc.car.toyota.interface import CarInterface as ToyotaInterface
    from opendbc.car.toyota.values import CAR as ToyotaCAR
    from openpilot.starpilot.controllers.toyota_cruise import capability, ToyotaCruisePreference
    vehicle = ToyotaInterface.get_non_essential_params(ToyotaCAR.TOYOTA_COROLLA_TSS2)
    self.assertIsNotNone(capability(vehicle))
    self.context.value = AuthorityContext(True, vehicle, vehicle.to_bytes())
    self.params.put('CustomCruise', 2.0, block=True)
    self.params.put('CustomCruiseLong', 8.0, block=True)
    before = {key: read_saved(self.params, key, 128)[0] for key in ('CustomCruise', 'CustomCruiseLong')}
    self.assertFalse(any(row['label'] in ('Short press', 'Hold', 'Swap Toyota Cruise Steps')
                         for row in self.page('profiles')['rows']))
    for value, enabled in (('On', True), ('Off', False)):
      page = self.page('wheel')
      self.assertFalse(any(row['label'] in ('Short press', 'Hold') for row in page['rows']))
      index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Swap Toyota Cruise Steps')
      self.assertTrue(page['rows'][index]['available'])
      intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, value=value)
      self.assertTrue(self.gateway.confirm(intent['intent'], self.session, self.generation))
      self.assertEqual(self.params.get_bool('ReverseCruise'), enabled)
      self.assertEqual(ToyotaCruisePreference(vehicle, self.params).update(), enabled)
      with self.assertRaises(SettingsChanged):
        self.gateway.confirm(intent['intent'], self.session, self.generation)
      self.assertEqual({key: read_saved(self.params, key, 128)[0] for key in before}, before)
    self.context.value.cp.brand = 'honda'
    self.assertFalse(any(row['label'] == 'Swap Toyota Cruise Steps' for row in self.page('wheel')['rows']))

  def test_row_revision_is_stable_only_for_same_source_vehicle_and_session(self):
    first, second = self.page("lane"), self.page("lane")
    self.assertNotEqual(first["view"], second["view"])
    self.assertEqual(first["rows"], second["rows"])
    first_row = first["rows"][0]
    self.assertEqual(first_row["value"], "On")
    self.params.put_bool("LaneCentering", True, block=True)
    row = self.page("lane")["rows"][0]
    self.assertEqual(first_row["value"], row["value"])
    self.assertNotEqual(first_row["revision"], row["revision"])
    self.context.value = AuthorityContext(True, self.context.value.cp, b"different-vehicle")
    changed_vehicle = self.page("lane")["rows"][0]
    self.assertNotEqual(row["revision"], changed_vehicle["revision"])
    for token, generation in (("other-session", self.generation), (self.session, b"other-generation")):
      with self.subTest(token=token, generation=generation):
        changed_session = self.gateway.page("lane", token, generation)["rows"][0]
        self.assertNotEqual(changed_vehicle["revision"], changed_session["revision"])
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(changed_vehicle["revision"], 0, 1, self.session, self.generation)

  def test_direct_switch_and_slider_values_preserve_source_checks(self):
    vehicle = ToyotaInterface.get_non_essential_params(ToyotaCar.TOYOTA_COROLLA_TSS2)
    vehicle.pcmCruise = False
    vehicle.openpilotLongitudinalControl = True
    self.context.value = AuthorityContext(True, vehicle, vehicle.to_bytes())
    self.assertFalse(any(row['label'] in ('Short press', 'Hold', 'Swap Toyota Cruise Steps')
                         for row in self.page('profiles')['rows']))
    page = self.page("wheel")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Short press")
    for invalid in (True, "nan", float("inf"), 0, 151, 2.5, "bogus"):
      with self.subTest(value=invalid), self.assertRaises(SettingsChanged):
        self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=invalid)
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=7)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(self.params.get("CustomCruise"), 7.0)
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=9)
    page = self.page("wheel")
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=9)
    self.context.value = AuthorityContext(False, self.context.value.cp, self.context.value.cp_raw)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(self.params.get("CustomCruise"), 9.0)

  def test_direct_offset_slider_keeps_si_units_and_range(self):
    from openpilot.starpilot.speed_limits import offset_document as od
    self.params.put("SLCOffsetSchedule", od.to_value(od.default_document(False)), block=True)
    page = self.page("slc")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"].endswith("offset"))
    self.assertEqual((page["rows"][index]["minimum"], page["rows"][index]["maximum"]), (-25, 99))
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=5)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertAlmostEqual(self.params.get("SLCOffsetSchedule")["offsets_mps"][0], 5 * od.MPH_TO_MPS)

  def test_limit_sign_visual_preference_without_longitudinal_authority(self):
    cp = self.context.value.cp
    contexts = ((None, None, False),
                (SimpleNamespace(**{**vars(cp), "openpilotLongitudinalControl": False, "pcmCruise": True}), b"stock-pcm", False),
                (SimpleNamespace(**{**vars(cp), "openpilotLongitudinalControl": True, "pcmCruise": True}), b"op-long-pcm", True),
                (cp, b"op-long", True))
    for vehicle, raw, can_control in contexts:
      with self.subTest(vehicle=raw):
        self.context.value = AuthorityContext(True, vehicle, raw)
        self.params.put_bool("ShowSpeedLimits", False, block=True)
        page = self.page("slc")
        signs = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Show speed limit signs")
        control = next(row for row in page["rows"] if row["label"] == "Speed Limit Controller")
        self.assertTrue(page["rows"][signs]["available"])
        self.assertEqual(control["available"], can_control)
        intent = self.gateway.preview(page["view"], signs, 1, self.session, self.generation)
        self.context.value = AuthorityContext(False, vehicle, raw)
        self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
        self.context.value = AuthorityContext(True, vehicle, raw)
        self.params.put_bool("ShowSpeedLimits", False, block=True)
        page = self.page("slc")
        signs = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Show speed limit signs")
        intent = self.gateway.preview(page["view"], signs, 1, self.session, self.generation)
        self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
        self.assertTrue(self.params.get_bool("ShowSpeedLimits"))
        self.assertFalse(self.params.get_bool("SpeedLimitController"))

  def test_all_saved_groups_are_display_only_projection(self):
    self.context.value = replace(self.context.value,
                                 cp=ToyotaInterface.get_non_essential_params(ToyotaCar.TOYOTA_COROLLA_TSS2))
    hub = self.page("hub")
    self.assertTrue(hub["sections"])
    self.assertTrue(all(section["pages"] and all(page["id"] in PAGES for page in section["pages"])
                        for section in hub["sections"]))
    lateral = next(section for section in hub["sections"] if section["id"] == "lateral")
    self.assertEqual(next(page for page in lateral["pages"] if page["id"] == "aol")["title"], self.page("aol")["title"])
    self.assertEqual([row["page"] for row in hub["rows"]],
                     ["curve", "slc", "lane", "lane_change", "profiles", "conditional", "torque", "aol", "wheel",
                     "vehicle", "personalities"])
    self.assertEqual([row["page"] for row in self.page("personalities")["rows"]],
                     ["aggressive", "standard", "relaxed", "traffic"])
    for page in ("slc", "lane", "lane_change", "profiles", "conditional", "conditional/cem", "conditional/ccm",
                 "curve", "torque", "aol", "appearance", "display", "sounds", "pip", "sentry", "personalities",
                 "aggressive", "standard", "relaxed", "traffic", "aggressive/following"):
      with self.subTest(page=page):
        result = self.page(page)
        self.assertTrue(result["rows"])
        for row in result["rows"]:
          self.assertFalse({"source", "related_source", "dependencies", "capability", "vehicle_fingerprint"} & row.keys())
          if row["page"]:
            self.assertIsInstance(row["carVisible"], bool)

  def test_appearance_confirmation_is_source_and_session_bound_without_cp(self):
    from openpilot.starpilot.ui.appearance_preferences import read_visibility

    self.context.value = AuthorityContext(True, None, None)
    page = self.page("appearance")
    rows = {row["label"]: (index, row) for index, row in enumerate(page["rows"])}
    self.assertEqual([rows[label][1]["value"] for label in
                      ("Hide Personality Alerts", "Wheel brake and acceleration colors", "C4 amber signal border", "C4 red blind-spot border")],
                     ["Off", "Off", "Off", "On"])
    # Layout owns widgets/timer/Rainbow Road.
    self.assertFalse({"Stopped Timer", "Show Torque Bar", "Rainbow Road", "Hide Driver Monitoring Icon"} & rows.keys())
    self.assertFalse(rows["C4 amber signal border"][1]["confirm"])
    intent = self.gateway.preview(page["view"], rows["C4 amber signal border"][0], 1, self.session, self.generation)
    self.assertEqual(intent["question"], "Save C4 amber signal border as On? This changes only the display.")
    alerts = self.gateway.preview(page["view"], rows["Hide Personality Alerts"][0], 1, self.session, self.generation)
    self.assertEqual(alerts["question"], "Save Hide Personality Alerts as On? This changes only the display.")
    self.assertIsNone(read_visibility(self.params, "SignalMetrics").raw)
    self.context.value = AuthorityContext(False, None, None)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(read_visibility(self.params, "SignalMetrics").raw, b"1")
    self.context.value = AuthorityContext(True, None, None)
    page = self.page("appearance")
    signal = next(i for i, row in enumerate(page["rows"]) if row["label"] == "C4 amber signal border")
    intent = self.gateway.preview(page["view"], signal, -1, self.session, self.generation)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(read_visibility(self.params, "SignalMetrics").raw, b"0")
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)
    page = self.page("appearance")
    blindspot = next(i for i, row in enumerate(page["rows"]) if row["label"] == "C4 red blind-spot border")
    intent = self.gateway.preview(page["view"], blindspot, -1, self.session, self.generation)
    Path(self.params.get_param_path("BlindSpotMetrics")).write_bytes(b"bad")
    self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(Path(self.params.get_param_path("BlindSpotMetrics")).read_bytes(), b"bad")

  def test_developer_sidebar_uses_the_shared_saved_choice(self):
    self.context.value = AuthorityContext(False, None, None)
    for desired in ("Off", "On"):
      page = self.page("appearance")
      index, row = next((i, row) for i, row in enumerate(page["rows"]) if row["label"] == "Developer Sidebar")
      self.assertEqual(row["defaultValue"], "Off")
      intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=desired)
      self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
      self.assertEqual(self.params.get_bool("DeveloperSidebar"), desired == "On")

  def test_onroad_visual_choice_rechecks_session_and_vehicle_source(self):
    self.context.value = AuthorityContext(False, None, b"car-a")
    page = self.page("appearance")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "C4 amber signal border")
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation, session_valid=lambda: False)
    self.assertIsNone(self.params.get("SignalMetrics"))
    page = self.page("appearance")
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    self.context.value = AuthorityContext(False, None, b"car-b")
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)
    self.assertIsNone(self.params.get("SignalMetrics"))

  def test_installed_sound_pack_gateway_is_exact_source_and_session_bound(self):
    from openpilot.starpilot.ui.sounds_owner import SoundsOwner

    self.context.value = AuthorityContext(True, None, None)
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      (root / "custom_pack" / "sounds").mkdir(parents=True)
      with mock.patch("openpilot.starpilot.galaxy.settings.SoundsOwner",
                      side_effect=lambda params, parked: SoundsOwner(params, parked, root)):
        def preview(value):
          page = self.page("sounds")
          index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Sound Pack")
          return self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=value)

        intent = preview("custom_pack")
        self.assertEqual(intent["proposed"], "custom_pack")
        self.assertIsNone(self.params.get("SoundPack"))
        self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
        saved = Path(self.params.get_param_path("SoundPack"))
        self.assertEqual(saved.read_bytes(), b"custom_pack")

        intent = preview("Stock Sounds")
        saved.write_bytes(b"custom_pack ")
        self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation))
        self.assertEqual(saved.read_bytes(), b"custom_pack ")

        saved.write_bytes(b"custom_pack")
        intent = preview("Stock Sounds")
        self.context.value = AuthorityContext(False, None, None)
        self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
        self.assertEqual(saved.read_bytes(), b"stock")

  def test_auto_slider_numeric_and_auto_choices_reach_saved_owners(self):
    from openpilot.starpilot.audio.alert_volume import AUTO, read_volume
    from openpilot.starpilot.ui.display_preferences import MASTER, read_choice
    self.context.value = AuthorityContext(True, None, None)
    self.params.put_bool(MASTER, True, block=True)
    for page_name, label, key, values in (
      ("sounds", "Immediate Warning", "WarningImmediateVolume", ("40", "Auto")),
      ("display", "Parked Brightness", "ScreenBrightness", ("42", "Auto")),
    ):
      for value in values:
        page = self.page(page_name)
        index = next(i for i, row in enumerate(page["rows"]) if row["label"] == label)
        self.assertIn("Auto", page["rows"][index]["choices"])
        self.assertEqual(page["rows"][index]["maximum"], 100)
        intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=value)
        self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
        saved = read_volume(self.params, key) if page_name == "sounds" else read_choice(self.params, key, large=True)
        self.assertEqual(saved.value, AUTO if value == "Auto" else int(value))
    page = self.page("sounds")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Immediate Warning")
    for value in ("20", "42", "105", "nan"):
      with self.assertRaises(SettingsChanged):
        self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=value)

  def test_moved_signal_lane_preference_keeps_onroad_source_guards(self):
    from openpilot.starpilot.conditional_mode.preferences import decode_preferences
    self.context.value = AuthorityContext(False, None, None)
    page = self.page("lane_change")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Signal lane detection")
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    saved = Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes()
    self.assertEqual(decode_preferences(saved).cem.signal_lane_detection, intent["proposed"] == "On")
    page = self.page("lane_change")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Signal lane width")
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    self.params.put_bool("IsMetric", True, block=True)
    self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes(), saved)

  def test_sounds_and_display_use_actual_native_owners_without_vehicle_cp(self):
    from openpilot.starpilot.audio.alert_volume import AUTO, read_volume
    from openpilot.starpilot.ui.display_preferences import read_choice

    self.context.value = AuthorityContext(True, None, None)
    sounds = self.page("sounds")
    warning = next(i for i, row in enumerate(sounds["rows"]) if row["label"] == "Immediate Warning")
    self.assertEqual(sounds["rows"][warning]["value"], "Auto")
    preview = self.gateway.preview(sounds["view"], warning, 1, self.session, self.generation)
    self.assertEqual(preview["proposed"], "25%")
    self.assertEqual(read_volume(self.params, "WarningImmediateVolume").value, AUTO)
    self.assertTrue(self.gateway.confirm(preview["intent"], self.session, self.generation))
    self.assertEqual(read_volume(self.params, "WarningImmediateVolume").value, 25)
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(preview["intent"], self.session, self.generation)

    with mock.patch("openpilot.starpilot.galaxy.settings.HARDWARE.get_device_type", return_value="mici"):
      display = self.page("display")
      timeout = next(row for row in display["rows"] if row["label"] == "Driving Interaction Timeout")
      self.assertEqual(timeout["value"], "5")
      master = next(i for i, row in enumerate(display["rows"]) if row["label"] == "Custom Display Settings")
      preview = self.gateway.preview(display["view"], master, 1, self.session, self.generation)
      self.assertEqual(preview["proposed"], "On")
      self.context.value = AuthorityContext(False, None, None)
      self.assertTrue(self.gateway.confirm(preview["intent"], self.session, self.generation))
      self.assertEqual(read_choice(self.params, "StarPilotDisplayPreferencesEnabled", large=False).value, 1)

    corrupt = Path(self.params.get_param_path("EngageVolume"))
    corrupt.write_bytes(b"bad")
    sounds = self.page("sounds")
    engage = next(i for i, row in enumerate(sounds["rows"]) if row["label"] == "Engagement Chime")
    self.assertEqual(sounds["rows"][engage]["repairValue"], "Auto")
    preview = self.gateway.preview(sounds["view"], engage, 1, self.session, self.generation)
    self.assertEqual(preview["proposed"], "Auto")
    self.assertTrue(self.gateway.confirm(preview["intent"], self.session, self.generation))
    self.assertEqual(read_volume(self.params, "EngageVolume").value, AUTO)

  def test_vision_source_choice_matches_native_diagnostic_gate(self):
    from opendbc.car import gen_empty_fingerprint
    from opendbc.car.honda.interface import CarInterface as HondaInterface
    from opendbc.car.honda.values import CAR as HondaCAR
    from openpilot.starpilot.speed_limits.vision_gate import diagnostic_choice_enabled

    vehicle = HondaInterface.get_params(HondaCAR.HONDA_CIVIC_BOSCH, gen_empty_fingerprint(), [], True, False, False)
    self.assertTrue(vehicle.openpilotLongitudinalControl and not vehicle.pcmCruise)
    self.assertTrue(diagnostic_choice_enabled({}, vehicle))
    self.context.value = AuthorityContext(True, vehicle, vehicle.to_bytes())
    self.params.put('SLCPriority1', 'Dashboard', block=True)
    self.params.put('SLCPriority2', 'Dashboard', block=True)
    for replay, vision, available in (("0", "0", False), ("1", "0", False),
      ("0", "1", False), ("1", "1", True)):
      with self.subTest(replay=replay, vision=vision), mock.patch.dict(
          os.environ, {"SLC_REPLAY_RUNTIME": replay, "SLC_VISION_DEVELOPMENT": vision}):
        self.assertEqual(diagnostic_choice_enabled(os.environ), available)
        rows = self.page("slc")["rows"]
        primary = next(row for row in rows if row["label"] == "Primary source")
        secondary = next(row for row in rows if row["label"] == "Secondary source")
        self.assertIn("Vision", primary["choices"])
        self.assertIn("Vision", secondary["choices"])
        self.assertNotIn("require your confirmation", primary["reason"])
        confirmation = next(row for row in rows if row["label"] == "Automatic adoption")
        self.assertEqual(confirmation["value"], "On")
        self.assertIn("immediately", confirmation["reason"])
        self.assertIn("accepts", confirmation["reason"])

    with mock.patch.dict(os.environ, {"SLC_REPLAY_RUNTIME": "0", "SLC_VISION_DEVELOPMENT": "0"}):
      # Vision needs no dev/replay flags.
      page = self.page("slc")
      index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Primary source")
      intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
      self.assertEqual(intent["proposed"], "Vision")
      self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
      self.assertEqual(read_saved(self.params, "SLCPriority1", 128)[0], b"Vision")
      self.params.put('SLCPriority1', 'Dashboard', block=True)
      original_context = self.context.value
      for withdrawal in ('passive', 'unavailable'):
        with self.subTest(withdrawal=withdrawal):
          self.context.value = original_context
          page = self.page("slc")
          index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Primary source")
          intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
          self.assertEqual(intent["proposed"], "Vision")
          if withdrawal == 'passive':
            withdrawn = vehicle.as_reader().as_builder()
            withdrawn.passive = True
            self.context.value = AuthorityContext(True, withdrawn, withdrawn.to_bytes())
          else:
            self.context.value = AuthorityContext(True, None, None)
          with self.assertRaises(SettingsChanged):
            self.gateway.confirm(intent["intent"], self.session, self.generation)
          self.assertEqual(read_saved(self.params, "SLCPriority1", 128)[0], b"Dashboard")

      # Stock-ACC display cannot grant LONG.
      from openpilot.starpilot.feature_runtime import slc_runtime_settings, vision_control_enabled
      stock = HondaInterface.get_params(HondaCAR.HONDA_CIVIC_BOSCH, gen_empty_fingerprint(), [], False, False, False)
      self.assertFalse(stock.openpilotLongitudinalControl)
      self.context.value = AuthorityContext(True, stock, stock.to_bytes())
      self.params.put_bool('SpeedLimitController', True, block=True)
      page = self.page("slc")
      index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Primary source")
      intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
      self.assertEqual(intent["proposed"], "Vision")
      self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
      settings = slc_runtime_settings(self.params, stock, {})
      self.assertTrue(settings.display and settings.acceptance.display_only)
      self.assertFalse(settings.enabled or vision_control_enabled(self.params, stock))

  def test_pip_saved_choices_no_vehicle_cp_and_one_use_confirmation(self):
    from openpilot.starpilot.ui.pip_preferences import BLINKER, MASK, read_pip
    self.context.value = AuthorityContext(True, None, None)
    page = self.page("pip")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Show on turn signal")
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(read_pip(self.params).source(BLINKER), (b"1", True))
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)
    page = self.page("pip")
    self.assertFalse(any('camera format' in row['label'] for row in page['rows']))
    self.assertIsNone(read_pip(self.params).source(MASK)[0])
    self.assertEqual((read_pip(self.params).mask.width, read_pip(self.params).mask.height), (1928, 1208))

  def test_pip_visual_crop_editor_binds_saved_source_and_one_use_confirmation(self):
    from openpilot.starpilot.ui.pip_preferences import MASK, encode_mask, read_pip, starting_mask
    from openpilot.starpilot.ui.pip_owner import PiPOwner

    self.context.value = AuthorityContext(True, None, None)
    page = self.page("pip")
    self.assertEqual(page["editor"]["centerLeft"], [315, 548])
    self.assertIs(page["editor"]["invert"], False)
    index = page["editorRow"]
    self.assertTrue(page["rows"][index]["confirm"])
    draft = {"width": 1928, "height": 1208, "center_left": None,
             "center_right": [1570, 540], "crop_size": 580}
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(page["view"], index, 0, self.session, self.generation,
                           draft={**draft, "width": 1344})
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, draft=draft)
    self.assertIsNone(read_pip(self.params).source(MASK)[0])
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(read_pip(self.params).mask.center_right, (1570.0, 540.0))
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)

    page = self.page("pip")
    intent = self.gateway.preview(page["view"], page["editorRow"], 0, self.session, self.generation, draft=draft)
    Path(self.params.get_param_path(MASK)).write_bytes(b'{"invalid":true}')
    self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(Path(self.params.get_param_path(MASK)).read_bytes(), b'{"invalid":true}')

    Path(self.params.get_param_path(MASK)).write_bytes(encode_mask(starting_mask(1928, 1208)))
    original = PiPOwner.editor_with_source
    def changed_invert(owner):
      Path(self.params.get_param_path("PIPPreviewInvert")).write_bytes(b"1")
      return original(owner)
    with mock.patch.object(PiPOwner, "editor_with_source", changed_invert), self.assertRaises(SettingsChanged):
      self.page("pip")

  def test_sentry_preferences_remain_editable_during_drive_without_arming_sensors(self):
    from openpilot.starpilot.sentry_mode.preferences import KEY, decode
    self.context.value = AuthorityContext(True, None, None)
    page = self.page("sentry")
    self.assertEqual([row["value"] for row in page["rows"]], ["Off", "0.040", "1.0"])
    self.assertFalse(Path(self.params.get_param_path(KEY)).exists())
    intent = self.gateway.preview(page["view"], 0, 1, self.session, self.generation)
    self.assertIn("development opt-in", intent["question"])
    self.assertFalse(Path(self.params.get_param_path(KEY)).exists())
    self.context.value = AuthorityContext(False, None, None)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertTrue(decode(Path(self.params.get_param_path(KEY)).read_bytes()).enabled)
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)

  def test_sentry_corrupt_reset_rechecks_exact_source_and_session(self):
    from openpilot.starpilot.sentry_mode.preferences import KEY, decode
    self.context.value = AuthorityContext(True, None, None)
    source = Path(self.params.get_param_path(KEY))
    source.write_bytes(b'{"version":2}')
    page = self.page("sentry")
    self.assertEqual([row["label"] for row in page["rows"]],
                     ["Saved motion settings", "Reset saved motion settings"])
    intent = self.gateway.preview(page["view"], 1, 0, self.session, self.generation)
    source.write_bytes(b'{"version":3}')
    self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(source.read_bytes(), b'{"version":3}')
    page = self.page("sentry")
    intent = self.gateway.preview(page["view"], 1, 0, self.session, self.generation)
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation, session_valid=lambda: False)
    self.assertEqual(source.read_bytes(), b'{"version":3}')
    page = self.page("sentry")
    intent = self.gateway.preview(page["view"], 1, 0, self.session, self.generation)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertFalse(decode(source.read_bytes()).enabled)

  def test_sentry_unverified_write_is_not_reported_as_saved(self):
    from openpilot.starpilot.sentry_mode.preferences import KEY, decode
    self.context.value = AuthorityContext(True, None, None)
    page = self.page("sentry")
    intent = self.gateway.preview(page["view"], 0, 1, self.session, self.generation)
    import os
    real_fsync = os.fsync
    calls = 0
    def uncertain(fd):
      nonlocal calls
      calls += 1
      if calls == 2:
        raise OSError("directory fsync unavailable")
      return real_fsync(fd)
    with mock.patch("openpilot.starpilot.saved_document.os.fsync", side_effect=uncertain):
      self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation))
    source = Path(self.params.get_param_path(KEY))
    self.assertTrue(decode(source.read_bytes()).enabled)
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)

  def test_pip_preferences_onroad_recheck_session_and_source_before_write(self):
    from openpilot.starpilot.ui.pip_preferences import BLINKER, read_pip
    self.context.value = AuthorityContext(True, None, None)
    page = self.page("pip")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Show on turn signal")
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    self.context.value = AuthorityContext(False, None, None)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(read_pip(self.params).source(BLINKER)[0], b"1")
    self.params.remove(BLINKER)
    page = self.page("pip")
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation, session_valid=lambda: False)
    self.assertIsNone(read_pip(self.params).source(BLINKER)[0])
    page = self.page("pip")
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    Path(self.params.get_param_path(BLINKER)).write_bytes(b"1")
    self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation))

  def test_conditional_intent_can_save_during_drive_and_rechecks_source(self):
    from openpilot.starpilot.conditional_mode.preferences import decode_preferences
    self.context.value.cp.pcmCruise = True  # Nidec still supports native long.
    page = self.page("conditional")
    self.assertEqual(page["rows"][0]["value"], "Conditional Experimental")
    intent = self.gateway.preview(page["view"], 0, 1, self.session, self.generation)
    self.assertIn("Conditional Chill", intent["question"])
    self.context.value = AuthorityContext(False, self.context.value.cp, b"verified-cp")
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(decode_preferences(Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes()).mode.value,
                     "conditional_chill")
    page = self.page("conditional/cem")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Open road")
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    self.context.value = AuthorityContext(False, self.context.value.cp, b"different-cp")
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)

  def test_ioniq_media_assignment_uses_shared_confirmed_editor(self):
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.hyundai.values import CAR, HyundaiFlags
    cp = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
    cp.flags = int(cp.flags | HyundaiFlags.CANFD_LKA_STEER_MSG)
    cp.openpilotLongitudinalControl = True
    self.context.value = AuthorityContext(True, cp, b"ioniq-hda2-cp")
    page = self.page("wheel")
    media = [row for row in page["rows"] if row["label"].startswith(("MODE ", "Star button", "Star long press", "Star very long press"))]
    self.assertEqual(len(media), 6)
    self.assertTrue(all(row["available"] for row in media))
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "MODE press")
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value="Experimental override")
    self.assertIn(intent["proposed"], intent["question"])
    Path(self.params.get_param_path("ModeButtonControl")).write_bytes(b"5")
    self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(read_saved(self.params, "ModeButtonControl", 8)[0], b"5")
    Path(self.params.get_param_path("ModeButtonControl")).write_bytes(b"0")
    page = self.page("wheel")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "MODE press")
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value="Experimental override")
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(read_saved(self.params, "ModeButtonControl", 8)[0], b"5")

  def test_conditional_corrupt_document_needs_one_use_reset(self):
    from openpilot.starpilot.conditional_mode.preferences import decode_preferences
    source = Path(self.params.get_param_path("ConditionalModeConfig"))
    source.write_bytes(b'{"version":2}')
    page = self.page("conditional")
    self.assertFalse(any(row["label"] == "Saved driving mode" for row in page["rows"]))
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Restore Chill defaults")
    self.assertTrue(page["rows"][index]["confirm"])
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation)
    self.assertEqual(source.read_bytes(), b'{"version":2}')
    source.write_bytes(b'{"version":3}')
    self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(source.read_bytes(), b'{"version":3}')
    page = self.page("conditional")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Restore Chill defaults")
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(decode_preferences(source.read_bytes()).mode.value, "stock")

  def test_conditional_manual_persist_clears_only_selected_saved_code(self):
    from openpilot.starpilot.conditional_mode.manual_saved import SavedCodes, decode, encode
    from openpilot.starpilot.conditional_mode.preferences import decode_preferences
    manual = Path(self.params.get_param_path("ConditionalManualState"))
    manual.write_bytes(encode(SavedCodes(cem=2, ccm=1)))
    page = self.page("conditional/cem")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Remember manual choice")
    self.assertTrue(page["rows"][index]["available"])
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    self.assertIn("Automatic", intent["question"])
    self.assertEqual(decode(manual.read_bytes()), SavedCodes(cem=2, ccm=1))
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(decode(manual.read_bytes()), SavedCodes(cem=0, ccm=1))
    config = Path(self.params.get_param_path("ConditionalModeConfig"))
    self.assertTrue(decode_preferences(config.read_bytes()).cem.persist_manual)
    manual.write_bytes(b'{"version":7}')
    page = self.page("conditional")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Reset remembered manual choices")
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation)
    self.assertIn("both", intent["question"].lower())
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(decode(manual.read_bytes()), SavedCodes())

  def test_curve_page_uses_one_use_source_bound_long_intent(self):
    page = self.page("curve")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Curve Speed Controller")
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    self.assertFalse(self.params.get_bool("CurveSpeedController"))
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertTrue(self.params.get_bool("CurveSpeedController"))
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)
    page = self.page("curve")
    no_lead = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Pause while following a lead")
    intent = self.gateway.preview(page["view"], no_lead, 1, self.session, self.generation)
    self.context.value = AuthorityContext(False, self.context.value.cp, b"changed-cp")
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)
    self.assertFalse(self.params.get_bool("CurveSpeedControllerNoLead"))

  def test_curve_learning_reset_preserves_legacy_and_rechecks_sources(self):
    legacy = Path(self.params.get_param_path("CurvatureData"))
    legacy.write_bytes(b'{"0.001":{"average":2.4,"count":2}}')
    canonical = Path(self.params.get_param_path("CurveComfortData"))
    page = self.page("curve")
    reset = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Reset Saved Curve Learning")
    self.assertTrue(page["rows"][reset]["confirm"])
    self.assertFalse(any(row["label"].startswith("Adopt") for row in page["rows"]))
    intent = self.gateway.preview(page["view"], reset, 0, self.session, self.generation)
    canonical.write_bytes(b'{bad')
    self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation))
    page = self.page("curve")
    reset = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Reset Saved Curve Learning")
    intent = self.gateway.preview(page["view"], reset, 0, self.session, self.generation)
    self.context.value = AuthorityContext(False, self.context.value.cp, b"changed-cp")
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)
    self.context.value = AuthorityContext(True, self.context.value.cp, b"verified-cp")
    page = self.page("curve")
    reset = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Reset Saved Curve Learning")
    intent = self.gateway.preview(page["view"], reset, 0, self.session, self.generation)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(json.loads(canonical.read_bytes()), {"version": 1, "buckets": {}})
    self.assertEqual(legacy.read_bytes(), b'{"0.001":{"average":2.4,"count":2}}')

  def test_one_use_confirmation_and_exact_source_context(self):
    self.params.put_bool("LaneCentering", False, block=True)
    page = self.page("lane")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Enable Lane Centering")
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    self.assertIn("enable lane centering", intent["question"])
    self.assertFalse(self.params.get_bool("LaneCentering"))
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertTrue(self.params.get_bool("LaneCentering"))
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)
    page = self.page("lane")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Enable Lane Centering")
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    self.params.put_bool("LaneCentering", False, block=True)
    self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertFalse(self.params.get_bool("LaneCentering"))

  def test_slc_first_edit_long_preset_and_lane_change_use_owner(self):
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.toyota.values import CAR as TOYOTA

    cp = interfaces[TOYOTA.TOYOTA_COROLLA_TSS2].get_non_essential_params(TOYOTA.TOYOTA_COROLLA_TSS2)
    cp.openpilotLongitudinalControl = True
    cp.pcmCruise = False
    self.context.value = replace(self.context.value, cp=cp)
    slc = self.page("slc")
    self.assertFalse(any(row["label"] == "Adopt fixed offsets" for row in slc["rows"]))
    offset = next(i for i, row in enumerate(slc["rows"]) if row["label"].endswith("offset"))
    self.assertEqual(slc["rows"][offset]["value"], "0")
    intent = self.gateway.preview(slc["view"], offset, 0, self.session, self.generation, value=3)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    assert read_saved(self.params, "SLCOffsetSchedule", 4096)[0] is not None
    self.assertAlmostEqual(self.params.get("SLCOffsetSchedule")["offsets_mps"][0], 3 * .44704)
    page = self.page("aggressive/acceleration")
    preset = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Preset")
    intent = self.gateway.preview(page["view"], preset, 1, self.session, self.generation)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    assert read_saved(self.params, "LongitudinalPersonalityProfiles", 65536)[0] is not None
    page = self.page("lane_change")
    auto = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Automatic Lane Changes")
    intent = self.gateway.preview(page["view"], auto, 1, self.session, self.generation)
    self.assertIn("after the current signal and maneuver end", intent["question"].lower())
    self.assertNotIn("next drive", intent["question"].lower())
    self.assertIn("blindspot checks remain required", intent["question"].lower())
    self.assertNotIn("development", intent["question"].lower())
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    assert read_saved(self.params, "LaneChangePreferences", 512)[0] is not None

  def test_lane_change_close_gap_uses_existing_parked_owner(self):
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.toyota.values import CAR as TOYOTA

    cp = interfaces[TOYOTA.TOYOTA_COROLLA_TSS2].get_non_essential_params(TOYOTA.TOYOTA_COROLLA_TSS2)
    cp.openpilotLongitudinalControl = True
    cp.pcmCruise = False
    self.context.value = replace(self.context.value, cp=cp)
    from openpilot.starpilot.lateral.lane_change_preferences import read_saved as read_lane_change

    page = self.page("lane_change")
    close = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Close lane-change gap")
    gap = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Lane-change follow gap")
    self.assertEqual((page["rows"][close]["value"], page["rows"][gap]["value"]), ("Off", "0.75"))
    self.assertEqual((page["rows"][gap]["minimum"], page["rows"][gap]["maximum"], page["rows"][gap]["step"]),
                     (0.75, 1.0, 0.05))
    intent = self.gateway.preview(page["view"], close, 1, self.session, self.generation)
    self.assertIn("to On", intent["question"])
    self.assertIn("Changes refresh during this drive", intent["question"])
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertTrue(read_lane_change(self.params).policy.close_gap)
    page = self.page("lane_change")
    intent = self.gateway.preview(page["view"], gap, 1, self.session, self.generation)
    self.assertIn("to 0.8 seconds", intent["question"])
    self.assertIn("Changes refresh during this drive", intent["question"])
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(read_lane_change(self.params).policy.close_gap_seconds, 0.8)
    self.assertEqual(self.page("lane_change")["rows"][gap]["value"], "0.8")
    self.assertEqual(self.params.get("LaneChangePreferences")["version"], 5)

    saved = self.page("lane_change")
    revoke = self.gateway.preview(saved["view"], close, -1, self.session, self.generation)
    self.context.value = AuthorityContext(False, self.context.value.cp, self.context.value.cp_raw)
    self.assertTrue(self.gateway.confirm(revoke["intent"], self.session, self.generation))
    self.assertFalse(read_lane_change(self.params).policy.close_gap)
    saved = self.page("lane_change")
    revoke = self.gateway.preview(saved["view"], close, 1, self.session, self.generation)
    self.context.value = AuthorityContext(False, self.context.value.cp, b"different-cp")
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(revoke["intent"], self.session, self.generation)
    self.assertFalse(read_lane_change(self.params).policy.close_gap)
    self.context.value = AuthorityContext(True, self.context.value.cp, b"verified-cp")

    stale = self.page("lane_change")
    self.params.put("LaneChangePreferences", {"version": 3, "enabled": True, "minimumSpeedMps": 0,
                 "onePerSignal": False, "autoLaneChange": False, "autoDelayS": 1.0, "minimumLaneWidthM": 0,
                 "closeGap": False, "closeGapSeconds": 0.75}, block=True)
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(stale["view"], close, -1, self.session, self.generation)
    self.context.value.cp.openpilotLongitudinalControl = False
    unavailable = self.page("lane_change")
    self.assertFalse(unavailable["rows"][close]["available"])
    self.assertFalse(unavailable["rows"][gap]["available"])

  def test_global_profiles_use_existing_owner_and_remain_editable_onroad(self):
    page = self.page("profiles")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Selected Deceleration Profile")
    self.assertEqual(page["rows"][index]["value"], "Comfort")
    self.assertEqual(page["rows"][index]["choices"], ["StarPilot Default", "Normal", "Comfort", "Sport"])
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value="Sport")
    self.assertEqual(intent["proposed"], "Sport")
    self.assertIn("Selected Profile", intent["question"])
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertFalse(self.params.get_bool("CustomPersonalities"))
    category = self.page("aggressive/braking")
    preset = next(i for i, row in enumerate(category["rows"]) if row["label"] == "Preset")
    category_intent = self.gateway.preview(category["view"], preset, 1, self.session, self.generation)
    self.assertTrue(self.gateway.confirm(category_intent["intent"], self.session, self.generation))
    raw, readable = read_saved(self.params, "LongitudinalPersonalityProfiles", 65536)
    self.assertTrue(readable)
    assert raw is not None
    previous = json.loads(raw)["profiles"]["aggressive"]["braking"]
    page = self.page("profiles")
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value="Sport")
    self.context.value = AuthorityContext(False, self.context.value.cp, self.context.value.cp_raw)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    raw, readable = read_saved(self.params, "LongitudinalPersonalityProfiles", 65536)
    self.assertTrue(readable)
    assert raw is not None
    document = json.loads(raw)
    self.assertEqual(document["schemaVersion"], 6)
    self.assertEqual(document["selectedDecelerationProfile"], "sport")
    self.assertNotIn("globalBrakingResponse", document)
    self.assertEqual(document["profiles"]["aggressive"]["braking"], previous)
    acceleration = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Selected Acceleration Profile")
    page = self.page("profiles")
    intent = self.gateway.preview(page["view"], acceleration, 1, self.session, self.generation)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(json.loads(required_raw(self.params, "LongitudinalPersonalityProfiles", 65536))["selectedAccelerationProfile"], "standard")

  def test_traffic_saved_controls_and_explicit_follow_repair_use_shared_gateway(self):
    from openpilot.starpilot.longitudinal.profile_runtime import read_traffic_settings

    page = self.page("traffic")
    follow = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Low-speed follow")
    self.assertEqual((page["rows"][follow]["value"], page["rows"][follow]["unit"]), ("0.75", "s"))
    self.assertIn("inactive while Custom Driving Profiles is Off", page["rows"][follow]["reason"])
    self.assertEqual([row["page"] for row in page["rows"] if row["page"]],
                     [])
    intent = self.gateway.preview(page["view"], follow, 1, self.session, self.generation)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(self.params.get("TrafficFollow"), 0.8)
    self.assertEqual(read_traffic_settings(self.params).follow[0], 0.75)
    path = Path(self.params.get_param_path("TrafficFollow"))
    path.write_bytes(b"0.5")
    page = self.page("traffic")
    follow = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Low-speed follow")
    self.assertTrue(page["rows"][follow]["action"])
    self.assertEqual(page["rows"][follow]["minimum"], 0.5)
    self.assertFalse(any(row["label"] == "Restore supported Traffic follow" for row in page["rows"]))
    intent = self.gateway.preview(page["view"], follow, 1, self.session, self.generation)
    path.write_bytes(b"0.6")
    self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(path.read_bytes(), b"0.6")

  def test_longitudinal_curve_point_preview_confirm_and_readback(self):
    for _ in range(10):
      page = self.page("standard/acceleration")
      if page["rows"][0]["value"] == "Custom":
        break
      intent = self.gateway.preview(page["view"], 0, 1, self.session, self.generation)
      self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    else:
      self.fail("Custom preset was not reachable")
    page = self.page("standard/acceleration")
    self.assertEqual([row["label"] for row in page["rows"][1:]], [f"{speed} mph point" for speed in range(0, 91, 10)])
    previous = float(page["rows"][1]["value"])
    direction = -1 if previous >= page["rows"][1]["maximum"] else 1
    intent = self.gateway.preview(page["view"], 1, direction, self.session, self.generation)
    self.assertIn("0 mph point", intent["question"])
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    updated = self.page("standard/acceleration")
    self.assertAlmostEqual(float(updated["rows"][1]["value"]), previous + direction * 0.05)
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)

  def test_qualified_torque_and_honda_aol_actions(self):
    from opendbc.car import gen_empty_fingerprint
    from opendbc.car.honda.interface import CarInterface
    from opendbc.car.honda.values import CAR as HONDA
    from openpilot.starpilot.lateral.tests.test_lane_runtime import ioniq_candidate
    _, torque_vehicle = ioniq_candidate()
    self.params.put(LEARNING_KEY, replace_preference(None, self.context.value.cp, False), block=True)
    legacy = read_saved(self.params, "ForceAutoTuneOff", 1)[0]
    self.context.value = AuthorityContext(True, torque_vehicle, torque_vehicle.as_reader().as_builder().to_bytes())
    page = self.page("torque")
    factor = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Lat Accel")
    self.assertTrue(page["rows"][factor]["action"])
    previous = float(page["rows"][factor]["value"])
    direction = -1 if previous >= page["rows"][factor]["maximum"] else 1
    intent = self.gateway.preview(page["view"], factor, direction, self.session, self.generation)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY
    self.assertIsNotNone(read_saved(self.params, DOCUMENT_KEY, 65536)[0])
    from openpilot.starpilot.lateral.tests.test_lane_runtime import ioniq_candidate
    _, ioniq = ioniq_candidate()
    self.context.value = AuthorityContext(True, ioniq, b"ioniq6-model-config")
    page = self.page("torque")
    learning = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Use StarPilot Auto Learn")
    self.assertTrue(page["rows"][learning]["action"])
    self.assertIn("automatically correct torque profiles", page["rows"][learning]["reason"])
    self.assertTrue(page["rows"][learning]["reason"].startswith("StarPilot’s FLM tuning will attempt to automatically correct torque profiles as you drive."))
    self.assertFalse(learning_allowed(self.params, ioniq))
    before_cp = ioniq.as_reader().as_builder().to_bytes()
    before_tune = read_saved(self.params, "TorqueOverrideDocument", 65536)[0]
    intent = self.gateway.preview(page["view"], learning, 0, self.session, self.generation, value="Off")
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertFalse(read_preference(self.params, ioniq).enabled)
    self.assertEqual(read_preference(self.params, ioniq).source, "scope")
    self.assertEqual(read_saved(self.params, "ForceAutoTuneOff", 1)[0], legacy)
    page = self.page("torque")
    controller = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Steering Controller")
    intent = self.gateway.preview(page["view"], controller, 0, self.session, self.generation, value="Stock Controller")
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual(read_saved(self.params, "ForceAutoTuneOff", 1)[0], legacy)
    self.assertFalse(read_preference(self.params, ioniq).enabled)
    page = self.page("torque")
    learning = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Use StarPilot Auto Learn")
    self.assertTrue(page["rows"][learning]["action"])
    intent = self.gateway.preview(page["view"], learning, 0, self.session, self.generation, value="On")
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertTrue(read_preference(self.params, ioniq).enabled)
    self.assertEqual(read_preference(self.params, ioniq).source, "scope")
    self.assertEqual(read_saved(self.params, "ForceAutoTuneOff", 1)[0], legacy)
    scoped, readable = read_saved(self.params, LEARNING_KEY, 4096)
    assert scoped is not None
    self.assertTrue(readable)
    self.assertEqual(json.loads(scoped)["vehicles"], {
      "toyota": {"TOYOTA_COROLLA_TSS2": False}, "hyundai": {"HYUNDAI_IONIQ_6": True},
    })
    self.assertEqual(read_saved(self.params, "TorqueOverrideDocument", 65536)[0], before_tune)
    self.assertEqual(ioniq.as_reader().as_builder().to_bytes(), before_cp)
    honda = CarInterface.get_params(HONDA.HONDA_CIVIC_BOSCH, gen_empty_fingerprint(), [], True, False, False)
    self.context.value = AuthorityContext(True, honda, b"honda-model-config")
    page = self.page("aol")
    threshold = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Brake pause below")
    self.assertTrue(page["rows"][threshold]["action"])
    intent = self.gateway.preview(page["view"], threshold, 1, self.session, self.generation)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    assert read_saved(self.params, "AolBrakePauseSpeedMps", 128)[0] is not None

  def test_context_and_session_change_reject_intent(self):
    page = self.page("lane")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Enable Lane Centering")
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(page["view"], index, 1, "other-session", self.generation)
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    self.context.value = AuthorityContext(False, self.context.value.cp, b"changed-cp")
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)
    self.assertFalse(self.params.get_bool("LaneCentering"))
    self.context.value = AuthorityContext(True, self.context.value.cp, b"different-cp")
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(page["view"], index, 1, self.session, self.generation)

  def test_generation_loss_inside_owner_cancels_write(self):
    page = self.page("lane")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Enable Lane Centering")
    intent = self.gateway.preview(page["view"], index, 1, self.session, self.generation)
    checks = 0
    def revoked():
      nonlocal checks
      checks += 1
      return checks == 1  # Revoke before owner callback.
    self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation, session_valid=revoked))
    self.assertFalse(self.params.get_bool("LaneCentering"))

  def test_invalid_saved_raw_remains_visible_without_action(self):
    path = Path(self.params.get_param_path("LaneCentering"))
    path.write_bytes(b"\xff")
    page = self.page("lane")
    row = next(row for row in page["rows"] if row["label"] == "Enable Lane Centering")
    self.assertFalse(row["action"])
    self.assertIn("Invalid saved value", row["reason"])
    self.assertEqual(path.read_bytes(), b"\xff")
    path.write_bytes(b"x" * 129)
    oversized = self.page("lane")
    self.assertFalse(next(row for row in oversized["rows"] if row["label"] == "Enable Lane Centering")["action"])
    self.assertEqual(path.read_bytes(), b"x" * 129)

  def test_bounded_reader_preserves_absent_and_rejects_nonregular(self):
    path = Path(self.params.get_param_path("LaneCentering"))
    self.assertEqual(read_saved(self.params, "LaneCentering", 128), (None, True))
    path.write_bytes(b"x" * 129)
    self.assertEqual(read_saved(self.params, "LaneCentering", 128), (b"", False))
    self.assertEqual(path.read_bytes(), b"x" * 129)
    path.unlink()
    target = path.parent / "target"
    target.write_bytes(b"1")
    path.symlink_to(target)
    self.assertEqual(read_saved(self.params, "LaneCentering", 128), (b"", False))
    path.unlink()
    import os
    os.mkfifo(path)
    self.assertEqual(read_saved(self.params, "LaneCentering", 128), (b"", False))


  def _live_lane_context(self):
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
      def put_bool(self, key, value, block=True):
        self.put(key, "1" if value else "0", block=block)
    self.gateway = SettingsGateway(Files(), self.context, clock=lambda: 100.0)
    self.context.value = AuthorityContext(False, tagged, tagged.as_reader().as_builder().to_bytes())
    return tagged, root

  def test_turn_assist_gateway_saves_next_drive_choice_onroad_with_selection_binding(self):
    unrelated = self.context.value
    unrelated.cp.safetyConfigs = []
    cp, root = self._live_lane_context()
    self.context.value = AuthorityContext(True, cp, self.context.value.cp_raw)
    page = self.page("torque")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Turn Assist")
    self.assertTrue(page["rows"][index]["available"])
    self.assertEqual(page["rows"][index]["value"], "On")
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value="On")
    self.context.value = AuthorityContext(False, cp, self.context.value.cp_raw)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    (root / "TurnAssist").unlink()
    self.assertTrue(self.page("torque")["rows"][index]["available"])
    self.context.value = AuthorityContext(True, cp, self.context.value.cp_raw)
    page = self.page("torque")
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value="On")
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual((root / "TurnAssist").read_bytes(), b"1")
    self.context.value = unrelated
    self.assertFalse(any(row["label"] == "Turn Assist" for row in self.page("torque")["rows"]))

  def test_turn_assist_nonpolicy_gateway_default_and_saved_off(self):
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.mazda.values import CAR
    from openpilot.starpilot.lateral.controller_selection import policy_for
    cp = interfaces[CAR.MAZDA_CX5_2022].get_non_essential_params(CAR.MAZDA_CX5_2022)
    self.assertEqual(policy_for(cp), 'generic')
    self.context.value = AuthorityContext(True, cp, cp.as_reader().as_builder().to_bytes())
    page = self.page('torque')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Turn Assist')
    self.assertTrue(page['rows'][index]['available'])
    self.assertEqual(page['rows'][index]['value'], 'On')
    intent = self.gateway.preview(page['view'], index, 0, self.session, self.generation, value='Off')
    self.assertTrue(self.gateway.confirm(intent['intent'], self.session, self.generation))
    self.assertEqual(Path(self.params.get_param_path('TurnAssist')).read_bytes(), b'0')
    self.assertEqual(self.page('torque')['rows'][index]['value'], 'Off')

  def test_live_lane_keys_write_through_existing_owner(self):
    _, root = self._live_lane_context()
    for key, value, expected in (("LaneCentering", "On", b"1"),
      ("LaneCenteringPauseOnSignal", "Off", b"0"),
      ("LaneCenterOffset", 0.15, b"0.15"),
      ("LaneCenteringE2EAuthority", 75, b"0.75"),
      ("LaneCenteringStrength", 105, b"1.575")):
      with self.subTest(key=key):
        page = self.page("lane")
        labels = {"LaneCentering": "Enable Lane Centering", "LaneCenteringPauseOnSignal": "Pause on signal",
                  "LaneCenterOffset": "Lane offset", "LaneCenteringE2EAuthority": "Model path preference",
                  "LaneCenteringStrength": "Lane Centering Strength"}
        index = next(i for i, row in enumerate(page["rows"]) if row["label"] == labels[key])
        self.assertTrue(page["rows"][index]["available"])
        intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=value)
        self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
        self.assertEqual((root / key).read_bytes(), expected)
    from openpilot.starpilot.galaxy.settings import _onroad_preference
    for key in ("LongPitch", "AlwaysOnLateral", "AdvancedLateralTune", "CustomCruise"):
      self.assertTrue(_onroad_preference("torque", key))
      self.assertTrue(_onroad_preference("lane", key))
    self.assertFalse(_onroad_preference("torque", "reset_profiles"))

  def test_live_lane_rejects_unsupported_vehicle_and_master_dependency(self):
    self.params.put_bool("LaneCentering", False, block=True)
    tagged, _ = self._live_lane_context()
    page = self.page("lane")
    pause = next(row for row in page["rows"] if row["label"] == "Pause on signal")
    self.assertFalse(pause["available"])
    for flag in ("passive", "notCar", "dashcamOnly"):
      bad = car.CarParams.new_message(**tagged.to_dict())
      setattr(bad, flag, True)
      self.context.value = AuthorityContext(False, bad, bad.as_reader().as_builder().to_bytes())
      self.assertFalse(any(row["available"] for row in self.page("lane")["rows"]))
    self.context.value = AuthorityContext(False, SimpleNamespace(brand="unknown", carFingerprint="OTHER", notCar=False,
      dashcamOnly=False, passive=False), b"other")
    page = self.page("lane")
    rows = page["rows"]
    self.assertFalse(next(row for row in rows if row["label"] == "Lane Centering Strength")["available"])
    index = next(i for i, row in enumerate(rows) if row["label"] == "Enable Lane Centering")
    self.assertFalse(rows[index]["available"])
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(page["view"], index, 1, self.session, self.generation)

  def test_live_lane_retains_range_session_source_and_cp_guards(self):
    tagged, root = self._live_lane_context()
    page = self.page("lane")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Lane Centering Strength")
    for value in (32, 201, float("nan")):
      with self.subTest(value=value), self.assertRaises(SettingsChanged):
        self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=value)
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(page["view"], index, 0, "other-session", self.generation, value=105)
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=105)
    self.context.value = AuthorityContext(False, tagged, b"changed-cp")
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)
    self.context.value = AuthorityContext(False, tagged, tagged.as_reader().as_builder().to_bytes())
    page = self.page("lane")
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=105)
    (root / "LaneCenteringStrength").write_bytes(b"1.1")
    self.assertFalse(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual((root / "LaneCenteringStrength").read_bytes(), b"1.1")

  def test_lane_strength_shared_owner_and_vehicle_admission(self):
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
    self.gateway = SettingsGateway(Files(), self.context, clock=lambda: 100.0)
    self.context.value.cp.safetyConfigs = []
    page = self.page("lane")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Lane Centering Strength")
    self.assertFalse(page["rows"][index]["available"])
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=200)
    self.context.value = AuthorityContext(True, tagged, tagged.as_reader().as_builder().to_bytes())
    page = self.page("lane")
    self.assertTrue(page["rows"][index]["available"])
    self.assertEqual((page["rows"][index]["minimum"], page["rows"][index]["maximum"]), (33, 200))
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=200)
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertEqual((root / "LaneCenteringStrength").read_bytes(), b"3.0")
    self.assertIsNone(self.params.get("LaneCenteringE2EAuthority"))

  def test_uploads_missing_default_off_saved_values_and_vehicle_independence(self):
    # Native keys tested separately.
    root = Path(self.params.get_param_path("IsMetric")).parent
    class Files:
      def get_param_path(self, key):
        return str(root / key)
      def remove(self, key):
        (root / key).unlink(missing_ok=True)
      def put(self, key, value, block=True):
        (root / key).write_bytes(value if isinstance(value, bytes) else str(value).encode())
      def put_bool(self, key, value, block=True):
        self.put(key, "1" if value else "0", block=block)
      def get(self, key):
        path = root / key
        return path.read_bytes() == b"1" if path.exists() else None
    self.params = Files()
    self.gateway = SettingsGateway(self.params, self.context, clock=lambda: 100.0)
    self.context.value = AuthorityContext(False, None, None)
    for saved in (None, False, True):
      if saved is None:
        self.params.remove("AlwaysAllowUploads")
      else:
        self.params.put_bool("AlwaysAllowUploads", saved, block=True)
      page = self.page("data")
      row = page["rows"][0]
      self.assertEqual(row["value"], "On" if saved else "Off")
      self.assertTrue(row["available"])
      self.assertIn("mobile data", row["reason"])
      intent = self.gateway.preview(page["view"], 0, 0, self.session, self.generation,
                                     value="Off" if saved else "On")
      self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
      self.assertEqual(self.params.get("AlwaysAllowUploads"), not bool(saved))
      self.assertIsNone(self.params.get("RecordFront"))
      self.assertIsNone(self.params.get("RecordFrontLock"))

  def test_data_page_has_only_upload_preference(self):
    self.context.value = AuthorityContext(False, None, None)
    page = self.page("data")
    self.assertEqual([row["label"] for row in page["rows"]], ["Always Allow Uploads"])
    self.assertTrue(page["rows"][0]["available"])

  def test_force_stop_off_onroad_with_master_off_and_session_vehicle_guards(self):
    cp = self.context.value.cp
    self.context.value = AuthorityContext(False, cp, b"verified-cp")
    self.params.put_bool("ForceStops", True, block=True)
    page = self.page("profiles")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Force Stop")
    self.assertTrue(page["rows"][index]["available"])
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value="Off")
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation, session_valid=lambda: False)
    page = self.page("profiles")
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value="Off")
    self.context.value = AuthorityContext(False, cp, b"changed-cp")
    with self.assertRaises(SettingsChanged):
      self.gateway.confirm(intent["intent"], self.session, self.generation)
    self.context.value = AuthorityContext(False, cp, b"verified-cp")
    page = self.page("profiles")
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value="Off")
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertFalse(self.params.get("ForceStops"))
    self.assertIsNone(self.params.get("QOLLongitudinal"))
    page = self.page("profiles")
    self.assertTrue(page["rows"][index]["available"])
    intent = self.gateway.preview(page["view"], index, 0, self.session, self.generation, value="On")
    self.assertTrue(self.gateway.confirm(intent["intent"], self.session, self.generation))
    self.assertTrue(self.params.get("ForceStops"))


class FakeMessages:
  def __init__(self, mono_now, boot_now):
    self.mono_now = mono_now
    self.boot_now = boot_now
    self.updated = {"deviceState": True, "pandaStates": True}
    self.seen = {"deviceState": True, "pandaStates": True}
    self.alive = {"deviceState": True, "pandaStates": True}
    self.valid = {"deviceState": True, "pandaStates": True}
    self.logMonoTime = {"deviceState": mono_now - 50_000_000, "pandaStates": boot_now - 50_000_000}
    self.recv_time = {"deviceState": (mono_now - 40_000_000) / 1e9,
                      "pandaStates": (mono_now - 40_000_000) / 1e9}
    self.device = SimpleNamespace(started=False)
    self.pandas = [SimpleNamespace(ignitionLine=False, ignitionCan=False)]
    self.calls = []

  def update(self, timeout):
    self.calls.append(timeout)

  def __getitem__(self, name):
    return self.device if name == "deviceState" else self.pandas


class LiveContextTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.params.put_bool("IsOffroad", True, block=True)
    self.mono = 10_000_000_000
    self.boot = 20_000_000_000
    self.wait_now = 0.0
    self.messages = FakeMessages(self.mono, self.boot)
    self.source = LiveContextSource(self.params, self.messages, mono_clock=lambda: self.mono,
                                    boot_clock=lambda: self.boot, evidence_wait_ms=0)
    self.mono += 100_000_000
    self.boot += 100_000_000
    self.messages.logMonoTime["deviceState"] = self.mono - 20_000_000
    self.messages.logMonoTime["pandaStates"] = self.boot - 20_000_000
    self.messages.recv_time = {"deviceState": (self.mono - 10_000_000) / 1e9,
                               "pandaStates": (self.mono - 10_000_000) / 1e9}

  def queued_messages(self, on_panda=None):
    messages = FakeMessages(self.mono, self.boot)
    messages.seen = {"deviceState": False, "pandaStates": False}
    messages.updated = {"deviceState": False, "pandaStates": False}
    events = iter((None, "deviceState", "pandaStates"))

    def update(timeout):
      messages.calls.append(timeout)
      self.wait_now += timeout / 1000
      event = next(events)
      messages.updated = {"deviceState": False, "pandaStates": False}
      if event is None:
        return
      self.mono += 100_000_000
      self.boot += 100_000_000
      messages.seen[event] = messages.updated[event] = True
      messages.valid[event] = True
      messages.logMonoTime[event] = (self.mono if event == "deviceState" else self.boot) - 1_000_000
      messages.recv_time[event] = self.mono / 1e9
      if event == "pandaStates" and on_panda is not None:
        on_panda(messages)

    messages.update = mock.Mock(side_effect=update)
    return messages

  def test_actual_clock_domains_and_suspend_barrier(self):
    self.assertTrue(self.source.sample().parked)
    self.messages.updated["deviceState"] = False
    self.boot += 1_600_000_000
    self.assertFalse(self.source.sample().parked)  # Mono stamp ages against boot on resume.
    # Pre-resume frames stay blocked.
    self.messages.updated["deviceState"] = True
    self.assertFalse(self.source.sample().parked)
    self.mono += 30_000_000
    self.boot += 30_000_000
    self.messages.logMonoTime["deviceState"] = self.mono - 1_000_000
    self.messages.logMonoTime["pandaStates"] = self.boot - 1_000_000
    self.messages.recv_time = {"deviceState": self.mono / 1e9, "pandaStates": self.mono / 1e9}
    self.assertTrue(self.source.sample().parked)
    self.boot -= 1_600_000_000
    self.messages.logMonoTime["pandaStates"] = self.mono - 50_000_000  # Wrong publisher domain.
    self.assertFalse(self.source.sample().parked)

  def test_startup_queued_frame_and_unbounded_clock_pair(self):
    # Queued frames predate this source.
    source = LiveContextSource(self.params, self.messages, mono_clock=lambda: self.mono,
                               boot_clock=lambda: self.boot, evidence_wait_ms=0)
    self.assertFalse(source.sample().parked)
    self.mono += 20_000_000
    self.boot += 20_000_000
    self.messages.logMonoTime["deviceState"] = self.mono - 1_000_000
    self.messages.logMonoTime["pandaStates"] = self.boot - 1_000_000
    self.messages.recv_time = {"deviceState": self.mono / 1e9, "pandaStates": self.mono / 1e9}
    self.assertTrue(source.sample().parked)
    from itertools import chain, repeat
    readings = chain((self.mono, self.mono, self.mono + 6_000_000), repeat(self.mono + 6_000_000))
    jittered = LiveContextSource(self.params, self.messages, mono_clock=lambda: next(readings),
                                 boot_clock=lambda: self.boot, evidence_wait_ms=0)
    self.assertFalse(jittered.sample().parked)

  def test_manager_device_and_panda_authority(self):
    self.assertTrue(self.source.sample().parked)
    self.params.put_bool("IsOffroad", False, block=True)
    self.assertFalse(self.source.sample().parked)
    self.params.put_bool("IsOffroad", True, block=True)
    self.messages.pandas[0].ignitionCan = True
    self.assertTrue(self.source.sample().parked)  # Offroad permits config with ignition.
    self.assertTrue(self.source.parked())       # Operations use effective offroad.
    self.messages.pandas[0].ignitionCan = False
    self.messages.device.started = True
    self.assertFalse(self.source.sample().parked)

  def test_first_parked_sample_waits_for_both_live_publishers(self):
    messages = self.queued_messages()
    source = LiveContextSource(self.params, messages, mono_clock=lambda: self.mono,
                               boot_clock=lambda: self.boot, wait_clock=lambda: self.wait_now)
    self.assertTrue(source.parked())
    self.assertEqual(messages.calls[0], 0)
    self.assertEqual(len(messages.calls), 3)
    self.assertTrue(all(0 < timeout <= 50 for timeout in messages.calls[1:]))

  def test_wait_never_admits_missing_or_known_unsafe_evidence(self):
    messages = FakeMessages(self.mono, self.boot)
    messages.device.started = True
    source = LiveContextSource(self.params, messages, mono_clock=lambda: self.mono,
                               boot_clock=lambda: self.boot)
    self.assertFalse(source.parked())
    self.assertEqual(messages.calls, [0])
    messages.pandas[0].ignitionCan = False
    messages.device.started = True
    self.assertFalse(source.parked())
    self.assertEqual(messages.calls, [0, 0])

    messages.device.started = False
    messages.seen["pandaStates"] = False
    def wait_update(timeout):
      messages.calls.append(timeout)
      self.wait_now += timeout / 1000
    messages.update = mock.Mock(side_effect=wait_update)
    source = LiveContextSource(self.params, messages, mono_clock=lambda: self.mono,
      boot_clock=lambda: self.boot, evidence_wait_ms=20,
      wait_clock=lambda: self.wait_now)
    self.assertFalse(source.parked())
    self.assertEqual(messages.calls[2], 0)
    self.assertTrue(any(timeout > 0 for timeout in messages.calls[3:]))

  def test_wait_rechecks_manager_offroad_before_admission(self):
    messages = self.queued_messages(lambda _: self.params.put_bool("IsOffroad", False, block=True))
    source = LiveContextSource(self.params, messages, mono_clock=lambda: self.mono,
                               boot_clock=lambda: self.boot, wait_clock=lambda: self.wait_now)
    self.assertFalse(source.parked())
    self.assertEqual(len(messages.calls), 3)

  def test_wait_allows_ignition_when_effective_state_is_offroad(self):
    messages = self.queued_messages(lambda m: setattr(m.pandas[0], "ignitionLine", True))
    source = LiveContextSource(self.params, messages, mono_clock=lambda: self.mono,
                               boot_clock=lambda: self.boot, wait_clock=lambda: self.wait_now)
    self.assertTrue(source.parked())
    self.assertEqual(len(messages.calls), 3)

  def test_wait_rejects_stale_invalid_then_accepts_new_valid_frames(self):
    messages = self.queued_messages()
    messages.seen = {"deviceState": True, "pandaStates": True}
    messages.valid["deviceState"] = False
    messages.logMonoTime = {"deviceState": self.mono - 2_000_000_000,
                            "pandaStates": self.boot - 2_000_000_000}
    source = LiveContextSource(self.params, messages, mono_clock=lambda: self.mono,
                               boot_clock=lambda: self.boot, wait_clock=lambda: self.wait_now)
    self.assertTrue(source.parked())
    self.assertEqual(len(messages.calls), 3)

  def test_wait_requires_post_resume_publisher_frames(self):
    messages = self.messages
    self.assertTrue(self.source.parked())
    self.boot += 1_600_000_000
    events = iter((None, "deviceState", "pandaStates"))

    def update(timeout):
      messages.calls.append(timeout)
      self.wait_now += timeout / 1000
      event = next(events)
      messages.updated = {"deviceState": False, "pandaStates": False}
      if event is None:
        return
      self.mono += 100_000_000
      self.boot += 100_000_000
      messages.updated[event] = True
      messages.logMonoTime[event] = (self.mono if event == "deviceState" else self.boot) - 1_000_000
      messages.recv_time[event] = self.mono / 1e9

    messages.update = mock.Mock(side_effect=update)
    self.source.evidence_wait_ms = 750
    self.source.wait_clock = lambda: self.wait_now
    self.assertTrue(self.source.parked())
    self.assertEqual(messages.calls[-3:], [0, 50, 50])

  def test_actual_verified_cp_envelope(self):
    from opendbc.car.structs import car
    from openpilot.starpilot.schema_cache import put_cache
    cp = car.CarParams.new_message()
    cp.carFingerprint = "TOYOTA COROLLA TSS2"
    cp.brand = "toyota"
    cp.openpilotLongitudinalControl = True
    put_cache(self.params, "CarParamsPersistent", cp, block=True)
    snapshot = self.source.sample()
    self.assertTrue(snapshot.parked)
    self.assertEqual(snapshot.cp.carFingerprint, "TOYOTA COROLLA TSS2")
    path = Path(self.params.get_param_path("CarParamsPersistent"))
    self.assertTrue(snapshot.cp_raw.endswith(path.read_bytes()))
    self.assertTrue(snapshot.cp_raw.startswith(b"settings-configuration:"))
    path.write_bytes(b"not a versioned cache")
    self.assertIsNone(self.source.sample().cp)
    self.assertEqual(path.read_bytes(), b"not a versioned cache")


class SettingsHttpTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    native = ToyotaInterface.get_non_essential_params(ToyotaCar.TOYOTA_COROLLA_TSS2)
    cp = SimpleNamespace(carFingerprint="TOYOTA_COROLLA_TSS2", openpilotLongitudinalControl=True,
      pcmCruise=False, notCar=False, dashcamOnly=False, passive=False,
      brand="toyota", steerControlType=native.steerControlType, carVin="",
      lateralTuning=native.lateralTuning, minSteerSpeed=native.minSteerSpeed, safetyConfigs=native.safetyConfigs,
      transmissionType=car.CarParams.TransmissionType.automatic)
    self.context = MutableContext(cp)
    self.gateway = SettingsGateway(self.params, self.context)
    self.access = GalaxyAccessOwner(Path(temporary.name) / "access")
    self.assertTrue(self.access.configure("password123", lambda: True))
    self.server = make_server(port=0, owner=self.access, settings=self.gateway)
    self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    self.worker.start()
    self.addCleanup(self.stop)

  def stop(self):
    self.server.shutdown()
    self.worker.join(timeout=2)
    self.server.server_close()

  def request(self, path, *, payload=None, cookie="", origin=None):
    connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
    headers = {"Cookie": cookie}
    if payload is not None:
      headers.update({"Content-Type": "application/json",
                      "Origin": origin if origin is not None else f"http://127.0.0.1:{self.server.server_port}"})
    try:
      connection.request("POST" if payload is not None else "GET", path,
                         body=json.dumps(payload) if payload is not None else None, headers=headers)
      result = connection.getresponse()
      return result.status, json.loads(result.read()), dict(result.getheaders())
    finally:
      connection.close()

  def login(self):
    status, _, headers = self.request("/api/auth/login", payload={"password": "password123"})
    self.assertEqual(status, 200)
    return headers["Set-Cookie"].split(";", 1)[0]

  def test_force_stop_disable_http_while_onroad(self):
    self.context.value = AuthorityContext(False, self.context.value.cp, b"verified-cp")
    self.params.put_bool("ForceStops", True, block=True)
    cookie = self.login()
    status, page, _ = self.request("/api/settings/pages/profiles", cookie=cookie)
    self.assertEqual(status, 200)
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Force Stop")
    self.assertTrue(page["rows"][index]["available"])
    status, intent, _ = self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": index, "value": "Off"})
    self.assertEqual(status, 200)
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                 payload={"intent": intent["intent"], "confirmed": True})[:2], (200, {"saved": True}))
    self.assertFalse(self.params.get("ForceStops"))
    self.assertIsNone(self.params.get("QOLLongitudinal"))
    status, page, _ = self.request("/api/settings/pages/profiles", cookie=cookie)
    self.assertEqual(status, 200)
    self.assertTrue(page["rows"][index]["available"])
    self.assertEqual(self.request("/api/settings/preview", cookie=cookie,
                                 payload={"view": page["view"], "row": index, "value": "On"})[0], 200)

  def test_gm_distance_traffic_assignment_http_and_vehicle_change(self):
    from opendbc.car.gm.tests.test_bolt_pedal import params as pedal_params
    from opendbc.car.gm.values import CAR
    cp = pedal_params(CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, setting=True, pedal=True)
    self.context.value = AuthorityContext(True, cp, cp.as_reader().as_builder().to_bytes())
    cookie = self.login()
    def preview():
      status, page, _ = self.request("/api/settings/pages/wheel", cookie=cookie)
      self.assertEqual(status, 200)
      self.assertFalse(any(row['label'].startswith(('MODE', 'Star button')) for row in page['rows']))
      index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Distance short press')
      self.assertTrue(page['rows'][index]['available'])
      status, intent, _ = self.request('/api/settings/preview', cookie=cookie,
        payload={'view': page['view'], 'row': index, 'value': 'Traffic mode'})
      self.assertEqual(status, 200)
      return intent['intent']
    intent = preview()
    self.assertIsNone(self.params.get('DistanceButtonControl'))
    self.assertEqual(self.request('/api/settings/confirm', cookie=cookie,
                                 payload={'intent': intent, 'confirmed': True})[:2], (200, {'saved': True}))
    self.assertEqual(self.params.get('DistanceButtonControl'), 6)
    self.assertIsNone(self.params.get('ConditionalModeConfig'))
    intent = preview()
    self.context.value = AuthorityContext(False, cp, cp.as_reader().as_builder().to_bytes())
    self.assertEqual(self.request('/api/settings/confirm', cookie=cookie,
                                 payload={'intent': intent, 'confirmed': True})[:2], (200, {'saved': True}))
    intent = preview()
    self.context.value = AuthorityContext(False, cp, b'different-cp')
    self.assertEqual(self.request('/api/settings/confirm', cookie=cookie,
                                 payload={'intent': intent, 'confirmed': True})[0], 409)
    self.context.value = AuthorityContext(True, cp, cp.as_reader().as_builder().to_bytes())
    intent = preview()
    cp.openpilotLongitudinalControl = False
    self.context.value = AuthorityContext(True, cp, cp.as_reader().as_builder().to_bytes())
    self.assertEqual(self.request('/api/settings/confirm', cookie=cookie,
                                 payload={'intent': intent, 'confirmed': True})[0], 409)
    self.assertEqual(self.params.get('DistanceButtonControl'), 6)

  def test_profile_document_staged_source_change_and_unverified_readback(self):
    cookie = self.login()
    path = Path(self.params.get_param_path("LongitudinalPersonalityProfiles"))

    def preview():
      status, page, _ = self.request("/api/settings/pages/profiles", cookie=cookie)
      self.assertEqual(status, 200)
      index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Selected Deceleration Profile")
      status, result, _ = self.request("/api/settings/preview", cookie=cookie,
        payload={"view": page["view"], "row": index, "value": "Comfort"})
      self.assertEqual(status, 200)
      return result["intent"]

    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                  payload={"intent": preview(), "confirmed": True})[:2], (200, {"saved": True}))
    self.assertEqual(json.loads(path.read_bytes())["selectedDecelerationProfile"], "eco")

    intent = preview()
    concurrent = json.loads(path.read_bytes())
    concurrent["selectedDecelerationProfile"] = "standard"
    external = json.dumps(concurrent).encode()
    actual_fsync = os.fsync
    calls = 0

    def staged_fsync(fd):
      nonlocal calls
      actual_fsync(fd)
      calls += 1
      if calls == 1:
        path.write_bytes(external)

    with mock.patch.object(saved_document.os, "fsync", side_effect=staged_fsync):
      self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                    payload={"intent": intent, "confirmed": True})[0], 409)
    self.assertEqual(path.read_bytes(), external)

    intent = preview()
    actual_read = saved_document.read_saved
    calls = 0

    def unverified(*args):
      nonlocal calls
      calls += 1
      return (b"unverified", True) if calls == 3 else actual_read(*args)

    with mock.patch.object(saved_document, "read_saved", side_effect=unverified):
      self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                    payload={"intent": intent, "confirmed": True})[0], 409)
    self.assertEqual(calls, 3)
    self.assertEqual(json.loads(path.read_bytes())["selectedDecelerationProfile"], "eco")

  def test_direct_choice_over_http_and_ambiguous_payload_rejected(self):
    cookie = self.login()
    status, page, _ = self.request("/api/settings/pages/profiles", cookie=cookie)
    self.assertEqual(status, 200)
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Use StarPilot Longitudinal Planner")
    payload = {"view": page["view"], "row": index, "value": "On"}
    self.assertEqual(self.request("/api/settings/preview", cookie=cookie, payload=payload | {"direction": 1})[0], 400)
    self.assertEqual(self.request("/api/settings/preview", cookie=cookie, payload=payload | {"value": True})[0], 400)
    self.assertEqual(self.request("/api/settings/preview", payload=payload)[0], 401)
    status, intent, _ = self.request("/api/settings/preview", cookie=cookie, payload=payload)
    self.assertEqual(status, 200)
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                  payload={"intent": intent["intent"], "confirmed": True})[0], 200)
    self.assertTrue(self.params.get_bool("UseStarPilotLongitudinalPlanner"))

  def test_sound_and_display_choices_over_authenticated_http(self):
    self.context.value = AuthorityContext(True, None, None)
    self.assertEqual(self.request("/api/settings/pages/sounds")[0], 401)
    cookie = self.login()
    status, page, _ = self.request("/api/settings/pages/sounds", cookie=cookie)
    self.assertEqual(status, 200)
    warning = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Immediate Warning")
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": warning, "direction": 1})
    self.assertEqual(status, 200)
    self.assertEqual(preview["proposed"], "25%")
    self.assertIsNone(self.params.get("WarningImmediateVolume"))
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                  payload={"intent": preview["intent"], "confirmed": True})[:2],
                     (200, {"saved": True}))
    self.assertEqual(self.params.get("WarningImmediateVolume"), 25)

    status, page, _ = self.request("/api/settings/pages/display", cookie=cookie)
    self.assertEqual(status, 200)
    master = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Custom Display Settings")
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": master, "direction": 1})
    self.assertEqual(status, 200)
    self.context.value = AuthorityContext(False, None, None)
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                  payload={"intent": preview["intent"], "confirmed": True})[0], 200)
    self.assertTrue(self.params.get_bool("StarPilotDisplayPreferencesEnabled"))

  def test_compact_border_choices_use_authenticated_parked_confirmation(self):
    from openpilot.starpilot.ui.appearance_preferences import read_visibility

    self.context.value = AuthorityContext(True, None, None)
    self.assertEqual(self.request("/api/settings/pages/appearance")[0], 401)
    cookie = self.login()
    status, page, _ = self.request("/api/settings/pages/appearance", cookie=cookie)
    self.assertEqual(status, 200)
    signal = next(i for i, row in enumerate(page["rows"]) if row["label"] == "C4 amber signal border")
    blindspot = next(i for i, row in enumerate(page["rows"]) if row["label"] == "C4 red blind-spot border")
    self.assertEqual([page["rows"][index]["value"] for index in (signal, blindspot)], ["Off", "On"])
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": signal, "direction": 1})
    self.assertEqual(status, 200)
    self.assertIsNone(read_visibility(self.params, "SignalMetrics").raw)
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                  payload={"intent": preview["intent"], "confirmed": True})[:2],
                     (200, {"saved": True}))
    self.assertEqual(read_visibility(self.params, "SignalMetrics").raw, b"1")
    page = self.request("/api/settings/pages/appearance", cookie=cookie)[1]
    blindspot = next(i for i, row in enumerate(page["rows"]) if row["label"] == "C4 red blind-spot border")
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": blindspot, "direction": -1})
    self.assertEqual(status, 200)
    self.context.value = AuthorityContext(False, None, None)
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                  payload={"intent": preview["intent"], "confirmed": True})[0], 200)
    self.assertEqual(read_visibility(self.params, "BlindSpotMetrics").raw, b"0")

  def test_pip_format_through_authenticated_http_without_vehicle_cp(self):
    from openpilot.starpilot.ui.pip_preferences import MASK, read_pip
    self.context.value = AuthorityContext(True, None, None)
    with mock.patch.object(self.gateway, "page", wraps=self.gateway.page) as page_read:
      self.assertEqual(self.request("/api/settings/pages/pip")[0], 401)
      page_read.assert_not_called()
    cookie = self.login()
    status, page, _ = self.request("/api/settings/pages/pip", cookie=cookie)
    self.assertEqual(status, 200)
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Restore default camera crop")
    self.assertTrue(page["rows"][index]["confirm"])
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": index, "direction": 0})
    self.assertEqual(status, 200)
    self.assertIsNone(self.params.get(MASK))
    status, result, _ = self.request("/api/settings/confirm", cookie=cookie,
      payload={"intent": preview["intent"], "confirmed": True})
    self.assertEqual((status, result), (200, {"saved": True}))
    saved = read_pip(self.params)
    self.assertEqual((saved.mask.width, saved.mask.height), (1928, 1208))
    self.assertFalse(saved.enabled)
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                  payload={"intent": preview["intent"], "confirmed": True})[0], 409)

  def test_small_pip_format_direct_center_save_and_native_step(self):
    from openpilot.starpilot.ui.pip_preferences import MASK, encode_mask, read_pip, starting_mask

    self.context.value = AuthorityContext(True, None, None)
    original = starting_mask(1344, 760)
    self.assertEqual(original.crop_size, 365)
    Path(self.params.get_param_path(MASK)).write_bytes(encode_mask(original))
    cookie = self.login()
    status, page, _ = self.request("/api/settings/pages/pip", cookie=cookie)
    self.assertEqual(status, 200)
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Vehicle right crop X")
    row = page["rows"][index]
    selected = row["minimum"] + row["step"]
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
                                    payload={"view": page["view"], "row": index, "value": selected})
    self.assertEqual(status, 200)
    self.assertEqual(read_pip(self.params).mask, original)
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                 payload={"intent": preview["intent"], "confirmed": True})[:2],
                     (200, {"saved": True}))
    saved = read_pip(self.params)
    assert original.center_left is not None
    self.assertEqual((row["minimum"], row["maximum"], row["step"]), (183, 1161, 10))
    self.assertEqual(saved.mask.center_left, (193, original.center_left[1]))
    self.assertEqual(saved.mask.center_right, original.center_right)
    self.assertEqual(saved.mask.crop_size, original.crop_size)
    self.assertFalse(saved.enabled)

    page = self.request("/api/settings/pages/pip", cookie=cookie)[1]
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
                                    payload={"view": page["view"], "row": index, "direction": 1})
    self.assertEqual(status, 200)
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                 payload={"intent": preview["intent"], "confirmed": True})[:2],
                     (200, {"saved": True}))
    updated = read_pip(self.params)
    assert updated.mask.center_left is not None
    self.assertEqual(updated.mask.center_left[0], 203)

  def test_sentry_saved_motion_settings_over_authenticated_http(self):
    from openpilot.starpilot.sentry_mode.preferences import KEY, decode
    self.context.value = AuthorityContext(True, None, None)
    with mock.patch.object(self.gateway, "page", wraps=self.gateway.page) as page_read:
      self.assertEqual(self.request("/api/settings/pages/sentry")[0], 401)
      page_read.assert_not_called()
    cookie = self.login()
    status, page, _ = self.request("/api/settings/pages/sentry", cookie=cookie)
    self.assertEqual(status, 200)
    self.assertEqual(page["rows"][0]["value"], "Off")
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": 0, "direction": 1})
    self.assertEqual(status, 200)
    self.assertFalse(Path(self.params.get_param_path(KEY)).exists())
    status, result, _ = self.request("/api/settings/confirm", cookie=cookie,
      payload={"intent": preview["intent"], "confirmed": True})
    self.assertEqual((status, result), (200, {"saved": True}))
    self.assertTrue(decode(Path(self.params.get_param_path(KEY)).read_bytes()).enabled)
    page = self.request("/api/settings/pages/sentry", cookie=cookie)[1]
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": 1, "direction": 1})
    self.assertEqual(status, 200)
    self.assertEqual(self.request("/api/auth/logout", cookie=cookie, payload={})[0], 200)
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                  payload={"intent": preview["intent"], "confirmed": True})[0], 401)
    self.assertEqual(decode(Path(self.params.get_param_path(KEY)).read_bytes()).settings.sensitivity, 0.04)

  def test_pip_http_rejects_native_change_and_logout_but_allows_drive_transition(self):
    from openpilot.starpilot.ui.pip_preferences import BLINKER, MASK, encode_mask, starting_mask
    self.context.value = AuthorityContext(True, None, None)
    cookie = self.login()
    for failure in ("native_change", "onroad", "logout"):
      with self.subTest(failure=failure):
        self.context.value = AuthorityContext(True, None, None)
        status, page, _ = self.request("/api/settings/pages/pip", cookie=cookie)
        self.assertEqual(status, 200)
        index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Show on turn signal")
        status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
          payload={"view": page["view"], "row": index, "direction": 1})
        self.assertEqual(status, 200)
        if failure == "native_change":
          Path(self.params.get_param_path(MASK)).write_bytes(encode_mask(starting_mask(1344, 760)))
        elif failure == "onroad":
          self.context.value = AuthorityContext(False, None, None)
        else:
          self.assertEqual(self.request("/api/auth/logout", cookie=cookie, payload={})[0], 200)
        status = self.request("/api/settings/confirm", cookie=cookie,
                              payload={"intent": preview["intent"], "confirmed": True})[0]
        self.assertEqual(status, 401 if failure == "logout" else 200 if failure == "onroad" else 409)
        self.assertEqual(self.params.get(BLINKER), True if failure == "onroad" else None)
        self.params.remove(BLINKER)

  def test_curve_page_saved_switch_over_authenticated_http(self):
    cookie = self.login()
    status, page, _ = self.request("/api/settings/pages/curve", cookie=cookie)
    self.assertEqual(status, 200)
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Curve Speed Controller")
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": index, "direction": 1})
    self.assertEqual(status, 200)
    self.assertFalse(self.params.get_bool("CurveSpeedController"))
    status, result, _ = self.request("/api/settings/confirm", cookie=cookie,
      payload={"intent": preview["intent"], "confirmed": True})
    self.assertEqual((status, result), (200, {"saved": True}))
    self.assertTrue(self.params.get_bool("CurveSpeedController"))

  def test_conditional_mode_over_authenticated_loopback_http(self):
    from openpilot.starpilot.conditional_mode.preferences import decode_preferences
    self.context.value.cp.pcmCruise = True
    cookie = self.login()
    status, page, _ = self.request("/api/settings/pages/conditional", cookie=cookie)
    self.assertEqual(status, 200)
    self.assertEqual(page["rows"][0]["value"], "Conditional Experimental")
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": 0, "direction": 1})
    self.assertEqual(status, 200)
    self.assertIsNone(self.params.get("ConditionalModeConfig"))
    self.context.value = AuthorityContext(False, self.context.value.cp, self.context.value.cp_raw)
    status, result, _ = self.request("/api/settings/confirm", cookie=cookie,
      payload={"intent": preview["intent"], "confirmed": True})
    self.assertEqual((status, result), (200, {"saved": True}))
    saved = Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes()
    self.assertEqual(decode_preferences(saved).mode.value, "conditional_chill")

  def test_conditional_manual_persist_over_authenticated_loopback_http(self):
    from openpilot.starpilot.conditional_mode.manual_saved import SavedCodes, decode, encode
    from openpilot.starpilot.conditional_mode.preferences import decode_preferences
    manual = Path(self.params.get_param_path("ConditionalManualState"))
    manual.write_bytes(encode(SavedCodes(cem=2, ccm=1)))
    cookie = self.login()
    status, page, _ = self.request("/api/settings/pages/conditional%2Fcem", cookie=cookie)
    self.assertEqual(status, 200)
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Remember manual choice")
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": index, "direction": 1})
    self.assertEqual(status, 200)
    status, result, _ = self.request("/api/settings/confirm", cookie=cookie,
      payload={"intent": preview["intent"], "confirmed": True})
    self.assertEqual((status, result), (200, {"saved": True}))
    self.assertEqual(decode(manual.read_bytes()), SavedCodes(cem=0, ccm=1))
    self.assertTrue(decode_preferences(Path(self.params.get_param_path("ConditionalModeConfig")).read_bytes()).cem.persist_manual)

  def test_curve_learning_action_over_authenticated_http(self):
    Path(self.params.get_param_path("CurveComfortData")).write_bytes(b"{")
    legacy = Path(self.params.get_param_path("CurvatureData"))
    legacy.write_bytes(b'{}')
    cookie = self.login()
    status, page, _ = self.request("/api/settings/pages/curve", cookie=cookie)
    self.assertEqual(status, 200)
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Reset Saved Curve Learning")
    self.assertTrue(page["rows"][index]["confirm"])
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": index, "direction": 0})
    self.assertEqual(status, 200)
    self.assertEqual(Path(self.params.get_param_path("CurveComfortData")).read_bytes(), b"{")
    status, result, _ = self.request("/api/settings/confirm", cookie=cookie,
      payload={"intent": preview["intent"], "confirmed": True})
    self.assertEqual((status, result), (200, {"saved": True}))
    self.assertEqual(Path(self.params.get_param_path("CurveComfortData")).read_bytes(), b'{"version":1,"buckets":{}}')
    self.assertEqual(legacy.read_bytes(), b'{}')

  def test_auth_precedes_source_read_and_http_save(self):
    self.params.put_bool("LaneCentering", False, block=True)
    calls = 0
    original = self.gateway.page
    def counted(*args):
      nonlocal calls
      calls += 1
      return original(*args)
    patcher = mock.patch.object(self.gateway, "page", side_effect=counted)
    patcher.start()
    self.addCleanup(patcher.stop)
    self.assertEqual(self.request("/api/settings/pages/lane")[0], 401)
    self.assertEqual(calls, 0)
    cookie = self.login()
    status, page, _ = self.request("/api/settings/pages/lane", cookie=cookie)
    self.assertEqual(status, 200)
    self.assertEqual(calls, 1)
    self.assertEqual(self.request("/api/settings/pages/aggressive%2Ffollowing", cookie=cookie)[0], 200)
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Enable Lane Centering")
    status, preview, _ = self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": index, "direction": 1})
    self.assertEqual(status, 200)
    self.assertFalse(self.params.get_bool("LaneCentering"))
    status, saved, _ = self.request("/api/settings/confirm", cookie=cookie,
                                    payload={"intent": preview["intent"], "confirmed": True})
    self.assertEqual((status, saved), (200, {"saved": True}))
    self.assertTrue(self.params.get_bool("LaneCentering"))
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                  payload={"intent": preview["intent"], "confirmed": True})[0], 409)

  def test_logout_and_credential_change_revoke_confirmation(self):
    cookie = self.login()
    _, page, _ = self.request("/api/settings/pages/lane", cookie=cookie)
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Enable Lane Centering")
    _, preview, _ = self.request("/api/settings/preview", cookie=cookie,
                                 payload={"view": page["view"], "row": index, "direction": 1})
    self.assertEqual(self.request("/api/auth/logout", cookie=cookie, payload={})[0], 200)
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                  payload={"intent": preview["intent"], "confirmed": True})[0], 401)
    self.assertFalse(self.params.get_bool("LaneCentering"))
    cookie = self.login()
    _, page, _ = self.request("/api/settings/pages/lane", cookie=cookie)
    _, preview, _ = self.request("/api/settings/preview", cookie=cookie,
                                 payload={"view": page["view"], "row": index, "direction": 1})
    self.assertTrue(self.access.remove(lambda: True))
    self.assertTrue(self.access.configure("replacement123", lambda: True))
    self.assertNotEqual(self.request("/api/settings/confirm", cookie=cookie,
                                     payload={"intent": preview["intent"], "confirmed": True})[0], 200)
    self.assertFalse(self.params.get_bool("LaneCentering"))

  def test_invalid_or_cross_origin_request_cannot_write(self):
    cookie = self.login()
    _, page, _ = self.request("/api/settings/pages/lane", cookie=cookie)
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Enable Lane Centering")
    self.assertEqual(self.request("/api/settings/preview", cookie=cookie,
                                  payload={"view": page["view"], "row": True, "direction": 1})[0], 400)
    self.assertEqual(self.request("/api/settings/confirm", cookie=cookie,
                                  payload={"intent": "invented", "confirmed": True})[0], 409)
    self.assertFalse(self.params.get_bool("LaneCentering"))
    self.assertEqual(self.request("/api/settings/preview", cookie=cookie,
      payload={"view": page["view"], "row": index, "direction": 1},
      origin="http://malicious.local")[0], 403)
    self.assertFalse(self.params.get_bool("LaneCentering"))

  def test_confirm_finishes_before_acknowledged_logout(self):
    self.params.put_bool("LaneCentering", False, block=True)
    cookie = self.login()
    _, page, _ = self.request("/api/settings/pages/lane", cookie=cookie)
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Enable Lane Centering")
    _, preview, _ = self.request("/api/settings/preview", cookie=cookie,
                                 payload={"view": page["view"], "row": index, "direction": 1})
    entered, release = threading.Event(), threading.Event()
    original = self.gateway.confirm
    def paused(*args, **kwargs):
      entered.set()
      self.assertTrue(release.wait(2))
      return original(*args, **kwargs)
    patcher = mock.patch.object(self.gateway, "confirm", side_effect=paused)
    patcher.start()
    self.addCleanup(patcher.stop)
    with ThreadPoolExecutor(max_workers=2) as pool:
      confirm = pool.submit(self.request, "/api/settings/confirm", cookie=cookie,
                            payload={"intent": preview["intent"], "confirmed": True})
      self.assertTrue(entered.wait(2))
      logout = pool.submit(self.request, "/api/auth/logout", cookie=cookie, payload={})
      self.assertFalse(logout.done())
      release.set()
      self.assertEqual(confirm.result()[0], 200)
      self.assertEqual(logout.result()[0], 200)
    self.assertTrue(self.params.get_bool("LaneCentering"))

  def test_page_read_revoked_before_response_contains_no_settings(self):
    cookie = self.login()
    entered, release = threading.Event(), threading.Event()
    original = self.gateway.page
    def paused(*args):
      entered.set()
      self.assertTrue(release.wait(2))
      return original(*args)
    patcher = mock.patch.object(self.gateway, "page", side_effect=paused)
    patcher.start()
    self.addCleanup(patcher.stop)
    with ThreadPoolExecutor(max_workers=2) as pool:
      pending = pool.submit(self.request, "/api/settings/pages/lane", cookie=cookie)
      self.assertTrue(entered.wait(2))
      self.assertEqual(self.request("/api/auth/logout", cookie=cookie, payload={})[0], 200)
      release.set()
      status, body, _ = pending.result()
    self.assertEqual(status, 401)
    self.assertNotIn("rows", body)


def required_raw(params, key, limit):
  raw, readable = read_saved(params, key, limit)
  assert readable and raw is not None
  return raw
