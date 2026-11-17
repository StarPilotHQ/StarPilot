#!/usr/bin/env python3
import math
import numpy as np
from collections import deque
from typing import Any
import os
import secrets
import time

import capnp
from openpilot.cereal import messaging, log
from opendbc.car.structs import car
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL, Priority, config_realtime_process, set_core_affinity
from openpilot.common.swaglog import cloudlog
from openpilot.common.simple_kalman import KF1D
from openpilot.starpilot.conditional_mode.adjacent import (
  CLOCK_PAIR_MAX_SKEW_NS, MODEL_EOF_MAX_AGE_NS, MODEL_MAX_AGE_NS, SOURCE_MAX_AGE_NS,
  AdjacentFrame, AdjacentObservation, AdjacentTrack, MonoSource, UNKNOWN, evaluate,
)
from openpilot.starpilot.feature_runtime import enabled as feature_enabled
from openpilot.starpilot.longitudinal.voacc import selected as voacc_selected


# Default lead acceleration decay set to 50% at 1s
_LEAD_ACCEL_TAU = 1.5

# radar tracks
SPEED, ACCEL = 0, 1     # Kalman filter states enum

# stationary qualification parameters
V_EGO_STATIONARY = 4.   # no stationary object flag below this speed

RADAR_TO_CAMERA = 1.52  # RADAR is ~ 1.5m ahead from center of mesh frame


def _clock_pair() -> tuple[int, int, int] | None:
  """Return a bounded MONOTONIC/BOOTTIME pair; missing BOOTTIME stays unknown."""
  boot_clock = getattr(time, 'CLOCK_BOOTTIME', None)
  if boot_clock is None:
    return None
  try:
    before = time.monotonic_ns()
    boot = time.clock_gettime_ns(boot_clock)
    after = time.monotonic_ns()
  except OSError:
    return None
  return (before + after) // 2, boot, after - before


class KalmanParams:
  def __init__(self, dt: float):
    # Lead Kalman Filter params, calculating K from A, C, Q, R requires the control library.
    # hardcoding a lookup table to compute K for values of radar_ts between 0.01s and 0.2s
    assert dt > .01 and dt < .2, "Radar time step must be between .01s and 0.2s"
    self.A = [[1.0, dt], [0.0, 1.0]]
    self.C = [1.0, 0.0]
    #Q = np.matrix([[10., 0.0], [0.0, 100.]])
    #R = 1e3
    #K = np.matrix([[ 0.05705578], [ 0.03073241]])
    dts = [i * 0.01 for i in range(1, 21)]
    K0 = [0.12287673, 0.14556536, 0.16522756, 0.18281627, 0.1988689,  0.21372394,
          0.22761098, 0.24069424, 0.253096,   0.26491023, 0.27621103, 0.28705801,
          0.29750003, 0.30757767, 0.31732515, 0.32677158, 0.33594201, 0.34485814,
          0.35353899, 0.36200124]
    K1 = [0.29666309, 0.29330885, 0.29042818, 0.28787125, 0.28555364, 0.28342219,
          0.28144091, 0.27958406, 0.27783249, 0.27617149, 0.27458948, 0.27307714,
          0.27162685, 0.27023228, 0.26888809, 0.26758976, 0.26633338, 0.26511557,
          0.26393339, 0.26278425]
    self.K = [[np.interp(dt, dts, K0)], [np.interp(dt, dts, K1)]]


