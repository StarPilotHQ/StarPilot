"""Numerical recovery regression; native/road qualification is separate."""
from dataclasses import replace
import pytest

from openpilot.starpilot.flm.live_tracking import RETRY_NS
from openpilot.starpilot.flm.tests.test_live_tracking import optimizer, ready, episode, refresh, acknowledge


@pytest.mark.parametrize('rates', [(2.5, 0.1, 0.1), (1.4, 1.4, 1.4)])
def test_scored_oscillation_rejection_prefers_another_qualified_coordinate(rates):
  owner = optimizer()
  _, now = ready(owner)
  owner.before = tuple(replace(row, reversal_hz=rate) for row, rate in zip(owner.before, (0.46, 0.46, 2.32), strict=True))
  offer = owner.offer(now)
  assert acknowledge(owner, offer)
  for i, rate in enumerate(rates):
    row = episode(owner, 40+i, 'ff_gain_left', profile=offer.target_sha256,
                  error=0.001, reversal=rate, start=now+1_000_000_000+i*1_000_000_000)
    refresh(owner, row.end_ns)
    owner.observe_episode(row, now_ns=row.end_ns)
    if owner.offer_state['phase'] == 'rollback':
      break
  assert owner.offer_state['phase'] == 'rollback'
  rollback = owner.offer(row.end_ns)
  assert acknowledge(owner, rollback)
  assert not any(owner.accepted.values())
  after = max(row.end_ns+1_000_000_000, owner.last_attempt+RETRY_NS)
  for i in range(6):
    row = episode(owner, 100+i, 'ff_gain_left', start=after+i*1_000_000_000)
    preferred = row.sensitivity[0]
    alternate = replace(preferred, name='turn_in_boost_left', error_feature_sum=0.04)
    row = replace(row, sensitivity=(preferred, alternate))
    refresh(owner, row.end_ns)
    owner.observe_episode(row, now_ns=row.end_ns)
    if i == 2:
      assert owner.discovery['name'] == 'turn_in_boost_left'
  assert owner.offer_state['phase'] == 'trial'
  assert owner.offer_state['names'] == ('turn_in_boost_left',)
  alternative_offer = owner.offer(row.end_ns)
  assert acknowledge(owner, alternative_offer)
  after = row.end_ns+1_000_000_000
  for i in range(3):
    row = episode(owner, 200+i, 'turn_in_boost_left', profile=alternative_offer.target_sha256,
                  error=0.001, start=after+i*1_000_000_000)
    refresh(owner, row.end_ns)
    owner.observe_episode(row, now_ns=row.end_ns)
  confirmed = owner.offer(row.end_ns)
  assert confirmed.phase == 'confirmed'
  assert acknowledge(owner, confirmed)
  assert owner.accepted['turn_in_boost_left'] > 0
  assert owner.accepted['ff_gain_left'] == 0
