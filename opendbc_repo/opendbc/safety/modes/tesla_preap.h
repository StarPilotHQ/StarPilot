#pragma once
#include "opendbc/safety/declarations.h"
#include "tesla_preap_pedal.h"
#include "tesla_preap_radar.h"

static bool tesla_preap_admitted = false;
static bool tesla_preap_pedal_long_armed = false;
static bool tesla_preap_pedal_pull_seen = false;
static bool tesla_preap_doors_open = true;
static int tesla_preap_gear = 0;
static int tesla_preap_lever_previous = 2;
static uint32_t tesla_preap_engage_ts = 0U;
static bool tesla_preap_pending_physical = false;
static bool tesla_preap_engage_seen = false;
static bool tesla_preap_brake_valid = false;
static bool tesla_preap_eps_fault = true;
static bool tesla_preap_eps_temporary = false;
static bool tesla_preap_belt_seen = false;
static bool tesla_preap_belt_latched = false;
static bool tesla_preap_belt_valid = false;
static uint32_t tesla_preap_belt_ts = 0U;
static bool tesla_preap_axis = false;
static bool tesla_preap_requested = false;
static bool tesla_preap_stw_seen = false;
static bool tesla_preap_physical_counter_seen = false;
static bool tesla_preap_echo_pending = false;
static uint8_t tesla_preap_stw[8];
static uint8_t tesla_preap_echo[8];
static uint32_t tesla_preap_stw_ts = 0U;
static uint32_t tesla_preap_echo_ts = 0U;

static void tesla_preap_clear(void) {
  if (tesla_preap_pedal_configured) { pcm_cruise_check(false); }
  tesla_preap_pedal_long_armed = false;
  tesla_preap_pedal_pull_seen = false;
  tesla_preap_pedal_tx_seen = false;
  tesla_preap_pedal_tx_enabled = false;
  tesla_preap_engage_seen = false;
  tesla_preap_pending_physical = false;
  tesla_preap_lever_previous = 2;
  tesla_preap_echo_pending = false;
  tesla_preap_stw_seen = false;
  tesla_preap_physical_counter_seen = false;
}

static void tesla_preap_reset(void) {
  tesla_preap_clear();
  tesla_preap_pending_physical = false;
  tesla_preap_belt_seen = false;
  tesla_preap_belt_latched = false;
  tesla_preap_belt_valid = false;
  tesla_preap_requested = false;
  tesla_preap_stw_seen = false;
  tesla_preap_echo_pending = false;
}

static uint32_t tesla_preap_checksum(const CANPacket_t *msg) { return msg->data[7]; }
static uint32_t tesla_preap_compute_checksum(const CANPacket_t *msg) { return tesla_preap_crc(msg); }

static bool tesla_preap_sources_ready(bool require_belt) {
  bool ready = tesla_preap_admitted && !safety_rx_checks_invalid && tesla_preap_brake_valid &&
    !tesla_preap_eps_fault && tesla_preap_belt_seen && tesla_preap_belt_valid && (!require_belt || tesla_preap_belt_latched) &&
    (safety_get_ts_elapsed(microsecond_timer_get(), tesla_preap_belt_ts) <= 1000000U) && tesla_preap_stw_seen &&
    (safety_get_ts_elapsed(microsecond_timer_get(), tesla_preap_stw_ts) <= 300000U);
  const uint32_t now = microsecond_timer_get();
  for (int i = 0; i < current_safety_config.rx_checks_len; i++) {
    ready = ready && rx_status[i].msg_seen &&
      rx_status[i].valid_checksum &&
      rx_status[i].valid_quality_flag &&
      (rx_status[i].wrong_counters < MAX_WRONG_COUNTERS) &&
      (safety_get_ts_elapsed(now, rx_status[i].last_timestamp) <= 1000000U);
  }
  return ready;
}

static void tesla_preap_host_request(uint8_t mask) { tesla_preap_requested |= mask != 0U; }

static uint8_t tesla_preap_request_mask(void) {
  uint8_t request = 0U;
  const bool expired = tesla_preap_requested &&
    (safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) > AOL_HOST_REQUEST_TIMEOUT_US);
  if (expired || relay_malfunction || !aol_rx_healthy() || !tesla_preap_sources_ready(false) ||
      (tesla_preap_pedal_configured && !tesla_preap_pedal_feedback_ready(microsecond_timer_get()))) {
    tesla_preap_pending_physical = false;
    tesla_preap_clear();
  } else if (!heartbeat_engaged) {
    tesla_preap_engage_seen = false;
  } else {
    if (tesla_preap_pending_physical &&
      (safety_get_ts_elapsed(microsecond_timer_get(), tesla_preap_engage_ts) <= 300000U) &&
      ((aol_host_axis_mask & 1U) != 0U)) {
      tesla_preap_engage_seen = true;
      tesla_preap_pending_physical = false;
    }
    request = aol_host_axis_mask & (tesla_preap_pedal_configured ? 3U : 1U);
  }
  return request;
}

