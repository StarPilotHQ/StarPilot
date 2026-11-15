"""The sign shows the limit SLC uses while it is on, otherwise the latest source observation."""
from dataclasses import replace

import pytest

from openpilot.starpilot.ui.onroad_state import ObservationKind, OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.unified_speed_presentation import resolve_unified_speed


def state(**changes):
  obs = SpeedLimitObservation(ObservationKind.VALID, 'vision', 30., accepted_speed_limit_mps=20.,
    accepted_adjusted_limit_mps=22., accepted_source='dashboard', presentation_adjusted_limit_mps=32.,
    action_enabled=True, session_id='drive')
  return replace(OnroadState(True, False, 20., 100., obs, metric=True, longitudinal_active=True), **changes)


@pytest.mark.parametrize('metric,live,accepted', [(True, ('108', '+7'), ('72', '+7')), (False, ('67', '+5'), ('45', '+4'))])
def test_sign_shows_the_accepted_limit_while_slc_is_on_and_the_latest_reading_otherwise(metric, live, accepted):
  # The fixture's VISION reading differs from the accepted DASH limit without being pending: it was rejected.
  current = state(metric=metric)
  for on in (True, False):
    shown = resolve_unified_speed(replace(current, longitudinal_active=on))
    assert (shown.posted_text, shown.offset_text) == (accepted if on else live)
    assert shown.source == ('dashboard' if on else 'vision')
    assert not shown.posted_retained and not shown.show_last
  pedal = resolve_unified_speed(replace(current, longitudinal_active=False, longitudinal_overridden=True))
  assert (pedal.posted_text, pedal.source) == (accepted[0], 'dashboard')


@pytest.mark.parametrize('metric', [False, True])
def test_rejected_reading_keeps_the_followed_limit_source_speed_and_offset(metric):
  factor = 3.6 if metric else 2.2369362921
  current = state(metric=metric, cruise_kph=80 / factor * 3.6)
  followed = replace(current.speed_limit, source='vision', speed_limit_mps=55 / factor, presentation_adjusted_limit_mps=60 / factor,
                     accepted_source='map', accepted_speed_limit_mps=45 / factor, accepted_adjusted_limit_mps=50 / factor,
                     effective_cluster_target_mps=50 / factor, effective_cap_mps=50 / factor, limiting_max_set=True)
  shown = resolve_unified_speed(replace(current, speed_limit=followed))
  assert (shown.source, shown.posted_text, shown.offset_text, shown.limit_in_use) == ('map', '45', '+5', True)
  offered = resolve_unified_speed(replace(current, speed_limit=replace(followed, pending_speed_limit_mps=55 / factor)))
  assert (offered.source, offered.posted_text, offered.pending) == ('vision', '55', True)
def test_set_speed_is_the_only_active_speed_role_even_below_or_above_the_input_limit():
  for selected in (50., 79.2, 120.):
    shown = resolve_unified_speed(state(cruise_kph=selected))
    assert shown.max_text == str(round(selected))
    assert shown.max_status == 'Set speed'
    assert (shown.active_side, shown.status) == ('max', 'Using set speed')


def test_slc_limiting_role_keeps_driver_set_and_source_sign_independent():
  current = state()
  limited = replace(current, speed_limit=replace(current.speed_limit, limiting_max_set=True, effective_cap_mps=22.))
  shown = resolve_unified_speed(limited)
  assert (shown.max_text, shown.posted_text, shown.active_side, shown.status) == ('100', '72', 'limit', 'Using speed limit')
  pedal = resolve_unified_speed(replace(limited, longitudinal_overridden=True))
  assert (pedal.active_side, pedal.status) == ('max', 'Using accelerator')


@pytest.mark.parametrize('automatic,status', [(False, 'Confirm speed limit'), (True, 'Adopting limit')])
def test_pending_limit_is_shown_immediately_with_its_own_offset(automatic, status):
  current = state()
  obs = replace(current.speed_limit, pending_speed_limit_mps=30., pending_adjusted_limit_mps=32., automatic_adoption_pending=automatic)
  shown = resolve_unified_speed(replace(current, speed_limit=obs))
  assert (shown.posted_text, shown.offset_text, shown.status) == ('108', '+7', status)
  assert shown.pending


