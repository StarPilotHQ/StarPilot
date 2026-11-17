"""Bounded withdrawal observations with declared, public GM fixtures."""

from unittest.mock import patch
import json
import os
import pytest
from openpilot.cereal import messaging, custom
from opendbc.car.gm.tests.test_ascm_intercept import params as ascm_params
from opendbc.car.gm.values import CAR, GMFlags
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls import controlsd
from openpilot.starpilot.aol.runtime import current_axis, current_native
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed

BASE = 10_000_000_000


def declared_inputs():
  def state(ns):
    return {
      'k': 'carState',
      'ns': ns,
      'valid': True,
      'v': {
        'canValid': True,
        'canTimeout': False,
        'vEgo': 17.63,
        'vEgoRaw': 17.63,
        'standstill': False,
        'gearShifter': 'low',
        'gasPressed': True,
        'cruiseState': {'available': True, 'enabled': False},
      },
    }

  def axis(ns, sequence, source):
    return {
      'k': 'aolAxisState',
      'ns': ns,
      'valid': True,
      'v': {
        'sessionId': 'diagnostic-session',
        'sequence': sequence,
        'sourceCarStateMonoTime': source,
        'observedMonoTime': ns,
        'validUntilMonoTime': ns + 30_000_000,
        'mode': 'lateralOnly',
        'lateralActive': True,
        'longitudinalActive': False,
        'desiredLateral': True,
        'desiredLongitudinal': False,
        'qualified': True,
        'nativeAcknowledged': True,
      },
    }

  observed = BASE - 30_000_000
  native = {
    'k': 'aolSafetyWire',
    'ns': observed,
    'valid': True,
    'v': {
      'kind': 1,
      'version': 2,
      'protocolVersion': 1,
      'compatible': True,
      'observedMonoTime': observed,
      'validUntilMonoTime': observed + 200_000_000,
      'safetyModel': 4,
      'safetyParam': 0x605,
      'lateralAllowed': True,
      'longitudinalAllowed': False,
      'requestedLateral': True,
      'requestedLongitudinal': False,
      'pandaSerial': 'diagnostic-panda',
      'axisSessionId': 'diagnostic-session',
      'sourcePandaStatesMonoTime': observed,
      'pandaInventory': [
        {
          'slotIndex': 0,
          'hardwareSerial': 'diagnostic-panda',
          'safetyModel': 4,
          'safetyParam': 0x605,
          'alternativeExperience': 32,
          'controlsAllowed': False,
          'safetyRxChecksInvalid': False,
          'heartbeatLost': False,
          'faults': 0,
        }
      ],
    },
  }
  return {
    'cached': [state(BASE - 5_000_000), axis(BASE - 20_000_000, 1, BASE - 25_000_000), native],
    'pending': [state(BASE + 15_000_000), axis(BASE + 10_000_000, 2, BASE + 5_000_000), native],
    'controlledStartNs': BASE,
    'controlledGuardNs': BASE + 16_000_000,
  }


DATA = declared_inputs()


def event(row):
  k = row['k']
  m = messaging.new_message(k, 0 if k == 'aolSafetyWire' else None, valid=row['valid'], logMonoTime=row['ns'])
  if k == 'aolSafetyWire':
    m.aolSafetyWire = custom.AolAxisState.SafetyWire.new_message(**row['v']).to_bytes()
  else:
    setattr(m, k, row['v'])
  return m.as_reader()


