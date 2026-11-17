"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

Who may do endpoint IO on the gadget, while one process owns it throughout.

The owner (owner.py) holds ep0 for as long as the link is on, and nothing else
ever does: to the Jetson, a change of who holds ep0 is an unplug, a fresh
libusb open and a fresh server session.

What still has to change hands is the right to read the endpoint files.
FunctionFS keeps a queued read queued until something completes it, so a second
reader would sit behind the first and take its reply. This is the handshake for
that: modeld borrows for the length of a drive, the owner stays off the
endpoints while it does, and the connection is the lease, so a modeld that is
killed returns it by dying. There is no "give it back" message: the socket
closing is the only signal, because it is the only one a killed process sends.

An iPhone cannot take the endpoint files: it is on the gadget's network
interface and dials the comma at CABLE_ADDR. Whoever holds the loan listens
for that dial: the owner while nobody does (CableListener), so a parked phone
shows connected and a dial is news that starts a provisioning run, and the
borrower while it holds the loan (Loan.accept), for the drive or the run. The
handshake is the handover: the owner closes its listener and the dial it
holds before it says yes, and listens again once the loan ends. No socket
changes hands, and nothing shares one.
"""
from __future__ import annotations

import errno
import json
import os
import select
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

from jetlink.comma import gadget

SOCKET = Path('/dev/shm/jetlink-lend.sock')
# how long a borrower waits for the owner to put the gadget down. It only has
# something to put down if it was mid-provision at ignition, and then it is one
# re-enumeration; the usual answer is immediate
BORROW_TIMEOUT = 8.0
# a stuck write is already 15 s old by the time this is asked for
BOUNCE_TIMEOUT = 10.0
# the owner records a note at once; this only bounds one that is wedged
NOTE_TIMEOUT = 5.0
RETRY = 0.25
POLL = 0.5
# what a borrower passes on of the server's hello (Loan.note_server): the owner
# never speaks the protocol, and sleep_after is how it knows whether letting
# go of the gadget lets the Jetson sleep. The rest is for its status record
SERVER_FIELDS = ('protocol', 'device', 'backend', 'runtime_version', 'trt_version', 'sleep_after', 'pick', 'fetches')


def _send(conn: socket.socket, msg: dict) -> None:
  conn.sendall(json.dumps(msg).encode() + b'\n')


def _recv_line(conn: socket.socket, buf: bytearray, deadline: float) -> dict | None:
  """One json message off the socket, or None if the deadline passes first.

  Both ends speak newline-delimited json over a stream, so a message can arrive
  in pieces or with the next one behind it, and the recv timeout is a poll
  rather than a refusal: the lender answers one borrower at a time and can be a
  moment late while it lets go of the one before.

  The peer going away raises, because that is the one thing neither end may
  read as "nothing yet": for the lender it is the whole lease ending.
  """
  while time.monotonic() < deadline:
    if b'\n' in buf:
      line, _, rest = bytes(buf).partition(b'\n')
      buf[:] = rest
      return json.loads(line)
    try:
      chunk = conn.recv(4096)
    except TimeoutError:
      continue
    if not chunk:
      raise ConnectionResetError('the peer closed the link socket')
    buf.extend(chunk)
  return None


class Loan:
  """The right to do endpoint IO on a gadget the owner holds, or on the
  cable the right to listen for the phone's dial (accept).

  Held for the length of a drive: modeld is stopped at every ignition-off and
  SIGKILLed if it lingers, so the socket closing is how the link is handed
  back, and a modeld that crashed hands it back the same way.
  """

  def __init__(self, conn: socket.socket, buf: bytearray, mount: str, udc: str, name: str = 'modeld'):
    self.conn = conn
    self.mount = mount
    self.udc = udc
    self.name = name
    # a phone is the host: the link is its dial, taken by accept, and the
    # endpoint files are left alone. The owner's answer says (_take)
    self.cable = False
    # the cable listener, bound on the first accept and kept for the loan
    self._srv: socket.socket | None = None
    self.bound: tuple | None = None
    self._buf = buf
    self._lock = threading.Lock()
    self._closed = False

  @property
  def closed(self) -> bool:
    return self._closed

  def bounce(self) -> bool:
    """Ask the owner to take the gadget down and put it back up.

    The only thing that completes a FunctionFS write nobody is reading is the
    unbind, and the unbind belongs to whoever holds ep0. Called on writes the
    host has stopped taking (FfsTransport._abort_write), so the re-enumeration
    it costs is not the expensive part.
    """
    with self._lock:
      if self._closed:
        return False
      try:
        _send(self.conn, {'op': 'bounce'})
        reply = _recv_line(self.conn, self._buf, time.monotonic() + BOUNCE_TIMEOUT)
      except OSError:
        gadget.log.exception("jetlink: could not ask for a gadget bounce")
        return False
      return bool(reply and reply.get('ok'))

  def note_server(self, hello: dict) -> bool:
    """Tell the owner what the server said in its hello, which the owner
    cannot ask for itself. Every borrower sends it after every hello, so
    modeld's join refreshes it each drive and a Jetson moved to another
    power supply is known by the next one. False when the owner did not
    take it: an older owner answers "unknown op", which is not an error.

    An answer that does not come within NOTE_TIMEOUT, or is not one, closes
    the loan. The exchange has no ids, so an answer that came later would be
    read as the answer to the next request on it, a renewal's or a bounce's,
    and every one after that would be one behind."""
    fields = {k: hello[k] for k in SERVER_FIELDS if k in hello}
    with self._lock:
      if self._closed:
        return False
      try:
        _send(self.conn, {'op': 'server', **fields})
        reply = _recv_line(self.conn, self._buf, time.monotonic() + NOTE_TIMEOUT)
      except (OSError, ValueError) as e:
        reply, why = None, str(e) or type(e).__name__
      else:
        why = 'no answer' if reply is None else f'the answer {reply!r}'
      if not isinstance(reply, dict):
        gadget.log.warning("jetlink: the owner did not take what the server said (%s), letting the loan go", why)
        self._closed = True
        _shut(self.conn)
        return False
    return bool(reply.get('ok'))

  def accept(self, timeout: float) -> socket.socket:
    """The phone's next dial, within `timeout` s: the link on the cable.

    The listener is bound on the first call and kept for the loan's life, so a
    phone that dials again after a lost link is taken here, with nobody else
    involved. The newest dial wins, as on the owner's listener: the app may
    have restarted behind an older one. Raises TimeoutError with no dial in
    time, and OSError when the address could not be bound for the whole wait:
    the owner lets go of it before it says yes, but a run before us may still
    be exiting.
    """
    if not self.cable:
      raise RuntimeError('this loan is the endpoint files, not the cable')
    deadline = time.monotonic() + timeout
    while self._srv is None:
      try:
        srv = _listen()
      except OSError:
        if time.monotonic() >= deadline:
          raise
        time.sleep(RETRY)
        continue
      self._srv, self.bound = srv, srv.getsockname()
    while True:
      if select.select([self._srv], [], [], max(0.0, deadline - time.monotonic()))[0]:
        conn = newest_dial(self._srv)
        if conn is not None:
          return conn
      if time.monotonic() >= deadline:
        raise TimeoutError(f'no phone dialed in {timeout:.0f} s')

  def _take(self, timeout: float) -> dict | None:
    """Ask the owner for the link until it lends one or `timeout` passes; the
    reply that lent it. The loan is closed when the owner is gone."""
    try:
      reply = _ask(self.conn, self._buf, self.name, time.monotonic() + timeout)
    except (OSError, ValueError, KeyError):
      if not self._closed:   # a close() from another thread wakes the ask this way
        gadget.log.exception("jetlink: could not ask the owner for the link")
      self._closed = True
      _close(self.conn)
      return None
    if reply is None:
      return None
    self.mount, self.udc = str(reply['mount']), str(reply['udc'])
    self.cable = bool(reply.get('cable'))
    return reply

  def close(self) -> None:
    # not behind the lock: the shutdown is what wakes an ask on another thread
    self._closed = True
    _shut(self.conn)
    with self._lock:
      _close(self.conn)
      srv, self._srv, self.bound = self._srv, None, None
      _close(srv)


def borrow(name: str = 'modeld', timeout: float = BORROW_TIMEOUT, path: Path | None = None) -> Loan | None:
  """Ask the owner for the link, or None if there is nobody to ask: the link
  was only just turned on, or the owner died or cannot listen, which it says
  in gadget.gadget_error(). The caller asks again later; only the owner ever
  holds ep0. `path` defaults to SOCKET as it is when called, so a test that
  points SOCKET elsewhere is never lent the real owner's link.
  """
  try:
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(POLL)
    conn.connect(str(SOCKET if path is None else path))
  except OSError:
    return None   # no owner listening
  loan = Loan(conn, bytearray(), '', '', name=name)
  reply = loan._take(timeout)
  if reply is None:
    loan.close()
    return None
  gadget.log.warning("jetlink: borrowed %s from the owner", _what_was_lent(reply))
  return loan


def _ask(conn: socket.socket, buf: bytearray, name: str, deadline: float) -> dict | None:
  """Ask for the link until the owner lends it or `deadline` passes: the reply
  that lent it. None when out of time or refused."""
  while time.monotonic() < deadline:
    _send(conn, {'op': 'borrow', 'name': name})
    reply = _recv_line(conn, buf, deadline)
    if reply is None:
      return None   # out of time
    if reply.get('ok'):
      return reply
    if not reply.get('retry'):
      gadget.log.warning("jetlink: the owner would not lend the gadget (%s)", reply.get('detail'))
      return None
    time.sleep(RETRY)
  return None


def _what_was_lent(reply: dict) -> str:
  if reply.get('cable'):
    return "the cable link"
  return f"the gadget (udc {reply.get('udc')})"


# between attempts to bind the cable address: usb0 has no address until
# jetlink-root.sh net has run, and a bind that keeps failing is not worth a log
# line twice a second
CABLE_BIND_BACKOFF = 5.0


def _listen() -> socket.socket:
  """A listener on CABLE_ADDR, non-blocking, or OSError: the address is
  usb0's, which may not exist yet, or another process still has it."""
  from jetlink.transport.tcp import TcpTransport
  srv = TcpTransport.listen(*gadget.CABLE_ADDR, backlog=2)
  srv.setblocking(False)
  return srv


