#pragma once

#include "opendbc/safety/declarations.h"
#include "opendbc/safety/modes/ford_param.h"
#include "opendbc/safety/modes/ford_aol.h"


// Safety-relevant CAN messages for Ford vehicles.
#define FORD_EngBrakeData          0x165U   // RX from PCM, for driver brake pedal and cruise state
#define FORD_EngVehicleSpThrottle  0x204U   // RX from PCM, for driver throttle input
#define FORD_DesiredTorqBrk        0x213U   // RX from ABS, for standstill state
#define FORD_BrakeSysFeatures      0x415U   // RX from ABS, for vehicle speed
#define FORD_EngVehicleSpThrottle2 0x202U   // RX from PCM, for second vehicle speed
#define FORD_Yaw_Data_FD1          0x91U    // RX from RCM, for yaw rate
#define FORD_SteeringPinion_Data   0x7EU    // RX from PSCM, optional angle_meas source (BluePilot STEER_ANGLE_CURVATURE)
#define FORD_Steering_Data_FD1     0x083U   // TX by OP, various driver switches and LKAS/CC buttons
#define FORD_ACCDATA               0x186U   // TX by OP, ACC controls
#define FORD_ACCDATA_3             0x18AU   // TX by OP, ACC/TJA user interface
#define FORD_Lane_Assist_Data1     0x3CAU   // TX by OP, Lane Keep Assist
#define FORD_Lane_Assist_Data3     0x3CCU   // RX from PSCM, LKA availability
#define FORD_LateralMotionControl  0x3D3U   // TX by OP, Lateral Control message
#define FORD_LateralMotionControl2 0x3D6U   // TX by OP, alternate Lateral Control message
#define FORD_IPMA_Data             0x3D8U   // TX by OP, IPMA and LKAS user interface

// CAN bus numbers.
#define FORD_MAIN_BUS 0U
#define FORD_CAM_BUS  2U

static uint8_t ford_get_counter(const CANPacket_t *msg) {
  uint8_t cnt = 0;
  if (msg->addr == FORD_BrakeSysFeatures) {
    // Signal: VehVActlBrk_No_Cnt
    cnt = (msg->data[2] >> 2) & 0xFU;
  }
  if (msg->addr == FORD_Yaw_Data_FD1) {
    // Signal: VehRollYaw_No_Cnt
    cnt = msg->data[5];
  }
  if (msg->addr == FORD_SteeringPinion_Data) {
    // Signal: StePinAn_No_Cnt (47|4@0+)
    cnt = (msg->data[5] >> 4) & 0xFU;
  }
  return cnt;
}

static uint32_t ford_get_checksum(const CANPacket_t *msg) {
  uint8_t chksum = 0;
  if (msg->addr == FORD_BrakeSysFeatures) {
    // Signal: VehVActlBrk_No_Cs
    chksum = msg->data[3];
  }
  if (msg->addr == FORD_Yaw_Data_FD1) {
    // Signal: VehRollYawW_No_Cs
    chksum = msg->data[4];
  }
  return chksum;
}

static uint32_t ford_compute_checksum(const CANPacket_t *msg) {
  uint8_t chksum = 0;
  if (msg->addr == FORD_BrakeSysFeatures) {
    chksum += msg->data[0] + msg->data[1];  // Veh_V_ActlBrk
    chksum += msg->data[2] >> 6;                    // VehVActlBrk_D_Qf
    chksum += (msg->data[2] >> 2) & 0xFU;           // VehVActlBrk_No_Cnt
    chksum = 0xFFU - chksum;
  }
  if (msg->addr == FORD_Yaw_Data_FD1) {
    chksum += msg->data[0] + msg->data[1];  // VehRol_W_Actl
    chksum += msg->data[2] + msg->data[3];  // VehYaw_W_Actl
    chksum += msg->data[5];                         // VehRollYaw_No_Cnt
    chksum += msg->data[6] >> 6;                    // VehRolWActl_D_Qf
    chksum += (msg->data[6] >> 4) & 0x3U;           // VehYawWActl_D_Qf
    chksum = 0xFFU - chksum;
  }
  return chksum;
}

