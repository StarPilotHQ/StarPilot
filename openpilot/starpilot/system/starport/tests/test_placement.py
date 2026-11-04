import os
import argparse
import ast
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.system.starport import placement
from openpilot.starpilot.system.starport.placement import FALLBACK_NICE, PLACEMENT_CHECK_INTERVAL, RENDER_CORE, RendererPlacement

SCHED_IDLE = 5


class FakeKernel:
  """Per-thread affinity and policy, standing in for Linux (and for macOS, which has neither)."""

  def __init__(self, monkeypatch, threads=(100,), mask=(0, 1, 2, 3, 4, 5), idle_allowed=True):
    self.masks = {tid: set(mask) for tid in threads}
    self.main = threads[0]
    self.online = {RENDER_CORE: True}
    self.policy = None
    self.niced = 0
    self.idle_allowed = idle_allowed
    monkeypatch.setattr(os, "SCHED_IDLE", SCHED_IDLE, raising=False)
    monkeypatch.setattr(os, "sched_param", lambda priority: priority, raising=False)
    monkeypatch.setattr(os, "sched_getaffinity", self.getaffinity, raising=False)
    monkeypatch.setattr(os, "sched_setaffinity", self.setaffinity, raising=False)
    monkeypatch.setattr(os, "sched_setscheduler", self.setscheduler, raising=False)
    monkeypatch.setattr(os, "nice", self.nice)

  def getaffinity(self, tid):
    if (tid or self.main) not in self.masks:
      raise OSError(3, "No such process")
    return set(self.masks[tid or self.main])

  def setaffinity(self, tid, mask):
    if not any(self.online.get(core, True) for core in mask):
      raise OSError(22, "Invalid argument")
    self.masks[tid or self.main] = set(mask)

  def setscheduler(self, tid, policy, param):
    if not self.idle_allowed:
      raise OSError(1, "Operation not permitted")
    self.policy = policy

  def nice(self, increment):
    self.niced += increment
    return self.niced

  def make(self, events, **kwargs):
    return RendererPlacement(report=events.append, thread_ids=lambda: list(self.masks),
                             core_online=lambda core: self.online.get(core, True), **kwargs)


@pytest.fixture
def events():
  return []


def test_start_uses_sched_idle_and_pins_every_thread_to_core_6(monkeypatch, events):
  kernel = FakeKernel(monkeypatch, threads=(100, 101, 102))
  kernel.make(events).start()

  assert kernel.policy == SCHED_IDLE and kernel.niced == 0
  assert all(mask == {6} for mask in kernel.masks.values())
  assert events[0] == {"event": "cpu_placement", "policy": "idle", "inherited_cores": [0, 1, 2, 3, 4, 5], "core": 6}
  assert events[1] == {"event": "cpu_placement", "pinned": True, "cores": [6]}


def test_falls_back_to_nice_without_sched_idle(monkeypatch, events):
  kernel = FakeKernel(monkeypatch, idle_allowed=False)
  kernel.make(events).start()

  assert kernel.policy is None and kernel.niced == FALLBACK_NICE
  assert events[0]["policy"] == f"nice{FALLBACK_NICE}"
  assert kernel.masks[100] == {6}


def test_offroad_power_save_then_repin_when_core_6_returns(monkeypatch, events):
  kernel = FakeKernel(monkeypatch, threads=(100, 101))
  kernel.online[RENDER_CORE] = False  # started offroad: big cluster offline
  p = kernel.make(events)
  p.start()
  assert kernel.masks[100] == {0, 1, 2, 3, 4, 5} and len(events) == 1  # left alone, no error

  kernel.online[RENDER_CORE] = True  # onroad: big cluster back
  p.maintain(PLACEMENT_CHECK_INTERVAL)
  assert all(mask == {6} for mask in kernel.masks.values())
  assert events[-1] == {"event": "cpu_placement", "pinned": True, "cores": [6]}

  kernel.online[RENDER_CORE] = False  # offroad again: kernel widens our mask
  kernel.masks = {tid: set(range(8)) for tid in kernel.masks}
  p.maintain(2 * PLACEMENT_CHECK_INTERVAL)
  assert events[-1] == {"event": "cpu_placement", "pinned": False, "reason": "core 6 offline"}

  kernel.online[RENDER_CORE] = True
  p.maintain(3 * PLACEMENT_CHECK_INTERVAL)
  assert all(mask == {6} for mask in kernel.masks.values()) and events[-1]["pinned"]


def test_threads_created_later_are_pinned_and_checks_are_throttled(monkeypatch, events):
  kernel = FakeKernel(monkeypatch)
  p = kernel.make(events)
  p.start()
  count = len(events)

  kernel.masks[200] = {0, 1, 2, 3, 4, 5}  # e.g. a graphics-driver thread started after the pin
  p.maintain(PLACEMENT_CHECK_INTERVAL / 2)
  assert kernel.masks[200] != {6}  # not due yet

  p.maintain(PLACEMENT_CHECK_INTERVAL)
  assert kernel.masks[200] == {6} and len(events) == count + 1

  p.maintain(2 * PLACEMENT_CHECK_INTERVAL)  # nothing changed: nothing logged
  assert len(events) == count + 1


