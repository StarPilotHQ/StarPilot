"""Native Developer callbacks retain parked-only authority for manual stop holds."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpilot.common.params import Params
from openpilot.starpilot.longitudinal.enhanced_stop_hold import KEY


def test_both_native_developer_callbacks_recheck_parked_before_saving(tmp_path):
  from openpilot.selfdrive.ui.layouts.settings import developer as c3
  from openpilot.selfdrive.ui.mici.layouts.settings import developer as c4
  params = Params(str(tmp_path))
  for module, layout in ((c3, c3.DeveloperLayout), (c4, c4.DeveloperLayoutMici)):
    owner = layout.__new__(layout)
    if isinstance(owner, c3.DeveloperLayout):
      owner._params = params
    owner._always_hold_toggle = Mock() if isinstance(owner, c3.DeveloperLayout) else Mock(spec=c4.BigToggle)
    ui = SimpleNamespace(params=params, is_offroad=lambda: True)
    with patch.object(module, 'ui_state', ui):
      owner._on_always_hold(True)
      assert params.get_bool(KEY)
      ui.is_offroad = lambda: False
      owner._on_always_hold(False)
      assert params.get_bool(KEY)
      ui.is_offroad = lambda: True
      owner._on_always_hold(False)
      assert not params.get_bool(KEY)
