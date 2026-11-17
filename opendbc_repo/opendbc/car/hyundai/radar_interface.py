import math
from dataclasses import dataclass

from opendbc.can import CANParser
from opendbc.car import Bus, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import RadarInterfaceBase
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.values import (CAR, DBC, HyundaiFlags, HYUNDAI_MRR35_RADAR_DBC, HYUNDAI_MRR30_RADAR_DBC,
                                        HYUNDAI_GV70_RADAR_DBC, HYUNDAI_G90_RADAR_DBC, is_blended)

RADAR_START_ADDR = 0x500
RADAR_MSG_COUNT = 32
G90_RADAR_MSG_COUNT = 64
G90_RADAR_CYCLE_NS = 20_000_000
MRREVO14F_RADAR_START_ADDR = 0x602
MRREVO14F_RADAR_MSG_COUNT = 16
MRREVO14F_RADAR_CYCLE_NS = 20_000_000  # track messages are published at 50 Hz
MRR30_RADAR_START_ADDR = 0x210
MRR30_RADAR_MSG_COUNT = 16
MRR30_RADAR_CYCLE_NS = 20_000_000
GV70_RADAR_CYCLE_NS = 50_000_000
MRR35_RADAR_START_ADDR = 0x3A5
MRR35_RADAR_MSG_COUNT = 32
MRR35_RADAR_CYCLE_NS = 50_000_000  # 20 Hz

# 2022+ Kia Stinger: 10 object slots x 3 frames (0x238..0x255, 8 bytes, 50 Hz) on bus 1. No radar DBC; decoded by hand.
STINGER_2022_RADAR_BUS = 1
STINGER_2022_RADAR_START_ADDR = 0x238
STINGER_2022_RADAR_SLOT_COUNT = 10
STINGER_2022_RADAR_MSG_COUNT = 30
STINGER_2022_MAX_TRIPLET_SPAN_NS = 20_000_000

# Suppress measured near-centre stopped-lead speed undershoot without changing oncoming tracks.
STINGER_2022_STOP_UNDERSHOOT_SPEED = -0.7  # m/s
STINGER_2022_STOP_UNDERSHOOT_MAX_LATERAL = 2.0  # m

# Measured radar/wheel-speed bias; taper to zero at standstill.
STINGER_2022_EGO_SPEED_OFFSET = 0.14  # m/s
STINGER_2022_EGO_SPEED_OFFSET_RAMP = 1.0  # m/s


def stinger_2022_radar(CP) -> bool:
  return CP.carFingerprint == CAR.KIA_STINGER_2022


def stinger_2022_radar_available(fingerprint) -> bool:
  return all(fingerprint[STINGER_2022_RADAR_BUS].get(addr) == 8
             for addr in range(STINGER_2022_RADAR_START_ADDR, STINGER_2022_RADAR_START_ADDR + STINGER_2022_RADAR_MSG_COUNT))


def _field(dat: bytes, start: int, size: int) -> int:
  return (int.from_bytes(dat, "big") >> (64 - start - size)) & ((1 << size) - 1)


@dataclass(frozen=True)
class Stinger2022CandidateObject:
  slot: int
  object_id: int
  cycle: int
  type_raw: int
  distance: float
  lateral_raw: float
  absolute_speed: float

  def relative_speed(self, ego_speed: float) -> float:
    if not math.isfinite(ego_speed):
      raise ValueError("ego speed must be finite")
    radar_ego_speed = ego_speed - STINGER_2022_EGO_SPEED_OFFSET * min(max(ego_speed, 0.0) / STINGER_2022_EGO_SPEED_OFFSET_RAMP, 1.0)
    return self.settled_absolute_speed() - radar_ego_speed

  def settled_absolute_speed(self) -> float:
    if STINGER_2022_STOP_UNDERSHOOT_SPEED < self.absolute_speed < 0.0 and \
       abs(self.lateral_raw) < STINGER_2022_STOP_UNDERSHOOT_MAX_LATERAL:
      return 0.0
    return self.absolute_speed


def decode_stinger_2022_triplet(slot: int, payloads: tuple[bytes, ...]) -> Stinger2022CandidateObject | None:
  if not 0 <= slot < STINGER_2022_RADAR_SLOT_COUNT or len(payloads) != 3 or any(len(dat) != 8 for dat in payloads):
    return None
  a, b, c = payloads
  cycle = _field(a, 44, 2)
  object_id = _field(a, 56, 7)
  if cycle != _field(b, 0, 2) or cycle != _field(c, 0, 2):
    return None
  if object_id != _field(b, 57, 7) or object_id != _field(c, 43, 7):
    return None
  distance_raw = _field(a, 0, 13)
  if object_id == 0 or distance_raw == 0:
    return None

  return Stinger2022CandidateObject(
    slot=slot, object_id=object_id, cycle=cycle, type_raw=_field(a, 13, 3),
    distance=distance_raw * 0.05, lateral_raw=_field(a, 16, 12) * 0.05 - 102.4,
    absolute_speed=_field(a, 32, 12) * 0.05 - 100.0,
  )


