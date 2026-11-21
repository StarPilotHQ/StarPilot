"""Exact source-owned classic NON_SCC lateral profiles.

Gas and steering limits remain those of each declared platform. Ray's pedal
and refresh-message topology is deliberately handled by its separate owner.
"""
import time

from opendbc.car import structs
from opendbc.car.hyundai.values import CAR, HyundaiFlags, HyundaiSafetyFlags

NON_SCC_IDS = frozenset((CAR.HYUNDAI_BAYON_1ST_GEN_NON_SCC, CAR.HYUNDAI_ELANTRA_2022_NON_SCC,
  CAR.HYUNDAI_ELANTRA_HEV_2022_NON_SCC, CAR.HYUNDAI_KONA_NON_SCC, CAR.HYUNDAI_KONA_EV_NON_SCC,
  CAR.KIA_CEED_PHEV_2022_NON_SCC, CAR.KIA_FORTE_2019_NON_SCC, CAR.KIA_FORTE_2021_NON_SCC,
  CAR.KIA_SELTOS_2023_NON_SCC, CAR.GENESIS_G70_2021_NON_SCC))
SOFTWARE_CRUISE = 0x0020
AOL_MARKER = 0x0400
AOL_EXPERIENCE = 32
AOL_WORDS = frozenset((0x1400, 0x1c00, 0x1440, 0x1c40, 0x1441, 0x1c41, 0x1402, 0x1c02))


def stock_word(cp):
  word = int(HyundaiSafetyFlags.NON_SCC)
  for flag, safety in ((HyundaiFlags.EV, HyundaiSafetyFlags.EV_GAS),
                       (HyundaiFlags.HYBRID, HyundaiSafetyFlags.HYBRID_GAS),
                       (HyundaiFlags.ALT_LIMITS, HyundaiSafetyFlags.ALT_LIMITS)):
    if cp.flags & flag:
      word |= int(safety)
  if cp.flags & HyundaiFlags.HAS_LDA_BUTTON:
    word |= int(HyundaiSafetyFlags.HAS_LDA_BUTTON)
  return word


def qualified(cp):
  if cp.carFingerprint not in NON_SCC_IDS or cp.brand != 'hyundai':
    return False
  declared = int(CAR[cp.carFingerprint].config.flags)
  identity_mask = int(HyundaiFlags.NON_SCC | HyundaiFlags.EV | HyundaiFlags.HYBRID | HyundaiFlags.ALT_LIMITS |
                      HyundaiFlags.ALT_LIMITS_2 | HyundaiFlags.CANFD)
  allowed = declared | int(HyundaiFlags.HAS_LDA_BUTTON | HyundaiFlags.USE_FCA | HyundaiFlags.SEND_LFA |
                           HyundaiFlags.NON_SCC_RADAR_FCA | HyundaiFlags.NON_SCC_NO_FCA)
  if (int(cp.flags) & identity_mask != declared & identity_mask or int(cp.flags) & ~allowed or
      cp.passive or cp.dashcamOnly or cp.notCar or cp.openpilotLongitudinalControl or not cp.pcmCruise or
      cp.alternativeExperience not in (0, AOL_EXPERIENCE) or len(cp.safetyConfigs) != 1):
    return False
  safety = cp.safetyConfigs[0]
  word = stock_word(cp)
  # The observed alternative CLU source is marked only at the AOL boundary;
  # ordinary safety may still use its original MAIN-only profile.
  ordinary = word & ~int(HyundaiSafetyFlags.HAS_LDA_BUTTON)
  return (safety.safetyModel == structs.CarParams.SafetyModel.hyundai and
          (safety.safetyParam & ~SOFTWARE_CRUISE) in (word, ordinary, word | AOL_MARKER))


def aol_word(cp):
  return stock_word(cp) | AOL_MARKER | (cp.safetyConfigs[0].safetyParam & SOFTWARE_CRUISE)


SOURCE_MAX_AGE_NS = 300_000_000


class NonSccLkasSources:
  """Fuse observed alternative sources without replaying held startup input."""
  def __init__(self):
    self.sources = {}
    self.neutral_seen = False
    self.held = False
    self.edges = []

  def update(self, can_packets):
    self.edges = []
    try:
      now = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
      self.sources.clear()
      self.neutral_seen = False
      self.held = False
      return
    current = {address: value for address, value in self.sources.items() if 0 <= now - value[0] <= SOURCE_MAX_AGE_NS}
    if len(current) != len(self.sources):
      # Expiry is loss of evidence, never a manufactured release or press.
      self.neutral_seen = False
      self.held = any(value[1] for value in current.values())
    self.sources = current
    for stamp, packets in can_packets:
      if type(stamp) is not int or not 0 < stamp <= now or now - stamp > SOURCE_MAX_AGE_NS:
        continue
      for address, data, bus in packets:
        if bus != 0 or address not in (0x391, 0x50c) or len(data) != 8:
          continue
        previous = self.sources.get(address)
        if previous is not None and stamp <= previous[0]:
          continue
        pressed = bool(data[0] & 0x10) if address == 0x391 else bool(data[7] & 1)
        source_neutral = (previous[2] if previous is not None else False) or not pressed
        self.sources[address] = (stamp, pressed, source_neutral)
        held = any(value[1] for value in self.sources.values())
        if not held and not self.neutral_seen:
          self.neutral_seen = True
          if not self.held:
            self.edges.append(False)
        if held != self.held and self.neutral_seen and (not held or source_neutral):
          self.edges.append(held)
        self.held = held
