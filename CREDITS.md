# Source provenance

## Jetlink external inference

The comma-side Jetlink client and USB transport in `jetlink_repo` are by Zeph Leggett and the [Jetlink contributors](https://github.com/zoompilot/jetlink). They are vendored from revision `a863c3801da6fd925d14eb1f5b1e250f8efcc649` under the MIT license, retained in [jetlink_repo/LICENSE](jetlink_repo/LICENSE). StarPilot's adapter (`openpilot/starpilot/models/jetlink_adapter.py`) builds on Jetlink's openpilot layer and owns its param names, Always On Lateral rule, model runner reset, settings and alerts. Companion applications and inference servers remain distributed by Jetlink.

## MRR35 Hyundai angle and radar

The four MRR35 angle platforms (2026 Genesis GV70, second-generation electrified GV70, 2025 Genesis GV80, and Hyundai Ioniq 9) adapt Hyundai CAN FD angle-control design and the MRR35 radar DBC generator from the locally frozen StarPilot source snapshot `678af783`. That snapshot traces its Hyundai extension work to sunnypilot/opendbc `hkg-angle-steering-2025` at `cc4b08625a98e94b318cab15e45e05dad58042bd`; its historical import revision is not established. The relevant upstream contributors include Haibin (Jason) Wen, Shane Smiskol, and DevTekVE. Their authorship remains attributable to the upstream history; they do not maintain or endorse this port.

The copied MRR35 generator source and DBC definition are at `opendbc_repo/opendbc/dbc/generator/hyundai/hyundai_mrr35_radar.py` and `.dbc`. The host angle adapter and native safety profile are adaptations, not copies of a generic DBC or of unrelated vehicle support. Applicable upstream notices are preserved in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Ford support adapted from BluePilot

The Ford CAN FD camera-lead radar filtering is adapted from StarPilot `2efe6017bfe14cee4807833e05f9a7f6ba255cdb`. Its lineage includes BluePilot's `bp-7.0` Ford work, principally by Alan Polk, with contributions from John Christman, Jacob Neulight, tonesto7, Haibin Wen and other sunnypilot and BluePilot contributors. The reference snapshot is [`e1d051d7ba270261b4455068bd68f1a58db15a4a`](https://github.com/BluePilotDev/bluepilot/commit/e1d051d7ba270261b4455068bd68f1a58db15a4a); the exact checkout used for the earlier StarPilot import was not recorded.

The radar interface preserves that attribution while adapting to the current interfaces. Upstream contributors do not maintain or endorse this adaptation. Applicable notices are retained in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## RAV4 Continental ARS510 receiver

The optional ACC-target and fused-track receivers and DBC preserve firestar5683's Dom integration from `b220a37f4bd119e402225a7aa16a7cd84878d0af` (Sea Turtles, 2026-10-13) and its ARS510 source attribution to ds-sebastian/ars510-radar `fb5b29c2b5999f34afeb6f7ac494a73949d66c67`, copyright 2026 ARS510 radar contributors. The complete MIT permission and warranty notice is retained in THIRD_PARTY_NOTICES.md. Startup/settings ownership is adapted to the current StarPilot architecture; no original firmware binaries or safety permissions are copied.

## Spotify Bluetooth output

Spotify's Bluetooth output binds the comma's BlueALSA A2DP source to the selected speaker through a process-local ALSA configuration, leaving the system ALSA default, soundd and the warning speaker untouched. That approach is adapted from firestar5683's RoadScore Bluetooth output (`roadscore/prototype/bluetooth_output.py`, firestar5683/StarPilot `RoadScore` at `249eb88b3a397db847e4a595d7e23b7e85d329e5`). The Soloist player itself is never included: each user downloads it from Spotify, and the PulseAudio server that carries its audio is vendored unmodified from Ubuntu (see THIRD_PARTY_NOTICES.md).

## Tesla Pre-AP pedal control

The Pre-AP configuration, pedal codec/feedback, VirtualDAS feedforward table, grade compensation, torque-zero estimator and vehicle-specific gains preserve firestar5683's intentional Dom work, including `7837a3c1aab`, `aed0b7fdb9e9` and pinned `b220a37f4bd119e402225a7aa16a7cd84878d0af`. Original author attribution: firestar5683 <168790843+firestar5683@users.noreply.github.com>. Startup/calibration ownership, current upstream button/PID interfaces and native command bounds are adapted to the current StarPilot architecture; the old permissive native gateway is not transplanted. Existing source/license notices remain intact.

## Subaru angle steering from Jacob Waller's fork

Subaru angle support includes implementation maintained and integrated by **Jacob Waller (jacobw)** in [jacobwaller/openpilot, jul-angle-based](https://github.com/jacobwaller/openpilot/tree/542816196a18dba5f6f0af093c1ece4f415b5a22), with its [opendbc implementation at 8acbeccdab45a47aa7af170d2d17b61f0eb5e4f0](https://github.com/jacobwaller/opendbc/tree/8acbeccdab45a47aa7af170d2d17b61f0eb5e4f0). Jacob authored the VehicleModel angle-limit type correction and integrated the Crosstrek support. The branch also retains the original angle implementation and follow-up work by **lukasloetkolben and elkoled**, alongside comma contributors; their authorship remains theirs.

The inherited source covers Subaru angle platform/firmware definitions, `ES_Status` cruise state, angle controller/CAN integration, vehicle-model limits and native angle enforcement. StarPilot has additional platform-specific limits, driver handoff, discovery and AOL safety guards; this attribution does not claim identical tuning or complete parity with the reference fork. Original source commits, author names, emails and titles are recorded in [the Subaru origin history](opendbc_repo/opendbc/car/subaru/ORIGIN_HISTORY.tsv).

The explicit StarPilot [jacobw Subaru adapter](opendbc_repo/opendbc/car/subaru/JACOBW.md) additionally integrates that branch's low-speed filter, target-first limiting and Crosstrek EPS envelope. Its preserved stricter guards and platform scope are documented alongside the code.

## Experimental Blendful lane centering

Blendful is authored by James Vecellio-Grant (Discountchubbs), with coauthors royjr <royjr96@gmail.com>, nayan <nayan8teen@gmail.com> and Jason Wen <haibin.wen3@gmail.com>. The algorithm and its tests come from commit `97135965effb3630ed55993903449edb73468d50`, “controls: Blendful lateral policy (alpha) (#2073)”. Original copyright notices and the referenced license are retained in [LICENSE.blendful.md](openpilot/starpilot/lateral/LICENSE.blendful.md). StarPilot adds the default-off developer selection and runtime compatibility guards.

This project uses software from Haibin Wen and SUNNYPILOT LLC and is licensed under a custom license requiring permission for use.
