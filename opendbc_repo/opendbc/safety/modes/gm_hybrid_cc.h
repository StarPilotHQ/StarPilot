#pragma once

static safety_config gm_init(uint16_t safety_param);
static bool gm_tx_hook(const CANPacket_t *msg);

static bool gm_hybrid_selected;
static bool gm_hybrid_rejected;
static bool gm_hybrid_pedal;
static bool gm_hybrid_stock;
static bool gm_hybrid_removed;
static bool gm_hybrid_main;
static bool gm_hybrid_drive;
static bool gm_hybrid_stock_active;
static bool gm_hybrid_speed;
static uint32_t gm_hybrid_wheel_sum;
static uint32_t gm_hybrid_stock_speed;
static bool gm_hybrid_gas_set_seen;
static uint32_t gm_hybrid_gas_set_us;
static bool gm_hybrid_regen;
static bool gm_hybrid_eps;
static bool gm_hybrid_eps_temporary;
static bool gm_hybrid_sensor_valid;
static bool gm_hybrid_sensor_seen;
static bool gm_hybrid_sensor_gas;
static uint8_t gm_hybrid_sensor_counter;
static bool gm_hybrid_seen[11] = {false, false, false, false, false, false, false, false, false, false, false};
static uint32_t gm_hybrid_us[11] = {0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
static bool gm_hybrid_credit;
static bool gm_hybrid_phase_seen;
static uint8_t gm_hybrid_phase;
static uint8_t gm_hybrid_prefix;
static uint8_t gm_hybrid_counter;
static uint32_t gm_hybrid_credit_us;
static bool gm_hybrid_requalify;
static bool gm_hybrid_button_seen;
static uint8_t gm_hybrid_button;
static bool gm_hybrid_semantic_reset;
static bool gm_hybrid_tx_seen;
static uint32_t gm_hybrid_tx_us;
static bool gm_hybrid_pedal_tx_seen;
static uint8_t gm_hybrid_pedal_tx_counter;
static uint8_t gm_hybrid_pscm[8] = {0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
static bool gm_hybrid_pscm_tx_seen;
static uint32_t gm_hybrid_pscm_tx_us;

static void gm_hybrid_clear_credit(void) {
  gm_hybrid_credit = false;
  gm_hybrid_requalify = true;
}

static void gm_hybrid_reset(void) {
  gm_hybrid_main = false; gm_hybrid_drive = false; gm_hybrid_stock_active = false;
  gm_hybrid_regen = false; gm_hybrid_eps = false; gm_hybrid_eps_temporary = false; gm_hybrid_sensor_valid = false;
  gm_hybrid_speed = false; gm_hybrid_wheel_sum = 0U; gm_hybrid_stock_speed = 0U;
  gm_hybrid_gas_set_seen = false; gm_hybrid_gas_set_us = 0U;
  gm_hybrid_sensor_seen = false; gm_hybrid_sensor_gas = false; gm_hybrid_sensor_counter = 0U;
  gm_hybrid_credit = false; gm_hybrid_phase_seen = false; gm_hybrid_phase = 0U;
  gm_hybrid_prefix = 1U; gm_hybrid_counter = 0U; gm_hybrid_credit_us = 0U;
  gm_hybrid_requalify = false; gm_hybrid_button_seen = false; gm_hybrid_button = 0U;
  gm_hybrid_semantic_reset = false; gm_hybrid_tx_seen = false; gm_hybrid_tx_us = 0U;
  gm_hybrid_pedal_tx_seen = false; gm_hybrid_pedal_tx_counter = 0U;
  gm_hybrid_pscm_tx_seen = false; gm_hybrid_pscm_tx_us = 0U;
  for (uint8_t i = 0U; i < 11U; i++) { gm_hybrid_seen[i] = false; gm_hybrid_us[i] = 0U; }
  for (uint8_t i = 0U; i < 8U; i++) { gm_hybrid_pscm[i] = 0U; }
}

static bool gm_hybrid_current(void) {
  static const uint32_t limits[11] = {300000U, 100000U, 100000U, 300000U, 300000U, 300000U,
                                    1000000U, 100000U, 100000U, 1000000U, 1000000U};
  bool current = !safety_rx_checks_invalid && !relay_malfunction;
  for (uint8_t i = 0U; i < 11U; i++) {
    if (((i != 8U) || gm_hybrid_pedal) && ((i < 9U) || !gm_hybrid_removed)) {
      current &= gm_hybrid_seen[i] && (safety_get_ts_elapsed(microsecond_timer_get(), gm_hybrid_us[i]) <= limits[i]);
    }
  }
  current &= !gm_hybrid_pedal || gm_hybrid_sensor_valid;
  if (!current) { gm_hybrid_clear_credit(); controls_allowed = false; }
  return current;
}

static bool gm_hybrid_ready(const bool allow_gas) {
  return gm_hybrid_current() && gm_hybrid_main && gm_hybrid_drive && gm_hybrid_eps &&
         !brake_pressed && !gm_hybrid_regen &&
         (allow_gas || (!gas_pressed && (!gm_hybrid_pedal || !gm_hybrid_sensor_gas)));
}

static uint8_t gm_hybrid_request(void) {
  uint8_t request = 0U;
  if (heartbeat_engaged && !relay_malfunction && !safety_rx_checks_invalid &&
      (safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) <= AOL_HOST_REQUEST_TIMEOUT_US)) {
    request = aol_host_axis_mask & (gm_hybrid_stock ? 1U : 3U);
  }
  return request;
}

static uint8_t gm_hybrid_permission(void) {
  uint8_t permission = 0U;
  if (gm_hybrid_current() && gm_hybrid_main && gm_hybrid_drive && gm_hybrid_eps) {
    permission = gm_hybrid_request() & 1U;
    if (!gm_hybrid_stock && controls_allowed && !brake_pressed && !gm_hybrid_regen &&
        (!gm_hybrid_pedal || (!gm_hybrid_stock_active && !gas_pressed && !gm_hybrid_sensor_gas))) {
      permission |= gm_hybrid_request() & 2U;
    }
  }
  return permission;
}

static void gm_hybrid_invalid(void) {
  gm_cruise_status_clear();
  gm_hybrid_clear_credit(); controls_allowed = false;
  for (uint8_t i = 0U; i < 11U; i++) { gm_hybrid_seen[i] = false; }
  aol_set_host_request(0U);
}

static bool gm_hybrid_tuple(const CANPacket_t *msg) {
  return (msg->data[0] == 0U) && (msg->data[1] == 0U) && (msg->data[2] == 0U) &&
         ((msg->data[3] == 1U) || (msg->data[3] == 0x41U)) && (msg->data[4] <= 3U);
}

static uint8_t gm_hybrid_semantic(const CANPacket_t *msg) {
  const uint16_t word = ((uint16_t)msg->data[5] << 8U) | msg->data[6];
  uint8_t button = 0U;
  if (gm_hybrid_tuple(msg)) {
    if ((word == 0x15EEU) || (word == 0x1FCCU)) { button = 1U; }
    else if ((word == 0x55AEU) || (word == 0x5F8CU)) { button = 2U; }
    else if ((word == 0x2ACDU) || (word == 0x20EFU)) { button = 5U; }
    else if ((word == 0x60AFU) || (word == 0x6A8DU)) { button = 6U; }
    else if ((msg->data[4] == 0U) && ((word == 0x30DFU) || (word == 0x35CEU) ||
                                      (word == 0x3ABDU) || (word == 0x3FACU))) { button = 3U; }
    else {
      const unsigned int checksum = 0xDFU + ((unsigned int)msg->data[4] * 0x4EFU);
      if ((msg->data[3] == 1U) && (word == (0x3000U | checksum))) { button = 3U; }
    }
  }
  return button;
}

static void gm_hybrid_rx(const CANPacket_t *msg) {
  static const uint32_t addresses[11] = {0x184U, 0x34AU, 0x1E1U, 0x1C4U, 0xC9U, 0x3D1U,
                                        0x1F5U, 0xBDU, 0x201U, 0x180U, 0x320U};
  static const uint8_t lengths[11] = {8U, 5U, 7U, 8U, 8U, 8U, 8U, 7U, 6U, 4U, 6U};
  const uint32_t now = microsecond_timer_get();
  for (uint8_t i = 0U; i < 11U; i++) {
    const unsigned int bus = (i >= 9U) ? 2U : 0U;
    if ((msg->addr == addresses[i]) && (msg->bus == bus)) {
      if (GET_LEN(msg) != lengths[i]) { gm_hybrid_seen[i] = false; gm_hybrid_clear_credit(); controls_allowed = false; }
      else {
        const bool gap = gm_hybrid_seen[i] && (i == 2U) && (safety_get_ts_elapsed(now, gm_hybrid_us[i]) > 100000U);
        gm_hybrid_seen[i] = true; gm_hybrid_us[i] = now;
        if (gap) { gm_hybrid_clear_credit(); }
        if (i == 0U) {
          for (uint8_t j = 0U; j < 8U; j++) { gm_hybrid_pscm[j] = msg->data[j]; }
          const uint32_t torque = (((uint32_t)msg->data[6] & 7U) << 8U) | msg->data[7];
          update_sample(&torque_driver, to_signed((int)torque, 11));
          const uint8_t eps = (msg->data[0] >> 3U) & 7U;
          gm_hybrid_eps = (eps != 2U) && (eps != 3U);
          gm_hybrid_eps_temporary = eps == 2U;
        } else if (i == 1U) {
          gm_hybrid_wheel_sum = (((uint32_t)msg->data[0] << 8U) | msg->data[1]) +
                                (((uint32_t)msg->data[2] << 8U) | msg->data[3]);
          gm_hybrid_speed = ((((uint32_t)msg->data[0] << 8U) | msg->data[1]) >= 1242U) &&
                            ((((uint32_t)msg->data[2] << 8U) | msg->data[3]) >= 1242U) &&
                            (((msg->data[4] >> 3U) & 7U) == 1U) && ((msg->data[4] & 7U) == 1U);
          vehicle_moving = ((((unsigned int)msg->data[0] << 8U) | msg->data[1]) > 10U) ||
                           ((((unsigned int)msg->data[2] << 8U) | msg->data[3]) > 10U);
        } else if (i == 3U) { gas_pressed = msg->data[5] != 0U; }
        else if (i == 4U) { gm_hybrid_main = GET_BIT(msg, 29U); brake_pressed = GET_BIT(msg, 40U); }
        else if (i == 5U) {
          gm_hybrid_stock_active = GET_BIT(msg, 39U);
          gm_hybrid_stock_speed = (((uint32_t)msg->data[2] & 15U) << 8U) | msg->data[3];
        }
        else if (i == 6U) {
          const uint8_t gear = msg->data[3] & 15U;
          gm_hybrid_drive = (gear == 4U) || (gear == 6U) || GET_BIT(msg, 41U);
        } else if (i == 7U) { gm_hybrid_regen = (msg->data[0] >> 4U) != 0U; }
        else if ((i == 8U) && gm_hybrid_pedal) {
          const uint32_t track1 = ((uint32_t)msg->data[0] << 8U) | msg->data[1];
          const uint32_t track2 = ((uint32_t)msg->data[2] << 8U) | msg->data[3];
          const uint8_t counter = msg->data[4] & 15U;
          gm_hybrid_sensor_valid = ((msg->data[4] >> 4U) == 0U) && (track1 <= 4095U) && (track2 <= 4095U) &&
            (gm_pedal_crc(msg) == msg->data[5]) && (!gm_hybrid_sensor_seen || (counter != gm_hybrid_sensor_counter));
          gm_hybrid_sensor_seen = true; gm_hybrid_sensor_counter = counter;
          gm_hybrid_sensor_gas = ((125677U * track1) + (251976U * track2)) > (uint32_t)198510000U;
        } else {
        }
        if (i == 2U) {
          gm_hybrid_credit = false;
          if (gm_hybrid_requalify) {
            if (gm_hybrid_ready(false)) {
              gm_hybrid_phase_seen = false; gm_hybrid_requalify = false;
              gm_hybrid_button_seen = false; gm_hybrid_semantic_reset = true;
            }
          } else {
            const uint16_t word = ((uint16_t)msg->data[5] << 8U) | msg->data[6];
            if (gm_hybrid_tuple(msg) && ((word == 0x15EEU) || (word == 0x1FCCU))) {
              const uint8_t phase = (word == 0x15EEU) ? 1U : 3U;
              if (!gm_hybrid_phase_seen || (phase != gm_hybrid_phase)) {
                gm_hybrid_phase = phase; gm_hybrid_phase_seen = true; gm_hybrid_credit = true;
                gm_hybrid_prefix = msg->data[3]; gm_hybrid_counter = msg->data[4]; gm_hybrid_credit_us = now;
              }
            }
            const uint8_t button = gm_hybrid_semantic(msg);
            if (button != 0U) {
              if (!gm_hybrid_button_seen) { gm_hybrid_semantic_reset = button != 1U; }
              else if (!gm_hybrid_stock && !gm_hybrid_semantic_reset && gm_hybrid_ready(gm_hybrid_pedal) &&
                       (((button == 2U) && (gm_hybrid_button != 2U)) || ((button == 1U) && (gm_hybrid_button == 3U)))) {
                controls_allowed = true;
              } else {
              }
              if (gm_hybrid_semantic_reset && (button == 1U)) { gm_hybrid_semantic_reset = false; }
              if ((button == 5U) || (button == 6U)) { controls_allowed = false; }
              gm_hybrid_button = button; gm_hybrid_button_seen = true;
            }
          }
        }
      }
    }
  }
  const bool current = gm_hybrid_current();
  if (gm_hybrid_stock) {
    // Evaluate the physical PCM edge after all sources agree, independent of
    // whether cruise or main arrived first. Faults do not reset a held edge.
    if ((gm_hybrid_seen[4] && !gm_hybrid_main) || (gm_hybrid_seen[5] && !gm_hybrid_stock_active)) {
      pcm_cruise_check(false);
    } else if (current && gm_hybrid_main && gm_hybrid_stock_active) {
      const bool previously_allowed = controls_allowed;
      pcm_cruise_check(true);
      // Observe the PCM edge during a temporary fault, but never arm from it.
      if (gm_hybrid_eps_temporary && !previously_allowed) { controls_allowed = false; }
    } else {
    }
  }
  const bool permanent_eps_withdrawal = !gm_hybrid_eps && !gm_hybrid_eps_temporary;
  if (!gm_hybrid_main || !gm_hybrid_drive || permanent_eps_withdrawal || (gm_hybrid_pedal && !gm_hybrid_sensor_valid)) {
    gm_hybrid_clear_credit(); controls_allowed = false;
  }
  // Driver intervention withdraws actuation, not the physical cancellation slot.
  if (brake_pressed || gm_hybrid_regen || (gm_hybrid_stock && gas_pressed)) {
    controls_allowed = false;
  }
}

static bool gm_hybrid_button_tx(const CANPacket_t *msg) {
  static const uint16_t resume[4] = {0x55AEU, 0x5F8CU, 0x6F7CU, 0x659EU};
  static const uint16_t cancel[4] = {0x60AFU, 0x659EU, 0x6A8DU, 0x6F7CU};
  const uint32_t now = microsecond_timer_get();
  if (gm_hybrid_credit && (safety_get_ts_elapsed(now, gm_hybrid_credit_us) > 100000U)) { gm_hybrid_clear_credit(); }
  bool allowed = gm_hybrid_current() && gm_hybrid_main && gm_hybrid_drive && gm_hybrid_eps &&
    gm_hybrid_credit && !gm_hybrid_requalify && gm_hybrid_tuple(msg) &&
    (!gm_hybrid_tx_seen || (safety_get_ts_elapsed(now, gm_hybrid_tx_us) >= 40000U));
  const uint16_t word = ((uint16_t)msg->data[5] << 8U) | msg->data[6];
  const bool longitudinal = get_longitudinal_allowed();
  bool cancel_long_request = controls_allowed;
  if (alternative_experience != 0) { cancel_long_request = (gm_hybrid_request() & 2U) != 0U; }
  const bool cancel_context = gm_hybrid_stock_active && (gm_hybrid_stock || !gm_hybrid_pedal || cancel_long_request);
  const bool custom_cancel = cancel_context && (msg->data[3] == gm_hybrid_prefix) && (msg->data[4] == 0U) &&
                             (word == cancel[(gm_hybrid_phase + 1U) % 4U]);
  const bool custom_resume = !gm_hybrid_stock && !gm_hybrid_pedal && gm_hybrid_speed && longitudinal && !gas_pressed && !brake_pressed &&
    !gm_hybrid_regen && (msg->data[3] == gm_hybrid_prefix) && (msg->data[4] == 0U) && (word == resume[gm_hybrid_phase]);
  const uint8_t next_counter = (gm_hybrid_counter + 1U) % 4U;
  const unsigned int set_checksum = 0xDFU + ((unsigned int)msg->data[4] * 0x4EFU);
  const bool gas_set = !gm_hybrid_stock && !gm_hybrid_pedal && longitudinal_controls_allowed() &&
    gas_pressed && gm_hybrid_stock_active && !brake_pressed && !gm_hybrid_regen && gm_hybrid_speed &&
    ((gm_hybrid_stock_speed * 1250U) < (gm_hybrid_wheel_sum * 311U)) &&
    (!gm_hybrid_gas_set_seen || (safety_get_ts_elapsed(now, gm_hybrid_gas_set_us) >= 520000U)) &&
    (msg->data[3] == 1U) && (msg->data[4] == 0U) && (word == 0x30DFU);
  const bool standard_set = !gm_hybrid_stock && !gm_hybrid_pedal && longitudinal && !gas_pressed &&
    !brake_pressed && !gm_hybrid_regen && gm_hybrid_speed && (msg->data[3] == 1U) &&
    (word == (0x3000U | set_checksum)) && (msg->data[4] == next_counter);
  allowed &= custom_cancel || custom_resume || standard_set || gas_set;
  if (allowed) {
    gm_hybrid_credit = false; gm_hybrid_tx_seen = true; gm_hybrid_tx_us = now;
    if (gas_set) { gm_hybrid_gas_set_seen = true; gm_hybrid_gas_set_us = now; }
  }
  return allowed;
}

static bool gm_hybrid_tx(const CANPacket_t *msg) {
  bool allowed = false;
  if ((msg->addr == 0x180U) && (msg->bus == 0U) && (GET_LEN(msg) == 4U)) {
    const bool current = gm_hybrid_current();
    const bool neutral = ((msg->data[0] & 15U) == 0U) && (msg->data[1] == 0U);
    allowed = gm_tx_hook(msg) && (neutral || (current && gm_hybrid_main && gm_hybrid_drive && gm_hybrid_eps));
  } else if ((msg->addr == 0x1E1U) && (msg->bus == 0U) && (GET_LEN(msg) == 7U)) {
    allowed = gm_hybrid_button_tx(msg);
  } else if (gm_hybrid_pedal && (msg->addr == 0x200U) && (msg->bus == 0U) && (GET_LEN(msg) == 6U)) {
    const uint32_t raw_track1 = ((uint32_t)msg->data[0] << 8U) | msg->data[1];
    const uint32_t raw_track2 = ((uint32_t)msg->data[2] << 8U) | msg->data[3];
    const int track1 = (int)raw_track1;
    const int track2 = (int)raw_track2;
    const bool enabled = GET_BIT(msg, 39U);
    const uint8_t counter = msg->data[4] & 15U;
    const int difference = track1 - (2 * track2);
    const bool inactive = !enabled && (track1 == 0) && (track2 == 0);
    const bool active = enabled && gm_hybrid_ready(false) && !gm_hybrid_stock_active && get_longitudinal_allowed() &&
      (track1 >= 604) && (track1 <= 2633) && (track2 >= 304) && (track2 <= 1316) &&
      (difference >= -16) && (difference <= 16);
    allowed = (inactive || active) && ((msg->data[4] & 0x70U) == 0U) &&
      (gm_pedal_crc(msg) == msg->data[5]) && (!gm_hybrid_pedal_tx_seen || (counter != gm_hybrid_pedal_tx_counter));
    if (allowed) { gm_hybrid_pedal_tx_seen = true; gm_hybrid_pedal_tx_counter = counter; }
  } else if ((msg->addr == 0x184U) && (msg->bus == 2U) && (GET_LEN(msg) == 8U)) {
    static const uint8_t masks[8] = {0x3FU, 0xFFU, 0x3FU, 0xFFU, 0x7BU, 0xFFU, 7U, 0xFFU};
    uint8_t expected[8];
    for (uint8_t i = 0U; i < 8U; i++) { expected[i] = gm_hybrid_pscm[i] & masks[i]; }
    unsigned int checksum = (((unsigned int)gm_hybrid_pscm[4] & 3U) << 8U) | gm_hybrid_pscm[5];
    if ((gm_hybrid_pscm[2] & 32U) == 0U) { checksum += 32U; }
    expected[2] |= 32U;
    expected[4] = (uint8_t)(((unsigned int)expected[4] & 0xFCU) | ((checksum >> 8U) & 3U));
    expected[5] = (uint8_t)(checksum & 255U);
    allowed = gm_hybrid_seen[0] && (safety_get_ts_elapsed(microsecond_timer_get(), gm_hybrid_us[0]) <= 300000U) &&
      (!gm_hybrid_pscm_tx_seen || (safety_get_ts_elapsed(microsecond_timer_get(), gm_hybrid_pscm_tx_us) >= 100000U));
    for (uint8_t i = 0U; i < 8U; i++) { allowed &= expected[i] == msg->data[i]; }
    if (allowed) { gm_hybrid_pscm_tx_seen = true; gm_hybrid_pscm_tx_us = microsecond_timer_get(); }
  } else if (gm_hybrid_removed && !gm_hybrid_stock && (msg->bus == 0U) && (GET_LEN(msg) == 7U) &&
             ((msg->addr == 0x409U) || (msg->addr == 0x40AU))) {
    allowed = true;
    for (uint8_t i = 0U; i < 7U; i++) { allowed &= msg->data[i] == 0U; }
  } else {
  }
  return allowed;
}

static safety_config gm_hybrid_init(uint16_t word) {
  safety_config ret = gm_init(1U);
  gm_aol_reset();
  gm_hybrid_reset();
  gm_hybrid_removed = (word & 1U) != 0U;
  gm_hybrid_pedal = (word == 0xE802U) || (word == 0xE803U);
  gm_hybrid_stock = (word == 0xE804U) || (word == 0xE805U);
  static const RxCheck rx_template[10] = {
    {.msg = {{0x184U, 0U, 8U, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    {.msg = {{0x34AU, 0U, 5U, 20U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    {.msg = {{0x1E1U, 0U, 7U, 33U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    {.msg = {{0x1C4U, 0U, 8U, 33U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    {.msg = {{0xC9U, 0U, 8U, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    {.msg = {{0x3D1U, 0U, 8U, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    {.msg = {{0x1F5U, 0U, 8U, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    {.msg = {{0xBDU, 0U, 7U, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    {.msg = {{0x180U, 2U, 4U, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    {.msg = {{0x320U, 2U, 6U, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
  };
  for (uint8_t i = 0U; i < 10U; i++) { gm_extended_rx_checks[i] = rx_template[i]; }
  ret.rx_checks = gm_extended_rx_checks;
  ret.rx_checks_len = gm_hybrid_removed ? 8 : 10;
  static const CanMsg present_tx[] = {
    {0x180U, 0U, 4U, .check_relay = true}, {0x1E1U, 0U, 7U, .check_relay = false},
    {0x184U, 2U, 8U, .check_relay = true}, {0x200U, 0U, 6U, .check_relay = false},
    {0x3D1U, 0U, 8U, .check_relay = false},
  };
  static const CanMsg removed_tx[] = {
    {0x180U, 0U, 4U, .check_relay = false}, {0x1E1U, 0U, 7U, .check_relay = false},
    {0x184U, 2U, 8U, .check_relay = true}, {0x409U, 0U, 7U, .check_relay = false}, {0x40AU, 0U, 7U, .check_relay = false},
    {0x200U, 0U, 6U, .check_relay = false},
    {0x3D1U, 0U, 8U, .check_relay = false},
  };
  if (gm_hybrid_removed) {
    ret.tx_msgs = removed_tx;
    if (gm_hybrid_stock) { ret.tx_msgs_len = 3; }
    else if (gm_hybrid_pedal) { ret.tx_msgs_len = 7; }
    else { ret.tx_msgs_len = 5; }
  } else {
    ret.tx_msgs = present_tx;
    if (gm_hybrid_pedal) { ret.tx_msgs_len = 5; }
    else { ret.tx_msgs_len = 3; }
  }
  static const AolSafetyPolicy policy = {
    .reset = gm_hybrid_reset, .host_request = NULL, .request_mask = gm_hybrid_request,
    .permission_mask = gm_hybrid_permission, .rx_invalid = gm_hybrid_invalid,
  };
  if ((unsigned int)alternative_experience == 32U) { aol_policy = &policy; }
  return ret;
}
