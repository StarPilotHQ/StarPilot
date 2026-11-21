from opendbc.car.volvo.helpers import (checksum_2_lca_2_message, checksum_1_pscm_related_message, checksum_lca_5_message)

## C1
def create_c1_pscm_message(packer, msg_pscm: dict):
  values = {
    "LKATorque": 0,
    "SteeringAngleServo": msg_pscm["SteeringAngleServo"],
    "byte0": msg_pscm["byte0"],
    "byte3": msg_pscm["byte3"],
    "byte4": msg_pscm["byte4"],
    "byte7": msg_pscm["byte7"],
    "LKAActive": int(msg_pscm["LKAActive"]) & 0xD,
  }
  return packer.make_can_msg("PSCM1", 2, values)

def create_c1_checksum(data: bytes) -> int:
  angle_raw = ((data[4] & 0x3F) << 8) | data[5]
  direction = data[7] & 0x3
  checksum_sum = (data[3] + direction + angle_raw + (angle_raw >> 8)) & 0xFF
  return checksum_sum ^ 0xFF

def create_c1_steering_control(packer, apply_angle: float, lat_active: bool):
  values = {
    "SET_X_E3": 0xE3,
    "SET_X_B4": 0xB4,
    "SET_X_08": 0x08,
    "TrqLim": 0,
    "LKAAngleReq": apply_angle,
    "LKASteerDirection": 3 if lat_active else 0,
    "SET_X_25": 0x25,
    "SET_X_02": 0x02,
  }
  data = packer.make_can_msg("FSM1", 0, values)[1]
  values["Checksum"] = create_c1_checksum(data)
  return packer.make_can_msg("FSM1", 0, values)

def create_c1_cancel(packer):
  return packer.make_can_msg("CCButtons", 0, {"ACCStopBtn": 1})


## CMA/SPA
def create_lca_message(packer, msg_lca: dict, authority_pos: float, authority_neg: float):
  """LCA (0x58) - enable bits plus the torque-authority envelope"""
  values = msg_lca | {
    'NEW_SIGNAL_1': 3,
    'LCA_ENABLE_INV': 0,
    'LANE_KEEP_ACTIVE_INV': 3,
    'RIGHT_TORQUE_AUTHORITY': round(authority_pos),
    'LEFT_TORQUE_AUTHORITY': round(authority_neg),
    'NEW_SIGNAL_6': 15, # car usually keeps at 15 unless driver overrides pilot assist
  }
  return packer.make_can_msg('LCA', 2, values)

def create_pscm_message(packer, msg_pscm: dict):
  """PSCM (0x16) relayed party -> main with hands-on-wheel spoofed"""
  values = msg_pscm | {
    'HANDS_ON_WHEEL_ALERT_FLAGS': 3,  # follow timer
    'HANDS_ON_WHEEL_ALERT_TIMER': 0,  # seconds since last hands on wheel
  }
  return packer.make_can_msg('PSCM', 0, values)

def create_lca_3_message(packer, msg_lca_3: dict):
  """LCA_3 (0x57) - enables PSCM to accept LCA commands"""
  values = msg_lca_3 | {
    'LCA_ACCEPT_COMMANDS_RELATED': 15,
    'LCA_ACCEPT_COMMANDS_INV': 0,
  }
  return packer.make_can_msg('LCA_3', 2, values)

def create_lca_2_message(packer, msg_lca_2: dict):
  """LCA_2 (0x69) - spoof PILOT_ASSIST_ENGAGED=1 so PSCM accepts LCA commands"""
  values = msg_lca_2 | {'PILOT_ASSIST_ENGAGED': 1}
  dat = packer.make_can_msg('LCA_2', 2, values)[1]
  values['CHECKSUM_2'] = checksum_2_lca_2_message(dat[0], dat[1], dat[3], dat[4])
  return packer.make_can_msg('LCA_2', 2, values)

def create_lca_5_message(packer, target_angle_deg: float, msg_lca_5: dict):
  """LCA_5 (0x67) - the angle command at 0.05596 deg/count"""
  values = msg_lca_5 | {'LCA_5_STEER': target_angle_deg}
  dat = packer.make_can_msg('LCA_5', 2, values)[1]
  values['CHECKSUM'] = checksum_lca_5_message(dat[0], dat[1], dat[3], dat[4], dat[5])
  return packer.make_can_msg('LCA_5', 2, values)

def create_pscm_related_message(packer, msg_pscm_related: dict, stock_lca_engaged: bool):
  """
  PSCM_RELATED (0x17) relayed party -> main. When stock Pilot Assist isn't engaged,
  LCA_ENABLED_ECHO is cleared so the rest of the car doesn't see a PA echo for
  openpilot's steering.
  """
  if stock_lca_engaged:
    return packer.make_can_msg('PSCM_RELATED', 0, msg_pscm_related)

  values = msg_pscm_related | {'LCA_ENABLED_ECHO': 0}
  dat = packer.make_can_msg('PSCM_RELATED', 0, values)[1]
  values['CHECKSUM_1'] = checksum_1_pscm_related_message(dat[1], dat[2])
  return packer.make_can_msg('PSCM_RELATED', 0, values)

def create_lca_4_message(packer, msg_lca_4: dict):
  """LCA_4 (0x90) - hold LCA_ENABLE at 3 so PSCM thinks Pilot Assist is on and accepts LCA commands"""
  values = msg_lca_4 | {'LCA_ENABLE': 3}
  return packer.make_can_msg('LCA_4', 2, values)
