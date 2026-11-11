import os
import struct

import pytest

from openpilot.starpilot.system.starport import media_audio as ma
from openpilot.starpilot.system.starport.wire import field, parse_fields

# The media channels a 2025 Honda Civic (Alpine head unit) advertised, as session.discover parses them.
CIVIC = [
  {'id': 4, 'media_type': 1, 'audio_type': 3, 'audio_configs': [{1: [48000], 2: [16], 3: [2]}]},
  {'id': 5, 'media_type': 1, 'audio_type': 1, 'audio_configs': [{1: [48000], 2: [16], 3: [1]}]},
  {'id': 18, 'media_type': 2, 'audio_type': 3, 'audio_configs': [{1: [48000], 2: [16], 3: [2]}]},
  {'id': 22, 'media_type': None, 'video_configs': [{1: [2]}]},
]
TONE = struct.pack('<hh', 3000, -3000) * 4800       # 100 ms of sound
SILENCE = b'\x00' * len(TONE)


class Session:
  def __init__(self):
    self.channels = CIVIC
    self.sent = []
  def send(self, channel, kind, body=b'', control=False):
    self.sent.append((channel, kind, body))
  def kinds(self):
    return [(channel, kind) for channel, kind, _ in self.sent]


class Source:
  def __init__(self):
    self.routed = True
    self.data = bytearray()
    self.closed = False
  def active(self, now): return self.routed
  def read(self, limit):
    out = bytes(self.data[:limit])
    del self.data[:limit]
    return out
  def close(self): self.closed = True


@pytest.fixture
def audio():
  session, source, now, paused = Session(), Source(), [100.0], []
  stream = ma.MediaAudio(session, 4, 0, source, lambda *a, **k: None, pause=lambda: paused.append(True), clock=lambda: now[0])
  return stream, session, source, now, paused


def ready(stream, session, window=2):
  stream.service()
  assert session.kinds()[-1] == (4, ma.MSG_CHANNEL_OPEN_REQUEST)
  stream.handle(4, ma.MSG_CHANNEL_OPEN_RESPONSE, {1: [0]})
  assert session.sent[-1][:2] == (4, ma.AV_SETUP_REQUEST) and parse_fields(session.sent[-1][2]) == {1: [ma.CODEC_PCM]}
  stream.handle(4, ma.AV_SETUP_RESPONSE, {1: [2], 2: [window], 3: [0]})
  assert stream.state == 'ready'


def play(stream, session, source, now):
  source.data += TONE
  stream.service()
  assert session.sent[-1] == (0, ma.AUDIO_FOCUS_REQUEST, field(1, ma.FOCUS_GAIN))
  assert stream.handle(0, ma.AUDIO_FOCUS_NOTIFICATION, {1: [ma.STATE_GAIN]})
  assert session.sent[-1] == (4, ma.AV_START_INDICATION, field(1, 1) + field(2, 0))


def test_media_channel_picks_pcm_media_not_guidance_or_aac():
  assert ma.media_channel(CIVIC) == (4, 0)
  assert ma.media_channel([c for c in CIVIC if c['id'] != 4]) is None


def test_stays_closed_until_spotify_is_routed(audio):
  stream, session, source, _, _ = audio
  source.routed = False
  source.data += TONE
  stream.service()
  assert session.sent == [] and stream.state == 'closed'


def test_open_setup_focus_and_stream_within_ack_credit(audio):
  stream, session, source, now, _ = audio
  ready(stream, session, window=1)
  play(stream, session, source, now)
  stream.service()
  media = [body for channel, kind, body in session.sent if kind == ma.AV_MEDIA_WITH_TIMESTAMP]
  assert len(media) == 1  # one-packet window: wait for the car's ACK
  timestamp, = struct.unpack('>Q', media[0][:8])
  assert len(media[0]) - 8 == len(TONE) and timestamp == stream.base_us
  source.data += TONE
  stream.service()
  assert sum(kind == ma.AV_MEDIA_WITH_TIMESTAMP for _, kind, _ in session.sent) == 1
  stream.handle(4, ma.AV_MEDIA_ACK, {1: [1], 2: [1]})
  stream.service()
  media = [body for channel, kind, body in session.sent if kind == ma.AV_MEDIA_WITH_TIMESTAMP]
  assert len(media) == 2
  assert struct.unpack('>Q', media[1][:8])[0] == stream.base_us + 100_000  # sample-accurate timestamps
  assert stream.status()['acked'] == 1


def test_silence_releases_focus_and_sound_asks_again(audio):
  stream, session, source, now, _ = audio
  ready(stream, session)
  play(stream, session, source, now)
  for _ in range(10):
    now[0] += 1
    source.data += SILENCE
    stream.service()
  assert (4, ma.AV_STOP_INDICATION) in session.kinds()
  assert session.sent[-1] == (0, ma.AUDIO_FOCUS_REQUEST, field(1, ma.FOCUS_RELEASE))
  assert stream.state == 'ready'
  source.data += TONE
  stream.service()
  assert stream.state == 'focus_wait'


def test_car_taking_focus_pauses_spotify_and_does_not_fight_back(audio):
  stream, session, source, now, paused = audio
  ready(stream, session)
  play(stream, session, source, now)
  stream.handle(0, ma.AUDIO_FOCUS_NOTIFICATION, {1: [ma.STATE_LOSS], 2: [1]})
  assert paused == [True] and stream.state == 'ready' and session.kinds()[-1] == (4, ma.AV_STOP_INDICATION)
  source.data += TONE  # the pause takes a moment to reach Spotify
  stream.service()
  assert stream.state == 'ready'
  now[0] += 3
  source.data += SILENCE
  stream.service()
  source.data += TONE  # the driver pressed play again
  stream.service()
  assert stream.state == 'focus_wait'


