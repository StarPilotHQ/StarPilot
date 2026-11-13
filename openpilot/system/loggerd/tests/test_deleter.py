import os
from collections import namedtuple
from pathlib import Path
from collections.abc import Sequence

import openpilot.system.loggerd.deleter as deleter
from openpilot.system.loggerd.tests.loggerd_tests_common import UploaderTestCase

Stats = namedtuple("Stats", ['f_bavail', 'f_blocks', 'f_frsize'])


class TestDeleter(UploaderTestCase):
  # Deletion behavior is independent of file size; use smaller files to keep these tests fast.
  def make_file_with_data(self, f_dir: str, fn: str, size_mb: float = .001, lock: bool = False,
                          upload_xattr: bytes | None = None, preserve_xattr: bytes | None = None) -> Path:
    return super().make_file_with_data(f_dir, fn, size_mb, lock, upload_xattr, preserve_xattr)

  def fake_statvfs(self, d):
    return self.fake_stats

  def setup_method(self):
    self.f_type = "fcamera.hevc"
    super().openpilot_setup_method()
    self.fake_stats = Stats(f_bavail=0, f_blocks=10, f_frsize=4096)
    deleter.os.statvfs = self.fake_statvfs  # ty: ignore[invalid-assignment]  # test double

  def test_delete(self):
    f_path = self.make_file_with_data(self.seg_dir, self.f_type)
    assert deleter.deleter_step() == (True, str(f_path.parent))
    assert not f_path.exists()

  def assertDeleteOrder(self, f_paths: Sequence[Path]) -> None:
    deleted_order = []
    for _ in f_paths:
      out_of_space, deleted_path = deleter.deleter_step()
      assert out_of_space and deleted_path is not None
      deleted_order.append(next(f for f in f_paths if f.parent == Path(deleted_path)))

    assert deleted_order == f_paths, "Files not deleted in expected order"

  def test_delete_order(self):
    self.assertDeleteOrder([
      self.make_file_with_data(self.seg_format.format(0), self.f_type),
      self.make_file_with_data(self.seg_format.format(1), self.f_type),
      self.make_file_with_data(self.seg_format2.format(0), self.f_type),
    ])

  def test_delete_many_preserved(self):
    self.assertDeleteOrder([
      self.make_file_with_data(self.seg_format.format(0), self.f_type),
      self.make_file_with_data(self.seg_format.format(1), self.f_type, preserve_xattr=deleter.PRESERVE_ATTR_VALUE),
      self.make_file_with_data(self.seg_format.format(2), self.f_type),
    ] + [
      self.make_file_with_data(self.seg_format2.format(i), self.f_type, preserve_xattr=deleter.PRESERVE_ATTR_VALUE)
      for i in range(5)
    ])

  def test_delete_last(self):
    preserved = self.make_file_with_data(self.seg_format.format(0), self.f_type, preserve_xattr=deleter.PRESERVE_ATTR_VALUE)
    self.assertDeleteOrder([
      self.make_file_with_data(self.seg_format.format(1), self.f_type),
      self.make_file_with_data(self.seg_format2.format(0), self.f_type),
      preserved,
      self.make_file_with_data("boot", self.seg_format[:-4]),
      self.make_file_with_data("crash", self.seg_format2[:-4]),
    ])

  def test_delete_order_after_route_count_reset(self):
    old = [self.make_file_with_data(f"000000e8--dcc621904d--{i}", "qlog.zst") for i in range(2)]
    new = [self.make_file_with_data(f"00000007--29d39f1644--{i}", "qlog.zst") for i in range(2)]
    for n, f in enumerate(old + new):
      os.utime(f, (1_700_000_000 + n, 1_700_000_000 + n))
    self.assertDeleteOrder(old + new)

  def test_delete_order_ignores_video_deletion_and_unsynced_clock(self):
    old = self.make_file_with_data("000000e8--dcc621904d--0", "qlog.zst")
    unsynced = self.make_file_with_data("00000007--29d39f1644--0", "qlog.zst")
    synced = self.make_file_with_data("00000007--29d39f1644--1", "qlog.zst")
    for f, t in ((old, 1_700_000_000), (unsynced, 1_600_000_000), (synced, 1_800_000_000)):
      os.utime(f, (t, t))
    os.utime(old.parent, (1_900_000_000, 1_900_000_000))
    self.assertDeleteOrder([old, unsynced, synced])

  def test_route_recorded_before_clock_sync_is_not_deleted_first(self):
    # Route 55 on the car: booted without network, every segment dated a month early, and the
    # deleter removed it before the routes it followed.
    older = self.make_file_with_data("00000053--8d3f0c2a11--0", "qlog.zst")
    previous = self.make_file_with_data("00000054--3a90b7e2c4--0", "qlog.zst")
    unsynced = [self.make_file_with_data(f"00000055--69f7e28da6--{i}", "qlog.zst") for i in range(2)]
    for f, t in ((older, 1_800_000_000), (previous, 1_800_100_000), *((f, 1_797_000_000 + i) for i, f in enumerate(unsynced))):
      os.utime(f, (t, t))
    self.assertDeleteOrder([older, previous, *unsynced])

  def test_consecutive_unsynced_routes_follow_their_predecessor(self):
    synced = self.make_file_with_data("00000010--aaaaaaaaaa--0", "qlog.zst")
    first = self.make_file_with_data("00000011--bbbbbbbbbb--0", "qlog.zst")
    second = self.make_file_with_data("00000012--cccccccccc--0", "qlog.zst")
    for f, t in ((synced, 1_800_000_000), (first, 1_600_000_000), (second, 1_600_000_100)):
      os.utime(f, (t, t))
    self.assertDeleteOrder([synced, first, second])

  def test_non_consecutive_routes_keep_their_own_times(self):
    # A gap in the count (a reset, or a deleted route) is not chained across.
    early = self.make_file_with_data("00000013--aaaaaaaaaa--0", "qlog.zst")
    late = self.make_file_with_data("00000011--bbbbbbbbbb--0", "qlog.zst")
    for f, t in ((early, 1_600_000_000), (late, 1_800_000_000)):
      os.utime(f, (t, t))
    self.assertDeleteOrder([early, late])

  def test_no_delete_when_available_space(self):
    f_path = self.make_file_with_data(self.seg_dir, self.f_type)

    block_size = 4096
    available = (10 * 1024 * 1024 * 1024) / block_size  # 10GB free
    self.fake_stats = Stats(f_bavail=available, f_blocks=10, f_frsize=block_size)

    assert deleter.deleter_step() == (False, None)
    assert f_path.exists(), "File deleted with available space"

  def test_no_delete_with_lock_file(self):
    f_path = self.make_file_with_data(self.seg_dir, self.f_type, lock=True)

    assert deleter.deleter_step() == (True, None)
    assert f_path.exists(), "File deleted when locked"
