"""Per-car record of how each car-specific fix worked, kept in quirk_state.json apart from the setup ladder."""

from __future__ import annotations

import json
import statistics
import threading
from dataclasses import dataclass
from pathlib import Path

from openpilot.common.utils import atomic_write
from openpilot.starpilot.system.starport import identity as identity_store
from openpilot.starpilot.system.starport.compat_report import read_log

STATE_VERSION = 1
MAX_CARS = 16
MAX_STATE_BYTES = 256 << 10
MATCH_FIELDS = ("car_make", "car_model", "car_year", "head_unit_make", "head_unit_model", "head_unit_software_version")
EARLY_SECONDS = 60.0        # a car that ends the session sooner than this after video started has hung up early
CONFIRM_SECONDS = 120.0     # streaming this long with no early shutdown is a success
CONFIRM_SESSIONS = 2        # successes needed to confirm a trial
LEVEL_PATIENCE = 2          # failed sessions at one level before the ladder moves up
REVERT_STREAK = 2           # worse-than-baseline sessions in a row that revert a trial
MIN_BASELINE = 3            # first-frame samples needed before slowness counts as a regression
SLOW_FACTOR = 2.0
SLOW_MARGIN_S = 5.0
KEEP_SAMPLES = 12

TRYING, CONFIRMED, OFF = "trying", "confirmed", "off"
SUCCESS, FAILED, INCONCLUSIVE, REGRESSION = "success", "failed", "inconclusive", "regression"


@dataclass(frozen=True)
class SessionOutcome:
  """What one finished session says about a fix. Built by the supervisor from facts it already has."""
  video_seen: bool = False          # the car acknowledged a video frame
  streamed_s: float = 0.0
  first_frame_s: float | None = None
  ended_by_car: bool = False        # the car sent a shutdown request
  user_stopped: bool = False        # the owner stopped projection, or the car or comma went away
  hangup: bool = False              # the link dropped with no video, or the car hung up on a reply
  pairing_prompt: bool = False      # the car reported the comma as not yet paired after a pairing request
  waiting_for_driver: bool = False  # focus was never granted and the car may be waiting for the driver to choose

  @property
  def early_shutdown(self) -> bool:
    return self.video_seen and self.ended_by_car and self.streamed_s < EARLY_SECONDS


def classify(outcome: SessionOutcome, frozen: dict) -> str:
  """The verdict for one session run with a fix on, judged against the baseline frozen when the trial started."""
  if outcome.user_stopped or outcome.waiting_for_driver:
    return INCONCLUSIVE
  if outcome.pairing_prompt:
    return REGRESSION
  if outcome.hangup and not outcome.video_seen and frozen.get("sessions_with_video", 0) > 0:
    return REGRESSION
  ttf = frozen.get("first_frame_s") or []
  if (outcome.first_frame_s is not None and len(ttf) >= MIN_BASELINE and
      outcome.first_frame_s > statistics.median(ttf) * SLOW_FACTOR + SLOW_MARGIN_S):
    return REGRESSION
  if outcome.early_shutdown:
    return FAILED
  if outcome.video_seen and outcome.streamed_s >= CONFIRM_SECONDS and not outcome.ended_by_car:
    return SUCCESS
  return INCONCLUSIVE


def _baseline() -> dict:
  return {"sessions": 0, "sessions_with_video": 0, "early_shutdowns": 0, "hangups": 0, "pairing_prompts": 0, "first_frame_s": []}


def _trial(level: int, baseline: dict, generation: int) -> dict:
  frozen = {key: (list(value) if isinstance(value, list) else value) for key, value in baseline.items()}
  return {"state": TRYING, "level": level, "good": 0, "bad": 0, "worse": 0, "sessions": 0, "frozen": frozen,
          "generation": generation, "reason": ""}


def _fresh(fingerprint: dict) -> dict:
  return {"fingerprint": dict(fingerprint), "baseline": _baseline(), "trials": {}, "dismissed": [], "updated": 0.0}


def _number_list(value) -> list[float]:
  return [float(v) for v in value[-KEEP_SAMPLES:] if isinstance(v, (int, float)) and not isinstance(v, bool)] if isinstance(value, list) else []


def _count(value) -> int:
  return value if type(value) is int and 0 <= value < 10_000_000 else 0


def _clean_baseline(raw) -> dict:
  base = _baseline()
  if not isinstance(raw, dict):
    return base
  for key in ("sessions", "sessions_with_video", "early_shutdowns", "hangups", "pairing_prompts"):
    base[key] = _count(raw.get(key))
  base["first_frame_s"] = _number_list(raw.get("first_frame_s"))
  return base


