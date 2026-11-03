"""Bounded Galaxy HTTP connections and shared-source shutdown."""

from contextlib import ExitStack
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from functools import lru_cache
import gzip
from ipaddress import IPv4Address, ip_address, ip_network
import json
import mimetypes
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ReverseProxy:
  peer: str
  origin: str

  @classmethod
  def validated(cls, peer, origin):
    try:
      address = ip_address(peer)
      url = urlsplit(origin)
      if (address.is_unspecified or not url.hostname or url.scheme != 'https' or url.username is not None or
          url.password is not None or url.path or url.query or url.fragment or
          origin != f'https://{url.netloc}' or url.port == 0):
        raise ValueError
    except ValueError as error:
      raise ValueError('Enter a literal proxy IP address and an HTTPS origin without a path or trailing slash') from error
    return cls(str(address), origin)

  def matches_peer(self, peer):
    try:
      return ip_address(peer) == ip_address(self.peer)
    except ValueError:
      return False

  def allows(self, peer, host, origin, *, mutation=False):
    return (self.matches_peer(peer) and host == urlsplit(self.origin).netloc and
            (origin == self.origin or origin is None and not mutation))


class _LocalHTTPServer(ThreadingHTTPServer):
  """Bound idle browser connections without blocking other local requests."""

  MAX_CONNECTIONS = 8
  # comma's own services call back over loopback while a browser request waits on them.
  LOOPBACK_CONNECTIONS = 4
  REQUEST_TIMEOUT = 4.0

  def __init__(self, *args, **kwargs):
    self._slots = threading.BoundedSemaphore(self.MAX_CONNECTIONS)
    self._loopback_slots = threading.BoundedSemaphore(self.LOOPBACK_CONNECTIONS)
    self._draining = False
    super().__init__(*args, **kwargs)

  def get_request(self):
    request, address = super().get_request()
    request.settimeout(self.REQUEST_TIMEOUT)
    return request, address

  def loopback(self, request) -> bool:
    if getattr(self, 'remote_transport', False):
      return False
    try:
      return request.getpeername()[0] == request.getsockname()[0] == '127.0.0.1'
    except (OSError, IndexError, TypeError):
      return False

  def _admit(self, request) -> threading.BoundedSemaphore | None:
    """The pool this request got a slot from; loopback overflows into its reserve instead of waiting."""
    if self._draining:
      return None
    if self.loopback(request):
      if self._slots.acquire(blocking=False):
        return self._slots
      return self._loopback_slots if self._loopback_slots.acquire(timeout=self.REQUEST_TIMEOUT) else None
    return self._slots if self._slots.acquire(timeout=self.REQUEST_TIMEOUT) else None

  def process_request_thread(self, request, client_address):
    # Wait on the request's own thread so a full browser pool never blocks accepting.
    slots = self._admit(request)
    if slots is None:
      self.shutdown_request(request)
      return
    try:
      if self._draining:
        self.shutdown_request(request)
      else:
        super().process_request_thread(request, client_address)
    finally:
      slots.release()

  def drain_requests(self, timeout: float) -> bool:
    """Wait for accepted requests before closing their shared data sources."""
    self._draining = True
    deadline = time.monotonic() + timeout
    acquired: list[threading.BoundedSemaphore] = []
    try:
      for slots, count in ((self._slots, self.MAX_CONNECTIONS), (self._loopback_slots, self.LOOPBACK_CONNECTIONS)):
        for _ in range(count):
          if not slots.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return False
          acquired.append(slots)
      return True
    finally:
      for slots in acquired:
        slots.release()

  def service_actions(self):
    super().service_actions()
    maintain = getattr(self, 'maintain_starport', None)
    if maintain is not None:
      try:
        maintain()
      except Exception:
        pass  # Optional housekeeping must never stop serve_forever.

  def server_close(self, *, close_sources: bool = True):
    if not close_sources:
      # Managed shutdown can abandon a stuck daemon request. Its readers must
      # remain alive until that process exits; only retire the listening socket.
      media = getattr(self, 'recording_media_source', None)
      if media is not None:
        media.stop_child()
      super().server_close()
      return
    # Always close every owned source, even if another cleanup raises. The
    # owned sources must retire before their supporting readers.
    with ExitStack() as cleanup:
      cleanup.callback(super().server_close)
      media = getattr(self, 'recording_media_source', None)
      if media is not None:
        cleanup.callback(media.close)
      for name in ('weather_source', 'map_source', 'settings_source', 'vehicle_selection_source', 'toyota_tools_source', 'model_source', 'plots_source',
                   'bluetooth_authority', 'bluetooth_source', 'model_manager_source', 'model_authority', 'layout_authority', 'favorites_source',
                   'sound_authority', 'sound_source', 'software_operations_source', 'drive_stats_authority', 'drive_stats_source',
                   'pairing_authority', 'evidence_source', 'device_state_source', 'navigation_source', 'drive_physical_source', 'notification_source',
                   'starport_setup_source'):
        close = getattr(getattr(self, name, None), 'close', None)
        if callable(close):
          cleanup.callback(close)


