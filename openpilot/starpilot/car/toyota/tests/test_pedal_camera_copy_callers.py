"""Real CI/Card/Controls/SD/C++/native; bus2 cannot replace primary pedal RX."""
import pytest
from openpilot.starpilot.car.toyota.tests import test_pedal_parent_callers as parent
from openpilot.starpilot.car.toyota.tests.test_toyota_cpp_transport import host_heartbeat_bridge as host_heartbeat_bridge

source_matched_native = parent.source_matched_native
daemon_gc_scope = parent.daemon_gc_scope

ROWS = (
  ('TOYOTA_RAV4H', '2FF8', 'catalog'),
  ('TOYOTA_PRIUS', '2FF4', 'catalog'),
  ('TOYOTA_PRIUS_RETROFIT', '2FF8', 'EPS73_8965B47070'),
  ('LEXUS_GS_F', '2FF4', 'catalog'),
  ('TOYOTA_CAMRY', 'camera343_or_4CB8', 'catalog'),
  ('TOYOTA_CHR', '2AA8', 'catalog'),
  ('TOYOTA_COROLLA_TSS2', 'standalone2016', 'catalog'),
)


@pytest.mark.parametrize('name,topology,rack', ROWS)
@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('kind', ('nominal', 'pedal_drop'))
def test_camera_pedal_copy_cannot_create_or_retain_authority(name, topology, rack, stock, kind, host_heartbeat_bridge):
  parent.run(name, topology, rack, stock, host_heartbeat_bridge, kind, mirror=True)
