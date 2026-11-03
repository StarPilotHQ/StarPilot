"""Fixed, opt-in manager ownership for the diagnostic Mapd shadow process."""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from opendbc.car.structs import car
from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.system.manager.process import NativeProcess, nativelauncher
from openpilot.starpilot.maps.artifact import elf_arm64_static_file, source_digest
from openpilot.starpilot.maps.storage import STORAGE_ANCHOR, offline_root as map_offline_root


PROVIDER_DIR = Path(BASEDIR) / 'openpilot/starpilot/maps/provider'
PROVIDER_BINARY = PROVIDER_DIR / 'mapd'
PROVIDER_MANIFEST = PROVIDER_DIR / 'manifest.json'
OFFLINE_ROOT = map_offline_root()
OPT_IN_KEY = 'MapdShadowEnabled'
MAX_MANIFEST_BYTES = 4096
MAX_SELECTOR_BYTES = 256
MAX_RECEIPT_BYTES = 16 << 20


def _open_regular(path: Path, *, max_bytes: int | None = None, executable: bool = False):
  descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
  try:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or (max_bytes is not None and info.st_size > max_bytes) or (executable and not info.st_mode & 0o111):
      raise ValueError('not an acceptable regular file')
    return os.fdopen(descriptor, 'rb')
  except Exception:
    os.close(descriptor)
    raise


def _owned_package(binary: Path, manifest: Path, package_dir: Path, checkout_root: Path) -> bool:
  if binary != package_dir / 'mapd' or manifest != package_dir / 'manifest.json':
    return False
  try:
    relative = package_dir.relative_to(checkout_root)
  except ValueError:
    return False
  current = checkout_root
  for part in ('.', *relative.parts):
    if part != '.':
      current = current / part
    try:
      if not stat.S_ISDIR(current.lstat().st_mode):
        return False
    except OSError:
      return False
  return True


def _owned_root(root: Path, anchor: Path) -> bool:
  if root != map_offline_root(anchor):
    return False
  for path in (anchor, root.parent.parent, root.parent, root):
    try:
      if not stat.S_ISDIR(path.lstat().st_mode):
        return False
    except OSError:
      return False
  return True


def _unique_object(pairs):
  result = {}
  for name, value in pairs:
    if name in result:
      raise ValueError('duplicate snapshot JSON field')
    result[name] = value
  return result


def _invalid_json_constant(_value):
  raise ValueError('nonfinite snapshot JSON number')


def _selected_receipt(root: Path):
  """Read the bounded, content-addressed receipt selected by Go."""
  try:
    with _open_regular(root / 'current.json', max_bytes=MAX_SELECTOR_BYTES) as file:
      selector = json.loads(file.read(MAX_SELECTOR_BYTES + 1), object_pairs_hook=_unique_object,
                            parse_constant=_invalid_json_constant)
    if (type(selector) is not dict or set(selector) != {'version', 'generation'} or
        type(selector['version']) is not int or selector['version'] != 1 or
        type(selector['generation']) is not str or re.fullmatch(r'[0-9a-f]{64}', selector['generation']) is None):
      return None
    generations = root / 'generations'
    generation = generations / selector['generation']
    if not stat.S_ISDIR(generations.lstat().st_mode) or not stat.S_ISDIR(generation.lstat().st_mode):
      return None
    with _open_regular(generation / 'receipt.json', max_bytes=MAX_RECEIPT_BYTES) as file:
      raw = file.read(MAX_RECEIPT_BYTES + 1)
    if hashlib.sha256(raw).hexdigest() != selector['generation']:
      return None
    receipt = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_json_constant)
    if (type(receipt) is not dict or receipt.get('version') != 1 or type(receipt.get('version')) is not int or
        receipt.get('tileSchema') != 'offline.capnp:0xda3a0d9284ca402f' or
        receipt.get('attribution') != '© OpenStreetMap contributors; https://www.openstreetmap.org/copyright (ODbL)' or
        receipt.get('sourceVerified') is not False or
        receipt.get('sourceKind') not in ('local-pbf', 'provided-group-archives') or
        type(receipt.get('tiles')) is not list or not 0 < len(receipt['tiles']) <= 65536 or
        type(receipt.get('inputs')) is not list or not receipt['inputs']):
      return None
    return selector['generation'], receipt, len(raw)
  except (OSError, ValueError, TypeError, OverflowError, RecursionError):
    return None



