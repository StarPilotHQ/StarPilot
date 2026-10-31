#pragma once
#include "opendbc/safety/modes/ford_param.h"

static bool ford_aol_manual = false;
static bool ford_aol_enabled = false;
static bool ford_aol_token = false;
static bool ford_aol_main_available = false;
static bool ford_aol_main_off = false;
static bool ford_aol_token_claimed = false;
static uint32_t ford_aol_token_ts = 0U;
static bool ford_aol_main_neutral = false;
static bool ford_aol_main_previous = false;
static bool ford_aol_button_neutral = false;
static bool ford_aol_button_previous = false;
static bool ford_aol_session = false;
static bool ford_aol_request_seen = false;
static uint16_t ford_aol_param = 0U;

// CAN FD (not the Mondeo's new port) keeps the cluster display while AOL owns steering
static bool ford_aol_retained_display_profile(void) {
  const uint16_t base = ford_aol_param & 0xFFFEU;
  return base == 2U;
}
static bool ford_aol_drive = false;
static uint8_t ford_aol_eps = 0U;
static bool ford_aol_lka_ready = false;
// Keep steering and display ownership separate without adding state storage.
#define FORD_AOL_STEERING_OWNED 1U
#define FORD_AOL_DISPLAY_OWNED 2U
static uint8_t ford_aol_owned = 0U;
static uint32_t ford_aol_accepted_ts = 0U;

// The host sends the steering actuator every frame, active or not. While it is sending, the
// camera's copy stays off the bus, so the PSCM hears one sender, as it would under static
// blocking. A rejected host frame is a missed frame, as without AOL: letting the camera's
// inactive frame through in its place, mid-steer, makes the PSCM ramp out and fault. Only a
// host that has stopped sending gives the actuator back to the camera.
static bool ford_aol_host_actuator_seen = false;
static uint32_t ford_aol_host_actuator_ts = 0U;

static bool ford_aol_host_actuator_current(void) {
  return ford_aol_host_actuator_seen &&
    (safety_get_ts_elapsed(microsecond_timer_get(), ford_aol_host_actuator_ts) <= AOL_HOST_REQUEST_TIMEOUT_US);
}

// An accepted inactive actuator plus a truthful OFF/standby display can own
// cluster continuity without owning steering. Keep the existing command lease.
#define FORD_AOL_NEUTRAL_STEERING_SEEN 0x80U
static bool ford_aol_neutral_request_seen = false;
static uint32_t ford_aol_neutral_steering_ts = 0U;
static uint8_t ford_aol_neutral_display_mask = 0U;
static uint32_t ford_aol_neutral_ui_ts = 0U;

static void ford_aol_neutral_reset(void) {
  ford_aol_neutral_steering_ts = 0U;
  ford_aol_neutral_display_mask = 0U;
  ford_aol_neutral_ui_ts = 0U;
}

static bool ford_aol_neutral_sources_current(void) {
  return ford_aol_enabled && ford_aol_retained_display_profile() && ((ford_aol_param & 1U) != 0U) &&
    !relay_malfunction && ford_aol_drive && (ford_aol_eps == 0U) && ford_aol_lka_ready &&
    (ford_aol_main_available || ford_aol_main_off) &&
    ((aol_host_axis_mask == 0U) || ford_aol_main_off || heartbeat_engaged) && ford_aol_neutral_request_seen &&
    (safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) <= AOL_HOST_REQUEST_TIMEOUT_US) && aol_rx_healthy();
}

static bool ford_aol_neutral_steering_current(void) {
  return ford_aol_neutral_sources_current() && ((ford_aol_neutral_display_mask & FORD_AOL_NEUTRAL_STEERING_SEEN) != 0U) &&
    (safety_get_ts_elapsed(microsecond_timer_get(), ford_aol_neutral_steering_ts) <= AOL_HOST_REQUEST_TIMEOUT_US);
}

static bool ford_aol_neutral_current(void) {
  return ford_aol_neutral_steering_current() && ((ford_aol_neutral_display_mask & 3U) != 0U) &&
    (safety_get_ts_elapsed(microsecond_timer_get(), ford_aol_neutral_ui_ts) <= AOL_HOST_REQUEST_TIMEOUT_US);
}

