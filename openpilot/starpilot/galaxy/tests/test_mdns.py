"""galaxy[-N].local is claimed per interface, remembered, and withdrawn on loss."""

import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jeepney.low_level import HeaderFields, MessageType

from openpilot.starpilot.galaxy import mdns

IFADDRS = {'lo': '127.0.0.1', 'ppp0': '10.228.238.20', 'wlan0': '192.168.35.249', 'galaxy0': '172.31.254.1'}
INDEX = {'wlan0': 12, 'eth0': 3, 'galaxy0': 29}


def ifaddr_lookup(sock, name):
  if name not in IFADDRS:
    raise OSError(19, 'No such device')
  return IFADDRS[name]


def index(name):
  if name not in INDEX:
    raise OSError(19, 'No such device')
  return INDEX[name]


class FakeAvahi:
  """Names other devices hold collide; dropped groups read as not established."""

  def __init__(self, taken=(), down=0):
    self.taken: set[str] = set(taken)
    self.down, self.visible = down, set()
    self.claims, self.groups, self.freed, self.updates, self.closed = [], {}, [], [], 0

  def claim(self, name, entries, group=None):
    if self.down:
      self.down -= 1
      raise ConnectionRefusedError(111, 'avahi down')
    self.claims.append((name, entries))
    if name in self.taken:
      if group is not None:
        self.free(group)
      return None
    if group is not None:
      self.updates.append(group)
      return group
    group = f'/group/{len(self.claims)}'
    self.groups[group] = mdns.ESTABLISHED
    return group

  def state(self, group):
    return self.groups[group]

  def free(self, group):
    self.freed.append(group)
    self.groups.pop(group, None)

  def close(self):
    self.closed += 1


class Reply:
  def __init__(self, *body, error=None):
    self.body = body
    fields = {HeaderFields.error_name: error} if error else {}
    self.header = SimpleNamespace(message_type=MessageType.error if error else MessageType.method_return, fields=fields)


class FakeConnection:
  """Scripted avahi D-Bus replies, keyed by member name."""

  def __init__(self, states=(mdns.ESTABLISHED,), fail=None, error='org.freedesktop.Avahi.CollisionError', group=('/Client1/EntryGroup1',)):
    self.calls, self.states, self.fail, self.error, self.group, self.closed = [], list(states), fail, error, group, False

  def send_and_get_reply(self, message, timeout=None):
    member = message.header.fields[HeaderFields.member]
    self.calls.append((member, message.body))
    if member == self.fail:
      return Reply('Local name collision', error=self.error)
    if member == 'EntryGroupNew':
      return Reply(*self.group)
    if member == 'GetState':
      return Reply(self.states.pop(0) if len(self.states) > 1 else self.states[0])
    return Reply()

  def close(self):
    self.closed = True


class MdnsTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.name_file = Path(self.tmp.name) / 'mdns_name'
    for name, value in (('_published', None), ('_redirected', False)):  # module state the publisher and Redirect share
      patcher = patch.object(mdns, name, value)
      patcher.start()
      self.addCleanup(patcher.stop)

  def tearDown(self):
    self.tmp.cleanup()

  def run_publish(self, avahi, address_seq, check=None, ipv6=lambda name: None, retry=0):
    stop = threading.Event()
    sequence = iter(address_seq)

    def addresses():
      try:
        value = next(sequence)
      except StopIteration:
        stop.set()
        return {}
      if check is not None:
        check()
      return value

    mdns.publish(stop, avahi=avahi, addresses=addresses, ipv6=ipv6, index=index, taken=lambda name, own: name in avahi.visible,
                 name_file=self.name_file, refresh=0, retry=retry)

  def names(self, avahi):
    return [name for name, _ in avahi.claims]

  def test_interface_addresses_skip_missing_and_non_candidate_interfaces(self):
    self.assertEqual(mdns.interface_addresses(ifaddr_lookup), {'wlan0': '192.168.35.249', 'galaxy0': '172.31.254.1'})
    self.assertEqual(mdns.interface_addresses(Mock(side_effect=OSError)), {})
    with patch.object(subprocess, 'run', side_effect=AssertionError('subprocess')):
      mdns.interface_addresses(ifaddr_lookup)

  def test_each_interface_answers_with_its_own_address(self):
    found = {'wlan0': '10.10.99.177', 'galaxy0': '172.31.254.1', 'eth0': '100.64.0.2'}
    entries = mdns.records(found, ipv6=lambda name: 'fe80::1' if name == 'wlan0' else None, index=index)
    self.assertEqual(entries, ((12, mdns.PROTO_INET, '10.10.99.177'), (12, mdns.PROTO_INET6, 'fe80::1'),
                               (29, mdns.PROTO_INET, '172.31.254.1')))
    self.assertEqual(mdns.records({'wlan0': '10.10.99.177', 'galaxy0': '172.31.254.1'}, ipv6=lambda name: None,
                                  index=lambda name: index(name) if name == 'galaxy0' else (_ for _ in ()).throw(OSError())),
                     ((29, mdns.PROTO_INET, '172.31.254.1'),))

  def test_first_device_gets_galaxy_local(self):
    avahi, seen = FakeAvahi(), []
    self.run_publish(avahi, [{'wlan0': '192.168.1.5', 'galaxy0': '172.31.254.1'}] * 2,
                     check=lambda: seen.append(mdns.published_record()))
    self.assertEqual(self.names(avahi), ['galaxy.local'])
    self.assertEqual(seen[-1], ('galaxy.local', ('192.168.1.5', '172.31.254.1')))
    self.assertIsNone(mdns.published_record())
    self.assertEqual(avahi.freed, ['/group/1'])
    self.assertEqual(avahi.closed, 1)
    self.assertEqual(self.name_file.read_text().strip(), 'galaxy.local')

  def test_collision_walks_to_next_name_and_remembers_it(self):
    avahi = FakeAvahi(taken={'galaxy.local'})
    self.run_publish(avahi, [{'wlan0': '192.168.1.6'}])
    self.assertEqual(self.names(avahi), ['galaxy.local', 'galaxy-2.local'])
    self.assertEqual(mdns.load_name(self.name_file), 'galaxy-2.local')

    # Next boot keeps galaxy-2 even when galaxy.local is free.
    avahi = FakeAvahi()
    self.run_publish(avahi, [{'wlan0': '192.168.1.6'}])
    self.assertEqual(self.names(avahi), ['galaxy-2.local'])

  def test_remembered_name_taken_falls_back_to_first_free(self):
    mdns.save_name('galaxy-2.local', self.name_file)
    avahi = FakeAvahi(taken={'galaxy-2.local'})
    self.run_publish(avahi, [{'wlan0': '192.168.1.6'}])
    self.assertEqual(self.names(avahi), ['galaxy-2.local', 'galaxy.local'])
    self.assertEqual(mdns.load_name(self.name_file), 'galaxy.local')

  def test_hotspot_coming_up_republishes_same_name_with_its_address(self):
    avahi = FakeAvahi()
    self.run_publish(avahi, [{'wlan0': '192.168.1.5'}, {'wlan0': '192.168.1.5', 'galaxy0': '172.31.254.1'}])
    self.assertEqual(avahi.claims, [('galaxy.local', ((12, 0, '192.168.1.5'),)),
                                    ('galaxy.local', ((12, 0, '192.168.1.5'), (29, 0, '172.31.254.1')))])
    self.assertEqual(avahi.updates, ['/group/1'], 'the established group is republished in place')

  def test_network_switch_keeps_the_name_even_if_our_old_address_still_answers(self):
    avahi = FakeAvahi()
    asked = []

    def stale_answer(name, own):
      asked.append(name)
      return name == 'galaxy.local' and '10.10.99.177' not in own  # avahi still returns our old work address

    stop, steps = threading.Event(), iter([{'wlan0': '10.10.99.177'}, {'wlan0': '172.20.10.4'}])

    def addresses():
      try:
        return next(steps)
      except StopIteration:
        stop.set()
        return {}

    mdns.publish(stop, avahi=avahi, addresses=addresses, ipv6=lambda name: None, index=index, taken=stale_answer,
                 name_file=self.name_file, refresh=0, retry=0)
    self.assertEqual(self.names(avahi), ['galaxy.local', 'galaxy.local'])
    self.assertEqual(asked, ['galaxy.local'], 'an in-place update never re-checks a name we hold')
    self.assertEqual(mdns.load_name(self.name_file), 'galaxy.local')

  def test_our_earlier_addresses_never_count_as_another_device(self):
    run = Mock(return_value=SimpleNamespace(stdout='galaxy.local\t10.10.99.177\n'))
    avahi = FakeAvahi()
    taken = lambda name, own: mdns.held_elsewhere(name, own, run)  # noqa: E731
    entries = ((12, 0, '172.20.10.4'),)
    self.assertEqual(mdns._claim(avahi, entries, None, threading.Event(), taken, {'10.10.99.177'})[0], 'galaxy.local')
    self.assertEqual(mdns._claim(FakeAvahi(), entries, None, threading.Event(), taken)[0], 'galaxy-2.local')

  def test_failed_in_place_update_falls_back_to_a_fresh_claim(self):
    avahi = FakeAvahi()

    def lose_name_on_update():
      if len(avahi.claims) == 1:
        avahi.taken.add('galaxy.local')

    self.run_publish(avahi, [{'wlan0': '192.168.1.5'}, {'wlan0': '192.168.1.9'}], check=lose_name_on_update)
    # update collides, the fresh claim probes galaxy.local once more, then moves on
    self.assertEqual(self.names(avahi), ['galaxy.local', 'galaxy.local', 'galaxy.local', 'galaxy-2.local'])

  def test_lost_name_is_reclaimed(self):
    avahi = FakeAvahi()

    def collide_later():
      if len(avahi.claims) == 1 and avahi.groups:
        avahi.groups['/group/1'] = 3  # another device won the name

    self.run_publish(avahi, [{'wlan0': '192.168.1.5'}] * 3, check=collide_later)
    self.assertEqual(len(avahi.claims), 2)

  def test_names_answered_elsewhere_are_skipped_without_probing(self):
    avahi = FakeAvahi()
    avahi.visible = {'galaxy.local'}
    self.run_publish(avahi, [{'wlan0': '192.168.1.6'}])
    self.assertEqual(self.names(avahi), ['galaxy-2.local'])

  def test_held_elsewhere_ignores_our_own_addresses(self):
    def run(out):
      return Mock(return_value=SimpleNamespace(stdout=out))
    own = {'192.168.1.6', '172.31.254.1'}
    self.assertTrue(mdns.held_elsewhere('galaxy.local', own, run('galaxy.local\t192.168.1.7\n')))
    self.assertFalse(mdns.held_elsewhere('galaxy.local', own, run('galaxy.local\t172.31.254.1\n')))
    self.assertFalse(mdns.held_elsewhere('galaxy.local', own, run('Failed to resolve host name')))
    self.assertFalse(mdns.held_elsewhere('galaxy.local', own, Mock(side_effect=subprocess.TimeoutExpired('x', 1))))

  def test_all_names_taken_publishes_nothing(self):
    avahi = FakeAvahi(taken=set(mdns.NAMES))
    self.run_publish(avahi, [{'wlan0': '192.168.1.5'}])
    self.assertEqual(len(avahi.claims), len(mdns.NAMES))
    self.assertIsNone(mdns.load_name(self.name_file))

  def test_no_lan_address_publishes_nothing(self):
    avahi = FakeAvahi()
    self.run_publish(avahi, [{}, {'ppp0': '10.228.238.20'}])
    self.assertEqual(avahi.claims, [])

  def test_avahi_down_logs_once_and_retries(self):
    logged = []
    avahi = FakeAvahi(down=3)
    with patch.object(mdns, '_log', side_effect=lambda message, warning=True: logged.append(message)):
      self.run_publish(avahi, [{'wlan0': '192.168.1.5'}] * 5)
    self.assertEqual(sum('avahi unavailable' in m for m in logged), 1)
    self.assertEqual(self.names(avahi), ['galaxy.local'])
    self.assertIn('published galaxy.local on wlan0', logged)
    self.assertFalse(any('192.168.1.5' in m for m in logged), 'LAN addresses stay out of swaglog')

  def test_retry_waits_after_failure(self):
    avahi = FakeAvahi(down=1)
    self.run_publish(avahi, [{'wlan0': '192.168.1.5'}] * 3, retry=3600)
    self.assertEqual(avahi.claims, [])

  def test_stop_wakes_publisher_promptly(self):
    stop = threading.Event()
    worker = threading.Thread(target=mdns.publish, args=(stop,), kwargs={
      'avahi': FakeAvahi(), 'addresses': dict, 'taken': lambda name, own: False, 'name_file': self.name_file, 'refresh': 60})
    worker.start()
    stop.set()
    worker.join(2)
    self.assertFalse(worker.is_alive())

  def test_avahi_claim_publishes_every_record_then_waits_for_probe(self):
    connection = FakeConnection(states=[1, mdns.ESTABLISHED])
    avahi = mdns.Avahi(connect=lambda: connection)
    entries = ((12, 0, '10.10.99.177'), (12, 1, 'fe80::1'), (29, 0, '172.31.254.1'))
    self.assertEqual(avahi.claim('galaxy.local', entries), '/Client1/EntryGroup1')
    added = [body for member, body in connection.calls if member == 'AddAddress']
    self.assertEqual(added, [(n, p, mdns.PUBLISH_NO_REVERSE, 'galaxy.local', a) for n, p, a in entries])
    self.assertEqual([m for m, _ in connection.calls].count('Commit'), 1)

  def test_avahi_claim_frees_group_on_collision_or_error(self):
    for connection in (FakeConnection(states=[3]), FakeConnection(fail='AddAddress')):
      avahi = mdns.Avahi(connect=lambda c=connection: c)
      self.assertIsNone(avahi.claim('galaxy.local', ((12, 0, '10.10.99.177'),)))
      self.assertEqual(connection.calls[-1][0], 'Free')

  def test_avahi_other_errors_are_failures_not_taken_names(self):
    connection = FakeConnection(fail='AddAddress', error='org.freedesktop.Avahi.NotPermittedError')
    with self.assertRaises(mdns.AvahiError):
      mdns.Avahi(connect=lambda: connection).claim('galaxy.local', ((12, 0, '10.10.99.177'),))
    self.assertEqual(connection.calls[-1][0], 'Free')

  def test_malformed_avahi_reply_is_an_error(self):
    for group in ((), (7,)):
      with self.assertRaises(mdns.AvahiError):
        mdns.Avahi(connect=lambda g=group: FakeConnection(group=g)).claim('galaxy.local', ((12, 0, '10.10.99.177'),))

  def test_claim_stops_waiting_for_probe_on_stop(self):
    stop = threading.Event()
    stop.set()
    connection = FakeConnection(states=[1])
    self.assertIsNone(mdns.Avahi(connect=lambda: connection, stopped=stop.is_set).claim('galaxy.local', ((12, 0, '10.10.99.177'),)))
    self.assertEqual([m for m, _ in connection.calls].count('GetState'), 1)
    avahi = FakeAvahi()
    self.assertEqual(mdns._claim(avahi, ((12, 0, '10.10.99.177'),), None, stop, lambda name, own: False), (None, None))
    self.assertEqual(avahi.claims, [])

  def test_claim_with_our_group_resets_it_in_place(self):
    connection = FakeConnection()
    group = mdns.Avahi(connect=lambda: connection).claim('galaxy.local', ((12, 0, '172.20.10.4'),), group='/Client1/EntryGroup1')
    self.assertEqual(group, '/Client1/EntryGroup1')
    self.assertEqual([m for m, _ in connection.calls][:3], ['Reset', 'AddAddress', 'Commit'])
    self.assertNotIn('EntryGroupNew', [m for m, _ in connection.calls])
    self.assertNotIn('Free', [m for m, _ in connection.calls])

  def test_avahi_reconnects_after_connection_loss(self):
    connections = [Mock(send_and_get_reply=Mock(side_effect=ConnectionResetError())), FakeConnection()]
    avahi = mdns.Avahi(connect=lambda: connections.pop(0))
    with self.assertRaises(ConnectionResetError):
      avahi.state('/group')
    self.assertIsNone(avahi.connection)
    self.assertEqual(avahi.state('/group'), mdns.ESTABLISHED)

  def test_bad_remembered_name_ignored(self):
    self.name_file.write_text('evil.example\n')
    self.assertIsNone(mdns.load_name(self.name_file))

  def test_redirect_targets_only_own_addresses_and_resyncs_on_change(self):
    calls = []

    def run(cmd, **kwargs):
      calls.append(cmd[7:])  # drop sudo -n iptables -w 2 -t nat
      return SimpleNamespace(returncode=1 if cmd[7] == '-C' else 0)

    redirect = mdns.Redirect(8082, run=run, which=lambda name: f'/usr/sbin/{name}')
    redirect.sync(['192.168.35.249', '172.31.254.1'])
    self.assertIn(['-I', 'PREROUTING', '-p', 'tcp', '--dport', '80', '-j', 'GALAXY_HTTP'], calls)
    self.assertEqual([c for c in calls if c[0] == '-A'], [
      ['-A', 'GALAXY_HTTP', '-d', '172.31.254.1', '-p', 'tcp', '-j', 'REDIRECT', '--to-ports', '8082'],
      ['-A', 'GALAXY_HTTP', '-d', '192.168.35.249', '-p', 'tcp', '-j', 'REDIRECT', '--to-ports', '8082']])

    calls.clear()
    redirect.sync(['172.31.254.1', '192.168.35.249'])
    self.assertEqual(calls, [])  # unchanged set: no sudo on the poll

    redirect.sync(['192.168.35.50', '100.64.0.1'])
    self.assertEqual(calls, [['-F', 'GALAXY_HTTP'],
                             ['-A', 'GALAXY_HTTP', '-d', '192.168.35.50', '-p', 'tcp', '-j', 'REDIRECT', '--to-ports', '8082']])

  def test_redirect_failure_is_logged_not_retried_every_poll(self):
    run = Mock(side_effect=subprocess.TimeoutExpired('sudo', 5))
    redirect = mdns.Redirect(8082, run=run, which=lambda name: '/usr/sbin/iptables')
    redirect.sync(['192.168.35.249'])
    redirect.sync(['192.168.35.249'])
    self.assertEqual(run.call_count, 1)  # tried once, not on every poll
    mdns.Redirect(8082, run=run, which=lambda name: None).sync(['192.168.35.249'])
    self.assertEqual(run.call_count, 1)

  def test_link_local_reads_proc(self):
    proc = Path(self.tmp.name) / 'if_inet6'
    proc.write_text('\n'.join(['fe80000000000000020af5fffe66c88a 0c 40 20 80    wlan0',
                                '2001db80000000000000000000000001 0c 40 00 00    wlan0',
                                'fe800000000000000000000000000001 1d 40 20 80  galaxy0']) + '\n')
    self.assertEqual(mdns.link_local('wlan0', proc), 'fe80::20a:f5ff:fe66:c88a')
    self.assertIsNone(mdns.link_local('eth0', proc))
    self.assertIsNone(mdns.link_local('wlan0', Path(self.tmp.name) / 'missing'))

  def test_published_url_drops_the_port_only_while_port_80_redirects(self):
    with patch.object(mdns, '_published', None):
      self.assertIsNone(mdns.published_url(8082))
    with patch.object(mdns, '_published', ('galaxy-2.local', ('192.168.1.5',))):
      run = Mock(return_value=SimpleNamespace(returncode=0))
      redirect = mdns.Redirect(8082, run=run, which=lambda name: '/usr/sbin/iptables')
      self.assertEqual(mdns.published_url(8082), 'http://galaxy-2.local:8082/')
      redirect.sync(['192.168.1.5'])
      self.assertEqual(mdns.published_url(8082), 'http://galaxy-2.local/')
      redirect.close()
      self.assertEqual(mdns.published_url(8082), 'http://galaxy-2.local:8082/')
      with patch.object(mdns, '_log'):
        mdns.Redirect(8082, run=Mock(side_effect=OSError('denied')), which=lambda name: '/usr/sbin/iptables').sync(['192.168.1.5'])
      self.assertEqual(mdns.published_url(8082), 'http://galaxy-2.local:8082/')

  def test_redirect_close_removes_rules_once(self):
    calls = []
    redirect = mdns.Redirect(8082, run=lambda cmd, **kw: calls.append(cmd[7:]) or SimpleNamespace(returncode=0),
                             which=lambda name: '/usr/sbin/iptables')
    redirect.close()
    self.assertEqual(calls, [])  # never installed: nothing to remove
    redirect.sync(['192.168.35.249'])
    calls.clear()
    redirect.close()
    self.assertEqual([c[0] for c in calls], ['-D', '-F', '-X'])
    calls.clear()
    redirect.close()
    self.assertEqual(calls, [])

  def test_oversized_name_file_ignored(self):
    self.name_file.write_text('galaxy.local' + ' ' * 100)
    self.assertIsNone(mdns.load_name(self.name_file))

  def test_unexpected_crash_is_logged_and_cleans_up(self):
    redirect = Mock()
    with patch.object(mdns, 'Redirect', return_value=redirect), \
         patch.object(mdns, 'publish', side_effect=RuntimeError('boom')), \
         patch('openpilot.common.swaglog.cloudlog.exception') as logged:
      mdns.run(threading.Event(), 8082)
    logged.assert_called_once()
    redirect.close.assert_called_once()


if __name__ == '__main__':
  unittest.main()
