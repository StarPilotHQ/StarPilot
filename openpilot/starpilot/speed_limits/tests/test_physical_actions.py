"""Card-side validation for confirmation, engagement, and active caps."""

import math

import pytest
from opendbc.car.dashboard_speed_limit import Observation, Status

from openpilot.cereal import messaging
from openpilot.selfdrive.car.cruise import SlcPendingConfirmation
from openpilot.starpilot.speed_limits import physical_actions
from openpilot.starpilot.speed_limits.selection import Source
from openpilot.starpilot.speed_limits.tests.test_runtime_replay import ReplaySM


NOW = 60_000_000_000


@pytest.fixture
def sm():
  state = messaging.new_message('slcState').slcState
  state.enabled, state.observationKind, state.source = True, 'valid', 'map'
  state.sessionId, state.frameMonoTime = 'drive', NOW
  state.hasPending, state.pendingSpeedLimit, state.speedLimit = True, 25.0, 25.0
  state.pendingSource, state.pendingSinceMonoTime = 'map', NOW
  state.decisionId, state.presentationId = 1, 2
  return ReplaySM({'slcState': state}, NOW)


@pytest.mark.parametrize('source', tuple(Source))
def test_confirm_any_source_without_dashboard_evidence(sm, source):
  sm['slcState'].pendingSource = source.value
  assert physical_actions.pending_confirmation(sm, NOW, long_active=True) == SlcPendingConfirmation('drive', 1, 2)


def test_retained_choice_still_consumes_a_press_as_its_published_deadline_passes(sm):
  state = sm['slcState']
  state.observationKind, state.source, state.pendingSource = 'unknown', 'none', 'map'
  state.pendingSinceMonoTime = NOW
  published = NOW + physical_actions.CONFIRMATION_NS - 10_000_000
  state.frameMonoTime = sm.logMonoTime['slcState'] = published
  # The fresh displayed frame still has the choice; a late press must not adjust SET.
  assert physical_actions.pending_confirmation(sm, published + 20_000_000, long_active=True) is not None
  assert physical_actions.pending_confirmation(sm, published + physical_actions.STATE_MAX_AGE_NS + 1, long_active=True) is None


@pytest.mark.parametrize('field,value', [
  ('enabled', False), ('displayOnly', True), ('sessionId', ''), ('automaticAdoptionPending', True),
  ('pendingSource', 'none'), ('pendingSource', ''), ('pendingSource', 'unsupported'),
  ('pendingSinceMonoTime', 0), ('pendingSinceMonoTime', NOW + 1),
  ('pendingSinceMonoTime', NOW - physical_actions.CONFIRMATION_NS - 1),
  ('frameMonoTime', 0), ('frameMonoTime', NOW + 1), ('frameMonoTime', NOW - physical_actions.STATE_MAX_AGE_NS - 1),
  ('hasPending', False), ('decisionId', 0), ('presentationId', 0), ('pendingSpeedLimit', 0.0),
  ('pendingSpeedLimit', math.nan), ('pendingSpeedLimit', math.inf),
])
def test_reject_invalid_or_unavailable_pending_decision(sm, field, value):
  setattr(sm['slcState'], field, value)
  assert physical_actions.pending_confirmation(sm, NOW, long_active=True) is None


@pytest.mark.parametrize('valid,alive,stamp', [
  (False, True, NOW), (True, False, NOW), (True, True, 0), (True, True, NOW + 1),
  (True, True, NOW - physical_actions.STATE_MAX_AGE_NS - 1),
])
def test_reject_invalid_or_stale_transport(sm, valid, alive, stamp):
  sm.valid['slcState'], sm.alive['slcState'], sm.logMonoTime['slcState'] = valid, alive, stamp
  assert physical_actions.pending_confirmation(sm, NOW, long_active=True) is None
  sm['slcState'].hasCeiling, sm['slcState'].effectiveCap = True, 20.
  assert physical_actions.active_ceiling(sm, NOW) is None


@pytest.mark.parametrize('kind', ['valid', 'unknown', 'stale', 'absent'])
def test_active_ceiling_includes_a_qualified_retained_cap(sm, kind):
  state = sm['slcState']
  state.hasCeiling, state.effectiveCap, state.observationKind = True, 20., kind
  assert physical_actions.active_ceiling(sm, NOW) == 20.
  assert physical_actions.active_ceiling(sm, NOW, after_ns=NOW) is None


