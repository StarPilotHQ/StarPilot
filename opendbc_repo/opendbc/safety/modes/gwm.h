#pragma once

#include "opendbc/safety/declarations.h"
#include "opendbc/safety/modes/gwm_aol.h"

// GWM Haval H6 GT safety. Message layout, checksum polynomial and the
// longitudinal gas/brake scaling derive from Alexandre Sato's GWM Haval H6
// openpilot port; see the repository root CREDITS.md and THIRD_PARTY_NOTICES.md.

#define GWM_ADAS_ACTIVATION      0xA1U // RX from STEER_AND_AP_STALK
#define GWM_GAS                  0x60U // RX from CAR_OVERALL_SIGNALS
#define GWM_BRAKE               0x120U // RX from BRAKE2
#define GWM_SPEED               0x13BU // RX from WHEEL_SPEEDS
#define GWM_RX_STEER_RELATED    0x147U // RX from EPS to CAMERA
#define GWM_STEER_CMD           0x12BU // TX from OP to EPS
#define GWM_CRUISE              0x2ABU
#define GWM_LONG_CONTROL        0x143U // TX from OP to PCM
#define GWM_BLIND_SPOT          0x16FU
#define GWM_HUD                 0x23DU

// CAN bus
#define GWM_MAIN_BUS    0U
#define GWM_CAMERA_BUS  2U

#define GWM_KPH_TO_MS 0.277778f

// Every rolling counter on this platform walks 0..14 and never emits 15, so the
// expected-counter modulus is 15 (max_counter + 1). Verified on the bus for the
// stalk (0xA1), WHEEL_SPEEDS (0x13B, front and rear), the camera's STEER_CMD and
// ACC.COUNTER2 - all roll 14 -> 0.
#define GWM_MAX_COUNTER 14U

static bool gwm_longitudinal = false;

static const TorqueSteeringLimits GWM_STEERING_LIMITS = {
  // 253: validated over the original 700 km trip. Higher caps drove the EPS
  // into its own EPS_FAULT_PERMANENT latch mid-curve (see values.py). Must
  // match CarControllerParams.STEER_MAX.
  .max_torque = 253,
  .max_rate_up = 4,
  .max_rate_down = 6,
  .max_rt_delta = 100,
  .max_torque_error = 80,
  .type = TorqueMotorLimited,
};

static const LongitudinalLimits GWM_LONG_LIMITS = {
  .max_gas = 4577,
  .min_gas = -10,
  .inactive_gas = 0,
  .max_brake = 107,
};

static uint8_t gwm_get_counter(const CANPacket_t *msg) {
  uint8_t cnt = 0;
  if ((msg->addr == GWM_SPEED) || (msg->addr == GWM_ADAS_ACTIVATION)) {
    cnt = msg->data[7] & 0xFU;
  } else {
  }
  return cnt;
}

static uint32_t gwm_get_checksum(const CANPacket_t *msg) {
  uint8_t chksum = 0;
  if ((msg->addr == GWM_SPEED) || (msg->addr == GWM_ADAS_ACTIVATION)) {
    chksum = msg->data[0] & 0xFFU;
  } else {
  }
  return chksum;
}

static uint32_t gwm_compute_checksum(const CANPacket_t *msg) {
  uint8_t chksum = 0;
  uint8_t crc = 0x00;
  const uint8_t poly = 0x1D;
  uint8_t xor_out = 0x00;
  int len = 8;
  for (int i = 1; i < len; i++) {
    uint8_t byte = msg->data[i];
    crc ^= byte;
    for (int bit = 0; bit < 8; bit++) {
      if ((crc & 0x80U) != 0U) {
        crc = (crc << 1) ^ poly;
      } else {
        crc <<= 1;
      }
      crc &= 0xFFU;
    }
  }
  if (msg->addr == GWM_ADAS_ACTIVATION) {
    xor_out = 0x2DU;
  } else if (msg->addr == GWM_SPEED) {
    xor_out = 0x7FU;
  } else {
  }
  chksum = crc ^ xor_out;
  return chksum;
}

