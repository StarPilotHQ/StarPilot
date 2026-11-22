import json
import os
import requests
from urllib3.exceptions import HTTPError

TEST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(TEST_DIR, "../agnos.json")


from openpilot.common.test import OpenpilotTestCase
class TestAgnosUpdater(OpenpilotTestCase):

  def test_manifest(self):
    with open(MANIFEST) as f:
      m = json.load(f)

    for img in m:
      headers = {'Range': 'bytes=0-5', 'Accept-Encoding': 'identity'}
      failures = []
      for url in (img['url'], *img.get('fallback_urls', [])):
        try:
          with requests.get(url, headers=headers, stream=True, timeout=10) as response:
            response.raise_for_status()
            if response.raw.read(6) == b'\xfd7zXZ\x00':
              break
            failures.append('response was not XZ')
        except (requests.RequestException, HTTPError) as exc:
          failures.append(type(exc).__name__)
      else:
        self.fail(f"{img['name']}: no mirror returned XZ ({', '.join(failures)})")
      if not img['sparse']:
        assert img['hash'] == img['hash_raw']
