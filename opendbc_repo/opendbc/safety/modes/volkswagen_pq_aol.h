#pragma once

static bool pq_aol_enabled = false;
static bool pq_aol_token = false;
static bool pq_aol_pending = false;
static bool pq_aol_session = false;
static uint32_t pq_aol_grant_ts = 0U;
static bool pq_aol_long_armed = false;
static uint8_t pq_aol_buttons = 0U;
static bool pq_aol_cruise = false;
static uint32_t pq_aol_neutral_ts = 0U;
static bool pq_aol_startup = true;
static bool pq_aol_main_neutral = false;
static bool pq_aol_requested = false;
static bool pq_aol_gear_drive = false;
static bool pq_aol_eps_ready = false;
static bool pq_aol_speed_ready = false;
static bool pq_aol_pair_seen[2] = {false, false};
static uint8_t pq_aol_counter[2] = {0U, 0U};
static uint8_t pq_aol_seen = 0U;
static uint32_t pq_aol_source_ts[4] = {0U, 0U, 0U, 0U};
static bool pq_aol_accepted[2] = {false, false};
static uint32_t pq_aol_accepted_ts[2] = {0U, 0U};

static void pq_aol_clear(void) {
  pq_aol_token = false;
  pq_aol_pending = false;
  pq_aol_session = false;
  pq_aol_long_armed = false;
  pq_aol_buttons = 0U;
  pq_aol_main_neutral = false;
  for (unsigned int i = 0U; i < 2U; i++) {
    pq_aol_accepted[i] = false;
  }
}

static void pq_aol_reset(void) {
  pq_aol_clear();
  pq_aol_enabled = false;
  pq_aol_startup = true;
  pq_aol_requested = false;
  pq_aol_cruise = false;
  pq_aol_neutral_ts = 0U;
  pq_aol_grant_ts = 0U;
  pq_aol_gear_drive = false;
  pq_aol_eps_ready = false;
  pq_aol_speed_ready = false;
  pq_aol_seen = 0U;
  for (unsigned int i = 0U; i < 4U; i++) {
    pq_aol_source_ts[i] = 0U;
  }
  for (unsigned int i = 0U; i < 2U; i++) {
    pq_aol_pair_seen[i] = false;
    pq_aol_counter[i] = 0U;
    pq_aol_accepted_ts[i] = 0U;
  }
}

static bool pq_aol_current(void) {
  bool current = pq_aol_seen == 15U;
  for (unsigned int i = 0U; i < 4U; i++) {
    current = current && (safety_get_ts_elapsed(microsecond_timer_get(), pq_aol_source_ts[i]) <= 300000U);
  }
  return current;
}

static void pq_aol_request(uint8_t mask) {
  pq_aol_requested |= mask != 0U;
  if (mask == 0U) {
    pq_aol_accepted[0] = false;
    pq_aol_accepted[1] = false;
  }
}

static uint8_t pq_aol_request_mask(void) {
  uint8_t request = 0U;
  const bool expired = pq_aol_requested &&
    (safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) > AOL_HOST_REQUEST_TIMEOUT_US);
  const uint32_t grant_age = safety_get_ts_elapsed(microsecond_timer_get(), pq_aol_grant_ts);
  const bool pending_expired = pq_aol_pending && (grant_age > AOL_HOST_REQUEST_TIMEOUT_US);
  if (!pq_aol_enabled || relay_malfunction || expired || pending_expired || safety_rx_checks_invalid ||
      !aol_rx_healthy() || !pq_aol_current()) {
    pq_aol_clear();
  } else if (pq_aol_pending) {
    if (heartbeat_engaged && ((aol_host_axis_mask & 2U) != 0U) &&
        (safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) < grant_age)) {
      pq_aol_pending = false;
      pq_aol_session = true;
      request = aol_host_axis_mask;
    }
  } else if (!heartbeat_engaged) {
    if (pq_aol_session) {
      pq_aol_clear();
    } else if (pq_aol_main_neutral &&
               (safety_get_ts_elapsed(microsecond_timer_get(), pq_aol_neutral_ts) > AOL_HOST_REQUEST_TIMEOUT_US)) {
      pq_aol_main_neutral = false;
    } else {
    }
  } else {
    pq_aol_session = true;
    request = aol_host_axis_mask;
  }
  return request;
}

