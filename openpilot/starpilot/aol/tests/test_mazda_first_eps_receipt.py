from dataclasses import replace

import pytest

from openpilot.starpilot.aol.tests.test_mazda_eps_recovery import output, set_events, setup
from openpilot.starpilot.tests.test_ford_reverse_intent import NOW, PRODUCER, change, native_change, sample


@pytest.mark.parametrize('stop', [False, True])
@pytest.mark.parametrize('fault', [None, 'panda', 'stale', 'permanent', 'main_off'])
def test_first_eps_frame_with_previous_active_receipt(stop, fault):
  cp, cs, feedback = setup()
  cs.steerFaultTemporary = True
  cs.standstill = cs.brakePressed = stop
  cs.gasPressed = not stop
  cs.vEgo = 0.02 if stop else 6.
  now = NOW + 10_000_000
  sm, native = sample(cp, NOW, NOW, 1)
  # Event receipt can predate Card's new physical sample.
  set_events(sm, {'name': 'pcmDisable', 'userDisable': True})
  if fault == 'panda':
    native = native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0], faults=1),))
  elif fault == 'stale':
    now = NOW + 31_000_000
  elif fault == 'permanent':
    cs.steerFaultPermanent = True
  elif fault == 'main_off':
    cs.cruiseState.available = False
  lost = feedback.observe(sm, cp, cs, now, latched=True, intent_producer_session=PRODUCER)
  assert not output(cs, native).lateral_active
  if fault:
    assert getattr(feedback, 'mazda_restriction_native_ns', 0) == 0
    return
  assert not lost and feedback.mazda_restriction_ns == now
  cs.steerFaultTemporary = cs.standstill = cs.brakePressed = False
  waiting, denial = sample(cp, now + 10_000_000, now + 10_000_000, 2, active=False, allowed=False, event_stamp=now + 10_000_000)
  change(waiting, 'aolAxisState', lambda axis: setattr(axis, 'desiredLateral', True))
  set_events(waiting)
  assert not feedback.observe(waiting, cp, cs, now + 10_000_000, latched=True, intent_producer_session=PRODUCER)
  assert feedback.mazda_restriction_ns == now
  assert not output(cs, denial).lateral_active
  resumed, grant = sample(cp, now + 100_000_000, now + 100_000_000, 3, event_stamp=now + 10_000_000)
  set_events(resumed)
  assert not feedback.observe(resumed, cp, cs, now + 100_000_000, latched=True, intent_producer_session=PRODUCER)
  assert output(cs, grant).lateral_active
