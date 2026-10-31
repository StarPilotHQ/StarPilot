"""Descriptions of existing settings; no preferences, defaults or runtime state.

Rows and their availability still come from the canonical feature owners.
This presentation catalog supplies the purpose before any genuine prerequisite.
"""
from openpilot.starpilot.ui.long_profile_feature import CATEGORY_HELP, VALUE_HELP
from openpilot.starpilot.ui.feature_settings_state import SLC_OFFSET_KEYS

DESCRIPTIONS = {
  'DisengageOnAccelerator': 'When enabled, pressing the accelerator pedal will disengage openpilot.',
  'ReduceMotion': 'Reduces interface animations for a steadier display.',
  'IsMetric': 'On displays speed and distance in kilometers; Off uses miles. Saved speed-limit offsets keep their physical values.',
  'IsRhdDetected': 'Sets the initial steering-wheel side used by driver monitoring. Applies next drive; automatic side detection continues.',
  'lane_change:turn_signal_guidance': 'Uses your turn signal to help the model choose a turning path at intersections.',
  'AlphaLongitudinalEnabled': ('Requests StarPilot speed control instead of factory ACC. May disable Automatic Emergency Braking. ' +
                              'Applies after the next startup; exact vehicle takeover checks remain required.'),
  'lane_change:enabled': 'Enables lane-change assistance after you signal. With Automatic Lane Changes off, give the wheel a nudge to begin.',
  'lane_change:pace': 'Sets how quickly steering moves into the adjacent lane. Higher values change lanes more quickly; lower values make steering gentler.',
  'lane_change:speed': 'Sets the lowest speed at which lane-change assistance can begin.',
  'lane_change:one': 'Allows one assisted lane change until you release the turn signal.',
  'lane_change:auto': 'Starts a lane change after the signal delay without a steering nudge, when the adjacent lane and blind spot are clear.',
  'lane_change:delay': 'Sets the waiting time after signaling before an automatic lane change can begin.',
  'lane_change:width': 'Sets the minimum detected width of the adjacent lane for an automatic lane change.',
  'lane_change:close_gap': 'Temporarily reduces following time during a lane change when the lane and lead gap are clear.',
  'lane_change:close_gap_seconds': 'Sets following time while Close lane-change gap is active. Higher values leave more space.',
  'lane_change:reset': 'Restores the default lane-change assistance, signal timing, pace and gap settings.',
  'LaneCentering': 'Uses detected lane lines to help keep the vehicle centered in its lane.',
  'LaneCenteringPauseOnSignal': 'Pauses lane-centering correction while a turn signal is on.',
  'LaneCenterOffset': 'Moves the lane-centering target left or right of the detected lane center.',
  'LaneCenteringE2EAuthority': 'Lets a confident driving-model path reduce lane-centering correction when the model path differs from the lane center.',
  'LaneCenteringStrength': 'Sets the strength of lane-centering correction. 100% is normal; higher values strengthen it.',
  'PauseLateralSpeed': 'Pauses steering assistance below this speed. Zero disables the speed-based pause.',
  'PauseLateralOnSignal': 'Limits the low-speed steering pause to times when a turn signal is on.',
  'LateralResumeDelay': 'Waits before resuming steering after signaling ends, if speed fell below half the steering-pause threshold.',
  'AlwaysOnLateral': 'Keeps steering assistance available independently of cruise control on supported vehicles.',
  'AolBrakePauseSpeedMps': 'Pauses Always On Lateral during braking below this speed. Zero disables this brake-speed pause.',
  'NostalgiaMode': 'Lets the Ioniq 5 or Ioniq 6 left paddle pause acceleration and braking while Always On Lateral keeps steering.',
  'TurnAssist': 'Uses steering-angle feedback to assist rolling low-speed turns above the vehicle minimum steering speed.',
  'LateralControllerSelection': 'Chooses the stock steering controller or the vehicle-specific StarPilot controller.',
  'ForceAutoTuneOff': 'StarPilot’s FLM tuning will attempt to automatically correct torque profiles as you drive.',
  'torque_prepare_firestar': 'Prepares steering settings for a tuning-request recording. Turning it off restores the previous settings.',
  'SteerLatAccel': 'Sets the legacy lateral-acceleration-to-torque conversion. Review the vehicle-specific tuning profile before applying it.',
  'SteerFriction': 'Sets legacy steering friction compensation. Review the vehicle-specific tuning profile before applying it.',
  'UseStarPilotLongitudinalPlanner': 'Selects the StarPilot acceleration, braking and following planner. Off selects the upstream planner.',
  'CustomCruise': 'Sets how much a short cruise-button press changes the set speed.',
  'CustomCruiseLong': 'Sets how much holding a cruise button changes the set speed.',
  'ReverseCruise': 'Swaps the Toyota short-press and held-press cruise-speed increments.',
  'ForceStops': 'Commits to stops detected by the driving model. Press the accelerator to continue.',
  'ForceStopDistanceOffset': 'Moves the planned stopping point. Positive values stop farther forward; negative values stop earlier.',
  'LeadApproachBuffer': 'Gradually adds following distance when approaching a slower or braking lead vehicle.',
  'ExperimentalSpeedNudge': ('Gently recovers toward set speed when Experimental mode hesitates on a clear road. ' +
                            'Leads, stops, curve limits and driver overrides retain priority.'),
  'FasterLeadTakeoff': ('Improves launches from a stop and responds sooner when a lead starts moving. ' +
                        'Selected acceleration profiles, stop detection and vehicle limits still apply.'),
  'CustomPersonalities': 'Uses your following-time and jerk settings for the selected driving personality.',
  'profile:global_acceleration': 'Chooses the acceleration curve used by personalities set to Selected Profile.',
  'profile:global_braking': 'Chooses the braking response used by personalities set to Selected Profile.',
  'reset_profiles': 'Restores the default acceleration, braking and following curves and presets.',
  'CurveSpeedController': 'Adjusts cruise speed for curves ahead using available learned cornering preferences.',
  'CurveSpeedControllerNoLead': 'Pauses curve-speed adjustments while following a lead vehicle.',
  'ShowCSCStatus': 'Shows when Curve Speed Controller is adjusting speed on the driving screen.',
  'curve_reset': 'Deletes saved cornering learning so Curve Speed Controller can learn your preferences again.',
  'SpeedLimitController': 'Adjusts the set speed to accepted speed limits using your source priority and offsets.',
  'ShowSpeedLimits': 'Shows recognized speed-limit signs on the driving screen.',
  'SLCConfirmation': 'Auto adopts new limits immediately. With Automatic adoption off, + accepts and − declines within 10 seconds.',
  'SLCAdvanceSeconds': 'Offers mapped limits early using road distance and current speed. Existing adoption and confirmation settings still apply.',
  'SLCPriority1': 'Chooses the first source checked for a fresh speed limit.',
  'SLCPriority2': 'Chooses the source used when the primary source has no fresh speed limit.',
  'slc_reset': 'Resets saved speed-limit offsets while retaining valid configured speed ranges.',
  'reset_driver_monitoring': 'Clear saved wheel-side detection. Automatic detection restarts on the next drive.',
  'AlwaysAllowUploads': 'Allows queued full logs and videos to upload over metered networks. This uses mobile data.',
  'UseVOACC': ('Use vision-only adaptive cruise control instead of radar-assisted lead detection. ' +
               'Requires StarPilot longitudinal control. Applies next drive; radar faults and safety checks remain active.'),
  'ToyotaRadarMode': 'Chooses the supported Toyota front radar configuration, including the optional ARS510 retrofit.',
  'ToyotaAutoHold': ('Holds the brakes after you press the brake for over one second at a complete stop ' +
    'with cruise main on and cruise disengaged. Press the accelerator to release.'),
  'GMPedalLongitudinal': 'Uses a detected compatible pedal interceptor for StarPilot acceleration control.',
  'DisableOpenpilotLongitudinal': 'Turns off StarPilot acceleration and braking control for the supported Bolt configuration.',
  'VoltSNG': 'Assists automatic resume from a cruise-controlled stop when fresh lead and stopping information allow it.',
  'GMAutoHold': 'Holds the brakes at a stop with cruise main on. Press the accelerator or the supported regen paddle to release.',
  'VoltOnePedalMode': 'Slows and holds a Volt when you lift off the accelerator in Low or single-pedal mode, with cruise main on and cruise disengaged.',
  'LongPitch': 'Compensates acceleration and braking for road grade.',
  'GMLongitudinalTune': 'Selects the supported vehicle speed-control response tune.',
  'TruckTuning': 'Softens small acceleration requests, reduces grade correction and smooths light braking on supported GM trucks.',
  'EVTuning': 'Selects EV acceleration curves for inherited acceleration presets.',
  'TeslaAOLScreenTap': 'Lets a three-finger tap on a supported Tesla screen toggle Always On Lateral.',
  'TeslaAOLDisengageOnBrake': 'Disarms screen-tap Always On Lateral when you press the brake.',
  'RemoteStartBootsComma': 'Wakes comma during GM remote start.',
  'HKGRemoteStartBootsComma': 'Wakes comma during Hyundai, Kia or Genesis remote climate operation.',
  'IgnoreIgnitionLine': 'Uses GM CAN ignition instead of the harness ignition signal.',
  'TeslaWakeOnCAN': 'Wakes comma when the Tesla wakes up, before you press the brake.',
  'RadarRecoveryAlert': 'Play a chime and show a brief message when a radar fault clears. You must re-engage manually.',
  'CameraView': 'Chooses the camera view shown on the driving screen.',
  'DeveloperSidebar': 'Shows live developer metrics on the right of the Big driving screen. Off gives that space back to the road view.',
  'DriverCamera': 'Shows the driver camera while reversing.',
  'StockConfidenceBallWidget': 'Uses StarPilot driving widgets in place of the stock confidence ball.',
  'HidePersonalityAlerts': 'Hides the alert shown when the driving personality changes.',
  'ShowBrakeStatus': 'Colors the steering wheel to show braking and acceleration.',
  'HideLeadMarker': 'Shows the lead-vehicle indicator on the driving screen.',
  'LeadInfo': 'Shows the lead vehicle distance or speed beside its indicator.',
  'SignalMetrics': 'Shows an amber border for turn signals on comma 4.',
  'BlindSpotMetrics': 'Shows a red border for blind-spot warnings on comma 4.',
  'StarPilotDisplayPreferencesEnabled': 'Uses your configured screen brightness and display timeouts.',
  'ScreenBrightness': 'Sets screen brightness while parked. Auto follows the normal brightness policy.',
  'ScreenBrightnessOnroad': 'Sets screen brightness while driving. Auto follows the normal brightness policy.',
  'ScreenTimeout': 'Sets how long the parked screen remains on after interaction.',
  'ScreenTimeoutOnroad': 'Sets the wait after interaction before returning to the driving view or turning off the screen if configured.',
  'KeepScreenOnGpuLoading': 'Keeps the driving screen visible while an eGPU model loads. Manual Screen Off still works.',
  'TurnOffCommaScreen': 'Pauses comma UI rendering to reduce CPU and GPU use. Tap once to wake the screen.',
  'LoudBlindspotAlert': 'Uses the soft-warning sound when a lane change is blocked by a detected blind-spot vehicle.',
  'LoudBlindspotAlertWhenDisengaged': ('Warns when signaling toward a detected blind-spot vehicle with steering assistance off or paused. ' +
                                      'Independent of Loud Blindspot Alert. Hazards do not trigger it.'),
  'SoundPack': 'Chooses the sounds used for driving alerts and chimes.',
  'WarningImmediateVolume': 'Sets the starting volume of immediate warnings. These warnings still ramp to full volume.',
  'WarningSoftVolume': 'Sets the starting volume of soft warnings. Warning volume keeps its required minimum.',
  'RefuseVolume': 'Sets the volume of the engagement-refused sound.',
  'PromptDistractedVolume': 'Sets the volume of distracted-driver prompts.',
  'EngageVolume': 'Sets the engagement-chime volume.',
  'DisengageVolume': 'Sets the disengagement-alert volume.',
  'PromptVolume': 'Sets the volume of general prompts.',
  'PIPPreviewEnabled': 'Shows the configured blind-spot camera preview when fresh camera frames are available.',
  'PIPPreviewShowOnBlinker': 'Shows the blind-spot camera preview while a turn signal is on.',
  'PIPPreviewShowOnBSM': 'Shows the blind-spot camera preview during a blind-spot warning.',
  'PIPPreviewInvert': 'Mirrors the blind-spot camera crop horizontally.',
  'pip:mask:size': 'Sets the size of each camera-image crop shown in the preview.',
  'pip:mask:editor': 'Edits the source-image crop for the vehicle left and right camera views.',
  'pip:reset': 'Restores the default source-image camera crops.',
  'vasm:enabled': 'Enables visual spot-monitor warnings for the configured camera regions when qualified camera observations are available.',
  'vasm:confidence': 'Sets the model confidence needed for a visual spot-monitor warning.',
  'vasm:smooth': 'Sets how slowly visual spot-monitor warnings change.',
  'vasm:annotation': 'Selects the camera-image regions corresponding to the vehicle windows for visual spot monitoring.',
  'vasm:reset': 'Turns off visual spot monitoring and clears its configured camera regions.',
  'sentry:enabled': 'Monitors parked motion and stores detected events when fresh parked sensor evidence is available.',
  'sentry:sensitivity': 'Sets how large an acceleration change must be to count as parked motion. Lower values detect smaller changes.',
  'sentry:warning': 'Sets how long detected motion must persist before a warning is recorded.',
  'sentry:reset': 'Turns off parked motion monitoring and restores its sensitivity and warning persistence defaults.',
  'conditional:mode': 'Chooses Chill, Experimental or automatic switching based on the configured driving conditions.',
  'conditional:reset': 'Restores the default conditional driving-mode settings.',
  'conditional:manual_reset': 'Returns both remembered manual driving-mode choices to Automatic.',
  'disable_BP_lat_UI': ('Steers with the BluePilot lateral controller instead of the stock Ford curvature path: curvature or' +
    ' angle as the primary control, with lane positioning and its own tuning. Off by default.'),
  'FordPrefLateralControl': ('Chooses what BluePilot steers with. Curvature follows the planned curvature like the stock path;' +
    ' Angle uses the path angle as the main actuator.'),
  'FordPrefSteerAngleCurvature': ('Measures curvature from the steering pinion angle instead of the yaw-rate sensor, in control and' +
    ' Panda safety. For Fords with a faulty yaw sensor.'),
  'enable_human_turn_detection_curv': 'Eases off steering while you turn the wheel through a turn yourself.',
  'lane_change_factor_high_curv': 'Scales steering during a lane change. Values under 1.0 soften it.',
  'enable_lane_positioning_curv': 'Nudges the car toward the lane center, with an optional in-lane offset.',
  'custom_path_offset_curv': "Biases the car's position inside the lane, in meters.",
  'enable_lane_full_mode_curv': ("Centers between the lane lines when both are confidently detected, instead of following the model's" +
    ' path.'),
  'LC_PID_gain_UI_curv': 'Sets how strongly lane positioning pulls toward its target.',
  'custom_profile_curv': 'Uses the two predicted-curvature blend settings below instead of the built-in profile.',
  'pc_blend_ratio_low_C_UI_curv': 'Mixes predicted curvature into gentle curves: 0 follows the plan, 1 follows the prediction.',
  'pc_blend_ratio_high_C_UI_curv': 'Mixes predicted curvature into sharp curves: 0 follows the plan, 1 follows the prediction.',
  'FordLowSpeedFactor_ang': 'Scales the low-speed steering response in angle mode. 1.0 is the default feel.',
  'FordHighSpeedFactor_ang': 'Scales the high-speed steering response in angle mode. 1.0 is the default feel.',
  'FordHighSpeedDampening_ang': ('Tunes the straight-road response at highway speed in angle mode: lower it if the car oversteers,' +
    ' raise it if it understeers.'),
  'lane_change_factor_high_ang': 'Scales steering during a lane change in angle mode. Values over 1.0 strengthen it.',
  'enable_lane_positioning_ang': 'Nudges the car toward the lane-line center in angle mode, with an optional in-lane offset.',
  'custom_path_offset_ang': "Biases the car's position inside the lane in angle mode, in meters.",
  'lane_centering_strength_ang': "Sets how strongly lane positioning pulls against the model's own path in angle mode.",
}

