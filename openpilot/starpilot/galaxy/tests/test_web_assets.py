from contextlib import closing
import gzip
import hashlib
import http.client
import re
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urljoin, urlsplit

from openpilot.starpilot.galaxy.http_server import _LocalHTTPHandler, _LocalHTTPServer
from openpilot.starpilot.galaxy.web_assets import WebAssets
from openpilot.starpilot.galaxy.web_manifest import web_manifest


class TestWebAssets(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name) / 'web'
    (self.root / 'js').mkdir(parents=True)
    (self.root / 'css').mkdir()
    (self.root / 'js/boot.js').write_text('import "./app.js";\n' + '// shell\n' * 300)
    (self.root / 'js/app.js').write_text('export const build = "old";\n')
    (self.root / 'css/galaxy.css').write_text('body { color: purple }')
    (self.root / 'manifest.webmanifest').write_text('{"id":"./","start_url":"./","scope":"./"}')
    (self.root / 'index.html').write_text('<link rel="manifest" href="./manifest.webmanifest">\n' +
                                        '<link rel="stylesheet" href="./css/galaxy.css">\n' +
                                        '<script type="module" src="./js/boot.js"></script>')
    self.assets = WebAssets(self.root)
    assets = self.assets

    class Handler(_LocalHTTPHandler):
      def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith('/ExistingGalaxy01/'):
          path = path[len('/ExistingGalaxy01'):]
        assets.serve(self, path)

      do_HEAD = do_GET

    self.server = _LocalHTTPServer(('127.0.0.1', 0), Handler)
    self.worker = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    self.worker.start()
    self.addCleanup(self.close)

  def close(self):
    self.server.shutdown()
    self.worker.join(2)
    self.server.server_close()

  def request(self, path, headers=None, method='GET'):
    with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)) as connection:
      connection.request(method, path, headers=headers or {})
      response = connection.getresponse()
      return response.status, dict(response.getheaders()), response.read()

  def test_html_refreshes_graph_without_touching_pairing_or_manifest_identity(self):
    for cookie in ('galaxy_session=ExistingGalaxy01%3A' + 'a' * 64, 'galaxy_session=multi-device-placeholder'):
      status, headers, body = self.request('/ExistingGalaxy01/', {'Cookie': cookie, 'If-None-Match': '"old"'})
      self.assertEqual(status, 200)
      self.assertEqual(headers['Cache-Control'], 'no-store')
      self.assertNotIn('Set-Cookie', headers)
      self.assertNotIn('Clear-Site-Data', headers)
      self.assertIn(b'href="./manifest.webmanifest"', body)
      self.assertIn(('src=".' + self.assets.prefix + 'js/boot.js"').encode(), body)
      self.assertIn(('href=".' + self.assets.prefix + 'css/galaxy.css"').encode(), body)
      boot = urljoin('https://galaxy.example/ExistingGalaxy01/', '.' + self.assets.prefix + 'js/boot.js')
      app = urljoin(boot, './app.js')
      self.assertEqual(self.request(urlsplit(app).path)[2], b'export const build = "old";\n')

  def test_changed_transitive_module_changes_all_asset_urls(self):
    first = self.assets.prefix
    (self.root / 'js/app.js').write_text('export const build = "new";\n')
    fresh = WebAssets(self.root)
    self.assertNotEqual(first, fresh.prefix)
    self.assets.prefix = fresh.prefix
    self.assertEqual(self.request(first + 'js/app.js')[0], 404)
    self.assertEqual(self.request(fresh.prefix + 'js/app.js')[2], b'export const build = "new";\n')
    self.assertEqual(WebAssets(self.root).prefix, fresh.prefix)

  def test_etag_gzip_head_and_unversioned_compatibility(self):
    path = self.assets.prefix + 'js/boot.js'
    status, headers, packed = self.request(path, {'Accept-Encoding': 'gzip'})
    self.assertEqual(status, 200)
    self.assertEqual(gzip.decompress(packed), (self.root / 'js/boot.js').read_bytes())
    self.assertEqual(headers['Cache-Control'], 'private, max-age=0, must-revalidate')
    self.assertEqual(headers['Vary'], 'Accept-Encoding')
    status, _, body = self.request(path, {'Accept-Encoding': 'gzip', 'If-None-Match': headers['ETag']})
    self.assertEqual((status, body), (304, b''))
    self.assertEqual(self.request(path, method='HEAD')[2], b'')
    self.assertEqual(self.request('/js/app.js')[0], 200)
    self.assertEqual(self.request('/index.html')[2], (self.root / 'index.html').read_bytes())
    self.assertEqual(self.request('/ExistingGalaxy01/index.html')[2], (self.root / 'index.html').read_bytes())
    self.assertEqual(self.request('/manifest.webmanifest')[2], (self.root / 'manifest.webmanifest').read_bytes())

  def test_shipped_module_graph_resolves_under_one_version(self):
    assets = WebAssets(Path(__file__).resolve().parents[1] / 'web')

    class Capture:
      headers = {}

      def respond(self, status, body, *_args, **_kwargs):
        self.status, self.body = status, body

      def json(self, status, _value):
        self.status, self.body = status, b''

    handler = Capture()
    assets.serve(handler, '/')
    document = handler.body.decode()
    entries = re.findall(r'(?:src|href)="(\./_assets/[^" ]+)"', document)
    self.assertGreater(len(entries), 4)
    pending = [urljoin('http://localhost/', path) for path in entries]
    visited = set()
    while pending:
      url = pending.pop()
      if url in visited:
        continue
      visited.add(url)
      path = urlsplit(url).path
      self.assertTrue(path.startswith(assets.prefix))
      assets.serve(handler, path)
      self.assertEqual(handler.status, 200, url)
      if path.endswith('.js'):
        imports = re.findall(r"(?:from\s+|import\s*\(\s*|import\s+)[\"'](\.{1,2}/[^\"']+)[\"']", handler.body.decode())
        pending.extend(urljoin(url, value) for value in imports)
    self.assertGreater(len(visited), 80)
    # Native companion apps verify every unversioned download against this manifest.
    for entry in web_manifest(assets.root)['files']:
      assets.serve(handler, '/' + entry['path'])
      self.assertEqual(handler.status, 200, entry['path'])
      self.assertEqual(len(handler.body), entry['size'], entry['path'])
      self.assertEqual(hashlib.sha256(handler.body).hexdigest(), entry['sha256'], entry['path'])

  def test_version_prefix_never_grants_api_or_filesystem_access(self):
    (self.root.parent / 'private').write_text('not public')
    for relative in ('api/auth/session', 'index.html', 'js/../../private', 'js/%2e%2e/%2e%2e/private'):
      self.assertEqual(self.request(self.assets.prefix + relative)[0], 404)
    self.assertEqual(self.request('/../private')[0], 404)
    self.assertEqual(self.request('/js/missing.js')[0], 404)


if __name__ == '__main__':
  unittest.main()
