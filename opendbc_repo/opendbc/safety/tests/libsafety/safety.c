#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

// TODO: time should just be passed into the hooks we expose
uint32_t timer_cnt = 0;
uint32_t microsecond_timer_get(void);
uint32_t microsecond_timer_get(void) {
  return timer_cnt;
}

#include "opendbc/safety/can.h"
#include "opendbc/safety/safety.h"
#include "opendbc/safety/modes/tesla_preap_pedal_transport.h"
#include "opendbc/safety/ignition.h"

// Capture checked internally scheduled CAN frames for native safety tests.
#define RECORDED_CAN_CAPACITY 32
static CANPacket_t recorded_can[RECORDED_CAN_CAPACITY];
static unsigned int recorded_can_count = 0U;

void reset_recorded_can(void) {
  recorded_can_count = 0U;
}

unsigned int get_recorded_can_count(void) {
  return recorded_can_count;
}

bool get_recorded_can(unsigned int index, CANPacket_t *out) {
  if (index >= recorded_can_count || out == NULL) {
    return false;
  }
  *out = recorded_can[index];
  return true;
}

void can_set_checksum(CANPacket_t *packet) {
  // This test harness inspects the CAN payload and exercises the TX hook;
  // Panda packet framing checksums are added by the device transport.
  packet->checksum = 0U;
}

void can_send(CANPacket_t *to_push, uint8_t bus_number, bool skip_tx_hook) {
  CANPacket_t packet = *to_push;
  packet.bus = bus_number;
  if ((skip_tx_hook || safety_tx_hook(&packet)) && recorded_can_count < RECORDED_CAN_CAPACITY) {
    recorded_can[recorded_can_count++] = packet;
  }
}

bool safety_config_valid() {
  if (current_safety_config.rx_checks_len <= 0) {
    printf("missing RX checks\n");
    return false;
  }

  for (int i = 0; i < current_safety_config.rx_checks_len; i++) {
    const RxCheck addr = current_safety_config.rx_checks[i];
    bool valid = addr.status.msg_seen && !addr.status.lagging && addr.status.valid_checksum && (addr.status.wrong_counters < MAX_WRONG_COUNTERS) && addr.status.valid_quality_flag;
    if (!valid) {
      // printf("i %d seen %d lagging %d valid checksum %d wrong counters %d valid quality flag %d\n", i, addr.status.msg_seen, addr.status.lagging, addr.status.valid_checksum, addr.status.wrong_counters, addr.status.valid_quality_flag);
      return false;
    }
  }
  return true;
}

// Exercise defensive AOL RX guards with configs that production safety init
// cannot construct. Restore the active mode's config before returning.
bool safety_test_rx_health_fixture(unsigned int variant, bool canfd) {
  const uint32_t now = microsecond_timer_get();
  RxCheck check = {
    .msg = {{.addr = 0x123, .bus = 0U, .len = 8,
             .frequency = (variant == 2U) ? 9U : 100U,
             .ignore_checksum = false, .ignore_counter = false,
             .max_counter = 0U, .ignore_quality_flag = false}, {0}, {0}},
    .status = {.msg_seen = true, .index = 0, .valid_checksum = true,
               .valid_quality_flag = true, .last_timestamp = (variant == 3U) ? now - 100001U : now},
  };
  const safety_config original = current_safety_config;
  current_safety_config.rx_checks = (variant == 1U) ? NULL : &check;
  current_safety_config.rx_checks_len = (variant == 0U) ? 0 : 1;
  const bool healthy = canfd ? hyundai_canfd_ioniq6_rx_healthy() : aol_rx_healthy();
  current_safety_config = original;
  return healthy;
}

void set_controls_allowed(bool c){
  controls_allowed = c;
}

void set_heartbeat_engaged_mismatches(uint32_t count) {
  heartbeat_engaged_mismatches = count;
}

uint32_t get_heartbeat_engaged_mismatches(void) {
  return heartbeat_engaged_mismatches;
}

void set_aol_test_heartbeat(bool engaged) {
  heartbeat_engaged = engaged;
}

void set_alternative_experience(int mode){
  alternative_experience = mode;
}

void set_relay_malfunction(bool c){
  relay_malfunction = c;
}

bool get_controls_allowed(void){
  return controls_allowed;
}

bool get_ignition_can(void){
  return ignition_can;
}

bool get_relay_malfunction(void){
  return relay_malfunction;
}

bool get_gas_pressed_prev(void){
  return gas_pressed_prev;
}

void set_gas_pressed_prev(bool c){
  gas_pressed_prev = c;
}

bool get_brake_pressed_prev(void){
  return brake_pressed_prev;
}

bool get_regen_braking_prev(void){
  return regen_braking_prev;
}