static uint8_t tesla_preap_permission(void) {
  const uint8_t request = tesla_preap_request_mask();
  const bool lateral_ready = tesla_preap_engage_seen && (tesla_preap_gear == 4) && !tesla_preap_doors_open &&
    !steering_disengage && !tesla_preap_eps_temporary;
  uint8_t permission = lateral_ready ? (request & 1U) : 0U;
  const bool ordinary_ready = tesla_preap_engage_seen && (tesla_preap_gear == 4) && !tesla_preap_doors_open &&
    !steering_disengage && !tesla_preap_eps_fault;
  if (tesla_preap_pedal_configured && tesla_preap_pedal_long_armed && tesla_preap_pedal_tx_seen && ordinary_ready && controls_allowed && !brake_pressed && !gas_pressed &&
      tesla_preap_sources_ready(true) && tesla_preap_pedal_feedback_ready(microsecond_timer_get())) {
    permission |= request & 2U;
  }
  return permission;
}

static void tesla_preap_optional_rx(const CANPacket_t *msg) {
  tesla_preap_radar_rx(msg);
  if ((msg->addr == 0x552U) && tesla_preap_pedal_configured && (msg->bus == tesla_preap_pedal_bus)) {
    bool ready = false;
    if (GET_LEN(msg) == 6U) {
      ready = tesla_preap_pedal_feedback(msg->data, msg->bus, microsecond_timer_get());
    } else {
      tesla_preap_pedal_feedback_healthy = false;
      tesla_preap_pedal_feedback_seen = false;
    }
    gas_pressed = !ready || (tesla_preap_pedal_feedback_di > 2.0F);
    if (!ready) {
      controls_allowed = false;
      tesla_preap_clear();
    } else if (gas_pressed) {
      // Preserve the existing engaged mode through gas override, but require a
      // real zero/disabled command before any resumed active demand.
      tesla_preap_pedal_tx_seen = false;
      tesla_preap_pedal_tx_enabled = false;
    } else {
      // Healthy feedback without driver demand preserves the accepted TX state.
    }
  }

  if (msg_matches(msg, 0x201U, 0U, 5U)) {
    tesla_preap_belt_seen = true;
    tesla_preap_belt_ts = microsecond_timer_get();
    const unsigned int buckle = (msg->data[0] >> 4U) & 3U;
    tesla_preap_belt_valid = buckle <= 1U;
    tesla_preap_belt_latched = buckle == 1U;
    if (!tesla_preap_belt_latched) {
      controls_allowed = false;
      if (tesla_preap_pedal_configured) { pcm_cruise_check(false); }
      tesla_preap_pedal_long_armed = false;
      tesla_preap_pedal_pull_seen = false;
      tesla_preap_pedal_tx_seen = false;
      tesla_preap_pedal_tx_enabled = false;
      if (!tesla_preap_axis || !tesla_preap_belt_valid) { tesla_preap_clear(); }
    }
  }
}

