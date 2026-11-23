"""Physical MAIN intent for qualified angle and Impreza torque AOL; no ordinary controls grant."""
import pytest

from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests import test_subaru_stock_aol as stock_module


@pytest.fixture(params=(17, 145, 209, 401, 465, 4096, 4104, 4608, 4616, 8192, 8200))
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
  bus = 1 if owner.param in (17, 145, 209, 401, 465) else 2
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
    name = 'ES_Status' if owner.param in (17, 145, 209, 401, 465) else 'CruiseControl'
    values = {'Cruise_Activated': enabled, 'Cruise_On': 1} if name == 'CruiseControl' else {'Cruise_Activated': enabled}
    assert owner.safety.safety_rx_hook(owner.frame(name, values, 1 if owner.param in (17, 145, 209, 401, 465) else 0))
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


def test_legacy_2025_cvt_manual_gears():
  item = stock_module.TestSubaruStockAol(methodName='runTest')
  item.setUp()
  try:
    item.configure(145)
    neutral(item)
    assert item.feed() == 1
    for gear in (137, 145, 153, 161, 169, 177, 185, 193):
      assert item.feed(gear=gear) == 1
    assert item.feed(gear=3) == 0
    assert item.feed(gear=145) == 1

    # Pressing MAIN while in Park (gear=4) then shifting to Drive grants permission.
    assert item.feed(main=False, gear=4, speed=0) == 0
    assert item.feed(main=True, gear=4, speed=0) == 0
    assert item.feed(main=True, gear=3, speed=0) == 0
    assert item.feed(main=True, gear=121, speed=54) == 1

    item.configure(17)
    neutral(item)
    assert item.feed() == 1
    assert item.feed(gear=137) == 0
  finally:
    item.tearDown()


@pytest.mark.parametrize('param, experience, max_torque', [(0, 0, 2047), (8192, 0, 3071), (8192, 32, 3071), (8200, 0, 3071), (8200, 32, 3071)])
def test_early_crosstrek_steering_limit(param, experience, max_torque):
  item = stock_module.TestSubaruStockAol(methodName='runTest')
  item.setUp()
  try:
    item.configure(param, experience)
    neutral(item)
    assert item.feed(cruise=experience == 0) == int(experience == 32)
    assert item.safety.get_controls_allowed() == (experience == 0)

    def tx(torque):
      item.safety.set_desired_torque_last(torque)
      item.safety.set_rt_torque_last(torque)
      return item.safety.safety_tx_hook(item.frame('ES_LKAS', {'LKAS_Output': torque, 'LKAS_Request': 1}))

    for direction in (-1, 1):
      assert tx(direction * max_torque)
      assert not tx(direction * (max_torque + 1))
    assert item.feed(main=False) == 0
    assert not tx(50)
  finally:
    item.tearDown()


@pytest.mark.parametrize('param, experience', [(8193, 32), (8194, 0), (8192, 33), (8200, 33), (8201, 32)])
def test_early_crosstrek_rejects_mixed_safety_settings(param, experience):
  item = stock_module.TestSubaruStockAol(methodName='runTest')
  item.setUp()
  try:
    item.configure(param, experience)
    neutral(item)
    assert item.feed() == 0
    assert not item.safety.safety_tx_hook(item.frame('ES_LKAS', {}))
  finally:
    item.tearDown()
