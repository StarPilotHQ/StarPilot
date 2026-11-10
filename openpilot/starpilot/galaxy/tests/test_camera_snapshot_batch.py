from io import BytesIO
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from openpilot.starpilot.galaxy.camera_snapshot import CameraSnapshot, SnapshotDenied, SnapshotUnavailable, _CAPTURE_LOCK


JPEG = b'\xff\xd8\xff\xd9'


class Clock:
  now = 0.0

  def __call__(self):
    return self.now


class Reader:
  def __init__(self, clock, *, ready=float('inf'), body=JPEG, code=0):
    self.clock, self.ready, self.body, self.code = clock, ready, body, code
    self.stdout = BytesIO()
    self.returncode = None
    self.killed_at = self.waited_at = None
    self.poll_timeouts = []

  def communicate(self, timeout):
    self.poll_timeouts.append(timeout)
    if self.ready <= self.clock.now + timeout:
      self.clock.now = max(self.clock.now, self.ready)
      self.returncode = self.code
      return self.body, None
    self.clock.now += timeout
    raise subprocess.TimeoutExpired('reader', timeout)

  def poll(self):
    return self.returncode

  def kill(self):
    self.killed_at = self.clock.now
    self.returncode = -9

  def wait(self):
    self.waited_at = self.clock.now
    return self.returncode


