#pragma once
#include "opendbc/safety/declarations.h"
#include "opendbc/safety/modes/volvo_eucd_aol.h"

// EUCD (V60 2010-18). Bus 0 is the car, bus 2 the forward camera; bus 1 is a radar-only aux bus, not monitored.
#define VOLVO_EUCD_MAIN_BUS  0U
#define VOLVO_EUCD_PARTY_BUS 2U

#define VOLVO_EUCD_AccPedal      0x020U  // RX, gas pedal
#define VOLVO_EUCD_GAS_PRESS     100U    // AccPedal raw (0.1 %)
#define VOLVO_EUCD_GAS_RELEASE   90U
#define VOLVO_EUCD_FSM0          0x051U  // TX by OP, camera's relayed; ACC state
#define VOLVO_EUCD_EngineData    0x12AU  // RX, ECM ACC state
#define VOLVO_EUCD_VehicleSpeed1 0x148U  // RX, vehicle speed
#define VOLVO_EUCD_Brake_Info    0x20AU  // RX, driver brake pressed
#define VOLVO_EUCD_CCButtons     0x127U  // TX by OP, CC buttons
#define VOLVO_EUCD_PSCM1         0x246U  // TX by OP to camera, PSCM state
#define VOLVO_EUCD_WHEEL_REAR    0x220U  // TX by OP to camera, rear wheel speeds + brake request active
#define VOLVO_EUCD_BRAKE_REQ_ACK 0x299U  // TX by OP to camera, brake request ack
#define VOLVO_EUCD_BRAKE_HOLD    0x1FFU  // TX by OP to camera, brake hold state (masked in OP's no-target stop)
#define VOLVO_EUCD_ACC_HOLD      0x325U  // TX by OP to camera, ECM ACC hold state (shown as standby in OP's no-target stop)
#define VOLVO_EUCD_FSM1          0x260U  // TX by OP, ACC radar/distance message
#define VOLVO_EUCD_FSM2          0x262U  // TX by OP, LKA command
#define VOLVO_EUCD_FSM3          0x270U  // TX by OP, ACC accel request + status
#define VOLVO_EUCD_FSM4          0x31AU  // TX by OP, camera's relayed; brake request

// The camera's own FSM3/FSM4 just received: relayed unchanged while it drives (OP's long off), even past OP's limits
#define VOLVO_EUCD_CAM_FRAMES 4U
#define VOLVO_EUCD_CAM_FRAME_MAX_AGE_US 100000U
typedef struct {
  uint8_t data[VOLVO_EUCD_CAM_FRAMES][8];
  uint32_t ts[VOLVO_EUCD_CAM_FRAMES];
  bool valid[VOLVO_EUCD_CAM_FRAMES];
  uint8_t index;
} VolvoEucdCamFrames;
static VolvoEucdCamFrames volvo_eucd_cam_fsm3;
static VolvoEucdCamFrames volvo_eucd_cam_fsm4;

// OP's stop (camera shown standby, ECM engaged): camera FSM1/FSM3 kept off the ECM also on the gas, per ECM frame
#define VOLVO_EUCD_OP_OWNS_ECM_FRAMES 3
static int volvo_eucd_op_owns_ecm_fresh = 0;

static void volvo_eucd_cam_frames_clear(VolvoEucdCamFrames *frames) {
  frames->index = 0U;
  for (uint8_t i = 0U; i < VOLVO_EUCD_CAM_FRAMES; i++) {
    frames->valid[i] = false;
  }
}

static void volvo_eucd_cam_frames_store(VolvoEucdCamFrames *frames, const CANPacket_t *msg) {
  for (uint8_t byte = 0U; byte < 8U; byte++) {
    frames->data[frames->index][byte] = msg->data[byte];
  }
  frames->ts[frames->index] = microsecond_timer_get();
  frames->valid[frames->index] = true;
  frames->index = (frames->index + 1U) % VOLVO_EUCD_CAM_FRAMES;
}

