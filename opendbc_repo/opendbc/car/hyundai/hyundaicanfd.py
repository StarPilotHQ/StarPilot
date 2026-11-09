import math
from binascii import crc_hqx
from typing import cast

import numpy as np
from opendbc.car import CanBusBase, CanData
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.hyundai.ioniq6_bsm import BlindspotStatus, FRONT_NEUTRAL_BODY, rear_body
from opendbc.car.hyundai.values import CAR, HyundaiFlags


class CanBus(CanBusBase):
  def __init__(self, CP, fingerprint=None, lka_steering=None) -> None:
    super().__init__(CP, fingerprint)

    if lka_steering is None:
      lka_steering = CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG.value if CP is not None else False

    # On the CAN-FD platforms, the LKAS camera is on both A-CAN and E-CAN. LKA steering cars
    # have a different harness than the LFA steering variants in order to split
    # a different bus, since the steering is done by different ECUs.
    self._a, self._e = 1, 0
    if lka_steering:
      self._a, self._e = 0, 1

    self._a += self.offset
    self._e += self.offset
    self._cam = 2 + self.offset

  @property
  def ECAN(self):
    return self._e

  @property
  def ACAN(self):
    return self._a

  @property
  def CAM(self):
    return self._cam


def create_steering_messages(packer, CP, CAN, enabled, lat_active, apply_torque):
  values = {
    "LKA_OptUsmSta": 2,
    "LKA_SysIndReq": 2 if enabled else 1,
    "StrTqReqVal": apply_torque,
    "LKA_SysWrn": 0,
    "ActToiSta": 1 if lat_active else 0,
    "LKA_UsmMod": 0,  # hide LKAS settings
    "LKA_RcgSta": 0,
    "Damping_Gain": 100,  # can potentially tuned for better perf [3, 200]
  }

  ret = []
  if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
    lkas_msg = "LKAS_ALT" if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT else "LKAS"
    if CP.openpilotLongitudinalControl:
      ret.append(packer.make_can_msg("LFA", CAN.ECAN, values))
    # Dom uses separate damping values for camera LKAS and ADAS LFA torque commands.
    # Preserve the explicit Carnival/GV70 LKAS calibration in either longitudinal mode.
    lkas_values = values
    if (not CP.flags & HyundaiFlags.CANFD_ANGLE_STEERING and CP.carFingerprint not in (
        CAR.KIA_CARNIVAL_4TH_GEN, CAR.KIA_CARNIVAL_2025,
        CAR.KIA_CARNIVAL_HEV_4TH_GEN, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN)):
      lkas_values = {**values, "Damping_Gain": 0}
    ret.append(packer.make_can_msg(lkas_msg, CAN.ACAN, lkas_values))
  else:
    ret.append(packer.make_can_msg("LFA", CAN.ECAN, values))

  return ret


def _create_angle_frame(packer, name, bus, enabled, lat_active, apply_angle, reduction_gain,
                        stock_values=None, original_ccnc_ev_wire=False):
  # Frozen angle protocol stores a signed 0.1-degree request in bits 82..95
  # on LKAS/LKAS_ALT/LFA, including the variants whose current DBC names only
  # the torque fields. Keep the original status fields where they are observed.
  # The original CCNC EV active command replaces stock warning/fault/UI fields.
  # Other angle profiles retain their existing status-preservation behavior.
  source_status = {} if original_ccnc_ev_wire and lat_active else (stock_values or {})
  values = {key: value for key, value in source_status.items() if key not in ("CHECKSUM", "COUNTER")}
  values.update({
    "LKA_OptUsmSta": 0,
    "LKA_SysIndReq": 2 if enabled else 1,
    "StrTqReqVal": 0,
    "ActToiSta": 0,
    "LKA_SysWrn": 0,
    "LKA_RcgSta": 3 if lat_active else 0,
    "Damping_Gain": 100,
  })
  address, packed, _ = packer.make_can_msg(name, bus, values)
  data = bytearray(packed)
  angle = float(np.clip(apply_angle, -360.0, 360.0))
  # Match the original DBC packer's signed 0.1-degree quantization for the exact CCNC EV stock identities.
  angle_raw = (int(math.floor(angle / 0.1 + 0.5)) if original_ccnc_ev_wire else int(round(angle * 10.0))) & 0x3FFF
  data[9] = (data[9] & ~0x30) | ((2 if lat_active else 1) << 4)
  data[10] = (data[10] & 0x03) | ((angle_raw & 0x3F) << 2)
  data[11] = (angle_raw >> 6) & 0xFF
  data[12] = int(np.clip(round(reduction_gain / 0.004), 0, 250)) if lat_active else 0
  data[0:2] = hkg_can_fd_checksum(address, None, data).to_bytes(2, "little")
  return address, bytes(data), bus


