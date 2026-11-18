from opendbc.car import structs
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.g90_lead import CanLeadData, eligible as g90_lead_eligible
from opendbc.car.crc import CRC8J1850, mk_crc8_fun
from opendbc.car.hyundai.values import is_blended, CAR, HyundaiFlags

hyundai_checksum = mk_crc8_fun(CRC8J1850, init_crc=0xFD, xor_out=0xDF)

_LEGACY_FCA_RADAR_FW = b"\xf1\x00OSev SCC F-CUP      1.00 1.01 99110-K4000         "
_LEGACY_FCA_REQUIRED = int(HyundaiFlags.EV | HyundaiFlags.USE_FCA)
_LEGACY_FCA_EXCLUDED = int(HyundaiFlags.CANFD | HyundaiFlags.CAMERA_SCC)


def uses_legacy_fca11(CP):
  if (CP.brand != 'hyundai' or CP.carFingerprint != CAR.HYUNDAI_KONA_EV or
      not CP.openpilotLongitudinalControl or CP.passive or CP.dashcamOnly or CP.notCar or
      CP.flags & _LEGACY_FCA_REQUIRED != _LEGACY_FCA_REQUIRED or CP.flags & _LEGACY_FCA_EXCLUDED or is_blended(CP)):
    return False
  versions = []
  for fw in CP.carFw:
    if fw.ecu == structs.CarParams.Ecu.fwdRadar:
      if fw.address != 0x7d0 or fw.subAddress != 0:
        return False
      version = bytes(fw.fwVersion)
      if version.startswith(b'\xf1\x00'):
        versions.append(version)
  return bool(versions) and all(version == _LEGACY_FCA_RADAR_FW for version in versions)


def _legacy_fca11_checksum(data):
  return -sum(sum(divmod(byte, 16)) for byte in data) & 0xF


