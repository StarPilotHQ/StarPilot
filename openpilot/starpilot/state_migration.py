"""Offline settings recovery and staging. Archived bytes are never an import policy."""
import base64
import binascii
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path


PREFERENCES = frozenset({
  "AlwaysOnLateral", "IsMetric", "RecordAudio", "RecordFront", "RecordFrontLock",
  "ShowDebugInfo", "GsmMetered", "OpenpilotEnabledToggle",
})
# Keys removed from the registry. Saved copies are archived at startup instead of blocking it.
RETIRED_KEYS = frozenset({'ShareUsageStats'})
RENAMED_BOOL_KEYS = {'AndroidAutoEnabled': 'StarPortEnabled'}
MAX_VALUE_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_KEYS = 4096
VEHICLE_PREFERENCE_KEYS = frozenset({
  'RemoteStartBootsComma', 'HKGRemoteStartBootsComma', 'IgnoreIgnitionLine',
  'TeslaWakeOnCAN', 'DisableOpenpilotLongitudinal', 'LongPitch',
  'VoltSNG', 'SNGHack', 'GMAutoHold',
  'VoltOnePedalMode', 'TeslaCoopSteering', 'TeslaAOLScreenTap',
  'TeslaAOLDisengageOnBrake', 'SubaruAvhStartup', 'SubaruStopStartOff',
  'SubaruSNG', 'SubaruRedneckCruise', 'ToyotaAutoHold',
  'JeepBrakeHold', 'DriveModeProfiles', 'DriveModeAcceleration',
  'DriveModeDeceleration', 'NostalgiaMode', 'RemapCancelToDistance',
  'FordHandsFreeCluster',
})
OPERATIONAL_KEYS = frozenset({
  'DongleId', 'HardwareSerial', 'GithubSshKeys', 'GithubUsername', 'SshEnabled',
  'AlphaLongitudinalEnabled', 'HasAcceptedTerms', 'CompletedTrainingVersion',
  'CellularDisabled', 'GsmApn', 'GsmMetered', 'GsmRoaming', 'NetworkMetered', 'BluetoothEnabled',
  'ConnectProvider', 'PairingProvider', 'PairingEmail', 'PrimeType', 'AssistNowToken', 'SecOCKey', 'SecOCKeys',
}) | VEHICLE_PREFERENCE_KEYS


class MigrationRequired(RuntimeError):
  """Existing state has not been qualified for this runtime."""


def canonical_json(value):
  return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')


def digest(data):
  return hashlib.sha256(data).hexdigest()


def _key(value):
  if not isinstance(value, str) or not value or value in ('.', '..') or '/' in value or '\0' in value:
    raise ValueError("Invalid settings key")
  return value


def _hash(value, length=64):
  if not isinstance(value, str) or re.fullmatch(f'[0-9a-f]{{{length}}}', value) is None:
    raise ValueError("Invalid source digest")


def _private_dir(path):
  path = Path(path)
  path.mkdir(mode=0o700, parents=True, exist_ok=True)
  info = path.lstat()
  if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
    raise ValueError("Recovery storage must be a private, owned directory")
  return path


def _fsync_dir(path):
  fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
  try:
    os.fsync(fd)
  finally:
    os.close(fd)


def _write(path, data):
  fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
  with os.fdopen(fd, 'wb') as output:
    output.write(data)
    output.flush()
    os.fsync(output.fileno())


def _atomic_write(path, data):
  fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
  try:
    with os.fdopen(fd, 'wb') as output:
      output.write(data)
      output.flush()
      os.fsync(output.fileno())
    os.replace(temporary, path)
    _fsync_dir(path.parent)
  finally:
    Path(temporary).unlink(missing_ok=True)


@contextmanager
def _params_lock(namespace, *, read_only=False):
  # Params writers use this same lock in the parent of the namespace symlink.
  flags = os.O_RDONLY | os.O_NOFOLLOW if read_only else os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
  try:
    fd = os.open(namespace.parent / '.lock', flags, 0o600)
  except FileNotFoundError:
    if not read_only:
      raise
    yield
    return
  try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      raise ValueError("Invalid Params lock")
    fcntl.flock(fd, fcntl.LOCK_EX)
    yield
  finally:
    os.close(fd)


