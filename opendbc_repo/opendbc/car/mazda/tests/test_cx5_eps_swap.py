import pytest

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.mazda.interface import CarInterface
from opendbc.car.mazda.stock_aol import temporary_restriction
from opendbc.car.mazda.tests.test_stock_startup import complete_frames
from opendbc.car.mazda.values import CAR


def recorded_firmware():
  return [structs.CarParams.CarFw(ecu=ecu, address=address, brand='mazda', fwVersion=part.ljust(24, b'\x00'))
          for ecu, address, part in (
            ('engine', 0x7E0, b'PX2H-188K2-E'),
            ('transmission', 0x7E1, b'PYB2-21PS1-G'),
            ('eps', 0x730, b'KSD5-3210X-C-00'),
          )]


@pytest.mark.parametrize('profile,defect,expected', [
  (CAR.MAZDA_CX5, None, 0.),
  (CAR.MAZDA_CX5, 'missing_eps', 12.5),
  (CAR.MAZDA_CX5, 'missing_engine', 12.5),
  (CAR.MAZDA_CX5, 'wrong_transmission', 12.5),
  (CAR.MAZDA_CX5, 'wrong_eps_bus', 12.5),
  (CAR.MAZDA_CX5, 'conflicting_eps', 12.5),
  (CAR.MAZDA_CX5_2022, 'no_fw', 0.),
  (CAR.MAZDA_CX9_2021, None, 12.5),
])
def test_low_speed_eligibility_is_limited_to_verified_cx5(profile, defect, expected):
  fw = recorded_firmware()
  if defect == 'missing_eps':
    del fw[2]
  elif defect == 'missing_engine':
    del fw[0]
  elif defect == 'wrong_transmission':
    fw[1].fwVersion = b'unknown'
  elif defect == 'wrong_eps_bus':
    fw[2].bus = 1
  elif defect == 'conflicting_eps':
    fw.append(structs.CarParams.CarFw(ecu='eps', address=0x730, brand='mazda', fwVersion=b'unknown'))
  elif defect == 'no_fw':
    fw.clear()
  cp = CarInterface.get_params(profile, gen_empty_fingerprint(), fw, False, False, False)
  assert cp.minSteerSpeed == expected
  assert cp.pcmCruise and not cp.openpilotLongitudinalControl and not cp.dashcamOnly
  assert [(str(s.safetyModel), s.safetyParam) for s in cp.safetyConfigs] == [('mazda', 0)]


def test_eps_readiness_still_blocks_lateral_at_low_speed():
  cp = CarInterface.get_params(CAR.MAZDA_CX5, gen_empty_fingerprint(), recorded_firmware(), False, False, False)
  cp.alternativeExperience = 32
  ci = CarInterface(cp)
  packer = CANPacker('mazda_2017')
  ci.update([(10_000_000, complete_frames(packer, cp, 1, main=True, speed_kph=16., temporary=True))])
  for tick, (speed, blocked, restricted) in enumerate(((16., True, True), (16., False, False),
                                                        (16., True, True), (0., False, True)), 2):
    frames = complete_frames(packer, cp, tick, main=True, speed_kph=speed, temporary=blocked)
    state = ci.update([(tick * 10_000_000, frames)])
    assert state.canValid and not state.canTimeout
    assert state.steerFaultTemporary == blocked
    assert temporary_restriction(cp, state) == restricted
