#pragma once

#include "opendbc/safety/declarations.h"
#include "opendbc/safety/modes/volvo_cma_integrity.h"

// Volvo CMA/SPA receiver layout, safetyParam 0: XC40 Recharge, S60 Recharge, Polestar 2.
// Polestar 2 is technically CMA, but appears to use the SPA DBC for CAN bus 1.
// Selected by volvo_dispatch.h.

// Volvo SPA CAN message addresses
#define VOLVO_LCA_STEER           0x58U    // TX from VCU1 to PSCM, LCA steering command (0x58)
#define VOLVO_LCA_2               0x69U   // RX from BCM, brake pedal, cruise state
#define VOLVO_SAS                 0x55U    // RX from SAS, steering angle sensor
#define VOLVO_PSCM                0x16U    // RX from PSCM, driver steering input
#define VOLVO_GEAR_POSITION       0x80U   // RX from transmission, gear position
#define VOLVO_DRIVER_INPUT        0x15U
#define VOLVO_LCA_3               0x57U   // TX from VCU1 to PSCM
#define VOLVO_LCA_5               0x67U   // TX LCA_5 message (formerly SPEED_1, contains wheel speeds and LCA signals)
#define VOLVO_SPEED               0x60U   // RX/TX SPEED message
#define VOLVO_SPEED_2             0x68U   // RX
#define VOLVO_EGSM                0x45U   // RX from EGSM
#define VOLVO_PSCM_RELATED        0x17U   // RX from PSCM, related messages
#define VOLVO_LCA_4               0x90U   // TX LCA_4 message (PA status spoofing)
#define VOLVO_LCA_6               0x97U   // RX, forwarded unmodified
#define VOLVO_LCA_7               0x92U   // RX, forwarded unmodified

// SPA PT bus addresses
#define VOLVO_SPA_BUS1_SPEED          0x75U   // RX vehicle speed
#define VOLVO_SPA_ECM_1               0x25U   // RX accelerator pedal position
#define VOLVO_SPA_BUS1_CRUISE_CONTROL 0x349U  // RX cruise control state

// SPEED (0x60) is raw counts in the DBC. Measured against GPS ground speed on two
// harnesses: implied LSB 0.0039736 and 0.0039792 m/s.
#define VOLVO_SPEED_TO_MS 0.003977f

// LCA_5_STEER is a signed 15-bit steering-wheel-angle command in 0.05596 deg/count.
// Keep the absolute envelope aligned with the software controller's 540 deg limit.
#define VOLVO_ANGLE_DEG_TO_CAN 17.869907f
#define VOLVO_MAX_ANGLE_CAN 9650
#define VOLVO_RELAY_ANGLE_TOLERANCE 54  // approximately 3 degrees

// CAN bus definitions for Volvo
// Using same naming as carstate.py for consistency: main, pt, party
#define VOLVO_MAIN_BUS    0U  // Bus.main - VCU1 car side
#define VOLVO_PT_BUS      1U  // Bus.pt - VCU1 ECM side (where ECM is)
#define VOLVO_PARTY_BUS   2U  // Bus.party - VCU PSCM/BCM2 side (BCM2, SAS, EGSM, PSCM, where LCA is sent to)

// Messages openpilot replaces while actively steering. While openpilot is sending
// one, the stock version arriving on stock_bus must not also be forwarded. Panda
// tracks when each was last allowed through volvo_cma_tx_hook and only blocks the stock
// frame while that is recent, so the two sides can't disagree: if openpilot stops
// sending (disengaged, lateral paused, or the process stalls) the stock frames flow
// again within timeout_us instead of the address going silent.
//
// timeout_us is ~2.5x the longest gap between openpilot sends for that address:
// long enough to ride through normal scheduling jitter, short enough that a stall
// only drops a couple of frames before stock traffic takes over.
typedef struct {
  uint32_t addr;
  int stock_bus;
  uint32_t timeout_us;
} VolvoReplacedMsg;

