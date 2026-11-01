import fcntl
import hashlib
import os
import py_compile
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.host_runtime import GALAXY, LIVE_RELOAD, LIVE_TAG, LIVE_VERSION, HostRuntime, file_lock, main, parse


class TestDeveloperRuntime(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name) / 'source with spaces'
    self.root.mkdir()
    self.git('init', '-q')
    self.git('config', 'user.email', 'test@example.invalid')
    self.git('config', 'user.name', 'Test')
    (self.root / '.gitignore').write_text('.host_runtime/\n*.so\n')
    (self.root / 'source.py').write_text('original\n')
    self.git('add', '.')
    self.git('commit', '-qm', 'fixture')
    self.runtime = HostRuntime(self.root, 'shared', system='Darwin', machine='arm64')

  def git(self, *args):
    return subprocess.check_output(['git', *args], cwd=self.root, text=True)

  def test_sync_includes_local_edits_and_untracked_but_not_device_artifacts(self):
    (self.root / 'source.py').write_text('local edit\n')
    (self.root / 'new.py').write_text('new\n')
    (self.root / 'native.so').write_bytes(b'device binary')
    index_before = hashlib.sha256((self.root / '.git/index').read_bytes()).hexdigest()
    with self.runtime.locked():
      self.runtime.sync()
    self.assertEqual((self.runtime.work / 'source.py').read_text(), 'local edit\n')
    self.assertTrue((self.runtime.work / 'new.py').is_file())
    self.assertFalse((self.runtime.work / 'native.so').exists())
    self.assertFalse((self.runtime.work / '.git').is_symlink())
    self.assertEqual(hashlib.sha256((self.root / '.git/index').read_bytes()).hexdigest(), index_before)
    subprocess.check_call(['git', 'add', 'source.py'], cwd=self.runtime.work)
    self.assertEqual(hashlib.sha256((self.root / '.git/index').read_bytes()).hexdigest(), index_before)

  def test_resync_removes_deleted_source_and_keeps_host_builds_incremental(self):
    self.runtime.sync()
    native = self.runtime.work / 'native.so'
    native.write_bytes(b'host binary')
    source = self.runtime.work / 'source.py'
    before = source.stat().st_mtime_ns
    self.runtime.sync()
    self.assertEqual(source.stat().st_mtime_ns, before)
    (self.root / 'source.py').unlink()
    self.runtime.sync()
    self.assertFalse(source.exists())
    self.assertEqual(native.read_bytes(), b'host binary')

  def test_sync_skips_tracked_native_outputs_and_preserves_host_builds(self):
    artifacts = ('native.a', 'native.so', 'native.so.1', 'native.dylib', 'native.o', 'native.os', 'openpilot/tools/replay/replay')
    for name in artifacts:
      (self.root / name).parent.mkdir(parents=True, exist_ok=True)
      (self.root / name).write_bytes(b'device binary')
    self.git('add', '-f', *artifacts)
    self.runtime.sync()
    for name in artifacts:
      self.assertFalse((self.runtime.work / name).exists(), name)
    native = self.runtime.work / 'native.a'
    native.write_bytes(b'host binary')
    replay = self.runtime.work / 'openpilot/tools/replay/replay'
    replay.parent.mkdir(parents=True, exist_ok=True)
    replay.write_bytes(b'host replay')
    self.runtime.sync()
    self.assertEqual(native.read_bytes(), b'host binary')
    self.assertEqual(replay.read_bytes(), b'host replay')

  def test_command_build_prepares_mpc_import_dependency(self):
    solver = 'openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so'
    for command in ('c3', 'c4', 'onroad', 'galaxy', 'python', 'pytest', 'shell'):
      with self.subTest(command=command), patch('tools.host_runtime.run') as build:
        self.runtime.build(command, 4)
        self.assertIn(solver, build.call_args.args[0])

  def test_cache_environment_ignores_external_imports_and_settings(self):
    with patch.dict(os.environ, PYTHONPATH='/elsewhere', PARAMS_ROOT='/real/params', OPENPILOT_PREFIX='real', CC='cross-compiler'):
      env = self.runtime.environment()
    self.assertNotIn('/elsewhere', env['PYTHONPATH'])
    self.assertEqual(env['CC'], '/usr/bin/clang')
    self.assertTrue(Path(env['PARAMS_ROOT']).is_relative_to(self.runtime.cache))
    self.assertNotEqual(env['OPENPILOT_PREFIX'], 'real')
    self.assertEqual(Path(env['STARPORT_DIR']), self.runtime.cache / 'starport')
    second = HostRuntime(self.root, 'shared', system='Darwin', machine='arm64')
    cabana = HostRuntime(self.root, 'cabana', system='Darwin', machine='arm64')
    self.assertEqual(second.prefix, self.runtime.prefix)
    self.assertNotEqual(cabana.prefix, self.runtime.prefix)

  def test_cache_symlink_cannot_write_into_source(self):
    self.runtime.cache.mkdir(parents=True)
    self.runtime.work.symlink_to(self.root, target_is_directory=True)
    with self.assertRaises(RuntimeError):
      self.runtime.sync()
    self.assertEqual((self.root / 'source.py').read_text(), 'original\n')

  def test_cache_cannot_borrow_source_git_index(self):
    self.runtime.work.mkdir(parents=True)
    (self.runtime.work / '.git').symlink_to(self.root / '.git', target_is_directory=True)
    with self.assertRaisesRegex(RuntimeError, 'shared Git'):
      self.runtime.sync_git()

  def test_command_parsing_preserves_tool_arguments(self):
    self.assertEqual(parse(['c3', '8', '--demo']), ('c3', 8, ['--demo']))
    self.assertEqual(parse(['juggle', '4', '--demo']), ('jotpluggler', 4, ['--demo']))
    self.assertEqual(parse(['jotpluggler', '4', '--demo']), ('jotpluggler', 4, ['--demo']))
    self.assertEqual(parse(['plotjuggler', '4', '--demo']), ('plotjuggler', 4, ['--demo']))
    self.assertEqual(parse(['python', '-c', 'print(1)'])[2], ['-c', 'print(1)'])
    self.assertEqual(parse(['pytest', '-n', '2'])[2], ['-n', '2'])
    self.assertEqual(parse(['--help'])[0], 'help')
    for args in (['unknown'], ['c4', '0'], ['sync', '../oops'], ['sync', 'shared', 'extra']):
      with self.subTest(args=args), self.assertRaises(ValueError):
        parse(args)

  def galaxy_fixture(self):
    galaxy = self.root / GALAXY
    (galaxy / 'web').mkdir(parents=True)
    (galaxy / 'server.py').write_text('server\n')
    (galaxy / 'web/app.js').write_text('app\n')
    (galaxy / 'web/index.html').write_text('<body>\n</body>\n')
    self.runtime.sync()
    return galaxy

  def test_live_galaxy_mirrors_package_edits_without_bytecode(self):
    galaxy = self.galaxy_fixture()
    (galaxy / '__pycache__').mkdir()
    (galaxy / '__pycache__/server.pyc').write_bytes(b'bytecode')
    sources = self.runtime.galaxy_sources()
    self.assertEqual(set(sources), {GALAXY / 'server.py', GALAXY / 'web/app.js', GALAXY / 'web/index.html'})
    (galaxy / 'web/app.js').write_text('edited\n')
    (galaxy / 'server.py').unlink()
    self.runtime.mirror([GALAXY / 'web/app.js'], [GALAXY / 'server.py'])
    self.assertEqual((self.runtime.work / GALAXY / 'web/app.js').read_text(), 'edited\n')
    self.assertFalse((self.runtime.work / GALAXY / 'server.py').exists())

  def run_live(self, edits, before_edit, **options):
    """Run live_galaxy with a fake server, applying one edit per poll, then Ctrl+C."""
    edits = iter(edits)
    children = []

    class Child:
      def __init__(self):
        self.returncode = None
        self.signals = []
        children.append(self)

      def poll(self):
        return self.returncode

      def send_signal(self, signum):
        self.signals.append(signum)
        self.returncode = 0

      def terminate(self):
        self.send_signal(signal.SIGTERM)

      def kill(self):
        self.send_signal(signal.SIGKILL)

      def wait(self, timeout=None):
        if self.returncode is None:
          assert timeout is not None
          raise subprocess.TimeoutExpired('galaxy', timeout)
        return self.returncode

    real_popen = subprocess.Popen

    def spawn(argv, *args, **kwargs):
      # Only the server is faked; source selection must really run.
      return real_popen(argv, *args, **kwargs) if argv[0] == 'git' else Child()

    def tick(_interval):
      before_edit()
      edit = next(edits, None)
      if edit is None:
        raise KeyboardInterrupt
      edit()

    with patch('tools.host_runtime.subprocess.Popen', spawn), patch('tools.host_runtime.time.sleep', tick):
      self.assertEqual(self.runtime.live_galaxy(['--port', '8099'], **options), 130)
    return children

  def test_live_galaxy_restarts_only_for_backend_edits(self):
    galaxy = self.galaxy_fixture()
    (galaxy / '.gitignore').write_text('.*.swp\n')
    web = self.runtime.work / GALAXY / 'web'
    versions = []

    def before_edit():
      versions.append((web / LIVE_VERSION).read_text())
      self.assertIn(LIVE_TAG, (web / 'index.html').read_text())

    children = self.run_live([lambda: (galaxy / 'web/app.js').write_text('frontend edit\n'),
                              lambda: (galaxy / '.server.py.swp').write_bytes(b'editor swap'),
                              lambda: (galaxy / '.server.py.swp').unlink(),
                              lambda: (galaxy / 'tests').mkdir(),
                              lambda: (galaxy / 'tests/test_offline_road_maps.mjs').write_text('test edit\n'),
                              lambda: (galaxy / 'tests/test_offline_road_maps.mjs').unlink(),
                              lambda: (galaxy / 'test_projection_layout.py').write_text('test edit\n'),
                              lambda: (galaxy / 'server.py').write_text('backend edit\n')], before_edit)
    self.assertEqual(len(children), 2)
    self.assertTrue(all(child.returncode == 0 for child in children))
    self.assertEqual(children[0].signals, [signal.SIGINT])
    self.assertEqual(children[1].signals, [signal.SIGTERM])  # Ctrl+C already reached it.
    self.assertEqual((web / 'app.js').read_text(), 'frontend edit\n')
    self.assertEqual((self.runtime.work / GALAXY / 'server.py').read_text(), 'backend edit\n')
    self.assertFalse((self.runtime.work / GALAXY / '.server.py.swp').exists())
    self.assertEqual(len(set(versions)), 3)  # Start, frontend edit, backend restart; never the swap file.
    self.assertEqual((web / 'index.html').read_text(), '<body>\n</body>\n')
    self.assertFalse((web / LIVE_RELOAD).exists() or (web / LIVE_VERSION).exists())

  def test_live_galaxy_without_autoreload_leaves_the_page_alone(self):
    galaxy = self.galaxy_fixture()
    web = self.runtime.work / GALAXY / 'web'

    def before_edit():
      self.assertEqual((web / 'index.html').read_text(), '<body>\n</body>\n')
      self.assertFalse((web / LIVE_RELOAD).exists() or (web / LIVE_VERSION).exists())

    children = self.run_live([lambda: (galaxy / 'web/app.js').write_text('frontend edit\n'),
                              lambda: (galaxy / 'server.py').write_text('backend edit\n')],
                             before_edit, autoreload=False)
    self.assertEqual(len(children), 2)  # Edits are still mirrored and Python still restarts.
    self.assertEqual((web / 'app.js').read_text(), 'frontend edit\n')

  def test_live_reload_tag_survives_resync_once(self):
    galaxy = self.galaxy_fixture()
    page = self.runtime.work / GALAXY / 'web/index.html'
    self.runtime.live_reload(1)
    self.runtime.live_reload()
    self.assertEqual(page.read_text().count(LIVE_TAG), 1)
    self.runtime.sync()  # Another shared command restores the checkout's page.
    self.assertNotIn(LIVE_TAG, page.read_text())
    self.runtime.live_reload()
    self.assertEqual(page.read_text().count(LIVE_TAG), 1)
    self.assertNotIn(LIVE_TAG, (galaxy / 'web/index.html').read_text())
    script = self.runtime.work / GALAXY / 'web' / LIVE_RELOAD
    self.runtime.end_live_reload()
    self.runtime.live_reload()
    self.assertTrue(script.is_file())
    self.assertTrue((page.parent / LIVE_VERSION).is_file())

  def test_live_mirror_removes_stale_bytecode_and_detects_preserved_timestamps(self):
    self.galaxy_fixture()
    module = GALAXY / 'sample.py'
    source = self.root / module
    source.write_text('value = 1\n')
    self.runtime.mirror([module], [])
    cached = self.runtime.work / module
    py_compile.compile(str(cached), doraise=True)
    timestamp = source.stat().st_mtime_ns
    before = self.runtime.galaxy_sources()
    source.write_text('value = 2\n')
    os.utime(source, ns=(timestamp, timestamp))
    self.assertNotEqual(before[module], self.runtime.galaxy_sources()[module])
    self.runtime.mirror([module], [])
    result = subprocess.check_output([sys.executable, '-B', '-c', 'import sample; print(sample.value)'], cwd=cached.parent, text=True)
    self.assertEqual(result.strip(), '2')
    self.assertFalse(list((cached.parent / '__pycache__').glob('sample.*.pyc')))

  def test_mirror_replaces_files_without_truncating_existing_readers(self):
    galaxy = self.galaxy_fixture()
    module = GALAXY / 'web/app.js'
    with (self.runtime.work / module).open() as reader:
      (galaxy / 'web/app.js').write_text('new contents\n')
      self.runtime.mirror([module], [])
      self.assertEqual(reader.read(), 'app\n')
    self.assertEqual((self.runtime.work / module).read_text(), 'new contents\n')

  def test_sync_removes_deleted_live_added_files(self):
    galaxy = self.galaxy_fixture()
    module = GALAXY / 'new.py'
    (galaxy / 'new.py').write_text('new\n')
    self.runtime.mirror([module], [])
    (galaxy / 'new.py').unlink()
    self.runtime.sync()
    self.assertFalse((self.runtime.work / module).exists())

  def test_live_sources_and_sync_agree_on_tracked_ignored_files_and_symlinks(self):
    galaxy = self.galaxy_fixture()
    (galaxy / '.gitignore').write_text('ignored.py\n')
    (galaxy / 'ignored.py').write_text('tracked\n')
    self.git('add', '-f', str(GALAXY / 'ignored.py'))
    link = GALAXY / 'linked.py'
    (self.root / link).symlink_to('ignored.py')
    sources = self.runtime.galaxy_sources()
    self.assertIn(GALAXY / 'ignored.py', sources)
    self.assertIn(link, sources)
    self.runtime.mirror([link, GALAXY / 'ignored.py'], [])
    self.assertTrue((self.runtime.work / link).is_symlink())
    self.assertEqual((self.runtime.work / link).read_text(), 'tracked\n')

  def test_live_start_reconciles_edits_and_deletions_during_build(self):
    galaxy = self.galaxy_fixture()
    (galaxy / 'web/app.js').write_text('edited during build\n')
    (galaxy / 'server.py').unlink()

    def check():
      self.assertEqual((self.runtime.work / GALAXY / 'web/app.js').read_text(), 'edited during build\n')
      self.assertFalse((self.runtime.work / GALAXY / 'server.py').exists())

    self.run_live([], check)

  def test_live_retries_failed_copy_without_another_edit(self):
    galaxy = self.galaxy_fixture()
    module = GALAXY / 'web/app.js'
    original = self.runtime.copy_source
    failures = []

    def copy(name):
      if name == module and (galaxy / 'web/app.js').read_text() == 'edited\n' and not failures:
        failures.append(name)
        raise FileNotFoundError('editor replaced the file')
      original(name)

    with patch.object(self.runtime, 'copy_source', copy):
      self.run_live([lambda: (galaxy / 'web/app.js').write_text('edited\n'), lambda: None], lambda: None)
    self.assertEqual(failures, [module])
    self.assertEqual((self.runtime.work / module).read_text(), 'edited\n')

  def test_live_rejects_another_owner_of_the_same_cache(self):
    self.galaxy_fixture()
    with file_lock(self.runtime.cache / 'galaxy-live-lock'), patch('tools.host_runtime.subprocess.Popen') as spawn:
      with self.assertRaisesRegex(RuntimeError, 'already owns'):
        self.runtime.live_galaxy(['--port', '8099'])
    spawn.assert_not_called()

  def test_live_child_exit_and_start_failure_clean_up(self):
    self.galaxy_fixture()
    web = self.runtime.work / GALAXY / 'web'
    for code in (0, 2, -signal.SIGTERM):
      child = Mock(returncode=code)
      child.poll.return_value = code
      with patch.object(self.runtime, 'galaxy_sources', return_value=self.runtime.galaxy_sources()), \
           patch('tools.host_runtime.subprocess.Popen', return_value=child):
        self.assertEqual(self.runtime.live_galaxy([]), code if code >= 0 else 128 - code)
      self.assertFalse((web / LIVE_VERSION).exists())
    with patch.object(self.runtime, 'galaxy_sources', return_value=self.runtime.galaxy_sources()), \
         patch('tools.host_runtime.subprocess.Popen', side_effect=OSError('spawn failed')):
      with self.assertRaisesRegex(OSError, 'spawn failed'):
        self.runtime.live_galaxy([])
    self.assertFalse((web / LIVE_VERSION).exists())
    self.assertNotIn(LIVE_TAG, (web / 'index.html').read_text())

  def test_live_termination_stops_child_and_restores_handlers(self):
    self.galaxy_fixture()
    for signum in (signal.SIGTERM, signal.SIGHUP):
      previous = signal.getsignal(signum)
      child = Mock(returncode=None)
      child.poll.return_value = None

      def tick(_interval, signum=signum):
        handler = signal.getsignal(signum)
        assert callable(handler)
        handler(signum, None)

      with patch.object(self.runtime, 'galaxy_sources', return_value=self.runtime.galaxy_sources()), \
           patch('tools.host_runtime.subprocess.Popen', return_value=child), \
           patch('tools.host_runtime.time.sleep', tick), patch('tools.host_runtime.stop') as stop:
        self.assertEqual(self.runtime.live_galaxy([]), 128 + signum)
      stop.assert_called_once_with(child, interrupted=False)
      self.assertEqual(signal.getsignal(signum), previous)
      self.assertFalse((self.runtime.work / GALAXY / 'web' / LIVE_VERSION).exists())

  def test_live_galaxy_refuses_a_taken_port_before_syncing(self):
    with socket.socket() as taken:
      taken.bind(('127.0.0.1', 0))
      taken.listen()
      port = str(taken.getsockname()[1])
      with patch('tools.host_runtime.ROOT', self.root), patch.object(HostRuntime, 'sync') as sync, \
           self.assertRaisesRegex(RuntimeError, f'Port {port} is already in use'):
        main(['galaxy', '--live', '--port', port])
    sync.assert_not_called()

  def test_no_autoreload_requires_live(self):
    with self.assertRaisesRegex(ValueError, 'only applies'):
      main(['galaxy', '--no-autoreload'])

  def test_live_galaxy_serves_after_releasing_shared_lock(self):
    def serve(runtime, arguments, *, autoreload):
      with (runtime.cache / 'lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # Raises if the build still holds it.
      self.assertEqual(arguments, ['--port', '8099'])
      self.assertTrue(autoreload)
      return 0

    with patch('tools.host_runtime.ROOT', self.root), patch('tools.host_runtime.galaxy_port_free'), \
         patch.object(HostRuntime, 'prepare'), patch.object(HostRuntime, 'build') as build, patch.object(HostRuntime, 'launch') as launch, \
         patch.object(HostRuntime, 'live_galaxy', serve):
      self.assertEqual(main(['galaxy', '4', '--live', '--port', '8099']), 0)
    build.assert_called_once_with('galaxy', 4)
    launch.assert_not_called()


if __name__ == '__main__':
  unittest.main()
