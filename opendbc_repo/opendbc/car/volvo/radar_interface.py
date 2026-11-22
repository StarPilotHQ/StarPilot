from math import cos, sin
from opendbc.can.parser import CANParser
from opendbc.car import Bus, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import RadarInterfaceBase
from opendbc.car.volvo.eucd_params import CANBUS
from opendbc.car.volvo.values import DBC

# Delphi ESR 2.5: 64 tracks at 0x500..0x53F, 20 Hz, bus 1
DELPHI_ESR_TRACK_ADDRS = list(range(0x500, 0x540))  # Target1..Target64
DELPHI_ESR_TRACK_NAMES = [f"Target{i}" for i in range(1, 65)]
# (addr, name) pairs
TRACK_ADDR_NAMES = list(zip(DELPHI_ESR_TRACK_ADDRS, DELPHI_ESR_TRACK_NAMES, strict=True))
TRIGGER_MSG_ADDR = DELPHI_ESR_TRACK_ADDRS[-1]

# Track status: 0 no target, 6 invalid coasted
INVALID_STATUSES = {0, 6}

# Consecutive valid frames before a track is published
MIN_VALID_CNT = 3


def _create_radar_can_parser(CP) -> CANParser:
  messages = [(name, 20) for name in DELPHI_ESR_TRACK_NAMES]
  return CANParser(DBC[CP.carFingerprint][Bus.radar], messages, CANBUS.body)


class RadarInterface(RadarInterfaceBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.updated_messages: set[int] = set()
    self.valid_cnt: dict[int, int] = {addr: 0 for addr in DELPHI_ESR_TRACK_ADDRS}
    self.track_id = 0
    self.rcp = None if CP.radarUnavailable else _create_radar_can_parser(CP)

  def update(self, can_strings):
    if self.rcp is None:
      return super().update(can_strings)

    self.updated_messages.update(self.rcp.update(can_strings))

    # Publish after the last slot of a sweep
    if TRIGGER_MSG_ADDR not in self.updated_messages:
      return None
    self.updated_messages.clear()

    ret = structs.RadarData()
    if not self.rcp.can_valid:
      ret.errors.canError = True

    for addr, name in TRACK_ADDR_NAMES:
      cpt = self.rcp.vl[name]
      rng = cpt["CAN_TX_TRACK_RANGE"]
      status = int(cpt["CAN_TX_TRACK_STATUS"])
      valid = status not in INVALID_STATUSES and rng > 0.5

      if valid:
        self.valid_cnt[addr] = min(self.valid_cnt[addr] + 1, MIN_VALID_CNT + 2)
      else:
        self.valid_cnt[addr] = max(self.valid_cnt[addr] - 1, 0)

      # Keep the last good reading on flicker
      if valid and self.valid_cnt[addr] >= MIN_VALID_CNT:
        if addr not in self.pts:
          self.pts[addr] = structs.RadarData.RadarPoint()
          self.pts[addr].trackId = self.track_id
          self.track_id += 1
        angle_rad = cpt["CAN_TX_TRACK_ANGLE"] * CV.DEG_TO_RAD
        # Flip the ESR angle for left-positive yRel
        self.pts[addr].dRel = rng * cos(angle_rad)
        self.pts[addr].yRel = -rng * sin(angle_rad)
        self.pts[addr].vRel = cpt["CAN_TX_TRACK_RANGE_RATE"]
      elif self.valid_cnt[addr] < MIN_VALID_CNT and addr in self.pts:
        del self.pts[addr]

    ret.points = list(self.pts.values())
    return ret