class Track:
  def __init__(self, identifier: int, v_lead: float, kalman_params: KalmanParams):
    self.identifier = identifier
    self.cnt = 0
    self.aLeadTau = FirstOrderFilter(_LEAD_ACCEL_TAU, 0.45, DT_MDL)
    self.K_A = kalman_params.A
    self.K_C = kalman_params.C
    self.K_K = kalman_params.K
    self.kf = KF1D([[v_lead], [0.0]], self.K_A, self.K_C, self.K_K)

  def update(self, d_rel: float, y_rel: float, v_rel: float, v_lead: float):
    # relative values, copy
    self.dRel = d_rel   # LONG_DIST
    self.yRel = y_rel   # -LAT_DIST
    self.vRel = v_rel   # REL_SPEED
    self.vLead = v_lead

    # computed velocity and accelerations
    if self.cnt > 0:
      self.kf.update(self.vLead)

    self.vLeadK = float(self.kf.x[SPEED][0])
    self.aLeadK = float(self.kf.x[ACCEL][0])

    # Learn if constant acceleration
    if abs(self.aLeadK) < 0.5:
      self.aLeadTau.x = _LEAD_ACCEL_TAU
    else:
      self.aLeadTau.update(0.0)

    self.cnt += 1

  def get_RadarState(self, model_prob: float = 0.0):
    return {
      "dRel": float(self.dRel),
      "yRel": float(self.yRel),
      "vRel": float(self.vRel),
      "vLead": float(self.vLead),
      "vLeadK": float(self.vLeadK),
      "aLeadK": float(self.aLeadK),
      "aLeadTau": float(self.aLeadTau.x),
      "present": True,
      "modelProb": model_prob,
      "radar": True,
      "radarTrackId": self.identifier,
    }

  def potential_low_speed_lead(self, v_ego: float):
    # stop for stuff in front of you and low speed, even without model confirmation
    # Radar points closer than 0.75, are almost always glitches on toyota radars
    return abs(self.yRel) < 1.0 and (v_ego < V_EGO_STATIONARY) and (0.75 < self.dRel < 25)

  def __str__(self):
    ret = f"x: {self.dRel:4.1f}  y: {self.yRel:4.1f}  v: {self.vRel:4.1f}  a: {self.aLeadK:4.1f}"
    return ret


def laplacian_pdf(x: float, mu: float, b: float):
  b = max(b, 1e-4)
  return math.exp(-abs(x-mu)/b)


def match_vision_to_track(v_ego: float, lead: capnp._DynamicStructReader, tracks: dict[int, Track]):
  offset_vision_dist = lead.x[0] - RADAR_TO_CAMERA

  def prob(c):
    prob_d = laplacian_pdf(c.dRel, offset_vision_dist, lead.xStd[0])
    prob_y = laplacian_pdf(c.yRel, -lead.y[0], lead.yStd[0])
    prob_v = laplacian_pdf(c.vRel + v_ego, lead.v[0], lead.vStd[0])

    # This isn't exactly right, but it's a good heuristic
    return prob_d * prob_y * prob_v

  track = max(tracks.values(), key=prob)

  # if no 'sane' match is found return -1
  # stationary radar points can be false positives
  dist_sane = abs(track.dRel - offset_vision_dist) < max([(offset_vision_dist)*.25, 5.0])
  vel_sane = (abs(track.vRel + v_ego - lead.v[0]) < 10) or (v_ego + track.vRel > 3)
  if dist_sane and vel_sane:
    return track
  else:
    return None


def get_RadarState_from_vision(lead_msg: capnp._DynamicStructReader, v_ego: float, model_v_ego: float, lead_prob: float):
  lead_v_rel_pred = lead_msg.v[0] - model_v_ego
  return {
    "dRel": float(lead_msg.x[0] - RADAR_TO_CAMERA),
    "yRel": float(-lead_msg.y[0]),
    "vRel": float(lead_v_rel_pred),
    "vLead": float(v_ego + lead_v_rel_pred),
    "vLeadK": float(v_ego + lead_v_rel_pred),
    "aLeadK": float(lead_msg.a[0]),
    "aLeadTau": 0.3,
    "modelProb": float(lead_prob),
    "present": True,
    "radar": False,
    "radarTrackId": -1,
  }


def get_lead(v_ego: float, ready: bool, tracks: dict[int, Track], lead_msg: capnp._DynamicStructReader,
             model_v_ego: float, lead_prob: float, low_speed_override: bool = True) -> dict[str, Any]:
  # Determine leads, this is where the essential logic happens
  if len(tracks) > 0 and ready and lead_prob > .5:
    track = match_vision_to_track(v_ego, lead_msg, tracks)
  else:
    track = None

  lead_dict = {'present': False}
  if track is not None:
    lead_dict = track.get_RadarState(lead_prob)
  elif (track is None) and ready and (lead_prob > .5):
    lead_dict = get_RadarState_from_vision(lead_msg, v_ego, model_v_ego, lead_prob)

  if low_speed_override:
    low_speed_tracks = [c for c in tracks.values() if c.potential_low_speed_lead(v_ego)]
    if len(low_speed_tracks) > 0:
      closest_track = min(low_speed_tracks, key=lambda c: c.dRel)

      # Only choose new track if it is actually closer than the previous one
      if (not lead_dict['present']) or (closest_track.dRel < lead_dict['dRel']):
        lead_dict = closest_track.get_RadarState()

  return lead_dict


