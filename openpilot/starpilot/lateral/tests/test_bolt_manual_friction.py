"""Typed friction ownership is independent of Bolt controller selection."""
import os
import json
import unittest
from unittest.mock import patch

from pathlib import Path

from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.locationd.torqued import TorqueEstimator
from openpilot.starpilot.lateral.bolt_policy import BOLT_GENERATIONS
from openpilot.starpilot.lateral.controller_selection import ControllerMode, replace_mode
from openpilot.starpilot.lateral.torque_runtime import runtime_enabled, manual_overrides_present
from openpilot.starpilot.lateral.torque_settings import (DOCUMENT_KEY, FieldChoice, PlatformProfile, bounds,
                                                       replace_field, resolve_document, serialize_document)
from openpilot.starpilot.lateral.tests.test_torque_runtime import LearnedFrame
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.longitudinal.tests.test_bolt_mode_transition import params


def profile(cp, *, factor=False):
  tune = cp.lateralTuning.torque
  basis = (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction))
  value = min(bounds(basis, 'friction')[1], basis[2] + .04)
  entry = PlatformProfile(basis, FieldChoice('custom', basis[0]) if factor else FieldChoice(), FieldChoice('custom', value))
  return basis, value, serialize_document({str(cp.carFingerprint): entry})


class TestBoltManualFriction(unittest.TestCase):
  def test_explicit_document_admits_actual_controls_without_retired_master(self):
    for master in (None, False):
      with self.subTest(master=master), OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'TORQUE_REPLAY_RUNTIME': '0'}):
        cp = params(next(iter(BOLT_GENERATIONS)), alpha=True)
        saved = Params()
        saved.put('CarParams', cp.to_bytes(), block=True)
        if master is not None:
          saved.put_bool('AdvancedLateralTune', master, block=True)
        assert Controls().torque_host is None
        saved.put(DOCUMENT_KEY, json.loads(profile(cp)[2]), block=True)
        assert Controls().torque_host is not None
        basis = profile(cp)[0]
        saved.put(DOCUMENT_KEY, json.loads(serialize_document({str(cp.carFingerprint):
                  PlatformProfile(basis, FieldChoice(), FieldChoice())})), block=True)
        assert Controls().torque_host is None
        Path(saved.get_param_path(DOCUMENT_KEY)).write_bytes(b'{invalid')
        assert Controls().torque_host is None

  def test_actual_controls_ff_and_invalid_car_gate(self):
    for mode in ControllerMode:
      with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1'}):
        cp = params(next(iter(BOLT_GENERATIONS)), alpha=True)
        settings = Params()
        settings.put('CarParams', cp.to_bytes(), block=True)
        settings.put('LateralControllerSelection', json.loads(replace_mode(None, cp, mode)), block=True)
        baseline = Controls()
        settings.put_bool('AdvancedLateralTune', True, block=True)
        settings.put(DOCUMENT_KEY, json.loads(profile(cp)[2]), block=True)
        selected = Controls()
        differences = []
        for tick in range(240):
          now = 1_000_000_000 + tick * 10_000_000
          feed(baseline, now, tick)
          feed(selected, now, tick)
          reference, _ = baseline.state_control()
          command, _ = selected.state_control()
          differences.append(abs(reference.actuators.torque - command.actuators.torque))
        self.assertGreater(max(differences), 1e-6)
        feed(selected, now + 10_000_000, 240, can_valid=False)
        command, _ = selected.state_control()
        self.assertFalse(command.latActive)
        self.assertEqual(command.actuators.torque, 0.)
        self.assertEqual(selected.torque_host.selected, selected.torque_host.vehicle)

  def test_estimator_default_cache_path_and_optin_fresh_session_without_gm_learning(self):
    with OpenpilotPrefix():
      cp = params(next(iter(BOLT_GENERATIONS)), alpha=True)
      settings = Params()
      with patch('openpilot.selfdrive.locationd.torqued.get_cache', return_value=None) as cache:
        baseline = TorqueEstimator(cp, allow_learning=True)
        self.assertEqual(cache.call_count, 2)
        self.assertFalse(baseline.use_params)
      settings.put_bool('AdvancedLateralTune', True, block=True)
      settings.put(DOCUMENT_KEY, json.loads(profile(cp)[2]), block=True)
      with patch.dict(os.environ, {'TORQUE_REPLAY_RUNTIME': '0'}):
        self.assertTrue(manual_overrides_present(cp, settings))
      with patch('openpilot.selfdrive.locationd.torqued.get_cache', return_value=None) as cache:
        opted = TorqueEstimator(cp, allow_learning=True)
        self.assertEqual(cache.call_count, 0)
        self.assertFalse(opted.use_params)

  def test_ui_factor_refusal_review_and_other_profile_preservation(self):
    with OpenpilotPrefix():
      cp = params(next(iter(BOLT_GENERATIONS)), alpha=True)
      settings = Params()
      settings.put_bool('AdvancedLateralTune', True, block=True)
      _, _, raw = profile(cp)
      document = json.loads(raw)
      other = next(identity for identity in BOLT_GENERATIONS if identity != cp.carFingerprint)
      other_cp = params(other, alpha=True)
      document['vehicles'].update(json.loads(profile(other_cp)[2])['vehicles'])
      settings.put(DOCUMENT_KEY, document, block=True)
      owner = FeatureSettingsOwner(settings, lambda _: True, vehicle_fingerprint=lambda: cp.carFingerprint,
                                   vehicle_params=lambda: cp)
      state = owner.snapshot('torque/developer', parked=True, system_long=True, lateral_context=True, metric=False)
      self.assertFalse(any(row.key.startswith('torque:factor') for row in state.rows))
      original = owner._raw(DOCUMENT_KEY)
      request = FeatureSettingsRequest('torque:factor:mode', original, 'Custom', vehicle_fingerprint=cp.carFingerprint,
                                       capability=owner._capability('torque'))
      self.assertFalse(owner.apply(request))
      self.assertEqual(owner._raw(DOCUMENT_KEY), original)
      document['vehicles'][str(cp.carFingerprint)]['factor'] = {'mode': 'custom', 'customValue': profile(cp)[0][0]}
      settings.put(DOCUMENT_KEY, document, block=True)
      state = owner.snapshot('torque/developer', parked=True, system_long=True, lateral_context=True, metric=False)
      reset = next(row for row in state.rows if row.key == 'torque_reset_profile')
      request = FeatureSettingsRequest(reset.key, reset.source, '', confirmation=True,
                                       vehicle_fingerprint=cp.carFingerprint, capability=reset.capability, dependencies=reset.dependencies)
      self.assertTrue(owner.apply(request))
      self.assertEqual(settings.get(DOCUMENT_KEY)['vehicles'][str(other)], document['vehicles'][str(other)])
      malformed = b'{"schemaVersion":1,"vehicles":{"unknown":{}}}'
      Path(settings.get_param_path(DOCUMENT_KEY)).write_bytes(malformed)
      self.assertFalse(runtime_enabled(cp, settings))
      self.assertEqual(owner._raw(DOCUMENT_KEY), malformed)
      invalid = owner.snapshot('torque/developer', parked=True, system_long=True, lateral_context=True, metric=False)
      self.assertTrue(any(row.key == 'torque_reset' for row in invalid.rows))
      self.assertEqual(owner._raw(DOCUMENT_KEY), malformed)

  def test_actual_controls_startup_exact_modes_and_no_profile_invariance(self):
    for identity in BOLT_GENERATIONS:
      for mode in ControllerMode:
        for configured in (False, True):
          with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'TORQUE_REPLAY_RUNTIME': '0'}):
            cp = params(identity, alpha=True)
            settings = Params()
            settings.put('CarParams', cp.to_bytes(), block=True)
            settings.put('LateralControllerSelection', json.loads(replace_mode(None, cp, mode)), block=True)
            settings.put_bool('AdvancedLateralTune', configured, block=True)
            if configured:
              settings.put(DOCUMENT_KEY, json.loads(profile(cp)[2]), block=True)
            controls = Controls()
            self.assertEqual(controls.LaC.controller_mode, mode)
            self.assertEqual(controls.torque_host is not None, configured)
            # GM retains no native scalar estimator in either selected mode.
            self.assertFalse(controls.torque_learning_allowed)

  def test_host_friction_ff_fixed_factor_offset_and_live_precedence(self):
    for identity in BOLT_GENERATIONS:
      for mode in ControllerMode:
        with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1'}):
          cp = params(identity, alpha=True)
          basis, friction, raw = profile(cp)
          settings = Params()
          settings.put('CarParams', cp.to_bytes(), block=True)
          settings.put('LateralControllerSelection', json.loads(replace_mode(None, cp, mode)), block=True)
          settings.put_bool('AdvancedLateralTune', True, block=True)
          settings.put(DOCUMENT_KEY, json.loads(raw), block=True)
          controls = Controls()
          host = controls.torque_host
          now = 1_000_000_000
          learned = LearnedFrame(now, factor=basis[0] * 1.05, offset=.02, friction=basis[2])
          for tick in range(300):
            stamp = now + tick * 10_000_000
            learned.logMonoTime['lateralTorqueParameters'] = stamp
            host.apply(controls.LaC, host.sample(learned, now_ns=stamp, lat_active=True))
          self.assertAlmostEqual(controls.LaC.torque_params.friction, friction, places=6)
          self.assertAlmostEqual(controls.LaC.torque_params.latAccelOffset, basis[1], places=6)
          self.assertFalse(host.allow_learning)
          self.assertEqual(host.selected.source.value, 'user')
          if mode == ControllerMode.STARPILOT:
            self.assertEqual(controls.LaC.torque_from_lateral_accel(1., controls.LaC.torque_params),
                             controls.LaC.torque_from_lateral_accel(1., cp.lateralTuning.torque))
          # Removing the preference after opt-in follows the existing latched host contract.
          settings.put_bool('AdvancedLateralTune', False, block=True)
          settings.put(DOCUMENT_KEY, json.loads(serialize_document({str(cp.carFingerprint):
                       PlatformProfile(basis, FieldChoice(), FieldChoice())})), block=True)
          assert host is not None
          host.last_refresh_ns = None
          for tick in range(300, 600):
            stamp = now + tick * 10_000_000
            learned.logMonoTime['lateralTorqueParameters'] = stamp
            host.apply(controls.LaC, host.sample(learned, now_ns=stamp, lat_active=True))
          self.assertAlmostEqual(controls.LaC.torque_params.friction, basis[2], places=6)
          self.assertEqual(host.selected.source.value, 'vehicle')

  def test_unscoped_legacy_and_custom_factor_need_review(self):
    with OpenpilotPrefix():
      cp = params(next(iter(BOLT_GENERATIONS)), alpha=True)
      settings = Params()
      settings.put_bool('AdvancedLateralTune', True, block=True)
      settings.put('SteerFriction', .2, block=True)
      self.assertFalse(runtime_enabled(cp, settings))
      basis, _, raw = profile(cp, factor=True)
      settings.put(DOCUMENT_KEY, json.loads(raw), block=True)
      self.assertFalse(runtime_enabled(cp, settings))
      from openpilot.starpilot.lateral.torque_settings import parse_document
      self.assertTrue(resolve_document(parse_document(raw), str(cp.carFingerprint), basis)[2])
      with self.assertRaises(ValueError):
        replace_field({}, str(cp.carFingerprint), basis, 'factor', 'custom', basis[0])
