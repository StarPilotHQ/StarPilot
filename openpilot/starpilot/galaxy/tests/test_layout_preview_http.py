import base64
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from typing import NotRequired, TypedDict

from openpilot.common.params import Params
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.onroad_layout import OnroadLayoutOwner
from openpilot.starpilot.galaxy.remote import RemotePairing
from openpilot.starpilot.galaxy.server import make_remote_server, make_server
from openpilot.starpilot.ui.layout_preview_transport import PreviewService
from openpilot.starpilot.ui.onroad_customization import PARAM_KEY, default_document
from openpilot.starpilot.ui.tests.test_layout_preview_transport import png


class PreviewPayload(TypedDict):
  document: dict
  profile: str
  scene: str
  art: NotRequired[bool]


class TestLayoutPreviewHttp(unittest.TestCase):
  def setUp(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    root = Path(temp.name)
    self.socket_path = root / 'ui.sock'
    self.params = Params(str(root / 'params'))
    self.parked = True
    self.owner = OnroadLayoutOwner(self.params, lambda: self.parked)
    self.pairing = RemotePairing(root / 'pairing')
    self.rendered = []
    self.preview = PreviewService(lambda payload: self.rendered.append(payload) or png(),
                                  lambda: self.parked, self.socket_path, active_profile='large')
    self.preview.start()
    self.addCleanup(self.preview.close)
    self.server = make_server(port=0, owner=GalaxyAccessOwner(root / 'access'), layouts=self.owner, remote_pairing=self.pairing,
                              layout_preview_socket=self.socket_path)
    self.worker = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    self.worker.start()
    self.addCleanup(self.stop)
    self.payload: PreviewPayload = {'document': default_document(), 'profile': 'large', 'scene': 'aol'}

  def stop(self):
    self.server.shutdown()
    self.worker.join(timeout=2)
    self.server.server_close()

  def request(self, path, payload=None, cookie='', *, origin=None, raw=None, server=None, host=None, digest=None):
    port = (server or self.server).server_port
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=4)
    headers = {'Cookie': cookie}
    if digest:
      headers['X-Widget-Digest'] = digest
    if host:
      headers['Host'] = host
    body = raw if raw is not None else json.dumps(payload) if payload is not None else None
    if body is not None:
      headers.update({'Content-Type': 'application/json', 'Origin': origin or f'http://127.0.0.1:{port}'})
    try:
      connection.request('POST' if body is not None else 'GET', path, body=body, headers=headers)
      response = connection.getresponse()
      return response.status, response.read(), dict(response.getheaders())
    finally:
      connection.close()

  def login(self):
    status, _, headers = self.request('/api/auth/session')
    self.assertEqual(status, 200)
    return headers['Set-Cookie'].split(';', 1)[0]

  def preview_with_pump(self, cookie, path='/api/ui/layout/preview', **kwargs):
    outcome = {}
    client = threading.Thread(target=lambda: outcome.setdefault('response', self.request(path, self.payload, cookie, **kwargs)))
    client.start()
    deadline = time.monotonic() + 4
    while client.is_alive() and time.monotonic() < deadline:
      self.preview.poll()
      client.join(0.01)
    self.assertFalse(client.is_alive())
    return outcome['response']

  def test_firestar_widget_sheet_preserves_pixels_and_metadata(self):
    slug = self.pairing.pair(hashlib.sha256(b'preview-password').hexdigest())
    record = self.pairing.read()
    assert record is not None
    cookie = f"galaxy_session={slug}%3A{record['session']}"
    metadata = {'version': 1, 'width': 1, 'height': 1, 'sprites': {'current_speed': {'x': 0, 'y': 0, 'width': 1, 'height': 1}}}
    image = png(1, 1, widget_art=metadata)
    self.preview.renderer = lambda _: image
    self.payload['art'] = True
    with make_remote_server(self.server, port=0) as remote:
      worker = threading.Thread(target=remote.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
      worker.start()
      try:
        status, body, headers = self.preview_with_pump(cookie, f'/{slug}/api/ui/layout/preview', server=remote,
                                                      host='galaxy.firestar.link', origin='https://galaxy.firestar.link')
        repeat = self.preview_with_pump(cookie, f'/{slug}/api/ui/layout/preview', server=remote,
                                        host='galaxy.firestar.link', origin='https://galaxy.firestar.link', digest=headers['X-Widget-Digest'])
        self.assertEqual(repeat[:2], (204, b''))
      finally:
        remote.shutdown()
        worker.join(timeout=2)
    self.assertEqual((status, body), (200, image))
    self.assertEqual(headers['Cache-Control'], 'no-store, no-transform')
    self.assertEqual(json.loads(base64.b64decode(headers['X-Widget-Art'])), metadata)

  def test_live_profile_png_and_no_saved_write(self):
    cookie = self.login()
    status, body, _ = self.request('/api/ui/layout', cookie=cookie)
    self.assertEqual(status, 200)
    self.assertEqual(json.loads(body)['activeProfile'], 'large')
    status, body, headers = self.preview_with_pump(cookie)
    self.assertEqual(status, 200)
    self.assertEqual(headers['Content-Type'], 'image/png')
    self.assertEqual(headers['Cache-Control'], 'no-store')
    self.assertEqual(body, png())
    self.assertEqual(self.rendered, [self.payload])
    self.assertIsNone(self.params.get(PARAM_KEY))
    self.preview.close()
    status, body, _ = self.request('/api/ui/layout', cookie=cookie)
    self.assertEqual(status, 200)
    self.assertIsNone(json.loads(body)['activeProfile'])
    self.assertEqual(self.request('/api/ui/layout/preview', self.payload, cookie)[0], 503)

  def test_auth_shape_origin_and_fresh_parked_guards(self):
    cookie = self.login()
    self.assertEqual(self.request('/api/ui/layout/preview', self.payload)[0], 401)
    self.assertEqual(self.request('/api/ui/layout/preview', self.payload, cookie, origin='http://evil.example')[0], 403)
    self.assertEqual(self.request('/api/ui/layout/preview', {**self.payload, 'extra': 1}, cookie)[0], 400)
    self.assertEqual(self.request('/api/ui/layout/preview', {**self.payload, 'scene': 'unknown'}, cookie)[0], 400)
    self.assertEqual(self.request('/api/ui/layout/preview', {**self.payload, 'art': 'widgets'}, cookie)[0], 400)
    self.assertEqual(self.request('/api/ui/layout/preview', {**self.payload, 'document': {'version': 99}}, cookie)[0], 400)
    self.assertEqual(self.request('/api/ui/layout/preview', cookie=cookie,
                                  raw='{"scene":"aol","scene":"engaged"}')[0], 400)
    self.parked = False
    self.assertEqual(self.request('/api/ui/layout/preview', self.payload, cookie)[0], 403)
    self.assertEqual(self.rendered, [])
    self.parked = True
    self.preview.renderer = lambda payload: (setattr(self, 'parked', False), png())[1]
    self.assertEqual(self.preview_with_pump(cookie)[0], 403)
    self.assertIsNone(self.params.get(PARAM_KEY))

  def test_sidebar_draft_reaches_renderer_without_saving(self):
    cookie = self.login()
    self.payload['document']['developerSidebar'] = False
    self.assertEqual(self.preview_with_pump(cookie)[0], 200)
    self.assertEqual(self.rendered, [self.payload])
    self.assertIsNone(self.params.get('DeveloperSidebar'))
    self.assertIsNone(self.params.get(PARAM_KEY))
    self.payload['document']['developerSidebar'] = 'Off'
    self.assertEqual(self.request('/api/ui/layout/preview', self.payload, cookie)[0], 400)


if __name__ == '__main__':
  unittest.main()
