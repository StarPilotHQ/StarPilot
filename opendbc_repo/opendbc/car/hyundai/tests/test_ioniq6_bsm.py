import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from opendbc.can import CANPacker
from opendbc.car import Bus, CanData, gen_empty_fingerprint, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.hyundaicanfd import create_ioniq6_blindspot_status, hkg_can_fd_checksum
from opendbc.car.hyundai.ioniq6_bsm import BlindspotStatus, Ioniq6BlindspotSources
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.ioniq6_handoff import build_ioniq6_hda2_long_candidate
from opendbc.car.hyundai.values import CAR, DBC


CASES = json.loads((Path(__file__).parent / "testdata" / "ioniq6_bsm_frozen.json").read_text())["cases"]


def long_params():
  fingerprint = gen_empty_fingerprint()
  fingerprint[2].update({0x50: 16, 0x2A4: 24})
  fingerprint[1].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24,
                         0x1BA: 24, 0x1E5: 16, 0x36A: 16})
  fingerprint[0][0x3A5] = 24
  stock = CarInterface.get_params(CAR.HYUNDAI_IONIQ_6, fingerprint, [], False, False, False)
  candidate = build_ioniq6_hda2_long_candidate(stock, fingerprint)
  assert candidate is not None
  return candidate


class TestIoniq6BlindspotStatus(unittest.TestCase):
  def test_all_sixteen_reference_outputs_without_overlapping_sound_alias(self):
    for case in CASES:
      with self.subTest(case=case):
        left = 1 + int(case["leftBlinker"]) if case["left"] else 0
        right = 1 + int(case["rightBlinker"]) if case["right"] else 0
        frames = create_ioniq6_blindspot_status(case["counter"], BlindspotStatus(left, right))
        self.assertEqual([(addr, bus) for addr, _, bus in frames], [(0x1BA, 1), (0x1E5, 1)])
        expected_rear = bytearray.fromhex(case["rear"])
        expected_rear[5] = 0  # Retired FL_INDICATOR alias overlaps BCW_RtSndWrngSta.
        expected_rear[:2] = hkg_can_fd_checksum(0x1BA, None, expected_rear).to_bytes(2, "little")
        self.assertEqual([payload.hex() for _, payload, _ in frames], [expected_rear.hex(), case["front"]])
        rear = frames[0][1]
        sound_word = int.from_bytes(rear, "little")
        self.assertEqual((sound_word >> 34) & 0x3, 0)
        self.assertEqual((sound_word >> 36) & 0x3ff, 0)
        self.assertEqual(rear[16] & 3, left)
        self.assertEqual((rear[16] >> 2) & 0x3f, right)
        self.assertEqual((rear[3] >> 6) & 3, left)
        self.assertEqual(rear[4] & 3, right)
        for addr, payload, _ in frames:
          self.assertEqual(int.from_bytes(payload[:2], "little"), hkg_can_fd_checksum(addr, None, bytearray(payload)))

  def test_fresh_corner_and_lamp_holding_boundary(self):
    sources = Ioniq6BlindspotSources()
    t = 1_000_000_000
    self.assertIsNone(sources.status(t))
    sources.observe(corner_source_ns=t, corner_state=0x18, lamp_source_ns=t, left_lamp=True, right_lamp=False)
    self.assertEqual(sources.status(t), BlindspotStatus(2, 1))
    sources.observe(corner_source_ns=t + 100_000_000, corner_state=0x18,
                    lamp_source_ns=t + 100_000_000, left_lamp=False, right_lamp=False)
    self.assertEqual(sources.status(t + 100_000_000), BlindspotStatus(2, 1))
    # Repeated false lamp frames must not restart the hold.
    sources.observe(corner_source_ns=t + 590_000_000, corner_state=0x18,
                    lamp_source_ns=t + 590_000_000, left_lamp=False, right_lamp=False)
    self.assertEqual(sources.status(t + 600_000_000), BlindspotStatus(2, 1))
    self.assertEqual(sources.status(t + 600_000_001), BlindspotStatus(1, 1))
    self.assertIsNone(sources.status(t + 690_000_001))  # corner expiry
    sources.observe(corner_source_ns=t + 700_000_000, corner_state=0x18,
                    lamp_source_ns=t + 700_000_000, left_lamp=False, right_lamp=False)
    sources.observe(corner_source_ns=t + 2_200_000_001, corner_state=0x18,
                    lamp_source_ns=t + 700_000_000, left_lamp=False, right_lamp=False)
    self.assertIsNone(sources.status(t + 2_200_000_001))  # lamp expiry

  def test_live_lamp_does_not_expire_before_its_off_transition(self):
    sources = Ioniq6BlindspotSources()
    t = 1_000_000_000
    sources.observe(corner_source_ns=t, corner_state=0x10, lamp_source_ns=t, left_lamp=True, right_lamp=False)
    sources.observe(corner_source_ns=t + 600_000_000, corner_state=0x10,
                    lamp_source_ns=t + 600_000_000, left_lamp=True, right_lamp=False)
    self.assertEqual(sources.status(t + 600_000_000), BlindspotStatus(2, 0))
    sources.observe(corner_source_ns=t + 700_000_000, corner_state=0x10,
                    lamp_source_ns=t + 700_000_000, left_lamp=False, right_lamp=False)
    self.assertEqual(sources.status(t + 700_000_000), BlindspotStatus(2, 0))
    sources.observe(corner_source_ns=t + 1_200_000_001, corner_state=0x10,
                    lamp_source_ns=t + 1_200_000_001, left_lamp=False, right_lamp=False)
    self.assertEqual(sources.status(t + 1_200_000_001), BlindspotStatus(1, 0))

  def test_stale_lamp_gap_cannot_create_a_new_hold(self):
    sources = Ioniq6BlindspotSources()
    t = 1_000_000_000
    sources.observe(corner_source_ns=t, corner_state=0x10, lamp_source_ns=t, left_lamp=True, right_lamp=False)
    self.assertEqual(sources.status(t), BlindspotStatus(2, 0))
    self.assertIsNone(sources.status(t + 1_500_000_001))
    sources.observe(corner_source_ns=t + 2_000_000_000, corner_state=0x10,
                    lamp_source_ns=t + 2_000_000_000, left_lamp=False, right_lamp=False)
    self.assertEqual(sources.status(t + 2_000_000_000), BlindspotStatus(1, 0))

  def test_actual_parser_dynamic_alt_lamp_and_checksum_freshness(self):
    cp = long_params()
    ci = CarInterface(cp)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    t = 1_000_000_000
    corner = packer.make_can_msg("BLINDSPOTS_FRONT_CORNER_2", 1, {"SIDE_DETECT_STATE": 0x10})
    alt_lamp = packer.make_can_msg("BLINKERS", 1, {"USE_ALT_LAMP": 1, "LEFT_LAMP_ALT": 1})
    ci.update([(t, [corner, alt_lamp])])
    self.assertTrue(ci.CS.out.leftBlinker)
    self.assertEqual(ci.CS.ioniq6_bsm_sources.status(t), BlindspotStatus(2, 0))
    corrupted = bytearray(corner[1])
    corrupted[0] ^= 1
    ci.update([(t + 101_000_000, [CanData(corner[0], bytes(corrupted), corner[2])])])
    self.assertIsNone(ci.CS.ioniq6_bsm_sources.status(t + 101_000_000))

  def test_controller_emits_only_fresh_paired_status_in_exact_long_profile(self):
    cp = long_params()
    ci = CarInterface(cp)
    controller = CarController(DBC[cp.carFingerprint], cp)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    t = 10_000_000_000  # CAN BOOTTIME; controller below sees MONOTONIC.
    ci.update([(t, [packer.make_can_msg("BLINDSPOTS_FRONT_CORNER_2", 1, {"SIDE_DETECT_STATE": 0x10}),
                    packer.make_can_msg("BLINKERS", 1, {})])])
    ci.CS.out.vEgoRaw = 10.0
    control = structs.CarControl()
    _, frames = controller.update(control.as_reader(), ci.CS, t - 9_000_000_000)
    rear_front = [(addr, payload, bus) for addr, payload, bus in frames if addr in (0x1BA, 0x1E5)]
    self.assertEqual(rear_front, create_ioniq6_blindspot_status(0, BlindspotStatus(1, 0)))
    for i in range(1, 5):
      controller.update(control.as_reader(), ci.CS, t - 9_000_000_000 + i * 10_000_000)
    ci.update([(t + 50_000_000, [packer.make_can_msg("BLINDSPOTS_FRONT_CORNER_2", 1, {"SIDE_DETECT_STATE": 0x10}),
                               packer.make_can_msg("BLINKERS", 1, {})])])
    ci.CS.out.vEgoRaw = 10.0
    _, next_frames = controller.update(control.as_reader(), ci.CS, t - 9_000_000_000 + 50_000_000)
    self.assertEqual([(addr, payload, bus) for addr, payload, bus in next_frames if addr in (0x1BA, 0x1E5)],
                     create_ioniq6_blindspot_status(1, BlindspotStatus(1, 0)))
    for i in range(6, 10):
      controller.update(control.as_reader(), ci.CS, t - 9_000_000_000 + i * 10_000_000)
    # An unrelated physical CAN update advances BOOTTIME without refreshing the
    # corner source. Host must withhold both frames; no zero-status substitute.
    ci.update([(t + 160_000_000, [packer.make_can_msg("WHEEL_SPEEDS", 1, {})])])
    _, stale = controller.update(control.as_reader(), ci.CS, t - 9_000_000_000 + 160_000_000)
    self.assertFalse(any(addr in (0x1BA, 0x1E5) for addr, _, _ in stale))

  def test_controller_low_speed_indication_boundary_and_source_preservation(self):
    cp = long_params()
    ci = CarInterface(cp)
    controller = CarController(DBC[cp.carFingerprint], cp)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    control = structs.CarControl()
    controller.ioniq6_bsm_counter = 250  # Exercise rollover while crossing the speed threshold.
    threshold = 5 * CV.MPH_TO_MS
    # Include repeated crossings; each fresh pair keeps the rolling counter.
    speeds = (0.0, threshold - 1e-6, threshold, threshold + 1e-6, 10.0, 0.0, threshold)
    for left, right in ((False, False), (True, False), (False, True), (True, True)):
      for left_lamp, right_lamp in ((False, False), (True, False), (False, True), (True, True)):
        for speed in speeds:
          with self.subTest(left=left, right=right, lamps=(left_lamp, right_lamp), speed=speed):
            t = 10_000_000_000 + controller.frame * 10_000_000
            ci.update([(t, [packer.make_can_msg("BLINDSPOTS_FRONT_CORNER_2", 1,
                                               {"SIDE_DETECT_STATE": (0x10 if left else 0) | (0x08 if right else 0)}),
                            packer.make_can_msg("BLINKERS", 1, {})])])
            # Exact float boundary without Cap'n Proto Float32 quantization.
            out = ci.CS.out
            ci.CS.out = SimpleNamespace(**{name: getattr(out, name) for name in out.schema.fields})
            ci.CS.out.vEgoRaw = speed
            sources = Ioniq6BlindspotSources()
            sources.observe(corner_source_ns=t, corner_state=(0x10 if left else 0) | (0x08 if right else 0),
                            lamp_source_ns=t, left_lamp=left_lamp, right_lamp=right_lamp)
            ci.CS.ioniq6_bsm_sources = sources
            source_status = sources.status(t)
            assert source_status is not None
            counter = controller.ioniq6_bsm_counter
            _, frames = controller.update(control.as_reader(), ci.CS, t)
            status_frames = [(addr, payload, bus) for addr, payload, bus in frames if addr in (0x1BA, 0x1E5)]
            expected = (BlindspotStatus(0, 0) if speed < threshold or 2 in (source_status.left, source_status.right)
                        else source_status)
            self.assertEqual(status_frames, create_ioniq6_blindspot_status(counter, expected))
            self.assertEqual(sources.status(t), source_status)
            for addr, payload, _ in status_frames:
              self.assertEqual(payload[2], counter)
              self.assertEqual(int.from_bytes(payload[:2], "little"), hkg_can_fd_checksum(addr, None, bytearray(payload)))
            # The speed filter does not change the existing paired-send cadence.
            for i in range(1, 5):
              _, skipped = controller.update(control.as_reader(), ci.CS, t + i * 10_000_000)
              self.assertFalse(any(addr in (0x1BA, 0x1E5) for addr, _, _ in skipped))
            ci.CS.out = out

  def test_controller_requires_a_matching_queued_lane_change_for_escalation(self):
    cp = long_params()
    ci = CarInterface(cp)
    controller = CarController(DBC[cp.carFingerprint], cp)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    control = structs.CarControl()
    controller.ioniq6_bsm_counter = 250
    for physical_left in (True, False):
      for request in ('matching', 'none', 'opposite', 'hazards', 'matching', 'none'):
        with self.subTest(left=physical_left, request=request):
          t = 10_000_000_000 + controller.frame * 10_000_000
          ci.update([(t, [packer.make_can_msg('BLINDSPOTS_FRONT_CORNER_2', 1,
                                             {'SIDE_DETECT_STATE': 0x10 if physical_left else 0x08}),
                          packer.make_can_msg('BLINKERS', 1, {})])])
          ci.CS.out.vEgoRaw = 10.0
          sources = Ioniq6BlindspotSources()
          sources.observe(corner_source_ns=t, corner_state=0x10 if physical_left else 0x08,
                          lamp_source_ns=t, left_lamp=physical_left, right_lamp=not physical_left)
          ci.CS.ioniq6_bsm_sources = sources
          control.leftBlinker = request == 'hazards' or request == ('matching' if physical_left else 'opposite')
          control.rightBlinker = request == 'hazards' or request == ('opposite' if physical_left else 'matching')
          counter = controller.ioniq6_bsm_counter
          _, frames = controller.update(control.as_reader(), ci.CS, t)
          actual = [(addr, payload, bus) for addr, payload, bus in frames if addr in (0x1BA, 0x1E5)]
          source_status = sources.status(t)
          assert source_status is not None
          expected = source_status if request == 'matching' else BlindspotStatus(0, 0)
          self.assertEqual(actual, create_ioniq6_blindspot_status(counter, expected), 'UNQUEUED_BLINDSPOT_WARNING')
          self.assertEqual(sources.status(t), BlindspotStatus(2, 0) if physical_left else BlindspotStatus(0, 2))
          for offset in range(1, 5):
            _, skipped = controller.update(control.as_reader(), ci.CS, t + offset * 10_000_000)
            self.assertFalse(any(addr in (0x1BA, 0x1E5) for addr, _, _ in skipped))

  def test_actual_lane_change_request_completion_and_turn_with_held_stalk(self):
    from openpilot.cereal import log
    from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
    from openpilot.starpilot.lateral.lane_change_preferences import LaneChangePolicy

    for left in (True, False):
      cp = long_params()
      ci = CarInterface(cp)
      controller = CarController(DBC[cp.carFingerprint], cp)
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      state = structs.CarState(vEgo=20.0, leftBlinker=left, rightBlinker=not left)
      helper = DesireHelper(LaneChangePolicy(auto_lane_change=True))

      def check(expected, helper, *, controller=controller, ci=ci, packer=packer, left=left):
        control = structs.CarControl()
        t = 10_000_000_000 + controller.frame * 10_000_000
        ci.update([(t, [packer.make_can_msg('BLINDSPOTS_FRONT_CORNER_2', 1,
                                            {'SIDE_DETECT_STATE': 0x10 if left else 0x08}),
                         packer.make_can_msg('BLINKERS', 1, {'LEFT_LAMP': int(left), 'RIGHT_LAMP': int(not left)})])])
        ci.CS.out.vEgoRaw = 20.0
        queued = helper.lane_change_state != log.LaneChangeState.off
        control.leftBlinker = queued and helper.lane_change_direction == log.LaneChangeDirection.left
        control.rightBlinker = queued and helper.lane_change_direction == log.LaneChangeDirection.right
        counter = controller.ioniq6_bsm_counter
        _, frames = controller.update(control.as_reader(), ci.CS, t)
        self.assertEqual([frame for frame in frames if frame[0] in (0x1BA, 0x1E5)],
                         create_ioniq6_blindspot_status(counter, expected))
        for offset in range(1, 5):
          controller.update(control.as_reader(), ci.CS, t + offset * 10_000_000)

      # Missing automatic-lane evidence does not cancel a manual pending request.
      helper.update(state, True, 1.0, auto_evidence=False, engaged=True)
      helper.update(state, True, 1.0, auto_evidence=False, engaged=True)
      self.assertEqual(helper.auto_status, 'laneUnavailable')
      self.assertEqual(helper.lane_change_state, log.LaneChangeState.preLaneChange)
      check(BlindspotStatus(2, 0) if left else BlindspotStatus(0, 2), helper)
      state.steeringPressed = True
      state.steeringTorque = 1.0 if left else -1.0
      helper.update(state, True, 1.0, engaged=True)
      self.assertEqual(helper.lane_change_state, log.LaneChangeState.laneChangeStarting)
      check(BlindspotStatus(2, 0) if left else BlindspotStatus(0, 2), helper)
      state.steeringPressed = False
      for _ in range(12):
        helper.update(state, True, 0.0, engaged=True)
      self.assertEqual(helper.lane_change_state, log.LaneChangeState.off)
      check(BlindspotStatus(0, 0), helper)
      helper.update(state, True, 0.0, engaged=True)
      check(BlindspotStatus(0, 0), helper)
      helper = DesireHelper()
      helper.update(state, True, 1.0, navigation_turn=True)
      self.assertEqual(helper.lane_change_state, log.LaneChangeState.off)
      check(BlindspotStatus(0, 0), helper)
