"""Fresh moving Toyota LAT-only input replaces an expired proof with PCM already off."""
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from opendbc.car.toyota import prius_advert_filter
from openpilot.cereal import log
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import BASE, intent
from openpilot.starpilot.aol.tests.test_toyota_parking_brake_deadline import refresh_saved_settings
from openpilot.starpilot.car.toyota.tests.test_pedal_parent_configurations import sources


def moving():
  state = fixture.car_state()
  state.vEgo = state.vEgoRaw = 13.64
  state.standstill = False
  state.cruiseState.available = True
  state.cruiseState.enabled = False
  return state


def cp_for(profile):
  if profile.startswith('prius'):
    fp, fw = sources(CAR.TOYOTA_PRIUS, '2FF4', 'EPS73_8965B47070')
    cp = CarInterface.get_params(CAR.TOYOTA_PRIUS, fp, fw, True, False, False)
    assert cp.safetyConfigs[0].safetyParam == 0xD049
    if profile.endswith('stock'):
      assert prius_advert_filter.prepare_stock(cp)
    return cp
  return CarInterface.get_params(CAR.TOYOTA_HIGHLANDER_TSS2, {0: {}, 1: {}, 2: {}}, [], profile.endswith('long'), False, False)


def callers(cleanup, profile):
  cleanup.enter_context(OpenpilotPrefix())
  cleanup.enter_context(patch.dict('os.environ', {'SIMULATION': '0', 'REPLAY': '0'}))
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  cleanup.callback(case.doCleanups)
  sd, controls, drive = case._authority_callers(False, cp=cp_for(profile), genuine_events=True)
  refresh_saved_settings(sd)
  for tick in (0, 1):
    axis, host, command = drive(tick, observed_state=moving(), native_request_long=False)
    assert axis.lateralActive and command.latActive and not host.enabled and not command.longActive
  prior, _, command = drive(2, offset_ns=7_578_769, state_stamp=BASE,
    intent_message=intent(BASE, 3), observed_state=moving(), native_request_long=False)
  assert prior.lateralActive and command.latActive
  assert prior.validUntilMonoTime == BASE + 37_578_769
  assert log.OnroadEvent.EventName.pcmDisable in sd.events.names
  return sd, controls, drive, prior


@pytest.mark.parametrize('profile', ('prius_long', 'prius_stock', 'camera_long', 'camera_stock'))
@pytest.mark.parametrize('expired', (False, True))
def test_fresh_moving_source_keeps_lateral_without_reengaging_cruise(profile, expired):
  with ExitStack() as cleanup:
    sd, controls, drive, prior = callers(cleanup, profile)
    source = BASE + 27_024_668
    now = BASE + (37_651_171 if expired else 35_651_171)
    if expired:
      # Recorded route04: previous axis proof expired by exactly 72,402ns.
      with patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now):
        old, _ = controls.state_control()
      assert not old.latActive and not old.longActive
    axis, host, command = drive(5, offset_ns=now - BASE - 30_000_000,
      state_stamp=source, intent_message=intent(source, 4), observed_state=moving(), native_request_long=False)
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names, 'TOYOTA_MOVING_PCM_ALREADY_OFF'
    assert axis.sourceCarStateMonoTime == source and axis.lateralActive and command.latActive
    assert not host.enabled and not host.active and not axis.longitudinalActive and not command.longActive
    assert not sd.aol_authority_lost and sd.aol_last_intent.allowedLatch
    assert axis.validUntilMonoTime != prior.validUntilMonoTime


@pytest.mark.parametrize('restriction', ('native_denied', 'native_invalid', 'can_invalid', 'eps', 'cancel',
                                        'intent_withdrawn', 'source_reversed', 'stale_source', 'source_future',
                                        'main_off', 'prior_main_off', 'prior_cruise_on'))
def test_current_restrictions_never_reuse_the_expired_lateral_proof(restriction):
  with ExitStack() as cleanup:
    sd, _, drive, _ = callers(cleanup, 'prius_long')
    state = moving()
    source = BASE + 27_024_668
    if restriction == 'can_invalid':
      state.canValid = False
    elif restriction == 'eps':
      state.steerFaultPermanent = True
    elif restriction == 'cancel':
      state.buttonEvents = [fixture.car.CarState.ButtonEvent(type='cancel', pressed=True)]
    elif restriction == 'source_reversed':
      source = BASE - 1
    elif restriction == 'stale_source':
      source = BASE
    elif restriction == 'source_future':
      source = BASE + 40_000_000
    elif restriction == 'main_off':
      state.cruiseState.available = False
    elif restriction in ('prior_main_off', 'prior_cruise_on'):
      previous = moving()
      previous.cruiseState.available = restriction != 'prior_main_off'
      previous.cruiseState.enabled = restriction == 'prior_cruise_on'
      sd.CS_prev = previous.as_reader()
    axis, host, command = drive(5, offset_ns=7_651_171, state_stamp=source,
      intent_message=intent(source, 4, latched=restriction != 'intent_withdrawn'), observed_state=state,
      native_request_long=False, allowed=restriction not in ('native_denied', 'main_off'), native_valid=restriction != 'native_invalid')
    assert not axis.lateralActive and not command.latActive
    assert not axis.longitudinalActive and not command.longActive and not host.enabled
