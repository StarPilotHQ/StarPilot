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

// One confirmation between normal health ticks for the first manual MQB OP-long request.
class VolkswagenManualAolStartupPoll {
public:
  bool due(uint64_t frame) {
    if (frame % 10U != 5U || !scheduled_) return false;
    scheduled_ = false;
    return true;
  }

  void observe(bool onroad, bool ordinary_engaged, bool healthy, const AolWritePlan &plan, const AolOutcome &outcome) {
    if (!onroad) {
      observed_first_request_ = scheduled_ = false;
    } else if (!observed_first_request_ && outcome.compatible && !outcome.session.empty() &&
               outcome.status && outcome.status->safety_mode == VOLKSWAGEN_LONG_AOL_PROFILE.mode &&
               outcome.status->safety_param == 25U && plan.request_mask != 0U) {
      observed_first_request_ = true;
      scheduled_ = healthy && !ordinary_engaged && plan.request_mask == 1U && plan.retain_lateral_arm &&
                   outcome.status->request_mask == 1U && outcome.status->permission_mask == 0U;
    }
  }

private:
  bool observed_first_request_ = false;
  bool scheduled_ = false;
};
