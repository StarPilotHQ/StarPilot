#pragma once

static bool tesla_screen_enabled = false;
static bool tesla_screen_brake_disengage = false;
static bool tesla_screen_longitudinal = false;
static bool tesla_screen_initialized = false;
static bool tesla_screen_pressed = false;
static bool tesla_screen_main = false;
static bool tesla_screen_cruise = false;
static bool tesla_screen_drive = false;
static bool tesla_screen_occupant_ready = false;
static bool tesla_screen_belt_latched = false;
static bool tesla_screen_eps_ready = false;
static bool tesla_screen_session = false;
static bool tesla_screen_autopark = false;
static int tesla_screen_override = 0;

static void tesla_screen_clear(void) {
  tesla_screen_override = -1;
  tesla_screen_initialized = false;
  tesla_screen_pressed = false;
}

static void tesla_screen_reset(void) {
  tesla_screen_clear();
  tesla_screen_override = 0;
  tesla_screen_main = false;
  tesla_screen_cruise = false;
  tesla_screen_drive = false;
  tesla_screen_occupant_ready = false;
  tesla_screen_belt_latched = false;
  tesla_screen_eps_ready = false;
  tesla_screen_session = false;
  tesla_screen_autopark = false;
}

static uint8_t tesla_screen_request(void) {
  uint8_t request = 0U;
  const bool expired = safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) > AOL_HOST_REQUEST_TIMEOUT_US;
  if (!aol_rx_healthy() || relay_malfunction || expired) {
    tesla_screen_clear();
  } else if (tesla_screen_session && !heartbeat_engaged) {
    tesla_screen_clear();
    // Clear the ended arm once. Subsequent healthy idle samples must establish
    // a new neutral/press edge; an OFF heartbeat cannot erase it every cycle.
    tesla_screen_session = false;
  } else if (heartbeat_engaged) {
    tesla_screen_session = true;
    request = aol_host_axis_mask;
  } else {
  }
  return request;
}

static bool tesla_screen_physical_ready(void) {
  return tesla_screen_drive && tesla_screen_occupant_ready && tesla_screen_eps_ready &&
    !steering_disengage && !tesla_screen_autopark;
}

static uint8_t tesla_screen_permission(void) {
  const uint8_t request = tesla_screen_request();
  uint8_t permission = 0U;
  if (tesla_screen_physical_ready()) {
    const bool lateral = (tesla_screen_override == 1) || ((tesla_screen_override == 0) && tesla_screen_main);
    if (lateral && !(tesla_screen_brake_disengage && brake_pressed)) {
      permission = request & 1U;
    }
    if (tesla_screen_longitudinal && tesla_screen_belt_latched && controls_allowed) {
      permission |= request & 2U;
    }
  }
  return permission;
}

static void tesla_screen_optional_rx(const CANPacket_t *msg) {
  if (tesla_screen_enabled && msg_matches(msg, 0x3DFU, 1U, 8U)) {
    const bool pressed = msg->data[3] == 3U;
    if (tesla_screen_initialized && pressed && !tesla_screen_pressed && aol_rx_healthy() &&
        tesla_screen_physical_ready() && !(tesla_screen_brake_disengage && brake_pressed)) {
      const bool lateral = (tesla_screen_override == 1) || ((tesla_screen_override == 0) && tesla_screen_main);
      tesla_screen_override = lateral ? -1 : 1;
    }
    tesla_screen_initialized = true;
    tesla_screen_pressed = pressed;
  }
}

static void tesla_screen_rx(const CANPacket_t *msg, bool autopark) {
  if (tesla_screen_enabled) {
    tesla_screen_autopark = autopark;
    if (msg_matches(msg, 0x118U, 0U, 8U)) {
      const unsigned int gear = (msg->data[2] >> 5U) & 7U;
      tesla_screen_drive = gear == 4U;
      // Known Reverse/Neutral inhibit output without creating a new arm.
      if (!tesla_screen_drive && (gear != 2U) && (gear != 3U)) { tesla_screen_clear(); }
    }
    if (msg_matches(msg, 0x311U, 0U, 7U)) {
      tesla_screen_belt_latched = GET_BIT(msg, 13U);
      tesla_screen_occupant_ready = !GET_BIT(msg, 28U);
      if (!tesla_screen_occupant_ready) { tesla_screen_clear(); }
    }
    if (msg_matches(msg, 0x370U, 0U, 8U)) {
      const unsigned int status = msg->data[6] >> 5U;
      tesla_screen_eps_ready = (status == 1U) || (status == 2U);
      // Isolated EAC_INHIBITED withdraws steering until a fresh ready sample.
      // Permanent faults and a physical steering disengage still clear intent.
      if ((status > 2U) || steering_disengage) { tesla_screen_clear(); }
    }
    if (msg_matches(msg, 0x286U, 0U, 8U)) {
      const unsigned int state = (msg->data[1] >> 4U) & 7U;
      const bool cruise = ((state == 2U) || (state == 3U) || (state == 4U) || (state == 6U) || (state == 7U)) && !tesla_screen_autopark;
      const bool main_available = ((state == 1U) || cruise) && !tesla_screen_autopark;
      if (cruise && !tesla_screen_cruise) {
        tesla_screen_override = 0;
      }
      if (tesla_screen_main && !main_available && !brake_pressed) {
        tesla_screen_override = -1;
      }
      tesla_screen_main = main_available;
      tesla_screen_cruise = cruise;
    }
    if ((msg_matches(msg, 0x286U, 0U, 8U) && tesla_screen_autopark) || (tesla_screen_brake_disengage && brake_pressed)) {
      tesla_screen_clear();
    }
  }
}

static bool tesla_screen_param_valid(uint16_t param) {
  param &= ~256U;
  bool valid = (param == 512U) || (param == 1536U);
#ifdef ALLOW_DEBUG
  valid = valid || (param == 513U) || (param == 1537U);
#endif
  return valid;
}

static void tesla_screen_configure(uint16_t param) {
  param &= ~256U;
  tesla_screen_reset();
  tesla_screen_enabled = tesla_screen_param_valid(param);
  tesla_screen_enabled = tesla_screen_enabled && ((unsigned int)alternative_experience == 32U);
  tesla_screen_brake_disengage = (param == 1536U) || (param == 1537U);
  tesla_screen_longitudinal = (param == 513U) || (param == 1537U);
  if (tesla_screen_enabled) {
    static const AolSafetyPolicy policy = {
      .reset = tesla_screen_reset,
      .request_mask = tesla_screen_request,
      .permission_mask = tesla_screen_permission,
      .rx_invalid = tesla_screen_clear,
    };
    aol_policy = &policy;
  }
}