static bool ford_get_quality_flag_valid(const CANPacket_t *msg) {
  bool valid = false;
  if (msg->addr == FORD_BrakeSysFeatures) {
    valid = (msg->data[2] >> 6) == 0x3U;           // VehVActlBrk_D_Qf
  }
  if (msg->addr == FORD_EngVehicleSpThrottle2) {
    valid = ((msg->data[4] >> 5) & 0x3U) == 0x3U;  // VehVActlEng_D_Qf
  }
  if (msg->addr == FORD_Yaw_Data_FD1) {
    valid = ((msg->data[6] >> 4) & 0x3U) == 0x3U;  // VehYawWActl_D_Qf
  }
  if (msg->addr == FORD_SteeringPinion_Data) {
    valid = ((msg->data[5] >> 2) & 0x3U) == 0x3U;  // StePinCompAnEst_D_Qf (3=OK)
  }
  return valid;
}

#define FORD_INACTIVE_CURVATURE 1000U
#define FORD_INACTIVE_CURVATURE_RATE 4096U
#define FORD_INACTIVE_PATH_OFFSET 512U
#define FORD_INACTIVE_PATH_ANGLE 1000U

#define FORD_CANFD_INACTIVE_CURVATURE_RATE 1024U

static const CurvatureSteeringLimits FORD_STEERING_LIMITS = {
  .max_curvature = 1000,              // 0.02 rad/m * curvature_to_can
  .curvature_to_can = 50000,          // CAN units per rad/m
  .frequency = 20,                    // Hz
  .max_curvature_error = 100,         // 0.002 rad/m * curvature_to_can
  .curvature_error_min_speed = 10.0,  // m/s
  .max_steer_power = 0,               // disabled, Ford has no steed power signal
};

// BluePilot: the Ford 4-signal lateral checks, pinion angle_meas source and angle-mode state, from the
// bluepilot_lateral library. Lateral follows StarPilot's lateral permission, which includes AOL.
#define BP_HOST_LAT_ALLOWED() lateral_controls_allowed()
// cppcheck-suppress misra-c2012-20.1; needs the Ford constants defined above
#include "opendbc/bluepilot_lateral/safety/ford.h"

// Transit 0x3CA has 0.000005 rad/m per raw step and runs at 33 Hz. Keep its
// command history separate from the inactive 0x3D3 heartbeat at 20 Hz.
static const CurvatureSteeringLimits FORD_LKA_STEERING_LIMITS = {
  .max_curvature = 2046,
  .curvature_to_can = 200000,
  .frequency = 33,
  .max_curvature_error = 400,
  .curvature_error_min_speed = 10.0,
  .max_steer_power = 0,
};

// The physical cancel/resume switch passes through on stock-ACC profiles
static bool ford_stock_switch = false;
static bool ford_cancel_resume_button = false;

static bool ford_lka_steering = false;
static bool ford_lka_available = false;
static uint32_t ford_lka_last_us = 0U;
static uint32_t ford_lka_speed_last_us = 0U;
static uint32_t ford_lka_speed2_last_us = 0U;
static uint32_t ford_lka_yaw_last_us = 0U;
static bool ford_lka_speed_seen = false;
static bool ford_lka_speed2_seen = false;
static bool ford_lka_yaw_seen = false;
static CurvatureSteeringState ford_lka_curvature_state;
static int ford_lka_angle_last = 2048;

static bool ford_lka_curvature_checks(int desired_curvature, bool active) {
  CurvatureSteeringState previous = curvature_state;
  curvature_state = ford_lka_curvature_state;
  bool violation = steer_curvature_cmd_checks(desired_curvature, 0, active, FORD_LKA_STEERING_LIMITS);
  ford_lka_curvature_state = curvature_state;
  curvature_state = previous;
  return violation;
}

