"""Serialized current-event and clock-domain tests for the inactive projector."""

from types import SimpleNamespace
from dataclasses import replace
import math
import unittest

from openpilot.cereal import messaging
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.starpilot.conditional_mode.projection import ConditionalOwnerContext, ObservedBool, ObservedFloat, SERVICES, SceneProjector
from openpilot.starpilot.conditional_mode.preferences import CEMOptions
from openpilot.starpilot.conditional_mode.policy import ConditionalModePolicy, ManualIntent, ModeChoice, ModeSettings


MONO = 100_000_000_000
BOOT = MONO + 2_000_000_000


class FakeSubMaster:
  def __init__(self, payloads, stamp):
    self.payloads = payloads
    self.logMonoTime = dict.fromkeys(SERVICES, stamp)
    self.recv_time = dict.fromkeys(SERVICES, stamp / 1e9)
    self.seen = dict.fromkeys(SERVICES, True)
    self.alive = dict.fromkeys(SERVICES, True)
    self.valid = dict.fromkeys(SERVICES, True)

  def __getitem__(self, service):
    return self.payloads[service]

  def stamp(self, stamp):
    for service in SERVICES:
      self.logMonoTime[service] = stamp
      self.recv_time[service] = stamp / 1e9


def serialized_scene(
  stamp=BOOT, *, origin=0.0, later_negative=False, speed=15.0, horizon=192.0, lead_present=True, lead_distance=30.0,
  lead_speed=12.0, lead_relative=None, lead_accel=-0.1, standstill=False, plan_should_stop=False, plan_allow_throttle=True,
  left_blinker=False, right_blinker=False, left_edge=-8.0, gas_pressed=False, experimental_mode=False,
  steering_angle=0.0,
):
  messages = {name: messaging.new_message(name, valid=True) for name in SERVICES}
  car = messages['carState'].carState
  car.vEgo = speed
  car.vCruise = 72.0
  car.canValid = True
  car.canTimeout = False
  car.leftBlinker = left_blinker
  car.rightBlinker = right_blinker
  car.standstill = standstill
  car.steeringAngleDeg = steering_angle
  car.gasPressed = gas_pressed
  messages['controlsState'].controlsState.curvature = 0.008
  messages['carControl'].carControl.longActive = True
  messages['carControl'].carControl.enabled = True
  messages['carControl'].carControl.latActive = False
  messages['selfdriveState'].selfdriveState.enabled = True
  messages['selfdriveState'].selfdriveState.experimentalMode = experimental_mode
  lead = messages['radarState'].radarState.leadOne
  lead.present = lead_present
  lead.radar = True
  lead.dRel = lead_distance
  lead.vLead = lead_speed
  lead.aLeadK = lead_accel
  lead.vRel = lead_speed - speed if lead_relative is None else lead_relative
  lead.modelProb = 0.95
  messages['longitudinalPlan'].longitudinalPlan.shouldStop = plan_should_stop
  messages['longitudinalPlan'].longitudinalPlan.allowThrottle = plan_allow_throttle
  model = messages['modelV2'].modelV2
  model.timestampEof = stamp
  path_x = [horizon * i / 32 for i in range(33)]
  path_x[0] = origin
  if later_negative:
    path_x[5] = -1e-5
  model.position.x = path_x
  model.position.y = [0.0] * 33
  model.orientationRate.z = [0.15] * 33
  model.orientationRate.t = [float(i) / 10 for i in range(33)]
  model.velocity.x = [speed] * 33
  model.action.shouldStop = True
  lane_x = [float(index * 5) for index in range(33)]
  for lane, y in zip(model.init('laneLines', 4), (-5.4, -1.8, 1.8, 5.4), strict=True):
    lane.x = lane_x
    lane.y = [y] * 33
  for edge, y in zip(model.init('roadEdges', 2), (left_edge, 8.0), strict=True):
    edge.x = lane_x
    edge.y = [y] * 33
  return {name: getattr(messaging.log_from_bytes(message.to_bytes()), name) for name, message in messages.items()}


CP = SimpleNamespace(openpilotLongitudinalControl=True, pcmCruise=False, carFingerprint='HYUNDAI_IONIQ_6')


def owner_context(stamp: int, *, stop_time=8.0, slower=True, stopped=False):
  def flag(value: bool) -> ObservedBool:
    return ObservedBool(value, stamp)

  return ConditionalOwnerContext(
    traffic_mode=flag(False),
    stop_sign_confirmed=flag(False),
    forcing_stop=flag(False),
    dashboard_stop_sign=flag(False),
    pedal_override=flag(False),
    model_stop_time_s=ObservedFloat(stop_time, stamp),
    slower_option=flag(slower),
    stopped_option=flag(stopped),
    previous_experimental=flag(False),
    committed_turn_scene=flag(False),
    red_light=flag(False),
    plan_forcing_stop=flag(False),
  )


