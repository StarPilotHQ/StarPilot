"""Camera-bus DSU bypass identity and independent physical source ownership."""

from opendbc.can import CANParser
from opendbc.car import structs
from opendbc.car.toyota.config_cache import cached_config
from opendbc.car.toyota.toyotacan import toyota_checksum
from opendbc.car.toyota.values import CAR, ToyotaFlags

EPS5_CARS = frozenset(
  (CAR.TOYOTA_AVALON, CAR.TOYOTA_HIGHLANDER, CAR.TOYOTA_RAV4, CAR.TOYOTA_RAV4H, CAR.TOYOTA_SIENNA, CAR.LEXUS_ES, CAR.LEXUS_NX, CAR.LEXUS_RX)
)
EPS8_CARS = frozenset((CAR.TOYOTA_AVALON_2019, CAR.TOYOTA_CAMRY, CAR.TOYOTA_CHR, CAR.LEXUS_LS, CAR.TOYOTA_PRIUS_RETROFIT))
ALT_MAIN_CARS = frozenset((CAR.LEXUS_IS, CAR.LEXUS_RC, CAR.LEXUS_GS_F))
PROFILES = dict.fromkeys(EPS5_CARS, 0x8849)
PROFILES.update(dict.fromkeys(EPS8_CARS, 0x9849))
PROFILES.update(
  {
    CAR.TOYOTA_PRIUS: 0x9842,
    CAR.TOYOTA_COROLLA: 0x8958,
    CAR.TOYOTA_MATRIX_RETROFIT: 0x8958,
    CAR.TOYOTA_PRIUS_V: 0x8964,
    CAR.LEXUS_CTH: 0x8964,
    CAR.LEXUS_IS: 0xC84D,
    CAR.LEXUS_RC: 0xC84D,
    CAR.LEXUS_GS_F: 0xC949,
  }
)
PROFILES.update({CAR.TOYOTA_RAV4: 0x8949, CAR.LEXUS_ES: 0x8949})
AUTO_RESUME_CARS = frozenset((CAR.TOYOTA_HIGHLANDER, CAR.TOYOTA_PRIUS_V, CAR.TOYOTA_RAV4H, CAR.TOYOTA_SIENNA))
WORDS = frozenset(word | stock for word in PROFILES.values() for stock in (0, 0x200))


def hardware_supported(candidate, fingerprint, car_fw):
  if candidate not in PROFILES or any(address in frames for frames in fingerprint.values() for address in (0x201, 0x2FF, 0x2AA)):
    return False
  camera = fingerprint.get(2, {})
  if not any(camera.get(address) == 8 for address in (0x343, 0x4CB)) or any(camera.get(address, 8) != 8 for address in (0x343, 0x4CB)):
    return False
  late_prius = candidate in (CAR.TOYOTA_PRIUS, CAR.TOYOTA_PRIUS_RETROFIT) and any(
    fw.ecu == structs.CarParams.Ecu.fwdCamera and bytes(fw.fwVersion).startswith(b'8646F4705') for fw in car_fw
  )
  if candidate in (CAR.LEXUS_IS, CAR.TOYOTA_CAMRY, CAR.TOYOTA_PRIUS_RETROFIT) or late_prius:
    if not (
      (camera.get(0x343) == 8 and not any(0x343 in fingerprint.get(bus, {}) for bus in (0, 1)))
      or (camera.get(0x4CB) == 8 and 0x4CB not in fingerprint.get(0, {}))
    ):
      return False
  if candidate == CAR.TOYOTA_MATRIX_RETROFIT:
    # This is a manual fingerprint, not a firmware-catalog identity.
    return not car_fw and all(fingerprint.get(0, {}).get(a) == length for a, length in ((0x260, 8), (0x262, 5), (0x224, 8), (0x1D2, 8), (0x1D3, 8)))
  from opendbc.car.toyota.fingerprints import FW_VERSIONS

  known = FW_VERSIONS[CAR.TOYOTA_PRIUS if candidate == CAR.TOYOTA_PRIUS_RETROFIT else candidate]
  for ecu in (structs.CarParams.Ecu.eps, structs.CarParams.Ecu.fwdCamera, structs.CarParams.Ecu.fwdRadar):
    if not any(key[0] == ecu for key in known) and candidate != CAR.TOYOTA_PRIUS_RETROFIT:
      continue
    observed = [fw for fw in car_fw if fw.ecu == ecu]
    if not observed:
      return False
    for fw in observed:
      version = bytes(fw.fwVersion)
      allowed = (
        (version == b'8965B47070' + bytes(6))
        if ecu == structs.CarParams.Ecu.eps and candidate == CAR.TOYOTA_PRIUS_RETROFIT
        else version in known.get((ecu, fw.address, fw.subAddress or None), ())
      )
      if fw.logging or fw.bus != 0 or fw.brand != 'toyota' or not allowed:
        return False
  return True