def create_lkas11(packer, frame, CP, apply_torque, steer_req,
                  torque_fault, lkas11, sys_warning, sys_state, enabled,
                  left_lane, right_lane,
                  left_lane_depart, right_lane_depart, ray_lka_icon=None, aol_lka_icon=None):
  values = {s: lkas11[s] for s in [
    "CF_Lkas_LdwsActivemode",
    "CF_Lkas_LdwsSysState",
    "CF_Lkas_SysWarning",
    "CF_Lkas_LdwsLHWarning",
    "CF_Lkas_LdwsRHWarning",
    "CF_Lkas_HbaLamp",
    "CF_Lkas_FcwBasReq",
    "CF_Lkas_HbaSysState",
    "CF_Lkas_FcwOpt",
    "CF_Lkas_HbaOpt",
    "CF_Lkas_FcwSysState",
    "CF_Lkas_FcwCollisionWarning",
    "CF_Lkas_FusionState",
    "CF_Lkas_FcwOpt_USM",
    "CF_Lkas_LdwsOpt_USM",
  ]}
  values["CF_Lkas_LdwsSysState"] = sys_state
  values["CF_Lkas_SysWarning"] = 3 if sys_warning else 0
  values["CF_Lkas_LdwsLHWarning"] = left_lane_depart
  values["CF_Lkas_LdwsRHWarning"] = right_lane_depart
  values["CR_Lkas_StrToqReq"] = apply_torque
  values["CF_Lkas_ActToi"] = steer_req
  values["CF_Lkas_ToiFlt"] = torque_fault  # seems to allow actuation on CR_Lkas_StrToqReq
  values["CF_Lkas_MsgCount"] = frame % 0x10

  if CP.carFingerprint in (CAR.HYUNDAI_SONATA, CAR.HYUNDAI_PALISADE, CAR.KIA_NIRO_EV, CAR.KIA_NIRO_HEV_2021, CAR.KIA_NIRO_PHEV_2022, CAR.HYUNDAI_SANTA_FE,
                           CAR.HYUNDAI_IONIQ_EV_2020, CAR.HYUNDAI_IONIQ_PHEV, CAR.KIA_SELTOS, CAR.HYUNDAI_ELANTRA_2021, CAR.GENESIS_G70_2020,
                           CAR.HYUNDAI_ELANTRA_HEV_2021, CAR.HYUNDAI_SONATA_HYBRID, CAR.HYUNDAI_KONA_EV, CAR.HYUNDAI_KONA_HEV, CAR.HYUNDAI_KONA_EV_2022,
                           CAR.HYUNDAI_SANTA_FE_2022, CAR.KIA_K5_2021, CAR.HYUNDAI_IONIQ_HEV_2022, CAR.HYUNDAI_SANTA_FE_HEV_2022,
                           CAR.HYUNDAI_SANTA_FE_PHEV_2022, CAR.KIA_STINGER_2022, CAR.KIA_K5_HEV_2020, CAR.KIA_CEED, CAR.KIA_XCEED_PHEV, CAR.KIA_CEED_PHEV,
                           CAR.HYUNDAI_AZERA_6TH_GEN, CAR.HYUNDAI_AZERA_HEV_6TH_GEN, CAR.HYUNDAI_CUSTIN_1ST_GEN, CAR.HYUNDAI_KONA_2022,
                           CAR.HYUNDAI_ELANTRA_2024, CAR.HYUNDAI_ELANTRA_HEV_2024, CAR.KIA_RAY_EV):
    values["CF_Lkas_LdwsActivemode"] = int(left_lane) + (int(right_lane) << 1)
    values["CF_Lkas_LdwsOpt_USM"] = 2

    # FcwOpt_USM 5 = Orange blinking car + lanes
    # FcwOpt_USM 4 = Orange car + lanes
    # FcwOpt_USM 3 = Green blinking car + lanes
    # FcwOpt_USM 2 = Green car + lanes
    # FcwOpt_USM 1 = White car + lanes
    # FcwOpt_USM 0 = No car + lanes
    values["CF_Lkas_FcwOpt_USM"] = 2 if enabled else 1
    if CP.carFingerprint == CAR.KIA_RAY_EV and ray_lka_icon is not None:
      values["CF_Lkas_FcwOpt_USM"] = ray_lka_icon
    if aol_lka_icon is not None:
      values["CF_Lkas_FcwOpt_USM"] = aol_lka_icon

    # SysWarning 4 = keep hands on wheel
    # SysWarning 5 = keep hands on wheel (red)
    # SysWarning 6 = keep hands on wheel (red) + beep
    # Note: the warning is hidden while the blinkers are on
    values["CF_Lkas_SysWarning"] = 4 if sys_warning else 0

  # Likely cars lacking the ability to show individual lane lines in the dash
  elif CP.carFingerprint in (CAR.KIA_OPTIMA_G4, CAR.KIA_OPTIMA_G4_FL, CAR.HYUNDAI_KONA_NON_SCC):
    # SysWarning 4 = keep hands on wheel + beep
    values["CF_Lkas_SysWarning"] = 4 if sys_warning else 0

    # SysState 0 = no icons
    # SysState 1-2 = white car + lanes
    # SysState 3 = green car + lanes, green steering wheel
    # SysState 4 = green car + lanes
    values["CF_Lkas_LdwsSysState"] = sys_state if CP.carFingerprint == CAR.HYUNDAI_KONA_NON_SCC else 3 if enabled else 1
    values["CF_Lkas_LdwsOpt_USM"] = 2  # non-2 changes above SysState definition

    # these have no effect
    values["CF_Lkas_LdwsActivemode"] = 0
    values["CF_Lkas_FcwOpt_USM"] = 0

  elif CP.carFingerprint == CAR.HYUNDAI_GENESIS:
    # This field is actually LdwsActivemode
    # Genesis and Optima fault when forwarding while engaged
    values["CF_Lkas_LdwsActivemode"] = 2

  dat = packer.make_can_msg("LKAS11", 0, values)[1]

  if CP.flags & HyundaiFlags.CHECKSUM_CRC8:
    # CRC Checksum as seen on 2019 Hyundai Santa Fe
    dat = dat[:6] + dat[7:8]
    checksum = hyundai_checksum(dat)
  elif CP.flags & HyundaiFlags.CHECKSUM_6B:
    # Checksum of first 6 Bytes, as seen on 2018 Kia Sorento
    checksum = sum(dat[:6]) % 256
  else:
    # Checksum of first 6 Bytes and last Byte as seen on 2018 Kia Stinger
    checksum = (sum(dat[:6]) + dat[7]) % 256

  values["CF_Lkas_Chksum"] = checksum

  return packer.make_can_msg("LKAS11", 0, values)


def create_clu11(packer, frame, clu11, button, CP):
  values = {s: clu11[s] for s in [
    "CF_Clu_CruiseSwState",
    "CF_Clu_CruiseSwMain",
    "CF_Clu_SldMainSW",
    "CF_Clu_ParityBit1",
    "CF_Clu_VanzDecimal",
    "CF_Clu_Vanz",
    "CF_Clu_SPEED_UNIT",
    "CF_Clu_DetentOut",
    "CF_Clu_RheostatLevel",
    "CF_Clu_CluInfo",
    "CF_Clu_AmpInfo",
    "CF_Clu_AliveCnt1",
  ]}
  values["CF_Clu_CruiseSwState"] = button
  values["CF_Clu_AliveCnt1"] = frame % 0x10
  # send buttons to camera on camera-scc based cars
  bus = CanBus(CP).ECAN if is_blended(CP) else 2 if CP.flags & HyundaiFlags.CAMERA_SCC else 0
  return packer.make_can_msg("CLU11", bus, values)


