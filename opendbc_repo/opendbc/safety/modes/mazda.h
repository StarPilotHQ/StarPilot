#pragma once

#include "opendbc/safety/declarations.h"
#include "opendbc/safety/modes/mazda_stock_aol.h"

// CAN msgs we care about
/********** GEN1 msgs **********/
#define MAZDA_LKAS          0x243U
#define MAZDA_LKAS_HUD      0x440U
#define MAZDA_CRZ_CTRL      0x21cU
#define MAZDA_CRZ_BTNS      0x09dU
#define MAZDA_STEER_TORQUE  0x240U
#define MAZDA_ENGINE_DATA   0x202U
#define MAZDA_PEDALS        0x165U
#define TI_STEER_TORQUE     0x24AU
// Radar
#define MAZDA_CRZ_INFO      0x21BU
#define MAZDA_RADAR_361     0x361U
#define MAZDA_RADAR_362     0x362U
#define MAZDA_RADAR_363     0x363U
#define MAZDA_RADAR_364     0x364U
#define MAZDA_RADAR_365     0x365U
#define MAZDA_RADAR_366     0x366U
#define MAZDA_RADAR_499     0x499U
/************* GEN2 msgs *************/
#define MAZDA_2019_BRAKE          0x43FU  // main bus
#define MAZDA_2019_GAS            0x202U  // camera bus DBC: ENGINE_DATA
#define MAZDA_2019_CRUISE         0x44AU  // main bus. DBC: CRUISE_STATE
#define MAZDA_2019_SPEED          0x217U  // camera bus. DBC: SPEED
#define MAZDA_2019_STEER_TORQUE   0x24BU  // aux bus. DBC: TI_FEEDBACK
#define MAZDA_2019_CRZ_BTNS       0x9DU   // rx on main tx on camera. DBC: CRZ_BTNS
#define MAZDA_2019_ACC            0x220U  // main bus. DBC: ACC
#define MAZDA_TI_LKAS             0x249U
/************* GEN3 msgs *************/
#define MAZDA_2023_SPEED          0x215  // camera bus. DBC: WHEEL_SPEEDS
#define MAZDA_2023_BRAKE          0x9F  // main bus. DBC: BRAKE_PEDAL
#define MAZDA_2023_ACC            0x21e  // main bus. DBC: ACC

// CAN bus numbers
#define MAZDA_MAIN 0
#define MAZDA_AUX  1
#define MAZDA_CAM  2

// param flag masks
const int FLAG_GEN1 = 1;
const int FLAG_GEN2 = 2;
const int FLAG_GEN3 = 4;
const int FLAG_TORQUE_INTERCEPTOR = 8;
const int FLAG_RADAR_INTERCEPTOR = 16;
const int FLAG_NO_FSC = 32;
const int FLAG_NO_MRCC = 64;

static bool mazda_gen1 = false;
static bool mazda_gen2 = false;
static bool mazda_gen3 = false;
static bool mazda_torque_interceptor = false;
static bool mazda_radar_interceptor = false;
static bool mazda_no_fsc = false;
static bool mazda_no_mrcc = false;


