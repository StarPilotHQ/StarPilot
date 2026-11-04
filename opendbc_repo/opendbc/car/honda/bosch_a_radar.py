"""Honda Bosch-A object bank decoder (Civic Bosch, CR-V 5G), ported from Trung's (trung791997) ns-bosch-radar-testing
@ 216050fd98 (2026-10-17) onto 7.0's BoschARadar.

What changed from 7.0's decoder: firmware range scale 1/16 m with the per-car ROM offset (was 0.05712 and -3.0), U11
closing speed at 1/72 m/s per count (was 1/64), the saturation rails treated as bounds by the gates (D-063), the
D-054/D-055/D-057/D-059/D-062 range gate, re-anchor and join logic, the one-sided coast bounds, and a saturated
lifecycle counter that no longer deletes a long-tracked lead.

Left out on purpose, because they depend on Trung's radard, which 7.0 does not have: the newborn range publish (needs
radard's NEWBORN_* exemptions; BOSCH_A_NEWBORN_RANGE_PUBLISH is False), the D-089 far-range recovery (only safe behind
radard's RECOVERED_CAM_GATE; BOSCH_A_REANCHOR_RECOVER_SIGMA_FRAC is 0), and the NORMALIZED_CLOSING outputs (no RadarPoint
fields for them in 7.0, and the NC-at-rail vRel was rejected, D-069). Point flags use 7.0's RadarPoint.deprecated group.
Trung's D-numbers and STATUS items refer to his branch's DECISIONS.md / STATUS.md.
"""
import math
from collections import deque
from dataclasses import dataclass, field

from opendbc.can import CANParser
from opendbc.car import Bus, structs
from opendbc.car.honda.hondacan import CanBus
from opendbc.car.honda.values import CAR, DBC, HondaFlags
from opendbc.car.interfaces import RadarInterfaceBase

# ============================================================================
# Honda Bosch-A 16-slot object bank
# ============================================================================
# 16 CAN-visible object slots. Each slot has 4 main frames (f0..f3, one CAN ID each -- NOT sub-frames
# muxed onto a shared ID) plus one synchronized auxiliary 5th frame. This supersedes any prior model of
# 0x280/0x284/0x288/0x28C as pieces of one object, or of 0x2C8/0x2C9 as a separate "coarse" list: it is
# ONE 16-slot bank with one auxiliary frame per slot. See honda_bosch_a_radar.dbc for the bit geometry.
BOSCH_A_DBC_NAME = 'honda_bosch_a_radar'
BOSCH_A_NUM_SLOTS = 16


def _bosch_a_main_base(slot: int) -> int:
  return 0x280 + 4 * slot if slot < 4 else 0x2D0 + 4 * (slot - 4)


def _bosch_a_aux_id(slot: int) -> int:
  return 0x2C8 + slot if slot < 8 else 0x290 + (slot - 8)


BOSCH_A_MAIN_IDS = [[_bosch_a_main_base(s) + i for i in range(4)] for s in range(BOSCH_A_NUM_SLOTS)]
BOSCH_A_AUX_IDS = [_bosch_a_aux_id(s) for s in range(BOSCH_A_NUM_SLOTS)]
BOSCH_A_ALL_IDS = [addr for ids in BOSCH_A_MAIN_IDS for addr in ids] + BOSCH_A_AUX_IDS

# Publish RadarPoints when the last MAIN object frame arrives. Passive captures prove that slot 15's
# companion frame, 0x297, follows 0x2FF and is the final observed object-family frame in a full sweep:
#
#   ... 0x2FC, 0x2FD, 0x2FE, 0x2FF, 0x297
#
# Keep 0x2FF as the RadarPoint trigger while the companion data remains optional/debug-only. Making
# 0x297 the sole trigger would allow one dropped auxiliary frame to suppress an otherwise-valid point
# update, contrary to the parser's "aux never gates validity" contract.
BOSCH_A_TRIGGER_MSG = BOSCH_A_MAIN_IDS[BOSCH_A_NUM_SLOTS - 1][3]
BOSCH_A_SWEEP_END_MSG = BOSCH_A_AUX_IDS[BOSCH_A_NUM_SLOTS - 1]  # 0x297

# Coherent Bosch-A sweep cadence, measured from 0x280 inter-arrival across Peter's routes:
# median 14.35 Hz (p5 12.5, p95 16.9). This also sets the lead-Kalman dt in radard, so the
# previous round 15 ran the filter ~4.5% fast.
BOSCH_A_FREQ_HZ = 14.35

# Range: f0 raw_range (12-bit, B2:B3 high nibble) -> meters. Firmware q16 = 8*raw_range.
#
# The scale is firmware-exact, not fitted. AC004 converts the internal value with
# (q16 - n) / 128, and q16 = sat16(round(8 * raw_range)), so
#
#     range_m = (8 * raw_range - n) / 128 = raw_range / 16 - n / 128
#
# Corroboration that 1/16 is the designed mapping rather than a coincidence: 8 * 4095 = 32760
# fits int16 with 7 counts to spare, so the *8 exists to make the 12-bit field fill the internal
# word; full scale is 4095/16 = 255.9 m; and /128 (Q7) is this firmware's unit for physical
# quantities throughout. The previous 0.05712 was 16 * 0.00357, and that 0.00357 was solved from
# a single tape point with the offset assumed, so it read progressively short with distance
# (~9% low, -9 m at 60 m against vision).
BOSCH_A_RANGE_SCALE_M = 1.0 / 16.0

# Offset: the radar firmware's ROM default n0, as -n0/128 m, per car (D-076, 2026-10-07, Peter;
# Civic corrected 2026-10-16, Jason's static trace).
#
#   Civic (36802TBA A160): cfg 5530 -> n0 = trunc(5530/16 + 0.5) = 346 -> -2.703125 m
#   CR-V  (36802TLA A070): cfg 5448 -> n0 = trunc(5448/16 + 0.5) = 341 -> -2.6640625 m
#
# Evidence and its limits (static only, never road-measured):
# - D-076 first took Civic cfg = 0, which selects the 335 literal fallback (-2.6171875 m). The ROM refutes that:
#   at startup the radar copies a ROM record into RAM config S = 0x40039A64 (memcpy(S+3, rec+3, 241) at 0xB732E,
#   fallback copy 0xB7386) and reads cfg from S+0xD6 (0xDB998, 0xDC03C). The TBA A160 table at ROM 0x15BC94
#   (stride 244) holds cfg 0x159A = 5530 at +0xD6 in all 26 records. The same method reproduces CR-V 5448
#   (positive control). 335 -> 346 moves every Civic dRel 0.0859 m shorter; vRel and range differences unchanged.
# - n0 is a ROM default. NvM can overwrite cfg at runtime (TBA writer 0xB60A8, store to S+0xD6 at 0xB61F2), so the
#   runtime value is unproven; Peter's radar is A150, whose image we do not have (346 is the A160 value); whether
#   the -0xC0 addend applies is open.
# - The firmware adds a runtime addend from camera CAN 0x669 X (Peter's car: n = 220). That path, and the
#   BoschARangeOffsetFallback toggle that applied it, are REMOVED: 0x669 was a required parser message
#   (canError risk if absent), and the firmware never applies n/128 to a transmitted range. Range is copied
#   unmodified (0xdb7ee -> rec+0x10); (range - n)/128 is only a lever arm for lateral (0xdc004) and the
#   width gate (0xaa5a4).
# - So this number is a firmware-sourced choice, not a proven range origin. It replaces -3.0 (James's fw
#   math + tape check), moving dRel +0.30 m (Civic, 346). The real origin of the camera's range vs openpilot's
#   bumper-frame dRel needs a laser/tape check to a parked car (5/10/20/40/60 m).
BOSCH_A_RANGE_OFFSET_CIVIC_M = -346.0 / 128.0  # -2.703125 m
BOSCH_A_RANGE_OFFSET_CRV_M = -341.0 / 128.0    # -2.6640625 m
BOSCH_A_RANGE_OFFSET_M = BOSCH_A_RANGE_OFFSET_CIVIC_M


def bosch_a_range_offset_m(car_fingerprint: str = "") -> float:
  """Firmware ROM-default range offset for the car: CR-V -341/128, everything else (Civic) -346/128."""
  from opendbc.car.honda.values import CAR
  if car_fingerprint in (CAR.HONDA_CRV_5G, CAR.HONDA_CRV_HYBRID):
    return BOSCH_A_RANGE_OFFSET_CRV_M
  return BOSCH_A_RANGE_OFFSET_CIVIC_M


# Azimuth: f0 raw_angle (11-bit, B4:B5 high 3 bits), offset-binary about 1024.
#
# The f3 angular-edge pair independently closes the exact center-angle scale:
#   azimuth_rad = (raw_angle - 1024) / 2048
#
# This supersedes the older empirical 0.032 deg/count fit.
BOSCH_A_AZIMUTH_SCALE_RAD = 1.0 / 2048.0
BOSCH_A_AZIMUTH_CENTER = 1024

# Invalid sentinels (section 3/4/5/6 of the spec).
BOSCH_A_STATUS_INVALID = 0xF
BOSCH_A_RANGE_RAW_INVALID = 0xFFF
BOSCH_A_ANGLE_RAW_INVALID = 0x7FF
BOSCH_A_LIFE_INVALID = 0xFFF

# LIFECYCLE_RAW is a 12-bit counter that advances by 2 per frame index, so it reaches its highest
# even value, 0xFFE, after 2047 frames -- about 137 s of continuous tracking at the ~14.9 Hz sweep
# rate -- and then stops advancing. It is NOT the invalid sentinel (0xFFF): STATUS, range, azimuth
# and track id all stay valid, and the object is still really there.
#
# Measured on 00000232--fc8dad0d18 (device commit 0083ffa, replayed through this parser):
# track 37 was born at route t=442.7 s, saturated at t=580.2 s at an age of 137.4 s, and was then
# observed on every sweep for 121.8 s (1,815 sweeps) while `life_delta == 2 * frame_delta` failed
# every time -- so the range history was cleared every sweep, no point ever matured, and the object
# was published exactly 0 times. Its last published geometry was dRel 38.9 m, yRel -0.1 m: the lead
# we were following. The device's own recording agrees -- liveTracks carries id 37 in 894/894 frames
# of segment 8 and 569/893 of segment 9, then 0 in segments 10-12, and the radar lead goes from
# 1200/1200 frames to 0/1200 across that boundary. Track 34 did the same at an age of 137.5 s.
#
# A saturated counter cannot testify either way about identity, and per D-041/D-042 the safe
# direction is to keep publishing geometry rather than delete a real object: the range-innovation
# gate below still decides whether each sweep is trustworthy, and staleness still retires a genuine
# disappearance. The cost, recorded against D-049: an ID reuse that happens WHILE the counter is
# saturated cannot be detected here at all.
BOSCH_A_LIFE_SATURATED = 0xFFE
BOSCH_A_TRACK_ID_MIN = 1
BOSCH_A_TRACK_ID_MAX = 0x3F
# AUX logical 0x00CA has an explicit 0x3FF invalid sentinel. Firmware proves the normalization below;
# captures strongly support interpreting the active value as previous/current range ratio.
BOSCH_A_RANGE_RATIO_INVALID = 0x3FF
BOSCH_A_LOGICAL_00CA_INVALID = BOSCH_A_RANGE_RATIO_INVALID  # compatibility/debug alias
BOSCH_A_RANGE_RATIO_SCALE = 0.001
BOSCH_A_RANGE_RATIO_OFFSET = 0.5

