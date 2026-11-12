from opendbc.car.hyundai.ioniq5pe_long_profile import qualified as pe_long_qualified
"""Qualified longitudinal evidence and transport outside the upstream control loop."""
from dataclasses import replace
import os
import time

from opendbc.car.interfaces import ACCEL_MAX

from openpilot.starpilot.longitudinal.extension import LongitudinalContext
from opendbc.car.hyundai.blended_stopping import eligible as blended_longitudinal_eligible

from openpilot.starpilot.feature_runtime import enabled as feature_enabled
from openpilot.starpilot.conditional_mode.traffic_launch import TrafficLaunchState
from openpilot.starpilot.longitudinal.toyota_output_policy import (CLOCK_PAIR_MAX_SKEW_NS, COROLLA, SIENNA_4G, clock_pair_ns,
                                                                 development_enabled as toyota_development_enabled,
                                                                 production_enabled as toyota_production_enabled, leads_from_radar)
from openpilot.starpilot.longitudinal.ioniq6_start import StartEvidence, eligible as ioniq6_start_eligible
from openpilot.starpilot.longitudinal.force_stop_braking import eligible as distance_braking_eligible
from openpilot.starpilot.longitudinal.lead_takeoff_preferences import TakeoffPreferences
from openpilot.starpilot.longitudinal.takeoffs import eligible as takeoff_eligible
from opendbc.car.gm.conventional_pedal import policy_for as conventional_pedal_policy_for
from opendbc.car.gm.ordinary_cc import policy_for as ordinary_cc_policy_for
from opendbc.car.gm.hybrid_cc import policy_for as hybrid_policy_for
from opendbc.car.gm.suburban import SuburbanStopEvidence
from opendbc.car.gm.cc_longitudinal import VoltCcEvidence, policy_for as volt_cc_policy_for
from opendbc.car.gm.longitudinal import (
  PedalStartEvidence, policy_for as gm_pedal_policy_for,
  EuvLongitudinalEvidence, VoltStopEvidence, AscmStopEvidence,
)
from openpilot.starpilot.longitudinal.vehicle_policy import installed_policy_for
from openpilot.starpilot.longitudinal.profile_runtime import ProfileHost, active_personality_id
from opendbc.car.hyundai.ev9_longitudinal import qualified as ev9_long_qualified


class ResumeFreshness:
  """Current drive and producer/receipt clock qualification for resume evidence."""
  def __init__(self):
    self.offset_ns = None
    self.floor_ns = 0

  def current(self, sm):
    pair = clock_pair_ns()
    if pair is None:
      self.offset_ns = None
      return False
    now_ns, boot_ns = pair
    offset = boot_ns - now_ns
    if self.offset_ns is None or abs(offset - self.offset_ns) > CLOCK_PAIR_MAX_SKEW_NS:
      self.offset_ns = offset
      self.floor_ns = now_ns
      return False
    if not all(sm.seen[name] and sm.alive[name] and sm.valid[name] for name in ('deviceState',)):
      return False
    drive_id = int(sm['deviceState'].startedMonoTime)
    device_ns = int(sm.logMonoTime['deviceState'])
    if (not sm['deviceState'].started or not 0 < drive_id < device_ns <= now_ns or
        device_ns <= self.floor_ns or now_ns - device_ns > 2_000_000_000 or
        not 0 <= now_ns - int(sm.recv_time['deviceState'] * 1e9) <= 2_000_000_000):
      return False
    for name in ('carState', 'longitudinalPlan'):
      source_ns, receipt_ns = int(sm.logMonoTime[name]), int(sm.recv_time[name] * 1e9)
      if (not sm.all_checks([name]) or not max(self.floor_ns, drive_id) < source_ns <= receipt_ns <= now_ns or
          now_ns - source_ns > 150_000_000 or now_ns - receipt_ns > 150_000_000):
        return False
    return True


