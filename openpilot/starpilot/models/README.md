# Driving models

Model Manager installs the versioned catalog into `/data/models/v26`. Downloads use the catalog's declared size and SHA-256, publish atomically, and share progress and cancellation between Galaxy and the native menus. Small and Chestnut selections take effect when modeld next starts. Randomization chooses only installed, verified models for the available hardware and respects exclusions; favorites are a separate browsing preference.

Catalog behavior versions describe model inputs and outputs. The artifact ABI and compiler revision separately identify runtime compatibility. Existing compiled artifacts can be migrated when their kernels and state can be preserved and verified; changing the version label alone does not establish compatibility. Unavailable catalog entries cannot be downloaded or selected.

`modeld` records the loaded artifact digest, process identity, variant, and fallback reason in a bounded volatile receipt. Status requires a matching running process and fresh valid model outputs before reporting an active model. A failed Chestnut startup requests isolated Small recovery; inference failure falls back to the initialized Small runner. Artifact integrity and bench inference do not establish on-road driving quality.

## Conditional Model: automatic local model switching

Galaxy Models has three settings tabs: **Single Model**, **Split Model** and
**Conditional Model**. Single Model handles all driving. Split Model assigns
steering and speed to separate models using Model Laboratory and Chestnut.
Conditional Model switches the entire local Small runner according to speed;
no external GPU is required. Browsing a settings tab does not activate a mode.

Configure City, Highway and the Highway speed while offroad, then enable
Conditional Model. The speed uses the same `GalaxySettingRow` slider as Toggles,
including fine scrubbing, default reset and save feedback. It defaults to
**50 mph** and accepts 10–100 mph. Every normal drive starts with **City**.
There is no manual road selection, configurable starting mode or cooldown input.
The separate Single Model choice remains saved when Conditional Model is enabled.

Both verified runners load and warm sequentially at drive startup and remain
resident until normal process teardown. Only one runs per frame, with independent
history buffers. Identical City/Highway assignments use a single runner.
Missing/corrupt artifacts, unsupported state mapping or insufficient memory
headroom disable switching and retain the initialized fallback. Chestnut, Split
Model and Jetlink retain their existing precedence and fallback behavior;
expected external-model precedence is informational, not a driver fault alert.

### Speed selection and interlock

Highway is requested at the exact configured speed, without a delay.
City requires **30 continuous seconds** at least **5 mph below** that speed;
both guards are internal constants. A subsequent Highway request can happen
immediately, avoiding a cooldown delay during a merge. Repeated City entries
always require another complete low-speed interval. Speed is a condition hint,
not geographic road classification: slow highway traffic and fast local roads
can be misclassified.

Stale, missing, reordered, nonfinite or implausible speed samples reset the City
timer. Sampling continues while engaged, but crossing a threshold cannot acquire
an engagement hold or switch a runner. Requests require fresh valid model output,
fresh disengaged vehicle/control state and a fresh idle acknowledgment. There
is no preferences-reading thread, onroad configuration API or filesystem I/O in
the automatic request path.

Selfdrived grants the session/request-bound hold only while ordinary control is
disabled and both independent axes are inactive, unrequested and disarmed. Stock
cruise must also be off. Same-tick engagement loses to the existing no-entry
protection; the final independent-axis publication is inhibited as well. Standing
still never permits an engaged switch. Missing acknowledgments time out after
one second with the current runner retained.

The target's preallocated state is reset in place, preserving captured JIT buffer
addresses. Action smoothing and lane-change state restart at the handoff. Existing
model, driving-data, odometry, lane-change and identity messages keep their sole
original publisher. The hold spans five valid output frames and releases only
when selfdrived sees fresh output at or after completion. Stale/missing status,
old output and expired timers never release a hold. A restarted process must
establish fresh output before a prior hold can clear. There is no automatic
re-engagement.

### Failure recovery and driver notices

A target reset/inference/action failure retains or restores the previous model;
five valid recovery frames are required before releasing an established hold.
Failures pause further switching for the drive. A previous-runner reset failure
exits under the hold instead of running potentially corrupted history.

A two-second handoff timeout rolls back when disengagement is established.
Manager bounds startup preload to 120 seconds. During an acknowledged hold, a
native inference stall with progressing cameras triggers bounded SIGINT/SIGKILL
escalation. The old process must exit before a single-model recovery process can
start using the prior model. An established runner cannot be reclassified as a
preload merely because its receipt disappears. An arbitrary established/engaged
Small crash does not authorize loading another model. Recovery overrides do not
change the next drive's City startup.

