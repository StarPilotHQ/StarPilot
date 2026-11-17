"""Original EPS66 source leases and old-profile neighbors use actual native CAN."""
import pytest
from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.car.toyota.tests import test_rav4h_caller_native as helpers
from openpilot.starpilot.car.toyota.tests.test_prius_sdsu_filter import factory
from opendbc.car.toyota import prius_sdsu, prius_longitudinal

source_matched_native = helpers.source_matched_native
libsafety_py.ffi.cdef('extern safety_config current_safety_config;')


class Physical:
  def __init__(self, word, experience):
    self.native = libsafety_py.libsafety
    self.native.init_tests()
    self.native.set_alternative_experience(experience)
    assert self.native.set_safety_hooks(structs.CarParams.SafetyModel.toyota, word) == 0
    self.native.set_aol_test_heartbeat(True)
    self.word, self.experience, self.now = word, experience, 1_000_000
    self.packer = CANPacker('toyota_nodsu_pt_generated')

  def tick(self, cruise=True, *, status=0, malformed=False, mirror=False):
    self.now += 10_000
    self.native.set_timer(self.now)
    frames = [f for f in helpers.physical(self.packer, cruise=cruise) if f[0] != 0x2FF]
    if status is not None:
      frame = self.packer.make_can_msg('SDSU', 0, {'STATE': status})
      frames.append((frame[0], frame[1][:7] if malformed else frame[1], 2 if mirror else 0))
    for address, data, bus in frames:
      assert self.native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
    self.native.safety_tick()
    self.native.aol_set_host_request(3)

  def earn(self):
    for cruise in (False, True):
      for _ in range(25):
        self.tick(cruise)
    assert self.native.get_controls_allowed() and self.native.safety_config_valid()

  def tx(self, name, values):
    address, data, bus = self.packer.make_can_msg(name, 0, values)
    return bool(self.native.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)))

  def close(self):
    self.native.set_alternative_experience(0)
    self.native.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('experience', (0, 32))
def test_original_prius_required_status_lease_aeb_and_malformed_source(stock, experience):
  cp = factory()
  if stock:
    assert prius_longitudinal.prepare_stock(cp)
  case = Physical(cp.safetyConfigs[0].safetyParam, experience)
  try:
    cfg = libsafety_py.libsafety.current_safety_config
    assert cfg.rx_checks_len == 7
    checks = {cfg.rx_checks[i].msg[0].addr: cfg.rx_checks[i].msg[0] for i in range(7)}
    assert set(checks) == {0xAA, 0x260, 0x1D2, 0x226, 0x2FF, 0x262, 0x1D3}
    assert all(checks[address].len == 8 and not checks[address].ignore_checksum for address in (0x262, 0x1D3))
    assert not case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    case.earn()
    expected = 1 if stock else 3
    assert case.native.aol_get_permission_mask() == (expected if experience else 0)
    assert case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    assert case.tx('ACC_CONTROL', {'ACCEL_CMD': .1}) == (not stock)
    case.tick(status=7)
    assert case.native.safety_config_valid()
    assert case.native.aol_get_permission_mask() == (1 if experience else 0)
    assert not case.tx('ACC_CONTROL', {'ACCEL_CMD': .1})
    for failure in ('missing', 'short', 'mirror'):
      case.earn()
      prior = int(case.native.rx_status[4].last_timestamp)
      for _ in range(21):
        case.tick(status=None if failure == 'missing' else 0, malformed=failure == 'short', mirror=failure == 'mirror')
      assert int(case.native.rx_status[4].last_timestamp) == prior
      assert case.native.aol_get_permission_mask() == 0
      assert not case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
      assert not case.tx('ACC_CONTROL', {'ACCEL_CMD': .1})
      assert case.tx('ACC_CONTROL', {'ACCEL_CMD': 0, 'CANCEL_REQ': 1})
    case.earn()
    for value in (-3.501, 2.001):
      assert not case.tx('ACC_CONTROL', {'ACCEL_CMD': value})
    assert not case.tx('STEERING_LTA', {'STEER_REQUEST': 1})
    assert not case.native.safety_tx_hook(libsafety_py.make_CANPacket(0x750, 0, bytes.fromhex('0f02280100000000')))
  finally:
    case.close()


