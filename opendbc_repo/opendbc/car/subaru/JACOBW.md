# jacobw Subaru angle adapter

The explicit `jacobw_angle.py` adapter integrates the Crosstrek angle work from
[Jacob Waller's hackathon-crosstrek fork](https://github.com/jacobwaller/openpilot/tree/118bb0409520433c3e09119ee61c8a57546272e0),
pinned to opendbc `753fdd383a18c3f9e86af801f1e0e9db2a168e72`.
Jacob authored its speed-dependent angle filter and 13:1 Crosstrek safety model.
The implementation retains earlier contributions from lukasloetkolben and elkoled. Original source authors and titles remain in `ORIGIN_HISTORY.tsv`.

Only `SUBARU_CROSSTREK_2025` selects this adapter, including the Impreza vehicles
identified by that profile. It updates the angle filter on every 100 Hz control frame, with time constants
of 0.3/0.1/0 seconds at 5/10/20 m/s and an inactive-target reset. There is no
low-speed deadzone. It clips the target before jerk limiting and retains the
190 degree EPS bound. A small native header mirrors that bound for this exact
VM profile. Other Subaru profiles and the shared lateral helper are unchanged.

Host and native Crosstrek geometry both use the branch's 13:1 steering ratio.
StarPilot retains the stricter 1 degree
per-command rate instead of upstream's 5, and all existing driver handoff,
MAIN/gear/stop/fault, source-health and AOL permission guards. An impossible
intersection of acceleration and jerk limits withdraws the steering request
and follows bounded measured angle; it cannot relax native limits. Neither
Alpha LONG nor initial AOL arming policy changes.

This is a StarPilot adaptation, not byte-for-byte upstream tuning. The original
project/opendbc MIT notices and the root source credits apply.