The native alert manager displays **Switching conditional models** throughout a
held handoff. Genuine failures and prolonged holds produce visible notices.
Notices yield to equal/higher-priority current or retained vehicle alerts, and
completion removes only Conditional Model notices. Expected engagement, short
message gaps, ordinary speed changes and external-model precedence do not create
failure alerts.

Galaxy shows the actual verified running model, switching and failure state.
An enabled setting is configuration, not proof that a runner is active.
`POST /api/models/conditional_model` accepts only `enabled`, `city`, `highway` and
`thresholdMph`, and only offroad. Legacy `smallProfiles` data is read at one
migration boundary; model assignments, enablement and speed migrate, while
manual selection, pending manual requests and custom cooldowns are discarded.
The next save writes only the canonical `conditionalModel` shape.

### Module boundaries and qualification

`conditional_model.py` owns configuration and City startup selection;
`conditional_policy.py` owns speed/hysteresis clocks; `conditional_protocol.py`
owns the pure held handoff; `conditional_runtime.py` adapts modeld/selfdrived;
`conditional_alerts.py` supplies native notices. Tests cover migration, removed
controls/APIs, City startup/recovery, shared sliders, speed boundaries, anti-flap
behavior, invalid/gapped inputs, engagement races, independent axes, real typed
IPC, timeouts, rollback, watchdogs and native-alert priority/retirement.

Five output frames represent about 250 ms at 20 Hz, plus reset, inference and
handshake overhead; actual QCOM timing remains unmeasured. Standard Small files
occupy 57.9–85.6 MiB each; disk size is not a device-residency bound. Startup checks
host memory headroom (512 MiB plus four times the alternate artifact size), a
heuristic rather than accelerator-memory qualification. `modeld.catalog_load`
logs verification/construction/warmup durations and peak process RSS; RSS does
not measure device allocations. Host tests cannot certify 100% safety, memory
capacity, driving quality or device timing. Target bench validation remains
required. Small upstream hooks still require review during merges.

## Model Laboratory

Laboratory artifact management supports separate AMD variants of Small models
and Chestnut-class large models. At startup the frozen request can substitute
an installed native Small counterpart for one Small role while retaining a
Chestnut-backed role. The manifest declares AMD variants under
`accelerator_artifacts.amd`, with `execution_device: "AMD"`, the current
`compiler_revision`, and the same artifact format, digest, size, and chunk-count
fields as ordinary catalog artifacts. Files are named
`<id>_driving_amd_tinygrad.pkl` under the model's existing catalog directory.
Deleting an AMD variant preserves the ordinary on-device model.

The Galaxy page and configuration owner are available. The pair runner verifies
both role artifacts, shares compatible camera preprocessing, keeps each model's
state separate, and rejects the whole frame if either role fails. It decodes each
model's own action units before choosing lateral curvature and longitudinal
acceleration/stopping. The compositor assigns lateral geometry and the
corresponding plan columns to the lateral model, and leads, scene metadata, and
current-frame pose to the longitudinal model.

Paired inference is integrated into modeld through a frozen startup request and
pair receipts. Production advertises `runtimeSupported: true`; configuration
requires Chestnut and at least one Chestnut-backed role. Failed pair startup uses
the existing isolated Small recovery, and inference failures fall back to the
initialized Small runner. CPU state-isolation and mocked frame tests do not
establish device memory, timing, or on-road driving quality.

Jetlink is a separate remote model connection for comma 3X and comma 4. In Galaxy
Model Manager, choose **Off**, **USB computer**, or **iPhone / iPad** while parked.
Off is the default. USB covers supported Jetson, Mac, Linux, and Android hosts;
iPhone uses the direct USB connection. Chestnut takes precedence when fitted.
Wireless Android Auto can remain enabled.

Use a USB 3 data cable and separate power, and set up both devices online while
parked. Keep the computer awake; keep the iPhone app open and unlocked. Follow
[the StarPilot JetLink setup guide](JETLINK.md)
for the host installation and model preparation. Galaxy shows connection and
preparation progress; **prepared** does not mean the remote model is driving.
Small/Chestnut catalog selections and their defaults remain separate. Phone
charging over USB is optional and starts off.

For a remote model switch, fully disengage cruise and any lateral control that
stays on without cruise. Wait for Galaxy's fresh **Active · Jetlink remote model**
status before engaging again. A lost or stalled link falls back to the local
Small model and stays engaged with a **Big Model Lost** warning; remain ready to
take over while its behavior changes.