def apply(cp):
  cp.flags |= int(ToyotaFlags.DSU_BYPASS)
  cp.safetyConfigs[0].safetyParam = PROFILES[cp.carFingerprint]
  cp.openpilotLongitudinalControl = True
  cp.alphaLongitudinalAvailable = False
  cp.autoResumeSng = cp.carFingerprint in AUTO_RESUME_CARS
  if cp.carFingerprint in (CAR.TOYOTA_MATRIX_RETROFIT, CAR.TOYOTA_PRIUS_RETROFIT):
    cp.dashcamOnly = False


@cached_config
def configured(cp):
  from opendbc.car.toyota.pedal_composite import parent_cp
  cp = parent_cp(cp)
  if (
    cp is None
    or cp.brand != 'toyota'
    or cp.carFingerprint not in PROFILES
    or cp.notCar
    or cp.passive
    or cp.dashcamOnly
    or cp.secOcRequired
    or not cp.pcmCruise
    or cp.steerControlType != structs.CarParams.SteerControlType.torque
    or len(cp.safetyConfigs) != 1
  ):
    return False
  required = int(CAR[cp.carFingerprint].config.flags | ToyotaFlags.DSU_BYPASS)
  allowed = required | int(ToyotaFlags.HYBRID | ToyotaFlags.ZSS)
  cfg = cp.safetyConfigs[0]
  return (
    cp.flags & required == required
    and not cp.flags & ~allowed
    and not cp.alphaLongitudinalAvailable
    and cp.autoResumeSng == (cp.openpilotLongitudinalControl and cp.carFingerprint in AUTO_RESUME_CARS)
    and cfg.safetyModel == structs.CarParams.SafetyModel.toyota
    and cfg.safetyParam == PROFILES[cp.carFingerprint] | (0 if cp.openpilotLongitudinalControl else 0x200)
  )


def enabled(cp):
  return configured(cp) and cp.openpilotLongitudinalControl


def prepare_stock(cp):
  from opendbc.car.toyota import pedal_composite
  if pedal_composite.marked(cp):
    return pedal_composite.prepare_stock(cp)
  if not configured(cp):
    return False
  cp.openpilotLongitudinalControl = False
  cp.autoResumeSng = False
  cp.safetyConfigs[0].safetyParam = PROFILES[cp.carFingerprint] | 0x200
  return True


class BypassCANParser(CANParser):
  def __init__(self, *args, eps_length=8, alternate_main=False):
    super().__init__(*args)
    self.eps_length = eps_length
    self.main_address = 0x365 if alternate_main else 0x1D3
    self.main_length = 7 if alternate_main else 8
    self.main_valid = False
    self.eps_valid = False
    self.acc_valid = False
    self.ambiguous = False

  def update(self, strings, sendcan=False):
    batches = []
    for stamp, frames in strings:
      accepted = []
      for address, data, bus in frames:
        # pandad marks TX confirmations/rejections with bus >= 128; they
        # are not received hardware (see panda.h CAN_*_BUS_OFFSET).
        if bus >= 128:
          continue
        if address in (0x201, 0x2FF, 0x2AA):
          self.ambiguous = True
        if bus == self.bus:
          if address in (self.main_address, 0x262, 0x343):
            size = self.main_length if address == self.main_address else self.eps_length if address == 0x262 else 8
            valid = len(data) == size and (address == 0x365 or data[-1] == toyota_checksum(address, None, bytearray(data)))
            field = 'main_valid' if address == self.main_address else 'eps_valid' if address == 0x262 else 'acc_valid'
            setattr(self, field, valid)
            if not valid:
              continue
        accepted.append((address, data, bus))
      batches.append((stamp, accepted))
    return super().update(batches, sendcan)
