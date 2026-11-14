"""GM Traffic startup remains independent of the Ioniq controller owner."""
from unittest.mock import Mock, patch

from opendbc.car.gm.tests.test_bolt_pedal import params as pedal_params
from opendbc.car.gm.values import CAR
from openpilot.common.params import Params
from openpilot.selfdrive.controls import plannerd
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner
from openpilot.starpilot.longitudinal.tests.test_conditional_handoff import Frame, NOW, DRIVE


def test_gm_traffic_actual_planner_loop_uses_independent_settings_without_conditional_host(tmp_path):
  params = Params(str(tmp_path))
  cp = pedal_params(CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, setting=True, pedal=True)
  params.put('CarParams', cp.to_bytes(), block=True)
  params.put('DistanceButtonControl', 6, block=True)
  sm = Frame()
  sm.updated = dict.fromkeys(sm, True)
  sm.update = Mock()
  sm['deviceState'].startedMonoTime = DRIVE
  called = []
  sample = plannerd.TrafficOwner.sample
  class EndFrame(Exception):
    pass
  def observe(owner, event, **kwargs):
    assert isinstance(kwargs['settings'], ConditionalSettingsOwner)
    assert kwargs['cp'].brand == 'gm' and kwargs['drive_id'] == DRIVE
    assert kwargs['controller_toggle'] is False
    verdict = sample(owner, event, **kwargs)
    assert not verdict.requested and verdict.effective is not True
    called.append(verdict)
    raise EndFrame
  with (patch.object(plannerd, 'Params', return_value=params),
        patch('openpilot.common.params.Params', return_value=params),
        patch.object(plannerd, 'config_realtime_process'),
        patch.object(plannerd, 'feature_enabled', return_value=False),
        patch.object(plannerd, 'feature_requested', side_effect=lambda p, feature: feature == 'conditional'),
        patch.object(plannerd, 'ConditionalPlannerHost') as conditional,
        patch.object(plannerd, 'ModeActionOwner') as controller,
        patch.object(plannerd, 'paired_clocks_ns', return_value=(NOW, NOW + 2_000_000_000, 0)),
        patch.object(plannerd.time, 'monotonic_ns', return_value=NOW),
        patch.object(plannerd.messaging, 'sub_sock'),
        patch.object(plannerd.messaging, 'recv_one_or_none', return_value=None),
        patch.object(plannerd.messaging, 'SubMaster', return_value=sm),
        patch.object(plannerd.messaging, 'PubMaster'),
        patch.object(plannerd.TrafficOwner, 'sample', observe),
        patch.dict('os.environ', {}, clear=True)):
    try:
      plannerd.starpilot_main()
    except EndFrame:
      pass
    else:
      raise AssertionError('GM Traffic loop did not sample')
    conditional.assert_not_called()
    controller.assert_called_once_with()
  assert len(called) == 1
