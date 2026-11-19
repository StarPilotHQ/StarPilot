"""Jetlink's home icon and Android Auto card read one status, off the drawing thread, never keeping stale latency."""
import json
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from openpilot.starpilot.models import jetlink_adapter as adapter, jetlink_status
from openpilot.starpilot.models.jetlink_status import JetlinkStatus, JetlinkStatusFeed, read_status


def status(**kw):
  base = {'enabled': True, 'mode': 'usb', 'present': True, 'port_state': 'connected', 'active': False, 'reason': None, 'running': None, 'ready': False,
          'progress': None}
  return NS(**{**base, **kw})


def read(value, latency=24.6):
  with patch.object(adapter, 'link_on', return_value=True), \
       patch.object(adapter, 'hooks', return_value=NS(status=lambda: value)), \
       patch.object(jetlink_status, 'read_latency', return_value=latency):
    return read_status()


def test_off_reads_nothing_of_jetlink():
  with patch.object(adapter, 'link_on', return_value=False), patch.object(adapter, 'hooks') as hooks:
    assert read_status() == JetlinkStatus()
    hooks.assert_not_called()


@pytest.mark.parametrize('kw,expected', [
  ({'active': True, 'running': 'running'}, JetlinkStatus('Computer', 'active', 24.6, 'connected')),
  ({'running': 'ready'}, JetlinkStatus('Computer', 'ready', port='connected', offer=True)),
  ({'running': None, 'ready': True}, JetlinkStatus('Computer', 'ready', port='connected')),
  ({'progress': {'stage': 'build', 'frac': .4, 'code': 'build'}}, JetlinkStatus('Computer', 'connected', port='connected', building=True)),
  ({'progress': {'stage': 'joining', 'frac': 0, 'code': 'connecting'}}, JetlinkStatus('Computer', 'connected', port='connected')),
  ({'running': 'joining'}, JetlinkStatus('Computer', 'connecting', port='connected')),
  ({'running': 'retrying'}, JetlinkStatus('Computer', 'connecting', port='connected')),
  ({'running': None, 'ready': False}, JetlinkStatus('Computer', 'connected', port='connected')),
  ({'reason': 'service stopped'}, JetlinkStatus('Computer', 'unavailable', port='connected')),
  ({'present': False, 'port_state': 'device'}, JetlinkStatus('Computer', 'disconnected', port='device')),
  ({'present': False, 'port_state': 'empty'}, JetlinkStatus('Computer', 'disconnected')),
  ({'present': False, 'port_state': None}, JetlinkStatus('Computer', 'disconnected')),
  ({'mode': 'ios', 'active': True}, JetlinkStatus('iPhone', 'active', 24.6, 'connected')),
  ({'active': True, 'active_model': 'Cinque v3'}, JetlinkStatus('Computer', 'active', 24.6, 'connected', model='Cinque v3')),
  ({'running': 'ready', 'model': 'ResFit', 'active_model': 'Cinque v3'},
   JetlinkStatus('Computer', 'ready', port='connected', offer=True, model='Cinque v3')),
  ({'running': 'ready', 'model': 'ResFit', 'active_model': None},
   JetlinkStatus('Computer', 'ready', port='connected', offer=True, model='ResFit')),
  ({'enabled': False}, JetlinkStatus()),
])
def test_states(kw, expected):
  assert read(status(**kw)) == expected


def test_no_usable_jetlink_is_unavailable():
  assert read(None) == JetlinkStatus(state='unavailable')


def test_latency_record_round_trip_and_staleness(tmp_path):
  path = str(tmp_path / 'jetlink' / 'latency.json')
  jetlink_status._write_latency(NS(client=NS(last_timings=(1, 2, 24600))), path)
  assert jetlink_status.read_latency(path) == pytest.approx(24.6)
  jetlink_status._write_latency(NS(client=None), path)  # the small model drives
  assert jetlink_status.read_latency(path) is None
  with open(path, 'w') as f:
    json.dump({'at': time.monotonic() - jetlink_status.LATENCY_FRESH_S - 1, 'server_ms': 24.6}, f)
  assert jetlink_status.read_latency(path) is None
  assert jetlink_status.read_latency(str(tmp_path / 'missing.json')) is None


def test_feed_polls_off_thread_expires_and_disables():
  arrived = threading.Event()
  threads = []
  def fake():
    threads.append(threading.get_ident())
    arrived.set()
    return JetlinkStatus('iPhone', 'active', 24.6, 'connected')
  feed = JetlinkStatusFeed(read=fake, period=0.01)
  try:
    assert feed.snapshot() == JetlinkStatus()
    feed.set_enabled(True)
    assert arrived.wait(2)
    assert threading.get_ident() not in threads
    assert feed.snapshot().latency_ms == 24.6
    feed.set_enabled(False)
    assert feed.snapshot() == JetlinkStatus()
  finally:
    feed.close()


def test_feed_stale_and_failed_reads_drop_latency_and_port():
  feed = JetlinkStatusFeed(read=lambda: JetlinkStatus('iPhone', 'active', 24.6, 'connected', model='Cinque v3'))
  feed._enabled = True
  feed.poll()
  assert feed.snapshot().port == 'connected'
  assert feed.snapshot().model == 'Cinque v3'
  feed._read_at = 0
  assert feed.snapshot() == JetlinkStatus('iPhone', 'unavailable')
  feed.read = lambda: 1 / 0
  feed.poll()
  assert feed.snapshot() == JetlinkStatus(state='unavailable')
