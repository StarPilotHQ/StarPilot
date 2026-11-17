from types import SimpleNamespace as NS
from dataclasses import replace
import math
import pytest
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.starpilot.model_geometry import standstill_clear_horizon
from openpilot.starpilot.conditional_mode.projection import RawLead, _horizon, _stationary_launch_clear
from openpilot.starpilot.conditional_mode.stop import StopFrame, StopLightDetector


def launch():
  times = list(map(float, ModelConstants.T_IDXS))
  x = [0.0, -0.0005, -0.0014, -0.004, -0.0045, -0.0094, -0.012, -0.006] + [70.0 * (i - 7) / 25 for i in range(8, 33)]
  velocity = [-0.03] * 7 + [0.1] + [0.4 + i * 0.25 for i in range(25)]
  return NS(position=NS(x=x, y=[0.0] * 33, t=times), velocity=NS(x=velocity, t=times), action=NS(shouldStop=False, desiredAcceleration=0.8))


def test_coherent_stationary_launch_prefix_uses_existing_velocity_noise_budget():
  model = launch()
  assert _horizon(model) is None  # Spatial acquisition stays strict.
  assert standstill_clear_horizon(model) == 70.0
  car = NS(standstill=True, vEgo=0.0, vEgoRaw=0.0)
  assert _stationary_launch_clear(model, car, RawLead(False, False, None, None, None, None), standstill_clear_horizon(model))
  assert not _stationary_launch_clear(model, car, RawLead(True, False, None, None, None, None), standstill_clear_horizon(model))
  car.standstill = False
  assert not _stationary_launch_clear(model, car, RawLead(False, False, None, None, None, None), standstill_clear_horizon(model))


@pytest.mark.parametrize(
  'kind',
  [
    'stop',
    'no_launch',
    'negative_velocity',
    'wrong_time',
    'missing_time',
    'short_velocity',
    'tail_stop',
    'tail_reverses',
    'prefix_outside_budget',
    'late_reverse',
    'bad_origin',
    'nonfinite',
  ],
)
def test_contradictory_or_malformed_launch_never_relaxes_prefix(kind):
  model = launch()
  if kind == 'stop':
    model.action.shouldStop = True
  elif kind == 'no_launch':
    model.action.desiredAcceleration = 0.29
  elif kind == 'negative_velocity':
    model.velocity.x[3] = -0.051
  elif kind == 'wrong_time':
    model.position.t[3] = 1.0
  elif kind == 'missing_time':
    model.position.t = []
  elif kind == 'short_velocity':
    model.velocity.x.pop()
  elif kind == 'tail_stop':
    model.velocity.x[-1] = 0.1
  elif kind == 'tail_reverses':
    model.velocity.x[-3] = model.velocity.x[-2] + 0.1
  elif kind == 'prefix_outside_budget':
    model.position.x[1] = -0.02
  elif kind == 'late_reverse':
    model.position.x[24] = model.position.x[23] - 0.001
  elif kind == 'bad_origin':
    model.position.x[0] = -0.001
  elif kind == 'nonfinite':
    model.position.x[8] = math.nan
  assert standstill_clear_horizon(model) is None


def test_original_valid_geometry_does_not_require_launch_metadata():
  model = NS(position=NS(x=[i * 2.0 for i in range(33)]))
  assert standstill_clear_horizon(model) == 64.0


def test_renewed_stop_resets_clear_dwell():
  detector = StopLightDetector()
  detector.committed = detector.standstill_committed = True
  detector.commit_distance_m = 0.0
  frame = StopFrame(
    observed_mono_s=100.0,
    model_horizon_m=70.0,
    model_stop_time_s=7.7,
    traffic_mode=False,
    stop_sign_confirmed=False,
    lead=None,
    left_blinker=False,
    right_blinker=False,
    steering_angle_deg=0.0,
    driving_in_curve=False,
    car_fingerprint="HYUNDAI_IONIQ_6",
    pedal_override=False,
    standstill=True,
    longitudinal_launch_active=False,
    longitudinal_stop_active=True,
    model_should_stop=False,
    forcing_stop=False,
    stationary_launch_clear=True,
    speed_mps=0.0,
    now_mono_s=100.0,
  )
  for t in [100.0, 100.2, 100.4]:
    frame = replace(frame, now_mono_s=t)
    detector._commit(frame, 70.0, False, False, 0.05)
    assert detector.committed
  frame = replace(frame, now_mono_s=100.45, model_should_stop=True)
  detector._commit(frame, 70.0, False, False, 0.05)
  frame = replace(frame, model_should_stop=False)
  for t in [100.5, 100.7, 100.9]:
    frame = replace(frame, now_mono_s=t)
    detector._commit(frame, 70.0, False, False, 0.05)
    assert detector.committed
  frame = replace(frame, now_mono_s=101.0)
  detector._commit(frame, 70.0, False, False, 0.05)
  assert not detector.committed
