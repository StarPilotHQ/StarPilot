#pragma once

static bool subaru_stop_start_enabled = false;
static bool subaru_stop_start_state_zero = false;
static uint8_t subaru_stop_start_gear_bus;
static uint8_t subaru_stop_start_data[4][8];
static bool subaru_stop_start_seen[4];
static bool subaru_stop_start_seq[4];
static uint32_t subaru_stop_start_ts[4];
static uint32_t subaru_stop_start_start = 0U;
static uint32_t subaru_stop_start_first = 0U;
static uint32_t subaru_stop_start_last = 0U;
static uint8_t subaru_stop_start_count = 0U;
static uint8_t subaru_stop_start_template_counter = 0U;
static bool subaru_stop_start_done;

static void subaru_stop_start_init(bool enabled, uint8_t gear_bus, bool state_zero) {
  subaru_stop_start_enabled = enabled;
  subaru_stop_start_state_zero = enabled && state_zero;
  subaru_stop_start_gear_bus = gear_bus;
  subaru_stop_start_start = microsecond_timer_get();
  subaru_stop_start_first = 0U;
  subaru_stop_start_last = 0U;
  subaru_stop_start_count = 0U;
  subaru_stop_start_template_counter = 0U;
  subaru_stop_start_done = false;
  for (int i = 0; i < 4; i++) {
    subaru_stop_start_seen[i] = false;
    subaru_stop_start_seq[i] = false;
    subaru_stop_start_ts[i] = 0U;
    for (int j = 0; j < 8; j++) {
      subaru_stop_start_data[i][j] = 0U;
    }
  }
}

static void subaru_stop_start_rx(const CANPacket_t *msg) {
  static const unsigned int SUBARU_STOP_START_INPUTS[] = {0x390U, 0x48U, 0x174U, 0x221U};

  if (subaru_stop_start_enabled && (controls_allowed || lateral_controls_allowed() || vehicle_moving || gas_pressed || subaru_software_fault)) {
    subaru_stop_start_done = true;
  }
  if (subaru_stop_start_enabled && !subaru_stop_start_done) {
    for (int i = 0; i < 4; i++) {
      if ((msg->addr == SUBARU_STOP_START_INPUTS[i]) && (msg->bus == ((i == 1) ? subaru_stop_start_gear_bus : 1U))) {
        uint8_t checksum = (uint8_t)(msg->addr) + (uint8_t)(msg->addr >> 8U);
        for (int j = 1; j < 8; j++) {checksum += msg->data[j];}
        if ((GET_LEN(msg) != 8U) || (msg->data[0] != checksum)) {
          subaru_stop_start_done = true;
        } else {
          const uint8_t counter = msg->data[1] & 15U;
          const uint8_t previous = subaru_stop_start_data[i][1] & 15U;
          if (!subaru_stop_start_seen[i] || (counter != previous)) {
            subaru_stop_start_seq[i] = subaru_stop_start_seen[i] && (counter == ((previous + 1U) & 15U));
            subaru_stop_start_seen[i] = true;
            subaru_stop_start_ts[i] = microsecond_timer_get();
            for (int j = 0; j < 8; j++) {subaru_stop_start_data[i][j] = msg->data[j];}
          } else {
            for (int j = 0; j < 8; j++) {
              if (msg->data[j] != subaru_stop_start_data[i][j]) {subaru_stop_start_done = true;}
            }
          }
          if ((i == 0) && ((msg->data[6] & 0x40U) != 0U)) {subaru_stop_start_done = true;}
          if ((i == 3) && GET_BIT(msg, 15U)) {subaru_stop_start_done = true;}
          if ((i == 2) && ((msg->data[4] >> 6U) == 3U)) {subaru_stop_start_done = true;}
        }
      }
    }
  }
}

static bool subaru_stop_start_tx(const CANPacket_t *msg) {
  const uint32_t now = microsecond_timer_get();
  bool allowed = subaru_stop_start_enabled && !subaru_stop_start_done &&
    (msg->addr == 0x390U) && (msg->bus == 1U) && (GET_LEN(msg) == 8U) &&
    !relay_malfunction && !safety_rx_checks_invalid && aol_rx_healthy() &&
    !controls_allowed && !lateral_controls_allowed() && !vehicle_moving && !gas_pressed && !subaru_software_fault &&
    (safety_get_ts_elapsed(now, subaru_stop_start_start) >= 1000000U) &&
    (safety_get_ts_elapsed(now, subaru_stop_start_start) <= 10000000U) && (subaru_stop_start_count < 6U);
  for (int i = 0; i < 4; i++) {
    allowed &= subaru_stop_start_seen[i] && subaru_stop_start_seq[i] &&
      (safety_get_ts_elapsed(now, subaru_stop_start_ts[i]) <= 300000U);
  }
  allowed &= (subaru_stop_start_data[3][1] & 0x80U) == 0U;
  allowed &= (subaru_stop_start_data[1][3] == 4U) || (subaru_stop_start_data[1][3] == 2U);
  const uint8_t state = subaru_stop_start_data[2][4] >> 6U;
  allowed &= (state == 1U) || (state == 2U) || ((state == 0U) && subaru_stop_start_state_zero);
  if (subaru_stop_start_count > 0U) {
    allowed &= (subaru_stop_start_data[0][1] & 15U) != subaru_stop_start_template_counter;
    allowed &= safety_get_ts_elapsed(now, subaru_stop_start_first) < 300000U;
    allowed &= safety_get_ts_elapsed(now, subaru_stop_start_last) >= 50000U;
  }
  uint8_t checksum = 0x93U;
  for (int i = 1; i < 8; i++) {
    uint8_t expected = subaru_stop_start_data[0][i];
    if (i == 1) {expected = (expected & 0xf0U) | ((expected + 1U) & 15U);}
    if (i == 6) {expected |= 0x40U;}
    allowed &= msg->data[i] == expected;
    checksum += expected;
  }
  allowed &= msg->data[0] == checksum;
  if (allowed) {
    if (subaru_stop_start_count == 0U) {subaru_stop_start_first = now;}
    subaru_stop_start_template_counter = subaru_stop_start_data[0][1] & 15U;
    subaru_stop_start_last = now;
    subaru_stop_start_count++;
  }
  return allowed;
}
