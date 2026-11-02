"""Offline shell requests and supplied-observation semantics."""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path

from openpilot.starpilot.ui.onroad_state import ObservationKind, speed_limit_from_message
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.device_state import DeviceRequest
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile
from openpilot.starpilot.ui.preview_home import reference_state
from openpilot.starpilot.ui.settings_state import Destination, SettingsState
from openpilot.starpilot.ui.shell import ShellInput, ShellMode, ShellSnapshot, ShellView
from openpilot.starpilot.ui.toggles_state import Personality, ToggleKey, TogglesInput, TogglesState


def make_feature_view(profile: Profile):
  fonts = BitmapFonts.__new__(BitmapFonts)
  fonts.profile = profile
  from openpilot.starpilot.ui.feature_settings import FeatureSettingsView
  return FeatureSettingsView(fonts)


class ShellActionTests(unittest.TestCase):
  def test_sidebar_swipes_page_each_feature_pane_and_preserve_navigation_taps(self):
    from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsState
    from openpilot.starpilot.ui.settings_state import SettingsActionKind

    rows = (FeatureRow("action", "Action", "", actions=(("OPEN", True),), available=True),) * 7
    base = ShellSnapshot(ShellMode.SETTINGS, reference_state(), SettingsState(),
                         OnroadState(False, False, None, None, SpeedLimitObservation()))
    for destination, pane in ((Destination.DRIVING_CONTROLS, "features"), (Destination.SOUNDS, "sounds"),
                               (Destination.APPEARANCE, "appearance"), (Destination.SYSTEM, "display"),
                               (Destination.DRIVING_MODEL, "models")):
      with self.subTest(destination=destination):
        snapshot = replace(base, selected=destination, **{pane: FeatureSettingsState(rows=rows, scroll=5)})
        requests = []
        touch = ShellInput(Profile.LARGE, requests.append)
        touch.press(200, 450, 1, snapshot)
        touch.move(900, 450, 1.05, snapshot)
        touch.release(900, 450, 1.1, snapshot)
        touch.release(900, 450, 1.1, snapshot)
        self.assertEqual([(r.source, r.action.kind, r.action.direction) for r in requests], [(pane, "scroll", -1)])
        for end in ((220, 470), (350, 450)):
          requests.clear()
          touch.press(200, 450, 2, snapshot)
          touch.move(*end, 2.05, snapshot)
          self.assertEqual(getattr(touch, pane).drag_x, 0)
          touch.release(*end, 2.1, snapshot)
          self.assertEqual(len(requests), 1)
          self.assertEqual(requests[0].action.kind, SettingsActionKind.REQUEST_DESTINATION)
          self.assertEqual(requests[0].action.destination.destination, Destination.DEVICE)
        requests.clear()
        touch.press(200, 450, 3, snapshot)
        touch.move(900, 450, 3.05, replace(snapshot, selected=Destination.STAR))
        touch.release(900, 450, 3.1, snapshot)
        self.assertFalse(requests)

  def test_runtime_transition_interruptions_preserve_row_bindings_for_every_feature_pane(self):
    from openpilot.starpilot.ui import runtime_app
    from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsState

    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)

    session.slc_actions = None
    session.profile = Profile.LARGE
    session.favorites = Mock()
    session.view = ShellView.__new__(ShellView)
    session.view.onroad = Mock()
    requests = []
    session.input = ShellInput(Profile.LARGE, requests.append)
    rows = (FeatureRow("action", "Action", "", actions=(("OPEN", True),), available=True),) * 15
    start = FeatureSettingsState(rows=rows)
    end = replace(start, scroll=5)
    base = ShellSnapshot(ShellMode.SETTINGS, reference_state(), SettingsState(),
                         OnroadState(False, False, None, None, SpeedLimitObservation()))
    with patch("openpilot.starpilot.ui.feature_settings.time.monotonic", return_value=0):
      for destination, (state_name, _, input_name) in runtime_app._FEATURE_SETTINGS_PANES.items():
        with self.subTest(destination=destination):
          feature_view = make_feature_view(Profile.LARGE)
          setattr(session.view, state_name, feature_view)
          snapshot = replace(base, selected=destination, **{state_name: end})
          session.selected = destination
          session._settings_press_snapshot = Mock(return_value=snapshot)
          session._settings_gesture_snapshot = Mock(return_value=snapshot)
          feature_view._page_frame(start, 0, False)
          feature_view._page_frame(end, 0, False)
          requests.clear()
          session.press(ShellMode.SETTINGS, 1930, 200)
          session.release(ShellMode.SETTINGS, 1930, 200)
          self.assertFalse(requests)
          self.assertIsNone(getattr(session.input, input_name).held)
          self.assertIsNone(feature_view._transition)
          feature_view._page_frame(end, 0, False)
          session.press(ShellMode.SETTINGS, 1930, 200)
          session.release(ShellMode.SETTINGS, 1930, 200)
          self.assertEqual([(request.source, request.action.kind) for request in requests], [(input_name, "action")])
          for direction in (1, -1):
            for distance, duration, accepted in ((200, .5, True), (80, .075, True), (80, .5, False)):
              with self.subTest(direction=direction, distance=distance, duration=duration):
                requests.clear()
                feature_view.reset()
                feature_view._page_frame(replace(start, scroll=0 if direction == 1 else 10), 0, False)
                feature_view._page_frame(end, 0, False)
                self.assertIsNotNone(feature_view._transition)
                x = 1930 if direction == 1 else 1800
                session.press(ShellMode.SETTINGS, x, 200)
                for timestamp, fraction in ((0, 0), (duration / 2, .5), (duration, 1)):
                  session.move(ShellMode.SETTINGS, x - direction * distance * fraction, 200, timestamp=timestamp)
                session.release(ShellMode.SETTINGS, x - direction * distance, 200, timestamp=duration + .01)
                self.assertEqual([(request.source, request.action.kind, request.action.direction) for request in requests],
                                 [(input_name, "scroll", direction)] if accepted else [])
          feature_view._page_frame(start, 0, False)
          feature_view._page_frame(end, 0, False)
          session.cancel()
          self.assertIsNone(feature_view._last_state)

  def test_runtime_and_shared_feature_view_forward_selected_drag(self):
    from openpilot.starpilot.ui.raylib import rl
    from openpilot.starpilot.ui import runtime_app
    from openpilot.starpilot.ui.feature_settings import FeatureSettingsView
    from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsState

    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)

    session.slc_actions = None
    session.profile = Profile.LARGE
    session.favorites, session.pip_warning = Mock(), Mock()
    session.notice = ""
    session.settings_layer = session.network_layer = None
    session.input = ShellInput(Profile.LARGE, Mock())
    session.view = ShellView.__new__(ShellView)
    session.view.profile = Profile.LARGE
    session.view.onroad, session.view.settings, session.view.device = Mock(), Mock(), Mock()
    panes = runtime_app._FEATURE_SETTINGS_PANES
    for state_name, _, _ in panes.values():
      setattr(session.view, state_name, make_feature_view(Profile.LARGE))
    base = ShellSnapshot(ShellMode.SETTINGS, reference_state(), SettingsState(),
                         OnroadState(False, False, None, None, SpeedLimitObservation()))
    # Each controller carries a distinct displacement so a wrong pane cannot pass.
    for index, (_state_name, _, input_name) in enumerate(panes.values()):
      controller = getattr(session.input, input_name)
      controller.press(1000, 200, FeatureSettingsState())
      controller.move(920 - index, 200, FeatureSettingsState())
    with patch.object(runtime_app, "placed_at"), patch.object(FeatureSettingsView, "render", autospec=True) as feature_render, \
         patch.object(ShellView, "render", autospec=True, side_effect=ShellView.render) as shell_render:
      for destination, (state_name, _, input_name) in panes.items():
        with self.subTest(destination=destination):
          snapshot = replace(base, selected=destination)
          session.snapshot = Mock(return_value=snapshot)
          session.render(ShellMode.SETTINGS, rl.Rectangle(0, 0, 2160, 1080))
          drag = getattr(session.input, input_name).drag_x
          self.assertEqual(shell_render.call_args.kwargs["drag_x"], drag)
          self.assertEqual(getattr(session.view, state_name).pressed_action, getattr(session.input, input_name).pressed_action)
          feature_render.assert_called_with(getattr(session.view, state_name), getattr(snapshot, state_name), drag)
      feature_render.reset_mock()
      for destination in (Destination.STAR, Destination.DEVICE):
        session.snapshot = Mock(return_value=replace(base, selected=destination))
        session.render(ShellMode.SETTINGS, rl.Rectangle(0, 0, 2160, 1080))
        self.assertEqual(shell_render.call_args.kwargs["drag_x"], 0)
      feature_render.assert_not_called()
      session.profile = session.view.profile = Profile.COMPACT
      for state_name, _, _ in panes.values():
        setattr(session.view, state_name, None)
      for destination in panes:
        session.snapshot = Mock(return_value=replace(base, selected=destination))
        session.render(ShellMode.SETTINGS, rl.Rectangle(0, 0, 536, 240))
      feature_render.assert_not_called()

  def test_adapted_panels_draw_before_placed_navigation_and_notices(self):
    from openpilot.starpilot.ui.raylib import rl
    from openpilot.starpilot.ui import clip, runtime_app
    from openpilot.starpilot.ui.settings_state import DestinationAvailability

    rect = rl.Rectangle(20, 30, 2160, 1080)
    parent = rl.Rectangle(25, 35, 2140, 1060)
    for destination in (*runtime_app.NATIVE_SETTINGS_PANELS, Destination.NETWORK):
      for expanded in (True, False):
        with self.subTest(destination=destination, expanded=expanded):
          session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
          session.profile, session.slc_actions = Profile.LARGE, None
          session.favorites, session.pip_warning = Mock(), Mock()
          session.input, session.view, session.fonts = Mock(), Mock(), Mock()
          session.settings_layer, session.network_layer = Mock(), Mock(return_value=True)
          session.notice, session.notice_until = "Test notice", float("inf")
          settings = SettingsState(sidebar_expanded=expanded, availability=(DestinationAvailability(Destination.NETWORK, True),))
          snapshot = ShellSnapshot(ShellMode.SETTINGS, reference_state(), settings,
                                   OnroadState(False, False, None, None, SpeedLimitObservation()), selected=destination)
          session.snapshot = Mock(return_value=snapshot)
          def rail(*_args, **_kwargs):
            placement = clip._placement.get()
            self.assertIsNotNone(placement)
            assert placement is not None
            ox, oy, viewport, parent_clip = placement
            self.assertEqual((ox, oy, viewport.x, viewport.y, viewport.width, viewport.height), (20, 30, 25, 35, 2140, 1060))
            self.assertIs(parent_clip, parent)
          session.view.settings.render_rail.side_effect = rail
          order = Mock()
          order.attach_mock(session.view.render, "view")
          panel = session.network_layer if destination == Destination.NETWORK else session.settings_layer
          order.attach_mock(panel, "panel")
          order.attach_mock(session.view.settings.render_rail, "rail")
          order.attach_mock(session.fonts.draw, "notice")
          with patch.object(rl, "rl_push_matrix"), patch.object(rl, "rl_pop_matrix"), patch.object(rl, "rl_translatef"), \
               patch.object(rl, "begin_scissor_mode"), patch.object(rl, "end_scissor_mode"), patch.object(rl, "draw_rectangle"):
            session.render(ShellMode.SETTINGS, rect, parent)
          self.assertEqual([call[0] for call in order.mock_calls], ["view", "panel", "rail", "notice"])
          session.view.settings.render_rail.assert_called_once_with(settings, selected=destination)
          content = panel.call_args.args[-1]
          self.assertEqual((content.x, content.y, content.width, content.height),
                           (570 if expanded else 70, 55, 1560 if expanded else 2060, 1030))
          self.assertIsNone(clip._placement.get())

  def test_toggle_request_needs_same_displayed_value_on_release(self) -> None:
    requests = []
    touch = TogglesInput(requests.append)
    state = TogglesState()
    touch.press(2000, 120, state)
    touch.release(2000, 120, replace(state, enabled=False))
    self.assertEqual(requests, [])
    touch.press(2000, 120, state)
    touch.release(2000, 120, state)
    self.assertEqual(len(requests), 1)
    self.assertEqual(requests[0].key, ToggleKey.ENABLED)
    self.assertIs(requests[0].value, False)
    self.assertIs(state.enabled, True)

  def test_personality_request_is_inert(self) -> None:
    requests = []
    touch = TogglesInput(requests.append)
    state = TogglesState()
    touch.press(1350, 800, state)
    touch.release(1350, 800, state)
    self.assertEqual(requests[0].personality, Personality.AGGRESSIVE)
    self.assertEqual(state.personality, Personality.STANDARD)

  def test_below_fold_toggle_uses_supplied_scroll_and_value(self) -> None:
    requests = []
    touch = TogglesInput(requests.append)
    state = TogglesState(scroll_y=855)
    touch.press(2000, 240, state)
    touch.release(2000, 240, state)
    self.assertEqual(requests[-1].key, ToggleKey.ALWAYS_ON_DM)
    self.assertIs(requests[-1].value, True)
    self.assertIs(state.always_on_dm, False)

  def test_slc_numeric_defaults_are_not_observations(self) -> None:
    base = {"observationKind": "unknown", "source": "none", "speedLimit": 0.0, "offset": 0.0,
            "pendingSpeedLimit": 0.0, "effectiveCap": 0.0, "acceptedSpeedLimit": 0.0,
            "hasPending": False, "hasCeiling": False, "hasAccepted": False,
            "sessionId": "7", "decisionId": 8, "presentationId": 9, "status": "unavailable"}
    unknown = speed_limit_from_message(SimpleNamespace(**base))
    self.assertEqual(unknown.kind, ObservationKind.UNKNOWN)
    self.assertIsNone(unknown.speed_limit_mps)
    self.assertIsNone(unknown.pending_speed_limit_mps)
    valid = speed_limit_from_message(SimpleNamespace(**(base | {"observationKind": "valid", "source": "map",
                                                         "speedLimit": 13.4, "hasPending": True,
                                                         "pendingSpeedLimit": 14.1})))
    self.assertEqual(valid.speed_limit_mps, 13.4)
    self.assertEqual(valid.pending_speed_limit_mps, 14.1)
    self.assertIsNone(valid.effective_cap_mps)

  def test_malformed_native_slc_values_never_render_as_a_limit(self) -> None:
    base = {"observationKind": "valid", "source": "map", "speedLimit": float("nan"), "offset": 0.0,
            "pendingSpeedLimit": 0.0, "effectiveCap": 0.0, "acceptedSpeedLimit": 0.0,
            "hasPending": False, "hasCeiling": False, "hasAccepted": False,
            "sessionId": "abc123", "decisionId": 8, "presentationId": 9, "status": "live"}
    for speed in (float("nan"), float("inf"), 0.0, -1.0):
      observation = speed_limit_from_message(SimpleNamespace(**(base | {"speedLimit": speed})))
      self.assertEqual(observation.kind, ObservationKind.UNKNOWN)
      self.assertIsNone(observation.speed_limit_mps)
    stale = speed_limit_from_message(SimpleNamespace(**(base | {"observationKind": "stale"})))
    self.assertEqual(stale.kind, ObservationKind.STALE)
    self.assertIsNone(stale.speed_limit_mps)
    valid = speed_limit_from_message(SimpleNamespace(**(base | {"speedLimit": 13.4})))
    self.assertEqual(valid.session_id, "abc123")
    with self.assertRaises(ValueError):
      OnroadState(False, False, float("nan"), None, SpeedLimitObservation())

  def test_shell_navigation_cancels_press_across_panes(self) -> None:
    requests = []
    touch = ShellInput(Profile.LARGE, requests.append)
    snapshot = ShellSnapshot(ShellMode.HOME, reference_state(), SettingsState(),
                             OnroadState(False, False, 0.0, None, SpeedLimitObservation()))
    touch.press(100, 100, 0.0, snapshot)
    touch.release(100, 100, 0.1, snapshot)
    self.assertEqual(requests[-1].source, "home")
    settings = replace(snapshot, mode=ShellMode.SETTINGS)
    touch.press(200, 450, 0.2, settings)
    touch.release(200, 450, 0.3, settings)
    self.assertEqual(requests[-1].source, "settings")
    self.assertEqual(requests[-1].action.destination.destination, Destination.DEVICE)
    device = replace(settings, selected=Destination.DEVICE)
    touch.press(1900, 600, 0.4, device)
    touch.release(1900, 600, 0.5, replace(device, mode=ShellMode.ONROAD))
    self.assertEqual(len([request for request in requests if request.source == "device"]), 0)
    touch.press(1900, 600, 0.6, device)
    touch.release(1900, 600, 0.7, device)
    self.assertEqual(requests[-1].source, "device")
    self.assertEqual(requests[-1].action.request, DeviceRequest.PREVIEW_DRIVER_CAMERA)

  def test_onroad_wheel_request_requires_unchanged_supplied_state(self) -> None:
    requests = []
    touch = ShellInput(Profile.LARGE, requests.append)
    snapshot = ShellSnapshot(ShellMode.ONROAD, reference_state(), SettingsState(),
                             OnroadState(True, False, 0.0, None, SpeedLimitObservation(),
                                         experimental_available=True, experimental_enabled=False, viewport_width=1860))
    touch.press(1670, 170, 0.0, snapshot)
    touch.release(1670, 170, 0.1, replace(snapshot, onroad=replace(snapshot.onroad, experimental_enabled=True)))
    self.assertEqual(requests, [])
    touch.press(1670, 170, 0.2, snapshot)
    touch.release(1670, 170, 0.3, snapshot)
    self.assertEqual(requests[-1].source, "onroad")
    self.assertIs(requests[-1].action.value, True)

  def test_compact_never_dispatches_large_leaf_controls(self) -> None:
    requests = []
    touch = ShellInput(Profile.COMPACT, requests.append)
    snapshot = ShellSnapshot(ShellMode.SETTINGS, reference_state(), SettingsState(),
                             OnroadState(False, False, 0.0, None, SpeedLimitObservation()),
                             selected=Destination.TOGGLES)
    with (patch.object(touch.toggles, 'press') as toggles,
          patch.object(touch.device, 'press') as device,
          patch.object(touch.software, 'press') as software):
      for destination in (Destination.TOGGLES, Destination.DEVICE, Destination.SOFTWARE):
        injected = replace(snapshot, selected=destination)
        touch.press(510, 100, 0.0, injected)
        touch.release(510, 100, 0.1, injected)
      for control in (toggles, device, software):
        control.assert_not_called()
    self.assertFalse(any(request.source in {"toggles", "device", "software"} for request in requests))

  def test_compact_wheel_icon_is_not_a_large_button(self) -> None:
    requests = []
    touch = ShellInput(Profile.COMPACT, requests.append)
    snapshot = ShellSnapshot(ShellMode.ONROAD, reference_state(), SettingsState(),
                             OnroadState(True, False, 0.0, None, SpeedLimitObservation(),
                                         experimental_available=True))
    for x, y in ((46, 200), (1670, 170)):
      touch.press(x, y, 0.0, snapshot)
      touch.release(x, y, 0.1, snapshot)
    self.assertEqual(requests, [])

  def test_failed_shell_construction_closes_acquired_views(self) -> None:
    with (patch("openpilot.starpilot.ui.shell.HomeView") as home,
          patch("openpilot.starpilot.ui.shell.SettingsView") as settings,
          patch("openpilot.starpilot.ui.shell.OnroadView") as onroad,
          patch("openpilot.starpilot.ui.shell.DeviceView"),
          patch("openpilot.starpilot.ui.shell.SoftwareView"),
          patch("openpilot.starpilot.ui.shell.TogglesView") as toggles):
      settings.return_value.prepare.side_effect = RuntimeError("asset validation failed")
      with self.assertRaisesRegex(RuntimeError, "asset validation failed"):
        ShellView(Mock(profile=Profile.LARGE), Path("/unused"))
      home.return_value.close.assert_called_once()
      settings.return_value.close.assert_called_once()
      onroad.return_value.close.assert_called_once()
      toggles.return_value.close.assert_called_once()


if __name__ == "__main__":
  unittest.main()
