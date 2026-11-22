#pragma once
#include "openpilot/selfdrive/pandad/aol_protocol.h"
inline bool volvo_aol_param(uint16_t param) {
  return param == 8U;
}
inline constexpr AolSafetyProfile VOLVO_AOL_PROFILE{36U, volvo_aol_param, true};
