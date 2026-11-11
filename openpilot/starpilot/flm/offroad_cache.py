"""Small, user-owned immutable assessment cache; contains NO live authority."""

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

MAX_DOCUMENT = 1024 * 1024
MAX_FILES = 128
MAX_TOTAL = 64 * 1024 * 1024
MAX_CHECKPOINTS = 8


def _pairs(rows):
  result = {}
  for key, value in rows:
    if key in result:
      raise ValueError('Duplicate cache field')
    result[key] = value
  return result


def directory_for_params(params):
  """Persistent user-owned cache, isolated by the actual Params namespace."""
  namespace = Path(params.get_param_path('AutomaticSteeringProfiles')).parent.resolve(strict=True)
  info = namespace.stat()
  if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
    raise ValueError('Actual Params namespace must belong to the runtime user')
  identity = hashlib.sha256(os.fsencode(namespace)).hexdigest()[:24]
  return namespace.parent / ('.flm-offroad-' + identity)


class AssessmentCache:
  def __init__(self, directory):
    self.directory = Path(directory)
    self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    self._owned()

  def _owned(self):
    s = self.directory.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.geteuid() or s.st_mode & 0o022:
      raise ValueError('Private assessment directory ownership changed')

  def _read(self, name, maximum=MAX_DOCUMENT):
    self._owned()
    fd = os.open(self.directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
      before = os.fstat(fd)
      if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_mode & 0o022 or not 0 < before.st_size <= maximum:
        raise ValueError('Invalid assessment cache file')
      raw = os.read(fd, maximum + 1)
      after = os.fstat(fd)
      if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or len(
        raw
      ) != before.st_size:
        raise ValueError('Assessment cache changed')
    finally:
      os.close(fd)
    return raw

  def get(self, digest):
    if type(digest) is not str or len(digest) != 64 or digest.strip('0123456789abcdef'):
      raise ValueError('Exact assessment hash required')
    raw = self._read(digest + '.json')
    if hashlib.sha256(raw).hexdigest() != digest:
      raise ValueError('Assessment content changed')
    value = json.loads(raw, object_pairs_hook=_pairs)
    if type(value) is not dict:
      raise ValueError('Assessment object required')
    return value

  def _write(self, name, raw):
    self._owned()
    if not 0 < len(raw) <= MAX_DOCUMENT:
      raise ValueError('Assessment document bound')
    temporary = None
    try:
      fd, temporary = tempfile.mkstemp(prefix='.assessment-', dir=self.directory)
      with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
      self._owned()
      os.replace(temporary, self.directory / name)
      fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
      try:
        os.fsync(fd)
      finally:
        os.close(fd)
    finally:
      if temporary is not None:
        Path(temporary).unlink(missing_ok=True)

  def put(self, value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    if (self.directory / (digest + '.json')).exists():
      if self._read(digest + '.json') != raw:
        raise ValueError('Assessment hash collision')
      return digest
    self._capacity(len(raw))
    self._write(digest + '.json', raw)
    return digest

  def _capacity(self, size):
    total = count = 0
    for p in self.directory.iterdir():
      if p.name in ('latest.json', 'inputs.json'):
        continue
      if not p.name.endswith('.json'):
        raise ValueError('Unexpected assessment directory contents')
      s = p.lstat()
      if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid():
        raise ValueError('Foreign assessment file')
      total += s.st_size
      count += 1
    if count >= MAX_FILES or total + size > MAX_TOTAL:
      raise ValueError('Assessment cache full')

  def claim_trial(self, digest, session):
    """Durably consume one provisional start; any previous claim refuses."""
    if (
      type(digest) is not str
      or len(digest) != 64
      or digest.strip('0123456789abcdef')
      or type(session) is not str
      or len(session) != 32
      or session.strip('0123456789abcdef')
    ):
      raise ValueError('Exact trial analysis and source session required')
    self._owned()
    name = 'trial-' + digest + '.json'
    raw = json.dumps({'analysis_sha256': digest, 'vehicle_session': session}, sort_keys=True, separators=(',', ':')).encode()
    self._capacity(len(raw))
    try:
      fd = os.open(self.directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
      return False
    # An interrupted/partial claim deliberately remains consumed.
    with os.fdopen(fd, 'wb') as stream:
      stream.write(raw)
      stream.flush()
      os.fsync(stream.fileno())
    fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
      os.fsync(fd)
    finally:
      os.close(fd)
    return self._read(name) == raw

  @staticmethod
  def _checkpoint(value):
    if (
      type(value) is not dict
      or set(value) != {'input_fingerprint', 'result'}
      or type(value['input_fingerprint']) is not str
      or len(value['input_fingerprint']) != 64
      or value['input_fingerprint'].strip('0123456789abcdef')
      or type(value['result']) is not dict
      or any(type(value['result'].get(k)) is not str or len(value['result'][k]) > 512 for k in ('status', 'reason'))
    ):
      raise ValueError('Invalid completed-drive checkpoint')
    return value

  def latest(self):
    try:
      return self._checkpoint(json.loads(self._read('latest.json', 32768), object_pairs_hook=_pairs))
    except FileNotFoundError:
      return None

  def checkpoints(self):
    try:
      rows = json.loads(self._read('inputs.json', MAX_CHECKPOINTS * 32768 + 32), object_pairs_hook=_pairs)
    except FileNotFoundError:
      rows = []
    if type(rows) is not list or len(rows) > MAX_CHECKPOINTS:
      raise ValueError('Bounded completed-drive history required')
    rows = [self._checkpoint(row) for row in rows]
    if len({row['input_fingerprint'] for row in rows}) != len(rows):
      raise ValueError('Duplicate completed-drive fingerprint')
    latest = self.latest()
    if latest is not None:
      rows = [row for row in rows if row['input_fingerprint'] != latest['input_fingerprint']] + [latest]
    return rows[-MAX_CHECKPOINTS:]

  def checkpoint(self, value):
    self._checkpoint(value)
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > 32768:
      raise ValueError('Assessment checkpoint bound')
    rows = [row for row in self.checkpoints() if row['input_fingerprint'] != value['input_fingerprint']] + [value]
    self._write('inputs.json', json.dumps(rows[-MAX_CHECKPOINTS:], sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    self._write('latest.json', raw)