def create_angle_steering_messages(packer, CP, CAN, enabled, lat_active, apply_angle, reduction_gain,
                                   stock_lkas_status=None, measured_angle=0.0):
  if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
    lkas = "LKAS_ALT" if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT else "LKAS"
    return [_create_angle_frame(packer, lkas, CAN.ACAN, enabled, lat_active, apply_angle,
                                reduction_gain, stock_lkas_status, CP.carFingerprint in (CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9))]

  if CP.flags & HyundaiFlags.SEND_LFA:
    # Camera 0xCB topology retains a neutral LFA status frame; angle actuation
    # uses the same 24-byte 0xCB payload that current DBC calls LFA_ALT.
    status = _create_angle_frame(packer, "LFA", CAN.ECAN, enabled, False, measured_angle, 0.0)
    command = packer.make_can_msg("LFA_ALT", CAN.ECAN, {
      "ADAS_ActvACISta": 0,
      "ADAS_ActvACILvl2Sta": 2 if lat_active else 1,
      "ADAS_StrAnglReqVal": apply_angle,
      "ADAS_ACIAnglTqRedcGainVal": reduction_gain if lat_active else 0.0,
      "FCA_ESA_ActvSta": 0,
      "FCA_ESA_TqBstGainVal": 0.0,
    })
    return [status, command]

  return [_create_angle_frame(packer, "LFA", CAN.ECAN, enabled, lat_active, apply_angle, reduction_gain)]


def create_suppress_lfa(packer, CAN, lfa_block_msg, lka_steering_alt):
  suppress_msg = "CAM_0x362" if lka_steering_alt else "CAM_0x2a4"
  msg_bytes = 32 if lka_steering_alt else 24

  values = {f"BYTE{i}": lfa_block_msg[f"BYTE{i}"] for i in range(3, msg_bytes) if i != 7}
  values["COUNTER"] = lfa_block_msg["COUNTER"]
  values["SET_ME_0"] = 0
  values["SET_ME_0_2"] = 0
  values["LEFT_LANE_LINE"] = 0
  values["RIGHT_LANE_LINE"] = 0
  return packer.make_can_msg(suppress_msg, CAN.ACAN, values)


def create_buttons(packer, CP, CAN, cnt, btn):
  values = {
    "COUNTER": cnt,
    "SET_ME_1": 1,
    "CRUISE_BUTTONS": btn,
  }

  bus = CAN.ECAN if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG else CAN.CAM
  return packer.make_can_msg("CRUISE_BUTTONS", bus, values)


def create_carnival_alt_resume(packer, CP, CAN, source):
  values = {name: value for name, value in source.items() if name not in ("CHECKSUM", "COUNTER")}
  values.update({"COUNTER": (int(source["COUNTER"]) + 1) % 256, "SET_ME_1": 1, "CRUISE_BUTTONS": 1})
  bus = CAN.ECAN if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG else CAN.CAM
  address, data, bus = packer.make_can_msg("CRUISE_BUTTONS_ALT", bus, values)
  data = bytearray(data)
  checksum = hkg_can_fd_checksum(address, None, data)
  data[0:2] = checksum.to_bytes(2, "little")
  return address, bytes(data), bus


def create_acc_cancel(packer, CP, CAN, cruise_info_copy):
  # CAN FD camera-based SCC requires additional signals to be preserved
  # verbatim from the previous SCC_CONTROL frame to avoid checksum or
  # state validation faults. Classic CAN SCC only validates a subset.
  if CP.flags & HyundaiFlags.CANFD_CAMERA_SCC.value:
    values = {s: cruise_info_copy[s] for s in [
      "COUNTER",
      "CHECKSUM",
      "NEW_SIGNAL_1",
      "MainMode_ACC",
      "ACCMode",
      "ZEROS_9",
      "CRUISE_STANDSTILL",
      "ZEROS_5",
      "DISTANCE_SETTING",
      "VSetDis",
    ]}
  else:
    values = {s: cruise_info_copy[s] for s in [
      "COUNTER",
      "CHECKSUM",
      "ACCMode",
      "VSetDis",
      "CRUISE_STANDSTILL",
    ]}
  values.update({
    "ACCMode": 4,
    "aReqRaw": 0.0,
    "aReqValue": 0.0,
  })
  return packer.make_can_msg("SCC_CONTROL", CAN.ECAN, values)