static void tesla_preap_rx_hook(const CANPacket_t *msg) {
  if (msg->addr == 0x552U) { tesla_preap_optional_rx(msg); }
  if (msg->bus == 0U) {
    if (msg->addr == 0x370U) {
      const unsigned int angle_raw = ((msg->data[4] & 0x3FU) << 8U) | msg->data[5];
      const int angle = (int)angle_raw - 8192;
      update_sample(&angle_meas, angle);
      const int hands = msg->data[4] >> 6U;
      const int status = msg->data[6] >> 5U;
      const int error = msg->data[2] >> 4U;
      tesla_preap_eps_temporary = status == 0;
      tesla_preap_eps_fault = (status >= 3) || (error == 15);
      steering_disengage = tesla_preap_eps_fault || (hands >= 3) || ((status == 0) && (error >= 6) && (error <= 9));
    }
    if (msg->addr == 0x155U) {
      const float speed = ((msg->data[5] << 8U) | msg->data[6]) * 0.01F * KPH_TO_MS;
      UPDATE_VEHICLE_SPEED(speed);
      vehicle_moving = speed > (0.5F * KPH_TO_MS);
    }
    if ((msg->addr == 0x108U) && !tesla_preap_pedal_configured) { gas_pressed = msg->data[6] != 0U; }
    if (msg->addr == 0x20AU) {
      const unsigned int brake = (msg->data[0] >> 2U) & 3U;
      tesla_preap_brake_valid = (brake == 1U) || (brake == 2U);
      brake_pressed = brake != 1U;
      if (!tesla_preap_brake_valid) { tesla_preap_clear(); }
    }
    if (msg->addr == 0x118U) { tesla_preap_gear = (msg->data[1] >> 4U) & 7U; }
    if (msg->addr == 0x318U) {
      tesla_preap_doors_open = (((msg->data[1] >> 4U) & 3U) != 0U) ||
        (((msg->data[1] >> 6U) & 3U) != 0U) || (((msg->data[2] >> 6U) & 3U) != 0U) ||
        (((msg->data[3] >> 5U) & 3U) != 0U) || (((msg->data[6] >> 2U) & 3U) != 0U) ||
        (((msg->data[5] >> 6U) & 3U) != 0U);
    }
    if ((tesla_preap_gear != 4) || tesla_preap_doors_open || steering_disengage || brake_pressed) {
      controls_allowed = false;
      if (tesla_preap_pedal_configured) { pcm_cruise_check(false); }
      tesla_preap_pedal_long_armed = false;
      tesla_preap_pedal_pull_seen = false;
      tesla_preap_pedal_tx_seen = false;
      tesla_preap_pedal_tx_enabled = false;
      // Known Reverse/Neutral withdraw every axis but retain an established
      // physical arm. Permission still requires current Drive and source health.
      const bool gear_paused = (tesla_preap_gear == 2) || (tesla_preap_gear == 3);
      if (((tesla_preap_gear != 4) && !gear_paused) || tesla_preap_doors_open || steering_disengage) {
        tesla_preap_clear();
      }
    }
    if (msg->addr != 0x45U) { tesla_preap_radar_rx(msg); }
    if (msg->addr == 0x45U) {
      bool echo = tesla_preap_echo_pending &&
        (safety_get_ts_elapsed(microsecond_timer_get(), tesla_preap_echo_ts) <= 100000U);
      for (int i = 0; i < 8; i++) { echo = echo && (msg->data[i] == tesla_preap_echo[i]); }
      if (echo) {
        tesla_preap_echo_pending = false;
      } else if ((tesla_preap_crc(msg) != msg->data[7]) || ((msg->data[0] & 128U) != 0U)) {
        tesla_preap_clear();
      } else {
        static uint8_t tesla_preap_physical_counter = 0U;
        const uint8_t counter = msg->data[6] >> 4U;
        const bool established_counter = tesla_preap_physical_counter_seen;
        if (established_counter && (counter != ((tesla_preap_physical_counter + 1U) & 15U))) {
          tesla_preap_clear();
        } else {
          tesla_preap_physical_counter = counter;
          tesla_preap_physical_counter_seen = true;
          for (int i = 0; i < 8; i++) { tesla_preap_stw[i] = msg->data[i]; }
          tesla_preap_stw_seen = true;
          tesla_preap_stw_ts = microsecond_timer_get();
          const int lever = msg->data[0] & 0x3FU;
          if (established_counter && (lever == 2) && (tesla_preap_lever_previous == 0) && tesla_preap_sources_ready(!tesla_preap_axis) &&
              (tesla_preap_gear == 4) && !tesla_preap_doors_open && !steering_disengage && (!brake_pressed || tesla_preap_axis)) {
            if (tesla_preap_pedal_configured) {
              static uint32_t tesla_preap_pedal_pull_ts = 0U;
              const uint32_t now = microsecond_timer_get();
              tesla_preap_pedal_long_armed = tesla_preap_pedal_pull_seen && !brake_pressed &&
                tesla_preap_pedal_feedback_ready(now) &&
                (safety_get_ts_elapsed(now, tesla_preap_pedal_pull_ts) < 500000U);
              tesla_preap_pedal_pull_seen = true;
              tesla_preap_pedal_pull_ts = now;
            }
            if (tesla_preap_belt_latched) { pcm_cruise_check(true); }
            tesla_preap_engage_ts = microsecond_timer_get();
            tesla_preap_engage_seen = !tesla_preap_axis;
            tesla_preap_pending_physical = tesla_preap_axis;
          } else if (lever == 1) {
            pcm_cruise_check(false);
            tesla_preap_clear();
            tesla_preap_pending_physical = false;
          } else {
          }
          tesla_preap_lever_previous = lever;
          tesla_preap_radar_rx(msg);
        }
      }
    }
  }
}

