#pragma once
#include "opendbc/safety/declarations.h"

#define TESLA_HW1_FLAG 16U
static bool tesla_hw1_long = false;
static bool tesla_hw1_stock_aeb = false;
static bool tesla_hw1_stock_lkas = false;
static bool tesla_hw1_stock_lkas_prev = false;

static uint8_t tesla_hw1_counter(const CANPacket_t *msg) {
  return (msg->addr == 0x488U) ? (msg->data[2] & 0x0FU) : (msg->data[6] >> 5);
}
static int tesla_hw1_checksum_index(const CANPacket_t *msg) {
  int index = -1;
  if ((msg->addr == 0x488U) && (GET_LEN(msg) == 4U)) {
    index = 3;
  } else if ((msg->addr == 0x2b9U) && (GET_LEN(msg) == 8U)) {
    index = 7;
  } else {
  }
  return index;
}
static uint32_t tesla_hw1_checksum(const CANPacket_t *msg) {
  const int index = tesla_hw1_checksum_index(msg);
  return (index >= 0) ? msg->data[index] : 0U;
}
static uint32_t tesla_hw1_compute_checksum(const CANPacket_t *msg) {
  const int index = tesla_hw1_checksum_index(msg);
  unsigned int sum = 0U;
  if (index >= 0) {
    sum = (msg->addr & 0xFFU) + (msg->addr >> 8);
    for (int i = 0; i < index; i++) { sum += msg->data[i]; }
  }
  return sum & 0xFFU;
}

static void tesla_hw1_rx_hook(const CANPacket_t *msg) {
  if (msg->bus == 0U) {
    if (msg->addr == 0x370U) {
      int angle = ((msg->data[4] & 0x3FU) << 8) | msg->data[5];
      angle -= 8192;
      update_sample(&angle_meas, angle);
      const int hands = msg->data[4] >> 6;
      const int status = msg->data[6] >> 5;
      const int error = msg->data[2] >> 4;
      steering_disengage = (hands >= 3) || ((status == 0) && (error == 9));
    }
    if (msg->addr == 0x155U) {
      UPDATE_VEHICLE_SPEED(((msg->data[6] | (msg->data[5] << 8)) * 0.01F) * KPH_TO_MS);
    }
    if (msg->addr == 0x108U) { gas_pressed = msg->data[6] != 0U; }
    if (msg->addr == 0x20aU) { brake_pressed = ((msg->data[0] & 0x0CU) >> 2) != 1U; }
    if (msg->addr == 0x368U) {
      const int state = (msg->data[1] >> 4) & 0x0FU;
      const bool engaged = (state == 2) || (state == 3) || (state == 4) || (state == 6) || (state == 7);
      vehicle_moving = state != 3;
      acc_main_on = (state == 1) || engaged;
      pcm_cruise_check(engaged);
    }
  }
  if (msg->bus == 2U) {
    if (msg->addr == 0x2b9U) { tesla_hw1_stock_aeb = (msg->data[2] & 0x03U) == 1U; }
    if (msg->addr == 0x488U) {
      const bool stock_now = (msg->data[2] >> 6) == 2U;
      if (stock_now && !tesla_hw1_stock_lkas_prev && !controls_allowed) { tesla_hw1_stock_lkas = true; }
      if (!stock_now) { tesla_hw1_stock_lkas = false; }
      tesla_hw1_stock_lkas_prev = stock_now;
    }
  }
}

