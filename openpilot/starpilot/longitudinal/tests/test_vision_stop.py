import unittest

from openpilot.starpilot.longitudinal.tests.test_force_stop_runtime import ForceStopRuntimeTests, Frame, BASE
from openpilot.starpilot.speed_limits.vision.tests.test_stop_observation import evidence, message, BOX


class TestVisionForceStop(unittest.TestCase):
  setUp = ForceStopRuntimeTests.setUp
  sample = ForceStopRuntimeTests.sample

  def frame(self, tick, *, positive=True, **changes):
    sm = Frame(tick, horizon=45.)
    sm['modelV2'].velocity.x = [10.] * 33
    sample = evidence(BASE + (tick // 10) * 500_000_000, frame=1 + (tick // 10) * 10,
                      boxes=(BOX,) if positive else (), **changes)
    event = message(sample, now=sm.stamp)
    service = 'slcVisionObservation'
    sm[service] = event.slcVisionObservation
    sm.valid[service] = sm.alive[service] = True
    sm.logMonoTime[service] = sm.stamp
    sm.recv_time[service] = sm.stamp / 1e9
    sm.seen = {service: True}
    sm.recv_frame = {service: tick + 2}
    return sm

  def test_distinct_vision_frames_enter_existing_sign_hold_and_resume(self):
    for action in ('pedal_override', 'resume_requested'):
      self.owner.reset()
      for tick in range(35):
        plan = self.sample(self.frame(tick))
      self.assertTrue(plan.forcing)
      self.assertTrue(self.owner.policy.sign_confirmed)
      for tick in range(35, 435):
        sm = self.frame(tick, positive=False)
        sm['carState'].vEgo = 0.
        sm['carState'].standstill = True
        sm['modelV2'].position.x = [190. * i / 32 for i in range(33)]
        plan = self.sample(sm)
        self.assertTrue(plan.manual_hold)
      sm = self.frame(435, positive=False)
      sm['carState'].vEgo = 0.
      sm['carState'].standstill = True
      if action == 'pedal_override':
        sm['carState'].gasPressed = True
      else:
        self.owner.resume.consume = lambda **kwargs: True
      plan = self.sample(sm)
      self.assertFalse(plan.forcing)
      self.assertFalse(plan.manual_hold)

  def test_old_or_wrong_identity_vision_cannot_acquire_sign(self):
    for mutate in ('stale', 'wrong_model', 'single_frame', 'no_long_authority', 'aging'):
      self.owner.reset()
      for tick in range(35):
        sm = self.frame(tick)
        if mutate == 'stale':
          sm.recv_time['slcVisionObservation'] = (BASE - 2_000_000_000) / 1e9
        elif mutate == 'wrong_model':
          raw = sm['slcVisionObservation'].as_builder()
          raw.vision.modelId = 'wrong'
          sm['slcVisionObservation'] = raw
        elif mutate == 'single_frame':
          sm['slcVisionObservation'] = message(evidence(), now=sm.stamp).slcVisionObservation
        elif mutate == 'aging':
          raw = sm['slcVisionObservation'].as_builder()
          sample = raw.vision.stopSigns
          sample.observedMonoTime -= 900_000_000
          sample.cameraFrameEofBootTime -= 900_000_000
          sample.validUntilMonoTime -= 900_000_000
          sm['slcVisionObservation'] = raw
        else:
          sm['carControl'].longActive = False
        self.sample(sm)
      self.assertFalse(self.owner.policy.sign_confirmed, mutate)

  def test_force_stop_vision_works_with_slc_and_speed_display_disabled(self):
    self.params.put_bool('SpeedLimitController', False, block=True)
    self.params.put_bool('ShowSpeedLimits', False, block=True)
    self.params.put('SLCPriority1', 'Dashboard', block=True)
    self.params.put('SLCPriority2', 'Map Data', block=True)
    for tick in range(35):
      sm = self.frame(tick)
      sm['modelV2'].action.shouldStop = False
      plan = self.sample(sm)
    self.assertTrue(plan.forcing)
    self.assertTrue(self.owner.policy.sign_confirmed)