static bool tesla_preap_tx_hook(const CANPacket_t *msg) {
  const AngleSteeringLimits limits = {.max_angle = 3600, .angle_deg_to_can = 10, .frequency = 50U};
  const AngleSteeringParams vm = {.slip_factor = -0.0005666F, .steer_ratio = 15.0F, .wheelbase = 2.96F};
  bool accepted = false;
  if (msg->addr == 0x551U) {
    const bool allowed = tesla_preap_admitted && tesla_preap_pedal_long_armed && tesla_preap_sources_ready(true) &&
      (tesla_preap_gear == 4) && !tesla_preap_doors_open && !brake_pressed && !steering_disengage &&
      !tesla_preap_eps_fault && get_longitudinal_allowed();
    accepted = tesla_preap_pedal_tx(msg->data, msg->bus, microsecond_timer_get(),
      (float)vehicle_speed.values[0] / VEHICLE_SPEED_FACTOR, allowed);
  } else if (msg->bus == 1U) {
    accepted = tesla_preap_radar_tx(msg);
  } else if (msg->addr == 0x214U) {
    const unsigned int sum = 0x14U + 2U + msg->data[0] + msg->data[1];
    accepted = tesla_preap_admitted && ((msg->data[0] & 7U) <= 1U) &&
      ((msg->data[0] & 248U) == 0U) && ((msg->data[1] & 240U) == 0U) &&
      ((sum & 255U) == msg->data[2]);
  } else if (msg->addr == 0x45U) {
    const int lever = msg->data[0] & 63U;
    accepted = tesla_preap_admitted && tesla_preap_stw_seen && tesla_preap_sources_ready(true) &&
      (safety_get_ts_elapsed(microsecond_timer_get(), tesla_preap_stw_ts) <= 300000U) &&
      ((lever == 1) || ((lever == 16) && tesla_preap_engage_seen && !brake_pressed &&
       (tesla_preap_gear == 4) && !tesla_preap_doors_open && !steering_disengage && !tesla_preap_eps_temporary)) &&
      ((msg->data[0] & 192U) == 64U) && (tesla_preap_crc(msg) == msg->data[7]) &&
      ((msg->data[6] >> 4U) == (((tesla_preap_stw[6] >> 4U) + 1U) & 15U));
    for (int i = 1; i < 6; i++) { accepted = accepted && (msg->data[i] == tesla_preap_stw[i]); }
    accepted = accepted && ((msg->data[6] & 15U) == (tesla_preap_stw[6] & 15U));
    if (accepted && !relay_malfunction) {
      for (int i = 0; i < 8; i++) { tesla_preap_echo[i] = msg->data[i]; }
      tesla_preap_echo_pending = true;
      tesla_preap_echo_ts = microsecond_timer_get();
    }
  } else {
    const unsigned int angle_raw = ((msg->data[0] & 0x7FU) << 8U) | msg->data[1];
    const int angle = (int)angle_raw - 16384;
    const int type = msg->data[2] >> 6U;
    unsigned int checksum = 0x88U + 4U;
    for (int i = 0; i < 3; i++) { checksum += msg->data[i]; }
    bool violation = !tesla_preap_admitted || ((type != 0) && (type != 1)) ||
      ((checksum & 0xFFU) != msg->data[3]);
    violation |= (type == 1) && (!tesla_preap_sources_ready(!tesla_preap_axis) || (tesla_preap_gear != 4) ||
      tesla_preap_doors_open || steering_disengage || (brake_pressed && !tesla_preap_axis));
    violation |= steer_angle_cmd_checks_vm(angle, type == 1, limits, vm);
    accepted = !violation;
  }
  return accepted;
}

static bool tesla_preap_fwd_hook(int bus, int addr) {
  (void)bus;
  (void)addr;
  return true;
}