def create_lfahda_cluster(packer, CAN, enabled, *, lfa_icon=None):
  values = {
    "HDA_ICON": 1 if enabled else 0,
    "LFA_ICON": (2 if enabled else 0) if lfa_icon is None else lfa_icon,
  }
  return packer.make_can_msg("LFAHDA_CLUSTER", CAN.ECAN, values)


def create_acc_control(packer, CAN, enabled, accel_last, accel, stopping, gas_override, set_speed, hud_control,
                       *, direct_accel=False, main_mode_acc=1, jerk_upper=3.0, jerk_lower=None, raw_accel=None,
                       lead_distance=None, lead_rel_speed=None, lead_visible=None):
  jerk = 5
  jn = jerk / 50
  if not enabled or gas_override:
    a_val, a_raw = 0, 0
  else:
    a_raw = accel if not direct_accel or raw_accel is None else raw_accel
    a_val = accel if direct_accel else np.clip(accel, accel_last - jn, accel_last + jn)

  if lead_distance is None and lead_rel_speed is None and lead_visible is None:
    object_distance, object_relative, object_valid, object_status = 1., 0., 0, 2
  else:
    visible = bool(lead_visible)
    object_distance = float(np.clip(cast(float, lead_distance) if visible else 0., 0., 204.7))
    object_relative = float(np.clip(cast(float, lead_rel_speed) if visible else 0., -16.4, 34.7))
    object_valid = int(not visible)
    object_status = 0 if not (enabled and visible) else 1 if gas_override else 2

  values = {
    "ACCMode": 0 if not enabled else (2 if gas_override else 1),
    "MainMode_ACC": main_mode_acc,
    "StopReq": 1 if stopping else 0,
    "aReqValue": a_val,
    "aReqRaw": a_raw,
    "VSetDis": set_speed,
    "JerkLowerLimit": (jerk if enabled else 1) if jerk_lower is None else jerk_lower,
    "JerkUpperLimit": jerk_upper,

    "ACC_ObjDist": object_distance,
    "ObjValid": object_valid,
    "OBJ_STATUS": object_status,
    "SET_ME_2": 0x4,
    "SET_ME_3": 0x3,
    "SET_ME_TMP_64": 0x64,
    "DISTANCE_SETTING": hud_control.leadDistanceBars,
  }

  if lead_distance is not None or lead_rel_speed is not None or lead_visible is not None:
    values["ACC_ObjRelSpd"] = object_relative
  return packer.make_can_msg("SCC_CONTROL", CAN.ECAN, values)


def create_ioniq6_radar_heartbeat(counter: int, brake_pressed: bool, gas_pressed: bool, *, bus: int = 0) -> CanData:
  """Recorded Ioniq 6 ADAS replacement heartbeat, 24 bytes on A-CAN.

  Preserve the observed non-pedal body; this is not the 32-byte ICE message
  sharing address 0x100. Only rolling integrity and actual pedal state vary.
  """
  if type(bus) is not int or bus not in (0, 4):
    raise ValueError("reviewed Ioniq 6 A-CAN bus required")
  data = bytearray.fromhex("000000020000fcff000000000020000055ff000068000000")
  data[2] = counter & 0xff
  data[4] = int(brake_pressed)
  data[22] = int(gas_pressed)
  data[:2] = hkg_can_fd_checksum(0x100, None, data).to_bytes(2, 'little')
  return CanData(0x100, bytes(data), bus)


def create_gv60_radar_heartbeat(counter: int, brake_pressed: bool, gas_pressed: bool) -> CanData:
  """Frozen GV60 24-byte A-CAN heartbeat; CRC, counter and current pedals vary."""
  data = bytearray.fromhex("000000020000fcff000000000020000055ff000068000000")
  data[2] = counter & 0xff
  data[4] = int(brake_pressed)
  data[22] = int(gas_pressed)
  data[:2] = hkg_can_fd_checksum(0x100, None, data).to_bytes(2, 'little')
  return CanData(0x100, bytes(data), 0)


def create_ioniq6_blindspot_status(counter: int, status: BlindspotStatus, *, bus: int = 1) -> list[CanData]:
  """Paired HDA-II dashboard status from the frozen Ioniq 6 indicator layout."""
  if type(bus) is not int or bus not in (1, 5):
    raise ValueError("reviewed Ioniq 6 E-CAN bus required")
  frames = []
  for address, body in ((0x1BA, rear_body(status)), (0x1E5, FRONT_NEUTRAL_BODY)):
    data = bytearray(body)
    data[2] = counter & 0xff
    data[:2] = hkg_can_fd_checksum(address, None, data).to_bytes(2, 'little')
    frames.append(CanData(address, bytes(data), bus))
  return frames


