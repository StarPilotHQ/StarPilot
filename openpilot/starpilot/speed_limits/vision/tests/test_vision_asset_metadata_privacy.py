from pathlib import Path
import unittest


class TestVisionAssetMetadataPrivacy(unittest.TestCase):
  def test_numeric_classifier_metadata(self):
    asset = Path(__file__).resolve().parents[1] / 'assets' / 'speed_limit_us_value_classifier.onnx'
    data = asset.read_bytes()
    for prefix in (b'/Users/', b'/Volumes/', b'/home/', b'/tmp/', b'C:\\Users\\'):
      with self.subTest(prefix=prefix):
        self.assertNotIn(prefix, data)
    self.assertIn(b'Ultralytics', data)
    self.assertIn(b'AGPL-3.0 License (https://ultralytics.com/license)', data)
    self.assertIn(b'https://docs.ultralytics.com', data)


if __name__ == '__main__':
  unittest.main()
