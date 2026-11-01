"""Real daemon scheduling call; no native permission or physical proof is mocked in."""
from types import SimpleNamespace as S
import pytest
from openpilot.starpilot.flm import steerlearnerd
from openpilot.starpilot.flm.tests.test_live_tracking_daemon import BASE, source


@pytest.mark.parametrize('event_collection', (False, True))
def test_baseline_capture_can_wake_on_evidence_before_any_pose_or_trial(monkeypatch, event_collection):
  controls, outputs = object(), object()
  waits = []
  class Finished(Exception):
    pass
  class SM:
    updated = {'deviceMotion': False}
    logMonoTime = {'deviceMotion': 0}
    def update(self, timeout=None):
      self.updated['deviceMotion'] = False
  adapter = S(event_collection=event_collection, prepare_control=lambda value: None,
              select_objective=lambda value: None)
  collector = S(adapter=adapter, ready=[], controls={}, recent_controls={},
                split=lambda *a, **k: None, _original_source_pose_queue=())
  actor = S(ingress=None, certificate=None, reason='collecting',
            interrupt=lambda *a, **k: None, tick=lambda *a, **k: None,
            close=lambda *a, **k: True)
  evidence = S(identity='a'*64, generation=1, domain='normalized_torque',
               stage_revision='stage', response_revision='response', source_epoch='b'*64,
               controller_mode='torque', policy='policy', baseline_revision='c'*64,
               controller_revision='controller', turn_assist=False,
               source_receipt_ns=BASE, control_ns=BASE)
  event = S(which=lambda: 'starpilotLateralState',
            starpilotLateralState=S(learningEvidence=S(version=1)))
  original_next = steerlearnerd._SourceDelivery.next
  def next_delivery(delivery, sm, *, pending_evidence=()):
    waits.append(pending_evidence)
    if len(waits) == 2:
      assert collector.ready == [] and collector._original_source_pose_queue == ()
      assert actor.ingress is None and actor.certificate is None
      assert pending_evidence == ((controls, outputs) if event_collection else ())
      raise Finished
    return original_next(delivery, sm, pending_evidence=pending_evidence)
  def drain(control_socket, control, applied_socket, output, *args):
    control(event, None, BASE)
  monkeypatch.setattr(steerlearnerd._SourceDelivery, 'next', next_delivery)
  monkeypatch.setattr(steerlearnerd, 'drain_pair', drain)
  monkeypatch.setattr(steerlearnerd, 'read_control', lambda event: evidence)
  monkeypatch.setattr(steerlearnerd, 'FullrateCollector', lambda adapter: collector)
  monkeypatch.setattr('openpilot.starpilot.flm.event_collector.EventFullrateCollector', lambda adapter: collector)
  with pytest.raises(Finished):
    steerlearnerd.run(S(), SM(), controls, outputs, S(send=lambda *a: None),
        lambda *a: adapter, None, source, lambda evidence: ('objective',),
        lambda evidence: 'objective', None, None,
        S(cancel=lambda *a: None, close=lambda *a: True), None,
        clock=lambda: BASE, live_actor=actor)
  assert waits[0] == ()
