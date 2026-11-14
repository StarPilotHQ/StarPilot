import unittest
from unittest.mock import patch

from openpilot.system.ui.lib import cellular_manager as module


class TestModemCache(unittest.TestCase):
  def setUp(self):
    self.manager = module.CellularManager()
    self.profile_poll = patch.object(self.manager, '_poll_profiles').start()
    self.read = patch.object(module.HARDWARE, 'get_modem_state', return_value={'ip_address': '10.0.0.2', 'connected': True}).start()
    self.clock = patch.object(module.time, 'monotonic', return_value=100.0).start()
    self.addCleanup(patch.stopall)

  def test_pending_profile_poll_refreshes_connected_modem(self):
    self.manager._polling = True
    self.manager.process_callbacks()
    self.assertEqual(self.manager.modem_state['ip_address'], '10.0.0.2')
    self.profile_poll.assert_not_called()

  def test_busy_operation_refreshes_connected_modem(self):
    self.manager._busy = True
    self.manager.process_callbacks()
    self.assertEqual(self.manager.modem_state['ip_address'], '10.0.0.2')
    self.profile_poll.assert_not_called()

  def test_connection_appears_while_profile_poll_is_pending(self):
    self.read.return_value = {'ip_address': '', 'connected': False}
    self.manager.process_callbacks()
    self.manager._polling = True
    self.read.return_value = {'ip_address': '10.0.0.2', 'connected': True}
    self.clock.return_value = 105.0
    self.manager.process_callbacks()
    self.assertEqual(self.manager.modem_state['ip_address'], '10.0.0.2')
    self.assertEqual(self.profile_poll.call_count, 1)

  def test_no_per_frame_modem_reads(self):
    self.manager.process_callbacks()
    self.clock.return_value = 104.99
    self.manager.process_callbacks()
    self.read.assert_called_once()
    self.profile_poll.assert_called_once()

  def test_modem_refresh_is_independent_of_profile_refresh_delay(self):
    self.manager.process_callbacks()
    self.manager._last_profile_poll = 103.0
    self.clock.return_value = 105.0
    self.manager.process_callbacks()
    self.assertEqual(self.read.call_count, 2)
    self.profile_poll.assert_called_once()

  def test_lost_connection_clears_cached_ip_during_operation(self):
    self.manager.process_callbacks()
    self.manager._busy = True
    self.clock.return_value = 105.0
    self.read.return_value = {}
    self.manager.process_callbacks()
    self.assertEqual(self.manager.modem_state, {})
    self.profile_poll.assert_called_once()

  def test_profile_poll_still_runs_after_existing_interval(self):
    self.manager.process_callbacks()
    self.clock.return_value = 105.0
    self.manager.process_callbacks()
    self.assertEqual(self.read.call_count, 2)
    self.assertEqual(self.profile_poll.call_count, 2)


if __name__ == '__main__':
  unittest.main()
