from opendbc.car import structs
from opendbc.car.honda.values import CAR, HondaFlags, HondaSafetyFlags

CLASSIC_BOSCH = frozenset((CAR.HONDA_NBOX_2G, CAR.HONDA_ACCORD, CAR.HONDA_CIVIC_BOSCH,
  CAR.HONDA_CIVIC_BOSCH_DIESEL, CAR.HONDA_CRV_5G, CAR.HONDA_CRV_HYBRID, CAR.ACURA_RDX_3G,
  CAR.HONDA_INSIGHT, CAR.HONDA_E, CAR.HONDA_E_ADVANCE))
RADARLESS = frozenset((CAR.HONDA_CIVIC_2022, CAR.HONDA_HRV_3G, CAR.HONDA_CITY_7G,
  CAR.HONDA_FIT_4G, CAR.ACURA_INTEGRA, CAR.ACURA_ADX))
NIDEC = frozenset((
  CAR.ACURA_ILX,
  CAR.HONDA_CRV,
  CAR.HONDA_CRV_EU,
  CAR.HONDA_CRV_SA,
  CAR.HONDA_FIT,
  CAR.HONDA_FREED,
  CAR.HONDA_HRV,
  CAR.HONDA_CLARITY,
  CAR.HONDA_ODYSSEY,
  CAR.HONDA_ODYSSEY_TWN,
  CAR.ACURA_RDX,
  CAR.HONDA_PILOT,
  CAR.HONDA_RIDGELINE,
  CAR.HONDA_CIVIC,
  CAR.HONDA_ACCORD_9G,
  CAR.ACURA_MDX_3G,
  CAR.ACURA_MDX_3G_MMR,
  CAR.ACURA_TLX_1G,
))
CANFD_STOCK = frozenset((CAR.HONDA_CRV_6G, CAR.HONDA_PILOT_4G,
  CAR.HONDA_PASSPORT_4G, CAR.ACURA_MDX_4G_MMR, CAR.ACURA_TLX_2G_MMR))
ALT_RADAR_STOCK = frozenset((CAR.ACURA_RDX_3G_MMR, CAR.HONDA_ODYSSEY_5G_MMR,
  CAR.ACURA_TLX_2G, CAR.ACURA_MDX_4G))
BOSCH_WORDS = frozenset((0, 1, 8, 9, 10, 11, 16, 17, 80, 81, 512, 513, 1058, 1059, 1570, 1571, 2082, 2083, 2211))
NIDEC_WORDS = frozenset((0, 4, 260, 544, 548, 804))
MANUAL_SELECTION_PORTS = CLASSIC_BOSCH | NIDEC | RADARLESS | CANFD_STOCK | ALT_RADAR_STOCK | {CAR.HONDA_ACCORD_11G}

# qualified() runs several times per 100 Hz frame in card and selfdrived, so
# everything that depends only on this module is computed once, and its flag
# tests use plain ints: IntFlag operators cost microseconds each in Python.
# EPS_ANGLE_LOOP_FW / EPS_FW_UNREAD: Accord-only EPS-firmware detections (interface.py); the angle car keeps AOL, and
# an unread EPS is already a permanent steer fault in carstate, so neither may drop the car out of these owners.
_DETECTED = int(HondaFlags.BOSCH_EXT_HUD | HondaFlags.BOSCH_ALT_BRAKE | HondaFlags.HAS_BSM |
                HondaFlags.EPS_MODIFIED | HondaFlags.HYBRID | HondaFlags.GAS_INTERCEPTOR |
                HondaFlags.EPS_ANGLE_LOOP_FW | HondaFlags.EPS_FW_UNREAD)
_ALLOWED_FLAGS = {str(car): int(car.config.flags) | _DETECTED
                  for car in CLASSIC_BOSCH | NIDEC | RADARLESS | CANFD_STOCK | ALT_RADAR_STOCK | {CAR.HONDA_ACCORD_11G}}
_BOSCH, _BOSCH_RADARLESS, _BOSCH_ALT_BRAKE = int(HondaFlags.BOSCH), int(HondaFlags.BOSCH_RADARLESS), int(HondaFlags.BOSCH_ALT_BRAKE)
_NIDEC, _NIDEC_ALT_SCM_MESSAGES = int(HondaFlags.NIDEC), int(HondaFlags.NIDEC_ALT_SCM_MESSAGES)
_UNSUPPORTED = int(HondaFlags.BOSCH_ALT_RADAR)
_FIXED = structs.CarParams.FingerprintSource.fixed
_TRANSMISSIONS = (structs.CarParams.TransmissionType.automatic, structs.CarParams.TransmissionType.cvt)
_HONDA_NIDEC, _HONDA_BOSCH = structs.CarParams.SafetyModel.hondaNidec, structs.CarParams.SafetyModel.hondaBosch