static bool tesla_hw1_tx_hook(const CANPacket_t *msg) {
  const AngleSteeringLimits ANGLE_LIMITS = {.max_angle = 3600, .angle_deg_to_can = 10, .frequency = 50U};
  const AngleSteeringParams VM = {.slip_factor = -0.0005666493436310427F, .steer_ratio = 15.0F, .wheelbase = 2.96F};
  const LongitudinalLimits LONG_LIMITS = {.max_accel = 463, .min_accel = 288, .inactive_accel = 375};
  bool violation = tesla_hw1_checksum_index(msg) < 0;
  violation |= tesla_hw1_checksum(msg) != tesla_hw1_compute_checksum(msg);
  if (msg->addr == 0x488U) {
    int angle = ((msg->data[0] & 0x7FU) << 8) | msg->data[1];
    angle -= 16384;
    const int type = msg->data[2] >> 6;
    violation |= (type != 0) && (type != 1);
    violation |= tesla_hw1_stock_lkas;
    violation |= steer_angle_cmd_checks_vm(angle, type == 1, ANGLE_LIMITS, VM);
  }
  if (msg->addr == 0x2b9U) {
    const int maximum = ((msg->data[6] & 0x1FU) << 4) | (msg->data[5] >> 4);
    const int minimum = ((msg->data[5] & 0x0FU) << 5) | (msg->data[4] >> 3);
    const int state = msg->data[1] >> 4;
    const int jerk_min = ((msg->data[3] & 0x07U) << 6) | (msg->data[2] >> 2);
    const int jerk_max = ((msg->data[4] & 0x07U) << 5) | (msg->data[3] >> 3);
    violation |= (state != 4) && (state != 13);
    violation |= (jerk_min < 344) || (jerk_min > 508) || (jerk_max > 83);
    violation |= (msg->data[2] & 0x03U) != 0U;
    violation |= tesla_hw1_stock_aeb;
    if (tesla_hw1_long && (state != 13)) {
      violation |= (maximum < LONG_LIMITS.inactive_accel) && (minimum < LONG_LIMITS.inactive_accel);
      violation |= longitudinal_accel_checks(maximum, LONG_LIMITS);
      violation |= longitudinal_accel_checks(minimum, LONG_LIMITS);
    } else {
      violation |= state != 13;
      violation |= (maximum != LONG_LIMITS.inactive_accel) || (minimum != LONG_LIMITS.inactive_accel);
    }
  }
  return !violation;
}

static bool tesla_hw1_fwd_hook(int bus_num, int addr) {
  return (bus_num == 2) && (((addr == 0x488) && !tesla_hw1_stock_lkas) ||
         ((addr == 0x2b9) && tesla_hw1_long && !tesla_hw1_stock_aeb));
}

static safety_config tesla_hw1_init(uint16_t param) {
  tesla_hw1_long = GET_FLAG(param, 1U);
  tesla_hw1_stock_aeb = false;
  tesla_hw1_stock_lkas = false;
  tesla_hw1_stock_lkas_prev = false;
  static const CanMsg TX[] = {
    {0x488, 0, 4, .check_relay = true, .disable_static_blocking = true},
    {0x2b9, 0, 8, .check_relay = true, .disable_static_blocking = true},
  };
  static RxCheck RX[] = {
    {.msg = {{0x108, 0, 8, 100U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, {0}, {0}}},
    {.msg = {{0x2b9, 2, 8, 25U, .ignore_quality_flag = true, .max_counter = 7U}, {0}, {0}}},
    {.msg = {{0x370, 0, 8, 25U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, {0}, {0}}},
    {.msg = {{0x155, 0, 8, 50U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, {0}, {0}}},
    {.msg = {{0x20a, 0, 8, 50U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, {0}, {0}}},
    {.msg = {{0x368, 0, 8, 10U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, {0}, {0}}},
    {.msg = {{0x488, 2, 4, 50U, .ignore_quality_flag = true, .max_counter = 15U}, {0}, {0}}},
  };
  return BUILD_SAFETY_CFG(RX, TX);
}

const safety_hooks tesla_hw1_hooks = {
  .init = tesla_hw1_init, .rx = tesla_hw1_rx_hook, .tx = tesla_hw1_tx_hook, .fwd = tesla_hw1_fwd_hook,
  .get_counter = tesla_hw1_counter, .get_checksum = tesla_hw1_checksum, .compute_checksum = tesla_hw1_compute_checksum,
};
