#pragma once

#include <stddef.h>

#include "opendbc/safety/declarations.h"
#include "opendbc/safety/modes/volvo_cma_integrity.h"

// safetyParam: 0 = CMA (XC40 Recharge), 1 = SPA (S60 Recharge, Polestar 2),
// 2 = C1 (V40)
// Polestar 2 is technically CMA, but appears to use SPA DBC for CAN 1 bus
#define VOLVO_CMA_PORT_FLAG_SPA 1U

// Volvo CAN message addresses shared between CMA and SPA
#define VOLVO_CMA_PORT_LCA_STEER           0x58U    // TX from VCU1 to PSCM, LCA steering command (0x58)
#define VOLVO_CMA_PORT_LCA_2               0x69U   // RX from BCM, brake pedal, cruise state
#define VOLVO_CMA_PORT_SAS                 0x55U    // RX from SAS, steering angle sensor
#define VOLVO_CMA_PORT_PSCM                0x16U    // RX from PSCM, driver steering input
#define VOLVO_CMA_PORT_GEAR_POSITION       0x80U   // RX from transmission, gear position
#define VOLVO_CMA_PORT_DRIVER_INPUT        0x15U
#define VOLVO_CMA_PORT_LCA_3               0x57U   // TX from VCU1 to PSCM
#define VOLVO_CMA_PORT_LCA_5               0x67U   // TX LCA_5 message (formerly SPEED_1, contains wheel speeds and LCA signals)
#define VOLVO_CMA_PORT_SPEED               0x60U   // RX/TX SPEED message
#define VOLVO_CMA_PORT_SPEED_2             0x68U   // RX
#define VOLVO_CMA_PORT_0x1a                0x1aU   // RX
#define VOLVO_CMA_PORT_EGSM                0x45U   // RX from EGSM
#define VOLVO_CMA_PORT_PSCM_RELATED        0x17U   // RX from PSCM, related messages
#define VOLVO_CMA_PORT_LCA_4               0x90U   // TX LCA_4 message (PA status spoofing)
#define VOLVO_CMA_PORT_LCA_6               0x97U   // TX LCA_6 message
#define VOLVO_CMA_PORT_LCA_7               0x92U   // TX LCA_7 message

// C1-specific addresses (V40). The V40 powertrain bus is bus 0 and its
// forward-camera bus is bus 2; bus 1 is unused by this port.

// CMA-specific PT bus addresses
#define VOLVO_CMA_PORT_CMA_BUS1_SPEED          0x70U   // RX vehicle speed
#define VOLVO_CMA_PORT_CMA_ECM_1               0x250U  // RX accelerator pedal position
#define VOLVO_CMA_PORT_CMA_BUS1_CRUISE_CONTROL 0x340U  // RX cruise control state

// SPA-specific PT bus addresses
#define VOLVO_CMA_PORT_SPA_BUS1_SPEED          0x75U   // RX vehicle speed
#define VOLVO_CMA_PORT_SPA_ECM_1               0x25U   // RX accelerator pedal position
#define VOLVO_CMA_PORT_SPA_BUS1_CRUISE_CONTROL 0x349U  // RX cruise control state

// SPEED (0x60) is raw counts in the DBC. Measured against GPS ground speed on two
// harnesses: implied LSB 0.0039736 and 0.0039792 m/s.
#define VOLVO_CMA_PORT_SPEED_TO_MS 0.003977f

// LCA_5_STEER is a signed 15-bit steering-wheel-angle command in 0.05596 deg/count.
// Keep the absolute envelope aligned with the software controller's 540 deg limit.
#define VOLVO_CMA_PORT_ANGLE_DEG_TO_CAN 17.869907f
#define VOLVO_CMA_PORT_MAX_ANGLE_CAN 9650
#define VOLVO_CMA_PORT_RELAY_ANGLE_TOLERANCE 54  // approximately 3 degrees



// CAN bus definitions for Volvo
// Using same naming as carstate.py for consistency: main, pt, party
#define VOLVO_CMA_PORT_MAIN_BUS    0U  // Bus.main - VCU1 car side
#define VOLVO_CMA_PORT_PT_BUS      1U  // Bus.pt - VCU1 ECM side (where ECM is)
#define VOLVO_CMA_PORT_PARTY_BUS   2U  // Bus.party - VCU PSCM/BCM2 side (BCM2, SAS, EGSM, PSCM, where LCA is sent to)

// Runtime addresses set by volvo_cma_init based on safetyParam
static uint16_t volvo_cma_ecm_1_addr;
static uint16_t volvo_cma_bus1_cruise_control_addr;

static int volvo_cma_be_15(const CANPacket_t *msg, uint8_t byte) {
  const uint16_t raw = ((uint16_t)(msg->data[byte] & 0x7FU) << 8U) | msg->data[byte + 1U];
  return (int)raw;
}

