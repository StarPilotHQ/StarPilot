
from unittest.mock import Mock, call as mock_call
from pathlib import Path
from openpilot.starpilot.ui.presentation import BitmapFonts
import json
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.ui.appearance_preferences import OnroadAppearance
from openpilot.starpilot.ui.onroad_customization import (
  CLOCK_WIDGET, DEFAULT_WIDGET_ORDER, MAX_BYTES, STOPPED_TIMER_WIDGET, customization_metadata, default_document, offset, read_customization,
  validate_document, decode_document, widget_palette, rgba, widget_size)
from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
from openpilot.starpilot.ui.onroad_large_widgets import CurrentSpeedHud, UnifiedSpeedWidget, SteeringWheelWidget
from openpilot.starpilot.ui.onroad_state import (
  AlertSize, ObservationKind, OnroadAlert, OnroadInput, OnroadRequest, OnroadState, SpeedLimitObservation, slc_controls, speed_limit_bounds)
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.tests.test_unified_speed_native import foreground_calls


@pytest.fixture(autouse=True)
def drawing_guard(monkeypatch):
  monkeypatch.setattr(rl, 'draw_line_ex', Mock())
  monkeypatch.setattr('openpilot.starpilot.ui.speed_limit_shapes.SpeedLimitShapes.arrow', Mock())
  monkeypatch.setattr('openpilot.starpilot.ui.speed_limit_shapes.SpeedLimitShapes.outline', Mock())
  monkeypatch.setattr('openpilot.starpilot.ui.speed_limit_shapes.SpeedLimitShapes.circle', Mock())
  monkeypatch.setattr('openpilot.starpilot.ui.speed_limit_shapes.SpeedLimitShapes.button', Mock())


def road(document=None, **changes):
  changes.setdefault('slc_prompt_open', True)
  return replace(OnroadState(True, True, 15.0, 80.0, SpeedLimitObservation(),
                            appearance=OnroadAppearance(show_developer_sidebar=True), viewport_width=1860),
                 customization=document or default_document(), **changes)


def test_torque_bar_keeps_its_familiar_customization_label():
  for profile in customization_metadata()['profiles'].values():
    assert profile['widgets']['torque_bar']['label'] == 'Torque bar'


def test_v12_portrait_upgrade_preserves_preferences_and_is_idempotent():
  import copy
  old = default_document()
  old['version'] = 12
  old['layouts']['compact']['speed_limit'].update(x=330, y=20)
  old['layouts']['large']['current_speed'].update(x=700, enabled=False)
  old['widgetColors']['large']['current_speed'] = {'text': '#ABCDEF80'}
  old['reduceMotion'] = True
  before = copy.deepcopy(old)
  updated = validate_document(old)
  expected = copy.deepcopy(old)
  expected['version'] = 14
  expected['layouts']['compact']['speed_limit'].update(x=320, y=16)
  assert old == before and updated == expected
  assert validate_document(updated) == updated


def test_v12_edge_placements_remain_bounded_even_with_confirmation_disabled():
  old = default_document()
  old['version'] = 12
  for layout in old['layouts'].values():
    layout['speed_limit_actions']['enabled'] = False
  old['layouts']['large']['cruise_limits']['y'] = 882
  old['layouts']['compact']['speed_limit'].update(x=358, y=108)
  updated = validate_document(old)
  assert updated['layouts']['large']['cruise_limits']['y'] == 856
  assert updated['layouts']['compact']['speed_limit'] == {'x': 348, 'y': 96, 'enabled': True}
  assert validate_document(updated) == updated
  assert all(not layout['speed_limit_actions']['enabled'] for layout in updated['layouts'].values())


@pytest.mark.parametrize('sign_y', [12, 16, 20])
def test_v13_notice_upgrade_preserves_preferences_and_only_moves_a_conflicting_sign(sign_y):
  import copy
  old = default_document()
  old['version'] = 13
  old['layouts']['compact']['speed_limit']['y'] = sign_y
  old['layouts']['large']['current_speed'].update(x=700, enabled=False)
  old['widgetColors']['large']['current_speed'] = {'text': '#ABCDEF80'}
  old['reduceMotion'] = True
  before = copy.deepcopy(old)
  updated = validate_document(old)
  expected = copy.deepcopy(old)
  expected['version'] = 14
  expected['layouts']['compact']['speed_limit']['y'] = min(sign_y, 16)
  assert old == before and updated == expected
  assert validate_document(updated) == updated


def test_compact_notice_is_reserved_even_when_the_shorter_buttons_would_fit():
  document = default_document()
  document['layouts']['compact']['speed_limit']['y'] = 20
  with pytest.raises(ValueError, match='confirmation'):
    validate_document(document)
  document['layouts']['compact']['speed_limit_actions']['enabled'] = False
  assert validate_document(document) == document


@pytest.mark.parametrize('separate', [False, True])
def test_v12_source_attachment_follows_the_new_row_without_moving_custom_actions(separate):
  old = default_document()
  old.update(version=12, speedSources=True, speedSourcesCard=separate)
  actions = old['layouts']['large']['speed_limit_actions']
  actions['y'] = 367 if separate else 359
  updated = validate_document(old)
  assert updated['layouts']['large']['speed_limit_actions']['y'] == (389 if separate else 381)
  assert validate_document(updated) == updated
  actions.update(x=600, y=400)
  assert validate_document(old)['layouts']['large']['speed_limit_actions'] == actions


def test_current_portrait_layout_rejects_a_chooser_outside_the_compact_screen():
  document = default_document()
  document['layouts']['compact']['speed_limit']['x'] = 330
  with pytest.raises(ValueError, match='confirmation'):
    validate_document(document)


def test_defaults_and_metadata_are_independent_and_valid():
  document = default_document()
  assert validate_document(document) == document
  for profile, data in customization_metadata()["profiles"].items():
    for key, widget in data["widgets"].items():
      assert document["layouts"][profile][key] == {
        **widget["default"], **({"size": widget["width"]} if key == "steering_wheel" else {})}
      assert offset(document, profile, key) == (0, 0)
  document["layouts"]["large"]["current_speed"]["x"] = 50
  assert default_document()["layouts"]["large"]["current_speed"]["x"] == 640


@pytest.mark.parametrize('custom', (False, True))
def test_personality_upgrade_adds_large_widget_without_losing_saved_layout(custom):
  import copy
  document = default_document()
  del document['layouts']['large']['following_distance']
  document['layouts']['large']['driver_monitor'].update(x=700 if custom else 120)
  document['layouts']['large']['current_speed'].update(x=600, enabled=False)
  document['widgetOrder'] = {'large': [key for key in DEFAULT_WIDGET_ORDER['large'] if key != 'following_distance']}
  before = copy.deepcopy(document)
  migrated = validate_document(document)
  assert document == before and validate_document(migrated) == migrated
  large = migrated['layouts']['large']
  assert large['following_distance'] == {'x': 88, 'y': 804, 'enabled': True}
  assert large['driver_monitor']['x'] == (700 if custom else 270)
  assert large['current_speed'] == before['layouts']['large']['current_speed']
  assert set(migrated['widgetOrder']['large']) == set(large)
  assert migrated['layouts']['compact'] == before['layouts']['compact']


