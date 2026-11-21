#pragma once

#include "opendbc/safety/declarations.h"

// Volvo C1 (V40), safetyParam 2. Selected by volvo_dispatch.h.
// C1-specific addresses (V40). The V40 powertrain bus is bus 0 and its
// forward-camera bus is bus 2; bus 1 is unused by this port.
#define VOLVO_C1_BUTTONS           0x10U
#define VOLVO_C1_FSM_0             0x30U
#define VOLVO_C1_FSM_1             0xD0U
#define VOLVO_C1_PSCM_1            0x125U
#define VOLVO_C1_PEDAL_AND_BRAKE   0x55U
#define VOLVO_C1_SPEED             0x150U

#define VOLVO_C1_PT_BUS   0U  // powertrain bus
#define VOLVO_C1_CAM_BUS  2U  // forward-camera bus

#define VOLVO_C1_ANGLE_DEG_TO_CAN 22.753128f
#define VOLVO_C1_MAX_ANGLE_CAN 8189
#define VOLVO_C1_RELAY_ANGLE_TOLERANCE 2

static int volvo_c1_pscm_angle(const CANPacket_t *msg) {
  return (int)(((uint16_t)msg->data[5] << 8U) | msg->data[6]) - 32768;
}

static int volvo_c1_fsm_angle(const CANPacket_t *msg) {
  return (int)(((uint16_t)(msg->data[4] & 0x3FU) << 8U) | msg->data[5]) - 8192;
}

static uint8_t volvo_c1_fsm_checksum(const CANPacket_t *msg) {
  const uint16_t angle_raw = ((uint16_t)(msg->data[4] & 0x3FU) << 8U) | msg->data[5];
  const uint8_t direction = msg->data[7] & 0x3U;
  const uint8_t checksum_sum = (msg->data[3] + direction + angle_raw + (angle_raw >> 8U)) & 0xFFU;
  return checksum_sum ^ 0xFFU;
}

static const AngleSteeringLimits VOLVO_C1_ANGLE_STEERING_LIMITS = {
  .max_angle = VOLVO_C1_MAX_ANGLE_CAN,
  .angle_deg_to_can = VOLVO_C1_ANGLE_DEG_TO_CAN,
  .angle_rate_up_lookup = {
    {7.0f, 17.0f, 36.0f},
    {2.0f, 0.25f, 0.1f},
  },
  .angle_rate_down_lookup = {
    {7.0f, 17.0f, 36.0f},
    {2.0f, 0.25f, 0.1f},
  },
  .frequency = 50U,
};

