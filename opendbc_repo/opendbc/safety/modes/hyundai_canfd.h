#pragma once

#include "opendbc/safety/declarations.h"
#include "opendbc/safety/modes/hyundai_common.h"
#include "opendbc/safety/modes/hyundai_daw.h"
#include "opendbc/safety/modes/hyundai_canfd_aol.h"
#include "opendbc/safety/modes/hyundai_canfd_angle_aol.h"

#define HYUNDAI_CANFD_CRUISE_BUTTON_TX_MSGS(bus) \
  {0x1CF, bus, 8, .check_relay = false},  /* CRUISE_BUTTON */   \

#define HYUNDAI_CANFD_LKA_STEER_MSG_COMMON_TX_MSGS(a_can, e_can) \
  HYUNDAI_CANFD_CRUISE_BUTTON_TX_MSGS(e_can)                        \
  {0x50,  a_can, 16, .check_relay = (a_can) == 0},  /* LKAS */      \
  {0x2A4, a_can, 24, .check_relay = (a_can) == 0},  /* CAM_0x2A4 */ \

#define HYUNDAI_CANFD_LKA_STEER_MSG_ALT_COMMON_TX_MSGS(a_can, e_can) \
  HYUNDAI_CANFD_CRUISE_BUTTON_TX_MSGS(e_can)                        \
  {0x110, a_can, 32, .check_relay = (a_can) == 0},  /* LKAS_ALT */  \
  {0x362, a_can, 32, .check_relay = (a_can) == 0},  /* CAM_0x362 */ \

#define HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(e_can)  \
  {0x12A, e_can, 16, .check_relay = (e_can) == 0},  /* LFA */            \
  {0x1E0, e_can, 16, .check_relay = (e_can) == 0},  /* LFAHDA_CLUSTER */ \

#define HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(e_can, longitudinal) \
  {0x1A0, e_can, 32, .check_relay = (longitudinal)},  /* SCC_CONTROL */ \