def qualified(cp, *, marked_only=False):
  if (cp.brand != 'honda' or cp.passive or cp.notCar or cp.dashcamOnly or len(cp.safetyConfigs) not in (1, 2) or
      (len(cp.safetyConfigs) == 2 and not (cp.carFingerprint in CANFD_STOCK and
       cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.noOutput and cp.safetyConfigs[0].safetyParam == 0)) or
      (cp.fingerprintSource == _FIXED and cp.carFingerprint not in MANUAL_SELECTION_PORTS) or cp.transmissionType not in _TRANSMISSIONS or
      cp.alternativeExperience not in ((32,) if marked_only else (0, 32))):
    return False
  safety = cp.safetyConfigs[-1]
  fingerprint = str(cp.carFingerprint)
  allowed = _ALLOWED_FLAGS.get(fingerprint)
  if allowed is None:
    return False
  flags = int(cp.flags)
  if flags & ~allowed:
    return False
  if flags & int(HondaFlags.GAS_INTERCEPTOR) and fingerprint not in NIDEC:
    return False
  if fingerprint in ALT_RADAR_STOCK:
    expected = int(bool(flags & _BOSCH_ALT_BRAKE))
    if cp.openpilotLongitudinalControl:
      expected |= int(HondaSafetyFlags.BOSCH_LONG)
      if cp.alternativeExperience == 32:
        expected |= int(HondaSafetyFlags.ALT_RADAR_AOL | HondaSafetyFlags.AOL_BOSCH_LONG)
    if fingerprint == CAR.ACURA_MDX_4G and cp.alternativeExperience == 32:
      expected |= int(HondaSafetyFlags.STOCK_TJA)
    return (bool(flags & _BOSCH) and bool(flags & int(HondaFlags.BOSCH_ALT_RADAR)) and
            not bool(flags & int(HondaFlags.BOSCH_CANFD)) and bool(cp.pcmCruise) == (not cp.openpilotLongitudinalControl) and
            safety.safetyModel == _HONDA_BOSCH and safety.safetyParam == expected)
  if flags & _UNSUPPORTED:
    return False
  if fingerprint == CAR.HONDA_ACCORD_11G:
    from opendbc.car.honda.accord_mvl_stock import qualified as qualified_mvl
    return qualified_mvl(cp)
  if fingerprint in CANFD_STOCK:
    expected = 16 | int(bool(flags & _BOSCH_ALT_BRAKE))
    return (bool(flags & _BOSCH) and bool(flags & int(HondaFlags.BOSCH_CANFD)) and
            cp.pcmCruise and not cp.openpilotLongitudinalControl and
            safety.safetyModel == _HONDA_BOSCH and safety.safetyParam == expected)
  if flags & int(HondaFlags.BOSCH_CANFD):
    return False
  if fingerprint in NIDEC and flags & _NIDEC and not flags & _BOSCH:
    expected = 4 if flags & _NIDEC_ALT_SCM_MESSAGES else 0
    pedal = bool(flags & int(HondaFlags.GAS_INTERCEPTOR))
    if pedal:
      expected |= int(HondaSafetyFlags.GAS_INTERCEPTOR)
      if cp.alternativeExperience == 32:
        expected |= int(HondaSafetyFlags.NIDEC_PEDAL_AOL)
    if fingerprint == CAR.HONDA_ODYSSEY_TWN and cp.alternativeExperience == 32:
      expected |= 256
    return (bool(cp.pcmCruise) == (not pedal) and cp.openpilotLongitudinalControl and
            safety.safetyModel == _HONDA_NIDEC and safety.safetyParam == expected)
  if fingerprint in CLASSIC_BOSCH and flags & _BOSCH and not flags & _BOSCH_RADARLESS:
    expected = int(bool(flags & _BOSCH_ALT_BRAKE))
    if cp.openpilotLongitudinalControl:
      expected |= int(HondaSafetyFlags.BOSCH_LONG)
      if fingerprint == CAR.ACURA_RDX_3G:
        expected |= int(HondaSafetyFlags.RDX_HIGH_GAS)
      if cp.alternativeExperience == 32:
        expected |= int(HondaSafetyFlags.CLASSIC_LONG_AOL | HondaSafetyFlags.AOL_BOSCH_LONG)
    return (bool(cp.pcmCruise) == (not cp.openpilotLongitudinalControl) and
            safety.safetyModel == _HONDA_BOSCH and safety.safetyParam == expected)
  if fingerprint in RADARLESS and flags & _BOSCH_RADARLESS:
    expected = 8 | int(bool(flags & _BOSCH_ALT_BRAKE)) | (2 if cp.openpilotLongitudinalControl else 0)
    return (bool(cp.pcmCruise) == (not cp.openpilotLongitudinalControl) and
            safety.safetyModel == _HONDA_BOSCH and safety.safetyParam == expected)
  return False


def temporary_restriction(cp, state):
  gears = structs.CarState.GearShifter
  return bool(state is not None and qualified(cp, marked_only=True) and state.canValid and not state.canTimeout
              and not state.steerFaultPermanent and
              (state.gearShifter not in (gears.drive, gears.sport, gears.low, gears.brake) or state.steerFaultTemporary))


def native_observation(native, *, latched, panda_ready, restricted, now_ns, pending_since_ns,
                       previously_acknowledged=False, host_requested_lateral=False):
  lost = not panda_ready or native is None
  if not latched or lost or restricted:
    return 0, lost, False
  if native.requestedLateral and native.lateralAllowed:
    return 0, False, False
  if (previously_acknowledged and host_requested_lateral and
      not native.requestedLateral and not native.lateralAllowed):
    # A current host request lost an established grant, not a cold handshake.
    return 0, False, True
  pending_since_ns = pending_since_ns or now_ns
  return pending_since_ns, False, now_ns - pending_since_ns >= 200_000_000
