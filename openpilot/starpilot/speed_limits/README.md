# Speed Limit Controller

SLC supplies a speed-limit cap beneath the driver's cruise SET. The planner uses
the lower of SET and the accepted limit plus its configured offset, converted into
physical-speed coordinates once. Accepting a limit never rewrites SET. Native PCM
cruise owns its setpoint; non-PCM software cruise and the dedicated Subaru software
cruise mode retain their existing owners. Automatic SLC requires system longitudinal
control. Lead, curve, stop and actuator safety constraints retain their existing owners.
SLC works with either the StarPilot or upstream longitudinal planner.

## New limits and adoption

**Match Speed Limit on Engage** is Off by default. When enabled, a new software SET
engagement uses the SLC limit plus its offset. A limit accepted in this runtime
session remains available during a source gap. Restored history from a previous
drive or runtime restart never initializes SET; without an eligible limit, ordinary
SET initialization uses current vehicle speed or the configured initial minimum,
whichever is higher. A fresh SET can adopt the qualified current limit over one accepted
earlier in this drive when both the posted limit and the offset-adjusted target
are lower. This applies after an earlier timeout or explicit decline. Otherwise,
a different reading leaves the same-drive accepted limit as the SET target.
An accepted early Maps preview applies to the SLC ceiling only. In that case,
a new SET uses the qualified current road limit when available, or ordinary
SET initialization if only the preview is available.
RES restores the previous SET when one exists. Factory cruise keeps its own SET.

A different numeric limit from the selected source can start a new decision, including
after the driver changes SET. A source or road identity change at the same numeric
limit does not start another decision. The most recent decline survives source
gaps and other readings until a different limit is adopted or another limit is
declined. The sign continues to show the road reading.

A qualified limit from a different provider enters the same confirmation or automatic
adoption flow immediately, without an extra source-switch delay.

A proposed adjusted limit matching both displayed SET values is recorded without
another confirmation. The comparison uses the existing mph/km/h display rounding,
not actual traveling speed. A presented Accept or Decline takes priority. An
expired confirmation remains declined during ordinary SET adjustments. Unit
changes renew pending actions while preserving accepted limits, declines, SET
and the active physical-speed cap.

With **Confirm limits** set to **Off**, qualified new limits are adopted immediately.
**Both**, **Higher only**, and **Lower only** ask relative to the previous
accepted posted limit. An unselected direction is automatic.
Without a previous limit, any manual policy asks for the first one unless it
already matches SET.
The stationary New Limit caption gives two gentle pulses
(700 ms each, separated by 200 ms), then rests until the 3.2-second cycle repeats.
Only the caption changes color; the speed and source remain steady.
The arrow remains relative to vehicle speed.
Outside manual confirmation, large UI uses the same US or Vienna sign at 1.25×
scale beside SET speed.
Signs and confirmation choices have transparent interiors. Cached sign outlines and
tight dark glyph edges keep the camera visible without sampling its brightness.
SET keeps its label and value in both profiles. When the displayed road limit
matches the fresh accepted cap actually limiting SET, it gets an IN USE label;
the rectangular sign also turns green.
An applied offset target uses a large LIMIT value with the source reading below.
A held override uses the same rectangular readout labeled OVERRIDE, including in
Vienna mode; the road limit remains readable below it. While the accelerator is
pressed, PEDAL identifies the temporary driver input without claiming a held speed.
Both sign styles identify the displayed source: DASH for Dashboard, OSM for offline
Maps, and VISION for the vision model. Explicit Mapbox readings in historical
replay use NAV; unidentified live sources show SPEED. A retained road reading
shows LAST alongside its source when known. For the same source and speed, the
label clears after 150 ms of live updates. Vision keeps a confirmed sign current
after it leaves view while the camera pipeline stays healthy. DASH follows the
current OEM reading, and Maps keeps the current road limit separate from its
upcoming preview. Retained and recovering readings never claim IN USE.
During a new-limit confirmation the applied LIMIT or OVERRIDE stays below SET;
if SET is hidden, that status appears in the confirmation header with a `Now:`
label. The expanded Accept button contains the proposed limit including its offset
and unit; Reject says `new limit`. Source readings and offset arithmetic stay in
the collapsed notice, outside the expanded decision. Accept and Reject retain their original touch
areas and action validation. Green Accept and red Reject outlines contain each
button without filling its interior; pressing strengthens the outline and colors
the label. Disabled choices use muted ink.
Ordinary signs keep the source above the number, beside any pending arrow.
The source and arrow share a centered row; sign contents use the same geometry
on both screens. Small arrows are baked at double resolution and text is aligned
to final pixels for clear native rendering.
Steering-wheel + accepts and − declines. Tapping the sign opens the same
Accept / Reject choices on either native screen. Manual confirmations appear only
while system longitudinal control is engaged, including a temporary accelerator
override. Disengaged and AOL lateral-only modes keep displaying limits without
prompting. Losing longitudinal engagement cancels the pending choice without
declining it; re-engaging starts a fresh choice if needed. A pending manual choice
expires after thirty monotonic seconds while engaged, including source gaps. The same
decision, presentation, card and expanded controls remain actionable during a gap.
Accept applies that remembered proposal and Reject dismisses it. Recovery of the
same proposal preserves the interaction, deadline and attention cycle.
Wheel confirmation presses remain consumed during a gap, so they cannot become
ordinary SET adjustments. Acceptance takes effect immediately and is never queued.
Decline and timeout leave set speed unchanged. An action captured by the deadline
can complete its presented decision within the input freshness window. The prompt
closes at expiry, and an action captured after the deadline cannot accept it.

