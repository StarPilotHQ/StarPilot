#pragma once

#include "selfdrive/pandad/aol_protocol.h"

inline bool honda_aol_param(uint16_t param) {
  return (param == 34U) || (param == 35U) || (param == 163U);
}

inline constexpr AolSafetyProfile HONDA_AOL_PROFILE{20U, honda_aol_param, false};


inline bool honda_stock_aol_param(uint16_t param) {
  return (param == 0U) || (param == 1U) || (param == 8U) || (param == 9U) || (param == 10U) || (param == 11U) || (param == 16U) || (param == 17U) || (param == 80U) || (param == 81U) || (param == 512U) || (param == 513U) ||
         (param == 1058U) || (param == 1059U) || (param == 1570U) || (param == 1571U) || (param == 2082U) || (param == 2083U) || (param == 2211U) ||
         (param == 4096U) || (param == 4097U) || (param == 6178U) || (param == 6179U);
}

inline bool honda_nidec_aol_param(uint16_t param) {
  return (param == 0U) || (param == 4U) || (param == 260U) || (param == 544U) || (param == 548U) || (param == 804U);
}

inline constexpr AolSafetyProfile HONDA_STOCK_AOL_PROFILE{20U, honda_stock_aol_param, true};
inline constexpr AolSafetyProfile HONDA_NIDEC_AOL_PROFILE{1U, honda_nidec_aol_param, true};
