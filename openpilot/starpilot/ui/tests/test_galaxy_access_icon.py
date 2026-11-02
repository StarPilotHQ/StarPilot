"""The compact Galaxy action uses a reviewed image that Raylib can decode."""

import hashlib
import json
from pathlib import Path
import unittest

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.galaxy_access import GALAXY_ICON


class TestGalaxyAccessIcon(unittest.TestCase):
  def test_compact_icon_is_tracked_reviewed_and_decodable(self):
    root = Path(__file__).resolve().parents[3] / 'selfdrive/assets'
    manifest = json.loads((Path(__file__).resolve().parents[1] / 'settings-assets.json').read_text())
    entry = next(item for item in manifest['files'] if item['file'] == GALAXY_ICON)
    raw = (root / GALAXY_ICON).read_bytes()
    self.assertEqual(len(raw), entry['bytes'])
    self.assertEqual(hashlib.sha256(raw).hexdigest(), entry['sha256'])
    image = rl.load_image(str(root / GALAXY_ICON))
    try:
      self.assertGreater(image.width, 0)
      self.assertGreater(image.height, 0)
    finally:
      rl.unload_image(image)


if __name__ == '__main__':
  unittest.main()
