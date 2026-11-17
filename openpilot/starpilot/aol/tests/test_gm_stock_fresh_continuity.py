"""A fresh stock-GM LAT source does not inherit an expired output's PCM-off veto."""
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from openpilot.cereal import messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.selfdrived.events import EventName
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_gm_stock_gas_source_gap import params
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import intent

BASE = 10_000_000_000


@pytest.mark.parametrize('name', ['ascm', 'sdgm', 'bolt_acc'])
@pytest.mark.parametrize('fault', ['none', 'native', 'can', 'eps', 'cancel', 'main'])
def test_fresh_stock_gas_source_keeps_lateral_after_previous_output_expires(name, fault):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    sd, controls, step = case._authority_callers(False, cp=params(name), genuine_events=True)
    sd.aol_settings = read_settings(sd.params)
    state = fixture.car_state()
    state.vEgo = state.vEgoRaw = 12.1
    state.cruiseState.enabled = False
    for tick in (0, 1):
      axis, host, command = step(tick, observed_state=state, gas=True,
        native_request_long=False, panda_controls_allowed=False)
      assert axis.lateralActive and command.latActive and not command.longActive and not host.enabled
    prior_deadline = axis.validUntilMonoTime
    now = BASE + 54_063_646
    with patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now):
      expired, _ = controls.state_control()
    assert now > prior_deadline and not expired.latActive and not expired.longActive
    if fault == 'can':
      state.canValid = False
    elif fault == 'eps':
      state.steerFaultPermanent = True
    elif fault == 'main':
      state.cruiseState.available = False
    elif fault == 'cancel':
      state.buttonEvents = [fixture.car.CarState.ButtonEvent(type=fixture.car.CarState.ButtonEvent.Type.cancel, pressed=True)]
    source = BASE + 44_038_177
    raw = messaging.new_message('carState', valid=True, logMonoTime=source)
    raw.carState = state
    authority = messaging.new_message('starpilotCarState', valid=True, logMonoTime=source)
    authority.starpilotCarState.sourceCarStateMonoTime = source
    if sd.steering_companion is not None:
      sd.steering_companion._observe(source, True, authority.starpilotCarState)
    axis, host, command = step(5, offset_ns=4_063_646, observed_state=state, gas=True,
      car_state_message=raw, authority_message=authority, intent_message=intent(source, 6), native_request_long=False,
      panda_controls_allowed=False, native_valid=fault != 'native')
    assert not command.longActive and not axis.longitudinalActive and not host.enabled
    if fault != 'none':
      assert not command.latActive and not axis.lateralActive
      return
    assert axis.sourceCarStateMonoTime == source and axis.validUntilMonoTime > prior_deadline
    assert axis.lateralActive and command.latActive and axis.nativeAcknowledged
    assert not sd.aol_authority_lost and EventName.controlsMismatch not in sd.events.names
    assert EventName.pcmDisable in sd.events.names
