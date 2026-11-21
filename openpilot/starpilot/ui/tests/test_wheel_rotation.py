"""Large-wheel opt-in rotation, qualified samples, saved compatibility and draw geometry."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from openpilot.common.params import Params

from openpilot.starpilot.ui.onroad_customization import customization_metadata, default_document, decode_document, validate_document
from openpilot.starpilot.ui.onroad_large_widgets import SteeringWheelWidget
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation, _inside_widget
from openpilot.starpilot.ui.raylib import rl
from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import NOW, ui_fake
from openpilot.starpilot.ui.wheel_rotation import observe_wheel_rotation, write_rotation


def test_opt_in_roundtrip_and_legacy_default():
  document = default_document()
  wheel = document['layouts']['large']['steering_wheel']
  old = json.dumps(document)
  assert not decode_document(old)['layouts']['large']['steering_wheel'].get('rotate', False)
  assert 'rotate' not in wheel  # Validation must not mutate the supplied document.
  wheel['rotate'] = True
  assert decode_document(json.dumps(document)) == document
  assert customization_metadata()['profiles']['large']['widgets']['steering_wheel']['rotateToggle'] is True
  assert 'rotateToggle' not in customization_metadata()['profiles']['compact']['widgets']['steering_wheel']
  for bad in (None, 1, 'true', [], {}):
    wheel['rotate'] = bad
    with pytest.raises(ValueError):
      validate_document(document)
  wheel['rotate'] = False
  for profile, key in (('compact', 'steering_wheel'), ('large', 'driver_monitor')):
    other = default_document()
    other['layouts'][profile][key]['rotate'] = True
    with pytest.raises(ValueError):
      validate_document(other)


def test_only_finite_valid_can_angle_uses_small_ui_sign():
  for angle in (-720., -45, 0, 45., 720):
    assert observe_wheel_rotation(NS(canValid=True, canTimeout=False, steeringAngleDeg=angle)) == -angle
  for angle in (None, True, '45', float('nan'), float('inf'), -float('inf')):
    assert observe_wheel_rotation(NS(canValid=True, canTimeout=False, steeringAngleDeg=angle)) == 0
  for car in (None, NS(), NS(canValid=False, canTimeout=False, steeringAngleDeg=45),
              NS(canValid=True, canTimeout=True, steeringAngleDeg=45)):
    assert observe_wheel_rotation(car) == 0


def test_runtime_rejects_stale_previous_drive_invalid_and_offroad_samples():
  ui = ui_fake()
  vars(ui.sm.messages['carState']).update(canValid=True, canTimeout=False, steeringAngleDeg=45.)
  adapter = RuntimeSnapshotAdapter(ui)

  def rotation():
    return adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.wheel_rotation

  assert rotation() == -45
  ui.sm.logMonoTime['carState'] = NOW - 201_000_000
  assert rotation() == 0
  ui.sm.logMonoTime['carState'] = NOW
  ui.sm.recv_frame['carState'] = ui.started_frame
  assert rotation() == 0
  ui.sm.recv_frame['carState'] = ui.started_frame + 1
  ui.sm.valid['carState'] = False
  assert rotation() == 0
  ui.sm.valid['carState'] = True
  ui.started = False
  assert rotation() == 0


def test_runtime_reloads_sidecar_at_existing_appearance_refresh(tmp_path):
  ui = ui_fake()
  ui.params = Params(str(tmp_path))
  adapter = RuntimeSnapshotAdapter(ui)

  def configured():
    return adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.customization['layouts']['large']['steering_wheel'].get('rotate', False)

  assert not configured()
  assert write_rotation(ui.params, True, None, lambda: True)
  assert not configured()  # No extra poll between existing refreshes.
  adapter.invalidate_appearance()
  assert configured()
  assert write_rotation(ui.params, False, b'1', lambda: True)
  adapter.invalidate_appearance()
  assert not configured()


@pytest.mark.parametrize('size', [144, 192, 240])
def test_native_center_pivot_default_geometry_and_hit_target(size):
  state = OnroadState(True, True, 10, None, SpeedLimitObservation(), wheel_rotation=-45.)
  saved = state.customization['layouts']['large']['steering_wheel']
  saved.update(x=900, y=200, size=size)
  wheel = SteeringWheelWidget(Path('/unused'))
  wheel._texture = Mock()
  with patch.object(rl, 'draw_circle') as background, patch.object(rl, 'draw_texture_pro') as draw:
    wheel.render(rl.Rectangle(30, 30, 1800, 1020), state)
    upright = draw.call_args.args
    circle = background.call_args
    assert upright[4] == 0
    assert (upright[2].x, upright[2].y) == (900 + size / 8, 200 + size / 8)
    assert (upright[3].x, upright[3].y) == (0, 0)
    hit_before = [_inside_widget(x, y, state, 'steering_wheel', (1588, 75, 1780, 267))
                  for x, y in ((899, 200), (900, 200), (900 + size, 200 + size), (901 + size, 200))]
    saved['rotate'] = True
    for angle in (-45., 45., 0., float('nan')):
      wheel.render(rl.Rectangle(30, 30, 1800, 1020), replace(state, wheel_rotation=angle))
      args = draw.call_args.args
      assert background.call_args.args[:3] == circle.args[:3]
      assert tuple(getattr(background.call_args.args[3], c) for c in 'rgba') == tuple(getattr(circle.args[3], c) for c in 'rgba')
      assert tuple(getattr(args[-1], channel) for channel in 'rgba') == tuple(getattr(upright[-1], channel) for channel in 'rgba')
      if angle in (-45., 45.):
        assert args[4] == angle
        assert (args[2].x, args[2].y) == (900 + size / 2, 200 + size / 2)
        assert (args[3].x, args[3].y) == (size * .375, size * .375)
      else:
        assert args[4] == 0
    assert hit_before == [_inside_widget(x, y, state, 'steering_wheel', (1588, 75, 1780, 267))
                          for x, y in ((899, 200), (900, 200), (900 + size, 200 + size), (901 + size, 200))]
