"""State-bound SLC touch and request transport without a live PubMaster."""

from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from openpilot.starpilot.ui.onroad_state import (ObservationKind, OnroadInput, OnroadState,
                                                 SlcActionKind, SlcUiRequest, SpeedLimitObservation, slc_controls, speed_limit_bounds)
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.preview_home import reference_state
from openpilot.starpilot.ui.preview_settings import reference_settings_state
from openpilot.starpilot.ui.shell import ShellInput, ShellMode, ShellSnapshot
from openpilot.starpilot.ui.slc_action_dispatch import SlcActionDispatcher

from openpilot.starpilot.ui.onroad_customization import default_document, validate_document


NOW = 10_000_000_000


def state(*, pending: bool = True, decision: int = 7, presentation: int = 9) -> OnroadState:
  observation = SpeedLimitObservation(kind=ObservationKind.VALID, source="dashboard", speed_limit_mps=22.0,
                                      pending_speed_limit_mps=22.0 if pending else None,
                                      accepted_speed_limit_mps=18.0, session_id="drive-session",
                                      decision_id=decision, presentation_id=presentation, action_enabled=True)
  return OnroadState(True, False, 20.0, 80.0, observation,
                     longitudinal_active=True, slc_system_long_available=True)


def native_state(*, pending: bool = True, session: str = "drive-session", decision: int = 7,
                 presentation: int = 9, now_ns: int = NOW) -> NS:
  return NS(sessionId=session, frameMonoTime=now_ns, enabled=True, observationKind="valid", source="dashboard",
            speedLimit=22.0, hasPending=pending, pendingSpeedLimit=22.0 if pending else 0.0,
            hasAccepted=True, acceptedSpeedLimit=18.0, decisionId=decision, presentationId=presentation,
            actionSequenceId=0)


def tap_sign(touch, current):
  current = touch.present(current)
  bounds = speed_limit_bounds(touch.profile, current)
  assert bounds is not None
  left, top, right, bottom = bounds
  x, y = (left + right) / 2, (top + bottom) / 2
  touch.press(x, y, current)
  touch.release(x, y, current)


def tap_control(touch, current, kind):
  control = next(item for item in slc_controls(touch.profile, touch.present(current)) if item.request.kind == kind)
  left, top, right, bottom = control.bounds
  x, y = (left + right) / 2, (top + bottom) / 2
  touch.press(x, y, current)
  touch.release(x, y, current)


def test_dispatcher_can_accept_or_reject_the_presented_choice_during_a_source_gap():
  for kind in (SlcActionKind.ACCEPT, SlcActionKind.REJECT):
    current = native_state()
    current.observationKind, current.source, current.speedLimit = 'unknown', 'none', 0.
    publisher = Mock()
    dispatch = SlcActionDispatcher(publisher, lambda now, current=current: current, clock=lambda: NOW)
    request = SlcUiRequest(kind, current.sessionId, current.decisionId, current.presentationId, 22.)
    assert not dispatch.dispatch(replace(request, presentation_id=0))
    assert dispatch.dispatch(request)
    assert publisher.send.call_count == 1


