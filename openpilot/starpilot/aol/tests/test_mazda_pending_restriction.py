from dataclasses import replace

import pytest

from openpilot.starpilot.aol.tests.test_mazda_eps_recovery import output, set_events, setup
from openpilot.starpilot.tests.test_ford_reverse_intent import NOW, PRODUCER, change, native_change, sample


@pytest.mark.parametrize('approach', ['cold_main', 'braked_stop', 'eps_reblock'])
@pytest.mark.parametrize('fault', [None, 'wrong_panda', 'explicit_disarm', 'expired', 'native_fault'])
def test_pending_restriction_keeps_only_existing_intent(approach, fault):
  cp, state, feedback = setup(acc=approach == 'eps_reblock')
  if approach == 'cold_main':
    # The idle proof before the first MAIN arm does not contain a lateral grant.
    idle, _ = sample(cp, NOW + 1, NOW + 1, 2, active=False, allowed=False, requested=False, event_stamp=NOW + 1)
    set_events(idle)
    assert not feedback.observe(idle, cp, state, NOW + 1, latched=False, intent_producer_session=PRODUCER)
    assert not feedback.lateral_only_baseline
  stamp = NOW + 10_000_000
  pending, native = sample(cp, stamp, stamp, 3, active=False, requested=approach == 'braked_stop', allowed=False, event_stamp=stamp)
  change(pending, 'aolAxisState', lambda axis: setattr(axis, 'desiredLateral', True))
  state.brakePressed = state.standstill = approach == 'braked_stop'
  state.gasPressed = not state.brakePressed
  state.steerFaultTemporary = True
  set_events(pending, {'name': 'pcmDisable', 'userDisable': True}) if not state.cruiseState.enabled else set_events(pending)
  if fault == 'wrong_panda':
    slot = replace(native.pandaInventory[0], hardwareSerial='other')
    native_change(pending, native, pandaSerial='other', pandaInventory=(slot,))
  elif fault == 'native_fault':
    native_change(pending, native, pandaInventory=(replace(native.pandaInventory[0], faults=1),))
  elif fault == 'explicit_disarm':
    set_events(pending, {'name': 'buttonCancel', 'userDisable': True})
  now = stamp + (31_000_000 if fault == 'expired' else 5_000_000)
  lost = feedback.observe(pending, cp, state, now, latched=True, intent_producer_session=PRODUCER)
  assert not output(state, native).lateral_active
  if fault is not None:
    assert getattr(feedback, 'mazda_restriction_native_ns', 0) == 0
    return
  assert not lost and not feedback.failed
  assert feedback.mazda_restriction_native_ns == native.observedMonoTime
  state.steerFaultTemporary = state.brakePressed = state.standstill = False
  recovery = stamp + 50_000_000
  waiting, native = sample(cp, recovery, recovery, 4, active=False, requested=True, allowed=False, event_stamp=recovery)
  change(waiting, 'aolAxisState', lambda axis: setattr(axis, 'desiredLateral', True))
  set_events(waiting)
  assert not feedback.observe(waiting, cp, state, recovery, latched=True, intent_producer_session=PRODUCER)
  assert feedback.mazda_restriction_native_ns == native.observedMonoTime
  assert feedback.mazda_restriction_ns == now
  assert not output(state, native).lateral_active
  active, grant = sample(cp, recovery + 10_000_000, recovery + 10_000_000, 5, event_stamp=recovery)
  set_events(active)
  assert not feedback.observe(active, cp, state, recovery + 10_000_000, latched=True, intent_producer_session=PRODUCER)
  assert output(state, grant).lateral_active