# The synchronized AUX frame contains an 11-bit offset-binary velocity-like field and a 10-bit
# companion quality/uncertainty field.  The byte locations are now decoded in the DBC.  The exact
# Bosch descriptor name is still being verified, so keep the conversion constants isolated here.
# Capture validation shows active values in [0, 1728], with 0x7FE used as the inactive sentinel.
BOSCH_A_DIRECT_VREL_INVALID = 0x7FE
BOSCH_A_DIRECT_VREL_MIN_RAW = 0
BOSCH_A_DIRECT_VREL_MAX_RAW = 1728
BOSCH_A_DIRECT_VREL_CENTER_RAW = 864
# D-074 (ACCEPTED by the owner 2026-10-02; static and replay evidence only, no road A/B): U11 is 1/72 m/s per
# count, built in; the old 1/64 decode is removed. Routes logged before 2026-10-01 published 1/64, so every m/s figure
# in this file and in radard measured on them (13.5 rails, -16.54, -19.4 ...) is in 1/64 units: multiply by 64/72
# for 1/72.
#   * static: Bosch-radar-partner camera firmware (36161-TLA-A070; same-family TGG-A080, TGH-A040,
#     TFJ/TGG/TGL-G070) formats vRel as round((v + 12) / 0x3c638e45), and 0x3c638e45 is 1/72 as an f32.
#     (v + 12) * 72 spans raw 0..1728, the observed rails, centre 864: the field is +-12.0 m/s by design.
#     The owner's camera, 36161-TBA-A130, is not imaged. (firmware-analysis-kit: camera-re/bosch_a_inventory,
#     radar-re/u11_encode)
#   * replay: stationary objects vs GPS 70.75 counts per m/s; lead-stop 71.55 [71.26, 72.40]; road-speed
#     approaches 70.90 [70.29, 71.40]. All three exclude 64. (radar-re/u11_gps, u11_leadstop, u11_dynamics)
#   * OPEN: moving leads against the range rate (radar-re/u11_moving; 1/72 retrace 2026-10-02, R3/R4; Job's pooled fit,
#     STATUS 7 src 2 U10<64, 55.7-70.0) bracket about 55-70 counts per m/s, which excludes 72; Job's per-dRel-band
#     range check (D-074 second addendum) depends on the band, column A running 66.8-77.0, so the result is band-
#     dependent rather than a clean exclusion. The encoder is firmware-proven 1/72 and the range scale is firmware-
#     proven raw/16 (R18); the range offset (BOSCH_A_RANGE_OFFSET_M, -346/128 on Civic) is a constant and does not change a range
#     slope. So this reads as a range-vs-U11 discrepancy (the range slope runs faster than U11), not a decode
#     error. Unresolved; see STATUS. No closing-speed figure here is road-validated.
# Centre 864, rails raw 0/1728, sentinel 0x7FE, u10, range and azimuth are unchanged. 1/72 publishes 64/72 of the
# old closing speed (-11.1 %) and puts the rail at 12.0 instead of 13.5 m/s; no gate threshold was re-tuned.
# The decode divides by the count rate, v = (raw - 864) / 72 exactly as the firmware formatter's inverse; multiplying by
# a rounded 1/72 differs from it by one double ulp on 598 of the 1729 raws (none after the float32 publish).
BOSCH_A_DIRECT_VREL_COUNTS_PER_MPS = 72
BOSCH_A_DIRECT_VREL_SCALE_MPS = 1.0 / BOSCH_A_DIRECT_VREL_COUNTS_PER_MPS


# The domain endpoints are SATURATION RAILS: at raw 0 or 1728 the true |vRel| is >= 12.0 m/s (13.5 in the 1/64
# units the evidence below was logged in) and the exact value is not recoverable from this field. A rail is therefore a BOUND, not an unknown,
# and it must still be published.
#
# This was briefly treated as "no measurement" and routed to the coast path. That was a safety
# regression, because a stationary car approached above 12.0 m/s (27 mph; 13.5 / 30 mph at 1/64) rails on
# EVERY sweep -- so the coast never ended, it outlived BOSCH_A_STALE_S, and the radar point was
# deleted. Measured on route 000001f9 at 29:52: two stopped cars, 88 of 88 active frames on the low
# rail with healthy u10 (78-94), range closing smoothly at -19.4 m/s. The radar lead was dropped,
# radard fell back to the vision lead which reported only -11.4 m/s, and the planner commanded 0.00
# while closing on stopped traffic at 76 m with a 6.6 s TTC. The driver had to intervene.
#
# Publishing the rail understates the closing rate (a stopped car reads as vLead = vEgo - 12.0), and
# that understatement is why it looked worth "fixing". But understating closing still brakes;
# deleting the object does not. Recovering the true value past the rail needs the range channel and
# is deliberately left for a separate, validated change.
BOSCH_A_DIRECT_VREL_RAILS_RAW = (BOSCH_A_DIRECT_VREL_MIN_RAW, BOSCH_A_DIRECT_VREL_MAX_RAW)
# D-063 (replay and static only; road evidence pending): the GATES must read a rail as the bound it
# is, too. The publish path above already does; the range-innovation gate (D-054) and the re-anchor
# test (D-057) did not -- they extrapolated the range at exactly -13.5 m/s (1/64). Route 0000025e at 12:03,
# stock ACC: track 59, the lead in lane from 102.8 m, U11 on the low rail on 51% of sweeps while
# the range closed at -15.4 to -17 m/s. The 2-3.5 m/s shortfall accumulated against the anchor, the
# range was rejected, the re-anchor refused because the fitted slope sat more than 3 m/s past the
# railed median, and the lead stayed dark until 41 m. A railed U11 now predicts an INTERVAL of range
# -- from the rail out to this physical cap -- and a range inside it is not an innovation. The cap is
# not a tuning value: it is a closing speed no same-direction or stationary target reaches from a
# car under ~145 km/h, and it keeps a rail from vouching for an unbounded range jump.
# This only lets the gates accept more; nothing is withheld that was published before, and the
# published vRel is still the rail itself.
BOSCH_A_DIRECT_VREL_RAIL_BOUND_MPS = 20.0
# The rail interval only vouches for a track in our own lane. Replayed over 7 routes (STATUS 82, 89):
# with the interval on every track, 21 points were lost, all on tracks 24-32 m off-axis, and the
# over-closers were railed U11s on adjacent-lane tracks (25e track 6, 25f track 33, 262 track 9)
# whose range closed at 7-10 m/s. A 4 m gate kept the lead gains and lost 0 points; 2 m also cleared
# the adjacent-lane over-closers. Off-path tracks keep the exact (pre-D-063) gate.
BOSCH_A_RAIL_INTERVAL_MAX_Y_M = 2.0
# On a DEGRADED sweep (range sigma, existence or u10 flagged) the interval applies only when the RANGE corroborates
# the rail: a least-squares fit over the TAIL of the rejected run plus this sweep (the shortest tail meeting the D-043
# minimum window, 4 samples over 0.25 s) must lie within 3 m/s (the D-043 tolerance) of the rail interval. Otherwise the gate reads the rail
# exactly, as with the toggle off. A degraded sweep is gated at the tight 2.0 m limit because the sweep is
# suspect; the interval must not widen that on the rail's word alone. Replay evidence (STATUS 129):
# - Route 0000025e 6:43 (403 s), track 48: a range walk 47.9 -> 35.3 m admitted through the interval against a
#   0.53 s old baseline, all sweeps degraded, while the rejected run's last five ranges sat at 34.9-36.1 m (fit
#   about -2 m/s, 11 m/s off the rail) and U11 left the rail on the next sweep. A fit over the last 8 sweeps still
#   averaged the finished walk in (about -20 m/s) and admitted it; hence the shortest tail. The admitted rail was coasted for 0.25 s at 35 m
#   and the planner went to -3.23 (vision and toggle-off lead at 42 m, -0.03). With the fit test it is not admitted.
#   (Item 92's 402.912 s single-sweep admission has no run to fit and is read exactly too.)
# - Route 0000025e 12:03, track 59, the case D-063 exists for: degraded on EVERY sweep from 87 m in, admitted at
#   724.233 s against a baseline 2.16 s old. Its rejected run closes at about 16-18 m/s, inside the interval, so it
#   is still admitted. Item 92's first suggestion (exact on every degraded sweep) kept it dark until 32 m, as with
#   the toggle off; a baseline-age floor (0.31 s) kept 12:03 but still admitted the 6:43 walk.
# The D-057 re-anchor already refuses any window holding a degraded sweep, so this only changes the D-054 gate.
# No new number: the window and tolerance are D-043's. A bool so the replay can A/B it.
BOSCH_A_RAIL_INTERVAL_EXACT_WHEN_DEGRADED = True
# The item 92 coast bound is two-sided with the rail interval on. Its LESS-closing side exists to undo a
# rail-interval admission (a rail coasted on a range the interval let in), so it now acts only inside a hold such
# an admission started (`rail_hold`); every other coast keeps the one-sided STATUS 111 bound. Applied to every
# coast it softened three protected brakes in the 26-route replay, each a stale over-closing coast on a track the
# interval never touched: 00000232 1147.2 (-2.25 -> -1.88), 00000266 560.0 (-1.79 -> -1.38) and 00000266 795.4
# (-1.5 crossing 3.75 s later). Replay evidence only (STATUS 129); a bool so the replay can A/B it.
BOSCH_A_RAIL_INTERVAL_DOWN_SIDE_ONLY_ON_RAIL_HOLD = True
# D-063 is built in (was the BoschARailInterval toggle; on in the owner's drives 00000287-0000028b). Replay, static and
# limited road evidence. Set False (replays, tests) and both gate calls below run `exact`, the pre-D-063 gate.
BOSCH_A_RAIL_INTERVAL = True
# The D-063 addendum's coast bound (_bosch_a_coast_vrel) also runs, without the rail interval, when the
# D-053 range-derived vRel switch is on. The coast is exactly where D-053 cannot act: radard disarms its
# assist on every unmeasured sample. Route 00000268 9:52.3-9:54.7 (STATUS 110): a 5 m range step as track
# 25 moved in made two opening U11 samples (+3.3, +4.1) the trusted vRel; the true closing U11 then failed
# the one-sided check against the stale fit and +4.06 was coasted for 2.35 s while the live, gated range
# closed 52.2 -> 38.3 m (~-6 m/s) and vision agreed. The bound pulls such a coast to within 3 m/s of the
# coast's own fresh range fit. Item 91's two wrong-direction brakes came from the rail-interval ADMISSION,
# not from this bound (item 92), so the bound is split out here, one-sided (more closing only; see
# _bosch_a_coast_vrel). Replay evidence only (STATUS 111). Built in (was the RangeDerivedVrel toggle).
BOSCH_A_COAST_RANGE_BOUND = True
# A coast that says the car ahead is reversing (vEgo + coasted vRel < -BOSCH_A_COAST_REVERSING_MARGIN_MPS) gets
# the two-sided bound even outside a rail hold: pulled to within BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS of
# the coast's own fresh range fit. With no fit yet it is floored at vLead = -margin instead. Everywhere else
# the STATUS 129 rule stands (less-closing side only inside a rail hold). Route 00000287 2:39 (STATUS 177): track
# 49 coasted its birth vRel -11.4 for 2.9 s while its range fell at about -4 m/s and ego slowed to 9.6 m/s, so
# the planner saw a lead reversing at -1.8 m/s and asked for -2.87; vision had the car at vRel -6 and the
# radar re-measured -6.8 on the next sweep. Replay: the coast publishes -7.2 instead of -11.4. A plain floor at
# vLead >= -margin only reached -10.8, and widening the down side to 5 m/s on every coast -9.2. STATUS 129's
# three protected brakes are stale over-closing coasts on real slower cars, not reversing ones, so they keep
# the one-sided bound. The point is always kept (D-041/D-042); only an implied reversing speed is bounded. A
# vEgo of None (not yet known) turns this off. Replay evidence only (STATUS 179); rides the same switches as the
# coast bound (BOSCH_A_RAIL_INTERVAL or BOSCH_A_COAST_RANGE_BOUND).
BOSCH_A_COAST_REVERSING_MARGIN_MPS = 1.0
# Newborn range publish (REPLAY ONLY, no DECISIONS entry yet). A new identity whose U11 arrives with u10 above
# BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW has no trusted vRel to coast, so the coast branch withheld it and kept
# none of its ranges until u10 dropped. Route 000002ae seg 17 (Peter's bookmark, 1/72 on): track 39, a stopped
# car in lane, sat in that state for its first 17 sweeps from 116 m and reached liveTracks 1.28 s later at 89.7 m;
# its withheld ranges closed at -19.8 m/s, the rate the radar itself reported once u10 dropped. With this on, the
# withheld ranges are kept in `newborn_run` and, once their trailing D-043 window (4 sweeps over 0.25 s) fits a
# line within BOSCH_A_REANCHOR_MAX_RMS_M, the geometry is published unmeasured with vRel = that fit's rate, bounded
# at -vEgo (the most closing a stopped object can produce): the stationary bound. The live U11 is not used. Why:
# radard initialises its Track KF from the first published vRel and never updates it while measured=False, so the
# first newborn vRel freezes vLeadK for the whole run; and the live U11 at high u10 is junk on exactly these
# identities -- track 39 read -3.8 at u10 623, +2.4 at u10 1015, then sat on the rail -- so the first layer-1 form
# (live U11 clipped to the fit +-3, floored at the low rail) froze a lead far too fast. The fit agrees with the
# radar's own later low-u10 reading (-19.8). Radard's vision match and FAR_RAIL_VISION_BOUND also had to accept a
# range-verified closing young track (radard NEWBORN_RANGE_CLOSING_EXEMPT), since the camera had this stopped car at
# 15-18 m/s. Nothing is withheld that was published before (D-041/D-042); the first low-u10 sweep roots the range
# gate on the run's last range, so it is gated like any other sweep. Replay evidence only.
# Built in on (owner, 2026-10-03; it shipped as the BoschANewbornLeads toggle, which is removed), together with
# radard's three NEWBORN_* switches. RadarInterface copies it into self.newborn_range_publish; replays and tests
# set either one False for the code before the newborn publish.
# Night Star port: OFF. It is safe only with radard's NEWBORN_* exemptions, which 7.0's radard does not have.
BOSCH_A_NEWBORN_RANGE_PUBLISH = False

