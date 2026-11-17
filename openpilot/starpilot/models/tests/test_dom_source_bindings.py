from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from openpilot.starpilot.models.runtime_bridge import dom_loader


def test_reviewed_dom_sources_match_the_shipped_loader():
  dom_loader.verify_dom_sources()


def test_changed_or_symlinked_reviewed_source_still_fails_closed():
  original = Path(dom_loader.__file__).parent
  with TemporaryDirectory() as directory:
    root = Path(directory)
    for name in dom_loader._SOURCES:
      (root / name).write_bytes((original / name).read_bytes())
    with patch.object(dom_loader, "__file__", str(root / "dom_loader.py")):
      dom_loader.verify_dom_sources()
      target = root / "dom_admission.py"
      target.write_bytes(target.read_bytes() + b"\n")
      with pytest.raises(ValueError, match="Reviewed Dom source changed"):
        dom_loader.verify_dom_sources()
      target.unlink()
      target.symlink_to(original / target.name)
      with pytest.raises(ValueError, match="Missing reviewed Dom source"):
        dom_loader.verify_dom_sources()
