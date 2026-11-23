from contextlib import ExitStack

import pytest
from opendbc.car.hyundai.tests.test_ev9_pending_startup import EV9StartupFixture

from openpilot.cereal import log
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car.car_events import MAX_CTRL_SPEED
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture


@pytest.mark.parametrize('ending', ('resume', 'panda_fault', 'disarm', 'unarmed'))
def test_ev9_speed_limit_pauses_output_and_preserves_only_established_aol(ending):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')

  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    startup = EV9StartupFixture()
    cleanup.callback(startup.doCleanups)
    startup.setUp()
    owner, interface, _ = startup.instance(seal=False)
    cleanup.callback(owner.close)
    sd, _, step, card, receive = case._ioniq_availability_callers(
      full_op=False, stock_angle=True, cp=interface.CP, startup_owner=owner, initially_armed=ending != 'unarmed')
    assert card.CP.safetyConfigs[0].safetyParam == 0x5C95 and card.CP.alternativeExperience == 32

    def update(tick, speed, **kwargs):
      cs = fixture.car_state()
      cs.vEgo = cs.vEgoRaw = speed
      axis, state, command = step(tick, observed_state=cs, gas=True, **kwargs)
      latch = receive(10_000_000_000 + tick * 10_000_000, state=cs,
                      axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      return axis, state, command, latch

    axis, state, command, latch = update(2, MAX_CTRL_SPEED + 0.004, allowed=ending != 'unarmed')
    assert log.OnroadEvent.EventName.speedTooHigh in sd.events.names
    assert not any((axis.desiredLateral, axis.desiredLongitudinal, axis.lateralActive, axis.longitudinalActive,
                    command.latActive, command.longActive))
    if ending == 'unarmed':
      assert not latch and not axis.nativeAcknowledged
      return

    assert axis.nativeAcknowledged and latch
    assert state.alertType == 'speedTooHigh/warning'
    axis, _, command, latch = update(3, MAX_CTRL_SPEED + 0.004, allowed=False, native_request_lat=False)
    assert axis.nativeAcknowledged and latch and not (axis.desiredLateral or command.latActive or command.longActive)

    if ending == 'panda_fault':
      axis, _, command, latch = update(4, MAX_CTRL_SPEED + 0.004, relay_fault=True)
      assert not latch and not axis.nativeAcknowledged and not (command.latActive or command.longActive)
      return
    if ending == 'disarm':
      axis, _, command, _ = update(4, MAX_CTRL_SPEED + 0.004, latched=False, allowed=False, native_request_lat=False)
      assert not axis.nativeAcknowledged and not (axis.desiredLateral or command.latActive or command.longActive)
      return

    axis, state, command, latch = update(4, MAX_CTRL_SPEED - 0.008, allowed=False, native_request_lat=False)
    assert latch and axis.desiredLateral and not (command.latActive or command.longActive)
    assert state.alertType == 'speedTooHigh/warning'
    axis, _, command, latch = update(5, MAX_CTRL_SPEED - 0.008)
    assert latch and axis.lateralActive and command.latActive and not command.longActive