// track msgs coming from OP so that we know what CAM msgs to drop and what to forward
static void mazda_rx_hook(const CANPacket_t *msg) {
  mazda_aol_rx(msg);

  if ((int)msg->bus == MAZDA_MAIN) {
    if (mazda_gen1) {
      if (msg->addr == MAZDA_ENGINE_DATA) {
        // sample speed: scale by 0.01 to get kph
        int speed = (msg->data[2] << 8) | msg->data[3];
        vehicle_moving = speed > 10; // moving when speed > 0.1 kph
      }

      if (msg->addr == MAZDA_STEER_TORQUE && !mazda_torque_interceptor) {
        int torque_driver_new = msg->data[0] - 127U;
        // update array of samples
        update_sample(&torque_driver, torque_driver_new);
      }

      // enter controls on rising edge of ACC, exit controls on ACC off
      if (msg->addr == MAZDA_CRZ_CTRL && !mazda_no_mrcc && !mazda_radar_interceptor) {
        acc_main_on = GET_BIT(msg, 17U);
        bool cruise_engaged = msg->data[0] & 0x8U;
        const bool was_allowed = controls_allowed;
        pcm_cruise_check(cruise_engaged);
        mazda_aol_engagement(msg, cruise_engaged, !was_allowed && controls_allowed);
      }

      if (msg->addr == MAZDA_ENGINE_DATA) {
        gas_pressed = (msg->data[4] | (msg->data[5] & 0xF0U)) != 0U;
      }

      if (msg->addr == MAZDA_PEDALS) {
        brake_pressed = (msg->data[0] & 0x10U);
        if (mazda_radar_interceptor || mazda_no_mrcc) {
          bool cruise_engaged = msg->data[0] & 0x8U;
          pcm_cruise_check(cruise_engaged);
        }
      }
    }

    if (mazda_gen2) {
      if (msg->addr == MAZDA_2019_BRAKE) {
        brake_pressed = (msg->data[5] & 0x4U);
      }

      if (msg->addr == MAZDA_2019_CRUISE) {
        acc_main_on = true;
        bool cruise_engaged = msg->data[0] & 0x20U;
        bool pre_enable = msg->data[0] & 0x40U;
        pcm_cruise_check((cruise_engaged || pre_enable));
      }
    }

    if (mazda_gen3) {
      if (msg->addr == MAZDA_2023_BRAKE) {
        brake_pressed = (msg->data[7] & 0x8U);
      }
    }
  }

  if ((msg->bus == MAZDA_AUX)) {

    if (msg->addr == TI_STEER_TORQUE && mazda_gen1 && mazda_torque_interceptor) {
      int torque_driver_new = msg->data[0] - 127;
      update_sample(&torque_driver, torque_driver_new);
    }

    // enter controls on rising edge of ACC, exit controls on ACC off
    if (msg->addr == MAZDA_CRZ_CTRL) {
      bool cruise_engaged = msg->data[0] & 0x8U;
      pcm_cruise_check(cruise_engaged);

      acc_main_on = GET_BIT(msg, 17U);
    }

    if (msg->addr == MAZDA_2019_STEER_TORQUE && (mazda_gen2 || mazda_gen3)) {
      update_sample(&torque_driver, (int16_t)(msg->data[0] << 8 | msg->data[1]));
    }

    if (msg->addr == MAZDA_2019_CRUISE && mazda_gen3) {
      uint8_t state = msg->data[0] & 0x70U;
      bool cruise_engaged = (0x30U == state);
      bool cruise_override = (0x40U == state);
      acc_main_on = true;
      pcm_cruise_check(cruise_engaged || cruise_override);
    }
  }

  if ((msg->bus == MAZDA_CAM)) {
    if (mazda_gen2) {

      if (msg->addr == MAZDA_2019_GAS) {
        gas_pressed = (msg->data[4] || ((msg->data[5] & 0xC0U)));
      }

      if (msg->addr == MAZDA_2019_SPEED) {
        // sample speed: scale by 0.01 to get kph
        int speed = (msg->data[4] << 8) | (msg->data[5]);
        vehicle_moving = (speed > 10);  // moving when speed > 0.1 kph
      }
    }
    if (mazda_gen3) {
      if (msg->addr == MAZDA_2019_GAS) {
        gas_pressed = (msg->data[4] || (msg->data[5] & 0xC0U));
      }
      if (msg->addr == MAZDA_2023_SPEED) {
        // GEN3 wheel speeds arrive on the camera bus (bus 2), matching the rx-check
        int speed = ( (msg->data[0] << 8) | (msg->data[1]) ) - 10000; // Front Left Wheel Speed
        vehicle_moving = speed > 10;  // same 0.1 kph standstill threshold as GEN1/GEN2
      }
    }
  }
}

static bool mazda_tx_hook(const CANPacket_t *msg) {
  const TorqueSteeringLimits MAZDA_STEERING_LIMITS = {
    .max_torque = 800,
    .max_rate_up = 10,
    .max_rate_down = 25,
    .max_rt_delta = 300,
    .driver_torque_multiplier = 1,
    .driver_torque_allowance = 15,
    .type = TorqueDriverLimited,
  };

  const TorqueSteeringLimits MAZDA_2019_STEERING_LIMITS = {
    .max_torque = 8000,
    .max_rate_up = 45,
    .max_rate_down = 80,
    .max_rt_delta = 1688,  // (45*100hz*250000/1000000)*1.5
    .driver_torque_multiplier = 1,
    .driver_torque_allowance = 1400,
    .type = TorqueDriverLimited,
  };

  bool tx = true;
  // Check if msg is sent on the main BUS
  if (msg->bus == (unsigned char)MAZDA_MAIN) {
    // steer cmd checks
    if (msg->addr == MAZDA_LKAS) {
      int desired_torque = (((msg->data[0] & 0x0FU) << 8) | msg->data[1]) - 2048U;

      if (steer_torque_cmd_checks(desired_torque, -1, MAZDA_STEERING_LIMITS)) {
        tx = false;
      }
    }

    // cruise buttons check
    if (msg->addr == MAZDA_CRZ_BTNS) {
      // allow resume spamming while controls allowed, but
      // only allow cancel while controls not allowed
      bool cancel_cmd = (msg->data[0] == 0x1U);
      if (!controls_allowed && !cancel_cmd) {
        tx = false;
      }
    }
  }
  if ((mazda_gen2 || mazda_gen3) && (msg->bus == MAZDA_AUX) && (msg->addr == MAZDA_TI_LKAS)) {
    int desired_torque = (int16_t)((msg->data[0] << 8) | msg->data[1]);  // signal is signed
    if (steer_torque_cmd_checks(desired_torque, -1, MAZDA_2019_STEERING_LIMITS)) {
      tx = false;
    }
  }

  mazda_aol_tx_commit(msg, tx && !relay_malfunction);
  return tx;
}

