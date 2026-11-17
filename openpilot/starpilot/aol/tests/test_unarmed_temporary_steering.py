from contextlib import ExitStack

import pytest

from openpilot.cereal import log
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from opendbc.car import structs
from opendbc.car.hyundai.values import CAR


@pytest.mark.parametrize('profile', [False, CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9], ids=['ioniq6', 'ioniq5pe', 'ev9'])
def test_unarmed_parked_temporary_eps_keeps_fault_without_warning(profile):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, step, _, _ = case._ioniq_availability_callers(full_op=False, initially_armed=False, stock_angle=profile)
    parked = fixture.car_state()
    parked.gearShifter = structs.CarState.GearShifter.park
    parked.vEgo = parked.vEgoRaw = 0.
    parked.standstill = parked.steerFaultTemporary = True
    for tick in range(2, 32):
      axis, state, command = step(tick, observed_state=parked, native_request_lat=False,
                                  native_request_long=False, allowed=False)
      assert sd.CS_prev.steerFaultTemporary
      assert log.OnroadEvent.EventName.steerTempUnavailableSilent in sd.events.names
      assert not axis.lateralActive and not axis.longitudinalActive
      assert not command.latActive and not command.longActive and not command.enabled
      assert state.alertType != 'steerTempUnavailableSilent/warning', 'UNARMED_PARKED_EPS_WARNING'
    parked.steerFaultTemporary = False
    for tick in range(32, 45):
      _, state, command = step(tick, observed_state=parked, native_request_lat=False,
                               native_request_long=False, allowed=False)
      assert log.OnroadEvent.EventName.steerTempUnavailableSilent not in sd.events.names
      assert not command.latActive and not command.longActive
    assert state.alertType != 'steerTempUnavailableSilent/warning'
