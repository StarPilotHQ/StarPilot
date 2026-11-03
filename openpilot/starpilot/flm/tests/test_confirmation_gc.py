"""Actual daemon-loop GC lifecycle; declared actor state is NOT permission."""

import gc
from types import SimpleNamespace

import pytest

from openpilot.starpilot.flm import steerlearnerd
from openpilot.starpilot.flm.tests.test_live_tracking_daemon import BASE, source, RecordingActor, NoLegacyFitter, LoopComplete


def state(phase, *, accepted=False, expired=False):
  return {'phase': phase, 'accepted': accepted, 'deadline_ns': BASE - 1 if expired else BASE + 120_000_000_000}


def run_script(monkeypatch, states, *, initially_enabled=True, stop_pending=False, acknowledge_in_drain=False):
  prior = gc.isenabled()
  (gc.enable if initially_enabled else gc.disable)()
  now = [BASE]
  observations, polls, persistence_gc = [], [], []

  class Actor(RecordingActor):
    def __init__(self):
      super().__init__()
      self.index = 0
      self.set_state(states[0])

    def set_state(self, value):
      self.ingress = None if value is None else SimpleNamespace(optimizer=SimpleNamespace(offer_state=value))

    def tick(self, *args, **kwargs):
      super().tick(*args, **kwargs)
      self.index += 1
      if self.index < len(states):
        self.set_state(states[self.index])
      return None

    def persistence_inputs(self, now_ns):
      current = None if self.ingress is None else self.ingress.optimizer.offer_state
      if current is not None and current['phase'] == 'confirmed' and current['accepted']:
        persistence_gc.append(gc.isenabled())
      return None  # Never fabricate a native-authorized save input.

  actor, fitter = Actor(), NoLegacyFitter()

  class SM:
    updated = {'deviceMotion': False}
    logMonoTime = {'deviceMotion': 0}
    sock = {'deviceMotion': object()}

    def update(self, timeout=None):
      if actor.index == len(states):
        raise LoopComplete
      current = None if actor.ingress is None else actor.ingress.optimizer.offer_state
      observations.append((current, gc.isenabled()))
      if stop_pending:
        raise LoopComplete
      now[0] += 10_000_000

  class Poller:
    def registerSocket(self, _socket):
      pass

    def poll(self, timeout):
      polls.append(timeout)

  acknowledged = False

  def drain(*args):
    nonlocal acknowledged
    current = None if actor.ingress is None else actor.ingress.optimizer.offer_state
    if acknowledge_in_drain and current is not None and current['phase'] == 'confirmed' and not acknowledged:
      # Only a declared loop-state transition, no application/native evidence.
      current['accepted'] = True
      acknowledged = True

  monkeypatch.setattr(steerlearnerd.messaging, 'Poller', Poller)
  monkeypatch.setattr(steerlearnerd, 'drain_pair', drain)
  try:
    with pytest.raises(LoopComplete):
      steerlearnerd.run(SimpleNamespace(), SM(), object(), object(), SimpleNamespace(send=lambda *args: None),
        None, None, lambda stamp: source(stamp), None, None, None, None, fitter, None,
        clock=lambda: now[0], live_actor=actor, live_persistence=object())
    assert gc.isenabled() is initially_enabled
    assert actor.closed and fitter.closed
  finally:
    (gc.enable if prior else gc.disable)()
  return observations, polls, persistence_gc


@pytest.mark.parametrize('initially_enabled', (True, False))
def test_actual_loop_keeps_confirmation_handoff_pending_then_restores_gc(monkeypatch, initially_enabled):
  states = [state('trial'), state('confirmed'), state('confirmed', accepted=True), state('rollback'), None]
  observed, polls, _ = run_script(monkeypatch, states, initially_enabled=initially_enabled)
  assert [enabled for _, enabled in observed] == [False, False, initially_enabled, initially_enabled, initially_enabled]
  assert polls == [10, 10]  # Same evidence sockets wake trial AND confirmation.


def test_ack_and_same_iteration_persistence_check_precede_gc_reenable(monkeypatch):
  observed, _polls, persistence_gc = run_script(monkeypatch, [state('confirmed')], acknowledge_in_drain=True)
  assert [enabled for _, enabled in observed] == [False]
  assert persistence_gc == [False]


@pytest.mark.parametrize('pending', (state('confirmed', expired=True), state('confirmed', accepted=True), state('rollback'), None))
def test_accepted_rollback_context_loss_and_original_expiry_do_not_defer(monkeypatch, pending):
  observed, polls, _ = run_script(monkeypatch, [pending])
  assert [enabled for _, enabled in observed] == [True]
  assert not polls


@pytest.mark.parametrize('initially_enabled', (True, False))
def test_shutdown_during_pending_confirmation_restores_original_gc(monkeypatch, initially_enabled):
  observed, _polls, _ = run_script(monkeypatch, [state('confirmed')], initially_enabled=initially_enabled, stop_pending=True)
  assert [enabled for _, enabled in observed] == [False]
