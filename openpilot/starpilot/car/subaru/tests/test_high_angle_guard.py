import pytest

from opendbc.car import Bus, structs
from opendbc.can import CANParser
from opendbc.car.subaru.high_angle_guard import HighAngleGuard, selected
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.values import CAR, DBC
from openpilot.starpilot.car.subaru.tests.caller_helpers import CompleteCan


@pytest.mark.parametrize('sign', [-1, 1])
def test_guard_holds_until_vdc_angle_unwinds(sign):
  guard = HighAngleGuard()
  assert not guard.update(sign * 87.9, 8, 59)
  assert guard.update(sign * 88, 8, 59)
  assert guard.update(sign * 85, 8, 200)
  assert guard.update(sign * 84, 8, 0)
  assert not guard.update(sign * 83.9, 8, 59)
  assert not guard.update(sign * 100, 8, 120)
  assert guard.update(sign * 100, 8, 119)
  assert not guard.update(sign * 100, 50, 59)


@pytest.mark.parametrize('candidate', [CAR.SUBARU_IMPREZA_2020, CAR.SUBARU_IMPREZA, CAR.SUBARU_FORESTER, CAR.SUBARU_CROSSTREK_2025])
def test_guard_scope(candidate):
  cp = CarInterface.get_non_essential_params(candidate)
  assert selected(cp) == (candidate == CAR.SUBARU_IMPREZA_2020)
  cp.openpilotLongitudinalControl = True
  assert not selected(cp)


def _interface():
  ci = CarInterface(CarInterface.get_non_essential_params(CAR.SUBARU_IMPREZA_2020))
  stream = CompleteCan(ci)
  return ci, stream


def _values(vdc=0, warning=False):
  return {'Steering_Torque': {'Steering_Angle': 70, 'Steer_Torque_Sensor': 59, 'Steer_Warning': int(warning)},
          'Brake_Pressure_L_R': {'Steering_Angle': vdc}, 'Brake_Pedal': {'Speed': 8},
          'Wheel_Speeds': dict.fromkeys(('FL', 'FR', 'RL', 'RR'), 8), 'Transmission': {'Gear': 121}}


def test_real_parser_uses_vdc_angle_preserves_hardware_warning_and_zeroes_stale_command():
  ci, stream = _interface()
  for tick in range(100):
    cs = ci.update([(5_000_000_000 + tick * 10_000_000, stream.frames(_values(101), tick=tick))])
  assert cs.canValid and cs.steeringAngleDeg < 88 and cs.steerFaultTemporary
  command = structs.CarControl(latActive=True, actuators=structs.CarControl.Actuators(torque=1))
  decoder = CANParser(DBC[CAR.SUBARU_IMPREZA_2020][Bus.pt], [('ES_LKAS', 0)], 0)
  for tick in range(100, 110):
    now = 5_000_000_000 + tick * 10_000_000
    output, outgoing = ci.apply(command.as_reader(), now)
    decoder.update([(now, outgoing)])
    assert output.torque == 0
    if any(address == 0x122 for address, _, _ in outgoing):
      assert decoder.vl['ES_LKAS']['LKAS_Request'] == 0
      assert decoder.vl['ES_LKAS']['LKAS_Output'] == 0
  cs = ci.update([(6_100_000_000, stream.frames(_values(0, warning=True), tick=110))])
  assert not ci.CS.high_angle_cut and cs.steerFaultTemporary


@pytest.mark.parametrize('missing', ['Brake_Pressure_L_R', 'Brake_Pedal'])
def test_missing_guard_source_invalidates_actual_parser(missing):
  ci, stream = _interface()
  for tick in range(100):
    cs = ci.update([(5_000_000_000 + tick * 10_000_000, stream.frames(_values(), tick=tick))])
  assert cs.canValid
  for tick in range(100, 250):
    cs = ci.update([(5_000_000_000 + tick * 10_000_000, stream.frames(_values(), tick=tick, omit=((Bus.pt, missing),)))])
  assert not cs.canValid
