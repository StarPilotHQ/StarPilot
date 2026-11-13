"""Dom's six-cluster 10 Hz radar receiver for the 2018–20 Camry."""
from opendbc.can import CANParser
from opendbc.car import Bus
from opendbc.car.interfaces import RadarInterfaceBase
from opendbc.car.structs import RadarData
from opendbc.car.toyota.values import DBC

CLUSTERS = tuple(range(0x680, 0x686))
EGO_SPEED_SCALE = 0.922


class ClusterRadarInterface(RadarInterfaceBase):
  def __init__(self, cp):
    super().__init__(cp)
    self.rcp = CANParser(DBC[cp.carFingerprint][Bus.radar], [(addr, 10) for addr in CLUSTERS], 1)
    self.pt_cp = CANParser(DBC[cp.carFingerprint][Bus.pt], [('WHEEL_SPEEDS', 80)], 0)
    self.updated_messages = set()
    self.track_id = 0

  def update(self, can_packets):
    self.updated_messages.update(self.rcp.update(can_packets))
    self.pt_cp.update(can_packets)
    if CLUSTERS[-1] not in self.updated_messages:
      return None
    ret = RadarData()
    if not self.pt_cp.can_valid:
      self.updated_messages.clear()
      ret.errors.canError = True
      return ret
    if not self.rcp.can_valid:
      ret.errors.canError = True
    wheel = self.pt_cp.vl['WHEEL_SPEEDS']
    ego = sum(wheel['WHEEL_SPEED_' + corner] for corner in ('FL', 'FR', 'RL', 'RR')) / 4. / 3.6 * self.CP.wheelSpeedFactor
    updated_ids = set()
    for address in sorted(self.updated_messages):
      if address not in CLUSTERS:
        continue
      cluster = self.rcp.vl[address]
      track = int(cluster['ID'])
      if track == 0x3F or cluster['LONG_DIST'] <= 0:
        continue
      updated_ids.add(track)
      if track not in self.pts:
        self.pts[track] = RadarData.RadarPoint()
        self.pts[track].trackId = self.track_id
        self.track_id += 1
      point = self.pts[track]
      point.dRel = float(cluster['LONG_DIST'])
      point.yRel = float(cluster['LAT_DIST'])
      point.vRel = float(cluster['SPEED']) - ego * EGO_SPEED_SCALE
      point.deprecated.aRel = float('nan')
      point.deprecated.yvRel = float(cluster['LAT_SPEED'])
      point.deprecated.measured = True
    for track in list(self.pts):
      if track not in updated_ids:
        del self.pts[track]
    self.updated_messages.clear()
    ret.points = list(self.pts.values())
    return ret
