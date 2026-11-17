from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from openpilot.starpilot.favorites.actions import (
  BOOKMARK, INCREASE_SPEED, DECREASE_SPEED, TRAFFIC, SWITCHBACK, SCREEN_OFF, mapped_actions,
)
from openpilot.starpilot.ui.onroad import axis_status_color
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation


def test_catalog_contains_real_native_mode_speed_and_bookmark_callbacks_only():
  actions = mapped_actions(lambda *_: NS(rows=[]), Mock(), lambda: NS(rows=[]), Mock())
  for key in (BOOKMARK, INCREASE_SPEED, DECREASE_SPEED, TRAFFIC, SWITCHBACK, SCREEN_OFF):
    assert key in actions
    assert not actions[key].available  # Native session installs qualified callbacks.
  assert '__starpilot_controller_action__:force_coasting' not in actions
  assert '__starpilot_controller_action__:engage_openpilot' not in actions


def test_border_distinguishes_traffic_switchback_manual_chill_and_experimental():
  state = OnroadState(True, True, 15, 80, SpeedLimitObservation())
  state = replace(state, lateral_active=True, longitudinal_active=True)
  def color():
    result = axis_status_color(state)
    return result.r, result.g, result.b
  state = replace(state, traffic_mode=True)
  assert color() == (201, 34, 49)
  state = replace(state, switchback_mode=True)
  assert color() == (139, 108, 197)
  state = replace(state, traffic_mode=False, switchback_mode=False)
  state = replace(state, conditional_effective=NS(choice=ModeChoice.CEM, reason='manual_chill', effective_experimental=False))
  assert color() == (255, 214, 0)
  for reason in ('manual_experimental', 'automatic'):
    state = replace(state, conditional_effective=NS(choice=ModeChoice.CEM, reason=reason, effective_experimental=True))
    assert color() == (218, 111, 37)
  state = replace(state, longitudinal_overridden=True)
  assert color() == (145, 155, 149)


def test_actual_native_callbacks_recheck_safety_and_current_producer(monkeypatch):
  from openpilot.starpilot.ui import runtime_app
  from openpilot.selfdrive.ui import ui_state as device_module
  from openpilot.starpilot.controllers.tests.test_wheel_actions import NOW, services
  cp = NS(carFingerprint='qualified', flags=0, pcmCruise=True)
  sm = services(cp)
  sm.recv_frame = dict.fromkeys(sm, 2)
  monkeypatch.setattr(runtime_app.time, 'monotonic_ns', lambda: NOW)
  params = NS(get_bool=lambda _: False)
  ui = NS(CP=cp, params=params, sm=sm, has_longitudinal_control=True, personality=0, started_frame=1, started=True)
  state = OnroadState(True, True, 15, 80, SpeedLimitObservation())
  session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
  session._mode = runtime_app.ShellMode.ONROAD
  session.snapshot = Mock(return_value=NS(onroad=state))
  session._favorite_authority = Mock(return_value=True)
  session._conditional_favorite_active = Mock(return_value=False)
  session.mode_actions = Mock()
  session.mode_actions.dispatch.return_value = True
  bookmark, personality, experimental, sender = Mock(return_value=True), Mock(), Mock(), Mock()
  device = NS(awake=True, toggle_screen_off=Mock(return_value=True))
  with patch.object(runtime_app, 'ui_state', ui), patch.object(device_module, 'device', device), \
       patch.object(runtime_app, 'mode_action_authority', return_value=True), \
       patch.object(runtime_app, 'producer_available', return_value=True), \
       patch.object(runtime_app, '_slc_action_publisher', return_value=sender):
    actions = session.native_favorite_actions(bookmark, personality, experimental)
    assert actions[BOOKMARK].invoke()
    assert actions[SCREEN_OFF].invoke()
    assert actions[TRAFFIC].invoke()
    assert actions[SWITCHBACK].invoke()
    assert [call.args[0] for call in session.mode_actions.dispatch.call_args_list] == ['traffic', 'switchback']
    session._favorite_authority.return_value = False
    assert not actions[SWITCHBACK].invoke()
    assert session.mode_actions.dispatch.call_count == 2
    bookmark.assert_called_once()
    device.toggle_screen_off.assert_called_once()


def test_alpha_off_wheel_editor_restricts_choices_and_rechecks_actual_cp():
  import tempfile
  from openpilot.common.params import Params
  from opendbc.car.car_helpers import interfaces
  from opendbc.car.hyundai.values import CAR, HyundaiFlags
  from openpilot.starpilot.ui.conditional_feature import ConditionalFeature
  from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
  with tempfile.TemporaryDirectory() as directory:
    params = Params(directory)
    cp = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
    cp.openpilotLongitudinalControl = False
    cp.flags = int(HyundaiFlags.CANFD_LKA_STEER_MSG)
    owner = NS(params=params, authority=lambda group: group == 'switchback_wheel',
               vehicle_params=lambda: cp, vehicle_fingerprint=lambda: cp.carFingerprint)
    feature = ConditionalFeature(owner)
    rows = feature.wheel_rows()
    assert len(rows) == 6
    row = rows[0]
    assert row.available and row.choices == ('Off', 'Switchback Mode')
    request = FeatureSettingsRequest(row.key, row.source, 'Switchback Mode', dependencies=row.dependencies,
      vehicle_fingerprint=row.vehicle_fingerprint, capability=row.capability)
    assert feature.apply(request)
    from pathlib import Path
    assert Path(params.get_param_path('ModeButtonControl')).read_bytes() == b'7'
    new_row = feature.wheel_rows()[0]
    assert not feature.apply(replace(request, expected=new_row.source, dependencies=new_row.dependencies,
                                     value='Toggle traffic mode'))
    cp.carFingerprint = 'unknown'
    assert not feature.apply(request)
    assert feature.wheel_rows() == []
