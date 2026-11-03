"""UI request messages through the real SLC decision runtime, with no IPC."""

from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR

import openpilot.cereal.messaging as messaging
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages
from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits.runtime import Action, Runtime
from openpilot.starpilot.speed_limits.runtime_settings import parse
from openpilot.starpilot.speed_limits.tests.test_runtime_replay import ReplaySM
from openpilot.starpilot.ui.onroad_state import (OnroadInput, OnroadState, SlcActionKind, SlcUiRequest, slc_controls,
                                                speed_limit_from_message, speed_limit_bounds)
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.slc_action_dispatch import SlcActionDispatcher


START = 2_000_000_000


def replay_inputs() -> tuple[ReplaySM, object]:
  cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
  data, _ = messages()
  source = messaging.new_message("slcDashboardObservation").slcDashboardObservation
  data["slcDashboardObservation"] = source
  sm = ReplaySM(data, START)
  car = sm["carState"]
  car.vCruise = 60.0
  car.vCruiseCluster = 60.0
  car.vEgoCluster = 20.0
  sm["carControl"].enabled = True
  sm["carControl"].longActive = True
  source.producerSessionId = "card-producer"
  source.carStateLogMonoTime = START
  source.status = "valid"
  source.speedMps = 25.0
  source.observedMonoTime = START - 100_000_000
  source.validUntilMonoTime = START + 5_000_000_000
  source.episode = 1
  return sm, cp


def ui_request(message, *, profile: Profile, kind: SlcActionKind) -> SlcUiRequest:
  observation = speed_limit_from_message(message.slcState)
  view_state = OnroadState(True, False, 20.0, 60.0, observation,
                           longitudinal_active=True, slc_system_long_available=True)
  emitted: list[SlcUiRequest] = []

  def emit(request):
    if not isinstance(request, SlcUiRequest):
      raise AssertionError("Unexpected non-SLC touch request")
    emitted.append(request)

  touch = OnroadInput(emit, profile)
  if observation.pending_speed_limit_mps is not None:
    bounds = speed_limit_bounds(profile, view_state)
    assert bounds is not None
    left, top, right, bottom = bounds
    x, y = (left + right) / 2, (top + bottom) / 2
    touch.press(x, y, view_state)
    touch.release(x, y, view_state)
  control = next(item for item in slc_controls(profile, touch.present(view_state)) if item.request.kind == kind)
  left, top, right, bottom = control.bounds
  x, y = (left + right) / 2, (top + bottom) / 2
  touch.press(x, y, view_state)
  touch.release(x, y, view_state)
  if len(emitted) != 1:
    raise AssertionError("Visible SLC touch emitted no request")
  return emitted[0]


def publish_request(state, request: SlcUiRequest, now_ns: int):
  sent = []
  dispatcher = SlcActionDispatcher(NS(send=lambda service, message: sent.append((service, message))),
                                   lambda _: state, lambda: now_ns)
  if not dispatcher.dispatch(request):
    raise AssertionError("Current SLC presentation rejected the UI request")
  service, message = sent[0]
  if service != "slcAction":
    raise AssertionError("Wrong SLC action service")
  action = message.slcAction
  return Action(str(action.sessionId), int(action.sequenceId), int(action.decisionId),
                int(action.presentationId), str(action.kind))


def accepted_runtime(sm, cp, settings, *, session_id):
  runtime = Runtime(settings, session_id=session_id)
  candidate = next(obs.candidate for obs in runtime._observations(sm, cp, START).values() if obs.candidate is not None)
  accepted = acc.AcceptedLimit(candidate, session_id, 0)
  runtime.state = replace(runtime.state, acceptance=replace(acc.new_session(session_id, accepted), received_speed_mps=candidate.speed_mps))
  return runtime


