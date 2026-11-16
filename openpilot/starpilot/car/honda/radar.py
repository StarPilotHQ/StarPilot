from __future__ import annotations

import math
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import capnp

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import DT_MDL
from openpilot.common.simple_kalman import KF1D

if TYPE_CHECKING:
  from openpilot.selfdrive.controls.radard import KalmanParams

_LEAD_ACCEL_TAU = 0.6
V_EGO_STATIONARY = 4.0
RADAR_TO_CAMERA = 1.52
HONDA_BOSCH_A_LOW_SPEED_MIN_COUNT = 3
HONDA_BOSCH_A_CHALLENGER_STALE_CYCLES = 2
HONDA_BOSCH_A_GROSS_DISTANCE_STALE_CYCLES = 3
HONDA_BOSCH_A_GROSS_DISTANCE_M = 25.0
POST_STANDSTILL_RADAR_LEAD_PERSISTENCE_FRAMES = 3
POST_STANDSTILL_RADAR_LEAD_URGENT_TTC = 1.5
POST_STANDSTILL_RADAR_LEAD_URGENT_DISTANCE = 1.5
SPEED, ACCEL = (0, 1)


class Track:
  def __init__(self, identifier: int, v_lead: float, kalman_params: KalmanParams):
    self.identifier = identifier
    self.cnt = 0
    self.aLeadTau = FirstOrderFilter(_LEAD_ACCEL_TAU, 0.45, DT_MDL)
    self.K_A = kalman_params.A
    self.K_C = kalman_params.C
    self.K_K = kalman_params.K
    self.kf = KF1D([[v_lead], [0.0]], self.K_A, self.K_C, self.K_K)

  def update(self, d_rel: float, y_rel: float, v_rel: float, v_lead: float, measured: bool, measurement_update: bool | None = None):
    self.dRel = d_rel
    self.yRel = y_rel
    self.vRel = v_rel
    self.vLead = v_lead
    self.measured = measured
    if measurement_update is None:
      measurement_update = True
    if measurement_update and self.cnt > 0:
      self.kf.update(self.vLead)
    self.vLeadK = float(self.kf.x[SPEED][0])
    self.aLeadK = float(self.kf.x[ACCEL][0])
    if measurement_update:
      if abs(self.aLeadK) < 0.5:
        self.aLeadTau.x = min(max(self.aLeadTau.x, 0.01) * 1.1, _LEAD_ACCEL_TAU)
      else:
        self.aLeadTau.update(0.0)
      self.cnt += 1

  def get_RadarState(self, model_prob: float = 0.0):
    return {
      'dRel': float(self.dRel),
      'yRel': float(self.yRel),
      'vRel': float(self.vRel),
      'vLead': float(self.vLead),
      'vLeadK': float(self.vLeadK),
      'aLeadK': float(self.aLeadK),
      'aLeadTau': float(self.aLeadTau.x),
      'present': True,
      'deprecated': {'fcw': self.is_potential_fcw(model_prob)},
      'modelProb': model_prob,
      'radar': True,
      'radarTrackId': self.identifier,
    }

  def potential_low_speed_lead(self, v_ego: float):
    return abs(self.yRel) < 1.0 and v_ego < V_EGO_STATIONARY and (0.75 < self.dRel < 25)

  def is_potential_fcw(self, model_prob: float):
    return model_prob > 0.9

  def __str__(self):
    ret = f'x: {self.dRel:4.1f}  y: {self.yRel:4.1f}  v: {self.vRel:4.1f}  a: {self.aLeadK:4.1f}'
    return ret


def laplacian_pdf(x: float, mu: float, b: float):
  b = max(b, 0.0001)
  return math.exp(-abs(x - mu) / b)


