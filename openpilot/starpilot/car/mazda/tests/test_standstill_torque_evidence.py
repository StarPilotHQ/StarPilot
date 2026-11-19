"""The stopped frame that releases GEN2 AOL torque records the zero it transmitted.

The controller sends zero on the first stopped frame while latActive is still True.
applied_snapshot treats a frame with no projection as "no new command" and renews the
previous evidence, which would pair the transmitted zero with the last nonzero torque.
"""
import importlib
import sys
import types
from types import SimpleNamespace

import pytest

from opendbc.car import structs
from opendbc.car.mazda.tests.test_gen2_standstill_torque import TORQUE, cp, Rig
from opendbc.car.steering_projection import TorqueProjectionObserver, project_candidate


@pytest.fixture(scope='module')
def hooks():
  # Host checkouts lack msgq's IPC extension; stand in for it only if it cannot be
  # imported, and remove everything imported against the stand-in afterwards.
  before = set(sys.modules)
  stub = None
  try:
    importlib.import_module('openpilot.cereal.messaging')
  except (ImportError, OSError):
    stub = types.ModuleType('openpilot.cereal.messaging')
    stub.SubMaster = stub.PubMaster = object
    sys.modules[stub.__name__] = stub
  yield importlib.import_module('openpilot.starpilot.flm.evidence_hooks')
  if stub is not None:
    for name in set(sys.modules) - before:
      del sys.modules[name]
    sys.modules.pop(stub.__name__, None)


class Topics(dict):
  def __missing__(self, key):
    return True


def run_frame(hooks, owner, standstill, apply_ns):
  ctl = owner.CI.CC
  hooks.begin_applied_frame(owner)
  cc = structs.CarControl()
  cc.latActive = True
  cc.actuators.torque = TORQUE
  cs = structs.CarState(standstill=standstill, canValid=True, vEgo=0.0 if standstill else 5.0)
  ns = SimpleNamespace(out=cs, cam_lkas={}, acc={}, acc_standstill=standstill, steer_release_speed=standstill)
  output, _ = ctl.update(cc.as_reader(), ns, 0)
  owner.learning_applied_evidence = hooks.applied_snapshot(owner, cc, cs, output, apply_ns=apply_ns, state_ns=apply_ns)
  return owner.learning_applied_evidence


def make_owner():
  rig = Rig(cp())
  rig.ctl.learning_projection_observer = TorqueProjectionObserver(cp())
  sm = SimpleNamespace(valid=Topics(), alive=Topics(), seen=Topics(), logMonoTime={'carControl': 1})
  return SimpleNamespace(CP=rig.ctl.CP, sm=sm, CI=SimpleNamespace(CC=rig.ctl, CP=rig.ctl.CP))


def test_released_frame_records_the_transmitted_zero_not_the_old_command(hooks):
  owner = make_owner()
  for i in range(30):
    moving = run_frame(hooks, owner, False, 1_000_000 + i * 10_000_000)
  assert moving.projection.steer_requested and moving.projection.final_raw != 0

  apply_ns = 1_000_000 + 30 * 10_000_000
  stopped = run_frame(hooks, owner, True, apply_ns)
  assert stopped.apply_ns == apply_ns
  assert stopped.applied == 0.0
  assert stopped.projection.final_raw == 0


def test_released_frame_cannot_train_a_fit(hooks):
  owner = make_owner()
  for i in range(30):
    run_frame(hooks, owner, False, 1_000_000 + i * 10_000_000)
  stopped = run_frame(hooks, owner, True, 1_000_000 + 30 * 10_000_000)
  assert not stopped.projection.steer_requested
  assert stopped.limited
  assert project_candidate(stopped.projection, TORQUE) is None


def test_moving_frames_still_record_requested_steering(hooks):
  owner = make_owner()
  moving = run_frame(hooks, owner, False, 1_000_000)
  assert moving.projection.steer_requested
