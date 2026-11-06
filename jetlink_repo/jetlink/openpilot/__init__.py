"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

jetlink on an openpilot device: everything between the fork and the comma's
device layer (jetlink.comma).

The fork implements Openpilot (interface.py) once, in an adapter module that
holds every openpilot import, binds it here (bind) and calls the methods of
the Jetlink that comes back. That is the whole contract between the two repos:
the names in __all__, the Jetlink methods and Status, which
tests/openpilot/test_api.py and test_interface.py pin, and the facade the
fork's hook lines call them through (hooks.py) and the kit its CI runs
(testing.py). API names its version: an adapter declares the one it was
written to, hooks.Hooks refuses one it does not accept, and the fork treats
that as jetlink being absent, an offroad alert and no link rather than a
crash. Everything else in this package is internal to jetlink and may change
at any time.

API changes only for a breaking change to the contract: removing or renaming
anything in it, changing what one of its members means, or making an optional
member required. A new Status field, a new Jetlink method, or a new interface
member that jetlink reads with getattr and a fallback, is additive and keeps
it. A bump keeps the previous API working for one minor release, so a fork can
move its pin and then its adapter. Each release that changes the contract
says how in its CHANGELOG section (Forks, docs/publishing.md).

Imported by the resident gadget owner, so nothing heavy at module level: the
parts a heavy process uses are imported when it first uses them.
"""
from __future__ import annotations

import time

from jetlink.comma import gadget
from jetlink.openpilot.events import Flags, SwapEvents
from jetlink.openpilot.interface import MODES, STATES, Keys, ModelFace, Openpilot, OwnerConfig, conformance
from jetlink.openpilot.parts import Parts, for_this_process
from jetlink.openpilot.status import Status

API = 4

__all__ = ['API', 'MODES', 'STATES', 'Jetlink', 'Keys', 'ModelFace', 'Openpilot', 'OwnerConfig', 'Status', 'bind',
           'conformance', 'SHUTDOWN_TIMEOUT', 'Flags', 'SwapEvents']

# how long a power-off request waits for the owner's run to shut the Jetson
# down before it is withdrawn. Wake from suspend is ~8 s to a server
SHUTDOWN_TIMEOUT = 25.0


def bind(op) -> Jetlink:
  """jetlink for this process, over the fork's adapter. One per process: it
  keeps the process's caches (parts.for_this_process)."""
  return Jetlink(for_this_process(op))


