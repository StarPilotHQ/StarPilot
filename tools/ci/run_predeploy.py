#!/usr/bin/env python3
"""Run the source, host, vehicle and recorded checks used by GitHub Actions."""

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
STAGES = ('build', 'source', 'host', 'native', 'vehicles', 'recorded', 'replay')


def commands(output, cache, download=False):
  python = sys.executable
  source = [('mapd-package', [python, 'tools/release/stage_mapd_provider.py', '--source', str(ROOT), '--require-tracked']),
            ('lint', ['bash', 'scripts/lint/lint.sh']),
            ('dependencies', [python, 'tools/vendor/check.py', '--revision', 'HEAD']),
            ('schemas', [python, 'tools/ci/schema_policy.py'])]
  for name, path in (('vendor', 'tools/vendor/tests'), ('packaging', 'tools/release/tests'),
                     ('ci-tools', 'tools/ci/tests'), ('updater', 'openpilot/system/updated/tests')):
    command = [python, '-m', 'pytest', '-q', path] if name == 'packaging' else [python, '-m', 'unittest', 'discover', '-s', path, '-v']
    source.append((name, command))
  host = [('unittest', [python, 'tools/ci/run_host_tests.py', '--json-output', str(output / 'host.json')]),
          ('pytest', [python, 'tools/ci/run_host_tests.py', '--pytest'])]
  vehicles = [(suite, [python, 'tools/ci/run_vehicle_tests.py', '--suite', suite, '--output', str(output / suite)])
              for suite in ('interfaces', 'safety-debug', 'safety-release')]
  vehicles.append(('fleet-coverage', [python, 'tools/ci/fleet_coverage.py', '--require', 'interfaces',
                   '--interface-coverage', str(output / 'interfaces/coverage.json'),
                   '--interface-results', str(output / 'interfaces/results.json'),
                   '--output', str(output / 'interfaces/fleet-coverage.json')]))
  recorded = [(f'recorded-{variant}', [python, 'tools/ci/run_recorded_vehicle_tests.py', '--variant', variant,
               '--cache', str(cache), '--output', str(output / f'recorded-{variant}'), *(['--download'] if download else [])])
              for variant in ('debug', 'release')]
  native = [(name, [str(ROOT / 'openpilot/selfdrive/pandad/tests' / name)])
            for name in ('test_panda_usb', 'test_pandad_canprotocol', 'test_aol_protocol', 'test_aol_wire', 'test_panda_faults')]
  if platform.system() == 'Linux':
    native.extend((name, [str(ROOT / 'openpilot/selfdrive/pandad/tests' / name)])
                  for name in ('test_producer_timing', 'test_producer_sink'))
  native.append(('map-ipc', ['go', '-C', str(ROOT / 'mapd_repo'), 'test', '-mod=readonly', '-race',
                            '-run', '^Test(MsgqNativeLayout|Shadow(GoPythonIPC|ExecutableHostGpsIPC))$', '-count=1', '-v']))
  return {'build': [('native-build', [python, '-m', 'SCons', '-j4'])],
          'source': source, 'host': host, 'native': native, 'vehicles': vehicles, 'recorded': recorded,
          'replay': [('process-replay', [python, 'openpilot/selfdrive/test/process_replay/test_processes.py',
                                        '-j', str(os.cpu_count() or 1), '--output', str(output / 'process-replay')])]}


def run(plan, output, *, environment=None):
  output.mkdir(parents=True, exist_ok=False)
  records = []
  for name, command in plan:
    print(f'{name}: starting', flush=True)
    started = time.monotonic()
    with (output / f'{name}.log').open('w') as log:
      try:
        result = subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, check=False)
        code = result.returncode
      except OSError as error:
        log.write(str(error) + '\n')
        code = 127
    records.append({'check': name, 'command': command, 'returncode': code, 'seconds': round(time.monotonic() - started, 3)})
    (output / 'results.json').write_text(json.dumps(records, indent=2) + '\n')
    print(f'{name}: {"passed" if code == 0 else "FAILED"} ({output / (name + ".log")})', flush=True)
    if code:
      return code if code > 0 else 128 - code
  return 0


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, required=True, help='New directory for logs and per-check results')
  parser.add_argument('--cache', type=Path, default=Path.home() / '.cache/starpilot/recorded-fixtures')
  parser.add_argument('--stage', choices=STAGES, action='append', help='Run selected stages; default runs all stages')
  parser.add_argument('--download', action='store_true', help='Allow the pinned public recording downloads')
  args = parser.parse_args()
  output, cache = args.output.expanduser().resolve(), args.cache.expanduser().resolve()
  if output.exists():
    parser.error('--output must be new so earlier evidence is preserved')
  if output.is_relative_to(ROOT):
    parser.error('--output must be outside the source tree')
  selected = set(args.stage or STAGES)
  if "native" in selected and platform.system() != "Linux":
    parser.error("the native predeploy stage requires Linux; on macOS build host dependencies, then select --stage source --stage host")
  plan = commands(output, cache, args.download)
  environment = dict(os.environ, SCALE='1', RAYLIB_BACKEND='headless', FUZZ_SEED='0', GOTOOLCHAIN='local',
                     STARPILOT_SHADOW_IPC_REQUIRED='1', STARPILOT_SHADOW_EXEC_REQUIRED='1', STARPILOT_PYTHON=sys.executable)
  # An inherited fast-lint flag must not silently remove type/spelling checks.
  environment.pop('FAST', None)
  environment['PATH'] = str(Path(sys.executable).parent) + os.pathsep + environment.get('PATH', '')
  return run([entry for stage in STAGES if stage in selected for entry in plan[stage]], output, environment=environment)


if __name__ == '__main__':
  raise SystemExit(main())
