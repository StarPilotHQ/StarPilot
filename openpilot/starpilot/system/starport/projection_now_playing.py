"""Read-only now-playing snapshot for the car screen, polled away from Android Auto's drawing thread.

Cover art download and the 'elsewhere' take-over state are adapted from Peter Nguyen's now-playing card.
"""
import io
import threading
import urllib.request

from openpilot.starpilot.soloist import now_playing
from openpilot.starpilot.soloist.paths import Layout

MAX_ARTWORK_BYTES = 512 * 1024
ARTWORK_TIMEOUT = 4.0
ARTWORK_PX = 480
IMAGE_TYPES = ((b'\xff\xd8\xff', '.jpg'), (b'\x89PNG\r\n\x1a\n', '.png'))


def image_type(data):
  return next((kind for magic, kind in IMAGE_TYPES if data.startswith(magic)), None)


def decode_cover(data):
  """(side, side, RGBA bytes): the cover's centre square, at most ARTWORK_PX wide, ready for a texture."""
  from PIL import Image
  with Image.open(io.BytesIO(data)) as image:
    image = image.convert('RGBA')
    side = min(image.size)
    left, top = (image.width - side) // 2, (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    if side > ARTWORK_PX:
      image = image.resize((ARTWORK_PX, ARTWORK_PX), Image.Resampling.BILINEAR)
    return image.width, image.height, image.tobytes()


def fetch_artwork(url):
  """The decoded cover, or None. Only Spotify's image hosts, at most 512 KB, JPEG or PNG."""
  if not now_playing.artwork_url(url):
    return None
  request = urllib.request.Request(url, headers={'User-Agent': 'starpilot-car-screen'})
  with urllib.request.urlopen(request, timeout=ARTWORK_TIMEOUT) as response:
    if not now_playing.artwork_url(response.geturl()):
      return None
    data = response.read(MAX_ARTWORK_BYTES + 1)
  return decode_cover(data) if len(data) <= MAX_ARTWORK_BYTES and image_type(data) else None


class ProjectionNowPlayingFeed:
  """Only poll while placed. The renderer takes immutable snapshots without file or network IO."""
  def __init__(self, read=None, fetch=None):
    self.read = read or (lambda: now_playing.read_card(Layout.default().status))
    self.fetch = fetch or fetch_artwork
    self._artwork = ('', None)
    self._lock = threading.Lock()
    self._stop = threading.Event()
    self._thread = None
    self._enabled = False
    self._value = None

  def set_enabled(self, enabled):
    with self._lock:
      self._enabled = enabled
      if not enabled:
        self._value = None
    if enabled and self._thread is None:
      self._thread = threading.Thread(target=self._run, name='aa-now-playing', daemon=True)
      self._thread.start()

  def poll(self):
    with self._lock:
      if not self._enabled:
        return
    try:
      value = self.read()
    except Exception:
      value = None
    with self._lock:
      if self._enabled:
        self._value = value
      held = self._artwork[0]
    url = (value or {}).get('artwork') or ''
    if value is not None and url != held:
      # One try per cover; a failed download keeps the plain card until the song changes.
      try:
        data = self.fetch(url) if url else None
      except Exception:
        data = None
      with self._lock:
        self._artwork = (url, data)

  def snapshot(self):
    with self._lock:
      return self._value

  def artwork(self):
    """(url, (width, height, RGBA bytes) or None) for the current cover."""
    with self._lock:
      return self._artwork

  def _run(self):
    while not self._stop.is_set():
      self.poll()
      self._stop.wait(1.0)

  def close(self):
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=2)
