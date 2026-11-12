#pragma once

#include "opendbc/safety/declarations.h"

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

#include "opendbc/safety/modes/mazda_stock_aol.h"

// GEN2 ACC isolation: the last two stock ACC frames as received on the vehicle
// side of the relay. The target car's drive shows the host relaying the frame one
// period behind the newest in up to 61% of frames (0.3-61% by session), so both
// qualify while fresh. Relays further behind were seen 3 times in ~67k relayed frames;
// rejecting them as replays costs that frame. Index 0 is the newest.
#define MAZDA_STOCK_ACC_FRESHNESS_US 200000U
#define MAZDA_STOCK_ACC_REFS 2
static uint8_t mazda_stock_acc[MAZDA_STOCK_ACC_REFS][8] = {{0}};
static bool mazda_stock_acc_valid[MAZDA_STOCK_ACC_REFS] = {false};
static uint32_t mazda_stock_acc_ts[MAZDA_STOCK_ACC_REFS] = {0U};

// Modified 0x220 contract (safety plan 5.2). Only ACCEL_CMD (d2 bit 0, d3, d4
// bits 7..5), HOLD (d6 bit 1) and RESUME (d6 bit 6) may differ from a fresh stock
// frame; every other bit, including byte 0's rolling value and any AEB field,
// must match it. ACCEL_CMD = 2000 + 200 * accel was confirmed against measured
// acceleration on the target car (reports/mazda-gen2-aol-gate-report.md s24).
// The bounds encode the host's ACCEL_MIN/ACCEL_MAX (-3.5/+2.0 m/s^2).
static const uint8_t MAZDA_ACC_APPROVED[7] = {0x00U, 0x00U, 0x01U, 0xFFU, 0xE0U, 0x00U, 0x42U};
#define MAZDA_ACC_ACCEL_MIN 1300
#define MAZDA_ACC_ACCEL_MAX 2400
// HOLD/RESUME need current standstill evidence, not vehicle_moving's initial
// false value. Match the existing 300 ms GEN2 source freshness in stock-AOL.
#define MAZDA_SPEED_FRESHNESS_US 300000U
static bool mazda_speed_seen = false;
static uint32_t mazda_speed_ts = 0U;

// TI2 fault contract: 0x24B is a required 50 Hz message for GEN2/GEN3. Silence
// beyond this window is a fault, not a gap, and revokes actuation on its own.
#define MAZDA_TI_FEEDBACK_TIMEOUT_US 200000U
// Safety action plan phase 4.1 item 6: several consecutive valid, healthy frames
// before unhealthy status clears. The target capture delivers 0x24B at about
// 100 Hz, so ten frames is about 100 ms there. Keep this rule frame-based rather
// than assuming that cadence for other firmware.
#define MAZDA_TI_RECOVERY_FRAMES 10U
// CPU operating state that counts as healthy. The port documents
// DISCOVER=0, OFF=1, DRIVER_OVER=2, RUN=3 (values.py TI_STATE) and a parked
// capture with the interceptor running shows 3 on all four CPUs. Safety plan
// 4.1 item 3 and "unknown TI states are not treated as healthy": every other
// value is unhealthy. An ignition-cycle capture showed 0, 2 and 5 only as
// power-up transients, and violations 1/2/51/83 decaying to 0 within ~20 s of a
// restart; state 1 (OFF) was never seen because the unit stops transmitting when
// unpowered. DRIVER_OVER as a real override is still unverified (bench).
#define MAZDA_TI_STATE_RUN 3U
static bool mazda_ti_feedback_seen = false;
static uint32_t mazda_ti_feedback_ts = 0U;
// Latest verified TI2 fault state. It can flicker back to clear within 20 ms of
// a real recovery, so the tx hook consumes it directly rather than relying only
// on controls_allowed having been cleared.
static bool mazda_ti_fault = false;
static uint8_t mazda_ti_clean_frames = 0U;
// Latch: once a fault is seen, actuation stays revoked until a clean frame
// arrives while the host has already let go. A fault that clears while a cruise
// edge has re-armed controls must not silently restore authority.
static bool mazda_ti_latch = false;
// TI2 violation 0x02 is a self-clearing event, not a failure. In every capture
// all four CPUs report it together, stay in RUN, and clear it within 17-20 s
// (after an engine start, and as the vehicle reaches a standstill). It pauses
// lateral torque only: health, the latch, controls_allowed and the AOL token are
// untouched. A pause can only begin from proven health (mazda_ti_unhealthy()
// false), and an episode that outlasts MAZDA_TI_PAUSE_MAX_US becomes a latched
// fault. A new 0x02 before a full recovery continues the same episode, so a
// flickering code cannot extend the cap. CarState.update_gen2 mirrors this model;
// test_mazda_gen2_ti2_parity.py keeps the two in step.
#define MAZDA_TI_VIOL_TRANSIENT 0x02U
#define MAZDA_TI_PAUSE_MAX_US 30000000U
static bool mazda_ti_pause = false;
static uint32_t mazda_ti_pause_ts = 0U;
static uint8_t mazda_ti_pause_clean_frames = MAZDA_TI_RECOVERY_FRAMES;

