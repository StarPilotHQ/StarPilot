"""AOL admission for the ordinary CAN FD torque and physical-button layouts."""

from opendbc.car.structs import CarParams
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.canfd_owner import config_index

# Integer masks avoid constructing IntFlag objects in live 100 Hz admission checks.
_FLAG_CANFD = HyundaiFlags.CANFD.value
_FLAG_CANFD_ALT_BUTTONS = HyundaiFlags.CANFD_ALT_BUTTONS.value
_FLAG_CANFD_ALT_GEARS = HyundaiFlags.CANFD_ALT_GEARS.value
_FLAG_CANFD_ALT_GEARS_2 = HyundaiFlags.CANFD_ALT_GEARS_2.value
_FLAG_CANFD_ANGLE_STEERING = HyundaiFlags.CANFD_ANGLE_STEERING.value
_FLAG_CANFD_CAMERA_SCC = HyundaiFlags.CANFD_CAMERA_SCC.value
_FLAG_CANFD_LKA_STEER_MSG = HyundaiFlags.CANFD_LKA_STEER_MSG.value
_FLAG_CANFD_LKA_STEER_MSG_ALT = HyundaiFlags.CANFD_LKA_STEER_MSG_ALT.value
_FLAG_CANFD_NO_RADAR_DISABLE = HyundaiFlags.CANFD_NO_RADAR_DISABLE.value
_FLAG_CANFD_RADAR_SCC = HyundaiFlags.CANFD_RADAR_SCC.value
_FLAG_CCNC = HyundaiFlags.CCNC.value
_FLAG_EV = HyundaiFlags.EV.value
_FLAG_HYBRID = HyundaiFlags.HYBRID.value
_FLAG_NON_SCC = HyundaiFlags.NON_SCC.value

STOCK_AOL_MARKER = 0x0800
STOCK_EV_CARS = frozenset(
  (
    CAR.HYUNDAI_KONA_EV_2ND_GEN,
    CAR.HYUNDAI_IONIQ_5,
    CAR.HYUNDAI_IONIQ_5_N,
    CAR.KIA_NIRO_EV_2ND_GEN,
    CAR.KIA_EV6,
    CAR.GENESIS_GV60_EV_1ST_GEN,
    CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN,
  )
)
CARNIVAL_CARS = frozenset((CAR.KIA_CARNIVAL_2025, CAR.KIA_CARNIVAL_HEV_4TH_GEN))
STOCK_AOL_WORDS = frozenset(
  0x0800 | gas | topology | extra
  for gas in (0, 1, 2)
  for topology in (0, 8, 16, 144, 32, 40, 48, 176)
  for extra in (0, 0x0400, 0x2000, 0x2400)
  if (topology not in (48, 176) or extra & 0x2000)
  and (not extra & 0x0400 or topology in (8, 40))
  and (not extra & 0x2000 or (topology in (32, 40, 48, 176) and gas != 1))
)

STOCK_AOL_FLAGS = (
  _FLAG_CANFD
  | _FLAG_EV
  | _FLAG_HYBRID
  | _FLAG_CANFD_LKA_STEER_MSG
  | _FLAG_CANFD_LKA_STEER_MSG_ALT
  | _FLAG_CANFD_ALT_BUTTONS
  | _FLAG_CANFD_CAMERA_SCC
  | _FLAG_CANFD_ALT_GEARS
  | _FLAG_CANFD_ALT_GEARS_2
  | _FLAG_CANFD_RADAR_SCC
  | _FLAG_CANFD_NO_RADAR_DISABLE
  | _FLAG_CCNC
)

