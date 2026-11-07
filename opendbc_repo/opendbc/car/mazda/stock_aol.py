from opendbc.car import structs
from opendbc.car.mazda.values import CAR, MazdaSafetyFlags

# Stock GEN1 AOL. These two platforms have verified MAIN/LKAS sources and keep
# the original admission and behaviour unchanged.
CARS = frozenset((CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))

# GEN2 AOL. A dedicated profile, not a broadened version of the GEN1 predicate:
# GEN2 has no 0x21C/0x241/0x243 sources, so it must not reuse the GEN1 signal
# set, and it must not be enabled by forcing alternative-experience bit 32
# alone (reports/mazda-gen2-aol-implementation-spec.md section 1).
#
# Initial admission is the platform the specification targets. Other GEN2
# platforms stay excluded until each one is separately validated.
GEN2_CARS = frozenset((CAR.MAZDA_3_2019,))

# The GEN2 AOL profile is carried by the existing GEN2 safety parameter; the
# alternative-experience bit marks the AOL request, exactly as stock GEN1 does.
GEN2_AOL_SAFETY_PARAM = int(MazdaSafetyFlags.GEN2)

# Evidence gates from section 4 and section 5 of the implementation
# specification. Each one stays False until its work package is closed,
# reviewed and recorded with the evidence the specification requires. The
# configuration predicate below is already complete, so closing a gate is a
# one-line change rather than a redesign.
GEN2_EVIDENCE_GATES = {
  # W1: closed 2026-10-30. CRUZE_STATE CRZ_STATE is the GEN2 ACC main switch:
  # mazda_2019.dbc defines 0 = CRUISE_DISABLED, 1 = CRUISE_READY,
  # 2 = CRUISE_ENABLED, 4 = GAS_OVERRIDE, and leaves 3 and 5..7 undefined. A
  # parked capture showed 16 alternating 0<->1 transitions, each 19-162 ms after
  # a CRZ_BTNS MODE_ACC edge, which establishes the off/standby pair only.
  'main': True,
  # W1/W2: CLOSED 2026-10-30 by explicit delegation, not by a signal. Two
  # all-id captures found no defensible door or seatbelt bit on the buses the
  # comma sees; GEN2 carState keeps doorOpen/seatbeltUnlatched False and relies on
  # the car refusing ACC/LKAS in those states. Owner decision, with the residual
  # risk recorded in reports/mazda-gen2-aol-implementation-spec.md
  # ("Decision record - readiness").
  'readiness': True,
  # W4: OPEN. Round 3 confirmed the violation/liveness/re-engagement behaviour
  # (nine mandatory reproductions, no transition defect). The two remaining items
  # of safety action plan 4.1 are now addressed in code and test:
  #   3. CPU operating states - only RUN (3) is healthy; the port documents
  #      DISCOVER=0/OFF=1/DRIVER_OVER=2/RUN=3 and a parked capture shows 3 on all
  #      four CPUs. Every other value revokes, per "unknown states are not
  #      healthy". DRIVER_OVER recovers rather than latching.
  #   9. checksum/counter - TI_FEEDBACK allocates all 64 bits to torque, the four
  #      violation bytes and the four state nibbles, so the message carries no
  #      integrity field to check. Liveness is enforced instead.
  # W4: CLOSED 2026-10-30 by owner decision declining the Phase 8 bench/HIL
  # matrix, recorded in reports/mazda-gen2-aol-implementation-spec.md
  # ("Decision record - TI2 health without bench"). Evidence backing the decision:
  # steady state RUN=3 with zero violations across every capture, boot transients
  # only, and no DRIVER_OVER ever reported by this firmware, so the conservative
  # rule (only state 3 healthy, any non-zero violation unhealthy) is what the car
  # actually depends on. CarState surfaces violations as steerFaultPermanent.
  'ti2_faults': True,
  # W4: CLOSED 2026-10-30. The GEN2 ACC gate now asks
  # longitudinal_controls_allowed() (the framework predicate: controls_allowed
  # with no AOL policy, the longitudinal axis permission with one), so a
  # lateral-only grant cannot authorize a modified 0x220, and the exact-stock
  # passthrough, freshness and checksum rules apply whenever that authority is
  # absent. Proven in simulation with a lateral-only test policy
  # (test_lateral_only_authority_cannot_modify_acc); the controller side is
  # covered by test_gen2_acc_passthrough.py.
  'acc_isolation': True,
}