# BOSCH_A_U11_FLOOR_UNMEASURED (2026-10-22; static + replay evidence only, no road A/B): a qualified sweep whose raw
# U11 is 0 (the -12.0 m/s floor) is NOT a velocity measurement. Route 00000008--94686ab4ae (the "rough" drive):
# 489 published points sat at raw 0, 377 of them measured=True, so radard's KF was updated with -12.0 on each.
# Their own range fits mostly read -17..-37 m/s (true saturation, the rail understates closing), but whole tracks
# read 0.00 / -0.13 / -0.54 / -0.61 / -4.76 m/s (raw 0 used where the field has nothing to say), and the leadTwo
# track 57 at 75.6-81.1 s fit -11.6. Either way the -12.0 is not the object's speed. Such a sweep now:
#   * is range-gated exactly as before (D-063 rail interval; `direct_vrel` keeps the rail for the gate),
#   * keeps its point and its accepted range history (`samples`, `range_anchor`): D-041/D-042, the point is never
#     deleted for being on the floor,
#   * publishes measured=False with vRel = the D-043 range fit over the accepted ranges (`_bosch_a_newborn_vrel`:
#     trailing 4-sample / 0.25 s window, rms <= BOSCH_A_REANCHOR_MAX_RMS_M, bounded at -vEgo). With no fit yet an
#     existing point keeps coasting (`_bosch_a_coast_vrel` if a trusted vRel exists, else its last published vRel),
#     and a track born on the floor is not published until the fit exists, because radard freezes the first vRel
#     of an unmeasured point (see BOSCH_A_NEWBORN_RANGE_PUBLISH),
#   * never seeds or updates `last_trusted_vrel`.
# A high-u10 raw 0 already coasts through `high_u10_live_vrel` and is unchanged.
# UNRESOLVED: the top rail. Same route: 66 published sweeps at raw 1728 (+12.0), 1 at 1726. Tracks 53/6/19 fit
# +9.3/+15.3/+12.6 m/s (consistent with saturation, not with a sentinel), track 62 is 22 sweeps spread over 2 min
# (no usable fit). Too few to decide, so raw 1728 is still decoded as a measured +12.0 rail.
# False restores the decoder before this change (replays and tests).
BOSCH_A_U11_FLOOR_UNMEASURED = True

# Seeding the first trusted sweep's history from the newborn run (samples, range_anchor, last_trusted_vrel) is off.
# Replay, 00000284 22:36.7 track 17: the newborn run was a 0.6 s range burst 88.7 -> 79.9 m (published fit -4.8 ->
# -17.6 m/s); seeded into the history, the coast after four measured sweeps held last_trusted_vrel -10.47 for 1.3 s
# while the range went 79.1 -> 83.2 -> 80.0 m, and the planner reached -1.0 m/s^2 (base: none below -0.88), where
# the unseeded track's coast followed the range (-0.5 .. -5.0). Replay evidence only.
BOSCH_A_NEWBORN_SEED_HISTORY = False
# u10 is a genuine uncertainty on U11, but it is CONFOUNDED WITH DYNAMICS. Measured against an
# event-local reference (quadratic fit to a centred window, derivative at the centre) over 16,834
# frames: median |err| rises 0.26 -> 0.88 -> 1.44 -> 1.95 m/s across u10 bins 0-64 / 64-128 /
# 128-192 / 192-256, but median |a_rel| rises 0.73 -> 2.13 -> 3.51 -> 4.59 m/s^2 alongside it.
# corr(u10,|err|) = +0.40 and corr(u10,|a_rel|) = +0.43. Holding dynamics out, the quality signal is
# real (calm-frame corr +0.52) -- but u10 climbs during genuine hard braking just as reliably.
#
# 511 is the value validated in d5000fe344 by replaying 20 bookmarks through the real
# RadarInterface and radard's Kalman filter, where it collapsed recorded spikes up to -26.5 m/s^2
# aLeadK. It was briefly lowered to 128 on error statistics alone; that undid the validated result
# and caused a measured regression -- on route 000001f3 at 19:27 u10 sat above 128 for 0.81 s during
# a real ~8 m/s^2 lead decel, the coast outlived BOSCH_A_STALE_S, the lead was deleted, and the
# vision fallback injected a 5 m / 6 m/s step that drove a -3.51 m/s^2 brake. Restored here.
#
# Do not re-tune this from offline error statistics. u10's confounding with dynamics means a lower
# threshold preferentially rejects real manoeuvring; the coast paths below are what must stay safe,
# not this number.
BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW = 511

# Multi-sweep velocity/range consistency. The existing per-sweep innovation gate cannot see a
# velocity error at all: over one ~70 ms sweep even a 3 m/s error moves the range by 0.2 m, far
# under its 2-5 m thresholds. Comparing U11 against a range rate fitted over several sweeps does
# have that power.
#
# Scope, measured on Peter's nine flagged events: this catches GROSS disagreement -- the 000001eb
# 6:59 event reported vRel -10.58 m/s while the range was actually opening at +0.46 m/s, an 11 m/s
# contradiction, across a track-identity change. It deliberately does NOT try to catch the milder
# 0.6-2.5 m/s overshoots seen at deceleration onset: a trailing window legitimately lags
# instantaneous velocity during real braking, so a threshold tight enough to catch those would also
# reject genuine hard decels. Those are the u10 gate's job.
#
# The test is ONE-SIDED, and that matters. U11 lags the true closure at a deceleration onset -- at
# 000001f3 19:27 the fitted range rate was -6.7 m/s while U11 still read -2.08 -- so a symmetric
# |U11 - rate| test fires during genuine hard braking and coasts exactly when the velocity is most
# needed. Lag can only make U11 UNDER-report closing while the lead is braking, so only the other
# direction is evidence of a fault: U11 claiming more closing than the geometry can support.
# Checked both ways: 000001eb 6:59 (U11 -10.58 while the range OPENED at +0.46) is rejected;
# the 000001f3 onset is not. In the mirror case, a lead accelerating away, the one-sided test
# coasts a more-conservative velocity, so it fails safe.
BOSCH_A_VREL_RATE_CHECK_MIN_SAMPLES = 4
BOSCH_A_VREL_RATE_CHECK_MIN_SPAN_S = 0.25
BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS = 3.0

# Measurement-authority policy. These are replay-derived safety/tuning gates, not recovered Bosch
# constants. Range innovation is measured from the previous accepted observation so a reset cannot
# become the baseline for following sweeps.
BOSCH_A_RANGE_SIGMA_DEGRADED_RAW = 4
BOSCH_A_RANGE_INNOVATION_MAX_M = 2.0
BOSCH_A_RANGE_INNOVATION_HARD_MAX_M = 5.0
BOSCH_A_FALLBACK_RANGE_RATE_MAX_MPS = 50.0
BOSCH_A_USE_TAN_LATERAL_PROJECTION = True

# Accepted-range history is retained for continuity and the adjacent two-point fallback. Rejected
# range resets never enter it, and it is not used for a multi-sample OLS velocity fit.
BOSCH_A_VREL_MAX_SAMPLES = 8

# D-057 (replay and static only; road evidence pending): re-anchor a range-rejected identity on a LASTING,
# clean step. Replay of every range-rejection run on 00000232 / 236 / 237 / 239 / 23a (D-054 + D-055): the 13 runs of
# >= 3 sweeps that RETURNED to the baseline all lasted <= 1.2 s and every one was degraded (existence 0
# on 62-83% of sweeps, U11 railed at -13.5 (1/64)). The lead lockouts were not: 236 track 38 (27.4 s) and 237
# track 31 (20.4 s) had non-degraded tails whose range moved at the U11 rate. A run re-anchors only when
# it has outlasted every returning excursion, its last WINDOW sweeps are all non-degraded with U11
# present, and their range fits a line at the U11 rate (RMS <= MAX_RMS, slope within the D-043 rate
# disagreement). Until then the identity stays coasted, never deleted (D-041). Re-based onto D-055 and
# replayed on 232/236/237/239/23a/23b/23e: 0 point-sweeps lost on every route; restores 236 track 38
# (17.1 s) and 237 track 31 (16.2 s) as the lead and 237 track 63 (51.4 s, non-lead); re-admitted
# measured vRel over-closes the next-1 s range slope by >3 m/s on 2.2 / 2.2 / 0 % of sweeps (236 /
# 237 / 23a) against 5.4 / 5.9 / 2.6 % for the already-published reference.
BOSCH_A_REANCHOR_MIN_SPAN_S = 1.5
BOSCH_A_REANCHOR_WINDOW = 8
BOSCH_A_REANCHOR_MAX_RMS_M = 1.0
# D-089 (STATUS 225; replay only, not driven): far-range recovery. RANGE_SIGMA_RAW grows with range (~0.07-0.09 x dRel), so it
# is >= BOSCH_A_RANGE_SIGMA_DEGRADED_RAW on every sweep beyond ~60 m and a far lead locked out by one D-054 range step could
# never re-anchor until it came close: 103 s, 22 % of camera-only lead time on 7 routes. For the D-057 window ONLY, range
# sigma is judged against max(BOSCH_A_RANGE_SIGMA_DEGRADED_RAW, FRAC x dRel); existence and u10 still count, and every other
# use of the degraded flag is unchanged. Alone this was rejected: the recovered U11 was wrong by 7-8 m/s on 2f2/2a4/2a6, both
# ways. So a point re-anchored only because of this rule is published with RadarPoint.recovered, and radard uses it only
# while the camera and its own range slope agree (RECOVERED_CAM_GATE). Without this rule the point would not exist at all,
# so an unconfirmed recovered point is the old picture. 0.15 is the one value replayed closed-loop (6 routes: 2f5 +34 s
# radar lead, no extra brake dip; one genuine slowdown 2f5 16:44.5 crossed -1.0 0.6 s later).
# Night Star port: 0 (off). The relaxed window is safe only behind radard's RECOVERED_CAM_GATE, which 7.0 lacks, and
# 7.0's RadarPoint has no `recovered` flag to carry; 0 keeps the shipped strict D-057 rule.
BOSCH_A_REANCHOR_RECOVER_SIGMA_FRAC = 0.0

