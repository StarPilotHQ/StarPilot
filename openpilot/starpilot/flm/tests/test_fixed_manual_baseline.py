"""Manual scalar fields stay fixed while the separate automatic FF profile learns."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.flm.controller_replay import capture_snapshot
from openpilot.starpilot.flm.learner_factory import from_evidence
from openpilot.starpilot.flm.runtime_adapter_registry import read_source
from openpilot.starpilot.flm.tests.test_registered_controls_global import published_cp, frame
from openpilot.starpilot.flm.tests.test_registered_accepted_profile import computed_registered_fit, accepted_frame
from openpilot.starpilot.lateral.controller_selection import DOCUMENT_KEY as CONTROLLER_KEY, ControllerMode, replace_mode
from openpilot.starpilot.lateral.learning_preferences import KEY, replace_preference
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, FieldChoice, PlatformProfile, serialize_document


BASE = 400_000_000_000


def profile_for(cp, factor=True, friction=True):
  tune = cp.lateralTuning.torque
  basis = (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction))
  return PlatformProfile(basis, FieldChoice('custom', basis[0] * 1.04) if factor else FieldChoice(),
                         FieldChoice('custom', basis[2] * 1.10) if friction else FieldChoice())


@contextmanager
def fixed_controls(*, factor=True, friction=True, legacy=False, learning=True):
  cp = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
  with (OpenpilotPrefix(),
        mock.patch.dict(os.environ, {'REPLAY': '1', 'SIMULATION': '0', 'TORQUE_REPLAY_RUNTIME': '0',
                                     'AOL_REPLAY_RUNTIME': '0', 'LANE_CENTERING_REPLAY_RUNTIME': '0'}),
        mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster')):
    params = Params()
    params.put_bool('OpenpilotEnabledToggle', True, block=True)
    selected = published_cp(cp, params)
    params.put('CarParams', selected.to_bytes(), block=True)
    params.put(KEY, replace_preference(None, cp, learning), block=True)
    params.put(CONTROLLER_KEY, json.loads(replace_mode(None, cp, ControllerMode.STARPILOT)), block=True)
    profile = profile_for(cp, factor, friction)
    if legacy:
      if factor:
        params.put('SteerLatAccel', profile.factor.custom_value, block=True)
      if friction:
        params.put('SteerFriction', profile.friction.custom_value, block=True)
    else:
      params.put(DOCUMENT_KEY, json.loads(serialize_document({str(cp.carFingerprint): profile})), block=True)
    yield Controls(), profile


@pytest.mark.parametrize('factor,friction', ((True, False), (False, True), (True, True)))
@pytest.mark.parametrize('legacy', (False, True))
def test_manual_scalar_source_is_a_fixed_baseline_not_an_auto_profile_override(factor, friction, legacy):
  with fixed_controls(factor=factor, friction=friction, legacy=legacy) as (controls, profile):
    params = controls.params
    keys = (DOCUMENT_KEY, 'SteerLatAccel', 'SteerFriction', CONTROLLER_KEY)
    saved = {key: params.get(key) for key in keys}
    source = read_source(controls.CP, params, now_ns=BASE, calibration=(0., 0., 0.), vehicle_session='fixed-test')
    assert source.enabled and not source.manual and source.source_epoch
    for tick in range(100):
      frame(controls, BASE + tick * 10_000_000, tick)
    runtime = controls.global_learning
    assert runtime.eligible and runtime.descriptor.controller_mode == 'starpilot'
    assert runtime.descriptor.policy.startswith('ioniq6:')
    assert controls.LaC.controller_policy == 'ioniq6'
    assert {key: params.get(key) for key in keys} == saved
    if factor:
      assert controls.torque_host.selected.lat_accel_factor == profile.factor.custom_value
    if friction:
      assert controls.torque_host.selected.friction == profile.friction.custom_value
    # The independent old scalar learner remains disabled for the custom policy.
    assert not controls.torque_learning_allowed


def test_automatic_profile_changes_output_without_writing_manual_fields_and_expires():
  with fixed_controls() as (baseline, _), fixed_controls() as (candidate, profile):
    for tick in range(105):
      now = BASE + tick * 10_000_000
      frame(baseline, now, tick)
      frame(candidate, now, tick)
    runtime = candidate.global_learning
    assert runtime.eligible and runtime.identity
    saved = candidate.params.get(DOCUMENT_KEY)
    fixed_torque = candidate.LaC.torque_params.to_dict()
    fixed_gain = [list(row) for row in candidate.LaC.pid._k_p]
    fixed_cp = candidate.CP.as_builder().to_bytes()
    assert candidate.torque_host.selected.lat_accel_factor == profile.factor.custom_value
    assert candidate.torque_host.selected.friction == profile.friction.custom_value
    result, validation = computed_registered_fit(candidate.CP, runtime, BASE + 1_050_000_000, mode='starpilot',
                                                 baseline_snapshot=capture_snapshot(candidate.LaC, candidate.VM))
    changed = False
    for tick in range(105, 156):
      now = BASE + tick * 10_000_000
      reference, _ = frame(baseline, now, tick)
      actual, _ = accepted_frame(candidate, now, tick, result, validation)
      assert runtime.reason == 'qualified' and runtime.used
      assert runtime.outcome.status == 'applied'
      changed |= actual.actuators.torque != reference.actuators.torque
      assert candidate.LaC.torque_params.to_dict() == fixed_torque
      assert candidate.LaC.pid._k_p == fixed_gain
      assert candidate.CP.as_builder().to_bytes() == fixed_cp
      assert candidate.params.get(DOCUMENT_KEY) == saved
      assert actual.latActive == reference.latActive and actual.longActive == reference.longActive
    assert changed
    assert now == result.profile.expires_ns
    last_actual_stage = runtime.stage
    runtime.prepare(candidate.sm, now_ns=now + 1, active=True, healthy=True, controller=candidate.LaC,
                    native_required=bool(candidate.aol_replay or candidate.ordinary_axis_ack_required),
                    native_witness=candidate.flm_native_witness)
    assert runtime.torque_residual(last_actual_stage, stage_revision=runtime.descriptor.stage_revision) == (0.0, 0.0)
    assert runtime.applied_coordinates == (0.0, 0.0)
    assert candidate.LaC.torque_params.to_dict() == fixed_torque
    # Continue the real 100 Hz caller; duplicating a CarState timestamp is a
    # separate ordinary TorqueHost clock reset and must not be hidden here.
    accepted_frame(candidate, now + 10_000_000, 156, result, validation)
    assert runtime.applied_coordinates == (0.0, 0.0)
    assert candidate.LaC.torque_params.to_dict() == fixed_torque
    assert candidate.params.get(DOCUMENT_KEY) == saved


def test_actual_controller_checkpoint_reconstructs_the_fixed_custom_baseline():
  with fixed_controls() as (controls, _):
    runtime = controls.global_learning
    for tick in range(151):
      now = BASE + tick * 10_000_000
      frame(controls, now, tick)
      if tick > 100 and runtime.snapshot and runtime.eligible:
        evidence = runtime.capture_evidence(control_ns=now + 1, state_ns=int(controls.sm.logMonoTime['selfdriveState']),
                                            active=True, limited=False)
        assert evidence is not None and evidence.replay_snapshot
        source = read_source(controls.CP, controls.params, now_ns=now, calibration=(0., 0., 0.), vehicle_session='fixture-drive')
        adapter = from_evidence(controls.CP, evidence, source=source)
        assert adapter is not None
        assert adapter.baseline_revision == runtime.baseline_revision
        assert adapter.identity == runtime.identity
        assert adapter.prepare_control(evidence) is evidence
        return
    raise pytest.fail.Exception('No source-bound fixed manual baseline checkpoint reached the real learner factory')


@pytest.mark.parametrize('raw', (b'nan', b'99999', b'not-a-number'))
def test_corrupt_legacy_scalar_settings_still_refuse_learning(raw):
  with fixed_controls(legacy=True) as (controls, _):
    Path(controls.params.get_param_path('SteerLatAccel')).write_bytes(raw)
    source = read_source(controls.CP, controls.params, now_ns=BASE, calibration=(0., 0., 0.), vehicle_session='fixed-test')
    assert not source.enabled and not source.source_epoch


def test_editing_a_fixed_manual_value_changes_the_source_identity():
  with fixed_controls() as (controls, profile):
    before = read_source(controls.CP, controls.params, now_ns=BASE, calibration=(0., 0., 0.), vehicle_session='fixed-test')
    updated = replace(profile, factor=FieldChoice('custom', profile.factor.custom_value * 1.01))
    controls.params.put(DOCUMENT_KEY, json.loads(serialize_document({str(controls.CP.carFingerprint): updated})), block=True)
    after = read_source(controls.CP, controls.params, now_ns=BASE + 1, calibration=(0., 0., 0.), vehicle_session='fixed-test')
    assert before.enabled and after.enabled and not before.manual and not after.manual
    assert before.source_epoch != after.source_epoch


def test_explicit_learning_off_still_blocks_the_custom_controller():
  with fixed_controls(learning=False) as (controls, _):
    for tick in range(30):
      frame(controls, BASE + tick * 10_000_000, tick)
    assert controls.global_learning is None
    assert controls.LaC.controller_policy == 'ioniq6'
    source = read_source(controls.CP, controls.params, now_ns=BASE + 300_000_000,
                         calibration=(0., 0., 0.), vehicle_session='fixed-test')
    assert not source.enabled
    assert controls.torque_host.selected.lat_accel_factor == profile_for(controls.CP).factor.custom_value
    assert controls.torque_host.selected.friction == profile_for(controls.CP).friction.custom_value


@pytest.mark.parametrize('location', ('controller', 'policy'))
def test_explicit_manual_surface_still_blocks_automatic_profiles(location):
  with fixed_controls() as (controls, _):
    for tick in range(100):
      now = BASE + tick * 10_000_000
      frame(controls, now, tick)
    runtime = controls.global_learning
    assert runtime.eligible
    target = controls.LaC if location == 'controller' else controls.LaC.starpilot_extension.policy
    attribute = 'flm_surface' if location == 'controller' else 'surface'
    with mock.patch.object(target, attribute, SimpleNamespace(), create=True):
      runtime.prepare(controls.sm, now_ns=now + 1, active=True, healthy=True, controller=controls.LaC,
                      native_required=False)
      assert runtime.live_manual and not runtime.eligible
      assert runtime.applied_coordinates == (0.0, 0.0)