class RadarD:
  def __init__(self, delay: float = 0.0, *, adjacent_enabled: bool = False, radar_available: bool = False,
               clock_pair_fn=None, honda_bosch_a_radar: bool = False, use_voacc: bool = False):
    self.use_voacc = use_voacc
    self.tracks: dict[int, Track] = {}
    self.honda_bosch_a = None
    if honda_bosch_a_radar:
      from openpilot.starpilot.car.honda.radar import BoschALeadPolicy
      self.honda_bosch_a = BoschALeadPolicy()
    from opendbc.car.honda.bosch_a_radar import BOSCH_A_FREQ_HZ
    self.kalman_params = KalmanParams(1.0 / BOSCH_A_FREQ_HZ if self.honda_bosch_a is not None else DT_MDL)
    self.lead_prob_filters = [FirstOrderFilter(0.0, 0.2, DT_MDL) for _ in range(2)]

    self.v_ego = 0.0
    self.v_ego_hist = deque([0.0], maxlen=int(round(delay / DT_MDL))+1)
    self.last_v_ego_frame = -1

    self.radar_state: capnp._DynamicStructBuilder | None = None
    self.radar_state_valid = False

    self.ready = False
    self.adjacent_enabled = adjacent_enabled
    self.adjacent_radar_available = radar_available
    self.adjacent_clock_pair_fn = clock_pair_fn or _clock_pair
    self.adjacent_session = secrets.token_hex(16)
    self.adjacent_sequence = 0
    self.adjacent_offset_ns: int | None = None
    self.adjacent_barrier_mono_ns = 0
    self.adjacent_barrier_boot_ns = 0
    self.adjacent_last_mono_ns = 0
    self.adjacent_last_boot_ns = 0
    self.adjacent_observation: AdjacentObservation = UNKNOWN
    self.adjacent_sources: tuple[int, int, int, int] = (0, 0, 0, 0)
    self.adjacent_valid_until_ns = 0

  def _qualified_adjacent(self, sm: messaging.SubMaster, rr: car.RadarData) -> None:
    pair = self.adjacent_clock_pair_fn()
    self.adjacent_observation = UNKNOWN
    self.adjacent_sources = (0, 0, 0, 0)
    self.adjacent_valid_until_ns = 0
    if pair is None or len(pair) != 3:
      return
    now_mono, now_boot, skew = pair
    if not all(type(value) is int for value in pair) or not 0 <= skew <= CLOCK_PAIR_MAX_SKEW_NS:
      return
    offset = now_boot - now_mono
    regressed = now_mono <= self.adjacent_last_mono_ns or now_boot <= self.adjacent_last_boot_ns
    previous_mono, previous_boot = self.adjacent_last_mono_ns, self.adjacent_last_boot_ns
    self.adjacent_last_mono_ns = max(now_mono, previous_mono)
    self.adjacent_last_boot_ns = max(now_boot, previous_boot)
    if (self.adjacent_offset_ns is None or regressed or
        abs(offset - self.adjacent_offset_ns) > max(CLOCK_PAIR_MAX_SKEW_NS, skew * 2)):
      self.adjacent_offset_ns = offset
      self.adjacent_barrier_mono_ns = max(now_mono, previous_mono)
      self.adjacent_barrier_boot_ns = max(now_boot, previous_boot)
      return
    radar_stamp = int(sm.logMonoTime['radarTracks'])
    model_stamp = int(sm.logMonoTime['modelV2'])
    car_stamp = int(sm.logMonoTime['carState'])
    camera_eof = int(sm['modelV2'].timestampEof)
    self.adjacent_sources = (radar_stamp, model_stamp, car_stamp, camera_eof)
    primary_ids = frozenset(
      int(getattr(lead, 'radarTrackId', -1)) for lead in (self.radar_state.leadOne, self.radar_state.leadTwo)
      if bool(getattr(lead, 'present', False)) and bool(getattr(lead, 'radar', False)) and int(getattr(lead, 'radarTrackId', -1)) >= 0
    )
    frame = AdjacentFrame(
      model=sm['modelV2'],
      tracks=tuple(AdjacentTrack(track.identifier, track.dRel, track.yRel, track.vLead) for track in self.tracks.values()),
      primary_track_ids=primary_ids,
      radar_available=self.adjacent_radar_available,
      radar_valid=bool(sm.valid['radarTracks'] and sm.updated['radarTracks']),
      radar_error_free=not any(rr.errors.to_dict().values()),
      model_valid=bool(sm.valid['modelV2']),
      car_valid=bool(sm.valid['carState']),
      standstill=bool(sm['carState'].standstill),
      ego_speed_mps=self.v_ego,
      radar=MonoSource(radar_stamp, int(sm.recv_time['radarTracks'] * 1e9)),
      model_source=MonoSource(model_stamp, int(sm.recv_time['modelV2'] * 1e9)),
      car=MonoSource(car_stamp, int(sm.recv_time['carState'] * 1e9)),
      model_eof_boot_ns=camera_eof,
      now_mono_ns=now_mono,
      now_boot_ns=now_boot,
      expected_boot_minus_mono_ns=self.adjacent_offset_ns,
      barrier_mono_ns=self.adjacent_barrier_mono_ns,
      barrier_boot_ns=self.adjacent_barrier_boot_ns,
      sample_skew_ns=skew,
    )
    self.adjacent_observation = evaluate(frame)
    if self.adjacent_observation.ambiguous is not None:
      self.adjacent_valid_until_ns = min(
        frame.radar.producer_ns + SOURCE_MAX_AGE_NS,
        frame.radar.receipt_ns + SOURCE_MAX_AGE_NS,
        frame.model_source.producer_ns + MODEL_MAX_AGE_NS,
        frame.model_source.receipt_ns + MODEL_MAX_AGE_NS,
        frame.car.producer_ns + SOURCE_MAX_AGE_NS,
        frame.car.receipt_ns + SOURCE_MAX_AGE_NS,
        frame.model_eof_boot_ns - frame.expected_boot_minus_mono_ns - frame.sample_skew_ns + MODEL_EOF_MAX_AGE_NS,
      )

  def update(self, sm: messaging.SubMaster, rr: car.RadarData):
    self.ready = sm.seen['modelV2']

    if sm.recv_frame['carState'] != self.last_v_ego_frame:
      self.v_ego = sm['carState'].vEgo
      self.v_ego_hist.append(self.v_ego)
      self.last_v_ego_frame = sm.recv_frame['carState']

    if self.honda_bosch_a is not None:
      self.honda_bosch_a.update_tracks(sm, rr, self)
    else:
      ar_pts = {pt.trackId: [pt.dRel, pt.yRel, pt.vRel] for pt in rr.points}

      # *** remove missing points from meta data ***
      for ids in list(self.tracks.keys()):
        if ids not in ar_pts:
          self.tracks.pop(ids, None)

      # *** compute the tracks ***
      for ids in ar_pts:
        rpt = ar_pts[ids]

        # align v_ego by a fixed time to align it with the radar measurement
        v_lead = rpt[2] + self.v_ego_hist[0]

        # create the track if it doesn't exist or it's a new track
        if ids not in self.tracks:
          self.tracks[ids] = Track(ids, v_lead, self.kalman_params)
        self.tracks[ids].update(rpt[0], rpt[1], rpt[2], v_lead)

    # *** publish radarState ***
    self.radar_state_valid = sm.all_checks()
    self.radar_state = log.RadarState.new_message()
    self.radar_state.mdMonoTime = sm.logMonoTime['modelV2']
    self.radar_state.radarErrors = rr.errors

    if len(sm['modelV2'].velocity.x):
      model_v_ego = sm['modelV2'].velocity.x[0]
    else:
      model_v_ego = self.v_ego
    leads_v3 = sm['modelV2'].leadsV3
    if len(leads_v3) > 1:
      for i in range(2):
        # Asymmetric filter on lead prob to keep lead when uncertain
        lead_prob = leads_v3[i].prob
        if lead_prob > self.lead_prob_filters[i].x:
          self.lead_prob_filters[i].x = lead_prob
        else:
          self.lead_prob_filters[i].update(lead_prob)

      if self.use_voacc:
        self.radar_state.leadOne = get_lead(self.v_ego, self.ready, {}, leads_v3[0], model_v_ego,
                                          self.lead_prob_filters[0].x, low_speed_override=False)
        self.radar_state.leadTwo = get_lead(self.v_ego, self.ready, {}, leads_v3[1], model_v_ego,
                                          self.lead_prob_filters[1].x, low_speed_override=False)
      elif self.honda_bosch_a is not None:
        self.radar_state.leadOne, self.radar_state.leadTwo = self.honda_bosch_a.leads(sm, self, model_v_ego)
      else:
        self.radar_state.leadOne = get_lead(self.v_ego, self.ready, self.tracks, leads_v3[0], model_v_ego, self.lead_prob_filters[0].x, low_speed_override=True)
        self.radar_state.leadTwo = get_lead(self.v_ego, self.ready, self.tracks, leads_v3[1], model_v_ego,
                                          self.lead_prob_filters[1].x, low_speed_override=False)
    if self.adjacent_enabled:
      self._qualified_adjacent(sm, rr)

  def publish(self, pm: messaging.PubMaster):
    assert self.radar_state is not None

    radar_msg = messaging.new_message("radarState")
    radar_msg.valid = self.radar_state_valid
    radar_msg.radarState = self.radar_state
    pm.send("radarState", radar_msg)

    if not self.adjacent_enabled:
      return

    adjacent_msg = messaging.new_message('starpilotRadarState')
    adjacent_msg.valid = self.adjacent_observation.ambiguous is not None
    self.adjacent_sequence += 1
    status = adjacent_msg.starpilotRadarState.qualifiedAdjacent
    status.version = 1
    status.status = ('unknown' if self.adjacent_observation.ambiguous is None else
                     'ambiguous' if self.adjacent_observation.ambiguous else 'clear')
    status.producerSessionId = self.adjacent_session
    status.sequence = self.adjacent_sequence
    status.radarTracksMonoTime, status.modelMonoTime, status.carStateMonoTime, status.cameraEofBootTime = self.adjacent_sources
    observed = self.adjacent_observation.observed_mono_ns
    if observed is not None:
      status.observedMonoTime = observed
      status.validUntilMonoTime = self.adjacent_valid_until_ns
    for name, candidate in (('left', self.adjacent_observation.left), ('right', self.adjacent_observation.right)):
      if candidate is not None:
        wire = getattr(status, name)
        wire.present = True
        wire.trackId = candidate.track_id
        wire.distanceM = candidate.distance_m
        wire.lateralM = candidate.lateral_m
        wire.speedMps = candidate.speed_mps
    pm.send('starpilotRadarState', adjacent_msg)


