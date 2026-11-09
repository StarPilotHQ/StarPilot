#pragma once

#include "opendbc/safety/declarations.h"
#include "opendbc/safety/modes/hyundai_common.h"
#include "opendbc/safety/modes/hyundai_daw.h"
#include "opendbc/safety/modes/hyundai_classic_non_scc_aol.h"
#include "opendbc/safety/modes/hyundai_blended_stock_aol.h"

#define HYUNDAI_LIMITS(steer, rate_up, rate_down) { \
  .max_torque = (steer), \
  .max_rate_up = (rate_up), \
  .max_rate_down = (rate_down), \
  .max_rt_delta = 112, \
  .driver_torque_allowance = 50, \
  .driver_torque_multiplier = 2, \
  .type = TorqueDriverLimited, \
   /* the EPS faults when the steering angle is above a certain threshold for too long. to prevent this, */ \
   /* we allow setting CF_Lkas_ActToi bit to 0 while maintaining the requested torque value for two consecutive frames */ \
  .min_valid_request_frames = 89, \
  .max_invalid_request_frames = 2, \
  .min_valid_request_rt_interval = 810000,  /* 810ms; a ~10% buffer on cutting every 90 frames */ \
  .has_steer_req_tolerance = true, \
}

extern const LongitudinalLimits HYUNDAI_LONG_LIMITS;
const LongitudinalLimits HYUNDAI_LONG_LIMITS = {
  .max_accel = 200,   // 1/100 m/s2
  .min_accel = -350,  // 1/100 m/s2
};

#define HYUNDAI_COMMON_TX_MSGS(scc_bus, refresh) \
  {0x340, 0,       8, .check_relay = true},   /* LKAS11 Bus 0                              */ \
  {0x4F1, scc_bus, 4, .check_relay = false},  /* CLU11 Bus 0 (radar-SCC) or 2 (camera-SCC) */ \
  {0x485, 0, (refresh) ? 8 : 4, .check_relay = true}, /* LFAHDA_MFC Bus 0 */ \

#define HYUNDAI_LONG_COMMON_TX_MSGS(scc_bus, refresh) \
  HYUNDAI_COMMON_TX_MSGS(scc_bus, refresh) \
  {0x420, 0,       8, .check_relay = true},   /* SCC11 Bus 0       */ \
  {0x421, 0,       8, .check_relay = true},   /* SCC12 Bus 0       */ \
  {0x50A, 0,       8, .check_relay = true},   /* SCC13 Bus 0       */ \
  {0x389, 0,       8, .check_relay = true},   /* SCC14 Bus 0       */ \
  {0x4A2, 0,       2, .check_relay = false},  /* FRT_RADAR11 Bus 0 */ \