static safety_config tesla_preap_init(uint16_t param) {
  const bool pedal = ((param == 1U) || (param == 3U) || (param == 7U)) && tesla_preap_pedal_profile_init(param);
  if ((param != 1U) && (param != 3U) && (param != 7U)) { tesla_preap_pedal_startup.staged_ready = false; }
  tesla_preap_admitted = (((param == 0U) || (param == 2U) || (param == 6U)) || pedal) &&
    ((alternative_experience == 0) || (alternative_experience == 32));
  tesla_preap_axis = alternative_experience == 32;
  tesla_preap_reset();
  tesla_preap_radar_enabled = tesla_preap_admitted && ((param & 2U) != 0U);
  tesla_preap_radar_nosecone = (param & 4U) != 0U;
  tesla_preap_radar_ticket = NULL;
  tesla_preap_doors_open = true;
  tesla_preap_brake_valid = false;
  tesla_preap_eps_fault = true;
  tesla_preap_gear = 0;
  tesla_preap_lever_previous = 2;
  tesla_preap_engage_ts = 0U;
  tesla_preap_engage_seen = false;
  static const CanMsg tx[] = {
    {0x488, 0, 4, .check_relay = false, .disable_static_blocking = true},
    {0x214, 0, 3, .check_relay = false, .disable_static_blocking = true},
    {0x45, 0, 8, .check_relay = false, .disable_static_blocking = true},
    {0x551, 0, 6, .check_relay = false, .disable_static_blocking = true},
    {0x551, 2, 6, .check_relay = false, .disable_static_blocking = true},
  };
  if (tesla_preap_admitted && tesla_preap_axis) {
    static const AolSafetyPolicy policy = {
      .reset = tesla_preap_reset, .host_request = tesla_preap_host_request,
      .request_mask = tesla_preap_request_mask, .permission_mask = tesla_preap_permission,
      .rx_invalid = tesla_preap_clear,
    };
    aol_policy = &policy;
  }
  static const CanMsg radar_tx[] = {
    {0x488, 0, 4, .check_relay = false, .disable_static_blocking = true},
    {0x214, 0, 3, .check_relay = false, .disable_static_blocking = true},
    {0x45, 0, 8, .check_relay = false, .disable_static_blocking = true},
    {0x551, 0, 6, .check_relay = false, .disable_static_blocking = true},
    {0x551, 2, 6, .check_relay = false, .disable_static_blocking = true},
    {0x219, 1, 8, .check_relay = false, .disable_static_blocking = true},
    {0x109, 1, 8, .check_relay = false, .disable_static_blocking = true},
    {0x149, 1, 8, .check_relay = false, .disable_static_blocking = true},
    {0x159, 1, 8, .check_relay = false, .disable_static_blocking = true},
    {0x209, 1, 8, .check_relay = false, .disable_static_blocking = true},
    {0x2d9, 1, 8, .check_relay = false, .disable_static_blocking = true},
    {0x2b9, 1, 8, .check_relay = false, .disable_static_blocking = true},
    {0x2a9, 1, 8, .check_relay = false, .disable_static_blocking = true},
    {0x199, 1, 8, .check_relay = false, .disable_static_blocking = true},
    {0x129, 1, 6, .check_relay = false, .disable_static_blocking = true},
    {0x1a9, 1, 5, .check_relay = false, .disable_static_blocking = true},
    {0x119, 1, 6, .check_relay = false, .disable_static_blocking = true},
    {0x169, 1, 8, .check_relay = false, .disable_static_blocking = true},
  };
  static RxCheck rx[] = {
    {.msg = {{0x370, 0, 8, 25U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, {0}, {0}}},
    {.msg = {{0x108, 0, 8, 100U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, {0}, {0}}},
    {.msg = {{0x118, 0, 6, 100U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, {0}, {0}}},
    {.msg = {{0x20a, 0, 8, 50U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, {0}, {0}}},
    {.msg = {{0x368, 0, 8, 10U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, {0}, {0}}},
    {.msg = {{0x318, 0, 8, 10U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, {0}, {0}}},
    {.msg = {{0x45, 0, 8, 10U, .ignore_quality_flag = true, .ignore_counter = true}, {0}, {0}}},
    {.msg = {{0x155, 0, 8, 50U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, {0}, {0}}},
    {.msg = {{0x552, 0, 6, 50U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, {0}, {0}}},
  };
  safety_config config = BUILD_SAFETY_CFG(rx, tx);
  if (pedal) {
    rx[config.rx_checks_len - 1].msg[0].bus = tesla_preap_pedal_bus;
    rx_status[config.rx_checks_len - 1] = (RxStatus){0};
  } else {
    config.rx_checks_len--;
  }
  if (tesla_preap_radar_enabled) { SET_TX_MSGS(radar_tx, config); }
  if (!tesla_preap_admitted) { config.tx_msgs_len = 0; }
  return config;
}

const safety_hooks tesla_preap_hooks = {
  .init = tesla_preap_init, .rx = tesla_preap_rx_hook, .optional_rx = tesla_preap_optional_rx, .tx = tesla_preap_tx_hook, .fwd = tesla_preap_fwd_hook,
  .get_checksum = tesla_preap_checksum, .compute_checksum = tesla_preap_compute_checksum,
};