static bool volvo_eucd_cam_frames_match(const VolvoEucdCamFrames *frames, const CANPacket_t *msg) {
  const uint32_t now = microsecond_timer_get();
  bool matches_cam = false;
  for (uint8_t i = 0U; i < VOLVO_EUCD_CAM_FRAMES; i++) {
    bool matches = frames->valid[i] && (safety_get_ts_elapsed(now, frames->ts[i]) <= VOLVO_EUCD_CAM_FRAME_MAX_AGE_US);
    for (uint8_t byte = 0U; byte < 8U; byte++) {
      matches &= msg->data[byte] == frames->data[i][byte];
    }
    matches_cam |= matches;
  }
  return matches_cam;
}

static void volvo_eucd_rx_hook(const CANPacket_t *msg) {
  if (msg->bus == VOLVO_EUCD_MAIN_BUS) {
    const unsigned int addr = msg->addr;
    if (addr == VOLVO_EUCD_VehicleSpeed1) {
      // Signal: VehicleSpeed
      unsigned int speed_raw = ((unsigned int)msg->data[6] << 8) | (unsigned int)msg->data[7];
      vehicle_moving = speed_raw > 36U;
      UPDATE_VEHICLE_SPEED(speed_raw * 0.01 / 3.6);
    }

    if (addr == VOLVO_EUCD_AccPedal) {
      // Signal: AccPedal
      unsigned int gas_raw = (((unsigned int)msg->data[2] & 0x03U) << 8) | (unsigned int)msg->data[3];
      // 10% to press, under 9% to release, or a held foot flickers it (route 00000007, 273.7 s)
      gas_pressed = gas_raw >= (gas_pressed ? VOLVO_EUCD_GAS_RELEASE : VOLVO_EUCD_GAS_PRESS);
    }

    if (addr == VOLVO_EUCD_CCButtons) {
      // Signal: LKA_State, the driver's LKA on/off (always on lateral)
      volvo_eucd_aol_lka_on = GET_BIT(msg, 18U);
    }

    if (addr == VOLVO_EUCD_Brake_Info) {
      // Signal: BrakePedal
      brake_pressed = (((unsigned int)msg->data[2] & 0x0CU) >> 2U) == 2U;
    }

    if (addr == VOLVO_EUCD_PSCM1) {
      // Signal: SteeringAngleServo (factor 0.0447, offset -1465 deg), stored as angle_deg * 100
      unsigned int angle_raw = ((unsigned int)msg->data[2] << 8) | (unsigned int)msg->data[3];
      int angle_meas_new = ROUND(((angle_raw * 0.0447f) - 1465.0f) * 100.0f);
      update_sample(&angle_meas, angle_meas_new);
    }

    if (addr == VOLVO_EUCD_EngineData) {
      // Signal: ACC_State_ECM (byte 4 bits 5:4: 1 off, 2 standby, 3 engaged)
      bool cruise_engaged = (((unsigned int)msg->data[4] >> 4) & 0x3U) == 0x3U;
      pcm_cruise_check(cruise_engaged);
      if (volvo_eucd_op_owns_ecm_fresh > 0) {
        volvo_eucd_op_owns_ecm_fresh--;
      }
    }
  } else if (msg->bus == VOLVO_EUCD_PARTY_BUS) {
    if (msg->addr == VOLVO_EUCD_FSM0) {
      // Signal: ACC_Available, the camera's ACC main on (always on lateral)
      acc_main_on = GET_BIT(msg, 17U);
    }
  } else {
    // no other bus is monitored
  }
}

// Frames outside the RX checks: the camera's FSM3/FSM4 as it sends them
static void volvo_eucd_optional_rx(const CANPacket_t *msg) {
  volvo_eucd_aol_observe(msg);
  if (msg->bus == VOLVO_EUCD_PARTY_BUS) {
    if (msg->addr == VOLVO_EUCD_FSM3) {
      volvo_eucd_cam_frames_store(&volvo_eucd_cam_fsm3, msg);
    }
    if (msg->addr == VOLVO_EUCD_FSM4) {
      volvo_eucd_cam_frames_store(&volvo_eucd_cam_fsm4, msg);
    }
  }
}