def _base_word(cp, *, experiences=(0,)):
  owner = config_index(cp)
  allowed = STOCK_AOL_FLAGS
  try:
    factory = CAR(cp.carFingerprint)
  except ValueError:
    return None
  if (
    cp.brand != 'hyundai'
    or not int(factory.config.flags) & _FLAG_CANFD
    or int(factory.config.flags) & (_FLAG_CANFD_ANGLE_STEERING | _FLAG_NON_SCC)
    or cp.passive
    or cp.dashcamOnly
    or cp.notCar
    or cp.alternativeExperience not in experiences
    or cp.steerControlType != CarParams.SteerControlType.torque
    or not int(cp.flags) & _FLAG_CANFD
    or int(cp.flags) & ~int(allowed)
    or owner is None
  ):
    return None
  # EV identity is static; hybrid fuel is selected by the actual 0xFA fingerprint.
  if bool(int(cp.flags) & _FLAG_EV) != bool(int(factory.config.flags) & _FLAG_EV):
    return None
  ev, hybrid = bool(int(cp.flags) & _FLAG_EV), bool(int(cp.flags) & _FLAG_HYBRID)
  lka = bool(int(cp.flags) & _FLAG_CANFD_LKA_STEER_MSG)
  alt_lka = bool(int(cp.flags) & _FLAG_CANFD_LKA_STEER_MSG_ALT)
  alt_buttons = bool(int(cp.flags) & _FLAG_CANFD_ALT_BUTTONS)
  camera = bool(int(cp.flags) & _FLAG_CANFD_CAMERA_SCC)
  if (ev and hybrid) or (alt_lka and not lka) or (lka and camera):
    return None
  bus = CanBus(cp)
  offset = 4 * owner
  if (bus.ACAN - offset, bus.ECAN - offset, bus.CAM - offset) != ((0, 1, 2) if lka else (1, 0, 2)):
    return None
  word = (1 if ev else 2 if hybrid else 0) | (16 if lka else 0) | (128 if alt_lka else 0)
  word |= (32 if alt_buttons else 0) | (8 if camera else 0)
  if int(cp.flags) & _FLAG_CCNC and camera:
    word |= 0x0400
  if factory in CARNIVAL_CARS and alt_buttons:
    word |= 0x2000
  if factory == CAR.HYUNDAI_IONIQ_6 and word != 0x09:
    return None
  return word if word | STOCK_AOL_MARKER in STOCK_AOL_WORDS else None


def qualified(cp, *, marked_only=False):
  word = _base_word(cp)
  if word is None or cp.openpilotLongitudinalControl or not cp.pcmCruise:
    return False
  return cp.safetyConfigs[config_index(cp)].safetyParam in ((word | STOCK_AOL_MARKER,) if marked_only else (word, word | STOCK_AOL_MARKER))


SPORTAGE_LONG_AOL_WORDS = frozenset(0x0804 | fuel | topology for fuel in (0, 2) for topology in (0, 8, 16, 144, 32, 40))
CARNIVAL_LONG_AOL_WORD = 0x28B4
LONG_AOL_WORDS = frozenset((CARNIVAL_LONG_AOL_WORD, 0x080D, 0x0815, 0x0895, 0x2815, 0x2895)) | SPORTAGE_LONG_AOL_WORDS
CCNC_LONG_AOL_WORDS = frozenset((0x0C0C, 0x0C0E, 0x0C2C, 0x0C2E))
LONG_EV_CARS = frozenset((CAR.HYUNDAI_IONIQ_5, CAR.HYUNDAI_KONA_EV_2ND_GEN))
LONG_AOL_WORDS |= CCNC_LONG_AOL_WORDS
GENERIC_HDA2_CARS = frozenset((
  CAR.GENESIS_G80_2ND_GEN_FL,
  CAR.HYUNDAI_IONIQ_5_N,
  CAR.HYUNDAI_KONA_2ND_GEN,
  CAR.HYUNDAI_KONA_HEV_2ND_GEN,
  CAR.HYUNDAI_SANTA_CRUZ_1ST_GEN,
  CAR.HYUNDAI_SANTA_CRUZ_2025,
  CAR.HYUNDAI_SONATA_2024,
  CAR.HYUNDAI_SONATA_HEV_2024,
  CAR.HYUNDAI_STARIA_4TH_GEN,
  CAR.HYUNDAI_TUCSON_2025,
  CAR.HYUNDAI_TUCSON_4TH_GEN,
  CAR.HYUNDAI_TUCSON_HEV_2025,
  CAR.HYUNDAI_TUCSON_PHEV_2025,
  CAR.KIA_CARNIVAL_2025,
  CAR.KIA_CARNIVAL_HEV_4TH_GEN,
  CAR.KIA_K4_2025,
  CAR.KIA_K5_2025,
  CAR.KIA_K8_HEV_1ST_GEN,
  CAR.KIA_NIRO_EV_2ND_GEN,
  CAR.KIA_NIRO_HEV_2ND_GEN,
  CAR.KIA_SORENTO_2024,
  CAR.KIA_SPORTAGE_5TH_GEN,
))

