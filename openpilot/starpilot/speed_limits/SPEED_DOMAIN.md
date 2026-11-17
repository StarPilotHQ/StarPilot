# SLC offset and speed coordinates

`speed_domain.resolve` binds an actual acceptance decision to an explicit offset
schedule and current raw/cluster speed pairs. Every number is m/s except the
input to `selected_pair_from_kph`, which converts a selected cluster value from
km/h. Each valid pair carries the same session ID and monotonic nanosecond
timestamp as the acceptance decision; a mismatch is invalid. The schedule
contains explicit half-open bands with signed m/s offsets;
it supplies no settings defaults or source freshness policy. A missing band or
unavailable target/pair produces `UNAVAILABLE`; malformed values produce
`INVALID`. `SpeedPair` contains measured cluster speed, which may be below raw
speed; resolution clamps each delta to `max(cluster - raw, 0)`. Only a `VALID`
resolution supplies a planner coordinate.

Before entering this domain, the runtime floors finite negative ego-speed estimates
to zero when fresh `carState` evidence reports standstill. The speed filter can
undershoot zero after the wheels stop. Negative estimates while moving, nonfinite
values and stale vehicle data remain unavailable; the original messages are unchanged.

A finite negative offset that would make the target nonpositive in cluster or
planner coordinates is ignored for that limit. The unadjusted accepted limit
is used instead; the result stays within the ordinary cruise-speed bounds.
Malformed schedules still fail validation.

Call order is acceptance, domain resolution, then
`to_planner_coordinate(context)`. The accepted limit plus offset is
in cluster coordinates; subtracting the current ego cluster delta once produces
the planner cap. A nonpositive result supplies no coordinate. The planner compares
this cap with the independent driver's SET and applies the lower target.

Ordinary SET changes do not supply an override contribution. During source loss,
the previous accepted limit uses the current offset and fresh vehicle coordinates.
It can be restored from validated saved history, without representing its old
source identity as current evidence. Pending and declined proposals cannot replace it.

These helpers remain pure and have no Params, source-freshness or vehicle-output
ownership. Tests cover units, signed offsets, malformed and missing coordinates,
source-gap continuation, unchanged SET, authority and the native planner.
