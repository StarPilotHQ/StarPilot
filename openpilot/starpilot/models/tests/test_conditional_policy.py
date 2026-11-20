"""Speed classification is deterministic and independent of inference/control."""
import math

import pytest

from openpilot.starpilot.models.conditional_policy import SpeedPolicy, MPH_TO_MS

BASE = 100_000_000_000


def sample(policy, mph, t=0., *, current='highway', **kwargs):
  now = BASE + round(t * 1e9)
  return policy.sample(mph * MPH_TO_MS, kwargs.pop('stamp', now), now, current=current, valid=kwargs.pop('valid', True), **kwargs)


@pytest.mark.parametrize('mph,role', [(0, None), (45, None), (49, None), (49.99, None), (50, 'highway'), (54.99, 'highway'), (55, 'highway'), (60, 'highway')])
def test_highway_entry_is_immediate_at_configured_speed(mph, role):
  assert sample(SpeedPolicy(), mph, current='city') == role


def test_city_requires_thirty_continuous_seconds_and_deadband_resets_clock():
  policy = SpeedPolicy()
  for i in range(600):
    assert sample(policy, 45, i * .05) is None
  assert sample(policy, 45, 30.) == 'city'
  assert sample(policy, 46, 30.05) is None
  assert policy.low_since is None
  assert sample(policy, 45, 30.1) is None


def test_oscillation_near_threshold_does_not_flap():
  policy = SpeedPolicy()
  current, changes = 'city', []
  for i in range(2400):
    role = sample(policy, 45 if i % 2 else 55, i * .05, current=current)
    if role:
      current = role
      changes.append(role)
      policy.switched(BASE + round(i * .05 * 1e9))
  assert changes == ['highway']


def test_highway_merge_bypasses_city_cooldown_and_return_requires_another_dwell():
  policy = SpeedPolicy()
  policy.switched(BASE)
  assert sample(policy, 60, .05, current='city') == 'highway'
  policy.switched(BASE + 50_000_000)
  for i in range(1, 601):
    assert sample(policy, 40, .05 + i * .05) is None
  assert sample(policy, 40, 30.1) == 'city'


@pytest.mark.parametrize('bad', [math.nan, math.inf, -math.inf, -1., 101., True, None, '60'])
def test_invalid_speed_breaks_city_dwell(bad):
  policy = SpeedPolicy()
  sample(policy, 40)
  assert policy.sample(bad, BASE + 50_000_000, BASE + 50_000_000, valid=True, current='highway') is None
  assert policy.low_since is None


@pytest.mark.parametrize('change', [{'stamp': 0}, {'stamp': BASE + 1_000_000_000}, {'stamp': BASE - 1}, {'valid': False}])
def test_bad_or_reordered_sources_cannot_advance_dwell(change):
  policy = SpeedPolicy()
  sample(policy, 40)
  assert sample(policy, 40, .05, **change) is None
  assert policy.low_since is None


def test_sampling_gap_requires_new_full_dwell():
  policy = SpeedPolicy()
  sample(policy, 40)
  assert sample(policy, 40, 31.) is None
  assert policy.low_since == BASE + 31_000_000_000


@pytest.mark.parametrize('threshold', [True, 50., 9, 101, math.nan])
def test_invalid_policy_settings_are_rejected(threshold):
  with pytest.raises(ValueError):
    SpeedPolicy(threshold)


def test_user_speed_is_the_actual_highway_trigger_with_internal_return_delay():
  policy = SpeedPolicy(60)
  assert sample(policy, 59.99, current='city') is None
  assert sample(policy, 60, .05, current='city') == 'highway'
  for i in range(601):
    result = sample(policy, 55, .1 + i * .05)
    assert result == ('city' if i == 600 else None)
