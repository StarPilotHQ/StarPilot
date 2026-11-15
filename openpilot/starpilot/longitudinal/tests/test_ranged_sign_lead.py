from dataclasses import replace
import math
import pytest
from openpilot.starpilot.longitudinal.force_stop import ForceStop, StopFrame, vehicle_tune


def approach(**changes):
  frame = StopFrame(
    100_000_000_000,
    5.36,
    30.47,
    False,
    True,
    False,
    False,
    True,
    False,
    True,
    True,
    13.2,
    3.4,
    False,
    0.0,
    False,
    0.0,
    sign=True,
    vision_distance_m=12.3,
    acceleration_mps2=-1.77,
    actuator_delay_s=0.5,
    moving_preview=True,
    vision_source_ns=99_500_000_000,
  )
  return replace(frame, **changes)


def acquire(frame, *, tune=None, offset=0):
  owner = ForceStop()
  tune = vehicle_tune('HYUNDAI_IONIQ_6') if tune is None else tune
  plans = []
  for i in range(5):
    t = i * 0.05
    a = frame.acceleration_mps2 if isinstance(frame.acceleration_mps2, (int, float)) and math.isfinite(frame.acceleration_mps2) else 0.0
    sample = replace(
      frame,
      model_ns=frame.model_ns + i * 50_000_000,
      speed_mps=max(0.0, frame.speed_mps + a * t),
      lead_distance_m=frame.lead_distance_m + frame.lead_speed_mps * t - frame.speed_mps * t - 0.5 * a * t * t,
      vision_distance_m=max(0.0, frame.vision_distance_m - frame.speed_mps * t - 0.5 * a * t * t) if frame.vision_distance_m is not None else None,
    )
    plans.append(owner.step(sample, tune, offset))
  return owner, plans


def test_fresh_confirmed_ranged_sign_is_not_masked_by_rolling_lead():
  owner, plans = acquire(approach())
  assert plans[-1].forcing and owner.sign_confirmed
  assert 0 < plans[-1].tracked_distance_m <= 12.3
  assert not plans[0].forcing  # Original commitment dwell retained.


@pytest.mark.parametrize(
  'changes',
  [
    {'sign': False},
    {'vision_distance_m': None},
    {'dashboard_sign': True},
    {'acceleration_mps2': None},
    {'acceleration_mps2': math.nan},
    {'acceleration_mps2': 0.0},
    {'acceleration_mps2': 2.0},
    {'acceleration_mps2': -0.1},
    {'actuator_delay_s': None},
    {'vision_source_ns': 0},
    {'vision_source_ns': None},
    {'vision_source_ns': 99_000_000_000},
    {'vision_source_ns': 101_000_000_000},
    {'actuator_delay_s': 2.0},
    {'perception_available': False},
    {'driving_in_curve': True},
    {'horizon_m': 60.0},
    {'pedal_override': True},
    {'speed_mps': 8.74, 'vision_distance_m': 6.34, 'lead_speed_mps': 7.0},
  ],
)
def test_lead_exception_requires_complete_fresh_normal_stopping_proof(changes):
  owner, plans = acquire(approach(**changes))
  assert not any(p.forcing for p in plans)


def test_expired_sign_does_not_gain_commitment_from_cached_range():
  owner = ForceStop()
  tune = vehicle_tune('HYUNDAI_IONIQ_6')
  frame = approach()
  assert not owner.step(frame, tune).forcing
  for i in range(1, 21):
    plan = owner.step(replace(frame, model_ns=frame.model_ns + i * 50_000_000, sign=False, vision_distance_m=None), tune)
    assert not plan.forcing


def test_ranged_sign_remains_held_after_lead_departs_and_model_requests_go():
  owner, plans = acquire(approach())
  assert plans[-1].forcing
  frame = approach(
    model_ns=100_250_000_000,
    speed_mps=0.0,
    standstill=True,
    sign=False,
    vision_distance_m=None,
    lead_present=False,
    tracking_lead=False,
    horizon_m=80.0,
    raw_model_stopped=False,
  )
  plan = owner.step(frame, vehicle_tune('HYUNDAI_IONIQ_6'))
  assert plan.forcing and plan.manual_hold and plan.should_stop


def test_driver_override_clears_ranged_sign_hold():
  owner, plans = acquire(approach())
  assert plans[-1].forcing
  frame = approach(model_ns=100_250_000_000, speed_mps=0.0, standstill=True, sign=False, vision_distance_m=None, lead_present=False, tracking_lead=False)
  assert owner.step(frame, vehicle_tune('HYUNDAI_IONIQ_6')).manual_hold
  frame = replace(frame, model_ns=frame.model_ns + 50_000_000, pedal_override=True)
  plan = owner.step(frame, vehicle_tune('HYUNDAI_IONIQ_6'))
  assert not plan.forcing and not plan.manual_hold


@pytest.mark.parametrize('bias,offset', [(6.0, 0), (0.0, 1), (0.0, -1)])
def test_shifted_stop_coordinates_retain_original_lead_guard(bias, offset):
  owner = ForceStop()
  tune = replace(vehicle_tune('HYUNDAI_IONIQ_6'), distance_bias_m=bias)
  frame = approach()
  for i in range(5):
    plan = owner.step(replace(frame, model_ns=frame.model_ns + i * 50_000_000, vision_distance_m=12.3 if i == 0 else None), tune, offset)
    assert not plan.forcing


