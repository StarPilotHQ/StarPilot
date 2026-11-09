# Sentry motion events

This is the parked motion-event part of Sentry. Galaxy can list local event
metadata and captured images at `#/cameras/events`. Galaxy supports browser push,
Discord, webhooks, ntfy, and optional Firebase app push through the companion relay. Motion detection has
not yet been qualified on a physical device or vehicle.

The manager runs it only on device hardware when both
the vehicle is parked and a valid enabled `SentryMotionPreferences`
document is present. The same manager-owned
`sensord` supplies acceleration onroad and, under that explicit opt-in, while
parked. Sentry never starts another sensor, camera or power service.

The optional version-1 document contains `enabled`, `sensitivity` (0.005–1.0)
and `warningTimeSeconds` (0.1–10.0). Missing, unreadable or malformed settings
disable observation without replacing the saved bytes. There is no automatic
adoption of historical Sentry settings. Galaxy exposes the saved choices at
`#/cameras/sentry-settings`, through Cameras. Each edit requires a one-time
confirmation and fresh parked evidence, then compares the complete saved source
again before replacement. Invalid readable settings require an explicit reset
to Off and defaults; unreadable settings remain untouched. Saving preferences
does not bypass development opt-in. The shared settings page shows runtime
configuration blocks, a live arming countdown and motion-owner evidence failures.
Status expires after 1.5 seconds, is bound to the exact saved settings and boot,
and never grants sensor, camera or power authority. Heartbeats use namespace-local
shared memory on device hardware, not persistent event storage.

Arming requires 90 seconds of continuously fresh parked, ignition-off, power
and motion evidence. Observation runs at 10 Hz, preserving the magnitude-delta
threshold and warning-count behavior. An alarm requires more than 25 qualifying
deltas and at least 30 seconds within a 60-second motion window. A stale or
repeated sample, sensor loss, owner-loop gap, system resume, settings change,
ignition or shutdown request disarms the policy. Recovery requires rearming.
A poll with no new sensor message keeps arming while the last sample is fresh.
Hardwared retains battery accounting and shutdown authority; Sentry neither
extends parked runtime nor clears shutdown requests.

Warning and alarm records contain only kind, generated event/session IDs and
monotonic/system-clock timestamps. System time is not proof of an accurate wall
clock. Storage uses a private namespace-specific directory, at most 512 bounded
records, and atomic publication without overwriting an existing event. Full or
unavailable storage stops recording new events; it does not delete old evidence.
There are no image paths, location data or network requests. Authority and saved
settings are checked again immediately before publication. A publication whose
directory sync fails reports uncertain durability and is never blindly retried.

Tests cover continuous arming and thresholds, corrupt preferences, serialized
message timing, resume and power/ignition revocation, process selection, bounded
storage and interrupted writes. They do not establish physical motion sensitivity,
parked battery endurance, device wake behavior or complete Sentry parity.
