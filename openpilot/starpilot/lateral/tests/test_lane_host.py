"""Actual host curvature selection, limiter and native controllers on supplied messages."""

import argparse
from dataclasses import asdict, replace
import json
import math
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.basedir import BASEDIR


def probe(output, reference_method=None):
  if not os.environ.get("OPENPILOT_PREFIX", "").startswith("lane-host-") or any(
    name in os.environ for name in ("CEREAL_FAKE", "CEREAL_FAKE_PREFIX", "SIMULATION")
  ):
    raise RuntimeError("Native lane-host probe requires isolated IPC without simulation flags")
  from openpilot.cereal import messaging
  from openpilot.common.constants import ACCELERATION_DUE_TO_GRAVITY
  from openpilot.common.realtime import DT_CTRL
  from openpilot.common.params import Params
  from openpilot.selfdrive.controls.controlsd import Controls
  from openpilot.selfdrive.controls.lib.drive_helpers import (MAX_CURVATURE, MAX_LATERAL_ACCEL_NO_ROLL,
                                                            MAX_LATERAL_JERK, MIN_SPEED, clip_curvature)
  from openpilot.starpilot.lateral.lane_clearance import current_delay, limit_tracking
  from openpilot.starpilot.lateral.lane_tracking import SingleLineTracker
  from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
  from openpilot.selfdrive.controls.lib.longcontrol import LongControl
  from openpilot.selfdrive.controls.lib.highway_correction_gain import HighwayCorrectionGain, read_gain
  from openpilot.starpilot.lateral.controller_selection import turn_assist_supported
  from openpilot.starpilot.longitudinal.ioniq6_start import eligible as ioniq6_start_eligible
  from openpilot.starpilot.longitudinal.tests.extension_helpers import attach_inputs
  from openpilot.starpilot.lateral.lane_centering import ControlMode, LaneCenteringController, LaneCenteringRequest, LaneCenteringSettings
  from openpilot.starpilot.lateral.tests.test_lane_centering import model
  from openpilot.starpilot.lateral.lane_change_preferences import LiveLaneChangePolicy
  from openpilot.starpilot.lateral.lane_change_smoothing import LaneChangeSmoother
  from opendbc.car.car_helpers import interfaces
  from opendbc.car.toyota.values import CAR
  from opendbc.car.vehicle_model import VehicleModel

  def host():
    # Allocate just the state_control dependencies. Native controllers and SubMaster
    # are independent; Params, publication, pose calibration and manager are not run.
    control = Controls.__new__(Controls)
    from openpilot.starpilot.lateral.pause import LateralPause, PauseSettings
    control.lateral_pause = LateralPause(PauseSettings())
    attach_inputs(control)
    control.lateral_gain_owner = None
    control.aol_replay = False
    interface = interfaces[CAR.TOYOTA_COROLLA_TSS2]
    control.CP = interface.get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2).as_reader()
    control.longitudinal_inputs.ioniq6_start_enabled = ioniq6_start_eligible(control.CP)
    control.CI = interface(control.CP)
    control.VM = VehicleModel(control.CP)
    control.LaC = LatControlTorque(control.CP, control.CI, DT_CTRL)
    control.LoC = LongControl(control.CP)
    control.sm = messaging.SubMaster(["vehicleParameters", "lateralTorqueParameters", "carState", "selfdriveState",
                                     "longitudinalPlan", "modelV2", "lateralManeuverPlan", "lateralDelay", "onroadEvents"],
                                    frequency=100)
    control.curvature = control.desired_curvature = 0.0
    control.steer_limited_by_safety = False
    control.lane_centering_controller = LaneCenteringController()
    control.params = Params()
    control.highway_correction_gain = HighwayCorrectionGain()
    control.highway_smoothing_supported = turn_assist_supported(control.CP)
    control.highway_gain = read_gain(control.params) if control.highway_smoothing_supported else 1.0
    control.lane_change_preferences = LiveLaneChangePolicy(control.params, now_ns=1_000_000_000)
    control.lane_change_policy = control.lane_change_preferences.applied
    control.lane_change_smoother = LaneChangeSmoother()
    control.torque_host = None  # Explicit feature-off state for the manually constructed Controls fixture.
    control.torque_learning_allowed = False
    control.longitudinal_inputs.gm_start_enabled = False
    control.longitudinal_inputs.gm_volt_enabled = False
    control.longitudinal_inputs.toyota_sienna_replay = False
    control.lane_centering_host = None
    control.last_lane_centering_result = None
    return control

  request = LaneCenteringRequest(ControlMode.COMBINED, LaneCenteringSettings(True, 0.0, 0.0, True), DT_CTRL)
  changes = ("disabled_setting", "off", "long_only", "lateral_only", "fault", "disabled_host", "override", "signal",
             "invalid_model", "stale_model", "clock_reset", "absent_request", "invalid_request", "maneuver")
  records = []

  def input_bytes(control):
    return {name: tuple(event.as_builder().to_bytes() for event in control.sm[name]) if name == "onroadEvents"
            else control.sm[name].as_builder().to_bytes() for name in control.sm.services}

  for change in changes:
    candidate, reference = host(), host()
    tracking_reference = SingleLineTracker()
    previous_correction = 0.0
    # Each gate starts with positive acquired state; absence/disabled equivalence
    # starts disabled from the beginning to compare all controller history exactly.
    for tick in range(140):
      phase = "warm" if tick < 80 else change
      timestamp = 1_000_000_000 + tick * 10_000_000
      inputs = []

      def message(service, size=None, valid=True, timestamp=timestamp, inputs=inputs):
        msg = messaging.new_message(service, size, valid=valid, logMonoTime=timestamp)
        inputs.append(msg)
        return getattr(msg, service)

      cs = message("carState")
      cs.vEgo = cs.vEgoRaw = 20.0
      cs.vCruise = cs.vCruiseCluster = 100.0
      cs.canValid = True
      cs.steerFaultTemporary = phase == "fault"
      cs.steeringPressed = phase == "override"
      cs.leftBlinker = phase == "signal"
      state = message("selfdriveState")
      state.enabled = state.active = phase != "disabled_host"
      state.state = "enabled" if state.enabled else "disabled"
      lp = message("vehicleParameters")
      lp.stiffnessFactor, lp.steerRatio = 1.0, candidate.CP.steerRatio
      message("longitudinalPlan").aTarget = 0.5
      if tick % 25 == 0:  # lateralDelay is a 4 Hz service, not a control-rate packet.
        message("lateralDelay").lateralDelay = 0.1
      message("onroadEvents", 0)
      if tick % 5 == 0 and phase != "stale_model":
        event = messaging.new_message("modelV2", valid=phase != "invalid_model", logMonoTime=timestamp)
        event.modelV2 = model()
        event.modelV2.action.desiredCurvature = 0.0002
        inputs.append(event)
      if phase == "maneuver":
        message("lateralManeuverPlan").desiredCurvature = -0.0004
      readers = [msg.as_reader() for msg in inputs]
      candidate.sm.update_msgs(timestamp / 1e9, readers)
      reference.sm.update_msgs(timestamp / 1e9, readers)
      before = input_bytes(candidate)
      configured = request
      if change == "disabled_setting":
        configured = replace(request, settings=replace(request.settings, enabled=False))
      elif phase == "off":
        configured = replace(request, mode=ControlMode.OFF)
      elif phase == "long_only":
        configured = replace(request, mode=ControlMode.LONGITUDINAL_ONLY)
      elif phase == "lateral_only":
        configured = replace(request, mode=ControlMode.LATERAL_ONLY)
      elif phase == "clock_reset":
        configured = replace(request, time_discontinuity=True)
      elif phase == "absent_request":
        configured = None
      elif phase == "invalid_request":
        configured = "invalid"
      prior_curvature = candidate.desired_curvature
      # Supplied packet times and the native freshness clock share one epoch.
      with patch("openpilot.selfdrive.controls.controlsd.time.monotonic_ns", return_value=timestamp):
        command, lateral = candidate.state_control(lane_centering=configured)
        base_command, _ = reference.state_control() if reference_method is None else reference_method(reference)
      after = input_bytes(candidate)
      if before != after:
        raise AssertionError("Host changed its supplied messages")
      result = candidate.last_lane_centering_result
      delay = current_delay(candidate.sm, timestamp)
      if tick >= 25 and delay != candidate.sm["lateralDelay"].lateralDelay:
        raise AssertionError("Fresh native delay failed qualification")
      # Track the supplied geometry independently, including source-gap expiry
      # and signal release, without reading or mutating the host's tracker.
      if (isinstance(configured, LaneCenteringRequest) and configured.settings.enabled and configured.mode.lateral_authority and
          command.latActive and candidate.sm.all_checks(["modelV2"]) and not cs.steeringPressed and
          not configured.time_discontinuity and not cs.leftBlinker):
        tracking_reference.target(candidate.sm["modelV2"], candidate.sm.logMonoTime["modelV2"], cs.vEgo, DT_CTRL, 0.0)
      else:
        tracking_reference.reset()
      # These fixtures never request lane-change shaping: the native clip is
      # followed only by the two-line tracking guard, which may shrink its delta.
      if candidate.lane_change_smoother.previous_factor != 1.0:
        raise AssertionError("Unexpected lane-change shaping in the fixture")
      speed_squared = max(cs.vEgo, MIN_SPEED) ** 2
      curvature = candidate.desired_curvature
      if (not math.isfinite(curvature) or abs(curvature) > MAX_CURVATURE + 1e-12 or
          abs(curvature - prior_curvature) > MAX_LATERAL_JERK / speed_squared * DT_CTRL + 1e-12 or
          abs(curvature * speed_squared - lp.roll * ACCELERATION_DUE_TO_GRAVITY) > MAX_LATERAL_ACCEL_NO_ROLL + 1e-12):
        raise AssertionError("Final curvature exceeded independent native bounds")
      if result is not None and result.candidate_curvature is not None:
        clipped, _ = clip_curvature(cs.vEgo, prior_curvature, result.candidate_curvature, lp.roll)
        expected = clipped
        if result.correction != 0.0:
          current_model = candidate.sm["modelV2"]
          baseline_input = (candidate.sm["lateralManeuverPlan"].desiredCurvature
                            if candidate.sm.valid["lateralManeuverPlan"] else current_model.action.desiredCurvature)
          if not command.latActive:
            baseline_input = 0.0
          if not math.isclose(result.candidate_curvature - result.correction, baseline_input, abs_tol=1e-12):
            raise AssertionError("Lane correction changed its native baseline")
          without_lane, _ = clip_curvature(cs.vEgo, prior_curvature, baseline_input, lp.roll)
          # Apply the pure guard to independently tracked input geometry;
          # invoking guard_applied again would mutate the acquired correction.
          allowed = clipped - without_lane
          if tracking_reference.path is not None:
            allowed = (0.0 if delay is None else
                       limit_tracking(tracking_reference.path, without_lane, allowed, 0.0, cs.vEgo, delay))
          expected = without_lane + allowed
          if not min(without_lane, clipped) - 1e-12 <= expected <= max(without_lane, clipped) + 1e-12:
            raise AssertionError("Tracking guard increased or reversed the native contribution")
          if not math.isclose(candidate.lane_centering_applied, curvature - without_lane, abs_tol=1e-12):
            raise AssertionError("Lane feedback did not reflect the final command limiter")
        published_precision = struct.unpack("f", struct.pack("f", expected))[0]
        if curvature != expected or command.actuators.curvature != published_precision:
          raise AssertionError(f"Lane candidate bypassed native clipping or tracking guard: {change=} {tick=} {curvature=} {expected=}")
      if (result is None or result.correction == 0.0) and candidate.lane_centering_applied != 0.0:
        raise AssertionError("Inactive lane contribution remained visible")
      records.append({"scenario": change, "phase": phase, "tick": tick,
                      "model_sample": candidate.sm.logMonoTime["modelV2"], "model_checks": candidate.sm.all_checks(["modelV2"]),
                      "result": asdict(result) if result is not None else None, "previous_correction": previous_correction,
                      "host": command.to_dict(), "reference": base_command.to_dict(), "controller_active": lateral.active})
      previous_correction = result.correction if result is not None else 0.0
  Path(output).write_text(json.dumps({"scope": "Native host/controller software composition; supplied authority, no vehicle qualification",
                                    "records": records}, indent=2) + "\n")


