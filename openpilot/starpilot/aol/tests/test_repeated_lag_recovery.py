from contextlib import ExitStack
from unittest import mock

import pytest

from opendbc.car.hyundai.tests.test_ioniq6_handoff import ioniq6_profiles
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture


@pytest.mark.parametrize('offset', (0, 4))
@pytest.mark.parametrize('order', ('event_first', 'axis_first', 'together'))
@pytest.mark.parametrize('fault', ('none', 'rx', 'critical', 'expired'))
def test_repeated_lag_while_braking_retains_intent_until_fresh_native_recovery(offset, order, fault):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    _, _, cp = ioniq6_profiles(alternate=True, bus_offset=offset)
    cp.safetyConfigs[-1].safetyParam |= 0x800
    sd, _, drive, card, receive = case._ioniq_availability_callers(full_op=False, cp=cp)
    def tick(number, *, lag=False, native=True, **extra):
      with mock.patch.object(type(sd.rk), 'lagging', new_callable=mock.PropertyMock, return_value=lag):
        axis, _, command = drive(number, brake=True, native_request_lat=native,
          native_request_long=False, allowed=native, **extra)
      return axis, command
    axis, command = tick(2)
    receive(10_020_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'],
      state=fixture.car_state(brake=True))
    assert card.aol_card_intent.allowed_latch and axis.lateralActive and command.latActive
    axis, command = tick(3, lag=True)
    receive(10_030_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'],
      state=fixture.car_state(brake=True))
    assert card.aol_card_intent.allowed_latch and card.aol_transport_feedback.ordinary_warning_retained
    assert not axis.lateralActive and not command.latActive
    axis, command = tick(4, native=False)
    receive(10_040_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'],
      state=fixture.car_state(brake=True))
    assert card.aol_card_intent.allowed_latch and axis.desiredLateral and not axis.lateralActive
    assert not command.latActive
    options = {}
    if fault == 'rx':
      options['panda_fault'] = True
    elif fault == 'critical':
      from openpilot.cereal import log
      options['critical_event'] = log.OnroadEvent.EventName.controlsMismatch
    axis, command = tick(5, lag=True, native=False, **options)
    proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
    if fault == 'expired':
      old = proof.as_builder()
      old.aolAxisState.validUntilMonoTime = 10_049_999_999
      proof = old.as_reader()
    deliveries = [{'event': event}, {'axis': proof}] if order == 'event_first' else \
      [{'axis': proof}, {'event': event}] if order == 'axis_first' else [{'axis': proof, 'event': event}]
    for index, delivery in enumerate(deliveries):
      latched = receive(10_050_000_000 + index * 100_000, state=fixture.car_state(brake=True), **delivery)
      if fault == 'none':
        assert latched and not card.aol_transport_feedback.failed
    if fault != 'none':
      assert not latched and not card.aol_card_intent.allowed_latch
      assert not axis.lateralActive and not command.latActive
      return
    assert not axis.lateralActive and not command.latActive
    axis, command = tick(6, native=False)
    receive(10_060_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'],
      state=fixture.car_state(brake=True))
    assert card.aol_card_intent.allowed_latch and not axis.lateralActive and not command.latActive
    axis, _, command = drive(7, native_request_lat=True, native_request_long=False, allowed=True)
    receive(10_070_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    assert card.aol_card_intent.allowed_latch and axis.lateralActive and command.latActive
