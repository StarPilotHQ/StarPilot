"""Exact manual Matrix stock PCM native observer and authority regressions."""
import pytest

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.car.toyota.tests import test_rav4h_caller_native as helpers

# Read actual native required-RX timestamps; never modify them.
libsafety_py.ffi.cdef('extern safety_config current_safety_config;', override=True)

source_matched_native = helpers.source_matched_native


class Physical:
  def __init__(self, *, word=856, experience=32):
    self.safety = libsafety_py.libsafety
    self.now = 1_000_000
    self.packer = CANPacker('toyota_new_mc_pt_generated')
    self.safety.set_alternative_experience(experience)
    assert self.safety.set_safety_hooks(structs.CarParams.SafetyModel.toyota, word) == 0
    self.safety.init_tests()
    self.safety.set_alternative_experience(experience)
    self.safety.set_aol_test_heartbeat(True)

  def tick(self, *, fault=None, omit=None, **fields):
    self.now += 10_000
    self.safety.set_timer(self.now)
    frames = helpers.physical(self.packer, cruise=fields.pop('cruise', False), sdsu=None, **fields)
    for address, data, bus in frames:
      if address == omit:
        continue
      required = address in (0xAA, 0x260, 0x1D2, 0x224, 0x262, 0x1D3)
      corrupted = fault == ('crc', address)
      if corrupted:
        data = data[:-1] + bytes([data[-1] ^ 1])
      if fault == ('length', address):
        data = data + bytes(3) if address == 0x262 else data[:-1]
      if fault == ('bus', address):
        bus = 2
      accepted = bool(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data)))
      if required and corrupted:
        assert not accepted, (hex(address), 'corrupt required source was accepted')
      elif required and fault != ('length', address):
        assert accepted, (hex(address), data.hex())
    self.safety.safety_tick()
    self.safety.aol_set_host_request(3)

  def warm(self):
    for _ in range(25):
      self.tick(cruise=False)
    for _ in range(25):
      self.tick(cruise=True)

  def tx(self, name, values):
    address, data, bus = self.packer.make_can_msg(name, 0, values)
    return bool(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)))

  def close(self):
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)


@pytest.fixture
def matrix():
  case = Physical()
  try:
    yield case
  finally:
    case.close()


def test_actual_dlc5_stock_lateral_only(matrix):
  matrix.warm()
  assert matrix.safety.safety_config_valid()
  assert matrix.safety.aol_get_permission_mask() == 1
  assert matrix.tx('STEERING_LKA', {'STEER_TORQUE_CMD': 1, 'STEER_REQUEST': 1})
  assert not matrix.tx('ACC_CONTROL', {'ACCEL_CMD': .1})
  assert not matrix.tx('ACC_CONTROL', {'ACCEL_CMD': -.1})
  assert matrix.tx('ACC_CONTROL', {'ACCEL_CMD': 0, 'CANCEL_REQ': 1})


@pytest.mark.parametrize('fields', ({'main': False}, {'gear': 8}, {'gear': 16},
                                   {'eps': 0}, {'eps': 3}, {'eps': 9}, {'eps': 17}))
def test_physical_refusals_and_recovery(matrix, fields):
  matrix.warm()
  assert matrix.safety.aol_get_permission_mask() == 1
  matrix.tick(**fields)
  assert matrix.safety.aol_get_permission_mask() == 0
  assert not matrix.tx('STEERING_LKA', {'STEER_TORQUE_CMD': 1, 'STEER_REQUEST': 1})
  matrix.tick()
  assert matrix.safety.aol_get_permission_mask() == 1


@pytest.mark.parametrize('address', (0x262, 0x1D3, 0x260))
@pytest.mark.parametrize('kind', ('crc', 'length'))
def test_actual_required_rx_refusal(matrix, address, kind):
  matrix.warm()
  assert matrix.safety.aol_get_permission_mask() == 1
  config = matrix.safety.current_safety_config
  matches = [i for i in range(config.rx_checks_len) if config.rx_checks[i].msg[0].addr == address]
  assert len(matches) == 1, (hex(address), matches)
  index = matches[0]
  check = config.rx_checks[index]
  original_timestamp = matrix.safety.rx_status[index].last_timestamp
  matrix.tick(fault=(kind, address))
  if address == 0x260 and kind == 'length':
    # Generic required RX matches address, bus AND DLC. A wrong-DLC frame must
    # never renew the last good source; the unchanged native tick expires it.
    assert check.msg[0].frequency == 50
    assert matrix.safety.rx_status[index].last_timestamp == original_timestamp
    for _ in range(19):
      matrix.tick(fault=(kind, address))
      assert matrix.safety.rx_status[index].last_timestamp == original_timestamp
    # AOL's unchanged ten-period freshness bound is tighter than generic lag.
    assert matrix.now - original_timestamp == 200_000
    assert not matrix.safety.rx_status[index].lagging and matrix.safety.aol_get_permission_mask() == 1
    matrix.tick(fault=(kind, address))
    assert matrix.now - original_timestamp == 210_000
    assert matrix.safety.rx_status[index].last_timestamp == original_timestamp and not matrix.safety.rx_status[index].lagging
    assert matrix.safety.aol_get_permission_mask() == 0
    assert not matrix.tx('STEERING_LKA', {'STEER_TORQUE_CMD': 1, 'STEER_REQUEST': 1})
    for _ in range(79):
      matrix.tick(fault=(kind, address))
      assert matrix.safety.rx_status[index].last_timestamp == original_timestamp
    assert matrix.now - original_timestamp == 1_000_000
    assert not matrix.safety.rx_status[index].lagging and matrix.safety.aol_get_permission_mask() == 0
    matrix.tick(fault=(kind, address))
    assert matrix.safety.rx_status[index].last_timestamp == original_timestamp and matrix.safety.rx_status[index].lagging
    assert matrix.now - original_timestamp == 1_010_000
  # EPS/MAIN observer DLC and required-source checksum refusals are immediate.
  assert matrix.safety.aol_get_permission_mask() == 0
  assert not matrix.tx('STEERING_LKA', {'STEER_TORQUE_CMD': 1, 'STEER_REQUEST': 1})


