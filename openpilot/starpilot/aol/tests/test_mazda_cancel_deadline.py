from contextlib import ExitStack
from unittest import mock

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.mazda.interface import CarInterface
from opendbc.car.mazda.values import CAR
from openpilot.cereal import log
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import BASE, intent


@pytest.mark.parametrize('profile', [CAR.MAZDA_CX5, CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021])
@pytest.mark.parametrize('pulse', ['physical_cancel', 'permanent_eps', 'native_denied', 'driver_disarm', 'native_reordered'])
def test_mazda_cancel_after_previous_axis_deadline(profile, pulse):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    cp = CarInterface.get_params(profile, gen_empty_fingerprint(), [], False, False, False)
    sd, _, drive = case._authority_callers(True, cp=cp, genuine_events=True)
    sd.aol_settings = read_settings(sd.params)
    state = fixture.car_state()
    state.cruiseState.available = state.cruiseState.enabled = True
    options = {'observed_state': state, 'native_request_long': False, 'long_allowed': False, 'panda_controls_allowed': True}
    drive(0, **options)
    prior, _, command = drive(2, state_stamp=BASE, offset_ns=6_066_613, intent_message=intent(BASE, 3), **options)
    assert prior.lateralActive and command.latActive and not command.longActive
    assert prior.validUntilMonoTime == BASE + 36_066_613

    cancel_ns = BASE + 11_869_375
    now = [BASE + 18_554_693]
    state.buttonEvents = [{'type': 'cancel', 'pressed': True}]
    if pulse == 'permanent_eps':
      state.steerFaultPermanent = True
    if pulse == 'native_denied':
      options['allowed'] = False
    publish = sd.publish_axis_proof

    def publish_after_deadline(*args, **kwargs):
      now[0] = prior.validUntilMonoTime + 320_000
      return publish(*args, **kwargs)

    offset_ns = -21_445_307 if pulse == 'native_reordered' else -1_445_307
    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', side_effect=lambda: now[0]), \
         mock.patch.object(sd, 'publish_axis_proof', side_effect=publish_after_deadline) as published:
      axis, _, command = drive(4, state_stamp=cancel_ns, offset_ns=offset_ns,
                                intent_message=intent(cancel_ns, 4, latched=pulse != 'driver_disarm'), **options)
    assert published.called
    assert not command.longActive and not sd.enabled
    if pulse == 'physical_cancel':
      assert log.OnroadEvent.EventName.buttonCancel in sd.events.names
      assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
      assert not sd.aol_authority_lost
      assert axis.sourceCarStateMonoTime == cancel_ns and axis.lateralActive and command.latActive
    else:
      assert not axis.lateralActive and not command.latActive
