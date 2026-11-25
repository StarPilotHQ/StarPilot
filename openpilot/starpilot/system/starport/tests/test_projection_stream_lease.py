"""The lease that tells the comma's own screen StarPort's drive view is reaching the car screen."""
import os
from types import SimpleNamespace as NS

from openpilot.starpilot.system.starport import projection_onroad
from openpilot.starpilot.system.starport.projection_stream_lease import (MAX_AGE_S, StreamLeaseReader, StreamLeaseWriter,
                                                                         projection_stream_active)


def test_lease_is_active_only_while_fresh_and_ends_on_release(tmp_path):
  path = str(tmp_path / 'lease')
  writer = StreamLeaseWriter(path)
  assert not projection_stream_active(path)
  writer.touch(now=100.0)
  assert projection_stream_active(path)
  mtime = os.stat(path).st_mtime
  assert projection_stream_active(path, now=mtime + MAX_AGE_S)
  assert not projection_stream_active(path, now=mtime + MAX_AGE_S + 0.5)
  assert not projection_stream_active(path, now=mtime - 1.0), 'a wall clock set back never reads as fresh'
  writer.release()
  assert not projection_stream_active(path)
  writer.release()


def test_reader_stats_at_most_once_a_second(tmp_path):
  path = str(tmp_path / 'lease')
  clock = [10.0]
  reader = StreamLeaseReader(path, clock=lambda: clock[0])
  assert reader() is False
  StreamLeaseWriter(path).touch()
  clock[0] = 10.9
  assert reader() is False, 'cached'
  clock[0] = 11.0
  assert reader() is True
  os.unlink(path)
  clock[0] = 11.5
  assert reader() is True
  clock[0] = 12.0
  assert reader() is False
  StreamLeaseWriter(path).touch()
  clock[0] = 5.0
  assert reader() is True, 'a clock that went back reads again'


def drawing_view(tmp_path, *, started, content='birds_eye', streaming=True):
  view = object.__new__(projection_onroad.ProjectionOnroad)
  view.native = NS(ui_state=NS(started=started))
  view.content = content
  view._streaming = lambda: streaming
  view._stream_lease = StreamLeaseWriter(str(tmp_path / 'lease'))
  view._render_birds_eye_only = lambda: None
  view._standby = lambda: None
  view.favorites = view.pip = None
  view.onroad = NS()
  view.customization = None
  view.exit = NS(draw=lambda placed: None, document_placement=lambda customization: None)
  return view


def test_view_keeps_the_lease_fresh_only_while_the_car_takes_its_drive(tmp_path):
  driving = drawing_view(tmp_path, started=True)
  driving.render()
  assert projection_stream_active(driving._stream_lease.path)
  driving._stream_lease.release()
  silent = drawing_view(tmp_path, started=True, streaming=False)
  silent.render()
  assert not projection_stream_active(silent._stream_lease.path), 'drawn, but the car has accepted no frame'
  parked = drawing_view(tmp_path, started=False)
  parked.render()
  assert not projection_stream_active(parked._stream_lease.path), 'the standby screen is not a drive'
  unset = drawing_view(tmp_path, started=True)
  unset._stream_lease = None
  unset.render()
