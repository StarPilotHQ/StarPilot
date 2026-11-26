"""Recorded20Hz stationary launch: release-only prefix, unchanged red-hold guards."""

import gzip
import json
from pathlib import Path
from openpilot.starpilot.conditional_mode.tests.test_stop_commit_transport import CLEAR_97_V, CLEAR_97_X
from openpilot.starpilot.longitudinal.tests.test_cem_green_release import CemGreenCallerTests


class RecordedLaunchTests(CemGreenCallerTests):
  def setUp(self):
    super().setUp()
    from unittest.mock import patch
    from openpilot.cereal import messaging
    from openpilot.selfdrive.modeld.constants import ModelConstants
    from openpilot.starpilot.longitudinal.tests import test_cem_green_release as green

    original = green.serialized_scene

    def timed_scene(**kwargs):
      payloads = original(**kwargs)
      event = messaging.new_message('modelV2')
      event.modelV2 = payloads['modelV2']
      event.modelV2.position.t = list(ModelConstants.T_IDXS)
      event.modelV2.velocity.t = list(ModelConstants.T_IDXS)
      event.modelV2.position.y = [0.] * ModelConstants.IDX_N
      payloads['modelV2'] = messaging.log_from_bytes(event.to_bytes()).modelV2
      if getattr(self, 'second_lead', False):
        radar = messaging.new_message('radarState')
        radar.radarState = payloads['radarState']
        radar.radarState.leadTwo.present = True
        radar.radarState.leadTwo.dRel = 5.0
        payloads['radarState'] = messaging.log_from_bytes(radar.to_bytes()).radarState
      return payloads

    self.enterContext(patch.object(green, 'serialized_scene', timed_scene))

  def test_recorded_20hz_launch(self):
    self.prime()
    samples = json.loads(gzip.decompress((Path(__file__).parent / 'testdata/route160_stationary_launch.json.gz').read_bytes()))
    release = None
    for frame in samples:
      _, _, held, plan = self.cycle(stopped=True, position=frame['x'], velocity=frame['v'], should_stop=frame['stop'], desired_accel=frame['accel'])
      if frame['stop']:
        self.assertTrue(held and plan.shouldStop)
      if not held and release is None:
        release = frame['t']
      if release is not None:
        self.assertFalse(held or plan.shouldStop)
        self.assertGreater(plan.aTarget, 0.0)
    assert release is not None
    self.assertLess(release, 5953.0)

  def test_candidate_frame_rejects_red_lead_and_lost_authority(self):
    samples = json.loads(gzip.decompress((Path(__file__).parent / 'testdata/route160_stationary_launch.json.gz').read_bytes()))
    frame = next(f for f in samples if f['accel'] > 0.35 and min(f['x']) < -0.02)
    for change in (
      {'should_stop': True},
      {'lead': True},
      {'aol': True},
      {'stale': 'carControl'},
      {'stale': 'selfdriveState'},
      {'repeat_model': True},
      {'lead_two': True},
    ):
      self.owner.mode.projector.stop_detector.reset()
      self.prime()
      self.second_lead = change.get('lead_two', False)
      for _ in range(18):
        kwargs = {'stopped': True, 'position': frame['x'], 'velocity': frame['v'], 'should_stop': False, 'desired_accel': frame['accel']}
        kwargs.update({k: v for k, v in change.items() if k != 'lead_two'})
        self.cycle(**kwargs)
        if change.get('repeat_model'):
          self.tick -= 1  # Keep source time fixed, not a newly stamped old-shaped frame.
      self.second_lead = False
      if change.get('aol'):
        # Existing disengagement behavior clears the detector; a subsequent
        # red stop must still acquire and hold after re-enabling.
        self.prime()
      else:
        self.assertTrue(self.owner.mode.projector.stop_detector.committed, change)

  def test_real_stop_request_remains_after_light_handoff(self):
    self.prime()
    samples = json.loads(gzip.decompress((Path(__file__).parent / 'testdata/route160_stationary_launch.json.gz').read_bytes()))
    frame = next(f for f in samples if f['accel'] > 0.35 and min(f['x']) < -0.02)
    _, _, held, plan = self.cycle(stopped=True, position=frame['x'], velocity=frame['v'], should_stop=False, desired_accel=frame['accel'])
    self.assertFalse(held or plan.shouldStop)
    _, _, _, plan = self.cycle(stopped=True, position=frame['x'], velocity=frame['v'], should_stop=True, desired_accel=frame['accel'])
    self.assertTrue(plan.shouldStop)

  def test_gradual_straight_launch_requires_sustained_model_clear(self):
    self.prime()
    forward = {'stopped': True, 'position': CLEAR_97_X, 'velocity': CLEAR_97_V, 'should_stop': False, 'desired_accel': .15}
    for _ in range(8):
      _, _, held, plan = self.cycle(**forward)
      self.assertTrue(held and plan.shouldStop)
    _, _, held, plan = self.cycle(**(forward | {'should_stop': True}))
    self.assertTrue(held and plan.shouldStop)
    for _ in range(10):
      _, _, held, plan = self.cycle(**forward)
      self.assertTrue(held and plan.shouldStop)
    for _ in range(3):
      _, _, held, plan = self.cycle(**forward)
      if not held:
        break
    self.assertFalse(held or plan.shouldStop)
    self.assertGreater(plan.aTarget, 0.)
    self.assertLessEqual(plan.aTarget, .16)
    _, _, held, plan = self.cycle(**(forward | {'should_stop': True}))
    self.assertTrue(held and plan.shouldStop)
