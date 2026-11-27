import math
import tempfile
import unittest
from pathlib import Path

from opendbc.car.structs import car
from openpilot.cereal import log
from openpilot.starpilot.lateral import parked_runtime as runtime
from openpilot.starpilot.lateral.parked_runtime import ParkedClearanceHost, ParkedSettings, read_settings
from openpilot.starpilot.lateral.lane_centering import LaneCenteringController, RELEASE_TAU
from openpilot.starpilot.lateral.tests.test_lane_centering import model as lane_model, observation as lane_observation, settled

STEP_NS = 50_000_000


class Bus:
  def __init__(self):
    self.data = {}
    self.logMonoTime = {}
    self.valid = {}

  def __getitem__(self, name):
    return self.data[name]

  def put(self, name, message, stamp, valid=True):
    self.data[name], self.logMonoTime[name], self.valid[name] = message, stamp, valid


def model_message(left_line=-1.7, line_prob=0.9):
  model = log.ModelDataV2.new_message()
  xs = [float(x) for x in range(0, 100, 3)]
  model.position.x, model.position.y = xs, [0.0] * len(xs)
  lines = model.init('laneLines', 4)
  for line, y in zip(lines, (-5.0, left_line, 1.7, 5.0), strict=True):
    line.x, line.y = xs, [y] * len(xs)
  model.laneLineProbs, model.laneLineStds = [0.9, line_prob, 0.9, 0.9], [0.1] * 4
  edges = model.init('roadEdges', 2)
  for edge, y in zip(edges, (-6.0, 6.0), strict=True):
    edge.x, edge.y = xs, [y] * len(xs)
  model.roadEdgeStds = [0.3, 0.3]
  return model.as_reader()


def radar_message(points):
  radar = car.RadarData.new_message()
  built = radar.init('points', len(points))
  for target, (identifier, distance, y_rel, v_rel) in zip(built, points, strict=True):
    target.trackId, target.dRel, target.yRel, target.vRel = identifier, distance, y_rel, v_rel
  return radar.as_reader()


def car_message(speed=11.0, **fields):
  state = car.CarState.new_message(vEgo=speed, canValid=True, **fields)
  return state.as_reader()


def drive(host, seconds, points, *, speed=11.0, model=None, active=True, maneuver=False, start=1_000_000_000, **fields):
  bus = Bus()
  model = model_message() if model is None else model
  correction = 0.0
  for index in range(round(seconds / 0.05)):
    now = start + index * STEP_NS
    bus.put('carState', car_message(speed, **fields), now)
    bus.put('modelV2', model, now)
    bus.put('radarTracks', radar_message(points), now)
    correction = host.update(bus, now_ns=now, lateral_active=active, maneuver=maneuver)
  return correction, bus, start + round(seconds / 0.05) * STEP_NS


def host(enabled=True, gap=1.5, limit=runtime.DEFAULT_LIMIT):
  return ParkedClearanceHost(None, settings_reader=lambda: ParkedSettings(enabled, gap, limit))


PARKED = [(7, 20.0, -2.6, -11.0)]


