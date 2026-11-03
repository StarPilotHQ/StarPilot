import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from openpilot.cereal import messaging
from openpilot.starpilot.curve_speed.host import CurveHost

NOW = 10_000_000_000


def sample_messages():
  return {
    'modelV2': NS(orientationRate=NS(z=[0.25] * 33), velocity=NS(x=[25.0] * 33),
                  position=NS(x=[float(i * 5) for i in range(33)], y=[0.0] * 33), timestampEof=NOW + 2_000_000_000),
    'carState': NS(vCruise=108.0, vEgo=25.0, canValid=True, canTimeout=False, leftBlinker=False,
                   rightBlinker=False, gasPressed=False, brakePressed=False, standstill=False),
    'carControl': NS(longActive=True),
    'controlsState': NS(curvature=0.01, forceDecel=False),
    'selfdriveState': NS(enabled=True),
    'radarState': NS(leadOne=NS(present=False, radar=True, dRel=30.0, vLead=25.0,
                                aLeadK=0.0, modelProb=1.0, yRel=0.0)),
  }


class Bus:
  def __init__(self, messages, now=NOW):
    self.messages = messages
    self.logMonoTime = dict.fromkeys(messages, now)
    self.valid = dict.fromkeys(messages, True)
    self.alive = dict.fromkeys(messages, True)

  def __getitem__(self, name):
    return self.messages[name]


