"""Accepted stop engagement survives held brake without requesting acceleration."""

import pytest
from openpilot.starpilot.car.toyota.tests.test_gas_override_cancel import PROFILES, setup, acc_frames


@pytest.mark.parametrize('profile', PROFILES)
@pytest.mark.parametrize('long_requested', (False, True))
def test_held_brake_at_stop_is_neutral_without_cancel(profile, long_requested):
  cp, controller, command, state = setup(profile)
  state.out.gasPressed = False
  state.out.brakePressed = state.out.standstill = state.out.cruiseState.standstill = True
  state.out.vEgo = state.out.vEgoRaw = 0.
  state.pcm_acc_status = 7
  command.longActive = long_requested
  command.cruiseControl.resume = True
  controller.prev_accel = .9
  frames = acc_frames(cp, controller, command, state)
  assert frames and all(frame['ACCEL_CMD'] == 0 for frame in frames)
  assert all(not frame['CANCEL_REQ'] for frame in frames), 'BRAKE_STOP_INVENTED_CRUISE_CANCEL'
  assert all(not frame['RELEASE_STANDSTILL'] for frame in frames)
  assert controller.prev_accel == 0.


@pytest.mark.parametrize('profile', PROFILES)
def test_stop_brake_does_not_mask_explicit_cancel_or_source_loss(profile):
  for fault in ('cancel', 'source', 'moving'):
    cp, controller, command, state = setup(profile)
    state.out.gasPressed = False
    state.out.brakePressed = state.out.standstill = True
    state.out.vEgo = state.out.vEgoRaw = 0.
    if fault == 'cancel':
      command.cruiseControl.cancel = True
    elif fault == 'source':
      state.out.canValid = False
    else:
      state.out.standstill = False
      state.out.vEgo = state.out.vEgoRaw = 1.
    frames = acc_frames(cp, controller, command, state)
    assert frames and all(frame['CANCEL_REQ'] and frame['ACCEL_CMD'] == 0 for frame in frames)


@pytest.mark.parametrize('profile', PROFILES)
def test_stock_stop_brake_cannot_create_longitudinal_output(profile):
  cp, controller, command, state = setup(profile, stock=True)
  state.out.gasPressed = False
  state.out.brakePressed = state.out.standstill = True
  state.out.vEgo = state.out.vEgoRaw = 0.
  command.longActive = True
  assert not acc_frames(cp, controller, command, state)