def test_repeated_poll_does_not_count_as_an_independent_camera_sample():
  owner, plans = acquire(approach())
  assert plans[-1].forcing and owner.vision_samples == 1


def test_new_confirmation_without_range_cannot_borrow_old_range():
  owner = ForceStop()
  tune = vehicle_tune('HYUNDAI_IONIQ_6')
  frame = approach()
  assert not owner.step(frame, tune).forcing
  for i in range(1, 6):
    plan = owner.step(replace(frame, model_ns=frame.model_ns + i * 50_000_000, vision_source_ns=99_700_000_000, vision_distance_m=None), tune)
    assert not plan.forcing
  assert owner.vision_samples == 1


def test_regressed_camera_source_cannot_build_commitment():
  owner = ForceStop()
  tune = vehicle_tune('HYUNDAI_IONIQ_6')
  frame = approach()
  assert not owner.step(frame, tune).forcing
  for i in range(1, 6):
    plan = owner.step(replace(frame, model_ns=frame.model_ns + i * 50_000_000, vision_source_ns=99_400_000_000), tune)
    assert not plan.forcing
  assert owner.vision_samples == 1


def test_larger_later_range_cannot_extend_a_committed_stop():
  owner, plans = acquire(approach())
  before = plans[-1]
  assert before.forcing and not before.should_stop
  frame = approach(model_ns=100_250_000_000, speed_mps=4.9175, vision_source_ns=99_750_000_000, vision_distance_m=25.0)
  after = owner.step(frame, vehicle_tune('HYUNDAI_IONIQ_6'))
  assert after.forcing and after.episode_ns == before.episode_ns
  assert after.tracked_distance_m <= before.tracked_distance_m - frame.speed_mps * 0.05 + 1e-8


def test_green_light_with_departing_lead_does_not_gain_sign_retention():
  owner = ForceStop()
  tune = vehicle_tune('HYUNDAI_IONIQ_6')
  frame = approach(light=True, sign=False, vision_distance_m=None, vision_source_ns=0, tracking_lead=False, lead_present=False)
  for i in range(12):
    plan = owner.step(replace(frame, model_ns=100_000_000_000 + i * 50_000_000), tune)
  assert plan.forcing and not owner.ranged_stop
  frame = replace(frame, light=False, raw_model_stopped=False, model_should_stop=False, horizon_m=80.0, tracking_lead=True, lead_present=True)
  for i in range(12, 27):
    plan = owner.step(replace(frame, model_ns=100_000_000_000 + i * 50_000_000), tune)
  assert not plan.forcing and not owner.ranged_stop


@pytest.mark.parametrize('speed', [0.0, 5.0])
def test_turning_lead_cannot_clear_a_red_light_prediction_for_straight_ego(speed):
  from openpilot.starpilot.conditional_mode.stop import StopLightDetector, StopFrame as LightFrame, StopLead

  detector = StopLightDetector()
  detector.committed = True
  detector.standstill_committed = speed == 0.0
  detector.commit_distance_m = 0.0
  detector.standstill_reason = 'light'
  detector.standstill_model_stopped = True
  for i in range(21):
    t = 100.0 + i * 0.05
    frame = LightFrame(
      observed_mono_s=t,
      now_mono_s=t,
      speed_mps=speed,
      model_horizon_m=70.0,
      model_stop_time_s=7.7,
      traffic_mode=False,
      stop_sign_confirmed=False,
      forcing_stop=False,
      lead=StopLead(True, 8.0, 3.4, True, 1.0, True) if i < 4 else StopLead(False),
      standstill=speed == 0.0,
      left_blinker=False,
      right_blinker=False,
      steering_angle_deg=0.0,
      driving_in_curve=False,
      car_fingerprint='HYUNDAI_IONIQ_6',
      dashboard_stop_sign=False,
      pedal_override=False,
      model_tick_mono_s=t,
      model_should_stop=True,
      standstill_path_extent_m=70.0,
    )
    observation = detector.step(frame)
    assert detector.committed and observation.light_detected
    assert observation.standstill_hold == (speed == 0.0)


def test_clear_light_prediction_can_release_with_a_departing_lead():
  from openpilot.starpilot.conditional_mode.stop import StopLightDetector, StopFrame as LightFrame, StopLead

  detector = StopLightDetector()
  detector.committed = detector.standstill_committed = True
  detector.commit_distance_m = 0.0
  detector.standstill_reason = 'light'
  detector.standstill_model_stopped = True
  for i in range(21):
    t = 100.0 + i * 0.05
    frame = LightFrame(
      observed_mono_s=t,
      now_mono_s=t,
      speed_mps=0.0,
      model_horizon_m=70.0,
      model_stop_time_s=7.7,
      traffic_mode=False,
      stop_sign_confirmed=False,
      forcing_stop=False,
      lead=StopLead(True, 8.0, 3.4, True, 1.0, True),
      standstill=True,
      left_blinker=False,
      right_blinker=False,
      steering_angle_deg=0.0,
      driving_in_curve=False,
      car_fingerprint='HYUNDAI_IONIQ_6',
      dashboard_stop_sign=False,
      pedal_override=False,
      model_tick_mono_s=t,
      model_should_stop=False,
      standstill_path_extent_m=70.0,
    )
    observation = detector.step(frame)
  assert not detector.committed and not observation.standstill_hold
