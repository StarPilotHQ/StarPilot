import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.ci import run_device_preflight as preflight


class DevicePreflightTests(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.fonts = self.root / "fonts"
    self.fonts.mkdir()
    (self.root / "openpilot/starpilot/ui").mkdir(parents=True)
    (self.root / "openpilot/selfdrive/assets/icons").mkdir(parents=True)
    (self.root / "launch_env.sh").write_text('  export AGNOS_VERSION="19.8.1"\nexport AGNOS_UPDATE_POLICY="retain"\n')
    (self.root / "pyproject.toml").write_text('[project]\nrequires-python = ">= 3.12.3, < 3.13"\n')
    self._manifest("bitmap-fonts.json", self.fonts, "Inter.fnt", b"font")
    self._manifest("sora-brand-font.json", self.root / preflight.UI / "assets/fonts", "Sora-800.fnt", b"brand")
    for name in preflight.MANIFESTS[2:]:
      self._manifest(name, self.root / "openpilot/selfdrive/assets", "icons/image.png", b"image")

  def _manifest(self, manifest: str, root: Path, filename: str, content: bytes):
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    (self.root / preflight.UI / manifest).write_text(json.dumps({"files": [{"file": filename, "bytes": len(content),
                                                                                  "sha256": hashlib.sha256(content).hexdigest()}]}))

  def _collect(self, **kwargs):
    with patch.object(preflight.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="abc123\n")):
      return preflight.collect(self.root, self.fonts, python_version=(3, 12, 3), system="Darwin", machine="arm64",
                               probe=lambda *_: preflight._check("pass", "fixture import"), **kwargs)

  def test_complete_assets_are_checked_without_claiming_desktop_device_readiness(self):
    report = self._collect()
    self.assertEqual(report["checks"]["source_policy"]["status"], "pass")
    self.assertEqual(report["checks"]["bitmap-fonts.json"]["status"], "pass")
    self.assertEqual(report["checks"]["sora-brand-font.json"]["status"], "pass")
    self.assertEqual(report["checks"]["target_agnos"]["status"], "unavailable")
    self.assertEqual(report["prerequisites"], "incomplete")
    self.assertEqual(report["device_qualification"], "not_assessed")
    self.assertEqual(report["source_files_sha256"]["launch_env.sh"],
                     hashlib.sha256((self.root / "launch_env.sh").read_bytes()).hexdigest())

  def test_bundled_fonts_are_the_default_and_brand_corruption_fails(self):
    self._manifest("bitmap-fonts.json", self.root / preflight.UI / "assets/fonts", "Inter.fnt", b"font")
    self.assertEqual(preflight._assets(self.root, None)["bitmap-fonts.json"]["status"], "pass")
    brand = self.root / preflight.UI / "assets/fonts/Sora-800.fnt"
    brand.write_bytes(b"changed")
    self.assertEqual(preflight._assets(self.root, None)["sora-brand-font.json"]["status"], "fail")

  def test_missing_mismatched_and_symlinked_assets_fail(self):
    (self.fonts / "Inter.fnt").write_bytes(b"wrong")
    self.assertEqual(self._collect()["checks"]["bitmap-fonts.json"]["status"], "fail")
    (self.fonts / "Inter.fnt").unlink()
    (self.fonts / "Inter.fnt").symlink_to(self.root / "openpilot/selfdrive/assets/icons/image.png")
    self.assertEqual(self._collect()["checks"]["bitmap-fonts.json"]["status"], "fail")
    (self.root / "openpilot/selfdrive/assets/icons/image.png").unlink()
    self.assertEqual(self._collect()["checks"]["home-assets.json"]["status"], "fail")

  def test_wrong_policy_python_and_os_marker(self):
    with patch.object(preflight.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="abc123\n")):
      version_file = self.root / "VERSION"
      version_file.write_text("19.8\n")
      report = preflight.collect(self.root, self.fonts, system="Linux", machine="aarch64",
                                 os_version_file=version_file, python_version=(3, 11, 9),
                                 probe=lambda *_: preflight._check("pass", "fixture import"))
    self.assertEqual(report["checks"]["source_policy"]["status"], "pass")
    self.assertEqual(report["checks"]["python_version"]["status"], "fail")
    self.assertEqual(report["checks"]["target_agnos"]["status"], "fail")
    (self.root / "launch_env.sh").write_text('export AGNOS_VERSION="19.8"\nexport AGNOS_UPDATE_POLICY="auto"\n')
    self.assertEqual(self._collect()["checks"]["source_policy"]["status"], "fail")

  def test_probe_reports_failure_and_timeout_without_child_output(self):
    with patch.object(preflight.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stderr=b"private traceback")):
      result = preflight._probe_import(self.root, sys.executable, "raylib_nb")
    self.assertEqual(result, {"status": "fail", "detail": "isolated import failed"})
    with patch.object(preflight.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 8)):
      result = preflight._probe_import(self.root, sys.executable, "raylib_nb")
    self.assertEqual(result["detail"], "isolated import timed out")

  def test_editable_package_from_another_checkout_is_rejected(self):
    foreign = self.root / "second-checkout/tinygrad"
    foreign.mkdir(parents=True)
    (foreign / "__init__.py").write_text("value = 'wrong checkout'\n")
    own_parent = self.root / "tinygrad_repo"
    own_parent.mkdir()
    (own_parent / "tinygrad").symlink_to(foreign, target_is_directory=True)
    result = preflight._probe_import(self.root, sys.executable, "tinygrad")
    self.assertEqual(result, {"status": "fail", "detail": "isolated import failed: OriginMismatch"})
    (own_parent / "tinygrad").unlink()
    own = self.root / "tinygrad_repo/tinygrad"
    own.mkdir()
    (own / "__init__.py").write_text("value = 'staged checkout'\n")
    self.assertEqual(preflight._probe_import(self.root, sys.executable, "tinygrad")["status"], "pass")


if __name__ == "__main__":
  unittest.main()
