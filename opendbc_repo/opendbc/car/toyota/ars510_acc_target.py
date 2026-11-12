"""Continental ARS510 ACC-target radar, one point per radar cycle.

LAT_DIST holds the low 12 bits of the radar's 13-bit lateral distance, so a target more than 20.95 m to the left (in turns
and at junctions) reads 40.96 m too far right; ACC_TARGET_2's check byte tells when, and the 40.96 m is added back.

Adapted from ds-sebastian/ars510-radar at 7ff9485a454130bc5765c9f715046d2694c655a6.
Copyright (c) 2026 ARS510 radar contributors. MIT license; see THIRD_PARTY_NOTICES.md.
"""
from math import nan

from opendbc.can import CANParser
from opendbc.car.structs import RadarData
from opendbc.car.interfaces import RadarInterfaceBase

RADAR_DBC = "toyota_tss2_ars510"
TARGET_ADDRS = {0x235, 0x237, 0x239}
# ACC_TARGET_2's CHECK_BYTE is a CRC-8 over the frame's signals, linear in the payload bits: one column per bit (byte 1
# bit 7 first) and a constant 0xFE. When LAT_DIST has wrapped, the remainder is the hidden 13th bit's column 0x14.
LAT_CHECK = bytes.fromhex("0f89ca6599c261be5fa1de6fb9d269ba5d0a058c46239fc1ee77b5d46a35944a259c4e279dc06030180c06038f272c2eefef9898771d3a74")
LAT_WRAP_M = 40.96


def lat_dist_wrapped(dat: bytes) -> bool:
  crc = 0xFE ^ dat[0]
  for i, column in enumerate(LAT_CHECK):
    crc ^= column if dat[1 + i // 8] >> (7 - i % 8) & 1 else 0
  return crc == 0x14


class RadarInterface(RadarInterfaceBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.rcp = CANParser(RADAR_DBC, [("ACC_TARGET_1", 50), ("ACC_TARGET_2", 50), ("ACC_TARGET_3", 50)], 1)
    self.trigger_msg = 0x237
    self.updated_messages = set()
    self.cycle = None
    self.last_record_t = None
    self.lat_wrapped = False
    self.fault = 0  # consecutive cycles with the radar's fault flag (0x196 byte 1 bit 0) set

  def update(self, can_strings):
    self.frame += 1
    packets = [(nanos, [(addr, dat, src) for addr, dat, src in frames if addr not in TARGET_ADDRS or len(dat) == 8])
               for nanos, frames in can_strings]
    self.updated_messages.update(self.rcp.update(packets))
    for _, frames in packets:  # CANParser keeps signals, not the payload the check byte covers
      for addr, dat, src in frames:
        if addr == self.trigger_msg and src == 1 and len(dat) == 8:
          self.lat_wrapped = lat_dist_wrapped(dat)
        elif addr == 0x196 and src == 1 and len(dat) >= 2:  # set every cycle while the radar declares itself blocked; once at power-up
          self.fault = self.fault + 1 if dat[1] & 1 else 0
    t = can_strings[-1][0] * 1e-9 if can_strings else None
    cycle = self.rcp.vl["ACC_TARGET_3"]["TIMESTAMP"]
    clock_seen = bool(self.rcp.ts_nanos["ACC_TARGET_3"]["TIMESTAMP"])
    if self.trigger_msg not in self.updated_messages or not clock_seen or cycle == self.cycle:
      # Keep card publishing through startup or a stopped cycle clock, without erasing healthy tracks between scans.
      if t is None or self.frame % 5 != 0 or (self.last_record_t is not None and t - self.last_record_t <= 0.5):
        return None
      ret = RadarData()
      ret.errors.radarUnavailableTemporary = self.last_record_t is not None
      return ret

    self.cycle = cycle
    self.updated_messages.clear()
    self.last_record_t = t
    ret = RadarData()
    if not self.rcp.can_valid:
      ret.errors.canError = True
      return ret
    if self.fault >= 2:
      ret.errors.radarUnavailableTemporary = True
      return ret

    target, position = self.rcp.vl["ACC_TARGET_1"], self.rcp.vl["ACC_TARGET_2"]
    if target["STATE"] == 5 and position["PRESENT"]:
      pt = RadarData.RadarPoint()
      pt.trackId = int(target["TARGET_ID"])
      pt.dRel = position["LONG_DIST"]
      pt.yRel = position["LAT_DIST"] + LAT_WRAP_M * self.lat_wrapped
      pt.vRel = target["REL_SPEED"]
      pt.aRel = pt.yvRel = nan
      pt.measured = True
      ret.points = [pt]
    return ret
