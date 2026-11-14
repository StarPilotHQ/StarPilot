#pragma once

#include "opendbc/safety/declarations.h"
#include "opendbc/safety/can_tx.h"
#include "gm_rx.h"
#include "gm_aol.h"
#include "gm_volt_one_pedal.h"
#include "gm_cc_pedal.h"
#include "gm_volt_auto_hold.h"
#include "gm_camera_acc_pedal.h"
#include "gm_cruise_status.h"
#include "gm_hybrid_cc.h"
#include "gm_bolt_cc.h"

// TODO: do checksum and counter checks. Add correct timestep, 0.1s for now.
#define GM_COMMON_RX_CHECKS \
    {.msg = {{0x184, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
    {.msg = {{0x34A, 0, 5, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
    {.msg = {{0x1E1, 0, 7, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
    {.msg = {{0xBE, 0, 6, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},    /* Volt, Silverado, Acadia Denali */ \
             {0xBE, 0, 7, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true},    /* Bolt EUV */ \
             {0xBE, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}}},  /* Escalade */ \
    {.msg = {{0x1C4, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
    {.msg = {{0xC9, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \

static const LongitudinalLimits *gm_long_limits;

enum {
  GM_BTN_UNPRESS = 1,
  GM_BTN_RESUME = 2,
  GM_BTN_SET = 3,
  GM_BTN_CANCEL = 6,
};

typedef enum {
  GM_ASCM,
  GM_CAM
} GmHardware;
static GmHardware gm_hw = GM_ASCM;
static bool gm_pcm_cruise = false;
static bool gm_pedal_long = false;
static bool gm_pedal_acc = false;
static bool gm_pedal_sensor_good = false;
static uint32_t gm_pedal_sensor_last_us = 0U;
static bool gm_pedal_counter_seen = false;
static uint8_t gm_pedal_counter_last = 0U;
static bool gm_pedal_tx_counter_seen = false;
static uint8_t gm_pedal_tx_counter_last = 0U;
static bool gm_pedal_brake_counter_seen = false;
static uint8_t gm_pedal_brake_counter_last = 0U;
static bool gm_pedal_brake_producer_seen = false;
static uint32_t gm_pedal_brake_producer_us = 0U;
static bool gm_bolt_2017 = false;
static bool gm_paddle_sched = false;
static bool gm_bolt_gen2 = false;
static bool gm_ascm_intercept = false;
static bool gm_ascm_brake_c9 = false;
static bool gm_sdgm = false;
static bool gm_volt_sdgm_long = false;
static bool gm_ordinary_sdgm_long = false;
static bool gm_sdgm_invalid = false;
static bool gm_volt_camera_removed = false;
static bool gm_ordinary_camera_removed = false;
static bool gm_ordinary_camera_removed_long = false;
static bool gm_volt_removed_long = false;
static bool gm_volt_removed_main = false;
static bool gm_volt_removed_pcm = false;
static bool gm_volt_removed_credit = false;
static bool gm_volt_removed_button_seen = false;
static uint8_t gm_volt_removed_counter = 0U;
static uint32_t gm_volt_removed_button_us = 0U;
static uint32_t gm_volt_removed_button_observed_us = 0U;
static uint32_t gm_volt_removed_main_us = 0U;
static uint32_t gm_volt_removed_pcm_us = 0U;
static bool gm_volt_removed_cancel_seen = false;
static uint32_t gm_volt_removed_cancel_us = 0U;
static bool gm_sdgm_brake_c9 = false;
static bool gm_cc_gateway_stock = false;
// Exact EV|NO_ACC word20 was rejected previously; it is a DEBUG-only Volt CC owner.
static bool gm_volt_cc_long = false;
static bool gm_volt_cc_f1 = false;
static bool gm_ordinary_cc_long = false;
static bool gm_malibu_cc_f1 = false;
static bool gm_malibu_cc_stock = false;
static bool gm_malibu_f1_brake = false;
static bool gm_malibu_c9_brake = false;
static bool gm_volt_cc_seen[8] = {false, false, false, false, false, false, false, false};
static uint32_t gm_volt_cc_last_us[8] = {0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
static bool gm_volt_cc_main = false;
static bool gm_volt_cc_active = false;
static bool gm_volt_cc_forward_gear = false;
static bool gm_volt_cc_speed = false;
static bool gm_volt_cc_neutral = false;
static bool gm_volt_cc_credit = false;
static uint8_t gm_volt_cc_counter = 0U;
static bool gm_volt_cc_tx_seen = false;
static uint32_t gm_volt_cc_tx_us = 0U;
static bool gm_volt_cc_cancel_seen = false;
static uint32_t gm_volt_cc_cancel_us = 0U;
static uint32_t gm_volt_cc_stock_speed = 0U;
static uint32_t gm_volt_cc_wheel_speed_sum = 0U;
static bool gm_volt_cc_gas_set_seen = false;
static uint32_t gm_volt_cc_gas_set_us = 0U;

static bool gm_volt_cc_sources_current(void) {
  bool current = true;
  const uint32_t limits[8] = {300000U, 100000U, 100000U, 100000U, 300000U, 100000U, 100000U, 150000U};
  for (uint8_t i = 0U; i < 8U; i++) {
    if (gm_ordinary_cc_long && (i == 6U)) { continue; }
    current &= gm_volt_cc_seen[i] && (safety_get_ts_elapsed(microsecond_timer_get(), gm_volt_cc_last_us[i]) <= limits[i]);
  }
  return current;
}

static bool gm_volt_cc_current(void) {
  return gm_volt_cc_sources_current() && gm_volt_cc_main && gm_volt_cc_active && gm_volt_cc_forward_gear && gm_volt_cc_speed &&
         !brake_pressed && !gas_pressed && !regen_braking;
}

static bool gm_cc_gateway_invalid = false;
static bool gm_volt_invalid = false;
static bool gm_volt_gateway_alt_brake = false;
static uint8_t gm_cc_stock_button_counter = 0U;
static bool gm_cc_stock_button_seen = false;
static bool gm_cc_cancel_sent = false;
static uint32_t gm_cc_stock_button_last_us = 0U;
static bool gm_acc_status_seen = false;
static uint32_t gm_acc_status_last_us = 0U;
static bool gm_pedal_main_seen = false;
static uint32_t gm_pedal_main_last_us = 0U;
static bool gm_regen_gear_ready = false;
static bool gm_bolt_pedal_drive_supported = false;
static bool gm_pedal_forward_gear_ready = false;
static bool gm_acc_pedal_forward_owner = false;
static bool gm_acc_pedal_dashboard_seen = false;
static bool gm_acc_dashboard_paddle_gear = false;
static bool gm_acc_dashboard_paddle_pressed = false;
static uint32_t gm_acc_dashboard_paddle_us = 0U;
static uint32_t gm_acc_pedal_dashboard_us = 0U;
static bool gm_bolt_pedal_removed = false;
static bool gm_bolt_removed_acc_seen = false;
static bool gm_bolt_removed_gear_seen = false;
static bool gm_bolt_present_cc_cancel = false;
static bool gm_bolt_cancel_forward_gear_required = false;
static bool gm_bolt_removed_acc_active = false;
static uint32_t gm_bolt_removed_acc_us = 0U;
static uint32_t gm_regen_gear_last_us = 0U;
static bool gm_paddle_internal_tx = false;

typedef struct {
  bool valid;
  uint8_t bytes[8];
  uint32_t last_feed_us;
} GmPaddleFeed;
static GmPaddleFeed gm_bd_feed = {0};
static GmPaddleFeed gm_gear_feed = {0};


static bool gm_pedal_sensor_current(void) {
  return gm_pedal_sensor_good && safety_get_ts_elapsed(microsecond_timer_get(), gm_pedal_sensor_last_us) <= 100000U;
}

static bool gm_pedal_owns_longitudinal(void) {
  return !gm_pedal_acc || (gm_acc_status_seen && !cruise_engaged_prev &&
                           safety_get_ts_elapsed(microsecond_timer_get(), gm_acc_status_last_us) <= 300000U &&
                           (!gm_bolt_pedal_removed || (gm_bolt_removed_acc_seen && !gm_bolt_removed_acc_active &&
                             safety_get_ts_elapsed(microsecond_timer_get(), gm_bolt_removed_acc_us) <= 300000U)));
}

static bool gm_bolt_pedal_drive_ready(void) {
  return gm_bolt_pedal_drive_supported && gm_pedal_forward_gear_ready && !gm_regen_gear_ready;
}

static bool gm_pedal_regen_gear_ready(void) {
  return (gm_regen_gear_ready || gm_bolt_pedal_drive_ready()) &&
         safety_get_ts_elapsed(microsecond_timer_get(), gm_regen_gear_last_us) <= 100000U;
}

static bool gm_pedal_drive_ready(void) {
  return (gm_acc_pedal_forward_owner ? gm_pedal_forward_gear_ready : (gm_regen_gear_ready || gm_bolt_pedal_drive_ready())) &&
         safety_get_ts_elapsed(microsecond_timer_get(), gm_regen_gear_last_us) <= 100000U;
}

static bool gm_pedal_main_ready(void) {
  return gm_pedal_main_seen && acc_main_on &&
         safety_get_ts_elapsed(microsecond_timer_get(), gm_pedal_main_last_us) <= 300000U;
}

static bool gm_acc_pedal_dashboard_current(void) {
  const uint32_t now = microsecond_timer_get();
  const bool paddle_display = gm_acc_dashboard_paddle_gear && gm_acc_dashboard_paddle_pressed &&
    (safety_get_ts_elapsed(now, gm_acc_dashboard_paddle_us) <= 100000U) &&
    (safety_get_ts_elapsed(now, gm_regen_gear_last_us) <= 100000U);
  const bool physical = gm_acc_pedal_forward_owner && gm_pedal_sensor_current() &&
    gm_pedal_main_ready() && (gm_pedal_drive_ready() || paddle_display) && gm_acc_status_seen &&
    (safety_get_ts_elapsed(microsecond_timer_get(), gm_acc_status_last_us) <= 300000U) &&
    !safety_rx_checks_invalid && !relay_malfunction;
  if (!physical) { gm_acc_pedal_dashboard_seen = false; }
  return physical;
}


static void gm_pedal_brake_producer_clear(void) {
  gm_pedal_brake_producer_seen = false;
  gm_pedal_brake_counter_seen = false;
}

static bool gm_pedal_brake_producer_current(void) {
  if (!gm_pedal_main_ready() || !gm_pedal_owns_longitudinal() ||
      (safety_get_ts_elapsed(microsecond_timer_get(), gm_pedal_brake_producer_us) > 100000U)) {
    gm_pedal_brake_producer_clear();
  }
  return gm_pedal_brake_producer_seen;
}

static void gm_emit_paddle_after_stock(uint32_t now_us, uint32_t addr, uint8_t dlc, GmPaddleFeed *feed) {
  // No output before a host feed or more than four 25 Hz frames after it.
  if (gm_paddle_sched && feed->valid) {
    const bool expired = safety_get_ts_elapsed(now_us, feed->last_feed_us) > 100000U;
    if (expired) { feed->valid = false; }  // Retain the command through its bounded host-feed lifetime.
    bool applied = feed->bytes[0] == 0x20U;
    if (addr != 0xBDU) {
      applied = ((feed->bytes[3] & 0xFU) == (gm_bolt_gen2 ? 5U : 7U)) && (feed->bytes[5] == 2U);
      if (!applied && ((feed->bytes[3] & 0xFU) != (gm_bolt_pedal_drive_ready() ? 4U : 6U))) {
        feed->valid = false;
      }
    }
    const bool main_ready = (!(gm_pedal_acc || gm_cancel_remap_enabled) || gm_pedal_main_ready());
    const bool inactive_release = gm_cancel_remap_enabled && !applied;
    if (feed->valid && !expired && gm_pedal_regen_gear_ready() && !regen_braking &&
        (main_ready || inactive_release)) {
      CANPacket_t packet = {0};
      packet.addr = addr;
      packet.bus = 0U;
      packet.data_len_code = dlc;
      for (uint8_t i = 0U; i < dlc; i++) {
        packet.data[i] = feed->bytes[i];
      }
      can_set_checksum(&packet);
      gm_paddle_internal_tx = true;
      can_send(&packet, 0U, false);
      gm_paddle_internal_tx = false;
    }
  }
}

static bool gm_cruise_status_owner_ready(void) {
  bool ready;
  if (gm_cruise_status_hybrid) {
    ready = gm_hybrid_main && gm_hybrid_drive && gm_hybrid_eps && gm_hybrid_sensor_valid &&
            !gm_hybrid_sensor_gas && !gm_hybrid_regen;
  } else {
    ready = gm_cruise_status_main && gm_cruise_status_drive && gm_cruise_status_eps &&
            gm_pedal_sensor_current();
  }
  // Bolt's 0x3D1 replacement is dashboard status only. Accelerator override
  // still withdraws pedal/paddle actuation in their independent TX gates.
  return ready && !brake_pressed && (!gas_pressed || gm_cruise_status_bolt) && !regen_braking;
}

static void gm_rx_hook(const CANPacket_t *msg) {
  if (gm_pedal_acc) { (void)gm_pedal_brake_producer_current(); }
  const int GM_STANDSTILL_THRSLD = 10;  // 0.311kph

  gm_camera_pedal_rx(msg);
  if (msg->bus == 0U) {
    if ((gm_bolt_pedal_removed || gm_bolt_present_cc_cancel) && (msg->addr == 0x3D1U)) {
      gm_bolt_removed_acc_seen = GET_LEN(msg) == 8U;
      if (gm_bolt_removed_acc_seen) {
        gm_bolt_removed_acc_active = GET_BIT(msg, 39U);
        gm_bolt_removed_acc_us = microsecond_timer_get();
      }
      if (!gm_pedal_owns_longitudinal()) { gm_pedal_brake_producer_clear(); }
    }
    if (gm_volt_cc_long || gm_ordinary_cc_long) {
      int source = -1;
      if ((msg->addr == 0x3D1U) && (GET_LEN(msg) == 8U)) {
        source = 0;
        gm_volt_cc_active = GET_BIT(msg, 39U);
        gm_volt_cc_stock_speed = (((uint32_t)msg->data[2] & 0xFU) << 8) | msg->data[3];
        pcm_cruise_check(gm_volt_cc_active && gm_volt_cc_main);
      } else if ((msg->addr == 0x1E1U) && (GET_LEN(msg) == 7U)) {
        source = 1;
        const uint8_t counter = msg->data[4] & 0x3U;
        const uint16_t checksum = 0xFFU + (counter * 0x4EFU);
        gm_volt_cc_neutral = (msg->data[0] == 0U) && (msg->data[1] == 0U) && (msg->data[2] == 0U) &&
                             (msg->data[3] == 1U) && (msg->data[4] == counter) &&
                             (msg->data[5] == (0x10U | (checksum >> 8))) && (msg->data[6] == (uint8_t)checksum);
        const bool first = !gm_volt_cc_seen[1];
        const bool timely = gm_volt_cc_seen[1] && (safety_get_ts_elapsed(microsecond_timer_get(), gm_volt_cc_last_us[1]) <= 100000U);
        const bool forward = counter == ((gm_volt_cc_counter + 1U) % 4U);
        const bool duplicate = counter == gm_volt_cc_counter;
        if (gm_volt_cc_neutral && (first || (timely && forward))) {
          gm_volt_cc_credit = true;
        } else if (!gm_volt_cc_neutral || !timely || !duplicate) {
          gm_volt_cc_credit = false;
        } else {
          // A duplicate physical counter cannot replenish consumed credit.
        }
        gm_volt_cc_counter = counter;
      } else if ((msg_matches(msg, 0xC9U, 0U)) && (GET_LEN(msg) == 8U)) {
        source = 2;
        gm_volt_cc_main = GET_BIT(msg, 29U);
      } else if ((msg_matches(msg, (gm_malibu_cc_f1 || gm_volt_cc_f1) ? 0xF1U : 0xBEU, 0U)) && (GET_LEN(msg) == 6U)) {
        source = 3;
      } else if ((msg->addr == 0x1F5U) && (GET_LEN(msg) == 8U)) {
        source = 4;
        const uint8_t gear = msg->data[3] & 0xFU;
        gm_volt_cc_forward_gear = ((gear == 4U) || (gear == 6U)) && !GET_BIT(msg, 41U);
      } else if ((msg->addr == 0x1C4U) && (GET_LEN(msg) == 8U)) {
        source = 5;
      } else if ((msg->addr == 0xBDU) && (GET_LEN(msg) == 7U)) {
        source = 6;
      } else {
        // Other traffic cannot refresh an independent required source.
      }
      if ((msg->addr == 0x34AU) && (GET_LEN(msg) == 5U)) {
        source = 7;
      }
      if (source >= 0) {
        gm_volt_cc_seen[source] = true;
        gm_volt_cc_last_us[source] = microsecond_timer_get();
      }
      if ((msg->addr == 0x34AU) && (GET_LEN(msg) == 5U)) {
        gm_volt_cc_wheel_speed_sum = (((uint32_t)msg->data[0] << 8) | msg->data[1]) +
                                     (((uint32_t)msg->data[2] << 8) | msg->data[3]);
        gm_volt_cc_speed = ((((uint32_t)msg->data[0] << 8) | msg->data[1]) >= 1242U) &&
                           ((((uint32_t)msg->data[2] << 8) | msg->data[3]) >= 1242U) &&
                           (((msg->data[4] >> 3) & 0x7U) == 1U) && ((msg->data[4] & 0x7U) == 1U);
      }
      if (!gm_volt_cc_main || !gm_volt_cc_active) { controls_allowed = false; }
    }

    if (gm_volt_camera_removed || ((gm_bolt_pedal_removed || gm_bolt_present_cc_cancel) && !gm_pedal_acc) ||
        ((gm_camera_gateway || gm_camera_ascm || gm_camera_sdgm) && !gm_camera_pedal_long)) {
      const uint32_t now = microsecond_timer_get();
      if ((msg->addr == 0x1E1U) && (GET_LEN(msg) == 7U)) {
        const uint8_t counter = msg->data[4] & 0x3U;
        const uint16_t neutral_checksum = 0xFFU + (counter * 0x4EFU);
        const bool neutral_acc = (((msg->data[5] >> 4) & 0x7U) == 1U) &&
                             (msg->data[0] == 0U) && (msg->data[1] == 0U) && (msg->data[2] == 0U) &&
                             (msg->data[3] == 1U) && (msg->data[4] == counter) &&
                             (msg->data[5] == (uint8_t)(0x10U | (neutral_checksum >> 8))) &&
                             (msg->data[6] == (uint8_t)neutral_checksum);
        const uint16_t non_acc_checksum = 0xF0U + (counter * 0x3F0U);
        const bool neutral_non_acc = (gm_bolt_pedal_removed || gm_bolt_present_cc_cancel) && !gm_pedal_acc &&
          (msg->data[0] == 0U) && (msg->data[1] == 0U) && (msg->data[2] == 0U) &&
          (msg->data[3] == 0U) && (msg->data[4] == counter) &&
          (msg->data[5] == (uint8_t)(0x10U | (non_acc_checksum >> 8))) &&
          (msg->data[6] == (uint8_t)non_acc_checksum);
        const bool neutral = neutral_acc || neutral_non_acc;
        const bool forward = counter == ((gm_volt_removed_counter + 1U) % 4U);
        const bool timely = safety_get_ts_elapsed(now, gm_volt_removed_button_observed_us) <= 100000U;
        if (neutral && (!gm_volt_removed_button_seen || (forward && timely))) {
          gm_volt_removed_credit = true;
          gm_volt_removed_button_us = now;
        } else if (!neutral || !timely || (counter != gm_volt_removed_counter)) {
          gm_volt_removed_credit = false;
        } else {
          // Duplicate neutral counters do not refresh a consumed slot.
        }
        if (!gm_volt_removed_button_seen || (counter != gm_volt_removed_counter)) {
          // Physical observations recover the sequence; only valid neutral slots grant credit.
          gm_volt_removed_button_observed_us = now;
        }
        gm_volt_removed_counter = counter;
        gm_volt_removed_button_seen = true;
      } else if (msg_matches(msg, 0xC9U, 0U, 8U)) {
        gm_volt_removed_main = GET_BIT(msg, 29U);
        gm_volt_removed_main_us = now;
        if ((gm_bolt_pedal_removed || gm_bolt_present_cc_cancel) && !gm_volt_removed_main) { gm_volt_removed_credit = false; }
      } else if ((gm_bolt_pedal_removed || gm_bolt_present_cc_cancel) && (msg->addr == 0x3D1U) && (GET_LEN(msg) == 8U)) {
        gm_volt_removed_pcm = GET_BIT(msg, 39U);
        gm_volt_removed_pcm_us = now;
        if (!gm_volt_removed_pcm) { gm_volt_removed_credit = false; }
      } else if (!gm_bolt_pedal_removed && !gm_bolt_present_cc_cancel && (msg->addr == 0x1C4U) && (GET_LEN(msg) == 8U)) {
        const uint8_t cruise_state = msg->data[1] >> 5;
        gm_volt_removed_pcm = (cruise_state != 0U) && (!gm_volt_camera_removed || (cruise_state != 3U));
        gm_volt_removed_pcm_us = now;
        if (!gm_volt_removed_pcm) { gm_volt_removed_credit = false; }
      } else {
        // Only the physical PT sources grant cancellation credit.
      }
    }

    if (msg_matches(msg, 0x184U, 0U)) {
      int torque_driver_new = ((msg->data[6] & 0x7U) << 8) | msg->data[7];
      torque_driver_new = to_signed(torque_driver_new, 11);
      // update array of samples
      update_sample(&torque_driver, torque_driver_new);
    }

    // sample rear wheel speeds
    if (msg_matches(msg, 0x34AU, 0U)) {
      int left_rear_speed = (msg->data[0] << 8) | msg->data[1];
      int right_rear_speed = (msg->data[2] << 8) | msg->data[3];
      vehicle_moving = (left_rear_speed > GM_STANDSTILL_THRSLD) || (right_rear_speed > GM_STANDSTILL_THRSLD);
    }

    // ACC steering wheel buttons (GM_CAM is tied to the PCM)
    if ((msg_matches(msg, 0x1E1U, 0U)) && gm_cc_gateway_stock) {
      const uint8_t counter = msg->data[4] & 0x3U;
      gm_cc_cancel_sent = gm_cc_stock_button_seen && (counter == gm_cc_stock_button_counter);
      gm_cc_stock_button_counter = counter;
      gm_cc_stock_button_seen = true;
      gm_cc_stock_button_last_us = microsecond_timer_get();
    }
    if ((msg_matches(msg, 0x1E1U, 0U)) && !gm_pcm_cruise) {
      int button = (msg->data[5] & 0x70U) >> 4;

      // enter controls on falling edge of set or rising edge of resume (avoids fault)
      bool set = (button != GM_BTN_SET) && (cruise_button_prev == GM_BTN_SET);
      bool res = (button == GM_BTN_RESUME) && (cruise_button_prev != GM_BTN_RESUME);
      if (set || res) {
        controls_allowed = true;
      }

      // exit controls on cancel press
      if ((button == GM_BTN_CANCEL) && !gm_cancel_remap_enabled) {
        controls_allowed = false;
      }

      cruise_button_prev = button;
    }

    if (gm_malibu_cc_f1 || gm_volt_cc_f1) {
      if (msg_matches(msg, 0xF1U, 0U) && (GET_LEN(msg) == 6U)) {
        if (gm_volt_cc_f1) {
          gm_malibu_f1_brake = msg->data[1] >= 6U;
        } else {
          gm_malibu_f1_brake = (msg->data[1] >= 21U) || GET_BIT(msg, 1U);
        }
      }
      if (msg_matches(msg, 0xC9U, 0U) && (GET_LEN(msg) == 8U)) {
        gm_malibu_c9_brake = GET_BIT(msg, 40U);
      }
      brake_pressed = gm_malibu_f1_brake || gm_malibu_c9_brake;
    }

    // Reference for brake pressed signals:
    // https://github.com/commaai/openpilot/blob/master/selfdrive/car/gm/carstate.py
    if ((msg_matches(msg, 0xBEU, 0U)) && !gm_camera_gateway && !gm_malibu_cc_f1 && !gm_volt_cc_f1 && (((gm_hw == GM_ASCM) && !gm_volt_gateway_alt_brake) || (gm_ascm_intercept && !gm_ascm_brake_c9) ||
                                   (gm_sdgm && !gm_sdgm_brake_c9))) {
      brake_pressed = msg->data[1] >= 8U;
    }

    if ((msg->addr == 0xF1U) && gm_volt_gateway_alt_brake) {
      brake_pressed = msg->data[1] >= 6U;
    }

    if ((msg_matches(msg, 0xC9U, 0U)) && (((gm_hw == GM_CAM) && !gm_camera_gateway) || (gm_camera_gateway && !gm_camera_pedal_f1)) && (!gm_ascm_intercept || gm_ascm_brake_c9) &&
        (!gm_sdgm || gm_sdgm_brake_c9)) {
      brake_pressed = GET_BIT(msg, 40U);
      if ((gm_pedal_acc || (gm_cancel_remap_enabled && gm_pedal_long)) && (GET_LEN(msg) == 8U)) {
        acc_main_on = GET_BIT(msg, 29U);
        gm_pedal_main_seen = true;
        gm_pedal_main_last_us = microsecond_timer_get();
        if (!acc_main_on) { controls_allowed = false; gm_pedal_brake_producer_clear(); }
      }
    }

    if (msg_matches(msg, 0x1C4U, 0U)) {
      if (!gm_pedal_long) {
        gas_pressed = msg->data[5] != 0U;
      }

      // enter controls on rising edge of ACC, exit controls when ACC off
      if (gm_pcm_cruise && !gm_cc_gateway_stock) {
        const uint8_t cruise_state = msg->data[1] >> 5;
        const bool cruise_engaged = (cruise_state != 0U) && (!gm_volt_camera_removed || (cruise_state != 3U));
        pcm_cruise_check(cruise_engaged);
      } else if (gm_pedal_acc) {
        cruise_engaged_prev = (msg->data[1] >> 5) != 0U;
        gm_acc_status_seen = true;
        gm_acc_status_last_us = microsecond_timer_get();
        if (cruise_engaged_prev) { gm_pedal_brake_producer_clear(); }
      } else {
        // This configuration does not derive cruise state from ACC status.
      }
    }

    if ((msg_matches(msg, 0x3D1U, 0U)) && gm_cc_gateway_stock) {
      pcm_cruise_check(((msg->data[4] & 0x80U) != 0U) && acc_main_on);
    }

    if ((msg_matches(msg, 0xC9U, 0U)) && gm_cc_gateway_stock) {
      acc_main_on = GET_BIT(msg, 29U);
      if (!acc_main_on) { controls_allowed = false; }
    }

    if (msg_matches(msg, 0xBDU, 0U)) {
      regen_braking = (msg->data[0] >> 4) != 0U;
      gm_acc_dashboard_paddle_pressed = (msg->data[0] >> 4) == 2U;
      gm_acc_dashboard_paddle_us = microsecond_timer_get();
      gm_emit_paddle_after_stock(microsecond_timer_get(), 0xBDU, 7U, &gm_bd_feed);
    }
    if (msg_matches(msg, 0x1F5U, 0U)) {
      // Physical Low or supported Gen2 Drive; never Park/Reverse/manual.
      gm_regen_gear_ready = ((msg->data[3] & 0xFU) == 6U) && ((msg->data[5] & 0x2U) == 0U);
      gm_pedal_forward_gear_ready = (((msg->data[3] & 0xFU) == 4U) || ((msg->data[3] & 0xFU) == 6U)) &&
                                    ((msg->data[5] & 0x2U) == 0U);
      if (gm_bolt_pedal_removed || gm_bolt_present_cc_cancel) { gm_bolt_removed_gear_seen = true; }
      gm_acc_dashboard_paddle_gear = ((msg->data[3] & 0xFU) == 5U) && ((msg->data[5] & 0x2U) != 0U);
      gm_regen_gear_last_us = microsecond_timer_get();
      gm_emit_paddle_after_stock(microsecond_timer_get(), 0x1F5U, 8U, &gm_gear_feed);
    }

    if ((msg_matches(msg, 0x201U, 0U)) && gm_pedal_long) {
      // Incoming tracks are independent 12-bit ADC samples, not synthesized command pairs.
      const int track1 = (msg->data[0] << 8) | msg->data[1];
      const int track2 = (msg->data[2] << 8) | msg->data[3];
      const uint8_t state = msg->data[4] >> 4;
      const uint8_t counter = msg->data[4] & 0xFU;
      gm_pedal_sensor_good = (state == 0U) && (gm_pedal_crc(msg) == msg->data[5]) &&
                             (!gm_pedal_counter_seen || (counter != gm_pedal_counter_last)) &&
                             (track1 <= 4095) && (track2 <= 4095);
      gm_pedal_counter_seen = true;
      gm_pedal_counter_last = counter;
      gm_pedal_sensor_last_us = microsecond_timer_get();
      gas_pressed = (track1 + track2) > 1190;
      if (!gm_pedal_sensor_good) {
        controls_allowed = false;
      }
    }

    if (gm_pedal_long && (!controls_allowed || brake_pressed || gas_pressed || regen_braking ||
                          !gm_pedal_owns_longitudinal() || !gm_pedal_regen_gear_ready())) {
      if (gm_bd_feed.bytes[0] == 0x20U) { gm_bd_feed.valid = false; }
      if (gm_gear_feed.bytes[5] == 2U) { gm_gear_feed.valid = false; }
    }
  }
  if ((gm_volt_cc_long || gm_ordinary_cc_long) && (!gm_volt_cc_main || !gm_volt_cc_active)) {
    gm_volt_cc_credit = false;
  }

  gm_cc_pedal_rx(msg);
  gm_one_pedal_observe(msg);
  gm_hold_rx(msg);
  if (gm_camera_volt && gm_auto_hold) { (void)gm_camera_hold_sensor_ready(); }
  if (gm_acc_pedal_forward_owner) { (void)gm_acc_pedal_dashboard_current(); }
}

static bool gm_ascm_adas_valid(const CANPacket_t *msg) {
  bool valid = true;
  if (msg->addr == 0xA1U) {
    const uint16_t checksum = (0x1000U - msg->data[0] - msg->data[1] - msg->data[2] - msg->data[3]) & 0xFFFU;
    valid &= ((msg->data[3] & 0x3U) == 0U) && (msg->data[4] == (0x40U + (checksum >> 8))) &&
          (msg->data[5] == (checksum & 0xFFU)) && (msg->data[6] == 0x12U);
  } else if (msg->addr == 0x306U) {
    const uint16_t checksum = 0x60U + msg->data[0] + msg->data[1] + msg->data[2] + msg->data[3] + msg->data[4] + msg->data[5];
    valid &= ((msg->data[0] & 0x3FU) == 0U) && (msg->data[1] == 0xF0U) && (msg->data[2] == 0x20U) &&
          (msg->data[3] == 0U) && (msg->data[4] == 0U) && (msg->data[5] == 0U) &&
          (msg->data[6] == (checksum >> 8)) && (msg->data[7] == (checksum & 0xFFU));
  } else if (msg->addr == 0x308U) {
    const uint16_t speed = (msg->data[1] << 4) | (msg->data[2] >> 4);
    const uint8_t near = (speed <= 0x27U) ? 1U : 0U;
    const uint8_t far = 1U - near;
    const uint8_t counter = msg->data[5] >> 5;
    const uint16_t checksum = 0x62U + far + (counter << 2) + msg->data[0] + msg->data[1] + msg->data[2];
    const uint8_t counter_bits = (uint8_t)(counter << 5);
    const uint8_t far_bit = (uint8_t)(far << 4);
    const uint8_t near_bit = (uint8_t)(near << 3);
    const uint8_t checksum_high = (uint8_t)(checksum >> 8);
    const uint8_t expected_byte_5 = (uint8_t)(counter_bits + far_bit + near_bit + checksum_high);
    valid &= (counter <= 3U) && (msg->data[0] == 0x08U) && ((msg->data[2] & 0xFU) == 0U) &&
          (msg->data[3] == 0U) && (msg->data[4] == 0U) &&
          (msg->data[5] == expected_byte_5) &&
          (msg->data[6] == (checksum & 0xFFU));
  } else if (msg->addr == 0x310U) {
    valid &= (msg->data[0] == 0x42U) && (msg->data[1] == 0x04U);
  } else {
    // No additional message shape applies in this range.
  }
  return valid;
}

static bool gm_vehicle_tx_hook(const CANPacket_t *msg) {
  const TorqueSteeringLimits GM_STEERING_LIMITS = {
    .max_torque = 300,
    .max_rate_up = 10,
    .max_rate_down = 15,
    .driver_torque_allowance = 65,
    .driver_torque_multiplier = 4,
    .max_rt_delta = 128,
    .type = TorqueDriverLimited,
  };
  const TorqueSteeringLimits GM_BOLT_2017_STEERING_LIMITS = {
    .max_torque = 450,
    .max_rate_up = 15,
    .max_rate_down = 34,
    .driver_torque_allowance = 78,
    .driver_torque_multiplier = 6,
    .max_rt_delta = 345,
    .type = TorqueDriverLimited,
  };

  bool tx = !gm_sdgm_invalid && !gm_cc_gateway_invalid && !gm_volt_invalid;

  // BRAKE: safety check
  if (msg->addr == 0x315U) {
    int brake = ((msg->data[0] & 0xFU) << 8) + msg->data[1];
    brake = (0x1000 - brake) & 0xFFF;
    const bool ordinary_brake_violation = longitudinal_brake_checks(brake, *gm_long_limits);
    if (gm_auto_hold && !get_longitudinal_allowed()) {
      const bool sensor_ready = gm_camera_hold_sensor_ready();
      tx &= ((brake == 0) || sensor_ready) && gm_hold_brake_tx(msg, brake);
    } else if (ordinary_brake_violation) {
      tx = false;
    } else {
      // Preserve ordinary longitudinal brake authority.
    }
    if (gm_auto_hold && get_longitudinal_allowed()) { gm_hold_counter_seen = false; gm_hold_accepted = false; }
    if (gm_pedal_acc) {
      (void)gm_pedal_brake_producer_current();
      const uint8_t mode = msg->data[0] >> 4;
      const uint8_t counter = msg->data[4] & 0x3U;
      const uint16_t checksum = ((uint16_t)msg->data[2] << 8) | msg->data[3];
      const uint32_t checksum_sum = ((uint32_t)mode << 12U) +
                                    (((uint32_t)msg->data[0] & 0xFU) << 8U) +
                                    (uint32_t)msg->data[1] + (uint32_t)counter;
      const uint16_t expected = (uint16_t)((0x10000U - checksum_sum) & 0xFFFFU);
      const bool owned = gm_pedal_sensor_current() && gm_pedal_owns_longitudinal() &&
                         gm_pedal_main_ready() && gm_pedal_drive_ready() && get_longitudinal_allowed() &&
                         !brake_pressed_prev && !regen_braking;
      const bool active = (brake > 0) && ((mode == 0xAU) || (mode == 0xDU)) && owned;
      const bool inactive = (brake == 0) && (((mode == 1U) && gm_pedal_owns_longitudinal() && gm_pedal_main_ready()) ||
                                              ((mode == 9U) && owned));
      if (!(active || inactive) || (checksum != expected) || ((msg->data[4] & 0xFCU) != 0U) ||
          (gm_pedal_brake_counter_seen && (counter == gm_pedal_brake_counter_last))) {
        tx = false;
      }
      if (tx) {
        gm_pedal_brake_counter_seen = true;
        gm_pedal_brake_counter_last = counter;
        gm_pedal_brake_producer_seen = true;
        gm_pedal_brake_producer_us = microsecond_timer_get();
      }
    }
  }

  // LKA STEER: safety check
  if (msg->addr == 0x180U) {
    int desired_torque = ((msg->data[0] & 0x7U) << 8) + msg->data[1];
    desired_torque = to_signed(desired_torque, 11);

    bool steer_req = GET_BIT(msg, 3U);

    if (gm_cancel_remap_enabled && ((desired_torque != 0) || steer_req) && !gm_pedal_main_ready()) {
      tx = false;
    }

    if ((gm_volt_cc_long && ((desired_torque != 0) || steer_req)) || (gm_ordinary_cc_long && (desired_torque != 0))) { tx &= gm_volt_cc_sources_current() && gm_volt_cc_main && (gm_volt_cc_active || gm_aol_lateral_allowed()) && gm_volt_cc_forward_gear; }
    if (steer_torque_cmd_checks(desired_torque, steer_req, gm_bolt_2017 ? GM_BOLT_2017_STEERING_LIMITS : GM_STEERING_LIMITS)) {
      tx = false;
    }
  }

  // Factory-ACC camera keepalive contains no actuation; permit only original literal layout.
  if (msg->addr == 0x2CDU) {
    const uint8_t counter = msg->data[0] >> 6;
    tx &= (msg->data[0] == (counter << 6)) && (msg->data[1] == 0x2CU) &&
          (msg->data[2] == 0x03U) && (msg->data[3] == 0xD3U) &&
          (msg->data[4] == (0xFDU - counter));
  }

  // GAS/REGEN: safety check
  if (msg->addr == 0x2CBU) {
    bool apply = GET_BIT(msg, 0U);
    // convert float CAN signal to an int for gas checks: 22534 / 0.125 = 180272
    int gas_regen = (((msg->data[1] & 0x7U) << 16) | (msg->data[2] << 8) | msg->data[3]) - 180272U;

    bool violation = false;
    // Allow apply bit in pre-enabled and overriding states
    violation |= !controls_allowed && apply;
    violation |= longitudinal_gas_checks(gas_regen, *gm_long_limits);

    if (violation) {
      tx = false;
    }
  }

  if (gm_acc_pedal_forward_owner && (msg->addr == 0x370U)) {
    const bool active = (msg->data[2] & 0x80U) != 0U;
    const uint16_t speed = ((uint16_t)(msg->data[2] & 0xFU) << 8U) | msg->data[3];
    const bool shape = (msg->data[0] == 1U) && (msg->data[1] == 2U) &&
      ((msg->data[2] & 0x40U) == 0U) && (speed <= 4080U) &&
      (msg->data[4] == 1U) && ((msg->data[5] & 0xECU) == 0U) &&
      (active || ((msg->data[2] & 0x30U) == 0U));
    tx &= shape && gm_acc_pedal_dashboard_current() && (!active || controls_allowed);
  }

  if (gm_ascm_intercept && (msg->addr == 0x370U)) {
    const bool active = (msg->data[2] & 0x80U) != 0U;
    tx &= (msg->data[0] & 1U) && (msg->data[4] & 1U) && (!active || controls_allowed);
  }

  if ((msg->addr == 0x200U) && gm_pedal_long) {
    const int track1 = (msg->data[0] << 8) | msg->data[1];
    const int track2 = (msg->data[2] << 8) | msg->data[3];
    const bool enabled = GET_BIT(msg, 39U);
    const uint8_t counter = msg->data[4] & 0xFU;
    const int pair_delta = track1 - (2 * track2);
    const bool inactive = !enabled && (track1 == 0) && (track2 == 0);
    const bool active = enabled && gm_pedal_sensor_current() && gm_pedal_owns_longitudinal() && gm_pedal_drive_ready() &&
                        (!(gm_pedal_acc || gm_cancel_remap_enabled) || gm_pedal_main_ready()) &&
                        get_longitudinal_allowed() && !brake_pressed_prev && !regen_braking &&
                        (track1 >= 604) && (track1 <= 2633) && (track2 >= 304) && (track2 <= 1316) &&
                        (pair_delta >= -16) && (pair_delta <= 16);
    if (!(inactive || active) || (gm_pedal_crc(msg) != msg->data[5]) ||
        (gm_pedal_tx_counter_seen && (counter == gm_pedal_tx_counter_last))) {
      tx = false;
    } else {
      gm_pedal_tx_counter_seen = true;
      gm_pedal_tx_counter_last = counter;
    }
  }

  if ((msg->addr == 0xBDU) && gm_paddle_sched) {
    const bool shape = (msg->data[0] == 0U) || (msg->data[0] == 0x20U);
    const bool applied = msg->data[0] == 0x20U;
    for (uint8_t i = 1U; i < 7U; i++) { if (msg->data[i] != 0U) { tx = false; } }
    if (!shape || (applied && !(gm_pedal_sensor_current() && gm_pedal_owns_longitudinal() &&
                               (!(gm_pedal_acc || gm_cancel_remap_enabled) || gm_pedal_main_ready()) &&
                               gm_pedal_regen_gear_ready() && !regen_braking && get_longitudinal_allowed()))) {
      tx = false;
    }
    if (!gm_paddle_internal_tx) {
      if (tx) {
        for (uint8_t i = 0U; i < 7U; i++) { gm_bd_feed.bytes[i] = msg->data[i]; }
        gm_bd_feed.valid = true;
        gm_bd_feed.last_feed_us = microsecond_timer_get();
      }
      tx = false;
    }
  }
  if ((msg->addr == 0x1F5U) && gm_paddle_sched) {
    const uint8_t prndl = msg->data[3] & 0xFU;
    const bool applied = (prndl == (gm_bolt_gen2 ? 5U : 7U)) && (msg->data[5] == 2U);
    const bool released = (prndl == (gm_bolt_pedal_drive_ready() ? 4U : 6U)) && (msg->data[5] == 0U);
    const bool shape = (msg->data[0] == 0x0CU) && (msg->data[1] == 0x0CU) && (msg->data[2] == 0U) &&
                       (msg->data[3] == prndl) && (msg->data[4] == 0U) && (msg->data[6] == 1U) &&
                       (msg->data[7] == 0U) && (applied || released);
    if (!shape || (applied && !(gm_pedal_sensor_current() && gm_pedal_owns_longitudinal() &&
                               (!(gm_pedal_acc || gm_cancel_remap_enabled) || gm_pedal_main_ready()) &&
                               gm_pedal_regen_gear_ready() && !regen_braking && get_longitudinal_allowed()))) {
      tx = false;
    }
    if (!gm_paddle_internal_tx) {
      if (tx) {
        for (uint8_t i = 0U; i < 8U; i++) { gm_gear_feed.bytes[i] = msg->data[i]; }
        gm_gear_feed.valid = true;
        gm_gear_feed.last_feed_us = microsecond_timer_get();
      }
      tx = false;
    }
  }

  // Preserve the registered admission and intercept/bus boundary.
  if (gm_ascm_intercept && (msg->bus == 1U)) {
    tx &= gm_ascm_adas_valid(msg);
  }

  if (gm_cc_pedal) { tx &= gm_cc_pedal_tx(msg); }

  // BUTTONS: used for resume spamming and cruise cancellation with stock longitudinal
  if ((msg->addr == 0x1E1U) && (gm_pcm_cruise || gm_pedal_acc)) {
    int button = (msg->data[5] >> 4) & 0x7U;

    bool allowed_cancel = (button == 6) && cruise_engaged_prev;
    if (gm_bolt_pedal_removed && gm_pedal_acc) {
      allowed_cancel = (button == 6) && gm_pedal_main_ready() && gm_bolt_removed_acc_seen &&
        (safety_get_ts_elapsed(microsecond_timer_get(), gm_bolt_removed_acc_us) <= 300000U) &&
        (cruise_engaged_prev || gm_bolt_removed_acc_active);
    }
    if (!allowed_cancel) {
      tx = false;
    }
  }

  if ((msg->addr == 0x1E1U) && gm_cc_gateway_stock) {
    const uint8_t counter = msg->data[4] & 0x3U;
    const uint16_t checksum = 0xFFU + (counter * 0x4EFU) - (5U << 4);
    tx &= cruise_engaged_prev && controls_allowed && gm_cc_stock_button_seen && !gm_cc_cancel_sent &&
          (counter == gm_cc_stock_button_counter) &&
          (safety_get_ts_elapsed(microsecond_timer_get(), gm_cc_stock_button_last_us) <= 100000U) &&
          (msg->data[0] == 0U) && (msg->data[1] == 0U) && (msg->data[2] == 0U) &&
          (msg->data[3] == 1U) && (msg->data[4] == counter) &&
          (msg->data[5] == (uint8_t)(0x60U | (checksum >> 8))) &&
          (msg->data[6] == (uint8_t)checksum);
    if (tx) { gm_cc_cancel_sent = true; }
  }

  if ((gm_volt_cc_long || gm_ordinary_cc_long) && (msg->addr == 0x1E1U)) {
    const uint8_t button = (msg->data[5] >> 4) & 0x7U;
    const uint8_t counter = (gm_volt_cc_counter + 1U) % 4U;
    const uint32_t button_offset = (button > 0U) ? ((uint32_t)button - 1U) : 0U;
    const uint16_t checksum = (uint16_t)(0xFFU + ((uint32_t)counter * 0x4EFU) - (button_offset << 4));
    const bool direction = !gm_malibu_cc_stock && ((button == GM_BTN_SET) || (button == GM_BTN_RESUME));
    const bool cancel = button == GM_BTN_CANCEL;
    // Cruise set speed is 0.0625 km/h; each rear wheel count is 0.0311 km/h.
    const bool gas_set = (button == GM_BTN_SET) && gas_pressed && longitudinal_controls_allowed() &&
                         gm_volt_cc_sources_current() && gm_volt_cc_main && gm_volt_cc_active &&
                         gm_volt_cc_forward_gear && gm_volt_cc_speed && !brake_pressed && !regen_braking &&
                         ((gm_volt_cc_stock_speed * 1250U) < (gm_volt_cc_wheel_speed_sum * 311U)) &&
                         (!gm_volt_cc_gas_set_seen || (safety_get_ts_elapsed(microsecond_timer_get(), gm_volt_cc_gas_set_us) >= 520000U));
    const uint32_t direction_interval = gm_ordinary_cc_long ? 20000U : 200000U;
    const uint32_t cancel_interval = gm_ordinary_cc_long ? 20000U : 40000U;
    const bool allowed = gm_volt_cc_neutral && gm_volt_cc_credit &&
                         ((direction && ((gm_volt_cc_current() && get_longitudinal_allowed()) || gas_set)) ||
                          (cancel && gm_volt_cc_sources_current() && gm_volt_cc_main && gm_volt_cc_active));
    tx &= allowed && (msg->data[0] == 0U) && (msg->data[1] == 0U) && (msg->data[2] == 0U) &&
          (msg->data[3] == 1U) && (msg->data[4] == counter) &&
          (msg->data[5] == ((button << 4) | (checksum >> 8))) && (msg->data[6] == (uint8_t)checksum) &&
          ((cancel && (!gm_volt_cc_cancel_seen || (safety_get_ts_elapsed(microsecond_timer_get(), gm_volt_cc_cancel_us) > cancel_interval))) ||
           (direction && (!gm_volt_cc_tx_seen || (safety_get_ts_elapsed(microsecond_timer_get(), gm_volt_cc_tx_us) > direction_interval))));
    if (tx) {
      gm_volt_cc_credit = false;
      if (gas_set) {
        gm_volt_cc_gas_set_seen = true;
        gm_volt_cc_gas_set_us = microsecond_timer_get();
      }
      if (cancel) {
        gm_volt_cc_cancel_seen = true;
        gm_volt_cc_cancel_us = microsecond_timer_get();
      } else {
        gm_volt_cc_tx_seen = true;
        gm_volt_cc_tx_us = microsecond_timer_get();
      }
    }
  }

  if ((gm_volt_camera_removed || ((gm_camera_gateway || gm_camera_ascm || gm_camera_sdgm) && !gm_camera_pedal_long)) && (msg->addr == 0x1E1U)) {
    const uint32_t now = microsecond_timer_get();
    const uint8_t counter = msg->data[4] & 0x3U;
    const uint16_t checksum = 0xFFU + (counter * 0x4EFU) - (5U << 4U);
    tx &= !gm_volt_removed_long && gm_volt_removed_main && gm_volt_removed_pcm &&
          gm_volt_removed_credit && gm_volt_removed_button_seen &&
          (counter == gm_volt_removed_counter) &&
          (safety_get_ts_elapsed(now, gm_volt_removed_button_us) <= 100000U) &&
          (safety_get_ts_elapsed(now, gm_volt_removed_main_us) <= 300000U) &&
          (safety_get_ts_elapsed(now, gm_volt_removed_pcm_us) <= 300000U) &&
          (!gm_volt_removed_cancel_seen || (safety_get_ts_elapsed(now, gm_volt_removed_cancel_us) > 40000U)) &&
          (msg->data[0] == 0U) && (msg->data[1] == 0U) && (msg->data[2] == 0U) &&
          (msg->data[3] == 1U) && (msg->data[4] == counter) &&
          (msg->data[5] == (uint8_t)(0x60U | (checksum >> 8))) && (msg->data[6] == (uint8_t)checksum);
    if (tx) {
      gm_volt_removed_credit = false;
      gm_volt_removed_cancel_seen = true;
      gm_volt_removed_cancel_us = now;
    }
  }

  if ((gm_bolt_pedal_removed || gm_bolt_present_cc_cancel) && !gm_pedal_acc && (msg->addr == 0x1E1U)) {
    const uint32_t now = microsecond_timer_get();
    const uint8_t counter = (gm_volt_removed_counter + 1U) % 4U;
    const uint16_t checksum = 0xFFU + (counter * 0x4EFU) - (5U << 4U);
    tx &= !safety_rx_checks_invalid && gm_pedal_sensor_current() && gm_bolt_removed_gear_seen &&
          (!gm_bolt_cancel_forward_gear_required || gm_pedal_forward_gear_ready) &&
          (safety_get_ts_elapsed(now, gm_regen_gear_last_us) <= 100000U) &&
          gm_volt_removed_main && gm_volt_removed_pcm && gm_bolt_removed_acc_seen && gm_bolt_removed_acc_active &&
          gm_volt_removed_credit && gm_volt_removed_button_seen &&
          (safety_get_ts_elapsed(now, gm_volt_removed_button_us) <= 100000U) &&
          (safety_get_ts_elapsed(now, gm_volt_removed_main_us) <= 300000U) &&
          (safety_get_ts_elapsed(now, gm_volt_removed_pcm_us) <= 300000U) &&
          (!gm_volt_removed_cancel_seen || (safety_get_ts_elapsed(now, gm_volt_removed_cancel_us) > 40000U)) &&
          (msg->data[0] == 0U) && (msg->data[1] == 0U) && (msg->data[2] == 0U) &&
          (msg->data[3] == 1U) && (msg->data[4] == counter) &&
          (msg->data[5] == (uint8_t)(0x60U | (checksum >> 8))) && (msg->data[6] == (uint8_t)checksum);
    gm_volt_removed_credit = false;
    if (tx) { gm_volt_removed_cancel_seen = true; gm_volt_removed_cancel_us = now; }
  }

  if ((gm_ordinary_cc_long || gm_bolt_pedal_removed) && ((msg->addr == 0x409U) || (msg->addr == 0x40AU))) {
    for (uint8_t i = 0U; i < 7U; i++) { tx &= msg->data[i] == 0U; }
  }

  tx = gm_camera_pedal_tx(msg, tx);
  if (gm_acc_pedal_forward_owner && (msg->addr == 0x370U) && tx &&
      !relay_malfunction && !safety_rx_checks_invalid) {
    gm_acc_pedal_dashboard_seen = true;
    gm_acc_pedal_dashboard_us = microsecond_timer_get();
  }
  return tx;
}

static bool gm_tx_hook(const CANPacket_t *msg) {
  // This topology has no camera-side receiver after harness interception.
  return !(gm_volt_camera_removed && (msg->bus == 2U)) && gm_vehicle_tx_hook(msg);
}

static bool gm_fwd_hook(int bus_num, int addr) {
  const bool dashboard_owned = gm_acc_pedal_dashboard_current() && gm_acc_pedal_dashboard_seen &&
    (safety_get_ts_elapsed(microsecond_timer_get(), gm_acc_pedal_dashboard_us) <= 100000U);
  if (gm_pedal_acc) { (void)gm_pedal_brake_producer_current(); }
  // SDGM replaces the PT PSCM status at the camera. Frozen SDGM topology
  // blocks this direction without treating camera PSCM traffic as a relay fault.
  return (dashboard_owned && (bus_num == 2) && (addr == 0x370)) ||
         (gm_bolt_pedal_removed && (((bus_num == 0) && (addr == 0x184)) ||
          ((bus_num == 2) && (addr == 0x180)))) ||
         (gm_camera_gateway && gm_camera_gateway_removed && (bus_num == 0) && (addr == 0x184)) ||
         (gm_camera_gateway && gm_camera_pedal_long && (bus_num == 2) && ((addr == 0x180) || (addr == 0x315) || (addr == 0x2CB) || (addr == 0x370) || (addr == 0x2CD))) ||
         (gm_cc_pedal_silverado && (bus_num == 0) && ((addr == 0x184) || (addr == 0x3D1))) ||
         (gm_cc_pedal_silverado && (bus_num == 2) && ((addr == 0x180) || (addr == 0x370))) ||
         (gm_ordinary_camera_removed && (bus_num == 0) && (addr == 0x184)) ||
         (gm_ordinary_camera_removed && (bus_num == 2) && ((addr == 0x180) ||
          (gm_ordinary_camera_removed_long && ((addr == 0x315) || (addr == 0x2CB) || (addr == 0x370) || (addr == 0x2CD))))) ||
         (gm_sdgm && (bus_num == 0) && (addr == 0x184)) ||
         ((gm_volt_sdgm_long || gm_ordinary_sdgm_long) && (bus_num == 2) && ((addr == 0x315) || (addr == 0x2CD))) ||
         (gm_pedal_acc && (bus_num == 2) && (addr == 0x315) && gm_pedal_owns_longitudinal() && gm_pedal_main_ready() &&
          gm_pedal_brake_producer_current());
}

static safety_config gm_init(uint16_t safety_param) {
  uint16_t bolt_param = safety_param;
  gm_bolt_pedal_removed = false;
  gm_bolt_removed_acc_seen = false;
  gm_bolt_removed_gear_seen = false;
  gm_bolt_removed_acc_active = false;
  gm_bolt_removed_acc_us = 0U;
  switch (safety_param) {
    case 0xE700U: bolt_param = 0xBDU; gm_bolt_pedal_removed = true; break;
    case 0xE701U: bolt_param = 0x9DU; gm_bolt_pedal_removed = true; break;
    case 0xE702U: bolt_param = 0x19DU; gm_bolt_pedal_removed = true; break;
    case 0xE703U: bolt_param = 0x1CDU; gm_bolt_pedal_removed = true; break;
    default: break;
  }
  const uint16_t camera_param = gm_camera_pedal_reset(gm_cc_pedal_volt_decode(bolt_param));
  uint16_t param = gm_one_pedal_reset(camera_param);
  gm_cc_pedal_silverado = (safety_param == 0xC182U) || (safety_param == 0xC183U) ||
                          (safety_param == 0xC184U) || (safety_param == 0xC185U);
  gm_cc_pedal_ordinary_stock = (safety_param == 0xC186U) || (safety_param == 0xC187U);
  gm_cc_pedal_stock_only = (safety_param == 0xC184U) || (safety_param == 0xC185U) || gm_cc_pedal_ordinary_stock || gm_cc_pedal_volt_stock;
  gm_cc_pedal = (safety_param == 0xC180U) || (safety_param == 0xC181U) || gm_cc_pedal_silverado || gm_cc_pedal_ordinary_stock || gm_cc_pedal_volt;
  const bool gm_cc_pedal_removed = (safety_param == 0xC181U) || (safety_param == 0xC183U) ||
                                    (safety_param == 0xC185U) || (safety_param == 0xC187U) || gm_cc_pedal_volt_removed;
  gm_cc_pedal_reset();
  const uint16_t GM_PARAM_HW_CAM = 1;
  const uint16_t GM_PARAM_EV = 4;
  const uint16_t GM_PARAM_PEDAL_LONG = 8;
  const uint16_t GM_PARAM_NO_ACC = 16;
  const uint16_t GM_PARAM_BOLT_2017 = 32;
  const uint16_t GM_PARAM_BOLT_ACC_PEDAL = 64;
  const uint16_t GM_PARAM_PADDLE_SCHED = 128;
  const uint16_t GM_PARAM_BOLT_GEN2 = 256;
  const uint16_t GM_PARAM_ASCM_INTERCEPT = 512;
  const uint16_t GM_PARAM_ASCM_BRAKE_C9 = 1024;
  const uint16_t GM_PARAM_ASCM_RADAR = 2048;
  const uint16_t GM_PARAM_SDGM = 4096;
  const uint16_t GM_PARAM_SDGM_CANCEL_PT = 8192;
  const uint16_t GM_PARAM_VOLT_LONG = 16384;
  const uint16_t GM_PARAM_VOLT_GATEWAY_ALT_BRAKE = 32768U;
  const uint16_t GM_PARAM_HW_CAM_LONG = 2;
  const bool volt_hold_standard = param == (GM_PARAM_EV | GM_PARAM_VOLT_LONG | GM_PARAM_PADDLE_SCHED);
  const bool volt_hold_alternate = param == (GM_PARAM_EV | GM_PARAM_VOLT_LONG | GM_PARAM_PADDLE_SCHED | GM_PARAM_VOLT_GATEWAY_ALT_BRAKE);
  const bool hold_ice = param == 0x80U;
  GMHoldConfig hold_config = {
    .enabled = volt_hold_standard || volt_hold_alternate || hold_ice,
    .alternate = volt_hold_alternate,
    .extended_be = hold_ice,
    .regen = !hold_ice,
    .tx_bus = (volt_hold_standard || hold_ice) ? 2U : 0U,
  };
#ifdef ALLOW_DEBUG
  const bool volt_hold_ascm = (param == 0x4287U) || (param == 0x4687U) || (param == 0x4A87U) || (param == 0x4E87U);
  const bool volt_hold_camera = param == 0x4087U;
  const bool volt_hold_sdgm = (param == 0x5087U) || (param == 0x5487U);
  const bool hold_removed = (param == 0xC1D1U) || (param == 0xC1D3U);
  hold_config.extended_be |= volt_hold_ascm || volt_hold_camera || volt_hold_sdgm;
  hold_config.c9_brake = volt_hold_camera || hold_removed || ((volt_hold_ascm || volt_hold_sdgm) && GET_FLAG(param, GM_PARAM_ASCM_BRAKE_C9));
  hold_config.enabled |= hold_config.extended_be || hold_removed;
  hold_config.sdgm = volt_hold_sdgm;
  hold_config.analog_required = hold_removed;
  hold_config.alternate |= param == 0xC1D3U;
  if (volt_hold_sdgm) { hold_config.tx_bus = 2U; }
#endif
#ifdef ALLOW_DEBUG
  if (gm_camera_volt && gm_camera_pedal_long && hold_config.enabled) {
    hold_config.c9_brake = !gm_camera_gateway || !gm_camera_pedal_f1;
    if (gm_camera_ascm || gm_camera_sdgm) { hold_config.c9_brake = gm_camera_pedal_f1; }
    hold_config.alternate = gm_camera_pedal_f1;
    hold_config.analog_required = true;
    hold_config.extended_be = (gm_camera_ascm || gm_camera_sdgm) && !gm_camera_pedal_f1;
    if (gm_camera_gateway || gm_camera_ascm) { hold_config.tx_bus = 0U; }
  }
#endif
  gm_hold_reset(&hold_config);
  if (gm_auto_hold) { param &= (uint16_t)(~GM_PARAM_PADDLE_SCHED); }
#ifdef ALLOW_DEBUG
  // The F1 contextual word is not a generic bit-mask alias of C151.
  if (hold_removed) { param = 0xC151U; }
#endif

  if (gm_cc_pedal) { param = GM_PARAM_HW_CAM | (gm_cc_pedal_volt ? GM_PARAM_EV : 0U); }
  gm_volt_camera_removed = param == 0xC150U;
  gm_volt_removed_long = false;
#ifdef ALLOW_DEBUG
  gm_volt_removed_long = param == 0xC151U;
  gm_volt_camera_removed |= gm_volt_removed_long;
#endif
  // Decode only these exact owners into common driver-input and codec semantics.
  // Their RX, TX and forwarding tables are installed independently below.
  if (gm_volt_camera_removed) {
    param = GM_PARAM_HW_CAM | GM_PARAM_EV;
#ifdef ALLOW_DEBUG
    if (gm_volt_removed_long) { param |= GM_PARAM_HW_CAM_LONG; }
#endif
  }
  gm_volt_removed_main = false;
  gm_volt_removed_pcm = false;
  gm_volt_removed_credit = false;
  gm_volt_removed_button_seen = false;
  gm_volt_removed_counter = 0U;
  gm_volt_removed_button_us = 0U;
  gm_volt_removed_button_observed_us = 0U;
  gm_volt_removed_main_us = 0U;
  gm_volt_removed_pcm_us = 0U;
  gm_volt_removed_cancel_seen = false;
  gm_volt_removed_cancel_us = 0U;

  gm_volt_cc_f1 = false;
#ifdef ALLOW_DEBUG
  gm_volt_cc_f1 = safety_param == 0xC163U;
  if (gm_volt_cc_f1) { param = GM_PARAM_EV | GM_PARAM_NO_ACC; }
#endif
  gm_malibu_cc_f1 = (safety_param == 0xC161U) || (safety_param == 0xC162U);
  gm_malibu_cc_stock = safety_param == 0xC162U;
  gm_malibu_f1_brake = false;
  gm_malibu_c9_brake = false;
  gm_ordinary_cc_long = (safety_param == 0xC160U) || gm_malibu_cc_f1;
  if (gm_ordinary_cc_long) { param = GM_PARAM_NO_ACC; }
  gm_ordinary_camera_removed = camera_param == 0xC172U;
  gm_ordinary_camera_removed_long = false;
#ifdef ALLOW_DEBUG
  gm_ordinary_camera_removed_long = camera_param == 0xC173U;
  gm_ordinary_camera_removed |= gm_ordinary_camera_removed_long;
#endif
  if (gm_ordinary_camera_removed) { param = GM_PARAM_HW_CAM; }
#ifdef ALLOW_DEBUG
  if (gm_ordinary_camera_removed_long) { param |= GM_PARAM_HW_CAM_LONG; }
#endif
  const bool gm_ordinary_camera_stock = camera_param == 0xC171U;
  if (gm_ordinary_camera_stock) { param = GM_PARAM_HW_CAM; }
#ifdef ALLOW_DEBUG
  const bool gm_ordinary_camera_long = camera_param == 0xC170U;
  if (gm_ordinary_camera_long) { param = GM_PARAM_HW_CAM | GM_PARAM_HW_CAM_LONG; }
#endif

  // common safety checks assume unscaled integer values
  static const int GM_GAS_TO_CAN = 8;  // 1 / 0.125

  static const LongitudinalLimits GM_ASCM_LONG_LIMITS = {
    .max_gas = 1018 * GM_GAS_TO_CAN,
    .min_gas = -650 * GM_GAS_TO_CAN,
    .inactive_gas = -650 * GM_GAS_TO_CAN,
    .max_brake = 400,
  };

  static const LongitudinalLimits GM_VOLT_LONG_LIMITS = {
    .max_gas = 2041 * GM_GAS_TO_CAN,
    .min_gas = -650 * GM_GAS_TO_CAN,
    .inactive_gas = -650 * GM_GAS_TO_CAN,
    .max_brake = 400,
  };

  static const CanMsg GM_ASCM_TX_MSGS[] = {
    {0x180, 0, 4, .check_relay = true},
    {0x409, 0, 7, .check_relay = false},
    {0x40A, 0, 7, .check_relay = false},
    {0x2CB, 0, 8, .check_relay = true},
    {0x370, 0, 6, .check_relay = false},
    {0xA1, 1, 7, .check_relay = false},
    {0x306, 1, 8, .check_relay = false},
    {0x308, 1, 7, .check_relay = false},
    {0x310, 1, 2, .check_relay = false},
    {0x315, 2, 5, .check_relay = false},
  };


  static const CanMsg GM_VOLT_GATEWAY_ALT_BRAKE_TX_MSGS[] = {
    {0x180, 0, 4, .check_relay = true}, {0x409, 0, 7, .check_relay = false}, {0x40A, 0, 7, .check_relay = false},
    {0x2CB, 0, 8, .check_relay = true}, {0x370, 0, 6, .check_relay = false}, {0x315, 0, 5, .check_relay = true},
    {0xA1, 1, 7, .check_relay = false}, {0x306, 1, 8, .check_relay = false},
    {0x308, 1, 7, .check_relay = false}, {0x310, 1, 2, .check_relay = false},
  };

  static const LongitudinalLimits GM_CAM_LONG_LIMITS = {
    .max_gas = 1346 * GM_GAS_TO_CAN,
    .min_gas = -540 * GM_GAS_TO_CAN,
    .inactive_gas = -500 * GM_GAS_TO_CAN,
    .max_brake = 400,
  };

#ifdef ALLOW_DEBUG
  // Original SDGM camera command range, normalized legacy raw - 6150.
  static const LongitudinalLimits GM_ORDINARY_SDGM_LONG_LIMITS = {
    .max_gas = 2698 * GM_GAS_TO_CAN,
    .min_gas = -540 * GM_GAS_TO_CAN,
    .inactive_gas = -500 * GM_GAS_TO_CAN,
    .max_brake = 400,
  };
  // Exact ordinary factory-ACC EV camera profile, normalized legacy raw - 6150.
  static const LongitudinalLimits GM_BOLT_EUV_LONG_LIMITS = {
    .max_gas = 2698 * GM_GAS_TO_CAN,
    .min_gas = -540 * GM_GAS_TO_CAN,
    .inactive_gas = -500 * GM_GAS_TO_CAN,
    .max_brake = 400,
  };
  static const CanMsg GM_BOLT_EUV_LONG_TX_MSGS[] = {
    {0x180, 0, 4, .check_relay = true}, {0x315, 0, 5, .check_relay = true},
    {0x2CB, 0, 8, .check_relay = true}, {0x370, 0, 6, .check_relay = true},
    {0x2CD, 0, 5, .check_relay = true}, {0x184, 2, 8, .check_relay = true},
  };
  // block PSCMStatus (0x184); forwarded through openpilot to hide an alert from the camera
  static const CanMsg GM_CAM_LONG_TX_MSGS[] = {
    {0x180, 0, 4, .check_relay = true},
    {0x315, 0, 5, .check_relay = true},
    {0x2CB, 0, 8, .check_relay = true},
    {0x370, 0, 6, .check_relay = true},
    {0x184, 2, 8, .check_relay = true},
  };
  static const CanMsg GM_CAM_LONG_ASCM_RADAR_TX_MSGS[] = {{0x180, 0, 4, .check_relay = true}, {0x315, 0, 5, .check_relay = true}, {0x2CB, 0, 8, .check_relay = true}, {0x370, 0, 6, .check_relay = true},
                                                          {0xA1, 1, 7, .check_relay = false}, {0x306, 1, 8, .check_relay = false}, {0x308, 1, 7, .check_relay = false}, {0x310, 1, 2, .check_relay = false},
                                                          {0x184, 2, 8, .check_relay = true}};
#endif


  static const CanMsg GM_VOLT_REMOVED_STOCK_TX_MSGS[] = {
    {0x180, 0, 4, .check_relay = false}, {0x1E1, 0, 7, .check_relay = false},
  };
#ifdef ALLOW_DEBUG
  static const CanMsg GM_VOLT_REMOVED_LONG_TX_MSGS[] = {
    {0x180, 0, 4, .check_relay = false}, {0x315, 0, 5, .check_relay = false},
    {0x2CB, 0, 8, .check_relay = false}, {0x370, 0, 6, .check_relay = false},
    {0x409, 0, 7, .check_relay = false}, {0x40A, 0, 7, .check_relay = false},
  };
#endif
  static RxCheck gm_volt_removed_rx_checks[] = {
    {.msg = {{0x184, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x34A, 0, 5, 20U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1E1, 0, 7, 33U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1C4, 0, 8, 33U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xC9, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xBD, 0, 7, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

  static const RxCheck gm_ordinary_camera_rx_template[] = {
    {.msg = {{0x184, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x34A, 0, 5, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1E1, 0, 7, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xF1, 0, 6, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1C4, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xC9, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

#ifdef ALLOW_DEBUG
  static const RxCheck gm_camera_extended_rx_template[] = {
    {.msg = {{0x184, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x34A, 0, 5, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1E1, 0, 7, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xF1, 0, 6, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1C4, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xC9, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x201, 0, 6, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1F5, 0, 8, 40U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

#endif
  for (uint8_t i = 0U; i < 10U; i++) { gm_extended_rx_checks[i] = (RxCheck){0}; }
  for (uint8_t i = 0U; i < 6U; i++) { gm_extended_rx_checks[i] = gm_ordinary_camera_rx_template[i]; }
  gm_extended_rx_checks[3].msg[0].addr = (gm_camera_pedal && !gm_camera_pedal_f1) ? 0xBEU : 0xF1U;


  static const RxCheck gm_rx_checks[] = {
    GM_COMMON_RX_CHECKS
  };
#define GM_ASCM_INTERCEPT_RX_CHECKS \
    {.msg = {{0x184, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
    {.msg = {{0x34A, 0, 5, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
    {.msg = {{0x1E1, 0, 7, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \
    {.msg = {{0x1C4, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \

#define GM_ASCM_INTERCEPT_BE_CHECK \
    {.msg = {{0xBE, 0, 6, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, \
             {0xBE, 0, 7, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, \
             {0xBE, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}}}, \

#define GM_ASCM_INTERCEPT_C9_CHECK \
    {.msg = {{0xC9, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \

#define GM_ASCM_INTERCEPT_EV_CHECK \
    {.msg = {{0xBD, 0, 7, 40U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}}, \

  static RxCheck gm_ascm_be_rx_checks[] = {GM_ASCM_INTERCEPT_RX_CHECKS GM_ASCM_INTERCEPT_BE_CHECK};
  static RxCheck gm_ascm_c9_rx_checks[] = {GM_ASCM_INTERCEPT_RX_CHECKS GM_ASCM_INTERCEPT_C9_CHECK};
  static RxCheck gm_ascm_be_ev_rx_checks[] = {GM_ASCM_INTERCEPT_RX_CHECKS GM_ASCM_INTERCEPT_BE_CHECK GM_ASCM_INTERCEPT_EV_CHECK};
  static RxCheck gm_ascm_c9_ev_rx_checks[] = {GM_ASCM_INTERCEPT_RX_CHECKS GM_ASCM_INTERCEPT_C9_CHECK GM_ASCM_INTERCEPT_EV_CHECK};
  static RxCheck gm_aol_be_rx_checks[] = {
    GM_ASCM_INTERCEPT_RX_CHECKS
    GM_ASCM_INTERCEPT_BE_CHECK
    GM_ASCM_INTERCEPT_C9_CHECK
  };
  static RxCheck gm_aol_be_ev_rx_checks[] = {
    GM_ASCM_INTERCEPT_RX_CHECKS
    GM_ASCM_INTERCEPT_BE_CHECK
    GM_ASCM_INTERCEPT_C9_CHECK
    GM_ASCM_INTERCEPT_EV_CHECK
  };

  static RxCheck gm_volt_alt_brake_rx_checks[] = {
    GM_ASCM_INTERCEPT_RX_CHECKS
    GM_ASCM_INTERCEPT_C9_CHECK
    GM_ASCM_INTERCEPT_EV_CHECK
    {.msg = {{0xF1, 0, 6, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };
  static const RxCheck gm_hold_extended_rx_template[] = {
    GM_ASCM_INTERCEPT_RX_CHECKS
    GM_ASCM_INTERCEPT_C9_CHECK
    GM_ASCM_INTERCEPT_EV_CHECK
    {.msg = {{0x1F5, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x232, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xBE, 0, 6, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

  // These mutually exclusive owners share RAM; every init copies complete flash metadata and clears status.


  static RxCheck gm_volt_cc_rx_checks[] = {
    {.msg = {{0x184, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x34A, 0, 5, 20U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1E1, 0, 7, 33U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xBE, 0, 6, 80U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1C4, 0, 8, 33U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xC9, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x3D1, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1F5, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xBD, 0, 7, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

  static RxCheck gm_ordinary_cc_rx_checks[] = {
    {.msg = {{0x184, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x34A, 0, 5, 20U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1E1, 0, 7, 33U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xBE, 0, 6, 80U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1C4, 0, 8, 33U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xC9, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x3D1, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1F5, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

  static const RxCheck gm_ev_rx_checks[] = {
    GM_COMMON_RX_CHECKS
    {.msg = {{0xBD, 0, 7, 40U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };
  static const RxCheck gm_pedal_rx_template[] = {
    {.msg = {{0x184, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x34A, 0, 5, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1E1, 0, 7, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1C4, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xC9, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x201, 0, 6, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xBD, 0, 7, 40U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1F5, 0, 8, 40U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };
  static RxCheck gm_noacc_rx_checks[] = {
    {.msg = {{0x184, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x34A, 0, 5, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1E1, 0, 7, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1C4, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0xC9, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };
  static RxCheck gm_cc_gateway_stock_rx_checks[] = {
    GM_COMMON_RX_CHECKS
    {.msg = {{0x3D1, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
    {.msg = {{0x1F5, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, { 0 }, { 0 }}},
  };

  static const CanMsg GM_CAM_TX_MSGS[] = {
    {0x180, 0, 4, .check_relay = true},
    {0x1E1, 2, 7, .check_relay = false},
    {0x184, 2, 8, .check_relay = true},
  };
  // Stock SDGM camera commands are forwarded. Only steering, cancel, and the
  // PSCM alert suppression frame are generated by this host controller.
  static const CanMsg GM_SDGM_PT_CANCEL_TX_MSGS[] = {{0x180, 0, 4, .check_relay = true},
                                                     {0x1E1, 0, 7, .check_relay = false},
                                                     {0x184, 2, 8, .check_relay = false}};
  static const CanMsg GM_SDGM_CAM_CANCEL_TX_MSGS[] = {{0x180, 0, 4, .check_relay = true},
                                                      {0x1E1, 2, 7, .check_relay = false},
                                                      {0x184, 2, 8, .check_relay = false}};
  static const CanMsg GM_BOLT_NOACC_TX_MSGS[] = {{0x180, 0, 4, .check_relay = true}, {0x184, 2, 8, .check_relay = true}};
  static const CanMsg GM_CC_GATEWAY_STOCK_TX_MSGS[] = {{0x180, 0, 4, .check_relay = true},
                                                       {0x1E1, 0, 7, .check_relay = false}};
  static const CanMsg GM_BOLT_PEDAL_TX_MSGS[] = {{0x180, 0, 4, .check_relay = true}, {0x200, 0, 6, .check_relay = false},
                                                {0xBD, 0, 7, .check_relay = false}, {0x1F5, 0, 8, .check_relay = false},
                                                {0x184, 2, 8, .check_relay = true}};
  static const CanMsg GM_BOLT_ACC_PEDAL_TX_MSGS[] = {{0x180, 0, 4, .check_relay = true}, {0x200, 0, 6, .check_relay = false},
                                                    {0x370, 0, 6, .check_relay = false},
                                                    {0x315, 0, 5, .check_relay = false},
                                                    {0xBD, 0, 7, .check_relay = false}, {0x1F5, 0, 8, .check_relay = false},
                                                    {0x184, 2, 8, .check_relay = true}, {0x1E1, 2, 7, .check_relay = false}};

  if (GET_FLAG(param, GM_PARAM_HW_CAM)) {
    gm_hw = GM_CAM;
    gm_long_limits = &GM_CAM_LONG_LIMITS;
  } else {
    gm_hw = GM_ASCM;
    gm_long_limits = &GM_ASCM_LONG_LIMITS;
  }

  gm_volt_sdgm_long = false;
  gm_ordinary_sdgm_long = false;
  gm_volt_gateway_alt_brake = param == (GM_PARAM_EV | GM_PARAM_VOLT_LONG | GM_PARAM_VOLT_GATEWAY_ALT_BRAKE);
  const bool gm_volt_gateway_long = (param == (GM_PARAM_EV | GM_PARAM_VOLT_LONG)) || gm_volt_gateway_alt_brake;
  bool gm_volt_long = gm_volt_gateway_long;
#ifdef ALLOW_DEBUG
  gm_ordinary_sdgm_long = (param == 0x1003U) || (param == 0x1403U);
  const uint16_t gm_volt_ascm_required = GM_PARAM_EV | GM_PARAM_HW_CAM | GM_PARAM_HW_CAM_LONG | GM_PARAM_ASCM_INTERCEPT | GM_PARAM_VOLT_LONG;
  const uint16_t gm_volt_ascm_optional = GM_PARAM_ASCM_BRAKE_C9 | GM_PARAM_ASCM_RADAR;
  const bool gm_volt_ascm_long = ((param & gm_volt_ascm_required) == gm_volt_ascm_required) &&
                                ((param & (uint16_t)(~(gm_volt_ascm_required | gm_volt_ascm_optional))) == 0U);
  const bool gm_volt_camera_long = param == (GM_PARAM_EV | GM_PARAM_HW_CAM | GM_PARAM_HW_CAM_LONG | GM_PARAM_VOLT_LONG);
  const uint16_t gm_volt_sdgm_required = GM_PARAM_EV | GM_PARAM_HW_CAM | GM_PARAM_HW_CAM_LONG | GM_PARAM_SDGM | GM_PARAM_VOLT_LONG;
  gm_volt_sdgm_long = (param == gm_volt_sdgm_required) || (param == (gm_volt_sdgm_required | GM_PARAM_ASCM_BRAKE_C9));
  gm_volt_long |= gm_volt_ascm_long || gm_volt_camera_long || gm_volt_sdgm_long;
#endif
  gm_volt_invalid = (GET_FLAG(param, GM_PARAM_VOLT_LONG) || GET_FLAG(param, GM_PARAM_VOLT_GATEWAY_ALT_BRAKE)) &&
                    !gm_volt_long;
#ifdef ALLOW_DEBUG
  const uint16_t gm_ordinary_ascm_required = GM_PARAM_HW_CAM | GM_PARAM_HW_CAM_LONG | GM_PARAM_ASCM_INTERCEPT;
  const uint16_t gm_ordinary_ascm_optional = GM_PARAM_ASCM_BRAKE_C9 | GM_PARAM_ASCM_RADAR;
  const bool gm_ordinary_ascm_long = ((param & gm_ordinary_ascm_required) == gm_ordinary_ascm_required) &&
                                   ((param & (uint16_t)(~(gm_ordinary_ascm_required | gm_ordinary_ascm_optional))) == 0U);
  if (gm_ordinary_ascm_long) {
    gm_long_limits = &GM_VOLT_LONG_LIMITS;
  }
#endif
  if (gm_volt_long) {
    gm_long_limits = &GM_VOLT_LONG_LIMITS;
  }
#ifdef ALLOW_DEBUG
  if (gm_ordinary_sdgm_long) {
    gm_long_limits = &GM_ORDINARY_SDGM_LONG_LIMITS;
  }
  if (gm_volt_camera_long || gm_volt_sdgm_long) {
    gm_long_limits = &GM_BOLT_EUV_LONG_LIMITS;
  }
  const bool volt_sdgm_accepted_envelope = (safety_param == 0x5007U) || (safety_param == 0x5407U) ||
    (safety_param == 0x5087U) || (safety_param == 0x5487U) ||
    (safety_param == 0xD107U) || (safety_param == 0xD108U) ||
    (safety_param == 0xD117U) || (safety_param == 0xD118U);
  if (!gm_camera_pedal && volt_sdgm_accepted_envelope) {
    static const LongitudinalLimits GM_VOLT_SDGM_ACCEPTED_LIMITS = {
      .max_gas = 2041 * GM_GAS_TO_CAN,
      .min_gas = -540 * GM_GAS_TO_CAN,
      .inactive_gas = -500 * GM_GAS_TO_CAN,
      .max_brake = 400,
    };
    gm_long_limits = &GM_VOLT_SDGM_ACCEPTED_LIMITS;
  }
#endif

  gm_pcm_cruise = (gm_hw == GM_CAM);
  const bool gm_no_acc = (gm_hw == GM_CAM) && GET_FLAG(param, GM_PARAM_NO_ACC);
  gm_volt_cc_long = false;
#ifdef ALLOW_DEBUG
  gm_volt_cc_long = param == (GM_PARAM_EV | GM_PARAM_NO_ACC);
#endif
  for (uint8_t i = 0U; i < 8U; i++) {
    gm_volt_cc_seen[i] = false;
    gm_volt_cc_last_us[i] = 0U;
  }
  gm_volt_cc_main = false;
  gm_volt_cc_active = false;
  gm_volt_cc_forward_gear = false;
  gm_volt_cc_speed = false;
  gm_volt_cc_neutral = false;
  gm_volt_cc_credit = false;
  gm_volt_cc_tx_seen = false;
  gm_volt_cc_tx_us = 0U;
  gm_volt_cc_cancel_seen = false;
  gm_volt_cc_cancel_us = 0U;
  gm_volt_cc_stock_speed = 0U;
  gm_volt_cc_wheel_speed_sum = 0U;
  gm_volt_cc_gas_set_seen = false;
  gm_volt_cc_gas_set_us = 0U;
  gm_cc_gateway_stock = (param == GM_PARAM_NO_ACC) && !gm_ordinary_cc_long;
  gm_cc_gateway_invalid = !GET_FLAG(param, GM_PARAM_HW_CAM) && GET_FLAG(param, GM_PARAM_NO_ACC) && !gm_cc_gateway_stock && !gm_ordinary_cc_long;
#ifdef ALLOW_DEBUG
  gm_cc_gateway_invalid = gm_cc_gateway_invalid && !gm_volt_cc_long;
#endif
  gm_cc_stock_button_seen = false;
  gm_cc_cancel_sent = false;
  gm_cc_stock_button_counter = 0U;
  gm_cc_stock_button_last_us = 0U;
  gm_bolt_present_cc_cancel = (safety_param == 0xBDU) || (safety_param == 0x9DU) || (safety_param == 0x19DU);
  // E701 historically shared the forward-gear guard through its canonical 9D word.
  gm_bolt_cancel_forward_gear_required = gm_bolt_present_cc_cancel || (safety_param == 0xE701U);
  gm_pedal_long = (gm_hw == GM_CAM) && GET_FLAG(param, GM_PARAM_PEDAL_LONG);
  gm_pedal_acc = gm_pedal_long && GET_FLAG(param, GM_PARAM_BOLT_ACC_PEDAL) && !gm_no_acc;
  gm_bolt_2017 = (gm_hw == GM_CAM) && GET_FLAG(param, GM_PARAM_BOLT_2017);
  gm_pedal_sensor_good = false;
  gm_pedal_sensor_last_us = 0U;
  gm_pedal_counter_seen = false;
  gm_pedal_counter_last = 0U;
  gm_pedal_tx_counter_seen = false;
  gm_pedal_tx_counter_last = 0U;
  gm_pedal_brake_counter_seen = false;
  gm_pedal_brake_counter_last = 0U;
  gm_pedal_brake_producer_clear();
  gm_pedal_brake_producer_us = 0U;
  gm_paddle_sched = gm_pedal_long && GET_FLAG(param, GM_PARAM_PADDLE_SCHED);
  gm_bolt_gen2 = gm_pedal_long && GET_FLAG(param, GM_PARAM_BOLT_GEN2);
  gm_ascm_intercept = (gm_hw == GM_CAM) && !gm_pedal_long && !gm_no_acc && GET_FLAG(param, GM_PARAM_ASCM_INTERCEPT);
  gm_ascm_brake_c9 = gm_ascm_intercept && GET_FLAG(param, GM_PARAM_ASCM_BRAKE_C9);
  gm_sdgm = (gm_hw == GM_CAM) && GET_FLAG(param, GM_PARAM_SDGM);
  bool unsupported_sdgm_long = GET_FLAG(param, GM_PARAM_HW_CAM_LONG);
#ifdef ALLOW_DEBUG
  unsupported_sdgm_long &= !(gm_volt_sdgm_long || gm_ordinary_sdgm_long);
#endif
  gm_sdgm_invalid = GET_FLAG(param, GM_PARAM_SDGM) &&
                    (!GET_FLAG(param, GM_PARAM_HW_CAM) || unsupported_sdgm_long ||
                     GET_FLAG(param, GM_PARAM_ASCM_INTERCEPT) || GET_FLAG(param, GM_PARAM_PEDAL_LONG) ||
                     GET_FLAG(param, GM_PARAM_NO_ACC) || GET_FLAG(param, GM_PARAM_BOLT_ACC_PEDAL) ||
                     GET_FLAG(param, GM_PARAM_PADDLE_SCHED) || GET_FLAG(param, GM_PARAM_BOLT_GEN2) ||
                     GET_FLAG(param, GM_PARAM_BOLT_2017) || GET_FLAG(param, GM_PARAM_ASCM_RADAR));
  gm_sdgm_brake_c9 = gm_sdgm && GET_FLAG(param, GM_PARAM_ASCM_BRAKE_C9);
  const bool gm_sdgm_cancel_pt = gm_sdgm && GET_FLAG(param, GM_PARAM_SDGM_CANCEL_PT);
  gm_sdgm_invalid |= GET_FLAG(param, GM_PARAM_SDGM_CANCEL_PT) && !gm_sdgm;
  gm_acc_status_seen = false;
  gm_acc_status_last_us = 0U;
  gm_pedal_main_seen = false;
  gm_pedal_main_last_us = 0U;
  gm_regen_gear_ready = false;
  gm_bolt_pedal_drive_supported = safety_param == 0x19DU;
  gm_pedal_forward_gear_ready = false;
  gm_acc_pedal_forward_owner = param == 0x1CDU;
  gm_acc_pedal_dashboard_seen = false;
  gm_acc_dashboard_paddle_gear = false;
  gm_acc_dashboard_paddle_pressed = false;
  gm_acc_dashboard_paddle_us = 0U;
  gm_acc_pedal_dashboard_us = 0U;
  gm_regen_gear_last_us = 0U;
  gm_paddle_internal_tx = false;
  gm_bd_feed.valid = false;
  gm_bd_feed.last_feed_us = 0U;
  gm_gear_feed.valid = false;
  gm_gear_feed.last_feed_us = 0U;
  if (gm_pedal_long || gm_no_acc) {
    gm_pcm_cruise = false;
  }
  if (gm_cc_gateway_stock) {
    gm_pcm_cruise = true;
  }

  safety_config ret;
  if (gm_hw == GM_CAM) {
    ret = BUILD_SAFETY_CFG(gm_rx_checks, GM_CAM_TX_MSGS);
#ifdef ALLOW_DEBUG
    const bool gm_cam_long = GET_FLAG(param, GM_PARAM_HW_CAM_LONG);
    const bool gm_bolt_euv_long = param == (GM_PARAM_EV | GM_PARAM_HW_CAM | GM_PARAM_HW_CAM_LONG);
    if (gm_bolt_euv_long || gm_ordinary_camera_long) {
      gm_long_limits = &GM_BOLT_EUV_LONG_LIMITS;
    }
    if (!gm_pedal_long && !gm_no_acc) {
      gm_pcm_cruise = !gm_cam_long;
    }
    if (gm_cam_long && !gm_pedal_long && !gm_no_acc) {
      ret = BUILD_SAFETY_CFG(gm_rx_checks, GM_CAM_LONG_TX_MSGS);
      if (gm_bolt_euv_long) {
        SET_TX_MSGS(GM_BOLT_EUV_LONG_TX_MSGS, ret);
      }
      if (gm_volt_camera_long || gm_ordinary_camera_long) {
        SET_TX_MSGS(GM_BOLT_EUV_LONG_TX_MSGS, ret);
      }
      if (gm_ascm_intercept && GET_FLAG(param, GM_PARAM_ASCM_RADAR) && !gm_volt_ascm_long && !gm_ordinary_ascm_long) {
        SET_TX_MSGS(GM_CAM_LONG_ASCM_RADAR_TX_MSGS, ret);
      }
    }
#endif
  } else {
    ret = BUILD_SAFETY_CFG(gm_rx_checks, GM_ASCM_TX_MSGS);
    if (gm_cc_gateway_stock) {
      SET_RX_CHECKS(gm_cc_gateway_stock_rx_checks, ret);
      if ((unsigned int)alternative_experience != GM_ALT_EXP_ALWAYS_ON_LATERAL) {
        ret.rx_checks_len -= 1;
      }
      SET_TX_MSGS(GM_CC_GATEWAY_STOCK_TX_MSGS, ret);
    }
  }

  const bool gm_ev = GET_FLAG(param, GM_PARAM_EV);
  if (gm_ev) {
    SET_RX_CHECKS(gm_ev_rx_checks, ret);
  }
  if (gm_no_acc) {
    SET_RX_CHECKS(gm_noacc_rx_checks, ret);
    SET_TX_MSGS(GM_BOLT_NOACC_TX_MSGS, ret);
  }
  if (gm_pedal_long) {
    for (uint8_t i = 0U; i < 10U; i++) { gm_extended_rx_checks[i] = (RxCheck){0}; }
    for (uint8_t i = 0U; i < 8U; i++) { gm_extended_rx_checks[i] = gm_pedal_rx_template[i]; }
    ret.rx_checks = gm_extended_rx_checks;
    ret.rx_checks_len = 8;
    if (gm_bolt_pedal_removed || gm_bolt_present_cc_cancel) {
      static const RxCheck removed_acc_cruise = {.msg = {{0x3D1U, 0U, 8U, 10U, .ignore_checksum = true,
        .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}};
      gm_extended_rx_checks[8] = removed_acc_cruise;
      ret.rx_checks_len = 9;
    }
    if (gm_pedal_acc) {
      SET_TX_MSGS(GM_BOLT_ACC_PEDAL_TX_MSGS, ret);
    } else if (gm_no_acc) {
      SET_TX_MSGS(GM_BOLT_PEDAL_TX_MSGS, ret);
    } else {
      // Other pedal profiles retain their existing transmit list.
    }
  }
  if (gm_bolt_present_cc_cancel) {
    static const CanMsg GM_BOLT_PRESENT_CC_PEDAL_TX_MSGS[] = {
      {0x180, 0, 4, .check_relay = true}, {0x200, 0, 6, .check_relay = false},
      {0xBD, 0, 7, .check_relay = false}, {0x1F5, 0, 8, .check_relay = false},
      {0x184, 2, 8, .check_relay = true}, {0x1E1, 0, 7, .check_relay = false},
      {0x3D1, 0, 8, .check_relay = false},
    };
    SET_TX_MSGS(GM_BOLT_PRESENT_CC_PEDAL_TX_MSGS, ret);
  }

  if (gm_bolt_pedal_removed) {
    static const CanMsg GM_BOLT_REMOVED_PEDAL_TX_MSGS[] = {
      {0x180, 0, 4, .check_relay = false}, {0x200, 0, 6, .check_relay = false},
      {0xBD, 0, 7, .check_relay = false}, {0x1F5, 0, 8, .check_relay = false},
      {0x184, 2, 8, .check_relay = false}, {0x1E1, 0, 7, .check_relay = false},
      {0x3D1, 0, 8, .check_relay = false},
      {0x409, 0, 7, .check_relay = false}, {0x40A, 0, 7, .check_relay = false},
    };
    static const CanMsg GM_BOLT_REMOVED_ACC_PEDAL_TX_MSGS[] = {
      {0x180, 0, 4, .check_relay = false}, {0x200, 0, 6, .check_relay = false},
      {0x315, 0, 5, .check_relay = false},
      {0xBD, 0, 7, .check_relay = false}, {0x1F5, 0, 8, .check_relay = false},
      {0x184, 2, 8, .check_relay = false}, {0x1E1, 2, 7, .check_relay = false},
      {0x409, 0, 7, .check_relay = false}, {0x40A, 0, 7, .check_relay = false},
    };
    if (gm_pedal_acc) { SET_TX_MSGS(GM_BOLT_REMOVED_ACC_PEDAL_TX_MSGS, ret); }
    else { SET_TX_MSGS(GM_BOLT_REMOVED_PEDAL_TX_MSGS, ret); }
  }
  if (gm_ascm_intercept) {
    if (gm_ascm_brake_c9) {
      if (gm_ev) { SET_RX_CHECKS(gm_ascm_c9_ev_rx_checks, ret); }
      else { SET_RX_CHECKS(gm_ascm_c9_rx_checks, ret); }
    } else {
      if (gm_ev) { SET_RX_CHECKS(gm_ascm_be_ev_rx_checks, ret); }
      else { SET_RX_CHECKS(gm_ascm_be_rx_checks, ret); }
    }
  }
  if (gm_sdgm) {
    const bool volt_sdgm_ev = gm_volt_sdgm_long || (param == (GM_PARAM_EV | GM_PARAM_HW_CAM | GM_PARAM_SDGM)) ||
                              (param == (GM_PARAM_EV | GM_PARAM_HW_CAM | GM_PARAM_SDGM | GM_PARAM_ASCM_BRAKE_C9));
    if (gm_sdgm_brake_c9) {
      if (volt_sdgm_ev) { SET_RX_CHECKS(gm_ascm_c9_ev_rx_checks, ret); }
      else { SET_RX_CHECKS(gm_ascm_c9_rx_checks, ret); }
    } else {
      if (volt_sdgm_ev) { SET_RX_CHECKS(gm_ascm_be_ev_rx_checks, ret); }
      else { SET_RX_CHECKS(gm_ascm_be_rx_checks, ret); }
    }
    if (gm_sdgm_cancel_pt) { SET_TX_MSGS(GM_SDGM_PT_CANCEL_TX_MSGS, ret); }
    else { SET_TX_MSGS(GM_SDGM_CAM_CANCEL_TX_MSGS, ret); }
#ifdef ALLOW_DEBUG
    if (gm_volt_sdgm_long || gm_ordinary_sdgm_long) {
      static const CanMsg GM_SDGM_LONG_TX_MSGS[] = {
        {0x180, 0, 4, .check_relay = true}, {0x2CB, 0, 8, .check_relay = true},
        {0x370, 0, 6, .check_relay = true}, {0x315, 2, 5, .check_relay = false},
        {0x184, 2, 8, .check_relay = false},
      };
      SET_TX_MSGS(GM_SDGM_LONG_TX_MSGS, ret);
    }
#endif
  }

  if (gm_volt_gateway_alt_brake) {
    SET_RX_CHECKS(gm_volt_alt_brake_rx_checks, ret);
    SET_TX_MSGS(GM_VOLT_GATEWAY_ALT_BRAKE_TX_MSGS, ret);
  }

  if (gm_volt_cc_long || gm_ordinary_cc_long) {
    if (gm_ordinary_cc_long) {
      gm_ordinary_cc_rx_checks[3].msg[0].addr = gm_malibu_cc_f1 ? 0xF1U : 0xBEU;
      gm_ordinary_cc_rx_checks[3].msg[0].frequency = gm_malibu_cc_f1 ? 100U : 80U;
      SET_RX_CHECKS(gm_ordinary_cc_rx_checks, ret);
    }
    else {
      gm_volt_cc_rx_checks[3].msg[0].addr = gm_volt_cc_f1 ? 0xF1U : 0xBEU;
      gm_volt_cc_rx_checks[3].msg[0].frequency = gm_volt_cc_f1 ? 100U : 80U;
      SET_RX_CHECKS(gm_volt_cc_rx_checks, ret);
    }
    SET_TX_MSGS(GM_CC_GATEWAY_STOCK_TX_MSGS, ret);
    if (gm_ordinary_cc_long && !gm_malibu_cc_stock) {
      static const CanMsg GM_ORDINARY_CC_TX_MSGS[] = {
        {0x180, 0, 4, .check_relay = true}, {0x1E1, 0, 7, .check_relay = false},
        {0x409, 0, 7, .check_relay = false}, {0x40A, 0, 7, .check_relay = false},
      };
      SET_TX_MSGS(GM_ORDINARY_CC_TX_MSGS, ret);
    }
  }

  if (gm_volt_camera_removed) {
    SET_RX_CHECKS(gm_volt_removed_rx_checks, ret);
    SET_TX_MSGS(GM_VOLT_REMOVED_STOCK_TX_MSGS, ret);
#ifdef ALLOW_DEBUG
    if (gm_volt_removed_long) {
      gm_long_limits = &GM_BOLT_EUV_LONG_LIMITS;
      SET_TX_MSGS(GM_VOLT_REMOVED_LONG_TX_MSGS, ret);
    }
#endif
  }

  if (gm_ordinary_camera_removed) {
    static const CanMsg GM_ORDINARY_CAMERA_REMOVED_STOCK_TX_MSGS[] = {
      {0x180, 0, 4, .check_relay = false}, {0x1E1, 2, 7, .check_relay = false},
      {0x184, 2, 8, .check_relay = false},
    };
    SET_RX_CHECKS(gm_extended_rx_checks, ret);
    ret.rx_checks_len -= 4;
    SET_TX_MSGS(GM_ORDINARY_CAMERA_REMOVED_STOCK_TX_MSGS, ret);
#ifdef ALLOW_DEBUG
    if (gm_ordinary_camera_removed_long) {
      static const CanMsg GM_ORDINARY_CAMERA_REMOVED_LONG_TX_MSGS[] = {
        {0x180, 0, 4, .check_relay = false}, {0x315, 0, 5, .check_relay = false},
        {0x2CB, 0, 8, .check_relay = false}, {0x370, 0, 6, .check_relay = false},
        {0x409, 0, 7, .check_relay = false}, {0x40A, 0, 7, .check_relay = false},
        {0x184, 2, 8, .check_relay = false},
      };
      gm_long_limits = &GM_BOLT_EUV_LONG_LIMITS;
      SET_TX_MSGS(GM_ORDINARY_CAMERA_REMOVED_LONG_TX_MSGS, ret);
    }
#endif
  }

  if (gm_cc_pedal) {
    static RxCheck gm_cc_pedal_rx_checks[] = {
      {.msg = {{0x184, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x34A, 0, 5, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x1E1, 0, 7, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0xF1, 0, 6, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x1C4, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0xC9, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x201, 0, 6, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x3D1, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x1F5, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    };
    static const CanMsg GM_CC_PEDAL_TX_MSGS[] = {
      {0x180, 0, 4, .check_relay = true}, {0x200, 0, 6, .check_relay = false},
      {0x1E1, 0, 7, .check_relay = false}, {0x184, 2, 8, .check_relay = true},
    };
    static const CanMsg GM_CC_PEDAL_REMOVED_TX_MSGS[] = {
      {0x180, 0, 4, .check_relay = false}, {0x200, 0, 6, .check_relay = false},
      {0x1E1, 0, 7, .check_relay = false}, {0x184, 2, 8, .check_relay = false},
      {0x409, 0, 7, .check_relay = false}, {0x40A, 0, 7, .check_relay = false},
    };
    gm_pcm_cruise = false;
    SET_RX_CHECKS(gm_cc_pedal_rx_checks, ret);
    if (gm_cc_pedal_removed) {
      SET_TX_MSGS(GM_CC_PEDAL_REMOVED_TX_MSGS, ret);
    } else {
      SET_TX_MSGS(GM_CC_PEDAL_TX_MSGS, ret);
    }
  }

  if (gm_cc_pedal_volt) {
    static const RxCheck VOLT_CC_RX_TEMPLATE[10] = {
      {.msg = {{0x184, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x34A, 0, 5, 20U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x1E1, 0, 7, 33U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0xBE, 0, 6, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x1C4, 0, 8, 33U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0xC9, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x201, 0, 6, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x3D1, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x1F5, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0xBD, 0, 7, 40U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    };
    for (uint8_t i = 0U; i < 10U; i++) { gm_extended_rx_checks[i] = VOLT_CC_RX_TEMPLATE[i]; }
    gm_extended_rx_checks[3].msg[0].addr = gm_cc_pedal_volt_f1 ? 0xF1U : 0xBEU;
    if (!gm_cc_pedal_volt_f1) {
      gm_extended_rx_checks[3].msg[1] = gm_extended_rx_checks[3].msg[0];
      gm_extended_rx_checks[3].msg[1].len = 7U;
      gm_extended_rx_checks[3].msg[2] = gm_extended_rx_checks[3].msg[0];
      gm_extended_rx_checks[3].msg[2].len = 8U;
    }
    if (gm_cc_pedal_volt_stock) {
      gm_extended_rx_checks[6] = VOLT_CC_RX_TEMPLATE[9];
      gm_extended_rx_checks[8] = (RxCheck){0}; gm_extended_rx_checks[9] = (RxCheck){0};
      ret.rx_checks = gm_extended_rx_checks; ret.rx_checks_len = 8;
    } else { ret.rx_checks = gm_extended_rx_checks; ret.rx_checks_len = 10; }
  }

  if (gm_cc_pedal_ordinary_stock || gm_cc_pedal_volt_stock) {
    // DisableLong retains literal CAM cancellation without pedal or PT button authority.
    static const CanMsg GM_CC_PEDAL_STOCK_TX_MSGS[] = {
      {0x180, 0, 4, .check_relay = true}, {0x1E1, 2, 7, .check_relay = false},
      {0x184, 2, 8, .check_relay = true},
    };
    static const CanMsg GM_CC_PEDAL_STOCK_REMOVED_TX_MSGS[] = {
      {0x180, 0, 4, .check_relay = false}, {0x1E1, 2, 7, .check_relay = false},
      {0x184, 2, 8, .check_relay = false},
    };
    if (gm_cc_pedal_removed) {
      SET_TX_MSGS(GM_CC_PEDAL_STOCK_REMOVED_TX_MSGS, ret);
    } else {
      SET_TX_MSGS(GM_CC_PEDAL_STOCK_TX_MSGS, ret);
    }
  }

  if (gm_cc_pedal_silverado) {
    // C9 owns the Silverado digital brake. BE/F1 is host analog display data,
    // not an alternative native brake or engagement source.
    static RxCheck gm_silverado_pedal_rx_checks[] = {
      {.msg = {{0x184, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x34A, 0, 5, 20U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x1E1, 0, 7, 33U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x1C4, 0, 8, 33U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0xC9, 0, 8, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x201, 0, 6, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x3D1, 0, 8, 10U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
      {.msg = {{0x1F5, 0, 8, 40U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    };
    static const CanMsg GM_SILVERADO_PEDAL_STOCK_TX_MSGS[] = {
      {0x180, 0, 4, .check_relay = true}, {0x1E1, 2, 7, .check_relay = false},
      {0x184, 2, 8, .check_relay = true},
    };
    static const CanMsg GM_SILVERADO_PEDAL_STOCK_REMOVED_TX_MSGS[] = {
      {0x180, 0, 4, .check_relay = false}, {0x1E1, 2, 7, .check_relay = false},
      {0x184, 2, 8, .check_relay = false},
    };
    SET_RX_CHECKS(gm_silverado_pedal_rx_checks, ret);
    if (gm_cc_pedal_stock_only) {
      if (gm_cc_pedal_removed) {
        SET_TX_MSGS(GM_SILVERADO_PEDAL_STOCK_REMOVED_TX_MSGS, ret);
      } else {
        SET_TX_MSGS(GM_SILVERADO_PEDAL_STOCK_TX_MSGS, ret);
      }
    }
  }

  bool gm_ordinary_camera = gm_ordinary_camera_stock;
#ifdef ALLOW_DEBUG
  gm_ordinary_camera |= gm_ordinary_camera_long;
#endif
  if (gm_ordinary_camera) {
    SET_RX_CHECKS(gm_extended_rx_checks, ret);
    ret.rx_checks_len -= 4;
  }

  if (gm_auto_hold) {
    for (uint8_t i = 0U; i < 9U; i++) { gm_extended_rx_checks[i] = gm_hold_extended_rx_template[i]; }
    for (uint8_t i = 0U; i < 9U; i++) { gm_extended_rx_checks[i].msg[0].frequency = 10U; }
    gm_extended_rx_checks[5].msg[0].frequency = 40U;
    gm_extended_rx_checks[8].msg[0].addr = gm_hold_alt_brake ? 0xF1U : 0xBEU;
    gm_extended_rx_checks[8].msg[1] = (CanMsgCheck){0};
    gm_extended_rx_checks[8].msg[2] = (CanMsgCheck){0};
    if (gm_hold_extended_be) {
      gm_extended_rx_checks[8].msg[1] = gm_extended_rx_checks[8].msg[0];
      gm_extended_rx_checks[8].msg[1].len = 7U;
      gm_extended_rx_checks[8].msg[2] = gm_extended_rx_checks[8].msg[0];
      gm_extended_rx_checks[8].msg[2].len = 8U;
    }
#ifdef ALLOW_DEBUG
    if (hold_removed) {
      gm_extended_rx_checks[1].msg[0].frequency = 20U;
      gm_extended_rx_checks[2].msg[0].frequency = 33U;
      gm_extended_rx_checks[3].msg[0].frequency = 33U;
      gm_extended_rx_checks[4].msg[0].frequency = 100U;
      gm_extended_rx_checks[5].msg[0].frequency = 50U;
      gm_extended_rx_checks[8].msg[0].frequency = gm_hold_alt_brake ? 100U : 80U;
    }
#endif
    if (hold_ice) {
      RxCheck regen = {0};
      regen = gm_extended_rx_checks[5];
      gm_extended_rx_checks[5] = gm_extended_rx_checks[8];
      gm_extended_rx_checks[8] = regen;
    }
    gm_extended_rx_checks[9] = (RxCheck){0};
    SET_RX_CHECKS(gm_extended_rx_checks, ret);
    ret.rx_checks_len -= 1;
    if (hold_ice) { ret.rx_checks_len -= 1; }
#ifdef ALLOW_DEBUG
    // C9-only ASCM/SDGM has no analog requirement; removed C9 requires both sources.
    if ((volt_hold_ascm || volt_hold_sdgm) && gm_hold_c9_brake) { ret.rx_checks_len -= 1; }
#endif
  }

  if (gm_camera_volt && !gm_camera_pedal_long) {
    for (uint8_t i = 0U; i < 10U; i++) { gm_extended_rx_checks[i] = (RxCheck){0}; }
    for (uint8_t i = 0U; i < 6U; i++) { gm_extended_rx_checks[i] = gm_ordinary_camera_rx_template[i]; }
    gm_extended_rx_checks[3].msg[0].addr = gm_camera_pedal_f1 ? 0xF1U : 0xBEU;
    if ((gm_camera_ascm || gm_camera_sdgm) && !gm_camera_pedal_f1) {
      gm_extended_rx_checks[3].msg[1] = gm_extended_rx_checks[3].msg[0];
      gm_extended_rx_checks[3].msg[1].len = 7U;
      gm_extended_rx_checks[3].msg[2] = gm_extended_rx_checks[3].msg[0];
      gm_extended_rx_checks[3].msg[2].len = 8U;
    }
    gm_extended_rx_checks[6] = gm_hold_extended_rx_template[5];
    SET_RX_CHECKS(gm_extended_rx_checks, ret);
    ret.rx_checks_len -= 3;
  }

#ifdef ALLOW_DEBUG
  if (gm_camera_pedal_long) {
    for (uint8_t i = 0U; i < 10U; i++) { gm_extended_rx_checks[i] = (RxCheck){0}; }
    for (uint8_t i = 0U; i < 8U; i++) { gm_extended_rx_checks[i] = gm_camera_extended_rx_template[i]; }
    gm_extended_rx_checks[3].msg[0].addr = gm_camera_pedal_f1 ? 0xF1U : 0xBEU;
    if (gm_camera_gateway || gm_camera_ascm || gm_camera_sdgm) { gm_extended_rx_checks[7].msg[0].frequency = 10U; }
    if ((gm_camera_ascm || gm_camera_sdgm) && !gm_camera_pedal_f1) {
      gm_extended_rx_checks[3].msg[1] = gm_extended_rx_checks[3].msg[0];
      gm_extended_rx_checks[3].msg[1].len = 7U;
      gm_extended_rx_checks[3].msg[2] = gm_extended_rx_checks[3].msg[0];
      gm_extended_rx_checks[3].msg[2].len = 8U;
    }
    SET_RX_CHECKS(gm_extended_rx_checks, ret);
    ret.rx_checks_len -= 2;
    if (gm_camera_volt) {
      gm_extended_rx_checks[8] = gm_hold_extended_rx_template[5];
      ret.rx_checks_len += 1;
      if (gm_auto_hold) {
        gm_extended_rx_checks[9] = gm_hold_extended_rx_template[7];
        ret.rx_checks_len += 1;
      }
    }
    static const CanMsg GM_CAMERA_PEDAL_PRESENT_TX[] = {
      {0x180, 0, 4, .check_relay = true}, {0x315, 0, 5, .check_relay = true},
      {0x2CB, 0, 8, .check_relay = true}, {0x370, 0, 6, .check_relay = true},
      {0x2CD, 0, 5, .check_relay = true}, {0x184, 2, 8, .check_relay = true},
      {0x200, 0, 6, .check_relay = false},
    };
    static const CanMsg GM_CAMERA_PEDAL_REMOVED_TX[] = {
      {0x180, 0, 4, .check_relay = false}, {0x315, 0, 5, .check_relay = false},
      {0x2CB, 0, 8, .check_relay = false}, {0x370, 0, 6, .check_relay = false},
      {0x409, 0, 7, .check_relay = false}, {0x40A, 0, 7, .check_relay = false},
      {0x184, 2, 8, .check_relay = false}, {0x200, 0, 6, .check_relay = false},
    };
    static const CanMsg GM_SDGM_PEDAL_TX[] = {
      {0x180, 0, 4, .check_relay = true}, {0x315, 2, 5, .check_relay = false},
      {0x2CB, 0, 8, .check_relay = true}, {0x370, 0, 6, .check_relay = true},
      {0x2CD, 0, 5, .check_relay = true}, {0x184, 2, 8, .check_relay = false},
      {0x200, 0, 6, .check_relay = false},
    };
    if (gm_camera_sdgm) { SET_TX_MSGS(GM_SDGM_PEDAL_TX, ret); }
    else if (gm_ordinary_camera_removed || gm_volt_camera_removed || gm_camera_gateway_removed) { SET_TX_MSGS(GM_CAMERA_PEDAL_REMOVED_TX, ret); }
    else { SET_TX_MSGS(GM_CAMERA_PEDAL_PRESENT_TX, ret); }
  }
#endif
  gm_volt_invalid |= gm_camera_pedal_rejected || gm_cc_pedal_volt_rejected;

  // Independent lateral authority also needs the physical main source on BE-selected rows.
  const bool gm_aol_be_main = ((unsigned int)alternative_experience == GM_ALT_EXP_ALWAYS_ON_LATERAL) &&
    gm_aol_profile_word(param) && !gm_camera_pedal && !gm_volt_invalid && !gm_sdgm_invalid && !gm_cc_gateway_invalid &&
    ((gm_ascm_intercept && !gm_ascm_brake_c9) || (gm_sdgm && !gm_sdgm_brake_c9));
  if (gm_aol_be_main && !gm_auto_hold) {
    if (gm_ev) {
      SET_RX_CHECKS(gm_aol_be_ev_rx_checks, ret);
    } else {
      SET_RX_CHECKS(gm_aol_be_rx_checks, ret);
    }
  }

  if (gm_camera_gateway && !gm_camera_pedal_long) {
    static const CanMsg GM_GATEWAY_PEDAL_STOCK_TX[] = {
      {0x180, 0, 4, .check_relay = true}, {0x370, 0, 6, .check_relay = true},
      {0x409, 0, 7, .check_relay = false}, {0x40A, 0, 7, .check_relay = false},
      {0x184, 2, 8, .check_relay = true}, {0x1E1, 2, 7, .check_relay = false},
    };
    SET_TX_MSGS(GM_GATEWAY_PEDAL_STOCK_TX, ret);
  }

  if (gm_camera_ascm && !gm_camera_pedal_long) {
    static const CanMsg GM_ASCM_PEDAL_STOCK_TX[] = {
      {0x180, 0, 4, .check_relay = true}, {0x370, 0, 6, .check_relay = true},
      {0x184, 2, 8, .check_relay = true}, {0x1E1, 2, 7, .check_relay = false},
    };
    SET_TX_MSGS(GM_ASCM_PEDAL_STOCK_TX, ret);
    gm_pcm_cruise = false;
  }

  if (gm_camera_sdgm && !gm_camera_pedal_long) {
    static const CanMsg GM_SDGM_PEDAL_STOCK_TX[] = {
      {0x180, 0, 4, .check_relay = true}, {0x370, 0, 6, .check_relay = true},
      {0x184, 2, 8, .check_relay = false}, {0x1E1, 2, 7, .check_relay = false},
    };
    SET_TX_MSGS(GM_SDGM_PEDAL_STOCK_TX, ret);
    gm_pcm_cruise = false;
  }

  // A removed Volt camera leaves bus 2 unacknowledged after relay interception.
  // Forwarding vehicle traffic there fills TX queues and stalls host CAN reads.
  if (gm_volt_camera_removed) { ret.disable_forwarding = true; }

  // ASCM does not forward any messages
  if ((gm_hw == GM_ASCM) && !gm_camera_gateway) {
    ret.disable_forwarding = true;
  }
  return ret;
}


static bool gm_aol_mode_valid(void) {
  return !gm_volt_invalid && !gm_sdgm_invalid && !gm_cc_gateway_invalid && !gm_cc_pedal_volt_rejected;
}

const safety_hooks gm_hooks = {
  .init = gm_bolt_cc_init,
  .rx = gm_bolt_cc_rx,
  .optional_rx = gm_bolt_cc_optional_rx,
  .tx = gm_bolt_cc_tx,
  .fwd = gm_bolt_cc_fwd,
};