def _clean_trial(raw) -> dict | None:
  if not isinstance(raw, dict) or raw.get("state") not in (TRYING, CONFIRMED, OFF):
    return None
  trial = _trial(_count(raw.get("level")), _clean_baseline(raw.get("frozen")), _count(raw.get("generation")))
  trial.update({key: _count(raw.get(key)) for key in ("good", "bad", "worse", "sessions")})
  trial["state"] = raw["state"]
  trial["reason"] = str(raw.get("reason", ""))[:80]
  return trial


class QuirkState:
  def __init__(self, path: Path | None = None):
    self.path = path or (identity_store.DATA_DIR / "quirk_state.json")
    self.cars: dict[str, dict] = {}
    self.memory: dict[str, dict] = {}  # wired cars: this drive only, never written
    self.generation = 0
    self._lock = threading.RLock()
    self._load()

  # ------------------------------------------------------------------ storage

  def _load(self) -> None:
    try:
      raw = json.loads(read_log(self.path, MAX_STATE_BYTES))
    except (OSError, ValueError):
      return
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION or not isinstance(raw.get("cars"), dict):
      return
    self.generation = _count(raw.get("generation"))
    for key, entry in list(raw["cars"].items())[:MAX_CARS]:
      if not isinstance(entry, dict) or not isinstance(key, str) or not key.startswith("bt:"):
        continue
      fingerprint = entry.get("fingerprint")
      clean = _fresh({str(k): str(v)[:80] for k, v in fingerprint.items()} if isinstance(fingerprint, dict) else {})
      clean["baseline"] = _clean_baseline(entry.get("baseline"))
      for rule_id, trial in (entry.get("trials") or {}).items() if isinstance(entry.get("trials"), dict) else ():
        cleaned = _clean_trial(trial)
        if cleaned is not None:
          clean["trials"][str(rule_id)[:64]] = cleaned
      clean["dismissed"] = [str(v)[:64] for v in entry.get("dismissed", [])][:16] if isinstance(entry.get("dismissed"), list) else []
      clean["updated"] = float(entry.get("updated") or 0.0) if isinstance(entry.get("updated"), (int, float)) else 0.0
      self.cars[key.upper().replace("BT:", "bt:", 1)] = clean

  def save(self) -> None:
    with self._lock:
      data = json.dumps({"version": STATE_VERSION, "generation": self.generation, "cars": self.cars}, indent=1) + "\n"
      self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
      with atomic_write(str(self.path), overwrite=True) as handle:
        handle.write(data)

  # --------------------------------------------------------------------- keys

  @staticmethod
  def key_for(address: str) -> str:
    return "bt:" + address.upper() if address else ""

  @staticmethod
  def wired_key(fingerprint: dict) -> str:
    """A head-unit key for one drive. Only a full fingerprint identifies a build; anything less gets no key."""
    return "usb:" + "|".join(fingerprint.get(k, "") for k in MATCH_FIELDS) if all(fingerprint.get(k) for k in MATCH_FIELDS) else ""

  def _store(self, key: str) -> dict:
    return self.memory if key.startswith("usb:") else self.cars

  def entry(self, key: str, fingerprint: dict) -> dict | None:
    """The car's entry. A changed head-unit fingerprint on a known address starts that car over."""
    if not key:
      return None
    with self._lock:
      store = self._store(key)
      current = store.get(key)
      if current is not None and fingerprint and current["fingerprint"] and current["fingerprint"] != fingerprint:
        current = None
      if current is None:
        if store is self.cars and len(self.cars) >= MAX_CARS:
          oldest = min(self.cars, key=lambda k: self.cars[k]["updated"])
          del self.cars[oldest]
        current = store[key] = _fresh(fingerprint)
      elif fingerprint and not current["fingerprint"]:
        current["fingerprint"] = dict(fingerprint)
      return current

  # ------------------------------------------------------------------- trials

  def trial(self, key: str, rule_id: str) -> dict | None:
    with self._lock:
      entry = (self._store(key) if key else {}).get(key)
      trial = entry["trials"].get(rule_id) if entry else None
      return trial if trial and trial["generation"] == self.generation else None

  def begin(self, key: str, fingerprint: dict, rule_id: str) -> dict | None:
    """Start a trial at level 1 from the baseline as it stands now (frozen), unless one is already running or was reverted."""
    with self._lock:
      entry = self.entry(key, fingerprint)
      if entry is None:
        return None
      existing = entry["trials"].get(rule_id)
      if existing is not None and existing["generation"] == self.generation:
        return existing
      trial = entry["trials"][rule_id] = _trial(1, entry["baseline"], self.generation)
      return trial

  def record(self, key: str, fingerprint: dict, outcome: SessionOutcome, *, rule_id: str = "", ladder_len: int = 0,
             trial_generation: int | None = None, now: float = 0.0) -> str:
    """Fold one finished session into the car's record; returns the verdict (or "baseline" when no trial was running)."""
    with self._lock:
      if trial_generation is not None and trial_generation != self.generation:
        return INCONCLUSIVE  # a reset happened while this session ran: its result belongs to nothing
      entry = self.entry(key, fingerprint)
      if entry is None:
        return "baseline"
      entry["updated"] = now
      trial = entry["trials"].get(rule_id) if rule_id else None
      if trial is None or trial["state"] == OFF or trial["generation"] != self.generation:
        self._add_baseline(entry["baseline"], outcome)
        return "baseline"
      verdict = classify(outcome, trial["frozen"])
      trial["sessions"] += 1
      if verdict == SUCCESS:
        trial["good"] += 1
        trial["worse"] = 0
        if trial["state"] == TRYING and trial["good"] >= CONFIRM_SESSIONS:
          trial["state"] = CONFIRMED
      elif verdict == FAILED:
        trial["bad"] += 1
        trial["worse"] = 0
        if trial["state"] == CONFIRMED:
          trial["worse"] = 1  # a confirmed fix that fails again is watched like a regression
          if trial["bad"] >= LEVEL_PATIENCE:
            self._revert(trial, "failed_after_confirm")
        elif trial["bad"] >= LEVEL_PATIENCE:
          trial["bad"], trial["good"] = 0, 0
          trial["level"] += 1
          if trial["level"] > ladder_len:
            self._revert(trial, "ladder_exhausted")
      elif verdict == REGRESSION:
        trial["worse"] += 1
        if trial["worse"] >= REVERT_STREAK:
          self._revert(trial, "regressed")
      return verdict

  @staticmethod
  def _revert(trial: dict, reason: str) -> None:
    trial["state"], trial["reason"] = OFF, reason

  @staticmethod
  def _add_baseline(baseline: dict, outcome: SessionOutcome) -> None:
    baseline["sessions"] += 1
    baseline["sessions_with_video"] += int(outcome.video_seen)
    baseline["early_shutdowns"] += int(outcome.early_shutdown)
    baseline["hangups"] += int(outcome.hangup)
    baseline["pairing_prompts"] += int(outcome.pairing_prompt)
    if outcome.first_frame_s is not None and outcome.video_seen:
      baseline["first_frame_s"] = (baseline["first_frame_s"] + [round(outcome.first_frame_s, 1)])[-KEEP_SAMPLES:]

  def early_shutdowns(self, key: str) -> int:
    """How many times this car, with no fix running, ended a session early after video started."""
    entry = (self._store(key) if key else {}).get(key)
    return entry["baseline"]["early_shutdowns"] if entry else 0

  # --------------------------------------------------------------- housekeeping

  def dismissed(self, key: str, rule_id: str) -> bool:
    entry = (self._store(key) if key else {}).get(key)
    return bool(entry) and rule_id in entry["dismissed"]

  def dismiss(self, key: str, rule_id: str) -> None:
    with self._lock:
      entry = (self._store(key) if key else {}).get(key)
      if entry and rule_id not in entry["dismissed"]:
        entry["dismissed"].append(rule_id)

  def forget(self, key: str) -> bool:
    with self._lock:
      return (self.cars.pop(key, None) is not None) | (self.memory.pop(key, None) is not None)

  def reset(self, key: str | None = None) -> None:
    """Reset handshake: clear the learning and invalidate every trial still in flight."""
    with self._lock:
      self.generation += 1
      if key:
        self.cars.pop(key, None)
        self.memory.pop(key, None)
        for entry in (*self.cars.values(), *self.memory.values()):
          for trial in entry["trials"].values():
            trial["generation"] = self.generation
      else:
        self.cars.clear()
        self.memory.clear()


def forget_offline(address: str, path: Path | None = None) -> bool:
  """Remove a car's entry from the file while the service isn't running."""
  state = QuirkState(path)
  changed = state.forget(QuirkState.key_for(address))
  if changed:
    state.save()
  return changed
