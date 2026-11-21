from opendbc.car import structs
from opendbc.car.subaru.values import CanBus

VisualAlert = structs.CarControl.HUDControl.VisualAlert
AudibleAlert = structs.CarControl.HUDControl.AudibleAlert


def create_steering_control(packer, apply_torque, steer_req):
  values = {
    "LKAS_Output": apply_torque,
    "LKAS_Request": steer_req,
    "SET_1": 1
  }
  return packer.make_can_msg("ES_LKAS", 0, values)


def create_steering_control_angle(packer, apply_torque, steer_req, bus=CanBus.main):
  values = {
    "LKAS_Output": apply_torque,
    "LKAS_Request": steer_req,
    "SET_3": 3
  }
  return packer.make_can_msg("ES_LKAS_ANGLE", bus, values)


def create_steering_status(packer):
  return packer.make_can_msg("ES_LKAS_State", 0, {})


def create_es_distance(packer, frame, es_distance_msg, bus, pcm_cancel_cmd, long_enabled = False, brake_cmd = False, cruise_throttle = 0):
  values = {s: es_distance_msg[s] for s in [
    "CHECKSUM",
    "Signal1",
    "Cruise_Fault",
    "Cruise_Throttle",
    "Signal2",
    "Car_Follow",
    "Low_Speed_Follow",
    "Cruise_Soft_Disable",
    "Signal7",
    "Cruise_Brake_Active",
    "Distance_Swap",
    "Cruise_EPB",
    "Signal4",
    "Close_Distance",
    "Signal5",
    "Cruise_Cancel",
    "Cruise_Set",
    "Cruise_Resume",
    "Signal6",
  ]}

  values["COUNTER"] = frame % 0x10

  if long_enabled:
    values["Cruise_Throttle"] = cruise_throttle

    # Do not disable openpilot on Eyesight Soft Disable, if openpilot is controlling long
    values["Cruise_Soft_Disable"] = 0
    values["Cruise_Fault"] = 0

    values["Cruise_Brake_Active"] = brake_cmd

  if pcm_cancel_cmd:
    values["Cruise_Cancel"] = 1
    values["Cruise_Throttle"] = 1818 # inactive throttle

  return packer.make_can_msg("ES_Distance", bus, values)