static int volvo_cma_pscm_angle(const CANPacket_t *msg) {
  return to_signed(volvo_cma_be_15(msg, 0U), 15);
}

static int volvo_cma_lca_5_angle(const CANPacket_t *msg) {
  return to_signed(volvo_cma_be_15(msg, 6U), 15);
}












#define VOLVO_CMA_RELAY_COUNT 9U
static const uint32_t volvo_cma_relay_addr[VOLVO_CMA_RELAY_COUNT] = {0x58U, 0x57U, 0x69U, 0x90U, 0x67U, 0x97U, 0x92U, 0x16U, 0x17U};
#if defined(STM32H7)
#define VOLVO_CMA_RELAY_STORAGE __attribute__((section(".axisram")))
#else
#define VOLVO_CMA_RELAY_STORAGE
#endif
static uint8_t volvo_cma_relay_data[VOLVO_CMA_RELAY_COUNT][4][8] VOLVO_CMA_RELAY_STORAGE;
static uint32_t volvo_cma_relay_ts[VOLVO_CMA_RELAY_COUNT][4] VOLVO_CMA_RELAY_STORAGE;
static bool volvo_cma_relay_valid[VOLVO_CMA_RELAY_COUNT][4] VOLVO_CMA_RELAY_STORAGE;
static uint8_t volvo_cma_relay_index[VOLVO_CMA_RELAY_COUNT] VOLVO_CMA_RELAY_STORAGE;

static void volvo_cma_capture_stock(const CANPacket_t *msg) {
  for (uint8_t i = 0U; i < VOLVO_CMA_RELAY_COUNT; i++) {
    const uint8_t source_bus = (i < 7U) ? 0U : 2U;
    if ((msg->addr == volvo_cma_relay_addr[i]) && (msg->bus == source_bus) && (GET_LEN(msg) == 8U)) {
      const uint8_t slot = volvo_cma_relay_index[i];
      for (uint8_t b = 0U; b < 8U; b++) {
        volvo_cma_relay_data[i][slot][b] = msg->data[b];
      }
      volvo_cma_relay_ts[i][slot] = microsecond_timer_get();
      volvo_cma_relay_valid[i][slot] = true;
      volvo_cma_relay_index[i] = (slot + 1U) % 4U;
    }
  }
}
static bool volvo_cma_exact_stock(const CANPacket_t *msg) {
  bool matches = false;
  for (uint8_t i = 0U; i < VOLVO_CMA_RELAY_COUNT; i++) {
    if (msg->addr == volvo_cma_relay_addr[i]) {
      for (uint8_t j = 0U; j < 4U; j++) {
        bool same = volvo_cma_relay_valid[i][j] &&
          (safety_get_ts_elapsed(microsecond_timer_get(), volvo_cma_relay_ts[i][j]) <= 100000U);
        for (uint8_t b = 0U; b < 8U; b++) {
          same &= msg->data[b] == volvo_cma_relay_data[i][j][b];
        }
        matches |= same;
      }
    }
  }
  return matches;
}

static bool volvo_cma_gear_seen;
static bool volvo_cma_drive_gear;
static uint32_t volvo_cma_gear_ts;
static bool volvo_cma_drive_current(void) {
  return volvo_cma_gear_seen && volvo_cma_drive_gear &&
    (safety_get_ts_elapsed(microsecond_timer_get(), volvo_cma_gear_ts) <= 100000U);
}