@pytest.mark.parametrize('address', (0x262, 0x1D3, 0x620, 0x3BC))
def test_wrong_bus_cannot_establish_source(matrix, address):
  for _ in range(50):
    matrix.tick(fault=('bus', address))
  assert matrix.safety.aol_get_permission_mask() == 0


@pytest.mark.parametrize('address,elapsed', ((0x262, 400001), (0x1D3, 303032)))
def test_original_source_expiry(matrix, address, elapsed):
  matrix.warm()
  assert matrix.safety.aol_get_permission_mask() == 1
  matrix.now += elapsed
  matrix.tick(omit=address)
  assert matrix.safety.aol_get_permission_mask() == 0


def test_original_host_request_expiry(matrix):
  matrix.warm()
  assert matrix.safety.aol_get_permission_mask() == 1
  matrix.safety.set_timer(matrix.now + 300001)
  assert matrix.safety.aol_get_permission_mask() == 0


def test_injected_relay_fault_refusal(matrix):
  matrix.warm()
  matrix.safety.set_relay_malfunction(True)
  assert matrix.safety.aol_get_permission_mask() == 0
  assert not matrix.tx('STEERING_LKA', {'STEER_TORQUE_CMD': 1, 'STEER_REQUEST': 1})


@pytest.mark.parametrize('experience', (0, 1, 33, 128, 96, 256, 544))
def test_other_alternative_experiences_remain_unadmitted(experience):
  case = Physical(experience=experience)
  try:
    case.warm()
    assert case.safety.aol_get_permission_mask() == 0
  finally:
    case.close()


def test_brake_and_unbelted_preserve_only_lateral(matrix):
  matrix.warm()
  matrix.tick(brake=True, cruise=False)
  assert matrix.safety.aol_get_permission_mask() == 1
  address, data, bus = matrix.packer.make_can_msg('BODY_CONTROL_STATE', 0, {'SEATBELT_DRIVER_UNLATCHED': 1})
  assert matrix.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
  assert matrix.safety.aol_get_permission_mask() == 1


def test_heartbeat_loss(matrix):
  matrix.warm()
  matrix.safety.set_aol_test_heartbeat(False)
  assert matrix.safety.aol_get_permission_mask() == 0
  assert not matrix.tx('STEERING_LKA', {'STEER_TORQUE_CMD': 1, 'STEER_REQUEST': 1})


def test_open_door_denies_lateral(matrix):
  matrix.warm()
  address, data, bus = matrix.packer.make_can_msg('BODY_CONTROL_STATE', 0, {'DOOR_OPEN_FL': 1})
  assert matrix.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
  assert matrix.safety.aol_get_permission_mask() == 0


def test_real_stock_steering_rx_triggers_relay_refusal(matrix):
  matrix.warm()
  assert not matrix.safety.get_relay_malfunction()
  address, data, bus = matrix.packer.make_can_msg('STEERING_LKA', 0, {})
  matrix.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
  assert matrix.safety.get_relay_malfunction()
  assert matrix.safety.aol_get_permission_mask() == 0
  assert not matrix.tx('STEERING_LKA', {'STEER_TORQUE_CMD': 1, 'STEER_REQUEST': 1})


from opendbc.car import Bus
from opendbc.car.toyota.stock_aol import PROFILES as FACTORY_PROFILES, EPS5_WORDS, ALT_MAIN_WORDS
from opendbc.car.toyota.values import DBC

FACTORY_CASES = {word: car for car, word in FACTORY_PROFILES.items()}


class FactoryPhysical(Physical):
  def __init__(self, word):
    super().__init__(word=word)
    self.word = word
    self.packer = CANPacker(DBC[FACTORY_CASES[word]][Bus.pt])

  def tick(self, *, main=True, **fields):
    self.now += 10_000
    self.safety.set_timer(self.now)
    frames = helpers.physical(self.packer, main=main, sdsu=None, **fields)
    if self.word in ALT_MAIN_WORDS:
      frames.append(self.packer.make_can_msg('DSU_CRUISE', 0, {'MAIN_ON': main, 'SET_SPEED': 90}))
    for address, data, bus in frames:
      self.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
    self.safety.safety_tick()
    self.safety.aol_set_host_request(3)


@pytest.mark.parametrize('word', sorted(FACTORY_CASES))
def test_every_factory_profile_keeps_stock_acc_and_physical_steering(word):
  case = FactoryPhysical(word)
  try:
    assert case.safety.aol_get_permission_mask() == 0
    case.warm()
    assert case.safety.safety_config_valid()
    assert case.safety.aol_get_permission_mask() == 1
    assert case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 1})
    assert not case.tx('ACC_CONTROL', {'ACCEL_CMD': .1})
    assert case.safety.safety_fwd_hook(2, 0x343) == 0
    cfg = case.safety.current_safety_config
    sources = [(cfg.rx_checks[i].msg[0].addr, cfg.rx_checks[i].msg[0].len) for i in range(cfg.rx_checks_len)]
    assert (0x262, 5 if word in EPS5_WORDS else 8) in sources
    assert (0x365, 7) in sources if word in ALT_MAIN_WORDS else (0x1D3, 8) in sources
    case.tick(main=False, cruise=True)
    assert case.safety.aol_get_permission_mask() == 0
    assert not case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 1})
  finally:
    case.close()