@pytest.mark.parametrize(
  'schedule',
  [
    'no_delay',
    'cross_before_guard',
    'expired_at_entry',
    'cross_no_pending',
    'cross_withdrawn_pending',
    'cross_eps_fault_pending',
    'expired_withdrawn',
    'expired_eps',
    'expired_empty',
    'expired_invalid_native',
    'expired_context',
  ],
)
def test_declared_proofs_with_controlled_schedule(schedule):
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}):
    params = Params()
    params.put_bool('OpenpilotEnabledToggle', True, block=True)
    params.put_bool('AlwaysOnLateral', True, block=True)
    cp = ascm_params(CAR.CHEVROLET_VOLT_ASCM, sascm=False, accelerator=False, alpha=False)
    cp.flags |= int(GMFlags.HAS_BSM)
    cp.alternativeExperience = 32
    params.put('CarParams', cp.to_bytes(), block=True)
    controls = controlsd.Controls()
    assert controls.aol_replay
    assert controls.CI.CS.steering_authority.enabled, 'Stock-ASCM refusal protection must stay enabled'
    start = DATA['controlledStartNs']
    guard = DATA['controlledGuardNs']
    now = [start]
    cached = [event(r) for r in DATA['cached']]
    pending = [event(r).as_builder() for r in DATA['pending']]
    if schedule in ('cross_no_pending', 'expired_empty'):
      pending = []
    if schedule in ('cross_withdrawn_pending', 'expired_withdrawn'):
      next(r for r in pending if r.which() == 'aolAxisState').aolAxisState.lateralActive = False
    if schedule == 'expired_invalid_native':
      next(r for r in pending if r.which() == 'aolSafetyWire').valid = False
    if schedule == 'expired_context':
      axis = next(r for r in pending if r.which() == 'aolAxisState').aolAxisState
      axis.lateralActive = axis.desiredLateral = False
      axis.mode = 'off'
      axis.faultReason = 'availabilityPause'
      axis.faultEventMonoTime = guard - 1
      physical = next(r for r in pending if r.which() == 'carState').carState
      physical.gearShifter = 'reverse'
      physical.cruiseState.available = physical.cruiseState.enabled = False
    if schedule in ('cross_eps_fault_pending', 'expired_eps'):
      next(r for r in pending if r.which() == 'carState').carState.steerFaultTemporary = True
    with patch.object(controls.sm, 'update_msgs') as capture:
      feed(controls, start, 1, active=False, enabled=False)
    frames = capture.call_args.args[1]
    replaced = {r.which() for r in cached}
    controls.sm.update_msgs(start / 1e9, [r for r in frames if r.which() not in replaced] + cached)
    # Actual last-known axis/native proof: active, no physical veto, no ordinary LONG.
    with patch.object(controlsd.time, 'monotonic_ns', return_value=start):
      healthy, _ = controls.state_control()
    assert healthy.latActive and not healthy.longActive
    cached_deadline = int(controls.sm['aolAxisState'].validUntilMonoTime)
    assert start < cached_deadline < guard
    updates = []
    axis_checks = []
    native_checks = []

    def receive(timeout):
      assert timeout == 0
      updates.append(now[0])
      controls.sm.update_msgs(now[0] / 1e9, [r.as_reader() for r in pending])

    original = controls.VM.update_params

    def delayed_vm(*args, **kwargs):
      result = original(*args, **kwargs)
      if schedule != 'no_delay':
        now[0] = guard
      return result

    def observe_axis(sm, *, now_ns):
      answer = current_axis(sm, now_ns=now_ns)
      axis_checks.append(
        {'now': now_ns, 'stamp': int(sm.logMonoTime['aolAxisState']), 'deadline': int(sm['aolAxisState'].validUntilMonoTime), 'accepted': answer is not None}
      )
      return answer

    def observe_native(sm, CP, **kwargs):
      answer = current_native(sm, CP, **kwargs)
      native_checks.append(answer is not None)
      return answer

    if schedule.startswith('expired'):
      now[0] = guard
    with (
      patch.object(controls.sm, 'update', side_effect=receive),
      patch.object(controlsd.time, 'monotonic_ns', side_effect=lambda: now[0]),
      patch.object(controls.VM, 'update_params', side_effect=delayed_vm),
      patch.object(controlsd, 'current_axis', side_effect=observe_axis),
      patch.object(controlsd, 'current_native', side_effect=observe_native),
    ):
      command, _ = controls.state_control()
    expected = schedule in ('no_delay', 'expired_at_entry', 'cross_before_guard')
    assert bool(command.latActive) == expected and not command.longActive
    assert native_checks == [schedule != 'expired_invalid_native']
    expected_updates = 2 if schedule in ('cross_no_pending', 'expired_empty') else int(schedule != 'no_delay')
    assert updates == [guard] * expected_updates
    assert axis_checks[0]['accepted'] == (schedule not in ('cross_no_pending', 'expired_empty'))
    diagnostic = controls.aol_transition_diagnostic
    assert (diagnostic.pending is None) == expected
    if not expected:
      assert isinstance(diagnostic.pending, tuple)
    order = []
    headers = []
    records = []

    class Publisher:
      def send(self, name, message):
        order.append(name)
        if name == 'carControl':
          headers.append(int(message.logMonoTime))

    with (
      patch.object(controls, 'pm', Publisher()),
      patch.object(controlsd.cloudlog, 'event', side_effect=lambda *a, **kw: (order.append('diagnostic'), records.append(kw['diagnostic']))),
      patch.object(controlsd.time, 'monotonic_ns', return_value=now[0]),
    ):
      controls.publish(command, _)
    record = records[0] if records else None
    if expected:
      assert record is None
    else:
      assert record is not None
      assert record['entry']['headerNs'] == DATA['cached'][1]['ns']
      assert record['entry']['session'] == DATA['cached'][1]['v']['sessionId']
      assert record['entry']['sequence'] == DATA['cached'][1]['v']['sequence']
      assert record['entry']['sourceNs'] == DATA['cached'][1]['v']['sourceCarStateMonoTime']
      assert record['entry']['deadlineNs'] == cached_deadline
      assert record['native']['accepted'] == (schedule != 'expired_invalid_native')
      assert record['native']['allowed']
      if schedule == 'expired_invalid_native':
        assert not record['native']['valid'] and record['native']['decoded']
      if schedule == 'expired_invalid_native':
        assert record['reason'] == 'native_rejected'
      elif schedule in ('expired_withdrawn', 'expired_context', 'cross_withdrawn_pending'):
        assert record['reason'] == 'axis_withdrawn'
      elif schedule in ('expired_eps', 'cross_eps_fault_pending'):
        assert record['reason'] == 'steer_temporary_fault'
      else:
        assert record['reason'] == 'axis_header_expired'
      assert record['refreshAttempted'] == (schedule != 'no_delay')
      assert record['entry']['atNs'] < cached_deadline if not schedule.startswith('expired') else record['entry']['atNs'] > cached_deadline
      assert record['gate']['atNs'] > cached_deadline
      assert record['entry']['mode'] == 'lateralOnly'
      assert record['physical']['gear'] == ('reverse' if schedule == 'expired_context' else 'low')
      assert record['physical']['mainAvailable'] == (schedule != 'expired_context')
      assert not record['physical']['cruiseEnabled']
      if schedule == 'expired_context':
        assert record['gate']['mode'] == 'off'
        assert record['gate']['faultReason'] == 'availabilityPause'
        assert record['gate']['faultEventNs'] == guard - 1
    assert order[:2] == ['controlsState', 'carControl']
    assert ('diagnostic' in order) == (not expected)
    if not expected:
      assert record is not None
      assert order.index('diagnostic') > order.index('carControl')
      assert record['controlHeaderNs'] == headers[0]
      for _ignored in range(20):
        with patch.object(controlsd.time, 'monotonic_ns', return_value=now[0]):
          repeated, _ = controls.state_control()
        assert not repeated.latActive and diagnostic.pending is None
      assert diagnostic.emitted == 1
    assert diagnostic.pending is None