static bool ford_aol_neutral_packet(const CANPacket_t *msg) {
  bool neutral = false;
  if (msg->addr == 0x18AU) {
    neutral = ((msg->data[5] >> 2U) & 7U) <= 1U;  // TJA Off/Standby only
  } else if (msg->addr == 0x3D8U) {
    const unsigned int lines = msg->data[6] >> 3U;
    neutral = ((lines == 0U) || (lines == 30U)) && (((msg->data[6] >> 1U) & 3U) == 0U);
  } else {
    // Other packet types do not supply neutral display state.
  }
  return neutral;
}


static void ford_aol_clear(void) {
  // A revoked session stays revoked, but must not repeatedly erase neutral
  // physical input history needed for the next real driver activation.
  ford_aol_session = false;
  ford_aol_token = false;
  ford_aol_token_claimed = false;
  ford_aol_token_ts = 0U;
  ford_aol_main_neutral = false;
  ford_aol_button_neutral = false;
  ford_aol_owned = 0U;
}

static void ford_aol_rx_invalid(void) {
  ford_aol_clear();
  ford_aol_neutral_reset();
}

static void ford_aol_reset(void) {
  ford_aol_neutral_request_seen = false;
  ford_aol_neutral_reset();
  ford_aol_main_off = false;
  ford_aol_clear();
  ford_aol_enabled = false;
  ford_aol_main_available = false;
  ford_aol_main_previous = false;
  ford_aol_button_previous = false;
  ford_aol_session = false;
  ford_aol_request_seen = false;
  ford_aol_param = 0U;
  ford_aol_drive = false;
  ford_aol_eps = 0U;
  ford_aol_lka_ready = false;
  ford_aol_accepted_ts = 0U;
  ford_aol_host_actuator_seen = false;
  ford_aol_host_actuator_ts = 0U;
}

// Fresh physical MAIN/LKAS or ordinary cruise activation begins a new session.
// pandad reads status before writing its request and engagement heartbeat; the
// old session's disengaged heartbeat is not a loss of this unclaimed token.
// Original token expiry and current heartbeat/request/RX guards remain in force.
static void ford_aol_arm(void) {
  ford_aol_token = true;
  ford_aol_token_claimed = false;
  ford_aol_token_ts = microsecond_timer_get();
  ford_aol_session = false;
}

static void ford_aol_host_request(uint8_t mask) {
  ford_aol_neutral_request_seen = true;
  ford_aol_request_seen |= mask != 0U;
  if (ford_aol_retained_display_profile() && ((mask & 1U) == 0U)) {
    // Bridge Off UI before the next inactive actuator without renewing its accepted-command deadline.
    if ((mask == 0U) && ford_aol_neutral_sources_current() && ((ford_aol_owned & FORD_AOL_STEERING_OWNED) != 0U) &&
        (safety_get_ts_elapsed(microsecond_timer_get(), ford_aol_accepted_ts) <= AOL_HOST_REQUEST_TIMEOUT_US)) {
      ford_aol_neutral_steering_ts = ford_aol_accepted_ts;
      ford_aol_neutral_display_mask |= FORD_AOL_NEUTRAL_STEERING_SEEN;
    }
    ford_aol_owned &= FORD_AOL_STEERING_OWNED;
  }
}

