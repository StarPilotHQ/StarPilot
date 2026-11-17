"""Optional, cached display weather. Network work never runs on the UI thread."""

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import threading
import time


@dataclass(frozen=True)
class WeatherReading:
  temperature_c: float
  code: int
  is_day: bool


CODES = frozenset((0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67,
                   71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99))
REFRESH_SECONDS = 600
MAX_AGE_SECONDS = 1800


def valid_location(point) -> bool:
  return bool(isinstance(point, tuple) and len(point) == 2 and
              all(type(value) in (int, float) and math.isfinite(value) for value in point) and
              -90 <= point[0] <= 90 and -180 <= point[1] <= 180)


def nearby(left, right) -> bool:
  latitude = math.radians((left[0] + right[0]) / 2)
  longitude_delta = (left[1] - right[1] + 180) % 360 - 180
  return math.hypot(left[0] - right[0], longitude_delta * math.cos(latitude)) * 111.2 <= 25


def fetch_weather(point) -> WeatherReading:
  from urllib.parse import urlencode
  from urllib.request import Request, urlopen

  query = urlencode({'latitude': round(point[0], 2), 'longitude': round(point[1], 2),
                     'current': 'temperature_2m,weather_code,is_day', 'temperature_unit': 'celsius', 'timeformat': 'unixtime'})
  request = Request('https://api.open-meteo.com/v1/forecast?' + query, headers={'User-Agent': 'StarPilot-Weather/1.0'})
  with urlopen(request, timeout=5) as response:
    raw = response.read(16385)
  if len(raw) > 16384:
    raise ValueError('Weather response is too large')
  data = json.loads(raw)
  current = data['current']
  temperature, code, day, stamp = (current[key] for key in ('temperature_2m', 'weather_code', 'is_day', 'time'))
  if (data['current_units']['temperature_2m'] != '°C' or type(temperature) not in (int, float) or
      not math.isfinite(temperature) or not -100 <= temperature <= 70 or type(code) is not int or code not in CODES or
      type(day) is not int or day not in (0, 1) or type(stamp) not in (int, float) or
      not math.isfinite(stamp) or not -300 <= datetime.now(UTC).timestamp() - stamp <= 2700):
    raise ValueError('Invalid or stale current weather')
  return WeatherReading(temperature, code, bool(day))


class WeatherClient:
  def __init__(self, *, fetch=fetch_weather, clock=time.monotonic):
    self._fetch = fetch
    self._clock = clock
    self._lock = threading.Lock()
    self._reading = None
    self._position = None
    self._received = 0.0
    self._next_request = 0.0
    self._running = False

  def observe(self, point) -> WeatherReading | None:
    if not valid_location(point):
      return None
    now = self._clock()
    with self._lock:
      reading = self._reading if (self._position is not None and nearby(point, self._position) and
                                 0 <= now - self._received <= MAX_AGE_SECONDS) else None
      start = not self._running and now >= self._next_request
      if start:
        self._running = True
        self._next_request = now + REFRESH_SECONDS
    if start:
      try:
        threading.Thread(target=self._refresh, args=(point,), name='weather', daemon=True).start()
      except RuntimeError:
        with self._lock:
          self._running = False
    return reading

  def _refresh(self, point):
    try:
      reading = self._fetch(point)
      with self._lock:
        self._reading, self._position, self._received = reading, point, self._clock()
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
      pass
    finally:
      with self._lock:
        self._running = False
