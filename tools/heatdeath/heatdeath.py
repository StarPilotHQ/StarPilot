#!/usr/bin/env python3
"""Reproducible joined Controls replay benchmark; run --help for commands."""

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import sysconfig
import time
import uuid

if __package__:
  from .placement import load_plan, parked
else:
  from placement import load_plan, parked

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCES = (
  'openpilot/selfdrive/car/card.py',
  'openpilot/selfdrive/selfdrived/selfdrived.py',
  'openpilot/selfdrive/controls/controlsd.py',
  'openpilot/selfdrive/controls/plannerd.py',
  'openpilot/selfdrive/controls/radard.py',
  'openpilot/starpilot/flm/steerlearnerd.py',
  'openpilot/common/realtime.py',
  'opendbc_repo/opendbc/car/car.capnp',
  'openpilot/cereal/log.capnp',
  'openpilot/cereal/custom.capnp',
)


def sha(path):
  return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
  with Path(path).open('x') as stream:
    json.dump(value, stream, indent=2)
    stream.write('\n')


def git(repo, *args):
  return subprocess.check_output(
    ['git', '--no-optional-locks', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', '-C', str(repo), *args], text=True, timeout=30
  ).strip()


def clean(repo):
  if git(repo, 'status', '--porcelain'):
    raise RuntimeError('Use a clean, built checkout; benchmark outputs must be outside it')
  return git(repo, 'rev-parse', 'HEAD')


def external(path, repo):
  path, repo = path.resolve(), repo.resolve()
  if path.is_relative_to(repo) or path.exists():
    raise ValueError('Output must be a new path outside the source checkout')
  return path


def prepare(args):
  repo, output = args.repo.resolve(), external(args.output, args.repo.resolve())
  head = clean(repo)
  for part in ('', 'opendbc_repo', 'msgq_repo', 'rednose_repo', 'tinygrad_repo'):
    sys.path.insert(0, str(repo / part))
  from openpilot.tools.lib.logreader import LogReader
  from opendbc.car.structs import car

  cp, cp_time, prior, source = None, -1, [], None
  for path in args.rlog:
    for msg in LogReader(str(path.resolve())):
      if msg.logMonoTime >= args.start_mono_ns:
        continue
      if msg.which() == 'carParams' and int(msg.logMonoTime) > cp_time:
        cp, cp_time = msg.carParams.as_builder().to_bytes(), int(msg.logMonoTime)
      if msg.which() == 'carState' and args.start_mono_ns - 1_000_000_000 <= msg.logMonoTime:
        cs = msg.carState
        prior.append(
          {
            'monoNs': int(msg.logMonoTime),
            'vCruiseKph': float(cs.vCruise),
            'vCruiseClusterKph': float(cs.vCruiseCluster),
            'canValid': bool(cs.canValid),
            'canTimeout': bool(cs.canTimeout),
            'cruiseEnabled': bool(cs.cruiseState.enabled),
            'cruiseAvailable': bool(cs.cruiseState.available),
            'buttonCount': len(cs.buttonEvents),
            'rawEventBase64': base64.b64encode(msg.as_builder().to_bytes()).decode(),
            'source': str(path.resolve()),
          }
        )
  if cp is None:
    raise ValueError('Include the route startup rlog containing its original CarParams')
  with car.CarParams.from_bytes(cp) as parsed:
    if parsed.carFingerprint != 'HYUNDAI_IONIQ_6' or not parsed.openpilotLongitudinalControl:
      raise ValueError('This exported steady-state fixture supports active-long Ioniq 6 recordings only')
  prior.sort(key=lambda row: row['monoNs'])
  last = prior[-2:]
  if (
    len(last) != 2
    or last[0]['source'] != last[1]['source']
    or last[0]['monoNs'] >= last[1]['monoNs']
    or any(not row['canValid'] or row['canTimeout'] or row['buttonCount'] or not 0 < row['vCruiseKph'] < 150 for row in last)
    or any(last[0][key] != last[1][key] for key in ('vCruiseKph', 'vCruiseClusterKph'))
  ):
    raise ValueError('Need two clean stable cruise-speed samples in the second before the window')
  source = Path(last[0]['source'])
  for row in last:
    del row['source']
  output.mkdir(parents=True, mode=0o700)
  packet = output / 'packet'
  packet.mkdir()
  for path in HERE.iterdir():
    if path.suffix in ('.py', '.cc'):
      shutil.copyfile(path, packet / path.name)
  pins = {'sourceHead': head, 'sources': {name: sha(repo / name) for name in SOURCES}, 'helpers': {p.name: sha(p) for p in packet.iterdir()}}
  write(packet / 'joined-source-pins.json', pins)
  (output / 'carparams.bin').write_bytes(cp)
  shutil.copyfile(args.params_json, output / 'params.json')
  write(
    output / 'cruise.json',
    {
      'kind': 'private-recorded-cruise-speed-checkpoint',
      'startupExcluded': True,
      'sourceStartMonoNs': args.start_mono_ns,
      'sourceRlog': str(source),
      'sourceRlogSha256': sha(source),
      'cpSha256': sha(output / 'carparams.bin'),
      'samples': last,
      'sourceHead': head,
      'carSchemaSha256': pins['sources']['opendbc_repo/opendbc/car/car.capnp'],
      'values': {'v_cruise_kph': last[-1]['vCruiseKph'], 'v_cruise_cluster_kph': last[-1]['vCruiseClusterKph'], 'v_cruise_kph_last': last[0]['vCruiseKph']},
    },
  )
  subprocess.run(
    [
      sys.executable,
      '-B',
      str(packet / 'driver_aa_v47_fixture.py'),
      'prepare',
      '--repo',
      str(repo),
      *sum((['--rlog', str(path.resolve())] for path in args.rlog), []),
      '--start-mono-ns',
      str(args.start_mono_ns),
      '--duration',
      str(args.duration),
      '--source-boot-minus-mono-ns',
      str(args.source_boot_minus_mono_ns),
      '--device-motion-clock',
      args.device_motion_clock,
      '--output',
      str(output / 'inputs'),
    ],
    check=True,
  )
  if clean(repo) != head:
    raise RuntimeError('Source changed during preparation')
  write(
    output / 'bundle.json', {'sourceHead': head, 'repo': str(repo), 'files': {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()}}
  )
  print(f'Prepared {output}; next run ./heatdeath build --bundle {output}')


def verify(bundle):
  bundle = bundle.resolve()
  info = json.loads((bundle / 'bundle.json').read_text())
  repo = Path(info['repo']).resolve()
  if clean(repo) != info['sourceHead']:
    raise RuntimeError('Bundle is pinned to another source revision; prepare it again for this checkout')
  for name, expected in info['files'].items():
    path = (bundle / name).resolve()
    if not path.is_relative_to(bundle) or sha(path) != expected:
      raise RuntimeError('Bundle changed: ' + name)
  pins = json.loads((bundle / 'packet/joined-source-pins.json').read_text())
  if any(sha(repo / name) != wanted for name, wanted in pins['sources'].items()):
    raise RuntimeError('Pinned production source changed')
  return bundle, repo, info


def require_device():
  if sys.platform != 'linux' or platform.machine() not in ('aarch64', 'arm64') or os.geteuid() != 0:
    raise RuntimeError('Execution/build require a root Linux ARM64 comma bench environment')
  parked()


@contextmanager
def device_lease():
  path = Path('/data/starbench/heatdeath.lock')
  path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
  with path.open('a') as stream:
    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    parked()
    yield


def build(args):
  require_device()
  bundle, repo, info = verify(args.bundle)
  out = external(bundle / 'native', repo)
  out.mkdir(mode=0o700)
  site = Path(sysconfig.get_path('purelib'))
  base = ['clang++', '-std=c++17', '-O2', '-g', '-fPIC', '-pthread', '-D__COMMA_HARDWARE__=1']
  for name in ('', 'openpilot', 'msgq_repo', 'opendbc_repo', 'openpilot/cereal/gen/cpp'):
    base += ['-I', str(repo / name)]
  for name in ('capnproto', 'json11', 'zeromq'):
    base += ['-I', str(site / name / 'install/include')]
  sources = [bundle / 'packet/native_endpoint.cc', repo / 'openpilot/selfdrive/pandad/panda.cc', repo / 'openpilot/selfdrive/pandad/panda_safety.cc']
  libs = [repo / name for name in ('openpilot/common/libcommon.a', 'openpilot/cereal/libsocketmaster.a', 'msgq_repo/libmsgq.a', 'openpilot/cereal/libcereal.a')]
  libs += [
    site / name / 'install/lib' / lib
    for name, lib in (('capnproto', 'libcapnp.a'), ('capnproto', 'libkj.a'), ('json11', 'libjson11.a'), ('zeromq', 'libzmq.a'))
  ]
  inputs = {str(p): sha(p) for p in [*sources, *libs, repo / 'opendbc_repo/opendbc/safety/tests/libsafety/safety.c']}
  commands = []
  for index, source in enumerate(sources):
    commands.append([*base, '-c', str(source), '-o', str(out / f'endpoint-{index}.o')])
  commands.append(
    [
      'clang++',
      *(str(out / f'endpoint-{index}.o') for index in range(3)),
      '-Wl,--start-group',
      *(str(p) for p in libs),
      '-Wl,--end-group',
      '-ldl',
      '-lrt',
      '-pthread',
      '-Wl,--no-undefined',
      '-o',
      str(out / 'native_endpoint'),
    ]
  )
  commands.append(
    [
      'cc',
      '-fPIC',
      '-Wall',
      '-Wextra',
      '-Werror',
      '-nostdlib',
      '-fno-builtin',
      '-std=gnu11',
      *(['-DALLOW_DEBUG'] if args.safety_build == 'debug' else []),
      '-Wfatal-errors',
      '-Wno-pointer-to-int-cast',
      '-g',
      '-O2',
      '-fno-omit-frame-pointer',
      '-fsanitize=undefined',
      '-fno-sanitize-recover=undefined',
      '-I',
      str(repo / 'opendbc_repo'),
      '-c',
      str(repo / 'opendbc_repo/opendbc/safety/tests/libsafety/safety.c'),
      '-o',
      str(out / 'libsafety.o'),
    ]
  )
  commands.append(['cc', '-shared', str(out / 'libsafety.o'), '-o', str(out / 'libsafety.so'), '-fsanitize=undefined', '-fno-sanitize-recover=undefined'])
  for index, command in enumerate(commands):
    parked()
    with (out / f'build-{index}.log').open('x') as stream:
      subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=90)
  verify(bundle)
  if any(sha(name) != expected for name, expected in inputs.items()):
    raise RuntimeError('Native build inputs changed')
  write(
    out / 'receipt.json',
    {
      'sourceHead': info['sourceHead'],
      'safetyBuild': args.safety_build,
      'commands': commands,
      'inputs': inputs,
      'outputs': {name: sha(out / name) for name in ('native_endpoint', 'libsafety.so')},
    },
  )
  print(f'Built virtual endpoint in {out}; no USB/SPI implementation linked')


