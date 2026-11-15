import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from opendbc.can import CANParser
from opendbc.car import Bus, structs
from opendbc.car.honda.hondacan import CanBus
from opendbc.car.honda.values import CAR, DBC, HondaFlags
from opendbc.car.interfaces import RadarInterfaceBase

BOSCH_A_DBC_NAME = "honda_bosch_a_radar"
BOSCH_A_NUM_SLOTS = 16


def _bosch_a_main_base(slot: int) -> int:
  return 640 + 4 * slot if slot < 4 else 720 + 4 * (slot - 4)


def _bosch_a_aux_id(slot: int) -> int:
  return 712 + slot if slot < 8 else 656 + (slot - 8)


BOSCH_A_MAIN_IDS = [[_bosch_a_main_base(s) + i for i in range(4)] for s in range(BOSCH_A_NUM_SLOTS)]
BOSCH_A_AUX_IDS = [_bosch_a_aux_id(s) for s in range(BOSCH_A_NUM_SLOTS)]
BOSCH_A_ALL_IDS = [addr for ids in BOSCH_A_MAIN_IDS for addr in ids] + BOSCH_A_AUX_IDS
BOSCH_A_TRIGGER_MSG = BOSCH_A_MAIN_IDS[BOSCH_A_NUM_SLOTS - 1][3]
BOSCH_A_SWEEP_END_MSG = BOSCH_A_AUX_IDS[BOSCH_A_NUM_SLOTS - 1]
BOSCH_A_FREQ_HZ = 15
BOSCH_A_RANGE_SCALE_M = 0.05712
BOSCH_A_RANGE_OFFSET_M = -3.0
BOSCH_A_AZIMUTH_SCALE_RAD = 1.0 / 2048.0
BOSCH_A_AZIMUTH_CENTER = 1024
BOSCH_A_STATUS_INVALID = 15
BOSCH_A_RANGE_RAW_INVALID = 4095
BOSCH_A_ANGLE_RAW_INVALID = 2047
BOSCH_A_LIFE_INVALID = 4095
BOSCH_A_TRACK_ID_MIN = 1
BOSCH_A_TRACK_ID_MAX = 63
BOSCH_A_RANGE_RATIO_INVALID = 1023
BOSCH_A_LOGICAL_00CA_INVALID = BOSCH_A_RANGE_RATIO_INVALID
BOSCH_A_RANGE_RATIO_SCALE = 0.001
BOSCH_A_RANGE_RATIO_OFFSET = 0.5
BOSCH_A_DIRECT_VREL_INVALID = 2046
BOSCH_A_DIRECT_VREL_MIN_RAW = 0
BOSCH_A_DIRECT_VREL_MAX_RAW = 1728
BOSCH_A_DIRECT_VREL_CENTER_RAW = 864
BOSCH_A_DIRECT_VREL_SCALE_MPS = 1.0 / 64.0
BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW = 511
BOSCH_A_RANGE_SIGMA_DEGRADED_RAW = 4
BOSCH_A_RANGE_INNOVATION_MAX_M = 2.0
BOSCH_A_RANGE_INNOVATION_HARD_MAX_M = 5.0
BOSCH_A_FALLBACK_RANGE_RATE_MAX_MPS = 50.0
BOSCH_A_USE_TAN_LATERAL_PROJECTION = True
BOSCH_A_VREL_MAX_SAMPLES = 8
BOSCH_A_STALE_S = 0.2


@dataclass
class _BoschASlotState:
  last_seen_nanos: int | None = None
  logical_00c9_raw: float = float("nan")
  logical_00ca_raw: float = float("nan")
  direct_vrel_raw: int | None = None
  direct_vrel_uncertainty_raw: int | None = None

  def reset(self):
    self.last_seen_nanos = None
    self.logical_00c9_raw = float("nan")
    self.logical_00ca_raw = float("nan")
    self.direct_vrel_raw = None
    self.direct_vrel_uncertainty_raw = None


@dataclass
class _BoschATrackState:
  track_id: int
  prev_frame_idx: int | None = None
  prev_life: int | None = None
  last_seen_nanos: int | None = None
  wire_slot: int | None = None
  samples: deque = field(default_factory=lambda: deque(maxlen=BOSCH_A_VREL_MAX_SAMPLES))
  last_trusted_vrel: float | None = None
  last_trusted_vrel_nanos: int | None = None


