"""Three visible purple pulses and the unchanged manual confirmation rhythm."""
from dataclasses import replace

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.ui.onroad_state import ObservationKind, OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.speed_limit_pulse import SpeedLimitPulse


def rgba(color):
  return tuple(color) if isinstance(color, tuple) else (color.r, color.g, color.b, color.a)


def state(automatic=True, **changes):
  obs = SpeedLimitObservation(ObservationKind.VALID, 'dashboard', 20., pending_speed_limit_mps=20.,
                             automatic_adoption_pending=automatic, pending_since_ns=10_000_000_000, presentation_id=1, action_enabled=True)
  return replace(OnroadState(True, False, 10., 80., obs, drive_frame=1), **changes)


def test_three_slow_flashes_stop_after_three_visible_seconds():
  pulse = SpeedLimitPulse()
  pulse.update(state(), 10.)
  assert pulse.start_time == 10.
  for now in (10., 11., 12., 13., 14.):
    assert rgba(pulse.color(rl.WHITE, now)) == rgba(rl.WHITE)
  for now in (10.5, 11.5, 12.5):
    assert rgba(pulse.color(rl.WHITE, now)) == (188, 132, 255, 255)


def test_manual_confirmation_repeats_two_gentle_pulses_for_thirty_seconds_without_tinting_content():
  pulse = SpeedLimitPulse()
  pulse.update(state(False), 10.)
  base = rl.Color(244, 247, 245, 96)
  for cycle in (0., 3.2, 6.4, 25.6):
    for elapsed in (.35, 1.25):
      now = 10. + cycle + elapsed
      pulse.update(state(False), now)
      assert pulse.confirmation_strength(now) == pytest.approx(1.)
      assert rgba(pulse.color(base, now)) == rgba(base)
    for elapsed in (0., .8, 1.6, 2.1, 2.59, 3., 3.19):
      assert rgba(pulse.color(base, 10. + cycle + elapsed)) == rgba(base)
  assert pulse.confirmation_strength(9.9) == 0.
  assert pulse.confirmation_strength(19.95) > .9
  assert pulse.confirmation_strength(39.15) > .9
  assert pulse.confirmation_strength(40.) == 0.
  assert pulse.confirmation_strength(42.75) == 0.


@pytest.mark.parametrize('kind', list(ObservationKind))
def test_finished_or_unavailable_manual_decision_stops_pulsing(kind):
  pulse = SpeedLimitPulse()
  current = state(False)
  pulse.update(current, 10.)
  observation = replace(current.speed_limit, kind=kind, speed_limit_mps=20. if kind == ObservationKind.VALID else None,
                        pending_speed_limit_mps=None, last_received_speed_limit_mps=20.)
  pulse.update(replace(current, speed_limit=observation), 10.1)
  assert rgba(pulse.color(rl.WHITE, 10.35)) == rgba(rl.WHITE)
  assert pulse.start_time == -float('inf')


def test_manual_widget_reappearance_and_new_decision_use_controller_time():
  pulse = SpeedLimitPulse()
  pulse.update(state(False), 10., visible=False)
  pulse.update(state(False), 13.2)
  assert pulse.confirmation_strength(13.55) == pytest.approx(1.)
  current = state(False)
  current = replace(current, speed_limit=replace(current.speed_limit, pending_since_ns=13_000_000_000,
                                               speed_limit_mps=25., pending_speed_limit_mps=25.))
  pulse.update(current, 13.)
  assert rgba(pulse.color(rl.WHITE, 13.)) == rgba(rl.WHITE)
  assert pulse.confirmation_strength(13.35) == pytest.approx(1.)


def test_missing_pending_timestamp_starts_once_and_resets_for_a_new_drive():
  pulse = SpeedLimitPulse()
  current = state(False)
  current = replace(current, speed_limit=replace(current.speed_limit, pending_since_ns=0))
  pulse.update(current, 10.)
  pulse.update(current, 10.35)
  assert pulse.confirmation_strength(10.35) == pytest.approx(1.)
  pulse.update(replace(current, drive_frame=2), 20.)
  assert pulse.start_time == 20.


def test_switching_between_manual_and_automatic_uses_the_current_rhythm():
  pulse = SpeedLimitPulse()
  pulse.update(state(), 10.)
  pulse.update(state(False), 10.)
  assert pulse.confirmation_strength(10.35) == pytest.approx(1.)
  pulse.update(state(), 10.)
  assert rgba(pulse.color(rl.WHITE, 10.5)) == (188, 132, 255, 255)
  assert rgba(pulse.color(rl.WHITE, 13.)) == rgba(rl.WHITE)


def test_reappearing_automatic_widget_gets_three_visible_cycles():
  pulse = SpeedLimitPulse()
  pulse.update(state(), 10., visible=False)
  pulse.update(state(), 12.)
  assert rgba(pulse.color(rl.WHITE, 12.5)) == (188, 132, 255, 255)
  assert rgba(pulse.color(rl.WHITE, 13.5)) == (188, 132, 255, 255)
  assert rgba(pulse.color(rl.WHITE, 14.5)) == (188, 132, 255, 255)
  assert rgba(pulse.color(rl.WHITE, 15.)) == rgba(rl.WHITE)


def display_state(limit=20., **changes):
  current = state()
  return replace(current, speed_limit=replace(current.speed_limit, speed_limit_mps=limit,
    pending_speed_limit_mps=None, automatic_adoption_pending=False, action_enabled=False, pending_since_ns=0), **changes)


