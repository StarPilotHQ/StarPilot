"""Opt-in, offroad-only restarts on a numeric five-field cron schedule."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import uuid
from zoneinfo import ZoneInfo

from openpilot.starpilot.state_migration import _atomic_write, _private_dir

DEFAULT_CRON = '0 0 * * *'
_LIMITS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def cron_fields(expression: str) -> list[set[int]]:
  if not isinstance(expression, str) or len(expression) > 128 or len(expression.split()) != 5:
    raise ValueError('Use five cron fields: minute hour day month weekday')
  fields = []
  for field, (low, high) in zip(expression.split(), _LIMITS, strict=True):
    values = set()
    for item in field.split(','):
      parts = item.split('/')
      if len(parts) > 2:
        raise ValueError('Invalid cron step')
      step = int(parts[1]) if len(parts) == 2 else 1
      base = parts[0]
      if base == '*':
        start, end = low, high
      elif '-' in base:
        start, end = map(int, base.split('-'))
      else:
        start = int(base)
        end = high if len(parts) == 2 else start
      if not low <= start <= end <= high or not 1 <= step <= high - low + 1:
        raise ValueError('Cron field is outside its allowed range')
      values.update(range(start, end + 1, step))
    fields.append(values)
  return fields


def cron_matches(expression: str, now: datetime) -> bool:
  minute, hour, day, month, weekday = cron_fields(expression)
  dow = (now.weekday() + 1) % 7
  day_matches, weekday_matches = now.day in day, dow in weekday or dow == 0 and 7 in weekday
  parts = expression.split()
  restricted = not parts[2].startswith('*') and not parts[4].startswith('*')
  calendar_matches = (day_matches or weekday_matches) if restricted else day_matches and weekday_matches
  return now.minute in minute and now.hour in hour and now.month in month and calendar_matches


def next_restart(expression: str, now: datetime) -> datetime | None:
  minutes, hours, days, months, weekdays = cron_fields(expression)
  restricted = not expression.split()[2].startswith('*') and not expression.split()[4].startswith('*')
  # Eight years includes the longest gap between leap days.
  for offset in range(366 * 8):
    day = now + timedelta(days=offset)
    dow = (day.weekday() + 1) % 7
    day_matches = day.day in days
    weekday_matches = dow in weekdays or dow == 0 and 7 in weekdays
    if day.month not in months or not ((day_matches or weekday_matches) if restricted else day_matches and weekday_matches):
      continue
    for hour in sorted(hours):
      for minute in sorted(minutes):
        candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now and candidate.astimezone(UTC).astimezone(now.tzinfo) == candidate:
          return candidate
  return None


class InstallSettings:
  def __init__(self, root: Path | None = None):
    self._root = Path(root) if root is not None else None

  @property
  def root(self) -> Path:
    if self._root is None:
      from openpilot.starpilot.storage import starpilot_storage_root
      self._root = starpilot_storage_root() / 'software' / 'auto-install'
    return self._root

  @property
  def path(self) -> Path:
    return self.root / 'settings.json'

  def read(self) -> dict | None:
    try:
      fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
      with os.fdopen(fd, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
          return None
        raw = source.read(4097)
      if len(raw) > 4096:
        return None
      value = json.loads(raw)
      keys = {'version', 'enabled', 'cron', 'revision', 'lastAttempt'}
      if (not isinstance(value, dict) or set(value) not in (keys, keys | {'timezone'}) or
          type(value['version']) is not int or value['version'] != 1 or type(value['enabled']) is not bool or not isinstance(value['revision'], str) or
          not re.fullmatch('[0-9a-f]{32}', value['revision']) or
          value['lastAttempt'] is not None and (not isinstance(value['lastAttempt'], str) or len(value['lastAttempt']) > 64)):
        return None
      ZoneInfo(value.get('timezone', 'UTC'))
      cron_fields(value['cron'])
      return value
    except FileNotFoundError:
      return {'version': 1, 'enabled': False, 'cron': DEFAULT_CRON, 'revision': '0' * 32, 'lastAttempt': None}
    except (OSError, ValueError, TypeError, KeyError):
      return None

  @contextmanager
  def locked(self):
    _private_dir(self.root)
    fd = os.open(self.root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
      fcntl.flock(fd, fcntl.LOCK_EX)
      yield
    finally:
      os.close(fd)

  def write(self, value: dict) -> None:
    _atomic_write(self.path, json.dumps(value, sort_keys=True).encode())

  def save(self, enabled: bool, expression: str, expected_revision: str | None, admitted, timezone: str | None = None) -> dict:
    if type(enabled) is not bool:
      raise ValueError('Choose whether to install updates automatically')
    cron_fields(expression)
    if timezone is not None:
      ZoneInfo(timezone)
    with self.locked():
      current = self.read()
      if (current['revision'] if current else None) != expected_revision:
        raise ValueError('Install preferences changed; refresh before saving')
      if not admitted():
        raise PermissionError('Park the vehicle before changing install preferences')
      value = {'version': 1, 'enabled': enabled, 'cron': ' '.join(expression.split()), 'revision': uuid.uuid4().hex,
               'lastAttempt': current['lastAttempt'] if current else None}
      value['timezone'] = timezone or (current.get('timezone', 'UTC') if current else 'UTC')
      self.write(value)
      if self.read() != value:
        raise OSError('Install preferences could not be verified')
      return value


def scheduled_restart(params, settings: InstallSettings, ready, parked, now: datetime) -> bool:
  value = settings.read()
  if value:
    now = now.astimezone(ZoneInfo(value.get('timezone', 'UTC')))
  if not value or not value['enabled'] or not cron_matches(value['cron'], now):
    return False
  occurrence = now.strftime('%Y-%m-%dT%H:%M%z')

  def admitted():
    return (params.get_bool('IsOffroad') and not params.get_bool('DisableUpdates') and
            not params.get_bool('DoReboot') and ready() and parked())

  with settings.locked():
    current = settings.read()
    if current is None or current != value or current['lastAttempt'] == occurrence or not admitted():
      return False
    # Reserve the occurrence before rebooting so a failed restart cannot loop within the same minute.
    settings.write({**current, 'lastAttempt': occurrence})
    if not admitted():
      return False
    params.put_bool('DoReboot', True, block=True)
    return True
