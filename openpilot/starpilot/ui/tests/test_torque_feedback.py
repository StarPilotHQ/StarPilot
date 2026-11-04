"""Direction-preserving steering demand, output identity and unavailable sources."""

from copy import deepcopy
from types import SimpleNamespace as NS
import unittest

from openpilot.starpilot.ui.torque_feedback import observe_torque_feedback


DEFAULT_CP = NS(brand="test", maxLateralAccel=3.0)


class Messages(dict):
  def __init__(self, state="torqueState"):
    super().__init__({
      "carState": NS(canValid=True, canTimeout=False, vEgo=10.0),
      "carControl": NS(latActive=True, actuators=NS(torque=-0.9)),
      "controlsState": NS(lateralControlState=NS(which=lambda: state), curvature=0.002, desiredCurvature=0.004),
      "carOutput": NS(actuatorsOutput=NS(torque=0.25, torqueOutputCan=750)),
      "vehicleParameters": NS(valid=True, roll=0.01),
    })


def observe(sm, cp=DEFAULT_CP):
  return observe_torque_feedback(sm.get("carState"), sm.get("carControl"), sm.get("controlsState"),
                                 sm.get("carOutput"), sm.get("vehicleParameters"), cp)


class TorqueFeedbackTests(unittest.TestCase):
  def test_actual_normalized_output_not_request_or_raw_can(self):
    sm = Messages()
    self.assertEqual((observe(sm).utilization, observe(sm).source), (-0.25, "actuator_output"))
    sm["carControl"].actuators.torque = 0.95
    self.assertEqual(observe(sm).utilization, -0.25)
    sm["carOutput"].actuatorsOutput.torque = -0.7
    self.assertEqual(observe(sm).utilization, 0.7)
    sm["carOutput"].actuatorsOutput.torque = 0
    zero = observe(sm)
    self.assertEqual((zero.utilization, zero.source, zero.reason), (0, "actuator_output", "observed"))
    del sm["carOutput"]
    self.assertIsNone(observe(sm).utilization)

  def test_angle_demand_uses_configured_scale_without_roll_bias(self):
    cases = ((0, 0.0), (5, 0.1 / 3), (10, 0.4 / 3), (15, 0.9 / 3), (20, 1.6 / 3))
    sm = Messages("angleState")
    for speed, expected in cases:
      with self.subTest(speed=speed):
        sm["carState"].vEgo = speed
        result = observe(sm)
        self.assertAlmostEqual(result.utilization, expected)
        self.assertEqual(result.source, "lateral_acceleration_demand")
    sm["controlsState"].desiredCurvature = 0.05
    self.assertEqual(observe(sm).utilization, 1)
    sm["controlsState"].desiredCurvature = -0.05
    self.assertEqual(observe(sm).utilization, -1)
    sm["carControl"].latActive = False
    self.assertEqual((observe(sm).utilization, observe(sm).reason), (0, "lateral_inactive"))

  def test_angle_and_curvature_demand_preserve_direction_on_banked_roads(self):
    for state in ("angleState", "curvatureState"):
      for roll in (-0.15, -0.08, 0, 0.08, 0.15):
        for direction in (-1, 1):
          with self.subTest(state=state, roll=roll, direction=direction):
            sm = Messages(state)
            sm["vehicleParameters"].roll = roll
            sm["controlsState"].desiredCurvature = direction * 0.001
            sm["controlsState"].curvature = -direction * 0.002
            sm["carOutput"].actuatorsOutput.torque = -direction
            result = observe(sm, NS(maxLateralAccel=1.5))
            self.assertAlmostEqual(result.utilization, direction * .1 / 1.5)
            self.assertEqual(result.source, "lateral_acceleration_demand")

  def test_estimate_is_not_measured_torque_and_cp_fallback_is_explicit(self):
    sm = Messages("angleState")
    sm["carOutput"].actuatorsOutput.torque = -1
    self.assertAlmostEqual(observe(sm, None).utilization, 0.4 / 3)
    self.assertAlmostEqual(observe(sm, NS(maxLateralAccel=2)).utilization, 0.4 / 2)
    for maximum in (0, -1, float("nan"), float("inf"), True, None):
      self.assertIsNone(observe(sm, NS(maxLateralAccel=maximum)).utilization)
    self.assertIsNone(observe(sm, NS()).utilization)

  def test_absent_or_disqualified_sources_are_not_default_messages(self):
    for state, services in (("torqueState", ("carState", "controlsState", "carOutput")),
                            ("angleState", ("carState", "controlsState", "carControl", "vehicleParameters"))):
      for service in services:
        with self.subTest(state=state, service=service):
          sm = Messages(state)
          sm[service] = None
          self.assertIsNone(observe(sm).utilization)
    self.assertIsNone(observe({}).utilization)

  def test_invalid_payload_is_unavailable_not_zero(self):
    for changes in ({"canValid": False}, {"canTimeout": True}):
      sm = Messages()
      vars(sm["carState"]).update(changes)
      self.assertIsNone(observe(sm).utilization)
    for torque in (float("nan"), float("inf"), -1.001, 1.001, True, None):
      sm = Messages()
      sm["carOutput"].actuatorsOutput.torque = torque
      self.assertIsNone(observe(sm).utilization)
    for service, field, value in (("vehicleParameters", "valid", False), ("vehicleParameters", "roll", float("nan")),
                                  ("carState", "vEgo", -1), ("carState", "vEgo", 1e308),
                                  ("controlsState", "curvature", float("inf")),
                                  ("controlsState", "desiredCurvature", None)):
      sm = Messages("angleState")
      setattr(sm[service], field, value)
      self.assertIsNone(observe(sm).utilization)
    self.assertIsNone(observe(Messages("unknownState")).utilization)

  def test_reported_rivian_handoff_and_current_torque_only_schema(self):
    sm = Messages()
    cp = NS(brand="rivian", maxLateralAccel=3.0)
    self.assertEqual(observe(sm, cp).utilization, -0.25)
    output = sm["carOutput"].actuatorsOutput
    output.lateralControlMode = "angle"
    self.assertAlmostEqual(observe(sm, cp).utilization, 0.4 / 3)
    self.assertEqual(observe(sm, cp).source, "lateral_acceleration_demand")
    for mode in ("torque", "torqueRecovering", "inactive"):
      output.lateralControlMode = mode
      self.assertEqual(observe(sm, cp).source, "actuator_output")
    output.lateralControlMode = "angle"
    output.torque = 0
    self.assertEqual(observe(sm, cp).source, "lateral_acceleration_demand")
    self.assertEqual(observe(sm).source, "actuator_output", "other brands cannot inherit Rivian handoff semantics")
    sm["carOutput"] = None
    self.assertIsNone(observe(sm, cp).utilization)
    sm["carOutput"] = NS(actuatorsOutput=output)
    output.lateralControlMode = "newUnknownChannel"
    self.assertIsNone(observe(sm, cp).utilization)

  def test_read_only(self):
    sm = Messages("angleState")
    before = deepcopy(vars(sm)), repr(sm)
    observe(sm)
    self.assertEqual((vars(sm), repr(sm)), before)


if __name__ == "__main__":
  unittest.main()
