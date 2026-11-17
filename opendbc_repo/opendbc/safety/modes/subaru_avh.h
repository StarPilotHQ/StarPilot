#pragma once

// Legacy startup AVH only. Never transmit the 0x32B status message.
static uint8_t subaru_avh_data[7][8];
static uint32_t subaru_avh_ts[7];
static bool subaru_avh_seen[7];
static bool subaru_avh_seq[7];
static bool subaru_avh_enabled;
static bool subaru_avh_done;
static unsigned int subaru_avh_count;
static uint32_t subaru_avh_start;
static uint32_t subaru_avh_sent;
static uint32_t subaru_avh_template_ts;
static uint32_t subaru_avh_stable_since;
static bool subaru_avh_stable;

static void subaru_avh_init(bool enabled) {
  subaru_avh_enabled = enabled;
  subaru_avh_done = false;
  subaru_avh_count = 0U;
  subaru_avh_start = microsecond_timer_get();
  subaru_avh_sent = 0U;
  subaru_avh_template_ts = 0U;
  subaru_avh_stable_since = 0U;
  subaru_avh_stable = false;
  for (int i = 0; i < 7; i++) {
    subaru_avh_seen[i] = false;
    subaru_avh_seq[i] = false;
    subaru_avh_ts[i] = 0U;
    for (int j = 0; j < 8; j++) {
      subaru_avh_data[i][j] = 0U;
    }
  }
}

static bool subaru_avh_ready(uint32_t now) {
  bool ready = true;
  for (int i = 0; i < 7; i++) {
    ready &= subaru_avh_seen[i] && subaru_avh_seq[i] &&
             (safety_get_ts_elapsed(now, subaru_avh_ts[i]) <= ((i == 0) ? 1500000U : 300000U));
  }
  const unsigned int rpm = ((unsigned int)subaru_avh_data[2][2] | ((unsigned int)subaru_avh_data[2][3] << 8U)) & 0x1FFFU;
  ready &= (subaru_avh_data[6][3] == 4U);
  ready &= (rpm >= 400U) && (subaru_avh_data[2][4] == 0U) && (subaru_avh_data[3][3] == 4U);
  ready &= (subaru_avh_data[5][2] & 8U) != 0U;
  ready &= !vehicle_moving && !controls_allowed;
  return ready;
}

static void subaru_avh_rx(const CANPacket_t *msg) {
  static const unsigned int SUBARU_AVH_INPUTS[] = {0x6BBU, 0x32BU, 0x40U, 0x48U, 0x13AU, 0x174U, 0x48U};
  static const uint8_t SUBARU_AVH_BUSES[] = {1U, 1U, 1U, 1U, 1U, 1U, 0U};
  if (subaru_avh_enabled && (controls_allowed || lateral_controls_allowed() || vehicle_moving || gas_pressed || subaru_software_fault)) {
    subaru_avh_done = true;
  }
  if (subaru_avh_enabled && !subaru_avh_done) {
    const uint32_t now = microsecond_timer_get();
    for (int i = 0; i < 7; i++) {
      if ((msg->addr == SUBARU_AVH_INPUTS[i]) && (msg->bus == SUBARU_AVH_BUSES[i])) {
        uint8_t checksum = (uint8_t)(msg->addr) + (uint8_t)(msg->addr >> 8U);
        for (int j = 1; j < 8; j++) {checksum += msg->data[j];}
        if ((GET_LEN(msg) != 8U) || (msg->data[0] != checksum)) {
          subaru_avh_done = true;
        } else {
          const uint8_t old_counter = subaru_avh_data[i][1] & 0xFU;
          const uint8_t counter = msg->data[1] & 0xFU;
          if (!subaru_avh_seen[i] || (counter != old_counter)) {
            subaru_avh_seq[i] = subaru_avh_seen[i] && (counter == ((old_counter + 1U) & 0xFU));
            subaru_avh_seen[i] = true;
            subaru_avh_ts[i] = now;
            for (int j = 0; j < 8; j++) {
              subaru_avh_data[i][j] = msg->data[j];
            }
          }
          if (subaru_avh_seen[i] && (counter == old_counter)) {
            for (int j = 0; j < 8; j++) {
              if (subaru_avh_data[i][j] != msg->data[j]) {subaru_avh_done = true;}
            }
          }
          if (((i == 0) && ((msg->data[2] & 3U) != 0U)) ||
              ((i == 1) && ((msg->data[5] & 0x20U) != 0U)) ||
              ((i == 2) && (msg->data[4] != 0U)) || (((i == 3) || (i == 6)) && (msg->data[3] != 4U)) ||
              ((i == 4) && ((((GET_BYTES_LE(msg, 1, 3) >> 4) & 0x1FFFU) != 0U) ||
                            (((GET_BYTES_LE(msg, 3, 3) >> 1) & 0x1FFFU) != 0U) ||
                            (((GET_BYTES_LE(msg, 4, 3) >> 6) & 0x1FFFU) != 0U) ||
                            (((GET_BYTES_LE(msg, 6, 2) >> 3) & 0x1FFFU) != 0U)))) {
            subaru_avh_done = true;
          }
        }
      }
    }
    if (controls_allowed || (safety_get_ts_elapsed(now, subaru_avh_start) > 30000000U)) {
      subaru_avh_done = true;
    }
    if (!subaru_avh_ready(now)) {
      subaru_avh_stable = false;
      if (subaru_avh_count > 0U) {
        subaru_avh_done = true;
      }
    } else if (!subaru_avh_stable) {
      subaru_avh_stable = true;
      subaru_avh_stable_since = now;
    } else {
      // Retain the start of the continuously qualified stationary interval.
    }
  }
}

