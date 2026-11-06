"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

A ModelState that starts as the small model and upgrades to the Jetson.

modeld's large-model load is a one-shot with BIG_MODEL_TIMEOUT and a silent,
one-way fallback. That suits a chestnut, powered from the comma. A Jetson is
on the ignition rail: cranking browns it out, so its boot starts about when
the comma goes onroad and takes 45 to 100 s, long after modeld gave up.

So modeld is handed the small model it already loaded, and the Jetson is
swapped in underneath once the link, the engine and the warp are all there.
modeld re-reads `model` every frame, and `modelV2.big` keeps its meaning.

Until the swap the comma runs its own model and nothing else; the link idles
on a keepalive ping. The large model is built on the swap frame and starts
from a reset history, as at every modeld start. The second after the swap is
its proof: the fork holds engagement off for it, and a large model that holds
a frame in it (model_state.PROVING_FRAMES) hands back before anyone can
engage. While it drives, a reply that is not back in time is not waited for:
the previous frame's output is published again (a held frame,
model_state.HOLD_FRAME) and the camera frame is not dropped. Too many holds
hand the drive back to the small model, as a lost link does.

No frames go to the host while the small model drives. Sending them costs
the comma ~9 ms of every frame for as long as the window stays shut (a whole
drive with MADS on), which starves the driver monitoring model under
selfdrived's frequency floor (commIssue).

Two rules the swap keeps:

- tinygrad work happens on modeld's thread. The joining thread does link IO
  only; building the JetlinkModelState unpickles a TinyJit, and doing that
  next to the small model running frames on the same device is not safe.
- never swap while the plan is steering. The two models disagree by ~195 m of
  planned path, and the swap costs a frame or two. The adapter says before
  every frame whether anything is in control (in_control), from openpilot's
  own messages; standstill alone is not enough, longitudinal control may still
  hold the brake.