def newest_dial(srv: socket.socket) -> socket.socket | None:
  """Every dial waiting on the non-blocking listener `srv`, the newest kept
  and the others closed: the app may have restarted behind an older one.
  None with nobody waiting. The dial comes back blocking, as a transport
  wants it."""
  conn = None
  while True:
    try:
      newer, _ = srv.accept()
    except (BlockingIOError, InterruptedError):
      break
    _close(conn)
    conn = newer
  if conn is not None:
    conn.setblocking(True)
  return conn


class CableListener:
  """The owner's ear for a phone: one accept socket on CABLE_ADDR, open while
  the gadget is presented and nobody holds the loan, holding at most one dial
  at a time.

  A dial is accepted from the owner's step, never read: what it proves is that
  a phone is on the cable, and whether its app is running. A newer dial
  replaces an older one, so a phone whose app restarted is not stuck behind
  its own dead connection. A borrower takes the port over (vacate) and
  listens for the phone itself.
  """

  def __init__(self):
    self._srv: socket.socket | None = None
    self._sock: socket.socket | None = None
    self.peer: str | None = None
    self.bound: tuple | None = None
    self.next_open = 0.0
    # we hung up on the phone because its borrower finished, so its next dial
    # is the same phone coming back, not news; `news` says which the last
    # accepted dial was
    self.redial_expected = False
    self.news = False
    self._last_error = ''
    self._lock = threading.Lock()

  @property
  def listening(self) -> bool:
    return self._srv is not None

  @property
  def held(self) -> bool:
    """Is a phone's dial in hand?"""
    return self._sock is not None

  def open(self) -> bool:
    """Bind, or say why not. Never raises: the address is usb0's, which may
    not exist yet, and the owner's loop must carry on without it."""
    if self._srv is not None:
      return True
    now = time.monotonic()
    if now < self.next_open:
      return False
    self.next_open = now + CABLE_BIND_BACKOFF
    try:
      srv = _listen()
    except OSError as e:
      why = ('usb0 has no address yet' if e.errno == errno.EADDRNOTAVAIL else str(e))
      if why != self._last_error:
        self._last_error = why
        gadget.log.warning("jetlink: cannot listen for a phone on %s:%d (%s)", *gadget.CABLE_ADDR, why)
      return False
    self._srv = srv
    self.bound = srv.getsockname()
    self._last_error = ''
    gadget.log.warning("jetlink: listening for a phone on %s:%d", *self.bound[:2])
    return True

  def poll(self) -> str | None:
    """Take a dial if one is waiting. The peer's address when one arrived,
    else None. A held dial the phone has since dropped is let go here too,
    so a borrower is never handed a dead socket."""
    if self._srv is None:
      return None
    self._drop_if_dead()
    try:
      conn = newest_dial(self._srv)
    except OSError:
      gadget.log.exception("jetlink: the cable listener failed")
      return None
    if conn is None:
      return None
    with self._lock:
      old, self._sock = self._sock, conn
      self.peer = str(conn.getpeername()[0])
      self.news, self.redial_expected = not self.redial_expected, False
    _close(old)
    return self.peer

  def _drop_if_dead(self) -> None:
    """A phone that closed its end. It sends nothing until a hello."""
    with self._lock:
      sock = self._sock
      if sock is None or not hung_up(sock):
        return
      self._sock, self.peer = None, None
    gadget.log.warning("jetlink: the phone hung up")
    _close(sock)

  def vacate(self) -> None:
    """Stop listening and let the held dial go, for a borrower that listens
    itself: the port is free when the owner's yes lands, and the phone dials
    again in a moment, to the borrower. Its dial after the loan is the same
    phone coming back, not news. From the lender's thread."""
    self.release(expect_redial=True)
    self._stop_listening()
    self.next_open = 0.0   # listen again on the first step after the loan ends

  def release(self, expect_redial: bool = False) -> None:
    """Close the held dial. The phone dials again and the next accept
    replaces it: a borrower that has finished, or died, must not leave the
    phone talking to nobody. `expect_redial` marks that next dial as ours to
    expect rather than a phone turning up."""
    with self._lock:
      sock, self._sock = self._sock, None
      self.peer = None
      self.redial_expected = expect_redial and sock is not None
    _close(sock)

  def close(self) -> None:
    self.release()
    self._stop_listening()

  def _stop_listening(self) -> None:
    with self._lock:
      srv, self._srv = self._srv, None
      self.bound = None
    _close(srv)


