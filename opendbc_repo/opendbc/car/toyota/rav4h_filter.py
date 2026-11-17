"""Exact EPS5 Toyota/Lexus smartDSU ownership; separate from Prius retrofit."""
from opendbc.can import CANParser
from opendbc.car import structs
from opendbc.car.config_cache import cached_config
from opendbc.car.toyota.values import CAR, ToyotaFlags

# Bit15 is a distinct exact-profile marker; reserve Dom bit14 for Lexus ALT_CRUISE.
EPS5_SDSU_CARS = frozenset((
  CAR.TOYOTA_RAV4H, CAR.TOYOTA_HIGHLANDER, CAR.TOYOTA_SIENNA, CAR.LEXUS_NX, CAR.LEXUS_RX, CAR.TOYOTA_AVALON,
))
LONG_WORD = 0x9049
STOCK_WORD = 0x9249


def hardware_supported(candidate, fingerprint, car_fw):
  if (candidate not in EPS5_SDSU_CARS or fingerprint.get(0, {}).get(0x2FF) != 8 or
      any(0x2FF in messages and (bus != 2 or messages[0x2FF] != 8) for bus, messages in fingerprint.items() if bus != 0) or
      any(address in messages for messages in fingerprint.values() for address in (0x201, 0x2AA)) or
      any(fingerprint.get(2, {}).get(address, 8) != 8 for address in (0x343, 0x4CB))):
    return False
  from opendbc.car.toyota.fingerprints import FW_VERSIONS
  expected = FW_VERSIONS[candidate]
  required = (structs.CarParams.Ecu.eps, structs.CarParams.Ecu.fwdCamera, structs.CarParams.Ecu.fwdRadar)
  for ecu in required:
    observed = [fw for fw in car_fw if fw.ecu == ecu]
    if not observed:
      return False
    for fw in observed:
      if fw.logging or fw.bus != 0 or fw.brand != "toyota":
        return False
      key = (ecu, fw.address, fw.subAddress or None)
      if bytes(fw.fwVersion) not in expected.get(key, ()):
        return False
  return True


@cached_config
def configured(cp):
  from opendbc.car.toyota.pedal_composite import parent_cp
  cp = parent_cp(cp)
  from opendbc.car.toyota import eps8_sdsu, legacy_sdsu
  if legacy_sdsu.configured(cp) or eps8_sdsu.configured(cp):
    return True
  if (cp is None or cp.brand != "toyota" or cp.carFingerprint not in EPS5_SDSU_CARS or
      not cp.pcmCruise or cp.dashcamOnly or cp.notCar or cp.passive or
      cp.steerControlType != structs.CarParams.SteerControlType.torque or len(cp.safetyConfigs) != 1):
    return False
  flags = int(ToyotaFlags.LONG_FILTER) | (int(ToyotaFlags.HYBRID) if cp.flags & ToyotaFlags.HYBRID else 0)
  if cp.carFingerprint == CAR.TOYOTA_RAV4H and not cp.flags & ToyotaFlags.HYBRID:
    return False
  hold = bool(cp.flags & ToyotaFlags.AUTO_BRAKE_HOLD)
  if hold and cp.carFingerprint != CAR.TOYOTA_RAV4H:
    return False
  if cp.flags != flags | (int(ToyotaFlags.AUTO_BRAKE_HOLD) if hold else 0):
    return False
  config = cp.safetyConfigs[0]
  return (config.safetyModel == structs.CarParams.SafetyModel.toyota and
          config.safetyParam in (LONG_WORD, STOCK_WORD) and
          cp.openpilotLongitudinalControl == (config.safetyParam == LONG_WORD) and
          (not hold or cp.openpilotLongitudinalControl))


def enabled(cp):
  return configured(cp) and cp.openpilotLongitudinalControl


def prepare_stock(cp):
  from opendbc.car.toyota import pedal_composite
  if pedal_composite.marked(cp):
    return pedal_composite.prepare_stock(cp)
  from opendbc.car.toyota import eps8_sdsu, legacy_sdsu
  if legacy_sdsu.prepare_stock(cp) or eps8_sdsu.prepare_stock(cp):
    return True
  if not configured(cp):
    return False
  cp.flags &= ~int(ToyotaFlags.AUTO_BRAKE_HOLD)
  cp.alternativeExperience &= ~(128 | 256)
  cp.openpilotLongitudinalControl = False
  cp.safetyConfigs[0].safetyParam = STOCK_WORD
  cp.autoResumeSng = False
  return True


class Rav4hCANParser(CANParser):
  """Malformed smartDSU frames cannot refresh this exact owner's source lease."""

  def update(self, strings, sendcan=False):
    frames = [(stamp, [frame for frame in batch if frame[0] != 0x2FF or frame[2] != self.bus or len(frame[1]) == 8])
              for stamp, batch in strings]
    return super().update(frames, sendcan)
