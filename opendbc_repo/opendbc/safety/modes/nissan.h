#pragma once

#include "opendbc/safety/declarations.h"
#include "opendbc/safety/modes/nissan_aol.h"

static bool nissan_alt_eps = false;

static const AngleSteeringLimits NISSAN_STEERING_LIMITS = {
  .max_angle = 60000,  // 600 deg, reasonable limit
  .angle_deg_to_can = 100,
  .angle_rate_up_lookup = {
    {0., 5., 15.},
    {5., .8, .15},
  },
  .angle_rate_down_lookup = {
    {0., 5., 15.},
    {5., 3.5, .4},
  },
};

static void nissan_rx_hook(const CANPacket_t *msg) {
  nissan_aol_observe(msg);
  // Altima: on camera bus, others: on pt bus
  if (msg_matches(msg, 0x185U, 0U)) {
    // Current steering angle
    int angle_meas_new = (msg->data[2] << 10) | (msg->data[3] << 2) | (msg->data[4] >> 6);
    // Factor is -0.01, offset is 1310. Flip to correct sign, but keep units in CAN scale
    angle_meas_new = -angle_meas_new + (1310.0f * NISSAN_STEERING_LIMITS.angle_deg_to_can);

    // update array of samples
    update_sample(&angle_meas, angle_meas_new);
  }

  if (msg_matches(msg, 0x285U, nissan_alt_eps ? 1U : 0U)) {
    // Get current speed and standstill
    uint16_t right_rear = (msg->data[0] << 8) | (msg->data[1]);
    uint16_t left_rear = (msg->data[2] << 8) | (msg->data[3]);
    vehicle_moving = (right_rear | left_rear) != 0U;
    UPDATE_VEHICLE_SPEED((right_rear + left_rear) / 2.0 * 0.005 * KPH_TO_MS);
  }

  // X-Trail 0x15c, Leaf 0x239
  if (msg_matches(msg, 0x15cU, nissan_alt_eps ? 1U : 0U) || msg_matches(msg, 0x239U, nissan_alt_eps ? 1U : 0U)) {
    if (msg->addr == 0x15cU) {
      gas_pressed = ((msg->data[5] << 2) | ((msg->data[6] >> 6) & 0x3U)) > 3U;
    } else {
      gas_pressed = msg->data[0] > 3U;
    }
  }

  // X-trail 0x454, Leaf 0x239
  if (msg_matches(msg, 0x454U, nissan_alt_eps ? 1U : 0U) || msg_matches(msg, 0x239U, nissan_alt_eps ? 1U : 0U)) {
    if (msg->addr == 0x454U) {
      brake_pressed = (msg->data[2] & 0x80U) != 0U;
    } else {
      brake_pressed = ((msg->data[4] >> 5) & 1U) != 0U;
    }
  }

  // Handle cruise enabled
  if (msg_matches(msg, 0x30fU, nissan_alt_eps ? 1U : 2U)) {
    bool cruise_engaged = (msg->data[0] >> 3) & 1U;
    pcm_cruise_check(cruise_engaged);
  }
}

static bool nissan_tx_hook(const CANPacket_t *msg) {
  bool tx = true;
  bool violation = false;

  // steer cmd checks
  if (msg->addr == 0x169U) {
    int desired_angle = ((msg->data[0] << 10) | (msg->data[1] << 2) | ((msg->data[2] >> 6) & 0x3U));
    bool lka_active = (msg->data[6] >> 4) & 1U;

    // Factor is -0.01, offset is 1310. Flip to correct sign, but keep units in CAN scale
    desired_angle = -desired_angle + (1310.0f * NISSAN_STEERING_LIMITS.angle_deg_to_can);

    if (steer_angle_cmd_checks(desired_angle, lka_active, NISSAN_STEERING_LIMITS)) {
      violation = true;
    }
  }

  // acc button check, only allow cancel button to be sent
  if (msg->addr == 0x20bU) {
    // Violation of any button other than cancel is pressed
    violation |= ((msg->data[1] & 0x3dU) > 0U);
  }

  if (violation) {
    tx = false;
  }

  return tx;
}

