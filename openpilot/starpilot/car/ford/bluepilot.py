"""BluePilot lateral: StarPilot's startup settings and the per-frame inputs its Ford CarController reads
(opendbc bluepilot_lateral/hosts/starpilot)."""
from collections import deque
from dataclasses import replace
import math
from typing import TYPE_CHECKING, cast
import numpy as np

import openpilot.cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.saved_source import read_saved

from opendbc.bluepilot_lateral.core.values_ext import BP_LATERAL_PARAMS
from opendbc.bluepilot_lateral.hosts.starpilot.lateral import BluePilotLateral, FrameInputs
from opendbc.bluepilot_lateral.core.lateral_curv_ext import PrimaryLateralControl, MAX_LATERAL_ACCEL
from opendbc.bluepilot_lateral.core.values_ext import BP_ANGLE_LIMITS
from opendbc.bluepilot_lateral.core.lateral_angle_ext import LateralAngleExt
from opendbc.bluepilot_lateral.core.lateral_inputs import lateral_inputs_complete
from opendbc.bluepilot_lateral.can.fordcan_ext import lat_ctl2_checksum, create_lat_ctl2_msg
from opendbc.car.ford.values import CarControllerParams, FordFlags
from opendbc.car.ford.aol import qualified as qualified_aol
from opendbc.car import structs
from opendbc.car.vehicle_model import calc_slip_factor
from opendbc.bluepilot_lateral.core.carcontroller_ext import pinion_vehicle_model
from opendbc.car.lateral import ISO_LATERAL_ACCEL
from openpilot.starpilot.car.ford.controller_selection import FordController, read_controller

if TYPE_CHECKING:
  from openpilot.starpilot.car.ford.starpilot_controller import StarPilotFordController


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


def quiet_cluster_display(address, data):
  if len(data) != 8:
    return False
  if address == 0x18A:
    return ((data[5] >> 2) & 7) <= 1 and not (
      data[0] & 0xE0 or data[1] & 1 or data[3] & 0xF0 or data[4] & 0xC0 or data[5] & 0x80 or data[6] & 0x3F)
  if address == 0x3D8:
    return data[6] >> 3 in (0, 30) and not (data[4] & 0x0D or data[5] & 0x18 or data[6] & 6)
  return False