def test_exited_thread_is_ignored(monkeypatch, events):
  kernel = FakeKernel(monkeypatch, threads=(100, 101))
  p = kernel.make(events)
  p.thread_ids = lambda: [100, 101, 999]  # 999 exited between listing and setting
  p.start()
  assert kernel.masks[100] == kernel.masks[101] == {6}


def test_defaults_target_core_6():
  assert placement.RENDER_CORE == 6


def test_normal_scheduling_pins_without_sched_idle(monkeypatch, events):
  # starportd: core 6 next to the renderer, but never below other work
  kernel = FakeKernel(monkeypatch, threads=(100, 101))
  kernel.make(events, idle=False).start()
  assert kernel.policy is None and kernel.niced == 0
  assert all(mask == {6} for mask in kernel.masks.values())
  assert events[0]["policy"] == "normal"


def test_background_renderer_is_idle_and_excludes_all_driving_cores(monkeypatch, events):
  kernel = FakeKernel(monkeypatch, threads=(100, 101, 102))
  p = kernel.make(events, core=placement.BACKGROUND_RENDER_CORES)
  p.start()
  assert kernel.policy == SCHED_IDLE and kernel.niced == 0
  assert placement.BACKGROUND_RENDER_CORES == (0, 1, 2)
  assert all(mask == {0, 1, 2} for mask in kernel.masks.values())
  assert all(mask.isdisjoint({3, 4, 5, 6, 7}) for mask in kernel.masks.values())
  assert events[0]['policy'] == 'idle' and events[0]['cores'] == [0, 1, 2]
  kernel.masks[200] = set(range(8))
  p.maintain(PLACEMENT_CHECK_INTERVAL)
  assert kernel.masks[200] == {0, 1, 2}


def test_background_pool_tracks_online_cores_without_using_driving_cores(monkeypatch, events):
  kernel = FakeKernel(monkeypatch)
  kernel.online.update({0: True, 1: False, 2: True})
  p = kernel.make(events, core=placement.BACKGROUND_RENDER_CORES)
  p.start()
  assert kernel.masks[100] == {0, 2}
  kernel.online.update({1: True, 2: False})
  p.maintain(PLACEMENT_CHECK_INTERVAL)
  assert kernel.masks[100] == {0, 1}


@pytest.mark.parametrize('cores', [(), (-1,), (0, -1)])
def test_rejects_invalid_core_pools(cores):
  with pytest.raises(ValueError):
    RendererPlacement(core=cores)


class PlacementReached(Exception):
  pass


class TestStarPortStartup(unittest.TestCase):
  def test_transport_priority_before_workers(self):
    tree = ast.parse((Path(__file__).parents[1] / 'daemon.py').read_text())
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    for device in ('mici', 'tici', 'tizi', 'pc'):
      with self.subTest(device=device):
        order = []
        hardware = types.ModuleType('openpilot.common.hardware')
        hardware.COMMA_HARDWARE = device != 'pc'
        hardware.HARDWARE = types.SimpleNamespace(get_device_type=lambda device=device: device)
        placement = types.ModuleType('openpilot.starpilot.system.starport.placement')
        instance = Mock()

        def start(order=order):
          order.append('start')
          raise PlacementReached

        instance.start.side_effect = start
        placement.RendererPlacement = Mock(return_value=instance)
        fake_os = Mock(PRIO_PROCESS=0)
        fake_os.setpriority.side_effect = lambda *args, order=order: order.append(('priority', args))
        fake_os.unlink.side_effect = PlacementReached
        namespace = {'argparse': argparse, '__doc__': 'fixture', 'os': fake_os, 'json': json,
                     'STARPORT_SOCKET_PATH': 'fixture'}
        exec(compile(ast.Module(body=[main], type_ignores=[]), '<actual-starport-main>', 'exec'), namespace)
        modules = {'openpilot.common.hardware': hardware, 'openpilot.starpilot.system.starport.placement': placement}
        with patch.dict(sys.modules, modules), patch.object(sys, 'argv', ['starportd']):
          with self.assertRaises(PlacementReached):
            namespace['main']()
        if device != 'pc':
          self.assertEqual(order, [('priority', (0, 0, 5)), 'start'])
        else:
          fake_os.setpriority.assert_not_called()
        if device != 'pc':
          placement.RendererPlacement.assert_called_once()
          self.assertIs(placement.RendererPlacement.call_args.kwargs['idle'], False)
        else:
          placement.RendererPlacement.assert_not_called()
