#pragma once

#include "opendbc/safety/declarations.h"
#include "opendbc/safety/modes/toyota_aol.h"

// Stock longitudinal
#define TOYOTA_BASE_TX_MSGS \
  {0x191, 0, 8, .check_relay = true}, {0x412, 0, 8, .check_relay = true}, {0x1D2, 0, 8, .check_relay = false},  /* LKAS + LTA + PCM cancel cmd */  \
  {0x750, 0, 8, .check_relay = false},  /* literal stationary body commands, restricted below */ \

#define TOYOTA_COMMON_TX_MSGS \
  TOYOTA_BASE_TX_MSGS \
  {0x2E4, 0, 5, .check_relay = true}, \
  {0x343, 0, 8, .check_relay = false},  /* ACC cancel cmd */  \

#define TOYOTA_COMMON_SECOC_TX_MSGS \
  TOYOTA_BASE_TX_MSGS \
  {0x2E4, 0, 8, .check_relay = true}, {0x131, 0, 8, .check_relay = true}, \
  {0x343, 0, 8, .check_relay = false},  /* ACC cancel cmd */ \

#define TOYOTA_COMMON_LONG_TX_MSGS \
  TOYOTA_COMMON_TX_MSGS \
  /* DSU bus 0 */ \
  {0x283, 0, 7, .check_relay = false}, {0x2E6, 0, 8, .check_relay = false}, {0x2E7, 0, 8, .check_relay = false}, {0x33E, 0, 7, .check_relay = false}, \
  {0x344, 0, 8, .check_relay = false}, {0x365, 0, 7, .check_relay = false}, {0x366, 0, 7, .check_relay = false}, {0x4CB, 0, 8, .check_relay = false}, \
  /* DSU bus 1 */ \
  {0x128, 1, 6, .check_relay = false}, {0x141, 1, 4, .check_relay = false}, {0x160, 1, 8, .check_relay = false}, {0x161, 1, 7, .check_relay = false}, \
  {0x470, 1, 4, .check_relay = false}, \
  /* PCS_HUD */                        \
  {0x411, 0, 8, .check_relay = false}, \
  /* ACC */                            \
  {0x343, 0, 8, .check_relay = true},  \

#define TOYOTA_FILTER_LONG_TX_MSGS \
  TOYOTA_COMMON_TX_MSGS \
  /* DSU bus 0 */ \
  {0x283, 0, 7, .check_relay = false}, {0x2E6, 0, 8, .check_relay = false}, {0x2E7, 0, 8, .check_relay = false}, {0x33E, 0, 7, .check_relay = false}, \
  {0x344, 0, 8, .check_relay = false}, {0x365, 0, 7, .check_relay = false}, {0x366, 0, 7, .check_relay = false}, {0x4CB, 0, 8, .check_relay = false}, \
  /* DSU bus 1 */ \
  {0x128, 1, 6, .check_relay = false}, {0x141, 1, 4, .check_relay = false}, {0x160, 1, 8, .check_relay = false}, {0x161, 1, 7, .check_relay = false}, \
  {0x470, 1, 4, .check_relay = false}, \
  /* PCS_HUD */                        \
  {0x411, 0, 8, .check_relay = false},

#define TOYOTA_COMMON_SECOC_LONG_TX_MSGS \
  TOYOTA_COMMON_SECOC_TX_MSGS \
  {0x343, 0, 8, .check_relay = true}, \
  {0x183, 0, 8, .check_relay = true},  /* ACC_CONTROL_2 */ \

#define TOYOTA_COMMON_RX_CHECKS(lta)                                                                                                       \
  {.msg = {{ 0xaa, 0, 8, 83U, .ignore_checksum = true, .ignore_counter = true}, { 0 }, { 0 }}},      \
  {.msg = {{0x260, 0, 8, 50U, .ignore_counter = true, .ignore_quality_flag=!(lta)}, { 0 }, { 0 }}},  \

