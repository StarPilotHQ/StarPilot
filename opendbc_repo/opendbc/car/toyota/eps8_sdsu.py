"""Eight-byte EPS smartDSU ownership, separate from EPS5 and radar advertisements."""

from opendbc.car import structs
from opendbc.car.toyota.values import ARS510_RADAR_CAR, CAR, ToyotaFlags

CARS = frozenset(
  (
    CAR.TOYOTA_ALPHARD_TSS2,
    CAR.TOYOTA_AVALON_TSS2,
    CAR.TOYOTA_CAMRY_TSS2,
    CAR.TOYOTA_COROLLA_TSS2,
    CAR.TOYOTA_HIGHLANDER_TSS2,
    CAR.TOYOTA_PRIUS_TSS2,
    CAR.TOYOTA_RAV4_TSS2,
    CAR.TOYOTA_MIRAI,
    CAR.LEXUS_ES_TSS2,
    CAR.LEXUS_NX_TSS2,
    CAR.LEXUS_LC_TSS2,
    CAR.LEXUS_RX_TSS2,
    CAR.LEXUS_IS_TSS2,
    CAR.LEXUS_RC_TSS2,
    CAR.TOYOTA_AVALON_2019,
    CAR.LEXUS_LS,
    CAR.TOYOTA_CAMRY,
    CAR.TOYOTA_CHR,
    CAR.TOYOTA_CHR_TSS2,
    CAR.TOYOTA_RAV4_TSS2_2022,
    CAR.TOYOTA_RAV4_TSS2_2023,
  )
)
WORDS = frozenset((0x8049, 0x8249, 0x8449, 0x8649))


def words(candidate):
  return (0x8449, 0x8649) if candidate == CAR.TOYOTA_RAV4_TSS2_2023 else (0x8049, 0x8249)


def hardware_supported(candidate, fingerprint, car_fw):
  if (
    candidate not in CARS
    or fingerprint.get(0, {}).get(0x2FF) != 8
    or any(0x2FF in messages and (bus != 2 or messages[0x2FF] != 8) for bus, messages in fingerprint.items() if bus != 0)
    or any(address in messages for messages in fingerprint.values() for address in (0x201, 0x2AA))
  ):
    return False
  # TSS2 camera ACC is normally on bus2. It is not evidence of a DSU bypass.
  if not CAR(candidate).config.flags & ToyotaFlags.TSS2 and any(fingerprint.get(2, {}).get(address, 8) != 8 for address in (0x343, 0x4CB)):
    return False
  from opendbc.car.toyota.fingerprints import FW_VERSIONS

  known = FW_VERSIONS[candidate]
  required = {structs.CarParams.Ecu.eps, structs.CarParams.Ecu.fwdCamera}
  if any(ecu == structs.CarParams.Ecu.fwdRadar for ecu, _, _ in known):
    required.add(structs.CarParams.Ecu.fwdRadar)
  for ecu in required:
    observed = [fw for fw in car_fw if fw.ecu == ecu]
    if not observed or any(
      fw.logging or fw.bus != 0 or fw.brand != "toyota" or bytes(fw.fwVersion) not in known.get((ecu, fw.address, fw.subAddress or None), ()) for fw in observed
    ):
      return False
  return True


def apply(cp, alpha_long):
  cp.flags |= int(ToyotaFlags.LONG_FILTER)
  cp.flags &= ~int(ToyotaFlags.DISABLE_RADAR)
  gated = bool(cp.flags & (ToyotaFlags.NO_DSU | ToyotaFlags.RADAR_ACC))
  cp.alphaLongitudinalAvailable = gated
  cp.openpilotLongitudinalControl = bool(alpha_long or not gated or cp.flags & ToyotaFlags.TSS2 and not cp.flags & ToyotaFlags.RADAR_ACC)
  cp.safetyConfigs[0].safetyParam = words(cp.carFingerprint)[not cp.openpilotLongitudinalControl]


def configured(cp):
  from opendbc.car.toyota.pedal_composite import parent_cp
  cp = parent_cp(cp)
  if (
    cp is None
    or cp.brand != "toyota"
    or cp.carFingerprint not in CARS
    or cp.notCar
    or cp.passive
    or cp.dashcamOnly
    or cp.secOcRequired
    or not cp.pcmCruise
    or len(cp.safetyConfigs) != 1
  ):
    return False
  required = int(CAR(cp.carFingerprint).config.flags | ToyotaFlags.LONG_FILTER)
  if CAR(cp.carFingerprint).config.flags & ToyotaFlags.TSS2:
    required |= int(ToyotaFlags.RAISED_ACCEL_LIMIT)
  allowed = required | int(ToyotaFlags.HYBRID | ToyotaFlags.HAS_BSM)
  if cp.carFingerprint in ARS510_RADAR_CAR:
    allowed |= int(ToyotaFlags.ARS510_RADAR)
  expected_type = structs.CarParams.SteerControlType.angle if cp.carFingerprint == CAR.TOYOTA_RAV4_TSS2_2023 else structs.CarParams.SteerControlType.torque
  return bool(
    cp.flags & required == required
    and not int(cp.flags) & ~allowed
    and cp.steerControlType == expected_type
    and cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.toyota
    and cp.safetyConfigs[0].safetyParam == words(cp.carFingerprint)[not cp.openpilotLongitudinalControl]
  )


def prepare_stock(cp):
  from opendbc.car.toyota import pedal_composite
  if pedal_composite.marked(cp):
    return pedal_composite.prepare_stock(cp)
  if not configured(cp):
    return False
  cp.openpilotLongitudinalControl = False
  cp.safetyConfigs[0].safetyParam = words(cp.carFingerprint)[1]
  cp.autoResumeSng = False
  return True
