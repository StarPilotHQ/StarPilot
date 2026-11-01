"""Which wireless handshake to try for a car, in what order, and which one last worked.

The ladder is the one place that decides this. It owns the flip between the standard and phone-style version reply that
the dongle work added, and it is also the "learned profile": the rung that last worked is simply the strategy used first
the next time.

One rule decides everything: **a failure advances the ladder only when the car answered the version exchange and no video
was acknowledged yet in this session.** A sleeping car, this comma's own Bluetooth trouble, a car that shuts off after the
drive has streamed, anything that is not the handshake being refused: none of these say anything about which strategy the
car prefers, so none of them move it. Without that rule every ignition with an asleep car would walk the whole ladder and
a car that works fine would end up running the dongle experiments.

The first pass for a car is exactly what ran before (rung ``standard``); the later rungs are knobs that already existed.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from openpilot.starpilot.system.starport.bootstrap import CHANNEL_24GHZ, CHANNEL_DUAL, MODERN_MAJOR, REFUSAL_PATIENCE
from openpilot.starpilot.system.starport.identity import DATA_DIR

STATE_PATH = DATA_DIR / "setup_state.json"
STATE_VERSION = 1
MAX_CARS = 16
MAX_PASSES = 3          # full runs through the ladder per boot (one boot is one ignition cycle); not yet confirmed against real cars
DROP_AFTER = 2          # failures at the remembered strategy before it is forgotten and the ladder starts over
CLEAN_SECONDS = 60.0    # a session that streamed this long without ending in a handshake failure counts as working
LONG_PATIENCE = 2 * REFUSAL_PATIENCE

RUNGS = ("standard", "other_reply", "modern_dual", "modern_24ghz", "patient", "experiments")
LABELS = {
  "standard": "Standard handshake",
  "other_reply": "The other version reply",
  "modern_dual": "Phone-style reply, both Wi-Fi bands",
  "modern_24ghz": "Phone-style reply, 2.4 GHz",
  "patient": "Waiting longer for the car",
  "experiments": "Adapter handshake variations",
}
MODERN_RUNGS = ("other_reply", "modern_dual", "modern_24ghz")  # only mean something to a car that speaks protocol 4 or newer


@dataclass(frozen=True)
class Strategy:
  rung: str
  modern_reply: bool
  channel_type: int | None = None   # forces the Wi-Fi band reply inside the phone-style reply; None keeps the car's own preference
  patience: int = REFUSAL_PATIENCE
  experiments: bool = False

  def as_dict(self) -> dict:
    return {"rung": self.rung, "modern_reply": self.modern_reply, "channel_type": self.channel_type,
            "patience": self.patience, "experiments": self.experiments}

  @property
  def label(self) -> str:
    return LABELS.get(self.rung, self.rung)


def resolve(rung: str, base_modern: bool) -> Strategy:
  """The concrete strategy for a rung, relative to the car's remembered reply style (today's behavior)."""
  if rung == "other_reply":
    return Strategy(rung, not base_modern)
  if rung == "modern_dual":
    return Strategy(rung, True, CHANNEL_DUAL)
  if rung == "modern_24ghz":
    return Strategy(rung, True, CHANNEL_24GHZ)
  if rung == "patient":
    return Strategy(rung, base_modern, patience=LONG_PATIENCE)
  if rung == "experiments":
    return Strategy(rung, base_modern, experiments=True)
  return Strategy("standard", base_modern)


def applicable(rung: str, version_major: int, adapter: bool) -> bool:
  if rung in MODERN_RUNGS:
    return version_major >= MODERN_MAJOR
  if rung == "experiments":
    return adapter
  return True


def _boot_id() -> str:
  try:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
  except OSError:
    return ""


def _valid_strategy(value) -> Strategy | None:
  if not isinstance(value, dict) or value.get("rung") not in RUNGS:
    return None
  channel, patience = value.get("channel_type"), value.get("patience")
  if type(value.get("modern_reply")) is not bool or type(value.get("experiments")) is not bool:
    return None
  if channel not in (None, CHANNEL_24GHZ, CHANNEL_DUAL, 1) or type(patience) is not int or not 1 <= patience <= 12:
    return None
  return Strategy(value["rung"], value["modern_reply"], channel, patience, value["experiments"])


def _fresh() -> dict:
  return {"rung": "standard", "results": {}, "passes": 0, "boot": "", "working": None, "working_failures": 0,
          "t_ready": [], "successes": 0, "updated": 0.0}


def _clean(entry) -> dict:
  """A saved entry, or a fresh one when anything about it is not what this module writes."""
  fresh = _fresh()
  if not isinstance(entry, dict):
    return fresh
  try:
    rung = entry.get("rung")
    results = entry.get("results")
    if rung in RUNGS:
      fresh["rung"] = rung
    if isinstance(results, dict):
      fresh["results"] = {key: value for key, value in results.items() if key in RUNGS and value in ("failed", "skipped", "worked")}
    for key in ("passes", "working_failures", "successes"):
      if type(entry.get(key)) is int and 0 <= entry[key] <= 10_000:
        fresh[key] = entry[key]
    fresh["boot"] = entry["boot"][:64] if isinstance(entry.get("boot"), str) else ""
    working = _valid_strategy(entry.get("working"))
    fresh["working"] = working.as_dict() if working else None
    fresh["t_ready"] = [round(float(value), 1) for value in entry.get("t_ready", []) if isinstance(value, (int, float)) and 0 <= value <= 3600][-8:]
    fresh["updated"] = float(entry["updated"]) if isinstance(entry.get("updated"), (int, float)) else 0.0
  except (TypeError, ValueError, KeyError):
    return _fresh()
  return fresh


class SetupLadder:
  def __init__(self, path: Path | None = None, boot_id=_boot_id, clock=time.time):
    self.path = path or STATE_PATH
    self.boot_id, self.clock = boot_id, clock
    self.cars: dict[str, dict] = {}
    self.load()

  def load(self) -> None:
    try:
      data = json.loads(self.path.read_text())
      cars = data["cars"] if isinstance(data, dict) and data.get("version") == STATE_VERSION and isinstance(data.get("cars"), dict) else {}
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
      cars = {}
    self.cars = {str(address).upper(): _clean(entry) for address, entry in list(cars.items())[:MAX_CARS]}

  def save(self) -> bool:
    ordered = dict(sorted(self.cars.items(), key=lambda item: item[1]["updated"], reverse=True)[:MAX_CARS])
    self.cars = ordered
    try:
      self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
      fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".setup-state-")
      try:
        with os.fdopen(fd, "w") as handle:
          json.dump({"version": STATE_VERSION, "cars": ordered}, handle, separators=(",", ":"))
          handle.flush()
          os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
      finally:
        if os.path.exists(temporary):
          os.unlink(temporary)
    except OSError:
      return False  # progress is a convenience; a read-only disk must never stop a connection
    return True

  def _entry(self, address: str) -> dict:
    entry = self.cars.setdefault(address.upper(), _fresh())
    boot = self.boot_id()
    if entry["boot"] != boot:       # a new ignition cycle gets its full passes again and resumes at the first untried rung
      entry["boot"], entry["passes"] = boot, 0
    return entry

  def forget(self, address: str) -> bool:
    """A car's pairing was deleted: drop what was learned about it."""
    removed = self.cars.pop(address.upper(), None) is not None
    if removed:
      self.save()
    return removed

  def exhausted(self, address: str) -> bool:
    entry = self.cars.get(address.upper())
    return bool(entry) and entry["boot"] == self.boot_id() and entry["passes"] >= MAX_PASSES and not entry["working"]

  def strategy(self, address: str, base_modern: bool) -> Strategy:
    """What to try now: the strategy that worked, else the current rung (standard once the passes are used up)."""
    entry = self._entry(address)
    working = _valid_strategy(entry["working"])
    if working:
      return working
    if entry["passes"] >= MAX_PASSES:
      return resolve("standard", base_modern)
    return resolve(entry["rung"], base_modern)

  def failed(self, address: str, strategy: Strategy, *, ladder_failure: bool, video_seen: bool, version_major: int,
             adapter: bool) -> str | None:
    """Record a failed attempt; returns what changed ("advanced", "wrapped", "exhausted", "counted", "dropped") or None.

    Only a handshake the car answered, before any video this session, counts: see the module docstring."""
    if not ladder_failure or video_seen:
      return None
    entry = self._entry(address)
    entry["updated"] = self.clock()
    working = _valid_strategy(entry["working"])
    if working and strategy.rung == working.rung:
      entry["working_failures"] += 1
      if entry["working_failures"] < DROP_AFTER:
        self.save()
        return "counted"
      entry.update(working=None, working_failures=0, rung="standard", results={})
      self.save()
      return "dropped"
    if entry["passes"] >= MAX_PASSES:
      return None
    entry["results"][strategy.rung] = "failed"
    start = RUNGS.index(strategy.rung) if strategy.rung in RUNGS else 0
    outcome = "wrapped"
    for rung in RUNGS[start + 1:]:
      if applicable(rung, version_major, adapter):
        entry["rung"], outcome = rung, "advanced"
        break
      entry["results"][rung] = "skipped"
    if outcome == "wrapped":
      entry["passes"] += 1
      entry["rung"], entry["results"] = "standard", {}
      if entry["passes"] >= MAX_PASSES:
        outcome = "exhausted"
    self.save()
    return outcome

  def worked(self, address: str, strategy: Strategy, t_ready_s: float | None = None) -> None:
    entry = self._entry(address)
    entry.update(working=strategy.as_dict(), working_failures=0, rung=strategy.rung, results={strategy.rung: "worked"},
                 passes=0, successes=entry["successes"] + 1, updated=self.clock())
    if t_ready_s is not None and 0 <= t_ready_s <= 3600:
      entry["t_ready"] = (entry["t_ready"] + [round(float(t_ready_s), 1)])[-8:]
    self.save()

  def ready_patience(self, address: str, stage_timeout: float = 20.0) -> int:
    """Stage timeouts a slow car needs: it was ready after t_ready_s once, so the bootstrap must be willing to wait that long.

    Never less than the standard patience, and 0 (no opinion) for a car nothing is known about."""
    entry = self.cars.get(address.upper())
    times = entry["t_ready"] if entry else []
    return min(9, int(-(-max(times) // stage_timeout))) if times else 0

  def snapshot(self, address: str, adapter: bool = False) -> dict:
    """What the setup screen and the reports show: every rung, its state, and whether this car has a learned strategy."""
    entry = self.cars.get(address.upper()) if address else None
    entry = entry or _fresh()
    working = _valid_strategy(entry["working"])
    current = working.rung if working else entry["rung"]
    passes = entry["passes"] if entry["boot"] == self.boot_id() else 0   # passes belong to one ignition cycle
    steps = []
    for rung in RUNGS:
      if rung == "experiments" and not adapter:
        continue
      state = entry["results"].get(rung) or ("current" if rung == current else "waiting")
      steps.append({"id": rung, "label": LABELS[rung], "state": state})
    return {"steps": steps, "current": current, "learned": bool(working), "passes": passes, "max_passes": MAX_PASSES,
            "exhausted": passes >= MAX_PASSES and not working, "successes": entry["successes"],
            "t_ready_s": entry["t_ready"][-1] if entry["t_ready"] else None}
