from io import BytesIO
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
import numpy as np

from openpilot.starpilot.galaxy.camera_request import TTL_NS
from openpilot.starpilot.galaxy.camera_snapshot import CAPTURE_TIMEOUT_SECONDS, CameraSnapshot, SnapshotDenied, SnapshotUnavailable
from openpilot.starpilot.galaxy.camera_snapshot_worker import capture


class CameraSnapshotTest(unittest.TestCase):
  def setUp(self):
    for name in ("request", "mark_frame"):
      replacement = patch("openpilot.starpilot.galaxy.camera_request." + name)
      setattr(self, name + "_mock", replacement.start())
      self.addCleanup(replacement.stop)

  def test_denied_does_not_launch_reader(self):
    owner = CameraSnapshot(spawn=lambda *args, **kwargs: self.fail("launched"))
    with self.assertRaises(SnapshotDenied):
      owner.capture('cabin', permitted=lambda: False)
    with self.assertRaises(ValueError):
      owner.capture('../camera', permitted=lambda: True)

  def test_revocation_kills_and_reaps_blocked_reader(self):
    children = []
    def spawn(args, **kwargs):
      self.assertEqual(args[-1], 'cabin')
      with self.assertRaises(SnapshotUnavailable):
        CameraSnapshot(spawn=lambda *a, **k: self.fail("concurrent reader")).capture(
          'wide', permitted=lambda: True)
      children.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs))
      return children[-1]
    calls = 0
    def permitted():
      nonlocal calls
      calls += 1
      return calls < 3
    with self.assertRaises(SnapshotDenied):
      CameraSnapshot(spawn=spawn).capture('cabin', permitted=permitted)
    self.assertIsNotNone(children[0].poll())
    self.assertTrue(children[0].stdout.closed)

  def test_deadline_kills_blocked_native_reader(self):
    children = []
    ticks = iter(range(100))
    def spawn(args, **kwargs):
      children.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs))
      return children[-1]
    with self.assertRaises(SnapshotUnavailable):
      CameraSnapshot(spawn=spawn, clock=lambda: next(ticks)).capture('cabin', permitted=lambda: True)
    self.assertIsNotNone(children[0].poll())
    self.assertTrue(children[0].stdout.closed)

  def test_reader_result_and_final_authority_check(self):
    def spawn(args, **kwargs):
      return subprocess.Popen([sys.executable, '-c', "import sys; sys.stdout.buffer.write(bytes([255,216,255,217]))"], **kwargs)
    owner = CameraSnapshot(spawn=spawn)
    self.assertEqual(owner.capture('wide', permitted=lambda: True), b'\xff\xd8\xff\xd9')
    self.assertEqual(owner.capture('cabin', permitted=lambda: True), b'\xff\xd8\xff\xd9')
    self.assertEqual(self.request_mock.call_count, 2)
    self.assertEqual(self.mark_frame_mock.call_count, 1) # Cabin availability receipt only.
    with self.assertRaises(SnapshotUnavailable):
      owner.capture('wide', permitted=lambda: True)
    calls = 0
    def permitted():
      nonlocal calls
      calls += 1
      return calls < 3
    with self.assertRaises(SnapshotDenied):
      CameraSnapshot(spawn=spawn).capture('wide', permitted=permitted)

  def test_real_nv12_conversion_accepts_only_post_request_fresh_native_frame(self):
    now = 1_000_000_000
    data = np.full(80, 128, dtype=np.uint8)
    frame = SimpleNamespace(width=4, height=4, stride=4, uv_offset=16, data=data)
    class Client:
      valid = False  # Native camerad leaves the extra metadata bit unset.
      timestamp_sof = now - 1
      timestamp_eof = now - 1
      frames = 0
      def connect(self, blocking):
        self.blocking = blocking
        return True
      def recv(self, timeout_ms):
        nonlocal now
        self.frames += 1
        now += 100_000_000
        if self.frames > 1:
          self.timestamp_sof = now - 20_000_000
          self.timestamp_eof = now - 10_000_000
        return frame
    client = Client()
    result = capture('cabin', client_factory=lambda *args: client, clock=lambda: now)
    self.assertFalse(client.blocking)
    self.assertEqual(client.frames, 100)
    image = Image.open(BytesIO(result))
    self.assertEqual(image.size, (4, 4))
    self.assertEqual(image.mode, 'RGB')
    frame.width = 99999
    self.assertIsNone(capture('cabin', client_factory=lambda *args: client, clock=lambda: now))


  def test_native_frames_with_stale_future_or_reversed_timestamps_never_convert(self):
    for fault in ('stale', 'future', 'reversed'):
      with self.subTest(fault=fault):
        now = [1_000_000_000]
        frame = SimpleNamespace(width=4, height=4, stride=4, uv_offset=16,
                                data=np.full(80, 128, dtype=np.uint8))
        class Client:
          valid = False
          timestamp_sof = timestamp_eof = 0
          def connect(self, blocking): return True
          def recv(self, timeout_ms, *, fault=fault, frame=frame, now=now):
            now[0] += 100_000_000
            self.timestamp_sof = now[0] - (600_000_000 if fault == 'stale' else 20_000_000)
            self.timestamp_eof = now[0] + 1 if fault == 'future' else self.timestamp_sof - 1 if fault == 'reversed' else now[0] - 10_000_000
            return frame
        with patch('openpilot.starpilot.galaxy.camera_snapshot_worker.extract_image') as convert:
          self.assertIsNone(capture('cabin', client_factory=lambda *args: Client(), clock=lambda now=now: now[0]))
          convert.assert_not_called()

  def test_only_wide_adds_five_seconds_of_fresh_frames(self):
    for camera, expected_frames in (('narrow', 1), ('wide', 51), ('cabin', 100)):
      with self.subTest(camera=camera):
        now = [1_000_000_000]
        data = np.full(80, 128, dtype=np.uint8)
        frame = SimpleNamespace(width=4, height=4, stride=4, uv_offset=16, data=data)
        class Client:
          frames = 0
          def connect(self, blocking): return True
          def recv(self, timeout_ms, *, now=now, data=data, frame=frame):
            self.frames += 1
            now[0] += 100_000_000
            self.timestamp_sof, self.timestamp_eof = now[0] - 20_000_000, now[0] - 10_000_000
            data[:16] = 0 if self.frames <= 50 else 128
            return frame
        client = Client()
        body = capture(camera, client_factory=lambda *args, client=client: client, clock=lambda now=now: now[0])
        self.assertEqual(client.frames, expected_frames)
        self.assertEqual(float(np.asarray(Image.open(BytesIO(body))).mean()), 0 if camera == 'narrow' else 128)

  def test_wide_settles_after_delayed_connection_and_first_frame(self):
    started = 1_000_000_000
    now = [started]
    frame = SimpleNamespace(width=4, height=4, stride=4, uv_offset=16, data=np.full(80, 128, dtype=np.uint8))
    class Client:
      def connect(self, blocking): return now[0] >= started + 8_900_000_000
      def recv(self, timeout_ms):
        now[0] += 100_000_000
        if now[0] < started + 10_500_000_000:
          return None
        self.timestamp_sof, self.timestamp_eof = now[0] - 20_000_000, now[0] - 10_000_000
        return frame
    def sleep(seconds):
      now[0] += int(seconds * 1e9)
    with patch('openpilot.starpilot.galaxy.camera_snapshot_worker.time.sleep', side_effect=sleep):
      body = capture('wide', client_factory=lambda *args: Client(), clock=lambda: now[0])
    self.assertIsNotNone(body)
    self.assertEqual(now[0] - started, 15_500_000_000)

  def test_wide_rejects_pre_settle_exposure_received_after_boundary(self):
    now = [1_000_000_000]
    frame = SimpleNamespace(width=4, height=4, stride=4, uv_offset=16, data=np.full(80, 128, dtype=np.uint8))
    class Client:
      frames = 0
      def connect(self, blocking): return True
      def recv(self, timeout_ms):
        self.frames += 1
        if self.frames == 1:
          now[0] += 100_000_000
          self.timestamp_sof = now[0] - 20_000_000
          self.ready_after = self.timestamp_sof + 5_000_000_000
        else:
          now[0] = self.ready_after + 200_000_000
          self.timestamp_sof = self.ready_after - (1 if self.frames == 2 else 0)
        self.timestamp_eof = now[0] - 10_000_000
        return frame
    client = Client()
    self.assertIsNotNone(capture('wide', client_factory=lambda *args: client, clock=lambda: now[0]))
    self.assertEqual(client.frames, 3)
    self.assertEqual(client.timestamp_sof, client.ready_after)

  def test_invalid_wide_frames_do_not_start_settling(self):
    for fault in ('missing', 'pre_request', 'stale', 'future', 'reversed'):
      with self.subTest(fault=fault):
        started = 1_000_000_000
        now = [started]
        frame = SimpleNamespace(width=4, height=4, stride=4, uv_offset=16, data=np.full(80, 128, dtype=np.uint8))
        class Client:
          frames = 0
          def connect(self, blocking): return True
          def recv(self, timeout_ms, *, fault=fault, now=now, frame=frame, started=started):
            self.frames += 1
            now[0] += 3_000_000_000 if self.frames == 2 else 100_000_000
            self.timestamp_sof, self.timestamp_eof = now[0] - 20_000_000, now[0] - 10_000_000
            if self.frames == 1:
              if fault == 'missing':
                return None
              if fault == 'pre_request':
                self.timestamp_sof = started
              if fault == 'stale':
                self.timestamp_sof = now[0] - 600_000_000
              if fault == 'future':
                self.timestamp_eof = now[0] + 1
              if fault == 'reversed':
                self.timestamp_eof = self.timestamp_sof - 1
            return frame
        client = Client()
        self.assertIsNotNone(capture('wide', client_factory=lambda *args, client=client: client, clock=lambda now=now: now[0]))
        self.assertEqual(client.frames, 52)
        self.assertEqual(now[0] - started, 8_100_000_000)

  def test_wide_missing_or_late_frames_timeout_inside_owner_and_lease(self):
    for late_frame in (False, True):
      with self.subTest(late_frame=late_frame):
        now = [1_000_000_000]
        frame = SimpleNamespace(width=4, height=4, stride=4, uv_offset=16, data=np.full(80, 128, dtype=np.uint8))
        class Client:
          def connect(self, blocking): return True
          def recv(self, timeout_ms, *, now=now, frame=frame, late_frame=late_frame):
            now[0] += 100_000_000
            self.timestamp_sof, self.timestamp_eof = now[0] - 20_000_000, now[0] - 10_000_000
            return frame if late_frame and now[0] >= 15_000_000_000 else None
        with patch('openpilot.starpilot.galaxy.camera_snapshot_worker.extract_image') as convert:
          self.assertIsNone(capture('wide', client_factory=lambda *args: Client(), clock=lambda now=now: now[0]))
          convert.assert_not_called()
        self.assertEqual(now[0] - 1_000_000_000, 17_000_000_000)
        self.assertLess(17, CAPTURE_TIMEOUT_SECONDS['wide'])
        self.assertLess(CAPTURE_TIMEOUT_SECONDS['wide'] * 1_000_000_000, TTL_NS)

  def test_wide_owner_reaps_reader_on_deadline_or_revocation_during_settling(self):
    for revoke_at in (None, 3):
      with self.subTest(revoke_at=revoke_at):
        now = [0.0]
        class Process:
          stdout = BytesIO()
          killed = waited = False
          def communicate(self, timeout, *, now=now):
            now[0] += timeout
            raise subprocess.TimeoutExpired('snapshot worker', timeout)
          def poll(self): return -9 if self.killed else None
          def kill(self): self.killed = True
          def wait(self): self.waited = True
        process = Process()
        def spawn(args, *, process=process, **kwargs):
          self.assertEqual(args[-1], 'wide')
          return process
        owner = CameraSnapshot(spawn=spawn, clock=lambda now=now: now[0])
        error = SnapshotUnavailable if revoke_at is None else SnapshotDenied
        with self.assertRaises(error):
          owner.capture('wide', permitted=lambda now=now, revoke_at=revoke_at: revoke_at is None or now[0] < revoke_at)
        self.assertAlmostEqual(now[0], 19 if revoke_at is None else revoke_at)
        self.assertTrue(process.killed and process.waited and process.stdout.closed)
        self.assertEqual(CAPTURE_TIMEOUT_SECONDS, {'cabin': 24, 'wide': 19, 'narrow': 12})