@pytest.mark.parametrize('version', (10, 11))
@pytest.mark.parametrize('separate', (False, True))
@pytest.mark.parametrize('lower', (False, True))
def test_source_attachment_saves_real_bounded_positions_without_changing_input(version, separate, lower):
  import copy
  from openpilot.starpilot.ui.onroad_customization import placement
  document = default_document()
  document.update(version=version, speedSources=True, speedSourcesCard=separate)
  if lower:
    document['layouts']['large']['cruise_limits']['y'] = 690
    document['layouts']['large']['speed_limit_actions']['y'] = 882
  before = copy.deepcopy(document)
  saved = validate_document(document)
  card, actions = (saved['layouts']['large'][key] for key in ('cruise_limits', 'speed_limit_actions'))
  assert document == before and validate_document(saved) == saved
  assert placement(saved, 'large', 'speed_limit_actions') == actions
  assert actions['y'] == card['y'] + widget_size(saved, 'large', 'cruise_limits')[1] + 8
  assert actions['y'] + widget_size(saved, 'large', 'speed_limit_actions')[1] <= 1050
  assert actions['y'] == (389 if separate else 381)


def test_v7_speed_layout_migration_is_bounded_non_mutating_and_idempotent():
  import copy
  from openpilot.starpilot.ui.onroad_customization import SPEED_LAYOUT_V7
  document = default_document()
  document['version'] = 7
  for profile, widgets in SPEED_LAYOUT_V7.items():
    for key, (_, _, x, y) in widgets.items():
      document['layouts'][profile][key].update(x=x, y=y)
  document['layouts']['large']['cem_gauge'].update(x=480, y=99)
  document['layouts']['large']['cruise_limits']['x'] = 1510
  document['layouts']['large']['speed_limit_actions'].update(x=1510, y=900)
  document['layouts']['compact']['max_speed']['y'] = 60
  document['layouts']['compact']['speed_limit'].update(x=330, y=20)
  document['layouts']['large']['current_speed'].update(x=700, y=100, enabled=False)
  document['widgetColors']['large']['current_speed'] = {'text': '#12345678'}
  document['speedSources'] = True
  original = copy.deepcopy(document)
  migrated = validate_document(document)
  assert document == original
  assert migrated['version'] == 14 and validate_document(migrated) == migrated
  assert migrated['layouts']['large']['cruise_limits']['x'] == 1437
  assert migrated['layouts']['large']['speed_limit_actions'] == {'x': 1510, 'y': 882, 'enabled': True}
  assert migrated['layouts']['large']['current_speed'] == original['layouts']['large']['current_speed']
  assert migrated['layouts']['large']['cem_gauge'] == {'x': 56, 'y': 536, 'enabled': True}
  assert migrated['layouts']['compact']['speed_limit']['x'] == 320
  assert migrated['layouts']['compact']['max_speed']['y'] == 60
  assert migrated['widgetColors'] == original['widgetColors'] and migrated['speedSources']
  document['layouts']['large']['cruise_limits']['x'] = 2000
  with pytest.raises(ValueError, match='outside'):
    validate_document(document)


@pytest.mark.parametrize('custom', (False, True))
def test_v8_restores_only_compact_speed_defaults_and_preserves_customizations(custom):
  import copy
  from openpilot.starpilot.ui.onroad_customization import COMPACT_SPEED_LAYOUT_V8
  document = default_document()
  document['version'] = 8
  for key, (_, _, x, y) in COMPACT_SPEED_LAYOUT_V8.items():
    document['layouts']['compact'][key].update(x=x, y=y)
  if custom:
    document['layouts']['compact']['speed_limit'].update(x=100, y=40, enabled=False)
    document['layouts']['compact']['max_speed'].update(x=50, y=0)
  document['widgetColors']['compact']['max_speed'] = {'text': '#12345678'}
  document['layouts']['large']['cruise_limits'].update(x=1100, y=180)
  document['widgetOrder'] = {profile: list(order) for profile, order in DEFAULT_WIDGET_ORDER.items()}
  original = copy.deepcopy(document)
  migrated = validate_document(document)
  assert document == original
  assert migrated['version'] == 14 and validate_document(migrated) == migrated
  assert migrated['layouts']['large'] == document['layouts']['large']
  assert migrated['widgetColors'] == document['widgetColors']
  assert migrated['widgetOrder'] == document['widgetOrder']
  expected = document['layouts']['compact']['speed_limit'] if custom else {'x': 320, 'y': 16, 'enabled': True}
  assert migrated['layouts']['compact']['speed_limit'] == expected
  assert migrated['layouts']['compact']['max_speed']['x'] == 0
  assert migrated['layouts']['compact']['speed_limit_actions'] == {'x': 146, 'y': 44, 'enabled': True}


def test_v8_compact_speed_widgets_at_old_edges_stay_in_bounds_after_upgrade():
  from openpilot.starpilot.ui.onroad_customization import COMPACT_SPEED_LAYOUT_V8
  document = default_document()
  document['version'] = 8
  for key, (_, _, x, y) in COMPACT_SPEED_LAYOUT_V8.items():
    document['layouts']['compact'][key].update(x=x, y=y)
  document['layouts']['compact']['max_speed'].update(x=330, y=126)
  document['layouts']['compact']['speed_limit'].update(x=212, y=158)
  migrated = validate_document(document)
  assert migrated['layouts']['compact']['max_speed'] == {'x': 0, 'y': 0, 'enabled': True}
  assert migrated['layouts']['compact']['speed_limit'] == {'x': 320, 'y': 16, 'enabled': True}
  assert validate_document(migrated) == migrated


def test_v8_actionless_layout_preserves_customizations_when_adding_compact_actions():
  import copy
  from openpilot.starpilot.ui.onroad_customization import COMPACT_SPEED_LAYOUT_V8, RAIL_WIDGETS
  document = default_document()
  document['version'] = 8
  for key, (_, _, x, y) in COMPACT_SPEED_LAYOUT_V8.items():
    document['layouts']['compact'][key].update(x=x, y=y)
  large_actions = document['layouts']['large'].pop('speed_limit_actions')
  for key in ('speed_limit_actions', *RAIL_WIDGETS):
    del document['layouts']['compact'][key]
  document['layouts']['large']['current_speed']['x'] = 700
  document['layouts']['compact']['speed_limit'].update(x=100, y=40)
  document['palette']['text'] = '#ABCD1234'
  document['widgetColors']['compact']['max_speed'] = {'text': '#12345678'}
  original = copy.deepcopy(document)
  migrated = validate_document(document)
  assert document == original
  assert migrated['version'] == 14 and validate_document(migrated) == migrated
  assert migrated['layouts']['large'] == {**original['layouts']['large'], 'speed_limit_actions': large_actions}
  assert migrated['layouts']['compact']['speed_limit'] == default_document()['layouts']['compact']['speed_limit']
  assert migrated['layouts']['compact']['speed_limit_actions'] == {'x': 146, 'y': 44, 'enabled': True}
  assert migrated['palette'] == original['palette']
  assert migrated['widgetColors'] == original['widgetColors']