def test_manual_override_and_pedal_status_preserve_the_set_speed_and_sign():
  current = state()
  manual = resolve_unified_speed(replace(current, speed_limit=replace(current.speed_limit, driver_override_active=True)))
  pedal = resolve_unified_speed(replace(current, longitudinal_overridden=True))
  assert manual.status == 'Set-speed override'
  assert pedal.status == 'Using accelerator'
  assert manual.max_text == pedal.max_text == '100'
  assert manual.posted_text == pedal.posted_text == '72'


def test_retained_limit_is_information_only_without_a_separate_ceiling():
  current = state()
  retained = replace(current.speed_limit, kind=ObservationKind.UNKNOWN, speed_limit_mps=None,
                     effective_cluster_target_mps=22., effective_cap_mps=None)
  shown = resolve_unified_speed(replace(current, speed_limit=retained))
  assert (shown.posted_text, shown.offset_text, shown.source) == ('72', '+7', 'dashboard')
  assert shown.active_side == 'max'


def test_display_only_and_unset_speed_remain_explicit():
  current = state(cruise_kph=None, longitudinal_active=False)
  shown = resolve_unified_speed(replace(current, speed_limit=replace(current.speed_limit, action_enabled=False)))
  assert (shown.max_text, shown.max_status, shown.status) == ('–', 'Not set', 'Display only')


def test_hidden_max_and_offset_preferences_keep_the_latest_sign():
  current = state()
  shown = resolve_unified_speed(replace(current, customization={**current.customization, 'showMaxSpeed': False}, show_slc_offset=False))
  assert (shown.mode, shown.posted_text, shown.offset_text) == ('limit_only', '72', None)


@pytest.mark.parametrize('stage,expected', [('live', True), ('confirmation', False), ('accepted', False), ('last_received', True)])
def test_configured_offset_follows_the_resolved_posted_limit(stage, expected):
  from openpilot.starpilot.speed_limits.speed_domain import OffsetBand, OffsetSchedule
  current = state(slc_offset_schedule=OffsetSchedule((OffsetBand(0., 25., 0.), OffsetBand(25., None, 2.))),
                  longitudinal_active=stage in ('confirmation', 'accepted'))
  if stage == 'confirmation':
    current = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=20., pending_adjusted_limit_mps=20.))
  elif stage == 'last_received':
    current = replace(current, speed_limit=replace(current.speed_limit, kind=ObservationKind.STALE, speed_limit_mps=None,
      last_received_speed_limit_mps=30., last_received_adjusted_limit_mps=32.))
  shown = resolve_unified_speed(current, confirmation=stage == 'confirmation')
  assert shown.posted_text == ('108' if expected else '72')
  assert shown.offset_configured == expected
@pytest.mark.parametrize('pending,adjusted,presentation,offset,expected', [
  (30., 33., 32., 2., '119'),
  (30., None, 32., 2., '115'),
  (30., None, None, 2., '115'),
  (25., None, 32., 2., '97'),
])
def test_confirmation_proposal_uses_the_pending_target_and_its_offset(pending, adjusted, presentation, offset, expected):
  current = state()
  observation = replace(current.speed_limit, pending_speed_limit_mps=pending, pending_adjusted_limit_mps=adjusted,
                        presentation_adjusted_limit_mps=presentation, offset_mps=offset)
  assert resolve_unified_speed(replace(current, speed_limit=observation)).proposal_text == expected


@pytest.mark.parametrize('change', ['none', 'retained', 'hold', 'recovering', 'pending', 'source', 'rounded-posted',
                                  'rounded-target', 'pedal', 'override', 'display-only', 'not-limiting', 'disengaged'])
def test_in_use_requires_accepted_target_and_actual_control(change):
  current = state()
  obs = replace(current.speed_limit, accepted_speed_limit_mps=30., accepted_adjusted_limit_mps=32.,
                accepted_source='vision', effective_cluster_target_mps=32., effective_cap_mps=32., limiting_max_set=True)
  changes = {
    'retained': {'kind': ObservationKind.STALE, 'speed_limit_mps': None, 'last_received_speed_limit_mps': 30.},
    'hold': {'status': 'display_hold'}, 'recovering': {'sign_recovering': True},
    'pending': {'pending_speed_limit_mps': 30.}, 'source': {'accepted_source': 'dashboard'},
    'rounded-posted': {'accepted_speed_limit_mps': 30.01},
    'rounded-target': {'effective_cluster_target_mps': 32.01},
    'override': {'driver_override_active': True}, 'display-only': {'action_enabled': False},
    'not-limiting': {'limiting_max_set': False},
  }
  current = replace(current, speed_limit=replace(obs, **changes.get(change, {})),
                    longitudinal_overridden=change == 'pedal', longitudinal_active=change != 'disengaged')
  # The accepted limit is the one in use, whichever source or reading the latest observation shows.
  assert resolve_unified_speed(current).limit_in_use == (change in ('none', 'retained', 'recovering', 'source', 'rounded-posted'))


