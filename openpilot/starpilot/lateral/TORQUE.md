# Torque controllers and parameter sources

Controller selection and torque parameter selection are separate. `LatControlTorque` uses the standard controller unless the vehicle has an explicit policy, currently Ioniq 6, Genesis G70 2020, Corolla TSS2, the five manual Bolt identities or the five Volt identities. The `AdvancedLateralTune` preference does **not** select a different controller; it enables manual factor/friction adjustments through `TorqueHost`.

## Controller choice

Supported vehicles can save either the standard torque controller or their StarPilot vehicle policy. `LateralControllerSelection` is a versioned JSON document with separate choices by vehicle identity. Missing or invalid settings preserve the existing controller: the vehicle policy for the registered supported platforms, and the standard controller elsewhere. An unsupported vehicle cannot select another vehicle's policy.

`controlsd` resolves the choice once from CarParams before constructing the controller. Later preference changes take effect on the next drive; they cannot change the running controller or carry its integrator into another algorithm. Selecting standard skips vehicle-policy construction, including policy-specific gains and factor multipliers. It retains the vehicle's CarParams, delay estimation and separately configured manual torque values. Automatic learning is a separate preference. It therefore selects a control algorithm, not OEM steering or a complete upstream vehicle tune.

Galaxy and native settings share the preference owner. The owner checks the current vehicle and displayed document before saving, preserves other vehicles' choices, and reports a saved selection rather than claiming that it is already active.

**Automatic Steering Learning** saves an On or Off choice for the exact vehicle brand and fingerprint in `SteeringLearningPreferences`. Controller changes preserve this choice. Vehicles without a saved entry use the valid legacy `ForceAutoTuneOff` preference, or default to On when it is absent; no migration rewrites that legacy value. The UI shows the saved choice separately from controller compatibility. On does not enable incompatible scalar learning or claim an automatic result is applied. Prep My Vehicle for Tuning deliberately saves Off for the prepared vehicle and journals the prior choice for exact restoration.

The runtime uses the same exact-platform learning policy. The running StarPilot controller, including a default or invalid-selection fallback to an established policy, cannot consume learned parameters through either the direct controls path or `TorqueHost`. The `torqued` process samples this policy at startup and, when disabled, skips old caches, sample collection, fitting and cache writes; existing cached data is retained. Standard with a saved learning-off preference also disables learning, even when manual adjustments are off. Unknown platforms keep their established learning behavior. Saved controller and learning changes are intent for the next process startup, not evidence of the currently running controller. Offline `TorqueEstimator` analysis retains estimation by default; the running process explicitly supplies its learning policy.

## Runtime admission

### Preparing a support log

**Prep My Vehicle for Tuning** is a parked On/Off toggle for registered steering-policy vehicles. On saves a durable, vehicle-bound snapshot before selecting StarPilot steering, turning learning/manual adjustments/Turn Assist Off and selecting default lateral lane preferences. Off restores the exact saved bytes, including absent keys, after a restart or interrupted write. Later user edits are preserved and require review rather than being overwritten. Manual torque documents, calibration and longitudinal preferences remain saved. Record a route, then open a tuning request on the StarPilot Discord: https://firestar.link/discord.

The journal is written before preference changes, and every replacement rechecks the vehicle and parked authority. Interrupted preparation is shown as incomplete; switching Off recovers the original snapshot. Invalid controller/lane documents or later user edits block changes without overwriting them.

Start a new drive after preparation and use the ordinary route log. The controls startup event records its actual selected controller/policy, learning decision, initial vehicle source, torque values and PID limits. The usual route data contains vehicle/model/calibration and control behavior; this setup does not claim that a saved request proves a running baseline or vehicle qualification. The preparation journal records saved preference state, not proof of a qualified running tune.

`torque_runtime.py` admits normal Ioniq 6 sessions when manual adjustments are enabled and valid, independently of longitudinal ownership. `TORQUE_REPLAY_RUNTIME=1` additionally permits the exact Toyota/Lexus torque platforms in `torque_supported.py` for offline replay. Admission requires the expected brand, torque steering and lateral tuning, finite valid CarParams values, and a non-dashcam configuration. The five manual Bolt identities also support an explicit, vehicle-bound friction profile; their requirements are described below. Other vehicles keep the standard parameter path and cannot inherit these manual settings.

The source selector does not grant steering, longitudinal or independent-axis permission. Existing controls and vehicle safety checks continue to own actuation.

## Parameter precedence

The source starts with `CarParams.lateralTuning.torque`. When the startup learning policy permits it, fresh, valid version-1 `lateralTorqueParameters` may replace those values within the car-relative limits in `TorqueHost._learned`. Messages must pass the service checks and be no older than two publication periods. `ForceAutoTuneOff` ignores learned values without disabling valid manual overrides.

Manual factor and friction are independent. A manual factor uses the vehicle's original offset; an unchanged field may still use its learned value when learning is allowed, or its vehicle value otherwise. Missing settings do not create overrides. Malformed values or a saved profile requiring review fall back to the vehicle values while preserving the saved data.