bool get_steering_disengage_prev(void){
  return steering_disengage_prev;
}

bool get_cruise_engaged_prev(void){
  return cruise_engaged_prev;
}

void set_cruise_engaged_prev(bool engaged){
  cruise_engaged_prev = engaged;
}

bool get_vehicle_moving(void){
  return vehicle_moving;
}

bool get_acc_main_on(void){
  return acc_main_on;
}

float get_vehicle_speed_min(void){
  return vehicle_speed.min / VEHICLE_SPEED_FACTOR;
}

float get_vehicle_speed_max(void){
  return vehicle_speed.max / VEHICLE_SPEED_FACTOR;
}

int get_current_safety_mode(void){
  return current_safety_mode;
}

int get_current_safety_param(void){
  return current_safety_param;
}

void set_timer(uint32_t t){
  timer_cnt = t;
}

void set_torque_meas(int min, int max){
  torque_meas.min = min;
  torque_meas.max = max;
}

int get_torque_meas_min(void){
  return torque_meas.min;
}

int get_torque_meas_max(void){
  return torque_meas.max;
}

void set_torque_driver(int min, int max){
  torque_driver.min = min;
  torque_driver.max = max;
}

int get_torque_driver_min(void){
  return torque_driver.min;
}

int get_torque_driver_max(void){
  return torque_driver.max;
}

void set_rt_torque_last(int t){
  rt_torque_last = t;
}

void set_desired_torque_last(int t){
  desired_torque_last = t;
}

int get_desired_angle_last(void){
  return desired_angle_last;
}

void set_desired_angle_last(int t){
  desired_angle_last = t;
}

void set_angle_meas(int min, int max){
  angle_meas.min = min;
  angle_meas.max = max;
}

int get_angle_meas_min(void){
  return angle_meas.min;
}

int get_angle_meas_max(void){
  return angle_meas.max;
}

void set_desired_curvature_last(int t){
  curvature_state.desired_last = t;
}

int get_desired_curvature_last(void){
  return curvature_state.desired_last;
}

void set_curvature_meas(int min, int max){
  curvature_state.meas.min = min;
  curvature_state.meas.max = max;
}

int get_curvature_meas_min(void){
  return curvature_state.meas.min;
}

int get_curvature_meas_max(void){
  return curvature_state.meas.max;
}


// ***** car specific helpers *****

void set_honda_alt_brake_msg(bool c){
  honda_alt_brake_msg = c;
}

void set_honda_fwd_brake(bool c){
  honda_fwd_brake = c;
}

bool get_honda_fwd_brake(void){
  return honda_fwd_brake;
}

void init_tests(void){
  safety_mode_cnt = 2U;  // avoid ignoring relay_malfunction logic
  alternative_experience = 0;
  set_timer(0);
  ts_steer_req_mismatch_last = 0;
  valid_steer_req_count = 0;
  invalid_steer_req_count = 0;

  // assumes autopark on safety mode init to avoid a fault. get rid of that for testing
  tesla_autopark = false;

  ignition_can = false;
  ignition_can_cnt = 0U;
}

// Direct native callback contracts do not change registered public admission.
bool safety_test_selected_tx(const CANPacket_t *msg) {
  return (current_hooks != NULL) && (current_hooks->tx != NULL) && current_hooks->tx(msg);
}

bool safety_test_selected_fwd(int bus, int addr) {
  return (current_hooks != NULL) && (current_hooks->fwd != NULL) && current_hooks->fwd(bus, addr);
}

void safety_test_toyota_aol_observe(const CANPacket_t *msg) {
  toyota_aol_observe(msg);
}

