import platform
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.ci.run_predeploy import STAGES, commands, run, main
from tools.ci import run_local_tests


class TestPredeploy(unittest.TestCase):
  def test_local_mac_rejects_bsd_sed_before_checks_run(self):
    with tempfile.TemporaryDirectory() as temporary:
      sed = Path(temporary) / 'sed'
      sed.write_text('#!/bin/sh\necho "sed: illegal option -- -" >&2\nexit 1\n')
      sed.chmod(0o755)
      with (patch.dict(os.environ, {'SP_HOST_RUNTIME': '1'}),
            patch.object(run_local_tests.platform, 'system', return_value='Darwin'),
            patch.object(Path, 'is_dir', return_value=False),
            patch.object(run_local_tests.shutil, 'which', return_value=str(sed)),
            patch.object(run_local_tests, 'run') as invoke,
            patch('sys.stderr') as stderr):
        with self.assertRaises(SystemExit) as error:
          run_local_tests.main(['--output', str(Path(temporary) / 'results')])
        self.assertEqual(error.exception.code, 2)
        self.assertIn('brew install gnu-sed', ''.join(call.args[0] for call in stderr.write.call_args_list))
        invoke.assert_not_called()

  def test_local_command_preserves_failure_and_uses_isolated_host(self):
    with tempfile.TemporaryDirectory() as temporary, \
         patch.dict(os.environ, {'SP_HOST_RUNTIME': '0'}), \
         patch.object(run_local_tests.subprocess, 'run') as invoke:
      invoke.return_value.returncode = 7
      output = (Path(temporary) / 'results').resolve()
      self.assertEqual(run_local_tests.main(['--output', str(output)]), 7)
      self.assertEqual(invoke.call_args.args[0], [str(run_local_tests.ROOT / 'dev'), 'python',
                       'tools/ci/run_local_tests.py', '--output', str(output)])

  def test_local_regressions_use_disposable_state_and_full_lint(self):
    observed = {}
    def invoke(plan, output, *, environment):
      observed.update(environment)
      self.assertTrue(Path(environment['PARAMS_ROOT']).is_dir())
      self.assertNotIn('FAST', environment)
      self.assertNotIn('SKIP', environment)
      self.assertNotIn('PYTEST_ADDOPTS', environment)
      self.assertEqual(plan[:2], [('mapd-package', [sys.executable, 'tools/release/stage_mapd_provider.py',
                                                    '--source', str(run_local_tests.ROOT), '--require-tracked']),
                                 ('lint', ['bash', 'scripts/lint/lint.sh'])])
      self.assertIn('openpilot/starpilot/tests', plan[-1][1])
      self.assertIn('opendbc_repo/opendbc/safety/tests/test_gm_bolt_pedal.py', plan[-1][1])
      self.assertIn('opendbc_repo/opendbc/safety/tests/test_hyundai_ioniq6_long.py', plan[-1][1])
      return 9
    with tempfile.TemporaryDirectory() as temporary, \
         patch.dict(os.environ, {'SP_HOST_RUNTIME': '1', 'FAST': '1', 'SKIP': 'ty', 'PYTEST_ADDOPTS': '--collect-only'}), \
         patch.object(run_local_tests, 'run', side_effect=invoke):
      self.assertEqual(run_local_tests.main(['--output', str(Path(temporary) / 'results')]), 9)
    self.assertFalse(Path(observed['PARAMS_ROOT']).exists())

  def test_full_local_command_delegates_to_existing_suite(self):
    with tempfile.TemporaryDirectory() as temporary, \
         patch.dict(os.environ, {'SP_HOST_RUNTIME': '1', 'PYTEST_ADDOPTS': '--collect-only'}), \
         patch.object(run_local_tests.platform, 'system', return_value='Linux'), \
         patch.object(run_local_tests.subprocess, 'call', return_value=3) as invoke:
      output = (Path(temporary) / 'results').resolve()
      self.assertEqual(run_local_tests.main(['--full', '--download', '--output', str(output)]), 3)
      self.assertEqual(invoke.call_args.args[0], [sys.executable, 'tools/ci/run_predeploy.py',
                       '--output', str(output), '--download'])
      self.assertNotIn('PYTEST_ADDOPTS', invoke.call_args.kwargs['env'])

  def test_failure_is_preserved_and_later_check_does_not_run(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      marker = root / 'unexpected'
      plan = [('broken', [sys.executable, '-c', 'print("diagnostic"); raise SystemExit(7)']),
              ('later', [sys.executable, '-c', f'from pathlib import Path; Path({str(marker)!r}).touch()'])]
      self.assertEqual(run(plan, root / 'results'), 7)
      self.assertFalse(marker.exists())
      self.assertIn('diagnostic', (root / 'results/broken.log').read_text())
      self.assertEqual(json.loads((root / 'results/results.json').read_text())[0]['returncode'], 7)

  def test_recorded_download_is_explicit_and_both_variants_run(self):
    plan = commands(Path('/results'), Path('/cache'))
    self.assertEqual([name for name, _ in plan['recorded']], ['recorded-debug', 'recorded-release'])
    self.assertTrue(all('--download' not in command for _, command in plan['recorded']))
    self.assertTrue(all('--download' in command for _, command in commands(Path('/results'), Path('/cache'), True)['recorded']))

  def test_full_plan_builds_and_requires_native_protocols(self):
    plan = commands(Path('/results'), Path('/cache'))
    self.assertEqual(plan['build'][0][1][1:], ['-m', 'SCons', '-j4'])
    self.assertEqual([name for name, _ in plan['native']], [
      'test_panda_usb', 'test_pandad_canprotocol', 'test_aol_protocol', 'test_aol_wire', 'test_panda_faults',
      *(['test_producer_timing', 'test_producer_sink'] if platform.system() == 'Linux' else []), 'map-ipc'])
    self.assertIn('-race', plan['native'][-1][1])

  def test_full_plan_requires_unfiltered_process_replay(self):
    with patch('tools.ci.run_predeploy.os.cpu_count', return_value=8):
      plan = commands(Path('/results'), Path('/cache'))
    self.assertIn('replay', STAGES)
    self.assertEqual(plan['replay'], [('process-replay', [sys.executable,
      'openpilot/selfdrive/test/process_replay/test_processes.py', '-j', '8', '--output', '/results/process-replay'])])

  def test_default_run_includes_process_replay(self):
    with tempfile.TemporaryDirectory() as temporary, \
         patch.object(sys, 'argv', ['run_predeploy.py', '--output', str(Path(temporary) / 'results')]), \
         patch('tools.ci.run_predeploy.platform.system', return_value='Linux'), \
         patch('tools.ci.run_predeploy.run', return_value=0) as invoke:
      self.assertEqual(main(), 0)
      self.assertIn('process-replay', [name for name, _ in invoke.call_args.args[0]])

  def test_reports_cannot_be_overwritten(self):
    with tempfile.TemporaryDirectory() as temporary:
      with self.assertRaises(FileExistsError):
        run([], Path(temporary))


  def test_full_source_stage_does_not_inherit_fast_lint(self):
    with tempfile.TemporaryDirectory() as temporary, \
         patch.dict(os.environ, {'FAST': '1'}), \
         patch.object(sys, 'argv', ['run_predeploy.py', '--output', str(Path(temporary) / 'results'), '--stage', 'source']), \
         patch('tools.ci.run_predeploy.run', return_value=0) as invoke:
      self.assertEqual(main(), 0)
      self.assertNotIn('FAST', invoke.call_args.kwargs['environment'])
      self.assertIn('lint', [name for name, _ in invoke.call_args.args[0]])


  def test_native_stage_rejects_non_linux_before_running(self):
    with tempfile.TemporaryDirectory() as temporary, \
         patch.object(sys, 'argv', ['run_predeploy.py', '--output', str(Path(temporary) / 'results')]), \
         patch('tools.ci.run_predeploy.platform.system', return_value='Darwin'), \
         patch('tools.ci.run_predeploy.run') as invoke, \
         patch('sys.stderr'):
      with self.assertRaises(SystemExit) as error:
        main()
      self.assertEqual(error.exception.code, 2)
      invoke.assert_not_called()
