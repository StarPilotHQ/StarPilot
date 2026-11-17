"""Actual current RadarState/ModelV2 wire and frozen lead-transition evidence."""

import unittest

from openpilot.cereal import messaging
from openpilot.starpilot.lead_tracking import (
  LeadDetector,
  _radarless_follow_window,
  _should_hold_vision,
  _should_track,
)


def wire_lead(*, present=True, radar=True, distance=30.0, speed=12.0, accel=-0.1, probability=0.95, lateral=0.0):
  event = messaging.new_message('radarState', valid=True)
  lead = event.radarState.leadOne
  lead.present = present
  lead.radar = radar
  lead.dRel = distance
  lead.vLead = speed
  lead.aLeadK = accel
  lead.modelProb = probability
  lead.yRel = lateral
  return messaging.log_from_bytes(event.to_bytes()).radarState.leadOne


def wire_model(*, horizon=192.0, lateral=0.0, points=33, origin=0.0, later_negative=False):
  event = messaging.new_message('modelV2', valid=True)
  xs = [float(index) * horizon / 32 for index in range(points)]
  if xs:
    xs[0] = origin
  if later_negative and len(xs) > 5:
    xs[5] = -1e-5
  event.modelV2.position.x = xs
  event.modelV2.position.y = [lateral] * points
  return messaging.log_from_bytes(event.to_bytes()).modelV2


def step(
  detector, tick, *, lead=None, model=None, speed=15.0, headway=1.45, standstill=False, radar_fresh=True, model_fresh=True, car_fresh=True, headway_fresh=True
):
  return detector.step(
    lead if lead is not None else wire_lead(),
    model if model is not None else wire_model(),
    speed_mps=speed,
    t_follow_s=headway,
    standstill=standstill,
    observed_mono_s=100.0 + tick * 0.05,
    now_mono_s=100.005 + tick * 0.05,
    radar_fresh=radar_fresh,
    model_fresh=model_fresh,
    car_fresh=car_fresh,
    headway_fresh=headway_fresh,
  )