static void ford_rx_hook(const CANPacket_t *msg) {
  ford_aol_rx(msg);
  // Update in motion state from standstill signal
  if (msg_matches(msg, FORD_DesiredTorqBrk, FORD_MAIN_BUS)) {
    // Signal: VehStop_D_Stat
    vehicle_moving = ((msg->data[3] >> 3) & 0x3U) != 1U;
  }

  // Update vehicle speed
  if (msg_matches(msg, FORD_BrakeSysFeatures, FORD_MAIN_BUS)) {
    // Signal: Veh_V_ActlBrk
    UPDATE_VEHICLE_SPEED(((msg->data[0] << 8) | msg->data[1]) * 0.01 * KPH_TO_MS);
    if (ford_lka_steering) { ford_lka_speed_seen = true; ford_lka_speed_last_us = microsecond_timer_get(); }
  }
  if (msg_matches(msg, FORD_Lane_Assist_Data3, FORD_MAIN_BUS) && ford_lka_steering) {
    ford_lka_available = (((msg->data[0] >> 4) & 0x3U) == 3U) && ((msg->data[0] & 0x40U) == 0U);
    ford_lka_last_us = microsecond_timer_get();
  }

  // Check vehicle speed against a second source
  if (msg_matches(msg, FORD_EngVehicleSpThrottle2, FORD_MAIN_BUS)) {
    // Disable controls if speeds from ABS and PCM ECUs are too far apart.
    // Signal: Veh_V_ActlEng
    float filtered_pcm_speed = ((msg->data[6] << 8) | msg->data[7]) * 0.01 * KPH_TO_MS;
    UPDATE_VEHICLE_SPEED_2(filtered_pcm_speed);
    if (ford_lka_steering) { ford_lka_speed2_seen = true; ford_lka_speed2_last_us = microsecond_timer_get(); }
  }

  // Update vehicle yaw rate
  if (msg_matches(msg, FORD_Yaw_Data_FD1, FORD_MAIN_BUS) && !ford_bp_pinion_curvature) {  // BluePilot: pinion source replaces yaw
    // FIXME: safety can receive yaw before new vehicle speed, it should recompute meas on either received
    // Signal: VehYaw_W_Actl
    // TODO: we should use the speed which results in the closest angle measurement to the desired angle
    float ford_yaw_rate = (((msg->data[2] << 8U) | msg->data[3]) * 0.0002) - 6.5;
    float current_curvature = ford_yaw_rate / SAFETY_MAX(vehicle_speed.values[0] / VEHICLE_SPEED_FACTOR, 0.1);
    // convert current curvature into units on CAN for comparison with desired curvature
    update_sample(&curvature_state.meas, ROUND(current_curvature * FORD_STEERING_LIMITS.curvature_to_can));
    if (ford_lka_steering) {
      update_sample(&ford_lka_curvature_state.meas, ROUND(current_curvature * FORD_LKA_STEERING_LIMITS.curvature_to_can));
      ford_lka_yaw_seen = true;
      ford_lka_yaw_last_us = microsecond_timer_get();
    }
  }

  // Update gas pedal
  if (msg_matches(msg, FORD_EngVehicleSpThrottle, FORD_MAIN_BUS)) {
    // Pedal position: (0.1 * val) in percent
    // Signal: ApedPos_Pc_ActlArb
    gas_pressed = (((msg->data[0] & 0x03U) << 8) | msg->data[1]) > 0U;
  }

  // Update brake pedal and cruise state
  if (msg_matches(msg, FORD_EngBrakeData, FORD_MAIN_BUS)) {
    // Signal: BpedDrvAppl_D_Actl
    brake_pressed = ((msg->data[0] >> 4) & 0x3U) == 2U;

    // Signal: CcStat_D_Actl
    unsigned int cruise_state = msg->data[1] & 0x07U;
    bool cruise_engaged = (cruise_state == 4U) || (cruise_state == 5U);
    const bool cruise_rising = cruise_engaged && !cruise_engaged_prev;
    pcm_cruise_check(cruise_engaged);
    ford_aol_full_engagement(cruise_rising);
    if (ford_stock_switch) {
      acc_main_on = (cruise_state == 3U) || cruise_engaged;
    }
  }
  if (ford_stock_switch && msg_matches(msg, FORD_Steering_Data_FD1, FORD_MAIN_BUS)) {
    // Physical combined cancel/resume switch, not the outgoing resume signal.
    ford_cancel_resume_button = (msg->data[2] & 0x20U) != 0U;
  }
  ford_bp_rx_hook(msg);  // BluePilot: pinion angle_meas source
}

