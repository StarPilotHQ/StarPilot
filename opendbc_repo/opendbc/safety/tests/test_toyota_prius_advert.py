"""Original Prius four-byte identity never provides a status or source lease."""

import pytest
from opendbc.car import structs
from opendbc.car.toyota import prius_advert_filter as owner, prius_sdsu
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.test_toyota_prius_sdsu import Physical
from openpilot.starpilot.car.toyota.tests import test_rav4h_caller_native as helpers

source_matched_native = helpers.source_matched_native


class AdvertPhysical(Physical):
  def tick(self, cruise=True, *, status=0, malformed=False, mirror=False, source=None, failure=None, conflict=None, ad=True):
    assert status == 0 and not malformed and not mirror, 'Advertisement profiles have no SDSU status input'
    self.now += 10_000
    self.native.set_timer(self.now)
    frames = [frame for frame in helpers.physical(self.packer, cruise=cruise) if frame[0] != 0x2FF]
    if ad:
      # Nibble7 is not a status or AEB source; a mirror cannot replace MAIN/EPS.
      frames.extend(((0x2FF, bytes.fromhex('00007409'), 0), (0x2FF, bytes.fromhex('80007409'), 2)))
    if conflict is not None:
      frames.append(conflict)
    for address, raw, bus in frames:
      expected = True
      if address == source and bus == 0:
        if failure == 'missing':
          continue
        if failure == 'checksum':
          raw = raw[:-1] + bytes([raw[-1] ^ 1])
          expected = False
        elif failure == 'length':
          raw = raw[:-1]
        elif failure == 'mirror':
          bus = 2
      assert bool(self.native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, raw))) == expected
    self.native.safety_tick()
    self.native.aol_set_host_request(3)


def active(case, stock, experience):
  assert case.native.safety_config_valid() and case.native.get_controls_allowed()
  assert case.native.aol_get_permission_mask() == ((1 if stock else 3) if experience else 0)
  assert case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
  assert case.tx('ACC_CONTROL', {'ACCEL_CMD': 0.1}) == (not stock)


def refused(case):
  assert case.native.aol_get_permission_mask() == 0
  assert not case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
  assert not case.tx('ACC_CONTROL', {'ACCEL_CMD': 0.1})
  assert case.tx('ACC_CONTROL', {'ACCEL_CMD': 0, 'CANCEL_REQ': 1})


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('experience', (0, 32))
def test_original_prius_advert_six_independent_rx_and_limits(stock, experience):
  case = AdvertPhysical(owner.STOCK_WORD if stock else owner.LONG_WORD, experience)
  try:
    cfg = case.native.current_safety_config
    assert cfg.rx_checks_len == 6
    checks = {cfg.rx_checks[i].msg[0].addr: cfg.rx_checks[i].msg[0] for i in range(6)}
    assert set(checks) == {0xAA, 0x260, 0x1D2, 0x226, 0x262, 0x1D3}
    assert all(checks[a].len == 8 and not checks[a].ignore_checksum for a in (0x262, 0x1D3))
    assert not case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    case.earn()
    active(case, stock, experience)
    for _ in range(50):
      case.tick(ad=False)
    active(case, stock, experience)
    for accel in (-3.501, 2.001):
      assert not case.tx('ACC_CONTROL', {'ACCEL_CMD': accel})
    assert not case.tx('STEERING_LTA', {'STEER_REQUEST': 1})
    assert not case.native.safety_tx_hook(libsafety_py.make_CANPacket(0x750, 0, bytes.fromhex('0f02280100000000')))
    assert case.native.safety_fwd_hook(2, 0x343) == 0
  finally:
    case.close()


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('experience', (0, 32))
@pytest.mark.parametrize('source', (0x1D3, 0x262))
@pytest.mark.parametrize('failure', ('checksum', 'length', 'missing', 'mirror'))
def test_original_prius_advert_main_eps_refusal_and_recovery(stock, experience, source, failure):
  case = AdvertPhysical(owner.STOCK_WORD if stock else owner.LONG_WORD, experience)
  try:
    case.earn()
    index = 5 if source == 0x1D3 else 4
    prior = int(case.native.rx_status[index].last_timestamp)
    for _ in range(41):
      case.tick(source=source, failure=failure)
    if failure == 'checksum':
      assert int(case.native.rx_status[index].last_timestamp) == case.now
      assert not case.native.rx_status[index].valid_checksum
    else:
      assert int(case.native.rx_status[index].last_timestamp) == prior
    refused(case)
    case.earn()
    active(case, stock, experience)
  finally:
    case.close()


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('experience', (0, 32))
@pytest.mark.parametrize('address,bus,length', ((0x201, 0, 6), (0x2AA, 0, 8), (0x2FF, 1, 4), (0x2FF, 0, 8), (0x2FF, 2, 8), (0x2FF, 0, 7)))
def test_original_prius_advert_sticky_conflict_until_reconfiguration(stock, experience, address, bus, length):
  word = owner.STOCK_WORD if stock else owner.LONG_WORD
  case = AdvertPhysical(word, experience)
  try:
    case.earn()
    case.tick(conflict=(address, bytes(length), bus))
    case.tick(source=0x262, failure='checksum')
    case.earn()
    assert case.native.safety_config_valid()
    refused(case)
    assert case.native.set_safety_hooks(structs.CarParams.SafetyModel.toyota, word) == 0
    case.native.set_aol_test_heartbeat(True)
    cfg = case.native.current_safety_config
    assert cfg.rx_checks_len == 6
    assert all(case.native.rx_status[i].last_timestamp == 0 for i in range(6))
    case.earn()
    active(case, stock, experience)
  finally:
    case.close()


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('experience', (0, 32))
def test_original_prius_advert_warm_switch_keeps_separate_status_owner(stock, experience):
  case = AdvertPhysical(owner.STOCK_WORD if stock else owner.LONG_WORD, experience)
  try:
    case.earn()
    assert case.native.set_safety_hooks(structs.CarParams.SafetyModel.toyota, prius_sdsu.STOCK_WORD if stock else prius_sdsu.LONG_WORD) == 0
    case.native.set_aol_test_heartbeat(True)
    assert case.native.current_safety_config.rx_checks_len == 7
    assert not case.tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    # The old owner earns status from genuine eight-byte SDSU traffic.
    for cruise in (False, True):
      for _ in range(25):
        Physical.tick(case, cruise)
    active(case, stock, experience)
    assert case.native.set_safety_hooks(structs.CarParams.SafetyModel.toyota, owner.STOCK_WORD if stock else owner.LONG_WORD) == 0
    case.native.set_aol_test_heartbeat(True)
    assert case.native.current_safety_config.rx_checks_len == 6
    case.earn()
    active(case, stock, experience)
  finally:
    case.close()
