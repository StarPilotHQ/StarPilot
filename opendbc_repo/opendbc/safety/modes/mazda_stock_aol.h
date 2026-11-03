#pragma once

static bool mazda_ti_unhealthy(void);
static bool mazda_ti_lateral_paused(void);

static bool mazda_aol_enabled = false;
static bool mazda_aol_startup_pending = true;
static bool mazda_aol_token = false;
static bool mazda_aol_neutral = false;
static bool mazda_aol_buttons_neutral = false;
static bool mazda_aol_cruise_neutral = false;
static bool mazda_aol_pending = false;
static bool mazda_aol_session = false;
static uint32_t mazda_aol_grant_ts = 0U;
static bool mazda_aol_requested = false;
static bool mazda_aol_owned = false;
static uint8_t mazda_aol_seen = 0U;
static uint8_t mazda_aol_sources = 0U;
static uint32_t mazda_aol_main_ts = 0U;
static uint32_t mazda_aol_gear_ts = 0U;
static uint32_t mazda_aol_eps_ts = 0U;
static uint32_t mazda_aol_cam_ts = 0U;
static uint32_t mazda_aol_accepted_ts = 0U;

static void mazda_aol_clear(void) {
  mazda_aol_token = false;
  mazda_aol_pending = false;
  mazda_aol_session = false;
  mazda_aol_neutral = false;
  mazda_aol_buttons_neutral = false;
  mazda_aol_cruise_neutral = false;
  mazda_aol_owned = false;
}

static void mazda_aol_reset(void) {
  mazda_aol_clear();
  mazda_aol_enabled = false;
  mazda_aol_startup_pending = true;
  mazda_aol_requested = false;
  mazda_aol_seen = 0U;
  mazda_aol_sources = 0U;
  mazda_aol_main_ts = 0U;
  mazda_aol_gear_ts = 0U;
  mazda_aol_eps_ts = 0U;
  mazda_aol_cam_ts = 0U;
  mazda_aol_accepted_ts = 0U;
  mazda_aol_grant_ts = 0U;
}

static bool mazda_aol_current(void) {
  const uint32_t now = microsecond_timer_get();
  return (mazda_aol_seen == 15U) &&
    (safety_get_ts_elapsed(now, mazda_aol_main_ts) <= 300000U) &&
    (safety_get_ts_elapsed(now, mazda_aol_gear_ts) <= 300000U) &&
    (safety_get_ts_elapsed(now, mazda_aol_eps_ts) <= 300000U) &&
    (safety_get_ts_elapsed(now, mazda_aol_cam_ts) <= 300000U);
}

static void mazda_aol_request(uint8_t mask) {
  mazda_aol_requested |= mask != 0U;
}

// A physical grant may arrive before pandad's engaged heartbeat. It remains
// output-ineligible until a current host request from after this grant joins it.
static void mazda_aol_grant(void) {
  mazda_aol_startup_pending = false;
  mazda_aol_token = true;
  mazda_aol_pending = !heartbeat_engaged;
  mazda_aol_session = heartbeat_engaged;
  mazda_aol_grant_ts = microsecond_timer_get();
}

static uint8_t mazda_aol_request_mask(void) {
  uint8_t request = 0U;
  const uint32_t now = microsecond_timer_get();
  const uint32_t grant_age = safety_get_ts_elapsed(now, mazda_aol_grant_ts);
  const bool pending_expired = mazda_aol_pending && (grant_age > AOL_HOST_REQUEST_TIMEOUT_US);
  const bool expired = mazda_aol_requested &&
    (safety_get_ts_elapsed(now, aol_host_request_ts) > AOL_HOST_REQUEST_TIMEOUT_US);
  if (relay_malfunction || expired || pending_expired || safety_rx_checks_invalid ||
      (mazda_aol_session && !heartbeat_engaged) || !aol_rx_healthy() || !mazda_aol_current() ||
      // TI2 fault or unavailable interceptor revokes AOL as well as ordinary
      // engagement: a latched session must be re-armed, never silently resumed.
      mazda_ti_unhealthy()) {
    mazda_aol_clear();
  } else if (!mazda_aol_startup_pending && heartbeat_engaged && aol_rx_healthy() && mazda_aol_current()) {
    // Equal microsecond stamps cannot prove request-after-grant ordering.
    // Wait for a strictly later request; the original grant deadline stays fixed.
    const bool pending_ack = !mazda_aol_pending || ((aol_host_axis_mask != 0U) &&
      (safety_get_ts_elapsed(now, aol_host_request_ts) < grant_age));
    if (pending_ack) {
      mazda_aol_pending = false;
      mazda_aol_session = true;
      request = aol_host_axis_mask;
    }
  } else {
  }
  return request;
}

