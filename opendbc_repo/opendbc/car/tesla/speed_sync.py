"""Speed sync: one-way native selected-speed mirror. Experimental; not vehicle-qualified.

No speed-control state is written here. Normal control packets are owned by the
existing controller. Only a bounded relative wheel request can be returned.

Each request is one right-wheel frame: a single click (+-1 mph), or a fast flick
(+-5 clicks) that moves the Tesla to the next multiple of 5. The Tesla only treats
a flick as fast when it starts a new gesture: a scroll soon after the previous one
moves the speed by 1. So fast steps are spaced, and the spacing
is learned. The panda decides what may be sent. A request the panda does not transmit (no bus-129 return, or a
bus-193 rejection) never reached the car, so it is cleared and retried later.
"""
from dataclasses import dataclass
from collections import deque
import math

# Vehicle qualification is a separate fact from this explicitly installed test build.
PROTOCOL_QUALIFIED = False
EXPERIMENTAL_TEST_BUILD = False  # True only once the panda images carry tesla_speed_sync.h (see the PR notes)
SYNC_FLAG = 4096  # TeslaSafetyFlags.SCROLL_SPEED_SYNC
NS = 1_000_000_000
FRESH_NS = 250_000_000
# No Tesla change by SETTLE_NS: the request went unanswered (the updated panda frees its own request after 0.8 s).
SETTLE_NS = 1_000_000_000
TOKENS_MAX = 30                   # panda rate limit: burst of 30 requests...
TOKEN_REFILL_NS = 1_050_000_000   # ...then one per second (a little slower here so the panda never refuses)
FAST_PAUSE_NS = 2 * NS            # after a refused fast step, single clicks for this long
MANUAL_COMPLETION_NS = 2 * NS
HANDOVER_NS = 100_000_000
TEMPLATE_SEND_NS = 100_000_000    # one wheel-frame period (panda allows 250 ms)
RETURN_NS = 100_000_000           # panda echoes a transmitted frame on bus 129 within ~5 ms
BACKOFF_NS = 300_000_000          # wait after a request the panda refused,
BACKOFF_MAX_NS = 2 * NS           # doubling on each refusal in a row
MANUAL_SETTLE_NS = 600_000_000    # let the Tesla apply the driver's own scroll first
FAST_TICKS = 5
# Fast-step spacing. Measured: a flick 0.1-0.24 s after the previous scroll moved
# the Tesla by 1; the driver's own flicks 0.4 s apart all jumped 5. Start short and
# lengthen on each fast step that lands as a single click; shorten again after a
# run of good ones. Nothing here ever stops the sync.
FAST_GAP_NS = 280_000_000
FAST_GAP_STEP_NS = 100_000_000
FAST_GAP_MAX_NS = 800_000_000
FAST_GAP_RELAX = 30               # good fast steps in a row before trying a shorter gap
FAST_RESPONSE_NS = 250_000_000    # Tesla applies a new gesture after ~0.23 s
STREAM_STEP_NS = 100_000_000      # a click inside a gesture shows up at the next DI frame
ENGAGE_DELAY_NS = 150_000_000     # pandad tells the panda about engagement at 10 Hz
MAX_RETRIES = 3                   # unanswered clicks retried at one speed before waiting


def neutral_wheel(data):
  if len(data) != 8 or data[0] & 3 != 1:
    return False
  bits = int.from_bytes(data, "little")
  return (all((bits >> shift) & 3 == 1 for shift in (3, 5, 8, 10, 12, 14)) and
          data[2] & 63 == 0 and data[3] & 63 == 0 and data[4] & 15 == 0)


def scroll_request(template, ticks):
  """Copy a physical neutral frame and set only the right-wheel scroll count."""
  if not neutral_wheel(template) or ticks not in (-FAST_TICKS, -1, 1, FAST_TICKS):
    return None
  data = bytearray(template)
  data[3] = (data[3] & 0xC0) | (ticks & 0x3F)
  return (0x3C2, bytes(data), 1)


