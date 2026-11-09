"""Unchanged native output contracts using declared unit fixtures."""

from dataclasses import replace
import unittest


class TestOffroadEqualNativeOutput(unittest.TestCase):
  @staticmethod
  def boundary(platform, *, unwind=False):
    from openpilot.starpilot.flm.tests.test_steering_projection import make_interface, control, apply, settle, install_observer, check_packet
    from openpilot.starpilot.flm.torque_emission_replay import Emission

    ci = make_interface(platform)
    assert install_observer(ci)
    initial = settle(ci, control(0.0))[-1][2]
    limits = dict(initial.limits)
    step = limits['STEER_DELTA_DOWN' if unwind else 'STEER_DELTA_UP']
    if unwind:
      settle(ci, control(4 * step / initial.effective_max))
    target = (3 if unwind else 2) * step
    for _ in range(24):
      _, packets, witness = apply(ci, control(target / initial.effective_max))
      if witness is None:
        continue
      if (witness.unlimited_raw == witness.final_raw == target and
          witness.final_raw - witness.previous_torque == (-step if unwind else step)):
        check_packet(ci, packets, witness)
        stamp = 10_000_000_000 + witness.frame * 10_000_000
        return Emission(stamp, stamp, stamp + 1_000_000, witness, 'a' * 64, 'b' * 64, 'c' * 64,
                        True, True, False, False, False, False, False, 0, 0, '')
    raise AssertionError('Actual registered encoder did not provide the unclipped rate boundary')

  def test_exact_same_request_uses_candidate_native_prestate(self):
    from opendbc.car.gm.values import CAR as GM
    from opendbc.car.hyundai.values import CAR as HYUNDAI
    from opendbc.car.toyota.values import CAR as TOYOTA
    from openpilot.starpilot.flm.torque_emission_replay import EncoderBranch

    for platform in (GM.GMC_ACADIA, HYUNDAI.HYUNDAI_IONIQ_6, TOYOTA.TOYOTA_COROLLA_TSS2):
      with self.subTest(platform=platform):
        event = self.boundary(platform)
        w = event.witness
        original = EncoderBranch()
        self.assertEqual(original.step(event, w.requested_torque, actual=True), w.final_raw / w.effective_max)
        strict = EncoderBranch()
        strict.previous = w.previous_torque - 1
        with self.assertRaisesRegex(ValueError, 'limiter/support'):
          strict.step(event, w.requested_torque)
        candidate = EncoderBranch(allow_unclipped_equal_output=True)
        candidate.previous = w.previous_torque - 1
        self.assertEqual(candidate.step(event, w.requested_torque), (w.final_raw - 1) / w.effective_max)
        self.assertEqual(candidate.previous, w.final_raw - 1)
        self.assertFalse(event.limited)
        self.assertEqual(event.witness, w)

  def test_native_unwind_cap_is_not_an_unsafe_request(self):
    from opendbc.car.hyundai.values import CAR
    from openpilot.starpilot.flm.torque_emission_replay import EncoderBranch

    event = self.boundary(CAR.HYUNDAI_IONIQ_6, unwind=True)
    w = event.witness
    request = (w.unlimited_raw - 1) / w.effective_max
    with self.assertRaisesRegex(ValueError, 'limiter/support'):
      EncoderBranch().step(event, request)
    branch = EncoderBranch(allow_unclipped_equal_output=True)
    self.assertEqual(branch.step(event, request), w.final_raw / w.effective_max)
    self.assertEqual(branch.previous, w.final_raw)
    for unsafe in ((w.unlimited_raw + 1) / w.effective_max, -w.requested_torque):
      candidate = EncoderBranch(allow_unclipped_equal_output=True)
      candidate.previous = w.previous_torque + 100
      with self.assertRaises(ValueError):
        candidate.step(event, unsafe)

  def test_equal_output_does_not_relax_original_or_legacy_quality(self):
    from opendbc.car.gm.values import CAR as GM
    from opendbc.car.hyundai.values import CAR as HYUNDAI
    from opendbc.car.toyota.values import CAR as TOYOTA
    from openpilot.starpilot.flm.torque_emission_replay import EncoderBranch
    from openpilot.starpilot.flm.tests import test_state_aligned_encoder as legacy

    event = self.boundary(HYUNDAI.HYUNDAI_IONIQ_6)
    branch = EncoderBranch(allow_unclipped_equal_output=True)
    branch.previous = event.witness.previous_torque - 1
    with self.assertRaisesRegex(ValueError, 'actual previous'):
      branch.step(event, event.witness.requested_torque, actual=True)
    clipped = legacy.captured_event(TOYOTA.TOYOTA_COROLLA_TSS2)
    with self.assertRaisesRegex(ValueError, 'limiter/support'):
      EncoderBranch(allow_unclipped_equal_output=True).step(replace(clipped, limited=False), clipped.witness.requested_torque)
    for platform in (TOYOTA.TOYOTA_COROLLA_TSS2, GM.GMC_ACADIA):
      legacy.test_registered_measured_driver_same_request_shadow_and_increased_demand(platform)
    legacy.test_common_state_unlimited_reference_recorded_opposite_and_original_unlimited_vetoes()
    legacy.test_unsupported_paired_projection_fails_without_advancing_candidate_native_state()
    legacy.test_honda_normalized_pre_lookup_comparison_and_modified_filter_shadow_is_pure()
    legacy.test_honda_nonfinite_reference_and_paired_original_pressed_fail_closed()


if __name__ == '__main__':
  unittest.main()
