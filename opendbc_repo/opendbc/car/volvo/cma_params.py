from opendbc.car.lateral import AngleSteeringLimits


class CarControllerCMAParams:
  STEER_STEP = 1  # 100 Hz LCA command frequency (controlsd runs at 100 Hz)

  # Max commanded-vs-actual steering angle error (deg). Stock Volvo Pilot Assist holds
  # commanded within ~1.7° of actual even under sustained driver override; bounding the
  # command to actual ± this error prevents the stale-command snap-back that causes
  # aggressive post-release overcorrection.
  ANGLE_ERROR = 3.0

  # LCA torque-authority envelope, modeled after stock Pilot Assist behavior.
  # LCA_STEER_LOOSELY (positive arm) and LCA_STEER_LOOSELY_INV (negative arm)
  # form a directional envelope that PSCM applies to its EPS torque. Stock PA:
  #  - holds both arms at saturation (±LCA_AUTH_MAX) when no driver torque
  #  - on driver override, collapses both arms symmetrically at COLLAPSE_RATE
  #    until envelope reaches ~±LCA_AUTH_SPLIT, then splits asymmetrically:
  #    the arm matching driver direction (yielding) settles at ±PLATEAU_YIELD,
  #    the counter arm holds at ±PLATEAU_COUNTER (yield is shallower than counter)
  #  - rebuilds at REBUILD_RATE after release (~3 s back to saturation)
  # See route_analysis/lca_override_mechanism.md for the data behind these.
  LCA_AUTH_MAX = 614              # signal saturation
  LCA_AUTH_PLATEAU_COUNTER = 130  # counter-arm magnitude during sustained override
  # Override trigger thresholds on |CS.out.steeringTorque| (op-convention raw
  # units, mirror of DRIVER_INPUT). Must be ABOVE the resting-hand noise floor
  # (CS.steeringPressed uses |raw|>2 as a sensitive DM-fallback floor and does
  # NOT indicate override intent — don't use it for envelope triggering).
  # Hysteresis: enter override at ENTER, exit at EXIT (< ENTER) to prevent the
  # envelope flapping between collapse and rebuild when driver torque hovers
  # near a single threshold (was causing ~10 Hz EPS-torque ripple in lane
  # changes when driver applied 6-8 raw to "ride along" with op).
  LCA_AUTH_OVERRIDE_ENTER = 5
  LCA_AUTH_OVERRIDE_EXIT = 3
  # "Light contact" / haptic-acknowledgment region. When |drv| crosses into
  # [LIGHT_THRESH, OVERRIDE_THRESH] from below, briefly collapse the envelope
  # for LIGHT_HOLD_FRAMES (a haptic confirmation of hand-on-wheel detection),
  # then rebuild even while the contact persists. Prevents the driver from
  # needing to sustain force just to feel that the system noticed them — helps
  # with hand-fatigue / RSI.
  # Rising edge detected via per-frame derivative; the brief-yield window does
  # NOT re-arm while still active, so a steady elevated torque only triggers
  # one yield and then the envelope rebuilds.
  # Cooldown: light_collapse only fires when real_override has been off for
  # LIGHT_COOLDOWN_FRAMES — suppresses repeated firings during active
  # co-steering (lane changes), where |drv| oscillates and would otherwise
  # re-arm the haptic-ack window each time, causing felt ripple.
  LCA_AUTH_LIGHT_THRESH = 3            # min |drv| to consider as contact
  LCA_AUTH_LIGHT_RISE_DELTA = 1.0      # min per-frame increase in |drv| to count as rising contact
  LCA_AUTH_LIGHT_HOLD_FRAMES = 15      # ~150 ms of yield on fresh light contact
  LCA_AUTH_LIGHT_COOLDOWN_FRAMES = 30  # ~300 ms quiet-time on real_override before light contact re-arms
  # Yield-arm plateau scales with driver-torque magnitude so brief strong presses
  # (potholes, lane corrections) get full yield while light sustained pressure
  # only gets a soft yield. yield_signed = YIELD_BASE − YIELD_SLOPE *
  # max(0, drv_mag_filt − OVERRIDE_ENTER), clamped to [YIELD_MIN, YIELD_BASE].
  # drv_mag_filt is a low-pass of |drv| (alpha=0.04, ~250 ms time constant) —
  # without it, 1-2 unit driver-torque jitter became ~10 unit yield-arm jitter
  # which PSCM converted to felt ripple at sustained co-steering pressure.
  LCA_AUTH_YIELD_BASE = 60        # yield-arm magnitude at the override threshold
  LCA_AUTH_YIELD_SLOPE = 8        # counts of yield reduction per unit |drv torque| above threshold
  LCA_AUTH_YIELD_MIN = 0          # yield authority without crossing the safety sign boundary
  LCA_AUTH_YIELD_LP_ALPHA = 0.04  # LP-filter coefficient on |drv| for yield calc (~250 ms tau)
  LCA_AUTH_SPLIT = 200            # symmetric → asymmetric handover
  LCA_AUTH_REBUILD_RATE = 230     # counts/s (≈ 2.7 s rebuild from 0 to 614)
  LCA_AUTH_COLLAPSE_RATE = 2500   # counts/s base (scales with |drv|/THRESH for sharper pothole jolts)

  # Angle limits for rate limiting
  ANGLE_LIMITS: AngleSteeringLimits = AngleSteeringLimits(
    540, # deg - 1.5 turns to lock
    ([0., 5., 25.], [2.5, 1.5, .2]),  # rate up limits at different speeds
    ([0., 5., 25.], [5., 2., .3]),    # rate down limits at different speeds
  )

  LCA_5_ANGLE_LIMITS = AngleSteeringLimits(
    ANGLE_LIMITS.STEER_ANGLE_MAX,
    (ANGLE_LIMITS.ANGLE_RATE_LIMIT_UP[0], [value * 2 for value in ANGLE_LIMITS.ANGLE_RATE_LIMIT_UP[1]]),
    (ANGLE_LIMITS.ANGLE_RATE_LIMIT_DOWN[0], [value * 2 for value in ANGLE_LIMITS.ANGLE_RATE_LIMIT_DOWN[1]]),
  )
