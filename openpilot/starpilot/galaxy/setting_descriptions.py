"""Descriptions of existing settings; no preferences, defaults or runtime state.

Rows and their availability still come from the canonical feature owners.
This presentation catalog supplies the purpose before any genuine prerequisite.
"""
from openpilot.starpilot.ui.long_profile_feature import CATEGORY_HELP, VALUE_HELP
from openpilot.starpilot.ui.feature_settings_state import SLC_OFFSET_KEYS
from openpilot.starpilot.ui.slc_custom_feature import KEYS as SLC_CUSTOM_KEYS, SOURCE_HELP as SLC_SOURCE_HELP

DESCRIPTIONS = {
  'GreenLightAlert': 'Chimes once when the model’s traffic-light stop prediction clears while you remain stopped.',
  'LeadDepartingAlert': 'Chimes once when the tracked vehicle ahead starts moving while you remain stopped.',
  'DriveModeProfiles': 'Matches acceleration and deceleration profiles to the vehicle’s Eco, Normal or Sport drive mode.',
  'DriveModeAcceleration': 'Matches the acceleration profile to the vehicle’s drive mode.',
  'DriveModeDeceleration': 'Matches the deceleration profile to the vehicle’s drive mode.',
  'AggressiveCoolingEnabled': 'Uses a stronger fan curve on supported devices to reduce temperature at the cost of fan noise.',
  'HighwaySmoothingLevel': 'Reduces small steering corrections on highways above about 40 mph. 0% is off; higher values add more smoothing.',
  'FordRearFogIndicator': 'Shows a movable amber telltale while the Ford rear fog light is on, using read-only vehicle status.',
  'DisengageOnAccelerator': 'When enabled, pressing the accelerator pedal will disengage openpilot.',
  'ReduceMotion': 'Reduces interface animations for a steadier display.',
  'IsMetric': 'On displays speed and distance in kilometers; Off uses miles. Saved speed-limit offsets keep their physical values.',
  'IsRhdDetected': 'Sets the initial steering-wheel side used by driver monitoring. Applies next drive; automatic side detection continues.',
  'lane_change:turn_signal_guidance': 'Uses your turn signal to help the model choose a turning path at intersections.',
  'AlphaLongitudinalEnabled': ('Let StarPilot control acceleration and braking instead of factory ACC. ' +
                              'Takes effect next drive. May disable factory Automatic Emergency Braking.'),
  'lane_change:enabled': 'Enables lane-change assistance after you signal. With Automatic Lane Changes off, give the wheel a nudge to begin.',
  'lane_change:pace': 'Sets how quickly steering moves into the adjacent lane. Higher values change lanes more quickly; lower values make steering gentler.',
  'lane_change:speed': 'Sets the lowest speed at which lane-change assistance can begin.',
  'lane_change:one': 'Allows one assisted lane change until you release the turn signal.',
  'lane_change:require_cruise': ('Limits nudgeless lane changes to when factory cruise or openpilot speed control is engaged. ' +
    'With cruise off, use a wheel nudge. If cruise engages after signaling, release and reapply the signal. ' +
    'Off also allows automatic changes during lateral-only driving; lane and blindspot checks still apply.'),
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
  'ForceAutoTuneOff': ('StarPilot’s FLM tuning will attempt to automatically correct torque profiles as you drive. ' +
                       'On allows automatic learning; Off disables it. Nonzero manual FLM overrides pause Auto Learn.'),
  'torque_prepare_firestar': 'Prepares steering settings for a tuning-request recording. Turning it off restores the previous settings.',
  'SteerLatAccel': 'Sets the legacy lateral-acceleration-to-torque conversion. Review the vehicle-specific tuning profile before applying it.',
  'SteerFriction': 'Sets legacy steering friction compensation. Review the vehicle-specific tuning profile before applying it.',
  'UseStarPilotLongitudinalPlanner': 'Selects the StarPilot acceleration, braking and following planner. Off selects the upstream planner.',
  'CustomCruise': 'Sets how much a short cruise-button press changes the set speed.',
  'CustomCruiseLong': 'Sets how much holding a cruise button changes the set speed.',
  'ReverseCruise': 'Swaps the Toyota short-press and held-press cruise-speed increments.',
  'ForceStops': 'Improves stopping behavior at traffic lights and enables automatic stopping at detected stop signs. Press the accelerator to continue.',
  'ForceStopDistanceOffset': 'Moves the planned stopping point. Positive values stop farther forward; negative values stop earlier.',
  'LeadApproachBuffer': 'Gradually adds following distance when approaching a slower or braking lead vehicle.',
  'ExperimentalSpeedNudge': ('Gently recovers toward set speed when Experimental mode hesitates on a clear road. ' +
                            'Leads, stops, curve limits and driver overrides retain priority.'),
  'FasterLeadTakeoff': ('Improves launches from a stop and responds sooner when a lead starts moving. ' +
                        'Selected acceleration profiles, stop detection and vehicle limits still apply.'),
  'CustomPersonalities': 'Uses your following-time and jerk settings for the selected driving personality.',
  'profile:stopped_gap': 'Adds this distance to the following gap behind a stopped lead vehicle.',
  'profile:global_acceleration': 'Chooses the acceleration curve used by personalities set to Selected Profile.',
  'profile:global_braking': 'Chooses the braking response used by personalities set to Selected Profile.',
  'reset_profiles': 'Restores the default acceleration, braking and following curves and presets.',
  'CurveSpeedController': 'Adjusts cruise speed for curves ahead using available learned cornering preferences.',
  'CurveSpeedControllerNoLead': 'Pauses curve-speed adjustments while following a lead vehicle.',
  'ShowCSCStatus': 'Shows when Curve Speed Controller is adjusting speed on the driving screen.',
  'curve_reset': 'Deletes saved cornering learning so Curve Speed Controller can learn your preferences again.',
  'SpeedLimitController': ('On: StarPilot keeps you to the speed limit plus your offset. The limit caps your set speed, or becomes ' +
                           'your set speed with SLC sets the set speed on (always on a Tesla Model 3 or Y). Off: the set speed is ' +
                           'left to you.'),
  'SLCSetsSetSpeed': ('Off: the speed limit with your offset is a maximum under your own set speed, and your set speed never ' +
                      'changes. On: the speed limit with your offset becomes your set speed, so the set speed follows each new ' +
                      'limit and the speed buttons change it from there. Only on cars where StarPilot controls the set speed.'),
  'SLCOnEngage': ('Starts software cruise SET at the SLC limit plus offset. ' +
                  'A limit accepted this session remains available during data gaps. Previous-drive history does not initialize SET. ' +
                  'RES restores the previous SET.'),
  'SLCOverrideMode': ('Cruise uses this target when you lift off the accelerator. ' +
                      'Holding your speed uses the highest speed you reached, up to SET.'),
  'SLCWheelOverride': 'Press + to raise SET past the adjusted limit and use your new SET speed.',
  'ShowSpeedLimits': 'Shows recognized speed-limit signs on the driving screen.',
  'SLCConfirmation': ('Off adopts new limits automatically. Both, Higher only, and Lower only prompt relative to the last accepted limit. ' +
                      '+ accepts and − declines within 30 seconds.'),
  'SLCAdvanceHigherSeconds': ('Offers higher mapped limits early using road distance and current speed. ' +
                             'Existing adoption and confirmation settings still apply.'),
  'SLCAdvanceLowerSeconds': ('Offers lower mapped limits early using road distance and current speed. ' +
                            'Existing adoption and confirmation settings still apply.'),
  'SLCPriority1': SLC_SOURCE_HELP['SLCPriority1'],
  'SLCPriority2': SLC_SOURCE_HELP['SLCPriority2'],
  'SLCPriority3': 'Used when neither of the first two sources has a speed limit. Off: no third source.',
  'SLCLastLimitFallback': ('On: when no source has a speed limit at the start of a drive, StarPilot uses the last limit it ' +
                           'accepted on an earlier drive. Off: it waits for a source.'),
  'slc_reset': 'Resets saved speed-limit offsets while retaining valid configured speed ranges.',
  'reset_calibration': 'Clears saved camera and steering calibration. The device recalibrates on the next drive.',
  'reset_driver_monitoring': 'Clear saved wheel-side detection. Automatic detection restarts on the next drive.',
  'AlwaysAllowUploads': 'Allows queued full logs and videos to upload over metered networks. This uses mobile data.',
  'UseVOACC': ('Use vision-only adaptive cruise control instead of radar-assisted lead detection. ' +
               'Requires StarPilot longitudinal control. Applies next drive; radar faults and safety checks remain active.'),
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
  'TeslaAOLDisengageOnBrake': 'Disarms Tesla Always On Lateral when you press the brake, with either harness.',
  'RemoteStartBootsComma': 'Wakes comma during GM remote start.',
  'HKGRemoteStartBootsComma': 'Wakes comma during Hyundai, Kia or Genesis remote climate operation.',
  'IgnoreIgnitionLine': 'Uses GM CAN ignition instead of the harness ignition signal.',
  'heading:starport': 'Settings for connecting Android Auto to the car through StarPort.',
  'StarPortNewSetup': 'Shows the guided StarPort setup with a live checklist instead of the classic setup screens.',
  'StarPortAllowHevc': 'Lets a car that offers only H.265 video receive H.265; a car with an H.264 mode keeps H.264.',
  'StarPortHeadUnitExperiments': 'Shows experimental fixes for specific cars and dongles, each with its own switch. Off is the standard connection.',
  'StarPortDedicatedWifi': "Joins the car's Wi-Fi on a second interface so the comma stays on its home Wi-Fi or phone hotspot.",
  'TeslaWakeOnCAN': 'Wakes comma when the Tesla wakes up, before you press the brake.',
  'RadarRecoveryAlert': 'Play a chime and show a brief message when a radar fault clears. You must re-engage manually.',
  'CameraView': 'Chooses the camera view shown on the driving screen.',
  'DeveloperSidebar': 'Shows live developer metrics on the right of the Big driving screen. Off gives that space back to the road view.',
  'DriverCamera': 'Shows the driver camera while reversing.',
  'StockConfidenceBallWidget': 'Uses StarPilot driving widgets in place of the stock confidence ball.',
  'HidePersonalityAlerts': 'Hides the alert shown when the driving personality changes on the comma and Android Auto.',
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
  'SLCWarningVolume': 'Sets the volume of the chime before Speed Limit Controller changes speed for a new limit. 0% mutes it.',
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
  'FordPrefLateralControl': ('Chooses BluePilot Curvature, BluePilot Angle or StarPilot Controller. Mach-E and classic Explorer' +
    ' default to StarPilot; other supported Fords default to BluePilot Curvature. Lane centering and offset use the global' +
    ' StarPilot settings. Change while parked.'),
  'FordPrefSteerAngleCurvature': ('Measures curvature from the steering pinion angle instead of the yaw-rate sensor, in control and' +
    ' Panda safety. For Fords with a faulty yaw sensor.'),
  'enable_human_turn_detection_curv': 'Eases off steering while you turn the wheel through a turn yourself.',
  'lane_change_factor_high_curv': 'Scales steering during a lane change. Values under 1.0 soften it.',
  'custom_profile_curv': 'Uses the two predicted-curvature blend settings below instead of the built-in profile.',
  'pc_blend_ratio_low_C_UI_curv': 'Mixes predicted curvature into gentle curves: 0 follows the plan, 1 follows the prediction.',
  'pc_blend_ratio_high_C_UI_curv': 'Mixes predicted curvature into sharp curves: 0 follows the plan, 1 follows the prediction.',
  'FordLowSpeedFactor_ang': 'Scales the low-speed steering response in angle mode. 1.0 is the default feel.',
  'FordHighSpeedFactor_ang': 'Scales the high-speed steering response in angle mode. 1.0 is the default feel.',
  'FordHighSpeedDampening_ang': ('Tunes the straight-road response at highway speed in angle mode: lower it if the car oversteers,' +
    ' raise it if it understeers.'),
  'lane_change_factor_high_ang': 'Scales steering during a lane change in angle mode. Values over 1.0 strengthen it.',
}

