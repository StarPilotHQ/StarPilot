#pragma once

#include "openpilot/selfdrive/pandad/aol_protocol.h"

// Only the long-control word. GwmFlags.LONG_CONTROL == 1, and the panda's
// gwm_init gates its AolSafetyPolicy on exactly param == 1 as well, so the two
// sides agree on which profile may run.
inline bool gwm_aol_param(uint16_t param) {
  return param == 1U;
}

// 40 is SAFETY_GWM. normal_runtime is true: engagement is owned by the physical
// stalk, so there is no separate host opt-in step to wait for.
inline constexpr AolSafetyProfile GWM_AOL_PROFILE{40U, gwm_aol_param, true};