class Jetlink:
  """What the fork calls: one per process, from bind(). Its public methods are
  the API; the parts behind it are not."""

  def __init__(self, parts: Parts):
    self._parts = parts
    self._log = parts.log
    # prepare() said yes in this process, so attach() may join modeld
    self._prepared = False
    # what -> the last failure logged reading it; cleared by a read that works
    self._failures: dict[str, str] = {}
    # when this process asked for the Jetson to power off, until it was taken
    self._shutdown_asked: float | None = None
    # shutdown_step: has it run in this process, and has it said yes
    self._stepped = False
    self._may_power_off = False
    # the joining model's handovers as handed_over() last saw them
    self._handovers = 0

  def _read(self, what: str, read, fallback):
    """read(), or fallback(error) when it raises. The readers never raise:
    they are asked several times a second, from threads that have nothing to
    do with jetlink. A failure is logged once per distinct error, and again
    after a read that worked."""
    try:
      value = read()
    except Exception as e:
      error = f"{type(e).__name__}: {e}"
      if self._failures.get(what) != error:
        self._failures[what] = error
        self._log.exception("jetlink: could not read %s", what)
      return fallback(error)
    self._failures.pop(what, None)
    return value

  def _mode(self) -> str:
    """The link setting, or 'off' when it cannot be read."""
    return self._read('the link setting', lambda: self._parts.settings.mode(), lambda error: 'off')

  def enabled(self) -> bool:
    """Has the user turned the link on, with no chestnut fitted? The setting,
    never link state or readiness. A chestnut runs the big model natively and
    the link stays off beside it, so jetlinkd never takes the USB controller
    from it. manager's should_run for jetlinkd, on every device."""
    mode = self._mode()
    return self._read('whether the link is on', lambda: self._parts.enabled(mode), lambda error: False)

  def status(self) -> Status:
    """One snapshot for the UI and the panels."""
    from jetlink.openpilot import status
    mode = self._mode()
    return self._read('the status', lambda: status.read(self._parts, mode), lambda error: status.failed(error, mode))

  def runtime(self):
    """What modeld runs (status.Runtime), the part of status() that changes
    while driving, for a loop: the setting, the chestnut test and modeld's
    record, nothing else. The chestnut test may walk the USB devices in sysfs,
    once per parts.CHESTNUT_TTL (2 s), as status() does. None where
    status().running is None for want of the link: off, or beside a chestnut."""
    from jetlink.openpilot import status
    return status.runtime() if self.enabled() else None

  def model_state(self, ref: str) -> str | None:
    """One catalog model for the picker's list: 'ready' (built on the Jetson),
    'downloaded' (on the comma) or None. Asked when a list opens."""
    from jetlink.openpilot import status
    return self._read('a model\'s state', lambda: status.model_state(self._parts, ref), lambda error: None)

  def reason(self) -> str | None:
    """Why the link the user asked for cannot run: hardwared's offroad alert.
    None with the link off. The files the gadget and the build leave, nothing
    else: hardwared asks twice a second on every device."""
    from jetlink.openpilot import status
    mode = self._mode()
    return self._read('why the link cannot run', lambda: status.reason(self._parts, mode),
                      lambda error: status.failure(error, mode))

  def prepare(self) -> bool:
    """Will the link join this modeld? modeld only, before it goes realtime:
    enabled(), then the process-wide setup that has to happen before then,
    which is also a last veto."""
    from jetlink.openpilot.status import NO_WARP
    from jetlink.openpilot.warp import init_device
    self._prepared = False
    if not self.enabled():
      return False
    # the link is not worth waiting for: attach() joins in the background.
    # enabled() is the setting alone, so this is where a device that cannot
    # present a gadget at all says so; nothing here would ever reach a Jetson
    if not gadget.link_configured():
      self._log.warning("jetlink: no usable gadget (%s), staying on the small model",
                        gadget.gadget_error() or 'not set up')
      return False
    # the warp is a build product and nothing compiles one at runtime, so one
    # missing now stays missing, and saying no keeps modeld on the plain small
    # model; the offroad alert has said why
    if not self._parts.warps.built():
      self._log.warning("jetlink: %s, staying on the small model", NO_WARP)
      return False
    # the last hook before modeld goes SCHED_FIFO on core 7, and the GPU's init
    # spawns a thread that would inherit that. See warp.init_device
    init_device(self._log)
    self._prepared = True
    return True

  def attach(self, small, cam_w: int, cam_h: int):
    """Join the link to modeld, once the camera is up and `small` is built:
    the joining model, which drives as `small` until the Jetson is there.
    Before every run() it asks the adapter's in_control() (is openpilot or
    MADS in control? it swaps only while not), and modeld writes onto it
    `frame_drop_ratio` (its share of dropped camera frames, which hands a
    lagging large model back).

    None unless prepare() said yes in this process: without it the GPU's
    thread would start on modeld's realtime core. If the joining model cannot
    be built, `small`, and the failure is logged.
    """
    if not self._prepared:
      return None
    from jetlink.openpilot.joining import join
    try:
      return join(self._parts, cam_w, cam_h, small)
    except Exception:
      self._log.exception("jetlink load failed")
      return small

  def handed_over(self, model) -> bool:
    """modeld, after every run(): did it change which model drives? The
    joining model counts its swaps and hand-backs (JoinedModel.handovers),
    and the stall of one is not lag, nor are the drops of its frame. Any
    other model has no count and never hands over. Since the last call in
    this process, which runs one modeld loop."""
    handovers = getattr(model, 'handovers', 0)
    changed, self._handovers = handovers != self._handovers, handovers
    return changed

  def state(self, model) -> str:
    """What modeld publishes as the big model's state: the model's
    big_model_state as it is, one of STATES on a joining model, and 'none' for
    a model without one. A fork's capnp enum holds exactly STATES, so a value
    outside it would raise on the frame thread."""
    return getattr(model, 'big_model_state', 'none')

  def request_shutdown(self, reason: str = '') -> bool:
    """The device is powering off for good: ask for the Jetson to go down
    with it, and return at once. True when the request now waits for the
    owner, which shutdown_pending() follows; False when there was nothing to
    ask, with the link off, beside a chestnut, or with no Jetson known to be
    there. Never raises.

    hardwared calls this once and goes on publishing deviceState, putting
    DoShutdown once shutdown_pending() clears or SHUTDOWN_TIMEOUT has passed.
    The owner wakes a sleeping Jetson and starts a run that asks it; the
    wake and one round trip take ~10 s.
    """
    try:
      return self._ask_for_power_off(reason)
    except Exception:
      self._log.exception("jetlink: shutdown request failed")
      return False

  def shutdown_pending(self) -> bool:
    """Has the owner still to take the power-off request? A stat, for a
    caller's loop. Never raises.

    Once SHUTDOWN_TIMEOUT has passed since this process asked, the request is
    withdrawn and False: a comma that outlives its DoShutdown must not have
    the Jetson powered off later, when nobody wants it off, and the log says
    the request went unanswered."""
    try:
      return self._pending()
    except Exception:
      return False

  def shutdown_step(self, should_shutdown: bool, reason: str = '') -> bool:
    """hardwared, every loop: may the device power off now? True once it may
    (put DoShutdown), and from then on. False while should_shutdown is False
    and nothing has been asked. The first True asks once (request_shutdown):
    True at once when there was nothing to ask, otherwise once the owner has
    taken the request, or SHUTDOWN_TIMEOUT after asking, when it is withdrawn.
    Once asked, should_shutdown is not read again, and the caller keeps no
    timer. A stat a call. Never raises: a failure once a power-off is wanted
    is a yes, since a jetlink bug must not keep a comma on to drain the car's
    battery, and one before then is a no."""
    if self._may_power_off:
      return True
    try:
      first, self._stepped = not self._stepped, True
      # a request this process did not write: a partial one, or one from a
      # hardwared that has since restarted. Left, the owner would power the
      # Jetson off whenever it next saw one, maybe a drive later
      if first and self._shutdown_asked is None:
        gadget.finish_shutdown()
      if self._shutdown_asked is None and not should_shutdown:
        return False
      asked = self._shutdown_asked is not None or self.request_shutdown(reason)
      self._may_power_off = not asked or not self._pending()
    except Exception:
      self._log.exception("jetlink: the shutdown step failed")
      if self._shutdown_asked is not None:
        # nobody follows the request any more, so it must not stay for the owner
        try:
          gadget.finish_shutdown()
        except Exception:
          pass
      self._may_power_off = should_shutdown or self._shutdown_asked is not None
    return self._may_power_off

  def _pending(self) -> bool:
    """shutdown_pending(), raising."""
    pending = gadget.SHUTDOWN_REQUEST.exists()
    asked, now = self._shutdown_asked, time.monotonic()
    if asked is None:
      return pending
    if not pending:
      # the run removes it whether or not the Jetson answered; its log says which
      self._log.warning("jetlink: the owner took the shutdown request after %.1f s", now - asked)
    elif now - asked >= SHUTDOWN_TIMEOUT:
      self._log.warning("jetlink: nobody took the shutdown request within %.0f s", SHUTDOWN_TIMEOUT)
      gadget.finish_shutdown()
      pending = False
    if not pending:
      self._shutdown_asked = None
    return pending

  def _ask_for_power_off(self, reason: str) -> bool:
    """Hand the request to the owner: nothing else can touch the link, and
    the request is a file (gadget.SHUTDOWN_REQUEST) the owner's step looks
    for before anything else. Skipped when no Jetson is known to be there
    (dormant counts as there). A run busy in a long provision is stopped for
    it; shutdown_pending()'s deadline covers a Jetson that never answers."""
    from jetlink.openpilot.status import Presence
    if not self.enabled():
      return False
    # a presence of its own, off the files: one this process's readers keep
    # refreshed, or the owner's with its hold, would count a Jetson seen
    # seconds before the power-off, and the run would wait 20 s for a host
    # that has gone
    if not Presence().present():
      return False
    self._log.warning("jetlink: asking the jetson to power off: %s", reason)
    if not gadget.request_shutdown(reason):
      return False
    self._shutdown_asked = time.monotonic()
    return True

  def lists_big_models(self) -> bool:
    """Is the model manager's big-model list jetlink's (big_catalog) rather
    than sunnypilot's? With no chestnut fitted. Hardware, not the link
    setting: the model manager drops a pick its catalog does not list."""
    return not self._parts.chestnut_fitted()

  def big_catalog(self, selector: int) -> dict | None:
    """The model index as the model manager's big-model catalog, every bundle
    at `selector` (its REQUIRED_JSON_VERSION), fetched again
    (Models.big_catalog). None when it cannot be had, offline say, and the
    model manager keeps the list it has."""
    from jetlink.registry.index import RegistryError
    try:
      return self._parts.models.big_catalog(selector)
    except RegistryError as e:
      self._log.warning("jetlink: could not fetch the model index: %s", e)
      return None

  def listed_ref(self, ref: str) -> str | None:
    """The ref big_catalog() lists a pick's model under, for a pick a new list
    no longer names: a recompile is listed once, under its newest ref. None
    while the list is not jetlink's (lists_big_models)."""
    return self._parts.models.listed_ref(ref) if self.lists_big_models() else None

  # API 3's, until the next minor release: the catalog is left as fetched.
  # Its picks still drive, since the index knows every ref it ever saw
  def should_extend_catalog(self) -> bool:
    return False

  def extend_catalog(self, catalog: dict) -> dict:
    return catalog