#define VOLVO_REPLACED_MSG_CNT 7U
static const VolvoReplacedMsg VOLVO_REPLACED_MSGS[VOLVO_REPLACED_MSG_CNT] = {
  {VOLVO_LCA_STEER,    (int)VOLVO_MAIN_BUS,  25000U},  // 100 Hz
  {VOLVO_PSCM,         (int)VOLVO_PARTY_BUS, 25000U},  // 100 Hz
  {VOLVO_PSCM_RELATED, (int)VOLVO_PARTY_BUS, 25000U},  // 100 Hz
  {VOLVO_LCA_3,        (int)VOLVO_MAIN_BUS,  50000U},  // 2 of every 3 frames, max gap 20 ms
  {VOLVO_LCA_2,        (int)VOLVO_MAIN_BUS,  50000U},  // 50 Hz
  {VOLVO_LCA_5,        (int)VOLVO_MAIN_BUS,  50000U},  // 50 Hz
  {VOLVO_LCA_4,        (int)VOLVO_MAIN_BUS,  100000U}, // ~29 Hz, max gap 40 ms
};
static uint32_t volvo_replaced_tx_ts[VOLVO_REPLACED_MSG_CNT];
static bool volvo_replaced_tx_seen[VOLVO_REPLACED_MSG_CNT];

static int volvo_replaced_msg_index(uint32_t addr) {
  int index = -1;
  for (uint8_t i = 0U; i < VOLVO_REPLACED_MSG_CNT; i++) {
    if (VOLVO_REPLACED_MSGS[i].addr == addr) {
      index = (int)i;
    }
  }
  return index;
}

// Big-endian field whose high bits sit in data[byte] (under high_mask) and low byte in data[byte + 1].
static int volvo_be_masked(const CANPacket_t *msg, uint8_t byte, uint32_t high_mask) {
  const uint32_t raw = (((uint32_t)msg->data[byte] & high_mask) << 8U) | (uint32_t)msg->data[byte + 1U];
  return (int)raw;
}

static int volvo_be_15(const CANPacket_t *msg, uint8_t byte) {
  return volvo_be_masked(msg, byte, 0x7FU);
}

static int volvo_pscm_angle(const CANPacket_t *msg) {
  return to_signed(volvo_be_15(msg, 0U), 15);
}

static int volvo_lca_5_angle(const CANPacket_t *msg) {
  return to_signed(volvo_be_15(msg, 6U), 15);
}

static void volvo_cma_rx_hook(const CANPacket_t *msg) {
  // Main bus (bus 0) messages
  if (msg->bus == VOLVO_MAIN_BUS) {
    // Update brake pedal and cruise state from BCM2
    if (msg->addr == VOLVO_LCA_2) {
      // DBC: SG_ BRAKE_PEDAL_PRESSED_A : 47|1@0+ (-1,1) - inverted in DBC, so we invert raw bit
      // DBC: SG_ BRAKE_PEDAL_PRESSED_B : 46|1@0+ (1,0) - not inverted
      //bool brake_a = !((msg->data[5] >> 7) & 1U); // Raw bit, active low (DBC inverts it)
      bool brake_b = (msg->data[5] >> 6) & 1U; // Raw bit, active high
      //brake_pressed = brake_a || brake_b;
      brake_pressed = brake_b;
    }

    // Vehicle speed from the main bus, matching carstate.py. The PT bus carries a
    // speed message too, but which car bus lands on PT is harness-dependent and its
    // scaling differs per PT DBC, so both sides read the main bus instead.
    // DBC: SG_ SPEED : 6|15@0+ (1,0) - raw counts, scaled here
    if (msg->addr == VOLVO_SPEED) {
      uint16_t speed_raw = ((msg->data[0] & 0x7FU) << 8) | msg->data[1];
      float speed = (float)speed_raw * VOLVO_SPEED_TO_MS;
      vehicle_moving = speed > 0.1;
      UPDATE_VEHICLE_SPEED(speed);
    }
  }

  // PT bus (bus 1) messages
  if (msg->bus == VOLVO_PT_BUS) {
    if (msg->addr == VOLVO_SPA_ECM_1) {
      // SG_ ACCELERATOR_PEDAL_POS : 6|15@0+ (0.00390625,0) [0|32767] "%"
      uint16_t gas_raw = ((msg->data[0] & 0x7FU) << 8) | msg->data[1];
      gas_pressed = (gas_raw * 0.00390625) > 1.0; // > 1%
    }

    if (msg->addr == VOLVO_SPA_BUS1_CRUISE_CONTROL) {
      // SG_ CRUISE_CONTROL_SPA_ENABLED : 1|1@0+ (-1,1) — byte 0 bit 1, active low
      bool cruise_enabled = !((msg->data[0] >> 1) & 1U);
      pcm_cruise_check(cruise_enabled);
    }
  }

  // Party bus (bus 2) messages - BCM2, SAS, PSCM, EGSM
  if (msg->bus == VOLVO_PARTY_BUS) {

    if (msg->addr == VOLVO_PSCM) {
      // PSCM_ANGLE_SENSOR is the measurement consumed by carstate.py. It uses
      // the same signed 0.05596 deg/count representation as LCA_5_STEER.
      update_sample(&angle_meas, volvo_pscm_angle(msg));
    }

    // DRIVER_INPUT is the signal consumed by carstate.py for driver torque.
    // The PSCM frame's DRIVER_INPUT_DEVIATION is a different signal and must
    if (msg->addr == VOLVO_DRIVER_INPUT) {
      // STEERING_DRIVER_INPUT is a Motorola signal starting at bit 55. The
      // DBC also carries a +1 offset, so its raw byte is data[6].
      const int driver_input = to_signed(msg->data[6], 8) + 1;
      update_sample(&torque_driver, driver_input);
    }

  }
}

