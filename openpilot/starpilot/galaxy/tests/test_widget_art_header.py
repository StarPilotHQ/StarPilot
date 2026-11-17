import base64
import json
import struct
import unittest
import zlib

from openpilot.starpilot.galaxy.batch_routes import widget_art_header


def chunk(kind, body):
  return struct.pack('!I', len(body)) + kind + body + struct.pack('!I', zlib.crc32(kind + body))


class WidgetArtHeaderTest(unittest.TestCase):
  def png(self, text):
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', 1, 1, 8, 6, 0, 0, 0)) + text + chunk(b'IEND', b'')

  def test_sheet_is_copied_into_header(self):
    meta = {'version': 1, 'width': 1, 'height': 1, 'sprites': {}}
    image = self.png(chunk(b'tEXt', b'starpilot-widget-art\0' + json.dumps(meta).encode()))
    self.assertEqual(json.loads(base64.b64decode(widget_art_header(image))), meta)

  def test_missing_or_oversized_sheet_gives_no_header(self):
    self.assertEqual(widget_art_header(self.png(b'')), '')
    self.assertEqual(widget_art_header(self.png(chunk(b'tEXt', b'starpilot-widget-art\0' + b'x' * 9000))), '')


if __name__ == '__main__':
  unittest.main()