# fuses camera and radar data for best lead detection
def main() -> None:
  config_realtime_process(5, Priority.CTRL_LOW)

  # wait for stats about the car to come in from controls
  cloudlog.info("radard is waiting for CarParams")
  params = Params()
  CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  cloudlog.info("radard got CarParams")

  # *** setup messaging
  sm = messaging.SubMaster(['modelV2', 'carState', 'radarTracks'], poll='modelV2')
  adjacent_enabled = feature_enabled(params, CP, 'conditional', os.environ) and 'REPLAY' not in os.environ
  pm = messaging.PubMaster(['radarState', 'starpilotRadarState'] if adjacent_enabled else ['radarState'])

  from opendbc.car.honda.bosch_a_radar import VERIFIED_BOSCH_A_CARS
  honda_bosch_a = CP.brand == "honda" and CP.carFingerprint in VERIFIED_BOSCH_A_CARS and not CP.radarUnavailable
  RD = RadarD(CP.radarDelay, adjacent_enabled=adjacent_enabled, radar_available=not CP.radarUnavailable,
              honda_bosch_a_radar=honda_bosch_a, use_voacc=voacc_selected(params, CP))
  set_core_affinity([3, 5])

  while 1:
    sm.update()

    RD.update(sm, sm['radarTracks'])
    RD.publish(pm)


if __name__ == "__main__":
  main()
