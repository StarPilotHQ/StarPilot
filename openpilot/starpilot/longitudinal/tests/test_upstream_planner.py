"""Import actual upstream planner/MPC; mock only native solver and process IPC."""
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pytest
from openpilot.cereal import log, messaging
from openpilot.common.params import Params
from openpilot.selfdrive.controls import plannerd as starpilot_plannerd
from openpilot.starpilot.longitudinal.upstream import mpc, planner, plannerd
from openpilot.starpilot.speed_limits.tests import test_runtime_replay as replay


class Solver:
  def __init__(self, *_):
    self.costs = {}
  def reset(self):
    self.costs.clear()
  def cost_set(self, stage, key, value):
    self.costs[stage, key] = np.copy(value)
  def set(self, *_):
    pass


class TestUpstreamPlanner(unittest.TestCase):
  def test_actual_upstream_mpc_has_stock_weights_and_distinct_owner(self):
    with patch.object(mpc, 'AcadosOcpSolverCython', Solver):
      stock = mpc.LongitudinalMpc()
      self.assertEqual(stock.__class__.__module__, 'openpilot.starpilot.longitudinal.upstream.mpc')
      np.testing.assert_equal(np.diag(stock.solver.costs[0, 'W']), [3., 0., 0., 0., 200., 5.])
      stock.set_weights(personality=log.LongitudinalPersonality.aggressive)
      np.testing.assert_equal(np.diag(stock.solver.costs[0, 'W']), [3., 0., 0., 0., 100., 2.5])
      np.testing.assert_equal(stock.solver.costs[mpc.N, 'Zl'], stock.solver.costs[0, 'Zl'])
      stock.set_weights(prev_accel_constraint=False)
      self.assertEqual(stock.solver.costs[0, 'W'][4, 4], 0)
      self.assertEqual(mpc.get_T_FOLLOW(log.LongitudinalPersonality.relaxed), 1.75)
      self.assertEqual(mpc.get_T_FOLLOW(log.LongitudinalPersonality.standard), 1.45)
      self.assertEqual(mpc.get_T_FOLLOW(log.LongitudinalPersonality.aggressive), 1.25)
      with self.assertRaises(TypeError):
        method = getattr(stock, 'set_' + 'weights')
        method(acceleration_jerk=0.25)
      with self.assertRaises(TypeError):
        method = getattr(stock, 'up' + 'date')
        method(None, follow_seconds=3.)

  def test_actual_stock_planner_uses_isolated_mpc_and_preserves_cp(self):
    cp = SimpleNamespace(openpilotLongitudinalControl=True, pcmCruise=False)
    before = vars(cp).copy()
    with patch.object(mpc, 'AcadosOcpSolverCython', Solver):
      stock = planner.LongitudinalPlanner(cp)
    self.assertIs(stock.CP, cp)
    self.assertIs(type(stock.mpc), mpc.LongitudinalMpc)
    self.assertEqual(vars(cp), before)
    self.assertFalse(hasattr(stock, 'profile_tuning'))

  def test_upstream_process_without_slc_transport_uses_only_native_services(self):
    calls = []
    cp = SimpleNamespace(brand='honda', openpilotLongitudinalControl=True, passive=True)
    class SM:
      def update(self):
        raise InterruptedError('bounded end of mocked loop')
    def submaster(services, **kwargs):
      calls.append(('subscribe', services, kwargs))
      return SM()
    fake_params = SimpleNamespace(get=lambda key, **kwargs: b'CP')
    with patch.dict('os.environ', {}, clear=True), patch.object(plannerd, 'config_realtime_process'), \
         patch.object(plannerd, 'Params', return_value=fake_params), \
         patch.object(plannerd.messaging, 'log_from_bytes', return_value=cp), \
         patch.object(plannerd, 'LongitudinalPlanner', side_effect=lambda cp: calls.append(('planner', cp))), \
         patch.object(plannerd, 'LaneDepartureWarning', return_value=object()), \
         patch.object(plannerd.messaging, 'PubMaster', side_effect=lambda services: calls.append(('publish', services))), \
         patch.object(plannerd.messaging, 'SubMaster', side_effect=submaster):
      with self.assertRaises(InterruptedError):
        plannerd.main()
    self.assertEqual([item for item in calls if item[0] == 'publish'], [('publish', ['longitudinalPlan', 'driverAssistance'])])
    subscribed = next(item[1] for item in calls if item[0] == 'subscribe')
    self.assertEqual(subscribed, ['carControl', 'carState', 'controlsState', 'vehicleParameters', 'radarState', 'modelV2', 'selfdriveState'])
    self.assertFalse(any('starpilot' in value.lower() or value.startswith('slc') for value in subscribed))
    root = Path(__file__).parents[1] / 'upstream'
    self.assertEqual(Path(plannerd.__file__).resolve(), (root / 'plannerd.py').resolve())


