#pragma once

// V60 AOL: lateral with the LKA or ACC main on; host requests only narrow it
#define VOLVO_EUCD_GEAR_SELECTOR 0x063U
#define VOLVO_EUCD_AOL_GEAR_MAX_AGE_US 300000U

static bool volvo_eucd_aol_enabled = false;
static bool volvo_eucd_aol_lka_on = false;
static bool volvo_eucd_aol_drive = false;
static bool volvo_eucd_aol_gear_seen = false;
static uint32_t volvo_eucd_aol_gear_ts = 0U;

static void volvo_eucd_aol_reset(void) {
  volvo_eucd_aol_lka_on = false;
  volvo_eucd_aol_drive = false;
  volvo_eucd_aol_gear_seen = false;
  volvo_eucd_aol_gear_ts = 0U;
}

static uint8_t volvo_eucd_aol_request(void) {
  uint8_t request = 0U;
  if (heartbeat_engaged && aol_rx_healthy() && !safety_rx_checks_invalid && !relay_malfunction &&
      (safety_get_ts_elapsed(microsecond_timer_get(), aol_host_request_ts) <= AOL_HOST_REQUEST_TIMEOUT_US)) {
    request = aol_host_axis_mask;
  }
  return request;
}

static uint8_t volvo_eucd_aol_permission(void) {
  const uint8_t request = volvo_eucd_aol_request();
  uint8_t permission = 0U;
  const bool drive = volvo_eucd_aol_gear_seen && volvo_eucd_aol_drive &&
    (safety_get_ts_elapsed(microsecond_timer_get(), volvo_eucd_aol_gear_ts) <= VOLVO_EUCD_AOL_GEAR_MAX_AGE_US);
  if (drive && (acc_main_on || volvo_eucd_aol_lka_on)) {
    permission = request & 1U;
  }
  // No gas or brake check, as the tx hook: denying them dropped the AOL latch, a pink frame (routes 0000000a, 00000027)
  if (drive && controls_allowed) {
    permission |= request & 2U;
  }
  return permission;
}

static void volvo_eucd_aol_observe(const CANPacket_t *msg) {
  if (volvo_eucd_aol_enabled && msg_matches(msg, VOLVO_EUCD_GEAR_SELECTOR, 0U)) {
    volvo_eucd_aol_gear_seen = GET_LEN(msg) == 8U;
    if (volvo_eucd_aol_gear_seen) {
      volvo_eucd_aol_gear_ts = microsecond_timer_get();
      // Signal: GearLever (byte 6 bits 6:5: 0 P, 1 R, 2 N, 3 D)
      volvo_eucd_aol_drive = (((unsigned int)msg->data[6] >> 5) & 0x3U) == 0x3U;
    }
  }
}
