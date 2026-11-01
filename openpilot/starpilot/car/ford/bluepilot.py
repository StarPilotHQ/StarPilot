"""BluePilot lateral: StarPilot's startup settings and the per-frame inputs its Ford CarController reads
(opendbc bluepilot_lateral/hosts/starpilot)."""
from collections import deque
import math
import numpy as np

import openpilot.cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.saved_source import read_saved

from opendbc.bluepilot_lateral.core.values_ext import BP_LATERAL_PARAMS
from opendbc.bluepilot_lateral.hosts.starpilot.lateral import BluePilotLateral, FrameInputs
from opendbc.bluepilot_lateral.core.lateral_curv_ext import PrimaryLateralControl, MAX_LATERAL_ACCEL
from opendbc.bluepilot_lateral.core.values_ext import BP_ANGLE_LIMITS
from opendbc.bluepilot_lateral.can.fordcan_ext import lat_ctl2_checksum, create_lat_ctl2_msg
from opendbc.car.ford.values import CarControllerParams, FordFlags
from opendbc.car import structs

STARTUP_KEYS = ("FordPrefSteerAngleCurvature",)
# Global StarPilot lane settings are the only lane-positioning owner.
HOST_SETTINGS = {
  "disable_BP_lat_UI": False,
  "enable_lane_positioning_curv": False,
  "enable_lane_full_mode_curv": False,
  "custom_path_offset_curv": 0.0,
  "LC_PID_gain_UI_curv": 0.0,
  "enable_lane_positioning_ang": False,
  "custom_path_offset_ang": 0.0,
  "lane_centering_strength_ang": 0.0,
}
SETTINGS_REFRESH_FRAMES = 300  # 3 s of card's 100 Hz loop


def startup_params(params, *, enabled: bool) -> dict[str, str]:
  """The startup settings init_ford_safety_param_starpilot reads. BluePilot lateral stays off without the openpilot
  toggle, in Safe Mode, or when a startup setting can't be read. Legacy stock selection is retired."""
  safe, safe_readable = read_saved(params, "SafeMode", 8)
  if not enabled or not safe_readable or safe not in (None, b"0"):
    return {"disable_BP_lat_UI": "1"}
  settings = {"disable_BP_lat_UI": "0"}
  for key in STARTUP_KEYS:
    raw, readable = read_saved(params, key, 8)
    if not readable:
      return {"disable_BP_lat_UI": "1"}
    settings[key] = raw.decode() if raw is not None else ("1" if params.get_default_value(key) else "0")
  return settings


def encode(value) -> bytes:
  return (b"1" if value else b"0") if isinstance(value, bool) else str(value).encode()


class BluePilotInputs:
  """Card-owned: this frame's model and vehicle-parameter inputs, and the per-frame settings (BP_LATERAL_PARAMS)."""

  def __init__(self, params):
    self.params = params
    self.sm = messaging.SubMaster(["modelV2", "vehicleParameters", "lateralDelay"])
    self.frames = 0
    self.settings = []
    self.unknown_keys = set()

  def read_settings(self):
    entries = []
    for key in BP_LATERAL_PARAMS:
      try:
        value = HOST_SETTINGS[key] if key in HOST_SETTINGS else self.params.get(key, return_default=True)
      except Exception:
        if key not in self.unknown_keys:
          cloudlog.warning(f"BluePilot lateral param {key} not registered in params_keys.h; skipping")
          self.unknown_keys.add(key)
        continue
      if value is not None:
        entries.append(FrameInputs.Param(key, encode(value)))
    return entries

  def update(self) -> FrameInputs:
    self.sm.update(0)
    if self.frames % SETTINGS_REFRESH_FRAMES == 0:
      self.settings = self.read_settings()
    self.frames += 1

    frame = FrameInputs(params=self.settings)
    li, md, vp = frame.lateralInputs, self.sm["modelV2"], self.sm["vehicleParameters"]
    li.valid = bool(self.sm.seen["modelV2"] and self.sm.seen["vehicleParameters"] and
                    len(md.laneLines) >= 3 and len(md.laneLineProbs) >= 3 and len(md.laneLineStds) >= 3)
    if li.valid:
      li.pathYawRate = list(md.orientationRate.z)
      li.pathX = list(md.position.x)
      li.pathY = list(md.position.y)
      li.laneLineLeftX = list(md.laneLines[1].x)
      li.laneLineLeftY = list(md.laneLines[1].y)
      li.laneLineRightX = list(md.laneLines[2].x)
      li.laneLineRightY = list(md.laneLines[2].y)
      li.laneLineProbs = list(md.laneLineProbs)
      li.laneLineStds = list(md.laneLineStds)
      li.laneChangeState = int(md.meta.laneChangeState.raw)
      li.laneChangeDirection = int(md.meta.laneChangeDirection.raw)
      li.steerRatio = vp.steerRatio
      li.stiffnessFactor = vp.stiffnessFactor
      li.roll = vp.roll
      li.angleOffsetDeg = vp.angleOffsetDeg
      li.lateralDelay = self.sm["lateralDelay"].lateralDelay
    return frame


