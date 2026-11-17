"""New auxiliary capability preserves actual stock/full native ownership."""
import pytest
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.car.toyota.tests import test_rav4_angle_filter_callers as helpers
from openpilot.starpilot.car.toyota.tests.test_camry_filter_radar_bus import observed

source_matched_native = helpers.source_matched_native
daemon_gc_scope = helpers.daemon_gc_scope


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('aol', (False, True))
def test_four_byte_camry_filter_actual_callers(stock, aol, tmp_path):
  frames = [(address, bytes(length), bus) for bus, messages in observed().items() for address, length in messages.items()]
  helpers.run_case(CAR.TOYOTA_CAMRY, True, stock, 0, tmp_path, aol=aol, filter_advertisements=frames)