LOCAL_NETWORKS = tuple(ip_network(network) for network in ('127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16'))


def direct_local_connection(peer: str, destination: str, headers) -> bool:
  """Passwordless access is limited to direct local sockets, never forwarded requests."""
  proxy_headers = {'forwarded', 'via', 'x-real-ip', 'x-client-ip', 'x-original-forwarded-for', 'cf-connecting-ip', 'true-client-ip'}
  if any(name.lower() in proxy_headers or name.lower().startswith('x-forwarded-') for name in headers):
    return False
  try:
    return all(isinstance(address, IPv4Address) and any(address in network for network in LOCAL_NETWORKS)
               for address in (ip_address(peer), ip_address(destination)))
  except ValueError:
    return False


def install_origin_allowed(peer: str, destination: str, headers, remote_transport: bool = False) -> bool:
  """Large installs are for the tunnel or a real LAN/hotspot client, never the phone Bluetooth bridge (loopback)."""
  if remote_transport:
    return True
  try:
    return not ip_address(peer).is_loopback and direct_local_connection(peer, destination, headers)
  except ValueError:
    return False


def dns_authority_name(host: str, port: int, *, allow_portless=False):
  from openpilot.starpilot.galaxy.local_hostnames import dns_name
  if host.endswith(f':{port}'):
    name = host[:-len(f':{port}')]
  elif allow_portless and ':' not in host:
    name = host
  else:
    return None
  return dns_name(name)


def allowed_authority(host: str, local_address: str, port: int, *, local_hostnames=None, allow_portless=False) -> bool:
  """Match the destination address or its independently discovered LAN identity."""
  if host == f'localhost:{port}' and local_address == '127.0.0.1':
    return True
  if host != f'{local_address}:{port}':
    if local_hostnames is None:
      return False
    canonical = dns_authority_name(host, port, allow_portless=allow_portless)
    return canonical is not None and canonical in local_hostnames.names(local_address)
  try:
    address = ip_address(local_address)
  except ValueError:
    return False
  return isinstance(address, IPv4Address) and not address.is_unspecified


@lru_cache(maxsize=128)
def static_asset(path: str, identity: tuple[int, int, int]):
  body = Path(path).read_bytes()
  content_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
  compressible = content_type.startswith('text/') or content_type in ('application/javascript', 'application/json', 'image/svg+xml')
  packed = gzip.compress(body, compresslevel=5, mtime=0) if compressible and len(body) > 1024 else body
  if len(packed) >= len(body):
    packed = body
  return body, packed, content_type


class _LocalHTTPHandler(BaseHTTPRequestHandler):
  protocol_version = 'HTTP/1.1'

  def parse_request(self):
    parsed = super().parse_request()
    # Rejected writes can leave unread bodies; only bodyless reads are reusable.
    if parsed and (self.command not in ('GET', 'HEAD') or 'Content-Length' in self.headers or 'Transfer-Encoding' in self.headers):
      self.close_connection = True
    return parsed

  def end_headers(self):
    if self.close_connection:
      self.send_header('Connection', 'close')
    super().end_headers()

  def log_message(self, format, *args):  # noqa: A002 - Match BaseHTTPRequestHandler's keyword signature.
    pass

  def handle(self):
    try:
      super().handle()
    except (BrokenPipeError, ConnectionResetError):
      # The browser can retire an in-flight fetch or speculative connection.
      self.close_connection = True

  def respond(self, status, body, content_type='application/json', cookie=None, *, asset_headers=None, cache_control=None):
    self.send_response(status)
    self.send_header('Content-Type', content_type)
    if status != 304:
      self.send_header('Content-Length', str(len(body)))
    self.send_header('Cache-Control', cache_control or ('private, max-age=0, must-revalidate' if asset_headers else 'no-store'))
    for key, value in (asset_headers or {}).items():
      self.send_header(key, value)
    self.send_header('X-Content-Type-Options', 'nosniff')
    self.send_header('Referrer-Policy', 'no-referrer')
    if cookie is not None:
      self.send_header('Set-Cookie', cookie)
    self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; " +
                     "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
    self.end_headers()
    if self.command != 'HEAD':
      self.wfile.write(body)

  def json(self, status, value):
    self.respond(status, json.dumps(value, allow_nan=False).encode())

  def attachment(self, filename, body, content_type):
    """A download the browser saves as ``filename`` rather than displays."""
    self.send_response(200)
    self.send_header('Content-Type', content_type)
    self.send_header('Content-Length', str(len(body)))
    self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
    self.send_header('Cache-Control', 'no-store')
    self.send_header('X-Content-Type-Options', 'nosniff')
    self.send_header('Referrer-Policy', 'no-referrer')
    self.end_headers()
    if self.command != 'HEAD':
      self.wfile.write(body)
