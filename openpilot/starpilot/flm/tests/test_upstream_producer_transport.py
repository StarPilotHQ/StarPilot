"""Installed source closure only; no kernel or model-output equivalence proof."""
from openpilot.starpilot.flm.tests._drain_fixture import separate_drain_fixture
import hashlib
from pathlib import Path

import pytest

from openpilot.starpilot.flm import producer_clock_equivalence as clock


APPROVED = {
  'selfdrive/modeld/modeld.py': '8aa09862919bd3900489b2444f03179661b62e45f683270abf8c50d1ae3173fa',
  'visionipc/visionipc_py.cc': '6c131c4f5eccb9f822f1aaae63733f26b1427c52340b61c9cdaf6cd4aee2f89d',
  'visionipc/visionipc_server.cc': 'a4325a5171eacb931eb271d06a992d2f27216040df113d92099f62cdfa67d81a',
  'visionipc/visionipc_client.cc': 'd142cd60eedabfc581e9770fcd6f0ca81b762ba9dbc608b54dd20a27a317a52d',
}


def test_exact_installed_upstream_closure_is_checked_before_contract():
  contract = clock.pinned_default_contract()
  assert len(clock.PRODUCER_SOURCE_HASHES) == 19
  assert {relative: expected for _, relative, expected in clock.PRODUCER_SOURCE_HASHES if relative in APPROVED} == APPROVED
  assert contract.producer_revision == clock.CLOSURE_SHA256
  assert contract.proof_sha256 == clock.CLOSURE_SHA256


@pytest.mark.parametrize('relative', tuple(APPROVED))
def test_unapproved_change_to_each_new_producer_fails_closed(monkeypatch, relative):
  original = Path.read_bytes
  reached = []
  def changed(path):
    body = original(path)
    if path.as_posix().endswith(relative):
      reached.append(path)
      assert hashlib.sha256(body).hexdigest() == APPROVED[relative]
      return body + b'\n'
    return body
  monkeypatch.setattr(Path, 'read_bytes', changed)
  with pytest.raises(ValueError, match='unsupported original mixed-producer source closure'):
    clock.pinned_default_contract()
  assert len(reached) == 1


def test_unapproved_second_producer_cannot_be_blessed_by_new_modeld_pin(monkeypatch):
  rows = list(clock.PRODUCER_SOURCE_HASHES)
  index = next(index for index, (_, relative, _) in enumerate(rows) if relative == 'system/sensord/sensord.py')
  package, relative, _ = rows[index]
  rows[index] = (package, relative, '0' * 64)
  monkeypatch.setattr(clock, 'PRODUCER_SOURCE_HASHES', tuple(rows))
  with pytest.raises(ValueError, match='unsupported original mixed-producer source closure'):
    clock.pinned_default_contract()


def test_actual_live_collector_startup_uses_installed_producer_contract():
  from types import SimpleNamespace
  from openpilot.starpilot.flm.fullrate_collector import FullrateCollector
  from openpilot.starpilot.flm.source_plant_runtime import start_original_capture

  enabled = []
  adapter = SimpleNamespace(enable_original_context_capture=lambda: enabled.append(True),
                            current_physical_valid=False, last_physical_ns=0, source_plant_clock=None)
  collector = FullrateCollector(adapter)
  assert start_original_capture(collector)
  assert enabled == [True]
  assert collector.original_source_capture is not None
  assert collector.original_source_clock_contract == clock.pinned_default_contract()
  assert adapter.source_plant_capture_error is None
  assert not adapter.current_physical_valid and adapter.last_physical_ns == collector.last_qualified_response == 0
  assert not collector.ready and collector.live_tracking is None


def test_changed_model_producer_still_blocks_actual_live_collector(monkeypatch):
  from types import SimpleNamespace
  from openpilot.starpilot.flm.fullrate_collector import FullrateCollector
  from openpilot.starpilot.flm.source_plant_runtime import start_original_capture

  original = Path.read_bytes
  def changed(path):
    body = original(path)
    return body + b'\n' if path.as_posix().endswith('selfdrive/modeld/modeld.py') else body
  monkeypatch.setattr(Path, 'read_bytes', changed)
  enabled = []
  adapter = SimpleNamespace(enable_original_context_capture=lambda: enabled.append(True),
                            current_physical_valid=False, last_physical_ns=0, source_plant_clock=None)
  collector = FullrateCollector(adapter)
  assert not start_original_capture(collector)
  assert enabled == []
  assert adapter.source_plant_capture_error == 'original_source_clock_contract_unavailable'
  assert not adapter.current_physical_valid and adapter.last_physical_ns == collector.last_qualified_response == 0
  assert getattr(collector, 'original_source_capture', None) is None
  from openpilot.starpilot.flm.source_observation_report import refusal_report
  error = refusal_report(collector)['lastException']
  assert error['type'] == 'ValueError'
  assert 'openpilot/selfdrive/modeld/modeld.py' in error['message']


