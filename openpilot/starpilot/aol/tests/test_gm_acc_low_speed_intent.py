"""Cruise's engagement floor cannot clear independent GM steering intent."""
from contextlib import ExitStack

import pytest
from opendbc.car.gm.tests.test_ascm_intercept import params
from opendbc.car.gm.values import CAR
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.selfdrived.events import EventName
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.tests import test_gm as gm_fixture, test_ioniq_availability as fixture
from openpilot.starpilot.aol.wire import IntentState, encode_intent
from openpilot.starpilot.audio.tests.test_axis_alerts import AudioFixture, AudibleAlert


@pytest.mark.parametrize('candidate', [CAR.CHEVROLET_BLAZER, CAR.CHEVROLET_TRAVERSE, CAR.CHEVROLET_MALIBU_ASCM])
@pytest.mark.parametrize('braking', [False, True])
def test_acc_cruise_floor_does_not_clear_main_intent_or_chime_on_pedals(candidate, braking):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    saved = Params()
    saved.put_bool('AlwaysOnLateral', True, block=True)
    card = gm_fixture.TestGmAol.card(params(candidate, sascm=True, alpha=True), saved)
    cleanup.callback(card.vehicle_startup.close)
    card.slc_producer_session = 'authority-card'
    card.aol_card_intent.allowed_latch = True
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    sd, _, step = case._authority_callers(False, cp=card.CP.as_reader().as_builder(), genuine_events=True)
    sd.aol_settings = read_settings(sd.params)
    audio = AudioFixture()
    sounds = []
    for tick, speed in enumerate((5., 4., 3.2, 2., 1.5, 1.3, .5, 0., .5, 1.3, 2., 3.2, 4.)):
      now = 10_000_000_000 + tick * 10_000_000
      cs = fixture.car_state(brake=braking and tick < 8)
      cs.vEgo = cs.vEgoRaw = speed
      cs.standstill = speed == 0.
      cs.cruiseState.enabled = False
      axis, host, control = step(tick, observed_state=cs, gas=tick >= 8,
        latched=card.aol_card_intent.allowed_latch, native_request_long=False, panda_controls_allowed=False)
      assert not host.enabled and not control.longActive and not axis.longitudinalActive
      assert axis.desiredLateral and axis.lateralActive and axis.nativeAcknowledged
      assert bool(control.latActive) == (speed >= card.CP.minSteerSpeed)
      below_cruise_minimum = speed < card.CP.minEnableSpeed and not (cs.brakePressed and cs.standstill)
      assert (EventName.belowEngageSpeed in sd.events.names) == below_cruise_minimum
      frames = [sd.pm.messages[n] for n in ('aolAxisState', 'onroadEvents', 'selfdriveState')]
      for service in ('pandaStates', 'aolSafetyWire', 'deviceState'):
        msg = messaging.new_message(service, len(sd.sm[service]) if service in ('pandaStates', 'aolSafetyWire') else None,
                                    valid=sd.sm.valid[service], logMonoTime=sd.sm.logMonoTime[service])
        setattr(msg, service, sd.sm[service])
        frames.append(msg.as_reader())
      card.sm.update_msgs(now / 1e9, frames)
      rejected = card.aol_disarming_fault(sd.CS_prev, int(card.sm.logMonoTime['onroadEvents']), now)
      card.aol_card_intent.update(sd.CS_prev, fault_active=rejected, now_ns=now)
      assert not rejected and card.aol_card_intent.allowed_latch
      wire = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=now)
      wire.aolIntentWire = encode_intent(IntentState('authority-card', tick+1, now, now, now+200_000_000,
        True, False, False, True, card.aol_card_intent.allowed_latch))
      sounds.append(audio.publish(intent_message=wire, enabled=False, native=host.alertSound))
    assert sounds == [AudibleAlert.none] * len(sounds)
