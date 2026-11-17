"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

The comma side of the link.

Knows nothing about openpilot: warped frame and packed scalars in, model output
out. The glue lives in the fork (sunnypilot/jetlink/), so any fork can import
this.

Every error is a LinkError, which modeld already catches and answers with the
small model. A frame blocks until the answer arrives, as it does on a chestnut;
only a stall past FRAME_TIMEOUT is a failure.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import numpy as np

from jetlink import protocol as P
from jetlink.spec import CHUNK, DEFAULT_FRAME_SKIP, ModelSpec
from jetlink.transport.base import LinkError, LinkTimeout, Message, Transport


def _name(enum_cls, value) -> str:
  """Enum name for a value off the wire, which may be anything."""
  try:
    return enum_cls(value).name
  except ValueError:
    return f'unknown({value})'

log = logging.getLogger('jetlink.client')

ProgressFn = Callable[[str, float, str], None]

# Not a frame budget: a long frame is a dropped camera frame, which modeld
# tolerates as it does on a chestnut. This is chestnut's HCQDEV_WAIT_TIMEOUT_MS
# (3000) - past it the far end is gone, not slow.
FRAME_TIMEOUT = 3.0

StopFn = Callable[[], bool]

# openpilot logs the whole output vector as modelV2.rawPredictions when this is
# set, from what infer_end returns. The server keeps the hidden state, so ask for
# it on those runs, or the log would carry zeros where it goes.
RAW_PRED_ENV = 'SEND_RAW_PRED'


class Sent(NamedTuple):
  """A frame sent and not yet answered."""
  seq: int
  frame_id: int
  flags: int
  sent_at: float


class EngineMissing(LinkError):
  """The server has no engine and this caller has no ONNX to upload.

  Raised in modeld, which never carries the file: the Jetson's cache was pruned,
  re-flashed or swapped since a provisioning run recorded it ready.
  """


def _default_name() -> str:
  """Something the Jetson's journal can tell one comma process from another by."""
  return (Path(sys.argv[0]).stem if sys.argv else '') or 'python'


