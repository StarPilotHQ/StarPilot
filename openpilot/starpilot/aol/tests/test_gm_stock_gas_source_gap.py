"""Stock LONG-off source gaps preserve GM LAT intent, never expired output."""
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from opendbc.car.gm.tests.test_bolt_factory_acc import factory_params
from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
from opendbc.car.gm.tests.test_volt_sdgm_control import sdgm_params
from opendbc.car.gm.values import CAR
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.selfdrived.events import EventName
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import intent
from openpilot.starpilot.aol.tests import test_gm as gm_fixture
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture

BASE = 10_000_000_000


def params(name):
  if name == 'ascm':
    return ordinary_params(CAR.CHEVROLET_VOLT_ASCM, alpha=False, sascm=True, radar=True)
  if name == 'sdgm':
    return sdgm_params(alpha=False, brake_c9=True)
  return factory_params(alpha=False)


@pytest.mark.parametrize('name', ['ascm', 'sdgm', 'bolt_acc'])
@pytest.mark.parametrize('fault', ['none', 'native', 'rx', 'can', 'eps', 'cancel', 'new_gas', 'expired', 'model'])
def test_accepted_stock_gas_gap_reaches_card_without_takeover_or_output(name, fault):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    saved = Params()
    saved.put_bool('AlwaysOnLateral', True, block=True)
    card = gm_fixture.TestGmAol.card(params(name), saved)
    cleanup.callback(card.vehicle_startup.close)
    card.slc_producer_session = 'authority-card'
    card.aol_card_intent.allowed_latch = True
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    sd, controls, step = case._authority_callers(False, cp=card.CP.as_reader().as_builder(), genuine_events=True)
    sd.aol_settings = read_settings(sd.params)
    def receive(now):
      frames = [sd.pm.messages[n] for n in ('aolAxisState', 'onroadEvents', 'selfdriveState')]
      for service in ('pandaStates', 'aolSafetyWire', 'deviceState'):
        msg = messaging.new_message(service, len(sd.sm[service]) if service in ('pandaStates', 'aolSafetyWire') else None,
                                    valid=sd.sm.valid[service], logMonoTime=sd.sm.logMonoTime[service])
        setattr(msg, service, sd.sm[service])
        frames.append(msg.as_reader())
      card.sm.update_msgs(now / 1e9, frames)
      return card.aol_disarming_fault(sd.CS_prev, int(card.sm.logMonoTime['onroadEvents']), now)
    for tick in (0, 1):
      state = fixture.car_state()
      state.gearShifter = fixture.car.CarState.GearShifter.low
      state.vEgo = state.vEgoRaw = 12.5
      state.cruiseState.enabled = False
      axis, host, command = step(tick, observed_state=state, gas=fault != 'new_gas', steering=True,
        native_request_long=False, panda_controls_allowed=False)
      assert axis.lateralActive and command.latActive and not command.longActive and not host.enabled
      assert not receive(BASE + tick * 10_000_000)
    original_deadline = axis.validUntilMonoTime
    cs = sd.CS_prev
    sd.car_state_samples = ()
    if fault in ('can', 'eps', 'new_gas'):
      cs = cs.as_builder()
      if fault == 'can':
        cs.canValid = False
      elif fault == 'eps':
        cs.steerFaultPermanent = True
      else:
        cs.gasPressed = True
    elif fault == 'native':
      sd.sm.valid['aolSafetyWire'] = False
    elif fault == 'rx':
      bad = messaging.new_message('pandaStates', len(sd.sm['pandaStates']), valid=True, logMonoTime=BASE + 10_000_000)
      for slot, previous in zip(bad.pandaStates, sd.sm['pandaStates'], strict=True):
        slot.from_dict(previous.to_dict())
        slot.safetyRxChecksInvalid = True
      sd.sm.update_msgs((BASE + 10_000_000) / 1e9, [bad.as_reader()])
    elif fault == 'cancel':
      sd.events.add(EventName.buttonCancel)
    elif fault == 'model':
      sd.sm.valid['modelV2'] = False
    now = BASE + (220_000_001 if fault == 'expired' else 41_372_648)
    with patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now):
      sd.publish_axis_proof(cs)
    paused = sd.pm.messages['aolAxisState'].aolAxisState
    assert not paused.lateralActive and not paused.longitudinalActive and paused.sourceCarStateMonoTime == 0
    # Original Controls decision cannot command after its own original deadline.
    with patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=max(now, original_deadline + 1)):
      expired, _ = controls.state_control()
    assert not expired.latActive and not expired.longActive
    if fault != 'none':
      assert sd.aol_authority_lost and EventName.controlsMismatch in sd.events.names
      return
    assert str(paused.faultReason) == 'availabilityPause' and paused.desiredLateral and paused.nativeAcknowledged
    assert not sd.aol_authority_lost and EventName.controlsMismatch not in sd.events.names
    assert not receive(now)
    assert card.aol_transport_feedback.retention_verified and not card.aol_transport_feedback.failed
    for tick in (6, 10):
      held, host, command = step(tick, timeout=True, state_stamp=BASE + 10_000_000,
        intent_message=intent(BASE + 10_000_000, 2), observed_state=state, gas=True, steering=True,
        native_request_long=False, panda_controls_allowed=False)
      assert str(held.faultReason) == 'availabilityPause' and held.sourceCarStateMonoTime == 0
      assert not held.lateralActive and not held.longitudinalActive and not sd.aol_authority_lost
      assert not command.latActive and not command.longActive and not host.enabled
      assert EventName.controlsMismatch not in sd.events.names and not receive(BASE + tick * 10_000_000)
    axis, host, command = step(11, gas=True, steering=True, observed_state=state, native_request_long=False,
                               panda_controls_allowed=False)
    assert axis.lateralActive and command.latActive and not command.longActive and not host.enabled
