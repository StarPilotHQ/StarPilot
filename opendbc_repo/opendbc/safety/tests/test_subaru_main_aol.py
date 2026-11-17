"""Physical MAIN intent for qualified angle and Impreza torque AOL; no ordinary controls grant."""
import pytest

from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests import test_subaru_stock_aol as stock_module


@pytest.fixture(params=(17, 209, 465, 4096, 4104, 4608, 4616))
def owner(request):
  item = stock_module.TestSubaruStockAol(methodName='runTest')
  item.setUp()
  item.configure(request.param)
  try:
    yield item
  finally:
    item.tearDown()


def neutral(item, **kwargs):
  for _ in range(12):
    assert item.feed(main=False, **kwargs) == 0


def test_physical_main_without_set_grants_only_fresh_lateral(owner):
  neutral(owner)
  assert owner.feed() == 1
  assert not owner.safety.get_controls_allowed()
  owner.safety.aol_set_host_request(3)
  assert owner.safety.aol_get_permission_mask() == 1
  assert owner.feed(brake=True) == 1
  assert owner.feed(gear=3) == 0
  assert owner.feed(gear=121) == 1
  assert owner.feed(main=False) == 0


def test_main_held_at_startup_cannot_invent_a_driver_gesture(owner):
  for _ in range(20):
    assert owner.feed() == 0
  assert not owner.safety.get_controls_allowed()


def test_main_can_record_stopped_intent_but_waits_for_moving_request(owner):
  neutral(owner, speed=0, host_mask=0)
  assert owner.feed(speed=0, host_mask=0) == 0
  for _ in range(30):
    assert owner.feed(speed=0, host_mask=0) == 0
  assert owner.feed(speed=54, host_mask=1) == 1
  assert not owner.safety.get_controls_allowed()


@pytest.mark.parametrize('fault', ('permanent', 'acc_fault'))
def test_fault_clear_with_main_held_cannot_rearm(owner, fault):
  neutral(owner)
  assert owner.feed() == 1
  assert owner.feed(**{fault: True}) == 0
  for _ in range(10):
    assert owner.feed() == 0
  assert owner.feed(main=False) == 0
  assert owner.feed() == 1


def test_new_physical_acc_source_is_required_before_main_edge(owner):
  neutral(owner, omit_distance=True)
  assert owner.feed(omit_distance=True) == 0
  for _ in range(20):
    assert owner.feed() == 0
  assert owner.feed(main=False) == 0
  assert owner.feed() == 1


def test_acc_wire_checksum_and_counter_cannot_be_faked_by_host(owner):
  neutral(owner)
  assert owner.feed() == 1
  bus = 1 if owner.param in (17, 209, 465) else 2
  bad = owner.frame('ES_Distance', {}, bus)
  raw = bytearray(bytes(bad.data))
  raw[0] ^= 1
  assert not owner.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x221, bus, bytes(raw[:8])))
  assert owner.safety.aol_get_permission_mask() == 0


def test_heartbeat_and_host_lease_still_withdraw(owner):
  neutral(owner)
  assert owner.feed() == 1
  owner.safety.set_aol_test_heartbeat(False)
  assert owner.safety.aol_get_permission_mask() == 0
  owner.safety.set_aol_test_heartbeat(True)
  assert owner.feed() == 0
  assert owner.feed(main=False) == 0
  assert owner.feed() == 1
  owner.safety.set_timer(owner.now + 301_000)
  assert owner.safety.aol_get_permission_mask() == 0


def test_acc_fault_cannot_be_bypassed_by_stock_enable_packets(owner):
  neutral(owner)
  assert owner.feed() == 1
  assert owner.feed(acc_fault=True) == 0
  for enabled in (0, 1):
    name = 'ES_Status' if owner.param in (17, 209, 465) else 'CruiseControl'
    values = {'Cruise_Activated': enabled, 'Cruise_On': 1} if name == 'CruiseControl' else {'Cruise_Activated': enabled}
    assert owner.safety.safety_rx_hook(owner.frame(name, values, 1 if owner.param in (17, 209, 465) else 0))
    owner.safety.aol_set_host_request(1)
    assert owner.safety.aol_get_permission_mask() == 0


@pytest.mark.parametrize('param, experience', [(4096, 0), (4104, 0), (4608, 0), (4616, 0), (4097, 32), (4098, 32)])
def test_impreza_main_setting_cannot_select_unqualified_native_output(param, experience):
  item = stock_module.TestSubaruStockAol(methodName='runTest')
  item.setUp()
  try:
    item.configure(param, experience)
    neutral(item)
    for _ in range(20):
      assert item.feed() == 0
    assert not item.safety.safety_tx_hook(item.frame('ES_LKAS', {}, 0))
  finally:
    item.tearDown()
