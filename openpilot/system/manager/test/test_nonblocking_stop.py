import signal
from unittest.mock import Mock, call, patch

import pytest

from openpilot.starpilot.models.recovery_process import ModeldProcess
from openpilot.system.manager import process


def owner(kind):
  if kind == 'native':
    return process.NativeProcess('worker', '.', ['unused'], lambda *_: True)
  if kind == 'modeld':
    result = ModeldProcess('modeld', 'unused', lambda *_: True)
    result.context_ready = True
    return result
  return process.PythonProcess('starportd', 'unused', lambda *_: True)


@pytest.mark.parametrize('kind', ['python', 'native', 'modeld'])
@pytest.mark.parametrize('restart', [False, True])
def test_polling_stop_and_restart_never_wait_for_a_live_child(kind, restart):
  p = owner(kind)
  child = Mock(pid=123, exitcode=None)
  p.proc = child
  now = [100.]
  with patch.object(process.time, 'monotonic', side_effect=lambda: now[0]), \
       patch.object(process.os, 'kill') as kill, \
       patch.object(process, 'join_process', side_effect=AssertionError('NONBLOCKING_STOP_WAITED')), \
       patch.object(process, 'Process', side_effect=AssertionError('OVERLAPPING_CHILD')):
    p.stop(block=False)
    for elapsed in (0.5, 1., 4.99):
      now[0] = 100. + elapsed
      if restart:
        p.start()
      else:
        p.stop(block=False)
      assert p.proc is child and p.shutting_down
      assert p.stop_requested_at == 100.
      assert kill.call_args_list == [call(123, signal.SIGINT)]
    now[0] = 105.
    p.stop(block=False)
    assert kill.call_args_list == [call(123, signal.SIGINT), call(123, signal.SIGKILL)]
    child.join.assert_not_called()
    child.exitcode = -signal.SIGKILL
    assert p.stop(block=False) == -signal.SIGKILL
    assert p.proc is None and not p.shutting_down and p.stop_requested_at is None


def test_nonblocking_stop_retry_false_preserves_original_escalation_deadline():
  p = owner('python')
  p.proc = Mock(pid=123, exitcode=None)
  now = [100.]
  with patch.object(process.time, 'monotonic', side_effect=lambda: now[0]), \
       patch.object(process.os, 'kill') as kill, \
       patch.object(process, 'join_process', side_effect=AssertionError('NONBLOCKING_STOP_WAITED')):
    p.stop(retry=False, block=False)
    now[0] = 106.
    p.stop(retry=False, block=False)
    assert kill.call_count == 1
    p.stop(block=False)
    assert kill.call_args.args == (123, signal.SIGKILL)


@pytest.mark.parametrize('retry', [False, True])
def test_explicit_blocking_shutdown_keeps_wait_and_kill_behavior(retry):
  p = owner('python')
  child = Mock(pid=123, exitcode=None)
  p.proc = child
  child.join.side_effect = lambda: setattr(child, 'exitcode', -signal.SIGKILL)
  with patch.object(process.os, 'kill') as kill, patch.object(process, 'join_process') as wait:
    result = p.stop(retry=retry)
    wait.assert_called_once_with(child, 5)
    assert kill.call_args.args == (123, signal.SIGKILL if retry else signal.SIGINT)
    assert child.join.call_count == int(retry)
    assert result == (-signal.SIGKILL if retry else None)
