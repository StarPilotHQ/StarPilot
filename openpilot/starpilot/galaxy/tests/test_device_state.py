import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpilot.starpilot.galaxy.device_state import DeviceStateSource, TTL_NS
from openpilot.starpilot.parked_evidence import RESUME_SKEW_NS, host_parked


class Clock:
  def __init__(self):
    self.now = 10_000_000_000
    self.offset = 20_000_000_000
    self.sampling_delay = 0

  def mono(self):
    return self.now

  def boot(self):
    self.now += self.sampling_delay
    return self.now + self.offset


class Messages:
  def __init__(self):
    self.updated = dict.fromkeys(('deviceState', 'pandaStates'), False)
    self.seen = dict(self.updated)
    self.valid = dict(self.updated)
    self.logMonoTime: dict[str, int | float] = dict.fromkeys(self.updated, 0)
    self.recv_time = dict(self.logMonoTime)
    self.values = {'deviceState': SimpleNamespace(started=False), 'pandaStates': []}
    self.timeouts = []
    self.reads = []
    self.sock = {'deviceState': SimpleNamespace(close=Mock()), 'pandaStates': SimpleNamespace(close=Mock())}

  def update(self, timeout):
    self.timeouts.append(timeout)

  def __getitem__(self, key):
    self.reads.append(key)
    return self.values[key]

  def publish(self, clock, *, started=False, ignition_line=False, ignition_can=False):
    self.updated = dict.fromkeys(self.updated, True)
    self.seen = dict.fromkeys(self.updated, True)
    self.valid = dict.fromkeys(self.updated, True)
    self.logMonoTime: dict[str, int | float] = {'deviceState': clock.now - 250_000_000, 'pandaStates': clock.now + clock.offset - 100_000_000}
    self.recv_time = dict.fromkeys(self.updated, (clock.now - 125_000_000) / 1e9)
    self.values = {'deviceState': SimpleNamespace(started=started),
                   'pandaStates': [SimpleNamespace(ignitionLine=ignition_line, ignitionCan=ignition_can)]}


