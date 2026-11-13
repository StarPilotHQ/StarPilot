"""Persistent read connections must never reuse an unread body or partial response."""

import http.client
from contextlib import closing
import os
import socket
import threading
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import pytest

from openpilot.starpilot.galaxy.http_server import _LocalHTTPHandler, _LocalHTTPServer
from openpilot.starpilot.galaxy.replay_http import ReplayHTTP
from openpilot.starpilot.galaxy.recording_http import send_recording


@pytest.fixture
def server(tmp_path, monkeypatch):
  session, client = '1' * 32, '2' * 32
  def ram_session(session_id):
    return tmp_path / 'ram' / ('msgq_replay-' + session_id[:24]) / session_id
  monkeypatch.setattr('openpilot.starpilot.replay.media.web_media_dir', ram_session)
  directory = ram_session(session) / 'media' / '1'
  directory.mkdir(parents=True)
  media = directory / 'init.mp4'
  media.write_bytes(b'x' * (65536 + 5))
  state: dict[str, Any] = {'requests': [], 'chunks': 0, 'revoke_after': None, 'expire_session': False, 'expired': False,
                           'recording_checks': 0, 'change_recording': False}

  def owner(payload):
    if payload['op'] == 'media':
      result = {'available': True, 'epoch': 1, 'sequence': 0, 'length': media.stat().st_size, 'mediaPath': str(media)}
    else:
      state['chunks'] += 1
      result = {'owned': state['revoke_after'] is None or state['chunks'] <= state['revoke_after'],
                'offroad': True, 'mediaReady': True, 'state': 'playing', 'sessionId': session, 'epoch': 1}
      state['expired'] = state['expire_session'] and not result['owned']
    return {'ok': True, 'result': result}

  api = ReplayHTTP(owner, tmp_path)

  class Handler(_LocalHTTPHandler):
    def settings_session(self):
      return None if state['expired'] else ('test', b'generation')

    def require_session(self):
      return True

    def authenticated(self):
      return True

    def do_GET(self):
      state['requests'].append(self.path)
      path = urlsplit(self.path).path
      if path == '/api/replay/media':
        api.get(self, path)
      elif path == '/recording':
        def current():
          state['recording_checks'] += 1
          return not state['change_recording'] or state['recording_checks'] <= 2
        fd = os.open(media, os.O_RDONLY)
        send_recording(self, SimpleNamespace(size=media.stat().st_size, video_fd=fd,
                                            source=SimpleNamespace(current=current), close=lambda: os.close(fd)))
      else:
        self.respond(304 if path == '/unchanged' else 200, b'' if path == '/unchanged' else b'ok')

    do_HEAD = do_GET

    def do_POST(self):
      state['requests'].append(self.path)
      self.json(413, {'error': 'Rejected before reading the body'})

  with _LocalHTTPServer(('127.0.0.1', 0), Handler) as http:
    services: set[int] = set()
    monkeypatch.setattr(http, 'loopback', lambda request: request.getpeername()[1] in services)
    http.services = services
    thread = threading.Thread(target=http.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
      yield http, state, f'/api/replay/media?sessionId={session}&clientId={client}&epoch=1&sequence=0'
    finally:
      http.shutdown()
      thread.join(2)


def test_get_head_and_media_reuse_one_connection(server):
  running, _, media = server
  with closing(http.client.HTTPConnection('127.0.0.1', running.server_port, timeout=2)) as connection:
    connection.request('GET', media)
    response = connection.getresponse()
    assert response.version == 11 and response.read() == b'x' * (65536 + 5)
    opened = connection.sock
    assert opened is not None
    for method, path in [('HEAD', media), ('GET', '/recording'), ('GET', '/unchanged'), ('GET', '/next')]:
      connection.request(method, path)
      response = connection.getresponse()
      assert response.read() == (b'x' * (65536 + 5) if path == '/recording' else b'ok' if path == '/next' else b'')
      if path == '/unchanged':
        assert response.getheader('Content-Length') is None
      assert connection.sock is opened


@pytest.mark.parametrize('method,headers,body', [
  ('POST', 'Content-Length: 4', b'junk'),
  ('GET', 'Content-Length: 4', b'junk'),
  ('GET', 'Transfer-Encoding: chunked', b'0\r\n\r\n'),
])
def test_unread_request_body_closes_connection(server, method, headers, body):
  running, state, _ = server
  with socket.create_connection(('127.0.0.1', running.server_port), timeout=2) as connection:
    connection.sendall(f'{method} /first HTTP/1.1\r\nHost: localhost\r\n{headers}\r\n\r\n'.encode() + body +
                       b'GET /second HTTP/1.1\r\nHost: localhost\r\n\r\n')
    response = http.client.HTTPResponse(connection)
    response.begin()
    response.read()
    assert response.getheader('Connection') == 'close'
    assert state['requests'] == ['/first']


@pytest.mark.parametrize('allowed_chunks', [0, 1])
@pytest.mark.parametrize('expire_session', [False, True])
def test_revoked_media_closes_partial_response(server, allowed_chunks, expire_session):
  running, state, media = server
  state['revoke_after'] = allowed_chunks
  state['expire_session'] = expire_session
  with closing(http.client.HTTPConnection('127.0.0.1', running.server_port, timeout=2)) as connection:
    connection.request('GET', media)
    response = connection.getresponse()
    assert response.status == 200
    with pytest.raises(http.client.IncompleteRead) as receipt:
      response.read()
    assert receipt.value.partial == b'x' * (65536 * allowed_chunks)


def test_changed_recording_closes_partial_response(server):
  running, state, _ = server
  state['change_recording'] = True
  with closing(http.client.HTTPConnection('127.0.0.1', running.server_port, timeout=2)) as connection:
    connection.request('GET', '/recording')
    response = connection.getresponse()
    with pytest.raises(http.client.IncompleteRead) as receipt:
      response.read()
    assert receipt.value.partial == b'x' * 65536


def test_idle_persistent_connections_release_capacity(server):
  running, _, _ = server
  running.REQUEST_TIMEOUT = .15
  held = []
  try:
    for _ in range(running.MAX_CONNECTIONS):
      connection = http.client.HTTPConnection('127.0.0.1', running.server_port, timeout=2)
      held.append(connection)
      connection.request('GET', '/')
      assert connection.getresponse().read() == b'ok'
    with closing(http.client.HTTPConnection('127.0.0.1', running.server_port, timeout=2)) as waiting:
      waiting.request('GET', '/after-idle')
      assert waiting.getresponse().read() == b'ok'
  finally:
    for connection in held:
      connection.close()


def test_full_browser_pool_still_serves_comma_services(server):
  running, _, _ = server
  held = []
  try:
    for _ in range(running.MAX_CONNECTIONS + 2):
      held.append(socket.create_connection(('127.0.0.1', running.server_port), timeout=2))
    service = socket.socket()
    service.bind(('127.0.0.1', 0))
    running.services.add(service.getsockname()[1])
    service.settimeout(1)
    service.connect(('127.0.0.1', running.server_port))
    with closing(service):
      service.sendall(b'GET /service HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
      assert service.recv(64).startswith(b'HTTP/1.1 200')
  finally:
    for connection in held:
      connection.close()


def test_drain_turns_away_requests_still_waiting_for_capacity(server, monkeypatch):
  running, state, _ = server
  held = []
  try:
    # A completed response proves each keepalive socket owns a pool slot.
    for _ in range(running.MAX_CONNECTIONS):
      connection = http.client.HTTPConnection('127.0.0.1', running.server_port, timeout=2)
      held.append(connection)
      connection.request('GET', '/held')
      assert connection.getresponse().read() == b'ok'
    waiting_for_capacity = threading.Event()
    original_admit = running._admit
    def admit(request):
      waiting_for_capacity.set()
      return original_admit(request)
    monkeypatch.setattr(running, '_admit', admit)
    with closing(socket.create_connection(('127.0.0.1', running.server_port), timeout=2)) as waiting:
      waiting.sendall(b'GET /late HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n')
      assert waiting_for_capacity.wait(1)
      drained: list[bool] = []
      drain = threading.Thread(target=lambda: drained.append(running.drain_requests(2)))
      drain.start()
      # Starting a thread does not guarantee it runs before capacity is released.
      deadline = time.monotonic() + 1
      while not running._draining and time.monotonic() < deadline:
        time.sleep(.001)
      assert running._draining
      for connection in held:
        connection.close()
      held.clear()
      drain.join(3)
      assert drained == [True]
      try:
        assert waiting.recv(64) == b''
      except ConnectionResetError:
        pass
    assert '/late' not in state['requests']
  finally:
    for connection in held:
      connection.close()
