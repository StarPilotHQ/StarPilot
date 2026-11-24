from contextlib import ExitStack

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.mazda.interface import CarInterface
from opendbc.car.mazda.values import CAR
from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture


@pytest.mark.parametrize('identity', [CAR.MAZDA_CX5, CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021])
@pytest.mark.parametrize('case_name', ['launch', 'moving_denial', 'panda_fault'])
def test_mazda_intersection_aol(identity, case_name):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    params = Params()
    params.put('AolBrakePauseSpeedMps', 0.0, block=True)
    params.put('PauseLateralSpeed', 0.0, block=True)
    params.put_bool('PauseLateralOnSignal', False, block=True)
    cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
    sd, _, step = case._authority_callers(False, cp=cp, genuine_events=True)
    sd.aol_settings = read_settings(params)
    assert sd.aol_settings.pause_brake_mps == 0.0
    state = fixture.car_state()
    state.leftBlinker = True
    state.cruiseState.enabled = True
    state.vEgo = state.vEgoRaw = 20.0
    axis, _, command = step(2, observed_state=state, native_request_long=False)
    assert axis.lateralActive and command.latActive and not command.longActive
    state.standstill = case_name != 'moving_denial'
    if state.standstill:
      state.vEgo = state.vEgoRaw = 0.0
    axis, _, command = step(3, observed_state=state, allowed=False, native_request_long=False,
                            panda_fault=case_name == 'panda_fault')
    assert not axis.lateralActive and not command.latActive and not command.longActive
    if case_name != 'launch':
      assert log.OnroadEvent.EventName.controlsMismatch in sd.events.names
      return
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
    assert not sd.aol_authority_lost
    state.cruiseState.enabled = False
    for tick in range(4, 30):
      state.brakePressed = tick < 27
      axis, _, command = step(tick, observed_state=state, gas=tick >= 28, allowed=False, native_request_long=False)
      assert not command.latActive and not command.longActive and not sd.aol_authority_lost
      assert sd.aol_last_intent.allowedLatch
    state.standstill = state.brakePressed = False
    state.vEgo = state.vEgoRaw = 20.0
    axis, _, command = step(30, observed_state=state, gas=True, native_request_long=False)
    assert axis.lateralActive and command.latActive and not command.longActive
    assert sd.aol_last_intent.allowedLatch and not sd.aol_authority_lost
    state.brakePressed = True
    axis, _, command = step(31, observed_state=state, native_request_long=False)
    assert axis.lateralActive and command.latActive and not command.longActive
