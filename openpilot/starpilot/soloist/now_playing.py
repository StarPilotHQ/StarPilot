"""One small now-playing snapshot for the car screen, from Spotify or from a phone's Bluetooth player."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from openpilot.starpilot.soloist.client import REPEATS, artwork_url

MAX_TEXT = 120
STALE_SECONDS = 10.0
ELSEWHERE = 'elsewhere'


def _text(value: Any) -> str:
  return ''.join(ch for ch in value if ch.isprintable())[:MAX_TEXT].strip() if isinstance(value, str) else ''


def _whole(value: Any, limit: int = 86400000) -> int | None:
  return int(value) if type(value) in (int, float) and 0 <= value < limit else None


def snapshot(source: str, status: str, title: str, artist: str, album: str, duration_ms: int | None,
             position: dict | None, shuffle: bool | None = None, artwork: Any = '', repeat: Any = None,
             device: Any = '') -> dict | None:
  """``shuffle`` and ``repeat`` are None when the player cannot report or change them."""
  title = _text(title)
  if not title or status not in ('playing', 'paused'):
    return None
  return {'source': source, 'status': status, 'title': title, 'artist': _text(artist), 'album': _text(album),
          'durationMs': duration_ms, 'position': position, 'shuffle': shuffle, 'artwork': artwork_url(artwork),
          'repeat': repeat if repeat in REPEATS else None, 'device': _text(device)[:40]}


def from_spotify(player: dict | None) -> dict | None:
  if not isinstance(player, dict) or not player.get('loggedIn'):
    return None
  track = player.get('track')
  if not isinstance(track, dict) or player.get('status') not in ('playing', 'paused') or not player.get('active'):
    return None
  return snapshot('spotify', player['status'], track.get('title'), track.get('artists'), track.get('album'),
                  _whole(track.get('durationMs')), player.get('position'), player.get('shuffle') is True,
                  track.get('artwork'), player.get('repeat') if player.get('repeat') in REPEATS else 'off',
                  player.get('deviceName'))


def from_phone(props: dict | None, now_ms: int | None = None, device: str = '') -> dict | None:
  """BlueZ MediaPlayer1 properties: Track {Title, Artist, Album, Duration ms}, Status, Position ms."""
  if not isinstance(props, dict) or not isinstance(props.get('Track'), dict):
    return None
  status = {'playing': 'playing', 'paused': 'paused'}.get(str(props.get('Status', '')).lower())
  track = props['Track']
  position = _whole(props.get('Position'))
  anchor = None
  if position is not None:
    # Anchors are epoch ms, like the ones Spotify sends.
    anchor = {'ms': position, 'at': int(time.time() * 1000) if now_ms is None else now_ms,  # noqa: TID251
              'speed': 1.0 if status == 'playing' else 0.0}
  duration = _whole(track.get('Duration'))
  return snapshot('phone', status or '', track.get('Title'), track.get('Artist'), track.get('Album'),
                  duration if duration else None, anchor, device=device)


def elapsed_ms(now_playing: dict, now_ms: float | None = None) -> int:
  position, duration = now_playing.get('position'), now_playing.get('durationMs')
  if not isinstance(position, dict):
    return 0
  now_ms = time.time() * 1000 if now_ms is None else now_ms  # noqa: TID251 epoch-ms anchors
  value = position['ms'] + max(0.0, now_ms - position['at']) * position['speed']
  return int(max(0, min(duration if duration else value, value)))


def _published(path: Path, now: float | None) -> dict | None:
  try:
    if (time.time() if now is None else now) - path.stat().st_mtime > STALE_SECONDS:  # noqa: TID251 compared with mtime
      return None
    found = json.loads(path.read_text())
  except (OSError, ValueError):
    return None
  return found if isinstance(found, dict) else None


def _song(status: dict | None) -> dict | None:
  found = status.get('nowPlaying') if status is not None else None
  return found if isinstance(found, dict) and found.get('title') and found.get('status') in ('playing', 'paused') else None


def read(path: Path, now: float | None = None) -> dict | None:
  """The supervisor's published snapshot; None when missing, malformed or the supervisor stopped updating."""
  return _song(_published(path, now))


def elsewhere(player: dict | None) -> dict | None:
  """Spotify is signed in here and another device is playing or paused on a track; ``device`` is this player's own name."""
  if not isinstance(player, dict) or not (player.get('connected') and player.get('loggedIn')) or player.get('active'):
    return None
  if player.get('status') not in ('playing', 'paused') or not isinstance(player.get('track'), dict):
    return None
  return {'source': 'spotify', 'status': ELSEWHERE, 'title': '', 'device': _text(player.get('deviceName'))[:40]}


def read_card(path: Path, now: float | None = None) -> dict | None:
  """For the car screen: the song, or the take-over offer while Spotify plays on another device."""
  status = _published(path, now)
  return _song(status) or (elsewhere(status.get('player')) if status is not None else None)