def create_es_lkas_state(packer, frame, es_lkas_state_msg, enabled, visual_alert, left_line, right_line, left_lane_depart, right_lane_depart,
                         ready=None, lkas_active=None, in_drive=False):
  values = {s: es_lkas_state_msg[s] for s in [
    "CHECKSUM",
    "LKAS_Alert_Msg",
    "Signal1",
    "LKAS_ACTIVE",
    "LKAS_Dash_State",
    "Signal2",
    "Backward_Speed_Limit_Menu",
    "LKAS_Left_Line_Enable",
    "LKAS_Left_Line_Light_Blink",
    "LKAS_Right_Line_Enable",
    "LKAS_Right_Line_Light_Blink",
    "LKAS_Left_Line_Visible",
    "LKAS_Right_Line_Visible",
    "LKAS_Alert",
    "Signal3",
  ]}

  values["COUNTER"] = frame % 0x10

  legacy_2025_hud = (ready is not None) or (lkas_active is not None)
  if legacy_2025_hud:
    # Filter stock EyeSight centering engage/cancel and "Keep hands on wheel" alerts
    # (25: centering engage beep, 27: centering off beep, 28: hands-on warning beep, 30: hands-on OFF cancel beep)
    if values["LKAS_Alert"] in (25, 27, 28, 30):
      values["LKAS_Alert"] = 0
    if values["LKAS_Alert_Msg"] in (1, 7):
      values["LKAS_Alert_Msg"] = 0
  else:
    # Filter the stock LKAS "Keep hands on wheel" alert
    if values["LKAS_Alert_Msg"] == 1:
      values["LKAS_Alert_Msg"] = 0

    # Filter the stock LKAS sending an audible alert when it turns off LKAS
    if values["LKAS_Alert"] == 27:
      values["LKAS_Alert"] = 0

    # Filter the stock LKAS sending an audible alert when "Keep hands on wheel" alert is active (2020+ models)
    if values["LKAS_Alert"] == 28 and values["LKAS_Alert_Msg"] == 7:
      values["LKAS_Alert"] = 0

    # Filter the stock LKAS sending an audible alert when "Keep hands on wheel OFF" alert is active (2020+ models)
    if values["LKAS_Alert"] == 30:
      values["LKAS_Alert"] = 0

    # Filter the stock LKAS sending "Keep hands on wheel OFF" alert (2020+ models)
    if values["LKAS_Alert_Msg"] == 7:
      values["LKAS_Alert_Msg"] = 0

  # Preserve stock EyeSight Lane Departure Warnings (11, 12) at all times.
  # Show Keep hands on wheel alert for openpilot steerRequired alert
  if visual_alert == VisualAlert.steerRequired:
    if legacy_2025_hud:
      if values["LKAS_Alert"] == 0 and values["LKAS_Alert_Msg"] == 0:
        values["LKAS_Alert_Msg"] = 1
    else:
      values["LKAS_Alert_Msg"] = 1

  # Ensure we don't overwrite potentially more important alerts from stock (e.g. FCW)
  if visual_alert == VisualAlert.ldw and values["LKAS_Alert"] == 0:
    if left_lane_depart:
      values["LKAS_Alert"] = 12  # Left lane departure dash alert
    elif right_lane_depart:
      values["LKAS_Alert"] = 11  # Right lane departure dash alert

  if legacy_2025_hud:
    screen_active = True if lkas_active is None else bool(lkas_active)
    if screen_active:
      values["LKAS_ACTIVE"] = 1  # Show LKAS lane lines / keep screen active
      values["LKAS_Left_Line_Enable"] = 1
      values["LKAS_Right_Line_Enable"] = 1
      if enabled:
        values["LKAS_Dash_State"] = 2  # 2: Green enabled / active steering
      elif ready:
        values["LKAS_Dash_State"] = 1  # 1: White ready / override
      else:
        values["LKAS_Dash_State"] = 0  # 0: Inactive / no steering wheel icon
      line_visible = 1 if in_drive else 0
    else:
      values["LKAS_ACTIVE"] = 0
      values["LKAS_Dash_State"] = 0
      values["LKAS_Left_Line_Enable"] = 0
      values["LKAS_Right_Line_Enable"] = 0
      line_visible = 0

    values["LKAS_Left_Line_Visible"] = line_visible if left_line else 0
    values["LKAS_Right_Line_Visible"] = line_visible if right_line else 0
  else:
    if enabled:
      values["LKAS_ACTIVE"] = 1  # Show LKAS lane lines
      values["LKAS_Dash_State"] = 2  # Green enabled indicator
    else:
      values["LKAS_Dash_State"] = 0  # LKAS Not enabled

    values["LKAS_Left_Line_Visible"] = int(left_line)
    values["LKAS_Right_Line_Visible"] = int(right_line)

  return packer.make_can_msg("ES_LKAS_State", CanBus.main, values)


def create_es_dashstatus(packer, frame, dashstatus_msg, enabled, long_enabled, long_active, lead_visible):
  values = {s: dashstatus_msg[s] for s in [
    "CHECKSUM",
    "PCB_Off",
    "LDW_Off",
    "Signal1",
    "Cruise_State_Msg",
    "LKAS_State_Msg",
    "Signal2",
    "Cruise_Soft_Disable",
    "Cruise_Status_Msg",
    "Signal3",
    "Cruise_Distance",
    "Signal4",
    "Conventional_Cruise",
    "Signal5",
    "Cruise_Disengaged_Dash",
    "Cruise_Activated_Dash",
    "Signal6",
    "Cruise_Set_Speed",
    "Cruise_Fault",
    "Cruise_On",
    "Display_Own_Car",
    "Brake_Lights",
    "Car_Follow",
    "Signal7",
    "Far_Distance",
    "Cruise_State",
  ]}

  values["COUNTER"] = frame % 0x10

  if long_enabled:
    values["Cruise_State"] = 0
    # TODO: Cruise_Activated_dash should respect gas pressed and standstill stock behavior
    values["Cruise_Activated_Dash"] = enabled
    values["Cruise_Disengaged_Dash"] = 0
    values["Car_Follow"] = int(lead_visible)

    values["PCB_Off"] = 1 # AEB is not preserved, so show the PCB_Off on dash
    values["LDW_Off"] = 0
    values["Cruise_Fault"] = 0

  # Filter stock LKAS disabled and Keep hands on steering wheel OFF alerts
  if values["LKAS_State_Msg"] in (2, 3):
    values["LKAS_State_Msg"] = 0

  return packer.make_can_msg("ES_DashStatus", CanBus.main, values)


