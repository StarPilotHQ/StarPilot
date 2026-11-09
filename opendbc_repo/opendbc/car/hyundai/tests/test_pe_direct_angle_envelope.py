"""Direct PE EPS output retains its LFA target envelope and native handoff checks."""
from types import SimpleNamespace
import unittest

import pytest

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.tests.test_angle_fault_recovery import controller, profile
from opendbc.car.hyundai.tests.test_ev9_long_controller import packets
from opendbc.car.hyundai.values import CAR


def direct(frames):
  _, data, bus = next(frame for frame in frames if frame[0] == 0xCB)
  raw = (int.from_bytes(data, 'little') >> 32) & 0x3FFF
  angle = (raw - 0x4000 if raw & 0x2000 else raw) * .1
  return angle, (data[3] >> 4) & 15, data[6] * .004, bus


@pytest.mark.parametrize('sign', (-1, 1))
@pytest.mark.parametrize('aol', (False, True))
def test_pe_direct_target_and_measured_history_stay_in_reference_envelope(sign, aol):
  ci, _ = controller(CAR.HYUNDAI_IONIQ_5_PE, True, aol)
  cs = ci.CS
  cs.out.vEgo = cs.out.vEgoRaw = 2.
  cs.out.steeringAngleDeg = cs.angle_steering_angle = 0.
  cs.out.steeringPressed = False
  cs.out.steeringTorque = 0.
  command = structs.CarControl(enabled=not aol, latActive=True)
  command.actuators.steeringAngleDeg = sign * 360.
  peak = 0.
  for tick in range(400):
    ci.CC.frame = tick + 21
    _, frames = ci.apply(command.as_reader(), (tick + 21) * 10_000_000)
    angle, active, gain, bus = direct(frames)
    assert bus == 1
    assert active == 2 and gain > 0
    assert abs(angle) <= 175.0001, 'PE_DIRECT_EPS_TARGET_EXCEEDED_REFERENCE_ENVELOPE'
    peak = max(peak, abs(angle))
  assert peak == pytest.approx(175., abs=.1)

  for measured in (sign * 200., sign * 300., sign * 170., sign * 170.):
    cs.out.steeringAngleDeg = cs.angle_steering_angle = measured
    previous = ci.CC.apply_angle_last
    ci.CC.frame += 1
    _, frames = ci.apply(command.as_reader(), (ci.CC.frame + 21) * 10_000_000)
    angle, active, gain, _ = direct(frames)
    if abs(measured) > 175 or abs(previous) > 175:
      assert active == 1 and gain == 0
      assert angle == pytest.approx(measured, abs=.1)
    else:
      assert active == 2 and gain == pytest.approx(.004)
      assert abs(angle) <= 175.
      assert abs(angle - measured) <= 5.


def test_ev9_and_stock_camera_envelopes_are_preserved():
  for identity, long, expected in ((CAR.KIA_EV9, True, 140.), (CAR.HYUNDAI_IONIQ_5_PE, False, 360.)):
    ci, _ = controller(identity, long, True)
    cs = ci.CS
    cs.out.vEgo = cs.out.vEgoRaw = 2.
    cs.out.steeringAngleDeg = cs.angle_steering_angle = 0.
    cs.out.cruiseState.enabled = True
    cs.out.steeringPressed = False
    cs.out.steeringTorque = 0.
    command = structs.CarControl(enabled=True, latActive=True)
    command.actuators.steeringAngleDeg = 360.
    for tick in range(400):
      ci.CC.frame = tick + 21
      ci.apply(command.as_reader(), (tick + 21) * 10_000_000)
    assert ci.CC.apply_angle_last == pytest.approx(expected, abs=.1)
    assert ci.CC.params.ANGLE_LIMITS.STEER_ANGLE_MAX == 360.


@pytest.mark.parametrize('sign', (-1, 1))
def test_actual_pe_direct_output_and_outside_envelope_reentry_are_native_admitted(sign):
  from opendbc.safety.tests.libsafety import libsafety_py
  from opendbc.safety.tests.test_ioniq5pe_paired_native import PEFixture

  class Native(PEFixture, unittest.TestCase):
    pass

  case = Native(methodName='runTest')
  case.setUp()
  ci = CarInterface(profile(CAR.HYUNDAI_IONIQ_5_PE, True, False))
  ci.CC.pe_startup = SimpleNamespace(handed_off=True, can_emit=lambda controller: True)
  packer = CANPacker('hyundai_canfd_generated')
  command = structs.CarControl(enabled=True, latActive=True, longActive=True)
  command.actuators.steeringAngleDeg = sign * 360.
  ci.update([])
  active_count = 0
  for tick in range(1, 801):
    case.now = tick * 10_000
    case.safety.set_timer(case.now)
    frames = packets(packer, tick, moving=False, advance_counter=True,
                     cruise_button=2 if tick == 12 else 0, adaptive_main=tick == 8)
    wheel = packer.make_can_msg('WHEEL_SPEEDS', 1, {})[0]
    mdps = packer.make_can_msg('MDPS', 1, {})[0]
    frames = [f for f in frames if f[0] not in (wheel, mdps)]
    frames.append(packer.make_can_msg('WHEEL_SPEEDS', 1, {'COUNTER': tick,
      **dict.fromkeys(('WHL_SpdFLVal', 'WHL_SpdFRVal', 'WHL_SpdRLVal', 'WHL_SpdRRVal'), 7.2)}))
    measured = sign * 300. if 550 <= tick < 570 else 0.
    frames.append(packer.make_can_msg('MDPS', 1, {'COUNTER': tick,
      'MDPS_EstStrAnglVal': measured, 'MDPS_StrTqSnsrVal': 0.}))
    for address, data, bus in frames:
      assert case.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data)), (tick, hex(address))
    ci.update([(tick * 10_000_000, frames)])
    command.enabled = command.latActive = tick >= 14
    _, sends = ci.apply(command.as_reader(), tick * 10_000_000)
    if tick < 14:
      continue
    angle, active, gain, _ = direct(sends)
    for address, data, bus in sends:
      assert case.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)), (tick, hex(address), bus)
    if active == 2:
      active_count += 1
      assert abs(angle) <= 175.0001, 'PE_DIRECT_EPS_TARGET_EXCEEDED_REFERENCE_ENVELOPE'
    if 550 <= tick <= 570:
      assert active == 1 and gain == 0
      assert angle == pytest.approx(measured, abs=.1)
    if tick == 800:
      assert active == 2 and abs(angle) == pytest.approx(175., abs=.1)
  assert active_count > 600
