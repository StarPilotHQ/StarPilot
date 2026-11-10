"""Music from Spotify or a Bluetooth phone over the head unit's media audio channel, as a phone sends it.

The channel is opened only when music is set to play through Android Auto. Every step is driven
by messages the session loop already reads, so nothing here waits on the car and the video path
keeps its timing: open → setup (PCM) → audio focus → start → timestamped PCM with ACK credit.

PCM comes from the Spotify supervisor's real-time pipe (48 kHz, 16-bit, stereo), which runs on the
system clock and carries silence while paused. Focus is requested when sound starts and released
after a stretch of silence, the way a phone treats a paused player. If the car takes focus for good
(the driver picks the radio), the selected source is paused instead of fighting for the speakers.
"""
from __future__ import annotations

import array
import errno
import fcntl
import os
import stat
import struct
import termios
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openpilot.starpilot.system.starport.wire import field, one, signed

MSG_CHANNEL_OPEN_REQUEST = 0x07
MSG_CHANNEL_OPEN_RESPONSE = 0x08
AUDIO_FOCUS_REQUEST = 0x12
AUDIO_FOCUS_NOTIFICATION = 0x13
AV_MEDIA_WITH_TIMESTAMP = 0x0000
AV_SETUP_REQUEST = 0x8000
AV_START_INDICATION = 0x8001
AV_STOP_INDICATION = 0x8002
AV_SETUP_RESPONSE = 0x8003
AV_MEDIA_ACK = 0x8004

CODEC_PCM = 1
AUDIO_TYPE_MEDIA = 3
SETUP_STATUS_READY = 2
FOCUS_GAIN, FOCUS_RELEASE = 1, 4
STATE_GAIN, STATE_GAIN_TRANSIENT, STATE_LOSS, STATE_LOSS_DUCK, STATE_LOSS_TRANSIENT, STATE_GAIN_MEDIA_ONLY = 1, 2, 3, 4, 5, 6
GAINED = (STATE_GAIN, STATE_GAIN_TRANSIENT, STATE_GAIN_MEDIA_ONLY)

RATE, CHANNELS, BITS = 48000, 2, 16
FRAME_BYTES = CHANNELS * BITS // 8
BYTES_PER_SECOND = RATE * FRAME_BYTES
MIN_CHUNK = BYTES_PER_SECOND // 50          # 20 ms
MAX_CHUNK = 32768                           # ~170 ms, so a one-packet ACK window still keeps up
BUFFER_PLAYING = BYTES_PER_SECOND           # cap while streaming; older audio is dropped, never delayed further
BUFFER_IDLE = BYTES_PER_SECOND // 2         # pre-roll kept while asking for focus
PIPE_SIZE = 1048576                         # ~5.5 s, so a late read does not make the audio server drop sound
PIPE_PAGE = 4096
MAX_WINDOW = 16
SILENCE_LEVEL = 16                          # peak sample magnitude treated as silence
RELEASE_AFTER = 8.0                         # seconds of silence before giving the speakers back
STEP_TIMEOUT = 3.0
ACK_STALL = 3.0
RETRY_AFTER = 10.0


def media_channel(channels: list[dict]) -> tuple[int, int] | None:
  """(channel id, configuration index) of the head unit's PCM media channel at 48 kHz/16-bit/stereo."""
  for channel in channels:
    if channel.get('media_type') != CODEC_PCM or channel.get('audio_type') != AUDIO_TYPE_MEDIA:
      continue
    for index, config in enumerate(channel.get('audio_configs', [])):
      if (one(config, 1), one(config, 2), one(config, 3)) == (RATE, BITS, CHANNELS):
        return int(channel['id']), index
  return None


class PipeSource:
  """Non-blocking reader of the Spotify pipe; reopens when the audio server restarts."""

  def __init__(self, path: Path, routed: Callable[[], bool]):
    self.path, self.routed = path, routed
    self.fd: int | None = None
    self.checked = 0.0
    self.active_value = False
    self.generation = None
    self.capacity = 65536
    self.overruns = 0

  def active(self, now: float) -> bool:
    if now - self.checked >= 1.0:
      self.checked = now
      try:
        info = self.path.stat()
        identity = (info.st_dev, info.st_ino)
        if identity != self.generation:
          self.close()
          self.generation = identity
        self.active_value = self.routed() and stat.S_ISFIFO(info.st_mode)
      except OSError:
        self.active_value = False
      if not self.active_value:
        self.close()
    return self.active_value

  def read(self, limit: int) -> bytes:
    if self.fd is None:
      try:
        self.fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
      except OSError:
        return b''
      self.capacity = self._grow(self.fd)
    elif self._queued(self.fd) >= self.capacity - PIPE_PAGE:
      self.overruns += 1  # full between reads: the audio server was discarding sound
    chunks, total = [], 0
    while total < limit:
      try:
        data = os.read(self.fd, min(65536, limit - total))
      except OSError as error:
        if error.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
          self.close()
        break
      if not data:  # writer gone: the audio server stopped
        self.close()
        break
      chunks.append(data)
      total += len(data)
    return b''.join(chunks)

  @staticmethod
  def _grow(fd: int) -> int:
    try:
      fcntl.fcntl(fd, fcntl.F_SETPIPE_SZ, PIPE_SIZE)
    except (AttributeError, OSError):
      pass
    try:
      return fcntl.fcntl(fd, fcntl.F_GETPIPE_SZ)
    except (AttributeError, OSError):
      return 65536

  @staticmethod
  def _queued(fd: int) -> int:
    try:
      return struct.unpack('i', fcntl.ioctl(fd, termios.FIONREAD, b'\0\0\0\0'))[0]
    except OSError:
      return 0

  def close(self) -> None:
    if self.fd is not None:
      os.close(self.fd)
      self.fd = None


