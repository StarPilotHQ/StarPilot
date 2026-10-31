"""Actual daemon scheduling seams; these fixtures assert no physical permission."""
from dataclasses import replace
from types import SimpleNamespace as S
import pytest
from openpilot.starpilot.flm import steerlearnerd
from openpilot.starpilot.flm.live_tracking_response import prepare_response_witness, response_witness_value
from openpilot.starpilot.flm.tests.test_live_tracking_daemon import BASE, source
from openpilot.starpilot.flm.tests.test_live_trial_owner import offer


@pytest.mark.parametrize('state,stamp,upper,now,expected', [
  (BASE+50, BASE, BASE+50, BASE+50, True),
  (BASE+49, BASE, BASE+50, BASE+50, False),
  (BASE+50, BASE, BASE+50, BASE+100_000_000, True),
  (BASE+50, BASE, BASE+50, BASE+100_000_001, False),
  (BASE+50, BASE+51, BASE+50, BASE+50, False),
  (True, BASE, BASE+50, BASE+50, False),
  (BASE+50, BASE, 'unknown', BASE+50, False),
])
def test_pending_bracket_hint_waits_for_original_state_and_original_deadline(state, stamp, upper, now, expected):
  collector = S(_original_source_pose_queue=(S(mono_ns=stamp, source_upper_ns=upper),))
  assert steerlearnerd._pending_pose_bracket_ready(collector, S(state_ns=state), now) is expected
  assert collector._original_source_pose_queue[0].mono_ns == stamp


def test_absent_pending_pose_does_not_start_extra_observation():
  assert not steerlearnerd._pending_pose_bracket_ready(S(), S(state_ns=BASE), BASE)


