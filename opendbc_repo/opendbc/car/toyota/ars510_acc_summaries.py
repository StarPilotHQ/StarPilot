"""Continental ARS510 ACC target plus the radar's target summaries: up to three points per radar cycle.

The radar's ACC target (0x235 / 0x237) is published as in `acc`. The target summaries add up to two more moving cars from
the same tracker: range and lateral in 0x192 / 0x194, a pair code in 0x191 / 0x193 (trackId 64 + code). A summary within
1 m / 0.5 m of the ACC point is that car and is skipped. vRel is the least-squares slope of the code's last 8 ranges
over CAN time (the 8 must span less than 0.6 s). Summaries beyond 40 m are left out: radard cannot tell a neighbouring
lane from the camera's farther lead there (at 47-58 m it took such a car as the lead before the radar's ACC picked
the real one). Evidence and replay numbers: docs/11 ("acc_summaries").
Forks whose RadarPoint still has aRel / yvRel / measured get them filled: the ACC point's aRel is the target's own relative
acceleration (0x235 REL_ACCEL) and its yvRel its lateral speed (0x235 LAT_SPEED, d(LAT_DIST)/dt in the car's frame, left
positive, unwrapped by its check byte); a summary car's yvRel is the slope of its last 8 laterals and its aRel NaN (not sent
by the radar); measured is True (docs/05, docs/11).
While the radar's fault flag (0x196 byte 1 bit 0) is set on 2+ consecutive cycles, as it is while the radar declares itself
blocked, the report is radarUnavailableTemporary with no points (docs/05, docs/08).

Adapted from ds-sebastian/ars510-radar at 7ff9485a454130bc5765c9f715046d2694c655a6 (openpilot/standalone/ars510_acc_summaries.py;
evidence and replay numbers in its docs/05, docs/08 and docs/11).
Copyright (c) 2026 ARS510 radar contributors. MIT license; see THIRD_PARTY_NOTICES.md.
"""
from collections import deque
from functools import reduce

from opendbc.car.structs import RadarData
from opendbc.car.interfaces import RadarInterfaceBase

# JUMP_M: a range step this large between cycles is a glitch, a new car, or two cars sharing a pair code (codes are per slot)
STALE_S, FAR_M, SLOPE_N, SLOPE_SPAN_S, JUMP_M, SUMMARY_ID = 0.5, 40.0, 8, 0.6, 5.0, 64  # summary trackIds: 64 + pair code
# 0x235 LAT_SPEED and 0x237 LAT_DIST are the low 11 / 12 bits of one-bit-wider values: beyond +12.8 m/s / +20.95 m they wrap
# by 25.6 m/s / 40.96 m. The check byte (CRC-8 over the signals, linear: constant ^ one column per payload bit from byte 1
# bit 7; docs/05) then leaves the remainder 0x9C / 0x14 instead of 0. Per address: (constant, wrap remainder, columns).
COLS_235 = "68341a0d18dcafd96ce43c1e0f89ca6561be5fa1de6fb9d269ba5d8c46239fc1ee77b5d46a4e279dc06030180c06038fcfe975b45a2d984c"
COLS_237 = "0f89ca6599c261be5fa1de6fb9d269ba5d0a058c46239fc1ee77b5d46a35944a259c4e279dc06030180c06038f272c2eefef9898771d3a74"
CHECK = {0x235: (0xDF, 0x9C, bytes.fromhex(COLS_235)), 0x237: (0xFE, 0x14, bytes.fromhex(COLS_237))}
# optional point fields, when this tree's RadarPoint carries them
LEGACY = tuple(f for f in ("aRel", "yvRel", "measured") if hasattr(RadarData.RadarPoint(), f))


def bits(b, start, length):
  return (int.from_bytes(b, "big") >> start) & ((1 << length) - 1)


def wrapped(address, dat):
  return reduce(lambda crc, i: crc ^ CHECK[address][2][i] * bits(dat, 55 - i, 1), range(56), CHECK[address][0] ^ dat[0]) == CHECK[address][1]


