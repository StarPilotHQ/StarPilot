import base64
from contextlib import contextmanager, ExitStack
import fcntl
import json
import os
from pathlib import Path
import stat
import uuid

from openpilot.starpilot.state_migration import (
  _atomic_write, _fsync_dir, _private_dir, _read_value, _save_snapshot, canonical_json, load_snapshot,
)


@contextmanager
def _owner_lock(root, filename):
  if not root.exists():
    yield
    return
  if root.is_symlink() or not root.is_dir():
    raise ValueError('Invalid external preferences directory')
  fd = os.open(root / filename, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
  try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      raise ValueError('Invalid external preferences lock')
    fcntl.flock(fd, fcntl.LOCK_EX)
    yield
  finally:
    os.close(fd)


def _read(path):
  try:
    return _read_value(path)
  except FileNotFoundError:
    return None


def _fresh_navigation(raw, path):
  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError('Duplicate navigation settings field')
      result[key] = value
    return result
  value = json.loads(raw, object_pairs_hook=unique)
  from openpilot.starpilot.navigation.owner import NavigationOwner
  owner = NavigationOwner.__new__(NavigationOwner)
  owner.path = path
  document = owner.read()
  if not isinstance(value, dict) or document['token'] != value.get('token'):
    raise ValueError('Navigation credential cannot be preserved safely')
  return canonical_json({'version': 1, 'revision': uuid.uuid4().hex, 'enabled': False, 'token': document['token'],
                         'destination': None, 'favorites': [], 'recents': [], 'routeChoice': 0})


def reset_external_preferences(storage, model_root):
  storage, model_root = Path(storage).absolute(), Path(model_root).absolute()
  navigation_root = storage / 'navigation'
  state = _private_dir(storage / 'fresh-external-profile-v2')
  identity = canonical_json({'version': 2, 'models': str(model_root), 'navigation': str(navigation_root)})
  with ExitStack() as locks:
    locks.enter_context(_owner_lock(state, '.lock'))
    locks.enter_context(_owner_lock(model_root, '.state.lock'))
    locks.enter_context(_owner_lock(navigation_root, '.lock'))
    locks.enter_context(_owner_lock(storage / 'software' / 'auto-install', '.lock'))
    if _read(state / 'complete.json') == identity:
      return ()
    paths = {'model_preferences': model_root / 'preferences.json',
             'navigation_settings': navigation_root / 'settings.json',
             'software_install': storage / 'software' / 'auto-install' / 'settings.json'}
    journal_path = state / 'journal.json'
    journal_raw = _read(journal_path)
    if journal_raw is None:
      original = {key: raw for key, path in paths.items() if (raw := _read(path)) is not None}
      snapshot = _save_snapshot(original, state / 'snapshots')
      if load_snapshot(snapshot) != original:
        raise ValueError('External preferences archive readback failed')
      desired = {}
      issues = []
      if 'navigation_settings' in original:
        try:
          desired['navigation_settings'] = _fresh_navigation(original['navigation_settings'], paths['navigation_settings'])
        except (ValueError, UnicodeError, RecursionError):
          desired['navigation_settings'] = canonical_json({'version': 1, 'revision': uuid.uuid4().hex, 'enabled': False,
                                                          'token': '', 'destination': None, 'favorites': [], 'recents': [], 'routeChoice': 0})
          issues.append('Invalid navigation credential document archived; fresh navigation is disabled')
      journal = {'version': 2, 'snapshot': str(snapshot),
                 'desired': {key: base64.b64encode(raw).decode() for key, raw in desired.items()}, 'issues': issues}
      _atomic_write(journal_path, canonical_json(journal))
    else:
      journal = json.loads(journal_raw)
      if (not isinstance(journal, dict) or set(journal) != {'version', 'snapshot', 'desired', 'issues'} or
          journal['version'] != 2 or not isinstance(journal['desired'], dict) or
          not set(journal['desired']) <= {'navigation_settings'} or not isinstance(journal['issues'], list) or
          any(not isinstance(issue, str) for issue in journal['issues']) or not isinstance(journal['snapshot'], str)):
        raise ValueError('Invalid external preferences reset journal')
      snapshot = Path(journal['snapshot'])
      if snapshot.parent != state / 'snapshots':
        raise ValueError('External reset archive escapes recovery storage')
      original = load_snapshot(snapshot)
      if not set(original) <= set(paths) or ('navigation_settings' in original) != ('navigation_settings' in journal['desired']):
        raise ValueError('Invalid external preferences archive keys')
    desired = {key: base64.b64decode(raw, validate=True) for key, raw in journal['desired'].items()}
    for key, path in paths.items():
      current = _read(path)
      if current not in (original.get(key), desired.get(key)):
        raise ValueError('External preferences changed during reset')
    for key, path in paths.items():
      expected = desired.get(key)
      if _read(path) == expected:
        continue
      if expected is None:
        path.unlink()
        _fsync_dir(path.parent)
      else:
        _atomic_write(path, expected)
    if any(_read(path) != desired.get(key) for key, path in paths.items()):
      raise ValueError('Fresh external preferences readback failed')
    _atomic_write(state / 'complete.json', identity)
    return tuple(journal['issues'])