static bool volvo_cma_tx_hook(const CANPacket_t *msg) {
  static const AngleSteeringLimits VOLVO_ANGLE_STEERING_LIMITS = {
    .max_angle = VOLVO_MAX_ANGLE_CAN,
    .angle_deg_to_can = VOLVO_ANGLE_DEG_TO_CAN,
    .angle_rate_up_lookup = {
      {0.0f, 5.0f, 25.0f},
      {5.0f, 3.0f, 0.4f},
    },
    .angle_rate_down_lookup = {
      {0.0f, 5.0f, 25.0f},
      {10.0f, 4.0f, 0.6f},
    },
    .frequency = 50U,
  };

  bool tx = true;

  // LCA_5 carries the actual angle command used by the controller. The stock
  // LCA frame also contains an angle-shaped field, but the imported controller
  // deliberately leaves that field at the observed vehicle value.
  if (msg->addr == VOLVO_LCA_5) {
    const int desired_angle = volvo_lca_5_angle(msg);
    tx &= SAFETY_ABS(desired_angle) <= VOLVO_MAX_ANGLE_CAN;
    tx &= !steer_angle_cmd_checks(desired_angle, lateral_controls_allowed(), VOLVO_ANGLE_STEERING_LIMITS);
  }

  // Keep the two torque-authority arms and the companion LCA angle bounded even
  // though these fields are not the primary steering command.
  if (msg->addr == VOLVO_LCA_STEER) {
    const int authority_pos = to_signed(volvo_be_masked(msg, 0U, 0x07U), 11);
    const int authority_neg = to_signed(volvo_be_masked(msg, 2U, 0x07U), 11);
    const int lca_angle = to_signed(volvo_be_masked(msg, 5U, 0x3FU), 14);
    tx &= ((authority_pos >= 0) && (authority_pos <= 614));
    tx &= ((authority_neg >= -614) && (authority_neg <= 0));
    tx &= SAFETY_ABS(lca_angle) <= VOLVO_MAX_ANGLE_CAN;
  }

  // PSCM is relayed back onto the main bus to preserve the stock hands-on-wheel
  // path. Do not allow that relay to invent a steering-angle measurement.
  if (msg->addr == VOLVO_PSCM) {
    const int relayed_angle = volvo_pscm_angle(msg);
    const int measured_max = SAFETY_CLAMP(angle_meas.max + VOLVO_RELAY_ANGLE_TOLERANCE,
                                          -VOLVO_MAX_ANGLE_CAN, VOLVO_MAX_ANGLE_CAN);
    const int measured_min = SAFETY_CLAMP(angle_meas.min - VOLVO_RELAY_ANGLE_TOLERANCE,
                                          -VOLVO_MAX_ANGLE_CAN, VOLVO_MAX_ANGLE_CAN);
    tx &= !safety_max_limit_check(relayed_angle, measured_max, measured_min);
  }

  // NOTE: the wrong-bus rejections below are unreachable defense-in-depth:
  // safety_tx_hook() only calls this hook after the message passes the
  // VOLVO_TX_MSGS allowlist, which already pins each TX address to a single
  // bus (and VOLVO_DRIVER_INPUT/VOLVO_SAS are not TX'able on any bus).
  // Hence the GCOV_EXCL markers, following the defaults.h convention.
  // test_volvo.py's test_tx_hook_wrong_bus_blocked pins down the blocked
  // wrong-bus TX behavior at the safety_tx_hook() level.
  if (msg->addr == VOLVO_LCA_STEER) {
    // LCA message flows: VCU1 (main bus) -> PSCM (party bus)
    // We're acting as VCU1, so we send LCA message to party bus (bus 2)
    // GCOV_EXCL_START
    // Unreachable by design (allowlist pins VOLVO_LCA_STEER to the party bus)
    if (msg->bus != VOLVO_PARTY_BUS) {
      tx = false;  // Wrong bus
    }
    // GCOV_EXCL_STOP
  }

  if (msg->addr == VOLVO_PSCM) {
    // PSCM message: we relay from party bus (bus 2) to main bus (bus 0)
    // So we TX on main bus (bus 0)
    // GCOV_EXCL_START
    // Unreachable by design (allowlist pins VOLVO_PSCM to the main bus)
    if (msg->bus != VOLVO_MAIN_BUS) {
      tx = false;  // Wrong bus
    }
    // GCOV_EXCL_STOP
  }

  if (msg->addr == VOLVO_DRIVER_INPUT) {
    // Driver input message: we relay from party bus (bus 2) to main bus (bus 0)
    // So we TX on main bus (bus 0)
    // GCOV_EXCL_START
    // Unreachable by design (VOLVO_DRIVER_INPUT is not in VOLVO_TX_MSGS)
    if (msg->bus != VOLVO_MAIN_BUS) {
      tx = false;  // Wrong bus
    }
  }
  // GCOV_EXCL_STOP

  if (msg->addr == VOLVO_SAS) {
    // SAS message: we relay from party bus (bus 2) to main bus (bus 0)
    // So we TX on main bus (bus 0)
    // GCOV_EXCL_START
    // Unreachable by design (VOLVO_SAS is not in VOLVO_TX_MSGS)
    if (msg->bus != VOLVO_MAIN_BUS) {
      tx = false;  // Wrong bus
    }
  }
  // GCOV_EXCL_STOP

  if (msg->addr == VOLVO_LCA_2) {
    // LCA_2 -> PSCM
    // GCOV_EXCL_START
    // Unreachable by design (allowlist pins VOLVO_LCA_2 to the party bus)
    if (msg->bus != VOLVO_PARTY_BUS) {
      tx = false;  // Wrong bus
    }
    // GCOV_EXCL_STOP
  }

  // Only a replacement that is actually allowed onto the bus takes over from the
  // stock frame; a rejected one leaves the stock frame forwarding.
  if (tx) {
    const int index = volvo_replaced_msg_index(msg->addr);
    if (index >= 0) {
      volvo_replaced_tx_ts[index] = microsecond_timer_get();
      volvo_replaced_tx_seen[index] = true;
    }
  }

  return tx;
}

