"""Camry/C-HR cluster-radar traffic must not impersonate a bus-0 filter status."""
import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.toyota import radar_filter
from opendbc.car.toyota.cluster_radar import CLUSTERS
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.car.toyota.aol import qualified


def observed():
  fp = gen_empty_fingerprint()
  fp[0][0x2FF] = fp[2][0x2FF] = 4
  fp[1] = {0x2FF: 8, **dict.fromkeys(CLUSTERS, 8)}
  return fp


@pytest.mark.parametrize('candidate', (CAR.TOYOTA_CAMRY, CAR.TOYOTA_CHR))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('primary', (0x2FF, 0x2AA))
def test_camry_radar_bus_preserves_exact_filter_owner(candidate, alpha, primary):
  fp = observed()
  if primary == 0x2AA:
    fp[0] = fp[2] = {primary: 8}
  canonical = gen_empty_fingerprint()
  canonical[0][primary] = 4 if primary == 0x2FF else 8
  expected = CarInterface.get_params(candidate, canonical, [], alpha, False, False)
  actual = CarInterface.get_params(candidate, fp, [], alpha, False, False)
  assert radar_filter.hardware_supported(candidate, fp)
  assert actual.to_dict() == expected.to_dict()
  assert actual.safetyConfigs[0].safetyParam == (0x1049 if alpha else 0x1249)
  assert actual.openpilotLongitudinalControl == alpha and qualified(actual)


@pytest.mark.parametrize('candidate', (CAR.TOYOTA_CAMRY, CAR.TOYOTA_CHR))
@pytest.mark.parametrize('bad', ('missing_filter', 'bus1_short', 'missing_cluster', 'bad_cluster',
                                  'wrong_cluster_bus', 'wrong_model', 'bus1_advert', 'mixed_status', 'pedal'))
def test_camry_radar_bus_is_not_general_adapter_evidence(candidate, bad):
  fp = observed()
  if bad == 'missing_filter':
    fp[0].clear()
  elif bad == 'bus1_short':
    fp[1][0x2FF] = 7
  elif bad == 'missing_cluster':
    del fp[1][CLUSTERS[-1]]
  elif bad == 'bad_cluster':
    fp[1][CLUSTERS[-1]] = 7
  elif bad == 'wrong_cluster_bus':
    fp[3] = fp.pop(1)
  elif bad == 'wrong_model':
    candidate = CAR.TOYOTA_CHR_TSS2
  elif bad == 'bus1_advert':
    fp[1][0x2FF] = 4
  elif bad == 'mixed_status':
    fp[2][0x2FF] = 8
  else:
    fp[0][0x201] = 6
  assert not radar_filter.hardware_supported(candidate, fp)