class TestLeadDetector(unittest.TestCase):
  def test_frozen_executable_predicate_reference(self):
    # Values from executing frozen 678af783/selfdrive/controls/lib/lead_behavior.py
    # should_track_lead, should_hold_tracked_vision_lead, and matched-follow.
    self.assertTrue(_should_track(True, 30, 192, 15, 12, True))
    self.assertFalse(_should_track(True, 60, 70, 25, 24, False))
    self.assertTrue(_should_hold_vision(True, 60, 70, 25, 0.96, 0, 0, False))
    self.assertFalse(_should_hold_vision(True, 100, 70, 25, 0.96, 0, 0, False))
    self.assertFalse(_should_hold_vision(True, 60, 70, 25, 0.96, 2, 0, False))
    self.assertTrue(_radarless_follow_window(25, 35, 24, 1.45, False, 0, 0.95))
    self.assertFalse(_radarless_follow_window(25, 60, 24, 1.45, False, 0, 0.95))

  def test_filter_acquisition_loss_reacquisition_and_standstill(self):
    detector = LeadDetector()
    states = [step(detector, tick) for tick in range(15)]
    self.assertTrue(all(state.raw_present for state in states))
    self.assertFalse(states[0].tracked)
    self.assertTrue(states[-1].tracked)
    self.assertTrue(states[-1].following)
    # Frozen planner does not update tracking while at a full standstill.
    held = step(detector, 15, lead=wire_lead(present=False, distance=0), standstill=True)
    self.assertTrue(held.tracked)
    # The old following formula still sees the retained filtered track here.
    self.assertTrue(held.following)
    losses = [step(detector, tick, lead=wire_lead(present=False, distance=0)) for tick in range(16, 35)]
    self.assertFalse(losses[-1].tracked)
    self.assertFalse(losses[-1].following)
    reacquired = [step(detector, tick) for tick in range(35, 50)]
    self.assertTrue(reacquired[-1].tracked)

  def test_explicit_current_headway_changes_following_not_raw_presence(self):
    detector = LeadDetector()
    lead = wire_lead(distance=40)
    for tick in range(15):
      last = step(detector, tick, lead=lead, speed=15, headway=1.5)
    self.assertTrue(last.tracked)
    self.assertTrue(last.following)  # 40 < 2*1.5*15.
    changed = step(detector, 15, lead=lead, speed=15, headway=1.0)
    self.assertTrue(changed.raw_present)
    self.assertTrue(changed.tracked)
    self.assertFalse(changed.following)  # 40 >= 2*1.0*15.

  def test_close_stopped_lead_and_radarless_matched_continuity(self):
    stopped = LeadDetector()
    for tick in range(15):
      observed = step(stopped, tick, lead=wire_lead(distance=5, speed=0), speed=2.0)
    self.assertTrue(observed.raw_present)
    self.assertTrue(observed.tracked)
    self.assertTrue(observed.following)

    radarless = LeadDetector()
    for tick in range(15):
      observed = step(radarless, tick, lead=wire_lead(radar=False, distance=30, speed=24), speed=25)
    self.assertTrue(observed.tracked)
    # dRel=55 is beyond frozen initial vision entrance but in the matched
    # follow window; lateral offset defeats vision hold, so only its 0.45 s
    # radarless continuity latch keeps the tracking filter fed.
    continuity = step(radarless, 15, lead=wire_lead(radar=False, distance=55, speed=24, lateral=2.0), speed=25)
    self.assertTrue(continuity.tracked)
    self.assertTrue(radarless.radarless_hold_until_s > 100.75)

  def test_missing_headway_and_model_keep_raw_presence_only(self):
    detector = LeadDetector()
    known = step(detector, 0)
    self.assertTrue(known.raw_present)
    unknown_headway = step(detector, 1, headway=None, headway_fresh=False)
    self.assertTrue(unknown_headway.raw_present)
    self.assertIsNone(unknown_headway.tracked)
    self.assertIsNone(unknown_headway.following)
    unknown_model = step(detector, 2, model=wire_model(points=0))
    self.assertTrue(unknown_model.raw_present)
    self.assertIsNone(unknown_model.tracked)
    self.assertIsNone(unknown_model.following)
    stale_radar = step(detector, 3, radar_fresh=False)
    self.assertIsNone(stale_radar.raw_present)
    self.assertIsNone(stale_radar.tracked)

  def test_repeated_source_and_gap_cannot_reuse_consensus(self):
    detector = LeadDetector()
    for tick in range(15):
      last = step(detector, tick)
    self.assertTrue(last.tracked)
    duplicate = detector.step(
      wire_lead(),
      wire_model(),
      speed_mps=15,
      t_follow_s=1.45,
      standstill=False,
      observed_mono_s=100.7,
      now_mono_s=100.705,
      radar_fresh=True,
      model_fresh=True,
      car_fresh=True,
      headway_fresh=True,
    )
    self.assertIsNone(duplicate.tracked)
    self.assertTrue(detector.tracked)
    gap = detector.step(
      wire_lead(),
      wire_model(),
      speed_mps=15,
      t_follow_s=1.45,
      standstill=False,
      observed_mono_s=101.0,
      now_mono_s=101.005,
      radar_fresh=True,
      model_fresh=True,
      car_fresh=True,
      headway_fresh=True,
    )
    self.assertIsNone(gap.tracked)
    self.assertFalse(detector.tracked)
    self.assertFalse(step(detector, 21).tracked)

  def test_old_source_age_and_zero_inactive_headway(self):
    detector = LeadDetector()
    for tick in range(15):
      last = step(detector, tick, headway=0.0)
    self.assertTrue(last.raw_present)
    self.assertTrue(last.tracked)
    self.assertFalse(last.following)
    stale = detector.step(
      wire_lead(),
      wire_model(),
      speed_mps=15.0,
      t_follow_s=1.45,
      standstill=False,
      observed_mono_s=100.7,
      now_mono_s=101.0,
      radar_fresh=True,
      model_fresh=True,
      car_fresh=True,
      headway_fresh=True,
    )
    self.assertTrue(stale.raw_present)
    self.assertIsNone(stale.tracked)
    self.assertFalse(detector.tracked)

  def test_stopped_track_retains_only_fresh_bounded_origin_geometry(self):
    for variation in ('clear', 'quantized', 'stale', 'moving', 'bad_y', 'tail_backstep', 'flag_speed'):
      detector = LeadDetector()
      for tick in range(15):
        step(detector, tick, lead=wire_lead(distance=5, speed=0), speed=2.)
      self.assertTrue(detector.tracked)
      event = messaging.new_message('modelV2', valid=True)
      xs = [70. * index / 32 for index in range(33)]
      xs[1:3] = [0.0005, 0.0002]
      if variation == 'tail_backstep':
        xs[20] = xs[19] - .001
      event.modelV2.position.x = xs
      event.modelV2.position.y = [float('nan') if variation == 'bad_y' else 0.] * 33
      model = messaging.log_from_bytes(event.to_bytes()).modelV2
      observation = step(detector, 15, model=model, lead=wire_lead(distance=5, speed=.8),
                         speed={'moving': .5, 'flag_speed': .5, 'quantized': .044}.get(variation, 0.),
                         standstill=variation != 'moving', model_fresh=variation != 'stale')
      if variation in ('clear', 'quantized'):
        self.assertTrue(observation.tracked)
      else:
        self.assertIsNone(observation.tracked)

  def test_curved_path_keeps_lead_state_known(self):
    event = messaging.new_message('modelV2', valid=True)
    xs = [245. * index / 32 for index in range(33)]
    event.modelV2.position.x = xs
    event.modelV2.position.y = [x * x / 460. for x in xs]
    curved = messaging.log_from_bytes(event.to_bytes()).modelV2
    detector = LeadDetector()
    clear = [step(detector, tick, lead=wire_lead(present=False, distance=0, speed=0), model=curved, speed=24.6)
             for tick in range(3)]
    self.assertTrue(all(observation.tracked is False and observation.following is False for observation in clear))

  def test_serialized_model_origin_roundoff_only(self):
    detector = LeadDetector()
    roundoff = step(detector, 0, model=wire_model(origin=-6.64888977208733e-11))
    self.assertFalse(roundoff.tracked)  # A valid first filter tick.
    self.assertIsNotNone(detector.last_observed_mono_s)
    for model in (wire_model(origin=-2e-6), wire_model(later_negative=True)):
      invalid = step(detector, 1, model=model)
      self.assertTrue(invalid.raw_present)
      self.assertIsNone(invalid.tracked)
      self.assertIsNone(detector.last_observed_mono_s)


if __name__ == '__main__':
  unittest.main()