static void volvo_cma_rx_hook(const CANPacket_t *msg) {
  volvo_cma_capture_stock(msg);
  if (msg_matches(msg, 0x80U, 0U, 8U)) {
    // Same GEAR_POSITION 58|3 Motorola field and P/R/N/D/B mapping as state.
    const uint8_t gear = msg->data[7] & 7U;
    volvo_cma_gear_seen = true;
    volvo_cma_drive_gear = (gear == 3U) || (gear == 4U);
    volvo_cma_gear_ts = microsecond_timer_get();
  }



  // Main bus (bus 0) messages
  if (msg->bus == VOLVO_CMA_PORT_MAIN_BUS) {


    // Update brake pedal and cruise state from BCM2
    if (msg->addr == VOLVO_CMA_PORT_LCA_2) {
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
    if (msg->addr == VOLVO_CMA_PORT_SPEED) {
      uint16_t speed_raw = ((msg->data[0] & 0x7FU) << 8) | msg->data[1];
      float speed = (float)speed_raw * VOLVO_CMA_PORT_SPEED_TO_MS;
      vehicle_moving = speed > 0.1;
      UPDATE_VEHICLE_SPEED(speed);
    }
  }

  // PT bus (bus 1) messages
  if (msg->bus == VOLVO_CMA_PORT_PT_BUS) {
    if (msg->addr == volvo_cma_ecm_1_addr) {
      if (volvo_cma_ecm_1_addr == VOLVO_CMA_PORT_CMA_ECM_1) {
        // CMA: SG_ ACCELERATOR_PEDAL_POS : 31|8@0+ (1,0) [0|255]
        uint8_t gas_pedal_position = msg->data[3];
        gas_pressed = gas_pedal_position > 21U; // 20 baseline + 1 tolerance
      } else {
        // SPA: SG_ ACCELERATOR_PEDAL_POS : 6|15@0+ (0.00390625,0) [0|32767] "%"
        uint16_t gas_raw = ((msg->data[0] & 0x7FU) << 8) | msg->data[1];
        gas_pressed = (gas_raw * 0.00390625) > 1.0; // > 1%
      }
    }

    if (msg->addr == volvo_cma_bus1_cruise_control_addr) {
      bool cruise_enabled;
      if (volvo_cma_bus1_cruise_control_addr == VOLVO_CMA_PORT_CMA_BUS1_CRUISE_CONTROL) {
        // CMA: SG_ CRUISE_CONTROL_ENABLED : 56|1@0+ and CRUISE_CONTROL_ENABLED_IDLE_TRAFFIC : 57|1@0+
        cruise_enabled = ((msg->data[7] & 1U) || (msg->data[7] & 2U));
      } else {
        // SPA: SG_ CRUISE_CONTROL_SPA_ENABLED : 1|1@0+ (-1,1) — byte 0 bit 1, active low
        cruise_enabled = !((msg->data[0] >> 1) & 1U);
      }
      pcm_cruise_check(cruise_enabled);
    }
  }

  // Party bus (bus 2) messages - BCM2, SAS, PSCM, EGSM
  if (msg->bus == VOLVO_CMA_PORT_PARTY_BUS) {

    if (msg->addr == VOLVO_CMA_PORT_PSCM) {
      // PSCM_ANGLE_SENSOR is the measurement consumed by carstate.py. It uses
      // the same signed 0.05596 deg/count representation as LCA_5_STEER.
      update_sample(&angle_meas, volvo_cma_pscm_angle(msg));
    }

    // DRIVER_INPUT is the signal consumed by carstate.py for driver torque.
    // The PSCM frame's DRIVER_INPUT_DEVIATION is a different signal and must
    if (msg->addr == VOLVO_CMA_PORT_DRIVER_INPUT) {
      // STEERING_DRIVER_INPUT is a Motorola signal starting at bit 55. The
      // DBC also carries a +1 offset, so its raw byte is data[6].
      const int driver_input = to_signed(msg->data[6], 8) + 1;
      update_sample(&torque_driver, driver_input);
    }

  }
}

// The two documented counters advance +2/+4 modulo16. Learn only their
// initial low-bit phases/relative offset from checksum-valid physical RX;
// neither host TX nor an invalid first packet can invent that receiver phase.
static bool volvo_cma_lca2_phase_valid;
static uint8_t volvo_cma_lca2_phase_one;
static uint8_t volvo_cma_lca2_phase_two;
static uint8_t volvo_cma_lca2_phase_offset;

static bool volvo_cma_lca2_mod15;
static bool volvo_cma_lca2_second_seen;
static uint8_t volvo_cma_lca2_second_last;
static bool volvo_cma_lca2_primary_seen;
static uint8_t volvo_cma_lca2_primary_last;
static uint8_t volvo_cma_lca2_primary_counter;
static int volvo_cma_lca2_second_wrong;
static bool volvo_cma_lca2_quality(const CANPacket_t *msg) {
  bool valid = true;
  if (msg->addr == 0x69U) {
    const uint8_t one = msg->data[1] & 0xFU;
    const uint8_t two = msg->data[5] & 0xFU;
    const bool checksum_valid = volvo_cma_get_checksum(msg) == volvo_cma_compute_checksum(msg);
    if (volvo_cma_lca2_mod15) {
      // Recorded Polestar counters are independent +2/+4 modulo15 clocks.
      // Keep the shared primary watchdog and the same tolerance for the secondary.
      const uint8_t counter = two;
      if ((msg->bus == 0U) && checksum_valid && (one < 15U) && (two < 15U)) {
        if (volvo_cma_lca2_second_seen) {
          const uint8_t delta = (counter + 15U - volvo_cma_lca2_second_last) % 15U;
          volvo_cma_lca2_second_wrong += ((delta >= 3U) && (delta <= 5U)) ? -1 : 1;
          volvo_cma_lca2_second_wrong = SAFETY_CLAMP(volvo_cma_lca2_second_wrong, 0, MAX_WRONG_COUNTERS);
        }
        volvo_cma_lca2_second_last = counter;
        volvo_cma_lca2_second_seen = true;
      }
      valid = checksum_valid && (one < 15U) && (two < 15U) &&
        (volvo_cma_lca2_second_wrong < MAX_WRONG_COUNTERS);
    } else {
      const uint8_t offset = ((two >> 2U) + 4U - ((one >> 1U) & 3U)) & 3U;
      if (!volvo_cma_lca2_phase_valid && (msg->bus == 0U) && checksum_valid) {
        volvo_cma_lca2_phase_one = one & 1U;
        volvo_cma_lca2_phase_two = two & 3U;
        volvo_cma_lca2_phase_offset = offset;
        volvo_cma_lca2_phase_valid = true;
      }
      valid = checksum_valid && volvo_cma_lca2_phase_valid &&
        ((one & 1U) == volvo_cma_lca2_phase_one) &&
        ((two & 3U) == volvo_cma_lca2_phase_two) &&
        (offset == volvo_cma_lca2_phase_offset);
    }
  }
  return valid;
}
static bool volvo_cma_receiver_quality(const CANPacket_t *msg) {
  return volvo_cma_quality(msg) && volvo_cma_lca2_quality(msg);
}

// The Polestar source clocks advance nominally2/4 per50Hz frame, with
// observed sampling jitter of1..3/3..5. Frozen/jumped counters retain the shared tolerance.
static uint8_t volvo_cma_lca2_counter(const CANPacket_t *msg) {
  const uint8_t raw = msg->data[1] & 0xFU;
  uint8_t counter = 0U;
  if (msg->addr == 0x69U) {
    counter = raw >> 1U;
    if (volvo_cma_lca2_mod15) {
      const uint8_t delta = (raw + 15U - volvo_cma_lca2_primary_last) % 15U;
      if (!volvo_cma_lca2_primary_seen || ((delta >= 1U) && (delta <= 3U))) {
        volvo_cma_lca2_primary_counter = (volvo_cma_lca2_primary_counter + 1U) % 15U;
      }
      volvo_cma_lca2_primary_last = raw;
      volvo_cma_lca2_primary_seen = true;
      counter = volvo_cma_lca2_primary_counter;
    }
  }
  return counter;
}

// Bind every non-mutable field to the native RX which supplied this exact
// receiver. This does not renew the original 100ms source age on host TX.
static bool volvo_cma_source_projection(const CANPacket_t *msg) {
  static const uint8_t mutable[9][8] = {
    {0xCFU, 0xFFU, 0x3FU, 0xFFU, 0xFFU, 0xC0U, 0x00U, 0xF0U},
    {0xFFU, 0xFFU, 0x00U, 0x00U, 0x00U, 0x00U, 0xFFU, 0x00U},
    {0xFFU, 0x1FU, 0xFFU, 0xFFU, 0xFFU, 0x3FU, 0xFFU, 0xFFU},
    {0x00U, 0x03U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0xFFU, 0xF0U, 0x00U, 0x00U, 0x7FU, 0xFFU},
    {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0xFFU, 0xFFU, 0x00U, 0x00U, 0x00U, 0x00U},
    {0xFFU, 0xFFU, 0x0FU, 0xFFU, 0x00U, 0x00U, 0x00U, 0x00U},
  };
  static const uint8_t fixed_mask[9][8] = {
    {0xC8U, 0x00U, 0x38U, 0x00U, 0xFFU, 0xC0U, 0x00U, 0xF0U},
    {0xFFU, 0xFFU, 0x00U, 0x00U, 0x00U, 0x00U, 0xCFU, 0x00U},
    {0xFFU, 0x10U, 0x00U, 0xFFU, 0xFFU, 0x30U, 0x00U, 0xFFU},
    {0x00U, 0x03U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
  };
  static const uint8_t fixed_value[9][8] = {
    {0xC0U, 0x00U, 0x38U, 0x00U, 0x50U, 0xC0U, 0x00U, 0xF0U},
    {0x1EU, 0xDEU, 0x00U, 0x00U, 0x00U, 0x00U, 0xC1U, 0x00U},
    {0x18U, 0x10U, 0x00U, 0x00U, 0x00U, 0x30U, 0x00U, 0x00U},
    {0x00U, 0x03U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U},
  };
  bool matched = volvo_cma_exact_stock(msg);
  if (!matched) {
    for (uint8_t i = 0U; i < VOLVO_CMA_RELAY_COUNT; i++) {
      if (msg->addr == volvo_cma_relay_addr[i]) {
        for (uint8_t j = 0U; j < 4U; j++) {
          bool same = volvo_cma_relay_valid[i][j] &&
            (safety_get_ts_elapsed(microsecond_timer_get(), volvo_cma_relay_ts[i][j]) <= 100000U);
          for (uint8_t b = 0U; b < 8U; b++) {
            same &= ((msg->data[b] ^ volvo_cma_relay_data[i][j][b]) & (uint8_t)~mutable[i][b]) == 0U;
            same &= (msg->data[b] & fixed_mask[i][b]) == fixed_value[i][b];
          }
          if (msg->addr == 0x17U) {
            const uint8_t echo = msg->data[1] & 0xFU;
            same &= (echo == 0U) || (echo == (volvo_cma_relay_data[i][j][1] & 0xFU));
          }
          matched |= same;
        }
      }
    }
    if (msg->addr == 0x16U) {
      matched &= ((msg->data[2] == 195U) && (msg->data[3] == 186U)) ||
                 ((msg->data[2] == 249U) && (msg->data[3] == 154U));
    }
  }
  return matched;
}

static bool volvo_cma_tx_hook(const CANPacket_t *msg) {
  static const AngleSteeringLimits VOLVO_CMA_PORT_ANGLE_STEERING_LIMITS = {
    .max_angle = VOLVO_CMA_PORT_MAX_ANGLE_CAN,
    .angle_deg_to_can = VOLVO_CMA_PORT_ANGLE_DEG_TO_CAN,
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
  // With native control withdrawn, only a recently received exact stock frame
  // can cross the harness. No host assertion, MAIN value, or companion spoof grants permission.
  // This shared helper reads actual RX status/age now; it grants no AOL
  // policy or intent and retains the existing health deadlines/tolerances.
  bool tx;
  if (!controls_allowed || !volvo_cma_drive_current() || !aol_rx_healthy()) {
    tx = volvo_cma_exact_stock(msg);
  } else {
    tx = volvo_cma_receiver_quality(msg) && volvo_cma_source_projection(msg);
    if ((msg->addr == 0x69U) || (msg->addr == 0x67U) || (msg->addr == 0x17U)) {
      tx &= volvo_cma_get_checksum(msg) == volvo_cma_compute_checksum(msg);
    }



    // LCA_5 carries the actual angle command used by the controller. The stock
    // LCA frame also contains an angle-shaped field, but the imported controller
    // deliberately leaves that field at the observed vehicle value.
    if (msg->addr == VOLVO_CMA_PORT_LCA_5) {
      if (volvo_cma_exact_stock(msg)) {
        desired_angle_last = SAFETY_CLAMP(angle_meas.values[0], -VOLVO_CMA_PORT_MAX_ANGLE_CAN, VOLVO_CMA_PORT_MAX_ANGLE_CAN);
      } else {
        const int desired_angle = volvo_cma_lca_5_angle(msg);
        tx &= SAFETY_ABS(desired_angle) <= VOLVO_CMA_PORT_MAX_ANGLE_CAN;
        tx &= !steer_angle_cmd_checks(desired_angle, controls_allowed, VOLVO_CMA_PORT_ANGLE_STEERING_LIMITS);
      }
    }

    // Keep the two torque-authority arms and the companion LCA angle bounded even
    // though these fields are not the primary steering command.
    if (msg->addr == VOLVO_CMA_PORT_LCA_STEER) {
      const uint16_t authority_pos_raw = ((uint16_t)(msg->data[0] & 0x07U) << 8U) | msg->data[1];
      const int authority_pos = to_signed((int)authority_pos_raw, 11);
      const uint16_t authority_neg_raw = ((uint16_t)(msg->data[2] & 0x07U) << 8U) | msg->data[3];
      const int authority_neg = to_signed((int)authority_neg_raw, 11);
      const uint16_t lca_angle_raw = ((uint16_t)(msg->data[5] & 0x3FU) << 8U) | msg->data[6];
      const int lca_angle = to_signed((int)lca_angle_raw, 14);
      tx &= (authority_pos >= 0) && (authority_pos <= 614);
      tx &= (authority_neg >= -614) && (authority_neg <= 0);
      tx &= SAFETY_ABS(lca_angle) <= VOLVO_CMA_PORT_MAX_ANGLE_CAN;
    }

    // PSCM is relayed back onto the main bus to preserve the stock hands-on-wheel
    // path. Do not allow that relay to invent a steering-angle measurement.
    if (msg->addr == VOLVO_CMA_PORT_PSCM) {
      const int relayed_angle = volvo_cma_pscm_angle(msg);
      const int measured_max = SAFETY_CLAMP(angle_meas.max + VOLVO_CMA_PORT_RELAY_ANGLE_TOLERANCE,
                                            -VOLVO_CMA_PORT_MAX_ANGLE_CAN, VOLVO_CMA_PORT_MAX_ANGLE_CAN);
      const int measured_min = SAFETY_CLAMP(angle_meas.min - VOLVO_CMA_PORT_RELAY_ANGLE_TOLERANCE,
                                            -VOLVO_CMA_PORT_MAX_ANGLE_CAN, VOLVO_CMA_PORT_MAX_ANGLE_CAN);
      tx &= !safety_max_limit_check(relayed_angle, measured_max, measured_min);
    }

    // NOTE: the wrong-bus rejections below are unreachable defense-in-depth:
    // safety_tx_hook() only calls this hook after the message passes the
    // VOLVO_CMA_PORT_TX_MSGS allowlist, which already pins each TX address to a single
    // bus (and VOLVO_CMA_PORT_DRIVER_INPUT/VOLVO_CMA_PORT_SAS are not TX'able on any bus).
    // Hence the GCOV_EXCL markers, following the defaults.h convention.
    // test_volvo.py's test_tx_hook_wrong_bus_blocked pins down the blocked
    // wrong-bus TX behavior at the safety_tx_hook() level.
    if (msg->addr == VOLVO_CMA_PORT_LCA_STEER) {
      // LCA message flows: VCU1 (main bus) -> PSCM (party bus)
      // We're acting as VCU1, so we send LCA message to party bus (bus 2)
      // GCOV_EXCL_START
      // Unreachable by design (allowlist pins VOLVO_CMA_PORT_LCA_STEER to the party bus)
      if (msg->bus != VOLVO_CMA_PORT_PARTY_BUS) {
        tx = false;  // Wrong bus
      }
      // GCOV_EXCL_STOP
    }

    if (msg->addr == VOLVO_CMA_PORT_PSCM) {
      // PSCM message: we relay from party bus (bus 2) to main bus (bus 0)
      // So we TX on main bus (bus 0)
      // GCOV_EXCL_START
      // Unreachable by design (allowlist pins VOLVO_CMA_PORT_PSCM to the main bus)
      if (msg->bus != VOLVO_CMA_PORT_MAIN_BUS) {
        tx = false;  // Wrong bus
      }
      // GCOV_EXCL_STOP
    }

    if (msg->addr == VOLVO_CMA_PORT_DRIVER_INPUT) {
      // Driver input message: we relay from party bus (bus 2) to main bus (bus 0)
      // So we TX on main bus (bus 0)
      // GCOV_EXCL_START
      // Unreachable by design (VOLVO_CMA_PORT_DRIVER_INPUT is not in VOLVO_CMA_PORT_TX_MSGS)
      if (msg->bus != VOLVO_CMA_PORT_MAIN_BUS) {
        tx = false;  // Wrong bus
      }
    }
    // GCOV_EXCL_STOP

    if (msg->addr == VOLVO_CMA_PORT_SAS) {
      // SAS message: we relay from party bus (bus 2) to main bus (bus 0)
      // So we TX on main bus (bus 0)
      // GCOV_EXCL_START
      // Unreachable by design (VOLVO_CMA_PORT_SAS is not in VOLVO_CMA_PORT_TX_MSGS)
      if (msg->bus != VOLVO_CMA_PORT_MAIN_BUS) {
        tx = false;  // Wrong bus
      }
    }
    // GCOV_EXCL_STOP

    if (msg->addr == VOLVO_CMA_PORT_LCA_2) {
      // LCA_2 -> PSCM
      // GCOV_EXCL_START
      // Unreachable by design (allowlist pins VOLVO_CMA_PORT_LCA_2 to the party bus)
      if (msg->bus != VOLVO_CMA_PORT_PARTY_BUS) {
        tx = false;  // Wrong bus
      }
      // GCOV_EXCL_STOP
    }

  }
  return tx;
}

// Only one Volvo profile is active at a time. Keep immutable descriptors in
// flash and explicitly initialize the selected mutable RX workspace on every
// mode entry. H7 already reserves AXI SRAM for native CAN state; its NOLOAD
// section must never be assumed to contain startup zeroes.
#define VOLVO_CMA_RX_CHECK_COUNT 17U


static RxCheck *volvo_cma_rx_checks_workspace(void) {
#if defined(STM32H7)
  static RxCheck volvo_cma_rx_workspace[VOLVO_CMA_RX_CHECK_COUNT] __attribute__((section(".axisram")));
#else
  static RxCheck volvo_cma_rx_workspace[VOLVO_CMA_RX_CHECK_COUNT];
#endif
  return volvo_cma_rx_workspace;
}

static safety_config volvo_cma_init(uint16_t param) {
  RxCheck *const rx_workspace = volvo_cma_rx_checks_workspace();
  volvo_cma_lca2_mod15 = param == 5U;
  volvo_cma_lca2_second_seen = false;
  volvo_cma_lca2_second_last = 0U;
  volvo_cma_lca2_second_wrong = 0;
  volvo_cma_lca2_primary_seen = false;
  volvo_cma_lca2_primary_last = 0U;
  volvo_cma_lca2_primary_counter = 0U;
  volvo_cma_lca2_phase_valid = false;
  volvo_cma_lca2_phase_one = 0U;
  volvo_cma_lca2_phase_two = 0U;
  volvo_cma_lca2_phase_offset = 0U;
  volvo_cma_gear_seen = false;
  volvo_cma_drive_gear = false;
  volvo_cma_gear_ts = 0U;
  bool spa = GET_FLAG(param, VOLVO_CMA_PORT_FLAG_SPA);

  for (uint8_t i = 0U; i < VOLVO_CMA_RELAY_COUNT; i++) {
    volvo_cma_relay_index[i] = 0U;
    for (uint8_t j = 0U; j < 4U; j++) {
      volvo_cma_relay_valid[i][j] = false;
      volvo_cma_relay_ts[i][j] = 0U;
      for (uint8_t b = 0U; b < 8U; b++) {
        volvo_cma_relay_data[i][j][b] = 0U;
      }
    }
  }

  // Set PT bus addresses based on platform
  volvo_cma_ecm_1_addr = spa ? VOLVO_CMA_PORT_SPA_ECM_1 : VOLVO_CMA_PORT_CMA_ECM_1;
  volvo_cma_bus1_cruise_control_addr = spa ? VOLVO_CMA_PORT_SPA_BUS1_CRUISE_CONTROL : VOLVO_CMA_PORT_CMA_BUS1_CRUISE_CONTROL;

  // Define the TX messages needed to replace the stock LCA path. Payload
  // limits for steering and the PSCM relay are enforced in volvo_cma_tx_hook.
  static const CanMsg VOLVO_CMA_PORT_TX_MSGS[] = {
    {VOLVO_CMA_PORT_LCA_STEER, VOLVO_CMA_PORT_PARTY_BUS, 8, .check_relay = true},  // LCA steering command to party bus
    {VOLVO_CMA_PORT_PSCM, VOLVO_CMA_PORT_MAIN_BUS, 8, .check_relay = true},  // PSCM message sent to main bus (relay from party bus)
    {VOLVO_CMA_PORT_LCA_3, VOLVO_CMA_PORT_PARTY_BUS, 8, .check_relay = true},  // LCA_3 message sent to party bus
    {VOLVO_CMA_PORT_LCA_2, VOLVO_CMA_PORT_PARTY_BUS, 8, .check_relay = true},  // LCA_2 message sent to party bus (spoof PILOT_ASSIST_ENGAGED for PSCM)
    {VOLVO_CMA_PORT_LCA_4, VOLVO_CMA_PORT_PARTY_BUS, 8, .check_relay = true},  // LCA_4 message sent to party bus (spoof LCA_ENABLE for PA state)
    {VOLVO_CMA_PORT_LCA_5, VOLVO_CMA_PORT_PARTY_BUS, 8, .check_relay = true},  // LCA_5 message sent to party bus (wheel speeds + LCA signals)
    {VOLVO_CMA_PORT_LCA_6, VOLVO_CMA_PORT_PARTY_BUS, 8, .check_relay = true},  // LCA_6 message sent to party bus
    {VOLVO_CMA_PORT_LCA_7, VOLVO_CMA_PORT_PARTY_BUS, 8, .check_relay = true},  // LCA_7 message sent to party bus
    //{VOLVO_CMA_PORT_SPEED, VOLVO_CMA_PORT_PARTY_BUS, 8, .check_relay = true},  // SPEED message sent to main bus
    //{VOLVO_CMA_PORT_SPEED_2, VOLVO_CMA_PORT_PARTY_BUS, 8, .check_relay = true},  // SPEED_2 message sent to main bus
    //{VOLVO_CMA_PORT_0x1a, VOLVO_CMA_PORT_PARTY_BUS, 8, .check_relay = true},  // 0x1a message sent to main bus
    //{VOLVO_CMA_PORT_GEAR_POSITION, VOLVO_CMA_PORT_PARTY_BUS, 8, .check_relay = true},  // GEAR_POSITION message sent from main to party bus
    //{VOLVO_CMA_PORT_EGSM, VOLVO_CMA_PORT_MAIN_BUS, 8, .check_relay = true},  // EGSM message sent from party to main bus
    {VOLVO_CMA_PORT_PSCM_RELATED, VOLVO_CMA_PORT_MAIN_BUS, 8, .check_relay = true},  // PSCM_RELATED message sent to party bus
  };

  // Define RX checks - PT bus addresses depend on CMA vs SPA
  const RxCheck *selected;
  if (!spa) {
    static const RxCheck volvo_cma_rx_checks_cma[] = {
      {.msg = {{VOLVO_CMA_PORT_GEAR_POSITION, VOLVO_CMA_PORT_MAIN_BUS, 8, 40U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_2, VOLVO_CMA_PORT_MAIN_BUS, 8, 50U, .ignore_checksum = false, .ignore_counter = false, .max_counter = 7U, .ignore_quality_flag = false}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_4, VOLVO_CMA_PORT_MAIN_BUS, 8, 29U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_6, VOLVO_CMA_PORT_MAIN_BUS, 8, 25U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_7, VOLVO_CMA_PORT_MAIN_BUS, 8, 29U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_SAS, VOLVO_CMA_PORT_PARTY_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_PSCM, VOLVO_CMA_PORT_PARTY_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_DRIVER_INPUT, VOLVO_CMA_PORT_PARTY_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_CMA_ECM_1, VOLVO_CMA_PORT_PT_BUS, 8, 17U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_STEER, VOLVO_CMA_PORT_MAIN_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_CMA_BUS1_CRUISE_CONTROL, VOLVO_CMA_PORT_PT_BUS, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_3, VOLVO_CMA_PORT_MAIN_BUS, 8, 67U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_5, VOLVO_CMA_PORT_MAIN_BUS, 8, 50U, .ignore_checksum = false, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_SPEED, VOLVO_CMA_PORT_MAIN_BUS, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_SPEED_2, VOLVO_CMA_PORT_MAIN_BUS, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_EGSM, VOLVO_CMA_PORT_PARTY_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_PSCM_RELATED, VOLVO_CMA_PORT_PARTY_BUS, 8, 100U, .ignore_checksum = false, .ignore_counter = true, .ignore_quality_flag = false}, { 0 }, { 0 }}},
    };
    _Static_assert((sizeof(volvo_cma_rx_checks_cma) / sizeof(volvo_cma_rx_checks_cma[0])) == VOLVO_CMA_RX_CHECK_COUNT, "CMA RX workspace size");
    selected = volvo_cma_rx_checks_cma;
  } else {
    static const RxCheck volvo_cma_rx_checks_spa[] = {
      {.msg = {{VOLVO_CMA_PORT_GEAR_POSITION, VOLVO_CMA_PORT_MAIN_BUS, 8, 40U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_2, VOLVO_CMA_PORT_MAIN_BUS, 8, 50U, .ignore_checksum = false, .ignore_counter = false, .max_counter = 7U, .ignore_quality_flag = false}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_4, VOLVO_CMA_PORT_MAIN_BUS, 8, 29U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_6, VOLVO_CMA_PORT_MAIN_BUS, 8, 25U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_7, VOLVO_CMA_PORT_MAIN_BUS, 8, 29U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_SAS, VOLVO_CMA_PORT_PARTY_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_PSCM, VOLVO_CMA_PORT_PARTY_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_DRIVER_INPUT, VOLVO_CMA_PORT_PARTY_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_SPA_ECM_1, VOLVO_CMA_PORT_PT_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_STEER, VOLVO_CMA_PORT_MAIN_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      // true rate is 5Hz, but safety_tick invalidates checks declared <10Hz; lagging floor is 1s either way
      {.msg = {{VOLVO_CMA_PORT_SPA_BUS1_CRUISE_CONTROL, VOLVO_CMA_PORT_PT_BUS, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_3, VOLVO_CMA_PORT_MAIN_BUS, 8, 67U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_LCA_5, VOLVO_CMA_PORT_MAIN_BUS, 8, 50U, .ignore_checksum = false, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_SPEED, VOLVO_CMA_PORT_MAIN_BUS, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_SPEED_2, VOLVO_CMA_PORT_MAIN_BUS, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_EGSM, VOLVO_CMA_PORT_PARTY_BUS, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{VOLVO_CMA_PORT_PSCM_RELATED, VOLVO_CMA_PORT_PARTY_BUS, 8, 100U, .ignore_checksum = false, .ignore_counter = true, .ignore_quality_flag = false}, { 0 }, { 0 }}},
    };
    _Static_assert((sizeof(volvo_cma_rx_checks_spa) / sizeof(volvo_cma_rx_checks_spa[0])) == VOLVO_CMA_RX_CHECK_COUNT, "SPA RX workspace size");
    selected = volvo_cma_rx_checks_spa;
  }
  for (uint8_t i = 0U; i < VOLVO_CMA_RX_CHECK_COUNT; i++) {
    rx_workspace[i] = selected[i];
    if (volvo_cma_lca2_mod15 && (rx_workspace[i].msg[0].addr == (unsigned int)VOLVO_CMA_PORT_LCA_2)) {
      rx_workspace[i].msg[0].max_counter = 14U;
    }
  }
  const size_t tx_msgs_count = sizeof(VOLVO_CMA_PORT_TX_MSGS) / sizeof(VOLVO_CMA_PORT_TX_MSGS[0]);
  const safety_config config = {
    .rx_checks = rx_workspace,
    .rx_checks_len = (int)VOLVO_CMA_RX_CHECK_COUNT,
    .tx_msgs = VOLVO_CMA_PORT_TX_MSGS,
    .tx_msgs_len = (int)tx_msgs_count,
    .disable_forwarding = false,
  };
  return config;
}

const safety_hooks volvo_cma_hooks = {
  .init = volvo_cma_init,
  .rx = volvo_cma_rx_hook,
  .tx = volvo_cma_tx_hook,
  .get_counter = volvo_cma_lca2_counter,
  .get_checksum = volvo_cma_get_checksum,
  .compute_checksum = volvo_cma_compute_checksum,
  .get_quality_flag_valid = volvo_cma_receiver_quality,
  // No custom fwd hook - stock LCA always blocked by .check_relay = true
};