def test_clock_is_an_opt_in_widget_and_migrates_saved_layout_and_layer_order():
  document = default_document()
  metadata = customization_metadata()['profiles']
  for profile in ('large', 'compact'):
    assert metadata[profile]['widgets'][CLOCK_WIDGET]['kind'] == CLOCK_WIDGET
    assert document['layouts'][profile][CLOCK_WIDGET]['enabled'] is False
    document['layouts'][profile].pop(CLOCK_WIDGET)
  document['widgetOrder'] = {
    profile: [key for key in DEFAULT_WIDGET_ORDER[profile] if key != CLOCK_WIDGET]
    for profile in ('large', 'compact')
  }
  migrated = validate_document(document)
  for profile in ('large', 'compact'):
    assert migrated['layouts'][profile][CLOCK_WIDGET] == metadata[profile]['widgets'][CLOCK_WIDGET]['default']
    assert migrated['widgetOrder'][profile][-1] == CLOCK_WIDGET


def test_clock_format_preference_defaults_migrates_and_validates():
  document = default_document()
  assert document['clock24Hour'] is False
  document['clock24Hour'] = True
  assert validate_document(document)['clock24Hour'] is True
  document.pop('clock24Hour')
  assert validate_document(document)['clock24Hour'] is False
  document['clock24Hour'] = 1
  with pytest.raises(ValueError, match='clock format'):
    validate_document(document)


@pytest.mark.parametrize("widget,x,y", [("max_speed", 174, 25), ("steering_wheel", 174, 137)])
def test_protected_compact_actions_reject_actual_overlap(widget, x, y):
  document = default_document()
  document["layouts"]["compact"][widget].update(x=x, y=y)
  with pytest.raises(ValueError, match="protected"):
    validate_document(document)


@pytest.mark.parametrize("widget,x,y", [("speed_limit", 320, 16), ("max_speed", 0, 0), ("steering_wheel", 78, 174)])
def test_protected_zone_edge_touch_is_valid(widget, x, y):
  document = default_document()
  document["layouts"]["compact"][widget].update(x=x, y=y)
  assert validate_document(document) == document
  zones = customization_metadata()["profiles"]["compact"]["reservedZones"]
  assert zones == []
  assert customization_metadata()["profiles"]["compact"]["protectedWidget"] == "speed_limit_actions"


@pytest.mark.parametrize("change", [
  lambda d: d.update(version=True),
  lambda d: d.update(extra=0),
  lambda d: d["palette"].update(text="#FFFFFF"),
  lambda d: d["palette"].update(text="#GGFFFFFF"),
  lambda d: d["layouts"]["large"].update(unknown={}),
  lambda d: d["layouts"]["compact"]["max_speed"].update(x=float("nan")),
  lambda d: d["layouts"]["compact"]["max_speed"].update(y=float("inf")),
  lambda d: d["layouts"]["large"]["current_speed"].update(x=True),
  lambda d: d["layouts"]["large"]["current_speed"].update(enabled=1),
  lambda d: d["layouts"]["large"]["steering_wheel"].update(x=1800),
  lambda d: d["layouts"]["compact"]["speed_limit"].update(y=200),
])
def test_invalid_contract_rejected(change):
  document = default_document()
  change(document)
  with pytest.raises(ValueError):
    validate_document(document)


@pytest.mark.parametrize("raw", [None, b"{", b"\xff", b"x" * (MAX_BYTES + 1), b'{"version":1,"version":1}', b"[" * 1200 + b"]" * 1200])
def test_saved_invalid_or_absent_uses_defaults(raw):
  with patch("openpilot.starpilot.ui.onroad_customization.read_saved", return_value=(raw, True)):
    assert read_customization(Mock()) == default_document()


def test_mode_widget_defaults_off_without_resetting_saved_layouts():
  key = 'driving_mode_descriptions'
  document = default_document()
  document['layouts']['large']['current_speed'].update(x=700, y=100, enabled=False)
  document['layouts']['compact']['speed_limit_actions']['y'] = 44
  for layout in document['layouts'].values():
    assert layout.pop(key)['enabled'] is False
  migrated = validate_document(document)
  for profile, layout in document['layouts'].items():
    assert {key: value for key, value in migrated['layouts'][profile].items()
            if key != 'driving_mode_descriptions'} == layout
    assert migrated['layouts'][profile][key]['enabled'] is False
  migrated['layouts']['large'][key].update(enabled=True, x=300, y=600)
  assert validate_document(migrated) == migrated
  migrated['layouts']['compact'][key]['enabled'] = True
  migrated['layouts']['compact'][key]['y'] = 130
  with pytest.raises(ValueError, match='protected speed limit actions'):
    validate_document(migrated)


def test_saved_valid_layouts_survive_independently():
  document = default_document()
  document["layouts"]["large"]["current_speed"].update(x=100, y=120)
  document["layouts"]["compact"]["max_speed"].update(x=0, y=20)
  with patch("openpilot.starpilot.ui.onroad_customization.read_saved", return_value=(json.dumps(document).encode(), True)):
    assert read_customization(Mock()) == document


def test_v3_layout_migrates_wheel_size_without_changing_saved_placement_or_color():
  document = default_document()
  document["version"] = 3
  del document["layouts"]["large"]["cem_gauge"]  # Version 3 predates the CEM gauge.
  document["palette"]["text"] = "#ABCDEF80"
  document["layouts"]["large"]["steering_wheel"].update(x=1300, y=100, enabled=False)
  document["layouts"]["compact"]["steering_wheel"].update(x=90, y=130)
  for profile in ("large", "compact"):
    del document["layouts"][profile]["steering_wheel"]["size"]
  migrated = validate_document(document)
  assert migrated["version"] == 14
  assert migrated["palette"] == document["palette"]
  assert migrated["layouts"]["large"]["steering_wheel"] == {"x": 1300, "y": 100, "enabled": False, "size": 192}
  assert migrated["layouts"]["compact"]["steering_wheel"] == {"x": 90, "y": 130, "enabled": True, "size": 50}


@pytest.mark.parametrize("removed", [("driver_monitor", "torque_bar"), ("torque_bar",), ()])
def test_pre_action_saved_layout_migrates_without_losing_positions_or_palette(removed):
  document = default_document()
  document["version"] = 1
  del document["layouts"]["large"]["cem_gauge"]  # Version 1 predates the CEM gauge.
  del document["widgetColors"]
  del document["roadColors"]
  document["palette"]["cardFill"] = "#12345678"
  document["layouts"]["large"]["current_speed"].update(x=700, y=120, enabled=False)
  document["layouts"]["large"]["cruise_limits"].update(x=288, y=175, enabled=False)
  document["layouts"]["compact"]["speed_limit"].update(x=200, y=20)
  for key in ("model_confidence", "conditional_mode", "following_distance"):
    del document["layouts"]["compact"][key]
  for layout in document["layouts"].values():
    del layout["speed_limit_actions"]
    del layout["steering_wheel"]["size"]
    for key in removed:
      del layout[key]
  migrated = validate_document(document)
  assert migrated["palette"] == document["palette"]
  for profile in ("large", "compact"):
    for key, position in document["layouts"][profile].items():
      if profile == "compact" and key == "speed_limit":
        assert migrated["layouts"][profile][key] == default_document()["layouts"][profile][key]
        continue
      assert migrated["layouts"][profile][key] == {
        **position, **({"size": customization_metadata()["profiles"][profile]["widgets"][key]["width"]}
                     if key == "steering_wheel" else {})}
  assert migrated["layouts"]["large"]["speed_limit_actions"] == {"x": 312, "y": 367, "enabled": False}
  assert migrated["layouts"]["compact"]["speed_limit_actions"] == default_document()["layouts"]["compact"]["speed_limit_actions"]


