import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location('firefox_setup', Path(__file__).with_name('setup.py'))
assert spec is not None and spec.loader is not None
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_corrupt_cached_package_is_rejected(tmp_path):
  path = tmp_path / 'package.deb'
  path.write_bytes(b'good')
  package = {'name': path.name, 'size': 4, 'sha256': hashlib.sha256(b'good').hexdigest()}
  setup.verify(path, package)
  path.write_bytes(b'evil')
  with pytest.raises(ValueError, match='checksum'):
    setup.verify(path, package)


def test_shipped_offline_payload_matches_the_pinned_bundle():
  offline = Path(setup.__file__).with_name('offline')
  expected = set()
  for package in setup.approved()['packages']:
    digest = package.get('sha512', package.get('sha256'))
    path = offline / f'{digest}-{package["name"]}'
    setup.verify(path, package)
    expected.add(path)
  assert set(offline.iterdir()) == expected


@pytest.mark.parametrize('corrupt', [False, True])
def test_bundled_package_is_verified_in_the_private_cache_without_network(tmp_path, monkeypatch, corrupt):
  data = b'good'
  package = {'name': 'test.deb', 'url': 'https://example.invalid/test.deb',
             'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
  monkeypatch.setattr(setup, '__file__', str(tmp_path / 'setup.py'))
  offline = tmp_path / 'offline'
  cache = tmp_path / 'cache'
  offline.mkdir()
  cache.mkdir()
  name = f'{package["sha256"]}-{package["name"]}'
  (offline / name).write_bytes(b'evil' if corrupt else data)
  fetch = Mock(side_effect=AssertionError('network during offline installation'))
  monkeypatch.setattr(setup.urllib.request, 'urlopen', fetch)
  if corrupt:
    with pytest.raises(ValueError, match='checksum'):
      setup.download(package, cache)
    assert not list(cache.iterdir())
  else:
    assert setup.download(package, cache).read_bytes() == data
  fetch.assert_not_called()


def test_download_reports_progress_and_reuses_verified_package(tmp_path, monkeypatch):
  data = b'verified browser'
  package = {'name': 'firefox-test.tar.xz', 'url': 'https://example.invalid/browser',
             'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
  response = io.BytesIO(data)
  response.url = package['url']
  fetch, progress = Mock(return_value=response), Mock()
  monkeypatch.setattr(setup.urllib.request, 'urlopen', fetch)
  monkeypatch.setattr(setup, 'report', progress)
  monkeypatch.setattr(setup, 'safe', Mock())
  path = setup.download(package, tmp_path)
  assert path.read_bytes() == data
  progress.assert_called_once_with('Downloading Firefox… 100%')
  assert setup.download(package, tmp_path) == path
  fetch.assert_called_once()


def test_cancelled_download_never_publishes_partial_package(tmp_path, monkeypatch):
  data = b'partial'
  response = io.BytesIO(data)
  response.url = 'https://example.invalid/browser'
  package = {'name': 'firefox-test.tar.xz', 'url': response.url, 'size': len(data),
             'sha256': hashlib.sha256(data).hexdigest()}
  monkeypatch.setattr(setup.urllib.request, 'urlopen', Mock(return_value=response))
  monkeypatch.setattr(setup, 'report', Mock(side_effect=InterruptedError('cancelled')))
  with pytest.raises(InterruptedError):
    setup.download(package, tmp_path)
  assert not list(tmp_path.iterdir())


def test_archive_cannot_escape_the_private_runtime(tmp_path):
  path = tmp_path / 'browser.tar.xz'
  with tarfile.open(path, 'w:xz') as archive:
    entry = tarfile.TarInfo('../escaped')
    entry.size = 4
    archive.addfile(entry, io.BytesIO(b'bad!'))
  destination = tmp_path / 'runtime'
  destination.mkdir()
  with pytest.raises(tarfile.FilterError):
    setup.unpack(path, destination)
  assert not (tmp_path / 'escaped').exists()


def test_preparation_refuses_unprivileged_host_without_writing(tmp_path, monkeypatch):
  monkeypatch.setattr(setup.os, 'geteuid', lambda: 1000)
  monkeypatch.setattr(setup, 'ROOT', tmp_path / 'browser')
  with pytest.raises(RuntimeError, match='ARM64 AGNOS'):
    setup.prepare()
  assert not (tmp_path / 'browser').exists()


@pytest.mark.parametrize('version', ['19.8.1', '20.2-starpilot.1', '20.2.1', '20.2.2'])
def test_private_display_bundle_has_an_explicit_agnos_allowlist(tmp_path, monkeypatch, version):
  target = setup.approved()
  version_file = tmp_path / 'VERSION'
  version_file.write_text(version)
  monkeypatch.setattr(setup.os, 'geteuid', lambda: 0)
  monkeypatch.setattr(setup.platform, 'machine', lambda: 'aarch64')
  monkeypatch.setattr(setup, 'Path', lambda path: version_file if path == '/VERSION' else Path(path))
  if version == '20.2.2':
    with pytest.raises(RuntimeError, match='ARM64 AGNOS'):
      setup.check_host(target)
  else:
    setup.check_host(target)


@pytest.mark.parametrize('existing', ['missing', 'directory', 'symlink', 'unsafe'])
def test_first_use_under_unwritable_data(tmp_path, monkeypatch, existing):
  if not shutil.which('unshare') or not shutil.which('setpriv'):
    raise pytest.skip.Exception('Linux user namespaces and setpriv are required')
  probe = subprocess.run(['unshare', '-Ur', 'true'], capture_output=True, check=False)
  if probe.returncode:
    raise pytest.skip.Exception('Linux user namespaces are unavailable')

  spec = importlib.util.spec_from_file_location(
    'browser_session', Path(__file__).resolve().parents[2] / 'selfdrive/ui/layouts/arabesque/tools/browser_session.py')
  assert spec is not None and spec.loader is not None
  runtime = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(runtime)
  data = tmp_path / 'data'
  data.mkdir()
  root = data / 'arabesque-firefox'
  target = tmp_path / 'target'
  target.mkdir()
  (target / 'sentinel').write_text('preserved')
  if existing == 'symlink':
    root.symlink_to(target)
  elif existing != 'missing':
    root.mkdir(mode=0o755)
    if existing == 'unsafe':
      root.chmod(0o777)
  data.chmod(0o555)
  monkeypatch.setattr(runtime, 'ROOT', root)
  command = runtime.preparation_command('test', 'test')
  prestart = next((shlex.split(p.removeprefix('--property=ExecStartPre=')) for p in command
                   if p.startswith('--property=ExecStartPre=')), [])
  if prestart:
    assert prestart[0].startswith('-+')
    prestart[0] = prestart[0][2:]
    assert prestart == ['/usr/bin/mkdir', '-m', '0755', str(root)]
  caps = next(p.split('=', 2)[2] for p in command if p.startswith('--property=CapabilityBoundingSet='))
  caps = ','.join('+' + cap.removeprefix('CAP_').lower() for cap in caps.split())
  installer = '''
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('setup', sys.argv[1])
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
setup.ROOT = Path(sys.argv[2])
setup.platform.machine = lambda: 'aarch64'
read_text = Path.read_text
Path.read_text = lambda path, *a, **kw: '19.8.1' if str(path) == '/VERSION' else read_text(path, *a, **kw)
setup.prepare_locked = lambda manifest: None
assert os.geteuid() == 0
assert int(next(line.split(':')[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:')), 16) == 1
setup.prepare()
'''
  runner = '''
import json, os, subprocess, sys
prestart = json.loads(sys.argv[1])
if prestart:
  subprocess.run(prestart, capture_output=True, check=False)
os.execvp('setpriv', ['setpriv', '--no-new-privs', '--bounding-set=-all,' + sys.argv[2],
                      sys.executable, '-c', sys.argv[3], sys.argv[4], sys.argv[5]])
'''
  try:
    result = subprocess.run(['unshare', '-Ur', sys.executable, '-c', runner, json.dumps(prestart), caps, installer,
                             str(Path(__file__).with_name('setup.py')), str(root)], capture_output=True, text=True, check=False)
    if existing in ('missing', 'directory'):
      assert result.returncode == 0, result.stderr
      assert root.stat().st_mode & 0o777 == 0o755
      assert (root / '.prepare.lock').is_file()
    else:
      assert result.returncode != 0
      assert ('Refusing symlink' if existing == 'symlink' else 'Unsafe browser directory ownership') in result.stderr
      if existing == 'unsafe':
        assert root.stat().st_mode & 0o777 == 0o777
    assert (target / 'sentinel').read_text() == 'preserved'
    assert len(list(target.iterdir())) == 1
  finally:
    data.chmod(0o755)


def test_restore_needs_no_access_to_private_state_contents(tmp_path):
  if not shutil.which('unshare') or not shutil.which('setpriv'):
    raise pytest.skip.Exception('Linux user namespaces and setpriv are required')
  if subprocess.run(['unshare', '-Ur', 'true'], capture_output=True, check=False).returncode:
    raise pytest.skip.Exception('Linux user namespaces are unavailable')
  script = '''
import importlib.util, json, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('setup', sys.argv[1])
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
root = Path(sys.argv[2])
root.mkdir(mode=0o755)
setup.ROOT, setup.BROWSER_UID = root, 0
setup.quiescent = lambda: None
assert os.geteuid() == 0
assert int(next(line.split(':')[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:')), 16) == 1
previous = setup.approved()
target = dict(previous, version='153.7.0esr', bundle_revision=previous['bundle_revision'] + 1)
identity = 'a' * 32
tx = root / 'updates' / identity
(root / 'updates').mkdir(mode=0o755)
tx.mkdir(mode=0o755)
for path, value in ((root / 'runtime', target), (tx / 'previous', previous)):
  (path / 'firefox').mkdir(parents=True)
  path.chmod(0o755)
  (path / 'firefox').chmod(0o755)
  (path / 'firefox/firefox').write_text('test browser')
  (path / 'firefox/firefox').chmod(0o755)
  (path / 'ready.json').write_text(json.dumps(value))
  (path / 'ready.json').chmod(0o644)
for name, data in (('state', 'newer data'), ('restore-state', 'pre-update data')):
  directory = root / name
  directory.mkdir(mode=0o700)
  (directory / 'data').write_text(data)
  # An unmapped second UID is unavailable here; mode 000 models the controller's lack of directory access.
  directory.chmod(0)
record = {'format': 1, 'id': identity, 'phase': 'restore', 'target': target, 'previous': previous}
try:
  setup.restore(record)
  assert setup.journal()['phase'] == 'restored'
  assert setup.receipt(root / 'runtime') == previous
  assert (root / 'state').stat().st_mode & 0o777 == 0
  assert (root / 'recovery-state').stat().st_mode & 0o777 == 0
  (root / 'state').chmod(0o700)
  (root / 'recovery-state').chmod(0o700)
  assert (root / 'state/data').read_text() == 'pre-update data'
  assert (root / 'recovery-state/data').read_text() == 'newer data'
  (root / 'state/data').write_text('recent browsing after interruption')
  setup.restore(record)
  assert (root / 'state/data').read_text() == 'recent browsing after interruption'
  assert (root / 'recovery-state/data').read_text() == 'newer data'
finally:
  for name in ('state', 'restore-state', 'recovery-state'):
    if (root / name).exists():
      (root / name).chmod(0o700)
'''
  result = subprocess.run(['unshare', '-Ur', 'setpriv', '--bounding-set=-all,+chown', '--no-new-privs', sys.executable, '-c', script,
                           str(Path(__file__).with_name('setup.py')), str(tmp_path / 'browser')],
                          capture_output=True, text=True, timeout=10, check=False)
  assert result.returncode == 0, result.stderr
