from unittest.mock import patch

import pytest

from opendbc.car.structs import car
from openpilot.selfdrive.selfdrived import selfdrived


@pytest.mark.parametrize('brand', ['hyundai', 'toyota', 'honda', 'ford', 'mazda', 'nissan', 'subaru',
                                   'volkswagen', 'chrysler', 'rivian', 'tesla', 'body', 'psa', 'volvo', 'polestar', ''])
def test_non_gm_owner_does_not_run_gm_admission(brand):
  owner = selfdrived.SelfdriveD.__new__(selfdrived.SelfdriveD)
  owner.CP = car.CarParams(brand=brand)
  owner.steering_authority_unavailable = True
  with patch.object(selfdrived, 'monitored_profile', side_effect=AssertionError('Unrelated GM admission')):
    owner._update_steering_authority(None)
  assert owner.steering_authority_unavailable is False


def test_gm_admission_remains_live_on_every_call():
  owner = selfdrived.SelfdriveD.__new__(selfdrived.SelfdriveD)
  owner.CP = car.CarParams(brand='gm')
  owner.steering_authority_unavailable = True
  with patch.object(selfdrived, 'monitored_profile', return_value=False) as admission:
    owner._update_steering_authority(None)
    owner.CP.flags = 1
    owner._update_steering_authority(None)
  assert admission.call_count == 2
  assert owner.steering_authority_unavailable is False


def test_changed_brand_is_not_hidden_by_a_startup_cache():
  owner = selfdrived.SelfdriveD.__new__(selfdrived.SelfdriveD)
  owner.CP = car.CarParams(brand='hyundai')
  with patch.object(selfdrived, 'monitored_profile', return_value=False) as admission:
    owner._update_steering_authority(None)
    admission.assert_not_called()
    owner.CP.brand = 'gm'
    owner._update_steering_authority(None)
    admission.assert_called_once_with(owner.CP)
