#!/usr/bin/env python3
"""StarPilot desktop commands with an independent build tree, index and Params."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
HELP = """Usage: ./dev <command> [args...]
       ./tool <command> [args...]       ./tools/host <command> [args...]

  c3 / c4       Build and launch the large / compact StarPilot Raylib UI.
  onroad        Build replay and launch route UI(s); see ./onroad --help.
  replay        Build and run upstream replay.
  cabana        Build and run Cabana in its own concurrent cache.
  jotpluggler   Build and run JotPluggler (alias: juggle; also ./juggle).
  plotjuggler   Run legacy PlotJuggler.
  galaxy        Run authenticated Galaxy on loopback (optional --port).
                --live releases the shared lock and follows edits under
                openpilot/starpilot/galaxy, restarting for Python changes and
                reloading the open page after each edit (--no-autoreload: refresh
                by hand).
  python        Run Python with current source and native extensions.
  pytest        Run pytest with current source and native extensions.
  shell         Open a shell in the isolated host worktree.
  sync [bucket] Refresh all caches, or shared/cabana, without launching tools.
  help          Show this help without installing or building anything.

Build/launch commands accept an optional leading jobs count, e.g. ./c3 8.
The host cache is .host_runtime/<system>-<architecture>/<bucket>/.
Native build artifacts, editable installs and Params stay in that cache.
Shared commands wait while another shared command is running; Cabana is separate.
./build remains the Linux ARM64 device build, including ./build --panda.
"""
COMMANDS = {'c3', 'c4', 'onroad', 'replay', 'cabana', 'jotpluggler', 'plotjuggler', 'galaxy', 'python', 'pytest', 'shell', 'sync'}
BUCKET_ALIASES = dict.fromkeys(('shared', 'default', 'ui', 'c3', 'c4', 'onroad', 'replay', 'shell', 'jotpluggler', 'plotjuggler', 'juggle'), 'shared')
BUCKET_ALIASES['cabana'] = 'cabana'
VENDORS = ('msgq_repo', 'opendbc_repo', 'rednose_repo', 'teleoprtc_repo', 'tinygrad_repo', 'jetlink_repo')
# Never inherit a device/cross compiler or another project's editable imports.
REMOVE_ENV = ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS',
              'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH', 'PKG_CONFIG_PATH', 'CPATH', 'LIBRARY_PATH',
              'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR', 'SP_HOST_PARKED')
EXCLUDE_DIRS = {'.git', '.venv', '.venv-linux-arm64', '.host_runtime', '.comma_sysroot', '.cache', '__pycache__'}
NATIVE_SUFFIXES = {'.a', '.so', '.dylib', '.o', '.os'}
GALAXY = Path('openpilot/starpilot/galaxy')
LIVE_RELOAD = 'starpilot-live-reload.js'
LIVE_VERSION = 'starpilot-live-version.txt'
LIVE_TAG = f'<script src="/{LIVE_RELOAD}"></script>'
LIVE_SCRIPT = f"""// Added by ./dev galaxy --live to its host cache only: reload after each mirrored edit.
{{
  let seen = null
  setInterval(async () => {{
    try {{
      const response = await fetch('/{LIVE_VERSION}', {{ cache: 'no-store' }})
      if (!response.ok) return
      const version = (await response.text()).trim()
      if (seen !== null && version !== seen) {{
        const event = new Event('beforeunload', {{ cancelable: true }})
        if (!window.dispatchEvent(event)) return
        location.reload()
      }}
      seen = version
    }} catch {{}}
  }}, 500)
}}
"""


def run(arguments, *, cwd, env=None, capture=False):
  return subprocess.run([str(arg) for arg in arguments], cwd=cwd, env=env, check=True,
                        stdout=subprocess.PIPE if capture else None, text=capture)


def git(root, *arguments):
  env = {key: value for key, value in os.environ.items() if key not in REMOVE_ENV}
  return run(['git', *arguments], cwd=root, env=env, capture=True).stdout


def parse(arguments):
  command = arguments[0] if arguments else 'help'
  if command in ('help', '-h', '--help'):
    return 'help', 0, []
  if command == 'juggle':
    command = 'jotpluggler'
  if command not in COMMANDS:
    raise ValueError(f'Unknown developer command: {command}. Run ./dev help.')
  args = list(arguments[1:])
  jobs = max(1, os.cpu_count() or 8)
  if command not in ('python', 'pytest', 'shell', 'sync') and args and args[0].isdigit():
    jobs = int(args.pop(0))
    if jobs < 1:
      raise ValueError('The jobs count must be positive.')
  if command == 'sync' and (len(args) > 1 or (args and args[0] not in BUCKET_ALIASES)):
    raise ValueError('Usage: ./dev sync [shared|cabana]')
  return command, jobs, args


def inside(path, root):
  """Reject cache symlinks escaping the disposable tree before any mutation."""
  if not path.resolve().is_relative_to(root.resolve()):
    raise RuntimeError(f'Cache path escapes its root: {path}')
  return path


def same_contents(source, destination):
  if not destination.is_file() or source.stat().st_size != destination.stat().st_size:
    return False
  with source.open('rb') as left, destination.open('rb') as right:
    while block := left.read(1024 * 1024):
      if block != right.read(len(block)):
        return False
  return True


@contextmanager
def file_lock(path, *, nonblocking=False):
  with path.open('a+') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
    yield


@contextmanager
def atomic_destination(path):
  """Publish a complete file or symlink without truncating readers of the old inode."""
  fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}-')
  os.close(fd)
  temporary = Path(temporary)
  try:
    yield temporary
    os.replace(temporary, path)
  finally:
    temporary.unlink(missing_ok=True)


def atomic_write(path, data):
  with atomic_destination(path) as temporary:
    temporary.write_bytes(data)


class HostRuntime:
  def __init__(self, root, bucket, *, system=None, machine=None):
    self.root = Path(root).resolve()
    self.system = system or platform.system()
    machine = machine or platform.machine()
    if self.system not in ('Darwin', 'Linux'):
      raise RuntimeError('Desktop tools support macOS and Linux.')
    self.cache = self.root / '.host_runtime' / f'{self.system.lower()}-{machine}' / bucket
    if (self.root / '.host_runtime').is_symlink():
      raise RuntimeError('.host_runtime must be a local directory, not a symlink.')
    inside(self.cache, self.root / '.host_runtime')
    self.work = self.cache / 'worktree'
    self.venv = self.cache / 'venv'
    self.prefix = 'starpilot-dev-' + hashlib.sha256(str(self.cache).encode()).hexdigest()[:12]
    self.lock_fd = None

  @contextmanager
  def locked(self):
    self.cache.mkdir(parents=True, exist_ok=True)
    lock_path = inside(self.cache / 'lock', self.cache)
    with lock_path.open('a+') as lock:
      try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
      except BlockingIOError:
        lock.seek(0)
        owner = lock.read().strip()
        detail = f' (owner PID {owner})' if owner.isdecimal() else ''
        print(f'Waiting for the {self.cache.name} host session{detail} to exit…',
              'Stop that session with Ctrl+C to continue.', flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
      lock.seek(0)
      lock.truncate()
      lock.write(str(os.getpid()))
      lock.flush()
      self.lock_fd = lock.fileno()
      try:
        yield
      finally:
        self.lock_fd = None
        fcntl.flock(lock, fcntl.LOCK_UN)

  def source_paths(self, *pathspec):
    # Include uncommitted development, exclude ignored native/device outputs.
    listed = git(self.root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', *pathspec).split('\0')
    return {Path(name) for name in listed if name and name != 'openpilot/tools/replay/replay' and not (set(Path(name).parts) & EXCLUDE_DIRS)
            and not (set(Path(name).suffixes) & NATIVE_SUFFIXES) and (self.root / name).is_file()}

  def source_lock(self):
    return file_lock(inside(self.cache / 'source-lock', self.cache))

  def copy_source(self, name):
    source = self.root / name
    destination = inside(self.work / name, self.work)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_dir() and not destination.is_symlink():
      shutil.rmtree(destination)
    if source.is_symlink():
      inside(source, self.root)
      target = os.readlink(source)
      if os.path.isabs(target):
        raise RuntimeError(f'Absolute source symlink is not portable: {name}')
      if destination.is_symlink() and os.readlink(destination) == target:
        return
      with atomic_destination(destination) as temporary:
        temporary.unlink()
        temporary.symlink_to(target)
    elif destination.is_symlink() or not same_contents(source, destination):
      old_mtime = destination.stat().st_mtime_ns if destination.exists() else None
      with atomic_destination(destination) as temporary:
        shutil.copy2(source, temporary)
        if temporary.stat().st_mtime_ns == old_mtime:
          os.utime(temporary, None)  # SCons must notice edits even when an editor keeps mtime.
    elif source.stat().st_mode != destination.stat().st_mode:
      destination.chmod(source.stat().st_mode)
    if name.suffix == '.py':
      # Timestamp-based bytecode can accept same-size edits within the same second.
      for bytecode in (destination.parent / '__pycache__').glob(f'{destination.stem}.*.pyc'):
        inside(bytecode, self.work).unlink(missing_ok=True)

  def mirror(self, changed, removed):
    """Serialize cache mutations and retain ownership of live-added files for later syncs."""
    with self.source_lock():
      return self._mirror(changed, removed)

  def _mirror(self, changed, removed):
    manifest = inside(self.cache / 'source-files.json', self.cache)
    paths = set(json.loads(manifest.read_text())) if manifest.exists() else set()
    copied = []
    for name in removed:
      path = inside(self.work / name, self.work)
      path.unlink(missing_ok=True)
      paths.discard(str(name))
    for name in changed:
      try:
        self.copy_source(name)
      except FileNotFoundError:
        continue  # Do not acknowledge this edit; the watcher will retry it.
      copied.append(name)
      paths.add(str(name))
    atomic_write(manifest, (json.dumps(sorted(paths)) + '\n').encode())
    return copied

  def sync(self):
    inside(self.work, self.cache).mkdir(parents=True, exist_ok=True)
    paths = self.source_paths()
    with self.source_lock():
      manifest = inside(self.cache / 'source-files.json', self.cache)
      previous = set(json.loads(manifest.read_text())) if manifest.exists() else set()
      self._mirror(sorted(paths), [Path(name) for name in sorted(previous - set(map(str, paths)))])
      self.sync_git()

  def sync_git(self):
    # Same read-only object sharing as `git clone --shared`, independent index/refs.
    metadata = self.work / '.git'
    if metadata.is_symlink() or (metadata.exists() and not metadata.is_dir()):
      raise RuntimeError('Host worktree has shared Git metadata; remove this host cache and retry.')
    if not metadata.exists():
      git(self.work, 'init', '-q')
    common = Path(git(self.root, 'rev-parse', '--git-common-dir').strip())
    if not common.is_absolute():
      common = self.root / common
    (metadata / 'objects/info/alternates').write_text(str(common.resolve() / 'objects') + '\n')
    head = git(self.root, 'rev-parse', 'HEAD').strip()
    branch = git(self.root, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    branch = 'host-detached' if branch == 'HEAD' else branch
    git(self.work, 'symbolic-ref', 'HEAD', f'refs/heads/{branch}')
    git(self.work, 'update-ref', 'HEAD', head)
    git(self.work, 'read-tree', head)
    config = git(self.root, 'config', '--list')
    origin = next((line.split('=', 1)[1] for line in config.splitlines() if line.startswith('remote.origin.url=')), '')
    if origin:
      git(self.work, 'config', 'remote.origin.url', origin)
    # A host helper must never accidentally push through this diagnostic clone.
    git(self.work, 'config', 'remote.origin.pushurl', 'disabled://host-runtime')
    git(self.work, 'config', 'core.hooksPath', '/dev/null')

  def environment(self):
    env = {key: value for key, value in os.environ.items() if key not in REMOVE_ENV}
    paths = [self.work, *(self.work / name for name in VENDORS)]
    env.update(PYTHONPATH=os.pathsep.join(map(str, paths)), PYTHONNOUSERSITE='1',
               PATH=f'{self.venv}/bin' + os.pathsep + env.get('PATH', ''), VIRTUAL_ENV=str(self.venv),
               BASEDIR=str(self.work), PWD=str(self.work), PARAMS_ROOT=str(inside(self.cache / 'params', self.cache)),
               OPENPILOT_PREFIX=self.prefix, SP_HOST_PREFIX=self.prefix,
               SP_HOST_PARAMS_ROOT=str(self.cache / 'params'), SP_HOST_RUNTIME='1',
               STARPORT_DIR=str(inside(self.cache / 'starport', self.cache)),
               SP_SCONS_CACHE_DIR=str(self.cache / 'scons-cache'),
               SCONS_CACHE=str(self.cache / 'scons-cache'),
               NOBOARD='1', SIMULATION='1', SKIP_FW_QUERY='1', STARPILOT_UI_DEV='1',
               COMMA_CACHE=str(self.cache.parent / 'downloads'))
    if self.system == 'Darwin':
      env.update(CC='/usr/bin/clang', CXX='/usr/bin/clang++', ZMQ='1')
    return env

  def prepare(self):
    uv = shutil.which('uv')
    if uv is None:
      candidate = Path.home() / '.local/bin/uv'
      if candidate.is_file() and os.access(candidate, os.X_OK):
        uv = str(candidate)
    if uv is None:
      raise RuntimeError('uv is required. Run tools/setup_dependencies.sh, then retry ./dev.')
    inside(self.venv, self.cache)
    env = self.environment()
    env.update(UV_PROJECT_ENVIRONMENT=str(self.venv), UV_PYTHON='3.12')
    run([uv, 'sync', '--frozen', '--extra', 'tools', '--extra', 'testing', '--extra', 'dev', '--extra', 'safety'], cwd=self.work, env=env)
    link = self.work / '.venv'
    if link.is_symlink():
      if link.resolve() != self.venv.resolve():
        raise RuntimeError('Host worktree .venv points outside its environment.')
    elif link.exists():
      raise RuntimeError('Host worktree .venv is not the managed environment; remove this host cache and retry.')
    else:
      link.symlink_to(self.venv, target_is_directory=True)

  def build(self, command, jobs):
    params = 'openpilot/common/libparams_c' + ('.dylib' if self.system == 'Darwin' else '.so')
    targets = [params, 'msgq_repo/msgq/ipc_pyx.so', 'msgq_repo/msgq/visionipc/visionipc_pyx.so']
    if command in ('c3', 'c4', 'onroad', 'galaxy', 'python', 'pytest', 'shell'):
      targets += ['rednose_repo/rednose/helpers/ekf_sym_pyx.so', 'openpilot/selfdrive/locationd',
                  'openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so']
    if command in ('onroad', 'replay', 'cabana'):
      targets += ['openpilot/tools/' + ('cabana/cabana' if command == 'cabana' else 'replay/replay')]
    if command == 'jotpluggler':
      targets += ['openpilot/tools/jotpluggler/jotpluggler']
    run([self.venv / 'bin/python', '-m', 'SCons', f'-j{jobs}', *targets], cwd=self.work, env=self.environment())

  def private_ipc(self):
    ipc = Path('/tmp' if self.system == 'Darwin' else '/dev/shm') / f'msgq_{self.prefix}'
    if ipc.is_symlink() or (ipc.exists() and (not ipc.is_dir() or ipc.stat().st_uid != os.getuid())):
      raise RuntimeError(f'Private messaging path is not owned by this user: {ipc}')
    ipc.mkdir(mode=0o700, exist_ok=True)

  def launch(self, command, jobs, arguments):
    env = self.environment()
    if command in ('c3', 'c4', 'galaxy'):
      env['SP_HOST_PARKED'] = '1'  # No car or drive-state publishers: parked-only setup stays usable.
    python = self.venv / 'bin/python'
    if command in ('c3', 'c4', 'onroad'):
      script = 'launch_onroad_desktop.sh' if command == 'onroad' else f'launch_ui_{command}_desktop.sh'
      argv = [self.work / 'scripts' / script, str(jobs), *arguments]
    elif command in ('replay', 'cabana', 'jotpluggler'):
      argv = [self.work / 'openpilot/tools' / command / command, *arguments]
    elif command == 'plotjuggler':
      argv = [python, '-m', 'openpilot.tools.plotjuggler.juggle', *arguments]
    elif command == 'galaxy':
      argv = [python, '-m', 'openpilot.starpilot.galaxy.server', *arguments]
    elif command == 'pytest':
      argv = [python, '-m', 'pytest', *arguments]
      env.setdefault('SCALE', '1')  # UI imports must not probe a monitor in test workers.
      if self.system == 'Darwin':
        env['PYTEST_ADDOPTS'] = env.get('PYTEST_ADDOPTS', '') + ' -n0'
    elif command == 'shell':
      argv = [env.get('SHELL', '/bin/bash'), *arguments]
    else:
      argv = [python, *arguments]
    self.private_ipc()
    # Keep the bucket locked if this wrapper is killed while its child is alive.
    inherited = () if self.lock_fd is None else (self.lock_fd,)
    with subprocess.Popen([str(arg) for arg in argv], cwd=self.work, env=env, pass_fds=inherited) as child:
      def forward(signum, _frame):
        if child.poll() is None:
          child.send_signal(signum)
      previous = {sig: signal.signal(sig, forward) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
      try:
        result = child.wait()
        return result if result >= 0 else 128 - result
      finally:
        for sig, handler in previous.items():
          signal.signal(sig, handler)

  def galaxy_sources(self):
    """Content identities using the same source selection as sync(), including relative symlinks."""
    found = {}
    for name in self.source_paths(str(GALAXY)):
      path = self.root / name
      try:
        identity = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        found[name] = (path.is_symlink(), path.stat().st_mode, hashlib.sha256(identity).digest())
      except FileNotFoundError:
        continue
    return found

  def live_reload(self, version=None):
    """Keep the cached page loading the reload script; publish a new edit version if given."""
    with self.source_lock():
      self._live_reload(version)

  def _live_reload(self, version=None):
    web = self.work / GALAXY / 'web'
    if version is None and not (web / LIVE_VERSION).is_file():
      version = time.time_ns()
    if version is not None:
      atomic_write(inside(web / LIVE_VERSION, self.work), f'{version}\n'.encode())
    script = inside(web / LIVE_RELOAD, self.work)
    if not script.is_file() or script.read_text() != LIVE_SCRIPT:
      atomic_write(script, LIVE_SCRIPT.encode())
    page = inside(web / 'index.html', self.work)
    try:
      html = page.read_text()
    except FileNotFoundError:
      return
    # Another command's sync restores the checkout's page.
    if LIVE_TAG not in html and '</body>' in html:
      atomic_write(page, html.replace('</body>', f'  {LIVE_TAG}\n</body>', 1).encode())

  def end_live_reload(self):
    with self.source_lock():
      web = self.work / GALAXY / 'web'
      for name in (LIVE_RELOAD, LIVE_VERSION):
        inside(web / name, self.work).unlink(missing_ok=True)
      if (self.root / GALAXY / 'web/index.html').is_file():
        self.copy_source(GALAXY / 'web/index.html')

  def live_galaxy(self, arguments, *, autoreload=True, interval=0.5):
    """One live owner per cache; desktop UI and replay may still run alongside."""
    try:
      with file_lock(inside(self.cache / 'galaxy-live-lock', self.cache), nonblocking=True):
        return self._live_galaxy(arguments, autoreload=autoreload, interval=interval)
    except BlockingIOError:
      raise RuntimeError('A Galaxy live session already owns this host cache; stop it before starting another.') from None

  def _live_galaxy(self, arguments, *, autoreload, interval):
    argv = [str(self.venv / 'bin/python'), '-B', '-m', 'openpilot.starpilot.galaxy.server', *arguments]
    env = self.environment() | {'SP_HOST_PARKED': '1'}
    self.private_ipc()
    # Without autoreload the cached page is left exactly as synced.
    publish = self.live_reload if autoreload else lambda version=None: None
    if autoreload:
      print(f'Galaxy live: edits under {GALAXY} reload the open page; Python edits restart the server first.', flush=True)
      print('Refresh an already-open page once.', flush=True)
    else:
      print(f'Galaxy live: refresh to see edits under {GALAXY}; Python edits restart the server.', flush=True)
    print('Changes outside Galaxy need ./dev sync and a restart. Stop with Ctrl+C.', flush=True)
    seen = self.galaxy_sources()
    # Reconcile additions, edits and deletions made during preparation/building.
    with self.source_lock():
      manifest = self.cache / 'source-files.json'
      previous_sources = set(map(Path, json.loads(manifest.read_text())))
      removed = [name for name in previous_sources - seen.keys() if name.is_relative_to(GALAXY)]
      copied = self._mirror(list(seen), removed)
    seen = {name: seen[name] for name in copied}
    child = None
    interrupted = False
    termination = None

    def terminate(signum, _frame):
      nonlocal termination
      termination = signum

    previous = {sig: signal.signal(sig, terminate) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
      publish(time.time_ns())
      child = subprocess.Popen(argv, cwd=self.work, env=env)
      while True:
        if termination is not None:
          return 128 + termination
        if child.poll() is not None:
          result = child.returncode
          return result if result >= 0 else 128 - result
        time.sleep(interval)
        publish()
        current = self.galaxy_sources()
        changed = [name for name, identity in current.items() if seen.get(name) != identity]
        removed = [name for name in seen if name not in current]
        if not changed and not removed:
          continue
        changed = self.mirror(changed, removed)
        for name in removed:
          seen.pop(name, None)
        seen.update({name: current[name] for name in changed})
        backend = [name for name in changed + removed
                   if not name.is_relative_to(GALAXY / 'web')
                   and not name.is_relative_to(GALAXY / 'tests')
                   and not name.name.startswith('test_')]
        if backend:
          print(f'Galaxy live: restarting for {backend[0].relative_to(GALAXY)}', flush=True)
          stop(child)
          # Publish only once the old server is gone, so the page reloads onto the new one.
          publish(time.time_ns())
          child = subprocess.Popen(argv, cwd=self.work, env=env)
        elif any(name.is_relative_to(GALAXY / 'web') for name in changed + removed):
          publish(time.time_ns())
    except KeyboardInterrupt:
      interrupted = True
      return 130
    finally:
      try:
        if child is not None:
          stop(child, interrupted=interrupted)
        if autoreload:
          self.end_live_reload()
      finally:
        for sig, handler in previous.items():
          signal.signal(sig, handler)


def galaxy_port_free(arguments):
  """Fail before a long sync when another Galaxy, often a second --live, owns the port."""
  parser = argparse.ArgumentParser(prog='./dev galaxy --live', add_help=False)
  parser.add_argument('--port', type=int, default=8082)  # The server's own default.
  parser.add_argument('--host', default='127.0.0.1')
  known, _ = parser.parse_known_args(arguments)
  with socket.socket() as probe:
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Matches the server's bind.
    try:
      probe.bind((known.host, known.port))
    except OSError:
      raise RuntimeError(f'Port {known.port} is already in use; stop the other Galaxy or pass --port.') from None


def stop(child, *, interrupted=False, timeout=5):
  # SIGINT is the server's clean shutdown path; it reaps its own camera and media helpers.
  # A terminal Ctrl+C already delivered it, and a second one would cut that cleanup short.
  first = (lambda: None) if interrupted else (lambda: child.send_signal(signal.SIGINT))
  for send in (first, child.terminate, child.kill):
    if child.poll() is not None:
      return
    send()
    try:
      child.wait(timeout)
    except subprocess.TimeoutExpired:
      pass


def main(arguments=None):
  command, jobs, args = parse(sys.argv[1:] if arguments is None else arguments)
  live = command == 'galaxy' and '--live' in args
  autoreload = '--no-autoreload' not in args
  if command == 'galaxy' and not autoreload and not live:
    raise ValueError('--no-autoreload only applies to ./dev galaxy --live.')
  if live:
    args = [arg for arg in args if arg not in ('--live', '--no-autoreload')]
    galaxy_port_free(args)
  if command == 'help':
    print(HELP)
    return 0
  if command in ('c3', 'c4', 'onroad') and args in (['--help'], ['-h']):
    helper = ROOT / ('openpilot/tools/replay/onroad.py' if command == 'onroad' else 'openpilot/starpilot/ui/host_launch.py')
    profile = [] if command == 'onroad' else ['large' if command == 'c3' else 'compact']
    return subprocess.call([sys.executable, str(helper), *profile, '--help'], cwd=ROOT)
  if command == 'sync':
    buckets = [BUCKET_ALIASES[args[0]]] if args else ['shared', 'cabana']
  else:
    buckets = ['cabana' if command == 'cabana' else 'shared']
  for bucket in buckets:
    runtime = HostRuntime(ROOT, bucket)
    with runtime.locked():
      runtime.sync()
      runtime.prepare()
      if command != 'sync':
        runtime.build(command, jobs)
        if not live:
          return runtime.launch(command, jobs, args)
    if live:
      # Hold the lock only while building, so ./c3, ./c4 and ./onroad can run alongside.
      return runtime.live_galaxy(args, autoreload=autoreload)
  return 0


if __name__ == '__main__':
  try:
    raise SystemExit(main())
  except (ValueError, RuntimeError, OSError) as error:
    print(f'Host tools: {error}', file=sys.stderr)
    raise SystemExit(1) from None
  except subprocess.CalledProcessError as error:
    raise SystemExit(error.returncode if error.returncode > 0 else 128 - error.returncode) from None
  except KeyboardInterrupt:
    raise SystemExit(130) from None
