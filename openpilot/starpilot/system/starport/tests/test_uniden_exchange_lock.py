"""A slow detector exchange must not hold the supervisor lock the video loop needs."""
import threading
from unittest.mock import Mock, patch
from openpilot.starpilot.system.starport.supervisor import Supervisor


def test_detector_exchange_leaves_supervisor_lock_free():
  host = Supervisor.__new__(Supervisor)
  host._lock = threading.RLock()
  host._shared_bluetooth_owner = Mock(lock=threading.RLock(), phone_lease=object())
  free = []
  def exchange(owner, *args, **kwargs):
    # Stands in for a BlueZ Connect that blocks; another thread (the stream's _set) must still get in.
    probe = threading.Thread(target=lambda: free.append(host._lock.acquire(timeout=0.5) and (host._lock.release() or True)))
    probe.start()
    probe.join()
    assert owner.lock._is_owned()
    return {'connected': True}
  with patch('openpilot.starpilot.uniden.transport.exchange', exchange):
    assert host.uniden_exchange('E0:00:00:00:0B:4D', 'R8', ('galaxy', 'test')) == {'connected': True}
  assert free == [True]