def test_malformed_pre_action_cruise_placement_is_rejected():
  document = default_document()
  for key in ("model_confidence", "conditional_mode", "following_distance"):
    del document["layouts"]["compact"][key]
  for layout in document["layouts"].values():
    del layout["speed_limit_actions"]
  document["layouts"]["large"]["cruise_limits"]["x"] = "moved"
  with pytest.raises(ValueError, match="Invalid widget placement"):
    validate_document(document)


def test_duplicate_key_cannot_apply_otherwise_valid_placement():
  document = default_document()
  document["layouts"]["large"]["current_speed"]["x"] = 100
  raw = json.dumps(document).replace('"version": 14', '"version": 11, "version": 11').encode()
  with patch("openpilot.starpilot.ui.onroad_customization.read_saved", return_value=(raw, True)):
    assert read_customization(Mock()) == default_document()


@pytest.mark.parametrize('sources', [False, True])
def test_v9_large_sign_migration_preserves_customizations_and_clamps_bottom_edge(sources):
  from copy import deepcopy
  old = default_document()
  old['version'] = 9
  old['speedSources'] = sources
  old['layouts']['large']['cruise_limits'].update(y=918)
  old['layouts']['large']['speed_limit_actions'].update(x=250, y=400)
  before = deepcopy(old)
  updated = validate_document(old)
  assert old == before
  assert updated['version'] == 14
  assert updated['layouts']['large']['cruise_limits']['y'] == default_document()['layouts']['large']['cruise_limits']['y']
  assert updated['layouts']['large']['speed_limit_actions'] == before['layouts']['large']['speed_limit_actions']
  assert updated['layouts']['compact'] == before['layouts']['compact']
  assert validate_document(updated) == updated


def test_v9_moves_only_actions_paired_with_the_old_large_speed_group():
  old = default_document()
  old['version'] = 9
  old['layouts']['large']['cruise_limits'].update(x=188, y=95)
  old['layouts']['large']['speed_limit_actions'].update(x=200, y=268)
  updated = validate_document(old)
  assert updated['layouts']['large']['speed_limit_actions'] == {'x': 212, 'y': 287, 'enabled': True}
  assert validate_document(updated) == updated


def test_wheel_hit_edges_match_default_draw_bounds():
  emit = Mock()
  inputs = OnroadInput(emit)
  state = road(experimental_available=True)
  for x in (1588, 1775, 1780):
    inputs.press(x, 100, state)
    inputs.release(x, 100, state)
  assert emit.call_count == 3
  emit.reset_mock()
  for x in (1580, 1781):
    inputs.press(x, 100, state)
    inputs.release(x, 100, state)
  emit.assert_not_called()


def test_resized_wheel_draw_and_touch_share_large_bounds():
  document = default_document()
  document["layouts"]["large"]["steering_wheel"].update(x=1300, y=100, size=240)
  state = road(document, experimental_available=True)
  assert validate_document(document) == document
  wheel = SteeringWheelWidget(Path('/unused'))
  wheel._texture = Mock()
  with patch.object(rl, "draw_circle") as circle, patch.object(rl, "draw_texture_pro") as texture:
    wheel.render(rl.Rectangle(30, 30, 1800, 1020), state)
  assert circle.call_args.args[:3] == (1420, 220, 120)
  rect = texture.call_args.args[2]
  assert (rect.x, rect.y, rect.width, rect.height) == (1330, 130, 180, 180)
  emit = Mock()
  inputs = OnroadInput(emit)
  for x, y in ((1300, 100), (1539, 339)):
    inputs.press(x, y, state)
    inputs.release(x, y, state)
  assert emit.call_count == 2
  inputs.press(1541, 339, state)
  inputs.release(1541, 339, state)
  assert emit.call_count == 2


def test_compact_resize_rejects_protected_overlap_and_other_widget_sizes():
  document = default_document()
  document["layouts"]["compact"]["steering_wheel"].update(size=70, y=52)
  assert validate_document(document) == document
  assert widget_size(document, "compact", "steering_wheel") == (70, 70)
  document["layouts"]["compact"]["steering_wheel"].update(x=150, y=150)
  with pytest.raises(ValueError, match="protected"):
    validate_document(document)
  document = default_document()
  document["layouts"]["compact"]["speed_limit_actions"]["size"] = 70
  with pytest.raises(ValueError, match="placement"):
    validate_document(document)


@pytest.mark.parametrize("changed_token", ["new-settings", "new-drive", "new-mode", ""])
def test_wheel_press_cancels_when_action_token_changes(changed_token):
  emit = Mock()
  inputs = OnroadInput(emit)
  state = road(experimental_available=True, experimental_action_token="displayed-action")
  inputs.press(1600, 100, state)
  assert inputs.claimed
  changed = replace(state, experimental_action_token=changed_token)
  inputs.move(1600, 100, changed)
  assert not inputs.claimed
  inputs.release(1600, 100, state)
  emit.assert_not_called()
  inputs.press(1600, 100, state)
  inputs.release(1600, 100, changed)
  emit.assert_not_called()


@pytest.mark.parametrize("enabled,token", [(False, ""), (True, "stable-action")])
def test_moved_wheel_emits_captured_action_token_and_effective_bool(enabled, token):
  document = default_document()
  document["layouts"]["large"]["steering_wheel"].update(x=1300, y=100)
  state = road(document, experimental_available=True, experimental_enabled=enabled, experimental_action_token=token)
  emit = Mock()
  inputs = OnroadInput(emit)
  inputs.press(1310, 110, state)
  inputs.release(1310, 110, replace(state))
  emit.assert_called_once_with(OnroadRequest("set_experimental", not enabled, token))


def test_large_draw_origins_and_wheel_input_move_together():
  document = default_document()
  document["layouts"]["large"]["cruise_limits"].update(x=188, y=95)
  document["layouts"]["large"]["current_speed"].update(x=680, y=70)
  document["layouts"]["large"]["steering_wheel"].update(x=1300, y=100)
  state = road(document, experimental_available=True)
  fonts = NS(profile=Profile.LARGE, measure=lambda *a, **k: NS(width=10, height=20), vertical_ink=lambda text, role, size: (0., size * .7),
             horizontal_ink=lambda text, role, size: (0., 10.), draw=Mock())
  content = rl.Rectangle(30, 30, 1800, 1020)
  with patch("openpilot.starpilot.ui.onroad_large_widgets.draw_control_card") as card, patch.object(rl, "draw_line_ex"):
    UnifiedSpeedWidget(Mock(spec=BitmapFonts, **vars(fonts))).render(content, state)
    card.assert_not_called()
    assert foreground_calls(fonts.draw)[0].args[3:5] == pytest.approx((275, 106.07), abs=.01)
  CurrentSpeedHud(Mock(spec=BitmapFonts, **vars(fonts))).render(content, state)
  assert foreground_calls(fonts.draw)[-2].args[3:5] == (965, 112)
  wheel = SteeringWheelWidget(Path('/unused'))
  wheel._texture = Mock()
  with patch.object(rl, "draw_circle") as circle, patch.object(rl, "draw_texture_pro"):
    wheel.render(content, state)
    assert circle.call_args.args[:3] == (1396, 196, 96)
  emit = Mock()
  inputs = OnroadInput(emit)
  inputs.press(1310, 110, state)
  inputs.release(1310, 110, state)
  emit.assert_called_once()
  emit.reset_mock()
  inputs.press(1600, 80, state)
  inputs.release(1600, 80, state)
  emit.assert_not_called()

  document["layouts"]["large"]["steering_wheel"]["enabled"] = False
  inputs.press(1310, 110, state)
  inputs.release(1310, 110, state)
  emit.assert_not_called()


