from types import SimpleNamespace
from unittest.mock import patch

import pytest

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.hyundai.angle_tx_recovery import AngleTxRecovery
from opendbc.car.hyundai.carcontroller import angle_torque_reduction_gain
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.tests.test_ev9_long_controller import packets
from opendbc.car.hyundai.tests.test_stock_angle_aol import source_profile
from opendbc.car.hyundai.values import CAR


@pytest.mark.parametrize('gain', (0.18, 0.532, 0.692, 0.848))
def test_inactive_steering_does_not_retain_pre_fault_gain(gain):
  released = angle_torque_reduction_gain(0, 5., False, gain)
  assert released == 0., 'INACTIVE_STEERING_RETAINED_GAIN'
  assert angle_torque_reduction_gain(0, 5., True, released) == 0.004


def profile(identity, long, aol):
  cp = source_profile(identity)
  if long:
    if identity == CAR.HYUNDAI_IONIQ_5_PE:
      from opendbc.car.hyundai.ioniq5pe_long_profile import candidate
    else:
      from opendbc.car.hyundai.ev9_longitudinal import candidate
    cp = candidate(cp, enabled=True, is_release=False)
    cp.alternativeExperience = 32 if aol else 0
  elif aol:
    cp.safetyConfigs[0].safetyParam |= 0x800 if not cp.safetyConfigs[0].safetyParam & 0x4000 else 0
    cp.alternativeExperience = 32
  return cp


def controller(identity, long, aol):
  cp = profile(identity, long, aol)
  ci = CarInterface(cp)
  # Isolate command recovery after the separately tested owner handoff.
  ci.CC.pe_startup = SimpleNamespace(handed_off=True, can_emit=lambda controller: True)
  packer = CANPacker(ci.can_parsers[next(iter(ci.can_parsers))].dbc.name)
  for tick in range(1, 14):
    frames = packets(packer, tick, advance_counter=True)
    if not long and tick % 2 == 0:
      frames.append(packer.make_can_msg('SCC_CONTROL', 1, {'COUNTER': tick // 2, 'ACCMode': 1, 'MainMode_ACC': 1}))
    ci.update([(tick * 10_000_000, frames)])
  assert ci.CS.out.canValid
  return ci, packer


@pytest.mark.parametrize('identity', (CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9))
@pytest.mark.parametrize('long', (False, True))
@pytest.mark.parametrize('aol', (False, True))
def test_actual_controller_fault_and_full_lateral_withdrawal_restart_gain_from_zero(identity, long, aol):
  ci, _ = controller(identity, long, aol)
  cs = ci.CS
  cs.out.vEgoRaw = cs.out.vEgo = 5.
  cs.out.steeringAngleDeg = cs.angle_steering_angle = 20.
  command = structs.CarControl(enabled=True, latActive=True)
  command.actuators.steeringAngleDeg = 50.
  ci.CC.apply_angle_last = 20.
  ci.CC.angle_gain_last = .692
  cs.out.steerFaultTemporary = cs.angle_steering_fault = True
  ci.CC.frame = 1
  _, inactive = ci.apply(command.as_reader(), 200_000_000)
  assert ci.CC.angle_gain_last == 0., 'FAULT_RETAINED_PREVIOUS_GAIN'
  cs.out.steerFaultTemporary = cs.angle_steering_fault = False
  ci.CC.frame = 3
  _, output = ci.apply(command.as_reader(), 210_000_000)
  assert ci.CC.angle_gain_last == .004
  address = 0xCB if long else 0x110
  payload = next(data for addr, data, _ in output if addr == address)
  assert payload[6 if long else 12] == 1
  command.latActive = command.enabled = False
  ci.CC.frame = 7
  ci.apply(command.as_reader(), 220_000_000)
  assert ci.CC.angle_gain_last == 0.


@pytest.mark.parametrize('case', ('current','accepted','wrong_bus','wrong_address','wrong_payload','short','stale','duplicate','inactive'))
def test_long_echo_exact_source_freshness_and_one_use(case):
  now = [1_000_000_000]
  recovery = AngleTxRecovery(0xCB, 1, clock=lambda: now[0])
  raw = bytearray(24)
  raw[3] = 0x10 if case == 'inactive' else 0x20
  recovery.record([(0xCB, bytes(raw), 1)], 10)
  now[0] += 31_000_000 if case == 'stale' else 10_000_000
  if case == 'wrong_payload':
    raw[6] ^= 1
  data = bytes(raw[:23] if case == 'short' else raw)
  bus = 129 if case == 'accepted' else 192 if case == 'wrong_bus' else 193
  address = 0x110 if case == 'wrong_address' else 0xCB
  recovery.observe([(now[0], [(address, data, bus)])], 11)
  assert recovery.take_rejection(11) == (case in ('current','duplicate'))
  assert not recovery.take_rejection(11)
  recovery.observe([(now[0]+1, [(address, data, bus)])], 12)
  assert not recovery.take_rejection(12)


@pytest.mark.parametrize('identity', (CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9))
def test_long_controller_consumes_current_rejection_without_retaining_filtered_history(identity):
  now = [1_000_000_000]
  with patch('time.monotonic_ns', side_effect=lambda: now[0]):
    ci, packer = controller(identity, True, True)
    assert ci.CC.angle_tx_recovery is not None, 'LONG_REJECTION_RECOVERY_MISSING'
    cs = ci.CS
    cs.out.vEgo = cs.out.vEgoRaw = 5.
    cs.out.steeringAngleDeg = cs.angle_steering_angle = -20.
    command = structs.CarControl(enabled=True, latActive=True)
    command.actuators.steeringAngleDeg = -100.
    ci.CC.apply_angle_last = -70.
    ci.CC.ev9_angle_filter.x = -70.
    ci.CC.angle_gain_last = .692
    ci.CC.frame = 21
    _, frames = ci.apply(command.as_reader(), now[0])
    emitted = next(frame for frame in frames if frame[0] == 0xCB)
    now[0] += 10_000_000
    ci.update([(now[0], [(emitted[0], emitted[1], 193)])])
    # Current physical angle is the new native limiter baseline after rejection.
    cs.out.canValid, cs.out.canTimeout = True, False
    cs.out.steeringAngleDeg = cs.angle_steering_angle = -20.
    _, frames = ci.apply(command.as_reader(), now[0])
    assert abs(ci.CC.apply_angle_last + 20.) <= 5.
    assert ci.CC.angle_gain_last == .004