static uint8_t ford_aol_request_mask(void) {
  uint8_t result = 0U;
  const bool expired = ford_aol_request_seen &&
    (safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) > AOL_HOST_REQUEST_TIMEOUT_US);
  const bool token_expired = ford_aol_token && !ford_aol_token_claimed &&
    (safety_get_ts_elapsed(microsecond_timer_get(), ford_aol_token_ts) > AOL_HOST_REQUEST_TIMEOUT_US);
  const bool long_profile = (ford_aol_param & 1U) != 0U;
  const bool lost_session = ford_aol_session && !heartbeat_engaged;
  if (lost_session && (aol_host_axis_mask != 0U) && !ford_aol_main_off) { ford_aol_neutral_reset(); }
  if (!aol_rx_healthy() || relay_malfunction || expired || lost_session) {
    // Ordinary LONG cannot revive solely because a stale source/host recovers.
    // A fresh real PCM cruise activation is required after this withdrawal.
    if (long_profile) { controls_allowed = false; }
    ford_aol_clear();
  } else {
    if (token_expired) {
      // Expiry of an unclaimed lateral token must not create a LONG grant or
      // invalidate an independent, still-current ordinary LONG activation.
      ford_aol_clear();
    }
    if (heartbeat_engaged) {
      ford_aol_session = true;
      result = aol_host_axis_mask & (long_profile ? 3U : 1U);
      if (ford_aol_token && (((result & 1U) != 0U) || (((result & 2U) != 0U) && controls_allowed))) {
        ford_aol_token_claimed = true;
      }
    }
  }
  return result;
}

static uint8_t ford_aol_permission_mask(void) {
  uint8_t result = 0U;
  if (ford_aol_eps >= 2U) {
    controls_allowed = false;
    ford_aol_clear();
  }
  const uint16_t base = ford_aol_param & 0xFFFEU;
  const bool needs_lka = (base == 2U) || (base == 10U) || (base == 12U);
  const uint8_t requested = ford_aol_request_mask();
  if (((requested & 1U) != 0U) && (controls_allowed || ford_aol_token) &&
      ford_aol_main_available && ford_aol_drive && (ford_aol_eps == 0U) && (!needs_lka || ford_aol_lka_ready)) {
    result = 1U;
  }
  if (((requested & 2U) != 0U) && ((ford_aol_param & 1U) != 0U) && controls_allowed &&
      ford_aol_main_available && ford_aol_drive && (ford_aol_eps < 2U) &&
      !brake_pressed && !gas_pressed && !gas_pressed_prev) {
    result |= 2U;
  }
  if ((base == 2U) && ((result & 1U) == 0U)) {
    ford_aol_owned &= FORD_AOL_STEERING_OWNED;
  }
  return result;
}

static void ford_aol_rx(const CANPacket_t *msg) {
  if (ford_aol_enabled && (msg->bus == 0U)) {
    if (msg_matches(msg, 0x165U, 0U, 8U)) {
      const unsigned int state = msg->data[1] & 7U;
      const bool main_available = (state == 3U) || (state == 4U) || (state == 5U);
      ford_aol_main_available = main_available;
      ford_aol_main_off = state == 0U;
      if (!main_available) {
        ford_aol_clear();
        ford_aol_main_neutral = true;
      } else if (ford_aol_main_neutral && !ford_aol_main_previous && (aol_host_axis_mask == 0U)) {
        ford_aol_arm();
      } else {
      }
      ford_aol_main_previous = main_available;
      if ((state == 1U) || (state == 2U)) {
        ford_aol_clear();
      }
    } else if (msg_matches(msg, 0x83U, 0U, 8U)) {
      const bool button = GET_BIT(msg, 40U);
      if (!button) {
        ford_aol_button_neutral = true;
      } else if (ford_aol_button_neutral && !ford_aol_button_previous && (aol_host_axis_mask == 0U)) {
        ford_aol_arm();
      } else {
      }
      ford_aol_button_previous = button;
      if (GET_BIT(msg, 8U) || GET_BIT(msg, 24U)) {
        ford_aol_clear();
      }
    } else if (msg_matches(msg, 0x82U, 0U, 8U)) {
      ford_aol_eps = msg->data[1] & 3U;
      if (ford_aol_eps >= 2U) {
        controls_allowed = false;
        ford_aol_clear();
      }
    } else if (msg_matches(msg, 0x3CCU, 0U, 8U)) {
      const unsigned int state = msg->data[2] & 7U;
      ford_aol_lka_ready = ((ford_aol_param & 0xFFFEU) == 12U) ?
        ((((msg->data[0] >> 4U) & 3U) == 3U) && ((msg->data[0] & 0x40U) == 0U)) :
        ((state == 1U) || (state == 2U) || (state == 3U));
    } else if (ford_aol_manual && msg_matches(msg, 0x3C3U, 0U, 8U)) {
      ford_aol_drive = !GET_BIT(msg, 9U);
    } else if (!ford_aol_manual) {
      const uint16_t base = ford_aol_param & 0xFFFEU;
      const uint16_t gear_addr = (base == 8U) ? 0x230U : ((base == 10U) ? 0x5AU : 0x176U);
      if (msg_matches(msg, gear_addr, 0U, 8U)) {
        const unsigned int gear = (base == 8U) ? ((msg->data[1] >> 1U) & 15U) :
                                  ((base == 10U) ? (msg->data[6] & 15U) : (msg->data[3] & 15U));
        ford_aol_drive = (gear == 3U) || (gear == 4U) || (gear == 5U);
      }
    } else {
      // Manual profiles consume only the dedicated reverse lamp source.
    }
    if (!ford_aol_drive || (ford_aol_eps != 0U) || !ford_aol_lka_ready ||
        (!ford_aol_main_available && !ford_aol_main_off)) {
      ford_aol_neutral_reset();
    }
    // A transient physical veto revokes only display ownership, even when a
    // healthy packet follows in the same RX batch. AOL intent is retained.
    if (ford_aol_retained_display_profile() &&
        (!ford_aol_main_available || !ford_aol_drive || (ford_aol_eps != 0U) || !ford_aol_lka_ready)) {
      ford_aol_owned &= FORD_AOL_STEERING_OWNED;
    }
  }
}

