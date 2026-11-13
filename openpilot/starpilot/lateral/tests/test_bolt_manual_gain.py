"""Manual gain changes preserve independent parameter ownership and PID history."""
import ast
import builtins
from pathlib import Path
from types import SimpleNamespace
import json
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.locationd.torqued import TorqueEstimator
from openpilot.starpilot.lateral.bolt_policy import BOLT_GENERATIONS
from openpilot.starpilot.lateral.controller_selection import ControllerMode, replace_mode
from openpilot.starpilot.lateral.gain_runtime import create_gain_owner, gain_basis
from openpilot.starpilot.lateral.torque_runtime import runtime_enabled
from openpilot.starpilot.lateral.torque_settings import (DOCUMENT_KEY, FieldChoice, PlatformProfile, parse_document,
                                                       replace_field, replace_gain, serialize_document)
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed
from openpilot.starpilot.longitudinal.tests.test_bolt_mode_transition import params
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest


class TestBoltManualGain(unittest.TestCase):
  def test_actual_startup_gain_only_does_not_take_torque_or_cache_ownership(self):
    for identity in BOLT_GENERATIONS:
      for mode in ControllerMode:
        with OpenpilotPrefix(), patch.dict(os.environ, {"SIMULATION": "1", "REPLAY": "1"}):
          cp = params(identity, alpha=True)
          saved = Params()
          saved.put("CarParams", cp.to_bytes(), block=True)
          saved.put("LateralControllerSelection", json.loads(replace_mode(None, cp, mode)), block=True)
          baseline = Controls()
          self.assertIsNone(baseline.lateral_gain_owner)
          basis = gain_basis(cp, baseline.LaC)
          saved.put_bool("AdvancedLateralTune", True, block=True)
          saved.put(DOCUMENT_KEY, json.loads(serialize_document(replace_gain({}, str(identity), basis, "custom", .7))), block=True)
          selected = Controls()
          assert selected.lateral_gain_owner is not None
          self.assertIsNone(selected.torque_host)
          self.assertFalse(runtime_enabled(cp, saved))
          self.assertEqual(selected.torque_learning_allowed, baseline.torque_learning_allowed)
          self.assertEqual(selected.LaC.pid._k_p, [[0.], [.7]])
          with patch("openpilot.selfdrive.locationd.torqued.get_cache", return_value=None) as cache:
            estimator = TorqueEstimator(cp, allow_learning=selected.torque_learning_allowed)
            self.assertEqual(cache.call_count, 2 if baseline.torque_learning_allowed else 0)
            self.assertFalse(estimator.use_params)

  def test_refresh_withdrawal_preserves_history_and_complete_source_table(self):
    for mode in ControllerMode:
      with OpenpilotPrefix(), patch.dict(os.environ, {"SIMULATION": "1", "REPLAY": "1"}):
        cp = params(next(iter(BOLT_GENERATIONS)), alpha=True)
        saved = Params()
        saved.put("CarParams", cp.to_bytes(), block=True)
        saved.put("LateralControllerSelection", json.loads(replace_mode(None, cp, mode)), block=True)
        controls = Controls()
        basis = gain_basis(cp, controls.LaC)
        document = replace_gain({}, str(cp.carFingerprint), basis, "custom", .3)
        saved.put_bool("AdvancedLateralTune", True, block=True)
        saved.put(DOCUMENT_KEY, json.loads(serialize_document(document)), block=True)
        owner = create_gain_owner(saved, cp, controls.LaC)
        controls.LaC.pid.i = .123
        owner.refresh(now_ns=1_000_000_000)
        for value in (.6, .9):
          document = replace_gain(document, str(cp.carFingerprint), basis, "custom", value)
          saved.put(DOCUMENT_KEY, json.loads(serialize_document(document)), block=True)
          owner.refresh(now_ns=int(value * 10_000_000_000))
          self.assertEqual(controls.LaC.pid._k_p, [[0.], [value]])
          self.assertEqual(controls.LaC.pid.i, .123)
        saved.put_bool("AdvancedLateralTune", False, block=True)
        owner.refresh(now_ns=10_000_000_000)
        self.assertEqual(tuple(tuple(row) for row in controls.LaC.pid._k_p), basis.source_table)
        self.assertEqual(controls.LaC.pid.i, .123)
        controls.LaC.pid._k_p[1][0] = 12345
        self.assertNotEqual(controls.LaC.pid._k_p[1][0], basis.source_table[1][0])

  def test_v1_upgrade_preserves_profiles_and_rejects_unbound_or_bad_gain(self):
    with OpenpilotPrefix(), patch.dict(os.environ, {"SIMULATION": "1", "REPLAY": "1"}):
      cp = params(next(iter(BOLT_GENERATIONS)), alpha=True)
      saved = Params()
      saved.put("CarParams", cp.to_bytes(), block=True)
      controls = Controls()
      basis = gain_basis(cp, controls.LaC)
      original = {str(identity): PlatformProfile(basis.torque_basis, FieldChoice(), FieldChoice("custom", .08))
                  for identity in BOLT_GENERATIONS}
      v1 = serialize_document(original)
      self.assertEqual(json.loads(v1)["schemaVersion"], 1)
      upgraded = replace_gain(parse_document(v1), str(cp.carFingerprint), basis, "custom", .7)
      roundtrip = parse_document(serialize_document(upgraded))
      for key in original:
        self.assertEqual(roundtrip[key].friction, original[key].friction)
        if key != str(cp.carFingerprint):
          self.assertEqual(roundtrip[key], original[key])
      changed = replace_field(roundtrip, str(cp.carFingerprint), basis.torque_basis, "friction", "custom", .09)
      self.assertEqual(changed[str(cp.carFingerprint)].gain_basis, basis)
      for value in (.29, .91, float("nan"), True):
        with self.assertRaises(ValueError):
          replace_gain(roundtrip, str(cp.carFingerprint), basis, "custom", value)
      bad = replace(original[str(cp.carFingerprint)], proportional_gain=FieldChoice("custom", .7))
      with self.assertRaises(ValueError):
        serialize_document({str(cp.carFingerprint): bad})

  def test_ui_controller_change_reviews_gain_only_and_allows_withdrawal(self):
    with OpenpilotPrefix(), patch.dict(os.environ, {"SIMULATION": "1", "REPLAY": "1"}):
      cp = params(next(iter(BOLT_GENERATIONS)), alpha=True)
      saved = Params()
      saved.put("CarParams", cp.to_bytes(), block=True)
      saved.put_bool("AdvancedLateralTune", True, block=True)
      controls = Controls()
      basis = gain_basis(cp, controls.LaC)
      profiles = replace_gain({}, str(cp.carFingerprint), basis, "custom", .7)
      profiles = replace_field(profiles, str(cp.carFingerprint), basis.torque_basis, "friction", "custom", .08)
      saved.put(DOCUMENT_KEY, json.loads(serialize_document(profiles)), block=True)
      owner = FeatureSettingsOwner(saved, lambda _: True, vehicle_fingerprint=lambda: cp.carFingerprint, vehicle_params=lambda: cp)
      before = owner.snapshot("torque", parked=True, system_long=True, lateral_context=True, metric=False)
      edit = next(row for row in before.rows if row.key == "torque:gain:value")
      omitted = FeatureSettingsRequest(edit.key, edit.source, "0.8", vehicle_fingerprint=cp.carFingerprint, capability=edit.capability)
      self.assertFalse(owner.apply(omitted))
      saved.put("LateralControllerSelection", json.loads(replace_mode(None, cp, ControllerMode.STANDARD)), block=True)
      request = FeatureSettingsRequest(edit.key, edit.source, "0.8",
                                       vehicle_fingerprint=cp.carFingerprint, capability=edit.capability, dependencies=edit.dependencies)
      self.assertFalse(owner.apply(request))
      rows = owner.snapshot("torque", parked=True, system_long=True, lateral_context=True, metric=False).rows
      self.assertTrue(any(row.key == "torque_gain_rebase" for row in rows))
      withdrawal = next(row for row in rows if row.key == "torque:gain:mode")
      request = FeatureSettingsRequest(withdrawal.key, withdrawal.source, "Selected controller",
                                       vehicle_fingerprint=cp.carFingerprint, capability=withdrawal.capability, dependencies=withdrawal.dependencies)
      self.assertTrue(owner.apply(request))
      raw = owner._raw(DOCUMENT_KEY)
      assert raw is not None
      updated = parse_document(raw)[str(cp.carFingerprint)]
      self.assertEqual(updated.proportional_gain.mode, "source")
      self.assertEqual(updated.friction, profiles[str(cp.carFingerprint)].friction)

  def test_actual_controls_ticks_change_gain_without_parameter_ownership(self):
    for mode in ControllerMode:
      with OpenpilotPrefix(), patch.dict(os.environ, {"SIMULATION": "1", "REPLAY": "1"}):
        cp = params(next(iter(BOLT_GENERATIONS)), alpha=True)
        saved = Params()
        saved.put("CarParams", cp.to_bytes(), block=True)
        saved.put("LateralControllerSelection", json.loads(replace_mode(None, cp, mode)), block=True)
        baseline = Controls()
        basis = gain_basis(cp, baseline.LaC)
        saved.put_bool("AdvancedLateralTune", True, block=True)
        saved.put(DOCUMENT_KEY, json.loads(serialize_document(replace_gain({}, str(cp.carFingerprint), basis, "custom", .3))), block=True)
        selected = Controls()
        deltas = []
        for tick in range(100):
          now = 1_000_000_000 + tick * 10_000_000
          feed(baseline, now, tick)
          feed(selected, now, tick)
          a, _ = baseline.state_control()
          b, _ = selected.state_control()
          deltas.append(abs(a.actuators.torque - b.actuators.torque))
        self.assertGreater(max(deltas), 1e-6)
        self.assertIsNone(selected.torque_host)
        self.assertEqual(selected.LaC.torque_params.to_dict(), baseline.LaC.torque_params.to_dict())

  def test_confirmation_handlers_keep_gain_review_intent(self):
    # Execute the real handler bodies with dialog rendering replaced; no display is opened.
    from openpilot.starpilot.ui import feature_settings_state as state
    from openpilot.starpilot.ui.feature_reset_question import feature_reset_question
    from openpilot.starpilot.galaxy.settings import _question
    row = state.FeatureRow("torque_gain_rebase", "Review steering response", "Changed controller", b"document",
                           capability=("vehicle",), dependencies=(("LateralControllerSelection", b"selection"),))
    intent = FeatureSettingsRequest(row.key, row.source, "confirm", confirmation=True,
                                    capability=row.capability, dependencies=row.dependencies)
    self.assertIn("selected controller", _question(row, intent))
    imports = builtins.__import__
    pushed, sent = [], []
    result = SimpleNamespace(CONFIRM=1)
    def importer(name, *args, **kwargs):
      if name == "openpilot.system.ui.widgets.confirm_dialog":
        return SimpleNamespace(ConfirmDialog=lambda question, button, callback: SimpleNamespace(question=question, callback=callback))
      if name == "openpilot.system.ui.widgets":
        return SimpleNamespace(DialogResult=result)
      return imports(name, *args, **kwargs)
    for path, method in (("ui/runtime_app.py", "_confirm_feature_reset"), ("ui/feature_settings_compact.py", "_confirm_reset")):
      file = Path(__file__).resolve().parents[2] / path
      tree = ast.parse(file.read_text())
      function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == method)
      function.returns = None
      for argument in function.args.args:
        argument.annotation = None
      namespace: dict = {"FeatureSettingsRequest": FeatureSettingsRequest, "FeatureRow": state.FeatureRow,
                   "LANE_CHANGE_RESET": "lane_change:reset", "CONDITIONAL_CONFIRM_ACTIONS": frozenset(),
                   "SETUP_ACTION": "torque_prepare_firestar", "SETUP_QUESTION": "Prepare",
                   "long_confirm_question": lambda _: "Other action", "feature_reset_question": feature_reset_question,
                   "gui_app": SimpleNamespace(push_widget=pushed.append, texture=lambda *args: None),
                   "BigConfirmationDialog": lambda question, texture, callback, red: SimpleNamespace(question=question, callback=callback)}
      exec(compile(ast.Module(body=[function], type_ignores=[]), str(file), "exec"), namespace)
      owner = SimpleNamespace(feature_request=sent.append, session=SimpleNamespace(feature_request=lambda request: sent.append(request) or True))
      with patch("builtins.__import__", side_effect=importer):
        if method == "_confirm_feature_reset":
          namespace[method](owner, row)
          pushed[-1].callback(result.CONFIRM)
        else:
          namespace[method](owner, row, lambda: None)
          pushed[-1].callback()
      self.assertEqual(sent[-1].key, row.key)
      self.assertTrue(sent[-1].confirmation)
      self.assertEqual(sent[-1].dependencies, row.dependencies)
      self.assertIn("selected controller", pushed[-1].question)
