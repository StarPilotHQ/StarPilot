#pragma once
#include "selfdrive/pandad/aol_protocol.h"
inline bool volkswagen_stock_aol_param(uint16_t param) {
  return (param == 8U) || (param == 24U) || (param == 40U);
}
inline bool volkswagen_long_aol_param(uint16_t param) {
  return (param == 9U) || (param == 25U) || (param == 41U);
}
inline constexpr AolSafetyProfile VOLKSWAGEN_LONG_AOL_PROFILE{15U, volkswagen_long_aol_param, true};
inline constexpr AolSafetyProfile VOLKSWAGEN_STOCK_AOL_PROFILE{15U, volkswagen_stock_aol_param, true};

inline bool volkswagen_meb_aol_param(uint16_t param) {
  return (param == 8U) || (param == 10U) || (param == 12U) || (param == 14U) ||
         (param == 24U) || (param == 26U) || (param == 28U) || (param == 30U);
}
inline constexpr AolSafetyProfile VOLKSWAGEN_MEB_AOL_PROFILE{34U, volkswagen_meb_aol_param, true};

inline bool volkswagen_pq_aol_param(uint16_t param) {
  return (param == 8U) || (param == 9U);
}
inline constexpr AolSafetyProfile VOLKSWAGEN_PQ_AOL_PROFILE{21U, volkswagen_pq_aol_param, true};