// A real ordinary cruise rising edge can arm retained lateral intent without
// requiring a historical MAIN/LKAS cycle. This never grants ordinary controls
// or longitudinal permission; the current request/heartbeat/RX gates still own TX.
// Accelerator override does not erase this lateral intent. The independent
// longitudinal permission mask continues to require released accelerator/brake.
static void ford_aol_full_engagement(bool cruise_rising) {
  if (ford_aol_enabled && cruise_rising && controls_allowed && !brake_pressed &&
      !relay_malfunction && aol_rx_healthy() && ford_aol_main_available && ford_aol_drive && (ford_aol_eps == 0U)) {
    ford_aol_arm();
  }
}

static bool ford_aol_param_valid(uint16_t param) {
  bool result = false;
  switch (ford_profile_word(param) & ~256U) {
    case 0U: case 1U: case 2U: case 3U:
    case 8U: case 9U: case 10U: case 11U: case 12U: case 13U:
      result = true;
      break;
    default:
      break;
  }
  return result;
}

static void ford_aol_configure(uint16_t param) {
  ford_aol_reset();
  ford_aol_enabled = ford_aol_param_valid(param) && ((unsigned int)alternative_experience == 32U);
  ford_aol_manual = (param & 256U) != 0U;
  ford_aol_param = ford_profile_word(param) & ~256U;
  if (ford_aol_enabled) {
    static const AolSafetyPolicy policy = {
      .reset = ford_aol_reset,
      .host_request = ford_aol_host_request,
      .request_mask = ford_aol_request_mask,
      .permission_mask = ford_aol_permission_mask,
      .rx_invalid = ford_aol_rx_invalid,
    };
    aol_policy = &policy;
  }
}

static unsigned int ford_aol_replacement(void) {
  const uint16_t base = ford_aol_param & 0xFFFEU;
  return (base == 12U) ? 0x3CAU : (((base == 2U) || (base == 10U)) ? 0x3D6U : 0x3D3U);
}

