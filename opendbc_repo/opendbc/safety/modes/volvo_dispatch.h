#pragma once

#include "opendbc/safety/modes/volvo_c1.h"
#include "opendbc/safety/modes/volvo_cma.h"
#include "opendbc/safety/modes/volvo_eucd.h"

// safetyParam: 0 = CMA/SPA (XC40 Recharge, S60 Recharge, Polestar 2), 2 = C1 (V40), 8 = EUCD (V60).
// safety.h admits only these three values for SAFETY_VOLVO.
#define VOLVO_FLAG_C1 2U
#define VOLVO_FLAG_EUCD 8U

static bool volvo_dispatch_c1;
static bool volvo_dispatch_eucd;

static safety_config volvo_dispatch_init(uint16_t param) {
  volvo_dispatch_c1 = param == VOLVO_FLAG_C1;
  volvo_dispatch_eucd = param == VOLVO_FLAG_EUCD;
  safety_config ret;
  if (volvo_dispatch_eucd) {
    ret = volvo_eucd_init(param);
  } else if (volvo_dispatch_c1) {
    ret = volvo_c1_init(param);
  } else {
    ret = volvo_cma_init(param);
  }
  return ret;
}

static void volvo_dispatch_rx(const CANPacket_t *msg) {
  if (volvo_dispatch_eucd) {
    volvo_eucd_rx_hook(msg);
  } else if (volvo_dispatch_c1) {
    volvo_c1_rx_hook(msg);
  } else {
    volvo_cma_rx_hook(msg);
  }
}

static bool volvo_dispatch_tx(const CANPacket_t *msg) {
  bool tx;
  if (volvo_dispatch_eucd) {
    tx = volvo_eucd_tx_hook(msg);
  } else if (volvo_dispatch_c1) {
    tx = volvo_c1_tx_hook(msg);
  } else {
    tx = volvo_cma_tx_hook(msg);
  }
  return tx;
}

static void volvo_dispatch_optional_rx(const CANPacket_t *msg) {
  if (volvo_dispatch_eucd) {
    volvo_eucd_optional_rx(msg);
  }
}

// C1 has no dynamic forwarding; CMA/SPA hands replaced frames back to stock, EUCD has its own relay rules.
static bool volvo_dispatch_fwd(int bus_num, int addr) {
  bool fwd;
  if (volvo_dispatch_eucd) {
    fwd = volvo_eucd_fwd_hook(bus_num, addr);
  } else if (volvo_dispatch_c1) {
    fwd = false;
  } else {
    fwd = volvo_cma_fwd_hook(bus_num, addr);
  }
  return fwd;
}

// C1 and EUCD declare no checksum or quality checks; all of their RX checks ignore them.
static uint32_t volvo_dispatch_get_checksum(const CANPacket_t *msg) {
  return (volvo_dispatch_c1 || volvo_dispatch_eucd) ? 0U : volvo_cma_get_checksum(msg);
}

static uint32_t volvo_dispatch_compute_checksum(const CANPacket_t *msg) {
  return (volvo_dispatch_c1 || volvo_dispatch_eucd) ? 0U : volvo_cma_compute_checksum(msg);
}

static bool volvo_dispatch_quality(const CANPacket_t *msg) {
  return volvo_dispatch_c1 || volvo_dispatch_eucd || volvo_cma_quality(msg);
}

const safety_hooks volvo_dispatch_hooks = {
  .init = volvo_dispatch_init,
  .rx = volvo_dispatch_rx,
  .tx = volvo_dispatch_tx,
  .optional_rx = volvo_dispatch_optional_rx,
  .fwd = volvo_dispatch_fwd,
  .get_checksum = volvo_dispatch_get_checksum,
  .compute_checksum = volvo_dispatch_compute_checksum,
  .get_quality_flag_valid = volvo_dispatch_quality,
};
