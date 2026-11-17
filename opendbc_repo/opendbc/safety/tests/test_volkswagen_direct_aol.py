"""Checked direct-drive gear contract on the source-matched native owner."""
from contextlib import contextmanager
import unittest
import pytest
from opendbc.car import structs
from opendbc.safety.tests import test_volkswagen_stock_aol as stock
from opendbc.safety.tests.common import make_msg
from opendbc.safety.tests.libsafety import libsafety_py


@contextmanager
def direct_owner():
  class DirectOwner(stock.TestVolkswagenStockAol):
    def packet(self, name, fields, bus=0):
      if name == 'Gateway_73':
        return super().packet('Motor_EV_01', {'MO_Waehlpos': 5 if fields['GE_Fahrstufe'] == 8 else 3}, bus)
      return super().packet(name, fields, bus)
  owner = DirectOwner()
  owner.configure(40)
  try:
    yield owner
  finally:
    owner.tearDown()


@pytest.fixture
def owner():
  with direct_owner() as native:
    yield native


def test_direct_cold_main_needs_two_fresh_gear_frames(owner):
  owner.feed()
  assert owner.safety.aol_get_permission_mask() == 0
  owner.feed()
  assert owner.safety.aol_get_permission_mask() == 1
  assert not owner.safety.get_controls_allowed()
  assert owner.safety.safety_tx_hook(owner.packet('HCA_01', {'HCA_01_LM_Offset': 4, 'HCA_01_Sendestatus': 1}))
  owner.safety.aol_set_host_request(3)
  assert owner.safety.aol_get_permission_mask() == 1
  assert not owner.safety.safety_tx_hook(owner.packet('ACC_06', {'ACC_Sollbeschleunigung_02': 0.5}))


def test_direct_reverse_temporary_brake_and_permanent_semantics(owner):
  owner.arm()
  owner.feed(brake=True)
  assert owner.safety.aol_get_permission_mask() == 1
  owner.feed(gear=False)
  assert owner.safety.aol_get_permission_mask() == 0
  owner.feed()
  assert owner.safety.aol_get_permission_mask() == 1
  owner.feed(temporary=True)
  assert owner.safety.aol_get_permission_mask() == 0
  owner.feed()
  assert owner.safety.aol_get_permission_mask() == 1
  owner.feed(permanent=True)
  owner.feed()
  assert owner.safety.aol_get_permission_mask() == 0
  owner.feed(engaged=True)
  assert owner.safety.aol_get_permission_mask() == 1
  owner.feed(cancel=True)
  assert not owner.safety.get_controls_allowed()
  owner.feed()
  assert owner.safety.aol_get_permission_mask() == 1


@pytest.mark.parametrize('gear,drive', ((0, False), (1, False), (2, False), (3, False), (4, False),
                                       (5, True), (6, True), (7, False)))
def test_direct_exact_gear_enums_and_no_gateway_substitute(owner, gear, drive):
  owner.arm()
  original = owner.packet

  def packet(name, fields, bus=0):
    if name == 'Gateway_73':
      return owner.packer.make_can_msg_safety('Motor_EV_01', bus, {'MO_Waehlpos': gear})
    return original(name, fields, bus)
  owner.packet = packet
  owner.feed()
  assert owner.safety.aol_get_permission_mask() == int(drive)
  # A contradictory automatic gearbox frame cannot change the direct source.
  owner.safety.safety_rx_hook(owner.packer.make_can_msg_safety('Gateway_73', 0, {'GE_Fahrstufe': 8 if not drive else 6}))
  assert owner.safety.aol_get_permission_mask() == int(drive)