@pytest.mark.parametrize('missing', ['state_bracket', 'native_pair', 'delivery_in_batch', 'delivery_received_by_native_refresh', 'pending_trial_ingest'])
@pytest.mark.parametrize('retained_header', [False, True])
def test_actual_callbacks_publish_new_witness_before_remaining_fifo_work(monkeypatch, missing, retained_header):
  now = [BASE-25_000_000]
  old = offer(BASE-50_000_000)
  raw = response_witness_value(old.response_witness)
  fresh = prepare_response_witness(replace(raw, last_control_ns=BASE,
    last_apply_ns=BASE, last_pose_ns=BASE, valid_until_ns=BASE+100_000_000), old.binding, BASE+30_000_000)
  renewed = replace(old, sequence=2, observed_ns=BASE+30_000_000,
    expires_ns=BASE+100_000_000, response_witness=fresh)

  class Stop(Exception):
    pass

  class SM:
    updated = {'deviceMotion': False}
    logMonoTime = {'deviceMotion': BASE-50_000_000 if missing in ('delivery_in_batch', 'delivery_received_by_native_refresh') else BASE}
    delivered_once = False
    def update(self, timeout=None):
      if timeout == 0:
        self.updated['deviceMotion'] = (now[0] >= BASE+30_000_000 and
          (missing == 'delivery_in_batch' or missing == 'delivery_received_by_native_refresh' and not self.delivered_once))
        if self.updated['deviceMotion']:
          self.logMonoTime['deviceMotion'] = BASE
          self.delivered_once = True
      else:
        now[0] += 25_000_000
        self.updated['deviceMotion'] = True
        self.logMonoTime['deviceMotion'] = (BASE-50_000_000 if missing in ('delivery_in_batch', 'delivery_received_by_native_refresh') else
          BASE if retained_header else now[0])
    def __getitem__(self, key):
      assert key == 'pandaStates'
      return []

  hold_bracket = [False]
  def evidence():
    return S(source_epoch='b'*64, source_receipt_ns=BASE, control_ns=now[0], state_ns=BASE-1 if hold_bracket[0] else now[0]-10_000_000,
      identity='a'*64, generation=1, domain='normalized_torque', stage_revision='stage',
      response_revision='response', controller_mode='torque', policy='policy', baseline_revision='d'*64,
      controller_revision='controller', turn_assist=False, native_required=False)

  adapter = S(event_collection=True, source_plant_protocol='unit', bind_source=lambda value:None,
    select_objective=lambda value:None, prepare_control=lambda value:value,
    envelope=S(prior_full_profile_sha256='e'*64))
  initial = evidence()
  initial.control_ns = initial.state_ns = BASE-50_000_000
  collector = S(adapter=adapter, ready=[(initial, S(apply_ns=BASE-50_000_000))], controls={}, recent_controls={},
    physical_context=('unit',), _original_source_pose_queue=(), right_available=False)
  def admit(control, stamp):
    collector.controls[control.control_ns] = control
    collector.right_available = control.state_ns >= BASE
    return True
  def join(output, stamp):
    collector.ready.append((collector.controls[output.control_ns], output))
    return True
  collector.control, collector.output = admit, join

  class Actor:
    reason = 'unit_pending_offer'
    ingress = None
    def __init__(self):
      self.calls = 0
      self.closed = False
      self.published = []
    def tick(self, *args, **kwargs):
      self.calls += 1
      assert self.calls == 1
      if missing == 'pending_trial_ingest':
        self.ingress = S(optimizer=S(offer_state={'phase':'trial', 'accepted':False, 'deadline_ns':old.deadline_ns}))
      return old
    def mark_publication(self, value):
      self.published.append(value.sequence)
    def interrupt(self, *args, **kwargs):
      pass
    def close(self, reason):
      self.closed = True
      return True

  actor = Actor()
  control_tape, output_tape = object(), object()
  original_next = steerlearnerd._SourceDelivery.next
  def observe_wait(delivery, sm, *, pending_evidence=()):
    if missing == 'pending_trial_ingest' and actor.calls:
      assert not collector._original_source_pose_queue
      assert pending_evidence == (control_tape, output_tape)
      # The fixture owns the arrival schedule below; actual socket polling is
      # separately tested. This assertion observes the real daemon's API call.
      return original_next(delivery, sm)
    return original_next(delivery, sm, pending_evidence=pending_evidence)
  monkeypatch.setattr(steerlearnerd._SourceDelivery, 'next', observe_wait)
  fitter = S(cancel=lambda *args:None, close=lambda *args:True)
  history = S(offer=None, requalifier=None, reset=lambda:None, advance=lambda *args, **kwargs:None,
    observe_response_recovery=lambda *args:False)
  monkeypatch.setattr(steerlearnerd, '_LiveHistoryRestore', lambda *args:history)
  monkeypatch.setattr('openpilot.starpilot.flm.event_collector.EventFullrateCollector', lambda *args:collector)
  monkeypatch.setattr('openpilot.starpilot.flm.source_plant_runtime.start_original_capture', lambda *args:True)
  monkeypatch.setattr('openpilot.starpilot.flm.source_plant_runtime.observe', lambda *args:None)
  def native(sm, cp, control, stamp):
    if missing == 'delivery_received_by_native_refresh':
      return stamp < BASE+30_000_000 or sm.logMonoTime['deviceMotion'] >= BASE
    return missing != 'native_pair' or now[0] <= BASE+10_000_000 or control.control_ns >= BASE+25_000_000
  monkeypatch.setattr('openpilot.starpilot.flm.native_health.current_native_health', native)
  monkeypatch.setattr(steerlearnerd, 'read_control', lambda *args:evidence())
  monkeypatch.setattr(steerlearnerd, 'read_applied', lambda *args:S(control_ns=now[0], apply_ns=now[0]))
  premature = []
  def early(*args, **kwargs):
    if missing in ('delivery_in_batch', 'delivery_received_by_native_refresh') and args[0].logMonoTime['deviceMotion'] < BASE:
      return None
    if not collector.right_available:
      if collector._original_source_pose_queue:
        premature.append(now[0])
      collector._original_source_pose_queue = (S(mono_ns=BASE, source_upper_ns=BASE),)
      return None
    collector._original_source_pose_queue = ()
    return renewed
  monkeypatch.setattr(steerlearnerd, '_early_tracking_offer', early)
  sent = []
  def send(topic, message):
    if topic == 'starpilotSteeringTracking':
      sent.append((now[0], message.starpilotSteeringTracking.trial.version,
                   message.starpilotSteeringTracking.trial.sequence))
  event = S(which=lambda:'starpilotLateralState',
    starpilotLateralState=S(learningEvidence=S(version=1), trackingApplication=S(version=0)))
  applied_event = S(which=lambda:'starpilotCarState', starpilotCarState=S(appliedEvidence=S(version=1)))
  batches = []
  def drain(control_socket, control, applied_socket, output, *args):
    batches.append(now[0])
    if len(batches) == 1:
      control(event, None, now[0])
      return
    if missing == 'state_bracket':
      hold_bracket[0] = True
      control(event, None, now[0])
      hold_bracket[0] = False
      assert not premature
    now[0] += 5_000_000
    control(event, None, now[0])
    if missing == 'native_pair':
      output(applied_event, None, now[0])
    # Remaining original FIFO work would otherwise cross the old100ms lease.
    now[0] += 70_000_000
    raise Stop
  monkeypatch.setattr(steerlearnerd, 'drain_pair', drain)
  with pytest.raises(Stop):
    steerlearnerd.run(S(), SM(), control_tape, output_tape, S(send=send), lambda *args, **kwargs:adapter,
      None, source, lambda *args:('objective',), lambda *args:'objective', None, None, fitter, None,
      clock=lambda:now[0], live_actor=actor)
  fresh_sends = [stamp for stamp, version, sequence in sent if version == 1 and sequence == 2]
  assert fresh_sends == [BASE+30_000_000]
  assert fresh_sends[0] < old.expires_ns < now[0]
  assert renewed.deadline_ns == old.deadline_ns and renewed.target == old.target
  assert actor.closed and actor.published == [1, 2]