def _read_value(path):
  fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
  with os.fdopen(fd, 'rb') as source:
    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
      raise ValueError("Settings namespace must contain only regular files")
    value = source.read(MAX_VALUE_BYTES + 1)
  if len(value) > MAX_VALUE_BYTES:
    raise ValueError("Settings value exceeds recovery limit")
  return value


def _read_namespace(namespace):
  actual = namespace.resolve(strict=True)
  values = {}
  total = 0
  for entry in sorted(actual.iterdir()):
    key = _key(entry.name)
    value = _read_value(entry)
    total += len(value)
    values[key] = value
    if total > MAX_SNAPSHOT_BYTES or len(values) > MAX_KEYS:
      raise ValueError("Settings namespace exceeds recovery limit")
  return values


def _inventory(values):
  return [{'key': key, 'sha256': digest(value), 'size': len(value)} for key, value in sorted(values.items())]


def _outside_params(namespace, storage):
  if storage.resolve().is_relative_to(namespace.parent.resolve()) or storage.resolve().is_relative_to(namespace.resolve()):
    raise ValueError("Recovery storage must be outside Params")


def _save_snapshot(values, storage):
  inventory = _inventory(values)
  snapshot_id = digest(canonical_json(inventory))
  storage = _private_dir(storage)
  destination = storage / snapshot_id
  if destination.exists():
    if load_snapshot(destination) != values:
      raise ValueError("Existing recovery snapshot does not match")
    return destination
  temporary = Path(tempfile.mkdtemp(prefix='.pending-', dir=storage))
  try:
    data_dir = _private_dir(temporary / 'values')
    for key, value in values.items():
      _write(data_dir / key, value)
    _write(temporary / 'manifest.json', canonical_json({'format': 'starpilot-raw-settings', 'version': 1, 'entries': inventory}))
    _fsync_dir(data_dir)
    _fsync_dir(temporary)
    os.rename(temporary, destination)
    _fsync_dir(storage)
  finally:
    if temporary.exists():
      shutil.rmtree(temporary)
  return destination


def snapshot_settings(namespace, storage):
  """Capture all raw values, including empty files, unknown keys and credentials.

  Call with services stopped. The native Params lock coordinates cooperating
  writers, but cannot coordinate an older runtime's separate settings cache.
  """
  namespace, storage = Path(namespace).absolute(), Path(storage).absolute()
  _outside_params(namespace, storage)
  with _params_lock(namespace):
    return _save_snapshot(_read_namespace(namespace), storage)


def load_snapshot(snapshot):
  snapshot = Path(snapshot)
  manifest = json.loads(_read_value(snapshot / 'manifest.json'))
  if not isinstance(manifest, dict) or set(manifest) != {'format', 'version', 'entries'}:
    raise ValueError("Invalid recovery snapshot fields")
  if manifest['format'] != 'starpilot-raw-settings' or type(manifest['version']) is not int or manifest['version'] != 1:
    raise ValueError("Unsupported recovery snapshot")
  values = _read_namespace(snapshot / 'values')
  inventory = _inventory(values)
  if inventory != manifest['entries'] or digest(canonical_json(inventory)) != snapshot.name:
    raise ValueError("Recovery snapshot integrity failure")
  return values


def restore_snapshot(snapshot, namespace):
  """Restore exact bytes to a NEW, unused namespace; never overwrite live state."""
  values = load_snapshot(snapshot)
  namespace = Path(namespace).absolute()
  namespace.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
  with _params_lock(namespace):
    if namespace.exists() or namespace.is_symlink():
      raise FileExistsError("Recovery requires an unused destination namespace")
    temporary = Path(tempfile.mkdtemp(prefix='.restored-', dir=namespace.parent))
    try:
      for key, value in values.items():
        _write(temporary / key, value)
      _fsync_dir(temporary)
      namespace.symlink_to(temporary, target_is_directory=True)
      _fsync_dir(namespace.parent)
    except BaseException:
      if not namespace.is_symlink():
        shutil.rmtree(temporary)
      raise
  return namespace


