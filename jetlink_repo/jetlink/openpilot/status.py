"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

What the UI, hardwared and manager are told, in one snapshot, and the
progress the panels show while something provisions or joins.

Every answer here is files and params, no link IO: the UI asks five times a
second. The fork maps the snapshot onto its own widgets; the one mapping that
is jetlink's knowledge, which icon a state is, is Status.icon.

Light at module level: the package imports it, and the owner imports the package.
"""
from __future__ import annotations

import time
from typing import NamedTuple

from jetlink.comma import gadget

# -- progress -----------------------------------------------------------------
# Written by the provisioning run and the joining state, read by the UI: a
# param (Keys.progress), because the writer is another process.

# Each report is a file write, and the UI reads it at 5 Hz. An upload reports
# once per 4 MB chunk, 440 of them for a 1.7 GB model, and onroad that is IO a
# recording would have to share the disk with.
PROGRESS_MIN_INTERVAL = 0.25

# what a progress record's `code` may be, for a fork that words the line
# itself; `msg` stays the English one. `args`, where a code has them:
# downloading, download and upload {got_mb, total_mb}, download_stalled
# {retry_s}, reconnecting {why: lost, behind, load_failed or bad_output},
# build {eta_s}. downloading is the comma fetching the model, download the
# server fetching it itself. From download on they are stage names, and the
# server's stages are its own:
# a server can add one, so a fork falls back to `msg` for a code it does not
# know. A server message with no stage has no code
PROGRESS_CODES = ('waiting_for_host', 'finding_model', 'model_missing', 'ready_waiting_window', 'reconnecting',
                  'connecting', 'downloading', 'download_stalled', 'download_failed', 'downloaded_waiting_host',
                  'engine_ready', 'failed', 'download', 'upload', 'patch', 'parse', 'convert', 'compile', 'build',
                  'load', 'warm')


def estimated_build_seconds(size: int) -> int:
  """Orin Nano Super, TensorRT 10.3: the 766 MB models built in 102 to 166 s,
  the 1.75 GB ones in 230 to 294 s."""
  return int(60 + 130 * size / 1e9)


def _eta(seconds: float) -> str:
  if seconds >= 90:
    return f"about {seconds / 60:.0f} min left"
  return f"about {max(seconds, 1):.0f}s left"


class Progress:
  def __init__(self, op, size=None):
    self.op = op
    # () -> the picked model's size in bytes, or None: what the build's estimate is made from
    self._size = size
    self._last = ('', 0.0)

  def read(self) -> dict | None:
    """{stage, frac, msg} while something provisions, else None. Never raises."""
    try:
      value = self.op.get(self.op.keys.progress)
    except Exception:
      return None
    return value if isinstance(value, dict) else None

  def report(self, stage: str, frac: float, msg: str = '', drops: int = 0, code: str | None = None,
             args: dict | None = None) -> None:
    """Never raises: called from except handlers. A fraction's ticks are held to
    4 Hz within a stage; a report with no fraction (a join's state) and the end
    of a stage always go through, so the panel's last word is never dropped. A
    join says 'waiting for jetlink' and 're-engage to switch' moments apart;
    holding the second left the first on the panel for the rest of the drive.
    `msg` is the panel's one short line; `drops`, once the link has dropped often
    enough to blame the cable, is the panel's to word; `code` and `args` say
    `msg` for a fork with its own words (PROGRESS_CODES)."""
    last_stage, last_at = self._last
    now = time.monotonic()
    if 0.0 < frac < 1.0 and stage == last_stage and now - last_at < PROGRESS_MIN_INTERVAL:
      return
    self._last = (stage, now)
    record = {'stage': stage, 'frac': round(frac, 4), 'msg': msg, 'drops': drops}
    if code is not None:
      record['code'] = code
    if args:
      record['args'] = args
    try:
      self.op.put(self.op.keys.progress, record)
    except Exception:
      self.op.log.exception("jetlink: could not report progress")

  def clear(self) -> None:
    try:
      self.op.remove(self.op.keys.progress)
    except Exception:
      self.op.log.exception("jetlink: could not clear progress")

  def report_with_eta(self, stage: str, frac: float, msg: str = '') -> None:
    """Progress, with how long the build still has to run, from the model's
    size. Only the build: the upload reports MB of MB and a connect has nothing
    to predict."""
    args = None
    size = self._size() if self._size is not None and stage in ('build', 'upload', 'download') else None
    if size and stage == 'build':
      left = estimated_build_seconds(size) * max(0.0, 1.0 - frac)
      msg, args = _eta(left), {'eta_s': round(left)}
    elif size:
      args = {'got_mb': int(frac * size) >> 20, 'total_mb': size >> 20}
    if stage == 'download' and args:
      # the server downloading the model itself: not the comma's own 'downloading'
      msg = f"jetson downloading {args['got_mb']} of {args['total_mb']} MB"
    self.report(stage, frac, msg, code=stage or None, args=args)


# -- what is on the other end -------------------------------------------------
# The owner (jetlink.comma.owner) writes all of this into its status record
# every step, presence and its hold included, so every reader agrees with it
# and with each other. Without a live record (no owner since boot, one that
# stopped cleanly, or one too old to write it) each reader reads the gadget's
# files itself, as below.

# the offroad alert for a link that is on while nobody holds the gadget: the
# owner's heartbeat stopped. manager starts it again, and its crash-loop
# backoff says so in its record's error while it waits
STOPPED = "service stopped"


def owner_record() -> tuple[dict | None, dict | None]:
  """The owner's status record, and the same record if it is live, else None.
  A stale record from an owner that was asked to stop is no record: manager
  SIGKILLed it in a slow teardown, and nothing is wrong."""
  record = gadget.owner_status()
  if record is None or gadget.owner_alive(record):
    return record, record
  return (None, None) if record.get('stopping') else (record, None)


class Presence:
  """Presence as a reader works it out from the files, for when the owner's
  record is not there to say."""

  def __init__(self):
    self._last_configured = 0.0

  def present(self) -> bool:
    """Is a host on the other end now: configured us, or did within
    PRESENCE_HOLD? A phone on the cable is a host on the gadget like any other."""
    if gadget.dormant():
      # no enumeration during suspend; the CC line still tells a sleeping host from an unplugged one
      return gadget.port_has_host()
    now = time.monotonic()
    if gadget.host_attached():
      self._last_configured = now
      return True
    return now - self._last_configured < gadget.PRESENCE_HOLD


def link_transport(mode: str | None = None, live: dict | None = None) -> str:
  """What carries the link, for the panels: a host on the vendor interface,
  or an iPhone dialed in over the network one. `live` is the owner's record;
  without it, `mode` stands in until the owner has said. Never raises."""
  try:
    if live is not None and live.get('link') in ('usb', 'cable'):
      kind, peer = live['link'], live.get('peer')
    else:
      kind, peer = gadget.link_state()
      kind = kind or gadget.link_kind(mode)
    if kind == 'cable' and not peer:
      # the owner records the phone while it holds the dial; a borrower that
      # took the dial itself noted it in the link record
      peer = gadget.link_state()[1]
    if kind == 'cable':
      return f"iOS over USB ({peer})" if peer else "iOS over USB"
  except Exception:
    pass
  return "USB"


def usb_port() -> str | None:
  """What the comma's USB-C port controller sees on the CC pin: 'empty', or
  'host' for a cable with something live behind it (it cannot say what). None
  where the kernel does not expose it, rather than claiming an empty port."""
  cc = gadget.cc_orientation()
  if cc is None:
    return None
  return 'host' if cc else 'empty'


# -- whether the link can run --------------------------------------------------

# the offroad alert's text for a device whose checkout has no warp for its camera
NO_WARP = "no warp built for this camera"
# how a failed snapshot starts its reason (the owner's backoff: gadget.BACKING_OFF)
STATUS_FAILED = "jetlink status failed"
# Status.reason_code: the reason's kind, for a fork with its own words. Any
# reason that is none of the others is the gadget's, its text the detail
REASON_CODES = ('no_warp', 'service_stopped', 'backing_off', 'status_failed', 'gadget')


def unavailable(parts, record: dict | None) -> str | None:
  """Why an enabled link cannot run the large model, or None: files only.
  `record` is the owner's status record: its error when it is live, STOPPED
  when its heartbeat is not, and the gadget's own files without one."""
  if record is None:
    error = gadget.gadget_error()
  elif not gadget.owner_alive(record):
    return STOPPED
  else:
    error = record.get('error')
  if error:
    return str(error)
  return None if parts.warps.built() else NO_WARP


def reason(parts, mode: str) -> str | None:
  """Why the link cannot run, for someone who asked for it only: with it off,
  a device that cannot present the gadget simply does not offer the feature."""
  return unavailable(parts, owner_record()[0]) if parts.enabled(mode) else None


# -- what modeld runs ------------------------------------------------------------

# a big model drives while its last answer was this recent when the record
# was written: five frames at 20 Hz, so a held frame or two still counts
OUTPUT_FRESH = 0.25
# and while the record itself is this recent: a modeld that froze stops
# reading as driving within it. Two writer periods, since the writer runs at
# background priority and a late write must not read as a hand-back; below the
# owner's HEARTBEAT_TIMEOUT, which `running` keeps
ACTIVE_FRESH = 2 * gadget.RECORD_PERIOD


class Runtime(NamedTuple):
  state: str | None = None        # the joining model's state (STATES)
  active: bool = False            # the big model drives modeld's frames now
  sha256: str | None = None       # and which model it is, while it does
  checkpoint: str | None = None


def runtime() -> Runtime:
  """The joining model's state, whether the big model drives now and which
  model it is, from modeld's runtime record (gadget.MODELD); Runtime() without
  a live one: no modeld with the link since boot, or one that stopped, died
  or stopped writing (gadget.HEARTBEAT_TIMEOUT, as the owner's). Never raises."""
  record = gadget.read_record(gadget.MODELD)
  try:
    if record is None or record.get('stopping') or not gadget.owner_alive(record) \
       or not gadget.pid_alive(record.get('pid')):
      return Runtime()
    # the answer's age when written: the writer wakes once a second, the frames 20 times
    answered = record['at'] - record.get('last_output_at', 0.0) < OUTPUT_FRESH
    active = bool(record.get('active')) and answered and time.monotonic() - record['at'] < ACTIVE_FRESH

    def text(key: str, shown: bool = True) -> str | None:
      value = record.get(key)
      return value if shown and isinstance(value, str) else None
    return Runtime(text('state'), active, text('sha256', active), text('checkpoint', active))
  except (TypeError, KeyError):
    return Runtime()


# -- the snapshot ----------------------------------------------------------------

# Status.phase, in the order it is decided (see there). Plain strings, and
# like the codes only ever added to: a fork maps one it does not know to its
# own default
PHASES = ('unavailable', 'off', 'active', 'waiting', 'reconnecting', 'failed', 'preparing', 'connecting', 'prepared')


class Status(NamedTuple):
  """Everything a reader shows, taken at once. Fields are only ever added."""
  enabled: bool                 # the setting is on and no chestnut is fitted
  mode: str                     # the Jetlink setting, one of MODES
  transport: str                # 'USB', or 'iOS over USB (<peer>)'
  present: bool                 # a host is on the gadget now, or asleep and known to be there
  port: str | None              # 'host' or 'empty' off the CC pin; None where the kernel does not say
  ready: bool                   # the picked model's engine is built (records only, no link IO)
  reason: str | None            # why an enabled link cannot run: the offroad alert's text
  progress: dict | None         # {stage, frac, msg} while provisioning or joining
  model: str | None             # the big model it will run: the pick, else jetlink's default
  default_model: str | None     # jetlink's default, named like the chestnut's (no build date)
  standin: str | None = None    # the last model built, which drives while the pick is not ready
  running: str | None = None    # the joining model's state (STATES) in a live modeld, by its record (runtime)
  active: bool = False          # the big model drives modeld's frames now, by the same record
  running_sha256: str | None = None      # the model it drives with, while active
  running_checkpoint: str | None = None

  @property
  def runnable(self) -> bool:
    """Is a big model built to run: the pick, or the stand-in for it?"""
    return self.ready or self.standin is not None

  @property
  def active_model(self) -> str | None:
    """The big model that runs: the pick once it is built, else the stand-in."""
    return self.model if self.ready else self.standin

  @property
  def shown(self) -> bool:
    """Anything of the link's to show: it is on, a host is there, or progress."""
    return self.enabled or self.present or self.progress is not None

  @property
  def holds_port(self) -> bool:
    """The link holds the comma's USB port, so ADB cannot have it."""
    return self.enabled

  @property
  def port_state(self) -> str | None:
    """The port line's: 'connected' with a host on the link, else the CC
    pin's 'empty' or 'device' (a cable with something live behind it, it
    cannot say what); None where the kernel does not say."""
    if self.present:
      return 'connected'
    if self.port is None:
      return None
    return 'empty' if self.port == 'empty' else 'device'

  @property
  def reason_code(self) -> str | None:
    """`reason` as one of REASON_CODES, None without one."""
    reason = self.reason
    if reason is None:
      return None
    if reason == NO_WARP:
      return 'no_warp'
    if reason == STOPPED:
      return 'service_stopped'
    if reason.startswith(gadget.BACKING_OFF):
      return 'backing_off'
    return 'status_failed' if reason.startswith(STATUS_FAILED) else 'gadget'

  @property
  def phase(self) -> str:
    """One of PHASES, what a panel's state chip says, decided in that order.
    Why the link cannot run comes first (a failed snapshot has the reason
    with enabled False), then the setting, then what a live modeld says it
    runs, then the progress stage, then the records. Without a live record
    the progress line decides, and it can be a drive old: a modeld that is
    never closed leaves its join's last 'connect' line, which reads
    connecting offroad, as the icon reads loading. A big model that stopped
    answering while it drives is reconnecting: it is about to hand back. A
    build inside a join is preparing; a connect is connecting, a join's or a
    provisioning run's."""
    if self.reason is not None:
      return 'unavailable'
    if not self.enabled:
      return 'off'
    if self.active:
      return 'active'
    if self.running == 'ready':
      return 'waiting'
    if self.running in ('retrying', 'running'):
      return 'reconnecting'
    stage = str((self.progress or {}).get('stage') or '')
    if stage == 'failed':
      return 'failed'
    if stage not in ('', 'ready', 'connect'):
      return 'preparing'
    if self.running == 'joining' or stage == 'connect':
      return 'connecting'
    return 'prepared' if self.runnable else 'preparing'

  def icon(self, started: bool, model_seen: bool, running_big: bool, state: str) -> str:
    """The chestnut icon's state for the link: a ChestnutState value.

    Offroad it is progress and the records; onroad, modelV2 and the joining
    model's state (modelDataV2SP.acceleratorState). `model_seen` is a modelV2
    since this drive started, `running_big` a live one that says big, and
    `state` the acceleratorState name.
    """
    if not started:
      stage = str((self.progress or {}).get('stage', ''))
      if not self.present:
        return 'disconnected'
      if stage and stage != 'ready':
        return 'failed' if stage == 'failed' else 'loading'
      return 'ready' if self.runnable else 'uncompiled'

    if model_seen and running_big:
      return 'active'
    if not self.present:
      return 'disconnected'
    # attached, a pending join is loading, not a failed model
    if state in ('joining', 'retrying') or not model_seen:
      return 'loading'
    # the engine is up and only the swap window is missing: it opens when
    # nothing is in control, so a driver who stays engaged keeps the small model
    if state == 'ready':
      return 'waiting'
    if not self.runnable:
      return 'uncompiled'
    if state == 'running':
      return 'active'
    return 'failed'


def read(parts, mode: str) -> Status:
  """Everything at once, over the link setting the caller read: each file once."""
  record, live = owner_record()
  enabled = parts.enabled(mode)
  live_model = runtime() if enabled else Runtime()
  reason = unavailable(parts, record) if enabled else None
  # the last model the server built; the pick's, or the stand-in that drives
  # until the pick is built (link.open_link)
  built = parts.spec.built_sha() if enabled and reason is None else None
  selected = parts.models.selected_model() if built is not None else None
  ready = built is not None and selected is not None and built == selected['oid']
  return Status(
    enabled=enabled,
    mode=mode,
    transport=link_transport(mode, live),
    present=bool(live.get('present')) if live is not None else parts.presence.present(),
    port=usb_port(),
    ready=ready,
    reason=reason,
    progress=parts.progress.read(),
    model=parts.models.selected_model_name(),
    default_model=parts.models.default_model_name(),
    standin=parts.models.name_for(built) if built is not None and not ready else None,
    running=live_model.state,
    active=live_model.active,
    running_sha256=live_model.sha256,
    running_checkpoint=live_model.checkpoint,
  )


def model_state(parts, ref: str) -> str | None:
  """One listed model, for the picker's list: 'ready' when the Jetson has
  built it (the spec record), 'downloaded' when the comma has its file, else
  None. Records and one stat."""
  model = next((m for m in parts.models.model_index() if m['ref'] == ref), None)
  if model is None:
    return None
  if parts.spec.engine_ready_for(model['oid']):
    return 'ready'
  return 'downloaded' if parts.models.has_file(model) else None


def failed(error: str, mode: str) -> Status:
  """What a reader gets when the snapshot itself failed: nothing to show, the
  setting as it is, and, for someone who asked for the link, the failure where
  the offroad alert puts it. A device with the link off is never nagged."""
  return Status(enabled=False, mode=mode, transport='USB', present=False, port=None, ready=False,
                reason=failure(error, mode), progress=None, model=None, default_model=None)


def failure(error: str, mode: str) -> str | None:
  return f"{STATUS_FAILED}: {error}" if mode != 'off' else None
