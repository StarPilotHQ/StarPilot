"""Exact EPS5 four-byte filter ownership; advertisements never carry SDSU state."""

from opendbc.can import CANParser
from opendbc.car import structs
from opendbc.car.toyota import legacy_sdsu, rav4h_filter
from opendbc.car.toyota.values import CAR, MIN_ACC_SPEED, ToyotaFlags
from opendbc.car.toyota.toyotacan import toyota_checksum

PROFILES = dict.fromkeys(rav4h_filter.EPS5_SDSU_CARS, (0x9449, 0x9649))
PROFILES.update({candidate: (pair[0] | 0x400, pair[1] | 0x400) for candidate, pair in legacy_sdsu.PROFILES.items()})
WORDS = frozenset(word for pair in PROFILES.values() for word in pair)
ALT_MAIN_CARS = legacy_sdsu.ALT_MAIN_CARS
AUTO_RESUME_CARS = frozenset((CAR.TOYOTA_RAV4H, CAR.TOYOTA_HIGHLANDER, CAR.TOYOTA_SIENNA, CAR.TOYOTA_PRIUS_V))


def hardware_supported(candidate, fingerprint, car_fw):
  if candidate == CAR.TOYOTA_MATRIX_RETROFIT:
    from opendbc.car.toyota import matrix_filter
    return matrix_filter.hardware_supported(candidate, fingerprint, car_fw, length=4)
  if (candidate not in PROFILES or fingerprint.get(0, {}).get(0x2FF) != 4 or
      any(address in messages for messages in fingerprint.values() for address in (0x201, 0x2AA)) or
      any(bus not in (0, 2) or messages[0x2FF] != 4 for bus, messages in fingerprint.items() if 0x2FF in messages) or
      any(fingerprint.get(2, {}).get(address, 8) != 8 for address in (0x343, 0x4CB))):
    return False
  from opendbc.car.toyota.fingerprints import FW_VERSIONS
  known = FW_VERSIONS[candidate]
  eps = structs.CarParams.Ecu.eps
  required = {structs.CarParams.Ecu.fwdCamera, structs.CarParams.Ecu.fwdRadar}
  required.add(eps if any(ecu == eps for ecu, _, _ in known) else structs.CarParams.Ecu.engine)
  for ecu in required | {eps}:
    observed = [fw for fw in car_fw if fw.ecu == ecu]
    if ecu in required and not observed:
      return False
    if any(fw.logging or fw.bus != 0 or fw.brand != 'toyota' or
           bytes(fw.fwVersion) not in known.get((ecu, fw.address, fw.subAddress or None), ()) for fw in observed):
      return False
  return True


def apply(cp):
  cp.flags |= int(ToyotaFlags.RADAR_CAN_FILTER)
  if cp.carFingerprint == CAR.TOYOTA_RAV4H:
    cp.flags |= int(ToyotaFlags.HYBRID)
  cp.safetyConfigs[0].safetyParam = PROFILES[cp.carFingerprint][0]
  cp.openpilotLongitudinalControl = True
  cp.alphaLongitudinalAvailable = False


def configured(cp):
  from opendbc.car.toyota.pedal_composite import parent_cp
  cp = parent_cp(cp)
  if cp is not None and cp.carFingerprint == CAR.TOYOTA_MATRIX_RETROFIT:
    from opendbc.car.toyota import matrix_filter
    return matrix_filter.configured(cp, length=4)
  if (cp is None or cp.brand != 'toyota' or cp.carFingerprint not in PROFILES or cp.notCar or cp.passive or cp.dashcamOnly or
      cp.secOcRequired or not cp.pcmCruise or len(cp.safetyConfigs) != 1 or
      cp.steerControlType != structs.CarParams.SteerControlType.torque):
    return False
  required = int(CAR(cp.carFingerprint).config.flags | ToyotaFlags.RADAR_CAN_FILTER)
  if cp.flags & required != required or int(cp.flags) & ~int(required | ToyotaFlags.HYBRID):
    return False
  if cp.carFingerprint == CAR.TOYOTA_RAV4H and not cp.flags & ToyotaFlags.HYBRID:
    return False
  config = cp.safetyConfigs[0]
  return (config.safetyModel == structs.CarParams.SafetyModel.toyota and
          config.safetyParam == PROFILES[cp.carFingerprint][not cp.openpilotLongitudinalControl] and
          cp.autoResumeSng == bool(cp.openpilotLongitudinalControl and cp.carFingerprint in AUTO_RESUME_CARS))


def enabled(cp):
  return configured(cp) and cp.openpilotLongitudinalControl


def prepare_stock(cp):
  from opendbc.car.toyota import pedal_composite
  if pedal_composite.marked(cp):
    return pedal_composite.prepare_stock(cp)
  if not configured(cp):
    return False
  cp.openpilotLongitudinalControl = False
  if cp.carFingerprint == CAR.TOYOTA_HIGHLANDER:
    cp.minEnableSpeed = MIN_ACC_SPEED
  cp.safetyConfigs[0].safetyParam = PROFILES[cp.carFingerprint][1]
  cp.autoResumeSng = False
  return True


class LegacyAdvertCANParser(CANParser):
  """Physical EPS5 and optional DSU MAIN remain distinct from FD_BUTTON."""

  def __init__(self, *args, alternate_main=False):
    super().__init__(*args)
    self.main_address = 0x365 if alternate_main else 0x1D3
    self.main_length = 7 if alternate_main else 8
    self.main_valid = False
    self.eps_valid = False
    self.advert_distance_button = 0
    self.advert_button_ns = 0

  def update(self, strings, sendcan=False):
    batches = []
    for stamp, frames in strings:
      accepted = []
      for address, data, bus in frames:
        if bus == self.bus:
          if address == self.main_address:
            self.main_valid = len(data) == self.main_length and (
              self.main_address == 0x365 or data[-1] == toyota_checksum(address, None, bytearray(data)))
            if not self.main_valid:
              continue
          if address == 0x262:
            self.eps_valid = len(data) == 5 and data[-1] == toyota_checksum(address, None, bytearray(data))
            if not self.eps_valid:
              continue
          if address == 0x2FF:
            self.advert_button_ns = stamp if len(data) == 4 else 0
            self.advert_distance_button = (data[0] >> 7) if len(data) == 4 else 0
            continue
        accepted.append((address, data, bus))
      batches.append((stamp, accepted))
    return super().update(batches, sendcan)
