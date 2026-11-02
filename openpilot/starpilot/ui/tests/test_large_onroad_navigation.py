from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, PropertyMock, patch

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.ui.tests.test_runtime_layouts import runtime_app
from openpilot.selfdrive.ui.layouts import main
from openpilot.selfdrive.ui.layouts.sidebar import Colors, Sidebar, ThermalStatus
from openpilot.selfdrive.ui.layouts.settings.settings import PanelType
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation, OnroadInput
from openpilot.starpilot.ui.onroad_navigation import NavigationCard
from openpilot.starpilot.ui.navigation_state import NavigationDisplay
from openpilot.system.ui.lib.application import MouseEvent, MousePos
from openpilot.system.ui.widgets import Widget


@pytest.fixture
def large_layout():
  session = Mock(profile=runtime_app.Profile.LARGE, fonts=Mock(profile=runtime_app.Profile.LARGE))
  session.release.return_value = False

  def native_init(layout):
    Widget.__init__(layout)
    layout._sidebar = Sidebar()
    layout._sidebar.set_visible(False)
    layout._current_mode = runtime_app.MainState.ONROAD
    layout._prev_onroad = True
    settings = Mock()
    settings._panels = {panel: NS(instance=Mock()) for panel in PanelType}
    layout._layouts = {runtime_app.MainState.HOME: Mock(), runtime_app.MainState.SETTINGS: settings,
                       runtime_app.MainState.ONROAD: Mock()}
    layout._sidebar.set_callbacks(on_settings=layout._on_settings_clicked, on_flag=layout._on_bookmark_clicked)
    layout._sidebar._render = Mock()
    layout._sidebar._update_state = Mock()
    layout._pm = Mock()

  with (patch.object(runtime_app, 'validate_runtime_fonts'), patch.object(runtime_app, 'validate_runtime_assets'),
        patch.object(runtime_app.MainLayout, '__init__', native_init),
        patch.object(runtime_app.gui_app, 'texture', return_value=NS(width=100, height=100)),
        patch.object(runtime_app.gui_app, 'font', return_value=rl.Font()),
        patch('openpilot.system.ui.widgets.list_view.measure_text_cached', return_value=rl.Vector2(80, 40)),
        patch.object(runtime_app, 'button_item'),
        patch.object(runtime_app, 'StarShellSession', return_value=session),
        patch.object(runtime_app, 'ui_state', NS(is_body=False, started=True)),
        patch.object(main, 'ui_state', NS(is_body=False, started=True)),
        patch.object(type(runtime_app.gui_app), 'show_touches', new_callable=PropertyMock, return_value=False),
        patch.object(type(runtime_app.native_device), 'awake', new_callable=PropertyMock, return_value=True)):
    layout = runtime_app.StarMainLayout()
    layout.set_rect(rl.Rectangle(0, 0, 2160, 1080))
    def frame(*events):
      with patch.object(type(runtime_app.gui_app), 'mouse_events', new_callable=PropertyMock, return_value=list(events)):
        layout._render_main_content()
    def event(x, y, *, pressed=False, released=False, down=False):
      return MouseEvent(MousePos(x, y), 0, pressed, released, down, 1.0)
    frame()
    yield NS(layout=layout, session=session, frame=frame, event=event)


def tap(n, x, y, *, dx=0, dy=0):
  n.frame(n.event(x, y, pressed=True, down=True))
  n.frame(n.event(x + dx, y + dy, down=True))
  n.frame(n.event(x + dx, y + dy, released=True))
  n.frame()


@pytest.mark.parametrize('thermal_status,color', [(ThermalStatus.ok, Colors.GOOD),
                                                (ThermalStatus.overheated, Colors.DANGER),
                                                (ThermalStatus.critical, Colors.DANGER)])
