"""BluePilot lateral: StarPilot's startup settings and the per-frame inputs its Ford CarController reads
(opendbc bluepilot_lateral/hosts/starpilot)."""
import openpilot.cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.saved_source import read_saved

from opendbc.bluepilot_lateral.core.values_ext import BP_LATERAL_PARAMS
from opendbc.bluepilot_lateral.hosts.starpilot.lateral import FrameInputs

STARTUP_KEYS = ("disable_BP_lat_UI", "FordPrefSteerAngleCurvature")
SETTINGS_REFRESH_FRAMES = 300  # 3 s of card's 100 Hz loop


def startup_params(params, *, enabled: bool) -> dict[str, str]:
  """The startup settings init_ford_safety_param_starpilot reads. BluePilot lateral stays off without the openpilot
  toggle, in Safe Mode, or when a setting can't be read; an unset key gets its params_keys.h default."""
  safe, safe_readable = read_saved(params, "SafeMode", 8)
  if not enabled or not safe_readable or safe not in (None, b"0"):
    return {"disable_BP_lat_UI": "1"}
  settings = {}
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
        value = self.params.get(key, return_default=True)
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
