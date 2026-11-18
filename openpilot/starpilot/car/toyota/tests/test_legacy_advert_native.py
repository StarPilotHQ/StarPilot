"""Physical CAN arms native controls; no permission or controls-allowed injection."""
import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.toyota import legacy_advert_filter
from opendbc.car.toyota.values import CAR, DBC
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.car.toyota.tests import test_rav4h_caller_native as helpers

source_matched_native = helpers.source_matched_native
CARS = (CAR.TOYOTA_RAV4H, CAR.TOYOTA_COROLLA, CAR.TOYOTA_RAV4, CAR.LEXUS_CTH, CAR.LEXUS_GS_F, CAR.LEXUS_IS)


@pytest.mark.parametrize('candidate', CARS)
@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('experience', (0, 32))
def test_native_sources_limits_and_angle_denial(candidate, stock, experience):
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(experience)
  assert safety.set_safety_hooks(structs.CarParams.SafetyModel.toyota, legacy_advert_filter.PROFILES[candidate][stock]) == 0
  safety.set_aol_test_heartbeat(True)
  packer = CANPacker(DBC[candidate][Bus.pt])
  lta_packer = CANPacker('toyota_nodsu_pt_generated')
  now = 1_000_000
  alternate = candidate in legacy_advert_filter.ALT_MAIN_CARS

  def tick(cruise=True, missing=None):
    nonlocal now
    now += 10_000
    safety.set_timer(now)
    frames = [f for f in helpers.physical(packer, cruise=cruise) if f[0] != 0x2FF]
    if alternate:
      frames.append(packer.make_can_msg('DSU_CRUISE', 0, {'MAIN_ON': True, 'SET_SPEED': 90}))
    for address, data, bus in frames:
      if address != missing:
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
    safety.safety_tick()
    safety.aol_set_host_request(3)

  def tx(name, values, other_packer=None):
    address, data, bus = (other_packer or packer).make_can_msg(name, 0, values)
    return bool(safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)))

  for _ in range(25):
    tick(False)
  for _ in range(25):
    tick()
  assert safety.get_controls_allowed() and safety.safety_config_valid()
  assert safety.aol_get_permission_mask() == ((1 if stock else 3) if experience else 0)
  assert tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
  assert tx('ACC_CONTROL', {'ACCEL_CMD': .1}) == (not stock)
  assert tx('ACC_CONTROL', {'ACCEL_CMD': 0, 'CANCEL_REQ': 1})
  for accel in (-3.5, 2.001, 3.5):
    assert tx('ACC_CONTROL', {'ACCEL_CMD': accel}) == (not stock)
  for accel in (-3.501, 3.501):
    assert not tx('ACC_CONTROL', {'ACCEL_CMD': accel})
  for values in ({'STEER_REQUEST': 1}, {'STEER_REQUEST_2': 1}, {'STEER_ANGLE_CMD': .1}, {'STEER_ANGLE_CMD': -.1}, {'TORQUE_WIND_DOWN': 100}):
    assert not tx('STEERING_LTA', values, lta_packer), values
  for payload in ('0f023e0000000000', '0f02280100000000'):
    assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(0x750, 0, bytes.fromhex(payload)))
  if alternate:
    assert not tx('DSU_CRUISE', {'MAIN_ON': True})
  assert safety.safety_fwd_hook(2, 0x343) == 0
  # No eight-byte status was supplied. Real MAIN and EPS remain mandatory.
  for source in (0x365 if alternate else 0x1D3, 0x262):
    for _ in range(45):
      tick(missing=source)
    assert not tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
    assert not tx('ACC_CONTROL', {'ACCEL_CMD': .1})
    assert not tx('ACC_CONTROL', {'ACCEL_CMD': 0, 'RELEASE_STANDSTILL': 1})
    assert tx('ACC_CONTROL', {'ACCEL_CMD': 0, 'CANCEL_REQ': 1})
    assert tx('STEERING_LKA', {'STEER_REQUEST': 0, 'STEER_TORQUE_CMD': 0})
    if experience:
      assert safety.aol_get_permission_mask() == 0
    for _ in range(25):
      tick(False)
    for _ in range(25):
      tick()
    assert tx('ACC_CONTROL', {'ACCEL_CMD': .1}) == (not stock)
  for source in (0x365 if alternate else 0x1D3, 0x262):
    message = 'EPS_STATUS' if source == 0x262 else 'DSU_CRUISE' if alternate else 'PCM_CRUISE_2'
    values = {'LKA_STATE': 1.0} if source == 0x262 else {'MAIN_ON': 1.0}
    frame = packer.make_can_msg(message, 0, values)
    malformed = [frame[1][:-1]]
    if source != 0x365:
      malformed.append(frame[1][:-1] + bytes([frame[1][-1] ^ 1]))
    for data in malformed:
      safety.safety_rx_hook(libsafety_py.make_CANPacket(source, 0, data))
      assert not tx('STEERING_LKA', {'STEER_REQUEST': 1, 'STEER_TORQUE_CMD': 3})
      assert not tx('ACC_CONTROL', {'ACCEL_CMD': .1})
      assert tx('ACC_CONTROL', {'ACCEL_CMD': 0, 'CANCEL_REQ': 1})
      for _ in range(25):
        tick(False)
      for _ in range(25):
        tick()
      assert tx('ACC_CONTROL', {'ACCEL_CMD': .1}) == (not stock)
  measured = packer.make_can_msg('STEER_TORQUE_SENSOR', 0, {'STEER_TORQUE_EPS': 100})
  for _ in range(8):
    assert safety.safety_rx_hook(libsafety_py.make_CANPacket(measured[0], measured[2], measured[1]))
  factor = legacy_advert_filter.PROFILES[candidate][stock] & 0xFF
  assert safety.get_torque_meas_min() == factor - 1
  assert safety.get_torque_meas_max() == factor + 1
  safety.set_alternative_experience(0)
  safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
