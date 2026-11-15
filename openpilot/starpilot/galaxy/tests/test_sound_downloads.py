from unittest.mock import Mock
import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import wave
import zipfile

from openpilot.starpilot.audio.downloads import SoundDownloads
from openpilot.common.params import Params
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway
from openpilot.starpilot.ui.sounds_owner import SoundsOwner


class SoundDownloadsHttpTest(unittest.TestCase):
  def test_local_install_preserves_selection_and_requires_parked_authority(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      wav = io.BytesIO()
      with wave.open(wav, 'wb') as output:
        output.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        output.writeframes(b'\0\0' * 480)
      archive = io.BytesIO()
      with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('engage.wav', wav.getvalue())
      data = archive.getvalue()
      catalog = root / 'catalog.json'
      catalog.write_text(json.dumps({'version': 1, 'packs': [{
        'id': 'test', 'name': 'Test', 'path': 'theme/Themes/test/sounds.zip',
        'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
      }]}))
      parked = [True]
      downloads = SoundDownloads(parked=lambda: parked[0], root=root / 'packs', catalog_path=catalog,
                                 opener=lambda *args, **kwargs: io.BytesIO(data))
      params = Params(str(root / 'params'))
      params.put('SoundPack', 'stock', block=True)
      context = SimpleNamespace(sample=lambda: AuthorityContext(parked[0], None, None))
      settings = SettingsGateway(params, Mock(wraps=context))
      server = make_server(port=0, owner=GalaxyAccessOwner(root / 'access'), sounds=downloads, settings=settings)
      thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
      thread.start()
      cookie = ['']

      def request(path, payload=None, *, forwarded=False, origin=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        headers = {'Origin': origin or f'http://127.0.0.1:{server.server_port}', 'Content-Type': 'application/json'}
        if cookie[0]:
          headers['Cookie'] = cookie[0]
        if forwarded:
          headers['Forwarded'] = 'for=203.0.113.8'
        try:
          connection.request('GET' if payload is None else 'POST', path,
                             body=None if payload is None else json.dumps(payload), headers=headers)
          response = connection.getresponse()
          if response.getheader('Set-Cookie'):
            cookie[0] = response.getheader('Set-Cookie').split(';', 1)[0]
          return response.status, json.loads(response.read())
        finally:
          connection.close()

      try:
        self.assertEqual(request('/api/auth/session')[0], 200)
        self.assertEqual(request('/api/sounds')[0], 200)
        self.assertEqual(request('/api/sounds', forwarded=True)[0], 503)
        self.assertEqual(request('/api/sounds/download', {'pack': 'test'}, origin='https://unrelated.example')[0], 403)
        parked[0] = False
        self.assertEqual(request('/api/sounds/download', {'pack': 'test'})[0], 409)
        self.assertFalse((root / 'packs/test').exists())
        parked[0] = True
        self.assertEqual(request('/api/sounds/download', {'pack': 'test'})[0], 200)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
          status, snapshot = request('/api/sounds')
          if snapshot['job']['state'] in ('complete', 'failed'):
            break
          time.sleep(0.01)
        self.assertEqual(status, 200)
        self.assertEqual(snapshot['job']['state'], 'complete', snapshot)
        self.assertTrue(snapshot['packs'][0]['installed'])
        self.assertEqual((root / 'packs/test/sounds/engage.wav').read_bytes(), wav.getvalue())
        with patch('openpilot.starpilot.galaxy.settings.SoundsOwner',
                   side_effect=lambda p, parked, **kwargs: SoundsOwner(p, parked, root / 'packs', **kwargs)):
          status, page = request('/api/settings/pages/sounds')
          self.assertEqual(status, 200)
          row = next(item for item in page['rows'] if item['label'] == 'Sound Pack')
          self.assertEqual(row['value'], 'Stock Sounds')
          self.assertIn('test', row['choices'])
        self.assertEqual(Path(params.get_param_path('SoundPack')).read_bytes(), b'stock')
        self.assertEqual(request('/api/sounds/download', {'pack': 'test'})[0], 409)
        self.assertEqual(request('/api/sounds/download', {'pack': 'test', 'url': 'https://unrelated.example'})[0], 400)
        self.assertEqual(request('/api/sounds/cancel', {'job': 'not-current'})[0], 409)
      finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


if __name__ == '__main__':
  unittest.main()