// EUCD sends LKAAngleReq=0 when not steering, so the angle must be exactly zero while disabled
static bool volvo_eucd_angle_cmd_checks(int desired_angle, const AngleSteeringLimits limits) {
  const bool allowed = lateral_controls_allowed();
  bool violation = false;

  if (allowed) {
    // Same rate limits as steer_angle_cmd_checks: speed fudged by 1 m/s, with 1 count of margin
    const float fudged_speed = (vehicle_speed.min / VEHICLE_SPEED_FACTOR) - 1.;
    int delta_angle_up = (safety_interpolate(limits.angle_rate_up_lookup, fudged_speed) * limits.angle_deg_to_can) + 1.;
    int delta_angle_down = (safety_interpolate(limits.angle_rate_down_lookup, fudged_speed) * limits.angle_deg_to_can) + 1.;

    int highest_desired_angle = desired_angle_last + ((desired_angle_last > 0) ? delta_angle_up : delta_angle_down);
    int lowest_desired_angle = desired_angle_last - ((desired_angle_last >= 0) ? delta_angle_down : delta_angle_up);

    violation |= safety_max_limit_check(desired_angle, highest_desired_angle, lowest_desired_angle);
    desired_angle_last = desired_angle;
  } else {
    violation |= desired_angle != 0;
  }

  if (violation || !allowed) {
    desired_angle_last = 0;
  }

  return violation;
}