def vision_track_probability(track: Track, lead: capnp._DynamicStructReader, v_ego: float) -> float:
  offset_vision_dist = lead.x[0] - RADAR_TO_CAMERA
  prob_d = laplacian_pdf(track.dRel, offset_vision_dist, lead.xStd[0])
  prob_y = laplacian_pdf(track.yRel, -lead.y[0], lead.yStd[0])
  prob_v = laplacian_pdf(track.vRel + v_ego, lead.v[0], lead.vStd[0])
  return prob_d * prob_y * prob_v


def honda_bosch_a_low_speed_radar_lead_sane(track: Track, v_ego: float) -> bool:
  return track.cnt >= HONDA_BOSCH_A_LOW_SPEED_MIN_COUNT and track.potential_low_speed_lead(v_ego)


def post_standstill_radar_lead_is_urgent(lead: dict[str, Any]) -> bool:
  d_rel = float(lead.get('dRel', math.inf))
  v_rel = float(lead.get('vRel', 0.0))
  closing_speed = max(-v_rel, 0.0)
  ttc = d_rel / closing_speed if closing_speed > 0.1 else math.inf
  return d_rel <= POST_STANDSTILL_RADAR_LEAD_URGENT_DISTANCE or ttc <= POST_STANDSTILL_RADAR_LEAD_URGENT_TTC


def track_matches_vision(
  track: Track, lead: capnp._DynamicStructReader, v_ego: float, *, dist_scale: float, dist_floor: float, vel_limit: float, y_std_scale: float, y_floor: float
) -> bool:
  offset_vision_dist = lead.x[0] - RADAR_TO_CAMERA
  dist_sane = abs(track.dRel - offset_vision_dist) < max(abs(offset_vision_dist) * dist_scale, dist_floor)
  vel_sane = abs(track.vRel + v_ego - lead.v[0]) < vel_limit or v_ego + track.vRel > 3
  lat_sane = abs(track.yRel + lead.y[0]) < max(y_floor, y_std_scale * max(float(lead.yStd[0]), 0.2))
  return dist_sane and vel_sane and lat_sane


def vision_braking_priority(track: Track, lead: capnp._DynamicStructReader) -> bool:
  if not lead.a or not all(math.isfinite(value) for value in (lead.a[0], lead.v[0])):
    return True
  return lead.a[0] < min(track.aLeadK, 0.0) or track.vLead - lead.v[0] > 10.0


def match_vision_to_track(
  v_ego: float,
  lead: capnp._DynamicStructReader,
  tracks: dict[int, Track],
  preferred_track_id: int = -1,
):
  if not tracks:
    return None
  track = max(tracks.values(), key=lambda candidate: vision_track_probability(candidate, lead, v_ego))
  if track_matches_vision(track, lead, v_ego, dist_scale=0.25, dist_floor=5.0, vel_limit=10.0, y_std_scale=1.0, y_floor=1.0):
    return track
  preferred_track = tracks.get(preferred_track_id)
  if preferred_track is not None and preferred_track.cnt >= 3:
    if track_matches_vision(preferred_track, lead, v_ego, dist_scale=0.4, dist_floor=8.0, vel_limit=13.0, y_std_scale=2.0, y_floor=1.5):
      return preferred_track
    # Keep a moving radar lead through a brief farther vision outlier; the policy bounds this grace.
    if (preferred_track.vLead > 3.0 and not vision_braking_priority(preferred_track, lead) and
        lead.x[0] - RADAR_TO_CAMERA > preferred_track.dRel and track_matches_vision(
                preferred_track, lead, v_ego, dist_scale=1.0, dist_floor=8.0, vel_limit=13.0, y_std_scale=3.0, y_floor=1.5)):
      return preferred_track
  return None


