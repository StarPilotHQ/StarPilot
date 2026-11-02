#pragma once

#include "selfdrive/pandad/aol_protocol.h"

inline bool tesla_preap_aol_param(uint16_t param) {
  return (param == 0U) || (param == 1U) || (param == 2U) || (param == 3U) || (param == 6U) || (param == 7U);
}

inline constexpr AolSafetyProfile TESLA_PREAP_AOL_PROFILE{39U, tesla_preap_aol_param, true};

inline bool tesla_screen_aol_param(uint16_t param) {
  param &= ~256U;
  return param == 512U || param == 513U || param == 1536U || param == 1537U;
}

inline constexpr AolSafetyProfile TESLA_SCREEN_AOL_PROFILE{10U, tesla_screen_aol_param, true};