// Lateral torque is withheld during a 0x02 pause and until MAZDA_TI_RECOVERY_FRAMES
// consecutive clean frames have followed it.
static bool mazda_ti_lateral_paused(void) {
  return (mazda_gen2 || mazda_gen3) &&
    (mazda_ti_pause || (mazda_ti_pause_clean_frames < MAZDA_TI_RECOVERY_FRAMES));
}

static void mazda_ti_end_pause(void) {
  mazda_ti_pause = false;
  mazda_ti_pause_clean_frames = MAZDA_TI_RECOVERY_FRAMES;
}

// GEN1 Torque Interceptor (TI1). Separate from the TI2 state above, which is
// GEN2/GEN3 only. TI_FEEDBACK (0x24A, bus 1): byte 2 VERSION_NUMBER, byte 3
// STATE (TI_STATE), byte 4 VIOL, byte 6 RAMP_DOWN (firmware version > 1).
// A TI1 violation or a state other than RUN withholds nonzero 0x249 torque; it
// does not touch controls_allowed. Class 2/3 violations clear by themselves, and
// the stock 0x243 path keeps its own checks. Host: CarState.ti_lkas_allowed.
static bool mazda_ti1_feedback_seen = false;
static uint32_t mazda_ti1_feedback_ts = 0U;
static uint8_t mazda_ti1_state = 0U;
static uint8_t mazda_ti1_viol = 0U;
static bool mazda_ti1_ramp_down = false;

// CAM_LKAS2 (0x249, bus 1) for the TI1: LKAS_REQUEST and its copy CHKSUM are
// 12-bit (torque + 2048) in bytes 0-1 and 2-3; bytes 4-7 carry a fixed KEY.
static const uint8_t MAZDA_TI1_KEY[4] = {0xC4U, 0x61U, 0xCEU, 0x60U};

// With the camera present the host sends 0x243 and then 0x249 in the same frame.
// The tx hook records the 0x243 torque it accepted; a nonzero 0x249 must have its
// sign and at most its magnitude, so the TI1 is bounded by every 0x243 check (max,
// rate, driver torque, controls). Each 0x243 pairs with at most one 0x249, within
// a short window. The TI1 channel is also rate-limited on its own from the last
// command it was sent (mazda_ti1_last), so after an override or a fault it ramps
// up from zero instead of jumping to the stock command.
#define MAZDA_TI1_PAIR_WINDOW_US 20000U
static bool mazda_ti1_pair_pending = false;
static int mazda_ti1_pair_torque = 0;
static uint32_t mazda_ti1_pair_ts = 0U;
static int mazda_ti1_last = 0;

static bool mazda_ti1_healthy(void) {
  return mazda_ti1_feedback_seen && (mazda_ti1_state == MAZDA_TI_STATE_RUN) && (mazda_ti1_viol == 0U) &&
    !mazda_ti1_ramp_down &&
    (safety_get_ts_elapsed(microsecond_timer_get(), mazda_ti1_feedback_ts) <= MAZDA_TI_FEEDBACK_TIMEOUT_US);
}