def test_actual_daemon_reports_startup_source_refusal_without_issuing_a_trial(monkeypatch):
  """Synthetic parent input; genuine installed-source refusal, no native permission."""
  from types import SimpleNamespace
  from openpilot.common.swaglog import cloudlog
  from openpilot.starpilot.flm import steerlearnerd
  from openpilot.starpilot.flm.tests.test_live_tracking_daemon import BASE, LoopComplete, NoLegacyFitter, RecordingActor, source

  now, sends, logs = [BASE], [], []
  original = Path.read_bytes
  monkeypatch.setattr(Path, 'read_bytes', lambda path: original(path)+b'\n' if path.as_posix().endswith('selfdrive/modeld/modeld.py') else original(path))
  monkeypatch.setattr(cloudlog, 'event', lambda name, **kw: logs.append((name, kw)))

  class SM:
    updated = {'deviceMotion': False}
    logMonoTime = {'deviceMotion': 0}
    steps = 0

    def update(self, timeout=None):
      if timeout == 0:
        return
      self.steps += 1
      if self.steps > 7:
        raise LoopComplete
      now[0] = BASE+self.steps*1_000_000_000

  actor, fitter = RecordingActor(), NoLegacyFitter()
  actor.interrupt = lambda reason, **kw: setattr(actor, 'reason', reason)
  fitter.cancel = lambda reason: None
  control_socket, output_socket = object(), object()
  def forbid_capture() -> None:
    raise AssertionError('Unapproved source must not enable capture')

  adapter = SimpleNamespace(event_collection=True, source_plant_protocol='declared-unit-source-refusal',
    source_plant_clock=None, current_physical_valid=False, last_physical_ns=0,
    enable_original_context_capture=forbid_capture)

  def drain(socket, callback, receive, sink, stamp):
    if socket is control_socket:
      event = SimpleNamespace(which=lambda: 'starpilotLateralState',
        starpilotLateralState=SimpleNamespace(learningEvidence=SimpleNamespace(version=1)))
      callback(event, sink, stamp)

  def read_control(event):
    return SimpleNamespace(source_epoch=source(now[0]).source_epoch, source_receipt_ns=now[0], control_ns=now[0],
      identity='f'*64, generation=1, domain='normalized_torque', stage_revision='stage', response_revision='response',
      controller_mode='starpilot', policy='declared-unit-policy', baseline_revision='baseline', controller_revision='controller', turn_assist=False)

  monkeypatch.setattr(steerlearnerd, 'drain_pair', separate_drain_fixture(drain))
  monkeypatch.setattr(steerlearnerd, 'read_control', read_control)
  with pytest.raises(LoopComplete):
    steerlearnerd.run(SimpleNamespace(), SM(), control_socket, output_socket,
      SimpleNamespace(send=lambda topic, message: sends.append((topic, message.as_reader()))),
      lambda *args: adapter, None, lambda stamp: source(stamp), lambda evidence: ('declared-unit',),
      lambda evidence: 'declared-unit', None, None, fitter, None, clock=lambda: now[0], live_actor=actor)
  learning = [message.autoSteeringLearning.learning for topic, message in sends if topic == 'autoSteeringLearning']
  assert learning and all(row.reason == 'original_source_clock_contract_unavailable' for row in learning)
  tracking = [message.starpilotSteeringTracking for topic, message in sends if topic == 'starpilotSteeringTracking']
  assert tracking and all(row.trial.version == 0 and row.restore.version == 0 for row in tracking)
  assert actor.closed and fitter.closed
  failures = [value for name, value in logs if name == 'auto_learn_source_stall']
  assert len(failures) == 1
  assert failures[0]['captureError'] == 'original_source_clock_contract_unavailable'
  assert 'openpilot/selfdrive/modeld/modeld.py' in failures[0]['sourceObservation']['lastException']['message']