@pytest.mark.parametrize('choice', ['wheel', 'screen', 'stock_cruise'])
def test_upstream_planner_applies_confirmed_slc_with_op_long(tmp_path, monkeypatch, choice):
  fixture = replay.RuntimeReplayTests()
  fixture.setUp()
  cp, sm = fixture.cp, fixture.sm
  cp.openpilotLongitudinalControl = choice != 'stock_cruise'
  sm['slcDashboardObservation'].speedMps = 15.
  sm.data['deviceState'] = messaging.new_message('deviceState').deviceState
  sm.valid['deviceState'] = sm.alive['deviceState'] = False
  params = Params(str(tmp_path))
  params.put('CarParams', cp.to_bytes(), block=True)
  params.put_bool('UseStarPilotLongitudinalPlanner', False, block=True)
  params.put_bool('SpeedLimitController', True, block=True)
  params.put('SLCPriority1', 'Dashboard', block=True)
  params.put('SLCConfirmation', 1, block=True)

  publisher = Mock()
  subscriber = Mock(return_value=sm)
  outputs = []
  events = {}
  start = 2_000_000_000

  class EndLoop(Exception):
    pass

  def update():
    sm.frame = len(outputs) + 1
    if sm.frame == 3:
      raise EndLoop
    sm.advance(start + (sm.frame - 1) * 50_000_000)
    if sm.frame != 2 or choice == 'stock_cruise':
      return
    shown = outputs[0].slcState
    assert shown.hasPending and not shown.hasCeiling
    service = 'slcCruiseEvent' if choice == 'wheel' else 'slcAction'
    event = messaging.new_message(service, valid=True)
    event.logMonoTime = sm.logMonoTime['modelV2']
    action = getattr(event, service)
    action.sessionId, action.decisionId, action.presentationId = shown.sessionId, shown.decisionId, shown.presentationId
    if choice == 'wheel':
      action.kind, action.button = 'confirmationAccept', 'accel'
      action.eventId, action.producerSessionId, action.observedMonoTime = 1, 'card-producer', event.logMonoTime
    else:
      action.kind, action.sequenceId = 'accept', 1
    events[service] = event

  def send(service, message):
    if service == 'slcState':
      outputs.append(message)

  sm.all_checks = lambda services=None: True
  monkeypatch.setattr(sm, 'update', update)
  monkeypatch.setenv('REPLAY', '1')
  monkeypatch.delenv('SLC_REPLAY_RUNTIME', raising=False)
  monkeypatch.setattr(starpilot_plannerd, 'Params', lambda: params)
  monkeypatch.setattr(plannerd, 'Params', lambda: params)
  monkeypatch.setattr(plannerd, 'config_realtime_process', Mock())
  monkeypatch.setattr(plannerd, 'LaneDepartureWarning', Mock(return_value=Mock(left=False, right=False)))
  monkeypatch.setattr(messaging, 'SubMaster', subscriber)
  monkeypatch.setattr(messaging, 'PubMaster', Mock(return_value=publisher))
  monkeypatch.setattr(messaging, 'sub_sock', lambda service, **kwargs: service)
  monkeypatch.setattr(messaging, 'recv_one_or_none', lambda service: events.pop(service, None))
  publisher.send.side_effect = send
  cruise = Mock(wraps=planner.get_cruise_accel)
  monkeypatch.setattr(planner, 'get_cruise_accel', cruise)

  with pytest.raises(EndLoop):
    starpilot_plannerd.main()

  assert 'slcDashboardObservation' in subscriber.call_args.kwargs['ignore_valid']
  assert cruise.call_args_list[0].args[1] == pytest.approx(100. / 3.6)
  assert sm['carState'].vCruise == 100.
  state = outputs[1].slcState
  if choice == 'stock_cruise':
    assert state.displayOnly and not state.hasPending and not state.hasCeiling
    assert cruise.call_args.args[1] == pytest.approx(100. / 3.6)
  else:
    assert state.hasCeiling and state.actionStatus == 'accept'
    assert cruise.call_args.args[1] == 15.
    # The planner queues history persistence without blocking its control loop.
    deadline = time.monotonic() + 5.
    while (saved_history := params.get('SLCQualifiedHistory')) is None and time.monotonic() < deadline:
      time.sleep(.01)
    assert saved_history is not None
    assert saved_history['speed_mps'] == 15.


if __name__ == '__main__':
  unittest.main()