class TestSlcUiRuntime(unittest.TestCase):
  def test_confirmation_requires_current_presentation_through_source_handoff(self):
    for profile in Profile:
      for kind in (SlcActionKind.ACCEPT, SlcActionKind.REJECT):
        for change in ('none', 'wrong_id', 'earlier_handoff', 'same_step_handoff', 'new_limit'):
          with self.subTest(profile=profile, kind=kind, change=change):
            sm, cp = replay_inputs()
            runtime = Runtime(parse({'SpeedLimitController': True, 'SLCConfirmation': 1,
                                     'SLCPriority1': 'Dashboard'}))
            pending = runtime.step(sm, cp, now_ns=START).message
            request = ui_request(pending, profile=profile, kind=kind)
            action = publish_request(pending.slcState, request, START + 10_000_000)
            if change == 'wrong_id':
              action = replace(action, presentation_id=action.presentation_id + 1000)
            elif change != 'none':
              sm['slcDashboardObservation'].episode += 1
              sm['slcDashboardObservation'].observedMonoTime = START + 40_000_000
              if change == 'new_limit':
                sm['slcDashboardObservation'].speedMps = 30.0
            if change == 'earlier_handoff':
              sm.advance(START + 50_000_000)
              renewed = runtime.step(sm, cp, now_ns=START + 50_000_000).message.slcState
              self.assertEqual(renewed.decisionId, pending.slcState.decisionId)
              self.assertNotEqual(renewed.presentationId, pending.slcState.presentationId)
            now_ns = START + (100_000_000 if change == 'earlier_handoff' else 50_000_000)
            sm.advance(now_ns)
            output = runtime.step(sm, cp, now_ns=now_ns, request=action)
            if change == 'none':
              self.assertEqual(output.message.slcState.actionStatus, kind.value)
              self.assertFalse(output.message.slcState.hasPending)
              self.assertEqual(output.result.ceiling is not None, kind == SlcActionKind.ACCEPT)
            else:
              self.assertTrue(output.message.slcState.hasPending)
              self.assertFalse(output.message.slcState.hasAccepted)
            self.assertEqual(runtime.ledger.transactions, ())

  def test_card_accept_produces_planner_receipt_and_cap_without_changing_set(self):
    sm, cp = replay_inputs()
    runtime = Runtime(parse({"SpeedLimitController": True, "SLCConfirmation": 1,
                             "SLCPriority1": "Dashboard"}), session_id="ui-accept")
    pending = runtime.step(sm, cp, now_ns=START).message
    self.assertTrue(pending.slcState.hasPending)
    request = ui_request(pending, profile=Profile.LARGE, kind=SlcActionKind.ACCEPT)
    self.assertEqual(request.kind, SlcActionKind.ACCEPT)
    action = publish_request(pending.slcState, request, START)
    sm.advance(START + 50_000_000)
    result = runtime.step(sm, cp, now_ns=START + 50_000_000, request=action)
    self.assertEqual(result.message.slcState.actionStatus, "accept")
    self.assertFalse(result.message.slcState.hasPending)
    self.assertGreater(result.result.ceiling.speed_mps, 60.0 / 3.6)
    self.assertAlmostEqual(sm['carState'].vCruise, 60.0)

  def test_reject_or_timeout_hides_use_and_adoption_request_still_works(self):
    for profile in Profile:
      for response in ('reject', 'timeout'):
        with self.subTest(profile=profile, response=response):
          sm, cp = replay_inputs()
          sm['slcDashboardObservation'].validUntilMonoTime = START + acc.CONFIRMATION_NS + 1_000_000_000
          runtime = Runtime(parse({"SpeedLimitController": True, "SLCConfirmation": 1,
                                   "SLCPriority1": "Dashboard"}), session_id="ui-reject")
          pending = runtime.step(sm, cp, now_ns=START).message
          action = None
          now_ns = START + acc.CONFIRMATION_NS
          if response == 'reject':
            reject = ui_request(pending, profile=profile, kind=SlcActionKind.REJECT)
            action = publish_request(pending.slcState, reject, START)
            now_ns = START + 50_000_000
          sm.advance(now_ns)
          rejected = runtime.step(sm, cp, now_ns=now_ns, request=action)
          self.assertEqual(rejected.result.acceptance.basis, 'driver_reject' if action else 'timeout_reject')
          self.assertFalse(rejected.message.slcState.hasPending)
          state = OnroadState(True, False, 20.0, 60.0, speed_limit_from_message(rejected.message.slcState),
                              longitudinal_active=True, slc_system_long_available=True)
          self.assertEqual(slc_controls(profile, state), ())
          adopt = SlcUiRequest(SlcActionKind.ADOPT, str(rejected.message.slcState.sessionId),
                               int(rejected.message.slcState.decisionId), int(rejected.message.slcState.presentationId),
                               float(rejected.message.slcState.speedLimit))
          restore = publish_request(rejected.message.slcState, adopt, now_ns)
          sm.advance(now_ns + 50_000_000)
          restored = runtime.step(sm, cp, now_ns=now_ns + 50_000_000, request=restore)
          self.assertEqual(restored.message.slcState.actionStatus, "adopt")
          self.assertAlmostEqual(restored.result.ceiling.speed_mps, 25.0)

  def test_adoption_request_still_works_with_lower_selected_cruise(self):
    sm, cp = replay_inputs()
    runtime = accepted_runtime(sm, cp, parse({"SpeedLimitController": True, "SLCPriority1": "Dashboard"}), session_id="ui-readopt")
    shown = runtime.step(sm, cp, now_ns=START).message
    self.assertTrue(shown.slcState.hasAccepted)
    self.assertAlmostEqual(shown.slcState.acceptedSpeedLimit, shown.slcState.speedLimit)
    request = SlcUiRequest(SlcActionKind.ADOPT, str(shown.slcState.sessionId), int(shown.slcState.decisionId),
                           int(shown.slcState.presentationId), float(shown.slcState.speedLimit))
    action = publish_request(shown.slcState, request, START)
    sm.advance(START + 50_000_000)
    result = runtime.step(sm, cp, now_ns=START + 50_000_000, request=action)
    self.assertEqual(result.message.slcState.actionStatus, "adopt")
    self.assertIsNotNone(result.result.ceiling)

  def test_projection_does_not_claim_the_removed_use_button(self):
    from openpilot.starpilot.system.starport.projection_onroad import ProjectionOnroad
    from openpilot.starpilot.ui.onroad_state import slc_action_bounds

    sm, cp = replay_inputs()
    runtime = accepted_runtime(sm, cp, parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard'}), session_id='projection')
    shown = runtime.step(sm, cp, now_ns=START).message
    state = OnroadState(True, False, 20.0, 60.0, speed_limit_from_message(shown.slcState),
                        longitudinal_active=True, slc_system_long_available=True)
    emitted = []
    view = ProjectionOnroad.__new__(ProjectionOnroad)
    view.native = NS(ui_state=NS(started=True))
    view._state = state
    view._slc_input = OnroadInput(emitted.append, Profile.LARGE)
    first, last = slc_action_bounds(Profile.LARGE, state)
    x, y = (first[0] + last[2]) / 2, (first[1] + last[3]) / 2
    self.assertFalse(view._slc_touch('down', x, y))
    self.assertFalse(view._slc_touch('up', x, y))
    self.assertFalse(emitted)

  def test_old_press_cannot_publish_after_new_runtime_presentation_or_staleness(self):
    sm, cp = replay_inputs()
    runtime = Runtime(parse({"SpeedLimitController": True, "SLCConfirmation": 1,
                             "SLCPriority1": "Dashboard"}), session_id="ui-stale")
    pending = runtime.step(sm, cp, now_ns=START).message
    old_touch = ui_request(pending, profile=Profile.LARGE, kind=SlcActionKind.ACCEPT)
    publisher = Mock()
    stale = SlcActionDispatcher(publisher, lambda _: pending.slcState, lambda: START + 200_000_000)
    self.assertFalse(stale.dispatch(old_touch))
    source = sm["slcDashboardObservation"]
    source.speedMps = 30.0
    source.observedMonoTime = START + 40_000_000
    source.episode = 2
    sm.advance(START + 50_000_000)
    changed = runtime.step(sm, cp, now_ns=START + 50_000_000).message
    self.assertNotEqual(changed.slcState.presentationId, pending.slcState.presentationId)
    fresh = SlcActionDispatcher(publisher, lambda _: changed.slcState, lambda: START + 50_000_000)
    self.assertFalse(fresh.dispatch(old_touch))
    publisher.send.assert_not_called()


if __name__ == "__main__":
  unittest.main()