def run(args):
  require_device()
  bundle, repo, info = verify(args.bundle)
  plan = load_plan(args.placement)
  available = os.sched_getaffinity(0)
  if any(not set(cpus) <= available for cpus in plan.values()):
    raise ValueError('Requested CPU is unavailable in this environment')
  out = external(args.output, repo)
  if out.is_relative_to(bundle):
    raise ValueError('Keep run outputs outside the immutable bundle')
  receipt = json.loads((bundle / 'native/receipt.json').read_text())
  if receipt['sourceHead'] != info['sourceHead']:
    raise ValueError('Native build belongs to another source revision')
  for name, expected in receipt['outputs'].items():
    if sha(bundle / 'native' / name) != expected:
      raise ValueError('Native artifact changed')
  packet = bundle / 'packet'
  cp = bundle / 'carparams.bin'
  command = [
    sys.executable,
    '-B',
    str(packet / 'driver_aa_v47_fixture.py'),
    'run',
    '--repo',
    str(repo),
    '--inputs',
    str(bundle / 'inputs'),
    '--car-params',
    str(cp),
    '--car-params-sha256',
    sha(cp),
    '--route-car-params',
    str(cp),
    '--route-car-params-sha256',
    sha(cp),
    '--car-schema-sha256',
    sha(repo / 'opendbc_repo/opendbc/car/car.capnp'),
    '--params-json',
    str(bundle / 'params.json'),
    '--native-endpoint',
    str(bundle / 'native/native_endpoint'),
    '--native-endpoint-sha256',
    receipt['outputs']['native_endpoint'],
    '--native-library',
    str(bundle / 'native/libsafety.so'),
    '--native-library-sha256',
    receipt['outputs']['libsafety.so'],
    '--output',
    str(out),
    '--helpers-dir',
    str(packet),
    '--root-grant',
    'execute-private-native-ipc-bench',
    '--steady-state-checkpoint',
    '--cruise-checkpoint',
    str(bundle / 'cruise.json'),
    '--cruise-checkpoint-sha256',
    sha(bundle / 'cruise.json'),
  ]
  if args.native_core2:
    command.append('--private-native-core2')
  forbidden = ('REPLAY', 'SIMULATION', 'CEREAL_FAKE', 'FAKESEND', 'STARTED')
  if any(key in os.environ for key in forbidden) or any(key.startswith(('SP_PRIVATE_', 'SP_PERF_')) for key in os.environ):
    raise ValueError('Run from a clean environment, outside ./dev; private candidate hooks are not accepted')
  env = dict(
    os.environ, PYTHONDONTWRITEBYTECODE='1', HEATDEATH_PLACEMENT=json.dumps(plan), SP_PRIVATE_LOOP_MACRO='1', SP_PRIVATE_IRQ_LEASE_TOKEN=uuid.uuid4().hex
  )
  reserve = None
  if args.reserve_core is not None:
    if args.reserve_core not in available or not 0 < args.reserve_ms < args.reserve_period_ms <= 1000:
      raise ValueError('Reserve needs an available core and 0 < dose < period <= 1000 ms')
    reserve = {'core': args.reserve_core, 'cpuMs': args.reserve_ms, 'periodMs': args.reserve_period_ms}
  env['HEATDEATH_RESERVE'] = json.dumps(reserve)
  context_before = context()
  old_handlers = {}
  child = None

  def stop(_signum, _frame):
    raise KeyboardInterrupt

  try:
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
      old_handlers[number] = signal.signal(number, stop)
    child = subprocess.Popen(command, env=env, start_new_session=True)
    deadline = time.monotonic() + 180
    while child.poll() is None:
      parked()
      if time.monotonic() >= deadline:
        raise TimeoutError('Benchmark exceeded its 180-second wall bound')
      time.sleep(0.2)
    if child.returncode:
      raise RuntimeError(f'Benchmark failed ({child.returncode}); inspect {out}')
  finally:
    for number in old_handlers:
      signal.signal(number, signal.SIG_IGN)
    try:
      try:
        if child is not None and child.poll() is None:
          child.terminate()
          try:
            child.wait(timeout=15)
          except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
      finally:
        reap_token(env['SP_PRIVATE_IRQ_LEASE_TOKEN'])
        journal = out / 'shm-ownership.json'
        if journal.exists():
          cleanup = importlib.util.spec_from_file_location('heatdeath_cleanup', packet / 'owned_msgq_cleanup_v2.py')
          module = importlib.util.module_from_spec(cleanup)
          cleanup.loader.exec_module(module)
          previous = os.environ.get('SP_PRIVATE_IRQ_LEASE_TOKEN')
          os.environ['SP_PRIVATE_IRQ_LEASE_TOKEN'] = env['SP_PRIVATE_IRQ_LEASE_TOKEN']
          try:
            module.cleanup(journal, out / 'cleanup.json')
          finally:
            if previous is None:
              os.environ.pop('SP_PRIVATE_IRQ_LEASE_TOKEN', None)
            else:
              os.environ['SP_PRIVATE_IRQ_LEASE_TOKEN'] = previous
        verify(bundle)
        if out.is_dir():
          write(out / 'context.json', {'before': context_before, 'after': context()})
    finally:
      for number, handler in old_handlers.items():
        signal.signal(number, handler)
  print(json.dumps(json.loads((out / 'result.json').read_text()), indent=2))


