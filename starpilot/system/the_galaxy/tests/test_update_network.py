import ast
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock


class UpdateNetworkTest(unittest.TestCase):
  def setUp(self):
    source = (Path(__file__).resolve().parents[1] / 'the_galaxy.py').read_text()
    self.ns = dict(os=os, re=re, selectors=selectors, subprocess=subprocess, time=time,
                   _git_command_env=lambda: dict(os.environ), _set_fast_update_progress=Mock(),
                   _set_fast_update_state=Mock(), _FAST_UPDATE_TOTAL_STEPS=5, _FAST_UPDATE_PROGRESS_UPDATE_INTERVAL_S=0.2,
                   _parse_git_progress_line=lambda text: (None, text, None))
    for node in ast.parse(source).body:
      if isinstance(node, ast.FunctionDef) and node.name in ('_run_git_with_progress', '_set_fast_update_error_state'):
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'actual_the_galaxy.py', 'exec'), self.ns)

  def invoke(self, error, args=None, fallback_fails=False):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      script = root / 'git_fixture.py'
      script.write_text('import sys,json\nfrom pathlib import Path\n'
                        'p=Path("calls.json"); a=json.loads(p.read_text()) if p.exists() else []\n'
                        'a.append(sys.argv[1:]);p.write_text(json.dumps(a))\n'
                        f'failed={fallback_fails!r} or "http.version=HTTP/1.1" not in sys.argv\n'
                        f'print({error!r} if failed else "Receiving objects: 100%",flush=True)\n'
                        'sys.exit(128 if failed else 0)\n')
      self.ns['_git_base_cmd'] = lambda: [sys.executable, str(script)]
      result = self.ns['_run_git_with_progress'](str(root), args or ['fetch', '--depth=1', 'origin', 'Dom'],
                                                 timeout=5, step=2, label='Fetching branch snapshot')
      return result, json.loads((root / 'calls.json').read_text())

  def test_http2_fetch_retries_once_using_http1_without_changing_target(self):
    result, calls = self.invoke('error: RPC failed; curl 92 HTTP/2 stream 7 CANCEL\nfatal: early EOF')
    self.assertEqual(result[0], 0)
    self.assertEqual(len(calls), 2)
    self.assertEqual(calls[1], ['-c', 'http.version=HTTP/1.1', *calls[0]])

  def test_failed_fallback_is_not_retried_again(self):
    result, calls = self.invoke('curl 92 HTTP/2 stream CANCEL', fallback_fails=True)
    self.assertEqual(result[0], 128)
    self.assertEqual(len(calls), 2)

  def test_other_errors_do_not_trigger_transport_retry(self):
    for error in ('No space left on device', 'Authentication failed', 'Could not resolve host', 'fatal: early EOF'):
      with self.subTest(error=error):
        result, calls = self.invoke(error)
        self.assertEqual(result[0], 128)
        self.assertEqual(len(calls), 1)

  def test_non_fetch_command_is_never_retried(self):
    result, calls = self.invoke('curl 92 HTTP/2 stream CANCEL', args=['submodule', 'update', '--init'])
    self.assertEqual(result[0], 128)
    self.assertEqual(len(calls), 1)

  def test_explicit_http1_request_is_not_retried(self):
    result, calls = self.invoke('curl 92 HTTP/2 stream CANCEL',
                                args=['-c', 'http.version=HTTP/1.1', 'fetch', 'origin', 'Dom'], fallback_fails=True)
    self.assertEqual(result[0], 128)
    self.assertEqual(len(calls), 1)

  def test_network_failure_is_actionable_and_preserves_git_details(self):
    error = 'RPC failed; curl 92 HTTP/2 stream CANCEL; fatal: early EOF'
    self.ns['_set_fast_update_error_state']('Fast branch switch failed.', RuntimeError(error))
    state = self.ns['_set_fast_update_state'].call_args.kwargs
    self.assertEqual(state['stage'], 'error')
    self.assertFalse(state['running'])
    self.assertIn('network connection', state['message'])
    self.assertEqual(state['lastError'], error)


if __name__ == '__main__':
  unittest.main()
