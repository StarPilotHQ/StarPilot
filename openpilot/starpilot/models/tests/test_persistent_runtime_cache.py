"""Persistent startup cache selection; not GPU or road qualification."""
import copy
import stat
from types import SimpleNamespace
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import pytest

from openpilot.starpilot.models.runtime_bridge import runtime


@pytest.mark.parametrize("role", ["driving", "monitoring"])
def test_inference_cache_survives_reboot_without_changing_role_isolation(role):
  selected = {"role": role}
  with (patch("openpilot.common.hardware.AGNOS", True),
        patch.object(runtime, "_selection", None),
        patch.object(runtime, "_verify_policy") as verify,
        patch.object(runtime, "_inference_cache_root", return_value=Path("/data/starpilot-model-runtime")),
        patch.object(runtime, "activate", return_value=selected) as activate):
    assert runtime.prepare(role) is selected
    verify.assert_called_once()
    activate.assert_called_once_with(runtime.BUNDLE, runtime.MANIFEST_SHA256,
                                     runtime.LEGACY_REVISION, role, Path("/data/starpilot-model-runtime"))
    assert runtime.prepare(role) is selected
    assert activate.call_count == 1


def test_host_does_not_create_device_cache():
  with (patch("openpilot.common.hardware.AGNOS", False),
        patch.object(runtime, "_selection", None),
        patch.object(runtime, "activate", side_effect=AssertionError("must not touch device cache"))):
    assert runtime.prepare("driving") is None


class CachePath(PurePosixPath):
  entries = {}

  def lstat(self):
    try:
      uid, mode = self.entries[str(self)]
    except KeyError:
      raise FileNotFoundError(str(self)) from None
    if isinstance(uid, BaseException):
      raise uid
    return SimpleNamespace(st_uid=uid, st_mode=mode)


@pytest.fixture
def cache_metadata():
  root = CachePath("/data/starpilot-model-runtime")
  revision = root / runtime.LEGACY_REVISION
  CachePath.entries = {"/": (0, stat.S_IFDIR | 0o755), "/data": (1000, stat.S_IFDIR | 0o755)}
  for role in ("driving", "monitoring"):
    leaf = revision / role / runtime.MANIFEST_SHA256
    for path in (root, revision, revision / role, leaf, leaf / "xdg"):
      CachePath.entries[str(path)] = (1000, stat.S_IFDIR | 0o700)
  def route(role="monitoring", uid=1000):
    with patch.object(runtime, "Path", CachePath), patch.object(runtime.os, "geteuid", return_value=uid):
      return runtime._inference_cache_root(role)
  yield root, revision, route
  CachePath.entries = {}


@pytest.mark.parametrize("role", ["driving", "monitoring"])
def test_owned_hot_cache_keeps_canonical_path_without_writes(cache_metadata, role):
  root, _, route = cache_metadata
  before = copy.deepcopy(CachePath.entries)
  assert route(role) == root
  assert CachePath.entries == before


def test_foreign_monitoring_cache_uses_only_fixed_uid_sibling(cache_metadata):
  root, revision, route = cache_metadata
  CachePath.entries[str(revision / "monitoring")] = (0, stat.S_IFDIR | 0o700)
  before = copy.deepcopy(CachePath.entries)
  assert route() == root.with_name(root.name + "-uid1000")
  assert route("driving") == root
  assert CachePath.entries == before


def test_root_mismatch_requires_explicit_private_cache(cache_metadata):
  _, _, route = cache_metadata
  before = copy.deepcopy(CachePath.entries)
  with pytest.raises(ValueError, match="explicit private"):
    route(uid=0)
  assert CachePath.entries == before


@pytest.mark.parametrize("uid,mode", [(2000, 0o755), (0, 0o777), (1000, 0o775)])
def test_uid_fallback_rejects_untrusted_parent(cache_metadata, uid, mode):
  _, revision, route = cache_metadata
  CachePath.entries[str(revision / "monitoring")] = (0, stat.S_IFDIR | 0o700)
  CachePath.entries["/data"] = (uid, stat.S_IFDIR | mode)
  with pytest.raises(ValueError, match="Untrusted parent"):
    route()


@pytest.mark.parametrize("mode", [stat.S_IFLNK | 0o777, stat.S_IFREG | 0o600])
def test_cache_route_rejects_symlink_or_nondirectory(cache_metadata, mode):
  _, revision, route = cache_metadata
  CachePath.entries[str(revision / "monitoring")] = (1000, mode)
  with pytest.raises(ValueError, match="Invalid runtime cache directory"):
    route()


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("unknown metadata")])
def test_cache_metadata_errors_propagate_without_fallback(cache_metadata, error):
  root, _, route = cache_metadata
  CachePath.entries[str(root)] = (error, 0)
  with pytest.raises(type(error)):
    route()


@pytest.mark.parametrize("uid,mode", [(0, stat.S_IFDIR | 0o700), (1000, stat.S_IFDIR | 0o777),
                                     (1000, stat.S_IFLNK | 0o777)])
def test_existing_uid_sibling_must_be_private_and_owned(cache_metadata, uid, mode):
  root, revision, route = cache_metadata
  CachePath.entries[str(revision / "monitoring")] = (0, stat.S_IFDIR | 0o700)
  CachePath.entries[str(root.with_name(root.name + "-uid1000"))] = (uid, mode)
  with pytest.raises(ValueError, match="Invalid per-user"):
    route()
