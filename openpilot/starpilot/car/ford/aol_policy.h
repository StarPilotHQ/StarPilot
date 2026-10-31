#pragma once

#include "openpilot/selfdrive/pandad/aol_protocol.h"
#include "opendbc/safety/modes/ford_param.h"

static inline bool ford_aol_param(uint16_t param) {
  bool result = false;
  switch (ford_profile_word(param) & ~256U) {
    case 0U: case 1U: case 2U: case 3U:
    case 8U: case 9U: case 10U: case 11U: case 12U: case 13U:
      result = true;
      break;
    default:
      break;
  }
  return result;
}
inline constexpr AolSafetyProfile FORD_AOL_PROFILE{6U, ford_aol_param, true};