bool safety_test_ford_aol_tx_bounds(int length, bool null_catalog) {
  if ((current_safety_mode != SAFETY_FORD) || (current_safety_config.tx_msgs_len < 1) ||
      (current_safety_config.tx_msgs_len > 7)) {
    return false;
  }
  const uint16_t mode = current_safety_mode;
  const uint16_t param = current_safety_param;
  const int original_len = current_safety_config.tx_msgs_len;
  CanMsg original_tx[7];
  memcpy(original_tx, current_safety_config.tx_msgs, (size_t)original_len * sizeof(CanMsg));
  CanMsg source[8] = {
    {.addr = 0x3D3, .bus = 0U, .len = 8}, {.addr = 0x3D8, .bus = 0U, .len = 8}, {.addr = 0x18A, .bus = 0U, .len = 8}, {.addr = 0x83, .bus = 0U, .len = 8},
    {.addr = 0x3D3, .bus = 0U, .len = 8}, {.addr = 0x3D8, .bus = 0U, .len = 8}, {.addr = 0x18A, .bus = 0U, .len = 8}, {.addr = 0x83, .bus = 0U, .len = 8},
  };
  CanMsg workspace[7];
  safety_config fixture = {.tx_msgs = null_catalog ? NULL : source, .tx_msgs_len = length};
  ford_aol_bind_tx(&fixture, workspace);
  const bool valid_length = !null_catalog && (length > 0) && (length <= 7);
  bool contract = false;
  if (valid_length) {
    contract = (fixture.tx_msgs == workspace) && (fixture.tx_msgs_len == length) && ford_aol_enabled;
    for (int i = 0; i < length; i++) {
      contract &= (workspace[i].addr == source[i].addr) && (workspace[i].bus == source[i].bus) &&
                  (workspace[i].len == source[i].len) &&
                  (workspace[i].disable_static_blocking == (source[i].addr != 0x83));
    }
  } else {
    contract = (fixture.tx_msgs == NULL) && (fixture.tx_msgs_len == 0) && !ford_aol_enabled;
  }
  // The fixture starts from a fresh registered configuration and restores it.
  const int restored = set_safety_hooks(mode, param);
  contract &= (restored == 0) && (current_safety_mode == mode) && (current_safety_param == param) &&
              (current_safety_config.tx_msgs_len == original_len) &&
              (memcmp(original_tx, current_safety_config.tx_msgs, (size_t)original_len * sizeof(CanMsg)) == 0);
  return contract;
}

bool safety_test_gm_ascm_adas(const CANPacket_t *msg) {
  const unsigned int length = GET_LEN(msg);
  const bool fixed_shape = (msg->bus == 1U) &&
    (((msg->addr == 0xA1U) && (length == 7U)) || ((msg->addr == 0x306U) && (length == 8U)) ||
     ((msg->addr == 0x308U) && (length == 7U)) || ((msg->addr == 0x310U) && (length == 2U)));
  return fixed_shape && gm_ascm_adas_valid(msg);
}

bool safety_test_gm_bolt_invalid_profile(void) {
  const uint16_t mode = current_safety_mode;
  const uint16_t param = current_safety_param;
  const safety_config fixture = gm_bolt_cc_profile_init(0U);
  const bool denied = (fixture.tx_msgs_len == 1) && (fixture.tx_msgs[0].addr == 0) &&
                      (fixture.tx_msgs[0].bus == 0U) && (fixture.tx_msgs[0].len == 0);
  return (set_safety_hooks(mode, param) == 0) && denied;
}

bool safety_test_gm_bolt_unowned_tx(void) {
  const CANPacket_t msg = {.addr = 0x200U, .bus = 0U, .data_len_code = 6U};
  return gm_bolt_cc_profile_tx(&msg);
}


bool safety_test_hyundai_angle_invalid_capacity(unsigned int count) {
  if ((count != 0U) && (count != 65U)) {
    return false;
  }
  safety_config fixture = {.rx_checks_len = 1, .tx_msgs_len = (int)count};
  const bool bounded = hyundai_canfd_angle_aol_capacity(&fixture);
  return !bounded && (fixture.rx_checks_len == 0) && (fixture.tx_msgs_len == 0);
}

// Toyota's actual required-RxStatus guard, exercised without a periodic safety tick.
bool safety_test_toyota_rx_summary_invalid(void) {
  return safety_rx_checks_invalid;
}

bool safety_test_toyota_invalid_counter_status(void) {
  if ((current_safety_mode != SAFETY_TOYOTA) || (current_safety_config.rx_checks_len <= 0)) {
    return false;
  }
  current_safety_config.rx_checks[0].status.wrong_counters = MAX_WRONG_COUNTERS;
  return !safety_rx_checks_invalid;
}

// Model retained/uninitialized H7 NOLOAD memory before a fresh Volvo mode init.
// Test-only helper: do not corrupt a workspace currently selected by safety.
bool safety_test_volvo_cma_poison_workspace(void) {
  if (current_safety_mode != SAFETY_NOOUTPUT) {
    return false;
  }
  memset(volvo_cma_rx_checks_workspace(), 0xA5, VOLVO_CMA_RX_CHECK_COUNT * sizeof(RxCheck));
  memset(volvo_cma_relay_data, 0xA5, sizeof(volvo_cma_relay_data));
  memset(volvo_cma_relay_ts, 0xA5, sizeof(volvo_cma_relay_ts));
  memset(volvo_cma_relay_valid, 0xA5, sizeof(volvo_cma_relay_valid));
  memset(volvo_cma_relay_index, 0xA5, sizeof(volvo_cma_relay_index));
  volvo_cma_lca2_primary_seen = true;
  volvo_cma_lca2_primary_last = 0xA5U;
  volvo_cma_lca2_primary_counter = 0xA5U;
  volvo_cma_lca2_second_seen = true;
  volvo_cma_lca2_second_last = 0xA5U;
  volvo_cma_lca2_second_wrong = 99;
  volvo_cma_lca2_phase_valid = true;
  volvo_cma_lca2_phase_one = 0xA5U;
  volvo_cma_lca2_phase_two = 0xA5U;
  volvo_cma_lca2_phase_offset = 0xA5U;
  volvo_cma_gear_seen = true;
  volvo_cma_drive_gear = true;
  volvo_cma_gear_ts = 0xA5A5A5A5U;
  return true;
}

