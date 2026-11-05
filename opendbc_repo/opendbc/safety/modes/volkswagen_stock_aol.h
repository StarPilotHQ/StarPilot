#pragma once

static bool vw_aol_enabled = false;
static bool vw_aol_longitudinal = false;
static bool vw_aol_long_armed = false;
static uint8_t vw_aol_buttons = 0U;
static bool vw_aol_manual = false;
static bool vw_aol_direct = false;
static bool vw_aol_direct_counter_seen = false;
static uint8_t vw_aol_direct_counter = 0U;
static bool vw_aol_token = false;
static bool vw_aol_initial_main = true;
static bool vw_aol_cruise_engaged = false;
static bool vw_aol_main_neutral = false;
static bool vw_aol_requested = false;
static bool vw_aol_gear_drive = false;
static bool vw_aol_eps_ready = false;
static bool vw_aol_owned = false;
static uint8_t vw_aol_seen = 0U;
static uint32_t vw_aol_main_ts = 0U;
static uint32_t vw_aol_gear_ts = 0U;
static uint32_t vw_aol_eps_ts = 0U;
static uint32_t vw_aol_accepted_ts[3] = {0U, 0U, 0U};
static bool vw_aol_accepted[3] = {false, false, false};

static void vw_aol_clear(void) {
  vw_aol_token = false;
  vw_aol_long_armed = false;
  vw_aol_buttons = 0U;
  vw_aol_main_neutral = false;
  vw_aol_owned = false;
  for (unsigned int i = 0U; i < 3U; i++) {
    vw_aol_accepted[i] = false;
  }
}

static void vw_aol_reset(void) {
  vw_aol_clear();
  vw_aol_enabled = false;
  vw_aol_initial_main = true;
  vw_aol_longitudinal = false;
  vw_aol_cruise_engaged = false;
  vw_aol_manual = false;
  vw_aol_direct = false;
  vw_aol_direct_counter_seen = false;
  vw_aol_direct_counter = 0U;
  vw_aol_requested = false;
  vw_aol_gear_drive = false;
  vw_aol_eps_ready = false;
  vw_aol_seen = 0U;
  vw_aol_main_ts = 0U;
  vw_aol_gear_ts = 0U;
  vw_aol_eps_ts = 0U;
  for (unsigned int i = 0U; i < 3U; i++) {
    vw_aol_accepted_ts[i] = 0U;
  }
}

static bool vw_aol_current(void) {
  const uint32_t now = microsecond_timer_get();
  return (vw_aol_seen == 7U) &&
    (safety_get_ts_elapsed(now, vw_aol_main_ts) <= 300000U) &&
    (safety_get_ts_elapsed(now, vw_aol_gear_ts) <= 300000U) &&
    (safety_get_ts_elapsed(now, vw_aol_eps_ts) <= 300000U);
}

static void vw_aol_request(uint8_t mask) {
  vw_aol_requested |= mask != 0U;
  if (mask == 0U) {
    vw_aol_owned = false;
    for (unsigned int i = 0U; i < 3U; i++) {
      vw_aol_accepted[i] = false;
    }
  }
}

static uint8_t vw_aol_request_mask(void) {
  const bool expired = vw_aol_requested &&
    (safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) > AOL_HOST_REQUEST_TIMEOUT_US);
  uint8_t request = 0U;
  if (!vw_aol_enabled || relay_malfunction || expired || safety_rx_checks_invalid ||
      !heartbeat_engaged || !aol_rx_healthy() || !vw_aol_current()) {
    vw_aol_clear();
  } else {
    request = aol_host_axis_mask;
  }
  return request;
}

static uint8_t vw_aol_permission(void) {
  const uint8_t request = vw_aol_request_mask();
  uint8_t permission = 0U;
  if (acc_main_on && vehicle_moving && vw_aol_gear_drive && vw_aol_eps_ready &&
      vw_aol_token) {
    permission = request & 1U;
  }
  // Stock ACC never grants LONG. DEBUG OP-long requires its ordinary native grant,
  // fresh host request and current physical sources; lateral token cannot enable speed control.
  if (vw_aol_longitudinal && vw_aol_long_armed && acc_main_on && vw_aol_gear_drive && controls_allowed && !brake_pressed) {
    permission |= request & 2U;
  }
  return permission;
}

