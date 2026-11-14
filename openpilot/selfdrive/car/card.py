from openpilot.starpilot.gps.publisher import CarGpsPublisher
#!/usr/bin/env python3
import os
import time
import threading
import uuid
from typing import cast

import openpilot.cereal.messaging as messaging

from openpilot.cereal import log
from opendbc.car.structs import car

from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, Priority, Ratekeeper
from openpilot.common.swaglog import cloudlog, ForwardingHandler

from opendbc.car import DT_CTRL, structs
from opendbc.car.can_definitions import CanData, CanRecvCallable, CanSendCallable
from opendbc.car.carlog import carlog
from opendbc.car.fw_versions import ObdCallback
from opendbc.car.car_helpers import get_car, interfaces
from opendbc.car.gm.distance_button import GMDistanceButtons
from opendbc.car.gm.interface import CarInterface as GMInterface
from opendbc.car.gm.profiles import profiles_supported as gm_profiles_supported
from opendbc.car.hyundai.ioniq6_handoff import (IONIQ6_LONG_PREARM_ENABLED, HandoffOutcome,
                                                TimestampedCanPacket, build_ioniq6_hda2_long_candidate,
                                                confirm_ioniq6_prepared_takeover, restore_ioniq6_adas,
                                                finalize_ioniq6_prepublication, Ioniq6StartupKeepalive,
                                                inspect_ioniq6_long_sources)
from opendbc.car.hyundai.hyundaicanfd import CanBus as HyundaiCanBus
from opendbc.car.hyundai.ioniq6_media import Ioniq6MediaButtons
from opendbc.car.hyundai.values import CAR as HYUNDAI_CAR
from opendbc.car.interfaces import CarInterfaceBase, RadarInterfaceBase
from openpilot.selfdrive.pandad import can_capnp_to_list, can_list_to_can_capnp
from openpilot.selfdrive.car.cruise import SlcWheelContext, VCruiseHelper
from openpilot.starpilot.speed_limits import physical_actions as slc_physical
from openpilot.starpilot.aol.intent import AolProcessFaultContext, independent_axis_requested, read_settings
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback, cp_identity
from opendbc.car.honda.stock_aol import (
  qualified as qualified_honda_stock_aol, native_observation as honda_native_observation,
  temporary_restriction as honda_temporary_restriction,
)
from opendbc.car.volkswagen.stock_aol import (native_observation as volkswagen_native_observation,
  safety_config as volkswagen_aol_safety_config)
from openpilot.starpilot.car.volkswagen.aol import (qualified as qualified_volkswagen_aol,
  temporary_restriction as volkswagen_temporary_restriction)
from openpilot.starpilot.car.rivian.aol import retained_brake_lateral_intent as rivian_brake_intent_retained
from opendbc.car.rivian.stock_aol import (qualified as qualified_rivian_aol,
                                         native_observation as rivian_native_observation,
                                         temporary_restriction as rivian_temporary_restriction)
from opendbc.car.mazda.stock_aol import (qualified as qualified_mazda_aol,
                                        native_observation as mazda_native_observation,
                                        temporary_restriction as mazda_temporary_restriction)
from opendbc.car.tesla.preap.aol import (
  qualified as qualified_tesla_preap, native_observation as preap_native_observation,
  temporary_restriction as preap_temporary_restriction,
)
from opendbc.car.ford.aol import (
  qualified as qualified_ford_aol, native_observation as ford_native_observation,
  temporary_restriction as ford_temporary_restriction,
)
from opendbc.car.hyundai.canfd_angle_aol import native_observation, temporary_restriction, qualified as qualified_angle_aol
from opendbc.car.hyundai.canfd_owner import config_index as hyundai_canfd_config_index
from opendbc.car.hyundai.captured_adrv_startup import CapturedADRVStartup
from openpilot.starpilot.car.nissan.aol import (
  qualified as qualified_nissan_aol, native_observation as nissan_native_observation,
  temporary_restriction as nissan_temporary_restriction,
)
from openpilot.starpilot.car.hyundai.aol import aol_safety_config as hyundai_aol_safety_config, pending_stock_angle_arm
from openpilot.starpilot.car.hyundai.aol import retained_shared_long_cancel_intent as hkg_cancel_intent_retained
from openpilot.starpilot.car.ford.aol import (brake_pause_active as ford_brake_pause_active,
                                               awaiting_initial_request as ford_awaiting_initial_request)
from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS, current_axis, current_native
from openpilot.starpilot.aol.vehicle import create_intent as create_aol_intent, native_latch_rejected, policy_for as aol_policy_for
from openpilot.starpilot.aol.wire import IntentState, encode_intent
from openpilot.starpilot.conditional_mode.manual import Button, ButtonTracker, IoniqMediaMapCache, WheelMapCache, Press, ioniq6_media_eligible
from openpilot.starpilot.longitudinal.ioniq6_start import eligible as ioniq6_long_eligible
from opendbc.car.gm.aol import qualified_gm
from openpilot.starpilot.aol.vehicle import native_bootstrap_supported
from opendbc.car.gm.values import CAR as GM_CAR, GMFlags, is_volt_ascm_longitudinal, is_volt_sdgm_profile, is_bolt_present_no_acc_pedal_profile
from opendbc.car.gm.lateral import lane_centering_supported as gm_lane_centering_supported
from opendbc.car.gm.ordinary_cc import control_transport_required as gm_cc_transport_required
from openpilot.starpilot.longitudinal.toyota_output_policy import CLOCK_PAIR_MAX_SKEW_NS, clock_pair_ns
from openpilot.starpilot.longitudinal.cruise_intervals import read_cruise_intervals
from openpilot.starpilot.conditional_mode.card_input import conditional_manual_candidate, conditional_traffic_candidate
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner
from openpilot.starpilot.conditional_mode.card_settings import CardSettingsWorker
from openpilot.starpilot.conditional_mode.status import settings_fingerprint
from openpilot.starpilot.feature_runtime import (requested as feature_requested, enabled as feature_enabled,
                                               slc_runtime_settings, slc_transport_capable)
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.vehicle_selection import startup_candidate
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
from openpilot.starpilot.controller_extensions import configure_controller, observe_controller_can, order_controller_can
from openpilot.starpilot.vehicle_startup import VehicleStartupOwner
from openpilot.starpilot.controllers.wheel_actions import LIFETIME_NS as WHEEL_ACTION_MAX_AGE_NS, WheelPublisher, key_for
from openpilot.starpilot.controllers.cruise_action import CruiseActionConsumer, eligible as cruise_action_eligible
from openpilot.starpilot.schema_cache import get_cache, prewarm_cache_contracts, put_cache

REPLAY = "REPLAY" in os.environ

EventName = log.OnroadEvent.EventName
DISTANCE_GESTURE_KEYS = {Press.SHORT: ('DistanceButtonControl', 0),
                         Press.LONG: ('LongDistanceButtonControl', 1),
                         Press.VERY_LONG: ('VeryLongDistanceButtonControl', 2)}

# forward
carlog.addHandler(ForwardingHandler(cloudlog))


def obd_callback(params: Params) -> ObdCallback:
  def set_obd_multiplexing(obd_multiplexing: bool):
    if params.get_bool("ObdMultiplexingEnabled") != obd_multiplexing:
      cloudlog.warning(f"Setting OBD multiplexing to {obd_multiplexing}")
      params.remove("ObdMultiplexingChanged")
      params.put_bool("ObdMultiplexingEnabled", obd_multiplexing, block=True)
      params.get_bool("ObdMultiplexingChanged", block=True)
      cloudlog.warning("OBD multiplexing set successfully")
  return set_obd_multiplexing


def can_comm_callbacks(logcan: messaging.SubSocket, sendcan: messaging.PubSocket) -> tuple[CanRecvCallable, CanSendCallable]:
  def can_recv(wait_for_one: bool = False) -> list[list[CanData]]:
    """
    wait_for_one: wait the normal logcan socket timeout for a CAN packet, may return empty list if nothing comes

    Returns: CAN packets comprised of CanData objects for easy access
    """
    ret = []
    for can in messaging.drain_sock(logcan, wait_for_one=wait_for_one):
      ret.append(TimestampedCanPacket([CanData(msg.address, msg.dat, msg.src) for msg in can.can], int(can.logMonoTime)))
    return ret

  def can_send(msgs: list[CanData]) -> None:
    sendcan.send(can_list_to_can_capnp(msgs, msgtype='sendcan'))

  return can_recv, can_send


def curve_press_receipt(CS: car.CarState, helper: VCruiseHelper, sm, *, curve_replay: bool,
                        slc_replay: bool, host_long_active: bool, observed_ns: int):
  """A confirmation or native standstill RES must not also override Curve control."""
  accelerate = frozenset((car.CarState.ButtonEvent.Type.accelCruise,
                          car.CarState.ButtonEvent.Type.resumeCruise))
  pressed = {b.type.raw for b in CS.buttonEvents if b.pressed and b.type.raw in accelerate}
  if (not curve_replay or not host_long_active or not CS.canValid or CS.canTimeout or not CS.cruiseState.available or
      CS.standstill or CS.cruiseState.standstill or not pressed):
    return None
  if (helper.slc_consumed_button is not None or bool(pressed & helper.slc_suppressed_buttons) or
      slc_replay and bool(sm['slcState'].hasPending)):
    return None
  return ('curveAccelPress', None, float(helper.v_cruise_kph_last / 3.6),
          float(helper.v_cruise_kph / 3.6), observed_ns)


def alpha_long_requested(params, *, is_release: bool) -> bool:
  """Retain saved intent on release branches without granting Alpha authority."""
  return not is_release and params.get_bool("AlphaLongitudinalEnabled")