def test_frozen_default_draw_geometry_and_neutral_colors():
  state = road()
  fonts = NS(profile=Profile.LARGE, measure=lambda *a, **k: NS(width=10, height=20), vertical_ink=lambda text, role, size: (0., size * .7),
             horizontal_ink=lambda text, role, size: (0., 10.), draw=Mock())
  content = rl.Rectangle(30, 30, 1800, 1020)
  with patch("openpilot.starpilot.ui.onroad_large_widgets.draw_control_card") as card, patch.object(rl, "draw_line_ex"):
    UnifiedSpeedWidget(Mock(spec=BitmapFonts, **vars(fonts))).render(content, state)
    card.assert_not_called()
    assert fonts.draw.call_args_list[0].args[0] == 'SET'
    assert foreground_calls(fonts.draw)[0].args[3:5] == pytest.approx((175, 86.07), abs=.01)
  CurrentSpeedHud(Mock(spec=BitmapFonts, **vars(fonts))).render(content, state)
  assert foreground_calls(fonts.draw)[-2].args[3:5] == (925, 72)
  assert foreground_calls(fonts.draw)[-1].args[3:5] == pytest.approx((925, 215.2))
  wheel = SteeringWheelWidget(Path('/unused'))
  wheel._texture = Mock()
  with patch.object(rl, "draw_circle") as circle, patch.object(rl, "draw_texture_pro"):
    wheel.render(content, state)
    assert circle.call_args.args[:3] == (1684, 171, 96)


@pytest.mark.parametrize("speed", [65, 105])
def test_compact_us_sign_keeps_camera_visible_at_saved_position(speed):
  document = default_document()
  document["layouts"]["compact"]["speed_limit"].update(x=200, y=30)
  state = road(document, metric=True, speed_limit=SpeedLimitObservation(
    kind=ObservationKind.VALID, speed_limit_mps=speed / 3.6))
  state = replace(state, appearance=replace(state.appearance, show_speed_limit_sign=True))
  fonts = NS(profile=Profile.COMPACT, measure=lambda *a, **k: NS(width=10, height=20), vertical_ink=lambda text, role, size: (0., size * .7),
             horizontal_ink=lambda text, role, size: (0., 10.), draw=Mock())
  hud = CompactHudRenderer(Mock(spec=BitmapFonts, **vars(fonts)), Mock())
  with patch.object(rl, "draw_rectangle_rounded") as fill, \
       patch('openpilot.starpilot.ui.speed_limit_shapes.SpeedLimitShapes.outline') as outline:
    hud._speed_limit_sign(state)
  fill.assert_not_called()
  outline.assert_called_once()
  assert outline.call_args.args[:2] == (264, 96)
  assert [call.args[0] for call in foreground_calls(fonts.draw)] == ['SPEED', str(speed)]
  assert foreground_calls(fonts.draw)[0].args[3:5] == (259, 47)


def test_compact_drag_keeps_animation_state_and_separate_origins():
  document = default_document()
  document["layouts"]["compact"]["max_speed"].update(x=100, y=10)
  document["layouts"]["compact"]["steering_wheel"].update(x=90, y=150)
  fonts = NS(profile=Profile.COMPACT, draw=Mock(), vertical_ink=lambda text, role, size: (0, size * .7),
             horizontal_ink=lambda text, role, size: (0, len(text) * size * .5), measure=lambda text, _role, size: NS(
    width=len(text) * size * .5, height=size * Profile.COMPACT.font_scale))
  hud = CompactHudRenderer(Mock(spec=BitmapFonts, **vars(fonts)), Mock())
  hud._wheel = Mock()
  state = road(document, lateral_active=True, longitudinal_active=True)
  with patch.object(hud, "_speed_limit_sign"), patch.object(rl, "draw_circle_gradient"), patch.object(rl, "draw_texture_pro") as wheel:
    hud.render(state)
    hud.render(state)
    assert foreground_calls(fonts.draw)[-1].args[3:5] == (138, 62.5)
    rect = wheel.call_args.args[2]
    assert (rect.x, rect.y) == (115, 175)
    alpha = hud._set_speed_alpha.x
    document["layouts"]["compact"]["max_speed"]["enabled"] = False
    document["layouts"]["compact"]["steering_wheel"]["enabled"] = False
    fonts.draw.reset_mock()
    wheel.reset_mock()
    hud.render(state)
    assert hud._set_speed_alpha.x > alpha
    fonts.draw.assert_not_called()
    wheel.assert_not_called()


def test_saved_six_widget_layout_adds_only_default_rail_positions():
  document = default_document()
  for key in ("model_confidence", "conditional_mode", "following_distance"):
    del document["layouts"]["compact"][key]
  document["layouts"]["compact"]["speed_limit_actions"].update(x=162, enabled=False)
  document["palette"]["text"] = "#ABCD1234"
  migrated = validate_document(document)
  assert migrated["palette"] == document["palette"]
  for profile, layout in document["layouts"].items():
    for key, value in layout.items():
      assert migrated["layouts"][profile][key] == value
  assert migrated["layouts"]["compact"]["conditional_mode"] == {"x": 476, "y": 80, "enabled": True}
  assert "conditional_mode" not in document["layouts"]["compact"]


def test_movable_rail_keeps_limits_and_does_not_cover_active_alerts():
  from openpilot.starpilot.ui.onroad_compact_widgets import MiciSidebarWidgets
  document = default_document()
  document["layouts"]["compact"]["model_confidence"].update(x=200, y=20, enabled=False)
  document["layouts"]["compact"]["conditional_mode"].update(x=330, y=25)
  assert validate_document(document) == document
  widget = MiciSidebarWidgets(Mock())
  widget._confidence_ball, widget._conditional, widget._personality = Mock(), Mock(), Mock()
  with patch.object(rl, "draw_rectangle"):
    widget.render(rl.Rectangle(0, 0, 536, 240), road(document))
    widget._confidence_ball.assert_not_called()
    assert tuple(getattr(widget._conditional.call_args.args[0], name) for name in ("x", "y", "width", "height")) == (330, 25, 60, 80)
    assert tuple(getattr(widget._personality.call_args.args[0], name) for name in ("x", "y", "width", "height")) == (476, 160, 60, 80)
    widget._conditional.reset_mock()
    widget.render(rl.Rectangle(0, 0, 536, 240), road(document, alert=OnroadAlert(size=AlertSize.FULL)))
    widget._conditional.assert_not_called()
  document["layouts"]["compact"]["conditional_mode"].update(x=200, y=107)
  with pytest.raises(ValueError, match="protected"):
    validate_document(document)
  document["layouts"]["compact"]["conditional_mode"].update(x=477, y=0)
  with pytest.raises(ValueError, match="bounds"):
    validate_document(document)


