"""Real temporary files, independent profile copies and interrupted application/profile transactions."""

from copy import deepcopy
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from openpilot.tools.arabesque_firefox.test_setup import setup

spec = importlib.util.spec_from_file_location('browser_session', Path(__file__).resolve().parents[2] /
                                             'selfdrive/ui/layouts/arabesque/tools/browser_session.py')
assert spec and spec.loader
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def bundle(path, value):
  (path / 'firefox').mkdir(parents=True)
  path.chmod(0o755)
  (path / 'firefox').chmod(0o755)
  (path / 'firefox/firefox').write_text(value['version'])
  (path / 'firefox/firefox').chmod(0o755)
  (path / 'ready.json').write_text(json.dumps(value))
  (path / 'ready.json').chmod(0o644)


@pytest.fixture
def installed(tmp_path, monkeypatch):
  root = tmp_path / 'browser'
  root.mkdir(mode=0o755)
  current = setup.approved()
  current.update(bundle_revision=0, packages=[
    package for package in current['packages'] if not package['name'].startswith(('display-', 'graphics-'))])
  target = deepcopy(current)
  target.update(version='153.7.0esr', bundle_revision=1)
  bundle(root / 'runtime', current)
  (root / 'state/profile').mkdir(parents=True, mode=0o700)
  (root / 'state').chmod(0o700)
  (root / 'state/profile/prefs.js').write_text('before update')
  (root / 'state/downloads').mkdir()
  (root / 'state/downloads/document').write_text('download preserved')
  (root / '.prepare.lock').touch()
  (root / '.prepare.lock').chmod(0o644)
  real_safe = setup.safe
  monkeypatch.setattr(setup, 'safe', lambda path, uid=0, private=False: real_safe(path, os.getuid(), private))
  monkeypatch.setattr(setup, 'BROWSER_UID', os.getuid())
  monkeypatch.setattr(runtime, 'BROWSER_UID', os.getuid())
  monkeypatch.setattr(setup, 'ROOT', root)
  monkeypatch.setattr(runtime, 'STATE', root / 'state')
  monkeypatch.setattr(setup, 'approved', lambda: deepcopy(target))
  monkeypatch.setattr(setup, 'check_host', Mock())
  monkeypatch.setattr(setup, 'host_display', lambda: True)
  monkeypatch.setattr(setup, 'quiescent', Mock())
  monkeypatch.setattr(setup, 'build', bundle_builder := Mock(side_effect=lambda value, path: bundle(path, value)))
  monkeypatch.setattr(setup.shutil, 'disk_usage', lambda _: NS(free=128 * 1024**3))
  monkeypatch.setattr(runtime, 'load_setup', lambda: setup)
  jobs = []

  def job(kind, identity, lock, garbage):
    # Passing this same open-file description must not lose the controller's lock.
    with (root / '.prepare.lock').open('rb') as competitor:
      with pytest.raises(BlockingIOError):
        fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    duplicate = os.dup(lock)
    try:
      fcntl.flock(duplicate, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
      os.close(duplicate)
    jobs.append(kind)
    if kind == 'qualify':
      tx = root / 'updates' / identity
      live = root / 'state/profile/prefs.js'
      snapshot, probe = tx / 'profile-backup/profile/prefs.js', tx / 'probe-state/profile/prefs.js'
      assert len({path.stat().st_ino for path in (live, snapshot, probe)}) == 3
      probe.write_text('candidate migrated this copy')
      assert live.read_text() == snapshot.read_text() == 'before update'
    else:
      runtime.profile_job(kind, identity, garbage, Mock(), updates=root / 'updates', state=root / 'state', restore_state=root / 'restore-state')

  return NS(root=root, current=current, target=target, job=job, jobs=jobs, builder=bundle_builder)


def update(device):
  setup.maintain('update', setup.bundle_id(device.target), device.job)


def test_update_qualifies_copy_then_keeps_one_previous_pair(installed):
  update(installed)
  record = setup.journal()
  tx = installed.root / 'updates' / record['id']
  assert installed.jobs == ['copy', 'qualify', 'cleanup']
  assert record['phase'] == 'committed'
  assert setup.receipt(installed.root / 'runtime') == installed.target
  assert setup.receipt(tx / 'previous') == installed.current
  assert (tx / 'profile-backup/profile/prefs.js').read_text() == 'before update'
  assert (installed.root / 'state/profile/prefs.js').read_text() == 'before update'
  assert not list((tx / 'probe-state').iterdir())
  assert setup.inspect()['restorable']
  installed.target.update(version='153.8.0esr', bundle_revision=2)
  update(installed)
  assert len(list((installed.root / 'updates').iterdir())) == 1
  assert (installed.root / 'state/downloads/document').read_text() == 'download preserved'


@pytest.mark.parametrize('after_move', [1, 2])
def test_power_loss_finishes_application_forward_without_profile_rewind(installed, monkeypatch, after_move):
  move = setup.move
  count = 0

  def interrupted(source, target):
    nonlocal count
    move(source, target)
    count += 1
    if count == after_move:
      raise InterruptedError('power lost')

  monkeypatch.setattr(setup, 'move', interrupted)
  with pytest.raises(InterruptedError):
    update(installed)
  assert setup.inspect()['action'] == 'recover'
  if after_move == 1:
    with pytest.raises(RuntimeError, match='pending recovery'):
      setup.prepare_locked(installed.target)
  # An older branch may have used the newly activated browser before seeing the journal.
  (installed.root / 'state/profile/prefs.js').write_text('newer user data')
  monkeypatch.setattr(setup, 'move', move)
  setup.maintain('recover', '', installed.job)
  setup.maintain('recover', '', installed.job)
  assert setup.journal()['phase'] == 'committed'
  assert setup.receipt(installed.root / 'runtime') == installed.target
  assert (installed.root / 'state/profile/prefs.js').read_text() == 'newer user data'


def test_qualification_failure_recovers_old_app_without_touching_profile(installed):
  def reject(kind, *args):
    if kind == 'qualify':
      raise RuntimeError('blank candidate frame')
    installed.job(kind, *args)

  with pytest.raises(RuntimeError, match='blank'):
    setup.maintain('update', setup.bundle_id(installed.target), reject)
  assert setup.journal()['phase'] == 'qualifying'
  assert setup.receipt(installed.root / 'runtime') == installed.current
  setup.maintain('recover', '', installed.job)
  assert setup.journal() is None
  assert (installed.root / 'state/profile/prefs.js').read_text() == 'before update'


def test_failed_second_update_retains_the_previous_committed_pair(installed):
  update(installed)
  previous_record = setup.journal()
  installed.target.update(version='153.8.0esr', bundle_revision=2)

  def reject(kind, *args):
    if kind == 'qualify':
      raise InterruptedError('closed')
    installed.job(kind, *args)

  with pytest.raises(InterruptedError):
    setup.maintain('update', setup.bundle_id(installed.target), reject)
  setup.maintain('recover', '', installed.job)
  assert setup.journal() == previous_record
  assert setup.inspect()['restorable']


def test_corrupt_prior_metadata_cannot_replace_the_current_recovery_record(installed):
  update(installed)
  installed.target.update(version='153.8.0esr', bundle_revision=2)

  def reject(kind, *args):
    if kind == 'qualify':
      raise InterruptedError('closed')
    installed.job(kind, *args)

  with pytest.raises(InterruptedError):
    setup.maintain('update', setup.bundle_id(installed.target), reject)
  record = setup.journal()
  tx = installed.root / 'updates' / record['id']
  (tx / 'prior.json').write_text('{"phase":"committed"}')
  with pytest.raises(ValueError):
    setup.maintain('recover', '', installed.job)
  assert setup.journal() == record
  assert (tx / 'candidate').is_dir()


@pytest.mark.parametrize('after_move', [0, 1, 2, 3, 4])
def test_explicit_restore_preserves_newer_state_and_recovers_each_rename(installed, monkeypatch, after_move):
  update(installed)
  (installed.root / 'state/profile/prefs.js').write_text('newer data to preserve')
  move, count = setup.move, 0

  def interrupted(source, target):
    nonlocal count
    move(source, target)
    count += 1
    if count == after_move:
      raise InterruptedError('power lost')

  monkeypatch.setattr(setup, 'move', interrupted)
  if after_move:
    with pytest.raises(InterruptedError):
      setup.maintain('restore', '', installed.job)
    monkeypatch.setattr(setup, 'move', move)
    setup.maintain('recover', '', installed.job)
  else:
    setup.maintain('restore', '', installed.job)
  setup.maintain('recover', '', installed.job)
  assert setup.receipt(installed.root / 'runtime') == installed.current
  assert (installed.root / 'state/profile/prefs.js').read_text() == 'before update'
  assert (installed.root / 'recovery-state/profile/prefs.js').read_text() == 'newer data to preserve'
  assert (installed.root / 'recovery-state/downloads/document').read_text() == 'download preserved'
  assert setup.journal()['phase'] == 'restored'
  with pytest.raises(RuntimeError, match='No complete previous'):
    setup.maintain('restore', '', installed.job)


def test_interrupted_restore_copy_keeps_original_backup_and_current_profile(installed):
  update(installed)
  record = setup.journal()
  (installed.root / 'state/profile/prefs.js').write_text('newer data')

  def reject(kind, *args):
    installed.job(kind, *args)
    if kind == 'restore-copy':
      raise InterruptedError('closed')

  with pytest.raises(InterruptedError):
    setup.maintain('restore', '', reject)
  setup.maintain('recover', '', installed.job)
  assert setup.journal() == record
  assert (installed.root / 'state/profile/prefs.js').read_text() == 'newer data'
  assert (installed.root / 'updates' / record['id'] / 'profile-backup/profile/prefs.js').read_text() == 'before update'
  assert not (installed.root / 'recovery').exists()
  assert not (installed.root / 'restore-state').exists()


@pytest.mark.parametrize('after_move', [1, 2, 3, 4])
def test_restored_profile_is_never_exposed_to_the_newer_runtime(installed, monkeypatch, after_move):
  update(installed)
  (installed.root / 'state/profile/prefs.js').write_text('newer Firefox profile')
  move, count = setup.move, 0

  def interrupt(source, destination):
    nonlocal count
    move(source, destination)
    count += 1
    if count == after_move:
      raise InterruptedError('power lost')

  monkeypatch.setattr(setup, 'move', interrupt)
  with pytest.raises(InterruptedError):
    setup.maintain('restore', '', installed.job)
  profile = installed.root / 'state/profile/prefs.js'
  if profile.exists() and profile.read_text() == 'before update':
    assert setup.receipt(installed.root / 'runtime') == installed.current
    profile.write_text('recent browsing from a branch ignoring the journal')
  monkeypatch.setattr(setup, 'move', move)
  setup.maintain('recover', '', installed.job)
  assert setup.receipt(installed.root / 'runtime') == installed.current
  assert profile.read_text() == ('recent browsing from a branch ignoring the journal' if after_move == 4 else 'before update')
  assert (installed.root / 'recovery-state/profile/prefs.js').read_text() == 'newer Firefox profile'


def test_ambiguous_state_first_restore_is_preserved_without_rewinding_data(installed, monkeypatch):
  update(installed)
  original_restore = setup.restore
  monkeypatch.setattr(setup, 'restore', Mock(side_effect=InterruptedError('before first rename')))
  with pytest.raises(InterruptedError):
    setup.maintain('restore', '', installed.job)
  monkeypatch.setattr(setup, 'restore', original_restore)
  (installed.root / 'state').rename(installed.root / 'recovery-state')
  (installed.root / 'restore-state').rename(installed.root / 'state')
  profile = installed.root / 'state/profile/prefs.js'
  profile.write_text('data changed outside the recorded restore order')
  with pytest.raises(RuntimeError, match='restoration order'):
    setup.maintain('recover', '', installed.job)
  assert profile.read_text() == 'data changed outside the recorded restore order'
  assert setup.receipt(installed.root / 'runtime') == installed.target
  assert setup.journal()['phase'] == 'restore'


def test_saved_recovery_state_is_never_replaced_even_without_its_metadata_directory(installed):
  update(installed)
  saved = installed.root / 'recovery-state'
  saved.mkdir(mode=0o700)
  (saved / 'data').write_text('saved browsing data')
  assert not setup.inspect()['restorable']
  with pytest.raises(RuntimeError, match='saved recovery'):
    setup.maintain('restore', '', installed.job)
  assert (saved / 'data').read_text() == 'saved browsing data'


def test_first_update_cleanup_flushes_journal_removal_before_deleting_transaction(installed, monkeypatch):
  def reject(kind, *args):
    if kind == 'qualify':
      raise InterruptedError('owner closed')
    installed.job(kind, *args)

  with pytest.raises(InterruptedError):
    setup.maintain('update', setup.bundle_id(installed.target), reject)
  journal = installed.root / 'update.json'
  unlink, sync, remove = Path.unlink, setup.sync_dir, setup.remove_transactions
  events = []

  def tracked_unlink(path, *args, **kwargs):
    unlink(path, *args, **kwargs)
    if path == journal:
      events.append('unlinked journal')

  def tracked_sync(path):
    sync(path)
    if path == installed.root and 'unlinked journal' in events:
      events.append('flushed journal removal')

  def tracked_remove(identities):
    assert events[-1] == 'flushed journal removal'
    events.append('removed transaction')
    remove(identities)

  monkeypatch.setattr(Path, 'unlink', tracked_unlink)
  monkeypatch.setattr(setup, 'sync_dir', tracked_sync)
  monkeypatch.setattr(setup, 'remove_transactions', tracked_remove)
  setup.maintain('recover', '', installed.job)
  assert events[:3] == ['unlinked journal', 'flushed journal removal', 'removed transaction']
  assert setup.journal() is None
  assert not list((installed.root / 'updates').iterdir())


def test_unknown_activation_layout_is_preserved(installed, monkeypatch):
  original = setup.activate
  monkeypatch.setattr(setup, 'activate', Mock(side_effect=InterruptedError('power lost')))
  with pytest.raises(InterruptedError):
    update(installed)
  monkeypatch.setattr(setup, 'activate', original)
  (installed.root / 'runtime/ready.json').write_text('{}')
  with pytest.raises(ValueError):
    setup.maintain('recover', '', installed.job)
  assert setup.journal()['phase'] == 'activate'
  assert (installed.root / 'runtime/ready.json').read_text() == '{}'


def test_ambiguous_restore_does_not_move_profile_before_validating_applications(installed, monkeypatch):
  update(installed)
  (installed.root / 'state/profile/prefs.js').write_text('current data')
  restore = setup.restore
  monkeypatch.setattr(setup, 'restore', Mock(side_effect=InterruptedError('power lost before first rename')))
  with pytest.raises(InterruptedError):
    setup.maintain('restore', '', installed.job)
  monkeypatch.setattr(setup, 'restore', restore)
  tx = installed.root / 'updates' / setup.journal()['id']
  (tx / 'previous/ready.json').write_text('{}')
  with pytest.raises(ValueError):
    setup.maintain('recover', '', installed.job)
  assert (installed.root / 'state/profile/prefs.js').read_text() == 'current data'
  assert not (installed.root / 'recovery').exists()


def test_local_detection_legacy_current_component_update_and_no_downgrade(installed, monkeypatch):
  old = deepcopy(installed.current)
  old.pop('bundle_revision')
  old.pop('bundle_format')
  (installed.root / 'runtime/ready.json').write_text(json.dumps(old))
  monkeypatch.setattr(setup.urllib.request, 'urlopen', Mock(side_effect=AssertionError('network during inspection')))
  installed.target.update(installed.current)
  assert setup.inspect()['action'] == 'browse'
  installed.target['bundle_revision'] = 1
  assert setup.inspect()['action'] == 'update'
  installed.target.update(version='152.9.0esr', bundle_revision=2)
  assert setup.inspect()['action'] == 'browse'
  installed.target.update(version='153.6.0esr', bundle_revision=0)
  installed.target['packages'][0]['size'] += 1
  with pytest.raises(RuntimeError, match='revision'):
    setup.inspect()


@pytest.mark.parametrize('private_display', [False, True])
def test_same_engine_display_repair_is_automatic_and_preserves_profile(installed, monkeypatch, tmp_path, private_display):
  monkeypatch.setattr(setup, 'host_display', lambda: False)
  monkeypatch.setattr(setup, 'Path', lambda path: tmp_path / 'missing' if path == '/usr/lib/aarch64-linux-gnu/libfreetype.so.6' else Path(path))
  display_package = dict(installed.current['packages'][0], name=setup.DISPLAY_PACKAGE)
  if private_display:
    installed.current['bundle_revision'] = 1
    installed.current['packages'].append(display_package)
    (installed.root / 'runtime/ready.json').write_text(json.dumps(installed.current))
    installed.target['bundle_revision'] = 2
    installed.target['packages'].append(display_package)
  assert not setup.inspect()['repair']
  if not private_display:
    installed.target['packages'].append(display_package)
  installed.target['packages'].append(dict(installed.current['packages'][0], name=setup.FREETYPE_PACKAGE))
  assert not setup.inspect()['repair']
  installed.target['version'] = installed.current['version']
  assert setup.inspect()['repair']
  update(installed)
  assert (installed.root / 'state/profile/prefs.js').read_text() == 'before update'
  assert not setup.inspect()['restorable']
  with pytest.raises(RuntimeError, match='missing display dependencies'):
    setup.maintain('restore', '', installed.job)


def test_reset_during_receive_preserves_queued_worker_error(monkeypatch):
  monkeypatch.setattr(runtime.time, 'monotonic', lambda: 1)
  owner, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
  with owner, worker:
    session = runtime.BrowserSession.__new__(runtime.BrowserSession)
    session._next_start, session._preparing, session.ready = False, False, False
    session._operation, session._unit, session._job = 'browse', 'test', None
    session._started = session._heartbeat = 1
    session._socket, session.close = owner, Mock()
    runtime.packet(owner, {'type': 'lease'})
    runtime.packet(worker, {'type': 'error', 'message': 'Private Xvfb failed to start'})
    worker.close()
    session.poll()
    assert session.error == 'Private Xvfb failed to start'
    session.close.assert_called_once()


def test_xkb_extension_is_scoped_to_candidate_or_installed_worker(tmp_path, monkeypatch):
  monkeypatch.setattr(runtime, 'ROOT', tmp_path)
  monkeypatch.setattr(runtime, 'RUNTIME', tmp_path / 'runtime')
  identity = 'a' * 32
  extension = tmp_path / 'updates' / identity / 'candidate/isolation/xkb'
  extension.mkdir(parents=True)
  command = runtime.launch_command('test', 'test', 320, 240, 10, 'auto', 'qualify', identity)
  assert f'--property=ExtensionDirectories={extension}' in command
  command = runtime.launch_command('test', 'test', 320, 240, 10, 'auto')
  assert not any(value.startswith('--property=ExtensionDirectories=') for value in command)


def test_target_is_frozen_and_quiescence_is_checked_before_activation(installed):
  with pytest.raises(RuntimeError, match='changed'):
    setup.maintain('update', '0' * 64, installed.job)
  installed.builder.assert_not_called()
  setup.quiescent.side_effect = [None, None, RuntimeError('browser still running')]
  with pytest.raises(RuntimeError, match='still running'):
    update(installed)
  assert setup.journal()['phase'] == 'qualifying'
  assert setup.receipt(installed.root / 'runtime') == installed.current


def test_owner_cancellation_after_qualification_cannot_begin_activation(installed, monkeypatch):
  def report(message):
    if message == 'Activating verified Firefox...':
      raise InterruptedError('owner closed')

  monkeypatch.setattr(setup, 'report', report)
  with pytest.raises(InterruptedError):
    update(installed)
  assert setup.journal()['phase'] == 'qualifying'
  assert setup.receipt(installed.root / 'runtime') == installed.current


def test_quiescence_checks_actual_browser_uid_and_conflicting_units(tmp_path, monkeypatch):
  proc = tmp_path / 'proc'
  proc.mkdir()
  group = tmp_path / 'cgroup'
  group.write_text('0::/system.slice/arabesque-firefox-controller.service\n')
  real_path = setup.Path
  monkeypatch.setattr(setup, 'Path', lambda value: proc if value == '/proc' else group if value == '/proc/self/cgroup' else real_path(value))
  monkeypatch.setattr(setup.pwd, 'getpwuid', Mock(side_effect=KeyError))
  monkeypatch.setattr(setup, 'BROWSER_UID', os.getuid())
  (proc / '1234').mkdir()
  with pytest.raises(RuntimeError, match='running processes'):
    setup.quiescent()
  (proc / '1234').rmdir()
  monkeypatch.setattr(setup.subprocess, 'check_output', query := Mock(return_value='arabesque-firefox-controller.service loaded active running\n'))
  setup.quiescent()
  query.return_value += 'arabesque-firefox-job.service loaded active running\n'
  with pytest.raises(RuntimeError, match='Close all Firefox'):
    setup.quiescent()


def test_profile_copy_preserves_internal_links_and_rejects_escape_and_special_files(tmp_path, monkeypatch):
  source = tmp_path / 'source'
  source.mkdir()
  (source / 'prefs.js').write_text('data')
  (source / 'internal').symlink_to('prefs.js')
  (source / '.parentlock').symlink_to('missing-host-lock')
  runtime.copy_profile(source, tmp_path / 'copy', Mock())
  assert (tmp_path / 'copy/internal').is_symlink()
  assert not (tmp_path / 'copy/.parentlock').exists()
  assert (source / 'prefs.js').stat().st_ino != (tmp_path / 'copy/prefs.js').stat().st_ino
  (source / 'external').symlink_to('../secret')
  with pytest.raises(RuntimeError, match='outside'):
    runtime.copy_profile(source, tmp_path / 'bad-copy', Mock())
  (source / 'external').unlink()
  os.mkfifo(source / 'pipe')
  with pytest.raises(RuntimeError, match='special'):
    runtime.copy_profile(source, tmp_path / 'special-copy', Mock())
  (tmp_path / 'dangling').symlink_to('absent-profile')
  with pytest.raises(RuntimeError, match='real directory'):
    runtime.copy_profile(tmp_path / 'dangling', tmp_path / 'root-link-copy', Mock())


def test_low_space_refuses_profile_copy_before_writing(installed, monkeypatch):
  monkeypatch.setattr(setup.shutil, 'disk_usage', lambda _: NS(free=setup.FLOOR))
  tx = installed.root / 'updates' / ('a' * 32)
  tx.mkdir(parents=True)
  (tx / 'profile-backup').mkdir()
  (tx / 'probe-state').mkdir()
  with pytest.raises(RuntimeError, match='storage'):
    runtime.profile_job('copy', tx.name, [], Mock(), updates=tx.parent, state=installed.root / 'state')
  assert not list((tx / 'profile-backup').iterdir())


@pytest.mark.parametrize('kind', ['empty', 'populated', 'unsafe', 'symlink'])
def test_private_directory_repairs_only_interrupted_empty_root_owned_creation(tmp_path, monkeypatch, kind):
  path = tmp_path / 'private'
  path.mkdir(mode=0o700)
  if kind == 'populated':
    (path / 'data').write_text('preserved')
  elif kind == 'unsafe':
    path.chmod(0o777)
  elif kind == 'symlink':
    path.rmdir()
    path.symlink_to(tmp_path, target_is_directory=True)
  lstat, chown = Path.lstat, os.chown
  pending = True

  def interrupted_owner(value, *args, **kwargs):
    info = lstat(value, *args, **kwargs)
    if value == path and pending:
      fields = list(info)
      fields[4] = 0
      return os.stat_result(fields)
    return info

  def assign(value, uid, gid):
    nonlocal pending
    chown(value, uid, gid)
    pending = False

  monkeypatch.setattr(setup, 'BROWSER_UID', os.getuid())
  monkeypatch.setattr(Path, 'lstat', interrupted_owner)
  monkeypatch.setattr(setup.os, 'chown', assign_owner := Mock(side_effect=assign))
  if kind == 'empty':
    setup.private_directory(path)
    assign_owner.assert_called_once_with(path, os.getuid(), os.getuid())
  else:
    with pytest.raises(RuntimeError, match='Unsafe'):
      setup.private_directory(path)
    assign_owner.assert_not_called()
    if kind == 'populated':
      assert (path / 'data').read_text() == 'preserved'


def test_private_directory_requires_a_directory_even_with_correct_owner(tmp_path, monkeypatch):
  path = tmp_path / 'not-a-directory'
  path.write_text('preserved')
  path.chmod(0o700)
  monkeypatch.setattr(setup, 'BROWSER_UID', os.getuid())
  with pytest.raises(RuntimeError, match='private directory'):
    setup.private_directory(path)
  assert path.read_text() == 'preserved'


def test_digest_cache_recovers_stale_alias_and_corrupt_digest_entry(tmp_path, monkeypatch):
  data = b'new GTK library'
  package = {'name': 'libgtk.deb', 'url': 'https://example.invalid/package', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
  (tmp_path / package['name']).write_bytes(b'old package')
  digest_path = tmp_path / (package['sha256'] + '-' + package['name'])
  digest_path.write_bytes(b'corrupt')
  response = io.BytesIO(data)
  response.url = package['url']
  monkeypatch.setattr(setup, 'safe', Mock())
  monkeypatch.setattr(setup.urllib.request, 'urlopen', fetch := Mock(return_value=response))
  assert setup.download(package, tmp_path) == digest_path
  assert digest_path.read_bytes() == data
  fetch.assert_called_once()


@pytest.mark.parametrize('value', ['../escape', '/data/runtime', '', 1, 'g' * 32])
def test_transaction_paths_reject_arbitrary_input(value):
  with pytest.raises(ValueError):
    setup.transaction_id(value)


@pytest.mark.parametrize('patch', [{'bundle_format': True}, {'bundle_format': 2}, {'bundle_revision': -1}, {'packages': ['bad']}, {'version': 'latest'}])
def test_manifest_boundary_rejects_unsupported_or_malformed_bundles(patch):
  value = setup.approved()
  value.update(patch)
  with pytest.raises(ValueError):
    setup.manifest(value)
