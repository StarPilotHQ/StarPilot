from opendbc.car.hyundai.left_paddle import has_left_paddle
from opendbc.car.hyundai.ioniq5pe_long_profile import qualified as pe_long_qualified
from opendbc.car.hyundai.ioniq5pe_ready import PEReadySource
from opendbc.car.hyundai.blended_stock_aol import qualified as qualified_blended_stock, qualified_alpha as qualified_blended_alpha, MixedStockLkasSources
from opendbc.car.hyundai.classic_long_aol import qualified as qualified_classic_long
from opendbc.car.hyundai.classic_scc_aol import qualified as qualified_classic_scc, ClassicSccLkasSources
from opendbc.car.hyundai.ev9_camera_lead import EV9CameraLead
from opendbc.car.hyundai.canfd_camera_lead import CANFDCameraLead
from opendbc.car.hyundai.torque_ev_scc import eligible as torque_ev_scc_eligible
from opendbc.car.hyundai.ev9_longitudinal import qualified as ev9_long_qualified
from opendbc.car.hyundai.canfd_angle_aol import qualified as angle_aol_qualified
from opendbc.car.hyundai.long_buttons import LongCruiseButtons, qualified as long_buttons_qualified
from collections import deque
import copy
import math

from opendbc.can import CANDefine, CANParser
from opendbc.car import Bus, create_button_events, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.non_scc_aol import NonSccLkasSources, qualified as qualified_non_scc
from opendbc.car.hyundai.gv70_camera_lead import GV70CameraLead, eligible as gv70_lead_eligible
from opendbc.car.hyundai.ioniq6_bsm import Ioniq6BlindspotSources
from opendbc.car.hyundai.ray_pedal import ray_pedal_enabled
from opendbc.car.hyundai.values import is_blended, HyundaiFlags, HyundaiSafetyFlags, CAR, DBC, Buttons, CarControllerParams
from opendbc.car.interfaces import CarStateBase
from opendbc.car.dashboard_speed_limit import Tracker as LimitTracker, hyundai_canfd_sign, parser_expiry

ButtonType = structs.CarState.ButtonEvent.Type

PREV_BUTTON_SAMPLES = 8
CLUSTER_SAMPLE_RATE = 20  # frames
STANDSTILL_THRESHOLD = 12 * 0.03125
IONIQ_6_BSM_MAX_AGE_NS = 200_000_000
IONIQ_6_BSM_LEFT_MASK = 0x10
IONIQ_6_BSM_RIGHT_MASK = 0x08


def ioniq_6_bsm_source_fresh(now_ns: int, source_ns: int) -> bool:
  return source_ns > 0 and 0 <= now_ns - source_ns <= IONIQ_6_BSM_MAX_AGE_NS


def decode_ioniq_6_corner_bsm(state: int) -> tuple[bool, bool]:
  return bool(state & IONIQ_6_BSM_LEFT_MASK), bool(state & IONIQ_6_BSM_RIGHT_MASK)

# Cancel button can sometimes be ACC pause/resume button, main button can also enable on some cars
ENABLE_BUTTONS = (Buttons.RES_ACCEL, Buttons.SET_DECEL, Buttons.CANCEL)
BUTTONS_DICT = {Buttons.RES_ACCEL: ButtonType.accelCruise, Buttons.SET_DECEL: ButtonType.decelCruise,
                Buttons.GAP_DIST: ButtonType.gapAdjustCruise, Buttons.CANCEL: ButtonType.cancel}