class LongitudinalInputs:
  def __init__(self, cp, params, messages):
    self.CP = cp
    self.takeoff_enabled = takeoff_eligible(cp)
    self.takeoff_preferences = TakeoffPreferences(params) if self.takeoff_enabled else None
    self.takeoff_boot_offset_ns = None
    self.takeoff_source_floor_ns = 0
    self.faster_takeoffs = False
    self.ev9_long_enabled = ev9_long_qualified(cp) or pe_long_qualified(cp)
    if self.ev9_long_enabled:
      self.ev9_boot_offset_ns = None
      self.ev9_source_floor_ns = 0
      self.ev9_traffic = TrafficLaunchState(params)
      self.ev9_profile = ProfileHost(params)
    self.blended_longitudinal_enabled = blended_longitudinal_eligible(cp)
    self.params = params
    self.messages = messages
    self.toyota_sienna_replay = toyota_development_enabled(self.CP) and str(self.CP.carFingerprint) == SIENNA_4G
    toyota_production = toyota_production_enabled(self.CP)
    self.toyota_corolla_stop = toyota_production and str(self.CP.carFingerprint) == COROLLA
    self.toyota_sienna_production = toyota_production and str(self.CP.carFingerprint) == SIENNA_4G
    self.ioniq6_start_enabled = ioniq6_start_eligible(self.CP)
    self.force_stop_distance_braking = distance_braking_eligible(self.CP)
    gm_policy = gm_pedal_policy_for(self.CP)
    self.gm_start_enabled = gm_policy is not None and gm_policy.friction_variant
    installed_policy = installed_policy_for(self.CP)
    stop_policy = installed_policy.stop_policy() if hasattr(installed_policy, 'stop_policy') else None
    evidence_type = getattr(stop_policy, 'evidence_type', None)
    self.gm_volt_enabled = evidence_type is VoltStopEvidence
    self.resume_freshness = ResumeFreshness()
    self.gm_cc_enabled = (conventional_pedal_policy_for(self.CP) or ordinary_cc_policy_for(self.CP) or
                          volt_cc_policy_for(self.CP) or hybrid_policy_for(self.CP)) is not None
    self.gm_cc_stop_enabled = evidence_type is VoltCcEvidence
    if self.gm_cc_enabled or self.gm_cc_stop_enabled:
      self.gm_cc_evidence = None
      self.gm_cc_boot_offset_ns = None
      self.gm_cc_source_floor_ns = 0
    self.gm_suburban_enabled = evidence_type is SuburbanStopEvidence
    if self.gm_suburban_enabled:
      self.gm_suburban_boot_offset_ns = None
      self.gm_suburban_source_floor_ns = 0
    self.gm_ascm_enabled = evidence_type is AscmStopEvidence
    if self.gm_ascm_enabled:
      self.gm_ascm_boot_offset_ns = None
      self.gm_ascm_source_floor_ns = 0
    self.gm_euv_enabled = evidence_type is EuvLongitudinalEvidence
    if self.gm_euv_enabled:
      self.gm_euv_boot_offset_ns = None
      self.gm_euv_source_floor_ns = 0
    if self.gm_volt_enabled:
      self.gm_volt_boot_offset_ns = None
      self.gm_volt_source_floor_ns = 0
    if self.gm_start_enabled:
      self.gm_boot_offset_ns = None
      self.gm_source_floor_ns = 0
      self.gm_traffic_state = TrafficLaunchState(self.params, cp=self.CP)
      self.gm_profile_host = ProfileHost(self.params) if feature_enabled(self.params, self.CP, 'profile', os.environ) else None
    if self.toyota_sienna_replay or self.toyota_sienna_production or self.toyota_corolla_stop:
      self.toyota_boot_offset_ns: int | None = None
      self.toyota_source_floor_ns = 0
    if self.ioniq6_start_enabled:
      self.ioniq6_boot_offset_ns: int | None = None
      self.ioniq6_source_floor_ns = 0

  @property
  def sm(self):
    return self.messages()

  @property
  def optional_services(self):
    if self.force_stop_distance_braking:
      return ['radarState', 'deviceState', 'starpilotLongitudinalPlan'] + (['slcState'] if self.ev9_long_enabled else [])
    if (self.toyota_sienna_replay or self.toyota_sienna_production or self.toyota_corolla_stop or
        self.ioniq6_start_enabled or self.gm_start_enabled or self.gm_volt_enabled or self.gm_euv_enabled or
        self.gm_cc_enabled or self.gm_cc_stop_enabled or self.gm_ascm_enabled or self.gm_suburban_enabled):
      return ['radarState', 'deviceState'] + (['slcState'] if self.gm_start_enabled or self.ev9_long_enabled else [])
    if self.ev9_long_enabled:
      return ['radarState', 'deviceState', 'slcState']
    return ['radarState', 'deviceState'] if self.takeoff_enabled else []

  @property
  def publish_state(self):
    return self.ioniq6_start_enabled or self.gm_start_enabled or self.faster_takeoffs

  def qualify_active(self, active):
    if not self.gm_cc_enabled:
      return active
    self.gm_cc_evidence = self._gm_cc_evidence() if active else None
    return bool(active and isinstance(self.gm_cc_evidence, VoltCcEvidence))

  def context(self, active):
    context = self._vehicle_context(active)
    if active and self.force_stop_distance_braking:
      from openpilot.starpilot.longitudinal.force_stop_braking import current_braking
      context = replace(context, force_stop_braking=current_braking(self.sm, time.monotonic_ns()))
    self.faster_takeoffs = bool(self.takeoff_preferences is not None and
                               self.takeoff_preferences.sample(time.monotonic_ns()))
    if not self.faster_takeoffs:
      if context.profile_max_accel is not None and context.profile_max_accel > ACCEL_MAX:
        context = replace(context, profile_max_accel=ACCEL_MAX)
      return context
    evidence = self._start_evidence('takeoff_boot_offset_ns', 'takeoff_source_floor_ns') if active else None
    return replace(context, faster_takeoffs=True, start_evidence=evidence)

  def _vehicle_context(self, active):
    if self.ev9_long_enabled:
      return self._ev9_context(active)
    if self.toyota_sienna_replay or self.toyota_sienna_production or self.toyota_corolla_stop:
      return LongitudinalContext(leads=self._toyota_leads() if active else None)
    cc_evidence = (self.gm_cc_evidence if self.gm_cc_enabled else self._gm_cc_evidence()
                   if self.gm_cc_stop_enabled and active else None)
    volt_observation = self._gm_volt_observation() if self.gm_volt_enabled and active else None
    return LongitudinalContext(
      experimental_mode=bool(self.sm['selfdriveState'].experimentalMode) if self.sm.all_checks(['selfdriveState']) else None,
      start_evidence=self._ioniq6_start_evidence() if self.ioniq6_start_enabled else None,
      gm_start_evidence=self._gm_start_evidence() if self.gm_start_enabled and active else None,
      has_lead=(bool(self.sm['longitudinalPlan'].hasLead)
                if self.blended_longitudinal_enabled and self.sm.all_checks(['longitudinalPlan'])
                else cc_evidence.has_lead if cc_evidence is not None else volt_observation[0]
                if volt_observation is not None else None),
      vehicle_stop_evidence=cc_evidence if cc_evidence is not None else self._gm_suburban_evidence()
        if self.gm_suburban_enabled and active else self._gm_ascm_evidence()
        if self.gm_ascm_enabled and active else self._gm_euv_evidence()
        if self.gm_euv_enabled and active else self._gm_volt_stop_from_observation(volt_observation),
    )


  def resume_sources_current(self):
    return self.resume_freshness.current(self.sm)

  def _ev9_context(self, active):
    unavailable = LongitudinalContext()
    if not active:
      return unavailable
    pair = clock_pair_ns()
    if pair is None:
      self.ev9_boot_offset_ns = None
      return unavailable
    now_ns, boot_ns = pair
    offset = boot_ns - now_ns
    if self.ev9_boot_offset_ns is None or abs(offset - self.ev9_boot_offset_ns) > CLOCK_PAIR_MAX_SKEW_NS:
      self.ev9_boot_offset_ns = offset
      self.ev9_source_floor_ns = now_ns
      return unavailable
    sm = self.sm
    # Optional subscriptions are ignored by SubMaster.all_checks even when
    # explicitly named. Require producer health directly for drive ownership.
    if not (sm.seen['deviceState'] and sm.alive['deviceState'] and sm.valid['deviceState']):
      return unavailable
    drive_id = int(sm['deviceState'].startedMonoTime)
    device_ns = int(sm.logMonoTime['deviceState'])
    if (not sm['deviceState'].started or not 0 < drive_id < device_ns <= now_ns or
        device_ns <= self.ev9_source_floor_ns or now_ns - device_ns > 2_000_000_000 or
        not 0 <= now_ns - int(sm.recv_time['deviceState'] * 1e9) <= 2_000_000_000):
      return unavailable
    sm = self.sm
    for name in ('carState', 'longitudinalPlan', 'selfdriveState'):
      source_ns, receipt_ns = int(sm.logMonoTime[name]), int(sm.recv_time[name] * 1e9)
      if (not sm.all_checks([name]) or not max(self.ev9_source_floor_ns, drive_id) < source_ns <= receipt_ns <= now_ns or
          now_ns - source_ns > 150_000_000 or now_ns - receipt_ns > 150_000_000):
        return unavailable
    # EV9 controller-wheel mode actions are not currently admitted. Never turn
    # a future controller-owned Traffic frame into an inferred off state.
    if sm.seen['slcState'] and bool(sm['slcState'].trafficMode.controllerSource):
      return unavailable
    traffic = self.ev9_traffic.sample(sm, now_ns=now_ns, boot_ns=now_ns + self.ev9_boot_offset_ns, drive_id=drive_id)
    if traffic is None:
      return unavailable
    profile = self.ev9_profile.sample(now_ns, sm['selfdriveState'].personality, sm['carState'].vEgo, self.CP, traffic_mode=traffic)
    selected = self.ev9_profile.sample_selected(now_ns, sm['selfdriveState'].personality, sm['carState'].vEgo,
                                                self.CP, traffic_mode=traffic, legacy=profile)
    if self.ev9_profile.selected_success_ns < 0 or (profile is None and not self.ev9_profile.disabled):
      return unavailable
    ceiling = selected.acceleration_max if selected is not None else None
    if ceiling is None:
      ceiling = profile.acceleration_max if profile is not None and profile.acceleration_max is not None else 0.
    custom = bool(profile is not None and profile.custom_acceleration)
    document = self.ev9_profile.selected_document
    if document is not None:
      identity = active_personality_id(traffic, sm['selfdriveState'].personality)
      config = document['profiles'][identity]['acceleration']
      if not config.get('legacyActivation', False):
        custom = config['preset'] == 'custom'
    return LongitudinalContext(experimental_mode=bool(sm['selfdriveState'].experimentalMode),
                               has_lead=bool(sm['longitudinalPlan'].hasLead), traffic_mode=traffic,
                               custom_acceleration=custom, profile_max_accel=ceiling)

  def lead_visible(self, default):
    if self.gm_cc_enabled:
      return self.gm_cc_evidence.has_lead if self.gm_cc_evidence is not None else False
    return default

  def _toyota_leads(self):
    if not (self.toyota_sienna_replay or self.toyota_sienna_production or self.toyota_corolla_stop):
      return None
    result = self._qualified_radar_leads('toyota_boot_offset_ns', 'toyota_source_floor_ns')
    return result[0] if result is not None else None

  def _qualified_radar_leads(self, offset_name: str, floor_name: str):
    # Replay has no producer-side receipt timestamp in radarState. Never infer one
    # from a repeatedly read model frame; the native target remains available.
    if os.getenv('REPLAY') == '1':
      return None
    pair = clock_pair_ns()
    if pair is None:
      setattr(self, offset_name, None)
      return None
    now_ns, boot_ns = pair
    offset = boot_ns - now_ns
    previous_offset = getattr(self, offset_name)
    if previous_offset is None or abs(offset - previous_offset) > CLOCK_PAIR_MAX_SKEW_NS:
      # First sample or suspend/resume: cached messages cannot reauthorize the
      # optional lead policy until fresh producers advance in this epoch.
      setattr(self, offset_name, offset)
      setattr(self, floor_name, now_ns)
      return None
    sm = self.sm
    if not all(sm.seen[name] and sm.alive[name] and sm.valid[name] for name in ('radarState', 'deviceState')):
      return None
    if any(int(sm.logMonoTime[name]) <= getattr(self, floor_name) for name in ('radarState', 'deviceState', 'carState')):
      return None
    device_ns = int(sm.logMonoTime['deviceState'])
    drive_id = int(sm['deviceState'].startedMonoTime)
    if (not sm['deviceState'].started or not 0 < drive_id < device_ns <= now_ns or
        now_ns - device_ns > 2_000_000_000 or
        not 0 <= now_ns - int(sm.recv_time['deviceState'] * 1e9) <= 2_000_000_000):
      return None
    errors = sm['radarState'].radarErrors
    if (not drive_id < int(sm.logMonoTime['carState']) <= now_ns or
        not sm.all_checks(['carState']) or errors.canError or errors.radarFault or
        errors.wrongConfig or errors.radarUnavailableTemporary):
      return None
    leads = leads_from_radar(sm['radarState'], message_ns=int(sm.logMonoTime['radarState']),
                             receipt_ns=int(sm.recv_time['radarState'] * 1e9), now_ns=now_ns,
                             drive_id=drive_id, valid=bool(sm.valid['radarState']))
    return (leads, now_ns, drive_id) if leads is not None else None

  def _ioniq6_start_evidence(self) -> StartEvidence:
    unavailable = StartEvidence(False, False, None, None, None)
    if not self.ioniq6_start_enabled:
      return unavailable
    return self._start_evidence('ioniq6_boot_offset_ns', 'ioniq6_source_floor_ns')

  def _start_evidence(self, offset_name, floor_name):
    unavailable = StartEvidence(False, False, None, None, None)
    result = self._qualified_radar_leads(offset_name, floor_name)
    if result is None:
      return unavailable
    leads, now_ns, drive_id = result
    sm = self.sm
    if not all(sm.seen[name] and sm.alive[name] and sm.valid[name] for name in ('carState', 'longitudinalPlan')):
      return unavailable
    if not sm.all_checks(['carState', 'longitudinalPlan']):
      return unavailable
    floor_ns = getattr(self, floor_name)
    for name in ('carState', 'longitudinalPlan'):
      source_ns = int(sm.logMonoTime[name])
      receipt_ns = int(sm.recv_time[name] * 1e9)
      if not max(floor_ns, drive_id) < source_ns <= receipt_ns <= now_ns or now_ns - source_ns > 150_000_000 or now_ns - receipt_ns > 150_000_000:
        return unavailable
    return StartEvidence(True, True, not any(lead.present for lead in leads), drive_id, now_ns)

  def _gm_volt_observation(self) -> tuple[bool, bool, int, int] | None:
    result = self._qualified_radar_leads('gm_volt_boot_offset_ns', 'gm_volt_source_floor_ns')
    if result is None:
      return None
    leads, now_ns, drive_id = result
    sm = self.sm
    if not sm['carState'].canValid or sm['carState'].canTimeout:
      return None
    for name in ('carState', 'longitudinalPlan'):
      source_ns, receipt_ns = int(sm.logMonoTime[name]), int(sm.recv_time[name] * 1e9)
      if (not sm.all_checks([name]) or not max(self.gm_volt_source_floor_ns, drive_id) < source_ns <= receipt_ns <= now_ns or
          now_ns - source_ns > 150_000_000 or now_ns - receipt_ns > 150_000_000):
        return None
    return any(lead.present for lead in leads), leads[0].present, now_ns, drive_id

  def _gm_volt_stop_from_observation(self, observation: tuple[bool, bool, int, int] | None) -> VoltStopEvidence | None:
    if observation is None:
      return None
    # LongitudinalPlan.hasLead describes leadOne, not the secondary radar lead.
    _, has_lead, now_ns, drive_id = observation
    if bool(self.sm['longitudinalPlan'].hasLead) != has_lead:
      return None
    return VoltStopEvidence(drive_id, now_ns, has_lead)

  def _gm_euv_evidence(self):
    result = self._qualified_radar_leads('gm_euv_boot_offset_ns', 'gm_euv_source_floor_ns')
    if result is None:
      return None
    leads, now_ns, drive_id = result
    sm = self.sm
    if not sm['carState'].canValid or sm['carState'].canTimeout:
      return None
    for name in ('carState', 'longitudinalPlan'):
      source_ns, receipt_ns = int(sm.logMonoTime[name]), int(sm.recv_time[name] * 1e9)
      if (not sm.all_checks([name]) or not max(self.gm_euv_source_floor_ns, drive_id) < source_ns <= receipt_ns <= now_ns or
          now_ns - source_ns > 150_000_000 or now_ns - receipt_ns > 150_000_000):
        return None
    has_lead = leads[0].present
    if bool(sm['longitudinalPlan'].hasLead) != has_lead:
      return None
    return EuvLongitudinalEvidence(drive_id, now_ns, has_lead)

  def _gm_ascm_evidence(self):
    return self._gm_ordinary_stop_evidence('gm_ascm', AscmStopEvidence)

  def _gm_suburban_evidence(self):
    return self._gm_ordinary_stop_evidence('gm_suburban', SuburbanStopEvidence)

  def _gm_ordinary_stop_evidence(self, prefix, evidence_type):
    floor_key = prefix + '_source_floor_ns'
    result = self._qualified_radar_leads(prefix + '_boot_offset_ns', floor_key)
    if result is None:
      return None
    leads, now_ns, drive_id = result
    sm = self.sm
    if not sm['carState'].canValid or sm['carState'].canTimeout:
      return None
    for name in ('carState', 'longitudinalPlan'):
      source_ns, receipt_ns = int(sm.logMonoTime[name]), int(sm.recv_time[name] * 1e9)
      if (not sm.all_checks([name]) or not max(getattr(self, floor_key), drive_id) < source_ns <= receipt_ns <= now_ns or
          now_ns - source_ns > 150_000_000 or now_ns - receipt_ns > 150_000_000):
        return None
    has_lead = leads[0].present
    if bool(sm['longitudinalPlan'].hasLead) != has_lead:
      return None
    return evidence_type(drive_id, now_ns, has_lead)

  def _gm_cc_evidence(self):
    result = self._qualified_radar_leads('gm_cc_boot_offset_ns', 'gm_cc_source_floor_ns')
    if result is None:
      return None
    leads, now_ns, drive_id = result
    sm = self.sm
    if not sm['carState'].canValid or sm['carState'].canTimeout:
      return None
    for name in ('carState', 'longitudinalPlan'):
      source_ns, receipt_ns = int(sm.logMonoTime[name]), int(sm.recv_time[name] * 1e9)
      if (not sm.all_checks([name]) or not max(self.gm_cc_source_floor_ns, drive_id) < source_ns <= receipt_ns <= now_ns or
          now_ns - source_ns > 150_000_000 or now_ns - receipt_ns > 150_000_000):
        return None
    has_lead = leads[0].present
    if bool(sm['longitudinalPlan'].hasLead) != has_lead:
      return None
    return VoltCcEvidence(drive_id, now_ns, has_lead)

  def _gm_start_evidence(self):
    result = self._qualified_radar_leads('gm_boot_offset_ns', 'gm_source_floor_ns')
    if result is None:
      return None
    leads, now_ns, drive_id = result
    sm = self.sm
    for name in ('carState', 'longitudinalPlan', 'selfdriveState'):
      source_ns, receipt_ns = int(sm.logMonoTime[name]), int(sm.recv_time[name] * 1e9)
      if (not sm.all_checks([name]) or not max(self.gm_source_floor_ns, drive_id) < source_ns <= receipt_ns <= now_ns or
          now_ns - source_ns > 150_000_000 or now_ns - receipt_ns > 150_000_000):
        return None
    profile = (self.gm_profile_host.sample(now_ns, sm['selfdriveState'].personality, sm['carState'].vEgo, self.CP)
               if self.gm_profile_host is not None else None)
    traffic_state = getattr(self, 'gm_traffic_state', None)
    traffic_mode = (traffic_state.sample(sm, now_ns=now_ns, boot_ns=now_ns + self.gm_boot_offset_ns, drive_id=drive_id)
                    if traffic_state is not None else False)
    if traffic_mode is None:
      return None
    ceiling = profile.acceleration_max if profile is not None else None
    if self.gm_profile_host is not None and profile is None and not self.gm_profile_host.disabled:
      return None
    return PedalStartEvidence(drive_id, now_ns, any(lead.present for lead in leads), traffic_mode=traffic_mode,
                              custom_acceleration=profile.custom_acceleration if profile is not None else False, profile_max_accel=ceiling)