# Source-derived CCNC camera display transform; the repository MIT notice applies.
def create_ccnc(packer, CAN, openpilot_longitudinal, enabled, hud, left_blinker, right_blinker, msg_161, msg_162, msg_1b5,
                is_metric, out, main_cruise_enabled, lfa_icon):
  if lfa_icon:
    # The host owns lateral; keep unrelated faults and obstruction warnings.
    for field in ("FAULT_LSS", "FAULT_HDA", "FAULT_DAS", "FAULT_LFA", "FAULT_DAW"):
      if msg_162.get(field) == 1:
        msg_162[field] = 0
    if msg_161.get("ALERTS_2") == 5:
      msg_161.update({"ALERTS_2": 0, "SOUNDS_2": 0})
    if msg_161.get("ALERTS_3") == 17:
      msg_161["ALERTS_3"] = 0
    if msg_161.get("ALERTS_5") in (2, 5):
      msg_161["ALERTS_5"] = 0
    msg_161["DAW_ICON"] = 0
    lane_change_speed_min = 8.9408
    any_blinker = left_blinker or right_blinker
    curvature = {i: (31 if i == -1 else 13 - abs(i + 15)) if i < 0 else 15 + i for i in range(-15, 16)}

    msg_161.update({
      "LKA_ICON": 0,
      "LFA_ICON": 2 if lfa_icon else 0,
      "CENTERLINE": 1 if lfa_icon else 0,
      "LANELINE_CURVATURE": curvature.get(max(-15, min(int(out.steeringAngleDeg / 4.5), 15)), 14) if lfa_icon and not any_blinker else 15,
      "LANELINE_LEFT": 0 if not lfa_icon else 1 if not hud.leftLaneVisible else 4 if hud.leftLaneDepart else 6 if any_blinker else 2,
      "LANELINE_RIGHT": 0 if not lfa_icon else 1 if not hud.rightLaneVisible else 4 if hud.rightLaneDepart else 6 if any_blinker else 2,
      "LCA_LEFT_ICON": 0 if not lfa_icon or out.vEgo < lane_change_speed_min else 1 if out.leftBlindspot else 2 if any_blinker else 4,
      "LCA_RIGHT_ICON": 0 if not lfa_icon or out.vEgo < lane_change_speed_min else 1 if out.rightBlindspot else 2 if any_blinker else 4,
      "LCA_LEFT_ARROW": 2 if left_blinker else 0,
      "LCA_RIGHT_ARROW": 2 if right_blinker else 0,
    })

    if any_blinker and msg_1b5 is not None:
      left_lane_raw = msg_1b5["Info_LftLnPosVal"]
      right_lane_raw = msg_1b5["Info_RtLnPosVal"]
      scale_per_m = 15 / 1.7
      left_lane = abs(int(round(15 + (left_lane_raw - 1.7) * scale_per_m)))
      right_lane = abs(int(round(15 + (right_lane_raw - 1.7) * scale_per_m)))

      if msg_1b5["Info_LftLnQualSta"] not in (2, 3):
        left_lane = 0
      if msg_1b5["Info_RtLnQualSta"] not in (2, 3):
        right_lane = 0

      if left_lane_raw == -2.0248375:
        left_lane = 30 - right_lane
      if right_lane_raw == 2.0248375:
        right_lane = 30 - left_lane

      if left_lane_raw == right_lane_raw == 0:
        left_lane = right_lane = 15
      elif left_lane_raw == 0:
        left_lane = 30 - right_lane
      elif right_lane_raw == 0:
        right_lane = 30 - left_lane

      total = left_lane + right_lane
      if total == 0:
        left_lane = right_lane = 15
      else:
        left_lane = round((left_lane / total) * 30)
        right_lane = 30 - left_lane

      msg_161["LANELINE_LEFT_POSITION"] = left_lane
      msg_161["LANELINE_RIGHT_POSITION"] = right_lane

    if hud.leftLaneDepart or hud.rightLaneDepart:
      msg_162["VIBRATE"] = 1
  else:
    # Stock steering commands are suppressed in this topology. Its active
    # lane indicators cannot describe the host's inactive lateral command.
    msg_161.update({"LKA_ICON": 0, "LFA_ICON": 0, "CENTERLINE": 0, "LANELINE_LEFT": 0, "LANELINE_RIGHT": 0,
                    "LCA_LEFT_ICON": 0, "LCA_RIGHT_ICON": 0, "LCA_LEFT_ARROW": 0, "LCA_RIGHT_ARROW": 0})

  if openpilot_longitudinal:
    cruise_speed = round(out.vCruiseCluster * (1 if is_metric else CV.KPH_TO_MPH))
    msg_161.update({
      "SETSPEED": 3 if enabled else 1,
      "SETSPEED_HUD": 0 if not main_cruise_enabled else 2 if enabled else 1,
      "SETSPEED_SPEED": 255 if not main_cruise_enabled else (40 if is_metric else 25) if cruise_speed > (145 if is_metric else 90) else cruise_speed,
      "DISTANCE": hud.leadDistanceBars,
      "DISTANCE_SPACING": 0 if not main_cruise_enabled else 1 if enabled else 3,
      "DISTANCE_LEAD": 0 if not main_cruise_enabled else 2 if enabled and hud.leadVisible else 1 if hud.leadVisible else 0,
      "DISTANCE_CAR": 0 if not main_cruise_enabled else 2 if enabled else 1,
    })
    msg_162["LEAD"] = 0 if not main_cruise_enabled else 2 if enabled else 1
    if msg_1b5 is not None:
      msg_162["LEAD_DISTANCE"] = msg_1b5["Longitudinal_Distance"]

  return [packer.make_can_msg(msg, CAN.ECAN, values) for msg, values in (("CCNC_0x161", msg_161), ("CCNC_0x162", msg_162))]


