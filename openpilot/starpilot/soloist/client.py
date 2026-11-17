"""One persistent connection to Soloist's loopback WebSocket API, normalized into small snapshots.

Soloist's API has no authentication, so only this owner talks to it; Galaxy and the UI talk to the
supervisor's control socket instead. Every vendor field is treated as untrusted display input.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeGuard
from urllib.parse import urlsplit

MAX_FRAME = 256 * 1024
MAX_TEXT = 200
ARTWORK_HOSTS = ('i.scdn.co', 'mosaic.scdn.co', 'image-cdn-ak.spotifycdn.com', 'image-cdn-fa.spotifycdn.com')
STATUSES = ('idle', 'playing', 'paused', 'buffering')
REPEATS = ('off', 'context', 'track')
ACTIONS = ('play', 'pause', 'skip_next', 'skip_prev', 'seek', 'shuffle', 'set_repeat')
AUTH_POLL_SECONDS = 15.0
AUTH_TIMEOUT_SECONDS = 45.0


def _text(value: Any) -> str:
  return ''.join(ch for ch in value if ch.isprintable())[:MAX_TEXT] if isinstance(value, str) else ''


def _name(entity: Any) -> str:
  return _text(((entity or {}).get('decorations') or {}).get('identity', {}).get('name')) if isinstance(entity, dict) else ''


def artwork_url(value: Any) -> str:
  if not isinstance(value, str) or len(value) > 512:
    return ''
  try:
    parts = urlsplit(value)
    ok = parts.scheme == 'https' and parts.hostname in ARTWORK_HOSTS and not parts.username and parts.port is None
  except ValueError:
    return ''
  return value if ok else ''


def _artwork(entity: dict) -> str:
  covers = ((entity.get('decorations') or {}).get('visual_identity') or {}).get('cover') or []
  ranked = {'default': 0, 'large': 1, 'small': 2, 'xlarge': 3}
  for cover in sorted((c for c in covers if isinstance(c, dict)), key=lambda c: ranked.get(c.get('size'), 9)):
    if url := artwork_url(cover.get('url')):
      return url
  return ''


def _number(value: Any) -> TypeGuard[int | float]:
  return type(value) in (int, float)


def track(entity: Any) -> dict | None:
  if not isinstance(entity, dict) or not entity.get('uri') and not _name(entity):
    return None
  decorations = entity.get('decorations') or {}
  creators = [_name((c or {}).get('entity')) for c in (decorations.get('creators') or [])[:8] if isinstance(c, dict)]
  duration = (decorations.get('playback') or {}).get('duration_ms')
  kind = entity.get('entity_type') if entity.get('entity_type') in ('track', 'episode', 'ad') else 'unknown'
  return {'title': _name(entity), 'artists': ', '.join(c for c in creators if c)[:MAX_TEXT],
          'album': _name((decorations.get('parent') or {}).get('entity')), 'artwork': _artwork(entity), 'kind': kind,
          'durationMs': int(duration) if _number(duration) and 0 <= duration < 86400000 else None}


def position(value: Any) -> dict | None:
  if not isinstance(value, dict):
    return None
  ms, at, speed = value.get('position_ms'), value.get('timestamp_ms'), value.get('speed')
  if not all(type(x) in (int, float) for x in (ms, at, speed)) or ms < 0 or not 0 <= speed <= 4:
    return None
  return {'ms': int(ms), 'at': int(at), 'speed': float(speed)}


class PlayerState:
  """Applies vendor events to one bounded snapshot. Not thread-safe; the client serializes access."""

  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self.value: dict[str, Any] = {'connected': False, 'loggedIn': False, 'active': False, 'deviceName': '', 'status': 'idle', 'track': None,
                  'position': None, 'volume': None, 'shuffle': False, 'repeat': 'off', 'actions': [], 'updated': 0.0}

  def apply(self, message: dict) -> None:
    kind = message.get('type')
    v = self.value
    if kind == 'auth_state':
      v['loggedIn'] = message.get('logged_in') is True
      v['active'] = message.get('is_active') is True
      v['deviceName'] = _text(message.get('device_name'))
      if not v['loggedIn']:
        v.update(status='idle', track=None, position=None, actions=[])
    elif kind == 'playback_state':
      self._status(message.get('status'))
      v['track'] = track(message.get('item'))
      v['position'] = position(message.get('position'))
      self._volume(message.get('volume'))
      v['active'] = message.get('is_active') is True
      self._options(message.get('options'))
      actions = message.get('available_actions')
      v['actions'] = sorted(a for a in actions if a in ACTIONS) if isinstance(actions, dict) else []
    elif kind == 'track_changed':
      v['track'] = track(message.get('item'))
    elif kind == 'playback_changed':
      self._status(message.get('status'))
    elif kind == 'volume_changed':
      self._volume(message.get('volume'))
    elif kind == 'device_changed':
      v['active'] = message.get('is_active') is True
      v['deviceName'] = _text(message.get('device_name')) or v['deviceName']
    elif kind == 'options_changed':
      self._options(message.get('options'))
    elif kind == 'position_sync':
      v['position'] = position(message.get('position'))
    else:
      return
    v['updated'] = time.monotonic()

  def _status(self, status: Any) -> None:
    if status in STATUSES:
      self.value['status'] = status

  def _volume(self, volume: Any) -> None:
    if type(volume) is int and 0 <= volume <= 100:
      self.value['volume'] = volume

  def _options(self, options: Any) -> None:
    if isinstance(options, dict):
      self.value['shuffle'] = options.get('shuffle') is True
      self.value['repeat'] = options.get('repeat') if options.get('repeat') in REPEATS else 'off'


def commands(action: str, args: dict) -> list[dict]:
  """Translate one validated StarPilot control into Soloist commands. Raises ValueError otherwise."""
  simple = {'play': 'play', 'pause': 'pause', 'next': 'skip_next', 'previous': 'skip_prev', 'activate': 'activate',
            'deactivate': 'deactivate'}
  if action in simple and not args:
    return [{'type': 'command', 'command': simple[action]}]
  if action == 'seek' and set(args) == {'positionMs'} and type(args['positionMs']) is int and 0 <= args['positionMs'] < 86400000:
    return [{'type': 'command', 'command': 'seek', 'position_ms': args['positionMs']}]
  if action == 'volume' and set(args) == {'volume'} and type(args['volume']) is int and 0 <= args['volume'] <= 100:
    return [{'type': 'command', 'command': 'set_volume', 'volume': args['volume']}]
  if action == 'shuffle' and set(args) == {'enabled'} and type(args['enabled']) is bool:
    return [{'type': 'command', 'command': 'set_shuffle', 'enabled': args['enabled']}]
  if action == 'repeat' and set(args) == {'mode'} and args['mode'] in REPEATS:
    track_on, context_on = args['mode'] == 'track', args['mode'] == 'context'
    order = [('set_repeat_context', False), ('set_repeat_track', True)] if track_on else \
            [('set_repeat_track', False), ('set_repeat_context', context_on)]
    return [{'type': 'command', 'command': name, 'enabled': enabled} for name, enabled in order]
  raise ValueError('Unsupported Spotify control')


def endpoint(data_dir: Path) -> str | None:
  try:
    address = (data_dir / 'ws.addr').read_text().strip()
    port = int((data_dir / 'ws.port').read_text().strip())
  except (OSError, ValueError):
    return None
  return f'ws://127.0.0.1:{port}' if address == '127.0.0.1' and 0 < port < 65536 else None


def _connect(url: str):
  import websocket  # websocket-client, present in the device venv
  return websocket.create_connection(url, timeout=5, enable_multithread=True)


class PlayerClient:
  def __init__(self, data_dir: Path, *, connect: Callable[[str], Any] = _connect, on_change: Callable[[], None] = lambda: None,
               clock: Callable[[], float] = time.monotonic):
    self.data_dir, self.connect, self.on_change = data_dir, connect, on_change
    self.clock = clock
    self.state = PlayerState()
    self.lock = threading.Lock()
    self.outbox: queue.Queue[dict] = queue.Queue(maxsize=16)
    self.stop_event = threading.Event()
    self.thread: threading.Thread | None = None
    self.last_error = ''

  def snapshot(self) -> dict:
    with self.lock:
      value = dict(self.state.value)
    return value

  def start(self) -> None:
    if self.thread is None or not self.thread.is_alive():
      self.stop_event.clear()
      self.thread = threading.Thread(target=self._loop, name='soloist_ws', daemon=True)
      self.thread.start()

  def stop(self) -> None:
    self.stop_event.set()
    if self.thread is not None:
      self.thread.join(timeout=3)
    with self.lock:
      self.state.reset()

  def send(self, action: str, args: dict) -> None:
    for command in commands(action, args):
      try:
        self.outbox.put_nowait(command)
      except queue.Full as error:
        raise RuntimeError('Spotify is busy; try again') from error

  def _loop(self) -> None:
    delay = 0.5
    while not self.stop_event.is_set():
      url = endpoint(self.data_dir)
      if url is None:
        self.stop_event.wait(0.5)
        continue
      try:
        connection = self.connect(url)
      except Exception as error:
        self.last_error = type(error).__name__
        self.stop_event.wait(delay)
        delay = min(delay * 2, 10.0)
        continue
      delay = 0.5
      try:
        self._session(connection)
      except Exception as error:
        self.last_error = type(error).__name__
      finally:
        try:
          connection.close()
        except Exception:
          pass
        with self.lock:
          self.state.value['connected'] = False
        self.on_change()

  def _session(self, connection) -> None:
    connection.settimeout(0.25)
    with self.lock:
      self.state.reset()
    while not self.outbox.empty():  # never replay commands from before a reconnect
      self.outbox.get_nowait()
    connection.send(json.dumps({'type': 'command', 'command': 'get_auth_state'}))
    pending_auth = self.clock()
    next_auth = pending_auth + AUTH_POLL_SECONDS
    asked_state = False
    while not self.stop_event.is_set():
      now = self.clock()
      if pending_auth is not None and now - pending_auth >= AUTH_TIMEOUT_SECONDS:
        raise TimeoutError('Soloist did not answer its local health check')
      if pending_auth is None and now >= next_auth:
        connection.send(json.dumps({'type': 'command', 'command': 'get_auth_state'}))
        pending_auth = now
        next_auth = now + AUTH_POLL_SECONDS
      try:
        while True:
          connection.send(json.dumps(self.outbox.get_nowait()))
      except queue.Empty:
        pass
      try:
        frame = connection.recv()
      except Exception as error:
        if type(error).__name__ in ('WebSocketTimeoutException', 'TimeoutError', 'timeout'):
          continue
        raise
      if not frame:
        return
      if len(frame) > MAX_FRAME:
        continue
      try:
        message = json.loads(frame)
      except ValueError:
        continue
      if not isinstance(message, dict):
        continue
      if message.get('type') == 'auth_state':
        pending_auth = None
      with self.lock:
        self.state.apply(message)
        if message.get('type') == 'auth_state':
          self.state.value['connected'] = True
        logged_in = self.state.value['loggedIn']
      if logged_in and not asked_state:
        asked_state = True
        connection.send(json.dumps({'type': 'command', 'command': 'get_state'}))
      elif not logged_in:
        asked_state = False
      self.on_change()
