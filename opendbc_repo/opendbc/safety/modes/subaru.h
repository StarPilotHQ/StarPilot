#pragma once

#include "opendbc/safety/declarations.h"

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
#define MSG_SUBARU_Steering_2            0x11aU
#define MSG_SUBARU_Steering_Torque       0x119U
#define MSG_SUBARU_Wheel_Speeds          0x13aU
#define MSG_SUBARU_Brake_Pedal           0x139U

#define MSG_SUBARU_ES_LKAS               0x122U
#define MSG_SUBARU_ES_LKAS_ANGLE         0x124U
#define MSG_SUBARU_ES_Brake              0x220U
#define MSG_SUBARU_ES_Distance           0x221U
#define MSG_SUBARU_ES_Status             0x222U
#define MSG_SUBARU_ES_DashStatus         0x321U
#define MSG_SUBARU_ES_LKAS_State         0x322U
#define MSG_SUBARU_ES_Infotainment       0x323U
#define MSG_SUBARU_ES_LKAS_Alert         0x3C4U
#define MSG_SUBARU_Cruise_Buttons        0x146U

#define MSG_SUBARU_ES_UDS_Request        0x787U

#define MSG_SUBARU_ES_HighBeamAssist     0x121U
#define MSG_SUBARU_ES_STATIC_1           0x22aU
#define MSG_SUBARU_ES_STATIC_2           0x325U
#define MSG_SUBARU_Dashlights            0x390U

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

#define SUBARU_REDNECK_TX_MSGS() \
  {MSG_SUBARU_Cruise_Buttons, SUBARU_MAIN_BUS, 8, .check_relay = false}, \

#define SUBARU_D_PLATFORM_ANGLE_TX_MSGS(bus) \
  {MSG_SUBARU_ES_LKAS_ANGLE,   bus, 8, .check_relay = true}, \
  {MSG_SUBARU_ES_DashStatus,   bus, 8, .check_relay = true}, \
  {MSG_SUBARU_ES_LKAS_State,   bus, 8, .check_relay = true}, \
  {MSG_SUBARU_ES_Infotainment, bus, 8, .check_relay = true}, \

#define SUBARU_STOP_START_TX_MSGS(bus) \
  {MSG_SUBARU_Dashlights, bus, 8, .check_relay = false}, \

#define SUBARU_COMMON_LONG_TX_MSGS(alt_bus) \
  {MSG_SUBARU_ES_Distance,       alt_bus,         8, .check_relay = true}, \
  {MSG_SUBARU_ES_Brake,          alt_bus,         8, .check_relay = true}, \
  {MSG_SUBARU_ES_Status,         alt_bus,         8, .check_relay = true}, \

#define SUBARU_GEN2_LONG_ADDITIONAL_TX_MSGS() \
  {MSG_SUBARU_ES_UDS_Request,    SUBARU_CAM_BUS,  8, .check_relay = false}, \
  {MSG_SUBARU_ES_HighBeamAssist, SUBARU_MAIN_BUS, 8, .check_relay = false}, \
  {MSG_SUBARU_ES_STATIC_1,       SUBARU_MAIN_BUS, 8, .check_relay = false}, \
  {MSG_SUBARU_ES_STATIC_2,       SUBARU_MAIN_BUS, 8, .check_relay = false}, \

#define SUBARU_STOP_AND_GO_ADDITIONAL_TX_MSGS() \
  {MSG_SUBARU_Throttle,          SUBARU_CAM_BUS,  8, .check_relay = true}, \
  {MSG_SUBARU_Brake_Pedal,       SUBARU_CAM_BUS,  8, .check_relay = true}, \

