import json
import os
import requests

TEST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(TEST_DIR, "../agnos.json")


from openpilot.common.test import OpenpilotTestCase
class TestAgnosUpdater(OpenpilotTestCase):

  def test_manifest(self):
    with open(MANIFEST) as f:
      m = json.load(f)

    for img in m:
      headers = {'Range': 'bytes=0-5', 'Accept-Encoding': 'identity'}
      with requests.get(img['url'], headers=headers, stream=True, timeout=10) as response:
        response.raise_for_status()
        self.assertEqual(response.raw.read(6), b'\xfd7zXZ\x00', img['name'])
      if not img['sparse']:
        assert img['hash'] == img['hash_raw']