class TestCurveHost(unittest.TestCase):
  def setUp(self):
    self.sm = Bus(sample_messages())
    self.cp = NS(openpilotLongitudinalControl=True, pcmCruise=False)
    self.host = CurveHost(enabled=True, replay=True)

  def test_valid_projection_and_stale_radar(self):
    self.assertIsNotNone(self.host.sample(self.sm, self.cp, NOW, follow_time_s=1.45).ceiling_mps)
    self.sm.logMonoTime['radarState'] = NOW - 1_000_000_000
    self.assertIsNone(self.host.sample(self.sm, self.cp, NOW + 50_000_000).ceiling_mps)

  def test_curve_uses_same_cycle_slc_capped_cruise_without_mutating_set(self):
    with patch.object(self.host.runtime, 'step', wraps=self.host.runtime.step) as step:
      self.host.sample(self.sm, self.cp, NOW, follow_time_s=1.45, cruise_ceiling_mps=20.)
    self.assertEqual(step.call_args.args[0].cruise_mps, 20.)
    self.assertEqual(self.sm['carState'].vCruise, 108.)

  def test_stock_owner_and_malformed_field_fail_closed(self):
    self.cp.openpilotLongitudinalControl = False
    self.assertIsNone(self.host.sample(self.sm, self.cp, NOW).ceiling_mps)
    self.cp.openpilotLongitudinalControl = True
    self.sm.messages['carState'].vCruise = 10**1000
    self.assertIsNone(self.host.sample(self.sm, self.cp, NOW + 50_000_000).ceiling_mps)

  def test_unknown_headway_or_geometry_never_becomes_no_lead(self):
    host = CurveHost(enabled=True, no_lead=True, replay=True)
    self.assertIsNone(host.sample(self.sm, self.cp, NOW).ceiling_mps)
    self.sm.logMonoTime = dict.fromkeys(self.sm.messages, NOW + 25_000_000)
    self.assertIsNone(host.sample(self.sm, self.cp, NOW + 25_000_000, follow_time_s=0.0).ceiling_mps)
    self.sm.messages['modelV2'].position.y = [0.0]
    self.sm.logMonoTime = dict.fromkeys(self.sm.messages, NOW + 50_000_000)
    self.assertIsNone(host.sample(self.sm, self.cp, NOW + 50_000_000, follow_time_s=1.45).ceiling_mps)
    self.sm.messages['modelV2'].position.y = [0.0] * 33
    self.sm.logMonoTime = dict.fromkeys(self.sm.messages, NOW + 100_000_000)
    self.assertIsNotNone(host.sample(self.sm, self.cp, NOW + 100_000_000, follow_time_s=1.45).ceiling_mps)

  def test_inactive_long_uses_frozen_zero_headway_for_vision_tracking(self):
    host = CurveHost(enabled=True, replay=True)
    radar = self.sm.messages['radarState'].leadOne
    radar.present = True
    radar.radar = False
    radar.modelProb = 0.95
    radar.dRel = 30.0
    seen = []
    frames = []
    original = host.leads.step
    original_runtime = host.runtime.step
    def capture(*args, **kwargs):
      observation = original(*args, **kwargs)
      seen.append((kwargs['t_follow_s'], observation))
      return observation
    def runtime_capture(frame, **kwargs):
      frames.append(frame)
      return original_runtime(frame, **kwargs)
    self.enterContext(patch.object(host.leads, 'step', side_effect=capture))
    self.enterContext(patch.object(host.runtime, 'step', side_effect=runtime_capture))
    for n in range(1, 17):
      stamp = NOW + n * 50_000_000
      self.sm.logMonoTime = dict.fromkeys(self.sm.messages, stamp)
      host.sample(self.sm, self.cp, stamp, follow_time_s=1.75)
    self.assertTrue(seen[-1][1].tracked)
    self.assertTrue(seen[-1][1].following)
    self.sm.messages['carControl'].longActive = False
    radar.dRel = 65.0  # Matched only with the old 1.75 s vision window.
    for n in range(17, 38):
      stamp = NOW + n * 50_000_000
      self.sm.logMonoTime = dict.fromkeys(self.sm.messages, stamp)
      self.assertIsNone(host.sample(self.sm, self.cp, stamp, follow_time_s=1.75).ceiling_mps)
    self.assertEqual(seen[16][0], 1.75)  # Frozen tracker reads prior headway before reset.
    self.assertFalse(frames[16].following_lead)  # Frozen following update has already reset it.
    self.assertEqual(seen[-1][0], 0.0)
    self.assertFalse(seen[-1][1].following)
    self.assertFalse(seen[-1][1].tracked)

  def capture_frames(self, host):
    frames = []
    original = host.runtime.step
    def runtime_capture(frame, **kwargs):
      frames.append(frame)
      return original(frame, **kwargs)
    self.enterContext(patch.object(host.runtime, 'step', side_effect=runtime_capture))
    return frames

  def present_lead(self, d_rel=30.0):
    radar = self.sm.messages['radarState'].leadOne
    radar.present = True
    radar.dRel = d_rel

  def test_stale_frame_keeps_lead_consensus(self):
    host = CurveHost(enabled=True, no_lead=True, replay=True)
    frames = self.capture_frames(host)
    self.present_lead()
    for n in range(1, 17):
      stamp = NOW + n * 50_000_000
      self.sm.logMonoTime = dict.fromkeys(self.sm.messages, stamp)
      host.sample(self.sm, self.cp, stamp, follow_time_s=1.45)
    self.assertTrue(frames[-1].following_lead)
    stamp += 45_000_000
    self.sm.logMonoTime = dict.fromkeys(self.sm.messages, stamp)
    self.sm.logMonoTime['carState'] = stamp - 30_000_000
    host.sample(self.sm, self.cp, stamp, follow_time_s=1.45)
    stamp += 45_000_000
    self.sm.logMonoTime = dict.fromkeys(self.sm.messages, stamp)
    host.sample(self.sm, self.cp, stamp, follow_time_s=1.45)
    self.assertTrue(frames[-1].tracking_lead)
    self.assertTrue(frames[-1].following_lead)

  def test_present_lead_is_unknown_until_detector_warms_up(self):
    host = CurveHost(enabled=True, no_lead=True, replay=True)
    frames = self.capture_frames(host)
    self.present_lead()
    for n in range(1, 21):
      stamp = NOW + n * 50_000_000
      self.sm.logMonoTime = dict.fromkeys(self.sm.messages, stamp)
      self.assertIsNone(host.sample(self.sm, self.cp, stamp, follow_time_s=1.45).ceiling_mps)
    self.assertNotIn(False, [frame.following_lead for frame in frames])
    self.assertTrue(frames[-1].following_lead)

  def test_actual_serialized_schema_projection(self):
    events = {name: messaging.new_message(name) for name in self.sm.messages}
    events['modelV2'].modelV2.orientationRate.z = [0.25] * 33
    events['modelV2'].modelV2.velocity.x = [25.0] * 33
    events['modelV2'].modelV2.position.x = [float(i * 5) for i in range(33)]
    events['modelV2'].modelV2.position.y = [0.0] * 33
    events['carState'].carState.vCruise = 108.0
    events['carState'].carState.vEgo = 25.0
    events['carState'].carState.canValid = True
    events['carControl'].carControl.longActive = True
    events['selfdriveState'].selfdriveState.enabled = True
    events['controlsState'].controlsState.curvature = 0.01
    events['radarState'].radarState.leadOne.present = False
    events['radarState'].radarState.leadOne.radar = True
    events['radarState'].radarState.leadOne.dRel = 30.0
    events['radarState'].radarState.leadOne.vLead = 25.0
    events['radarState'].radarState.leadOne.modelProb = 1.0
    decoded = {name: getattr(messaging.log_from_bytes(event.to_bytes()), name) for name, event in events.items()}
    sm = Bus(decoded)
    self.assertIsNotNone(CurveHost(enabled=True, replay=True).sample(sm, self.cp, NOW, follow_time_s=1.45).ceiling_mps)
    lead_event = messaging.new_message('radarState')
    lead_event.radarState.leadOne.present = True
    lead_event.radarState.leadOne.radar = True
    lead_event.radarState.leadOne.dRel = 30.0
    lead_event.radarState.leadOne.vLead = 25.0
    lead_event.radarState.leadOne.modelProb = 1.0
    decoded['radarState'] = messaging.log_from_bytes(lead_event.to_bytes()).radarState
    host = CurveHost(enabled=True, no_lead=True, replay=True)
    for n in range(1, 21):
      stamp = NOW + n * 50_000_000
      sm.logMonoTime = dict.fromkeys(sm.messages, stamp)
      result = host.sample(sm, self.cp, stamp, follow_time_s=1.45)
    self.assertIsNone(result.ceiling_mps)
    self.assertFalse(host.runtime.was_controlling)

  def test_live_resume_barrier_rejects_queued_monotonic_frames(self):
    pair = [(NOW, NOW + 2_000_000_000),
            (NOW + 50_000_000, NOW + 2_050_000_000),
            (NOW + 100_000_000, NOW + 11_100_000_000),
            (NOW + 150_000_000, NOW + 11_150_000_000),
            (NOW + 200_000_000, NOW + 11_200_000_000)]
    host = CurveHost(enabled=True, clock_pair=iter(pair).__next__)
    self.assertIsNone(host.sample(self.sm, self.cp, NOW).ceiling_mps)
    self.sm.logMonoTime = dict.fromkeys(self.sm.messages, NOW + 50_000_000)
    self.assertIsNotNone(host.sample(self.sm, self.cp, NOW + 50_000_000).ceiling_mps)
    # BOOT advanced nine seconds relative to MONO; queued pre-resume input
    # could otherwise still appear fresh on the MONOTONIC timeline.
    self.assertIsNone(host.sample(self.sm, self.cp, NOW + 100_000_000).ceiling_mps)
    self.assertIsNone(host.sample(self.sm, self.cp, NOW + 150_000_000).ceiling_mps)
    self.sm.logMonoTime = dict.fromkeys(self.sm.messages, NOW + 200_000_000)
    self.sm.messages['modelV2'].timestampEof = NOW + 11_200_000_000
    self.assertIsNotNone(host.sample(self.sm, self.cp, NOW + 200_000_000).ceiling_mps)

  def test_live_fresh_envelope_cannot_renew_old_or_future_camera(self):
    tick = [NOW]
    offset = 2_000_000_000
    host = CurveHost(enabled=True, clock_pair=lambda: (tick[0], tick[0] + offset))
    self.assertIsNone(host.sample(self.sm, self.cp, tick[0]).ceiling_mps)
    # Use the actual wire type, with a new transport timestamp each tick.
    for age, expected in ((50_000_000, True), (150_000_001, False), (-1, False),
                          (None, False), (50_000_000, True)):
      with self.subTest(camera_age=age):
        tick[0] += 50_000_000
        event = messaging.new_message('modelV2')
        event.modelV2.orientationRate.z = [0.25] * 33
        event.modelV2.velocity.x = [25.0] * 33
        event.modelV2.position.x = [float(i * 5) for i in range(33)]
        event.modelV2.position.y = [0.0] * 33
        event.modelV2.timestampEof = tick[0] + offset - age if age is not None else 0
        self.sm.messages['modelV2'] = messaging.log_from_bytes(event.to_bytes()).modelV2
        self.sm.logMonoTime = dict.fromkeys(self.sm.messages, tick[0])
        result = host.sample(self.sm, self.cp, tick[0])
        self.assertEqual(result.ceiling_mps is not None, expected)
        if not expected:
          self.assertFalse(result.training)
          self.assertEqual(result.reason, 'stale_or_invalid_evidence')

  def test_live_planner_latency_does_not_age_out_loop_inputs(self):
    offset = 2_000_000_000
    for latency, input_age, camera_age, expected in (
        (35_000_000, 5_000_000, 60_000_000, True),
        (35_000_000, 21_000_000, 60_000_000, False),
        (35_000_000, 5_000_000, 115_000_000, True),
        (35_000_000, 5_000_000, 115_000_001, False),
        (35_000_000, 5_000_000, 140_000_000, False),
        (35_000_000, 5_000_000, -1, False),
        (35_000_000, 5_000_000, 150_000_001, False),
        (150_000_001, 5_000_000, 60_000_000, False),
    ):
      with self.subTest(latency=latency, input_age=input_age, camera_age=camera_age):
        tick = [NOW]
        host = CurveHost(enabled=True, clock_pair=lambda tick=tick, latency=latency: (tick[0] + latency, tick[0] + latency + offset))
        result = None
        for _ in range(4):
          tick[0] += 50_000_000
          self.sm.logMonoTime = dict.fromkeys(self.sm.messages, tick[0] - input_age)
          self.sm.logMonoTime['modelV2'] = tick[0] - 3_000_000
          self.sm.logMonoTime['radarState'] = tick[0] - 40_000_000
          self.sm.messages['modelV2'].timestampEof = tick[0] + offset - camera_age
          result = host.sample(self.sm, self.cp, tick[0])
        self.assertEqual(result.ceiling_mps is not None, expected)
        if not expected:
          self.assertEqual(result.reason, 'stale_or_invalid_evidence')


if __name__ == '__main__':
  unittest.main()