def validate_bundle(bundle):
  if not isinstance(bundle, dict) or set(bundle) != {'format', 'version', 'source', 'preferences', 'pending', 'omitted'}:
    raise ValueError("Invalid settings bundle fields")
  if bundle['format'] != 'starpilot-settings' or type(bundle['version']) is not int or bundle['version'] != 1:
    raise ValueError("Unsupported settings bundle")
  source = bundle['source']
  required = {'revision', 'registry_sha256', 'snapshot_sha256'}
  if not isinstance(source, dict) or not required <= source.keys() or source.keys() - required - {'cache_snapshot_sha256'}:
    raise ValueError("Invalid settings source")
  for key, value in source.items():
    _hash(value, 40 if key == 'revision' else 64)
  preferences = bundle['preferences']
  if not isinstance(preferences, dict) or not preferences.keys() <= PREFERENCES or 'AlwaysOnLateral' not in preferences:
    raise ValueError("Unreviewed active preference")
  if any(type(value) is not bool for value in preferences.values()):
    raise ValueError("Preferences require canonical booleans")
  seen = set(preferences)
  for group in ('pending', 'omitted'):
    if not isinstance(bundle[group], list) or len(bundle[group]) > MAX_KEYS:
      raise ValueError("Invalid settings disposition list")
    for entry in bundle[group]:
      expected = {'key', 'type', 'raw_base64', 'sha256'} if group == 'pending' else {'key', 'reason'}
      if not isinstance(entry, dict) or set(entry) != expected:
        raise ValueError("Invalid settings disposition fields")
      key = _key(entry['key'])
      if key in seen:
        raise ValueError("Duplicate settings disposition")
      seen.add(key)
      if group == 'omitted':
        if not isinstance(entry['reason'], str) or not entry['reason']:
          raise ValueError("Missing settings omission reason")
        continue
      if not isinstance(entry['type'], str) or entry['type'] not in {'BOOL', 'INT', 'FLOAT', 'TIME', 'JSON', 'STRING'}:
        raise ValueError("Invalid pending settings type")
      if not isinstance(entry['raw_base64'], str) or len(entry['raw_base64']) > 4 * ((MAX_VALUE_BYTES + 2) // 3):
        raise ValueError("Invalid pending settings encoding size")
      try:
        value = base64.b64decode(entry['raw_base64'], validate=True)
      except (ValueError, binascii.Error) as error:
        raise ValueError("Invalid pending settings encoding") from error
      _hash(entry['sha256'])
      if len(value) > MAX_VALUE_BYTES or digest(value) != entry['sha256']:
        raise ValueError("Pending settings integrity failure")
  if len(canonical_json(bundle)) > MAX_SNAPSHOT_BYTES:
    raise ValueError("Settings bundle exceeds recovery limit")


def decode_bundle(encoded):
  """Parse serialized input without silently accepting duplicate JSON members."""
  if not isinstance(encoded, bytes) or len(encoded) > MAX_SNAPSHOT_BYTES:
    raise ValueError("Invalid settings bundle size or encoding")

  def unique_object(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError("Duplicate settings JSON member")
      result[key] = value
    return result

  bundle = json.loads(encoded, object_pairs_hook=unique_object)
  validate_bundle(bundle)
  return bundle


def stage_settings(bundle, storage, namespace):
  """Persist a reviewed-format bundle outside Params. Nothing is activated."""
  validate_bundle(bundle)
  _outside_params(Path(namespace).absolute(), Path(storage).absolute())
  encoded = canonical_json(bundle)
  destination = _private_dir(Path(storage) / 'staged') / f'{digest(encoded)}.json'
  _atomic_write(destination, encoded)
  return destination


def _migrate_first_start(params, namespace, storage, values, known, cache_keys, inspect_cache, *, retire_keys=None,
                         retire_action='archive incompatible reconstructible vehicle cache'):
  actual = namespace.resolve(strict=True)
  snapshot = _save_snapshot(values, storage / 'snapshots')
  if load_snapshot(snapshot) != values:
    raise MigrationRequired('Raw recovery snapshot readback failed')
  prepared = dict(values)
  actions = {}
  if (retire_keys is None and 'AlphaLongitudinalEnabled' in known and 'AlphaLongitudinalEnabled' not in values and
      values.get('ExperimentalLongitudinalEnabled') in (b'0', b'1')):
    prepared['AlphaLongitudinalEnabled'] = values['ExperimentalLongitudinalEnabled']
    actions['AlphaLongitudinalEnabled'] = 'preserve legacy longitudinal choice'
  for key, raw in values.items():
    if retire_keys is not None:
      if key in retire_keys:
        prepared.pop(key)
        actions[key] = ('archive incompatible reconstructible vehicle cache' if key in known else
                        'archive unknown saved key; retire without interpretation')
      continue
    if key not in known or key not in OPERATIONAL_KEYS:
      prepared.pop(key)
      actions[key] = 'archive prior software state; initialize fresh defaults'
    elif getattr(params.get_type(key), 'name', None) == 'BOOL' and raw not in (b'0', b'1'):
      prepared.pop(key)
      actions[key] = 'archive invalid operational flag; initialize registry default'
  report = {'format': 'starpilot-first-start-migration', 'version': 2,
            'namespace': str(namespace), 'target': str(actual), 'snapshot': str(snapshot),
            'status': 'prepared', 'actions': actions, 'attempted': [], 'written': []}
  receipt = snapshot / 'migration.json'
  _atomic_write(receipt, canonical_json(report))
  try:
    if namespace.resolve(strict=True) != actual or _read_namespace(namespace) != values:
      raise MigrationRequired('Settings source changed before migration')
    for key in sorted(actions):
      if namespace.resolve(strict=True) != actual:
        raise MigrationRequired('Settings namespace changed during migration')
      report['attempted'].append(key)
      _atomic_write(receipt, canonical_json(report))
      if key in prepared:
        _atomic_write(actual / key, prepared[key])
      else:
        (actual / key).unlink()
        _fsync_dir(actual)
      report['written'].append(key)
    if namespace.resolve(strict=True) != actual or _read_namespace(namespace) != prepared:
      raise MigrationRequired('Migrated settings readback differs from prepared batch')
    report['status'] = 'migrated'
    _atomic_write(receipt, canonical_json(report))
  except Exception as error:
    report.update(status='failed', error=type(error).__name__)
    _atomic_write(receipt, canonical_json(report))
    raise MigrationRequired(f'Settings migration interrupted; raw recovery snapshot: {snapshot}') from error
  return prepared


def _dom_handoff(namespace):
  path = namespace.parent / f'.starpilot-dom-handoff-{digest(str(namespace).encode())}.json'
  try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
  except FileNotFoundError:
    return None
  except OSError as error:
    raise MigrationRequired('Invalid Dom handoff marker') from error
  try:
    with os.fdopen(fd, 'rb') as source:
      info = os.fstat(source.fileno())
      if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 4096:
        raise ValueError('Invalid Dom handoff file')
      raw = source.read(4097)
    record = json.loads(raw)
    if type(record) is not dict or set(record) != {'format', 'version', 'namespace', 'target', 'token'}:
      raise ValueError('Invalid Dom handoff fields')
    _hash(record['token'], 32)
    expected = {'format': 'starpilot-dom-handoff', 'version': 1, 'namespace': str(namespace),
                'target': str(namespace.resolve(strict=True)), 'token': record['token']}
    if type(record['version']) is not int or raw != canonical_json(expected):
      raise ValueError('Dom handoff does not bind this namespace')
    return record['token']
  except (OSError, ValueError, TypeError, KeyError) as error:
    raise MigrationRequired('Invalid Dom handoff marker') from error


def prepare_manager_start(params, storage, *, dry_run=False, auto_migrate=False):
  from openpilot.starpilot.schema_cache import CACHE_KEYS, inspect_cache

  namespace, storage = Path(params.get_param_path()).absolute(), Path(storage).absolute()
  _outside_params(namespace, storage)
  marker_dir = storage / 'profiles' if dry_run else _private_dir(storage / 'profiles')
  if dry_run and (marker_dir.exists() or marker_dir.is_symlink()):
    info = marker_dir.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
      raise ValueError("Recovery storage must be a private, owned directory")
  marker = marker_dir / f'{digest(str(namespace).encode())}.json'
  with _params_lock(namespace, read_only=dry_run):
    values = _read_namespace(namespace)
    identity = {'version': 2, 'schema_epoch': 2, 'namespace': str(namespace), 'target': str(namespace.resolve())}
    handoff = _dom_handoff(namespace)
    consumed = None
    try:
      raw_identity = _read_value(marker)
      saved_identity = json.loads(raw_identity)
      candidate_identity = dict(identity)
      if type(saved_identity) is dict and 'consumed_dom_token' in saved_identity:
        _hash(saved_identity['consumed_dom_token'], 32)
        candidate_identity['consumed_dom_token'] = saved_identity['consumed_dom_token']
      initialized = raw_identity == canonical_json(candidate_identity)
      if initialized:
        consumed = candidate_identity.get('consumed_dom_token')
    except (OSError, ValueError, TypeError):
      initialized = False
    foreign_handoff = handoff is not None and handoff != consumed
    if consumed is not None:
      identity['consumed_dom_token'] = consumed
    known = {key.decode() if isinstance(key, bytes) else key for key in params.all_keys()}
    if auto_migrate and not dry_run and initialized and not foreign_handoff:
      for old, new in RENAMED_BOOL_KEYS.items():
        if old not in values:
          continue
        _save_snapshot(values, storage / 'snapshots')
        if new not in values and values[old] in (b'0', b'1'):
          _atomic_write(namespace / new, values[old])
        (namespace / old).unlink()
        _fsync_dir(namespace)
        values = _read_namespace(namespace)
    if auto_migrate and not dry_run and (foreign_handoff or not initialized and values):
      values = _migrate_first_start(params, namespace, storage, values, known, CACHE_KEYS, inspect_cache)
      migrated = True
      if foreign_handoff:
        identity["consumed_dom_token"] = handoff
    else:
      migrated = False
    unknown = set(values) - known
    retired = unknown & RETIRED_KEYS
    if auto_migrate and not dry_run and initialized and not foreign_handoff and retired:
      values = _migrate_first_start(params, namespace, storage, values, known, CACHE_KEYS, inspect_cache,
                                    retire_keys=retired, retire_action='archive setting removed from the registry')
      unknown = set(values) - known
    invalid_preferences = any(key in values and values[key] not in (b'0', b'1') for key in PREFERENCES)
    incompatible_keys = {key for key in CACHE_KEYS if key in values and inspect_cache(key, values[key]).status != 'valid'}
    invalid_booleans = any(getattr(params.get_type(key), 'name', None) == 'BOOL' and raw not in (b'0', b'1')
                           for key, raw in values.items() if key in known)
    if (auto_migrate and not dry_run and initialized and not foreign_handoff and unknown and
        not invalid_preferences and not invalid_booleans and not incompatible_keys and
        'LocationFilterInitialState' not in values):
      values = _migrate_first_start(params, namespace, storage, values, known, CACHE_KEYS, inspect_cache,
                                    retire_keys=unknown)
      unknown = set()
    if (auto_migrate and not dry_run and initialized and incompatible_keys and
        incompatible_keys <= {'CarParamsCache', 'CarParamsPersistent', 'CarParamsPrevRoute'} and
        not unknown and not invalid_preferences and not invalid_booleans and 'LocationFilterInitialState' not in values):
      values = _migrate_first_start(params, namespace, storage, values, known, CACHE_KEYS, inspect_cache,
                                    retire_keys=incompatible_keys)
      incompatible_keys = set()
    incompatible_cache = bool(incompatible_keys)
    # This retired cache has no compatible producer or conversion.
    incompatible_cache |= 'LocationFilterInitialState' in values
    unqualified = (foreign_handoff and not migrated or unknown or invalid_preferences or incompatible_cache or
                   (not initialized and not migrated and bool(set(values) - PREFERENCES)))
    if unqualified:
      if dry_run:
        reasons = []
        if foreign_handoff:
          reasons.append('unconsumed Dom handoff')
        if unknown:
          reasons.append('unknown saved keys')
        if invalid_preferences:
          reasons.append('invalid saved preferences')
        if incompatible_cache:
          reasons.append('incompatible retained cache')
        if not initialized and bool(set(values) - PREFERENCES):
          reasons.append('unqualified existing namespace')
        raise MigrationRequired('Settings require migration before startup: ' + ', '.join(reasons))
      snapshot = _save_snapshot(values, storage / 'snapshots')
      raise MigrationRequired(f"Settings require migration before startup; raw recovery snapshot: {snapshot}")
    if (not initialized or foreign_handoff) and not dry_run:
      _atomic_write(marker, canonical_json(identity))
