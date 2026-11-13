from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.hyundai.interface import CarInterface as HyundaiInterface
from opendbc.car.hyundai.values import CAR
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.starpilot.speed_limits import composition
from openpilot.starpilot.speed_limits.runtime_settings import parse
from openpilot.starpilot.speed_limits.tests.test_runtime_replay import RuntimeReplayTests
from openpilot.starpilot.speed_limits.tests.test_set_speed import tick


@pytest.fixture(params=('honda', 'hyundai'))
def drive(request):
  fixture = RuntimeReplayTests()
  fixture.setUp()
  if request.param == 'hyundai':
    fingerprint = gen_empty_fingerprint()
    fingerprint[2][0x421] = 8
    fixture.cp = HyundaiInterface.get_params(CAR.HYUNDAI_ELANTRA_2021, fingerprint, [], True, False, False)
  assert fixture.cp.openpilotLongitudinalControl
  fixture.cp.pcmCruise = False
  state = fixture.sm['carState']
  state.vEgo, state.vEgoCluster = 15., 16.
  state.standstill = False
  state.vCruise = state.vCruiseCluster = 72.
  fixture.sm['slcDashboardObservation'].speedMps = 16.
  runtime = fixture.accepted_runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard'}),
                                     session_id='cluster-feedback')
  return fixture, runtime


@pytest.mark.parametrize('change', (-.3, .3))
def test_raw_wheel_feedback_remains_active_while_dashboard_is_held(drive, change):
  fixture, runtime = drive
  first = tick(runtime, fixture.sm, fixture.cp, 0.)
  assert first.result.ceiling is not None
  assert first.result.ceiling.speed_mps == pytest.approx(15.)
  fixture.sm['carState'].vEgo = 15. + change
  following = tick(runtime, fixture.sm, fixture.cp, .05)
  assert following.result.ceiling is not None
  assert abs(following.result.ceiling.speed_mps - first.result.ceiling.speed_mps) < .02
  error = following.result.ceiling.speed_mps - fixture.sm['carState'].vEgo
  assert error * change < 0 and abs(error) > abs(change) * .9
  assert following.result.domain.context.ego_raw_mps == pytest.approx(15. + change)
  assert fixture.sm['carState'].vEgoCluster == 16.


@pytest.mark.parametrize('display_step', (1. / 3.6, .44704))
def test_whole_display_speed_steps_do_not_jump_the_planner_target(drive, display_step):
  fixture, runtime = drive
  first = tick(runtime, fixture.sm, fixture.cp, 0.)
  fixture.sm['carState'].vEgoCluster += display_step
  following = tick(runtime, fixture.sm, fixture.cp, .05)
  assert first.result.ceiling is not None and following.result.ceiling is not None
  assert abs(following.result.ceiling.speed_mps - first.result.ceiling.speed_mps) < display_step * .1
  assert following.message.slcState.acceptedSpeedLimit == first.message.slcState.acceptedSpeedLimit
  assert fixture.sm['carState'].vEgo == 15.


def test_actual_native_planner_responds_before_the_dashboard_updates(drive):
  fixture, runtime = drive
  tick(runtime, fixture.sm, fixture.cp, 0.)
  fixture.sm['carState'].vEgo = 15.3
  fixture.sm.advance(2_050_000_000)
  frame = runtime._frame(fixture.sm, fixture.cp, 2_050_000_000, None, None, None, None,
                         wheel_override_blocked=False)
  result = composition.step(runtime.state, frame)
  old = composition.step(runtime.state, replace(frame, filtered_ego_delta_mps=None))
  assert result.ceiling is not None and old.ceiling is not None
  current = LongitudinalPlanner(fixture.cp, init_v=15.3)
  legacy = LongitudinalPlanner(fixture.cp, init_v=15.3)
  current.update(fixture.sm, cruise_ceiling=result.ceiling)
  legacy.update(fixture.sm, cruise_ceiling=old.ceiling)
  assert current.mpc.solution_status == legacy.mpc.solution_status == 0
  assert current.output_a_target < -.01
  assert abs(legacy.output_a_target) < 1e-5
  np.testing.assert_array_equal(current.mpc.params, legacy.mpc.params)
  np.testing.assert_array_equal(current.mpc.x_sol, legacy.mpc.x_sol)


@pytest.mark.parametrize('failure', ('stale', 'invalid', 'timeout', 'bad_domain'))
def test_filter_cannot_admit_bad_current_speed_evidence(drive, failure):
  fixture, runtime = drive
  tick(runtime, fixture.sm, fixture.cp, 0.)
  now = 2_050_000_000
  fixture.sm.advance(now)
  if failure == 'stale':
    fixture.sm.logMonoTime['carState'] = now - 1_000_000_000
  elif failure == 'invalid':
    fixture.sm.valid['carState'] = False
  elif failure == 'timeout':
    fixture.sm['carState'].canTimeout = True
  else:
    fixture.sm['carState'].vEgoCluster = 100.
  output = runtime.step(fixture.sm, fixture.cp, now_ns=now)
  assert output.result.ceiling is None
  assert not output.message.slcState.hasCeiling


def test_duplicate_sample_does_not_advance_calibration(drive):
  fixture, runtime = drive
  tick(runtime, fixture.sm, fixture.cp, 0.)
  fixture.sm['carState'].vEgoCluster += 1.
  first = tick(runtime, fixture.sm, fixture.cp, .05)
  following = runtime.step(fixture.sm, fixture.cp, now_ns=2_060_000_000)
  assert first.result.ceiling is not None and following.result.ceiling is not None
  assert following.result.ceiling.speed_mps == first.result.ceiling.speed_mps


@pytest.mark.parametrize('value', (float('nan'), float('inf'), -1.))
def test_invalid_filtered_coordinate_is_rejected(drive, value):
  fixture, runtime = drive
  with patch.object(runtime, '_smoothed_ego_delta', return_value=value):
    output = tick(runtime, fixture.sm, fixture.cp, 0.)
  assert output.result.errors
  assert output.result.ceiling is None


def test_new_lower_speed_limit_is_not_smoothed(drive):
  fixture, _ = drive
  runtime = fixture.accepted_runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard',
                                            'SLCConfirmation': 0}), session_id='immediate-lower-limit')
  first = tick(runtime, fixture.sm, fixture.cp, 0.)
  source = fixture.sm['slcDashboardObservation']
  source.speedMps = 12.
  source.episode += 1
  source.observedMonoTime = 2_050_000_000
  fixture.sm['carState'].vEgoCluster += .44704
  lowered = tick(runtime, fixture.sm, fixture.cp, .05)
  assert first.result.ceiling is not None and lowered.result.ceiling is not None
  assert lowered.message.slcState.acceptedSpeedLimit == 12.
  assert lowered.result.ceiling.speed_mps < first.result.ceiling.speed_mps - 3.9
  assert fixture.sm['carState'].vCruise == 72.