# These are initialization explanations, not a second defaults table.
INITIALIZATION = {
  'LateralControllerSelection': ('Applies when driving processes next start: the controller class, vehicle policy and ' +
    'PID/filter state are constructed together.'),
  'UseStarPilotLongitudinalPlanner': 'Applies when the planner next starts: each choice runs a different planner process with its own subscriptions and state.',
  'TurnAssist': ('Applies when driving processes next start: the selected torque controller creates its ' +
    'low-speed feedback controller and filter state at initialization.'),
  'ToyotaRadarMode': 'Chooses the supported Toyota front radar configuration, including the optional ARS510 retrofit.',
  'ToyotaAutoHold': 'Requires the next vehicle startup to configure the brake-hold permission in CarParams and Panda safety.',
  'DisableOpenpilotLongitudinal': 'Requires the next vehicle startup to rebuild cruise ownership and Panda safety configuration.',
  'GMAutoHold': 'First enable requires the next vehicle startup to configure the brake-hold safety permission. An already admitted hold can be disabled live.',
  'VoltOnePedalMode': ('First enable requires the next vehicle startup to configure the braking safety ' +
    'permission. An already admitted mode can be disabled live.'),
  'VoltSNG': 'Applies when the vehicle controller next starts: its fresh resume-plan input is created for the selected stop-and-go mode.',
  'TeslaAOLScreenTap': 'Requires the next vehicle startup to configure the screen-button CAN source and matching safety permission.',
  'TeslaAOLDisengageOnBrake': 'Requires the next vehicle startup to configure the screen-button brake/disarm safety permission.',
  'RemoteStartBootsComma': 'Requires a device restart to load the matching Panda ignition firmware.',
  'HKGRemoteStartBootsComma': 'Requires a device restart to load the matching Panda ignition firmware.',
  'IgnoreIgnitionLine': 'Requires a device restart to load the matching Panda ignition firmware.',
  'TeslaWakeOnCAN': 'Requires a device restart to load the matching Panda ignition firmware.',
  'disable_BP_lat_UI': 'Requires the next vehicle startup to configure Panda safety for the BluePilot steering checks.',
  'FordPrefSteerAngleCurvature': 'Requires the next vehicle startup to configure the pinion measurement in Panda safety.',
}