class JetlinkClient:
  def __init__(self, transport: Transport, deadline: float = FRAME_TIMEOUT,
               name: str | None = None, want_hidden: bool | None = None):
    self.t = transport
    self.deadline = deadline
    # Flag.WANT_HIDDEN on every frame; see RAW_PRED_ENV
    self.want_hidden = bool(os.environ.get(RAW_PRED_ENV)) if want_hidden is None else want_hidden
    # Who the server logs this connection as. The nonce is per client object,
    # so two processes taking turns on one gadget, or one process reopening
    # the link, are separable in a journal whose clock is wrong anyway.
    self.name = name or _default_name()
    self.nonce = secrets.token_hex(4)
    self.seq = 0
    self.spec: ModelSpec | None = None
    self.progress_cb: ProgressFn | None = None
    self._engine_state: dict | None = None
    self._should_stop: StopFn = lambda: False
    self.dead = False
    self.last_timings = (0, 0, 0)  # gpu_us, queue_us, total_us, server-side
    self.last_state: dict | None = None  # most recent piggybacked telemetry
    # the newest model output read off the link, whichever call read it: what
    # a frame whose own reply is late publishes again (JetlinkModelState.end)
    self.last_output: np.ndarray | None = None
    # frames sent and not yet answered, oldest first. More than one after a
    # reply a frame gave up on (infer_end with hold)
    self._in_flight: deque[Sent] = deque()
    # seqs sent with no reply wanted (leave): an ERROR to one is not this link's
    self._no_reply_wanted: set[int] = set()
    # frames the server never answered this session, for the log: a lost
    # datagram is one, and to the caller a held frame like any late reply
    self.frames_lost = 0
    # take a server's offer of frame datagrams (_take_datagrams); False keeps frames on the stream, for A/B runs
    self.allow_datagrams = True

  # -- construction ---------------------------------------------------------

  @classmethod
  def open_ffs(cls, mount: str = '/dev/ffs-jetlink', gadget: str | None = None,
               udc: str | None = None, **kw) -> JetlinkClient:
    """This end is the USB gadget (FunctionFS).

    The roles follow what the two kernels support, not who is the client: AGNOS
    has F_FS built in, and a host needs no driver at all. See docs/transport.md.
    """
    from jetlink.transport.ffs import FfsTransport
    return cls(FfsTransport(mount, gadget=gadget, udc=udc), **kw)

  @classmethod
  def open_borrowed_ffs(cls, mount: str, udc: str, bounce=None, owner_gadget: str | None = None,
                        **kw) -> JetlinkClient:
    """This end is the USB gadget, over a gadget another process owns.

    The owner holds ep0 and the UDC bind for as long as the link is enabled,
    so a drive starting or ending is not an unplug the host has to recover
    from. See FfsTransport.borrowed.
    """
    from jetlink.transport.ffs import FfsTransport
    return cls(FfsTransport.borrowed(mount, udc, bounce=bounce, owner_gadget=owner_gadget), **kw)

  @classmethod
  def open_tcp(cls, host: str, port: int = 5599, **kw) -> JetlinkClient:
    from jetlink.transport.tcp import TcpTransport
    return cls(TcpTransport.connect(host, port), **kw)

  @classmethod
  def open_listen(cls, address: str, timeout: float | None = None, **kw) -> JetlinkClient:
    """Wait for one peer to dial `address`, '[HOST:]PORT' (every interface by
    default), and run the session over that connection, as the comma takes a
    phone's dial (docs/transport.md). For the bench and parity scripts."""
    from jetlink.transport.tcp import TcpTransport
    host, _, port = address.rpartition(':')
    if not port.isdigit():
      raise ValueError(f'listen on [HOST:]PORT, not {address!r}')
    transport, peer = TcpTransport.listen_once(host or '0.0.0.0', int(port), timeout)
    log.info('peer dialed in from %s:%d', peer[0], peer[1])
    return cls(transport, **kw)

  @classmethod
  def open_socket(cls, sock, **kw) -> JetlinkClient:
    """Over a socket somebody else connected: a phone that dialed the comma
    over the cable's network interface, accepted by whoever holds the loan
    (jetlink.openpilot.link.connect). The same session as open_tcp from here on."""
    from jetlink.transport.tcp import TcpTransport
    return cls(TcpTransport(sock), **kw)

  # -- plumbing -------------------------------------------------------------

  def _next_seq(self) -> int:
    self.seq = (self.seq + 1) & 0xFFFFFFFF
    return self.seq

  def _dispatch(self, msg: Message) -> None:
    """Handle the messages the server may send at any time."""
    if msg.msg_type == P.Msg.PROGRESS:
      p = json.loads(bytes(msg.payload))
      if self.progress_cb:
        self.progress_cb(p.get('stage', ''), float(p.get('frac', 0.0)), p.get('msg', ''))
    elif msg.msg_type == P.Msg.ENGINE_RESP:
      state = json.loads(bytes(msg.payload))
      # A build that finished for a model this client has moved off would
      # otherwise read as ready, and every frame after it as NOT_READY.
      wanted = (self._engine_state or {}).get('sha256')
      if wanted is not None and state.get('sha256') not in (None, wanted):
        log.warning("ignoring engine state for %s (we want %s)",
                    str(state.get('sha256'))[:16], wanted[:16])
      else:
        self._engine_state = state
    elif msg.msg_type == P.Msg.ERROR:
      e = json.loads(bytes(msg.payload))
      if msg.seq in self._no_reply_wanted:
        # an older server answering a message it does not know; see leave()
        self._no_reply_wanted.discard(msg.seq)
        log.info("the server did not take seq %d (%s: %s)", msg.seq, e.get('error'), e.get('detail'))
        return
      raise LinkError(f"server error: {e.get('error')}: {e.get('detail')}")

  def _expect(self, msg_type: int, seq: int, timeout: float | None) -> Message:
    """Wait for one specific reply, servicing anything unsolicited on the way.
    A timeout of 0 still takes a reply that has already arrived: one read
    that does not wait, as the transport does it."""
    end = None if timeout is None else time.monotonic() + timeout
    read_once_late = False
    while True:
      remaining = None if end is None else end - time.monotonic()
      if remaining is not None and remaining <= 0:
        if read_once_late:
          raise LinkTimeout(f'timed out waiting for {_name(P.Msg, msg_type)} seq={seq}')
        read_once_late, remaining = True, 0.0
      msg = self.t.recv(timeout=remaining)
      if msg.msg_type == msg_type and msg.seq == seq:
        return msg
      if msg.msg_type in (P.Msg.PROGRESS, P.Msg.ENGINE_RESP, P.Msg.ERROR):
        self._dispatch(msg)
        continue
      if msg.msg_type == P.Msg.INFER_RESP and any(f.seq == msg.seq for f in self._in_flight):
        # the answer to a frame that gave up waiting for it (a held frame):
        # read like any other, then on to what is wanted
        self._take_reply(msg)
        continue
      # A reply a caller gave up on. Drop it, or every frame reads one behind.
      log.warning("discarding stale %s seq=%d (waiting for %s seq=%d)",
                  _name(P.Msg, msg.msg_type), msg.seq, _name(P.Msg, msg_type), seq)

  # -- handshake ------------------------------------------------------------

  def hello(self, timeout: float = 5.0, pick: dict | None = None) -> dict:
    """Introduce this client. The server starts its session over on a hello,
    so this is also how a new owner of the gadget takes over one the server
    never saw end; see Session.greet in JetlinkServer. `pick` is the comma's
    own big-model pick with its age and its date by the comma's clock, {ref,
    sha256, nbytes, name, age_s, at}; the answer's `pick` is the newer of it
    and the server's (PickStore)."""
    seq = self._next_seq()
    client = {'nonce': self.nonce, 'name': self.name}
    if pick is not None:
      client['pick'] = pick
    try:
      link = self.t.link_info()
    except Exception:
      link = {}   # never worth a failed hello
    if link:
      # Which link this is and how fast: the server shows it (USB 3, USB 2,
      # TCP), and only this end always knows. See Transport.link_info.
      client['link'] = link
    # a new session: frames of the old one still unanswered will not be, and
    # its last output is nothing the new model may publish
    self._in_flight.clear()
    self.last_output = None
    self.frames_lost = 0
    self.t.send_json(P.Msg.HELLO_REQ, seq, {'client': client})
    resp = json.loads(bytes(self._expect(P.Msg.HELLO_RESP, seq, timeout).payload))
    self._take_datagrams(resp)
    return resp

  def _take_datagrams(self, hello: dict) -> None:
    """Frames as datagrams when the server offers them (only a phone's server,
    over the cable). A session that offers none puts frames back on the link."""
    port, token = hello.get('frame_port'), hello.get('frame_token')
    if port and token is not None and self.allow_datagrams and self.t.use_datagrams(int(port), int(token)):
      log.info("frames go to the server as datagrams (port %d)", int(port))
    else:
      self.t.stop_datagrams()

  def state(self, timeout: float = 2.0) -> dict:
    seq = self._next_seq()
    self.t.send_json(P.Msg.STATE_REQ, seq, {})
    return json.loads(bytes(self._expect(P.Msg.STATE_RESP, seq, timeout).payload))

  def ping(self, timeout: float = 1.0) -> float:
    seq = self._next_seq()
    t0 = time.perf_counter()
    self.t.send(P.Msg.PING, seq)
    self._expect(P.Msg.PONG, seq, timeout)
    return time.perf_counter() - t0

  def shutdown(self, reason: str = '', timeout: float = 5.0) -> dict:
    """Ask the Jetson to power off, not sleep: only a DC cycle or the button
    brings it back, so this is for the comma's low-battery shutdown alone. The
    reply comes before the box goes down."""
    seq = self._next_seq()
    self.t.send_json(P.Msg.SHUTDOWN_REQ, seq, {'reason': reason})
    return json.loads(bytes(self._expect(P.Msg.SHUTDOWN_RESP, seq, timeout).payload))

  def leave(self, reason: str, timeout: float = 0.5, **measured) -> None:
    """Say why this client stops using the link (protocol.LEAVE_REASONS), and
    what it measured over it (JetlinkModelState.trips): the server logs both
    next to its own numbers, which are the half the comma cannot see, and the
    other way round. No reply is read, and the connection stays: a later hello
    starts a new session over it. An older server answers ERROR
    unknown_message, which _dispatch discards by this seq. Never raises, and
    says nothing over a dead link; a send that fails leaves it dead."""
    if self.dead:
      return
    seq = self._next_seq()
    self._no_reply_wanted.add(seq)
    try:
      self.t.send_json(P.Msg.LEAVE, seq, {'reason': reason, **measured}, timeout=timeout)
    except LinkError as e:
      self.dead = True
      log.warning("could not say why the link is left (%s)", e)

  # -- model provisioning ---------------------------------------------------

  def ensure_engine(self, sha256: str, nbytes: int, onnx_path: str | Path | None = None,
                    frame_skip: int = DEFAULT_FRAME_SKIP,
                    progress: ProgressFn | None = None,
                    build_timeout: float = 900.0,
                    should_stop: StopFn | None = None,
                    fetch: bool = False, ref: str | None = None) -> ModelSpec:
    """Make the server ready to run this model, uploading and building if needed.

    Blocks until ready or the build fails, and returns the spec the server
    derived from the ONNX. Only the server parses the model, so a device whose
    parser rejects an export (tinygrad and the org.tinygrad domain) still runs.

    `onnx_path` None means the caller cannot upload (modeld) and a missing
    engine is EngineMissing. `fetch` asks a server that lacks the model to
    download it itself (state downloading, stage download) rather than answer
    need_upload; one that cannot, or is too old to, answers need_upload as
    before. `ref` is the catalog ref the model is known by.
    `progress(stage, frac, msg)` has stage in
    download/upload/patch/parse/build/load. `should_stop` is polled during long waits.
    """
    self.progress_cb = progress
    self._should_stop = should_stop or (lambda: False)
    self.spec = None
    self._engine_state = None

    seq = self._next_seq()
    request = {'sha256': sha256, 'nbytes': nbytes, 'frame_skip': frame_skip}
    if fetch:
      request['fetch'] = True
    if ref:
      request['ref'] = ref
    self.t.send_json(P.Msg.ENGINE_REQ, seq, request)
    resp = json.loads(bytes(self._expect(P.Msg.ENGINE_RESP, seq, 60.0).payload))
    self._engine_state = resp
    log.info("server engine state: %s (%s)", resp['state'], resp.get('detail', ''))

    if resp['state'] == 'need_upload':
      if onnx_path is None:
        raise EngineMissing(f"server has no engine for {sha256[:16]} ({resp.get('detail', '')})")
      self._upload(Path(onnx_path), nbytes, int(resp.get('chunk') or CHUNK))

    try:
      self._await_ready(build_timeout)
    except EngineMissing:
      # need_upload came later, once the job holding the device (a preload
      # of another model) was done. A caller with the file sends it now.
      if onnx_path is None:
        raise
      self._upload(Path(onnx_path), nbytes, int(self._engine_state.get('chunk') or CHUNK))
      self._await_ready(build_timeout)
    spec = ModelSpec.from_dict(self._engine_state['spec'])
    if spec.sha256 != sha256:
      raise LinkError(f"server answered for {spec.sha256[:16]}, we asked for {sha256[:16]}")
    self.spec = spec
    return spec

  def _stopped(self) -> None:
    if self._should_stop():
      raise LinkError("stopped while waiting for the engine")

  def _upload(self, onnx_path: Path, nbytes: int, chunk: int) -> None:
    log.info("uploading %s (%d MB)", onnx_path.name, nbytes >> 20)
    t0 = time.time()
    sent = 0
    with open(onnx_path, 'rb') as f:
      while data := f.read(chunk):
        self._stopped()
        self.t.send(P.Msg.UPLOAD_CHUNK, self._next_seq(),
                    (sent.to_bytes(8, 'little'), data))
        sent += len(data)
        if self.progress_cb:
          self.progress_cb('upload', sent / nbytes, f'{sent >> 20}/{nbytes >> 20} MB')
    seq = self._next_seq()
    self.t.send_json(P.Msg.UPLOAD_DONE, seq, {})
    resp = json.loads(bytes(self._expect(P.Msg.ENGINE_RESP, seq, 300.0).payload))
    self._engine_state = resp
    rate = sent / max(1e-6, time.time() - t0) / 1e6
    log.info("uploaded %d MB at %.1f MB/s -> %s", sent >> 20, rate, resp['state'])
    if resp['state'] == 'failed':
      raise LinkError(f"upload rejected: {resp.get('detail')}")

  def _await_ready(self, timeout: float) -> None:
    end = time.monotonic() + timeout
    while True:
      st = (self._engine_state or {}).get('state')
      if st == 'ready':
        if 'spec' not in (self._engine_state or {}):
          raise LinkError("server reported ready without a model spec")
        return
      if st == 'failed':
        raise LinkError(f"engine build failed: {self._engine_state.get('detail')}")
      if st == 'need_upload':
        # pushed when the device came free: without this the wait ran to
        # build_timeout, a whole drive for provisioning's 1800 s
        sha = str(self._engine_state.get('sha256'))
        raise EngineMissing(f"server has no engine for {sha[:16]} ({self._engine_state.get('detail', '')})")
      if time.monotonic() > end:
        raise LinkTimeout(f"engine not ready after {timeout:.0f}s (state={st})")
      self._stopped()
      try:
        self._dispatch(self.t.recv(timeout=min(1.0, max(0.1, end - time.monotonic()))))
      except LinkTimeout:
        continue

  # -- inference ------------------------------------------------------------

  def infer_begin(self, warped: np.ndarray, packed: np.ndarray, frame_id: int = 0,
                  reset: bool = False, want_state: bool = False, deadline: float | None = None,
                  skip_if_busy: bool = False) -> int | None:
    """Send a frame and return immediately with its sequence number.

    Split from infer_end so the caller can work while the Jetson is busy;
    openpilot publishes chestnutState in that window. A frame whose reply
    infer_end stopped waiting for is read by the next drain or infer_end. A
    host that has answered none of them for the client's deadline is gone,
    not slow, and the link is done here, before another frame goes out to
    it. `deadline` bounds this frame's send.

    `skip_if_busy` says the caller can do without this frame's reply: it
    holds the last output, as JetlinkModelState.end does. A frame the link
    cannot take without waiting for the host to drain earlier ones is then
    not sent, and None comes back: the link is as good as it was
    (Transport.try_send). Over a phone's cable it goes as datagrams instead,
    where a lost one is a frame the server never answers, and the next reply
    takes it out of flight (_take_reply).
    """
    if self.spec is None:
      raise LinkError("ensure_engine() first")
    if self.dead:
      raise LinkError("link previously failed")
    if self.waiting_for() > self.deadline:
      self.dead = True
      raise LinkError(f"no answer to {len(self._in_flight)} frames in {self.deadline:.1f}s; link abandoned")
    warped = _as_bytes(warped, self.spec.warped_nbytes, 'warped')
    packed = _as_bytes(packed, self.spec.packed_nbytes, 'packed')
    seq = self._next_seq()
    flags = ((P.Flag.RESET_QUEUES if reset else 0) | (P.Flag.WANT_STATE if want_state else 0)
             | (P.Flag.WANT_HIDDEN if self.want_hidden else 0))
    parts = (P.pack_infer_req(frame_id, flags), warped, packed)
    sent = Sent(seq, frame_id, flags, time.monotonic())
    timeout = self.deadline if deadline is None else deadline
    try:
      if skip_if_busy and self.t.datagrams:
        self.t.send_datagrams(P.Msg.INFER_REQ, seq, parts)
      elif skip_if_busy:
        if not self.t.try_send(P.Msg.INFER_REQ, seq, parts, timeout=timeout):
          return None   # the seq is skipped; replies are matched by seq, not counted
      else:
        self.t.send(P.Msg.INFER_REQ, seq, parts, timeout=timeout)
    except LinkError:
      self.dead = True
      raise
    self._in_flight.append(sent)
    return seq

  def infer_end(self, seq: int, deadline: float | None = None, hold: float | None = None) -> np.ndarray | None:
    """Block for the frame's output, as modeld blocks on a chestnut.

    A long frame is a dropped camera frame, which modeld tolerates. Only a stall
    past `deadline` (the client's by default) is a failure, and then the link
    is done. With `hold`, wait that long and no longer: None comes back when
    the reply has not, the link is as good as it was, and the reply is read
    off the stream by whatever reads it next. Another frame can go out
    meanwhile, so the camera frame is not dropped waiting for this one.
    """
    sent = next((f for f in self._in_flight if f.seq == seq), None)
    if sent is None:
      raise LinkError(f'frame seq {seq} was not sent, or has been read already')
    failing = hold is None
    if failing:
      hold = (self.deadline if deadline is None else deadline) - (time.monotonic() - sent.sent_at)
    try:
      if failing and hold <= 0:
        raise LinkTimeout('frame deadline elapsed during send')
      msg = self._expect(P.Msg.INFER_RESP, seq, hold)
    except LinkTimeout as e:
      if not failing:
        return None
      self.dead = True
      raise LinkError(f"no answer for frame in {self.deadline:.1f}s; link abandoned") from e
    except LinkError:
      self.dead = True
      raise
    return self._take_reply(msg)

  def drain(self) -> int:
    """Read every reply that has already arrived, without waiting for one that
    has not. How many frames were answered. What they carried is kept as
    last_output, last_timings and last_state, as infer_end keeps it; a frame
    the server failed is this link's failure, as it would have been there."""
    answered = 0
    while self._in_flight:   # with nothing out there is nothing to read for
      try:
        msg = self.t.recv(timeout=0)
      except LinkTimeout:
        break
      except LinkError:
        self.dead = True
        raise
      if msg.msg_type == P.Msg.INFER_RESP:
        self._take_reply(msg)
        answered += 1
      else:
        self._dispatch(msg)
    return answered

  @property
  def unanswered(self) -> int:
    """Frames sent and not yet answered."""
    return len(self._in_flight)

  def waiting_for(self) -> float:
    """How long the oldest frame still unanswered has been out, in seconds;
    0 with none. A host that has gone quiet shows here before any frame
    waits on it."""
    return time.monotonic() - self._in_flight[0].sent_at if self._in_flight else 0.0

  def _take_reply(self, msg: Message) -> np.ndarray:
    """One INFER_RESP off the stream: checked against the frame it answers,
    its timings and telemetry kept, the frame and any older one taken out of
    flight. Raises NotFinite on Status.NOT_FINITE, the link kept, and with
    the link dead on anything else but a good answer."""
    while self._in_flight and self._in_flight[0].seq != msg.seq:
      # an older frame the server never answered: it answers in order, so a
      # frame that fell out of its session (a hello between), or whose
      # datagrams did not all arrive, is not coming
      skipped = self._in_flight.popleft()
      log.warning('frame %d (seq %d) was never answered', skipped.frame_id, skipped.seq)
      self.frames_lost += 1
    if not self._in_flight:
      self.dead = True
      raise LinkError(f'inference response seq {msg.seq} answers no frame sent')
    _, frame_id, flags, _ = self._in_flight.popleft()
    if msg.payload.nbytes < P.INFER_RESP_SIZE:
      self.dead = True
      raise LinkError('inference response is missing its header')
    fid, status, gpu_us, queue_us, total_us = P.unpack_infer_resp(msg.payload)
    self.last_timings = (gpu_us, queue_us, total_us)
    if status == P.Status.NOT_FINITE and fid == frame_id:
      # the server's session goes on, keeping nothing of the frame
      # (Session.infer), so the link stays and last_output is the one before
      raise P.NotFinite(f"inference output is not finite: NOT_FINITE (frame {fid})")
    if status != P.Status.OK:
      self.dead = True
      raise LinkError(f"inference failed: {_name(P.Status, status)} (frame {fid})")
    if fid != frame_id:
      self.dead = True
      raise LinkError(f'inference response frame {fid}, expected {frame_id}')
    whole = bool(flags & P.Flag.WANT_HIDDEN)
    n = self.spec.output_nelem if whole else self.spec.reply_nelem
    end = P.INFER_RESP_SIZE + n * 4
    if msg.payload.nbytes < end:
      self.dead = True
      raise LinkError('inference response is missing model outputs')
    if msg.payload.nbytes > end:
      # piggybacked telemetry, when asked for; anything else is outputs laid
      # out some other way (hidden_state left in by a server that says
      # protocol 3, or a slice the two ends resolve differently): every float
      # after it would be misread
      state = _telemetry(msg.payload[end:]) if flags & P.Flag.WANT_STATE else None
      if state is None:
        self.dead = True
        raise LinkError(f'inference response is {msg.payload.nbytes} bytes, expected {end}')
      self.last_state = state
    receive = getattr(self.t, 'last_receive', None)
    if receive is not None and total_us > 50_000:
      log.warning('frame %d receive maxima: prepare %.1f read_wait %.1f handoff %.1f ms; '
                  'server gpu %.1f queue %.1f total %.1f ms', fid,
                  receive['prepare'] * 1e3, receive['read_wait'] * 1e3, receive['handoff'] * 1e3,
                  gpu_us / 1e3, queue_us / 1e3, total_us / 1e3)
    self.last_output = _whole_output(self.spec, np.frombuffer(msg.payload, np.float32, n, P.INFER_RESP_SIZE), whole)
    return self.last_output

  def infer(self, warped: np.ndarray, packed: np.ndarray, frame_id: int = 0,
            reset: bool = False, deadline: float | None = None,
            want_state: bool = False) -> np.ndarray:
    """One frame. Returns the model output as float32, shaped (n,), laid out
    as the spec's output_slices say; hidden_state reads as zeros unless
    `want_hidden`, since it stays on the server.

    `warped` is (2, 6, H, W) uint8 off openpilot's warp, `packed` the float32
    packed_npy_inputs. Either may be any buffer, so a tinygrad Tensor.data()
    memoryview reaches the wire with no numpy round trip.
    """
    return self.infer_end(self.infer_begin(warped, packed, frame_id, reset, want_state, deadline), deadline)

  @property
  def lendable(self) -> bool:
    """Could another process take the IO over from this link? See
    FfsTransport.lendable."""
    return self.t.lendable

  def rebind(self) -> bool:
    """Make the peer see the link arrive again, where that is a thing this
    transport can do. See FfsTransport.rebind; False everywhere else."""
    return self.t.rebind()

  def release_endpoints(self) -> bool:
    """Give the endpoints up without giving the gadget up. See
    FfsTransport.release_endpoints; False everywhere else."""
    return self.t.release_endpoints()

  def close(self) -> None:
    # a closed client is as dead as a failed one: whoever still holds it must
    # open a new one rather than reuse a closed socket or endpoint file
    self.dead = True
    self.t.close()