def context():
  result = {
    'monotonicNs': time.monotonic_ns(),
    'machine': platform.machine(),
    'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    'files': {},
  }
  paths = [Path('/sys/devices/system/cpu/online')]
  for pattern in ('/sys/devices/system/cpu/cpufreq/policy*/scaling_*', '/sys/class/thermal/thermal_zone*/temp'):
    import glob

    paths.extend(Path(path) for path in glob.glob(pattern))
  for path in paths:
    try:
      result['files'][str(path)] = path.read_text().strip()
    except OSError as error:
      result['files'][str(path)] = type(error).__name__
  return result


def token_processes(token):
  key = ('SP_PRIVATE_IRQ_LEASE_TOKEN=' + token).encode()
  owned = []
  for path in Path('/proc').iterdir():
    if not path.name.isdecimal() or int(path.name) == os.getpid():
      continue
    try:
      if key in (path / 'environ').read_bytes().split(b'\0'):
        owned.append(int(path.name))
    except (FileNotFoundError, ProcessLookupError):
      pass
  return owned


def reap_token(token):
  for number, seconds in ((signal.SIGTERM, 3), (signal.SIGKILL, 3)):
    for pid in token_processes(token):
      try:
        descriptor = os.pidfd_open(pid)
      except ProcessLookupError:
        continue
      try:
        # Bind the signal to this process identity, even if its PID is reused.
        if pid in token_processes(token):
          signal.pidfd_send_signal(descriptor, number)
      except ProcessLookupError:
        pass
      finally:
        os.close(descriptor)
    deadline = time.monotonic() + seconds
    while token_processes(token) and time.monotonic() < deadline:
      time.sleep(0.05)
  if token_processes(token):
    raise RuntimeError('Owned benchmark actors remain; namespace retained for inspection')


