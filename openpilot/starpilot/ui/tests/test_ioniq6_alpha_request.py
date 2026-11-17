"""Both native Developer layouts retain an exact stock Ioniq LONG request."""

from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.ioniq6_handoff import build_ioniq6_hda2_long_candidate
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.common.params import Params, ParamKeyFlag
from openpilot.starpilot.car.hyundai.aol import ioniq6_settings_capable, qualified_ioniq6
from openpilot.selfdrive.ui.layouts.settings import developer as large
from openpilot.selfdrive.ui.mici.layouts.settings import developer as compact


class Toggle:
  def __init__(self):
    self.action_item = self
    self.visible = None

  def set_visible(self, visible):
    self.visible = visible

  def set_enabled(self, enabled):
    self.enabled = enabled

  def set_state(self, state):
    self.state = state

  def set_checked(self, state):
    self.state = state


def stock_ioniq(offset=0):
  fp = gen_empty_fingerprint()
  fp[offset + 2].update({0x50: 16, 0x2A4: 24})
  fp[offset + 1].update({0x1CF: 8, 0x1AA: 16, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24,
                0x1BA: 24, 0x1E5: 16, 0x36A: 16})
  fp[offset][0x3A5] = 24
  return CarInterface.get_params(CAR.HYUNDAI_IONIQ_6, fp, [], False, False, False)


@pytest.mark.parametrize('module,layout', ((large, large.DeveloperLayout), (compact, compact.DeveloperLayoutMici)))
@pytest.mark.parametrize('offset', (0, 4))
def test_request_survives_capability_loss_and_recovery(module, layout, offset):
  with TemporaryDirectory() as directory:
    params = Params(directory)
    params.put_bool('AlphaLongitudinalEnabled', True, block=True)
    cp = stock_ioniq(offset)
    assert not cp.alphaLongitudinalAvailable and not cp.openpilotLongitudinalControl
    ui = SimpleNamespace(CP=cp, params=params, is_release=False, engaged=False,
                         has_longitudinal_control=False, is_offroad=lambda: True, update_params=lambda: None)
    panel = layout.__new__(layout)
    panel._alpha_long_toggle = Toggle()
    panel._long_maneuver_toggle = Toggle()
    panel._lat_maneuver_toggle = Toggle()
    if layout is large.DeveloperLayout:
      panel._params = params
      panel._is_release = False
      panel._joystick_toggle = Toggle()
      panel._adb_toggle = Toggle()
      panel._ssh_toggle = Toggle()
      panel._ui_debug_toggle = Toggle()
      panel._cellular_disabled_toggle = Toggle()
    else:
      panel._refresh_toggles = (('AlphaLongitudinalEnabled', panel._alpha_long_toggle),)
    with patch.object(module, 'ui_state', ui):
      panel._update_toggles()
      assert panel._alpha_long_toggle.visible is True
      assert params.get_bool('AlphaLongitudinalEnabled')
      fp = gen_empty_fingerprint()
      fp[offset + 2].update({0x50: 16, 0x2A4: 24})
      fp[offset + 1].update({0x1CF: 8, 0x1AA: 16, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24,
                    0x1BA: 24, 0x1E5: 16, 0x36A: 16})
      fp[offset][0x3A5] = 24
      tagged_long = build_ioniq6_hda2_long_candidate(cp, fp)
      assert tagged_long is not None and int(tagged_long.safetyConfigs[-1].safetyParam) == 0x8015
      ui.CP = tagged_long
      panel._update_toggles()
      assert panel._alpha_long_toggle.visible is True
      assert params.get_bool('AlphaLongitudinalEnabled')
      # A stale passive CP is not runtime authority and must not erase the request.
      cp.passive = True
      cp.dashcamOnly = True
      ui.CP = cp
      assert not ioniq6_settings_capable(cp) and not qualified_ioniq6(cp)
      panel._update_toggles()
      assert panel._alpha_long_toggle.visible is False
      assert params.get_bool('AlphaLongitudinalEnabled')
      cp.passive = False
      cp.dashcamOnly = False
      ui.CP = cp
      panel._update_toggles()
      assert panel._alpha_long_toggle.visible is True
      assert params.get_bool('AlphaLongitudinalEnabled')
      # A different CAN-FD button topology is not this reviewed handoff.
      cp.flags |= int(HyundaiFlags.CANFD_ALT_BUTTONS)
      ui.CP = cp
      panel._update_toggles()
      assert panel._alpha_long_toggle.visible is False
      assert params.get_bool('AlphaLongitudinalEnabled')


@pytest.mark.parametrize('module,layout', ((large, large.DeveloperLayout), (compact, compact.DeveloperLayoutMici)))
@pytest.mark.parametrize('offset', (0, 4))
def test_release_preserves_request_without_advertising_passive_authority(module, layout, offset):
  with TemporaryDirectory() as directory:
    params = Params(directory)
    params.put_bool('AlphaLongitudinalEnabled', True, block=True)
    cp = stock_ioniq(offset)
    cp.passive = True
    ui = SimpleNamespace(CP=cp, params=params, is_release=True, engaged=False,
                         has_longitudinal_control=False, is_offroad=lambda: True, update_params=lambda: None)
    panel = layout.__new__(layout)
    panel._alpha_long_toggle = Toggle()
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
    else:
      panel._refresh_toggles = (('AlphaLongitudinalEnabled', panel._alpha_long_toggle),)
    with patch.object(module, 'ui_state', ui):
      panel._update_toggles()
    assert panel._alpha_long_toggle.visible is False
    assert params.get_bool('AlphaLongitudinalEnabled')


def test_release_and_drive_cleanup_keep_explicit_alpha_preference():
  with TemporaryDirectory() as directory:
    params = Params(directory)
    params.put_bool("AlphaLongitudinalEnabled", True, block=True)
    params.put_bool("JoystickDebugMode", True, block=True)
    params.clear_all(ParamKeyFlag.DEVELOPMENT_ONLY)
    assert params.get_bool("AlphaLongitudinalEnabled")
    assert params.get_bool("JoystickDebugMode")  # This key clears at manager/offroad, not release.
    for flag in (ParamKeyFlag.CLEAR_ON_MANAGER_START, ParamKeyFlag.CLEAR_ON_ONROAD_TRANSITION,
                 ParamKeyFlag.CLEAR_ON_OFFROAD_TRANSITION):
      params.clear_all(flag)
      assert params.get_bool("AlphaLongitudinalEnabled")
      assert not params.get_bool("JoystickDebugMode")
    params.put_bool("AlphaLongitudinalEnabled", False, block=True)
    params.clear_all(ParamKeyFlag.DEVELOPMENT_ONLY)
    assert not params.get_bool("AlphaLongitudinalEnabled")
