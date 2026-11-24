import os

import pytest

from openpilot.starpilot.software import save_me


def test_legacy_and_current_paths_are_removed_but_wifi_and_software_survive(tmp_path):
  legacy = (
    "data/params", "cache/starpilot/params", "cache/params", "data/media/0/realdata",
    "data/media/0/realdata_HD", "data/media/0/realdata_konik", "data/models", "data/toggle_backups",
    "data/backups", "data/themes", "data/media/0/osm/offline", "cache/use_HD", "cache/use_konik",
  )
  removed = (*legacy, "data/starpilot", "data/media/0/starpilot", "data/galaxy", "data/starport")
  kept = ("data/etc/NetworkManager/system-connections/home.nmconnection", "data/etc/netplan/config",
          "data/openpilot/.git/HEAD", "data/safe_staging/finalized/program", "data/media/0/other", "VERSION")
  for path in removed:
    entry = tmp_path / path
    entry.mkdir(parents=True)
    (entry / "data").touch()
  for path in kept:
    entry = tmp_path / path
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("keep")
  marker = tmp_path / "data/safe_staging/finalized/.overlay_consistent"
  marker.touch()

  assert save_me.wipe(tmp_path) == []
  assert not marker.exists()
  assert all(not (tmp_path / path).exists() for path in removed)
  assert all((tmp_path / path).read_text() == "keep" for path in kept)
  assert save_me.wipe(tmp_path) == []


def test_symlinks_cannot_redirect_deletion(tmp_path):
  protected = tmp_path / "data/etc"
  protected.mkdir(parents=True)
  (protected / "wifi").touch()
  (tmp_path / "data/models").symlink_to(protected, target_is_directory=True)
  (tmp_path / "data/media").symlink_to(protected, target_is_directory=True)

  assert save_me.wipe(tmp_path)
  assert not (tmp_path / "data/models").is_symlink()
  assert (protected / "wifi").is_file()


def test_requests_expire_and_are_not_replayed_or_run_while_driving(tmp_path, monkeypatch):
  marker = tmp_path / "request"
  monkeypatch.setattr(save_me, "REQUEST", marker)
  save_me.request()
  assert not save_me.take_request(parked=False, started=False)
  assert save_me.take_request(parked=True, started=False)
  assert not save_me.take_request(parked=True, started=False)
  save_me.request()
  assert not save_me.take_request(parked=False, started=True)
  assert not marker.exists()
  save_me.request()
  old = marker.stat().st_mtime - 60
  os.utime(marker, (old, old))
  assert not save_me.take_request(parked=True, started=False)
  assert not marker.exists()


def test_cannot_wipe_if_staged_update_cannot_be_disarmed(tmp_path):
  params = tmp_path / "data/params"
  params.mkdir(parents=True)
  (tmp_path / "data/safe_staging/finalized/.overlay_consistent").mkdir(parents=True)
  with pytest.raises(OSError):
    save_me.wipe(tmp_path)
  assert params.is_dir()
