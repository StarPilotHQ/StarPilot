# Experimental RAV4 Radar

The optional Continental ARS510 parser supports the Toyota RAV4 2022 and RAV4
2023-2025 profiles, including hybrids. Earlier RAV4s, RAV4 Prime, and other Toyota
or Lexus profiles are unchanged. This is a beta feature, disabled by default.

In Galaxy, open **Vehicle > RAV4 Radar (Experimental)** while parked:

- **Off** preserves the existing vision-only behavior on these profiles.
- **ACC Target** publishes the radar's selected lead: the target Toyota's own ACC
  follows. Other leads remain vision-based. This is the recommended mode.
- **ACC Target + Summaries** publishes the ACC target plus up to two more moving
  cars within 40 m from the radar's own tracker (its target summaries), with each
  car's speed from its range over the last 8 radar cycles. In replay it brakes like
  ACC Target and backs cut-ins with radar slightly earlier. Its points also carry
  the radar's own relative acceleration and lateral speed (`aRel`, `yvRel`) for
  code that reads them.

Restart the device after changing the mode. The equivalent persistent parameter
is `ToyotaRadarMode`: `0` for Off, `1` for ACC Target, and `2` for ACC Target + Summaries.
The parser waits through the radar's delayed startup rather than requiring its
object frames in the initial fingerprint. Once running, missing scans clear the
tracks and report radar unavailability; healthy tracks are not cleared between scans.
Both radar modes also report radar unavailability while the radar's own fault flag
is held (two or more consecutive cycles), as it is while the radar declares itself
blocked; its ACC target can then be missing for a lead it still sees.

This setting does not enable Alpha Long, change longitudinal or lateral tuning,
send new CAN commands, or alter the existing radar-disable procedure. Without a
radar CAN filter, Alpha Long still disables the factory AEB output. Receiving
radar objects does not restore AEB. Filter-equipped and unfiltered cars retain
their existing longitudinal and safety configurations.

Use only for supervised beta testing and remain ready to brake or steer. An
available parser is not evidence that a particular car is providing live objects.
Collect rlogs with normal lead following, braking, stops and takeoffs, curves,
cut-ins, and both stock ACC and Alpha Long configurations. Bookmark unexpected
braking or lead loss. Do not deliberately approach an obstacle to test AEB.

The parser is adapted from the [ARS510 radar reference](https://github.com/ds-sebastian/ars510-radar)
at `7ff9485a454130bc5765c9f715046d2694c655a6`; attribution and its MIT notice are
preserved in `THIRD_PARTY_NOTICES.md`. Protocol and offline replay validation do
not establish closed-loop performance on every trim, firmware, or harness.