static uint8_t pq_aol_permission(void) {
  const uint8_t request = pq_aol_request_mask();
  uint8_t permission = 0U;
  if (acc_main_on && pq_aol_gear_drive && pq_aol_eps_ready) {
    if (pq_aol_token && pq_aol_speed_ready) {
      permission = request & 1U;
    }
    if (volkswagen_longitudinal && pq_aol_long_armed && controls_allowed && !brake_pressed) {
      permission |= request & 2U;
    }
  }
  return permission;
}

static void pq_aol_optional_rx(const CANPacket_t *msg) {
  if (pq_aol_enabled && (msg_matches(msg, 0x440U, 0U, 8U) || msg_matches(msg, 0x3D2U, 0U, 7U))) {
    const bool gear = msg->addr == 0x440U;
    const unsigned int index = gear ? 0U : 1U;
    const unsigned int source = gear ? 1U : 2U;
    const uint8_t counter = gear ? ((msg->data[6] >> 3) & 15U) : (msg->data[1] & 15U);
    uint8_t checksum = 0U;
    for (unsigned int i = 1U; i < 7U; i++) {
      checksum ^= msg->data[i];
    }
    const bool valid = gear || (checksum == msg->data[0]);
    const bool pair = valid && pq_aol_pair_seen[index] &&
      (counter == ((pq_aol_counter[index] + 1U) & 15U)) &&
      (safety_get_ts_elapsed(microsecond_timer_get(), pq_aol_source_ts[source]) <= 300000U);
    pq_aol_pair_seen[index] = valid;
    pq_aol_counter[index] = counter;
    if (valid) {
      pq_aol_source_ts[source] = microsecond_timer_get();
    }
    if (pair) {
      pq_aol_seen |= (uint8_t)(1U << source);
    } else {
      pq_aol_seen &= (uint8_t)~(1U << source);
    }
    if (gear) {
      const unsigned int position = msg->data[1] >> 4;
      pq_aol_gear_drive = pair && ((position == 5U) || (position == 12U) ||
        (position == 10U) || (position == 11U) || (position == 14U));
    } else {
      const unsigned int eps = msg->data[2] & 15U;
      pq_aol_eps_ready = pair && ((eps == 3U) || (eps == 5U) || (eps == 7U));
    }
    if (!pair) {
      pq_aol_clear();
    } else if (gear && !pq_aol_gear_drive) {
      pq_aol_long_armed = false;
    } else if (!gear && !pq_aol_eps_ready) {
      const unsigned int eps = msg->data[2] & 15U;
      pq_aol_long_armed = false;
      if ((eps != 1U) && (eps != 4U)) {
        pq_aol_clear();
      }
    } else {
    }
  }
}