def get_RadarState_from_vision(lead_msg: capnp._DynamicStructReader, v_ego: float, model_v_ego: float, model_prob: float, vision_state):
  prev_aLeadK = getattr(vision_state, 'prev_aLeadK', 0.0)
  blended_aLeadK = 0.8 * float(lead_msg.a[0]) + 0.2 * prev_aLeadK
  vision_state.prev_aLeadK = blended_aLeadK
  return {
    'dRel': float(lead_msg.x[0] - RADAR_TO_CAMERA),
    'yRel': float(-lead_msg.y[0]),
    'vRel': float(lead_msg.v[0] - model_v_ego),
    'vLead': float(v_ego + (lead_msg.v[0] - model_v_ego)),
    'vLeadK': float(v_ego + (lead_msg.v[0] - model_v_ego)),
    'aLeadK': blended_aLeadK,
    'aLeadTau': 0.3,
    'deprecated': {'fcw': False},
    'modelProb': float(model_prob),
    'present': True,
    'radar': False,
    'radarTrackId': -1,
  }


def get_lead(
  v_ego: float,
  ready: bool,
  tracks: dict[int, Track],
  lead_msg: capnp._DynamicStructReader,
  model_v_ego: float,
  starpilot_toggles: SimpleNamespace,
  low_speed_override: bool = True,
  lead_prob: float | None = None,
  preferred_track_id: int = -1,
  *,
  vision_state,
) -> dict[str, Any]:
  lead_detection_probability = float(getattr(starpilot_toggles, 'lead_detection_probability', 0.35))
  filtered_lead_prob = float(lead_msg.prob if lead_prob is None else lead_prob)
  if len(tracks) > 0 and ready and (filtered_lead_prob > lead_detection_probability):
    track = match_vision_to_track(v_ego, lead_msg, tracks, preferred_track_id=preferred_track_id)
  else:
    track = None
  lead_dict = {'present': False}
  if track is not None:
    lead_dict = track.get_RadarState(filtered_lead_prob)
  elif track is None and ready and (filtered_lead_prob > lead_detection_probability):
    lead_dict = get_RadarState_from_vision(lead_msg, v_ego, model_v_ego, filtered_lead_prob, vision_state)
  if low_speed_override:
    low_speed_tracks = [c for c in tracks.values() if honda_bosch_a_low_speed_radar_lead_sane(c, v_ego)]
    model_lead_available = ready and filtered_lead_prob > lead_detection_probability
    preferred_track = tracks.get(preferred_track_id)
    if preferred_track is not None and honda_bosch_a_low_speed_radar_lead_sane(preferred_track, v_ego):
      preferred_matches_model = not model_lead_available or track_matches_vision(
        preferred_track, lead_msg, v_ego, dist_scale=0.25, dist_floor=5.0, vel_limit=10.0, y_std_scale=1.0, y_floor=1.0
      )
      preferred_is_current = (
        not lead_dict.get('present', False)
        or lead_dict.get('radarTrackId', -1) == preferred_track_id
        or (lead_dict.get('present', False) and (not lead_dict.get('radar', False)))
      )
      if preferred_is_current and preferred_matches_model:
        lead_dict = preferred_track.get_RadarState(filtered_lead_prob)

    def candidate_is_established(candidate: Track) -> bool:
      if candidate.cnt < HONDA_BOSCH_A_LOW_SPEED_MIN_COUNT:
        return False
      if not lead_dict.get('present', False):
        return True
      if lead_dict.get('radarTrackId', -1) == candidate.identifier:
        return True
      return model_lead_available and track_matches_vision(
        candidate, lead_msg, v_ego, dist_scale=0.25, dist_floor=5.0, vel_limit=10.0, y_std_scale=1.0, y_floor=1.0
      )

    low_speed_tracks = [c for c in low_speed_tracks if candidate_is_established(c)]
    if len(low_speed_tracks) > 0:
      closest_track = min(low_speed_tracks, key=lambda c: c.dRel)
      if not lead_dict['present'] or closest_track.dRel < lead_dict['dRel']:
        lead_dict = closest_track.get_RadarState()
  return lead_dict


