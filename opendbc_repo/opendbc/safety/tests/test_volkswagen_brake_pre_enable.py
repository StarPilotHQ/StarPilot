"""MQB stop-and-go accepts a fresh physical engagement while the brake inhibits output."""
import unittest

import pytest

from opendbc.car import structs
from opendbc.safety.tests import test_volkswagen_stock_aol as stock
from opendbc.safety.tests.libsafety import libsafety_py


class BrakeOwner(stock.TestVolkswagenStockAol):
  direct = False
  def packet(self, name, fields, bus=0):
    if self.direct and name == 'Gateway_73':
      name, fields = 'Motor_EV_01', {'MO_Waehlpos': 5 if fields['GE_Fahrstufe'] == 8 else 3}
    return super().packet(name, fields, bus)


def prepare(word):
  debug = libsafety_py.libsafety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) == 0
  owner = BrakeOwner()
  owner.direct = word == 41
  owner.configure(word)
  owner.manual = word == 25
  for _ in range(12):
    owner.feed(moving=False, brake=True)
  return owner, debug


def request(owner, button):
  assert owner.safety.safety_rx_hook(owner.packet('GRA_ACC_01', {button: 1}))
  owner.feed(moving=False, brake=True)
  owner.safety.aol_set_host_request(3)


def assert_acc_inhibited(owner):
  assert owner.safety.aol_get_permission_mask() & 2 == 0
  assert not owner.safety.safety_tx_hook(owner.packet('ACC_06', {'ACC_Sollbeschleunigung_02': 0.5}))


@pytest.mark.parametrize('word', (9, 41))
@pytest.mark.parametrize('button', ('GRA_Tip_Setzen', 'GRA_Tip_Wiederaufnahme'))
def test_brake_held_request_only_grants_long_after_physical_release(word, button):
  owner, debug = prepare(word)
  try:
    request(owner, button)
    for _ in range(12):
      owner.feed(moving=False, brake=True)
      owner.safety.aol_set_host_request(3)
      assert_acc_inhibited(owner)
      assert owner.safety.aol_get_permission_mask() == 0
      if debug:
        assert owner.safety.get_controls_allowed()
    owner.feed(moving=False)
    owner.safety.aol_set_host_request(3)
    assert owner.safety.aol_get_permission_mask() == (2 if debug else 0), 'VW_BRAKE_PRE_ENABLE_REQUEST_LOST'
    assert bool(owner.safety.safety_tx_hook(owner.packet('ACC_06', {'ACC_Sollbeschleunigung_02': 0.5}))) == debug
    owner.feed()
    owner.safety.aol_set_host_request(3)
    assert owner.safety.aol_get_permission_mask() == (3 if debug else 0)
    owner.feed(brake=True)
    owner.feed()
    owner.safety.aol_set_host_request(3)
    assert owner.safety.aol_get_permission_mask() & 2 == 0
  finally:
    owner.tearDown()


@pytest.mark.parametrize('word', (9, 25, 41))
def test_brake_release_without_explicit_request_never_arms_long(word):
  owner, _ = prepare(word)
  try:
    owner.feed(moving=False)
    owner.safety.aol_set_host_request(3)
    assert owner.safety.aol_get_permission_mask() & 2 == 0
    owner.feed()
    owner.safety.aol_set_host_request(3)
    assert owner.safety.aol_get_permission_mask() & 2 == 0
  finally:
    owner.tearDown()


@pytest.mark.parametrize('word', (9, 41))
@pytest.mark.parametrize('fault', ('cancel', 'main', 'reverse', 'permanent', 'moving', 'heartbeat', 'relay', 'expiry', 'crc'))
def test_brake_held_request_cannot_survive_native_disengagement(word, fault):
  owner, _ = prepare(word)
  try:
    request(owner, 'GRA_Tip_Wiederaufnahme')
    if fault in ('cancel', 'main', 'reverse', 'permanent', 'moving'):
      kwargs = {'moving': False, 'brake': True}
      kwargs.update({'cancel': {'cancel': True}, 'main': {'main': False}, 'reverse': {'gear': False},
                     'permanent': {'permanent': True}, 'moving': {'moving': True}}[fault])
      owner.feed(**kwargs)
    elif fault == 'heartbeat':
      owner.safety.set_aol_test_heartbeat(False)
    elif fault == 'relay':
      owner.safety.set_relay_malfunction(True)
    elif fault == 'expiry':
      owner.time += 300_001
      owner.safety.set_timer(owner.time)
    else:
      bad = owner.packet('TSK_06', {'TSK_Status': 2})
      bad[0].data[0] ^= 0xFF
      assert not owner.safety.safety_rx_hook(bad)
    assert_acc_inhibited(owner)
    owner.safety.set_aol_test_heartbeat(True)
    owner.safety.set_relay_malfunction(False)
    for _ in range(12):
      owner.feed(moving=False, brake=True)
    owner.feed(moving=False)
    owner.safety.aol_set_host_request(3)
    assert owner.safety.aol_get_permission_mask() & 2 == 0
  finally:
    owner.tearDown()


def test_manual_brake_held_request_does_not_add_stop_and_go_support():
  owner, _ = prepare(25)
  try:
    request(owner, 'GRA_Tip_Wiederaufnahme')
    owner.feed(moving=False)
    owner.safety.aol_set_host_request(3)
    assert owner.safety.aol_get_permission_mask() & 2 == 0
  finally:
    owner.tearDown()


class TestVolkswagenBrakePreEnable(unittest.TestCase):
  def test_native_brake_pre_enable(self):
    for word in (9, 41):
      for button in ('GRA_Tip_Setzen', 'GRA_Tip_Wiederaufnahme'):
        test_brake_held_request_only_grants_long_after_physical_release(word, button)
      for fault in ('cancel', 'main', 'reverse', 'permanent', 'moving', 'heartbeat', 'relay', 'expiry', 'crc'):
        test_brake_held_request_cannot_survive_native_disengagement(word, fault)
    for word in (9, 25, 41):
      test_brake_release_without_explicit_request_never_arms_long(word)
    test_manual_brake_held_request_does_not_add_stop_and_go_support()
