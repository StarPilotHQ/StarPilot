from opendbc.car.mazda.values import Buttons, MazdaSafetyFlags
from numpy import clip

def create_steering_control(packer, CP, frame, apply_torque, lkas, ti_apply_torque = None):
  msgs = []
  if CP.flags & MazdaSafetyFlags.GEN1:
    if not CP.flags & MazdaSafetyFlags.NO_FSC:
      tmp = apply_torque + 2048

      lo = tmp & 0xFF
      hi = tmp >> 8

      # copy values from camera
      b1 = int(lkas["BIT_1"])
      er1 = int(lkas["ERR_BIT_1"])
      lnv = 0
      ldw = 0
      er2 = int(lkas["ERR_BIT_2"])

      # Some older models do have these, newer models don't.
      # Either way, they all work just fine if set to zero.
      steering_angle = 0
      b2 = 0

      tmp = steering_angle + 2048
      ahi = tmp >> 10
      amd = (tmp & 0x3FF) >> 2
      amd = (amd >> 4) | (( amd & 0xF) << 4)
      alo = (tmp & 0x3) << 2

      ctr = frame % 16
      # bytes:     [    1  ] [ 2 ] [             3               ]  [           4         ]
      csum = 249 - ctr - hi - lo - (lnv << 3) - er1 - (ldw << 7) - ( er2 << 4) - (b1 << 5)

      # bytes      [ 5 ] [ 6 ] [    7   ]
      csum = csum - ahi - amd - alo - b2

      if ahi == 1:
        csum = csum + 15

      if csum < 0:
        if csum < -256:
          csum = csum + 512
        else:
          csum = csum + 256

      csum = csum % 256

      values = {
        "LKAS_REQUEST": apply_torque,
        "CTR": ctr,
        "ERR_BIT_1": er1,
        "LINE_NOT_VISIBLE" : lnv,
        "LDW": ldw,
        "BIT_1": b1,
        "ERR_BIT_2": er2,
        "STEERING_ANGLE": steering_angle,
        "ANGLE_ENABLED": b2,
        "CHKSUM": csum
      }
      msgs.append(packer.make_can_msg("CAM_LKAS", 0, values))

    if ti_apply_torque is not None:
      values = {
          "LKAS_REQUEST"     : ti_apply_torque,
          "CHKSUM"           : ti_apply_torque,
          "KEY"              : 3294744160
      }
      msgs.append(packer.make_can_msg("CAM_LKAS2", 1, values))

  elif CP.flags & (MazdaSafetyFlags.GEN2 | MazdaSafetyFlags.GEN3) :
    bus = 1
    sig_name = "EPS_LKAS"
    values = {
      "LKAS_REQUEST": apply_torque,
      "STEER_FEEL": 10000,
    }
    msgs.append(packer.make_can_msg(sig_name, bus, values))

  return msgs

def create_alert_command(packer, cam_msg: dict, ldw: bool, steer_required: bool):
  values = {s: cam_msg[s] for s in [
    "LINE_VISIBLE",
    "LINE_NOT_VISIBLE",
    "LANE_LINES",
    "BIT1",
    "BIT2",
    "BIT3",
    "NO_ERR_BIT",
    "S1",
    "S1_HBEAM",
  ]}
  values.update({
    # TODO: what's the difference between all these? do we need to send all?
    "HANDS_WARN_3_BITS": 0b111 if steer_required else 0,
    "HANDS_ON_STEER_WARN": steer_required,
    "HANDS_ON_STEER_WARN_2": steer_required,

    # TODO: right lane works, left doesn't
    # TODO: need to do something about L/R
    "LDW_WARN_LL": 0,
    "LDW_WARN_RL": 0,
  })
  return packer.make_can_msg("CAM_LANEINFO", 0, values)


def create_button_cmd(packer, CP, counter, button):

  can = int(button == Buttons.CANCEL)
  res = int(button == Buttons.RESUME)

  if CP.flags & MazdaSafetyFlags.GEN1:
    values = {
      "CAN_OFF": can,
      "CAN_OFF_INV": (can + 1) % 2,

      "SET_P": 0,
      "SET_P_INV": 1,

      "RES": res,
      "RES_INV": (res + 1) % 2,

      "SET_M": 0,
      "SET_M_INV": 1,

      "DISTANCE_LESS": 0,
      "DISTANCE_LESS_INV": 1,

      "DISTANCE_MORE": 0,
      "DISTANCE_MORE_INV": 1,

      "MODE_X": 0,
      "MODE_X_INV": 1,

      "MODE_Y": 0,
      "MODE_Y_INV": 1,

      "BIT1": 1,
      "BIT2": 1,
      "BIT3": 1,
      "CTR": (counter + 1) % 16,
    }

    return packer.make_can_msg("CRZ_BTNS", 0, values)

