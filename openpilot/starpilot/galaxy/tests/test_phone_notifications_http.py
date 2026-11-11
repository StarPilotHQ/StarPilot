import base64
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.remote import RemotePairing
from openpilot.starpilot.galaxy.server import make_remote_server, make_server
from openpilot.starpilot.system.starport.phone_notifications import PhoneNotifications, current

NOTE = {'source': 'android', 'app': 'com.whatsapp', 'appName': 'WhatsApp', 'sender': 'Alice', 'text': 'On my way',
        'category': 'message', 'ageMs': 0}


class PhoneNotificationsHttpTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    root = Path(temporary.name)
    self.access = GalaxyAccessOwner(root / 'access')
    self.pairing = RemotePairing(root / 'pairing')
    self.notifications = PhoneNotifications(root / 'settings' / 'phone_notifications.json', root / 'shm.json',
                                            boot_id=lambda: 'boot', boot_offset=lambda: 0)
    self.local = make_server(port=0, owner=self.access, remote_pairing=self.pairing, phone_notifications=self.notifications)
    self.remote = make_remote_server(self.local, port=0)
    self.servers = []
    for server in (self.local, self.remote):
      thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
      thread.start()
      self.servers.append((server, thread))
    self.addCleanup(self.close)
    self.local_cookie = self.request()[2]['Set-Cookie'].split(';', 1)[0]
    self.access.configure('password123', lambda: True)
    self.slug = self.pairing.pair(hashlib.sha256(b'password123').hexdigest())
    record = self.pairing.read()
    assert record is not None
    self.remote_cookie = 'galaxy_session=' + base64.urlsafe_b64encode(json.dumps({self.slug: record['session']}).encode()).decode().rstrip('=')

  def close(self):
    for server, thread in reversed(self.servers):
      server.shutdown()
      thread.join(timeout=2)
      server.server_close()

  def request(self, path='/api/auth/session', *, payload=None, remote=False, cookie=None, raw=None):
    server = self.remote if remote else self.local
    host = f'{self.slug}.devices.local' if remote else f'127.0.0.1:{server.server_port}'
    headers = {'Host': host}
    if cookie is not None:
      headers['Cookie'] = cookie
    body = raw if raw is not None else json.dumps(payload) if payload is not None else None
    if body is not None:
      headers.update({'Content-Type': 'application/json', 'Origin': 'https://galaxy.firestar.link' if remote else f'http://{host}'})
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
    try:
      connection.request('POST' if body is not None else 'GET', path, body, headers)
      response = connection.getresponse()
      return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
      connection.close()

  def shown(self):
    return current(path=self.notifications.handoff, boot_id=lambda: 'boot', boot_offset=lambda: 0)

  def test_companion_notification_is_local_only_and_validated(self):
    path = '/api/companion/notification'
    status, body, _ = self.request(path, payload=NOTE, cookie=self.local_cookie)
    self.assertEqual((status, body), (200, {'ok': True, 'accepted': True, 'enabled': True}))
    self.assertEqual(self.shown()['text'], 'On my way')
    self.assertEqual(self.request(path, payload=NOTE, remote=True, cookie=self.remote_cookie)[0], 403)
    self.assertEqual(self.request(path, payload=NOTE)[0], 401)
    status, body, _ = self.request(path, payload={**NOTE, 'extra': True}, cookie=self.local_cookie)
    self.assertEqual((status, body['error']), (400, 'Unexpected notification fields'))
    status, body, _ = self.request(path, payload={**NOTE, 'category': 'email', 'app': 'com.mail'}, cookie=self.local_cookie)
    self.assertEqual((status, body['accepted']), (200, False))

  def test_maximum_length_escaped_payload_fits(self):
    payload = {**NOTE, 'appName': '\U0001F600' * 80, 'sender': '\U0001F600' * 120, 'text': '\U0001F600' * 500, 'app': 'a' * 128}
    raw = json.dumps(payload, ensure_ascii=True)
    self.assertGreater(len(raw), 4096)
    status, body, _ = self.request('/api/companion/notification', raw=raw, cookie=self.local_cookie)
    self.assertEqual((status, body['accepted']), (200, True))

  def test_settings_require_a_session_and_apply_updates(self):
    path = '/api/starport/notifications'
    self.assertEqual(self.request(path)[0], 401)
    self.assertEqual(self.request(path, payload={'enabled': False})[0], 401)
    self.request('/api/companion/notification', payload=NOTE, cookie=self.local_cookie)
    for remote in (False, True):
      with self.subTest(remote=remote):
        cookie = self.remote_cookie if remote else self.local_cookie
        status, body, _ = self.request(path, remote=remote, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(body['apps'][0]['id'], 'com.whatsapp')
        status, body, _ = self.request(path, payload={'hideWhileMoving': remote, 'apps': {'com.whatsapp': not remote}},
                                       remote=remote, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual((body['hideWhileMoving'], body['apps'][0]['allowed'], body['apps'][0]['chosen']), (remote, not remote, True))
    status, body, _ = self.request(path, payload={'apps': {'com.unknown': True}}, cookie=self.local_cookie)
    self.assertEqual((status, body['error']), (400, 'Unknown notification app'))
    self.assertEqual(self.request(path, payload={'enabled': 'no'}, cookie=self.local_cookie)[0], 400)
    self.assertEqual(self.request(path, payload={'enabled': False}, cookie=self.local_cookie)[1]['enabled'], False)
    status, body, _ = self.request('/api/companion/notification', payload=NOTE, cookie=self.local_cookie)
    self.assertEqual(body, {'ok': True, 'accepted': False, 'enabled': False})


  def test_galaxy_test_button_needs_a_session_and_shows_a_toast(self):
    path = '/api/starport/notifications/test'
    self.assertEqual(self.request(path, payload={})[0], 401)
    self.assertEqual(self.request(path, payload={'text': 'x'}, cookie=self.local_cookie)[0], 400)
    for remote in (False, True):
      with self.subTest(remote=remote):
        status, body, _ = self.request(path, payload={}, remote=remote, cookie=self.remote_cookie if remote else self.local_cookie)
        self.assertEqual((status, body['accepted'], body['test']), (200, True, True))
        self.assertEqual(self.shown()['sender'], 'StarPilot test notification')
    self.assertEqual(self.notifications.snapshot()['apps'], [])


  def test_message_text_is_shown_only_on_a_direct_local_connection(self):
    self.request('/api/companion/notification', payload=NOTE, cookie=self.local_cookie)
    local = self.request('/api/starport/notifications', cookie=self.local_cookie)[1]
    self.assertEqual((local['showing']['sender'], local['showing']['text'], local['contentVisible']), ('Alice', 'On my way', True))
    self.assertEqual(local['recent'][0]['outcome'], 'shown')
    remote = self.request('/api/starport/notifications', remote=True, cookie=self.remote_cookie)[1]
    self.assertEqual((remote['showing'].get('sender'), remote['showing'].get('text'), remote['contentVisible']), (None, None, False))
    self.assertEqual(remote['showing']['appName'], 'WhatsApp')


if __name__ == '__main__':
  unittest.main()
