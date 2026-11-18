from collections import deque
from contextlib import ExitStack
from unittest import mock

import pytest

from opendbc.car.gm.tests.test_ordinary_cc import malibu_hybrid_params
from opendbc.car.structs import car
from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import BASE, intent
from openpilot.starpilot.aol.tests.test_event_input_reconciliation import changed_intent
from openpilot.starpilot.aol.tests.test_gm_source_deadline import (
  test_volt_cutoff_keeps_only_original_lease_then_consumes_next_source as check_original_deadline,
)


def message(stamp, state, *, valid=True):
  sample = messaging.new_message('carState', valid=valid, logMonoTime=stamp)
  sample.carState = state
  return sample.as_reader()


@pytest.mark.parametrize('timeout', [False, True])
@pytest.mark.parametrize('alternate', [False, True])
@pytest.mark.parametrize('removed', [False, True])
@pytest.mark.parametrize('arrival', ['car_event', 'intent_drain'])
@pytest.mark.parametrize('pulse', ['healthy', 'manual_cancel', 'brake_then_clear', 'regen_then_clear',
                                  'gas_then_clear', 'gas_held', 'main_off_then_clear', 'permanent_eps',
                                  'invalid_then_valid', 'intent_pause_then_clear', 'missing_intent', 'native_fault'])
def test_hybrid_consumes_late_state_without_dropping_restrictions(timeout, alternate, removed, arrival, pulse):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, controls, step = case._authority_callers(True, cp=malibu_hybrid_params(pedal=True, removed=removed, alternate=alternate), genuine_events=True)
    first, _, command = step(2, offset_ns=5_231_510, state_stamp=BASE, intent_message=intent(BASE, 3))
    assert first.lateralActive and first.longitudinalActive and command.latActive and command.longActive
    # Route0a: the old source is32.515ms old when the successor is3.049ms old.
    if pulse == 'gas_then_clear':
      sd.disengage_on_accelerator = True
    fresh = BASE + 29_466_406
    start_age = 32_514_948 if timeout else 27_000_000
    now = [BASE + start_age]
    queue, wires, waiting_states = deque(), deque(), []
    original_receive = messaging.recv_one_or_none
    original_car_update = sd._update_car_event_sample
    injected = False

    def receive(sock):
      if sock is sd.car_state_sock:
        return queue.popleft() if queue else None
      if sock is sd.aol_intent_companion.sock:
        if arrival == 'intent_drain' and waiting_states:
          queue.extend(waiting_states)
          waiting_states.clear()
        return wires.popleft() if wires else None
      return original_receive(sock)

    def car_update(sample, previous):
      nonlocal injected
      result = original_car_update(sample, previous)
      if injected:
        return result
      injected = True
      late = fixture.car_state()
      late.cruiseState.available = True
      late.vEgo = 11.204765
      if pulse == 'manual_cancel':
        # Hybrid pedal CarState preserves the physical OEM Cancel as unknown.
        late.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.unknown, pressed=True)]
      elif pulse == 'brake_then_clear':
        late.brakePressed = True
      elif pulse == 'regen_then_clear':
        late.regenBraking = True
      elif pulse in ('gas_then_clear', 'gas_held'):
        late.gasPressed = True
      elif pulse == 'main_off_then_clear':
        late.cruiseState.available = False
      elif pulse == 'permanent_eps':
        late.steerFaultPermanent = True
      waiting_states.append(message(fresh, late, valid=pulse != 'invalid_then_valid'))
      if pulse != 'missing_intent':
        wires.append(changed_intent(fresh, 4, **({'pauseLateral': True, 'pauseLongitudinal': True}
                                               if pulse == 'intent_pause_then_clear' else {})).as_reader())
      if pulse.endswith(('clear', 'valid', 'on')):
        cleared = fixture.car_state()
        cleared.cruiseState.available = True
        cleared.vEgo = 11.204765
        waiting_states.append(message(fresh + 100_000, cleared))
        wires.append(intent(fresh + 100_000, 5).as_reader())
      if arrival == 'car_event':
        queue.extend(waiting_states)
        waiting_states.clear()
      now[0] = BASE + 32_514_948
      return result

    original_control_receive = controls.sm.update_msgs

    def control_with_current_events(timestamp, messages):
      if any(sample.which() == 'carState' for sample in messages):
        messages = [sample for sample in messages if sample.which() != 'onroadEvents'] + [sd.pm.messages['onroadEvents']]
      return original_control_receive(timestamp, messages)

    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', side_effect=lambda: now[0]), \
         mock.patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one_or_none', side_effect=receive), \
         mock.patch.object(sd, '_update_car_event_sample', side_effect=car_update) as car_events, \
         mock.patch.object(sd.state_machine, 'update', wraps=sd.state_machine.update) as transition, \
         mock.patch.object(controls.sm, 'update_msgs', side_effect=control_with_current_events):
      axis, _, command = step(4, timeout=timeout, offset_ns=start_age - 20_000_000, state_stamp=BASE,
                              intent_message=intent(BASE, 3), panda_fault=pulse == 'native_fault')
    assert not queue and not waiting_states, (axis.sourceCarStateMonoTime, str(axis.faultReason),
                                              str(sd.pm.messages['selfdriveState'].selfdriveState.alertType))
    assert transition.call_count == 1
    assert car_events.call_count == (3 if pulse.endswith(('clear', 'valid', 'on')) else 2)
    if pulse in ('healthy', 'manual_cancel'):
      assert axis.sourceCarStateMonoTime == fresh
      assert sd.aol_last_intent.carStateLogMonoTime == fresh
      assert axis.lateralActive and axis.longitudinalActive and command.latActive and command.longActive
      assert sd.enabled and not sd.aol_authority_lost
      assert log.OnroadEvent.EventName.commIssue not in sd.events.names
    elif pulse == 'gas_held':
      assert axis.lateralActive and command.latActive and not command.longActive
      assert sd.events.contains(fixture.ET.OVERRIDE_LONGITUDINAL) and sd.enabled
    else:
      assert not axis.longitudinalActive and not command.longActive
      if pulse in ('brake_then_clear', 'regen_then_clear', 'gas_then_clear', 'gas_held'):
        assert axis.lateralActive and command.latActive
      else:
        assert not axis.lateralActive and not command.latActive
      if pulse in ('brake_then_clear', 'regen_then_clear', 'gas_then_clear'):
        assert log.OnroadEvent.EventName.pedalPressed in sd.events.names
        assert not sd.enabled
      if pulse == 'invalid_then_valid':
        assert axis.sourceCarStateMonoTime == 0 and not sd.conditional_car_state_valid


@pytest.mark.parametrize('removed', [False, True])
@pytest.mark.parametrize('alternate', [False, True])
@pytest.mark.parametrize('mode', ['ordinary', 'brake', 'regen'])
@pytest.mark.parametrize('successor', ['fresh', 'missing'])
def test_hybrid_never_extends_the_original_output_deadline(removed, alternate, mode, successor):
  cp = malibu_hybrid_params(pedal=True, removed=removed, alternate=alternate)
  check_original_deadline(mode, successor, cp)
