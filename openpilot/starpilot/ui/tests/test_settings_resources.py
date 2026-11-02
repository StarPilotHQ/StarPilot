"""Native API imports with synthetic allocations for failure/ownership checks."""
import numpy as np

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.settings_assets import SettingsAssets

MODULE = "openpilot.starpilot.ui.settings_assets"


class TestSettingsResources(unittest.TestCase):
  def test_galaxy_menu_icon_is_the_approved_transparent_white_artwork(self):
    root = Path(__file__).parents[3] / "selfdrive/assets"
    icon = root / "icons_mici/settings/galaxy.png"
    record = next(item for item in json.loads(Path(__file__).parents[1].joinpath("settings-assets.json").read_text())["files"]
                  if item["file"] == "icons_mici/settings/galaxy.png")
    self.assertEqual(icon.stat().st_size, record["bytes"])
    with icon.open("rb") as stream:
      header = stream.read(24)
    self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
    self.assertEqual(tuple(int.from_bytes(header[offset:offset + 4], "big") for offset in (16, 20)), (256, 195))
    self.assertEqual(hashlib.sha256(icon.read_bytes()).hexdigest(),
                     "817d30f1d18084ffa81567c70a1797982b7cdc404aa078a231484f50fc113dd6")
    self.assertEqual(record["sha256"], hashlib.sha256(icon.read_bytes()).hexdigest())

  def test_menu_images_keep_native_proportions_at_both_pixel_scales(self):
    for native, bounds, expected in (((256, 195), (64, 64), (64, 49)), ((128, 68), (72, 56), (72, 38))):
      for pixel_scale in (1, 2):
        with self.subTest(native=native, pixel_scale=pixel_scale), TemporaryDirectory() as directory, ExitStack() as stack:
          path = Path(directory) / "fixture.png"
          path.write_bytes(b"fixture")
          assets = SettingsAssets(Path(directory))
          assets._pixel_scale = (pixel_scale, pixel_scale)
          assets._manifest = {"fixture.png": {"bytes": 7, "sha256": hashlib.sha256(b"fixture").hexdigest()}}
          image = SimpleNamespace(data=np.zeros(4, dtype=np.uint8), width=native[0], height=native[1])
          texture = SimpleNamespace(id=17, width=0, height=0)
          stack.enter_context(patch(f"{MODULE}.rl.load_image", return_value=image))
          stack.enter_context(patch(f"{MODULE}.rl.image_resize"))
          stack.enter_context(patch(f"{MODULE}.rl.load_texture_from_image", return_value=texture))
          stack.enter_context(patch(f"{MODULE}.rl.set_texture_filter"))
          stack.enter_context(patch(f"{MODULE}.rl.set_texture_wrap"))
          release = stack.enter_context(patch(f"{MODULE}.rl.unload_image"))
          self.assertIs(assets.image("fixture.png", *bounds), texture)
          self.assertEqual((texture.width, texture.height), expected)
          self.assertIs(assets.image("fixture.png", *bounds), texture)
          release.assert_called_once()

  def test_unreviewed_or_changed_bytes_are_rejected_before_native_decode(self):
    with TemporaryDirectory() as directory, patch(f"{MODULE}.rl.load_image") as load:
      assets = SettingsAssets(Path(directory))
      with self.assertRaises(ValueError):
        assets.image("../../outside.png", 10, 10)
      image = Path(directory) / "icons/backspace.png"
      image.parent.mkdir()
      image.write_bytes(b"wrong bytes")
      with self.assertRaisesRegex(ValueError, "reviewed manifest"):
        assets.image("icons/backspace.png", 70, 70)
      load.assert_not_called()

  def test_failed_upload_and_filter_release_only_owned_allocations(self):
    for failure in ("empty_image", "gpu_upload", "filter"):
      with self.subTest(failure=failure), TemporaryDirectory() as directory, ExitStack() as stack:
        path = Path(directory) / "fixture.png"
        path.write_bytes(b"reviewed-fixture")
        assets = SettingsAssets(Path(directory))
        assets._manifest = {"fixture.png": {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
        image = SimpleNamespace(data=None if failure == "empty_image" else np.zeros(4, dtype=np.uint8), width=1, height=1)
        texture = SimpleNamespace(id=0 if failure == "gpu_upload" else 17)
        stack.enter_context(patch(f"{MODULE}.rl.load_image", return_value=image))
        upload = stack.enter_context(patch(f"{MODULE}.rl.load_texture_from_image", return_value=texture))
        free_image = stack.enter_context(patch(f"{MODULE}.rl.unload_image"))
        free_texture = stack.enter_context(patch(f"{MODULE}.rl.unload_texture"))
        stack.enter_context(patch(f"{MODULE}.rl.set_texture_filter", side_effect=RuntimeError("filter failed")))
        with self.assertRaises((ValueError, RuntimeError)):
          assets.image("fixture.png", 1, 1)
        self.assertEqual(assets._images, {})
        self.assertEqual(free_image.call_count, failure != "empty_image")
        self.assertEqual(free_texture.call_count, failure == "filter")
        if failure == "empty_image":
          upload.assert_not_called()

  def test_vector_render_failure_unwinds_modes_and_owned_target(self):
    assets = SettingsAssets(Path("unused"))
    target = SimpleNamespace(id=20, texture=SimpleNamespace(id=21))
    with ExitStack() as stack:
      stack.enter_context(patch(f"{MODULE}.rl.load_render_texture", return_value=target))
      release = stack.enter_context(patch(f"{MODULE}.rl.unload_render_texture"))
      calls = {}
      for name in ("begin_texture_mode", "clear_background", "rl_set_blend_factors_separate", "begin_blend_mode",
                   "end_blend_mode", "end_texture_mode"):
        calls[name] = stack.enter_context(patch(f"{MODULE}.rl.{name}"))
      stack.enter_context(patch(f"{MODULE}.draw_icon_geometry", side_effect=RuntimeError("draw failed")))
      with self.assertRaisesRegex(RuntimeError, "draw failed"):
        assets.prepare_icon("sound", 1, rl.Color(255, 255, 255, 255))
      release.assert_called_once_with(target)
      calls["end_blend_mode"].assert_called_once()
      calls["end_texture_mode"].assert_called_once()
      self.assertEqual(assets._icons, {})

  def test_repeated_close_releases_each_owned_image_and_vector_target_once(self):
    assets = SettingsAssets(Path("unused"))
    image, target = SimpleNamespace(id=17), SimpleNamespace(id=20)
    assets._images[("fixture", 1, 1)], assets._icons[("vector",)] = image, target
    with patch(f"{MODULE}.rl.is_window_ready", return_value=True), \
         patch(f"{MODULE}.rl.unload_texture") as free_image, patch(f"{MODULE}.rl.unload_render_texture") as free_target:
      assets.close()
      assets.close()
      free_image.assert_called_once_with(image)
      free_target.assert_called_once_with(target)
      self.assertEqual(assets._images, {})
      self.assertEqual(assets._icons, {})


if __name__ == "__main__":
  unittest.main()
