"""Real daemon/transport/public-Panda parser with declared health, not road proof."""
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from opendbc.car.car_helpers import interfaces
from opendbc.car.toyota.values import CAR

from openpilot.cereal import messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.flm import steerlearnerd
from openpilot.starpilot.flm.fullrate_collector import FullrateCollector
from openpilot.starpilot.flm.runtime_adapter_registry import SourceState, car_params_digest
from openpilot.starpilot.flm.tests.test_fullrate_pipeline import SyntheticAdapter, snapshots, serialized_control, serialized_output

BASE = 10_000_000_000


@pytest.mark.parametrize('native_allowed', (True, False))
def test_actual_merged_daemon_callbacks_keep_fresh_tail_and_current_native_checks(monkeypatch, native_allowed):
  class Finished(Exception):
    pass

  now = [BASE + 160_000_000]
  cp = interfaces[CAR.TOYOTA_RAV4_TSS2].get_non_essential_params(CAR.TOYOTA_RAV4_TSS2)
  source = SourceState(True, False, '0', '2' * 64, BASE - 10_000_000, BASE + 490_000_000,
    car_params_digest(cp), (0., 0., 0.), 'c' * 32, 'd' * 64)
  calls, instances, physical = [], [], []

  class Adapter(SyntheticAdapter):
    def select_objective(self, key):
      pass

    def prepare_control(self, value):
      return value

  class Collector(FullrateCollector):
    def control(self, value, now_ns):
      accepted = super().control(value, now_ns)
      calls.append(('control', value.control_ns, self._learning_current_native_allowed, accepted, now_ns))
      return accepted

    def output(self, value, now_ns):
      accepted = super().output(value, now_ns)
      calls.append(('output', value.apply_ns, self._learning_current_native_allowed, accepted, now_ns))
      return accepted

  def factory(adapter):
    collector = Collector(adapter)
    instances.append(collector)
    return collector

  sockets = object(), object()
  rows = [snapshots(BASE + offset)[:2] for offset in (0, 150_000_000)]
  queues = {sockets[0]: deque(serialized_control(c) for c, _ in rows),
            sockets[1]: deque(serialized_output(o) for _, o in rows)}

  def receive(socket):
    return queues[socket].popleft() if queues[socket] else None

  def clock():
    now[0] += 100
    return now[0]

  def observe(_sm, _cp, collector, stamp):
    physical.append((tuple(collector.ready), stamp, collector._learning_current_native_allowed))
    return None  # No physical evidence is invented for this acquisition unit.

  fitter = Mock(child=object())
  fitter.poll.side_effect = Finished
  monkeypatch.setattr(steerlearnerd, 'FullrateCollector', factory)
  monkeypatch.setattr(steerlearnerd.messaging, 'recv_one_or_none', receive)
  with OpenpilotPrefix():
    sm = messaging.SubMaster(['pandaStates', 'deviceMotion'])
    # Actual public message decoding/checks, with explicitly declared test state.
    for stamp in (BASE - 40_000_000, BASE + 60_000_000, BASE + 150_000_000):
      event = messaging.new_message('pandaStates', len(cp.safetyConfigs), valid=True, logMonoTime=stamp)
      for panda, config in zip(event.pandaStates, cp.safetyConfigs, strict=True):
        panda.safetyModel, panda.safetyParam = config.safetyModel, config.safetyParam
        panda.alternativeExperience = cp.alternativeExperience
        panda.controlsAllowed = native_allowed and str(config.safetyModel) not in ('silent', 'noOutput')
      sm.update_msgs(stamp / 1e9, [messaging.log_from_bytes(event.to_bytes())])
    assert sm.freq_ok['pandaStates']
    updates = []
    monkeypatch.setattr(sm, 'update', lambda timeout=None: updates.append(timeout))
    with pytest.raises(Finished):
      steerlearnerd.run(cp, sm, *sockets, SimpleNamespace(send=lambda *args: None),
        lambda *args: Adapter(), observe, lambda stamp: source,
        lambda row: ('fixture',), lambda row: 'fixture', lambda *args: None,
        None, fitter, None, clock=clock)
  assert len(instances) == 1 and all(not queue for queue in queues.values())
  assert [kind for kind, *_ in calls] == ['control', 'output', 'control', 'output']
  assert [accepted for *_, accepted, _stamp in calls] == [False, False, True, True]
  assert [native for _kind, _original, native, _accepted, _stamp in calls] == [native_allowed, False, native_allowed, native_allowed]
  assert all(stamp >= BASE + 160_000_000 for *_rest, stamp in calls)
  assert updates == [None, 0]  # Final pending source/native refresh remains mandatory.
  assert len(physical) == 1 and len(physical[0][0]) == 1
  assert physical[0][0][0][0].control_ns == BASE + 150_000_000
  assert physical[0][2] is native_allowed  # Acquisition does not create native permission.
  assert fitter.poll.call_args.kwargs['token'] is None
  assert not instances[0].ready  # Neither branch supplied a physical witness.
  fitter.close.assert_called_once_with('learner_parent_exit')


