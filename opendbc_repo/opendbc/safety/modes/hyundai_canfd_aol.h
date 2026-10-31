#pragma once

// Bit 11 selects the shared axis lease only for these ordinary torque layouts.
// Angle model selectors and the separate Ioniq 6 actuator profile stay separate.
static inline bool hyundai_canfd_stock_torque_aol_param(uint16_t param) {
  const uint16_t base = param & (uint16_t)~0x0800U;
  const uint16_t gas = base & 3U;
  const uint16_t topology = base & 0x00B8U;
  const bool carnival = (base & 0x2000U) != 0U;
  const bool ccnc = (base & 0x0400U) != 0U;
  const bool standard = (topology == 0U) || (topology == 8U) || (topology == 16U) || (topology == 144U);
  const bool alternate = (topology == 32U) || (topology == 40U) ||
                         (carnival && ((topology == 48U) || (topology == 176U)));
  return ((param & 0x0800U) != 0U) && ((base & (uint16_t)~0x24BBU) == 0U) && (gas != 3U) &&
         (standard || alternate) && (!ccnc || ((topology == 8U) || (topology == 40U))) &&
         (!carnival || (alternate && (gas != 1U)));
}

#if defined(ALLOW_DEBUG) || defined(__cplusplus)
static inline bool hyundai_canfd_torque_long_aol_param(uint16_t param) {
  const uint16_t base = param & (uint16_t)~0x0800U;
  const uint16_t fuel = base & 3U;
  const uint16_t topology = base & 0x00B8U;
  const bool sportage = ((param & 0x0800U) != 0U) && ((base & 0xFF41U) == 0U) &&
                        ((base & 4U) != 0U) && ((fuel == 0U) || (fuel == 2U)) &&
                        ((topology == 0U) || (topology == 8U) || (topology == 16U) ||
                         (topology == 144U) || (topology == 32U) || (topology == 40U));
  return sportage || (param == 0x080DU) || (param == 0x0815U) || (param == 0x0895U) || (param == 0x2815U) || (param == 0x2895U);
}
#endif
