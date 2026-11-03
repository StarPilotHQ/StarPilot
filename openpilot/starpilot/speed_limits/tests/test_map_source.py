"""Actual host cereal fixtures through the disabled map-source boundary."""

import base64
import json
import math
import unittest
from dataclasses import replace
from pathlib import Path

from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits import map_source as ms
from openpilot.starpilot.speed_limits import selection as sel


ROOT = Path(__file__).resolve().parents[4]


def v2_frame() -> ms.MapFrame:
  fixture = json.loads((ROOT / 'mapd_repo/cereal/testdata/mapd_v1_wire.json').read_text())
  return replace(ms.decode_event(base64.b64decode(fixture['event_base64'])), version=2)


class MapSourceTests(unittest.TestCase):
  def test_diagnostic_metadata_follows_accepted_order_only(self):
    frame = v2_frame()
    now = 456_100_000_000
    tracker = ms.MapTracker()
    tracker.step(frame, received=True, now_boot_ns=now)
    self.assertEqual(tracker.diagnostic(now_boot_ns=now), ms.MapDiagnostic(frame, 0, 0))
    old_loss = replace(frame, status='noGps', event_valid=False, speed_mps=0.0, way_id=0,
                       tile_loaded=False)
    tracker.step(old_loss, received=True, now_boot_ns=now+1)
    self.assertEqual(tracker.diagnostic(now_boot_ns=now+1).frame, frame)
    switched = replace(frame, source='internal', generation=frame.generation+1,
                       event_mono_ns=frame.event_mono_ns+2, computed_mono_ns=frame.computed_mono_ns+2)
    tracker.step(switched, received=True, now_boot_ns=now+2)
    self.assertEqual(tracker.diagnostic(now_boot_ns=now+2), ms.MapDiagnostic(switched, 0, 1))
    restarted = replace(switched, producer_session=frame.producer_session+1, generation=1,
                        event_mono_ns=frame.event_mono_ns+3, computed_mono_ns=frame.computed_mono_ns+3)
    tracker.step(restarted, received=True, now_boot_ns=now+3)
    self.assertEqual(tracker.diagnostic(now_boot_ns=now+3), ms.MapDiagnostic(restarted, 1, 1))
    self.assertIsNone(tracker.diagnostic(now_boot_ns=now+ms.EVENT_MAX_AGE_NS+4).frame)
    tracker.step(None, received=False, now_boot_ns=now+ms.EVENT_MAX_AGE_NS+4)
    self.assertIsNone(tracker.diagnostic(now_boot_ns=now+ms.EVENT_MAX_AGE_NS+4).frame)

  def test_can_gps_retains_source_age_and_unqualified_map_authority(self):
    frame = replace(v2_frame(), source='car', gps_mono_ns=453_600_000_000)
    now = 456_100_000_000
    result = ms.MapTracker().step(frame, received=True, now_boot_ns=now)
    self.assertEqual(result.kind, 'matched_limit_unqualified')
    self.assertIs(result.observation.kind, acc.ObservationKind.UNKNOWN)
    self.assertIsNone(result.observation.candidate)
    self.assertEqual(ms.MapTracker().step(frame, received=True, now_boot_ns=now+1).kind, 'stale')

  def test_real_provider_and_historical_wire_are_copied_immutably(self):
    frame = v2_frame()
    self.assertEqual((frame.version, frame.status, frame.source), (2, 'matchedLimit', 'external'))
    self.assertEqual((frame.gps_mono_ns, frame.computed_mono_ns, frame.event_mono_ns),
                     (455_950_000_000, 455_990_000_000, 456_000_000_000))
    self.assertEqual((frame.generation, frame.producer_session), (3, 0x1234abcd5011))
    result = ms.MapTracker().step(frame, received=True, now_boot_ns=456_100_000_000)
    self.assertEqual(result.kind, 'matched_limit_unqualified')
    assert result.speed_mps is not None
    self.assertAlmostEqual(result.speed_mps, 13.4112, places=4)
    self.assertIs(result.observation.kind, acc.ObservationKind.UNKNOWN)
    self.assertIsNone(result.observation.candidate)
    v1_fixture = json.loads((ROOT / 'mapd_repo/cereal/testdata/mapd_v1_wire.json').read_text())
    v1 = ms.decode_event(base64.b64decode(v1_fixture['event_base64']))
    self.assertEqual(v1.version, 1)
    self.assertEqual(ms.MapTracker().step(v1, received=True, now_boot_ns=456_100_000_000).kind, 'unknown')
    fixture = json.loads((ROOT / 'openpilot/starpilot/tests/fixtures/mapd_wire.json').read_text())
    old = ms.decode_event(base64.b64decode(fixture['historical']['event_base64']))
    self.assertEqual(old.version, 0)
    self.assertEqual(ms.MapTracker().step(old, received=True, now_boot_ns=123_100_000_000).kind, 'unknown')
    with self.assertRaises(ValueError):
      ms.decode_event(b'not capnp')
    with self.assertRaises(ValueError):
      ms.decode_event(b'\x00' * (ms.MAX_EVENT_BYTES + 1))

  def test_time_boundaries_and_malformed_types(self):
    frame = v2_frame()
    now = frame.computed_mono_ns + ms.COMPUTED_MAX_AGE_NS
    self.assertEqual(ms.MapTracker().step(frame, received=True, now_boot_ns=now).kind, 'matched_limit_unqualified')
    self.assertEqual(ms.MapTracker().step(frame, received=True, now_boot_ns=now+1).kind, 'stale')
    for changed in (
      replace(frame, event_mono_ns=now+1), replace(frame, computed_mono_ns=now+1),
      replace(frame, gps_mono_ns=now+1), replace(frame, gps_mono_ns=frame.gps_mono_ns-500_000_001),
      replace(frame, event_mono_ns=True), replace(frame, computed_mono_ns=True),
      replace(frame, gps_mono_ns=True), replace(frame, producer_session=True),
      replace(frame, generation=True), replace(frame, version=True),
    ):
      with self.subTest(changed=changed):
        self.assertNotIn(ms.MapTracker().step(changed, received=True, now_boot_ns=now).kind,
                         ('matched_limit_unqualified', 'matched_no_limit_unqualified'))
    with self.assertRaises(ValueError):
      ms.MapTracker().step(frame, received=True, now_boot_ns=True)

  def test_matched_no_limit_is_not_absence_or_control(self):
    frame = v2_frame()
    no_limit = replace(frame, status='matchedNoLimit', speed_mps=0.0)
    result = ms.MapTracker().step(no_limit, received=True, now_boot_ns=456_100_000_000)
    self.assertEqual(result.kind, 'matched_no_limit_unqualified')
    self.assertIsNone(result.speed_mps)
    self.assertIs(result.observation.kind, acc.ObservationKind.UNKNOWN)
    for changed in (replace(no_limit, speed_mps=13), replace(frame, speed_mps=0),
                    replace(frame, speed_mps=10 ** 400),
                    replace(frame, speed_mps=math.nan), replace(frame, event_valid=False),
                    replace(frame, tile_loaded=False), replace(frame, way_id=0),
                    replace(frame, way_selection='fail'), replace(frame, way_selection='bogus'),
                    replace(frame, status='unknown'),
                    replace(frame, version=1)):
      with self.subTest(changed=changed):
        self.assertEqual(ms.MapTracker().step(changed, received=True, now_boot_ns=456_100_000_000).kind, 'unknown')

  def test_coherent_coverage_and_match_loss_clear_without_becoming_absent(self):
    frame = v2_frame()
    for status in ('noCoverage', 'noMatch'):
      tracker = ms.MapTracker()
      tracker.step(frame, received=True, now_boot_ns=456_100_000_000)
      loss = replace(frame, status=status, event_valid=False, speed_mps=0.0, way_id=0,
                     tile_loaded=False, event_mono_ns=frame.event_mono_ns+1,
                     computed_mono_ns=frame.computed_mono_ns+1)
      result = tracker.step(loss, received=True, now_boot_ns=456_100_000_001)
      self.assertEqual(result.kind, 'loss')
      self.assertIs(result.observation.kind, acc.ObservationKind.UNKNOWN)
      self.assertIsNone(result.observation.candidate)
      self.assertEqual(tracker.step(frame, received=True, now_boot_ns=456_100_000_002).kind, 'loss')

  def test_old_packets_do_not_renew_or_revoke_new_loss_clears(self):
    frame = v2_frame()
    tracker = ms.MapTracker()
    now = 456_100_000_000
    tracker.step(frame, received=True, now_boot_ns=now)
    self.assertEqual(tracker.step(replace(frame, status='noGps', event_valid=False, speed_mps=0.0, way_id=0,
                                          tile_loaded=False), received=True, now_boot_ns=now+1).kind,
                     'matched_limit_unqualified')  # duplicate timestamp is ignored
    loss = replace(frame, status='noGps', event_valid=False, speed_mps=0.0, way_id=0, tile_loaded=False,
                   event_mono_ns=frame.event_mono_ns+1, computed_mono_ns=frame.computed_mono_ns+1)
    self.assertEqual(tracker.step(loss, received=True, now_boot_ns=now+2).kind, 'loss')
    self.assertEqual(tracker.step(frame, received=True, now_boot_ns=now+3).kind, 'loss')
    self.assertIs(tracker.step(None, received=False, now_boot_ns=now+ms.EVENT_MAX_AGE_NS+2).observation.kind,
                  acc.ObservationKind.STALE)
    self.assertIs(tracker.step(None, received=False, now_boot_ns=now+ms.EVENT_MAX_AGE_NS+3).observation.kind,
                  acc.ObservationKind.UNKNOWN)

  def test_source_generation_restart_and_clock_regression(self):
    frame = v2_frame()
    now = 456_100_000_000
    tracker = ms.MapTracker()
    tracker.step(frame, received=True, now_boot_ns=now)
    bad_source = replace(frame, source='internal', event_mono_ns=frame.event_mono_ns+1,
                         computed_mono_ns=frame.computed_mono_ns+1)
    self.assertEqual(tracker.step(bad_source, received=True, now_boot_ns=now+1).kind, 'unknown')
    self.assertEqual(tracker.step(frame, received=True, now_boot_ns=now+2).kind, 'unknown')  # blocked until reset
    tracker.reset()
    self.assertEqual(tracker.step(frame, received=True, now_boot_ns=now).kind, 'matched_limit_unqualified')
    switched = replace(frame, source='internal', generation=4, event_mono_ns=frame.event_mono_ns+1,
                       computed_mono_ns=frame.computed_mono_ns+1)
    self.assertEqual(tracker.step(switched, received=True, now_boot_ns=now+1).kind, 'matched_limit_unqualified')
    restarted = replace(switched, producer_session=frame.producer_session+1,
                        event_mono_ns=frame.event_mono_ns+2, computed_mono_ns=frame.computed_mono_ns+2)
    self.assertEqual(tracker.step(restarted, received=True, now_boot_ns=now+2).kind, 'matched_limit_unqualified')
    late_old = replace(frame, event_mono_ns=frame.event_mono_ns+3, computed_mono_ns=frame.computed_mono_ns+3)
    self.assertEqual(tracker.step(late_old, received=True, now_boot_ns=now+3).kind, 'matched_limit_unqualified')
    self.assertEqual(tracker.step(None, received=False, now_boot_ns=now-1).kind, 'unknown')
    self.assertEqual(tracker.step(restarted, received=True, now_boot_ns=now+4).kind, 'unknown')
    tracker.reset()
    self.assertEqual(tracker.step(restarted, received=True, now_boot_ns=now+4).kind, 'matched_limit_unqualified')

  def test_future_packet_does_not_poison_order_and_generation_regression_blocks(self):
    frame = v2_frame()
    now = 456_100_000_000
    tracker = ms.MapTracker()
    self.assertEqual(tracker.step(frame, received=True, now_boot_ns=now).kind, 'matched_limit_unqualified')
    future = replace(frame, event_mono_ns=now+1_000_000_000)
    self.assertEqual(tracker.step(future, received=True, now_boot_ns=now+1).kind, 'unknown')
    recovered = replace(frame, event_mono_ns=frame.event_mono_ns+1,
                        computed_mono_ns=frame.computed_mono_ns+1)
    self.assertEqual(tracker.step(recovered, received=True, now_boot_ns=now+2).kind, 'matched_limit_unqualified')
    newer = replace(recovered, generation=4, event_mono_ns=recovered.event_mono_ns+1,
                    computed_mono_ns=recovered.computed_mono_ns+1)
    self.assertEqual(tracker.step(newer, received=True, now_boot_ns=now+3).kind, 'matched_limit_unqualified')
    regressed = replace(newer, generation=3, event_mono_ns=newer.event_mono_ns+1,
                        computed_mono_ns=newer.computed_mono_ns+1)
    self.assertEqual(tracker.step(regressed, received=True, now_boot_ns=now+4).kind, 'unknown')
    self.assertEqual(tracker.step(newer, received=True, now_boot_ns=now+5).kind, 'unknown')

  def test_bounded_retired_sessions_fail_closed(self):
    frame = v2_frame()
    tracker = ms.MapTracker()
    now = 456_100_000_000
    for i in range(ms.MAX_RETIRED_SESSIONS+2):
      current = replace(frame, producer_session=100+i, event_mono_ns=frame.event_mono_ns+i,
                        computed_mono_ns=frame.computed_mono_ns+i)
      result = tracker.step(current, received=True, now_boot_ns=now+i)
    self.assertEqual(result.kind, 'unknown')
    self.assertEqual(tracker.step(frame, received=True, now_boot_ns=now+100).kind, 'unknown')

  def test_direct_wire_ranges_are_rejected_without_conversion(self):
    frame = v2_frame()
    fields = {
      'event_mono_ns': (1 << 64), 'gps_mono_ns': (1 << 64),
      'computed_mono_ns': (1 << 64), 'generation': (1 << 64),
      'producer_session': (1 << 64), 'version': (1 << 16),
      'way_id': (1 << 63), 'speed_mps': 1e100,
    }
    for field, value in fields.items():
      with self.subTest(field=field):
        result = ms.MapTracker().step(replace(frame, **{field: value}), received=True,
                                      now_boot_ns=456_100_000_000)
        self.assertEqual(result.kind, 'unknown')
    self.assertEqual(ms.MapTracker().step(replace(frame, way_id=-(1 << 63)-1), received=True,
                                          now_boot_ns=456_100_000_000).kind, 'unknown')
    self.assertEqual(ms.MapTracker().step(replace(frame, way_id=-1), received=True,
                                          now_boot_ns=456_100_000_000).kind, 'unknown')

  def test_no_gps_retained_source_requires_coherent_timestamp(self):
    frame = v2_frame()
    loss = replace(frame, status='noGps', event_valid=False, speed_mps=0.0,
                   tile_loaded=False, way_id=0)
    now = 456_100_000_000
    self.assertEqual(ms.MapTracker().step(replace(loss, source='none', gps_mono_ns=0),
                                          received=True, now_boot_ns=now).kind, 'loss')
    for source in ('external', 'internal'):
      self.assertEqual(ms.MapTracker().step(replace(loss, source=source),
                                            received=True, now_boot_ns=now).kind, 'loss')
      for gps_mono_ns in (0, loss.computed_mono_ns+1):
        with self.subTest(source=source, gps_mono_ns=gps_mono_ns):
          self.assertEqual(ms.MapTracker().step(replace(loss, source=source, gps_mono_ns=gps_mono_ns),
                                                received=True, now_boot_ns=now).kind, 'unknown')
    self.assertEqual(ms.MapTracker().step(replace(loss, source='none'),
                                          received=True, now_boot_ns=now).kind, 'unknown')

  def test_unsupported_wire_does_not_poison_ordering(self):
    frame = v2_frame()
    now = 456_100_000_000
    for changes in ({'version': 1}, {'status': 'unknown'}, {'source': 'unknown'}):
      with self.subTest(changes=changes):
        tracker = ms.MapTracker()
        self.assertEqual(tracker.step(frame, received=True, now_boot_ns=now).kind,
                         'matched_limit_unqualified')
        unsupported = replace(frame, producer_session=frame.producer_session+1, generation=4,
                              event_mono_ns=frame.event_mono_ns+5,
                              computed_mono_ns=frame.computed_mono_ns+5, **changes)
        self.assertEqual(tracker.step(unsupported, received=True, now_boot_ns=now+5).kind, 'unknown')
        valid = replace(frame, producer_session=frame.producer_session+1, generation=4,
                        event_mono_ns=frame.event_mono_ns+1,
                        computed_mono_ns=frame.computed_mono_ns+1)
        self.assertEqual(tracker.step(valid, received=True, now_boot_ns=now+6).kind,
                         'matched_limit_unqualified')

  def test_unqualified_map_cannot_replace_accepted_history(self):
    result = ms.MapTracker().step(v2_frame(), received=True, now_boot_ns=456_100_000_000)
    observations = {source: acc.Observation(acc.ObservationKind.ABSENT) for source in sel.Source}
    observations[sel.Source.MAP] = result.observation
    chosen = sel.select_limit(observations, sel.SelectionPolicy(sel.SelectionMode.ORDERED, (sel.Source.MAP, None), False))
    self.assertIsNone(chosen.selected_source)
    self.assertIs(chosen.observation.kind, acc.ObservationKind.UNKNOWN)
    prior_candidate = acc.Candidate('dashboard', acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value='old-road'), 20.0)
    prior = acc.AcceptedLimit(prior_candidate, 'drive', 0)
    authority = acc.Authority(acc.Mode.COMBINED, acc.LongitudinalOwner.SYSTEM, True, True, False, False)
    decision = acc.step(acc.new_session('drive', prior), chosen.observation, authority,
                        acc.Policy(), now_ns=456_100_000_000)
    self.assertEqual(decision.control_target_mps, prior.candidate.speed_mps)
    self.assertEqual(decision.basis, 'unknown')
    self.assertEqual(decision.state.accepted, prior)
    self.assertIsNone(decision.history_write)


if __name__ == '__main__':
  unittest.main()