class CameraSnapshotBatchTest(unittest.TestCase):
  def setUp(self):
    self.clock = Clock()
    self.request = self.enterContext(patch('openpilot.starpilot.galaxy.camera_request.request'))
    self.mark_frame = self.enterContext(patch('openpilot.starpilot.galaxy.camera_request.mark_frame'))
    self.enterContext(patch('openpilot.starpilot.galaxy.camera_request.boot_ns', return_value=123))
    self.readers = {}
    self.launched = []

  def spawn(self, args, **kwargs):
    camera = args[3]
    self.launched.append((camera, self.clock.now, args[4:]))
    self.assertEqual(kwargs, {'stdout': subprocess.PIPE, 'stderr': subprocess.DEVNULL, 'close_fds': True})
    reader = self.readers[camera]
    if isinstance(reader, Exception):
      raise reader
    return reader

  def owner(self):
    return CameraSnapshot(spawn=self.spawn, clock=self.clock)

  def assert_released(self):
    self.assertTrue(_CAPTURE_LOCK.acquire(blocking=False))
    _CAPTURE_LOCK.release()

  def test_parallel_launch_single_lease_and_generator_input(self):
    self.readers = {name: Reader(self.clock, ready=ready) for name, ready in [('wide', 5), ('cabin', 10)]}
    result = self.owner().capture_many(iter(['wide', 'cabin']), permitted=lambda: True, parked=True)
    self.assertEqual(result, {'wide': JPEG, 'cabin': JPEG})
    self.assertEqual(self.launched, [('wide', 0, ['parked']), ('cabin', 0, ['parked'])])
    self.assertAlmostEqual(self.clock.now, 10)
    self.request.assert_called_once_with()
    self.mark_frame.assert_called_once()
    self.assertEqual(self.mark_frame.call_args.kwargs['clock'](), 123)
    for reader in self.readers.values():
      self.assertIsNotNone(reader.waited_at)
      self.assertTrue(reader.stdout.closed)
      self.assertIsNone(reader.killed_at)
      self.assertLessEqual(max(reader.poll_timeouts), .1)
    self.assert_released()

  def test_each_deadline_reaps_before_waiting_for_other_camera(self):
    for cameras in [('wide', 'cabin'), ('cabin', 'wide')]:
      with self.subTest(cameras=cameras):
        self.clock.now = 0
        self.readers = {name: Reader(self.clock) for name in cameras}
        result = self.owner().capture_many(cameras, permitted=lambda: True)
        for name, deadline in [('wide', 19), ('cabin', 24)]:
          self.assertIsInstance(result[name], SnapshotUnavailable)
          self.assertAlmostEqual(self.readers[name].killed_at, deadline)
          self.assertAlmostEqual(self.readers[name].waited_at, deadline)
          self.assertTrue(self.readers[name].stdout.closed)
        self.assert_released()

  def test_slow_first_spawn_does_not_extend_shared_capture_budget(self):
    self.readers = {name: Reader(self.clock) for name in ['wide', 'cabin']}
    def slow_spawn(args, **kwargs):
      reader = self.spawn(args, **kwargs)
      if args[3] == 'wide':
        self.clock.now += 20
      return reader
    result = CameraSnapshot(spawn=slow_spawn, clock=self.clock).capture_many(self.readers, permitted=lambda: True)
    self.assertEqual(self.launched, [('wide', 0, []), ('cabin', 20, [])])
    self.assertAlmostEqual(self.clock.now, 24)
    for name, reaped_at in [('wide', 20), ('cabin', 24)]:
      self.assertIsInstance(result[name], SnapshotUnavailable)
      self.assertAlmostEqual(self.readers[name].waited_at, reaped_at)
      self.assertTrue(self.readers[name].stdout.closed)
    self.assert_released()

  def test_spawn_exhausting_budget_does_not_launch_next_reader(self):
    self.readers = {name: Reader(self.clock) for name in ['wide', 'cabin']}
    def slow_spawn(args, **kwargs):
      reader = self.spawn(args, **kwargs)
      self.clock.now += 25
      return reader
    result = CameraSnapshot(spawn=slow_spawn, clock=self.clock).capture_many(self.readers, permitted=lambda: True)
    self.assertEqual(self.launched, [('wide', 0, [])])
    self.assertTrue(all(isinstance(value, SnapshotUnavailable) for value in result.values()))
    self.assertEqual(self.readers['wide'].waited_at, 25)
    self.assertTrue(self.readers['wide'].stdout.closed)
    self.assert_released()

  def test_revocation_during_spawn_prevents_next_launch(self):
    self.readers = {name: Reader(self.clock) for name in ['wide', 'cabin']}
    with self.assertRaises(SnapshotDenied):
      self.owner().capture_many(self.readers, permitted=lambda: not self.launched)
    self.assertEqual(self.launched, [('wide', 0, [])])
    self.assertEqual(self.readers['wide'].waited_at, 0)
    self.assertTrue(self.readers['wide'].stdout.closed)
    self.assert_released()

  def test_partial_failure_preserves_other_camera(self):
    for failure in ['timeout', 'invalid', 'exit', 'spawn']:
      for cameras in [('wide', 'cabin'), ('cabin', 'wide')]:
        with self.subTest(failure=failure, cameras=cameras):
          self.clock.now = 0
          self.readers = {'cabin': Reader(self.clock, ready=1),
                          'wide': (OSError('spawn failed') if failure == 'spawn' else
                                   Reader(self.clock, ready=float('inf') if failure == 'timeout' else 0,
                                          body=b'bad' if failure == 'invalid' else JPEG, code=1 if failure == 'exit' else 0))}
          result = self.owner().capture_many(cameras, permitted=lambda: True)
          self.assertEqual(result['cabin'], JPEG)
          self.assertIsInstance(result['wide'], SnapshotUnavailable)
          self.assertTrue(self.readers['cabin'].stdout.closed)
          self.assert_released()

  def test_invalid_collections_have_no_side_effects(self):
    for cameras in [[], (), ['wide', 'wide'], ['unknown'], ['wide', []], 'wide']:
      with self.subTest(cameras=cameras):
        owner = self.owner()
        permitted = Mock(return_value=True)
        with self.assertRaises(ValueError):
          owner.capture_many(cameras, permitted=permitted)
        self.assertEqual(owner.last_request, {})
        self.assertEqual(self.launched, [])
        permitted.assert_not_called()
        self.request.assert_not_called()
        self.assert_released()

  def test_revocation_cleans_up_both_active_readers(self):
    self.readers = {name: Reader(self.clock) for name in ['wide', 'cabin']}
    with self.assertRaises(SnapshotDenied):
      self.owner().capture_many(self.readers, permitted=lambda: self.clock.now < .2)
    for reader in self.readers.values():
      self.assertIsNotNone(reader.killed_at)
      self.assertIsNotNone(reader.waited_at)
      self.assertTrue(reader.stdout.closed)
    self.mark_frame.assert_not_called()
    self.assert_released()

  def test_final_revocation_discards_successes_without_receipt(self):
    self.readers = {name: Reader(self.clock, ready=0) for name in ['wide', 'cabin']}
    permitted = Mock(side_effect=[True, True, True, False])
    with self.assertRaises(SnapshotDenied):
      self.owner().capture_many(self.readers, permitted=permitted)
    self.mark_frame.assert_not_called()
    self.assert_released()

  def test_request_failure_releases_lock(self):
    self.request.side_effect = OSError('lease unavailable')
    with self.assertRaises(SnapshotUnavailable):
      self.owner().capture_many(['wide', 'cabin'], permitted=lambda: True)
    self.assertEqual(self.launched, [])
    self.assert_released()

  def test_single_camera_spawn_failure_keeps_exception_contract(self):
    self.readers['wide'] = OSError('spawn failed')
    with self.assertRaisesRegex(SnapshotUnavailable, 'Camera unavailable'):
      self.owner().capture('wide', permitted=lambda: True)
    self.assert_released()

  def test_unexpected_poll_failure_still_cleans_up_every_reader(self):
    self.readers = {name: Reader(self.clock) for name in ['wide', 'cabin']}
    self.readers['wide'].communicate = Mock(side_effect=RuntimeError('reader failed'))
    with self.assertRaisesRegex(RuntimeError, 'reader failed'):
      self.owner().capture_many(self.readers, permitted=lambda: True)
    for reader in self.readers.values():
      self.assertIsNotNone(reader.killed_at)
      self.assertIsNotNone(reader.waited_at)
      self.assertTrue(reader.stdout.closed)
    self.assert_released()

  def test_cleanup_failure_keeps_reader_tracked_for_final_retry(self):
    self.readers = {name: Reader(self.clock) for name in ['wide', 'cabin']}
    wide = self.readers['wide']
    original_kill = wide.kill
    calls = 0
    def fail_first_kill():
      nonlocal calls
      calls += 1
      if calls == 1:
        raise OSError('transient kill failure')
      original_kill()
    wide.kill = fail_first_kill
    with self.assertRaises(SnapshotUnavailable):
      self.owner().capture_many(self.readers, permitted=lambda: True)
    self.assertEqual(calls, 2)
    for reader in self.readers.values():
      self.assertIsNotNone(reader.killed_at)
      self.assertIsNotNone(reader.waited_at)
      self.assertTrue(reader.stdout.closed)
    self.assert_released()

  def test_exit_between_poll_and_kill_still_reaps_reader(self):
    reader = Reader(self.clock)
    self.readers['wide'] = reader
    def exit_during_kill():
      reader.returncode = 0
      raise ProcessLookupError('already exited')
    reader.kill = exit_during_kill
    result = self.owner().capture_many(['wide'], permitted=lambda: True)
    self.assertIsInstance(result['wide'], SnapshotUnavailable)
    self.assertIsNotNone(reader.waited_at)
    self.assertTrue(reader.stdout.closed)
    self.assert_released()

  def test_throttled_camera_does_not_prevent_independent_capture(self):
    owner = self.owner()
    owner.last_request['wide'] = 0
    self.readers['cabin'] = Reader(self.clock, ready=0)
    result = owner.capture_many(['wide', 'cabin'], permitted=lambda: True)
    self.assertIsInstance(result['wide'], SnapshotUnavailable)
    self.assertEqual(result['cabin'], JPEG)
    self.assertEqual(self.launched, [('cabin', 0, [])])
    self.assert_released()

  def test_real_readers_revoked_together_and_exclusive_lock(self):
    children = []
    def spawn(args, **kwargs):
      with self.assertRaisesRegex(SnapshotUnavailable, 'in progress'):
        CameraSnapshot().capture('narrow', permitted=lambda: True)
      child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
      children.append(child)
      return child
    with self.assertRaises(SnapshotDenied):
      CameraSnapshot(spawn=spawn).capture_many(['wide', 'cabin'], permitted=lambda: len(children) < 2)
    self.assertEqual(len(children), 2)
    for child in children:
      self.assertIsNotNone(child.poll())
      self.assertTrue(child.stdout.closed)
    self.assert_released()
