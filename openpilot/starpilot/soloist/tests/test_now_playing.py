import json

from openpilot.starpilot.soloist import now_playing
from openpilot.starpilot.system.starport.projection_now_playing import ProjectionNowPlayingFeed

PLAYER: dict = {'loggedIn': True, 'active': True, 'status': 'playing',
          'track': {'title': 'Midnight City', 'artists': 'M83', 'album': 'Hurry Up', 'durationMs': 243000},
          'position': {'ms': 10000, 'at': 1000, 'speed': 1.0}}


def present(value):
  assert value is not None
  return value


def test_spotify_snapshot_needs_an_active_logged_in_player():
  found = present(now_playing.from_spotify(PLAYER))
  assert found['source'] == 'spotify' and found['title'] == 'Midnight City' and found['artist'] == 'M83'
  for change in ({'loggedIn': False}, {'active': False}, {'status': 'idle'}, {'track': None}):
    assert now_playing.from_spotify({**PLAYER, **change}) is None
  assert now_playing.from_spotify(None) is None


def test_phone_snapshot_reads_bluez_media_player():
  props = {'Status': 'playing', 'Position': 5000, 'Track': {'Title': 'Song', 'Artist': 'Band', 'Album': 'LP', 'Duration': 200000}}
  found = present(now_playing.from_phone(props, now_ms=2000))
  assert (found['source'], found['status'], found['title'], found['artist']) == ('phone', 'playing', 'Song', 'Band')
  assert found['durationMs'] == 200000 and found['position'] == {'ms': 5000, 'at': 2000, 'speed': 1.0}
  assert present(now_playing.from_phone({**props, 'Status': 'paused'}, now_ms=2000))['position']['speed'] == 0.0
  for bad in (None, {}, {'Status': 'stopped', 'Track': props['Track']}, {'Status': 'playing', 'Track': {}},
              {'Status': 'playing', 'Track': {'Title': '\x00\x01'}}):
    assert now_playing.from_phone(bad) is None


def test_untrusted_text_is_cleaned_and_bounded():
  found = present(now_playing.from_phone({'Status': 'playing', 'Track': {'Title': 'A\x00B' + 'x' * 500, 'Artist': 7}}))
  assert found['title'].startswith('AB') and len(found['title']) == now_playing.MAX_TEXT and found['artist'] == ''


def test_elapsed_follows_the_anchor_and_clamps_to_the_duration():
  found = present(now_playing.from_spotify(PLAYER))
  assert now_playing.elapsed_ms(found, 4000) == 13000
  assert now_playing.elapsed_ms(found, 10 ** 12) == 243000
  assert now_playing.elapsed_ms({**found, 'position': None}, 4000) == 0


def test_published_snapshot_is_ignored_when_stale_or_broken(tmp_path):
  path = tmp_path / 'status.json'
  assert now_playing.read(path) is None
  path.write_text(json.dumps({'nowPlaying': now_playing.from_spotify(PLAYER)}))
  mtime = path.stat().st_mtime
  assert present(now_playing.read(path, now=mtime + 1))['title'] == 'Midnight City'
  assert now_playing.read(path, now=mtime + 60) is None
  path.write_text('{broken')
  assert now_playing.read(path, now=mtime + 1) is None
  path.write_text(json.dumps({'nowPlaying': {'title': 'x', 'status': 'idle'}}))
  assert now_playing.read(path, now=mtime + 1) is None


def test_feed_only_reads_while_enabled():
  reads = []
  feed = ProjectionNowPlayingFeed(read=lambda: reads.append(1) or {'title': 'x'})
  feed.poll()
  assert reads == [] and feed.snapshot() is None
  feed._enabled = True
  feed.poll()
  assert feed.snapshot() == {'title': 'x'}
  feed.set_enabled(False)
  assert feed.snapshot() is None
  feed.close()


def test_shuffle_repeat_and_device_come_from_spotify_and_are_unavailable_for_phone_audio():
  found = present(now_playing.from_spotify({**PLAYER, 'shuffle': True, 'repeat': 'track', 'deviceName': 'StarPilot'}))
  assert (found['shuffle'], found['repeat'], found['device']) == (True, 'track', 'StarPilot')
  found = present(now_playing.from_spotify({**PLAYER, 'repeat': 'sometimes'}))
  assert (found['shuffle'], found['repeat']) == (False, 'off')
  found = present(now_playing.from_phone({'Status': 'playing', 'Track': {'Title': 'Song'}}, device='Pixel 9'))
  assert (found['shuffle'], found['repeat'], found['device'], found['artwork']) == (None, None, 'Pixel 9', '')


