"""Receiver scheduling contracts only; these sockets grant no native authority."""
from collections import deque
from types import SimpleNamespace
from openpilot.starpilot.flm import steerlearnerd


def test_pending_pose_waits_for_existing_evidence_without_consuming_fifo_heads(monkeypatch):
  controls, outputs = deque(['first-control', 'next-control']), deque(['first-output', 'next-output'])
  motion = object()
  polls, registrations, updates = [], [], []

  class Poller:
    def registerSocket(self, socket):
      registrations.append(socket)

    def poll(self, timeout):
      polls.append(timeout)
      return [controls, outputs]

  monkeypatch.setattr(steerlearnerd.messaging, 'Poller', Poller)
  sm = SimpleNamespace(sock={'deviceMotion': motion}, updated={'deviceMotion': False},
                       logMonoTime={'deviceMotion': 0}, update=lambda timeout=None: updates.append(timeout))
  delivery = steerlearnerd._SourceDelivery()
  for _ in range(2):
    assert delivery.next(sm, pending_evidence=(controls, outputs)) == 0
  assert registrations == [motion, controls, outputs]  # Reuse the bounded poller.
  assert polls == [10, 10] and updates == [0, 0]
  assert list(controls) == ['first-control', 'next-control']
  assert list(outputs) == ['first-output', 'next-output']


def test_incidental_fresh_motion_is_reused_without_another_blocking_wait(monkeypatch):
  calls = []
  monkeypatch.setattr(steerlearnerd.messaging, 'Poller', lambda: calls.append('unexpected-poller'))
  sm = SimpleNamespace(updated={'deviceMotion': True}, logMonoTime={'deviceMotion': 123},
                       update=lambda timeout=None: calls.append(timeout))
  delivery = steerlearnerd._SourceDelivery()
  delivery.refresh(sm)
  assert delivery.next(sm, pending_evidence=(object(), object())) == 123
  assert calls == [0]
  assert not delivery.pending and delivery.pose_header == 0
