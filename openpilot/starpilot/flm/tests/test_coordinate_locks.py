"""Manual choices remain fixed while other coordinates retain automatic ownership."""
from dataclasses import replace
from contextlib import contextmanager
import json
from unittest.mock import patch

import pytest

from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, CanonicalSurfaceEffects
from openpilot.starpilot.flm.manual_coordinates import ManualCoordinates, selected_manual
from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES, RouteSurfaceEffects, revision_for_manual, revision_locks
from openpilot.starpilot.flm.tests.test_manual_coordinates import cp as cp, manual, PrivateParams, save, feature, request, saved_profile
from openpilot.starpilot.flm.tests.test_configured_route_recording import control, wire
from openpilot.starpilot.flm.learning_wire import read_control
from openpilot.starpilot.flm.offroad_control_metadata import read_control_metadata
from openpilot.starpilot.flm.torque_stages import StageFrame
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, parse_document, serialize_document
from openpilot.starpilot.saved_source import read_saved


def configure_controls_manual(controls, *, automatic=None, **changes):
  from openpilot.starpilot.flm.manual_coordinates import context
  from openpilot.selfdrive.controls.controlsd import Controls

  cp = controls.CP.as_builder()
  cp.carVin = 'KMHM34AA0NA000001'  # Declared unit vehicle, never an identity supplied to a real recording.
  controls.params.put('CarParams', cp.to_bytes(), block=True)
  values = dict.fromkeys(NAMES if automatic is None else ROUTE_NAMES, 0.) | changes
  selected = ManualCoordinates(context(cp, controls.lateral_controller_selection.mode, recovered=automatic is not None), tuple(values.items()),
                               automatic=automatic).validate()
  raw, readable = read_saved(controls.params, DOCUMENT_KEY, 16384)
  assert readable and raw is not None
  profiles = parse_document(raw)
  fingerprint = str(controls.CP.carFingerprint)
  profiles[fingerprint] = replace(profiles[fingerprint], manual_coordinates=selected)
  controls.params.put(DOCUMENT_KEY, json.loads(serialize_document(profiles)), block=True)
  return Controls(), selected


@contextmanager
def lock_drive(controls):
  from openpilot.starpilot.flm.tests import test_registered_controls_global as packets
  from openpilot.starpilot.flm.tests import test_live_trial_global_runtime as live
  from openpilot.starpilot.flm.runtime_adapter_registry import read_source

  def source_packet(controls, now, tick, calibration, result=None, validation=None):
    source = read_source(controls.CP, controls.params, now_ns=now, calibration=calibration,
                         calibration_generation=live.GENERATION, vehicle_session='declared-lock-fixture')
    event = packets.result_message(result, 'declared-lock-fixture', tick + 1, now, replay_validation=validation)
    packets.write_source_receipt(event, source)
    return packets.messaging.log_from_bytes(event.to_bytes())
  with patch.object(packets, 'source_packet', source_packet), patch.object(live, 'source_packet', source_packet):
    yield live.LiveControls(controls)


@pytest.mark.parametrize('name', NAMES)
def test_old_documents_lock_only_nondefault_values_without_rewriting_them(tmp_path, cp, name):
  params = PrivateParams(tmp_path)
  original = save(params, cp, manual(cp, **{name: .01}))
  restored = selected_manual(cp, params)
  assert restored.locked == (name,)
  assert restored.document() == ManualCoordinates.from_document(restored.document()).document()
  assert (tmp_path / 'd' / 'TorqueOverrideDocument').read_bytes() == original
  assert revision_locks(revision_for_manual(restored)) == ((name,), True)


def test_edit_unchanged_save_restart_and_individual_default_preserve_other_locks(tmp_path, cp):
  params = PrivateParams(tmp_path)
  save(params, cp, manual(cp, center_taper_max=.01, unwind_taper_right=.02))
  state = {'parked': True}
  editor, capability = feature(cp, params, state)
  def row(name):
    return replace(next(r for r in editor.manual_rows(capability, True) if r.key.endswith(':' + name)), vehicle_fingerprint=str(cp.carFingerprint))
  assert editor.apply(request(row('center_taper_max'), 1.))
  assert selected_manual(cp, params).locked == ('unwind_taper_right', 'center_taper_max')
  assert editor.apply(request(row('center_taper_max'), 0.))
  assert selected_manual(cp, params).locked == ('unwind_taper_right',)
  assert dict(saved_profile(params, cp).manual_coordinates.values)['unwind_taper_right'] == .02
  restarted, restarted_capability = feature(cp, params, state)
  assert next(r for r in restarted.manual_rows(restarted_capability, True) if r.key.endswith(':unwind_taper_right')).value == '2.0000'


