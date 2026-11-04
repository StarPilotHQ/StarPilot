"""Onroad visibility preferences through real temporary Params and both native views."""

from dataclasses import replace
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.params import Params
from openpilot.starpilot import saved_document
from openpilot.starpilot.ui import appearance_compact, onroad
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.appearance_preferences import RETIRED_DISPLAY_KEYS, OnroadAppearance, onroad_appearance, read_visibility
from openpilot.starpilot.ui.onroad_customization import default_document
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureInput, FeatureSettingsRequest, FeatureSettingsState, row_change
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert, OnroadInput, OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile
from openpilot.starpilot.ui.settings_state import Destination, SettingsInput, SettingsState, tile_rects
from openpilot.starpilot.ui.shell import ShellInput, ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import NOW, ui_fake


def _hidden_widgets():
  document = default_document()
  document["layouts"]["large"]["current_speed"]["enabled"] = False
  for profile in ("large", "compact"):
    for key in ("steering_wheel", "torque_bar"):
      document["layouts"][profile][key]["enabled"] = False
  return document


class AppearanceSettingsTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    storage = tempfile.TemporaryDirectory()
    self.addCleanup(storage.cleanup)
    self.storage = Path(storage.name)
    self.parked = True
    self.owner = AppearanceOwner(self.params, lambda: self.parked)

  def row(self, key, profile=Profile.LARGE):
    return next(row for row in self.owner.snapshot(profile).rows if row.key == key)

  def test_absent_defaults_are_read_only_and_match_both_views(self):
    self.assertEqual(onroad_appearance(self.params), OnroadAppearance())
    self.assertEqual(tuple(row.key for row in self.owner.snapshot(Profile.LARGE).rows),
                     ("DeveloperSidebar", "HidePersonalityAlerts", "ShowBrakeStatus", "BlindSpotMetrics", "SignalMetrics", "ReduceMotion"))
    self.assertEqual(tuple(row.key for row in self.owner.snapshot(Profile.COMPACT).rows),
                     ("CameraView", "DriverCamera", "StockConfidenceBallWidget", "HidePersonalityAlerts", "ShowBrakeStatus",
                      "HideLeadMarker", "LeadInfo", "SignalMetrics", "BlindSpotMetrics"))
    # Widget visibility, the stopped timer and Rainbow Road moved into the onroad layout.
    for profile in Profile:
      self.assertFalse(set(RETIRED_DISPLAY_KEYS) & {row.key for row in self.owner.snapshot(profile).rows})
    for key in ("HideSpeed", "HideMaxSpeed", "HideSteeringWheel", "DriverCamera", "StoppedTimer",
                "StockConfidenceBallWidget", "EnableTorqueBarWidget", "RainbowPath", "HideLeadMarker", "SignalMetrics", "BlindSpotMetrics"):
      self.assertIsNone(read_visibility(self.params, key).raw)
      self.assertEqual(self.params.get_type(key).name, "BOOL")
    self.assertEqual(self.row("SignalMetrics").value, "Off")
    self.assertEqual(self.row("BlindSpotMetrics").value, "On")
    self.assertEqual((self.row("StockConfidenceBallWidget", Profile.COMPACT).label,
                      self.row("StockConfidenceBallWidget", Profile.COMPACT).value),
                     ("Use StarPilot Widgets", "On"))
    self.assertEqual(self.row("HideLeadMarker", Profile.COMPACT).value, "Off")
    self.assertEqual(self.row("CameraView", Profile.COMPACT).value, "Standard")
    self.assertEqual(self.row("DriverCamera", Profile.COMPACT).value, "Off")
    self.assertIn("border highlights apply to C4 only", self.owner.snapshot(Profile.LARGE).subtitle)

  def test_developer_sidebar_saved_choice_defaults_repair_and_source_guard(self):
    self.assertFalse(onroad_appearance(self.params).show_developer_sidebar)
    self.assertIsNone(read_visibility(self.params, "DeveloperSidebar").raw)
    self.assertEqual(self.params.get_default_value("DeveloperSidebar"), False)
    self.assertNotIn("DeveloperSidebar", {row.key for row in self.owner.snapshot(Profile.COMPACT).rows})
    row = self.row("DeveloperSidebar")
    self.assertEqual((row.label, row.value, row.default_value), ("Developer Sidebar", "Off", "Off"))
    request = row_change(row)
    assert request is not None
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.parked = True
    self.assertTrue(self.owner.apply(request))
    self.assertTrue(onroad_appearance(self.params).show_developer_sidebar)
    self.assertFalse(self.owner.apply(request), "a stale toggle must not overwrite another edit")
    request = row_change(self.row("DeveloperSidebar"))
    assert request is not None
    self.assertTrue(self.owner.apply(request))
    self.assertFalse(onroad_appearance(self.params).show_developer_sidebar)
    source = Path(self.params.get_param_path("DeveloperSidebar"))
    source.write_bytes(b"bad")
    self.assertFalse(onroad_appearance(self.params).show_developer_sidebar)
    self.assertEqual(source.read_bytes(), b"bad")
    request = row_change(self.row("DeveloperSidebar"))
    assert request is not None
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(source.read_bytes(), b"0")

  def test_personality_alert_visibility_is_default_off_and_widget_independent(self):
    from openpilot.starpilot.ui.onroad_customization import read_customization
    before = read_customization(self.params)
    self.assertFalse(onroad_appearance(self.params).hide_personality_alerts)
    self.assertIsNone(self.params.get("HidePersonalityAlerts"))
    self.assertEqual(self.params.get_type("HidePersonalityAlerts").name, "BOOL")
    for profile in (Profile.LARGE, Profile.COMPACT):
      row = self.row("HidePersonalityAlerts", profile)
      self.assertEqual((row.label, row.value, row.default_value), ("Hide Personality Alerts", "Off", "Off"))
      request = row_change(row)
      assert request is not None
      self.parked = False
      self.assertFalse(self.owner.apply(request))
      self.parked = True
      self.assertTrue(self.owner.apply(request))
      self.assertTrue(onroad_appearance(self.params).hide_personality_alerts)
      request = row_change(self.row("HidePersonalityAlerts", profile))
      assert request is not None
      self.assertTrue(self.owner.apply(request))
      self.assertFalse(onroad_appearance(self.params).hide_personality_alerts)
    self.assertEqual(read_customization(self.params), before)
    path = Path(self.params.get_param_path("HidePersonalityAlerts"))
    path.write_bytes(b"bad")
    self.assertFalse(onroad_appearance(self.params).hide_personality_alerts)
    self.assertEqual(path.read_bytes(), b"bad")

  def test_personality_toggle_controls_native_and_existing_projection_layouts(self):
    from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, projection_appearance, validate_layout_for_viewport
    from openpilot.starpilot.ui.tests.test_runtime_snapshot import RuntimeSnapshotAdapter

    viewport = (2880, 1080)
    for profile in (Profile.LARGE, Profile.COMPACT):
      for hidden in (True, False):
        request = row_change(self.row("HidePersonalityAlerts", profile))
        assert request is not None
        self.assertEqual(request.value, "On" if hidden else "Off")
        self.assertTrue(self.owner.apply(request))
        for old_aa_choice in (False, True):
          layout = default_layout_for_viewport(viewport)
          layout.update(hidePersonalityAlerts=old_aa_choice, wheelPedalFeedback=True)
          saved = validate_layout_for_viewport(layout, viewport)
          self.assertIs(saved['hidePersonalityAlerts'], old_aa_choice)
          self.assertTrue(saved['wheelPedalFeedback'])
          ui = ui_fake()
          ui.params = self.params
          source = ui.sm.messages['selfdriveState']
          source.personality = NS(raw=1)
          source.alertSize, source.alertType = NS(raw=1), 'personalityChanged/warning'
          source.alertText1 = 'Driving Personality: Standard'
          for projection in (False, True):
            with self.subTest(profile=profile, hidden=hidden, legacy_aa=old_aa_choice, projection=projection):
              adapter = RuntimeSnapshotAdapter(ui)
              if projection:
                adapter.appearance_override = lambda appearance, document=saved: projection_appearance(appearance, document)
              alert = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.alert
              self.assertEqual(alert.size, AlertSize.NONE if hidden else AlertSize.SMALL)
              source.alertSize, source.alertStatus = NS(raw=3), NS(raw=2)
              source.alertText1 = 'Take control'
              safety = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.alert
              self.assertEqual((safety.size, safety.text1), (AlertSize.FULL, 'Take control'))
              source.alertSize, source.alertStatus = NS(raw=1), NS(raw=0)
              source.alertText1 = 'Driving Personality: Standard'

  def test_personality_toggle_refreshes_on_projection_without_reconnect(self):
    from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, projection_appearance
    from openpilot.starpilot.ui.tests.test_runtime_snapshot import RuntimeSnapshotAdapter

    ui = ui_fake()
    ui.params = self.params
    source = ui.sm.messages['selfdriveState']
    source.personality = NS(raw=1)
    source.alertSize, source.alertType = NS(raw=1), 'personalityChanged/warning'
    source.alertText1 = 'Driving Personality: Standard'
    # A conflicting old AA-only preference must never pin either state.
    layout = {**default_layout_for_viewport((2880, 1080)), 'hidePersonalityAlerts': True}
    adapter = RuntimeSnapshotAdapter(ui)
    adapter.appearance_override = lambda appearance: projection_appearance(appearance, layout)
    for second, hidden in enumerate((False, True, False)):
      self.params.put_bool('HidePersonalityAlerts', hidden, block=True)
      now = NOW + second * 1_000_000_000
      ui.sm.logMonoTime['selfdriveState'] = now
      ui.sm.recv_time['selfdriveState'] = now / 1e9
      alert = adapter.build(ShellMode.ONROAD, now_ns=now).onroad.alert
      self.assertEqual(alert.size, AlertSize.NONE if hidden else AlertSize.SMALL)

  def test_developer_sidebar_external_changes_refresh_without_restart(self):
    from openpilot.starpilot.ui.tests.test_runtime_snapshot import RuntimeSnapshotAdapter
    ui = ui_fake()
    ui.params = self.params
    adapter = RuntimeSnapshotAdapter(ui)
    self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.appearance.show_developer_sidebar)
    self.params.put_bool("DeveloperSidebar", True, block=True)
    self.assertTrue(adapter.build(ShellMode.ONROAD, now_ns=NOW + 1_000_000_000).onroad.appearance.show_developer_sidebar)
    self.params.put_bool("DeveloperSidebar", False, block=True)
    self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW + 2_000_000_000).onroad.appearance.show_developer_sidebar)

  def test_compact_border_saved_controls_repair_and_parked_source_guard(self):
    amber = row_change(self.row("SignalMetrics", Profile.COMPACT))
    assert amber is not None
    self.assertTrue(self.owner.apply(amber))
    self.assertTrue(onroad_appearance(self.params).show_signal_border)
    self.assertFalse(self.owner.apply(amber), "the same source cannot be confirmed twice")
    red = row_change(self.row("BlindSpotMetrics", Profile.COMPACT), -1)
    assert red is not None
    self.assertTrue(self.owner.apply(red))
    self.assertFalse(onroad_appearance(self.params).show_blindspot_border)
    source = Path(self.params.get_param_path("BlindSpotMetrics"))
    source.write_bytes(b"bad")
    repair = row_change(self.row("BlindSpotMetrics", Profile.COMPACT))
    assert repair is not None
    self.assertEqual(repair.value, "On")
    self.parked = False
    self.assertFalse(self.owner.apply(repair))
    self.assertEqual(source.read_bytes(), b"bad")
    self.parked = True
    self.assertTrue(self.owner.apply(repair))
    self.assertEqual(source.read_bytes(), b"1")

  def test_staged_camera_and_visibility_edits_preserve_competing_values(self):
    actual_fsync = os.fsync
    for key, concurrent in (("CameraView", b"3"), ("HideLeadMarker", b"1")):
      with self.subTest(key=key):
        path = Path(self.params.get_param_path(key))
        request = row_change(self.row(key, Profile.COMPACT))
        assert request is not None
        calls = 0

        def stage_fsync(fd, path=path, concurrent=concurrent):
          nonlocal calls
          actual_fsync(fd)
          calls += 1
          if calls == 1:
            path.write_bytes(concurrent)

        with patch.object(saved_document.os, "fsync", side_effect=stage_fsync):
          self.assertFalse(self.owner.apply(request))
        self.assertEqual(path.read_bytes(), concurrent)

  def test_staged_camera_and_visibility_edits_require_parked_until_commit(self):
    actual_fsync = os.fsync
    for key in ("CameraView", "HideLeadMarker"):
      with self.subTest(key=key):
        self.parked = True
        request = row_change(self.row(key, Profile.COMPACT))
        assert request is not None

        def revoke(fd):
          actual_fsync(fd)
          self.parked = False

        with patch.object(saved_document.os, "fsync", side_effect=revoke):
          self.assertFalse(self.owner.apply(request))
        self.assertFalse(Path(self.params.get_param_path(key)).exists())

  def test_widgets_choice_inverts_legacy_stock_ball_without_rewriting_it(self):
    self.assertNotIn("StockConfidenceBallWidget", (row.key for row in self.owner.snapshot(Profile.LARGE).rows))
    legacy = Path(self.params.get_param_path("StockConfidenceBallWidget"))
    legacy.write_bytes(b"1")
    self.assertEqual(self.row("StockConfidenceBallWidget", Profile.COMPACT).value, "Off")
    request = row_change(self.row("StockConfidenceBallWidget", Profile.COMPACT))
    assert request is not None
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.parked = True
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(legacy.read_bytes(), b"0")
    self.assertFalse(onroad_appearance(self.params).show_stock_confidence_ball)
    self.assertFalse(self.owner.apply(request))

  def test_lead_indicator_inverts_exact_saved_choice_and_repairs_invalid_bytes(self):
    self.assertNotIn("HideLeadMarker", (row.key for row in self.owner.snapshot(Profile.LARGE).rows))
    source = Path(self.params.get_param_path("HideLeadMarker"))
    self.assertIsNone(read_visibility(self.params, "HideLeadMarker").raw)
    self.assertFalse(onroad_appearance(self.params).show_lead_indicator)
    initial = self.row("HideLeadMarker", Profile.COMPACT)
    self.assertEqual((initial.value, initial.source), ("Off", None))
    request = row_change(initial)
    assert request is not None
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.parked = True
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(source.read_bytes(), b"0")
    self.assertTrue(onroad_appearance(self.params).show_lead_indicator)
    self.assertFalse(self.owner.apply(request), "stale source must not overwrite")
    hide = row_change(self.row("HideLeadMarker", Profile.COMPACT))
    assert hide is not None
    self.assertTrue(self.owner.apply(hide))
    self.assertEqual(source.read_bytes(), b"1")
    self.assertFalse(onroad_appearance(self.params).show_lead_indicator)
    source.write_bytes(b"bad")
    invalid = self.row("HideLeadMarker", Profile.COMPACT)
    self.assertEqual((invalid.value, invalid.repair_value), ("Invalid saved choice", "Off"))
    repair = row_change(invalid)
    assert repair is not None
    self.assertTrue(self.owner.apply(repair))
    self.assertEqual(source.read_bytes(), b"1")

  def test_manager_materializes_lead_hidden_default_without_replacing_saved_choice(self):
    from openpilot.system.manager import manager

    class AfterDefaults(Exception):
      pass

    def initialize_defaults():
      with patch.object(manager, "Params", return_value=self.params), \
           patch.object(manager, "prepare_manager_start"), patch.object(manager, "starpilot_storage_root", return_value=self.storage), \
           patch.object(manager, "save_bootlog"), \
           patch.object(manager, "get_build_metadata", return_value=NS(release_channel=False)), \
           patch.object(manager.Paths, "shm_path", side_effect=AfterDefaults):
        with self.assertRaises(AfterDefaults):
          manager.manager_init()

    source = Path(self.params.get_param_path("HideLeadMarker"))
    self.assertIsNone(read_visibility(self.params, "HideLeadMarker").raw)
    self.assertIs(self.params.get_default_value("HideLeadMarker"), True)
    initialize_defaults()
    self.assertEqual(source.read_bytes(), b"1")
    self.assertFalse(onroad_appearance(self.params).show_lead_indicator)
    source.write_bytes(b"0")
    initialize_defaults()
    self.assertEqual(source.read_bytes(), b"0")
    self.assertTrue(onroad_appearance(self.params).show_lead_indicator)

  def test_stock_confidence_rail_requires_fresh_source_and_road_camera(self):
    from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
    view = onroad.OnroadView.__new__(onroad.OnroadView)
    view.personality = Mock()
    view.navigation = Mock()
    view.background_layer = None
    view.projection_viewport = None
    class Fonts(BitmapFonts):
      def __init__(self):
        self.profile = Profile.COMPACT
    view.fonts = Fonts()
    view.camera_layer = None
    view.pip_layer = None
    view.extra_overlays = None
    view.alert = Mock()
    view.compact_hud = Mock()
    view.compact_sidebar = Mock()
    view.torque_bar = Mock()
    view.stock_confidence_layer = Mock()
    view.stock_confidence_reset = Mock()
    view._fade = None
    base = OnroadState(engaged=True, camera_available=False, speed_mps=12, cruise_kph=60,
                       speed_limit=SpeedLimitObservation(),
                       appearance=OnroadAppearance(show_stock_confidence_ball=True),
                       stock_confidence_source_fresh=True,
                       stock_confidence_source_stamp_ns=1_000_000_000,
                       stock_confidence_drive_frame=3)
    with patch.object(onroad.clip, "begin_scissor_mode"), patch.object(onroad.clip, "end_scissor_mode"), \
         patch.object(onroad.rl, "draw_rectangle_rec"), patch.object(onroad.rl, "draw_rectangle_gradient_v"), \
         patch.object(onroad.rl, "draw_rectangle"), patch.object(onroad.rl, "draw_texture_ex"), \
         patch.object(onroad.rl, "draw_rectangle_rounded_lines_ex"), patch.object(onroad, "render_compact_half_borders"), \
         patch.object(view, "_prepare_compact_fade"), patch.object(view, "_slc_actions"):
      view.render(base)
      view.stock_confidence_layer.assert_called_once()
      view.stock_confidence_reset.assert_called_once()
      view.compact_sidebar.render.assert_not_called()
      view.stock_confidence_layer.reset_mock()
      view.render(replace(base, stock_confidence_source_stamp_ns=1_050_000_000))
      view.stock_confidence_reset.assert_called_once()
      view.render(replace(base, stock_confidence_source_stamp_ns=1_301_000_000))
      self.assertEqual(view.stock_confidence_reset.call_count, 2)
      for state in (replace(base, stock_confidence_source_fresh=False),
                    replace(base, appearance=OnroadAppearance()),
                    replace(base, appearance=OnroadAppearance(show_stock_confidence_ball=True,
                                                               camera_view=CameraViewChoice.NONE))):
        view.render(state)
      self.assertEqual(view.compact_sidebar.render.call_count, 3)
      self.assertEqual(view.stock_confidence_reset.call_count, 2)
      view.render(replace(base, stock_confidence_source_stamp_ns=1_351_000_000))
      self.assertEqual(view.stock_confidence_reset.call_count, 3)
      view.render(replace(base, stock_confidence_source_stamp_ns=1_401_000_000,
                          stock_confidence_drive_frame=4))
      self.assertEqual(view.stock_confidence_reset.call_count, 4)
      view.stock_confidence_layer.reset_mock()
      for state in (replace(base, appearance=OnroadAppearance(show_stock_confidence_ball=True,
                                                               camera_view=CameraViewChoice.DRIVER)),
                    replace(base, reverse_driver_camera=True)):
        view.render(state)
      self.assertEqual(view.compact_sidebar.render.call_count, 3)
      view.stock_confidence_layer.assert_not_called()

  def test_native_stock_confidence_reset_clears_previous_drive_filter(self):
    from openpilot.common.filter_simple import FirstOrderFilter
    from openpilot.selfdrive.ui.mici.onroad.augmented_road_view import AugmentedRoadView
    from openpilot.selfdrive.ui.mici.onroad.confidence_ball import ConfidenceBall

    ball = ConfidenceBall.__new__(ConfidenceBall)
    ball._confidence_filter = FirstOrderFilter(0.9, 0.5, 1 / 20)
    view = Mock()
    view._confidence_ball = ball
    AugmentedRoadView.reset_stock_confidence_layer(view)
    self.assertEqual(ball._confidence_filter.x, -0.5)
    ball.update_filter(0.5)
    self.assertGreater(ball._confidence_filter.x, -0.5)

  def test_native_stock_confidence_aol_context_and_cleanup(self):
    from openpilot.common.filter_simple import FirstOrderFilter
    from openpilot.selfdrive.ui.mici.onroad import confidence_ball as native_ball
    from openpilot.selfdrive.ui.mici.onroad.augmented_road_view import AugmentedRoadView
    from openpilot.selfdrive.ui.ui_state import UIStatus
    from openpilot.starpilot.ui.runtime_app import render_stock_confidence

    ball = native_ball.ConfidenceBall.__new__(native_ball.ConfidenceBall)
    ball._demo = False
    ball._confidence_filter = FirstOrderFilter(-0.5, 0.5, 1 / 20)
    ball._render_lateral_active = False
    rect = rl.Rectangle(0, 0, 536, 240)
    ball._rect = rect
    model = NS(meta=NS(disengagePredictions=NS(brakeDisengageProbs=[0.1], steerOverrideProbs=[0.1])))
    native_state = NS(status=UIStatus.DISENGAGED, sm={"modelV2": model})
    colors = []
    def render(_rect):
      ball._update_state()
      ball._render(None)
    with patch.object(native_ball, "ui_state", native_state), patch.object(ball, "render", side_effect=render), \
         patch.object(native_ball, "draw_circle_gradient", side_effect=lambda *args: colors.append(args[-2:])):
      ball.render_with_lateral(rect, False)
      self.assertEqual(ball._confidence_filter.x, -0.5)
      self.assertEqual(colors[-1][0].r, 50)
      self.assertFalse(ball._render_lateral_active)
      camera = Mock()
      camera.render_stock_confidence_layer.side_effect = lambda _rect, *, lateral_active: ball.render_with_lateral(_rect, lateral_active)
      state = OnroadState(engaged=True, camera_available=True, speed_mps=12, cruise_kph=60,
                          speed_limit=SpeedLimitObservation(), lateral_active=True,
                          stock_confidence_source_fresh=True, stock_confidence_source_stamp_ns=1,
                          stock_confidence_drive_frame=1)
      render_stock_confidence(camera, rect, state)
      camera.render_stock_confidence_layer.assert_called_once_with(rect, lateral_active=True)
      self.assertGreater(ball._confidence_filter.x, -0.5)
      self.assertEqual(colors[-1][0].r, 255)
      self.assertFalse(ball._render_lateral_active)
      native_state.status = UIStatus.OVERRIDE
      ball.render_with_lateral(rect, True)
      self.assertEqual((colors[-1][0].r, colors[-1][0].g, colors[-1][0].b), (255, 255, 255))
      self.assertFalse(ball._render_lateral_active)
    with patch.object(ball, "render", side_effect=RuntimeError("render failed")):
      with self.assertRaises(RuntimeError):
        ball.render_with_lateral(rect, True)
      self.assertFalse(ball._render_lateral_active)
    native_view = Mock()
    native_view._confidence_ball = Mock()
    AugmentedRoadView.render_stock_confidence_layer(native_view, rect, lateral_active=True)
    native_view._confidence_ball.render_with_lateral.assert_called_once_with(rect, True)

  def test_edit_repair_and_stale_source_guards(self):
    request = row_change(self.row("HidePersonalityAlerts"))
    assert request is not None
    self.assertEqual(request.value, "On")
    self.assertTrue(self.owner.apply(request))
    self.assertTrue(onroad_appearance(self.params).hide_personality_alerts)
    self.assertFalse(self.owner.apply(request))
    corrupt_path = Path(self.params.get_param_path("SignalMetrics"))
    corrupt_path.write_bytes(b"\xff")
    row = self.row("SignalMetrics")
    self.assertEqual(row.value, "Invalid saved choice")
    self.assertEqual(corrupt_path.read_bytes(), b"\xff")
    repair = row_change(row)
    assert repair is not None
    self.assertEqual(repair.value, "Off")
    corrupt_path.write_bytes(b"2")
    self.assertFalse(self.owner.apply(repair))
    self.assertEqual(corrupt_path.read_bytes(), b"2")
    repaired = row_change(self.row("SignalMetrics"))
    assert repaired is not None
    self.assertTrue(self.owner.apply(repaired))
    self.assertEqual(corrupt_path.read_bytes(), b"0")

  def test_parked_final_read_and_oversized_source_guards(self):
    request = row_change(self.row("BlindSpotMetrics"))
    assert request is not None
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.assertIsNone(read_visibility(self.params, "BlindSpotMetrics").raw)

    self.parked = True
    path = Path(self.params.get_param_path("BlindSpotMetrics"))
    checks = 0
    def parked_once():
      nonlocal checks
      checks += 1
      return checks == 1
    owner = AppearanceOwner(self.params, parked_once)
    self.assertFalse(owner.apply(request))
    self.assertIsNone(read_visibility(self.params, "BlindSpotMetrics").raw)
    original = b"1" * 100_000
    path.write_bytes(original)
    row = self.row("BlindSpotMetrics")
    self.assertFalse(row.available)
    self.assertIsNone(row_change(row))
    path.write_bytes(original[:-1] + b"0")
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(row.key, row.source, "Off")))
    self.assertEqual(path.read_bytes(), original[:-1] + b"0")
    path.unlink()
    original_path = self.params.get_param_path
    reads = 0
    def get_path(key):
      nonlocal reads
      reads += 1
      if reads == 2:
        raise OSError("source unreadable at final guard")
      return original_path(key)
    with patch.object(self.params, "get_param_path", side_effect=get_path):
      self.assertFalse(self.owner.apply(request))
    self.assertIsNone(read_visibility(self.params, "BlindSpotMetrics").raw)

  def test_nonregular_sources_are_unavailable_without_reading_or_repair(self):
    path = Path(self.params.get_param_path("SignalMetrics"))
    target = path.parent / "elsewhere"
    target.write_bytes(b"1")
    path.symlink_to(target)
    self.assertFalse(read_visibility(self.params, "SignalMetrics").readable)
    self.assertFalse(self.row("SignalMetrics").available)
    path.unlink()
    os.mkfifo(path)
    self.assertFalse(read_visibility(self.params, "SignalMetrics").readable)
    self.assertFalse(self.row("SignalMetrics").available)
    self.assertTrue(path.is_fifo())

  def test_large_tile_press_and_compact_visuals_refresh(self):
    emitted = []
    settings = SettingsState()
    x, y, width, height = tile_rects(settings)[4]
    touch = SettingsInput(Profile.LARGE, emitted.append)
    touch.press(x + width / 2, y + height / 2, settings)
    touch.release(x + width / 2, y + height / 2, settings)
    self.assertEqual(emitted[0].destination.destination, Destination.APPEARANCE)
    changed = []
    large = FeatureInput(changed.append)
    state = self.owner.snapshot(Profile.LARGE)
    large.press(1980, 300, state)
    large.move(1800, 300, state)
    large.release(1800, 300, state)
    self.assertEqual([(action.kind, action.direction) for action in changed], [('scroll', 1)])
    changed.clear()
    large.press(1980, 300, state)
    large.release(1980, 300, state)
    self.assertEqual(changed[0].row.key, "DeveloperSidebar")

    class Button:
      def __init__(self, label, value):
        self.label, self.value, self.click = label, value, None
      def set_click_callback(self, callback):
        self.click = callback
    class Scroller:
      def __init__(self):
        self._scroller = self
        self.items = []
      def add_widgets(self, cards):
        self.items.extend(cards)
    class Session:
      def __init__(inner):
        inner.feature_owner = FeatureSettingsOwner(self.params, lambda group: self.parked,
                                                   vehicle_fingerprint=lambda: "TEST CAR")
      def appearance_snapshot(inner):
        return self.owner.snapshot(Profile.COMPACT)
      def pip_snapshot(inner):
        return FeatureSettingsState()
      def appearance_request(inner, request):
        return self.owner.apply(request)
      def feature_snapshot(inner, page):
        return inner.feature_owner.snapshot(page, parked=self.parked, system_long=self.parked,
                                            lateral_context=False, metric=False)
      def feature_request(inner, request):
        return inner.feature_owner.apply(request)
    shown = []
    with patch.object(appearance_compact, "BigButton", Button), patch.object(appearance_compact, "GreyBigButton", Button), \
         patch.object(appearance_compact, "NavScroller", Scroller), patch.object(appearance_compact.gui_app, "push_widget", shown.append):
      appearance_compact.AppearanceCompact(Session()).entry_button().click()
      page = shown[0]
      self.assertEqual(page.items[0].label, "visuals")
      next(item for item in page.items if item.label == "hide personality alerts").click()
      self.assertTrue(read_visibility(self.params, "HidePersonalityAlerts").value)
      self.assertEqual(next(item for item in page.items if item.label == "hide personality alerts").value, "on")

  def test_runtime_snapshot_reads_saved_choice_and_large_session_routes_back(self):
    from openpilot.starpilot.ui.runtime_app import StarShellSession
    from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter
    from openpilot.starpilot.ui.feature_settings_state import FeatureUiAction
    ui = ui_fake()
    ui.params = self.params
    adapter = RuntimeSnapshotAdapter(ui)
    from openpilot.starpilot.ui import runtime_snapshot
    with patch.object(runtime_snapshot, "onroad_appearance", wraps=onroad_appearance) as reads:
      before = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad
      adapter.build(ShellMode.ONROAD, now_ns=NOW + 500_000_000)
      self.assertEqual(reads.call_count, 1)
      adapter.build(ShellMode.ONROAD, now_ns=NOW + 1_000_000_000)
      self.assertEqual(reads.call_count, 2)
      adapter.build(ShellMode.SETTINGS, now_ns=NOW + 1_000_000_001)
      adapter.build(ShellMode.ONROAD, now_ns=NOW + 1_000_000_002)
      self.assertEqual(reads.call_count, 3)
    self.assertEqual(before.appearance, OnroadAppearance())
    session = StarShellSession.__new__(StarShellSession)
    session._mode = ShellMode.SETTINGS
    session.selected = Destination.APPEARANCE
    session.appearance_owner = self.owner
    session.adapter = adapter
    session.appearance_scroll = 0
    session._snapshot_cache = None
    session.input = ShellInput(Profile.LARGE, lambda _: None)
    session.profile = Profile.LARGE
    session._unavailable = Mock()
    session._appearance_ui(FeatureUiAction("change", self.row("HidePersonalityAlerts")))
    self.assertTrue(adapter.build(ShellMode.ONROAD, now_ns=NOW + 1_000_000_003).onroad.appearance.hide_personality_alerts)
    session._appearance_ui(FeatureUiAction("back"))
    self.assertEqual(session.selected, Destination.STAR)

  def test_onroad_widget_visibility_and_wheel_request_cancel(self):
    base = OnroadState(engaged=True, camera_available=False, speed_mps=12, cruise_kph=60,
                       speed_limit=SpeedLimitObservation(), experimental_enabled=False,
                       experimental_available=True)
    fonts = Mock(spec=BitmapFonts, profile=Profile.LARGE)
    fonts.measure.return_value = NS(width=10, height=20)
    fonts.vertical_ink.side_effect = lambda text, role, size: (0., size * .7)
    fonts.horizontal_ink.return_value = (0., 10.)
    with patch("openpilot.starpilot.ui.onroad_large_widgets.draw_control_card") as card, patch.object(rl, "draw_line_ex"):
      UnifiedSpeedWidget(fonts).render(rl.Rectangle(30, 30, 1800, 1020), base)
      card.assert_not_called()
      from openpilot.starpilot.ui.tests.test_unified_speed_native import foreground_calls
      self.assertEqual(foreground_calls(fonts.draw)[0].args[3:5], (192, 90))
    for profile in (Profile.LARGE, Profile.COMPACT):
      view = onroad.OnroadView.__new__(onroad.OnroadView)
      view.personality = Mock()
      view.navigation = Mock()
      view.background_layer = None
      view.projection_viewport = None
      object.__setattr__(view, "fonts", type("Fonts", (), {"profile": profile})())
      view.navigation_favorites = onroad.NavigationFavorites(view.fonts)
      view.camera_layer = None
      view.extra_overlays = None
      view.alert = Mock()
      view.torque_bar = Mock()
      view.unified_speed = Mock()
      view.projection_viewport = None
      view.background_layer = None
      view._corner_cache = Mock()
      view.current_speed = Mock()
      view.steering_wheel = Mock()
      view.compact_hud = Mock()
      view.compact_sidebar = Mock()
      view._fade = None
      with patch.object(onroad.clip, "begin_scissor_mode"), patch.object(onroad.clip, "end_scissor_mode"), \
           patch.object(onroad.rl, "draw_rectangle_rec"), patch.object(onroad.rl, "draw_rectangle_gradient_v"), \
           patch.object(onroad.rl, "draw_rectangle_lines_ex"), patch.object(onroad.rl, "draw_rectangle"), \
           patch.object(onroad.rl, "draw_texture_ex"), patch.object(onroad.rl, "draw_rectangle_rounded_lines_ex"), \
           patch.object(onroad, "render_corner_hint"), patch.object(view, "_prepare_compact_fade"), \
           patch.object(view, "_slc_actions") as slc:
        view.render(base)
        view.torque_bar.render.assert_called_once()
        if profile == Profile.LARGE:
          view.unified_speed.render.assert_called_once()
          view.current_speed.render.assert_called_once()
          view.steering_wheel.render.assert_called_once()
        view.torque_bar.reset_mock()
        hidden = replace(base, customization=_hidden_widgets(), alert=OnroadAlert(AlertSize.NONE, "", "", True))
        view.render(hidden)
        view.torque_bar.render.assert_not_called()
        view.alert.render.assert_called()
        if profile == Profile.LARGE:
          self.assertEqual(view.unified_speed.render.call_count, 2)
          self.assertEqual(slc.call_count, 2)
          view.current_speed.render.assert_called_once()
          view.steering_wheel.render.assert_called_once()
        for alert_size in (AlertSize.SMALL, AlertSize.MID, AlertSize.FULL):
          view.alert.render.reset_mock()
          view.render(replace(hidden, alert=OnroadAlert(alert_size, "keep warning", "", True)))
          view.alert.render.assert_called_once()
    actions = []
    touch = OnroadInput(actions.append, Profile.LARGE)
    touch.press(1600, 100, base)
    touch.release(1600, 100, replace(base, customization=_hidden_widgets()))
    self.assertFalse(actions)
    touch.press(1600, 100, replace(base, customization=_hidden_widgets()))
    touch.release(1600, 100, base)
    self.assertFalse(actions)


if __name__ == "__main__":
  unittest.main()
