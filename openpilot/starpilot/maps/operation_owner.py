"""Single parked owner for named offline Mapd snapshot preparation and selection."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid

from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.starpilot.maps import shadow_lifecycle as shadow
from openpilot.starpilot.maps.artifact import elf_arm64_static_file, source_digest
from openpilot.starpilot.maps.storage import offline_root
from openpilot.starpilot.storage import starpilot_storage_root


OFFLINE_ROOT = offline_root()
SOCKET_PATH = starpilot_storage_root() / 'maps/operations.sock'
MAX_REQUEST = 4096
MAX_STATUS = 4096
MAX_CATALOG = 65536
MAX_REGION_GROUPS = 1024
MAX_TRANSFER_BYTES = 8 * 1024**3
MAX_NEW_DISK_BYTES = 16 * 1024**3
OPERATION_DEADLINE_S = 30 * 60
GENERATION = re.compile(r'[0-9a-f]{64}\Z')
TOKEN = re.compile(r'(?:nation|us_state)\.[A-Za-z0-9_-]{1,64}\Z')
ACTIVE = frozenset(('transferring', 'validating', 'selecting'))
STATES = frozenset(('idle', 'transferring', 'validating', 'selecting', 'completed',
                    'canceled', 'failed', 'interrupted', 'unavailable'))
ERROR_CODES = frozenset(('invalid_request', 'invalid_region', 'invalid_budget', 'busy', 'selection_changed',
                         'not_parked', 'operation_changed', 'package_unavailable', 'process_failed',
                         'canceled', 'interrupted', 'unavailable'))


class MapOperationUnavailable(RuntimeError):
  pass


class OperationError(Exception):
  def __init__(self, code: str):
    if code not in ERROR_CODES:
      code = 'unavailable'
    self.code = code
    super().__init__(code)


def _unique_object(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError('duplicate JSON field')
    result[key] = value
  return result


def _parse(raw: bytes, limit: int) -> dict:
  if len(raw) > limit:
    raise ValueError('message too large')
  value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
  if type(value) is not dict:
    raise ValueError('message is not an object')
  return value


def _encode(value: dict, limit: int) -> bytes:
  raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
  if len(raw) > limit:
    raise MapOperationUnavailable('bounded operation response exceeded')
  return struct.pack('!I', len(raw)) + raw


def _receive(sock: socket.socket, limit: int, *, deadline_s: float = 5.0) -> dict:
  deadline = time.monotonic() + deadline_s
  original_timeout = sock.gettimeout()

  def exact(size: int) -> bytes:
    output = bytearray()
    while len(output) < size:
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise MapOperationUnavailable('operation request deadline expired')
      sock.settimeout(remaining)
      chunk = sock.recv(size - len(output))
      if not chunk:
        raise MapOperationUnavailable('operation connection closed')
      output.extend(chunk)
    return bytes(output)
  try:
    length = struct.unpack('!I', exact(4))[0]
    if length == 0 or length > limit:
      raise MapOperationUnavailable('operation message size invalid')
    return _parse(exact(length), limit)
  except (UnicodeError, ValueError, TypeError, RecursionError) as error:
    raise MapOperationUnavailable('operation message invalid') from error
  finally:
    sock.settimeout(original_timeout)


def request_operation(request: dict, *, socket_path: Path | None = None, timeout_s: float = 8.0) -> dict:
  """Local v1 client. Return accepted/rejected envelopes; raise only for transport/protocol failure."""
  path = SOCKET_PATH if socket_path is None else socket_path
  if type(request) is not dict:
    raise MapOperationUnavailable('operation request must be an object')
  if not 0 < timeout_s <= 10:
    raise MapOperationUnavailable('invalid operation timeout')
  try:
    payload = _encode(request, MAX_REQUEST)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
      client.settimeout(timeout_s)
      client.connect(str(path))
      client.sendall(payload)
      response = _receive(client, MAX_CATALOG if request.get('op') == 'catalog' else MAX_STATUS,
                          deadline_s=timeout_s)
  except (OSError, TimeoutError, ValueError, TypeError) as error:
    raise MapOperationUnavailable('map operation owner unavailable') from error
  if (type(response.get('version')) is not int or response['version'] != 1 or
      type(response.get('ok')) is not bool or
      set(response) != ({'version', 'ok', 'result'} if response['ok'] else {'version', 'ok', 'error'}) or
      type(response['result' if response['ok'] else 'error']) is not dict):
    raise MapOperationUnavailable('map operation reply malformed')
  return response


def _envelope(result: dict | None = None, error: str | None = None) -> dict:
  if error is not None:
    return {'version': 1, 'ok': False, 'error': {'code': error, 'message': error.replace('_', ' ')}}
  return {'version': 1, 'ok': True, 'result': result or {}}


def _selected_generation(root: Path = OFFLINE_ROOT) -> str:
  selector = root / 'current.json'
  try:
    with shadow._open_regular(selector, max_bytes=shadow.MAX_SELECTOR_BYTES) as file:
      value = _parse(file.read(shadow.MAX_SELECTOR_BYTES + 1), shadow.MAX_SELECTOR_BYTES)
  except FileNotFoundError:
    return ''
  if (set(value) != {'version', 'generation'} or type(value['version']) is not int or value['version'] != 1 or
      type(value['generation']) is not str or GENERATION.fullmatch(value['generation']) is None or
      not shadow._selected_snapshot(root)):
    raise OperationError('unavailable')
  return value['generation']


def _package_ready() -> bool:
  """Check the fixed generated Go package without requiring a prior map selection."""
  binary, manifest_path, package = shadow.PROVIDER_BINARY, shadow.PROVIDER_MANIFEST, shadow.PROVIDER_DIR
  checkout = Path(BASEDIR)
  try:
    if not shadow._owned_package(binary, manifest_path, package, checkout):
      return False
    with shadow._open_regular(manifest_path, max_bytes=shadow.MAX_MANIFEST_BYTES) as file:
      manifest = _parse(file.read(shadow.MAX_MANIFEST_BYTES + 1), shadow.MAX_MANIFEST_BYTES)
    if (type(manifest.get('schemaVersion')) is not int or manifest['schemaVersion'] != 1 or
        manifest.get('goVersion') != 'go1.25.1' or manifest.get('target') != 'linux-arm64-static'):
      return False
    for name, pattern in (('sourceRevision', r'[0-9a-f]{40}'), ('upstreamRevision', r'[0-9a-f]{40}'),
                          ('sourceDigest', r'[0-9a-f]{64}'), ('binarySha256', r'[0-9a-f]{64}')):
      value = manifest.get(name)
      if type(value) is not str or re.fullmatch(pattern, value) is None:
        return False
    if manifest['sourceDigest'] != source_digest(checkout / 'mapd_repo'):
      return False
    pin = json.loads((checkout / 'upstream-sync.json').read_text())
    revision = next(item['commit'] for item in pin['dependencies'] if item['path'] == 'mapd_repo')
    if manifest['upstreamRevision'] != revision:
      return False
    digest = hashlib.sha256()
    stamps = {manifest['sourceDigest'].encode(), revision.encode(), manifest['sourceRevision'].encode()}
    seen, carry = set(), b''
    with shadow._open_regular(binary, executable=True) as file:
      if not elf_arm64_static_file(file):
        return False
      file.seek(0)
      for chunk in iter(lambda: file.read(1024 * 1024), b''):
        digest.update(chunk)
        window = carry + chunk
        seen.update(stamp for stamp in stamps if stamp in window)
        carry = window[-128:]
    return digest.hexdigest() == manifest['binarySha256'] and stamps == seen
  except (OSError, ValueError, KeyError, StopIteration, TypeError, OverflowError, RecursionError):
    return False



class _PackageReadinessCache:
  """Verify package bytes once per source/package/index metadata identity."""

  def __init__(self, validate, watch):
    self._validate, self._watch = validate, watch
    self._paths: tuple[Path, ...] | None = None
    self._identity: tuple | None = None
    self._ready = False
    self._lock = threading.RLock()

  @staticmethod
  def _stamp(paths: tuple[Path, ...]) -> tuple:
    result = []
    for path in paths:
      try:
        info = path.lstat()
      except FileNotFoundError:
        result.append((str(path), None))
      else:
        result.append((str(path), info.st_dev, info.st_ino, info.st_size,
                       info.st_mtime_ns, info.st_ctime_ns, info.st_mode))
    return tuple(result)

  def __call__(self) -> bool:
    with self._lock:
      try:
        if self._paths is not None and self._stamp(self._paths) == self._identity:
          return self._ready
        self._ready = False
        paths = self._watch()
        before = self._stamp(paths)
        ready = bool(self._validate())
        after = self._stamp(paths)
        if before != after:
          self._paths, self._identity = None, None
          return False
        self._paths, self._identity, self._ready = paths, after, ready
        return ready
      except (OSError, ValueError):
        self._paths, self._identity, self._ready = None, None, False
        return False


def _package_watch_paths() -> tuple[Path, ...]:
  checkout = Path(BASEDIR)
  source = checkout / 'mapd_repo'
  paths = {shadow.PROVIDER_DIR, shadow.PROVIDER_BINARY, shadow.PROVIDER_MANIFEST,
           checkout / 'upstream-sync.json', source, checkout / '.git'}
  git = checkout / '.git'
  if git.is_file():
    with shadow._open_regular(git, max_bytes=4096) as file:
      value = file.read(4097).decode('utf-8').strip()
    if not value.startswith('gitdir: '):
      raise ValueError('invalid Git directory identity')
    git = (checkout / value[8:]).resolve()
  paths.add(git / 'index')
  # Cache all source directory and file identities: existing-file edits as well
  # as additions/removals invalidate the full sourceDigest verification. Warm
  # reads stat these known paths; they do not rehash/re-enumerate the source.
  if source.exists():
    for path in source.rglob('*'):
      if path.name == '.DS_Store' or path.relative_to(source).parts[0] in ('.git', 'build', 'media', 'offline'):
        continue
      if path.is_symlink():
        raise ValueError('provider source symlink is not cacheable')
      paths.add(path)
      if len(paths) > 4096:
        raise ValueError('provider source inventory exceeds bound')
  return tuple(sorted(paths))


_PACKAGE_READY_CACHE = _PackageReadinessCache(_package_ready, _package_watch_paths)


def cached_package_ready() -> bool:
  return _PACKAGE_READY_CACHE()

def _catalog() -> list[dict]:
  try:
    completed = subprocess.run([str(shadow.PROVIDER_BINARY), '--snapshot-catalog'], capture_output=True,
                               timeout=5, check=True)
    if len(completed.stdout) > MAX_CATALOG or len(completed.stderr) > MAX_STATUS:
      raise ValueError('catalog output exceeds bound')
    rows = json.loads(completed.stdout, object_pairs_hook=_unique_object)
    if type(rows) is not list or len(rows) != 229:
      raise ValueError('catalog shape invalid')
    seen = set()
    for row in rows:
      if (type(row) is not dict or type(row.get('token')) is not str or TOKEN.fullmatch(row['token']) is None or
          row['token'] in seen or type(row.get('name')) is not str or not 0 < len(row['name']) <= 128 or
          type(row.get('bounds')) is not list or len(row['bounds']) != 4 or
          any(type(value) not in (int, float) or not math.isfinite(value) for value in row['bounds']) or
          type(row.get('groups')) is not int or not 0 <= row['groups'] <= 65536 or
          type(row.get('available')) is not bool or
          (row['available'] and not 1 <= row['groups'] <= MAX_REGION_GROUPS)):
        raise ValueError('catalog row invalid')
      seen.add(row['token'])
    return rows
  except (OSError, subprocess.SubprocessError, ValueError, TypeError) as error:
    raise OperationError('package_unavailable') from error


def _package_identity() -> tuple:
  try:
    return tuple((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                 for info in (shadow.PROVIDER_BINARY.stat(), shadow.PROVIDER_MANIFEST.stat()))
  except OSError as error:
    raise OperationError('package_unavailable') from error


def _guarded_go_command(binary: Path, args: list[str]) -> list[str]:
  guard = '; '.join(("import ctypes,os,signal,sys", "rc=ctypes.CDLL(None).prctl(1,signal.SIGKILL,0,0,0)",
                     "rc==0 or sys.exit(125)", "os.getppid()==int(sys.argv[1]) or sys.exit(125)",
                     "os.execv(sys.argv[2],sys.argv[2:])"))
  return [sys.executable, '-c', guard, str(os.getpid()), str(binary), *args]


def _ensure_owned_offline_root(root: Path) -> None:
  if not root.is_absolute():
    raise OperationError('unavailable')
  for directory in reversed((root, *root.parents)):
    try:
      info = directory.lstat()
    except FileNotFoundError:
      directory.mkdir(mode=0o700)
      info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
      raise OperationError('unavailable')
  info = root.stat()
  if info.st_uid != os.getuid() or info.st_mode & 0o077:
    raise OperationError('unavailable')


class MapSnapshotOperationOwner:
  """One active prepare/select transaction with independent parked authority."""

  def __init__(self, *, root: Path | None = None, marker: Path | None = None, catalog=None,
               parked=None, selected=None, execute=None, package_ready=None, snapshot_info=None):
    self.root = offline_root() if root is None else root
    self.marker = marker
    self._catalog_loader = _catalog if catalog is None else catalog
    self._parked = (lambda: False) if parked is None else parked
    self._inventory = None
    self._selected = self._disk_generation if selected is None else selected
    self._snapshot_info = self._disk_snapshot_info if snapshot_info is None else snapshot_info
    self._execute = self._run_go if execute is None else execute
    self._package_ready = cached_package_ready if package_ready is None else package_ready
    self._lock = threading.RLock()
    self._session = uuid.uuid4().hex
    self._sequence = 0
    self._cancel = threading.Event()
    self._closed = False
    self._child: subprocess.Popen | None = None
    self._worker: threading.Thread | None = None
    self._rows: list[dict] | None = None
    self._rows_identity: tuple | None = None
    self._status = self._fresh_status('idle')
    self._marker_event = threading.Event()
    self._marker_stop = threading.Event()
    self._marker_pending: dict | None = None
    self._restore_marker()
    self._marker_worker = threading.Thread(target=self._marker_loop, daemon=True) if marker is not None else None
    if self._marker_worker is not None:
      self._marker_worker.start()

  def _fresh_status(self, state: str) -> dict:
    return {'schemaVersion': 1, 'ownerSession': self._session, 'operationId': None, 'state': state,
            'regionToken': None, 'bounds': None, 'completedGroups': 0, 'totalGroups': 0,
            'transferredBytes': 0, 'transferBudgetBytes': 0, 'preparedGeneration': None,
            'selectedGeneration': '', 'errorCode': None, 'selectedForNextShadowStart': False}

  def _persist(self) -> None:
    if self.marker is None:
      return
    value = {name: self._status[name] for name in ('schemaVersion', 'ownerSession', 'operationId', 'state', 'regionToken')}
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    if len(raw) > 512:
      raise OperationError('unavailable')
    with self._lock:
      self._marker_pending = value
    self._marker_event.set()

  def _marker_loop(self) -> None:
    while True:
      self._marker_event.wait()
      with self._lock:
        value = self._marker_pending
        self._marker_pending = None
        self._marker_event.clear()
      if value is None:
        if self._marker_stop.is_set():
          return
        continue
      try:
        self._write_marker(value)
      except OSError:
        # The selector transaction is governed by Go's independently fsynced CAS.
        # This marker is recovery telemetry, never authority to select a snapshot.
        pass
      if self._marker_stop.is_set():
        with self._lock:
          if self._marker_pending is None:
            return

  def _write_marker(self, value: dict) -> None:
    marker = self.marker
    if marker is None:
      return
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    temp = marker.with_name(f'.operation-{self._session}')
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
      os.write(fd, raw)
      os.fsync(fd)
    finally:
      os.close(fd)
    try:
      os.replace(temp, marker)
      dir_fd = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY)
      try:
        os.fsync(dir_fd)
      finally:
        os.close(dir_fd)
    finally:
      temp.unlink(missing_ok=True)

  def _restore_marker(self) -> None:
    if self.marker is None:
      return
    try:
      with shadow._open_regular(self.marker, max_bytes=512) as file:
        saved = _parse(file.read(513), 512)
      if saved.get('state') in ACTIVE:
        self._status = self._fresh_status('interrupted')
        self._status['errorCode'] = 'interrupted'
        self._persist()
    except FileNotFoundError:
      pass
    except (OSError, ValueError, TypeError, OperationError):
      self._status = self._fresh_status('unavailable')
      self._status['errorCode'] = 'unavailable'

  @staticmethod
  def _disk_identity(paths: tuple[Path, ...]) -> tuple:
    return tuple((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                 for info in (path.lstat() for path in paths))

  def _disk_snapshot_info(self) -> dict | None:
    try:
      with shadow._open_regular(self.root / 'current.json', max_bytes=shadow.MAX_SELECTOR_BYTES) as file:
        selector = _parse(file.read(shadow.MAX_SELECTOR_BYTES + 1), shadow.MAX_SELECTOR_BYTES)
      generation = selector.get('generation')
      if (set(selector) != {'version', 'generation'} or type(selector['version']) is not int or
          selector['version'] != 1 or type(generation) is not str or GENERATION.fullmatch(generation) is None):
        self._inventory = None
        return None
      saved = self._inventory
      if saved is not None and saved[0]['generation'] == generation and self._disk_identity(saved[1]) == saved[2]:
        return dict(saved[0])
      self._inventory = None
      info = shadow.selected_snapshot_info(self.root)
      receipt = shadow._selected_receipt(self.root)
      if info is None or receipt is None or receipt[0] != generation:
        return None
      directory = self.root / 'generations' / generation
      # Go installs immutable generations. Watch their receipt and directory
      # index identities; do not rescan/hash every tile on each HTTP poll.
      paths = {self.root, self.root / 'current.json', self.root / 'generations',
               directory, directory / 'receipt.json'}
      for tile in receipt[1]['tiles']:
        parent = directory / Path(tile['path']).parent
        paths.update((parent, parent.parent))
      ordered = tuple(sorted(paths))
      identity = self._disk_identity(ordered)
      self._inventory = info, ordered, identity
      return dict(info)
    except (OSError, ValueError, TypeError, OperationError):
      self._inventory = None
      return None

  def _disk_generation(self) -> str:
    info = self._disk_snapshot_info()
    if info is None and (self.root / 'current.json').exists():
      raise OperationError('unavailable')
    return info['generation'] if info is not None else ''

  def _selected_info(self, generation: str) -> dict | None:
    if not generation:
      return None
    info = self._snapshot_info()
    if (type(info) is not dict or info.get('generation') != generation or
        type(info.get('storedBytes')) is not int or info['storedBytes'] <= 0 or
        type(info.get('tileCount')) is not int or info['tileCount'] <= 0):
      raise OperationError('unavailable')
    return info

  def snapshot(self) -> dict:
    self._parked()
    with self._lock:
      result = dict(self._status)
    try:
      result['selectedGeneration'] = self._selected()
      result['selectedSnapshot'] = self._selected_info(result['selectedGeneration'])
    except (OSError, ValueError, OperationError):
      result['state'] = 'unavailable'
      result['errorCode'] = 'unavailable'
      result['selectedSnapshot'] = None
      result['selectedGeneration'] = ''
    result['selectedForNextShadowStart'] = bool(result['state'] == 'completed' and
                                                 result['preparedGeneration'] == result['selectedGeneration'])
    return result

  def setup(self) -> dict:
    """Read prerequisites without changing a selection or starting a transfer."""
    ready = self._package_ready()
    state = 'ready' if ready else ('missing_binary' if not shadow.PROVIDER_BINARY.exists() else
                                  'missing_manifest' if not shadow.PROVIDER_MANIFEST.exists() else 'invalid_package')
    try:
      generation = self._selected()
      info = self._selected_info(generation)
    except (OSError, ValueError, OperationError):
      generation, info = '', None
    ancestor = self.root
    while not ancestor.exists() and ancestor != ancestor.parent:
      ancestor = ancestor.parent
    try:
      free = shutil.disk_usage(ancestor).free
    except OSError:
      free = 0
    return {'schemaVersion': 1, 'packageReady': ready, 'packageState': state,
            'snapshotReady': info is not None, 'selectedGeneration': generation, 'selectedSnapshot': info,
            'parked': bool(self._parked()), 'freeDiskBytes': free,
            'maxTransferBytes': MAX_TRANSFER_BYTES, 'maxNewDiskBytes': MAX_NEW_DISK_BYTES}

  def catalog(self) -> dict:
    with self._lock:
      identity = _package_identity() if self._catalog_loader is _catalog else None
      if self._rows is None or identity != self._rows_identity:
        if not self._package_ready():
          raise OperationError('package_unavailable')
        self._rows = self._catalog_loader()
        self._rows_identity = identity
      rows = self._rows
    return {'regions': rows, 'maxGroups': MAX_REGION_GROUPS, 'maxTransferBytes': MAX_TRANSFER_BYTES,
            'maxNewDiskBytes': MAX_NEW_DISK_BYTES, 'selectedGeneration': self.snapshot()['selectedGeneration']}

  def start(self, request: dict) -> dict:
    allowed = {'version', 'op', 'regionToken', 'maxTransferBytes', 'maxNewDiskBytes', 'expectedCurrentGeneration'}
    if set(request) != allowed:
      raise OperationError('invalid_request')
    token, transfer, disk, expected = (request['regionToken'], request['maxTransferBytes'],
                                       request['maxNewDiskBytes'], request['expectedCurrentGeneration'])
    if type(token) is not str or TOKEN.fullmatch(token) is None:
      raise OperationError('invalid_region')
    if (type(transfer) is not int or not 0 < transfer <= MAX_TRANSFER_BYTES or
        type(disk) is not int or not 0 < disk <= MAX_NEW_DISK_BYTES):
      raise OperationError('invalid_budget')
    if type(expected) is not str or (expected and GENERATION.fullmatch(expected) is None):
      raise OperationError('invalid_request')
    if not self._package_ready():
      raise OperationError('package_unavailable')
    with self._lock:
      if self._closed:
        raise OperationError('unavailable')
      if self._status['state'] in ACTIVE:
        raise OperationError('busy')
      if not self._parked():
        raise OperationError('not_parked')
      rows = self.catalog()['regions']
      region = next((row for row in rows if row['token'] == token), None)
      if region is None or not region['available']:
        raise OperationError('invalid_region')
      if self._selected() != expected:
        raise OperationError('selection_changed')
      try:
        _ensure_owned_offline_root(self.root)
      except OSError as error:
        raise OperationError('unavailable') from error
      self._sequence += 1
      self._cancel = threading.Event()
      self._status = self._fresh_status('transferring')
      self._status.update(operationId=f'{self._session}:{self._sequence}', regionToken=token,
                          bounds=region['bounds'], totalGroups=region['groups'], transferBudgetBytes=transfer)
      try:
        self._persist()
      except (OSError, OperationError):
        self._status = self._fresh_status('unavailable')
        self._status['errorCode'] = 'unavailable'
        raise OperationError('unavailable') from None
      self._worker = threading.Thread(target=self._work, args=(token, transfer, disk, expected), daemon=True)
      self._worker.start()
      return self.snapshot()

  def cancel(self, operation_id: str) -> dict:
    if type(operation_id) is not str or len(operation_id) > 80:
      raise OperationError('invalid_request')
    with self._lock:
      if self._status['operationId'] != operation_id:
        raise OperationError('operation_changed')
      if self._status['state'] in ACTIVE:
        self._cancel.set()
        if self._child is not None:
          self._terminate_child(self._child)
      return self.snapshot()

  def close(self) -> None:
    with self._lock:
      self._closed = True
      self._cancel.set()
      if self._child is not None:
        self._terminate_child(self._child)
      worker = self._worker
    if worker is not None:
      worker.join(timeout=5)
    self._marker_stop.set()
    self._marker_event.set()
    if self._marker_worker is not None:
      self._marker_worker.join(timeout=2)

  @staticmethod
  def _terminate_child(child: subprocess.Popen) -> None:
    if child.poll() is None:
      try:
        os.killpg(child.pid, signal.SIGINT)
      except ProcessLookupError:
        pass

  def _run_go(self, mode: str, args: list[str], progress) -> str:
    if self._cancel.is_set():
      raise OperationError('canceled')
    if not self._parked():
      raise OperationError('not_parked')
    command = _guarded_go_command(shadow.PROVIDER_BINARY, [mode, *args])
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    with self._lock:
      self._child = child
    output = bytearray()
    lines = []
    deadline = time.monotonic() + OPERATION_DEADLINE_S
    try:
      with selectors.DefaultSelector() as selector:
        if child.stdout is None or child.stderr is None:
          raise OperationError('process_failed')
        selector.register(child.stdout, selectors.EVENT_READ)
        selector.register(child.stderr, selectors.EVENT_READ)
        stderr_size = 0
        stopped_at = None
        exited_at = None
        while selector.get_map() or child.poll() is None:
          now = time.monotonic()
          if child.poll() is not None:
            exited_at = now if exited_at is None else exited_at
            if now - exited_at > 2:
              raise OperationError('process_failed')
          if self._cancel.is_set() or not self._parked() or now > deadline:
            if stopped_at is None:
              self._terminate_child(child)
              stopped_at = now
            elif now - stopped_at > 2 and child.poll() is None:
              try:
                os.killpg(child.pid, signal.SIGKILL)
              except ProcessLookupError:
                pass
          for key, _ in selector.select(timeout=.1):
            chunk = os.read(key.fd, 4096)
            if not chunk:
              selector.unregister(key.fileobj)
            elif key.fileobj is child.stderr:
              stderr_size += len(chunk)
              if stderr_size > 64 * 1024:
                raise OperationError('process_failed')
            else:
              output.extend(chunk)
              if len(output) > 4096:
                raise OperationError('process_failed')
              while b'\n' in output:
                line, _, rest = output.partition(b'\n')
                output = bytearray(rest)
                lines.append(_parse(bytes(line), 4096))
                if len(lines) > 2 + self._status['totalGroups'] + 8:
                  raise OperationError('process_failed')
                progress(lines[-1])
        code = child.wait(timeout=2)
        if output:
          raise OperationError('process_failed')
    finally:
      if child.poll() is None:
        self._terminate_child(child)
        try:
          child.wait(timeout=2)
        except subprocess.TimeoutExpired:
          try:
            os.killpg(child.pid, signal.SIGKILL)
          except ProcessLookupError:
            pass
          child.wait(timeout=2)
      if child.stdout is not None:
        child.stdout.close()
      if child.stderr is not None:
        child.stderr.close()
      with self._lock:
        if self._child is child:
          self._child = None
    if code != 0 or not lines:
      raise OperationError('canceled' if self._cancel.is_set() else
                           'not_parked' if not self._parked() else 'process_failed')
    final = lines[-1]
    expected_phase = 'prepared' if mode == '--snapshot-managed-prepare' else 'selected'
    generation = final.get('generation')
    if (final.get('phase') != expected_phase or type(generation) is not str or
        GENERATION.fullmatch(generation) is None):
      raise OperationError('process_failed')
    # A verified select receipt means Go committed the selector CAS. A cancel
    # racing after that commit cannot truthfully turn it back into "canceled".
    if mode != '--snapshot-select':
      if self._cancel.is_set():
        raise OperationError('canceled')
      if not self._parked():
        raise OperationError('not_parked')
    return generation

  def _progress(self, event: dict) -> None:
    if type(event) is not dict or type(event.get('phase')) is not str:
      raise OperationError('process_failed')
    phase = event['phase']
    if phase not in ('transferring', 'validating', 'prepared', 'selected'):
      raise OperationError('process_failed')
    with self._lock:
      if phase in ('transferring', 'validating'):
        completed, total = event.get('completedGroups'), event.get('totalGroups')
        transferred, budget = event.get('transferredBytes'), event.get('transferBudgetBytes')
        previous_completed = self._status.get('completedGroups')
        previous_transferred = self._status.get('transferredBytes')
        if (type(completed) is not int or type(total) is not int or type(transferred) is not int or
            type(budget) is not int or type(previous_completed) is not int or type(previous_transferred) is not int):
          raise OperationError('process_failed')
        if (total != self._status['totalGroups'] or budget != self._status['transferBudgetBytes'] or
            not previous_completed <= completed <= total or
            not previous_transferred <= transferred <= budget):
          raise OperationError('process_failed')
        self._status.update(state=phase, completedGroups=completed, transferredBytes=transferred)
        self._persist()

  def _work(self, token: str, transfer: int, disk: int, expected: str) -> None:
    prepared = None
    selection_acknowledged = False
    try:
      prepared = self._execute('--snapshot-managed-prepare',
                               ['--offline-root', str(self.root), '--region', token,
                                '--max-transfer-bytes', str(transfer), '--max-new-disk-bytes', str(disk)], self._progress)
      if type(prepared) is not str or GENERATION.fullmatch(prepared) is None:
        raise OperationError('process_failed')
      with self._lock:
        self._status.update(state='selecting', preparedGeneration=prepared)
        self._persist()
      if self._cancel.is_set() or not self._parked():
        raise OperationError('canceled' if self._cancel.is_set() else 'not_parked')
      selected = self._execute('--snapshot-select',
                               ['--offline-root', str(self.root), '--generation', prepared,
                                '--expected-current', expected], self._progress)
      if selected != prepared:
        raise OperationError('process_failed')
      selection_acknowledged = True
      if self._selected() != prepared:
        raise OperationError('selection_changed')
      self._selected_info(prepared)
      with self._lock:
        self._status.update(state='completed', selectedGeneration=prepared, errorCode=None)
        self._persist()
    except Exception as error:
      code = error.code if isinstance(error, OperationError) else 'process_failed'
      try:
        selected = self._selected()
        self._selected_info(selected)
      except (OSError, ValueError, OperationError):
        selected = None
      if selection_acknowledged and prepared is not None and selected == prepared:
        state, code = 'completed', None
      else:
        state = 'canceled' if code == 'canceled' else 'failed'
        if self._closed and state == 'canceled':
          state, code = 'interrupted', 'interrupted'
        if selected is not None and selected != expected and code == 'process_failed':
          code = 'selection_changed'
      with self._lock:
        self._status.update(state=state, errorCode=code, selectedGeneration=selected or '')
        self._persist()

  def dispatch(self, request: dict) -> dict:
    if (type(request) is not dict or type(request.get('version')) is not int or
        request['version'] != 1 or type(request.get('op')) is not str):
      raise OperationError('invalid_request')
    op = request['op']
    if op in ('catalog', 'status', 'setup') and set(request) != {'version', 'op'}:
      raise OperationError('invalid_request')
    if op == 'setup':
      return self.setup()
    if op == 'catalog':
      return self.catalog()
    if op == 'status':
      return self.snapshot()
    if op == 'start':
      return self.start(request)
    if op == 'cancel' and set(request) == {'version', 'op', 'operationId'}:
      return self.cancel(request['operationId'])
    raise OperationError('invalid_request')


def _owned_socket_dir(path: Path) -> None:
  directory = path.parent
  for ancestor in (directory, *directory.parents):
    if ancestor.is_symlink():
      raise MapOperationUnavailable('map operation socket ancestor unsafe')
  directory.mkdir(mode=0o700, parents=True, exist_ok=True)
  if (directory.is_symlink() or not stat.S_ISDIR(directory.lstat().st_mode) or
      directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077):
    raise MapOperationUnavailable('map operation socket directory unsafe')
  if len(os.fsencode(path)) >= 100:
    raise MapOperationUnavailable('map operation socket path too long')


def serve(owner: MapSnapshotOperationOwner, *, socket_path: Path = SOCKET_PATH,
          stop: threading.Event | None = None) -> None:
  if not hasattr(socket, 'SO_PEERCRED'):
    raise MapOperationUnavailable('map operation peer credentials unavailable')
  _owned_socket_dir(socket_path)
  lock_fd = os.open(socket_path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
  try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if socket_path.exists() or socket_path.is_symlink():
      info = socket_path.lstat()
      if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise MapOperationUnavailable('map operation socket path unsafe')
      socket_path.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
      server.bind(str(socket_path))
      os.chmod(socket_path, 0o600)
      server.listen(4)
      server.settimeout(.2)
      try:
        while stop is None or not stop.is_set():
          try:
            peer, _ = server.accept()
          except TimeoutError:
            continue
          with peer:
            peer.settimeout(1)
            request = {}
            try:
              _, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
              if uid != os.getuid():
                continue
              request = _receive(peer, MAX_REQUEST, deadline_s=1.5)
              result = _envelope(owner.dispatch(request))
            except OperationError as error:
              result = _envelope(error=error.code)
            except (MapOperationUnavailable, OSError, ValueError, TypeError):
              result = _envelope(error='invalid_request')
            try:
              peer.sendall(_encode(result, MAX_CATALOG if request.get('op') == 'catalog' else MAX_STATUS))
            except (OSError, MapOperationUnavailable):
              continue
      finally:
        socket_path.unlink(missing_ok=True)
  finally:
    os.close(lock_fd)



_SLC_READINESS_OWNER: MapSnapshotOperationOwner | None = None
_SLC_READINESS_LOCK = threading.RLock()


def maps_slc_available() -> bool:
  """Settings-only readiness using the canonical package and snapshot caches."""
  global _SLC_READINESS_OWNER
  with _SLC_READINESS_LOCK:
    if _SLC_READINESS_OWNER is None:
      # No marker, transfer, process, parked authority or worker is created.
      _SLC_READINESS_OWNER = MapSnapshotOperationOwner()
    owner = _SLC_READINESS_OWNER
    if not owner._package_ready():
      return False
    try:
      return bool(owner._selected())
    except (OSError, ValueError, OperationError):
      return False

def main() -> None:
  if sys.platform != 'linux':
    raise MapOperationUnavailable('managed map owner requires Linux')
  from openpilot.starpilot.galaxy.settings import LiveContextSource
  params = Params()
  evidence = LiveContextSource(params)
  try:
    evidence.parked()
    _owned_socket_dir(SOCKET_PATH)
    owner = MapSnapshotOperationOwner(marker=SOCKET_PATH.parent / 'operation-status.json', parked=evidence.parked)
    try:
      serve(owner)
    finally:
      owner.close()
  finally:
    evidence.close()


if __name__ == '__main__':
  main()