static void pq_aol_rx(const CANPacket_t *msg) {
  if (pq_aol_enabled) {
    if (brake_pressed && (!brake_pressed_prev || vehicle_moving)) {
      pq_aol_long_armed = false;
    }
    if (msg_matches(msg, MSG_BREMSE_1, 0U, 8U)) {
      const unsigned int speed = ((msg->data[2] & 0xFEU) >> 1) | (msg->data[3] << 7);
      pq_aol_speed_ready = speed >= 5000U;
      pq_aol_seen |= 8U;
      pq_aol_source_ts[3] = microsecond_timer_get();
    } else if (msg_matches(msg, MSG_MOTOR_5, 0U, 8U)) {
      acc_main_on = GET_BIT(msg, 50U);
      pq_aol_seen |= 1U;
      pq_aol_source_ts[0] = microsecond_timer_get();
      if (!acc_main_on) {
        pq_aol_startup = false;
        pq_aol_clear();
        pq_aol_main_neutral = true;
        pq_aol_neutral_ts = microsecond_timer_get();
        controls_allowed = false;
      } else if ((pq_aol_startup || pq_aol_main_neutral) && heartbeat_engaged && aol_rx_healthy() &&
                 !safety_rx_checks_invalid && pq_aol_current() && pq_aol_gear_drive && pq_aol_eps_ready &&
                 pq_aol_speed_ready && !brake_pressed) {
        pq_aol_token = true;
        pq_aol_startup = false;
        pq_aol_main_neutral = false;
      } else {
      }
    } else if (msg_matches(msg, MSG_MOTOR_2, 0U, 8U)) {
      const unsigned int status = (msg->data[2] >> 6) & 3U;
      const bool cruise = (status == 1U) || (status == 2U);
      if (!volkswagen_longitudinal && cruise && !pq_aol_cruise && controls_allowed && acc_main_on &&
          heartbeat_engaged && aol_rx_healthy() && !safety_rx_checks_invalid && pq_aol_current() &&
          pq_aol_gear_drive && pq_aol_eps_ready && pq_aol_speed_ready && !brake_pressed) {
        pq_aol_token = true;
        pq_aol_startup = false;
      }
      pq_aol_cruise = cruise;
    } else if (msg_matches(msg, MSG_GRA_NEU, 0U, 4U)) {
      if (GET_BIT(msg, 9U)) {
        if (!pq_aol_session || pq_aol_pending) {
          pq_aol_token = false;
        }
        pq_aol_pending = false;
        pq_aol_long_armed = false;
        pq_aol_buttons = 0U;
        pq_aol_main_neutral = false;
      } else if (volkswagen_longitudinal) {
        const uint8_t buttons = (GET_BIT(msg, 16U) ? 1U : 0U) | (GET_BIT(msg, 17U) ? 2U : 0U);
        if ((!heartbeat_engaged && pq_aol_session) || relay_malfunction ||
            !aol_rx_healthy() || safety_rx_checks_invalid || !pq_aol_current()) {
          pq_aol_buttons = 0U;
        } else if (buttons == 0U) {
          if (((pq_aol_buttons & 3U) != 0U) && controls_allowed && acc_main_on &&
              pq_aol_gear_drive && pq_aol_eps_ready && (!brake_pressed || !vehicle_moving)) {
            pq_aol_token = true;
            pq_aol_long_armed = true;
            pq_aol_pending = !heartbeat_engaged;
            pq_aol_session = heartbeat_engaged;
            pq_aol_grant_ts = microsecond_timer_get();
            pq_aol_startup = false;
          }
          pq_aol_buttons = 4U;
        } else if ((pq_aol_buttons & 4U) != 0U) {
          pq_aol_buttons = 4U | buttons;
        } else {
        }
      } else {
      }
    } else {
    }
  }
}

static void pq_aol_tx_commit(const CANPacket_t *msg, bool accepted) {
  if (pq_aol_enabled) {
    if (msg_matches(msg, MSG_HCA_1, 0U, 5U)) {
      const unsigned int status = msg->data[1] >> 4;
      const unsigned int torque = (msg->data[2] | ((msg->data[3] & 0x7FU) << 8)) / 32U;
      pq_aol_accepted[0] = accepted && ((status == 5U) || (status == 7U)) && (torque != 0U) &&
        ((pq_aol_permission() & 1U) != 0U);
      pq_aol_accepted_ts[0] = microsecond_timer_get();
    } else if (msg_matches(msg, MSG_LDW_1, 0U, 8U)) {
      pq_aol_accepted[1] = accepted;
      pq_aol_accepted_ts[1] = microsecond_timer_get();
    } else {
    }
  }
}

static bool pq_aol_fwd(int bus, int addr) {
  bool blocked = false;
  const uint32_t now = microsecond_timer_get();
  if (pq_aol_enabled && (bus == 2) && pq_aol_accepted[0] && ((pq_aol_permission() & 1U) != 0U) &&
      (safety_get_ts_elapsed(now, pq_aol_accepted_ts[0]) <= AOL_HOST_REQUEST_TIMEOUT_US)) {
    if (addr == (int)MSG_HCA_1) {
      blocked = true;
    } else if (addr == (int)MSG_LDW_1) {
      blocked = pq_aol_accepted[1] &&
        (safety_get_ts_elapsed(now, pq_aol_accepted_ts[1]) <= AOL_HOST_REQUEST_TIMEOUT_US);
    } else {
    }
  }
  return blocked;
}
