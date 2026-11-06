"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

Opening the link over what the owner lends, and making the Jetson ready to
run the model that is picked.

Both borrowers of the link do this. A provisioning run does it offroad, where
it can spend minutes downloading a gigabyte; modeld's join thread does it
onroad, where it cannot download but can upload a file the comma already has
and then wait out the build with the small model driving. What they share is
here: the lease and the client on it, the model's identity, the upload nobody
may make without proving the hash first, and the record of what the server
ended up with.
"""
from __future__ import annotations

import functools
import threading
import time
from pathlib import Path

from jetlink.comma import gadget

# how long one attempt holds the gadget open waiting for a host. Not a deadline
# on the large model: JoiningModelState retries for the drive, since the Jetson
# boots after the comma is already onroad
CONNECT_TIMEOUT = 45.0
CONNECT_DELAY = 0.5
# four frame periods, and four times the slowest frame seen. A host that goes
# silent with no USB edge (a hung server, a phone's cable) costs one frame this
# long plus the small model's, inside modelV2's 0.5 s alive limit; at 0.5 s
# here commIssue flashes on top of the fallback
INFERENCE_TIMEOUT = 0.2
# between joins while neither end has the picked model and nothing else is built
MODEL_WAIT = 60.0
# how long the load may wait for the early gadget bind; a provisioning run may
# still be letting go of the endpoints
PRESENT_TIMEOUT = 5.0
# An upload and a build on a busy box. The build itself is 102 to 294 s on this
# hardware; the ceiling is only there so a server that has stopped answering
# does not hold the caller for the rest of the day.
BUILD_TIMEOUT = 1800.0
# and on top of that, a server downloading the model itself: 1.75 GB at 1 MB/s
FETCH_TIMEOUT = 1800.0


class ModelMissing(RuntimeError):
  """Neither end has the picked model and nothing else is built: only a
  provisioning run's download changes that, so the join asks again after
  `retry_after` rather than every few seconds, and the panel says `waiting`
  (joining._join_loop)."""
  retry_after = MODEL_WAIT
  waiting = 'big model not downloaded yet'
  code = 'model_missing'   # waiting's progress code (status.PROGRESS_CODES)


def _left(deadline: float | None, default: float) -> float:
  """What is left of an absolute `deadline`, or `default` without one."""
  return default if deadline is None else max(0.0, deadline - time.monotonic())


def connect(log, loan, deadline: float | None = None, name: str | None = None, wait: float | None = None):
  """Open the link over what jetlinkd lent (jetlink.comma.lending): the
  endpoint files, or on the cable the phone's next dial, which the loan
  listens for (Loan.accept) for up to `wait` s, BORROW_TIMEOUT by default.
  `deadline` is per frame, FRAME_TIMEOUT by default: modeld blocks on a frame
  the way it blocks on a chestnut. `name` is what the server logs this
  connection as: two comma processes share one gadget, and the Jetson's
  journal has no clock to tell them apart by.
  """
  from jetlink.client import FRAME_TIMEOUT, JetlinkClient
  from jetlink.comma import lending
  deadline = FRAME_TIMEOUT if deadline is None else deadline
  if loan.cable:
    sock = loan.accept(lending.BORROW_TIMEOUT if wait is None else wait)
    peer = sock.getpeername()[0]
    # the panels name the phone; the owner records it only while it holds the dial itself
    gadget.note_link('cable', peer)
    log.warning("jetlink: the phone dialed in from %s", peer)
    return JetlinkClient.open_socket(sock, deadline=deadline, name=name)
  return JetlinkClient.open_borrowed_ffs(loan.mount, loan.udc, bounce=loan.bounce, deadline=deadline, name=name)


class Link:
  """modeld's end of the gadget: the lease, and the client that rides on it.

  Both are kept across join attempts. The lease lasts the drive, and opening
  the gadget again per attempt is an unplug as the Jetson sees it, which,
  while one boots and the join loop asks every few seconds, is an unplug a
  cycle. So an attempt that cannot use the link leaves it here rather than
  closing it, and only a deliberate close() lets go. On the cable the lease
  is the listener, and a client that died is replaced by the phone's next
  dial on it.
  """

  def __init__(self, log, name: str = 'modeld'):
    self.log = log
    self.name = name
    self.client = None
    self.loan = None
    self._lock = threading.Lock()
    self._abandoned = False

  def open(self, deadline: float | None = None):
    """The client, opening one if we have not got one yet, or if the one held
    is dead: a big model retired after a link loss closes its client, and
    reusing it fails the next attempt with EBADF."""
    if self.client is not None and self.client.dead:
      self.close()
    if self.client is None:
      from jetlink.comma import lending
      self.client = connect(self.log, name=self.name, loan=self._borrow(deadline),
                            wait=_left(deadline, lending.BORROW_TIMEOUT))
    return self.client

  def adopt(self, client) -> bool:
    """Take a client another thread opened. False once we have given up waiting
    for it, and then the caller closes what it opened."""
    with self._lock:
      if self._abandoned:
        return False
      self.client = client
      return True

  def abandon(self) -> None:
    with self._lock:
      self._abandoned = True

  def note_server(self, hello: dict) -> None:
    """Pass the server's hello on to the owner over the lease, so every
    drive's join refreshes what it knows of the far end (Loan.note_server).
    Raises when that let the lease go: a client whose bounce goes over a
    closed loan cannot free a stuck write, so the attempt starts over."""
    if self.loan is not None and not self.loan.note_server(hello) and self.loan.closed:
      from jetlink.transport.base import LinkError
      raise LinkError("the owner stopped answering on the loan")

  def close(self) -> None:
    """Let the link go. The lease stays: jetlinkd should hold the gadget for
    the whole drive, however many times the join has to start over."""
    client, self.client = self.client, None
    if client is not None:
      try:
        client.close()
      except Exception:
        self.log.exception("jetlink: error closing the link")

  def _borrow(self, deadline: float | None):
    """The lease on the gadget jetlinkd owns. Raises without one: only the
    owner ever holds ep0, so the join loop asks again with the small model
    driving. An owner whose lender cannot listen says so in the offroad alert."""
    from jetlink.comma import lending
    if self.loan is not None and not self.loan.closed:
      return self.loan
    # bounded by whatever the caller has left: an early present that spends its
    # whole budget here has nothing left to open the link with
    self.loan = lending.borrow(self.name, timeout=_left(deadline, lending.BORROW_TIMEOUT))
    if self.loan is None:
      raise TimeoutError("jetlinkd lent no link")
    return self.loan


def present_early(link: Link, background) -> None:
  """Take the link now, from a thread that is not modeld's.

  modeld's main thread is already SCHED_FIFO 54 on core 7, and the FunctionFS
  reader the open creates would inherit that and preempt the frame loop, so the
  helper first calls `background` to get off it. Bounded, so a hung open
  cannot hold modeld's load; a helper that finishes late closes what it opened.
  """
  deadline = time.monotonic() + PRESENT_TIMEOUT

  def present():
    background()
    # retried: a provisioning run may still have the endpoints open, and a
    # single try fails in milliseconds
    while True:
      try:
        client = link.open(deadline)
        break
      except Exception as e:
        if time.monotonic() >= deadline:
          link.log.warning("jetlink: could not present the gadget early (%s), the join will", e)
          return
        time.sleep(0.2)
    if not link.adopt(client):
      client.close()

  t = threading.Thread(target=present, name='jetlink-present', daemon=True)
  t.start()
  t.join(max(0.0, deadline - time.monotonic()) + 0.5)
  if t.is_alive():
    link.abandon()
    link.log.warning("jetlink: presenting the gadget took over %.0f s, the join will", PRESENT_TIMEOUT)


def connect_patiently(link: Link):
  """Open the link, tolerating a busy gadget or a Jetson that is still booting."""
  deadline = time.monotonic() + CONNECT_TIMEOUT
  last = None
  while True:
    try:
      client = link.open()
    except Exception as e:
      client, last = None, e
    if client is not None:
      # over a phone's cable wait_for_host returns at once and a TCP client's
      # rebind is a no-op, so its network interface is never bounced
      if gadget.wait_for_host(max(0.0, deadline - time.monotonic()), bounce=client.rebind,
                              report=lambda: link.log.warning("jetlink: gadget up, waiting for the jetson to enumerate")):
        return client
      # the link stays on `link`, still bound, for the next attempt
      raise TimeoutError(f"no jetson attached within {CONNECT_TIMEOUT:.0f}s")
    if time.monotonic() > deadline:
      # nothing is held here: this is a gadget we could not open at all,
      # usually a provisioning run still finishing an exchange on the endpoints
      raise last if last is not None else TimeoutError("could not open the link")
    link.log.warning("jetlink: link not ready (%s), retrying", last)
    time.sleep(CONNECT_DELAY)


# -- making the Jetson ready ------------------------------------------------------

def identity(parts, entry: dict) -> tuple[str, int]:
  """The picked model's sha256 and byte count, from the model index, so the
  comma can name the model without holding or hashing the ONNX. A row has
  them already where the comma's copy of the index knows the model; one it
  does not fetches the index again, the one part of provisioning that may
  need the internet."""
  sha256, nbytes = entry.get('oid'), entry.get('size')
  if sha256 and nbytes:
    return sha256, int(nbytes)
  parts.progress.report('connect', 0.0, 'finding model', code='finding_model')
  return parts.models.resolve(entry.get('ref'), sha256)


# What has already been hashed in this process, keyed on the file as it was
# then. A join that fails and tries again would otherwise read a gigabyte off
# the disk every time, on a thread that is now doing it next to a running frame
# loop.
_hashed: dict[tuple[str, int, int], str] = {}


def _hash_key(path: Path, st) -> tuple[str, int, int]:
  return str(path), st.st_size, st.st_mtime_ns


def remember_hash(path: Path, sha256: str) -> None:
  """A file whose hash is already proven, as a download's is on the way in:
  its upload need not read the gigabyte again (verified_upload)."""
  _hashed[_hash_key(path, path.stat())] = sha256


def verified_upload(log, model_path: Path | None, sha256: str, nbytes: int) -> Path | None:
  """The file to upload, once its hash is proven to match the registry: under
  a sha the bytes do not have, the Jetson's plan would lie about its contents."""
  if model_path is None:
    return None
  try:
    st = model_path.stat()
    if st.st_size != nbytes:
      log.error("jetlink: %s is %d bytes, the registry says %d; not uploading it",
                model_path.name, st.st_size, nbytes)
      return None
    key = _hash_key(model_path, st)
    have = _hashed.get(key)
    if have is None:
      from jetlink.spec import sha256_file
      have, _ = sha256_file(str(model_path))
      _hashed[key] = have
    if have != sha256:
      log.error("jetlink: %s hashes to %s, the registry says %s; not uploading it",
                model_path.name, have[:16], sha256[:16])
      return None
  except OSError:
    return None
  return model_path


