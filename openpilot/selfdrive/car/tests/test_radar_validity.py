from itertools import product
from unittest.mock import Mock

import pytest

from opendbc.car.structs import car
from openpilot.selfdrive.car.card import Car


@pytest.mark.parametrize("can_error,radar_fault,wrong_config,unavailable", list(product((False, True), repeat=4)))
def test_radar_publication_retains_faults(can_error, radar_fault, wrong_config, unavailable):
  rd = car.RadarData()
  rd.errors.canError = can_error
  rd.errors.radarFault = radar_fault
  rd.errors.wrongConfig = wrong_config
  rd.errors.radarUnavailableTemporary = unavailable
  owner = Car.__new__(Car)
  owner.pm = Mock()
  Car.radar_publish(owner, rd)
  owner.pm.send.assert_called_once()
  service, message = owner.pm.send.call_args.args
  assert service == 'radarTracks'
  assert message.valid == (not can_error)
  assert message.radarTracks.to_dict() == rd.to_dict()


def test_no_completed_radar_batch_does_not_publish():
  owner = Car.__new__(Car)
  owner.pm = Mock()
  Car.radar_publish(owner, None)
  owner.pm.send.assert_not_called()