The saved `SLCConfirmation` mode is `0` for Off, `1` for Both, `3` for Higher
only, or `4` for Lower only.

## Optional early adoption

The existing SLC settings page offers **Adopt higher limits early** and
**Adopt lower limits early**: each is Off by default, or 1–10 whole seconds
(`SLCAdvanceHigherSeconds` and `SLCAdvanceLowerSeconds`). Direction follows the
posted upcoming limit relative to the current mapped limit, before offsets.
Other sources keep their current-limit behavior.
An ordered source list without Maps ignores map readings and previews. Removing
Maps retires an in-progress preview and returns to the selected sources' normal
adoption and previous-limit fallback rules.
The upcoming Map proposal must win the configured source-selection policy.
There is no new onroad widget, caption, or countdown. The existing New Limit
confirmation presents the proposed limit and its own offset; the ordinary sign
and source readings continue to describe the current road.

A fresh, qualified along-road distance becomes eligible when
`0 < distance <= carState.vEgo * seconds`. Only physical vehicle speed is used;
SET, cluster speed, acceleration forecasts and wheel-distance propagation do not
estimate arrival. A first offer requires motion and active system speed control.
This is approximate timing, not a promise to reach the target speed at the sign.

The first offer latches one boundary encounter. Slowing or stopping does not
retract it or restart the existing thirty-second confirmation deadline. Source loss
retains the pending choice and its Accept / Reject interaction; recovery of the same
boundary uses the original deadline and presentation. Losing longitudinal
engagement cancels an unanswered choice; re-engaging starts a fresh deadline.
A fresh preview with a different destination way or limit closes an unanswered
prompt, even before the replacement is close enough to offer. Closing a prompt
preserves the rollback point for an earlier applied preview.
Decline, expiry and the matching decision survive the previewed limit becoming current.
An older preview cap ends at arrival if the selected current reading differs.
Changing the driver's SET does not withdraw an accepted early limit.
An unchanged approach limit cannot be offered again solely because the preview
is lost or disabled. Current-source evidence, active system-longitudinal authority and driver-button checks still apply.
A fresh road match that contradicts the preview retires the encounter, even if
Maps knows the road but has no posted speed for it.
A newly selected current Maps limit gets its own decision before the next exit
can be offered, including the first Maps reading of a drive.
The same source-selection policy used to offer a preview can retire it when another
qualified source wins. That approach cannot re-offer the same destination and
limit solely because the winning source disappears or a tile refresh changes the ID.
If the preferred live source matches the accepted preview speed, the cap continues.
Otherwise the previous acceptance is restored and the selected source follows normal
confirmation rules.
The configured adopt wheel action continues to adopt the displayed current limit
when no confirmation is pending.

Timing-only edits preserve the runtime session and driver decisions. Off cancels
unapplied advance work only for that direction and leaves an applied SLC cap
intact; normal current-limit behavior resumes at the boundary. Offset and unit edits retain their existing
meaning. A short lower-limit zone can immediately qualify its exit increase;
there is no hidden minimum dwell time. GPS uncertainty, missing map data and an
unresolved continuation can prevent advance adoption; ordinary current-limit SLC
remains the fallback. An applied cap is retained when source data vanishes during the engagement.

On the wire, `slcState.speedLimit` remains the current selected observation.
`upcomingSpeedLimit` and `upcomingBoundaryId` identify a qualified early proposal;
early acceptance remains bound to that encounter. The boundary record
is in memory for this runtime session, not a saved decision database.

Only a finite positive qualified observation can propose a new limit. Missing,
stale and unknown observations remain distinct. Source loss retains an engaged
manual choice and its interaction until the original deadline. Only an explicit
Accept can adopt that remembered proposal. Missing data uses the previous accepted limit plus the current offset,
bounded by SET. A pending or declined new limit cannot replace that remembered value.
Without a previous accepted limit, ordinary SET remains the target.