def test_transient_loss_suspends_and_resumes(audio):
  stream, session, source, now, paused = audio
  ready(stream, session)
  play(stream, session, source, now)
  stream.handle(0, ma.AUDIO_FOCUS_NOTIFICATION, {1: [ma.STATE_LOSS_TRANSIENT]})
  assert stream.state == 'suspended' and paused == []
  stream.handle(0, ma.AUDIO_FOCUS_NOTIFICATION, {1: [ma.STATE_GAIN]})
  assert stream.state == 'playing' and stream.session_id == 2


def test_rejections_and_timeouts_leave_projection_alone(audio):
  stream, session, source, now, _ = audio
  stream.service()
  stream.handle(4, ma.MSG_CHANNEL_OPEN_RESPONSE, {1: [0xFFFFFFFFFFFFFFFF]})  # -1 as a varint
  assert stream.state == 'unavailable'
  other = ma.MediaAudio(Session(), 4, 0, Source(), lambda *a, **k: None, clock=lambda: now[0])
  other.service()
  now[0] += ma.STEP_TIMEOUT + 1
  other.service()
  assert other.state == 'unavailable'


def test_ack_stall_gives_focus_back(audio):
  stream, session, source, now, _ = audio
  ready(stream, session, window=1)
  play(stream, session, source, now)
  stream.service()
  now[0] += ma.ACK_STALL + 0.5
  source.data += TONE
  stream.service()
  assert stream.state == 'ready' and session.sent[-1] == (0, ma.AUDIO_FOCUS_REQUEST, field(1, ma.FOCUS_RELEASE))


def test_buffer_is_bounded_and_drops_oldest(audio):
  stream, session, source, now, _ = audio
  ready(stream, session, window=1)
  play(stream, session, source, now)
  stream.service()
  for _ in range(30):
    source.data += TONE
    stream.service()
  assert len(stream.buffer) <= ma.BUFFER_PLAYING and stream.status()['dropped_ms'] > 0


def test_pipe_source_reads_a_fifo_without_blocking(tmp_path):
  path = tmp_path / 'aa.pcm'
  os.mkfifo(path)
  source = ma.PipeSource(path, lambda: True)
  assert source.active(10.0)
  writer = os.open(path, os.O_RDWR | os.O_NONBLOCK)
  assert source.read(1024) == b''
  os.write(writer, TONE[:4000])
  assert source.read(65536) == TONE[:4000]
  os.close(writer)
  source.close()
  assert not ma.PipeSource(tmp_path / 'missing', lambda: True).active(10.0)


def test_pipe_source_counts_reads_that_find_the_pipe_full(tmp_path):
  path = tmp_path / 'aa.pcm'
  os.mkfifo(path)
  source = ma.PipeSource(path, lambda: True)
  writer = os.open(path, os.O_RDWR | os.O_NONBLOCK)
  try:
    assert source.active(10.0) and source.read(1024) == b''
    assert source.capacity >= 4096 and source.overruns == 0
    os.write(writer, TONE[:1000])
    assert source.read(65536) == TONE[:1000] and source.overruns == 0
    source.capacity = ma.PIPE_PAGE + 1000
    os.write(writer, TONE[:1000])
    assert source.read(65536) == TONE[:1000] and source.overruns == 1
    source.close()
    os.write(writer, TONE[:1000])
    assert source.read(65536) == TONE[:1000] and source.overruns == 1  # backlog left while nobody read
  finally:
    source.close()
    os.close(writer)


def test_status_reports_pipe_overruns(audio):
  stream, _, source, _, _ = audio
  assert stream.status()['pipe_overruns'] == 0
  source.overruns = 3
  assert stream.status()['pipe_overruns'] == 3


def test_longest_service_gap_while_playing_lasts_until_reset(audio):
  stream, session, source, now, _ = audio
  ready(stream, session)
  now[0] += 5.0
  play(stream, session, source, now)
  assert stream.status()['max_service_gap_ms'] == 0
  now[0] += 0.25
  stream.service()
  now[0] += 0.05
  stream.service()
  assert stream.status()['max_service_gap_ms'] == 250
  assert stream.status()['max_service_gap_ms'] == 250
  stream.reset_peaks()
  assert stream.status()['max_service_gap_ms'] == 0


def test_source_switch_discards_old_audio_and_releases_focus(audio):
  stream, session, source, now, _ = audio
  source.generation = 1
  ready(stream, session)
  play(stream, session, source, now)
  stream.buffer.extend(TONE)
  source.generation = 2
  stream.service()
  assert not stream.buffer and stream.state == 'ready'
  assert session.sent[-1] == (0, ma.AUDIO_FOCUS_REQUEST, field(1, ma.FOCUS_RELEASE))


def test_replaced_pipe_reopens_instead_of_reading_previous_source(tmp_path):
  path = tmp_path / 'audio.pcm'
  os.mkfifo(path)
  source = ma.PipeSource(path, lambda: True)
  assert source.active(10)
  old = os.open(path, os.O_RDWR | os.O_NONBLOCK)
  source.read(16)
  generation = source.generation
  path.unlink()
  os.mkfifo(path)
  new = os.open(path, os.O_RDWR | os.O_NONBLOCK)
  try:
    os.write(old, b'old!')
    os.write(new, b'new!')
    assert source.active(12) and source.generation != generation
    assert source.read(16) == b'new!'
  finally:
    source.close()
    os.close(old)
    os.close(new)