CONDITIONS = {
  'speed_mps': 'Sets the speed threshold for automatic mode switching without a tracked lead vehicle.',
  'speed_with_lead_mps': 'Sets the speed threshold for automatic mode switching while tracking a lead vehicle.',
  'signal_speed_mps': 'Requests Experimental below this speed while signaling, subject to the configured adjacent-lane check.',
  'open_road': 'Requests Experimental on an open road without a tracked lead vehicle.',
  'curves': 'Requests Experimental for curves without a tracked lead vehicle.',
  'curves_with_lead': 'Requests Experimental for curves while tracking a lead vehicle.',
  'lead': 'Uses a tracked lead vehicle as a condition for automatic mode switching.',
  'slower_lead': 'Requests Experimental when approaching a slower lead vehicle.',
  'stopped_lead': 'Requests Experimental for a detected stopped lead vehicle.',
  'stop_lights': 'Requests Experimental for detected traffic lights and stop scenes.',
  'model_stop_s': 'Sets how far ahead in time to look for a stop predicted by the driving model.',
  'signal_lane_detection': ('Distinguishes a turn from a lane change using the space beside the car. A signal ' +
    'requests Experimental only when no adjacent lane is detected.'),
  'signal_lane_width_m': 'Sets the minimum adjacent width that counts as a lane when distinguishing a turn from a lane change.',
  'set_speed_margin_mps': 'Sets the margin below cruise speed required before Conditional Chill selects Chill.',
  'launch_assist': 'Temporarily selects Chill when a tracked lead moves away from a stop and the launch conditions are met.',
  'persist_manual': 'Remembers your manual driving-mode choice across drives. Changing this setting resets that choice to Automatic.',
}