static void ford_aol_tx(const CANPacket_t *msg, bool accepted) {
  if (ford_aol_enabled && (msg->bus == 0U) && (msg->addr == ford_aol_replacement())) {
    const bool active = (msg->addr == 0x3CAU) ? (((msg->data[0] >> 5U) == 2U) || ((msg->data[0] >> 5U) == 4U)) :
                       ((msg->addr == 0x3D3U) ? (((msg->data[4] >> 2U) & 7U) != 0U) :
                                               (((msg->data[0] >> 4U) & 7U) != 0U));
    if (accepted && !active && ford_aol_neutral_sources_current()) {
      if (!ford_aol_neutral_steering_current()) { ford_aol_neutral_display_mask = 0U; }
      ford_aol_neutral_steering_ts = microsecond_timer_get();
      ford_aol_neutral_display_mask |= FORD_AOL_NEUTRAL_STEERING_SEEN;
    } else {
      ford_aol_neutral_reset();
    }
    const bool retained_display = ford_aol_retained_display_profile() && ((ford_aol_owned & FORD_AOL_DISPLAY_OWNED) != 0U) &&
      (safety_get_ts_elapsed(microsecond_timer_get(), ford_aol_accepted_ts) <= AOL_HOST_REQUEST_TIMEOUT_US);
    const uint8_t permission = (accepted && (active || retained_display)) ? ford_aol_permission_mask() : 0U;
    const bool steering_owned = accepted && active && (permission != 0U) && !relay_malfunction;
    const bool display_owned = steering_owned || (accepted && retained_display && ((permission & 1U) != 0U) && !relay_malfunction);
    ford_aol_owned = (steering_owned ? FORD_AOL_STEERING_OWNED : 0U) | (display_owned ? FORD_AOL_DISPLAY_OWNED : 0U);
    ford_aol_accepted_ts = microsecond_timer_get();
    ford_aol_host_actuator_seen = true;
    ford_aol_host_actuator_ts = ford_aol_accepted_ts;
  }
  if ((msg->bus == 0U) && ((msg->addr == 0x18AU) || (msg->addr == 0x3D8U))) {
    if (accepted && ford_aol_neutral_steering_current() && ford_aol_neutral_packet(msg)) {
      ford_aol_neutral_ui_ts = microsecond_timer_get();
      ford_aol_neutral_display_mask |= (msg->addr == 0x18AU) ? 1U : 2U;
    } else if (!accepted) {
      ford_aol_neutral_reset();
    } else {
      // No fresh neutral proof; retain the existing lease without renewing it.
    }
  }
}

static bool ford_aol_fwd(int bus, int addr) {
  bool blocked = false;
  if (ford_aol_enabled && (bus == 2) && (ford_aol_owned != 0U) && (ford_aol_permission_mask() != 0U) &&
      (safety_get_ts_elapsed(microsecond_timer_get(), ford_aol_accepted_ts) <= AOL_HOST_REQUEST_TIMEOUT_US)) {
    const bool display_permitted = !ford_aol_retained_display_profile() || ((ford_aol_permission_mask() & 1U) != 0U);
    blocked = (((ford_aol_owned & FORD_AOL_STEERING_OWNED) != 0U) && (addr == (int)ford_aol_replacement())) ||
      (((ford_aol_owned & FORD_AOL_DISPLAY_OWNED) != 0U) && display_permitted && ((addr == 0x3D8) || (addr == 0x18A)));
  }
  if (ford_aol_enabled && (bus == 2) && (addr == (int)ford_aol_replacement()) && ford_aol_host_actuator_current()) {
    blocked = true;
  }
  if (ford_aol_neutral_current() && (bus == 2)) {
    blocked |= ((addr == 0x18A) && ((ford_aol_neutral_display_mask & 1U) != 0U)) ||
               ((addr == 0x3D8) && ((ford_aol_neutral_display_mask & 2U) != 0U));
  }
  return blocked;
}

// An unowned extended CAN-FD display must remain stock, including a rejected first
// steering command. This cannot grant steering or longitudinal permission.
static bool ford_aol_display_tx_allowed(const CANPacket_t *msg) {
  bool allowed = true;
  if (ford_aol_enabled && ford_aol_retained_display_profile()) {
    allowed = ((ford_aol_owned & FORD_AOL_DISPLAY_OWNED) != 0U) &&
      ((ford_aol_permission_mask() & 1U) != 0U) && !relay_malfunction &&
      (safety_get_ts_elapsed(microsecond_timer_get(), ford_aol_accepted_ts) <= AOL_HOST_REQUEST_TIMEOUT_US);
  }
  return allowed || (ford_aol_neutral_steering_current() && ford_aol_neutral_packet(msg));
}