class DeviceStateTest(unittest.TestCase):
  def setUp(self):
    self.clock = Clock()
    self.messages = Messages()
    self.source = DeviceStateSource(self.messages, mono=self.clock.mono, boot=self.clock.boot)
    self.clock.now = 11_000_000_000
    self.messages.publish(self.clock)
    self.addCleanup(self.source.close)

  def test_parked_driving_and_standby_are_display_only_and_nonblocking(self):
    for started, line, can, expected in ((False, False, False, 'parked'), (True, True, False, 'driving'),
                                        (False, True, False, 'standby'), (False, False, True, 'standby'),
                                        (True, False, False, 'driving')):
      with self.subTest(expected=expected, started=started, line=line, can=can):
        self.messages.publish(self.clock, started=started, ignition_line=line, ignition_can=can)
        self.assertEqual(self.source.sample(), {'state': expected, 'maxAgeMs': 2750})
    self.assertEqual(self.messages.timeouts, [0] * 5)
    self.assertEqual(set(self.messages.reads), {'deviceState', 'pandaStates'})

  def test_startup_requires_a_post_subscription_device_update(self):
    self.messages.logMonoTime['deviceState'] = 9_999_999_999
    self.assertEqual(self.source.sample(), {'state': None, 'maxAgeMs': 0})
    self.messages.publish(self.clock)
    self.messages.updated['deviceState'] = False
    self.assertIsNone(self.source.sample()['state'])
    self.messages.updated['deviceState'] = True
    self.assertEqual(self.source.sample()['state'], 'parked')

  def test_missing_invalid_or_empty_sources_never_invent_a_state(self):
    for collection in ('seen', 'valid'):
      for service in ('deviceState', 'pandaStates'):
        with self.subTest(collection=collection, service=service):
          self.messages.publish(self.clock)
          getattr(self.messages, collection)[service] = False
          self.assertIsNone(self.source.sample()['state'])
    self.messages.publish(self.clock)
    self.messages.values['pandaStates'] = []
    self.assertIsNone(self.source.sample()['state'])

  def test_source_and_receive_times_reject_stale_and_future_values(self):
    self.assertEqual(self.source.sample()['state'], 'parked')
    self.clock.now += TTL_NS + 1_000_000_000
    for service in ('deviceState', 'pandaStates'):
      for field in ('logMonoTime', 'recv_time'):
        for delta in (-TTL_NS, 1_000_000):
          with self.subTest(service=service, field=field, delta=delta):
            self.messages.publish(self.clock)
            now = self.clock.now + (self.clock.offset if field == 'logMonoTime' and service == 'pandaStates' else 0)
            getattr(self.messages, field)[service] = (now + delta) / (1e9 if field == 'recv_time' else 1)
            self.assertEqual(self.source.sample(), {'state': None, 'maxAgeMs': 0})

  def test_suspend_invalidates_cached_device_until_a_new_update(self):
    self.assertEqual(self.source.sample()['state'], 'parked')
    self.clock.offset += 60_000_000_000
    self.clock.now += 100_000_000
    self.messages.updated['deviceState'] = False
    self.assertIsNone(self.source.sample()['state'])
    self.clock.now += 100_000_000
    self.messages.logMonoTime['pandaStates'] = self.clock.now + self.clock.offset
    self.messages.recv_time['pandaStates'] = self.clock.now / 1e9
    self.assertIsNone(self.source.sample()['state'])
    self.clock.now += 500_000_000
    self.messages.publish(self.clock, ignition_can=True)
    self.assertEqual(self.source.sample()['state'], 'standby')

  def test_unstable_clock_sample_and_failed_message_read_return_unknown(self):
    self.clock.sampling_delay = RESUME_SKEW_NS + 1
    self.assertIsNone(self.source.sample()['state'])
    self.clock.sampling_delay = 0
    self.messages.update = Mock(side_effect=OSError('subscription unavailable'))
    self.assertEqual(self.source.sample(), {'state': None, 'maxAgeMs': 0})
    self.messages.update.assert_called_once_with(0)

  def test_close_releases_each_subscription_once(self):
    self.source.close()
    self.source.close()
    for socket in self.messages.sock.values():
      socket.close.assert_called_once_with()
    with patch('builtins.__import__', side_effect=AssertionError('Closed source reopened a subscription')):
      self.assertEqual(self.source.sample(), {'state': None, 'maxAgeMs': 0})