static void vw_aol_optional_rx(const CANPacket_t *msg) {
  if (vw_aol_enabled) {
    if (vw_aol_direct && msg_matches(msg, 0x187U, 0U, 8U)) {
      const uint8_t counter = volkswagen_mxb_get_counter(msg);
      const bool checksum_ok = volkswagen_mxb_get_checksum(msg) == volkswagen_mxb_compute_crc(msg);
      const bool fresh_pair = vw_aol_direct_counter_seen &&
        (safety_get_ts_elapsed(microsecond_timer_get(), vw_aol_gear_ts) <= 300000U) &&
        (counter == ((vw_aol_direct_counter + 1U) & 15U));
      vw_aol_direct_counter_seen = checksum_ok;
      vw_aol_direct_counter = counter;
      if (checksum_ok) {
        vw_aol_gear_ts = microsecond_timer_get();
      }
      if (checksum_ok && fresh_pair) {
        const unsigned int gear = msg->data[2] & 7U;
        vw_aol_seen |= 2U;
        vw_aol_gear_drive = (gear == 5U) || (gear == 6U);
      } else {
        vw_aol_seen &= 5U;
        vw_aol_gear_drive = false;
      }
    } else if (!vw_aol_direct && !vw_aol_manual && msg_matches(msg, 0x3DCU, 0U, 8U)) {
      const unsigned int gear = msg->data[5] & 15U;
      vw_aol_seen |= 2U;
      vw_aol_gear_ts = microsecond_timer_get();
      vw_aol_gear_drive = (gear == 8U) || (gear == 9U) || (gear == 10U) || (gear == 13U) || (gear == 14U);
    } else if (vw_aol_manual && msg_matches(msg, 0x3DBU, 0U, 8U)) {
      vw_aol_seen |= 2U;
      vw_aol_gear_ts = microsecond_timer_get();
      vw_aol_gear_drive = !GET_BIT(msg, 25U);
    } else {
    }
    if (!vw_aol_gear_drive) {
      vw_aol_long_armed = false;
    }
  }
}

