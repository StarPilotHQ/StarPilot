import subprocess
import unittest
from unittest import mock

from parameterized import parameterized

from openpilot.common.hardware.comma.modem import Modem


class TestModem(unittest.TestCase):
  @parameterized.expand([
    (('modem',), 'modem'),
    (('ModemManager',), 'ModemManager'),
    (('modem', 'ModemManager'), 'modem'),
    ((), None),
  ])
  def test_system_modem_handoff(self, services, owner):
    modem = Modem()
    modem.running = False
    units = {f'/lib/systemd/system/{name}.service' for name in services}
    with mock.patch('os.path.isfile', side_effect=lambda path: path in units), \
         mock.patch('subprocess.run') as run, mock.patch('os.remove'), \
         mock.patch.object(modem, '_publish_state'), mock.patch.object(modem, '_ppp') as ppp:
      calls = mock.Mock()
      calls.attach_mock(run, 'systemctl')
      calls.attach_mock(ppp.kill, 'kill')

      modem.run()
      modem.stop()

      expected = []
      if owner:
        expected.append(mock.call.systemctl(['sudo', 'systemctl', 'mask', '--runtime', '--now', owner],
                                            capture_output=True, check=True))
      expected.extend([mock.call.kill(), mock.call.kill()])
      if owner:
        expected.extend([
          mock.call.systemctl(['sudo', 'systemctl', 'unmask', '--runtime', owner], capture_output=True),
          mock.call.systemctl(['sudo', 'systemctl', 'start', owner], capture_output=True),
        ])
      assert calls.mock_calls == expected

  def test_failed_handoff_does_not_touch_modem(self):
    modem = Modem()
    failure = subprocess.CalledProcessError(1, ['systemctl'])
    with mock.patch('os.path.isfile', return_value=True), \
         mock.patch('subprocess.run', side_effect=failure), \
         mock.patch.object(modem, '_publish_state') as publish, mock.patch.object(modem, '_ppp') as ppp:
      with self.assertRaises(subprocess.CalledProcessError):
        modem.run()
      ppp.kill.assert_not_called()
      publish.assert_not_called()

  @parameterized.expand([
    (None, ['8.8.8.8', '1.1.1.1']),
    ('', ['8.8.8.8', '1.1.1.1']),
    ('1,5,"apn",10.0.0.2,10.0.0.1,invalid,', ['8.8.8.8', '1.1.1.1']),
    ('1,5,"apn",10.0.0.2,10.0.0.1,9.9.9.9,149.112.112.112', ['9.9.9.9', '149.112.112.112']),
  ])
  def test_cellular_dns_keeps_carrier_servers_or_uses_existing_fallback(self, response, expected):
    modem = Modem()
    with mock.patch.object(modem, '_atv', return_value=response):
      assert modem._read_cellular_dns() == expected