def gen2_configuration(cp, *, marked_only=False):
  """Everything about the target car that can be checked today.

  Deliberately excludes TI2 presence: CarParams carries no TI2 marker for GEN2
  (only GEN1 sets TORQUE_INTERCEPTOR), so "verified TI2 hardware" is proven at
  runtime by the fault contract instead of here.

  Automatic only, per the specification's initial scope. Openpilot longitudinal
  is allowed: the profile grants lateral and leaves the longitudinal axis to
  whatever is independently authorized (stock, openpilot or BlendedACC).
  """
  return (cp.brand == "mazda" and cp.carFingerprint in GEN2_CARS and
          int(cp.flags) == int(MazdaSafetyFlags.GEN2) and
          not cp.passive and not cp.notCar and not cp.dashcamOnly and
          cp.transmissionType == structs.CarParams.TransmissionType.automatic and
          cp.fingerprintSource != structs.CarParams.FingerprintSource.fixed and
          cp.pcmCruise and len(cp.safetyConfigs) == 1 and
          cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.mazda and
          cp.safetyConfigs[0].safetyParam == GEN2_AOL_SAFETY_PARAM and
          cp.alternativeExperience in ((32,) if marked_only else (0, 32)))


def gen2_qualified(cp, gates=GEN2_EVIDENCE_GATES, *, marked_only=False):
  """GEN2 AOL admission. Blocked while any evidence gate is open.

  The gate table must be exactly the known set: an empty, partial or extended
  table can never admit the car (all({}.values()) is True, so a bare
  all(gates.values()) would silently admit it).
  """
  # Key views compare as sets without allocating two new sets on every call (this
  # runs several times per card frame).
  return bool(gates.keys() == GEN2_EVIDENCE_GATES.keys() and all(gates.values()) and
              gen2_configuration(cp, marked_only=marked_only))


def qualified(cp, *, marked_only=False):
  """Stock GEN1 AOL admission. Unchanged by the GEN2 profile."""
  return (cp.brand == "mazda" and cp.carFingerprint in CARS and int(cp.flags) == int(MazdaSafetyFlags.GEN1) and
          not cp.passive and not cp.notCar and not cp.dashcamOnly and
          cp.transmissionType != structs.CarParams.TransmissionType.manual and
          cp.fingerprintSource != structs.CarParams.FingerprintSource.fixed and
          cp.pcmCruise and not cp.openpilotLongitudinalControl and len(cp.safetyConfigs) == 1 and
          cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.mazda and
          cp.safetyConfigs[0].safetyParam == 0 and cp.alternativeExperience in ((32,) if marked_only else (0, 32)))


def admitted(cp, *, marked_only=False):
  """AOL admission for either profile. GEN1 stock behaviour is unchanged."""
  return qualified(cp, marked_only=marked_only) or gen2_qualified(cp, marked_only=marked_only)


# Panda withholds lateral while a temporary restriction holds (standstill: its
# vehicle_moving gate; a TI2 pause; not in Drive) and keeps its AOL token. Its
# status reaches the host late, and the host may judge a sample up to
# SAFETY_MAX_AGE_NS (200 ms) old. A denial seen just after the restriction ends was
# sampled while it held, so the host keeps its intent for this long after release.
# Past it, a denial is a real revocation again. Host policy only: panda is unchanged.
RESTRICTION_RELEASE_GRACE_NS = 250_000_000


def within_restriction_grace(restricted_ns, now_ns):
  """A temporary restriction last held at restricted_ns: true now and for the release grace after."""
  return bool(restricted_ns and 0 <= now_ns - restricted_ns <= RESTRICTION_RELEASE_GRACE_NS)


def temporary_restriction(cp, state):
  return bool(state is not None and admitted(cp, marked_only=True) and state.canValid and not state.canTimeout and
              not state.steerFaultPermanent and (state.gearShifter != structs.CarState.GearShifter.drive or
                                                 state.steerFaultTemporary or state.standstill or (cp.minSteerSpeed > 0 and state.vEgo < cp.minSteerSpeed)))


def native_observation(native, *, latched, panda_ready, restricted, now_ns, pending_since_ns):
  lost = not panda_ready or native is None
  if not latched or lost or restricted:
    return 0, lost, False
  if native.requestedLateral and native.lateralAllowed:
    return 0, False, False
  pending_since_ns = pending_since_ns or now_ns
  return pending_since_ns, False, now_ns - pending_since_ns >= 200_000_000
