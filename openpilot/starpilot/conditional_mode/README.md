# Conditional longitudinal modes

CEM selects Experimental mode for configured speed, road, signal, curve, lead, stop, or SLC conditions. Conditional Chill starts in Experimental and selects Chill after its configured speed, lead, or launch conditions qualify. Both retain the frozen StarPilot `678af783` behavior where supported, with explicit source freshness and separate authority checks. They never grant lateral or longitudinal engagement.

## Runtime ownership

`ConditionalPlannerHost` joins the current native MPC result, current model/car/radar observations, saved preferences, and qualified wheel-button events. It produces a short-lived proposal on `slcState.conditionalMode`; it does not own the effective driving mode. `selfdrived` independently checks the current vehicle capability, longitudinal axis, drive, saved configuration, and input freshness before accepting that proposal. Its ordinary `ExperimentalMode` request remains the fallback. The UI displays an active conditional reason only from a fresh selfdrived acknowledgment matching the exact current `selfdriveState` frame and active longitudinal axis; the saved family is displayed separately.

Speed-limit availability is independent of mode selection. A missing or stale Maps or Vision limit does
not request Experimental or veto Chill. Other scene triggers, manual mode choices and committed
stop handling retain their existing behavior. Historical speed-limit reason labels
remain readable in recorded routes.

On an exact tagged Ioniq 6 system-longitudinal session, a valid saved v1 configuration with SafeMode off starts the normal runtime; Stock selection still yields stock behavior. `CONDITIONAL_MODE_REPLAY_RUNTIME=1` retains the explicit developer path. Saved settings never substitute for fresh current CAN, axis, model, radar, or paired MONOTONIC/BOOTTIME clock evidence. Explicit `REPLAY` lacks a qualified recorded clock pair and cannot activate the normal CEM path.

`ConditionalModeConfig` is a strict, versioned SI document; readable absence selects factory CEM without writing a document. An explicitly saved Stock choice and Restore Stock reset select Stock. Native UI and Galaxy share the parked settings editor. Invalid documents stay intact until an explicit reset. Legacy settings are not adopted automatically. `SafeMode` disables conditional authority. Settings refresh at most once per second; cached owner revisions are affirmed per frame, while live vehicle and axis authority are checked independently.

Wheel actions require an explicit button mapping and a fresh Card receipt tied to the drive and settings. Manual mode codes are separate from sensor evidence: a valid manual request can persist through an unavailable scene, but never through lost longitudinal authority. `ConditionalManualState` stores the selected family's opt-in manual code separately from configuration. The planner is its only runtime writer; disk work runs outside the model loop. With persistence enabled, a new wheel override takes effect only after its save is acknowledged. Rapid gestures remain ordered; a failed save cannot make an unsaved override appear active. Without persistence, wheel changes are immediate within the session. Turning persistence on or off clears that family's previous saved code first. Cancel retains its ordinary disengagement behavior on the Ioniq.

The optional Ioniq 6 MODE/CUSTOM media path requires the reviewed physical E-CAN
bus 1 layout. Only exact eight-byte 0x448 packets supply evidence; the separate
receive owner never changes main CAN validity or transmits a frame. Six saved
short/long/very-long mappings default to unassigned; action 5 requests the
existing conditional manual cycle and action 6 toggles Traffic Mode on
MODE/CUSTOM. Duration advances on ordered source packet
timestamps, not repeated cached button bits. Startup, missing/ambiguous data,
more than 300 ms between packets, authority loss, or changed drive/settings/map
require a fresh neutral state. The Card and planner independently restrict the
vehicle and bus topology. The planner holds accepted Traffic intent through
temporary longitudinal-authority loss in the same validated drive and Card
source, but it is ineffective until fresh authority returns. Source loss, a
new source epoch or session, or changed drive/settings/map revokes the intent.
Its short-lived status separates accepted intent, effective authority, and
actual profile application. Existing custom-schema enum values are extended;
core CarState is unchanged. A recorded neutral 5 Hz source establishes presence
only; physical press/release and duration behavior still need vehicle checks.

The existing Conditional driving modes page in both device layouts and Galaxy
provides these six assignments for the exact supported Ioniq layout. Choices are
Off, Cycle conditional mode, and Toggle traffic mode. Unsupported readable saved actions require an
explicit Set Off repair; merely opening the page leaves them unchanged. Each
save rechecks parked authority, the full vehicle configuration, conditional
settings and all wheel mappings under the shared Params writer. These are saved
preferences for a later drive; they do not change Cancel behavior. An unused
Traffic assignment does not suppress ordinary profile tuning.

## Evidence and boundaries

Scene inputs distinguish false from unknown. Model filters advance once per original model tick; the oldest required source bounds freshness separately, so a slower fresh radar cannot stall a model filter or renew stale data. Startup, clock discontinuities, and drive changes require fresh observations. Conditional Chill requires every applicable veto to be known false. Disabled build features can be known absent; an enabled producer without a qualified observation remains unknown.

Current producers cover native MPC follow time and stop/throttle intent, model lane geometry, qualified adjacent radar, SLC attribution, pedal override, and the effective mode reported by selfdrived. The exact Ioniq media path supplies source-stamped Traffic Mode to CEM and a bounded Traffic follow/jerk, acceleration and cruise-braking profile. The shared committed-turn evaluator feeds both the stop detector and slower-lead retuning from original car/model stamps. Traffic retains the existing 0.75-second effective headway floor; saved values below it remain unsupported and intact. The native planner retains the selected cruise-braking response for an SLC cap. Conditional speed comparisons and curve control receive the same-cycle SLC-capped cruise target while the driver's SET remains unchanged. A saved Traffic braking category reverts to its 0.42 m/s² cruise baseline for a present radar lead or standstill, then recovers through the existing profile smoother; MPC and model braking retain priority. The saved lane-change close-gap preference and current lateral minimum-speed policy are separate from conditional scene selection. Frozen weather and other hazard-context braking inputs remain absent. Stop-sign, forced-stop, and dashboard stop-sign producers remain absent. The historical red-light name is already represented by the current stop-light observation, so it does not need a duplicate owner.

The source contract includes frozen `starpilot/controls/lib/conditional_experimental_mode.py`, `conditional_chill_mode.py`, `starpilot/common/experimental_state.py`, and the planner's mode selection. Tests compare executable frozen policy/detector traces and exercise current serialized messages, native MPC/radar joins, lifecycle boundaries, manual controls, and settings concurrency. These are integration evidence, not vehicle qualification. Normal availability is limited to the exact tagged Ioniq 6 LONG profile; the separate ECU handoff and native safety acknowledgment must succeed first. Missing scene producers and road behavior still require evaluation.
