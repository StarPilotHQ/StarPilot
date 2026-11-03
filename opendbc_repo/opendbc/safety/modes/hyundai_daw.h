#pragma once

#include "opendbc/safety/modes/hyundai_common.h"

static uint16_t hyundai_daw_addr;
static unsigned int hyundai_daw_bus;
static unsigned int hyundai_daw_len;
static uint8_t hyundai_daw_data[16];
static uint8_t hyundai_daw_previous[16];
static bool hyundai_daw_previous_seen;
static uint32_t hyundai_daw_previous_ts;
static uint32_t hyundai_daw_rx_ts;
static uint32_t hyundai_daw_tx_ts;
static bool hyundai_daw_seen;
static bool hyundai_daw_counter_seen;
static bool hyundai_daw_sent;

static void hyundai_daw_init(bool canfd, bool enabled) {
  hyundai_daw_addr = enabled ? (canfd ? 0x11AU : 0x53EU) : 0U;
  hyundai_daw_bus = canfd ? 1U : 0U;
  hyundai_daw_len = canfd ? 16U : 6U;
  hyundai_daw_previous_seen = false;
  hyundai_daw_previous_ts = 0U;
  hyundai_daw_seen = false;
  hyundai_daw_counter_seen = false;
  hyundai_daw_sent = false;
  hyundai_daw_rx_ts = 0U;
  hyundai_daw_tx_ts = 0U;
}

static void hyundai_daw_rx(const CANPacket_t *msg) {
  const bool canfd = hyundai_daw_len == 16U;
  if ((hyundai_daw_addr != 0U) && (msg->addr == hyundai_daw_addr) && (msg->bus == (canfd ? hyundai_daw_bus : 2U))) {
    const uint32_t now = microsecond_timer_get();
    const bool fresh = hyundai_daw_counter_seen && (safety_get_ts_elapsed(now, hyundai_daw_rx_ts) <= 100000U);
    bool valid = GET_LEN(msg) == hyundai_daw_len;
    if (valid && canfd) {
      valid = GET_BYTES_LE(msg, 0, 2) == (hyundai_common_canfd_compute_checksum(msg) ^ 0x041DU);
      if (fresh) {
        valid &= (uint8_t)(msg->data[2] - hyundai_daw_data[2]) == 1U;
      }
    }
    hyundai_daw_previous_seen = canfd && valid && fresh && hyundai_daw_seen;
    if (hyundai_daw_previous_seen) {
      (void)memcpy(hyundai_daw_previous, hyundai_daw_data, hyundai_daw_len);
      hyundai_daw_previous_ts = hyundai_daw_rx_ts;
    }
    hyundai_daw_seen = valid;
    if (valid) {
      (void)memcpy(hyundai_daw_data, msg->data, hyundai_daw_len);
      hyundai_daw_rx_ts = now;
      hyundai_daw_counter_seen = true;
    }
  }
}

static uint8_t hyundai_daw_expected(const uint8_t *data, unsigned int i) {
  uint8_t expected = data[i];
  if ((hyundai_daw_len == 16U) && (i == 7U) && (((expected >> 3U) & 7U) == 1U)) {
    expected &= (uint8_t)~0x38U;
  } else if ((hyundai_daw_len == 6U) && (i == 5U) && ((expected & 7U) >= 1U) && ((expected & 7U) <= 3U)) {
    expected &= (uint8_t)~7U;
  } else {
    // Non-advisory payload bits are copied exactly from the current source.
  }
  return expected;
}

static bool hyundai_daw_tx(const CANPacket_t *msg) {
  const uint32_t now = microsecond_timer_get();
  const bool canfd = hyundai_daw_len == 16U;
  bool valid = hyundai_daw_seen && (hyundai_daw_addr != 0U) && (msg->addr == hyundai_daw_addr) &&
               (msg->bus == hyundai_daw_bus) && (GET_LEN(msg) == hyundai_daw_len) &&
               (safety_get_ts_elapsed(now, hyundai_daw_rx_ts) <= 100000U) &&
               (!hyundai_daw_sent || (safety_get_ts_elapsed(now, hyundai_daw_tx_ts) >= 5000U));
  if (valid) {
    const uint8_t *source = hyundai_daw_data;
    if (canfd && (msg->data[2] != hyundai_daw_data[2])) {
      valid = hyundai_daw_previous_seen && (msg->data[2] == hyundai_daw_previous[2]) &&
              (safety_get_ts_elapsed(now, hyundai_daw_previous_ts) <= 100000U);
      if (valid) {
        // A one-frame receive race cannot replace newer warning or status data.
        for (unsigned int i = 3U; i < hyundai_daw_len; i++) {
          valid &= hyundai_daw_expected(hyundai_daw_previous, i) == hyundai_daw_expected(hyundai_daw_data, i);
        }
        source = hyundai_daw_previous;
      }
    }
    for (unsigned int i = canfd ? 2U : 0U; i < hyundai_daw_len; i++) {
      valid &= msg->data[i] == hyundai_daw_expected(source, i);
    }
    if (canfd) {
      valid &= GET_BYTES_LE(msg, 0, 2) == (hyundai_common_canfd_compute_checksum(msg) ^ 0x041DU);
    }
  }
  if (valid) {
    hyundai_daw_sent = true;
    hyundai_daw_tx_ts = now;
  }
  return valid;
}

static safety_config hyundai_daw_configure(safety_config ret, bool canfd, bool enabled) {
  hyundai_daw_init(canfd, false);
  if (enabled && (ret.tx_msgs_len > 0) && (ret.tx_msgs_len < 64)) {
    static CanMsg daw_tx_msgs[64];
    for (int i = 0; i < ret.tx_msgs_len; i++) {
      daw_tx_msgs[i] = ret.tx_msgs[i];
    }
    const CanMsg daw = {canfd ? 0x11AU : 0x53EU, canfd ? 1U : 0U, canfd ? 16U : 6U, .check_relay = false};
    daw_tx_msgs[ret.tx_msgs_len] = daw;
    ret.tx_msgs_len++;
    ret.tx_msgs = daw_tx_msgs;
    hyundai_daw_init(canfd, true);
  }
  return ret;
}
