#pragma once

#include "opendbc/safety/declarations.h"
#include "opendbc/safety/modes/subaru_stock_aol.h"
#include "opendbc/safety/modes/subaru_software_cruise.h"
#include "opendbc/safety/modes/subaru_sng.h"
#include "opendbc/safety/modes/subaru_stop_start.h"
#include "opendbc/safety/modes/subaru_avh.h"
#include "opendbc/safety/modes/subaru_jacobw.h"

#define SUBARU_STEERING_LIMITS_GENERATOR(steer_max, rate_up, rate_down)               \
  {                                                                                   \
    .max_torque = (steer_max),                                                        \
    .max_rt_delta = 940,                                                              \
    .max_rate_up = (rate_up),                                                         \
    .max_rate_down = (rate_down),                                                     \
    .driver_torque_multiplier = 50,                                                   \
    .driver_torque_allowance = 60,                                                    \
    .type = TorqueDriverLimited,                                                      \
    /* the EPS will temporary fault if the steering rate is too high, so we cut the   \
       the steering torque every 7 frames for 1 frame if the steering rate is high */ \
    .min_valid_request_frames = 7,                                                    \
    .max_invalid_request_frames = 1,                                                  \
    .min_valid_request_rt_interval = 144000,  /* 10% tolerance */                     \
    .has_steer_req_tolerance = true,                                                  \
  }

#define MSG_SUBARU_Brake_Status          0x13cU
#define MSG_SUBARU_CruiseControl         0x240U
#define MSG_SUBARU_Throttle              0x40U
#define MSG_SUBARU_Steering_Torque       0x119U
#define MSG_SUBARU_Steering_2            0x11aU
#define MSG_SUBARU_Wheel_Speeds          0x13aU

#define MSG_SUBARU_ES_LKAS               0x122U
#define MSG_SUBARU_ES_LKAS_ANGLE         0x124U
#define MSG_SUBARU_ES_Distance           0x221U
#define MSG_SUBARU_ES_Status             0x222U
#define MSG_SUBARU_ES_DashStatus         0x321U
#define MSG_SUBARU_ES_LKAS_State         0x322U
#define MSG_SUBARU_ES_Infotainment       0x323U
#define MSG_SUBARU_ES_LKAS_Alert         0x3C4U

#define SUBARU_MAIN_BUS 0U
#define SUBARU_ALT_BUS  1U
#define SUBARU_CAM_BUS  2U

#define SUBARU_BASE_TX_MSGS(alt_bus, lkas_msg) \
  {lkas_msg,                     SUBARU_MAIN_BUS, 8, .check_relay = true},  \
  {MSG_SUBARU_ES_DashStatus,     SUBARU_MAIN_BUS, 8, .check_relay = true},  \
  {MSG_SUBARU_ES_LKAS_State,     SUBARU_MAIN_BUS, 8, .check_relay = true},  \
  {MSG_SUBARU_ES_Infotainment,   SUBARU_MAIN_BUS, 8, .check_relay = true},  \

#define SUBARU_COMMON_TX_MSGS(alt_bus) \
  {MSG_SUBARU_ES_Distance, alt_bus, 8, .check_relay = false}, \

#define SUBARU_GEN2_ANGLE_TX_MSGS(angle_bus) \
  {MSG_SUBARU_ES_LKAS_ANGLE, angle_bus, 8, .check_relay = true}, \
  {MSG_SUBARU_ES_DashStatus, SUBARU_MAIN_BUS, 8, .check_relay = true}, \
  {MSG_SUBARU_ES_LKAS_State, SUBARU_MAIN_BUS, 8, .check_relay = true}, \
  {MSG_SUBARU_ES_Infotainment, SUBARU_MAIN_BUS, 8, .check_relay = true}, \
  SUBARU_COMMON_TX_MSGS(SUBARU_ALT_BUS)