static void vw_aol_rx(const CANPacket_t *msg) {
  if (vw_aol_longitudinal && brake_pressed && (!brake_pressed_prev || vehicle_moving)) {
    vw_aol_long_armed = false;
  }
  if (vw_aol_enabled) {
    if (msg_matches(msg, MSG_LH_EPS_03, 0U, 8U)) {
      const unsigned int eps = msg->data[4] & 15U;
      vw_aol_seen |= 4U;
      vw_aol_eps_ts = microsecond_timer_get();
      vw_aol_eps_ready = (eps == 3U) || (eps == 5U);
      if ((eps != 1U) && (eps != 3U) && (eps != 4U) && (eps != 5U) && (eps != 8U)) {
        vw_aol_initial_main = false;
        vw_aol_clear();
      }
    } else if (msg_matches(msg, MSG_TSK_06, 0U, 8U)) {
      const unsigned int status = msg->data[3] & 7U;
      const bool engaged = (status == 3U) || (status == 4U) || (status == 5U);
      const bool engage_edge = engaged && !vw_aol_cruise_engaged;
      vw_aol_cruise_engaged = engaged;
      vw_aol_seen |= 1U;
      vw_aol_main_ts = microsecond_timer_get();
      if (!heartbeat_engaged || !aol_rx_healthy() || !vw_aol_current() || safety_rx_checks_invalid) {
        vw_aol_clear();
      } else if (!acc_main_on) {
        vw_aol_initial_main = false;
        vw_aol_clear();
        // Only a valid OFF observation may prepare a subsequent main-ON edge.
        vw_aol_main_neutral = ((msg->data[3] & 7U) < 2U);
      } else if (vw_aol_initial_main && vw_aol_gear_drive && vw_aol_eps_ready && !brake_pressed) {
        // Fresh complete physical MAIN-on startup grants LAT only. It never restores a cleared drive session.
        vw_aol_initial_main = false;
        vw_aol_token = true;
      } else if (!vw_aol_longitudinal && engage_edge && controls_allowed && vw_aol_gear_drive && vw_aol_eps_ready && !brake_pressed) {
        // A fresh stock engagement supplies native arm; host still requires its own current intent.
        vw_aol_token = true;
        vw_aol_main_neutral = false;
      } else if (vw_aol_main_neutral) {
        vw_aol_token = true;
        vw_aol_main_neutral = false;
      } else {
      }
    } else if (msg_matches(msg, MSG_GRA_ACC_01, 0U, 8U)) {
      if (GET_BIT(msg, 13U)) {
        vw_aol_initial_main = false;
        vw_aol_clear();
      } else if (vw_aol_longitudinal) {
        const bool healthy = heartbeat_engaged && aol_rx_healthy() && vw_aol_current() && !safety_rx_checks_invalid;
        const uint8_t buttons = (GET_BIT(msg, 16U) ? 1U : 0U) | (GET_BIT(msg, 19U) ? 2U : 0U);
        if (!healthy) {
          vw_aol_clear();
        } else if (buttons == 0U) {
          if (((vw_aol_buttons & 3U) != 0U) && controls_allowed && acc_main_on &&
              vw_aol_gear_drive && vw_aol_eps_ready && (!brake_pressed || (!vw_aol_manual && !vehicle_moving))) {
            vw_aol_token = true;
            vw_aol_long_armed = true;
          }
          vw_aol_buttons = 4U;
        } else if ((vw_aol_buttons & 4U) != 0U) {
          vw_aol_buttons = 4U | buttons;
        } else {
        }
      } else {
      }
    } else {
    }
  }
}

static void vw_aol_tx_commit(const CANPacket_t *msg, bool accepted) {
  if (vw_aol_enabled) {
    if (msg_matches(msg, MSG_HCA_01, 0U, 8U)) {
      vw_aol_owned = accepted && GET_BIT(msg, 30U) &&
        (volkswagen_mlb_mqb_steering_control_torque(msg) != 0) && ((vw_aol_permission() & 1U) != 0U);
      vw_aol_accepted[0] = vw_aol_owned;
      vw_aol_accepted_ts[0] = microsecond_timer_get();
    } else if (msg_matches(msg, MSG_LDW_02, 0U, 8U)) {
      vw_aol_accepted[1] = accepted;
      vw_aol_accepted_ts[1] = microsecond_timer_get();
    } else if (msg_matches(msg, MSG_LH_EPS_03, 2U, 8U)) {
      vw_aol_accepted[2] = accepted;
      vw_aol_accepted_ts[2] = microsecond_timer_get();
    } else {
    }
  }
}

static bool vw_aol_fwd(int bus, int addr) {
  bool blocked = false;
  if (vw_aol_enabled && vw_aol_owned && ((vw_aol_permission() & 1U) != 0U) &&
      (safety_get_ts_elapsed(microsecond_timer_get(), vw_aol_accepted_ts[0]) <= AOL_HOST_REQUEST_TIMEOUT_US)) {
    unsigned int index = 3U;
    if ((bus == 2) && (addr == (int)MSG_HCA_01)) {
      index = 0U;
    } else if ((bus == 2) && (addr == (int)MSG_LDW_02)) {
      index = 1U;
    } else if ((bus == 0) && (addr == (int)MSG_LH_EPS_03)) {
      index = 2U;
    } else {
    }
    blocked = (index < 3U) && vw_aol_accepted[index] &&
      (safety_get_ts_elapsed(microsecond_timer_get(), vw_aol_accepted_ts[index]) <= AOL_HOST_REQUEST_TIMEOUT_US);
  }
  return blocked;
}