def test_editing_an_accepted_finding_locks_only_that_value_and_default_releases_it(tmp_path, cp):
  from openpilot.starpilot.flm.manual_coordinates import context
  from openpilot.starpilot.galaxy.setting_descriptions import purpose
  from openpilot.starpilot.galaxy.settings import _projection

  params = PrivateParams(tmp_path)
  values = dict.fromkeys(ROUTE_NAMES, 0.) | {'ff_gain_left': .005, 'center_taper_max': .01, 'friction_threshold_mid': .003}
  selected = ManualCoordinates(context(cp, 'starpilot', recovered=True), tuple(values.items()),
                               automatic=('ff_gain_left', 'friction_threshold_mid')).validate()
  save(params, cp, selected)
  editor, capability = feature(cp, params, {'parked': True})
  def row(name):
    return replace(next(r for r in editor.manual_rows(capability, True) if r.key.endswith(':' + name)), vehicle_fingerprint=str(cp.carFingerprint))
  assert all(purpose(r) for r in editor.manual_rows(capability, True))
  assert _projection(row('ff_gain_left'))['acceptedFindingValue'] == .005
  assert _projection(row('ff_gain_left'))['coordinateLocked'] is False
  assert _projection(row('ff_gain_left'))['learningEligible'] is True
  assert editor.apply(request(row('ff_gain_left'), .5))
  assert 'acceptedFindingValue' not in _projection(row('ff_gain_left'))
  assert _projection(row('ff_gain_left'))['coordinateLocked'] is True
  assert _projection(row('ff_gain_left'))['learningEligible'] is False
  restored = selected_manual(cp, params)
  assert restored.locked == ('ff_gain_left', 'center_taper_max')
  assert restored.automatic == ('friction_threshold_mid',)
  assert editor.apply(request(row('ff_gain_left'), 0.))
  restored = selected_manual(cp, params)
  assert restored.locked == ('center_taper_max',)
  assert restored.automatic == ('friction_threshold_mid',)
  assert dict(restored.values)['friction_threshold_mid'] == .003
  assert _projection(row('ff_gain_left'))['coordinateLocked'] is False
  assert _projection(row('ff_gain_left'))['learningEligible'] is True
  assert _projection(row('friction_threshold_mid'))['acceptedFindingValue'] == .003
  assert ManualCoordinates.from_document(restored.document()) == restored


@pytest.mark.parametrize('parked', (False, True))
def test_readonly_coordinate_ownership_distinguishes_acceptance_lock_and_context(tmp_path, cp, parked):
  from openpilot.starpilot.flm.manual_coordinates import context
  from openpilot.starpilot.galaxy.settings import _projection

  params = PrivateParams(tmp_path)
  values = dict.fromkeys(ROUTE_NAMES, 0.) | {'unwind_taper_left': .005, 'unwind_taper_right': .01, 'friction_threshold_mid': .003}
  saved = ManualCoordinates(context(cp, 'starpilot', recovered=True), tuple(values.items()),
                            automatic=('unwind_taper_left', 'friction_threshold_mid')).validate()
  original = save(params, cp, saved)
  editor, capability = feature(cp, params, {'parked': parked})
  rows = {r.key.removeprefix('torque:surface:'): _projection(r) for r in editor.manual_rows(capability, parked)}
  for name, value in (('unwind_taper_left', .005), ('friction_threshold_mid', .003)):
    assert rows[name]['acceptedFindingValue'] == value
    assert rows[name]['coordinateLocked'] is False and rows[name]['learningEligible'] is True
    assert 'Change confirmed' in rows[name]['reason']
  assert rows['unwind_taper_left']['available'] is parked
  assert rows['unwind_taper_right']['coordinateLocked'] is True and rows['unwind_taper_right']['learningEligible'] is False
  assert 'acceptedFindingValue' not in rows['unwind_taper_right']
  assert rows['turn_in_boost_left']['coordinateLocked'] is False and rows['turn_in_boost_left']['learningEligible'] is True
  cp.mass += 1
  rows = {r.key.removeprefix('torque:surface:'): _projection(r) for r in editor.manual_rows(capability, parked)}
  assert rows['unwind_taper_left']['learningEligible'] is True
  cp.lateralTuning.torque.friction += .01
  editor, capability = feature(cp, params, {'parked': parked})
  rows = {r.key.removeprefix('torque:surface:'): _projection(r) for r in editor.manual_rows(capability, parked)}
  assert rows['unwind_taper_left']['learningEligible'] is False
  assert 'Eligible for Auto Learn' not in rows['unwind_taper_left']['reason']
  assert read_saved(params, DOCUMENT_KEY, 16384) == (original, True)