def _telemetry(tail) -> dict | None:
  """A WANT_STATE reply's telemetry: the JSON object after the outputs. None
  for anything else, floats included, which is a reply laid out another way."""
  raw = bytes(tail)
  if raw[:1] != b'{':
    return None
  try:
    state = json.loads(raw)
  except ValueError:
    return None
  return state if isinstance(state, dict) else None


def _whole_output(spec: ModelSpec, reply: np.ndarray, whole: bool) -> np.ndarray:
  """The output vector, from a reply that may leave hidden_state out.

  Always a new array: `reply` views the transport's reusable receive buffer.
  The full length keeps the spec's output_slices valid, so the caller slices
  as it did when the whole vector crossed the link.
  """
  hidden = spec.hidden_range
  if whole or hidden is None:
    return reply.copy()
  start, stop = hidden
  out = np.empty(spec.output_nelem, np.float32)
  out[:start] = reply[:start]
  out[start:stop] = 0
  out[stop:] = reply[start:]
  return out


def _as_bytes(buf, expect: int, name: str) -> memoryview:
  """Byte view over a numpy array or any buffer, size-checked.

  Bytes rather than shape, so the caller can pass whatever it has and a
  mismatch fails here instead of misparsing on the far end.
  """
  mv = memoryview(buf)
  if not mv.contiguous:
    raise LinkError(f"{name} must be contiguous")
  mv = mv.cast('B')
  if mv.nbytes != expect:
    raise LinkError(f"{name} is {mv.nbytes} bytes, expected {expect}")
  return mv