static safety_config nissan_init(uint16_t param) {
  static const CanMsg NISSAN_TX_MSGS[] = {
    {0x169, 0, 8, .check_relay = true},   // LKAS
    {0x2b1, 0, 8, .check_relay = true},   // PROPILOT_HUD
    {0x4cc, 0, 8, .check_relay = true},   // PROPILOT_HUD_INFO_MSG
    {0x20b, 2, 6, .check_relay = false},  // CRUISE_THROTTLE (X-Trail)
    {0x20b, 1, 6, .check_relay = false},  // CRUISE_THROTTLE (Altima)
    {0x280, 2, 8, .check_relay = true},   // CANCEL_MSG (Leaf)
  };

  // Signals duplicated below due to the fact that these messages can come in on either CAN bus, depending on car model.
  static const RxCheck nissan_rx_defaults[] = {
    {.msg = {{0x185, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},  // STEER_TORQUE_SENSOR
    {.msg = {{0x285, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},
             {0x285, 1, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }}},  // WHEEL_SPEEDS_REAR
    {.msg = {{0x30f, 2, 3, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},
             {0x30f, 1, 3, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }}},  // CRUISE_STATE
    {.msg = {{0x15c, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},
             {0x15c, 1, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},
             {0x239, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}}},  // GAS_PEDAL
    {.msg = {{0x454, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},
             {0x454, 1, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},
             {0x1cc, 0, 4, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}}},  // DOORS_LIGHTS / BRAKE
  };

  // Reuse one mutable RX table across ordinary and optional configurations.
  // Restore the stock defaults on every init, including after an AOL profile.
  static RxCheck nissan_rx_checks[5];
  for (unsigned int i = 0U; i < 5U; i++) {
    nissan_rx_checks[i] = nissan_rx_defaults[i];
  }

  // EPS Location. false = V-CAN, true = C-CAN
  const uint16_t NISSAN_PARAM_ALT_EPS_BUS = 1;

  nissan_alt_eps = GET_FLAG(param, NISSAN_PARAM_ALT_EPS_BUS);
  nissan_aol_reset();
  nissan_aol_enabled = ((unsigned int)alternative_experience == 32U) && ((param == 0U) || (param == 1U) || (param == 4U));
  nissan_aol_alt = param == 1U;
  nissan_aol_leaf = param == 4U;
  if (nissan_aol_enabled) {
    static const AolSafetyPolicy policy = {
      .reset = nissan_aol_reset,
      .request_mask = nissan_aol_request,
      .permission_mask = nissan_aol_permission,
      .rx_invalid = nissan_aol_reset,
    };
    aol_policy = &policy;
  }
  // Exact optional source ownership. Leaf uses 0x239 for BOTH gas and brake,
  // not the unrelated mixed-table 0x1cc/4-byte brake alternative.
  safety_config ret = BUILD_SAFETY_CFG(nissan_rx_checks, NISSAN_TX_MSGS);
  if (nissan_aol_enabled) {
    for (unsigned int i = 0U; i < 5U; i++) {
      nissan_rx_checks[i].msg[1] = (CanMsgCheck){0};
      nissan_rx_checks[i].msg[2] = (CanMsgCheck){0};
    }
    nissan_rx_checks[1].msg[0].bus = nissan_aol_alt ? 1U : 0U;
    nissan_rx_checks[2].msg[0].bus = nissan_aol_alt ? 1U : 2U;
    nissan_rx_checks[3].msg[0].addr = nissan_aol_leaf ? 0x239U : 0x15CU;
    nissan_rx_checks[3].msg[0].bus = nissan_aol_alt ? 1U : 0U;
    nissan_rx_checks[4].msg[0].bus = nissan_aol_alt ? 1U : 0U;
    ret.rx_checks_len = nissan_aol_leaf ? 4 : 5;
  }
  if (((unsigned int)alternative_experience == 32U) && !nissan_aol_enabled) {
    ret.tx_msgs = NULL;
    ret.tx_msgs_len = 0;
  }
  return ret;
}

const safety_hooks nissan_hooks = {
  .init = nissan_init,
  .rx = nissan_rx_hook,
  .tx = nissan_tx_hook,
  .optional_rx = nissan_aol_observe,
};
