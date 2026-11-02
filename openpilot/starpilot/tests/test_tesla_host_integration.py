"""Synthetic host -> native carControl IPC -> Tesla CAN -> DEBUG C safety.

Supplied host enable/override inputs are not a mode policy or rearm test. Vehicle
state and parser health come from scheduled CAN. The published command reaches
the interface unchanged; no vehicle, EPS actuation or fleet qualification follows.
"""
import argparse
import ctypes
import importlib
import importlib.machinery
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from openpilot.common.basedir import BASEDIR

FAKE_FLAGS = ("CEREAL_FAKE", "CEREAL_FAKE_PREFIX", "SIMULATION")
PHASES = (
  ("active", True, False, 1.0),
  ("supplied_longitudinal_override", True, True, 1.0),
  ("active_before_disable", True, False, 1.0),
  ("disabled_positive_plan", False, False, 1.0),
  ("disabled_negative_plan", False, False, -2.0),
  ("explicitly_reenabled_host_input", True, False, 1.0),
)


def probe(profile, output):
  # Native IPC libraries read process-global environment: validate before importing them.
  prefix = os.environ.get("OPENPILOT_PREFIX", "")
  if not prefix.startswith("tesla-host-") or not os.environ.get("PARAMS_ROOT") or any(name in os.environ for name in FAKE_FLAGS):
    raise RuntimeError("An isolated native Tesla host test environment is required")
  from openpilot.cereal import messaging
  from openpilot.common import params as params_module
  from openpilot.common.params import Params
  from openpilot.selfdrive.controls.controlsd import Controls
  from openpilot.starpilot.tests.tesla_fixture import EPS, FW_VERSIONS, NativeSafety, TeslaFixture, sha256, sha256_bytes

  native = {}
  for name in ("capnp.lib.capnp", "msgq._ipc", "_cffi_backend"):
    module_file = importlib.import_module(name).__file__
    if module_file is None:
      raise RuntimeError(f"Native extension has no file: {name}")
    path = Path(module_file).resolve()
    if not any(str(path).endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES):
      raise RuntimeError(f"Tesla host integration requires a native extension: {name}")
    native[name] = {"path": str(path), "sha256": sha256(path)}
  if not isinstance(params_module.lib, ctypes.CDLL):
    raise RuntimeError("Tesla host integration requires native Params")
  library = Path(params_module.lib._name).resolve()
  native["Params C ABI"] = {"path": str(library), "sha256": sha256(library)}

  safety = NativeSafety()
  observations = []
  try:
    fixture = TeslaFixture(safety, profile=profile, longitudinal=True)
    primed = fixture.prime()
    params = Params()
    cp_bytes = fixture.cp.to_bytes()
    params.put("CarParams", cp_bytes, block=True)
    controls = Controls()
    subscriber = messaging.sub_sock("carControl", timeout=1000)
    for phase, enabled, override, target in PHASES:
      for _ in range(20):
        host_observation = {}

        def published_control(state, timestamp, phase=phase, enabled=enabled, override=override, target=target,
                              host_observation=host_observation):
          inputs = []

          def message(service, size=None):
            msg = messaging.new_message(service, size, valid=True, logMonoTime=timestamp)
            inputs.append(msg)
            return getattr(msg, service)

          car_state = messaging.new_message("carState", valid=state.canValid, logMonoTime=timestamp)
          car_state.carState = state  # Actual parser result: do not overwrite health, speed, pedals or cruise.
          inputs.append(car_state)
          host = message("selfdriveState")
          host.enabled = host.active = enabled
          host.state = "enabled" if enabled else "disabled"
          parameters = message("vehicleParameters")
          parameters.stiffnessFactor, parameters.steerRatio = 1.0, fixture.cp.steerRatio
          message("longitudinalPlan").aTarget = target
          message("modelV2").action.desiredCurvature = 0.0001
          message("lateralDelay").lateralDelay = 0.1
          message("carOutput").actuatorsOutput = fixture.last_actuators
          events = message("onroadEvents", 1 if override else 0)
          if override:
            events[0].name = "gasPressedOverride"
            events[0].overrideLongitudinal = True
          controls.sm.update_msgs(timestamp / 1e9, [msg.as_reader() for msg in inputs])
          prior_acceleration = float(controls.LoC.last_output_accel)
          command, lateral_log = controls.state_control()
          publication_begin = int(time.monotonic() * 1e9)
          controls.publish(command, lateral_log)
          received = messaging.recv_one(subscriber)
          publication_end = int(time.monotonic() * 1e9)
          if received is None:
            raise RuntimeError("Native carControl publication was not received")
          emitted = received.carControl
          host_observation.update({"phase": phase, "plan_acceleration": target,
                                   "supplied_enabled": enabled, "supplied_override": override,
                                   "logical_frame_nanos": timestamp,
                                   "publication_begin": publication_begin, "publication_end": publication_end,
                                   "publication_log_mono_time": received.logMonoTime,
                                   "publication_valid": received.valid,
                                   "prior_host_acceleration": prior_acceleration,
                                   "host_published_command": command.to_dict(),
                                   "car_control_sha256": sha256_bytes(emitted.as_builder().to_bytes()),
                                   "car_control": emitted.to_dict(), "parsed_car_state": state.to_dict(),
                                   "host_car_state": controls.sm["carState"].to_dict()})
          return emitted  # Pass this actual IPC reader to interface.apply; no reconstructed command.

        record = fixture.step(control_callback=published_control)
        observations.append({"host": host_observation, "vehicle": record})
    platform = fixture.cp.carFingerprint
    firmware = FW_VERSIONS[platform][EPS][0]
    paths = ("openpilot/selfdrive/controls/controlsd.py", "openpilot/selfdrive/controls/lib/longcontrol.py",
             "openpilot/starpilot/tests/tesla_fixture.py", "openpilot/starpilot/tests/test_tesla_host_integration.py",
             "opendbc_repo/opendbc/car/tesla/interface.py", "opendbc_repo/opendbc/car/tesla/carcontroller.py",
             "opendbc_repo/opendbc/car/tesla/teslacan.py", "opendbc_repo/opendbc/car/tesla/carstate.py")
    report = {"scope": "synthetic host state_control/publish through native IPC, vehicle interface/controller, CAN and DEBUG C safety",
              "vehicle_qualified": False, "product_mode_qualified": False, "firmware_profile": profile,
              "platform": platform, "input_eps_firmware_hex": firmware.hex(), "flags": fixture.cp.flags,
              "safety_param": fixture.cp.safetyConfigs[0].safetyParam,
              "car_params_sha256": sha256_bytes(cp_bytes), "native": native, "safety_build": safety.provenance,
              "isolation": {"params_root": os.environ["PARAMS_ROOT"], "ipc_prefix": prefix,
                            "forbidden_flags_present": [name for name in FAKE_FLAGS if name in os.environ]},
              "sources": {path: sha256(Path(BASEDIR) / path) for path in paths},
              "primed": primed, "observations": observations}
    Path(output).write_text(json.dumps(report, indent=2) + "\n")
  finally:
    safety.close()


