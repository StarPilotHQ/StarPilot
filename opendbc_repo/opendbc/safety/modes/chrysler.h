#pragma once

#include "opendbc/safety/declarations.h"
#include "opendbc/safety/modes/chrysler_common.h"
#include "opendbc/safety/modes/chrysler_platform.h"
#include "opendbc/safety/modes/chrysler_aol.h"

static bool chrysler_jeep_hold;
static bool chrysler_hold_armed;
static bool chrysler_hold_cancelled;
static uint8_t chrysler_hold_stock[8];
static bool chrysler_hold_drive;
static bool chrysler_hold_gear_seen;
static bool chrysler_hold_gear_initialized;
static uint8_t chrysler_hold_gear_counter;
static uint32_t chrysler_hold_gear_timestamp;
static uint32_t chrysler_hold_stock_timestamp;

static bool chrysler_hold_ready(void) {
  const uint32_t now = microsecond_timer_get();
  bool healthy = !safety_rx_checks_invalid;
  for (int i = 0; i < current_safety_config.rx_checks_len; i++) {
    const RxStatus *status = &current_safety_config.rx_checks[i].status;
    healthy &= status->msg_seen && status->valid_checksum && status->valid_quality_flag &&
               (status->wrong_counters < MAX_WRONG_COUNTERS) && !status->lagging &&
               (safety_get_ts_elapsed(now, status->last_timestamp) <= 1000000U);
  }
  return chrysler_jeep_hold && healthy && chrysler_hold_gear_seen && chrysler_hold_drive &&
         safety_get_ts_elapsed(now, chrysler_hold_gear_timestamp) <= 100000U &&
         safety_get_ts_elapsed(now, chrysler_hold_stock_timestamp) <= 100000U && acc_main_on && !vehicle_moving && !gas_pressed && !brake_pressed &&
         ((chrysler_hold_stock[5] & 0xC0U) == 0U);
}

static uint8_t chrysler_get_counter(const CANPacket_t *msg) {
  return (uint8_t)(msg->data[6] >> 4);
}

static void chrysler_rx_hook(const CANPacket_t *msg) {
  chrysler_aol_rx(msg);
  // Measured EPS torque
  if (msg_matches(msg, CHRYSLER_ADDR(EPS_2), 0U)) {
    int torque_meas_new = ((msg->data[4] & 0x7U) << 8) + msg->data[5] - 1024U;
    update_sample(&torque_meas, torque_meas_new);
  }

  // enter controls on rising edge of ACC, exit controls on ACC off
  const unsigned int das_3_bus = (chrysler_platform == CHRYSLER_PACIFICA) ? 0U : 2U;
  if (msg_matches(msg, CHRYSLER_ADDR(DAS_3), das_3_bus)) {
    bool cruise_engaged = GET_BIT(msg, 21U);
    pcm_cruise_check(cruise_engaged);
    if (chrysler_jeep_hold) {
      chrysler_hold_stock_timestamp = microsecond_timer_get();
      const bool previous_active = (chrysler_hold_stock[2] & 0x20U) != 0U;
      if (cruise_engaged && !previous_active) { chrysler_hold_cancelled = false; }
      acc_main_on = GET_BIT(msg, 20U);
      for (int i = 0; i < 8; i++) { chrysler_hold_stock[i] = msg->data[i]; }
      const unsigned int raw_decel = ((msg->data[2] & 0xFU) << 8) | msg->data[3];
      if (chrysler_hold_ready() && !chrysler_hold_cancelled && cruise_engaged && (raw_decel < 3173U)) {
        chrysler_hold_armed = true;
      }
    }
  }

  // TODO: use the same message for both
  // update vehicle moving
  if ((chrysler_platform != CHRYSLER_PACIFICA) && msg_matches(msg, CHRYSLER_ADDR(ESP_8), 0U)) {
    vehicle_moving = ((msg->data[4] << 8) + msg->data[5]) != 0U;
  }
  if ((chrysler_platform == CHRYSLER_PACIFICA) && msg_matches(msg, 514U, 0U)) {
    int speed_l = (msg->data[0] << 4) + (msg->data[1] >> 4);
    int speed_r = (msg->data[2] << 4) + (msg->data[3] >> 4);
    vehicle_moving = (speed_l | speed_r) != 0;
  }

  // exit controls on rising edge of gas press
  if (msg_matches(msg, CHRYSLER_ADDR(ECM_5), 0U)) {
    gas_pressed = msg->data[0U] != 0U;
  }

  // exit controls on rising edge of brake press
  if (msg_matches(msg, CHRYSLER_ADDR(ESP_1), 0U)) {
    brake_pressed = ((msg->data[0U] & 0xFU) >> 2U) == 1U;
  }
  if (chrysler_jeep_hold && !chrysler_hold_ready()) { chrysler_hold_armed = false; }
}

