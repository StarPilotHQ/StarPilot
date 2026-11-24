"""Bare legacy Toyota/Lexus stock-cruise AOL, without claiming adapter authority.

The profile binds EPS packet length, brake address, torque scale and MAIN source.
It never enables longitudinal output or substitutes a smartDSU/pedal receiver.
"""
from opendbc.can import CANParser
from opendbc.car import structs
from opendbc.car.toyota.toyotacan import toyota_checksum
from opendbc.car.toyota.values import CAR, ToyotaFlags

PROFILES = dict.fromkeys((CAR.TOYOTA_AVALON, CAR.TOYOTA_HIGHLANDER, CAR.TOYOTA_RAV4H,
                         CAR.TOYOTA_SIENNA, CAR.LEXUS_NX, CAR.LEXUS_RX), 0x5249)
PROFILES.update(dict.fromkeys((CAR.TOYOTA_RAV4, CAR.LEXUS_ES), 0x5349))
PROFILES.update({CAR.TOYOTA_COROLLA: 0x5358, CAR.TOYOTA_PRIUS_V: 0x5364, CAR.LEXUS_CTH: 0x5364,
                 CAR.LEXUS_RC: 0x564D, CAR.LEXUS_GS_F: 0x5749, CAR.TOYOTA_PRIUS: 0x6242})
PROFILES.update(dict.fromkeys((CAR.TOYOTA_AVALON_2019, CAR.TOYOTA_CAMRY, CAR.TOYOTA_CHR, CAR.LEXUS_LS), 0x6249))
EPS5_WORDS = frozenset((0x5249, 0x5349, 0x5358, 0x5364, 0x564D, 0x5749))
ALT_MAIN_WORDS = frozenset((0x564D, 0x5749))
WORDS = frozenset(PROFILES.values())


def hardware_supported(candidate, fingerprint):
  if candidate not in PROFILES:
    return False
  from opendbc.car.toyota.radar_filter import _cluster_radar_bus

  for bus, frames in fingerprint.items():
    if 0x201 in frames or 0x2AA in frames:
      return False
    if 0x2FF in frames and not (candidate == CAR.TOYOTA_CAMRY and _cluster_radar_bus(candidate, bus, frames)):
      return False
  from opendbc.car.toyota.dsu_bypass import corolla_startup_mirror
  if corolla_startup_mirror(candidate, fingerprint):
    return True
  camera = fingerprint.get(2, {})
  if 0x4CB in camera:
    return False
  if 0x343 not in camera:
    return True

  # The stock Camry can mirror its powertrain bus at startup, including ACC_CONTROL.
  # An independent camera fingerprint is still treated as possible takeover hardware.
  pt = fingerprint.get(0, {})
  return (candidate == CAR.TOYOTA_CAMRY and pt == camera and
          all(pt.get(address) == 8 for address in (0xAA, 0x262, 0x343)))


def apply(cp):
  if (cp.carFingerprint not in PROFILES or cp.openpilotLongitudinalControl or cp.dashcamOnly or cp.passive or cp.notCar):
    return False
  cp.safetyConfigs[0].safetyParam = PROFILES[cp.carFingerprint]
  return True


def configured(cp):
  if (cp is None or cp.brand != 'toyota' or cp.carFingerprint not in PROFILES or cp.notCar or cp.passive or
      cp.dashcamOnly or cp.secOcRequired or cp.openpilotLongitudinalControl or not cp.pcmCruise or
      cp.steerControlType != structs.CarParams.SteerControlType.torque or len(cp.safetyConfigs) != 1 or cp.autoResumeSng):
    return False
  required = int(CAR(cp.carFingerprint).config.flags)
  allowed = required | int(ToyotaFlags.HYBRID)
  if cp.carFingerprint == CAR.TOYOTA_PRIUS:
    allowed |= int(ToyotaFlags.ZSS)
  config = cp.safetyConfigs[0]
  return bool(cp.flags & required == required and not int(cp.flags) & ~allowed and
              config.safetyModel == structs.CarParams.SafetyModel.toyota and config.safetyParam == PROFILES[cp.carFingerprint])


class StockCANParser(CANParser):
  def __init__(self, *args, stock_word, **kwargs):
    super().__init__(*args, **kwargs)
    self.main_address = 0x365 if stock_word in ALT_MAIN_WORDS else 0x1D3
    self.eps_size = 5 if stock_word in EPS5_WORDS else 8
    self.main_valid = self.eps_valid = False

  def update(self, strings, sendcan=False):
    batches = []
    for stamp, frames in strings:
      accepted = []
      for address, data, bus in frames:
        if bus == self.bus and address in (self.main_address, 0x262):
          expected = self.eps_size if address == 0x262 else (7 if address == 0x365 else 8)
          valid = len(data) == expected and (address == 0x365 or data[-1] == toyota_checksum(address, None, bytearray(data)))
          if address == self.main_address:
            self.main_valid = valid
          else:
            self.eps_valid = valid
          if not valid:
            continue
        accepted.append((address, data, bus))
      batches.append((stamp, accepted))
    return super().update(batches, sendcan)