def test_v1_palette_positions_and_visibility_migrate_without_mutating_source():
  import copy

  old = default_document()
  old['version'] = 1
  del old['layouts']['large']['cem_gauge']  # Version 1 predates the CEM gauge.
  del old['widgetColors']
  del old['roadColors']
  old['palette'].update(cardFill='#12345678', text='#AABBCCDD')
  old['layouts']['large']['current_speed'].update(x=600.5, y=100.25, enabled=False)
  old['layouts']['compact']['following_distance'].update(x=400, y=20)
  for layout in old['layouts'].values():
    del layout['steering_wheel']['size']
  original = copy.deepcopy(old)
  result = decode_document(json.dumps(old).encode())
  assert old == original
  assert result['version'] == 14
  assert result['palette'] == old['palette']
  for profile, layout in old['layouts'].items():
    for key, position in layout.items():
      assert result['layouts'][profile][key] == {
        **position, **({'size': customization_metadata()['profiles'][profile]['widgets'][key]['width']}
                     if key == 'steering_wheel' else {})}
  assert result['widgetColors'] == {'large': {'cruise_limits': {'cardFill': '#12345678', 'cardBorder': '#C4CDD0B4'}}, 'compact': {}}
  assert rgba(result, 'text', 'large', 'current_speed') == (170, 187, 204, 221)
  assert rgba(result, 'cardFill', 'compact', 'following_distance') == (0, 0, 0, 0)


@pytest.mark.parametrize('colors', [
  {'large': {}}, {'large': {}, 'compact': [], 'extra': {}},
  {'large': {'unknown': {}}, 'compact': {}},
  {'large': {'current_speed': {'cardFill': '#FFFFFFFF'}}, 'compact': {}},
  {'large': {}, 'compact': {'torque_bar': {'text': '#FFFFFFFF'}}},
  {'large': {'current_speed': {'text': 123}}, 'compact': {}},
  {'large': {'current_speed': {'text': '#FFFFFF'}}, 'compact': {}},
])
def test_unsupported_widget_colors_are_rejected(colors):
  document = default_document()
  document['widgetColors'] = colors
  with pytest.raises(ValueError):
    validate_document(document)


def test_widget_color_isolation_and_native_text_resolution():
  document = default_document()
  document['palette']['text'] = '#10203040'
  document['widgetColors']['large']['current_speed'] = {'text': '#aabbccdd'}
  document['widgetColors']['compact']['following_distance'] = {'cardFill': '#12345678', 'text': '#ABCDEF80'}
  validated = validate_document(document)
  assert rgba(validated, 'text', 'large', 'current_speed') == (170, 187, 204, 221)
  assert rgba(validated, 'text', 'compact', 'max_speed') == (16, 32, 48, 64)
  assert rgba(validated, 'text', 'large', 'cruise_limits') == (16, 32, 48, 64)
  assert widget_palette(validated, 'compact', 'conditional_mode')['cardFill'] == '#00000000'
  fonts = NS(profile=Profile.LARGE, measure=lambda *a, **k: NS(width=10, height=20), vertical_ink=lambda text, role, size: (0., size * .7),
             horizontal_ink=lambda text, role, size: (0., 10.), draw=Mock())
  CurrentSpeedHud(Mock(spec=BitmapFonts, **vars(fonts))).render(rl.Rectangle(30, 30, 1800, 1020), road(validated))
  actual = foreground_calls(fonts.draw)[0].args[-1]
  assert (actual.r, actual.g, actual.b, actual.a) == (170, 187, 204, 221)


def test_unsaved_color_changes_replace_values_and_restore_palette_fallback():
  document = default_document()
  for profile, widget in (("large", "current_speed"), ("compact", "max_speed")):
    assert rgba(document, "text", profile, widget) == (255, 255, 255, 255)
    document["palette"]["text"] = "#10203040"
    assert rgba(document, "text", profile, widget) == (16, 32, 48, 64)
    document["widgetColors"][profile][widget] = {"text": "#aabbccdd"}
    assert rgba(document, "text", profile, widget) == (170, 187, 204, 221)
    document["widgetColors"][profile][widget]["text"] = "#01020304"
    assert rgba(document, "text", profile, widget) == (1, 2, 3, 4)
    del document["widgetColors"][profile][widget]
    assert rgba(document, "text", profile, widget) == (16, 32, 48, 64)
    document["palette"]["text"] = "#FFFFFFFF"


def test_transparent_widget_frames_add_no_default_draw_calls():
  from openpilot.starpilot.ui.onroad_widget_style import draw_widget_frame

  document = default_document()
  with patch.object(rl, 'draw_rectangle_rounded') as fill, patch.object(rl, 'draw_rectangle_rounded_lines_ex') as border:
    for profile, widgets in (('compact', ('driver_monitor', 'model_confidence', 'conditional_mode', 'following_distance')),
                             ('large', ('driver_monitor',))):
      for widget in widgets:
        draw_widget_frame(rl.Rectangle(10, 20, 60, 80), document, profile, widget)
    fill.assert_not_called()
    border.assert_not_called()
    document['widgetColors']['compact']['conditional_mode'] = {'cardFill': '#12345678', 'cardBorder': '#ABCDEF90'}
    draw_widget_frame(rl.Rectangle(200, 80, 60, 80), document, 'compact', 'conditional_mode')
    color = fill.call_args.args[-1]
    assert (color.r, color.g, color.b, color.a) == (18, 52, 86, 120)
    color = border.call_args.args[-1]
    assert (color.r, color.g, color.b, color.a) == (171, 205, 239, 144)


def test_driver_frame_matches_native_preview_and_keeps_face_renderer_separate(monkeypatch):
  from openpilot.starpilot.ui.layout_preview_renderer import _DriverMonitorArt
  from openpilot.starpilot.ui.onroad_dm import DriverMonitorLayer

  document = default_document()
  document['layouts']['compact']['driver_monitor'].update(x=90, y=40)
  document['widgetColors']['compact']['driver_monitor'] = {'cardFill': '#AABBCC80'}
  state = road(document)
  live = DriverMonitorLayer.__new__(DriverMonitorLayer)
  live.profile = Profile.COMPACT
  matrix = Mock()
  monkeypatch.setattr(live, 'renderer', NS(set_should_draw=Mock(), set_position=Mock(), _rect=object(),
                     _fade_filter=NS(update=Mock()), _render=matrix.live_render), raising=False)
  preview = _DriverMonitorArt.__new__(_DriverMonitorArt)
  preview.profile = Profile.COMPACT
  monkeypatch.setattr(preview, 'renderer', NS(_render=matrix.preview_render), raising=False)
  with patch('openpilot.starpilot.ui.onroad_dm.draw_widget_frame') as live_frame, \
       patch('openpilot.starpilot.ui.layout_preview_renderer.draw_widget_frame') as preview_frame, \
       patch.multiple(rl, rl_push_matrix=matrix.push, rl_translatef=matrix.translate,
                      rl_scalef=matrix.scale, rl_pop_matrix=matrix.pop):
    live.render(state, monitor=None, driver=None, fresh=False, onroad=True)
    preview.render(rl.Rectangle(0, 0, 536, 240), state)
  assert live_frame.call_args.args[1:] == preview_frame.call_args.args[1:]
  live_rect, preview_rect = live_frame.call_args.args[0], preview_frame.call_args.args[0]
  assert tuple(getattr(live_rect, key) for key in ('x', 'y', 'width', 'height')) == \
    tuple(getattr(preview_rect, key) for key in ('x', 'y', 'width', 'height')) == (90, 40, 60, 60)
  live.renderer._fade_filter.update.assert_called_once_with(0.35)
  live.renderer._render.assert_called_once()
  preview.renderer._render.assert_called_once()
  assert matrix.mock_calls == [
    mock_call.push(), mock_call.translate(90, 40, 0), mock_call.scale(1, 1, 1), mock_call.translate(-90, -40, 0),
    mock_call.live_render(live.renderer._rect), mock_call.pop(),
    mock_call.push(), mock_call.translate(90, 40, 0), mock_call.scale(1, 1, 1), mock_call.translate(-90, -40, 0),
    mock_call.preview_render(preview.renderer._rect), mock_call.pop(),
  ]


