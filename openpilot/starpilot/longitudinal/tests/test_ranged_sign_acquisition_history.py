from dataclasses import replace
import pytest
from openpilot.starpilot.longitudinal.force_stop import ForceStop, vehicle_tune
from openpilot.starpilot.longitudinal.tests.test_ranged_sign_lead import approach


def easing_stop():
  owner = ForceStop()
  tune = vehicle_tune('HYUNDAI_IONIQ_6')
  speed, distance = 3.67, 8.3
  for tick, accel in enumerate([-1.6, -1.4, -1.2, -1.0, -.9]):
    sample = approach(model_ns=100_000_000_000 + tick * 50_000_000,
                      vision_source_ns=99_500_000_000, speed_mps=speed,
                      acceleration_ns=100_000_000_000 + tick * 50_000_000,
                      sample_ns=100_001_000_000 + tick * 50_000_000,
                      vision_distance_m=distance, acceleration_mps2=accel)
    plan = owner.step(sample, tune)
    speed += accel * .05
    distance -= speed * .05
  return owner, plan, sample


def test_easing_brake_retains_valid_stop_before_lead_departs():
  owner, plan, sample = easing_stop()
  assert plan.forcing and owner.sign_confirmed
  assert plan.obstacle_m is not None and plan.obstacle_m < sample.vision_distance_m
  sample = replace(sample, model_ns=sample.model_ns + 50_000_000,
                   sign=False, vision_distance_m=None, tracking_lead=False,
                   lead_present=False, horizon_m=80., raw_model_stopped=False)
  plan = owner.step(sample, vehicle_tune('HYUNDAI_IONIQ_6'))
  assert plan.forcing
  sample = replace(sample, model_ns=sample.model_ns + 50_000_000,
                   speed_mps=0., standstill=True)
  plan = owner.step(sample, vehicle_tune('HYUNDAI_IONIQ_6'))
  assert plan.forcing and plan.manual_hold and plan.should_stop


def test_old_braking_cannot_authorize_a_new_stop():
  owner = ForceStop()
  tune = vehicle_tune('HYUNDAI_IONIQ_6')
  sample = approach(sign=False, vision_distance_m=None, acceleration_ns=100_000_000_000, sample_ns=100_001_000_000)
  owner.step(sample, tune)
  for tick in range(1, 11):
    plan = owner.step(replace(sample, model_ns=sample.model_ns + tick * 50_000_000,
                              acceleration_ns=sample.model_ns + tick * 50_000_000,
                              sample_ns=sample.model_ns + tick * 50_000_000 + 1_000_000,
                              acceleration_mps2=-.1, sign=tick>=4,
                              vision_distance_m=12.3 if tick>=4 else None), tune)
    assert not plan.forcing


@pytest.mark.parametrize('veto', ['perception', 'pedal', 'resume', 'standstill', 'curve'])
def test_braking_history_is_cleared_by_loss_or_override(veto):
  owner = ForceStop()
  tune = vehicle_tune('HYUNDAI_IONIQ_6')
  sample = approach(sign=False, vision_distance_m=None, acceleration_ns=100_000_000_000, sample_ns=100_001_000_000)
  owner.step(sample, tune)
  changes = {'perception': {'perception_available': False},
             'pedal': {'pedal_override': True},
             'resume': {'resume_requested': True},
             'standstill': {'standstill': True, 'speed_mps': 0.},
             'curve': {'driving_in_curve': True}}[veto]
  owner.step(replace(sample, model_ns=sample.model_ns + 50_000_000,
                     acceleration_ns=sample.model_ns + 50_000_000,
                     sample_ns=sample.model_ns + 51_000_000, **changes), tune)
  assert not owner.braking_samples


def test_clock_discontinuity_cannot_reuse_braking():
  owner = ForceStop()
  tune = vehicle_tune('HYUNDAI_IONIQ_6')
  sample = approach(sign=False, vision_distance_m=None, acceleration_ns=100_000_000_000, sample_ns=100_001_000_000)
  owner.step(sample, tune)
  owner.step(replace(sample, model_ns=sample.model_ns + 300_000_000,
                     acceleration_ns=sample.model_ns + 300_000_000,
                     sample_ns=sample.model_ns + 301_000_000), tune)
  assert not owner.braking_samples


@pytest.mark.parametrize('kind', ['repeated', 'future', 'changed'])
def test_bad_acceleration_source_cannot_refresh_braking_history(kind):
  owner = ForceStop()
  tune = vehicle_tune('HYUNDAI_IONIQ_6')
  first = approach(sign=False, vision_distance_m=None, acceleration_ns=100_000_000_000, sample_ns=100_001_000_000)
  owner.step(first, tune)
  assert owner.braking_samples
  for tick in range(1, 11):
    now = first.model_ns + tick * 50_000_000
    source = now + 100_000_000 if kind == 'future' else first.acceleration_ns
    sample = replace(first, model_ns=now, sample_ns=now+1_000_000, acceleration_ns=source,
                     acceleration_mps2=-.1, sign=True, vision_distance_m=12.3)
    if kind == 'repeated':
      sample = replace(sample, acceleration_mps2=first.acceleration_mps2, sign=False, vision_distance_m=None)
    assert not owner.step(sample, tune).forcing
  assert not owner.braking_samples
