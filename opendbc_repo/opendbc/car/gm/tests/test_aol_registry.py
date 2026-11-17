"""Every qualified GM word must reach pandad's native permission publisher."""

import re
import unittest
from pathlib import Path

from opendbc.car.gm.aol import GM_AOL_WORDS


class TestGmAolRegistry(unittest.TestCase):
  def test_pandad_registry_matches_qualified_gm_words(self):
    root = Path(__file__).resolve().parents[5]
    policy = (root / "openpilot/starpilot/car/gm/aol_policy.h").read_text()
    native_words = {int(word, 0) for word in re.findall(r"case (0x[0-9A-Fa-f]+|[0-9]+)U:", policy)}
    self.assertEqual(native_words, GM_AOL_WORDS)
