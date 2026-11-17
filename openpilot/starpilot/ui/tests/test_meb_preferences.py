"""Both native settings surfaces retain and expose the exact release-supported MEB option."""
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from opendbc.car.volkswagen.interface import CarInterface
from opendbc.car.volkswagen.values import CAR
from opendbc.car.volkswagen.meb_modes import prepare_stock
from openpilot.common.params import Params
from openpilot.selfdrive.ui.layouts.settings import developer as large
from openpilot.selfdrive.ui.mici.layouts.settings import developer as compact
from openpilot.starpilot.ui.tests.test_ioniq6_alpha_request import Toggle


@pytest.mark.parametrize('module,layout', ((large, large.DeveloperLayout), (compact, compact.DeveloperLayoutMici)))
@pytest.mark.parametrize('release', (False, True))
@pytest.mark.parametrize('raw,expected', ((None, True), (b'0', False), (b'1', True), (b'bad', False)))
def test_saved_meb_off_can_be_restored_on_each_native_surface(module, layout, release, raw, expected):
  with TemporaryDirectory() as directory:
    params = Params(directory)
    path = Path(params.get_param_path('AlphaLongitudinalEnabled'))
    if raw is not None:
      path.write_bytes(raw)
    fp = {bus: {} for bus in range(8)}
    fp[1][0x13D] = 32
    cp = CarInterface.get_params(CAR.VOLKSWAGEN_ID4_MK1, fp, [], False, release, False)
    prepare_stock(cp, True)
    ui = SimpleNamespace(CP=cp, params=params, is_release=release, engaged=False,
                         has_longitudinal_control=False, is_offroad=lambda: True, update_params=lambda: None)
    panel = layout.__new__(layout)
    panel._alpha_long_toggle = Toggle()
    panel._long_maneuver_toggle = Toggle()
    panel._lat_maneuver_toggle = Toggle()
    if layout is large.DeveloperLayout:
      panel._params = params
      panel._is_release = release
      panel._joystick_toggle = Toggle()
      panel._adb_toggle = Toggle()
      panel._ssh_toggle = Toggle()
      panel._ui_debug_toggle = Toggle()
      panel._cellular_disabled_toggle = Toggle()
      panel._always_hold_toggle = Toggle()
    else:
      panel._refresh_toggles = (('AlphaLongitudinalEnabled', panel._alpha_long_toggle),)
    with patch.object(module, 'ui_state', ui):
      panel._update_toggles()
      assert panel._alpha_long_toggle.visible
      assert panel._alpha_long_toggle.state == expected
      assert (path.read_bytes() if path.exists() else None) == raw
      cp.passive = True
      cp.dashcamOnly = True
      panel._update_toggles()
      assert not panel._alpha_long_toggle.visible
      assert (path.read_bytes() if path.exists() else None) == raw


@pytest.mark.parametrize('module,layout', ((large, large.DeveloperLayout), (compact, compact.DeveloperLayoutMici)))
def test_release_meb_confirmation_rechecks_offroad_and_saves_actual_choice(module, layout):
  with TemporaryDirectory() as directory:
    params = Params(directory)
    params.put_bool('AlphaLongitudinalEnabled', False, block=True)
    fp = {bus: {} for bus in range(8)}
    fp[1][0x13D] = 32
    cp = CarInterface.get_params(CAR.VOLKSWAGEN_ID4_MK1, fp, [], False, True, False)
    prepare_stock(cp, True)
    parked = [True]
    ui = SimpleNamespace(CP=cp, params=params, is_release=True, engaged=False,
                         has_longitudinal_control=False, is_offroad=lambda: parked[0], update_params=lambda: None)
    class AlphaToggle(Toggle):
      title = 'Speed control'
      description = 'Use StarPilot speed control.'
    panel = layout.__new__(layout)
    panel._alpha_long_toggle = AlphaToggle()
    panel._long_maneuver_toggle = Toggle()
    panel._lat_maneuver_toggle = Toggle()
    if layout is large.DeveloperLayout:
      panel._params = params
      panel._is_release = True
      panel._joystick_toggle = Toggle()
      panel._adb_toggle = Toggle()
      panel._ssh_toggle = Toggle()
      panel._ui_debug_toggle = Toggle()
      panel._cellular_disabled_toggle = Toggle()
      panel._always_hold_toggle = Toggle()
    else:
      panel._refresh_toggles = (('AlphaLongitudinalEnabled', panel._alpha_long_toggle),)
    confirm = []
    def large_dialog(*args, callback, **kwargs):
      confirm.append(lambda: callback(large.DialogResult.CONFIRM))
      return object()
    def compact_dialog(callback, *, system_long_default=False):
      assert system_long_default
      confirm.append(callback)
      return object()
    with (patch.object(module, 'ui_state', ui), patch.object(module.gui_app, 'push_widget'),
          patch.object(large, 'ConfirmDialog', side_effect=large_dialog),
          patch.object(compact, 'AlphaLongConfirmPage', side_effect=compact_dialog),
          patch.object(compact, 'restart_needed_callback')):
      panel._on_alpha_long_enabled(True)
      assert len(confirm) == 1 and not params.get_bool('AlphaLongitudinalEnabled')
      parked[0] = False
      confirm[0]()
      assert not params.get_bool('AlphaLongitudinalEnabled')
      parked[0] = True
      confirm[0]()
      assert params.get_bool('AlphaLongitudinalEnabled')
