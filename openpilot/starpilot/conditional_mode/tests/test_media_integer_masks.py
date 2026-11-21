from types import SimpleNamespace
from unittest.mock import patch

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.starpilot.conditional_mode.manual import ioniq6_media_eligible


def params(car=CAR.KIA_EV6, hda2=True, offset=0, alpha=False):
  fp = gen_empty_fingerprint()
  bus = offset + int(hda2)
  fp[bus].update({0x1CF: 8, 0x35: 32, 0x130: 16, 0x175: 24, 0xA0: 24, 0xEA: 24})
  if hda2:
    fp[offset + 2][0x50] = 16
  return CarInterface.get_params(car, fp, [], alpha, False, False)


@pytest.mark.parametrize('car', (CAR.KIA_EV6, CAR.HYUNDAI_IONIQ_6))
@pytest.mark.parametrize('hda2', (False, True))
@pytest.mark.parametrize('offset', (0, 4))
@pytest.mark.parametrize('alpha', (False, True))
def test_live_factory_topologies_do_not_rebuild_flag_masks(car, hda2, offset, alpha):
  cp = params(car, hda2, offset, alpha)
  expected = car == CAR.KIA_EV6 or hda2
  with patch.object(HyundaiFlags, '__or__', side_effect=AssertionError('live mask allocation')):
    for _ in range(20):
      assert ioniq6_media_eligible(cp) is expected


@pytest.mark.parametrize('change', ('brand', 'car', 'canfd', 'ev', 'angle', 'ccnc', 'owner', 'second_owner'))
def test_mutated_profile_is_rechecked_on_every_call(change):
  cp = params()
  assert ioniq6_media_eligible(cp)
  if change == 'brand':
    cp.brand = 'other'
  elif change == 'car':
    cp.carFingerprint = CAR.KIA_NIRO_EV_2ND_GEN
  elif change in ('canfd', 'ev'):
    cp.flags &= ~int(HyundaiFlags.CANFD if change == 'canfd' else HyundaiFlags.EV)
  elif change in ('angle', 'ccnc'):
    cp.flags |= int(HyundaiFlags.CANFD_ANGLE_STEERING if change == 'angle' else HyundaiFlags.CCNC)
  elif change == 'owner':
    cp.safetyConfigs = []
  else:
    cp.safetyConfigs = [cp.safetyConfigs[0].to_dict()] * 2
  assert ioniq6_media_eligible(cp) is False


def test_unrelated_car_preserves_short_circuit_without_reading_flags():
  assert ioniq6_media_eligible(SimpleNamespace(brand='other', carFingerprint='unrelated')) is False