static void chrysler_hold_cancel_rx(const CANPacket_t *msg) {
  if (chrysler_jeep_hold && msg_matches(msg, 746U, 0U)) {
    // GEAR_CHECKSUM has no checksum algorithm in the Chrysler DBC. Require
    // its known size and advancing counter without inventing a CRC contract.
    const uint8_t counter = (uint8_t)(msg->data[3] >> 4);
    const bool valid = (GET_LEN(msg) == 5U) &&
                       (!chrysler_hold_gear_initialized || (counter == ((chrysler_hold_gear_counter + 1U) & 0xFU)));
    chrysler_hold_gear_initialized = true;
    chrysler_hold_gear_counter = counter;
    chrysler_hold_gear_seen = valid;
    chrysler_hold_drive = valid && ((msg->data[0] & 7U) == 4U);
    if (valid) { chrysler_hold_gear_timestamp = microsecond_timer_get(); }
    if (!chrysler_hold_ready()) { chrysler_hold_armed = false; }
  }
  if (chrysler_jeep_hold && msg_matches(msg, CHRYSLER_CRUISE_BUTTONS, 0U)) {
    if ((GET_LEN(msg) != 3U) || (chrysler_get_checksum(msg) != chrysler_compute_checksum(msg)) || GET_BIT(msg, 0U)) {
      chrysler_hold_armed = false;
      chrysler_hold_cancelled = true;
    }
  }
}

