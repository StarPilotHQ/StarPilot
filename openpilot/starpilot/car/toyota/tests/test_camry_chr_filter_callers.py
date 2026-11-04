"""Dom radar-filter takeover for pre-TSS2 Camry/C-HR."""
import pytest
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.car.toyota.tests import test_rav4_angle_filter_callers as helpers

source_matched_native = helpers.source_matched_native
daemon_gc_scope = helpers.daemon_gc_scope

@pytest.mark.parametrize('candidate', [CAR.TOYOTA_CAMRY, CAR.TOYOTA_CHR, CAR.TOYOTA_CHR_TSS2])
@pytest.mark.parametrize('alpha,disable', [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize('aol', [False, True])
def test_camry_chr_filter_actual_callers(candidate, alpha, disable, aol, tmp_path):
  helpers.run_case(candidate, alpha, disable, 0, tmp_path, aol=aol)


@pytest.mark.parametrize('alpha,disable', [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize('aol', [False, True])
def test_chr_filter_with_independent_cluster_radar_actual_callers(alpha, disable, aol, tmp_path):
  advertisements = [(0x2FF, bytes(4), 0), (0x2FF, bytes(4), 2), (0x2FF, bytes(8), 1)]
  advertisements.extend((address, bytes(8), 1) for address in range(0x680, 0x686))
  helpers.run_case(CAR.TOYOTA_CHR, alpha, disable, 0, tmp_path, aol=aol, filter_advertisements=advertisements)
