#pragma once

#include "selfdrive/pandad/aol_protocol.h"
#include "opendbc/safety/modes/hyundai_canfd_aol.h"
#include "opendbc/safety/modes/hyundai_canfd_angle_aol.h"

inline bool hyundai_aol_param(uint16_t param) {
  // Mode28 transports exact stock-angle requests and the paired EV9 LONG profile.
  return hyundai_canfd_angle_aol_param(param) || hyundai_canfd_stock_torque_aol_param(param) || hyundai_canfd_torque_long_aol_param(param) || param == 0x8815U || param == 0x8895U || param == 0x5491U || param == 0x5C91U || param == 0x5C95U || param == 0x5495U;
}

inline constexpr AolSafetyProfile HYUNDAI_AOL_PROFILE{28U, hyundai_aol_param, true};

inline bool hyundai_classic_scc_aol_param(uint16_t param) {
  return param == 0x0500U || param == 0x0D00U || param == 0x8408U || param == 0x840AU || param == 0x8C08U || param == 0x8C0AU ||
         (((param & 0x0400U) != 0U) && ((param & 0xF1B4U) == 0U) &&
          ((param & 3U) != 3U) && ((param & 0x0240U) != 0x0240U));
}

inline bool hyundai_legacy_aol_param(uint16_t param) {
  return param == 0xE902U || param == 0xE903U || param == 0xE912U || param == 0xE913U ||
    (hyundai_classic_scc_aol_param(param) && ((param & (8U | 256U)) == 0U));
}

inline constexpr AolSafetyProfile HYUNDAI_LEGACY_AOL_PROFILE{23U, hyundai_legacy_aol_param, true};

inline bool hyundai_classic_long_aol_param(uint16_t param) {
  return ((param & 0x0404U) == 0x0404U) && ((param & 0xF3B8U) == 0U) && ((param & 3U) != 3U);
}

inline bool hyundai_classic_aol_param(uint16_t param) {
  if ((param & 0x1020U) == 0x1020U) {
    switch (param & ~0x0020U) {
      case 0x1400U: case 0x1C00U: case 0x1440U: case 0x1C40U:
      case 0x1402U: case 0x1C02U: case 0x1441U: case 0x1C41U: case 0x1C51U:
        return true;
      default:
        return false;
    }
  }
  return param == 0x2000U || param == 0x2004U || param == 0x2010U || param == 0x2014U || hyundai_classic_long_aol_param(param) || hyundai_classic_scc_aol_param(param) || param == 0x1400U || param == 0x1C00U || param == 0x1440U || param == 0x1C40U ||
         param == 0x1402U || param == 0x1C02U || param == 0x1441U || param == 0x1C41U || param == 0x1C51U;
}

inline constexpr AolSafetyProfile HYUNDAI_CLASSIC_AOL_PROFILE{8U, hyundai_classic_aol_param, true};