static void gwm_rx_hook(const CANPacket_t *msg) {
  gwm_aol_observe(msg);

  if (msg->bus == GWM_MAIN_BUS) {
    // GAS_POSITION
    if (msg->addr == GWM_GAS) {
      gas_pressed = msg->data[9] > 0U;
    }

    if (msg->addr == GWM_SPEED) {
      uint32_t fl = ((msg->data[1] << 8) | msg->data[2]) & 0x1FFFU;
      uint32_t fr = ((msg->data[3] << 8) | msg->data[4]) & 0x1FFFU;
      uint32_t rl = ((msg->data[41] << 8) | msg->data[42]) & 0x1FFFU;
      uint32_t rr = ((msg->data[43] << 8) | msg->data[44]) & 0x1FFFU;
      float speed = (float)((fr + rr + rl + fl) / 4.0f * 0.05924739f * GWM_KPH_TO_MS);
      vehicle_moving = speed > 0.0f;
      UPDATE_VEHICLE_SPEED(speed);
    }

    if (msg->addr == GWM_BRAKE) {
      brake_pressed = GET_BIT(msg, 11U);
    }

    if (msg->addr == GWM_RX_STEER_RELATED) {
      int torque_meas_new = ((msg->data[13] & 0x7U) << 8) | (msg->data[14]);
      torque_meas_new = to_signed(torque_meas_new, 11) + 548;
      update_sample(&torque_meas, torque_meas_new);

      // increase torque_meas by 1 to be conservative on rounding
      torque_meas.min--;
      torque_meas.max++;
    }

    // state machine to enter and exit controls for button enabling
    if (msg->addr == GWM_ADAS_ACTIVATION) {
      bool cruise_button = GET_BIT(msg, 47U);
      // (Re)enter controls on every rising edge of the stalk pull, directly
      // (button-style engagement, like hyundai/gm). This must not go through
      // pcm_cruise_check: the brake clears controls_allowed (generic safety)
      // WITHOUT clearing acc_main_on, so a stalk pull after a brake-only
      // disengage produced no rising edge on acc_main_on and controls_allowed
      // stayed latched false while openpilot re-engaged -> permanent
      // "Controls Mismatch" (confirmed in route 0000005b--75dacbd411).
      if (cruise_button && !cruise_button_prev) {
        acc_main_on = true;
        controls_allowed = true;
      }
      // exit controls once cancel is pressed (stalk down). The brake is
      // intentionally NOT included here: it must not drop acc_main_on, so
      // Always On Lateral keeps steering with a foot on the brake.
      bool cancel_button = GET_BIT(msg, 46U);
      if (cancel_button) {
        acc_main_on = false;
        controls_allowed = false;
      }
      cruise_button_prev = cruise_button ? 1 : 0;
    }
  }
}

static bool gwm_tx_hook(const CANPacket_t *msg) {
  bool tx = true;
  bool violation = false;

  if (msg->bus == GWM_MAIN_BUS) {
    if (msg->addr == GWM_STEER_CMD) {
      int desired_torque = (((msg->data[12] & 0x7FU) << 3) | ((msg->data[13] & 0xE0U) >> 5));
      desired_torque = to_signed(desired_torque, 10) + 1;
      bool steer_req = GET_BIT(msg, 125U);
      violation |= steer_torque_cmd_checks(desired_torque, steer_req, GWM_STEERING_LIMITS);
    }

    if (msg->addr == GWM_LONG_CONTROL) {
      int brake_raw = msg->data[13];
      brake_raw = 181 - brake_raw;
      violation |= longitudinal_brake_checks(brake_raw, GWM_LONG_LIMITS);

      int gas_raw = ((msg->data[27] & 0x1FU) << 8) | (msg->data[28]);
      gas_raw = gas_raw - 192;
      violation |= longitudinal_gas_checks(gas_raw, GWM_LONG_LIMITS);
    }
  }

  if (violation) {
    tx = false;
  }
  return tx;
}

// No fwd hook: every message openpilot spoofs is declared in the TX list with
// .check_relay = true, and the generic forwarder already blocks a stock copy
// from reaching the destination bus for those addresses. The stock copies all
// arrive on the opposite bus from where we transmit (stalk and EPS feedback
// from bus 0, STEER_CMD / HUD / ACC_CMD from the camera on bus 2), so this
// reproduces the hand-written blocking the FrogPilot port validated on the
// road - and GWM_LONG_CONTROL is still only blocked when openpilot owns
// longitudinal, because it only appears in the long TX list.

