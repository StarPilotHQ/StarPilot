#!/usr/bin/env python3
"""Offline, commit-bound channel migration; never checks out or downloads code."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


PLAN = 'scripts/branch-migration.json'


def git(repo, *args):
  return subprocess.check_output(['git', '--no-optional-locks', '-c', 'gc.auto=0', '-c',
                                  'maintenance.auto=false', '-C', str(repo), *args],
                                 text=True, timeout=15).strip()


def atomic_json(path, value):
  atomic_write(path, json.dumps(value, sort_keys=True).encode() + b'\n')


def atomic_write(path, value):
  fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
  try:
    os.fchmod(fd, stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644)
    with os.fdopen(fd, 'wb') as stream:
      stream.write(value)
      stream.flush()
      os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
      os.fsync(directory)
    finally:
      os.close(directory)
  finally:
    Path(temporary).unlink(missing_ok=True)


def migrate(repo, params, checkpoint=lambda step: None):
  repo, params = Path(repo).resolve(), Path(params)
  plan_path = repo / PLAN
  if not plan_path.is_file() or plan_path.is_symlink():
    return 'no plan'
  plan = json.loads(plan_path.read_text())
  if (type(plan) is not dict or set(plan) != {'source', 'destination', 'parent', 'token'} or
      any(type(value) is not str for value in plan.values()) or
      (plan['source'], plan['destination']) not in (('Dom', 'testing'), ('StarPilot', 'stable')) or
      not re.fullmatch('[0-9a-f]{40}', plan['parent']) or not re.fullmatch('[0-9a-f]{32}', plan['token'])):
    raise ValueError('Invalid channel migration plan')
  head = git(repo, 'rev-parse', '--verify', 'HEAD')
  raw = git(repo, 'cat-file', 'commit', head)
  headers, message = raw.split('\n\n', 1)
  parents = [line[7:] for line in headers.splitlines() if line.startswith('parent ')]
  title = f"Branch-Migration: {plan['source']} -> {plan['destination']} ({plan['token']})"
  if parents != [plan['parent']] or message != title:
    return 'not migration release'
  gitdir = Path(git(repo, 'rev-parse', '--absolute-git-dir'))
  with (gitdir / 'starpilot-branch-migration.lock').open('a') as lease:
    try:
      fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
      return 'migration busy'
    completed = gitdir / ('starpilot-branch-migration-' + plan['token'] + '.done')
    pending = gitdir / 'starpilot-branch-migration.pending'
    record = {**plan, 'commit': head}
    if completed.exists():
      if completed.is_symlink() or json.loads(completed.read_text()) != record:
        raise ValueError('Migration completion does not match release')
      if pending.exists() and not pending.is_symlink() and json.loads(pending.read_text()) == record:
        pending.unlink()
      return 'already migrated'
    if pending.exists() and (pending.is_symlink() or json.loads(pending.read_text()) != record):
      raise ValueError('Another migration is pending')
    branch = git(repo, 'symbolic-ref', '--short', 'HEAD')
    if branch != plan['source'] and not (pending.exists() and branch == plan['destination']):
      return 'manual branch selection'
    target = params / 'UpdaterTargetBranch'
    saved = target.read_bytes() if target.exists() else b''
    if saved not in (b'', plan['source'].encode(), plan['destination'].encode()):
      return 'manual updater selection'
    if not params.is_dir() or target.is_symlink():
      raise ValueError('Updater Params directory is unavailable or unsafe')
    destination = 'refs/heads/' + plan['destination']
    existing = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--verify', '--quiet', destination],
                              text=True, capture_output=True, timeout=15)
    if existing.returncode not in (0, 1):
      raise ValueError('Cannot inspect destination ref')
    if existing.returncode == 0 and existing.stdout.strip() != head:
      raise ValueError('Destination local branch already contains different work')
    atomic_json(pending, record)
    checkpoint('pending')
    if existing.returncode == 1:
      git(repo, 'update-ref', destination, head, '0' * 40)
    checkpoint('destination')
    git(repo, 'symbolic-ref', 'HEAD', destination)
    checkpoint('head')
    git(repo, 'config', 'branch.' + plan['destination'] + '.remote', 'origin')
    checkpoint('remote')
    git(repo, 'config', 'branch.' + plan['destination'] + '.merge', destination)
    checkpoint('upstream')
    fetch = subprocess.run(['git', '-C', str(repo), 'config', '--get-all', 'remote.origin.fetch'],
                           text=True, capture_output=True, timeout=15)
    if fetch.returncode not in (0, 1):
      raise ValueError('Cannot inspect origin fetch configuration')
    refspec = '+' + destination + ':refs/remotes/origin/' + plan['destination']
    if refspec not in fetch.stdout.splitlines() and '+refs/heads/*:refs/remotes/origin/*' not in fetch.stdout.splitlines():
      git(repo, 'config', '--add', 'remote.origin.fetch', refspec)
    checkpoint('fetch')
    if (target.read_bytes() if target.exists() else b'') != saved:
      raise ValueError('Updater selection changed during migration')
    atomic_write(target, plan['destination'].encode())
    checkpoint('params')
    atomic_json(completed, record)
    checkpoint('completed')
    pending.unlink()
    return 'migrated to ' + plan['destination']


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
  parser.add_argument('--params', type=Path, default=Path('/data/params/d'))
  args = parser.parse_args()
  try:
    print(migrate(args.repo, args.params))
  except (OSError, ValueError, subprocess.SubprocessError) as error:
    parser.exit(1, f'Channel migration deferred: {error}\n')
