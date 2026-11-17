import json
import inspect
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import create_autospec, patch

from openpilot.starpilot.maps import operation_owner as ops


GEN_A = 'a' * 64
REGION = {'token': 'us_state.Oklahoma', 'name': 'Oklahoma', 'bounds': [33.6, -103.0, 37.0, -94.4],
          'groups': 1, 'available': True, 'unavailable': ''}


def start_request(expected: str = '') -> dict:
  return {'version': 1, 'op': 'start', 'regionToken': REGION['token'], 'maxTransferBytes': 1024,
          'maxNewDiskBytes': 2048, 'expectedCurrentGeneration': expected}


class TestMapSnapshotOperationOwner(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.base = Path(self.temp.name).resolve()
    self.root = self.base / 'starpilot/maps/offline'
    self.current = ''
    self.parked = True

  def owner(self, execute, **kwargs):
    owner = ops.MapSnapshotOperationOwner(root=self.root, marker=self.base / 'operation.json',
                                          catalog=lambda: [REGION], parked=lambda: self.parked,
                                          selected=lambda: self.current, execute=execute,
                                          package_ready=lambda: True,
                                          snapshot_info=lambda: {'generation': self.current, 'storedBytes': 32,
                                                                 'tileCount': 1, 'bounds': REGION['bounds']}, **kwargs)
    self.addCleanup(owner.close)
    return owner

  @staticmethod
  def await_terminal(owner):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
      status = owner.snapshot()
      if status['state'] not in ops.ACTIVE:
        return status
      time.sleep(.01)
    raise AssertionError('operation remained active')

  def test_setup_reports_package_and_snapshot_independently_without_transfer(self):
    executed = []
    owner = self.owner(lambda *args: executed.append(args))
    self.current = GEN_A
    result = owner.dispatch({'version': 1, 'op': 'setup'})
    self.assertTrue(result['packageReady'])
    self.assertEqual(result['packageState'], 'ready')
    self.assertTrue(result['snapshotReady'])
    self.assertEqual(result['selectedGeneration'], GEN_A)
    self.assertTrue(result['parked'])
    self.assertGreater(result['freeDiskBytes'], 0)
    self.assertEqual(executed, [])
    self.assertFalse(self.root.exists())
    self.parked = False
    self.assertFalse(owner.setup()['parked'])
    self.assertEqual(self.current, GEN_A)

  def test_setup_invalid_package_does_not_clear_saved_selection(self):
    owner = self.owner(lambda *args: self.fail('setup must not execute provider'))
    owner._package_ready = lambda: False
    self.current = GEN_A
    with patch.object(ops.shadow, 'PROVIDER_BINARY', self.base / 'mapd'), patch.object(ops.shadow, 'PROVIDER_MANIFEST', self.base / 'manifest.json'):
      self.assertEqual(owner.setup()['packageState'], 'missing_binary')
      (self.base / 'mapd').write_bytes(b'bad')
      self.assertEqual(owner.setup()['packageState'], 'missing_manifest')
      (self.base / 'manifest.json').write_text('{}')
      result = owner.setup()
      self.assertEqual(result['packageState'], 'invalid_package')
      self.assertTrue(result['snapshotReady'])
      self.assertEqual(self.current, GEN_A)
      with self.assertRaises(ops.OperationError):
        owner.dispatch({'version': 1, 'op': 'setup', 'url': 'https://example.com'})

  def test_first_install_prepare_then_select_and_status(self):
    def execute(mode, args, progress):
      self.assertTrue(self.root.is_dir())
      if mode == '--snapshot-managed-prepare':
        progress({'phase': 'transferring', 'completedGroups': 1, 'totalGroups': 1,
                  'transferredBytes': 512, 'transferBudgetBytes': 1024})
      else:
        self.assertIn('--expected-current', args)
        self.current = GEN_A
      return GEN_A

    owner = self.owner(execute)
    started = owner.start(start_request())
    self.assertTrue(started['operationId'].startswith(owner._session + ':'))
    done = self.await_terminal(owner)
    self.assertEqual(done['state'], 'completed')
    self.assertEqual(done['selectedGeneration'], GEN_A)
    self.assertTrue(done['selectedForNextShadowStart'])
    self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

  def test_nonparked_or_changed_selection_never_creates_root(self):
    for parked, current, code in ((False, '', 'not_parked'), (True, GEN_A, 'selection_changed')):
      self.parked, self.current = parked, current
      owner = self.owner(lambda *args: GEN_A)
      with self.assertRaises(ops.OperationError) as caught:
        owner.start(start_request())
      self.assertEqual(caught.exception.code, code)
      self.assertFalse(self.root.exists())

  def test_prefixed_download_and_cancel_preserve_normal_selection(self):
    normal = self.root
    normal.mkdir(parents=True)
    saved = b'{"version":1,"generation":"' + GEN_A.encode() + b'"}'
    (normal / 'current.json').write_bytes(saved)
    entered, release = threading.Event(), threading.Event()
    def execute(mode, args, progress):
      self.assertEqual(args[args.index('--offline-root') + 1], str(self.root))
      entered.set()
      self.assertTrue(release.wait(2))
      return GEN_A
    with patch.dict(os.environ, {'OPENPILOT_PREFIX': 'desk-check'}):
      self.root = ops.offline_root(self.base)
      with patch.object(ops, 'offline_root', return_value=self.root):
        owner = ops.MapSnapshotOperationOwner(catalog=lambda: [REGION], parked=lambda: self.parked,
                                              execute=execute, package_ready=lambda: True)
      self.addCleanup(owner.close)
      self.assertEqual(owner.root, self.base / 'starpilot-desk-check/maps/offline')
      operation_id = owner.start(start_request())['operationId']
      self.assertTrue(entered.wait(2))
      owner.cancel(operation_id)
      release.set()
      self.assertEqual(self.await_terminal(owner)['state'], 'canceled')
      self.assertEqual((normal / 'current.json').read_bytes(), saved)
      self.assertFalse((self.root / 'current.json').exists())

  def test_status_collects_evidence_but_start_rechecks_it(self):
    owner = self.owner(lambda *args: GEN_A)
    reads = []
    def parked():
      reads.append(self.parked)
      return self.parked
    owner._parked = parked
    owner.catalog()
    self.assertTrue(reads)
    self.parked = False
    with self.assertRaises(ops.OperationError) as caught:
      owner.start(start_request())
    self.assertEqual(caught.exception.code, 'not_parked')
    self.assertEqual(reads[-1], False)
    self.assertFalse(self.root.exists())

  def test_symlink_ancestor_rejected_without_following_it(self):
    external = self.base / 'external'
    external.mkdir()
    (self.base / 'starpilot').symlink_to(external, target_is_directory=True)
    owner = self.owner(lambda *args: GEN_A)
    with self.assertRaises(ops.OperationError):
      owner.start(start_request())
    self.assertFalse((external / 'maps').exists())

  def test_cancel_and_selection_race_do_not_claim_completion(self):
    begun = threading.Event()
    release = threading.Event()

    def execute(mode, args, progress):
      if mode == '--snapshot-managed-prepare':
        begun.set()
        self.assertTrue(release.wait(2))
      elif self.current != '':
        raise ops.OperationError('selection_changed')
      return GEN_A

    owner = self.owner(execute)
    op = owner.start(start_request())['operationId']
    self.assertTrue(begun.wait(2))
    owner.cancel(op)
    release.set()
    self.assertEqual(self.await_terminal(owner)['state'], 'canceled')
    self.assertEqual(self.current, '')

    self.current = ''
    begun.clear()
    release.clear()
    owner.start(start_request())
    self.assertTrue(begun.wait(2))
    self.current = GEN_A
    release.set()
    self.assertEqual(self.await_terminal(owner)['errorCode'], 'selection_changed')

  def test_cancel_after_acknowledged_select_reports_committed_selector(self):
    holder = {}

    def execute(mode, args, progress):
      if mode == '--snapshot-select':
        self.current = GEN_A
        holder['owner']._cancel.set()
      return GEN_A

    owner = self.owner(execute)
    holder['owner'] = owner
    owner.start(start_request())
    self.assertEqual(self.await_terminal(owner)['state'], 'completed')
    self.assertTrue(owner.snapshot()['selectedForNextShadowStart'])

  def test_absolute_receive_deadline_rejects_trickle(self):
    sender, receiver = socket.socketpair()
    self.addCleanup(sender.close)
    self.addCleanup(receiver.close)
    sender.sendall(struct.pack('!I', 4096) + b'{')
    with self.assertRaises((ops.MapOperationUnavailable, TimeoutError)):
      ops._receive(receiver, 4096, deadline_s=.05)

  def test_client_receives_reply_after_one_second_within_total_deadline(self):
    sender, receiver = socket.socketpair()
    self.addCleanup(sender.close)
    self.addCleanup(receiver.close)

    def delayed_reply():
      time.sleep(1.1)
      sender.sendall(ops._encode({'version': 1, 'ok': True, 'result': {}}, ops.MAX_STATUS))

    thread = threading.Thread(target=delayed_reply)
    thread.start()
    self.assertTrue(ops._receive(receiver, ops.MAX_STATUS, deadline_s=2)['ok'])
    thread.join(2)

  def test_catalog_accepts_bundled_oversize_unavailable_regions(self):
    rows = [{**REGION, 'token': f'nation.region{i}'} for i in range(229)]
    rows[0] = {**rows[0], 'groups': 1200, 'available': False, 'unavailable': 'too_large'}
    completed = subprocess.CompletedProcess([], 0, json.dumps(rows).encode(), b'')
    with patch.object(ops.subprocess, 'run', return_value=completed):
      self.assertEqual(ops._catalog()[0]['unavailable'], 'too_large')

  def test_catalog_accepts_canada_and_japan_within_snapshot_budget(self):
    rows = [{**REGION, 'token': f'nation.region{i}'} for i in range(229)]
    for i, (token, groups) in enumerate((('nation.CA', 765), ('nation.JP', 72))):
      rows[i] = {**rows[i], 'token': token, 'groups': groups}
    completed = subprocess.CompletedProcess([], 0, json.dumps(rows).encode(), b'')
    with patch.object(ops.subprocess, 'run', return_value=completed):
      self.assertEqual([row['groups'] for row in ops._catalog()[:2]], [765, 72])
    rows[0]['groups'] = ops.MAX_REGION_GROUPS + 1
    completed = subprocess.CompletedProcess([], 0, json.dumps(rows).encode(), b'')
    with patch.object(ops.subprocess, 'run', return_value=completed):
      with self.assertRaises(ops.OperationError):
        ops._catalog()

  def test_boolean_version_is_not_v1(self):
    owner = self.owner(lambda *args: GEN_A)
    with self.assertRaises(ops.OperationError) as caught:
      owner.dispatch({'version': True, 'op': 'status'})
    self.assertEqual(caught.exception.code, 'invalid_request')

  def test_client_timeout_contract(self):
    self.assertEqual(inspect.signature(ops.request_operation).parameters['timeout_s'].default, 8.0)
    with self.assertRaisesRegex(ops.MapOperationUnavailable, 'invalid operation timeout'):
      ops.request_operation({'version': 1, 'op': 'status'}, timeout_s=11)

  def test_marker_io_does_not_block_cancellation(self):
    writing = threading.Event()
    release_write = threading.Event()
    begun = threading.Event()
    release_prepare = threading.Event()

    def execute(mode, args, progress):
      begun.set()
      self.assertTrue(release_prepare.wait(2))
      return GEN_A

    owner = self.owner(execute)

    def blocked_marker(value):
      writing.set()
      self.assertTrue(release_write.wait(2))

    try:
      with patch.object(owner, '_write_marker', side_effect=blocked_marker):
        op = owner.start(start_request())['operationId']
        self.assertTrue(writing.wait(1))
        self.assertTrue(begun.wait(1))
        before = time.monotonic()
        owner.cancel(op)
        self.assertLess(time.monotonic() - before, .2)
        release_prepare.set()
        self.assertEqual(self.await_terminal(owner)['state'], 'canceled')
    finally:
      release_write.set()
      release_prepare.set()

  @unittest.skipUnless(sys.platform == 'linux', 'Linux prctl contract')
  def test_parent_death_guard_is_not_optimized_away(self):
    command = ops._guarded_go_command(Path('/bin/true'), [])
    command[1:1] = ['-O']
    command[4] = '-1'
    self.assertEqual(subprocess.run(command, check=False, timeout=2).returncode, 125)

  @unittest.skipUnless(sys.platform == 'linux', 'Linux process and peer-credential contract')
  def test_real_sigint_ignoring_child_is_killed_and_reaped(self):
    script = self.base / 'ignore.py'
    script.write_text('import signal,time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\nprint("bad-json", flush=True)\ntime.sleep(30)\n')
    owner = self.owner(lambda *args: GEN_A)
    with patch.object(ops.shadow, 'PROVIDER_BINARY', Path(sys.executable)):
      with patch.object(ops, '_guarded_go_command', return_value=[sys.executable, str(script)]):
        with self.assertRaises((ops.OperationError, ops.MapOperationUnavailable, ValueError)):
          owner._run_go('--snapshot-managed-prepare', [], owner._progress)
    self.assertIsNone(owner._child)

  @unittest.skipUnless(sys.platform == 'linux', 'Linux peer-credential contract')
  def test_disconnected_client_does_not_stop_server(self):
    owner = self.owner(lambda *args: GEN_A)
    stop = threading.Event()
    path = self.base / 'operations.sock'
    thread = threading.Thread(target=ops.serve, kwargs={'owner': owner, 'socket_path': path, 'stop': stop}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 2
    while not path.exists() and time.monotonic() < deadline:
      time.sleep(.01)
    with socket.socket(socket.AF_UNIX) as client:
      client.connect(str(path))
      client.sendall(struct.pack('!I', 30) + b'{')
    nested = ('[' * 1500 + ']' * 1500).encode()
    with socket.socket(socket.AF_UNIX) as client:
      client.settimeout(3)
      client.connect(str(path))
      client.sendall(struct.pack('!I', len(nested)) + nested)
      self.assertEqual(ops._receive(client, ops.MAX_STATUS)['error']['code'], 'invalid_request')
    reply = ops.request_operation({'version': 1, 'op': 'status'}, socket_path=path, timeout_s=3)
    self.assertTrue(reply['ok'])
    stop.set()
    thread.join(2)
    self.assertFalse(thread.is_alive())


if __name__ == '__main__':
  unittest.main()


class TestMapOperationEntrypoint(unittest.TestCase):
  def test_public_parked_source_and_cleanup(self):
    from openpilot.starpilot.galaxy.settings import LiveContextSource
    evidence = create_autospec(LiveContextSource, instance=True, spec_set=True)
    for failure in (None, RuntimeError('serve failed')):
      with self.subTest(failure=failure), patch.object(ops.sys, 'platform', 'linux'), \
           patch.object(ops, 'Params'), patch.object(ops, '_owned_socket_dir'), \
           patch('openpilot.starpilot.galaxy.settings.LiveContextSource', return_value=evidence), \
           patch.object(ops, 'MapSnapshotOperationOwner') as factory, \
           patch.object(ops, 'serve', side_effect=failure) as serve:
        evidence.reset_mock()
        if failure is None:
          ops.main()
        else:
          with self.assertRaisesRegex(RuntimeError, 'serve failed'):
            ops.main()
        evidence.parked.assert_called_once_with()
        self.assertIs(factory.call_args.kwargs['parked'], evidence.parked)
        serve.assert_called_once_with(factory.return_value)
        factory.return_value.close.assert_called_once_with()
        evidence.close.assert_called_once_with()

  def test_evidence_closed_when_setup_fails(self):
    from openpilot.starpilot.galaxy.settings import LiveContextSource
    evidence = create_autospec(LiveContextSource, instance=True, spec_set=True)
    with patch.object(ops.sys, 'platform', 'linux'), patch.object(ops, 'Params'), \
         patch('openpilot.starpilot.galaxy.settings.LiveContextSource', return_value=evidence), \
         patch.object(ops, '_owned_socket_dir', side_effect=OSError('socket directory')):
      with self.assertRaisesRegex(OSError, 'socket directory'):
        ops.main()
    evidence.close.assert_called_once_with()