def create_es_brake(packer, frame, es_brake_msg, long_enabled, long_active, brake_value):
  values = {s: es_brake_msg[s] for s in [
    "CHECKSUM",
    "Signal1",
    "Brake_Pressure",
    "AEB_Status",
    "Cruise_Brake_Lights",
    "Cruise_Brake_Fault",
    "Cruise_Brake_Active",
    "Cruise_Activated",
    "Signal3",
  ]}

  values["COUNTER"] = frame % 0x10

  if long_enabled:
    values["Cruise_Brake_Fault"] = 0
    values["Cruise_Activated"] = long_active

    values["Brake_Pressure"] = brake_value

    values["Cruise_Brake_Active"] = brake_value > 0
    values["Cruise_Brake_Lights"] = brake_value >= 70

  return packer.make_can_msg("ES_Brake", CanBus.main, values)


def create_es_status(packer, frame, es_status_msg, long_enabled, long_active, cruise_rpm):
  values = {s: es_status_msg[s] for s in [
    "CHECKSUM",
    "Signal1",
    "Cruise_Fault",
    "Cruise_RPM",
    "Cruise_Activated",
    "Brake_Lights",
    "Cruise_Hold",
    "Signal3",
  ]}

  values["COUNTER"] = frame % 0x10

  if long_enabled:
    values["Cruise_RPM"] = cruise_rpm
    values["Cruise_Fault"] = 0

    values["Cruise_Activated"] = long_active

  return packer.make_can_msg("ES_Status", CanBus.main, values)


def create_es_infotainment(packer, frame, es_infotainment_msg, visual_alert, audible_alert=AudibleAlert.none, legacy_2025=False):
  # Filter stock LKAS disabled and Keep hands on steering wheel OFF alerts
  values = {s: es_infotainment_msg[s] for s in [
    "CHECKSUM",
    "LKAS_State_Infotainment",
    "LKAS_Blue_Lines",
    "Signal1",
    "Signal2",
  ]}

  values["COUNTER"] = frame % 0x10

  if values["LKAS_State_Infotainment"] in (3, 4):
    values["LKAS_State_Infotainment"] = 0

  # Show Keep hands on wheel alert for openpilot steerRequired alert
  if visual_alert == VisualAlert.steerRequired:
    if legacy_2025:
      # 4 adds wheel OFF icon on lockout without clobbering existing warnings
      if values["LKAS_State_Infotainment"] == 0:
        values["LKAS_State_Infotainment"] = 4 if audible_alert == AudibleAlert.warningImmediate else 3
    else:
      values["LKAS_State_Infotainment"] = 3

  # Show Obstacle Detected for fcw
  if visual_alert == VisualAlert.fcw:
    values["LKAS_State_Infotainment"] = 2

  return packer.make_can_msg("ES_Infotainment", CanBus.main, values)


