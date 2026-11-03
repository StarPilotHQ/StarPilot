# Source selection contract

`selection.select_limit` chooses an observation for the acceptance reducer. It
does not accept a speed, command a car, request an online provider, read settings
or judge sensor health. `runtime.py` supplies the live source observations and
`runtime_settings.py` translates saved choices. Adoption and car-control behavior
are defined in the [controller contract](README.md).

Each of dashboard, map, vision and online must supply an explicit `Observation`.
Source adapters own normalized m/s, typed observation identity, freshness and eligibility.
For example, vision support and its separate display/control filters must be
resolved before this boundary. A missing producer is unknown; it is not proof
that no limit was observed. The selector invents no expiry period or road identity.
Geographic, producer-episode and session-value identities pass through unchanged.
Selection checks their structure but owns no drive session; the acceptance reducer
must verify session-value applicability against its current session before use.

`SelectionPolicy` has an explicit mode, up to three primary source slots and an
online-fallback Boolean. The default order is Vision, Maps, then Off. The saved
second and third slots can be Off. A slot may repeat another slot. Online is
never a primary slot. This module does not activate or translate preferences.

| Mode | Primary candidates | Choice |
| --- | --- | --- |
| Ordered | Only the configured slots | First eligible valid observation |
| Highest | Dashboard and map, plus vision if present in a slot | Highest eligible value |
| Lowest | Dashboard and map, plus vision if present in a slot | Lowest eligible value |

Equal extrema choose dashboard, then map, then vision, independent of input map
iteration order. The minimum eligible limit is explicitly 1 m/s, inclusive.
Smaller positive observations are reported in `below_minimum` and excluded;
zero, negative, nonfinite or malformed valid candidates are input errors.

The online observation is considered only if no configured primary is eligible
and fallback is enabled. It cannot override a valid primary. An eligible
secondary or online observation can replace an unavailable primary; all
considered unavailable sources remain in the result. Disabled and unconfigured
sources cannot create uncertainty in that choice.
The live settings adapter disables online fallback; online providers do not
authorize SLC commands.

If no eligible alternative exists, the result retains unknown or stale state.
Unknown takes precedence over stale, which takes precedence over absence; the
full per-source statuses remain available. Explicit absence, exclusion below the
minimum or an empty configured source set produces an absent result. Acceptance
uses the previous accepted limit during source loss, without inventing a current observation.
Malformed input produces `invalid_input`, errors and
an unknown observation without selecting a source.

The tests include composition through the acceptance reducer: accepted 65 mph,
proposed 45, driver rejection, then source loss and reconnect. Last accepted
history remains 65 for both longitudinal-only and combined authority. Source loss
does not change the driver's cruise SET or accept the rejected proposal.
Lateral-only or stock ownership cannot turn a selected
speed into a system longitudinal target. These are
deterministic policy tests, not native producer, planner, CAN or vehicle evidence.
