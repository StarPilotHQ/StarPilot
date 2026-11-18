"""Synthetic upstream Tesla contracts, not product-mode or vehicle qualification.

Inactive long input cases explicitly supply neutral acceleration, a host
prerequisite. The controller does not independently enforce that prerequisite.
Native C safety uses ALLOW_DEBUG, not a release firmware build. The stock cancel
case covers one edge only; sustained cancellation counter/receiver behavior is
an open requirement. Passing these tests does not qualify either product modes
or a physical vehicle.
"""
import unittest

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.fw_versions import match_fw_to_car
from opendbc.car.tesla.values import TeslaSafetyFlags
from openpilot.starpilot.tests.tesla_fixture import (
  DBC, EPS, FW_VERSIONS, MODERN_PLATFORMS, NativeSafety, TeslaFixture, car_params, messages,
)


class TestTeslaContract(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.safety = NativeSafety()
    cls.safety_build = cls.safety.provenance
    cls.observations = []
    cls.addClassCleanup(cls.safety.close)

  def fixture(self, **kwargs):
    fixture = TeslaFixture(self.safety, **kwargs)
    self.assert_healthy([fixture.prime()])
    self.assertTrue(self.safety.lib.get_controls_allowed())
    return fixture

  def assert_healthy(self, records):
    for record in records:
      self.assertTrue(record["can_valid"], record)
      self.assertFalse(record["can_timeout"], record)
      self.assertTrue(record["safety_config_valid"], record)
      self.assertTrue(all(record["rx_accepted"]), record)
      self.assertTrue(all(m["decoded_fresh"] for m in record["outgoing"]), record)

  def test_exact_firmware_classification_and_matching_safety_flag(self):
    for platform in MODERN_PLATFORMS:
      for version in FW_VERSIONS[platform][EPS]:
        with self.subTest(platform=platform, firmware=version):
          fw = structs.CarParams.CarFw(ecu=EPS[0], address=EPS[1], brand="tesla", fwVersion=version)
          exact, matches = match_fw_to_car([fw], "", log=False)
          self.assertTrue(exact)
          self.assertEqual(matches, {platform})
          cp = car_params(platform, firmware=version)
          self.assertFalse(cp.dashcamOnly)
          self.assertEqual(cp.safetyConfigs[0].safetyParam, int(TeslaSafetyFlags.LONG_CONTROL))
          for profile in ("marker489", "marker054"):
            marked = car_params(platform, profile=profile, firmware=version)
            self.assertFalse(marked.dashcamOnly)
          self.assertTrue(car_params(platform, profile="two_bit", firmware=version).dashcamOnly)
          self.assertTrue(cp.safetyConfigs[0].safetyParam & TeslaSafetyFlags.LONG_CONTROL)

  def test_unknown_firmware_identifies_no_candidate(self):
    fw = structs.CarParams.CarFw(ecu=EPS[0], address=EPS[1], brand="tesla", fwVersion=b"synthetic-unknown-EPS-response")
    _, matches = match_fw_to_car([fw], "", log=False)
    # The matcher returns (True, empty set) after exhausting both matching methods.
    self.assertEqual(matches, set())

  def check_axis_inputs(self, lateral, longitudinal):
    # All inactive-long cases satisfy the caller's neutralization prerequisite.
    for platform in MODERN_PLATFORMS:
      for profile, active_type in (("marker489", 1), ("marker054", 1)):
        with self.subTest(platform=platform, profile=profile, latActive=lateral, longActive=longitudinal):
          fixture = self.fixture(platform=platform, profile=profile)
          records = fixture.run(lateral=lateral, longitudinal=longitudinal, accel=1.0 if longitudinal else 0.0, angle=1.0)
          self.assert_healthy(records)
          steering, longitudinal_messages = messages(records, 0x488), messages(records, 0x2B9)
          self.assertEqual(len(steering), 10)
          self.assertEqual(len(longitudinal_messages), 5)
          # Timestamp freshness alone tolerates a few counter errors in the real parser.
          for emitted, counter_name, modulus in ((steering, "DAS_steeringControlCounter", 16),
                                                  (longitudinal_messages, "DAS_controlCounter", 8)):
            counters = [message["signals"][counter_name] for message in emitted]
            self.assertEqual(counters[1:], [(value + 1) % modulus for value in counters[:-1]])
          for message in steering:
            signals = message["signals"]
            self.assertEqual(signals["DAS_steeringControlType"], active_type if lateral else 0)
            self.assertAlmostEqual(signals["DAS_steeringAngleRequest"], -1.0 if lateral else 0.0, delta=0.051)
            self.assertTrue(message["safety_tx_accepted"])
          for message in longitudinal_messages:
            # Raw longitudinal fields are observations; state4/zero accel does not mean disabled.
            self.assertEqual(message["signals"]["DAS_accState"], 4)
            self.assertAlmostEqual(message["signals"]["DAS_accelMin"], 1.0 if longitudinal else 0.0, delta=0.021)
            self.assertAlmostEqual(message["signals"]["DAS_accelMax"], 1.0 if longitudinal else 0.0, delta=0.021)
            self.assertTrue(message["safety_tx_accepted"])
          self.observations.append({"platform": str(platform), "firmware_profile": profile,
                                    "caller_inactive_long_accel_neutralized": True, "records": records})

  def test_axis_inputs_lateral_false_longitudinal_false(self):
    self.check_axis_inputs(False, False)

  def test_axis_inputs_lateral_true_longitudinal_false(self):
    self.check_axis_inputs(True, False)

  def test_axis_inputs_lateral_false_longitudinal_true(self):
    self.check_axis_inputs(False, True)

  def test_axis_inputs_lateral_true_longitudinal_true(self):
    self.check_axis_inputs(True, True)

  def test_safety_rejects_other_firmware_active_steering_type(self):
    for profile, wrong_type in (("marker489", 2), ("marker054", 2)):
      with self.subTest(profile=profile):
        self.fixture(profile=profile)
        command = CANPacker(DBC).make_can_msg("DAS_steeringControl", 0,
                                            {"DAS_steeringControlType": wrong_type, "DAS_steeringAngleRequest": 0})
        self.assertFalse(self.safety.tx(command))

  def test_unknown_firmware_guard_latches_and_settings_branches_are_preserved(self):
    # Candidate is explicitly supplied to reach the downstream guard, not fingerprinted from unknown firmware.
    for profile, settings_present, autosteer, expected in (
      ("unknown", True, 0, False), ("marker489", True, 0, False), ("marker054", True, 0, False),
      ("marker054", True, 1, True), ("unknown", False, 0, False),
    ):
      with self.subTest(profile=profile, settings_present=settings_present, autosteer=autosteer):
        fixture = self.fixture(profile=profile, settings_present=settings_present)
        records = fixture.run(overrides={"DAS_settings": {"DAS_autosteerEnabled": autosteer},
                                        "DAS_steeringControl": {"DAS_steeringControlType": 1}})
        self.assert_healthy(records)
        self.assertEqual(records[-1]["invalid_lkas_setting"], expected)
        cleared = fixture.run()
        self.assertEqual(cleared[-1]["invalid_lkas_setting"], expected and not autosteer)

  def test_stock_lkas_parser_uses_firmware_specific_control_type(self):
    for profile, stock_type in (("marker489", 2), ("marker054", 2)):
      with self.subTest(profile=profile):
        fixture = self.fixture(profile=profile)
        records = fixture.run(overrides={"DAS_steeringControl": {"DAS_steeringControlType": stock_type}})
        self.assert_healthy(records)
        self.assertTrue(records[-1]["stock_lkas"])
        self.assertFalse(fixture.run()[-1]["stock_lkas"])

  def test_driver_steering_override_disables_command_and_revokes_permission(self):
    for profile in ("marker489", "marker054"):
      with self.subTest(profile=profile):
        fixture = self.fixture(profile=profile)
        fixture.run(lateral=True, angle=1)
        records = fixture.run(lateral=True, angle=1, overrides={"EPAS3S_sysStatus": {"EPAS3S_handsOnLevel": 3}})
        self.assert_healthy(records)
        self.assertTrue(records[-1]["steering_disengage"])
        self.assertFalse(records[-1]["controls_allowed"])
        steering = messages(records, 0x488)
        self.assertEqual(len(steering), 10)
        self.assertTrue(all(m["signals"]["DAS_steeringControlType"] == 0 for m in steering))

  def test_longitudinal_limits_and_brake_permission_are_independent_of_lateral_input(self):
    # 3.5 m/s² is encoded as 3.52 on the 0.04 m/s² native CAN lattice.
    for request, expected in ((100.0, 3.52), (-100.0, -3.48)):
      for lateral in (False, True):
        with self.subTest(request=request, latActive=lateral):
          fixture = self.fixture()
          records = fixture.run(lateral=lateral, longitudinal=True, accel=request)
          self.assert_healthy(records)
          longitudinal_messages = messages(records, 0x2B9)
          self.assertEqual(len(longitudinal_messages), 5)
          for message in longitudinal_messages:
            self.assertAlmostEqual(message["signals"]["DAS_accelMin"], expected, delta=0.021)
            self.assertGreaterEqual(message["signals"]["DAS_accelMax"], 0)
            self.assertTrue(message["safety_tx_accepted"])
          braking = fixture.run(lateral=lateral, longitudinal=True, accel=1,
                                overrides={"ESP_status": {"ESP_driverBrakeApply": 2}})
          self.assert_healthy(braking)
          self.assertTrue(braking[-1]["brake_pressed"])
          self.assertFalse(braking[-1]["longitudinal_allowed"])
          braking_messages = messages(braking, 0x2B9)
          self.assertEqual(len(braking_messages), 5)
          self.assertTrue(all(not m["safety_tx_accepted"] for m in braking_messages))

  def test_stock_longitudinal_single_cancel_edge_emits_neutral_command(self):
    for profile in ("marker489", "marker054"):
      with self.subTest(profile=profile):
        fixture = self.fixture(profile=profile, longitudinal=False)
        records = fixture.run(longitudinal=True, accel=1)
        self.assert_healthy(records)
        self.assertEqual(messages(records, 0x2B9), [])
        # One edge only: sustained cancellation counter cadence is a separate open requirement.
        records = fixture.run(frames=1, longitudinal=True, accel=1, cancel=True)
        self.assert_healthy(records)
        cancellation = messages(records, 0x2B9)
        self.assertEqual(len(cancellation), 1)
        for message in cancellation:
          self.assertEqual(message["signals"]["DAS_accState"], 13)
          self.assertAlmostEqual(message["signals"]["DAS_accelMin"], 0, delta=0.021)
          self.assertAlmostEqual(message["signals"]["DAS_accelMax"], 0, delta=0.021)
          self.assertTrue(message["safety_tx_accepted"])


if __name__ == "__main__":
  unittest.main()