class Car:
  CI: CarInterfaceBase
  RI: RadarInterfaceBase
  CP: car.CarParams
  controller_cruise_sock = None
  aol_process_fault_context = None
  curve_replay = False
  car_params_published = False

  def __init__(self, CI=None, RI: RadarInterfaceBase | None = None, startup_owner=None) -> None:
    prewarm_cache_contracts(("CarParamsCache", "CarParamsPersistent", "CarParamsPrevRoute"))
    self.can_sock = messaging.sub_sock('can', timeout=20)
    self.params = Params()
    self.slc_replay = os.getenv('SLC_REPLAY_RUNTIME') == '1' or feature_requested(self.params, 'slc')
    self.curve_replay = os.getenv('CURVE_REPLAY_RUNTIME') == '1' or feature_requested(self.params, 'curve')
    self.aol_replay = os.getenv('AOL_REPLAY_RUNTIME') == '1' or feature_requested(self.params, 'aol')
    self.conditional_replay = os.getenv('CONDITIONAL_MODE_REPLAY_RUNTIME') == '1' or feature_requested(self.params, 'conditional')
    subscribed_services = ['pandaStates', 'carControl', 'onroadEvents', 'deviceState', 'autoSteeringLearning']
    if self.params.get_bool('SubaruRedneckCruise') or self.params.get_bool('SubaruSNG'):
      subscribed_services.extend(('longitudinalPlan', 'selfdriveState'))
    if self.aol_replay:
      subscribed_services.extend(service for service in ('aolSafetyWire', 'managerState', 'aolAxisState', 'selfdriveState')
                                 if service not in subscribed_services)
    if self.slc_replay:
      subscribed_services.append('slcState')
    self.sm = messaging.SubMaster(subscribed_services, ignore_alive=['autoSteeringLearning'],
                                  ignore_valid=['autoSteeringLearning'], ignore_avg_freq=['autoSteeringLearning'])
    self.slc_cruise_event_id = 0
    self.slc_producer_session = uuid.uuid4().hex
    self.slc_receipts: list[tuple[str, object, float, float, int]] = []
    self.manual_receipt = None
    self.manual_event_sequence = 0
    self.traffic_receipt = None
    self.traffic_event_sequence = 0
    self.switchback_receipt = None
    self.switchback_event_sequence = 0
    publish_services = ['sendcan', 'carState', 'carParams', 'carOutput', 'radarTracks', 'starpilotCarState']
    self.car_gps_publisher = CarGpsPublisher()
    # One existing event channel also carries source-qualified Switchback gestures.
    publish_services.append('slcCruiseEvent')
    self.wheel_publisher = WheelPublisher(self.params)
    self.wheel_commands = ()
    if self.slc_replay:
      publish_services.append('slcDashboardObservation')
    if self.aol_replay:
      publish_services.append('aolIntentWire')
    self.pm = messaging.PubMaster(publish_services)

    self.can_rcv_cum_timeout_counter = 0

    self.CC_prev = car.CarControl.new_message()
    self.CS_prev = car.CarState.new_message()
    self.initialized_prev = False
    self.ci_initialized = False

    self.last_actuators_output = structs.CarControl.Actuators()

    self.manual_button_tracker = ButtonTracker() if self.conditional_replay else None
    self.traffic_button_tracker = ButtonTracker() if self.conditional_replay else None
    self.ioniq_media_map = IoniqMediaMapCache() if self.conditional_replay else None
    self.manual_settings_owner = ConditionalSettingsOwner(self.params) if self.conditional_replay else None
    self.ioniq6_long_prearmed = False
    self.ioniq6_long_pending = False
    self.ioniq6_long_selected = False
    self.ioniq6_last_preflight_at = 0.0
    self.ioniq6_panda_armed = False
    self.ioniq6_host_warmed = False
    self.ioniq6_handoff_source_floor_ns = 0
    self.ioniq6_long_lost = False
    self.ioniq6_keepalive = None
    self.ioniq6_restore_callbacks = None
    self.ioniq6_restore_bus = None
    self.ioniq6_restore_attempted = False
    self.ioniq6_init_complete = False

    self.vehicle_startup = VehicleStartupOwner(startup_owner)
    self.can_callbacks = can_comm_callbacks(self.can_sock, self.pm.sock['sendcan'])

    is_release = self.params.get_bool("IsReleaseBranch")
    openpilot_enabled_toggle = self.params.get_bool("OpenpilotEnabledToggle")
    startup_preferences = VehicleStartupPreferences.read(self.params, enabled=openpilot_enabled_toggle)

    def initialize_aol_preference(cp):
      from openpilot.starpilot.aol.defaults import enroll_missing_preference
      enroll_missing_preference(self.params, cp)
      if not self.aol_replay and feature_requested(self.params, 'aol'):
        self.aol_replay = True
        subscribed_services.extend(('aolSafetyWire', 'managerState', 'aolAxisState', 'selfdriveState'))
        self.sm = messaging.SubMaster(subscribed_services)
        self.pm.sock['aolIntentWire'] = messaging.pub_sock('aolIntentWire')

    if CI is None:
      # wait for one pandaState and one CAN packet
      print("Waiting for CAN messages...")
      while True:
        can = messaging.recv_one_retry(self.can_sock)
        if len(can.can) > 0:
          break

      alpha_long_allowed = alpha_long_requested(self.params, is_release=is_release)

      cached_params = None
      cached_params_raw = get_cache(self.params, "CarParamsCache")
      if cached_params_raw is not None:
        with car.CarParams.from_bytes(cached_params_raw) as _cached_params:
          cached_params = _cached_params

      def pre_create_hook(cp, candidate, fingerprints, car_fw):
        initialize_aol_preference(cp)
        if candidate == HYUNDAI_CAR.HYUNDAI_IONIQ_6:
          # This decision must precede CarInterface parser/controller construction
          # and the first CarParams publication below. The exact opt-in is
          # still subject to current source and restore verification.
          eligible = alpha_long_allowed and IONIQ6_LONG_PREARM_ENABLED and not is_release and openpilot_enabled_toggle and not self.params.get_bool("IsOffroad")
          if eligible:
            self.sm.update(0)
            eligible = bool(self.sm.valid['pandaStates'] and self.sm.alive['pandaStates'] and
                            any(ps.ignitionLine or ps.ignitionCan for ps in self.sm['pandaStates']))
          long_cp = build_ioniq6_hda2_long_candidate(cp, fingerprints) if eligible else None
          if long_cp is not None:
            preflight = inspect_ioniq6_long_sources(self.can_callbacks[0], ecan_bus=HyundaiCanBus(cp).ECAN)
            cloudlog.event('ioniq6_long_preflight', reason=preflight.reason)
            if preflight.source_period is None or preflight.vehicle_ready is not False:
              # Reboot in READY retains original stock CP before CI/parsers and
              # publication. Unknown evidence cannot be treated as Ready=False.
              long_cp = None
          if long_cp is not None:
            initialize_aol_preference(long_cp)
          if long_cp is not None and self.aol_replay and independent_axis_requested(read_settings(self.params)):
            # Bit 11 is AOL only inside the exact Ioniq 6 bit-15 namespace.
            # This immutable choice precedes the ECU transaction and CP publish.
            long_cp.safetyConfigs[-1].safetyParam |= 0x0800
          if long_cp is not None and not self.startup_diagnostic_admission(cp=long_cp):
            long_cp = None
          if long_cp is not None:
            self.ioniq6_restore_callbacks = self.can_callbacks
            self.ioniq6_restore_bus = HyundaiCanBus(long_cp).ECAN
          cp, self.ioniq6_long_prearmed, self.ioniq6_handoff_result = finalize_ioniq6_prepublication(
            cp, long_cp, *self.can_callbacks, enabled=eligible, is_release=is_release)
          self.ioniq6_long_selected = self.ioniq6_long_prearmed
          if self.ioniq6_long_prearmed:
            self.ioniq6_handoff_source_floor_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
            ecan_bus = self.ioniq6_restore_bus
            self.ioniq6_keepalive = Ioniq6StartupKeepalive(
              self.can_callbacks[1], lambda: restore_ioniq6_adas(*self.can_callbacks, ecan_bus=ecan_bus),
              lambda: setattr(self, 'ioniq6_long_lost', True),
              lambda restored: setattr(self, 'ioniq6_restore_attempted', True), ecan_bus=ecan_bus)
            self.ioniq6_keepalive.start()
          else:
            self.ioniq6_restore_callbacks = None
        cp = startup_preferences.prepare(cp, fingerprints=fingerprints)
        from openpilot.starpilot.car.ford.longitudinal_preferences import prepare_release_long as prepare_ford_release_long
        cp = prepare_ford_release_long(cp, self.params, is_release=is_release, enabled=openpilot_enabled_toggle)
        return self.vehicle_startup.prepare(cp, interfaces[candidate], self.can_callbacks,
                                            requested=alpha_long_allowed and not is_release and openpilot_enabled_toggle,
                                            admission=self.startup_diagnostic_admission, defer_until_controls=True)

      self.CI = get_car(*self.can_callbacks, obd_callback(self.params), alpha_long_allowed, is_release, cached_params,
                        pre_create_hook=pre_create_hook,
                        forced_candidate=startup_candidate(self.params, developer_fingerprint=bool(os.getenv('FINGERPRINT'))))
      self.RI = interfaces[self.CI.CP.carFingerprint].RadarInterface(self.CI.CP)
      self.CP = self.CI.CP

      # continue onto next fingerprinting step in pandad
      self.params.put_bool("FirmwareQueryDone", True, block=True)
    else:
      self.CI, self.CP = CI, CI.CP
      if RI is None:
        raise ValueError('explicit car interface requires a radar interface')
      self.RI = RI

    self.volt_cc_selected = gm_cc_transport_required(self.CP)
    self.volt_cc_boot_offset_ns = None
    self.volt_cc_source_floor_ns = 0
    if self.volt_cc_selected and 'deviceState' not in subscribed_services:
      subscribed_services.append('deviceState')
      self.sm = messaging.SubMaster(subscribed_services, ignore_alive=['deviceState'], ignore_valid=['deviceState'],
                                    ignore_avg_freq=['deviceState'])

    self.CP.alternativeExperience = 0
    self.switchback_capable = ioniq6_media_eligible(self.CP)
    self.ioniq6_media = Ioniq6MediaButtons(HyundaiCanBus(self.CP).ECAN) if self.switchback_capable else None
    self.switchback_button_tracker = ButtonTracker() if self.switchback_capable else None
    self.switchback_settings_owner = ConditionalSettingsOwner(self.params) if self.switchback_capable else None
    self.gm_distance = GMDistanceButtons() if self.conditional_replay and gm_profiles_supported(self.CP) else None
    self.gm_distance_map = WheelMapCache(include_ioniq_media=False, cp=self.CP) if self.gm_distance is not None else None
    self.gm_distance_claim_tracker = ButtonTracker() if self.gm_distance is not None else None
    controller_available = self.CI.CC is not None and openpilot_enabled_toggle and not self.CP.dashcamOnly
    self.CP.passive = not controller_available or self.CP.dashcamOnly
    if self.CP.passive:
      safety_config = structs.CarParams.SafetyConfig()
      safety_config.safetyModel = structs.CarParams.SafetyModel.noOutput
      self.CP.safetyConfigs = [safety_config]

    startup_preferences.finalize(self.CP)
    from opendbc.car.gm.cancel_remap import migrate_assignments as migrate_gm_cancel_assignments
    migrate_gm_cancel_assignments(self.params, self.CP)
    startup_preferences.configure_controller(self.CI)
    configure_controller(self.CI, self.params)
    self.vehicle_startup.configure(self.CI)

    if self.CP.secOcRequired:
      # Copy user key if available
      try:
        with open("/cache/params/SecOCKey") as f:
          user_key = f.readline().strip()
          from openpilot.starpilot.car.toyota.security_key import decode_key
          if decode_key(user_key) is not None:
            self.params.put("SecOCKey", user_key, block=True)
      except Exception:
        pass

      from openpilot.starpilot.car.toyota.security_key import decode_key
      secoc_key = self.params.get("SecOCKey")
      if secoc_key is not None:
        saved_secoc_key = decode_key(secoc_key)
        if saved_secoc_key is not None:
          self.CP.secOcKeyAvailable = True
          self.CI.CS.secoc_key = saved_secoc_key
          if controller_available:
            self.CI.CC.secoc_key = saved_secoc_key
        else:
          cloudlog.warning("Saved SecOC key is invalid")

    from openpilot.starpilot.car.hyundai.button_migration import migrate_lkas_default
    migrate_lkas_default(self.CP, self.params)
    self.is_metric = self.params.get_bool("IsMetric")
    aol_policy = aol_policy_for(self.CP)
    if aol_policy.full_axis_runtime_required and not self.aol_replay:
      self.aol_replay = True
      subscribed_services.extend(('aolSafetyWire', 'managerState', 'aolAxisState', 'selfdriveState'))
      self.sm = messaging.SubMaster(subscribed_services)
      self.pm.sock['aolIntentWire'] = messaging.pub_sock('aolIntentWire')
    initialize_aol_preference(self.CP)
    self.aol_settings = read_settings(self.params) if self.aol_replay else None
    self.aol_qualified = bool(self.aol_settings is not None and
                              (aol_policy.full_axis_runtime_required or aol_policy.physical_stalk_owner or independent_axis_requested(self.aol_settings,
                                include_auxiliary=aol_policy.intent_supported and not aol_policy.explicit_latch)) and
                              aol_policy.intent_supported and not self.CP.passive)
    self.aol_card_intent = (create_aol_intent(self.CP, self.aol_settings, aol_policy)
                            if self.aol_qualified and self.aol_settings is not None else None)
    self.distance_personality_tracker = ButtonTracker() if aol_policy.distance_personality else None
    self.aol_sequence = 0
    self.aol_process_fault_context = AolProcessFaultContext() if self.aol_replay else None
    if self.aol_qualified:
      if self.CP.brand == 'ford':
        from opendbc.car.ford.aol import aol_safety_config as ford_aol_safety_config
        config = ford_aol_safety_config(self.CP)
      else:
        config = (volkswagen_aol_safety_config(self.CP) if self.CP.brand == 'volkswagen' else
                  (hyundai_aol_safety_config(self.CP) if self.CP.brand == 'hyundai' else self.CP.safetyConfigs[0]))
      if config is None:
        raise RuntimeError('AOL requires the exact vehicle safety configuration')
      config.safetyParam |= aol_policy.safety_param_addition
      self.CP.alternativeExperience |= aol_policy.alternative_experience_addition
    self.vehicle_startup.finalize_aol_configuration(self.CI)
    self.learning_stamp_observer = None
    self.learning_send_mono_ns = self.learning_send_committed_mono_ns = 0
    self.learning_car_state_source_ns = 0
    self.learning_applied_evidence = self.learning_previous_applied_evidence = None
    try:
      from openpilot.starpilot.lateral.learning_preferences import read_preference
      preference = read_preference(self.params, self.CP)
      if preference.valid and preference.enabled:
        from opendbc.car.steering_projection import install_observer
        self.learning_stamp_observer = self.record_learning_send_stamp if install_observer(self.CI) else None
    except Exception:
      pass  # Optional learning setup cannot stop native startup.
    self.aol_transport_feedback = TransportPauseFeedback(self.CP) if self.aol_replay else None
    if isinstance(self.CI, GMInterface) and self.CI.CC is not None and self.CI.CC.volt_sng_plan_input is not None:
      self.CI.CC.volt_sng_plan_input.boottime = self.volt_startup_keepalive()

    # Reserve optional transport from finalized capability, not saved admission.
    self.slc_transport_available = slc_transport_capable(self.CP, os.environ)
    if self.slc_transport_available:
      if 'slcState' not in subscribed_services:
        subscribed_services.append('slcState')
        self.sm = messaging.SubMaster(subscribed_services)
      for ignored in (self.sm.ignore_alive, self.sm.ignore_valid, self.sm.ignore_average_freq):
        if 'slcState' not in ignored:
          ignored.append('slcState')
      if 'slcDashboardObservation' not in self.pm.sock:
        self.pm.sock['slcDashboardObservation'] = messaging.pub_sock('slcDashboardObservation')
    self.slc_requested_configuration = self.read_slc_configuration()
    self.slc_configuration = self.slc_requested_configuration
    self.slc_replay = self.slc_configuration[0]
    self.slc_control_enabled = self.slc_replay and self.slc_configuration[1].enabled
    self.slc_match_on_engage = self.slc_transport_available and self.read_slc_match_on_engage()
    self.slc_source_floor_ns = 0

    # Write previous route's CarParams
    prev_cp = get_cache(self.params, "CarParamsPersistent")
    if prev_cp is not None:
      with car.CarParams.from_bytes(prev_cp) as previous:
        put_cache(self.params, "CarParamsPrevRoute", previous, block=True)

    # Write CarParams for controls and radard
    self.vehicle_startup.seal_publication()
    cp_bytes = self.CP.to_bytes()
    from openpilot.starpilot.car.tesla.pedal_startup import install as install_preap_pedal
    install_preap_pedal(self.CI, self.CP, cp_bytes, startup_preferences.tesla_preap_pedal, self.params)
    from openpilot.starpilot.car.tesla.radar_context import install_startup_context
    self.tesla_preap_radar_context = install_startup_context(
      self.RI, self.CP, cp_bytes, startup_preferences.tesla_preap_radar)
    from openpilot.starpilot.car.subaru.smoothing import install as install_subaru_smoothing
    install_subaru_smoothing(self.CP, cp_bytes, startup_preferences.subaru_smoothing, self.params)
    self.params.put("CarParams", cp_bytes, block=True)
    put_cache(self.params, "CarParamsCache", self.CP)
    put_cache(self.params, "CarParamsPersistent", self.CP)

    self.controller_cruise_consumer = CruiseActionConsumer()
    self.controller_cruise_sock = messaging.sub_sock('slcAction', conflate=False) if cruise_action_eligible(self.CP) else None
    self.v_cruise_helper = VCruiseHelper(self.CP)
    self.v_cruise_helper.intervals = read_cruise_intervals(self.params, self.CP)

    self.experimental_mode = self.params.get_bool("ExperimentalMode")
    self.initial_cruise_speed = self.params.get("InitialCruiseSpeedKph", return_default=True)

    # card is driven by can recv, expected at 100Hz
    self.rk = Ratekeeper(100, print_delay_threshold=None)
    self.ioniq6_init_complete = True

  def read_slc_configuration(self):
    settings = slc_runtime_settings(self.params, self.CP, os.environ)
    admitted = (self.slc_transport_available and not settings.errors and
                feature_enabled(self.params, self.CP, 'slc', os.environ))
    return bool(admitted), settings

  def read_slc_match_on_engage(self) -> bool:
    return read_saved(self.params, 'SLCOnEngage', 1) == (b'1', True)

  def refresh_slc_configuration(self, now_ns: int) -> None:
    configuration = getattr(self, 'slc_requested_configuration', None)
    if configuration is None or configuration == getattr(self, 'slc_configuration', None):
      return
    self.slc_configuration = configuration
    self.slc_replay = configuration[0]
    self.slc_control_enabled = self.slc_replay and configuration[1].enabled
    # Invalidate old SLC choices while keeping consumed buttons latched until release.
    self.slc_source_floor_ns = max(getattr(self, 'slc_source_floor_ns', 0), now_ns)

  def observe_distance_personality(self, CS: car.CarState, now_ns: int) -> None:
    tracker = getattr(self, 'distance_personality_tracker', None)
    if tracker is None:
      return
    for gesture in tracker.observe(CS):
      if gesture.button is not Button.DISTANCE:
        continue
      key, slot = DISTANCE_GESTURE_KEYS[gesture.press]
      try:
        raw, readable = read_saved(self.params, key, 8)
      except (OSError, TypeError, ValueError):
        raw, readable = None, False
      sampled = self.aol_card_intent.settings.distance_actions[slot] if self.aol_card_intent is not None else 0
      handled = sampled in (3, 4) or sampled == 9 and self.aol_card_intent is not None and \
        self.aol_card_intent.settings.enabled and not self.aol_card_intent.explicit_latch
      configured = readable and (raw in (b'3', b'4') or ioniq6_long_eligible(self.CP) and raw == b'5')
      if handled or configured:
        tracker.claim(gesture, now_ns, key)


  def state_update(self, *, defer_radar=False) -> tuple[car.CarState, structs.RadarDataT | None]:
    """carState update loop, driven by can"""

    can_strs = messaging.drain_sock_raw(self.can_sock, wait_for_one=True)
    can_received_ns = time.monotonic_ns()
    can_list = can_capnp_to_list(can_strs)

    # Update carState from CAN
    observe_controller_can(self.CI, can_list)
    CS = self.CI.update(can_list)
    from openpilot.starpilot.car.gm.aol import f1_conventional_lateral_owner
    cancel_boot_ns = (time.clock_gettime_ns(time.CLOCK_BOOTTIME)
                      if (getattr(self.CI.CS, 'ev6_aol_source_healthy', False) or
                          qualified_honda_stock_aol(self.CP, marked_only=True) or
                          qualified_gm(self.CP) and (is_bolt_present_no_acc_pedal_profile(self.CP) or
                                                     f1_conventional_lateral_owner(self.CP)) and
                          int(self.CP.alternativeExperience) in (32, 96)) else 0)
    self.observe_ioniq6_long_authority(can_list, CS)
    media_owner = getattr(self, 'ioniq6_media', None)
    media_observation = media_owner.update(can_list) if media_owner is not None else None
    distance_owner = getattr(self, 'gm_distance', None)
    distance_observation = distance_owner.update(can_list) if distance_owner is not None else None

    # Direct state_update callers retain eager radar behavior. The production
    # loop consumes this exact CAN batch after carState and control publication.
    self._deferred_radar_can = can_list if defer_radar else None
    RD = None if defer_radar else self.radar_update(can_list)

    self.sm.update(0)

    self.update_vehicle_state_context(CS)

    can_rcv_valid = len(can_strs) > 0

    # Check for CAN timeout
    if not can_rcv_valid:
      self.can_rcv_cum_timeout_counter += 1

    if can_rcv_valid and REPLAY:
      self.can_log_mono_time = messaging.log_from_bytes(can_strs[0]).logMonoTime

    self.slc_receipts = []
    now_ns = int(self.can_log_mono_time) if REPLAY and hasattr(self, 'can_log_mono_time') else time.monotonic_ns()
    settings_worker = getattr(self, 'conditional_settings_worker', None)
    if settings_worker is not None:
      settings_worker.adopt()
    self._aol_can_observation = (CS, can_received_ns,
                                 max((stamp for stamp, _ in can_list), default=0), cancel_boot_ns)
    self.refresh_slc_configuration(now_ns)
    evidence = getattr(self.CI.CS, 'dashboard_limit', None) if self.slc_replay else None
    observation = evidence.observation if evidence is not None else None
    floor_ns = getattr(self, 'slc_source_floor_ns', 0)
    if (observation is not None and floor_ns and
        (observation.observed_ns <= floor_ns or int(self.sm.logMonoTime['slcState']) <= floor_ns or
         int(self.sm['slcState'].frameMonoTime) <= floor_ns)):
      observation = None
    control_log_ns = int(self.sm.logMonoTime['carControl'])
    host_enabled = bool(
      CS.canValid and not CS.canTimeout and
      self.CC_prev.enabled and
      self.sm.valid['carControl'] and self.sm.alive['carControl'] and
      0 < control_log_ns <= now_ns and now_ns - control_log_ns <= slc_physical.STATE_MAX_AGE_NS and
      self.sm['carControl'].enabled)
    host_control_enabled = bool(self.CP.openpilotLongitudinalControl and not self.CP.pcmCruise and host_enabled)
    host_long_active = bool(self.CP.openpilotLongitudinalControl and host_enabled and self.sm['carControl'].longActive)
    slc_long_active = bool(getattr(self, 'slc_control_enabled', self.slc_replay) and self.CP.openpilotLongitudinalControl and
                           host_enabled and (self.sm['carControl'].longActive or CS.gasPressed))
    pending = None
    if self.slc_replay:
      pending = slc_physical.pending_confirmation(
        self.sm, now_ns, long_active=slc_long_active, after_ns=floor_ns)
    wheel_context = None
    slc_settings = getattr(self, 'slc_configuration', (False, None))[1]
    cruise_helper = self.v_cruise_helper
    if (self.slc_replay and getattr(slc_settings, 'wheel_override', False) and host_long_active and can_rcv_valid and
        not self.CP.pcmCruise and pending is None and (cruise_helper.slc_wheel_press is not None or len(CS.buttonEvents) > 0) and
        not (CS.brakePressed or CS.gasPressed or CS.standstill or CS.cruiseState.standstill)):
      wheel_context = slc_physical.wheel_override_context(self.sm, now_ns, after_ns=floor_ns,
        selected_kph=cruise_helper.v_cruise_kph, is_metric=self.is_metric)
    self.update_cruise_speed(CS, pending, wheel_context=wheel_context)
    if self.controller_cruise_sock is not None:
      for _ in range(8):
        request = messaging.recv_one_or_none(self.controller_cruise_sock)
        if request is None:
          break
        self.controller_cruise_consumer.apply(request, self.CP, CS, self.sm, self.v_cruise_helper,
                                             now_ns=now_ns, is_metric=self.is_metric,
                                             enabled=bool(host_control_enabled and can_rcv_valid and not self.params.get_bool("SafeMode")))
    if self.slc_replay:
      self.apply_slc_adopt(now_ns, host_control_enabled=host_control_enabled, after_ns=floor_ns)
    consumed = self.v_cruise_helper.slc_consumed_button
    if self.aol_card_intent is not None:
      if self.aol_process_fault_context is None:
        self.aol_process_fault_context = AolProcessFaultContext()
      self.aol_process_fault_context.observe(self.sm, now_ns)
      if getattr(self, 'aol_transport_feedback', None) is None:
        self.aol_transport_feedback = TransportPauseFeedback(self.CP)
      cancel_source_ns = self.aol_cancel_source(CS, now_ns)
      brake_source_ns = self.aol_honda_brake_source(CS, now_ns)
      gm_withdrawal_source_ns = self.aol_gm_withdrawal_source(CS, now_ns)
      hkg_cancel_retained = hkg_cancel_intent_retained(self.CP, CS, self.sm, self.aol_transport_feedback,
        self.aol_card_intent, cancel_source_ns=cancel_source_ns, now_ns=now_ns,
        producer=getattr(self, 'slc_producer_session', ''))
      cancel_feedback = self.aol_transport_feedback
      if hkg_cancel_retained and cancel_feedback is not None and not cancel_feedback.longitudinal_cancel_ns:
        # The pure guard proved this exact raw CANCEL/FULL join. Bind
        # its first local receipt for the existing bounded withdrawal handshake;
        # held samples never renew it or any source/native/output deadline.
        if cancel_source_ns > cancel_feedback.last_cancel_source_ns:
          cancel_feedback.longitudinal_cancel_ns = cancel_source_ns
          cancel_feedback.last_cancel_source_ns = cancel_source_ns
      rivian_brake_retained = rivian_brake_intent_retained(self.CP, CS, self.sm, self.aol_transport_feedback,
        self.aol_card_intent, now_ns=now_ns, producer=self.slc_producer_session)
      angle_arm_pending = pending_stock_angle_arm(self.CP, CS, self.sm, self.aol_card_intent,
        now_ns=now_ns, producer=self.slc_producer_session,
        acknowledged_arm_ns=getattr(self, '_angle_aol_acknowledged_arm_ns', 0))
      ford_initial_wait = bool(not self.aol_transport_feedback.ever_lateral_active and
        self.aol_card_intent.allowed_latch and qualified_ford_aol(self.CP, marked_only=True) and
        ford_awaiting_initial_request(self.CP, CS, self.sm,
          current_native(self.sm, self.CP, now_ns=now_ns, axis_session_id=self.aol_axis_session()),
          self.aol_transport_feedback, self.aol_card_intent, now_ns=now_ns, producer=self.slc_producer_session))
      transport_reset = not (hkg_cancel_retained or rivian_brake_retained or angle_arm_pending or ford_initial_wait) and self.aol_transport_feedback.observe(
        self.sm, self.CP, CS, now_ns,
        latched=self.aol_card_intent.allowed_latch, cancel_source_ns=cancel_source_ns,
        brake_source_ns=brake_source_ns,
        gm_withdrawal_source_ns=gm_withdrawal_source_ns,
        intent_producer_session=getattr(self, 'slc_producer_session', ''),
        main_arm_ns=(getattr(self.aol_card_intent, 'main_arm_ns', 0) or
          getattr(self.aol_card_intent, 'physical_arm_ns', 0)),
        startup_calibration_pending=getattr(self.aol_card_intent, 'startup_calibration_pending', False),
        intent_paused=(getattr(self.aol_card_intent, 'pause_lateral', False) or
          ford_brake_pause_active(self.CP, CS, self.aol_settings)))
      fault_active = True if transport_reset else None
      event_ns = int(self.sm.logMonoTime['onroadEvents'])
      if (getattr(self.aol_card_intent, 'requires_fault_observation', self.aol_card_intent.explicit_latch) and
          self.sm.updated['onroadEvents'] and
          self.sm.valid['onroadEvents'] and 0 < event_ns <= now_ns and now_ns - event_ns <= 1_500_000_000):
        feedback = self.aol_transport_feedback
        # Reuse only this call's healthy observation; fault/physical paths reobserve.
        reuse_transport = rivian_brake_retained or bool(not transport_reset and not cancel_source_ns and not brake_source_ns and
          not gm_withdrawal_source_ns and CS.canValid and not CS.canTimeout and
          not CS.steerFaultTemporary and not CS.steerFaultPermanent and not CS.accFaulted and
          feedback.signature is not None and feedback.signature[5] == 'none' and
          not feedback.source_retention_started_ns and not feedback.longitudinal_cancel_ns and
          not feedback.honda_brake_ns and not feedback.gm_withdrawal_ns and not feedback.failed and not feedback.paused and
          not feedback.pending_event and not feedback.awaiting and not feedback.qualified and
          not feedback.retention_started_ns and not feedback.retention_qualified and not feedback.retention_pending and
          not any(e.immediateDisable or e.softDisable or e.userDisable for e in self.sm['onroadEvents']))
        fault_active = transport_reset or self.aol_disarming_fault(CS, event_ns, now_ns,
          transport_observed_healthy=reuse_transport or angle_arm_pending or ford_initial_wait)
      angle_aol, ford_aol, honda_aol, mazda_aol, preap_aol, volkswagen_aol = self.aol_permission_owners()
      nissan_aol = qualified_nissan_aol(self.CP, marked_only=True)
      rivian_aol = qualified_rivian_aol(self.CP, marked_only=True)
      permission_owner = angle_aol or ford_aol or honda_aol or mazda_aol or preap_aol or nissan_aol or volkswagen_aol or rivian_aol
      angle_panda_ready = not permission_owner or self.startup_panda_configured()
      native = (
        current_native(self.sm, self.CP, now_ns=now_ns, axis_session_id=self.aol_axis_session() if permission_owner else None)
        if self.aol_card_intent.explicit_latch and (permission_owner or self.sm.updated['aolSafetyWire'])
        else None
      )
      native_reset = False
      if permission_owner:
        observe = (rivian_native_observation if rivian_aol else
                   volkswagen_native_observation if volkswagen_aol else nissan_native_observation if nissan_aol else
                   preap_native_observation if preap_aol else mazda_native_observation if mazda_aol else self.aol_honda_native_observation if honda_aol else
                   ford_native_observation if ford_aol else native_observation)
        restrict = (rivian_temporary_restriction if rivian_aol else
                    volkswagen_temporary_restriction if volkswagen_aol else nissan_temporary_restriction if nissan_aol else
                    preap_temporary_restriction if preap_aol else mazda_temporary_restriction if mazda_aol else honda_temporary_restriction if honda_aol else
                    ford_temporary_restriction if ford_aol else temporary_restriction)
        preap_observation = {}
        if preap_aol:
          cancel_stamp = int(self.CI.CS.preap.cancel_echo_stamp_ns)
          if cancel_stamp > getattr(self, '_preap_cancel_echo_stamp_ns', 0):
            self._preap_cancel_echo_stamp_ns = cancel_stamp
            self._preap_cancel_echo_pending_ns = now_ns
          preap_observation['pending_cancel_ns'] = getattr(self, '_preap_cancel_echo_pending_ns', 0)
        pending_since, lost, native_reset = observe(
          native,
          latched=self.aol_card_intent.allowed_latch,
          panda_ready=angle_panda_ready,
          restricted=(getattr(self.aol_transport_feedback, 'ordinary_warning_qualified', False) or
            ford_initial_wait and not self.sm['aolAxisState'].desiredLateral or
            restrict(self.CP, CS) or
            ((angle_aol or ford_aol or mazda_aol or preap_aol or rivian_aol) and self.aol_card_intent.pause_lateral) or
            (angle_aol and CS.brakePressed and (not CS.standstill) and (CS.vEgo < self.aol_settings.pause_brake_mps)) or
            ford_brake_pause_active(self.CP, CS, self.aol_settings) or
            (volkswagen_aol and (self.aol_card_intent.pause_lateral or
            (CS.brakePressed and (not CS.standstill) and (CS.vEgo < self.aol_card_intent.settings.pause_brake_mps))))),
          now_ns=now_ns,
          pending_since_ns=getattr(self, '_angle_aol_pending_since_ns', 0),
          **preap_observation,
        )
        if (preap_aol and native is not None and preap_observation['pending_cancel_ns'] and
            native.observedMonoTime >= preap_observation['pending_cancel_ns']):
          self._preap_cancel_echo_pending_ns = 0
        self._angle_aol_pending_since_ns = pending_since
        if lost:
          fault_active = True
      if (angle_aol and native is not None and native.requestedLateral and native.lateralAllowed and
          0 < self.aol_card_intent.physical_arm_ns <= native.observedMonoTime):
        self._angle_aol_acknowledged_arm_ns = self.aol_card_intent.physical_arm_ns
      subaru_braked_main_pending = bool(self.CP.brand == 'subaru' and not transport_reset and native is not None and
        getattr(self.aol_transport_feedback, 'subaru_main_retained_native_ns', 0) == int(native.observedMonoTime))
      rejected = native_reset or (not angle_arm_pending and not subaru_braked_main_pending and
        native_latch_rejected(self.CP, native, state=CS if angle_panda_ready else None))
      rejection_ns = now_ns if native_reset else int(native.observedMonoTime) if rejected else 0
      observe_physical = getattr(self.aol_card_intent, 'observe_physical_samples', None)
      if observe_physical is not None:
        observe_physical(self.CI.CS, now_ns=now_ns)
      startup_readiness = getattr(self.aol_card_intent, 'observe_startup_readiness', None)
      if startup_readiness is not None:
        startup_readiness(self.sm, CS, native=native, panda_ready=angle_panda_ready,
                          now_ns=now_ns, producer=self.slc_producer_session)
      self.aol_card_intent.update(CS, fault_active=fault_active, now_ns=now_ns, native_rejection_ns=rejection_ns,
                                  standard_enabled=(host_enabled if getattr(self.aol_card_intent, 'observe_stock_engagement', False)
                                                    else host_control_enabled))
      # Observe ordinary state directly: independent AOL actuator activity is
      # not evidence that ordinary preEnabled has become active engagement.
      engagement_ns = int(self.sm.logMonoTime.get('selfdriveState', 0))
      engagement_seen = self.sm.seen.get('selfdriveState', False)
      engagement = self.sm['selfdriveState'] if engagement_seen else None
      self.aol_card_intent.observe_full_engagement(
        CS, enabled=bool(engagement is not None and engagement.enabled),
        active=bool(engagement is not None and engagement.active), source_ns=engagement_ns,
        now_ns=now_ns, fault_active=fault_active, native_rejection_ns=rejection_ns,
        observation_valid=bool(engagement_seen and self.sm.valid['selfdriveState'] and self.sm.alive['selfdriveState'] and
          0 < engagement_ns <= now_ns and now_ns - engagement_ns <= AXIS_MAX_AGE_NS))
    if self.aol_card_intent is not None and preap_aol:
      self.CI.CS.preap_lateral_authorized = bool(
        native is not None and native.lateralAllowed and self.aol_card_intent.allowed_latch and
        not self.aol_card_intent.pause_lateral and CS.canValid and not CS.canTimeout and
        CS.gearShifter == structs.CarState.GearShifter.drive and not CS.doorOpen and
        not CS.steerFaultPermanent and not CS.steeringDisengage)
    if self.aol_card_intent is not None:
      self.aol_card_intent.update_auxiliary(CS, media=media_observation,
        media_eligible=ioniq6_media_eligible(self.CP), fault_active=fault_active)
      self.observe_aol_calibration(CS, now_ns,
        host_enabled if getattr(self.aol_card_intent, 'observe_stock_engagement', False) else host_control_enabled)
    self.observe_distance_personality(CS, now_ns)
    gm_claim = getattr(self, 'gm_distance_claim_tracker', None)
    if gm_claim is not None:
      gm_claim.observe(CS)
    self.manual_receipt = None
    self.traffic_receipt = None
    if (self.conditional_replay and (not REPLAY or hasattr(self, 'can_log_mono_time')) and
        self.manual_button_tracker is not None and self.manual_settings_owner is not None):
      # Live Card uses its worker-verified, frame-local view; replay retains
      # the source-clock owner. Held gestures lose claims on unavailable settings.
      manual_snapshot = self.manual_settings_owner.refresh(now_ns)
      manual_drive_id = int(self.sm['deviceState'].startedMonoTime)
      manual_verdict = self.manual_settings_owner.verdict(manual_snapshot, now_mono_ns=now_ns, drive_id=manual_drive_id)
      manual_fingerprint = (settings_fingerprint(manual_snapshot, owner=self.manual_settings_owner)
                            if manual_verdict.status == 'ready' and manual_verdict.safe_mode is False and
                            manual_verdict.selection is not None and manual_verdict.selection.choice in (ModeChoice.CEM, ModeChoice.CCM)
                            else None)
      self.manual_button_tracker.keep_claim(manual_drive_id, manual_fingerprint)
      shared_media_map = (self.ioniq_media_map.sample(self.params, media_observation, now_ns)
                          if self.ioniq_media_map is not None and ioniq6_media_eligible(self.CP) else None)
      shared_media_captured = self.ioniq_media_map is not None and ioniq6_media_eligible(self.CP)
      self.manual_receipt = conditional_manual_candidate(
        CS, self.manual_button_tracker, self.params, self.manual_settings_owner, self.CP, self.sm,
        now_ns=now_ns, explicit_aol_latch=bool(self.aol_card_intent is not None and self.aol_card_intent.explicit_latch),
        media=media_observation, media_buttons=shared_media_map, media_map_captured=shared_media_captured)
      if self.manual_receipt is not None:
        gesture, drive_id, fingerprint, _, _ = self.manual_receipt
        self.manual_button_tracker.claim(gesture, drive_id, fingerprint)
      gm_buttons = (self.gm_distance_map.sample(self.params, distance_observation, now_ns)
                    if distance_owner is not None else None)
      if self.traffic_button_tracker is not None:
        self.traffic_receipt = conditional_traffic_candidate(
          CS, self.traffic_button_tracker, self.params, self.manual_settings_owner,
          self.CP, self.sm, now_ns=now_ns, media=media_observation,
          media_buttons=gm_buttons if distance_owner is not None else shared_media_map,
          media_map_captured=distance_owner is not None or shared_media_captured, distance=distance_observation)
        if self.traffic_receipt is not None and self.traffic_receipt[0] and distance_owner is not None and gm_claim is not None:
          gm_claim.claim(self.traffic_receipt[1], self.traffic_receipt[2], self.traffic_receipt[3])
    self.switchback_receipt = None
    if self.switchback_button_tracker is not None and self.switchback_settings_owner is not None:
      self.switchback_receipt = conditional_traffic_candidate(
        CS, self.switchback_button_tracker, self.params, self.switchback_settings_owner,
        self.CP, self.sm, now_ns=now_ns, media=media_observation, action=7)
    if consumed is not None:
      self.slc_receipts.append(('confirmationAccept' if consumed.button == 'accel' else 'confirmationReject',
                                consumed, 0.0, 0.0, now_ns))
    curve_receipt = curve_press_receipt(CS, self.v_cruise_helper, self.sm, curve_replay=self.curve_replay,
                                        slc_replay=self.slc_replay, host_long_active=host_long_active, observed_ns=now_ns)
    if curve_receipt is not None:
      self.slc_receipts.append(curve_receipt)
    blocked_keys = set()
    if self.manual_receipt is not None:
      blocked_keys.add(key_for(self.manual_receipt[0]))
    for receipt in (self.traffic_receipt, self.switchback_receipt):
      if receipt is not None and receipt[0] and receipt[1] is not None:
        blocked_keys.add(key_for(receipt[1]))
    self.wheel_commands = self.wheel_publisher.observe(
      self.params, self.CP, CS, now_ns=now_ns, drive_id=int(self.sm['deviceState'].startedMonoTime),
      media=media_observation, blocked_keys=blocked_keys)
    # TODO: mirror the carState.cruiseState struct?
    CS.vCruise = float(self.v_cruise_helper.v_cruise_kph)
    CS.vCruiseCluster = float(self.v_cruise_helper.v_cruise_cluster_kph)

    return CS, RD

  def apply_slc_adopt(self, now_ns: int, *, host_control_enabled: bool, after_ns: int = 0) -> None:
    pending = getattr(self, 'slc_wheel_adopt', None)
    if pending is None:
      return
    wheel_ns, selected_kph = pending
    helper = self.v_cruise_helper
    if (not after_ns < wheel_ns <= now_ns <= wheel_ns + WHEEL_ACTION_MAX_AGE_NS or
        not host_control_enabled or not self.slc_control_enabled or
        not helper.v_cruise_initialized or helper.v_cruise_kph != selected_kph or any(helper.button_timers.values())):
      self.slc_wheel_adopt = None
      return
    target = slc_physical.adopted_target(self.sm, now_ns, wheel_ns=wheel_ns)
    if target is not None:
      self.slc_wheel_adopt = None
      helper.apply_slc_target(target, self.is_metric)

  def update_cruise_speed(self, CS, pending=None, *, wheel_context: SlcWheelContext | None = None):
    self.v_cruise_helper.update_v_cruise(CS, self.sm['carControl'].enabled, self.is_metric, pending,
                                       slc_enabled=getattr(self, 'slc_control_enabled', getattr(self, 'slc_replay', False)),
                                       wheel_context=wheel_context)
    if self.sm['carControl'].enabled and not self.CC_prev.enabled:
      target, initial = None, None
      now_ns = int(self.can_log_mono_time) if REPLAY and hasattr(self, 'can_log_mono_time') else time.monotonic_ns()
      if (getattr(self, 'slc_match_on_engage', False) and getattr(self, 'slc_control_enabled', False) and
          not self.CP.pcmCruise and self.CP.openpilotLongitudinalControl and
          CS.canValid and not CS.canTimeout and CS.cruiseState.available and
          self.sm.valid['carControl'] and self.sm.alive['carControl'] and
          0 < int(self.sm.logMonoTime['carControl']) <= now_ns and
          now_ns - int(self.sm.logMonoTime['carControl']) <= slc_physical.STATE_MAX_AGE_NS):
        long_requested = self.sm['carControl'].longActive or CS.gasPressed
        if not long_requested and 'aolAxisState' in self.sm.logMonoTime:
          # AOL can acknowledge the request before longitudinal actuators become active.
          axis = current_axis(self.sm, now_ns=now_ns)
          long_requested = axis is not None and axis.nativeAcknowledged and axis.desiredLongitudinal
        if long_requested:
          evidence = getattr(self.CI.CS, 'dashboard_limit', None)
          target, initial = slc_physical.engagement_target(
            self.sm, evidence.observation if evidence is not None else None, self.slc_producer_session,
            now_ns, after_ns=getattr(self, 'slc_source_floor_ns', 0))
      adopted = self.v_cruise_helper.initialize_v_cruise(
        self.CS_prev, self.experimental_mode, resume=self.vehicle_startup.consume_cruise_resume(), initial_speed=self.initial_cruise_speed,
        slc_target_mps=target, is_metric=self.is_metric)
      if adopted and initial is not None:
        self.slc_receipts.append(('engagementAdopt', initial, 0.0, float(self.v_cruise_helper.v_cruise_kph / 3.6), now_ns))

  def aol_cancel_source(self, CS, now_ns: int) -> int:
    """Bind Cancel to this parsed CAN batch and its local monotonic receipt."""
    observation = getattr(self, '_aol_can_observation', None)
    if getattr(getattr(self, 'CP', None), 'brand', '') == 'honda' and qualified_honda_stock_aol(self.CP, marked_only=True):
      from opendbc.car import Bus
      parser = self.CI.can_parsers[Bus.pt]
      stamp = int(parser.ts_nanos.get('SCM_BUTTONS', {}).get('CRUISE_BUTTONS', 0))
      if observation is None or observation[0] is not CS:
        return 0
      _, received_ns, can_source_ns, boot_ns = observation
      if (parser.vl.get('SCM_BUTTONS', {}).get('CRUISE_BUTTONS') != 2 or
          not any(e.type == structs.CarState.ButtonEvent.Type.cancel and e.pressed for e in CS.buttonEvents) or
          not 0 < received_ns <= now_ns or now_ns - received_ns > 30_000_000 or
          not 0 < stamp <= can_source_ns <= boot_ns or boot_ns - stamp > 30_000_000 or
          can_source_ns - stamp > 30_000_000):
        return 0
      return received_ns
    from opendbc.car.hyundai.values import Buttons
    state = getattr(getattr(self, 'CI', None), 'CS', None)
    observation = getattr(self, '_aol_can_observation', None)
    if (state is None or observation is None or observation[0] is not CS or
        not getattr(state, 'ev6_aol_source_healthy', False)):
      return 0
    _, received_ns, can_source_ns, boot_ns = observation
    stamp = int(state.ev6_aol_sample_stamp_ns)
    # Parser source and CAN batch use the producer clock; compare only those.
    # The fixed intent-retention deadline uses the local MONOTONIC receipt.
    if (not 0 < received_ns <= now_ns or now_ns - received_ns > 30_000_000 or
        not 0 < stamp <= can_source_ns <= boot_ns or
        boot_ns - stamp > min(int(state.ev6_aol_timeout_ns), 30_000_000) or
        can_source_ns - stamp > min(int(state.ev6_aol_timeout_ns), 30_000_000) or
        not any(e.type == structs.CarState.ButtonEvent.Type.cancel and e.pressed for e in CS.buttonEvents) or
        not any(cruise == Buttons.CANCEL for _, _, cruise in state.ev6_aol_samples)):
      return 0
    return received_ns

  def aol_honda_brake_source(self, CS, now_ns: int) -> int:
    observation = getattr(self, '_aol_can_observation', None)
    if (not CS.brakePressed or not qualified_honda_stock_aol(self.CP, marked_only=True) or
        observation is None or observation[0] is not CS):
      return 0
    from opendbc.car import Bus
    from opendbc.car.honda.values import HondaFlags
    parser = self.CI.can_parsers[Bus.pt]
    message = 'BRAKE_MODULE' if self.CP.flags & HondaFlags.BOSCH_ALT_BRAKE else 'POWERTRAIN_DATA'
    signals = parser.vl.get(message, {})
    # Match the real parser's applied-brake or two-sample switch decision.
    brake = bool(signals.get('BRAKE_PRESSED', 0) or message == 'POWERTRAIN_DATA' and
                 signals.get('BRAKE_SWITCH', 0) and self.CI.CS.brake_switch_active)
    stamp = int(parser.ts_nanos.get(message, {}).get('BRAKE_PRESSED', 0))
    _, received_ns, can_source_ns, boot_ns = observation
    # CANParser/CAN batch use BOOTTIME; retain the original MONOTONIC receipt.
    if (not brake or not 0 < received_ns <= now_ns or now_ns - received_ns > 30_000_000 or
        not 0 < stamp <= can_source_ns <= boot_ns or boot_ns - stamp > 30_000_000 or
        can_source_ns - stamp > 30_000_000):
      return 0
    return received_ns

  def aol_gm_withdrawal_source(self, CS, now_ns: int) -> int:
    if self.CP.brand != 'gm':
      return 0
    from opendbc.car import Bus
    from openpilot.starpilot.car.gm.aol import physical_longitudinal_withdrawal_source
    parser = getattr(getattr(self, 'CI', None), 'can_parsers', {}).get(Bus.pt)
    return physical_longitudinal_withdrawal_source(self.CP, CS, parser,
      getattr(self, '_aol_can_observation', None), now_ns)

  def aol_disarming_fault(self, CS, event_ns: int, now_ns: int, *, transport_observed_healthy=False) -> bool:
    if not aol_policy_for(self.CP).intent_supported:
      return True
    from openpilot.starpilot.aol.vehicle import intent_disarming_fault
    if getattr(self, 'aol_transport_feedback', None) is None:
      self.aol_transport_feedback = TransportPauseFeedback(self.CP)
    hkg_cancel_retained = hkg_cancel_intent_retained(self.CP, CS, self.sm, self.aol_transport_feedback,
      self.aol_card_intent, cancel_source_ns=self.aol_cancel_source(CS, now_ns), now_ns=now_ns,
      producer=getattr(self, 'slc_producer_session', ''))
    if not transport_observed_healthy and not hkg_cancel_retained and self.aol_transport_feedback.observe(self.sm, self.CP, CS, now_ns,
        latched=getattr(getattr(self, 'aol_card_intent', None), 'allowed_latch', True),
        cancel_source_ns=self.aol_cancel_source(CS, now_ns), brake_source_ns=self.aol_honda_brake_source(CS, now_ns),
        gm_withdrawal_source_ns=self.aol_gm_withdrawal_source(CS, now_ns),
        intent_producer_session=getattr(self, 'slc_producer_session', ''),
        main_arm_ns=(getattr(self.aol_card_intent, 'main_arm_ns', 0) or
          getattr(self.aol_card_intent, 'physical_arm_ns', 0)),
        startup_calibration_pending=getattr(self.aol_card_intent, 'startup_calibration_pending', False),
        intent_paused=(getattr(self.aol_card_intent, 'pause_lateral', False) or
          ford_brake_pause_active(self.CP, CS, self.aol_settings))):
      return True
    events = self.sm['onroadEvents']
    if self.aol_transport_feedback.retention_qualified or self.aol_transport_feedback.retention_pending:
      from openpilot.starpilot.aol.transport_pause import AVAILABILITY_EVENTS
      events = [event for event in events if not
                (event.name in AVAILABILITY_EVENTS and event.softDisable and not event.immediateDisable)]
    if getattr(self.aol_transport_feedback, 'temporary_host_qualified', False):
      events = [event for event in events if not (event.name in
                (log.OnroadEvent.EventName.locationdTemporaryError, log.OnroadEvent.EventName.commIssue) and
                event.softDisable and not event.immediateDisable)]
    return intent_disarming_fault(self.CP, events, CS,
      startup_calibration_pending=bool(getattr(self.aol_card_intent, 'startup_calibration_pending', False) and
        self.aol_card_intent.settings.enabled and self.aol_card_intent.main_action != 9),
      temporary_ui_process_failure=self.aol_process_fault_context.temporary_ui_failure(event_ns, now_ns),
      temporary_selfdrive_lagging=(qualified_gm(self.CP) or aol_policy_for(self.CP).intent_supported),
      temporary_source_pause=(self.aol_transport_feedback.qualified or self.aol_transport_feedback.awaiting))

  def aol_permission_owners(self) -> tuple[bool, bool, bool, bool, bool, bool]:
    # CP is fixed once the drive starts; these checks ran five times per 100 Hz frame.
    cached = getattr(self, '_aol_owner_cache', None)
    if cached is None or cached[0] is not self.CP:
      owners = (bool(qualified_angle_aol(self.CP, marked_only=True)), bool(qualified_ford_aol(self.CP, marked_only=True)),
                bool(qualified_honda_stock_aol(self.CP, marked_only=True)), bool(qualified_mazda_aol(self.CP, marked_only=True)),
                bool(qualified_tesla_preap(self.CP, marked_only=True)), bool(qualified_volkswagen_aol(self.CP, marked_only=True)))
      cached = self._aol_owner_cache = (self.CP, owners)
    return cached[1]

  def aol_honda_native_observation(self, native, *, latched, panda_ready, restricted, now_ns, pending_since_ns):
    acknowledged = getattr(self, '_honda_aol_acknowledgment', None)
    key = None
    host_requested = False
    if native is not None and qualified_honda_stock_aol(self.CP, marked_only=True):
      key = (cp_identity(self.CP), str(native.axisSessionId), str(native.pandaSerial),
             tuple((p.hardwareSerial, p.safetyModel, p.safetyParam) for p in native.pandaInventory))
      try:
        axis = current_axis(self.sm, now_ns=now_ns)
        feedback = self.aol_transport_feedback
        # A pre-publication OFF cannot revoke this newer host request.
        # Unmatched denial still uses the existing bounded pending timeout.
        host_requested = bool(axis is not None and axis.sessionId == native.axisSessionId and
          axis.observedMonoTime == self.sm.logMonoTime['aolAxisState'] and
          axis.validUntilMonoTime == axis.observedMonoTime + AXIS_MAX_AGE_NS and
          axis.faultSessionId == axis.sessionId and str(axis.faultReason) == 'none' and
          self.sm.seen['onroadEvents'] and self.sm.valid['onroadEvents'] and self.sm.alive['onroadEvents'] and
          0 < axis.faultEventMonoTime == self.sm.logMonoTime['onroadEvents'] <= axis.observedMonoTime and
          now_ns - axis.faultEventMonoTime <= 1_500_000_000 and
          not any(event.immediateDisable or event.softDisable for event in self.sm['onroadEvents']) and
          axis.sequence > 0 and axis.nativeAcknowledged and axis.desiredLateral and axis.lateralActive and
          axis.intentProducerSessionId == self.slc_producer_session and
          0 < axis.sourceCarStateMonoTime <= axis.observedMonoTime <= native.observedMonoTime <= now_ns and
          now_ns - axis.sourceCarStateMonoTime <= AXIS_MAX_AGE_NS and
          not feedback.qualified and not feedback.retention_qualified and not feedback.paused and
          not feedback.awaiting and not feedback.retention_pending)
      except (AttributeError, KeyError, TypeError, ValueError):
        host_requested = False
    previously_acknowledged = bool(acknowledged is not None and acknowledged[0] == key and
                                    0 < acknowledged[1] <= native.observedMonoTime) if native is not None else False
    if not latched or not panda_ready or key is None:
      self._honda_aol_acknowledgment = None
    elif native.requestedLateral and native.lateralAllowed:
      self._honda_aol_acknowledgment = (key, int(native.observedMonoTime))
    elif acknowledged is not None and acknowledged[0] != key:
      self._honda_aol_acknowledgment = None
    return honda_native_observation(native, latched=latched, panda_ready=panda_ready, restricted=restricted,
      now_ns=now_ns, pending_since_ns=pending_since_ns,
      previously_acknowledged=previously_acknowledged, host_requested_lateral=host_requested)

  def aol_axis_session(self) -> str:
    # pandad stamps aolSafetyWire with selfdrived's aolAxisState session, not card's producer session.
    seen = getattr(self.sm, 'seen', {})
    if 'aolAxisState' in seen and seen['aolAxisState'] and self.sm['aolAxisState'].sessionId:
      return str(self.sm['aolAxisState'].sessionId)
    return self.slc_producer_session

  def observe_aol_calibration(self, CS, now_ns: int, standard_enabled: bool) -> None:
    calibration_events = None
    event_ns = int(self.sm.logMonoTime['onroadEvents'])
    if (self.sm.updated['onroadEvents'] and self.sm.valid['onroadEvents'] and
        0 < event_ns <= now_ns and now_ns - event_ns <= 1_500_000_000):
      calibration_events = self.sm['onroadEvents']
    self.aol_card_intent.observe_calibration(CS, events=calibration_events, standard_enabled=standard_enabled)

  def state_publish(self, CS: car.CarState, RD: structs.RadarDataT | None):
    """carState and carParams publish loop"""

    # Publish the finalized CP immediately, then retain the segment cadence.
    if not self.car_params_published or self.sm.frame % int(50. / DT_CTRL) == 0:
      cp_send = messaging.new_message('carParams')
      cp_send.valid = True
      cp_send.carParams = self.CP
      self.pm.send('carParams', cp_send)
      self.car_params_published = True

    # publish new carOutput
    co_send = messaging.new_message('carOutput')
    co_send.valid = self.sm.all_checks(['carControl'])
    co_send.carOutput.actuatorsOutput = self.last_actuators_output
    self.learning_feedback_publication = None
    capture = self._feedback_capture(int(co_send.logMonoTime))
    if capture is not None:
      try:
        self.learning_feedback_publication = capture.publication(co_send)
      except Exception:
        capture.invalidate()  # Optional observation cannot affect the original CO.
    self.pm.send('carOutput', co_send)

    # kick off controlsd step while we actuate the latest carControl packet
    cs_send = messaging.new_message('carState')
    if (self.slc_replay or self.curve_replay or getattr(self, 'conditional_replay', False)) and REPLAY and hasattr(self, 'can_log_mono_time'):
      cs_send.logMonoTime = int(self.can_log_mono_time)
    cs_send.valid = CS.canValid
    cs_send.carState = CS
    manual_tracker = getattr(self, 'manual_button_tracker', None)
    personality_tracker = getattr(self, 'distance_personality_tracker', None)
    gm_claim = getattr(self, 'gm_distance_claim_tracker', None)
    if ((getattr(self, 'conditional_replay', False) and manual_tracker is not None and manual_tracker.suppress_distance_release) or
        (personality_tracker is not None and personality_tracker.suppress_distance_release) or
        (gm_claim is not None and gm_claim.suppress_distance_release) or
        (getattr(self, 'wheel_publisher', None) is not None and self.wheel_publisher.suppress_distance_release)):
      # This is the serialized carState copy. Keep the original parsed state
      # and raw CAN intact for the controller and diagnostics.
      cs_send.carState.buttonEvents = [event for event in CS.buttonEvents if not
                                       (event.type == car.CarState.ButtonEvent.Type.gapAdjustCruise and not event.pressed)]
    cs_send.carState.canErrorCounter = self.can_rcv_cum_timeout_counter
    cs_send.carState.cumLagMs = -self.rk.remaining * 1000.
    if self.aol_replay:
      self.aol_sequence += 1
      intent_msg = messaging.new_message('aolIntentWire', 0)
      intent_msg.logMonoTime = int(cs_send.logMonoTime)
      intent_msg.valid = bool(cs_send.valid and self.aol_qualified)
      allowed_latch = pause_lateral = pause_longitudinal = False
      lateral_armed = False
      if self.aol_card_intent is not None:
        allowed_latch, pause_lateral, pause_longitudinal = self.aol_card_intent.output(CS)
        lateral_armed = self.aol_card_intent.allowed_latch
      intent_msg.aolIntentWire = encode_intent(IntentState(
        self.slc_producer_session, self.aol_sequence, int(cs_send.logMonoTime), int(cs_send.logMonoTime),
        int(cs_send.logMonoTime) + 200_000_000, allowed_latch, pause_lateral, pause_longitudinal, self.aol_qualified, lateral_armed,
        bool(getattr(self.aol_card_intent, "optional_set_release_policy", lambda: False)())))
      self.pm.send('aolIntentWire', intent_msg)

    # carState wakes selfdrived. Commit its companion intent first, so a
    # consumer scheduled at the wakeup cannot sample the preceding intent.
    if self.CP.brand == 'tesla':
      from openpilot.starpilot.car.tesla.stock_events import publish_stock_status
      internal = getattr(self.CI.CS, 'preap', None)
      if internal is not None:
        self.slc_cruise_event_id += 1
        publish_stock_status(self.pm, self.CP, internal, session=self.slc_producer_session,
                             sequence=self.slc_cruise_event_id, car_state_stamp=int(cs_send.logMonoTime),
                             valid=bool(cs_send.valid and not CS.canTimeout))
    companion = None
    if self.CP.brand == 'tesla' and self.CP.openpilotLongitudinalControl and qualified_tesla_preap(self.CP):
      internal = self.CI.CS.preap
      companion = messaging.new_message('starpilotCarState', valid=bool(cs_send.valid), logMonoTime=int(cs_send.logMonoTime))
      companion.starpilotCarState.sourceCarStateMonoTime = int(cs_send.logMonoTime)
      companion.starpilotCarState.pedalLongActive = bool(internal.enableLongControl)
      companion.starpilotCarState.pedalMaxRegen = internal.pccEvent == 'pedalMaxRegen'
    steering_authority = getattr(self.CI.CS, 'steering_authority', None)
    if self.CP.brand == 'gm' and steering_authority is not None and steering_authority.enabled:
      companion = messaging.new_message('starpilotCarState', valid=bool(cs_send.valid), logMonoTime=int(cs_send.logMonoTime))
      companion.starpilotCarState.lateralAuthorityUnavailable = bool(steering_authority.latched)
      companion.starpilotCarState.sourceCarStateMonoTime = int(cs_send.logMonoTime)
    if self.CP.brand == 'tesla':
      info = getattr(self.CI.CC, 'steering_limit_info', None)
      if info:
        if companion is None:
          companion = messaging.new_message('starpilotCarState', valid=bool(cs_send.valid), logMonoTime=int(cs_send.logMonoTime))
        companion.starpilotCarState.sourceCarStateMonoTime = int(cs_send.logMonoTime)
        companion.starpilotCarState.steeringLimitInfo = info
    if getattr(self, 'learning_applied_evidence', None) is not None:
      try:
        from openpilot.starpilot.flm.evidence_hooks import applied_message
        # Commit the optional child only after it is complete. A failed writer
        # must not publish partial trust or discard existing GM authority/GPS.
        learning_companion = (companion.as_reader().as_builder() if companion is not None else
          messaging.new_message('starpilotCarState', valid=bool(cs_send.valid), logMonoTime=int(cs_send.logMonoTime)))
        if applied_message(learning_companion, self.learning_applied_evidence) is not True:
          raise ValueError('optional applied evidence write failed')
        companion = learning_companion
      except Exception:
        self._clear_learning_evidence()
    publication = getattr(self, 'learning_feedback_publication', None)
    if publication is not None:
      try:
        from openpilot.starpilot.flm.feedback_wire import write_publication
        feedback_companion = (companion.as_reader().as_builder() if companion is not None else
          messaging.new_message('starpilotCarState', valid=bool(cs_send.valid), logMonoTime=int(cs_send.logMonoTime)))
        write_publication(feedback_companion.starpilotCarState, publication)
        companion = feedback_companion
      except Exception:
        self.learning_feedback_publication = None
    if self.CP.brand == 'mazda':
      from openpilot.starpilot.car.mazda.dashboard import compose
      companion = compose(self.CP, self.CI.CS, CS, companion, car_state_stamp=int(cs_send.logMonoTime))
    elif self.CP.brand == 'hyundai':
      from openpilot.starpilot.car.hyundai.dashboard import compose as hyundai_compose
      companion = hyundai_compose(self.CP, self.CI.CS, CS, companion, car_state_stamp=int(cs_send.logMonoTime))
    self.car_gps_publisher.update(self.CI.CS, self.pm, companion)
    self.pm.send('carState', cs_send)
    if getattr(self, 'learning_stamp_observer', None) is not None:
      self.learning_car_state_source_ns = int(cs_send.logMonoTime)

    wheel = getattr(self, 'wheel_publisher', None)
    if wheel is not None:
      if (getattr(self, 'slc_control_enabled', False) and self.CC_prev.enabled and self.sm['carControl'].enabled and
          any(action == 10 for _, action in self.wheel_commands)):
        self.slc_wheel_adopt = (int(cs_send.logMonoTime), self.v_cruise_helper.v_cruise_kph)
      wheel.publish(self.wheel_commands, self.CP, self.pm, now_ns=int(cs_send.logMonoTime),
                    drive_id=int(self.sm['deviceState'].startedMonoTime), source_car_ns=int(cs_send.logMonoTime),
                    source_control_ns=int(self.sm.logMonoTime['carControl']))

    if self.slc_replay:
      source = messaging.new_message('slcDashboardObservation')
      source.logMonoTime = int(cs_send.logMonoTime)
      source.valid = bool(cs_send.valid)
      evidence = getattr(self.CI.CS, 'dashboard_limit', None)
      source.slcDashboardObservation.producerSessionId = self.slc_producer_session
      source.slcDashboardObservation.carStateLogMonoTime = int(cs_send.logMonoTime)
      if evidence is not None:
        observation = evidence.observation
        source.slcDashboardObservation.status = observation.status.value
        source.slcDashboardObservation.speedMps = observation.speed_mps
        source.slcDashboardObservation.observedMonoTime = observation.observed_ns
        source.slcDashboardObservation.validUntilMonoTime = observation.valid_until_ns
        source.slcDashboardObservation.episode = observation.episode
      self.pm.send('slcDashboardObservation', source)

    change = self.v_cruise_helper.slc_cruise_change
    if self.slc_replay or self.curve_replay:
      if change is not None:
        self.slc_receipts.append(('driverChange', change, change[0], change[1], int(cs_send.logMonoTime)))
      for kind, detail, previous_mps, selected_mps, observed_ns in self.slc_receipts:
        self.slc_cruise_event_id += 1
        event = messaging.new_message('slcCruiseEvent')
        event.logMonoTime = int(cs_send.logMonoTime)
        event.valid = bool(cs_send.valid)
        record = event.slcCruiseEvent
        record.eventId = self.slc_cruise_event_id
        record.producerSessionId = self.slc_producer_session
        record.observedMonoTime = observed_ns
        record.kind = kind
        record.previousMps = previous_mps
        record.selectedMps = selected_mps
        if kind == 'driverChange':
          assert isinstance(detail, tuple) and len(detail) == 4
          record.button, record.longPress = detail[2], detail[3]
          if self.v_cruise_helper.slc_wheel_tap is not None:
            record.sessionId, record.wheelOverrideSince = self.v_cruise_helper.slc_wheel_tap
        elif kind in ('confirmationAccept', 'confirmationReject'):
          record.button = detail.button
          record.sessionId = detail.pending.session_id
          record.decisionId = detail.pending.decision_id
          record.presentationId = detail.pending.presentation_id
        elif kind == 'curveAccelPress':
          record.button = 'accel'
        elif kind == 'engagementAdopt':
          record.sessionId = str(detail.sessionId)
          record.decisionId = int(detail.decisionId)
          record.presentationId = int(detail.presentationId)
          record.button = 'decel'
        self.pm.send('slcCruiseEvent', event)

    if getattr(self, 'conditional_replay', False) and self.manual_receipt is not None:
      gesture, drive_id, fingerprint, choice, observed_ns = self.manual_receipt
      self.slc_cruise_event_id += 1
      self.manual_event_sequence += 1
      event = messaging.new_message('slcCruiseEvent')
      event.logMonoTime = int(cs_send.logMonoTime)
      event.valid = bool(cs_send.valid)
      record = event.slcCruiseEvent
      record.eventId = self.slc_cruise_event_id
      record.producerSessionId = self.slc_producer_session
      record.observedMonoTime = observed_ns
      record.kind = 'conditionalMode'
      record.manualMode = {
        'version': 1, 'sessionId': self.slc_producer_session, 'sequence': self.manual_event_sequence,
        'observedMonoTime': observed_ns, 'driveStartMonoTime': drive_id,
        'settingsFingerprint': fingerprint,
        'choice': 'conditionalExperimental' if choice is ModeChoice.CEM else 'conditionalChill',
        'button': gesture.button.value, 'press': gesture.press.value,
        'sourceCarStateMonoTime': int(cs_send.logMonoTime),
        'validUntilMonoTime': int(cs_send.logMonoTime) + 100_000_000,
      }
      self.pm.send('slcCruiseEvent', event)

    self.publish_traffic_receipt(cs_send)

    self.radar_publish(RD)

  def radar_update(self, can_list):
    RD = self.RI.update(can_list)
    return RD

  def radar_publish(self, RD):
    if RD is not None:
      tracks_msg = messaging.new_message('radarTracks')
      tracks_msg.valid = not RD.errors.canError
      tracks_msg.radarTracks = RD
      self.pm.send('radarTracks', tracks_msg)

  def publish_traffic_receipt(self, cs_send):
    traffic_receipt = getattr(self, 'traffic_receipt', None)
    if getattr(self, 'conditional_replay', False) and traffic_receipt is not None:
      toggle, gesture, drive_id, fingerprint, map_fingerprint, source_epoch, source_boot_ns, observed_ns = traffic_receipt
      self.slc_cruise_event_id += 1
      self.traffic_event_sequence += 1
      event = messaging.new_message('slcCruiseEvent')
      event.logMonoTime = int(cs_send.logMonoTime)
      event.valid = bool(cs_send.valid)
      record = event.slcCruiseEvent
      record.eventId = self.slc_cruise_event_id
      record.producerSessionId = self.slc_producer_session
      record.observedMonoTime = observed_ns
      record.kind = 'trafficMode'
      record.trafficMode = {
        'version': 1, 'sessionId': self.slc_producer_session, 'sequence': self.traffic_event_sequence,
        'observedMonoTime': observed_ns, 'driveStartMonoTime': drive_id,
        'settingsFingerprint': fingerprint, 'buttonMapFingerprint': map_fingerprint,
        'sourceEpoch': source_epoch, 'sourceBootTime': source_boot_ns,
        'sourceCarStateMonoTime': int(cs_send.logMonoTime),
        'validUntilMonoTime': int(cs_send.logMonoTime) + 100_000_000,
        'toggle': toggle, 'button': gesture.button.value if gesture is not None else 'unknown',
        'press': gesture.press.value if gesture is not None else 'unknown',
      }
      self.pm.send('slcCruiseEvent', event)

    switchback_receipt = getattr(self, 'switchback_receipt', None)
    if getattr(self, 'switchback_capable', False) and switchback_receipt is not None:
      toggle, gesture, drive_id, fingerprint, map_fingerprint, source_epoch, source_boot_ns, observed_ns = switchback_receipt
      self.slc_cruise_event_id += 1
      self.switchback_event_sequence += 1
      event = messaging.new_message('slcCruiseEvent')
      event.logMonoTime = int(cs_send.logMonoTime)
      event.valid = bool(cs_send.valid)
      record = event.slcCruiseEvent
      record.eventId = self.slc_cruise_event_id
      record.producerSessionId = self.slc_producer_session
      record.observedMonoTime = observed_ns
      record.kind = 'switchbackMode'
      record.trafficMode = {
        'version': 1, 'sessionId': self.slc_producer_session, 'sequence': self.switchback_event_sequence,
        'observedMonoTime': observed_ns, 'driveStartMonoTime': drive_id,
        'settingsFingerprint': fingerprint, 'buttonMapFingerprint': map_fingerprint,
        'sourceEpoch': source_epoch, 'sourceBootTime': source_boot_ns,
        'sourceCarStateMonoTime': int(cs_send.logMonoTime),
        'validUntilMonoTime': int(cs_send.logMonoTime) + 100_000_000,
        'toggle': toggle, 'button': gesture.button.value if gesture is not None else 'unknown',
        'press': gesture.press.value if gesture is not None else 'unknown',
      }
      self.pm.send('slcCruiseEvent', event)

  def ioniq6_post_handoff_sources_current(self):
    """A parser's invalid grace is not proof of post-transaction warmup."""
    from opendbc.can.parser import MAX_BAD_COUNTER
    floor = getattr(self, 'ioniq6_handoff_source_floor_ns', 0)
    if floor <= 0:
      return False
    now = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    sources = [source for parser in self.CI.can_parsers.values()
               for source in parser.message_states.values() if not source.ignore_alive]
    return bool(sources) and all(
      source.timestamps and floor < source.timestamps[-1] <= now and
      now - source.timestamps[-1] <= source.timeout_threshold and source.counter_fail < MAX_BAD_COUNTER
      for source in sources)

  def observe_ioniq6_long_authority(self, can_list, CS):
    if getattr(self, 'ioniq6_long_selected', False) and self.ioniq6_long_lost:
      CS.canValid = False
      return
    if not self.ioniq6_long_prearmed:
      return
    CAN = self.CI.CC.CAN
    if any((src == CAN.ECAN and address in (0x1A0, 0x1BA, 0x1E5)) or (src == CAN.ACAN and address == 0x100)
           for _, packet in can_list for address, _, src in packet):
      self.ioniq6_long_lost = True
    if self.ioniq6_host_warmed:
      if not CS.canValid:
        self.ioniq6_long_lost = True
    elif CS.canValid and self.ioniq6_post_handoff_sources_current():
      self.ioniq6_host_warmed = True
    if self.ioniq6_long_lost:
      CS.canValid = False
      if self.ioniq6_keepalive is not None:
        self.ioniq6_keepalive.stop()

  def ioniq6_startup_keepalive(self):
    return bool(getattr(self, 'ioniq6_long_prearmed', False) and not self.ioniq6_long_lost)

  def ioniq6_panda_matches(self):
    panda_states = self.sm['pandaStates']
    return bool(
      self.sm.valid['pandaStates'] and self.sm.alive['pandaStates'] and
      len(panda_states) == len(self.CP.safetyConfigs) and
      all(ps.safetyModel == config.safetyModel and ps.safetyParam == config.safetyParam and
          ps.alternativeExperience == self.CP.alternativeExperience and not ps.safetyRxChecksInvalid
          for ps, config in zip(panda_states, self.CP.safetyConfigs, strict=False))
    )

  def ioniq6_panda_diagnostic_ready(self):
    if not ioniq6_long_eligible(self.CP):
      return False
    owner = hyundai_canfd_config_index(self.CP)
    if owner is None:
      return False
    if not self.sm.valid['pandaStates'] or not self.sm.alive['pandaStates']:
      return False
    panda_states = self.sm['pandaStates']
    return bool(
      len(panda_states) == len(self.CP.safetyConfigs) and
      (owner == 0 or panda_states[owner].safetyParam == 1) and
      all(ps.safetyModel == structs.CarParams.SafetyModel.elm327 and
          ps.safetyParam in (0, 1) and not ps.controlsAllowed
          for ps in panda_states)
    )

  def update_vehicle_state_context(self, state):
    holder = getattr(self, 'vehicle_startup', None)
    if holder is None or holder.owner is None:
      return
    # Python controls and receive times use MONOTONIC. Panda/CAN producers
    # use BOOTTIME, which can diverge after suspend.
    clocks = clock_pair_ns()
    mono_ns, boot_ns = clocks if clocks is not None else (0, 0)
    sm = self.sm
    control_stamp = int(sm.logMonoTime['carControl'])
    control_receipt = int(sm.recv_time['carControl'] * 1e9)
    panda_stamp = int(sm.logMonoTime['pandaStates'])
    panda_receipt = int(sm.recv_time['pandaStates'] * 1e9)
    control_current = bool(clocks is not None and
                           sm.seen['carControl'] and sm.valid['carControl'] and sm.alive['carControl'] and
                           0 < control_stamp <= mono_ns and mono_ns - control_stamp <= 150_000_000 and
                           0 < control_receipt <= mono_ns and mono_ns - control_receipt <= 150_000_000)
    panda_current = bool(clocks is not None and
                         sm.seen['pandaStates'] and sm.valid['pandaStates'] and sm.alive['pandaStates'] and
                         0 < panda_stamp <= boot_ns and boot_ns - panda_stamp <= 300_000_000 and
                         0 < panda_receipt <= mono_ns and mono_ns - panda_receipt <= 300_000_000)
    holder.after_state(ci=self.CI, state=state, now_ns=boot_ns,
                       control_enabled=bool(sm['carControl'].enabled), control_current=control_current,
                       pandas=sm['pandaStates'], panda_log_ns=panda_stamp,
                       panda_recv_ns=panda_receipt + boot_ns - mono_ns if panda_current else 0,
                       panda_current=panda_current)

  def startup_diagnostic_admission(self, *, cp=None):
    from openpilot.starpilot.aol.panda_faults import blocking_fault_list
    self.sm.update(0)
    now, boot = time.monotonic_ns(), time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    sm = self.sm
    stamp, receipt = int(sm.logMonoTime['pandaStates']), int(sm.recv_time['pandaStates'] * 1e9)
    pandas = sm['pandaStates']
    owner = getattr(self.vehicle_startup, 'owner', None)
    ioniq_owner = None if cp is None else hyundai_canfd_config_index(cp)
    if cp is not None and (not ioniq6_long_eligible(cp) or ioniq_owner is None):
      return False
    count = (len(cp.safetyConfigs) if cp is not None else
             2 if (isinstance(owner, CapturedADRVStartup) and owner.continuity_required() and
                   hyundai_canfd_config_index(owner.cp) == 1) else 1)
    ignition_pandas = pandas
    if (count == 2 and len(pandas) == 2 and
        pandas[0].pandaType == log.PandaState.PandaType.dos and
        pandas[1].pandaType == log.PandaState.PandaType.redPanda):
      # pandad clears the internal DOS ignition line in this exact topology.
      # The external captured-ADRV owner must still observe vehicle ignition.
      ignition_pandas = (pandas[1],)
    return bool(not self.params.get_bool('IsOffroad') and not self.params.get_bool('ControlsReady') and
                sm.seen['pandaStates'] and sm.valid['pandaStates'] and sm.alive['pandaStates'] and
                0 < stamp <= boot and boot - stamp <= 300_000_000 and
                0 < receipt <= now and now - receipt <= 300_000_000 and len(pandas) == count and
                all(ps.safetyModel == structs.CarParams.SafetyModel.elm327 and
                    ps.safetyParam in ((0, 1) if ioniq_owner == 1 and index == 0 else (1,)) and
                    not ps.controlsAllowed and not ps.safetyRxChecksInvalid and
                    not blocking_fault_list(getattr(ps, 'faults', None), cp if cp is not None else getattr(owner, 'cp', None))
                    for index, ps in enumerate(pandas)) and
                all(ps.ignitionLine or ps.ignitionCan for ps in ignition_pandas))

  def startup_panda_configured(self, *, inactive_keepalive=False):
    # Inactive keepalives carry no axis authority during the first native RX health tick.
    now, boot = time.monotonic_ns(), time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    sm = self.sm
    stamp, receipt = int(sm.logMonoTime['pandaStates']), int(sm.recv_time['pandaStates'] * 1e9)
    pandas = sm['pandaStates']
    return bool(sm.seen['pandaStates'] and sm.valid['pandaStates'] and sm.alive['pandaStates'] and
                0 < stamp <= boot and boot - stamp <= 300_000_000 and
                0 < receipt <= now and now - receipt <= 300_000_000 and
                len(pandas) == len(self.CP.safetyConfigs) and
                all(ps.safetyModel == cfg.safetyModel and ps.safetyParam == cfg.safetyParam and
                    ps.alternativeExperience == self.CP.alternativeExperience and
                    (not ps.safetyRxChecksInvalid or inactive_keepalive and self.gm_startup_keepalive())
                    for ps, cfg in zip(pandas, self.CP.safetyConfigs, strict=True)))

  def _feedback_capture(self, now_ns):
    # Default OFF performs no feedback-module import, digest or witness allocation.
    try:
      source = self.sm['autoSteeringLearning'].learning.sourceReceipt
      if not source.enabled or source.manual:
        self.learning_feedback_capture = None
        return None
      from openpilot.starpilot.flm.feedback_observation import card_capture_for
      return card_capture_for(self, now_ns)
    except Exception:
      self.learning_feedback_capture = None
      return None

  def _invalidate_feedback_output(self, *, assignment=False):
    capture = getattr(self, 'learning_feedback_capture', None)
    if capture is not None:
      try:
        if assignment:
          capture.begin_assignment()
        else:
          capture.invalidate()
      except Exception:
        self.learning_feedback_capture = None

  def _clear_learning_evidence(self):
    self._invalidate_feedback_output()
    self.learning_feedback_publication = None
    self.learning_stamp_observer = None
    self.learning_applied_evidence = self.learning_previous_applied_evidence = None
    self.learning_send_mono_ns = self.learning_send_committed_mono_ns = 0
    self.learning_car_state_source_ns = 0

  def record_learning_send_stamp(self, observed_ns):
    # Metadata only; no permission, coordinate conversion, or extra clock sample.
    self.learning_send_mono_ns = int(observed_ns)

  def _observe_learning_send_stamp(self, observed_ns):
    try:
      observer = getattr(self, 'learning_stamp_observer', None)
      if observer is not None:
        observer(observed_ns)
    except Exception:
      self._clear_learning_evidence()

  def publish_sendcan(self, frames, valid=True):
    if valid:
      frames = order_controller_can(getattr(self, 'CI', None), frames)
    steering_authority = getattr(getattr(getattr(self, 'CI', None), 'CS', None), 'steering_authority', None)
    monitored_steering = (getattr(getattr(self, 'CP', None), 'brand', None) == 'gm' and
                          steering_authority is not None and steering_authority.enabled)
    observer=getattr(self,'learning_stamp_observer',None)
    self.learning_send_mono_ns=self.learning_send_committed_mono_ns=0
    if observer is None:
      packet = can_list_to_can_capnp(frames, msgtype='sendcan', valid=valid)
    else:
      packet = can_list_to_can_capnp(frames, msgtype='sendcan', valid=valid,source_observer=self._observe_learning_send_stamp)
    if getattr(self, 'volt_cc_selected', False):
      # CAN producer/parser and pandad send age use BOOTTIME; Python controls/device envelopes use MONOTONIC.
      builder = messaging.log_from_bytes(packet).as_builder()
      builder.logMonoTime = int(getattr(self, 'volt_cc_now_boot_ns', 0))
      packet = builder.to_bytes()
    elif self.volt_startup_keepalive() or monitored_steering or (self.gm_startup_keepalive() and not self.initialized_prev):
      builder = messaging.log_from_bytes(packet).as_builder()
      builder.logMonoTime = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
      packet = builder.to_bytes()
    if self.vehicle_startup.owner is not None:
      with self.vehicle_startup.send_lock:
        self.pm.send('sendcan', packet)
      # Successful send acknowledgment is outside the sender lock, before any join.
      self.vehicle_startup.sent(frames, valid=valid)
    elif self.ioniq6_long_prearmed and self.ioniq6_keepalive is not None:
      # The diagnostic sender and the normal controller share one PubSocket.
      with self.ioniq6_keepalive.lock:
        self.pm.send('sendcan', packet)
    else:
      self.pm.send('sendcan', packet)
    self.learning_send_committed_mono_ns=self.learning_send_mono_ns

  def volt_cc_control_current(self):
    pair = clock_pair_ns()
    if pair is None or REPLAY:
      self.volt_cc_boot_offset_ns = None
      return False
    now_ns, boot_ns = pair
    offset = boot_ns - now_ns
    if self.volt_cc_boot_offset_ns is None or abs(offset - self.volt_cc_boot_offset_ns) > CLOCK_PAIR_MAX_SKEW_NS:
      self.volt_cc_boot_offset_ns = offset
      self.volt_cc_source_floor_ns = now_ns
      return False
    sm = self.sm
    if not all(sm.seen[name] and sm.alive[name] and sm.valid[name] for name in ('carControl', 'deviceState')):
      return False
    drive_id = int(sm['deviceState'].startedMonoTime)
    if not sm['deviceState'].started or drive_id <= 0:
      return False
    for name, limit in (('carControl', 150_000_000), ('deviceState', 2_000_000_000)):
      source_ns, receipt_ns = int(sm.logMonoTime[name]), int(sm.recv_time[name] * 1e9)
      if (not max(drive_id, self.volt_cc_source_floor_ns) < source_ns <= receipt_ns <= now_ns or
          now_ns - source_ns > limit or now_ns - receipt_ns > limit):
        return False
    self.volt_cc_drive_id = drive_id
    self.volt_cc_now_boot_ns = boot_ns
    self.volt_cc_now_mono_ns = now_ns
    return True

  def gm_startup_keepalive(self):
    # Every GM EPS needs a continuous neutral stream after the camera relay opens,
    # including while the host is still initializing and the vehicle is in reverse.
    return (getattr(getattr(self, 'CP', None), 'brand', None) == 'gm' and not self.CP.passive)

  def volt_sdgm_startup_keepalive(self):
    if getattr(getattr(self, 'CP', None), 'brand', None) != 'gm':
      return False
    return (is_volt_sdgm_profile(self.CP, longitudinal=True) and self.CP.safetyConfigs[0].safetyParam == 0x5007)

  def volt_startup_keepalive(self):
    if getattr(getattr(self, 'CP', None), 'brand', None) != 'gm':
      return False
    return (self.volt_sdgm_startup_keepalive() or
            self.CP.alternativeExperience == 32 and not self.CP.flags & GMFlags.PEDAL_LONG and
            (is_volt_ascm_longitudinal(self.CP) or
             self.CP.carFingerprint == GM_CAR.CHEVROLET_VOLT_ASCM and self.CP.pcmCruise and
             not self.CP.openpilotLongitudinalControl and gm_lane_centering_supported(self.CP)))

  def ev6_startup_keepalive(self):
    from opendbc.car.hyundai.ev6_startup import EV6Startup, required
    from opendbc.car.hyundai.ecu_startup import Outcome
    owner = getattr(getattr(self, 'vehicle_startup', None), 'owner', None)
    return (isinstance(owner, EV6Startup) and owner.outcome in (Outcome.PENDING, Outcome.SENT_UNCONFIRMED) and
            owner.ready and owner.published and owner.ci is self.CI and not owner.closed and
            required(self.CP))

  def classic_startup_keepalive(self):
    from opendbc.car.hyundai.g90_startup import G90Startup
    from opendbc.car.hyundai.classic_long_aol import qualified
    from opendbc.car.hyundai.legacy_long_aol import qualified as legacy_qualified
    from opendbc.car.hyundai.ecu_startup import Outcome
    owner = getattr(getattr(self, 'vehicle_startup', None), 'owner', None)
    return (isinstance(owner, G90Startup) and owner.outcome in (Outcome.PENDING, Outcome.SENT_UNCONFIRMED) and
            owner.ready and owner.published and owner.ci is self.CI and not owner.closed and
            (qualified(self.CP) or legacy_qualified(self.CP)))

  def torque_ev_startup_keepalive(self):
    from opendbc.car.hyundai.torque_ev_startup import TorqueEVStartup, required
    from opendbc.car.hyundai.ecu_startup import Outcome
    owner = getattr(getattr(self, 'vehicle_startup', None), 'owner', None)
    from opendbc.car.hyundai.generic_hda2_startup import GenericHDA2Startup, carnival_long
    return (isinstance(owner, (TorqueEVStartup, GenericHDA2Startup)) and owner.outcome in (Outcome.PENDING, Outcome.SENT_UNCONFIRMED) and
            owner.ready and owner.published and owner.ci is self.CI and not owner.closed and
            (required(self.CP) or (isinstance(owner, GenericHDA2Startup) and carnival_long(self.CP))))

  def mixed_startup_keepalive(self):
    from opendbc.car.hyundai.blended_longitudinal import BlendedStartup
    owner = getattr(getattr(self, 'vehicle_startup', None), 'owner', None)
    from opendbc.car.hyundai.ioniq5pe_startup import PEPendingStartup
    return (isinstance(owner, (BlendedStartup, PEPendingStartup)) and owner.owner.published and not owner.closed and
            (owner.pending_target or owner.owner.active()))

  def prepare_pe_restoration(self):
    from opendbc.car.hyundai.ioniq5pe_startup import PEPendingStartup
    owner = getattr(getattr(self, 'vehicle_startup', None), 'owner', None)
    if not isinstance(owner, PEPendingStartup) or not (owner.session_attempted or owner.takeover_attempted) or owner.restored:
      return True
    owner.quiesce()


    self.params.put_bool('ControlsReady', False)
    deadline = time.monotonic() + 1.
    while time.monotonic() < deadline:
      if self.startup_diagnostic_admission():
        return True
      time.sleep(.01)
    return False

  def controls_update(self, CS: car.CarState, CC: car.CarControl, *, initialize_only=False):
    """control update loop, driven by carControl"""

    if initialize_only:
      ioniq6_startup = self.ioniq6_startup_keepalive()
      ev6_startup = self.ev6_startup_keepalive()
      classic_startup = self.classic_startup_keepalive() or self.torque_ev_startup_keepalive()
      mixed_startup = self.mixed_startup_keepalive()
      now_ns = time.monotonic_ns()
      source_ns = int(self.sm.logMonoTime['carControl'])
      receipt_ns = int(self.sm.recv_time['carControl'] * 1e9)
      # A verified I6 ECU handoff needs inactive replacement traffic before the
      # first control message. A seen-but-stale producer still fails closed.
      first_neutral = ioniq6_startup and not self.sm.seen['carControl']
      if first_neutral:
        if (not CS.canValid or CS.canTimeout or not self.ioniq6_host_warmed or
            CC.enabled or CC.latActive or CC.longActive or CC.actuators.torque != 0.0 or CC.actuators.accel != 0.0):
          return
        if not self.ci_initialized:
          if not self.ioniq6_panda_diagnostic_ready():
            return
          self.params.put_bool('ControlsReady', True)
          self.ci_initialized = True
      # Disabled torque/accel are commands; inactive curvature is only telemetry.
      if not first_neutral and (not (ioniq6_startup or mixed_startup or ev6_startup or classic_startup or self.gm_startup_keepalive() or
               (self.aol_replay and native_bootstrap_supported(self.CP) and self.vehicle_startup.owner is None)) or
          not CS.canValid or CS.canTimeout or
          not (self.sm.seen['carControl'] and self.sm.valid['carControl'] and self.sm.alive['carControl']) or
          not (0 < source_ns <= now_ns and now_ns - source_ns <= 150_000_000 and
               0 < receipt_ns <= now_ns and now_ns - receipt_ns <= 150_000_000) or
          CC.enabled or CC.latActive or CC.longActive or CC.actuators.torque != 0.0 or CC.actuators.accel != 0.0):
        return
      if (ev6_startup or classic_startup) and not self.vehicle_startup.owner.prepared_for(self.CP):
        return

    if getattr(self, 'volt_cc_selected', False):
      if not self.volt_cc_control_current():
        self.publish_sendcan([], valid=False)
        return
      physical = getattr(self.CI.CS, 'volt_cc_physical', None)
      if physical is None or any(source - self.volt_cc_boot_offset_ns <= max(self.volt_cc_drive_id, self.volt_cc_source_floor_ns)
                                 for source in (physical.observed_ns, *physical.source_ns)):
        self.publish_sendcan([], valid=False)
        return
      if physical.button_credit_ns - self.volt_cc_boot_offset_ns <= max(self.volt_cc_drive_id, self.volt_cc_source_floor_ns):
        self.CI.CC.volt_cc_consumed_source_ns = physical.button_credit_ns
      self.CI.CC.volt_cc_metric = self.is_metric
      self.CI.CC.volt_cc_speed_ceiling_mps = (slc_physical.active_ceiling(
        self.sm, self.volt_cc_now_mono_ns,
        after_ns=max(self.volt_cc_drive_id, self.volt_cc_source_floor_ns, getattr(self, 'slc_source_floor_ns', 0)))
        if getattr(self, 'slc_control_enabled', False) else None)

    if getattr(self.CI.CC, "bolt_cc_profile", False):
      self.CI.CC.bolt_cc_metric = self.is_metric

    from opendbc.car.hyundai.generic_hda2_startup import GenericHDA2Startup
    from opendbc.car.hyundai.ecu_startup import Outcome
    pending_carnival = (isinstance(self.vehicle_startup.owner, GenericHDA2Startup) and
                        self.vehicle_startup.owner.outcome is Outcome.PENDING)
    if initialize_only and (pending_carnival or (self.mixed_startup_keepalive() and self.vehicle_startup.owner.pending_target)):
      # Fresh disabled controls alone are insufficient: finish the host's
      # selfdriveInitializing phase before suppressing factory ADAS output.
      return

    if not self.ci_initialized:
      # Keep stock ECU output intact until CAN and controls are ready.
      if (not CS.canValid or CS.canTimeout or not self.sm.valid['carControl'] or
          not self.sm.all_alive(['carControl'])):
        self.publish_sendcan([], valid=False)
        return
      mixed = self.vehicle_startup.owner if self.mixed_startup_keepalive() else None
      from opendbc.car.hyundai.g90_startup import G90Startup
      from opendbc.car.hyundai.torque_ev_startup import TorqueEVStartup
      from opendbc.car.hyundai.ev6_startup import EV6Startup
      from opendbc.car.hyundai.ecu_startup import Outcome
      classic = self.vehicle_startup.owner
      if isinstance(classic, (G90Startup, TorqueEVStartup, EV6Startup, GenericHDA2Startup)) and classic.outcome is Outcome.PENDING:
        now = time.monotonic_ns()
        source = int(self.sm.logMonoTime['carControl'])
        receipt = int(self.sm.recv_time['carControl'] * 1e9)
        current = bool(self.sm.seen['carControl'] and self.sm.alive['carControl'] and self.sm.valid['carControl'] and
                       0 < source <= now and now - source <= 150_000_000 and
                       0 < receipt <= now and now - receipt <= 150_000_000)
        inactive = not (CC.enabled or CC.latActive or CC.longActive or CC.actuators.torque != 0.0 or CC.actuators.accel != 0.0)
        sources = classic.sources_current(self.CI, time.clock_gettime_ns(time.CLOCK_BOOTTIME))
        if not current or not inactive or not sources or CS.accFaulted or CS.cruiseState.enabled or not self.startup_diagnostic_admission():
          self.publish_sendcan([], valid=False)
          return
        classic.commit_pending(self.CI, sources_current=sources, control_current=current)
      elif mixed is not None and mixed.pending_target:
        now = time.monotonic_ns()
        current = (self.sm.seen['carControl'] and self.sm.alive['carControl'] and self.sm.valid['carControl'] and
                   0 < int(self.sm.logMonoTime['carControl']) <= now and now - int(self.sm.logMonoTime['carControl']) <= 150_000_000 and
                   0 < int(self.sm.recv_time['carControl'] * 1e9) <= now and now - int(self.sm.recv_time['carControl'] * 1e9) <= 150_000_000)
        inactive = not (CC.enabled or CC.latActive or CC.longActive or CC.actuators.torque != 0.0 or CC.actuators.accel != 0.0)
        sources = mixed.sources_current(self.CI, time.clock_gettime_ns(time.CLOCK_BOOTTIME))
        if not current or not inactive or not sources:
          self.publish_sendcan([], valid=False)
          return
        mixed.commit_pending(self.CI, sources_current=sources, control_current=current)
      elif self.ioniq6_long_prearmed:
        if self.ioniq6_long_lost or not self.ioniq6_panda_diagnostic_ready():
          self.publish_sendcan([], valid=False)
          return
      elif getattr(self, 'ioniq6_long_pending', False):
        # Panda is still in its diagnostic safety mode here. Take over only
        # after the control producer is ready, then request Hyundai safety.
        if not self.ioniq6_panda_diagnostic_ready():
          self.publish_sendcan([], valid=False)
          return
        handoff_started = time.monotonic()
        if handoff_started - self.ioniq6_last_preflight_at < 0.5:
          self.publish_sendcan([], valid=False)
          return
        self.ioniq6_last_preflight_at = handoff_started
        try:
          self.ioniq6_handoff_result = confirm_ioniq6_prepared_takeover(*self.can_callbacks, ecan_bus=self.CI.CC.CAN.ECAN)
        except Exception:
          cloudlog.exception("Ioniq 6 takeover transaction failed")
          self.ioniq6_handoff_result = None
        result = self.ioniq6_handoff_result
        cloudlog.event('ioniq6_long_handoff',
                       outcome=result.outcome.name if result is not None else 'EXCEPTION',
                       reason=result.reason if result is not None else 'transaction_exception',
                       elapsed_s=round(time.monotonic() - handoff_started, 3))
        if result is not None and result.outcome is HandoffOutcome.NOT_READY:
          self.publish_sendcan([], valid=False)
          return
        self.ioniq6_long_pending = False
        if result is None or result.outcome is not HandoffOutcome.CONFIRMED:
          self.ioniq6_long_lost = True
          self.ci_initialized = True
          CS.canValid = False
          self.publish_sendcan([], valid=False)
          return
        self.ioniq6_long_prearmed = True
        # The transaction consumed Card's CAN cursor. Rewarm the host parser
        # from post-handoff independent inputs before its first controller TX.
        self.ioniq6_host_warmed = False
        self.ioniq6_handoff_source_floor_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        self.ioniq6_restore_callbacks = self.can_callbacks
      elif self.vehicle_startup.owner is None:
        self.CI.init(self.CP, *self.can_callbacks)
      # signal pandad to switch to car safety mode
      self.params.put_bool("ControlsReady", True)
      self.ci_initialized = True

    if initialize_only:
      if ioniq6_startup:
        if (not self.ioniq6_host_warmed or self.ioniq6_long_lost or
            not self.startup_panda_configured() or any(ps.faults for ps in self.sm['pandaStates'])):
          return
        self.ioniq6_panda_armed = True
        if self.ioniq6_keepalive is not None:
          self.ioniq6_keepalive.stop()
        if self.ioniq6_long_lost:
          return
        inactive = car.CarControl.new_message().as_reader()
        self._invalidate_feedback_output(assignment=True)
        self.last_actuators_output, can_sends = self.CI.apply(inactive, time.clock_gettime_ns(time.CLOCK_BOOTTIME))
        self.publish_sendcan(can_sends, valid=CS.canValid)
        return
      if (ev6_startup or classic_startup) and any(ps.faults for ps in self.sm['pandaStates']):
        return
      if mixed_startup:
        owner = self.vehicle_startup.owner
        now_boot_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        if (not owner.pending_target and self.startup_panda_configured() and
            owner.sources_current(self.CI, now_boot_ns)):
          inactive = car.CarControl.new_message().as_reader()
          self._invalidate_feedback_output(assignment=True)
          self.last_actuators_output, can_sends = self.CI.apply(inactive, now_boot_ns)
          self.publish_sendcan(can_sends, valid=CS.canValid)
        return
      if (((isinstance(self.CI, GMInterface) and self.gm_startup_keepalive()) or ev6_startup or classic_startup) and
          self.startup_panda_configured(inactive_keepalive=True)):
        inactive = car.CarControl.new_message().as_reader()
        now_boot_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        if ev6_startup or classic_startup:
          now_mono_ns = time.monotonic_ns()
          source_ns = int(self.sm.logMonoTime['carControl'])
          receipt_ns = int(self.sm.recv_time['carControl'] * 1e9)
          current = bool(0 < source_ns <= now_mono_ns and now_mono_ns - source_ns <= 150_000_000 and
                         0 < receipt_ns <= now_mono_ns and now_mono_ns - receipt_ns <= 150_000_000)
          if not self.vehicle_startup.before_control(configured=True, ci=self.CI, now_ns=now_boot_ns, control_current=current):
            self.publish_sendcan([], valid=False)
            return
          self._invalidate_feedback_output(assignment=True)
          self.last_actuators_output, can_sends = self.CI.apply(inactive, now_boot_ns)
        else:
          self._invalidate_feedback_output(assignment=True)
          self.last_actuators_output, can_sends = cast(GMInterface, self.CI).apply(inactive, now_boot_ns, startup_keepalive=True)
        self.publish_sendcan(can_sends, valid=CS.canValid)
      return

    self.vehicle_startup.check()
    now_ns = time.monotonic_ns()
    control_current = bool(self.sm.seen['carControl'] and self.sm.alive['carControl'] and self.sm.valid['carControl'] and
                           0 < int(self.sm.logMonoTime['carControl']) <= now_ns and
                           now_ns - int(self.sm.logMonoTime['carControl']) <= 150_000_000 and
                           0 < int(self.sm.recv_time['carControl'] * 1e9) <= now_ns and
                           now_ns - int(self.sm.recv_time['carControl'] * 1e9) <= 150_000_000)
    if self.vehicle_startup.owner is not None and not self.vehicle_startup.before_control(
        configured=self.startup_panda_configured(), ci=self.CI,
        now_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME), control_current=control_current):
      self.publish_sendcan([], valid=False)
      return

    if getattr(self, 'ioniq6_long_selected', False) and not self.ioniq6_long_prearmed:
      CS.canValid = False
      self.publish_sendcan([], valid=False)
      return

    if self.ioniq6_long_prearmed:
      panda_matches = self.ioniq6_panda_matches()
      if not panda_matches and self.ioniq6_panda_armed:
        self.ioniq6_long_lost = True
      self.ioniq6_panda_armed |= panda_matches
      if not panda_matches or not self.ioniq6_host_warmed or self.ioniq6_long_lost or not CS.canValid:
        self.publish_sendcan([], valid=False)
        return

    if self.sm.valid['carControl'] and self.sm.all_alive(['carControl']):
      if self.mixed_startup_keepalive() and not self.vehicle_startup.owner.handed_off:
        # The first admitted replacement cycle is always neutral; the actual
        # send receipt, not reaching apply(), transfers diagnostic continuity.
        CC = car.CarControl.new_message().as_reader()
      if self.ioniq6_long_prearmed and self.ioniq6_keepalive is not None:
        self.ioniq6_keepalive.stop()
        if self.ioniq6_long_lost:
          self.publish_sendcan([], valid=False)
          return
      steering_authority = getattr(self.CI.CS, 'steering_authority', None)
      monitored_steering = self.CP.brand == 'gm' and steering_authority is not None and steering_authority.enabled
      # send car controls over can
      now_nanos = (self.volt_cc_now_boot_ns if getattr(self, 'volt_cc_selected', False) else
                   self.can_log_mono_time if REPLAY else
                   time.clock_gettime_ns(time.CLOCK_BOOTTIME) if self.volt_startup_keepalive() or monitored_steering else
                   int(time.monotonic() * 1e9))
      learning_snapshot = None
      try:
        from openpilot.starpilot.flm.evidence_hooks import begin_applied_frame, applied_snapshot
        begin_applied_frame(self)
        learning_snapshot = applied_snapshot
      except Exception:
        self._clear_learning_evidence()
      self._invalidate_feedback_output(assignment=True)
      if self.CP.brand == "subaru":
        from openpilot.starpilot.car.subaru.software_cruise_caller import update as update_subaru_software_cruise
        update_subaru_software_cruise(self, CS, CC, now_nanos)
        from openpilot.starpilot.car.subaru.sng_caller import update as update_subaru_sng
        update_subaru_sng(self, CS, CC, now_nanos)
        from openpilot.starpilot.car.subaru.stop_start_caller import update as update_subaru_startup
        update_subaru_startup(self, now_nanos)
      self.last_actuators_output, can_sends = self.CI.apply(CC, now_nanos)
      self.publish_sendcan(can_sends, valid=CS.canValid)
      if learning_snapshot is not None and self.learning_send_committed_mono_ns:
        try:
          self.learning_applied_evidence = learning_snapshot(self, CC, CS, self.last_actuators_output,
            apply_ns=self.learning_send_committed_mono_ns, state_ns=self.learning_car_state_source_ns)
          capture = getattr(self, 'learning_feedback_capture', None)
          if capture is not None:
            try:
              capture.committed(self.last_actuators_output, self.learning_applied_evidence)
            except Exception:
              self.learning_feedback_capture = None  # Keep the original applied evidence.
        except Exception:
          self._clear_learning_evidence()

      self.CC_prev = CC

  def step(self):
    CS, RD = self.state_update(defer_radar=True)

    if self.vehicle_startup.owner is not None:
      self.vehicle_startup.maintain(configured=self.startup_panda_configured())
    self.state_publish(CS, RD)

    if (self.ioniq6_startup_keepalive() and not self.ci_initialized and
        self.startup_diagnostic_admission(cp=self.CP)):
      self.params.put_bool('ControlsReady', True, block=True)
      self.ci_initialized = True

    initialized = (not any(e.name == EventName.selfdriveInitializing for e in self.sm['onroadEvents']) and
                   self.sm.seen['onroadEvents'])
    if not self.CP.passive and initialized:
      self.controls_update(CS, self.sm['carControl'])
    elif self.ioniq6_startup_keepalive():
      self.controls_update(CS, self.sm['carControl'], initialize_only=True)
    elif ((not self.ci_initialized or self.ioniq6_startup_keepalive() or self.gm_startup_keepalive() or self.ev6_startup_keepalive() or
           self.classic_startup_keepalive() or self.torque_ev_startup_keepalive() or self.mixed_startup_keepalive()) and
          self.sm.seen['onroadEvents'] and
          any(e.name == EventName.selfdriveInitializing for e in self.sm['onroadEvents']) and
          (self.ioniq6_startup_keepalive() or self.gm_startup_keepalive() or
           (self.aol_replay and native_bootstrap_supported(self.CP)) or self.ev6_startup_keepalive() or
           self.classic_startup_keepalive() or self.torque_ev_startup_keepalive() or self.mixed_startup_keepalive())):
      self.controls_update(CS, self.sm['carControl'], initialize_only=True)

    radar_can = self._deferred_radar_can
    self._deferred_radar_can = None
    self.radar_publish(self.radar_update(radar_can))
    self.initialized_prev = initialized
    self.CS_prev = CS

  def configure_conditional_settings_worker(self):
    self.conditional_settings_worker = None
    if not REPLAY and (getattr(self, 'manual_settings_owner', None) is not None or
                       getattr(self, 'switchback_settings_owner', None) is not None):
      worker = CardSettingsWorker(self.params)
      self.conditional_settings_worker = worker
      if getattr(self, 'manual_settings_owner', None) is not None:
        self.manual_settings_owner = worker.view
      if getattr(self, 'switchback_settings_owner', None) is not None:
        self.switchback_settings_owner = worker.view

  def params_thread(self, evt):
    next_cruise_read = 0.0
    while not evt.is_set():
      settings_worker = getattr(self, 'conditional_settings_worker', None)
      if settings_worker is not None:
        settings_worker.refresh(time.monotonic_ns())
      self.is_metric = self.params.get_bool("IsMetric")
      self.experimental_mode = self.params.get_bool("ExperimentalMode") and self.CP.openpilotLongitudinalControl
      self.initial_cruise_speed = self.params.get("InitialCruiseSpeedKph", return_default=True)
      if self.aol_card_intent is not None:
        self.aol_card_intent.settings = read_settings(self.params)
      now = time.monotonic()
      if now >= next_cruise_read:
        self.slc_requested_configuration = self.read_slc_configuration()
        if getattr(self, 'slc_transport_available', False):
          self.slc_match_on_engage = self.read_slc_match_on_engage()
        self.v_cruise_helper.intervals = read_cruise_intervals(self.params, self.CP)
        for name in ('bluepilot_inputs', 'hands_free_cluster_input'):
          source = getattr(self.CI.CC, name, None)
          if source is not None:
            source.refresh_settings()
        pitch_input = getattr(self.CI.CC, 'long_pitch_input', None)
        if pitch_input is not None:
          self.CI.CC.long_pitch = pitch_input.update(time.monotonic_ns())
        next_cruise_read = now + 1.0
      time.sleep(0.1)

  def card_thread(self):
    self.configure_conditional_settings_worker()
    e = threading.Event()
    t = threading.Thread(target=self.params_thread, args=(e, ))
    try:
      t.start()
      while True:
        self.vehicle_startup.check()
        self.step()
        self.rk.monitor_time()
    finally:
      e.set()
      t.join()
      if self.ioniq6_keepalive is not None:
        self.ioniq6_long_lost = True
        self.ioniq6_keepalive.stop()


