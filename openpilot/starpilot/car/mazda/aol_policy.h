#pragma once

#include "selfdrive/pandad/aol_protocol.h"

// Native profile for GEN2 Mazda (safety model 13, safetyParam 2 = FLAG_GEN2).
// Parameter 0 remains the stock GEN1 profile; parameter 2 is the GEN2 profile
// implemented in opendbc/safety/modes/mazda.h, which grants lateral only.
inline bool mazda_aol_param(uint16_t param) {
  return (param == 0U) || (param == 2U);
}

inline constexpr AolSafetyProfile MAZDA_AOL_PROFILE{13U, mazda_aol_param, true};
