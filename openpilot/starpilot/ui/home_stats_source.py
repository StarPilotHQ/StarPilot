"""Cached local driving history for the large Home dashboard."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
import os
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from openpilot.common.hardware import PC
from openpilot.common.realtime import drop_realtime, set_core_affinity
from openpilot.starpilot.ui.home_state import DailyDistance, DriveStatsData, DriveSummary, PersonalRecord


def read_home_stats(metric: bool, *, store: Path | None = None, clock: Callable[[], float] = lambda: datetime.now(UTC).timestamp(),
                    timezone: str = 'UTC') -> DriveStatsData | None:
  from openpilot.starpilot.galaxy.drive_stats import DriveStatsOwner
  from openpilot.starpilot.storage import starpilot_storage_root

  path = store if store is not None else starpilot_storage_root() / 'galaxy/drive-stats.json'
  if not path.is_file():
    return None
  owner = DriveStatsOwner(store=path, permitted=lambda: False, clock=clock, metric=lambda: metric)
  try:
    data = owner.snapshot(timezone)
  finally:
    owner.close()
  if data['coverage']['cacheCorrupt']:
    return None
  scale, unit = (1000., 'kilometers') if metric else (1609.344, 'miles')
  def summary(row):
    return DriveSummary(row['drives'], row['distanceMeters'] / scale, row['durationSeconds'] / 3600., unit)
  today = datetime.fromtimestamp(clock(), ZoneInfo(timezone)).date()
  days = tuple(DailyDistance(datetime.fromisoformat(row['date']).strftime('%a'), row['distanceMeters'] / scale,
                             row['date'] == today.isoformat(), row['date'] > today.isoformat()) for row in data['week']['days'])
  records = []
  for row in data['records']:
    value, detail = row['value'], row['unit']
    if value is None:
      text = '—'
    elif detail == 'm':
      text, detail = f'{value / scale:,.1f}', 'km' if metric else 'miles'
    elif detail == 's':
      text, detail = f'{value / 3600.:,.1f}', 'hours'
    elif detail == '%':
      text = f'{value:g}'
    else:
      text = f'{value:,}'
    records.append(PersonalRecord(row['label'], text, detail))
  return DriveStatsData(summary(data['totals']), summary(data['pastWeek']), summary(data['week']), days, tuple(records))


def _background_priority() -> None:
  if not PC:
    drop_realtime()
    set_core_affinity([0, 1, 2, 3])
    os.setpriority(os.PRIO_PROCESS, 0, 19)


class HomeStatsSource:
  REFRESH_SECONDS = 30.

  def __init__(self, request: Callable[[bool], DriveStatsData | None] = read_home_stats,
               clock: Callable[[], float] = time.monotonic):
    self.request, self.clock = request, clock
    self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='home-history', initializer=_background_priority)
    self.pending: Future | None = None
    self.pending_metric = None
    self.data: DriveStatsData | None = None
    self.data_metric = None
    self.next_read = 0.
    self.closed = False

  def snapshot(self, metric: bool) -> DriveStatsData | None:
    if self.closed:
      return None
    now = self.clock()
    if self.pending is not None and self.pending.done():
      try:
        self.data = self.pending.result()
      except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        self.data = None
      self.data_metric = self.pending_metric
      self.pending = None
    if now < self.next_read - self.REFRESH_SECONDS:
      self.next_read = 0.
    if self.pending is None and (now >= self.next_read or metric != self.pending_metric):
      self.pending_metric = metric
      self.pending = self.executor.submit(self.request, metric)
      self.next_read = now + self.REFRESH_SECONDS
    return self.data if self.data_metric == metric else None

  def close(self) -> None:
    self.closed = True
    self.executor.shutdown(wait=False, cancel_futures=True)