def startup_params(params, *, enabled: bool) -> dict[str, str]:
  """The startup settings init_ford_safety_param_starpilot reads. BluePilot lateral stays off without the openpilot
  toggle, in Safe Mode, or when a startup setting can't be read. Legacy stock selection is retired."""
  safe, safe_readable = read_saved(params, "SafeMode", 8)
  if not enabled or not safe_readable or safe not in (None, b"0"):
    return {"disable_BP_lat_UI": "1"}
  choice, _, readable = read_controller(None, params)
  if not readable or choice is None:
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

  def __init__(self, params, cp=None):
    self.params = params
    self.cp = cp
    self.controller_choice = FordController.BLUEPILOT_CURVATURE
    self.current_inputs_valid = False
    self.sm = messaging.SubMaster(["modelV2", "vehicleParameters", "lateralDelay"])
    self.frames = 0
    self.settings = []
    self.unknown_keys = set()

  def read_settings(self):
    entries = []
    self.controller_choice = read_controller(self.cp, self.params)[0]
    for key in BP_LATERAL_PARAMS:
      try:
        if key == "FordPrefLateralControl":
          value = int(self.controller_choice in (FordController.BLUEPILOT_ANGLE, FordController.STARPILOT))
        elif key == "disable_BP_lat_UI" and self.controller_choice is None:
          value = True
        else:
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
    self.current_inputs_valid = bool(li.valid and self.sm.all_checks(["modelV2", "vehicleParameters"]))
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

  def __init__(self, CP, packer, CAN, inputs_source=None):
    super().__init__(CP, packer, CAN)
    self.inputs_source = inputs_source
    self.starpilot_controller = None
    self.starpilot_selected = False
    self.starpilot_frame_valid = False
    self.lateral_active = False
    self.last_measured_curvature = 0.0
    self.speed_history = deque(maxlen=6)
    self.measured_history = deque([0] * 6, maxlen=6)
    self.measured_speed = 0
    self.measured_seen = False
    self.native_pinion_model = pinion_vehicle_model(CP) if self.inputs.bp_pinion_curvature_enabled else None
    self.native_pinion_slip = (np.float32(calc_slip_factor(self.native_pinion_model))
                               if self.native_pinion_model is not None else np.float32(0.))

  def observe_can(self, can_list):
    # Match Panda's receive order: yaw can precede the new speed in the same batch.
    if not self.CP.flags & FordFlags.CANFD and not self.starpilot_selected and self.native_pinion_model is None:
      return
    for _, batch in can_list:
      for address, data, bus in batch:
        if bus != self.CAN.main or len(data) != 8:
          continue
        if address == 0x415 and data[2] >> 6 == 3:
          speed = np.float32(((data[0] << 8) | data[1]) * .01 * (1. / 3.6))
          self.measured_speed = math.floor(float(np.float32(speed * 1000.)) + .5)
          self.speed_history.append(self.measured_speed)
        elif address == 0x7E and self.native_pinion_model is not None:
          # Mirror the fixed native pinion geometry, before live ratio/roll corrections.
          raw_angle = ((data[2] & 0x7F) << 8) | data[3]
          angle = np.float32(np.float32(raw_angle) * np.float32(.1) - np.float32(1600.))
          radians = np.float32(angle * np.float32(math.pi / 180.))
          speed = np.float32(max(self.measured_speed / 1000., .1))
          slip_speed = np.float32(self.native_pinion_slip * np.float32(speed * speed))
          factor = np.float32(1. / (1. - float(slip_speed)) / float(np.float32(self.native_pinion_model.l)))
          curvature = np.float32(np.float32(radians * factor) / np.float32(self.native_pinion_model.sR))
          raw = float(np.float32(curvature * np.float32(50000.)))
          self.measured_history.append(math.floor(raw + .5) if raw >= 0 else math.ceil(raw - .5))
          self.measured_seen = True
        elif address == 0x91 and (data[6] >> 4) & 3 == 3 and not self.inputs.bp_pinion_curvature_enabled:
          yaw = np.float32(((data[2] << 8) | data[3]) * .0002 - 6.5)
          speed = np.float32(self.measured_speed / 1000.)
          curvature = np.float32(yaw / max(speed, np.float32(.1)))
          raw = float(np.float32(curvature * 50000.))
          self.measured_history.append(math.floor(raw + .5) if raw >= 0 else math.ceil(raw - .5))
          self.measured_seen = True

  def update(self, frame_inputs, frame):
    selected = (self.inputs_source is not None and
                self.inputs_source.controller_choice == FordController.STARPILOT)
    if selected != self.starpilot_selected and self.lateral_active:
      # Apply a controller change only after an inactive steering frame, never mid-maneuver.
      selected = self.starpilot_selected
      mode = int(self.curv.primary_lateral_control == PrimaryLateralControl.angle)
      frame_inputs = replace(frame_inputs, params=[
        FrameInputs.Param(p.key, str(mode).encode()) if p.key == "FordPrefLateralControl" else p
        for p in frame_inputs.params])
    if selected != self.starpilot_selected:
      from openpilot.starpilot.car.ford.starpilot_controller import StarPilotFordController
      self.angle = (StarPilotFordController if selected else LateralAngleExt)(self.CP, self.inputs, self.shared)
      self.angle.bp_kappa_cmd = self.last_measured_curvature
      self.shared.human_turn_detector.reset()
      self.starpilot_controller = self.angle if selected else None
    self.starpilot_selected = selected
    self.starpilot_frame_valid = bool(frame_inputs.lateralInputs.valid and lateral_inputs_complete(frame_inputs.lateralInputs) and
                                      getattr(self.inputs_source, "current_inputs_valid", False))
    return super().update(frame_inputs, frame)

  def step_lateral(self, CC, CS, actuators, apply_curvature_last):
    measured = self.inputs.get_current_curvature(CS)
    self.last_measured_curvature = measured if math.isfinite(measured) else 0.0
    self.lateral_active = bool(CC.latActive)
    if self.starpilot_selected and (not self.starpilot_frame_valid or not self.angle.inputs_valid(CS, actuators)):
      CC = structs.CarControl.new_message(latActive=False)
    if self.starpilot_selected:
      angle = cast("StarPilotFordController", self.angle)
      angle.native_curvature_bounds = None
      if self.measured_seen and CS.out.vEgoRaw > 10.:
        if self.native_pinion_model is not None:
          # Pinion native limits use 150 CAN curvature units, without learned geometry.
          error = 150 * .00002
          low = -max(self.measured_history) * .00002 - error
          high = -min(self.measured_history) * .00002 + error
        else:
          # An old yaw extreme can expire before the next steering packet consumes its metadata.
          history = list(self.measured_history)
          sample_step = max((abs(b - a) for a, b in zip(history[:-1], history[1:], strict=True)), default=0)
          # Native RX may advance one sample before the host receives the same batch.
          error = max(0., CarControllerParams.CURVATURE_ERROR - (sample_step + 1) * .00002)
          current_native = -self.measured_history[-1] * .00002
          low = current_native - error
          high = current_native + error
        low = max(low, measured - CarControllerParams.CURVATURE_ERROR)
        high = min(high, measured + CarControllerParams.CURVATURE_ERROR)
        # Tighten the host envelope only where both measurements agree on a command.
        if low <= high:
          angle.native_curvature_bounds = (low, high)
    packets, applied = super().step_lateral(CC, CS, actuators, apply_curvature_last)
    return self._constrain_curvature(CC, CS, actuators, apply_curvature_last, packets, applied)

  def order_can_sends(self, frames):
    if not self.CP.flags & (FordFlags.CANFD | FordFlags.NEW_PORT) and qualified_aol(self.CP, marked_only=True) and not self.lateral_active:
      # Inactive classic steering returns display ownership to the camera.
      frames = [(a, d, b) for a, d, b in frames if b != self.CAN.main or not quiet_cluster_display(a, d)]
    if (not self.starpilot_selected or self.curv.primary_lateral_control != PrimaryLateralControl.angle or
        self.frame % CarControllerParams.STEER_STEP or self.frame % CarControllerParams.LKA_STEP):
      return frames
    lka, lmc = [], []
    steering_address = 0x3D6 if self.CP.flags & FordFlags.CANFD else 0x3D3
    for index, (address, data, bus) in enumerate(frames):
      if bus == self.CAN.main and len(data) == 8:
        if address == 0x3CA:
          lka.append(index)
        elif address == steering_address:
          lmc.append(index)
    if len(lka) != 1 or len(lmc) != 1 or lka[0] < lmc[0]:
      return frames
    # Both were computed this tick; Panda must see the current shadow before its steering command.
    reordered = list(frames)
    reordered.insert(lmc[0], reordered.pop(lka[0]))
    return reordered

  def _constrain_curvature(self, CC, CS, actuators, apply_curvature_last, packets, applied):
    state = CS.out
    if (not CC.latActive or (not self.CP.flags & FordFlags.CANFD and not self.starpilot_selected) or
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
    accel_cap = MAX_LATERAL_ACCEL if self.CP.flags & FordFlags.CANFD else ISO_LATERAL_ACCEL
    limit = min(self.curv.curvature_max, accel_cap / state.vEgoRaw ** 2)
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
