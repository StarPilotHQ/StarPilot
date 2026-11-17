#pragma once

#include "selfdrive/pandad/aol_protocol.h"

inline bool subaru_aol_param(uint16_t param) {
  return (param == 16U) || (param == 0U) || (param == 1U) || (param == 17U) || (param == 209U) || (param == 465U) || (param == 145U) ||
         (param == 1169U) || (param == 1425U) || (param == 401U) || (param == 177U) || (param == 8U) || (param == 520U) || (param == 512U) || (param == 2225U) ||
         (param == 4096U) || (param == 4104U) || (param == 4608U) || (param == 4616U);
}

inline constexpr AolSafetyProfile SUBARU_AOL_PROFILE{11U, subaru_aol_param, true};