def main():
  config_realtime_process(4, Priority.CTRL_HIGH)
  # Retain the partially initialized object so its diagnostic worker can be
  # stopped even if construction fails after a confirmed ECU handoff.
  car = Car.__new__(Car)
  try:
    Car.__init__(car)
    car.card_thread()
  finally:
    if getattr(car, 'vehicle_startup', None) is not None:
      try:
        car.prepare_pe_restoration()
      finally:
        car.vehicle_startup.close()
    if getattr(car, 'ioniq6_keepalive', None) is not None:
      car.ioniq6_long_lost = True
      car.ioniq6_keepalive.stop()
    if getattr(car, 'ioniq6_restore_callbacks', None) is not None and not getattr(car, 'ioniq6_restore_attempted', False):
      # This Card is no longer an output owner, even if it had published a
      # qualified CP. Restore stock ECU traffic before a later Card starts.
      # Do not mutate the already published CP or claim stock on failure.
      callbacks = getattr(car, 'ioniq6_restore_callbacks', None)
      if callbacks is not None:
        car.ioniq6_restore_attempted = True
        try:
          restore_bus = getattr(car, 'ioniq6_restore_bus', None)
          if restore_bus is None:
            restore_bus = car.CI.CC.CAN.ECAN
          restored = restore_ioniq6_adas(*callbacks, ecan_bus=restore_bus)
        except Exception:
          restored = False
        if not restored:
          cloudlog.error("Ioniq 6 Card exited; stock SCC restoration unverified")


if __name__ == "__main__":
  main()