class TestSlcTouch(unittest.TestCase):
  def test_padding_tolerates_drift_but_cancels_on_leaving_the_original_target(self):
    for profile in Profile:
      emitted, current = [], state()
      touch = OnroadInput(emitted.append, profile)
      bounds = speed_limit_bounds(profile, current)
      assert bounds is not None
      left, top, right, bottom = bounds
      x, y = (left + right) / 2, (top + bottom) / 2
      touch.press(x, y, current)
      touch.release(x + 12, y + 12, current)
      self.assertTrue(touch.present(current).slc_prompt_open)
      controls = slc_controls(profile, touch.present(current))
      for control in controls:
        left, top, right, bottom = control.bounds
        x, y = (left + right) / 2, (top + bottom) / 2
        for drift in (0, 6, 12):
          tap_sign(touch, current)
          touch.press(x, y, current)
          self.assertEqual(touch.pressed_action, control.request)
          touch.release(x + drift, y + drift, current)
          self.assertEqual(emitted.pop(), control.request)
        tap_sign(touch, current)
        touch.press(x, y, current)
        touch.move(right + 1, y, current)
        touch.release(x, y, current)
        self.assertFalse(emitted)
        self.assertIsNone(touch.pressed_action)
      tap_sign(touch, current)
      x = (controls[0].bounds[2] + controls[1].bounds[0]) / 2
      y = (controls[0].bounds[1] + controls[0].bounds[3]) / 2
      touch.press(x, y, current)
      touch.release(x, y, current)
      self.assertFalse(emitted)

  def test_both_profiles_offer_accept_and_reject_only_after_tapping_the_sign(self):
    for profile in Profile:
      current, emitted = state(), []
      touch = OnroadInput(emitted.append, profile)
      self.assertEqual(slc_controls(profile, current), ())
      tap_sign(touch, current)
      self.assertFalse(emitted)
      controls = slc_controls(profile, touch.present(current))
      self.assertEqual([control.label for control in controls], ['Accept', 'Reject'])
      self.assertEqual(controls[0].request.candidate_speed_mps, 22.)
      tap_control(touch, current, SlcActionKind.ACCEPT)
      self.assertEqual(emitted[-1].kind, SlcActionKind.ACCEPT)
      tap_sign(touch, current)
      tap_control(touch, current, SlcActionKind.REJECT)
      self.assertEqual(emitted[-1].kind, SlcActionKind.REJECT)

  def test_shell_routes_the_open_prompt_as_a_typed_request(self):
    emitted = []
    shell = ShellInput(Profile.LARGE, emitted.append)
    snapshot = ShellSnapshot(ShellMode.ONROAD, reference_state(), reference_settings_state(), state())
    bounds = speed_limit_bounds(Profile.LARGE, snapshot.onroad)
    assert bounds is not None
    left, top, right, bottom = bounds
    x, y = (left + right) / 2, (top + bottom) / 2
    shell.press(x, y, 0., snapshot)
    shell.release(x, y, .1, snapshot)
    control = slc_controls(Profile.LARGE, shell.onroad.present(snapshot.onroad))[0]
    left, top, right, bottom = control.bounds
    x, y = (left + right) / 2, (top + bottom) / 2
    shell.press(x, y, .2, snapshot)
    shell.release(x, y, .3, snapshot)
    self.assertEqual(emitted[0].source, 'slc')
    self.assertIsInstance(emitted[0].action, SlcUiRequest)

  def test_open_prompt_tracks_its_decision_while_old_presentation_presses_cannot_act(self):
    for profile in Profile:
      for changed in (state(decision=8), state(presentation=10), state(pending=False)):
        emitted = []
        touch = OnroadInput(emitted.append, profile)
        current = state()
        tap_sign(touch, current)
        control = slc_controls(profile, touch.present(current))[0]
        left, top, right, bottom = control.bounds
        x, y = (left + right) / 2, (top + bottom) / 2
        touch.press(x, y, current)
        touch.release(x, y, changed)
        self.assertFalse(emitted)
        self.assertEqual(touch.present(changed).slc_prompt_open,
                         changed.speed_limit.decision_id == current.speed_limit.decision_id and
                         changed.speed_limit.pending_speed_limit_mps is not None)

  def test_moving_a_target_during_a_press_cancels_the_action(self):
    import copy
    for profile in Profile:
      emitted, current = [], state()
      touch = OnroadInput(emitted.append, profile)
      tap_sign(touch, current)
      control = slc_controls(profile, touch.present(current))[0]
      left, top, right, bottom = control.bounds
      x, y = (left + right) / 2, (top + bottom) / 2
      touch.press(x, y, current)
      document = copy.deepcopy(current.customization)
      document['layouts'][str(profile)]['cruise_limits' if profile == Profile.LARGE else 'speed_limit']['x'] += 1
      touch.release(x, y, replace(current, customization=document))
      self.assertFalse(emitted)

  def test_automatic_countdown_has_no_touch_confirmation(self):
    current = state()
    current = replace(current, speed_limit=replace(current.speed_limit, automatic_adoption_pending=True))
    for profile in Profile:
      touch = OnroadInput(Mock(), profile)
      tap_sign(touch, current)
      self.assertFalse(touch.present(current).slc_prompt_open)
      self.assertFalse(slc_controls(profile, replace(current, slc_prompt_open=True)))

  def test_no_use_action_or_touch_target_for_existing_limit(self):
    for profile in Profile:
      for observation in (state(pending=False).speed_limit,
                          replace(state(pending=False).speed_limit, driver_override_active=True),
                          replace(state(pending=False).speed_limit, accepted_speed_limit_mps=22.0)):
        current = replace(state(pending=False), speed_limit=observation, cruise_kph=50.0)
        emitted = []
        touch = OnroadInput(emitted.append, profile)
        self.assertEqual(slc_controls(profile, current), ())
        bounds = speed_limit_bounds(profile, current)
        assert bounds is not None
        left, top, right, bottom = bounds
        x, y = (left + right) / 2, (top + bottom) / 2
        touch.press(x, y, current)
        touch.release(x, y, current)
        self.assertFalse(emitted)