class FordBluePilotController(BluePilotLateral):
  """Keep the library's curvature handoff inside its unchanged native convergence envelope."""

  def __init__(self, CP, packer, CAN):
    super().__init__(CP, packer, CAN)
    self.measured_history = deque([0] * 6, maxlen=6)
    self.measured_speed = 0
    self.measured_seen = False

  def observe_can(self, can_list):
    # Match Panda's receive order: yaw can precede the new speed in the same batch.
    if not self.CP.flags & FordFlags.CANFD or self.inputs.bp_pinion_curvature_enabled:
      return
    for _, batch in can_list:
      for address, data, bus in batch:
        if bus != self.CAN.main or len(data) != 8:
          continue
        if address == 0x415 and data[2] >> 6 == 3:
          speed = np.float32(((data[0] << 8) | data[1]) * .01 * (1. / 3.6))
          self.measured_speed = math.floor(float(np.float32(speed * 1000.)) + .5)
        elif address == 0x91 and (data[6] >> 4) & 3 == 3:
          yaw = np.float32(((data[2] << 8) | data[3]) * .0002 - 6.5)
          speed = np.float32(self.measured_speed / 1000.)
          curvature = np.float32(yaw / max(speed, np.float32(.1)))
          raw = float(np.float32(curvature * 50000.))
          self.measured_history.append(math.floor(raw + .5) if raw >= 0 else math.ceil(raw - .5))
          self.measured_seen = True

  def step_lateral(self, CC, CS, actuators, apply_curvature_last):
    packets, applied = super().step_lateral(CC, CS, actuators, apply_curvature_last)
    state = CS.out
    if (not CC.latActive or not self.CP.flags & FordFlags.CANFD or
        self.curv.primary_lateral_control == PrimaryLateralControl.angle or applied == 0. or
        not state.canValid or state.canTimeout or state.steerFaultTemporary or state.steerFaultPermanent or
        not math.isfinite(state.vEgoRaw) or state.vEgoRaw <= 10.):
      return packets, applied
    measured = self.inputs.get_current_curvature(CS)
    if not math.isfinite(measured):
      return packets, applied
    error = CarControllerParams.CURVATURE_ERROR
    low, high = measured - error, measured + error
    if self.measured_seen and not self.inputs.bp_pinion_curvature_enabled:
      low = max(low, -max(self.measured_history) * .00002 - error)
      high = min(high, -min(self.measured_history) * .00002 + error)
    if low > high:
      return packets, applied
    limit = min(self.curv.curvature_max, MAX_LATERAL_ACCEL / state.vEgoRaw ** 2)
    if state.steeringPressed and (low > limit or high < -limit):
      # Use the curvature controller's neutral reset while the driver exceeds its acceleration cap.
      inactive = structs.CarControl.new_message(latActive=False)
      super().step_lateral(inactive, CS, actuators, apply_curvature_last)
      for index, (address, data, bus) in enumerate(packets):
        if address == 0x3D6 and bus == self.CAN.main:
          packets[index] = create_lat_ctl2_msg(self.packer, self.CAN, 1, 3, 1, 0., 0., 0., 0., (data[7] >> 1) & 0xF)
          return packets, 0.
      return packets, applied
    delta = float(np.interp(state.vEgoRaw, *BP_ANGLE_LIMITS.ANGLE_RATE_LIMIT_DOWN))
    # Rejoin the measured band with the library's existing unwind rate.
    target = float(np.clip(applied, low, high))
    corrected = float(np.clip(target, apply_curvature_last - delta, apply_curvature_last + delta))
    corrected = float(np.clip(corrected, -limit, limit))
    if corrected == applied:
      return packets, applied
    for index, (address, data, bus) in enumerate(packets):
      if address != 0x3D6 or bus != self.CAN.main or (data[0] >> 4) & 7 != 1:
        continue
      raw = round(-corrected / 0.00002) + 1000
      payload = bytearray(data)
      payload[2] = raw >> 3
      payload[3] = (payload[3] & 0x1F) | ((raw & 7) << 5)
      payload[1] = lat_ctl2_checksum(1, (payload[7] >> 1) & 0xF, bytes(payload))
      packets[index] = (address, bytes(payload), bus)
      return packets, float((1000 - raw) * 0.00002)
    return packets, applied
