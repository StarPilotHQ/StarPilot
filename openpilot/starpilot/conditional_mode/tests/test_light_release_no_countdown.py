from dataclasses import replace

import pytest
from opendbc.car import structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from openpilot.selfdrive.controls.lib.longcontrol import LongControl, LongCtrlState
from openpilot.starpilot.conditional_mode.stop import StopFrame, StopLead, StopLightDetector


def held_light():
  detector = StopLightDetector()
  detector.committed = detector.standstill_committed = True
  detector.standstill_reason = 'light'
  frame = StopFrame(100., 100., 0., 80., 7.7, False, False, False, StopLead(False), True,
                    False, False, 0., False, 'HYUNDAI_IONIQ_6', dashboard_stop_sign=False,
                    pedal_override=False, model_tick_mono_s=100., model_should_stop=False,
                    stationary_launch_clear=True)
  return detector, frame


def test_qualified_stationary_light_does_not_add_a_countdown():
  detector, frame = held_light()
  detector._commit(frame, 80., False, False, .05)
  assert not detector.committed or detector.standstill_committed
  assert not detector.committed and not detector.standstill_committed


@pytest.mark.parametrize('changes', [
  {'model_should_stop': True}, {'forcing_stop': True}, {'stationary_launch_clear': False},
  {'stop_sign_confirmed': True}, {'dashboard_stop_sign': True},
  {'lead': StopLead(True)}, {'lead': None}, {'standstill': False},
])
def test_shortcut_requires_the_existing_clear_light_proof(changes):
  detector, frame = held_light()
  detector._commit(replace(frame, **changes), 80., False, False, .05)
  assert detector.committed


@pytest.mark.parametrize('reason', ['sign', None])
def test_nonlight_hold_does_not_receive_the_light_shortcut(reason):
  detector, frame = held_light()
  detector.standstill_reason = reason
  detector._commit(frame, 80., False, False, .05)
  assert detector.committed


def test_unknown_or_short_geometry_cannot_clear_the_hold():
  for horizon in (None, 2.):
    detector, frame = held_light()
    detector._commit(frame, horizon, None if horizon is None else True, False, .05)
    assert detector.committed


def test_native_stop_request_still_overrides_a_positive_target():
  cp = CarInterface.get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
  cp.openpilotLongitudinalControl = True
  car = structs.CarState.new_message(vEgo=0., aEgo=0., canValid=True)
  control = LongControl(cp)
  control.long_control_state = LongCtrlState.stopping
  assert control.update(True, car, .35, False, (-3.5, 2.)) > 0.
  assert control.update(True, car, .35, True, (-3.5, 2.)) <= 0.


def test_retained_renewed_stop_sequence_matches_native_handback():
  cp = CarInterface.get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
  cp.openpilotLongitudinalControl = True
  car = structs.CarState.new_message(vEgo=0., aEgo=0., canValid=True)
  native, candidate = LongControl(cp), LongControl(cp)
  native.long_control_state = candidate.long_control_state = LongCtrlState.stopping
  detector, frame = held_light()
  for i in range(45):
    stop = i >= 22
    target = .1 if i < 20 else .35
    current = replace(frame, now_mono_s=100. + i * .05, model_should_stop=stop,
                      stationary_launch_clear=i >= 20 and not stop)
    detector._commit(current, 80. if i >= 20 else None, False if i >= 20 else None, False, .05)
    native_stop = stop or i < 20
    candidate_stop = native_stop or detector.standstill_committed
    for _ in range(5):
      baseline = native.update(True, car, target, native_stop, (-3.5, 2.))
      actual = candidate.update(True, car, target, candidate_stop, (-3.5, 2.))
      assert actual == pytest.approx(baseline)
      if stop:
        assert actual <= 0.


def test_released_light_honors_a_renewed_stop_without_new_geometry():
  detector, frame = held_light()
  detector._commit(frame, 80., False, False, .05)
  assert detector.released_standstill_light and not detector.committed
  renewed = replace(frame, now_mono_s=100.1, model_should_stop=True)
  detector._commit(renewed, None, None, False, .05)
  assert detector.committed and detector.standstill_committed


def test_unknown_source_retains_only_the_prior_light_episode():
  detector, frame = held_light()
  detector._commit(frame, 80., False, False, .05)
  detector.invalidate()
  assert detector.released_standstill_light and not detector.committed
  detector._commit(replace(frame, now_mono_s=100.1, model_should_stop=True), None, None, False, .05)
  assert detector.committed


@pytest.mark.parametrize('change', [{'standstill': False}, {'pedal_override': True}])
def test_motion_or_driver_override_ends_the_released_light_episode(change):
  detector, frame = held_light()
  detector._commit(frame, 80., False, False, .05)
  detector._commit(replace(frame, now_mono_s=100.1, **change), None, None, False, .05)
  assert not detector.released_standstill_light


def test_stop_bit_alone_cannot_acquire_a_new_light():
  detector, frame = held_light()
  detector.reset()
  detector._commit(replace(frame, model_should_stop=True), None, None, False, .05)
  assert not detector.committed