def ensure(parts, client, sha256: str, nbytes: int, model_path: Path | None, *,
           progress=None, should_stop=None, build_timeout: float = BUILD_TIMEOUT, ref: str | None = None,
           fetch: bool = True):
  """Make the server ready for this model and remember what it answered.
  Asks without the file first: the server answers from the sha alone when it
  has the model, which is every parked poll and every join. Without the file
  here either, the server is asked to download the model itself, which only
  the Jetson needs. EngineMissing means the Jetson has neither the plan nor
  the bytes and could not get them, and neither has the caller. `fetch` False
  never asks for the download: a stand-in is a model the server has. `ref`,
  the catalog's name for the model, goes with a download."""
  from jetlink.client import EngineMissing
  fetch = fetch and model_path is None
  ask = functools.partial(client.ensure_engine, sha256, nbytes, progress=progress, should_stop=should_stop)
  try:
    spec = ask(onnx_path=None, fetch=fetch, ref=ref if fetch else None,
               build_timeout=build_timeout + (FETCH_TIMEOUT if fetch else 0.0))
  except EngineMissing:
    upload = verified_upload(parts.log, model_path, sha256, nbytes)
    if upload is None:
      raise
    spec = ask(onnx_path=upload, build_timeout=build_timeout)
  parts.spec.store(spec)
  return spec