@pytest.mark.parametrize('temperature,celsius,fahrenheit', [(0, '0°C', '32°F'), (65.6, '66°C', '150°F'), (107, '107°C', '225°F')])
def test_sidebar_temperature_follows_metric_setting(thermal_status, color, temperature, celsius, fahrenheit):
  with patch('openpilot.selfdrive.ui.layouts.sidebar.gui_app'):
    sidebar = Sidebar()
  assert sidebar._temp_status.value == '--'
  device_state = NS(maxTempC=temperature, thermalStatus=thermal_status)
  for metric, value in ((True, celsius), (False, fahrenheit), (True, celsius)):
    with patch('openpilot.selfdrive.ui.layouts.sidebar.ui_state', NS(is_metric=metric)):
      sidebar._update_temperature_status(device_state)
    assert (sidebar._temp_status.label, sidebar._temp_status.value) == ('TEMP', value)
    assert sidebar._temp_status.color == color


def test_background_tap_opens_settings_sidebar_and_can_close_repeatedly(large_layout):
  n = large_layout
  for _ in range(3):
    tap(n, 900, 650, dx=20, dy=23)
    assert n.layout._sidebar.is_visible
    assert (n.layout.page.rect.x, n.layout.page.rect.width) == (300, 1860)
    n.layout._sidebar._render.assert_called()
    tap(n, 900, 650)
    assert not n.layout._sidebar.is_visible
    assert (n.layout.page.rect.x, n.layout.page.rect.width) == (0, 2160)


def test_sidebar_settings_release_does_not_fall_through_to_new_page(large_layout):
  n = large_layout
  tap(n, 900, 650)
  n.frame(n.event(150, 80, pressed=True, down=True))
  n.session.render.reset_mock()
  n.session.release.reset_mock()
  n.frame(n.event(150, 80, released=True))
  assert n.layout._current_mode == runtime_app.MainState.SETTINGS
  n.session.render.assert_not_called()
  n.session.release.assert_not_called()
  n.frame()
  assert n.layout.page.mode == runtime_app.ShellMode.SETTINGS
  assert n.layout.page.rect.width == 2160
  n.layout._set_mode_for_state()
  n.frame()
  assert n.layout.page.mode == runtime_app.ShellMode.ONROAD
  assert not n.layout._sidebar.is_visible


@pytest.mark.parametrize('kind', ['claimed', 'drag', 'outside'])
def test_control_tap_and_swipes_do_not_toggle_sidebar(large_layout, kind):
  n = large_layout
  if kind == 'claimed':
    n.session.release.return_value = True
    tap(n, 900, 650)
  elif kind == 'drag':
    tap(n, 900, 650, dx=100)
  else:
    n.frame(n.event(900, 650, pressed=True, down=True))
    n.frame(n.event(2200, 650, released=True))
    n.session.cancel.assert_called()
  assert not n.layout._sidebar.is_visible


def test_background_tap_on_home_or_settings_does_not_toggle_onroad_sidebar(large_layout):
  n = large_layout
  for mode in (runtime_app.MainState.HOME, runtime_app.MainState.SETTINGS):
    n.layout._current_mode = mode
    tap(n, 900, 650)
    assert not n.layout._sidebar.is_visible


@pytest.mark.parametrize('width', [1560, 1860, 2160])
def test_wheel_and_legacy_navigation_hit_boxes_follow_resized_camera(width):
  observed = OnroadState(False, True, 10, 50, SpeedLimitObservation(), viewport_width=width,
                        experimental_available=True, experimental_action_token='test',
                        navigation=NavigationDisplay(('route', 1), 'Turn left', 'turn', 'left', 120, 1000, 90))
  # Legacy callers without an editable card retain their right-edge anchor.
  observed.customization['layouts']['large'].pop('nav_card')
  emitted = []
  wheel = OnroadInput(emitted.append)
  x = 1650 + width - 1860
  wheel.press(x, 120, observed)
  assert wheel.claimed
  wheel.release(x, 120, observed)
  assert len(emitted) == 1
  nav = NavigationCard(Mock(profile=runtime_app.Profile.LARGE))
  bounds = nav.bounds(observed)
  assert bounds.x + bounds.width < width
  assert nav.press(bounds.x + 20, bounds.y + 20, observed)
  nav.release(bounds.x + 20, bounds.y + 20, observed)
  assert nav.collapsed
  assert nav.bounds(replace(observed, viewport_width=1860)).x == 1678
