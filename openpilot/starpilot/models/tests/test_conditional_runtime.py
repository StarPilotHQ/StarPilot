"""Protocol and owner tests; these do not qualify device memory or latency."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.models.conditional_protocol import (
  ACK_TIMEOUT_NS, SETTLE_FRAMES, SWITCH_TIMEOUT_NS, Ack, EngagementHold, ResidentSwitch, State,
)
from openpilot.starpilot.models.conditional_runtime import modeld_idle

BASE = 10_000_000_000


def switcher():
  models = {name: (NS(model_id=name), name + '-receipt', Mock()) for name in ('highway', 'city')}
  owner = ResidentSwitch(models, 'highway', now=BASE)
  request = {'session': owner.session, 'id': 'a' * 32, 'model': 'city'}
  return owner, request


def begin(owner, request):
  assert not owner.before_frame(request, now=BASE, idle=True, ack=None)
  assert owner.phase == 'waiting'
  owner.announced = BASE
  ack = Ack(owner.session, request['id'], True, BASE, BASE + 10_000_000)
  assert owner.before_frame(request, now=BASE + 20_000_000, idle=True, ack=ack)
  assert owner.active == 'city'


def test_resident_swap_changes_only_after_matching_fresh_hold_and_keeps_separate_state():
  owner, request = switcher()
  begin(owner, request)
  owner.models['city'][2].assert_called_once()
  owner.models['highway'][2].assert_not_called()
  for i in range(SETTLE_FRAMES):
    owner.published(BASE + (i + 1) * 50_000_000, BASE + (i + 1) * 50_000_000)
    assert owner.phase == ('ready' if i == SETTLE_FRAMES - 1 else 'switching')
  assert owner.elapsed == .25
  assert not owner.before_frame(request, now=BASE + 300_000_000, idle=True, ack=None)
  # Returning to Highway resets its own original buffers, never City's state.
  request = request | {'id': 'b' * 32, 'model': 'highway'}
  assert not owner.before_frame(request, now=BASE + 300_000_000, idle=True, ack=None)
  owner.announced = BASE + 300_000_000
  assert owner.before_frame(request, now=BASE + 320_000_000, idle=True,
                            ack=Ack(owner.session, request['id'], True, owner.announced, BASE + 310_000_000))
  owner.models['highway'][2].assert_called_once()


@pytest.mark.parametrize('change', [{'session': 'old'}, {'request': 'old'}, {'holding': False},
                                  {'source': BASE - 1}, {'stamp': BASE - 200_000_000}, {'stamp': BASE + 999_000_000}])
def test_bad_ack_never_changes_runner(change):
  owner, request = switcher()
  owner.before_frame(request, now=BASE, idle=True, ack=None)
  owner.announced = BASE
  values = {'session': owner.session, 'request': request['id'], 'holding': True, 'source': BASE, 'stamp': BASE + 10_000_000}
  assert not owner.before_frame(request, now=BASE + 20_000_000, idle=True, ack=Ack(**(values | change)))
  assert owner.active == 'highway'
  owner.models['city'][2].assert_not_called()


def test_engaged_request_denied_and_engagement_race_cancels_before_reset():
  owner, request = switcher()
  assert not owner.before_frame(request, now=BASE, idle=False, ack=None)
  assert owner.phase == 'ready' and owner.error
  request['id'] = 'b' * 32
  owner.before_frame(request, now=BASE, idle=True, ack=None)
  owner.announced = BASE
  assert not owner.before_frame(request, now=BASE + 10_000_000, idle=False,
                                ack=Ack(owner.session, request['id'], True, BASE, BASE + 5_000_000))
  assert owner.active == 'highway' and not owner.error
  owner.models['city'][2].assert_not_called()


def test_ack_timeout_consumes_request_token_and_retains_current_model():
  owner, request = switcher()
  owner.before_frame(request, now=BASE, idle=True, ack=None)
  owner.before_frame(request, now=BASE + ACK_TIMEOUT_NS, idle=True, ack=None)
  assert owner.active == 'highway' and owner.phase == 'ready' and 'timed out' in owner.error
  owner.before_frame(request, now=BASE + ACK_TIMEOUT_NS + 1, idle=True, ack=None)
  assert owner.phase == 'ready'


def test_reset_failure_and_inference_failure_restore_previous_without_reloading():
  owner, request = switcher()
  owner.models['city'][2].side_effect = RuntimeError('mapping unavailable')
  owner.before_frame(request, now=BASE, idle=True, ack=None)
  owner.announced = BASE
  assert not owner.before_frame(request, now=BASE + 20_000_000, idle=True,
                                ack=Ack(owner.session, request['id'], True, BASE, BASE + 10_000_000))
  assert owner.active == 'highway' and 'reset failed' in owner.error
  owner, request = switcher()
  begin(owner, request)
  owner.abort('Candidate inference failed', BASE + 40_000_000)
  assert owner.active == 'highway' and owner.phase == 'recovering'
  owner.models['highway'][2].assert_called_once()
  for i in range(SETTLE_FRAMES):
    owner.published(BASE + (i + 1) * 50_000_000, BASE + (i + 1) * 50_000_000)
  assert owner.phase == 'ready' and owner.error == 'Candidate inference failed'


def test_handoff_timeout_rolls_back_but_unknown_engagement_keeps_hold():
  owner, request = switcher()
  begin(owner, request)
  assert not owner.before_frame(request, now=BASE + SWITCH_TIMEOUT_NS, idle=False, ack=None)
  for i in range(SETTLE_FRAMES + 1):
    owner.published(BASE + SWITCH_TIMEOUT_NS + i, BASE + SWITCH_TIMEOUT_NS + i)
  assert owner.phase == 'switching' and owner.active == 'city'
  assert owner.before_frame(request, now=BASE + SWITCH_TIMEOUT_NS + 100, idle=True, ack=None)
  assert owner.active == 'highway' and owner.phase == 'recovering'


def test_wrong_session_and_nonresident_model_are_never_loaded():
  owner, request = switcher()
  assert not owner.before_frame(request | {'session': 'prior-drive'}, now=BASE, idle=True, ack=None)
  assert owner.request == ''
  assert not owner.before_frame(request | {'model': 'unloaded'}, now=BASE, idle=True, ack=None)
  assert owner.active == 'highway' and owner.error


def test_engagement_hold_blocks_reengagement_until_post_handoff_output():
  hold = EngagementHold()
  waiting = State('session', 'request', 'waiting', BASE - 1, BASE - 100)
  assert hold.step(waiting, BASE, BASE, idle=True, model_stamp=BASE - 1, model_valid=True)
  assert hold.step(None, 0, BASE + 9_000_000_000, idle=True, model_stamp=0, model_valid=False)
  ready = State('session', 'request', 'ready', BASE + 50_000_000, BASE - 100)
  assert hold.step(ready, BASE + 60_000_000, BASE + 60_000_000, idle=True, model_stamp=BASE, model_valid=True)
  assert not hold.step(ready, BASE + 70_000_000, BASE + 70_000_000, idle=True, model_stamp=BASE + 50_000_000, model_valid=True)


def test_hold_denies_engaged_and_stale_states_and_requires_new_publisher_after_restart():
  hold = EngagementHold()
  waiting = State('session', 'request', 'waiting', 0, BASE)
  assert not hold.step(waiting, BASE, BASE, idle=False, model_stamp=BASE, model_valid=True)
  assert not hold.step(waiting, BASE, BASE + 200_000_000, idle=True, model_stamp=BASE, model_valid=True)
  assert hold.step(waiting, BASE + 210_000_000, BASE + 210_000_000, idle=True, model_stamp=BASE, model_valid=True)
  restarted = State('new-process', '', 'disabled', BASE + 300_000_000, BASE + 250_000_000)
  assert hold.step(restarted, BASE + 310_000_000, BASE + 310_000_000, idle=True, model_stamp=BASE + 220_000_000, model_valid=True)
  assert not hold.step(restarted, BASE + 320_000_000, BASE + 320_000_000, idle=True,
                       model_stamp=BASE + 300_000_000, model_valid=True)


class Feed(dict):
  def __init__(self):
    super().__init__(selfdriveState=NS(enabled=False, active=False, state='disabled'),
                     carControl=NS(enabled=False, latActive=False, longActive=False),
                     carState=NS(cruiseState=NS(enabled=False), canValid=True, canTimeout=False, vEgo=0.))
    self.seen = self.valid = self.alive = dict.fromkeys(self, True)
    self.logMonoTime = dict.fromkeys(self, BASE)


def test_disengagement_evidence_must_be_fresh_and_include_both_axes_and_stock_cruise():
  sm = Feed()
  assert modeld_idle(sm, BASE)
  for service, field in (('carControl', 'latActive'), ('carControl', 'longActive'), ('selfdriveState', 'enabled')):
    setattr(sm[service], field, True)
    assert not modeld_idle(sm, BASE)
    setattr(sm[service], field, False)
  sm['carState'].cruiseState.enabled = True
  assert not modeld_idle(sm, BASE)
  sm['carState'].cruiseState.enabled = False
  assert not modeld_idle(sm, BASE + 200_000_000)
  sm['selfdriveState'].state = 'preEnabled'
  assert not modeld_idle(sm, BASE)


def test_live_handshake_over_real_typed_ipc_has_valid_state_and_ack():
  import os
  import time
  import uuid
  from openpilot.cereal import messaging
  from openpilot.selfdrive.selfdrived.state import StateMachine
  from openpilot.starpilot.models.conditional_runtime import ConditionalModelRuntime, ConditionalModelGate

  messaging.set_fake_prefix('conditional_runtime_' + uuid.uuid4().hex)
  gate = ConditionalModelGate()
  publisher = messaging.PubMaster(['conditionalModelState'])
  acknowledgments = messaging.SubMaster(['conditionalModelAck'])
  now = time.monotonic_ns()
  modes = ConditionalModelRuntime.__new__(ConditionalModelRuntime)
  modes.owner = ResidentSwitch({'highway': ()}, 'highway', now=now)
  modes.owner.phase, modes.owner.request = 'waiting', uuid.uuid4().hex
  modes.pid, modes.ticks = os.getpid(), 1
  modes.config = {'highway': 'highway', 'city': 'city', 'thresholdMph': 50}
  from openpilot.starpilot.models.conditional_policy import SpeedPolicy
  modes.paused, modes.policy, modes.unavailable_reason = False, SpeedPolicy(), ''
  modes.ack = NS(seen={'conditionalModelAck': False})
  sm = Feed()
  sm.logMonoTime = dict.fromkeys(sm, now)
  drive = NS(initialized=True, conditional_car_state_valid=True, enabled=False, active=False,
             state_machine=StateMachine(), aol_replay=False, aol_axis_decision=None,
             sm=NS(valid={'modelV2': True}, alive={'modelV2': True}, logMonoTime={'modelV2': now}), events=None)
  car = NS(canValid=True, canTimeout=False, cruiseState=NS(enabled=False))
  time.sleep(.05)
  # A subscriber started before its publisher may need another frame to attach
  # to the publisher generation. Production publishes continuously at 20 Hz.
  for _ in range(3):
    modes.publish(publisher, sm, 0)
    gate.sm.update(20)
    if gate.sm.seen['conditionalModelState']:
      break
  assert gate.sm.seen['conditionalModelState'] and gate.sm.valid['conditionalModelState']
  assert gate.step(drive, car)
  acknowledgments.update(100)
  assert acknowledgments.valid['conditionalModelAck']
  ack = acknowledgments['conditionalModelAck']
  assert ack.holding and ack.idle and ack.session == modes.owner.session
  assert ack.requestId == modes.owner.request and ack.stateMonoTime >= modes.owner.announced
  assert gate.alert().alert_text_1 == 'Switching conditional models'
  with patch('openpilot.starpilot.models.conditional_runtime.time.monotonic_ns', return_value=gate.hold.held_since + SWITCH_TIMEOUT_NS + 1):
    assert gate.alert().alert_text_1 == 'Conditional model switch stalled'
  # Only a fresh ready boundary and output from this publisher can release it.
  modes.owner.phase = 'ready'
  frame = time.monotonic_ns()
  drive.sm.logMonoTime['modelV2'] = frame
  modes.publish(publisher, sm, frame)
  gate.sm.update(100)
  assert not gate.step(drive, car)
  assert gate.alert() is None


def test_real_selfdrive_hold_ack_blocks_enable_and_both_axes_until_new_output():
  import ast
  from pathlib import Path
  from openpilot.cereal import messaging
  from openpilot.selfdrive.selfdrived.events import Events, EventName
  from openpilot.selfdrive.selfdrived.state import StateMachine
  from openpilot.starpilot.aol.runtime import AxisDecision
  from openpilot.starpilot.models.conditional_runtime import ConditionalModelGate

  message = messaging.new_message('conditionalModelState')
  message.conditionalModelState = {'session': 'a' * 32, 'requestId': 'b' * 32, 'phase': 'waiting', 'sessionStartMonoTime': BASE - 100}
  class Subscriber:
    seen = valid = alive = {'conditionalModelState': True}
    logMonoTime = {'conditionalModelState': BASE}
    def update(self, _):
      pass
    def __getitem__(self, _):
      return message.conditionalModelState
  gate = ConditionalModelGate.__new__(ConditionalModelGate)
  gate.sm, gate.pm, gate.hold = Subscriber(), Mock(), EngagementHold()
  sm = NS(valid={'modelV2': True}, alive={'modelV2': True}, logMonoTime={'modelV2': BASE - 1})
  car = NS(canValid=True, canTimeout=False, cruiseState=NS(enabled=False))
  drive = NS(initialized=True, conditional_car_state_valid=True, enabled=False, active=False,
             state_machine=StateMachine(), aol_replay=False, aol_axis_decision=AxisDecision(),
             sm=sm, conditional_model_gate=gate, events=Events(), CP=NS(passive=False))
  # Execute the production step's hold admission, state-machine update, and
  # final axis override. An ENABLE in the exact acknowledgment tick must lose.
  source = Path(__file__).resolve().parents[3] / 'selfdrive/selfdrived/selfdrived.py'
  tree = ast.parse(source.read_text())
  step = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'step')
  start = next(i for i, n in enumerate(step.body) if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == 'conditional_gate')
  admission = step.body[start:start + 3]
  update = next(n for n in step.body if isinstance(n, ast.If) and ast.unparse(n.test) == 'not self.CP.passive and self.initialized')
  override = [n for n in step.body if isinstance(n, ast.If) and ast.unparse(n.test) == 'conditional_hold'][-1]
  env = {'self': drive, 'CS': car, 'EventName': EventName, 'AxisDecision': AxisDecision}
  drive.events.add(EventName.buttonEnable)
  with patch('openpilot.starpilot.models.conditional_runtime.time.monotonic_ns', return_value=BASE + 10):
    exec(compile(ast.fix_missing_locations(ast.Module(body=[*admission, update], type_ignores=[])), str(source), 'exec'), env)
  assert not drive.enabled and not drive.active
  ack = gate.pm.send.call_args.args[1].conditionalModelAck
  assert ack.holding and ack.session == 'a' * 32 and ack.requestId == 'b' * 32
  drive.aol_axis_decision = AxisDecision(desired_lateral=True, lateral_active=True, desired_longitudinal=True, longitudinal_active=True)
  exec(compile(ast.fix_missing_locations(ast.Module(body=[override], type_ignores=[])), str(source), 'exec'), env)
  assert drive.aol_axis_decision == AxisDecision()
  message.conditionalModelState.phase = 'ready'
  message.conditionalModelState.frameMonoTime = BASE + 20
  gate.sm.logMonoTime['conditionalModelState'] = BASE + 30
  with patch('openpilot.starpilot.models.conditional_runtime.time.monotonic_ns', return_value=BASE + 40):
    assert gate.step(drive, car)  # previous model output cannot clear the hold
    sm.logMonoTime['modelV2'] = BASE + 20
    assert gate.step(drive, car)  # do not carry an Enable attempt through the hold
    drive.events.clear()
    assert not gate.step(drive, car)


@pytest.mark.parametrize('armed,active', [(True, False), (False, True)])
def test_independent_lateral_intent_or_axis_refuses_hold(armed, active):
  from openpilot.cereal import log, messaging
  from openpilot.starpilot.models.conditional_runtime import ConditionalModelGate
  message = messaging.new_message('conditionalModelState')
  message.conditionalModelState = {'session': 'a' * 32, 'requestId': 'b' * 32, 'phase': 'waiting', 'sessionStartMonoTime': BASE - 100}
  class Subscriber:
    seen = valid = alive = {'conditionalModelState': True}
    logMonoTime = {'conditionalModelState': BASE}
    def update(self, _):
      pass
    def __getitem__(self, _):
      return message.conditionalModelState
  gate = ConditionalModelGate.__new__(ConditionalModelGate)
  gate.sm, gate.pm, gate.hold = Subscriber(), Mock(), EngagementHold()
  drive = NS(initialized=True, conditional_car_state_valid=True, enabled=False, active=False,
             state_machine=NS(state=log.SelfdriveState.OpenpilotState.disabled), aol_replay=True, aol_car_state_log_ns=BASE,
             aol_axis_decision=NS(lateral_active=active),
             sm=NS(valid={'modelV2': True}, alive={'modelV2': True}, logMonoTime={'modelV2': BASE}))
  with patch('openpilot.starpilot.models.conditional_runtime.time.monotonic_ns', return_value=BASE + 10), \
       patch('openpilot.starpilot.aol.runtime.current_intent', return_value=NS(allowedLatch=armed, lateralArmed=armed)):
    assert not gate.step(drive, NS(canValid=True, canTimeout=False, cruiseState=NS(enabled=False)))
    assert not gate.pm.send.call_args.args[1].conditionalModelAck.holding


@pytest.mark.parametrize('failure', [None, 'load', 'memory', 'missing', 'mapping'])
def test_startup_preloads_alternate_once_or_retains_initial_runner(tmp_path, failure):
  from openpilot.starpilot.models.conditional_runtime import ConditionalModelRuntime
  first_reset, other_reset = Mock(), Mock()
  first = NS(model_id='cheddar', handoff_reset=Mock(return_value=first_reset))
  other = NS(model_id='gwm8223', handoff_reset=Mock(return_value=other_reset))
  if failure == 'mapping':
    other.handoff_reset.side_effect = ValueError('unmapped history')
  artifact = tmp_path / 'alternate.pkl'
  artifact.write_bytes(b'fixture')
  config = {'enabled': True, 'city': 'cheddar', 'highway': 'gwm8223', 'thresholdMph': 50}
  entries = {'gwm8223': {'version': 'v15', 'artifact_sha256': 'a' * 64}}
  with patch('openpilot.cereal.messaging.SubMaster'), \
       patch('openpilot.starpilot.models.manager.catalog', return_value=entries), \
       patch('openpilot.starpilot.models.manager.verified_artifact', return_value=None if failure == 'missing' else artifact), \
       patch('openpilot.starpilot.models.runner.load_verified_model', return_value=(other, 'other-receipt')) as load, \
       patch('pathlib.Path.read_text', return_value='MemAvailable: 1 kB' if failure == 'memory' else 'MemAvailable: 9000000 kB'):
    if failure == 'load':
      load.side_effect = RuntimeError('native loading failed')
    runtime = ConditionalModelRuntime(first, 'first-receipt', config, root=tmp_path, cam_w=1928, cam_h=1208, enabled=True)
  assert runtime.owner.active == 'cheddar'
  first_reset.assert_not_called()
  other_reset.assert_not_called()
  if failure is None:
    assert runtime.owner.models['cheddar'] == (first, 'first-receipt', first_reset)
    assert runtime.owner.models['gwm8223'] == (other, 'other-receipt', other_reset)
    load.assert_called_once()
  else:
    assert not runtime.owner.models and runtime.owner.error


@pytest.mark.parametrize('configured', [False, True])
def test_expected_external_precedence_or_disabled_feature_is_not_a_driver_fault(tmp_path, configured):
  from openpilot.starpilot.models.conditional_runtime import ConditionalModelRuntime
  from openpilot.starpilot.models.conditional_alerts import conditional_model_alert
  config = {'enabled': configured, 'city': 'cheddar', 'highway': 'gwm8223', 'thresholdMph': 50}
  with patch('openpilot.cereal.messaging.SubMaster'):
    runtime = ConditionalModelRuntime(NS(model_id='cheddar'), None, config,
                                      root=tmp_path, cam_w=1928, cam_h=1208, enabled=False)
  assert runtime.owner.phase == 'disabled' and not runtime.owner.error
  assert bool(runtime.unavailable_reason) == configured
  assert conditional_model_alert(NS(phase=runtime.owner.phase, error=runtime.owner.error), holding=False) is None


def test_stale_or_armed_completion_does_not_release_hold():
  hold = EngagementHold()
  waiting = State('session', 'request', 'waiting', 0, BASE - 100)
  assert hold.step(waiting, BASE, BASE, idle=True, model_stamp=BASE, model_valid=True)
  ready = State('session', 'request', 'ready', BASE + 10, BASE - 100)
  assert hold.step(ready, BASE + 20, BASE + 20, idle=False, model_stamp=BASE + 10, model_valid=True)
  assert hold.step(ready, BASE + 30, BASE + 30, idle=True, model_stamp=BASE + 10, model_valid=True, release_allowed=False)
  assert not hold.step(ready, BASE + 40, BASE + 40, idle=True, model_stamp=BASE + 10, model_valid=True)


def test_fresh_status_cannot_restart_the_hold_duration_clock():
  hold = EngagementHold()
  state = State('session', 'request', 'recovering', 0, BASE - 1)
  for i in range(50):
    now = BASE + i * 50_000_000
    assert hold.step(state, now, now, idle=True, model_stamp=now, model_valid=False)
    assert hold.held_since == BASE
  assert hold.last_stamp > hold.held_since + SWITCH_TIMEOUT_NS


def conditional_runtime_fixture():
  from openpilot.starpilot.models.conditional_policy import SpeedPolicy
  from openpilot.starpilot.models.conditional_runtime import ConditionalModelRuntime
  owner, _ = switcher()
  owner.active = owner.previous = 'city'
  owner.frame = BASE
  modes = ConditionalModelRuntime.__new__(ConditionalModelRuntime)
  modes.owner, modes.policy = owner, SpeedPolicy()
  modes.paused, modes.last_status = False, None
  modes.config = {'highway': 'highway', 'city': 'city', 'thresholdMph': 50}
  class AckFeed(dict):
    def update(self, _):
      pass
  modes.ack = AckFeed(conditionalModelAck=NS(session=owner.session, requestId='', holding=False, stateMonoTime=BASE, idle=True))
  modes.ack.seen = modes.ack.valid = modes.ack.alive = {'conditionalModelAck': True}
  modes.ack.logMonoTime = {'conditionalModelAck': BASE}
  feed = Feed()
  feed['carState'].vEgo = 60 * .44704
  return modes, feed


def test_automatic_highway_request_uses_same_held_handoff_and_separate_buffers():
  modes, feed = conditional_runtime_fixture()
  with patch('openpilot.starpilot.models.conditional_runtime.time.monotonic_ns', return_value=BASE):
    assert modes.before_frame(feed) is None
  assert modes.owner.phase == 'waiting' and modes.owner.active == 'city'
  modes.owner.models['highway'][2].assert_not_called()
  modes.owner.announced = BASE
  ack = modes.ack['conditionalModelAck']
  ack.requestId, ack.holding = modes.owner.request, True
  modes.ack.logMonoTime['conditionalModelAck'] = BASE + 10_000_000
  with patch('openpilot.starpilot.models.conditional_runtime.time.monotonic_ns', return_value=BASE + 20_000_000):
    assert modes.before_frame(feed) == modes.owner.models['highway']
  modes.owner.models['highway'][2].assert_called_once()
  modes.owner.models['city'][2].assert_not_called()
  assert modes.owner.phase == 'switching'


@pytest.mark.parametrize('denial', ['engaged', 'lateral', 'armed', 'stale-ack', 'stale-output', 'invalid-can', 'invalid-speed'])
def test_automatic_speed_never_bypasses_engagement_or_freshness(denial):
  modes, feed = conditional_runtime_fixture()
  if denial == 'engaged':
    feed['selfdriveState'].enabled = True
  elif denial == 'lateral':
    feed['carControl'].latActive = True
  elif denial == 'armed':
    modes.ack['conditionalModelAck'].idle = False
  elif denial == 'stale-ack':
    modes.ack.logMonoTime['conditionalModelAck'] = BASE - 1_000_000_000
  elif denial == 'stale-output':
    modes.owner.frame = BASE - 1_000_000_000
  elif denial == 'invalid-can':
    feed['carState'].canValid = False
  else:
    feed['carState'].vEgo = float('nan')
  with patch('openpilot.starpilot.models.conditional_runtime.time.monotonic_ns', return_value=BASE):
    assert modes.before_frame(feed) is None
  assert modes.owner.phase == 'ready' and modes.owner.active == 'city'
  assert not modes.owner.error and not modes.paused
  modes.owner.models['highway'][2].assert_not_called()


def test_engaging_before_hold_cancels_without_false_failure_or_disabling_automation():
  modes, feed = conditional_runtime_fixture()
  with patch('openpilot.starpilot.models.conditional_runtime.time.monotonic_ns', return_value=BASE):
    assert modes.before_frame(feed) is None
  assert modes.owner.phase == 'waiting'
  feed['selfdriveState'].enabled = True
  with patch('openpilot.starpilot.models.conditional_runtime.time.monotonic_ns', return_value=BASE + 20_000_000):
    assert modes.before_frame(feed) is None
  assert modes.owner.phase == 'ready' and modes.owner.active == 'city'
  assert not modes.owner.error and not modes.paused
  modes.owner.models['highway'][2].assert_not_called()


def test_auto_failure_pauses_for_drive_instead_of_retrying_every_frame():
  modes, feed = conditional_runtime_fixture()
  modes.owner.error = 'Candidate failed; previous model restored'
  with patch('openpilot.starpilot.models.conditional_runtime.time.monotonic_ns', return_value=BASE):
    assert modes.before_frame(feed) is None
    assert modes.paused and modes.owner.phase == 'ready'
    modes.owner.error = ''
    assert modes.before_frame(feed) is None
  assert modes.paused and modes.owner.active == 'city'


def test_identical_model_roles_never_reset_or_request_a_noop_handoff():
  modes, feed = conditional_runtime_fixture()
  modes.config['highway'] = 'city'
  feed['carState'].vEgo = 0.
  for i in range(601):
    now = BASE + i * 50_000_000
    feed.logMonoTime = dict.fromkeys(feed, now)
    modes.ack.logMonoTime['conditionalModelAck'] = modes.owner.frame = now
    with patch('openpilot.starpilot.models.conditional_runtime.time.monotonic_ns', return_value=now):
      assert modes.before_frame(feed) is None
  assert modes.owner.phase == 'ready' and modes.owner.request == ''
  for _, _, reset in modes.owner.models.values():
    reset.assert_not_called()


def test_previous_reset_failure_demands_isolated_recovery_instead_of_using_bad_history():
  owner, request = switcher()
  begin(owner, request)
  owner.models['highway'][2].side_effect = RuntimeError('device mapping failed')
  with pytest.raises(RuntimeError, match='isolated recovery required'):
    owner.abort('Candidate failed', BASE + 40_000_000)
  assert owner.phase == 'recovering' and owner.error


def test_randomized_transition_trace_never_changes_runner_without_disengagement():
  import random
  rng = random.Random(42)
  owner, _ = switcher()
  for i in range(1, 2000):
    now = BASE + i * 50_000_000
    idle = rng.choice((True, False))
    desired = {'session': owner.session, 'id': f'{i:032x}', 'model': rng.choice(('highway', 'city'))}
    ack = Ack(owner.session, owner.request, True, owner.announced, now)
    previous = owner.active
    changed = owner.before_frame(desired, now=now, idle=idle, ack=ack)
    if changed:
      assert idle and previous != owner.active
    if not idle:
      assert owner.active == previous
    if owner.phase == 'waiting' and not owner.announced:
      owner.announced = now
    owner.published(now, now)
