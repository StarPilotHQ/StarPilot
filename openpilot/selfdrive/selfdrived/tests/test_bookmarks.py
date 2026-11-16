import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from openpilot.selfdrive.selfdrived.bookmarks import BookmarkCounter
from openpilot.selfdrive.selfdrived.events import ET, EVENTS, EventName, Events
from openpilot.cereal import messaging


# Exercise the production method without loading unrelated native control solvers.
source = Path(__file__).parents[1] / 'selfdrived.py'
module = ast.parse(source.read_text())
owner = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'SelfdriveD')
method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == '_update_bookmarks')
namespace = {'messaging': messaging, 'EventName': EventName}
exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
update_bookmarks = namespace['_update_bookmarks']


class Sources(dict):
  seen = {"deviceState": True}


def test_numbering_and_offroad_reset():
  counter = BookmarkCounter()
  assert counter.update(started=True, drive_id=10, bookmarks=1)
  assert counter.count == 1
  assert not counter.update(started=True, drive_id=10, bookmarks=0)
  assert counter.count == 1
  assert counter.update(started=True, drive_id=10, bookmarks=2)
  assert counter.count == 3
  assert not counter.update(started=False, drive_id=10, bookmarks=1)
  assert counter.count == 0
  assert counter.update(started=True, drive_id=20, bookmarks=1)
  assert counter.count == 1


def test_new_drive_resets_even_if_offroad_frame_was_not_observed():
  counter = BookmarkCounter()
  counter.update(started=True, drive_id=10, bookmarks=7)
  counter.update(started=True, drive_id=20, bookmarks=1)
  assert counter.count == 1


@pytest.mark.parametrize('paired', [False, True])
def test_numbered_alert_preserves_native_style_and_pairing_subtitle(paired):
  counter = BookmarkCounter()
  counter.update(started=True, drive_id=10, bookmarks=12)
  event = EventName.userBookmark if paired else EventName.userBookmarkNotPaired
  events = Events(counter.event_rules())
  events.add(event)
  alert, = events.create_alerts([ET.PERMANENT])
  baseline = EVENTS[event][ET.PERMANENT]
  assert alert.alert_text_1 == 'Bookmark #12 Saved'
  assert alert.alert_text_2 == baseline.alert_text_2
  for key in ('alert_size', 'alert_status', 'priority', 'visual_alert', 'audible_alert', 'duration'):
    assert getattr(alert, key) == getattr(baseline, key)
  assert baseline.alert_text_1 == 'Bookmark Saved'  # no mutable global rule changes


@pytest.mark.parametrize('prime_type,expected', [(None, EventName.userBookmarkNotPaired), (b'0', EventName.userBookmark)])
def test_live_consumer_drains_each_bookmark_and_not_idle_frames(prime_type, expected):
  drive = NS()
  drive.bookmarks = BookmarkCounter()
  drive.bookmark_sock = object()
  drive.events = Events(drive.bookmarks.event_rules())
  drive.sm = Sources(deviceState=NS(started=True, startedMonoTime=10))
  drive.params = NS(get=lambda _: prime_type)
  with patch.object(messaging, 'recv_one_or_none', side_effect=[object(), object(), None]):
    update_bookmarks(drive)
  assert drive.bookmarks.count == 2
  assert drive.events.names == [expected]
  with patch.object(messaging, 'recv_one_or_none', return_value=None):
    drive.events.clear()
    update_bookmarks(drive)
    assert drive.bookmarks.count == 2
    assert drive.events.names == []
    drive.sm['deviceState'].started = False
    update_bookmarks(drive)
    assert drive.bookmarks.count == 0
  drive.sm['deviceState'].started = True
  drive.sm['deviceState'].startedMonoTime = 20
  with patch.object(messaging, 'recv_one_or_none', side_effect=[object(), None]):
    update_bookmarks(drive)
  assert drive.bookmarks.count == 1


def test_bookmark_subscription_is_not_conflated_and_poll_precedes_early_returns():
  init = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == '__init__')
  subscriptions = [node for node in ast.walk(init) if isinstance(node, ast.Call) and
                   isinstance(node.func, ast.Attribute) and node.func.attr == 'sub_sock' and
                   node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'userBookmark']
  assert len(subscriptions) == 1
  assert not subscriptions[0].keywords  # sub_sock defaults to conflate=False
  update = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == 'update_events')
  poll = next(node for node in ast.walk(update) if isinstance(node, ast.Call) and
              isinstance(node.func, ast.Attribute) and node.func.attr == '_update_bookmarks')
  assert all(poll.lineno < node.lineno for node in ast.walk(update) if isinstance(node, ast.Return))