def test_pedal_basis_cannot_be_presented_as_a_held_override_between_message_updates():
  current = state()
  current = replace(current, speed_limit=replace(current.speed_limit, driver_override_active=True,
                    effective_cluster_target_mps=26., override_basis='pedal'))
  assert resolve_unified_speed(current).override_text is None
  released = replace(current, speed_limit=replace(current.speed_limit, override_basis='persistent'))
  assert resolve_unified_speed(released).override_text == '94'


@pytest.mark.parametrize('metric', [False, True])
@pytest.mark.parametrize('selected', [45, 60, 70])
@pytest.mark.parametrize('kind', [ObservationKind.ABSENT, ObservationKind.UNKNOWN, ObservationKind.STALE])
def test_accepted_target_survives_source_gaps_without_presenting_rejected_history(metric, selected, kind):
  factor = 3.6 if metric else 2.2369362921
  current = state(metric=metric, cruise_kph=selected / factor * 3.6)
  obs = replace(current.speed_limit, source='dashboard', speed_limit_mps=55 / factor,
                accepted_speed_limit_mps=55 / factor, accepted_adjusted_limit_mps=60 / factor,
                presentation_adjusted_limit_mps=60 / factor, effective_cluster_target_mps=60 / factor,
                effective_cap_mps=58 / factor, limiting_max_set=selected > 60)
  before = resolve_unified_speed(replace(current, speed_limit=obs))
  gap = replace(obs, kind=kind, source='unknown', speed_limit_mps=None,
                last_received_speed_limit_mps=99 / factor, last_received_adjusted_limit_mps=109 / factor,
                last_received_source='vision')
  held = resolve_unified_speed(replace(current, speed_limit=gap))
  assert held.posted_retained
  assert not held.show_last
  assert (held.posted_text, held.source, held.offset_text) == ('55', 'dashboard', '+5')
  assert (held.limit_in_use, held.active_side, held.status) == (before.limit_in_use, before.active_side, before.status)
  recovered = resolve_unified_speed(replace(current, speed_limit=replace(obs, sign_recovering=True)))
  assert recovered == before


@pytest.mark.parametrize('change,accepted', [
  ('none', True), ('no-cap', True), ('different-target', True), ('no-session', True), ('pedal', True), ('override', True),
  ('no-accepted', False), ('no-adjusted', False), ('no-source', False), ('off', False), ('display-only', False),
  ('display-hold', False), ('pending', False)])
def test_source_history_stays_last_only_without_an_accepted_limit_in_use(change, accepted):
  current = state(cruise_kph=60., longitudinal_active=change != 'off', longitudinal_overridden=change == 'pedal')
  obs = replace(current.speed_limit, kind=ObservationKind.STALE, speed_limit_mps=None, source='none',
                last_received_speed_limit_mps=30., last_received_source='vision',
                last_received_adjusted_limit_mps=32., effective_cluster_target_mps=22., effective_cap_mps=21.)
  changes = {
    'no-cap': {'effective_cap_mps': None}, 'different-target': {'effective_cluster_target_mps': 23.},
    'no-accepted': {'accepted_speed_limit_mps': None}, 'no-adjusted': {'accepted_adjusted_limit_mps': None},
    'no-session': {'session_id': None}, 'no-source': {'accepted_source': 'none'},
    'override': {'driver_override_active': True}, 'display-only': {'action_enabled': False},
    'display-hold': {'status': 'display_hold'}, 'pending': {'pending_speed_limit_mps': 25.},
  }
  shown = resolve_unified_speed(replace(current, speed_limit=replace(obs, **changes.get(change, {}))))
  assert shown.posted_retained
  assert shown.show_last != accepted
  assert (shown.source, shown.posted_text) == (('dashboard', '72') if accepted else ('vision', '108'))