class TestSceneProjector(unittest.TestCase):
  def test_conditional_speed_comparisons_use_the_slc_cap_without_mutating_set(self):
    for cap, expected in ((None, 20.), (15., 15.), (25., 20.), (float('nan'), None)):
      with self.subTest(cap=cap):
        projector = SceneProjector()
        sm = FakeSubMaster(serialized_scene(), MONO-5_000_000)
        projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
        now = MONO+50_000_000
        sm.stamp(now)
        sm.payloads.update(serialized_scene(stamp=BOOT+50_000_000))
        result = projector.project(sm, CP, now_mono_ns=now, now_boot_ns=BOOT+50_000_000,
          sample_skew_ns=1000, safe_mode=False, owner_context=owner_context(now), cruise_ceiling_mps=cap)
        self.assertEqual(result.scene.set_speed_mps, expected)
        self.assertEqual(sm['carState'].vCruise, 72.)

  def test_standstill_filter_undershoot_requires_fresh_physical_zero_speed(self):
    cases = ((-1.401298464324817e-45, True, 0., True),
             (-0.0008391447481699288, True, 0., True),
             (-0.001509889611043036, True, 0., True),
             (-0.01, True, 0., True),
             (-0.0101, True, 0., False),
             (-0.0015, False, 0., False),
             (-0.0015, True, 0.01, False),
             (-0.0015, True, -0.01, False),
             (float('nan'), True, 0., False),
             (float('inf'), True, 0., False),
             (-0.0015, True, float('nan'), False))
    for speed, standstill, raw_speed, accepted in cases:
      with self.subTest(speed=speed, standstill=standstill, raw_speed=raw_speed):
        projector = SceneProjector()
        sm = FakeSubMaster(serialized_scene(speed=0., lead_present=False), MONO - 5_000_000)
        projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
        event = MONO + 50_000_000
        sm.stamp(event)
        sm.payloads.update(serialized_scene(stamp=BOOT + 50_000_000, speed=speed,
                                            standstill=standstill, lead_present=False))
        car = messaging.new_message('carState', valid=True)
        car.carState = sm.payloads['carState']
        car.carState.vEgoRaw = raw_speed
        sm.payloads['carState'] = messaging.log_from_bytes(car.to_bytes()).carState
        result = projector.project(sm, CP, now_mono_ns=event + 1_000_000,
                                   now_boot_ns=BOOT + 51_000_000, sample_skew_ns=1000,
                                   safe_mode=False, owner_context=owner_context(event))
        if accepted:
          self.assertIsNotNone(result.authority)
          self.assertEqual(result.scene.speed_mps, 0.)
          self.assertTrue(result.ready)
        else:
          self.assertIsNone(result.authority)
          self.assertIsNone(result.scene.speed_mps)
        sm.seen['carState'] = False
        missing = projector.project(sm, CP, now_mono_ns=event + 2_000_000,
                                    now_boot_ns=BOOT + 52_000_000, sample_skew_ns=1000,
                                    safe_mode=False, owner_context=owner_context(event))
        self.assertIsNone(missing.authority)
        self.assertIsNone(missing.scene.speed_mps)

  def test_fresh_committed_turn_retains_next_tick_slow_lead_filter_alpha(self):
    sm = FakeSubMaster(serialized_scene(speed=20.0, lead_present=False), MONO - 5_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)

    def frame(offset: int, *, speed: float, blinker: bool, angle: float):
      stamp = MONO + offset
      sm.stamp(stamp)
      sm.payloads.update(serialized_scene(stamp=BOOT + offset, speed=speed, lead_present=False,
                                          left_blinker=blinker, steering_angle=angle))
      return projector.project(sm, CP, now_mono_ns=stamp + 1_000_000,
                               now_boot_ns=BOOT + offset + 1_000_000, sample_skew_ns=1000,
                               selected_t_follow_s=1.45, selected_t_follow_observed_mono_ns=stamp,
                               owner_context=replace(owner_context(stamp), committed_turn_scene=None))

    high = frame(10_000_000, speed=20.0, blinker=False, angle=0.0)
    self.assertIsNotNone(high.slower_source_observed_mono_s)
    high_alpha = projector.slower_lead_detector.filter.alpha
    self.assertLess(high_alpha, 0.5)
    turn = frame(60_000_000, speed=5.0, blinker=True, angle=50.0)
    self.assertIsNotNone(turn.slower_source_observed_mono_s)
    self.assertEqual(projector.slower_lead_detector.filter.alpha, high_alpha)
    straight = frame(110_000_000, speed=5.0, blinker=False, angle=0.0)
    self.assertIsNotNone(straight.slower_source_observed_mono_s)
    self.assertEqual(projector.slower_lead_detector.filter.alpha, 1.0)
    sm.stamp(MONO + 500_000_000)
    stale = projector.project(sm, CP, now_mono_ns=MONO + 501_000_000,
                              now_boot_ns=BOOT + 501_000_000, sample_skew_ns=1000,
                              selected_t_follow_s=1.45,
                              selected_t_follow_observed_mono_ns=MONO + 500_000_000,
                              owner_context=replace(owner_context(MONO + 500_000_000), committed_turn_scene=None))
    self.assertIsNone(stale.scene.slow_lead_detected)

  def test_brief_missing_car_does_not_erase_stop_hysteresis(self):
    sm = FakeSubMaster(serialized_scene(lead_present=False), MONO - 5_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    for tick in range(1, 35):
      stamp = MONO + tick * 50_000_000
      sm.stamp(stamp)
      sm.payloads.update(serialized_scene(stamp=BOOT + tick * 50_000_000, horizon=30., lead_present=False))
      if tick == 30:
        sm.seen['carState'] = False
      else:
        sm.seen['carState'] = True
      result = projector.project(sm, CP, now_mono_ns=stamp + 1_000_000,
                                 now_boot_ns=BOOT + tick * 50_000_000 + 1_000_000, sample_skew_ns=1000,
                                 safe_mode=False, selected_t_follow_s=1.45,
                                 selected_t_follow_observed_mono_ns=stamp, owner_context=owner_context(stamp))
      if tick == 29:
        self.assertTrue(result.scene.stop_light_detected)
        held_filter = projector.stop_detector.light_filter.x
      elif tick == 30:
        self.assertIsNone(result.scene.stop_light_detected)
        self.assertIsNone(result.authority)
        self.assertEqual(projector.stop_detector.light_filter.x, held_filter)
      elif tick > 30:
        self.assertTrue(result.scene.stop_light_detected)

  def test_actual_nidec_pcmcruise_with_system_long_is_capable(self):
    sm = FakeSubMaster(serialized_scene(), MONO + 10_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    nidec = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    self.assertTrue(nidec.openpilotLongitudinalControl)
    self.assertTrue(nidec.pcmCruise)
    result = projector.project(sm, nidec, now_mono_ns=MONO + 12_000_000,
                               now_boot_ns=BOOT + 12_000_000, sample_skew_ns=1000, safe_mode=False)
    self.assertTrue(result.authority.system_long_capable)
    stock_acc = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC_BOSCH)
    self.assertFalse(stock_acc.openpilotLongitudinalControl)
    blocked = projector.project(sm, stock_acc, now_mono_ns=MONO + 13_000_000,
                                now_boot_ns=BOOT + 13_000_000, sample_skew_ns=1000, safe_mode=False)
    self.assertFalse(blocked.authority.system_long_capable)

  def test_qualified_adjacent_wire_is_source_bound_and_replay_guarded(self):
    event_ns = MONO + 10_000_000
    sm = FakeSubMaster(serialized_scene(stamp=BOOT + 10_000_000), MONO - 5_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    sm.stamp(event_ns)

    def adjacent(sequence=1, status='ambiguous', *, valid=True, model_ns=event_ns):
      event = messaging.new_message('starpilotRadarState', valid=valid)
      wire = event.starpilotRadarState.qualifiedAdjacent
      wire.version = 1
      wire.status = status
      wire.producerSessionId = 'a' * 32
      wire.sequence = sequence
      wire.radarTracksMonoTime = event_ns
      wire.modelMonoTime = model_ns
      wire.carStateMonoTime = event_ns
      wire.cameraEofBootTime = BOOT + 10_000_000
      wire.observedMonoTime = event_ns - 1000
      wire.validUntilMonoTime = event_ns + 150_000_000
      return messaging.log_from_bytes(event.to_bytes()).starpilotRadarState

    sm.payloads['starpilotRadarState'] = adjacent()
    result = projector.project(sm, CP, now_mono_ns=event_ns + 1_000_000, now_boot_ns=BOOT + 11_000_000, sample_skew_ns=1000)
    self.assertTrue(result.scene.adjacent_lead_ambiguous)
    repeated = projector.project(sm, CP, now_mono_ns=event_ns + 2_000_000, now_boot_ns=BOOT + 12_000_000, sample_skew_ns=1000)
    self.assertTrue(repeated.scene.adjacent_lead_ambiguous)
    sm.payloads['starpilotRadarState'] = adjacent(status='clear')
    self.assertIsNone(projector.project(sm, CP, now_mono_ns=event_ns + 3_000_000,
                                        now_boot_ns=BOOT + 13_000_000, sample_skew_ns=1000).scene.adjacent_lead_ambiguous)
    sm.payloads['starpilotRadarState'] = adjacent(sequence=2, status='unknown', valid=False)
    sm.valid['starpilotRadarState'] = False
    self.assertIsNone(projector.project(sm, CP, now_mono_ns=event_ns + 4_000_000,
                                        now_boot_ns=BOOT + 14_000_000, sample_skew_ns=1000).scene.adjacent_lead_ambiguous)
    sm.payloads['starpilotRadarState'] = adjacent()
    sm.valid['starpilotRadarState'] = True
    self.assertIsNone(projector.project(sm, CP, now_mono_ns=event_ns + 5_000_000,
                                        now_boot_ns=BOOT + 15_000_000, sample_skew_ns=1000).scene.adjacent_lead_ambiguous)
    sm.payloads['starpilotRadarState'] = adjacent(sequence=3, model_ns=event_ns + 100_000_000)
    self.assertIsNone(projector.project(sm, CP, now_mono_ns=event_ns + 6_000_000,
                                        now_boot_ns=BOOT + 16_000_000, sample_skew_ns=1000).scene.adjacent_lead_ambiguous)

  def test_serialized_signal_width_four_model_updates_and_loss(self):
    options = CEMOptions(signal_speed_mps=20.0, signal_lane_width_m=3.0)
    sm = FakeSubMaster(serialized_scene(), MONO - 5_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000, signal_options=options)
    sm.seen['radarState'] = False  # Optional, slower-cadence radar cannot stall model-width ticks.
    for index in range(1, 5):
      event = MONO + index * 50_000_000
      sm.stamp(event)
      sm.payloads = serialized_scene(stamp=BOOT + index * 50_000_000, left_blinker=True, left_edge=-2.0)
      result = projector.project(sm, CP, now_mono_ns=event + 1_000_000, now_boot_ns=BOOT + index * 50_000_000 + 1_000_000,
                                 sample_skew_ns=1000, signal_options=options)
      self.assertEqual(result.scene.lane_available, False if index == 4 else None)
    repeated = projector.project(sm, CP, now_mono_ns=event + 2_000_000, now_boot_ns=BOOT + 4 * 50_000_000 + 2_000_000,
                                 sample_skew_ns=1000, signal_options=options)
    self.assertFalse(repeated.scene.lane_available)
    sm.seen['modelV2'] = False
    missing = projector.project(sm, CP, now_mono_ns=event + 3_000_000, now_boot_ns=BOOT + 4 * 50_000_000 + 3_000_000,
                                sample_skew_ns=1000, signal_options=options)
    self.assertIsNone(missing.scene.lane_available)

  def test_serialized_projection_and_unknown_fields(self):
    sm = FakeSubMaster(serialized_scene(), MONO - 5_000_000)
    projector = SceneProjector()
    self.assertFalse(projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000).ready)
    sm.stamp(MONO + 10_000_000)
    result = projector.project(sm, CP, now_mono_ns=MONO + 12_000_000, now_boot_ns=BOOT + 12_000_000, sample_skew_ns=1000, safe_mode=True)
    self.assertTrue(result.ready)
    self.assertEqual(result.scene.set_speed_mps, 20.0)
    self.assertTrue(result.raw_driving_in_curve)
    self.assertTrue(result.raw_lead.present)
    self.assertFalse(result.raw_model_stopped)
    self.assertTrue(result.current_model_should_stop)
    self.assertIsNone(result.scene.following_lead)
    self.assertIsNone(result.scene.curve_detected)
    self.assertIsNone(result.scene.stop_light_detected)
    self.assertFalse(result.authority.lat_active)
    observed = result.scene.observed_mono_s
    self.assertIsNotNone(observed)
    if observed is not None:
      self.assertAlmostEqual(observed, (MONO + 10_000_000) / 1e9)

  def test_stale_services_and_repeated_source_do_not_become_false_or_new(self):
    sm = FakeSubMaster(serialized_scene(), MONO + 10_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    first = projector.project(sm, CP, now_mono_ns=MONO + 12_000_000, now_boot_ns=BOOT + 12_000_000, sample_skew_ns=1000, safe_mode=True)
    repeated = projector.project(sm, CP, now_mono_ns=MONO + 16_000_000, now_boot_ns=BOOT + 16_000_000, sample_skew_ns=1000, safe_mode=True)
    self.assertEqual(first.scene.observed_mono_s, repeated.scene.observed_mono_s)
    sm.logMonoTime['radarState'] = MONO - 500_000_000
    sm.recv_time['radarState'] = (MONO - 500_000_000) / 1e9
    stale = projector.project(sm, CP, now_mono_ns=MONO + 17_000_000, now_boot_ns=BOOT + 17_000_000, sample_skew_ns=1000, safe_mode=True)
    self.assertIsNone(stale.raw_lead)
    self.assertTrue(stale.ready)

  def test_optional_missing_radar_does_not_block_core_projection(self):
    sm = FakeSubMaster(serialized_scene(), MONO - 5_000_000)
    sm.seen['radarState'] = False
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    sm.stamp(MONO + 10_000_000)
    result = projector.project(sm, CP, now_mono_ns=MONO + 12_000_000, now_boot_ns=BOOT + 12_000_000, sample_skew_ns=1000, safe_mode=True)
    self.assertTrue(result.ready)
    self.assertIsNone(result.raw_lead)
    self.assertNotIn('radarState', result.fresh_services)

  def test_missing_controls_curvature_does_not_erase_car_speed(self):
    sm = FakeSubMaster(serialized_scene(), MONO - 5_000_000)
    sm.seen['controlsState'] = False
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    sm.stamp(MONO + 10_000_000)
    result = projector.project(sm, CP, now_mono_ns=MONO + 12_000_000, now_boot_ns=BOOT + 12_000_000, sample_skew_ns=1000,
                               safe_mode=True, owner_context=owner_context(MONO + 10_000_000))
    self.assertTrue(result.ready)
    self.assertEqual(result.scene.speed_mps, 15.0)
    self.assertIsNone(result.raw_driving_in_curve)
    self.assertIsNone(result.scene.curve_detected)

  def test_fresh_envelope_does_not_renew_old_camera_eof(self):
    sm = FakeSubMaster(serialized_scene(stamp=BOOT - 140_000_000), MONO + 10_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    near = projector.project(
      sm,
      CP,
      now_mono_ns=MONO + 12_000_000,
      now_boot_ns=BOOT + 12_000_000,
      sample_skew_ns=1000,
      safe_mode=True,
      selected_t_follow_s=1.45,
      selected_t_follow_observed_mono_ns=MONO + 10_000_000,
    )
    self.assertTrue(near.ready)
    self.assertIsNone(near.model_horizon_m)  # 152 ms old: past the 150 ms EOF limit.
    self.assertIsNone(near.raw_model_stopped)
    self.assertIsNone(near.raw_road_curve)
    self.assertIsNone(near.scene.curve_detected)
    self.assertTrue(near.raw_lead.present)
    self.assertIsNone(near.lead_observation.tracked)

  def test_invalid_model_path_remains_unknown(self):
    payloads = serialized_scene()
    # Serialize a second current wire message with a zero-length path rather
    # than mutating a decoded Cap'n Proto reader.
    invalid = messaging.new_message('modelV2', valid=True)
    invalid.modelV2.timestampEof = BOOT
    invalid.modelV2.position.x = []
    payloads['modelV2'] = messaging.log_from_bytes(invalid.to_bytes()).modelV2
    sm = FakeSubMaster(payloads, MONO + 10_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    result = projector.project(sm, CP, now_mono_ns=MONO + 12_000_000, now_boot_ns=BOOT + 12_000_000, sample_skew_ns=1000, safe_mode=True)
    self.assertTrue(result.ready)
    self.assertIsNone(result.model_horizon_m)
    self.assertIsNone(result.raw_model_stopped)
    self.assertIsNone(result.current_model_should_stop)

  def test_resume_rearms_only_after_post_barrier_events(self):
    sm = FakeSubMaster(serialized_scene(), MONO + 10_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    self.assertTrue(projector.project(sm, CP, now_mono_ns=MONO + 12_000_000, now_boot_ns=BOOT + 12_000_000, sample_skew_ns=1000, safe_mode=True).ready)
    # MONOTONIC advances only 20 ms across a 90 ms suspend, while BOOTTIME
    # advances 110 ms. The old model is still inside its BOOTTIME TTL.
    self.assertFalse(projector.project(sm, CP, now_mono_ns=MONO + 32_000_000, now_boot_ns=BOOT + 122_000_000, sample_skew_ns=1000, safe_mode=True).ready)
    self.assertFalse(projector.project(sm, CP, now_mono_ns=MONO + 34_000_000, now_boot_ns=BOOT + 124_000_000, sample_skew_ns=1000, safe_mode=True).ready)
    sm.stamp(MONO + 35_000_000)
    self.assertTrue(projector.project(sm, CP, now_mono_ns=MONO + 36_000_000, now_boot_ns=BOOT + 126_000_000, sample_skew_ns=1000, safe_mode=True).ready)

  def test_current_model_curve_filters_only_with_explicit_mode_evidence(self):
    sm = FakeSubMaster(serialized_scene(), MONO + 10_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    first = projector.project(sm, CP, now_mono_ns=MONO + 12_000_000, now_boot_ns=BOOT + 12_000_000, sample_skew_ns=1000, safe_mode=True)
    self.assertTrue(first.raw_road_curve)
    self.assertIsNone(first.scene.curve_detected)
    # A caller-proven non-traffic mode makes the actual frozen filter active.
    for tick in range(1, 30):
      event = MONO + 10_000_000 + tick * 50_000_000
      sm.stamp(event)
      sm.payloads['modelV2'] = serialized_scene(stamp=BOOT + 10_000_000 + tick * 50_000_000)['modelV2']
      result = projector.project(
        sm, CP, now_mono_ns=event + 1_000_000, now_boot_ns=BOOT + 11_000_000 + tick * 50_000_000, sample_skew_ns=1000,
        safe_mode=True, owner_context=owner_context(event),
      )
    self.assertTrue(result.scene.curve_detected)
    repeated = projector.project(
      sm, CP, now_mono_ns=event + 2_000_000, now_boot_ns=BOOT + 12_000_000 + tick * 50_000_000, sample_skew_ns=1000,
      safe_mode=True, owner_context=owner_context(event),
    )
    self.assertIsNone(repeated.scene.curve_detected)
    self.assertGreater(projector.curve_detector.value, 0.6)

  def test_exact_curve_gap_preserves_proposal_at_different_clock_offsets(self):
    for base in (100_000_000_000, 100_100_000_000):
      with self.subTest(base_ns=base):
        projector, policy = SceneProjector(), ConditionalModePolicy()
        sm = FakeSubMaster(serialized_scene(speed=30.0, lead_present=False), base)
        projector.project(sm, CP, now_mono_ns=base - 10_000_000,
                          now_boot_ns=base + 1_990_000_000, sample_skew_ns=1000)
        stamps = [base + index * 50_000_000 for index in range(40)]
        stamps += [stamps[-1] + 100_000_000 + index * 50_000_000 for index in range(28)]
        curves, proposals = [], []
        for event in stamps:
          sm.stamp(event)
          sm.recv_time = dict.fromkeys(SERVICES, (event + 2_000_000) / 1e9)
          sm.payloads = serialized_scene(stamp=event + 2_000_000_000, speed=30.0, lead_present=False)
          result = projector.project(
            sm, CP, now_mono_ns=event + 5_000_000, now_boot_ns=event + 2_005_000_000,
            sample_skew_ns=1000, safe_mode=False, owner_context=owner_context(event),
            selected_t_follow_s=1.45, selected_t_follow_observed_mono_ns=event,
          )
          self.assertIsNotNone(result.authority)
          assert result.authority is not None
          decision = policy.step((event + 5_000_000) / 1e9, ModeChoice.CEM, ManualIntent.NONE,
                                 result.authority, result.scene, ModeSettings(cem_curves=True, cem_lead=False, cem_stop=False))
          curves.append(result.scene.curve_detected)
          proposals.append(decision.requested_experimental)
        self.assertEqual(proposals[40:], [True] * 28)
        self.assertEqual(curves[40:], [True] * 28)

  def test_curve_timestamp_retains_event_receipt_minimum_and_invalid_reset(self):
    projector = SceneProjector()
    sm = FakeSubMaster(serialized_scene(), MONO)
    projector.project(sm, CP, now_mono_ns=MONO - 10_000_000,
                      now_boot_ns=BOOT - 10_000_000, sample_skew_ns=1000)
    for event, receipt, expected in ((MONO + 1, MONO, MONO),
                                     (MONO + 50_000_000, MONO + 60_000_000, MONO + 50_000_000)):
      sm.stamp(event)
      sm.recv_time['modelV2'] = receipt / 1e9
      sm.payloads['modelV2'] = serialized_scene(stamp=event + 2_000_000_000)['modelV2']
      projector.project(
        sm, CP, now_mono_ns=event + 15_000_000, now_boot_ns=event + 2_015_000_000,
        sample_skew_ns=1000, safe_mode=False, owner_context=owner_context(event),
      )
      self.assertEqual(projector.curve_detector.last_observed_mono_ns, expected)
    sm.valid['modelV2'] = False
    result = projector.project(
      sm, CP, now_mono_ns=event + 16_000_000, now_boot_ns=event + 2_016_000_000,
      sample_skew_ns=1000, safe_mode=False, owner_context=owner_context(event),
    )
    self.assertIsNone(result.scene.curve_detected)
    self.assertEqual(projector.curve_detector.value, 0.0)
    self.assertIsNone(projector.curve_detector.last_observed_mono_ns)

  def test_joined_serialized_lead_tracks_once_per_model_tick(self):
    sm = FakeSubMaster(serialized_scene(origin=-6.64888977208733e-11), MONO - 5_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    for tick in range(15):
      event = MONO + 10_000_000 + tick * 50_000_000
      sm.stamp(event)
      sm.payloads['modelV2'] = serialized_scene(stamp=BOOT + 10_000_000 + tick * 50_000_000, origin=-6.64888977208733e-11)['modelV2']
      result = projector.project(
        sm,
        CP,
        now_mono_ns=event + 1_000_000,
        now_boot_ns=BOOT + 11_000_000 + tick * 50_000_000,
        sample_skew_ns=1000,
        safe_mode=True,
        selected_t_follow_s=1.45,
        selected_t_follow_observed_mono_ns=event,
      )
    self.assertTrue(result.raw_lead.present)
    self.assertTrue(result.lead_observation.tracked)
    self.assertTrue(result.lead_observation.following)
    self.assertTrue(result.scene.lead.tracked)
    self.assertTrue(result.scene.following_lead)
    self.assertEqual(result.model_horizon_m, 192.0)
    previous_filter = projector.lead_detector.filter_value
    repeated = projector.project(
      sm,
      CP,
      now_mono_ns=event + 2_000_000,
      now_boot_ns=BOOT + 12_000_000 + tick * 50_000_000,
      sample_skew_ns=1000,
      safe_mode=True,
      selected_t_follow_s=1.45,
      selected_t_follow_observed_mono_ns=event + 1_000_000,
    )
    self.assertTrue(repeated.raw_lead.present)
    self.assertIsNone(repeated.lead_observation.tracked)
    self.assertIsNone(repeated.scene.lead)
    self.assertEqual(projector.lead_detector.filter_value, previous_filter)

  def test_bend_does_not_shorten_the_stop_horizon(self):
    from openpilot.starpilot.conditional_mode.projection import _horizon
    model = messaging.new_message('modelV2').modelV2
    angles = [1.25 * index / 32 for index in range(33)]
    model.position.x = [200. * math.sin(angle) for angle in angles]
    model.position.y = [200. * (1. - math.cos(angle)) for angle in angles]
    self.assertLess(model.position.x[-1], 190.)
    self.assertAlmostEqual(_horizon(model), 250., delta=.5)
    model.position.y = [0.] * 33
    self.assertEqual(_horizon(model), model.position.x[-1])

  def test_stationary_clear_endpoint_does_not_relax_spatial_path_validation(self):
    from openpilot.starpilot.conditional_mode.projection import _horizon, _standstill_clear_horizon
    from openpilot.starpilot.conditional_mode.tests.test_stop_commit_transport import CLEAR_97_X
    model = messaging.new_message('modelV2').modelV2
    model.position.x = CLEAR_97_X
    self.assertIsNone(_horizon(model))
    self.assertEqual(_standstill_clear_horizon(model), CLEAR_97_X[-1])
    malformed = (list(CLEAR_97_X[:-1]), list(CLEAR_97_X), list(CLEAR_97_X), list(CLEAR_97_X), list(CLEAR_97_X))
    malformed[1][3] = -.011
    malformed[2][18] = malformed[2][17] - .001
    malformed[3][0] = -2e-6
    malformed[4][4] = float('nan')
    for values in malformed:
      with self.subTest(values=values):
        model.position.x = values
        self.assertIsNone(_standstill_clear_horizon(model))

  def test_headway_stale_and_real_negative_path_leave_raw_only(self):
    for origin, later_negative in ((-2e-6, False), (-6.64888977208733e-11, True)):
      sm = FakeSubMaster(serialized_scene(origin=origin, later_negative=later_negative), MONO - 5_000_000)
      projector = SceneProjector()
      projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
      sm.stamp(MONO + 10_000_000)
      result = projector.project(
        sm,
        CP,
        now_mono_ns=MONO + 12_000_000,
        now_boot_ns=BOOT + 12_000_000,
        sample_skew_ns=1000,
        safe_mode=True,
        selected_t_follow_s=1.45,
        selected_t_follow_observed_mono_ns=MONO + 10_000_000,
      )
      self.assertTrue(result.raw_lead.present)
      self.assertIsNone(result.model_horizon_m)
      self.assertIsNone(result.lead_observation.tracked)
    sm = FakeSubMaster(serialized_scene(origin=-6.64888977208733e-11), MONO - 5_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    sm.stamp(MONO + 10_000_000)
    stale = projector.project(
      sm,
      CP,
      now_mono_ns=MONO + 12_000_000,
      now_boot_ns=BOOT + 12_000_000,
      sample_skew_ns=1000,
      safe_mode=True,
      selected_t_follow_s=1.45,
      selected_t_follow_observed_mono_ns=MONO - 5_000_000,
    )
    self.assertTrue(stale.raw_lead.present)
    self.assertIsNone(stale.lead_observation.tracked)
    self.assertIsNone(stale.scene.following_lead)

  def test_malformed_lead_value_preserves_raw_presence(self):
    payloads = serialized_scene()
    invalid = messaging.new_message('radarState', valid=True)
    invalid.radarState.leadOne.present = True
    invalid.radarState.leadOne.radar = True
    invalid.radarState.leadOne.dRel = math.nan
    payloads['radarState'] = messaging.log_from_bytes(invalid.to_bytes()).radarState
    sm = FakeSubMaster(payloads, MONO - 5_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    sm.stamp(MONO + 10_000_000)
    result = projector.project(
      sm,
      CP,
      now_mono_ns=MONO + 12_000_000,
      now_boot_ns=BOOT + 12_000_000,
      sample_skew_ns=1000,
      selected_t_follow_s=1.45,
      selected_t_follow_observed_mono_ns=MONO + 10_000_000,
    )
    self.assertTrue(result.raw_lead.present)
    self.assertIsNone(result.raw_lead.distance_m)
    self.assertIsNone(result.lead_observation.tracked)
    self.assertIsNone(result.scene.lead)

  def test_joined_serialized_stop_filters_only_with_original_owner_sources(self):
    sm = FakeSubMaster(serialized_scene(horizon=40, lead_present=False), MONO - 5_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    for tick in range(28):
      event = MONO + 10_000_000 + tick * 50_000_000
      sm.stamp(event)
      sm.payloads['modelV2'] = serialized_scene(stamp=BOOT + 10_000_000 + tick * 50_000_000, horizon=40, lead_present=False)['modelV2']
      result = projector.project(
        sm,
        CP,
        now_mono_ns=event + 1_000_000,
        now_boot_ns=BOOT + 11_000_000 + tick * 50_000_000,
        sample_skew_ns=1000,
        safe_mode=True,
        owner_context=owner_context(event),
      )
    self.assertTrue(result.scene.stop_light_detected)
    self.assertFalse(result.scene.standstill_stop_hold)
    self.assertIsNone(result.scene.slow_lead_detected)  # No selected headway: unavailable, not a negative.
    self.assertEqual(result.stop_source_observed_mono_s, event / 1e9)
    filter_value = projector.stop_detector.light_filter.x
    repeated = projector.project(
      sm,
      CP,
      now_mono_ns=event + 2_000_000,
      now_boot_ns=BOOT + 12_000_000 + tick * 50_000_000,
      sample_skew_ns=1000,
      owner_context=owner_context(event + 1_000_000),
    )
    self.assertIsNone(repeated.scene.stop_light_detected)
    self.assertEqual(repeated.stop_source_observed_mono_s, event / 1e9)
    self.assertEqual(projector.stop_detector.light_filter.x, filter_value)
    lost_context = projector.project(
      sm,
      CP,
      now_mono_ns=event + 3_000_000,
      now_boot_ns=BOOT + 13_000_000 + tick * 50_000_000,
      sample_skew_ns=1000,
      owner_context=None,
    )
    self.assertIsNone(lost_context.scene.stop_light_detected)
    self.assertEqual(projector.stop_detector.light_filter.x, 0.0)
    next_event = event + 300_000_000
    sm.stamp(next_event)
    sm.payloads['modelV2'] = serialized_scene(stamp=BOOT + 10_000_000 + tick * 50_000_000 + 300_000_000, horizon=40, lead_present=False)['modelV2']
    stale_context = projector.project(
      sm,
      CP,
      now_mono_ns=next_event + 1_000_000,
      now_boot_ns=BOOT + 11_000_000 + tick * 50_000_000 + 300_000_000,
      sample_skew_ns=1000,
      owner_context=owner_context(event),
    )
    self.assertIsNone(stale_context.scene.stop_light_detected)
    self.assertIsNone(stale_context.scene.slow_lead_detected)
    self.assertEqual(stale_context.scene.speed_mps, 15.0)

  def test_joined_standstill_sign_hold_is_explicit_policy_evidence(self):
    sm = FakeSubMaster(serialized_scene(speed=0, horizon=192, lead_present=False, standstill=True), MONO - 5_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    event = MONO + 10_000_000
    sm.stamp(event)
    context = replace(owner_context(event), stop_sign_confirmed=ObservedBool(True, event))
    result = projector.project(
      sm,
      CP,
      now_mono_ns=event + 1_000_000,
      now_boot_ns=BOOT + 11_000_000,
      sample_skew_ns=1000,
      owner_context=context,
    )
    self.assertTrue(result.scene.stop_light_detected)
    self.assertTrue(result.scene.standstill_stop_hold)
    self.assertTrue(result.scene.stop_sign_confirmed)

  def test_joined_detector_stamp_is_oldest_original_input_not_poll_time(self):
    sm = FakeSubMaster(serialized_scene(horizon=40, lead_present=False), MONO - 5_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    event = MONO + 10_000_000
    sm.stamp(event)
    context = replace(owner_context(event), forcing_stop=ObservedBool(False, event - 2_000_000))
    first = projector.project(
      sm,
      CP,
      now_mono_ns=event + 1_000_000,
      now_boot_ns=BOOT + 11_000_000,
      sample_skew_ns=1000,
      owner_context=context,
    )
    self.assertIsNotNone(first.stop_source_observed_mono_s)
    assert first.stop_source_observed_mono_s is not None
    self.assertAlmostEqual(first.stop_source_observed_mono_s, (event - 2_000_000) / 1e9)
    repeated = projector.project(
      sm,
      CP,
      now_mono_ns=event + 2_000_000,
      now_boot_ns=BOOT + 12_000_000,
      sample_skew_ns=1000,
      owner_context=owner_context(event + 1_000_000),
    )
    self.assertIsNotNone(repeated.stop_source_observed_mono_s)
    assert repeated.stop_source_observed_mono_s is not None
    self.assertAlmostEqual(repeated.stop_source_observed_mono_s, (event - 2_000_000) / 1e9)
    self.assertIsNone(repeated.scene.stop_light_detected)

  def test_joined_model_cadence_with_half_rate_radar_and_owner_inputs(self):
    for speed, horizon, present, lead_speed, count in ((15.0, 40.0, False, 0.0, 28), (30.0, 192.0, True, 20.0, 50)):
      sm = FakeSubMaster(serialized_scene(speed=speed, horizon=horizon, lead_present=present, lead_distance=50, lead_speed=lead_speed), MONO - 5_000_000)
      projector = SceneProjector()
      projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
      for tick in range(count):
        event = MONO + 10_000_000 + tick * 50_000_000
        older = MONO + 10_000_000 + (tick // 2) * 100_000_000
        sm.stamp(event)
        sm.logMonoTime['radarState'] = older
        sm.recv_time['radarState'] = older / 1e9
        sm.payloads.update(
          serialized_scene(
            stamp=BOOT + 10_000_000 + tick * 50_000_000,
            speed=speed,
            horizon=horizon,
            lead_present=present,
            lead_distance=50,
            lead_speed=lead_speed,
          )
        )
        result = projector.project(
          sm,
          CP,
          now_mono_ns=event + 1_000_000,
          now_boot_ns=BOOT + 11_000_000 + tick * 50_000_000,
          sample_skew_ns=1000,
          selected_t_follow_s=1.45,
          selected_t_follow_observed_mono_ns=event,
          owner_context=owner_context(older),
        )
      if present:
        self.assertTrue(result.scene.slow_lead_detected)
        assert result.slower_source_observed_mono_s is not None
        assert projector.slower_lead_detector.last_model_tick_mono_s is not None
        self.assertAlmostEqual(result.slower_source_observed_mono_s, older / 1e9)
        self.assertAlmostEqual(projector.slower_lead_detector.last_model_tick_mono_s, event / 1e9)
      else:
        self.assertTrue(result.scene.stop_light_detected)
        assert result.stop_source_observed_mono_s is not None
        assert projector.stop_detector.last_model_tick_mono_s is not None
        self.assertAlmostEqual(result.stop_source_observed_mono_s, older / 1e9)
        self.assertAlmostEqual(projector.stop_detector.last_model_tick_mono_s, event / 1e9)
      expired_event = event + 300_000_000
      sm.stamp(expired_event)
      sm.payloads['modelV2'] = serialized_scene(
        stamp=BOOT + 10_000_000 + (count - 1) * 50_000_000 + 300_000_000,
        speed=speed,
        horizon=horizon,
        lead_present=present,
        lead_distance=50,
        lead_speed=lead_speed,
      )['modelV2']
      expired = projector.project(
        sm,
        CP,
        now_mono_ns=expired_event + 1_000_000,
        now_boot_ns=BOOT + 11_000_000 + (count - 1) * 50_000_000 + 300_000_000,
        sample_skew_ns=1000,
        selected_t_follow_s=1.45,
        selected_t_follow_observed_mono_ns=expired_event,
        owner_context=owner_context(older),
      )
      self.assertIsNone(expired.scene.slow_lead_detected)
      self.assertIsNone(expired.scene.stop_light_detected)
      self.assertEqual(expired.scene.speed_mps, speed)

  def test_joined_serialized_slower_lead_and_context_barriers(self):
    sm = FakeSubMaster(serialized_scene(speed=30, lead_distance=50, lead_speed=20), MONO - 5_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    for tick in range(48):
      event = MONO + 10_000_000 + tick * 50_000_000
      sm.stamp(event)
      sm.payloads['modelV2'] = serialized_scene(stamp=BOOT + 10_000_000 + tick * 50_000_000, speed=30, lead_distance=50, lead_speed=20)['modelV2']
      result = projector.project(
        sm,
        CP,
        now_mono_ns=event + 1_000_000,
        now_boot_ns=BOOT + 11_000_000 + tick * 50_000_000,
        sample_skew_ns=1000,
        safe_mode=True,
        selected_t_follow_s=1.45,
        selected_t_follow_observed_mono_ns=event,
        owner_context=owner_context(event),
      )
    self.assertTrue(result.scene.lead.tracked)
    self.assertTrue(result.scene.slow_lead_detected)
    self.assertEqual(result.slower_source_observed_mono_s, event / 1e9)
    self.assertEqual(result.scene.speed_mps, 30.0)
    # A stale owner flag invalidates only the detector that needs it; speed,
    # raw lead and independent stop/curve projection remain available.
    next_event = event + 50_000_000
    sm.stamp(next_event)
    sm.payloads['modelV2'] = serialized_scene(stamp=BOOT + 10_000_000 + 48 * 50_000_000, speed=30, lead_distance=50, lead_speed=20)['modelV2']
    stale = owner_context(next_event)
    stale = replace(stale, slower_option=ObservedBool(True, MONO - 5_000_000))
    invalid = projector.project(
      sm,
      CP,
      now_mono_ns=next_event + 1_000_000,
      now_boot_ns=BOOT + 11_000_000 + 48 * 50_000_000,
      sample_skew_ns=1000,
      safe_mode=True,
      selected_t_follow_s=1.45,
      selected_t_follow_observed_mono_ns=next_event,
      owner_context=stale,
    )
    self.assertIsNone(invalid.scene.slow_lead_detected)
    self.assertEqual(invalid.scene.speed_mps, 30.0)
    self.assertTrue(invalid.raw_lead.present)
    self.assertIsNotNone(invalid.scene.stop_light_detected)
    # Resume invalidates pre-suspend owner evidence even when its MONOTONIC
    # age is small. Fresh model and owner events are needed before rearming.
    self.assertFalse(
      projector.project(
        sm,
        CP,
        now_mono_ns=next_event + 20_000_000,
        now_boot_ns=BOOT + 101_000_000 + 48 * 50_000_000,
        sample_skew_ns=1000,
        owner_context=owner_context(next_event),
      ).ready
    )
    resumed_event = next_event + 25_000_000
    sm.stamp(resumed_event)
    sm.payloads['modelV2'] = serialized_scene(stamp=BOOT + 106_000_000 + 48 * 50_000_000, speed=30, lead_distance=50, lead_speed=20)['modelV2']
    resumed = projector.project(
      sm,
      CP,
      now_mono_ns=resumed_event + 1_000_000,
      now_boot_ns=BOOT + 107_000_000 + 48 * 50_000_000,
      sample_skew_ns=1000,
      selected_t_follow_s=1.45,
      selected_t_follow_observed_mono_ns=resumed_event,
      owner_context=owner_context(next_event),
    )
    self.assertIsNone(resumed.scene.slow_lead_detected)
    self.assertEqual(resumed.scene.speed_mps, 30.0)

  def test_joined_fresh_no_lead_releases_then_stale_radar_is_unknown(self):
    sm = FakeSubMaster(serialized_scene(speed=30, lead_distance=50, lead_speed=20), MONO - 5_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    for tick in range(70):
      event = MONO + 10_000_000 + tick * 50_000_000
      present = tick < 45
      sm.stamp(event)
      sm.payloads.update(
        serialized_scene(
          stamp=BOOT + 10_000_000 + tick * 50_000_000,
          speed=30,
          lead_present=present,
          lead_distance=50,
          lead_speed=20,
        )
      )
      result = projector.project(
        sm,
        CP,
        now_mono_ns=event + 1_000_000,
        now_boot_ns=BOOT + 11_000_000 + tick * 50_000_000,
        sample_skew_ns=1000,
        selected_t_follow_s=1.45,
        selected_t_follow_observed_mono_ns=event,
        owner_context=owner_context(event),
      )
      if tick == 44:
        self.assertTrue(result.scene.slow_lead_detected)
    self.assertFalse(result.raw_lead.present)
    self.assertFalse(result.scene.slow_lead_detected)
    self.assertFalse(result.scene.following_lead)
    sm.logMonoTime['radarState'] = event - 500_000_000
    sm.recv_time['radarState'] = (event - 500_000_000) / 1e9
    stale = projector.project(
      sm,
      CP,
      now_mono_ns=event + 2_000_000,
      now_boot_ns=BOOT + 12_000_000 + 69 * 50_000_000,
      sample_skew_ns=1000,
      selected_t_follow_s=1.45,
      selected_t_follow_observed_mono_ns=event,
      owner_context=owner_context(event),
    )
    self.assertIsNone(stale.scene.slow_lead_detected)
    self.assertIsNone(stale.raw_lead)
    self.assertEqual(stale.scene.speed_mps, 30.0)

  def test_chill_launch_uses_real_longitudinal_plan_not_model_action(self):
    sm = FakeSubMaster(serialized_scene(speed=0.0, standstill=True, lead_present=False), MONO + 10_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    context = owner_context(MONO + 10_000_000)
    launched = projector.project(
      sm, CP, now_mono_ns=MONO + 12_000_000, now_boot_ns=BOOT + 12_000_000, sample_skew_ns=1000,
      safe_mode=True, owner_context=context, selected_t_follow_s=1.45,
      selected_t_follow_observed_mono_ns=MONO + 10_000_000,
    )
    self.assertTrue(sm['modelV2'].action.shouldStop)  # Current model action is not frozen longitudinalPlan.shouldStop.
    self.assertTrue(launched.scene.launch_candidate)
    self.assertFalse(launched.scene.launch_forced_exit)
    self.assertFalse(launched.scene.launch_lead)
    self.assertFalse(launched.scene.low_speed_stop_scene)
    self.assertEqual(launched.chill_observation.lead_reason, 'no_lead')

    event = MONO + 60_000_000
    sm.stamp(event)
    sm.payloads.update(serialized_scene(stamp=BOOT + 60_000_000, speed=0.0, standstill=True,
                                        lead_present=False, plan_should_stop=True))
    stopped = projector.project(
      sm, CP, now_mono_ns=event + 1_000_000, now_boot_ns=BOOT + 61_000_000, sample_skew_ns=1000,
      safe_mode=True, owner_context=owner_context(event), selected_t_follow_s=1.45,
      selected_t_follow_observed_mono_ns=event,
    )
    self.assertFalse(stopped.scene.launch_candidate)
    self.assertTrue(stopped.scene.launch_forced_exit)
    self.assertEqual(stopped.chill_observation.speed_reason, 'longitudinal_stop')

  def test_same_cycle_native_plan_owner_without_loopback_service(self):
    event = MONO + 10_000_000
    sm = FakeSubMaster(serialized_scene(speed=0.0, standstill=True, lead_present=False), event)
    sm.seen['longitudinalPlan'] = False
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    context = replace(owner_context(event), plan_should_stop=ObservedBool(False, event),
                      plan_allow_throttle=ObservedBool(True, event))
    owned = projector.project(sm, CP, now_mono_ns=event + 2_000_000, now_boot_ns=BOOT + 12_000_000,
                              sample_skew_ns=1000, safe_mode=True, owner_context=context,
                              selected_t_follow_s=1.45, selected_t_follow_observed_mono_ns=event)
    self.assertTrue(owned.scene.launch_candidate)
    self.assertNotIn('longitudinalPlan', owned.fresh_services)
    old = replace(context, plan_should_stop=ObservedBool(False, MONO - 500_000_000))
    stale = projector.project(sm, CP, now_mono_ns=event + 3_000_000, now_boot_ns=BOOT + 13_000_000,
                              sample_skew_ns=1000, safe_mode=True, owner_context=old,
                              selected_t_follow_s=1.45, selected_t_follow_observed_mono_ns=event)
    self.assertIsNone(stale.scene.launch_candidate)

  def test_chill_stop_proof_survives_missing_launch_only_owners(self):
    sm = FakeSubMaster(serialized_scene(speed=0.0, horizon=30.0, lead_present=False), MONO + 10_000_000)
    sm.seen['longitudinalPlan'] = False
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    missing_plan = projector.project(
      sm, CP, now_mono_ns=MONO + 12_000_000, now_boot_ns=BOOT + 12_000_000, sample_skew_ns=1000,
      safe_mode=True, owner_context=replace(owner_context(MONO + 10_000_000), red_light=None),
      selected_t_follow_s=1.45, selected_t_follow_observed_mono_ns=MONO + 10_000_000,
    )
    self.assertTrue(missing_plan.ready)  # New optional plan never changes core readiness.
    self.assertTrue(missing_plan.scene.low_speed_stop_scene)
    self.assertIsNone(missing_plan.scene.launch_candidate)
    self.assertNotIn('longitudinalPlan', missing_plan.fresh_services)
    self.assertIsNone(missing_plan.scene.launch_lead)

    # Missing launch-only context cannot erase a fresh raw-model stop proof.
    sm.seen['longitudinalPlan'] = True
    event = MONO + 60_000_000
    sm.stamp(event)
    sm.payloads.update(serialized_scene(stamp=BOOT + 60_000_000, speed=0.0, horizon=30.0, lead_present=False))
    stale_red = projector.project(
      sm, CP, now_mono_ns=event + 1_000_000, now_boot_ns=BOOT + 61_000_000, sample_skew_ns=1000,
      safe_mode=True, owner_context=replace(owner_context(event),
                                            red_light=ObservedBool(False, MONO - 500_000_000)),
      selected_t_follow_s=1.45, selected_t_follow_observed_mono_ns=event,
    )
    self.assertIsNone(stale_red.scene.launch_candidate)
    self.assertTrue(stale_red.scene.low_speed_stop_scene)

  def test_chill_launch_requires_raw_lead_relative_speed(self):
    sm = FakeSubMaster(serialized_scene(speed=0.0, lead_present=True, lead_speed=0.8,
                                        lead_relative=0.05, lead_accel=0.2), MONO + 10_000_000)
    projector = SceneProjector()
    projector.project(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000)
    blocked = projector.project(
      sm, CP, now_mono_ns=MONO + 12_000_000, now_boot_ns=BOOT + 12_000_000, sample_skew_ns=1000,
      safe_mode=True, owner_context=owner_context(MONO + 10_000_000), selected_t_follow_s=1.45,
      selected_t_follow_observed_mono_ns=MONO + 10_000_000,
    )
    assert blocked.raw_lead is not None and blocked.raw_lead.relative_speed_mps is not None
    self.assertAlmostEqual(blocked.raw_lead.relative_speed_mps, 0.05)
    self.assertFalse(blocked.scene.launch_candidate)
    self.assertEqual(blocked.chill_observation.lead_reason, 'not_departing')
    event = MONO + 60_000_000
    sm.stamp(event)
    sm.payloads.update(serialized_scene(stamp=BOOT + 60_000_000, speed=0.0, lead_present=True,
                                        lead_speed=0.8, lead_relative=0.5, lead_accel=0.2))
    departure = projector.project(
      sm, CP, now_mono_ns=event + 1_000_000, now_boot_ns=BOOT + 61_000_000, sample_skew_ns=1000,
      safe_mode=True, owner_context=owner_context(event), selected_t_follow_s=1.45,
      selected_t_follow_observed_mono_ns=event,
    )
    self.assertTrue(departure.scene.launch_candidate)
    self.assertTrue(departure.scene.launch_lead)
    self.assertEqual(departure.chill_observation.lead_reason, 'departing')

  def test_host_warms_curve_with_only_stamped_owner_context(self):
    from openpilot.starpilot.conditional_mode.host import ConditionalModeHost
    from openpilot.starpilot.conditional_mode.policy import ManualIntent, ModeChoice, ModeSettings
    from openpilot.starpilot.conditional_mode.preferences import manual_for_drive, selection_for_drive

    sm = FakeSubMaster(serialized_scene(), MONO - 5_000_000)
    host = ConditionalModeHost()
    selection = selection_for_drive(ModeChoice.CCM, ModeSettings(), MONO)
    manual = manual_for_drive(ManualIntent.NONE, MONO, MONO + 1_000_000)
    host.sample(sm, CP, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000, drive_id=MONO,
                selection=selection, manual=manual, safe_mode=ObservedBool(False, MONO - 5_000_000))
    for tick in range(30):
      event = MONO + 10_000_000 + tick * 50_000_000
      sm.stamp(event)
      sm.payloads['modelV2'] = serialized_scene(stamp=BOOT + 10_000_000 + tick * 50_000_000)['modelV2']
      result = host.sample(
        sm, CP, now_mono_ns=event + 1_000_000, now_boot_ns=BOOT + 11_000_000 + tick * 50_000_000,
        sample_skew_ns=1000, drive_id=MONO, selection=selection, manual=manual,
        safe_mode=ObservedBool(False, event), owner_context=owner_context(event),
        selected_t_follow_s=1.45, selected_t_follow_observed_mono_ns=event,
      )
    self.assertTrue(result.projected.scene.curve_detected)
    self.assertGreater(host.projector.curve_detector.value, 0.6)


if __name__ == '__main__':
  unittest.main()
