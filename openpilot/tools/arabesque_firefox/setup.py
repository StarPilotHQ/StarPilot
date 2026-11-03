#!/usr/bin/env python3
"""Stage the pinned ARM64 browser under /data; never install packages into AGNOS."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid

ROOT = Path('/data/arabesque-firefox')
BROWSER_UID = 61234
FORMAT = 1
FLOOR = 6 * 1024**3
DISPLAY_PACKAGE = 'display-xvfb.deb'
SUPPORTED_AGNOS = ('19.8.1', '20.2-starpilot.1', '20.2.1')
PHASES = {'staging', 'copying', 'qualifying', 'activate', 'committed', 'restore-copy', 'restore', 'restored'}


def manifest(value):
  if not isinstance(value, dict):
    raise ValueError('Unsupported Firefox bundle receipt')
  value = dict(value)
  value.setdefault('bundle_revision', 0)
  value.setdefault('bundle_format', FORMAT)
  if (set(value) != {'version', 'agnos', 'packages', 'bundle_revision', 'bundle_format'} or
      type(value['bundle_format']) is not int or value['bundle_format'] != FORMAT or
      type(value['bundle_revision']) is not int or value['bundle_revision'] < 0 or
      not isinstance(value['version'], str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?esr', value['version']) or
      value['agnos'] != '19.8.1' or not isinstance(value['packages'], list) or not 1 <= len(value['packages']) <= 32):
    raise ValueError('Unsupported Firefox bundle receipt')
  names = set()
  for package in value['packages']:
    if not isinstance(package, dict):
      raise ValueError('Invalid pinned Firefox package')
    digest = 'sha512' if 'sha512' in package else 'sha256'
    if (set(package) != {'name', 'url', 'size', digest} or not isinstance(package['name'], str) or
        not re.fullmatch(r'[a-zA-Z0-9_.+-]+\.(?:deb|tar\.xz)', package['name']) or package['name'] in names or
        not isinstance(package['url'], str) or not package['url'].startswith('https://') or
        type(package['size']) is not int or not 0 < package['size'] < 512 * 1024**2 or
        not isinstance(package[digest], str) or not re.fullmatch('[0-9a-f]{' + str(128 if digest == 'sha512' else 64) + '}', package[digest])):
      raise ValueError('Invalid pinned Firefox package')
    names.add(package['name'])
  return value


def read_json(path):
  with path.open('rb') as stream:
    data = stream.read(65537)
  if len(data) > 65536 or not isinstance(value := json.loads(data), dict):
    raise ValueError('Invalid Firefox metadata')
  return value


def approved():
  return manifest(read_json(Path(__file__).with_name('runtime.json')))


def bundle_id(value):
  return hashlib.sha256(json.dumps(manifest(value), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def sync_dir(path):
  fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
  try:
    os.fsync(fd)
  finally:
    os.close(fd)


def write_json(path, value):
  temporary = path.with_name(path.name + '.tmp')
  fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
  with os.fdopen(fd, 'w') as stream:
    json.dump(value, stream, sort_keys=True)
    stream.write('\n')
    stream.flush()
    os.fsync(stream.fileno())
  os.replace(temporary, path)
  sync_dir(path.parent)


def safe(path, uid=0, private=False):
  info = path.lstat()
  if stat.S_ISLNK(info.st_mode) or info.st_uid != uid or info.st_mode & (0o077 if private else 0o022):
    raise RuntimeError(f'Unsafe Firefox path: {path}')
  return info


def receipt(path):
  safe(path)
  safe(path / 'ready.json')
  safe(path / 'firefox/firefox')
  return manifest(read_json(path / 'ready.json'))


def transaction_id(value):
  if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
    raise ValueError('Invalid Firefox transaction identity')
  return value


def journal():
  path = ROOT / 'update.json'
  if not path.exists():
    return None
  safe(path)
  return update_record(read_json(path))


def update_record(value):
  if (set(value) != {'format', 'id', 'phase', 'target', 'previous'} or type(value['format']) is not int or value['format'] != FORMAT or
      not isinstance(value['phase'], str) or value['phase'] not in PHASES):
    raise ValueError('Invalid Firefox update journal; preserve it for review')
  transaction_id(value['id'])
  value['target'] = manifest(value['target'])
  value['previous'] = manifest(value['previous'])
  safe(ROOT / 'updates')
  safe(ROOT / 'updates' / value['id'])
  return value


def private_display(bundle):
  return any(package['name'] == DISPLAY_PACKAGE for package in bundle['packages'])


def host_display():
  return all(os.access(path, os.X_OK) for path in ('/usr/bin/Xvfb', '/usr/bin/xauth', '/usr/bin/xkbcomp'))


def inspect():
  """Small local reads only; safe to call when the Firefox tool opens."""
  target = approved()
  if ROOT.is_symlink():
    raise RuntimeError('Firefox runtime path must not be a symlink')
  if not ROOT.exists():
    return {'action': 'install', 'target': target}
  safe(ROOT)
  record = journal()
  if record and record['phase'] not in ('committed', 'restored'):
    return {'action': 'recover', 'target': target}
  if not (ROOT / 'runtime').exists():
    if record:
      raise RuntimeError('Firefox runtime is missing; preserve its update journal for review')
    return {'action': 'install', 'target': target}
  current = receipt(ROOT / 'runtime')
  safe(ROOT / 'state', BROWSER_UID, private=True)
  if record and bundle_id(current) != bundle_id(record['target'] if record['phase'] == 'committed' else record['previous']):
    raise RuntimeError('Firefox no longer matches its recorded transaction; preserve it for review')
  if current['bundle_revision'] == target['bundle_revision'] and bundle_id(current) != bundle_id(target):
    raise RuntimeError('Firefox bundle changed without a new bundle revision')
  def version(value):
    return tuple((list(map(int, value.removesuffix('esr').split('.'))) + [0, 0])[:3])
  newer = target['bundle_revision'] > current['bundle_revision'] and version(target['version']) >= version(current['version'])
  repair = (newer and not host_display() and not private_display(current) and private_display(target) and
            current['version'] == target['version'] and all(package in target['packages'] for package in current['packages']) and
            all(package in current['packages'] or package['name'].startswith('display-') for package in target['packages']))
  restorable = bool(record and record['phase'] == 'committed' and not (ROOT / 'recovery').exists() and
                    not (ROOT / 'recovery-state').exists() and
                    (private_display(record['previous']) or host_display()) and
                    (ROOT / 'updates' / record['id'] / 'previous').is_dir() and
                    (ROOT / 'updates' / record['id'] / 'profile-backup').is_dir())
  return {'action': 'update' if newer else 'browse', 'target': target, 'current': current, 'restorable': restorable, 'repair': repair}


def report(message):
  print(message, flush=True)


def verify(path, package):
  algorithm = 'sha512' if 'sha512' in package else 'sha256'
  with path.open('rb') as stream:
    digest = hashlib.file_digest(stream, algorithm).hexdigest()
  if path.stat().st_size != package['size'] or digest != package[algorithm]:
    raise ValueError(f'Package checksum mismatch: {package["name"]}')


def copy_package(package, response, partial, label):
  remaining = package['size']
  with partial.open('wb') as target:
    while block := response.read(min(1024 * 1024, remaining + 1)):
      remaining -= len(block)
      if remaining < 0:
        raise ValueError('Browser package exceeds pinned size')
      target.write(block)
      report(f'{label}… {100 * (package["size"] - remaining) // package["size"]}%')


def download(package, cache, *, cache_uid=0):
  digest = package.get('sha512', package.get('sha256'))
  path = cache / (digest + '-' + package['name'])
  legacy = cache / package['name']
  for existing in (path, legacy):
    if existing.exists():
      safe(existing, cache_uid)
      try:
        verify(existing, package)
        if existing == legacy:
          legacy.rename(path)
        return path
      except ValueError:
        if existing == path:
          existing.unlink()
  if not path.exists():
    partial = path.with_suffix('.partial')
    bundled = Path(__file__).with_name('offline') / path.name
    label = 'Firefox' if package['name'].startswith('firefox-') else 'browser libraries'
    try:
      try:
        fd = os.open(bundled, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
      except FileNotFoundError:
        with urllib.request.urlopen(package['url'], timeout=30) as response:
          if not response.url.startswith('https://'):
            raise ValueError('Refusing an insecure browser package redirect') from None
          copy_package(package, response, partial, f'Downloading {label}')
      else:
        with os.fdopen(fd, 'rb') as response:
          info = os.fstat(response.fileno())
          if not stat.S_ISREG(info.st_mode) or info.st_size != package['size']:
            raise ValueError(f'Invalid bundled Firefox package: {package["name"]}')
          copy_package(package, response, partial, f'Copying bundled {label}')
      verify(partial, package)
      partial.rename(path)
    finally:
      partial.unlink(missing_ok=True)
  verify(path, package)
  return path


def unpack(path, destination):
  if path.suffix == '.deb':
    subprocess.run(['/usr/bin/dpkg-deb', '--extract', str(path), str(destination)], check=True, timeout=30)
  else:
    with tarfile.open(path) as archive:
      archive.extractall(destination, filter='data')


def check_host(target):
  # Keep the receipt compatible with older checkouts while qualifying the private display on newer images.
  supported = SUPPORTED_AGNOS if private_display(target) else (target['agnos'],)
  if os.geteuid() != 0 or platform.machine() != 'aarch64' or Path('/VERSION').read_text().strip() not in supported:
    raise RuntimeError(f'Prepare this runtime as root on ARM64 AGNOS {", ".join(supported)}')


def prepare():
  target = approved()
  check_host(target)
  for path in (ROOT, ROOT / 'runtime', ROOT / 'state', ROOT / 'packages'):
    if path.is_symlink():
      raise RuntimeError(f'Refusing symlink: {path}')
  if ROOT.exists() and (ROOT.stat().st_uid != 0 or ROOT.stat().st_mode & 0o022):
    raise RuntimeError('Unsafe browser directory ownership')
  ROOT.mkdir(mode=0o755, exist_ok=True)
  fd = os.open(ROOT / '.prepare.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
  with os.fdopen(fd, 'r+') as lock:
    safe(ROOT / '.prepare.lock')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    prepare_locked(target)


def prepare_locked(manifest):
  cache = ROOT / 'packages'
  if (ROOT / 'runtime').exists():
    raise RuntimeError('Runtime already exists; stop and review an update separately. The profile is never replaced.')
  if (ROOT / 'update.json').exists():
    raise RuntimeError('Open Firefox in Arabesque to finish its pending recovery before manual preparation')
  quiescent()
  if shutil.disk_usage(ROOT).free < FLOOR:
    raise RuntimeError('At least 6 GiB free on /data is required to prepare Firefox')
  # A hard stop can interrupt extraction; only this root-owned staging area is disposable.
  for path in ROOT.glob('.prepare-*'):
    if path.is_symlink():
      path.unlink()
    elif path.is_dir():
      shutil.rmtree(path)
  private_directory(ROOT / 'state')
  cache.mkdir(mode=0o700, exist_ok=True)
  safe(cache, private=True)
  with tempfile.TemporaryDirectory(prefix='.prepare-', dir=ROOT) as temporary:
    staging = Path(temporary) / 'runtime'
    build(manifest, staging)
    staging.rename(ROOT / 'runtime')
    sync_dir(ROOT)
  report('Firefox is prepared. Starting the browser…')


def quiescent():
  try:
    pwd.getpwuid(BROWSER_UID)
  except KeyError:
    pass
  else:
    raise RuntimeError(f'UID {BROWSER_UID} is already assigned')
  for entry in Path('/proc').iterdir():
    if entry.name.isdigit():
      try:
        if entry.stat().st_uid == BROWSER_UID:
          raise RuntimeError(f'UID {BROWSER_UID} already has running processes')
      except FileNotFoundError:
        pass
  active = subprocess.check_output(['systemctl', '--no-pager', '--plain', '--no-legend', 'list-units',
                                    '--state=activating,active,deactivating', 'arabesque-firefox-*'], text=True, timeout=5)
  own_unit = next((Path(line[3:]).name for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::')), '')
  if any(line.split()[0] != own_unit for line in active.splitlines() if line.split()):
    raise RuntimeError('Close all Firefox sessions before preparing the runtime')


def build(manifest, staging):
  cache = ROOT / 'packages'
  cache.mkdir(mode=0o700, exist_ok=True)
  safe(cache, private=True)
  staging.mkdir()
  for index, package in enumerate(manifest['packages'], 1):
    report(f'Preparing Firefox ({index}/{len(manifest["packages"])})…')
    archive = download(package, cache)
    safe(archive)
    report('Unpacking Firefox…' if package['name'].startswith('firefox-') else 'Preparing browser libraries…')
    unpack(archive, staging / 'display' if package['name'].startswith('display-') else staging)
  if private_display(manifest):
    # Ubuntu Xvfb resolves xkbcomp at /usr/bin. Expose just that helper through a unit-local /usr overlay.
    extension = staging / 'isolation/xkb/usr'
    (extension / 'bin').mkdir(parents=True)
    release = extension / 'lib/extension-release.d'
    release.mkdir(parents=True)
    (release / 'extension-release.xkb').write_text('ID=_any\n')
    (staging / 'display/usr/bin/xkbcomp').rename(extension / 'bin/xkbcomp')
  schemas = staging / 'usr/share/glib-2.0/schemas'
  for source in Path('/usr/share/glib-2.0/schemas').glob('*.xml'):
    if not (schemas / source.name).exists():
      shutil.copyfile(source, schemas / source.name)
  subprocess.run(['/usr/bin/glib-compile-schemas', '--strict', str(schemas)], check=True, timeout=10)
  distribution = staging / 'firefox/distribution'
  distribution.mkdir(exist_ok=True)
  (distribution / 'policies.json').write_text(json.dumps({'policies': {
    'DisableAppUpdate': True, 'DontCheckDefaultBrowser': True, 'DisableTelemetry': True, 'DisableFirefoxStudies': True,
  }}))
  for name in ('empty', 'devices', 'devices/shm'):
    (staging / 'isolation' / name).mkdir(parents=True, exist_ok=True)
  for name in ('null', 'zero', 'random', 'urandom'):
    (staging / 'isolation/devices' / name).touch()
  (staging / 'ready.json').write_text(json.dumps(manifest, indent=2) + '\n')
  for path in (staging, *staging.rglob('*')):
    if not path.is_symlink():
      os.chown(path, 0, 0)
      path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
      if path.is_file():
        with path.open('rb') as stream:
          os.fsync(stream.fileno())
  for path, _, _ in os.walk(staging, topdown=False):
    sync_dir(path)
  (ROOT / '.isolation-probe').write_text('This file must be hidden from the Firefox worker.\n')


def save(record, phase):
  record['phase'] = phase
  write_json(ROOT / 'update.json', record)


def move(source, destination):
  source.rename(destination)
  sync_dir(destination.parent)
  if source.parent != destination.parent:
    sync_dir(source.parent)


def matches(path, value):
  return path.exists() and bundle_id(receipt(path)) == bundle_id(value)


def activate(record):
  """Finish forward after durable intent; never touch a profile during application activation."""
  tx = ROOT / 'updates' / record['id']
  current, candidate, previous = ROOT / 'runtime', tx / 'candidate', tx / 'previous'
  if current.exists() and matches(current, record['target']) and not candidate.exists():
    if not matches(previous, record['previous']):
      raise RuntimeError('Missing previous Firefox runtime; preserve the transaction for review')
  else:
    if not matches(candidate, record['target']):
      raise RuntimeError('Invalid staged Firefox runtime; preserve the transaction for review')
    if current.exists():
      if previous.exists() or not matches(current, record['previous']):
        raise RuntimeError('Firefox changed outside the recorded update; preserve it for review')
      move(current, previous)
    elif not matches(previous, record['previous']):
      raise RuntimeError('Ambiguous interrupted Firefox activation')
    move(candidate, current)
  save(record, 'committed')


def restore(record):
  tx, recovery = ROOT / 'updates' / record['id'], ROOT / 'recovery'
  current, previous, failed = ROOT / 'runtime', tx / 'previous', recovery / 'runtime'
  # Same-parent renames avoid needing access to another UID's private directory to update its '..' entry.
  state, replacement, preserved = ROOT / 'state', ROOT / 'restore-state', ROOT / 'recovery-state'
  app_done = current.exists() and matches(current, record['previous']) and not previous.exists()
  if app_done:
    if not matches(failed, record['target']):
      raise RuntimeError('Missing preserved Firefox runtime')
  else:
    if not matches(previous, record['previous']):
      raise RuntimeError('Invalid previous Firefox runtime')
    if current.exists():
      if failed.exists() or not matches(current, record['target']):
        raise RuntimeError('Firefox changed outside the recorded restoration')
    elif not matches(failed, record['target']):
      raise RuntimeError('Ambiguous interrupted Firefox restoration')
  for path in (state, replacement, preserved):
    if path.exists() or path.is_symlink():
      if not stat.S_ISDIR(safe(path, BROWSER_UID, private=True).st_mode):
        raise RuntimeError(f'Unsafe Firefox private directory: {path}')
  state_before = state.exists() and replacement.exists() and not preserved.exists()
  state_between = not state.exists() and replacement.exists() and preserved.exists()
  state_done = state.exists() and not replacement.exists() and preserved.exists()
  if not (state_before or state_between or state_done) or not app_done and not state_before:
    raise RuntimeError('Ambiguous Firefox restoration order; all copies were preserved')
  recovery.mkdir(mode=0o755, exist_ok=True)
  safe(recovery)
  metadata = {'saved': record['target'], 'restored': record['previous']}
  if (recovery / 'receipt.json').exists() and read_json(recovery / 'receipt.json') != metadata:
    raise RuntimeError('A different saved Firefox recovery already exists')
  write_json(recovery / 'receipt.json', metadata)
  sync_dir(ROOT)
  if not app_done:
    if current.exists():
      move(current, failed)
    move(previous, current)
  if not state_done:
    quiescent()
    if state_before:
      move(state, preserved)
    move(replacement, state)
  save(record, 'restored')


def private_directory(path):
  if not path.exists():
    path.mkdir(mode=0o700)
  info = path.lstat()
  if not stat.S_ISDIR(info.st_mode):
    raise RuntimeError(f'Unsafe Firefox private directory: {path}')
  # A power loss can separate mkdir from chown; only an empty, private root-owned directory is repairable.
  if info.st_uid == 0 and not info.st_mode & 0o077 and not any(path.iterdir()):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
      os.chown(path, BROWSER_UID, BROWSER_UID)
      os.fsync(fd)
    finally:
      os.close(fd)
  safe(path, BROWSER_UID, private=True)
  sync_dir(path.parent)


def transaction_directories():
  result = []
  for path in (ROOT / 'updates').iterdir():
    transaction_id(path.name)
    safe(path)
    result.append(path.name)
  return result


def remove_transactions(identities):
  for identity in identities:
    path = ROOT / 'updates' / transaction_id(identity)
    safe(path)
    for name in ('profile-backup', 'probe-state'):
      child = path / name
      if child.exists():
        safe(child, BROWSER_UID, private=True)
        child.rmdir()  # The unprivileged cleanup job must have emptied it first.
    shutil.rmtree(path)
  sync_dir(ROOT / 'updates')


def maintain(operation, expected, job):
  """Root control plane. Jobs run in independent, unprivileged transient services."""
  target = approved()
  check_host(target)
  safe(ROOT)
  safe(ROOT / '.prepare.lock')
  fd = os.open(ROOT / '.prepare.lock', os.O_RDWR | os.O_NOFOLLOW)
  with os.fdopen(fd, 'r+') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    quiescent()

    def run_job(kind, identity, garbage=()):
      for transaction in {identity, *garbage}:
        for name in ('profile-backup', 'probe-state'):
          path = ROOT / 'updates' / transaction / name
          if path.exists():
            private_directory(path)
      scratch = ROOT / 'restore-state'
      if kind in ('restore-copy', 'cleanup') and scratch.exists():
        private_directory(scratch)
      job(kind, identity, lock.fileno(), list(garbage))
      quiescent()
      if kind == 'cleanup' and scratch.exists():
        scratch.rmdir()
        sync_dir(ROOT)

    record = journal()
    if operation == 'update':
      if expected != bundle_id(target) or inspect()['action'] != 'update':
        raise RuntimeError('Firefox update changed; reopen Firefox to review the current offer')
      updates = ROOT / 'updates'
      updates.mkdir(mode=0o755, exist_ok=True)
      safe(updates)
      identity = uuid.uuid4().hex
      tx = updates / identity
      tx.mkdir(mode=0o755)
      sync_dir(updates)
      if record:
        write_json(tx / 'prior.json', record)
      record = {'format': FORMAT, 'id': identity, 'phase': 'staging', 'target': target, 'previous': receipt(ROOT / 'runtime')}
      save(record, 'staging')
      required = FLOOR + sum(p['size'] for p in target['packages']) * 7
      if shutil.disk_usage(ROOT).free < required:
        raise RuntimeError('Insufficient free storage to stage Firefox while preserving the current installation')
      build(target, tx / 'candidate')
      sync_dir(tx)
      for name in ('profile-backup', 'probe-state'):
        private_directory(tx / name)
      save(record, 'copying')
      run_job('copy', identity)
      save(record, 'qualifying')
      run_job('qualify', identity)
      report('Activating verified Firefox...')
      save(record, 'activate')
      activate(record)
    elif operation == 'restore':
      if (not record or record['phase'] != 'committed' or
          (ROOT / 'recovery').exists() or (ROOT / 'recovery-state').exists()):
        raise RuntimeError('No complete previous Firefox pair is available, or a saved recovery already exists')
      if not private_display(record['previous']) and not host_display():
        raise RuntimeError('Previous Firefox requires Xvfb and xauth from AGNOS')
      tx = ROOT / 'updates' / record['id']
      if not matches(ROOT / 'runtime', record['target']) or not matches(tx / 'previous', record['previous']):
        raise RuntimeError('Firefox no longer matches its recorded backup')
      safe(tx / 'profile-backup', BROWSER_UID, private=True)
      private_directory(ROOT / 'restore-state')
      save(record, 'restore-copy')
      run_job('restore-copy', record['id'])
      report('Restoring previous Firefox...')
      save(record, 'restore')
      restore(record)
    elif operation == 'recover':
      if not record:
        raise RuntimeError('No Firefox update transaction is available to recover')
      if record['phase'] == 'activate':
        activate(record)
      elif record['phase'] == 'restore':
        restore(record)
      elif record['phase'] in ('staging', 'copying', 'qualifying', 'restore-copy'):
        if not matches(ROOT / 'runtime', record['target'] if record['phase'] == 'restore-copy' else record['previous']):
          raise RuntimeError('Firefox changed during an interrupted operation; preserve it for review')
        prior_record = None
        prior = ROOT / 'updates' / record['id'] / 'prior.json'
        if record['phase'] != 'restore-copy' and prior.exists():
          safe(prior)
          prior_record = update_record(read_json(prior))
          if prior_record['phase'] not in ('committed', 'restored') or not matches(
              ROOT / 'runtime', prior_record['target'] if prior_record['phase'] == 'committed' else prior_record['previous']):
            raise RuntimeError('Invalid previous Firefox transaction')
        run_job('cleanup', record['id'], [] if record['phase'] == 'restore-copy' else [record['id']])
        if record['phase'] == 'restore-copy':
          # Keep the recorded previous pair; only the incomplete replacement is disposable.
          save(record, 'committed')
          return
        if prior_record is not None:
          write_json(ROOT / 'update.json', prior_record)
        else:
          (ROOT / 'update.json').unlink()
        sync_dir(ROOT)
        remove_transactions([record['id']])
        return
    else:
      raise ValueError('Unknown Firefox maintenance operation')
    garbage = [identity for identity in transaction_directories() if identity != record['id']]
    run_job('cleanup', record['id'], garbage)
    remove_transactions(garbage)
    cache = ROOT / 'packages'
    if cache.exists():
      safe(cache, private=True)
      wanted = {p.get('sha512', p.get('sha256')) + '-' + p['name'] for p in receipt(ROOT / 'runtime')['packages']}
      for path in cache.iterdir():
        safe(path)
        if path.is_file() and path.name not in wanted:
          path.unlink()


if __name__ == '__main__':
  import argparse
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--stage-offline', action='store_true', help='Refresh the offline archives in the source tree.')
  if parser.parse_args().stage_offline:
    offline = Path(__file__).with_name('offline')
    offline.mkdir(mode=0o755, exist_ok=True)
    safe(offline, os.getuid())
    wanted = {download(package, offline, cache_uid=os.getuid()) for package in approved()['packages']}
    for path in offline.iterdir():
      if path not in wanted and re.fullmatch(r'(?:[0-9a-f]{64}|[0-9a-f]{128})-[a-zA-Z0-9_.+-]+[.](?:deb|tar[.]xz)', path.name):
        path.unlink()
  else:
    prepare()