// *** Addresses checked in rx hook ***
// EV, ICE, HYBRID: ACCELERATOR (0x35), ACCELERATOR_BRAKE_ALT (0x100), ACCELERATOR_ALT (0x105)
#define HYUNDAI_CANFD_COMMON_RX_CHECKS(pt_bus)                                                                          \
  {.msg = {{0x35, (pt_bus), 32, 100U, .max_counter = 0xffU, .ignore_quality_flag = true},                  \
           {0x100, (pt_bus), 32, 100U, .max_counter = 0xffU, .ignore_quality_flag = true},                 \
           {0x105, (pt_bus), 32, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}}},               \
  {.msg = {{0x175, (pt_bus), 24, 50U, .max_counter = 0xffU, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{0xa0, (pt_bus), 24, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{0xea, (pt_bus), 24, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \

// Only the three banked angle hybrids bind their observed fuel source exactly.
// Older profiles retain their established alternative-fuel RX definition.
#define HYUNDAI_CANFD_BANKED_ANGLE_RX_CHECKS(pt_bus, fuel_addr)                                                            \
  {.msg = {{(fuel_addr), (pt_bus), 32, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}, { 0 }, { 0 }}},             \
  {.msg = {{0x175, (pt_bus), 24, 50U, .max_counter = 0xffU, .ignore_quality_flag = true}, { 0 }, { 0 }}},                  \
  {.msg = {{0xa0, (pt_bus), 24, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}, { 0 }, { 0 }}},                  \
  {.msg = {{0xea, (pt_bus), 24, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}, { 0 }, { 0 }}},                  \

#define HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(pt_bus, fuel_addr)                                                        \
  HYUNDAI_CANFD_BANKED_ANGLE_RX_CHECKS(pt_bus, fuel_addr)                                                                  \
  {.msg = {{0x1cf, (pt_bus), 8, 50U, .ignore_checksum = true, .max_counter = 0xfU, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \

#define HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(pt_bus, fuel_addr)                                                        \
  HYUNDAI_CANFD_BANKED_ANGLE_RX_CHECKS(pt_bus, fuel_addr)                                                                  \
  {.msg = {{0x1aa, (pt_bus), 16, 50U, .ignore_checksum = true, .max_counter = 0xffU, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \

#define HYUNDAI_CANFD_STD_BUTTONS_RX_CHECKS(pt_bus)                                                                                            \
  HYUNDAI_CANFD_COMMON_RX_CHECKS(pt_bus)                                                                                                       \
  {.msg = {{0x1cf, (pt_bus), 8, 50U, .ignore_checksum = true, .max_counter = 0xfU, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \

#define HYUNDAI_CANFD_ALT_BUTTONS_RX_CHECKS(pt_bus)                                                                                              \
  HYUNDAI_CANFD_COMMON_RX_CHECKS(pt_bus)                                                                                                         \
  {.msg = {{0x1aa, (pt_bus), 16, 50U, .ignore_checksum = true, .max_counter = 0xffU, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \

// SCC_CONTROL (from ADAS unit or camera)
#define HYUNDAI_CANFD_SCC_ADDR_CHECK(scc_bus)                                                                            \
  {.msg = {{0x1a0, (scc_bus), 32, 50U, .max_counter = 0xffU, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \

// Exact stock-SCC Ioniq 5 PE banked angle profile.
#define HYUNDAI_IONIQ5_PE_STOCK_PARAM 0x5491U
#define HYUNDAI_EV9_STOCK_PARAM 0x5C91U
#define HYUNDAI_EV9_LONG_PARAM 0x5C95U
#define HYUNDAI_IONIQ5_PE_LONG_PARAM 0x5495U
static bool hyundai_canfd_ccnc_ev_long = false;
static bool hyundai_canfd_pe_long = false;
static uint8_t hyundai_ev9_inactive_accel_count = 0U;
static bool hyundai_angle_aol_enabled = false;
static bool hyundai_ev9_long_aol = false;
static uint8_t hyundai_ev9_long_aol_permission_mask(void);
static bool hyundai_angle_aol_drive = false;
static bool hyundai_angle_aol_eps_fault = false;
static bool hyundai_angle_aol_gear_seen = false;
static uint32_t hyundai_angle_aol_gear_ts = 0U;
static uint8_t hyundai_angle_aol_fault_mask = 2U;
static uint8_t hyundai_angle_aol_permission_mask(void);
static bool hyundai_ordinary_angle_stock = false;
static bool hyundai_ordinary_angle_drive = false;
static bool hyundai_ordinary_angle_eps_fault = false;
static bool hyundai_ordinary_angle_owned = false;
static bool hyundai_ordinary_angle_counter_seen = false;
static uint8_t hyundai_ordinary_angle_counter = 0U;
static uint32_t hyundai_ordinary_angle_accepted_ts = 0U;

static void hyundai_ordinary_angle_release(void) {
  hyundai_ordinary_angle_owned = false;
  hyundai_ordinary_angle_counter_seen = false;
  if (hyundai_ordinary_angle_stock || hyundai_angle_aol_enabled) {
    // Match the shared limiter's inactive/violation baseline. Its rate and
    // acceleration envelopes and real-time message budget remain unchanged.
    desired_angle_last = SAFETY_CLAMP(angle_meas.values[0], -3600, 3600);
  }
}

static void hyundai_ordinary_angle_reset(void) {
  hyundai_angle_aol_enabled = false;
  hyundai_ev9_long_aol = false;
  hyundai_angle_aol_drive = false;
  hyundai_angle_aol_eps_fault = false;
  hyundai_angle_aol_gear_seen = false;
  hyundai_angle_aol_gear_ts = 0U;
  hyundai_ordinary_angle_release();
  hyundai_ordinary_angle_stock = false;
  hyundai_canfd_ccnc_ev_long = false;
  hyundai_canfd_pe_long = false;
  hyundai_ev9_inactive_accel_count = 0U;
  hyundai_ordinary_angle_drive = false;
  hyundai_ordinary_angle_eps_fault = false;
  hyundai_ordinary_angle_counter_seen = false;
  hyundai_ordinary_angle_counter = 0U;
  hyundai_ordinary_angle_accepted_ts = 0U;
}

static void hyundai_ordinary_angle_host_request(uint8_t mask) {
  if ((mask & 1U) == 0U) {
    hyundai_ordinary_angle_release();
  }
}

static uint8_t hyundai_ordinary_angle_request_mask(void) {
  uint8_t result = 0U;
  if (hyundai_ordinary_angle_stock && heartbeat_engaged && !relay_malfunction && !safety_rx_checks_invalid &&
      (safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) <= AOL_HOST_REQUEST_TIMEOUT_US)) {
    result = aol_host_axis_mask & 1U;
  } else {
    hyundai_ordinary_angle_release();
  }
  return result;
}

static uint8_t hyundai_ordinary_angle_permission_mask(void) {
  uint8_t result = 0U;
  if (hyundai_angle_aol_enabled) {
    result = hyundai_ev9_long_aol ? (hyundai_ev9_long_aol_permission_mask() & 1U) : hyundai_angle_aol_permission_mask();
    if (result == 0U) {
      hyundai_ordinary_angle_release();
    }
  } else if (aol_rx_healthy() && controls_allowed && vehicle_moving && hyundai_ordinary_angle_drive && !hyundai_ordinary_angle_eps_fault &&
             !brake_pressed && !gas_pressed && ((hyundai_ordinary_angle_request_mask() & 1U) != 0U)) {
    result = 1U;
  } else {
    hyundai_ordinary_angle_release();
  }
  return result;
}

static bool hyundai_ordinary_angle_owner_current(void) {
  if ((hyundai_ordinary_angle_permission_mask() == 0U) || !hyundai_ordinary_angle_owned ||
      (safety_get_ts_elapsed(microsecond_timer_get(), hyundai_ordinary_angle_accepted_ts) > AOL_HOST_REQUEST_TIMEOUT_US)) {
    hyundai_ordinary_angle_release();
  }
  return hyundai_ordinary_angle_owned;
}

static bool hyundai_canfd_fwd_hook(int bus, int addr) {
  bool blocked = false;
  if ((bus == 2) && ((hyundai_ordinary_angle_stock && ((addr == 0x110) || (addr == 0x362))) ||
      (!hyundai_ev9_long_aol && hyundai_angle_aol_enabled && ((addr == 0x50) || (addr == 0x110) || (addr == 0x12A) || (addr == 0xCB) || (addr == 0x2A4) || (addr == 0x362))))) {
    blocked = hyundai_ordinary_angle_owner_current();
  }
  if (hyundai_canfd_ccnc_ev_long) {
    if ((bus == 2) && ((addr == 0x110) || (addr == 0x362))) {
      blocked = hyundai_ev9_long_aol ? (((hyundai_ev9_long_aol_permission_mask() & 1U) != 0U) && hyundai_ordinary_angle_owner_current()) :
                (controls_allowed && aol_rx_healthy() && vehicle_moving && hyundai_ordinary_angle_drive &&
                 !hyundai_ordinary_angle_eps_fault && !brake_pressed && !gas_pressed);
    }
    // Original EV9 LONG replaces ADAS radar-track forwarding onto ECAN.
    blocked |= (bus == 0) && (addr >= 0x3A5) && (addr <= 0x3C4);
  }
  return blocked;
}

static bool hyundai_canfd_alt_buttons = false;
static bool hyundai_canfd_lka_steer_msg_alt = false;
static bool hyundai_canfd_carnival_alt_resume = false;
static bool hyundai_canfd_carnival_source_valid = false;
static bool hyundai_canfd_carnival_previous_valid = false;
static bool hyundai_canfd_carnival_counter_seen = false;
static bool hyundai_canfd_carnival_counter_consumed = false;
static bool hyundai_canfd_angle_steering = false;
static bool hyundai_canfd_angle_observed_adas = false;
static bool hyundai_canfd_ioniq6_long = false;
static bool aol_ioniq6_long = false;
static bool hyundai_stock_torque_aol = false;
static bool aol_optional_release_policy = false;
static bool aol_optional_release_neutral = false;
static uint8_t aol_optional_release_previous = 0U;
static bool aol_optional_release_pending = false;
static bool aol_optional_release_latch = false;
static uint32_t aol_optional_release_ts = 0U;
static bool aol_optional_release_counter_seen = false;
static bool hyundai_torque_aol_cancel_preserves_lateral = false;
static bool hyundai_canfd_torque_ev_long = false;
static bool hyundai_canfd_lkas_damping_100 = false;
static bool aol_ioniq6_lateral_latch = false;
static bool aol_ioniq6_buttons_seen = false;
static bool aol_ioniq6_button_prev = false;
static bool aol_ioniq6_session_started = false;
static bool aol_ioniq6_request_seen = false;
static bool aol_ioniq6_lateral_token_claimed = false;
static uint32_t aol_ioniq6_gesture_ts = 0U;
static bool aol_ioniq6_enable_counter_seen = false;
static bool aol_ioniq6_enable_pending = false;
static int aol_stock_enable_previous = HYUNDAI_BTN_NONE;
static bool aol_ioniq6_enable_neutral = false;
static bool aol_ioniq6_enable_release = false;
static uint32_t aol_ioniq6_enable_release_ts = 0U;
static bool hyundai_canfd_ioniq6_lfa_unpaired = false;
static uint32_t hyundai_canfd_ioniq6_lfa_ts = 0U;
#ifdef ALLOW_DEBUG
static bool hyundai_canfd_gv60_long_param(uint16_t param) {
  return ((param == 0x9015U) || (param == 0x9095U)) && ((unsigned int)alternative_experience == 0U);
}
#endif

static bool hyundai_canfd_gv60_long = false;
static bool hyundai_canfd_gv60_heartbeat_seen = false;
static uint8_t hyundai_canfd_gv60_heartbeat_counter = 0U;
static bool hyundai_canfd_ioniq6_heartbeat_seen = false;
static uint8_t hyundai_canfd_ioniq6_heartbeat_counter = 0U;
static bool hyundai_canfd_ioniq6_bsm_low_speed = false;
static bool hyundai_canfd_ioniq6_bsm_wheel_seen = false;
static uint8_t hyundai_canfd_ioniq6_bsm_wheel_counter = 0U;
static uint32_t hyundai_canfd_ioniq6_bsm_wheel_ts = 0U;
static bool hyundai_canfd_ioniq6_corner_valid = false;
static bool hyundai_canfd_ioniq6_corner_counter_seen = false;
static uint8_t hyundai_canfd_ioniq6_corner_counter = 0U;
static uint8_t hyundai_canfd_ioniq6_corner_bits = 0U;
static uint32_t hyundai_canfd_ioniq6_corner_ts = 0U;
static bool hyundai_canfd_ioniq6_lamp_valid = false;
static bool hyundai_canfd_ioniq6_left_lamp = false;
static bool hyundai_canfd_ioniq6_right_lamp = false;
static uint32_t hyundai_canfd_ioniq6_lamp_ts = 0U;
static uint32_t hyundai_canfd_ioniq6_left_lamp_off_ts = 0U;
static uint32_t hyundai_canfd_ioniq6_right_lamp_off_ts = 0U;
static bool hyundai_canfd_ioniq6_bsm_counter_seen = false;
static uint8_t hyundai_canfd_ioniq6_bsm_counter = 0U;
static bool hyundai_canfd_ioniq6_bsm_pair_pending = false;
static uint8_t hyundai_canfd_ioniq6_bsm_pair_counter = 0U;
static uint32_t hyundai_canfd_ioniq6_bsm_pair_ts = 0U;
static uint8_t hyundai_canfd_angle_model = 0U;
static uint8_t hyundai_canfd_carnival_consumed_counter = 0U;
static uint8_t hyundai_canfd_carnival_source[16] = {0};
static uint8_t hyundai_canfd_carnival_previous[16] = {0};

// Standard CRUISE_BUTTONS sources use either 0..14 or 0..15. Only the
// observed terminal rollover may omit 15; repeats and other gaps stay invalid.
static bool hyundai_canfd_button_counter_follows(uint8_t previous, uint8_t current, uint8_t maximum) {
  return (current == (uint8_t)((previous + 1U) & maximum)) ||
         ((maximum == 0xFU) && (previous == 0xEU) && (current == 0U));
}

static void aol_ioniq6_enable_reset(void) {
  aol_ioniq6_enable_counter_seen = false;
  aol_stock_enable_previous = HYUNDAI_BTN_NONE;
  aol_ioniq6_enable_neutral = false;
  aol_ioniq6_enable_pending = false;
  aol_ioniq6_enable_release = false;
}

static void aol_ioniq6_reset(void) {
  aol_ioniq6_long = false;
  hyundai_stock_torque_aol = false;
  aol_ioniq6_enable_reset();
  aol_optional_release_capable = false;
  aol_optional_release_policy = false;
  aol_optional_release_neutral = false;
  aol_optional_release_counter_seen = false;
  aol_optional_release_previous = 0U;
  aol_optional_release_pending = false;
  aol_optional_release_latch = false;

  hyundai_torque_aol_cancel_preserves_lateral = false;
  hyundai_canfd_torque_ev_long = false;
  hyundai_canfd_lkas_damping_100 = false;
  aol_ioniq6_lateral_latch = false;
  aol_ioniq6_buttons_seen = false;
  aol_ioniq6_button_prev = false;
  aol_ioniq6_session_started = false;
  aol_ioniq6_request_seen = false;
  aol_ioniq6_lateral_token_claimed = false;
  aol_ioniq6_gesture_ts = 0U;
}

static void aol_ioniq6_host_request(uint8_t axis_mask) {
  const bool policy = aol_optional_release_capable && ((axis_mask & 0x4U) != 0U);
  if (!policy || !aol_optional_release_policy) {
    aol_optional_release_neutral = false;
    aol_optional_release_counter_seen = false;
    aol_optional_release_previous = 0U;
    aol_optional_release_pending = false;
    aol_optional_release_latch = false;
  }
  aol_optional_release_policy = policy;
  if ((axis_mask & 0x1U) == 0U) {
    aol_optional_release_latch = false;
    aol_optional_release_pending = false;
  }
  if (policy && aol_optional_release_pending && ((axis_mask & 0x1U) != 0U) &&
      heartbeat_engaged && aol_rx_healthy() && !relay_malfunction &&
      (safety_get_ts_elapsed(microsecond_timer_get(), aol_optional_release_ts) <= AOL_HOST_REQUEST_TIMEOUT_US)) {
    aol_optional_release_latch = true;
    aol_optional_release_pending = false;
  }

  if (axis_mask != 0U) {
    aol_ioniq6_request_seen = true;
  }
}

static uint8_t aol_ioniq6_request_mask(void) {
  uint8_t request = 0U;
  const uint32_t now = microsecond_timer_get();
  // A fresh physical gesture may precede the first host request and heartbeat.
  const bool pre_session_expired = aol_ioniq6_lateral_latch && !aol_ioniq6_lateral_token_claimed &&
    (safety_get_ts_elapsed(now, aol_ioniq6_gesture_ts) > AOL_HOST_REQUEST_TIMEOUT_US);
  const bool request_expired = aol_ioniq6_request_seen &&
    (safety_get_ts_elapsed(now, aol_host_request_ts) > AOL_HOST_REQUEST_TIMEOUT_US);
  if (relay_malfunction || safety_rx_checks_invalid ||
      (aol_ioniq6_session_started && !heartbeat_engaged) || pre_session_expired || request_expired) {
    aol_ioniq6_lateral_latch = false;
    aol_ioniq6_enable_reset();
    aol_optional_release_policy = false;
    aol_optional_release_neutral = false;
    aol_optional_release_counter_seen = false;
    aol_optional_release_previous = 0U;
    aol_optional_release_pending = false;
    aol_optional_release_latch = false;
    aol_ioniq6_buttons_seen = false;
    aol_ioniq6_button_prev = false;
    aol_ioniq6_session_started = false;
    aol_ioniq6_request_seen = false;
    aol_ioniq6_lateral_token_claimed = false;
    aol_host_axis_mask = 0U;
  } else if (heartbeat_engaged) {
    aol_ioniq6_session_started = true;
    if ((hyundai_stock_torque_aol || hyundai_canfd_ioniq6_long || hyundai_ev9_long_aol ||
         (aol_optional_release_capable && !aol_optional_release_policy)) && controls_allowed && aol_ioniq6_enable_release &&
        ((aol_host_axis_mask & 0x3U) == (hyundai_stock_torque_aol ? 0x1U : 0x3U)) &&
        (safety_get_ts_elapsed(now, aol_ioniq6_enable_release_ts) <= AOL_HOST_REQUEST_TIMEOUT_US)) {
      aol_ioniq6_lateral_latch = true;
      aol_ioniq6_enable_release = false;
    }
    if (((aol_host_axis_mask & 0x1U) != 0U) && aol_ioniq6_lateral_latch) {
      aol_ioniq6_lateral_token_claimed = true;
    }
    request = aol_host_axis_mask;
  } else {
    // A pending physical gesture does not grant a host request before heartbeat.
  }
  return request;
}

static uint8_t aol_ioniq6_permission_mask(void) {
  uint8_t permission = 0U;
  if (!aol_rx_healthy()) {
    aol_ioniq6_lateral_latch = false;
    aol_ioniq6_enable_reset();
    aol_optional_release_policy = false;
    aol_optional_release_neutral = false;
    aol_optional_release_counter_seen = false;
    aol_optional_release_previous = 0U;
    aol_optional_release_pending = false;
    aol_optional_release_latch = false;
    aol_ioniq6_buttons_seen = false;
    aol_ioniq6_button_prev = false;
    aol_ioniq6_session_started = false;
    aol_ioniq6_request_seen = false;
    aol_ioniq6_lateral_token_claimed = false;
    aol_host_axis_mask = 0U;
  } else {
    const uint8_t request = aol_get_request_mask();
    if (((request & 0x1U) != 0U) && (aol_ioniq6_lateral_latch || aol_optional_release_latch || (!hyundai_angle_aol_enabled && controls_allowed))) {
      permission |= 0x1U;
    }
    if (hyundai_longitudinal && ((request & 0x2U) != 0U) && controls_allowed) {
      permission |= 0x2U;
    }
  }
  return permission;
}

static uint8_t hyundai_angle_aol_permission_mask(void) {
  const uint8_t request = aol_ioniq6_permission_mask();
  const bool gear_current = hyundai_angle_aol_gear_seen &&
    (safety_get_ts_elapsed(microsecond_timer_get(), hyundai_angle_aol_gear_ts) <= 100000U);
  return (((request & 1U) != 0U) && gear_current && hyundai_angle_aol_drive && vehicle_moving &&
         !hyundai_angle_aol_eps_fault) ? 1U : 0U;
}

static uint8_t hyundai_ev9_long_aol_permission_mask(void) {
  const uint8_t requested = aol_ioniq6_request_mask();
  const uint8_t permitted = aol_ioniq6_permission_mask();
  uint8_t result = permitted & 2U;
  const bool lateral_authorized = ((permitted & 1U) != 0U) || (controls_allowed && ((requested & 1U) != 0U));
  // Pedals withdraw ordinary control, while a physically armed AOL lease
  // retains lateral only. The original gear, EPS and freshness gates remain.
  const bool pedal_allowed = (aol_ioniq6_lateral_latch || aol_optional_release_latch) || (!brake_pressed && !gas_pressed);
  const bool gear_current = hyundai_angle_aol_gear_seen &&
    (safety_get_ts_elapsed(microsecond_timer_get(), hyundai_angle_aol_gear_ts) <= 100000U);
  if (lateral_authorized && gear_current && hyundai_angle_aol_drive && vehicle_moving &&
      !hyundai_angle_aol_eps_fault && !hyundai_ordinary_angle_eps_fault && pedal_allowed) {
    result |= 1U;
  }
  return result;
}

static void aol_ioniq6_rx_invalid(void) {
  aol_ioniq6_enable_reset();
  aol_optional_release_policy = false;
  aol_optional_release_neutral = false;
  aol_optional_release_counter_seen = false;
  aol_optional_release_previous = 0U;
  aol_optional_release_pending = false;
  aol_optional_release_latch = false;

  aol_ioniq6_lateral_latch = false;
  aol_ioniq6_lateral_token_claimed = false;
  aol_ioniq6_buttons_seen = false;
  aol_ioniq6_button_prev = false;
}

static bool hyundai_canfd_ioniq6_rx_healthy(void) {
  const uint32_t now = microsecond_timer_get();
  bool healthy = (current_safety_config.rx_checks_len > 0) && (current_safety_config.rx_checks != NULL);
  if (healthy) {
    for (int i = 0; i < current_safety_config.rx_checks_len; i++) {
      const RxCheck *check = &current_safety_config.rx_checks[i];
      const RxStatus *status = &rx_status[i];
      healthy = status->msg_seen && !status->lagging && status->valid_checksum &&
                status->valid_quality_flag && (status->wrong_counters < MAX_WRONG_COUNTERS);
      if (healthy) {
        const uint32_t frequency = check->msg[status->index].frequency;
        if (frequency < 10U) {
          healthy = false;
        } else {
          const uint32_t period_limit = 10000000U / frequency;
          const uint32_t max_age = (period_limit > 100000U) ? period_limit : 100000U;
          healthy = safety_get_ts_elapsed(now, status->last_timestamp) <= max_age;
        }
      }
      if (!healthy) {
        break;
      }
    }
  }
  return healthy;
}
static uint32_t hyundai_canfd_carnival_source_ts = 0U;
static uint32_t hyundai_canfd_carnival_previous_ts = 0U;

static unsigned int hyundai_canfd_get_lka_addr(void) {
  return hyundai_canfd_lka_steer_msg_alt ? 0x110U : 0x50U;
}

static uint8_t hyundai_canfd_get_counter(const CANPacket_t *msg) {
  uint8_t ret = 0;
  if (GET_LEN(msg) == 8U) {
    ret = msg->data[1] >> 4;
  } else {
    ret = msg->data[2];
  }
  return ret;
}

static uint32_t hyundai_canfd_get_checksum(const CANPacket_t *msg) {
  uint32_t chksum = msg->data[0] | (msg->data[1] << 8);
  return chksum;
}

static void hyundai_canfd_ioniq6_optional_rx_hook(const CANPacket_t *msg) {
  const uint32_t now = microsecond_timer_get();
  if (msg->addr == 0x36aU) {
    if ((GET_LEN(msg) == 16U) &&
        (hyundai_canfd_get_checksum(msg) == (hyundai_common_canfd_compute_checksum(msg) ^ 0x041DU)) &&
        ((!hyundai_canfd_ioniq6_corner_counter_seen) || (msg->data[2] != hyundai_canfd_ioniq6_corner_counter))) {
      hyundai_canfd_ioniq6_corner_valid = true;
      hyundai_canfd_ioniq6_corner_counter_seen = true;
      hyundai_canfd_ioniq6_corner_counter = msg->data[2];
      hyundai_canfd_ioniq6_corner_bits = msg->data[3] & 0x18U;
      hyundai_canfd_ioniq6_corner_ts = now;
    } else {
      hyundai_canfd_ioniq6_corner_valid = false;
    }
  } else if ((msg->addr == 0x413U) && (GET_LEN(msg) == 8U)) {
    if (!hyundai_canfd_ioniq6_lamp_valid || safety_get_ts_elapsed(now, hyundai_canfd_ioniq6_lamp_ts) > 1500000U) {
      // A new frame after source loss cannot create a hold from an old true.
      hyundai_canfd_ioniq6_left_lamp = false;
      hyundai_canfd_ioniq6_right_lamp = false;
      hyundai_canfd_ioniq6_left_lamp_off_ts = 0U;
      hyundai_canfd_ioniq6_right_lamp_off_ts = 0U;
    }
    const bool alternate = (msg->data[7] & 0x40U) != 0U;
    const bool left = alternate ? ((msg->data[7] & 0x08U) != 0U) : ((msg->data[2] & 0x10U) != 0U);
    const bool right = alternate ? ((msg->data[7] & 0x20U) != 0U) : ((msg->data[2] & 0x40U) != 0U);
    if (hyundai_canfd_ioniq6_left_lamp && !left) {
      hyundai_canfd_ioniq6_left_lamp_off_ts = now;
    }
    if (hyundai_canfd_ioniq6_right_lamp && !right) {
      hyundai_canfd_ioniq6_right_lamp_off_ts = now;
    }
    if (left) {
      hyundai_canfd_ioniq6_left_lamp_off_ts = 0U;
    }
    if (right) {
      hyundai_canfd_ioniq6_right_lamp_off_ts = 0U;
    }
    hyundai_canfd_ioniq6_left_lamp = left;
    hyundai_canfd_ioniq6_right_lamp = right;
    hyundai_canfd_ioniq6_lamp_valid = true;
    hyundai_canfd_ioniq6_lamp_ts = now;
  } else if (msg->addr == 0x413U) {
    hyundai_canfd_ioniq6_lamp_valid = false;
    hyundai_canfd_ioniq6_left_lamp = false;
    hyundai_canfd_ioniq6_right_lamp = false;
    hyundai_canfd_ioniq6_left_lamp_off_ts = 0U;
    hyundai_canfd_ioniq6_right_lamp_off_ts = 0U;
  } else {
    // No optional Ioniq 6 source update for unrelated addresses.
  }
}

static void hyundai_canfd_optional_rx_hook(const CANPacket_t *msg) {
  hyundai_daw_rx(msg);
  if (hyundai_canfd_ioniq6_long && (msg->bus == 1U)) {
    hyundai_canfd_ioniq6_optional_rx_hook(msg);
  }
}

static bool hyundai_canfd_ioniq6_bsm_sources_healthy(uint32_t now) {
  return hyundai_canfd_ioniq6_rx_healthy() && hyundai_canfd_ioniq6_corner_valid && hyundai_canfd_ioniq6_lamp_valid &&
         safety_get_ts_elapsed(now, hyundai_canfd_ioniq6_corner_ts) <= 100000U &&
         safety_get_ts_elapsed(now, hyundai_canfd_ioniq6_lamp_ts) <= 1500000U;
}

static uint8_t hyundai_canfd_ioniq6_bsm_level(bool detected, bool lamp, uint32_t lamp_off_ts, uint32_t now) {
  const bool held_lamp = lamp || ((lamp_off_ts != 0U) && (safety_get_ts_elapsed(now, lamp_off_ts) <= 500000U));
  return detected ? (held_lamp ? 2U : 1U) : 0U;
}

static void hyundai_canfd_rx_hook(const CANPacket_t *msg) {
  if (hyundai_angle_aol_enabled) {
    const unsigned int pt = hyundai_canfd_lka_steer_msg ? 1U : 0U;
    const unsigned int gear_addr = hyundai_ev_gas_signal ? 0x35U : 0x70U;
    if (msg_matches(msg, gear_addr, pt)) {
      hyundai_angle_aol_drive = (hyundai_ev_gas_signal ? (msg->data[24] & 7U) : ((msg->data[7] >> 4U) & 7U)) == 5U;
      hyundai_angle_aol_gear_seen = true;
      hyundai_angle_aol_gear_ts = microsecond_timer_get();
    }
    if (msg_matches(msg, 0xEAU, pt)) {
      hyundai_angle_aol_eps_fault = (((msg->data[6] >> 6U) & 3U) != 0U) ||
        ((((msg->data[18] >> 4U) & 7U) & hyundai_angle_aol_fault_mask) != 0U);
    }
  }
  if ((hyundai_ordinary_angle_stock || hyundai_canfd_ccnc_ev_long) && (msg->bus == 1U) && (msg->addr == 0x35U)) {
    hyundai_ordinary_angle_drive = (msg->data[24] & 0x7U) == 5U;
  }

  if ((hyundai_ordinary_angle_stock || hyundai_canfd_ccnc_ev_long) && (msg->bus == 1U) && (msg->addr == 0xEAU)) {
    const uint8_t fault = (msg->data[18] >> 4U) & 0x7U;
    if (hyundai_canfd_ccnc_ev_long && !hyundai_canfd_pe_long) {
      hyundai_ordinary_angle_eps_fault = (fault & 2U) != 0U;
    } else {
      hyundai_ordinary_angle_eps_fault = (fault != 0U) || (hyundai_canfd_pe_long && (((msg->data[6] >> 6U) & 3U) != 0U));
    }
  }
  const unsigned pt_bus = hyundai_canfd_lka_steer_msg ? 1U : 0U;
  const unsigned int scc_bus = hyundai_camera_scc ? 2U : pt_bus;

  // driver torque
  if (msg_matches(msg, 0xeaU, pt_bus)) {
    int torque_driver_new = ((msg->data[11] & 0x1fU) << 8U) | msg->data[10];
    torque_driver_new -= 4095;
    update_sample(&torque_driver, torque_driver_new);
    if (hyundai_canfd_angle_steering) {
      const unsigned int offset = hyundai_canfd_ccnc_ev_long ? 16U : 12U;
      int angle_meas_new = (msg->data[offset + 1U] << 8U) | msg->data[offset];
      update_sample(&angle_meas, to_signed(angle_meas_new, 16));
    }
  }

  // cruise buttons
  const unsigned int button_addr = hyundai_canfd_alt_buttons ? 0x1aaU : 0x1cfU;
  if (msg_matches(msg, button_addr, pt_bus)) {
    bool main_button = false;
    int cruise_button = 0;
    if (msg_matches(msg, 0x1cfU, pt_bus)) {
      cruise_button = msg->data[2] & 0x7U;
      main_button = GET_BIT(msg, 19U);
    } else {
      cruise_button = (msg->data[4] >> 4) & 0x7U;
      main_button = GET_BIT(msg, 34U);
    }
    const bool lkas_button = hyundai_canfd_alt_buttons ? GET_BIT(msg, 39U) : GET_BIT(msg, 23U);
    const bool previous_controls_allowed = controls_allowed;
    const int previous_cruise_button = hyundai_stock_torque_aol ? aol_stock_enable_previous : cruise_button_prev;
    const int main_policy = hyundai_canfd_ccnc_ev_long ? (hyundai_canfd_pe_long ? HYUNDAI_MAIN_WITHDRAW : HYUNDAI_MAIN_TRACK) : HYUNDAI_MAIN_NONE;
    const bool normal_main_button = hyundai_canfd_ccnc_ev_long && GET_BIT(msg, 21U);
    hyundai_common_cruise_buttons_check(cruise_button, main_button, normal_main_button, main_policy);
    if ((hyundai_stock_torque_aol || hyundai_canfd_ioniq6_long || hyundai_ev9_long_aol ||
         (aol_optional_release_capable && !aol_optional_release_policy)) && aol_ioniq6_long) {
      static uint8_t aol_ioniq6_enable_counter = 0U;
      static uint32_t aol_ioniq6_enable_ts = 0U;
      const uint8_t counter = hyundai_canfd_get_counter(msg);
      const uint32_t now = microsecond_timer_get();
      const uint32_t elapsed = safety_get_ts_elapsed(now, aol_ioniq6_enable_ts);
      const bool fresh = aol_ioniq6_enable_counter_seen &&
        hyundai_canfd_button_counter_follows(aol_ioniq6_enable_counter, counter, hyundai_canfd_alt_buttons ? 0xFFU : 0xFU) && (elapsed > 0U) && (elapsed <= 100000U);
      if (!fresh || main_button || lkas_button) {
        aol_ioniq6_enable_pending = false;
        aol_ioniq6_enable_release = false;
      } else if (aol_ioniq6_enable_neutral && (previous_cruise_button == HYUNDAI_BTN_NONE) &&
                 ((cruise_button == HYUNDAI_BTN_SET) || (cruise_button == HYUNDAI_BTN_RESUME))) {
        aol_ioniq6_enable_pending = true;
      } else if (aol_ioniq6_enable_pending && (cruise_button == HYUNDAI_BTN_NONE) &&
                 ((previous_cruise_button == HYUNDAI_BTN_SET) || (previous_cruise_button == HYUNDAI_BTN_RESUME))) {
        if ((controls_allowed || hyundai_stock_torque_aol) && aol_rx_healthy()) {
          heartbeat_engaged_mismatches = 0U;
          aol_ioniq6_enable_release = true;
          aol_ioniq6_enable_release_ts = now;
        }
        aol_ioniq6_enable_pending = false;
      } else if ((cruise_button != HYUNDAI_BTN_SET) && (cruise_button != HYUNDAI_BTN_RESUME)) {
        aol_ioniq6_enable_pending = false;
      } else {
        // A held enable button does not renew the heartbeat grace.
      }
      aol_ioniq6_enable_neutral = !main_button && !lkas_button && (cruise_button == HYUNDAI_BTN_NONE);
      aol_ioniq6_enable_counter_seen = true;
      aol_ioniq6_enable_counter = counter;
      aol_ioniq6_enable_ts = now;
      aol_stock_enable_previous = cruise_button;
    }
    if (hyundai_canfd_ccnc_ev_long && !previous_controls_allowed && controls_allowed) {
      hyundai_ev9_inactive_accel_count = 0U;
    }
    if (aol_ioniq6_long && (msg_matches(msg, hyundai_canfd_alt_buttons ? 0x1aaU : 0x1cfU, pt_bus))) {
      if (aol_optional_release_capable && aol_optional_release_policy) {
        static uint8_t aol_optional_release_counter = 0U;
        static uint32_t aol_optional_release_sample_ts = 0U;
        const uint8_t counter = hyundai_canfd_get_counter(msg);
        const uint8_t maximum = hyundai_canfd_alt_buttons ? 0xffU : 0xfU;
        const uint32_t sample_ts = microsecond_timer_get();
        const uint32_t elapsed = safety_get_ts_elapsed(sample_ts, aol_optional_release_sample_ts);
        const bool fresh = !aol_optional_release_counter_seen ||
          (hyundai_canfd_button_counter_follows(aol_optional_release_counter, counter, maximum) &&
           (elapsed > 0U) && (elapsed <= 100000U));
        aol_optional_release_counter_seen = true;
        aol_optional_release_counter = counter;
        aol_optional_release_sample_ts = sample_ts;
        if (!fresh) {
          aol_optional_release_neutral = false;
          aol_optional_release_previous = 0U;
          aol_optional_release_pending = false;
          aol_optional_release_latch = false;
        }
        const bool raw_neutral = fresh && !main_button &&
          !(hyundai_canfd_alt_buttons ? GET_BIT(msg, 39U) : GET_BIT(msg, 23U)) && (cruise_button == HYUNDAI_BTN_NONE);
        if (!aol_optional_release_neutral) {
          aol_optional_release_neutral = raw_neutral;
        } else if (fresh && ((aol_optional_release_previous == HYUNDAI_BTN_SET) ||
                    (aol_optional_release_previous == HYUNDAI_BTN_RESUME)) && (cruise_button == HYUNDAI_BTN_NONE)) {
          aol_optional_release_pending = true;
          aol_optional_release_ts = microsecond_timer_get();
        } else {
          // No release edge; retain the current neutral/release state.
        }
        aol_optional_release_previous = (uint8_t)cruise_button;
      }
      const bool gesture = main_button || lkas_button;
      if (cruise_button == HYUNDAI_BTN_CANCEL) {
        aol_ioniq6_enable_reset();
        aol_optional_release_pending = false;
        if ((!hyundai_torque_aol_cancel_preserves_lateral && !hyundai_angle_aol_enabled) ||
            (!aol_ioniq6_lateral_token_claimed && !aol_optional_release_latch)) {
          aol_ioniq6_lateral_latch = false;
          aol_optional_release_policy = false;
          aol_optional_release_neutral = false;
          aol_optional_release_counter_seen = false;
          aol_optional_release_previous = 0U;
          aol_optional_release_pending = false;
          aol_optional_release_latch = false;
          aol_ioniq6_lateral_token_claimed = false;
          aol_set_host_request(0U);
        }
      } else if (!aol_ioniq6_buttons_seen) {
        // A held button at safety init is not a fresh deliberate gesture.
        aol_ioniq6_buttons_seen = !gesture;
      } else if (gesture && !aol_ioniq6_button_prev && !aol_ioniq6_lateral_latch && !aol_optional_release_latch) {
        // Host owns the configured on/off toggle. A later MAIN/LKAS edge
        // cannot refresh an existing token or its original claim deadline.
        aol_ioniq6_lateral_latch = true;
        aol_ioniq6_lateral_token_claimed = false;
        aol_ioniq6_gesture_ts = microsecond_timer_get();
      } else {
        // No new deliberate gesture in this button sample.
      }
      aol_ioniq6_button_prev = gesture;
    }
  }
  if (hyundai_canfd_carnival_alt_resume && (msg_matches(msg, 0x1aaU, pt_bus))) {
    static uint8_t hyundai_canfd_carnival_rx_counter = 0U;
    const bool source_fresh = !hyundai_canfd_carnival_counter_seen ||
      hyundai_canfd_button_counter_follows(hyundai_canfd_carnival_rx_counter, msg->data[2], 0xFFU);
    hyundai_canfd_carnival_counter_seen = true;
    hyundai_canfd_carnival_rx_counter = msg->data[2];
    const bool preceding_valid = source_fresh && hyundai_canfd_carnival_source_valid &&
      hyundai_canfd_button_counter_follows(hyundai_canfd_carnival_source[2], msg->data[2], 0xFFU);
    hyundai_canfd_carnival_previous_valid = false;
    hyundai_canfd_carnival_source_valid = false;
    const bool idle_buttons = ((msg->data[4] & 0xF4U) == 0U) && ((msg->data[5] & 0x02U) == 0U);
    if (source_fresh && idle_buttons && ((!hyundai_canfd_carnival_counter_consumed) ||
                         (msg->data[2] != hyundai_canfd_carnival_consumed_counter)) &&
        (hyundai_canfd_get_checksum(msg) == (hyundai_common_canfd_compute_checksum(msg) ^ 0x041DU))) {
      if (preceding_valid) {
        for (int i = 0; i < 16; i++) {
          hyundai_canfd_carnival_previous[i] = hyundai_canfd_carnival_source[i];
        }
        hyundai_canfd_carnival_previous_ts = hyundai_canfd_carnival_source_ts;
        hyundai_canfd_carnival_previous_valid = true;
      }
      for (int i = 0; i < 16; i++) {
        hyundai_canfd_carnival_source[i] = msg->data[i];
      }
      hyundai_canfd_carnival_source_ts = microsecond_timer_get();
      hyundai_canfd_carnival_source_valid = true;
    }
  }

  // gas press, different for EV, hybrid, and ICE models
  if ((msg_matches(msg, 0x35U, pt_bus)) && hyundai_ev_gas_signal) {
    gas_pressed = msg->data[5] != 0U;
  } else if ((msg_matches(msg, 0x105U, pt_bus)) && hyundai_hybrid_gas_signal) {
    gas_pressed = GET_BIT(msg, 103U) || (msg->data[13] != 0U) || GET_BIT(msg, 112U);
  } else if ((msg_matches(msg, 0x100U, pt_bus)) && !hyundai_ev_gas_signal && !hyundai_hybrid_gas_signal) {
    gas_pressed = GET_BIT(msg, 176U);
  } else {
  }

  // brake press
  if (msg_matches(msg, 0x175U, pt_bus)) {
    brake_pressed = GET_BIT(msg, 81U);
  }

  // vehicle moving
  if (msg_matches(msg, 0xa0U, pt_bus)) {
    uint32_t fl = (GET_BYTES_LE(msg, 8, 2)) & 0x3FFFU;
    uint32_t fr = (GET_BYTES_LE(msg, 10, 2)) & 0x3FFFU;
    uint32_t rl = (GET_BYTES_LE(msg, 12, 2)) & 0x3FFFU;
    uint32_t rr = (GET_BYTES_LE(msg, 14, 2)) & 0x3FFFU;
    vehicle_moving = (fl > HYUNDAI_STANDSTILL_THRSLD) || (fr > HYUNDAI_STANDSTILL_THRSLD) ||
                     (rl > HYUNDAI_STANDSTILL_THRSLD) || (rr > HYUNDAI_STANDSTILL_THRSLD);

    // average of all 4 wheel speeds. Conversion: raw * 0.03125 / 3.6 = m/s
    UPDATE_VEHICLE_SPEED((fr + rr + rl + fl) / 4.0 * 0.03125 * KPH_TO_MS);
    if (hyundai_canfd_ioniq6_long) {
      // Mean wheel speed < 5 mph: raw sum < 2.2352 * 4 * 3.6 / 0.03125.
      // This hook runs only for valid required RX; TX also checks its freshness.
      const bool fresh_counter = !hyundai_canfd_ioniq6_bsm_wheel_seen ||
                                 (msg->data[2] == (uint8_t)(hyundai_canfd_ioniq6_bsm_wheel_counter + 1U));
      hyundai_canfd_ioniq6_bsm_low_speed = fresh_counter && ((fr + rr + rl + fl) < 1030U);
      if (fresh_counter) {
        hyundai_canfd_ioniq6_bsm_wheel_ts = microsecond_timer_get();
      }
      hyundai_canfd_ioniq6_bsm_wheel_seen = true;
      hyundai_canfd_ioniq6_bsm_wheel_counter = msg->data[2];
    }
  }

  // cruise state
  if (msg_matches(msg, 0x1a0U, scc_bus) && !hyundai_longitudinal) {
    // 1=enabled, 2=driver override
    int cruise_status = ((msg->data[8] >> 4) & 0x7U);
    bool cruise_engaged = (cruise_status == 1) || (cruise_status == 2);
    hyundai_common_cruise_state_check(cruise_engaged);
  }
}

static bool hyundai_canfd_gv60_heartbeat_valid(const CANPacket_t *msg) {
  static const uint8_t body[24] = {
    0, 0, 0, 2, 0, 0, 0xfc, 0xff, 0, 0, 0, 0, 0, 0x20, 0, 0,
    0x55, 0xff, 0, 0, 0x68, 0, 0, 0,
  };
  bool valid = hyundai_canfd_ioniq6_rx_healthy() &&
               (hyundai_canfd_get_checksum(msg) == hyundai_common_canfd_compute_checksum(msg)) &&
               ((!hyundai_canfd_gv60_heartbeat_seen) || (msg->data[2] != hyundai_canfd_gv60_heartbeat_counter)) &&
               (GET_BIT(msg, 32U) == brake_pressed) && (GET_BIT(msg, 176U) == gas_pressed);
  for (int i = 3; i < 24; i++) {
    const uint8_t mask = ((i == 4) || (i == 22)) ? 0xfeU : 0xffU;
    valid &= (msg->data[i] & mask) == body[i];
  }
  if (valid) {
    hyundai_canfd_gv60_heartbeat_seen = true;
    hyundai_canfd_gv60_heartbeat_counter = msg->data[2];
  }
  return valid;
}

#ifdef ALLOW_DEBUG
static safety_config hyundai_canfd_gv60_extend_tx(const safety_config original, const CanMsg *expected, int expected_len,
                                                const CanMsg *replacement, int replacement_len) {
  safety_config ret = original;
  if ((ret.tx_msgs == expected) && (ret.tx_msgs_len == expected_len)) {
    ret.tx_msgs = replacement;
    ret.tx_msgs_len = replacement_len;
    hyundai_canfd_gv60_long = true;
  } else {
    ret.rx_checks_len = 0;
    ret.tx_msgs_len = 0;
  }
  return ret;
}
#endif

static bool hyundai_canfd_tx_hook_valid(const CANPacket_t *msg) {
  static uint8_t hyundai_canfd_ioniq6_lfa_mirror[4] = {0};
  const TorqueSteeringLimits HYUNDAI_CANFD_STEERING_LIMITS = {
    .max_torque = 409,
    .max_rt_delta = 375,
    .max_rate_up = 10,
    .max_rate_down = 3,
    .driver_torque_allowance = 250,
    .driver_torque_multiplier = 2,
    .type = TorqueDriverLimited,

    // the EPS faults when the steering angle is above a certain threshold for too long. to prevent this,
    // we allow setting torque actuation bit to 0 while maintaining the requested torque value for two consecutive frames
    .min_valid_request_frames = 89,
    .max_invalid_request_frames = 2,
    .min_valid_request_rt_interval = 810000,  // 810ms; a ~10% buffer on cutting every 90 frames
    .has_steer_req_tolerance = true,
  };
  const AngleSteeringLimits HYUNDAI_CANFD_ANGLE_LIMITS = {
    .max_angle = 3600,
    .angle_deg_to_can = 10,
    .frequency = 100U,
  };
  // These are the actual CP VehicleModel geometries, including tire-stiffness
  // derived slip. Selector bits 11-13 are valid only under the angle bit 14.
  const AngleSteeringParams HYUNDAI_CANFD_ANGLE_MODELS[] = {
    {.slip_factor = -0.0005844189175F, .steer_ratio = 14.6F,  .wheelbase = 2.87F},
    {.slip_factor = -0.0005844189054F, .steer_ratio = 17.1F,  .wheelbase = 2.87F},
    {.slip_factor = -0.0005685702046F, .steer_ratio = 14.14F, .wheelbase = 2.95F},
    {.slip_factor = -0.0005358728714F, .steer_ratio = 16.02F, .wheelbase = 3.13F},
    {.slip_factor = -0.0005793721360F, .steer_ratio = 13.5F,  .wheelbase = 2.895F},
    {.slip_factor = -0.0009169985667F, .steer_ratio = 13.27F, .wheelbase = 2.814F},
    {.slip_factor = -0.0006085930193F, .steer_ratio = 13.7F,  .wheelbase = 2.756F},
    {.slip_factor = -0.0005968975988F, .steer_ratio = 13.72F, .wheelbase = 2.81F},  // CCNC Santa Fe HEV
    {.slip_factor = -0.0006085929296F, .steer_ratio = 13.7F,  .wheelbase = 2.756F}, // CCNC Sportage 2026
    {.slip_factor = -0.0008898049378F, .steer_ratio = 14.26F, .wheelbase = 2.9F},   // EV6 2025
    {.slip_factor = -0.0008688329820F, .steer_ratio = 14.26F, .wheelbase = 2.97F}, // Ioniq 5 PE: typed CP geometry
    {.slip_factor = -0.0005410588126F, .steer_ratio = 16.0F, .wheelbase = 3.1F}, // EV9: typed CP geometry
  };

  bool tx = true;

  // steering
  const bool torque_lka_long = hyundai_canfd_ioniq6_long || hyundai_canfd_torque_ev_long ||
                               (hyundai_canfd_lka_steer_msg && hyundai_longitudinal && !hyundai_canfd_angle_steering);
  const unsigned int steer_addr = (hyundai_canfd_lka_steer_msg && !hyundai_longitudinal) ? hyundai_canfd_get_lka_addr() : 0x12aU;
  if (msg->addr == steer_addr) {
    if (torque_lka_long) {
      // Even a rejected replacement invalidates an older unpaired command.
      hyundai_canfd_ioniq6_lfa_unpaired = false;
    }
    if (hyundai_canfd_ccnc_ev_long && (msg->addr == 0x12AU)) {
      // Reached original caller emits only neutral LFA outside Drive.
      const unsigned int torque_unsigned = (((unsigned int)msg->data[6] & 0xFU) << 7U) | ((unsigned int)msg->data[5] >> 1U);
      const int torque = (int)torque_unsigned - 1024;
      tx &= (torque == 0) && ((msg->data[6] & 0x30U) == 0U);
      for (int i = 7; i < 16; i++) {
        tx &= msg->data[i] == ((i == 13) ? 0x64U : 0U);
      }
    } else if (hyundai_canfd_angle_steering) {
      const int angle_active = (msg->data[9] >> 4U) & 0x3U;
      const bool steer_req = angle_active == 2;
      int desired_angle = (msg->data[11] << 6U) | (msg->data[10] >> 2U);
      desired_angle = to_signed(desired_angle, 14);
      const uint8_t gain_raw = msg->data[12];
      const unsigned int torque_high = ((unsigned int)msg->data[6] & 0xFU) << 7U;
      const unsigned int torque_low = (unsigned int)msg->data[5] >> 1U;
      const unsigned int torque_bits = torque_high | torque_low;
      const int torque_req = (int)torque_bits - 1024;
      const int torque_enabled = (msg->data[6] >> 4U) & 0x3U;
      // The current LFA DBC aliases FCA_ESA_CtrlSta onto a gain bit, so only
      // LKAS_ALT has an independently checkable FCA enable field.
      const bool fca_esa_enabled = (msg->addr == 0x110U) && ((msg->data[13] & 0x08U) != 0U);
      const bool neutral_status = hyundai_canfd_angle_observed_adas && (msg->addr == 0x12AU);
      if (((angle_active != 1) && (angle_active != 2)) || (gain_raw > 250U) ||
          (torque_req != 0) || (torque_enabled != 0) || fca_esa_enabled ||
          ((!steer_req) && (gain_raw != 0U)) ||
          (neutral_status && ((angle_active != 1) || steer_angle_cmd_inactive_check(desired_angle, HYUNDAI_CANFD_ANGLE_LIMITS.max_angle))) ||
          (!neutral_status && steer_angle_cmd_checks_vm(desired_angle, steer_req, HYUNDAI_CANFD_ANGLE_LIMITS,
                                                        HYUNDAI_CANFD_ANGLE_MODELS[hyundai_canfd_angle_model]))) {
        tx = false;
      }
    } else {
      int desired_torque = (((msg->data[6] & 0xFU) << 7U) | (msg->data[5] >> 1U)) - 1024U;
      bool steer_req = GET_BIT(msg, 52U);
      if (steer_torque_cmd_checks(desired_torque, steer_req, HYUNDAI_CANFD_STEERING_LIMITS)) {
        tx = false;
      }
      if (torque_lka_long) {
        // This E-CAN command is the sole limited steering authority. Other
        // angle/enable fields in the same payload remain the host's fixed
        // neutral status, and CRC must cover the exact wire bytes.
        for (int i = 7; i < 16; i++) {
          tx &= msg->data[i] == ((i == 13) ? 0x64U : 0U);
        }
        tx &= hyundai_canfd_get_checksum(msg) == (hyundai_common_canfd_compute_checksum(msg) ^ 0x041DU);
      }
      if ((torque_lka_long) && tx) {
        for (int i = 0; i < 4; i++) {
          hyundai_canfd_ioniq6_lfa_mirror[i] = msg->data[i + 3];
        }
        hyundai_canfd_ioniq6_lfa_ts = microsecond_timer_get();
        hyundai_canfd_ioniq6_lfa_unpaired = true;
      }
    }
  }

  // The HDA-II controller sends its limited E-CAN LFA command first, followed
  // by the identical steering/status request on A-CAN. The second channel may
  // mirror only that single fresh, accepted command; it must not run the global
  // torque limiter a second time or create independent steering authority.
  if ((torque_lka_long) && (msg->addr == hyundai_canfd_get_lka_addr())) {
    bool mirror = hyundai_canfd_ioniq6_lfa_unpaired &&
                  safety_get_ts_elapsed(microsecond_timer_get(), hyundai_canfd_ioniq6_lfa_ts) <= 10000U;
    const int mirror_torque = (((msg->data[6] & 0xFU) << 7U) | (msg->data[5] >> 1U)) - 1024U;
    const bool mirror_req = GET_BIT(msg, 52U);
    if (((mirror_torque != 0) || mirror_req) && !lateral_controls_allowed()) {
      mirror = false;
    }
    for (int i = 0; i < 4; i++) {
      mirror &= msg->data[i + 3] == hyundai_canfd_ioniq6_lfa_mirror[i];
    }
    if (msg->addr == 0x110U) {
      mirror &= (msg->data[13] & 0x08U) == 0U; // no FCA_ESA actuation
    }
    for (uint8_t i = 7U; i < GET_LEN(msg); i++) {
      mirror &= msg->data[i] == (((i == 8U) && hyundai_canfd_lkas_damping_100) ? 0x64U : 0U);
    }
    mirror &= hyundai_canfd_get_checksum(msg) == (hyundai_common_canfd_compute_checksum(msg) ^
                                                 ((GET_LEN(msg) == 16U) ? 0x041DU : 0U));
    hyundai_canfd_ioniq6_lfa_unpaired = false;
    tx &= mirror;
  }

  if (hyundai_canfd_ioniq6_long && ((msg->addr == 0x51U) || (msg->addr == 0x160U) || (msg->addr == 0x1EAU) ||
                                   (msg->addr == 0x200U) || (msg->addr == 0x345U) || (msg->addr == 0x1DAU))) {
    // These ADR V frames are constant-status emulation in the current host
    // controller. Reject any other actuation/enable payload, including one
    // with a recomputed CRC. Bytes 0-1 are CRC, byte 2 is the rolling counter;
    // 0x160 additionally has a 20-frame warning counter in byte 3.
    const uint8_t adr_51[29] = {0};
    const uint8_t adr_160[12] = {0, 0, 0, 2, 0xff, 0xfc, 9, 0, 0, 0, 0, 0};
    const uint8_t adr_1ea[29] = {[0] = 0x1c, [12] = 0xff, [26] = 0x0f, [27] = 0x0f};
    const uint8_t adr_200[5] = {0xe1, 0x3a, 0, 0, 0};
    const uint8_t adr_345[5] = {0x15, 0, 0, 0, 0};
    const uint8_t adr_1da[29] = {
      0x22, 0, 0x41, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    };
    const uint8_t *expected = NULL;
    if (msg->addr == 0x51U) expected = adr_51;
    if (msg->addr == 0x1EAU) expected = adr_1ea;
    if (msg->addr == 0x200U) expected = adr_200;
    if (msg->addr == 0x345U) expected = adr_345;
    if (msg->addr == 0x1DAU) expected = adr_1da;
    if (expected != NULL) {
      for (uint8_t i = 3U; i < GET_LEN(msg); i++) {
        tx &= msg->data[i] == expected[i - 3U];
      }
    } else {
      // 0x160 byte 3 is a source warning counter; all other bytes are fixed.
      for (uint8_t i = 4U; i < GET_LEN(msg); i++) {
        tx &= msg->data[i] == adr_160[i - 4U];
      }
    }
    uint16_t crc_xor = (GET_LEN(msg) == 8U) ? 0x5F29U : ((GET_LEN(msg) == 16U) ? 0x041DU : 0U);
    tx &= hyundai_canfd_get_checksum(msg) == (hyundai_common_canfd_compute_checksum(msg) ^ crc_xor);
  }

  if (hyundai_canfd_gv60_long && (msg->addr == 0x100U)) {
    tx &= hyundai_canfd_gv60_heartbeat_valid(msg);
  }

  if (hyundai_canfd_ioniq6_long && (msg->addr == 0x100U)) {
    // User-route sendcan evidence: this A-CAN radar heartbeat keeps one
    // constant 24-byte body; only CRC, counter, physical brake, and physical
    // gas may vary. A duplicate counter cannot replay the last heartbeat.
    static const uint8_t body[24] = {
      0, 0, 0, 2, 0, 0, 0xfc, 0xff, 0, 0, 0, 0, 0, 0x20, 0, 0,
      0x55, 0xff, 0, 0, 0x68, 0, 0, 0,
    };
    bool valid = hyundai_canfd_ioniq6_rx_healthy() &&
                 (hyundai_canfd_get_checksum(msg) == hyundai_common_canfd_compute_checksum(msg)) &&
                 ((!hyundai_canfd_ioniq6_heartbeat_seen) || (msg->data[2] != hyundai_canfd_ioniq6_heartbeat_counter)) &&
                 (GET_BIT(msg, 32U) == brake_pressed) && (GET_BIT(msg, 176U) == gas_pressed);
    for (int i = 3; i < 24; i++) {
      const uint8_t mask = ((i == 4) || (i == 22)) ? 0xfeU : 0xffU;
      valid &= (msg->data[i] & mask) == body[i];
    }
    if (valid) {
      hyundai_canfd_ioniq6_heartbeat_seen = true;
      hyundai_canfd_ioniq6_heartbeat_counter = msg->data[2];
    }
    tx &= valid;
  }

  if (hyundai_canfd_ioniq6_long && (msg->addr == 0x1baU)) {
    // 0x1BA begins a one-use, paired blind-spot dashboard status. Any new
    // attempt invalidates the older pending pair, accepted or rejected.
    hyundai_canfd_ioniq6_bsm_pair_pending = false;
    const uint32_t now = microsecond_timer_get();
    bool valid = hyundai_canfd_ioniq6_bsm_sources_healthy(now) &&
                 (hyundai_canfd_get_checksum(msg) == hyundai_common_canfd_compute_checksum(msg)) &&
                 ((!hyundai_canfd_ioniq6_bsm_counter_seen) || (msg->data[2] != hyundai_canfd_ioniq6_bsm_counter));
    const uint8_t left = hyundai_canfd_ioniq6_bsm_level((hyundai_canfd_ioniq6_corner_bits & 0x10U) != 0U,
      hyundai_canfd_ioniq6_left_lamp, hyundai_canfd_ioniq6_left_lamp_off_ts, now);
    const uint8_t right = hyundai_canfd_ioniq6_bsm_level((hyundai_canfd_ioniq6_corner_bits & 0x08U) != 0U,
      hyundai_canfd_ioniq6_right_lamp, hyundai_canfd_ioniq6_right_lamp_off_ts, now);
    uint8_t body[24] = {0};
    body[3] = ((left || right) ? 1U : 0U) | (left << 6U);
    body[4] = right;
    // Retired FL_INDICATOR overlaps the right sound-warning bits; require zero.
    body[6] = right << 5U;
    body[7] = 0x80U | (SAFETY_MAX(left, right) << 3U);
    body[16] = left | (right << 2U);
    bool physical_body = true;
    bool neutral_body = hyundai_canfd_ioniq6_bsm_low_speed &&
                        (safety_get_ts_elapsed(now, hyundai_canfd_ioniq6_bsm_wheel_ts) <= 100000U);
    for (int i = 3; i < 24; i++) {
      physical_body &= msg->data[i] == body[i];
      // Only the complete frozen neutral indication may replace a detection.
      neutral_body &= msg->data[i] == ((i == 7) ? 0x80U : 0U);
    }
    valid &= physical_body || neutral_body;
    if (valid) {
      hyundai_canfd_ioniq6_bsm_counter_seen = true;
      hyundai_canfd_ioniq6_bsm_counter = msg->data[2];
      hyundai_canfd_ioniq6_bsm_pair_pending = true;
      hyundai_canfd_ioniq6_bsm_pair_counter = msg->data[2];
      hyundai_canfd_ioniq6_bsm_pair_ts = now;
    }
    tx &= valid;
  }
  if (hyundai_canfd_ioniq6_long && (msg->addr == 0x1e5U)) {
    const uint32_t now = microsecond_timer_get();
    static const uint8_t body[16] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0};
    bool valid = hyundai_canfd_ioniq6_bsm_pair_pending &&
                 hyundai_canfd_ioniq6_bsm_sources_healthy(now) &&
                 (safety_get_ts_elapsed(now, hyundai_canfd_ioniq6_bsm_pair_ts) <= 10000U) &&
                 (msg->data[2] == hyundai_canfd_ioniq6_bsm_pair_counter) &&
                 hyundai_canfd_get_checksum(msg) == (hyundai_common_canfd_compute_checksum(msg) ^ 0x041DU);
    hyundai_canfd_ioniq6_bsm_pair_pending = false;
    for (int i = 3; i < 16; i++) {
      valid &= msg->data[i] == body[i];
    }
    tx &= valid;
  }

  if (msg->addr == 0xCBU) {
    if (!hyundai_canfd_angle_steering || (!hyundai_canfd_ccnc_ev_long && (!hyundai_canfd_angle_observed_adas || hyundai_canfd_lka_steer_msg))) {
      tx = false;
    } else {
      const int angle_active = (msg->data[3] >> 4U) & 0xFU;
      const bool steer_req = angle_active == 2;
      int desired_angle = ((msg->data[5] & 0x3FU) << 8U) | msg->data[4];
      desired_angle = to_signed(desired_angle, 14);
      const uint8_t gain_raw = msg->data[6];
      if (hyundai_canfd_ccnc_ev_long) {
        tx &= (msg->bus == 1U) && aol_rx_healthy() && !relay_malfunction &&
              (hyundai_canfd_get_checksum(msg) == hyundai_common_canfd_compute_checksum(msg));
        if (steer_req) {
          if (hyundai_ev9_long_aol) {
            tx &= (hyundai_ev9_long_aol_permission_mask() & 1U) != 0U;
          } else {
            tx &= (controls_allowed && vehicle_moving && hyundai_ordinary_angle_drive &&
                 !hyundai_ordinary_angle_eps_fault && !brake_pressed && !gas_pressed);
          }
        }
      }
      if (((angle_active != 1) && (angle_active != 2)) || (gain_raw > 250U) ||
          ((!steer_req) && (gain_raw != 0U)) || ((msg->data[3] & 0xFU) != 0U) ||
          ((msg->data[7] & 0x3U) != 0U) || (msg->data[8] != 0U) ||
          steer_angle_cmd_checks_vm(desired_angle, steer_req, HYUNDAI_CANFD_ANGLE_LIMITS,
                                    HYUNDAI_CANFD_ANGLE_MODELS[hyundai_canfd_angle_model])) {
        tx = false;
      }
    }
  }

  // cruise buttons check
  if (msg->addr == 0x1cfU) {
    int button = msg->data[2] & 0x7U;
    bool is_cancel = (button == HYUNDAI_BTN_CANCEL);
    bool is_resume = (button == HYUNDAI_BTN_RESUME);

    bool allowed = (is_cancel && cruise_engaged_prev) || (is_resume && controls_allowed);
    if (!allowed) {
      tx = false;
    }
  }
  if (msg->addr == 0x1aaU) {
    bool current_match = hyundai_canfd_carnival_source_valid &&
      safety_get_ts_elapsed(microsecond_timer_get(), hyundai_canfd_carnival_source_ts) <= 100000U &&
      (msg->data[2] == (uint8_t)(hyundai_canfd_carnival_source[2] + 1U));
    bool previous_match = hyundai_canfd_carnival_previous_valid &&
      safety_get_ts_elapsed(microsecond_timer_get(), hyundai_canfd_carnival_previous_ts) <= 100000U &&
      (msg->data[2] == (uint8_t)(hyundai_canfd_carnival_previous[2] + 1U));
    bool allowed = hyundai_canfd_carnival_alt_resume && controls_allowed && !safety_rx_checks_invalid &&
                   ((msg->data[3] & 0x30U) == 0x10U) && ((msg->data[4] & 0x70U) == 0x10U) &&
                   (hyundai_canfd_get_checksum(msg) == (hyundai_common_canfd_compute_checksum(msg) ^ 0x041DU));
    for (int i = 3; i < 16; i++) {
      const uint8_t changed_bits = (i == 3) ? 0x30U : ((i == 4) ? 0x70U : 0U);
      const uint8_t stable_bits = (uint8_t)~changed_bits;
      current_match &= ((msg->data[i] & stable_bits) == (hyundai_canfd_carnival_source[i] & stable_bits));
      previous_match &= ((msg->data[i] & stable_bits) == (hyundai_canfd_carnival_previous[i] & stable_bits));
    }
    allowed &= current_match || previous_match;
    if (!allowed) {
      tx = false;
    } else {
      hyundai_canfd_carnival_consumed_counter = current_match ? hyundai_canfd_carnival_source[2] : hyundai_canfd_carnival_previous[2];
      hyundai_canfd_carnival_counter_consumed = true;
      hyundai_canfd_carnival_previous_valid = false;
      if (current_match) {
        hyundai_canfd_carnival_source_valid = false;
      }
    }
  }

  // UDS: only tester present ("\x02\x3E\x80\x00\x00\x00\x00\x00") allowed on diagnostics address
  if (((msg->addr == 0x730U) && hyundai_canfd_lka_steer_msg) || ((msg->addr == 0x7D0U) && !hyundai_camera_scc)) {
    if (GET_BYTES_64_LE(msg, 0, 8) != 0x0000000000803E02ULL) {
      tx = false;
    }
  }

  // ACCEL: safety check
  if (msg->addr == 0x1a0U) {
    int desired_accel_raw = (((msg->data[17] & 0x7U) << 8) | msg->data[16]) - 1023U;
    int desired_accel_val = ((msg->data[18] << 4) | (msg->data[17] >> 4)) - 1023U;

    bool violation = false;

    if (hyundai_longitudinal) {
      // Exact EV9's reached PID ceiling is 2.2 m/s2. Other vehicles retain
      // their existing 2.0 m/s2 maximum; this is narrower than original EV9 safety.
      const LongitudinalLimits ev9_long_limits = {.max_accel = 220, .min_accel = -350};
      const LongitudinalLimits limits = (hyundai_canfd_ccnc_ev_long && !hyundai_canfd_pe_long) ? ev9_long_limits : HYUNDAI_LONG_LIMITS;
      if (hyundai_canfd_ccnc_ev_long && ((desired_accel_raw != 0) || (desired_accel_val != 0))) {
        violation |= !aol_rx_healthy();
      }
      violation |= longitudinal_accel_checks(desired_accel_raw, limits);
      violation |= longitudinal_accel_checks(desired_accel_val, limits);
    } else {
      // only used to cancel on here
      const int acc_mode = (msg->data[8] >> 4) & 0x7U;
      if (acc_mode != 4) {
        violation = true;
      }

      if ((desired_accel_raw != 0) || (desired_accel_val != 0)) {
        violation = true;
      }
    }

    if (violation) {
      tx = false;
    }
  }

  if (hyundai_canfd_ccnc_ev_long) {
    // No reached EV9 LONG caller emits 110; its steering owner is direct CB.
    tx &= msg->addr != 0x110U;
    if (msg->addr == 0x362U) {
      tx &= hyundai_ev9_long_aol ? (((hyundai_ev9_long_aol_permission_mask() & 1U) != 0U) && hyundai_ordinary_angle_owner_current()) :
            (controls_allowed && aol_rx_healthy() && vehicle_moving && hyundai_ordinary_angle_drive &&
             !hyundai_ordinary_angle_eps_fault && !brake_pressed && !gas_pressed);
    }
    if ((msg->addr != 0x730U) && (msg->addr != 0x1CFU)) {
      const unsigned int len = GET_LEN(msg);
      const uint32_t xor_out = (len == 16U) ? (uint32_t)0x041DU : ((len == 8U) ? (uint32_t)0x5F29U : (uint32_t)0U);
      tx &= hyundai_canfd_get_checksum(msg) == (hyundai_common_canfd_compute_checksum(msg) ^ xor_out);
    }
    if (msg->addr == 0x100U) {
      static const uint8_t ev9_heartbeat[24] = {
        0U, 0U, 0U, 0U, 0xFFU, 0U, 0x6FU, 0U, 0xE8U, 4U, 0U, 0U,
        0x12U, 1U, 3U, 0U, 0x55U, 0xFFU, 0xFFU, 0U, 0U, 0U, 0U, 0U,
      };
      static const uint8_t pe_heartbeat[24] = {
        0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0xFFU, 0x1FU, 0xFFU, 0xFFU, 0x55U, 0xFFU, 0xFFU, 0x00U, 0xA8U, 0x00U, 0x00U, 0x00U,
      };
      for (int i = 3; i < 24; i++) {
        uint8_t expected = hyundai_canfd_pe_long ? pe_heartbeat[i] : ev9_heartbeat[i];
        if (i == 4) {
          expected = (uint8_t)((expected & 0xFEU) | (brake_pressed ? 1U : 0U));
        } else if (i == 22) {
          expected = (uint8_t)((expected & 0xFEU) | (gas_pressed ? 1U : 0U));
        } else {
          // The original caller changes only counter and physical pedal bits.
        }
        tx &= msg->data[i] == expected;
      }
    }
    if (tx && (msg->addr == 0x1A0U)) {
      const unsigned int raw_unsigned = (((unsigned int)msg->data[17] & 0x7U) << 8U) | (unsigned int)msg->data[16];
      const int raw = (int)raw_unsigned - 1023;
      const unsigned int val_unsigned = ((unsigned int)msg->data[18] << 4U) | ((unsigned int)msg->data[17] >> 4U);
      const int val = (int)val_unsigned - 1023;
      const bool inactive = (((msg->data[8] >> 4U) & 0x7U) == 0U) && (raw == 0) && (val == 0);
      hyundai_ev9_inactive_accel_count = inactive ? SAFETY_MIN(hyundai_ev9_inactive_accel_count + 1U, 10U) : 0U;
      if (hyundai_ev9_inactive_accel_count >= 10U) {
        controls_allowed = false;
      }
    }
  }
  return tx;
}

static bool hyundai_canfd_tx_hook(const CANPacket_t *msg) {
  bool tx = false;
  const bool ordinary_angle_frame = hyundai_ordinary_angle_stock && ((msg->addr == 0x110U) || (msg->addr == 0x362U));
  bool ordinary_angle_valid = true;
  const bool independent_command = hyundai_angle_aol_enabled &&
    ((msg->addr == 0x50U) || (msg->addr == 0x110U) ||
     ((msg->addr == 0x12AU) && !hyundai_canfd_angle_observed_adas) || (msg->addr == 0xCBU));
  bool independent_active = false;
  if (independent_command) {
    independent_active = (msg->addr == 0xCBU) ? (((msg->data[3] >> 4U) & 0xFU) == 2U) : (((msg->data[9] >> 4U) & 3U) == 2U);
    if (independent_active) {
      const unsigned int len = GET_LEN(msg);
      const uint32_t xor_out = (len == 16U) ? 0x041DU : 0U;
      ordinary_angle_valid = (hyundai_ordinary_angle_permission_mask() != 0U) &&
        (hyundai_canfd_get_checksum(msg) == (hyundai_common_canfd_compute_checksum(msg) ^ xor_out));
      if (hyundai_ordinary_angle_counter_seen) {
        const uint8_t delta = (uint8_t)(msg->data[2] - hyundai_ordinary_angle_counter);
        ordinary_angle_valid &= (delta > 0U) && (delta <= 127U);
      }
    } else {
      hyundai_ordinary_angle_release();
    }
  }
  if (hyundai_ordinary_angle_stock && !hyundai_angle_aol_enabled) {
    // Core whitelisting precedes this hook; relay is its only final denial.
    ordinary_angle_valid = !relay_malfunction;
    if (ordinary_angle_frame) {
      // Recheck accepted-TX freshness before continuity, including a new 110.
      // An expired acquisition releases its counter and measured-angle baseline.
      const bool ordinary_angle_owner = hyundai_ordinary_angle_owner_current();
      ordinary_angle_valid &= (hyundai_ordinary_angle_permission_mask() != 0U) && (msg->bus == 0U) && (GET_LEN(msg) == 32U);
      if (msg->addr == 0x110U) {
        ordinary_angle_valid &= ((msg->data[9] >> 4U) & 0x3U) == 2U;
      }
      if (ordinary_angle_valid) {
        ordinary_angle_valid = hyundai_canfd_get_checksum(msg) == hyundai_common_canfd_compute_checksum(msg);
      }
      if ((msg->addr == 0x110U) && ordinary_angle_valid && hyundai_ordinary_angle_counter_seen) {
        const uint8_t delta = (uint8_t)(msg->data[2] - hyundai_ordinary_angle_counter);
        ordinary_angle_valid = (delta > 0U) && (delta <= 127U);
      }
      if (msg->addr == 0x362U) {
        ordinary_angle_valid &= ordinary_angle_owner;
      }
    }
  }
  if (ordinary_angle_valid && !((hyundai_canfd_ioniq6_long || hyundai_canfd_ccnc_ev_long) && safety_rx_checks_invalid)) {
    tx = (msg->addr == 0x11AU) ? hyundai_daw_tx(msg) : hyundai_canfd_tx_hook_valid(msg);
  }
  if ((hyundai_ordinary_angle_stock && !hyundai_angle_aol_enabled && (msg->addr == 0x110U) && tx) ||
      (independent_command && independent_active && tx)) {
    // Commit only after every shape, CRC, counter and actuation guard accepts.
    hyundai_ordinary_angle_owned = true;
    hyundai_ordinary_angle_accepted_ts = microsecond_timer_get();
    hyundai_ordinary_angle_counter_seen = true;
    hyundai_ordinary_angle_counter = msg->data[2];
  }
  if (hyundai_angle_aol_enabled && independent_command && !tx) {
    hyundai_ordinary_angle_release();
  }
  return tx;
}

static void hyundai_canfd_reset_ioniq6_state(void) {
  hyundai_canfd_ioniq6_long = false;
  aol_ioniq6_long = false;
  hyundai_stock_torque_aol = false;
  aol_ioniq6_enable_reset();
  aol_optional_release_capable = false;
  aol_optional_release_policy = false;
  aol_optional_release_neutral = false;
  aol_optional_release_counter_seen = false;
  aol_optional_release_previous = 0U;
  aol_optional_release_pending = false;
  aol_optional_release_latch = false;

  hyundai_canfd_torque_ev_long = false;
  hyundai_canfd_lkas_damping_100 = false;
  hyundai_canfd_ioniq6_lfa_unpaired = false;
  hyundai_canfd_ioniq6_lfa_ts = 0U;
  hyundai_canfd_gv60_long = false;
  hyundai_canfd_gv60_heartbeat_seen = false;
  hyundai_canfd_gv60_heartbeat_counter = 0U;
  hyundai_canfd_ioniq6_heartbeat_seen = false;
  hyundai_canfd_ioniq6_heartbeat_counter = 0U;
  hyundai_canfd_ioniq6_bsm_low_speed = false;
  hyundai_canfd_ioniq6_bsm_wheel_seen = false;
  hyundai_canfd_ioniq6_bsm_wheel_counter = 0U;
  hyundai_canfd_ioniq6_bsm_wheel_ts = 0U;
  hyundai_canfd_ioniq6_corner_valid = false;
  hyundai_canfd_ioniq6_corner_counter_seen = false;
  hyundai_canfd_ioniq6_corner_counter = 0U;
  hyundai_canfd_ioniq6_corner_bits = 0U;
  hyundai_canfd_ioniq6_corner_ts = 0U;
  hyundai_canfd_ioniq6_lamp_valid = false;
  hyundai_canfd_ioniq6_left_lamp = false;
  hyundai_canfd_ioniq6_right_lamp = false;
  hyundai_canfd_ioniq6_lamp_ts = 0U;
  hyundai_canfd_ioniq6_left_lamp_off_ts = 0U;
  hyundai_canfd_ioniq6_right_lamp_off_ts = 0U;
  hyundai_canfd_ioniq6_bsm_counter_seen = false;
  hyundai_canfd_ioniq6_bsm_counter = 0U;
  hyundai_canfd_ioniq6_bsm_pair_pending = false;
  hyundai_canfd_ioniq6_bsm_pair_counter = 0U;
  hyundai_canfd_ioniq6_bsm_pair_ts = 0U;
}

static bool hyundai_canfd_angle_aol_capacity(safety_config *ret) {
  const bool bounded = (ret->tx_msgs_len > 0) && (ret->tx_msgs_len <= 64);
  if (!bounded) {
    ret->rx_checks_len = 0;
    ret->tx_msgs_len = 0;
  }
  return bounded;
}

static safety_config hyundai_canfd_init_validated(uint16_t supplied_param) {
  uint16_t param = supplied_param;
  const uint16_t HYUNDAI_PARAM_LONGITUDINAL = 4U;
  const uint16_t HYUNDAI_PARAM_CANFD_LKA_STEER_MSG_ALT = 128;
  const uint16_t HYUNDAI_PARAM_CANFD_ALT_BUTTONS = 32;
  const uint16_t HYUNDAI_PARAM_CARNIVAL_ALT_RESUME = 8192U;
  const uint16_t HYUNDAI_PARAM_CANFD_ANGLE = 16384U;
  const uint16_t HYUNDAI_PARAM_ANGLE_OBSERVED_ADAS = 512U;
  const uint16_t HYUNDAI_PARAM_ANGLE_CCNC_MODEL_BANK = 1024U;
  const uint16_t HYUNDAI_PARAM_ANGLE_MODEL_BANK = 8192U;
  const uint16_t HYUNDAI_PARAM_ANGLE_MODEL_MASK = 2048U | 4096U | HYUNDAI_PARAM_ANGLE_MODEL_BANK;
  const uint16_t HYUNDAI_PARAM_ANGLE_TOPOLOGY_MASK = 8U | 16U | 32U | 128U;
  // Bit 13 names Dom's explicit Carnival/GV70 camera damping calibration only
  // under torque LKA + LONG. Other uses of the bit retain their original meaning.
  const uint16_t damping_profile_mask = HYUNDAI_PARAM_CANFD_ANGLE | 16U | HYUNDAI_PARAM_LONGITUDINAL | 8192U;
  hyundai_canfd_lkas_damping_100 = (param & damping_profile_mask) == (16U | HYUNDAI_PARAM_LONGITUDINAL | 8192U);
  if (hyundai_canfd_lkas_damping_100) {
    param &= (uint16_t)~8192U;
  }
  const bool angle_requested = GET_FLAG(param, HYUNDAI_PARAM_CANFD_ANGLE);
  const bool ioniq6_stock_aol_requested = hyundai_canfd_stock_torque_aol_param(param);
#ifdef ALLOW_DEBUG
  const bool torque_long_aol_requested = hyundai_canfd_torque_long_aol_param(param) && ((unsigned int)alternative_experience == 32U);
#else
  const bool torque_long_aol_requested = false;
#endif
  const bool angle_aol_requested = hyundai_canfd_angle_aol_param(param) && ((unsigned int)alternative_experience == 32U);
  const bool pe_requested = (param == HYUNDAI_IONIQ5_PE_STOCK_PARAM) && (((unsigned int)alternative_experience == 0U) || angle_aol_requested);
  const bool ev9_requested = (param == HYUNDAI_EV9_STOCK_PARAM) && (((unsigned int)alternative_experience == 0U) || angle_aol_requested);
  const bool ordinary_angle_requested = pe_requested || ev9_requested;
#ifdef ALLOW_DEBUG
  const bool ev9_long_requested = (param == HYUNDAI_EV9_LONG_PARAM) &&
                                  (((unsigned int)alternative_experience == 0U) || ((unsigned int)alternative_experience == 32U));
  const bool pe_long_requested = (param == HYUNDAI_IONIQ5_PE_LONG_PARAM) &&
    (((unsigned int)alternative_experience == 0U) || ((unsigned int)alternative_experience == 32U));
#else
  const bool ev9_long_requested = false;
  const bool pe_long_requested = false;
#endif
#ifdef ALLOW_DEBUG
  const bool ccnc_long_requested = ev9_long_requested || pe_long_requested;
#else
  const bool ccnc_long_requested = false;
#endif
  const uint16_t angle_strip_mask = HYUNDAI_PARAM_CANFD_ANGLE | HYUNDAI_PARAM_ANGLE_CCNC_MODEL_BANK | HYUNDAI_PARAM_ANGLE_MODEL_MASK |
                                    HYUNDAI_PARAM_ANGLE_OBSERVED_ADAS;
#ifdef ALLOW_DEBUG
  const bool gv60_long_requested = hyundai_canfd_gv60_long_param(param);
  const bool ioniq6_long_requested = GET_FLAG(param, 32768U) && !gv60_long_requested;
#endif
  // Model selector bits are shared with ordinary-mode metadata, but never passed
  // to common init as those meanings when the angle discriminator is present.
#ifdef ALLOW_DEBUG
  uint16_t common_param = (ioniq6_stock_aol_requested || torque_long_aol_requested) ? (param & (uint16_t)~2048U) : param;
  if (angle_requested) {
    common_param = param & (uint16_t)~angle_strip_mask;
  } else if (gv60_long_requested) {
    common_param = param & (uint16_t)~0x9000U;
  } else if (ioniq6_long_requested) {
    const uint16_t ioniq6_strip_mask = 32768U | 2048U;
    common_param = param & (uint16_t)~ioniq6_strip_mask;
  } else {
    // Ordinary safety parameters have no profile-specific bits to strip.
  }
#else
  const uint16_t common_param = angle_requested ? (uint16_t)(param & (uint16_t)~angle_strip_mask) :
                                (uint16_t)(ioniq6_stock_aol_requested ? (param & (uint16_t)~2048U) : param);
#endif

  static const CanMsg HYUNDAI_CANFD_LKA_STEER_MSG_TX_MSGS[] = {
    HYUNDAI_CANFD_LKA_STEER_MSG_COMMON_TX_MSGS(0, 1)
  };
  static const CanMsg HYUNDAI_CANFD_LKA_STEER_MSG_CARNIVAL_TX_MSGS[] = {
    HYUNDAI_CANFD_LKA_STEER_MSG_COMMON_TX_MSGS(0, 1)
    {0x1AA, 1, 16, .check_relay = false},
  };

  static const CanMsg HYUNDAI_CANFD_LKA_STEER_MSG_ALT_TX_MSGS[] = {
    HYUNDAI_CANFD_LKA_STEER_MSG_ALT_COMMON_TX_MSGS(0, 1)
  };
  static const CanMsg HYUNDAI_CANFD_ANGLE_LFA_TX_MSGS[] = {
    HYUNDAI_CANFD_CRUISE_BUTTON_TX_MSGS(2)
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(0)
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(0, false)
    {0x160, 0, 16, .check_relay = false}, // stock camera warning
  };
  static const CanMsg HYUNDAI_CANFD_ANGLE_LFA_ADAS_TX_MSGS[] = {
    HYUNDAI_CANFD_CRUISE_BUTTON_TX_MSGS(2)
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(0)
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(0, false)
    {0xCB, 0, 24, .check_relay = true},  // ADAS_CMD angle control (current DBC: LFA_ALT)
    {0x160, 0, 16, .check_relay = false}, // stock camera warning
  };
  static const CanMsg HYUNDAI_CANFD_ANGLE_LFA_ALT_BUTTONS_TX_MSGS[] = {
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(0)
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(0, false)
    {0x160, 0, 16, .check_relay = false},
  };
  static const CanMsg HYUNDAI_CANFD_ANGLE_LFA_ALT_BUTTONS_ADAS_TX_MSGS[] = {
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(0)
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(0, false)
    {0xCB, 0, 24, .check_relay = true},
    {0x160, 0, 16, .check_relay = false},
  };
  // Stock-SCC CCNC angle SUVs re-emit only the paired camera display sample.
  // These lists deliberately omit legacy 0x160, diagnostic and MDPS traffic.
  static const CanMsg HYUNDAI_CANFD_ANGLE_CCNC_LFA_TX_MSGS[] = {
    HYUNDAI_CANFD_CRUISE_BUTTON_TX_MSGS(2)
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(0)
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(0, false)
    {0x161, 0, 32, .check_relay = true},
    {0x162, 0, 32, .check_relay = true},
  };
  static const CanMsg HYUNDAI_CANFD_ANGLE_CCNC_LFA_ADAS_TX_MSGS[] = {
    HYUNDAI_CANFD_CRUISE_BUTTON_TX_MSGS(2)
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(0)
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(0, false)
    {0xCB, 0, 24, .check_relay = true},
    {0x161, 0, 32, .check_relay = true},
    {0x162, 0, 32, .check_relay = true},
  };
  static const CanMsg HYUNDAI_CANFD_ANGLE_CCNC_LFA_ALT_BUTTONS_TX_MSGS[] = {
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(0)
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(0, false)
    {0x161, 0, 32, .check_relay = true},
    {0x162, 0, 32, .check_relay = true},
  };
  static const CanMsg HYUNDAI_CANFD_ANGLE_CCNC_LFA_ALT_BUTTONS_ADAS_TX_MSGS[] = {
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(0)
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(0, false)
    {0xCB, 0, 24, .check_relay = true},
    {0x161, 0, 32, .check_relay = true},
    {0x162, 0, 32, .check_relay = true},
  };
  static const CanMsg HYUNDAI_CANFD_LKA_STEER_MSG_ALT_CARNIVAL_TX_MSGS[] = {
    HYUNDAI_CANFD_LKA_STEER_MSG_ALT_COMMON_TX_MSGS(0, 1)
    {0x1AA, 1, 16, .check_relay = false},
  };

#define HYUNDAI_CANFD_LKA_STEER_MSG_LONG_TX_MSGS_BODY \
    HYUNDAI_CANFD_LKA_STEER_MSG_COMMON_TX_MSGS(0, 1) \
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(1) \
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(1, true) \
    {0x51,  0, 32, .check_relay = false}, /* ADRV_0x51 */ \
    {0x730, 1,  8, .check_relay = false}, /* tester present for ADAS ECU disable */ \
    {0x160, 1, 16, .check_relay = false}, /* ADRV_0x160 */ \
    {0x1EA, 1, 32, .check_relay = false}, /* ADRV_0x1ea */ \
    {0x200, 1,  8, .check_relay = false}, /* ADRV_0x200 */ \
    {0x345, 1,  8, .check_relay = false}, /* ADRV_0x345 */ \
    {0x1DA, 1, 32, .check_relay = false}, /* ADRV_0x1da */
  static const CanMsg HYUNDAI_CANFD_LKA_STEER_MSG_LONG_TX_MSGS[] = {
    HYUNDAI_CANFD_LKA_STEER_MSG_LONG_TX_MSGS_BODY
  };
#define HYUNDAI_CANFD_LKA_STEER_MSG_ALT_LONG_TX_MSGS_BODY \
    HYUNDAI_CANFD_LKA_STEER_MSG_ALT_COMMON_TX_MSGS(0, 1) \
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(1) \
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(1, true) \
    {0x51,  0, 32, .check_relay = false}, /* ADRV_0x51 */ \
    {0x730, 1,  8, .check_relay = false}, /* tester present for ADAS ECU disable */ \
    {0x160, 1, 16, .check_relay = false}, /* ADRV_0x160 */ \
    {0x1EA, 1, 32, .check_relay = false}, /* ADRV_0x1ea */ \
    {0x200, 1,  8, .check_relay = false}, /* ADRV_0x200 */ \
    {0x345, 1,  8, .check_relay = false}, /* ADRV_0x345 */ \
    {0x1DA, 1, 32, .check_relay = false}, /* ADRV_0x1da */
  static const CanMsg HYUNDAI_CANFD_LKA_STEER_MSG_ALT_LONG_TX_MSGS[] = {
    HYUNDAI_CANFD_LKA_STEER_MSG_ALT_LONG_TX_MSGS_BODY
  };

  static const CanMsg HYUNDAI_CANFD_LFA_STEERING_TX_MSGS[] = {
    HYUNDAI_CANFD_CRUISE_BUTTON_TX_MSGS(2)
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(0)
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(0, false)
  };

  // ADRV_0x160 is checked for radar liveness
  static const CanMsg HYUNDAI_CANFD_LFA_STEERING_LONG_TX_MSGS[] = {
    HYUNDAI_CANFD_CRUISE_BUTTON_TX_MSGS(2)
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(0)
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(0, true)
    {0x160, 0, 16, .check_relay = true},  // ADRV_0x160
    {0x7D0, 0, 8, .check_relay = false},  // tester present for radar ECU disable
  };

  // ADRV_0x160 is checked for relay malfunction
#define HYUNDAI_CANFD_LFA_STEERING_CAMERA_SCC_TX_MSGS(longitudinal) \
    HYUNDAI_CANFD_CRUISE_BUTTON_TX_MSGS(2) \
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(0) \
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(0, (longitudinal)) \
    {0x160, 0, 16, .check_relay = (longitudinal)}, /* ADRV_0x160 */ \

// Ordinary CCNC without an ADAS steering ECU substitutes its two stock camera
// display frames for the older 0x160 warning frame. No camera/MDPS support
// traffic or LKA/angle-steering actuation is added by this topology.
#define HYUNDAI_CANFD_CCNC_CAMERA_SCC_TX_MSGS(longitudinal) \
    HYUNDAI_CANFD_CRUISE_BUTTON_TX_MSGS(2) \
    HYUNDAI_CANFD_LFA_STEERING_COMMON_TX_MSGS(0) \
    HYUNDAI_CANFD_SCC_CONTROL_COMMON_TX_MSGS(0, (longitudinal)) \
    {0x161, 0, 32, .check_relay = true}, /* CCNC_0x161 */ \
    {0x162, 0, 32, .check_relay = true}, /* CCNC_0x162 */ \

  hyundai_common_init(common_param);

  hyundai_canfd_alt_buttons = GET_FLAG(param, HYUNDAI_PARAM_CANFD_ALT_BUTTONS);
  hyundai_canfd_carnival_alt_resume = !angle_requested && GET_FLAG(common_param, HYUNDAI_PARAM_CARNIVAL_ALT_RESUME) &&
                                     hyundai_canfd_alt_buttons && !GET_FLAG(common_param, HYUNDAI_PARAM_LONGITUDINAL) && !hyundai_longitudinal;
  hyundai_canfd_carnival_source_valid = false;
  hyundai_canfd_carnival_previous_valid = false;
  hyundai_canfd_carnival_counter_seen = false;
  hyundai_canfd_carnival_counter_consumed = false;
  hyundai_canfd_carnival_source_ts = 0U;
  hyundai_canfd_lka_steer_msg_alt = GET_FLAG(param, HYUNDAI_PARAM_CANFD_LKA_STEER_MSG_ALT);
  hyundai_canfd_angle_steering = false;
  hyundai_canfd_angle_observed_adas = false;
  hyundai_canfd_angle_model = 0U;
#ifdef ALLOW_DEBUG
  hyundai_canfd_ioniq6_long = ioniq6_long_requested;
  hyundai_canfd_torque_ev_long = (param == 0x0015U) || (param == 0x0095U) || (torque_long_aol_requested && GET_FLAG(param, 16U)) || gv60_long_requested;
#endif
  hyundai_torque_aol_cancel_preserves_lateral = ioniq6_stock_aol_requested || torque_long_aol_requested ||
                                               (param == 0x8815U) || (param == 0x8895U);
  aol_optional_release_capable = torque_long_aol_requested;
  hyundai_stock_torque_aol = ioniq6_stock_aol_requested;
  aol_ioniq6_long = ioniq6_stock_aol_requested || torque_long_aol_requested || (param == 0x8815U) || (param == 0x8895U);
  if (aol_ioniq6_long) {
    static const AolSafetyPolicy aol_ioniq6_policy = {
      .reset = aol_ioniq6_reset,
      .host_request = aol_ioniq6_host_request,
      .request_mask = aol_ioniq6_request_mask,
      .permission_mask = aol_ioniq6_permission_mask,
      .rx_invalid = aol_ioniq6_rx_invalid,
    };
    aol_policy = &aol_ioniq6_policy;
  }
  const uint16_t HYUNDAI_PARAM_CCNC = 1024U;
  const bool hyundai_canfd_ccnc = !angle_requested && GET_FLAG(param, HYUNDAI_PARAM_CCNC) && hyundai_camera_scc && !hyundai_canfd_lka_steer_msg;

  safety_config ret = {0};
  if (angle_requested) {
    // The unbanked seven models retain their exact 60 raw profiles. Angle-only
    // bit 10 selects CCNC geometry 0/1; Sportage permits documented camera
    // topology only. Observed 0xCB applies only to camera-LFA in either bank.
    const uint16_t topology = param & HYUNDAI_PARAM_ANGLE_TOPOLOGY_MASK;
    const bool topology_valid = (topology == 16U) || (topology == (16U | 128U)) ||
                                (topology == 8U) || (topology == (8U | 32U));
    const uint16_t angle_allowed_mask = HYUNDAI_PARAM_CANFD_ANGLE | HYUNDAI_PARAM_ANGLE_CCNC_MODEL_BANK | HYUNDAI_PARAM_ANGLE_MODEL_MASK |
                                        HYUNDAI_PARAM_ANGLE_OBSERVED_ADAS |
                                        HYUNDAI_PARAM_ANGLE_TOPOLOGY_MASK | 1U | 2U;
    const uint8_t model = (uint8_t)((param & HYUNDAI_PARAM_ANGLE_MODEL_MASK) >> 11U);
    const bool ccnc_bank = GET_FLAG(param, HYUNDAI_PARAM_ANGLE_CCNC_MODEL_BANK);
    const bool ev6_stock_profile = (param == 0x7809U) || (param == 0x7829U);
    const bool model_ev = ordinary_angle_requested || (!ccnc_bank && ((model == 1U) || (model == 3U) || ev6_stock_profile));
    const bool model_hybrid_capable = (!ccnc_bank && (model >= 4U) && (model <= 6U)) || (ccnc_bank && (model == 0U));
    const bool ev_flag = GET_FLAG(param, 1U);
    const bool hybrid_flag = GET_FLAG(param, 2U);
    const bool observed_adas = GET_FLAG(param, HYUNDAI_PARAM_ANGLE_OBSERVED_ADAS);
    const uint16_t invalid_angle_bits = (uint16_t)~angle_allowed_mask;
    const bool invalid_ccnc_model = !ordinary_angle_requested && ((model > 1U) || ((model == 1U) && ((topology & 8U) == 0U)));
    const bool invalid_model = (ccnc_bank && invalid_ccnc_model) || (!ccnc_bank && (model == 7U) && !ev6_stock_profile);
    const bool valid_angle = topology_valid && ((param & invalid_angle_bits) == 0U) && !invalid_model &&
                             (ev_flag == model_ev) && (!hybrid_flag || model_hybrid_capable) &&
                             (!observed_adas || ((topology & 8U) != 0U));
    if (valid_angle || ccnc_long_requested) {
    hyundai_canfd_angle_steering = true;
    hyundai_canfd_angle_observed_adas = observed_adas;
    hyundai_canfd_angle_model = (pe_requested || pe_long_requested) ? 10U : (ev9_requested || ev9_long_requested) ? 11U : ccnc_bank ? (uint8_t)(7U + model) : (ev6_stock_profile ? 9U : model);
    static RxCheck hyundai_canfd_angle_lka_rx_checks[] = {
      HYUNDAI_CANFD_STD_BUTTONS_RX_CHECKS(1)
      HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
    };
    static RxCheck hyundai_canfd_banked_lka_ice_rx_checks[] = {
      HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(1, 0x100)
      HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
    };
    static RxCheck hyundai_canfd_banked_lka_hybrid_rx_checks[] = {
      HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(1, 0x105)
      HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
    };
    static RxCheck hyundai_canfd_banked_lfa_ice_rx_checks[] = {
      HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(0, 0x100)
      HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
    };
    static RxCheck hyundai_canfd_banked_lfa_hybrid_rx_checks[] = {
      HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(0, 0x105)
      HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
    };
    static RxCheck hyundai_canfd_banked_lfa_alt_ice_rx_checks[] = {
      HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(0, 0x100)
      HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
    };
    static RxCheck hyundai_canfd_banked_lfa_alt_hybrid_rx_checks[] = {
      HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(0, 0x105)
      HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
    };
    if (topology == 16U) {
      ret = BUILD_SAFETY_CFG(hyundai_canfd_angle_lka_rx_checks, HYUNDAI_CANFD_LKA_STEER_MSG_TX_MSGS);
    } else if (topology == (16U | 128U)) {
      ret = BUILD_SAFETY_CFG(hyundai_canfd_angle_lka_rx_checks, HYUNDAI_CANFD_LKA_STEER_MSG_ALT_TX_MSGS);
    } else if (topology == 8U) {
      static RxCheck hyundai_canfd_angle_lfa_rx_checks[] = {
        HYUNDAI_CANFD_STD_BUTTONS_RX_CHECKS(0)
        HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
      };
      if (ccnc_bank) {
        ret = observed_adas ? BUILD_SAFETY_CFG(hyundai_canfd_angle_lfa_rx_checks, HYUNDAI_CANFD_ANGLE_CCNC_LFA_ADAS_TX_MSGS) :
                              BUILD_SAFETY_CFG(hyundai_canfd_angle_lfa_rx_checks, HYUNDAI_CANFD_ANGLE_CCNC_LFA_TX_MSGS);
      } else {
        ret = observed_adas ? BUILD_SAFETY_CFG(hyundai_canfd_angle_lfa_rx_checks, HYUNDAI_CANFD_ANGLE_LFA_ADAS_TX_MSGS) :
                              BUILD_SAFETY_CFG(hyundai_canfd_angle_lfa_rx_checks, HYUNDAI_CANFD_ANGLE_LFA_TX_MSGS);
      }
    } else {
      static RxCheck hyundai_canfd_angle_lfa_alt_rx_checks[] = {
        HYUNDAI_CANFD_ALT_BUTTONS_RX_CHECKS(0)
        HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
      };
      if (ccnc_bank) {
        ret = observed_adas ? BUILD_SAFETY_CFG(hyundai_canfd_angle_lfa_alt_rx_checks, HYUNDAI_CANFD_ANGLE_CCNC_LFA_ALT_BUTTONS_ADAS_TX_MSGS) :
                              BUILD_SAFETY_CFG(hyundai_canfd_angle_lfa_alt_rx_checks, HYUNDAI_CANFD_ANGLE_CCNC_LFA_ALT_BUTTONS_TX_MSGS);
      } else {
        ret = observed_adas ? BUILD_SAFETY_CFG(hyundai_canfd_angle_lfa_alt_rx_checks, HYUNDAI_CANFD_ANGLE_LFA_ALT_BUTTONS_ADAS_TX_MSGS) :
                              BUILD_SAFETY_CFG(hyundai_canfd_angle_lfa_alt_rx_checks, HYUNDAI_CANFD_ANGLE_LFA_ALT_BUTTONS_TX_MSGS);
      }
    }
    if (!ordinary_angle_requested && (model_hybrid_capable || ccnc_bank)) {
      if ((topology == 16U) || (topology == (16U | 128U))) {
        if (hybrid_flag) {
          SET_RX_CHECKS(hyundai_canfd_banked_lka_hybrid_rx_checks, ret);
        } else {
          SET_RX_CHECKS(hyundai_canfd_banked_lka_ice_rx_checks, ret);
        }
      } else if (topology == 8U) {
        if (hybrid_flag) {
          SET_RX_CHECKS(hyundai_canfd_banked_lfa_hybrid_rx_checks, ret);
        } else {
          SET_RX_CHECKS(hyundai_canfd_banked_lfa_ice_rx_checks, ret);
        }
      } else {
        if (hybrid_flag) {
          SET_RX_CHECKS(hyundai_canfd_banked_lfa_alt_hybrid_rx_checks, ret);
        } else {
          SET_RX_CHECKS(hyundai_canfd_banked_lfa_alt_ice_rx_checks, ret);
        }
      }
    }
    if (ccnc_long_requested) {
      static RxCheck ev9_long_rx_checks[] = {
        HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(1, 0x35)
      };
      static const CanMsg ev9_long_tx_msgs[] = {
        {0x110, 0, 32, .check_relay = true, .disable_static_blocking = true},
        {0x362, 0, 32, .check_relay = true, .disable_static_blocking = true},
        {0x1CF, 1, 8, .check_relay = false},
        {0xCB, 1, 24, .check_relay = false},
        {0x12A, 1, 16, .check_relay = false},
        {0x1E0, 1, 16, .check_relay = false},
        {0x1A0, 1, 32, .check_relay = true},
        {0x1BA, 1, 24, .check_relay = false},
        {0x1E5, 1, 16, .check_relay = false},
        {0x100, 0, 24, .check_relay = false},
        {0x730, 1, 8, .check_relay = false},
        {0x160, 1, 16, .check_relay = false},
        {0x161, 1, 32, .check_relay = false},
        {0x162, 1, 32, .check_relay = false},
        {0x1EA, 1, 32, .check_relay = false},
        {0x200, 1, 8, .check_relay = false},
        {0x345, 1, 8, .check_relay = false},
        {0x38C, 1, 32, .check_relay = false},
        {0x1DA, 1, 32, .check_relay = false},
      };
      ret = BUILD_SAFETY_CFG(ev9_long_rx_checks, ev9_long_tx_msgs);
      static const CanMsg pe_long_tx_msgs[] = {
        {0x110, 0, 32, .check_relay = true, .disable_static_blocking = true},
        {0x362, 0, 32, .check_relay = true, .disable_static_blocking = true},
        {0x1CF, 1, 8, .check_relay = false},
        {0xCB, 1, 24, .check_relay = true, .check_relay_immediately = true},
        {0x12A, 1, 16, .check_relay = true, .check_relay_immediately = true},
        {0x1E0, 1, 16, .check_relay = true, .check_relay_immediately = true},
        {0x1A0, 1, 32, .check_relay = true, .check_relay_immediately = true},
        {0x1BA, 1, 24, .check_relay = true, .check_relay_immediately = true},
        {0x1E5, 1, 16, .check_relay = true, .check_relay_immediately = true},
        {0x100, 0, 24, .check_relay = true, .check_relay_immediately = true},
        {0x730, 1, 8, .check_relay = false},
        {0x160, 1, 16, .check_relay = true, .check_relay_immediately = true},
        {0x161, 1, 32, .check_relay = true, .check_relay_immediately = true},
        {0x162, 1, 32, .check_relay = true, .check_relay_immediately = true},
        {0x1EA, 1, 32, .check_relay = true, .check_relay_immediately = true},
        {0x200, 1, 8, .check_relay = true, .check_relay_immediately = true},
        {0x345, 1, 8, .check_relay = true, .check_relay_immediately = true},
        {0x38C, 1, 32, .check_relay = true, .check_relay_immediately = true},
        {0x1DA, 1, 32, .check_relay = true, .check_relay_immediately = true},
      };
      if (pe_long_requested) {
        SET_TX_MSGS(pe_long_tx_msgs, ret);
      }
      hyundai_canfd_ccnc_ev_long = true;
      hyundai_canfd_pe_long = pe_long_requested;
    }
    if (ordinary_angle_requested) {
      static RxCheck ordinary_angle_rx_checks[] = {
        HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(1, 0x35)
        HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
      };
      static const CanMsg ordinary_angle_tx_msgs[] = {
        {0x110, 0, 32, .check_relay = true, .disable_static_blocking = true},
        {0x362, 0, 32, .check_relay = true, .disable_static_blocking = true},
        {0x1CF, 1, 8, .check_relay = false},
      };
      ret = BUILD_SAFETY_CFG(ordinary_angle_rx_checks, ordinary_angle_tx_msgs);
      hyundai_ordinary_angle_stock = true;
      static const AolSafetyPolicy ordinary_angle_policy = {
        .reset = hyundai_ordinary_angle_reset,
        .host_request = hyundai_ordinary_angle_host_request,
        .request_mask = hyundai_ordinary_angle_request_mask,
        .permission_mask = hyundai_ordinary_angle_permission_mask,
        .rx_invalid = hyundai_ordinary_angle_release,
      };
      aol_policy = &ordinary_angle_policy;
    }
    }
  } else
#ifdef ALLOW_DEBUG
  if (hyundai_canfd_ioniq6_long) {
    // Exact HDA-II EV source and standard-button topology. Stock SCC 0x1A0
    // must already be silent from the prepublication UDS transaction.
    static RxCheck ioniq6_long_rx_checks[] = {
      HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(1, 0x35)
    };
    static const CanMsg ioniq6_long_lkas_tx_msgs[] = {
      {0x50, 0, 16, .check_relay = true},
      {0x2A4, 0, 24, .check_relay = true},
      {0x12A, 1, 16, .check_relay = false},
      {0x1E0, 1, 16, .check_relay = false},
      {0x1A0, 1, 32, .check_relay = true, .check_relay_immediately = true},
      {0x100, 0, 24, .check_relay = true, .check_relay_immediately = true},
      {0x1BA, 1, 24, .check_relay = true, .check_relay_immediately = true},
      {0x1E5, 1, 16, .check_relay = true, .check_relay_immediately = true},
      {0x51, 0, 32, .check_relay = false},
      {0x730, 1, 8, .check_relay = false},
      {0x160, 1, 16, .check_relay = false},
      {0x1EA, 1, 32, .check_relay = false},
      {0x200, 1, 8, .check_relay = false},
      {0x345, 1, 8, .check_relay = false},
      {0x1DA, 1, 32, .check_relay = false},
    };
    static const CanMsg ioniq6_long_lkas_alt_tx_msgs[] = {
      {0x110, 0, 32, .check_relay = true, .disable_static_blocking = true},
      {0x362, 0, 32, .check_relay = true, .disable_static_blocking = true},
      {0x12A, 1, 16, .check_relay = false},
      {0x1E0, 1, 16, .check_relay = false},
      {0x1A0, 1, 32, .check_relay = true, .check_relay_immediately = true},
      {0x100, 0, 24, .check_relay = true, .check_relay_immediately = true},
      {0x1BA, 1, 24, .check_relay = true, .check_relay_immediately = true},
      {0x1E5, 1, 16, .check_relay = true, .check_relay_immediately = true},
      {0x51, 0, 32, .check_relay = false},
      {0x730, 1, 8, .check_relay = false},
      {0x160, 1, 16, .check_relay = false},
      {0x1EA, 1, 32, .check_relay = false},
      {0x200, 1, 8, .check_relay = false},
      {0x345, 1, 8, .check_relay = false},
      {0x1DA, 1, 32, .check_relay = false},
    };
    ret = ((param == 0x8095U) || (param == 0x8895U)) ? BUILD_SAFETY_CFG(ioniq6_long_rx_checks, ioniq6_long_lkas_alt_tx_msgs) :
                                                      BUILD_SAFETY_CFG(ioniq6_long_rx_checks, ioniq6_long_lkas_tx_msgs);
  } else
#endif
  if (hyundai_longitudinal) {
    if (hyundai_canfd_lka_steer_msg) {
      static const RxCheck hyundai_canfd_lka_steer_msg_long_rx_checks[] = {
        HYUNDAI_CANFD_STD_BUTTONS_RX_CHECKS(1)
      };

      ret = BUILD_SAFETY_CFG(hyundai_canfd_lka_steer_msg_long_rx_checks, HYUNDAI_CANFD_LKA_STEER_MSG_LONG_TX_MSGS);
      if (hyundai_canfd_lka_steer_msg_alt) {
        SET_TX_MSGS(HYUNDAI_CANFD_LKA_STEER_MSG_ALT_LONG_TX_MSGS, ret);
      }
    } else {
      // Longitudinal checks for LFA steering
      static const RxCheck hyundai_canfd_long_rx_checks[] = {
        HYUNDAI_CANFD_STD_BUTTONS_RX_CHECKS(0)
      };

      static const RxCheck hyundai_canfd_alt_buttons_long_rx_checks[] = {
        HYUNDAI_CANFD_ALT_BUTTONS_RX_CHECKS(0)
      };

      static const CanMsg hyundai_canfd_lfa_steering_camera_scc_tx_msgs[] = {
        HYUNDAI_CANFD_LFA_STEERING_CAMERA_SCC_TX_MSGS(true)
      };
      static CanMsg hyundai_canfd_ccnc_camera_scc_tx_msgs[] = {
        HYUNDAI_CANFD_CCNC_CAMERA_SCC_TX_MSGS(true)
      };

      if (hyundai_canfd_alt_buttons) {
        SET_RX_CHECKS(hyundai_canfd_alt_buttons_long_rx_checks, ret);
      } else {
        SET_RX_CHECKS(hyundai_canfd_long_rx_checks, ret);
      }

      if (hyundai_camera_scc) {
        if (hyundai_canfd_ccnc) {
          SET_TX_MSGS(hyundai_canfd_ccnc_camera_scc_tx_msgs, ret);
        } else {
          SET_TX_MSGS(hyundai_canfd_lfa_steering_camera_scc_tx_msgs, ret);
        }
      } else {
        SET_TX_MSGS(HYUNDAI_CANFD_LFA_STEERING_LONG_TX_MSGS, ret);
      }
    }
  } else {
    if (hyundai_canfd_lka_steer_msg) {
      // *** LKA steering checks ***
      // E-CAN is on bus 1, SCC messages are sent on cars with ADRV ECU.
      // Does not use the alt buttons message
      static const RxCheck hyundai_canfd_lka_steer_msg_rx_checks[] = {
        HYUNDAI_CANFD_STD_BUTTONS_RX_CHECKS(1)
        HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
      };
      static RxCheck hyundai_canfd_lka_steer_msg_carnival_rx_checks[] = {
        HYUNDAI_CANFD_ALT_BUTTONS_RX_CHECKS(1)
        HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
      };

      if (hyundai_canfd_carnival_alt_resume) {
        SET_RX_CHECKS(hyundai_canfd_lka_steer_msg_carnival_rx_checks, ret);
      } else {
        SET_RX_CHECKS(hyundai_canfd_lka_steer_msg_rx_checks, ret);
      }
      if (hyundai_canfd_lka_steer_msg_alt) {
        if (hyundai_canfd_carnival_alt_resume) {
          SET_TX_MSGS(HYUNDAI_CANFD_LKA_STEER_MSG_ALT_CARNIVAL_TX_MSGS, ret);
        } else {
          SET_TX_MSGS(HYUNDAI_CANFD_LKA_STEER_MSG_ALT_TX_MSGS, ret);
        }
      } else {
        if (hyundai_canfd_carnival_alt_resume) {
          SET_TX_MSGS(HYUNDAI_CANFD_LKA_STEER_MSG_CARNIVAL_TX_MSGS, ret);
        } else {
          SET_TX_MSGS(HYUNDAI_CANFD_LKA_STEER_MSG_TX_MSGS, ret);
        }
      }
    } else if (!hyundai_camera_scc) {
      // Radar sends SCC messages on these cars instead of camera
      static const RxCheck hyundai_canfd_radar_scc_rx_checks[] = {
        HYUNDAI_CANFD_STD_BUTTONS_RX_CHECKS(0)
        HYUNDAI_CANFD_SCC_ADDR_CHECK(0)
      };

      static const RxCheck hyundai_canfd_alt_buttons_radar_scc_rx_checks[] = {
        HYUNDAI_CANFD_ALT_BUTTONS_RX_CHECKS(0)
        HYUNDAI_CANFD_SCC_ADDR_CHECK(0)
      };

      SET_TX_MSGS(HYUNDAI_CANFD_LFA_STEERING_TX_MSGS, ret);

      if (hyundai_canfd_alt_buttons) {
        SET_RX_CHECKS(hyundai_canfd_alt_buttons_radar_scc_rx_checks, ret);
      } else {
        SET_RX_CHECKS(hyundai_canfd_radar_scc_rx_checks, ret);
      }
    } else {
      // *** LFA steering checks ***
      // Camera sends SCC messages on LFA steering cars.
      // Both button messages exist on some platforms, so we ensure we track the correct one using flag
      static const RxCheck hyundai_canfd_rx_checks[] = {
        HYUNDAI_CANFD_STD_BUTTONS_RX_CHECKS(0)
        HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
      };

      static const RxCheck hyundai_canfd_alt_buttons_rx_checks[] = {
        HYUNDAI_CANFD_ALT_BUTTONS_RX_CHECKS(0)
        HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
      };

      static const CanMsg hyundai_canfd_lfa_steering_camera_scc_tx_msgs[] = {
        HYUNDAI_CANFD_LFA_STEERING_CAMERA_SCC_TX_MSGS(false)
      };
      static CanMsg hyundai_canfd_ccnc_camera_scc_tx_msgs[] = {
        HYUNDAI_CANFD_CCNC_CAMERA_SCC_TX_MSGS(false)
      };
      static CanMsg hyundai_canfd_ccnc_carnival_camera_scc_tx_msgs[] = {
        HYUNDAI_CANFD_CCNC_CAMERA_SCC_TX_MSGS(false)
        {0x1AA, 2, 16, .check_relay = false},
      };

      if (hyundai_canfd_ccnc) {
        if (hyundai_canfd_carnival_alt_resume) {
          SET_TX_MSGS(hyundai_canfd_ccnc_carnival_camera_scc_tx_msgs, ret);
        } else {
          SET_TX_MSGS(hyundai_canfd_ccnc_camera_scc_tx_msgs, ret);
        }
      } else {
        SET_TX_MSGS(hyundai_canfd_lfa_steering_camera_scc_tx_msgs, ret);
      }

      if (hyundai_canfd_alt_buttons) {
        SET_RX_CHECKS(hyundai_canfd_alt_buttons_rx_checks, ret);
      } else {
        SET_RX_CHECKS(hyundai_canfd_rx_checks, ret);
      }
    }
  }

  // Only one banked profile is active; keep its mutable RX status in RAM,
  // while the alternative configuration templates remain in flash.
  static RxCheck banked_rx_workspace[7];
  if (ioniq6_stock_aol_requested && (param != 0x0811U) && (param != 0x0891U)) {
    const RxCheck *selected = NULL;
    if (hyundai_canfd_lka_steer_msg) {
      if (hyundai_hybrid_gas_signal) {
        static const RxCheck torque_hda2_hybrid_alt[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(1, 0x105)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
        };
        _Static_assert((sizeof(torque_hda2_hybrid_alt) / sizeof(torque_hda2_hybrid_alt[0])) == 6U, "Banked RX template size");
        static const RxCheck torque_hda2_hybrid_std[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(1, 0x105)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
        };
        _Static_assert((sizeof(torque_hda2_hybrid_std) / sizeof(torque_hda2_hybrid_std[0])) == 6U, "Banked RX template size");
        selected = hyundai_canfd_alt_buttons ? torque_hda2_hybrid_alt : torque_hda2_hybrid_std;
      } else {
        static const RxCheck torque_hda2_ice_alt[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(1, 0x100)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
        };
        _Static_assert((sizeof(torque_hda2_ice_alt) / sizeof(torque_hda2_ice_alt[0])) == 6U, "Banked RX template size");
        static const RxCheck torque_hda2_ice_std[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(1, 0x100)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
        };
        _Static_assert((sizeof(torque_hda2_ice_std) / sizeof(torque_hda2_ice_std[0])) == 6U, "Banked RX template size");
        selected = hyundai_canfd_alt_buttons ? torque_hda2_ice_alt : torque_hda2_ice_std;
      }
    } else if (hyundai_camera_scc) {
      if (hyundai_ev_gas_signal) {
        static const RxCheck torque_camera_ev_alt[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(0, 0x35)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
        };
        _Static_assert((sizeof(torque_camera_ev_alt) / sizeof(torque_camera_ev_alt[0])) == 6U, "Banked RX template size");
        static const RxCheck torque_camera_ev_std[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(0, 0x35)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
        };
        _Static_assert((sizeof(torque_camera_ev_std) / sizeof(torque_camera_ev_std[0])) == 6U, "Banked RX template size");
        selected = hyundai_canfd_alt_buttons ? torque_camera_ev_alt : torque_camera_ev_std;
      } else if (hyundai_hybrid_gas_signal) {
        static const RxCheck torque_camera_hybrid_alt[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(0, 0x105)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
        };
        _Static_assert((sizeof(torque_camera_hybrid_alt) / sizeof(torque_camera_hybrid_alt[0])) == 6U, "Banked RX template size");
        static const RxCheck torque_camera_hybrid_std[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(0, 0x105)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
        };
        _Static_assert((sizeof(torque_camera_hybrid_std) / sizeof(torque_camera_hybrid_std[0])) == 6U, "Banked RX template size");
        selected = hyundai_canfd_alt_buttons ? torque_camera_hybrid_alt : torque_camera_hybrid_std;
      } else {
        static const RxCheck torque_camera_ice_alt[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(0, 0x100)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
        };
        _Static_assert((sizeof(torque_camera_ice_alt) / sizeof(torque_camera_ice_alt[0])) == 6U, "Banked RX template size");
        static const RxCheck torque_camera_ice_std[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(0, 0x100)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
        };
        _Static_assert((sizeof(torque_camera_ice_std) / sizeof(torque_camera_ice_std[0])) == 6U, "Banked RX template size");
        selected = hyundai_canfd_alt_buttons ? torque_camera_ice_alt : torque_camera_ice_std;
      }
    } else {
      if (hyundai_ev_gas_signal) {
        static const RxCheck torque_radar_ev_alt[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(0, 0x35)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(0)
        };
        _Static_assert((sizeof(torque_radar_ev_alt) / sizeof(torque_radar_ev_alt[0])) == 6U, "Banked RX template size");
        static const RxCheck torque_radar_ev_std[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(0, 0x35)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(0)
        };
        _Static_assert((sizeof(torque_radar_ev_std) / sizeof(torque_radar_ev_std[0])) == 6U, "Banked RX template size");
        selected = hyundai_canfd_alt_buttons ? torque_radar_ev_alt : torque_radar_ev_std;
      } else if (hyundai_hybrid_gas_signal) {
        static const RxCheck torque_radar_hybrid_alt[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(0, 0x105)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(0)
        };
        _Static_assert((sizeof(torque_radar_hybrid_alt) / sizeof(torque_radar_hybrid_alt[0])) == 6U, "Banked RX template size");
        static const RxCheck torque_radar_hybrid_std[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(0, 0x105)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(0)
        };
        _Static_assert((sizeof(torque_radar_hybrid_std) / sizeof(torque_radar_hybrid_std[0])) == 6U, "Banked RX template size");
        selected = hyundai_canfd_alt_buttons ? torque_radar_hybrid_alt : torque_radar_hybrid_std;
      } else {
        static const RxCheck torque_radar_ice_alt[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(0, 0x100)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(0)
        };
        _Static_assert((sizeof(torque_radar_ice_alt) / sizeof(torque_radar_ice_alt[0])) == 6U, "Banked RX template size");
        static const RxCheck torque_radar_ice_std[] = {
          HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(0, 0x100)
          HYUNDAI_CANFD_SCC_ADDR_CHECK(0)
        };
        _Static_assert((sizeof(torque_radar_ice_std) / sizeof(torque_radar_ice_std[0])) == 6U, "Banked RX template size");
        selected = hyundai_canfd_alt_buttons ? torque_radar_ice_alt : torque_radar_ice_std;
      }
    }
    ret.rx_checks_len = 6;
    for (int i = 0; i < ret.rx_checks_len; i++) {
      banked_rx_workspace[i] = selected[i];
    }
    ret.rx_checks = banked_rx_workspace;
  }
  if (angle_aol_requested && hyundai_canfd_angle_steering && !hyundai_longitudinal) {
    static CanMsg independent_tx_msgs[64];
    const bool bounded = hyundai_canfd_angle_aol_capacity(&ret);
    if (bounded) {
      const RxCheck *selected = NULL;
      if (hyundai_canfd_lka_steer_msg) {
        if (hyundai_ev_gas_signal) {
          static const RxCheck angle_hda2_ev_alt[] = {
            HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(1, 0x35)
            HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
          };
          _Static_assert((sizeof(angle_hda2_ev_alt) / sizeof(angle_hda2_ev_alt[0])) == 6U, "Banked RX template size");
          static const RxCheck angle_hda2_ev_std[] = {
            HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(1, 0x35)
            HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
          };
          _Static_assert((sizeof(angle_hda2_ev_std) / sizeof(angle_hda2_ev_std[0])) == 6U, "Banked RX template size");
          selected = hyundai_canfd_alt_buttons ? angle_hda2_ev_alt : angle_hda2_ev_std;
        }
        else if (hyundai_hybrid_gas_signal) {
          static const RxCheck angle_hda2_hybrid_alt[] = {
            HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(1, 0x105)
            HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
            {.msg = {{0x70, 1, 32, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}, {0}, {0}}},
          };
          _Static_assert((sizeof(angle_hda2_hybrid_alt) / sizeof(angle_hda2_hybrid_alt[0])) == 7U, "Banked RX template size");
          static const RxCheck angle_hda2_hybrid_std[] = {
            HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(1, 0x105)
            HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
            {.msg = {{0x70, 1, 32, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}, {0}, {0}}},
          };
          _Static_assert((sizeof(angle_hda2_hybrid_std) / sizeof(angle_hda2_hybrid_std[0])) == 7U, "Banked RX template size");
          selected = hyundai_canfd_alt_buttons ? angle_hda2_hybrid_alt : angle_hda2_hybrid_std;
        }
        else {
          static const RxCheck angle_hda2_ice_alt[] = {
            HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(1, 0x100)
            HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
            {.msg = {{0x70, 1, 32, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}, {0}, {0}}},
          };
          _Static_assert((sizeof(angle_hda2_ice_alt) / sizeof(angle_hda2_ice_alt[0])) == 7U, "Banked RX template size");
          static const RxCheck angle_hda2_ice_std[] = {
            HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(1, 0x100)
            HYUNDAI_CANFD_SCC_ADDR_CHECK(1)
            {.msg = {{0x70, 1, 32, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}, {0}, {0}}},
          };
          _Static_assert((sizeof(angle_hda2_ice_std) / sizeof(angle_hda2_ice_std[0])) == 7U, "Banked RX template size");
          selected = hyundai_canfd_alt_buttons ? angle_hda2_ice_alt : angle_hda2_ice_std;
        }
      }
      else {
        if (hyundai_ev_gas_signal) {
          static const RxCheck angle_camera_ev_alt[] = {
            HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(0, 0x35)
            HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
          };
          _Static_assert((sizeof(angle_camera_ev_alt) / sizeof(angle_camera_ev_alt[0])) == 6U, "Banked RX template size");
          static const RxCheck angle_camera_ev_std[] = {
            HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(0, 0x35)
            HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
          };
          _Static_assert((sizeof(angle_camera_ev_std) / sizeof(angle_camera_ev_std[0])) == 6U, "Banked RX template size");
          selected = hyundai_canfd_alt_buttons ? angle_camera_ev_alt : angle_camera_ev_std;
        }
        else if (hyundai_hybrid_gas_signal) {
          static const RxCheck angle_camera_hybrid_alt[] = {
            HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(0, 0x105)
            HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
            {.msg = {{0x70, 0, 32, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}, {0}, {0}}},
          };
          _Static_assert((sizeof(angle_camera_hybrid_alt) / sizeof(angle_camera_hybrid_alt[0])) == 7U, "Banked RX template size");
          static const RxCheck angle_camera_hybrid_std[] = {
            HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(0, 0x105)
            HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
            {.msg = {{0x70, 0, 32, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}, {0}, {0}}},
          };
          _Static_assert((sizeof(angle_camera_hybrid_std) / sizeof(angle_camera_hybrid_std[0])) == 7U, "Banked RX template size");
          selected = hyundai_canfd_alt_buttons ? angle_camera_hybrid_alt : angle_camera_hybrid_std;
        }
        else {
          static const RxCheck angle_camera_ice_alt[] = {
            HYUNDAI_CANFD_BANKED_ANGLE_ALT_RX_CHECKS(0, 0x100)
            HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
            {.msg = {{0x70, 0, 32, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}, {0}, {0}}},
          };
          _Static_assert((sizeof(angle_camera_ice_alt) / sizeof(angle_camera_ice_alt[0])) == 7U, "Banked RX template size");
          static const RxCheck angle_camera_ice_std[] = {
            HYUNDAI_CANFD_BANKED_ANGLE_STD_RX_CHECKS(0, 0x100)
            HYUNDAI_CANFD_SCC_ADDR_CHECK(2)
            {.msg = {{0x70, 0, 32, 100U, .max_counter = 0xffU, .ignore_quality_flag = true}, {0}, {0}}},
          };
          _Static_assert((sizeof(angle_camera_ice_std) / sizeof(angle_camera_ice_std[0])) == 7U, "Banked RX template size");
          selected = hyundai_canfd_alt_buttons ? angle_camera_ice_alt : angle_camera_ice_std;
        }
      }
      ret.rx_checks_len = hyundai_ev_gas_signal ? 6 : 7;
      for (int i = 0; i < ret.rx_checks_len; i++) {
        banked_rx_workspace[i] = selected[i];
      }
      ret.rx_checks = banked_rx_workspace;
      for (int i = 0; i < ret.tx_msgs_len; i++) {
        independent_tx_msgs[i] = ret.tx_msgs[i];
        const unsigned int addr = independent_tx_msgs[i].addr;
        if ((addr == 0x50U) || (addr == 0x110U) || (addr == 0x12AU) || (addr == 0xCBU) || (addr == 0x2A4U) || (addr == 0x362U)) {
          independent_tx_msgs[i].disable_static_blocking = true;
        }
      }
      ret.tx_msgs = independent_tx_msgs;
      hyundai_angle_aol_enabled = true;
      hyundai_angle_aol_fault_mask = (pe_requested || ev9_requested) ? 7U : 2U;
      aol_ioniq6_long = true;
      static const AolSafetyPolicy independent_policy = {
        .reset = aol_ioniq6_reset,
        .host_request = aol_ioniq6_host_request,
        .request_mask = aol_ioniq6_request_mask,
        .permission_mask = hyundai_angle_aol_permission_mask,
        .rx_invalid = aol_ioniq6_rx_invalid,
      };
      aol_policy = &independent_policy;
    }
  }
#ifdef ALLOW_DEBUG
  if (gv60_long_requested) {
    // Keep the exact ordinary catalog plus GV60 heartbeat in flash, without
    // duplicating a 64-entry mutable table in the firmware RAM region.
    static const CanMsg gv60_standard_long_tx_msgs[] = {
      HYUNDAI_CANFD_LKA_STEER_MSG_LONG_TX_MSGS_BODY
      {0x100, 0, 24, .check_relay = true, .check_relay_immediately = true},
    };
    static const CanMsg gv60_alternate_long_tx_msgs[] = {
      HYUNDAI_CANFD_LKA_STEER_MSG_ALT_LONG_TX_MSGS_BODY
      {0x100, 0, 24, .check_relay = true, .check_relay_immediately = true},
    };
    const CanMsg *expected = HYUNDAI_CANFD_LKA_STEER_MSG_LONG_TX_MSGS;
    int expected_len = (int)sizeof(HYUNDAI_CANFD_LKA_STEER_MSG_LONG_TX_MSGS) / (int)sizeof(CanMsg);
    const CanMsg *replacement = gv60_standard_long_tx_msgs;
    int replacement_len = (int)sizeof(gv60_standard_long_tx_msgs) / (int)sizeof(CanMsg);
    if (hyundai_canfd_lka_steer_msg_alt) {
      expected = HYUNDAI_CANFD_LKA_STEER_MSG_ALT_LONG_TX_MSGS;
      expected_len = (int)sizeof(HYUNDAI_CANFD_LKA_STEER_MSG_ALT_LONG_TX_MSGS) / (int)sizeof(CanMsg);
      replacement = gv60_alternate_long_tx_msgs;
      replacement_len = (int)sizeof(gv60_alternate_long_tx_msgs) / (int)sizeof(CanMsg);
    }
    ret = hyundai_canfd_gv60_extend_tx(ret, expected, expected_len, replacement, replacement_len);
  }
#endif
  if (ccnc_long_requested && ((unsigned int)alternative_experience == 32U)) {
    // Keep the LONG RX/TX catalogs. Stock-SCC RX and lateral-only policy cannot own this ECU.
    hyundai_ev9_long_aol = true;
    hyundai_angle_aol_enabled = true;
    hyundai_angle_aol_fault_mask = pe_long_requested ? 7U : 2U;
    aol_ioniq6_long = true;
    static const AolSafetyPolicy ev9_combined_policy = {
      .reset = aol_ioniq6_reset,
      .host_request = aol_ioniq6_host_request,
      .request_mask = aol_ioniq6_request_mask,
      .permission_mask = hyundai_ev9_long_aol_permission_mask,
      .rx_invalid = aol_ioniq6_rx_invalid,
    };
    aol_policy = &ev9_combined_policy;
  }
  return ret;
}

static safety_config hyundai_canfd_init(uint16_t param) {
  hyundai_daw_init(true, false);
  hyundai_canfd_reset_ioniq6_state();
  hyundai_ordinary_angle_reset();
#ifdef ALLOW_DEBUG
  const bool valid = !GET_FLAG(param, 32768U) || (param == 0x8015U) || (param == 0x8095U) ||
                     (param == 0x8815U) || (param == 0x8895U) ||
                     hyundai_canfd_gv60_long_param(param);
#else
  const bool valid = !GET_FLAG(param, 32768U);
#endif
  safety_config ret = {0};
  const bool marker_valid = GET_FLAG(param, 16384U) || !GET_FLAG(param, 2048U) ||
                            hyundai_canfd_stock_torque_aol_param(param) ||
#ifdef ALLOW_DEBUG
                            (hyundai_canfd_torque_long_aol_param(param) && ((unsigned int)alternative_experience == 32U)) ||
#endif
                            (param == 0x8815U) || (param == 0x8895U);
  if (valid && marker_valid) {
    ret = hyundai_canfd_init_validated(param);
    const bool daw_profile = (param == 0x0011U) || (param == 0x0091U) || (param == 0x0811U) || (param == 0x0891U) ||
                             (param == 0x8015U) || (param == 0x8095U) || (param == 0x8815U) || (param == 0x8895U);
    ret = hyundai_daw_configure(ret, true, daw_profile);
  }
  return ret;
}

const safety_hooks hyundai_canfd_hooks = {
  .init = hyundai_canfd_init,
  .rx = hyundai_canfd_rx_hook,
  .optional_rx = hyundai_canfd_optional_rx_hook,
  .tx = hyundai_canfd_tx_hook,
  .fwd = hyundai_canfd_fwd_hook,
  .get_counter = hyundai_canfd_get_counter,
  .get_checksum = hyundai_canfd_get_checksum,
  .compute_checksum = hyundai_common_canfd_compute_checksum,
};