class TestLaneHost(unittest.TestCase):
  def test_native_host_gates_limiter_and_unchanged_longitudinal_path(self):
    ipc_root = "/tmp" if sys.platform == "darwin" else "/dev/shm"
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory(prefix="msgq_lane-host-", dir=ipc_root) as ipc:
      output = Path(tmp) / "observations.json"
      env = dict(os.environ, OPENPILOT_PREFIX=Path(ipc).name.removeprefix("msgq_"))
      for name in ("CEREAL_FAKE", "CEREAL_FAKE_PREFIX", "SIMULATION"):
        env.pop(name, None)
      run = subprocess.run([sys.executable, "-m", "openpilot.starpilot.lateral.tests.test_lane_host", "--probe", str(output)],
                           cwd=BASEDIR, env=env, capture_output=True, text=True, timeout=30)
      self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
      records = json.loads(output.read_text())["records"]
    self.assertEqual(len(records), 1960)
    for row in records:
      phase, result, command, reference = row["phase"], row["result"], row["host"], row["reference"]
      with self.subTest(scenario=row["scenario"], tick=row["tick"]):
        for field in ("enabled", "latActive", "longActive"):
          self.assertEqual(command[field], reference[field])
        self.assertEqual(command["actuators"]["accel"], reference["actuators"]["accel"])
        self.assertTrue(math.isfinite(command["actuators"]["torque"]))
        if row["scenario"] == "disabled_setting":
          self.assertEqual(command, reference)
        elif phase == "warm" and row["tick"] >= 30:
          # Two fresh delay packets qualify the guard; its correction may settle.
          self.assertGreater(result["correction"], 0.0)
          self.assertGreater(command["actuators"]["curvature"], reference["actuators"]["curvature"])
        elif phase in ("off", "long_only", "fault", "disabled_host", "override", "invalid_model", "clock_reset", "invalid_request"):
          self.assertEqual(result["correction"], 0.0)
        elif phase == "absent_request":
          self.assertIsNone(result)
        elif phase == "signal":
          self.assertLess(result["correction"], row["previous_correction"])
        elif phase == "stale_model" and not row["model_checks"]:
          self.assertEqual(result["correction"], 0.0)
        elif phase == "maneuver":
          self.assertAlmostEqual(result["candidate_curvature"] - result["correction"], -0.0004, places=9)
        if phase in ("fault", "disabled_host"):
          self.assertFalse(command["latActive"])
          self.assertEqual(command["actuators"]["torque"], 0.0)
    stale = [row for row in records if row["phase"] == "stale_model"]
    self.assertTrue(any(row["model_checks"] for row in stale))
    self.assertTrue(any(not row["model_checks"] for row in stale))
    held = [row for row in records if row["scenario"] == "lateral_only" and row["tick"] in (31, 32)]
    self.assertEqual(held[0]["model_sample"], held[1]["model_sample"])
    self.assertGreater(held[1]["result"]["correction"], held[0]["result"]["correction"])


