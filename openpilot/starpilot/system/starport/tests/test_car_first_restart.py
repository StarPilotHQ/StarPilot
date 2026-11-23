"""Cold housekeeping starts the saved adapter only after confirmed car HFP."""
from unittest.mock import Mock
from openpilot.starpilot.system.starport.auto_connect import AutoConnectPolicy
from openpilot.starpilot.system.starport.supervisor import Supervisor


def fixture():
  unit = Supervisor.__new__(Supervisor)
  unit.config = {'receiver_address': 'adapter', 'companion_address': 'car', 'auto_connect': True, 'connection': 'wireless'}
  unit._pairing_active = Mock(return_value=False)
  unit._projection_enabled = lambda: True
  unit._session_alive = Mock(return_value=False)
  unit._bluez_factory = None
  unit._bluez = None
  unit._car_seen_at = -10.0
  unit._companion_retry_at = 0.0
  unit._wifi_ready = lambda now: True  # NetworkManager is not part of this fixture
  phone = Mock()
  phone.snapshot.return_value = True, {'paired': True, 'connected': False}
  unit._phone = Mock(return_value=phone)
  unit._shared_bluetooth_owner = Mock()
  unit._onroad = lambda: False
  unit._set = Mock()
  unit.log = Mock()
  unit.start = Mock()
  unit.auto = AutoConnectPolicy()
  return unit


def test_parked_restart_connects_car_then_starts_adapter():
  unit = fixture()
  unit._shared_bluetooth_owner.prepare_companion.side_effect = [{'connected': False}, {'connected': True}]
  unit._auto_connect(100)
  assert unit._shared_bluetooth_owner.prepare_companion.call_args.kwargs == {'connect': True}
  unit.start.assert_called_once_with(trigger='car_connected')


def test_ignition_cannot_bypass_failed_car_and_retry_is_bounded():
  unit = fixture()
  unit._onroad = lambda: True
  def car(address, selected, *, connect=False):
    if connect:
      raise RuntimeError('car unavailable')
    return {'connected': False}
  unit._shared_bluetooth_owner.prepare_companion.side_effect = car
  unit._auto_connect(100)
  unit.start.assert_not_called()
  unit._auto_connect(101)
  assert [call.kwargs.get('connect', False) for call in unit._shared_bluetooth_owner.prepare_companion.call_args_list] == [False, True, False]
  unit._auto_connect(115)
  assert unit._shared_bluetooth_owner.prepare_companion.call_count == 5
  unit.start.assert_not_called()


def test_existing_connected_car_never_reconnects_or_drops_bond():
  unit = fixture()
  unit._shared_bluetooth_owner.prepare_companion.return_value = {'connected': True}
  unit._auto_connect(100)
  assert unit._shared_bluetooth_owner.prepare_companion.call_count == 1
  assert unit._shared_bluetooth_owner.prepare_companion.call_args.kwargs == {}
  unit.start.assert_called_once()