@pytest.mark.parametrize('name', NAMES)
@pytest.mark.parametrize('setpoint,jerk,speed', [(0., 0., 15.), (.5, 1., 5.), (-1., 1., 25.)])
def test_locked_manual_math_matches_existing_controller_math_when_no_auto_values_are_present(cp, name, setpoint, jerk, speed):
  selected = manual(cp, **{name: .01})
  baseline = 'a' * 64
  before = CanonicalSurfaceEffects({NAMES[0]: dict(selected.values)[NAMES[0]]}, baseline, accumulated=dict(selected.values))
  values = dict.fromkeys(ROUTE_NAMES, 0.) | dict(selected.values)
  after = RouteSurfaceEffects({NAMES[0]: values[NAMES[0]]}, baseline, accumulated=values, revision=revision_for_manual(selected))
  frame = StageFrame(speed, setpoint, jerk, .4, .3, True, False, False)
  for source in (before, after):
    source.begin_frame(1)
  expected = before.selected_stage(frame, original_anchor=before.original_effect_anchor)
  assert after.selected_stage(frame, original_anchor=after.original_effect_anchor) == expected


def test_an_unlocked_automatic_coordinate_changes_output_without_changing_the_manual_value(cp):
  selected = manual(cp, center_taper_max=.01)
  original = dict.fromkeys(ROUTE_NAMES, 0.) | dict(selected.values)
  candidate = dict(original, ff_gain_left=.005)
  sources = [RouteSurfaceEffects({NAMES[0]: v[NAMES[0]]}, 'a' * 64, accumulated=v, revision=revision_for_manual(selected)) for v in (original, candidate)]
  frame = StageFrame(20., .3, .1, .4, .3, True, False, False)
  results = []
  for source in sources:
    source.begin_frame(1)
    results.append(source.selected_stage(frame, original_anchor=source.original_effect_anchor))
    assert source.values['center_taper_max'] == .01
  assert results[0] != results[1]
  assert selected.locked == ('center_taper_max',)


@pytest.mark.parametrize('inhibited', [False, True])
def test_original_wire_retains_locked_identity_and_does_not_invent_an_application_on_a_limited_tick(cp, inhibited):
  selected = manual(cp, center_taper_max=.01)
  revision = revision_for_manual(selected)
  original = control(inhibited=inhibited)
  configured = dict(original.route_configured_coordinates, center_taper_max=.01)
  actual = dict.fromkeys(ROUTE_NAMES, 0.) if inhibited else configured
  original = replace(original, stage_schema_revision='torque-selected-stage-snapshot-v1@' + revision + '+configured-v1',
    route_effect_revision=revision, route_configured_coordinates=tuple(configured.items()), route_applied_coordinates=tuple(actual.items()))
  packet = wire(original)
  for read in (read_control, read_control_metadata):
    restored = read(packet)
    assert restored.route_effect_revision == revision
    assert restored.route_applied_coordinates == original.route_applied_coordinates
    assert restored.route_configured_coordinates == original.route_configured_coordinates


@pytest.mark.parametrize('revision', ['route-coordinate-locks-v1-c-000000000', 'route-coordinate-locks-v1-c-800000000',
                                    'route-coordinate-locks-v1-c-100000000', 'route-coordinate-locks-v1-x-000000001'])
