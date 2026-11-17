"""Replay large-panel input through real widgets with graphics and effects isolated."""
from unittest.mock import patch


from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.params import Params
from openpilot.selfdrive.ui.layouts.settings import device, software
from openpilot.selfdrive.ui.layouts.settings.settings import PanelType
from openpilot.starpilot.software.preferences import automatic_downloads
from openpilot.starpilot.ui import runtime_app
from openpilot.starpilot.ui.device_state import DeviceState
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.preview_home import reference_state
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.settings_state import Destination, SettingsState
from openpilot.starpilot.ui.shell import ShellInput, ShellMode, ShellSnapshot
from openpilot.starpilot.ui.software_state import SoftwareState
from openpilot.system.ui.lib.application import gui_app, MouseEvent, MousePos
from openpilot.system.ui.widgets import DialogResult
from openpilot.system.ui.widgets import list_view
from openpilot.system.ui.widgets.label import Label


class NativeSettingsTests(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.params = Params(directory.name)
    self.params.put('UpdaterState', 'idle', block=True)
    self.params.put('UpdaterAvailableBranches', 'SecretGoodStarPilot,Dom', block=True)
    self.params.put('GitBranch', 'SecretGoodStarPilot', block=True)
    self.offroad = True
    self.ui = NS(params=self.params, is_offroad=lambda: self.offroad, is_onroad=lambda: not self.offroad,
                 prime_state=NS(is_paired=lambda: False), engaged=False, started=False, started_frame=0,
                 add_offroad_transition_callback=Mock())
    self.enterContext(patch.object(device, 'ui_state', self.ui))
    self.enterContext(patch.object(software, 'ui_state', self.ui))
    self.enterContext(patch.object(runtime_app, 'ui_state', self.ui))
    self.enterContext(patch.object(device, 'Params', return_value=self.params))
    self.enterContext(patch.object(gui_app, 'font', return_value=rl.Font()))
    self.enterContext(patch.object(gui_app, '_mouse_events', []))
    self.enterContext(patch.object(gui_app, '_show_touches', False))
    self.pushed = self.enterContext(patch.object(gui_app, 'push_widget'))
    self.enterContext(patch.object(list_view, 'measure_text_cached', return_value=rl.Vector2(100, 50)))
    self.enterContext(patch.object(list_view, 'gui_label'))
    self.enterContext(patch.object(list_view, 'HtmlRenderer', side_effect=lambda *a, **kw: Mock(elements=[])))
    self.enterContext(patch.object(Label, '_render'))
    self.enterContext(patch('openpilot.system.ui.widgets.device', NS(awake=True)))
    for name in ('draw_rectangle_rounded', 'draw_circle', 'draw_line', 'draw_text_ex', 'begin_scissor_mode', 'end_scissor_mode'):
      self.enterContext(patch.object(rl, name))
    self.enterContext(patch.object(rl, 'get_mouse_wheel_move', return_value=0))
    self.enterContext(patch.object(rl, 'get_frame_time', return_value=1 / 60))
    self.software = software.SoftwareLayout()
    self.device = device.DeviceLayout()
    self.bounds = rl.Rectangle(550, 25, 1560, 1030)
    self.snapshot = ShellSnapshot(ShellMode.SETTINGS, reference_state(), SettingsState(),
                                  OnroadState(False, False, None, None, SpeedLimitObservation()),
                                  device=DeviceState(offroad=False, available_actions=frozenset()),
                                  software=SoftwareState(available_actions=frozenset()))

  def event(self, panel, x, y, *, pressed=False, down=False, released=False, t=1):
    gui_app._mouse_events = [MouseEvent(MousePos(x, y), 0, pressed, released, down, t)]
    panel.render(self.bounds)
    gui_app._mouse_events = []

  def click(self, panel, item):
    panel.render(self.bounds)
    rect = item.get_right_item_rect(item.rect)
    point = rect.x + rect.width - 100, item.rect.y + 85
    self.event(panel, *point, pressed=True, down=True)
    self.event(panel, *point, released=True, t=1.1)

  def test_desk_buttons_and_scrolling_use_native_widgets(self):
    with patch.object(software.subprocess, 'run') as signal:
      self.click(self.software, self.software._download_btn)
      signal.assert_any_call("pkill -SIGUSR1 -f openpilot.system.updated.updated", shell=True)
    self.assertEqual(sum(call.args == ("pkill -SIGUSR1 -f openpilot.system.updated.updated",) for call in signal.call_args_list), 1)
    self.software._waiting_for_updater = False
    with patch.object(software, 'MultiOptionDialog', return_value='branch chooser'):
      self.click(self.software, self.software._branch_btn)
    self.pushed.assert_called_once_with('branch chooser')
    camera = next(item for item in self.device._scroller._items if item.title == 'Cabin Camera')
    with patch.object(device, 'CabinCameraDialog', return_value='camera'):
      self.click(self.device, camera)
    self.pushed.assert_called_with('camera')
    for panel in (self.software, self.device):
      with self.subTest(panel=type(panel).__name__):
        panel.show_event()
        panel.render(self.bounds)
        first = next(item for item in panel._scroller._items if item.is_visible)
        before = first.rect.y
        self.event(panel, 1000, 800, pressed=True, down=True)
        self.event(panel, 1000, 770, down=True, t=1.1)
        self.event(panel, 1000, 650, down=True, t=1.2)
        self.assertLess(first.rect.y, before)
        self.assertFalse(panel._scroller.scroll_panel.is_touch_valid())
        panel.hide_event()
        panel.show_event()
        self.assertEqual(panel._scroller.scroll_panel.offset, 0)

  def test_restored_controls_and_confirmation_recheck(self):
    self.click(self.software, self.software._auto_updates_toggle)
    self.assertFalse(automatic_downloads(self.params))
    self.assertFalse(self.params.get_bool('DisableUpdates'))
    with patch.object(software.Path, 'read_text', return_value='example failure'), patch.object(software, 'ConfirmDialog') as dialog:
      self.software._on_error_log()
    self.assertEqual(dialog.call_args.args[0], 'example failure')
    with patch.object(device, 'ConfirmDialog') as dialog:
      self.device._reset_driver_monitoring_prompt()
    confirm = dialog.call_args.kwargs['callback']
    self.params.put_bool('IsRhdDetected', True, block=True)
    self.offroad = False
    confirm(DialogResult.CONFIRM)
    self.assertTrue(self.params.get_bool('IsRhdDetected'))
    self.offroad = True
    confirm(DialogResult.CANCEL)
    self.assertTrue(self.params.get_bool('IsRhdDetected'))
    self.params.put_bool('IsDriverViewEnabled', True, block=True)
    confirm(DialogResult.CONFIRM)
    self.assertTrue(self.params.get_bool('IsRhdDetected'))
    self.params.put_bool('IsDriverViewEnabled', False, block=True)
    confirm(DialogResult.CONFIRM)
    self.assertFalse(Path(self.params.get_param_path('IsRhdDetected')).exists())
    self.assertTrue(self.params.get_bool('OnroadCycleRequested'))

  def test_software_effects_recheck_native_offroad_state(self):
    with patch.object(software, 'ConfirmDialog') as dialog:
      self.software._on_uninstall()
    uninstall = dialog.call_args.kwargs['callback']
    with patch.object(software, 'MultiOptionDialog') as dialog:
      self.software._on_select_branch()
    branch = dialog.call_args.kwargs['callback']
    self.enterContext(patch.object(self.software._branch_dialog, 'selection', 'Dom', create=True))
    self.params.put_bool('UpdateAvailable', True, block=True)
    self.offroad = False
    with patch.object(software.subprocess, 'run') as signal:
      self.software._on_download_update()
      self.software._on_install_update()
      self.software._on_auto_updates_toggle(False)
      uninstall(DialogResult.CONFIRM)
      branch(DialogResult.CONFIRM)
    signal.assert_not_called()
    self.assertFalse(self.params.get_bool('DoReboot'))
    self.assertFalse(self.params.get_bool('DoUninstall'))
    self.assertFalse(self.params.get('UpdaterTargetBranch'))
    self.assertTrue(automatic_downloads(self.params))

  def test_native_leaf_input_cannot_also_emit_shell_requests(self):
    requests = []
    handler = ShellInput(Profile.LARGE, requests.append, native_panels=(Destination.DEVICE, Destination.SOFTWARE))
    for destination in (Destination.DEVICE, Destination.SOFTWARE):
      snapshot = replace(self.snapshot, selected=destination)
      handler.press(1980, 650, 1, snapshot)
      handler.move(1985, 650, 1.1, snapshot)
      handler.release(1985, 650, 1.2, snapshot)
    self.assertEqual(requests, [])
    handler.press(250, 160, 2, snapshot)
    handler.release(250, 160, 2.1, snapshot)
    self.assertEqual(requests[0].source, 'settings')

  def test_session_mounts_native_panels_inside_shared_rail(self):
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session.slc_actions = None
    session.profile = Profile.LARGE
    session.view = Mock()
    session.favorites = Mock()
    session.pip_warning = Mock()
    session.pip_renderer = Mock()
    session.notice = ''
    session.network_layer = None
    with patch.object(runtime_app, 'placed_at'), patch.object(software.subprocess, 'run') as signal, \
         patch.object(device, 'CabinCameraDialog', return_value='camera'):
      for destination, panel in ((Destination.DEVICE, self.device), (Destination.SOFTWARE, self.software)):
        with self.subTest(destination=destination):
          session.snapshot = Mock(return_value=replace(self.snapshot, selected=destination))
          session.settings_layer = Mock(side_effect=lambda dest, rect, panel=panel: panel.render(rect))
          session.render(ShellMode.SETTINGS, rl.Rectangle(0, 0, 2160, 1080))
          session.settings_layer.assert_called_once()
          self.assertEqual(session.view.render.call_args.args[0].selected, Destination.NETWORK)
          self.assertEqual(panel.rect.width, 1560)
          # Replay the page too: missing parked evidence must not swallow native desk touches.
          session.selected = destination
          emitted = Mock()
          session.input = ShellInput(Profile.LARGE, emitted, native_panels=runtime_app.NATIVE_SETTINGS_PANELS)
          page = runtime_app.StarShellPage(session, ShellMode.SETTINGS)
          item = (self.software._download_btn if destination == Destination.SOFTWARE else
                  next(item for item in self.device._scroller._items if item.title == 'Cabin Camera'))
          rect = item.get_right_item_rect(item.rect)
          point = MousePos(rect.x + rect.width - 100, item.rect.y + 85)
          for pressed, released in ((True, False), (False, True)):
            gui_app._mouse_events = [MouseEvent(point, 0, pressed, released, pressed, 1)]
            page.render(rl.Rectangle(0, 0, 2160, 1080))
          gui_app._mouse_events = []
          emitted.assert_not_called()
    signal.assert_any_call("pkill -SIGUSR1 -f openpilot.system.updated.updated", shell=True)
    self.assertEqual(sum(call.args == ("pkill -SIGUSR1 -f openpilot.system.updated.updated",) for call in signal.call_args_list), 1)
    self.pushed.assert_called_once_with('camera')

  def test_layout_reuses_native_panels_and_preserves_galaxy_entry(self):
    toggles = NS(_scroller=NS(_items=[NS(title=str(index)) for index in range(9)]))
    panels = {PanelType.DEVICE: NS(instance=self.device), PanelType.SOFTWARE: NS(instance=self.software),
              PanelType.DEVELOPER: NS(instance=Mock()), PanelType.NETWORK: NS(instance=Mock()), PanelType.TOGGLES: NS(instance=toggles)}
    session = Mock()
    session.fonts.profile = Profile.LARGE

    def parent_init(layout):
      layout._layouts = {runtime_app.MainState.SETTINGS: NS(_panels=panels), runtime_app.MainState.ONROAD: Mock()}

    with patch.object(runtime_app.MainLayout, '__init__', parent_init), \
         patch.object(runtime_app, 'validate_runtime_fonts'), patch.object(runtime_app, 'validate_runtime_assets'), \
         patch.object(runtime_app, 'StarShellSession', return_value=session):
      layout = runtime_app.StarMainLayout()
    self.assertIs(layout._large_panels[Destination.DEVICE].panel, self.device)
    self.assertIs(layout._large_panels[Destination.SOFTWARE].panel, self.software)
    self.assertIs(layout._large_panels[Destination.TOGGLES].panel, toggles)
    self.assertEqual(toggles._scroller._items[2].title, 'Safe Mode')
    self.assertFalse(any(item.title == 'Right Hand Driving' for item in toggles._scroller._items),
                     'RHD belongs to the native toggle owner, not an inserted N/A placeholder')
    self.assertEqual(self.device._scroller._items[0].title, 'Galaxy')
    self.assertNotIn(self.device._power_off_btn, self.device._scroller._items)
    self.assertEqual(session.system_power_action, layout._system_power_action)
    galaxy = next(item for item in self.device._scroller._items if item.title == 'Galaxy')
    galaxy.callback()
    session.galaxy_flow.open_large.assert_called_once_with()

  def test_system_power_uses_native_confirmation_and_rechecks_before_effects(self):
    put_bool = self.params.put_bool
    self.enterContext(patch.object(self.params, 'put_bool', side_effect=lambda key, value: put_bool(key, value, block=True)))
    layout = runtime_app.StarMainLayout.__new__(runtime_app.StarMainLayout)
    layout._current_mode = runtime_app.MainState.SETTINGS
    layout.star = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    layout.star._mode = ShellMode.SETTINGS
    layout.star.selected = Destination.SYSTEM
    layout._panel_owner = Mock(return_value=self.device)
    self.enterContext(patch.object(layout, '_confirmed_offroad', side_effect=lambda: self.offroad))
    for index, key in enumerate(("DoReboot", "DoShutdown")):
      with self.subTest(key=key), patch.object(device, 'ConfirmDialog') as dialog:
        layout._system_power_action(index)
        confirm = dialog.call_args.kwargs['callback']
        confirm(DialogResult.CANCEL)
        self.assertFalse(self.params.get_bool(key))
        self.ui.engaged = True
        confirm(DialogResult.CONFIRM)
        self.assertFalse(self.params.get_bool(key))
        self.ui.engaged = False
        layout.star.selected = Destination.STAR
        confirm(DialogResult.CONFIRM)
        self.assertFalse(self.params.get_bool(key))
        layout.star.selected = Destination.SYSTEM
        layout.star.display_page = "display"
        confirm(DialogResult.CONFIRM)
        self.assertFalse(self.params.get_bool(key))
        layout.star.display_page = "system"
        layout._current_mode = runtime_app.MainState.HOME
        confirm(DialogResult.CONFIRM)
        self.assertFalse(self.params.get_bool(key))
        layout._current_mode = runtime_app.MainState.SETTINGS
        if index == 1:
          self.offroad = False
          confirm(DialogResult.CONFIRM)
          self.assertFalse(self.params.get_bool(key))
          self.offroad = True
        confirm(DialogResult.CONFIRM)
        self.assertTrue(self.params.get_bool(key))
    with patch.object(device, 'ConfirmDialog') as dialog:
      self.offroad = False
      layout._system_power_action(1)
      layout._system_power_action(-1)
      layout._system_power_action(2)
      dialog.assert_not_called()


if __name__ == '__main__':
  unittest.main()