class Stinger2022ObjectDecoder:
  def __init__(self):
    self.frames: dict[tuple[int, int], tuple[int, bytes]] = {}

  def update(self, timestamp_ns: int, address: int, dat: bytes, bus: int) -> Stinger2022CandidateObject | None:
    end_addr = STINGER_2022_RADAR_START_ADDR + STINGER_2022_RADAR_MSG_COUNT
    if bus != STINGER_2022_RADAR_BUS or not STINGER_2022_RADAR_START_ADDR <= address < end_addr or len(dat) != 8:
      return None
    slot, part = divmod(address - STINGER_2022_RADAR_START_ADDR, 3)
    self.frames[slot, part] = (timestamp_ns, dat)
    if part != 2 or any((slot, i) not in self.frames for i in range(3)):
      return None
    triple = [self.frames[slot, i] for i in range(3)]
    times = [t for t, _ in triple]

    # Consume the complete cycle so repeated final frames cannot reuse old parts.
    for i in range(3):
      del self.frames[slot, i]

    if max(times) != timestamp_ns or max(times) - min(times) > STINGER_2022_MAX_TRIPLET_SPAN_NS:
      return None

    return decode_stinger_2022_triplet(slot, tuple(dat for _, dat in triple))


def blended_track_radar(CP) -> bool:
  # Exact mixed classic PT / HDA-II steering identity, independently observed
  # by the existing factory. Never relabel another classic or CANFD car.
  return bool(CP.brand == 'hyundai' and is_blended(CP) and CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG)


def radar_dbc(CP):
  return HYUNDAI_GV70_RADAR_DBC if blended_track_radar(CP) else DBC[CP.carFingerprint].get(Bus.radar)


def radar_bus(CP) -> int:
  offset = CanBus(CP).offset if CP.flags & HyundaiFlags.CANFD else 0
  # Frozen Ioniq 6 camera-SCC wiring exposes the MRR35 object bank on bus 1;
  # its LKA/HDA-II wiring and the other MRR35 cars expose it on bus 0.
  if CP.carFingerprint == CAR.HYUNDAI_IONIQ_6 and CP.flags & HyundaiFlags.CANFD_CAMERA_SCC:
    return offset + 1
  return offset

# POC for parsing corner radars: https://github.com/commaai/openpilot/pull/24221/


def get_radar_can_parser(CP):
  if stinger_2022_radar(CP):
    # Only the ego speed comes from a DBC; the object frames are decoded by hand (Stinger2022ObjectDecoder).
    return CANParser(DBC[CP.carFingerprint][Bus.pt], [("WHL_SPD11", 50)], 0)
  if radar_dbc(CP) is None:
    return None

  start, count = radar_message_range(CP)
  mrr35 = radar_dbc(CP) == HYUNDAI_MRR35_RADAR_DBC
  g90 = radar_dbc(CP) == HYUNDAI_G90_RADAR_DBC
  gv70 = radar_dbc(CP) == HYUNDAI_GV70_RADAR_DBC
  messages = [(f"RADAR_TRACK_{addr:x}",
               20 if mrr35 and addr == start + count - 1 else
               float("nan") if mrr35 or g90 and addr >= start + RADAR_MSG_COUNT else 20 if gv70 else 50) for addr in range(start, start + count)]
  mrr30 = radar_dbc(CP) == HYUNDAI_MRR30_RADAR_DBC
  # MRR35 publishes many unused/unknown fields in each object packet. Decode
  # the four consumed track fields, retaining parser integrity fields automatically.
  selected = ({name: ('STATE', 'LONG_DIST', 'LAT_DIST', 'REL_SPEED') for name, _ in messages} if mrr35 else None)
  return CANParser(radar_dbc(CP), messages, radar_bus(CP) if mrr35 or mrr30 or gv70 else 1, signals=selected)


def radar_message_range(CP) -> tuple[int, int]:
  if stinger_2022_radar(CP):
    return STINGER_2022_RADAR_START_ADDR, STINGER_2022_RADAR_MSG_COUNT
  if radar_dbc(CP) == HYUNDAI_G90_RADAR_DBC:
    return RADAR_START_ADDR, G90_RADAR_MSG_COUNT
  if radar_dbc(CP) in (HYUNDAI_MRR30_RADAR_DBC, HYUNDAI_GV70_RADAR_DBC):
    return MRR30_RADAR_START_ADDR, MRR30_RADAR_MSG_COUNT
  if radar_dbc(CP) == HYUNDAI_MRR35_RADAR_DBC:
    return MRR35_RADAR_START_ADDR, MRR35_RADAR_MSG_COUNT
  if radar_dbc(CP) == "hyundai_mrrevo14f_radar_generated":
    return MRREVO14F_RADAR_START_ADDR, MRREVO14F_RADAR_MSG_COUNT
  return RADAR_START_ADDR, RADAR_MSG_COUNT