// Each replaced address is marked .disable_static_blocking in VOLVO_TX_MSGS so this
// hook decides forwarding instead of panda blocking it permanently from boot. See
// VOLVO_REPLACED_MSGS for the timing rationale.
static bool volvo_cma_fwd_hook(int bus_num, int addr) {
  bool block_msg = false;
  const int index = volvo_replaced_msg_index((uint32_t)addr);
  if ((index >= 0) && (VOLVO_REPLACED_MSGS[index].stock_bus == bus_num) && volvo_replaced_tx_seen[index]) {
    const uint32_t elapsed = safety_get_ts_elapsed(microsecond_timer_get(), volvo_replaced_tx_ts[index]);
    block_msg = elapsed <= VOLVO_REPLACED_MSGS[index].timeout_us;
  }

  return block_msg;
}

static safety_config volvo_cma_init(uint16_t param) {
  (void)param;
  for (uint8_t i = 0U; i < VOLVO_REPLACED_MSG_CNT; i++) {
    volvo_replaced_tx_seen[i] = false;
    volvo_replaced_tx_ts[i] = 0U;
  }

  // Define the TX messages needed to replace the stock LCA path. Payload
  // limits for steering and the PSCM relay are enforced in volvo_cma_tx_hook.
  // disable_static_blocking defers the forwarding decision to volvo_cma_fwd_hook, which
  // only blocks the stock version of each address while openpilot is recently
  // sending its replacement, instead of permanently from boot. LCA_6/LCA_7 are not
  // modified, so they're simply forwarded and not TX'd at all.
  static const CanMsg VOLVO_TX_MSGS[] = {
    {VOLVO_LCA_STEER, VOLVO_PARTY_BUS, 8, .check_relay = true, .disable_static_blocking = true},  // LCA steering command to party bus
    {VOLVO_PSCM, VOLVO_MAIN_BUS, 8, .check_relay = true, .disable_static_blocking = true},  // PSCM message sent to main bus (relay from party bus)
    {VOLVO_LCA_3, VOLVO_PARTY_BUS, 8, .check_relay = true, .disable_static_blocking = true},  // LCA_3 message sent to party bus
    {VOLVO_LCA_2, VOLVO_PARTY_BUS, 8, .check_relay = true, .disable_static_blocking = true},  // LCA_2 message sent to party bus (spoof PILOT_ASSIST_ENGAGED for PSCM)
    {VOLVO_LCA_4, VOLVO_PARTY_BUS, 8, .check_relay = true, .disable_static_blocking = true},  // LCA_4 message sent to party bus (spoof LCA_ENABLE for PA state)
    {VOLVO_LCA_5, VOLVO_PARTY_BUS, 8, .check_relay = true, .disable_static_blocking = true},  // LCA_5 message sent to party bus (wheel speeds + LCA signals)
    {VOLVO_PSCM_RELATED, VOLVO_MAIN_BUS, 8, .check_relay = true, .disable_static_blocking = true},  // PSCM_RELATED message sent to party bus
  };

  static RxCheck volvo_rx_checks_spa[] = {
    {.msg = {{VOLVO_GEAR_POSITION, VOLVO_MAIN_BUS, 8, 40U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    // LCA_2 carries the brake pedal: both checksums are validated (volvo_cma_integrity.h).
    {.msg = {{VOLVO_LCA_2, VOLVO_MAIN_BUS, 8, 50U, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_LCA_4, VOLVO_MAIN_BUS, 8, 29U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_LCA_6, VOLVO_MAIN_BUS, 8, 25U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_LCA_7, VOLVO_MAIN_BUS, 8, 29U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_SAS, VOLVO_PARTY_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_PSCM, VOLVO_PARTY_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_DRIVER_INPUT, VOLVO_PARTY_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_SPA_ECM_1, VOLVO_PT_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_LCA_STEER, VOLVO_MAIN_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    // true rate is 5Hz, but safety_tick invalidates checks declared <10Hz; lagging floor is 1s either way
    {.msg = {{VOLVO_SPA_BUS1_CRUISE_CONTROL, VOLVO_PT_BUS, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_LCA_3, VOLVO_MAIN_BUS, 8, 67U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_LCA_5, VOLVO_MAIN_BUS, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_SPEED, VOLVO_MAIN_BUS, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_SPEED_2, VOLVO_MAIN_BUS, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_EGSM, VOLVO_PARTY_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    // PSCM_RELATED: both checksums and the rack status (quality) are validated.
    {.msg = {{VOLVO_PSCM_RELATED, VOLVO_PARTY_BUS, 8, 100U, .ignore_counter = true}, { 0 }, { 0 }}},
  };
  return BUILD_SAFETY_CFG(volvo_rx_checks_spa, VOLVO_TX_MSGS);
}

const safety_hooks volvo_cma_hooks = {
  .init = volvo_cma_init,
  .rx = volvo_cma_rx_hook,
  .tx = volvo_cma_tx_hook,
  .fwd = volvo_cma_fwd_hook,
  .get_checksum = volvo_cma_get_checksum,
  .compute_checksum = volvo_cma_compute_checksum,
  .get_quality_flag_valid = volvo_cma_quality,
};