class SharedDeviceStateTest(unittest.TestCase):
  def setUp(self):
    from openpilot.starpilot.galaxy.evidence import EvidenceSource

    self.clock = Clock()
    self.messages = SharedMessages()
    self.messages.alive = dict(self.messages.seen)
    # The shared collector includes the other physical-admission services;
    # this display adapter consumes only deviceState and pandaStates.
    self.messages.values.update(carState=SimpleNamespace(), selfdriveState=SimpleNamespace(), carParams=SimpleNamespace(),
                                peripheralState=SimpleNamespace(pandaType=1, voltage=12640))
    self.shared = EvidenceSource(self.messages, mono=self.clock.mono, boot=self.clock.boot)
    self.addCleanup(self.shared.close)

  def publish(self, **state):
    self.clock.now += 100_000_000
    self.messages.publish(self.clock, **state)
    self.messages.values.update(carState=SimpleNamespace(), selfdriveState=SimpleNamespace(), carParams=SimpleNamespace(),
                                peripheralState=SimpleNamespace(pandaType=1, voltage=12640))
    self.messages.alive = dict(self.messages.seen)
    self.shared.poll()

  def display(self):
    result = DeviceStateSource(self.shared, mono=self.clock.mono, boot=self.clock.boot)
    self.addCleanup(result.close)
    return result

  def test_actual_constructor_cold_then_first_idle_read_uses_shared_startup_floor(self):
    self.shared.poll()
    self.assertIsNone(self.display().sample()['state'])
    for _ in range(40):
      self.publish()
    source = self.display()  # Created after the latest publisher, unlike its server-owned collector.
    calls = len(self.messages.timeouts)
    self.assertEqual(source.sample()['state'], 'parked')
    self.assertEqual(len(self.messages.timeouts), calls)

  def test_shared_labels_and_display_ttl_remain_unchanged(self):
    self.clock.now += 500_000_000
    for started, ignition, expected in ((False, False, 'parked'), (False, True, 'standby'), (True, False, 'driving')):
      self.publish(started=started, ignition_can=ignition)
      source = self.display()
      self.assertEqual(source.sample(), {'state': expected, 'maxAgeMs': 2750})
    self.clock.now += TTL_NS
    self.assertIsNone(source.sample()['state'])

  def test_short_resume_before_sampler_poll_rejects_lazy_header_until_new_device(self):
    self.clock.now += 500_000_000
    self.publish()
    self.assertEqual(self.display().sample()['state'], 'parked')
    self.clock.offset += 100_000_000
    source = self.display()
    self.assertIsNone(source.sample()['state'])
    self.shared.poll()
    self.assertIsNone(source.sample()['state'])
    self.clock.now += 500_000_000
    self.publish(ignition_line=True)
    self.assertEqual(source.sample()['state'], 'standby')

  def test_actual_server_factory_borrows_one_collector_for_local_and_remote(self):
    import tempfile
    from pathlib import Path
    from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
    from openpilot.starpilot.galaxy.remote import RemotePairing
    from openpilot.starpilot.galaxy.server import make_remote_server, make_server

    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      (root / 'IsOffroad').write_bytes(b'1')
      params = SimpleNamespace(get_param_path=lambda key: str(root / key))
      with patch('openpilot.starpilot.galaxy.evidence.EvidenceSource', return_value=self.shared) as factory, \
           patch('openpilot.common.params.Params', return_value=params):
        local = make_server(port=0, owner=GalaxyAccessOwner(root / 'access'), remote_pairing=RemotePairing(root / 'pairing'))
      self.addCleanup(local.server_close)
      remote = make_remote_server(local, port=0)
      self.addCleanup(remote.server_close)
      factory.assert_called_once_with()
      self.assertIs(local.device_state_source.messages, self.shared)
      self.assertIs(local.evidence_source, self.shared)
      self.assertTrue(self.shared.thread.is_alive())
      remote.server_close()
      self.assertTrue(self.shared.thread.is_alive())
      local.server_close()
      self.assertFalse(self.shared.thread.is_alive())
      self.assertIsNone(local.device_state_source.sample()['state'])

  def test_header_close_does_not_retire_shared_owner_and_owner_close_returns_unknown(self):
    self.clock.now += 500_000_000
    self.publish()
    source = self.display()
    self.assertEqual(source.sample()['state'], 'parked')
    source.close()
    self.assertIsNone(source.sample()['state'])
    for socket in self.messages.sock.values():
      socket.close.assert_not_called()
    other = self.display()
    self.assertEqual(other.sample()['state'], 'parked')
    self.shared.close()
    self.assertIsNone(other.sample()['state'])
    for socket in self.messages.sock.values():
      socket.close.assert_called_once_with()


class SharedMessages(Messages):
  def __getitem__(self, key):
    self.reads.append(key)
    return self.values[key]


class TestHostParked(unittest.TestCase):
  HOST = {'SP_HOST_PARKED': '1', 'SP_HOST_RUNTIME': '1', 'SP_HOST_PREFIX': 'starpilot-dev-abc',
          'OPENPILOT_PREFIX': 'starpilot-dev-abc'}

  def test_only_host_launchers_with_runner_prefix_are_parked(self):
    self.assertTrue(host_parked(self.HOST))
    self.assertFalse(host_parked({}))
    for key in ('SP_HOST_PARKED', 'SP_HOST_RUNTIME', 'OPENPILOT_PREFIX'):
      self.assertFalse(host_parked({k: v for k, v in self.HOST.items() if k != key}))
    self.assertFalse(host_parked(self.HOST | {'OPENPILOT_PREFIX': 'replay-demo'}))

  def test_host_header_reports_parked_without_publishers(self):
    with patch.dict('os.environ', self.HOST):
      self.assertEqual(DeviceStateSource(Messages()).sample(), {'state': 'parked', 'maxAgeMs': TTL_NS // 1_000_000})