def purpose(row):
  key = row.key
  if key in DESCRIPTIONS:
    return DESCRIPTIONS[key]
  if key.startswith(('wheel:', 'conditional:button:')):
    return f'Assigns the action performed by {row.label.lower()}.'
  if key.startswith('conditional:'):
    section, field = key.split(':')[1:]
    if field in ('speed_mps', 'speed_with_lead_mps'):
      direction, mode = ('below', 'Experimental') if section == 'cem' else ('at or above', 'Chill')
      lead = 'while following a lead' if field == 'speed_with_lead_mps' else 'without a tracked lead'
      return f'Requests {mode} {direction} this speed {lead}, when the remaining driving conditions qualify.'
    if field == 'lead':
      return ('Requests Experimental when a qualified slower lead is detected.' if section == 'cem' else
              'Allows Chill while following a qualified stable lead above the lead-speed threshold.')
    return CONDITIONS.get(key.rsplit(':', 1)[-1])
  if key.startswith('profile:'):
    parts = key.split(':')
    category = parts[2] if len(parts) >= 3 else None
    if category in CATEGORY_HELP:
      if len(parts) == 3:
        return CATEGORY_HELP[category]
      return {'following': 'Sets following time at this speed. Higher values leave more space.',
              'acceleration': 'Sets the acceleration limit at this speed. Higher values allow stronger acceleration.',
              'braking': 'Sets braking response at this speed. Higher values allow stronger braking.'}[category]
  if key.startswith(('Aggressive', 'Standard', 'Relaxed', 'Traffic')):
    if key == 'TrafficFollow':
      return 'Sets following time at low speeds in Traffic mode, blending toward Relaxed as speed rises.'
    suffix = next((name for name in VALUE_HELP if key.endswith(name)), None)
    value = VALUE_HELP.get(suffix)
    return value + ' Blends toward Relaxed as speed rises in Traffic mode.' if value and key.startswith('Traffic') else value
  if key in SLC_OFFSET_KEYS:
    return 'Adds or subtracts this offset from the accepted speed limit within the displayed speed range.'
  if key.startswith('long_repair:'):
    return 'Restores the default value of this invalid longitudinal setting.'
  if key.startswith('long_reset:'):
    return 'Restores the default following and jerk settings for this driving personality.'
  if key.startswith('display:auto:'):
    return 'Returns this brightness setting to the automatic display policy.'
  if key.startswith('sounds:auto:'):
    return 'Returns this alert volume to the ambient-sound policy.'
  if key.startswith('pip:format:'):
    return 'Selects camera crop dimensions matching the source camera image.'
  if key.startswith('pip:mask:'):
    return 'Moves the camera-image crop center along the indicated axis.'
  if key.startswith('torque:'):
    field = key.split(':')[1]
    return {'factor': 'Sets the lateral-acceleration-to-torque conversion for compatible steering controllers.',
            'friction': 'Sets steering friction compensation around changes in steering direction.',
            'gain': 'Sets how strongly the steering controller responds to tracking error.',
            'ratio': 'Sets the steering-wheel ratio used by the vehicle model.',
            'full_delay': 'Sets the full steering delay used to anticipate the vehicle response.',
            'automatic_delay': 'Chooses a learned or vehicle/custom steering delay.',
            'learning': 'Chooses whether compatible steering geometry and torque learning are used or overridden.',
            'geometry_reset': 'Restores the vehicle steering ratio and full delay.'}.get(field)
  if key in ('torque_adopt', 'torque_rebase', 'torque_gain_rebase', 'torque_reset', 'torque_reset_profile'):
    return {'torque_adopt': 'Keeps the existing steering adjustments in a vehicle-specific tuning profile.',
            'torque_rebase': 'Reviews the saved steering adjustments against the current vehicle tune.',
            'torque_gain_rebase': 'Reviews the saved steering response against the selected controller.',
            'torque_reset': 'Restores the default steering tuning profiles.',
            'torque_reset_profile': 'Restores the vehicle tuning for this model.'}[key]
  return None