@pytest.mark.parametrize('fault', ('crc', 'replay', 'counter_jump', 'wrong_bus', 'length', 'missing'))
def test_direct_source_integrity_and_recovery(owner, fault):
  owner.arm()
  packet = owner.packer.make_can_msg_safety('Motor_EV_01', 0, {'MO_Waehlpos': 5})
  owner.safety.safety_rx_hook(packet)
  if fault == 'crc':
    packet[0].data[0] ^= 0xFF
    owner.safety.safety_rx_hook(packet)
  elif fault == 'replay':
    owner.safety.safety_rx_hook(packet)
  elif fault == 'counter_jump':
    for _ in range(3):
      jumped = owner.packer.make_can_msg_safety('Motor_EV_01', 0, {'MO_Waehlpos': 5})
    owner.safety.safety_rx_hook(jumped)
  else:
    for _ in range(40):
      owner.feed(omit='Gateway_73')
      if fault == 'wrong_bus':
        owner.safety.safety_rx_hook(owner.packer.make_can_msg_safety('Motor_EV_01', 1, {'MO_Waehlpos': 5}))
      elif fault == 'length':
        owner.safety.safety_rx_hook(make_msg(0, 0x187, dat=bytes(7)))
  assert owner.safety.aol_get_permission_mask() == 0
  assert owner.safety.safety_fwd_hook(2, 0x126) == 0
  # Full source health plus a deliberate physical MAIN cycle rearms only LAT.
  owner.arm()
  assert owner.safety.aol_get_permission_mask() == 1


def test_direct_captured_counter_cycle_and_crc(owner):
  owner.arm()
  vectors = ['70801500000000f0', '07811500000000f0', '7a821500000000f0', '26831500000000f0',
             'be841500000000f0', '5a851500000000f0', 'fc861500000000f0', '9e871500000000f0',
             'af881500000000f0', '35891500000000f0', 'c58a1500000000f0', '118b1500000000f0',
             'd08c1500000000f0', 'e88d1500000000f0', 'f58e1500000000f0', '008f1500000000f0']
  # Exact captured Motor_EV_01 vectors retained in opendbc/can/tests/test_checksums.py.
  for cycle in range(2):
    for index, hex_data in enumerate(vectors):
      owner.safety.safety_rx_hook(make_msg(0, 0x187, dat=bytes.fromhex(hex_data)))
      if cycle or index > 0:
        # The initial arbitrary counter can only seed a pair; subsequent vectors qualify.
        owner.feed(omit='Gateway_73')
        if not cycle and index == 1:
          owner.feed(main=False, omit='Gateway_73')
          owner.feed(omit='Gateway_73')
        assert owner.safety.aol_get_permission_mask() == 1


@pytest.mark.parametrize('button', ('GRA_Tip_Setzen', 'GRA_Tip_Wiederaufnahme'))
def test_direct_debug_long_requires_real_engage_and_release_denies(owner, button):
  debug = libsafety_py.libsafety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) == 0
  owner.configure(41)
  for _ in range(12):
    owner.feed()
  owner.safety.aol_set_host_request(3)
  assert owner.safety.aol_get_permission_mask() == (1 if debug else 0)
  if debug:
    owner.safety.safety_rx_hook(owner.packet('GRA_ACC_01', {button: 1}))
    owner.feed()
    owner.safety.aol_set_host_request(3)
    assert owner.safety.aol_get_permission_mask() == 3
    owner.feed(brake=True)
    owner.feed()
    owner.safety.aol_set_host_request(3)
    assert owner.safety.aol_get_permission_mask() == 1
  else:
    assert not owner.safety.safety_tx_hook(owner.packet('HCA_01', {'HCA_01_LM_Offset': 4, 'HCA_01_Sendestatus': 1}))
  assert not owner.safety.safety_tx_hook(owner.packet('ACC_06', {'ACC_Sollbeschleunigung_02': 0.5}))


class TestVolkswagenDirectUnittest(unittest.TestCase):
  """The native 100% coverage gate uses unittest discovery, so run the same assertions there."""
  def test_native_cold_main_and_fault_lifecycle(self):
    for test in (test_direct_cold_main_needs_two_fresh_gear_frames,
                 test_direct_reverse_temporary_brake_and_permanent_semantics,
                 test_direct_captured_counter_cycle_and_crc):
      with direct_owner() as owner:
        test(owner)

  def test_native_gear_and_integrity(self):
    for gear in range(8):
      with direct_owner() as owner:
        test_direct_exact_gear_enums_and_no_gateway_substitute(owner, gear, gear in (5, 6))
    for fault in ('crc', 'replay', 'counter_jump', 'wrong_bus', 'length', 'missing'):
      with direct_owner() as owner:
        test_direct_source_integrity_and_recovery(owner, fault)

  def test_debug_long_and_release_denial(self):
    for button in ('GRA_Tip_Setzen', 'GRA_Tip_Wiederaufnahme'):
      with direct_owner() as owner:
        test_direct_debug_long_requires_real_engage_and_release_denies(owner, button)