# Staleness gate -- TUNING constant, reused plumbing pattern (not a firmware fact). At the observed
# ~15 Hz cadence, 0.20 s is approximately three missed sweeps.
BOSCH_A_STALE_S = 0.20


@dataclass
class _BoschASlotState:
  last_seen_nanos: int | None = None

  # Firmware logical-ID companion data -- telemetry/debug only for now, never a RadarPoint validity
  # gate. 0x00C9 has a firmware transform but no proven physical meaning; 0x00CA has an explicit
  # invalid sentinel and its physical meaning remains unresolved.
  logical_00c9_raw: float = float('nan')
  logical_00ca_raw: float = float('nan')
  direct_vrel_raw: int | None = None
  direct_vrel_uncertainty_raw: int | None = None

  def reset(self):
    self.last_seen_nanos = None
    self.logical_00c9_raw = float('nan')
    self.logical_00ca_raw = float('nan')
    self.direct_vrel_raw = None
    self.direct_vrel_uncertainty_raw = None


@dataclass
class _BoschATrackState:
  """Persistent state for one Bosch CAN object identity, independent of wire slot."""
  track_id: int
  prev_frame_idx: int | None = None
  prev_life: int | None = None
  last_seen_nanos: int | None = None
  wire_slot: int | None = None
  samples: deque = field(default_factory=lambda: deque(maxlen=BOSCH_A_VREL_MAX_SAMPLES))
  # (time, range) of the last observation that PASSED the range-innovation gate, accepted or coasted
  # (D-054). The gate measures the next sweep against it. It is never a velocity-derivative baseline:
  # `samples` above stays accepted-only, and a range-rejected sweep never moves either of them.
  range_anchor: tuple[float, float] | None = None
  # D-057: the current run of range-REJECTED sweeps, (time, range, U11 or None, degraded).
  # Cleared by any accepted sweep and by a lifecycle discontinuity.
  rejected_run: list = field(default_factory=list)
  # D-062: the current run of range-PASSED sweeps whose vRel failed the rate check, same tuple shape.
  # Cleared by any rate-consistent sweep, any range rejection and a lifecycle discontinuity.
  inconsistent_run: list = field(default_factory=list)
  rejoin_samples: list | None = None  # D-059: gated ranges since a join, until a fresh rate fit agrees with vRel
  # D-063: the current D-059 hold was started (or joined) by a rail-interval admission. Only such a hold's coast
  # may be pulled LESS closing toward its fresh fit (_bosch_a_coast_vrel); cleared whenever the hold ends.
  rail_hold: bool = False
  last_trusted_vrel: float | None = None
  last_trusted_vrel_nanos: int | None = None
  # BOSCH_A_NEWBORN_RANGE_PUBLISH: (time, range) of high-u10 sweeps before the first accepted sample.
  newborn_run: list = field(default_factory=list)
  newborn_vrel: float | None = None  # the vRel the newborn point was last published with
  floor_vrel: float | None = None  # BOSCH_A_U11_FLOOR_UNMEASURED: the unmeasured vRel a raw-0 sweep last published
  floor_run: list[tuple[float, float]] = field(default_factory=list)  # raw-0 sweeps' (t, dRel), for the fit only
  floor_seen: bool = False  # a raw-0 sweep was seen in this lifecycle (BOSCH_A_U11_FLOOR_UNMEASURED)
  # D-089: the anchor came from a re-anchor only the range-scaled sigma window allowed (RadarPoint.recovered). Cleared by
  # BOSCH_A_REANCHOR_WINDOW consecutive accepted sweeps that are non-degraded by the shipped rule, i.e. the evidence a
  # shipped D-057 re-anchor would have needed, and by a lifecycle discontinuity.
  recovered: bool = False
  strict_clean_count: int = 0
  # The F0 STATUS nibble of the latest valid observation (0 before the first one); see BoschARadar.point_statuses.
  status: int = 0
  rejected_strict_dirty_t: float = -1e9  # last rejected sweep that only the relaxed window called clean


def _bosch_a_newborn_vrel(run: list, v_ego: float | None) -> float | None:
  """BOSCH_A_NEWBORN_RANGE_PUBLISH: the vRel to publish for a newborn high-u10 run, or None while the run is too short
  or its ranges do not fit a line within BOSCH_A_REANCHOR_MAX_RMS_M. The fit's own rate, bounded at -vEgo (a stopped
  object is the most closing physically possible). The high-u10 live U11 is not used: radard freezes the first
  published vRel of an unmeasured point, and that U11 is junk here (see the constant's comment block)."""
  window = _bosch_a_trailing_fit_window(run)
  rate = _bosch_a_fresh_range_rate(window)
  if rate is None:
    return None
  ts = [w[0] for w in window]
  ds = [w[1] for w in window]
  n = len(window)
  t_mean = sum(ts) / n
  d_mean = sum(ds) / n
  rms = (sum((d - (d_mean + rate * (t - t_mean))) ** 2 for t, d in zip(ts, ds, strict=True)) / n) ** 0.5
  if rms > BOSCH_A_REANCHOR_MAX_RMS_M:
    return None
  return rate if v_ego is None else max(rate, -v_ego)


def _bosch_a_direct_vrel(raw_value: int | float | None,
                         uncertainty_raw: int | float | None = None,
                         counts_per_mps: int = BOSCH_A_DIRECT_VREL_COUNTS_PER_MPS) -> float | None:
  """Decode the capture-validated AUX relative-velocity candidate.

  None means that AUX was absent/invalid and the caller should use the existing fallback policy. The [0, 1728]
  active domain is deliberately enforced here because raw values above the observed 1728 rail (+12.0 m/s) have
  not appeared on active objects; 0x7FE is the observed inactive sentinel. `counts_per_mps` is 72 (D-074).
  """
  if raw_value is None:
    return None
  raw = int(raw_value)
  if raw == BOSCH_A_DIRECT_VREL_INVALID:
    return None
  if not BOSCH_A_DIRECT_VREL_MIN_RAW <= raw <= BOSCH_A_DIRECT_VREL_MAX_RAW:
    return None
  # u10 is retained as a raw quality indicator because its physical units remain unresolved.  The
  # conservative threshold is evidence-backed tuning, not a recovered firmware validity rule.
  if uncertainty_raw is not None and int(uncertainty_raw) > BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW:
    return None
  return (raw - BOSCH_A_DIRECT_VREL_CENTER_RAW) / counts_per_mps


def _bosch_a_range_ratio(raw_value: int | float | None) -> float | None:
  if raw_value is None:
    return None
  raw = int(raw_value)
  if raw == BOSCH_A_RANGE_RATIO_INVALID or not 0 <= raw < BOSCH_A_RANGE_RATIO_INVALID:
    return None
  return BOSCH_A_RANGE_RATIO_OFFSET + BOSCH_A_RANGE_RATIO_SCALE * raw


def _bosch_a_range_ratio_vrel(raw_value: int | float | None, d_rel: float, dt: float) -> float | None:
  """Return the range rate implied by the empirical previous/current range ratio."""
  ratio = _bosch_a_range_ratio(raw_value)
  if ratio is None or dt <= 0.0 or not math.isfinite(d_rel):
    return None
  return d_rel * (1.0 - ratio) / dt


def _bosch_a_direct_vrel_interval(direct_vrel: float, exact: bool = False,
                                  counts_per_mps: int = BOSCH_A_DIRECT_VREL_COUNTS_PER_MPS) -> tuple[float, float]:
  """D-063: the vRel interval a decoded U11 vouches for. A rail is a one-sided bound, anything else exact.
  `counts_per_mps` must be the one the U11 was decoded with (D-074), so the rail test matches the decode."""
  low_rail = (BOSCH_A_DIRECT_VREL_MIN_RAW - BOSCH_A_DIRECT_VREL_CENTER_RAW) / counts_per_mps
  high_rail = (BOSCH_A_DIRECT_VREL_MAX_RAW - BOSCH_A_DIRECT_VREL_CENTER_RAW) / counts_per_mps
  if exact:
    return direct_vrel, direct_vrel
  if direct_vrel <= low_rail:
    return -BOSCH_A_DIRECT_VREL_RAIL_BOUND_MPS, direct_vrel
  if direct_vrel >= high_rail:
    return direct_vrel, BOSCH_A_DIRECT_VREL_RAIL_BOUND_MPS
  return direct_vrel, direct_vrel


def _bosch_a_distance_to_interval(value: float, interval: tuple[float, float]) -> float:
  return max(0.0, interval[0] - value, value - interval[1])


def _bosch_a_range_innovation_rejected(baseline: tuple[float, float], now_s: float, dRel: float,
                                       direct_vrel: float | None, ratio: float | None, degraded: bool,
                                       exact: bool = False,
                                       counts_per_mps: int = BOSCH_A_DIRECT_VREL_COUNTS_PER_MPS) -> bool:
  """Does this range contradict one (time, range) baseline? See the D-054 comment at the call site."""
  previous_time, previous_range = baseline
  dt = now_s - previous_time
  if dt <= 0.0:
    return True
  residuals_m = []
  if direct_vrel is not None:
    low, high = _bosch_a_direct_vrel_interval(direct_vrel, exact, counts_per_mps)
    residuals_m.append(_bosch_a_distance_to_interval(dRel, (previous_range + low * dt, previous_range + high * dt)))
  if ratio is not None:
    residuals_m.append(abs(previous_range - dRel * ratio))
  if not residuals_m:
    return abs((dRel - previous_range) / dt) > BOSCH_A_FALLBACK_RANGE_RATE_MAX_MPS
  innovation_m = min(residuals_m)
  return (innovation_m > BOSCH_A_RANGE_INNOVATION_HARD_MAX_M or
          (degraded and innovation_m > BOSCH_A_RANGE_INNOVATION_MAX_M))


def _bosch_a_measurement_degraded(range_sigma_raw: int, existence_raw: int,
                                  direct_vrel_uncertainty_raw: int | None,
                                  range_sigma_limit_raw: float = BOSCH_A_RANGE_SIGMA_DEGRADED_RAW) -> bool:
  range_quality_bad = range_sigma_raw >= range_sigma_limit_raw or existence_raw in (0, 0x7F)
  velocity_quality_bad = (direct_vrel_uncertainty_raw is not None and
                          direct_vrel_uncertainty_raw > BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW)
  return range_quality_bad or velocity_quality_bad


