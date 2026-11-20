"""Pure Conditional Model handshake and resident-runner state machine; no process or GPU imports."""
from dataclasses import dataclass
import uuid

FRESH_NS = 150_000_000
ACK_TIMEOUT_NS = 1_000_000_000
SWITCH_TIMEOUT_NS = 2_000_000_000
SETTLE_FRAMES = 5
PRELOAD_TIMEOUT_NS = 120_000_000_000


def fresh(stamp: int, now: int) -> bool:
  return 0 < stamp <= now and now - stamp <= FRESH_NS


@dataclass(frozen=True)
class State:
  session: str
  request: str
  phase: str
  frame: int
  born: int


@dataclass(frozen=True)
class Ack:
  session: str
  request: str
  holding: bool
  source: int
  stamp: int
  idle: bool = False


class EngagementHold:
  """A granted hold never expires into permission to engage."""
  def __init__(self):
    self.owner: tuple[str, str] | None = None
    self.last_stamp = 0
    self.held_since = 0

  def step(self, state: State | None, stamp: int, now: int, *, idle: bool, model_stamp: int, model_valid: bool,
           release_allowed: bool = True) -> bool:
    if state is None or not fresh(stamp, now) or stamp < self.last_stamp:
      return self.owner is not None
    self.last_stamp = stamp
    key = state.session, state.request
    if state.phase in ('waiting', 'switching', 'recovering'):
      if idle and state.session and state.request:
        if self.owner != key:
          self.held_since = now
        self.owner = key
    elif self.owner is not None and state.phase in ('ready', 'disabled'):
      # The same request can finish/abort, or a new process can establish a new
      # publisher. Old output cannot release a hold after either boundary.
      same = key == self.owner
      restarted = state.session != self.owner[0]
      if (idle and release_allowed and (same or restarted) and model_valid and fresh(model_stamp, now) and
          0 < state.born <= state.frame <= model_stamp <= now and state.born <= stamp):
        self.owner = None
        self.held_since = 0
    return self.owner is not None


class ResidentSwitch:
  def __init__(self, models: dict, active: str, *, now: int, error: str = ''):
    # Each entry is (runner, prepared artifact, preallocated reset closure).
    self.models = models
    self.active = self.previous = self.target = active
    self.session = uuid.uuid4().hex
    self.born = now
    self.request = ''
    self.phase = 'ready' if models else 'disabled'
    self.error = error
    self.started = self.announced = self.frame = self.frames = 0
    self.elapsed = 0.
    self.safe_to_finish = False

  def before_frame(self, desired: dict | None, *, now: int, idle: bool, ack: Ack | None) -> bool:
    """Return True only when the active runner changed at this frame boundary."""
    old = self.active
    self.safe_to_finish = idle
    if (desired and desired.get('session') == self.session and desired.get('id') != self.request and
        self.phase == 'ready'):
      self.request = desired['id']
      self.error = ''
      if desired.get('model') not in self.models:
        self.error = 'Requested model is not resident; configure models with the vehicle off'
      elif not idle:
        self.error = 'Fully disengage cruise and independent lateral control before switching'
      elif desired['model'] != self.active:
        self.previous, self.target = self.active, desired['model']
        self.phase, self.started, self.announced = 'waiting', now, 0
    if self.phase == 'waiting':
      if not idle:
        # The driver may engage before the hold is granted, or a source may
        # briefly go stale. Retain the current runner without declaring a fault.
        self.abort('', now)
      elif now - self.started >= ACK_TIMEOUT_NS:
        self.abort('Engagement hold acknowledgment timed out; current model retained', now)
      elif (self.announced and ack is not None and ack.holding and fresh(ack.stamp, now) and
            (ack.session, ack.request) == (self.session, self.request) and
            self.announced <= ack.source <= ack.stamp):
        try:
          self.models[self.target][2]()
        except Exception:
          self.abort('Model state reset failed; current model retained', now)
        else:
          self.active, self.phase, self.frames = self.target, 'switching', 0
    elif self.phase == 'switching' and idle and now - self.started >= SWITCH_TIMEOUT_NS:
      self.abort('Model handoff timed out; previous model restored', now)
    return self.active != old

  def abort(self, error: str, now: int) -> None:
    resetting = self.active != self.previous
    self.active, self.phase, self.error = self.previous, 'recovering' if resetting else 'ready', error
    self.frames = 0
    self.elapsed = (now - self.started) / 1e9 if self.started else 0.
    if resetting:
      try:
        self.models[self.previous][2]()
      except Exception as exc:
        # Do not run potentially corrupted history after a reset fails. Exit
        # under the existing hold; manager owns the isolated recovery process.
        self.error = 'Previous model reset failed; isolated recovery required'
        raise RuntimeError(self.error) from exc

  def published(self, frame: int, now: int) -> None:
    if frame <= self.frame:
      return
    self.frame = frame
    if self.phase in ('switching', 'recovering') and self.safe_to_finish:
      self.frames += 1
      if self.frames >= SETTLE_FRAMES:
        self.phase = 'ready'
        self.elapsed = (now - self.started) / 1e9


