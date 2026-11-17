"""Native controls/permissions are earned from actual decoded physical CAN."""

from typing import cast
import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus
from opendbc.car.toyota import camera_2aa_filter
from opendbc.car.toyota.values import CAR, DBC
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.car.toyota.tests import test_rav4h_caller_native as helpers
from openpilot.starpilot.car.toyota.tests.test_camera_2aa_filter import factory

source_matched_native = helpers.source_matched_native


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('experience', (0, 32))
def test_native_camera_sources_limits_and_identity(stock, experience):
  cp = factory(CAR.TOYOTA_COROLLA_TSS2)
  if stock:
    assert camera_2aa_filter.prepare_stock(cp)
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(experience)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  safety.set_aol_test_heartbeat(True)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  now = 1_000_000

  def tx(name, values):
    a, data, bus = packer.make_can_msg(name, 0, values)
    return bool(safety.safety_tx_hook(libsafety_py.make_CANPacket(a, bus, data)))

  def tick(cruise=True, missing=None):
    nonlocal now
    now += 10_000
    safety.set_timer(now)
    frames = [f for f in helpers.physical(packer, cruise=cruise) if f[0] != 0x2FF]
    frames.append((0x2AA, bytes(8), 0))
    for a, data, bus in frames:
      if a != missing:
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(a, bus, data))
    safety.safety_tick()
    safety.aol_set_host_request(3)

  assert not tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
  for _ in range(25):
    tick(False)
  for _ in range(25):
    tick()
  assert safety.get_controls_allowed() and safety.safety_config_valid()
  assert safety.aol_get_permission_mask() == ((1 if stock else 3) if experience else 0)
  assert tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
  assert tx('ACC_CONTROL', {'ACCEL_CMD': 0.1}) == (not stock)
  for accel in (-3.5, 2.001, 3.5):
    assert tx('ACC_CONTROL', {'ACCEL_CMD': accel}) == (not stock)
  for accel in (-3.501, 3.501):
    assert not tx('ACC_CONTROL', {'ACCEL_CMD': accel})
  for values in ({'STEER_REQUEST': 1}, {'STEER_REQUEST_2': 1}, {'STEER_ANGLE_CMD': 0.1}, {'TORQUE_WIND_DOWN': 100}):
    assert not tx('STEERING_LTA', values)
  for data in ('0f023e0000000000', '0f02280100000000'):
    assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(0x750, 0, bytes.fromhex(data)))
  for source in (0x1D3, 0x262):
    for _ in range(45):
      tick(missing=source)
    assert not tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    assert not tx('ACC_CONTROL', {'ACCEL_CMD': 0.1})
    assert not tx('ACC_CONTROL', {'ACCEL_CMD': 0, 'RELEASE_STANDSTILL': 1})
    assert tx('ACC_CONTROL', {'ACCEL_CMD': 0, 'CANCEL_REQ': 1})
    for _ in range(25):
      tick(False)
    for _ in range(25):
      tick()
    assert tx('ACC_CONTROL', {'ACCEL_CMD': 0.1}) == (not stock)
  # A status/pedal contradiction is refused even if every independent source stays fresh.
  assert safety.safety_rx_hook(libsafety_py.make_CANPacket(0x2FF, 0, bytes(8)))
  a, data, bus = packer.make_can_msg('EPS_STATUS', 0, {'LKA_STATE': 1})
  invalid = data[:-1] + bytes([data[-1] ^ 1])
  assert not safety.safety_rx_hook(libsafety_py.make_CANPacket(a, bus, invalid))
  for _ in range(25):
    tick(False)
  for _ in range(25):
    tick()
  assert safety.aol_get_permission_mask() == 0
  assert not tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3}) and not tx('ACC_CONTROL', {'ACCEL_CMD': 0.1})
  assert tx('ACC_CONTROL', {'ACCEL_CMD': 0, 'CANCEL_REQ': 1})
  safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam)
  assert not tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
  for _ in range(25):
    tick(False)
  for _ in range(25):
    tick()
  assert tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
  safety.set_alternative_experience(0)


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('experience', (0, 32))
@pytest.mark.parametrize('identity', ('pedal', 'bad-dlc', 'wrong-bus', 'advert'))
def test_native_identity_refusal_independent_of_aol(stock, experience, identity):
  cp = factory(CAR.TOYOTA_COROLLA_TSS2)
  if stock:
    assert camera_2aa_filter.prepare_stock(cp)
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(experience)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  now = 1_000_000

  def tx(name, values):
    a, data, bus = packer.make_can_msg(name, 0, values)
    return bool(safety.safety_tx_hook(libsafety_py.make_CANPacket(a, bus, data)))

  def tick(cruise):
    nonlocal now
    now += 10_000
    safety.set_timer(now)
    for a, data, bus in helpers.physical(packer, cruise=cruise):
      if a != 0x2FF:
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(a, bus, data))
    assert safety.safety_rx_hook(libsafety_py.make_CANPacket(0x2AA, 0, bytes(8)))
    safety.safety_tick()
    safety.aol_set_host_request(3)

  def configure_and_earn():
    assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
    safety.set_aol_test_heartbeat(True)
    assert not tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    for _ in range(25):
      tick(False)
    for _ in range(25):
      tick(True)
    assert safety.get_controls_allowed() and safety.safety_config_valid()
    assert safety.aol_get_permission_mask() == ((1 if stock else 3) if experience else 0)
    assert safety.safety_fwd_hook(2, 0x343) == 0
    assert tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    assert tx('ACC_CONTROL', {'ACCEL_CMD': 0.1}) == (not stock)

  configure_and_earn()
  address, bus, data = {'pedal': (0x201, 0, bytes(6)), 'bad-dlc': (0x2AA, 0, bytes(7)),
                        'wrong-bus': (0x2AA, 1, bytes(8)), 'advert': (0x2FF, 0, bytes(4))}[identity]
  assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
  for cruise in (False, True):
    for _ in range(25):
      tick(cruise)
  # Fresh valid required sources and a real cruise cycle cannot clear identity refusal.
  assert safety.safety_config_valid()
  assert safety.aol_get_permission_mask() == 0
  assert not tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
  assert not tx('ACC_CONTROL', {'ACCEL_CMD': 0.1})
  assert tx('ACC_CONTROL', {'ACCEL_CMD': 0, 'CANCEL_REQ': 1})
  configure_and_earn()
  safety.set_alternative_experience(0)


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('experience', (0, 32))
def test_native_optional_wrong_bus_source_does_not_replace_required_source(stock, experience):
  cp = factory(CAR.TOYOTA_COROLLA_TSS2)
  if stock:
    assert camera_2aa_filter.prepare_stock(cp)
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(experience)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  safety.set_aol_test_heartbeat(True)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  now = 1_000_000

  def tick(cruise):
    nonlocal now
    now += 10_000
    safety.set_timer(now)
    for address, data, bus in helpers.physical(packer, cruise=cruise):
      if address != 0x2FF:
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
    safety.safety_tick()
    safety.aol_set_host_request(3)

  def positive():
    address, data, bus = packer.make_can_msg('STEERING_LKA', 0, {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    return bool(safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)))

  for _ in range(25):
    tick(False)
  for _ in range(25):
    tick(True)
  assert positive()
  for name, values in (('PCM_CRUISE_2', {'MAIN_ON': 1}), ('EPS_STATUS', {'LKA_STATE': 1})):
    address, data, bus = packer.make_can_msg(name, 0, cast(dict[str, float], values))
    assert address in (0x1D3, 0x262) and len(data) == 8 and bus == 0
    invalid = data[:-1] + bytes([data[-1] ^ 1])
    # Optional bus2 packets have no source authority; bad CRC cannot replace bus0.
    assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, 2, invalid))
    assert safety.safety_config_valid() and safety.get_controls_allowed() and positive()
    assert safety.aol_get_permission_mask() == ((1 if stock else 3) if experience else 0)
    # The same corrupt packet on required bus0 is rejected and denies output.
    assert not safety.safety_rx_hook(libsafety_py.make_CANPacket(address, 0, invalid))
    safety.safety_tick()
    assert not safety.get_controls_allowed()
    assert not positive() and safety.aol_get_permission_mask() == 0
    for _ in range(25):
      tick(False)
    for _ in range(25):
      tick(True)
    assert positive()
  safety.set_alternative_experience(0)