def test_invalid_or_out_of_manifest_locks_refuse(revision):
  with pytest.raises(ValueError):
    revision_locks(revision)


@pytest.mark.parametrize('accepted', [False, True])
def test_actual_controls_keep_collecting_and_reconstruct_user_selected_values(accepted):
  from openpilot.starpilot.flm.tests.test_fixed_manual_baseline import fixed_controls
  from openpilot.starpilot.flm.learner_factory import _frame
  from openpilot.starpilot.flm.controller_replay import ControllerReplay, SelectedControllerFactory, snapshot_from_values
  from openpilot.starpilot.flm.torque_adapter import Objective

  with fixed_controls(factor=False, friction=False) as (initial, _):
    controls, selected = configure_controls_manual(initial, center_taper_max=.01, automatic=('center_taper_max',) if accepted else None)
    raw = read_saved(controls.params, DOCUMENT_KEY, 16384)
    assert controls.LaC.manual_flm_effects.profile == selected
    assert controls.LaC.route_flm_effects is not None, controls.route_flm_status
    captured = None
    with lock_drive(controls) as drive:
      for tick in range(151):
        now, _ = drive.frame()
        runtime = controls.global_learning
        if tick > 100 and runtime.eligible and runtime.snapshot:
          captured = runtime.capture_evidence(control_ns=now + 1, state_ns=int(controls.sm.logMonoTime['selfdriveState']), active=True, limited=False)
          if captured is not None:
            break
    assert captured is not None, controls.global_learning.reason
    assert not captured.manual and dict(captured.route_applied_coordinates)['center_taper_max'] == .01
    assert revision_locks(captured.route_effect_revision)[0] == (() if accepted else ('center_taper_max',))
    factory = SelectedControllerFactory(controls.CP, captured.controller_mode, turn_assist=captured.turn_assist)
    replay = ControllerReplay(factory, Objective('feedforward', 'high', 'steady'), identity=captured.identity,
                              baseline_revision=captured.baseline_revision, model_revision='declared-unit-reconstruction')
    original_frame = _frame(captured)
    replay.checkpoint(original_frame.control_ns, snapshot_from_values(captured.replay_snapshot))
    replay.raw_mode = True
    reconstructed = replay.normalize(original_frame, _verify_only=True)
    assert reconstructed is not None and reconstructed.verified
    assert read_saved(controls.params, DOCUMENT_KEY, 16384) == raw


def test_actual_runtime_applies_an_unlocked_adjustment_and_quality_rollback_keeps_the_manual_value():
  from openpilot.starpilot.flm.tests.test_fixed_manual_baseline import fixed_controls
  from openpilot.starpilot.flm.offroad_runtime import RouteEffects
  from openpilot.starpilot.flm.offroad_surface import stop_marker

  # Declared saved-row values exercise runtime application, not a claim of road qualification.
  with fixed_controls(factor=False, friction=False) as (initial, _):
    controls, selected = configure_controls_manual(initial, center_taper_max=.01)
    original = controls.LaC.route_flm_effects
    assert original is not None, controls.route_flm_status
    target = dict(original.values, ff_gain_left=.005)
    owner = RouteEffects(original.scope, tuple(target.items()), original.numeric, previous=original.values,
                         fixed_numeric=original.fixed_numeric, revision=revision_for_manual(selected))
    controls.LaC.route_flm_effects = owner
    with lock_drive(controls) as drive:
      for _ in range(151):
        drive.frame()
      assert controls.global_learning.eligible
      assert owner.calls and owner.frame_allowed
      assert owner.source.values['center_taper_max'] == .01 and owner.source.values['ff_gain_left'] == .005
      owner.observe_current_status(stop_marker(owner.scope.key(), target, 'gross_tracking_or_oscillation_worse'))
      assert owner.quality_stopped
      assert dict(owner.values)['center_taper_max'] == .01
      assert dict(owner.values)['ff_gain_left'] == 0.
      for _ in range(5):
        drive.frame()
    assert controls.global_learning.eligible and owner.frame_allowed
    assert owner.source.values['center_taper_max'] == .01