def slope(hist, k):  # least-squares slope of column k (1 range, 2 lateral) over time
  tm, xm = sum(h[0] for h in hist) / len(hist), sum(h[k] for h in hist) / len(hist)
  return sum((h[0] - tm) * (h[k] - xm) for h in hist) / sum((h[0] - tm) ** 2 for h in hist)


def point(track_id, d, y, v, a=float("nan"), yv=float("nan")):
  return RadarData.RadarPoint(trackId=track_id, dRel=d, yRel=y, vRel=v, **{k: x for k, x in {"aRel": a, "yvRel": yv, "measured": True}.items() if k in LEGACY})


class RadarInterface(RadarInterfaceBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.acc = self.pos = self.last_t = None
    self.fault, self.ident, self.summaries, self.hist = 0, {}, {}, {}

  def update(self, can_packets):
    self.frame += 1
    t = None
    for nanos, frames in can_packets:
      t = nanos * 1e-9
      for address, dat, src in frames:
        if src != 1:
          continue
        if address == 0x235 and len(dat) == 8:  # id, closing speed, rel. accel, lateral speed; status nibble 5 = target present
          self.last_t, self.acc = t, (bits(dat, 0, 6), (bits(dat, 29, 11) - 1024) * 0.125, (dat[2] - 100) * 0.125,
                                      (bits(dat, 8, 11) - 1024) * 0.0125 + 25.6 * wrapped(0x235, dat)) if dat[1] & 0x0F == 5 else None
        elif address == 0x237 and len(dat) == 8:  # distance, lateral (left positive); bit 10 = target present
          self.last_t, self.pos = t, ((bits(dat, 39, 13) * 0.025, (bits(dat, 27, 12) - 2000) * 0.01 + 40.96 * wrapped(0x237, dat))
                                      if bits(dat, 10, 1) else None)
        elif address == 0x196 and len(dat) >= 2:  # fault flag: every cycle while blocked; one cycle at power-up
          self.fault = self.fault + 1 if dat[1] & 1 else 0
        elif address in (0x191, 0x193) and len(dat) == 8:  # byte 0 >> 1: score (127 = no car), byte 3 >> 2: pair code
          self.ident[address] = dat
        elif address in (0x192, 0x194) and len(dat) >= 4 and address - 1 in self.ident:  # range, lateral (left positive)
          ident, self.summaries[address] = self.ident[address - 1], None
          # sentinel, corrupt word (top 3 bits of each word are 0 unless corrupt, e.g. FF FF FF FF), or no car (score 127)
          if dat[0:4] != b"\x00\xff\x00\xff" and not (dat[0] | dat[2]) & 0xE0 and ident[0] >> 1 != 127:
            code, d, y = ident[3] >> 2, (bits(dat[0:2], 0, 13) - 160) / 16, (bits(dat[2:4], 0, 13) - 2048) * 0.015
            hist = self.hist.setdefault(code, deque(maxlen=SLOPE_N))
            if hist and abs(d - hist[-1][1]) > JUMP_M:  # no car moves 5 m in one 60 ms cycle: restart rather than slope through it
              hist.clear()
            hist.append((t, d, y))
            self.summaries[address] = (code, d, y, t)
    if t is None or self.frame % 5 != 0:  # card calls update at 100 Hz: report at 20 Hz
      return None
    ret = RadarData()
    if self.last_t is None or t - self.last_t > STALE_S or self.fault >= 2:
      ret.errors.radarUnavailableTemporary = self.last_t is not None or self.fault >= 2
      return ret
    points = [point(self.acc[0], *self.pos, *self.acc[1:])] if self.acc is not None and self.pos is not None else []
    for code, d, y, ts in filter(None, self.summaries.values()):
      hist, is_acc = self.hist[code], self.pos is not None and abs(d - self.pos[0]) < 1.0 and abs(y - self.pos[1]) < 0.5
      if t - ts < STALE_S and d <= FAR_M and not is_acc and len(hist) == SLOPE_N and hist[-1][0] - hist[0][0] < SLOPE_SPAN_S:
        points.append(point(SUMMARY_ID + code, d, y, slope(hist, 1), yv=slope(hist, 2)))
    ret.points = points  # assigned as a list: forks with capnp-backed structs (sunnypilot) take list assignment only
    return ret