static bool subaru_avh_tx(const CANPacket_t *msg) {
  const uint32_t now = microsecond_timer_get();
  const uint32_t elapsed = safety_get_ts_elapsed(now, subaru_avh_start);
  const bool second = subaru_avh_count == 1U;
  bool allowed = subaru_avh_enabled && !subaru_avh_done && (subaru_avh_count < 2U) &&
                 (msg->addr == 0x6BBU) && (msg->bus == 1U) && (GET_LEN(msg) == 8U) && !relay_malfunction && !safety_rx_checks_invalid &&
                 aol_rx_healthy() && !lateral_controls_allowed() && !gas_pressed && !subaru_software_fault &&
                 (elapsed >= 10000000U) && (elapsed <= 30000000U) && subaru_avh_ready(now) &&
                 subaru_avh_stable && (safety_get_ts_elapsed(now, subaru_avh_stable_since) >= 3000000U);
  // Rejected required wheel-speed input must also invalidate its cached source.
  for (int i = 0; i < current_safety_config.rx_checks_len; i++) {
    const RxCheck *check = &current_safety_config.rx_checks[i];
      const RxStatus *status = &rx_status[i];
    if (((unsigned int)check->msg[status->index].addr == 0x13AU) && (check->msg[status->index].bus == 1U)) {
      allowed &= status->valid_checksum && (status->wrong_counters < MAX_WRONG_COUNTERS);
    }
  }

  if (second) {
    const uint32_t spacing = safety_get_ts_elapsed(now, subaru_avh_sent);
    allowed &= (spacing >= 45000U) && (spacing <= 80000U) && (subaru_avh_ts[0] == subaru_avh_template_ts) &&
               (safety_get_ts_elapsed(now, subaru_avh_ts[0]) <= 110000U);
  } else {
    allowed &= safety_get_ts_elapsed(now, subaru_avh_ts[0]) <= 30000U;
  }
  uint8_t sum = (uint8_t)(0xBBU + 6U);
  for (int i = 1; i < 8; i++) {
    uint8_t expected = subaru_avh_data[0][i];
    if (i == 1) {
      expected = (expected & 0xF0U) | ((expected + (second ? 2U : 1U)) & 0xFU);
    } else if (i == 2) {
      expected |= 2U;
    } else {
      // Preserve every unrelated payload bit.
    }
    allowed &= msg->data[i] == expected;
    sum += expected;
  }
  allowed &= msg->data[0] == sum;
  if (allowed) {
    subaru_avh_count++;
    subaru_avh_sent = now;
    subaru_avh_template_ts = subaru_avh_ts[0];
    subaru_avh_done = second;
  }
  return allowed;
}
