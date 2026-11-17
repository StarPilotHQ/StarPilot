#pragma once

// The existing axis transport owns host request/heartbeat leases. This owner
// grants lateral after physical intent, with current Subaru sources.
static bool subaru_aol_enabled = false;
static uint8_t subaru_aol_gear_bus = 0U;
static bool subaru_aol_armed = false;
static bool subaru_aol_token = false;
static bool subaru_aol_pending = false;
static bool subaru_aol_session = false;
static bool subaru_aol_requested = false;
static uint32_t subaru_aol_grant_ts = 0U;
static bool subaru_aol_main = false;
static bool subaru_aol_cruise = false;
static bool subaru_aol_permanent = false;
static bool subaru_aol_temporary = false;
static bool subaru_aol_drive = false;
static bool subaru_aol_gear_seen = false;
static bool subaru_aol_gear_qualified = false;
static uint8_t subaru_aol_gear_counter = 0U;
static uint32_t subaru_aol_gear_ts = 0U;
static uint32_t subaru_aol_main_ts = 0U;
static bool subaru_aol_main_seen = false;
static bool subaru_aol_main_arm = false;
static bool subaru_aol_main_epoch = false;
static bool subaru_aol_main_low_seen = false;
static bool subaru_aol_acc_fault = false;

static void subaru_aol_clear(void) {
  subaru_aol_main_epoch = false;
  subaru_aol_armed = false;
  subaru_aol_token = false;
  subaru_aol_pending = false;
  subaru_aol_session = false;
}

static void subaru_aol_reset(void) {
  subaru_aol_clear();
  subaru_aol_enabled = false;
  subaru_aol_requested = false;
  subaru_aol_grant_ts = 0U;
  subaru_aol_gear_bus = 0U;
  subaru_aol_main = false;
  subaru_aol_cruise = false;
  subaru_aol_permanent = false;
  subaru_aol_temporary = false;
  subaru_aol_drive = false;
  subaru_aol_gear_seen = false;
  subaru_aol_gear_qualified = false;
  subaru_aol_main_seen = false;
  subaru_aol_main_arm = false;
  subaru_aol_main_low_seen = false;
  subaru_aol_acc_fault = false;
  subaru_aol_gear_counter = 0U;
  subaru_aol_gear_ts = 0U;
  subaru_aol_main_ts = 0U;
}

// A successful physical stock engagement precedes the host/Panda join.
// The token cannot grant output until the existing fresh request and heartbeat
// acknowledge it. A fault-clear while cruise remains held cannot mint a token.
static void subaru_aol_grant(bool cruise) {
  if (subaru_aol_enabled && cruise && !subaru_aol_cruise && controls_allowed &&
      subaru_aol_main && subaru_aol_gear_qualified && subaru_aol_drive &&
      (safety_get_ts_elapsed(microsecond_timer_get(), subaru_aol_gear_ts) <= 100000U) &&
      (!subaru_aol_main_arm || !subaru_aol_acc_fault) &&
      !subaru_aol_permanent && !subaru_aol_temporary && !brake_pressed &&
      !relay_malfunction && !safety_rx_checks_invalid && aol_rx_healthy()) {
    subaru_aol_token = true;
    subaru_aol_pending = !heartbeat_engaged;
    subaru_aol_session = heartbeat_engaged;
    subaru_aol_grant_ts = microsecond_timer_get();
  }
  subaru_aol_cruise = cruise;
}

// MAIN authorizes intent, never output. Joining still needs a new bounded host
// request, heartbeat, healthy physical ACC/steering sources and moving Drive.
static void subaru_aol_observe_main(bool main_on) {
  if (!main_on) {
    subaru_aol_clear();
    subaru_aol_main_low_seen = true;
  } else if (subaru_aol_main_arm && subaru_aol_main_low_seen && !subaru_aol_main &&
             subaru_aol_gear_qualified && subaru_aol_drive &&
             (safety_get_ts_elapsed(microsecond_timer_get(), subaru_aol_gear_ts) <= 100000U) &&
             !subaru_aol_permanent && !subaru_aol_temporary && !subaru_aol_acc_fault &&
             !relay_malfunction && !safety_rx_checks_invalid && aol_rx_healthy()) {
    subaru_aol_main_epoch = true;
  } else {
    // No qualified MAIN edge; retain epoch.
  }
  subaru_aol_main = main_on;
  subaru_aol_main_seen = true;
  subaru_aol_main_ts = microsecond_timer_get();
}