`torque_settings.py` stores manual choices by vehicle identity and the factor/offset/friction basis. A changed vehicle basis requires review before reusing its custom values. For the existing Ioniq/Toyota parameter hosts, legacy `SteerLatAccel` and `SteerFriction` are used only when no profile document exists. Bolt does not import these device-wide values. Matching `SteerLatAccelStock` and `SteerFrictionStock` markers identify stock tracking rather than a manual override; these optional keys remain persistent. Legacy comparisons retain two-decimal rounding and are resolved before checking custom-value bounds.

## Application and reset

`TorqueTuning` is the typed boundary between source selection and the controller. `TorqueHost` refreshes saved settings at 1 Hz, checks source freshness every control frame, and rate-limits changes while active. A missing learner falls back to valid manual values or the vehicle tune. Clock discontinuities, invalid settings or loss of lateral permission reset the optional tune to vehicle values. Invalid CAN suppresses lateral output and clears the optional controller's integrator.

In admitted sessions, the torque learner skips old cached CarParams and learned values without deleting them. Other vehicles retain the standard cache policy. No learned cache is automatically translated into a manual profile.

A vehicle policy may convert a selected raw factor before application. The Ioniq 6 policy applies its factor multiplier once; callers must not pre-apply it. `TorqueHost.apply` also avoids redundant initial updates because a vehicle policy's startup PID limits can differ from its later parameter-update limits. Tests under `tests/` cover source selection, vehicle isolation, transitions and driver-override behavior.

## Manual Bolt controller

The manual Bolt ACC 2022–2023, ACC Pedal 2022–2023, CC 2022–2023, CC 2018–2021 and CC 2017 identities default to the StarPilot controller. Their generation policy owns asymmetric torque conversion, feedforward, friction, center shaping and request history. It adjusts the live VehicleModel steering ratio for CC 2017 and CC 2018–2021. Saved STANDARD selection preserves the upstream controller and ratio. Ordinary Bolt EUV keeps its standard controller.

The default Bolt policy uses its established constant proportional gain and generation calibration. Manual friction is available with either controller, independently of automatic learning. The current torque estimator does not authorize learned parameters for GM; this feature preserves that producer policy. Starting the optional parameter host requires manual adjustments On and a valid custom-friction profile for the exact vehicle and current calibration. Enabling it takes effect on the next drive. An absent profile leaves the existing parameter path unchanged; old device-wide tuning values are retained but never activated automatically.

Once started, the host follows the refresh and transition rules above. Returning friction to its source, or turning manual adjustments Off, removes the manual override and uses the admitted learned or vehicle values according to the running learning policy. Invalid settings or inactive lateral control restore vehicle values. The Bolt editor uses the existing calibration-relative friction bounds. It does not expose lateral-acceleration factor editing: the StarPilot Bolt conversion uses a fixed nonlinear curve, and historical factor selections also affected offset selection. Profiles containing that unsupported choice require an explicit reset for this vehicle; other vehicle profiles are preserved.

Manual steering ratio/delay and FLM trial overrides are separate from these ordinary manual preferences. Steering and longitudinal admission remain with the current control and safety owners.

Vehicle-specific torque behavior is selected once in `torque_extension.py`. The upstream controller calls its generic update and parameter hooks; Controls calls its generic vehicle-model ratio hook. Standard selection constructs no extension and retains the upstream numerical path.

## Manual Bolt steering response

The same five manual Bolt identities support a typed custom proportional gain with either selected controller. The editor calls this Steering response: higher values respond more strongly and lower values more gently. Custom values must be finite and within 0.3–0.9. This is independent of friction and automatic learning. A gain-only profile does not start `TorqueHost`, alter torque-parameter sources, or change the estimator cache policy. No raw `SteerKP` value is imported.

A custom gain is bound to the selected controller, its initialized proportional-gain table and the vehicle's current torque calibration. The StarPilot source gain is 0.6; Standard retains its upstream speed table. A changed binding pauses only the gain until explicitly reviewed, preserving friction and other vehicle profiles. Version-1 documents remain readable with no gain override; an explicit gain save adds the version-2 fields.

Starting the gain owner requires manual adjustments On and a qualified custom gain at startup, so enabling it takes effect on the next drive. Once started, saved values refresh at 1 Hz. Returning to Selected controller, turning manual adjustments Off, or invalidating the saved gain restores the captured source table. Changing gain never resets the integrator or request history. This typed refresh contract rejects out-of-range values rather than reproducing the old device-wide knob's clamping and cached reload timing. Steering permission and output limits remain with the existing controllers and safety owners.

## Volt controller

Admitted Volt, Volt ASCM, Volt Camera, Volt CC and Volt 2019 configurations use their StarPilot torque policy by default. It uses the common Volt torque calibration, asymmetric fixed conversion, proportional gain 0.6 and integral gain 0.35. A saved Standard selection preserves the standard torque algorithm and its parameter policy, using the restored vehicle geometry and calibration. The vehicle model uses the selected configuration’s geometry without a policy ratio multiplier. This controller selection does not enable manual torque settings or expand automatic learning.

Original-rack and EPS-retrofit Prius profiles use the pre-rebase Prius torque shaping by default. The two racks retain distinct high-speed output and friction-jerk taper values. An explicit Standard selection remains available; manual and learned torque parameters retain their existing owners.
