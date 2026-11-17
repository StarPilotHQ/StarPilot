from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from openpilot.common.params import Params
from openpilot.selfdrive.ui.layouts.settings import developer as large
from openpilot.selfdrive.ui.mici.layouts.settings import developer as compact
from openpilot.starpilot.ui.tests.test_ioniq6_alpha_request import Toggle
from openpilot.starpilot.state_migration import prepare_manager_start, load_snapshot
from openpilot.starpilot.aol.tests.test_pq_aol import cp_for


@pytest.mark.parametrize('module,layout', ((large, large.DeveloperLayout), (compact, compact.DeveloperLayoutMici)))
@pytest.mark.parametrize('raw', (None, b'0', b'1'))
def test_sharan_alpha_available_without_rewriting_choice(module, layout, raw):
  with TemporaryDirectory() as folder:
    params = Params(folder)
    path = Path(params.get_param_path('AlphaLongitudinalEnabled'))
    if raw is not None:
      path.write_bytes(raw)
    ui = SimpleNamespace(
      CP=cp_for(), params=params, is_release=False, engaged=False, has_longitudinal_control=False, is_offroad=lambda: True, update_params=lambda: None
    )
    panel = layout.__new__(layout)
    panel._alpha_long_toggle = Toggle()
    panel._long_maneuver_toggle = Toggle()
    panel._lat_maneuver_toggle = Toggle()
    if layout is large.DeveloperLayout:
      panel._params = params
      panel._is_release = False
      for key in ('_joystick_toggle', '_adb_toggle', '_ssh_toggle', '_ui_debug_toggle', '_cellular_disabled_toggle'):
        setattr(panel, key, Toggle())
    else:
      panel._refresh_toggles = (('AlphaLongitudinalEnabled', panel._alpha_long_toggle),)
    with patch.object(module, 'ui_state', ui):
      panel._update_toggles()
      assert panel._alpha_long_toggle.visible
      assert panel._alpha_long_toggle.state == (raw == b'1')
      assert (path.read_bytes() if path.exists() else None) == raw


@pytest.mark.parametrize('raw', (b'0', b'1'))
def test_first_start_archives_prior_alpha_without_enabling_speed_control(raw):
  with TemporaryDirectory() as folder:
    root = Path(folder)
    params = Params(str(root / 'params'))
    params.put_bool('AlphaLongitudinalEnabled', raw == b'1', block=True)
    prepare_manager_start(params, root / 'recovery', auto_migrate=True)
    assert params.get('AlphaLongitudinalEnabled') is None
    assert not params.get_bool('AlphaLongitudinalEnabled')
    snapshots = list((root / 'recovery/snapshots').iterdir())
    assert len(snapshots) == 1
    assert load_snapshot(snapshots[0])['AlphaLongitudinalEnabled'] == raw
    # Later explicit choices in the initialized namespace survive ordinary startup.
    params.put_bool('AlphaLongitudinalEnabled', raw == b'1', block=True)
    prepare_manager_start(params, root / 'recovery', auto_migrate=True)
    assert Path(params.get_param_path('AlphaLongitudinalEnabled')).read_bytes() == raw
    assert params.get_bool('AlphaLongitudinalEnabled') == (raw == b'1')