#define SUBARU_COMMON_RX_CHECKS(alt_bus)                                                                                                         \
  {.msg = {{MSG_SUBARU_Throttle,        SUBARU_MAIN_BUS, 8, 100U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_Steering_Torque, SUBARU_MAIN_BUS, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{MSG_SUBARU_Wheel_Speeds,    alt_bus,         8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{MSG_SUBARU_Brake_Status,    alt_bus,         8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{MSG_SUBARU_CruiseControl,   alt_bus,         8, 20U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \

#define SUBARU_GEN2_ANGLE_RX_CHECKS()                                                                                                           \
  {.msg = {{MSG_SUBARU_Throttle,        SUBARU_MAIN_BUS, 8, 100U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_Steering_Torque, SUBARU_MAIN_BUS, 8, 50U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_Steering_2,      SUBARU_MAIN_BUS, 8, 50U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_Wheel_Speeds,    SUBARU_ALT_BUS,  8, 50U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_Brake_Status,    SUBARU_ALT_BUS,  8, 50U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_ES_Status,       SUBARU_ALT_BUS,  8, 20U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_ES_DashStatus,   SUBARU_CAM_BUS,  8, 10U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \

static bool subaru_gen2 = false;
static bool subaru_forester_angle = false;
static bool subaru_d_platform = false;
static bool subaru_lkas_angle = false;
static bool subaru_fixed_angle_limits = false;
static bool subaru_angle_main_bus = false;
static bool subaru_angle_invalid = false;

static uint32_t subaru_get_checksum(const CANPacket_t *msg) {
  return (uint8_t)msg->data[0];
}

static uint8_t subaru_get_counter(const CANPacket_t *msg) {
  return (uint8_t)(msg->data[1] & 0xFU);
}

static uint32_t subaru_compute_checksum(const CANPacket_t *msg) {
  int len = GET_LEN(msg);
  uint8_t checksum = (uint8_t)(msg->addr) + (uint8_t)((unsigned int)(msg->addr) >> 8U);
  for (int i = 1; i < len; i++) {
    checksum += (uint8_t)msg->data[i];
  }
  return checksum;
}

static void subaru_rx_hook(const CANPacket_t *msg) {
  const unsigned int alt_main_bus = subaru_gen2 ? SUBARU_ALT_BUS : SUBARU_MAIN_BUS;

  if (msg_matches(msg, MSG_SUBARU_Steering_Torque, SUBARU_MAIN_BUS)) {
    subaru_software_fault = GET_BIT(msg, 12U) || GET_BIT(msg, 29U);
    int torque_driver_new;
    torque_driver_new = ((GET_BYTES_LE(msg, 0, 4) >> 16) & 0x7FFU);
    torque_driver_new = -1 * to_signed(torque_driver_new, 11);
    update_sample(&torque_driver, torque_driver_new);
    if (subaru_aol_enabled) {
      subaru_aol_permanent = GET_BIT(msg, 12U);
      subaru_aol_temporary = GET_BIT(msg, 29U);
      if (subaru_aol_permanent) {
        subaru_aol_clear();
      }
    }
  }

  if (subaru_lkas_angle && (msg->addr == MSG_SUBARU_Steering_2) && (msg->bus == SUBARU_MAIN_BUS)) {
    int angle_meas_new = GET_BYTES_LE(msg, 3, 3) & 0x1FFFFU;
    angle_meas_new = -1 * to_signed(angle_meas_new, 17);
    update_sample(&angle_meas, angle_meas_new);
  }

  if (subaru_lkas_angle && (msg->addr == MSG_SUBARU_ES_Status) && (msg->bus == (subaru_forester_angle ? SUBARU_CAM_BUS : SUBARU_ALT_BUS))) {
    pcm_cruise_check(GET_BIT(msg, 29U));
    subaru_aol_grant(GET_BIT(msg, 29U));
  }
  if (subaru_lkas_angle && (msg->addr == MSG_SUBARU_ES_DashStatus) && (msg->bus == SUBARU_CAM_BUS)) {
    acc_main_on = GET_BIT(msg, 49U);
    subaru_aol_observe_main(acc_main_on);
  }

  if (subaru_aol_main_arm && msg_matches(msg, MSG_SUBARU_ES_Distance, subaru_lkas_angle ? SUBARU_ALT_BUS : SUBARU_CAM_BUS)) {
    subaru_aol_acc_fault = GET_BIT(msg, 15U);
    if (subaru_aol_acc_fault) {
      subaru_aol_clear();
    }
  }

  // enter controls on rising edge of ACC, exit controls on ACC off
  if (!subaru_lkas_angle && msg_matches(msg, MSG_SUBARU_CruiseControl, alt_main_bus)) {
    bool cruise_engaged = (msg->data[5] >> 1) & 1U;
    pcm_cruise_check(cruise_engaged);
    subaru_software_main = GET_BIT(msg, 40U);
    if (subaru_aol_enabled) {
      subaru_aol_grant(cruise_engaged);
      subaru_aol_observe_main(GET_BIT(msg, 40U));
    }
  }

  // update vehicle moving with any non-zero wheel speed
  if (msg_matches(msg, MSG_SUBARU_Wheel_Speeds, alt_main_bus)) {
    uint32_t fr = (GET_BYTES_LE(msg, 1, 3) >> 4) & 0x1FFFU;
    uint32_t rr = (GET_BYTES_LE(msg, 3, 3) >> 1) & 0x1FFFU;
    uint32_t rl = (GET_BYTES_LE(msg, 4, 3) >> 6) & 0x1FFFU;
    uint32_t fl = (GET_BYTES_LE(msg, 6, 2) >> 3) & 0x1FFFU;

    vehicle_moving = (fr | rr | rl | fl) != 0U;

    UPDATE_VEHICLE_SPEED((fr + rr + rl + fl) / 4.0 * 0.057 * KPH_TO_MS);
  }

  if (msg_matches(msg, MSG_SUBARU_Brake_Status, alt_main_bus)) {
    brake_pressed = (msg->data[7] >> 6) & 1U;
  }

  if (msg_matches(msg, MSG_SUBARU_Throttle, subaru_d_platform ? SUBARU_ALT_BUS : SUBARU_MAIN_BUS)) {
    gas_pressed = msg->data[4] != 0U;
  }
  subaru_sng_rx(msg);
  subaru_stop_start_rx(msg);
  subaru_avh_rx(msg);
}

static bool subaru_tx_hook(const CANPacket_t *msg) {
  const TorqueSteeringLimits SUBARU_STEERING_LIMITS      = SUBARU_STEERING_LIMITS_GENERATOR(2047, 50, 70);
  const TorqueSteeringLimits SUBARU_GEN2_STEERING_LIMITS = SUBARU_STEERING_LIMITS_GENERATOR(1000, 40, 40);
  const AngleSteeringLimits SUBARU_ANGLE_STEERING_LIMITS = {
    .max_angle = (subaru_forester_angle ? 650 : SUBARU_JACOBW_MAX_ANGLE) * 100,
    .angle_deg_to_can = 100.,
    .frequency = 50U,
  };
  const AngleSteeringLimits SUBARU_FIXED_ANGLE_STEERING_LIMITS = {
    .max_angle = 545 * 100,
    .angle_deg_to_can = 100.,
    .angle_rate_up_lookup = {{0.0, 5.0, 35.0}, {5.0, 0.8, 0.15}},
    .angle_rate_down_lookup = {{0.0, 5.0, 35.0}, {5.0, 0.8, 0.15}},
    .frequency = 50U,
  };
  // Only Crosstrek 2025 selects this VM profile. Match the host CarParams
  // vehicle model instead of the frozen generic angle example's Ascent geometry.
  const AngleSteeringParams SUBARU_CROSSTREK_2025_ANGLE_PARAMS = {
    .slip_factor = -0.0006281955319491566,
    .steer_ratio = 17.0,
    .wheelbase = 2.6700000762939453,
  };

  const AngleSteeringParams SUBARU_FORESTER_2022_ANGLE_PARAMS = {
    .slip_factor = -0.0006281956135115129,
    .steer_ratio = 17.0,
    .wheelbase = 2.6700000762939453,
  };

  const LongitudinalLimits SUBARU_LONG_LIMITS = {
    .min_gas = 808,       // appears to be engine braking
    .max_gas = 3400,      // approx  2 m/s^2 when maxing cruise_rpm and cruise_throttle
    .inactive_gas = 1818,  // this is zero acceleration
    .max_brake = 600,     // approx -3.5 m/s^2

    .min_transmission_rpm = 0,
    .max_transmission_rpm = 3600,
  };

  bool tx = false;
  if (!subaru_angle_invalid) {
    tx = true;
    bool violation = false;

    if (msg->addr == 0x390U) {
      violation |= !subaru_stop_start_tx(msg);
    }

    if (msg->addr == 0x6BBU) {
      violation |= !subaru_avh_tx(msg);
    }

    // steer cmd checks
    if (msg->addr == MSG_SUBARU_ES_LKAS) {
      int desired_torque = ((GET_BYTES_LE(msg, 0, 4) >> 16) & 0x1FFFU);
      desired_torque = -1 * to_signed(desired_torque, 13);

      bool steer_req = (msg->data[3] >> 5) & 1U;

      const TorqueSteeringLimits limits = subaru_gen2 ? SUBARU_GEN2_STEERING_LIMITS : SUBARU_STEERING_LIMITS;
      violation |= steer_torque_cmd_checks(desired_torque, steer_req, limits);
    }
    if (msg->addr == MSG_SUBARU_ES_LKAS_ANGLE) {
      int desired_angle = GET_BYTES_LE(msg, 5, 3) & 0x1FFFFU;
      desired_angle = -1 * to_signed(desired_angle, 17);
      bool lkas_request = GET_BIT(msg, 12U);
      violation |= lkas_request && !acc_main_on;
      if (subaru_angle_main_bus) {
        violation |= SAFETY_ABS(desired_angle) > SUBARU_FIXED_ANGLE_STEERING_LIMITS.max_angle;
      }
      if (subaru_fixed_angle_limits) {
        violation |= steer_angle_cmd_checks(desired_angle, lkas_request, SUBARU_FIXED_ANGLE_STEERING_LIMITS);
      } else {
        if (!subaru_forester_angle) {
          violation |= SAFETY_ABS(desired_angle) > SUBARU_ANGLE_STEERING_LIMITS.max_angle;
        }
        violation |= steer_angle_cmd_checks_vm(desired_angle, lkas_request, SUBARU_ANGLE_STEERING_LIMITS, subaru_forester_angle ? SUBARU_FORESTER_2022_ANGLE_PARAMS : SUBARU_CROSSTREK_2025_ANGLE_PARAMS);
      }
    }

    if (msg->addr == MSG_SUBARU_ES_LKAS_Alert) {
      violation |= subaru_get_checksum(msg) != subaru_compute_checksum(msg);
    }

    // check es_distance cruise_throttle limits
    if (msg->addr == MSG_SUBARU_ES_Distance) {
      int cruise_throttle = (GET_BYTES_LE(msg, 2, 2) & 0x1FFFU);
      bool cruise_cancel = (msg->data[7] >> 0) & 1U;

      // If openpilot is not controlling long, only allow ES_Distance for cruise cancel requests,
      // (when Cruise_Cancel is true, and Cruise_Throttle is inactive)
      violation |= (cruise_throttle != SUBARU_LONG_LIMITS.inactive_gas);
      violation |= (!cruise_cancel);
    }

    if (violation){
      tx = false;
    }
  }

  if ((msg->addr == 0x40U) || (msg->addr == 0x139U)) {
    tx = subaru_sng_tx(msg);
  }
  if (msg->addr == 0x146U) {
    tx = subaru_software_tx(msg);
  }
  return tx;
}

static safety_config subaru_init(uint16_t raw_param) {
  uint16_t param = raw_param;
  const bool impreza_main = (param == 4096U) || (param == 4104U) || (param == 4608U) || (param == 4616U);
  if (impreza_main) {
    param &= ~4096U;
  }
#ifdef ALLOW_DEBUG
  const bool avh = ((param == 1169U) || (param == 1425U)) &&
    (((unsigned int)alternative_experience == 0U) || ((unsigned int)alternative_experience == 32U));
#else
  const bool avh = false;
#endif
  const bool stop_start = ((param == 401U) || (param == 465U) || (avh && (param == 1425U))) &&
    (((unsigned int)alternative_experience == 0U) || ((unsigned int)alternative_experience == 32U));
  const uint16_t base_param = stop_start ? ((param == 465U) ? 209U : 145U) : (avh ? 145U : param);
  static const CanMsg SUBARU_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_MAIN_BUS, MSG_SUBARU_ES_LKAS)
    SUBARU_COMMON_TX_MSGS(SUBARU_MAIN_BUS)
  };

  static const CanMsg SUBARU_SOFTWARE_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_MAIN_BUS, MSG_SUBARU_ES_LKAS)
    SUBARU_COMMON_TX_MSGS(SUBARU_MAIN_BUS)
    {0x146U, SUBARU_MAIN_BUS, 8, .check_relay = false},
  };

  static const CanMsg SUBARU_SNG_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_MAIN_BUS, MSG_SUBARU_ES_LKAS)
    SUBARU_COMMON_TX_MSGS(SUBARU_MAIN_BUS)
    {0x40U, SUBARU_CAM_BUS, 8, .check_relay = true, .disable_static_blocking = true},
    {0x139U, SUBARU_CAM_BUS, 8, .check_relay = true, .disable_static_blocking = true},
  };
  static const CanMsg SUBARU_SNG_SOFTWARE_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_MAIN_BUS, MSG_SUBARU_ES_LKAS)
    SUBARU_COMMON_TX_MSGS(SUBARU_MAIN_BUS)
    {0x146U, SUBARU_MAIN_BUS, 8, .check_relay = false},
    {0x40U, SUBARU_CAM_BUS, 8, .check_relay = true, .disable_static_blocking = true},
    {0x139U, SUBARU_CAM_BUS, 8, .check_relay = true, .disable_static_blocking = true},
  };

  static const CanMsg SUBARU_GEN2_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_ALT_BUS, MSG_SUBARU_ES_LKAS)
    SUBARU_COMMON_TX_MSGS(SUBARU_ALT_BUS)
  };
  static const CanMsg SUBARU_GEN2_ANGLE_TX_MSGS_LIST[] = {
    SUBARU_GEN2_ANGLE_TX_MSGS(SUBARU_MAIN_BUS)
  };

  static const CanMsg SUBARU_ASCENT_ANGLE_TX_MSGS_LIST[] = {
    SUBARU_GEN2_ANGLE_TX_MSGS(SUBARU_MAIN_BUS)
  };

  static const CanMsg SUBARU_ASCENT_ALERT_TX_MSGS[] = {
    SUBARU_GEN2_ANGLE_TX_MSGS(SUBARU_MAIN_BUS)
    {MSG_SUBARU_ES_LKAS_Alert, SUBARU_MAIN_BUS, 8, .check_relay = true},
  };

  static const CanMsg SUBARU_STOP_START_TX_MSGS[] = {
    SUBARU_GEN2_ANGLE_TX_MSGS(SUBARU_MAIN_BUS)
    {0x390U, SUBARU_ALT_BUS, 8, .check_relay = false},
  };

  static const CanMsg SUBARU_AVH_TX_MSGS[] = {
    SUBARU_GEN2_ANGLE_TX_MSGS(SUBARU_MAIN_BUS)
    {0x6BBU, SUBARU_ALT_BUS, 8, .check_relay = false},
  };
  static const CanMsg SUBARU_AVH_STOP_START_TX_MSGS[] = {
    SUBARU_GEN2_ANGLE_TX_MSGS(SUBARU_MAIN_BUS)
    {0x6BBU, SUBARU_ALT_BUS, 8, .check_relay = false},
    {0x390U, SUBARU_ALT_BUS, 8, .check_relay = false},
  };

  static const CanMsg SUBARU_FORESTER_ANGLE_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_MAIN_BUS, MSG_SUBARU_ES_LKAS_ANGLE)
    SUBARU_COMMON_TX_MSGS(SUBARU_MAIN_BUS)
  };

  static const CanMsg SUBARU_OUTBACK_STOP_START_TX_MSGS[] = {
    SUBARU_GEN2_ANGLE_TX_MSGS(SUBARU_MAIN_BUS)
    {0x390U, SUBARU_ALT_BUS, 8, .check_relay = false},
  };

  const uint16_t SUBARU_PARAM_ASCENT_ALERT = 2048;
  const bool ascent_alert = param == (1U | 16U | 128U | 32U | SUBARU_PARAM_ASCENT_ALERT);
  const uint16_t SUBARU_PARAM_GEN2 = 1;
  const uint16_t SUBARU_PARAM_LKAS_ANGLE = 16;
  const uint16_t SUBARU_PARAM_FIXED_ANGLE = 128;

  const uint16_t SUBARU_PARAM_ANGLE_MAIN_BUS = 32;
  subaru_d_platform = (base_param == 209U) &&
    (((unsigned int)alternative_experience == 0U) || ((unsigned int)alternative_experience == 32U));
  subaru_angle_main_bus = GET_FLAG(param, SUBARU_PARAM_ANGLE_MAIN_BUS) || subaru_d_platform;

  subaru_forester_angle = (param == 16U) &&
    (((unsigned int)alternative_experience == 0U) || ((unsigned int)alternative_experience == 32U));
  subaru_gen2 = GET_FLAG(param, SUBARU_PARAM_GEN2);
  subaru_lkas_angle = GET_FLAG(param, SUBARU_PARAM_LKAS_ANGLE);
  subaru_fixed_angle_limits = GET_FLAG(param, SUBARU_PARAM_FIXED_ANGLE);
  subaru_angle_invalid = (subaru_lkas_angle && (base_param != (SUBARU_PARAM_GEN2 | SUBARU_PARAM_LKAS_ANGLE)) &&
                          (base_param != (SUBARU_PARAM_GEN2 | SUBARU_PARAM_LKAS_ANGLE | SUBARU_PARAM_FIXED_ANGLE)) &&
                          (base_param != (SUBARU_PARAM_GEN2 | SUBARU_PARAM_LKAS_ANGLE | SUBARU_PARAM_FIXED_ANGLE | SUBARU_PARAM_ANGLE_MAIN_BUS)) && !ascent_alert && !subaru_forester_angle && !subaru_d_platform) ||
                         ((subaru_fixed_angle_limits || subaru_angle_main_bus) && !subaru_lkas_angle);

  // TODO: re-enable once more work is done on the limits
  // revert this in the PR that re-enables Subaru longitudinal: https://github.com/commaai/opendbc/pull/3689

  subaru_avh_init(avh);
  subaru_stop_start_init(stop_start, (uint8_t)(subaru_d_platform ? SUBARU_ALT_BUS : SUBARU_MAIN_BUS));
  subaru_sng_reset(((param == 8U) || (param == 520U)) &&
    (((unsigned int)alternative_experience == 0U) || ((unsigned int)alternative_experience == 32U)));
  subaru_software_reset(((param == 512U) || (param == 520U)) && (((unsigned int)alternative_experience == 0U) ||
    ((unsigned int)alternative_experience == 32U)));
  subaru_aol_reset();
  subaru_aol_gear_bus = (uint8_t)(subaru_d_platform ? SUBARU_ALT_BUS : SUBARU_MAIN_BUS);
  subaru_aol_enabled = ((unsigned int)alternative_experience == 32U) &&
    ((param == 209U) || (param == 465U) || (param == 16U) || (param == 0U) || (param == 1U) || (param == 17U) || (param == 145U) ||
     avh || (param == 177U) || (param == 401U) || (param == 8U) || (param == 520U) || (param == 512U) || (param == 2225U));
  subaru_aol_main_arm = subaru_aol_enabled && ((param == 17U) || impreza_main || subaru_d_platform);
  if (subaru_aol_enabled) {
    static const AolSafetyPolicy policy = {
      .reset = subaru_aol_reset,
      .host_request = subaru_aol_host_request,
      .request_mask = subaru_aol_request,
      .permission_mask = subaru_aol_permission,
      .rx_invalid = subaru_aol_clear,
    };
    aol_policy = &policy;
  }

  safety_config ret;
  // Only one Subaru profile is active; share its mutable RX workspace.
  static RxCheck subaru_rx_workspace[8];
  int rx_count;
  if (subaru_lkas_angle) {
    static const RxCheck subaru_gen2_angle_rx_checks[] = {
      SUBARU_GEN2_ANGLE_RX_CHECKS()
      {.msg = {{MSG_SUBARU_ES_Distance, SUBARU_ALT_BUS, 8, 20U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    };
    rx_count = sizeof(subaru_gen2_angle_rx_checks) / sizeof(subaru_gen2_angle_rx_checks[0]);
    for (int i = 0; i < rx_count; i++) {
      subaru_rx_workspace[i] = subaru_gen2_angle_rx_checks[i];
    }
    // Only one Subaru angle profile is active. Rebind the complete topology
    // on every init; the safety selector resets every selected RX status afterward.
    subaru_rx_workspace[0].msg[0].bus = subaru_d_platform ? SUBARU_ALT_BUS : SUBARU_MAIN_BUS;
    subaru_rx_workspace[3].msg[0].bus = subaru_forester_angle ? SUBARU_MAIN_BUS : SUBARU_ALT_BUS;
    subaru_rx_workspace[4].msg[0].bus = subaru_forester_angle ? SUBARU_MAIN_BUS : SUBARU_ALT_BUS;
    subaru_rx_workspace[5].msg[0].bus = subaru_forester_angle ? SUBARU_CAM_BUS : SUBARU_ALT_BUS;
    ret = subaru_d_platform ? (stop_start ? BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_OUTBACK_STOP_START_TX_MSGS) :
                                          BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_ASCENT_ANGLE_TX_MSGS_LIST)) :
          subaru_forester_angle ? BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_FORESTER_ANGLE_TX_MSGS) :
          avh ? (stop_start ? BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_AVH_STOP_START_TX_MSGS) :
                              BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_AVH_TX_MSGS)) :
          stop_start ? BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_STOP_START_TX_MSGS) :
          ascent_alert ? BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_ASCENT_ALERT_TX_MSGS) :
          subaru_angle_main_bus ? BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_ASCENT_ANGLE_TX_MSGS_LIST) :
                                  BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_GEN2_ANGLE_TX_MSGS_LIST);
    // Keep the original seven checks on other profiles; only MAIN arming needs ACC health.
    if (!subaru_aol_main_arm) {
      rx_count--;
    }
  } else if (subaru_gen2) {
    static const RxCheck subaru_gen2_rx_checks[] = {
      SUBARU_COMMON_RX_CHECKS(SUBARU_ALT_BUS)
    };
    rx_count = sizeof(subaru_gen2_rx_checks) / sizeof(subaru_gen2_rx_checks[0]);
    for (int i = 0; i < rx_count; i++) {
      subaru_rx_workspace[i] = subaru_gen2_rx_checks[i];
    }
    ret = BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_GEN2_TX_MSGS);
  } else {
    static const RxCheck subaru_rx_checks[] = {
      SUBARU_COMMON_RX_CHECKS(SUBARU_MAIN_BUS)
      {.msg = {{MSG_SUBARU_ES_Distance, SUBARU_CAM_BUS, 8, 20U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    };
    rx_count = sizeof(subaru_rx_checks) / sizeof(subaru_rx_checks[0]);
    for (int i = 0; i < rx_count; i++) {
      subaru_rx_workspace[i] = subaru_rx_checks[i];
    }
    if (!subaru_aol_main_arm) {
      rx_count--;
    }
    ret = subaru_sng_enabled ? (subaru_software_enabled ?
      BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_SNG_SOFTWARE_TX_MSGS) : BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_SNG_TX_MSGS)) :
      subaru_software_enabled ? BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_SOFTWARE_TX_MSGS) :
                                   BUILD_SAFETY_CFG(subaru_rx_workspace, SUBARU_TX_MSGS);
  }
  ret.rx_checks_len = rx_count;
  if ((((unsigned int)alternative_experience == 32U) && !subaru_aol_enabled) ||
      (impreza_main && ((unsigned int)alternative_experience != 32U))) {
    ret.tx_msgs = NULL;
    ret.tx_msgs_len = 0;
  }
  return ret;
}

static void subaru_optional_rx(const CANPacket_t *msg) {
  subaru_aol_optional_rx(msg);
  subaru_software_optional_rx(msg);
  subaru_sng_rx(msg);
  subaru_stop_start_rx(msg);
  subaru_avh_rx(msg);
}

const safety_hooks subaru_hooks = {
  .init = subaru_init,
  .rx = subaru_rx_hook,
  .optional_rx = subaru_optional_rx,
  .fwd = subaru_sng_fwd,
  .tx = subaru_tx_hook,
  .get_counter = subaru_get_counter,
  .get_checksum = subaru_get_checksum,
  .compute_checksum = subaru_compute_checksum,
};
