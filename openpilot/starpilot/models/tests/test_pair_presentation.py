from dataclasses import replace
from unittest.mock import Mock, patch

from openpilot.starpilot.models.catalog import BUNDLED_CURRENT, model_entries
from openpilot.starpilot.models.runtime import ModelStatusSource
from openpilot.starpilot.models.status import ModelStatus, ModelHealth, LaboratoryLoad
from openpilot.starpilot.models.tests.test_runtime import FakeSubMaster, FakeParams
from openpilot.starpilot.ui.models_state import model_display_text


def test_actual_pair_status_reaches_home_and_galaxy_and_expires():
  sm = FakeSubMaster()
  sm.update = Mock()
  sm.messages['modelV2'].big = True
  source = ModelStatusSource(FakeParams(True), sm, lambda: 2_000_000_000)
  ids = list(model_entries())[:2]
  config = {"enabled": True, "lateralModel": ids[1], "longitudinalModel": ids[0]}
  load = LaboratoryLoad(421, 54321, 1_800_000_000, ids[0], 'v16', 'a'*64, ids[1], 'v16', 'b'*64, 'c'*64)
  normal = ModelStatus(BUNDLED_CURRENT, None, None, ModelHealth.IDENTITY_UNAVAILABLE, False, None, None)
  with patch.object(source, 'snapshot', return_value=normal), \
       patch.object(source, 'laboratory_configuration', return_value=config), \
       patch('openpilot.starpilot.models.runtime.process_start_ticks', return_value=54321), \
       patch('openpilot.starpilot.models.runtime.read_pair_receipt', return_value=load) as receipt:
    status = source.json()['laboratory']
    assert status['active'] and status['pendingNextStart']
    assert (status['lateralModel'], status['longitudinalModel']) == tuple(ids)
    assert '/' in source.home_label()
    sm.valid['drivingModelData'] = False
    assert source.json()['laboratory']['health'] == 'stale'
    assert source.home_label() == '/'.join(model_display_text(model_entries()[mid].name) for mid in reversed(ids))
    sm.valid['drivingModelData'] = True
    receipt.return_value = replace(load, process_start_ticks=99999)
    assert not source.json()['laboratory']['active']
    assert '/' in source.home_label()