def create_lfahda_mfc(packer, enabled, *, lfa_icon=None):
  values = {
    "LFA_Icon_State": (2 if enabled else 0) if lfa_icon is None else lfa_icon,
  }
  return packer.make_can_msg("LFAHDA_MFC", 0, values)


def create_acc_commands(packer, enabled, accel, upper_jerk, idx, hud_control, set_speed, stopping, long_override, use_fca, CP, *,
                        lead_data=None, legacy_fca11=False):
  commands = []
  if g90_lead_eligible(CP):
    lead_data = lead_data or CanLeadData()
  else:
    lead_data = None

  scc11_values = {
    "MainMode_ACC": 1,
    "TauGapSet": hud_control.leadDistanceBars,
    "VSetDis": set_speed if enabled else 0,
    "AliveCounterACC": idx % 0x10,
    "ObjValid": 1, # close lead makes controls tighter
    "ACC_ObjStatus": 1, # close lead makes controls tighter
    "ACC_ObjLatPos": 0,
    "ACC_ObjRelSpd": 0,
    "ACC_ObjDist": 1, # close lead makes controls tighter
    }
  if lead_data is not None:
    scc11_values.update(ObjValid=int(lead_data.lead_visible), ACC_ObjStatus=int(lead_data.lead_visible),
                        ACC_ObjRelSpd=lead_data.lead_rel_speed, ACC_ObjDist=int(lead_data.lead_distance))
  commands.append(packer.make_can_msg("SCC11", 0, scc11_values))

  scc12_values = {
    "ACCMode": 2 if enabled and long_override else 1 if enabled else 0,
    "StopReq": 1 if stopping else 0,
    "aReqRaw": accel,
    "aReqValue": accel,  # stock ramps up and down respecting jerk limit until it reaches aReqRaw
    "CR_VSM_Alive": idx % 0xF,
  }

  # show AEB disabled indicator on dash with SCC12 if not sending FCA messages.
  # these signals also prevent a TCS fault on non-FCA cars with alpha longitudinal
  if not use_fca:
    scc12_values["CF_VSM_ConfMode"] = 1
    scc12_values["AEB_Status"] = 1  # AEB disabled

  scc12_dat = packer.make_can_msg("SCC12", 0, scc12_values)[1]
  scc12_values["CR_VSM_ChkSum"] = 0x10 - sum(sum(divmod(i, 16)) for i in scc12_dat) % 0x10

  commands.append(packer.make_can_msg("SCC12", 0, scc12_values))

  scc14_values = {
    "ComfortBandUpper": 0.0, # stock usually is 0 but sometimes uses higher values
    "ComfortBandLower": 0.0, # stock usually is 0 but sometimes uses higher values
    "JerkUpperLimit": upper_jerk, # stock usually is 1.0 but sometimes uses higher values
    "JerkLowerLimit": 5.0, # stock usually is 0.5 but sometimes uses higher values
    "ACCMode": 2 if enabled and long_override else 1 if enabled else 4, # stock will always be 4 instead of 0 after first disengage
    "ObjGap": 2 if hud_control.leadVisible else 0, # 5: >30, m, 4: 25-30 m, 3: 20-25 m, 2: < 20 m, 0: no lead
  }
  if lead_data is not None:
    scc14_values.update(ObjGap=lead_data.object_gap, ObjDistStat=lead_data.object_rel_gap)
  commands.append(packer.make_can_msg("SCC14", 0, scc14_values))

  # Only send FCA11 on cars where it exists on the bus
  # On Camera SCC cars, FCA11 is not disabled, so we forward stock FCA11 back to the car forward hooks
  if use_fca and not (CP.flags & HyundaiFlags.CAMERA_SCC):
    # note that some vehicles most likely have an alternate checksum/counter definition
    # https://github.com/commaai/opendbc/commit/9ddcdb22c4929baf310295e832668e6e7fcfa602
    fca11_values = {
      "CR_FCA_Alive": idx % 0xF,
      "PAINT1_Status": 1,
      "FCA_DrvSetStatus": 1,
      "FCA_Status": 1,  # AEB disabled
    }
    if legacy_fca11:
      fca11_values["CR_FCA_Alive"] = 0
      fca11_values["CR_FCA_ChkSum"] = idx % 0xF
      fca11_dat = packer.make_can_msg("FCA11", 0, fca11_values)[1]
      fca11_values["CR_FCA_ChkSum"] |= _legacy_fca11_checksum(fca11_dat) << 4
    else:
      fca11_dat = packer.make_can_msg("FCA11", 0, fca11_values)[1]
      fca11_values["CR_FCA_ChkSum"] = hyundai_checksum(fca11_dat[:7])
    commands.append(packer.make_can_msg("FCA11", 0, fca11_values))

  return commands


