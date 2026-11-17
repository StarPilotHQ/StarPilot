"""Saved generic torque edits reach the actual production controller."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from opendbc.car.car_helpers import interfaces
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed
from openpilot.starpilot.lateral.torque_runtime import production_supported_cp, runtime_enabled
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, parse_document
from openpilot.starpilot.lateral.torque_tuning import TorqueSource
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest


@pytest.mark.parametrize('identity', ['HYUNDAI_SONATA_HYBRID', 'TOYOTA_COROLLA_TSS2', 'HYUNDAI_IONIQ_5', 'KIA_EV6', 'HONDA_FIT_4G'])
def test_generic_saved_editor_reaches_actual_controls(identity):
  cp = interfaces[identity].get_non_essential_params(identity)
  if identity == 'HYUNDAI_SONATA_HYBRID':
    cp.lateralTuning.torque.latAccelFactor = 2.899026393890381
    cp.lateralTuning.torque.latAccelOffset = 0.0
    cp.lateralTuning.torque.friction = 0.08998055011034012
  assert cp.lateralTuning.which() == 'torque'
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'TORQUE_REPLAY_RUNTIME': '0', 'AOL_REPLAY_RUNTIME': '0'}):
    params = Params()
    if identity == 'TOYOTA_COROLLA_TSS2':
      from openpilot.starpilot.lateral.tests.test_lane_runtime import publish_corolla_params
      cp = publish_corolla_params(params)
    params.put('CarParams', cp.to_bytes(), block=True)
    baseline = Controls()
    assert baseline.torque_host is None
    original = baseline.LaC.torque_params.to_dict()
    assert production_supported_cp(cp)
    owner = FeatureSettingsOwner(params, lambda _: True, vehicle_fingerprint=lambda: identity,
                                 vehicle_params=lambda: cp, galaxy=lambda: True)
    def rows():
      return {row.key: row for row in owner.snapshot('torque', parked=True, system_long=True,
              lateral_context=True, metric=False).rows}
    for field, value in [('factor', float(cp.lateralTuning.torque.latAccelFactor) * 1.1),
                         ('friction', float(cp.lateralTuning.torque.friction) + 0.01)]:
      row = rows()[f'torque:{field}:value']
      assert row.available
      assert owner.apply(FeatureSettingsRequest(row.key, row.source, str(value), vehicle_fingerprint=identity,
                         capability=row.capability, dependencies=row.dependencies))
    assert identity in parse_document(Path(params.get_param_path(DOCUMENT_KEY)).read_bytes())
    assert runtime_enabled(cp, params)
    actual = Controls()
    assert actual.torque_host is not None
    before = actual.CP.to_dict()
    for tick in range(120):
      now = 1_000_000_000 + tick * 10_000_000
      feed(actual, now, tick)
      with patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now + 1000):
        command, _ = actual.state_control()
      assert abs(command.actuators.torque) <= actual.LaC.steer_max + 1e-6
    assert actual.torque_host.selected.source == TorqueSource.USER
    assert actual.LaC.torque_params.latAccelFactor != original['latAccelFactor']
    assert actual.LaC.torque_params.friction != original['friction']
    assert actual.CP.to_dict() == before
    assert actual.CP.to_dict() == baseline.CP.to_dict()


@pytest.mark.parametrize('field,value', [('passive', True), ('notCar', True), ('dashcamOnly', True),
  ('brand', 'toyota'), ('carFingerprint', 'FAKE_PLATFORM'), ('steerControlType', 'angle')])
def test_generic_editor_rejects_non_driving_or_mismatched_capability(field, value):
  cp = interfaces['HYUNDAI_SONATA_HYBRID'].get_non_essential_params('HYUNDAI_SONATA_HYBRID')
  setattr(cp, field, value)
  assert not production_supported_cp(cp)


@pytest.mark.parametrize('field,value', [('latAccelFactor', 0), ('latAccelFactor', float('nan')),
  ('latAccelOffset', float('inf')), ('friction', -1), ('friction', float('nan'))])
def test_generic_editor_rejects_invalid_tune(field, value):
  cp = interfaces['HYUNDAI_SONATA_HYBRID'].get_non_essential_params('HYUNDAI_SONATA_HYBRID')
  setattr(cp.lateralTuning.torque, field, value)
  assert not production_supported_cp(cp)


def test_generic_editor_rejects_pid_controller():
  cp = interfaces['HYUNDAI_SONATA_HYBRID'].get_non_essential_params('HYUNDAI_SONATA_HYBRID')
  cp.lateralTuning.init('pid')
  assert not production_supported_cp(cp)


@pytest.mark.parametrize('enabled', [False, True])
def test_generic_partial_manual_tune_honors_scoped_learning_preference(enabled):
  from openpilot.starpilot.lateral.controller_selection import policy_for
  from openpilot.starpilot.lateral.learning_preferences import KEY, replace_preference
  from openpilot.starpilot.lateral.tests.test_torque_family import learner
  from openpilot.starpilot.lateral.torque_runtime import TorqueHost, read_settings
  cp = interfaces['HONDA_FIT_4G'].get_non_essential_params('HONDA_FIT_4G')
  assert cp.lateralTuning.which() == 'torque' and policy_for(cp) == 'generic'
  with OpenpilotPrefix():
    params = Params()
    params.put(KEY, replace_preference(None, cp, enabled), block=True)
    params.put('SteerFriction', float(cp.lateralTuning.torque.friction) + 0.01, block=True)
    host = TorqueHost(params, cp)
    assert host.allow_learning == enabled
    host.settings = read_settings(params, host.vehicle, allow_learning=host.allow_learning)
    selected = host._select(learner(1_000_000_000, host.vehicle), 1_000_000_000)
    expected = host.vehicle.lat_accel_factor * (1.1 if enabled else 1.0)
    assert selected.lat_accel_factor == pytest.approx(expected)