def music_routed() -> bool:
  from openpilot.starpilot.soloist import paths, preferences
  return paths.enabled() and preferences.load().output == 'starport'


def music_control(action: str) -> None:
  def worker():
    try:
      from openpilot.starpilot.galaxy.spotify_setup import SpotifyClient
      SpotifyClient().call('control', action=action, args={})
    except Exception:
      pass
  threading.Thread(target=worker, name=f'aa_music_{action}', daemon=True).start()


def pause_music() -> None:
  music_control('pause')


class MediaAudio:
  def __init__(self, session, channel: int, config_index: int, source, log: Callable[..., None],
               pause: Callable[[], None] = pause_music, clock: Callable[[], float] = time.monotonic):
    self.session, self.channel, self.config_index = session, channel, config_index
    self.source, self.log, self.pause, self.clock = source, log, pause, clock
    self.state = 'closed'  # closed, opening, setup, ready, focus_wait, playing, suspended, unavailable
    self.deadline = 0.0
    self.retry_at = 0.0
    self.window = 1
    self.unacked = 0
    self.pending: list[float] = []
    self.session_id = 0
    self.buffer = bytearray()
    self.frames_sent = 0
    self.base_us = 0
    self.last_sound = -1e9
    self.car_paused = False
    self.last_service: float | None = None
    self.max_gap = 0.0
    self.source_generation = getattr(source, 'generation', None)
    self.stats: dict[str, Any] = {'chunks_sent': 0, 'acked': 0, 'dropped_ms': 0, 'focus': None}
    self.track_info = None  # MediaStatus for the car's own displays, when the head unit offers it

  @classmethod
  def create(cls, session, log, *, source=None, routed: Callable[[], bool] = music_routed, **kwargs):
    found = media_channel(session.channels)
    if found is None:
      return None
    if source is None:
      from openpilot.starpilot.soloist.backend import starport_pipe
      source = PipeSource(starport_pipe(), routed)
    return cls(session, *found, source, log, **kwargs)

  def _event(self, name: str, **values) -> None:
    self.log(f'media_audio_{name}', channel=self.channel, **values)

  def _set(self, state: str, timeout: float | None = None) -> None:
    if state != self.state:
      self._event('state', state=state, previous=self.state)
    self.state = state
    self.deadline = self.clock() + timeout if timeout else 0.0

  # ------------------------------------------------------------------ loop
  def service(self) -> None:
    if self.track_info is not None:
      self.track_info.service()
    now = self.clock()
    if self.state == 'playing' and self.last_service is not None:
      self.max_gap = max(self.max_gap, now - self.last_service)
    self.last_service = now
    active = self.source.active(now)
    if not active:
      self.buffer.clear()
      if self.state in ('playing', 'focus_wait'):
        self._give_back('music_not_routed')
      return
    generation = getattr(self.source, 'generation', None)
    if generation != self.source_generation:
      self.source_generation = generation
      self.buffer.clear()
      self.last_sound = -1e9
      self.car_paused = False
      if self.state in ('playing', 'focus_wait', 'suspended'):
        self._give_back('source_changed')
    playing = self.state == 'playing'
    data = self.source.read(BUFFER_PLAYING)
    if data:
      self._take(data, now, BUFFER_PLAYING if playing else BUFFER_IDLE)
    sounding = now - self.last_sound < 0.5
    if self.deadline and now > self.deadline:
      self._timeout(now)
    if self.state == 'closed' and now >= self.retry_at:
      self.session.send(self.channel, MSG_CHANNEL_OPEN_REQUEST, field(1, 0) + field(2, self.channel), control=True)
      self._set('opening', STEP_TIMEOUT)
    elif self.state == 'ready' and sounding and not self.car_paused and now >= self.retry_at:
      self.session.send(0, AUDIO_FOCUS_REQUEST, field(1, FOCUS_GAIN))
      self._set('focus_wait', STEP_TIMEOUT)
    elif self.state == 'playing':
      if now - self.last_sound > RELEASE_AFTER:
        self._give_back('silence')
      else:
        self._send(now)

  def _take(self, data: bytes, now: float, cap: int) -> None:
    usable = len(data) - len(data) % 2
    samples = array.array('h', data[:usable])
    if samples and (max(samples) > SILENCE_LEVEL or min(samples) < -SILENCE_LEVEL):
      self.last_sound = now
    elif now - self.last_sound > 2.0:
      self.car_paused = False  # the player went quiet; a new press of play may ask for focus again
    self.buffer.extend(data)
    excess = len(self.buffer) - cap
    if excess > 0:
      excess += (-excess) % FRAME_BYTES
      del self.buffer[:excess]
      if self.state == 'playing':
        self.stats['dropped_ms'] += excess * 1000 // BYTES_PER_SECOND

  def _send(self, now: float) -> None:
    if self.pending and now - self.pending[0] > ACK_STALL:
      self._event('ack_stall', pending=self.unacked)
      self._give_back('ack_stall')
      return
    sent = 0
    while self.unacked < self.window and len(self.buffer) >= MIN_CHUNK and sent < 2:
      size = min(len(self.buffer), MAX_CHUNK)
      size -= size % FRAME_BYTES
      chunk = bytes(self.buffer[:size])
      del self.buffer[:size]
      timestamp = self.base_us + self.frames_sent * 1_000_000 // RATE
      self.session.send(self.channel, AV_MEDIA_WITH_TIMESTAMP, struct.pack('>Q', timestamp) + chunk)
      self.frames_sent += size // FRAME_BYTES
      self.unacked += 1
      self.pending.append(now)
      self.stats['chunks_sent'] += 1
      sent += 1

  def _timeout(self, now: float) -> None:
    state = self.state
    self.deadline = 0.0
    self._event('timeout', state=state)
    if state in ('opening', 'setup'):
      self._set('unavailable')
    elif state == 'focus_wait':
      self.retry_at = now + RETRY_AFTER
      self._set('ready')

  def _give_back(self, reason: str) -> None:
    if self.state == 'playing':
      self.session.send(self.channel, AV_STOP_INDICATION)
    if self.state in ('playing', 'focus_wait', 'suspended'):
      self.session.send(0, AUDIO_FOCUS_REQUEST, field(1, FOCUS_RELEASE))
    self.unacked, self.pending = 0, []
    self._event('released', reason=reason)
    self._set('ready')

  # -------------------------------------------------------------- messages
  def handle(self, channel: int, kind: int, fields: dict) -> bool:
    if self.track_info is not None and self.track_info.handle(channel, kind, fields):
      return True
    if channel == 0 and kind == AUDIO_FOCUS_NOTIFICATION:
      self._focus(one(fields, 1), one(fields, 2, 0))
      return True
    if channel != self.channel:
      return False
    if kind == MSG_CHANNEL_OPEN_RESPONSE and self.state == 'opening':
      if signed(one(fields, 1)) != 0:
        self._event('open_rejected', status=signed(one(fields, 1)))
        self._set('unavailable')
      else:
        self.session.send(self.channel, AV_SETUP_REQUEST, field(1, CODEC_PCM))
        self._set('setup', STEP_TIMEOUT)
    elif kind == AV_SETUP_RESPONSE and self.state == 'setup':
      window, configs = int(one(fields, 2, 0) or 0), fields.get(3, [])
      if one(fields, 1) != SETUP_STATUS_READY or window < 1 or configs and self.config_index not in configs:
        self._event('setup_rejected', status=one(fields, 1), window=window)
        self._set('unavailable')
      else:
        self.window = min(window, MAX_WINDOW)
        self._event('setup', window=window, used_window=self.window)
        self._set('ready')
    elif kind == AV_MEDIA_ACK:
      count = int(one(fields, 2, 0) or 0)
      if one(fields, 1) == self.session_id and 0 < count <= self.unacked:
        self.unacked -= count
        del self.pending[:count]
        self.stats['acked'] += count
    return True

  def _focus(self, focus, unsolicited) -> None:
    self.stats['focus'] = focus
    self._event('focus', focus=focus, unsolicited=unsolicited)
    if focus in GAINED and self.state in ('focus_wait', 'suspended'):
      self.session_id += 1
      self.unacked, self.pending, self.frames_sent = 0, [], 0
      self.base_us = time.monotonic_ns() // 1000
      self.session.send(self.channel, AV_START_INDICATION, field(1, self.session_id) + field(2, self.config_index))
      self._set('playing')
    elif focus == STATE_LOSS_TRANSIENT and self.state == 'playing':
      self.session.send(self.channel, AV_STOP_INDICATION)
      self._set('suspended')  # a call or the car's own prompt; the car gives focus back afterwards
    elif focus == STATE_LOSS and self.state in ('playing', 'focus_wait', 'suspended'):
      if self.state == 'playing':
        self.session.send(self.channel, AV_STOP_INDICATION)
      self.car_paused = True
      self.unacked, self.pending = 0, []
      self._set('ready')
      self.pause()

  def status(self) -> dict:
    return {'state': self.state, 'window': self.window, 'pending': self.unacked, **self.stats,
            'pipe_overruns': getattr(self.source, 'overruns', 0), 'max_service_gap_ms': round(self.max_gap * 1000)}

  def reset_peaks(self) -> None:
    self.max_gap = 0.0

  def close(self) -> None:
    self.source.close()
    if self.track_info is not None:
      self.track_info.close()