def test_bounded_yield_keeps_actual_daemon_fresh_native_tail(monkeypatch):
  from dataclasses import replace
  from openpilot.starpilot.flm.fullrate_collector import MAX_DRAIN
  from openpilot.starpilot.flm.tests.test_native_health import toyota_cp, typed_state

  class Finished(Exception):
    pass

  cp = toyota_cp()
  now = BASE + 341_000_000
  physical, collectors, callbacks = [], [], []

  class Adapter(SyntheticAdapter):
    def select_objective(self, key):
      pass

    def prepare_control(self, row):
      return row

  def factory(adapter):
    collector = FullrateCollector(adapter)
    collectors.append(collector)
    return collector

  def observe(_sm, _cp, collector, stamp):
    physical.append((tuple(collector.ready), stamp, collector._learning_current_native_allowed))
    return None

  fitter = Mock(child=object())
  fitter.poll.side_effect = Finished
  monkeypatch.setattr(steerlearnerd, 'FullrateCollector', factory)
  with OpenpilotPrefix():
    from openpilot.starpilot.flm.runtime_adapter_registry import current_authority

    native = typed_state(cp, BASE + 330_000_000)
    allowed, _, identity = current_authority(native, cp, now_ns=now, required=True)
    assert allowed and identity

    class Receiver:
      updated = {'deviceMotion': False}
      logMonoTime = {**native.logMonoTime, 'deviceMotion': 0}

      def __getitem__(self, service):
        return native[service]

      def __getattr__(self, name):
        return getattr(native, name)

      def update(self, timeout=None):
        callbacks.append(timeout)

    source = SourceState(True, False, '0', '2' * 64, BASE - 10_000_000, BASE + 490_000_000,
      car_params_digest(cp), (0., 0., 0.), 'c' * 32, 'd' * 64)
    pairs = []
    for index in range(MAX_DRAIN + 3):
      stamp = BASE + index * 10_000_000
      control, output, _ = snapshots(stamp)
      control = replace(control, native_required=True, native_allowed=True, native_identity=identity,
        native_observed_ns=stamp - 1_000_000, native_expires_ns=stamp + 199_000_000)
      pairs.append((control, output))
    sockets = object(), object()
    queues = {sockets[0]: deque(serialized_control(c) for c, _ in pairs),
              sockets[1]: deque(serialized_output(o) for _, o in pairs)}
    monkeypatch.setattr(steerlearnerd.messaging, 'recv_one_or_none',
      lambda socket: queues[socket].popleft() if queues[socket] else None)
    with pytest.raises(Finished):
      steerlearnerd.run(cp, Receiver(), *sockets, SimpleNamespace(send=lambda *args: None),
        lambda *args: Adapter(), observe, lambda stamp: source,
        lambda row: ('fixture',), lambda row: 'fixture', lambda *args: None,
        None, fitter, None, clock=lambda: now)
  assert len(physical) == 1 and physical[0][0] == tuple(pairs[25:MAX_DRAIN - 1])
  assert physical[0][1] == now and physical[0][2] is True
  assert collectors[0].dropped == 0
  assert collectors[0].training_interruptions['stale_control'] == 25
  assert callbacks == [None, 0]
  assert not collectors[0].ready
  fitter.close.assert_called_once_with('learner_parent_exit')
