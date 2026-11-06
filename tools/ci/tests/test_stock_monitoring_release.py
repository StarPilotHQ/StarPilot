from pathlib import Path

from openpilot.common.file_chunker import materialize_file_chunked, package_file
import shutil

import pytest

from openpilot.starpilot.models.runtime_bridge import runtime
from tools.laptop_device_build import qcom_artifact_contract as contract
from tools.laptop_device_build import validate_artifacts as validator

ROOT = Path(__file__).resolve().parents[3]
MODELS = ROOT / "openpilot/selfdrive/modeld/models"
NAMES = ("dm_warp_1344x760_tinygrad.pkl", "dm_warp_1928x1208_tinygrad.pkl")


@pytest.mark.parametrize("name", NAMES)
def test_actual_shipped_warps_pass_the_startup_guard(name):
  assert runtime._stock_bytes(MODELS / name) == (MODELS / name).read_bytes()


@pytest.mark.parametrize("name", NAMES)
def test_structural_pickle_validation_is_not_sufficient(name, tmp_path):
  artifact = tmp_path / name
  artifact.write_bytes((MODELS / name).read_bytes() + b"\0")
  validator.require_qcom_pickle(artifact)
  with pytest.raises(ValueError, match="Stock model artifact changed"):
    runtime._stock_bytes(artifact)
  with pytest.raises(ValueError, match="Stock model artifact changed"):
    contract.preserve_stock_artifact(artifact)


@pytest.mark.parametrize("name", NAMES)
def test_device_build_preserves_verified_bytes_and_rejects_symlink(name, tmp_path):
  artifact = tmp_path / name
  shutil.copy2(MODELS / name, artifact)
  before = artifact.stat()
  contract.preserve_stock_artifact(artifact)
  after = artifact.stat()
  assert (before.st_ino, before.st_mtime_ns, before.st_size) == (after.st_ino, after.st_mtime_ns, after.st_size)
  artifact.unlink()
  artifact.symlink_to(MODELS / name)
  with pytest.raises(OSError):
    contract.preserve_stock_artifact(artifact)


def test_release_checks_all_three_actual_stock_artifacts():
  validator.require_stock_monitoring(MODELS)


@pytest.mark.parametrize("name", NAMES)
def test_release_refuses_structurally_valid_but_untrusted_warp(name, tmp_path):
  for other in (*NAMES, "dmonitoring_model_tinygrad.pkl"):
    shutil.copy2(materialize_file_chunked(MODELS / other), tmp_path / other)
  artifact = tmp_path / name
  artifact.write_bytes(artifact.read_bytes() + b"\0")
  with pytest.raises(ValueError, match="Stock model artifact changed"):
    validator.require_stock_monitoring(tmp_path)


def test_chunked_monitoring_model_is_preserved_as_a_regular_build_target(tmp_path):
  source = materialize_file_chunked(MODELS / "dmonitoring_model_tinygrad.pkl")
  target = tmp_path / source.name.removesuffix(".unchunked")
  package_file(source, target)
  assert not target.exists()
  contract.preserve_stock_artifact(target)
  assert runtime._stock_bytes(target) == runtime._stock_bytes(source)
