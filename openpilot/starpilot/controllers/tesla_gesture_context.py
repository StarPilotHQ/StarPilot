"""Speed sync: optional gesture identity. Carries context, never dispatches actions."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import re

HEX32 = re.compile(r"[0-9a-f]{32}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = {"version", "workerSession", "sequence", "sourceCanBootTime", "sourceCanFrameIndex",
           "name", "fast", "assignmentsHash", "vehicleHash", "driveStartMonoTime"}
_current = ContextVar("tesla_gesture", default=None)


@dataclass(frozen=True)
class GestureSource:
  worker_session: str
  sequence: int
  boot_ns: int
  frame_index: int
  name: str
  fast: bool
  document: str
  vehicle: str
  drive: int

  def to_wire(self):
    return {"version": 1, "workerSession": self.worker_session, "sequence": self.sequence,
                "sourceCanBootTime": self.boot_ns, "sourceCanFrameIndex": self.frame_index,
                "name": self.name, "fast": self.fast, "assignmentsHash": self.document,
                "vehicleHash": self.vehicle, "driveStartMonoTime": self.drive}

  @property
  def step(self):
    return (1 if self.name == "right_wheel_up" else -1) * (5 if self.fast else 1)

  def matches(self, step_units, intervals=None):
    """The comma speed step this scroll produced: Short scroll or Fast scroll (1 and 5 without saved steps)."""
    from openpilot.starpilot.longitudinal.cruise_intervals import CruiseIntervals, step_units as units
    return step_units == (1 if self.name == "right_wheel_up" else -1) * units(intervals or CruiseIntervals(), self.fast)


def parse_source(value):
  try:
    if not isinstance(value, dict) or set(value) != _FIELDS:
      return None
    if (type(value["version"]) is not int or value["version"] != 1 or
        any(type(value[k]) is not int or not 0 < value[k] < 2**64
            for k in ("sequence", "sourceCanBootTime", "driveStartMonoTime")) or
        type(value["sourceCanFrameIndex"]) is not int or not 0 <= value["sourceCanFrameIndex"] < 4096 or
        type(value["fast"]) is not bool or value["name"] not in ("right_wheel_up", "right_wheel_down") or
        type(value["workerSession"]) is not str or not HEX32.fullmatch(value["workerSession"]) or
        any(type(value[k]) is not str or not HEX64.fullmatch(value[k])
            for k in ("assignmentsHash", "vehicleHash"))):
      return None
    return GestureSource(value["workerSession"], value["sequence"], value["sourceCanBootTime"],
                         value["sourceCanFrameIndex"], value["name"], value["fast"],
                         value["assignmentsHash"], value["vehicleHash"], value["driveStartMonoTime"])
  except (KeyError, TypeError, ValueError):
    return None


def source_from_event(event):
  source = parse_source(event.get("provenance"))
  if source is None:
    return None
  if (source.sequence != event["sequence"] or source.name != event["name"] or
      source.fast != event["fast"] or source.document != event["document"] or
      source.vehicle != event["vehicle"] or source.drive != event["drive"]):
    return None
  return source


@contextmanager
def source_scope(event):
  token = _current.set(source_from_event(event))
  try:
    yield
  finally:
    _current.reset(token)


def current_source():
  return _current.get()