def create_acc_opt(packer, CP):
  commands = []

  scc13_values = {
    "SCCDrvModeRValue": 2,
    "SCC_Equip": 1,
    "Lead_Veh_Dep_Alert_USM": 2,
  }
  commands.append(packer.make_can_msg("SCC13", 0, scc13_values))

  # TODO: this needs to be detected and conditionally sent on unsupported long cars
  # On Camera SCC cars, FCA12 is not disabled, so we forward stock FCA12 back to the car forward hooks
  if not (CP.flags & HyundaiFlags.CAMERA_SCC):
    fca12_values = {
      "FCA_DrvSetState": 2,
      "FCA_USM": 1, # AEB disabled
    }
    commands.append(packer.make_can_msg("FCA12", 0, fca12_values))

  return commands


def create_frt_radar_opt(packer):
  frt_radar11_values = {
    "CF_FCA_Equip_Front_Radar": 1,
  }
  return packer.make_can_msg("FRT_RADAR11", 0, frt_radar11_values)


def create_ray_lfahda_mfc(packer, lat_active, lfa_icon):
  values = {
    "HDA_USM": 2,
    "HDA_Icon_State": 2 if lfa_icon else 0,
    "HDA_VSetReq": 0,
    "HDA_Icon_Wheel": int(lat_active),
    "LFA_Icon_State": lfa_icon,
  }
  return packer.make_can_msg("LFAHDA_MFC", 0, values)


def create_checksum_can_canfd_blended(packer, bus, addr, values):
  dat = packer.make_can_msg(addr, bus, values)[1]
  return hyundai_checksum(dat[1:8])

def create_lkas11_can_canfd_blended(packer, frame, CP, apply_steer, steer_req,
                                    torque_fault, lkas11, sys_warning, sys_state, enabled,
                                    left_lane, right_lane,
                                    left_lane_depart, right_lane_depart, msg_364,
                                    include_alerts=True, counter_mod=0x10, fcw_opt_usm=None):
  bus = CanBus(CP).ECAN
  values = {
    "CF_Lkas_LdwsActivemode": int(left_lane) + (int(right_lane) << 1),
    "CF_Lkas_LdwsLHWarning": left_lane_depart,
    "CF_Lkas_LdwsRHWarning": right_lane_depart,
    "CF_Lkas_FcwOpt_USM": (2 if enabled else 1) if fcw_opt_usm is None else fcw_opt_usm,
    "CR_Lkas_StrToqReq": apply_steer,
    "CF_Lkas_ActToi": steer_req,
    "CF_Lkas_ToiFlt": torque_fault,
    "CF_Lkas_MsgCount": frame % counter_mod,
    "NEW_SIGNAL_1": 0,
    "NEW_SIGNAL_5": 100,
  }
  values["CF_Lkas_Chksum"] = create_checksum_can_canfd_blended(packer, bus, "LKAS11", values)

  alerts_364 = {k: v for k, v in msg_364.items() if k not in ("CHECKSUM", "COUNTER")} if msg_364 else {}
  alerts_364.setdefault("BYTE2", 0)
  alerts_364.setdefault("BYTE3", 0)
  alerts_364.setdefault("DAW_Status", 0)
  alerts_364["DAW_Warning"] = 0
  alerts_364.setdefault("BYTE5", 0)
  alerts_364.setdefault("BYTE6", 0)
  alerts_364.setdefault("BYTE7", 0)
  alerts_364["COUNTER"] = frame % counter_mod
  alerts_364["CHECKSUM"] = create_checksum_can_canfd_blended(packer, bus, "ALERTS_364", alerts_364)

  ret = [packer.make_can_msg("LKAS11", bus, values)]
  if include_alerts:
    ret.append(packer.make_can_msg("ALERTS_364", bus, alerts_364))
  return ret


def create_blended_steering(packer, CAN, torque, requested, icon):
  values = {"LKA_MODE": 2, "LKA_ICON": icon, "TORQUE_REQUEST": torque,
            "LKA_ASSIST": 0, "STEER_REQ": int(requested), "STEER_MODE": 0, "LKA_AVAILABLE": 0}
  return packer.make_can_msg("LKAS", CAN.ACAN, values)


def create_blended_lfahda(packer, CAN, frame, icon):
  values = {"LFA_Icon_State": icon, "COUNTER": frame % 16}
  values['CHECKSUM'] = create_checksum_can_canfd_blended(packer, CAN.ECAN, 'LFAHDA_MFC', values)
  return packer.make_can_msg('LFAHDA_MFC', CAN.ECAN, values)
