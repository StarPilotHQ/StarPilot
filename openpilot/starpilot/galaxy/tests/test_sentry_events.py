"""Local motion records are projected without exposing storage or session details."""

from pathlib import Path
import tempfile
import unittest

from openpilot.starpilot.galaxy.sentry_events import SentryEvents, SentryEventsUnavailable
from openpilot.starpilot.sentry_mode.storage import EventStore


class SentryEventsTest(unittest.TestCase):
  def test_actual_store_projection_and_empty_store(self):
    with tempfile.TemporaryDirectory() as directory:
      store = EventStore(Path(directory).resolve() / "events")
      self.assertEqual(SentryEvents(store).snapshot()["events"], [])
      receipt = store.record("warning", 123, permitted=lambda: True)
      result = SentryEvents(store).snapshot()
      self.assertEqual(result["schemaVersion"], 1)
      self.assertEqual(result["source"], "local")
      self.assertEqual(result["capacity"], 512)
      self.assertEqual(result["events"][0]["eventId"], receipt.event_id)
      self.assertEqual(set(result["events"][0]), {"eventId", "kind", "systemTimeMs", "images"})
      self.assertEqual(result["events"][0]["kind"], "warning")
      self.assertEqual(result["events"][0]["images"], [])

  def test_battery_event_projects_voltage_and_rejects_missing_reading(self):
    with tempfile.TemporaryDirectory() as directory:
      store = EventStore(Path(directory).resolve() / "events")
      store.record("battery", 1, permitted=lambda: True, details={"voltage": 120, "cutoff": 118})
      event = SentryEvents(store).snapshot()["events"][0]
      self.assertEqual((event["kind"], event["voltage"], event["cutoff"]), ("battery", 120, 118))
      with self.assertRaises(ValueError):
        store.record("battery", 2, permitted=lambda: True)
      with self.assertRaises(ValueError):
        store.record("warning", 3, permitted=lambda: True, details={"voltage": 120, "cutoff": 118})

  def test_published_images_project_after_restart_and_use_same_event_id(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory).resolve() / "events"
      image = b"\xff\xd8fixture\xff\xd9"
      receipt = EventStore(root).record("alarm", 456, permitted=lambda: True, images={"cabin": image})
      projection = SentryEvents(EventStore(root))
      event = projection.snapshot()["events"][0]
      self.assertEqual(event["eventId"], receipt.event_id)
      self.assertEqual(event["images"], ["cabin"])
      self.assertEqual(projection.image(event["eventId"], "cabin"), image)
      with self.assertRaises(SentryEventsUnavailable):
        projection.image(event["eventId"], "wide")

  def test_invalid_or_oversized_snapshot_is_unavailable(self):
    class Fixture:
      def __init__(self, value):
        self.value = value

      def snapshot(self):
        return self.value

    base = {"version": 1, "incomplete": False, "capacity": 512, "events": []}
    event = {"eventId": "a" * 32, "kind": "alarm", "wallTimeNs": 123_000_000}
    for value in ({**base, "version": True}, {**base, "capacity": True},
                  {**base, "events": [event] * 513},
                  {**base, "events": [{**event, "kind": "unknown"}]},
                  {**base, "events": [{**event, "wallTimeNs": -1}]}):
      with self.subTest(value=value):
        with self.assertRaises(SentryEventsUnavailable):
          SentryEvents(Fixture(value)).snapshot()


if __name__ == "__main__":
  unittest.main()