def create_ascent_lkas_alert(
  raw_dat: bytes,
  lateral_active: bool,
  visual_alert,
  armed: bool = False,
  acc_lead_visible: bool = False,
  legacy_2025: bool = False,
):
  address = 0x3C4
  if len(raw_dat) != 8 or raw_dat[0] != (((address & 0xff) + (address >> 8) + sum(raw_dat[1:])) & 0xff):
    return None

  dat = bytearray(raw_dat)
  changed = False

  if legacy_2025 and armed:
    # 0x3C4 on Legacy 2025: dat[2]=chime (0x18 chirp, 0x1a/0x1b single, 0x1c 2x bouncy, 0x1e 4x strong),
    # dat[3]=small-screen popup (0x01 hands-on, 0x07 flashing), dat[4]=HUD LEDs (0x01/0x02/0x04 yellow, 0x10/0x20/0x40 red).
    target_alert = b"\x00\x01\x04" if visual_alert == VisualAlert.steerRequired else None

    # Suppress all background EyeSight centering/toggle/hands-off alerts whenever Cruise Main is armed,
    # while preserving stock FCW (0x01, 0x02, 0x0e), native LDW (0x0b, 0x0c), and opaque status bits in dat[5:].
    if dat[2] in (0x19, 0x1b, 0x1c, 0x1e) or (
      dat[2] == 0x00 and (
        (dat[3] in (0x01, 0x07) and dat[4] in (0x04, 0x30)) or
        (dat[3] == 0x00 and dat[4] in (0x02, 0x30))
      )
    ):
      if not (target_alert is not None and dat[2:5] == target_alert):
        dat[2:5] = b"\x00\x00\x00"
        changed = True
    if target_alert is not None:
      if dat[2:5] == b"\x00\x00\x00":
        dat[2:5] = target_alert
        changed = True
      if dat[2:5] == target_alert and dat[5] == 0x01:
        dat[5] = 0x00
        changed = True

  if lateral_active and visual_alert != VisualAlert.steerRequired:
    if dat[2:] == b"\x00\x01\x04\x00\x00\x00":
      dat[3:5] = b"\x00\x00"
      changed = True
    elif legacy_2025 and (dat[2] == 0x1b or (dat[2] == 0x00 and dat[3] == 0x00 and dat[4] == 0x02)):
      # Suppress stock lane-centering toggle chime/LED while preserving native LDW (0x0b, 0x0c, 000005, 000006)
      dat[2:5] = b"\x00\x00\x00"
      changed = True

  # Restore dat[5] = 0x01 when ACC is tracking a lead and background LKAS warning cleared dat[5].
  if legacy_2025 and acc_lead_visible and dat[2:5] == b"\x00\x00\x00" and dat[5] == 0x00:
    dat[5] = 0x01
    changed = True

  if changed:
    dat[0] = ((address & 0xff) + (address >> 8) + sum(dat[1:])) & 0xff
  return address, bytes(dat), CanBus.main


def create_es_highbeamassist(packer):
  values = {
    "HBA_Available": False,
  }

  return packer.make_can_msg("ES_HighBeamAssist", CanBus.main, values)


def create_es_static_1(packer):
  values = {
    "SET_3": 3,
  }

  return packer.make_can_msg("ES_STATIC_1", CanBus.main, values)


def create_es_static_2(packer):
  values = {
    "SET_3": 3,
  }

  return packer.make_can_msg("ES_STATIC_2", CanBus.main, values)


# *** Subaru Pre-global ***

def subaru_preglobal_checksum(packer, values, addr, checksum_byte=7):
  dat = packer.make_can_msg(addr, 0, values)[1]
  return (sum(dat[:checksum_byte]) + sum(dat[checksum_byte+1:])) % 256


def create_preglobal_steering_control(packer, frame, apply_torque, steer_req):
  values = {
    "COUNTER": frame % 0x08,
    "LKAS_Command": apply_torque,
    "LKAS_Active": steer_req,
  }
  values["Checksum"] = subaru_preglobal_checksum(packer, values, "ES_LKAS")

  return packer.make_can_msg("ES_LKAS", CanBus.main, values)


def create_preglobal_es_distance(packer, cruise_button, es_distance_msg):
  values = {s: es_distance_msg[s] for s in [
    "Cruise_Throttle",
    "Signal1",
    "Car_Follow",
    "Signal2",
    "Cruise_Brake_Active",
    "Distance_Swap",
    "Standstill",
    "Signal3",
    "Close_Distance",
    "Signal4",
    "Standstill_2",
    "Cruise_Fault",
    "Signal5",
    "COUNTER",
    "Signal6",
    "Cruise_Button",
    "Signal7",
  ]}

  values["Cruise_Button"] = cruise_button
  values["Checksum"] = subaru_preglobal_checksum(packer, values, "ES_Distance")

  return packer.make_can_msg("ES_Distance", CanBus.main, values)


def subaru_checksum(address: int, sig, d: bytearray) -> int:
  s = 0
  addr = address
  while addr:
    s += addr & 0xFF
    addr >>= 8
  for i in range(1, len(d)):
    s += d[i]
  return s & 0xFF
