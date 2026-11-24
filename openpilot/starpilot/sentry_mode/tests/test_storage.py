import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.starpilot.sentry_mode import storage


class EventStoreTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    # macOS TemporaryDirectory may use /var, itself a system symlink. The
    # storage contract intentionally never follows application path symlinks.
    self.root = Path(temporary.name).resolve() / "events"
    self.store = storage.EventStore(self.root)

  def record(self, kind="warning", now=90_000_000_000):
    return self.store.record(kind, now, permitted=lambda: True)

  def test_round_trip_private_metadata_only_and_missing_is_empty(self):
    self.assertEqual(self.store.snapshot()["events"], [])
    first = self.record()
    second = self.record("alarm", 120_000_000_000)
    self.assertTrue(first.durable and second.durable)
    events = self.store.snapshot()["events"]
    self.assertEqual([event["kind"] for event in events], ["alarm", "warning"])
    self.assertEqual({event["sessionId"] for event in events}, {self.store.session_id})
    self.assertEqual(set(events[0]), storage.FIELDS)
    self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
    for path in self.root.iterdir():
      self.assertEqual(path.stat().st_mode & 0o777, 0o600)
      self.assertLessEqual(path.stat().st_size, storage.MAX_EVENT_BYTES)

  def test_onroad_before_or_during_write_does_not_publish(self):
    for decisions in ((False,), (True, False)):
      with self.assertRaises(storage.StorageUnavailable):
        self.store.record("warning", 100, permitted=iter(decisions).__next__)
      self.assertEqual(self.store.snapshot()["events"], [])
    self.assertFalse(any(path.name.endswith(".tmp") for path in self.root.iterdir()))

  def test_capacity_stops_writes_without_deleting_existing_evidence(self):
    with patch.object(storage, "MAX_EVENTS", 2):
      self.record()
      self.record("alarm")
      before = {path.name: path.read_bytes() for path in self.root.iterdir()}
      with self.assertRaisesRegex(storage.StorageUnavailable, "full"):
        self.record()
      self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})
      self.assertEqual(len(self.store.snapshot()["events"]), 2)

  def test_refuses_symlink_directory_lock_or_event_without_touching_target(self):
    target = self.root.parent / "unrelated"
    target.mkdir(mode=0o700)
    self.root.symlink_to(target)
    with self.assertRaises(storage.StorageUnavailable):
      self.record()
    self.root.unlink()
    self.root.mkdir(mode=0o700)
    outside = target / "content"
    outside.write_bytes(b"keep")
    (self.root / ".lock").symlink_to(outside)
    with self.assertRaises(storage.StorageUnavailable):
      self.record()
    (self.root / ".lock").unlink()
    (self.root / ("a" * 32 + ".json")).symlink_to(outside)
    snapshot = self.store.snapshot()
    self.assertTrue(snapshot["incomplete"])
    self.assertEqual(snapshot["events"], [])
    self.assertEqual(outside.read_bytes(), b"keep")

  def test_refuses_symlinked_parent_before_mkdir_or_publication(self):
    outside = self.root.parent / "outside"
    outside.mkdir(mode=0o700)
    redirected = outside / "sentry"
    redirected.mkdir(mode=0o700)
    parent = self.root.parent / "alias"
    parent.symlink_to(outside, target_is_directory=True)
    store = storage.EventStore(parent / "sentry" / "events")
    with self.assertRaises(storage.StorageUnavailable):
      store.record("warning", 100, permitted=lambda: True)
    with self.assertRaises(storage.StorageUnavailable):
      store.snapshot()
    self.assertFalse((redirected / "events").exists())
    self.assertEqual(list(redirected.iterdir()), [])

  def test_first_use_creates_only_private_directories_without_dotdot(self):
    root = self.root.parent / "new" / "sentry" / "events"
    store = storage.EventStore(root)
    self.assertTrue(store.record("warning", 100, permitted=lambda: True).durable)
    for parent in (root, root.parent, root.parent.parent):
      self.assertEqual(parent.stat().st_mode & 0o777, 0o700)
    escaped = storage.EventStore(self.root.parent / "new" / ".." / "outside" / "event")
    with self.assertRaises(storage.StorageUnavailable):
      escaped.record("warning", 100, permitted=lambda: True)

  def test_invalid_duplicate_oversized_unknown_files_are_bounded_and_skipped(self):
    first = self.record()
    valid = self.root / (first.event_id + ".json")
    value = json.loads(valid.read_bytes())
    for index, raw in enumerate((b"{" + valid.read_bytes()[1:-1] + b',"version":1}',
                                 b"x" * (storage.MAX_EVENT_BYTES + 1),
                                 json.dumps({**value, "imagePaths": ["/private"]}).encode())):
      path = self.root / (str(index) * 32 + ".json")
      path.write_bytes(raw)
      path.chmod(0o600)
    (self.root / "unrecognized").write_bytes(b"keep")
    result = self.store.snapshot()
    self.assertTrue(result["incomplete"])
    self.assertEqual(len(result["events"]), 1)
    with patch.object(storage, "MAX_EVENTS", 1):
      self.assertTrue(self.store.snapshot()["incomplete"])

  def test_fsync_error_after_publication_reports_uncertain_durability_without_retry(self):
    original = os.fsync
    def fail_directory(fd):
      if storage.stat.S_ISDIR(os.fstat(fd).st_mode):
        raise OSError("disk sync failed")
      original(fd)
    with patch.object(storage.os, "fsync", side_effect=fail_directory):
      receipt = self.record()
    self.assertFalse(receipt.durable)
    self.assertEqual([event["eventId"] for event in self.store.snapshot()["events"]], [receipt.event_id])

  def test_fsync_error_before_publication_leaves_no_record(self):
    with patch.object(storage.os, "fsync", side_effect=OSError("disk sync failed")):
      with self.assertRaises(storage.StorageUnavailable):
        self.record()
    self.assertEqual(self.store.snapshot()["events"], [])
    self.assertEqual([path.name for path in self.root.iterdir()], [".lock"])

  def test_event_and_timestamps_are_strict(self):
    for kind, stamp in (("image", 1), ("warning", True), ("warning", -1), ("alarm", 2**63)):
      with self.assertRaises(ValueError):
        self.record(kind, stamp)
    self.assertFalse(self.root.exists())


if __name__ == "__main__":
  unittest.main()


def test_simulated_events_expire_without_removing_real_events(tmp_path, monkeypatch):
  import time
  from openpilot.starpilot.sentry_mode.storage import EventStore
  store = EventStore(tmp_path / 'events')
  clock = [1000000000000]
  monkeypatch.setattr(time, 'time_ns', lambda: clock[0])
  fake = store.record('warning', 1, permitted=lambda: True, simulated=True, images={'wide': b'\xff\xd8\xff\xd9'})
  real = store.record('warning', 2, permitted=lambda: True)
  assert len(store.snapshot()['events']) == 2
  clock[0] += 601000000000
  assert [event['eventId'] for event in store.snapshot()['events']] == [real.event_id]
  assert not (store.root / (fake.event_id + '.json')).exists()
  assert not (store.root / fake.event_id).exists()
