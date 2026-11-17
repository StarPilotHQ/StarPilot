# Speed-limit composition

`composition.step` joins source selection, acceptance, offsets and qualified raw/cluster
speed pairs into `Result.ceiling`. The planner caps the driver's SET with this value;
SLC never writes SET. Source, action, session, clock and authority validation remain
at their existing boundaries. Ordinary driver SET changes do not create an SLC bypass.

The previous accepted limit plus the current offset remains the cap through source
gaps and later engagements. Validated saved history supplies the same fallback
after a restart. It never creates a fresh source observation or accepts a pending
proposal. Current vehicle coordinates and system-longitudinal authority remain
required. There is no separate SLC override reducer or retained pedal target.

Lead following, curves, stopping, cruise profiles and actuator restrictions retain
their existing owners. See the [controller contract](README.md).
