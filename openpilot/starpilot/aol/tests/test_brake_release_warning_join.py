from contextlib import ExitStack
from unittest import mock

import pytest

from opendbc.car.hyundai.tests.test_ioniq6_handoff import ioniq6_profiles
from openpilot.cereal import messaging, log
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture


@pytest.mark.parametrize('offset', (0, 4))
@pytest.mark.parametrize('case_name', ('release', 'expired_original', 'critical'))
def test_brake_release_before_warning_refresh_uses_only_original_brake_proof(offset, case_name):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    _, _, cp = ioniq6_profiles(alternate=True, bus_offset=offset)
    cp.safetyConfigs[-1].safetyParam |= 0x800
    sd, _, drive, card, receive = case._ioniq_availability_callers(full_op=False, cp=cp)
    for tick in (2, 3, 4):
      with mock.patch.object(type(sd.rk), 'lagging', new_callable=mock.PropertyMock, return_value=tick > 2):
        axis, _, command = drive(tick, brake=True, native_request_lat=tick == 2,
          native_request_long=False, allowed=tick == 2)
      assert receive(10_000_000_000 + tick * 10_000_000,
        axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'],
        state=fixture.car_state(brake=True))
    assert not axis.lateralActive and not command.latActive and not command.longActive
    proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
    now = 10_049_900_000
    if case_name == 'expired_original':
      now = 10_080_000_000
      fresh = proof.as_builder()
      fresh.logMonoTime = now
      fresh.aolAxisState.sequence += 1
      fresh.aolAxisState.sourceCarStateMonoTime = fresh.aolAxisState.observedMonoTime = now
      fresh.aolAxisState.validUntilMonoTime = now + 30_000_000
      proof = fresh.as_reader()
    elif case_name == 'critical':
      faults = Events()
      faults.add(log.OnroadEvent.EventName.controlsMismatch)
      event = messaging.new_message('onroadEvents', len(faults), valid=True, logMonoTime=now)
      event.onroadEvents = faults.to_msg()
      event = event.as_reader()
    held = receive(now, axis=proof, event=event, state=fixture.car_state(brake=False))
    assert held == (case_name == 'release')
    if case_name != 'release':
      return
    with mock.patch.object(type(sd.rk), 'lagging', new_callable=mock.PropertyMock, return_value=True):
      axis, _, command = drive(5, brake=False, native_request_lat=False, native_request_long=False, allowed=False)
    assert receive(10_050_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'],
      state=fixture.car_state(brake=False))
    assert not axis.lateralActive and not command.latActive
    axis, _, command = drive(6, native_request_lat=True, native_request_long=False, allowed=True)
    assert receive(10_060_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    assert axis.lateralActive and command.latActive