def _selected_snapshot(root: Path) -> bool:
  """Check bounded receipt identity; Go checks each requested tile lazily."""
  return _selected_receipt(root) is not None


def selected_snapshot_info(root: Path) -> dict | None:
  """Disk inventory of the selected package; Go verifies tile content at selection/use."""
  selected = _selected_receipt(root)
  if selected is None:
    return None
  generation_id, receipt, receipt_size = selected
  generation = root / 'generations' / generation_id
  total = receipt_size
  seen = set()
  try:
    if not stat.S_ISDIR(root.lstat().st_mode):
      return None
    for tile in receipt['tiles']:
      if (type(tile) is not dict or type(tile.get('path')) is not str or type(tile.get('size')) is not int or
          not 0 < tile['size'] <= 73 << 20 or type(tile.get('sha256')) is not str or
          re.fullmatch(r'[0-9a-f]{64}', tile['sha256']) is None):
        return None
      relative = Path(tile['path'])
      if (relative.is_absolute() or len(relative.parts) != 3 or '..' in relative.parts or
          '\\' in tile['path'] or tile['path'] in seen or relative.as_posix() != tile['path']):
        return None
      seen.add(tile['path'])
      for parent in (generation / relative.parts[0], generation / relative.parts[0] / relative.parts[1]):
        if not stat.S_ISDIR(parent.lstat().st_mode):
          return None
      with _open_regular(generation / relative) as file:
        info = os.fstat(file.fileno())
        if info.st_size != tile['size']:
          return None
      total += info.st_size
    bounds = receipt.get('bounds')
    if (type(bounds) is not list or len(bounds) != 4 or
        any(type(value) not in (int, float) or not math.isfinite(value) for value in bounds) or
        not -90 <= bounds[0] < bounds[2] <= 90 or not -180 <= bounds[1] < bounds[3] <= 180):
      return None
    return {'generation': generation_id, 'storedBytes': total, 'tileCount': len(seen), 'bounds': bounds}
  except (OSError, ValueError, TypeError):
    return None


def preflight(binary: Path = PROVIDER_BINARY, manifest_path: Path = PROVIDER_MANIFEST,
              offline_root: Path = OFFLINE_ROOT, source: Path | None = None,
              storage_anchor: Path = STORAGE_ANCHOR, package_dir: Path = PROVIDER_DIR,
              checkout_root: Path = Path(BASEDIR)) -> bool:
  """Verify the fixed packaged artifact and an existing, non-legacy tile root."""
  source = source or Path(BASEDIR) / 'mapd_repo'
  try:
    if (not _owned_root(offline_root, storage_anchor) or not _selected_snapshot(offline_root) or
        not _owned_package(binary, manifest_path, package_dir, checkout_root)):
      return False
    with _open_regular(manifest_path, max_bytes=MAX_MANIFEST_BYTES) as file:
      manifest = json.loads(file.read(MAX_MANIFEST_BYTES + 1))
    if (not isinstance(manifest, dict) or type(manifest.get('schemaVersion')) is not int or manifest['schemaVersion'] != 1 or
        manifest.get('goVersion') != 'go1.25.1' or manifest.get('target') != 'linux-arm64-static'):
      return False
    for name, pattern in (('sourceRevision', r'[0-9a-f]{40}'), ('upstreamRevision', r'[0-9a-f]{40}'),
                          ('sourceDigest', r'[0-9a-f]{64}'), ('binarySha256', r'[0-9a-f]{64}')):
      value = manifest.get(name)
      if type(value) is not str or re.fullmatch(pattern, value) is None:
        return False
    if manifest.get('sourceDigest') != source_digest(source):
      return False
    pinned = json.loads((Path(BASEDIR) / 'upstream-sync.json').read_text())
    revision = next(item['commit'] for item in pinned['dependencies'] if item['path'] == 'mapd_repo')
    if manifest.get('upstreamRevision') != revision:
      return False
    digest = hashlib.sha256()
    stamps = {manifest['sourceDigest'].encode(), revision.encode(), manifest['sourceRevision'].encode()}
    seen = set()
    carry = b''
    with _open_regular(binary, executable=True) as file:
      if not elf_arm64_static_file(file):
        return False
      file.seek(0)
      for chunk in iter(lambda: file.read(1024 * 1024), b''):
        digest.update(chunk)
        window = carry + chunk
        seen.update(stamp for stamp in stamps if stamp in window)
        carry = window[-128:]
    return digest.hexdigest() == manifest.get('binarySha256') and stamps == seen
  except (OSError, ValueError, KeyError, StopIteration, TypeError, OverflowError, RecursionError):
    return False