def test_large_burst_is_bounded_without_discarding_remaining_bookmarks():
  counter = BookmarkCounter()
  drive = NS(bookmarks=counter, bookmark_sock=object(), events=Events(counter.event_rules()),
             sm=Sources(deviceState=NS(started=True, startedMonoTime=10)), params=NS(get=lambda _: b'0'))
  with patch.object(messaging, 'recv_one_or_none', side_effect=[object() for _ in range(65)] + [None]) as receive:
    update_bookmarks(drive)
    assert receive.call_count == 64
    assert counter.count == 64
    drive.events.clear()
    update_bookmarks(drive)
  assert counter.count == 65
  alert, = drive.events.create_alerts([ET.PERMANENT])
  assert alert.alert_text_1 == 'Bookmark #65 Saved'


class SavedCheckpoint:
  def __init__(self, value=None):
    self.value = value
    self.writes = self.reads = self.removes = 0

  def read(self):
    self.reads += 1
    return self.value

  def write(self, value):
    self.value = value.copy()
    self.writes += 1

  def clear(self):
    self.value = None
    self.removes += 1


def test_restart_recovers_same_drive_and_offroad_resets_without_idle_io():
  params = SavedCheckpoint()
  counter = BookmarkCounter(params)
  counter.update(started=True, drive_id=10, bookmarks=2)
  restarted = BookmarkCounter(params)
  restarted.update(started=True, drive_id=10, bookmarks=1)
  assert restarted.count == 3
  for _ in range(100):
    restarted.update(started=True, drive_id=10, bookmarks=0)
  assert (params.reads, params.writes, params.removes) == (2, 2, 0)
  restarted.update(started=False, drive_id=10, bookmarks=0)
  assert params.value is None
  restarted.update(started=False, drive_id=10, bookmarks=0)
  assert params.removes == 1
  next_drive = BookmarkCounter(params)
  next_drive.update(started=True, drive_id=20, bookmarks=1)
  assert next_drive.count == 1


@pytest.mark.parametrize('saved', [None, {}, {'drive_id': 9, 'count': 10}, {'drive_id': 10, 'count': -1},
                                   {'drive_id': 10, 'count': True}, {'drive_id': '10', 'count': 5}])
def test_invalid_or_previous_drive_saved_count_is_not_reused(saved):
  counter = BookmarkCounter(SavedCheckpoint(saved))
  counter.update(started=True, drive_id=10, bookmarks=1)
  assert counter.count == 1


def test_unseen_startup_device_state_does_not_erase_saved_count():
  params = SavedCheckpoint({'drive_id': 10, 'count': 4})
  counter = BookmarkCounter(params)
  sources = Sources(deviceState=NS(started=False, startedMonoTime=0))
  sources.seen = {'deviceState': False}
  drive = NS(bookmarks=counter, sm=sources)
  update_bookmarks(drive)
  assert params.value['count'] == 4
  assert params.reads == params.writes == params.removes == 0


def test_storage_failure_does_not_interrupt_bookmark_feedback():
  from unittest.mock import Mock
  params = Mock()
  params.read.side_effect = OSError('unavailable')
  params.write.side_effect = OSError('unavailable')
  params.clear.side_effect = OSError('unavailable')
  counter = BookmarkCounter(params)
  with patch('openpilot.selfdrive.selfdrived.bookmarks.cloudlog.exception') as log:
    assert counter.update(started=True, drive_id=10, bookmarks=1)
    assert counter.alert(True).alert_text_1 == 'Bookmark #1 Saved'
    assert counter.update(started=True, drive_id=10, bookmarks=1)
    assert counter.count == 2
    counter.update(started=False, drive_id=10, bookmarks=0)
    assert counter.count == 0
  assert log.call_count == 4


def test_real_checkpoint_restart_and_offroad_boundary(tmp_path):
  from openpilot.starpilot.bookmark_state import BookmarkCheckpoint
  root = tmp_path / 'checkpoint'
  counter = BookmarkCounter(BookmarkCheckpoint(root, boot='boot'))
  counter.update(started=True, drive_id=10, bookmarks=1)
  assert counter.count == 1
  counter.update(started=True, drive_id=10, bookmarks=1)
  assert counter.count == 2
  restarted = BookmarkCounter(BookmarkCheckpoint(root, boot='boot'))
  restarted.update(started=True, drive_id=10, bookmarks=1)
  assert restarted.count == 3
  restarted.update(started=False, drive_id=10, bookmarks=0)
  assert BookmarkCheckpoint(root, boot='boot').read() is None
  new_drive = BookmarkCounter(BookmarkCheckpoint(root, boot='boot'))
  new_drive.update(started=True, drive_id=20, bookmarks=1)
  assert new_drive.count == 1
  rebooted = BookmarkCounter(BookmarkCheckpoint(root, boot='new-boot'))
  rebooted.update(started=True, drive_id=20, bookmarks=1)
  assert rebooted.count == 1