def test_display_only_new_limit_pulses_without_restarting_on_repeated_messages():
  pulse = SpeedLimitPulse()
  pulse.update(display_state(), 10.)
  pulse.update(display_state(), 10.5)
  assert rgba(pulse.color(rl.WHITE, 10.5)) == (188, 132, 255, 255)
  pulse.update(display_state(), 13.5)
  assert rgba(pulse.color(rl.WHITE, 13.5)) == rgba(rl.WHITE)
  pulse.update(display_state(25.), 14.)
  pulse.update(display_state(25.), 14.5)
  assert rgba(pulse.color(rl.WHITE, 14.5)) == (188, 132, 255, 255)


def test_display_only_hidden_stale_and_reappearance_do_not_invent_new_limit():
  pulse = SpeedLimitPulse()
  pulse.update(display_state(), 10., visible=False)
  pulse.update(display_state(), 10.5)
  assert rgba(pulse.color(rl.WHITE, 10.5)) == rgba(rl.WHITE)
  current = display_state()
  stale = replace(current, speed_limit=replace(current.speed_limit, kind=ObservationKind.STALE,
                  speed_limit_mps=None, last_received_speed_limit_mps=25.))
  pulse.update(stale, 11.)
  assert rgba(pulse.color(rl.WHITE, 11.5)) == rgba(rl.WHITE)
  pulse.update(display_state(25.), 12.)
  assert rgba(pulse.color(rl.WHITE, 12.5)) == rgba(rl.WHITE)


@pytest.mark.parametrize('held_fields', [{'status': 'display_hold'}, {'sign_recovering': True}])
def test_retained_display_finishes_existing_pulse_without_restarting(held_fields):
  pulse = SpeedLimitPulse()
  current = display_state()
  pulse.update(current, 10.)
  assert rgba(pulse.color(rl.WHITE, 10.5)) != rgba(rl.WHITE)
  held = replace(current, speed_limit=replace(current.speed_limit, **held_fields))
  pulse.update(held, 10.5)
  assert rgba(pulse.color(rl.WHITE, 10.5)) != rgba(rl.WHITE)
  for now in (11.5, 12.5):
    pulse.update(held, now)
    assert rgba(pulse.color(rl.WHITE, now)) == (188, 132, 255, 255)
  pulse.update(held, 13.5)
  assert rgba(pulse.color(rl.WHITE, 13.5)) == rgba(rl.WHITE)
  pulse.update(display_state(25.), 14.)
  assert rgba(pulse.color(rl.WHITE, 14.5)) != rgba(rl.WHITE)


def test_display_only_new_drive_can_highlight_its_first_limit():
  pulse = SpeedLimitPulse()
  pulse.update(display_state(), 10.)
  pulse.update(display_state(drive_frame=2), 20.)
  assert rgba(pulse.color(rl.WHITE, 20.5)) == (188, 132, 255, 255)


def test_automatic_adoption_finishes_existing_highlight():
  pulse = SpeedLimitPulse()
  current = state()
  pulse.update(current, 10.)
  accepted = replace(current, speed_limit=replace(current.speed_limit, pending_speed_limit_mps=None,
                     automatic_adoption_pending=False, pending_since_ns=0, accepted_speed_limit_mps=20.))
  for now in (11.5, 12.5):
    pulse.update(accepted, now)
    assert rgba(pulse.color(rl.WHITE, now)) == (188, 132, 255, 255)
  pulse.update(accepted, 13.5)
  assert rgba(pulse.color(rl.WHITE, 13.5)) == rgba(rl.WHITE)


@pytest.mark.parametrize('held_fields', [{'status': 'display_hold'}, {'sign_recovering': True}])
def test_new_held_value_does_not_start_or_inherit_a_highlight(held_fields):
  pulse = SpeedLimitPulse()
  pulse.update(display_state(), 10.)
  held = replace(display_state(25.), speed_limit=replace(display_state(25.).speed_limit, **held_fields))
  pulse.update(held, 10.5)
  assert rgba(pulse.color(rl.WHITE, 10.5)) == rgba(rl.WHITE)


@pytest.mark.parametrize('kind', [ObservationKind.STALE, ObservationKind.ABSENT, ObservationKind.UNKNOWN])
def test_same_retained_sign_finishes_all_three_purple_pulses(kind):
  pulse = SpeedLimitPulse()
  current = display_state()
  pulse.update(current, 10.)
  retained = replace(current, speed_limit=replace(current.speed_limit, kind=kind, speed_limit_mps=None,
                     last_received_speed_limit_mps=20.))
  for now in (10.5, 11.5, 12.5):
    pulse.update(retained, now)
    assert rgba(pulse.color(rl.WHITE, now)) == (188, 132, 255, 255)
  pulse.update(current, 13.5)
  assert rgba(pulse.color(rl.WHITE, 13.5)) == rgba(rl.WHITE)


def test_late_automatic_observation_gets_three_complete_visible_pulses():
  pulse = SpeedLimitPulse()
  current = state()
  pulse.update(current, 11.4)
  # Controller timing governs adoption, not the local visual notification.
  for now in (11.9, 12.9, 13.9):
    pulse.update(current, now)
    assert rgba(pulse.color(rl.WHITE, now)) == (188, 132, 255, 255)
  pulse.update(current, 14.4)
  assert rgba(pulse.color(rl.WHITE, 14.4)) == rgba(rl.WHITE)


def test_repeated_pending_timestamp_changes_do_not_restart_purple_pulse():
  pulse = SpeedLimitPulse()
  current = state()
  pulse.update(current, 10.)
  for now in (10.5, 11.5, 12.5):
    changed = replace(current, speed_limit=replace(current.speed_limit, pending_since_ns=int(now * 1e9)))
    pulse.update(changed, now)
    assert rgba(pulse.color(rl.WHITE, now)) == (188, 132, 255, 255)
  pulse.update(current, 13.5)
  assert rgba(pulse.color(rl.WHITE, 13.5)) == rgba(rl.WHITE)
