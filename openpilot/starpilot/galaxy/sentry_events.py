"""Bounded projection of local Sentry motion metadata."""

import json
import re
from collections.abc import Callable
from typing import Protocol, cast

from openpilot.starpilot.sentry_mode.storage import BATTERY_FIELDS, EventStore, StorageUnavailable, valid_tenths


MAX_RESPONSE_BYTES = 128 * 1024
ID = re.compile(r"[0-9a-f]{32}\Z")


class SentryEventsUnavailable(Exception):
  pass


class EventSnapshotSource(Protocol):
  def snapshot(self) -> dict: ...


class EventDeletionSource(Protocol):
  def delete(self, event_id: str | None = None, *, permitted: Callable[[], bool]) -> dict: ...


class SentryEvents:
  def __init__(self, store: EventSnapshotSource | None = None):
    self.store = store if store is not None else EventStore()

  def snapshot(self) -> dict:
    try:
      source = self.store.snapshot()
      if (type(source) is not dict or type(source.get("version")) is not int or source["version"] != 1 or
          type(source.get("incomplete")) is not bool or type(source.get("capacity")) is not int or source["capacity"] != 512 or
          type(source.get("events")) is not list or len(source["events"]) > 512):
        raise ValueError("Invalid motion inventory")
      events = []
      for value in source["events"]:
        if (type(value) is not dict or type(value.get("eventId")) is not str or
            ID.fullmatch(value["eventId"]) is None or type(value.get("kind")) is not str or
            value["kind"] not in ("warning", "alarm", "selfie", "battery") or
            (value["kind"] == "battery" and not all(valid_tenths(value.get(k)) for k in BATTERY_FIELDS)) or
            type(value.get("wallTimeNs")) is not int or not 0 < value["wallTimeNs"] < 2**63):
          raise ValueError("Invalid motion event")
        events.append({"eventId": value["eventId"], "kind": value["kind"],
                       "systemTimeMs": value["wallTimeNs"] // 1_000_000,
                       **({"simulated": True} if value.get("simulated") else {}),
                       **({k: value[k] for k in BATTERY_FIELDS} if value["kind"] == "battery" else {}),
                       "images": cast(Callable[[str], list], self.store.images)(value["eventId"]) if hasattr(self.store, "images") else []})
      result = {"schemaVersion": 1, "source": "local", "scanIncomplete": source["incomplete"],
                "capacity": source["capacity"], "events": events,
                "storageBytes": source.get("storageBytes", 0), "storageIncomplete": source.get("storageIncomplete", False)}
      if len(json.dumps(result, separators=(",", ":")).encode()) > MAX_RESPONSE_BYTES:
        raise ValueError("Motion response exceeds limit")
      return result
    except (StorageUnavailable, OSError, ValueError, TypeError, OverflowError, RecursionError) as error:
      raise SentryEventsUnavailable from error

  def image(self, event_id, camera):
    try:
      return self.store.image(event_id, camera)
    except (OSError, StorageUnavailable, AttributeError) as error:
      raise SentryEventsUnavailable from error

  def delete(self, event_id=None, *, permitted=lambda: True):
    try:
      result = cast(EventDeletionSource, self.store).delete(event_id, permitted=permitted)
      return {**self.snapshot(), **result}
    except (StorageUnavailable, OSError, AttributeError) as error:
      raise SentryEventsUnavailable from error