static bool chrysler_tx_hook(const CANPacket_t *msg) {
  const TorqueSteeringLimits CHRYSLER_STEERING_LIMITS = {
    .max_torque = 261,
    .max_rt_delta = 112,
    .max_rate_up = 3,
    .max_rate_down = 3,
    .max_torque_error = 80,
    .type = TorqueMotorLimited,
  };

  const TorqueSteeringLimits CHRYSLER_RAM_DT_STEERING_LIMITS = {
    .max_torque = 350,
    .max_rt_delta = 112,
    .max_rate_up = 6,
    .max_rate_down = 6,
    .max_torque_error = 80,
    .type = TorqueMotorLimited,
  };

  const TorqueSteeringLimits CHRYSLER_RAM_HD_STEERING_LIMITS = {
    .max_torque = 361,
    .max_rt_delta = 182,
    .max_rate_up = 14,
    .max_rate_down = 14,
    .max_torque_error = 80,
    .type = TorqueMotorLimited,
  };

  bool tx = true;

  // STEERING
  if (msg->addr == CHRYSLER_ADDR(LKAS_COMMAND)) {
    int start_byte = (chrysler_platform == CHRYSLER_PACIFICA) ? 0 : 1;
    int desired_torque = ((msg->data[start_byte] & 0x7U) << 8) | msg->data[start_byte + 1];
    desired_torque -= 1024;

    const TorqueSteeringLimits limits = (chrysler_platform == CHRYSLER_PACIFICA) ? CHRYSLER_STEERING_LIMITS :
                                        (chrysler_platform == CHRYSLER_RAM_DT) ? CHRYSLER_RAM_DT_STEERING_LIMITS : CHRYSLER_RAM_HD_STEERING_LIMITS;

    bool steer_req = (chrysler_platform == CHRYSLER_PACIFICA) ? GET_BIT(msg, 4U) : (msg->data[3] & 0x7U) == 2U;
    if (steer_torque_cmd_checks(desired_torque, steer_req, limits)) {
      tx = false;
    }
  }

  // FORCE CANCEL: only the cancel button press is allowed
  if (msg->addr == CHRYSLER_ADDR(CRUISE_BUTTONS)) {
    const bool is_cancel = msg->data[0] == 1U;
    const bool is_resume = msg->data[0] == 0x10U;
    if (is_cancel && chrysler_jeep_hold) {
      chrysler_hold_armed = false;
      chrysler_hold_cancelled = true;
    }
    const bool hold_resume = chrysler_hold_armed && chrysler_hold_ready() && ((chrysler_hold_stock[2] & 0x20U) == 0U) &&
                             (chrysler_get_checksum(msg) == chrysler_compute_checksum(msg));
    const bool allowed = is_cancel || (is_resume && (controls_allowed || hold_resume));
    if (!allowed) {
      tx = false;
    }
  }

  if (msg->addr == CHRYSLER_DAS_3) {
    const unsigned int raw_decel = ((msg->data[2] & 0xFU) << 8) | msg->data[3];
    const unsigned int stock_counter = chrysler_hold_stock[6] >> 4;
    const unsigned int counter = msg->data[6] >> 4;
    tx &= chrysler_hold_armed && chrysler_hold_ready() && ((chrysler_hold_stock[2] & 0x20U) == 0U) &&
          (raw_decel >= 2661U) && (raw_decel <= 3173U) &&
          ((counter == ((stock_counter + 2U) & 0xFU)) || (counter == ((stock_counter + 3U) & 0xFU))) &&
          (msg->data[0] == (chrysler_hold_stock[0] & 0x1FU)) && (msg->data[1] == chrysler_hold_stock[1]) &&
          ((msg->data[2] & 0xF0U) == ((chrysler_hold_stock[2] & 0x80U) | 0x30U)) &&
          (msg->data[4] == 0x12U) && (msg->data[5] == (chrysler_hold_stock[5] & 0xC0U)) &&
          ((msg->data[6] & 0xFU) == (chrysler_hold_stock[6] & 1U)) &&
          (chrysler_get_checksum(msg) == chrysler_compute_checksum(msg));
  }
  return tx;
}

