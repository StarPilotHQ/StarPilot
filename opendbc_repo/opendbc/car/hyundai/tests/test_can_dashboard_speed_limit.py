import unittest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.dashboard_speed_limit import Status, hyundai_can_sign, parser_expiry
from opendbc.car.hyundai.carstate import CarState
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC

KPH, MPH = 1 / 3.6, 0.44704
NAV, LKAS12 = 'Navi_HU', 'LKAS12'
NAV_SIG, TSR_SIG = 'SpeedLim_Nav_Clu', 'CF_Lkas_TsrSpeed_Display_Clu'  # TSR only used to prove LKAS12 is ignored
TICK_NS = 10_000_000  # 100 Hz bus frames; Navi_HU is sent far slower, like the car (about 5 Hz)


def build(car=CAR.HYUNDAI_SONATA, fingerprint=None):
  cp = CarInterface.get_params(car, fingerprint or gen_empty_fingerprint(), [], False, False, False)
  state = CarState(cp)
  return cp, state, state.get_can_parsers(cp), CANPacker(DBC[cp.carFingerprint][Bus.pt])


class Drive:
  """Feeds every declared message each tick (so canValid is healthy) except the speed limit sources, which are scripted."""

  def __init__(self, car=CAR.HYUNDAI_SONATA, metric=False):
    self.cp, self.state, self.parsers, self.packer = build(car)
    self.metric, self.tick = metric, 0
    self.out = None
    self.valid = []

  def step(self, nav=None, tsr=None, nav_every=20, tsr_every=10, send_nav=True, send_tsr=True):
    frames = []
    for parser in self.parsers.values():
      for address in parser.addresses:
        name = parser.dbc.addr_to_msg[address].name
        if name in (NAV, LKAS12):
          continue
        values = {'COUNTER': self.tick % 16} if 'COUNTER' in parser.dbc.addr_to_msg[address].sigs else {}
        if name == 'CLU11':
          values['CF_Clu_SPEED_UNIT'] = 0 if self.metric else 1
        frames.append(self.packer.make_can_msg(name, parser.bus, values))
    if send_nav and nav is not None and self.tick % nav_every == 0:
      frames.append(self.packer.make_can_msg(NAV, 0, {NAV_SIG: nav}))
    if send_tsr and tsr is not None and self.tick % tsr_every == 0:
      frames.append(self.packer.make_can_msg(LKAS12, 2, {TSR_SIG: tsr}))
    now = 1_000_000_000 + self.tick * TICK_NS
    for parser in self.parsers.values():
      parser.update([(now, frames)])
    self.out = self.state.update(self.parsers)
    self.state.out = self.out
    self.valid.append(all(p.can_valid for p in self.parsers.values()))
    self.tick += 1
    return self.state.dashboard_limit.observation

  def run(self, ticks, **kwargs):
    obs = None
    for _ in range(ticks):
      obs = self.step(**kwargs)
    return obs


class TestHyundaiCanSign(unittest.TestCase):
  def test_valid_range_units(self):
    for raw in (1, 25, 65, 120, 254):
      self.assertEqual(hyundai_can_sign(raw, False), (Status.VALID, raw * MPH))
      self.assertEqual(hyundai_can_sign(raw, True), (Status.VALID, raw * KPH))

  def test_none_values_are_unknown_not_absent(self):
    for raw in (0, 255):
      for metric in (False, True):
        self.assertEqual(hyundai_can_sign(raw, metric), (Status.UNKNOWN, 0.0))