def enabled(started: bool, params: Params, CP: car.CarParams) -> bool:
  if not started or CP.notCar:
    return False
  try:
    with _open_regular(Path(params.get_param_path(OPT_IN_KEY)), max_bytes=1) as file:
      if file.read(2) == b'1':
        return True
  except (OSError, ValueError):
    pass
  # Selecting Maps owns producer demand directly. Do not rewrite the separate
  # diagnostic opt-in, and do not start control inside the Go shadow provider.
  from openpilot.starpilot.saved_source import read_saved
  from openpilot.starpilot.speed_limits.runtime_settings import LABELS, SOURCE_PRIORITY_DEFAULTS, SOURCE_PRIORITY_KEYS, SOURCE_PRIORITY_MAX_BYTES, read_params
  from openpilot.starpilot.speed_limits.selection import SelectionMode, Source
  # These bounded reads are demand hints only; canonical parsing below owns
  # the enabled/display state and rejects malformed complete preferences.
  labels = []
  for key, default in zip(SOURCE_PRIORITY_KEYS, SOURCE_PRIORITY_DEFAULTS, strict=True):
    raw, readable = read_saved(params, key, SOURCE_PRIORITY_MAX_BYTES)
    if not readable:
      return False
    try:
      labels.append(raw.decode('utf-8') if raw is not None else default)
    except UnicodeError:
      return False
  if not any(LABELS.get(label) is Source.MAP for label in labels) and labels[0] not in ('Highest', 'Lowest'):
    return False
  settings = read_params(params)
  return bool(not settings.errors and (settings.enabled or settings.display) and
              (Source.MAP in settings.selection.slots or settings.selection.mode is not SelectionMode.ORDERED))


# Map evidence is background work beside card/selfdrived/controlsd on the
# busiest cores: below normal priority (still SCHED_OTHER), and two Go
# processors so the runtime and its GC workers stay off most cores.
SHADOW_NICE = 10
SHADOW_GOMAXPROCS = '2'
# Soft heap cap about 2x the densest tile's live set; Go returns the rest to the OS.
SHADOW_GOMEMLIMIT = '64MiB'


def shadow_launcher(pargs: list[str], cwd: str, name: str) -> None:
  """In the forked child before exec, so every Go thread inherits it and the manager is untouched."""
  os.environ['GOMAXPROCS'] = SHADOW_GOMAXPROCS
  os.environ['GOMEMLIMIT'] = SHADOW_GOMEMLIMIT
  try:
    os.nice(SHADOW_NICE)
  except OSError:
    cloudlog.exception('mapd shadow could not lower its priority')
  nativelauncher(pargs, cwd, name)


class MapdShadowProcess(NativeProcess):
  """NativeProcess with bounded retry for an exited or unavailable shadow."""

  def __init__(self, clock=time.monotonic):
    super().__init__('mapd_shadow', str(PROVIDER_DIR.relative_to(BASEDIR)),
                     [str(PROVIDER_BINARY), '--shadow', '--offline-root', str(OFFLINE_ROOT)], enabled)
    self.launcher = shadow_launcher
    self._clock = clock
    self._failures = 0
    self._retry_at = 0.0
    self._started_at = 0.0

  def _delay_retry(self, now: float) -> None:
    self._failures += 1
    self._retry_at = now + min(2 ** min(self._failures - 1, 6), 60)

  def start(self) -> None:
    now = self._clock()
    if self.shutting_down:
      NativeProcess.stop(self)
      if self.proc is not None:
        return
    if self.proc is not None:
      if self.proc.exitcode is None:
        if now - self._started_at >= 60:
          self._failures = 0
        return
      NativeProcess.stop(self, block=False)  # reap a failed direct child
      self._delay_retry(now)
      return
    if now < self._retry_at:
      return
    if not preflight():
      cloudlog.warning('mapd shadow package or offline root unavailable')
      self._delay_retry(now)
      return
    NativeProcess.start(self)
    self._started_at = now

  def stop(self, retry: bool = True, block: bool = True, sig=None):
    result = NativeProcess.stop(self, retry=retry, block=block, sig=sig)
    self._failures = 0
    self._retry_at = 0.0
    return result