static safety_config mazda_init(uint16_t param) {
  // MAZDA_TX_MSGS stays non-const: the Dom stock-AOL path rewrites check_relay.
  static CanMsg MAZDA_TX_MSGS[] = {{MAZDA_LKAS, 0, 8, .check_relay = true}, {MAZDA_CRZ_BTNS, 0, 8, .check_relay = false}, {MAZDA_LKAS_HUD, 0, 8, .check_relay = true}};
  static const CanMsg MAZDA_TI_TX_MSGS[] = {{MAZDA_LKAS, 0, 8, .check_relay = true}, {MAZDA_TI_LKAS, 1, 8, .check_relay = false}, {MAZDA_CRZ_BTNS, 0, 8, .check_relay = false}, {MAZDA_LKAS_HUD, 0, 8, .check_relay = true}};
  static const CanMsg MAZDA_RI_TX_MSGS[] = {{MAZDA_LKAS, 0, 8, .check_relay = true}, {MAZDA_CRZ_BTNS, 0, 8, .check_relay = false}, {MAZDA_LKAS_HUD, 0, 8, .check_relay = true},
                                    {MAZDA_CRZ_CTRL, 0, 8, .check_relay = true}, {MAZDA_CRZ_INFO, 0, 8, .check_relay = true}, {MAZDA_RADAR_361, 0, 8, .check_relay = true}, {MAZDA_RADAR_362, 0, 8, .check_relay = true},
                                    {MAZDA_RADAR_363, 0, 8, .check_relay = true}, {MAZDA_RADAR_364, 0, 8, .check_relay = true}, {MAZDA_RADAR_365, 0, 8, .check_relay = true}, {MAZDA_RADAR_366, 0, 8, .check_relay = true},
                                    {MAZDA_RADAR_499, 0, 8, .check_relay = true}};
  static const CanMsg MAZDA_TI_RI_TX_MSGS[] = {{MAZDA_LKAS, 0, 8, .check_relay = true}, {MAZDA_TI_LKAS, 1, 8, .check_relay = false}, {MAZDA_CRZ_BTNS, 0, 8, .check_relay = false}, {MAZDA_LKAS_HUD, 0, 8, .check_relay = true},
                                  {MAZDA_CRZ_CTRL, 0, 8, .check_relay = true}, {MAZDA_CRZ_INFO, 0, 8, .check_relay = true}, {MAZDA_RADAR_361, 0, 8, .check_relay = true}, {MAZDA_RADAR_362, 0, 8, .check_relay = true},
                                  {MAZDA_RADAR_363, 0, 8, .check_relay = true}, {MAZDA_RADAR_364, 0, 8, .check_relay = true}, {MAZDA_RADAR_365, 0, 8, .check_relay = true}, {MAZDA_RADAR_366, 0, 8, .check_relay = true},
                                  {MAZDA_RADAR_499, 0, 8, .check_relay = true}};

  static const CanMsg MAZDA_2019_TX_MSGS[] = {{MAZDA_TI_LKAS, 1, 8, .check_relay = false}, {MAZDA_2019_ACC, 2, 8, .check_relay = true}};
  // GEN3 is lateral-only: no openpilot longitudinal, so 0x220 is never TX-allowed
  static const CanMsg MAZDA_2023_TX_MSGS[] = {{MAZDA_TI_LKAS, 1, 8, .check_relay = false}};

  static const RxCheck mazda_rx_checks[] = {
    {.msg = {{MAZDA_CRZ_CTRL,     0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{MAZDA_CRZ_BTNS,     0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{MAZDA_STEER_TORQUE, 0, 8, 83U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{MAZDA_ENGINE_DATA,  0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{MAZDA_PEDALS,       0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    // slot 5: populated at init only when a torque interceptor is fitted,
    // instead of keeping a second full RxCheck table in DTCMRAM
    { .msg = {{ 0 }, { 0 }, { 0 }} },
  };
  static RxCheck mazda_gen23_rx_checks[5];

  mazda_aol_reset();

  // A1: reset all generation/flag state on every init, so a mode switch can
  // never inherit the previous safety configuration.
  mazda_gen1 = false;
  mazda_gen2 = false;
  mazda_gen3 = false;
  mazda_torque_interceptor = false;
  mazda_radar_interceptor = false;
  mazda_no_fsc = false;
  mazda_no_mrcc = false;

  // MoreTore param flags select the generation and interceptors. Pure-stock
  // GEN1 keeps param 0 (Dom's stock-AOL configuration); the GEN1 bit is set
  // only for TI/RI/NoMRCC/NoFSC variants (see car/mazda/interface.py).
  const bool stock_gen1 = (param == 0U);
  mazda_gen1 = GET_FLAG(param, FLAG_GEN1) || stock_gen1;
  mazda_gen2 = GET_FLAG(param, FLAG_GEN2);
  mazda_gen3 = GET_FLAG(param, FLAG_GEN3);
  mazda_radar_interceptor = GET_FLAG(param, FLAG_RADAR_INTERCEPTOR);
  mazda_torque_interceptor = GET_FLAG(param, FLAG_TORQUE_INTERCEPTOR);
  mazda_no_fsc = GET_FLAG(param, FLAG_NO_FSC);
  mazda_no_mrcc = GET_FLAG(param, FLAG_NO_MRCC);

  mazda_aol_enabled = stock_gen1 && ((unsigned int)alternative_experience == 32U);
  MAZDA_TX_MSGS[0].check_relay = !mazda_aol_enabled;
  MAZDA_TX_MSGS[2].check_relay = !mazda_aol_enabled;
  if (mazda_aol_enabled) {
    static const AolSafetyPolicy policy = {
      .reset = mazda_aol_reset,
      .host_request = mazda_aol_request,
      .request_mask = mazda_aol_request_mask,
      .permission_mask = mazda_aol_permission,
      .rx_invalid = mazda_aol_clear,
    };
    aol_policy = &policy;
  }

  safety_config ret = BUILD_SAFETY_CFG(mazda_rx_checks, MAZDA_TX_MSGS);

  if (mazda_gen1) {
    SET_RX_CHECKS(mazda_rx_checks, ret);
    if (mazda_torque_interceptor) {
      mazda_rx_checks[5].msg[0] = (CanMsgCheck){TI_STEER_TORQUE, 1, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true};
    }
    // only expose the TI slot when a torque interceptor is actually fitted;
    // a zeroed slot must not become a check on address 0
    ret.rx_checks_len = mazda_torque_interceptor ? 6 : 5;
    if (mazda_radar_interceptor && mazda_torque_interceptor) {
      SET_TX_MSGS(MAZDA_TI_RI_TX_MSGS, ret);
    } else if (mazda_radar_interceptor) {
      SET_TX_MSGS(MAZDA_RI_TX_MSGS, ret);
    } else if (mazda_torque_interceptor) {
      SET_TX_MSGS(MAZDA_TI_TX_MSGS, ret);
    } else {
      SET_TX_MSGS(MAZDA_TX_MSGS, ret);
    }
  }

  if (mazda_gen2) {
    static const RxCheck checks[5] = {
      {.msg = {{MAZDA_2019_BRAKE,         0, 8, 20U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{MAZDA_2019_GAS,           2, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{MAZDA_2019_CRUISE,        0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{MAZDA_2019_SPEED,         2, 8, 30U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{MAZDA_2019_STEER_TORQUE,  1, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    };
    memcpy(mazda_gen23_rx_checks, checks, sizeof(checks));
    ret = BUILD_SAFETY_CFG(mazda_gen23_rx_checks, MAZDA_2019_TX_MSGS);
  }
  if (mazda_gen3) {
    static const RxCheck checks[5] = {
      {.msg = {{MAZDA_2023_BRAKE,         0, 8, 5U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{MAZDA_2019_GAS,           2, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{MAZDA_2019_CRUISE,        1, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{MAZDA_2023_SPEED,         2, 8, 30U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{MAZDA_2019_STEER_TORQUE,  1, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    };
    memcpy(mazda_gen23_rx_checks, checks, sizeof(checks));
    ret = BUILD_SAFETY_CFG(mazda_gen23_rx_checks, MAZDA_2023_TX_MSGS);
  }

  // Alt-exp 32 (stock AOL) is only defined for pure-stock GEN1 (param 0).
  // Every other configuration (TI, RI, GEN2, GEN3) gets no TX in that mode,
  // matching Dom's fail-closed design for a GEN1-only feature.
  if (((unsigned int)alternative_experience == 32U) && !stock_gen1) {
    ret.tx_msgs = NULL;
    ret.tx_msgs_len = 0;
  }
  return ret;
}

const safety_hooks mazda_hooks = {
  .init = mazda_init,
  .rx = mazda_rx_hook,
  .tx = mazda_tx_hook,
  .optional_rx = mazda_aol_optional_rx,
  .fwd = mazda_aol_fwd,
};