INDEPENDENT_LONG_BUTTON_CARS = LONG_EV_CARS | GENERIC_HDA2_CARS | {
  CAR.KIA_EV6, CAR.KIA_NIRO_EV_2ND_GEN, CAR.HYUNDAI_IONIQ_6, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN,
  CAR.GENESIS_GV60_EV_1ST_GEN, CAR.KIA_SPORTAGE_5TH_GEN, CAR.KIA_CARNIVAL_2025,
}


def qualified_long(cp, *, marked_only=False):
  if cp.carFingerprint == CAR.KIA_EV6 and int(cp.flags) & _FLAG_CANFD_LKA_STEER_MSG:
    from opendbc.car.hyundai.ev6_aol import qualified as ev6_qualified
    return ev6_qualified(cp, marked_only=marked_only)
  if cp.carFingerprint not in INDEPENDENT_LONG_BUTTON_CARS or not cp.openpilotLongitudinalControl or cp.pcmCruise:
    return False
  if cp.carFingerprint == CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN:
    from opendbc.car.hyundai.gv70_startup import eligible
    if not eligible(cp):
      return False
  word = _base_word(cp, experiences=(0, 32))
  if cp.carFingerprint in (CAR.KIA_EV6, CAR.GENESIS_GV60_EV_1ST_GEN, CAR.HYUNDAI_IONIQ_6):
    if word != 0x09:
      return False
  elif cp.carFingerprint == CAR.KIA_SPORTAGE_5TH_GEN:
    if word is None or word | 0x804 not in SPORTAGE_LONG_AOL_WORDS:
      return False
  elif cp.carFingerprint == CAR.KIA_CARNIVAL_2025:
    if word != 0x20B0:
      return False
  elif cp.carFingerprint == CAR.KIA_CARNIVAL_HEV_4TH_GEN:
    return False
  elif cp.carFingerprint in GENERIC_HDA2_CARS:
    if word is None or not cp.alphaLongitudinalAvailable:
      return False
    if word & 0x10:
      if len(cp.safetyConfigs) != 1 or word not in (0x10, 0x11, 0x12, 0x90, 0x91, 0x92):
        return False
    elif cp.carFingerprint == CAR.KIA_NIRO_EV_2ND_GEN:
      if word != 0x09:
        return False
    elif word not in (0x08, 0x0A, 0x28, 0x2A, 0x408, 0x40A, 0x428, 0x42A):
      return False
  elif word not in (0x11, 0x91) and not (cp.carFingerprint == CAR.HYUNDAI_IONIQ_5 and word == 0x09):
    return False
  raw = int(cp.safetyConfigs[config_index(cp)].safetyParam)
  if cp.carFingerprint == CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN:
    if not raw & 0x2000:
      return False
    raw &= ~0x2000
  return (raw == (word | 0x804) and cp.alternativeExperience == 32) or (
    not marked_only and raw == (word | 4) and cp.alternativeExperience == 0
  )