static uint8_t mazda_aol_permission(void) {
  const uint8_t request = mazda_aol_request_mask();
  uint8_t permission = 0U;
  if (acc_main_on && (mazda_aol_sources == 1U) && !mazda_ti_unhealthy()) {
    // Only lateral has a standstill restriction. Ordinary openpilot
    // longitudinal must retain HOLD/RESUME authority while stopped.
    // A TI2 0x02 pause withholds lateral without revoking the token, like standstill.
    if (vehicle_moving && !mazda_ti_lateral_paused() && (controls_allowed || mazda_aol_token)) {
      permission = request & 1U;
    }
    if (controls_allowed) {
      permission |= request & 2U;
    }
  }
  return permission;
}

static void mazda_aol_button_rx(const CANPacket_t *msg);

static void mazda_aol_optional_rx(const CANPacket_t *msg) {
  if (mazda_aol_enabled) {
    mazda_aol_button_rx(msg);
    const uint32_t now = microsecond_timer_get();
    const bool gear_msg = msg_matches(msg, 0x228U, 0U, 8U) || msg_matches(msg, 0x228U, 2U, 8U);
    if (gear_msg) {
      // On this car GEAR originates on the camera bus (bus 2); bus-0 copies only
      // appear intermittently when the panda relays them. Accept both.
      mazda_aol_seen |= 2U;
      mazda_aol_gear_ts = now;
      if (mazda_gen2 || mazda_gen3) {
        // GEN2/GEN3 GEAR is a little-endian nibble in byte 4 (mazda_2019.dbc
        // 33|4@1+). Its VAL_ table is 13 = P, 12 = R, 11 = N, 1..6 = D.
        // 33|4@1+ is a little-endian field starting at bit 33, so the nibble is
        // offset by one bit inside byte 4.
        const uint8_t gear = (msg->data[4] >> 1) & 0xFU;
        mazda_aol_sources = (mazda_aol_sources & 6U) | (((gear >= 1U) && (gear <= 6U)) ? 1U : 0U);
      } else {
        mazda_aol_sources = (mazda_aol_sources & 6U) | (((msg->data[0] & 7U) == 4U) ? 1U : 0U);
      }
    } else if (msg_matches(msg, 0x241U, 0U, 8U)) {
      mazda_aol_seen |= 4U;
      mazda_aol_eps_ts = now;
      mazda_aol_sources = (mazda_aol_sources & 5U) | (GET_BIT(msg, 50U) ? 2U : 0U);
    } else if (msg_matches(msg, 0x243U, 2U, 8U)) {
      mazda_aol_seen |= 8U;
      mazda_aol_cam_ts = now;
      mazda_aol_sources = (mazda_aol_sources & 3U) | (GET_BIT(msg, 16U) ? 4U : 0U);
      if ((mazda_aol_sources & 4U) != 0U) {
        mazda_aol_clear();
        mazda_aol_startup_pending = false;
      }
    } else {
    }
  }
}

static void mazda_aol_rx(const CANPacket_t *msg) {
  if (mazda_aol_enabled) {
    if (msg_matches(msg, 0x44AU, 0U, 8U)) {
      // GEN2 main switch (see mazda.h): CRZ_STATE != 0. States 0 = off, 1 = ready,
      // 2 = engaged, 4 = gas override; 3 and 5..7 are undefined and treated as on.
      mazda_aol_seen |= 1U;
      mazda_aol_main_ts = microsecond_timer_get();
      acc_main_on = (msg->data[0] & 0x70U) != 0U;
      if ((mazda_aol_session && !heartbeat_engaged) || !aol_rx_healthy() || !mazda_aol_current() || safety_rx_checks_invalid || ((mazda_aol_sources & 4U) != 0U)) {
        mazda_aol_clear();
      } else if (!acc_main_on) {
        mazda_aol_clear();
        mazda_aol_startup_pending = false;
        mazda_aol_neutral = true;
      } else if (mazda_aol_startup_pending && vehicle_moving && (mazda_aol_sources == 1U) && !brake_pressed && !gas_pressed &&
                 ((aol_host_axis_mask & 1U) != 0U) &&
                 (safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) <= AOL_HOST_REQUEST_TIMEOUT_US)) {
        // Same rule as GEN1: follow a fresh MAIN only after physical restrictions
        // have cleared, and require a strictly later host ACK.
        mazda_aol_startup_pending = false;
        mazda_aol_grant();
        mazda_aol_pending = true;
        mazda_aol_session = false;
      } else if (mazda_aol_neutral) {
        mazda_aol_grant();
        mazda_aol_neutral = false;
      } else {
      }
    } else if (msg_matches(msg, 0x24BU, 1U, 8U)) {
      // GEN2 interceptor liveness plays the role of the GEN1 EPS source. There is
      // no EPS-override bit on this platform, so the source bit stays clear.
      mazda_aol_seen |= 4U;
      mazda_aol_eps_ts = microsecond_timer_get();
    } else if (msg_matches(msg, 0x217U, 2U, 8U)) {
      // GEN2 camera-bus speed plays the role of the GEN1 camera source.
      mazda_aol_seen |= 8U;
      mazda_aol_cam_ts = microsecond_timer_get();
    } else if (msg_matches(msg, 0x21CU, 0U, 8U)) {
      mazda_aol_seen |= 1U;
      mazda_aol_main_ts = microsecond_timer_get();
      acc_main_on = GET_BIT(msg, 17U);
      if ((mazda_aol_session && !heartbeat_engaged) || !aol_rx_healthy() || !mazda_aol_current() || safety_rx_checks_invalid || ((mazda_aol_sources & 4U) != 0U)) {
        mazda_aol_clear();
      } else if (!acc_main_on) {
        mazda_aol_clear();
        mazda_aol_startup_pending = false;
        mazda_aol_neutral = true;
      } else if (mazda_aol_startup_pending && GET_BIT(msg, 3U)) {
        mazda_aol_startup_pending = false;
      } else if (mazda_aol_startup_pending && vehicle_moving && (mazda_aol_sources == 1U) && !brake_pressed && !gas_pressed &&
                 ((aol_host_axis_mask & 1U) != 0U) &&
                 (safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) <= AOL_HOST_REQUEST_TIMEOUT_US)) {
        // Dom follows fresh MAIN at boot. Wait through physical restrictions,
        // then require a strictly later host ACK within the original deadline.
        mazda_aol_startup_pending = false;
        mazda_aol_grant();
        mazda_aol_pending = true;
        mazda_aol_session = false;
      } else if (mazda_aol_neutral) {
        mazda_aol_grant();
        mazda_aol_neutral = false;
      } else {
      }
    } else {
      mazda_aol_button_rx(msg);
    }
  }
}