@pytest.mark.parametrize("enabled", [True, False])
def test_small_sign_action_overlap_roundtrips_without_hiding_sign(enabled):
  document = default_document()
  large = json.loads(json.dumps(document["layouts"]["large"]))
  document["layouts"]["compact"]["speed_limit"].update(x=320, y=16, enabled=enabled)
  document["layouts"]["compact"]["speed_limit_actions"].update(x=150, y=44)
  assert decode_document(json.dumps(validate_document(document))) == document
  assert document["layouts"]["large"] == large
  observation = SpeedLimitObservation(kind=ObservationKind.VALID, speed_limit_mps=20,
                                     session_id="drive", decision_id=1, presentation_id=2, action_enabled=True)
  state = road(document, speed_limit=observation, longitudinal_active=True, slc_system_long_available=True)
  assert (speed_limit_bounds(Profile.COMPACT, state) is not None) is enabled
  assert slc_controls(Profile.COMPACT, state) == ()


def test_overlapping_sign_stays_visible_and_chooser_keeps_exact_input():
  from openpilot.starpilot.ui.onroad_state import SlcActionKind
  from openpilot.starpilot.ui.tests.test_slc_ui_actions import tap_sign, tap_control
  document = default_document()
  document['layouts']['compact']['speed_limit_actions'].update(x=150, y=44)
  observation = SpeedLimitObservation(kind=ObservationKind.VALID, speed_limit_mps=20,
                                     pending_speed_limit_mps=15, session_id='drive', decision_id=1,
                                     presentation_id=2, action_enabled=True)
  state = road(document, speed_limit=observation, longitudinal_active=True, slc_system_long_available=True)
  fonts = NS(profile=Profile.COMPACT, measure=lambda *a, **k: NS(width=10, height=20), vertical_ink=lambda text, role, size: (0., size*.7),
             horizontal_ink=lambda text, role, size: (0., 10.), draw=Mock())
  hud = CompactHudRenderer(Mock(spec=BitmapFonts, **vars(fonts)), Mock())
  recovery = replace(state, speed_limit=replace(observation, pending_speed_limit_mps=None))
  with patch.object(hud._speed_limit_widget.shapes, 'outline'):
    hud._speed_limit_sign(recovery)
  assert fonts.draw.called
  emit = Mock()
  inputs = OnroadInput(emit, Profile.COMPACT)
  for kind in (SlcActionKind.ACCEPT, SlcActionKind.REJECT):
    emit.reset_mock()
    tap_sign(inputs, state)
    tap_control(inputs, state, kind)
    inputs.release(238, 73, state)
    emit.assert_called_once()
    assert emit.call_args.args[0].kind == kind


@pytest.mark.parametrize('profile', ['large', 'compact'])
def test_pip_circles_are_independent_widgets_and_migrate_old_camera_pair(profile):
  import copy
  document = default_document()
  metadata = customization_metadata()['profiles'][profile]['widgets']
  assert 'vasm' not in metadata
  for key in ('pip_left', 'pip_right'):
    assert metadata[key]['kind'] == 'pip_camera'
    assert metadata[key]['width'] == metadata[key]['height']
  right = copy.deepcopy(document['layouts'][profile]['pip_right'])
  document['layouts'][profile]['pip_left']['enabled'] = False
  assert validate_document(document)['layouts'][profile]['pip_right'] == right

  legacy = copy.deepcopy(document)
  for name, layout in legacy['layouts'].items():
    layout.pop('pip_left')
    layout.pop('pip_right')
    layout['vasm'] = {'x': 1200 if name == 'large' else 300, 'y': 650 if name == 'large' else 30, 'enabled': False}
  migrated = validate_document(legacy)
  for name, layout in migrated['layouts'].items():
    assert 'vasm' not in layout
    assert not layout['pip_left']['enabled'] and not layout['pip_right']['enabled']
    assert layout['pip_left']['x'] <= layout['pip_right']['x']
    assert layout['current_speed' if name == 'large' else 'max_speed'] == document['layouts'][name]['current_speed' if name == 'large' else 'max_speed']
  assert validate_document(migrated) == migrated

  for layout in legacy['layouts'].values():
    layout.pop('vasm')
  assert validate_document(legacy)['layouts'][profile]['pip_left']['enabled']


def test_stopped_timer_widget_is_optional_off_and_can_yield_to_slc_when_on():
  older = default_document()
  del older["layouts"]["compact"][STOPPED_TIMER_WIDGET]
  older["widgetOrder"] = {"compact": [key for key in DEFAULT_WIDGET_ORDER["compact"] if key != STOPPED_TIMER_WIDGET]}
  upgraded = validate_document(older)
  assert upgraded["layouts"]["compact"][STOPPED_TIMER_WIDGET] == {"x": 18, "y": 48, "enabled": False}
  assert upgraded["widgetOrder"]["compact"][-1] == STOPPED_TIMER_WIDGET
  assert upgraded["layouts"]["large"][STOPPED_TIMER_WIDGET]["enabled"] is False
  # The transient timer yields at render time when SLC needs its area.
  parked = default_document()
  parked["layouts"]["compact"][STOPPED_TIMER_WIDGET].update(y=120)
  validate_document(parked)
  parked["layouts"]["compact"][STOPPED_TIMER_WIDGET]["enabled"] = True
  assert validate_document(parked) == parked


def test_show_max_speed_is_an_optional_boolean():
  document = default_document()
  assert "showMaxSpeed" not in validate_document(document)
  document["showMaxSpeed"] = False
  assert validate_document(document)["showMaxSpeed"] is False
  document["showMaxSpeed"] = 0
  with pytest.raises(ValueError):
    validate_document(document)


def test_stopped_timer_draws_inside_its_moved_widget():
  from openpilot.starpilot.ui import onroad
  document = default_document()
  view = onroad.OnroadView.__new__(onroad.OnroadView)
  view.personality = Mock()
  view.fonts = Mock(spec=BitmapFonts)
  view.fonts.profile = Profile.COMPACT
  view.fonts.measure.return_value = NS(width=100, height=20)
  state = road(document)
  view._stopped_timer(state, 75)
  view.fonts.draw.assert_not_called()
  document["layouts"]["compact"][STOPPED_TIMER_WIDGET]["enabled"] = True
  view._stopped_timer(state, 75)
  default = [call.args[3:5] for call in view.fonts.draw.call_args_list[1::2]]
  assert default == [((476 - 100) / 2, 101 - 10), ((476 - 100) / 2, 149 - 10)]
  document["layouts"]["compact"][STOPPED_TIMER_WIDGET].update(x=0, y=0)
  view.fonts.draw.reset_mock()
  view._stopped_timer(state, 75)
  assert [call.args[3:5] for call in view.fonts.draw.call_args_list[1::2]] == [(170, 53 - 10), (170, 101 - 10)]


