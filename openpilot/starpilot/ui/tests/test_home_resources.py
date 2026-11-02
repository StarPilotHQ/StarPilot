import numpy as np
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


from openpilot.starpilot.ui.home import HomeView, validate_home_asset


class TestHomeResources(unittest.TestCase):
  def test_unreviewed_or_changed_asset_is_rejected_before_native_load(self):
    with TemporaryDirectory() as directory, patch("openpilot.starpilot.ui.home.rl.load_image") as load:
      root = Path(directory)
      with self.assertRaisesRegex(ValueError, "Unreviewed Home asset"):
        validate_home_asset(root, "../../outside.png")
      path = root / "icons_mici/settings.png"
      path.parent.mkdir()
      path.write_bytes(b"not an image")
      view = object.__new__(HomeView)
      view.assets, view._textures = root, {}
      with self.assertRaisesRegex(ValueError, "reviewed manifest"):
        view._texture("icons_mici/settings.png", 48, 48)
      load.assert_not_called()

  def test_failed_gpu_upload_unloads_image_without_caching_texture(self):
    view = object.__new__(HomeView)
    view.assets, view._textures, view._pixel_scale = Path("unused"), {}, (1.0, 1.0)
    image = SimpleNamespace(data=np.zeros(4, dtype=np.uint8), width=1, height=1)
    with patch("openpilot.starpilot.ui.home.validate_home_asset", return_value=Path("unused")), \
         patch("openpilot.starpilot.ui.home.rl.load_image", return_value=image), \
         patch("openpilot.starpilot.ui.home.rl.load_texture_from_image", return_value=SimpleNamespace(id=0)), \
         patch("openpilot.starpilot.ui.home.rl.unload_image") as unload:
      with self.assertRaisesRegex(RuntimeError, "Unable to load Home texture"):
        view._texture("unused", 1, 1)
      unload.assert_called_once_with(image)
      self.assertEqual(view._textures, {})


if __name__ == "__main__":
  unittest.main()