def test_diagnostic_rate_lifetime_payload_and_failure_bounds():
  from types import SimpleNamespace
  from openpilot.starpilot.aol.transition_diagnostic import TransitionDiagnostic, MAX_EVENTS, MAX_EVENT_BYTES, MIN_EVENT_INTERVAL_NS
  from openpilot.starpilot.aol.wire import decode_safety

  axis = event(DATA['cached'][1]).aolAxisState
  snap = (DATA['controlledStartNs'], DATA['cached'][1]['ns'], axis, True, True, True, DATA['cached'][0]['ns'])
  native = decode_safety(event(DATA['cached'][2]).aolSafetyWire)
  cs = event(DATA['cached'][0]).carState
  diag = TransitionDiagnostic()
  events = []
  now = DATA['controlledGuardNs']

  def finish(stamp, owner=diag):
    owner.finish(
      snap,
      snap,
      now_ns=stamp,
      refreshed=False,
      native=native,
      native_header_ns=DATA['cached'][2]['ns'],
      native_flags=(True, True, True),
      native_raw=b'',
      reason='axis_header_expired',
      cs=cs,
      enabled=False,
      long_active=False,
    )

  finish(now)
  diag.emit(lambda name, **kw: events.append(kw['diagnostic']))
  for tick in range(1, 1000):
    finish(now + tick)
    diag.emit(lambda name, **kw: events.append(kw['diagnostic']))
  assert len(events) == 1 and diag.suppressed == 999
  finish(now + MIN_EVENT_INTERVAL_NS)
  diag.emit(lambda name, **kw: events.append(kw['diagnostic']))
  assert events[-1]['suppressed'] == 999
  for tick in range(2, MAX_EVENTS + 20):
    finish(now + tick * MIN_EVENT_INTERVAL_NS)
    diag.emit(lambda name, **kw: events.append(kw['diagnostic']))
  assert len(events) == MAX_EVENTS and diag.emitted == MAX_EVENTS and diag.pending is None
  assert all(len(json.dumps(r, separators=(',', ':')).encode()) <= MAX_EVENT_BYTES for r in events)
  assert diag.capture(None, now) is None
  broken = TransitionDiagnostic()
  finish(now, broken)

  def failed_sink(*args, **kwargs):
    raise RuntimeError('sink unavailable')

  with patch.object(broken, '_format', return_value=events[0]):
    broken.emit(failed_sink)
  assert broken.disabled and broken.pending is None
  overflow = TransitionDiagnostic()
  finish(now, overflow)
  with patch.object(overflow, '_format', return_value={'bad': 'x' * (MAX_EVENT_BYTES + 1)}):
    overflow.emit(failed_sink)
  assert overflow.disabled and overflow.pending is None
  unavailable = TransitionDiagnostic()
  assert unavailable.capture(SimpleNamespace(), now) is None and unavailable.disabled