static safety_config gwm_init(uint16_t param) {
  static const CanMsg GWM_TX_MSGS[] = {
    {GWM_ADAS_ACTIVATION, GWM_CAMERA_BUS, 8, .check_relay = true},   // Stalk stream (cancel / S&G resume)
    {GWM_RX_STEER_RELATED, GWM_CAMERA_BUS, 64, .check_relay = true}, // EPS steering feedback to camera
    {GWM_STEER_CMD, GWM_MAIN_BUS, 64, .check_relay = true},          // Steering command
    {GWM_HUD, GWM_MAIN_BUS, 64, .check_relay = true},                // HUD and dashboard
  };

  static const CanMsg GWM_LONG_TX_MSGS[] = {
    {GWM_ADAS_ACTIVATION, GWM_CAMERA_BUS, 8, .check_relay = true},   // Stalk stream (cancel / S&G resume)
    {GWM_RX_STEER_RELATED, GWM_CAMERA_BUS, 64, .check_relay = true}, // EPS steering feedback to camera
    {GWM_STEER_CMD, GWM_MAIN_BUS, 64, .check_relay = true},          // Steering command
    {GWM_LONG_CONTROL, GWM_MAIN_BUS, 64, .check_relay = true},       // Longitudinal control message from camera
    {GWM_HUD, GWM_MAIN_BUS, 64, .check_relay = true},                // HUD and dashboard
  };

  // Keep rx checks only on safety-critical bus 0 messages. Camera-bus (bus 2)
  // and comfort messages (BSM) were removed on purpose: any transient pause on
  // those streams tripped the 1s lagging check, silently clearing
  // controls_allowed while openpilot stayed engaged -> "Controls Mismatch"
  // disengages. The openpilot side still monitors camera messages via the CAN
  // parser (canValid), so a dead camera is not silent.
  // The checksum/counter/quality flags are fail-closed here: leaving a flag
  // unset means "check it", and a check with no backing hook resolves to
  // invalid. Only the stalk and WHEEL_SPEEDS carry a checksum and counter that
  // gwm_get_checksum/gwm_get_counter understand, so the other three must opt
  // out explicitly, and none of the messages has a quality flag.
  static RxCheck gwm_rx_checks[] = {
    {.msg = {{GWM_ADAS_ACTIVATION, GWM_MAIN_BUS, 8, 100U, .max_counter = GWM_MAX_COUNTER, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{GWM_SPEED, GWM_MAIN_BUS, 64, 50U, .max_counter = GWM_MAX_COUNTER, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{GWM_GAS, GWM_MAIN_BUS, 64, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{GWM_BRAKE, GWM_MAIN_BUS, 64, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{GWM_RX_STEER_RELATED, GWM_MAIN_BUS, 64, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

  gwm_longitudinal = false;
  #ifdef ALLOW_DEBUG
    const int FLAG_GWM_LONG_CONTROL = 1;
    gwm_longitudinal = GET_FLAG(param, FLAG_GWM_LONG_CONTROL);
  #else
    UNUSED(param);
  #endif

  // Always On Lateral. Qualified on the safety word rather than on
  // gwm_longitudinal, which only exists in ALLOW_DEBUG builds; the host side
  // (starpilot/car/gwm/aol.py) admits the same single profile. The param test
  // is intentionally unconditional so a release build qualifies identically.
  gwm_aol_reset();
  gwm_aol_enabled = ((unsigned int)alternative_experience == 32U) && ((unsigned int)param == 1U);
  if (gwm_aol_enabled) {
    static const AolSafetyPolicy policy = {
      .reset = gwm_aol_reset,
      .request_mask = gwm_aol_request_mask,
      .permission_mask = gwm_aol_permission,
      .rx_invalid = gwm_aol_clear,
    };
    aol_policy = &policy;
  }

  return gwm_longitudinal ? BUILD_SAFETY_CFG(gwm_rx_checks, GWM_LONG_TX_MSGS) : \
                            BUILD_SAFETY_CFG(gwm_rx_checks, GWM_TX_MSGS);
}

const safety_hooks gwm_hooks = {
  .init = gwm_init,
  .rx = gwm_rx_hook,
  .tx = gwm_tx_hook,
  .get_checksum = gwm_get_checksum,
  .compute_checksum = gwm_compute_checksum,
  .get_counter = gwm_get_counter,
};