def test_car_screen_offers_play_here_only_while_spotify_plays_on_another_device(tmp_path):
  path = tmp_path / 'status.json'
  inactive = {**PLAYER, 'active': False, 'connected': True, 'deviceName': 'StarPilot'}
  path.write_text(json.dumps({'nowPlaying': None, 'player': inactive}))
  mtime = path.stat().st_mtime
  assert now_playing.read(path, now=mtime + 1) is None
  assert now_playing.read_card(path, now=mtime + 1) == {'source': 'spotify', 'status': 'elsewhere', 'title': '', 'device': 'StarPilot'}
  assert now_playing.read_card(path, now=mtime + 60) is None
  for player in ({**inactive, 'loggedIn': False}, {**inactive, 'connected': False}, {**inactive, 'status': 'idle'},
                 {**inactive, 'track': None}, None):
    path.write_text(json.dumps({'nowPlaying': None, 'player': player}))
    assert now_playing.read_card(path, now=mtime + 1) is None
  path.write_text(json.dumps({'nowPlaying': now_playing.from_spotify(PLAYER), 'player': PLAYER}))
  assert present(now_playing.read_card(path, now=mtime + 1))['title'] == 'Midnight City'


def test_spotify_cover_is_kept_only_on_spotify_image_hosts():
  cover = 'https://i.scdn.co/image/ab67616d0000b273'
  found = present(now_playing.from_spotify({**PLAYER, 'track': {**PLAYER['track'], 'artwork': cover}}))
  assert found['artwork'] == cover
  for bad in ('http://i.scdn.co/image/a', 'https://evil.example/a.jpg', 'https://i.scdn.co:8443/a', 'https://u@i.scdn.co/a',
              'https://i.scdn.co:abc/a', 'https://[bad/a', 'https://i.scdn.co/' + 'a' * 600, None, 7):
    assert present(now_playing.from_spotify({**PLAYER, 'track': {**PLAYER['track'], 'artwork': bad}}))['artwork'] == ''


def test_feed_downloads_each_cover_once_and_drops_failures():
  from openpilot.starpilot.system.starport import projection_now_playing
  value = {'title': 'x', 'artwork': 'https://i.scdn.co/image/a'}
  fetched = []

  def fetch(url):
    fetched.append(url)
    if url.endswith('/bad'):
      raise OSError('timed out')
    return (1, 1, b'\0\0\0\0')
  feed = ProjectionNowPlayingFeed(read=lambda: value, fetch=fetch)
  feed._enabled = True
  feed.poll()
  feed.poll()
  assert fetched == [value['artwork']] and feed.artwork() == (value['artwork'], (1, 1, b'\0\0\0\0'))
  cover, value = value, None
  feed.poll()  # buffering between songs: the cover stays
  assert feed.artwork() == (cover['artwork'], (1, 1, b'\0\0\0\0')) and len(fetched) == 1
  value = {'title': 'y', 'artwork': 'https://i.scdn.co/image/bad'}
  feed.poll()
  feed.poll()
  assert feed.artwork() == (value['artwork'], None) and len(fetched) == 2
  value = {'title': 'z', 'artwork': ''}
  feed.poll()
  assert feed.artwork() == ('', None) and len(fetched) == 2
  assert projection_now_playing.fetch_artwork('https://evil.example/a.jpg') is None
  assert projection_now_playing.image_type(b'\x89PNG\r\n\x1a\n...') == '.png' and projection_now_playing.image_type(b'GIF89a') is None
  feed.close()


def test_jpeg_cover_is_decoded_to_a_square_rgba_texture_source():
  import io
  from PIL import Image
  from openpilot.starpilot.system.starport import projection_now_playing
  jpeg = io.BytesIO()
  Image.new('RGB', (640, 600), (200, 30, 60)).save(jpeg, format='JPEG')
  width, height, pixels = projection_now_playing.decode_cover(jpeg.getvalue())
  assert (width, height) == (projection_now_playing.ARTWORK_PX,) * 2 and len(pixels) == width * height * 4
  assert abs(pixels[0] - 200) < 20 and pixels[3] == 255
