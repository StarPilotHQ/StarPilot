"""Default values and numeric bounds for saved driving settings."""

from openpilot.selfdrive.car.cruise import LOW_SPEED_CRUISE_ON_SET
from openpilot.starpilot.curve_speed.preferences import MASTER_KEY as CURVE_MASTER_KEY, NO_LEAD_KEY as CURVE_NO_LEAD_KEY
from openpilot.starpilot.lateral.lane_centering import STRENGTH_MAX
from openpilot.starpilot.longitudinal.downhill_coast import KEY as DOWNHILL_KEY, SURPLUS_KEY, MAX_SURPLUS_MPH
from openpilot.starpilot.longitudinal.encouragement import KEY as NUDGE_KEY
from openpilot.starpilot.longitudinal.lead_approach_runtime import KEY as LEAD_APPROACH_KEY
from openpilot.starpilot.longitudinal.lead_takeoff_preferences import KEY as LEAD_TAKEOFF_KEY
from openpilot.starpilot.longitudinal.planner_selection import KEY as PLANNER_SELECTION_KEY
from openpilot.starpilot.speed_limits.runtime_settings import WHEEL_OVERRIDE_KEY

AGGRESSIVE_COOLING_KEY = "AggressiveCoolingEnabled"
AGGRESSIVE_COOLING_DESCRIPTION = ("Runs the fan sooner and harder to keep the comma 10-16 °C (18-29 °F) cooler on hot days, " +
                                  "at the cost of more fan noise. Applies next drive.")
BOOL_DEFAULTS = {
  "GMPedalLongitudinal": False, "ForceStops": True, "AlwaysAllowUploads": False, "TurnAssist": True,
  "DriveModeProfiles": False, "DriveModeAcceleration": True, "DriveModeDeceleration": True,
  "SubaruAvhStartup": False,
  "SubaruStopStartOff": False,
  "SubaruSNG": False,
  "SubaruRedneckCruise": False,
  "RedneckCruise": False,
  "ReverseCruise": False, "ToyotaAutoHold": False, "SNGHack": False, "JeepBrakeHold": False, "VoltSNG": False, "GMAutoHold": False, "VoltOnePedalMode": False,
  "RemapCancelToDistance": False, "LongPitch": True, "DisableOpenpilotLongitudinal": False,
  "SpeedLimitController": False, "ShowSpeedLimits": True,
  "SLCOnEngage": False,
  WHEEL_OVERRIDE_KEY: False,
  "PauseLateralOnSignal": False,
  "LaneCentering": True, "LaneCenteringPauseOnSignal": True,
  "CustomPersonalities": False, PLANNER_SELECTION_KEY: True,
  LEAD_APPROACH_KEY: False,
  LEAD_TAKEOFF_KEY: False,
  DOWNHILL_KEY: False,
  NUDGE_KEY: True,
  LOW_SPEED_CRUISE_ON_SET: False,
  "DisengageOnAccelerator": False,
  AGGRESSIVE_COOLING_KEY: False,
  CURVE_MASTER_KEY: False, CURVE_NO_LEAD_KEY: False,
  "ShowCSCStatus": False,
  "ForceAutoTuneOff": False, "AlwaysOnLateral": True, "NostalgiaMode": False,
}
FLOAT_SPECS = {
  SURPLUS_KEY: (0, MAX_SURPLUS_MPH, 1, "mph"),
  "ForceStopDistanceOffset": (-20, 20, 1, "ft"),
  "LaneCenterOffset": (-0.3, 0.3, 0.01, "m"),
  "LaneCenteringE2EAuthority": (0.0, 1.0, 0.05, "fraction"),
  "LaneCenteringStrength": (0.5, STRENGTH_MAX, 0.015, "fraction"),
  **{f"{name}{suffix}": (0.5, 3.0, 0.05, "s")
     for name in ("Aggressive", "Standard", "Relaxed") for suffix in ("Follow", "FollowHigh")},
  **{f"{name}{suffix}": (25.0, 200.0, 5.0, "%")
     for name in ("Aggressive", "Standard", "Relaxed")
     for suffix in ("JerkAcceleration", "JerkDeceleration", "JerkDanger", "JerkSpeedDecrease", "JerkSpeed")},
}