def description(row):
  """Keep actual error/prerequisite information after the setting's purpose."""
  base = purpose(row)
  if base is None:
    return row.reason
  suffix = INITIALIZATION.get(row.key, '')
  if row.key.startswith('lane_change:') and row.key not in ('lane_change:close_gap', 'lane_change:close_gap_seconds', 'lane_change:reset'):
    suffix = 'Changes apply when the current signal and maneuver end; turning assistance off cancels it after the preference refresh.'
  if row.key.startswith('torque:') and row.key.split(':')[1] in ('factor', 'friction', 'gain'):
    suffix = ('First activation of a manual tuning owner requires a driving-process restart. An ' +
      'already active owner refreshes saved adjustments during the drive.')
  if row.key == 'ForceAutoTuneOff':
    suffix = ('The native torque learner and controller eligibility are initialized when driving ' +
      'processes start; compatible live learning consumers also recheck the preference.')
  if row.key == 'AlwaysOnLateral' and 'Applies after the next startup' in row.reason:
    suffix = ('The pending vehicle configuration needs the next startup to admit its steering and ' +
      'native button permissions. An already admitted configuration refreshes the steering preference during the drive.')
  if row.key.startswith('wheel:'):
    suffix = ('Axis-pause and AOL assignments require the next vehicle startup to configure the ' +
      'native button permissions. Other actions refresh during the drive.')
  if row.key == 'GMLongitudinalTune':
    suffix = 'ACC Tune refreshes during the drive.'
  warning = row.reason.strip().removeprefix('Saved for this vehicle. ')
  related = any(word in warning.lower() for word in
    ('temporarily ignored', 'temporarily ignores', 'fixed torque conversion', 'inactive while', 'custom display is off',
     'require', 'needs', 'uses its vehicle tune', 'unavailable', 'paused'))
  actionable = warning and (related or (not row.available or row.repair_value) and any(word in warning.lower() for word in
    ('invalid', 'unreadable', 'cannot be read', 'unavailable', 'not supported', 'not available', 'turn on', 'repair', 'not detected', 'paused', 'required')))
  if actionable and warning not in base and warning not in suffix:
    suffix = (suffix + ' ' + warning).strip()
  return ' '.join(value for value in (base, suffix) if value)