def plan_step(native, target, allow_fast):
  """Return (ticks, expected native) for the next step from native toward target.

  A fast flick lands on the next multiple of 5 in that direction (measured: nearly
  every flick of 3+ clicks did this in one change). Use it only
  when that multiple does not pass the target and saves at least one click.
  """
  sign = 1 if target > native else -1
  if allow_fast:
    nxt = (native // 5 + 1) * 5 if sign > 0 else ((native - 1) // 5) * 5
    if abs(nxt - native) >= 2 and (target - nxt) * sign >= 0:
      return sign * FAST_TICKS, nxt
  return sign, native + sign


@dataclass(frozen=True)
class SyncAuthority:
  observed_boot_ns: int
  target_kph: float
  is_metric: bool
  configuration_id: str
  software_owned: bool
  document_hash: str = ""
  vehicle_hash: str = ""
  drive_start_ns: int = 0
  manual_receipts: tuple = ()


@dataclass(frozen=True)
class ManualReceipt:
  boot_ns: int
  frame_index: int
  name: str
  fast: bool
  worker_session: str
  sequence: int
  document_hash: str
  vehicle_hash: str
  drive_start_ns: int
  processed_boot_ns: int
  target_kph: float


@dataclass
class Pending:
  before: float
  direction: int
  units: int
  sent_ns: int
  data: bytes
  ticks: int = 0
  retry: bool = False
  returned: bool = False
  expected: int = 0
  gap_ns: int = 0                 # time since the previous scroll when sent


class SpeedSynchronizer:
  def __init__(self, *, qualified=PROTOCOL_QUALIFIED, experimental_test=False):
    self.qualified = qualified
    self.experimental_test = experimental_test
    self.enabled = bool(qualified or experimental_test)
    self.template = None
    self.template_ns = 0
    self.driver_ns = 0
    self.native_ns = 0
    self.native = None
    self.units = None
    self.cruise_state = None
    self.autopark = None
    self.counter = None
    self.di_valid = False
    self.di_key = (0, -1)
    self.wheel_stamp = 0
    self.pending = None
    self.unanswered = None
    self.unanswered_count = 0
    self.last_sent = 0
    self.last_template_sent = 0
    self.last_update = 0
    self.backoff_until = 0
    self.target = None
    self.steps = 0
    self.blocked = None
    self.status = "unqualified" if not self.enabled else "idle"
    self.active = False
    self.authority_id = None
    self.manual_armed = False
    self.manual_active = None
    self.manual_queue = []
    self.manual_receipts = {}
    self.manual_receipt_acks = []
    self.manual_ack_key = (0, -1)
    self.wheel_key = (0, -1)
    self.wheel_held = False       # the last wheel frame was a tilt or press without a scroll
    self.gas_was_pressed = False
    self.handover_until = 0
    self.response_ns = 0
    self.history = deque(maxlen=32)
    self.failure_logged = False
    # Nothing is switched off for good: a refusal only pauses that feature.
    self.fast_paused_until = 0
    self.retry_refused = False
    self.tokens = TOKENS_MAX
    self.refill_ns = 0
    self.last_scroll_ns = 0       # last scroll the Tesla saw: our sent request or the driver's
    self.fast_gap_ns = FAST_GAP_NS
    self.fast_streak = 0
    self.engaged_ns = 0
    self.refusals = 0
    self.notices = []
    self.noticed = set()

  def observe(self, packets):
    """Use original arrival timestamps; cached values never refresh freshness."""
    for stamp, frames in packets:
      if type(stamp) is not int or stamp <= 0:
        continue
      for frame_index, (address, data, bus) in enumerate(frames):
        if address == 0x3C2 and bus in (129, 193):
          # Panda echo of a frame we asked it to send: 129 = transmitted,
          # 193 = refused by safety. Exact bytes and clock must match.
          p = self.pending
          if p is not None and not p.returned and stamp >= p.sent_ns and bytes(data) == p.data:
            if bus == 129:
              p.returned = True
              self.last_scroll_ns = max(self.last_scroll_ns, p.sent_ns)
              self.refusals = 0
              self.steps += 1
              self.tokens = max(self.tokens - 1, 0)
            else:
              self._not_transmitted(p, "panda_rejected")
          continue
        if address == 0x3C2 and bus == 1:
          key = (stamp, frame_index)
          if key == self.wheel_key:
            continue
          if key < self.wheel_key:
            self.template = None
            self.blocked = "wheel_clock_reversal"
            continue
          self.wheel_key = key
          self.wheel_stamp = stamp
          if len(data) != 8:
            self.template = None
            if self.manual_active:
              self._pause_manual("ambiguous_manual_input")
            self.manual_armed = False
            continue
          mux = data[0] & 3
          if mux == 0:
            continue
          if mux != 1:
            self.template = None
            if self.manual_active:
              self._pause_manual("ambiguous_manual_input")
            self.manual_armed = False
            continue
          self._observe_manual(data, stamp, frame_index)
          if neutral_wheel(data):
            self.template, self.template_ns = bytes(data), stamp
          else:
            self.template = None
            self.driver_ns = stamp
            self.last_scroll_ns = max(self.last_scroll_ns, stamp)
            # Genuine controls take priority. The request stays outstanding
            # until the Tesla's speed changes; direction is then recomputed
            # from reality, so nothing is repeated blindly.
            if self.pending is not None:
              self._record("input_during_request", stamp)
        elif address == 0x286 and bus == 0:
          # Several DI frames can share one receive batch (same stamp), so
          # order by (stamp, index) like the wheel frames above.
          key = (stamp, frame_index)
          if key <= self.di_key:
            if key < self.di_key:
              self.blocked = "feedback_clock_reversal"
              self.native_ns = 0
            continue
          self.di_key = key
          if len(data) != 8 or data[0] != ((0x86 + 2 + sum(data[1:])) & 0xFF):
            self.di_valid = False
            continue
          counter = data[1] & 15
          advancing = self.counter is None or (counter - self.counter) % 16 == 1
          self.counter = counter
          # Same rule as the panda: a frame after a counter gap is not used,
          # and nothing may be sent until the next in-sequence frame.
          self.di_valid = advancing
          if not advancing:
            continue
          bits = int.from_bytes(data, "little")
          self.native = ((bits >> 15) & 511) * .5
          self.units = (bits >> 24) & 1  # 0 mph, 1 km/h
          self.cruise_state = (bits >> 12) & 7
          self.autopark = (bits >> 25) & 15
          self.native_ns = stamp

  def _observe_manual(self, data, stamp, frame_index):
    right = data[3] & 63
    right = right - 64 if right >= 32 else right
    if right:
      released = bytearray(data)
      released[3] &= 0xC0
      name = "right_wheel_up" if right > 0 else "right_wheel_down"
      if self.manual_active is None and self.wheel_held and abs(right) == 1 and neutral_wheel(released):
        # A wheel tilted or pressed often clicks once as it springs back. That click ends the nudge and is not a
        # scroll: the comma does not act on it, so nothing waits for it, and the Tesla's change is corrected below.
        self._record("nudge_release_click", stamp)
        self.wheel_held = False
        self.manual_armed = False
        return
      if (not neutral_wheel(released) or
          self.manual_active is None and not (self.manual_armed and self.template is not None and
            0 < self.template_ns <= stamp <= self.template_ns + FRESH_NS) or
          self.manual_active is not None and self.manual_active[0] != name):
        self._pause_manual("ambiguous_manual_input")
        return
      peak = max(abs(right), self.manual_active[1] if self.manual_active else 0)
      self.manual_active = (name, peak)
      self.manual_armed = False
      self.wheel_held = False
    elif neutral_wheel(data):
      if self.manual_active:
        if len(self.manual_queue) >= 16:
          self._fail("manual_input_overflow")
        else:
          name, peak = self.manual_active
          self.manual_queue.append((stamp, frame_index, name, peak >= 2))
        self.manual_active = None
      self.manual_armed = True
      self.wheel_held = False
    else:
      if self.manual_active:
        self._pause_manual("ambiguous_manual_input")
      self.manual_active = None
      self.manual_armed = False
      self.wheel_held = True

  def _pause_manual(self, reason):
    """Wheel input that cannot be read as one clean scroll. The comma's own input reader ignores the same gesture, so
    nothing waits for it: sync carries on and, after the driver's settle time, puts the Tesla back on the comma's speed.
    Logged once per engagement."""
    self._notice(reason)
    self._record(reason, self.last_update)
    self.manual_active = None
    self.manual_armed = False

  def accept_manual_receipts(self, authority, now_ns):
    # Metadata may arrive before Card sees the corresponding CAN batch. Cache
    # only bounded, validated receipts; never infer a gesture from the receipt.
    if type(authority.manual_receipts) is not tuple or len(authority.manual_receipts) > 16:
      self._fail("invalid_manual_receipts")
      return
    for receipt in authority.manual_receipts:
      if not isinstance(receipt, ManualReceipt):
        continue
      try:
        good = (
          type(receipt.boot_ns) is int and type(receipt.processed_boot_ns) is int and
          0 < receipt.boot_ns <= receipt.processed_boot_ns <= now_ns and
          type(receipt.frame_index) is int and 0 <= receipt.frame_index < 4096 and
          receipt.name in ("right_wheel_up", "right_wheel_down") and type(receipt.fast) is bool and
          type(receipt.worker_session) is str and len(receipt.worker_session) == 32 and
          all(c in "0123456789abcdef" for c in receipt.worker_session) and
          type(receipt.sequence) is int and receipt.sequence > 0 and
          receipt.document_hash == authority.document_hash and len(authority.document_hash) == 64 and
          receipt.vehicle_hash == authority.vehicle_hash and len(authority.vehicle_hash) == 64 and
          type(receipt.drive_start_ns) is int and receipt.drive_start_ns == authority.drive_start_ns > 0 and
          type(receipt.target_kph) in (int, float) and math.isfinite(receipt.target_kph) and
          0 < receipt.target_kph < 255)
      except (AttributeError, TypeError, ValueError):
        good = False
      if not good:
        continue
      if now_ns > receipt.boot_ns + MANUAL_COMPLETION_NS:
        # Validated but expired metadata cannot complete a gesture. Dispose of
        # it explicitly so it cannot occupy the producer queue indefinitely;
        # update() independently faults any unconfirmed observed gesture.
        self._ack_manual_receipt(receipt)
        continue
      key = (receipt.boot_ns, receipt.frame_index)
      if key <= self.manual_ack_key:
        self._ack_manual_receipt(receipt)
        continue
      if key not in self.manual_receipts and len(self.manual_receipts) >= 16:
        self._fail("manual_receipt_overflow")
        return
      if key in self.manual_receipts and self.manual_receipts[key] != receipt:
        self._fail("conflicting_manual_receipt")
        return
      self.manual_receipts[key] = receipt
      self._ack_manual_receipt(receipt)
    self.manual_receipts = {key: r for key, r in self.manual_receipts.items() if now_ns <= r.boot_ns + MANUAL_COMPLETION_NS}
    while self.manual_queue:
      stamp, index, name, fast = self.manual_queue[0]
      receipt = self.manual_receipts.get((stamp, index))
      if receipt is None:
        break
      if (receipt.name, receipt.fast) != (name, fast):
        self._fail("manual_receipt_mismatch")
        return
      self.manual_queue.pop(0)
      self.manual_receipts.pop((stamp, index))
      self.manual_ack_key = (stamp, index)
      self.target = None  # Re-read the processed target; never write its owner.

  def _ack_manual_receipt(self, receipt):
    # Acknowledge metadata custody only; this never acknowledges or repeats a
    # speed action. Card removes exactly these immutable receipts next frame.
    if receipt not in self.manual_receipt_acks:
      if len(self.manual_receipt_acks) >= 16:
        self._fail("manual_receipt_ack_overflow")
      else:
        self.manual_receipt_acks.append(receipt)

  def take_manual_receipt_acks(self):
    result = tuple(self.manual_receipt_acks)
    self.manual_receipt_acks.clear()
    return result

  def _fail(self, reason):
    if not self.blocked:
      self._record("fault", self.last_update, reason=reason)
    self.blocked = reason
    self.pending = None
    self.unanswered = None
    self._reset_handover()
    self.status = reason
    return None

  def _notice(self, kind, **extra):
    # One log line per kind per engagement; these are recoverable pauses.
    if kind not in self.noticed:
      self.noticed.add(kind)
      self.notices.append(dict(kind=kind, native=self.native, target=self.target, **extra))

  def _not_transmitted(self, p, reason):
    """The panda never put this frame on the bus, so the car never saw it."""
    self.pending = None
    if abs(p.ticks) > 1:
      # Use single clicks for a while, then try fast steps again.
      self.fast_paused_until = self.last_update + FAST_PAUSE_NS
      self._notice("fast_step_refused", reason=reason)
    elif p.retry:
      # Wait for the Tesla's speed to change for this request only.
      self.retry_refused = True
      self._notice("retry_refused", reason=reason)
    else:
      self._notice("request_not_transmitted", reason=reason)
      self.backoff_until = self.last_update + min(BACKOFF_NS << min(self.refusals, 3), BACKOFF_MAX_NS)
      self.refusals += 1
    self._record("not_transmitted", self.last_update, reason=reason, ticks=p.ticks)

  def _learn_fast_gap(self, p):
    landed = math.floor(self.native + .5)
    before = math.floor(p.before + .5)
    if landed == p.expected:
      self.fast_streak += 1
      if self.fast_streak >= FAST_GAP_RELAX and self.fast_gap_ns > FAST_GAP_NS:
        self.fast_gap_ns = max(self.fast_gap_ns - FAST_GAP_STEP_NS, FAST_GAP_NS)
        self.fast_streak = 0
    elif abs(landed - before) == 1 and abs(p.expected - before) >= 2:
      # The Tesla took it as part of the previous gesture: space them further.
      self.fast_streak = 0
      if p.gap_ns < FAST_GAP_MAX_NS:
        self.fast_gap_ns = min(max(self.fast_gap_ns, p.gap_ns) + FAST_GAP_STEP_NS, FAST_GAP_MAX_NS)
      self._notice("fast_step_landed_single", gap_ns=p.gap_ns, fast_gap_ns=self.fast_gap_ns)
      self._record("fast_landed_single", self.last_update, gap_ns=p.gap_ns, fast_gap_ns=self.fast_gap_ns)

  def _reset_handover(self):
    self.gas_was_pressed = False
    self.handover_until = 0

  def _record(self, event, now_ns, **extra):
    self.history.append(dict(event=event, boot_ns=now_ns, native=self.native,
                             target=self.target, native_ns=self.native_ns,
                             template_ns=self.template_ns, last_sent=self.last_sent,
                             units=self.units, cruise_state=self.cruise_state, **extra))

  def _resolve_pending(self, now_ns):
    """Advance the outstanding request. Returns True while it still blocks sending."""
    p = self.pending
    if p is None:
      return False
    if self.units is not None and self.units != p.units:
      self._fail("units_changed_during_request")
      return True
    if not p.returned and now_ns - p.sent_ns >= RETURN_NS:
      self._not_transmitted(p, "no_bus_return")
      return True
    if self.native_ns > p.sent_ns and self.native != p.before:
      # Fresh selected-speed feedback releases one outstanding request.
      # This is not an acknowledgement of command delivery or input origin:
      # Tesla may change its selection independently. Recompute from reality.
      self.pending = None
      self.unanswered = None
      self.unanswered_count = 0
      self.retry_refused = False
      self.response_ns = self.native_ns
      if abs(p.ticks) == FAST_TICKS:
        self._learn_fast_gap(p)
      self._record("native_response", now_ns, before=p.before, sent_ns=p.sent_ns, ticks=p.ticks)
      self.status = "native_speed_changed"
      return False
    if now_ns - p.sent_ns >= SETTLE_NS:
      # Unanswered. The panda keeps its own request open until the speed
      # changes (or, with the updated firmware, until its 1 s timeout).
      self.pending = None
      self.unanswered = p
      self.unanswered_count += 1
      self._notice("no_native_response", before=p.before, ticks=p.ticks)
      self._record("unanswered", now_ns, before=p.before, ticks=p.ticks)
      return True
    self.status = "waiting_for_response"
    return True

  def update(self, now_ns, *, target_kph, enabled, safe, longitudinal_active=True, gas_pressed=False):
    if not self.enabled:
      self.status = "unqualified"
      return None
    if not enabled:
      self.pending = None
      self.unanswered = None
      self.unanswered_count = 0
      self.retry_refused = False
      self.target = None
      self.steps = 0
      self.blocked = None
      self.active = False
      self.authority_id = None
      self._reset_handover()
      self.response_ns = 0
      self.backoff_until = 0
      self.engaged_ns = 0
      self.failure_logged = False
      self.noticed.clear()
      self.history.clear()
      self.manual_armed = bool(self.template is not None and
                               0 < self.template_ns <= now_ns <= self.template_ns + FRESH_NS)
      self.manual_active = None
      self.manual_queue.clear()
      self.manual_receipts.clear()
      self.manual_receipt_acks.clear()
      self.manual_ack_key = self.wheel_key
      self.status = "inactive"
      self.last_update = now_ns
      return None
    if type(now_ns) is not int or now_ns <= 0 or (self.last_update and now_ns <= self.last_update):
      return self._fail("controller_clock_reversal")
    gap = bool(self.last_update and now_ns - self.last_update > FRESH_NS)
    self.last_update = now_ns
    self.active = True
    if not self.engaged_ns:
      self.engaged_ns = now_ns
    if self.blocked:
      self.status = self.blocked
      return None
    if self._resolve_pending(now_ns) and self.pending is not None:
      return None
    if self.blocked:
      return None
    if gap:
      # A late controller frame only pauses this frame; a request is never
      # sent on timing that has not been observed continuously.
      self._reset_handover()
      self.status = "controller_clock_gap"
      return None
    # Past this bound no matching receipt can pass accept_manual_receipts: the comma did not act on that scroll
    # (it refused it, or never saw it). Drop it and carry on; the Tesla's change is then corrected like any other
    # change of its own. Never infer completion or repeat the input.
    if self.manual_queue and now_ns > self.manual_queue[0][0] + MANUAL_COMPLETION_NS:
      dropped = [entry for entry in self.manual_queue if now_ns > entry[0] + MANUAL_COMPLETION_NS]
      self.manual_queue = [entry for entry in self.manual_queue if entry not in dropped]
      self.manual_ack_key = max(self.manual_ack_key, (dropped[-1][0], dropped[-1][1]))
      self._notice("manual_action_unconfirmed", dropped=len(dropped))
      self._record("manual_action_unconfirmed", now_ns, dropped=len(dropped))
      self.target = None
    if not safe:
      self._reset_handover()
      self.target = None
      self.status = "inhibited"
      return None
    if not (0 < self.native_ns <= now_ns <= self.native_ns + FRESH_NS and self.di_valid and
            self.native is not None and self.native > 0 and
            self.cruise_state in (2, 4) and self.autopark not in (3, 4, 9)):
      self._reset_handover()
      self.status = "waiting_for_native_speed"
      return None
    if not math.isfinite(target_kph) or not 0 < target_kph < 255:
      self._reset_handover()
      self.status = "invalid_target"
      return None
    # Match the displayed selected speed in Tesla's units, not actual vehicle
    # speed or a transient model/curve/lead slowdown.
    target = math.floor((target_kph if self.units else target_kph / 1.609344) + .5)
    if target <= 0:
      self.status = "invalid_target"
      return None
    target_key = (target, self.units)
    if self.target != target_key:
      self.target = target_key
    # Card and controls publish independently. A genuine pedal release may
    # precede longActive by a frame; never transmit while neither current
    # authority is present.
    if self.gas_was_pressed and not gas_pressed:
      self.handover_until = now_ns + HANDOVER_NS
    self.gas_was_pressed = bool(gas_pressed)
    authorized = bool(longitudinal_active or gas_pressed)
    if authorized:
      self.handover_until = 0
    elif not (self.handover_until and now_ns < self.handover_until):
      self._reset_handover()
      self.status = "inhibited"
      return None
    if self.manual_active is not None or self.manual_queue:
      self.status = "waiting_for_manual_processing"
      return None
    if not authorized:
      if self.status != "waiting_for_handover":
        self._record("handover_wait", now_ns, until_ns=self.handover_until)
      self.status = "waiting_for_handover"
      return None
    native = math.floor(self.native + .5)
    if native == target:
      self.unanswered = None
      self.unanswered_count = 0
      self.status = "matched"
      return None
    # The driver's own scroll takes 0.18-0.4 s to reach the Tesla's selected
    # speed (measured). A request in that window can cancel a fast flick, so
    # let the Tesla act on it first.
    if self.driver_ns and now_ns < self.driver_ns + MANUAL_SETTLE_NS:
      self.status = "waiting_for_driver_scroll"
      return None
    retry = False
    if self.unanswered is not None:
      u = self.unanswered
      if self.native_ns > u.sent_ns and self.native != u.before:
        self.unanswered = None
        self.unanswered_count = 0
        self.retry_refused = False
      elif self.retry_refused or self.unanswered_count > MAX_RETRIES:
        # e.g. the Tesla is at its own speed limit; don't keep scrolling.
        self.status = "waiting_for_native_change"
        return None
      else:
        retry = True
    if now_ns < self.backoff_until:
      self.status = "backoff"
      return None
    # The panda only learns that comma engaged from pandad's next heartbeat.
    if now_ns < self.engaged_ns + ENGAGE_DELAY_NS:
      self.status = "waiting_for_panda_engage"
      return None
    # Mirror the panda's rate limit so it never has to refuse a request.
    if self.refill_ns == 0:
      self.refill_ns = now_ns
    while self.tokens < TOKENS_MAX and now_ns - self.refill_ns >= TOKEN_REFILL_NS:
      self.tokens += 1
      self.refill_ns += TOKEN_REFILL_NS
    if self.tokens >= TOKENS_MAX:
      self.refill_ns = now_ns
    if self.tokens <= 0:
      self.status = "rate_limited"
      return None
    # Any physical neutral frame newer than the last one used, still current.
    if not (self.template is not None and 0 < self.template_ns <= now_ns <= self.template_ns + TEMPLATE_SEND_NS and
            self.template_ns > self.last_template_sent):
      self.status = "waiting_for_neutral_wheel"
      return None
    ticks, expected = plan_step(native, target, allow_fast=now_ns >= self.fast_paused_until and self.units == 0)
    gap_ns = now_ns - self.last_scroll_ns if self.last_scroll_ns else 10 * NS
    if abs(ticks) == FAST_TICKS and gap_ns < self.fast_gap_ns:
      # Too soon for the Tesla to start a new gesture. Clicks give 1 mph per
      # 0.1 s and each one restarts the gap, so while fast steps are quicker
      # overall (gap under 0.5 s) wait for them on any change of 5 or more.
      # For a short change, wait only if that beats clicking there.
      wait_ns = self.fast_gap_ns - gap_ns
      if ((self.fast_gap_ns < FAST_TICKS * STREAM_STEP_NS and abs(target - native) >= FAST_TICKS) or
          wait_ns + FAST_RESPONSE_NS <= abs(expected - native) * STREAM_STEP_NS):
        self.status = "waiting_for_fast_gap"
        return None
      ticks, expected = plan_step(native, target, allow_fast=False)
    message = scroll_request(self.template, ticks)
    if message is None:
      return self._fail("invalid_template")
    direction = 1 if ticks > 0 else -1
    self.pending = Pending(self.native, direction, self.units, now_ns, message[1], ticks=ticks, retry=retry,
                           expected=expected, gap_ns=gap_ns)
    # A retry keeps the unanswered request on record until the speed changes,
    # so a refused retry falls back to waiting rather than retrying forever.
    self.last_sent = now_ns
    self.last_template_sent = self.template_ns
    self.status = "request_pending"
    self._record("request", now_ns, direction=direction, ticks=ticks, retry=retry, gap_ns=gap_ns)
    return message


def sync_controller(controller, CC, CS, now_ns):
  """Read-only mirror. now_ns is BOOTTIME, matching the CAN producer clock."""
  from opendbc.car.structs import CarState
  synchronizer = controller.speed_sync
  out = CS.out
  authority = getattr(controller, "speed_sync_authority", None)
  authority_valid = bool(
    isinstance(authority, SyncAuthority) and authority.software_owned is True and
    type(authority.observed_boot_ns) is int and
    0 < authority.observed_boot_ns <= now_ns <= authority.observed_boot_ns + 100_000_000 and
    type(authority.is_metric) is bool and synchronizer.units == int(authority.is_metric) and
    type(authority.target_kph) in (int, float) and math.isfinite(authority.target_kph) and authority.target_kph == float(out.vCruise) and
    type(authority.configuration_id) is str and len(authority.configuration_id) == 64 and all(c in "0123456789abcdef" for c in authority.configuration_id))
  if authority_valid and CC.enabled:
    if synchronizer.authority_id not in (None, authority.configuration_id):
      synchronizer._fail("configuration_changed")
    synchronizer.authority_id = authority.configuration_id
    synchronizer.accept_manual_receipts(authority, now_ns)
  safe = (authority_valid and out.canValid and not out.canTimeout and out.cruiseState.enabled and
          out.gearShifter == CarState.GearShifter.drive and
          not (out.standstill or out.brakePressed or out.accFaulted or out.stockAeb or out.stockFcw or
               out.espDisabled or out.espActive or out.vehicleSensorsInvalid or out.carNotReady or out.doorOpen or out.seatbeltUnlatched or
               out.steerFaultPermanent or out.steerFaultTemporary or out.stockLkas or
               out.invalidLkasSetting or CS.autopark))
  message = synchronizer.update(now_ns, target_kph=float(out.vCruise),
                                enabled=bool(CC.enabled and not CC.cruiseControl.cancel), safe=bool(safe),
                                longitudinal_active=bool(CC.longActive), gas_pressed=bool(out.gasPressed))
  if not authority_valid and CC.enabled and not synchronizer.blocked:
    synchronizer.status = "owner_or_config_ineligible"
  if synchronizer.notices or (synchronizer.blocked and not synchronizer.failure_logged):
    from opendbc.car.carlog import carlog
    for notice in synchronizer.notices:
      carlog.warning("speed sync paused: %s", dict(notice, boot_ns=now_ns, history=list(synchronizer.history)[-8:]))
    synchronizer.notices.clear()
    if synchronizer.blocked and not synchronizer.failure_logged:
      synchronizer.failure_logged = True
      carlog.warning("speed sync stopped: %s", {"reason": synchronizer.blocked, "boot_ns": now_ns,
                     "native": synchronizer.native, "target": synchronizer.target, "history": list(synchronizer.history)})
  return [] if message is None else [message]