if __name__ == "__main__":
  if "--probe" in sys.argv:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    probe(parser.parse_args().probe)
  else:
    unittest.main()


def run_tracking_plant(side, road_curvature, enabled):
  import numpy as np
  from openpilot.starpilot.lateral import lane_centering as lane
  from openpilot.starpilot.lateral.lane_clearance import BodyDimensions
  from openpilot.starpilot.lateral.tests import test_lane_centering as fixtures
  from openpilot.selfdrive.controls.lib.drive_helpers import clip_curvature as clip

  controller = lane.LaneCenteringController(body=BodyDimensions(1.88, 4.855))
  speed, dt = 20.0, 0.01
  y = heading = previous = 0.0
  measured = road_curvature
  delayed = [road_curvature] * 10
  samples = []
  for tick in range(1200):
    if tick % 5 == 0:
      nominal = road_curvature - 0.001 * (y - (-0.6 if side == 0 else 0.6)) - 0.08 * heading
      m = fixtures.model(path_std=0.02)
      for index, intercept in enumerate((-5.0, -1.8, 1.8, 5.0)):
        m.laneLines[index].y = [intercept - y - heading * x + 0.5 * road_curvature * x * x for x in fixtures.XS]
      m.position.y = [0.5 * nominal * x * x for x in fixtures.XS]
      m.laneLineProbs = [0.0, 0.95 if tick < 100 or side == 0 else 0.05, 0.95 if tick < 100 or side == 1 else 0.05, 0.0]
      m.init('roadEdges', 2)
      for edge, intercept in zip(m.roadEdges, (-5.0, 5.0), strict=True):
        edge.x = fixtures.XS
        edge.y = [intercept - y - heading * x + 0.5 * road_curvature * x * x for x in fixtures.XS]
      m.roadEdgeStds = [0.1, 0.1]
      m.action.desiredCurvature = nominal
      model = m.as_reader()
      stamp = 1_000_000_000 + tick * 10_000_000
    nominal = model.action.desiredCurvature
    desired = nominal
    if enabled:
      result = controller.update(
        lane.LaneCenteringInput(
          model,
          stamp,
          True,
          nominal,
          speed,
          lane.ControlMode.LATERAL_ONLY,
          True,
          lane.LaneCenteringSettings(True, 0.0, 0.0, True),
          dt,
          measured_curvature=measured,
          lateral_delay_s=0.1,
        )
      )
      assert result.candidate_curvature is not None
      desired = result.candidate_curvature
    command, _ = clip(speed, previous, desired, 0.0)
    baseline, _ = clip(speed, previous, nominal, 0.0)
    if enabled:
      command = controller.guard_applied(model, baseline, command, measured, speed, 0.1)
    assert abs(command) <= 3.0 / speed**2 + 1e-12
    assert abs(command - previous) <= 5.0 / speed**2 * dt + 1e-12
    previous = command
    delayed.append(command)
    measured += (delayed.pop(0) - measured) * dt / 0.1
    road_speed = speed * math.cos(heading) / (1 - road_curvature * y)
    y += speed * math.sin(heading) * dt
    heading += (speed * measured - road_curvature * road_speed) * dt
    samples.append((y, heading, command))
  return {
    'meanFinalAbsOffset': float(np.mean([abs(r[0]) for r in samples[-200:]])),
    'minimumLineClearance': min(1.8 - abs(r[0]) for r in samples),
    'maxAbsHeading': max(abs(r[1]) for r in samples),
    'finalOffset': y,
  }



class TestLaneTrackingPlant(unittest.TestCase):
  def test_single_line_tracking_reduces_bias_with_kinematic_feedback(self):
    for side in (0, 1):
      for curvature in (0., .004, -.004):
        with self.subTest(side=side, curvature=curvature):
          original = run_tracking_plant(side, curvature, False)
          candidate = run_tracking_plant(side, curvature, True)
          self.assertLess(candidate['meanFinalAbsOffset'], original['meanFinalAbsOffset'] * .8)
          self.assertGreater(candidate['minimumLineClearance'], original['minimumLineClearance'])
          self.assertLessEqual(candidate['maxAbsHeading'], original['maxAbsHeading'] + .001)