#define SUBARU_COMMON_RX_CHECKS(alt_bus)                                                                                                         \
  {.msg = {{MSG_SUBARU_Throttle,        SUBARU_MAIN_BUS, 8, 100U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_Steering_Torque, SUBARU_MAIN_BUS, 8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{MSG_SUBARU_Wheel_Speeds,    alt_bus,         8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{MSG_SUBARU_Brake_Status,    alt_bus,         8, 50U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{MSG_SUBARU_CruiseControl,   alt_bus,         8, 20U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \

#define SUBARU_LKAS_ANGLE_RX_CHECKS(alt_bus, status_bus)                                                                                         \
  {.msg = {{MSG_SUBARU_Throttle,        SUBARU_MAIN_BUS, 8, 100U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_Steering_Torque, SUBARU_MAIN_BUS, 8, 50U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{MSG_SUBARU_Steering_2,      SUBARU_MAIN_BUS, 8, 50U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{MSG_SUBARU_Wheel_Speeds,    alt_bus,         8, 50U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{MSG_SUBARU_Brake_Status,    alt_bus,         8, 50U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{MSG_SUBARU_ES_Status,       status_bus,      8, 20U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \
  {.msg = {{MSG_SUBARU_ES_DashStatus,   SUBARU_CAM_BUS,  8, 10U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}},  \

#define SUBARU_D_PLATFORM_ANGLE_RX_CHECKS() \
  {.msg = {{MSG_SUBARU_Throttle,        SUBARU_ALT_BUS, 8, 100U, .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_Steering_Torque, SUBARU_MAIN_BUS, 8, 50U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_Steering_2,      SUBARU_MAIN_BUS, 8, 50U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_Wheel_Speeds,    SUBARU_ALT_BUS, 8, 50U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_Brake_Status,    SUBARU_ALT_BUS, 8, 50U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_ES_Brake,        SUBARU_ALT_BUS, 8, 50U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_ES_Status,       SUBARU_ALT_BUS, 8, 20U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MSG_SUBARU_ES_DashStatus,   SUBARU_CAM_BUS, 8, 10U,  .max_counter = 15U, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \

static bool subaru_gen2 = false;
static bool subaru_longitudinal = false;
static bool subaru_ascent_long_throttle = false;
static bool subaru_stop_and_go = false;
static bool subaru_lkas_angle = false;
static bool subaru_d_platform = false;
static bool subaru_fixed_angle_limits = false;
static bool subaru_stop_start_button = false;
static bool subaru_redneck_cruise = false;

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

#include "opendbc/safety/modes/subaru_avh.h"

static void subaru_rx_hook(const CANPacket_t *msg) {
  const unsigned int alt_main_bus = subaru_gen2 ? SUBARU_ALT_BUS : SUBARU_MAIN_BUS;
  const unsigned int status_bus = subaru_gen2 ? SUBARU_ALT_BUS : SUBARU_CAM_BUS;
  const unsigned int steering_bus = SUBARU_MAIN_BUS;
  const unsigned int main_bus = subaru_d_platform ? SUBARU_ALT_BUS : SUBARU_MAIN_BUS;

  if ((msg->addr == MSG_SUBARU_Steering_Torque) && (msg->bus == steering_bus)) {
    int torque_driver_new;
    torque_driver_new = ((GET_BYTES(msg, 0, 4) >> 16) & 0x7FFU);
    torque_driver_new = -1 * to_signed(torque_driver_new, 11);
    update_sample(&torque_driver, torque_driver_new);
  }

  if (subaru_lkas_angle && (msg->addr == MSG_SUBARU_Steering_2) && (msg->bus == steering_bus)) {
    int angle_meas_new = GET_BYTES(msg, 3, 3) & 0x1FFFFU;
    angle_meas_new = -1 * to_signed(angle_meas_new, 17);
    update_sample(&angle_meas, angle_meas_new);
  }

  if (subaru_lkas_angle && (msg->addr == MSG_SUBARU_ES_Status) && (msg->bus == status_bus)) {
    bool cruise_engaged = GET_BIT(msg, 29U);
    pcm_cruise_check(cruise_engaged);
  }

  if (subaru_lkas_angle && (msg->addr == MSG_SUBARU_ES_DashStatus) && (msg->bus == SUBARU_CAM_BUS)) {
    acc_main_on = GET_BIT(msg, 49U);
  }

  if (!subaru_lkas_angle && (msg->addr == MSG_SUBARU_CruiseControl) && (msg->bus == alt_main_bus)) {
    bool cruise_engaged = (msg->data[5] >> 1) & 1U;
    pcm_cruise_check(cruise_engaged);

    acc_main_on = GET_BIT(msg, 40U);
  }

  // update vehicle moving with any non-zero wheel speed
  if ((msg->addr == MSG_SUBARU_Wheel_Speeds) && (msg->bus == alt_main_bus)) {
    uint32_t fr = (GET_BYTES(msg, 1, 3) >> 4) & 0x1FFFU;
    uint32_t rr = (GET_BYTES(msg, 3, 3) >> 1) & 0x1FFFU;
    uint32_t rl = (GET_BYTES(msg, 4, 3) >> 6) & 0x1FFFU;
    uint32_t fl = (GET_BYTES(msg, 6, 2) >> 3) & 0x1FFFU;

    vehicle_moving = (fr > 0U) || (rr > 0U) || (rl > 0U) || (fl > 0U);

    UPDATE_VEHICLE_SPEED((fr + rr + rl + fl) / 4.0 * 0.057 * KPH_TO_MS);
  }

  if ((msg->addr == MSG_SUBARU_Brake_Status) && (msg->bus == alt_main_bus)) {
    brake_pressed = (msg->data[7] >> 6) & 1U;
  }

  if ((msg->addr == MSG_SUBARU_Throttle) && (msg->bus == main_bus)) {
    gas_pressed = msg->data[4] != 0U;
  }
}

static bool subaru_tx_hook(const CANPacket_t *msg) {
  const TorqueSteeringLimits SUBARU_STEERING_LIMITS      = SUBARU_STEERING_LIMITS_GENERATOR(3071, 50, 70);
  const TorqueSteeringLimits SUBARU_GEN2_STEERING_LIMITS = SUBARU_STEERING_LIMITS_GENERATOR(1500, 35, 50);

  const AngleSteeringLimits SUBARU_ANGLE_STEERING_LIMITS = {
    .max_angle = 650 * 100,
    .angle_deg_to_can = 100.,
    .frequency = 50U,
  };

  const AngleSteeringLimits SUBARU_FIXED_ANGLE_STEERING_LIMITS = {
    .max_angle = 545 * 100,
    .angle_deg_to_can = 100.,
    .angle_rate_up_lookup = {
      {0.0, 5.0, 35.0},
      {5.0, 0.8, 0.15},
    },
    .angle_rate_down_lookup = {
      {0.0, 5.0, 35.0},
      {5.0, 0.8, 0.15},
    },
    .frequency = 50U,
  };

  const AngleSteeringParams SUBARU_ANGLE_STEERING_PARAMS = {
    .slip_factor = -0.000580374471400815,
    .steer_ratio = 13.5,
    .wheelbase = 2.890000104904175,
  };

  const LongitudinalLimits SUBARU_LONG_LIMITS = {
    .min_gas = 808,       // appears to be engine braking
    .max_gas = subaru_ascent_long_throttle ? 3800 : 3400, // 2019-21 Ascent; 3400 by default
    .inactive_gas = 1818, // this is zero acceleration
    .max_brake = 600,     // approx -3.5 m/s^2

    .min_transmission_rpm = 0,
    .max_transmission_rpm = 3600,
  };

  bool tx = true;
  bool violation = false;

  // steer cmd checks
  if (msg->addr == MSG_SUBARU_ES_LKAS) {
    int desired_torque = ((GET_BYTES(msg, 0, 4) >> 16) & 0x1FFFU);
    desired_torque = -1 * to_signed(desired_torque, 13);

    bool steer_req = (msg->data[3] >> 5) & 1U;

    const TorqueSteeringLimits limits = subaru_gen2 ? SUBARU_GEN2_STEERING_LIMITS : SUBARU_STEERING_LIMITS;
    violation |= steer_torque_cmd_checks(desired_torque, steer_req, limits);
  }

  if (msg->addr == MSG_SUBARU_ES_LKAS_ANGLE) {
    int desired_angle = GET_BYTES(msg, 5, 3) & 0x1FFFFU;
    desired_angle = -1 * to_signed(desired_angle, 17);
    bool lkas_request = GET_BIT(msg, 12U);

    if (subaru_fixed_angle_limits) {
      violation |= steer_angle_cmd_checks(desired_angle, lkas_request, SUBARU_FIXED_ANGLE_STEERING_LIMITS);
    } else {
      violation |= steer_angle_cmd_checks_vm(desired_angle, lkas_request, SUBARU_ANGLE_STEERING_LIMITS, SUBARU_ANGLE_STEERING_PARAMS);
    }
  }

  // check es_brake brake_pressure limits
  if (msg->addr == MSG_SUBARU_ES_Brake) {
    int es_brake_pressure = GET_BYTES(msg, 2, 2);
    violation |= longitudinal_brake_checks(es_brake_pressure, SUBARU_LONG_LIMITS);
  }

  // check es_distance cruise_throttle limits
  if (msg->addr == MSG_SUBARU_ES_Distance) {
    int cruise_throttle = (GET_BYTES(msg, 2, 2) & 0x1FFFU);
    bool cruise_cancel = (msg->data[7] >> 0) & 1U;

    if (subaru_longitudinal) {
      violation |= longitudinal_gas_checks(cruise_throttle, SUBARU_LONG_LIMITS);
    } else {
      // If openpilot is not controlling long, only allow ES_Distance for cruise cancel requests,
      // (when Cruise_Cancel is true, and Cruise_Throttle is inactive)
      violation |= (cruise_throttle != SUBARU_LONG_LIMITS.inactive_gas);
      violation |= (!cruise_cancel);
    }
  }

  // check es_status transmission_rpm limits
  if (msg->addr == MSG_SUBARU_ES_Status) {
    int transmission_rpm = (GET_BYTES(msg, 2, 2) & 0x1FFFU);
    violation |= longitudinal_transmission_rpm_checks(transmission_rpm, SUBARU_LONG_LIMITS);
  }

  if (msg->addr == MSG_SUBARU_ES_UDS_Request) {
    // tester present ('\x02\x3E\x80\x00\x00\x00\x00\x00') is allowed for gen2 longitudinal to keep eyesight disabled
    bool is_tester_present = (GET_BYTES(msg, 0, 4) == 0x00803E02U) && (GET_BYTES(msg, 4, 4) == 0x0U);

    // reading ES button data by identifier (b'\x03\x22\x11\x30\x00\x00\x00\x00') is also allowed (DID 0x1130)
    bool is_button_rdbi = (GET_BYTES(msg, 0, 4) == 0x30112203U) && (GET_BYTES(msg, 4, 4) == 0x0U);

    violation |= !(is_tester_present || is_button_rdbi);
  }

  if (msg->addr == MSG_SUBARU_Dashlights) {
    violation |= !subaru_stop_start_button;
    violation |= msg->bus != (subaru_gen2 ? SUBARU_ALT_BUS : SUBARU_MAIN_BUS);
    violation |= !GET_BIT(msg, 54U);
    violation |= subaru_get_checksum(msg) != subaru_compute_checksum(msg);
  }

  if (msg->addr == MSG_SUBARU_ES_LKAS_Alert) {
    violation |= subaru_get_checksum(msg) != subaru_compute_checksum(msg);
  }

  if (msg->addr == MSG_SUBARU_Cruise_Buttons) {
    violation |= !subaru_redneck_cruise;
    violation |= msg->bus != SUBARU_MAIN_BUS;
    violation |= subaru_get_checksum(msg) != subaru_compute_checksum(msg);
  }

  if (msg->addr == 0x6BBU) {
    violation |= !subaru_avh_tx(msg);
  }

  if (violation){
    tx = false;
  }
  return tx;
}

static safety_config subaru_init(uint16_t param) {
  static const CanMsg SUBARU_LEGACY_AVH_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_ALT_BUS, MSG_SUBARU_ES_LKAS_ANGLE)
    SUBARU_COMMON_TX_MSGS(SUBARU_ALT_BUS)
    SUBARU_STOP_START_TX_MSGS(SUBARU_ALT_BUS)
    {0x6BBU, SUBARU_ALT_BUS, 8, .check_relay = false},
  };
  static const CanMsg SUBARU_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_MAIN_BUS, MSG_SUBARU_ES_LKAS)
    SUBARU_COMMON_TX_MSGS(SUBARU_MAIN_BUS)
  };

  static const CanMsg SUBARU_REDNECK_TX_MSGS_CONFIG[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_MAIN_BUS, MSG_SUBARU_ES_LKAS)
    SUBARU_COMMON_TX_MSGS(SUBARU_MAIN_BUS)
    SUBARU_REDNECK_TX_MSGS()
  };

  static const CanMsg SUBARU_REDNECK_STOP_AND_GO_TX_MSGS_CONFIG[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_MAIN_BUS, MSG_SUBARU_ES_LKAS)
    SUBARU_COMMON_TX_MSGS(SUBARU_MAIN_BUS)
    SUBARU_REDNECK_TX_MSGS()
    SUBARU_STOP_AND_GO_ADDITIONAL_TX_MSGS()
  };

  static const CanMsg SUBARU_LONG_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_MAIN_BUS, MSG_SUBARU_ES_LKAS)
    SUBARU_COMMON_LONG_TX_MSGS(SUBARU_MAIN_BUS)
  };

  static const CanMsg SUBARU_GEN2_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_ALT_BUS, MSG_SUBARU_ES_LKAS)
    SUBARU_COMMON_TX_MSGS(SUBARU_ALT_BUS)
  };

  static const CanMsg SUBARU_GEN2_LONG_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_ALT_BUS, MSG_SUBARU_ES_LKAS)
    SUBARU_COMMON_LONG_TX_MSGS(SUBARU_ALT_BUS)
    SUBARU_GEN2_LONG_ADDITIONAL_TX_MSGS()
  };

  static const CanMsg SUBARU_STOP_AND_GO_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_MAIN_BUS, MSG_SUBARU_ES_LKAS)
    SUBARU_COMMON_TX_MSGS(SUBARU_MAIN_BUS)
    SUBARU_STOP_AND_GO_ADDITIONAL_TX_MSGS()
  };

  static const CanMsg SUBARU_LKAS_ANGLE_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_MAIN_BUS, MSG_SUBARU_ES_LKAS_ANGLE)
    SUBARU_COMMON_TX_MSGS(SUBARU_MAIN_BUS)
  };

  static const CanMsg SUBARU_GEN2_LKAS_ANGLE_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_ALT_BUS, MSG_SUBARU_ES_LKAS_ANGLE)
    SUBARU_COMMON_TX_MSGS(SUBARU_ALT_BUS)
  };

  static const CanMsg SUBARU_ASCENT_LKAS_ALERT_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_ALT_BUS, MSG_SUBARU_ES_LKAS_ANGLE)
    SUBARU_COMMON_TX_MSGS(SUBARU_ALT_BUS)
    {MSG_SUBARU_ES_LKAS_Alert, SUBARU_MAIN_BUS, 8, .check_relay = true},
  };

  static const CanMsg SUBARU_GEN2_LKAS_ANGLE_STOP_START_TX_MSGS[] = {
    SUBARU_BASE_TX_MSGS(SUBARU_ALT_BUS, MSG_SUBARU_ES_LKAS_ANGLE)
    SUBARU_COMMON_TX_MSGS(SUBARU_ALT_BUS)
    SUBARU_STOP_START_TX_MSGS(SUBARU_ALT_BUS)
  };

  static const CanMsg SUBARU_D_PLATFORM_ANGLE_MAIN_TX_MSGS[] = {
    SUBARU_D_PLATFORM_ANGLE_TX_MSGS(SUBARU_MAIN_BUS)
    SUBARU_COMMON_TX_MSGS(SUBARU_ALT_BUS)
  };

  static const CanMsg SUBARU_D_PLATFORM_ANGLE_STOP_START_MAIN_TX_MSGS[] = {
    SUBARU_D_PLATFORM_ANGLE_TX_MSGS(SUBARU_MAIN_BUS)
    SUBARU_COMMON_TX_MSGS(SUBARU_ALT_BUS)
    SUBARU_STOP_START_TX_MSGS(SUBARU_ALT_BUS)
  };

  static const CanMsg SUBARU_D_PLATFORM_ANGLE_CAMERA_TX_MSGS[] = {
    SUBARU_D_PLATFORM_ANGLE_TX_MSGS(SUBARU_CAM_BUS)
    SUBARU_COMMON_TX_MSGS(SUBARU_ALT_BUS)
  };

  static RxCheck subaru_rx_checks[] = {
    SUBARU_COMMON_RX_CHECKS(SUBARU_MAIN_BUS)
  };

  static RxCheck subaru_gen2_rx_checks[] = {
    SUBARU_COMMON_RX_CHECKS(SUBARU_ALT_BUS)
  };

  static RxCheck subaru_lkas_angle_rx_checks[] = {
    SUBARU_LKAS_ANGLE_RX_CHECKS(SUBARU_MAIN_BUS, SUBARU_CAM_BUS)
  };

  static RxCheck subaru_gen2_lkas_angle_rx_checks[] = {
    SUBARU_LKAS_ANGLE_RX_CHECKS(SUBARU_ALT_BUS, SUBARU_ALT_BUS)
  };

  static RxCheck subaru_d_platform_angle_rx_checks[] = {
    SUBARU_D_PLATFORM_ANGLE_RX_CHECKS()
  };

  const uint16_t SUBARU_PARAM_GEN2 = 1;

  subaru_gen2 = GET_FLAG(param, SUBARU_PARAM_GEN2);

  const uint16_t SUBARU_PARAM_STOP_AND_GO = 8;
  subaru_stop_and_go = GET_FLAG(param, SUBARU_PARAM_STOP_AND_GO);

  const uint16_t SUBARU_PARAM_LKAS_ANGLE = 16;
  subaru_lkas_angle = GET_FLAG(param, SUBARU_PARAM_LKAS_ANGLE);

  const uint16_t SUBARU_PARAM_D_PLATFORM = 32;
  subaru_d_platform = GET_FLAG(param, SUBARU_PARAM_D_PLATFORM);

  const uint16_t SUBARU_PARAM_D_PLATFORM_CAMERA = 64;
  const bool subaru_d_platform_camera = GET_FLAG(param, SUBARU_PARAM_D_PLATFORM_CAMERA);

  const uint16_t SUBARU_PARAM_FIXED_ANGLE_LIMITS = 128;
  subaru_fixed_angle_limits = GET_FLAG(param, SUBARU_PARAM_FIXED_ANGLE_LIMITS);

  const uint16_t SUBARU_PARAM_STOP_START_BUTTON = 256;
  subaru_stop_start_button = GET_FLAG(param, SUBARU_PARAM_STOP_START_BUTTON);

  const uint16_t SUBARU_PARAM_REDNECK_CRUISE = 512;
  subaru_redneck_cruise = GET_FLAG(param, SUBARU_PARAM_REDNECK_CRUISE);

  const uint16_t SUBARU_PARAM_ASCENT_LKAS_ALERT = 2048;
  const bool subaru_ascent_lkas_alert = GET_FLAG(param, SUBARU_PARAM_ASCENT_LKAS_ALERT);

  const uint16_t SUBARU_PARAM_ASCENT_LONG_THROTTLE = 4096;
  subaru_ascent_long_throttle = GET_FLAG(param, SUBARU_PARAM_ASCENT_LONG_THROTTLE);