def _bosch_a_direct_vrel(raw_value: int | float | None, uncertainty_raw: int | float | None = None) -> float | None:
  if raw_value is None:
    return None
  raw = int(raw_value)
  if raw == BOSCH_A_DIRECT_VREL_INVALID:
    return None
  if not BOSCH_A_DIRECT_VREL_MIN_RAW <= raw <= BOSCH_A_DIRECT_VREL_MAX_RAW:
    return None
  if uncertainty_raw is not None and int(uncertainty_raw) > BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW:
    return None
  return (raw - BOSCH_A_DIRECT_VREL_CENTER_RAW) * BOSCH_A_DIRECT_VREL_SCALE_MPS


def _bosch_a_range_ratio(raw_value: int | float | None) -> float | None:
  if raw_value is None:
    return None
  raw = int(raw_value)
  if raw == BOSCH_A_RANGE_RATIO_INVALID or not 0 <= raw < BOSCH_A_RANGE_RATIO_INVALID:
    return None
  return BOSCH_A_RANGE_RATIO_OFFSET + BOSCH_A_RANGE_RATIO_SCALE * raw


def _bosch_a_range_ratio_vrel(raw_value: int | float | None, d_rel: float, dt: float) -> float | None:
  ratio = _bosch_a_range_ratio(raw_value)
  if ratio is None or dt <= 0.0 or (not math.isfinite(d_rel)):
    return None
  return d_rel * (1.0 - ratio) / dt


def _bosch_a_measurement_degraded(range_sigma_raw: int, existence_raw: int, direct_vrel_uncertainty_raw: int | None) -> bool:
  range_quality_bad = range_sigma_raw >= BOSCH_A_RANGE_SIGMA_DEGRADED_RAW or existence_raw in (0, 127)
  velocity_quality_bad = direct_vrel_uncertainty_raw is not None and direct_vrel_uncertainty_raw > BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW
  return range_quality_bad or velocity_quality_bad


def _create_bosch_a_can_parser(CP):
  messages = [(addr, BOSCH_A_FREQ_HZ) for addr in BOSCH_A_ALL_IDS]
  main_signals = (
    ("FRAME_IDX", "STATUS", "RANGE_RAW", "AZIMUTH_RAW", "RANGE_SIGMA_RAW"),
    ("FRAME_IDX", "OBJECT_EXISTENCE_PROBABILITY_RAW"),
    ("FRAME_IDX", "LIFECYCLE_RAW"),
    ("FRAME_IDX", "TRACK_ID"),
  )
  signals: dict[str | int, Sequence[str]] = {addr: main_signals[index] for ids in BOSCH_A_MAIN_IDS for index, addr in enumerate(ids)}
  signals.update(dict.fromkeys(BOSCH_A_AUX_IDS, ("FRAME_IDX", "REL_VELOCITY_RAW", "REL_VELOCITY_UNCERTAINTY_RAW",
                                               "RANGE_RATIO_RAW", "FW_LID_00C9_RAW", "FW_LID_00CA_RAW")))
  return CANParser(DBC[CP.carFingerprint][Bus.radar], messages, CanBus(CP).camera, signals=signals)


VERIFIED_BOSCH_A_CARS = frozenset((CAR.HONDA_CIVIC_BOSCH, CAR.HONDA_CRV_5G))


