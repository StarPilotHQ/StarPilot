# StarPilot steering learning

Galaxy’s StarPilot Tuner at `/tuning` shows Auto Learn status, grouped full30
correction coordinates, and reported learning activity. `/tuning/flm` remains an
alias; `/tuning/plots` opens the separate live control plots. The saved vehicle
choice is read independently of the optional learner report, so Off remains
visible when the learner is stopped. Historical reports, current controller
acknowledgement, accepted corrections and disk saving are separate states.
Recorded-log analysis, manual GM trial generation, their worker and CLI have
been removed. Shared log readers remain for route summaries and downloads.

## Ioniq 6 torque-surface core

`torque_surface.py` is a pure, inactive reconstruction of the frozen Ioniq 6
surface stages. It accepts one immutable `standard` or `firmware_2025` profile
with finite knobs checked against the historical Ioniq-specific numeric bounds;
those bounds are not a live-trial or safety qualification. The
controller's existing firmware-marker helper, not a model-year guess, must
select the variant in any future integration. It returns center deadband,
center taper, the directional-taper **target**, FF scale with an explicitly
supplied filtered taper, friction threshold and low-speed angle-assist output.
The filtered taper is required: the caller computes the raw target, updates
its existing directional filter, then calls the pure evaluator. Its FF scale
excludes center taper, so multiply those once; the returned threshold already
includes the center-taper division.
The controller still owns its filter, PID, 2023 unwind term, friction fade,
highway output tapers, 2025 output limit and reset order. Missing or invalid
profile data must leave the current Ioniq controller entirely unchanged.

The Ioniq branch in the frozen controller already consumed its own FLM knobs;
the frozen generic full-surface FF/friction branch expressly excluded Ioniq.
Applying both would double shape FF. This module has no Params reader, gate or
trial selector. Its output is a computation contract, not a qualified tune or
permission to actuate. Reference provenance: frozen
`latcontrol_vehicle_tunes.py` SHA256
`2a7793c6ee4b53b45070d3267a01e8c7105dfd8df9d38cdfc96cfe4b672a125b`;
baseline `ioniq6_policy.py` before the replay seam, SHA256
`398353f28e8a367acb300a45dabc61967977aa5e952dd12888e1663b3b864bfb`.

Historical numeric admission is not safe combination admission: setting all
30 knobs to their allowed highs yields a negative directional taper near
18 m/s, +0.9 m/s² setpoint and -3 m/s³ jerk. The pure module preserves this
negative result and accepts a signed finite filtered-stage value for offline
parity. It never clips or authorizes it. Any future live profile owner must
reject or separately qualify coordinated knob combinations before application.

`Ioniq6TorquePolicy` accepts an optional immutable surface at construction for
offline replay. It validates exact Ioniq identity and the firmware-derived
variant before modifying controller state. The normal controller constructor
supplies no surface. Replay uses the same PID, request buffer, directional and
jerk filters, driver override/reset order, and final output stages as the
default Ioniq controller. No saved-profile or live selection path exists.

Standalone frozen-controller fixtures cover both firmware variants, default
and tuned surfaces, left/right transitions, driver override, reset, saturation,
and the first actual torque-parameter update. That longer comparison exposed
and corrected a prior startup-limit mismatch: the original Ioniq PID limits
use the raw vehicle calibration until a learned/custom parameter update. The
1.22 conversion factor alone must not silently widen those initial limits.
