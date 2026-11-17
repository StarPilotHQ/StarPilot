"""Current SI schedules, zero defaults and invalid-source boundaries."""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from openpilot.common.params import Params
from openpilot.starpilot.speed_limits import offset_document as od
from openpilot.starpilot.speed_limits.runtime_settings import parse, read_params


def offset_at(schedule, speed):
  return next((band.offset_mps for band in schedule.bands
               if band.lower_mps <= speed and (band.upper_mps is None or speed < band.upper_mps)), None)


class OffsetDocumentTests(unittest.TestCase):
  def test_all_schedule_edges_and_zero_tail(self):
    epsilon = 0.00001
    for metric, bounds, conversion in ((False, od.IMPERIAL_BOUNDS, od.MPH_TO_MPS),
                                       (True, od.METRIC_BOUNDS, od.KPH_TO_MPS)):
      with self.subTest(metric=metric):
        document = od.OffsetDocument(bounds, tuple(index * conversion for index in range(1, 8)))
        values = {'SpeedLimitController': True, 'SLCOffsetSchedule': document}
        schedule = parse(values).offsets
        self.assertEqual(len(schedule.bands), 8)
        for index in range(1, 8):
          edge = bounds[index]
          with self.subTest(edge=edge):
            self.assertAlmostEqual(offset_at(schedule, edge - epsilon), index * conversion)
            expected = (index + 1) * conversion if index < 7 else 0.0
            self.assertAlmostEqual(offset_at(schedule, edge), expected)
            self.assertAlmostEqual(offset_at(schedule, edge + epsilon), expected)
        self.assertEqual(schedule.bands[-1].lower_mps, bounds[-1])
        self.assertEqual(schedule.bands[-1].offset_mps, 0.0)

  def test_adopted_document_is_sole_numeric_authority(self):
    document = od.OffsetDocument(od.IMPERIAL_BOUNDS, tuple(value * od.MPH_TO_MPS for value in (0, 1, -2, 3, 4, 5, 7)))
    encoded = od.encode(document)
    self.assertEqual(od.decode(encoded), document)
    expected = document.schedule()
    for metric in (False, True, b'corrupt'):
      settings = parse({'SpeedLimitController': True, 'SLCOffsetSchedule': document,
                        'IsMetric': metric, 'Offset3': float('nan'), 'Offset7': 'bad'})
      self.assertTrue(settings.enabled)
      self.assertEqual(settings.offsets, expected)
    self.assertEqual(offset_at(expected, od.IMPERIAL_BOUNDS[-1]), 0.0)

    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('SpeedLimitController', True, block=True)
      Path(params.get_param_path('SLCOffsetSchedule')).write_bytes(encoded)
      Path(params.get_param_path('IsMetric')).write_bytes(b'bad')
      Path(params.get_param_path('Offset3')).write_bytes(b'bad')
      original_read = Path.read_bytes
      def no_legacy_read(path):
        if path.name == 'IsMetric' or path.name.startswith('Offset'):
          raise AssertionError(f'adopted runtime read legacy numeric source: {path.name}')
        return original_read(path)
      with mock.patch.object(Path, 'read_bytes', no_legacy_read):
        self.assertEqual(read_params(params).offsets, expected)
        self.assertTrue(read_params(params).enabled)

  def test_needs_review_marker_prevents_legacy_reactivation(self):
    self.assertIs(od.decode(od.encode(od.NEEDS_REVIEW)), od.NEEDS_REVIEW)
    settings = parse({'SpeedLimitController': True, 'SLCOffsetSchedule': od.NEEDS_REVIEW})
    self.assertFalse(settings.enabled)
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('SpeedLimitController', True, block=True)
      Path(params.get_param_path('SLCOffsetSchedule')).write_bytes(od.encode(od.NEEDS_REVIEW))
      params.put_bool('IsMetric', True, block=True)
      self.assertFalse(read_params(params).enabled)

  def test_registered_json_params_roundtrip_and_preserved_legacy_bytes(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('SpeedLimitController', True, block=True)
      legacy = Path(params.get_param_path('Offset7'))
      legacy.write_bytes(b'5.0000')
      document = od.OffsetDocument(od.IMPERIAL_BOUNDS, tuple(value * od.MPH_TO_MPS for value in (0, 1, 2, 3, 4, 5, 5)))
      params.put('SLCOffsetSchedule', od.to_value(document), block=True)
      self.assertEqual(params.get('SLCOffsetSchedule'), od.to_value(document))
      self.assertEqual(read_params(params).offsets, document.schedule())
      self.assertEqual(legacy.read_bytes(), b'5.0000')
      params.put('SLCOffsetSchedule', od.to_value(od.NEEDS_REVIEW), block=True)
      self.assertEqual(params.get('SLCOffsetSchedule'), od.to_value(od.NEEDS_REVIEW))
      self.assertFalse(read_params(params).enabled)
      self.assertEqual(legacy.read_bytes(), b'5.0000')

  def test_malformed_documents_never_fall_back_to_legacy(self):
    bad = (b'', b'{', b'\xff', b'{}', b'{"version":2}', b'{"version":1,"state":"other"}',
           b'{"version":1,"state":"needs_review","extra":1}',
           b'{"version":1,"version":1,"state":"needs_review"}',
           b'{"version":1,"bounds_mps":[0,1,2,3,4,5,6,7],"offsets_mps":[0,0,0,0,0,0,NaN]}',
           b'[' * 1100 + b'0' + b']' * 1100,
           b'x' * (od.MAX_DOCUMENT_BYTES + 1),
           b'x' * (1024 * 1024))
    for raw in bad:
      with self.subTest(raw=raw[:30]):
        with self.assertRaises(ValueError):
          od.decode(raw)
        with tempfile.TemporaryDirectory() as directory:
          params = Params(directory)
          params.put_bool('SpeedLimitController', True, block=True)
          Path(params.get_param_path('SLCOffsetSchedule')).write_bytes(raw)
          self.assertFalse(read_params(params).enabled)
    valid = od.default_document(True)
    for altered in ({'version': True, 'bounds_mps': valid.bounds_mps, 'offsets_mps': valid.offsets_mps},
                    {'version': 1, 'bounds_mps': (0, 1, 1, 3, 4, 5, 6, 7), 'offsets_mps': valid.offsets_mps},
                    {'version': 1, 'bounds_mps': valid.bounds_mps, 'offsets_mps': (0, 0, 0, 0, 0, 0, True)}):
      with self.assertRaises(ValueError):
        od.validate(altered)


if __name__ == '__main__':
  unittest.main()
