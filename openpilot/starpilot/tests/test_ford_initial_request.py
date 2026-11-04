import pytest
from opendbc.car.ford.values import CAR
from opendbc.car import structs
from openpilot.selfdrive.car import card as card_module
from openpilot.starpilot.tests.ford_lifecycle_helpers import ford_joined as ford_joined, run_actual_producer


@pytest.fixture(autouse=True)
def settings(monkeypatch, topology):
  monkeypatch.ford_bluepilot = True
  monkeypatch.ford_angle = True
  monkeypatch.ford_starpilot = True
  monkeypatch.ford_pinion = True
  monkeypatch.ford_topology = topology
  monkeypatch.ford_nonzero = True
  monkeypatch.ford_native_10hz = True


@pytest.mark.parametrize('identity,alpha,hands_free,topology', [
  ((CAR.FORD_MUSTANG_MACH_E_MK1, 2), True, True, 'automatic'),
  ((CAR.FORD_MUSTANG_MACH_E_MK1, 2), True, True, 'manual-multi'),
  ((CAR.FORD_MUSTANG_MACH_E_MK1, 2), False, True, 'automatic'),
  ((CAR.FORD_EXPLORER_MK6, 0), True, True, 'automatic'),
  ((CAR.FORD_F_150_MK14, 2), True, True, 'automatic'),
])
@pytest.mark.parametrize('scenario,delivery', [
  ('startup', 'paired'), ('startup', 'axis-delayed'), ('startup', 'state-delayed'),
  ('brake', 'health-first'), ('brake', 'wire-first'), ('startup', 'native-timeout'),
])
def test_initial_request_and_longitudinal_withdrawal(ford_joined, monkeypatch, scenario, delivery):
  invalid = [False]
  injected = [False]
  calls = []
  delayed = {}
  negative_checked = [False]
  if delivery == 'native-timeout':
    monkeypatch.ford_drop_native_requests = True
  if delivery == 'health-first':
    monkeypatch.ford_native_health_first = True
  if delivery == 'wire-first':
    monkeypatch.ford_native_wire_first = True

  def setup(host, sd, controls, local):
    if scenario == 'startup':
      sd.big_model_ready_t = card_module.time.monotonic()
    update = sd.sm.update_msgs

    def calibration(timestamp, messages):
      adjusted = []
      for message in messages:
        if message.which() in ('extrinsicsCalibration', 'driverMonitoringState') and invalid[0]:
          message = message.as_builder()
          message.valid = False
          message = message.as_reader()
        adjusted.append(message)
      if invalid[0] and not injected[0]:
        present = {message.which() for message in adjusted}
        for service in ('extrinsicsCalibration', 'driverMonitoringState'):
          if service not in present:
            message = card_module.messaging.new_message(service, valid=False)
            setattr(message, service, sd.sm[service])
            adjusted.append(message.as_reader())
        injected[0] = True
      return update(timestamp, adjusted)

    local.setattr(sd.sm, 'update_msgs', calibration)
    observe = card_module.ford_native_observation

    def traced_native(native, **kwargs):
      result = observe(native, **kwargs)
      calls.append({'now': kwargs['now_ns'], 'latched': kwargs['latched'], 'pending': kwargs['pending_since_ns'],
                    'restricted': kwargs['restricted'], 'result': result,
                    'native': None if native is None else [native.requestedLateral, native.lateralAllowed,
                                                         native.requestedLongitudinal, native.longitudinalAllowed]})
      return result

    local.setattr(card_module, 'ford_native_observation', traced_native)
    pending = getattr(card_module, 'ford_awaiting_initial_request', None)
    if pending is not None:
      def checked_pending(cp, cs, sm, native, feedback, intent, **kwargs):
        accepted = pending(cp, cs, sm, native, feedback, intent, **kwargs)
        if accepted and not negative_checked[0]:
          from copy import copy
          from dataclasses import replace
          from types import SimpleNamespace
          for fields in ({'canValid': False}, {'canTimeout': True}, {'brakePressed': True},
                         {'steerFaultPermanent': True}, {'steerFaultTemporary': True}, {'doorOpen': True}):
            state = structs.CarState(**cs.to_dict())
            for key, value in fields.items():
              setattr(state, key, value)
            assert not pending(cp, state.as_reader(), sm, native, feedback, intent, **kwargs)
          assert not pending(cp, cs, sm, None, feedback, intent, **kwargs)
          assert not pending(cp, cs, sm, replace(native, requestedLateral=True), feedback, intent, **kwargs)
          assert not pending(cp, cs, sm, replace(native, axisSessionId='other-session'), feedback, intent, **kwargs)
          assert not pending(cp, cs, sm, replace(native, pandaInventory=tuple(replace(p, faults=1) for p in native.pandaInventory)), feedback, intent, **kwargs)
          assert not pending(cp, cs, sm, native, feedback, SimpleNamespace(allowed_latch=False, pause_lateral=False), **kwargs)
          assert not pending(cp, cs, sm, native, feedback, SimpleNamespace(allowed_latch=True, pause_lateral=True), **kwargs)
          assert not pending(cp, cs, sm, native, feedback, intent, now_ns=kwargs['now_ns']+201_000_000, producer=kwargs['producer'])
          assert not pending(cp, cs, sm, native, feedback, intent, now_ns=kwargs['now_ns'], producer='other-producer')
          prior = copy(feedback)
          prior.ever_lateral_active = True
          assert not pending(cp, cs, sm, native, prior, intent, **kwargs)
          other = structs.CarParams(**cp.to_dict())
          other.brand = 'toyota'
          assert not pending(other, cs, sm, native, feedback, intent, **kwargs)
          class Snapshot:
            def __init__(self, service, value):
              self.service, self.value = service, value

            def __getitem__(self, service):
              return self.value if service == self.service else sm[service]

            def __getattr__(self, name):
              return getattr(sm, name)

          from openpilot.cereal import log
          event = log.OnroadEvent.new_message(name='controlsMismatch', immediateDisable=True)
          assert not pending(cp, cs, Snapshot('onroadEvents', [event.as_reader()]), native, feedback, intent, **kwargs)
          for key, value in (('sequence', 0), ('validUntilMonoTime', sm['aolAxisState'].validUntilMonoTime + 1),
                             ('sourceCarStateMonoTime', feedback.signature[8] - 1)):
            axis = sm['aolAxisState'].as_builder()
            setattr(axis, key, value)
            assert not pending(cp, cs, Snapshot('aolAxisState', axis.as_reader()), native, feedback, intent, **kwargs)
          assert pending(cp, cs, sm, native, feedback, intent, **kwargs)
          negative_checked[0] = True
        return accepted
      local.setattr(card_module, 'ford_awaiting_initial_request', checked_pending)

  def events(messages, now):
    if scenario != 'startup' or delivery in ('paired', 'native-timeout'):
      return messages
    name = 'aolAxisState' if delivery == 'axis-delayed' else 'selfdriveState'
    result = []
    for message in messages:
      if message.which() == name:
        previous = delayed.get(name)
        delayed[name] = message
        if previous is not None:
          result.append(previous)
      else:
        result.append(message)
    return result

  def run(step, host, sd, joined):
    if scenario == 'startup':
      invalid[0] = True
      for _ in range(45):
        state, axis, sd_state, command = step(engaged=True, fault='gas', steering=True)
        assert not command.latActive and not command.longActive
      invalid[0] = False
      ever_lateral = False
      for _ in range(100):
        state, axis, sd_state, command = step(engaged=True, fault='gas', steering=True)
        ever_lateral |= command.latActive
      if delivery == 'native-timeout':
        assert not host.aol_card_intent.allowed_latch
        assert host.aol_card_intent.latch_clear_reason == 'native_rejection'
        expiries = [call for call in calls if call['result'][2]]
        assert expiries and 200_000_000 <= expiries[0]['now'] - expiries[0]['pending'] < 220_000_000
        assert not ever_lateral
      else:
        assert host.aol_card_intent.allowed_latch, ('Readiness wait erased intent', host.aol_card_intent.latch_clear_reason)
        assert command.latActive
        for _ in range(8):
          state, axis, sd_state, command = step(main_off=True)
        assert not host.aol_card_intent.allowed_latch
        assert not command.latActive and not command.longActive
      assert negative_checked[0]
    else:
      for _ in range(50):
        step(engaged=True)
      assert host.aol_card_intent.allowed_latch
      for _ in range(35):
        state, axis, sd_state, command = step(brake=True)
        assert host.aol_card_intent.allowed_latch, ('Brake cleared intent', host.aol_card_intent.latch_clear_reason)
        assert command.latActive
        if state.brakePressed or not joined.last_permission & 2:
          assert not command.longActive

  monkeypatch.ford_probe_setup = setup
  monkeypatch.ford_probe_events = events
  monkeypatch.ford_probe_run = run
  run_actual_producer(ford_joined, monkeypatch, scenario='route22')
