"""Native pure-projection cache ordering/concurrency, complementary to prior parity proof."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import TypedDict
import unittest
from unittest.mock import patch

from openpilot.cereal import log
from openpilot.starpilot.longitudinal import toyota_output_policy as policy


class ProjectionArguments(TypedDict):
  message_ns: int
  receipt_ns: int
  now_ns: int
  drive_id: int
  valid: bool


class TestRadarLeadProjectionCache(unittest.TestCase):
  def setUp(self):
    with policy._RADAR_LEAD_LOCK:
      policy._RADAR_LEAD_ROWS.clear()
    self.kwargs: ProjectionArguments = {'message_ns': 1_000_000_000, 'receipt_ns': 1_001_000_000,
                   'now_ns': 1_002_000_000, 'drive_id': 900_000_000, 'valid': True}

  def tearDown(self):
    with policy._RADAR_LEAD_LOCK:
      policy._RADAR_LEAD_ROWS.clear()

  @staticmethod
  def radar(stamp, distance=10.):
    radar = log.RadarState.new_message()
    radar.mdMonoTime = stamp
    radar.leadOne.present = True
    radar.leadOne.dRel = distance
    return radar

  @staticmethod
  def key(radar):
    return radar.as_reader().as_builder().to_bytes()

  def test_exact_native_bytes_and_lru_recency(self):
    # All rows are equal; an unrelated native field still changes the exact key.
    radars = [self.radar(stamp) for stamp in range(9)]
    keys = [self.key(radar) for radar in radars]
    self.assertEqual(len(set(keys)), 9)
    for radar in radars[:8]:
      self.assertIsNotNone(policy.leads_from_radar(radar, **self.kwargs))
    policy.leads_from_radar(radars[0], **self.kwargs)
    policy.leads_from_radar(radars[8], **self.kwargs)
    with policy._RADAR_LEAD_LOCK:
      self.assertEqual(list(policy._RADAR_LEAD_ROWS), keys[2:8] + [keys[0], keys[8]])
      self.assertNotIn(keys[1], policy._RADAR_LEAD_ROWS)
      self.assertEqual(len(set(policy._RADAR_LEAD_ROWS.values())), 1)

  def test_concurrent_native_projection_retains_locked_bounded_rows(self):
    lock = policy._RADAR_LEAD_LOCK

    class GuardedRows(OrderedDict):
      # Assert every compound-operation primitive is inside the shared mutex.
      # The worker exercise also verifies concurrent output and retention bounds.
      def get(self, key, default=None):
        assert lock.locked()
        return super().get(key, default)

      def move_to_end(self, key, last=True):
        assert lock.locked()
        return super().move_to_end(key, last)

      def __setitem__(self, key, value):
        assert lock.locked()
        super().__setitem__(key, value)

      def popitem(self, last=True):
        assert lock.locked()
        return super().popitem(last)

    rows = GuardedRows()
    start = threading.Barrier(6)

    def project(worker):
      # Each thread owns its native builder; mutable builders are never shared.
      radar = self.radar(worker, worker + 1.)
      start.wait(timeout=10)
      for sequence in range(16):
        radar.mdMonoTime = worker * 100 + sequence
        for _ in range(5):
          leads = policy.leads_from_radar(radar, **self.kwargs)
          self.assertIsNotNone(leads)
          assert leads is not None
          self.assertEqual(leads[0], policy.Lead(True, worker + 1., 0., 0., 0.))
      return True

    with patch.object(policy, '_RADAR_LEAD_ROWS', rows):
      with ThreadPoolExecutor(max_workers=6) as workers:
        futures = [workers.submit(project, worker) for worker in range(6)]
        self.assertTrue(all(future.result(timeout=30) for future in futures))
      with lock:
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(type(key) is bytes and len(key) <= 4096 for key in rows))
        self.assertTrue(all(type(value) is tuple and all(type(row) is tuple for row in value) for value in rows.values()))


  def test_native_field_access_keeps_both_leads_and_rejects_invalid_values(self):
    for reader in (False, True):
      with self.subTest(reader=reader):
        radar = self.radar(1)
        radar.leadOne.dRel, radar.leadOne.yRel, radar.leadOne.vLead, radar.leadOne.aLeadK = 14.5, 1.25, 8.25, -.5
        radar.leadTwo.present = False
        radar.leadTwo.dRel, radar.leadTwo.yRel, radar.leadTwo.vLead, radar.leadTwo.aLeadK = 30.25, -2.5, 15.25, 1.5
        value = radar.as_reader() if reader else radar
        self.assertEqual(policy.leads_from_radar(value, **self.kwargs),
                         (policy.Lead(True, 14.5, 1.25, 8.25, -.5), policy.Lead(False, 30.25, -2.5, 15.25, 1.5)))
        radar.leadTwo.yRel = 21.
        self.assertIsNone(policy.leads_from_radar(value, **self.kwargs))
        radar.leadTwo.yRel = -2.5
        radar.leadOne.dRel = float('nan')
        self.assertIsNone(policy.leads_from_radar(value, **self.kwargs))
        radar.leadOne.dRel = 14.5
        invalid: ProjectionArguments = {**self.kwargs, 'valid': False}
        stale: ProjectionArguments = {**self.kwargs, 'now_ns': 2_000_000_000}
        self.assertIsNone(policy.leads_from_radar(value, **invalid))
        self.assertIsNone(policy.leads_from_radar(value, **stale))
        self.assertIsNotNone(policy.leads_from_radar(value, **self.kwargs))


if __name__ == '__main__':
  unittest.main()
