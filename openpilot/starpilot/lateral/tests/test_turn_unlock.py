from types import SimpleNamespace

from openpilot.starpilot.lateral.turn_unlock import TurnUnlock, UNLOCK_SPEED


def test_first_valid_sample_unlocks_and_subsequent_speed_does_not_revoke():
  turn = TurnUnlock()
  cs = SimpleNamespace(leftBlinker=True, rightBlinker=False, vEgo=UNLOCK_SPEED + .1)
  assert not turn.sample(cs, valid=True)
  cs.vEgo = UNLOCK_SPEED
  assert not turn.sample(cs, valid=False)
  assert turn.sample(cs, valid=True)
  assert not turn.driver_initiated
  cs.vEgo *= 2
  assert turn.sample(cs, valid=True, driver_confirmed=True)
  assert turn.driver_initiated
  assert not turn.sample(cs, valid=False)
  assert turn.sample(cs, valid=True)


def test_cancel_direction_hazards_and_completion_reset_independently():
  turn = TurnUnlock()
  cs = SimpleNamespace(leftBlinker=True, rightBlinker=False, vEgo=0.)
  assert turn.sample(cs, valid=True, driver_confirmed=True)
  assert not turn.sample(cs, valid=True, ended=True)
  assert not turn.sample(cs, valid=True)
  cs.leftBlinker, cs.rightBlinker, cs.vEgo = False, True, UNLOCK_SPEED + .1
  assert not turn.sample(cs, valid=True)
  assert not turn.driver_initiated
  cs.vEgo = 0.
  assert turn.sample(cs, valid=True)
  cs.leftBlinker = True
  assert not turn.sample(cs, valid=True)
  cs.leftBlinker, cs.rightBlinker = False, False
  assert not turn.sample(cs, valid=True)
  assert not turn.unlocked
  turn.reset()
  assert not turn.ended


def test_unlock_is_not_steering_authority_and_stop_guards_override_opt_in():
  turn = TurnUnlock()
  cs = SimpleNamespace(leftBlinker=False, rightBlinker=True, vEgo=0.)
  assert turn.sample(cs, valid=True)
  base = {'unlocked': True, 'automatic': False, 'lateral_ready': True, 'stopping': False, 'driver_braking': False, 'blindspot': False}
  assert not turn.permits(**base)
  assert turn.permits(**(base | {'automatic': True}))
  for flag in ('stopping', 'driver_braking', 'blindspot'):
    assert not turn.permits(**(base | {'automatic': True, flag: True}))
  assert not turn.permits(**(base | {'automatic': True, 'lateral_ready': False}))
  assert not turn.permits(**(base | {'automatic': True, 'unlocked': False}))
  assert turn.sample(cs, valid=True, driver_confirmed=True)
  assert turn.permits(**base)


def test_completed_sweep_cannot_rearm_on_same_signal():
  turn = TurnUnlock()
  cs = SimpleNamespace(leftBlinker=False, rightBlinker=True, vEgo=0.)
  assert turn.sample(cs, valid=True)
  cs.vEgo = 2.
  for _ in range(1000):
    assert turn.sample(cs, valid=True, curvature=.09)
  assert not turn.sample(cs, valid=True, curvature=0.)
  cs.vEgo = 0.
  assert not turn.sample(cs, valid=True)



def test_lateral_withdrawal_does_not_carry_driver_confirmation():
  turn = TurnUnlock()
  cs = SimpleNamespace(leftBlinker=True, rightBlinker=False, vEgo=0.)
  assert turn.sample(cs, valid=True, driver_confirmed=True)
  assert turn.driver_initiated
  assert turn.sample(cs, valid=True, lateral_active=False, driver_confirmed=True)
  assert not turn.driver_initiated
  cs.vEgo = 1.5
  assert turn.sample(cs, valid=True)
  assert not turn.driver_initiated