static bool volvo_eucd_tx_hook(const CANPacket_t *msg) {
  static const AngleSteeringLimits VOLVO_EUCD_STEERING_LIMITS = {
    .max_angle = 9000,  // 90 deg
    .angle_deg_to_can = 100,
    .angle_rate_up_lookup = {
      {0., 5., 15.},
      {5., .8, .15}
    },
    .angle_rate_down_lookup = {
      {0., 5., 15.},
      {5., 3.5, .4}
    },
  };

  // FSM3 byte1 accel: raw = byte1 - 126, 0.04 m/s^2 per step (+2.0 to -3.52)
  static const LongitudinalLimits VOLVO_EUCD_LONG_LIMITS = {
    .max_accel = 50,
    .min_accel = -88,
    .inactive_accel = 0,
  };

  bool tx = true;
  bool violation = false;
  const unsigned int addr = msg->addr;

  // Only ACC engaged, the same condition the fwd hook blocks the camera's frames on
  const bool long_allowed = controls_allowed && !gas_pressed_prev;

  // CC buttons to the car; the camera bus gets every CEM frame relayed, which cannot engage the ECM
  if ((addr == VOLVO_EUCD_CCButtons) && (msg->bus == VOLVO_EUCD_MAIN_BUS)) {
    // No resume without controls allowed, no cancel without cruise engaged
    violation |= !cruise_engaged_prev && (GET_BIT(msg, 59U) || !(GET_BIT(msg, 43U)));  // Signals: ACCOnOffBtn, ACCOnOffBtnInv (cancel)
    violation |= !controls_allowed && (GET_BIT(msg, 61U) || !(GET_BIT(msg, 45U)));  // Signals: ACCResumeBtn, ACCResumeBtnInv (resume)
  }

  // FSM0 from OP may report ACC enabled only while the ECM is engaged, so OP can never engage ACC by itself.
  if (addr == VOLVO_EUCD_FSM0) {
    bool acc_enabled = (msg->data[2] & 0x04U) != 0U;
    violation |= acc_enabled && !cruise_engaged_prev;
  }

  // Safety check for Lane Keep Assist action.
  if (addr == VOLVO_EUCD_FSM2) {
    // Signal: LKAAngleReq (14 bits, 0.04 deg, offset -327.68), as angle_deg * 100
    unsigned int angle_raw = (((unsigned int)msg->data[3] & 0x3FU) << 8) | (unsigned int)msg->data[4];
    int desired_angle = ((int)angle_raw * 4) - 32768;

    // Gate on the angle, not LKASteerDirection, which pauses ~8 frames on a direction change
    if (volvo_eucd_angle_cmd_checks(desired_angle, VOLVO_EUCD_STEERING_LIMITS)) {
      violation = true;
    }
  }

  // EngineData to the camera showing ACC standby with the ECM engaged: OP's stop, it sends the ECM's FSM1/FSM3
  if ((addr == VOLVO_EUCD_EngineData) && (msg->bus == VOLVO_EUCD_PARTY_BUS)) {
    const bool shows_standby = (((unsigned int)msg->data[4] >> 4) & 0x3U) == 0x2U;
    if (shows_standby && cruise_engaged_prev && controls_allowed) {
      volvo_eucd_op_owns_ecm_fresh = VOLVO_EUCD_OP_OWNS_ECM_FRAMES;
    }
  }

  // Long: controls_allowed and range check; stock FSM3 still flows cam->main
  if (addr == VOLVO_EUCD_FSM3) {
    int raw_accel = (int)msg->data[1] - 126;
    // Neutral accel only without controls; with them the accel range, or the camera's own frame while it drives
    const bool cam_frame = long_allowed && volvo_eucd_cam_frames_match(&volvo_eucd_cam_fsm3, msg);
    bool accel_violation;
    if (controls_allowed) {
      const bool accel_valid = long_allowed && !safety_max_limit_check(raw_accel, VOLVO_EUCD_LONG_LIMITS.max_accel, VOLVO_EUCD_LONG_LIMITS.min_accel);
      accel_violation = !accel_valid && (raw_accel != VOLVO_EUCD_LONG_LIMITS.inactive_accel) && !cam_frame;
    } else {
      accel_violation = raw_accel != VOLVO_EUCD_LONG_LIMITS.inactive_accel;
    }
    if (accel_violation) {
      violation = true;
    }
  }

  // FSM4 brake request only with OP's long allowed, or the camera's own frame unchanged (route 807)
  if (addr == VOLVO_EUCD_FSM4) {
    const bool brake_request = (msg->data[5] & 0x40U) != 0U;
    violation |= brake_request && !long_allowed && !volvo_eucd_cam_frames_match(&volvo_eucd_cam_fsm4, msg);
  }

  if (violation) {
    tx = false;
  }

  return tx;
}

static bool volvo_eucd_fwd_hook(int bus_num, int addr) {
  bool block_msg = false;
  // Camera FSM1/FSM3 blocked while OP controls; on the gas they go to the ECM, except in OP's stop (OP sends no accel)
  const bool op_owns_ecm = volvo_eucd_op_owns_ecm_fresh > 0;
  if (((unsigned int)bus_num == VOLVO_EUCD_PARTY_BUS) && controls_allowed && (!gas_pressed || op_owns_ecm)) {
    if (((unsigned int)addr == VOLVO_EUCD_FSM1) || ((unsigned int)addr == VOLVO_EUCD_FSM3)) {
      block_msg = true;
    }
  }
  // FSM0 never forwarded main->cam (check_relay on the cam bus would trip on the camera's own FSM0)
  if (((unsigned int)bus_num == VOLVO_EUCD_MAIN_BUS) && ((unsigned int)addr == VOLVO_EUCD_FSM0)) {
    block_msg = true;
  }
  return block_msg;
}