static bool ford_tx_hook(const CANPacket_t *msg) {
  const LongitudinalLimits FORD_LONG_LIMITS = {
    // acceleration cmd limits (used for brakes)
    // Signal: AccBrkTot_A_Rq
    .max_accel = 5641,       //  1.9999 m/s^s
    .min_accel = 4231,       // -3.4991 m/s^2
    .inactive_accel = 5128,  // -0.0008 m/s^2

    // gas cmd limits
    // Signal: AccPrpl_A_Rq & AccPrpl_A_Pred
    .max_gas = 700,          //  2.0 m/s^2
    .min_gas = 450,          // -0.5 m/s^2
    .inactive_gas = 0,       // -5.0 m/s^2
  };

  bool tx = true;

  if ((msg->addr == FORD_ACCDATA_3) || (msg->addr == FORD_IPMA_Data)) {
    tx = ford_aol_display_tx_allowed(msg);
  }

  // Safety check for ACCDATA accel and brake requests
  if (msg->addr == FORD_ACCDATA) {
    // Signal: AccPrpl_A_Rq
    int gas = ((msg->data[6] & 0x3U) << 8) | msg->data[7];
    // Signal: AccPrpl_A_Pred
    int gas_pred = ((msg->data[2] & 0x3U) << 8) | msg->data[3];
    // Signal: AccBrkTot_A_Rq
    int accel = ((msg->data[0] & 0x1FU) << 8) | msg->data[1];
    // Signal: CmbbDeny_B_Actl
    bool cmbb_deny = (msg->data[4] >> 5) & 1U;

    // Signal: AccBrkPrchg_B_Rq & AccBrkDecel_B_Rq
    bool brake_actuation = (msg->data[6] & 0xC0U) != 0U;

    bool violation = false;
    violation |= longitudinal_accel_checks(accel, FORD_LONG_LIMITS);
    violation |= longitudinal_gas_checks(gas, FORD_LONG_LIMITS);
    violation |= longitudinal_gas_checks(gas_pred, FORD_LONG_LIMITS);

    // Safety check for stock AEB
    violation |= cmbb_deny;  // do not prevent stock AEB actuation

    violation |= !get_longitudinal_allowed() && brake_actuation;

    if (violation) {
      tx = false;
    }
  }

  // Safety check for Steering_Data_FD1 button signals
  // Note: Many other signals in this message are not relevant to safety (e.g. blinkers, wiper switches, high beam)
  // which we passthru in OP.
  if (msg->addr == FORD_Steering_Data_FD1) {
    // Violation if resume button is pressed while controls not allowed, or
    // if cancel button is pressed when cruise isn't engaged.
    bool violation = false;
    violation |= ((msg->data[1] >> 0) & 1U) && !cruise_engaged_prev;   // Signal: CcAslButtnCnclPress (cancel)
    // Only an actual stock-cruise driver switch can request resume while OP is
    // inactive. Required RX health guards the original level-based permission.
    const bool stock_resume_from_driver = ford_stock_switch && acc_main_on && ford_cancel_resume_button && aol_rx_healthy();
    violation |= ((msg->data[3] >> 1) & 1U) && !(controls_allowed || stock_resume_from_driver);  // Signal: CcAsllButtnResPress (resume)

    if (violation) {
      tx = false;
    }
  }

  // Safety check for Lane_Assist_Data1 action
  if (msg->addr == FORD_Lane_Assist_Data1) {
    // Do not allow steering using Lane_Assist_Data1 (Lane-Departure Aid).
    // This message must be sent for Lane Centering to work, and can include
    // values such as the steering angle or lane curvature for debugging,
    // but the action (LkaActvStats_D2_Req) must be set to zero.
    unsigned int action = msg->data[0] >> 5;
    if (ford_lka_steering) {
      const int angle = ((msg->data[2] & 0xFU) << 8) | msg->data[3];
      const int curvature = (msg->data[1] << 4) | (msg->data[2] >> 4);
      const uint32_t now = microsecond_timer_get();
      const bool source_current = ford_lka_available && ford_lka_speed_seen && ford_lka_speed2_seen && ford_lka_yaw_seen &&
                                  (cruise_engaged_prev || (ford_aol_enabled && (ford_aol_permission_mask() != 0U))) &&
                                  safety_get_ts_elapsed(now, ford_lka_last_us) <= 100000U &&
                                  safety_get_ts_elapsed(now, ford_lka_speed_last_us) <= 100000U &&
                                  safety_get_ts_elapsed(now, ford_lka_speed2_last_us) <= 100000U &&
                                  safety_get_ts_elapsed(now, ford_lka_yaw_last_us) <= 100000U;
      const bool active = (action == 2U) || (action == 4U);
      const bool direction_valid = ((action == 2U) && (angle >= 2048)) || ((action == 4U) && (angle < 2048));
      const bool payload_valid = (angle >= 24) && (angle <= 4072) && (curvature >= 2) && (curvature <= 4094) &&
                                 (SAFETY_ABS(angle - ford_lka_angle_last) <= 350) &&
                                 ((msg->data[4] & 0x60U) == 0U) && ((msg->data[0] & 0x1FU) == 3U);
      if (active) {
        if (!lateral_controls_allowed() || !source_current || !direction_valid || !payload_valid) {
          tx = false;
        } else if (ford_lka_curvature_checks(curvature - 2048, true)) {
          tx = false;
        } else {
          // Valid active request keeps its transmit decision.
        }
      } else if ((action != 0U) || (angle != 2048) || (curvature != 2048)) {
        tx = false;
      } else {
        // Neutral request needs no additional steering check.
      }
      if (tx) {
        ford_lka_angle_last = active ? angle : 2048;
        if (!active) { (void)ford_lka_curvature_checks(0, false); }
      } else {
        ford_lka_angle_last = 2048;
        ford_lka_curvature_state.desired_last = 0;
      }
    } else if (action != 0U) {
      tx = false;
    } else {
      // Other Ford profiles retain their neutral status handling.
    }
    ford_bp_lka_tx(msg);  // BluePilot: angle-mode state from the frame's unused bits
  }

  // Safety check for LateralMotionControl action
  if (msg->addr == FORD_LateralMotionControl) {
    // Signal: LatCtl_D_Rq
    bool steer_control_enabled = ((msg->data[4] >> 2) & 0x7U) != 0U;
    unsigned int raw_curvature = (msg->data[0] << 3) | (msg->data[1] >> 5);
    unsigned int raw_curvature_rate = ((msg->data[1] & 0x1FU) << 8) | msg->data[2];
    unsigned int raw_path_angle = (msg->data[3] << 3) | (msg->data[4] >> 5);
    unsigned int raw_path_offset = (msg->data[5] << 2) | (msg->data[6] >> 6);

    bool violation = false;
    if (ford_bp_lateral) {  // BluePilot: 4-signal lateral
      violation = ford_bp_lmc_tx(false, raw_curvature, raw_curvature_rate, raw_path_offset, raw_path_angle, steer_control_enabled);
    } else {
      // These signals are not yet tested with the current safety limits
      violation = (raw_curvature_rate != FORD_INACTIVE_CURVATURE_RATE) || (raw_path_angle != FORD_INACTIVE_PATH_ANGLE) || (raw_path_offset != FORD_INACTIVE_PATH_OFFSET);

      // Check angle error and steer_control_enabled
      int desired_curvature = raw_curvature - FORD_INACTIVE_CURVATURE;  // /FORD_STEERING_LIMITS.curvature_to_can to get real curvature
      violation |= steer_curvature_cmd_checks(desired_curvature, 0, steer_control_enabled, FORD_STEERING_LIMITS);
    }
    // The Transit steers through Lane_Assist_Data1; its LateralMotionControl is an inactive heartbeat
    violation |= ford_lka_steering && steer_control_enabled;

    if (violation) {
      tx = false;
    }
  }

  // Safety check for LateralMotionControl2 action
  if (msg->addr == FORD_LateralMotionControl2) {
    // Signal: LatCtl_D2_Rq
    bool steer_control_enabled = ((msg->data[0] >> 4) & 0x7U) != 0U;
    unsigned int raw_curvature = (msg->data[2] << 3) | (msg->data[3] >> 5);
    unsigned int raw_curvature_rate = (msg->data[6] << 3) | (msg->data[7] >> 5);
    unsigned int raw_path_angle = ((msg->data[3] & 0x1FU) << 6) | (msg->data[4] >> 2);
    unsigned int raw_path_offset = ((msg->data[4] & 0x3U) << 8) | msg->data[5];

    bool violation = false;
    if (ford_bp_lateral) {  // BluePilot: 4-signal lateral
      violation = ford_bp_lmc_tx(true, raw_curvature, raw_curvature_rate, raw_path_offset, raw_path_angle, steer_control_enabled);
    } else {
      // These signals are not yet tested with the current safety limits
      violation = (raw_curvature_rate != FORD_CANFD_INACTIVE_CURVATURE_RATE) || (raw_path_angle != FORD_INACTIVE_PATH_ANGLE) || (raw_path_offset != FORD_INACTIVE_PATH_OFFSET);

      // Check angle error and steer_control_enabled
      int desired_curvature = raw_curvature - FORD_INACTIVE_CURVATURE;  // /FORD_STEERING_LIMITS.curvature_to_can to get real curvature
      violation |= steer_curvature_cmd_checks(desired_curvature, 0, steer_control_enabled, FORD_STEERING_LIMITS);
    }

    if (violation) {
      tx = false;
    }
  }

  ford_aol_tx(msg, tx);
  return tx;
}