#ifdef ALLOW_DEBUG
  const uint16_t SUBARU_PARAM_LONGITUDINAL = 2;
  subaru_longitudinal = GET_FLAG(param, SUBARU_PARAM_LONGITUDINAL);
#endif

  safety_config ret;
  if (subaru_lkas_angle) {
    ret = subaru_d_platform ? (subaru_stop_start_button ? BUILD_SAFETY_CFG(subaru_d_platform_angle_rx_checks, SUBARU_D_PLATFORM_ANGLE_STOP_START_MAIN_TX_MSGS) : \
                              (subaru_d_platform_camera ? BUILD_SAFETY_CFG(subaru_d_platform_angle_rx_checks, SUBARU_D_PLATFORM_ANGLE_CAMERA_TX_MSGS) : \
                              BUILD_SAFETY_CFG(subaru_d_platform_angle_rx_checks, SUBARU_D_PLATFORM_ANGLE_MAIN_TX_MSGS))) : \
          subaru_gen2 ? (subaru_ascent_lkas_alert ? BUILD_SAFETY_CFG(subaru_gen2_lkas_angle_rx_checks, SUBARU_ASCENT_LKAS_ALERT_TX_MSGS) : \
                        subaru_stop_start_button ? BUILD_SAFETY_CFG(subaru_gen2_lkas_angle_rx_checks, SUBARU_GEN2_LKAS_ANGLE_STOP_START_TX_MSGS) : \
                         BUILD_SAFETY_CFG(subaru_gen2_lkas_angle_rx_checks, SUBARU_GEN2_LKAS_ANGLE_TX_MSGS)) : \
                        BUILD_SAFETY_CFG(subaru_lkas_angle_rx_checks, SUBARU_LKAS_ANGLE_TX_MSGS);
  } else if (subaru_gen2) {
    ret = subaru_longitudinal ? BUILD_SAFETY_CFG(subaru_gen2_rx_checks, SUBARU_GEN2_LONG_TX_MSGS) : \
                                BUILD_SAFETY_CFG(subaru_gen2_rx_checks, SUBARU_GEN2_TX_MSGS);
  } else {
    ret = subaru_redneck_cruise ? (subaru_stop_and_go ? BUILD_SAFETY_CFG(subaru_rx_checks, SUBARU_REDNECK_STOP_AND_GO_TX_MSGS_CONFIG) : \
                                   BUILD_SAFETY_CFG(subaru_rx_checks, SUBARU_REDNECK_TX_MSGS_CONFIG)) : \
          subaru_longitudinal ? BUILD_SAFETY_CFG(subaru_rx_checks, SUBARU_LONG_TX_MSGS) : \
          subaru_stop_and_go  ? BUILD_SAFETY_CFG(subaru_rx_checks, SUBARU_STOP_AND_GO_TX_MSGS) : \
                                BUILD_SAFETY_CFG(subaru_rx_checks, SUBARU_TX_MSGS);
  }
  bool avh_enabled = false;
#ifdef ALLOW_DEBUG
  avh_enabled = GET_FLAG(param, 1024U) && subaru_gen2 && subaru_lkas_angle && subaru_fixed_angle_limits &&
                subaru_stop_start_button && !subaru_d_platform && !GET_FLAG(param, 2U) && !subaru_redneck_cruise;
#endif
  subaru_avh_init(avh_enabled);
  if (avh_enabled) {
    ret = BUILD_SAFETY_CFG(subaru_gen2_lkas_angle_rx_checks, SUBARU_LEGACY_AVH_TX_MSGS);
  }
  return ret;
}

const safety_hooks subaru_hooks = {
  .init = subaru_init,
  .rx = subaru_rx_hook,
  .rx_all = subaru_avh_rx,
  .tx = subaru_tx_hook,
  .get_counter = subaru_get_counter,
  .get_checksum = subaru_get_checksum,
  .compute_checksum = subaru_compute_checksum,
};
