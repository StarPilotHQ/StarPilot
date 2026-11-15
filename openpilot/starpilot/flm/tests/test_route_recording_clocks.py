"""Actual controller hooks and immutable observed plots, not physical road qualification."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openpilot.cereal import messaging
from openpilot.starpilot.flm.evidence_hooks import control_message
from openpilot.starpilot.flm.offroad_control_metadata import read_control_metadata
from openpilot.starpilot.flm.offroad_runtime import RouteEffects
from openpilot.starpilot.flm.offroad_store import RouteScope
from openpilot.starpilot.flm.offroad_worker import ZERO
from openpilot.starpilot.flm.runtime_adapter_registry import baseline_values
from openpilot.starpilot.flm.tests.test_live_trial_global_runtime import live_controls


@contextmanager
def known_vehicle_controls():
  from opendbc.car.car_helpers import interfaces
  from opendbc.car.toyota.values import CAR
  from openpilot.starpilot.flm.learning_wire import result_message, write_source_receipt
  from openpilot.starpilot.flm.runtime_adapter_registry import read_source
  from openpilot.starpilot.flm.tests import test_live_trial_global_runtime as live

  factory = interfaces[CAR.TOYOTA_RAV4_TSS2]
  cp = factory.get_non_essential_params(CAR.TOYOTA_RAV4_TSS2)
  cp.carVin = 'JTMAB3FV0MD000001'  # Declared synthetic unit identity, never a real vehicle.

  def source_packet(controls, now, tick, calibration):
    source = read_source(controls.CP, controls.params, now_ns=now, calibration=calibration,
                         calibration_generation=live.GENERATION, vehicle_session='declared-route-fixture')
    event = result_message(None, 'declared-route-fixture', tick + 1, now)
    write_source_receipt(event, source)
    return messaging.log_from_bytes(event.to_bytes())

  with patch.object(factory, 'get_non_essential_params', return_value=cp), patch.object(live, 'source_packet', side_effect=source_packet):
    with live_controls() as fixture:
      yield fixture


@pytest.mark.parametrize('input_limited', [False, True])
@pytest.mark.parametrize('published_limited', [False, True])
def test_original_input_and_actual_hooks_determine_map_not_later_feedback(input_limited, published_limited):
  with known_vehicle_controls() as fixture:
    runtime = fixture.runtime
    source = fixture.source(runtime.now_ns)
    scope = RouteScope(runtime.descriptor.cp_sha256, runtime.descriptor.controller_revision,
                       runtime.baseline_revision, source.persistent_source_epoch, source.reset,
                       runtime.descriptor.controller_mode, runtime.descriptor.domain).validate()
    controller = fixture.controls.LaC
    controller.route_flm_scope = scope
    controller.route_flm_scope_key = scope.key()
    changed = dict(ZERO, friction_threshold_mid=.001, friction_threshold_highway=.002)
    owner = RouteEffects(scope, tuple(changed.items()), baseline_values(controller))
    controller.route_flm_effects = owner
    fixture.controls.steer_limited_by_safety = input_limited
    now, cc = fixture.frame()
    assert runtime.reason == 'offroad_route_recording'
    assert owner.frame_allowed
    expected_calls = 0 if input_limited else 1
    assert (owner.source.stage_calls, owner.source.friction_calls, owner.source.deadzone_calls,
            owner.source.output_calls) == (expected_calls,) * 4
    before = cc.to_dict()
    event = messaging.new_message('starpilotLateralState', valid=True, logMonoTime=now)
    with patch('openpilot.starpilot.flm.offroad_runtime.time.monotonic_ns', return_value=now):
      assert control_message(event, controller, cc, control_ns=now,
                             state_ns=int(fixture.controls.sm.logMonoTime['carState']), limited=published_limited)
    recorded = read_control_metadata(event.as_reader())
    from openpilot.starpilot.flm.learning_wire import read_control
    from openpilot.starpilot.flm.learner_factory import _frame

    frame = _frame(read_control(event.as_reader()))
    assert frame.route_applied_coordinates == recorded.route_applied_coordinates
    assert dict(recorded.route_applied_coordinates) == (ZERO if input_limited else changed)
    assert dict(recorded.route_configured_coordinates) == changed
    assert recorded.limited is published_limited
    assert recorded.native_allowed and recorded.valid
    assert dict(runtime.replay_inputs)['safety_limited'] == float(input_limited)
    assert cc.to_dict() == before, 'Logging cannot alter the actual command or limiter'


def test_partial_hooks_still_refuse_instead_of_claiming_a_complete_map():
  from openpilot.starpilot.flm.route_recording import with_actual_route, BASES
  from openpilot.starpilot.flm.tests.test_offroad_backend import scope

  owner = RouteEffects(scope(), tuple(dict(ZERO, center_taper_max=.005).items()), ())
  owner.frame_allowed = True
  owner.source.begin_frame(1)
  owner.source.stage_calls = 1
  from openpilot.starpilot.flm.tests.test_control_wire_fields import row_with_fields

  evidence = replace(row_with_fields(), stage_schema_revision=BASES[0], active=True, valid=True, manual=False,
                     override=False, fault=False, limited=True, live_applied_coordinates=(),
                     replay_inputs=(('active', 1.0), ('safety_limited', 1.0)))
  with pytest.raises(ValueError, match='Partial route execution'):
    with_actual_route(evidence, SimpleNamespace(route_flm_scope=scope(), route_flm_scope_key=scope().key(), route_flm_effects=owner))


def test_recorded_graph_survives_absent_automatic_comparison_windows():
  from openpilot.starpilot.flm.offroad_evidence import whole_windows
  from openpilot.starpilot.flm.offroad_processor import _assess_inputs
  from openpilot.starpilot.flm.tests.test_offroad_coordinate_policy import TestCrossDriveEvidence

  inputs, inventory = TestCrossDriveEvidence.inputs(1)
  context, original = next(iter(inputs.windows.items()))
  raw = [w for source in original for w in whole_windows(source.points)]
  inputs.raw_windows = {context: raw}
  inputs.windows = {}
  assessment = _assess_inputs(inventory, inputs, ZERO, permitted=lambda: True)
  observed = assessment.recorded_tracking
  assert observed is not None
  assert observed['windows'] == len(raw)
  assert observed['source_start_ns'] == raw[0].points[0].mono_ns
  assert observed['source_end_ns'] == raw[0].points[-1].mono_ns
  assert observed['points'][0] == [raw[0].points[0].mono_ns, raw[0].points[0].desired, raw[0].points[0].actual]
  assert assessment.counts['qualified_whole_windows'] == 0
  assert assessment.counts['stable_qualified_whole_windows'] == 0
  assert not assessment.groups and not assessment.stable_groups
  assert not assessment.report()['automatically_saved']