class BoschARadar(RadarInterfaceBase):
  def __init__(self, CP):
    super().__init__(CP)
    if CP.carFingerprint not in VERIFIED_BOSCH_A_CARS or CP.flags & (HondaFlags.BOSCH_RADARLESS | HondaFlags.BOSCH_CANFD | HondaFlags.BOSCH_ALT_RADAR):
      raise ValueError("Honda radar requires its verified classic Bosch-A profile")
    self.rcp = _create_bosch_a_can_parser(CP)
    self.trigger_msg = BOSCH_A_TRIGGER_MSG
    self._slots = [_BoschASlotState() for _ in range(BOSCH_A_NUM_SLOTS)]
    self._tracks = {}
    self._slot_track_ids = [None] * BOSCH_A_NUM_SLOTS
    self._last_trigger_nanos = -1
    self.updated_messages = set()

  def update(self, can_strings):
    self.updated_messages.update(self.rcp.update(can_strings))
    if self.trigger_msg not in self.updated_messages:
      if self._last_trigger_nanos >= 0 and (self.rcp._last_update_nanos - self._last_trigger_nanos) * 1e-09 > BOSCH_A_STALE_S:
        return self._bosch_a_stale_radardata()
      return None
    result = self._update_bosch_a(self.updated_messages)
    self.updated_messages.clear()
    return result

  def _bosch_a_stale_radardata(self):
    self.pts.clear()
    self._tracks.clear()
    self._slot_track_ids = [None] * BOSCH_A_NUM_SLOTS
    for slot_state in self._slots:
      slot_state.reset()
    self._last_trigger_nanos = -1
    stale = structs.RadarData()
    if not self.rcp.can_valid:
      stale.errors.canError = True
    stale.errors.radarUnavailableTemporary = True
    return stale

  def _bosch_a_retire_track(self, track_id: int):
    self._tracks.pop(track_id, None)
    self.pts.pop(track_id, None)
    for slot, slot_track_id in enumerate(self._slot_track_ids):
      if slot_track_id == track_id:
        self._slot_track_ids[slot] = None

  def _bosch_a_retire_stale_tracks(self, now: int):
    for track_id, track in list(self._tracks.items()):
      if track.last_seen_nanos is not None and (now - track.last_seen_nanos) * 1e-09 > BOSCH_A_STALE_S:
        self._bosch_a_retire_track(track_id)

  def _update_bosch_a(self, updated_messages):
    ret = structs.RadarData()
    if not self.rcp.can_valid:
      ret.errors.canError = True
    now = self.rcp._last_update_nanos
    self._last_trigger_nanos = now
    self._bosch_a_retire_stale_tracks(now)
    observations = []
    for slot in range(BOSCH_A_NUM_SLOTS):
      f0, f1, f2, f3 = BOSCH_A_MAIN_IDS[slot]
      aux = BOSCH_A_AUX_IDS[slot]
      st = self._slots[slot]
      if not (f0 in updated_messages and f1 in updated_messages and (f2 in updated_messages) and (f3 in updated_messages)):
        continue
      v0 = self.rcp.vl[f0]
      v1 = self.rcp.vl[f1]
      v2 = self.rcp.vl[f2]
      v3 = self.rcp.vl[f3]
      idx0 = int(v0["FRAME_IDX"])
      if not idx0 == int(v1["FRAME_IDX"]) == int(v2["FRAME_IDX"]) == int(v3["FRAME_IDX"]):
        continue
      status = int(v0["STATUS"])
      range_raw = int(v0["RANGE_RAW"])
      angle_raw = int(v0["AZIMUTH_RAW"])
      range_sigma_raw = int(v0["RANGE_SIGMA_RAW"])
      existence_raw = int(v1["OBJECT_EXISTENCE_PROBABILITY_RAW"])
      life = int(v2["LIFECYCLE_RAW"])
      track_id = int(v3["TRACK_ID"])
      track_id_valid = BOSCH_A_TRACK_ID_MIN <= track_id <= BOSCH_A_TRACK_ID_MAX
      st.last_seen_nanos = now
      direct_vrel_raw = None
      direct_vrel_uncertainty_raw = None
      range_ratio_raw = None
      if aux in updated_messages:
        av = self.rcp.vl[aux]
        if int(av["FRAME_IDX"]) == idx0:
          direct_vrel_raw = int(av["REL_VELOCITY_RAW"])
          direct_vrel_uncertainty_raw = int(av["REL_VELOCITY_UNCERTAINTY_RAW"])
          range_ratio_raw = int(av["RANGE_RATIO_RAW"])
          logical_00c9_raw = av["FW_LID_00C9_RAW"]
          logical_00ca_raw = av["FW_LID_00CA_RAW"]
          st.logical_00c9_raw = logical_00c9_raw
          st.logical_00ca_raw = logical_00ca_raw if logical_00ca_raw != BOSCH_A_LOGICAL_00CA_INVALID else float("nan")
          st.direct_vrel_raw = direct_vrel_raw
          st.direct_vrel_uncertainty_raw = direct_vrel_uncertainty_raw
      object_valid = (
        status != BOSCH_A_STATUS_INVALID
        and range_raw != BOSCH_A_RANGE_RAW_INVALID
        and (angle_raw != BOSCH_A_ANGLE_RAW_INVALID)
        and (life != BOSCH_A_LIFE_INVALID)
      )
      observations.append(
        {
          "slot": slot,
          "frame_idx": idx0,
          "life": life,
          "track_id": track_id,
          "track_id_valid": track_id_valid,
          "object_valid": object_valid,
          "range_sigma_raw": range_sigma_raw,
          "existence_raw": existence_raw,
          "direct_vrel_raw": direct_vrel_raw,
          "direct_vrel_uncertainty_raw": direct_vrel_uncertainty_raw,
          "range_ratio_raw": range_ratio_raw,
        }
      )
    valid_by_id = {}
    for observation in observations:
      if not (observation["object_valid"] and observation["track_id_valid"]):
        ids_to_hide = {self._slot_track_ids[observation["slot"]]}
        if observation["track_id_valid"]:
          ids_to_hide.add(observation["track_id"])
        for invalid_id in ids_to_hide - {None}:
          self.pts.pop(invalid_id, None)
        continue
      track_id = observation["track_id"]
      current = valid_by_id.get(track_id)
      if current is None:
        valid_by_id[track_id] = observation
        continue
      track = self._tracks.get(track_id)
      current_score = (0 if track is not None and track.wire_slot == current["slot"] else 1, current["slot"])
      candidate_score = (0 if track is not None and track.wire_slot == observation["slot"] else 1, observation["slot"])
      if candidate_score < current_score:
        valid_by_id[track_id] = observation
    valid_ids = set(valid_by_id)
    for track_id, observation in sorted(valid_by_id.items(), key=lambda item: item[1]["slot"]):
      slot = observation["slot"]
      idx0 = observation["frame_idx"]
      life = observation["life"]
      old_id = self._slot_track_ids[slot]
      if old_id is not None and old_id != track_id and (old_id not in valid_ids):
        self.pts.pop(old_id, None)
      track = self._tracks.get(track_id)
      if track is None:
        track = _BoschATrackState(track_id=track_id)
        self._tracks[track_id] = track
      same_incarnation = False
      if track.prev_frame_idx is not None and track.prev_life is not None:
        frame_delta = idx0 - track.prev_frame_idx & 15
        life_delta = life - track.prev_life & 4095
        same_incarnation = life_delta == 2 * frame_delta
      if not same_incarnation:
        track.samples.clear()
        track.last_trusted_vrel = None
        track.last_trusted_vrel_nanos = None
        self.pts.pop(track_id, None)
      v0 = self.rcp.vl[BOSCH_A_MAIN_IDS[slot][0]]
      range_raw = int(v0["RANGE_RAW"])
      angle_raw = int(v0["AZIMUTH_RAW"])
      dRel = BOSCH_A_RANGE_SCALE_M * range_raw + BOSCH_A_RANGE_OFFSET_M
      azimuth_rad = BOSCH_A_AZIMUTH_SCALE_RAD * (angle_raw - BOSCH_A_AZIMUTH_CENTER)
      lateral_projection = math.tan if BOSCH_A_USE_TAN_LATERAL_PROJECTION else math.sin
      yRel = dRel * lateral_projection(azimuth_rad)
      now_s = now * 1e-09
      direct_vrel_raw = observation["direct_vrel_raw"]
      direct_vrel_uncertainty_raw = observation["direct_vrel_uncertainty_raw"]
      direct_vrel = _bosch_a_direct_vrel(direct_vrel_raw, direct_vrel_uncertainty_raw)
      live_direct_vrel = _bosch_a_direct_vrel(direct_vrel_raw)
      range_ratio_raw = observation["range_ratio_raw"]
      high_u10_live_vrel = (
        direct_vrel is None
        and live_direct_vrel is not None
        and (direct_vrel_uncertainty_raw is not None)
        and (direct_vrel_uncertainty_raw > BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW)
      )
      previous_sample = track.samples[-1] if track.samples else None
      fallback_vrel = 0.0
      ratio_vrel = None
      range_rejected = False
      degraded = _bosch_a_measurement_degraded(observation["range_sigma_raw"], observation["existence_raw"], direct_vrel_uncertainty_raw)
      if previous_sample is not None:
        previous_time, previous_range = previous_sample
        dt = now_s - previous_time
        if dt <= 0.0:
          range_rejected = True
        else:
          fallback_vrel = (dRel - previous_range) / dt
          ratio = _bosch_a_range_ratio(range_ratio_raw)
          ratio_vrel = _bosch_a_range_ratio_vrel(range_ratio_raw, dRel, dt)
          residuals_m = []
          if direct_vrel is not None:
            residuals_m.append(abs(dRel - (previous_range + direct_vrel * dt)))
          if ratio is not None:
            residuals_m.append(abs(previous_range - dRel * ratio))
          if residuals_m:
            innovation_m = min(residuals_m)
            range_rejected = innovation_m > BOSCH_A_RANGE_INNOVATION_HARD_MAX_M or (degraded and innovation_m > BOSCH_A_RANGE_INNOVATION_MAX_M)
          else:
            range_rejected = abs(fallback_vrel) > BOSCH_A_FALLBACK_RANGE_RATE_MAX_MPS
      if range_rejected:
        accepted_fresh = previous_sample is not None and now_s - previous_sample[0] <= BOSCH_A_STALE_S
        point = self.pts.get(track_id)
        if accepted_fresh and point is not None:
          point.deprecated.measured = False
        else:
          self.pts.pop(track_id, None)
        track.prev_frame_idx = idx0
        track.prev_life = life
        track.last_seen_nanos = now
        track.wire_slot = slot
        for old_slot, old_id in enumerate(self._slot_track_ids):
          if old_slot != slot and old_id == track_id:
            self._slot_track_ids[old_slot] = None
        self._slot_track_ids[slot] = track_id
        continue
      if high_u10_live_vrel:
        trusted_fresh = (
          track.last_trusted_vrel is not None
          and track.last_trusted_vrel_nanos is not None
          and ((now - track.last_trusted_vrel_nanos) * 1e-09 <= BOSCH_A_STALE_S)
        )
        if trusted_fresh:
          point = self.pts.get(track_id)
          if point is not None:
            point.dRel = dRel
            point.yRel = yRel
            point.vRel = track.last_trusted_vrel
            point.deprecated.measured = False
        else:
          track.last_trusted_vrel = None
          track.last_trusted_vrel_nanos = None
          self.pts.pop(track_id, None)
        track.prev_frame_idx = idx0
        track.prev_life = life
        track.last_seen_nanos = now
        track.wire_slot = slot
        for old_slot, old_id in enumerate(self._slot_track_ids):
          if old_slot != slot and old_id == track_id:
            self._slot_track_ids[old_slot] = None
        self._slot_track_ids[slot] = track_id
        continue
      u11_and_ratio_unavailable = direct_vrel is None and (ratio_vrel is None or degraded) and (previous_sample is not None)
      if u11_and_ratio_unavailable:
        trusted_fresh = (
          track.last_trusted_vrel is not None
          and track.last_trusted_vrel_nanos is not None
          and ((now - track.last_trusted_vrel_nanos) * 1e-09 <= BOSCH_A_STALE_S)
        )
        if trusted_fresh:
          point = self.pts.get(track_id)
          if point is not None:
            point.dRel = dRel
            point.yRel = yRel
            point.vRel = track.last_trusted_vrel
            point.deprecated.measured = False
        else:
          track.last_trusted_vrel = None
          track.last_trusted_vrel_nanos = None
          self.pts.pop(track_id, None)
        track.prev_frame_idx = idx0
        track.prev_life = life
        track.last_seen_nanos = now
        track.wire_slot = slot
        for old_slot, old_id in enumerate(self._slot_track_ids):
          if old_slot != slot and old_id == track_id:
            self._slot_track_ids[old_slot] = None
        self._slot_track_ids[slot] = track_id
        continue
      track.samples.append((now_s, dRel))
      sample_count = len(track.samples)
      if direct_vrel is not None:
        vRel = direct_vrel
      else:
        vRel = ratio_vrel
      trustworthy_vrel = True
      matured = sample_count >= 2 and math.isfinite(vRel)
      if trustworthy_vrel:
        track.last_trusted_vrel = vRel
        track.last_trusted_vrel_nanos = now
      if matured and track_id not in self.pts:
        self.pts[track_id] = structs.RadarData.RadarPoint()
        self.pts[track_id].trackId = track_id
        self.pts[track_id].deprecated.aRel = float("nan")
        self.pts[track_id].deprecated.yvRel = float("nan")
      if matured:
        self.pts[track_id].dRel = dRel
        self.pts[track_id].yRel = yRel
        self.pts[track_id].vRel = vRel
        self.pts[track_id].deprecated.measured = True
      else:
        self.pts.pop(track_id, None)
      track.prev_frame_idx = idx0
      track.prev_life = life
      track.last_seen_nanos = now
      track.wire_slot = slot
      for old_slot, old_id in enumerate(self._slot_track_ids):
        if old_slot != slot and old_id == track_id:
          self._slot_track_ids[old_slot] = None
      self._slot_track_ids[slot] = track_id
    ret.points = [self.pts[track_id] for track_id in sorted(self.pts)]
    return ret
