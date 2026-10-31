
from unittest.mock import patch
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import unittest

from openpilot.starpilot.ui.appearance_preferences import CameraViewChoice
from openpilot.starpilot.ui.onroad_favorites import OnroadFavorites
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert, OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.presentation import Profile


def setup(profile=Profile.COMPACT):
  emit = Mock(return_value=True)
  controller = OnroadFavorites(NS(profile=profile), emit)
  state = OnroadState(True, True, 15, 80, SpeedLimitObservation())
  data = {'revision': 'revision', 'configurable': True,
              'slots': [{'key': f'action{i}', 'label': f'Favorite {i}', 'enabled': True, 'show_onroad': True, 'state_label': 'Off'} for i in range(3)],
              'options': [{'key': 'new', 'label': 'New control', 'available': False}]}
  state.customization['reduceMotion'] = profile == Profile.LARGE
  controller.update(state, data, 1)
  return controller, emit, state, data


class TestOnroadFavorites(unittest.TestCase):
  def test_compact_feedback_requires_successful_release_and_keeps_result_text(self):
    controller, emit, state, data = setup()
    controller.press(80, 80, 1)
    assert controller.feedback is None
    emit.return_value = False
    controller.release(80, 80, 1.1)
    assert controller.feedback is None
    def applied(_request):
      data['slots'][0]['state_label'] = 'On'
      controller.update(state, data, 1.3)
      return True
    emit.side_effect = applied
    controller.press(80, 80, 1.2)
    controller.release(80, 80, 1.3)
    assert controller.feedback_text == ('Favorite 0', 'On')
    data['slots'][0]['state_label'] = 'Off'
    controller.update(state, data, 1.4)
    assert controller.feedback_text == ('Favorite 0', 'On')
    controller._compact_feedback(4.3)
    assert controller.feedback is None

  def test_actual_onroad_lane_favorite_toggles_saved_state_and_confirmation_twice(self):
    from unittest.mock import patch
    import tempfile
    from openpilot.common.params import Params
    from opendbc.car.honda.interface import CarInterface
    from opendbc.car.honda.values import CAR
    from openpilot.starpilot.favorites.owner import FavoritesOwner
    from openpilot.starpilot.lateral.lane_runtime import read_settings
    from openpilot.starpilot.ui import runtime_app
    from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
    from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner

    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool("LaneCentering", False, block=True)
      cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
      session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
      session._mode, session.profile, session.selected = runtime_app.ShellMode.ONROAD, Profile.COMPACT, runtime_app.Destination.STAR
      session.compact_y = session.compact_scroll_x = 0
      session.sidebar_expanded = True
      session._snapshot_cache = None
      state = OnroadState(True, True, 15, 80, SpeedLimitObservation())
      ui = NS(CP=cp, params=params, is_metric=False, sm=NS(frame=1))
      self.enterContext(patch.object(session, 'adapter', NS(ui_state=ui, build=Mock(return_value=NS(onroad=state))), create=True))
      session.confirmed_offroad = Mock(return_value=False)
      session._native_favorite_actions = dict
      session._unavailable = Mock()
      session.feature_owner = FeatureSettingsOwner(params, session._feature_authority,
        vehicle_fingerprint=lambda: cp.carFingerprint, vehicle_params=lambda: cp)
      session.appearance_owner = AppearanceOwner(params, session._favorite_authority)
      session.favorites_owner = FavoritesOwner(params, session._favorite_actions, session._favorite_authority, session._favorite_render_actions,
        current_action=lambda key: session._resolve_favorite_actions(frozenset((key,))).get(key))
      session.favorites = OnroadFavorites(NS(profile=Profile.COMPACT), session._favorite_request)
      session._favorite_read_at = None
      session._favorite_data = session._favorite_snapshot = None
      with patch.object(runtime_app, 'ui_state', ui), patch.object(runtime_app.time, 'monotonic_ns', return_value=1_000_000_000):
        revision = session.favorites_owner.snapshot().revision
        assert session.favorites_owner.configure_index(0, 'LaneCentering', revision)
        session._update_favorites(state, 1)
        # All snapshots of this frame must share the onroad presentation.
        before = session.adapter.build.call_count
        for _ in range(3):
          assert session.favorites_owner.snapshot().slots[0].available
        assert session.adapter.build.call_count == before
        session.confirmed_offroad.assert_not_called()
        catalog = Mock(wraps=session.favorites_owner.actions)
        session.favorites_owner.actions = catalog
        tap_times = []
        for index, expected in enumerate((True, False, True)):
          now = 1.1 + index * .15  # Faster than the periodic Favorites refresh.
          session.favorites.press(80, 80, now)
          assert session.favorites.feedback is None
          started = runtime_app.time.perf_counter()
          session.favorites.release(80, 80, now + .05)
          tap_times.append(runtime_app.time.perf_counter() - started)
          assert params.get_bool('LaneCentering') == expected
          assert read_settings(params).enabled == expected
          assert session.favorites.feedback_text[1] == ('On' if expected else 'Off')
        # A source change during the press must not be overwritten or falsely confirmed.
        session.favorites.press(80, 80, 1.7)
        params.put_bool('LaneCentering', False, block=True)
        session.favorites.release(80, 80, 1.75)
        assert not params.get_bool('LaneCentering')
        assert session.favorites.feedback is None
        cp.passive = True
        session._favorite_read_at = None
        session._update_favorites(state, 2)
        assert not session._favorite_snapshot.slots[0].available
        session.favorites.press(80, 80, 2.1)
        session.favorites.release(80, 80, 2.15)
        assert not params.get_bool('LaneCentering')
        assert session.favorites.feedback is None
        catalog.assert_not_called()
        print(f'Actual saved LaneCentering tap callbacks: {tap_times}')

  def test_compact_invisible_thirds_activate_same_slot_0(self):
    index = 0
    controller, emit, _, _ = setup()
    x = (index + .5) * 476 / 3
    assert controller.press(x, 80, 1)
    assert controller.release(x + 24, 80, 1.1)
    assert emit.call_args.args[0].index == index
    assert controller.mode == 'collapsed'


  def test_compact_invisible_thirds_activate_same_slot_1(self):
    index = 1
    controller, emit, _, _ = setup()
    x = (index + .5) * 476 / 3
    assert controller.press(x, 80, 1)
    assert controller.release(x + 24, 80, 1.1)
    assert emit.call_args.args[0].index == index
    assert controller.mode == 'collapsed'


  def test_compact_invisible_thirds_activate_same_slot_2(self):
    index = 2
    controller, emit, _, _ = setup()
    x = (index + .5) * 476 / 3
    assert controller.press(x, 80, 1)
    assert controller.release(x + 24, 80, 1.1)
    assert emit.call_args.args[0].index == index
    assert controller.mode == 'collapsed'




  def test_compact_maximum_travel_cannot_return_to_activate(self):
    controller, emit, _, _ = setup()
    controller.press(80, 80, 1)
    controller.move(105, 80)
    controller.release(80, 80, 1.1)
    emit.assert_not_called()


  def test_compact_crossing_third_cannot_activate(self):
    controller, emit, _, _ = setup()
    controller.press(157, 80, 1)
    controller.release(161, 80, 1.1)
    emit.assert_not_called()


  def test_actual_reverse_camera_gate_0(self):
    camera, allowed = CameraViewChoice.NONE, True
    controller, emit, state, data = setup()
    state = replace(state, reversing=True, appearance=replace(state.appearance, camera_view=camera))
    controller.update(state, data, 1.1)
    assert controller.press(80, 80, 1.2) is allowed
    emit.assert_not_called()


  def test_actual_reverse_camera_gate_1(self):
    camera, allowed = CameraViewChoice.DRIVER, True
    controller, emit, state, data = setup()
    state = replace(state, reversing=True, appearance=replace(state.appearance, camera_view=camera))
    controller.update(state, data, 1.1)
    assert controller.press(80, 80, 1.2) is allowed
    emit.assert_not_called()


  def test_actual_reverse_camera_gate_2(self):
    camera, allowed = CameraViewChoice.STANDARD, False
    controller, emit, state, data = setup()
    state = replace(state, reversing=True, appearance=replace(state.appearance, camera_view=camera))
    controller.update(state, data, 1.1)
    assert controller.press(80, 80, 1.2) is allowed
    emit.assert_not_called()




  def test_changed_context_cancels_pending_action_0(self):
    change = 'alert'
    controller, emit, state, data = setup()
    controller.press(80, 80, 1)
    if change == 'alert':
      state = replace(state, alert=OnroadAlert(size=AlertSize.FULL))
    elif change == 'camera':
      state = replace(state, camera_available=False)
    else:
      data = {**data, 'revision': 'changed'}
    controller.update(state, data, 1.1)
    controller.release(80, 80, 1.2)
    emit.assert_not_called()


  def test_changed_context_cancels_pending_action_1(self):
    change = 'camera'
    controller, emit, state, data = setup()
    controller.press(80, 80, 1)
    if change == 'alert':
      state = replace(state, alert=OnroadAlert(size=AlertSize.FULL))
    elif change == 'camera':
      state = replace(state, camera_available=False)
    else:
      data = {**data, 'revision': 'changed'}
    controller.update(state, data, 1.1)
    controller.release(80, 80, 1.2)
    emit.assert_not_called()


  def test_changed_context_cancels_pending_action_2(self):
    change = 'revision'
    controller, emit, state, data = setup()
    controller.press(80, 80, 1)
    if change == 'alert':
      state = replace(state, alert=OnroadAlert(size=AlertSize.FULL))
    elif change == 'camera':
      state = replace(state, camera_available=False)
    else:
      data = {**data, 'revision': 'changed'}
    controller.update(state, data, 1.1)
    controller.release(80, 80, 1.2)
    emit.assert_not_called()




  def test_large_corner_tap_and_diagonal_open_drawer_0(self):
    end = (50, 1000)
    controller, emit, _, _ = setup(Profile.LARGE)
    assert controller.press(50, 1000, 1)
    controller.release(*end, 1.1)
    assert controller.mode == 'shelf'
    emit.assert_not_called()


  def test_large_corner_tap_and_diagonal_open_drawer_1(self):
    end = (120, 930)
    controller, emit, _, _ = setup(Profile.LARGE)
    assert controller.press(50, 1000, 1)
    controller.release(*end, 1.1)
    assert controller.mode == 'shelf'
    emit.assert_not_called()

  def test_large_corner_reserves_monitor_and_keeps_bottom_edge_accessible(self):
    controller, emit, _, _ = setup(Profile.LARGE)
    assert not controller.press(180, 940, 1)
    assert controller.press(330, 1020, 1)
    controller.release(330, 1020, 1.1)
    assert controller.is_open
    emit.assert_not_called()

  def test_corner_gesture_cancels_if_a_widget_moves_into_its_start(self):
    controller, emit, state, data = setup(Profile.LARGE)
    assert controller.press(50, 1000, 1)
    state.customization['layouts']['large']['clock'].update(enabled=True, x=40, y=940)
    controller.update(state, data, 1.05)
    controller.release(50, 1000, 1.1)
    assert not controller.is_open
    emit.assert_not_called()

  def test_tray_keeps_long_labels_states_and_press_feedback_inside_equal_buttons(self):
    from openpilot.starpilot.ui import onroad_favorites as native
    controller, _, _, data = setup(Profile.LARGE)
    controller.fonts = NS(profile=Profile.LARGE, draw=Mock(),
                          measure=lambda text, role, size: NS(width=len(text) * size / 2, height=size),
                          vertical_ink=lambda text, role, size: (0, size),
                          horizontal_ink=lambda text, role, size: (0, len(text) * size / 2))
    data['slots'][0]['label'] = 'An intentionally long favorite label that requires more than two lines'
    controller.open(1)
    controller.press_target = ('slot', 1)
    for name in ('draw_line_ex', 'draw_circle', 'draw_circle_v', 'draw_rectangle_rounded', 'draw_rectangle_rounded_lines_ex', 'draw_ring'):
      self.enterContext(patch.object(native.rl, name))
    controller._shelf(1)
    native.rl.draw_rectangle_rounded_lines_ex.assert_called_once()
    assert native.rl.draw_rectangle_rounded.call_count == 2  # Shared tray and pressed-button fill.
    for index, (button, _) in enumerate(controller.menu_buttons()):
      texts = [call.args for call in controller.fonts.draw.call_args_list if button[0] <= call.args[3] < button[0] + button[2]]
      title = [args for args in texts if args[2] == 28]
      assert 1 <= len(title) <= 2
      if index == 0:
        assert title[-1][0].endswith('...')
      for text, role, size, x, y, _ in texts:
        measured = controller.fonts.measure(text, role, size)
        assert button[0] <= x <= x + measured.width <= button[0] + button[2]
        assert button[1] <= y <= y + measured.height <= button[1] + button[3]




  def test_large_long_press_edits_without_invoking_and_unassigns_with_revision(self):
    controller, emit, state, data = setup(Profile.LARGE)
    controller.open(1)
    blade, center = controller.menu_buttons()[0]
    controller.press(*center, 1)
    controller.update(state, data, 1.61)
    controller.release(*center, 1.62)
    assert controller.editing == 0
    emit.assert_not_called()
    x, y = blade[0] + 32, blade[1] + 32
    controller.press(x, y, 2)
    controller.release(x, y, 2.1)
    request = emit.call_args.args[0]
    assert (request.kind, request.index, request.revision) == ('unassign', 0, 'revision')


  def test_unavailable_action_can_still_be_assigned_when_configuration_allowed(self):
    controller, emit, _, data = setup(Profile.LARGE)
    data['slots'][0]['enabled'] = False
    controller.open(1)
    center = controller.menu_buttons()[0][1]
    controller.press(*center, 1)
    controller.release(*center, 1.1)
    assert controller.mode == 'picker'
    cell = controller.picker_geometry()[2][0]
    controller.press(cell[0] + 20, cell[1] + 20, 1.2)
    controller.release(cell[0] + 20, cell[1] + 20, 1.3)
    request = emit.call_args.args[0]
    assert (request.kind, request.key) == ('assign', 'new')


  def test_configuration_authority_prevents_assignment(self):
    controller, emit, _, data = setup(Profile.LARGE)
    data['configurable'] = False
    assert not controller._emit('assign', 0, 'new')
    emit.assert_not_called()


  def test_native_actions_recheck_context_and_preserve_conditional_semantics(self):
    from unittest.mock import patch
    from openpilot.starpilot.ui import runtime_app
    from openpilot.starpilot.favorites.actions import BOOKMARK, CYCLE_PERSONALITY, EXPERIMENTAL
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    self.enterContext(patch.object(session, 'selected', 'star', create=True))
    state = OnroadState(True, True, 15, 80, SpeedLimitObservation())
    self.enterContext(patch.object(session, 'adapter', NS(build=Mock(return_value=NS(onroad=state))), create=True))
    session.snapshot = Mock(return_value=NS(onroad=state))
    session._favorite_authority = Mock(return_value=True)
    session._bookmark_authority = Mock(return_value=True)
    session._conditional_favorite_active = Mock(return_value=False)
    self.enterContext(patch.object(session, 'conditional_actions', NS(context=Mock(return_value=None)), create=True))
    bookmark, personality, experimental = Mock(return_value=True), Mock(return_value=True), Mock(return_value=True)
    native = NS(CP=NS(carFingerprint='car', flags=0, pcmCruise=True), has_longitudinal_control=True, started_frame=42, personality=1,
                sm={"deviceState": NS(startedMonoTime=42)},
                params=NS(get_bool=lambda key: False))
    with patch.object(runtime_app, 'ui_state', native), patch.object(runtime_app, 'read_saved', return_value=(b'1', True)):
      actions = session.native_favorite_actions(bookmark, personality, experimental)
      assert actions[BOOKMARK].invoke()
      assert actions[CYCLE_PERSONALITY].invoke()
      personality.assert_called_once_with(2)
      assert actions[EXPERIMENTAL].invoke()
      experimental.assert_called_once()
      session._conditional_favorite_active.return_value = True
      assert not actions[EXPERIMENTAL].invoke()
      current = session.native_favorite_actions(bookmark, personality, experimental)
      assert not current[EXPERIMENTAL].available
      assert 'conditional status' in current[EXPERIMENTAL].reason
      session._favorite_authority.return_value = False
      session._bookmark_authority.return_value = False
      assert not actions[BOOKMARK].invoke()
      assert not actions[CYCLE_PERSONALITY].invoke()
      assert bookmark.call_count == personality.call_count == experimental.call_count == 1


  def test_plain_stock_selection_does_not_use_conditional_shortcut(self):
    from unittest.mock import patch
    from openpilot.starpilot.ui import runtime_app
    from openpilot.starpilot.conditional_mode.preferences import SavedPreferences, encode_preferences
    from openpilot.starpilot.conditional_mode.policy import ModeChoice
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    params = NS(get=Mock(return_value=encode_preferences(SavedPreferences(mode=ModeChoice.STOCK))))
    with patch.object(runtime_app, 'ui_state', NS(params=params, CP=object())), patch.object(runtime_app, 'feature_enabled', return_value=True):
      assert not session._conditional_favorite_active()
      params.get.return_value = None
      assert session._conditional_favorite_active()
      params.get.return_value = b'{'
      assert session._conditional_favorite_active()


  def test_collapsed_favorites_yield_to_existing_controls_0(self):
    claimed = True
    from openpilot.starpilot.ui import runtime_app
    from openpilot.starpilot.ui.shell import ShellMode
    controller, emit, state, _ = setup()
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session.favorites = controller
    session.profile = controller.profile
    self.enterContext(patch.object(session, 'input', NS(press=Mock(), cancel=Mock(), onroad=NS(claimed=claimed)), create=True))
    session.snapshot = Mock(return_value=NS(onroad=state))
    session._update_favorites = Mock()
    session.view = Mock(onroad=NS(navigation=NS(press=lambda *args: False)))
    session.press(ShellMode.ONROAD, 80, 80)
    assert session._favorite_claimed is not claimed
    assert session.input.cancel.called is not claimed
    emit.assert_not_called()


  def test_collapsed_favorites_yield_to_existing_controls_1(self):
    claimed = False
    from openpilot.starpilot.ui import runtime_app
    from openpilot.starpilot.ui.shell import ShellMode
    controller, emit, state, _ = setup()
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session.favorites = controller
    session.profile = controller.profile
    self.enterContext(patch.object(session, 'input', NS(press=Mock(), cancel=Mock(), onroad=NS(claimed=claimed)), create=True))
    session.snapshot = Mock(return_value=NS(onroad=state))
    session._update_favorites = Mock()
    session.view = Mock(onroad=NS(navigation=NS(press=lambda *args: False)))
    session.press(ShellMode.ONROAD, 80, 80)
    assert session._favorite_claimed is not claimed
    assert session.input.cancel.called is not claimed
    emit.assert_not_called()




  def test_open_drawer_claims_outside_tap_and_suppresses_background(self):
    from openpilot.starpilot.ui import runtime_app
    from openpilot.starpilot.ui.shell import ShellMode
    controller, emit, state, _ = setup(Profile.LARGE)
    controller.open(1)
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session.favorites = controller
    session.profile = controller.profile
    self.enterContext(patch.object(session, 'input', NS(press=Mock(), cancel=Mock()), create=True))
    session.snapshot = Mock(return_value=NS(onroad=state))
    session._update_favorites = Mock()
    session.press(ShellMode.ONROAD, 1700, 900)
    assert session.release(ShellMode.ONROAD, 1700, 900)
    assert controller.mode == 'collapsed'
    session.input.press.assert_not_called()
    emit.assert_not_called()


  def test_compact_personality_adapter_rechecks_safe_and_long_gates(self):
    from unittest.mock import patch
    from openpilot.selfdrive.ui.mici.layouts.settings import toggles
    owner = toggles.TogglesLayoutMici.__new__(toggles.TogglesLayoutMici)
    owner._update_toggles = Mock()
    self.enterContext(patch.object(owner, '_personality_toggle', NS(request_index=Mock(return_value=True)), create=True))
    native = NS(CP=object(), has_longitudinal_control=False)
    with patch.object(toggles, 'ui_state', native):
      assert not owner.request_personality(2)
      owner._personality_toggle.request_index.assert_not_called()
      native.has_longitudinal_control = True
      assert owner.request_personality(2)
      owner._personality_toggle.request_index.assert_called_once_with(2, block=True)
    assert owner._update_toggles.call_count == 2


  def test_favorites_serialization_and_owner_reads_are_cached_until_refresh(self):
    from unittest.mock import patch
    from openpilot.starpilot.ui import runtime_app
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session._favorite_read_at = None
    self.enterContext(patch.object(session, 'favorites_owner', NS(snapshot=Mock(return_value=object())), create=True))
    self.enterContext(patch.object(session, 'favorites', NS(update=Mock(), mode='collapsed'), create=True))
    with patch.object(runtime_app, 'asdict', return_value={'revision': 'r'}) as serialize:
      session._update_favorites(object(), 10)
      for now in (10.01, 10.02, 10.9):
        session._update_favorites(object(), now)
      assert serialize.call_count == session.favorites_owner.snapshot.call_count == 1
      session._update_favorites(object(), 11)
      assert serialize.call_count == session.favorites_owner.snapshot.call_count == 2


  def test_conditional_shortcut_uses_qualified_owner_and_rechecks_authority(self):
    from unittest.mock import patch
    from openpilot.starpilot.ui import runtime_app
    from openpilot.starpilot.favorites.actions import EXPERIMENTAL
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    self.enterContext(patch.object(session, 'selected', 'star', create=True))
    state = OnroadState(True, True, 15, 80, SpeedLimitObservation())
    self.enterContext(patch.object(session, 'adapter', NS(build=Mock(return_value=NS(onroad=state))), create=True))
    session.snapshot = Mock(return_value=NS(onroad=state))
    session._favorite_authority = Mock(return_value=True)
    session._bookmark_authority = Mock(return_value=True)
    session._conditional_favorite_active = Mock(return_value=True)
    context = NS(token='drive-settings-planner-effective-manual')
    self.enterContext(patch.object(session, 'conditional_actions', NS(context=Mock(return_value=context), dispatch=Mock(return_value=True)), create=True))
    experimental = Mock(return_value=True)
    native = NS(CP=NS(carFingerprint='car', flags=0, pcmCruise=True), has_longitudinal_control=True, started_frame=42,
                personality=1, sm={"deviceState": NS(startedMonoTime=42)}, params=NS(get_bool=lambda key: False))
    with patch.object(runtime_app, 'ui_state', native), patch.object(runtime_app, '_slc_action_publisher') as publisher:
      action = session.native_favorite_actions(Mock(), Mock(), experimental)[EXPERIMENTAL]
      assert action.available and action.invoke()
      args = session.conditional_actions.dispatch.call_args.args
      assert args == (context, native.sm, native.CP, publisher.return_value)
      experimental.assert_not_called()
      assert action.token == session.native_favorite_actions(Mock(), Mock(), experimental)[EXPERIMENTAL].token
      session.conditional_actions.context.return_value = NS(token='changed-settings')
      assert action.token != session.native_favorite_actions(Mock(), Mock(), experimental)[EXPERIMENTAL].token
      session._favorite_authority.return_value = False
      assert not action.invoke()
      session._favorite_authority.return_value = True
      native.params.get_bool = lambda key: key == 'SafeMode'
      assert not action.invoke()
      native.params.get_bool = lambda key: False
      session._conditional_favorite_active.return_value = False
      assert not action.invoke()
      assert session.conditional_actions.dispatch.call_count == 1
      experimental.assert_not_called()


  def test_wheel_request_rechecks_binding_token_without_raw_toggle(self):
    from openpilot.starpilot.ui import runtime_app
    from openpilot.starpilot.favorites.actions import EXPERIMENTAL
    from openpilot.starpilot.favorites.state import FavoriteAction
    from openpilot.starpilot.ui.onroad_state import OnroadRequest
    from openpilot.starpilot.ui.shell import ShellMode, ShellRequest, ShellSnapshot
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    self.enterContext(patch.object(session, 'selected', 'star', create=True))
    session.profile = Profile.LARGE
    state = OnroadState(True, True, 15, 80, SpeedLimitObservation())
    snapshot = ShellSnapshot(ShellMode.ONROAD, Mock(), Mock(), state)
    session.snapshot = Mock(return_value=snapshot)
    invoke = Mock(return_value=True)
    action = FavoriteAction(EXPERIMENTAL, 'Experimental Mode', available=True, token='current', invoke=invoke)
    session._native_favorite_actions = Mock(return_value={EXPERIMENTAL: action})
    session._unavailable = Mock()
    projected = session._input_snapshot(ShellMode.ONROAD)
    assert projected.onroad.experimental_available
    assert projected.onroad.experimental_action_token == 'current'
    assert snapshot.onroad.experimental_action_token == ''
    session._emit(ShellRequest('onroad', OnroadRequest('set_experimental', True, 'stale')))
    invoke.assert_not_called()
    session._emit(ShellRequest('onroad', OnroadRequest('set_experimental', True, 'current')))
    invoke.assert_called_once()
    session._native_favorite_actions.return_value = {EXPERIMENTAL: replace(action, available=False)}
    assert not session._input_snapshot(ShellMode.ONROAD).onroad.experimental_available
    session._emit(ShellRequest('onroad', OnroadRequest('set_experimental', True, 'current')))
    assert invoke.call_count == 1
    session.profile = Profile.COMPACT
    assert session._input_snapshot(ShellMode.ONROAD) is snapshot