The previous accepted limit survives disengagement and runtime restarts. The
existing `SLCQualifiedHistory` record stores it with format and schema validation;
restoration retains only the value and source label, never an old source episode
as fresh evidence. An early preview can cap speed during the drive, but only a
qualified current reading can confirm that preview for history. This remembered
value may describe a different road. Current
vehicle coordinates and active system-longitudinal authority are still required
to apply its cap. Disabled SLC and stock longitudinal control apply no cap.
Returning to the already accepted numeric limit clears an obsolete confirmation.

The widget retains the latest received limit, offset and source with an unavailable
status. If the active SLC target differs from the displayed limit with its visible
offset, the steady sign shows both values and labels the active target SLC.
Normal conditional-mode triggers and live braking safeguards retain their
owners; missing limit data cannot select an Experimental or Chill fallback mode.

Previous accepted limit is the fixed fallback. There is no fallback selector or
Experimental fallback. Old Quick Select assignments remain saved but unavailable.

Offsets use only the saved `SLCOffsetSchedule`, with fixed physical speed ranges
and adjustments. Without a saved schedule, all seven ranges start at zero and
can be edited immediately in Galaxy, Large UI, or Compact UI. Retired
`Offset1`–`Offset7` values are ignored; users with only those values must configure
their offsets again. Existing schedules are preserved, including across display
unit changes. There is no separate offset-adoption step.
Converted offsets display as whole mph or km/h, and edits use whole units.
Unit switches retain the saved precision so repeated conversions do not drift.

Offset-only edits preserve accepted and rejected state and renew action identities.
A changed offset updates the previous accepted SLC cap, including during source loss.
Valid source and confirmation edits also preserve the accepted limit and applied
cap while renewing pending actions. Disabling SLC still clears that constraint.

## SET speed and accelerator

Cruise + and − change SET. Confirmation presses remain consumed through hold and
release and do not also apply a software cruise step. Native PCM button effects remain native;
StarPilot does not pretend it can suppress the vehicle's own SET adjustment.
Native standstill RES takes precedence over confirmation.

Software cruise keeps its saved short/held intervals. PCM vehicles retain their
native steps, including the Toyota Swap Cruise Steps preference. The dedicated
Subaru software-cruise mode remains independent of SLC.
The Volt conventional-cruise controller uses the fresh SLC cap beneath SET for its
cruise-button speed boundary, with its existing physical-source and timing checks.

**After an accelerator override** defaults to **Hold the speed you reached**. Pressing
the gas above the accepted limit plus offset lets cruise hold the fastest speed you
reach, up to SET. **Use your SET speed** targets SET. **Resume the adjusted limit**
returns to the accepted limit plus offset on release. The override clears when cruise disengages,
the adjusted limit catches up, or the driver explicitly accepts or adopts a limit.
Stopping while cruise stays engaged preserves the override.
After an explicit choice, the current accelerator press cannot start another
override. Releasing and pressing again can start one.
SET and the accepted limit are separate from the override. Lead, curve and
stopping constraints still apply.

**Override speed limit with +** defaults to Off and is available when StarPilot
owns SET. A tap must move the displayed SET from at or below the adjusted limit
to above it. Cruise then uses SET until it returns to the adjusted limit, a different
limit is accepted, or engagement ends. A display-unit change or a stop while
cruise stays engaged preserves an active override. Confirmation, Curve Speed and
Force Stop take priority over this gesture.

The configured wheel action can explicitly adopt the displayed current limit. Once
the planner approves that specific press, card moves software SET to the approved
adjusted limit while cruise is engaged. A newer SET change cancels the write. If
card misses the approval frame, only SLC acceptance applies. On Hyundai
independent-button cars, cruise MAIN assigned to adopt is no longer also the
physical AOL lateral toggle. The onroad screen adds no new control.

## Source and action qualification

Source selection, registered vehicle capabilities, strict saved-setting decoding,
Dashboard parser freshness, Maps road qualification, and Vision clock/inference
checks remain in their source adapters. Vision still requires its separate display
and control qualification. Online providers are not connected as live control
sources. See [selection](SELECTION.md), [Maps](MAP_SOURCE.md), and the pinned camera
[model provenance](vision/assets/PROVENANCE.md).

Vision interprets sign numerals using the existing Metric setting: km/h when on,
mph when off. The producer converts once to m/s for both UI layouts and the SLC cap.
Changing Metric clears Vision's retained sign and supporting reads, including
in-flight results and cached heartbeats; fresh evidence must confirm the next limit.
SET and the current engagement's applied cap remain unchanged by this reset.

