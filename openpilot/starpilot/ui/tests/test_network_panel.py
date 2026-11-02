"""Native NetworkUI ownership in the large custom settings rail."""

from types import SimpleNamespace as NS
from contextlib import nullcontext
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.params import Params
from openpilot.starpilot.ui.network_panel import NetworkPanelBridge
from openpilot.starpilot.ui import runtime_app
from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter
from openpilot.starpilot.ui.settings_state import Destination, DestinationAvailability, SettingsAction, SettingsActionKind
from openpilot.starpilot.ui.shell import ShellMode, ShellRequest
from openpilot.starpilot.ui.tests.test_runtime_snapshot import NOW, ui_fake
from openpilot.system.ui.widgets import DialogResult, Widget
from openpilot.system.ui.widgets import network as native_network
from openpilot.system.ui.lib.wifi_manager import Network, SecurityType


NETWORK = Network("fixture", 75, SecurityType.WPA2, False)


def native_panel(manager):
  """Use real native lifecycle methods without constructing fonts or NetworkManager."""
  panel = native_network.NetworkUI.__new__(native_network.NetworkUI)
  Widget.__init__(panel)
  wifi = native_network.WifiManagerUI.__new__(native_network.WifiManagerUI)
  Widget.__init__(wifi)
  wifi._wifi_manager = manager
  wifi._action_guard = None
  wifi._networks = [NETWORK]
  advanced = native_network.AdvancedNetworkSettings.__new__(native_network.AdvancedNetworkSettings)
  Widget.__init__(advanced)
  advanced._wifi_manager = manager
  advanced._action_guard = None
  panel._wifi_panel = panel._child(wifi)
  panel._advanced_panel = panel._child(advanced)
  panel._action_guard = None
  return panel, wifi, advanced