class RadarInterface(RadarInterfaceBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.updated_messages = set()
    self.start_addr, self.msg_count = radar_message_range(CP)
    self.g90 = radar_dbc(CP) == HYUNDAI_G90_RADAR_DBC
    self.mrrevo14f = self.start_addr == MRREVO14F_RADAR_START_ADDR
    self.mrr35 = self.start_addr == MRR35_RADAR_START_ADDR
    self.gv70 = radar_dbc(CP) == HYUNDAI_GV70_RADAR_DBC
    self.mrr30 = self.start_addr == MRR30_RADAR_START_ADDR and not self.gv70
    self.stinger_2022 = stinger_2022_radar(CP)
    self.last_mrr35_trigger_ns = 0
    if self.stinger_2022:
      self.stinger_2022_decoder = Stinger2022ObjectDecoder()
      self.stinger_2022_objects: dict[int, Stinger2022CandidateObject] = {}
      self.v_ego = 0.0
    self.trigger_msg = self.start_addr + (RADAR_MSG_COUNT if self.g90 else self.msg_count) - 1

    self.radar_off_can = CP.radarUnavailable
    self.rcp = get_radar_can_parser(CP)

  def update(self, can_strings):
    if self.radar_off_can or (self.rcp is None):
      return super().update(None)

    if (self.g90 or self.mrr30 or self.gv70 or self.mrr35 and self.CP.carFingerprint == CAR.HYUNDAI_IONIQ_6) and can_strings:
      expected_length = 8 if self.g90 else 32 if self.mrr30 or self.gv70 else 24
      entries = [can_strings] if not isinstance(can_strings[0], list | tuple) else can_strings
      can_strings = [(timestamp, [(addr, data, bus) for addr, data, bus in frames
                                  if not (bus == self.rcp.bus and self.start_addr <= addr < self.start_addr + self.msg_count and len(data) != expected_length)])
                     for timestamp, frames in entries]

    vls = self.rcp.update(can_strings)
    self.updated_messages.update(vls)
    if self.stinger_2022:
      self._update_stinger_2022_tracks(can_strings)

    if self.trigger_msg not in self.updated_messages:
      return None

    if self.mrr35:
      trigger_ns = self.rcp.ts_nanos[self.trigger_msg]["LONG_DIST"]
      if trigger_ns <= self.last_mrr35_trigger_ns:
        self.updated_messages.clear()
        return None
      self.last_mrr35_trigger_ns = trigger_ns

    rr = self._update_stinger_2022() if self.stinger_2022 else self._update(self.updated_messages)
    self.updated_messages.clear()

    return rr

  def _update_stinger_2022_tracks(self, can_strings):
    if "WHL_SPD11" in self.rcp.vl and self.rcp.ts_nanos["WHL_SPD11"]["WHL_SPD_FL"] > 0:
      whl = self.rcp.vl["WHL_SPD11"]
      wheel_speed = 0.25 * (whl["WHL_SPD_FL"] + whl["WHL_SPD_FR"] + whl["WHL_SPD_RL"] + whl["WHL_SPD_RR"])
      self.v_ego = wheel_speed * CV.KPH_TO_MS * self.CP.wheelSpeedFactor

    end_addr = self.start_addr + self.msg_count
    for timestamp_ns, frames in can_strings:
      for address, dat, src in frames:
        if src != STINGER_2022_RADAR_BUS or not (self.start_addr <= address < end_addr) or len(dat) != 8:
          continue

        self.updated_messages.add(address)
        obj = self.stinger_2022_decoder.update(timestamp_ns, address, bytes(dat), src)
        if obj is not None:
          self.stinger_2022_objects[obj.object_id] = obj

  def _update_stinger_2022(self):
    ret = structs.RadarData()
    if not self.rcp.can_valid or self.rcp.ts_nanos["WHL_SPD11"]["WHL_SPD_FL"] == 0:
      # objects decoded in an error cycle must not be published in the next one
      self.stinger_2022_objects.clear()
      ret.errors.canError = True
      return ret

    current_ids = set()
    for object_id, obj in self.stinger_2022_objects.items():
      current_ids.add(object_id)
      pt = self.pts.get(object_id)
      if pt is None:
        pt = structs.RadarData.RadarPoint()
        pt.trackId = self.track_id
        self.track_id += 1
        self.pts[object_id] = pt

      pt.dRel = obj.distance
      pt.yRel = -obj.lateral_raw  # in car frame's y axis, left is positive
      pt.vRel = obj.relative_speed(self.v_ego)

    # tracks not seen this cycle are dropped
    for oid in list(self.pts.keys()):
      if oid not in current_ids:
        del self.pts[oid]

    self.stinger_2022_objects.clear()

    ret.points = list(self.pts.values())
    return ret

  def _update(self, updated_messages):
    ret = structs.RadarData()
    if self.rcp is None:
      return ret

    if not self.rcp.can_valid:
      ret.errors.canError = True

    for addr in range(self.start_addr, self.start_addr + self.msg_count):
      msg = self.rcp.vl[f"RADAR_TRACK_{addr:x}"]

      if self.mrr30 or self.gv70:
        trigger_time = self.rcp.ts_nanos[self.trigger_msg]["1_LONG_DIST"]
        source_time = self.rcp.ts_nanos[addr]["1_LONG_DIST"]
        cycle_ns = GV70_RADAR_CYCLE_NS if self.gv70 else MRR30_RADAR_CYCLE_NS
        fresh = addr in updated_messages and 0 <= trigger_time - source_time <= cycle_ns
        for index in (1, 2):
          track_key = addr * 2 + index - 1
          if fresh and msg[f"{index}_STATE"] in (3, 4):
            point = self.pts.get(track_key)
            if point is None:
              point = structs.RadarData.RadarPoint()
              point.trackId = self.track_id
              self.track_id += 1
              self.pts[track_key] = point
            point.dRel = msg[f"{index}_LONG_DIST"]
            point.yRel = msg[f"{index}_LAT_DIST"]
            point.vRel = msg[f"{index}_REL_SPEED"]
          elif track_key in self.pts:
            del self.pts[track_key]
        continue

      if self.mrr35:
        trigger_ns = self.last_mrr35_trigger_ns
        source_ns = self.rcp.ts_nanos[addr]["LONG_DIST"]
        fresh = addr in updated_messages and 0 <= trigger_ns - source_ns <= MRR35_RADAR_CYCLE_NS
        if fresh and msg["STATE"] in (3, 4):
          point = self.pts.get(addr)
          if point is None:
            point = structs.RadarData.RadarPoint()
            point.trackId = self.track_id
            self.track_id += 1
            self.pts[addr] = point
          point.dRel = msg["LONG_DIST"]
          point.yRel = msg["LAT_DIST"]
          point.vRel = msg["REL_SPEED"]
        elif addr in self.pts:
          del self.pts[addr]
        continue

      if self.mrrevo14f:
        trigger_time = self.rcp.ts_nanos[self.trigger_msg]["1_DISTANCE"]
        source_time = self.rcp.ts_nanos[addr]["1_DISTANCE"]
        fresh = addr in updated_messages and 0 <= trigger_time - source_time <= MRREVO14F_RADAR_CYCLE_NS
        for index in (1, 2):
          track_key = addr * 2 + index - 1
          valid = fresh and msg[f"{index}_DISTANCE"] != 255.75
          if valid:
            point = self.pts.get(track_key)
            if point is None:
              point = structs.RadarData.RadarPoint()
              point.trackId = self.track_id
              self.track_id += 1
              self.pts[track_key] = point
            point.dRel = msg[f"{index}_DISTANCE"]
            point.yRel = msg[f"{index}_LATERAL"]
            point.vRel = msg[f"{index}_SPEED"]
          elif track_key in self.pts:
            del self.pts[track_key]
        continue

      if self.g90 and addr >= self.start_addr + RADAR_MSG_COUNT:
        trigger_time = self.rcp.ts_nanos[self.trigger_msg]["LONG_DIST"]
        source_time = self.rcp.ts_nanos[addr]["LONG_DIST"]
        if addr not in updated_messages or not 0 <= trigger_time - source_time <= G90_RADAR_CYCLE_NS:
          self.pts.pop(addr, None)
          continue

      if addr not in self.pts:
        self.pts[addr] = structs.RadarData.RadarPoint()
        self.pts[addr].trackId = self.track_id
        self.track_id += 1

      valid = msg['STATE'] in (3, 4)
      if valid:
        azimuth = math.radians(msg['AZIMUTH'])
        self.pts[addr].dRel = math.cos(azimuth) * msg['LONG_DIST']
        self.pts[addr].yRel = 0.5 * -math.sin(azimuth) * msg['LONG_DIST']
        self.pts[addr].vRel = msg['REL_SPEED']

      else:
        del self.pts[addr]

    ret.points = list(self.pts.values())
    return ret