class TestSlcDispatcher(unittest.TestCase):
  def test_publishes_schema_action_once_without_mutating_supplied_state(self):
    current = native_state()
    publisher = Mock()
    clock = [NOW]
    dispatcher = SlcActionDispatcher(publisher, lambda _: current, lambda: clock[0])
    request = SlcUiRequest(SlcActionKind.ACCEPT, "drive-session", 7, 9, 22.0)
    self.assertTrue(dispatcher.dispatch(request))
    service, message = publisher.send.call_args.args
    self.assertEqual(service, "slcAction")
    self.assertTrue(message.valid)
    self.assertEqual(str(message.slcAction.kind), "accept")
    self.assertEqual(message.slcAction.sessionId, "drive-session")
    self.assertEqual(message.slcAction.decisionId, 7)
    self.assertEqual(message.slcAction.presentationId, 9)
    self.assertGreaterEqual(message.slcAction.sequenceId, NOW)
    self.assertTrue(current.hasPending)
    self.assertFalse(dispatcher.dispatch(request))
    publisher.send.assert_called_once()

  def test_rechecks_session_decision_freshness_and_action_eligibility(self):
    current = native_state()
    publisher = Mock()
    dispatcher = SlcActionDispatcher(publisher, lambda _: current, lambda: NOW)
    request = SlcUiRequest(SlcActionKind.REJECT, "drive-session", 7, 9, 22.0)
    for mutation in ({"sessionId": "next-drive"}, {"decisionId": 8}, {"presentationId": 10},
                     {"frameMonoTime": NOW - 200_000_000}, {"hasPending": False}, {"enabled": False}):
      current = NS(**(vars(native_state()) | mutation))
      self.assertFalse(dispatcher.dispatch(request), mutation)
    publisher.send.assert_not_called()

  def test_adopt_requires_current_presentation_and_sequence_survives_restart(self):
    current = native_state(pending=False)
    current.actionSequenceId = NOW + 5
    publisher = Mock()
    dispatcher = SlcActionDispatcher(publisher, lambda _: current, lambda: NOW)
    request = SlcUiRequest(SlcActionKind.ADOPT, "drive-session", 7, 9, 22.0)
    self.assertTrue(dispatcher.dispatch(request))
    self.assertEqual(publisher.send.call_args.args[1].slcAction.sequenceId, NOW + 6)
    current = NS(**(vars(current) | {"speedLimit": 24.0}))
    self.assertFalse(dispatcher.dispatch(request))
    self.assertEqual(publisher.send.call_count, 1)

  def test_adopt_preserves_the_wheel_timestamp_on_the_wire(self):
    current = native_state(pending=False)
    request = SlcUiRequest(SlcActionKind.ADOPT, "drive-session", 7, 9, 22.0)
    wheel_ns = NOW - 30_000_000
    for options, expected in (({}, 0), ({"wheel_observed_ns": wheel_ns}, wheel_ns)):
      publisher = Mock()
      dispatcher = SlcActionDispatcher(publisher, lambda _: current, lambda: NOW)
      self.assertTrue(dispatcher.dispatch(request, **options))
      message = publisher.send.call_args.args[1]
      self.assertEqual(message.logMonoTime, NOW)
      self.assertEqual(message.slcAction.wheelAdoptMonoTime, expected)

  def test_failed_publish_does_not_acknowledge_or_mutate_state(self):
    current = native_state()
    publisher = Mock()
    publisher.send.side_effect = OSError("isolated transport unavailable")
    dispatcher = SlcActionDispatcher(publisher, lambda _: current, lambda: NOW)
    request = SlcUiRequest(SlcActionKind.ACCEPT, "drive-session", 7, 9, 22.0)
    self.assertFalse(dispatcher.dispatch(request))
    self.assertEqual(current.actionSequenceId, 0)
    self.assertTrue(current.hasPending)


def test_v10_default_migration_preserves_unrelated_customization_and_optional_widgets():
  old = default_document()
  old['version'] = 10
  old['layouts']['compact']['speed_limit_actions'] = {'x': 128, 'y': 186, 'enabled': True}
  old['layouts']['compact']['clock'].update(x=168, y=92, enabled=True)
  old['layouts']['compact']['driving_mode_descriptions'].update(x=162, y=22, enabled=True)
  old['layouts']['large']['speed_limit_actions'] = {'x': 100, 'y': 296, 'enabled': True}
  old['layouts']['large']['current_speed'].update(x=800, y=120)
  migrated = validate_document(old)
  assert all('speed_limit_actions' not in layout for layout in migrated['layouts'].values())
  assert migrated['layouts']['large']['current_speed'] == old['layouts']['large']['current_speed']
  clock = migrated['layouts']['compact']['clock']
  mode = migrated['layouts']['compact']['driving_mode_descriptions']
  assert clock['enabled'] and mode['enabled'] and mode['y'] + 44 <= 44
  assert clock['x'] >= mode['x'] + 168 and clock['y'] < 20
  assert validate_document(migrated) == migrated


def test_large_controls_with_source_card_clear_the_default_cem_gauge():
  current = replace(state(), slc_prompt_open=True)
  current.customization.update(speedSources=True, speedSourcesCard=True)
  current = replace(current, customization=validate_document(current.customization))
  controls = slc_controls(Profile.LARGE, current)
  assert len(controls) == 2
  assert controls[-1].bounds[3] < current.customization['layouts']['large']['cem_gauge']['y']


if __name__ == "__main__":
  unittest.main()
