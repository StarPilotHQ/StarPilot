"""Song, artist and progress for the car's own displays (such as the instrument cluster), as a phone sends them.

Only head units that advertise the media playback status service receive it, and only while music
plays through Android Auto. The snapshot is polled on its own thread; the session loop just compares
and sends, once a second at most.

Wire facts: mrmees/open-android-auto ``oaa/media`` and ``oaa/control/ChannelDescriptorData`` (reference only).
"""
from __future__ import annotations

import time
from collections.abc import Callable

from openpilot.starpilot.system.starport.wire import field, signed, one

MSG_CHANNEL_OPEN_REQUEST = 0x07
MSG_CHANNEL_OPEN_RESPONSE = 0x08
PLAYBACK_STATUS = 0x8001
PLAYBACK_METADATA = 0x8003

MEDIA_INFO_SERVICE = 9  # ChannelDescriptor.media_info_channel
STATE_STOPPED, STATE_PLAYING, STATE_PAUSED = 1, 2, 3
SOURCE_NAMES = {'spotify': 'Spotify', 'phone': 'Phone'}
STEP_TIMEOUT = 3.0
UPDATE_EVERY = 1.0


def media_info_channel(channels: list[dict]) -> int | None:
  return next((int(channel['id']) for channel in channels if MEDIA_INFO_SERVICE in channel.get('services', [])), None)


def status_message(track: dict | None, now_ms: float | None = None) -> bytes:
  if track is None:
    return field(1, STATE_STOPPED) + field(2, '') + field(3, 0) + field(4, 0) + field(5, 0) + field(6, 0)
  from openpilot.starpilot.soloist.now_playing import elapsed_ms
  state = STATE_PLAYING if track['status'] == 'playing' else STATE_PAUSED
  return (field(1, state) + field(2, SOURCE_NAMES.get(track.get('source'), 'StarPilot')) +
          field(3, elapsed_ms(track, now_ms) // 1000) + field(4, 0) + field(5, 0) + field(6, 0))


def metadata_message(track: dict) -> bytes:
  return (field(1, track['title']) + field(2, track.get('artist') or '') + field(3, track.get('album') or '') +
          field(6, (track.get('durationMs') or 0) // 1000) + field(7, 0))


def routed_snapshot() -> dict | None:
  from openpilot.starpilot.soloist import now_playing
  from openpilot.starpilot.soloist.paths import Layout
  from openpilot.starpilot.system.starport.media_audio import music_routed
  return now_playing.read(Layout.default().status) if music_routed() else None


class MediaStatus:
  def __init__(self, session, channel: int, feed, log: Callable[..., None], clock: Callable[[], float] = time.monotonic):
    self.session, self.channel, self.feed, self.log, self.clock = session, channel, feed, log, clock
    self.state = 'closed'  # closed, opening, open, unavailable
    self.deadline = 0.0
    self.next_update = 0.0
    self.shown: tuple | None = None
    self.last_status: str | None = None
    self.stopped = True

  @classmethod
  def create(cls, session, log, *, feed=None, **kwargs):
    channel = media_info_channel(session.channels)
    if channel is None:
      return None
    if feed is None:
      from openpilot.starpilot.system.starport.projection_now_playing import ProjectionNowPlayingFeed
      feed = ProjectionNowPlayingFeed(read=routed_snapshot, fetch=lambda _url: None)
      feed.set_enabled(True)
    return cls(session, channel, feed, log, **kwargs)

  def _set(self, state: str) -> None:
    if state != self.state:
      self.log('media_status_state', channel=self.channel, state=state, previous=self.state)
    self.state = state

  def service(self) -> None:
    now = self.clock()
    if self.state == 'closed':
      self.session.send(self.channel, MSG_CHANNEL_OPEN_REQUEST, field(1, 0) + field(2, self.channel), control=True)
      self.deadline = now + STEP_TIMEOUT
      self._set('opening')
    elif self.state == 'opening' and now > self.deadline:
      self._set('unavailable')
    elif self.state == 'open' and now >= self.next_update:
      self.next_update = now + UPDATE_EVERY
      self._update(self.feed.snapshot())

  def _update(self, track: dict | None) -> None:
    if track is None:
      if not self.stopped:
        self.session.send(self.channel, PLAYBACK_STATUS, status_message(None))
        self.stopped, self.shown, self.last_status = True, None, None
      return
    shown = (track.get('source'), track['title'], track.get('artist'), track.get('album'), track.get('durationMs'))
    if shown != self.shown:
      self.session.send(self.channel, PLAYBACK_METADATA, metadata_message(track))
      self.shown = shown
    if track['status'] == 'playing' or track['status'] != self.last_status:
      self.session.send(self.channel, PLAYBACK_STATUS, status_message(track))
    self.last_status, self.stopped = track['status'], False

  def handle(self, channel: int, kind: int, fields: dict) -> bool:
    if channel != self.channel:
      return False
    if kind == MSG_CHANNEL_OPEN_RESPONSE and self.state == 'opening':
      if signed(one(fields, 1)) != 0:
        self.log('media_status_rejected', channel=self.channel, status=signed(one(fields, 1)))
        self._set('unavailable')
      else:
        self._set('open')
    return True  # the car's own media button events (0x8002) are not used

  def close(self) -> None:
    self.feed.close()