// C1 retains its measured-angle EPS envelope; the shared angle API no longer supplies it.
static bool volvo_c1_angle_cmd_checks(int desired_angle, bool steer_control_enabled, const AngleSteeringLimits limits) {
  bool violation = false;

  if (lateral_controls_allowed() && steer_control_enabled) {
    // convert floating point angle rate limits to integers in the scale of the desired angle on CAN,
    // add 1 to not false trigger the violation. also fudge the speed by 1 m/s so rate limits are
    // always slightly above openpilot's in case we read an updated speed in between angle commands
    // TODO: this speed fudge can be much lower, look at data to determine the lowest reasonable offset
    const float fudged_speed = (vehicle_speed.min / VEHICLE_SPEED_FACTOR) - 1.;
    int delta_angle_up = (safety_interpolate(limits.angle_rate_up_lookup, fudged_speed) * limits.angle_deg_to_can) + 1.;
    int delta_angle_down = (safety_interpolate(limits.angle_rate_down_lookup, fudged_speed) * limits.angle_deg_to_can) + 1.;

    // allow down limits at zero since small floats from openpilot will be rounded to 0
    // TODO: openpilot should be cognizant of this and not send small floats
    int highest_desired_angle = desired_angle_last + ((desired_angle_last > 0) ? delta_angle_up : delta_angle_down);
    int lowest_desired_angle = desired_angle_last - ((desired_angle_last >= 0) ? delta_angle_down : delta_angle_up);

    // check that commanded angle value isn't too far from measured, used to limit torque for some safety modes
    // ensure we start moving in direction of meas while respecting relaxed rate limits if error is exceeded
    if (((vehicle_speed.values[0] / VEHICLE_SPEED_FACTOR) > 0.0F)) {
      // flipped fudge to avoid false positives
      const float fudged_speed_error = (vehicle_speed.max / VEHICLE_SPEED_FACTOR) + 1.;
      const int delta_angle_up_relaxed = (safety_interpolate(limits.angle_rate_up_lookup, fudged_speed_error) * limits.angle_deg_to_can) - 1.;
      const int delta_angle_down_relaxed = (safety_interpolate(limits.angle_rate_down_lookup, fudged_speed_error) * limits.angle_deg_to_can) - 1.;

      // the minimum and maximum angle allowed based on the measured angle
      const int lowest_desired_angle_error = angle_meas.min - 455 - 1;
      const int highest_desired_angle_error = angle_meas.max + 455 + 1;

      // the MAX is to allow the desired angle to hit the edge of the bounds and not require going under it
      if (desired_angle_last > highest_desired_angle_error) {
        const int delta = (desired_angle_last >= 0) ? delta_angle_down_relaxed : delta_angle_up_relaxed;
        highest_desired_angle = SAFETY_MAX(desired_angle_last - delta, highest_desired_angle_error);

      } else if (desired_angle_last < lowest_desired_angle_error) {
        const int delta = (desired_angle_last <= 0) ? delta_angle_down_relaxed : delta_angle_up_relaxed;
        lowest_desired_angle = SAFETY_MIN(desired_angle_last + delta, lowest_desired_angle_error);

      } else {
        // already inside error boundary, don't allow commanding outside it
        highest_desired_angle = SAFETY_MIN(highest_desired_angle, highest_desired_angle_error);
        lowest_desired_angle = SAFETY_MAX(lowest_desired_angle, lowest_desired_angle_error);
      }

      // don't enforce above the max steer
      // TODO: this should always be done
      lowest_desired_angle = SAFETY_CLAMP(lowest_desired_angle, -limits.max_angle, limits.max_angle);
      highest_desired_angle = SAFETY_CLAMP(highest_desired_angle, -limits.max_angle, limits.max_angle);
    }

    // check for violation;
    violation |= safety_max_limit_check(desired_angle, highest_desired_angle, lowest_desired_angle);
  }
  desired_angle_last = desired_angle;

  // Angle should be close to current angle while not steering
  if (!steer_control_enabled) {
    violation |= steer_angle_cmd_inactive_check(desired_angle, limits.max_angle);
  }

  // No angle control allowed when controls are not allowed
  if (!lateral_controls_allowed()) {
    violation |= steer_control_enabled;
  }

  // reset to current angle if either controls is not allowed or there's a violation
  if (violation || !lateral_controls_allowed()) {
    desired_angle_last = SAFETY_CLAMP(angle_meas.values[0], -limits.max_angle, limits.max_angle);
  }

  return violation;
}


static void volvo_c1_rx_hook(const CANPacket_t *msg) {
  if (msg->bus == VOLVO_C1_PT_BUS) {
    if (msg->addr == VOLVO_C1_PSCM_1) {
      update_sample(&angle_meas, volvo_c1_pscm_angle(msg));
    }

    if (msg->addr == VOLVO_C1_SPEED) {
      const uint16_t speed_raw = ((uint16_t)msg->data[6] << 8U) | msg->data[7];
      const float speed = ((float)speed_raw * 0.01f) / 3.6f;
      vehicle_moving = speed > 0.1f;
      UPDATE_VEHICLE_SPEED(speed);
    }

    if (msg->addr == VOLVO_C1_PEDAL_AND_BRAKE) {
      const uint16_t gas_raw = ((uint16_t)(msg->data[1] & 0x3U) << 8U) | msg->data[2];
      gas_pressed = gas_raw > 50U;  // DBC factor 0.1: greater than 5 percent
      brake_pressed = GET_BIT(msg, 24U) || GET_BIT(msg, 38U);
    }
  }

  if ((msg->bus == VOLVO_C1_CAM_BUS) && (msg->addr == VOLVO_C1_FSM_0)) {
    pcm_cruise_check(GET_BIT(msg, 58U));
  }
}

