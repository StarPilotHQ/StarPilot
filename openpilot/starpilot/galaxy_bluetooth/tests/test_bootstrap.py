"""Setup never downloads packages and never turns on the shared Bluetooth radio."""
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from openpilot.starpilot.galaxy_bluetooth.bootstrap import Bootstrap, LAN_UNIT, RADIO_UNIT, RADIO_WAIT_UNTIL, UNIT


class BootstrapTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.data = self.root / 'data'
    self.data.mkdir()
    self.bridge = self.root / 'bridge'
    self.bridge.mkdir()
    self.params = Mock()
    self.params.get_bool.return_value = True
    self.calls = []
    def run(args, **kwargs):
      self.calls.append(args)
      return SimpleNamespace(stdout='1', returncode=0)
    self.worker = Bootstrap(self.params, self.data, self.bridge, run)

  def installed(self):
    from openpilot.starpilot.galaxy_bluetooth.bridge.pairing import add_phone
    add_phone('iPhone', self.data / 'pairing.json')

  def test_bridge_runs_before_any_phone_is_paired(self):
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=True):
      self.worker.step()
    self.assertTrue(any('start' in call and UNIT in call for call in self.calls))
    from openpilot.starpilot.galaxy_bluetooth.bridge.pairing import load_keys
    self.assertEqual(load_keys(self.data / 'pairing.json'), {})  # empty list: no phone is accepted yet

  def test_status_written_only_on_change(self):
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=False):
      self.worker.step()
      path = self.data / 'setup-status.json'
      path.unlink()
      self.worker.step()
    self.assertFalse(path.exists())

  def test_paired_bridge_starts_onroad_without_downloading(self):
    self.installed()
    self.params.get_bool.return_value = False
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=True):
      self.worker.step()
    self.assertTrue(any('start' in call and UNIT in call for call in self.calls))
    joined = ' '.join(' '.join(call) for call in self.calls)
    for word in ('install', 'pip', 'uv ', 'venv'):
      self.assertNotIn(word, joined)

  def test_retry_restarts_bridge(self):
    from openpilot.starpilot.galaxy_bluetooth.bootstrap import retry_setup
    self.installed()
    retry_setup(self.data)
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=True):
      self.worker.step()
      self.worker.step()
    self.assertEqual([call for call in self.calls if 'restart' in call], [['sudo', '-n', 'systemctl', 'restart', UNIT]])

  def test_open_pairing_during_startup_does_not_restart(self):
    from openpilot.starpilot.galaxy_bluetooth.bootstrap import retry_setup
    self.worker.registered = True
    self.worker.discovery_registered = True
    self.worker.next_attempt = float('inf')
    retry_setup(self.data, restart=False)
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=True):
      self.worker.step()
    self.assertTrue(any('start' in call and UNIT in call for call in self.calls))
    self.assertFalse(any('restart' in call or 'link' in call for call in self.calls))

  def test_wake_preserves_explicit_retry(self):
    from openpilot.starpilot.galaxy_bluetooth.bootstrap import retry_setup
    retry_setup(self.data)
    retry_setup(self.data, restart=False)
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=True):
      self.worker.step()
    self.assertEqual(len([call for call in self.calls if 'restart' in call]), 1)

  def test_power_off_never_starts_bluetooth_or_bridge(self):
    self.installed()
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=False):
      self.worker.step()
    self.assertFalse(any('start' in call and UNIT in call for call in self.calls))
    self.assertFalse(any('bluetooth.service' in call for call in self.calls))
    self.assertTrue(any('stop' in call and UNIT in call for call in self.calls))

  def test_only_bridge_and_discovery_start_when_radio_enabled(self):
    self.installed()
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=True):
      self.worker.step()
    self.assertTrue(any('start' in call and UNIT in call for call in self.calls))
    self.assertFalse(any('bluetooth.service' in call for call in self.calls))

  def test_pairing_a_phone_never_restarts_the_bridge(self):
    from openpilot.starpilot.galaxy_bluetooth.bridge.pairing import add_phone
    self.installed()
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=True):
      self.worker.step()
      add_phone('Android phone', self.data / 'pairing.json')
      self.worker.step()
    self.assertFalse(any('restart' in call for call in self.calls))
    self.assertFalse(any('bluetooth.service' in call for call in self.calls))

  def test_both_units_link_with_one_reload(self):
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=True):
      self.worker.step()
    links = [call for call in self.calls if 'link' in call]
    self.assertEqual(len(links), 2)
    self.assertTrue(all('--no-reload' in call for call in links))
    self.assertEqual(len([call for call in self.calls if 'daemon-reload' in call]), 1)
    self.assertTrue(self.worker.registered and self.worker.discovery_registered)

  def test_setup_waits_for_the_radio_unit_at_boot(self):
    states = iter(['activating', 'activating', 'active'])
    def run(args, **kwargs):
      self.calls.append(args)
      if 'show' in args and RADIO_UNIT in args:
        return SimpleNamespace(stdout=next(states) + '\n', returncode=0)
      self.assertNotIn('show', args)
      return SimpleNamespace(stdout='1', returncode=0)
    self.worker.run = run
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=True), \
         patch('openpilot.starpilot.galaxy_bluetooth.bootstrap.time.monotonic', return_value=12.0), \
         patch('openpilot.starpilot.galaxy_bluetooth.bootstrap.time.sleep') as sleep:
      self.worker.step()
    self.assertEqual(sleep.call_count, 2)
    first_link = next(i for i, call in enumerate(self.calls) if 'link' in call)
    radio_checks = [i for i, call in enumerate(self.calls) if 'show' in call and RADIO_UNIT in call]
    self.assertEqual(len(radio_checks), 3)
    self.assertLess(max(radio_checks), first_link)

  def test_setup_never_waits_for_the_radio_late_in_the_boot(self):
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=True), \
         patch('openpilot.starpilot.galaxy_bluetooth.bootstrap.time.monotonic', return_value=RADIO_WAIT_UNTIL + 1):
      self.worker.step()
    self.assertFalse(any('show' in call and RADIO_UNIT in call for call in self.calls))
    self.assertTrue(self.worker.registered)

  def test_discovery_link_failure_still_registers_the_bridge(self):
    def run(args, **kwargs):
      self.calls.append(args)
      if 'link' in args and str(self.bridge / LAN_UNIT) in args:
        raise subprocess.CalledProcessError(1, args)
      return SimpleNamespace(stdout='1', returncode=0)
    self.worker.run = run
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=True):
      self.worker.step()
    self.assertTrue(self.worker.registered)
    self.assertFalse(self.worker.discovery_registered)
    self.assertEqual(len([call for call in self.calls if 'daemon-reload' in call]), 1)
    self.assertFalse(any('start' in call and LAN_UNIT in call for call in self.calls))
    self.assertTrue(any('start' in call and UNIT in call for call in self.calls))

  def test_registered_units_never_reload_or_check_the_radio(self):
    self.worker.registered = self.worker.discovery_registered = True
    with patch('openpilot.starpilot.bluetooth.radio_preference.RadioPreference.enabled', return_value=True):
      self.worker.step()
    self.assertFalse(any('daemon-reload' in call or 'link' in call for call in self.calls))
    self.assertFalse(any('show' in call and RADIO_UNIT in call for call in self.calls))

if __name__ == '__main__':
  unittest.main()
