"""Exact Forte non-SCC AOL identity and physical-button observations."""
from opendbc.car.structs import CarParams
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.car.hyundai.non_scc_aol import SOFTWARE_CRUISE

FORTE_IDS = (CAR.KIA_FORTE_2019_NON_SCC, CAR.KIA_FORTE_2021_NON_SCC)
FORTE_AOL_MARKER = 0x0400
FORTE_AOL_EXPERIENCE = 32
FORTE_AOL_WORDS = frozenset((0x1400, 0x1c00))
SOURCE_MAX_AGE_NS = 300_000_000


def qualified(cp):
  physical = bool(cp.flags & HyundaiFlags.HAS_LDA_BUTTON)
  words = (0x1000, 0x1800, 0x1c00) if physical else (0x1000, 0x1400)
  allowed_flags = (HyundaiFlags.NON_SCC | HyundaiFlags.NON_SCC_NO_FCA | HyundaiFlags.NON_SCC_RADAR_FCA |
                   HyundaiFlags.HAS_LDA_BUTTON | HyundaiFlags.USE_FCA | HyundaiFlags.SEND_LFA)
  return (cp.carFingerprint in FORTE_IDS and cp.brand == 'hyundai' and cp.alternativeExperience in (0, FORTE_AOL_EXPERIENCE) and
          not cp.passive and not cp.dashcamOnly and
          not cp.notCar and not cp.openpilotLongitudinalControl and cp.pcmCruise and
          bool(cp.flags & HyundaiFlags.NON_SCC) and not int(cp.flags) & ~int(allowed_flags) and
          len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == CarParams.SafetyModel.hyundai and
          (cp.safetyConfigs[0].safetyParam & ~SOFTWARE_CRUISE) in words)


# Keep the public Forte fixture/provider API while sharing the same state machine.
from opendbc.car.hyundai.non_scc_aol import NonSccLkasSources

ForteLkasSources = NonSccLkasSources