class TestParkedRuntime(unittest.TestCase):
  def test_disabled_by_default_adds_nothing(self):
    self.assertEqual(ParkedSettings().enabled, False)
    self.assertEqual(drive(host(enabled=False), 3.0, PARKED)[0], 0.0)

  def test_parked_car_steers_left_within_cap(self):
    subject = host()
    correction = drive(subject, 4.0, PARKED)[0]
    self.assertEqual(correction, -runtime.MAX_CORRECTION)
    self.assertEqual(subject.reason, 'parked')
    subject = host(gap=1.4)
    correction = drive(subject, 4.0, [(7, 20.0, -3.1, -11.0)])[0]
    self.assertAlmostEqual(subject.detector.shift, 0.1, places=5)
    self.assertAlmostEqual(correction, -2.0 * 0.1 / 11.0 ** 2 * runtime.GAIN, places=7)

  def test_limit_setting_scales_cap(self):
    small, large = host(limit=0.1), host(gap=2.0, limit=0.5)
    self.assertAlmostEqual(drive(small, 4.0, PARKED)[0], -2.0 * 0.1 / 11.0 ** 2 * runtime.GAIN, places=7)
    self.assertAlmostEqual(small.shift, 0.1, places=5)
    wide = model_message(line_prob=0.05)
    self.assertAlmostEqual(drive(large, 6.0, PARKED, model=wide)[0], -runtime.MAX_CORRECTION * 0.5 / 0.3, places=7)
    self.assertLessEqual(large.shift, 0.5)

  def test_clear_road_and_far_objects_add_nothing(self):
    self.assertEqual(drive(host(), 3.0, [])[0], 0.0)
    self.assertEqual(drive(host(), 3.0, [(7, 20.0, -4.5, -11.0), (8, 20.0, 2.6, -11.0), (9, 20.0, -2.6, 0.0)])[0], 0.0)

  def test_no_left_room_adds_nothing(self):
    self.assertEqual(drive(host(), 3.0, PARKED, model=model_message(left_line=-0.9))[0], 0.0)

  def test_oncoming_keeps_wider_line_spacing(self):
    oncoming = PARKED + [(9, 90.0, 2.5, -22.0)]
    self.assertLess(drive(host(), 4.0, oncoming)[0], 0.0)
    narrow = model_message(left_line=-1.05)
    self.assertLess(drive(host(), 4.0, PARKED, model=narrow)[0], 0.0)
    self.assertEqual(drive(host(), 4.0, oncoming, model=narrow)[0], 0.0)
    unmarked = model_message(line_prob=0.05)
    self.assertLess(drive(host(), 4.0, PARKED, model=unmarked)[0], 0.0)
    self.assertEqual(drive(host(), 4.0, oncoming, model=unmarked)[0], 0.0)

  def test_driver_and_authority_gates_reset(self):
    for fields in ({'steeringPressed': True}, {'leftBlinker': True}, {'rightBlinker': True}, {'canTimeout': True}, {'leftBlindspot': True}):
      self.assertEqual(drive(host(), 3.0, PARKED, **fields)[0], 0.0)
    self.assertEqual(drive(host(), 3.0, PARKED, active=False)[0], 0.0)
    self.assertEqual(drive(host(), 3.0, PARKED, maneuver=True)[0], 0.0)
    subject = host()
    _, bus, now = drive(subject, 4.0, PARKED)
    bus.put('carState', car_message(steeringPressed=True), now)
    bus.put('radarTracks', radar_message(PARKED), now)
    bus.put('modelV2', bus['modelV2'], now)
    self.assertGreater(subject.shift, 0.0)
    self.assertEqual(subject.shift, subject.detector.shift)
    self.assertEqual(subject.update(bus, now_ns=now, lateral_active=True, maneuver=False), 0.0)
    self.assertEqual(subject.detector.shift, 0.0)
    self.assertEqual(subject.shift, 0.0)

  def test_blinker_keeps_watching_and_resumes_without_reconfirm(self):
    subject = host()
    correction, _, now = drive(subject, 2.0, PARKED, leftBlinker=True)
    self.assertEqual(correction, 0.0)
    self.assertEqual(subject.shift, 0.0)
    self.assertLess(drive(subject, 0.25, PARKED, start=now)[0], 0.0)

  def test_stale_or_invalid_inputs_reset(self):
    for name in ParkedClearanceHost.REQUIRED:
      subject = host()
      _, bus, now = drive(subject, 4.0, PARKED)
      self.assertEqual(subject.update(bus, now_ns=now + 10 * STEP_NS, lateral_active=True, maneuver=False), 0.0)
      subject = host()
      _, bus, now = drive(subject, 4.0, PARKED)
      for other in ParkedClearanceHost.REQUIRED:
        bus.put(other, bus[other], now, valid=other != name)
      self.assertEqual(subject.update(bus, now_ns=now, lateral_active=True, maneuver=False), 0.0)
      self.assertEqual(subject.reason, f'{name}_stale')

  def test_lane_change_and_speed_window(self):
    model = model_message().as_builder()
    model.meta.laneChangeState = log.LaneChangeState.laneChangeStarting
    self.assertEqual(drive(host(), 3.0, PARKED, model=model.as_reader())[0], 0.0)
    self.assertEqual(drive(host(), 3.0, [(7, 20.0, -2.6, -25.0)], speed=25.0)[0], 0.0)

  def test_held_between_radar_samples(self):
    subject = host()
    correction, bus, now = drive(subject, 4.0, PARKED)
    bus.put('carState', car_message(), now)
    self.assertEqual(subject.update(bus, now_ns=now, lateral_active=True, maneuver=False), correction)

  def test_curving_path_adds_nothing(self):
    model = model_message().as_builder()
    model.position.y = [0.15 * x for x in model.position.x]
    subject = host()
    self.assertEqual(drive(subject, 3.0, [(7, 20.0, -5.6, -11.0)], model=model.as_reader())[0], 0.0)
    self.assertEqual(subject.reason, 'curve')

  def test_malformed_model_adds_nothing(self):
    self.assertEqual(drive(host(), 3.0, PARKED, model=log.ModelDataV2.new_message().as_reader())[0], 0.0)

  def test_saved_settings(self):
    class Saved:
      def __init__(self, root):
        self.root = root

      def get_param_path(self, key):
        return str(Path(self.root) / key)

    with tempfile.TemporaryDirectory() as root:
      saved = Saved(root)
      self.assertEqual(read_settings(saved), ParkedSettings(False, runtime.DEFAULT_GAP))
      (Path(root) / 'ParkedClearance').write_bytes(b'1')
      self.assertEqual(read_settings(saved), ParkedSettings(True, runtime.DEFAULT_GAP))
      (Path(root) / 'ParkedClearanceGap').write_bytes(b'1.2')
      self.assertEqual(read_settings(saved), ParkedSettings(True, 1.2))
      for bad in (b'nan', b'0', b'9', b'x'):
        (Path(root) / 'ParkedClearanceGap').write_bytes(bad)
        self.assertEqual(read_settings(saved), ParkedSettings())
      (Path(root) / 'ParkedClearanceGap').write_bytes(b'1.2')
      for side, expected in ((b'0', ParkedSettings(True, 1.2)), (b'1', ParkedSettings()), (b'x', ParkedSettings())):
        (Path(root) / 'IsRhdDetected').write_bytes(side)
        self.assertEqual(read_settings(saved), expected)
      (Path(root) / 'IsRhdDetected').unlink()
      (Path(root) / 'ParkedClearanceShift').write_bytes(b'0.45')
      self.assertEqual(read_settings(saved), ParkedSettings(True, 1.2, 0.45))
      for bad in (b'nan', b'0', b'0.6', b'x'):
        (Path(root) / 'ParkedClearanceShift').write_bytes(bad)
        self.assertEqual(read_settings(saved), ParkedSettings())
      (Path(root) / 'ParkedClearanceShift').unlink()
      (Path(root) / 'ParkedClearance').write_bytes(b'yes')
      self.assertEqual(read_settings(saved), ParkedSettings())


class TestParkedLaneCentering(unittest.TestCase):
  def test_hold_fades_lane_centering_and_resumes(self):
    lanes = lane_model(left=-1.5, right=2.1)
    controller = LaneCenteringController()
    start = previous = settled(controller, lane_observation(lanes)).correction
    self.assertGreater(start, 0.0)
    for _ in range(100):
      result = controller.update(lane_observation(lanes, hold=True))
      self.assertEqual(result.reason, 'parked_clearance')
      self.assertIsNotNone(result.candidate_curvature)
      self.assertLessEqual(result.correction, previous)
      self.assertGreaterEqual(result.correction, previous * math.exp(-0.01 / RELEASE_TAU) - 1e-15)
      previous = result.correction
    self.assertLess(previous, 0.01 * start)
    resumed = settled(controller, lane_observation(lanes))
    self.assertNotEqual(resumed.reason, 'parked_clearance')
    self.assertAlmostEqual(resumed.correction, start, delta=0.05 * start)


if __name__ == '__main__':
  unittest.main()
