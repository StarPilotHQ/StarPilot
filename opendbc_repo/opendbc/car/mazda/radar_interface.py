#!/usr/bin/env python3
import math

from opendbc.can.parser import CANParser
from opendbc.car import Bus, structs
from opendbc.car.interfaces import RadarInterfaceBase
from opendbc.car.mazda.values import DBC, MazdaSafetyFlags

def get_radar_can_parser(CP):
  if Bus.radar not in DBC[CP.carFingerprint]:
    return None
  if not CP.flags & MazdaSafetyFlags.RADAR_INTERCEPTOR:
    return None

  messages = [(f"RADAR_TRACK_{addr}", 10) for addr in range(361,367)]
  return CANParser(DBC[CP.carFingerprint][Bus.radar], messages, 2)

class RadarInterface(RadarInterfaceBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.updated_messages = set()
    self.track_id = 0

    self.radar_off_can = CP.radarUnavailable
    self.rcp = get_radar_can_parser(CP)

  def update(self, can_strings):
    if self.radar_off_can or (self.rcp is None):
      return super().update(None)
    vls = self.rcp.update_strings(can_strings)
    self.updated_messages.update(vls)
    rr = self._update(self.updated_messages)
    self.updated_messages.clear()

    return rr

  def _update(self, updated_messages):
    ret = structs.RadarData()
    if self.rcp is None:
      return ret
    errors = []
    if not self.rcp.can_valid:
      errors.append("canError")
    ret.errors = errors
    for addr in range(361,367):
      msg = self.rcp.vl[f"RADAR_TRACK_{addr}"]
      if addr not in self.pts:
        self.pts[addr] = structs.RadarData.RadarPoint()
        self.pts[addr].trackId = self.track_id
        self.track_id += 1
      valid = (msg['DIST_OBJ'] != 4095) and (msg['ANG_OBJ'] != 2046) and (msg['RELV_OBJ'] != -16)
      if valid:
        azimuth = math.radians(msg['ANG_OBJ']/64)
        self.pts[addr].measured = True
        self.pts[addr].dRel = msg['DIST_OBJ']/16
        self.pts[addr].yRel = -math.sin(azimuth) * msg['DIST_OBJ']/16
        self.pts[addr].vRel = msg['RELV_OBJ']/16
        self.pts[addr].aRel = float('nan')
        self.pts[addr].yvRel = float('nan')
      else:
        del self.pts[addr]
    ret.points = list(self.pts.values())
    return ret