def initialize_controls():
  params = Params()
  params.put_bool('OpenpilotEnabledToggle', True, block=True)
  params.put_bool('AlwaysOnLateral', True, block=True)
  cp = ascm_params(CAR.CHEVROLET_VOLT_ASCM, sascm=False, accelerator=False, alpha=False)
  cp.flags |= int(GMFlags.HAS_BSM)
  cp.alternativeExperience = 32
  params.put('CarParams', cp.to_bytes(), block=True)
  owner = controlsd.Controls()
  stamp = DATA['controlledStartNs']
  with patch.object(owner.sm, 'update_msgs') as capture:
    feed(owner, stamp, 1, active=False, enabled=False)
  cached = [event(r) for r in DATA['cached']]
  replaced = {r.which() for r in cached}
  owner.sm.update_msgs(stamp / 1e9, [r for r in capture.call_args.args[1] if r.which() not in replaced] + cached)
  return owner


@pytest.mark.parametrize('failure', ['format', 'decode', 'json', 'sink'])
def test_diagnostic_failure_occurs_after_outputs_and_cannot_change_command(failure):
  from types import SimpleNamespace
  from openpilot.starpilot.aol import transition_diagnostic as observer_module, wire

  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}):
    owner = initialize_controls()
    now = DATA['controlledStartNs']
    with patch.object(controlsd.time, 'monotonic_ns', return_value=now) as clock:
      command, _ = owner.state_control()
      assert command.latActive
      changed = event(DATA['cached'][0]).as_builder()
      changed.carState.steerFaultTemporary = True
      changed.carState.gearShifter = 'reverse'
      changed.carState.cruiseState.available = False
      changed.carState.cruiseState.enabled = False
      axis = event(DATA['cached'][1]).as_builder()
      axis.aolAxisState.faultReason = 'availabilityPause'
      axis.aolAxisState.faultEventMonoTime = now - 1
      inputs = [changed.as_reader(), axis.as_reader()]
      if failure == 'decode':
        rejected = event(DATA['cached'][2]).as_builder()
        rejected.valid = False
        inputs.append(rejected.as_reader())
      now += 1_000_000
      clock.return_value = now
      owner.sm.update_msgs(now / 1e9, inputs)
      command, state = owner.state_control()
    assert not command.latActive and isinstance(owner.aol_transition_diagnostic.pending, tuple)
    original = command.to_dict()
    order, sent = [], {}

    class Publisher:
      def send(self, name, message):
        order.append(name)
        sent[name] = message.as_reader()

    def broken(*args, **kwargs):
      assert order[:3] == ['controlsState', 'carControl', 'starpilotLateralState']
      raise RuntimeError('diagnostic ' + failure)

    # publish normally adds HUD/cruise fields. Compare against that real path,
    # with the optional observer disabled, rather than a pre-publication body.
    baseline_command = command.as_reader().as_builder()
    with patch.object(owner, 'aol_transition_diagnostic', None), patch.object(owner.pm, 'send'):
      owner.publish(baseline_command, state)
    target = (
      patch.object(owner.aol_transition_diagnostic, '_format', side_effect=broken)
      if failure == 'format'
      else patch.object(wire, 'decode_safety', side_effect=broken)
      if failure == 'decode'
      else patch.object(observer_module, 'json', SimpleNamespace(dumps=broken))
      if failure == 'json'
      else patch.object(controlsd.cloudlog, 'event', side_effect=broken)
    )
    with patch.object(owner, 'pm', Publisher()), target:
      owner.publish(command, state)
    assert sent['carControl'].carControl.to_dict() == baseline_command.to_dict() == command.to_dict()
    assert command.actuators.to_dict() == original['actuators']
    assert (command.enabled, command.latActive, command.longActive) == (original['enabled'], original['latActive'], original['longActive'])
    assert owner.aol_transition_diagnostic.disabled and owner.aol_transition_diagnostic.pending is None


