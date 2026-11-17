"""Car media buttons skip tracks, and the playing song reaches the car's own displays."""

import pytest

from openpilot.starpilot.system.starport import media_status as ms
from openpilot.starpilot.system.starport.session import INPUT_EVENT, ProjectionSession
from openpilot.starpilot.system.starport.touch import KeyTracker
from openpilot.starpilot.system.starport.wire import field, parse_fields

TRACK = {'source': 'spotify', 'status': 'playing', 'title': 'Midnight City', 'artist': 'M83', 'album': 'Hurry Up',
         'durationMs': 243000, 'position': {'ms': 12000, 'at': 1000, 'speed': 1.0}}


def key(code, down, longpress=False):
  return field(1, code) + field(2, int(down)) + field(3, 0) + field(4, int(longpress))


def keys(*pressed):
  return field(1, 123456) + field(4, b''.join(field(1, k) for k in pressed))


def test_one_action_per_press_even_while_held():
  tracker = KeyTracker()
  assert tracker.decode(keys(key(87, True))) == ['next']
  assert tracker.decode(keys(key(87, True, longpress=True))) == []
  assert tracker.decode(keys(key(87, False))) == []
  assert tracker.decode(keys(key(88, True), key(85, True))) == ['previous', 'play_pause']
  assert tracker.decode(keys(key(87, True))) == ['next']


def test_non_media_buttons_and_touches_are_ignored():
  tracker = KeyTracker()
  assert tracker.decode(keys(key(3, True), key(84, True))) == []
  assert tracker.decode(field(1, 1) + field(3, field(3, 0))) == []
  with pytest.raises(ValueError):
    tracker.decode(b'\x00' * (17 * 1024))


def session_with_input(focused):
  session = ProjectionSession.__new__(ProjectionSession)
  session.audio, session.mode, session.input_channel, session.input_events = None, None, 3, 0
  session.touch, session.focused, session.keys = None, focused, KeyTracker()
  session.touch_events, session.events, session.actions = [], [], []  # ty: ignore[invalid-assignment]  # test double
  session.event = lambda name, **values: session.events.append((name, values))  # ty: ignore[invalid-assignment]  # test double
  session.media_control = session.actions.append
  return session


@pytest.mark.parametrize('focused', [True, False])
def test_session_sends_media_keys_even_on_the_cars_own_screen(focused):
  session = session_with_input(focused)
  session.handle(3, INPUT_EVENT, keys(key(87, True)))
  session.handle(3, INPUT_EVENT, keys(key(87, False)))
  session.handle(3, INPUT_EVENT, keys(key(88, True)))
  assert session.actions == ['next', 'previous']
  assert ('media_key', {'channel': 3, 'action': 'next'}) in session.events


class Session:
  def __init__(self, services):
    self.channels = [{'id': 4, 'services': [3]}, {'id': 11, 'services': services}]
    self.sent = []
  def send(self, channel, kind, body=b'', control=False):
    self.sent.append((channel, kind, body))


class Feed:
  def __init__(self): self.value, self.closed = None, False
  def snapshot(self): return self.value
  def close(self): self.closed = True


@pytest.fixture
def status():
  session, feed, now = Session([ms.MEDIA_INFO_SERVICE]), Feed(), [100.0]
  stream = ms.MediaStatus.create(session, lambda *a, **k: None, feed=feed, clock=lambda: now[0])
  return stream, session, feed, now


def opened(stream, session):
  stream.service()
  assert session.sent[-1][:2] == (11, ms.MSG_CHANNEL_OPEN_REQUEST)
  assert stream.handle(11, ms.MSG_CHANNEL_OPEN_RESPONSE, {1: [0]})
  assert stream.state == 'open'
  session.sent.clear()


def test_unknown_duration_is_still_sent_as_zero():
  assert parse_fields(ms.metadata_message({**TRACK, 'durationMs': None}))[6] == [0]


def test_only_head_units_offering_the_service_get_it():
  assert ms.MediaStatus.create(Session([3]), lambda *a, **k: None, feed=Feed()) is None
  assert ms.media_info_channel(Session([ms.MEDIA_INFO_SERVICE]).channels) == 11


def test_rejected_or_silent_channel_stays_quiet(status):
  stream, session, feed, now = status
  stream.service()
  now[0] += ms.STEP_TIMEOUT + 1
  stream.service()
  assert stream.state == 'unavailable'
  feed.value = TRACK
  stream.service()
  assert len(session.sent) == 1


def test_song_then_progress_once_a_second_then_stop(status, monkeypatch):
  stream, session, feed, now = status
  opened(stream, session)
  stream.service()
  assert session.sent == []  # nothing playing, nothing to clear
  feed.value = TRACK
  monkeypatch.setattr('time.time', lambda: 5.0)  # 4 s after the position anchor
  now[0] += 1
  stream.service()
  (_, kind, meta), (_, kind2, playing) = session.sent
  assert kind == ms.PLAYBACK_METADATA and kind2 == ms.PLAYBACK_STATUS
  assert parse_fields(meta) == {1: [b'Midnight City'], 2: [b'M83'], 3: [b'Hurry Up'], 6: [243], 7: [0]}
  assert parse_fields(playing) == {1: [ms.STATE_PLAYING], 2: [b'Spotify'], 3: [16], 4: [0], 5: [0], 6: [0]}
  stream.service()
  assert len(session.sent) == 2  # within the same second
  now[0] += 1
  stream.service()
  assert [k for _, k, _ in session.sent[2:]] == [ms.PLAYBACK_STATUS]
  feed.value = {**TRACK, 'status': 'paused'}
  now[0] += 1
  stream.service()
  now[0] += 1
  stream.service()
  assert [parse_fields(b)[1] for _, _, b in session.sent[3:]] == [[ms.STATE_PAUSED]]
  feed.value = {**TRACK, 'title': 'Wait'}
  now[0] += 1
  stream.service()
  assert parse_fields(session.sent[-2][2])[1] == [b'Wait']
  feed.value = None
  now[0] += 1
  stream.service()
  now[0] += 1
  stream.service()
  assert parse_fields(session.sent[-1][2])[1] == [ms.STATE_STOPPED]
  assert session.sent[-2][1] != ms.PLAYBACK_STATUS or parse_fields(session.sent[-2][2])[1] != [ms.STATE_STOPPED]
  stream.close()
  assert feed.closed


def test_messages_on_other_channels_pass_through(status):
  stream, _, _, _ = status
  assert not stream.handle(4, ms.MSG_CHANNEL_OPEN_RESPONSE, {1: [0]})
  assert stream.handle(11, 0x8002, {1: [3]})


def test_audio_step_drives_track_info_without_touching_audio_state():
  from openpilot.starpilot.system.starport import media_audio as ma
  class Source:
    def active(self, now): return False
    def close(self): pass
  session, feed = Session([ms.MEDIA_INFO_SERVICE]), Feed()
  audio = ma.MediaAudio(session, 4, 0, Source(), lambda *a, **k: None, pause=lambda: None, clock=lambda: 100.0)
  audio.track_info = ms.MediaStatus.create(session, lambda *a, **k: None, feed=feed, clock=lambda: 100.0)
  audio.service()
  assert session.sent[-1][:2] == (11, ms.MSG_CHANNEL_OPEN_REQUEST) and audio.state == 'closed'
  assert audio.handle(11, ms.MSG_CHANNEL_OPEN_RESPONSE, {1: [0]}) and audio.track_info.state == 'open'
  audio.close()
  assert feed.closed
