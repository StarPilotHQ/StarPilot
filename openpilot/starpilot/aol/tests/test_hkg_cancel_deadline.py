from contextlib import ExitStack
from unittest import mock

import pytest

from opendbc.car.structs import car
from opendbc.car.hyundai.values import CAR
from opendbc.car.hyundai.tests.test_ioniq5pe_stock import params
from opendbc.car.hyundai.ioniq5pe_long_profile import candidate as pe_long
from opendbc.car.hyundai.ev9_longitudinal import candidate as ev9_long
from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import BASE, intent


@pytest.mark.parametrize('restriction', ['none', 'coast', 'native_denied', 'native_invalid', 'critical', 'cancel_mapping', 'expired_source', 'latch_off'])
@pytest.mark.parametrize('identity', [CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9])
def test_cancel_crossing_output_deadline_keeps_fresh_independent_lateral(identity, restriction):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    with OpenpilotPrefix():
      saved = Params()
      saved.put('LKASButtonControl', 9, block=True)
      for key in ('CancelButtonControl', 'LongCancelButtonControl', 'VeryLongCancelButtonControl'):
        saved.put(key, 0, block=True)
      if restriction in ('coast', 'cancel_mapping'):
        saved.put('LongCancelButtonControl', 8 if restriction == 'coast' else 9, block=True)
      cp = (pe_long if identity == CAR.HYUNDAI_IONIQ_5_PE else ev9_long)(
        params(candidate=identity), enabled=True, is_release=False)
      cp.alternativeExperience = 32
      sd, controls, step = case._authority_callers(True, cp=cp, genuine_events=True)
      sd.aol_settings = read_settings(saved)
      for tick in (0, 1):
        step(tick)
      prior, _, command = step(2, offset_ns=4_266_961, state_stamp=BASE, intent_message=intent(BASE, 3))
      assert prior.lateralActive and prior.longitudinalActive and command.latActive and command.longActive
      now = BASE + 34_638_124
      source = now - (31_000_000 if restriction == 'expired_source' else 24_218_150)
      assert now - prior.validUntilMonoTime == 371_163
      with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now):
        expired, _ = controls.state_control()
      assert not expired.latActive and not expired.longActive
      state = fixture.car_state()
      state.vEgoRaw = state.vEgo
      state.buttonEvents = [car.CarState.ButtonEvent(type='cancel', pressed=True)]
      axis, _, command = step(5, offset_ns=4_638_124, state_stamp=source,
        intent_message=intent(source, 4, latched=restriction != 'latch_off'), observed_state=state,
        allowed=restriction != 'native_denied', native_valid=restriction != 'native_invalid',
        critical_event=log.OnroadEvent.EventName.controlsMismatch if restriction == 'critical' else None)
      assert axis.sourceCarStateMonoTime == (0 if restriction == 'expired_source' else source)
      assert not axis.longitudinalActive and not command.longActive and not sd.enabled
      if restriction in ('none', 'coast'):
        assert axis.lateralActive and command.latActive
        assert not sd.aol_authority_lost and sd.aol_last_intent.allowedLatch
        assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
      else:
        assert not axis.lateralActive and not command.latActive
