#pragma once

static bool blended_aol_enabled = false;
static bool blended_aol_long = false;
static bool blended_aol_hda2 = false;
static bool blended_adrv_counter_seen = false;
static uint8_t blended_adrv_counter = 0U;
static bool blended_camera_seen = false;
static uint8_t blended_camera_data[24] = {0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
static uint32_t blended_camera_ts = 0U;
static bool blended_camera_previous_seen = false;
static uint8_t blended_camera_previous_data[24] = {0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
static uint32_t blended_camera_previous_ts = 0U;
static bool blended_camera_stale_echo = false;
static bool blended_aol_main_seen = false;
static bool blended_aol_main = false;
static uint32_t blended_aol_main_ts = 0U;
static bool blended_aol_token = false;
static bool blended_aol_claimed = false;
static bool blended_aol_session = false;
static bool blended_aol_request_seen = false;
static bool blended_aol_neutral[3] = {false, false, false};
static bool blended_aol_pressed[3] = {false, false, false};
static uint32_t blended_aol_source_ts[3] = {0U, 0U, 0U};
static uint32_t blended_aol_token_ts = 0U;
static unsigned int blended_aol_token_source = 3U;
static bool blended_aol_counter_seen = false;
static uint8_t blended_aol_counter = 0U;
static bool blended_aol_owned[4] = {false, false, false, false};
static uint32_t blended_aol_tx_ts[4] = {0U, 0U, 0U, 0U};

static void blended_aol_clear(void) {
  blended_camera_stale_echo = false;
  blended_aol_main_seen = false;
  blended_aol_main = false;
  blended_aol_main_ts = 0U;
  blended_aol_counter_seen = false;
  blended_aol_counter = 0U;
  blended_adrv_counter_seen = false;
  blended_adrv_counter = 0U;
  blended_aol_owned[3] = false;
  blended_aol_tx_ts[3] = 0U;
  blended_aol_token = false;
  blended_aol_claimed = false;
  blended_aol_session = false;
  blended_aol_request_seen = false;
  blended_aol_token_ts = 0U;
  blended_aol_token_source = 3U;
  aol_host_axis_mask = 0U;
  for (unsigned int i = 0U; i < 3U; i++) {
    blended_aol_neutral[i] = false;
    blended_aol_pressed[i] = false;
    blended_aol_source_ts[i] = 0U;
    blended_aol_owned[i] = false;
    blended_aol_tx_ts[i] = 0U;
  }
}

static void blended_aol_reset(void) {
  blended_aol_enabled = false;
  blended_aol_long = false;
  blended_aol_hda2 = false;
  blended_camera_seen = false;
  blended_camera_ts = 0U;
  blended_camera_previous_seen = false;
  blended_camera_previous_ts = 0U;
  blended_aol_counter_seen = false;
  blended_aol_counter = 0U;
  blended_aol_clear();
}

static void blended_aol_host_request(uint8_t mask) {
  blended_aol_request_seen |= mask != 0U;
}

static uint8_t blended_aol_request_mask(void) {
  uint8_t result = 0U;
  const uint32_t now = microsecond_timer_get();
  const bool token_expired = blended_aol_token && !blended_aol_claimed &&
    (safety_get_ts_elapsed(now, blended_aol_token_ts) > AOL_HOST_REQUEST_TIMEOUT_US);
  const bool request_expired = blended_aol_request_seen &&
    (safety_get_ts_elapsed(now, aol_host_request_ts) > AOL_HOST_REQUEST_TIMEOUT_US);
  if (relay_malfunction || safety_rx_checks_invalid || token_expired || request_expired ||
      (blended_aol_session && !heartbeat_engaged)) {
    blended_aol_clear();
  } else if (heartbeat_engaged) {
    blended_aol_session = true;
    if (blended_aol_long && controls_allowed && blended_aol_main_seen && blended_aol_main &&
        (safety_get_ts_elapsed(now, blended_aol_main_ts) <= 100000U) && ((aol_host_axis_mask & 1U) != 0U)) {
      // Normal paired enable can seed lateral; a lateral token never enables long.
      blended_aol_token = true;
      blended_aol_token_source = 0U;
      blended_aol_token_ts = now;
    }
    result = aol_host_axis_mask & (blended_aol_long ? 3U : 1U);
    if ((result != 0U) && blended_aol_token) {
      blended_aol_claimed = true;
    }
  } else {
  }
  return result;
}

static uint8_t blended_aol_permission_mask(void) {
  uint8_t result = 0U;
  const bool source_current = (blended_aol_token_source < 3U) &&
    (safety_get_ts_elapsed(microsecond_timer_get(), blended_aol_source_ts[blended_aol_token_source]) <= 300000U);
  if (!aol_rx_healthy() || (blended_aol_token && !source_current)) {
    blended_aol_clear();
  } else if (((blended_aol_request_mask() & 1U) != 0U) && (controls_allowed || blended_aol_token)) {
    result = 1U;
  } else {
  }
  if (blended_aol_long) {
    const bool main_current = blended_aol_main_seen && blended_aol_main &&
      (safety_get_ts_elapsed(microsecond_timer_get(), blended_aol_main_ts) <= 100000U);
    if (!main_current) {
      blended_aol_clear();
      result = 0U;
    } else if (aol_rx_healthy() && controls_allowed && !brake_pressed && !gas_pressed &&
               ((blended_aol_request_mask() & 2U) != 0U)) {
      result |= 2U;
    } else {
    }
  } else {
  }
  return result;
}

static void blended_aol_rx(const CANPacket_t *msg) {
  if (blended_aol_hda2 && msg_matches(msg, 0x2A4U, 2U, 24U)) {
    if (blended_camera_seen && (msg->data[2] != blended_camera_data[2])) {
      for (unsigned int i = 0U; i < 24U; i++) {
        blended_camera_previous_data[i] = blended_camera_data[i];
      }
      blended_camera_previous_seen = true;
      blended_camera_previous_ts = blended_camera_ts;
    }
    for (unsigned int i = 0U; i < 24U; i++) {
      blended_camera_data[i] = msg->data[i];
    }
    blended_camera_seen = true;
    blended_camera_ts = microsecond_timer_get();
  }
  if (blended_aol_enabled) {
    const unsigned int pt = blended_aol_hda2 ? 1U : 0U;
    const uint32_t now = microsecond_timer_get();
    if (blended_aol_long && msg_matches(msg, 0x394U, pt, 8U)) {
      blended_aol_main_seen = true;
      blended_aol_main = ((msg->data[5] >> 3U) & 3U) == 0U;
      blended_aol_main_ts = now;
      if (!blended_aol_main) {
        blended_aol_token = false;
        blended_aol_claimed = false;
      }
    }
    unsigned int source = 3U;
    bool pressed = false;
    if ((msg->bus == pt) && (msg->addr == 0x4F1U) && (GET_LEN(msg) == 4U)) {
      if ((msg->data[0] & 7U) == 4U) {
        // Mixed LONG Pause/Resume still clears ordinary/LONG authority in the
        // cruise hook. Retain only an already claimed, current lateral token.
        // CANCEL cannot arm, renew a request/gesture lease, or clear an inhibit.
        const bool source_current = (blended_aol_token_source < 3U) &&
          (safety_get_ts_elapsed(now, blended_aol_source_ts[blended_aol_token_source]) <= 300000U);
        const bool retain_lateral = blended_aol_long && !GET_BIT(msg, 3U) &&
          blended_aol_token && blended_aol_claimed && blended_aol_session && heartbeat_engaged &&
          blended_aol_main_seen && blended_aol_main &&
          (safety_get_ts_elapsed(now, blended_aol_main_ts) <= 100000U) && source_current &&
          blended_aol_request_seen && ((aol_host_axis_mask & 1U) != 0U) &&
          (safety_get_ts_elapsed(now, aol_host_request_ts) <= AOL_HOST_REQUEST_TIMEOUT_US) &&
          aol_rx_healthy() && !relay_malfunction && !safety_rx_checks_invalid;
        if (!retain_lateral) {
          blended_aol_clear();
        }
        // A simultaneous MAIN is not an arming gesture. The advancing physical
        // source remains observable without changing its token or host lease.
        blended_aol_pressed[0] = GET_BIT(msg, 3U);
        blended_aol_source_ts[0] = now;
      } else {
        source = 0U;
        pressed = GET_BIT(msg, 3U);
      }
    } else if ((msg->bus == pt) && (msg->addr == 0x391U) && (GET_LEN(msg) == 8U)) {
      source = 1U;
      pressed = GET_BIT(msg, 4U);
    } else if ((msg->bus == pt) && (msg->addr == 0x50CU) && (GET_LEN(msg) == 8U)) {
      source = 2U;
      pressed = GET_BIT(msg, 56U);
    } else {
    }
    if (source < 3U) {
      const bool expired = (blended_aol_source_ts[source] == 0U) ||
        (safety_get_ts_elapsed(now, blended_aol_source_ts[source]) > 300000U);
      if (expired) {
        blended_aol_neutral[source] = false;
        blended_aol_pressed[source] = pressed;
      }
      if (!pressed) {
        blended_aol_neutral[source] = true;
      } else if (blended_aol_neutral[source] && !blended_aol_pressed[source] && (aol_host_axis_mask == 0U)) {
        blended_aol_token = true;
        blended_aol_claimed = false;
        blended_aol_token_ts = now;
        blended_aol_token_source = source;
      } else {
      }
      blended_aol_pressed[source] = pressed;
      blended_aol_source_ts[source] = now;
    }
  }
}

static void blended_aol_configure(uint16_t param) {
  blended_aol_reset();
#ifdef ALLOW_DEBUG
  blended_aol_long = (param == 0x2004U) || (param == 0x2014U);
#endif
  blended_aol_hda2 = (param == 0x2010U) || (param == 0x2014U);
  blended_aol_enabled = ((param == 0x2000U) || (param == 0x2010U) || blended_aol_long) && ((unsigned int)alternative_experience == 32U);
  if (blended_aol_enabled) {
    static const AolSafetyPolicy policy = {
      .reset = blended_aol_reset,
      .host_request = blended_aol_host_request,
      .request_mask = blended_aol_request_mask,
      .permission_mask = blended_aol_permission_mask,
      .rx_invalid = blended_aol_clear,
    };
    aol_policy = &policy;
  }
}

static bool blended_aol_integrity(const CANPacket_t *msg) {
  bool valid = true;
  blended_camera_stale_echo = false;
  const unsigned int steering = blended_aol_hda2 ? 0x50U : 0x340U;
  if (blended_aol_hda2 && ((msg->addr == 0x50U) || (msg->addr == 0x51U) || (msg->addr == 0x2A4U))) {
    unsigned int length = 24U;
    if (msg->addr == 0x50U) {
      length = 16U;
    } else if (msg->addr == 0x51U) {
      length = 32U;
    } else {
      // The remaining admitted camera message retains its 24-byte shape.
    }
    const uint32_t xor_out = (length == 16U) ? 0x041DU : 0U;
    valid = msg_matches(msg, msg->addr, 0U, length) &&
      (GET_BYTES_LE(msg, 0, 2) == (hyundai_common_canfd_compute_checksum(msg) ^ xor_out));
    if (valid && (msg->addr == 0x50U) && blended_aol_counter_seen) {
      valid = msg->data[2] == (uint8_t)(blended_aol_counter + 1U);
    }
    if (valid && (msg->addr == 0x51U) && blended_adrv_counter_seen) {
      valid = msg->data[2] == (uint8_t)(blended_adrv_counter + 1U);
    }
    if (valid && (msg->addr == 0x50U)) {
      blended_aol_counter = msg->data[2];
      blended_aol_counter_seen = true;
    }
    if (valid && (msg->addr == 0x51U)) {
      blended_adrv_counter = msg->data[2];
      blended_adrv_counter_seen = true;
    }
    if (valid && (msg->addr == 0x2A4U)) {
      const uint32_t now = microsecond_timer_get();
      const bool camera_current = blended_camera_seen &&
        (safety_get_ts_elapsed(now, blended_camera_ts) <= 500000U);
      valid = camera_current && (msg->data[2] == blended_camera_data[2]) && (msg->data[7] == 0U);
      bool previous = camera_current && blended_camera_previous_seen && (msg->data[7] == 0U) &&
        (safety_get_ts_elapsed(now, blended_camera_previous_ts) <= 500000U) &&
        (msg->data[2] == blended_camera_previous_data[2]);
      for (unsigned int i = 3U; i < 24U; i++) {
        if (i != 7U) {
          valid &= msg->data[i] == blended_camera_data[i];
          previous &= msg->data[i] == blended_camera_previous_data[i];
        }
      }
      blended_camera_stale_echo = !valid && previous && aol_rx_healthy();
    }
  } else if (blended_aol_enabled && (msg->addr == steering)) {
    valid = msg_matches(msg, steering, 0U, 8U);
    uint8_t counter = 0U;
    if (valid) {
      uint8_t crc = 0x22U;
      for (unsigned int i = 1U; i < 8U; i++) {
        crc = crc8_update(crc, msg->data[i], 0x1DU);
      }
      valid = (crc ^ 0xDFU) == msg->data[0];
      counter = (msg->data[4] >> 4U) & 0xFU;
    } else {
    }
    if (valid && blended_aol_counter_seen) {
      const unsigned int mask = 15U;
      valid = counter == ((blended_aol_counter + 1U) & mask);
    }
    if (valid) {
      blended_aol_counter = counter;
      blended_aol_counter_seen = true;
    }
  } else {
    // Messages outside the owned integrity paths retain the existing verdict.
  }
  if (!valid && !blended_camera_stale_echo) {
    blended_aol_clear();
  }
  return valid;
}

static void blended_aol_tx(const CANPacket_t *msg, bool accepted) {
  if (blended_aol_enabled) {
    // A queued prior camera snapshot is denied without renewing its last accepted output lease.
    bool retain_camera_ownership = false;
    if (!accepted && blended_camera_stale_echo && msg_matches(msg, 0x2A4U, 0U, 24U)) {
      retain_camera_ownership = blended_aol_permission_mask() != 0U;
    }
    bool accepted_current = accepted;
    const uint32_t addresses[4] = {blended_aol_hda2 ? 0x50U : 0x340U, blended_aol_hda2 ? 0x2A4U : 0x364U, 0x485U, 0x340U};
    const unsigned int count = (blended_aol_hda2 && blended_aol_long) ? 4U : 3U;
    if ((msg->addr == addresses[0]) && (msg->bus == 0U)) {
      const bool active = GET_BIT(msg, blended_aol_hda2 ? 52U : 27U);
      if (!accepted || !active) {
        for (unsigned int i = 0U; i < count; i++) {
          blended_aol_owned[i] = false;
        }
      }
      accepted_current &= active;
    }
    for (unsigned int i = 0U; i < count; i++) {
      const unsigned int bus = blended_aol_hda2 && (i >= 2U) ? 1U : 0U;
      if ((msg->addr == addresses[i]) && (msg->bus == bus) && !retain_camera_ownership) {
        blended_aol_owned[i] = accepted_current && ((i == 0U) || (blended_aol_owned[0] &&
          safety_get_ts_elapsed(microsecond_timer_get(), blended_aol_tx_ts[0]) <= AOL_HOST_REQUEST_TIMEOUT_US)) && aol_rx_healthy() && (controls_allowed || (blended_aol_permission_mask() != 0U));
        blended_aol_tx_ts[i] = microsecond_timer_get();
      }
    }
  }
}

static bool blended_aol_fwd(int bus, int addr) {
  bool blocked = blended_aol_long && !blended_aol_enabled && (bus == 2) &&
    ((addr == (blended_aol_hda2 ? 0x50 : 0x340)) ||
     (addr == (blended_aol_hda2 ? 0x2A4 : 0x364)) || (!blended_aol_hda2 && (addr == 0x485)));
  if (blended_aol_enabled && (bus == 2) && aol_rx_healthy() && (controls_allowed || (blended_aol_permission_mask() != 0U))) {
    const uint32_t addresses[4] = {blended_aol_hda2 ? 0x50U : 0x340U, blended_aol_hda2 ? 0x2A4U : 0x364U, 0x485U, 0x340U};
    const unsigned int count = blended_aol_hda2 ? 2U : 3U;
    for (unsigned int i = 0U; i < count; i++) {
      blocked |= (addr == (int)addresses[i]) && blended_aol_owned[i] &&
        (safety_get_ts_elapsed(microsecond_timer_get(), blended_aol_tx_ts[i]) <= AOL_HOST_REQUEST_TIMEOUT_US);
    }
  }
  return blocked;
}