// Called after selecting CMA/SPA and before any RX: every retained cache field
// must be initialized, even though invalid entries could not authorize TX.
bool safety_test_volvo_cma_cold_state_zeroed(void) {
  if ((current_safety_mode != SAFETY_VOLVO) || ((current_safety_param > 1U) && (current_safety_param != 5U))) {
    return false;
  }
  bool cleared = !volvo_cma_lca2_primary_seen && (volvo_cma_lca2_primary_last == 0U) &&
    (volvo_cma_lca2_primary_counter == 0U) && !volvo_cma_lca2_second_seen &&
    (volvo_cma_lca2_second_last == 0U) && (volvo_cma_lca2_second_wrong == 0) &&
    !volvo_cma_lca2_phase_valid && (volvo_cma_lca2_phase_one == 0U) &&
    (volvo_cma_lca2_phase_two == 0U) && (volvo_cma_lca2_phase_offset == 0U) &&
    !volvo_cma_gear_seen && !volvo_cma_drive_gear && (volvo_cma_gear_ts == 0U);
  for (uint8_t i = 0U; i < VOLVO_CMA_RELAY_COUNT; i++) {
    cleared &= volvo_cma_relay_index[i] == 0U;
    for (uint8_t j = 0U; j < 4U; j++) {
      cleared &= !volvo_cma_relay_valid[i][j] && (volvo_cma_relay_ts[i][j] == 0U);
      for (uint8_t b = 0U; b < 8U; b++) {
        cleared &= volvo_cma_relay_data[i][j][b] == 0U;
      }
    }
  }
  return cleared;
}

// Test transport adapter only; enforces the actual board startup mode guard.
bool tesla_preap_test_config_word(uint16_t index, uint16_t value) {
  const bool non_car = (current_safety_mode == SAFETY_SILENT) || (current_safety_mode == SAFETY_NOOUTPUT) ||
    (current_safety_mode == SAFETY_ELM327);
  return tesla_preap_pedal_config_word(index, value, non_car && !controls_allowed && !heartbeat_engaged);
}
unsigned int tesla_preap_test_config_read(uint8_t *out) {
  return tesla_preap_pedal_config_read(out);
}

// A real foreign catalog must fail closed instead of acquiring the GV60 heartbeat.
bool safety_test_hyundai_gv60_catalog_rejection(bool alternate) {
  bool rejected = false;
#ifdef ALLOW_DEBUG
  uint16_t base = 0x15U;
  uint16_t gv60 = 0x9015U;
  if (alternate) {
    base = 0x95U;
    gv60 = 0x9095U;
  }
  const safety_config expected = hyundai_canfd_init(base);
  const safety_config replacement = hyundai_canfd_init(gv60);
  const safety_config foreign = hyundai_canfd_init(0x1U);
  if ((expected.tx_msgs_len > 0) && (replacement.tx_msgs_len > expected.tx_msgs_len) &&
      (foreign.tx_msgs_len > 0) && (foreign.tx_msgs != expected.tx_msgs)) {
    const safety_config result = hyundai_canfd_gv60_extend_tx(foreign, expected.tx_msgs, expected.tx_msgs_len,
                                                            replacement.tx_msgs, replacement.tx_msgs_len);
    rejected = (result.rx_checks_len == 0) && (result.tx_msgs_len == 0) && !hyundai_canfd_gv60_long;
  }
#else
  (void)alternate;
#endif
  return rejected;
}

// BluePilot lateral hooks (modes/ford.h): the startup state ford_init gave the library, and the pinion RX
// append's guard. A selected RX list with no room left denies TX instead of dropping a check.
bool get_ford_bp_lateral(void) { return ford_bp_lateral; }
bool get_ford_bp_pinion_curvature(void) { return ford_bp_pinion_curvature; }
int get_ford_rx_checks_len(void) { return current_safety_config.rx_checks_len; }

bool test_ford_bp_pinion_rx_overflow(void) {
  RxCheck workspace[11] = {0};
  safety_config config = current_safety_config;
  config.rx_checks = workspace;
  config.rx_checks_len = 11;
  ford_bp_append_pinion_rx(&config, workspace);
  return (config.tx_msgs == NULL) && (config.tx_msgs_len == 0) && (config.rx_checks_len == 11) && !ford_aol_enabled;
}
