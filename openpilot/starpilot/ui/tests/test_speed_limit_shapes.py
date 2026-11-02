"""The atlas has smooth coverage, solid arrow joins, and bounded GPU ownership."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
from PIL import Image
import pytest

from openpilot.starpilot.ui import speed_limit_shapes as shapes
from openpilot.starpilot.ui.assets.generate_speed_limit_shapes import generate


def test_atlas_matches_generated_geometry_and_has_symmetric_antialiased_edges():
  with Image.open(shapes.ATLAS_PATH) as atlas:
    assert atlas.size == shapes.ATLAS_SIZE
    assert atlas.tobytes() == generate().tobytes()
    for name, (x, y, width, height) in shapes.SPRITES.items():
      alpha = np.asarray(atlas.crop((x, y, x+width, y+height)))[..., 3]
      assert np.array_equal(alpha, alpha[:, ::-1])
      assert ((alpha > 0) & (alpha < 255)).sum() > 10
      assert not alpha[0].any() and not alpha[-1].any()
      assert not alpha[:, 0].any() and not alpha[:, -1].any()
      if name.startswith('arrow'):
        for row in alpha:
          ink = np.flatnonzero(row > 40)
          assert not len(ink) or ink[-1] - ink[0] + 1 == len(ink)
        center = alpha[:, width//2]
        painted = np.flatnonzero(center > 40)
        assert painted[-1] - painted[0] + 1 == len(painted)
      if name == 'outline':
        assert not alpha[18:-18, 18:-18].any(), 'the camera must remain visible inside the sign'
        assert np.array_equal(alpha, alpha[::-1]), 'rounded corners must be symmetric'


@pytest.fixture
def gpu(monkeypatch):
  monkeypatch.setattr(shapes.rl, 'is_window_ready', lambda: True)
  calls = {}
  for name, value in (('load_image', NS(data=1, width=shapes.ATLAS_SIZE[0], height=shapes.ATLAS_SIZE[1])),
                      ('load_texture_from_image', NS(id=7)), ('unload_image', None), ('unload_texture', None),
                      ('set_texture_filter', None), ('set_texture_wrap', None), ('draw_texture_pro', None)):
    calls[name] = Mock(return_value=value)
    monkeypatch.setattr(shapes.rl, name, calls[name])
  return calls


def test_shapes_share_one_upload_and_arrow_is_one_draw_in_either_direction(gpu):
  owner = shapes.SpeedLimitShapes()
  owner.circle(389, 87, shapes.rl.RED)
  assert gpu['draw_texture_pro'].call_count == 2
  gpu['draw_texture_pro'].reset_mock()
  owner.outline(59, 66, shapes.rl.WHITE)
  owner.outline(59, 66, shapes.rl.WHITE)
  assert gpu['draw_texture_pro'].call_count == 2
  _, source, destination, _, _, _ = gpu['draw_texture_pro'].call_args.args
  assert (source.width, source.height) == (128, 144)
  assert (destination.x, destination.y) == (59, 66)
  for direction, rotation in ((1, 0), (-1, 180)):
    gpu['draw_texture_pro'].reset_mock()
    owner.arrow('compact', 389, 49, direction, shapes.rl.BLACK)
    gpu['draw_texture_pro'].assert_called_once()
    assert gpu['draw_texture_pro'].call_args.args[-2] == rotation
  gpu['load_image'].assert_called_once()
  gpu['load_texture_from_image'].assert_called_once()
  gpu['unload_image'].assert_called_once()
  owner.close()
  owner.close()
  gpu['unload_texture'].assert_called_once()


def test_bad_atlas_dimensions_release_image_without_uploading(gpu):
  gpu['load_image'].return_value.width = 4096
  with pytest.raises(ValueError, match='Invalid speed-limit'):
    shapes.SpeedLimitShapes().circle(0, 0, shapes.rl.RED)
  gpu['unload_image'].assert_called_once()
  gpu['load_texture_from_image'].assert_not_called()


def test_large_sign_scale_changes_destination_and_rotation_origin_only(gpu):
  owner = shapes.SpeedLimitShapes(1.25)
  owner.arrow('compact', 345.75, 111.25, -1, shapes.rl.BLACK)
  _, source, destination, origin, rotation, _ = gpu['draw_texture_pro'].call_args.args
  assert (source.width, source.height) == (32, 48)
  assert (destination.x, destination.y, destination.width, destination.height) == (345.75, 111.25, 20, 30)
  assert (origin.x, origin.y, rotation) == (10, 15, 180)
  owner.close()


def test_small_arrow_has_larger_ink_at_double_density():
  x, y, width, height = shapes.SPRITES['arrow_compact']
  with Image.open(shapes.ATLAS_PATH) as atlas:
    bounds = atlas.crop((x, y, x+width, y+height)).getchannel('A').point(lambda alpha: 255 if alpha > 40 else 0).getbbox()
  density = shapes.SPRITE_DENSITIES['arrow_compact']
  assert density == 2
  assert bounds is not None
  assert ((bounds[2]-bounds[0])/density, (bounds[3]-bounds[1])/density) == (12, 16)


def test_confirmation_arrow_has_the_same_fill_without_the_dark_outline():
  with Image.open(shapes.ATLAS_PATH) as atlas:
    sprites = {}
    for name in ('arrow_compact', 'arrow_confirmation'):
      x, y, width, height = shapes.SPRITES[name]
      sprites[name] = np.asarray(atlas.crop((x, y, x + width, y + height)))
    outlined = sprites['arrow_compact']
    plain = sprites['arrow_confirmation']
    assert shapes.SPRITE_DENSITIES['arrow_confirmation'] == 2
    assert np.all(plain[..., :3] == 255)
    assert np.all(plain[..., 3] <= outlined[..., 3])
    assert np.any(plain[..., 3] < outlined[..., 3])
    assert np.array_equal(plain[..., 3] == 255, outlined[..., 3] == 255)


@pytest.mark.parametrize('direction,rotation', [(1, 0), (-1, 180)])
def test_confirmation_arrow_uses_the_shared_atlas_in_both_directions(gpu, direction, rotation):
  owner = shapes.SpeedLimitShapes()
  owner.arrow('confirmation', 100, 50, direction, shapes.rl.WHITE, scale=1.1)
  _, source, destination, _, drawn_rotation, _ = gpu['draw_texture_pro'].call_args.args
  assert (source.x, source.y, source.width, source.height) == (280, 224, 32, 48)
  assert (destination.width, destination.height) == pytest.approx((17.6, 26.4))
  assert drawn_rotation == rotation
  owner.close()


def test_wheel_cues_have_smooth_rounded_edges_and_distinct_symbols():
  with Image.open(shapes.ATLAS_PATH) as atlas:
    cues = {}
    for name in ('wheel_plus', 'wheel_minus'):
      x, y, width, height = shapes.SPRITES[name]
      cues[name] = np.asarray(atlas.crop((x, y, x + width, y + height)))
      alpha = cues[name][..., 3]
      assert shapes.SPRITE_DENSITIES[name] == 2
      assert alpha[5, 5] == 0 and alpha[24, 5] > 0
      assert ((alpha > 0) & (alpha < 255)).sum() > 40
      assert np.array_equal(alpha, alpha[::-1]) and np.array_equal(alpha, alpha[:, ::-1])
    assert cues['wheel_plus'][16, 24, 3] == 255
    assert cues['wheel_minus'][16, 24, 3] == 0
    assert np.array_equal(cues['wheel_plus'][24, 24], cues['wheel_minus'][24, 24])


def test_wheel_cues_share_the_confirmation_atlas(gpu):
  owner = shapes.SpeedLimitShapes()
  owner.confirmation((0, 0, 240, 152))
  gpu['draw_texture_pro'].reset_mock()
  owner.wheel_key(True, 50, 135.5, scale=1)
  owner.wheel_key(False, 160, 135.5, scale=1)
  assert gpu['draw_texture_pro'].call_count == 2
  assert [call.args[1].x for call in gpu['draw_texture_pro'].call_args_list] == [280, 328]
  assert all(call.args[2].width == 24 for call in gpu['draw_texture_pro'].call_args_list)
  gpu['load_image'].assert_called_once()
  gpu['load_texture_from_image'].assert_called_once()
  owner.close()
  gpu['unload_texture'].assert_called_once()


def test_texture_setup_failure_releases_both_resources(gpu):
  gpu['set_texture_filter'].side_effect = RuntimeError('filter unavailable')
  with pytest.raises(RuntimeError, match='filter unavailable'):
    shapes.SpeedLimitShapes().circle(0, 0, shapes.rl.RED)
  gpu['unload_texture'].assert_called_once()
  gpu['unload_image'].assert_called_once()


def test_headless_context_loads_atlas_without_a_window(gpu, monkeypatch):
  monkeypatch.setattr(shapes.rl, 'is_window_ready', lambda: False)
  with pytest.raises(RuntimeError, match='graphics context'):
    shapes.SpeedLimitShapes().circle(0, 0, shapes.rl.RED)
  owner = shapes.SpeedLimitShapes(headless_context=True)
  owner.circle(0, 0, shapes.rl.RED)
  gpu['load_texture_from_image'].assert_called_once()


def test_short_outline_reuses_the_same_atlas_and_preserves_corner_scale(gpu):
  owner = shapes.SpeedLimitShapes()
  owner.outline(59, 58, shapes.rl.WHITE, short=True)
  assert gpu['draw_texture_pro'].call_count == 2
  rows = [call.args for call in gpu['draw_texture_pro'].call_args_list]
  assert [(r[1].x, r[1].y, r[1].width, r[1].height) for r in rows] == [(256, 0, 128, 64), (256, 80, 128, 64)]
  assert [(r[2].x, r[2].y, r[2].width, r[2].height) for r in rows] == [(59, -6, 128, 64), (59, 58, 128, 64)]
  assert all((r[3].x, r[3].y) == (64, 0) for r in rows)
  owner.outline(59, 66, shapes.rl.WHITE)
  owner.outline(59, 58, shapes.rl.WHITE, short=True)
  gpu['load_image'].assert_called_once()
  gpu['load_texture_from_image'].assert_called_once()
  owner.close()
  gpu['unload_texture'].assert_called_once()


@pytest.mark.parametrize('scale', [1, 1.3416407865])
@pytest.mark.parametrize('opened', [False, True])
def test_confirmation_reuses_atlas_and_preserves_corners_without_overlapping_slices(gpu, scale, opened):
  owner = shapes.SpeedLimitShapes()
  width = (312 if opened else 240) * scale
  bounds = (12., 8., 12 + width, 8 + 152 * scale)
  owner.confirmation(bounds, opened=opened, strength=1)
  rows = [call.args for call in gpu['draw_texture_pro'].call_args_list]
  assert len(rows) == (3 if opened else 6)
  cap = (16 if opened else 24) * scale
  for group in (rows[:3], rows[3:]):
    if not group:
      continue
    left, middle, right = [row[2] for row in group]
    assert left.x == pytest.approx(bounds[0])
    assert left.width == pytest.approx(cap)
    assert middle.x == pytest.approx(left.x + left.width)
    assert right.x == pytest.approx(middle.x + middle.width)
    assert right.x + right.width == pytest.approx(bounds[2])
    assert right.width == pytest.approx(cap)
    assert all(row[2].y + row[2].height == pytest.approx(bounds[3]) for row in group)
  assert rows[0][2].height == pytest.approx(152 * scale)
  if not opened:
    assert rows[3][2].y == pytest.approx(bounds[1] + 86 * scale)
  owner.confirmation(bounds, opened=opened)
  gpu['load_image'].assert_called_once()
  gpu['load_texture_from_image'].assert_called_once()
  owner.close()
  gpu['unload_texture'].assert_called_once()


def test_confirmation_has_translucent_fill_a_defined_rim_and_a_local_pulse():
  with Image.open(shapes.ATLAS_PATH) as atlas:
    x, y, w, h = shapes.SPRITES['confirmation']
    face = np.asarray(atlas.crop((x, y, x + w, y + h)))
    assert face[h // 2, w // 2, 3] == 230
    assert face[2, w // 2, 3] > 230
    x, y, w, h = shapes.SPRITES['confirmation_pulse']
    pulse = np.asarray(atlas.crop((x, y, x + w, y + h)))
    assert pulse[2, w // 2, 3] == 0
    assert 0 < pulse[-3, w // 2, 3] <= 51


@pytest.mark.parametrize('scale', [1, 1.3416407865])
def test_native_confirmation_blends_once_and_pulses_only_below_the_speed(scale):
  rl = shapes.rl
  owns_context = not rl.is_window_ready()
  if owns_context:
    rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
    rl.init_window(128, 128, 'SLC surface fixture')
  target = rl.load_render_texture(460, 240)
  owner = shapes.SpeedLimitShapes()

  def capture(bounds, opened, strength, background=(90, 120, 160)):
    rl.begin_texture_mode(target)
    rl.clear_background(rl.Color(*background, 255))
    owner.confirmation(bounds, opened=opened, strength=strength)
    rl.end_texture_mode()
    image = rl.load_image_from_texture(target.texture)
    try:
      rl.image_flip_vertical(image)
      return np.frombuffer(image.data[:image.width * image.height * 4], dtype=np.uint8).reshape(image.height, image.width, 4).copy()
    finally:
      rl.unload_image(image)

  try:
    for opened in (False, True):
      width = (312 if opened else 240) * scale
      bounds = (16, 16, 16 + width, 16 + 152 * scale)
      rest, peak = (capture(bounds, opened, strength) for strength in (0, 1))
      assert np.array_equal(rest[:int(16 + 86 * scale)], peak[:int(16 + 86 * scale)])
      if opened:
        assert np.array_equal(rest, peak)
      else:
        assert not np.array_equal(rest, peak)
      changed = np.any(rest != peak, axis=-1)
      assert not changed[:, :16].any() and not changed[:, int(np.ceil(bounds[2])):].any()
      assert not changed[int(np.ceil(bounds[3])):].any()
      row = int(16 + 76 * scale)
      columns = slice(18, int(bounds[2]) - 2)
      assert np.ptp(rest[row, columns, :3], axis=0).max() <= 1, 'slices must join without a seam'
      lighter = capture(bounds, opened, 0, (190, 220, 255))
      difference = lighter[row, int(16 + width / 2), :3].astype(int) - rest[row, int(16 + width / 2), :3]
      assert np.all((difference >= 8) & (difference <= 12)), 'the core should transmit about 10% of the camera'
  finally:
    owner.close()
    rl.unload_render_texture(target)
    if owns_context:
      rl.close_window()