@pytest.mark.parametrize('field,value', [
  ('enabled', False), ('displayOnly', True), ('sessionId', ''), ('hasCeiling', False),
  ('frameMonoTime', 0), ('frameMonoTime', NOW + 1), ('frameMonoTime', NOW - physical_actions.STATE_MAX_AGE_NS - 1),
  ('effectiveCap', 0.), ('effectiveCap', -1.), ('effectiveCap', math.nan), ('effectiveCap', math.inf), ('effectiveCap', 146 / 3.6),
])
def test_active_ceiling_rejects_unqualified_caps(sm, field, value):
  state = sm['slcState']
  state.hasCeiling, state.effectiveCap = True, 20.
  setattr(state, field, value)
  assert physical_actions.active_ceiling(sm, NOW) is None


@pytest.mark.parametrize('long_active,after_ns', [(False, 0), (True, NOW)])
def test_preserve_authority_and_configuration_barriers(sm, long_active, after_ns):
  assert physical_actions.pending_confirmation(sm, NOW, long_active=long_active, after_ns=after_ns) is None


def test_fresh_post_configuration_decision_qualifies(sm):
  assert physical_actions.pending_confirmation(sm, NOW, long_active=True, after_ns=NOW - 1) is not None


@pytest.mark.parametrize('field,value', [
  ('enabled', False), ('displayOnly', True), ('observationKind', 'stale'), ('source', 'online'),
  ('presentationId', 0), ('frameMonoTime', NOW - physical_actions.STATE_MAX_AGE_NS - 1),
  ('hasPresentationAdjustedLimit', False), ('presentationAdjustedLimit', 0.),
  ('presentationAdjustedLimit', math.nan), ('presentationAdjustedLimit', math.inf),
  ('presentationAdjustedLimit', 146 / 3.6), ('presentationAdjustedLimit', 7 / 3.6),
])
def test_engagement_rejects_unqualified_current_target(sm, field, value):
  sm['slcState'].hasPresentationAdjustedLimit, sm['slcState'].presentationAdjustedLimit = True, 25.
  setattr(sm['slcState'], field, value)
  assert physical_actions.engagement_state(sm, None, 'card', NOW) is None


@pytest.mark.parametrize('target', [8 / 3.6, 145 / 3.6])
def test_engagement_accepts_float32_cruise_boundaries(sm, target):
  sm['slcState'].hasPresentationAdjustedLimit, sm['slcState'].presentationAdjustedLimit = True, target
  assert physical_actions.engagement_state(sm, None, 'card', NOW) is not None


@pytest.mark.parametrize('state_changes, local_changes, after_ns, matches', [
  pytest.param({}, {}, 0, True, id='newer-same-episode'),
  pytest.param({}, {'observed_ns': NOW - 100_000_000, 'valid_until_ns': NOW + 50_000_000}, 0, True, id='same-packet'),
  pytest.param({}, {'observed_ns': NOW - 100_000_000}, 0, False, id='inconsistent-same-packet'),
  pytest.param({'sourceValidUntilMonoTime': NOW}, {}, 0, True, id='at-planner-expiry'),
  pytest.param({}, {'valid_until_ns': NOW}, 0, True, id='at-local-expiry'),
  pytest.param({'sourceValidUntilMonoTime': NOW - 1}, {}, 0, False, id='expired-planner-sample'),
  pytest.param({}, {'valid_until_ns': NOW - 1}, 0, False, id='expired-local-sample'),
  pytest.param({}, {'observed_ns': NOW - 100_000_001}, 0, False, id='older-local-sample'),
  pytest.param({}, {'observed_ns': NOW + 1}, 0, False, id='future-local-sample'),
  pytest.param({'sourceObservedMonoTime': NOW - 10_000_000}, {}, 0, False, id='sample-after-planner-frame'),
  pytest.param({}, {'episode': 2}, 0, False, id='new-episode'),
  pytest.param({}, {'speed_mps': 24.}, 0, False, id='different-speed'),
  pytest.param({'sourceProducerSessionId': 'old-card'}, {}, 0, False, id='different-producer'),
  pytest.param({}, {}, NOW - 100_000_000, False, id='planner-sample-before-settings-change'),
])
def test_dashboard_engagement_qualifies_both_samples(sm, state_changes, local_changes, after_ns, matches):
  state = sm['slcState']
  state.source, state.sourceProducerSessionId, state.sourceEpisode = 'dashboard', 'card', 1
  state.frameMonoTime = sm.logMonoTime['slcState'] = NOW - 20_000_000
  state.sourceObservedMonoTime, state.sourceValidUntilMonoTime = NOW - 100_000_000, NOW + 50_000_000
  state.hasPresentationAdjustedLimit, state.presentationAdjustedLimit = True, 25.
  for field, value in state_changes.items():
    setattr(state, field, value)
  local = Observation(**({'status': Status.VALID, 'speed_mps': 25., 'episode': 1,
                          'observed_ns': NOW - 5_000_000, 'valid_until_ns': NOW + 145_000_000} | local_changes))

  assert (physical_actions.engagement_state(sm, local, 'card', NOW, after_ns=after_ns) is not None) == matches
