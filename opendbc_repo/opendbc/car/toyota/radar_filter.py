"""Dom's bus-0 radar CAN-filter paths for qualified RAV4, Camry and C-HR configurations."""
from opendbc.car import structs
from opendbc.car.toyota.values import CAR, ToyotaFlags

LONG_WORD = 0x1449
STOCK_WORD = 0x1649
PROFILES = {
  CAR.TOYOTA_CAMRY: (structs.CarParams.SteerControlType.torque, 0x1049, 0x1249),
  CAR.TOYOTA_CHR: (structs.CarParams.SteerControlType.torque, 0x1049, 0x1249),
  CAR.TOYOTA_CHR_TSS2: (structs.CarParams.SteerControlType.torque, 0x1049, 0x1249),
  CAR.TOYOTA_RAV4_TSS2_2022: (structs.CarParams.SteerControlType.torque, 0x1049, 0x1249),
  CAR.TOYOTA_RAV4_TSS2_2023: (structs.CarParams.SteerControlType.angle, LONG_WORD, STOCK_WORD),
}


def _cluster_radar_bus(candidate, bus, messages):
  # The 2018–20 Camry and 2017–20 C-HR expose an independent eight-byte 2FF stream alongside
  # its six cluster-radar frames on bus1 (cluster_radar.CLUSTERS). It is not
  # the four-byte filter advertisement or a bus0 smartDSU status source.
  return (candidate in (CAR.TOYOTA_CAMRY, CAR.TOYOTA_CHR) and bus == 1 and messages.get(0x2FF) == 8 and
          all(messages.get(address) == 8 for address in range(0x680, 0x686)))


def hardware_supported(candidate, fingerprint):
  # The filter's advertisement is a hardware discriminator, not an actuation
  # permission or a copy of ACC_CONTROL to decode under a different checksum.
  # Radar filters can use a four-byte 2FF advertisement, as observed on
  # the 2025 RAV4. This is distinct from legacy eight-byte SDSU status.
  # Panda discovery may also expose its forwarded camera-bus copy.
  if (candidate in PROFILES and fingerprint.get(0, {}).get(0x2FF) == 4 and
      not any(address in messages for messages in fingerprint.values() for address in (0x201, 0x2AA)) and
      all((bus in (0, 2) and messages[0x2FF] == 4) or _cluster_radar_bus(candidate, bus, messages)
          for bus, messages in fingerprint.items() if 0x2FF in messages)):
    return True
  return (candidate in PROFILES and fingerprint.get(0, {}).get(0x2AA) == 8 and
          all(bus in (0, 2) and frames[0x2AA] == 8 for bus, frames in fingerprint.items() if 0x2AA in frames) and
          not any(0x201 in frames or 0x2FF in frames and not _cluster_radar_bus(candidate, bus, frames)
                  for bus, frames in fingerprint.items()))


def configured(cp):
  from opendbc.car.toyota.pedal_composite import parent_cp
  cp = parent_cp(cp)
  profile = PROFILES.get(cp.carFingerprint) if cp is not None else None
  return bool(profile is not None and cp.brand == 'toyota' and
              cp.flags & ToyotaFlags.RADAR_CAN_FILTER and not cp.flags & ToyotaFlags.DISABLE_RADAR and
              cp.steerControlType == profile[0] and len(cp.safetyConfigs) == 1 and
              cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.toyota and
              cp.safetyConfigs[0].safetyParam == (profile[1] if cp.openpilotLongitudinalControl else profile[2]))


def prepare_stock(cp):
  from opendbc.car.toyota import pedal_composite
  if pedal_composite.marked(cp):
    return pedal_composite.prepare_stock(cp)
  if not configured(cp):
    return False
  cp.openpilotLongitudinalControl = False
  cp.safetyConfigs[0].safetyParam = PROFILES[cp.carFingerprint][2]
  cp.autoResumeSng = False
  return True