def create_spas_messages(packer, CAN, left_blink, right_blink):
  ret = []

  values = {
  }
  ret.append(packer.make_can_msg("SPAS1", CAN.ECAN, values))

  blink = 0
  if left_blink:
    blink = 3
  elif right_blink:
    blink = 4
  values = {
    "BLINKER_CONTROL": blink,
  }
  ret.append(packer.make_can_msg("SPAS2", CAN.ECAN, values))

  return ret


def create_fca_warning_light(packer, CAN, frame):
  ret = []

  if frame % 2 == 0:
    values = {
      'AEB_SETTING': 0x1,  # show AEB disabled icon
      'SET_ME_2': 0x2,
      'SET_ME_FF': 0xff,
      'SET_ME_FC': 0xfc,
      'SET_ME_9': 0x9,
    }
    ret.append(packer.make_can_msg("ADRV_0x160", CAN.ECAN, values))
  return ret


def create_adrv_messages(packer, CAN, frame, *, template=None, drive_gear=False, speed=None):
  # messages needed to car happy after disabling
  # the ADAS Driving ECU to do longitudinal control

  ret = []

  values = {
  }
  from opendbc.car.hyundai.gv70_template import GV70Template
  if template is not None:
    if type(CAN.ACAN) is not int or type(CAN.offset) is not int:
      raise ValueError('Captured ADRV requires integer CAN routing')
    local_bus = CAN.ACAN - CAN.offset
    packet = (template.frame(frame, drive_gear, local_bus, speed=speed) if isinstance(template, GV70Template) else
              template.frame(frame, drive_gear, local_bus))
    ret.append(CanData(packet.address, packet.dat, CAN.ACAN))
  else:
    ret.append(packer.make_can_msg("ADRV_0x51", CAN.ACAN, values))

  ret.extend(create_fca_warning_light(packer, CAN, frame))

  if frame % 5 == 0:
    values = {
      'SET_ME_1C': 0x1c,
      'SET_ME_FF': 0xff,
      'SET_ME_TMP_F': 0xf,
      'SET_ME_TMP_F_2': 0xf,
    }
    ret.append(packer.make_can_msg("ADRV_0x1ea", CAN.ECAN, values))

    values = {
      'SET_ME_E1': 0xe1,
      'SET_ME_3A': 0x3a,
    }
    ret.append(packer.make_can_msg("ADRV_0x200", CAN.ECAN, values))

  if frame % 20 == 0:
    values = {
      'SET_ME_15': 0x15,
    }
    ret.append(packer.make_can_msg("ADRV_0x345", CAN.ECAN, values))

  if frame % 100 == 0:
    values = {
      'SET_ME_22': 0x22,
      'SET_ME_41': 0x41,
    }
    ret.append(packer.make_can_msg("ADRV_0x1da", CAN.ECAN, values))

  return ret


def hkg_can_fd_checksum(address: int, sig, d: bytearray) -> int:
  crc = crc_hqx(d[2:] + (address & 0xFFFF).to_bytes(2, "little"), 0)
  if len(d) == 8:
    crc ^= 0x5F29
  elif len(d) == 16:
    crc ^= 0x041D
  elif len(d) == 24:
    crc ^= 0x819D
  elif len(d) == 32:
    crc ^= 0x9F5B
  return crc
