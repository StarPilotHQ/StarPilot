from types import SimpleNamespace

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.hyundai.ev9_longitudinal import candidate as ev9_candidate
from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.ioniq5pe_long_profile import candidate as pe_candidate
from opendbc.car.hyundai.tests.test_ev9_long_controller import packets
from opendbc.car.hyundai.tests.test_ioniq5pe_stock import params
from opendbc.car.hyundai.values import CAR, DBC


def frame_inputs(packer, tick, speed, measured, *, fault=0):
  frames = packets(packer, tick, fault=fault, advance_counter=True)
  replacements = [
    packer.make_can_msg('WHEEL_SPEEDS', 1, {'COUNTER': tick % 256, **dict.fromkeys(
      ('WHL_SpdFLVal', 'WHL_SpdFRVal', 'WHL_SpdRLVal', 'WHL_SpdRRVal'), speed * 3.6)}),
    packer.make_can_msg('MDPS', 1, {'COUNTER': tick % 256, 'MDPS_EstStrAnglVal': measured,
                                 'MDPS_ADAS_AciFltSig_Lv2': fault}),
    packer.make_can_msg('STEERING_SENSORS', 1, {'COUNTER': tick % 256, 'STEERING_ANGLE': measured}),
  ]
  replaced = {message[0] for message in replacements}
  return [message for message in frames if message[0] not in replaced] + replacements


@pytest.mark.parametrize('car', (CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9))
@pytest.mark.parametrize('speed', (0.3125, 2.4, 4.5))
@pytest.mark.parametrize('direction', (-1, 1))
def test_direct_eps_angle_steps_obey_hardware_rate_and_keep_fault_withdrawal(car, speed, direction):
  build = pe_candidate if car == CAR.HYUNDAI_IONIQ_5_PE else ev9_candidate
  cp = build(params(candidate=car), enabled=True, is_release=False)
  assert cp.openpilotLongitudinalControl and not cp.pcmCruise
  assert cp.safetyConfigs[0].safetyParam == (0x5495 if car == CAR.HYUNDAI_IONIQ_5_PE else 0x5C95)
  ci = CarInterface(cp)
  # Controller-only calibration test; startup/native authority are qualified separately.
  ci.CC.pe_startup = SimpleNamespace(handed_off=True, can_emit=lambda controller: True)
  packer = CANPacker(DBC[car][Bus.pt])
  ci.update([])
  measured = -100.0 * direction
  for tick in range(1, 13):
    ci.update([(tick * 10_000_000, frame_inputs(packer, tick, speed, measured))])
  assert ci.CS.out.canValid and not ci.CS.out.canTimeout and not ci.CS.out.standstill
  command = structs.CarControl(enabled=True, latActive=True, longActive=True)
  command.actuators.steeringAngleDeg = 100.0 * direction
  ci.CC.apply_angle_last = measured
  ci.CC.ev9_angle_filter.x = command.actuators.steeringAngleDeg
  ci.CC.angle_gain_last = 1.0
  previous = measured
  for tick in range(13, 43):
    ci.update([(tick * 10_000_000, frame_inputs(packer, tick, speed, measured))])
    _, output = ci.apply(command.as_reader(), tick * 10_000_000)
    steering = [message for message in output if message[0] == 0xCB]
    assert len(steering) == 1 and steering[0][2] == 1
    body = steering[0][1]
    assert (body[3] >> 4) == 2
    assert int.from_bytes(body[:2], 'little') == hkg_can_fd_checksum(0xCB, None, body)
    raw = int.from_bytes(body[4:6], 'little') & 0x3FFF
    angle = ((raw ^ 0x2000) - 0x2000) / 10
    assert abs(angle - previous) <= 2.200001
    assert abs(angle) <= (175 if car == CAR.HYUNDAI_IONIQ_5_PE else 140)
    previous = angle
  ci.update([(430_000_000, frame_inputs(packer, 43, speed, measured, fault=2))])
  assert ci.CS.out.steerFaultTemporary
  _, output = ci.apply(command.as_reader(), 430_000_000)
  body = next(message[1] for message in output if message[0] == 0xCB)
  assert body[3] >> 4 == 1 and body[6] == 0
