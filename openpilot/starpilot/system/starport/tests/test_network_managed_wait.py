"""A shared Wi-Fi interface that NetworkManager has not taken yet is waited for, then reported as unmanaged."""
import pytest

from openpilot.starpilot.system.starport import network
from openpilot.starpilot.system.starport.network import NetworkError, NetworkLease


class FakeLease(NetworkLease):
  def __init__(self, states):
    super().__init__(lambda *a, **k: self.events.append((a, k)), "wlan0")
    self.states, self.events, self.device_path = list(states), [], "/dev/0"

  def _get(self, path, interface, name):
    assert name == "State"
    return self.states.pop(0) if len(self.states) > 1 else self.states[0]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
  monkeypatch.setattr(network.time, "sleep", lambda seconds: None)


def test_a_device_that_becomes_managed_is_joined_normally():
  lease = FakeLease([10, 10, 20, 30])  # unmanaged, unmanaged, unavailable, disconnected
  lease._wait_managed(lambda: False)
  assert [event[0][0] for event in lease.events] == ["wifi_waiting_managed", "wifi_became_managed"]


def test_a_managed_device_does_not_wait_or_log():
  lease = FakeLease([30])
  lease._wait_managed(lambda: False)
  assert lease.events == []


def test_a_device_that_stays_unmanaged_says_so(monkeypatch):
  ticks = iter(range(0, 1000, 3))
  monkeypatch.setattr(network.time, "monotonic", lambda: next(ticks))
  lease = FakeLease([10])
  with pytest.raises(NetworkError, match="unmanaged by NetworkManager"):
    lease._wait_managed(lambda: False, timeout=8.0)


def test_cancelling_stops_the_wait():
  with pytest.raises(NetworkError, match="cancelled"):
    FakeLease([10])._wait_managed(lambda: True)
