"""Native upstream host neutralization under supplied state, not mode qualification.

The input messages are synthetic, explicitly supplied through SubMaster.update_msgs.
The real Controls constructor, longitudinal controller, publication and native IPC
run in a fresh child process. No manager, vehicle, policy coordinator or CAN runs.
"""
import argparse
import ctypes
import hashlib
import importlib
import importlib.machinery
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.basedir import BASEDIR


def probe(system_longitudinal, output):
  # Check isolation before importing libraries which own process-global IPC state.
  prefix = os.environ.get("OPENPILOT_PREFIX", "")
  if not prefix.startswith("host-boundary-") or not os.environ.get("PARAMS_ROOT") or "CEREAL_FAKE" in os.environ:
    raise RuntimeError("An isolated native host-boundary test environment is required")
  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.tesla.interface import CarInterface
  from opendbc.car.tesla.values import CAR
  from openpilot.cereal import messaging
  from openpilot.common.params import Params
  from openpilot.common import params as params_module
  from openpilot.selfdrive.controls.controlsd import Controls

  native = {}
  for name in ("capnp.lib.capnp", "msgq._ipc"):
    module_file = importlib.import_module(name).__file__
    if module_file is None:
      raise RuntimeError(f"Native extension has no file: {name}")
    path = Path(module_file).resolve()
    if not any(str(path).endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES):
      raise RuntimeError(f"Host boundary requires a native extension: {name}")
    native[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
  if not isinstance(params_module.lib, ctypes.CDLL):
    raise RuntimeError("Host boundary requires native Params")
  params_library = Path(params_module.lib._name).resolve()
  native["Params C ABI"] = {"path": str(params_library), "sha256": hashlib.sha256(params_library.read_bytes()).hexdigest()}
  params = Params()
  fingerprint = gen_empty_fingerprint()
  fingerprint[2][0x293] = 8
  cp = CarInterface.get_params(CAR.TESLA_MODEL_Y, fingerprint, [], system_longitudinal, False, False)
  car_params_bytes = cp.to_bytes()
  params.put("CarParams", car_params_bytes, block=True)
  controls = Controls()
  subscriber = messaging.sub_sock("carControl", timeout=1000)
  observations = []
  phases = (
    ("active", True, True, False, False, 1.0),
    ("longitudinal_override", True, True, True, False, 1.0),
    ("lateral_fault", True, True, False, True, 1.0),
    ("disabled", False, False, False, False, 1.0),
    ("disabled_negative_plan", False, False, False, False, -2.0),
    ("explicitly_reenabled", True, True, False, False, 1.0),
  )
  frame = 0
  for name, enabled, active, override, steering_fault, target in phases:
    for _ in range(12):
      timestamp = 1_000_000_000 + frame * 10_000_000
      frame += 1
      messages = []

      def message(service, size=None, timestamp=timestamp, messages=messages):
        msg = messaging.new_message(service, size, valid=True, logMonoTime=timestamp)
        messages.append(msg)
        return getattr(msg, service)

      state = message("carState")
      state.canValid = True  # supplied synthetic host input, not observed bus health
      state.vEgo = state.vEgoRaw = 10.0
      state.vCruise = state.vCruiseCluster = 100.0
      state.steeringAngleDeg = 0.0
      state.steerFaultTemporary = steering_fault
      state.cruiseState.enabled = True
      state.cruiseState.available = True
      state.gearShifter = "drive"
      state.seatbeltUnlatched = False
      host = message("selfdriveState")
      host.enabled, host.active = enabled, active
      host.state = "enabled" if enabled else "disabled"
      parameters = message("vehicleParameters")
      parameters.stiffnessFactor = 1.0
      parameters.steerRatio = cp.steerRatio
      message("longitudinalPlan").aTarget = target
      message("modelV2").action.desiredCurvature = 0.0001
      message("lateralDelay").lateralDelay = 0.1
      message("carOutput")
      events = message("onroadEvents", 1 if override else 0)
      if override:
        events[0].name = "gasPressedOverride"
        events[0].overrideLongitudinal = True
      controls.sm.update_msgs(timestamp / 1e9, [msg.as_reader() for msg in messages])
      command, lateral_log = controls.state_control()
      controls.publish(command, lateral_log)
      received = messaging.recv_one(subscriber)
      if received is None:
        raise RuntimeError("Native carControl publication was not received")
      emitted = received.carControl
      observations.append({"phase": name, "frame": frame, "plan_acceleration": target,
                           "enabled": emitted.enabled, "lateral": emitted.latActive, "longitudinal": emitted.longActive,
                           "acceleration": emitted.actuators.accel, "steering_angle": emitted.actuators.steeringAngleDeg,
                           "cancel": emitted.cruiseControl.cancel, "override": emitted.cruiseControl.override,
                           "valid": received.valid})
  source_paths = ("openpilot/selfdrive/controls/controlsd.py", "openpilot/selfdrive/controls/lib/longcontrol.py",
                  "openpilot/starpilot/tests/test_control_host_boundary.py")
  report = {"scope": "synthetic upstream host neutralization and native publication only",
            "vehicle_qualification": "not_established", "system_longitudinal": system_longitudinal,
            "identity": "Model Y candidate deliberately supplied; EPS firmware and physical hardware not identified",
            "native": native,
            "car_params_sha256": hashlib.sha256(car_params_bytes).hexdigest(),
            "sources": {path: hashlib.sha256((Path(BASEDIR) / path).read_bytes()).hexdigest() for path in source_paths},
            "observations": observations}
  Path(output).write_text(json.dumps(report, indent=2) + "\n")


class TestControlHostBoundary(unittest.TestCase):
  def check_profile(self, system_longitudinal):
    ipc_root = "/tmp" if sys.platform == "darwin" else "/dev/shm"
    with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory(prefix="msgq_host-boundary-", dir=ipc_root) as ipc:
      output = Path(temporary) / "observations.json"
      environment = dict(os.environ, PARAMS_ROOT=str(Path(temporary) / "params"),
                         OPENPILOT_PREFIX=Path(ipc).name.removeprefix("msgq_"))
      for name in ("CEREAL_FAKE", "CEREAL_FAKE_PREFIX", "SIMULATION"):
        environment.pop(name, None)
      command = [sys.executable, "-m", "openpilot.starpilot.tests.test_control_host_boundary", "--probe",
                 "--owner", "system" if system_longitudinal else "stock", "--output", str(output)]
      completed = subprocess.run(command, cwd=BASEDIR, env=environment, capture_output=True, text=True, timeout=30)
      self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
      report = json.loads(output.read_text())
    self.assertEqual(len(report["observations"]), 72)
    for row in report["observations"]:
      with self.subTest(phase=row["phase"], frame=row["frame"]):
        disabled = row["phase"].startswith("disabled")
        longitudinal = system_longitudinal and row["phase"] not in ("longitudinal_override", "disabled", "disabled_negative_plan")
        self.assertTrue(row["valid"])
        self.assertEqual(row["enabled"], not disabled)
        self.assertEqual(row["lateral"], not disabled and row["phase"] != "lateral_fault")
        self.assertEqual(row["longitudinal"], longitudinal)
        self.assertEqual(row["cancel"], disabled)
        self.assertEqual(row["override"], system_longitudinal and row["phase"] == "longitudinal_override")
        if longitudinal:
          self.assertGreater(row["acceleration"], 0.0)
        else:
          self.assertEqual(row["acceleration"], 0.0)

  def test_controls_import_does_not_load_model_execution(self):
    program = """
import importlib.abc
import sys

class NoModelExecution(importlib.abc.MetaPathFinder):
  def find_spec(self, fullname, path=None, target=None):
    if fullname == 'openpilot.selfdrive.modeld.modeld' or fullname == 'tinygrad' or fullname.startswith('tinygrad.'):
      raise AssertionError('Controls must not import model execution: ' + fullname)
    return None

sys.meta_path.insert(0, NoModelExecution())
from openpilot.selfdrive.controls.controlsd import LAT_SMOOTH_SECONDS
assert type(LAT_SMOOTH_SECONDS) is float and LAT_SMOOTH_SECONDS == 0.0
"""
    completed = subprocess.run([sys.executable, '-B', '-c', program], cwd=BASEDIR,
                               capture_output=True, text=True, timeout=30)
    self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

  def test_inactive_system_longitudinal_neutralizes_warmed_controller_and_plan(self):
    self.check_profile(True)

  def test_stock_longitudinal_never_gets_host_acceleration(self):
    with patch.dict(os.environ, {"CEREAL_FAKE": "1", "CEREAL_FAKE_PREFIX": "must-not-be-used"}):
      self.check_profile(False)


if __name__ == "__main__":
  if "--probe" in sys.argv:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--owner", choices=("stock", "system"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    probe(arguments.owner == "system", arguments.output)
  else:
    unittest.main()
