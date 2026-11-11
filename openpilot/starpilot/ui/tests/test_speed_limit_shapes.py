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
  assert gpu['draw_texture_pro'].call_count == 5
  assert all(call.args[1].x == shapes.SPRITES['ring'][0] for call in gpu['draw_texture_pro'].call_args_list)
  gpu['draw_texture_pro'].reset_mock()
  owner.outline(59, 66, shapes.rl.WHITE)
  owner.outline(59, 66, shapes.rl.WHITE)
  assert gpu['draw_texture_pro'].call_count == 10
  _, source, destination, _, _, _ = gpu['draw_texture_pro'].call_args.args
  assert (source.width, source.height) == (136, 152)
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


@pytest.mark.parametrize('scale', (1., 8 / 9))
def test_narrow_outline_reuses_the_atlas_and_keeps_its_center(gpu, scale):
  owner = shapes.SpeedLimitShapes()
  owner.outline(64, 65, shapes.rl.WHITE, scale=scale, width_scale=.9)
  assert gpu['draw_texture_pro'].call_count == 5
  _, source, destination, origin, _, _ = gpu['draw_texture_pro'].call_args.args
  assert (source.width, source.height) == (136, 152)
  assert (destination.width, destination.height) == pytest.approx((136 * scale * .9, 152 * scale))
  assert (origin.x, origin.y) == pytest.approx((destination.width / 2, destination.height / 2))
  owner.outline(64, 65, shapes.rl.WHITE)
  assert gpu['draw_texture_pro'].call_args.args[3].x == 68
  gpu['load_texture_from_image'].assert_called_once()
  owner.close()


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
  owner.outline(120, 76, shapes.rl.WHITE)
  gpu['draw_texture_pro'].reset_mock()
  owner.wheel_key(True, 50, 135.5, scale=1)
  owner.wheel_key(False, 160, 135.5, scale=1)
  assert {call.args[1].x for call in gpu['draw_texture_pro'].call_args_list} == {280, 328}
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


@pytest.mark.parametrize('profile', ('large', 'compact'))
def test_buttons_have_smooth_symmetric_contours_and_transparent_interiors(profile):
  colored_areas = []
  with Image.open(shapes.ATLAS_PATH) as atlas:
    for name in (f'button_{profile}', f'button_{profile}_pressed'):
      x, y, width, height = shapes.SPRITES[name]
      pixels = np.asarray(atlas.crop((x, y, x + width, y + height)))
      alpha = pixels[..., 3]
      assert shapes.SPRITE_DENSITIES[name] == 2
      assert np.array_equal(pixels, pixels[::-1]) and np.array_equal(pixels, pixels[:, ::-1])
      assert not alpha[40:-40, 40:-40].any()
      assert np.mean(alpha == 0) > .85
      assert ((alpha > 0) & (alpha < 255)).sum() > 500
      colored_areas.append(np.count_nonzero((pixels[..., 0] > 128) & (alpha > 0)))
  assert colored_areas[1] > colored_areas[0], 'Press feedback strengthens the same contour'


def test_buttons_share_one_upload_and_each_uses_one_quad(gpu):
  owner = shapes.SpeedLimitShapes()
  for profile, scale in (('compact', 1.), ('large', 1.342)):
    for pressed in (False, True):
      gpu['draw_texture_pro'].reset_mock()
      owner.button(200, 96, shapes.rl.GREEN, profile=profile, scale=scale, pressed=pressed)
      gpu['draw_texture_pro'].assert_called_once()
      _, source, destination, origin, _, color = gpu['draw_texture_pro'].call_args.args
      assert (source.x, source.y, source.width, source.height) == shapes.SPRITES[f'button_{profile}' + ('_pressed' if pressed else '')]
      assert (destination.x, destination.y) == (200, 96)
      assert (destination.width, destination.height) == pytest.approx((source.width / 2 * scale, source.height / 2 * scale))
      assert (origin.x, origin.y) == pytest.approx((source.width / 4 * scale, source.height / 4 * scale))
      assert color == shapes.rl.GREEN
  gpu['load_texture_from_image'].assert_called_once()
  owner.close()
  gpu['unload_texture'].assert_called_once()
