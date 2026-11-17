"""Manual confirmations follow the longitudinal axis, including pedal override."""

import pytest

from openpilot.cereal import messaging
from openpilot.starpilot.speed_limits.runtime import Action, Runtime
from openpilot.starpilot.speed_limits.runtime_settings import parse
from openpilot.starpilot.ui.tests.test_slc_ui_runtime import START, replay_inputs


@pytest.mark.parametrize('mode', ['disengaged', 'aol', 'longitudinal', 'combined', 'accelerator'])
def test_runtime_confirms_only_with_engaged_longitudinal_control(mode):
  sm, cp = replay_inputs()
  active = mode in ('longitudinal', 'combined')
  engaged = active or mode == 'accelerator'
  sm['selfdriveState'].enabled = sm['carControl'].enabled = engaged
  sm['carControl'].longActive = active
  sm['carControl'].latActive = mode in ('aol', 'combined')
  sm['controlsState'].longControlState = 'pid' if active else 'off'
  sm['carState'].gasPressed = mode == 'accelerator'
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCConfirmation': 1, 'SLCPriority1': 'Dashboard'}))
  output = runtime.step(sm, cp, now_ns=START)
  assert not output.result.errors
  assert output.message.slcState.hasPending == engaged
  assert output.message.slcState.observationKind == 'valid'
  assert output.message.slcState.presentationId > 0


def test_runtime_cancels_old_action_and_restarts_confirmation_after_disengagement():
  sm, cp = replay_inputs()
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCConfirmation': 1, 'SLCPriority1': 'Dashboard'}))
  first = runtime.step(sm, cp, now_ns=START).message.slcState
  action = Action(runtime.session_id, 1, first.decisionId, first.presentationId, 'accept')
  sm.advance(START+50_000_000)
  sm['selfdriveState'].enabled = sm['carControl'].enabled = sm['carControl'].longActive = False
  sm['carControl'].latActive = True
  sm['controlsState'].longControlState = 'off'
  inactive = runtime.step(sm, cp, now_ns=START+50_000_000, request=action)
  assert not inactive.message.slcState.hasPending and not inactive.message.slcState.hasAccepted
  assert not inactive.action_result.change
  now = START+20_000_000_000
  sm.advance(now)
  sm['slcDashboardObservation'].observedMonoTime = now
  sm['slcDashboardObservation'].validUntilMonoTime = now+1_000_000_000
  sm['selfdriveState'].enabled = sm['carControl'].enabled = sm['carControl'].longActive = True
  sm['controlsState'].longControlState = 'pid'
  resumed = runtime.step(sm, cp, now_ns=now).message.slcState
  assert resumed.hasPending and resumed.decisionId != first.decisionId
  assert resumed.pendingSinceMonoTime == now and not resumed.hasAccepted


@pytest.mark.parametrize('desired,acknowledged,fresh', [(False, True, True), (True, False, True),
                                                       (True, True, False), (True, True, True)])
def test_aol_pedal_override_requires_current_longitudinal_engagement(desired, acknowledged, fresh):
  sm, cp = replay_inputs()
  sm['carControl'].longActive = False
  sm['carControl'].latActive = True
  sm['carState'].gasPressed = True
  sm['controlsState'].longControlState = 'off'
  axis = messaging.new_message('aolAxisState').aolAxisState
  axis.qualified, axis.nativeAcknowledged, axis.desiredLongitudinal = True, acknowledged, desired
  axis.sessionId = 'aol-drive'
  axis.observedMonoTime = START
  axis.validUntilMonoTime = START+30_000_000
  sm.data['aolAxisState'] = axis
  sm.seen = {'aolAxisState': True}
  sm.valid['aolAxisState'] = sm.alive['aolAxisState'] = True
  sm.logMonoTime['aolAxisState'] = START if fresh else START-100_000_000
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCConfirmation': 1, 'SLCPriority1': 'Dashboard'}))
  output = runtime.step(sm, cp, now_ns=START)
  assert not output.result.errors
  assert output.message.slcState.hasPending == (desired and acknowledged and fresh)
