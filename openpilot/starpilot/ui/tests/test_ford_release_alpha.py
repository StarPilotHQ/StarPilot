"""Both actual Developer panels expose only qualified Ford release opt-in."""
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.values import CAR
from openpilot.common.params import Params
from openpilot.starpilot.tests.ford_lifecycle_helpers import FORD_FACTORIES
from openpilot.starpilot.ui.tests.test_ioniq6_alpha_request import Toggle
from openpilot.selfdrive.ui.layouts.settings import developer as large
from openpilot.selfdrive.ui.mici.layouts.settings import developer as compact


def panel_for(module, cp, params):
  ui = SimpleNamespace(CP=cp.as_reader(), params=params, is_release=True, engaged=False, has_longitudinal_control=False,
                       is_offroad=lambda: True, update_params=lambda: None)
  layout = large.DeveloperLayout if module is large else compact.DeveloperLayoutMici
  panel = layout.__new__(layout)
  alpha = Toggle()
  alpha.title, alpha.description = 'Alpha Long', 'Existing alpha confirmation'
  attributes: list[tuple[str, object]] = [('_alpha_long_toggle', alpha), ('_long_maneuver_toggle', Toggle()),
                                        ('_lat_maneuver_toggle', Toggle())]
  if module is large:
    attributes.extend((('_params', params), ('_is_release', True)))
    attributes.extend((name, Toggle()) for name in
                      ('_joystick_toggle', '_adb_toggle', '_ssh_toggle', '_ui_debug_toggle', '_cellular_disabled_toggle', '_always_hold_toggle'))
  else:
    attributes.append(('_refresh_toggles', (('AlphaLongitudinalEnabled', alpha),)))
  for name, value in attributes:
    setattr(panel, name, value)
  return panel, ui


def ford(identity, manual, offset):
  fp = gen_empty_fingerprint()
  if not manual:
    fp[offset][0x5A] = 8
  fp[offset + 2].update({0x3D6: 8, 0x186: 8})
  return CarInterface.get_params(identity, fp, [], False, True, False)


@pytest.mark.parametrize('module', [large, compact])
@pytest.mark.parametrize('identity,base', FORD_FACTORIES)
@pytest.mark.parametrize('manual,offset', [(False, 0), (True, 0), (False, 4), (True, 4)])
def test_actual_release_panels_show_registered_ford_and_preserve_request(module, identity, base, manual, offset):
  cp = ford(identity, manual, offset)
  before = cp.to_dict()
  with TemporaryDirectory() as directory:
    params = Params(directory)
    params.put_bool('AlphaLongitudinalEnabled', True, block=True)
    panel, ui = panel_for(module, cp, params)
    with patch.object(module, 'ui_state', ui):
      panel._update_toggles()
      assert panel._alpha_long_toggle.visible is True
      cp.passive = True
      panel._update_toggles()
      assert panel._alpha_long_toggle.visible is False
      assert params.get_bool('AlphaLongitudinalEnabled')
      cp.passive = False
      ui.CP = cp.as_reader().as_builder()
      ui.CP.brand = 'hyundai'
      panel._update_toggles()
      assert panel._alpha_long_toggle.visible is False
    assert cp.to_dict() == before


@pytest.mark.parametrize('module', [large, compact])
@pytest.mark.parametrize('confirmation_state', ['valid', 'onroad', 'foreign'])
def test_actual_release_confirmation_rechecks_offroad_and_exact_ford_before_saving(module, confirmation_state):
  cp = ford(CAR.FORD_MUSTANG_MACH_E_MK1, False, 4)
  before = cp.to_dict()
  with TemporaryDirectory() as directory:
    params = Params(directory)
    panel, ui = panel_for(module, cp, params)
    callbacks = []
    def dialog(*args, **kwargs):
      callbacks.append(kwargs['callback'] if module is large else args[0])
      return object()
    confirmation = 'ConfirmDialog' if module is large else 'AlphaLongConfirmPage'
    with patch.object(module, 'ui_state', ui), patch.object(module, confirmation, side_effect=dialog), \
         patch.object(module.gui_app, 'push_widget'), patch.object(panel, '_update_toggles'):
      panel._on_alpha_long_enabled(True)
      assert len(callbacks) == 1 and not params.get_bool('AlphaLongitudinalEnabled')
      if confirmation_state == 'onroad':
        ui.is_offroad = lambda: False
      elif confirmation_state == 'foreign':
        ui.CP = cp.as_reader().as_builder()
        ui.CP.brand = 'hyundai'
      if module is large:
        callbacks[0](module.DialogResult.CONFIRM)
      else:
        with patch.object(module, 'restart_needed_callback'):
          callbacks[0]()
    assert params.get_bool('AlphaLongitudinalEnabled') == (confirmation_state == 'valid')
    assert cp.to_dict() == before
