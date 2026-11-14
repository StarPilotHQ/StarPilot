"""Bounded local route inventory and read leases shared by Galaxy and replay; never decode or alter sources."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import re
import stat

from openpilot.common.hardware.hw import Paths
from openpilot.starpilot.connect.provider import PROVIDER_NAMES, ROUTE_MARKER
from openpilot.starpilot.galaxy.drive_history import (
  DIR_FLAGS, FILE_KINDS, FILES, MAX_ROUTES, MAX_SEGMENT_ENTRIES, ROUTE, SEGMENT_NAME, DriveHistory, _same_directory,
)
from openpilot.starpilot.galaxy.recording_library import RecordingLibrary, display_name

MAX_ROOT_ENTRIES = 32768
MAX_ROUTE_SEGMENTS = 512
MAX_CATALOG_SEGMENTS = 512
_ROUTE_ENTRY = re.compile(rf'(?P<route>{ROUTE})--.*\Z')


class CatalogError(Exception):
  def __init__(self, code: str, message: str):
    super().__init__(message)
    self.code = code


def _route_id(value: object) -> str:
  if type(value) is not str or SEGMENT_NAME.fullmatch(value + '--0') is None:
    raise CatalogError('invalid_route', 'Invalid local route identity')
  return value.replace('_', '|')


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
  # Upload status xattrs change ctime without changing replay content.
  return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _route_names(root_fd: int, selected: str | None = None) -> tuple[dict[str, list[str]], bool]:
  routes: dict[str, list[str]] = {}
  incomplete = False
  with os.scandir(root_fd) as entries:
    for count, entry in enumerate(entries, 1):
      if count > MAX_ROOT_ENTRIES:
        incomplete = True
        break
      matched = _ROUTE_ENTRY.fullmatch(entry.name)
      if matched is None:
        continue
      route_id = matched.group('route').replace('_', '|')
      if selected is not None and route_id != selected:
        continue
      if selected is None and route_id not in routes and len(routes) >= MAX_ROUTES:
        incomplete = True
        continue
      names = routes.setdefault(route_id, [])
      # One extra entry records that a selected route exceeds the admission bound.
      if len(names) <= MAX_ROUTE_SEGMENTS:
        names.append(entry.name)
  return routes, incomplete


def _sources(fd: int) -> dict[str, tuple[int, int, int, int]]:
  sources = {}
  with os.scandir(fd) as entries:
    for count, entry in enumerate(entries, 1):
      if count > MAX_SEGMENT_ENTRIES:
        raise CatalogError('scan_incomplete', 'A route segment has too many entries to verify')
      if entry.name.endswith('.lock'):
        raise CatalogError('in_progress', 'This route is still being recorded')
      if entry.name not in FILES:
        continue
      info = entry.stat(follow_symlinks=False)
      if not stat.S_ISREG(info.st_mode):
        raise CatalogError('route_unavailable', 'A route source is not a regular file')
      if _identity(info) != _identity(os.stat(entry.name, dir_fd=fd, follow_symlinks=False)):
        raise CatalogError('route_changed', 'The route changed while it was being inspected')
      sources[entry.name] = _identity(info)
  if not sources:
    raise CatalogError('route_unavailable', 'A route segment contains no saved driving logs or camera footage')
  return sources


def _provider(segment_fd: int) -> str | None:
  fd = -1
  try:
    fd = os.open(ROUTE_MARKER, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=segment_fd)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size > 32:
      return None
    value = os.read(fd, 32).decode('ascii')
    return value if value in PROVIDER_NAMES else None
  except FileNotFoundError:
    return 'comma'
  except (OSError, UnicodeError):
    return None
  finally:
    if fd >= 0:
      os.close(fd)


def _segment(name: str) -> dict:
  match = SEGMENT_NAME.fullmatch(name)
  number = int(match.group('number')) if match is not None and len(match.group('number')) <= 6 else None
  return {'segmentName': name, 'number': number, 'files': dict.fromkeys(FILE_KINDS, False), 'sourceFiles': [],
          'fileTime': None, 'provider': None, 'logFiles': [], 'logBytes': {}, 'startTime': None, 'endTime': None}


class RouteInventory:
  def __init__(self, root: Path | None = None):
    self.root = Path(root) if root is not None else Path(Paths.log_root())
    self.library = RecordingLibrary(DriveHistory(self.root))

  def _open_root(self) -> int:
    try:
      fd = os.open(self.root, DIR_FLAGS)
    except OSError as error:
      raise CatalogError('route_unavailable', 'Local recordings are unavailable') from error
    if not _same_directory(fd, None, self.root):
      os.close(fd)
      raise CatalogError('route_changed', 'The recording directory changed')
    return fd

  def _inspect(self, root_fd: int, route_id: str, names: list[str], *, hold: bool = False) -> tuple[dict, list]:
    route = {'routeId': route_id, 'displayName': route_id, 'source': 'local', 'segmentCount': len(names),
             'fileTime': None, 'provider': None, 'segments': [_segment(name) for name in names],
             'startTime': None, 'endTime': None, 'availability': 'unavailable', 'playable': False,
             'reason': None, 'warnings': [], 'videoBytes': dict.fromkeys(('fcamera', 'dcamera', 'ecamera', 'qcamera'), 0)}
    opened = []
    error = None
    content_error = None
    numbers = [segment['number'] for segment in route['segments']]
    if len(names) > MAX_ROUTE_SEGMENTS:
      error = CatalogError('scan_incomplete', 'This route exceeds the 512-segment replay limit')
    elif None in numbers or len(set(numbers)) != len(numbers):
      error = CatalogError('route_unavailable', 'This route has malformed or duplicate segment identities')
    else:
      for segment in route['segments']:
        fd = -1
        try:
          fd = os.open(segment['segmentName'], DIR_FLAGS, dir_fd=root_fd)
          if hold:
            try:
              fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as busy:
              raise CatalogError('source_busy', 'This route is being deleted') from busy
          if not _same_directory(fd, root_fd, segment['segmentName']):
            raise CatalogError('route_changed', 'A route segment changed')
          sources = _sources(fd)
          if any(identity[2] == 0 for identity in sources.values()):
            content_error = CatalogError('route_unavailable', 'A route source is empty; replay is unavailable')
          for name, identity in sources.items():
            if identity[2] == 0:
              continue
            segment['files'][FILES[name]] = True
            if FILES[name] in route['videoBytes']:
              route['videoBytes'][FILES[name]] += identity[2]
            if name.startswith(('rlog.', 'qlog.')):
              segment['logBytes'][name] = identity[2]
          segment['sourceFiles'] = sorted(sources)
          segment['logFiles'] = sorted(segment['logBytes'])
          segment['provider'] = _provider(fd)
          segment['fileTime'] = min((identity[3] / 1e9 for identity in sources.values() if identity[3] > 946684800 * 1e9), default=None)
          if hold:
            opened.append((segment['segmentName'], fd, sources))
            fd = -1
        except CatalogError as problem:
          error = error or problem
        except OSError:
          error = error or CatalogError('route_changed', 'A route source is missing or cannot be opened safely')
        finally:
          if fd >= 0:
            os.close(fd)
    route['segments'].sort(key=lambda segment: (segment['number'] is None, segment['number'] or 0, segment['segmentName']))
    # Keep existing user labels and preservation metadata; neither affects admission.
    self.library.describe({'routes': [route]})
    try:
      route['displayName'] = display_name(route['displayName'])
    except ValueError:
      route['displayName'] = route_id
    route['inventoryComplete'] = error is None
    error = error or content_error
    route['fileTime'] = min((segment['fileTime'] for segment in route['segments'] if segment['fileTime'] is not None), default=None)
    providers = {segment['provider'] for segment in route['segments']}
    route['provider'] = next(iter(providers)) if len(providers) == 1 else None
    if error is not None:
      route.update(reason=str(error), reasonCode=error.code)
    else:
      files = [segment['files'] for segment in route['segments']]
      if any(not item['rlog'] and item['qlog'] for item in files):
        route['warnings'].append('Reduced log detail; model and lane overlays may be unavailable.')
      if any(not item['fcamera'] and item['qcamera'] for item in files):
        route['warnings'].append('Some road video uses lower resolution qcamera footage.')
      if any(not (item['rlog'] or item['qlog']) for item in files):
        route['warnings'].append('Some segments have no recorded driving state.')
      if any(not (item['fcamera'] or item['qcamera']) for item in files):
        route['warnings'].append('Some segments have no road camera footage.')
      ordered = sorted(numbers)
      if ordered and (ordered[0] != 0 or any(b != a + 1 for a, b in zip(ordered, ordered[1:], strict=False))):
        route['warnings'].append('This route has missing segments; playback contains gaps.')
      if any((item['rlog'] or item['qlog']) and (item['fcamera'] or item['qcamera']) for item in files):
        route.update(playable=True, availability='limited' if route['warnings'] else 'ready')
      else:
        route.update(reason='No segment has both driving logs and road camera footage', reasonCode='route_unavailable')
    return route, opened

  def catalog(self) -> dict:
    result = {'schemaVersion': 1, 'routes': [], 'scanIncomplete': False}
    try:
      self.root.lstat()
    except FileNotFoundError:
      return result
    except OSError as error:
      raise CatalogError('route_unavailable', 'Local recordings are unavailable') from error
    root_fd = self._open_root()
    try:
      groups, result['scanIncomplete'] = _route_names(root_fd)
      remaining = MAX_CATALOG_SEGMENTS
      for route_id, names in sorted(groups.items()):
        if len(names) <= remaining:
          route, _ = self._inspect(root_fd, route_id, names)
          remaining -= len(names)
        else:
          route, _ = self._inspect(root_fd, route_id, [])
          route.update(segmentCount=len(names), reason='Open this route to inspect its replay sources', reasonCode='scan_incomplete')
        result['routes'].append({key: value for key, value in route.items() if key != 'segments'})
        result['scanIncomplete'] |= route.get('reasonCode') == 'scan_incomplete'
      if not _same_directory(root_fd, None, self.root):
        raise CatalogError('route_changed', 'The recording directory changed')
      return result
    except OSError as error:
      raise CatalogError('scan_incomplete', 'Local recordings could not be completely inspected') from error
    finally:
      os.close(root_fd)

  def detail(self, route_id: object) -> dict:
    route_id = _route_id(route_id)
    root_fd = self._open_root()
    try:
      groups, incomplete = _route_names(root_fd, route_id)
      if incomplete:
        raise CatalogError('scan_incomplete', 'The recording directory exceeds the replay scan limit')
      if route_id not in groups:
        raise CatalogError('route_unavailable', 'This local route is no longer available')
      route, _ = self._inspect(root_fd, route_id, groups[route_id])
      if not _same_directory(root_fd, None, self.root):
        raise CatalogError('route_changed', 'The recording directory changed')
      return route
    except OSError as error:
      raise CatalogError('route_changed', 'The recording directory changed') from error
    finally:
      os.close(root_fd)

  def lease(self, route_id: object) -> SourceLease:
    return SourceLease(self, _route_id(route_id))


class SourceLease:
  """Shared directory locks protect sources from cooperating deletion paths."""
  def __init__(self, catalog: RouteInventory, route_id: str):
    self.catalog, self.route_id = catalog, route_id
    self.root_fd, self.opened = -1, []
    try:
      self.root_fd = catalog._open_root()
      groups, incomplete = _route_names(self.root_fd, route_id)
      if incomplete:
        raise CatalogError('scan_incomplete', 'The recording directory exceeds the replay scan limit')
      if route_id not in groups:
        raise CatalogError('route_unavailable', 'This local route is no longer available')
      self.names = set(groups[route_id])
      self.route, self.opened = catalog._inspect(self.root_fd, route_id, groups[route_id], hold=True)
      if not self.route['playable']:
        raise CatalogError(self.route.get('reasonCode', 'route_unavailable'), self.route['reason'])
      if not self.current():
        raise CatalogError('route_changed', 'The route changed while acquiring replay sources')
    except Exception:
      self.close()
      raise

  def current(self) -> bool:
    if self.root_fd < 0 or not _same_directory(self.root_fd, None, self.catalog.root):
      return False
    try:
      groups, incomplete = _route_names(self.root_fd, self.route_id)
      return not incomplete and set(groups.get(self.route_id, [])) == self.names and all(
        _same_directory(fd, self.root_fd, name) and _sources(fd) == sources for name, fd, sources in self.opened)
    except (OSError, CatalogError):
      return False

  def close(self) -> None:
    for _, fd, _ in self.opened:
      os.close(fd)
    self.opened.clear()
    if self.root_fd >= 0:
      os.close(self.root_fd)
      self.root_fd = -1

  def __enter__(self) -> SourceLease:
    return self

  def __exit__(self, *_args) -> None:
    self.close()
