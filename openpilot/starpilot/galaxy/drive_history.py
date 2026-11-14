"""Bounded local route inventory; no cloud metadata or log-content interpretation.

The logger creates rlog.lock while a segment is open and removes it on close.
A listed segment is lock-free and has at least one recognized regular file. This
is an inventory snapshot, not a claim that recordings decode or stats exist.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import re
import stat

from openpilot.common.hardware.hw import Paths
from openpilot.starpilot.connect.provider import configured_provider, recording_provider, route_url

MAX_ROOT_ENTRIES = 2048
MAX_ROUTES = 100
MAX_SEGMENTS = 512
MAX_SEGMENTS_PER_ROUTE = MAX_SEGMENTS
MAX_STATS_SEGMENTS_PER_ROUTE = 512
MAX_SEGMENT_ENTRIES = 32
# logger_get_identifier emits the local v2 form without a dongle ID. Older
# local timestamp names and downloaded route-directory names may coexist.
LOCAL_V2 = r'[a-f0-9]{8}--[a-f0-9]{10}'
TIMESTAMP = r'[0-9]{4}-[0-9]{2}-[0-9]{2}--[0-9]{2}-[0-9]{2}-[0-9]{2}'
ROUTE = rf'(?:{LOCAL_V2}|{TIMESTAMP}|[a-f0-9]{{16}}[|_](?:{LOCAL_V2}|{TIMESTAMP}))'
SEGMENT_NAME = re.compile(rf'(?P<route>{ROUTE})--(?P<number>[0-9]+)\Z')
FILES = {
  'rlog.zst': 'rlog', 'rlog.bz2': 'rlog',
  'qlog.zst': 'qlog', 'qlog.bz2': 'qlog',
  'fcamera.hevc': 'fcamera', 'dcamera.hevc': 'dcamera',
  'ecamera.hevc': 'ecamera', 'qcamera.ts': 'qcamera',
}
FILE_KINDS = ('rlog', 'qlog', 'fcamera', 'dcamera', 'ecamera', 'qcamera')
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK


class DriveHistoryUnavailable(Exception):
  pass


def _same_directory(fd: int, parent_fd: int | None, name: str | Path) -> bool:
  try:
    opened = os.fstat(fd)
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
  except OSError:
    return False
  return stat.S_ISDIR(opened.st_mode) and stat.S_ISDIR(current.st_mode) and \
    (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)


def _segment_files(root_fd: int, name: str) -> tuple[dict[str, bool] | None, bool, float | None, str | None]:
  """Return file presence and whether a bounded/racy segment was omitted."""
  try:
    fd = os.open(name, DIR_FLAGS, dir_fd=root_fd)
  except OSError:
    return None, True, None, None
  try:
    if not _same_directory(fd, root_fd, name):
      return None, True, None, None
    found = dict.fromkeys(FILE_KINDS, False)
    file_times = []
    locked = False
    count = 0
    with os.scandir(fd) as entries:
      for entry in entries:
        count += 1
        if count > MAX_SEGMENT_ENTRIES:
          return None, True, None, None  # a later entry may be a lock
        if entry.name.endswith('.lock'):
          locked = True
        kind = FILES.get(entry.name)
        if kind is None:
          continue
        try:
          info = entry.stat(follow_symlinks=False)
          current = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
        except OSError:
          return None, True, None, None
        if stat.S_ISREG(info.st_mode) and stat.S_ISREG(current.st_mode) and \
            (info.st_dev, info.st_ino) == (current.st_dev, current.st_ino):
          found[kind] = True
          if info.st_mtime > 946684800:
            file_times.append(info.st_mtime)
    if not _same_directory(fd, root_fd, name):
      return None, True, None, None
    provider = recording_provider('', dir_fd=fd)
    return (found if not locked and any(found.values()) else None), False, min(file_times, default=None), provider
  except OSError:
    return None, True, None, None
  finally:
    os.close(fd)


class DriveHistory:
  def __init__(self, root: Path | None = None, *, max_segments_per_route: int = MAX_SEGMENTS_PER_ROUTE,
               max_segments: int = MAX_SEGMENTS):
    if type(max_segments_per_route) is not int or not 1 <= max_segments_per_route <= MAX_STATS_SEGMENTS_PER_ROUTE:
      raise ValueError('Invalid route segment limit')
    if type(max_segments) is not int or not max_segments_per_route <= max_segments <= MAX_ROOT_ENTRIES:
      raise ValueError('Invalid total segment limit')
    self.root = root if root is not None else Path(Paths.log_root())
    self.max_segments_per_route = max_segments_per_route
    self.max_segments = max_segments

  def delete_videos(self, name: object) -> dict:
    return delete_segment_videos(self.root, name)

  def detail(self, route_id: object) -> dict:
    from openpilot.starpilot.galaxy.route_inventory import CatalogError, RouteInventory
    try:
      return RouteInventory(self.root).detail(route_id)
    except CatalogError as error:
      if error.code == 'invalid_route':
        raise ValueError('Invalid route identifier') from error
      raise DriveHistoryUnavailable(str(error)) from error

  def snapshot(self, *, isolate_route_errors: bool = False) -> dict:
    result: dict = {'schemaVersion': 1, 'source': 'local', 'partialHistory': True,
                    'scanIncomplete': False, 'routes': []}
    try:
      root_fd = os.open(self.root, DIR_FLAGS)
    except FileNotFoundError:
      return result
    except OSError:
      raise DriveHistoryUnavailable from None
    try:
      if not _same_directory(root_fd, None, self.root):
        raise DriveHistoryUnavailable
      routes: dict[str, dict[int, dict]] = {}
      route_errors: dict[str, str] = {}
      segment_count = 0

      def omit(route_id: str, reason: str) -> None:
        nonlocal segment_count
        route_errors[route_id] = reason
        if isolate_route_errors:
          segment_count -= len(routes.pop(route_id, {}))
        else:
          result['scanIncomplete'] = True

      scanned = 0
      with os.scandir(root_fd) as entries:
        for entry in entries:
          scanned += 1
          if scanned > MAX_ROOT_ENTRIES:
            result['scanIncomplete'] = True
            break
          matched = SEGMENT_NAME.fullmatch(entry.name)
          if matched is None or '/' in entry.name:
            continue
          route_id = matched.group('route').replace('_', '|')
          number_text = matched.group('number')
          if len(number_text) > 6:
            omit(route_id, 'Invalid segment number')
            continue
          if isolate_route_errors and route_id in route_errors:
            continue
          number = int(number_text)
          if route_id not in routes and len(routes) >= MAX_ROUTES:
            result['scanIncomplete'] = True
            continue
          existing = routes.get(route_id)
          if existing is not None and (number in existing or len(existing) >= self.max_segments_per_route):
            omit(route_id, 'Duplicate segment or route segment limit exceeded')
            continue
          if segment_count >= self.max_segments:
            result['scanIncomplete'] = True
            continue
          try:
            files, incomplete, file_time, provider = _segment_files(root_fd, entry.name)
          except (ValueError, RuntimeError) as error:
            if not isolate_route_errors:
              raise
            omit(route_id, str(error)[:160])
            continue
          if incomplete:
            omit(route_id, 'Recording directory is unreadable or changed during inventory')
          if files is None:
            if isolate_route_errors:
              omit(route_id, 'Recording segment is open, empty or unavailable')
            continue
          routes.setdefault(route_id, {})[number] = {'number': number, 'segmentName': entry.name, 'files': files, 'fileTime': file_time, 'provider': provider}
          segment_count += 1
      if not _same_directory(root_fd, None, self.root):
        raise DriveHistoryUnavailable
      result['routes'] = [
        {'routeId': route_id, 'segmentCount': len(segments),
         'provider': next(iter({s['provider'] for s in segments.values()})) if len({s['provider'] for s in segments.values()}) == 1 else None,
         'fileTime': min((s['fileTime'] for s in segments.values() if s['fileTime'] is not None), default=None),
         'segments': [segments[number] for number in sorted(segments)]}
        for route_id, segments in sorted(routes.items()) if not isolate_route_errors or route_id not in route_errors
      ]
      if isolate_route_errors:
        result['routeErrors'] = [{'routeId': key, 'reason': value} for key, value in sorted(route_errors.items())]
      return result
    except OSError:
      raise DriveHistoryUnavailable from None
    finally:
      os.close(root_fd)


VIDEO_FILES = ('fcamera.hevc', 'dcamera.hevc', 'ecamera.hevc', 'qcamera.ts')


class SegmentInUse(Exception):
  pass


class SegmentMissing(Exception):
  pass


def delete_segment_videos(root: Path, name: object) -> dict:
  if type(name) is not str or '/' in name or SEGMENT_NAME.fullmatch(name) is None:
    raise ValueError('Invalid segment identity')
  try:
    root_fd = os.open(root, DIR_FLAGS)
  except FileNotFoundError:
    raise SegmentMissing from None
  try:
    try:
      fd = os.open(name, DIR_FLAGS, dir_fd=root_fd)
    except FileNotFoundError:
      raise SegmentMissing from None
    try:
      try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
      except BlockingIOError:
        raise SegmentInUse from None
      if not _same_directory(root_fd, None, root) or not _same_directory(fd, root_fd, name):
        raise SegmentMissing
      with os.scandir(fd) as entries:
        names = []
        for count, entry in enumerate(entries, 1):
          if count > MAX_SEGMENT_ENTRIES or entry.name.endswith('.lock'):
            raise SegmentInUse
          names.append(entry.name)
      deleted, freed = [], 0
      for filename in VIDEO_FILES:
        if filename not in names:
          continue
        try:
          info = os.stat(filename, dir_fd=fd, follow_symlinks=False)
          if not stat.S_ISREG(info.st_mode):
            continue
          os.unlink(filename, dir_fd=fd)
        except FileNotFoundError:
          continue
        deleted.append(FILES[filename])
        freed += info.st_size
      return {'segmentName': name, 'deleted': deleted, 'freedBytes': freed}
    finally:
      os.close(fd)
  finally:
    os.close(root_fd)


def recording_details(inventory: dict, dates: dict, dongle_id: str | None, *, device_ids=None) -> dict:
  """Add observed recording dates and Connect links without cloud lookups."""
  for route in inventory.get('routes', []):
    identifier = route['routeId']
    route['startTime'] = dates.get(identifier)
    if '|' in identifier:
      device, local = identifier.split('|', 1)
    else:
      provider = route.get('provider', 'comma')
      device = device_ids.get(provider) if device_ids is not None else dongle_id if provider == 'comma' else None
      local = identifier
    provider = configured_provider(route.get('provider', 'comma'))
    route['connectUrl'] = route_url(device, local, provider) if provider is not None else None
  return inventory