def _bosch_a_lasting_clean_step(run: list, exact: bool = False,
                                counts_per_mps: int = BOSCH_A_DIRECT_VREL_COUNTS_PER_MPS) -> bool:
  """D-057: True when the trailing window of a range-rejected run is long, clean and moving at the U11 rate."""
  if len(run) < BOSCH_A_REANCHOR_WINDOW or run[-1][0] - run[0][0] < BOSCH_A_REANCHOR_MIN_SPAN_S:
    return False
  window = run[-BOSCH_A_REANCHOR_WINDOW:]
  if any(degraded or u11 is None for _, _, u11, degraded in window):
    return False
  ts = [w[0] for w in window]
  ds = [w[1] for w in window]
  n = len(window)
  t_mean = sum(ts) / n
  d_mean = sum(ds) / n
  denom = sum((t - t_mean) ** 2 for t in ts)
  if denom <= 1e-9:
    return False
  rate = sum((t - t_mean) * (d - d_mean) for t, d in zip(ts, ds, strict=True)) / denom
  rms = (sum((d - (d_mean + rate * (t - t_mean))) ** 2 for t, d in zip(ts, ds, strict=True)) / n) ** 0.5
  u11 = sorted(w[2] for w in window)[n // 2]
  return (rms <= BOSCH_A_REANCHOR_MAX_RMS_M and
          _bosch_a_distance_to_interval(rate, _bosch_a_direct_vrel_interval(u11, exact, counts_per_mps)) <=
          BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS)


def _bosch_a_fresh_range_rate(run: list) -> float | None:
  """Least-squares range rate over a run of (t_s, dRel, ...) tuples that grows on EVERY sweep of a coast
  (`rejoin_samples`, `inconsistent_run`), i.e. one that is not frozen the way `samples` is during a coast
  (D-062). None until the D-043 minimum window (4 samples over 0.25 s) is met."""
  if len(run) < BOSCH_A_VREL_RATE_CHECK_MIN_SAMPLES or run[-1][0] - run[0][0] < BOSCH_A_VREL_RATE_CHECK_MIN_SPAN_S:
    return None
  ts = [w[0] for w in run]
  ds = [w[1] for w in run]
  n = len(run)
  t_mean = sum(ts) / n
  d_mean = sum(ds) / n
  denom = sum((t - t_mean) ** 2 for t in ts)
  if denom <= 1e-9:
    return None
  return sum((t - t_mean) * (d - d_mean) for t, d in zip(ts, ds, strict=True)) / denom


def _bosch_a_trailing_fit_window(run: list) -> list:
  """The shortest tail of `run` that meets the D-043 minimum window (BOSCH_A_VREL_RATE_CHECK_MIN_SAMPLES samples
  over BOSCH_A_VREL_RATE_CHECK_MIN_SPAN_S), or the whole run when it is shorter. The rate the range has NOW, not
  the average over a step that is already over (STATUS 129, 0000025e 6:43)."""
  for start in range(len(run) - BOSCH_A_VREL_RATE_CHECK_MIN_SAMPLES, -1, -1):
    if run[-1][0] - run[start][0] >= BOSCH_A_VREL_RATE_CHECK_MIN_SPAN_S:
      return run[start:]
  return run


# D-104: the coast range bound fits only the last BOSCH_A_COAST_FIT_WINDOW_S of `inconsistent_run`, the range rate the
# lead has now. Route 00000311 84:21 (track 33, ~100 m): a +2.6 m range jump made D-062 coast the -1.5 vRel, and every
# later sweep was degraded (far range sigma), so D-062 never re-rooted and the coast ran 14 s (211 sweeps). Fitting the
# whole run averaged the old near-steady ranges in and lagged (-0.9..-4.8) while U11 read -4..-7.7 and the last 1 s of
# range -5..-11. The car closed late and braked -1.60 (replay -1.78). With 1.0 s: replay -1.43, braking starts ~3 s
# earlier, min time to contact 4.9 -> 7.0 s. The bound stays one-sided outside a rail hold (more closing only), and
# 1.0 s is 4x the D-043 minimum span, so a single bad sweep cannot set it. Replay (testing agent, 23 windows incl. the
# protected real brakes): 21 identical, 25b/25f within 0.01; the only other change is 311 86:35, sim -4.27 -> -4.38
# with identical radar input (sim state carried over from 84:21). 0 restores the whole-run fit.
BOSCH_A_COAST_FIT_WINDOW_S = 1.0


def _bosch_a_coast_vrel(track, rail_interval: bool, range_bound: bool = False, v_ego: float | None = None) -> float:
  """The vRel a coast publishes. Off (pre-D-063): the last trusted vRel, verbatim. With the rail interval on
  (D-063 addendum, STATUS 92): the same value bounded to within BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS of
  a FRESH fit over the ranges of the coast itself, when one exists. Replayed on 0000025e 6:43 (track 48): the
  rail interval admitted a slot-merge range walk, the D-059 hold then coasted the -13.5 (1/64) rail for 1.5 s while
  its own post-join fit read +0.8 m/s and said so (fresh_ok false on every sweep), and the planner asked for
  -3.45 m/s^2 against a lead the log and vision put at -1..-3 m/s. With the bound, replay of that window
  bottomed at -2.39 and never crossed -3.0. The hold's one-sided law (a vRel that over-closes the fresh
  range fit by > 3 m/s is not trusted) is applied to the value it coasts, both ways, so a stale opening
  vRel is pulled toward a closing fit once one exists. Never a one-sweep derivative: the fit is the D-043
  multi-sample fit, and with fewer than 4 fresh samples (0.25 s) the coast is unchanged. That is why it
  cannot act on 0000025e 12:05 (+11.1 coasted for 0.2 s at track birth, no fresh samples yet) nor on the
  first 5 sweeps after the 6:43 re-root, which still coast the rail. The point is always kept (D-041/D-042).
  Replay evidence only (STATUS 92).

  With only the range bound on (`range_bound`, STATUS 111) the bound is ONE-SIDED, D-053's rule: a coast may
  be made more closing, never less. The two-sided clamp without the rail interval softened 6 protected
  brakes on the 20-route replay (237 9:58.5 -3.10 -> -2.38, 266 8:04 -1.98 -> -1.48, 266 9:20.9 -1.66 ->
  -1.11, ...): the down side exists to undo a rail-interval admission, which this path never makes.

  With the rail interval on, the down side is likewise limited to a coast inside a hold that a rail-interval
  admission started (`track.rail_hold`, STATUS 129); every other coast gets the one-sided bound. Applied to every
  coast, the down side softened three protected brakes in the 26-route replay (00000232 1147.2 -2.25 -> -1.88,
  00000266 560.0 -1.79 -> -1.38, 00000266 795.4 -1.5 crossing 3.75 s later), none of them near a rail admission.

  Either switch on, a coast that implies the car ahead is reversing (v_ego + vRel < -BOSCH_A_COAST_REVERSING_MARGIN_MPS)
  also gets the down side, or with no fit a floor at vLead = -margin (route 00000287 2:39, STATUS 179)."""
  vrel = track.last_trusted_vrel
  if not (rail_interval or range_bound):
    return vrel
  rate = None
  if track.rejoin_samples is not None:
    rate = _bosch_a_fresh_range_rate(track.rejoin_samples)
  if rate is None:
    run = track.inconsistent_run
    if BOSCH_A_COAST_FIT_WINDOW_S > 0 and run:
      run = [w for w in run if w[0] >= run[-1][0] - BOSCH_A_COAST_FIT_WINDOW_S]
    rate = _bosch_a_fresh_range_rate(run)
  reversing = v_ego is not None and v_ego + vrel < -BOSCH_A_COAST_REVERSING_MARGIN_MPS
  if rate is None:
    if reversing:
      vrel = max(vrel, -(v_ego + BOSCH_A_COAST_REVERSING_MARGIN_MPS))
    return vrel
  vrel = min(vrel, rate + BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS)
  if reversing or (rail_interval and (track.rail_hold or not BOSCH_A_RAIL_INTERVAL_DOWN_SIDE_ONLY_ON_RAIL_HOLD)):
    vrel = max(vrel, rate - BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS)
  return vrel


def _create_bosch_a_can_parser(CP):
  messages = [(addr, BOSCH_A_FREQ_HZ) for addr in BOSCH_A_ALL_IDS]
  # Bus.radar selects the Bosch-A DBC; the object/fusion feed itself is physically on the camera-side ACC-CAN.
  return CANParser(DBC[CP.carFingerprint][Bus.radar], messages, CanBus(CP).camera)


VERIFIED_BOSCH_A_CARS = frozenset((CAR.HONDA_CIVIC_BOSCH, CAR.HONDA_CRV_5G))


class BoschARadar(RadarInterfaceBase):
  def __init__(self, CP):
    super().__init__(CP)
    if CP.carFingerprint not in VERIFIED_BOSCH_A_CARS or CP.flags & (HondaFlags.BOSCH_RADARLESS | HondaFlags.BOSCH_CANFD | HondaFlags.BOSCH_ALT_RADAR):
      raise ValueError("Honda radar requires its verified classic Bosch-A profile")
    self.rcp = _create_bosch_a_can_parser(CP)
    self.trigger_msg = BOSCH_A_TRIGGER_MSG
    # Ego speed, set by card before each update (the reversing-coast bound). None until then.
    self.v_ego: float | None = None
    # F0 STATUS per point of the last RadarData returned (7 confirmed; 9 on route 34 T479's phantom lead). card
    # carries it to radard in starpilotCarState.radarPointStatus: car.capnp's RadarPoint is upstream-owned on 7.0.
    self.point_statuses: dict[int, int] = {}
    self._slots = [_BoschASlotState() for _ in range(BOSCH_A_NUM_SLOTS)]
    self._tracks: dict[int, _BoschATrackState] = {}
    self._slot_track_ids: list[int | None] = [None] * BOSCH_A_NUM_SLOTS
    self._last_trigger_nanos = -1
    self.rail_interval = BOSCH_A_RAIL_INTERVAL
    self.coast_range_bound = BOSCH_A_COAST_RANGE_BOUND
    # D-074: U11 counts per m/s (72). An attribute so replays of 1/64-era logs can set it.
    self.u11_counts_per_mps = BOSCH_A_DIRECT_VREL_COUNTS_PER_MPS
    self.newborn_range_publish = BOSCH_A_NEWBORN_RANGE_PUBLISH
    self.u11_floor_unmeasured = BOSCH_A_U11_FLOOR_UNMEASURED
    # D-076: firmware ROM-default range offset (-2.703 m Civic, -2.664 m CR-V). Fixed per car.
    self.range_offset_m = bosch_a_range_offset_m(CP.carFingerprint)
    self.updated_messages = set()

  def update(self, can_strings):
    self.updated_messages.update(self.rcp.update(can_strings))
    if self.trigger_msg not in self.updated_messages:
      if self._last_trigger_nanos >= 0 and (self.rcp._last_update_nanos - self._last_trigger_nanos) * 1e-9 > BOSCH_A_STALE_S:
        return self._bosch_a_stale_radardata()
      return None
    result = self._update_bosch_a(self.updated_messages)
    self.updated_messages.clear()
    return result

  def _bosch_a_stale_radardata(self):
    # Whole-bus silence: clear every live point/history and emit an EMPTY RadarData (not None) so
    # radard drops any lead within a cycle instead of freezing a phantom. The next observation starts
    # a fresh incarnation for its CAN identity.
    self.pts.clear()
    self._tracks.clear()
    self._slot_track_ids = [None] * BOSCH_A_NUM_SLOTS
    for slot_state in self._slots:
      slot_state.reset()
    self._last_trigger_nanos = -1
    stale = structs.RadarData()
    if not self.rcp.can_valid:
      stale.errors.canError = True
    stale.errors.radarUnavailableTemporary = True
    return stale

  def _bosch_a_retire_track(self, track_id: int):
    self._tracks.pop(track_id, None)
    self.pts.pop(track_id, None)
    for slot, slot_track_id in enumerate(self._slot_track_ids):
      if slot_track_id == track_id:
        self._slot_track_ids[slot] = None

  def _bosch_a_retire_stale_tracks(self, now: int):
    for track_id, track in list(self._tracks.items()):
      if track.last_seen_nanos is not None and (now - track.last_seen_nanos) * 1e-9 > BOSCH_A_STALE_S:
        self._bosch_a_retire_track(track_id)

  def _update_bosch_a(self, updated_messages):
    ret = structs.RadarData()
    if not self.rcp.can_valid:
      ret.errors.canError = True

    now = self.rcp._last_update_nanos
    self._last_trigger_nanos = now
    self._bosch_a_retire_stale_tracks(now)

    observations = []

    for slot in range(BOSCH_A_NUM_SLOTS):
      f0, f1, f2, f3 = BOSCH_A_MAIN_IDS[slot]
      aux = BOSCH_A_AUX_IDS[slot]
      st = self._slots[slot]

      if not (f0 in updated_messages and f1 in updated_messages and
              f2 in updated_messages and f3 in updated_messages):
        # Incomplete main-frame set: a missing CAN frame must not be treated as a lifecycle mismatch or
        # death/replacement. Logical tracks are retired independently by their global staleness gate.
        continue

      v0 = self.rcp.vl[f0]
      v1 = self.rcp.vl[f1]
      v2 = self.rcp.vl[f2]
      v3 = self.rcp.vl[f3]

      idx0 = int(v0['FRAME_IDX'])
      if not (idx0 == int(v1['FRAME_IDX']) == int(v2['FRAME_IDX']) == int(v3['FRAME_IDX'])):
        # The four main frames don't share a common cycle index -- not a coherent observation this
        # window. Only combine main-frame data from a coherent common frame index (section 2).
        continue

      status = int(v0['STATUS'])
      range_raw = int(v0['RANGE_RAW'])
      angle_raw = int(v0['AZIMUTH_RAW'])
      range_sigma_raw = int(v0['RANGE_SIGMA_RAW'])
      existence_raw = int(v1['OBJECT_EXISTENCE_PROBABILITY_RAW'])
      life = int(v2['LIFECYCLE_RAW'])
      track_id = int(v3['TRACK_ID'])
      track_id_valid = BOSCH_A_TRACK_ID_MIN <= track_id <= BOSCH_A_TRACK_ID_MAX
      st.last_seen_nanos = now
      direct_vrel_raw = None
      direct_vrel_uncertainty_raw = None
      range_ratio_raw = None

      # Attach synchronized companion data only when its cycle matches. Missing AUX never invalidates
      # an otherwise coherent F0-F3 object; it only removes independent motion/quality evidence.
      if aux in updated_messages:
        av = self.rcp.vl[aux]
        if int(av['FRAME_IDX']) == idx0:
          direct_vrel_raw = int(av['REL_VELOCITY_RAW'])
          direct_vrel_uncertainty_raw = int(av['REL_VELOCITY_UNCERTAINTY_RAW'])
          range_ratio_raw = int(av['RANGE_RATIO_RAW'])
          logical_00c9_raw = av['FW_LID_00C9_RAW']
          logical_00ca_raw = av['FW_LID_00CA_RAW']
          st.logical_00c9_raw = logical_00c9_raw
          st.logical_00ca_raw = (
            logical_00ca_raw if logical_00ca_raw != BOSCH_A_LOGICAL_00CA_INVALID else float('nan')
          )
          st.direct_vrel_raw = direct_vrel_raw
          st.direct_vrel_uncertainty_raw = direct_vrel_uncertainty_raw

      object_valid = (status != BOSCH_A_STATUS_INVALID and range_raw != BOSCH_A_RANGE_RAW_INVALID and
                      angle_raw != BOSCH_A_ANGLE_RAW_INVALID and life != BOSCH_A_LIFE_INVALID)

      observations.append({
        'slot': slot,
        'frame_idx': idx0,
        'life': life,
        'track_id': track_id,
        'track_id_valid': track_id_valid,
        'object_valid': object_valid,
        'status': status,
        'range_sigma_raw': range_sigma_raw,
        'existence_raw': existence_raw,
        'direct_vrel_raw': direct_vrel_raw,
        'direct_vrel_uncertainty_raw': direct_vrel_uncertainty_raw,
        'range_ratio_raw': range_ratio_raw,
      })

    # First collapse duplicate wire observations of one CAN identity. The dictionary is also the
    # output uniqueness boundary: one valid Bosch identity can never create two RadarPoints.
    valid_by_id = {}
    # D-055 (replay and static only; road evidence pending): an identity valid in ANY slot this sweep
    # is decided by its own valid observation below, never hidden by an invalid one, mirroring the
    # slot-replacement guard. Only an accept re-creates a hidden point; a coast updates an existing
    # one. So when the radar migrated an object to another slot and the old slot went invalid in the
    # same sweep, the hide below deleted a point the coast would have kept. Measured on 00000232 /
    # 236 / 237 / 239 / 23a: 2,252 withheld point-sweeps, 504 of them the lead (00000237 track 9:
    # 21.2 s dark). On the fresh routes 0000023b / 23e (driven on D-054, 0756f810): 0 point-sweeps
    # lost, 9 + 331 restored (33 lead), all coasts — no new measured vRel enters control.
    valid_this_sweep = {o['track_id'] for o in observations if o['object_valid'] and o['track_id_valid']}
    for observation in observations:
      if not (observation['object_valid'] and observation['track_id_valid']):
        # An invalid observation ends publication for the object currently occupying this wire slot,
        # but it does not immediately destroy the persistent state. The object may be multiplexed to
        # another slot or may return before the per-identity stale deadline; a later lifecycle break
        # or staleness expiry will clear its derivative history.
        ids_to_hide = {self._slot_track_ids[observation['slot']]}
        if observation['track_id_valid']:
          ids_to_hide.add(observation['track_id'])
        for invalid_id in ids_to_hide - {None} - valid_this_sweep:
          self.pts.pop(invalid_id, None)
        continue
      track_id = observation['track_id']
      current = valid_by_id.get(track_id)
      if current is None:
        valid_by_id[track_id] = observation
        continue

      # Prefer the wire slot currently associated with the persistent state. If neither candidate is
      # preferred, retain the lower slot for deterministic handling of a malformed duplicate frame.
      track = self._tracks.get(track_id)
      current_score = (0 if track is not None and track.wire_slot == current['slot'] else 1, current['slot'])
      candidate_score = (0 if track is not None and track.wire_slot == observation['slot'] else 1,
                         observation['slot'])
      if candidate_score < current_score:
        valid_by_id[track_id] = observation

    valid_ids = set(valid_by_id)
    for track_id, observation in sorted(valid_by_id.items(), key=lambda item: item[1]['slot']):
      slot = observation['slot']
      idx0 = observation['frame_idx']
      life = observation['life']

      # A wire-slot replacement ends publication for the old occupant, but not its persistent
      # identity/history. If the old ID is still valid in another slot this sweep, keep its point;
      # otherwise hide it until that ID returns or reaches its own stale deadline.
      old_id = self._slot_track_ids[slot]
      if old_id is not None and old_id != track_id and old_id not in valid_ids:
        self.pts.pop(old_id, None)

      track = self._tracks.get(track_id)
      if track is None:
        track = _BoschATrackState(track_id=track_id)
        self._tracks[track_id] = track

      same_incarnation = False
      if track.prev_frame_idx is not None and track.prev_life is not None:
        frame_delta = (idx0 - track.prev_frame_idx) & 0xF
        life_delta = (life - track.prev_life) & 0xFFF
        same_incarnation = life_delta == 2 * frame_delta
        if not same_incarnation and life == BOSCH_A_LIFE_SATURATED and track.prev_life == BOSCH_A_LIFE_SATURATED:
          # The counter is pinned at its maximum and can no longer advance (see the constant's
          # evidence block). Without this, an object tracked for ~137 s is deleted for as long as it
          # remains visible -- measured at 121.8 s of continuous suppression of the followed lead.
          same_incarnation = True

      if not same_incarnation:
        # The CAN identity remains the externally-visible key, but a lifecycle discontinuity starts a
        # new incarnation and must not inherit the previous object's range-rate history.
        track.samples.clear()
        track.range_anchor = None
        track.rejected_run.clear()
        track.inconsistent_run.clear()
        track.rejoin_samples = None
        track.rail_hold = False
        track.recovered = False
        track.strict_clean_count = 0
        track.last_trusted_vrel = None
        track.last_trusted_vrel_nanos = None
        track.newborn_run.clear()
        track.newborn_vrel = None
        track.floor_vrel = None
        track.floor_run.clear()
        track.floor_seen = False
        self.pts.pop(track_id, None)
      track.status = observation['status']

      v0 = self.rcp.vl[BOSCH_A_MAIN_IDS[slot][0]]
      range_raw = int(v0['RANGE_RAW'])
      angle_raw = int(v0['AZIMUTH_RAW'])
      dRel = BOSCH_A_RANGE_SCALE_M * range_raw + self.range_offset_m
      azimuth_rad = BOSCH_A_AZIMUTH_SCALE_RAD * (angle_raw - BOSCH_A_AZIMUTH_CENTER)
      # The firmware's internal geometry path uses tan(angle) for a forward-axis distance.  The
      # Bosch object range is consumed as that forward-axis quantity here, so use the same projection
      # rather than treating it as radial/slant range.  Keep the switch explicit while the final
      # output bridge remains under static review.
      #
      # Sign: AZIMUTH_RAW > center (positive azimuth_rad) is a LEFT-of-center detection in car frame's
      # y axis (left is positive), matching the Nidec RadarPoint.yRel contract. Confirmed against real
      # captures 2026-08-22: a stationary cluster of queued vehicles visible on the left in the road
      # camera was rendering on the right in both the Qt and raylib radar-track overlays with the
      # previously-flipped sign.
      lateral_projection = math.tan if BOSCH_A_USE_TAN_LATERAL_PROJECTION else math.sin
      yRel = dRel * lateral_projection(azimuth_rad)

      now_s = now * 1e-9
      direct_vrel_raw = observation['direct_vrel_raw']
      direct_vrel_uncertainty_raw = observation['direct_vrel_uncertainty_raw']
      direct_vrel = _bosch_a_direct_vrel(direct_vrel_raw, direct_vrel_uncertainty_raw, self.u11_counts_per_mps)
      live_direct_vrel = _bosch_a_direct_vrel(direct_vrel_raw, counts_per_mps=self.u11_counts_per_mps)
      range_ratio_raw = observation['range_ratio_raw']

      # A live U11 rejected solely by the conservative U10 qualification threshold is neither an
      # unavailable velocity nor permission to synthesize a one-sweep range derivative. It is also
      # NOT permission to skip range-innovation checking below: u10 correlates with range_sigma in
      # replay data, so a high-u10 sweep is exactly the condition where a bad/discontinuous range
      # (slot migration, reset) is most likely, not less likely. Range acceptance is therefore
      # decided on the same terms as every other sweep first; only once the range clears that gate
      # does a high-u10 U11 fall back to coasting instead of publishing a synthesized derivative.
      high_u10_live_vrel = (direct_vrel is None and live_direct_vrel is not None and
                            direct_vrel_uncertainty_raw is not None and
                            direct_vrel_uncertainty_raw > BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW)

      # Qualified U11 and the range-ratio field are independent corroboration paths for the range;
      # high-U10 U11 is deliberately excluded from this decision.
      #
      # D-054: a sweep is range-rejected only if it contradicts BOTH the last ACCEPTED sample and
      # `range_anchor`, the last range that passed this gate (a velocity coast also advances it). Each
      # baseline on its own locked a real object out, measured through this parser on 00000232 / 236 /
      # 237 / 239 / 23a:
      # - Accepted sample only (the parser before D-054). A coast held it still while the range kept
      #   moving, and every later sweep was predicted across the growing gap with the very U11 the coast
      #   had just distrusted. 00000232 track 43, the followed lead pulling away 78.5 -> 84.5 m: the
      #   D-043 rate check coasted four sweeps, the error grew 0.86 / 1.12 / 1.53 / 1.87 / 2.11 m, the
      #   degraded 2.0 m limit rejected it, the point was deleted after BOSCH_A_STALE_S, and radar lost
      #   the lead for 15 s while it closed from 84 m to 27 m.
      # - Anchor only (the first D-054 draft). 00000237 track 12, a new object at 25 m whose range walked
      #   out to 28.06 m while U11 said -2 m/s. The rate check rightly coasted the walk, the walk became
      #   the baseline, and the real ranges (25.3 m closing to 17 m) were rejected against it for 53 s
      #   while the accepted sample still tracked them.
      # The recorded range resets this gate exists for contradict both baselines.
      if track.newborn_run and (track.samples or track.range_anchor is not None):
        track.newborn_run.clear()
        track.newborn_vrel = None
      newborn_vrel = None
      if self.newborn_range_publish and track.newborn_run and not high_u10_live_vrel and track_id in self.pts:
        newborn_vrel = track.newborn_vrel
      if newborn_vrel is not None and not BOSCH_A_NEWBORN_SEED_HISTORY:
        # The first trusted sweep starts the track exactly as it would have without newborn publishing.
        track.newborn_run.clear()
        track.newborn_vrel = None
      elif newborn_vrel is not None:
        # The published newborn run becomes the accepted history and its published vRel the velocity a coast may
        # hold, so this sweep is range-gated against it and a coast here keeps the point instead of dropping it.
        track.samples.extend(track.newborn_run[-BOSCH_A_VREL_MAX_SAMPLES:])
        track.range_anchor = track.newborn_run[-1]
        track.last_trusted_vrel = newborn_vrel
        track.last_trusted_vrel_nanos = now
        track.newborn_run.clear()
        track.newborn_vrel = None
      previous_sample = track.samples[-1] if track.samples else None
      range_anchor = track.range_anchor
      ratio_vrel = None
      range_rejected = False
      rail_admitted = False
      degraded = _bosch_a_measurement_degraded(
        observation['range_sigma_raw'], observation['existence_raw'], direct_vrel_uncertainty_raw,
      )
      if range_anchor is not None:
        ratio = _bosch_a_range_ratio(range_ratio_raw)
        # Deliberately still timed from the last ACCEPTED sample, as before D-054: changing a
        # published vRel is a separate decision, recorded as open in STATUS.md.
        if previous_sample is not None and now_s > previous_sample[0]:
          ratio_vrel = _bosch_a_range_ratio_vrel(range_ratio_raw, dRel, now_s - previous_sample[0])
        baselines = [range_anchor] if previous_sample in (None, range_anchor) else [range_anchor, previous_sample]
        # D-063: a railed U11 vouches for an interval of range, but only with the rail interval on and only
        # for a track in our lane; otherwise the gate reads the rail as the exact value, as before.
        exact_gate = not self.rail_interval or abs(yRel) > BOSCH_A_RAIL_INTERVAL_MAX_Y_M
        if not exact_gate and degraded and BOSCH_A_RAIL_INTERVAL_EXACT_WHEN_DEGRADED:
          # A DEGRADED sweep gets the interval only when the range corroborates the rail: a fit over the recent
          # rejected run plus this sweep moves at a railed speed (BOSCH_A_RAIL_INTERVAL_EXACT_WHEN_DEGRADED).
          run_rate = _bosch_a_fresh_range_rate(_bosch_a_trailing_fit_window(track.rejected_run + [(now_s, dRel)]))
          exact_gate = (run_rate is None or direct_vrel is None or
                        _bosch_a_distance_to_interval(run_rate, _bosch_a_direct_vrel_interval(direct_vrel, counts_per_mps=self.u11_counts_per_mps)) >
                        BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS)
        range_rejected = all(_bosch_a_range_innovation_rejected(baseline, now_s, dRel, direct_vrel, ratio, degraded, exact=exact_gate,
                                                                counts_per_mps=self.u11_counts_per_mps)
                             for baseline in baselines)
        rail_admitted = not exact_gate and not range_rejected and all(
          _bosch_a_range_innovation_rejected(baseline, now_s, dRel, direct_vrel, ratio, degraded, exact=True,
                                             counts_per_mps=self.u11_counts_per_mps)
          for baseline in baselines)

      if range_rejected:
        track.inconsistent_run.clear()
        # D-089: the window's own degraded flag, with range sigma judged against a range-scaled limit.
        step_degraded = _bosch_a_measurement_degraded(
          observation['range_sigma_raw'], observation['existence_raw'], direct_vrel_uncertainty_raw,
          max(BOSCH_A_RANGE_SIGMA_DEGRADED_RAW, BOSCH_A_REANCHOR_RECOVER_SIGMA_FRAC * dRel))
        track.rejected_run.append((now_s, dRel, direct_vrel, step_degraded))
        track.strict_clean_count = 0
        if step_degraded != degraded:
          track.rejected_strict_dirty_t = now_s
        if _bosch_a_lasting_clean_step(track.rejected_run, exact=exact_gate, counts_per_mps=self.u11_counts_per_mps):
          window_t0 = track.rejected_run[-BOSCH_A_REANCHOR_WINDOW][0]
          track.recovered = track.rejected_strict_dirty_t >= window_t0
          rail_admitted = not exact_gate and not _bosch_a_lasting_clean_step(track.rejected_run, exact=True,
                                                                              counts_per_mps=self.u11_counts_per_mps)
          # D-057: the step has outlasted every returning excursion measured, cleanly and at the U11
          # rate. Re-root the accepted history and the anchor on it and gate this sweep as passed.
          recent = track.rejected_run[-BOSCH_A_VREL_RATE_CHECK_MIN_SAMPLES:]
          track.samples.clear()
          track.samples.extend((t, d) for t, d, _, _ in recent[:-1])
          track.range_anchor = recent[-2][:2]
          previous_sample = track.samples[-1]
          range_anchor = track.range_anchor
          range_rejected = False
          track.rejected_run.clear()
      else:
        if track.rejected_run:
          # D-059: a join. The range passed only after a rejection run, so `samples` straddle the gap.
          track.rejoin_samples = []
          track.rail_hold = False
        track.rejected_run.clear()
      if rail_admitted:
        if track.rejoin_samples is None:
          track.rejoin_samples = []
        track.rail_hold = True

      if range_rejected:
        # Keep the last trusted point briefly as an unmeasured coast. The rejected geometry is not
        # published and never becomes the baseline for a later derivative or for this gate.
        accepted_fresh = range_anchor is not None and now_s - range_anchor[0] <= BOSCH_A_STALE_S
        point = self.pts.get(track_id)
        if accepted_fresh and point is not None:
          point.deprecated.measured = False
        elif point is not None and self.u11_floor_unmeasured and track.floor_seen:
          # BOSCH_A_U11_FLOOR_UNMEASURED: with the floor out of the accepted history, a floor-era identity can be left
          # with no baseline that would ever accept its range again (route 00000008 track 17, 217.05-223.03 s: kept
          # before only by a 3 s-old sample). The gate is unsure, so publish a bound, never delete (D-041/D-042): the
          # reported geometry, unmeasured, at the rejected run's range fit, else the coast, else the floor's vRel.
          run_vrel = _bosch_a_newborn_vrel([(t, d) for t, d, _, _ in track.rejected_run], self.v_ego)
          if run_vrel is not None:
            point.vRel = run_vrel
          elif track.last_trusted_vrel is not None:
            point.vRel = _bosch_a_coast_vrel(track, self.rail_interval, self.coast_range_bound, self.v_ego)
          elif track.floor_vrel is not None:
            point.vRel = track.floor_vrel
          point.dRel = dRel
          point.yRel = yRel
          point.deprecated.measured = False
        else:
          self.pts.pop(track_id, None)
        track.prev_frame_idx = idx0
        track.prev_life = life
        track.last_seen_nanos = now
        track.wire_slot = slot
        for old_slot, old_id in enumerate(self._slot_track_ids):
          if old_slot != slot and old_id == track_id:
            self._slot_track_ids[old_slot] = None
        self._slot_track_ids[slot] = track_id
        continue

      # The range passed the gate: it is the gate's baseline from here on, whatever happens to vRel.
      # Only a range that was actually GATED can advance it. With no anchor there was no gate, so a
      # coast now (a high-u10 birth) would root the gate on an unchecked, unpublished range: measured
      # on 00000239 that withheld 2,236 sweeps the pre-D-054 parser published, rejecting later sweeps
      # 2-17 m off such a birth range. The first ACCEPTED sample below roots the anchor instead.
      if range_anchor is not None:
        track.range_anchor = (now_s, dRel)

      # BOSCH_A_U11_FLOOR_UNMEASURED: raw U11 0 is not a velocity. Publish the range fit unmeasured and never let
      # the floor become a trusted vRel. Like every coast path, the floor sweep stays out of the accepted history
      # (`samples`, `rejoin_samples`, the consistency run): it must not become a derivative baseline. Its ranges are
      # kept in their own run, for the fit only.
      if (self.u11_floor_unmeasured and direct_vrel is not None and direct_vrel_raw is not None and
          int(direct_vrel_raw) == BOSCH_A_DIRECT_VREL_MIN_RAW):
        track.floor_seen = True
        track.floor_run.append((now_s, dRel))
        del track.floor_run[:-BOSCH_A_REANCHOR_WINDOW]
        fit_history = sorted(list(track.samples) + track.floor_run)[-BOSCH_A_REANCHOR_WINDOW:]
        fit_vrel = _bosch_a_newborn_vrel(fit_history, self.v_ego)
        point = self.pts.get(track_id)
        if fit_vrel is not None:
          floor_vrel = fit_vrel
        elif track.last_trusted_vrel is not None:
          floor_vrel = _bosch_a_coast_vrel(track, self.rail_interval, self.coast_range_bound, self.v_ego)
        elif point is not None:
          floor_vrel = point.vRel
        else:
          floor_vrel = direct_vrel  # no fit yet: the floor itself, as a bound (D-041), published unmeasured
        # Same maturity as an accepted sweep (a second sample), so nothing published before is withheld.
        if point is None and len(fit_history) >= 2:
          point = self.pts[track_id] = structs.RadarData.RadarPoint()
          point.trackId = track_id
          point.deprecated.aRel = float('nan')
          point.deprecated.yvRel = float('nan')
        if point is not None:
          point.dRel = dRel
          point.yRel = yRel
          point.vRel = floor_vrel
          point.deprecated.measured = False
          track.floor_vrel = floor_vrel
        track.prev_frame_idx = idx0
        track.prev_life = life
        track.last_seen_nanos = now
        track.wire_slot = slot
        for old_slot, old_id in enumerate(self._slot_track_ids):
          if old_slot != slot and old_id == track_id:
            self._slot_track_ids[old_slot] = None
        self._slot_track_ids[slot] = track_id
        continue

      # Multi-sweep consistency: does the range actually move the way this velocity claims?
      # Fitted over the accepted range history, so it is immune to the single-sweep blindness above.
      vrel_candidate = direct_vrel if direct_vrel is not None else ratio_vrel
      vrel_inconsistent = False
      if vrel_candidate is not None and len(track.samples) >= BOSCH_A_VREL_RATE_CHECK_MIN_SAMPLES - 1:
        ts = [sample[0] for sample in track.samples] + [now_s]
        ds = [sample[1] for sample in track.samples] + [dRel]
        span = ts[-1] - ts[0]
        if span >= BOSCH_A_VREL_RATE_CHECK_MIN_SPAN_S:
          n = len(ts)
          t_mean = sum(ts) / n
          d_mean = sum(ds) / n
          denom = sum((t - t_mean) ** 2 for t in ts)
          if denom > 1e-9:
            # strict=True: ts and ds are same-length by construction. A silent truncation here
            # would bias the fitted rate and weaken the one-sided gate rather than erroring.
            rate = sum((t - t_mean) * (d - d_mean) for t, d in zip(ts, ds, strict=True)) / denom
            # One-sided: only U11 claiming MORE closing than the range supports is a fault.
            vrel_inconsistent = vrel_candidate < rate - BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS

      # D-062: `samples` only grow on accepted sweeps, so during a coast the fit above is frozen. If the
      # lead turns from opening to closing, U11 pulls away from that stale rate and the check latches: route
      # 00000258 43:29-43:46, track 13 held vRel -0.45 for 12.7 s (fit frozen while it opened at +2.8 m/s)
      # as its range fell 124 -> 31 m, then FCW. Appending coasted ranges to `samples` instead (F1)
      # re-admits over-closing U11: replayed on 00000231, newly measured vRel over-closed the next-1 s
      # range slope by >3 m/s on 17.0% of sweeps (reference 6.8%), the D-056 failure. So only a coast run
      # that passes the D-057 lasting-clean-step test (>=1.5 s, non-degraded, ranges fit a line within
      # 1 m and at the U11 rate) re-roots the fit. Replayed on 24 routes against the coast it replaces,
      # re-rooted vRel was nearer the next-1 s range slope on 235 sweeps, the coast on 79 (lead 73/35),
      # and total error halved; 00000241 and 0000024f leads went the other way, by over-closing (STATUS 70).
      if vrel_inconsistent:
        track.inconsistent_run.append((now_s, dRel, direct_vrel, degraded))
        if _bosch_a_lasting_clean_step(track.inconsistent_run, counts_per_mps=self.u11_counts_per_mps):
          recent = track.inconsistent_run[-BOSCH_A_VREL_RATE_CHECK_MIN_SAMPLES:]
          track.samples.clear()
          track.samples.extend((t, d) for t, d, _, _ in recent[:-1])
          previous_sample = track.samples[-1]
          track.inconsistent_run.clear()
          vrel_inconsistent = False
      else:
        track.inconsistent_run.clear()

      # D-059: after a join, `samples` hold pre-gap ranges plus the joined one, so the fit above is set by
      # the gap rather than by the object and cannot see a contradicting U11. Replayed on 00000232 / 236 /
      # 237 / 239 / 23a / 23b / 23e: measured vRel in the second after a join over-closed the next-1 s
      # range slope by >3 m/s on 21.0 % of sweeps (33.1 % where the rejected run's own slope contradicted
      # U11) against 4.6 % for all measured points. Hold such a vRel as a coast until the same one-sided
      # check also passes over the post-join ranges alone.
      # - `samples` are left exactly as before: clearing them instead (fitting only the fresh ranges)
      #   admitted 106 new measured sweeps that over-closed on 16 % and lost 65 point-sweeps (the D-056
      #   failure mode). With them untouched, this can only withdraw measured vRel, never add it.
      # - The hold needs a trusted vRel to coast and re-publishes a point the rejection had dropped: a
      #   join must never delete an object the radar is reporting and the gate just accepted (D-041/D-042).
      # Replay vs D-057: 0 lost, 0 new measured, 612 sweeps withdrawn (30 lead); the withdrawn vRel
      # over-closed on 29.2 % (105 / 359 scored).
      rejoin_hold = False
      if track.rejoin_samples is not None:
        track.rejoin_samples.append((now_s, dRel))
        del track.rejoin_samples[:-BOSCH_A_REANCHOR_WINDOW]
        fresh_ok = False
        if vrel_candidate is not None and len(track.rejoin_samples) >= BOSCH_A_VREL_RATE_CHECK_MIN_SAMPLES:
          ts = [x[0] for x in track.rejoin_samples]
          ds = [x[1] for x in track.rejoin_samples]
          if ts[-1] - ts[0] >= BOSCH_A_VREL_RATE_CHECK_MIN_SPAN_S:
            n = len(ts)
            t_mean = sum(ts) / n
            d_mean = sum(ds) / n
            denom = sum((t - t_mean) ** 2 for t in ts)
            if denom > 1e-9:
              rate = sum((t - t_mean) * (d - d_mean) for t, d in zip(ts, ds, strict=True)) / denom
              fresh_ok = vrel_candidate >= rate - BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS
        if fresh_ok:
          track.rejoin_samples = None
          track.rail_hold = False
        else:
          rejoin_hold = track.last_trusted_vrel is not None and not (high_u10_live_vrel or vrel_inconsistent)
      if rejoin_hold:
        track.samples.append((now_s, dRel))
      if high_u10_live_vrel or vrel_inconsistent or rejoin_hold:
        # The range cleared innovation checking above, so geometry here is trustworthy; only vRel is
        # in question. Preserve current geometry but coast only a recent authoritative motion
        # estimate without a KF update, rather than publishing a one-sweep-derivative synthesis.
        # A coast means the VELOCITY is doubtful, not that the object is gone: object existence is
        # decided earlier by STATUS/existence/range validity, which route elsewhere. Dropping the
        # point here therefore discards a geometry the radar is still reporting and the range
        # innovation check just accepted. Measured on 000001fb segments 34-35 through the real
        # RadarInterface: 28 suppression gaps, 70% of them longer than BOSCH_A_STALE_S, i.e. long
        # enough for radard to delete the track and fall back to vision. That is the 000001f9
        # failure mode -- there a stopped car was dropped and the planner commanded 0.00 at 76 m.
        #
        # Keep publishing the geometry and hold the last trusted velocity, flagged measured=False.
        # An understated closing rate still brakes; a deleted object does not. Genuine disappearance
        # is still handled by _bosch_a_retire_stale_tracks, which every coast path leaves armed by
        # refreshing last_seen_nanos only while the radar keeps reporting this identity.
        point = self.pts.get(track_id)
        if point is None and rejoin_hold:
          point = self.pts[track_id] = structs.RadarData.RadarPoint()
          point.trackId = track_id
          point.deprecated.aRel = float('nan')
          point.deprecated.yvRel = float('nan')
        if point is not None and track.last_trusted_vrel is not None:
          point.dRel = dRel
          point.yRel = yRel
          point.vRel = _bosch_a_coast_vrel(track, self.rail_interval, self.coast_range_bound, self.v_ego)
          point.deprecated.measured = False
        elif (self.newborn_range_publish and high_u10_live_vrel and range_anchor is None and
              track.last_trusted_vrel is None):
          # A newborn high-u10 identity: keep its ranges outside the accepted history and publish the
          # geometry once they fit a line (BOSCH_A_NEWBORN_RANGE_PUBLISH).
          track.newborn_run.append((now_s, dRel))
          del track.newborn_run[:-BOSCH_A_REANCHOR_WINDOW]
          newborn_vrel = track.newborn_vrel = _bosch_a_newborn_vrel(track.newborn_run, self.v_ego)
          if newborn_vrel is None:
            self.pts.pop(track_id, None)
          else:
            if point is None:
              point = self.pts[track_id] = structs.RadarData.RadarPoint()
              point.trackId = track_id
              point.deprecated.aRel = float('nan')
              point.deprecated.yvRel = float('nan')
            point.dRel = dRel
            point.yRel = yRel
            point.vRel = newborn_vrel
            point.deprecated.measured = False
        elif point is not None and track.floor_vrel is not None:
          # BOSCH_A_U11_FLOOR_UNMEASURED: a point published only by raw-0 sweeps has no trusted vRel, but it was
          # published; keep it unmeasured at the floor's range-fit vRel rather than delete it (D-041/D-042).
          point.dRel = dRel
          point.yRel = yRel
          point.vRel = track.floor_vrel
          point.deprecated.measured = False
        elif point is not None:
          # No trusted velocity was ever established for this identity, so there is nothing to
          # coast and no way to publish a defensible vRel.
          self.pts.pop(track_id, None)

        # Do not let a coast-only range observation become a future derivative baseline.
        track.prev_frame_idx = idx0
        track.prev_life = life
        track.last_seen_nanos = now
        track.wire_slot = slot
        for old_slot, old_id in enumerate(self._slot_track_ids):
          if old_slot != slot and old_id == track_id:
            self._slot_track_ids[old_slot] = None
        self._slot_track_ids[slot] = track_id
        continue

      # Native U11 is unavailable here (sentinel/out-of-range -- the high-u10-but-live case above
      # already returned before this point) and the range-ratio field is unusable or degraded too.
      # The remaining option is the raw one-sweep (dRel-previous_range)/dt derivative, which is
      # structurally the same hazard the high-u10 case guards against: a synthesized rate becoming an
      # authoritative measurement. Coast a recent trusted velocity instead, on the same terms as above,
      # rather than publish it.
      # A true birth observation (no previous accepted range yet) can never mature into a published
      # point this cycle regardless of vRel source -- `matured` below requires a second sample -- so
      # only intercept once a fallback derivative would actually have something to poison.
      u11_and_ratio_unavailable = (direct_vrel is None and (ratio_vrel is None or degraded) and
                                   previous_sample is not None)
      if u11_and_ratio_unavailable:
        # A coast means the VELOCITY is doubtful, not that the object is gone: object existence is
        # decided earlier by STATUS/existence/range validity, which route elsewhere. Dropping the
        # point here therefore discards a geometry the radar is still reporting and the range
        # innovation check just accepted. Measured on 000001fb segments 34-35 through the real
        # RadarInterface: 28 suppression gaps, 70% of them longer than BOSCH_A_STALE_S, i.e. long
        # enough for radard to delete the track and fall back to vision. That is the 000001f9
        # failure mode -- there a stopped car was dropped and the planner commanded 0.00 at 76 m.
        #
        # Keep publishing the geometry and hold the last trusted velocity, flagged measured=False.
        # An understated closing rate still brakes; a deleted object does not. Genuine disappearance
        # is still handled by _bosch_a_retire_stale_tracks, which every coast path leaves armed by
        # refreshing last_seen_nanos only while the radar keeps reporting this identity.
        point = self.pts.get(track_id)
        if point is not None and track.last_trusted_vrel is not None:
          point.dRel = dRel
          point.yRel = yRel
          point.vRel = _bosch_a_coast_vrel(track, self.rail_interval, self.coast_range_bound, self.v_ego)
          point.deprecated.measured = False
        elif point is not None and track.floor_vrel is not None:
          # BOSCH_A_U11_FLOOR_UNMEASURED: a point published only by raw-0 sweeps has no trusted vRel, but it was
          # published; keep it unmeasured at the floor's range-fit vRel rather than delete it (D-041/D-042).
          point.dRel = dRel
          point.yRel = yRel
          point.vRel = track.floor_vrel
          point.deprecated.measured = False
        elif point is not None:
          # No trusted velocity was ever established for this identity, so there is nothing to
          # coast and no way to publish a defensible vRel.
          self.pts.pop(track_id, None)

        # Do not let a coast-only range observation become a future derivative baseline.
        track.prev_frame_idx = idx0
        track.prev_life = life
        track.last_seen_nanos = now
        track.wire_slot = slot
        for old_slot, old_id in enumerate(self._slot_track_ids):
          if old_slot != slot and old_id == track_id:
            self._slot_track_ids[old_slot] = None
        self._slot_track_ids[slot] = track_id
        continue

      track.samples.append((now_s, dRel))
      track.range_anchor = (now_s, dRel)
      sample_count = len(track.samples)
      if track.recovered:
        track.strict_clean_count = 0 if degraded else track.strict_clean_count + 1
        if track.strict_clean_count >= BOSCH_A_REANCHOR_WINDOW:
          track.recovered = False

      # Prefer qualified native U11; otherwise the range-ratio field. The raw one-sweep derivative is
      # never published as a measurement -- see the coast/drop branch above, which intercepts before
      # this point whenever neither native U11 nor the ratio field is usable.
      if direct_vrel is not None:
        vRel = direct_vrel
      else:
        vRel = ratio_vrel
      trustworthy_vrel = True

      # A birth observation has no range-rate yet. Keep it as history, but do not publish a RadarPoint
      # until a second coherent observation of the same CAN identity supplies a finite derivative.
      # A point the floor already published (BOSCH_A_U11_FLOOR_UNMEASURED) is not a birth: keep it on its first
      # accepted sample rather than delete it (D-041/D-042).
      matured = (sample_count >= 2 or (track.floor_vrel is not None and track_id in self.pts)) and vRel is not None and \
        math.isfinite(vRel)
      if vRel is None and track.floor_vrel is not None and track_id in self.pts:
        # A floor-published point's first accepted range with no U11 and no ratio: still no velocity measurement,
        # so keep the floor bound unmeasured rather than delete the point (D-041/D-042).
        trustworthy_vrel = False
        point = self.pts[track_id]
        point.dRel = dRel
        point.yRel = yRel
        point.vRel = track.floor_vrel
        point.deprecated.measured = False
        matured = None
      if trustworthy_vrel:
        track.last_trusted_vrel = vRel
        track.last_trusted_vrel_nanos = now
        track.floor_vrel = None
        track.floor_run.clear()
      if matured and track_id not in self.pts:
        self.pts[track_id] = structs.RadarData.RadarPoint()
        self.pts[track_id].trackId = track_id
        self.pts[track_id].deprecated.aRel = float('nan')
        self.pts[track_id].deprecated.yvRel = float('nan')

      if matured:
        self.pts[track_id].dRel = dRel
        self.pts[track_id].yRel = yRel
        self.pts[track_id].vRel = vRel
        self.pts[track_id].deprecated.measured = True
      elif matured is not None:
        self.pts.pop(track_id, None)

      track.prev_frame_idx = idx0
      track.prev_life = life
      track.last_seen_nanos = now
      track.wire_slot = slot
      for old_slot, old_id in enumerate(self._slot_track_ids):
        if old_slot != slot and old_id == track_id:
          self._slot_track_ids[old_slot] = None
      self._slot_track_ids[slot] = track_id

    self.point_statuses = {track_id: (self._tracks[track_id].status if track_id in self._tracks else 0)
                           for track_id in sorted(self.pts)}
    ret.points = [self.pts[track_id] for track_id in sorted(self.pts)]
    return ret