static void ford_aol_bind_tx(safety_config *ret, CanMsg workspace[7]) {
  const bool bounded = (ret->tx_msgs != NULL) && (ret->tx_msgs_len > 0) && (ret->tx_msgs_len <= 7);
  if (bounded) {
    for (int i = 0; i < ret->tx_msgs_len; i++) {
      workspace[i] = ret->tx_msgs[i];
      const unsigned int addr = workspace[i].addr;
      if ((addr == ford_aol_replacement()) || (addr == 0x3D8U) || (addr == 0x18AU)) {
        workspace[i].disable_static_blocking = true;
      }
    }
    ret->tx_msgs = workspace;
  } else {
    ret->tx_msgs = NULL;
    ret->tx_msgs_len = 0;
    ford_aol_reset();
  }
}

// BluePilot: the selected RX checks plus the pinion message, enforced only while the pinion angle_meas source
// is on (ford_bp_init), so a pinion hiccup can't disable controls for users who never consume the message.
static void ford_bp_append_pinion_rx(safety_config *config, RxCheck workspace[11]) {
  if (config->rx_checks_len <= 10) {
    const int count = config->rx_checks_len;
    for (int i = 0; i < count; i++) {
      workspace[i] = config->rx_checks[i];
    }
    // StePinAn_No_Cs matches no Ford checksum pattern on real frames: integrity via counter (0-15) + quality flag + 100 Hz
    workspace[count] = (RxCheck){.msg = {{FORD_SteeringPinion_Data, 0, 8, 100U, .max_counter = 15U, .ignore_checksum = true}, {0}, {0}}};
    config->rx_checks = workspace;
    config->rx_checks_len = count + 1;
  } else {
    config->tx_msgs = NULL;
    config->tx_msgs_len = 0;
    ford_aol_reset();
  }
}