def get_non_scc_cruise_signals(flags: int, car=None) -> tuple[str, str, str, str, str, str]:
  if car == CAR.KIA_RAY_EV:
    return "LABEL11", "CC_React", "LABEL11", "CC_Engaged", "E_EMS11", "Cruise_Limit_Target"
  if flags & HyundaiFlags.EV:
    return "LABEL11", "CC_React", "EMS12", "ACC_ACT", "E_EMS11", "Cruise_Limit_Target"
  if flags & HyundaiFlags.HYBRID:
    return "E_CRUISE_CONTROL", "CRUISE_LAMP_M", "E_CRUISE_CONTROL", "CRUISE_LAMP_S", "ELECT_GEAR", "SLC_SET_SPEED"
  return "EMS16", "CRUISE_LAMP_M", "EMS16", "CRUISE_LAMP_S", "LVR12", "CF_Lvr_CruiseSet"


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.forte_lkas_sources = NonSccLkasSources() if qualified_non_scc(CP) and CP.flags & HyundaiFlags.HAS_LDA_BUTTON else None
    if (qualified_classic_scc(CP) or qualified_classic_long(CP)) and CP.flags & HyundaiFlags.HAS_LDA_BUTTON:
      self.forte_lkas_sources = ClassicSccLkasSources(CP.carFingerprint)
    if qualified_blended_stock(CP) or qualified_blended_alpha(CP):
      self.forte_lkas_sources = MixedStockLkasSources(CP)
    self.long_buttons = (LongCruiseButtons(track_main=CP.carFingerprint != CAR.HYUNDAI_IONIQ_5_PE)
                         if long_buttons_qualified(CP) else None)
    self.pe_long = pe_long_qualified(CP)
    self.pe_ready = PEReadySource() if self.pe_long or ev9_long_qualified(CP) else None
    self.ev9_long = ev9_long_qualified(CP) or self.pe_long
    self.ev9_camera_lead = (CANFDCameraLead(CP, CanBus(CP).ECAN) if self.pe_long else
                            EV9CameraLead(CP) if self.ev9_long else None)
    self.ioniq6_camera_lead = (CANFDCameraLead(CP, CanBus(CP).ECAN)
                               if CP.carFingerprint == CAR.HYUNDAI_IONIQ_6 and CP.openpilotLongitudinalControl else None)
    self.torque_ev_camera_lead = CANFDCameraLead(CP, CanBus(CP).ECAN) if torque_ev_scc_eligible(CP) else None
    self.angle_steering_angle = 0.0
    self.angle_steering_fault = False
    self.hba_icon = 0
    self.left_blindspot_from_radar = False
    self.right_blindspot_from_radar = False
    self.gv70_camera_lead = GV70CameraLead(CP) if gv70_lead_eligible(CP) else None
    can_define = CANDefine(DBC[CP.carFingerprint][Bus.pt])

    self.cruise_buttons: deque = deque([Buttons.NONE] * PREV_BUTTON_SAMPLES, maxlen=PREV_BUTTON_SAMPLES)
    self.main_buttons: deque = deque([Buttons.NONE] * PREV_BUTTON_SAMPLES, maxlen=PREV_BUTTON_SAMPLES)
    self.lda_button = 0
    self.ev6_aol_samples = ()
    self.ev6_aol_sample_stamp_ns = 0
    self.ev6_aol_timeout_ns = 0
    self.ev6_aol_source_healthy = False
    self.left_paddle = 0

    self.gear_msg_canfd = "ACCELERATOR" if CP.flags & HyundaiFlags.EV else \
                          "GEAR_ALT" if CP.flags & HyundaiFlags.CANFD_ALT_GEARS else \
                          "GEAR_ALT_2" if CP.flags & HyundaiFlags.CANFD_ALT_GEARS_2 else \
                          "GEAR_SHIFTER"
    if CP.flags & HyundaiFlags.CANFD:
      self.shifter_values = can_define.dv[self.gear_msg_canfd]["GEAR"]
    elif CP.flags & (HyundaiFlags.HYBRID | HyundaiFlags.EV):
      self.shifter_values = can_define.dv["ELECT_GEAR"]["Elect_Gear_Shifter"]
    elif self.CP.flags & HyundaiFlags.CLUSTER_GEARS:
      self.shifter_values = can_define.dv["CLU15"]["CF_Clu_Gear"]
    elif self.CP.flags & HyundaiFlags.TCU_GEARS:
      self.shifter_values = can_define.dv["TCU12"]["CUR_GR"]
    elif CP.flags & HyundaiFlags.FCEV:
      self.shifter_values = can_define.dv["EMS20"]["HYDROGEN_GEAR_SHIFTER"]
    else:
      self.shifter_values = can_define.dv["LVR12"]["CF_Lvr_Gear"]

    self.accelerator_msg_canfd = "ACCELERATOR" if CP.flags & HyundaiFlags.EV else \
                                 "ACCELERATOR_ALT" if CP.flags & HyundaiFlags.HYBRID else \
                                 "ACCELERATOR_BRAKE_ALT"
    self.cruise_btns_msg_canfd = "CRUISE_BUTTONS_ALT" if CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS else \
                                 "CRUISE_BUTTONS"
    self.is_metric = False
    self.buttons_counter = 0
    self.cruise_buttons_alt_msg = {}
    self.cruise_buttons_alt_ts_ns = 0

    self.cruise_info = {}
    self.angle_lkas_status = {}
    self.ccnc_161 = {}
    self.ccnc_162 = {}
    self.ccnc_1b5 = {}
    self.ccnc_161_ts_ns = 0
    self.ccnc_162_ts_ns = 0
    self.ccnc_1b5_ts_ns = 0
    self.dashboard_limit = LimitTracker()
    self.ioniq6_bsm_sources = (Ioniq6BlindspotSources()
                               if CP.carFingerprint == CAR.HYUNDAI_IONIQ_6 and CP.openpilotLongitudinalControl else None)
    self.ioniq6_bsm_now_ns = 0

    # On some cars, CLU15->CF_Clu_VehicleSpeed can oscillate faster than the dash updates. Sample at 5 Hz
    self.cluster_speed = 0
    self.cluster_speed_counter = CLUSTER_SAMPLE_RATE

    self.params = CarControllerParams(CP)
    self.blended_camera_current = False
    self.ray_pedal_valid = False
    self.ray_pedal_state = 5

  def recent_button_interaction(self) -> bool:
    # On some newer model years, the CANCEL button acts as a pause/resume button based on the PCM state
    # To avoid re-engaging when openpilot cancels, check user engagement intention via buttons
    # Main button also can trigger an engagement on these cars
    return any(btn in ENABLE_BUTTONS for btn in self.cruise_buttons) or any(self.main_buttons)

  def blended_cancel_sources_current(self, cp, *, cancel=True):
    if not (is_blended(self.CP) and self.CP.openpilotLongitudinalControl):
      return False
    now = cp._last_update_nanos
    for name in ('TCS13', 'EMS16', 'CLU11'):
      source = cp.message_states.get(cp.dbc.name_to_msg[name].address)
      if source is None or not source.timestamps:
        return False
      stamp = source.timestamps[-1]
      if not 0 < stamp <= now or now - stamp > source.timeout_threshold:
        return False
    signals = [('TCS13', 'ACCEnable'), ('TCS13', 'DriverOverride'), ('EMS16', 'CF_Ems_AclAct')]
    if cancel:
      signals.append(('TCS13', 'ACC_REQ'))
    return not any(cp.vl[name][signal] != 0 or any(value != 0 for value in cp.vl_all[name][signal])
                   for name, signal in signals)

  def update_button_enable(self, buttonEvents):
    if self.long_buttons is not None:
      return self.long_buttons.button_enable
    return super().update_button_enable(buttonEvents)

  def create_main_button_events(self, cur_button, prev_button):
    if self.long_buttons is not None:
      return [event for current, previous in self.long_buttons.main_transitions
              for event in create_button_events(current, previous, {1: ButtonType.mainCruise})]
    return create_button_events(cur_button, prev_button, {1: ButtonType.mainCruise})

  def create_cruise_button_events(self, cur_button, prev_button, samples=()):
    if self.long_buttons is not None:
      return [event for current, previous in self.long_buttons.cruise_transitions
              for event in create_button_events(current, previous, BUTTONS_DICT)]
    if (is_blended(self.CP) and self.CP.openpilotLongitudinalControl or
        samples and self.CP.flags & HyundaiFlags.CANFD_ANGLE_STEERING and angle_aol_qualified(self.CP, marked_only=True)):
      events = []
      for button in samples:
        events.extend(create_button_events(int(button), prev_button, BUTTONS_DICT))
        prev_button = int(button)
      return events
    return create_button_events(cur_button, prev_button, BUTTONS_DICT)

  def finalize_blended_cancel(self, ret):
    if is_blended(self.CP) and self.CP.openpilotLongitudinalControl:
      # Card's after-state owner can grant one later acknowledged enable credit.
      ret.buttonEnable = False

  def update(self, can_parsers) -> structs.CarState:
    cp = can_parsers[Bus.pt]
    cp_pedal = can_parsers.get(Bus.party)
    cp_cam = can_parsers[Bus.cam]

    if self.CP.flags & HyundaiFlags.CANFD:
      return self.update_canfd(can_parsers)

    ret = structs.CarState()
    cp_cruise = cp_cam if self.CP.flags & HyundaiFlags.CAMERA_SCC else cp
    self.is_metric = cp.vl["CLU11"]["CF_Clu_SPEED_UNIT"] == 0
    speed_conv = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS

    ret.doorOpen = any([cp.vl["CGW1"]["CF_Gway_DrvDrSw"], cp.vl["CGW1"]["CF_Gway_AstDrSw"],
                        cp.vl["CGW2"]["CF_Gway_RLDrSw"], cp.vl["CGW2"]["CF_Gway_RRDrSw"]])

    ret.seatbeltUnlatched = cp.vl["CGW1"]["CF_Gway_DrvSeatBeltSw"] == 0

    self.parse_wheel_speeds(ret,
      cp.vl["WHL_SPD11"]["WHL_SPD_FL"],
      cp.vl["WHL_SPD11"]["WHL_SPD_FR"],
      cp.vl["WHL_SPD11"]["WHL_SPD_RL"],
      cp.vl["WHL_SPD11"]["WHL_SPD_RR"],
    )
    ret.standstill = cp.vl["WHL_SPD11"]["WHL_SPD_FL"] <= STANDSTILL_THRESHOLD and cp.vl["WHL_SPD11"]["WHL_SPD_RR"] <= STANDSTILL_THRESHOLD

    self.cluster_speed_counter += 1
    if self.cluster_speed_counter > CLUSTER_SAMPLE_RATE:
      self.cluster_speed = cp.vl["CLU15"]["CF_Clu_VehicleSpeed"]
      self.cluster_speed_counter = 0

      # Mimic how dash converts to imperial.
      # Sorento is the only platform where CF_Clu_VehicleSpeed is already imperial when not is_metric
      # TODO: CGW_USM1->CF_Gway_DrLockSoundRValue may describe this
      if not self.is_metric and self.CP.carFingerprint not in (CAR.KIA_SORENTO,):
        self.cluster_speed = math.floor(self.cluster_speed * CV.KPH_TO_MPH + CV.KPH_TO_MPH)

    ret.vEgoCluster = self.cluster_speed * speed_conv

    ret.steeringAngleDeg = cp.vl["SAS11"]["SAS_Angle"]
    ret.steeringRateDeg = cp.vl["SAS11"]["SAS_Speed"]
    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_lamp(
      50, cp.vl["CGW1"]["CF_Gway_TurnSigLh"], cp.vl["CGW1"]["CF_Gway_TurnSigRh"])
    ret.steeringTorque = cp.vl["MDPS12"]["CR_Mdps_StrColTq"]
    ret.steeringTorqueEps = cp.vl["MDPS12"]["CR_Mdps_OutTq"]
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > self.params.STEER_THRESHOLD, 5)
    ret.steerFaultTemporary = cp.vl["MDPS12"]["CF_Mdps_ToiUnavail"] != 0 or cp.vl["MDPS12"]["CF_Mdps_ToiFlt"] != 0

    # cruise state
    non_scc = bool(self.CP.flags & HyundaiFlags.NON_SCC)
    if non_scc:
      available_msg, available_sig, enabled_msg, enabled_sig, speed_msg, speed_sig = get_non_scc_cruise_signals(self.CP.flags, self.CP.carFingerprint)
      ret.cruiseState.available = cp.vl[available_msg][available_sig] != 0
      ret.cruiseState.enabled = cp.vl[enabled_msg][enabled_sig] != 0
      ret.cruiseState.standstill = False
      ret.cruiseState.nonAdaptive = False
      ret.cruiseState.speed = cp.vl[speed_msg][speed_sig] * speed_conv
    elif self.CP.openpilotLongitudinalControl:
      # These are not used for engage/disengage since openpilot keeps track of state using the buttons
      ret.cruiseState.available = cp.vl["TCS13"]["ACCEnable"] == 0
      ret.cruiseState.enabled = cp.vl["TCS13"]["ACC_REQ"] == 1
      ret.cruiseState.standstill = False
      ret.cruiseState.nonAdaptive = False
    else:
      scc_status = "SCC12" if is_blended(self.CP) else "SCC11"
      ret.cruiseState.available = cp_cruise.vl[scc_status]["MainMode_ACC"] == 1
      ret.cruiseState.enabled = cp_cruise.vl["SCC12"]["ACCMode"] != 0
      ret.cruiseState.standstill = cp_cruise.vl[scc_status]["SCCInfoDisplay"] == 4.
      ret.cruiseState.nonAdaptive = cp_cruise.vl[scc_status]["SCCInfoDisplay"] == 2.  # Shows 'Cruise Control' on dash
      ret.cruiseState.speed = cp_cruise.vl[scc_status]["VSetDis"] * speed_conv

    ret.brakePressed = cp.vl["TCS13"]["DriverOverride"] == 2  # 2 includes regen braking by user on HEV/EV
    ret.brakeHoldActive = cp.vl["TCS15"]["AVH_LAMP"] == 2  # 0 OFF, 1 ERROR, 2 ACTIVE, 3 READY
    ret.parkingBrake = cp.vl["TCS13"]["PBRAKE_ACT"] == 1
    ret.espDisabled = cp.vl["TCS11"]["TCS_PAS"] == 1
    ret.espActive = cp.vl["TCS11"]["ABS_ACT"] == 1
    ret.accFaulted = False if non_scc else cp.vl["TCS13"]["ACCEnable"] != 0

    if self.CP.flags & (HyundaiFlags.HYBRID | HyundaiFlags.EV | HyundaiFlags.FCEV):
      if self.CP.flags & HyundaiFlags.FCEV:
        ret.gasPressed = cp.vl["FCEV_ACCELERATOR"]["ACCELERATOR_PEDAL"] > 0
      elif self.CP.flags & HyundaiFlags.HYBRID:
        ret.gasPressed = cp.vl["E_EMS11"]["CR_Vcu_AccPedDep_Pos"] > 0
      else:
        ret.gasPressed = cp.vl["E_EMS11"]["Accel_Pedal_Pos"] > 0
    else:
      ret.gasPressed = bool(cp.vl["EMS16"]["CF_Ems_AclAct"])

    if ray_pedal_enabled(self.CP):
      self.ray_pedal_valid = bool(cp_pedal is not None and cp_pedal.can_valid and
                                  cp_pedal.ts_nanos["GAS_SENSOR"]["STATE"] > 0)
      self.ray_pedal_state = int(cp_pedal.vl["GAS_SENSOR"]["STATE"]) if cp_pedal is not None else 5
      ret.accFaulted = not self.ray_pedal_valid or self.ray_pedal_state != 0
      if self.ray_pedal_valid:
        driver_pedal = cp_pedal.vl["GAS_SENSOR"]
        track1 = round((driver_pedal["INTERCEPTOR_GAS"] + 177.408) / 0.672)
        track2 = round((driver_pedal["INTERCEPTOR_GAS2"] + 165.004) / 0.332)
        ret.gasPressed = track1 > 272 or track2 > 513

    # Gear Selection via Cluster - For those Kia/Hyundai which are not fully discovered, we can use the Cluster Indicator for Gear Selection,
    # as this seems to be standard over all cars, but is not the preferred method.
    if self.CP.flags & (HyundaiFlags.HYBRID | HyundaiFlags.EV):
      gear = cp.vl["ELECT_GEAR"]["Elect_Gear_Shifter"]
    elif self.CP.flags & HyundaiFlags.FCEV:
      gear = cp.vl["EMS20"]["HYDROGEN_GEAR_SHIFTER"]
    elif self.CP.flags & HyundaiFlags.CLUSTER_GEARS:
      gear = cp.vl["CLU15"]["CF_Clu_Gear"]
    elif self.CP.flags & HyundaiFlags.TCU_GEARS:
      gear = cp.vl["TCU12"]["CUR_GR"]
    else:
      gear = cp.vl["LVR12"]["CF_Lvr_Gear"]

    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(gear))

    if not is_blended(self.CP) and (not self.CP.openpilotLongitudinalControl or self.CP.flags & HyundaiFlags.CAMERA_SCC):
      if non_scc:
        if not self.CP.flags & HyundaiFlags.NON_SCC_NO_FCA:
          fca_parser = cp if self.CP.flags & HyundaiFlags.NON_SCC_RADAR_FCA else cp_cam
          fca = fca_parser.vl["FCA11"]
          warning = fca["CF_VSM_Warn"] != 0
          braking = fca["CF_VSM_DecCmdAct"] != 0 or fca["FCA_CmdAct"] != 0
          ret.stockFcw = warning and not braking
          ret.stockAeb = warning and braking
      else:
        aeb_src = "FCA11" if self.CP.flags & HyundaiFlags.USE_FCA.value else "SCC12"
        aeb_sig = "FCA_CmdAct" if self.CP.flags & HyundaiFlags.USE_FCA.value else "AEB_CmdAct"
        aeb_warning = cp_cruise.vl[aeb_src]["CF_VSM_Warn"] != 0
        scc_warning = cp_cruise.vl["SCC12"]["TakeOverReq"] == 1
        aeb_braking = cp_cruise.vl[aeb_src]["CF_VSM_DecCmdAct"] != 0 or cp_cruise.vl[aeb_src][aeb_sig] != 0
        ret.stockFcw = (aeb_warning or scc_warning) and not aeb_braking
        ret.stockAeb = aeb_warning and aeb_braking

    if self.CP.deprecated.enableBsm:
      ret.leftBlindspot = cp.vl["LCA11"]["CF_Lca_IndLeft"] != 0
      ret.rightBlindspot = cp.vl["LCA11"]["CF_Lca_IndRight"] != 0

    # save the entire LKAS11 and CLU11
    if is_blended(self.CP):
      if self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
        self.lfa_block_msg = copy.copy(cp_cam.vl["CAM_0x2a4"])
        from opendbc.car.hyundai.blended_stock_aol import camera_current
        self.blended_camera_current = camera_current(cp_cam)
        self.lkas11 = {}
      else:
        self.msg_364 = copy.copy(cp_cam.vl["ALERTS_364"])
        self.lkas11 = copy.copy(cp_cam.vl["LKAS11"])
    else:
      self.lkas11 = copy.copy(cp_cam.vl["LKAS11"])
    self.clu11 = copy.copy(cp.vl["CLU11"])
    from opendbc.car.hyundai.classic_long_aol import independent_buttons_qualified
    self.ev6_aol_samples = ()
    self.ev6_aol_source_healthy = False
    if independent_buttons_qualified(self.CP):
      from opendbc.can.parser import MAX_BAD_COUNTER
      source = cp.message_states.get(cp.dbc.name_to_msg['CLU11'].address)
      self.ev6_aol_sample_stamp_ns = int(source.timestamps[-1]) if source is not None and source.timestamps else 0
      self.ev6_aol_timeout_ns = int(source.timeout_threshold) if source is not None else 0
      now = cp._last_update_nanos
      bus_timeout = cp.bus_timeout
      self.ev6_aol_source_healthy = bool(
        not bus_timeout and source is not None and source.timestamps
        and source.valid(now, bus_timeout) and source.counter_fail < MAX_BAD_COUNTER
        and 0 < self.ev6_aol_sample_stamp_ns <= now
        and now - self.ev6_aol_sample_stamp_ns <= source.timeout_threshold)
      values = cp.vl_all['CLU11']
      self.ev6_aol_samples = tuple((main, 0, cruise) for main, cruise in zip(
        values['CF_Clu_CruiseSwMain'], values['CF_Clu_CruiseSwState'], strict=True))

    if self.long_buttons is not None:
      values = cp.vl_all['CLU11']
      self.long_buttons.observe((main, False, cruise) for main, cruise in zip(
        values['CF_Clu_CruiseSwMain'], values['CF_Clu_CruiseSwState'], strict=True))
      ret.cruiseState.available = self.long_buttons.available(ret.cruiseState.available)

    self.steer_state = cp.vl["MDPS12"]["CF_Mdps_ToiActive"]  # 0 NOT ACTIVE, 1 ACTIVE
    prev_cruise_buttons = self.cruise_buttons[-1]
    prev_main_buttons = self.main_buttons[-1]
    prev_lda_button = self.lda_button
    self.cruise_buttons.extend(cp.vl_all["CLU11"]["CF_Clu_CruiseSwState"])
    self.main_buttons.extend(cp.vl_all["CLU11"]["CF_Clu_CruiseSwMain"])
    if self.forte_lkas_sources is not None and getattr(self.forte_lkas_sources, 'active', True):
      self.lda_button = int(self.forte_lkas_sources.held)
    elif self.CP.carFingerprint == CAR.HYUNDAI_ELANTRA_HEV_2024:
      # Both signals can pulse within one parser update; preserve a short press.
      lda_samples = [*cp.vl_all["CLU13"]["CF_Clu_LdwsLkasSW"], *cp.vl_all["BCM_PO_11"]["LDA_BTN"]]
      if lda_samples:
        self.lda_button = int(any(lda_samples))
    elif is_blended(self.CP):
      self.lda_button = int(any((
        cp.vl['CLU13']['CF_Clu_LdwsLkasSW'] if cp.ts_nanos['CLU13']['CF_Clu_LdwsLkasSW'] > 0 else 0,
        cp.vl['BCM_PO_11']['LDA_BTN'] if cp.ts_nanos['BCM_PO_11']['LDA_BTN'] > 0 else 0)))
    elif self.CP.flags & HyundaiFlags.HAS_LDA_BUTTON:
      self.lda_button = cp.vl["BCM_PO_11"]["LDA_BTN"]

    button_events = [*self.create_cruise_button_events(self.cruise_buttons[-1], prev_cruise_buttons,
                                                        cp.vl_all["CLU11"]["CF_Clu_CruiseSwState"]),
                        *self.create_main_button_events(self.main_buttons[-1], prev_main_buttons),
                        *create_button_events(self.lda_button, prev_lda_button, {1: ButtonType.lkas})]

    if self.forte_lkas_sources is not None and getattr(self.forte_lkas_sources, 'active', True):
      button_events = [event for event in button_events if event.type != ButtonType.lkas]
      button_events.extend(structs.CarState.ButtonEvent(type=ButtonType.lkas, pressed=pressed)
                              for pressed in self.forte_lkas_sources.edges)
      self.lda_button = int(self.forte_lkas_sources.held)
    ret.buttonEvents = button_events

    ret.blockPcmEnable = not self.recent_button_interaction()

    # low speed steer alert hysteresis logic (only for cars with steer cut off above 10 m/s)
    if ret.vEgo < (self.CP.minSteerSpeed + 2.) and self.CP.minSteerSpeed > 10.:
      self.low_speed_alert = True
    if ret.vEgo > (self.CP.minSteerSpeed + 4.):
      self.low_speed_alert = False
    ret.lowSpeedAlert = self.low_speed_alert

    return ret

  def update_canfd(self, can_parsers) -> structs.CarState:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]

    ret = structs.CarState()

    self.is_metric = cp.vl["CRUISE_BUTTONS_ALT"]["DISTANCE_UNIT"] != 1
    speed_factor = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS

    if self.CP.flags & (HyundaiFlags.EV | HyundaiFlags.HYBRID):
      ret.gasPressed = cp.vl[self.accelerator_msg_canfd]["ACCELERATOR_PEDAL"] > 1e-5
    else:
      ret.gasPressed = bool(cp.vl[self.accelerator_msg_canfd]["ACCELERATOR_PEDAL_PRESSED"])

    ret.brakePressed = cp.vl["TCS"]["DriverBraking"] == 1

    ret.doorOpen = cp.vl["DOORS_SEATBELTS"]["DRIVER_DOOR"] == 1
    ret.seatbeltUnlatched = cp.vl["DOORS_SEATBELTS"]["DRIVER_SEATBELT"] == 0

    gear = cp.vl[self.gear_msg_canfd]["GEAR"]
    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(gear))

    # TODO: figure out positions
    self.parse_wheel_speeds(ret,
      cp.vl["WHEEL_SPEEDS"]["WHL_SpdFLVal"],
      cp.vl["WHEEL_SPEEDS"]["WHL_SpdFRVal"],
      cp.vl["WHEEL_SPEEDS"]["WHL_SpdRLVal"],
      cp.vl["WHEEL_SPEEDS"]["WHL_SpdRRVal"],
    )
    ret.standstill = cp.vl["WHEEL_SPEEDS"]["WHL_SpdFLVal"] <= STANDSTILL_THRESHOLD and cp.vl["WHEEL_SPEEDS"]["WHL_SpdFRVal"] <= STANDSTILL_THRESHOLD and \
                     cp.vl["WHEEL_SPEEDS"]["WHL_SpdRLVal"] <= STANDSTILL_THRESHOLD and cp.vl["WHEEL_SPEEDS"]["WHL_SpdRRVal"] <= STANDSTILL_THRESHOLD

    ret.steeringRateDeg = cp.vl["STEERING_SENSORS"]["STEERING_RATE"]
    ret.steeringAngleDeg = cp.vl["STEERING_SENSORS"]["STEERING_ANGLE"]
    ret.steeringTorque = cp.vl["MDPS"]["MDPS_StrTqSnsrVal"]
    ret.steeringTorqueEps = cp.vl["MDPS"]["MDPS_OutTqVal"]
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > self.params.STEER_THRESHOLD, 5)
    ret.steerFaultTemporary = cp.vl["MDPS"]["MDPS_LkaFailSta"] != 0
    if self.CP.flags & HyundaiFlags.CANFD_ANGLE_STEERING:
      angle_fault = int(cp.vl["MDPS"]["MDPS_ADAS_AciFltSig_Lv2"])
      if self.ev9_long:
        self.angle_steering_angle = cp.vl["MDPS"]["MDPS_EstStrAnglVal"]
        self.angle_steering_fault = bool(angle_fault) if self.pe_long else bool(angle_fault & 2)
        ret.steerFaultTemporary |= self.angle_steering_fault
      else:
        ret.steerFaultTemporary |= bool(angle_fault if self.CP.carFingerprint in (CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9) else angle_fault & 2)

    if self.CP.flags & HyundaiFlags.CCNC and not self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
      self.ccnc_161 = copy.copy(cp_cam.vl["CCNC_0x161"])
      self.ccnc_162 = copy.copy(cp_cam.vl["CCNC_0x162"])
      self.ccnc_1b5 = copy.copy(cp_cam.vl["FR_CMR_03_50ms"])
      self.ccnc_161_ts_ns = cp_cam.ts_nanos[0x161]["COUNTER"]
      self.ccnc_162_ts_ns = cp_cam.ts_nanos[0x162]["COUNTER"]
      self.ccnc_1b5_ts_ns = cp_cam.ts_nanos[0x1b5]["Info_LftLnPosVal"]

    left_blinker_sig, right_blinker_sig = "LEFT_LAMP", "RIGHT_LAMP"
    if (self.CP.carFingerprint == CAR.HYUNDAI_KONA_EV_2ND_GEN or self.CP.flags & HyundaiFlags.CCNC or
        cp.vl["BLINKERS"]["USE_ALT_LAMP"] == 1):
      left_blinker_sig, right_blinker_sig = "LEFT_LAMP_ALT", "RIGHT_LAMP_ALT"
    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_lamp(50, cp.vl["BLINKERS"][left_blinker_sig],
                                                                      cp.vl["BLINKERS"][right_blinker_sig])
    if self.CP.deprecated.enableBsm:
      rear = cp.vl["ADAS_CMD_50_50ms"]
      ret.leftBlindspot = bool(rear["BCW_LtIndSta"])
      ret.rightBlindspot = bool(rear["BCW_RtIndSta"])
      if self.CP.carFingerprint == CAR.KIA_EV9:
        corner_left, corner_right = decode_ioniq_6_corner_bsm(int(cp.vl["BLINDSPOTS_FRONT_CORNER_2"]["SIDE_DETECT_STATE"]))
        if self.ev9_long:
          self.left_blindspot_from_radar, self.right_blindspot_from_radar = corner_left, corner_right
        ret.leftBlindspot |= corner_left
        ret.rightBlindspot |= corner_right
      if self.CP.carFingerprint == CAR.HYUNDAI_IONIQ_5_PE:
        self.left_blindspot_from_radar = self.right_blindspot_from_radar = False
        front_ts = cp.ts_nanos["BLINDSPOTS_FRONT_CORNER_2"]["CHECKSUM"]
        if ioniq_6_bsm_source_fresh(cp._last_update_nanos, front_ts):
          corner_left, corner_right = decode_ioniq_6_corner_bsm(int(cp.vl["BLINDSPOTS_FRONT_CORNER_2"]["SIDE_DETECT_STATE"]))
          if self.pe_long:
            self.left_blindspot_from_radar, self.right_blindspot_from_radar = corner_left, corner_right
          ret.leftBlindspot |= corner_left
          ret.rightBlindspot |= corner_right
      if self.CP.carFingerprint == CAR.HYUNDAI_IONIQ_6:
        now_ns = cp._last_update_nanos
        rear_ts = cp.ts_nanos["ADAS_CMD_50_50ms"]["ADAS_CMD_Crc50Val"]
        front_ts = cp.ts_nanos["BLINDSPOTS_FRONT_CORNER_2"]["CHECKSUM"]
        rear_fresh = ioniq_6_bsm_source_fresh(now_ns, rear_ts)
        front_fresh = ioniq_6_bsm_source_fresh(now_ns, front_ts)
        corner_left, corner_right = decode_ioniq_6_corner_bsm(int(cp.vl["BLINDSPOTS_FRONT_CORNER_2"]["SIDE_DETECT_STATE"]))
        ret.leftBlindspot = (rear_fresh and ret.leftBlindspot) or (front_fresh and corner_left)
        ret.rightBlindspot = (rear_fresh and ret.rightBlindspot) or (front_fresh and corner_right)
        if self.ioniq6_bsm_sources is not None:
          # CANParser timestamps and this receive clock are both BOOTTIME.
          # CarController's now_nanos is MONOTONIC and cannot age them.
          self.ioniq6_bsm_now_ns = now_ns
          self.ioniq6_bsm_sources.observe(
            corner_source_ns=front_ts,
            corner_state=int(cp.vl["BLINDSPOTS_FRONT_CORNER_2"]["SIDE_DETECT_STATE"]),
            lamp_source_ns=cp.ts_nanos["BLINKERS"]["USE_ALT_LAMP"],
            left_lamp=bool(cp.vl["BLINKERS"][left_blinker_sig]),
            right_lamp=bool(cp.vl["BLINKERS"][right_blinker_sig]),
          )

    if self.ev9_long and cp.ts_nanos["FR_CMR_01_10ms"]["FR_CMR_Crc1Val"] > 0:
      hba = int(cp.vl["FR_CMR_01_10ms"]["HBA_IndLmpReq"])
      self.hba_icon = hba if hba in (1, 2) else 0

    # cruise state
    # CAN FD cars enable on main button press, set available if no TCS faults preventing engagement
    ret.cruiseState.available = cp.vl["TCS"]["ACCEnable"] == 0
    if self.CP.openpilotLongitudinalControl:
      # These are not used for engage/disengage since openpilot keeps track of state using the buttons
      ret.cruiseState.enabled = cp.vl["TCS"]["ACC_REQ"] == 1
      ret.cruiseState.standstill = False
    else:
      cp_cruise_info = cp_cam if self.CP.flags & HyundaiFlags.CANFD_CAMERA_SCC else cp
      ret.cruiseState.enabled = cp_cruise_info.vl["SCC_CONTROL"]["ACCMode"] in (1, 2)
      ret.cruiseState.standstill = cp_cruise_info.vl["SCC_CONTROL"]["CRUISE_STANDSTILL"] == 1
      ret.cruiseState.speed = cp_cruise_info.vl["SCC_CONTROL"]["VSetDis"] * speed_factor
      self.cruise_info = copy.copy(cp_cruise_info.vl["SCC_CONTROL"])

    # Manual Speed Limit Assist is a feature that replaces non-adaptive cruise control on EV CAN FD platforms.
    # It limits the vehicle speed, overridable by pressing the accelerator past a certain point.
    # The car will brake, but does not respect positive acceleration commands in this mode
    # TODO: find this message on ICE & HYBRID cars + cruise control signals (if exists)
    if self.CP.flags & HyundaiFlags.EV:
      ret.cruiseState.nonAdaptive = cp.vl["MANUAL_SPEED_LIMIT_ASSIST"]["MSLA_ENABLED"] == 1

    from opendbc.car.hyundai.ev6_aol import qualified as ev6_aol_qualified
    self.ev6_aol_samples = ()
    self.ev6_aol_source_healthy = False
    from opendbc.car.hyundai.canfd_stock_aol import INDEPENDENT_LONG_BUTTON_CARS, qualified_long
    from opendbc.car.hyundai.ev9_long_aol import qualified as ev9_long_aol_qualified
    from opendbc.car.hyundai.pe_long_aol import qualified as pe_long_aol_qualified
    if ev6_aol_qualified(self.CP) or ev9_long_aol_qualified(self.CP, marked_only=True) or pe_long_aol_qualified(self.CP, marked_only=True) or (
        self.CP.carFingerprint in INDEPENDENT_LONG_BUTTON_CARS and qualified_long(self.CP, marked_only=True)):
      from opendbc.can.parser import MAX_BAD_COUNTER
      source = cp.message_states.get(cp.dbc.name_to_msg[self.cruise_btns_msg_canfd].address)
      self.ev6_aol_sample_stamp_ns = int(source.timestamps[-1]) if source is not None and source.timestamps else 0
      self.ev6_aol_timeout_ns = int(source.timeout_threshold) if source is not None else 0
      now = cp._last_update_nanos
      bus_timeout = cp.bus_timeout
      self.ev6_aol_source_healthy = bool(
        not bus_timeout and source is not None and source.timestamps
        and source.valid(now, bus_timeout) and source.counter_fail < MAX_BAD_COUNTER
        and 0 < self.ev6_aol_sample_stamp_ns <= now
        and now - self.ev6_aol_sample_stamp_ns <= source.timeout_threshold)
      values = cp.vl_all[self.cruise_btns_msg_canfd]
      self.ev6_aol_samples = tuple(zip(values['ADAPTIVE_CRUISE_MAIN_BTN'], values['LDA_BTN'], values['CRUISE_BUTTONS'], strict=True))
    if self.long_buttons is not None:
      values = cp.vl_all[self.cruise_btns_msg_canfd]
      self.long_buttons.observe(zip(values['ADAPTIVE_CRUISE_MAIN_BTN'], values['NORMAL_CRUISE_MAIN_BTN'],
                                    values['CRUISE_BUTTONS'], strict=True))
      ret.cruiseState.available = self.long_buttons.available(ret.cruiseState.available)
    prev_cruise_buttons = self.cruise_buttons[-1]
    prev_main_buttons = self.main_buttons[-1]
    prev_lda_button = self.lda_button
    self.cruise_buttons.extend(cp.vl_all[self.cruise_btns_msg_canfd]["CRUISE_BUTTONS"])
    self.main_buttons.extend(cp.vl_all[self.cruise_btns_msg_canfd]["ADAPTIVE_CRUISE_MAIN_BTN"])
    self.lda_button = cp.vl[self.cruise_btns_msg_canfd]["LDA_BTN"]
    prev_left_paddle = self.left_paddle
    self.left_paddle = cp.vl["CRUISE_BUTTONS"]["LEFT_PADDLE"] if has_left_paddle(self.CP) else 0
    self.buttons_counter = cp.vl[self.cruise_btns_msg_canfd]["COUNTER"]
    if not self.CP.flags & HyundaiFlags.CANFD_ANGLE_STEERING and self.CP.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.CARNIVAL_ALT_RESUME:
      self.cruise_buttons_alt_ts_ns = cp.ts_nanos["CRUISE_BUTTONS_ALT"]["COUNTER"]
      if self.cruise_buttons_alt_ts_ns > 0:
        self.cruise_buttons_alt_msg = copy.copy(cp.vl["CRUISE_BUTTONS_ALT"])
    ret.accFaulted = cp.vl["TCS"]["ACCEnable"] != 0  # 0 ACC CONTROL ENABLED, 1-3 ACC CONTROL DISABLED

    if self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
      self.lfa_block_msg = copy.copy(cp_cam.vl["CAM_0x362"] if self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT
                                          else cp_cam.vl["CAM_0x2a4"])
      if self.CP.flags & HyundaiFlags.CANFD_ANGLE_STEERING:
        self.angle_lkas_status = copy.copy(cp_cam.vl["LKAS_ALT" if self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT else "LKAS"])

    ret.buttonEvents = [*self.create_cruise_button_events(self.cruise_buttons[-1], prev_cruise_buttons,
                                                        cp.vl_all[self.cruise_btns_msg_canfd]["CRUISE_BUTTONS"]),
                        *self.create_main_button_events(self.main_buttons[-1], prev_main_buttons),
                        *create_button_events(self.lda_button, prev_lda_button, {1: ButtonType.lkas}),
                        *create_button_events(self.left_paddle, prev_left_paddle, {1: ButtonType.altButton2})]

    ret.blockPcmEnable = not self.recent_button_interaction()

    # The front camera's posted sign is distinct from SCC set speed and from
    # the CCNC cluster's forwarded display value. Only a received packet can
    # establish valid or absent evidence; the subscription is non-gating.
    sign_bus = cp if self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG else cp_cam
    timestamp, expiry = parser_expiry(sign_bus, "FR_CMR_02_100ms", "ISLW_SpdCluMainDis")
    if timestamp > self.dashboard_limit.observation.observed_ns:
      speed = int(sign_bus.vl["FR_CMR_02_100ms"]["ISLW_SpdCluMainDis"])
      system_status = int(sign_bus.vl["FR_CMR_02_100ms"]["ISLW_SysSta"])
      status, value_mps = hyundai_canfd_sign(speed, system_status, self.is_metric)
      self.dashboard_limit.update(timestamp, status, value_mps, valid_until_ns=expiry)

    return ret

  def get_can_parsers_canfd(self, CP):
    msgs = []
    if ev9_long_qualified(CP) or pe_long_qualified(CP):
      msgs.append(("FR_CMR_01_10ms", math.nan))
    cam_msgs: list[tuple[str | int, int | float]] = []
    if CP.carFingerprint in (CAR.HYUNDAI_IONIQ_6, CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9) and CP.deprecated.enableBsm:
      # Receive-only corner sources do not poison stock SCC CAN validity.
      # The separately gated exact-long controller may use a fresh source for
      # paired dashboard status; parsing alone never authorizes TX.
      msgs.extend((("ADAS_CMD_50_50ms", math.nan), ("BLINDSPOTS_FRONT_CORNER_2", math.nan)))
    if CP.flags & HyundaiFlags.EV:
      msgs.append(("MANUAL_SPEED_LIMIT_ASSIST", math.nan))
    sign_subscription = ("FR_CMR_02_100ms", math.nan)
    if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
      msgs.append(sign_subscription)
      if CP.flags & HyundaiFlags.CANFD_ANGLE_STEERING:
        cam_msgs.append(("LKAS_ALT" if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT else "LKAS", math.nan))
        cam_msgs.append(("CAM_0x362" if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT else "CAM_0x2a4", math.nan))
    else:
      cam_msgs.append(sign_subscription)
    if CP.flags & HyundaiFlags.CCNC and not CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
      cam_msgs += [("CCNC_0x161", math.nan), ("CCNC_0x162", math.nan), ("FR_CMR_03_50ms", math.nan)]
    if not (CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS):
      # TODO: this can be removed once we add dynamic support to vl_all
      msgs += [
        # this message is 50Hz but the ECU frequently stops transmitting for ~0.5s
        ("CRUISE_BUTTONS", 50 if CP.carFingerprint == CAR.HYUNDAI_IONIQ_6 and CP.openpilotLongitudinalControl else
         math.nan if CP.carFingerprint in (CAR.HYUNDAI_IONIQ_9, CAR.KIA_EV9) else
         50 if CP.flags & HyundaiFlags.CANFD_ANGLE_STEERING else 1)
      ]
    elif not CP.flags & HyundaiFlags.CANFD_ANGLE_STEERING and CP.safetyConfigs[-1].safetyParam & HyundaiSafetyFlags.CARNIVAL_ALT_RESUME:
      msgs.append(("CRUISE_BUTTONS_ALT", math.nan))
    from opendbc.car.hyundai.canfd_stock_aol import qualified as stock_aol_qualified
    if stock_aol_qualified(CP):
      msgs = [(name, frequency) for name, frequency in msgs if name != "CRUISE_BUTTONS_ALT"]
      msgs.append(("CRUISE_BUTTONS_ALT", 50 if CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS else math.nan))
    if CP.flags & HyundaiFlags.CANFD_ANGLE_STEERING:
      # These are actual state and safety inputs, not DBC defaults. Keep this
      # subscription expansion scoped to the new angle profile.
      fuel = "ACCELERATOR" if CP.flags & HyundaiFlags.EV else \
             "ACCELERATOR_ALT" if CP.flags & HyundaiFlags.HYBRID else "ACCELERATOR_BRAKE_ALT"
      required = [(fuel, 100), ("TCS", 50), ("WHEEL_SPEEDS", 100), ("MDPS", 100),
                  ("STEERING_SENSORS", 100), ("DOORS_SEATBELTS", 10), ("BLINKERS", 10),
                  (self.gear_msg_canfd, 100)]
      existing = {name for name, _ in msgs}
      for name, frequency in required:
        if name not in existing:
          msgs.append((name, frequency))
          existing.add(name)
      if CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
        msgs.append(("CRUISE_BUTTONS_ALT", 50))
      else:
        # update_canfd reads DISTANCE_UNIT from this alternate definition even
        # on standard-button cars; prevent the absent source gating CAN-valid.
        msgs.append(("CRUISE_BUTTONS_ALT", math.nan))
      if not (ev9_long_qualified(CP) or pe_long_qualified(CP)):
        (cam_msgs if CP.flags & HyundaiFlags.CANFD_CAMERA_SCC else msgs).append(("SCC_CONTROL", 50))
    elif torque_ev_scc_eligible(CP):
      msgs.extend(((self.accelerator_msg_canfd, 100), ("TCS", 50), ("WHEEL_SPEEDS", 100), ("MDPS", 100)))
      msgs.extend((("CRUISE_BUTTONS_ALT", math.nan), ("DOORS_SEATBELTS", math.nan),
                   ("STEERING_SENSORS", math.nan), ("BLINKERS", math.nan)))
      if self.gear_msg_canfd != self.accelerator_msg_canfd:
        msgs.append((self.gear_msg_canfd, 100))
      if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
        cam_msgs.append(("CAM_0x362" if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT else "CAM_0x2a4", 20))
    elif CP.carFingerprint == CAR.HYUNDAI_IONIQ_6 and CP.openpilotLongitudinalControl:
      # The acknowledged ADAS takeover removes stock SCC from E-CAN. Require
      # every independent native input and the matching camera source instead.
      msgs.extend((("ACCELERATOR", 100), ("TCS", 50), ("WHEEL_SPEEDS", 100), ("MDPS", 100)))
      msgs.extend((("CRUISE_BUTTONS_ALT", math.nan), ("DOORS_SEATBELTS", math.nan),
                   ("STEERING_SENSORS", math.nan), ("BLINKERS", math.nan)))
      cam_msgs.append(("CAM_0x362" if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT else "CAM_0x2a4", 20))
    elif CP.carFingerprint == CAR.HYUNDAI_IONIQ_6 and not CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
      # Stock standard-button Ioniq 6 reads alternate DISTANCE_UNIT too, but
      # that frame may be absent. Subscribe before the lazy lookup makes it
      # required; existing native and CarState safety inputs remain required.
      msgs.append(("CRUISE_BUTTONS_ALT", math.nan))
    from opendbc.car.hyundai.ev6_startup import eligible as ev6_eligible
    from opendbc.car.hyundai.gv70_startup import eligible as gv70_eligible
    if ev6_eligible(CP) or gv70_eligible(CP):
      # Standard 1CF supplies buttons; alternate 1AA only holds optional units.
      if not any(name == "CRUISE_BUTTONS_ALT" for name, _ in msgs):
        msgs.append(("CRUISE_BUTTONS_ALT", math.nan))
      existing = {name for name, _ in msgs}
      for name, frequency in (("ACCELERATOR", 100), ("TCS", 50), ("WHEEL_SPEEDS", 100), ("MDPS", 100),
                              (self.gear_msg_canfd, 100)):
        if name not in existing:
          msgs.append((name, frequency))
          existing.add(name)
      if not CP.openpilotLongitudinalControl:
        msgs.append(("SCC_CONTROL", 50))
      if ("CAM_0x2a4", 20) not in cam_msgs:
        cam_msgs.append(("CAM_0x2a4", 20))
    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], msgs, CanBus(CP).ECAN),
      # Native CANParser accepts NaN to subscribe without a CAN-valid frequency gate.
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], cam_msgs, CanBus(CP).CAM),
    }

  def get_can_parsers(self, CP):
    if CP.flags & HyundaiFlags.CANFD:
      return self.get_can_parsers_canfd(CP)

    if is_blended(CP):
      msgs = [("MDPS12", 100), ("TCS11", 100), ("TCS13", 50), ("TCS15", 10),
              ("CLU11", 50), ("CLU15", 5), ("ESP12", 100), ("CGW1", 10), ("CGW2", 5),
              ("WHL_SPD11", 50), ("SAS11", 100),
              ("EMS12", 100), ("EMS16", 100), ("LVR12", 100), ("BCM_PO_11", math.nan), ("CLU13", math.nan)]
      if not CP.openpilotLongitudinalControl:
        msgs.extend((("SCC11", 50), ("SCC12", 50)))
      elif CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
        msgs.append(("SCC12", math.nan))
      if CP.deprecated.enableBsm:
        msgs.append(("LCA11", 20))
      cam_msgs = [("CAM_0x2a4", 20)] if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG else [
        ("LKAS11", 100), ("ALERTS_364", math.nan)]
      return {Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], msgs, CanBus(CP).ECAN),
              Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], cam_msgs, CanBus(CP).CAM)}

    msgs = [("CLU13", math.nan), ("BCM_PO_11", math.nan)] if CP.carFingerprint == CAR.HYUNDAI_ELANTRA_HEV_2024 else []
    cam_msgs = []
    if CP.flags & HyundaiFlags.NON_SCC:
      available_msg, _, enabled_msg, _, speed_msg, _ = get_non_scc_cruise_signals(CP.flags, CP.carFingerprint)
      msgs.extend((name, 0) for name in {available_msg, enabled_msg, speed_msg})
      if not CP.flags & HyundaiFlags.NON_SCC_NO_FCA:
        (msgs if CP.flags & HyundaiFlags.NON_SCC_RADAR_FCA else cam_msgs).append(("FCA11", math.nan))
    parsers = {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], msgs, 0),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], cam_msgs, 2),
    }

    if ray_pedal_enabled(CP):
      parsers[Bus.party] = CANParser("hyundai_kia_ray_pedal", [("GAS_SENSOR", 50)], 0)
    return parsers
