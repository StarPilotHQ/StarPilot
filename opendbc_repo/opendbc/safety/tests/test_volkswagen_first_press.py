"""A physical SET/RES release precedes the cold host join; output still needs a new acknowledgement."""

import pytest
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests import test_volkswagen_stock_aol as stock
from opendbc.car import structs


class Owner(stock.TestVolkswagenStockAol):
  direct = False

  def packet(self, name, fields, bus=0):
    if self.direct and name == 'Gateway_73':
      name, fields = 'Motor_EV_01', {'MO_Waehlpos': 5 if fields['GE_Fahrstufe'] == 8 else 3}
    return super().packet(name, fields, bus)


def setup(word, *, brake=False):
  debug = libsafety_py.libsafety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) == 0
  o = Owner()
  o.direct = word == 41
  o.configure(word)
  o.manual = word == 25
  o.safety.set_aol_test_heartbeat(False)
  for _ in range(12):
    o.feed(moving=not brake, brake=brake)
  o.safety.aol_set_host_request(0)
  return o, debug


def press(o, button, brake):
  assert o.safety.safety_rx_hook(o.packet('GRA_ACC_01', {button: 1}))
  o.feed(moving=not brake, brake=brake)
  o.safety.aol_set_host_request(0)
  assert o.safety.aol_get_permission_mask() == 0
  assert not o.safety.safety_tx_hook(o.packet('ACC_06', {'ACC_Sollbeschleunigung_02': 0.5}))


@pytest.mark.parametrize('word', (9, 25, 41))
@pytest.mark.parametrize('button', ('GRA_Tip_Setzen', 'GRA_Tip_Wiederaufnahme'))
@pytest.mark.parametrize('brake', (False, True))
def test_first_press_before_heartbeat_needs_fresh_host_join(word, button, brake):
  o, debug = setup(word, brake=brake)
  try:
    press(o, button, brake)
    o.safety.set_aol_test_heartbeat(True)
    assert o.safety.aol_get_permission_mask() == 0
    o.time += 10_000
    o.safety.set_timer(o.time)
    o.safety.aol_set_host_request(3)
    if brake:
      assert o.safety.aol_get_permission_mask() == 0
      o.feed(moving=False)
      o.safety.aol_set_host_request(3)
    expected = (2 if brake else 3) if debug and not (brake and word == 25) else 0
    assert o.safety.aol_get_permission_mask() == expected, 'FIRST_PHYSICAL_RELEASE_LOST_BEFORE_HOST_JOIN'
    o.safety.set_aol_test_heartbeat(False)
    assert o.safety.aol_get_permission_mask() == 0
    o.safety.set_aol_test_heartbeat(True)
    o.feed(moving=not brake)
    o.safety.aol_set_host_request(3)
    assert o.safety.aol_get_permission_mask() == 0
  finally:
    o.tearDown()


@pytest.mark.parametrize('word', (9, 25, 41))
@pytest.mark.parametrize('fault', ('timeout', 'cancel', 'main', 'reverse', 'permanent', 'relay', 'crc', 'brake'))
def test_pending_physical_release_cannot_survive_fault_or_expiry(word, fault):
  o, _ = setup(word)
  try:
    press(o, 'GRA_Tip_Setzen', False)
    if fault == 'timeout':
      o.time += 300_001
      o.safety.set_timer(o.time)
    elif fault == 'relay':
      o.safety.set_relay_malfunction(True)
    elif fault == 'crc':
      bad = o.packet('TSK_06', {'TSK_Status': 2})
      bad[0].data[0] ^= 255
      assert not o.safety.safety_rx_hook(bad)
    else:
      o.feed(
        **{'cancel': {'cancel': True}, 'main': {'main': False}, 'reverse': {'gear': False}, 'permanent': {'permanent': True}, 'brake': {'brake': True}}[fault]
      )
    assert o.safety.aol_get_permission_mask() == 0
    o.safety.set_relay_malfunction(False)
    o.safety.set_aol_test_heartbeat(True)
    for _ in range(12):
      o.feed()
    o.safety.aol_set_host_request(3)
    assert o.safety.aol_get_permission_mask() & 2 == 0
  finally:
    o.tearDown()
