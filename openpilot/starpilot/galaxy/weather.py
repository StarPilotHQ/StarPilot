"""Cached weather estimate for durable map context, never a vehicle sensor."""

from concurrent.futures import ThreadPoolExecutor
import json
import math
import threading
import time

import requests

from openpilot.starpilot.navigation.position import LastPositionStore, coordinates
from openpilot.starpilot.storage import starpilot_storage_root


class WeatherSource:
  def __init__(self, position=None, *, fetch=None, clock=time.monotonic, wall=time.time):  # noqa: TID251 - Weather API timestamps are Unix time; retry intervals use monotonic.
    self.position = position or LastPositionStore(starpilot_storage_root() / 'navigation')
    self.fetch = fetch or self._fetch
    self.clock, self.wall = clock, wall
    self.lock = threading.Lock()
    self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='galaxy-weather')
    self.future = None
    self.point = self.pending_point = None
    self.value = None
    self.retry_at = 0.
    self.last_attempt = -60.
    self.closed = False

  @staticmethod
  def _fetch(point):
    with requests.get('https://api.open-meteo.com/v1/forecast',
                      params={'latitude': point[0], 'longitude': point[1], 'current': 'temperature_2m',
                              'temperature_unit': 'celsius', 'timeformat': 'unixtime', 'forecast_days': 1},
                      timeout=(3, 3), stream=True, allow_redirects=False) as response:
      response.raise_for_status()
      raw = bytearray()
      for chunk in response.iter_content(4096):
        raw.extend(chunk)
        if len(raw) > 16384:
          raise ValueError('Weather response too large')
      return json.loads(raw)

  def sample(self):
    with self.lock:
      if self.closed:
        return {'status': 'unavailable', 'temperatureC': None}
      now = self.clock()
      saved = self.position.read()
      point = (round(saved['latitude'], 2), round(saved['longitude'], 2)) if coordinates(saved) else None
      if self.future is not None and self.future.done():
        try:
          result = self.future.result()
          current = result['current']
          value, observed = current['temperature_2m'], current['time']
          if (result['current_units']['temperature_2m'] != '\u00b0C' or type(value) not in (int, float) or
              not math.isfinite(value) or not -100 <= value <= 70 or type(observed) not in (int, float) or
              not math.isfinite(observed) or not -300 <= self.wall() - observed <= 1800):
            raise ValueError('Invalid weather sample')
          self.value = {'temperatureC': value, 'observedAt': observed}
          self.point = self.pending_point
          self.retry_at = now + 600
        except (requests.RequestException, OSError, ValueError, KeyError, TypeError):
          self.retry_at = now + 60
        self.future = None
      if (self.future is None and point is not None and
          (now >= self.retry_at or point != self.pending_point and now - self.last_attempt >= 60)):
        self.pending_point = point
        self.last_attempt = now
        self.future = self.executor.submit(self.fetch, point)
      if self.value and point == self.point and -300 <= self.wall() - self.value['observedAt'] <= 1800:
        return dict(self.value, status='current', locationRecordedAt=saved.get('recordedAt'))
      return {'status': 'no-location' if point is None else 'loading' if self.future is not None else 'unavailable',
              'temperatureC': None}

  def close(self):
    with self.lock:
      self.closed = True
    self.executor.shutdown(wait=False, cancel_futures=True)