# These are initialization explanations, not a second defaults table.
INITIALIZATION = {
  'StarPortNewSetup': 'Applies the next time the setup screen opens.',
  'StarPortAllowHevc': 'Applies from the next StarPort connection.',
  'StarPortHeadUnitExperiments': 'Applies from the next StarPort connection.',
  'StarPortDedicatedWifi': 'Applies from the next StarPort connection.',
  'LateralControllerSelection': ('Applies when driving processes next start: the controller class, vehicle policy and ' +
    'PID/filter state are constructed together.'),
  'UseStarPilotLongitudinalPlanner': 'Applies when the planner next starts: each choice runs a different planner process with its own subscriptions and state.',
  'TurnAssist': ('Applies when driving processes next start: the selected torque controller creates its ' +
    'low-speed feedback controller and filter state at initialization.'),
  'ToyotaAutoHold': 'Requires the next vehicle startup to configure the brake-hold permission in CarParams and Panda safety.',
  'DisableOpenpilotLongitudinal': 'Requires the next vehicle startup to rebuild cruise ownership and Panda safety configuration.',
  'GMAutoHold': 'First enable requires the next vehicle startup to configure the brake-hold safety permission. An already admitted hold can be disabled live.',
  'VoltOnePedalMode': ('First enable requires the next vehicle startup to configure the braking safety ' +
    'permission. An already admitted mode can be disabled live.'),
  'VoltSNG': 'Applies when the vehicle controller next starts: its fresh resume-plan input is created for the selected stop-and-go mode.',
  'TeslaAOLScreenTap': 'Requires the next vehicle startup to configure the screen-button CAN source and matching safety permission.',
  'TeslaAOLDisengageOnBrake': 'Requires the next vehicle startup to configure the Tesla brake/disarm safety permission.',
  'RemoteStartBootsComma': 'Requires a device restart to load the matching Panda ignition firmware.',
  'HKGRemoteStartBootsComma': 'Requires a device restart to load the matching Panda ignition firmware.',
  'IgnoreIgnitionLine': 'Requires a device restart to load the matching Panda ignition firmware.',
  'TeslaWakeOnCAN': 'Requires a device restart to load the matching Panda ignition firmware.',
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


FLM_DESCRIPTIONS = {
  'center_taper_max': (
    'Softens planned steering near straight ahead. Higher reduces small steering corrections more; lower keeps a ' +
    'firmer response. This can calm weaving, but too much can make centering less precise.'),
  'highway_center_taper_max': (
    'Adds straight-ahead softening at highway speeds. Higher softens small corrections more; lower keeps more ' +
    'steering response. This changes steering feel, not your target position in the lane.'),
  'low_speed_angle_assist_max_torque': (
    'Adds low-speed steering help when the wheels have not reached the requested turn angle. Higher allows more ' +
    'extra help; lower limits it. This is an extra-assistance limit, not the car’s overall steering limit.'),
  'curvy_speed_min': (
    'Sets the lower end of the curve-exit smoothing speed range. Higher starts the effect at faster speeds; lower ' +
    'extends it to slower speeds. This changes where steering smoothing applies, not cruise speed.'),
  'curvy_speed_max': (
    'Sets the upper end of the curve-exit smoothing speed range. Higher extends the effect to faster speeds; ' +
    'lower ends it at slower speeds. This changes where steering smoothing applies, not cruise speed.'),
  'curvy_turn_in_trim_speed_min': (
    'Sets the lower end of the curve-entry softening speed range. Higher starts the effect at faster speeds; ' +
    'lower extends it to slower speeds. This changes where Turn-in trim applies, not cruise speed.'),
  'curvy_turn_in_trim_speed_max': (
    'Sets the upper end of the curve-entry softening speed range. Higher extends the effect to faster speeds; ' +
    'lower ends it at slower speeds. This changes where Turn-in trim applies, not cruise speed.'),
  'center_deadband_crawl_deg': (
    'Ignores very small steering tracking errors at crawling speeds. Higher tolerates a larger error before ' +
    'correcting; lower reacts to smaller errors. This can reduce constant tiny corrections, but too much allows ' +
    'more tracking error. Blends with the neighboring speed settings.'),
  'center_deadband_low_deg': (
    'Ignores very small steering tracking errors at low speeds. Higher tolerates a larger error before ' +
    'correcting; lower reacts to smaller errors. This can reduce constant tiny corrections, but too much allows ' +
    'more tracking error. Blends with the neighboring speed settings.'),
  'center_deadband_mid_deg': (
    'Ignores very small steering tracking errors at medium speeds. Higher tolerates a larger error before ' +
    'correcting; lower reacts to smaller errors. This can reduce constant tiny corrections, but too much allows ' +
    'more tracking error. Blends with the neighboring speed settings.'),
  'center_deadband_fast_deg': (
    'Ignores very small steering tracking errors at higher speeds. Higher tolerates a larger error before ' +
    'correcting; lower reacts to smaller errors. This can reduce constant tiny corrections, but too much allows ' +
    'more tracking error. Blends with the neighboring speed settings.'),
  'center_deadband_highway_deg': (
    'Ignores very small steering tracking errors at highway speeds. Higher tolerates a larger error before ' +
    'correcting; lower reacts to smaller errors. This can reduce constant tiny corrections, but too much allows ' +
    'more tracking error. Blends with the neighboring speed settings.'),
  'ff_gain_left': (
    'Sets the extra planned steering effort in left curves, before tracking-error corrections. Higher adds more ' +
    'effort; lower adds less. Negative values reduce the baseline effort. This is the FF gain used by the ' +
    'entry-boost and unwind settings.'),
  'turn_in_boost_left': (
    'Strengthens the FF-gain adjustment while entering a left curve, mainly at lower speeds. Higher strengthens ' +
    'that adjustment; lower weakens it. A positive FF gain adds effort; a negative gain reduces it. No effect ' +
    'with FF gain at zero.'),
  'unwind_taper_left': (
    'Fades the FF-gain adjustment as a left turn straightens out. Higher fades it more; lower keeps more of it ' +
    'during the exit. No effect with FF gain at zero.'),
  'turn_in_threshold_reduction_left': (
    'Makes friction compensation respond sooner to small tracking errors when entering a left curve. Higher makes ' +
    'that response sharper; lower makes it more gradual. Changes the response ramp, not maximum friction ' +
    'compensation. Torque controllers only; no PID friction effect.'),
  'unwind_threshold_increase_left': (
    'Softens friction compensation while straightening out of a left curve, mainly at higher speeds. Higher ' +
    'builds compensation more gradually; lower responds more sharply. Changes the response ramp, not maximum ' +
    'friction compensation. Torque controllers only; no PID friction effect.'),
  'crawl_turn_in_ff_boost_left': (
    'Adds planned steering effort while starting a left turn at very low speeds. Higher adds more help; lower ' +
    'adds less. Unlike Turn-in boost, this can act with FF gain at zero.'),
  'curvy_turn_in_trim_left': (
    'Softens planned steering while entering a left curve within the Turn-in trim speed range. Higher softens the ' +
    'entry more; lower keeps more steering effort. Too much can make turn-in feel weak.'),
  'curvy_unwind_floor_relief_left': (
    'Allows the FF-gain adjustment to fade further when exiting a left curve within the curve-exit speed range. ' +
    'Higher allows more fading; lower retains a higher minimum. Works with Unwind taper or Curvy unwind extra ' +
    'reduction; no effect with FF gain at zero.'),
  'curvy_unwind_extra_reduction_left': (
    'Adds more fading of the FF-gain adjustment when exiting a left curve within the curve-exit speed range. ' +
    'Higher fades it more; lower keeps more. The fade can be limited by Curvy unwind floor relief. No effect with ' +
    'FF gain at zero.'),
  'ff_gain_right': (
    'Sets the extra planned steering effort in right curves, before tracking-error corrections. Higher adds more ' +
    'effort; lower adds less. Negative values reduce the baseline effort. This is the FF gain used by the ' +
    'entry-boost and unwind settings.'),
  'turn_in_boost_right': (
    'Strengthens the FF-gain adjustment while entering a right curve, mainly at lower speeds. Higher strengthens ' +
    'that adjustment; lower weakens it. A positive FF gain adds effort; a negative gain reduces it. No effect ' +
    'with FF gain at zero.'),
  'unwind_taper_right': (
    'Fades the FF-gain adjustment as a right turn straightens out. Higher fades it more; lower keeps more of it ' +
    'during the exit. No effect with FF gain at zero.'),
  'turn_in_threshold_reduction_right': (
    'Makes friction compensation respond sooner to small tracking errors when entering a right curve. Higher ' +
    'makes that response sharper; lower makes it more gradual. Changes the response ramp, not maximum friction ' +
    'compensation. Torque controllers only; no PID friction effect.'),
  'unwind_threshold_increase_right': (
    'Softens friction compensation while straightening out of a right curve, mainly at higher speeds. Higher ' +
    'builds compensation more gradually; lower responds more sharply. Changes the response ramp, not maximum ' +
    'friction compensation. Torque controllers only; no PID friction effect.'),
  'crawl_turn_in_ff_boost_right': (
    'Adds planned steering effort while starting a right turn at very low speeds. Higher adds more help; lower ' +
    'adds less. Unlike Turn-in boost, this can act with FF gain at zero.'),
  'curvy_turn_in_trim_right': (
    'Softens planned steering while entering a right curve within the Turn-in trim speed range. Higher softens ' +
    'the entry more; lower keeps more steering effort. Too much can make turn-in feel weak.'),
  'curvy_unwind_floor_relief_right': (
    'Allows the FF-gain adjustment to fade further when exiting a right curve within the curve-exit speed range. ' +
    'Higher allows more fading; lower retains a higher minimum. Works with Unwind taper or Curvy unwind extra ' +
    'reduction; no effect with FF gain at zero.'),
  'curvy_unwind_extra_reduction_right': (
    'Adds more fading of the FF-gain adjustment when exiting a right curve within the curve-exit speed range. ' +
    'Higher fades it more; lower keeps more. The fade can be limited by Curvy unwind floor relief. No effect with ' +
    'FF gain at zero.'),
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
      if len(parts) == 4 and parts[3] == 'slc_limit':
        return row.reason  # each personality's SLC acceleration and braking limits carry their own text
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
  if key in SLC_CUSTOM_KEYS:
    return row.reason  # the Speed Limit Controller settings explain themselves, with examples
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
  if key.startswith('torque:surface:'):
    return ('Removes the thirty manual steering overrides while keeping saved Auto Learn history.'
            if key == 'torque:surface:reset' else
            FLM_DESCRIPTIONS.get(key.removeprefix('torque:surface:')))
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
  if row.key in ('CustomCruise', 'CustomCruiseLong') and row.label in ('Short scroll', 'Fast scroll'):
    return row.reason  # a Tesla's right scroll wheel steps describe themselves (and say when Match is needed)
  base = purpose(row)
  if base is None:
    return row.reason
  suffix = INITIALIZATION.get(row.key, '')
  if row.key.startswith('lane_change:') and row.key not in ('lane_change:close_gap', 'lane_change:close_gap_seconds', 'lane_change:reset'):
    suffix = 'Changes apply when the current signal and maneuver end; turning assistance off cancels it after the preference refresh.'
  if row.key.startswith('torque:surface:'):
    suffix = row.reason.strip()
    if row.key != 'torque:surface:reset':
      suffix = ('Zero keeps this stage’s original baseline. The % is an adjustment to this stage, ' +
                'not a percentage of overall steering strength. ' + suffix).strip()
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
