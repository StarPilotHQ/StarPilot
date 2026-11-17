from opendbc.can import CANParser
from opendbc.car import Bus, structs
from opendbc.car.interfaces import RadarInterfaceBase
from opendbc.car.tesla.preap.radar import PreAPRadarInterface
from opendbc.car.tesla.radar_hw1 import HW1RadarInterface
from opendbc.car.tesla.values import CAR, DBC

RADAR_START_ADDR = 0x410
RADAR_MSG_COUNT = 80  # 40 points * 2 messages each


def get_radar_can_parser(CP):
  if Bus.radar not in DBC[CP.carFingerprint]:
    return None

  messages = [('RadarStatus', 16)]
  for i in range(RADAR_MSG_COUNT // 2):
    messages.extend([
      (f'RadarPoint{i}_A', 16),
      (f'RadarPoint{i}_B', 16),
    ])

  return CANParser(DBC[CP.carFingerprint][Bus.radar], messages, 1)


class RadarInterface(RadarInterfaceBase):
  def __init__(self, CP):
    super().__init__(CP)
    if CP.carFingerprint == CAR.TESLA_MODEL_S_PREAP:
      self.preap = PreAPRadarInterface(CP)
      return
    if CP.carFingerprint == CAR.TESLA_MODEL_S_HW1:
      self.hw1 = HW1RadarInterface(CP)
      return
    self.updated_messages = set()
    self.trigger_msg = RADAR_START_ADDR + RADAR_MSG_COUNT - 1

    self.radar_off_can = CP.radarUnavailable
    self.rcp = get_radar_can_parser(CP)

  def configure_preap_radar(self, context, cp_bytes):
    if self.CP.carFingerprint != CAR.TESLA_MODEL_S_PREAP:
      raise ValueError("Radar context is only valid for Tesla PREAP")
    self.preap = PreAPRadarInterface(self.CP, context, cp_bytes)
    if self.preap.radar_off_can and not self.CP.radarUnavailable:
      raise ValueError("Invalid final Tesla radar startup context")

  def update(self, can_packets) -> structs.RadarDataT | None:
    if self.CP.carFingerprint == CAR.TESLA_MODEL_S_PREAP:
      return self.preap.update(can_packets)
    if self.CP.carFingerprint == CAR.TESLA_MODEL_S_HW1:
      return self.hw1.update(can_packets)
    if self.radar_off_can or self.rcp is None:
      return super().update([])

    vls = self.rcp.update(can_packets)
    self.updated_messages.update(vls)

    if self.trigger_msg not in self.updated_messages:
      return None

    rr = self._update(self.updated_messages)
    self.updated_messages.clear()

    return rr

  def _update(self, updated_messages):
    ret = structs.RadarData()
    if self.rcp is None:
      return ret

    if not self.rcp.can_valid:
      ret.errors.canError = True

    radar_status = self.rcp.vl['RadarStatus']
    if radar_status['shortTermUnavailable']:
      ret.errors.radarUnavailableTemporary = True
    if radar_status['sensorBlocked'] or radar_status['vehDynamicsError']:
      ret.errors.radarFault = True

    for i in range(RADAR_MSG_COUNT // 2):
      msg_a = self.rcp.vl[f'RadarPoint{i}_A']
      msg_b = self.rcp.vl[f'RadarPoint{i}_B']

      # Make sure msg A and B are together
      if msg_a['Index'] != msg_b['Index2']:
        self.pts.pop(i, None)
        continue

      if not msg_a['Tracked']:
        if i in self.pts:
          del self.pts[i]
        continue

      if i not in self.pts:
        self.pts[i] = structs.RadarData.RadarPoint()
        self.pts[i].trackId = self.track_id
        self.track_id += 1

      self.pts[i].dRel = msg_a['LongDist']
      self.pts[i].yRel = msg_a['LatDist']
      self.pts[i].vRel = msg_a['LongSpeed']

    ret.points = list(self.pts.values())
    return ret