"""
from __future__ import annotations

import os
import threading
import time

from jetlink import protocol as P
from jetlink.comma import gadget
from jetlink.openpilot.model_state import JetlinkModelState
from jetlink.transport.priority import background_thread

# after a join fails or the large model dies mid-drive. Long enough not to
# thrash a booting Jetson, short enough to catch one that finished a moment later
REJOIN_DELAY = 5.0
# doubled per consecutive failure, reset by a join that lasted STABLE_SECONDS.
# A link that dies on its first frame every time must not cost a swap, a
# demote and an alert every few seconds
REJOIN_DELAY_MAX = 60.0
STABLE_SECONDS = 60.0
# after a join that held: a link that ran for minutes and then went is a USB
# drop, and the host re-enumerates a rebound gadget in under a second
REJOIN_DELAY_QUICK = 1.0
# failures in a streak retried that quickly before the doubling starts. A
# demote is mostly one late frame, a tail of the link or the host and not a
# dead one, and a full backoff costs 5 to 60 s on the small model. A link
# that fails on its heels this many times is backed off as usual
QUICK_RETRIES = 3
# how often a link that is ready but has nowhere to land gets checked, and
# how long the check may take; both off the frame loop
KEEPALIVE_PERIOD = 10.0
PING_TIMEOUT = 2.0
# how often a backoff looks at the gadget. A host that configures it again
# after it went away is a replug, which the backoff is not for: a replugged
# Jetson is back in under half a second
REPLUG_POLL = 0.25
# link drops in one drive before the status names the cable. One is chance;
# repeated drops are almost always the USB-C port losing its host, and nothing
# else the driver can see says so
DROPS_TO_BLAME_CABLE = 2
# A host slower than the camera but never that slow (an iPhone that has warmed
# up: 55 ms a frame drops one camera frame in ten) still has modeld skipping
# frames, and selfdrived soft-disables on modeldLagging once modeld's filtered
# share of them (frameDropPerc) passes 1 %: three drops close together. modeld
# writes that share onto the model before every run(), and the large model
# hands back past DROP_LIMIT, short of the line: one dropped frame is forgiven,
# a second within about 6.5 s is not. modeld forgives the frame of a handover
# too, so the drops that decided it never reach selfdrived
DROP_LIMIT = 0.0075
# the small model drives the first frames of every modeld start, even with the
# large model ready: its first run in a process costs ~1.3 s, which the first
# fallback frame would pay (25 dropped frames, commIssue) if the large model
# drove from frame one. Nothing is in control at a modeld start
SMALL_WARMUP_FRAMES = 3
# the panel's line while a ready link waits for the window
READY = 're-engage to switch'
# why a demote happened, as the log says it
LOST = 'lost jetlink'
BEHIND = 'jetlink fell behind'
# a NaN or an inf in an output: a host that answers, wrongly. A lag and not a
# drop, the link is kept, and the next swap starts the host from a reset
BAD_OUTPUT = 'jetlink output not finite'
# what modeld_v2 builds the frame's inputs from: the small bundle's desire
# name and optional slots, read off the small model whichever drives
SMALL_READS = ('desire_key', 'numpy_inputs')
# why a built model was let go before it drove
NOT_UP = 'could not bring up the large model'
# and as the leave says it to the server (protocol.Msg.LEAVE)
LEAVING = {LOST: P.LEAVE_LOST, BEHIND: P.LEAVE_BEHIND}
# the demotes that count as lag, which says nothing about the cable
LAGS = (BEHIND, BAD_OUTPUT)
# and as the panel's one line says it while the join tries again, with the
# `why` of its progress code (status.PROGRESS_CODES 'reconnecting')
RECONNECTING = {LOST: ('reconnecting', 'lost'), BEHIND: ('fell behind, reconnecting', 'behind'),
                NOT_UP: ('load failed, reconnecting', 'load_failed'),
                BAD_OUTPUT: ('bad output, reconnecting', 'bad_output')}
# how long a model name the comma's index did not have stays the sha's
NAME_RETRY = 30.0


class JoiningModelState:
  """Duck-types openpilot's modeld ModelState, with a second one inside.

  `progress` is where the join says what it is waiting on (status.Progress),
  `log` is cloudlog on a comma, and `in_control` the adapter's, asked before
  every frame; without one, the in_control last written stands. `name` is
  the catalog's name for a model's sha256, for the runtime record.
  """

  def __init__(self, small, connect, build, prepare=None, reset_small=None, *, progress, log, in_control=None,
               name=None):
    self._small = small
    self._active = small
    self._connect = connect
    self._build = build
    self._reset_small = reset_small
    self._progress = progress
    self._log = log

    # whatever the swap would otherwise do on the frame loop, done now on
    # modeld's main thread before the frame loop exists; at the swap it costs
    # a 1.5 s frame
    if prepare is not None:
      try:
        t0 = time.monotonic()
        prepare()
        log.warning("jetlink: prepared the large model ahead of the swap in %.2f s", time.monotonic() - t0)
      except Exception:
        log.exception("jetlink: could not prepare the large model ahead of the swap")
        raise

    # handed over by the joining thread, consumed by the swap frame, which
    # builds the large model state from it. Only ever assigned under the lock
    self._joined: tuple[object, object] | None = None
    # a link is ready and waiting for the window. Kept true while a keepalive
    # ping has _joined out for a moment
    self._available = False
    # the large model state, from the swap until it hands back: always _active.
    # Only the frame thread assigns it
    self._big = None
    # what the frame thread let go of and why (LOST, BEHIND, BAD_OUTPUT, or a
    # build that failed), for the join thread to say goodbye to and close or keep.
    # Assigned under the lock
    self._retired: tuple[object, str] | None = None
    self._lock = threading.Lock()
    self._rejoin = threading.Event()
    self._rejoin.set()
    # earliest the join loop may try again. Without a backoff a fault that
    # recurs on the first frame after every swap is a connect, build and demote
    # every 700 ms for the drive, each costing modeld a 100 ms frame
    self._rejoin_at = 0.0
    self._failures = 0
    self._joined_at = 0.0
    # links lost and lag demotes after a swap this drive. Only a lost link
    # says anything about the cable
    self._drops = 0
    self._lags = 0
    # changes of the model that drives, and decisions to change it: modeld
    # compares it across run() (handovers)
    self._handovers = 0
    # set on a frame the large model fell behind on; the next frame demotes
    self._lagging = False
    # modeld's share of dropped camera frames, for the frame about to run
    self._frame_drop_ratio = 0.0
    # set by the large model's first frame: the join thread clears the
    # progress, which is params IO and not the frame thread's
    self._announce = False
    # frames the small model has run since start
    self._small_frames = 0
    # whether the host had let go of the gadget when the last link was lost,
    # and whether this failure streak has already skipped a backoff for a replug
    self._host_left = False
    self._replugged = False

    # in control until the adapter says otherwise, so a swap can never happen
    # on no information
    self._in_control = True
    self._ask_in_control = in_control
    self._stop = threading.Event()

    # whether the large model has produced a frame; a first inference that
    # fails never announces readiness. Travels in modelV2.big and
    # modelDataV2SP, not a param: a chestnut's load is over once, this never is
    self._loading = True

    # the runtime record's: when the large model's last answer (not a held
    # frame) came, and a wake for its writer on a hand-back and the first
    # answer. The frame thread assigns them and nothing else; the writer
    # thread reads the rest itself. The path is taken now, so a test's stays
    # its own
    self._output_at = 0.0
    self._record = threading.Event()
    self._record_path = gadget.MODELD
    self._name = name

    self._thread = threading.Thread(target=self._join_loop, daemon=True)
    self._thread.start()
    self._writer = threading.Thread(target=self._write_loop, daemon=True)
    self._writer.start()

  # -- what modeld reads ------------------------------------------------------
  # A read not defined here follows the model that is driving (__getattr__):
  # vision_input_names, and modeld_v2's constants, smoothing and action
  # function, which sunnypilot keeps on the ModelState so a custom small bundle
  # can carry its own. Swaps and demotes happen inside run(), so what is read
  # after it belongs to the model whose output it is. What the loop writes
  # lands on both, but for what only this reads. The frame's inputs are built
  # for the small bundle (SMALL_READS, off the small model whichever drives);
  # the large model reads the desire under any name and the loop always
  # supplies what it needs, so a swap can land on any frame

  @property
  def big_model_available(self) -> bool:
    """Connected and waiting to switch: what big_model_state calls ready, and
    what the driver is offered."""
    return not self._stop.is_set() and self._available and self._active is self._small

  @property
  def chestnut(self) -> bool:
    # modelV2.big. False while proxying, as the small model would report
    return getattr(self._active, 'chestnut', False)

  @property
  def handovers(self) -> int:
    """Moves on every swap and demote, and on the frame that decides a lag
    demote. modeld resets its dropped-frame filter when this changes across
    run(), so the stall of a handover is not lag, as for a chestnut's fallback.
    A count and not modelV2.big: a swap whose first frame fails and demotes in
    the same run() leaves `chestnut` as it was."""
    return self._handovers

  @property
  def big_model_state(self) -> str:
    """modelDataV2SP.acceleratorState, one of its enum names (STATES)."""
    if self._stop.is_set():
      return 'unavailable'
    if self._active is not self._small:
      return 'running'
    if self.big_model_available:
      # nothing left to wait for but a window. The icon says so rather than
      # pulsing "loading" for the rest of a drive with no stop in it
      return 'ready'
    return 'retrying' if self._failures else 'joining'

  @property
  def client(self):
    # read by the status publisher on every send, so the telemetry follows the link
    return getattr(self._active, 'client', None)

  @property
  def lat_delay(self):
    return self._active.lat_delay

  @lat_delay.setter
  def lat_delay(self, value):
    # modeld writes this every frame. Set on both, so a model that joins
    # mid-drive does not start on a stale delay
    self._small.lat_delay = value
    if self._active is not self._small:
      self._active.lat_delay = value

  @property
  def PLANPLUS_CONTROL(self):
    return self._active.PLANPLUS_CONTROL

  @PLANPLUS_CONTROL.setter
  def PLANPLUS_CONTROL(self, value):
    self._small.PLANPLUS_CONTROL = value
    if self._active is not self._small:
      self._active.PLANPLUS_CONTROL = value

  @property
  def frame_drop_ratio(self) -> float:
    return self._frame_drop_ratio

  @frame_drop_ratio.setter
  def frame_drop_ratio(self, value):
    # modeld's frameDropPerc / 100, written before every run(). Read here, by
    # the lag rule; no small model reads it
    self._frame_drop_ratio = value

  @property
  def in_control(self) -> bool:
    return self._in_control

  @in_control.setter
  def in_control(self, value):
    # openpilot or MADS in control, by openpilot's own messages, asked before
    # every run(): the swap waits for False. Not a thread polling selfdrived:
    # that wakes ~300 times a second and takes 6 to 11 % of a core inside modeld
    self._in_control = value

  def __getattr__(self, name):
    # Only for names this class does not define, so a read that a comma or
    # sunnypilot sync adds is not an AttributeError on the frame thread (modeld
    # re-raises it and is dead for the drive). Reads only; a write lands on
    # this object alone, so each one modeld makes has its own setter above
    if name.startswith('_'):
      # also what keeps __init__ from recursing before _active exists
      raise AttributeError(name)
    # a small model without one of SMALL_READS has none, whichever drives
    return getattr(self._small if name in SMALL_READS else self._active, name)

  # -- the frame path ---------------------------------------------------------

  def run(self, bufs, transforms, inputs, after_enqueue=None):
    if self._ask_in_control is not None:
      self.in_control = self._ask_in_control()
    if self._lagging:
      # the last frame was the large model's last, published as it came
      self._lagging = False
      self._demote(BEHIND)
    elif self._active is not self._small and self._frame_drop_ratio > DROP_LIMIT:
      # modeld is behind the large model; this frame is the small model's
      self._log.warning("jetlink: modeld dropped %.2f %% of camera frames behind the large model, " +
                        "the small model drives from this one", self._frame_drop_ratio * 100)
      self._demote(BEHIND)
    self._maybe_swap()
    if self._active is self._small:
      self._small_frames += 1
      return self._small.run(bufs, transforms, inputs, after_enqueue)
    return self._run_big(bufs, transforms, inputs, after_enqueue)

  def _run_big(self, bufs, transforms, inputs, after_enqueue):
    big = self._big
    started = time.monotonic()
    try:
      result = big.run(bufs, transforms, inputs, after_enqueue)
    except Exception as e:
      failed = time.monotonic()
      self._log.exception("jetlink: large model failed mid-drive, back to the small model")
      self._demote(BAD_OUTPUT if isinstance(e, P.NotFinite) else LOST)
      demoted = time.monotonic()
      # re-run the frame rather than propagate: modeld's fallback is permanent,
      # this one is retryable. after_enqueue is dropped, the large model may
      # already have called it
      result = self._small.run(bufs, transforms, inputs, None)
      # all of it is one modeld frame, 42 to 107 ms measured with the small
      # model warm. The dropped camera frames are forgiven
      # (modeld sees the model change), but the log should say which part it was
      done = time.monotonic()
      self._log.warning("jetlink: fallback frame %.0f ms: link %.0f, demote %.0f, small model %.0f",
                        (done - started) * 1e3, (failed - started) * 1e3,
                        (demoted - failed) * 1e3, (done - demoted) * 1e3)
      return result
    if not getattr(big, 'held', False):
      self._output_at = time.monotonic()
    if self._loading:
      # a connected engine can still fail its first inference; only announce
      # readiness after a frame the caller can publish
      self._loading = False
      self._announce = True
      self._rejoin.set()
      self._record.set()
      self._log.warning("jetlink: large model joined mid-drive, modelV2.big is now true")
    if big.behind:
      # this frame's output is published as it came; its stall is forgiven now
      self._lagging = True
      self._handed_over()
      self._log.warning("jetlink: large model %s, the small model drives from the next", big.behind)
    return result

  def _maybe_swap(self) -> None:
    """Build the large model state from a join that has landed and swap it in,
    on this thread, which is where everything tinygrad touches must happen.
    Its first frame resets the host's history. A build that fails is backed
    off like a demote, or one that fails the same way every time is a connect
    and a build per second for the drive."""
    if self._joined is None or self._in_control or self._small_frames < SMALL_WARMUP_FRAMES:
      return
    with self._lock:
      joined, self._joined = self._joined, None
      if joined is not None:
        self._available = False
    if joined is None:
      return
    client, spec = joined
    try:
      t0 = time.monotonic()
      big = self._build(client, spec)
      big.lat_delay = getattr(self._small, 'lat_delay', big.lat_delay)
      self._log.warning("jetlink: built the large model state in %.0f ms", (time.monotonic() - t0) * 1000)
    except Exception:
      self._log.exception("jetlink: could not bring up the large model, staying small")
      with self._lock:
        self._retired = (client, NOT_UP)
      self._back_off()
      return
    self._big = big
    self._joined_at = time.monotonic()
    # no wake: the record says the swap with the first answer, which wakes it
    self._handovers += 1
    self._active = big

  def _demote(self, why: str) -> None:
    """Back to the small model, from a reset history. On the frame thread, so
    nothing here waits: the join thread reads the port, reports and closes
    the link, moments later. Counted as a lost link or a hand-back for lag,
    and backed off."""
    big, self._big = self._big, None
    if why in LAGS:
      self._lags += 1
    else:
      self._drops += 1
    with self._lock:
      self._retired = (big, why)
    self._back_off()
    self._active = self._small
    self._handed_over()
    self._loading = True
    if self._reset_small is not None:
      self._reset_small()

  def _handed_over(self) -> None:
    self._handovers += 1
    self._record.set()

  def _close_retired(self, retired, why: str) -> None:
    """Say goodbye over the retired large model's link, then close it if it
    is dead. A live one stays, on `Link`, for the next attempt, which is a
    hello and a model request over it and not a reconnect: a model that fell
    behind, or failed on the comma's side, leaves one. `retired` is the model
    state, or the bare client of a build that failed."""
    self._leave(retired, LEAVING.get(why, why))
    if not getattr(getattr(retired, 'client', retired), 'dead', True):
      return
    try:
      retired.close()
    except Exception:
      self._log.exception('jetlink: closing the retired link')

  def _leave(self, model, reason: str) -> None:
    """Tell the server why the large model `model` stops using its link, with
    what the comma measured over it (model_state.Trips). Off the frame thread."""
    self._say_leaving(getattr(model, 'client', None), reason, getattr(model, 'trips', None))

  def _say_leaving(self, client, reason: str, trips=None) -> None:
    """JetlinkClient.leave, which says nothing over a dead link."""
    leave = getattr(client, 'leave', None)
    if leave is None:
      return
    measured = trips.summary() if trips is not None else {}
    try:
      leave(reason, drops=self._drops, lags=self._lags, **measured)
    except Exception:
      self._log.exception("jetlink: could not say why the link is left")

  def _back_off(self) -> None:
    """Push the next attempt out, further each time one fails on its heels.

    Each failed cycle is a swap frame, a demote frame and the alerts that go
    with them. The first QUICK_RETRIES failures of a streak are retried in
    REJOIN_DELAY_QUICK; the ones after double from REJOIN_DELAY. A join that
    held for STABLE_SECONDS starts a new streak.
    """
    held = time.monotonic() - self._joined_at if self._joined_at else 0.0
    stable = bool(self._joined_at) and held > STABLE_SECONDS
    self._failures = 1 if stable else self._failures + 1
    if stable:
      self._replugged = False   # a new streak may skip a backoff for a replug again
    self._joined_at = 0.0
    if self._failures <= QUICK_RETRIES:
      delay = REJOIN_DELAY_QUICK
    else:
      delay = min(REJOIN_DELAY * 2 ** (self._failures - QUICK_RETRIES - 1), REJOIN_DELAY_MAX)
    self._rejoin_at = time.monotonic() + delay
    self._rejoin.set()
    self._log.warning("jetlink: next attempt in %.0f s (failure %d, link held %.0f s, drop %d, lag %d this drive)",
                      delay, self._failures, held, self._drops, self._lags)

  # -- background -------------------------------------------------------------

  def _report(self, stage: str, msg: str, code: str | None, args: dict | None = None) -> None:
    """Tell the UI what the join is waiting on.

    Offroad the record carries a provisioning run's progress; without this, a
    Jetson that is not plugged in looks like one about to load. No fraction to
    give, and the panel does not invent one.
    """
    # the count is the diagnosis. A link that runs for minutes and then goes,
    # again and again, is the cable, and the cable is the one thing the
    # driver can do something about
    self._progress.report(stage, 0.0, msg, drops=self._drops if self._drops >= DROPS_TO_BLAME_CABLE else 0,
                          code=code, args=args)

  def _note_link_loss(self, why: str) -> None:
    """Off the frame thread: what the comma's USB-C port sees now, next to the
    failure (gadget.cc_orientation). A host still on the cable means the data
    link alone went; the kernel logs the same edge as a Type-C disconnect."""
    cc = gadget.cc_orientation()
    if cc is None:
      port = "port state unknown"
    else:
      port = f"port sees a host (cc {cc})" if cc else "port sees no host (cc 0)"
    # read now, before the teardown: a host back by the time the backoff starts
    # has still been replugged
    self._host_left = not gadget.host_attached()
    self._log.warning("jetlink: %s, %s; drop %d, lag %d this drive", why, port, self._drops, self._lags)
    msg, code = RECONNECTING[why]
    self._report('connect', msg, 'reconnecting', {'why': code})

  def _join_loop(self) -> None:
    """Open the link and get the engine ready. No tinygrad in here."""
    # off modeld's realtime core first. A thread started after
    # config_realtime_process(7, 54) inherits SCHED_FIFO and the single-core
    # affinity, and an equal-priority thread that wakes takes the core until
    # it blocks. Measured with these threads left as created: exec p95
    # 90.8 ms, max 159.9, 5% frame drops, enough for modeldLagging
    background_thread()
    while not self._stop.is_set():
      # no timeout: once joined there is nothing to poll for, the frames find
      # a dead link, and close() sets this. An idle wake per second is not
      # free on modeld's core
      self._rejoin.wait()
      if self._announce:
        # the large model's first frame is out. Before the retired one below,
        # whose reconnecting line is newer; woken for this alone, wait again.
        # A demote or close sets its own flag before the event, so clearing
        # the event and then looking misses neither
        self._announce = False
        self._progress.clear()
        self._rejoin.clear()
        if self._retired is None and not self._stop.is_set():
          continue
      with self._lock:
        retired, self._retired = self._retired, None
      if retired is not None:
        model, why = retired
        if not self._stop.is_set():
          # before the teardown below, which can block: the port is read about
          # when it let go. Not after close(), which has cleared the progress
          self._note_link_loss(why)
        # unbind and reader joins can block; only this thread does teardown,
        # and it finishes before opening another link
        self._close_retired(model, why)
      if self._stop.is_set():
        return
      self._rejoin.clear()
      if self._wait_out_back_off():
        return
      self._report('connect', 'waiting for jetlink', 'waiting_for_host')
      try:
        # the connect can take minutes when the picked model still has to be
        # built, so it is handed the flag close() sets rather than polled
        client, spec = self._connect(self._stop.is_set)
      except Exception as e:
        # expected while the Jetson boots, so no stack trace. A failure that
        # knows when it is worth asking again says so (link.ModelMissing), and
        # what the panel should show meanwhile
        delay = getattr(e, 'retry_after', REJOIN_DELAY)
        if waiting := getattr(e, 'waiting', None):
          self._report('connect', waiting, getattr(e, 'code', None))
        self._log.warning("jetlink: not joined yet (%s), retrying in %.0fs", e, delay)
        if self._stop.wait(delay):
          return
        self._rejoin.set()
        continue
      with self._lock:
        if self._stop.is_set():
          client.close()
          return
        self._available = True
        self._joined = (client, spec)
      self._log.warning("jetlink: link ready, waiting for a window to swap")
      self._report('connect', READY, 'ready_waiting_window')
      self._keep_alive()

  def _wait_out_back_off(self) -> bool:
    """Until the next attempt is due, or a host configures the gadget again
    after it went away. Once per failure streak: a host that flaps, a marginal
    cable, would otherwise cycle the swap and its alerts with no backoff at
    all. True once closed."""
    host_left, self._host_left = self._host_left or not gadget.host_attached(), False
    while (left := self._rejoin_at - time.monotonic()) > 0:
      if self._stop.wait(min(left, REPLUG_POLL)):
        return True
      attached = gadget.host_attached()
      if attached and host_left and not self._replugged:
        self._replugged = True
        self._log.warning("jetlink: the host configured the gadget again, retrying now")
        return False
      host_left = host_left or not attached
    return self._stop.is_set()

  def _keep_alive(self) -> None:
    """Ping a link that is waiting for a swap window.

    On a drive with no stop and no disengage that is the whole drive, and a
    host that reboots in there would otherwise be found at the swap: a build
    on a dead link, a demote and the backoff, all on modeld's thread.

    The client is taken out of _joined for the ping and put back after, so the
    frame loop sees a whole one or none, and never waits on the lock.
    """
    while not self._stop.is_set():
      if self._rejoin.wait(KEEPALIVE_PERIOD):
        return
      with self._lock:
        joined, self._joined = self._joined, None
      if joined is None:
        return  # swapped in on a frame, or closed under us
      try:
        joined[0].ping(timeout=PING_TIMEOUT)
      except Exception as e:
        self._available = False
        self._log.warning("jetlink: the link died before it could be used (%s), reopening", e)
        self._note_link_loss(LOST)
        try:
          joined[0].close()
        except Exception:
          self._log.exception('jetlink: closing the dead link')
        self._back_off()
        return
      with self._lock:
        stopped = self._stop.is_set()
        if not stopped:
          self._joined = joined
      if stopped:
        # close() ran during the ping and found nothing to close
        self._say_leaving(joined[0], P.LEAVE_STOPPED)
        joined[0].close()
        return

  def _write_loop(self) -> None:
    """The runtime record (gadget.MODELD), once a second, on a hand-back and on
    the first answer after a swap: tmpfs IO, the link record and a catalog
    read for the name, so never on the frame thread, and off modeld's realtime
    core first (_join_loop). The last one, after close(), is stopping.

    status.runtime reads pid, at, stopping, state, active and last_output_at;
    handovers, held, sha256, name, checkpoint, transport, drops and lags have
    no reader in jetlink yet, they are for forks (StarPilot's sha and
    checkpoint, a drive's counts)."""
    background_thread()
    pid, names, kinds = os.getpid(), {}, {}
    failing = False
    while True:
      # before the record is built: a wake from here on is for the next one
      self._record.clear()
      stopping = self._stop.is_set()
      try:
        gadget.write_record(self._record_path, self._runtime(pid, names, kinds, stopping))
        failing = False
      except Exception:
        if not failing:   # once, not every second
          self._log.exception("jetlink: could not write the runtime record")
        failing = True
      if stopping:
        return
      self._record.wait(gadget.RECORD_PERIOD)

  def _runtime(self, pid: int, names: dict, kinds: dict, stopping: bool) -> dict:
    big = self._big
    state = self.big_model_state
    swapped_at = self._joined_at   # after the state: a swap sets it before _active
    if state == 'running' and self._output_at < swapped_at:
      # swapped in and not answered yet: the swap is still landing, not a
      # model that stopped answering
      state = 'joining'
    spec = getattr(big, 'spec', None)
    sha = getattr(spec, 'sha256', None)
    if big is not None and swapped_at not in kinds:
      # once per swap: the gadget the link runs on does not change under it
      kinds.clear()
      kinds[swapped_at] = gadget.link_kind()
    return {'pid': pid, 'at': time.monotonic(), 'stopping': stopping, 'state': state,
            'handovers': self._handovers, 'active': big is not None and self._active is big,
            'last_output_at': self._output_at, 'held': bool(getattr(big, 'held', False)),
            'sha256': sha, 'name': self._name_of(sha, names) if sha else None,
            'checkpoint': getattr(spec, 'checkpoint', None),
            'transport': kinds.get(swapped_at) if big is not None else None, 'drops': self._drops,
            'lags': self._lags}

  def _name_of(self, sha: str, names: dict) -> str:
    """The index's name, kept once it is one. The sha's stand-in (a model the
    comma's index does not list yet, or a lookup that failed) is asked again
    after NAME_RETRY."""
    name, retry_at = names.get(sha, (None, 0.0))
    if time.monotonic() >= retry_at:
      try:
        name = self._name(sha) if self._name is not None else None
      except Exception:
        name = None
      real = bool(name) and name != sha[:16]
      name = name if real else sha[:16]
      names[sha] = (name, float('inf') if real else time.monotonic() + NAME_RETRY)
    return name

  def close(self) -> None:
    first = not self._stop.is_set()   # the leave is said once; cleanups close twice
    self._stop.set()
    self._available = False
    self._rejoin.set()
    # after the stop: the writer's next record is its last, and says stopping
    self._record.set()
    self._writer.join(1.0)
    self._progress.clear()
    with self._lock:
      joined, self._joined = self._joined, None
    if joined is not None:
      if first:
        self._say_leaving(joined[0], P.LEAVE_STOPPED)
      joined[0].close()
    big, self._big = self._big, None
    close = getattr(big, 'close', None)
    if close is not None:
      if first:
        self._leave(big, P.LEAVE_STOPPED)
      close()


def join(parts, cam_w: int, cam_h: int, small) -> JoiningModelState:
  """The model modeld runs: the small model now, the Jetson once it is there.
  Returns straight away; the join runs in the background.

  The warp is loaded and warmed here, before the frame loop exists, rather
  than at the swap on a driving frame. Sized from the recorded spec, which is
  what the link will hand back; another geometry is rejected.
  """
  from jetlink.openpilot import link as links
  op = parts.op
  face = op.model_face()
  ready: dict = {}
  link = links.Link(parts.log)

  def prepare():
    from jetlink.openpilot.interface import FrameWarp, conformance
    from jetlink.openpilot.warp import Warp, prepare_reset
    # the gadget first, so the Jetson enumerates while the warp loads. Left to
    # the join thread the bind lands ~3 s later, behind the small model's
    # first frame, and can stall a frame by hundreds of ms
    links.present_early(link, background_thread)
    cached = parts.spec.load()
    if cached is not None:
      img_h, img_w = cached.model_hw
      geometry = (img_w * 2, img_h * 2)
    else:
      geometry = parts.warps.geometry()[2:]
    try:
      # the fork's own reset of its small model, else jetlink's from its shape
      ready['reset_small'] = getattr(op, 'small_reset', lambda small: None)(small) or prepare_reset(small)
      # the fork's own warp, else openpilot's driving warp pickle
      warp = getattr(op, 'model_warp', lambda *args: None)(small, cam_w, cam_h, *geometry)
      if warp is None:
        warp = Warp(parts.warps.load(cam_w, cam_h, *geometry), face.frame_size(cam_w, cam_h))
      elif problems := conformance(warp, FrameWarp):
        raise RuntimeError(f"the adapter's model_warp is not a warp: {', '.join(problems)}")
    except Exception:
      link.close()
      raise
    ready.update(warp=warp, geometry=geometry)

  def build(client, spec):
    img_h, img_w = spec.model_hw
    warp = ready.get('warp') if ready.get('geometry') == (img_w * 2, img_h * 2) else None
    if warp is None:
      raise RuntimeError('no prepared warp for the server model geometry')
    return JetlinkModelState(client, spec, warp, face=face, log=parts.log, event=op.event)

  def connect(should_stop=None):
    return links.open_link(parts, link, should_stop)

  return JoiningModelState(small, connect, build, prepare, reset_small=lambda: ready['reset_small'](),
                           progress=parts.progress, log=parts.log, in_control=op.in_control,
                           name=parts.models.name_for)
