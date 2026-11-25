import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from openpilot.starpilot.ui import onroad
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile


def _view(profile, registered):
  fonts = Mock(spec=BitmapFonts, **vars(NS(profile=profile)))
  with patch.object(onroad.gui_app, 'add_render_prepare', side_effect=lambda cb, cleanup: registered.append(cb)):
    return onroad.OnroadView(fonts, Path('/unused'))


class TestEngagedAssetPrewarm(unittest.TestCase):
  """First-engage GPU work (path gradient shader, wheel texture) must happen at UI start.

  On-device it stalled card 16-33 ms on the first engage of a drive, expiring the
  30 ms AOL intent wire and raising controlsMismatch (TAKE CONTROL IMMEDIATELY).
  """

  def _check(self, profile, wheel_attr):
    registered = []
    view = _view(profile, registered)
    self.assertIn(view._prepare_engaged_assets, registered)
    wheel = Mock()
    setattr(view, wheel_attr, wheel)
    shader = Mock()
    with patch.object(onroad.ShaderState, 'get_instance', return_value=shader):
      view._prepare_engaged_assets()
      view._prepare_engaged_assets()  # runs every frame; work happens once
    shader.initialize.assert_called_once_with()
    wheel.prepare.assert_called_once_with()

  def test_compact_prewarms_path_shader_and_wheel(self):
    self._check(Profile.COMPACT, 'compact_hud')

  def test_large_prewarms_path_shader_and_wheel(self):
    self._check(Profile.LARGE, 'steering_wheel')

  def test_prewarm_failure_falls_back_to_lazy_render(self):
    view = _view(Profile.COMPACT, [])
    view.compact_hud = Mock(**{'prepare.side_effect': ValueError('art')})
    shader = Mock(**{'initialize.side_effect': RuntimeError('gl')})
    with patch.object(onroad.ShaderState, 'get_instance', return_value=shader):
      view._prepare_engaged_assets()
    view.compact_hud.prepare.assert_called_once_with()

  def test_close_unregisters_prewarm(self):
    view = _view(Profile.COMPACT, [])
    with patch.object(onroad.gui_app, 'remove_render_prepare') as remove, patch.object(view, 'compact_hud'), \
         patch.object(view, 'torque_bar'), patch.object(view, 'navigation'), patch.object(view, '_corner_cache'):
      view.close()
    remove.assert_any_call(view._prepare_engaged_assets)


if __name__ == '__main__':
  unittest.main()
