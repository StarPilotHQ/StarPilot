import hashlib

import pytest

from openpilot.common.file_chunker import package_file
from openpilot.starpilot.models import manager
from openpilot.starpilot.models.catalog import BUNDLED_CURRENT, DEFAULT_SMALL, DEFAULT_SMALL_SHA256


def test_unset_small_loads_shipped_cheddar_with_v16_parser(tmp_path, monkeypatch):
  artifact = tmp_path / "shipped.pkl"
  artifact.write_bytes(b"verified fixture")
  monkeypatch.setattr(manager, "shipped_default", lambda: artifact)
  selected = manager.resolve_runtime(False, root=tmp_path)
  assert manager.preferences(tmp_path)["small"] == DEFAULT_SMALL
  assert selected.small_id == "cheddar"
  assert selected.small_version == "v16"
  assert selected.small_path == artifact
  assert selected.small_sha256 == DEFAULT_SMALL_SHA256
  assert not selected.allow_big


@pytest.mark.parametrize("selection", [BUNDLED_CURRENT, "gwm8223"])
def test_legacy_alias_and_missing_selected_artifact_use_cheddar_only(tmp_path, monkeypatch, selection):
  manager.atomic_json(tmp_path / "preferences.json", {"small": selection})
  path = tmp_path / "cheddar.pkl"
  monkeypatch.setattr(manager, "shipped_default", lambda: path)
  selected = manager.resolve_runtime(False, root=tmp_path)
  assert selected.small_id == DEFAULT_SMALL and selected.small_path == path
  assert manager.preferences(tmp_path)["small"] == (DEFAULT_SMALL if selection == BUNDLED_CURRENT else selection)


def test_missing_or_corrupt_only_shipped_model_refuses_startup(tmp_path, monkeypatch):
  monkeypatch.setattr(manager, "shipped_default", lambda: None)
  with pytest.raises(manager.ModelError, match="Shipped Cheddar is missing or corrupt"):
    manager.resolve_runtime(False, root=tmp_path)


def test_big_default_disabled_and_explicit_download_preserved(tmp_path):
  assert manager.preferences(tmp_path)["big"] == ""
  manager.atomic_json(tmp_path / "preferences.json", {"big": "cinquev3"})
  assert manager.preferences(tmp_path)["big"] == "cinquev3"
  manager.atomic_json(tmp_path / "preferences.json", {"big": BUNDLED_CURRENT})
  assert manager.preferences(tmp_path)["big"] == ""


def test_verified_download_precedes_shipped_default(tmp_path, monkeypatch):
  path = tmp_path / "download.pkl"
  monkeypatch.setattr(manager, "catalog", lambda _: {DEFAULT_SMALL: {"artifact_sha256": "download-hash", "version": "v16"}})
  monkeypatch.setattr(manager, "verified_artifact", lambda *args: path)
  def unexpected_shipped_default():
    raise AssertionError("download replaced")
  monkeypatch.setattr(manager, "shipped_default", unexpected_shipped_default)
  selected = manager.resolve_runtime(False, root=tmp_path)
  assert selected.small_path == path
  assert selected.small_sha256 == "download-hash"


@pytest.mark.parametrize("damage", [None, "bytes", "size", "missing"])
def test_shipped_parts_require_pinned_hash_and_size(tmp_path, monkeypatch, damage):
  data = b"small isolated compiled artifact fixture"
  source = tmp_path / "source.pkl"
  source.write_bytes(data)
  destination = tmp_path / "cheddar_driving_tinygrad.pkl"
  package_file(source, destination)
  monkeypatch.setattr(manager, "SHIPPED_MODELS", tmp_path)
  monkeypatch.setattr(manager, "DEFAULT_SMALL_SHA256", hashlib.sha256(data).hexdigest())
  monkeypatch.setattr(manager, "DEFAULT_SMALL_SIZE", len(data) + (damage == "size"))
  part = tmp_path / "cheddar_driving_tinygrad.pkl.chunk01of01"
  if damage == "bytes":
    part.write_bytes(b"x" * len(data))
  elif damage == "missing":
    part.unlink()
  result = manager.shipped_default()
  assert (result is not None) == (damage is None)
  if result is not None:
    assert result.read_bytes() == data


def test_shipped_default_is_verified_asynchronously_in_menu(tmp_path, monkeypatch):
  data = b"isolated shipped fixture"
  source = tmp_path / "source.pkl"
  source.write_bytes(data)
  shipped = tmp_path / "shipped"
  package_file(source, shipped / "cheddar_driving_tinygrad.pkl")
  monkeypatch.setattr(manager, "SHIPPED_MODELS", shipped)
  monkeypatch.setattr(manager, "DEFAULT_SMALL_SHA256", hashlib.sha256(data).hexdigest())
  monkeypatch.setattr(manager, "DEFAULT_SMALL_SIZE", len(data))
  owner = manager.ModelManager(root=tmp_path / "preferences", parked=lambda: True, gpu_present=lambda: False)
  try:
    owner.snapshot()
    if owner.verify_worker is not None:
      owner.verify_worker.join(2)
    row = next(row for row in owner.snapshot()["models"] if row["value"] == DEFAULT_SMALL)
    assert row["installed"] and row["selectable"] and not row["checking"]
    owner.action("active", {"profile": "small", "model": DEFAULT_SMALL})
    selected = manager.resolve_runtime(False, root=owner.root)
    assert selected.small_id == DEFAULT_SMALL and selected.small_path.read_bytes() == data
  finally:
    owner.close()
