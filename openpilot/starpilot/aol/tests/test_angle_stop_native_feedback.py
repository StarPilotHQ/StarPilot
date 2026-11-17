from contextlib import ExitStack

import pytest

from opendbc.car.hyundai.canfd_angle_aol import temporary_restriction, MODELS
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.vehicle import native_latch_rejected
from openpilot.starpilot.aol.runtime import current_native
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture


@pytest.mark.parametrize('engaged', [pytest.param(False, id='aol_only'), pytest.param(True, id='stock_cruise_engaged')])
@pytest.mark.parametrize('identity', sorted(MODELS))
@pytest.mark.parametrize('signal', [False, True])
@pytest.mark.parametrize('brake', [False, True])
def test_native_stop_restriction_withdraws_steering_without_disarming_driver_intent(identity, signal, brake, engaged):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    with OpenpilotPrefix():
      saved = Params()
      saved.put_bool('AlwaysOnLateral', True, block=True)
      saved.put('LKASButtonControl', 9, block=True)
      saved.put('MainCruiseButtonControl', 0, block=True)
      saved.put('AolBrakePauseSpeedMps', 0.0, block=True)
      saved.put('PauseLateralSpeed', 0.0, block=True)
      saved.put_bool('PauseLateralOnSignal', False, block=True)
      saved.put('LateralResumeDelay', 0.0, block=True)
      for key in ('CancelButtonControl', 'LongCancelButtonControl', 'VeryLongCancelButtonControl'):
        saved.put(key, 0, block=True)
      sd, _, step, card, receive = case._ioniq_availability_callers(full_op=False, stock_angle=identity)
      sd.aol_settings = read_settings(saved)
      assert sd.aol_settings == card.aol_card_intent.settings
      assert sd.aol_settings.pause_brake_mps == 0.0
      rolling = fixture.car_state()
      rolling.vEgo = rolling.vEgoRaw = 1.0
      rolling.cruiseState.enabled = engaged
      axis, _, command = step(2, observed_state=rolling, panda_controls_allowed=engaged)
      assert axis.lateralActive and command.latActive
      assert receive(10_020_000_000, state=rolling, axis=sd.pm.messages['aolAxisState'],
                     event=sd.pm.messages['onroadEvents'])
      for tick in range(3, 37):
        stopped = fixture.car_state(brake=brake)
        stopped.vEgo = stopped.vEgoRaw = 0.0
        stopped.standstill = True
        stopped.leftBlinker = signal
        stopped.cruiseState.enabled = engaged
        assert temporary_restriction(card.CP, stopped)
        axis, _, command = step(tick, observed_state=stopped, allowed=False, native_request_lat=True,
                                native_request_long=False, panda_controls_allowed=engaged and not brake)
        now = 10_000_000_000 + tick * 10_000_000
        native = current_native(sd.sm, sd.CP, now_ns=now, axis_session_id=sd.aol_session_id)
        assert native is not None and native.requestedLateral and not native.lateralAllowed
        assert not native_latch_rejected(card.CP, native, state=stopped)
        assert not axis.lateralActive and not command.latActive
        assert not axis.longitudinalActive and not command.longActive
        assert receive(now, state=stopped, axis=sd.pm.messages['aolAxisState'],
                       event=sd.pm.messages['onroadEvents']), card.aol_card_intent.latch_clear_reason
        assert card.aol_card_intent.allowed_latch and not sd.aol_authority_lost
      axis, _, command = step(37, observed_state=rolling, allowed=True, native_request_lat=True,
                              native_request_long=False, panda_controls_allowed=engaged)
      assert axis.lateralActive and command.latActive
      assert not axis.longitudinalActive and not command.longActive
      assert receive(10_370_000_000, state=rolling, axis=sd.pm.messages['aolAxisState'],
                     event=sd.pm.messages['onroadEvents'])
      assert card.aol_card_intent.allowed_latch