static safety_config ford_init(uint16_t configured_param) {
  static RxCheck ford_rx_workspace[11];
  const uint16_t param = ford_profile_word(configured_param) & ~256U;
  const bool manual = (configured_param & 256U) != 0U;
  // Manual variants are only defined for the exact registered Ford profiles.
  const bool manual_valid = !manual || ford_aol_param_valid(configured_param);
  // warning: quality flags are not yet checked in openpilot's CAN parser,
  // this may be the cause of blocked messages
  // Shared entries preserve all current checksum/counter/quality contracts.
  #define FORD_COMMON_RX_CHECKS \
    {.msg = {{FORD_BrakeSysFeatures, 0, 8, 50U, .max_counter = 15U}, { 0 }, { 0 }}}, \
    {.msg = {{FORD_EngVehicleSpThrottle2, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true}, { 0 }, { 0 }}}, \
    {.msg = {{FORD_Yaw_Data_FD1, 0, 8, 100U, .max_counter = 255U}, { 0 }, { 0 }}}, \
    {.msg = {{FORD_EngBrakeData, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
    {.msg = {{FORD_EngVehicleSpThrottle, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
    {.msg = {{FORD_DesiredTorqBrk, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  static RxCheck ford_rx_checks[] = { FORD_COMMON_RX_CHECKS };
  static RxCheck ford_lka_rx_checks[] = {
    FORD_COMMON_RX_CHECKS
    {.msg = {{FORD_Lane_Assist_Data3, 0, 8, 30U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };
  static RxCheck ford_stock_rx_checks[] = {
    FORD_COMMON_RX_CHECKS
    {.msg = {{FORD_Steering_Data_FD1, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };
  static RxCheck ford_stock_lka_rx_checks[] = {
    FORD_COMMON_RX_CHECKS
    {.msg = {{FORD_Lane_Assist_Data3, 0, 8, 30U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{FORD_Steering_Data_FD1, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

  #define FORD_COMMON_TX_MSGS \
    {FORD_Steering_Data_FD1, 0, 8, .check_relay = false}, \
    {FORD_Steering_Data_FD1, 2, 8, .check_relay = false}, \
    {FORD_ACCDATA_3, 0, 8, .check_relay = true},          \
    {FORD_Lane_Assist_Data1, 0, 8, .check_relay = true},  \
    {FORD_IPMA_Data, 0, 8, .check_relay = true},          \

  static const CanMsg FORD_CANFD_LONG_TX_MSGS[] = {
    FORD_COMMON_TX_MSGS
    {FORD_ACCDATA, 0, 8, .check_relay = true},
    {FORD_LateralMotionControl2, 0, 8, .check_relay = true},
  };

  static const CanMsg FORD_CANFD_STOCK_TX_MSGS[] = {
    FORD_COMMON_TX_MSGS
    {FORD_LateralMotionControl2, 0, 8, .check_relay = true},
  };

  static const CanMsg FORD_LONG_TX_MSGS[] = {
    FORD_COMMON_TX_MSGS
    {FORD_ACCDATA, 0, 8, .check_relay = true},
    {FORD_LateralMotionControl, 0, 8, .check_relay = true},
  };
  static const CanMsg FORD_STOCK_TX_MSGS[] = {
    FORD_COMMON_TX_MSGS
    {FORD_LateralMotionControl, 0, 8, .check_relay = true},
  };

  const uint16_t FORD_PARAM_CANFD = 2;
  const uint16_t FORD_PARAM_LKA_STEERING = 4;
  const uint16_t FORD_PARAM_NEW_PORT = 8;
  ford_stock_switch = (((unsigned int)alternative_experience == 0U) || (((unsigned int)alternative_experience == 32U) && ford_aol_param_valid(param))) &&
                      ((param == 0U) || (param == 2U) || (param == 8U) || (param == 10U) || (param == 12U));
  ford_cancel_resume_button = false;
  const bool ford_canfd = GET_FLAG(param, FORD_PARAM_CANFD);
  ford_lka_steering = GET_FLAG(param, FORD_PARAM_LKA_STEERING);
  const bool ford_new_port = GET_FLAG(param, FORD_PARAM_NEW_PORT);
  ford_lka_available = false;
  ford_lka_last_us = 0U;
  ford_lka_speed_last_us = 0U;
  ford_lka_speed2_last_us = 0U;
  ford_lka_yaw_last_us = 0U;
  ford_lka_speed_seen = false;
  ford_lka_speed2_seen = false;
  ford_lka_yaw_seen = false;
  ford_lka_curvature_state = (CurvatureSteeringState){0};
  ford_lka_angle_last = 2048;

  safety_config ret;
  if (ford_canfd) {
    ret = BUILD_SAFETY_CFG(ford_rx_checks, FORD_CANFD_STOCK_TX_MSGS);
    bool long_profile = (param == 3U) || (param == 11U);
#ifdef ALLOW_DEBUG
    long_profile |= GET_FLAG(param, 1U);
#endif
    if (long_profile) {
      ret = BUILD_SAFETY_CFG(ford_rx_checks, FORD_CANFD_LONG_TX_MSGS);
    }
  } else {
    // Classic stock ACC (0) and the new ports send no ACCDATA unless openpilot long is set
    ret = (ford_new_port || (param == 0U)) ? BUILD_SAFETY_CFG(ford_rx_checks, FORD_STOCK_TX_MSGS) :
                                             BUILD_SAFETY_CFG(ford_rx_checks, FORD_LONG_TX_MSGS);
    if (ford_lka_steering) {
      SET_RX_CHECKS(ford_lka_rx_checks, ret);
    }
    if (ford_new_port && GET_FLAG(param, 1U)) {
      SET_TX_MSGS(FORD_LONG_TX_MSGS, ret);
    }
  }
  if (ford_stock_switch) {
    if (ford_lka_steering) {
      SET_RX_CHECKS(ford_stock_lka_rx_checks, ret);
    } else {
      SET_RX_CHECKS(ford_stock_rx_checks, ret);
    }
  }
  if (!manual_valid) {
    ret.tx_msgs = NULL;
    ret.tx_msgs_len = 0;
  }
  ford_aol_configure(configured_param);
  if (ford_aol_enabled) {
    static const RxCheck ford_aol_default_rx[] = {
      FORD_COMMON_RX_CHECKS
      {.msg = {{0x83, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x82, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x176, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    };
    static const RxCheck ford_aol_edge_rx[] = {
      FORD_COMMON_RX_CHECKS
      {.msg = {{0x83, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x82, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x230, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    };
    static const RxCheck ford_aol_mondeo_rx[] = {
      FORD_COMMON_RX_CHECKS
      {.msg = {{FORD_Lane_Assist_Data3, 0, 8, 30U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x83, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x82, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x5A, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    };
    static const RxCheck ford_aol_transit_rx[] = {
      FORD_COMMON_RX_CHECKS
      {.msg = {{0x83, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x82, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x176, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{FORD_Lane_Assist_Data3, 0, 8, 30U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    };
    _Static_assert(sizeof(ford_aol_default_rx) == (9U * sizeof(RxCheck)), "Ford default RX count");
    _Static_assert(sizeof(ford_aol_edge_rx) == (9U * sizeof(RxCheck)), "Ford Edge RX count");
    _Static_assert(sizeof(ford_aol_mondeo_rx) == (10U * sizeof(RxCheck)), "Ford Mondeo RX count");
    _Static_assert(sizeof(ford_aol_transit_rx) == (10U * sizeof(RxCheck)), "Ford Transit RX count");
    const uint16_t base = param & 0xFFFEU;
    const RxCheck *selected_rx = ford_aol_default_rx;
    int selected_len = 9;
    if (base == 8U) {
      selected_rx = ford_aol_edge_rx;
    } else if (base == 10U) {
      selected_rx = ford_aol_mondeo_rx;
      selected_len = 10;
    } else if ((base == 12U) || (base == 2U)) {
      selected_rx = ford_aol_transit_rx;
      selected_len = 10;
    } else {
    }
    for (int i = 0; i < 10; i++) {
      if (i < selected_len) {
        ford_rx_workspace[i] = selected_rx[i];
        const unsigned int addr = ford_rx_workspace[i].msg[0].addr;
        if (manual && ((addr == 0x176U) || (addr == 0x230U) || (addr == 0x5AU))) {
          ford_rx_workspace[i].msg[0].addr = 0x3C3U;
        }
      } else {
        ford_rx_workspace[i] = (RxCheck){0};
      }
    }
    ret.rx_checks = ford_rx_workspace;
    ret.rx_checks_len = selected_len;
    ret.disable_forwarding = false;
    static CanMsg ford_aol_tx_msgs[7];
    _Static_assert(sizeof(FORD_CANFD_STOCK_TX_MSGS) <= sizeof(ford_aol_tx_msgs), "Ford CAN FD stock TX capacity");
    _Static_assert(sizeof(FORD_STOCK_TX_MSGS) <= sizeof(ford_aol_tx_msgs), "Ford stock TX capacity");
    _Static_assert(sizeof(FORD_LONG_TX_MSGS) <= sizeof(ford_aol_tx_msgs), "Ford long TX capacity");
    _Static_assert(sizeof(FORD_CANFD_LONG_TX_MSGS) <= sizeof(ford_aol_tx_msgs), "Ford CAN FD long TX capacity");
    ford_aol_bind_tx(&ret, ford_aol_tx_msgs);
  }
  // BluePilot: the library's startup bits ride bits 9-14 (ford_param.h). The Transit steers through
  // Lane_Assist_Data1 alone and never takes them.
  const uint16_t bp_param = ford_lka_steering ? 0U : ((configured_param & FORD_BP_PARAM_MASK) >> FORD_BP_PARAM_SHIFT);
  if (ford_bp_init(bp_param)) {
    ford_bp_append_pinion_rx(&ret, ford_rx_workspace);
  }
  return ret;
}

const safety_hooks ford_hooks = {
  .fwd = ford_aol_fwd,
  .init = ford_init,
  .rx = ford_rx_hook,
  .tx = ford_tx_hook,
  .get_counter = ford_get_counter,
  .get_checksum = ford_get_checksum,
  .compute_checksum = ford_compute_checksum,
  .get_quality_flag_valid = ford_get_quality_flag_valid,
};