// ACC integrity: 0x220 carries a checksum in byte 7 (seed 0x2a over bytes 0..6).
static bool mazda_acc_checksum_valid(const uint8_t *data) {
  uint8_t sum = 0x2AU;
  for (int i = 0; i < 7; i++) {
    sum += data[i];
  }
  return data[7] == sum;
}


// Interceptor health: no feedback yet, a verified violation, a latched fault, or
// feedback older than the required 50 Hz window. This is the only definition; the
// rx hook and the tx hook must agree on it.
static bool mazda_ti_unhealthy(void) {
  return (mazda_gen2 || mazda_gen3) &&
    (!mazda_ti_feedback_seen || mazda_ti_fault || mazda_ti_latch ||
     (mazda_ti_clean_frames < MAZDA_TI_RECOVERY_FRAMES) ||
     (safety_get_ts_elapsed(microsecond_timer_get(), mazda_ti_feedback_ts) > MAZDA_TI_FEEDBACK_TIMEOUT_US));
}

// track msgs coming from OP so that we know what CAM msgs to drop and what to forward
static void mazda_rx_hook(const CANPacket_t *msg) {
  mazda_aol_rx(msg);

  // Health before this frame: used to detect unhealthy -> healthy transitions,
  // where an authorization acquired during the unhealthy interval is not valid.
  const bool ti_health_before = !mazda_ti_unhealthy();

  // TI2 fault contract: stale interceptor feedback revokes actuation without
  // waiting for the host. Never re-enables; the driver must authorize again.
  // Judged against the previous feedback frame before this frame is processed,
  // including when this frame is itself the returning feedback: a gap is a fault
  // and a latch, so health needs the full recovery, and a returning 0x02 is not
  // admitted as a pause.
  if ((mazda_gen2 || mazda_gen3) && mazda_ti_feedback_seen &&
      (safety_get_ts_elapsed(microsecond_timer_get(), mazda_ti_feedback_ts) > MAZDA_TI_FEEDBACK_TIMEOUT_US)) {
    mazda_ti_fault = true;
    mazda_ti_latch = true;
    mazda_ti_clean_frames = 0U;
    controls_allowed = false;
    mazda_ti_end_pause();
  }

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


      // CRZ_STATE (byte 0 bits 6..4) is the GEN2 ACC main switch. mazda_2019.dbc
      // defines 0 = CRUISE_DISABLED, 1 = CRUISE_READY, 2 = CRUISE_ENABLED and
      // 4 = GAS_OVERRIDE; 3 and 5..7 are undefined. Treating "anything but 0" as
      // main-on is the conservative reading for an undefined code.
      // Evidence: a parked capture in which the physical switch produced 16
      // alternating 0<->1 transitions, each 19-162 ms after a CRZ_BTNS MODE_ACC
      // edge (reports/mazda-gen2-aol-gate-report.md section 8). It does not
      // establish the engaged or override states.
      if (msg->addr == MAZDA_2019_CRUISE) {
        acc_main_on = (msg->data[0] & 0x70U) != 0U;
        bool cruise_engaged = msg->data[0] & 0x20U;
        bool pre_enable = msg->data[0] & 0x40U;
        const bool was_allowed = controls_allowed;
        pcm_cruise_check((cruise_engaged || pre_enable));
        // GEN2 must feed the same accepted-cruise observation the GEN1 path
        // provides, or AOL's neutral/grant sequencing never sees engagement.
        mazda_aol_engagement(msg, cruise_engaged || pre_enable, !was_allowed && controls_allowed);
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

      mazda_ti1_feedback_seen = true;
      mazda_ti1_feedback_ts = microsecond_timer_get();
      mazda_ti1_state = msg->data[3];
      mazda_ti1_viol = msg->data[4];
      mazda_ti1_ramp_down = (msg->data[2] > 1U) && (msg->data[6] != 0U);
    }

    // enter controls on rising edge of ACC, exit controls on ACC off
    if (msg->addr == MAZDA_CRZ_CTRL) {
      bool cruise_engaged = msg->data[0] & 0x8U;
      pcm_cruise_check(cruise_engaged);

      acc_main_on = GET_BIT(msg, 17U);
    }

    if (msg->addr == MAZDA_2019_STEER_TORQUE && (mazda_gen2 || mazda_gen3)) {
      update_sample(&torque_driver, (int16_t)(msg->data[0] << 8 | msg->data[1]));
      mazda_ti_feedback_seen = true;
      mazda_ti_feedback_ts = microsecond_timer_get();

      // TI2 fault contract: a CPU violation revokes actuation here, without
      // waiting for the host. CPU_n_VIOL are whole bytes 2..5 in both the GEN2
      // and GEN3 DBCs. Faults never re-enable: the driver must authorize again.
      // The single exception is a 0x02-only pause (see MAZDA_TI_VIOL_TRANSIENT).
      bool ti_violation = false;
      bool ti_other_violation = false;
      for (int i = 2; i < 6; i++) {
        ti_violation = ti_violation || (msg->data[i] != 0U);
        ti_other_violation = ti_other_violation ||
          ((msg->data[i] != 0U) && (msg->data[i] != MAZDA_TI_VIOL_TRANSIENT));
      }
      const bool ti_states_ok = (((msg->data[6] >> 4) & 0xFU) == MAZDA_TI_STATE_RUN) &&
                                ((msg->data[6] & 0xFU) == MAZDA_TI_STATE_RUN) &&
                                (((msg->data[7] >> 4) & 0xFU) == MAZDA_TI_STATE_RUN) &&
                                ((msg->data[7] & 0xFU) == MAZDA_TI_STATE_RUN);
      // Judged on state before this frame: a pause may only start from health.
      const bool pause_eligible = mazda_ti_pause || !mazda_ti_unhealthy();
      const bool pause_expired = mazda_ti_lateral_paused() &&
        (safety_get_ts_elapsed(mazda_ti_feedback_ts, mazda_ti_pause_ts) > MAZDA_TI_PAUSE_MAX_US);
      const bool ti_transient = ti_violation && !ti_other_violation && ti_states_ok &&
                                pause_eligible && !pause_expired;
      // Any other violation, or any state that is not RUN, is a fault. Treating
      // unknown values as unhealthy is the fail-closed reading required by the plan.
      mazda_ti_fault = (ti_violation && !ti_transient) || !ti_states_ok;
      if (mazda_ti_fault) {
        mazda_ti_latch = true;
        mazda_ti_clean_frames = 0U;
        controls_allowed = false;
        mazda_ti_end_pause();  // the latch now governs recovery
      } else if (ti_transient) {
        if (!mazda_ti_pause && (mazda_ti_pause_clean_frames >= MAZDA_TI_RECOVERY_FRAMES)) {
          mazda_ti_pause_ts = mazda_ti_feedback_ts;  // fresh episode only
        }
        mazda_ti_pause = true;
        mazda_ti_pause_clean_frames = 0U;
      } else {
        mazda_ti_pause = false;
        if (mazda_ti_pause_clean_frames < MAZDA_TI_RECOVERY_FRAMES) {
          mazda_ti_pause_clean_frames++;
        }
        if (mazda_ti_clean_frames < MAZDA_TI_RECOVERY_FRAMES) {
          mazda_ti_clean_frames++;
        }
        // Released only after a qualified recovery AND while the host is already
        // disengaged, so a cruise edge taken during the fault cannot re-arm.
        if (mazda_ti_latch && !controls_allowed && (mazda_ti_clean_frames >= MAZDA_TI_RECOVERY_FRAMES)) {
          mazda_ti_latch = false;
        }
      }
    }

    if (msg->addr == MAZDA_2019_CRUISE && mazda_gen3) {
      // GEN3 keeps the hardcoded reading: there is no GEN3 capture backing a
      // CRZ_STATE interpretation, so it must not be assumed from GEN2.
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
        mazda_speed_seen = true;
        mazda_speed_ts = microsecond_timer_get();
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

  // A latched TI2 fault overrides any re-arm that arrived in this same frame
  // (pcm_cruise_check runs inside the mode hook above).
  if ((mazda_gen2 || mazda_gen3) && mazda_ti_latch) {
    controls_allowed = false;
  }

  // Revoke AOL during RX, not only when a later TX happens to query permission.
  // Otherwise a complete fault/recovery cycle can preserve a stale token. The
  // pre-frame check also covers feedback returning after an unobserved timeout.
  if ((mazda_gen2 || mazda_gen3) && (!ti_health_before || mazda_ti_unhealthy())) {
    mazda_aol_clear();
  }

  // Health returning never restores authority. If controls were armed while the
  // interceptor was unhealthy (startup with no feedback, or a feedback timeout),
  // the authorization predates health and is revoked here; the driver has to
  // engage again.
  if ((mazda_gen2 || mazda_gen3) && !ti_health_before && !mazda_ti_unhealthy() && controls_allowed) {
    controls_allowed = false;
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
  // No verified TI2 feedback at all is a fault, not a gap: before the first clean
  // frame (and while feedback is stale or latched) openpilot may not actuate.
  const bool ti_unhealthy = mazda_ti_unhealthy();

  // Check if msg is sent on the main BUS
  if (msg->bus == (unsigned char)MAZDA_MAIN) {
    // steer cmd checks
    if (msg->addr == MAZDA_LKAS) {
      int desired_torque = (((msg->data[0] & 0x0FU) << 8) | msg->data[1]) - 2048U;

      if (steer_torque_cmd_checks(desired_torque, -1, MAZDA_STEERING_LIMITS)) {
        tx = false;
      }

      if (mazda_gen1 && mazda_torque_interceptor) {
        mazda_ti1_pair_pending = true;
        mazda_ti1_pair_torque = tx ? desired_torque : 0;
        mazda_ti1_pair_ts = microsecond_timer_get();
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
  if (mazda_gen2 && (msg->bus == (unsigned char)MAZDA_CAM) && (msg->addr == MAZDA_2019_ACC)) {
    // 1. Internally inconsistent frame: reject before any compare so a
    // checksum-only mutation cannot slip through.
    if (!mazda_acc_checksum_valid(msg->data)) {
      tx = false;
    }

    // 2. Compare with each fresh stock reference. Byte 7 is the checksum.
    bool identical = false;
    bool approved_only = false;
    const uint32_t now = microsecond_timer_get();
    for (int r = 0; r < MAZDA_STOCK_ACC_REFS; r++) {
      if (mazda_stock_acc_valid[r] &&
          (safety_get_ts_elapsed(now, mazda_stock_acc_ts[r]) <= MAZDA_STOCK_ACC_FRESHNESS_US)) {
        bool same = true;
        bool only = true;
        for (int i = 0; i < 7; i++) {
          const uint8_t diff = msg->data[i] ^ mazda_stock_acc[r][i];
          same = same && (diff == 0U);
          only = only && ((diff & (uint8_t)~MAZDA_ACC_APPROVED[i]) == 0U);
        }
        identical = identical || same;
        approved_only = approved_only || only;
      }
    }

    // 3. An exact fresh stock copy is the stock path: disengaged, stock ACC and
    // AEB, gas override and TI2 faults all relay it untouched.
    // 4. Anything else is an openpilot command and needs healthy TI2, the
    // longitudinal axis (controls_allowed with no AOL policy, the axis
    // permission with one, so a lateral-only grant can never modify 0x220), no
    // driver gas, approved fields only, bounded ACCEL_CMD, and HOLD/RESUME only
    // at standstill.
    if (!identical) {
      const int accel = ((msg->data[2] & 0x01) << 11) | (msg->data[3] << 3) | (msg->data[4] >> 5);
      const bool hold_or_resume = (msg->data[6] & 0x42U) != 0U;
      const bool standstill_current = mazda_speed_seen && !vehicle_moving &&
        (safety_get_ts_elapsed(now, mazda_speed_ts) <= MAZDA_SPEED_FRESHNESS_US);
      const bool allowed = approved_only && longitudinal_controls_allowed() && !ti_unhealthy && !gas_pressed &&
        (accel >= MAZDA_ACC_ACCEL_MIN) && (accel <= MAZDA_ACC_ACCEL_MAX) && (!hold_or_resume || standstill_current);
      if (!allowed) {
        tx = false;
      }
    }
  }

  if ((mazda_gen2 || mazda_gen3) && (msg->bus == MAZDA_AUX) && (msg->addr == MAZDA_TI_LKAS)) {
    int desired_torque = (int16_t)((msg->data[0] << 8) | msg->data[1]);  // signal is signed
    if (ti_unhealthy || mazda_ti_lateral_paused()) {
      // A verified TI2 fault blocks torque even if a cruise re-engagement briefly
      // set controls_allowed again between two feedback frames, and a 0x02 pause
      // blocks it until recovery. Releasing torque (zero) stays allowed so the
      // interceptor is never left holding a command. Refused frames never reach
      // the rate limiter, and its history restarts from zero: after recovery the
      // command ramps up from what the TI2 was actually sent.
      if (desired_torque != 0) {
        tx = false;
      }
      desired_torque_last = 0;
      rt_torque_last = 0;
      ts_torque_check_last = microsecond_timer_get();
    } else if (steer_torque_cmd_checks(desired_torque, -1, MAZDA_2019_STEERING_LIMITS)) {
      tx = false;
    }
  }

  if (mazda_gen1 && mazda_torque_interceptor && (msg->bus == (unsigned char)MAZDA_AUX) && (msg->addr == MAZDA_TI_LKAS)) {
    const int desired_torque = (int)((((uint32_t)msg->data[0] & 0x0FU) << 8) | (uint32_t)msg->data[1]) - 2048;

    // Only the frame layout the TI1 accepts: no stray high bits, CHKSUM equal to
    // LKAS_REQUEST, and the fixed KEY.
    bool well_formed = ((msg->data[0] & 0xF0U) == 0U) && (msg->data[2] == msg->data[0]) && (msg->data[3] == msg->data[1]);
    for (int i = 0; i < 4; i++) {
      well_formed = well_formed && (msg->data[4 + i] == MAZDA_TI1_KEY[i]);
    }

    // The order matters: a frame refused for its format or for TI1 health never
    // reaches a torque limiter, so it cannot move the torque history.
    bool ti1_allowed = false;
    if (!well_formed) {
      ti1_allowed = false;
    } else if (!mazda_ti1_healthy()) {
      // The TI1 is not steering. Only a release passes, and the next command must
      // ramp up from zero on both limiters.
      ti1_allowed = (desired_torque == 0);
      mazda_ti1_last = 0;
      if (mazda_no_fsc) {
        desired_torque_last = 0;
        rt_torque_last = 0;
        ts_torque_check_last = microsecond_timer_get();
      }
    } else if (mazda_no_fsc) {
      // No camera: 0x243 is not on the TX list, so 0x249 is the only steering
      // command and the only input to the standard checks.
      ti1_allowed = !steer_torque_cmd_checks(desired_torque, -1, MAZDA_STEERING_LIMITS);
    } else if (desired_torque == 0) {
      ti1_allowed = true;
    } else {
      const bool paired = mazda_ti1_pair_pending &&
        (safety_get_ts_elapsed(microsecond_timer_get(), mazda_ti1_pair_ts) <= MAZDA_TI1_PAIR_WINDOW_US);
      const bool same_sign = (desired_torque > 0) == (mazda_ti1_pair_torque > 0);
      const bool within_stock = (mazda_ti1_pair_torque != 0) && same_sign &&
        (SAFETY_ABS(desired_torque) <= SAFETY_ABS(mazda_ti1_pair_torque));
      // The TI1's own ramp: up by at most max_rate_up from the last command it was
      // sent in this direction (from zero after a reversal). Down is bounded by the
      // stock command, which is itself rate-limited.
      const int ti1_base = (((desired_torque > 0) == (mazda_ti1_last > 0)) && (mazda_ti1_last != 0)) ? SAFETY_ABS(mazda_ti1_last) : 0;
      const bool ramped = SAFETY_ABS(desired_torque) <= (ti1_base + MAZDA_STEERING_LIMITS.max_rate_up);
      ti1_allowed = paired && within_stock && ramped && lateral_controls_allowed();
    }

    if (!mazda_no_fsc) {
      mazda_ti1_pair_pending = false;
      // What the TI1 last received; a refused frame restarts its ramp from zero,
      // as the standard limiter does on a violation.
      mazda_ti1_last = ti1_allowed ? desired_torque : 0;
    }
    if (!ti1_allowed) {
      tx = false;
    }
  }

  mazda_aol_tx_commit(msg, tx && !relay_malfunction);
  return tx;
}

// RX check tables are const (Dom #3870), so each configuration selects its
// own table instead of patching a shared one at init.
#define MAZDA_GEN1_RX_CHECKS \
  {.msg = {{MAZDA_CRZ_CTRL,     0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MAZDA_CRZ_BTNS,     0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MAZDA_STEER_TORQUE, 0, 8, 83U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MAZDA_ENGINE_DATA,  0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
  {.msg = {{MAZDA_PEDALS,       0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},

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

  // TI1 without the camera (NoFSC): 0x243 is not sent, and must not be allowed,
  // or it could drive the same torque limiter as 0x249.
  static const CanMsg MAZDA_TI_NO_FSC_TX_MSGS[] = {{MAZDA_TI_LKAS, 1, 8, .check_relay = false}, {MAZDA_CRZ_BTNS, 0, 8, .check_relay = false}, {MAZDA_LKAS_HUD, 0, 8, .check_relay = true}};
  static const CanMsg MAZDA_TI_RI_NO_FSC_TX_MSGS[] = {{MAZDA_TI_LKAS, 1, 8, .check_relay = false}, {MAZDA_CRZ_BTNS, 0, 8, .check_relay = false}, {MAZDA_LKAS_HUD, 0, 8, .check_relay = true},
                                  {MAZDA_CRZ_CTRL, 0, 8, .check_relay = true}, {MAZDA_CRZ_INFO, 0, 8, .check_relay = true}, {MAZDA_RADAR_361, 0, 8, .check_relay = true}, {MAZDA_RADAR_362, 0, 8, .check_relay = true},
                                  {MAZDA_RADAR_363, 0, 8, .check_relay = true}, {MAZDA_RADAR_364, 0, 8, .check_relay = true}, {MAZDA_RADAR_365, 0, 8, .check_relay = true}, {MAZDA_RADAR_366, 0, 8, .check_relay = true},
                                  {MAZDA_RADAR_499, 0, 8, .check_relay = true}};

  static const CanMsg MAZDA_2019_TX_MSGS[] = {{MAZDA_TI_LKAS, 1, 8, .check_relay = false}, {MAZDA_2019_ACC, 2, 8, .check_relay = true}};
  // GEN3 is lateral-only: no openpilot longitudinal, so 0x220 is never TX-allowed
  static const CanMsg MAZDA_2023_TX_MSGS[] = {{MAZDA_TI_LKAS, 1, 8, .check_relay = false}};

  static const RxCheck mazda_rx_checks[] = {
    MAZDA_GEN1_RX_CHECKS
  };
  // A torque interceptor adds its feedback as a sixth required message.
  static const RxCheck mazda_ti_rx_checks[] = {
    MAZDA_GEN1_RX_CHECKS
    {.msg = {{TI_STEER_TORQUE, 1, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

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
  for (int r = 0; r < MAZDA_STOCK_ACC_REFS; r++) {
    mazda_stock_acc_valid[r] = false;
    mazda_stock_acc_ts[r] = 0U;
    for (int i = 0; i < 8; i++) {
      mazda_stock_acc[r][i] = 0U;
    }
  }
  mazda_speed_seen = false;
  mazda_speed_ts = 0U;
  mazda_ti_feedback_seen = false;
  mazda_ti_feedback_ts = 0U;
  mazda_ti_fault = false;
  mazda_ti_latch = false;
  mazda_ti_clean_frames = 0U;
  mazda_ti_pause_ts = 0U;
  mazda_ti_end_pause();
  mazda_ti1_feedback_seen = false;
  mazda_ti1_feedback_ts = 0U;
  mazda_ti1_state = 0U;
  mazda_ti1_viol = 0U;
  mazda_ti1_ramp_down = false;
  mazda_ti1_pair_pending = false;
  mazda_ti1_pair_torque = 0;
  mazda_ti1_pair_ts = 0U;
  mazda_ti1_last = 0;

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

  // Startup health is carried by mazda_ti_feedback_seen (see the tx hook): no
  // feedback has arrived yet, so actuation is refused until the first clean
  // 0x24B frame. That is a missing-input condition, not a fault latch.

  // GEN1 stock AOL keeps its original scope. GEN2 joins it through the same
  // protocol with its own sources (mazda_stock_aol.h): CRZ_STATE main, GEAR,
  // interceptor liveness and camera-bus speed.
  mazda_aol_enabled = (stock_gen1 || (param == (uint16_t)FLAG_GEN2)) && ((unsigned int)alternative_experience == 32U);
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
    if (mazda_torque_interceptor) {
      SET_RX_CHECKS(mazda_ti_rx_checks, ret);
    } else {
      SET_RX_CHECKS(mazda_rx_checks, ret);
    }
    if (mazda_radar_interceptor && mazda_torque_interceptor && mazda_no_fsc) {
      SET_TX_MSGS(MAZDA_TI_RI_NO_FSC_TX_MSGS, ret);
    } else if (mazda_torque_interceptor && mazda_no_fsc) {
      SET_TX_MSGS(MAZDA_TI_NO_FSC_TX_MSGS, ret);
    } else if (mazda_radar_interceptor && mazda_torque_interceptor) {
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
    ret = BUILD_SAFETY_CFG(checks, MAZDA_2019_TX_MSGS);
  }
  if (mazda_gen3) {
    static const RxCheck checks[5] = {
      {.msg = {{MAZDA_2023_BRAKE,         0, 8, 5U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{MAZDA_2019_GAS,           2, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{MAZDA_2019_CRUISE,        1, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{MAZDA_2023_SPEED,         2, 8, 30U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
      {.msg = {{MAZDA_2019_STEER_TORQUE,  1, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    };
    ret = BUILD_SAFETY_CFG(checks, MAZDA_2023_TX_MSGS);
  }

  // Alt-exp 32 without a supported profile gets no TX at all. GEN2 now has its
  // own profile, so it is excluded from that fail-closed rule; GEN1 TI/RI and
  // GEN3 still are not supported in that mode.
  if (((unsigned int)alternative_experience == 32U) && !mazda_aol_enabled) {
    ret.tx_msgs = NULL;
    ret.tx_msgs_len = 0;
  }
  return ret;
}

// 0x220 is not part of the required GEN2 rx set, so it arrives on the optional
// ingress path. Record the stock frame for the tx hook's passthrough check; the
// AOL sources below keep using the same callback.
static void mazda_optional_rx(const CANPacket_t *msg) {
  if (mazda_gen2 && (msg->bus == (unsigned char)MAZDA_MAIN) && (msg->addr == MAZDA_2019_ACC)) {
    // Only a complete, checksum-valid stock frame may become the passthrough
    // reference; a truncated or corrupt frame must not authorize a relay, and it
    // invalidates both references.
    if ((GET_LEN(msg) == 8U) && mazda_acc_checksum_valid(msg->data)) {
      for (int i = 0; i < 8; i++) {
        mazda_stock_acc[1][i] = mazda_stock_acc[0][i];
        mazda_stock_acc[0][i] = msg->data[i];
      }
      mazda_stock_acc_valid[1] = mazda_stock_acc_valid[0];
      mazda_stock_acc_ts[1] = mazda_stock_acc_ts[0];
      mazda_stock_acc_valid[0] = true;
      mazda_stock_acc_ts[0] = microsecond_timer_get();
    } else {
      mazda_stock_acc_valid[0] = false;
      mazda_stock_acc_valid[1] = false;
    }
  }
  mazda_aol_optional_rx(msg);
}

const safety_hooks mazda_hooks = {
  .init = mazda_init,
  .rx = mazda_rx_hook,
  .tx = mazda_tx_hook,
  .optional_rx = mazda_optional_rx,
  .fwd = mazda_aol_fwd,
};