def _close(sock: socket.socket | None) -> None:
  if sock is not None:
    try:
      sock.close()
    except OSError:
      pass


def _shut(sock: socket.socket) -> None:
  """End the connection for both ends, so the owner sees the lease end."""
  try:
    sock.shutdown(socket.SHUT_RDWR)
  except OSError:
    pass


def hung_up(sock: socket.socket, wait: float = 0.0) -> bool:
  """Whether the far end closed `sock`, waiting up to `wait` s to see. Bytes
  waiting read as alive, and the peek leaves them for whoever shares the socket;
  a peer that sends nothing unasked is readable only at EOF."""
  try:
    # select first: a socket with a timeout would wait that long in recv
    if not select.select([sock], [], [], wait)[0]:
      return False
    return sock.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b''
  except (BlockingIOError, InterruptedError, TimeoutError):
    return False   # a sharer took the bytes between the two
  except OSError:
    return True


class Lender:
  """The owner's side: one borrower at a time, for as long as it stays connected.

  `lendable` says whether the gadget is in the state a borrower can take over
  from, bound with no endpoint file open here; while it is not, a borrow is
  answered "retry" and the daemon's own loop puts it there. `cable` says the
  host is a phone (Jetlink iOS): the loan is then the right to
  listen for its dial, never the endpoint files, which a phone does not read,
  and `vacate` frees the port first (CableListener.vacate). `server` takes
  what a borrower passes on of the server's hello (Loan.note_server), with
  the borrower's name, on this thread.
  """

  def __init__(self, lendable: Callable[[], bool], bounce: Callable[[], bool],
               path: Path | None = None, cable: Callable[[], bool] | None = None,
               vacate: Callable[[], None] | None = None, server: Callable[[str, dict], None] | None = None):
    self._lendable = lendable
    self._bounce = bounce
    self._cable = cable or (lambda: False)
    self._vacate = vacate or (lambda: None)
    self._server = server
    # what this borrower was last told it has, so each change is logged once
    self._told = ''
    # as it is when made, for the same reason as borrow's
    self.path = SOCKET if path is None else path
    self.borrower = ''
    self._sock: socket.socket | None = None
    self._thread: threading.Thread | None = None
    self._stop = threading.Event()
    self._lent = threading.Event()
    # why the last start could not listen, for the owner to report
    self.error: str | None = None

  @property
  def listening(self) -> bool:
    """Can anybody ask us for the endpoints? If not, nobody can use the link,
    and the owner says so and starts this again; see Owner.ensure_lender."""
    return self._sock is not None

  @property
  def lent(self) -> bool:
    """Is somebody using the endpoints? True from the first ask, not the first
    successful one: the daemon has to get off them before it can say yes."""
    return self._lent.is_set()

  def start(self) -> bool:
    """Listen for borrowers. False, with the reason in `error`, on a read-only
    /dev/shm or a path somebody else owns; the caller logs it and tries again,
    so this does not."""
    if self._thread is not None:
      return True
    sock = None
    try:
      self._clear_stale()
      sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
      sock.bind(str(self.path))
      sock.listen(1)
      sock.settimeout(POLL)
    except OSError as e:
      if sock is not None:
        sock.close()
      self.error = str(e) or type(e).__name__
      return False
    self.error = None
    self._sock = sock
    self._thread = threading.Thread(target=self._serve, name='jetlink_lend', daemon=True)
    self._thread.start()
    return True

  def stop(self) -> None:
    self._stop.set()
    if self._thread is not None:
      self._thread.join(2.0)
      self._thread = None
    if self._sock is None:
      return   # never listened: the path, if any, is somebody else's
    self._sock.close()
    self._sock = None
    try:
      self.path.unlink(missing_ok=True)
    except OSError:
      pass

  def _clear_stale(self) -> None:
    """A socket file a dead daemon left behind. Proven dead by a connect that
    is refused, so a second owner cannot take the link from a live one."""
    if not self.path.exists():
      return
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
      probe.settimeout(0.5)
      probe.connect(str(self.path))
    except OSError:
      os.unlink(self.path)
    finally:
      probe.close()

  def _serve(self) -> None:
    while not self._stop.is_set():
      try:
        conn, _ = self._sock.accept()
      except (TimeoutError, OSError):
        continue
      try:
        self._handle(conn)
      except Exception:
        gadget.log.exception("jetlink: the borrower's connection failed")
      finally:
        conn.close()
        if self._lent.is_set():
          gadget.log.warning("jetlink: %s handed the %s back", self.borrower or 'the borrower',
                             'cable link' if self._told == 'cable' else 'gadget')
        # the owner's step listens for the phone again, now that the port is free
        self._told = ''
        self._lent.clear()
        self.borrower = ''

  def _tell(self, what: str, *msg) -> None:
    """Log a lend when it is not what this borrower already had: a renewal
    that changes nothing is not news."""
    if self._told != what:
      self._told = what
      gadget.log.warning(*msg)

  def _handle(self, conn: socket.socket) -> None:
    conn.settimeout(POLL)
    buf = bytearray()
    while not self._stop.is_set():
      try:
        msg = _recv_line(conn, buf, time.monotonic() + POLL)
      except (OSError, ValueError):
        return   # the borrower exited, was killed mid-drive, or is not one
      # None is only the poll coming round again; the lease ends on EOF, which
      # is what a modeld that manager stopped or SIGKILLed sends
      if msg is not None:
        self._answer(conn, msg)

  def _answer(self, conn: socket.socket, msg: dict) -> None:
    op = msg.get('op')
    if op == 'borrow':
      self.borrower = str(msg.get('name') or 'a borrower')
      self._lent.set()
      if self._cable():
        # a phone: the link is its dial, which the borrower listens for
        # itself; the owner's listener and the dial it holds go first, so the
        # port is free when this answer lands. The endpoint files stay put
        self._vacate()
        self._tell('cable', "jetlink: lending the cable link to %s", self.borrower)
        _send(conn, {'ok': True, 'cable': True, 'udc': gadget.bound_udc() or '', 'mount': str(gadget.FFS_MOUNT)})
        return
      udc = gadget.bound_udc()
      if not (udc and self._lendable()):
        # the daemon is mid-exchange, or has not bound yet. It sees `lent` on
        # its next cycle and puts the endpoints down for us
        _send(conn, {'ok': False, 'retry': True, 'detail': 'the gadget is still in use here'})
        return
      self._tell('gadget', "jetlink: lending the gadget to %s, udc %s", self.borrower, udc)
      _send(conn, {'ok': True, 'udc': udc, 'mount': str(gadget.FFS_MOUNT)})
    elif op == 'bounce':
      gadget.log.warning("jetlink: %s asked for a gadget bounce", self.borrower)
      _send(conn, {'ok': bool(self._bounce())})
    elif op == 'server' and self._server is not None:
      try:
        self._server(self.borrower or 'a borrower', {k: msg[k] for k in SERVER_FIELDS if k in msg})
      except Exception:
        # a note is never worth the lease: a raise here would end the loan
        # under a borrower that is using the endpoints
        gadget.log.exception("jetlink: could not record what the server said")
      _send(conn, {'ok': True})
    else:
      _send(conn, {'ok': False, 'detail': f'unknown op {op!r}'})
