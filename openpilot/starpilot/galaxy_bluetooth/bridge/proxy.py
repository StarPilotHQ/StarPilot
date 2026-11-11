"""Authenticated, bounded bridge to SecretGood's local Galaxy server."""
import base64
from http.cookies import SimpleCookie
import json
import time
import urllib.error
import urllib.parse
import urllib.request

if __package__:
  from .protocol import MAX_BODY  # imported by the comma UI
else:
  from protocol import MAX_BODY  # run as the bridge script

METHODS = {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'}
REQUEST_HEADERS = {'content-type', 'accept'}
RESPONSE_HEADERS = {'content-type'}
# Pairing credentials, the phone uplink and APK downloads are only offered over a
# direct Wi-Fi/LAN session; Bluetooth is never an uplink or media transport.
WIFI_PATHS = ('/api/recordings', '/api/replay', '/api/cameras', '/api/maps/tiles', '/api/navigation/map/tiles',
              '/api/starport/upload', '/api/starport/apk', '/api/phone-internet',
              '/api/companion/uplink', '/api/companion/app.apk',
              '/api/auth/login', '/api/auth/logout', '/api/spotify/upload')


def validate_target(target):
  if not isinstance(target, str) or len(target) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in target):
    raise ValueError('Invalid request path')
  parsed = urllib.parse.urlsplit(target)
  decoded = urllib.parse.unquote(parsed.path)
  if parsed.scheme or parsed.netloc or parsed.fragment or not decoded.startswith('/'):
    raise ValueError('Only relative Galaxy paths are allowed')
  if '\\' in decoded or '//' in decoded or any(p in ('.', '..') for p in decoded.split('/')):
    raise ValueError('Invalid path segments')
  if any(ord(c) < 32 or ord(c) == 127 for c in decoded) or '%' in decoded:
    raise ValueError('Invalid encoded path')
  if not (decoded.startswith('/api/') or decoded == '/data/runtime.json'):
    raise ValueError('Path is outside the Galaxy API')
  return target


def error_response(request_id, status, message):
  return {'id': request_id, 'status': status, 'headers': {'content-type': 'application/json'},
          'body': base64.b64encode(json.dumps({'error': message}).encode()).decode()}


class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self, req, fp, code, msg, headers, newurl):
    return None


class GalaxyProxy:
  def __init__(self, port=8082):
    if type(port) is not int or not 1 <= port <= 65535:
      raise ValueError('Invalid Galaxy port')
    self.port = port
    self.base = f'http://127.0.0.1:{port}'
    self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    self.cookie = ''

  def new_session(self):
    return GalaxyProxy(self.port)

  def send(self, target, method, body, headers):
    request = urllib.request.Request(self.base + target, data=body if body else None, method=method, headers=headers)
    try:
      response = self.opener.open(request, timeout=8)
    except urllib.error.HTTPError as error:
      response = error
    with response:
      content_type = response.headers.get('content-type', '')
      if content_type.startswith(('video/', 'multipart/', 'text/event-stream')):
        return 501, {'content-type': 'application/json'}, json.dumps({'error': 'This feature requires local Wi-Fi.'}).encode(), ''
      chunks, total = [], 0
      deadline = time.monotonic() + 10
      while total <= MAX_BODY:
        if time.monotonic() > deadline:
          raise TimeoutError('Galaxy response deadline exceeded')
        chunk = response.read1(min(65536, MAX_BODY + 1 - total))
        if not chunk:
          break
        chunks.append(chunk)
        total += len(chunk)
      if total > MAX_BODY:
        raise ValueError('Galaxy response exceeds the 1 MiB Bluetooth limit')
      if 300 <= (response.status or 0) < 400:
        raise ValueError('Galaxy redirects are not supported')
      headers = {k.lower(): v for k, v in response.headers.items() if k.lower() in RESPONSE_HEADERS}
      return response.status, headers, b''.join(chunks), response.headers.get('Set-Cookie', '')

  def authenticate(self):
    # This cookie belongs to one encrypted BLE handshake. Never accept a
    # browser-supplied cookie or expose it outside this loopback connection.
    status, _, raw, cookie = self.send('/api/auth/session', 'GET', b'', {'Cookie': self.cookie} if self.cookie else {})
    auth = json.loads(raw)
    parsed = SimpleCookie()
    parsed.load(cookie)
    token = parsed.get('galaxy_session')
    if status != 200 or auth.get('authenticated') is not True or auth.get('localAccess') is not True or token is None or not token.value:
      self.cookie = ''
      raise ValueError('Local Galaxy session is unavailable')
    self.cookie = 'galaxy_session=' + token.value

  def handle(self, request):
    request_id = str(request.get('id', ''))
    try:
      if request.get('path') == '/_bridge/health' and request.get('method') == 'GET':
        health = {'protocol': 1, 'transport': 'bluetooth', 'readStream': True, 'notificationStream': True, 'notificationBond': True,
                  'galaxy': 'secretgood'}
        return {'id': request_id, 'status': 200, 'headers': {'content-type': 'application/json'},
                'body': base64.b64encode(json.dumps(health).encode()).decode()}
      target = validate_target(request.get('path'))
      path = urllib.parse.unquote(urllib.parse.urlsplit(target).path)
      if any(path == prefix or path.startswith(prefix + '/') for prefix in WIFI_PATHS):
        return error_response(request_id, 501, 'This feature requires local Wi-Fi. Connect your phone and comma to the same network.')
      method = request.get('method', 'GET')
      if method not in METHODS:
        raise ValueError('Unsupported HTTP method')
      body = base64.b64decode(request.get('body', ''), validate=True)
      if len(body) > MAX_BODY:
        return error_response(request_id, 413, 'Upload exceeds the 1 MiB Bluetooth limit')
      supplied = request.get('headers', {})
      if not isinstance(supplied, dict):
        raise ValueError('Invalid headers')
      headers = {str(k): str(v) for k, v in supplied.items() if str(k).lower() in REQUEST_HEADERS}
      if any('\r' in v or '\n' in v for v in headers.values()):
        raise ValueError('Invalid header value')
      if not self.cookie:
        self.authenticate()
      if method not in ('GET', 'HEAD'):
        headers['Origin'] = self.base
        headers['Content-Type'] = 'application/json'
      for attempt in range(2):
        headers['Cookie'] = self.cookie
        status, response_headers, data, _ = self.send(target, method, body, headers)
        # Only the explicit pre-execution authentication rejection can
        # be retried. Never replay a mutation after a network failure.
        if status == 401 and attempt == 0:
          try:
            expired = json.loads(data).get('error') == 'Refresh the local Galaxy session'
          except (ValueError, AttributeError):
            expired = False
          if expired:
            self.authenticate()
            continue
        return {'id': request_id, 'status': status, 'headers': response_headers,
                'body': base64.b64encode(data).decode()}
    except (ValueError, TypeError, KeyError) as error:
      return error_response(request_id, 400, str(error))
    except Exception:
      return error_response(request_id, 502, 'Local Galaxy did not answer. Reconnect and check the outcome before retrying a change.')
