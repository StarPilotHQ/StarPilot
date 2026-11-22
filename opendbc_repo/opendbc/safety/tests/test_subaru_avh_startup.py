import unittest
import pytest
from opendbc.car import structs
from opendbc.car.subaru.avh import avh_request, checksum
from opendbc.car.subaru.tests.test_avh import sample
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py


def feed_step(safety, packer, last, tick):
  safety.set_timer(1_000_000 + tick * 10_000)
  for name, values, bus in [('Transmission', {'Gear': 4}, 0), ('Throttle', {'Engine_RPM': 800}, 0), ('Steering_Torque', {}, 0), ('Steering_2', {}, 0),
                            ('Wheel_Speeds', dict.fromkeys(('FL', 'FR', 'RL', 'RR'), 0), 1), ('Brake_Status', {}, 1),
                            ('ES_Status', {}, 1), ('ES_Distance', {}, 1), ('ES_DashStatus', {'Cruise_On': 1}, 2)]:
    safety.safety_rx_hook(packer.make_can_msg_safety(name, bus, values))
  for address in (0x6BB, 0x32B, 0x40, 0x48, 0x174):
    if address == 0x6BB and tick % 100:
      continue
    data = sample(address, tick // 100 if address == 0x6BB else tick)
    safety.safety_rx_hook(libsafety_py.make_CANPacket(address, 1, data))
    last[address] = data
  safety.safety_tick()


def _ready_with_packer(param, ae):
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_timer(1_000_000)
  safety.set_alternative_experience(ae)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.subaru, param) == 0
  packer = CANPackerSafety('subaru_global_2017_generated')
  last = {}
  for tick in range(1001):
    feed_step(safety, packer, last, tick)
  return safety, last, packer


def ready(param, ae):
  safety, last, _ = _ready_with_packer(param, ae)
  return safety, last


@pytest.mark.parametrize('param', [1169, 1425])
@pytest.mark.parametrize('ae', [0, 32])
def test_exact_pair_debug_only_and_status_never_tx(param, ae):
  # Probe the loaded build, not the Subaru authority under test. ready() resets
  # native state and selects Subaru before any RX or TX checks.
  debug = libsafety_py.libsafety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) == 0
  safety, last, packer = _ready_with_packer(param, ae)
  template = last[0x6BB]
  for step in (1, 2, 3, 4):
    if step > 1:
      for offset in range(1, 7):
        feed_step(safety, packer, last, 1000 + (step - 2) * 6 + offset)
    addr, data, bus = avh_request(template, step)
    assert bool(safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, bus, data))) == debug
  assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(0x6BB, 1, avh_request(template, 4)[1]))
  assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(0x32B, 1, sample(0x32B, 1)))


@pytest.mark.parametrize('target', [0x6BB, 0x32B, 0x40, 0x48, 0x174])
def test_conflicting_same_counter_withdraws(target):
  safety, last = ready(1169, 0)
  conflict = bytearray(last[target])
  conflict[7] ^= 1
  conflict[0] = checksum(target, conflict)
  safety.safety_rx_hook(libsafety_py.make_CANPacket(target, 1, bytes(conflict)))
  addr, data, bus = avh_request(last[0x6BB], 1)
  assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, bus, data))


@pytest.mark.parametrize('fault', ['main_gas', 'eps', 'control', 'wrong_bus', 'opaque', 'late_second', 'ack', 'manual',
                                 'alt_gear', 'alt_gas', 'engine', 'main_gear'])
def test_native_refusals(fault):
  safety, last = ready(1169, 0)
  addr, data, bus = avh_request(last[0x6BB], 1)
  packer = CANPackerSafety('subaru_global_2017_generated')
  if fault == 'main_gas':
    safety.safety_rx_hook(packer.make_can_msg_safety('Throttle', 0, {'Throttle_Pedal': 1}))
  elif fault == 'main_gear':
    safety.safety_rx_hook(packer.make_can_msg_safety('Transmission', 0, {'Gear': 121}))
  elif fault == 'eps':
    safety.safety_rx_hook(packer.make_can_msg_safety('Steering_Torque', 0, {'Steer_Warning': 1}))
  elif fault == 'control':
    safety.safety_rx_hook(packer.make_can_msg_safety('ES_Status', 1, {'Cruise_Activated': 1}))
  elif fault == 'wrong_bus':
    bus = 0
  elif fault == 'opaque':
    changed = bytearray(data)
    changed[7] ^= 1
    changed[0] = checksum(addr, changed)
    data = bytes(changed)
  elif fault == 'late_second':
    safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, bus, data))
    safety.set_timer(11_081_000)
    data = avh_request(last[0x6BB], 2)[1]
  else:
    target, index, value = {'ack': (0x32B, 5, 0x20), 'manual': (0x6BB, 2, 1), 'alt_gear': (0x48, 3, 3),
                            'alt_gas': (0x40, 4, 1), 'engine': (0x174, 2, 0)}[fault]
    changed = bytearray(last[target])
    changed[1] = (changed[1] + 1) & 15
    changed[index] = value
    changed[0] = checksum(target, changed)
    safety.safety_rx_hook(libsafety_py.make_CANPacket(target, 1, bytes(changed)))
  assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, bus, data))


class TestSubaruAvhDiscovery(unittest.TestCase):
  """The mandatory native coverage runner uses unittest discovery."""

  def test_pair_and_release_guard(self):
    for param in (1169, 1425):
      for ae in (0, 32):
        with self.subTest(param=param, ae=ae):
          test_exact_pair_debug_only_and_status_never_tx(param, ae)

  def test_conflicting_counters(self):
    for target in (0x6BB, 0x32B, 0x40, 0x48, 0x174):
      with self.subTest(target=target):
        test_conflicting_same_counter_withdraws(target)

  def test_receiver_refusals(self):
    for fault in ('main_gas', 'eps', 'control', 'wrong_bus', 'opaque', 'late_second', 'ack', 'manual',
                  'alt_gear', 'alt_gas', 'engine', 'main_gear'):
      with self.subTest(fault=fault):
        test_native_refusals(fault)


def test_explicit_release_blocks_avh_and_preserves_stock_aol():
  from opendbc.safety.tests.test_subaru_stock_aol import TestSubaruStockAol

  original = libsafety_py.libpath
  try:
    libsafety_py.load(libsafety_py._build_libsafety(release=True))
    assert libsafety_py.libsafety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) != 0
    for param in (1169, 1425):
      for ae in (0, 32):
        test_exact_pair_debug_only_and_status_never_tx(param, ae)
    # RELEASE must retain normal engagement and lateral-only permission after
    # rejecting the optional DEBUG AVH words, including both Legacy bases.
    case = TestSubaruStockAol()
    for param in (145, 401):
      case.configure(param)
      case.arm()
      case.safety.aol_set_host_request(3)
      assert case.safety.aol_get_permission_mask() == 1
      assert case.feed(main=False) == 0
  finally:
    libsafety_py.load(original)