def test_projection_timer_renders_at_saved_coordinates_without_native_timer_enabled():
  from openpilot.starpilot.ui import onroad
  from openpilot.starpilot.system.starport.projection_layout import default_layout_for_viewport, projection_customization
  layout = default_layout_for_viewport((2880, 1080))
  layout['widgets'][STOPPED_TIMER_WIDGET].update(enabled=True, x=450, y=600)
  document = projection_customization(layout, default_document())
  view = onroad.OnroadView.__new__(onroad.OnroadView)
  view.personality = Mock()
  view.fonts = Mock(spec=BitmapFonts)
  view.fonts.profile = Profile.LARGE
  view.fonts.measure.return_value = NS(width=100, height=20)
  view._stopped_timer(road(document), 134)
  text_calls = view.fonts.draw.call_args_list[1::2]
  assert [call.args[0] for call in text_calls] == ['2 minutes', '14 seconds']
  assert [call.args[3:5] for call in text_calls] == [(730, 669.5), (730, 741.5)]
  view.fonts.draw.reset_mock()
  document['layouts']['large'][STOPPED_TIMER_WIDGET]['enabled'] = False
  view._stopped_timer(road(document), 134)
  view.fonts.draw.assert_not_called()


def test_large_navigation_widgets_migrate_without_changing_existing_layout_or_order():
  import copy
  keys = ("nav_card", "nav_map", STOPPED_TIMER_WIDGET)
  older = default_document()
  older["layouts"]["large"]["current_speed"].update(x=700, enabled=False)
  older["widgetOrder"] = {"large": [key for key in reversed(DEFAULT_WIDGET_ORDER["large"]) if key not in keys]}
  for key in keys:
    del older["layouts"]["large"][key]
  original = copy.deepcopy(older)
  upgraded = validate_document(older)
  assert older == original
  assert upgraded["layouts"]["large"]["current_speed"] == older["layouts"]["large"]["current_speed"]
  assert upgraded["widgetOrder"]["large"][:len(older["widgetOrder"]["large"])] == older["widgetOrder"]["large"]
  assert set(upgraded["widgetOrder"]["large"]) == set(upgraded["layouts"]["large"])
  for key in keys:
    assert upgraded["layouts"]["large"][key] == default_document()["layouts"]["large"][key]
    assert upgraded["layouts"]["large"][key]["enabled"] is (key == "nav_card")
  upgraded["layouts"]["large"]["nav_map"].update(x=100, y=300, width=900, height=500, opacity=35, enabled=True)
  upgraded["layouts"]["large"]["nav_card"].update(x=800, y=500, enabled=True)
  assert decode_document(json.dumps(upgraded)) == upgraded
  assert widget_size(upgraded, "large", "nav_map") == (900, 500)


@pytest.mark.parametrize("change", [{"width": 279}, {"height": 199}, {"opacity": 14}, {"opacity": 101},
                                   {"opacity": True}, {"width": 560.5}, {"width": 900}, {"x": float("nan")}])
def test_native_map_rejects_invalid_size_opacity_and_offscreen_placements(change):
  document = default_document()
  document["layouts"]["large"]["nav_map"].update(change)
  with pytest.raises(ValueError):
    validate_document(document)


def test_big_widgets_can_use_the_space_a_hidden_sidebar_adds():
  from openpilot.starpilot.ui.onroad_customization import ANCHOR_SHIFT, PROFILES
  widgets = PROFILES["large"]["widgets"]
  document = default_document()
  speed = document["layouts"]["large"]["current_speed"]
  assert widgets["current_speed"]["bounds"]["x"] == 30 - 150 and widgets["steering_wheel"]["bounds"]["x"] == 30 - 300
  assert all(widget["expandable"] for widget in widgets.values())
  speed["x"] = 30 - 150
  assert validate_document(document)["layouts"]["large"]["current_speed"]["x"] == -120
  speed["x"] = 30 - 151
  with pytest.raises(ValueError):
    validate_document(document)
  # Every widget reaches the right edge of the full screen, and not past it.
  for key in widgets:
    document = default_document()
    document["layouts"]["large"]["speed_limit_actions"]["enabled"] = False
    placed, width = document["layouts"]["large"][key], widget_size(document, "large", key)[0]
    placed["x"] = 2130 - width - ANCHOR_SHIFT.get(key, 0) * 300
    validate_document(document)
    placed["x"] += 1
    with pytest.raises(ValueError):
      validate_document(document)


@pytest.mark.parametrize("right,bottom", [(False, False), (False, True), (True, False), (True, True)])
def test_every_large_widget_reaches_each_corner_and_fits_when_sidebar_returns(right, bottom):
  from openpilot.starpilot.ui.onroad_customization import ANCHOR_SHIFT, PROFILES, fit_to_road
  for key in PROFILES["large"]["widgets"]:
    document = default_document()
    document["layouts"]["large"]["speed_limit_actions"]["enabled"] = False
    width, height = widget_size(document, "large", key)
    shift = ANCHOR_SHIFT.get(key, 0) * 300
    x, y = (2130 - width if right else 30) - shift, 1050 - height if bottom else 30
    document["layouts"]["large"][key].update(x=x, y=y)
    assert validate_document(document)["layouts"]["large"][key]["x"] == x
    assert fit_to_road(document, 2160) is document
    fitted = fit_to_road(document, 1860)["layouts"]["large"][key]
    assert 30 <= fitted["x"] <= 1830 - width
    assert fitted["y"] == y
    assert document["layouts"]["large"][key]["x"] == x


def test_fit_to_road_pulls_widgets_in_only_where_the_road_is_narrower():
  from openpilot.starpilot.ui.onroad_customization import fit_to_road
  document = default_document()
  large = document["layouts"]["large"]
  large["stopped_timer"]["x"] = 2130 - 660
  large["steering_wheel"]["x"] = -270
  large["current_speed"]["x"] = -120
  assert fit_to_road(document, 2160) is document
  fitted = fit_to_road(document, 1860)["layouts"]["large"]
  assert fitted["stopped_timer"]["x"] == 1830 - 660
  assert fitted["steering_wheel"]["x"] == 30
  assert fitted["current_speed"]["x"] == 30
  assert fitted["cruise_limits"] is large["cruise_limits"]
  assert large["stopped_timer"]["x"] == 2130 - 660
  assert fit_to_road(default_document(), 1860)["layouts"]["large"] == default_document()["layouts"]["large"]


def test_jetlink_status_is_an_opt_in_big_widget_with_background_opacity():
  document = default_document()
  placed = document["layouts"]["large"]["jetlink_status"]
  assert placed["enabled"] is False and placed["opacity"] == 85
  assert "jetlink_status" not in document["layouts"]["compact"]
  placed.update(enabled=True, x=100, y=300, opacity=40)
  assert validate_document(document)["layouts"]["large"]["jetlink_status"]["opacity"] == 40
  older = default_document()
  del older["layouts"]["large"]["jetlink_status"]
  assert validate_document(older)["layouts"]["large"]["jetlink_status"]["enabled"] is False
  for bad in (101, -1, 50.5, True, None):
    document["layouts"]["large"]["jetlink_status"]["opacity"] = bad
    with pytest.raises(ValueError):
      validate_document(document)
