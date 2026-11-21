import json
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
import zlib

from openpilot.starpilot.ui.layout_preview_transport import (
  PreviewDenied, PreviewService, PreviewUnavailable, request_preview, request_profile, validate_png,
)
from openpilot.starpilot.ui.onroad_customization import default_document


def png(width=2, height=1, *, widget_art=None):
  def chunk(tag, data):
    return struct.pack('!I', len(data)) + tag + data + struct.pack('!I', zlib.crc32(tag + data))
  pixels = b''.join(b'\x00' + b'\x00\x00\x00\xff' * width for _ in range(height))
  return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
          (chunk(b'tEXt', b'starpilot-widget-art\0' + json.dumps(widget_art).encode()) if widget_art is not None else b'') +
          chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


class TestLayoutPreviewTransport(unittest.TestCase):
  def setUp(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    self.path = Path(temp.name) / 'preview.sock'
    self.parked = True
    self.calls = []

    def renderer(payload):
      self.calls.append((time.monotonic(), payload))
      return png()

    self.service = PreviewService(renderer, lambda: self.parked, self.path, active_profile='large')
    self.service.start()
    self.addCleanup(self.service.close)
    self.payload = {'document': default_document(), 'profile': 'large', 'scene': 'engaged'}

  def request_with_pump(self, payload=None):
    result = {}

    def client():
      try:
        result['image'] = request_preview(payload or self.payload, self.path)
      except Exception as error:
        result['error'] = error

    worker = threading.Thread(target=client)
    worker.start()
    deadline = time.monotonic() + 3
    while worker.is_alive() and time.monotonic() < deadline:
      self.service.poll()
      worker.join(0.01)
    self.assertFalse(worker.is_alive())
    return result

  def test_main_thread_render_exact_payload_and_live_profile(self):
    self.assertEqual(request_profile(self.path, timeout=0.5), 'large')
    self.service.set_active_profile('compact')
    self.assertEqual(request_profile(self.path), 'compact')
    result = self.request_with_pump()
    self.assertEqual(result['image'], png())
    self.assertEqual(self.calls[0][1], self.payload)
    self.assertTrue(validate_png(result['image']))

  def test_parked_rechecked_before_and_after_render(self):
    self.parked = False
    result = self.request_with_pump()
    self.assertIsInstance(result['error'], PreviewDenied)
    self.assertEqual(self.calls, [])
    self.parked = True
    self.service.renderer = lambda payload: (setattr(self, 'parked', False), png())[1]
    result = self.request_with_pump()
    self.assertIsInstance(result['error'], PreviewDenied)

  def test_rate_limit_defers_latest_request_then_expires_unpolled_request(self):
    self.assertIn('image', self.request_with_pump())
    self.assertIn('image', self.request_with_pump())
    self.assertGreaterEqual(self.calls[1][0] - self.calls[0][0], 0.49)
    result = {}
    client = threading.Thread(target=lambda: result.setdefault('error', self._unpolled_request()))
    client.start()
    client.join(3)
    self.assertFalse(client.is_alive())
    self.assertIsInstance(result['error'], PreviewUnavailable)
    self.assertFalse(self.service.poll())
    self.assertEqual(len(self.calls), 2)

  def _unpolled_request(self):
    try:
      request_preview(self.payload, self.path)
    except Exception as error:
      return error
    return None

  def test_invalid_request_and_offline_are_bounded(self):
    invalid = {**self.payload, 'scene': 'unknown'}
    with self.assertRaises(ValueError):
      request_preview(invalid, self.path)
    with self.assertRaises(ValueError):
      validate_png(png(5000, 1))
    self.service.close()
    self.assertIsNone(request_profile(self.path))
    with self.assertRaises(PreviewUnavailable):
      request_preview(self.payload, self.path, timeout=0.2)

  def test_second_service_cannot_remove_live_socket(self):
    contender = PreviewService(lambda payload: png(), lambda: True, self.path)
    with self.assertRaises(PreviewUnavailable):
      contender.start()
    contender.close()
    self.assertTrue(self.path.exists())
    deadline = time.monotonic() + 1.5
    profile = None
    while profile is None and time.monotonic() < deadline:
      profile = request_profile(self.path, timeout=0.5)
      time.sleep(0.02)
    self.assertEqual(profile, 'large')

  def test_unexpected_render_error_is_logged_once_and_client_gets_generic_error(self):
    def broken(payload):
      raise RuntimeError('Missing render texture')

    self.service.renderer = broken
    with self.assertLogs('openpilot.starpilot.ui.layout_preview_transport', level='ERROR') as captured:
      first = self.request_with_pump()
      second = self.request_with_pump()
    self.assertIsInstance(first['error'], PreviewUnavailable)
    self.assertIsInstance(second['error'], PreviewUnavailable)
    self.assertEqual(str(first['error']), 'Onroad UI preview is unavailable')
    self.assertEqual(len(captured.output), 1)
    self.assertIn('RuntimeError', captured.output[0])
    self.assertIn('broken', captured.output[0])
    self.assertIn('Missing render texture', captured.output[0])
    self.assertNotIn(str(self.payload), captured.output[0])

  def test_deliberate_unavailable_does_not_log(self):
    def unavailable(payload):
      raise PreviewUnavailable('Graphics context unavailable')

    self.service.renderer = unavailable
    with self.assertNoLogs('openpilot.starpilot.ui.layout_preview_transport', level='ERROR'):
      result = self.request_with_pump()
    self.assertIsInstance(result['error'], PreviewUnavailable)
    self.assertEqual(str(result['error']), 'Graphics context unavailable')


if __name__ == '__main__':
  unittest.main()
