#pragma once
#include "opendbc/safety/modes/volvo_c1.h"
#include "opendbc/safety/modes/volvo_cma.h"

static bool volvo_dispatch_c1;
static safety_config volvo_dispatch_init(uint16_t param) {
  volvo_dispatch_c1 = param == 2U;
  return volvo_dispatch_c1 ? volvo_c1_init(param) : volvo_cma_init(param);
}
static void volvo_dispatch_rx(const CANPacket_t *msg) {
  if (volvo_dispatch_c1) {
    volvo_c1_rx_hook(msg);
  } else {
    volvo_cma_rx_hook(msg);
  }
}
static bool volvo_dispatch_tx(const CANPacket_t *msg) {
  return volvo_dispatch_c1 ? volvo_c1_tx_hook(msg) : volvo_cma_tx_hook(msg);
}
// C1 continues to ignore these hooks exactly as before; all of its RX
// declarations retain their existing ignore_checksum/quality settings.
static uint8_t volvo_dispatch_get_counter(const CANPacket_t *msg) {
  return volvo_dispatch_c1 ? 0U : volvo_cma_lca2_counter(msg);
}
static uint32_t volvo_dispatch_get_checksum(const CANPacket_t *msg) {
  return volvo_dispatch_c1 ? 0U : volvo_cma_get_checksum(msg);
}
static uint32_t volvo_dispatch_compute_checksum(const CANPacket_t *msg) {
  return volvo_dispatch_c1 ? 0U : volvo_cma_compute_checksum(msg);
}
static bool volvo_dispatch_quality(const CANPacket_t *msg) {
  return volvo_dispatch_c1 || volvo_cma_receiver_quality_rx(msg);
}
const safety_hooks volvo_dispatch_hooks = {.init = volvo_dispatch_init, .rx = volvo_dispatch_rx, .tx = volvo_dispatch_tx,
  .get_counter = volvo_dispatch_get_counter,
  .get_checksum = volvo_dispatch_get_checksum, .compute_checksum = volvo_dispatch_compute_checksum,
  .get_quality_flag_valid = volvo_dispatch_quality};