class TestCanDashboardLimit(unittest.TestCase):
  def test_subscriptions_are_non_gating(self):
    _, state, parsers, _ = build()
    nav = parsers[Bus.pt].message_states[0x544]
    self.assertTrue(nav.ignore_alive)
    self.assertEqual(parsers[Bus.pt].dbc.addr_to_msg[0x544].name, NAV)
    self.assertTrue(state.can_limit_sources)

  def test_lkas12_not_subscribed_and_ignored(self):
    for car in (CAR.HYUNDAI_SONATA, CAR.KIA_STINGER_2022, CAR.KIA_RAY_EV):
      _, _, parsers, _ = build(car)
      self.assertNotIn(0x53E, parsers[Bus.cam].message_states)
    drive = Drive()
    obs = drive.run(400, tsr=40)  # 0x53E on bus 2 must not produce a limit
    self.assertEqual((obs.status, obs.observed_ns, obs.valid_until_ns), (Status.UNKNOWN, 0, 0))
    obs = drive.run(400, nav=25, tsr=40)
    self.assertEqual((obs.status, obs.speed_mps), (Status.VALID, 25 * MPH))
    self.assertTrue(all(drive.valid[50:]))

  def test_imperial_and_metric(self):
    for metric, factor in ((False, MPH), (True, KPH)):
      drive = Drive(metric=metric)
      obs = drive.run(400, nav=45)
      self.assertEqual(obs.status, Status.VALID)
      self.assertAlmostEqual(obs.speed_mps, 45 * factor, places=9)
      self.assertEqual(drive.state.is_metric, metric)

  def test_none_values_do_not_produce_a_limit(self):
    for raw in (0, 255):
      drive = Drive()
      obs = drive.run(400, nav=raw)
      self.assertEqual((obs.status, obs.speed_mps), (Status.UNKNOWN, 0.0))
      self.assertGreater(obs.observed_ns, 0)  # packets were seen; UNKNOWN is the decoded verdict, never ABSENT

  def test_expiry_follows_parser_timeout_and_tracker_only_advances_on_packets(self):
    drive = Drive()
    obs = drive.run(400, nav=45)
    ts, expiry = parser_expiry(drive.parsers[Bus.pt], NAV, NAV_SIG)
    self.assertEqual(obs.valid_until_ns, expiry)
    self.assertGreater(expiry, ts)
    self.assertEqual(obs.observed_ns, ts)
    held = drive.run(300, nav=45, send_nav=False)  # no new packet: nothing recorded, expiry unchanged
    self.assertEqual(held, obs)
    self.assertLess(held.valid_until_ns, 1_000_000_000 + drive.tick * TICK_NS)  # and it has lapsed by now

  def test_no_timestamp_before_a_packet_arrives(self):
    drive = Drive()
    obs = drive.run(400)
    self.assertEqual((obs.status, obs.observed_ns, obs.valid_until_ns), (Status.UNKNOWN, 0, 0))

  def test_can_valid_unaffected_when_sources_absent(self):
    drive = Drive()
    drive.run(400)
    self.assertTrue(all(drive.valid[50:]))
    ref = Drive()
    ref.run(400, nav=45)
    self.assertEqual(drive.valid, ref.valid)

  def test_can_valid_unaffected_when_sources_stop(self):
    drive = Drive()
    drive.run(400, nav=45)
    drive.run(2000, nav=45, send_nav=False)
    self.assertTrue(all(drive.valid[50:]))

  def test_fingerprint_without_navi_hu_yields_no_limit(self):
    fp = gen_empty_fingerprint()
    fp[0][0x391] = 8
    build(CAR.HYUNDAI_SONATA, fp)
    drive = Drive()
    obs = drive.run(400, nav=None)
    self.assertEqual(obs.status, Status.UNKNOWN)
    self.assertTrue(all(drive.valid[50:]))

  def test_stinger_navi_limit(self):
    drive = Drive(CAR.KIA_STINGER_2022)
    obs = drive.run(400, nav=65)
    self.assertEqual((obs.status, obs.speed_mps), (Status.VALID, 65 * MPH))
    self.assertTrue(all(drive.valid[50:]))

  def test_canfd_and_blended_cars_do_not_subscribe(self):
    for car in (CAR.KIA_EV6, CAR.HYUNDAI_PALISADE_2023):
      cp, state, parsers, _ = build(car)
      self.assertFalse(state.can_limit_sources)
      for parser in parsers.values():
        self.assertNotIn(0x544, parser.message_states)
        self.assertNotIn(0x53E, parser.message_states)


if __name__ == '__main__':
  unittest.main()
