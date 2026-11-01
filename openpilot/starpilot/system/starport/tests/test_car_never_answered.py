import errno
import unittest

from openpilot.starpilot.system.starport.bootstrap import BootstrapTimeout
from openpilot.starpilot.system.starport.supervisor import car_never_answered, drops_cached_channel


class TestCarNeverAnswered(unittest.TestCase):
  def test_local_bluetooth_failures_keep_the_cached_channel(self):
    for code in (errno.ENOSYS, errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT):
      self.assertFalse(car_never_answered(OSError(code, 'local')))

  def test_car_side_failures_drop_the_cached_channel(self):
    for error in (OSError(errno.ECONNREFUSED, 'refused'), OSError(errno.EHOSTDOWN, 'down'), TimeoutError(),
                  BootstrapTimeout('wifi_start', 'head unit did not answer in time')):
      self.assertTrue(car_never_answered(error))


class TestDropsCachedChannel(unittest.TestCase):
  def test_a_sleeping_car_keeps_its_channel(self):
    # The message is what the kernel prints on the device (the errno number differs between macOS and Linux).
    self.assertFalse(drops_cached_channel('rfcomm', OSError(errno.EHOSTDOWN, 'Host is down')))
    self.assertFalse(drops_cached_channel('rfcomm', OSError(112, 'Host is down')))

  def test_a_car_that_refuses_or_resets_loses_it(self):
    for error in (ConnectionRefusedError(errno.ECONNREFUSED, 'Connection refused'), ConnectionResetError(errno.ECONNRESET, 'reset'),
                  BootstrapTimeout('wifi_start', 'head unit did not answer in time')):
      self.assertTrue(drops_cached_channel('rfcomm' if not isinstance(error, BootstrapTimeout) else 'wifi_start', error))

  def test_other_stages_and_local_failures_never_drop_it(self):
    self.assertFalse(drops_cached_channel('authenticating', ConnectionResetError(errno.ECONNRESET, 'reset')))
    self.assertFalse(drops_cached_channel('rfcomm', OSError(errno.ENOSYS, 'local')))
    self.assertFalse(drops_cached_channel('rfcomm', OSError(errno.EBUSY, 'Device or resource busy')))