// Shared by the required and optional ingress paths: 0x9D is a required message
// for GEN1 but is only seen on the optional path for GEN2.
static void mazda_aol_button_rx(const CANPacket_t *msg) {
  if (mazda_aol_enabled && msg_matches(msg, 0x9DU, 0U, 8U)) {
    // Mapped Cancel can request LAT only after the same fresh physical edge
    // and current host acknowledgment used by the other cruise buttons.
    const bool press = (msg->data[0] & 0x35U) != 0U;
    if ((mazda_aol_session && !heartbeat_engaged) || !aol_rx_healthy() || !mazda_aol_current() || safety_rx_checks_invalid || ((mazda_aol_sources & 4U) != 0U)) {
      mazda_aol_clear();
    } else if (!press) {
      mazda_aol_buttons_neutral = true;
    } else if (mazda_aol_buttons_neutral && acc_main_on) {
      mazda_aol_grant();
      mazda_aol_buttons_neutral = false;
    } else {
    }
  }
}

// Observe the ordinary receiver's accepted cruise edge, not a host assertion.
// A healthy inactive observation is required; startup-active and held-active
// packets cannot create or restore an independent token after a disarming fault.
static void mazda_aol_engagement(const CANPacket_t *msg, bool cruise_engaged, bool normal_engagement) {
  if (mazda_aol_enabled && (msg_matches(msg, 0x21CU, 0U, 8U) || msg_matches(msg, 0x44AU, 0U, 8U))) {
    const bool request_current = !mazda_aol_requested ||
      (safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) <= AOL_HOST_REQUEST_TIMEOUT_US);
    const bool healthy = !(mazda_aol_session && !heartbeat_engaged) && acc_main_on && (mazda_aol_sources == 1U) &&
      !brake_pressed && !gas_pressed && !relay_malfunction && !safety_rx_checks_invalid &&
      request_current && aol_rx_healthy() && mazda_aol_current();
    if (!healthy) {
      mazda_aol_cruise_neutral = false;
    } else if (!cruise_engaged) {
      mazda_aol_cruise_neutral = true;
    } else {
      if (normal_engagement && mazda_aol_cruise_neutral) {
        mazda_aol_grant();
      }
      mazda_aol_cruise_neutral = false;
    }
  }
}

static void mazda_aol_tx_commit(const CANPacket_t *msg, bool accepted) {
  if (mazda_aol_enabled && msg_matches(msg, 0x243U, 0U, 8U)) {
    const unsigned int raw_torque = ((msg->data[0] & 15U) << 8U) | msg->data[1];
    const int torque = (int)raw_torque - 2048;
    mazda_aol_owned = accepted && (torque != 0) && ((mazda_aol_permission() & 1U) != 0U);
    mazda_aol_accepted_ts = microsecond_timer_get();
  }
}

static bool mazda_aol_fwd(int bus, int addr) {
  return mazda_aol_enabled && (bus == 2) && ((addr == 0x243) || (addr == 0x440)) &&
    mazda_aol_owned && ((mazda_aol_permission() & 1U) != 0U) &&
    (safety_get_ts_elapsed(microsecond_timer_get(), mazda_aol_accepted_ts) <= AOL_HOST_REQUEST_TIMEOUT_US);
}