class BoschALeadPolicy:
  def _reset_preferred_stale_evidence(self, lead_index: int, track_id: int = -1) -> None:
    self.preferred_stale_track_ids[lead_index] = track_id
    self.preferred_challenger_stale_counts[lead_index] = 0
    self.preferred_gross_distance_stale_counts[lead_index] = 0

  def _update_honda_bosch_a_preferred_staleness(self, lead_index: int, lead: capnp._DynamicStructReader, lead_prob: float) -> None:
    preferred_id = self.prev_lead_track_ids[lead_index]
    if self.preferred_stale_track_ids[lead_index] != preferred_id:
      self._reset_preferred_stale_evidence(lead_index, preferred_id)
    lead_detection_probability = float(getattr(self.starpilot_toggles, 'lead_detection_probability', 0.35))
    preferred_track = self.tracks.get(preferred_id)
    if preferred_id < 0 or preferred_track is None or (not self.ready) or (lead_prob <= lead_detection_probability):
      self._reset_preferred_stale_evidence(lead_index, preferred_id)
      return
    strict_match = track_matches_vision(preferred_track, lead, self.v_ego, dist_scale=0.25, dist_floor=5.0, vel_limit=10.0, y_std_scale=1.0, y_floor=1.0)
    relaxed_match = track_matches_vision(preferred_track, lead, self.v_ego, dist_scale=0.4, dist_floor=8.0, vel_limit=13.0, y_std_scale=2.0, y_floor=1.5)
    if relaxed_match:
      self.preferred_challenger_stale_counts[lead_index] = 0
    else:
      best_track = max(self.tracks.values(), key=lambda candidate: vision_track_probability(candidate, lead, self.v_ego))
      preferred_score = vision_track_probability(preferred_track, lead, self.v_ego)
      best_score = vision_track_probability(best_track, lead, self.v_ego)
      if best_track.identifier != preferred_id and best_score > preferred_score:
        self.preferred_challenger_stale_counts[lead_index] += 1
      else:
        self.preferred_challenger_stale_counts[lead_index] = 0
    distance_mismatch = abs(preferred_track.dRel - (lead.x[0] - RADAR_TO_CAMERA))
    if strict_match:
      self.preferred_gross_distance_stale_counts[lead_index] = 0
    elif ((not relaxed_match or preferred_track.vLead <= 3.0 or vision_braking_priority(preferred_track, lead)) and
          (distance_mismatch > HONDA_BOSCH_A_GROSS_DISTANCE_M or
           (not relaxed_match and lead.x[0] - RADAR_TO_CAMERA > preferred_track.dRel))):
      self.preferred_gross_distance_stale_counts[lead_index] += 1
    else:
      self.preferred_gross_distance_stale_counts[lead_index] = 0
    challenger_stale = self.preferred_challenger_stale_counts[lead_index] >= HONDA_BOSCH_A_CHALLENGER_STALE_CYCLES
    distance_stale = self.preferred_gross_distance_stale_counts[lead_index] >= HONDA_BOSCH_A_GROSS_DISTANCE_STALE_CYCLES
    if challenger_stale or distance_stale:
      self.prev_lead_track_ids[lead_index] = -1
      self._reset_preferred_stale_evidence(lead_index)

  def _prepare_post_standstill_gate(self, standstill: bool) -> None:
    if standstill:
      self._post_standstill_gate_active = False
      self._post_standstill_candidate_id = -1
      self._post_standstill_candidate_frames = 0
    elif self._was_standstill and (not self._standstill_had_lead):
      self._post_standstill_gate_active = True
      self._post_standstill_candidate_id = -1
      self._post_standstill_candidate_frames = 0

  def _filter_post_standstill_lead(self, lead: dict[str, Any]) -> dict[str, Any]:
    if not self._post_standstill_gate_active:
      return lead
    model_lead = float(lead.get('modelProb', 0.0)) > float(getattr(self.starpilot_toggles, 'lead_detection_probability', 0.35))
    radar_only = bool(lead.get('present', False) and lead.get('radar', False) and (not model_lead))
    if not radar_only:
      if lead.get('present', False):
        self._post_standstill_gate_active = False
      self._post_standstill_candidate_id = -1
      self._post_standstill_candidate_frames = 0
      return lead
    track_id = int(lead.get('radarTrackId', -1))
    if track_id == self._post_standstill_candidate_id:
      self._post_standstill_candidate_frames += 1
    else:
      self._post_standstill_candidate_id = track_id
      self._post_standstill_candidate_frames = 1
    persistent = self._post_standstill_candidate_frames >= POST_STANDSTILL_RADAR_LEAD_PERSISTENCE_FRAMES
    if persistent or post_standstill_radar_lead_is_urgent(lead):
      self._post_standstill_gate_active = False
      return lead
    return {'present': False}

  def _remember_post_standstill_state(self, standstill: bool, lead_status: bool) -> None:
    if standstill:
      self._was_standstill = True
      self._standstill_had_lead |= lead_status
    else:
      self._was_standstill = False
      self._standstill_had_lead = False

  def __init__(self):
    self.starpilot_toggles = SimpleNamespace(lead_detection_probability=0.35)
    self.vision_state = SimpleNamespace(prev_aLeadK=0.0)
    self.prev_lead_track_ids = [-1, -1]
    self.preferred_stale_track_ids = [-1, -1]
    self.preferred_challenger_stale_counts = [0, 0]
    self.preferred_gross_distance_stale_counts = [0, 0]
    self._was_standstill = False
    self._standstill_had_lead = False
    self._post_standstill_gate_active = False
    self._post_standstill_candidate_id = -1
    self._post_standstill_candidate_frames = 0
    self._last_tracks_frame = -1

  def update_tracks(self, sm, rr, radar):
    self.ready = radar.ready
    self.v_ego = radar.v_ego
    self.tracks = radar.tracks
    self._prepare_post_standstill_gate(bool(sm['carState'].standstill))
    fresh = sm.recv_frame['radarTracks'] != self._last_tracks_frame
    self._last_tracks_frame = sm.recv_frame['radarTracks']
    points = {point.trackId: point for point in rr.points}
    for identifier in list(self.tracks):
      if identifier not in points:
        del self.tracks[identifier]
    if len(sm['modelV2'].leadsV3) < 2:
      self._remember_post_standstill_state(bool(sm['carState'].standstill), False)
    for identifier, point in points.items():
      v_lead = point.vRel + radar.v_ego_hist[0]
      if identifier not in self.tracks:
        self.tracks[identifier] = Track(identifier, v_lead, radar.kalman_params)
      measured = bool(point.deprecated.measured and fresh)
      self.tracks[identifier].update(point.dRel, point.yRel, point.vRel, v_lead, measured, measured)

  def leads(self, sm, radar, model_v_ego):
    leads = sm['modelV2'].leadsV3
    for index in range(2):
      self._update_honda_bosch_a_preferred_staleness(index, leads[index], radar.lead_prob_filters[index].x)
    output = [
      get_lead(
        self.v_ego,
        self.ready,
        self.tracks,
        leads[index],
        model_v_ego,
        self.starpilot_toggles,
        low_speed_override=index == 0,
        lead_prob=radar.lead_prob_filters[index].x,
        preferred_track_id=self.prev_lead_track_ids[index],
        vision_state=self.vision_state,
      )
      for index in range(2)
    ]
    output[0] = self._filter_post_standstill_lead(output[0])
    for index, lead in enumerate(output):
      if lead.get('present', False) and lead.get('radar', False):
        identifier = int(lead.get('radarTrackId', -1))
        if identifier != self.prev_lead_track_ids[index]:
          self._reset_preferred_stale_evidence(index, identifier)
        self.prev_lead_track_ids[index] = identifier
      elif not lead.get('present', False) or self.prev_lead_track_ids[index] not in self.tracks:
        self.prev_lead_track_ids[index] = -1
        self._reset_preferred_stale_evidence(index)
    self._remember_post_standstill_state(bool(sm['carState'].standstill), bool(output[0].get('present', False)))
    return output
