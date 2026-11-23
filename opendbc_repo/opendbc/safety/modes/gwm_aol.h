#pragma once

// GWM Haval H6 GT: the physical stalk owns engagement, so lateral authority
// follows the stalk latch rather than ordinary engagement.
//
// acc_main_on is that latch, maintained in gwm_rx_hook: the rising edge of a
// stalk pull sets it and only the stalk-down cancel clears it - a brake press
// deliberately does not, which is the whole point of Always On Lateral on this
// car. Longitudinal keeps the ordinary requirement (controls_allowed with no
// pedal), so this grants no new longitudinal authority.
//
// Registering this policy moves lateral_controls_allowed() off plain
// controls_allowed and onto permission_mask() & 1, so every gate below is also
// a gate on normal engaged steering. It is therefore kept to the latch plus
// stream freshness; nothing here is a new torque allowance.
//
// Addresses are literals so this header stays independent of gwm.h include
// order, matching nissan_aol.h / rivian_stock_aol.h.
#define GWM_AOL_STALK_ADDR 0xA1U  // STEER_AND_AP_STALK, must match GWM_ADAS_ACTIVATION
#define GWM_AOL_STALK_BUS  0U     // must match GWM_MAIN_BUS
#define GWM_AOL_STALK_LEN  8U
#define GWM_AOL_STALK_TIMEOUT_US 300000U

static bool gwm_aol_enabled = false;
static bool gwm_aol_stalk_seen = false;
static uint32_t gwm_aol_stalk_ts = 0U;

// Drop the vehicle-sourced observation. A host request can never renew it:
// only a fresh stalk frame can, which arrives at 100 Hz.
static void gwm_aol_clear(void) {
  gwm_aol_stalk_seen = false;
  gwm_aol_stalk_ts = 0U;
}

static void gwm_aol_reset(void) {
  gwm_aol_clear();
  gwm_aol_enabled = false;
}

// No .host_request callback: aol_set_host_request already records the mask and
// its timestamp in the framework, and this profile needs no extra bookkeeping
// on top of them. Same shape as nissan.
static uint8_t gwm_aol_request_mask(void) {
  const bool request_fresh =
    safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) <= AOL_HOST_REQUEST_TIMEOUT_US;
  uint8_t request = 0U;
  if (heartbeat_engaged && aol_rx_healthy() && !safety_rx_checks_invalid && !relay_malfunction && request_fresh) {
    request = aol_host_axis_mask;
  }
  return request;
}

static uint8_t gwm_aol_permission(void) {
  const uint8_t request = gwm_aol_request_mask();
  const bool stalk_fresh = gwm_aol_stalk_seen &&
    (safety_get_ts_elapsed(microsecond_timer_get(), gwm_aol_stalk_ts) <= GWM_AOL_STALK_TIMEOUT_US);
  uint8_t permission = 0U;
  if (stalk_fresh && acc_main_on) {
    // Lateral tracks the stalk latch, which survives a brake press.
    permission = request & 1U;
    // Longitudinal keeps the ordinary engagement requirement; this policy adds
    // no longitudinal authority and no GWM longitudinal CAN messages.
    if (controls_allowed && !brake_pressed && !gas_pressed) {
      permission |= request & 2U;
    }
  }
  return permission;
}

static void gwm_aol_observe(const CANPacket_t *msg) {
  if (gwm_aol_enabled) {
    if (msg_matches(msg, GWM_AOL_STALK_ADDR, GWM_AOL_STALK_BUS)) {
      gwm_aol_stalk_seen = GET_LEN(msg) == GWM_AOL_STALK_LEN;
      if (gwm_aol_stalk_seen) {
        gwm_aol_stalk_ts = microsecond_timer_get();
      }
    }
  }
}
