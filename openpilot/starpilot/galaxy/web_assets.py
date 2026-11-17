"""Keep each browser module graph on one Galaxy build without touching authentication."""

from functools import lru_cache
import hashlib
from pathlib import Path
import re
from urllib.parse import unquote

from openpilot.starpilot.galaxy.http_server import static_asset


@lru_cache(maxsize=4)
def _revision(files):
  digest = hashlib.sha256()
  for relative, path, _identity in files:
    digest.update(relative.encode() + b'\0')
    digest.update(hashlib.sha256(Path(path).read_bytes()).digest())
  return digest.hexdigest()[:24]


class WebAssets:
  def __init__(self, root):
    self.root = Path(root).resolve()
    files = []
    for path in sorted(self.root.rglob('*')):
      if path.is_file() and path.resolve().is_relative_to(self.root):
        info = path.stat()
        files.append((path.relative_to(self.root).as_posix(), str(path), (info.st_ino, info.st_mtime_ns, info.st_size)))
    self.prefix = '/_assets/' + _revision(tuple(files)) + '/'

  def serve(self, handler, path):
    versioned = path.startswith('/_assets/')
    if versioned:
      if not path.startswith(self.prefix):
        handler.json(404, {'error': 'Galaxy was updated. Refresh this page.'})
        return
      relative = path[len(self.prefix):]
      if not relative.startswith(('js/', 'css/', 'vendor/', 'assets/')):
        handler.json(404, {'error': 'Page unavailable'})
        return
      path = '/' + relative
    try:
      target = (self.root / unquote(path).lstrip('/')).resolve()
      if path == '/':
        target = self.root / 'index.html'
      if not target.is_relative_to(self.root) or not target.is_file():
        raise FileNotFoundError
      info = target.stat()
      body, packed, content_type = static_asset(str(target), (info.st_ino, info.st_mtime_ns, info.st_size))
      if target == self.root / 'index.html' and versioned:
        raise FileNotFoundError
      if path == '/':
        # Companion manifest downloads /index.html verbatim. Only browser navigation is rewritten.
        # Manifest and worker URLs stay stable so installed apps and push keep their identity.
        body = re.sub(rb'((?:src|href)="\./)(?=(?:js|css|vendor|assets)/)',
                      lambda match: match[1] + self.prefix.lstrip('/').encode(), body)
        handler.respond(200, body, content_type, cache_control='no-store')
        return
    except (OSError, ValueError):
      handler.json(404, {'error': 'Page unavailable'})
      return
    encodings = [entry.strip() for entry in handler.headers.get('Accept-Encoding', '').lower().split(',')]
    compressed = 'gzip' in encodings and packed is not body
    payload = packed if compressed else body
    etag = '"' + hashlib.sha256(payload).hexdigest() + '"'
    headers = {'ETag': etag, 'Vary': 'Accept-Encoding'}
    if compressed:
      headers['Content-Encoding'] = 'gzip'
    unchanged = handler.headers.get('If-None-Match') == etag
    handler.respond(304 if unchanged else 200, b'' if unchanged else payload, content_type, asset_headers=headers)
