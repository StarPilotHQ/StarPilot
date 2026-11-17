#!/usr/bin/env python3
"""Run host tests with disposable Params and a private messaging namespace."""

import ast
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'tools/test_runner.py'
EXTRA_PYTEST_FILES = (
  'msgq_repo/msgq/tests/test_receive_gil.py',
  'openpilot/selfdrive/car/tests/test_ford_settings_worker.py',
  'openpilot/system/tests/test_sentry.py',
  'openpilot/system/ui/lib/tests/test_text_caches.py',
  'openpilot/system/ui/widgets/tests/test_label_cache.py',
  'openpilot/selfdrive/ui/tests/test_onboarding_textures.py',
)

# Public compatibility list also used by the local test command.
UI_PYTEST_FILES = EXTRA_PYTEST_FILES


def pytest_files(root=ROOT):
  """Select custom files containing pytest functions or plain Test classes."""
  files = []
  for path in sorted((root / 'openpilot/starpilot').rglob('test_*.py')):
    module = ast.parse(path.read_text(), filename=str(path))
    if any((isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_')) or
           (isinstance(node, ast.ClassDef) and node.name.startswith('Test') and not node.bases)
           for node in module.body):
      files.append(path.relative_to(root).as_posix())
  return files + [path for path in EXTRA_PYTEST_FILES if (root / path).is_file()]


def main(arguments=None):
  arguments = sys.argv[1:] if arguments is None else arguments
  pytest_mode = bool(arguments and arguments[0] == '--pytest')
  if pytest_mode:
    files = pytest_files()
    if not files:
      print('No StarPilot pytest functions were discovered.', file=sys.stderr)
      return 5
  shared_memory = '/tmp' if platform.system() == 'Darwin' else '/dev/shm'
  with tempfile.TemporaryDirectory(prefix='starpilot-host-params-') as params, \
       tempfile.TemporaryDirectory(prefix='msgq_starpilot-host-', dir=shared_memory) as messaging:
    # Set these before imports in the child. UIState constructs Params and
    # subscribers at import time; SCALE avoids querying a desktop monitor.
    environment = dict(os.environ, PARAMS_ROOT=params, SCALE='1',
                       OPENPILOT_PREFIX=Path(messaging).name.removeprefix('msgq_'))
    if pytest_mode:
      environment.pop('PYTEST_ADDOPTS', None)
      environment.pop('PYTEST_PLUGINS', None)
      environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    diagnostics = environment.get('STARPILOT_TEST_DIAGNOSTICS')
    evidence = []
    if pytest_mode and diagnostics:
      directory = Path(diagnostics).resolve()
      directory.mkdir(parents=True, exist_ok=True)
      evidence = ['-p', 'tools.ci.pytest_progress', '--junitxml=' + str(directory / 'pytest.xml')]
    command = ([sys.executable, '-m', 'pytest', '-q', '--import-mode=importlib', *evidence, *arguments[1:], *files] if pytest_mode
               else [sys.executable, str(RUNNER), *arguments])
    result = subprocess.run(command, cwd=ROOT, env=environment, check=False)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == '__main__':
  raise SystemExit(main())