#define TOYOTA_RX_CHECKS(lta)                                                                                                               \
  TOYOTA_COMMON_RX_CHECKS(lta)                                                                                                              \
  {.msg = {{0x1D2, 0, 8, 33U, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},                            \
  {.msg = {{0x226, 0, 8, 40U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},  { 0 }, { 0 }}},  \

#define TOYOTA_SECOC_RX_CHECKS                                                                                                             \
  TOYOTA_COMMON_RX_CHECKS(false)                                                                                                           \
  {.msg = {{0x176, 0, 8, 32U, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},                           \
  {.msg = {{0x116, 0, 8, 42U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{0x101, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \

static bool toyota_secoc = false;
static bool toyota_alt_brake = false;
static bool toyota_stock_longitudinal = false;
static bool toyota_lta = false;
static bool toyota_radar_diagnostics_allowed = false;
static int toyota_dbc_eps_torque_factor = 100;   // conversion factor for STEER_TORQUE_EPS in %: see dbc file

static bool toyota_hold_main_on = false;
static bool toyota_hold_host_seen = false;
static uint32_t toyota_hold_host_ts = 0U;
static bool toyota_hold_config_valid = false;
static bool toyota_hold_camera_seen = false;
static uint32_t toyota_hold_camera_ts = 0U;
static uint8_t toyota_hold_camera_data[7] = {0};
static uint32_t toyota_hold_main_ts = 0U;
static uint32_t toyota_hold_speed_ts = 0U;
static uint32_t toyota_hold_gas_ts = 0U;
static bool toyota_hold_main_seen = false;
static bool toyota_hold_speed_seen = false;
static bool toyota_hold_gas_seen = false;

static bool toyota_rx_ready(uint32_t now) {
  bool rx_valid = true;
  for (int i = 0; i < current_safety_config.rx_checks_len; i++) {
    const RxStatus *status = &rx_status[i];
    rx_valid &= status->msg_seen && status->valid_checksum && status->valid_quality_flag &&
                (status->wrong_counters < MAX_WRONG_COUNTERS) && !status->lagging &&
                (safety_get_ts_elapsed(now, status->last_timestamp) <= 1000000U);
  }
  return rx_valid;
}

static bool toyota_legacy_main_current(void) {
  bool ready = true;
  if (toyota_legacy_sdsu && toyota_alt_cruise) {
    ready = false;
    if ((current_safety_config.rx_checks_len == 6) ||
        (toyota_pedal_composite && (current_safety_config.rx_checks_len > 6))) {
      const RxStatus *main_status = &rx_status[5];
      ready = main_status->msg_seen && main_status->valid_checksum && main_status->valid_quality_flag &&
        !main_status->lagging && (main_status->wrong_counters < MAX_WRONG_COUNTERS) && acc_main_on &&
        (safety_get_ts_elapsed(microsecond_timer_get(), main_status->last_timestamp) <= 303031U);
    }
  }
  return ready;
}

static bool toyota_legacy_advert_ready(void) {
  bool ready = true;
  if (toyota_dsu_bypass || toyota_pedal_composite || toyota_legacy_advert() || toyota_camera_2aa() || toyota_prius_filter()) {
    static const uint32_t timeout[4] = {303031U, 10000000U, 3333334U, 400000U};
    const uint32_t now = microsecond_timer_get();
    ready = aol_rx_healthy() && toyota_aol_main && toyota_aol_drive && toyota_aol_doors_closed && toyota_aol_eps &&
            (!toyota_camera_2aa() || !toyota_camera_2aa_ambiguous) &&
            (!toyota_prius_filter() || !toyota_prius_sdsu_ambiguous) && (!toyota_dsu_bypass || !toyota_bypass_ambiguous);
    for (unsigned int i = 0U; i < 4U; i++) {
      ready &= toyota_aol_seen[i] && (safety_get_ts_elapsed(now, toyota_aol_ts[i]) <= timeout[i]);
    }
  }
  return ready;
}

static bool toyota_hold_ready(int permission) {
  const int hold_mask = ALT_EXP_TOYOTA_AUTO_HOLD | ALT_EXP_TOYOTA_AEB_HOLD;
  const uint32_t now = microsecond_timer_get();
  return toyota_rx_ready(now) && toyota_legacy_main_current() && toyota_legacy_advert_ready() &&
         (!(toyota_sdsu_status || toyota_pedal) || aol_rx_healthy()) && (!toyota_sdsu_status || toyota_sdsu_control) &&
         (!toyota_dsu_bypass || (toyota_bypass_control && !toyota_bypass_aeb)) &&
         ((alternative_experience & hold_mask) == permission) && !toyota_stock_longitudinal && !toyota_secoc &&
         toyota_hold_config_valid && !safety_rx_checks_invalid && !relay_malfunction && !vehicle_moving && !gas_pressed && toyota_hold_main_on &&
         toyota_hold_main_seen && toyota_hold_speed_seen && toyota_hold_gas_seen &&
         (safety_get_ts_elapsed(now, toyota_hold_main_ts) <= 1000000U) &&
         (safety_get_ts_elapsed(now, toyota_hold_speed_ts) <= 1000000U) &&
         (safety_get_ts_elapsed(now, toyota_hold_gas_ts) <= 1000000U);
}

static uint8_t toyota_pedal_checksum(const CANPacket_t *msg) {
  uint8_t crc = 0xFFU;
  for (int i = 4; i >= 0; i--) {
    crc ^= msg->data[i];
    for (unsigned int bit = 0U; bit < 8U; bit++) {
      crc = ((crc & 0x80U) != 0U) ? (uint8_t)((crc << 1U) ^ 0xD5U) : (uint8_t)(crc << 1U);
    }
  }
  return crc;
}

static uint8_t toyota_get_counter(const CANPacket_t *msg) {
  return (msg->addr == 0x201U) ? (msg->data[4] & 0xFU) : 0U;
}

static uint32_t toyota_compute_checksum(const CANPacket_t *msg) {
  int len = GET_LEN(msg);
  uint8_t checksum = (uint8_t)(msg->addr) + (uint8_t)((unsigned int)(msg->addr) >> 8U) + (uint8_t)(len);
  for (int i = 0; i < (len - 1); i++) {
    checksum += (uint8_t)msg->data[i];
  }
  if (toyota_pedal && (msg->addr == 0x201U)) {
    checksum = toyota_pedal_checksum(msg);
  }
  return checksum;
}

static uint32_t toyota_get_checksum(const CANPacket_t *msg) {
  int checksum_byte = GET_LEN(msg) - 1U;
  return (uint8_t)(msg->data[checksum_byte]);
}

static bool toyota_get_quality_flag_valid(const CANPacket_t *msg) {
  bool valid = false;
  if (toyota_pedal && (msg->addr == 0x201U)) {
    valid = (GET_LEN(msg) == 6U) && ((msg->data[4] >> 4U) == 0U);
  }
  if (msg->addr == 0x260U) {
    valid = !GET_BIT(msg, 3U);  // STEER_TORQUE_SENSOR.STEER_ANGLE_INITIALIZING
  }
  if (msg->addr == 0xaaU) {  // WHEEL_SPEEDS
    // each wheel speed is 1-bit fault + 15-bit speed
    valid = true;
    for (uint8_t i = 0U; i < 4U; i += 1U) {
      if (GET_BIT(msg, (i * 16U) + 7U)) {  // WHEEL_SPEED_xx_FAULT
        valid = false;
        break;
      }
    }
  } else {
  }
  if (toyota_sdsu_status && (msg->addr == 0x2FFU)) {
    const unsigned int state = msg->data[2] >> 4U;
    valid = (GET_LEN(msg) == 8U) && ((state == 0U) || (state == 7U));
  }
  return valid;
}

static void toyota_hold_observe(const CANPacket_t *msg) {
  if ((msg->bus == 2U) && (msg->addr == 0x344U) && (GET_LEN(msg) == 8U) &&
      (toyota_get_checksum(msg) == toyota_compute_checksum(msg))) {
    toyota_hold_camera_seen = true;
    toyota_hold_camera_ts = microsecond_timer_get();
    for (uint8_t i = 0U; i < 7U; i++) {
      toyota_hold_camera_data[i] = msg->data[i];
    }
  }
  if (msg->bus == 0U) {
    if ((msg->addr == 0x1D3U) && (GET_LEN(msg) == 8U)) {
      if (toyota_get_checksum(msg) != toyota_compute_checksum(msg)) {
        toyota_hold_main_seen = false;
      } else {
        toyota_hold_main_on = GET_BIT(msg, 15U);
        toyota_hold_main_seen = true;
        toyota_hold_main_ts = microsecond_timer_get();
      }
    }
  }
}

static void toyota_optional_rx_hook(const CANPacket_t *msg) {
  // Only bus0 pedal RX belongs to the required catalog. A CRC-valid camera
  // copy must neither invalidate its parent nor refresh the primary lease.
  const bool camera_copy = toyota_pedal && msg_matches(msg, 0x201U, 2U) && (GET_LEN(msg) == 6U) &&
    (msg->data[5] == toyota_pedal_checksum(msg));
  if (!camera_copy) {
    if (toyota_dsu_bypass) {
      if (msg_matches(msg, 0x343U, 2U)) {
        // A malformed packet outside the required catalog cannot retain LONG.
        toyota_bypass_control = false;
      }
      if ((msg->addr == 0x201U) || (msg->addr == 0x2FFU) || (msg->addr == 0x2AAU)) {
        toyota_bypass_ambiguous = true;
      }
      if (msg_matches(msg, 0x283U, 2U)) {
        const bool valid = (GET_LEN(msg) == 7U) && (toyota_get_checksum(msg) == toyota_compute_checksum(msg));
        toyota_bypass_aeb = !valid || (GET_BIT(msg, 41U) && GET_BIT(msg, 23U));
      }
    }
    if (toyota_prius_advert() && ((msg->addr == 0x201U) || (msg->addr == 0x2AAU) ||
        ((msg->addr == 0x2FFU) && (((msg->bus != 0U) && (msg->bus != 2U)) || (GET_LEN(msg) != 4U))))) {
      toyota_prius_sdsu_ambiguous = true;
    }
    if (toyota_prius_sdsu() && ((msg->addr == 0x201U) || (msg->addr == 0x2AAU) ||
        ((msg->addr == 0x2FFU) && (((msg->bus != 0U) && (msg->bus != 2U)) || (GET_LEN(msg) == 4U))))) {
      toyota_prius_sdsu_ambiguous = true;
    }
    if (toyota_camera_2aa() && ((msg->addr == 0x2FFU) || (msg->addr == 0x201U) ||
        ((msg->addr == 0x2AAU) && (((msg->bus != 0U) && (msg->bus != 2U)) || (GET_LEN(msg) != 8U))))) {
      // A discriminator cannot authorize a contradictory status/pedal receiver.
      // Keep the refusal until a new safety configuration, including with AOL off.
      toyota_camera_2aa_ambiguous = true;
    }
    const bool aol_checksum_source = (!toyota_alt_cruise && (msg->addr == 0x1D3U)) || (msg->addr == 0x262U) || (toyota_aol_secoc && (msg->addr == 0x127U));
    const unsigned int aol_source_len = ((toyota_factory_eps5 || (toyota_dsu_bypass && toyota_bypass_eps5) || toyota_aol_rav4h_filter || toyota_alt_cruise || toyota_legacy_sdsu || toyota_legacy_advert() || (current_safety_param == 856U)) && (msg->addr == 0x262U)) ? 5U : 8U;
    if (!aol_checksum_source || ((GET_LEN(msg) == aol_source_len) && (toyota_get_checksum(msg) == toyota_compute_checksum(msg)))) {
      toyota_aol_observe(msg);
    } else if ((toyota_dsu_bypass || toyota_pedal_composite || toyota_aol_enabled || toyota_legacy_advert() || toyota_camera_2aa() || toyota_prius_filter()) && (msg->bus == 0U)) {
      unsigned int index = 3U;
      if (msg->addr == 0x1D3U) {
        index = 0U;
      } else if (msg->addr == 0x127U) {
        index = 1U;
      } else {
      }
      toyota_aol_seen[index] = false;
    } else {
    }
    toyota_hold_observe(msg);
  }
}

static void toyota_rx_hook(const CANPacket_t *msg) {
  toyota_hold_observe(msg);
  if (toyota_dsu_bypass && msg_matches(msg, 0x343U, 2U)) {
    toyota_bypass_control = !GET_BIT(msg, 18U) && !GET_BIT(msg, 19U);
  }
  // Required sources have already passed RX length/checksum validation here.
  // Optional ingress only runs for messages outside the required RX catalog.
  if ((toyota_factory_stock || toyota_dsu_bypass || toyota_pedal_composite || toyota_legacy_advert() || toyota_camera_2aa() || toyota_prius_filter() || (current_safety_param == 856U)) && ((msg->addr == 0x262U) || (!toyota_alt_cruise && (msg->addr == 0x1D3U)))) {
    toyota_aol_observe(msg);
  }
  if (toyota_alt_cruise && msg_matches(msg, 0x365U, 0U) && (GET_LEN(msg) == 7U)) {
    acc_main_on = GET_BIT(msg, 0U);
    toyota_aol_observe(msg);
  }
  if (msg->bus == 0U) {
    if (toyota_pedal && (msg->addr == 0x201U) && (GET_LEN(msg) == 6U)) {
      const unsigned int gas = (((unsigned int)msg->data[0] << 8U) + msg->data[1] +
                                ((unsigned int)msg->data[2] << 8U) + msg->data[3]) / 2U;
      if (toyota_pedal_composite) {
        toyota_composite_pedal_gas = gas > 805U;
        gas_pressed = toyota_composite_pcm_gas || toyota_composite_pedal_gas;
      } else {
        gas_pressed = gas > 805U;
      }
    }
    if (toyota_sdsu_status && (msg->addr == 0x2FFU) && (GET_LEN(msg) == 8U)) {
      // Only normal sDSU state owns replacement LONG. AEB stays healthy and retains independent LAT.
      toyota_sdsu_control = (msg->data[2] >> 4U) == 0U;
    }
    if ((msg_matches(msg, 0xaaU, 0U)) && (GET_LEN(msg) == 8U)) {
      toyota_hold_speed_seen = true;
      toyota_hold_speed_ts = microsecond_timer_get();
    }
    if ((((msg_matches(msg, 0x1D2U, 0U)) && !toyota_secoc) ||
         ((msg_matches(msg, 0x116U, 0U)) && toyota_secoc)) && (GET_LEN(msg) == 8U)) {
      toyota_hold_gas_seen = true;
      toyota_hold_gas_ts = microsecond_timer_get();
    }

    // get eps motor torque (0.66 factor in dbc)
    if (msg_matches(msg, 0x260U, 0U)) {
      int torque_meas_new = (msg->data[5] << 8) | msg->data[6];
      torque_meas_new = to_signed(torque_meas_new, 16);

      // scale by dbc_factor
      torque_meas_new = (torque_meas_new * toyota_dbc_eps_torque_factor) / 100;

      // update array of sample
      update_sample(&torque_meas, torque_meas_new);

      // increase torque_meas by 1 to be conservative on rounding
      torque_meas.min--;
      torque_meas.max++;

      // driver torque for angle limiting
      int torque_driver_new = (msg->data[1] << 8) | msg->data[2];
      torque_driver_new = to_signed(torque_driver_new, 16);
      update_sample(&torque_driver, torque_driver_new);

      // LTA request angle should match current angle while inactive, clipped to max accepted angle.
      // note that angle can be relative to init angle on some TSS2 platforms, LTA has the same offset
      bool steer_angle_initializing = GET_BIT(msg, 3U);
      if (!steer_angle_initializing) {
        int angle_meas_new = (msg->data[3] << 8U) | msg->data[4];
        angle_meas_new = to_signed(angle_meas_new, 16);
        update_sample(&angle_meas, angle_meas_new);
      }
    }

    // enter controls on rising edge of ACC, exit controls on ACC off
    // exit controls on rising edge of gas press, if not alternative experience
    // exit controls on rising edge of brake press
    if (toyota_secoc) {
      if (msg_matches(msg, 0x176U, 0U)) {
        bool cruise_engaged = GET_BIT(msg, 5U);  // PCM_CRUISE.CRUISE_ACTIVE
        pcm_cruise_check(cruise_engaged);
      }
      if (msg_matches(msg, 0x116U, 0U)) {
        gas_pressed = msg->data[1] != 0U;  // GAS_PEDAL.GAS_PEDAL_USER
      }
      if (msg_matches(msg, 0x101U, 0U)) {
        brake_pressed = GET_BIT(msg, 3U);  // BRAKE_MODULE.BRAKE_PRESSED (toyota_rav4_prime_generated.dbc)
      }
    } else {
      if (msg_matches(msg, 0x1D2U, 0U)) {
        bool cruise_engaged = GET_BIT(msg, 5U);  // PCM_CRUISE.CRUISE_ACTIVE
        pcm_cruise_check(cruise_engaged);
        if (toyota_pedal_composite) {
          toyota_composite_pcm_gas = !GET_BIT(msg, 4U);
          gas_pressed = toyota_composite_pcm_gas || toyota_composite_pedal_gas;
        } else if (!toyota_pedal) { gas_pressed = !GET_BIT(msg, 4U); }  // PCM_CRUISE.GAS_RELEASED
        else { /* Standalone pedal retains its independently observed gas state. */ }
      }
      if (!toyota_alt_brake && (msg_matches(msg, 0x226U, 0U))) {
        brake_pressed = GET_BIT(msg, 37U);  // BRAKE_MODULE.BRAKE_PRESSED (toyota_nodsu_pt_generated.dbc)
      }
      if (toyota_alt_brake && (msg_matches(msg, 0x224U, 0U))) {
        brake_pressed = GET_BIT(msg, 5U);  // BRAKE_MODULE.BRAKE_PRESSED (toyota_new_mc_pt_generated.dbc)
      }
    }

    // sample speed
    if (msg_matches(msg, 0xaaU, 0U)) {
      int speed = 0;
      // sum 4 wheel speeds. conversion: raw * 0.01 - 67.67
      for (uint8_t i = 0U; i < 8U; i += 2U) {
        int wheel_speed = ((msg->data[i] & 0x7FU) << 8U) | msg->data[(i + 1U)];
        speed += wheel_speed - 6767;
      }
      // check that all wheel speeds are at zero value
      vehicle_moving = speed != 0;

      UPDATE_VEHICLE_SPEED(speed / 4.0 * 0.01 * KPH_TO_MS);
    }
  }
}

static bool toyota_tx_hook(const CANPacket_t *msg) {
  const TorqueSteeringLimits TOYOTA_TORQUE_STEERING_LIMITS = {
    .max_torque = 1500,
    .max_rate_up = 15,          // ramp up slow
    .max_rate_down = 25,        // ramp down fast
    .max_torque_error = 350,    // max torque cmd in excess of motor torque
    .max_rt_delta = 450,        // the real time limit is 1800/sec, a 20% buffer
    .type = TorqueMotorLimited,

    // the EPS faults when the steering angle rate is above a certain threshold for too long. to prevent this,
    // we allow setting STEER_REQUEST bit to 0 while maintaining the requested torque value for a single frame
    .min_valid_request_frames = 17,
    .max_invalid_request_frames = 1,
    .min_valid_request_rt_interval = 162000,  // 162ms; a ~10% buffer on cutting every 18 frames
    .has_steer_req_tolerance = true,
  };

  static const AngleSteeringLimits TOYOTA_ANGLE_STEERING_LIMITS = {
    // LTA angle limits
    // factor for STEER_TORQUE_SENSOR->STEER_ANGLE and STEERING_LTA->STEER_ANGLE_CMD (1 / 0.0573)
    .max_angle = 1657,  // EPS only accepts up to 94.9461
    .angle_deg_to_can = 17.452007,
    .angle_rate_up_lookup = {
      {5., 25., 25.},
      {0.3, 0.15, 0.15}
    },
    .angle_rate_down_lookup = {
      {5., 25., 25.},
      {0.36, 0.26, 0.26}
    },
  };

  const int TOYOTA_LTA_MAX_MEAS_TORQUE = 1500;
  const int TOYOTA_LTA_MAX_DRIVER_TORQUE = 150;

  // longitudinal limits
  const LongitudinalLimits TOYOTA_LONG_LIMITS = {
    .max_accel = 3500,   // 3.5 m/s2
    .min_accel = -3500,  // -3.5 m/s2
  };

  const bool filter_ready = (!(toyota_sdsu_status || toyota_pedal) || aol_rx_healthy()) && toyota_legacy_main_current() && toyota_legacy_advert_ready();
  const bool filter_long_ready = filter_ready && (!toyota_sdsu_status || toyota_sdsu_control) &&
                                 (!toyota_dsu_bypass || (toyota_bypass_control && !toyota_bypass_aeb));

  bool tx = true;
  if (toyota_pedal && (msg->addr == 0x200U)) {
    const bool neutral = (msg->data[0] == 0U) && (msg->data[1] == 0U) &&
                         (msg->data[2] == 0U) && (msg->data[3] == 0U) && !GET_BIT(msg, 39U);
    // Match Dom's bounded0.5 maximum and the receiver's two distinct raw scales.
    const unsigned int gas1 = ((unsigned int)msg->data[0] << 8U) | msg->data[1];
    const unsigned int gas2 = ((unsigned int)msg->data[2] << 8U) | msg->data[3];
    tx = (GET_LEN(msg) == 6U) && (msg->data[5] == toyota_pedal_checksum(msg)) &&
         ((msg->data[4] & 0x70U) == 0U) && (neutral ||
          (!toyota_stock_longitudinal && aol_rx_healthy() && longitudinal_controls_allowed() &&
           (!toyota_pedal_composite || filter_long_ready) &&
           !gas_pressed && !brake_pressed && GET_BIT(msg, 39U) && (gas1 >= 475U) && (gas1 <= 1275U) && (gas2 >= 949U) && (gas2 <= 1749U) &&
           (gas2 >= (gas1 + 473U)) && (gas2 <= (gas1 + 475U))));
  }


  // Check if msg is sent on BUS 0
  if (msg->bus == 0U) {
    // ACCEL: safety check on byte 1-2
    if (msg_matches(msg, 0x343U, 0U)) {
      int desired_accel = (msg->data[0] << 8) | msg->data[1];
      desired_accel = to_signed(desired_accel, 16);

      bool violation = !filter_long_ready && ((desired_accel != 0) || (!GET_BIT(msg, 24U) && GET_BIT(msg, 31U)));
      if (toyota_secoc) {
        // SecOC cars move accel to 0x183. Only allow inactive accel on 0x343 to match stock behavior
        violation = desired_accel != TOYOTA_LONG_LIMITS.inactive_accel;
      }
      const bool auto_hold = toyota_hold_ready(ALT_EXP_TOYOTA_AUTO_HOLD) && (desired_accel == -1000) &&
                             GET_BIT(msg, 30U) && !GET_BIT(msg, 31U) && !GET_BIT(msg, 24U);
      violation |= !auto_hold && longitudinal_accel_checks(desired_accel, TOYOTA_LONG_LIMITS);

      // only ACC messages that cancel are allowed when openpilot is not controlling longitudinal
      if (toyota_stock_longitudinal) {
        bool cancel_req = GET_BIT(msg, 24U);
        if (!cancel_req) {
          violation = true;
        }
        if (desired_accel != TOYOTA_LONG_LIMITS.inactive_accel) {
          violation = true;
        }
      }

      if (violation) {
        tx = false;
      }
    }

    if (msg_matches(msg, 0x183U, 0U)) {
      int desired_accel = (msg->data[0] << 8) | msg->data[1];
      desired_accel = to_signed(desired_accel, 16);

      tx = !longitudinal_accel_checks(desired_accel, TOYOTA_LONG_LIMITS);
    }

    // AEB: block all actuation. only used when DSU is unplugged
    if (msg_matches(msg, 0x283U, 0U)) {
      // only allow the checksum, which is the last byte
      bool block = GET_BYTES_64_LE(msg, 0, 6) != 0U;
      if (block) {
        tx = false;
      }
    }

    // STEERING_LTA angle steering check
    if (msg_matches(msg, 0x191U, 0U)) {
      // check the STEER_REQUEST, STEER_REQUEST_2, TORQUE_WIND_DOWN, STEER_ANGLE_CMD signals
      bool lta_request = GET_BIT(msg, 0U);
      bool lta_request2 = GET_BIT(msg, 25U);
      int torque_wind_down = msg->data[5];
      int lta_angle = (msg->data[1] << 8) | msg->data[2];
      lta_angle = to_signed(lta_angle, 16);

      bool steer_control_enabled = lta_request || lta_request2;
      if (!toyota_lta) {
        // using torque (LKA), block LTA msgs with actuation requests
        if (steer_control_enabled || (lta_angle != 0) || (torque_wind_down != 0)) {
          tx = false;
        }
      } else {
        // check angle rate limits and inactive angle
        if (steer_angle_cmd_checks(lta_angle, steer_control_enabled, TOYOTA_ANGLE_STEERING_LIMITS)) {
          tx = false;
        }

        if (lta_request != lta_request2) {
          tx = false;
        }

        // TORQUE_WIND_DOWN is gated on steer request
        if (!steer_control_enabled && (torque_wind_down != 0)) {
          tx = false;
        }

        // TORQUE_WIND_DOWN can only be no or full torque
        if ((torque_wind_down != 0) && (torque_wind_down != 100)) {
          tx = false;
        }

        // check if we should wind down torque
        int driver_torque = SAFETY_MIN(SAFETY_ABS(torque_driver.min), SAFETY_ABS(torque_driver.max));
        if ((driver_torque > TOYOTA_LTA_MAX_DRIVER_TORQUE) && (torque_wind_down != 0)) {
          tx = false;
        }

        int eps_torque = SAFETY_MIN(SAFETY_ABS(torque_meas.min), SAFETY_ABS(torque_meas.max));
        if ((eps_torque > TOYOTA_LTA_MAX_MEAS_TORQUE) && (torque_wind_down != 0)) {
          tx = false;
        }
      }
    }

    // STEERING_LTA_2 angle steering check (SecOC)
    if (toyota_secoc && (msg_matches(msg, 0x131U, 0U))) {
      // SecOC cars block any form of LTA actuation for now
      bool lta_request = GET_BIT(msg, 3U);  // STEERING_LTA_2.STEER_REQUEST
      bool lta_request2 = GET_BIT(msg, 0U);  // STEERING_LTA_2.STEER_REQUEST_2
      int lta_angle_msb = msg->data[2];  // STEERING_LTA_2.STEER_ANGLE_CMD (MSB)
      int lta_angle_lsb = msg->data[3];  // STEERING_LTA_2.STEER_ANGLE_CMD (LSB)

      bool actuation = lta_request || lta_request2 || (lta_angle_msb != 0) || (lta_angle_lsb != 0);
      if (actuation) {
        tx = false;
      }
    }

    // STEER: safety check on bytes 2-3
    if (msg_matches(msg, 0x2E4U, 0U)) {
      int desired_torque = (msg->data[1] << 8) | msg->data[2];
      desired_torque = to_signed(desired_torque, 16);
      bool steer_req = GET_BIT(msg, 0U);
      if (!filter_ready && ((desired_torque != 0) || steer_req)) {
        tx = false;
      }
      // When using LTA (angle control), assert no actuation on LKA message
      if (!toyota_lta) {
        if (steer_torque_cmd_checks(desired_torque, steer_req, TOYOTA_TORQUE_STEERING_LIMITS)) {
          tx = false;
        }
      } else {
        if ((desired_torque != 0) || steer_req) {
          tx = false;
        }
      }
    }
    if ((msg_matches(msg, 0x344U, 0U)) && ((alternative_experience & ALT_EXP_TOYOTA_AEB_HOLD) != 0)) {
      const bool exact_hold = (msg->data[0] == 0xFDU) && (msg->data[1] == 0x80U) &&
                              (msg->data[2] == 0U) && (msg->data[3] == 0U) && (msg->data[4] <= 1U) &&
                              (msg->data[5] == 0U) && (msg->data[6] == 0U);
      bool stock_camera = toyota_hold_camera_seen &&
                          (safety_get_ts_elapsed(microsecond_timer_get(), toyota_hold_camera_ts) <= 1000000U);
      for (uint8_t i = 0U; i < 7U; i++) {
        stock_camera &= msg->data[i] == toyota_hold_camera_data[i];
      }
      tx &= toyota_hold_ready(ALT_EXP_TOYOTA_AEB_HOLD) && (exact_hold || stock_camera) &&
            (toyota_get_checksum(msg) == toyota_compute_checksum(msg));
      if (tx) {
        toyota_hold_host_seen = true;
        toyota_hold_host_ts = microsecond_timer_get();
      }
    }
  }

  if (msg->addr == 0x750U) {
    uint64_t payload = GET_BYTES_64_LE(msg, 0, 8);
    bool body_command = (payload == 0x0000800011300540ULL) || (payload == 0x0000400011300540ULL);
    if (body_command) {
      // Never admit arbitrary UDS, an unobserved/stale stop, or a moving car.
      const uint32_t now = microsecond_timer_get();
      tx &= toyota_rx_ready(now) && toyota_hold_speed_seen && toyota_hold_gas_seen &&
            !vehicle_moving && !gas_pressed && !safety_rx_checks_invalid && !relay_malfunction &&
            (safety_get_ts_elapsed(now, toyota_hold_speed_ts) <= 100000U) &&
            (safety_get_ts_elapsed(now, toyota_hold_gas_ts) <= 100000U);
    } else {
      // Preserve the original radar-only tester-present allowance and configuration scope.
      tx &= toyota_radar_diagnostics_allowed && (payload == 0x00000000003E020FULL);
    }
  }

  // Required physical DSU MAIN cannot be manufactured through host TX.
  if ((toyota_dsu_bypass || toyota_legacy_sdsu || toyota_legacy_advert()) && toyota_alt_cruise && msg_matches(msg, 0x365U, 0U)) {
    tx = false;
  }
  return tx;
}

static safety_config toyota_init(uint16_t param) {
  const uint16_t parent_param = toyota_pedal_parent_param(param);
  toyota_factory_stock = toyota_factory_stock_param(parent_param);
  toyota_factory_eps5 = toyota_factory_stock && (parent_param != 0x6242U) && (parent_param != 0x6249U);
  toyota_factory_alt_main = (parent_param == 0x564DU) || (parent_param == 0x5749U);
  const uint16_t base_param = toyota_factory_stock_base(toyota_bypass_base_param(parent_param));
  toyota_dsu_bypass = toyota_bypass_param(parent_param);
  toyota_bypass_eps5 = toyota_dsu_bypass && ((parent_param & 0x1000U) == 0U);
  toyota_bypass_alt_main = toyota_dsu_bypass && ((parent_param & 0x4000U) != 0U);
  toyota_bypass_ambiguous = false;
  toyota_bypass_control = false;
  toyota_bypass_aeb = false;
  toyota_pedal_composite = toyota_pedal_composite_param(param);
  toyota_composite_pcm_gas = false;
  toyota_composite_pedal_gas = false;
  toyota_aol_configure(base_param);
  toyota_pedal = toyota_pedal || toyota_pedal_composite;
  toyota_hold_main_on = false;
  toyota_hold_host_seen = false;
  toyota_hold_host_ts = 0U;
  toyota_hold_config_valid = (((base_param & ~0x5FFU) == 0U) && ((base_param & 0xFFU) != 0U)) || toyota_aol_rav4h_filter || toyota_eps8_sdsu || toyota_legacy_sdsu ||
                             toyota_legacy_advert_param(base_param) || toyota_camera_2aa_param(base_param) ||
                             toyota_prius_filter_param(base_param) || toyota_pedal;
  toyota_hold_camera_seen = false;
  toyota_hold_camera_ts = 0U;
  toyota_hold_main_seen = false;
  toyota_hold_speed_seen = false;
  toyota_hold_gas_seen = false;
  toyota_hold_main_ts = 0U;
  toyota_hold_speed_ts = 0U;
  toyota_hold_gas_ts = 0U;
  static const CanMsg TOYOTA_TX_MSGS[] = {
    TOYOTA_COMMON_TX_MSGS
  };

  static const CanMsg TOYOTA_SECOC_TX_MSGS[] = {
    TOYOTA_COMMON_SECOC_TX_MSGS
  };

  static const CanMsg TOYOTA_LONG_TX_MSGS[] = {
    TOYOTA_COMMON_LONG_TX_MSGS
  };

  static const CanMsg TOYOTA_FILTER_LONG_TX_MSGS_SET[] = {
    TOYOTA_FILTER_LONG_TX_MSGS
  };

  static const CanMsg TOYOTA_PEDAL_LONG_TX_MSGS[] = {
    TOYOTA_COMMON_LONG_TX_MSGS
    {0x200, 0, 6, .check_relay = false},
  };
  static const CanMsg TOYOTA_COMPOSITE_LONG_TX_MSGS[] = {
    TOYOTA_FILTER_LONG_TX_MSGS
    {0x200, 0, 6, .check_relay = false},
  };
  static const CanMsg TOYOTA_PEDAL_STOCK_TX_MSGS[] = {
    TOYOTA_COMMON_TX_MSGS
    {0x200, 0, 6, .check_relay = false},
  };

  static const CanMsg TOYOTA_SECOC_LONG_TX_MSGS[] = {
    TOYOTA_COMMON_SECOC_LONG_TX_MSGS
  };

  // safety base_param flags
  // first byte is for EPS factor, second is for flags
  const uint32_t TOYOTA_PARAM_OFFSET = 8U;
  const uint32_t TOYOTA_EPS_FACTOR = (1UL << TOYOTA_PARAM_OFFSET) - 1U;
  const uint32_t TOYOTA_PARAM_ALT_BRAKE = 1UL << TOYOTA_PARAM_OFFSET;
  const uint32_t TOYOTA_PARAM_STOCK_LONGITUDINAL = 2UL << TOYOTA_PARAM_OFFSET;
  const uint32_t TOYOTA_PARAM_LTA = 4UL << TOYOTA_PARAM_OFFSET;

  const uint32_t TOYOTA_PARAM_SECOC = 8UL << TOYOTA_PARAM_OFFSET;
#ifdef ALLOW_DEBUG
  toyota_secoc = GET_FLAG(base_param, TOYOTA_PARAM_SECOC);
#endif

  toyota_alt_brake = GET_FLAG(base_param, TOYOTA_PARAM_ALT_BRAKE);
  toyota_stock_longitudinal = GET_FLAG(base_param, TOYOTA_PARAM_STOCK_LONGITUDINAL);
  toyota_radar_diagnostics_allowed = !toyota_dsu_bypass && !toyota_stock_longitudinal && (!GET_FLAG(base_param, TOYOTA_PARAM_SECOC) || (toyota_secoc && (base_param == 6217U))) && (base_param != 0x1049U) && (base_param != 0x1449U) && !toyota_eps8_sdsu && !toyota_legacy_sdsu && !toyota_legacy_advert_param(base_param) && !toyota_camera_2aa_param(base_param) && !toyota_prius_filter_param(base_param);
  toyota_lta = !toyota_legacy_advert_param(base_param) && GET_FLAG(base_param, TOYOTA_PARAM_LTA);
  toyota_dbc_eps_torque_factor = base_param & TOYOTA_EPS_FACTOR;

  safety_config ret;
  if (toyota_pedal) {
    if (toyota_stock_longitudinal) {
      SET_TX_MSGS(TOYOTA_PEDAL_STOCK_TX_MSGS, ret);
    } else if (toyota_pedal_composite) {
      SET_TX_MSGS(TOYOTA_COMPOSITE_LONG_TX_MSGS, ret);
    } else {
      SET_TX_MSGS(TOYOTA_PEDAL_LONG_TX_MSGS, ret);
    }
  } else if (toyota_secoc) {
    if (toyota_stock_longitudinal) {
      SET_TX_MSGS(TOYOTA_SECOC_TX_MSGS, ret);
    } else {
      SET_TX_MSGS(TOYOTA_SECOC_LONG_TX_MSGS, ret);
    }
  } else {
    if (toyota_stock_longitudinal) {
      SET_TX_MSGS(TOYOTA_TX_MSGS, ret);
    } else {
      if ((base_param == 0x1049U) || (base_param == 0x9049U) || (base_param == 0x1449U) || toyota_eps8_sdsu || toyota_legacy_sdsu || toyota_legacy_advert_param(base_param) || toyota_camera_2aa_param(base_param) || toyota_prius_filter_param(base_param)) {
        SET_TX_MSGS(TOYOTA_FILTER_LONG_TX_MSGS_SET, ret);
      } else {
        SET_TX_MSGS(TOYOTA_LONG_TX_MSGS, ret);
      }
    }
  }

  if (toyota_secoc) {
    static const RxCheck toyota_secoc_rx_checks[] = {
      TOYOTA_SECOC_RX_CHECKS
    };

    SET_RX_CHECKS(toyota_secoc_rx_checks, ret);
  } else {
    // These profiles use the same four base checks. Share their mutable
    // workspace; set_safety_hooks resets every active status on each switch.
    static RxCheck toyota_rx_checks[] = {
      TOYOTA_RX_CHECKS(false)
      {.msg = {{0x2FF, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = false}, { 0 }, { 0 }}},
      {.msg = {{0x365, 0, 7, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{0x1D3, 0, 8, 10U, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{0x201, 0, 6, 50U, .max_counter = 15U, .ignore_quality_flag = false}, { 0 }, { 0 }}},
    };
    // LTA alone validates initialized measured angle. Preserve the existing
    // alternate-brake selection only on non-LTA profiles.
    toyota_rx_checks[1].msg[0].ignore_quality_flag = !toyota_lta;
    toyota_rx_checks[3].msg[0].addr = (!toyota_lta && toyota_alt_brake) ? 0x224U : 0x226U;
    const bool standalone_pedal = toyota_pedal && !toyota_pedal_composite;
    toyota_rx_checks[4].msg[0] = (CanMsgCheck){.addr = toyota_alt_cruise ? 0x365 : (standalone_pedal ? 0x201 : 0x2FF),
      .bus = 0U, .len = toyota_alt_cruise ? 7 : (standalone_pedal ? 6 : 8), .frequency = toyota_alt_cruise ? 10U : 50U,
      .ignore_checksum = !standalone_pedal, .ignore_counter = !standalone_pedal,
      .max_counter = standalone_pedal ? 15U : 0U, .ignore_quality_flag = toyota_alt_cruise};
    if (toyota_legacy_sdsu) {
      // Alternate DSU MAIN and smartDSU state are independent mandatory sources.
      toyota_rx_checks[4].msg[0] = (CanMsgCheck){.addr = 0x2FF, .bus = 0U, .len = 8, .frequency = 50U,
        .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = false};
    }
    // Restore the sixth row on every warm switch before specializing the new owner.
    toyota_rx_checks[5].msg[0] = (CanMsgCheck){.addr = 0x365, .bus = 0U, .len = 7, .frequency = 10U,
      .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true};
    if (toyota_legacy_advert_param(base_param) || toyota_camera_2aa_param(base_param) || toyota_prius_advert_param(base_param) || (base_param == 856U)) {
      toyota_rx_checks[4].msg[0] = (CanMsgCheck){.addr = 0x262, .bus = 0U, .len = (toyota_camera_2aa_param(base_param) || toyota_prius_advert_param(base_param)) ? 8 : 5, .frequency = 25U,
        .ignore_counter = true, .ignore_quality_flag = true};
      if (!toyota_alt_cruise) {
        toyota_rx_checks[5].msg[0] = (CanMsgCheck){.addr = 0x1D3, .bus = 0U, .len = 8, .frequency = 10U,
          .ignore_counter = true, .ignore_quality_flag = true};
      }
    }
    if (toyota_prius_sdsu_param(base_param)) {
      toyota_rx_checks[5].msg[0] = (CanMsgCheck){.addr = 0x262, .bus = 0U, .len = 8, .frequency = 25U,
        .ignore_counter = true, .ignore_quality_flag = true};
      toyota_rx_checks[6].msg[0] = (CanMsgCheck){.addr = 0x1D3, .bus = 0U, .len = 8, .frequency = 10U,
        .ignore_counter = true, .ignore_quality_flag = true};
    }
    if (toyota_factory_stock) {
      toyota_rx_checks[4].msg[0] = (CanMsgCheck){.addr = 0x262, .bus = 0U, .len = toyota_factory_eps5 ? 5 : 8,
        .frequency = 25U, .ignore_counter = true, .ignore_quality_flag = true};
      toyota_rx_checks[5].msg[0] = (CanMsgCheck){.addr = toyota_factory_alt_main ? 0x365 : 0x1D3, .bus = 0U,
        .len = toyota_factory_alt_main ? 7 : 8, .frequency = 10U, .ignore_checksum = toyota_factory_alt_main,
        .ignore_counter = true, .ignore_quality_flag = true};
    }
    ret.rx_checks = toyota_rx_checks;
    ret.disable_forwarding = false;
    if (toyota_factory_stock) {
      ret.rx_checks_len = 6;
    } else if (toyota_prius_sdsu_param(base_param)) {
      ret.rx_checks_len = 7;
    } else if ((toyota_legacy_sdsu && toyota_alt_cruise) || toyota_legacy_advert_param(base_param) || toyota_camera_2aa_param(base_param) || toyota_prius_advert_param(base_param) || (base_param == 856U)) {
      ret.rx_checks_len = 6;
    } else if (standalone_pedal || toyota_alt_cruise || toyota_sdsu_status) {
      ret.rx_checks_len = 5;
    } else {
      ret.rx_checks_len = 4;
    }
    if (toyota_dsu_bypass) {
      toyota_rx_checks[4].msg[0] = (CanMsgCheck){.addr = 0x343, .bus = 2U, .len = 8, .frequency = 33U,
        .ignore_counter = true, .ignore_quality_flag = true};
      toyota_rx_checks[5].msg[0] = (CanMsgCheck){.addr = 0x262, .bus = 0U, .len = toyota_bypass_eps5 ? 5 : 8, .frequency = 25U,
        .ignore_counter = true, .ignore_quality_flag = true};
      toyota_rx_checks[6].msg[0] = (CanMsgCheck){.addr = toyota_alt_cruise ? 0x365 : 0x1D3, .bus = 0U,
        .len = toyota_alt_cruise ? 7 : 8, .frequency = 10U, .ignore_checksum = toyota_alt_cruise,
        .ignore_counter = true, .ignore_quality_flag = true};
      ret.rx_checks_len = 7;
    }
    if ((param == 0xB049U) || (param == 0xB249U)) {
      // Keep all parent checks, including smartDSU status, independently required.
      toyota_rx_checks[5].msg[0] = (CanMsgCheck){.addr = 0x201, .bus = 0U, .len = 6, .frequency = 50U,
        .max_counter = 15U, .ignore_quality_flag = false};
      toyota_rx_checks[6].msg[0] = (CanMsgCheck){.addr = 0x262, .bus = 0U, .len = 5, .frequency = 25U,
        .ignore_counter = true, .ignore_quality_flag = true};
      toyota_rx_checks[7].msg[0] = (CanMsgCheck){.addr = 0x1D3, .bus = 0U, .len = 8, .frequency = 10U,
        .ignore_counter = true, .ignore_quality_flag = true};
      ret.rx_checks_len = 8;
    } else if (toyota_pedal_composite) {
      // Append independent physical sources and pedal to the unchanged parent
      // catalog. Eight rows cover the largest parent without shrinking buffers.
      bool eps_required = false;
      bool main_required = false;
      for (int i = 0; i < ret.rx_checks_len; i++) {
        eps_required |= toyota_rx_checks[i].msg[0].addr == 0x262U;
        main_required |= toyota_rx_checks[i].msg[0].addr == (toyota_alt_cruise ? 0x365U : 0x1D3U);
      }
      if (!eps_required) {
        const bool eps5 = (toyota_dsu_bypass && toyota_bypass_eps5) || toyota_aol_rav4h_filter ||
                          toyota_legacy_sdsu || toyota_legacy_advert();
        toyota_rx_checks[ret.rx_checks_len].msg[0] = (CanMsgCheck){.addr = 0x262, .bus = 0U,
          .len = eps5 ? 5 : 8, .frequency = 25U, .ignore_counter = true, .ignore_quality_flag = true};
        ret.rx_checks_len++;
      }
      if (!main_required) {
        toyota_rx_checks[ret.rx_checks_len].msg[0] = (CanMsgCheck){.addr = toyota_alt_cruise ? 0x365 : 0x1D3,
          .bus = 0U, .len = toyota_alt_cruise ? 7 : 8, .frequency = 10U, .ignore_checksum = toyota_alt_cruise,
          .ignore_counter = true, .ignore_quality_flag = true};
        ret.rx_checks_len++;
      }
      toyota_rx_checks[ret.rx_checks_len].msg[0] = (CanMsgCheck){.addr = 0x201, .bus = 0U, .len = 6,
        .frequency = 50U, .max_counter = 15U, .ignore_quality_flag = false};
      ret.rx_checks_len++;
    } else {
    }
  }

  if (((base_param & 0xC000U) != 0U) && !toyota_alt_cruise && !toyota_sdsu_status && !toyota_legacy_advert_param(base_param) && !toyota_camera_2aa_param(base_param) && !toyota_prius_advert_param(base_param)) {
    ret.tx_msgs_len = 0;
  }
  if (((base_param & 0x2000U) != 0U) && !toyota_pedal) {
    ret.tx_msgs_len = 0;
  }
  return ret;
}

static bool toyota_fwd_hook(int bus_num, int addr) {
  return (toyota_dsu_bypass && !toyota_stock_longitudinal && (bus_num == 2) && (addr == 0x343)) ||
         ((bus_num == 2) && (addr == 0x344) && toyota_hold_ready(ALT_EXP_TOYOTA_AEB_HOLD) &&
         toyota_hold_host_seen && (safety_get_ts_elapsed(microsecond_timer_get(), toyota_hold_host_ts) <= 100000U));
}

const safety_hooks toyota_hooks = {
  .init = toyota_init,
  .rx = toyota_rx_hook,
  .optional_rx = toyota_optional_rx_hook,
  .tx = toyota_tx_hook,
  .fwd = toyota_fwd_hook,
  .get_counter = toyota_get_counter,
  .get_checksum = toyota_get_checksum,
  .compute_checksum = toyota_compute_checksum,
  .get_quality_flag_valid = toyota_get_quality_flag_valid,
};