STATIC_DATA_21B = [0x01FFE000, 0x00000000]
STATIC_DATA_361 = [0xFFF7FEFE, 0x1FC]
STATIC_DATA_362 = [0xFFF7FEFE, 0x1FC]
STATIC_DATA_363 = [0xFFF7FEFE, 0x1FC0000]
STATIC_DATA_364 = [0xFFF7FEFE, 0x1FC0000]
STATIC_DATA_365 = [0xFFF7FE7F, 0xFBFF3FC]
STATIC_DATA_366 = [0xFFF7FE7F, 0xFBFF3FC]
static_data_list = [STATIC_DATA_361, STATIC_DATA_362, STATIC_DATA_363, STATIC_DATA_364, STATIC_DATA_365, STATIC_DATA_366]

# GEN1 radar interceptor
def create_radar_command(packer, frame, active, CS, hold):
  #accel = 0
  ret = []
  crz_ctrl = CS.crz_cntr
  crz_info = CS.crz_info

  # if CC.longActive: # this is set true in longcontrol.py
  #   accel = CC.actuators.accel * 1150
  #   accel = accel if accel < 1000 else 1000
  # else:
  #   accel = int(crz_info["ACCEL_CMD"])

  crz_info["ACC_ACTIVE"] = active
  crz_info["ACC_SET_ALLOWED"] = int(bool(int(CS.cp.vl["GEAR"]["GEAR"]) & 4)) # we can set ACC_SET_ALLOWED bit when in drive. Allows crz to be set from 1kmh.
  crz_info["CRZ_ENDED"] = 0 # this should keep acc on down to 5km/h on my 2018 M3
  #crz_info["ACCEL_CMD"] = accel
  crz_info["STOPPING_MAYBE"] = hold
  crz_info["STOPPING_MAYBE2"] = hold

  crz_ctrl["CRZ_ACTIVE"] = active
  crz_ctrl["ACC_ACTIVE_2"] = active
  crz_ctrl["DISABLE_TIMER_1"] = 0
  crz_ctrl["DISABLE_TIMER_2"] = 0

  ret.append(packer.make_can_msg("CRZ_INFO", 0, crz_info))
  ret.append(packer.make_can_msg("CRZ_CTRL", 0, crz_ctrl))
  # convert steering angle to radar units and clip to range
  steer_angle = (CS.out.steeringAngleDeg *-17.4) + 2048

  if (frame % 10 == 0):
    for i, addr in enumerate(range(361,367)):
      addr_name = f"RADAR_{addr}"
      msg = CS.cp_cam.vl[addr_name]
      values = {
        "MSGS_1" : static_data_list[i][0],
        "MSGS_2" : static_data_list[i][1],
        "CTR"    : int(msg["CTR"]) #frame % 16
      }
      if addr == 361:
        values.update({
          "INVERSE_SPEED" : int(CS.out.vEgo * -4.4),
          "BIT" : 1,
        })
      if addr == 362:
        values.update({
          "CLIPPED_STEER_ANGLE" : int(clip(steer_angle, 0, 4092)),
        })
      ret.append(packer.make_can_msg(addr_name, 0, values))

  return ret

# GEN2 new mazdas
def create_acc_cmd(packer, values, hold, resume):
  msg_name = "ACC"
  bus = 2

  if (values["ACC_ENABLED"]):
    values["HOLD"] = hold
    values["RESUME"] = resume
  else:
    pass

  return packer.make_can_msg(msg_name, bus, values)

def mazda2019_checksum(address: int, sig, d: bytearray) -> int:
  checksum = 0
  if address == 0x220:
    checksum = 0x2a
  if address == 0x249:
    checksum = 0x53
  # Simple sum over the payload, except for the byte where the checksum lives.
  for i in range(7):
    checksum += d[i]
  return checksum % 256

def mazda2017_checksum(address: int, sig, d: bytearray) -> int:
  sum_val = 0
  if d[5] & 0x5:
    sum_val = 0xFC
  for i in range(len(d) - 1):
    sum_val += d[i]
  return (~sum_val) & 0xFF