@pytest.mark.parametrize('word', (4169, 4681, 0xC049, 0xC249))
@pytest.mark.parametrize('experience', (0, 32))
def test_warm_switch_preserves_retrofit_and_camera_stock_neighbors(word, experience):
  case = Physical(prius_sdsu.LONG_WORD, experience)
  try:
    case.earn()
    assert case.native.set_safety_hooks(structs.CarParams.SafetyModel.toyota, word) == 0
    case.word = word
    case.native.set_aol_test_heartbeat(True)
    assert case.native.current_safety_config.rx_checks_len == (6 if word in (0xC049, 0xC249) else 4)
    assert not case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    # C0 camera identity excludes a status receiver; retain every other physical source.
    if word in (0xC049, 0xC249):
      for cruise in (False, True):
        for _ in range(25):
          case.tick(cruise, status=None)
    else:
      case.earn()
    assert case.native.get_controls_allowed() and case.native.safety_config_valid()
    stock = word in (4681, 0xC249)
    assert case.native.aol_get_permission_mask() == ((1 if stock else 3) if experience else 0)
    assert case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    assert case.tx('ACC_CONTROL', {'ACCEL_CMD': .1}) == (not stock)
  finally:
    case.close()


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('experience', (0, 32))
@pytest.mark.parametrize('source', (0x1D3, 0x262))
@pytest.mark.parametrize('failure', ('checksum', 'length', 'missing', 'mirror'))
def test_original_prius_main_eps_independent_source_refusals(stock, experience, source, failure):
  case = Physical(prius_sdsu.STOCK_WORD if stock else prius_sdsu.LONG_WORD, experience)
  try:
    case.earn()
    index = 6 if source == 0x1D3 else 5
    prior = int(case.native.rx_status[index].last_timestamp)
    for _ in range(41):
      case.now += 10_000
      case.native.set_timer(case.now)
      for address, raw, bus in helpers.physical(case.packer, cruise=True):
        expected = True
        if address == source:
          if failure == 'missing':
            continue
          if failure == 'checksum':
            raw = raw[:-1] + bytes([raw[-1] ^ 1])
            expected = False
          elif failure == 'length':
            raw = raw[:7]
          else:
            bus = 2
        assert bool(case.native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, raw))) == expected
      case.native.safety_tick()
      case.native.aol_set_host_request(3)
    if failure == 'checksum':
      # Native records reception independently of checksum validity.
      assert int(case.native.rx_status[index].last_timestamp) == case.now
      assert not case.native.rx_status[index].valid_checksum
    else:
      assert int(case.native.rx_status[index].last_timestamp) == prior
    assert case.native.aol_get_permission_mask() == 0
    assert not case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    assert not case.tx('ACC_CONTROL', {'ACCEL_CMD': .1})
    assert case.tx('ACC_CONTROL', {'ACCEL_CMD': 0, 'CANCEL_REQ': 1})
    case.earn()
    assert case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    assert case.tx('ACC_CONTROL', {'ACCEL_CMD': .1}) == (not stock)
  finally:
    case.close()


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('experience', (0, 32))
@pytest.mark.parametrize('address,bus,length', ((0x201, 0, 6), (0x2AA, 0, 8), (0x2FF, 1, 8), (0x2FF, 0, 4), (0x2FF, 2, 4)))
def test_original_prius_contradictory_identity_requires_reconfiguration(stock, experience, address, bus, length):
  word = prius_sdsu.STOCK_WORD if stock else prius_sdsu.LONG_WORD
  case = Physical(word, experience)
  try:
    case.earn()
    assert case.native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, bytes(length)))
    for _ in range(30):
      case.tick()
    assert case.native.aol_get_permission_mask() == 0
    assert not case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    assert not case.tx('ACC_CONTROL', {'ACCEL_CMD': .1})
    assert case.tx('ACC_CONTROL', {'ACCEL_CMD': 0, 'CANCEL_REQ': 1})
    assert case.native.set_safety_hooks(structs.CarParams.SafetyModel.toyota, word) == 0
    case.native.set_aol_test_heartbeat(True)
    assert case.native.current_safety_config.rx_checks_len == 7
    assert all(case.native.rx_status[i].last_timestamp == 0 for i in range(7))
    assert not case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    case.earn()
    assert case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    assert case.tx('ACC_CONTROL', {'ACCEL_CMD': .1}) == (not stock)
  finally:
    case.close()


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('experience', (0, 32))
def test_original_prius_seventh_rx_slot_cannot_retain_warm_health(stock, experience):
  word = prius_sdsu.STOCK_WORD if stock else prius_sdsu.LONG_WORD
  case = Physical(word, experience)
  try:
    case.earn()
    assert case.native.set_safety_hooks(structs.CarParams.SafetyModel.toyota, 4169) == 0
    case.native.set_aol_test_heartbeat(True)
    case.earn()
    assert case.native.current_safety_config.rx_checks_len == 4
    assert case.native.set_safety_hooks(structs.CarParams.SafetyModel.toyota, word) == 0
    case.native.set_aol_test_heartbeat(True)
    cfg = case.native.current_safety_config
    assert cfg.rx_checks_len == 7
    assert all(case.native.rx_status[i].last_timestamp == 0 for i in range(7))
    assert not case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    case.earn()
    assert case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
  finally:
    case.close()