#define HYUNDAI_COMMON_RX_CHECKS(legacy)                                                                                                                                               \
  {.msg = {{0x260, 0, 8, 100U, .max_counter = 3U, .ignore_quality_flag = true},                                                                                           \
           {0x371, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }}},                                                    \
  {.msg = {{0x386, 0, 8, 100U, .ignore_checksum = (legacy), .ignore_counter = (legacy), .max_counter = (legacy) ? 0U : 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{0x394, 0, 8, 100U, .ignore_checksum = (legacy), .ignore_counter = (legacy), .max_counter = (legacy) ? 0U : 7U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{0x251, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},                                              \
  {.msg = {{0x4F1, 0, 4, 50U, .ignore_checksum = true, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},                                                  \

#define HYUNDAI_SCC12_ADDR_CHECK(scc_bus)                                                                            \
  {.msg = {{0x421, (scc_bus), 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \

#define HYUNDAI_FCEV_GAS_ADDR_CHECK \
  {.msg = {{0x91,  0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \

#define HYUNDAI_NON_SCC_HEV_ADDR_CHECK \
  {.msg = {{0x595, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \

#define HYUNDAI_NON_SCC_EV_ADDR_CHECK \
  {.msg = {{0x329, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \

#define HYUNDAI_LDA_BUTTON_ADDR_CHECK \
  {.msg = {{0x391, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \

// Ordinary-cruise combustion cars require EMS16 itself; E_EMS11 is not a
// substitute for the cruise lamp even though it is a gas-source alternative
// on other Hyundai configurations.
#define HYUNDAI_NON_SCC_ICE_RX_CHECKS \
  {.msg = {{0x260, 0, 8, 100U, .max_counter = 3U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{0x386, 0, 8, 100U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{0x394, 0, 8, 100U, .max_counter = 7U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{0x251, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{0x4F1, 0, 4, 50U, .ignore_checksum = true, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \

static const CanMsg HYUNDAI_TX_MSGS[] = {
  HYUNDAI_COMMON_TX_MSGS(0, false)
};

// Classic-mode-only meanings. CANFD Carnival/angle bit8192 is untouched.
// Exact stock words deliberately do not admit blended alpha-long or other flags.
static bool hyundai_blended_stock = false;
// Exact prepared mixed longitudinal profiles.
static bool hyundai_blended_alpha = false;
static bool hyundai_blended_cancel_armed = false;
static bool hyundai_blended_tcs_seen = false;
static uint32_t hyundai_blended_tcs_ts = 0U;
static bool hyundai_blended_mirror_pending = false;
static bool hyundai_blended_hda2 = false;

#define HYUNDAI_BLENDED_COMMON_RX_CHECKS(pt_bus, speed_frequency) \
  {.msg = {{0x260, (pt_bus), 8, 100U, .max_counter = 3U, .ignore_quality_flag = true}, \
           {0x371, (pt_bus), 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}}}, \
  {.msg = {{0x386, (pt_bus), 8, (speed_frequency), .max_counter = 15U, .ignore_quality_flag = true}, {0}, {0}}}, \
  {.msg = {{0x394, (pt_bus), 8, (speed_frequency), .max_counter = 7U, .ignore_quality_flag = true}, {0}, {0}}}, \
  {.msg = {{0x251, (pt_bus), 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}}, \
  {.msg = {{0x4F1, (pt_bus), 4, 50U, .ignore_checksum = true, .max_counter = 15U, .ignore_quality_flag = true}, {0}, {0}}}, \
  {.msg = {{0x420, (pt_bus), 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}}, \
  /* Stock SCC12 CRC is unverified: preserve original CRC omission explicitly. */ \
  /* Original max_counter15 enforced even though its ignore_counter was true. */ \
  {.msg = {{0x421, (pt_bus), 8, 50U, .ignore_checksum = true, .max_counter = 15U, .ignore_quality_flag = true}, {0}, {0}}},

#define HYUNDAI_BLENDED_ALPHA_RX_CHECKS(pt_bus, speed_frequency) \
  {.msg = {{0x260, (pt_bus), 8, 100U, .max_counter = 3U, .ignore_quality_flag = true}, \
           {0x371, (pt_bus), 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}}}, \
  {.msg = {{0x386, (pt_bus), 8, (speed_frequency), .max_counter = 15U, .ignore_quality_flag = true}, {0}, {0}}}, \
  {.msg = {{0x394, (pt_bus), 8, (speed_frequency), .max_counter = 7U, .ignore_quality_flag = true}, {0}, {0}}}, \
  {.msg = {{0x251, (pt_bus), 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}}, \
  {.msg = {{0x4F1, (pt_bus), 4, 50U, .ignore_checksum = true, .max_counter = 15U, .ignore_quality_flag = true}, {0}, {0}}},

static bool hyundai_legacy = false;
static bool hyundai_ray_pedal = false;
static bool hyundai_ray_pedal_healthy = false;
static uint32_t hyundai_ray_pedal_ts = 0U;

static uint8_t hyundai_ray_pedal_checksum(const CANPacket_t *msg) {
  uint8_t crc = 0xFFU;
  for (int i = 4; i >= 0; i--) {
    crc ^= msg->data[i];
    for (int j = 0; j < 8; j++) {
      crc = (crc & 0x80U) ? (uint8_t)((crc << 1U) ^ 0xD5U) : (uint8_t)(crc << 1U);
    }
  }
  return crc;
}

static uint8_t hyundai_get_counter(const CANPacket_t *msg) {
  uint8_t cnt = 0;
  if (hyundai_blended_hda2 && (msg->addr == 0x2A4U)) {
    cnt = msg->data[2];
  } else {
    if (msg->addr == 0x260U) {
      cnt = (msg->data[7] >> 4) & 0x3U;
    }
    if (msg->addr == 0x386U) {
      cnt = ((msg->data[3] >> 6) << 2) | (msg->data[1] >> 6);
    }
    if (msg->addr == 0x394U) {
      cnt = (msg->data[1] >> 5) & 0x7U;
    } else if (msg->addr == 0x421U) {
      cnt = hyundai_blended_stock ? ((msg->data[1] >> 4U) & 0xFU) : (msg->data[7] & 0xFU);
    } else if (hyundai_ray_pedal && (msg->addr == 0x201U)) {
      cnt = msg->data[4] & 0xFU;
    } else {
      // Retain the counter already selected above for other addresses.
    }
    if (msg->addr == 0x4F1U) {
      cnt = (msg->data[3] >> 4) & 0xFU;
    }
  }
  return cnt;
}

static uint32_t hyundai_get_checksum(const CANPacket_t *msg) {
  uint32_t result = 0U;
  if (hyundai_blended_hda2 && (msg->addr == 0x2A4U)) {
    result = GET_BYTES_LE(msg, 0, 2);
  } else {
    uint8_t chksum = 0;
    if (msg->addr == 0x260U) {
      chksum = msg->data[7] & 0xFU;
    }
    if (msg->addr == 0x386U) {
      chksum = ((msg->data[7] >> 6) << 2) | (msg->data[5] >> 6);
    }
    if (msg->addr == 0x394U) {
      chksum = msg->data[6] & 0xFU;
    }
    if (msg->addr == 0x421U) {
      chksum = msg->data[7] >> 4;
    }
    if (hyundai_ray_pedal && (msg->addr == 0x201U)) {
      chksum = msg->data[5];
    }
    result = chksum;
  }
  return result;
}

static uint32_t hyundai_compute_checksum(const CANPacket_t *msg) {
  uint32_t result = 0U;
  if (hyundai_blended_hda2 && (msg->addr == 0x2A4U)) {
    result = hyundai_common_canfd_compute_checksum(msg);
  } else {
    uint8_t chksum = 0;
    if (hyundai_ray_pedal && (msg->addr == 0x201U)) {
      chksum = hyundai_ray_pedal_checksum(msg);
    } else if (msg->addr == 0x386U) {
      // count the bits
      for (int i = 0; i < 8; i++) {
        uint8_t b = msg->data[i];
        for (int j = 0; j < 8; j++) {
          uint8_t bit = 0;
          // exclude checksum and counter
          if (((i != 1) || (j < 6)) && ((i != 3) || (j < 6)) && ((i != 5) || (j < 6)) && ((i != 7) || (j < 6))) {
            bit = (b >> (uint8_t)j) & 1U;
          }
          chksum += bit;
        }
      }
      chksum = (chksum ^ 9U) & 15U;
    } else {
      // sum of nibbles
      for (int i = 0; i < 8; i++) {
        if ((msg->addr == 0x394U) && (i == 7)) {
          continue;  // exclude
        }
        uint8_t b = msg->data[i];
        if (((msg->addr == 0x260U) && (i == 7)) || ((msg->addr == 0x394U) && (i == 6)) || ((msg->addr == 0x421U) && (i == 7))) {
          b &= (msg->addr == 0x421U) ? 0x0FU : 0xF0U;  // remove checksum
        }
        chksum += (b % 16U) + (b / 16U);
      }
      chksum = (16U - (chksum %  16U)) % 16U;
    }

    result = chksum;
  }
  return result;
}

// A held cancel pair cannot survive an intervening inhibit or stale source.
static void hyundai_blended_cancel_check(void) {
  if (hyundai_blended_alpha && (cruise_engaged_prev || brake_pressed || gas_pressed ||
      safety_rx_checks_invalid || relay_malfunction || !hyundai_blended_tcs_seen ||
      (safety_get_ts_elapsed(microsecond_timer_get(), hyundai_blended_tcs_ts) > 100000U))) {
    hyundai_blended_cancel_armed = false;
  }
}

static void hyundai_rx_hook(const CANPacket_t *msg) {
  classic_non_scc_aol_rx(msg);
  blended_aol_rx(msg);
  hyundai_blended_cancel_check();

  const uint8_t pt_bus = hyundai_blended_hda2 ? 1U : 0U;

  if (hyundai_blended_alpha && (msg->bus == pt_bus) && (msg->addr == 0x394U)) {
    hyundai_blended_tcs_seen = true;
    hyundai_blended_tcs_ts = microsecond_timer_get();
    cruise_engaged_prev = GET_BIT(msg, 54U);
  }

  // SCC12 is on bus 2 for camera-based SCC cars, bus 0 on all others
  if ((msg->addr == 0x421U) && !hyundai_non_scc && !hyundai_blended_alpha) {
    if (((msg->bus == pt_bus) && !hyundai_camera_scc) || ((msg->bus == 2U) && hyundai_camera_scc)) {
      // 2 bits: 13-14
      int cruise_engaged = hyundai_blended_stock ? ((msg->data[3] >> 4U) & 0x3U) : ((GET_BYTES_LE(msg, 0, 4) >> 13) & 0x3U);
      hyundai_common_cruise_state_check(cruise_engaged);
    }
  }

  if (hyundai_blended_stock && (msg->addr == 0x421U) && (msg->bus == pt_bus)) {
    acc_main_on = GET_BIT(msg, 27U);
  }

  if (msg->bus == pt_bus) {
    if (hyundai_ray_pedal && (msg->addr == 0x592U)) {
      acc_main_on = GET_BIT(msg, 34U);
      hyundai_common_cruise_state_check(GET_BIT(msg, 35U));
    }
    if (hyundai_non_scc) {
      if ((msg->addr == 0x595U) && hyundai_hybrid_gas_signal) {
        hyundai_common_cruise_state_check(GET_BIT(msg, 51U));
      } else if ((msg->addr == 0x329U) && hyundai_ev_gas_signal) {
        hyundai_common_cruise_state_check(GET_BIT(msg, 30U));
      } else if ((msg->addr == 0x260U) && !hyundai_ev_gas_signal && !hyundai_hybrid_gas_signal) {
        hyundai_common_cruise_state_check(GET_BIT(msg, 26U));
      } else {
        // No alternate cruise source is selected for this message.
      }
    }
    if (msg->addr == 0x251U) {
      int torque_driver_new = (GET_BYTES_LE(msg, 0, 2) & 0x7ffU) - 1024U;
      // update array of samples
      update_sample(&torque_driver, torque_driver_new);
    }

    // ACC steering wheel buttons
    if (msg->addr == 0x4F1U) {
      int cruise_button = msg->data[0] & 0x7U;
      bool main_button = GET_BIT(msg, 3U);
      if (hyundai_blended_alpha) {
        bool fresh_tcs = hyundai_blended_tcs_seen && !safety_rx_checks_invalid &&
                         (safety_get_ts_elapsed(microsecond_timer_get(), hyundai_blended_tcs_ts) <= 100000U);
        if ((cruise_button == HYUNDAI_BTN_CANCEL) && (cruise_button_prev != HYUNDAI_BTN_CANCEL)) {
          hyundai_blended_cancel_armed = fresh_tcs && !cruise_engaged_prev && !controls_allowed &&
                                        !brake_pressed && !gas_pressed && !relay_malfunction;
          controls_allowed = false;
        } else if ((cruise_button != HYUNDAI_BTN_CANCEL) && (cruise_button_prev == HYUNDAI_BTN_CANCEL)) {
          controls_allowed = hyundai_blended_cancel_armed && fresh_tcs && !cruise_engaged_prev &&
                             !brake_pressed && !gas_pressed;
          hyundai_blended_cancel_armed = false;
        } else if ((cruise_button != HYUNDAI_BTN_SET) && (cruise_button_prev == HYUNDAI_BTN_SET)) {
          controls_allowed = fresh_tcs && !brake_pressed && !gas_pressed;
        } else if ((cruise_button != HYUNDAI_BTN_RESUME) && (cruise_button_prev == HYUNDAI_BTN_RESUME)) {
          controls_allowed = fresh_tcs && !brake_pressed && !gas_pressed;
        } else {
        }
        cruise_button_prev = cruise_button;
      } else {
        hyundai_common_cruise_buttons_check(cruise_button, main_button, false, hyundai_long_main_policy);
      }
    }

    if (hyundai_ray_pedal && (msg->addr == 0x201U)) {
      gas_pressed = ((((uint32_t)msg->data[0] << 8U) | msg->data[1]) > 272U) ||
                    ((((uint32_t)msg->data[2] << 8U) | msg->data[3]) > 513U);
      hyundai_ray_pedal_healthy = (msg->data[4] >> 4U) == 0U;
      hyundai_ray_pedal_ts = microsecond_timer_get();
    }

    // gas press, different for EV, hybrid, and ICE models
    if ((msg->addr == 0x371U) && hyundai_ev_gas_signal && !hyundai_ray_pedal) {
      gas_pressed = (((msg->data[4] & 0x7FU) << 1) | (msg->data[3] >> 7)) != 0U;
    } else if ((msg->addr == 0x371U) && hyundai_hybrid_gas_signal) {
      gas_pressed = msg->data[7] != 0U;
    } else if ((msg->addr == 0x91U) && hyundai_fcev_gas_signal) {
      gas_pressed = msg->data[6] != 0U;
    } else if ((msg->addr == 0x260U) && !hyundai_ev_gas_signal && !hyundai_hybrid_gas_signal &&
               !(classic_scc_aol_enabled && hyundai_fcev_gas_signal)) {
      gas_pressed = (msg->data[7] >> 6) != 0U;
    } else {
    }

    // sample wheel speed, averaging opposite corners
    if (msg->addr == 0x386U) {
      uint32_t front_left_speed = GET_BYTES_LE(msg, 0, 2) & 0x3FFFU;
      uint32_t rear_right_speed = GET_BYTES_LE(msg, 6, 2) & 0x3FFFU;
      vehicle_moving = (front_left_speed > HYUNDAI_STANDSTILL_THRSLD) || (rear_right_speed > HYUNDAI_STANDSTILL_THRSLD);
    }

    if (msg->addr == 0x394U) {
      brake_pressed = ((msg->data[5] >> 5U) & 0x3U) == 0x2U;
    }
  }
  hyundai_blended_cancel_check();
  if (hyundai_ray_pedal && !acc_main_on) {
    controls_allowed = false;
  }
}

static bool hyundai_tx_hook(const CANPacket_t *msg) {
  static int hyundai_blended_mirror_torque = 0;
  static bool hyundai_blended_mirror_request = false;
  static uint32_t hyundai_blended_mirror_ts = 0U;
  hyundai_blended_cancel_check();
  const TorqueSteeringLimits HYUNDAI_STEERING_LIMITS = HYUNDAI_LIMITS(384, 3, 7);
  const TorqueSteeringLimits HYUNDAI_STEERING_LIMITS_BLENDED = HYUNDAI_LIMITS(404, 2, 3);
  const TorqueSteeringLimits HYUNDAI_STEERING_LIMITS_ALT = HYUNDAI_LIMITS(270, 2, 3);
  const TorqueSteeringLimits HYUNDAI_STEERING_LIMITS_ALT_2 = HYUNDAI_LIMITS(170, 2, 3);

  bool tx = (msg->addr != 0x53EU) || hyundai_daw_tx(msg);

  if (hyundai_ray_pedal && (msg->addr == 0x200U)) {
    const uint16_t track1 = ((uint16_t)msg->data[0] << 8U) | msg->data[1];
    const uint16_t track2 = ((uint16_t)msg->data[2] << 8U) | msg->data[3];
    const bool enabled = (msg->data[4] & 0x80U) != 0U;
    const int track_error = (83 * ((int)track2 - 497)) - (168 * ((int)track1 - 264));
    bool rx_valid = hyundai_ray_pedal_healthy && !safety_rx_checks_invalid &&
                    (safety_get_ts_elapsed(microsecond_timer_get(), hyundai_ray_pedal_ts) <= 1000000U);
    for (int i = 0; i < current_safety_config.rx_checks_len; i++) {
      const RxStatus *status = &rx_status[i];
      rx_valid &= status->msg_seen && status->valid_checksum && status->valid_quality_flag &&
                  (status->wrong_counters < MAX_WRONG_COUNTERS) && !status->lagging &&
                  (safety_get_ts_elapsed(microsecond_timer_get(), status->last_timestamp) <= 1000000U);
    }
    if (((msg->data[4] & 0x70U) != 0U) || (msg->data[5] != hyundai_ray_pedal_checksum(msg)) ||
        (enabled && ((track1 < 264U) || (track1 > 530U) || (track2 < 497U) || (track2 > 1035U) ||
                     (track_error < -125) || (track_error > 125))) ||
        (!enabled && ((track1 != 0U) || (track2 != 0U))) ||
        (enabled && (!get_longitudinal_allowed() || brake_pressed_prev || !acc_main_on || !rx_valid))) {
      tx = false;
    }
  }

  // FCA11: Block any potential actuation
  if ((msg->addr == 0x38DU) && !hyundai_blended_alpha) {
    int CR_VSM_DecCmd = msg->data[1];
    bool FCA_CmdAct = GET_BIT(msg, 20U);
    bool CF_VSM_DecCmdAct = GET_BIT(msg, 31U);

    if ((CR_VSM_DecCmd != 0) || FCA_CmdAct || CF_VSM_DecCmdAct) {
      tx = false;
    }
  }

  // ACCEL: safety check
  if ((msg->addr == 0x421U) && !hyundai_blended_alpha) {
    int desired_accel_raw = (((msg->data[4] & 0x7U) << 8) | msg->data[3]) - 1023U;
    int desired_accel_val = ((msg->data[5] << 3) | (msg->data[4] >> 5)) - 1023U;

    int aeb_decel_cmd = msg->data[2];
    bool aeb_req = GET_BIT(msg, 54U);
    bool aeb_stop_req = GET_BIT(msg, 55U);

    bool violation = false;

    violation |= longitudinal_accel_checks(desired_accel_raw, HYUNDAI_LONG_LIMITS);
    violation |= longitudinal_accel_checks(desired_accel_val, HYUNDAI_LONG_LIMITS);
    violation |= (aeb_decel_cmd != 0);
    violation |= aeb_req;
    violation |= aeb_stop_req;

    if (violation) {
      tx = false;
    }
  }

  if (hyundai_blended_alpha && (msg->addr == 0x420U)) {
    const LongitudinalLimits blended_long_limits = {.max_accel = 350, .min_accel = -350};
    uint32_t raw = (((uint32_t)msg->data[4] & 0x3FU) << 5U) | ((uint32_t)msg->data[3] >> 3U);
    uint32_t value = (((uint32_t)msg->data[3] & 0x7U) << 8U) | msg->data[2];
    if (longitudinal_accel_checks((int)raw - 1023, blended_long_limits) ||
        longitudinal_accel_checks((int)value - 1023, blended_long_limits)) {
      tx = false;
    }
  }
  // Exact reached ADRV source supplies only the packer's checksum and counter.
  if (hyundai_blended_alpha && (msg->addr == 0x51U)) {
    for (unsigned int i = 3U; i < 32U; i++) {
      if (msg->data[i] != 0U) {
        tx = false;
      }
    }
  }
  // Radar auxiliary payloads are fixed source bytes; no arbitrary new fields.
  if (hyundai_blended_alpha && ((msg->addr == 0x363U) || (msg->addr == 0x398U) ||
      (msg->addr == 0x399U) || (msg->addr == 0x39AU) || (msg->addr == 0x39BU) ||
      (msg->addr == 0x39CU) || (msg->addr == 0x43AU))) {
    uint8_t expected[8] = {0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
    if (msg->addr == 0x363U) {
      expected[1] = 1U;
    } else if (msg->addr == 0x398U) {
      expected[4] = 0x80U;
      expected[5] = hyundai_blended_hda2 ? 0x5DU : 0x10U;
    } else if (msg->addr == 0x399U) {
      expected[2] = 2U;
    } else if (msg->addr == 0x39AU) {
      expected[7] = 0xFFU;
    } else if (msg->addr == 0x39CU) {
      expected[5] = 0xE0U;
      expected[6] = 0x79U;
    } else if (msg->addr == 0x43AU) {
      expected[2] = 7U;
    } else {
    }
    if ((msg->data[1] & 0xFU) != expected[1]) {
      tx = false;
    }
    for (unsigned int i = 2U; i < 8U; i++) {
      if (msg->data[i] != expected[i]) {
        tx = false;
      }
    }
  }
  // HDAI FCA remains closed: reached old host and native layouts conflict.
  // HDAII preserves the old explicit request guard and exact source sentinels.
  if (hyundai_blended_alpha && (msg->addr == 0x38DU)) {
    if (!hyundai_blended_hda2 || GET_BIT(msg, 16U) || GET_BIT(msg, 19U) ||
        ((msg->data[1] & 0xFU) != 0U) || ((msg->data[1] >> 4U) == 15U) ||
        (msg->data[2] != 0U) || (msg->data[3] != 0U) ||
        (msg->data[4] != 0xC0U) || (msg->data[5] != 0x3FU) ||
        (msg->data[6] != 0x7FU) || (msg->data[7] != 0U)) {
      tx = false;
    }
  }
  if (hyundai_blended_alpha && (msg->addr == 0x4F1U)) {
    tx = false;
  }
  if (hyundai_blended_alpha && hyundai_blended_hda2 && (msg->addr == 0x340U)) {
    const uint32_t torque_raw = (GET_BYTES_LE(msg, 0, 4) >> 16U) & 0x7FFU;
    int torque = (int)torque_raw - 1024;
    bool request = GET_BIT(msg, 27U);
    if (!hyundai_blended_mirror_pending || (torque != hyundai_blended_mirror_torque) ||
        (request != hyundai_blended_mirror_request) ||
        (safety_get_ts_elapsed(microsecond_timer_get(), hyundai_blended_mirror_ts) > 10000U) ||
        (!lateral_controls_allowed() && ((torque != 0) || request))) {
      tx = false;
    }
    hyundai_blended_mirror_pending = false;
  }

  // LKA STEER: safety check
  if ((msg->addr == 0x340U) && !hyundai_blended_hda2) {
    int desired_torque = ((GET_BYTES_LE(msg, 0, 4) >> 16) & 0x7ffU) - 1024U;
    bool steer_req = GET_BIT(msg, 27U);

    const TorqueSteeringLimits limits = hyundai_blended_stock ? HYUNDAI_STEERING_LIMITS_BLENDED :
                                        hyundai_alt_limits_2 ? HYUNDAI_STEERING_LIMITS_ALT_2 :
                                        hyundai_alt_limits ? HYUNDAI_STEERING_LIMITS_ALT : HYUNDAI_STEERING_LIMITS;

    if (steer_torque_cmd_checks(desired_torque, steer_req, limits)) {
      tx = false;
    }
  }

  if (hyundai_blended_hda2 && (msg->addr == 0x50U)) {
    uint32_t raw_torque = ((((uint32_t)msg->data[6] & 0xFU) << 7U) | ((uint32_t)msg->data[5] >> 1U));
    int desired_torque = (int)raw_torque - 1024;
    bool steer_req = GET_BIT(msg, 52U);
    if (steer_torque_cmd_checks(desired_torque, steer_req, HYUNDAI_STEERING_LIMITS)) {
      tx = false;
    }
  }

  // Source-backed HDAII dashboard status: checksum, counter and LFA icon only.
  if ((hyundai_blended_hda2 || hyundai_blended_alpha) && (msg->addr == 0x485U)) {
    if (((msg->data[1] & 0xFU) != 0U) || (msg->data[2] != 0U) ||
        ((msg->data[3] & 0xF9U) != 0U) || (GET_BYTES_LE(msg, 4, 4) != 0U)) {
      tx = false;
    }
  }

  // UDS: Only tester present ("\x02\x3E\x80\x00\x00\x00\x00\x00") allowed on diagnostics address
  if ((msg->addr == 0x7D0U) || (hyundai_blended_alpha && (msg->addr == 0x730U))) {
    if (GET_BYTES_64_LE(msg, 0, 8) != 0x0000000000803E02ULL) {
      tx = false;
    }
  }

  // BUTTONS: used for resume spamming and cruise cancellation
  if ((msg->addr == 0x4F1U) && !hyundai_longitudinal) {
    int button = msg->data[0] & 0x7U;

    bool allowed_resume = (button == 1) && controls_allowed;
    bool allowed_set = hyundai_blended_stock && (button == 2) && controls_allowed;
    bool allowed_cancel = (button == 4) && cruise_engaged_prev;
    if (!(allowed_resume || allowed_set || allowed_cancel)) {
      tx = false;
    }
  }

  tx &= blended_aol_integrity(msg);
  if (hyundai_blended_alpha && hyundai_blended_hda2 && (msg->addr == 0x50U)) {
    hyundai_blended_mirror_pending = tx && !relay_malfunction && !safety_rx_checks_invalid;
    const uint32_t mirror_torque_raw = (((uint32_t)msg->data[6] & 0xFU) << 7U) | ((uint32_t)msg->data[5] >> 1U);
    hyundai_blended_mirror_torque = (int)mirror_torque_raw - 1024;
    hyundai_blended_mirror_request = GET_BIT(msg, 52U);
    hyundai_blended_mirror_ts = microsecond_timer_get();
  }
  blended_aol_tx(msg, tx);
  return tx;
}

static void hyundai_classic_scc_aol_rx_config(safety_config *config, bool legacy) {
  static RxCheck normal[] = {
    HYUNDAI_COMMON_RX_CHECKS(false)
    HYUNDAI_SCC12_ADDR_CHECK(0)
    {.msg = {{0x420, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
  };
  static RxCheck camera[] = {
    HYUNDAI_COMMON_RX_CHECKS(false)
    HYUNDAI_SCC12_ADDR_CHECK(2)
    {.msg = {{0x420, 2, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
  };
  static RxCheck older[] = {
    HYUNDAI_COMMON_RX_CHECKS(true)
    HYUNDAI_SCC12_ADDR_CHECK(0)
    {.msg = {{0x420, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
  };
  static RxCheck fcev[] = {
    HYUNDAI_COMMON_RX_CHECKS(false)
    HYUNDAI_SCC12_ADDR_CHECK(0)
    {.msg = {{0x420, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    HYUNDAI_FCEV_GAS_ADDR_CHECK
  };
  if (classic_scc_aol_enabled) {
    if (hyundai_fcev_gas_signal && !legacy) {
      SET_RX_CHECKS(fcev, (*config));
    } else if (legacy) {
      SET_RX_CHECKS(older, (*config));
    } else if (classic_scc_aol_camera) {
      SET_RX_CHECKS(camera, (*config));
    } else {
      SET_RX_CHECKS(normal, (*config));
    }
  }
}

static RxCheck hyundai_long_rx_checks[] = {
  HYUNDAI_COMMON_RX_CHECKS(false)
};

static void hyundai_long_rx_variant(bool legacy) {
  hyundai_long_rx_checks[1].msg[0].ignore_checksum = legacy;
  hyundai_long_rx_checks[1].msg[0].ignore_counter = legacy;
  hyundai_long_rx_checks[1].msg[0].max_counter = legacy ? 0U : 15U;
  hyundai_long_rx_checks[2].msg[0].ignore_checksum = legacy;
  hyundai_long_rx_checks[2].msg[0].ignore_counter = legacy;
  hyundai_long_rx_checks[2].msg[0].max_counter = legacy ? 0U : 7U;
}

#ifdef ALLOW_DEBUG
static const CanMsg HYUNDAI_LONG_TX_MSGS[] = {
    HYUNDAI_LONG_COMMON_TX_MSGS(0, false)
    {0x38D, 0, 8, .check_relay = false}, // FCA11 Bus 0
    {0x483, 0, 8, .check_relay = false}, // FCA12 Bus 0
    {0x7D0, 0, 8, .check_relay = false}, // radar UDS TX addr Bus 0 (for radar disable)
  };
#endif

static safety_config hyundai_init(uint16_t param) {
#ifndef ALLOW_DEBUG
  static const CanMsg HYUNDAI_LONG_TX_MSGS[] = {
      HYUNDAI_LONG_COMMON_TX_MSGS(0, false)
      {0x38D, 0, 8, .check_relay = false}, // FCA11 Bus 0
      {0x483, 0, 8, .check_relay = false}, // FCA12 Bus 0
      {0x7D0, 0, 8, .check_relay = false}, // radar UDS TX addr Bus 0 (for radar disable)
    };
#endif
  hyundai_long_rx_variant(false);
  static const CanMsg HYUNDAI_REFRESH_TX_MSGS[] = {
    HYUNDAI_COMMON_TX_MSGS(0, true)
  };

  static const CanMsg HYUNDAI_LONG_REFRESH_TX_MSGS[] = {
    HYUNDAI_LONG_COMMON_TX_MSGS(0, true)
    {0x38D, 0, 8, .check_relay = false},
    {0x483, 0, 8, .check_relay = false},
    {0x7D0, 0, 8, .check_relay = false},
  };

  static const CanMsg HYUNDAI_CAMERA_SCC_TX_MSGS[] = {
    HYUNDAI_COMMON_TX_MSGS(2, false)
  };

  static const CanMsg HYUNDAI_CAMERA_SCC_REFRESH_TX_MSGS[] = {
    HYUNDAI_COMMON_TX_MSGS(2, true)
  };

  static const CanMsg HYUNDAI_CAMERA_SCC_LONG_TX_MSGS[] = {
    HYUNDAI_LONG_COMMON_TX_MSGS(2, false)
  };

  static const CanMsg HYUNDAI_CAMERA_SCC_LONG_REFRESH_TX_MSGS[] = {
    HYUNDAI_LONG_COMMON_TX_MSGS(2, true)
  };

  hyundai_common_init(param);
  hyundai_daw_init(false, false);
  if ((param == 0x800EU) && (alternative_experience == 0)) {
    hyundai_long_main_policy = HYUNDAI_MAIN_TRACK;
  }
  classic_non_scc_aol_configure(param, false);
  blended_aol_configure(param);
  hyundai_blended_alpha = false;
#ifdef ALLOW_DEBUG
  hyundai_blended_alpha = (param == 0x2004U) || (param == 0x2014U);
#endif
  hyundai_blended_cancel_armed = false;
  hyundai_blended_tcs_seen = false;
  hyundai_blended_mirror_pending = false;
  hyundai_blended_stock = (param == 0x2000U) || (param == 0x2010U);
  hyundai_blended_hda2 = (param == 0x2010U);
#ifdef ALLOW_DEBUG
  hyundai_blended_hda2 |= (param == 0x2014U);
#endif
  hyundai_blended_stock |= hyundai_blended_alpha;
  hyundai_ray_pedal = param == 0x9805U;
  hyundai_ray_pedal_healthy = false;
  hyundai_ray_pedal_ts = 0U;
  if (hyundai_ray_pedal) {
    hyundai_longitudinal = true;
  }
  hyundai_legacy = false;

  safety_config ret;
#ifdef ALLOW_DEBUG
  if (hyundai_blended_alpha) {
    static const CanMsg alpha_hda1_tx[] = {
      {0x340, 0, 8, .check_relay = true, .disable_static_blocking = true},
      {0x485, 0, 8, .check_relay = true, .disable_static_blocking = true},
      {0x364, 0, 8, .check_relay = true, .disable_static_blocking = true}, {0x420, 0, 8, .check_relay = true},
      {0x421, 0, 8, .check_relay = true}, {0x389, 0, 8, .check_relay = true},
      {0x7D0, 0, 8, .check_relay = false}, {0x363, 0, 8, .check_relay = false},
      {0x398, 0, 8, .check_relay = false},
    };
    static const CanMsg alpha_hda2_tx[] = {
      {0x50, 0, 16, .check_relay = true, .disable_static_blocking = true},
      {0x2A4, 0, 24, .check_relay = true, .disable_static_blocking = true},
      {0x51, 0, 32, .check_relay = false}, {0x730, 1, 8, .check_relay = false},
      {0x340, 1, 8, .check_relay = true, .disable_static_blocking = true},
      {0x485, 1, 8, .check_relay = true, .disable_static_blocking = true},
      {0x420, 1, 8, .check_relay = true}, {0x421, 1, 8, .check_relay = true},
      {0x389, 1, 8, .check_relay = true}, {0x38D, 1, 8, .check_relay = false},
      {0x363, 1, 8, .check_relay = false},
      {0x398, 1, 8, .check_relay = false}, {0x399, 1, 8, .check_relay = false},
      {0x39A, 1, 8, .check_relay = false}, {0x39B, 1, 8, .check_relay = false},
      {0x39C, 1, 8, .check_relay = false}, {0x43A, 1, 8, .check_relay = false},
    };
    if (hyundai_blended_hda2) {
      static RxCheck alpha_hda2_rx[] = {
        HYUNDAI_BLENDED_ALPHA_RX_CHECKS(1, 50U)
        {.msg = {{0x2A4, 2, 24, 20U, .max_counter = 255U, .ignore_quality_flag = true}, {0}, {0}}},
      };
      ret = BUILD_SAFETY_CFG(alpha_hda2_rx, alpha_hda2_tx);
    } else {
      static RxCheck alpha_hda1_rx[] = {
        HYUNDAI_BLENDED_ALPHA_RX_CHECKS(0, 100U)
        {.msg = {{0x391, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},
                 {0x50C, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},
                 {0x50C, 1, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}}},
      };
      ret = BUILD_SAFETY_CFG(alpha_hda1_rx, alpha_hda1_tx);
    }
  } else
#endif
  if (hyundai_blended_stock) {
    static const CanMsg blended_tx[] = {
      {0x340, 0, 8, .check_relay = true},
      {0x4F1, 0, 4, .check_relay = false},
      {0x485, 0, 8, .check_relay = true},
      {0x364, 0, 8, .check_relay = true},
    };
    static const CanMsg blended_hda2_tx[] = {
      {0x50, 0, 16, .check_relay = true},
      {0x4F1, 1, 4, .check_relay = false},
      {0x2A4, 0, 24, .check_relay = true},
      {0x485, 1, 8, .check_relay = false},
    };
    static const CanMsg blended_hda2_aol_tx[] = {
      {0x50, 0, 16, .check_relay = true, .disable_static_blocking = true},
      {0x4F1, 1, 4, .check_relay = false},
      {0x2A4, 0, 24, .check_relay = true, .disable_static_blocking = true},
      {0x485, 1, 8, .check_relay = false, .disable_static_blocking = true},
    };
    static const CanMsg blended_aol_tx_msgs[] = {
      {0x340, 0, 8, .check_relay = true, .disable_static_blocking = true},
      {0x4F1, 0, 4, .check_relay = false},
      {0x485, 0, 8, .check_relay = true, .disable_static_blocking = true},
      {0x364, 0, 8, .check_relay = true, .disable_static_blocking = true},
    };
    if (hyundai_blended_hda2) {
      static RxCheck blended_hda2_rx[] = {
        HYUNDAI_BLENDED_COMMON_RX_CHECKS(1, 50U)
        {.msg = {{0x2A4, 2, 24, 20U, .max_counter = 255U, .ignore_quality_flag = true}, {0}, {0}}},
      };
      ret = blended_aol_enabled ? BUILD_SAFETY_CFG(blended_hda2_rx, blended_hda2_aol_tx) :
                                BUILD_SAFETY_CFG(blended_hda2_rx, blended_hda2_tx);
    } else {
      static RxCheck blended_rx[] = {HYUNDAI_BLENDED_COMMON_RX_CHECKS(0, 100U)};
      ret = blended_aol_enabled ? BUILD_SAFETY_CFG(blended_rx, blended_aol_tx_msgs) : BUILD_SAFETY_CFG(blended_rx, blended_tx);
    }
  } else if (hyundai_ray_pedal) {
    static const CanMsg ray_tx_msgs[] = {
      HYUNDAI_COMMON_TX_MSGS(0, true)
      {0x200, 0, 6, .check_relay = false},
    };
    static RxCheck ray_rx_checks[] = {
      HYUNDAI_COMMON_RX_CHECKS(false)
      {.msg = {{0x592, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      HYUNDAI_LDA_BUTTON_ADDR_CHECK
      {.msg = {{0x201, 0, 6, 50U, .max_counter = 15U, .ignore_quality_flag = true}, {0}, {0}}},
    };
    SET_RX_CHECKS(ray_rx_checks, ret);
    SET_TX_MSGS(ray_tx_msgs, ret);
  } else if (hyundai_longitudinal) {
    // Use CLU11 (buttons) to manage controls allowed instead of SCC cruise state

    static const RxCheck hyundai_fcev_long_rx_checks[] = {
      HYUNDAI_COMMON_RX_CHECKS(false)
      HYUNDAI_FCEV_GAS_ADDR_CHECK
    };

    if (hyundai_fcev_gas_signal) {
      SET_RX_CHECKS(hyundai_fcev_long_rx_checks, ret);
    } else {
      SET_RX_CHECKS(hyundai_long_rx_checks, ret);
    }
    if (hyundai_camera_scc) {
      if (hyundai_can_refresh_msgs) {
        SET_TX_MSGS(HYUNDAI_CAMERA_SCC_LONG_REFRESH_TX_MSGS, ret);
      } else {
        SET_TX_MSGS(HYUNDAI_CAMERA_SCC_LONG_TX_MSGS, ret);
      }
    } else {
      if (hyundai_can_refresh_msgs) {
        SET_TX_MSGS(HYUNDAI_LONG_REFRESH_TX_MSGS, ret);
      } else {
        SET_TX_MSGS(HYUNDAI_LONG_TX_MSGS, ret);
      }
    }
  } else if (hyundai_camera_scc) {
    static const RxCheck hyundai_cam_scc_rx_checks[] = {
      HYUNDAI_COMMON_RX_CHECKS(false)
      HYUNDAI_SCC12_ADDR_CHECK(2)
    };

    if (hyundai_can_refresh_msgs) {
      SET_RX_CHECKS(hyundai_cam_scc_rx_checks, ret);
      SET_TX_MSGS(HYUNDAI_CAMERA_SCC_REFRESH_TX_MSGS, ret);
    } else {
      ret = BUILD_SAFETY_CFG(hyundai_cam_scc_rx_checks, HYUNDAI_CAMERA_SCC_TX_MSGS);
    }
  } else {
    static const RxCheck hyundai_rx_checks[] = {
       HYUNDAI_COMMON_RX_CHECKS(false)
       HYUNDAI_SCC12_ADDR_CHECK(0)
    };

    static const RxCheck hyundai_fcev_rx_checks[] = {
      HYUNDAI_COMMON_RX_CHECKS(false)
      HYUNDAI_SCC12_ADDR_CHECK(0)
      HYUNDAI_FCEV_GAS_ADDR_CHECK
    };

    static RxCheck hyundai_non_scc_rx_checks[] = {
      HYUNDAI_NON_SCC_ICE_RX_CHECKS
    };
    static RxCheck hyundai_non_scc_lda_rx_checks[] = {
      HYUNDAI_NON_SCC_ICE_RX_CHECKS
      HYUNDAI_LDA_BUTTON_ADDR_CHECK
    };
    static RxCheck hyundai_classic_non_scc_aol_lda_rx_checks[] = {
      HYUNDAI_NON_SCC_ICE_RX_CHECKS
      {.msg = {{0x391, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},
               {0x50C, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}}},
    };
    #define HYUNDAI_CLASSIC_AOL_LDA_RX_CHECKS \
      {.msg = {{0x391, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, \
               {0x50C, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}}},
    #define HYUNDAI_CLASSIC_AOL_EV_MAIN_RX_CHECK \
      {.msg = {{0x592, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    static RxCheck hyundai_classic_aol_hev_lda_rx_checks[] = {
      HYUNDAI_COMMON_RX_CHECKS(false)
      HYUNDAI_NON_SCC_HEV_ADDR_CHECK
      HYUNDAI_CLASSIC_AOL_LDA_RX_CHECKS
    };
    static RxCheck hyundai_classic_aol_ev_rx_checks[] = {
      HYUNDAI_COMMON_RX_CHECKS(false)
      HYUNDAI_NON_SCC_EV_ADDR_CHECK
      HYUNDAI_CLASSIC_AOL_EV_MAIN_RX_CHECK
    };
    static RxCheck hyundai_classic_aol_ev_lda_rx_checks[] = {
      HYUNDAI_COMMON_RX_CHECKS(false)
      HYUNDAI_NON_SCC_EV_ADDR_CHECK
      HYUNDAI_CLASSIC_AOL_EV_MAIN_RX_CHECK
      HYUNDAI_CLASSIC_AOL_LDA_RX_CHECKS
    };
    static RxCheck hyundai_non_scc_hev_rx_checks[] = {
      HYUNDAI_COMMON_RX_CHECKS(false)
      HYUNDAI_NON_SCC_HEV_ADDR_CHECK
    };
    static RxCheck hyundai_non_scc_hev_lda_rx_checks[] = {
      HYUNDAI_COMMON_RX_CHECKS(false)
      HYUNDAI_NON_SCC_HEV_ADDR_CHECK
      HYUNDAI_LDA_BUTTON_ADDR_CHECK
    };
    static RxCheck hyundai_non_scc_ev_rx_checks[] = {
      HYUNDAI_COMMON_RX_CHECKS(false)
      HYUNDAI_NON_SCC_EV_ADDR_CHECK
    };
    static RxCheck hyundai_non_scc_ev_lda_rx_checks[] = {
      HYUNDAI_COMMON_RX_CHECKS(false)
      HYUNDAI_NON_SCC_EV_ADDR_CHECK
      HYUNDAI_LDA_BUTTON_ADDR_CHECK
    };

    if (hyundai_can_refresh_msgs) {
      SET_TX_MSGS(HYUNDAI_REFRESH_TX_MSGS, ret);
    } else {
      SET_TX_MSGS(HYUNDAI_TX_MSGS, ret);
    }
    if (hyundai_non_scc) {
      if (classic_non_scc_aol_ev) {
        if (classic_non_scc_aol_lda) {
          SET_RX_CHECKS(hyundai_classic_aol_ev_lda_rx_checks, ret);
        } else {
          SET_RX_CHECKS(hyundai_classic_aol_ev_rx_checks, ret);
        }
      } else if (classic_non_scc_aol_hev && classic_non_scc_aol_lda) {
        SET_RX_CHECKS(hyundai_classic_aol_hev_lda_rx_checks, ret);
      } else if (classic_non_scc_aol_enabled && classic_non_scc_aol_lda) {
        SET_RX_CHECKS(hyundai_classic_non_scc_aol_lda_rx_checks, ret);
      } else if (hyundai_ev_gas_signal) {
        if (hyundai_has_lda_button) {
          SET_RX_CHECKS(hyundai_non_scc_ev_lda_rx_checks, ret);
        } else {
          SET_RX_CHECKS(hyundai_non_scc_ev_rx_checks, ret);
        }
      } else if (hyundai_hybrid_gas_signal) {
        if (hyundai_has_lda_button) {
          SET_RX_CHECKS(hyundai_non_scc_hev_lda_rx_checks, ret);
        } else {
          SET_RX_CHECKS(hyundai_non_scc_hev_rx_checks, ret);
        }
      } else if (hyundai_has_lda_button) {
        SET_RX_CHECKS(hyundai_non_scc_lda_rx_checks, ret);
      } else {
        SET_RX_CHECKS(hyundai_non_scc_rx_checks, ret);
      }
    } else if (hyundai_fcev_gas_signal) {
      SET_RX_CHECKS(hyundai_fcev_rx_checks, ret);
    } else {
      SET_RX_CHECKS(hyundai_rx_checks, ret);
    }
  }
  hyundai_classic_scc_aol_rx_config(&ret, false);
  if (classic_long_aol_param(param) && !classic_long_aol_enabled) {
    const safety_config denied = {0};
    ret = denied;
  }
  return hyundai_daw_configure(ret, false, !hyundai_blended_stock && !hyundai_blended_alpha);
}

static safety_config hyundai_legacy_init(uint16_t param) {
  // older hyundai models have less checks due to missing counters and checksums
  static const RxCheck hyundai_legacy_rx_checks[] = {
    HYUNDAI_COMMON_RX_CHECKS(true)
    HYUNDAI_SCC12_ADDR_CHECK(0)
  };

  const bool legacy_long = legacy_long_param(param);
  uint16_t normalized = param;
  if (legacy_long) {
    normalized = ((param & 1U) != 0U) ? 6U : 4U;
    if (legacy_long_aol_param(param)) {
      normalized |= 0x0400U;
    }
    if ((param & 0x10U) != 0U) {
      normalized |= 0x0800U;
    }
  }
  hyundai_common_init(normalized);
  hyundai_daw_init(false, false);
  classic_non_scc_aol_configure(normalized, !legacy_long);
  hyundai_legacy = true;
  hyundai_blended_stock = false;
  hyundai_blended_hda2 = false;
  hyundai_blended_alpha = false;
  hyundai_blended_cancel_armed = false;
  hyundai_blended_tcs_seen = false;
  hyundai_blended_mirror_pending = false;
  hyundai_ray_pedal = false;
  hyundai_ray_pedal_healthy = false;
  hyundai_longitudinal = false;
  hyundai_camera_scc = false;
  safety_config ret = BUILD_SAFETY_CFG(hyundai_legacy_rx_checks, HYUNDAI_TX_MSGS);
  if ((param & 0xFF00U) == 0xE900U) {
    const safety_config denied = {0};
    ret = denied;
#ifdef ALLOW_DEBUG
    const bool marked = legacy_long_aol_param(param);
    const bool alternative_matches = marked ? ((unsigned int)alternative_experience == 32U) : ((unsigned int)alternative_experience == 0U);
    if (legacy_long && alternative_matches) {
      hyundai_long_rx_variant(true);
      hyundai_longitudinal = true;
      SET_RX_CHECKS(hyundai_long_rx_checks, ret);
      SET_TX_MSGS(HYUNDAI_LONG_TX_MSGS, ret);
    }
#endif
  }
  hyundai_classic_scc_aol_rx_config(&ret, true);
  return hyundai_daw_configure(ret, false, true);
}

static void hyundai_optional_rx_hook(const CANPacket_t *msg) {
  hyundai_daw_rx(msg);
  if ((msg->addr == 0x391U) || (msg->addr == 0x50CU)) {
    blended_aol_rx(msg);
  }
  // The unselected physical alternative does not renew mandatory RX health.
  // Only these exact classic LDA profiles may use shape-checked button evidence.
  if (classic_non_scc_aol_enabled && classic_non_scc_aol_lda && (msg->bus == 0U) && (GET_LEN(msg) == 8U) &&
      ((msg->addr == 0x391U) || (msg->addr == 0x50CU))) {
    classic_non_scc_aol_rx(msg);
  }
}

const safety_hooks hyundai_hooks = {
  .fwd_packet = hyundai_daw_fwd,
  .fwd = blended_aol_fwd,
  .init = hyundai_init,
  .optional_rx = hyundai_optional_rx_hook,
  .rx = hyundai_rx_hook,
  .tx = hyundai_tx_hook,
  .get_counter = hyundai_get_counter,
  .get_checksum = hyundai_get_checksum,
  .compute_checksum = hyundai_compute_checksum,
};

const safety_hooks hyundai_legacy_hooks = {
  .fwd_packet = hyundai_daw_fwd,
  .init = hyundai_legacy_init,
  .optional_rx = hyundai_optional_rx_hook,
  .rx = hyundai_rx_hook,
  .tx = hyundai_tx_hook,
  .get_counter = hyundai_get_counter,
  .get_checksum = hyundai_get_checksum,
  .compute_checksum = hyundai_compute_checksum,
};
