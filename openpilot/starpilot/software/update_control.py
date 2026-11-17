"""Same-user, process-bound local control for the running updater."""

from __future__ import annotations

import os
import re
import socket
import struct
import threading
from collections.abc import Callable
from pathlib import Path


TIMEOUT = 0.5
MAX_COMMAND = 256
_BRANCH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/+@-]{0,127}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_CREDENTIALS = struct.Struct('3i')


class UpdaterControlError(RuntimeError):
  pass


def process_start(pid: int) -> int:
  try:
    raw = (Path('/proc') / str(pid) / 'stat').read_text()
    fields = raw[raw.rindex(')') + 2:].split()
    start = int(fields[19])
    if start <= 0:
      raise ValueError
    return start
  except (OSError, ValueError, IndexError):
    raise UpdaterControlError('Updater process identity is unavailable') from None


def _address(pid: int, start: int, uid: int) -> str:
  if type(pid) is not int or type(start) is not int or pid <= 1 or start <= 0:
    raise UpdaterControlError('Invalid updater identity')
  return f'\0starpilot-updater-v1-{uid}-{pid}-{start}'


def _peer(connection: socket.socket) -> tuple[int, int]:
  if not hasattr(socket, 'SO_PEERCRED'):
    raise UpdaterControlError('Peer credentials are unavailable')
  pid, uid, _ = _CREDENTIALS.unpack(connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _CREDENTIALS.size))
  return pid, uid


def _line(connection: socket.socket) -> bytes:
  data = bytearray()
  while len(data) < MAX_COMMAND:
    chunk = connection.recv(1)
    if not chunk:
      break
    data.extend(chunk)
    if chunk == b'\n':
      return bytes(data)
  raise UpdaterControlError('Invalid updater control message')


def _exchange(pid: int, start: int, command: bytes) -> None:
  try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
      connection.settimeout(TIMEOUT)
      connection.connect(_address(pid, start, os.geteuid()))
      if _peer(connection) != (pid, os.geteuid()) or process_start(pid) != start:
        raise UpdaterControlError('Updater process changed')
      connection.sendall(command + b'\n')
      if _line(connection) != b'ok\n':
        raise UpdaterControlError('Updater declined control request')
  except (OSError, TimeoutError):
    raise UpdaterControlError('Updater control is unavailable') from None


def available(pid: int, start: int) -> bool:
  try:
    _exchange(pid, start, b'status')
    return True
  except UpdaterControlError:
    return False


def _command(action: str, branch=None, commit=None, selected_commit=None) -> bytes:
  if action in ('version', 'download-version') and type(selected_commit) is str and _COMMIT.fullmatch(selected_commit):
    base = _command('fast', branch, commit).decode('ascii').split(' ', 1)[1]
    return f'{action} {base} {selected_commit}'.encode('ascii')
  if selected_commit is not None:
    raise UpdaterControlError('Invalid updater version target')
  if action in ('check', 'download') and branch is None and commit is None:
    return action.encode('ascii')
  if (action in ('update', 'fast', 'rollback', 'versions') and type(branch) is str and type(commit) is str and
      _BRANCH.fullmatch(branch) and _COMMIT.fullmatch(commit)):
    return f'{action} {branch} {commit}'.encode('ascii')
  raise UpdaterControlError('Invalid updater control action')


def _parse(command: bytes):
  if command in (b'status\n', b'check\n', b'download\n'):
    return command[:-1].decode('ascii'), None, None
  try:
    parts = command[:-1].decode('ascii').split(' ')
    if len(parts) == 4 and parts[0] in ('version', 'download-version') and command.endswith(b'\n'):
      action, branch, commit, selected = parts
      if _command(action, branch, commit, selected) + b'\n' == command:
        return action, branch, commit, selected
    action, branch, commit = parts
  except (UnicodeError, ValueError):
    raise UpdaterControlError('Invalid updater control message') from None
  if not command.endswith(b'\n') or action not in ('update', 'fast', 'rollback', 'versions') or _command(action, branch, commit) + b'\n' != command:
    raise UpdaterControlError('Invalid updater control message')
  return action, branch, commit


def send(pid: int, start: int, action: str, *, branch=None, commit=None, selected_commit=None) -> None:
  _exchange(pid, start, _command(action, branch, commit, selected_commit))


class UpdaterControlServer:
  def __init__(self, request: Callable[..., None]):
    self.request = request
    self.socket: socket.socket | None = None
    self.thread: threading.Thread | None = None
    self.stopped = threading.Event()

  def start(self) -> None:
    if self.socket is not None:
      raise UpdaterControlError('Updater control already started')
    pid = os.getpid()
    address = _address(pid, process_start(pid), os.geteuid())
    listener = None
    try:
      listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
      listener.settimeout(TIMEOUT)
      listener.bind(address)
      listener.listen(4)
    except OSError:
      if listener is not None:
        listener.close()
      raise UpdaterControlError('Updater control cannot listen') from None
    self.socket = listener
    self.thread = threading.Thread(target=self._run, args=(listener,), name='updater-control', daemon=True)
    self.thread.start()

  def _run(self, listener: socket.socket) -> None:
    while not self.stopped.is_set():
      try:
        connection, _ = listener.accept()
      except TimeoutError:
        continue
      except OSError:
        break
      with connection:
        connection.settimeout(TIMEOUT)
        try:
          _, uid = _peer(connection)
          command = _line(connection)
          if uid != os.geteuid():
            connection.sendall(b'error\n')
          else:
            parsed = _parse(command)
            action, branch, commit = parsed[:3]
            if action in ('version', 'download-version'):
              self.request(action, branch=branch, commit=commit, selected_commit=parsed[3])
            elif action in ('update', 'fast', 'rollback', 'versions'):
              self.request(action, branch=branch, commit=commit)
            elif action != 'status':
              self.request(action)
            connection.sendall(b'ok\n')
        except (RuntimeError, ValueError):
          try:
            connection.sendall(b'error\n')
          except OSError:
            pass
        except OSError:
          pass

  def close(self) -> None:
    self.stopped.set()
    if self.socket is not None:
      self.socket.close()
      self.socket = None
    if self.thread is not None:
      self.thread.join(timeout=TIMEOUT * 2)
      self.thread = None