class NetworkPanelTests(unittest.TestCase):
  def setUp(self):
    self.manager = Mock()
    self.panel, self.wifi, self.advanced = native_panel(self.manager)
    self.parked = True
    self.selected = True
    self.bridge = NetworkPanelBridge(self.panel, lambda: self.parked and self.selected)

  def test_large_network_stays_open_onroad_and_revokes_on_navigation(self):
    from openpilot.selfdrive.ui.layouts.main import MainState
    from openpilot.starpilot.ui.tests.test_runtime_snapshot import TestParkedClockDomains
    fixture = TestParkedClockDomains()
    fixture.setUp()
    fixture.ui.sm['pandaStates'][0].ignitionLine = True
    layout = runtime_app.StarMainLayout.__new__(runtime_app.StarMainLayout)
    object.__setattr__(layout, '_current_mode', MainState.SETTINGS)
    object.__setattr__(layout, 'star', NS(selected=Destination.NETWORK,
                                       connectivity_allowed=fixture.adapter.connectivity_allowed))
    bridge = NetworkPanelBridge(self.panel, layout._network_authority)
    self.assertTrue(fixture.adapter.confirmed_offroad())
    self.assertTrue(bridge.enter())
    self.wifi.forget_network(NETWORK)
    self.manager.forget_connection.assert_called_once_with('fixture')
    fixture.ui.sm['deviceState'].started = True
    fixture.ui.started = True
    self.wifi.forget_network(NETWORK)
    self.assertEqual(self.manager.forget_connection.call_count, 2)
    with patch.object(self.panel, 'render'):
      self.assertTrue(bridge.render(rl.Rectangle(550, 25, 1560, 1030)))
    layout.star.selected = Destination.STAR
    self.wifi.forget_network(NETWORK)
    self.assertEqual(self.manager.forget_connection.call_count, 2)
    self.assertFalse(bridge.render(rl.Rectangle(550, 25, 1560, 1030)))
    self.manager.set_active.assert_called_with(False)

  def test_native_child_scanning_lifecycle_is_paired_without_frame_restart(self):
    self.assertTrue(self.bridge.enter())
    self.assertTrue(self.bridge.enter())
    self.manager.set_active.assert_called_once_with(True)
    self.bridge.leave()
    self.bridge.leave()
    self.assertEqual(self.manager.set_active.call_args_list, [call(True), call(False)])
    self.assertTrue(self.bridge.enter())
    self.bridge.leave()
    self.assertEqual(self.manager.set_active.call_count, 4)

  def test_real_settings_layout_lifecycle_does_not_duplicate_network_scan(self):
    from openpilot.selfdrive.ui.layouts.settings.settings import PanelInfo, PanelType, SettingsLayout
    settings = SettingsLayout.__new__(SettingsLayout)
    Widget.__init__(settings)
    native_device = Mock()
    settings._current_panel = PanelType.DEVICE
    settings._panels = {PanelType.DEVICE: PanelInfo("Device", native_device),
                        PanelType.NETWORK: PanelInfo("Network", self.panel)}
    settings.show_event()
    self.assertTrue(self.bridge.enter())
    self.bridge.leave()
    settings.hide_event()
    self.manager.set_active.assert_has_calls([call(True), call(False)])
    self.assertEqual(self.manager.set_active.call_count, 2)
    native_device.show_event.assert_called_once()
    native_device.hide_event.assert_called_once()

  def test_offroad_loss_revokes_effect_and_stops_scan(self):
    self.assertTrue(self.bridge.enter())
    self.parked = False
    network = NETWORK
    self.wifi.forget_network(network)
    self.manager.forget_connection.assert_not_called()
    self.assertFalse(self.bridge.render(rl.Rectangle(550, 25, 1560, 1030)))
    self.manager.set_active.assert_called_with(False)
    self.assertFalse(self.bridge.enter())

  def test_native_password_and_forget_callbacks_expire_after_navigation_and_reentry(self):
    self.assertTrue(self.bridge.enter())
    network = NETWORK
    self.wifi.state = native_network.UIState.NEEDS_AUTH
    self.wifi._state_network = network
    self.wifi._password_retry = False
    self.wifi.keyboard = Mock(text="secret123")
    with patch.object(native_network.gui_app, "push_widget"):
      self.wifi._render(rl.Rectangle(0, 0, 100, 100))
    password_callback = self.wifi.keyboard.set_callback.call_args.args[0]
    self.bridge.leave()
    self.assertTrue(self.bridge.enter())
    password_callback(DialogResult.CONFIRM)
    self.manager.connect_to_network.assert_not_called()
    self.wifi.state = native_network.UIState.SHOW_FORGET_CONFIRM
    self.wifi._state_network = network
    with patch.object(native_network, "ConfirmDialog", side_effect=lambda *args, **kwargs: NS(callback=kwargs["callback"], set_text=Mock())), \
         patch.object(native_network.gui_app, "push_widget") as pushed:
      self.wifi._render(rl.Rectangle(0, 0, 100, 100))
    forget_callback = pushed.call_args.args[0].callback
    self.bridge.leave()
    forget_callback(DialogResult.CONFIRM)
    self.manager.forget_connection.assert_not_called()

  def test_native_apn_and_hidden_network_confirmations_recheck_fresh_authority(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      self.advanced._params = params
      self.advanced._keyboard = Mock(text="fixture.apn")
      self.assertTrue(self.bridge.enter())
      with patch.object(native_network.gui_app, "push_widget"):
        self.advanced._edit_apn()
      apn_callback = self.advanced._keyboard.set_callback.call_args.args[0]
      self.parked = False
      apn_callback(DialogResult.CONFIRM)
      self.assertIsNone(params.get("GsmApn"))
      self.parked = True
      self.bridge.leave()
      self.assertTrue(self.bridge.enter())
      with patch.object(native_network.gui_app, "push_widget"):
        self.advanced._connect_to_hidden_network()
      hidden_callback = self.advanced._keyboard.set_callback.call_args.args[0]
      self.bridge.leave()
      hidden_callback(DialogResult.CONFIRM)
      self.manager.connect_to_network.assert_not_called()

  def test_nested_hidden_password_from_prior_page_cannot_connect_after_reentry(self):
    self.advanced._keyboard = Mock(text="hidden-name")
    self.assertTrue(self.bridge.enter())
    with patch.object(native_network.gui_app, "push_widget"):
      self.advanced._connect_to_hidden_network()
      name_callback = self.advanced._keyboard.set_callback.call_args.args[0]
      name_callback(DialogResult.CONFIRM)
    password_callback = self.advanced._keyboard.set_callback.call_args.args[0]
    self.bridge.leave()
    self.assertTrue(self.bridge.enter())
    self.advanced._keyboard.text = "secret123"
    password_callback(DialogResult.CONFIRM)
    self.manager.connect_to_network.assert_not_called()

  def test_advanced_side_effects_are_silent_after_authority_loss(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      self.advanced._params = params
      self.advanced._prime_state = NS(get_type=lambda: 0)
      self.advanced._cell_prime_types = (0,)
      self.advanced._roaming_btn = Mock()
      self.advanced._apn_btn = Mock()
      self.advanced._cellular_metered_btn = Mock()
      self.advanced._roaming_action = Mock()
      self.advanced._tethering_action = Mock()
      self.assertTrue(self.bridge.enter())
      self.parked = False
      self.advanced._update_state()
      self.advanced._toggle_tethering()
      self.advanced._toggle_roaming()
      self.advanced._toggle_cellular_metered()
      self.advanced._toggle_wifi_metered(1)
      self.manager.set_ipv4_forward.assert_not_called()
      self.manager.set_tethering_active.assert_not_called()
      self.manager.set_current_network_metered.assert_not_called()
      self.assertIsNone(params.get("GsmRoaming"))
      self.assertIsNone(params.get("GsmMetered"))

  def test_render_failure_cleans_up_scan_and_revokes_callbacks(self):
    self.assertTrue(self.bridge.enter())
    with patch.object(self.panel, "render", side_effect=RuntimeError("render failed")):
      self.assertFalse(self.bridge.render(rl.Rectangle(550, 25, 1560, 1030)))
    self.assertFalse(self.bridge.active)
    self.manager.set_active.assert_called_with(False)
    self.wifi.forget_network(NETWORK)
    self.manager.forget_connection.assert_not_called()

  def test_default_native_caller_has_no_new_guard(self):
    manager = Mock()
    panel, wifi, _advanced = native_panel(manager)
    panel.show_event()
    wifi.forget_network(NETWORK)
    panel.hide_event()
    manager.forget_connection.assert_called_once_with("fixture")

  def test_connectivity_panels_are_available_offroad_onroad_and_without_fresh_telemetry(self):
    ui = ui_fake()
    ui.started = False
    ui.sm.messages["deviceState"].started = False
    ui.sm.messages["pandaStates"][0].ignitionLine = False
    state = RuntimeSnapshotAdapter(ui).build(ShellMode.SETTINGS, Destination.NETWORK, now_ns=NOW)
    self.assertEqual(state.selected, Destination.NETWORK)
    self.assertTrue(state.settings.destination(Destination.NETWORK).available)
    ui.started = True
    ui.sm.messages["deviceState"].started = True
    state = RuntimeSnapshotAdapter(ui).build(ShellMode.SETTINGS, Destination.NETWORK, now_ns=NOW)
    self.assertTrue(state.settings.destination(Destination.NETWORK).available)
    for service in ('deviceState', 'pandaStates'):
      ui.sm.valid[service] = False
    for destination in (Destination.NETWORK, Destination.BLUETOOTH):
      with self.subTest(destination=destination):
        state = RuntimeSnapshotAdapter(ui).build(ShellMode.SETTINGS, destination, now_ns=NOW)
        self.assertEqual(state.selected, destination)
        self.assertTrue(state.settings.destination(destination).available)

  def test_large_bluetooth_opens_and_stays_open_without_connectivity_authority(self):
    layout = runtime_app.StarMainLayout.__new__(runtime_app.StarMainLayout)
    Widget.__init__(layout)
    layout._current_mode = runtime_app.MainState.SETTINGS
    layout._network_bridge = Mock()
    layout._large_panels = {}
    layout._large_destination = None
    object.__setattr__(layout, 'star', NS(selected=Destination.BLUETOOTH, connectivity_allowed=Mock(return_value=False),
                                         fonts=NS(profile=runtime_app.Profile.LARGE)))
    object.__setattr__(layout, 'page', NS(render=Mock()))
    with patch('openpilot.starpilot.ui.bluetooth_large.BluetoothLarge') as factory:
      layout._network_destination(Destination.BLUETOOTH)
    factory.assert_called_once_with(layout.star.connectivity_allowed)
    panel = factory.return_value
    panel.show_event.assert_called_once()
    layout._render_main_content()
    self.assertEqual(layout.star.selected, Destination.BLUETOOTH)
    self.assertEqual(layout._large_destination, Destination.BLUETOOTH)
    panel.hide_event.assert_not_called()
    layout._network_destination(Destination.STAR)
    panel.hide_event.assert_called_once()

  def test_shell_selection_cancels_press_and_renders_native_pane_in_screen_coordinates(self):
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session.slc_actions = None
    session.pip_warning = Mock()
    session.favorites = Mock()
    session.selected = Destination.STAR
    session._mode = ShellMode.SETTINGS
    session.profile = runtime_app.Profile.LARGE
    object.__setattr__(session, "input", NS(cancel=Mock()))
    session._on_destination_change = Mock()
    session._snapshot_cache = None
    session.sidebar_expanded = True
    object.__setattr__(session, "view", NS(render=Mock(), settings=NS(render_rail=Mock())))
    session.notice = ""
    session.network_layer = Mock(return_value=True)
    session._emit(ShellRequest("settings", SettingsAction(SettingsActionKind.REQUEST_DESTINATION,
                                   DestinationAvailability(Destination.NETWORK, True))))
    self.assertEqual(session.selected, Destination.NETWORK)
    session.input.cancel.assert_called_once()
    session._on_destination_change.assert_called_once_with(Destination.NETWORK)
    state = RuntimeSnapshotAdapter(self._parked_ui()).build(ShellMode.SETTINGS, Destination.NETWORK, now_ns=NOW)
    session.snapshot = Mock(return_value=state)
    with patch.object(runtime_app, "placed_at", return_value=nullcontext()):
      session.render(ShellMode.SETTINGS, rl.Rectangle(10, 20, 2160, 1080))
    session.view.settings.render_rail.assert_called_once_with(state.settings, selected=Destination.NETWORK)
    rect = session.network_layer.call_args.args[0]
    self.assertEqual((rect.x, rect.y, rect.width, rect.height), (560, 45, 1560, 1030))
    def back_from_network(_rect):
      session._emit(ShellRequest("settings", SettingsAction(SettingsActionKind.CLOSE)))
      return False  # the bridge revokes authority when Back changes the destination
    session.network_layer.side_effect = back_from_network
    with patch.object(runtime_app, "placed_at", return_value=nullcontext()):
      session.render(ShellMode.SETTINGS, rl.Rectangle(10, 20, 2160, 1080))
    self.assertEqual(session.notice, "")
    self.assertEqual(session.selected, Destination.STAR)
    self.assertEqual(session._on_destination_change.call_args.args[0], Destination.STAR)
    self.assertEqual(session.view.settings.render_rail.call_args_list, [
      call(state.settings, selected=Destination.NETWORK), call(state.settings, selected=Destination.NETWORK),
    ])

  def test_shell_render_failure_returns_to_star_without_retrying_deactivated_bridge(self):
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session.slc_actions = None
    session.pip_warning = Mock()
    session.favorites = Mock()
    session.selected = Destination.NETWORK
    session._mode = ShellMode.SETTINGS
    session.profile = runtime_app.Profile.LARGE
    object.__setattr__(session, "input", NS(cancel=Mock()))
    object.__setattr__(session, "view", NS(render=Mock(), settings=NS(render_rail=Mock())))
    object.__setattr__(session, "fonts", NS(draw=Mock()))
    session._on_destination_change = Mock()
    session.notice = ""
    session.notice_until = 0.0
    session.network_layer = Mock(return_value=False)  # bridge already deactivated
    network_state = RuntimeSnapshotAdapter(self._parked_ui()).build(ShellMode.SETTINGS, Destination.NETWORK, now_ns=NOW)
    session._snapshot_cache = ((), network_state)
    session.snapshot = Mock(side_effect=lambda _mode: replace(network_state, selected=session.selected))
    with (patch.object(runtime_app, "placed_at", return_value=nullcontext()),
          patch.object(runtime_app.time, "monotonic", return_value=100.0),
          patch.object(runtime_app.rl, "draw_rectangle")):
      session.render(ShellMode.SETTINGS, rl.Rectangle(0, 0, 2160, 1080))
      self.assertEqual(session.selected, Destination.STAR)
      self.assertIsNone(session._snapshot_cache)
      self.assertIn("network panel is unavailable", session.notice)
      session.view.settings.render_rail.assert_called_once_with(network_state.settings, selected=Destination.NETWORK)
      session.input.cancel.assert_called_once()
      session._on_destination_change.assert_called_once_with(Destination.STAR)
      session.render(ShellMode.SETTINGS, rl.Rectangle(0, 0, 2160, 1080))
    session.network_layer.assert_called_once()
    session.view.settings.render_rail.assert_called_once_with(network_state.settings, selected=Destination.NETWORK)

  @staticmethod
  def _parked_ui():
    ui = ui_fake()
    ui.started = False
    ui.sm.messages["deviceState"].started = False
    ui.sm.messages["pandaStates"][0].ignitionLine = False
    return ui

  def test_native_entrypoint_and_exit_clear_retained_network_selection(self):
    from openpilot.selfdrive.ui.layouts.settings.settings import PanelType
    layout = runtime_app.StarMainLayout.__new__(runtime_app.StarMainLayout)
    layout._network_bridge = Mock()
    layout._large_destination = None
    layout._large_panels = {}
    layout._current_mode = runtime_app.MainState.HOME
    object.__setattr__(layout, "star", NS(selected=Destination.STAR, cancel=Mock(), _snapshot_cache="old"))
    object.__setattr__(layout, "page", NS(hide_event=Mock(), show_event=Mock()))
    def opened_native(_panel):
      layout._current_mode = runtime_app.MainState.SETTINGS
    with patch.object(runtime_app.MainLayout, "open_settings", side_effect=opened_native) as opened:
      layout.open_settings(PanelType.NETWORK)
    opened.assert_called_once_with(PanelType.DEVICE)
    self.assertEqual(layout.star.selected, Destination.NETWORK)
    layout._network_bridge.enter.assert_called_once()
    with patch.object(runtime_app.MainLayout, "_set_current_layout", side_effect=lambda mode: setattr(layout, "_current_mode", mode)):
      layout._set_current_layout(runtime_app.MainState.ONROAD)
    layout._network_bridge.leave.assert_called()
    self.assertEqual(layout.star.selected, Destination.STAR)
    layout.star.cancel.assert_called_once()

  def test_body_transition_releases_network_before_native_layout_change(self):
    layout = runtime_app.StarMainLayout.__new__(runtime_app.StarMainLayout)
    layout._network_bridge = Mock()
    layout._large_destination = None
    layout._current_mode = runtime_app.MainState.SETTINGS
    object.__setattr__(layout, "star", NS(selected=Destination.NETWORK, cancel=Mock(), _snapshot_cache="old"))
    object.__setattr__(layout, "page", NS(hide_event=Mock(), show_event=Mock()))
    with patch.object(runtime_app.MainLayout, "_on_body_changed",
                      side_effect=lambda: layout._set_current_layout(runtime_app.MainState.HOME)), \
         patch.object(runtime_app.MainLayout, "_set_current_layout",
                      side_effect=lambda mode: setattr(layout, "_current_mode", mode)):
      layout._on_body_changed()
    layout._network_bridge.leave.assert_called_once()
    self.assertEqual(layout.star.selected, Destination.STAR)