def stand_in(parts, hello: dict, pick: str, built) -> tuple[str, int] | None:
  """The model to ask for in place of the pick, as (sha256, nbytes), or None
  for the pick itself. `built` is the spec record's ready spec, or None.

  The server's hello says what it has: the model it has loaded and the ones it
  has built. The pick wins whenever the server has it. Otherwise the model it
  has loaded drives, so a phone set to its own model serves that rather than
  whatever the comma last built on some other server; then the comma's own
  record of the last model built, if this server has it too. A server that lists nothing has only the record
  to go on."""
  cached = hello.get('cached_models')
  have = None
  if isinstance(cached, list):
    loaded = hello.get('loaded')
    have = set(cached) | ({loaded} if loaded else set())
    if pick in have:
      return None
    if loaded:
      # its size names it to the server: the record's, else the index's
      nbytes = built.nbytes if built is not None and built.sha256 == loaded else parts.models.size_for(loaded)
      if nbytes:
        return loaded, nbytes
  if built is not None and built.sha256 != pick and (have is None or built.sha256 in have):
    return built.sha256, built.nbytes
  return None


def open_link(parts, link: Link, should_stop=None):
  """Get a client and a spec. Link IO only, so it is safe off modeld's thread;
  everything that touches tinygrad stays in the joining state's build.

  The picked model is built here if the Jetson has not got it, minutes with
  the small model driving: a provisioning run works offroad only, so a model
  picked in the driveway would otherwise cost the whole drive. Only reached with
  the small model driving (the join loop stops asking once joined), so a build
  here never unloads an engine that is steering. Whatever this attempt cannot
  use stays on `link`, still open, for the next one.
  """
  from jetlink.client import EngineMissing

  selected = parts.models.selected_model()
  if selected is None:
    raise RuntimeError('no large model has been picked yet')

  # the endpoints may still be held by a provisioning run, and the Jetson may still be
  # booting; both resolve on their own
  client = connect_patiently(link)
  try:
    sent = parts.models.hello_pick()
    hello = parts.models.heard(client.hello(timeout=10.0, pick=sent), sent)
    parts.log.warning("jetlink: %s trt %s, engine %s, loaded %s",
                   hello.get('device'), hello.get('trt_version'),
                   hello.get('engine_state'), str(hello.get('loaded'))[:16])
    link.note_server(hello)
    # a newer pick on the server drives, and is ours from now on
    parts.models.adopt()
    selected = parts.models.selected_model() or selected
    sha256, nbytes = identity(parts, selected)
    path = parts.models.shipped_model_path()
    built = parts.spec.ready_spec()
    standin = stand_in(parts, hello, sha256, built)
    if standin is not None:
      # by design, a model already built drives until the
      # pick is fetched and built, which a provisioning run does parked, rather
      # than the small model. Building here would unload it, and uploading
      # would share the link with its frames
      parts.log.warning("jetlink: %s is not ready yet, %s drives until it is", selected.get('name', sha256[:16]),
                        parts.models.name_for(standin[0]))
      (sha256, nbytes), path = standin, None
    elif built is None or built.sha256 != sha256:
      parts.log.warning("jetlink: %s is not built yet, building it with the small model driving",
                        selected.get('name', sha256[:16]))
    # normally one round trip, since the provisioning run left the engine loaded. A
    # server that restarted reloads from the plan cache, 13 to 25 s; one
    # that has never seen this model builds it, 102 to 294 s
    spec = ensure(parts, client, sha256, nbytes, path, progress=parts.progress.report_with_eta,
                  should_stop=should_stop, ref=selected.get('ref'), fetch=standin is None)
    client.deadline = INFERENCE_TIMEOUT
    return client, spec
  except EngineMissing as e:
    # neither end has the bytes. Fetching them is a provisioning run's job;
    # clear the record if it named this model, so the next one does. The link
    # is fine and stays on `link`: closing it leaves a read queued that the
    # next attempt's open has to bounce the gadget for
    parts.spec.clear_ready(sha256)
    raise ModelMissing(str(e)) from e
  except BaseException:
    link.close()
    raise