static bool volvo_c1_tx_hook(const CANPacket_t *msg) {
  bool tx = true;
  if (msg->addr == VOLVO_C1_FSM_1) {
    const int desired_angle = volvo_c1_fsm_angle(msg);
    const uint8_t direction = msg->data[7] & 0x3U;
    const bool steer_control_enabled = direction != 0U;
    tx &= SAFETY_ABS(desired_angle) <= VOLVO_C1_MAX_ANGLE_CAN;
    tx &= !volvo_c1_angle_cmd_checks(desired_angle, steer_control_enabled, VOLVO_C1_ANGLE_STEERING_LIMITS);
    tx &= (direction == 0U) || (direction == 3U);
    tx &= (msg->data[0] == 0xE3U) && (msg->data[1] == 0xB4U) && (msg->data[2] == 0x08U);
    tx &= (msg->data[3] == 0x80U) && ((msg->data[4] & 0xC0U) == 0x80U) && ((msg->data[7] & 0xFCU) == 0x94U);
    tx &= msg->data[6] == volvo_c1_fsm_checksum(msg);
  }

  if (msg->addr == VOLVO_C1_PSCM_1) {
    const int relayed_angle = volvo_c1_pscm_angle(msg);
    const uint32_t torque_raw = (((uint32_t)msg->data[1] & 0x0FU) << 8U) | (uint32_t)msg->data[2];
    tx &= torque_raw == 2000U;
    tx &= (msg->data[1] & 0x20U) == 0U;
    const int measured_max = angle_meas.max + VOLVO_C1_RELAY_ANGLE_TOLERANCE;
    const int measured_min = angle_meas.min - VOLVO_C1_RELAY_ANGLE_TOLERANCE;
    tx &= !safety_max_limit_check(relayed_angle, measured_max, measured_min);
  }

  // Only ACC cancel (byte 7 bit 4) may be synthesized.
  if (msg->addr == VOLVO_C1_BUTTONS) {
    for (unsigned int i = 0U; i < 6U; i++) {
      tx &= msg->data[i] == 0U;
    }
    tx &= ((msg->data[7] & 0xEFU) == 0U) && (msg->data[6] == 0U);
  }
  return tx;
}

static safety_config volvo_c1_init(uint16_t param) {
  (void)param;
  static const CanMsg VOLVO_C1_TX_MSGS[] = {
    {VOLVO_C1_FSM_1, VOLVO_C1_PT_BUS, 8, .check_relay = true},
    {VOLVO_C1_PSCM_1, VOLVO_C1_CAM_BUS, 8, .check_relay = true},
    {VOLVO_C1_BUTTONS, VOLVO_C1_PT_BUS, 8, .check_relay = false},
  };
  static RxCheck volvo_c1_rx_checks[] = {
    {.msg = {{VOLVO_C1_PSCM_1, VOLVO_C1_PT_BUS, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_C1_FSM_0, VOLVO_C1_CAM_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_C1_PEDAL_AND_BRAKE, VOLVO_C1_PT_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_C1_SPEED, VOLVO_C1_PT_BUS, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };
  return BUILD_SAFETY_CFG(volvo_c1_rx_checks, VOLVO_C1_TX_MSGS);
}

const safety_hooks volvo_c1_hooks = {
  .init = volvo_c1_init,
  .rx = volvo_c1_rx_hook,
  .tx = volvo_c1_tx_hook,
};
