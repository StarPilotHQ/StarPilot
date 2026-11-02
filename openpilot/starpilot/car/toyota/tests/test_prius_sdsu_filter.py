"""Original Prius source ownership preserves every current numeric CP field."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.toyota import prius_sdsu, prius_longitudinal, prius_advert_filter
from opendbc.car.toyota.carstate import CarState
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR, ToyotaFlags
from openpilot.starpilot.car.toyota.tests.test_rav4h_caller_native import firmware

BASE = json.loads((Path(__file__).parent / 'data/prius-sdsu-factory-before.json').read_text())
VERSIONS = sorted({bytes.fromhex(key.split(':')[0]) for key in BASE})
ORIGINAL = [version for version in VERSIONS if version != prius_sdsu.RETROFIT_EPS]


def original_firmware(version=ORIGINAL[0]):
  fw = firmware(CAR.TOYOTA_PRIUS)
  for item in fw:
    if item.ecu == structs.CarParams.Ecu.eps:
      item.fwVersion = version
  return fw


def factory(version=ORIGINAL[0], *, hardware='2FF8', alpha=False, mirror=False):
  fp = gen_empty_fingerprint()
  if hardware != 'none':
    fp[0][0x2FF] = int(hardware[-1])
  if mirror:
    fp[2][0x2FF] = int(hardware[-1])
  return CarInterface.get_params(CAR.TOYOTA_PRIUS, fp, original_firmware(version), alpha, False, False)


@pytest.mark.parametrize('label', sorted(BASE))
def test_full_factory_cp_and_numeric_tunes(label):
  version, hardware, alpha = label.split(':')
  version = bytes.fromhex(version)
  alpha = alpha == 'alpha=True'
  cp = factory(version, hardware=hardware, alpha=alpha)
  expected = deepcopy(BASE[label])
  expected['flags'] |= int(ToyotaFlags.HYBRID)
  if version != b'8965B47060' + bytes(6):
    expected['steerActuatorDelay'] = pytest.approx(.14)
    expected['lateralTuning']['torque']['steeringAngleDeadzoneDeg'] = pytest.approx(.3)
  if hardware == 'none' and version != prius_sdsu.RETROFIT_EPS:
    expected['safetyConfigs'][0]['safetyParam'] = 0x6242
  owner = prius_sdsu if hardware == '2FF8' else prius_advert_filter
  if version in ORIGINAL and hardware in ('2FF4', '2FF8'):
    expected['flags'] |= int(ToyotaFlags.HYBRID | ToyotaFlags.LONG_FILTER | ToyotaFlags.RAISED_ACCEL_LIMIT)
    expected['longitudinalActuatorDelay'] = pytest.approx(.05)
    expected['openpilotLongitudinalControl'] = True
    expected['safetyConfigs'][0]['safetyParam'] = owner.LONG_WORD
    assert owner.configured(cp) and prius_longitudinal.enabled(cp)
    assert factory(version, hardware=hardware, alpha=alpha, mirror=True).to_dict() == cp.to_dict()
    assert CarState(cp).eps_torque_scale == .66
    actual = cp.to_dict()
    assert actual == expected
    assert prius_longitudinal.prepare_stock(cp)
    expected['openpilotLongitudinalControl'] = False
    expected['safetyConfigs'][0]['safetyParam'] = owner.STOCK_WORD
    assert cp.to_dict() == expected and owner.configured(cp)
    assert CarState(cp).eps_torque_scale == .66
  else:
    assert not prius_sdsu.configured(cp)
    if version == prius_sdsu.RETROFIT_EPS and hardware != 'none':
      expected['safetyConfigs'][0]['safetyParam'] = 0xD049 if hardware == '2FF4' else 0x8049
      expected['longitudinalActuatorDelay'] = pytest.approx(.05)
    assert cp.to_dict() == expected
    if version == prius_sdsu.RETROFIT_EPS and hardware != 'none':
      assert cp.safetyConfigs[0].safetyParam == (0xD049 if hardware == '2FF4' else 0x8049)
      assert CarState(cp).eps_torque_scale == .73


@pytest.mark.parametrize('bad', ('unknown_eps', 'unknown_camera', 'missing_eps', 'missing_camera',
                                 'logging_eps', 'pedal', 'twoaa', 'bus1_status'))
def test_real_source_identity_cannot_be_replaced_by_a_preference(bad):
  fp = gen_empty_fingerprint()
  fp[0][0x2FF] = 8
  fw = original_firmware()
  if bad.startswith('unknown_'):
    ecu = structs.CarParams.Ecu.eps if bad == 'unknown_eps' else structs.CarParams.Ecu.fwdCamera
    for item in fw:
      if item.ecu == ecu:
        item.fwVersion = b'unknown'
  elif bad.startswith('missing_'):
    ecu = structs.CarParams.Ecu.eps if bad == 'missing_eps' else structs.CarParams.Ecu.fwdCamera
    fw = [item for item in fw if item.ecu != ecu]
  elif bad == 'logging_eps':
    for item in fw:
      if item.ecu == structs.CarParams.Ecu.eps:
        item.logging = True
  elif bad == 'pedal':
    fp[0][0x201] = 5
  elif bad == 'twoaa':
    fp[0][0x2AA] = 8
  else:
    fp[1][0x2FF] = 8
  assert not prius_sdsu.hardware_supported(CAR.TOYOTA_PRIUS, fp, fw)
  cp = CarInterface.get_params(CAR.TOYOTA_PRIUS, fp, fw, True, False, False)
  assert not prius_sdsu.configured(cp) and cp.safetyConfigs[0].safetyParam not in prius_sdsu.WORDS
