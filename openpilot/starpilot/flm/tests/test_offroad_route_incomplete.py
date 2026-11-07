"""A latest drive with a missing segment must not crash the parked tuner.

Seen on a comma 4: the latest local routes had no segment 0 on disk. latest_route()
reached drive_analysis._selected(), which raised _Incomplete('Route has missing
segments or logs'). That is a plain Exception, and offroad_daemon handles
ValueError/OSError/RuntimeError only, so the process died and manager restarted it
every ~30 s (88 crash logs in one evening). An unusable drive must be reported
as ValueError, which the daemon turns into an 'unavailable' status.
"""
from unittest.mock import patch

import pytest

from openpilot.starpilot.flm import offroad_route
from openpilot.starpilot.galaxy.drive_analysis import _Incomplete

ROUTE_ID = '0000003b--03983916f5'


def route_without_first_segment():
  return {
    'routeId': ROUTE_ID,
    'segmentCount': 1,
    'segments': [{'number': 1, 'segmentName': f'{ROUTE_ID}--1', 'files': {'rlog': True, 'qlog': True}}],
  }


class FakeHistory:
  def __init__(self, *args, **kwargs):
    pass

  def snapshot(self):
    return {'scanIncomplete': False, 'routes': [route_without_first_segment()]}


def fake_read_saved(params, key, limit):
  return {'CurrentRoute': (ROUTE_ID.encode(), True), 'RouteCount': (b'60', True)}[key]


def test_latest_route_reports_missing_first_segment_as_value_error(tmp_path):
  with patch.object(offroad_route, 'DriveHistory', FakeHistory), patch.object(offroad_route, 'read_saved', fake_read_saved):
    with pytest.raises(ValueError, match='missing segments') as raised:
      offroad_route.latest_route(object(), tmp_path, permitted=lambda: True)
  assert not isinstance(raised.value, _Incomplete)
  assert isinstance(raised.value.__cause__, _Incomplete)


def test_walk_completed_route_reports_missing_first_segment_as_value_error(tmp_path):
  with pytest.raises(ValueError, match='missing segments'):
    offroad_route.walk_completed_route(tmp_path, route_without_first_segment(), permitted=lambda: True,
                                       consume=lambda *args, **kwargs: None)


def test_other_errors_are_not_rewrapped(tmp_path):
  class Boom(RuntimeError):
    pass

  def exploding_history(*args, **kwargs):
    raise Boom('unrelated')

  with patch.object(offroad_route, 'DriveHistory', exploding_history), patch.object(offroad_route, 'read_saved', fake_read_saved):
    with pytest.raises(Boom):
      offroad_route.latest_route(object(), tmp_path, permitted=lambda: True)
