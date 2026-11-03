"""SLC native offset actions and both unit paths against real temporary Params."""

from pathlib import Path
from ctypes import c_float
from dataclasses import replace
from types import SimpleNamespace as NS
from typing import Any, cast
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.speed_limits import offset_document as od
from openpilot.starpilot.speed_limits.runtime_settings import read_params
from openpilot.starpilot.speed_limits.speed_domain import offset_for_limit
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest, row_change
from openpilot.starpilot.ui.slc_offset_feature import DOCUMENT_KEY, SlcOffsetOwner, native_parked


class SlcOffsetFeatureTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.parked = True
    self.cp: NS | None = NS(carFingerprint="TOYOTA_COROLLA_TSS2", openpilotLongitudinalControl=True, pcmCruise=False,
                 notCar=False, dashcamOnly=False, passive=False)
    self.owner = FeatureSettingsOwner(self.params, lambda _group: self.parked,
                                      vehicle_fingerprint=lambda: self.cp.carFingerprint if self.cp else None,
                                      vehicle_params=lambda: self.cp)
    self.units = SlcOffsetOwner(self.params, lambda: self.parked, lambda: self.cp)

  def raw(self, key):
    path = Path(self.params.get_param_path(key))
    return path.read_bytes() if path.exists() else None

  def row(self, key):
    state = self.owner.snapshot("slc", parked=self.parked, system_long=True, lateral_context=True, metric=False)
    return next(item for item in state.rows if item.key == key)

  @staticmethod
  def confirm(row):
    return FeatureSettingsRequest(row.key, row.source, "confirm", confirmation=True,
                                  related_source=row.related_source, vehicle_fingerprint=row.vehicle_fingerprint,
                                  capability=row.capability, dependencies=row.dependencies)

  def test_native_offroad_uses_retained_fresh_authority_adapter(self):
    ui = NS()
    with patch("openpilot.starpilot.ui.runtime_snapshot.RuntimeSnapshotAdapter") as factory:
      factory.return_value.confirmed_offroad.side_effect = [True, False]
      self.assertTrue(native_parked(ui))
      self.assertFalse(native_parked(ui))
      factory.assert_called_once_with(ui)

  def test_pcm_cruise_longitudinal_can_edit_and_enable_slc_without_adoption(self):
    assert self.cp is not None
    self.cp.carFingerprint = "TOYOTA_HIGHLANDER_TSS2"
    self.cp.pcmCruise = True
    offset = self.row("slc:offset:1")
    self.assertTrue(offset.available)
    edit = row_change(offset)
    assert edit is not None
    self.assertTrue(self.owner.apply(edit))
    row = self.row("SpeedLimitController")
    self.assertTrue(row.available)
    request = row_change(row)
    assert request is not None
    self.assertTrue(self.owner.apply(request))
    self.cp.openpilotLongitudinalControl = False
    self.assertFalse(self.row("SpeedLimitController").available)

  def test_fresh_and_retired_offsets_start_at_editable_zero_without_adoption(self):
    for metric in (False, True):
      for retired in (None, b"5", b"bad"):
        with self.subTest(metric=metric, retired=retired):
          self.params.remove(DOCUMENT_KEY)
          self.params.put_bool("IsMetric", metric, block=True)
          Path(self.params.get_param_path("Offset1")).unlink(missing_ok=True)
          if retired is not None:
            Path(self.params.get_param_path("Offset1")).write_bytes(retired)
          state = self.owner.snapshot("slc", parked=True, system_long=True, lateral_context=True, metric=metric)
          offsets = [row for row in state.rows if row.key.startswith("slc:offset:")]
          self.assertEqual(len(offsets), 7)
          self.assertTrue(all(row.available and row.value == "0" for row in offsets))
          self.assertFalse(any(row.key == "slc_adopt" for row in state.rows))
          self.assertTrue(self.owner.slc_offsets.ready_to_enable())
          self.assertTrue(all(band.offset_mps == 0 for band in read_params(self.params).offsets.bands))
          self.assertFalse(self.owner.apply(FeatureSettingsRequest("slc_adopt", None, "confirm", confirmation=True)))
          self.assertFalse(self.owner.apply(FeatureSettingsRequest("Offset1", None, "3")))
          edit = row_change(offsets[0])
          assert edit is not None
          self.assertTrue(self.owner.apply(replace(edit, value="3", direction=0)))
          document = od.decode(self.raw(DOCUMENT_KEY))
          self.assertEqual(document.bounds_mps, od.default_document(metric).bounds_mps)
          factor = od.KPH_TO_MPS if metric else od.MPH_TO_MPS
          self.assertAlmostEqual(document.offsets_mps[0], 3 * factor)
          self.assertEqual(read_params(self.params).offsets, document.schedule())
          self.assertEqual(self.raw("Offset1"), retired)

  def test_invalid_current_schedule_or_units_require_repair(self):
    for key, raw in ((DOCUMENT_KEY, b"{bad"), (DOCUMENT_KEY, od.encode(od.NEEDS_REVIEW)), ("IsMetric", b"bad")):
      with self.subTest(key=key, raw=raw):
        for saved in (DOCUMENT_KEY, "IsMetric"):
          Path(self.params.get_param_path(saved)).unlink(missing_ok=True)
        Path(self.params.get_param_path(key)).write_bytes(raw)
        row = self.row("SpeedLimitController")
        self.assertFalse(row.available)
        self.assertIn("Repair invalid", row.reason)
        self.assertFalse(self.owner.slc_offsets.ready_to_enable())
        self.assertFalse(self.row("slc:offset:1").available)

  def test_unavailable_control_explains_vehicle_and_context_gate(self):
    assert self.cp is not None
    cp = self.cp
    self.cp = None
    self.assertIn("Select or connect", self.row("SpeedLimitController").reason)
    self.cp = cp
    cp.openpilotLongitudinalControl = False
    self.assertIn("StarPilot speed control is unavailable", self.row("SpeedLimitController").reason)
    cp.openpilotLongitudinalControl = True
    self.parked = False
    self.assertIn("settings context is unavailable", self.row("SpeedLimitController").reason)

  def test_range_labels_follow_saved_si_bounds_without_rewriting_them(self):
    document = od.OffsetDocument(od.IMPERIAL_BOUNDS, tuple(index * od.MPH_TO_MPS for index in range(1, 8)))
    self.params.put(DOCUMENT_KEY, od.to_value(document), block=True)
    original = self.raw(DOCUMENT_KEY)
    for metric, unit, factor, cutoffs in (
        (False, "mph", od.MPH_TO_MPS, (0, 26, 35, 44, 54, 64, 75, 99)),
        (True, "km/h", od.KPH_TO_MPS, (0, 41, 55, 71, 87, 103, 120, 160))):
      if metric:
        self.assertTrue(self.units.change_units(True, None))
      schedule = read_params(self.params).offsets
      self.assertEqual(schedule, document.schedule())
      for index, (lower, next_lower) in enumerate(zip(cutoffs[:-1], cutoffs[1:], strict=True)):
        self.assertEqual(self.row(f"slc:offset:{index + 1}").label, f"{lower}–{next_lower - 1} {unit} offset")
        for speed in range(max(lower, 1), next_lower):
          self.assertEqual(offset_for_limit(c_float(speed * factor).value, schedule), document.offsets_mps[index])
      state = self.owner.snapshot("slc", parked=True, system_long=True, lateral_context=True, metric=metric)
      self.assertTrue(any(row.label == f"{cutoffs[-1]} {unit} and above" and row.value == "Zero offset" for row in state.rows))
      self.assertEqual(offset_for_limit(c_float(cutoffs[-1] * factor).value, schedule), 0.)
      self.assertEqual(self.raw(DOCUMENT_KEY), original)

  def test_range_labels_handle_a_wire_boundary_and_bands_without_whole_limits(self):
    bounds = tuple(limit * od.MPH_TO_MPS for limit in (0., 25., 25.1, 26.1, 40., 50., 60., 70.))
    document = od.OffsetDocument(bounds, (0.,) * 7)
    self.params.put(DOCUMENT_KEY, od.to_value(document), block=True)
    self.assertEqual(self.row("slc:offset:1").label, "0–25 mph offset")
    self.assertEqual(self.row("slc:offset:2").label, "Band 2 offset (no whole-number limits)")
    self.assertEqual(self.row("slc:offset:3").label, "26 mph offset")

  def test_editor_rejects_offsets_that_cannot_produce_a_positive_target_in_the_band(self):
    for metric, offset in ((False, -30), (True, -45)):
      with self.subTest(metric=metric):
        self.params.put_bool("IsMetric", metric, block=True)
        document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (0.0,) * 7)
        self.params.put(DOCUMENT_KEY, od.to_value(document), block=True)
        original = self.raw(DOCUMENT_KEY)
        row = self.row("slc:offset:1")
        request = row_change(row)
        assert request is not None
        self.assertGreater(row.minimum, offset)
        self.assertFalse(self.owner.apply(replace(request, value=str(offset), direction=0)))
        self.assertEqual(self.raw(DOCUMENT_KEY), original)
        # Useful reductions remain supported, including in the first band.
        self.assertTrue(self.owner.apply(replace(request, value="-1", direction=0)))

  def test_stepper_can_repair_an_unsafe_saved_offset_and_clamps_at_the_band_minimum(self):
    document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (-30 * od.MPH_TO_MPS, 0, 0, 0, 0, 0, 0))
    self.params.put(DOCUMENT_KEY, od.to_value(document), block=True)
    row = self.row('slc:offset:1')
    request = row_change(row)
    assert request is not None
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)).offsets_mps[0], row.minimum * od.MPH_TO_MPS)
    # Unit conversion can leave a fractional value less than one step above the minimum.
    document = od.OffsetDocument(od.IMPERIAL_BOUNDS, ((row.minimum + .5) * od.MPH_TO_MPS, 0, 0, 0, 0, 0, 0))
    self.params.put(DOCUMENT_KEY, od.to_value(document), block=True)
    request = row_change(self.row('slc:offset:1'), -1)
    assert request is not None
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)).offsets_mps[0], row.minimum * od.MPH_TO_MPS)

  def test_onroad_adopted_offsets_edit_without_repair_authority(self):
    self.params.put_bool("IsMetric", False, block=True)
    document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (0.0,) * 7)
    self.params.put(DOCUMENT_KEY, od.to_value(document), block=True)
    owner = FeatureSettingsOwner(self.params, lambda group: group != "parked_preferences",
                                 vehicle_fingerprint=lambda: self.cp.carFingerprint if self.cp else None,
                                 vehicle_params=lambda: self.cp)
    state = owner.snapshot("slc", parked=False, system_long=True, lateral_context=True, metric=False,
                           configure_while_driving=True)
    offset = next(row for row in state.rows if row.key == "slc:offset:2")
    reset = next(row for row in state.rows if row.key == "slc_reset")
    self.assertTrue(offset.available)
    self.assertFalse(reset.available)
    request = row_change(offset)
    assert request is not None
    self.assertTrue(owner.apply(request))
    self.assertFalse(owner.apply(self.confirm(reset)))
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)).offsets_mps[1], od.MPH_TO_MPS)
    self.params.put_bool("IsMetric", True, block=True)
    self.assertFalse(owner.apply(request))

  def test_global_units_do_not_require_longitudinal_a_fingerprint_or_migration(self):
    document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (5 * od.MPH_TO_MPS,) * 7)
    for cp in (None, NS(carFingerprint="STOCK_CRUISE", openpilotLongitudinalControl=False,
                       notCar=False, dashcamOnly=False, passive=False)):
      for saved in (None, document):
        with self.subTest(vehicle=cp, saved=saved):
          self.cp = cp
          self.parked = False
          self.params.remove(DOCUMENT_KEY)
          if saved is not None:
            self.params.put(DOCUMENT_KEY, od.to_value(saved), block=True)
          original = self.raw(DOCUMENT_KEY)
          self.params.put_bool("IsMetric", False, block=True)
          Path(self.params.get_param_path("Offset1")).write_bytes(b"5.0")
          self.assertTrue(self.units.change_units(True, b"0"))
          self.assertEqual(self.raw(DOCUMENT_KEY), original)
          self.assertEqual(self.raw("IsMetric"), b"1")
          self.assertEqual(self.raw("Offset1"), b"5.0")

  def test_first_edit_and_unit_changes_preserve_exact_physical_offsets(self):
    edit = row_change(self.row("slc:offset:1"))
    assert edit is not None
    self.assertTrue(self.owner.apply(edit))
    document = od.decode(self.raw(DOCUMENT_KEY))
    self.assertEqual(document.offsets_mps[0], od.MPH_TO_MPS)
    self.assertTrue(self.units.change_units(True, None))
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)), document)
    metric_edit = row_change(self.row("slc:offset:1"))
    assert metric_edit is not None
    self.assertTrue(self.owner.apply(metric_edit))
    adjusted = od.decode(self.raw(DOCUMENT_KEY))
    self.assertAlmostEqual(adjusted.offsets_mps[0], 3 * od.KPH_TO_MPS)
    self.assertTrue(self.units.change_units(False, b"1"))
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)), adjusted)

  def test_first_edit_rejects_a_stale_missing_schedule(self):
    edit = row_change(self.row("slc:offset:1"))
    assert edit is not None
    document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (5 * od.MPH_TO_MPS,) * 7)
    self.params.put(DOCUMENT_KEY, od.to_value(document), block=True)
    self.assertFalse(self.owner.apply(edit))
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)), document)

  def test_converted_offsets_use_whole_units_without_round_trip_drift(self):
    cases = ((False, 3, 5), (True, 5, 3), (True, 4, 2), (False, -3, -5), (True, -5, -3))
    for metric, original, converted in cases:
      for direction in (-1, 1):
        with self.subTest(metric=metric, original=original, direction=direction):
          factor = od.KPH_TO_MPS if metric else od.MPH_TO_MPS
          other_factor = od.MPH_TO_MPS if metric else od.KPH_TO_MPS
          document = od.OffsetDocument(od.default_document(metric).bounds_mps, (original * factor,) * 7)
          self.params.put(DOCUMENT_KEY, od.to_value(document), block=True)
          self.params.put_bool("IsMetric", metric, block=True)
          saved = self.raw(DOCUMENT_KEY)
          for _ in range(3):
            self.assertTrue(self.units.change_units(not metric, b"1" if metric else b"0"))
            self.assertEqual(self.row("slc:offset:1").value, str(converted))
            self.assertTrue(self.units.change_units(metric, b"0" if metric else b"1"))
            self.assertEqual(self.row("slc:offset:1").value, str(original))
            self.assertEqual(self.raw(DOCUMENT_KEY), saved)
          self.assertTrue(self.units.change_units(not metric, b"1" if metric else b"0"))
          edit = row_change(self.row("slc:offset:1"), direction)
          assert edit is not None
          self.assertTrue(self.owner.apply(edit))
          changed = od.decode(self.raw(DOCUMENT_KEY))
          self.assertAlmostEqual(changed.offsets_mps[0], (converted + direction) * other_factor)
          self.assertEqual(changed.offsets_mps[1:], document.offsets_mps[1:])
          edit = row_change(self.row("slc:offset:1"))
          assert edit is not None
          entry = replace(edit, direction=0, value="4.83")
          self.assertTrue(self.owner.apply(entry))
          self.assertAlmostEqual(od.decode(self.raw(DOCUMENT_KEY)).offsets_mps[0], 5 * other_factor)

  def test_failed_unit_write_preserves_current_schedule_and_units(self):
    document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (5 * od.MPH_TO_MPS,) * 7)
    self.params.put(DOCUMENT_KEY, od.to_value(document), block=True)
    with patch("openpilot.starpilot.saved_document.os.replace", side_effect=OSError("write failed")):
      self.assertFalse(self.units.change_units(True, None))
    self.assertIsNone(self.raw("IsMetric"))
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)), document)

  def test_retired_offset_corruption_does_not_block_unit_changes(self):
    Path(self.params.get_param_path("Offset1")).write_bytes(b"nan")
    Path(self.params.get_param_path("IsMetric")).write_bytes(b"bad")
    self.params.put_bool("ShowSpeedLimits", True, block=True)
    self.cp = None  # global display setting cannot require a supported car when SLC is off
    self.parked = False
    self.assertTrue(self.units.change_units(True, b"bad"))
    self.assertIsNone(self.raw(DOCUMENT_KEY))
    self.assertEqual(self.raw("Offset1"), b"nan")
    self.assertEqual(self.raw("IsMetric"), b"1")

  def test_stale_unit_capability_and_parked_edit_write_nothing(self):
    edit = row_change(self.row("slc:offset:2"))
    assert edit is not None
    self.params.put_bool("IsMetric", True, block=True)
    self.assertFalse(self.owner.apply(edit))
    self.params.put_bool("IsMetric", False, block=True)
    assert self.cp is not None
    self.cp.carFingerprint = "TOYOTA_CAMRY"
    self.assertFalse(self.owner.apply(edit))
    self.cp.carFingerprint = "TOYOTA_COROLLA_TSS2"
    self.parked = False
    self.assertFalse(self.owner.apply(edit))

  def test_valid_document_ignores_legacy_bytes_and_reset_keeps_bounds(self):
    self.params.put(DOCUMENT_KEY, od.to_value(od.default_document(False)), block=True)
    original = od.decode(self.raw(DOCUMENT_KEY))
    Path(self.params.get_param_path("Offset1")).write_bytes(b"bad")
    self.assertTrue(self.units.change_units(True, None))
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)), original)
    reset = self.row("slc_reset")
    self.assertTrue(self.owner.apply(self.confirm(reset)))
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)).bounds_mps, original.bounds_mps)

  def test_corrupt_document_and_units_require_explicit_repairs(self):
    self.params.put_bool("SpeedLimitController", True, block=True)
    Path(self.params.get_param_path(DOCUMENT_KEY)).write_bytes(b"{broken")
    Path(self.params.get_param_path("IsMetric")).write_bytes(b"broken")
    self.assertTrue(self.units.change_units(True, b"broken"))
    self.assertEqual(self.raw(DOCUMENT_KEY), b"{broken")
    self.assertFalse(read_params(self.params).enabled)
    reset = self.row("slc_reset")
    self.assertTrue(self.owner.apply(self.confirm(reset)))
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)).offsets_mps, (0.0,) * 7)
    self.assertTrue(read_params(self.params).enabled)

  def test_all_seven_edits_and_stale_unit_request(self):
    for index in range(1, 8):
      row = self.row(f"slc:offset:{index}")
      edit = row_change(row, -1)
      assert edit is not None
      self.assertTrue(self.owner.apply(edit))
    document = od.decode(self.raw(DOCUMENT_KEY))
    self.assertEqual(document.offsets_mps, (-od.MPH_TO_MPS,) * 7)
    self.assertEqual(document.schedule().bands[-1].offset_mps, 0.0)
    self.params.put_bool("IsMetric", True, block=True)
    self.assertFalse(self.units.change_units(False, None))
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)), document)

  def test_large_and_compact_metric_callbacks_use_same_owner_and_restore_veto(self):
    from openpilot.selfdrive.ui.layouts.settings.toggles import TogglesLayout
    from openpilot.selfdrive.ui.mici.layouts.settings import toggles as compact

    self.cp = None
    self.params.put(DOCUMENT_KEY, od.to_value(od.default_document(False)), block=True)
    large = TogglesLayout.__new__(TogglesLayout)
    large._params = self.params
    large._slc_offsets = self.units
    large._metric_source = None
    large._toggles = {"IsMetric": NS(action_item=NS(set_state=lambda value: setattr(large, "shown", value)))}
    large._toggle_defs = {"IsMetric": (lambda: "metric", "", "", False)}
    self.enterContext(patch.object(large, "_update_toggles", return_value=None))
    self.assertTrue(large._toggle_callback(True, "IsMetric"))
    self.assertTrue(large.shown)
    self.assertEqual(self.raw("IsMetric"), b"1")
    adopted = od.decode(self.raw(DOCUMENT_KEY))

    compact_layout = compact.TogglesLayoutMici.__new__(compact.TogglesLayoutMici)
    compact_layout._slc_offsets = self.units
    self.enterContext(patch.object(compact_layout, "_update_toggles", return_value=None))
    compact_layout._metric_source = b"0"  # stale displayed source
    cast(Any, compact_layout)._metric_toggle = NS(set_checked=lambda value: setattr(compact_layout, "shown", value))
    with patch.object(compact, "ui_state", NS(params=self.params)):
      compact_layout._on_metric(False)
      self.assertTrue(compact_layout.shown)
      self.assertEqual(self.raw("IsMetric"), b"1")
      compact_layout._metric_source = b"1"
      compact_layout._on_metric(False)
      self.assertFalse(compact_layout.shown)
    self.assertEqual(self.raw("IsMetric"), b"0")
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)), adopted)

  def test_both_native_reset_confirmations_recover_current_document_and_recheck_source(self):
    from openpilot.starpilot.ui import feature_settings_compact as compact
    from openpilot.starpilot.ui.runtime_app import StarShellSession
    from openpilot.system.ui.widgets import DialogResult

    class Dialog:
      def __init__(self, _question, *_args, callback=None, **_kwargs):
        self.confirmed = callback or _args[-1]

    source = Path(self.params.get_param_path(DOCUMENT_KEY))
    source.write_bytes(b"bad")
    large = StarShellSession.__new__(StarShellSession)
    cast(Any, large).feature_request = self.owner.apply
    pushed = []
    with patch("openpilot.system.ui.widgets.confirm_dialog.ConfirmDialog", Dialog), \
         patch("openpilot.starpilot.ui.runtime_app.gui_app.push_widget", pushed.append):
      large._confirm_feature_reset(self.row("slc_reset"))
      source.write_bytes(b"changed")
      pushed[-1].confirmed(DialogResult.CONFIRM)
      self.assertEqual(self.raw(DOCUMENT_KEY), b"changed")
      large._confirm_feature_reset(self.row("slc_reset"))
      pushed[-1].confirmed(DialogResult.CONFIRM)
    self.assertFalse(read_params(self.params).enabled)
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)).offsets_mps, (0.0,) * 7)
    Path(self.params.get_param_path(DOCUMENT_KEY)).unlink()

    self.params.put_bool("SpeedLimitController", True, block=True)
    source.write_bytes(b"bad")
    compact_adapter = compact.FeatureSettingsCompact(cast(Any, NS(feature_request=self.owner.apply)))
    pushed = []
    with patch.object(compact, "BigConfirmationDialog", Dialog), \
         patch.object(compact.gui_app, "push_widget", pushed.append), \
         patch.object(compact.gui_app, "texture", lambda *_args: None):
      compact_adapter._confirm_reset(self.row("slc_reset"), lambda: None)
      pushed[-1].confirmed()
    self.assertTrue(read_params(self.params).enabled)
    self.assertEqual(self.raw("SpeedLimitController"), b"1")
    self.assertEqual(od.decode(self.raw(DOCUMENT_KEY)).offsets_mps, (0.0,) * 7)

  def test_both_native_reset_dialogs_describe_saved_or_default_ranges(self):
    from openpilot.starpilot.ui import feature_settings_compact as compact
    from openpilot.starpilot.ui.runtime_app import StarShellSession

    class Dialog:
      def __init__(self, question, *_args, **_kwargs):
        self.question = question

    large = StarShellSession.__new__(StarShellSession)
    compact_adapter = compact.FeatureSettingsCompact(cast(Any, NS(feature_request=self.owner.apply)))
    pushed = []
    with patch("openpilot.system.ui.widgets.confirm_dialog.ConfirmDialog", Dialog), \
         patch.object(compact, "BigConfirmationDialog", Dialog), \
         patch("openpilot.starpilot.ui.runtime_app.gui_app.push_widget", pushed.append), \
         patch.object(compact.gui_app, "texture", lambda *_args: None):
      Path(self.params.get_param_path(DOCUMENT_KEY)).write_bytes(b"bad")
      recovery = self.row("slc_reset")
      self.assertEqual(recovery.value, "Use default mph speed ranges")
      large._confirm_feature_reset(recovery)
      compact_adapter._confirm_reset(recovery, lambda: None)
      self.assertTrue(all("default mph speed ranges" in dialog.question for dialog in pushed[-2:]))

      self.assertTrue(self.owner.apply(self.confirm(recovery)))
      saved = self.row("slc_reset")
      self.assertEqual(saved.value, "Keep saved speed ranges")
      large._confirm_feature_reset(saved)
      compact_adapter._confirm_reset(saved, lambda: None)
      self.assertTrue(all("Keep saved speed ranges" in dialog.question for dialog in pushed[-2:]))
