from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from openpilot.starpilot.galaxy.camera_exposure import CameraStates, ExposureReadiness, STATE_SERVICES


START = 1_000_000_000
STEP = 50_000_000


def report(frame_id, camera='wide', **changes):
  sof = START + frame_id * STEP
  fields = {'frameId': frame_id, 'timestampSof': sof, 'timestampEof': sof + 10_000_000,
            'measuredGreyFraction': .125, 'targetGreyFraction': .125, 'integLines': 1000, 'gain': 1., 'exposureValPercent': 50.}
  fields.update(changes)
  state = NS(**fields)
  return NS(valid=True, logMonoTime=sof + 20_000_000, which=lambda: STATE_SERVICES[camera], **{STATE_SERVICES[camera]: state})


class ExposureReadinessTest(unittest.TestCase):
  def observe(self, reader, frame_id, **changes):
    event = report(frame_id, **changes)
    state = getattr(event, reader.service)
    now = state.timestampEof + 10_000_000
    reader.observe_frame(frame_id, state.timestampSof, state.timestampEof)
    reader.observe([event], now)
    return reader.ready(frame_id, state.timestampSof, state.timestampEof, now)

  def warmed(self, camera='wide'):
    reader = ExposureReadiness(camera, START)
    for frame_id in range(3, 26):
      ready = self.observe(reader, frame_id, camera=camera)
      self.assertEqual(ready, frame_id == 25)
    return reader

  def test_requires_sustained_convergence_and_post_confirmation_exposure(self):
    for camera in ('wide', 'cabin'):
      with self.subTest(camera=camera):
        reader = self.warmed(camera)
        self.assertTrue(self.observe(reader, 26, camera=camera))
        self.assertFalse(reader.ready(24, START + 24 * STEP, START + 24 * STEP + 10_000_000, START + 26 * STEP))

  def test_latest_unsettled_report_in_same_batch_revokes_readiness(self):
    reader = self.warmed()
    events = [report(26), report(27, measuredGreyFraction=.02)]
    for event in events:
      state = event.wideRoadCameraState
      reader.observe_frame(state.frameId, state.timestampSof, state.timestampEof)
    reader.observe(events, events[-1].logMonoTime)
    state = events[-1].wideRoadCameraState
    self.assertFalse(reader.ready(27, state.timestampSof, state.timestampEof, events[-1].logMonoTime))
    self.assertIsNone(reader.ready_from)

  def test_one_target_crossing_does_not_count_as_convergence(self):
    reader = ExposureReadiness('wide', START)
    for frame_id in range(3, 40):
      self.assertFalse(self.observe(reader, frame_id, measuredGreyFraction=.125 if frame_id == 25 else .02))

  def test_saturated_or_changing_exposure_does_not_fast_path(self):
    for changes in ({'measuredGreyFraction': .02, 'exposureValPercent': 100.},
                    {'measuredGreyFraction': .9, 'exposureValPercent': 0.},
                    {'measuredGreyFraction': .098}, {'gain': 2.}):
      with self.subTest(changes=changes):
        reader = self.warmed()
        self.assertFalse(self.observe(reader, 26, **changes))
    reader = ExposureReadiness('wide', START)
    for frame_id in range(3, 45):
      self.assertFalse(self.observe(reader, frame_id, integLines=1000 + frame_id * 100))

  def test_invalid_default_or_nonfinite_exposure_never_counts(self):
    faults = ({'measuredGreyFraction': 0., 'targetGreyFraction': 0.}, {'targetGreyFraction': -1.},
              {'targetGreyFraction': float('nan')}, {'measuredGreyFraction': float('inf')},
              {'measuredGreyFraction': -1.}, {'gain': 0.}, {'gain': float('nan')},
              {'integLines': 0}, {'integLines': -1}, {'integLines': True},
              {'exposureValPercent': float('nan')}, {'exposureValPercent': 101.})
    for changes in faults:
      with self.subTest(changes=changes):
        reader = self.warmed()
        self.assertFalse(self.observe(reader, 26, **changes))

  def test_repeated_states_cannot_create_a_stability_window(self):
    reader = ExposureReadiness('wide', START)
    event = report(3)
    state = event.wideRoadCameraState
    reader.observe_frame(3, state.timestampSof, state.timestampEof)
    reader.observe([event] * 30, event.logMonoTime)
    self.assertIsNone(reader.ready_from)
    self.assertEqual(len(reader.ev), 0)

  def test_skipped_or_reversed_states_reset_the_window(self):
    for frame_id in (24, 28):
      with self.subTest(frame_id=frame_id):
        reader = self.warmed()
        event = report(frame_id)
        state = event.wideRoadCameraState
        if frame_id == 28:
          reader.observe_frame(frame_id, state.timestampSof, state.timestampEof)
        reader.observe([event], START + 28 * STEP)
        self.assertIsNone(reader.ready_from)

  def test_state_must_match_observed_frame_and_current_envelope(self):
    for fault in ('unobserved', 'mismatch', 'stale', 'future', 'reversed', 'pre_request', 'invalid', 'wrong_camera', 'old_envelope'):
      with self.subTest(fault=fault):
        reader = self.warmed()
        event = report(26)
        state = event.wideRoadCameraState
        now = event.logMonoTime
        if fault != 'unobserved':
          reader.observe_frame(26, state.timestampSof, state.timestampEof)
        if fault == 'mismatch':
          state.timestampSof += 1
        elif fault == 'stale':
          now += 600_000_000
        elif fault == 'future':
          state.timestampEof = now + 1
        elif fault == 'reversed':
          state.timestampEof = state.timestampSof - 1
        elif fault == 'pre_request':
          state.timestampSof = START
        elif fault == 'invalid':
          event.valid = False
        elif fault == 'wrong_camera':
          event.which = lambda: 'cabinCameraState'
        elif fault == 'old_envelope':
          event.logMonoTime = START
        reader.observe([event], now)
        self.assertIsNone(reader.ready_from)

  def test_fresh_frame_after_camera_restart_cannot_reuse_old_metadata(self):
    reader = self.warmed()
    old = report(3)
    sof = START + 30 * STEP
    reader.observe_frame(3, sof, sof + 10_000_000)
    reader.observe([old], sof + 20_000_000)
    self.assertFalse(reader.ready(3, sof, sof + 10_000_000, sof + 20_000_000))
    self.assertIsNone(reader.ready_from)

  def test_ready_metadata_cannot_authorize_unrelated_later_frame(self):
    reader = self.warmed()
    event = report(28)
    state = event.wideRoadCameraState
    reader.observe_frame(28, state.timestampSof, state.timestampEof)
    self.assertFalse(reader.ready(28, state.timestampSof, state.timestampEof, event.logMonoTime))
    self.assertFalse(reader.ready(25, START + 25 * STEP, START + 25 * STEP + 10_000_000, START + 40 * STEP))

  def test_native_serialized_event_and_one_frame_publication_lag(self):
    from openpilot.cereal import messaging, log
    reader = ExposureReadiness('wide', START)
    pending = None
    for frame_id in range(3, 27):
      event = report(frame_id)
      state = event.wideRoadCameraState
      reader.observe_frame(frame_id, state.timestampSof, state.timestampEof)
      if pending is not None:
        with log.Event.from_bytes(pending) as decoded:
          reader.observe([decoded], event.logMonoTime)
      self.assertEqual(reader.ready(frame_id, state.timestampSof, state.timestampEof, event.logMonoTime), frame_id >= 25)
      native = messaging.new_message('wideRoadCameraState', valid=True, logMonoTime=event.logMonoTime)
      for name, value in vars(state).items():
        setattr(native.wideRoadCameraState, name, value)
      pending = native.to_bytes()

  def test_state_reader_bounds_backlog_and_closes_socket(self):
    reader = CameraStates.__new__(CameraStates)
    calls = []
    with patch.object(reader, 'sock', NS(close=lambda: calls.append('closed')), create=True):
      with patch.object(reader, 'messaging', NS(recv_one_or_none=lambda sock: calls.append('read') or object()), create=True):
        self.assertEqual(len(reader()), 32)
      self.assertEqual(len(calls), 32)
      reader.close()
      self.assertEqual(calls[-1], 'closed')