static safety_config volvo_eucd_init(uint16_t param) {
  (void)param;
  volvo_eucd_cam_frames_clear(&volvo_eucd_cam_fsm3);
  volvo_eucd_cam_frames_clear(&volvo_eucd_cam_fsm4);
  volvo_eucd_op_owns_ecm_fresh = 0;
  volvo_eucd_aol_reset();
  volvo_eucd_aol_enabled = (unsigned int)alternative_experience == 32U;
  if (volvo_eucd_aol_enabled) {
    static const AolSafetyPolicy policy = {
      .reset = volvo_eucd_aol_reset,
      .request_mask = volvo_eucd_aol_request,
      .permission_mask = volvo_eucd_aol_permission,
      .rx_invalid = volvo_eucd_aol_reset,
    };
    aol_policy = &policy;
  }
  static const CanMsg VOLVO_EUCD_TX_MSGS[] = {
    {VOLVO_EUCD_CCButtons, VOLVO_EUCD_MAIN_BUS,  8, .check_relay = false},
    // relayed by OP, Set/Minus/Resume released while the camera is shown standby; check_relay so the CEM's frame does not pass
    {VOLVO_EUCD_CCButtons, VOLVO_EUCD_PARTY_BUS, 8, .check_relay = true},
    {VOLVO_EUCD_PSCM1,     VOLVO_EUCD_PARTY_BUS, 8, .check_relay = true},  // OP replaces stock steering servo state
    // car -> camera, relayed by OP
    {VOLVO_EUCD_WHEEL_REAR,    VOLVO_EUCD_PARTY_BUS, 8, .check_relay = true},
    {VOLVO_EUCD_BRAKE_REQ_ACK, VOLVO_EUCD_PARTY_BUS, 8, .check_relay = true},
    {VOLVO_EUCD_BRAKE_HOLD,    VOLVO_EUCD_PARTY_BUS, 8, .check_relay = true},
    {VOLVO_EUCD_ACC_HOLD,      VOLVO_EUCD_PARTY_BUS, 8, .check_relay = true},
    {VOLVO_EUCD_EngineData,    VOLVO_EUCD_PARTY_BUS, 8, .check_relay = true},  // camera sees ACC standby in OP's no-target stop
    {VOLVO_EUCD_FSM2,      VOLVO_EUCD_MAIN_BUS,  8, .check_relay = true},  // OP replaces stock LKA command
    // FSM1/FSM3: camera's pass through while OP does not control (relay delay faults the ECM)
    {VOLVO_EUCD_FSM1,      VOLVO_EUCD_MAIN_BUS,  8, .check_relay = false},
    {VOLVO_EUCD_FSM3,      VOLVO_EUCD_MAIN_BUS,  8, .check_relay = false},
    // camera -> car, relayed by OP
    {VOLVO_EUCD_FSM4,      VOLVO_EUCD_MAIN_BUS,  8, .check_relay = true},
    {VOLVO_EUCD_FSM0,      VOLVO_EUCD_MAIN_BUS,  8, .check_relay = true},
  };

  // TODO: add counters
  static RxCheck volvo_eucd_rx_checks[] = {
    {.msg = {{VOLVO_EUCD_AccPedal,      VOLVO_EUCD_MAIN_BUS,  8, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true, .frequency = 100U}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_EUCD_FSM0,          VOLVO_EUCD_PARTY_BUS, 8, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true, .frequency = 100U}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_EUCD_EngineData,    VOLVO_EUCD_MAIN_BUS,  8, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true, .frequency = 67U}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_EUCD_CCButtons,     VOLVO_EUCD_MAIN_BUS,  8, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true, .frequency = 66U}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_EUCD_VehicleSpeed1, VOLVO_EUCD_MAIN_BUS,  8, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true, .frequency = 50U}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_EUCD_Brake_Info,    VOLVO_EUCD_MAIN_BUS,  8, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true, .frequency = 50U}, { 0 }, { 0 }}},
    {.msg = {{VOLVO_EUCD_PSCM1,         VOLVO_EUCD_MAIN_BUS,  8, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true, .frequency = 50U}, { 0 }, { 0 }}},  // steering angle meas
  };

  return BUILD_SAFETY_CFG(volvo_eucd_rx_checks, VOLVO_EUCD_TX_MSGS);
}