def report(args):
  if __package__:
    from .report import summarize
  else:
    from report import summarize

  result = summarize(args.output, args.skip_seconds)
  print(json.dumps(result, indent=2))


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest='command', required=True)
  prep = commands.add_parser('prepare', help='Extract a pinned recorded window and helper bundle')
  prep.add_argument('--repo', type=Path, default=REPO)
  prep.add_argument('--rlog', type=Path, action='append', required=True)
  prep.add_argument('--start-mono-ns', type=int, required=True)
  prep.add_argument('--duration', type=float, default=60)
  prep.add_argument('--source-boot-minus-mono-ns', type=int, required=True)
  prep.add_argument('--device-motion-clock', choices=('monotonic', 'boottime'), required=True)
  prep.add_argument('--params-json', type=Path, required=True, help='Explicit base64-valued private benchmark preference snapshot')
  prep.add_argument('--output', type=Path, required=True)
  compile_parser = commands.add_parser('build', help='Build the virtual native endpoint using existing compiled dependencies')
  compile_parser.add_argument('--bundle', type=Path, required=True)
  compile_parser.add_argument('--safety-build', choices=('debug', 'release'), default='debug',
                              help='Match the Panda firmware build; normal development builds use debug')
  execute = commands.add_parser('run', help='Run an offroad joined replay; no physical CAN I/O')
  execute.add_argument('--bundle', type=Path, required=True)
  execute.add_argument('--output', type=Path, required=True)
  execute.add_argument('--placement', default='{}', help='JSON actor-to-CPU-list map, e.g. controlsd:[6], card:[4]')
  execute.add_argument('--native-core2', action='store_true', help='Move only the virtual endpoint to core 2 after native startup')
  execute.add_argument('--reserve-core', type=int, help='Add an explicitly synthetic CFS reserve on this CPU')
  execute.add_argument('--reserve-ms', type=float, default=8)
  execute.add_argument('--reserve-period-ms', type=float, default=50)
  results = commands.add_parser('report', help='Summarize captured timing, native evidence and limitations')
  results.add_argument('--output', type=Path, required=True)
  results.add_argument('--skip-seconds', type=float, default=10)
  if len(sys.argv) == 1:
    parser.print_help()
    return
  args = parser.parse_args()
  if args.command == 'prepare' and not 1 <= args.duration <= 120:
    parser.error('Duration must be between 1 and 120 seconds')
  try:
    if args.command in ('build', 'run'):
      require_device()
      with device_lease():
        {'build': build, 'run': run}[args.command](args)
    else:
      {'prepare': prepare, 'report': report}[args.command](args)
  except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
    parser.exit(1, f'heatdeath: {error}\n')


if __name__ == '__main__':
  sys.dont_write_bytecode = True
  main()
