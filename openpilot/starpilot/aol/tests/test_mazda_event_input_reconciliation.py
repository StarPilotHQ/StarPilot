from collections import deque
from contextlib import ExitStack
from unittest import mock

import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.mazda.interface import CarInterface
from opendbc.car.mazda.values import CAR
from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_carstate_queue import message
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import BASE, intent


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
@pytest.mark.parametrize('pulse', ('healthy', 'cancel', 'permanent_eps', 'invalid_can'))
def test_mazda_consumes_input_arriving_during_car_event_work(identity, pulse):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
    sd, _, drive = case._authority_callers(True, cp=cp, genuine_events=True)
    state = fixture.car_state()
    state.cruiseState.available = state.cruiseState.enabled = True
    options = {'observed_state': state, 'native_request_long': False, 'long_allowed': False, 'panda_controls_allowed': True}
    drive(0, **options)
    old, _, command = drive(2, state_stamp=BASE, offset_ns=4_237_448, intent_message=intent(BASE, 3), **options)
    assert old.lateralActive and command.latActive and not command.longActive
    now = [BASE + 27_000_000]
    queue, wires = deque(), deque()
    original_receive, original_update = messaging.recv_one_or_none, sd._update_car_event_sample
    visited = []

    def receive(sock):
      if sock is sd.car_state_sock:
        return queue.popleft() if queue else None
      if sock is sd.aol_intent_companion.sock:
        return wires.popleft() if wires else None
      return original_receive(sock)

    def update(sample, previous):
      result = original_update(sample, previous)
      visited.append(float(sample.vEgo))
      if len(visited) == 1:
        late = state.as_reader().as_builder()
        late.vEgo = 25.0
        if pulse == 'cancel':
          late.buttonEvents = [{'type': 'cancel', 'pressed': True}]
        elif pulse == 'permanent_eps':
          late.steerFaultPermanent = True
        stamp = BASE + 35_000_000
        queue.append(message(stamp, late, valid=pulse != 'invalid_can'))
        wires.append(intent(stamp, 4).as_reader())
        now[0] = BASE + 38_846_614
      return result

    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', side_effect=lambda: now[0]), \
         mock.patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one_or_none', side_effect=receive), \
         mock.patch.object(sd, '_update_car_event_sample', side_effect=update):
      axis, _, command = drive(4, state_stamp=BASE, offset_ns=7_000_000, intent_message=intent(BASE, 3), **options)
    assert not queue and visited == [20.0, 25.0]
    assert not command.longActive
    if pulse == 'healthy':
      assert axis.sourceCarStateMonoTime == BASE + 35_000_000
      assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
      assert not sd.aol_authority_lost
    elif pulse == 'cancel':
      assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
      assert not sd.aol_authority_lost
      assert log.OnroadEvent.EventName.buttonCancel in sd.events.names
      assert not sd.enabled
    elif pulse == 'permanent_eps':
      assert not command.latActive
    else:
      assert axis.sourceCarStateMonoTime == 0 and not command.latActive

    if pulse in ('healthy', 'cancel'):
      fresh = BASE + 45_000_000
      axis, _, command = drive(6, state_stamp=fresh, offset_ns=10_000_000, intent_message=intent(fresh, 5), **options)
      assert axis.sourceCarStateMonoTime == fresh and command.latActive
      assert not command.longActive and not sd.aol_authority_lost