Every action binds the current session, decision and presentation. The causal ledger
keeps consumed confirmation presses out of ordinary software cruise, through held
repeats and release. Accept and Reject cannot follow a replaced source candidate.
Source recovery keeps the presentation for an unchanged proposal and its original deadline.
The planner independently checks longitudinal authority, a valid initialized SET
and the numeric cap before applying the lower speed.

`SlcState.hasCeiling` and `effectiveCap` report the active physical-speed constraint;
`isLimitingMaxSet` identifies when it is below the driver's ceiling. Observations,
accepted limits and the effective constraint remain separate. The existing custom
wire layout and historical command fields remain reserved for compatibility; live
SLC no longer publishes or consumes `slcCruiseCommand`. Official vehicle schemas
are unchanged. Shared curve, conditional-mode and controller messages remain intact.

## Numeric comparison investigation

Native replay confirms a source-conversion cause of repeated confirmations. This
is a synthetic reproduction through real cereal packets, source qualification and
the runtime, not a diagnosis of a particular recorded drive.

The offline generator's [`maps.ParseMaxSpeed`](../../../mapd_repo/maps/generate_offline.go)
uses `0.277778 * posted_kph`, while Dashboard decoding and Vision use
`posted_kph * (1 / 3.6)`. The generated tile stores this result as Float64.
`CurrentWay.EffectiveMaxSpeed` reaches `State.MatchedLimitMps` and `mapdOut` as
Float32; the discrepancy survives that conversion. Dashboard and Vision also
publish Float32, so ordinary equivalent floating-point arithmetic often collapses
to an identical wire value instead of causing a new decision.

For the same posted **50 km/h**:

| Reading | Float32 m/s seen by SLC |
| --- | --- |
| Dashboard using `1 / 3.6` | `13.88888931274414` |
| Map using `0.277778` | `13.888899803161621` |
| Difference | `0.00001049041748046875` |

Both display as 50 km/h or 31 mph. Before the repair, switching between these
readings could open another confirmation even though the displayed number agreed.
StarPilot now checks current and upcoming Maps readings and restored Maps history
for the exact legacy Float32 encoding of a whole km/h limit through 250 km/h.
Matches are converted to the standard Float32 value before SLC uses them.

[`test_numeric_decisions.py`](tests/test_numeric_decisions.py) records the
following controls against the actual runtime:

- For 30, 50, 80, 100, 130 and 250 km/h, a switch to the legacy Maps value keeps
  the accepted cap without another confirmation in either display-unit mode.
- Over 40 seconds at 20 Hz, switching Dashboard availability every half-second
  with the two 50 km/h encodings now preserves one decision and its original
  timeout. The same replay created 81 decisions before the repair.
- Equivalent mph arithmetic and a Float64 `nextafter` difference disappear at
  the shared Float32 wire boundary and cause no new numerical decision.
- A same-number producer restart renews the presentation and retires an old
  press, while keeping the decision and deadline. A plain source gap preserves
  the presentation as well and counts toward the original deadline.
- Genuine 1 km/h and 1 mph changes remain decisions. In particular, both 40 and
  41 km/h display as 25 mph, so current display rounding is insufficient to
  establish that two limits mean the same thing.

A separate arithmetic sweep of integer 4–250 km/h found distinct Float32 values
for every shortened conversion; the largest difference was
`0.00006103515625 m/s`. This measures the current converter formula, not installed
map coverage or onroad prompt frequency. Conditional speed rules call the same
Maps parser. The separate `main.ParseMaxSpeed` helper in `speed_limit.go` has no
callers in the reviewed tree and is not the live producer path identified here.

The reference FrogPilot revision checked on 2026-11-03 was
[`98ac7448`](https://github.com/FrogAi/FrogPilot/blob/98ac7448ab542e9fdff72d1ec4d7de2cd31460be/frogpilot/controls/lib/speed_limit_controller.py).
Its controller and reviewed callers matched the earlier `bb8ffa7` source bytes.
Running its actual decision methods with provider/network work isolated confirmed
that its `< 1 m/s` rule updates the target for conversion noise, real 1 km/h and
real 1–2 mph changes without another confirmation; a 3 mph change asks. At 20 Hz,
the alternating-noise scenario retains its nominal timeout instead of restarting
it. These were decision-function checks, not a complete FrogPilot runtime test.

Repairing the value at SLC input covers existing tiles and saved Maps history.
Source identity and driver-action guards still apply after numeric repair.

Host tests cover decisions, source validation, planner caps, cruise buttons, profile
selection and both UI layouts. Synthetic captures and host tests do not establish
camera accuracy, device timing under load, or onroad behavior.
