"""Classic stock ACC user roles do not revoke an independent lateral owner."""
from contextlib import ExitStack

import pytest
from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from opendbc.car.hyundai.values import CAR
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_classic_scc_profiles import params
from openpilot.starpilot.aol.tests.test_ordinary_source_availability import settings

BASE = 10_000_000_000


def sample(stamp, pedal):
  message = messaging.new_message('carState', valid=True, logMonoTime=stamp)
  state = fixture.car_state(brake=pedal == 'brake')
  state.gasPressed = pedal == 'gas'
  state.cruiseState.available = state.cruiseState.enabled = False
  message.carState = state
  return message


@pytest.mark.parametrize('identity', [CAR.HYUNDAI_SONATA_HYBRID, CAR.HYUNDAI_SONATA, CAR.HYUNDAI_ELANTRA_2021])
@pytest.mark.parametrize('pedal', ['brake', 'gas'])
@pytest.mark.parametrize('fresh', [False, True])
def test_stock_hkg_main_off_pedal_source_join_preserves_only_valid_lateral(identity, pedal, fresh):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, controls, drive, card, receive = case._ioniq_availability_callers(False, cp=params(identity, 0x391), cold_start=True)
    sd.params.put('PauseAOLOnBrake', 0.0, block=True)
    sd.aol_settings = read_settings(sd.params)
    for tick in (0, 1, 2):
      stamp = BASE + tick * 10_000_000
      state = sample(stamp, pedal)
      axis, _, command = drive(tick, car_state_message=state, offset_ns=5_000_000 if tick == 2 else 0,
        long_allowed=False, **settings('lateral_only', stamp, tick + 1))
      assert axis.lateralActive and command.latActive and not command.longActive
      receive(stamp + (5_000_000 if tick == 2 else 0), axis=sd.pm.messages['aolAxisState'],
        event=sd.pm.messages['onroadEvents'], state=state.carState, preserve_cruise_state=True)
      assert card.aol_card_intent.allowed_latch
    original = axis.to_dict()
    stamp = BASE + (37_000_000 if fresh else 20_000_000)
    state = sample(stamp, pedal)
    axis, _, command = drive(6, car_state_message=state, timeout=not fresh, offset_ns=5_000_000,
      long_allowed=False, **settings('lateral_only', stamp, 4 if fresh else 3))
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names, 'STOCK_HKG_HELD_PEDAL_MISMATCH'
    assert not sd.aol_authority_lost and not command.longActive
    if fresh:
      assert axis.lateralActive and command.latActive
      assert axis.sourceCarStateMonoTime == stamp
      assert axis.validUntilMonoTime != original['validUntilMonoTime']
    else:
      assert str(axis.faultReason) == 'availabilityPause' and axis.desiredLateral
      assert not axis.lateralActive and not command.latActive and axis.sourceCarStateMonoTime == 0
    receive(BASE + 65_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'], state=state.carState, preserve_cruise_state=True)
    assert card.aol_card_intent.allowed_latch, 'STOCK_HKG_HELD_PEDAL_ERASED_LATCH'
    if not fresh:
      axis, _, command = drive(7, car_state_message=state, timeout=True, long_allowed=False,
        **settings('lateral_only', stamp, 3))
      assert str(axis.faultReason) == 'availabilityPause' and axis.desiredLateral
      assert not axis.lateralActive and not command.latActive and not command.longActive
      assert not sd.aol_authority_lost and log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
      receive(BASE + 70_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'],
        state=state.carState, preserve_cruise_state=True)
      assert card.aol_card_intent.allowed_latch
    stamp = BASE + 80_000_000
    state = sample(stamp, pedal)
    axis, _, command = drive(8, car_state_message=state, long_allowed=False,
      **settings('lateral_only', stamp, 5))
    assert axis.lateralActive and command.latActive and not command.longActive