static void subaru_aol_host_request(uint8_t mask) {
  if (subaru_aol_main_arm && subaru_aol_main_epoch && !subaru_aol_token && (mask != 0U) &&
      subaru_aol_main && subaru_aol_gear_qualified && subaru_aol_drive && vehicle_moving &&
      (safety_get_ts_elapsed(microsecond_timer_get(), subaru_aol_gear_ts) <= 100000U) &&
      !subaru_aol_acc_fault && !subaru_aol_permanent && !subaru_aol_temporary && !brake_pressed &&
      !relay_malfunction && !safety_rx_checks_invalid && aol_rx_healthy()) {
    subaru_aol_token = true;
    subaru_aol_pending = !heartbeat_engaged;
    subaru_aol_session = heartbeat_engaged;
    subaru_aol_grant_ts = microsecond_timer_get();
  }
  subaru_aol_requested |= mask != 0U;
}

static uint8_t subaru_aol_request(void) {
  uint8_t request = 0U;
  const uint32_t now = microsecond_timer_get();
  const uint32_t grant_age = safety_get_ts_elapsed(now, subaru_aol_grant_ts);
  const bool pending_expired = subaru_aol_pending && (grant_age > AOL_HOST_REQUEST_TIMEOUT_US);
  const bool request_expired = subaru_aol_requested &&
    (safety_get_ts_elapsed(now, aol_host_request_ts) > AOL_HOST_REQUEST_TIMEOUT_US);
  if (relay_malfunction || safety_rx_checks_invalid || !aol_rx_healthy() || pending_expired || request_expired ||
      (subaru_aol_session && !heartbeat_engaged)) {
    subaru_aol_clear();
  } else if (heartbeat_engaged) {
    const bool acknowledged = !subaru_aol_pending || ((aol_host_axis_mask != 0U) &&
      (safety_get_ts_elapsed(now, aol_host_request_ts) < grant_age));
    if (acknowledged) {
      subaru_aol_pending = false;
      subaru_aol_session = true;
      request = aol_host_axis_mask & 1U;
    }
  } else {
  }
  return request;
}

static uint8_t subaru_aol_permission(void) {
  const uint8_t request = subaru_aol_request();
  const uint32_t now = microsecond_timer_get();
  // Conservative additional-source bounds do not extend common RX/host leases.
  const bool current = subaru_aol_gear_qualified && subaru_aol_main_seen &&
    (safety_get_ts_elapsed(now, subaru_aol_gear_ts) <= 100000U);
  // MAIN is already a required, checksum/counter/frequency-qualified RX source.
  // Its original lease stays owned by aol_rx_healthy(); an extra one-period
  // deadline incorrectly revoked intent at ordinary 10Hz publication jitter.
  if (!subaru_aol_main || subaru_aol_permanent) {
    subaru_aol_clear();
  }
  uint8_t permission = 0U;
  if (current && subaru_aol_main && subaru_aol_drive && vehicle_moving &&
      (!subaru_aol_main_arm || !subaru_aol_acc_fault) &&
      !subaru_aol_permanent && !subaru_aol_temporary) {
    if ((request != 0U) && subaru_aol_token &&
        ((controls_allowed && subaru_aol_cruise) || subaru_aol_main_epoch)) {
      subaru_aol_armed = true;
    }
    if (subaru_aol_armed) {
      permission = request;
    }
  }
  return permission;
}

// Called only for the non-required Transmission frame, after its own exact
// checksum/counter checks. Required control RX remains independently mandatory.
static void subaru_aol_optional_rx(const CANPacket_t *msg) {
  if (subaru_aol_enabled && (msg->addr == 0x48U) && (msg->bus == subaru_aol_gear_bus)) {
    if (GET_LEN(msg) != 8U) {
      subaru_aol_gear_qualified = false;
      subaru_aol_gear_seen = false;
    } else {
      uint8_t checksum = 0x48U;
      for (int i = 1; i < 8; i++) {
        checksum += msg->data[i];
      }
      const uint8_t counter = msg->data[1] & 15U;
      const bool valid = msg->data[0] == checksum;
      if (!valid) {
        subaru_aol_gear_qualified = false;
        subaru_aol_gear_seen = false;
      } else if (!subaru_aol_gear_seen || (counter != subaru_aol_gear_counter)) {
        subaru_aol_gear_qualified = subaru_aol_gear_seen &&
          (counter == ((subaru_aol_gear_counter + 1U) & 15U));
        subaru_aol_gear_seen = true;
        subaru_aol_gear_counter = counter;
        subaru_aol_gear_ts = microsecond_timer_get();
        subaru_aol_drive = msg->data[3] == 121U;
      } else {
        // A contradictory duplicate must withdraw Drive immediately; it may
        // neither renew the source receipt nor retain an old Drive permission.
        if (((msg->data[3] == 121U) && !subaru_aol_drive) || ((msg->data[3] != 121U) && subaru_aol_drive)) {
          subaru_aol_gear_qualified = false;
          subaru_aol_gear_seen = false;
        }
      }
    }
  }
}