def test_finish_retains_references_without_formatting_decoding_or_reading_fields():
  from openpilot.starpilot.aol.transition_diagnostic import TransitionDiagnostic

  class Unreadable:
    def __getattr__(self, name):
      raise AssertionError('field read before output: ' + name)

  reader = Unreadable()
  diag = TransitionDiagnostic()
  snapshot = (1, 1, reader, True, True, True, 1)
  with patch.object(diag, 'axis_record', side_effect=AssertionError('format before output')):
    diag.finish(
      snapshot,
      snapshot,
      now_ns=2,
      refreshed=False,
      native=reader,
      native_header_ns=1,
      native_flags=(True, True, True),
      native_raw=b'',
      reason='lateral_gate',
      cs=reader,
      enabled=False,
      long_active=False,
      axis=reader,
    )
  assert isinstance(diag.pending, tuple) and not diag.disabled


@pytest.mark.parametrize('remove_observer', [False, True])
def test_optional_observer_access_and_commands_match_disabled_baseline(remove_observer):
  outputs = []
  for enabled in (False, True):
    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}):
      owner = initialize_controls()
      if not enabled:
        if remove_observer:
          del owner.aol_transition_diagnostic
        else:
          owner.aol_transition_diagnostic = None
      now = DATA['controlledStartNs']
      with patch.object(controlsd.time, 'monotonic_ns', return_value=now), patch.object(owner.pm, 'send'):
        command, state = owner.state_control()
        owner.publish(command, state)
      outputs.append((command.to_dict(), state.to_dict()))
  assert outputs[0] == outputs[1]