class TestTeslaHostIntegration(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.reports = []

  def check_profile(self, profile):
    ipc_root = "/tmp" if sys.platform == "darwin" else "/dev/shm"
    with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory(prefix="msgq_tesla-host-", dir=ipc_root) as ipc:
      output = Path(temporary) / "observations.json"
      environment = dict(os.environ, PARAMS_ROOT=str(Path(temporary) / "params"),
                         OPENPILOT_PREFIX=Path(ipc).name.removeprefix("msgq_"))
      # Exercise all inherited-flag removals; the child independently rejects any that survive.
      environment.update(dict.fromkeys(FAKE_FLAGS, "must-not-be-used"))
      for name in FAKE_FLAGS:
        environment.pop(name, None)
      command = [sys.executable, "-m", "openpilot.starpilot.tests.test_tesla_host_integration", "--probe",
                 "--profile", profile, "--output", str(output)]
      completed = subprocess.run(command, cwd=BASEDIR, env=environment, capture_output=True, text=True, timeout=30)
      self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
      report = json.loads(output.read_text())
    self.reports.append(report)
    self.assertEqual(report["isolation"]["forbidden_flags_present"], [])
    self.assertEqual(len(report["observations"]), 120)
    self.assertEqual([r["vehicle"]["frame"] for r in report["observations"]], list(range(30, 150)))
    self.assertEqual([r["host"]["phase"] for r in report["observations"]], [p[0] for p in PHASES for _ in range(20)])
    self.assertTrue(report["primed"]["can_valid"])
    self.assertTrue(report["primed"]["safety_config_valid"])
    for phase, enabled, override, _ in PHASES:
      rows = [row for row in report["observations"] if row["host"]["phase"] == phase]
      self.assertEqual(len(rows), 20)
      if override or phase == "disabled_positive_plan":
        self.assertGreater(rows[0]["host"]["prior_host_acceleration"], 0)
      steering, longitudinal = [], []
      for row in rows:
        with self.subTest(profile=profile, phase=phase, frame=row["vehicle"]["frame"]):
          host, vehicle = row["host"], row["vehicle"]
          cc = host["car_control"]
          self.assertEqual(host["car_control_sha256"], vehicle["car_control_sha256"])
          self.assertEqual(host["host_published_command"], cc)
          self.assertEqual(host["parsed_car_state"], host["host_car_state"])
          self.assertEqual(host["logical_frame_nanos"], vehicle["nanos"])
          self.assertGreaterEqual(host["publication_log_mono_time"], host["publication_begin"])
          self.assertLessEqual(host["publication_log_mono_time"], host["publication_end"])
          self.assertTrue(host["publication_valid"])
          self.assertTrue(vehicle["can_valid"])
          self.assertFalse(vehicle["can_timeout"])
          self.assertTrue(vehicle["safety_config_valid"])
          self.assertTrue(all(vehicle["rx_accepted"]))
          self.assertEqual(cc["enabled"], enabled)
          self.assertEqual(cc["latActive"], enabled)
          self.assertEqual(cc["longActive"], enabled and not override)
          self.assertEqual(cc["cruiseControl"]["cancel"], not enabled)
          self.assertEqual(cc["cruiseControl"]["override"], override)
          if enabled and not override:
            self.assertGreater(cc["actuators"]["accel"], 0)
          else:
            self.assertEqual(cc["actuators"]["accel"], 0)
          for emitted in vehicle["outgoing"]:
            self.assertTrue(emitted["decoded_fresh"])
            self.assertTrue(emitted["safety_tx_accepted"])
            if emitted["address"] == 0x488:
              steering.append(emitted)
              self.assertEqual(emitted["signals"]["DAS_steeringControlType"], 1 if enabled else 0)
            elif emitted["address"] == 0x2B9:
              longitudinal.append(emitted)
              signals = emitted["signals"]
              self.assertEqual(signals["DAS_accState"], 4 if enabled else 13)
              self.assertAlmostEqual(signals["DAS_accelMin"], cc["actuators"]["accel"], delta=0.021)
              self.assertAlmostEqual(signals["DAS_accelMax"], cc["actuators"]["accel"], delta=0.021)
      self.assertEqual(len(steering), 10)
      self.assertEqual(len(longitudinal), 5)
      for emitted, counter_name, modulus in ((steering, "DAS_steeringControlCounter", 16),
                                              (longitudinal, "DAS_controlCounter", 8)):
        counters = [message["signals"][counter_name] for message in emitted]
        self.assertEqual(counters[1:], [(value + 1) % modulus for value in counters[:-1]])
    # The sequence must remain continuous across host phase boundaries as well.
    for address, counter_name, modulus in ((0x488, "DAS_steeringControlCounter", 16), (0x2B9, "DAS_controlCounter", 8)):
      counters = [message["signals"][counter_name] for row in report["observations"] for message in row["vehicle"]["outgoing"]
                  if message["address"] == address]
      self.assertEqual(counters[1:], [(value + 1) % modulus for value in counters[:-1]])

  def test_marker489_host_command_reaches_can_unchanged(self):
    self.check_profile("marker489")

  def test_marker054_host_command_reaches_can_unchanged(self):
    self.check_profile("marker054")


if __name__ == "__main__":
  if "--probe" in sys.argv:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--profile", choices=("marker489", "marker054"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    probe(arguments.profile, arguments.output)
  else:
    unittest.main()