static safety_config chrysler_init(uint16_t param) {
  const uint32_t CHRYSLER_PARAM_RAM_DT = 1U;  // set for Ram DT platform
  chrysler_jeep_hold = param == 4U;
  chrysler_hold_armed = false;
  chrysler_hold_cancelled = false;
  chrysler_hold_drive = false;
  chrysler_hold_gear_seen = false;
  chrysler_hold_gear_initialized = false;
  chrysler_hold_gear_counter = 0U;
  chrysler_hold_gear_timestamp = 0U;
  chrysler_hold_stock_timestamp = 0U;
  for (int i = 0; i < 8; i++) { chrysler_hold_stock[i] = 0U; }

  static const RxCheck chrysler_ram_dt_rx_checks[] = {
    {.msg = {{CHRYSLER_RAM_DT_EPS_2, 0, 8, 100U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_DT_ESP_1, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_DT_ESP_8, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_DT_ECM_5, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_DT_DAS_3, 2, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

  static const RxCheck chrysler_rx_checks[] = {
    {.msg = {{CHRYSLER_EPS_2, 0, 8, 100U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_ESP_1, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{514, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_ECM_5, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_DAS_3, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

  static const CanMsg CHRYSLER_TX_MSGS[] = {
    {CHRYSLER_CRUISE_BUTTONS, 0, 3, .check_relay = false},
    {CHRYSLER_LKAS_COMMAND, 0, 6, .check_relay = true},
    {CHRYSLER_DAS_6, 0, 8, .check_relay = true},
  };

  static const CanMsg CHRYSLER_JEEP_HOLD_TX_MSGS[] = {
    {CHRYSLER_CRUISE_BUTTONS, 0, 3, .check_relay = false},
    {CHRYSLER_LKAS_COMMAND, 0, 6, .check_relay = true},
    {CHRYSLER_DAS_6, 0, 8, .check_relay = true},
    {CHRYSLER_DAS_3, 0, 8, .check_relay = false},
  };

  static const CanMsg CHRYSLER_RAM_DT_TX_MSGS[] = {
    {CHRYSLER_RAM_DT_CRUISE_BUTTONS, 2, 3, .check_relay = false},
    {CHRYSLER_RAM_DT_LKAS_COMMAND, 0, 8, .check_relay = true},
    {CHRYSLER_RAM_DT_DAS_6, 0, 8, .check_relay = true},
  };

#ifdef ALLOW_DEBUG
  static const RxCheck chrysler_ram_hd_rx_checks[] = {
    {.msg = {{CHRYSLER_RAM_HD_EPS_2, 0, 8, 100U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_HD_ESP_1, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_HD_ESP_8, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_HD_ECM_5, 0, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{CHRYSLER_RAM_HD_DAS_3, 2, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

  static const CanMsg CHRYSLER_RAM_HD_TX_MSGS[] = {
    {CHRYSLER_RAM_HD_CRUISE_BUTTONS, 2, 3, .check_relay = false},
    {CHRYSLER_RAM_HD_LKAS_COMMAND, 0, 8, .check_relay = true},
    {CHRYSLER_RAM_HD_DAS_6, 0, 8, .check_relay = true},
  };

  const uint32_t CHRYSLER_PARAM_RAM_HD = 2U;  // set for Ram HD platform
  bool enable_ram_hd = GET_FLAG(param, CHRYSLER_PARAM_RAM_HD);
#endif

  safety_config ret;

  bool enable_ram_dt = GET_FLAG(param, CHRYSLER_PARAM_RAM_DT);

  if (enable_ram_dt) {
    chrysler_platform = CHRYSLER_RAM_DT;
    ret = BUILD_SAFETY_CFG(chrysler_ram_dt_rx_checks, CHRYSLER_RAM_DT_TX_MSGS);
#ifdef ALLOW_DEBUG
  } else if (enable_ram_hd) {
    chrysler_platform = CHRYSLER_RAM_HD;
    ret = BUILD_SAFETY_CFG(chrysler_ram_hd_rx_checks, CHRYSLER_RAM_HD_TX_MSGS);
#endif
  } else {
    chrysler_platform = CHRYSLER_PACIFICA;
    ret = BUILD_SAFETY_CFG(chrysler_rx_checks, CHRYSLER_TX_MSGS);
    if (chrysler_jeep_hold) { SET_TX_MSGS(CHRYSLER_JEEP_HOLD_TX_MSGS, ret); }
  }
  chrysler_aol_configure(param);
  // The allocated Hybrid guard never falls back to ordinary or Ram output.
  if (((param & 8U) != 0U) && !chrysler_aol_enabled) {
    ret.tx_msgs = NULL;
    ret.tx_msgs_len = 0;
  }
  return ret;
}

static void chrysler_optional_rx(const CANPacket_t *msg) {
  chrysler_aol_optional_rx(msg);
  chrysler_hold_cancel_rx(msg);
}

const safety_hooks chrysler_hooks = {
  .init = chrysler_init,
  .rx = chrysler_rx_hook,
  .optional_rx = chrysler_optional_rx,
  .tx = chrysler_tx_hook,
  .get_counter = chrysler_get_counter,
  .get_checksum = chrysler_get_checksum,
  .compute_checksum = chrysler_compute_checksum,
};
