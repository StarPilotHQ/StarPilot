#pragma once
// Known checksum equations adapted from pinned Dom b220a37f4bd119e402225a7aa16a7cd84878d0af
// opendbc/car/volvo/helpers.py. Unknown receiver checksum domains remain unsupported.
static uint8_t volvo_cma_parity8(uint8_t x) {
  uint8_t folded = x;
  folded ^= folded >> 4U;
  folded ^= folded >> 2U;
  folded ^= folded >> 1U;
  return folded & 1U;
}

static uint8_t volvo_cma_crc_lca2_first(uint8_t b0, uint8_t b5) {
  // Retain the pinned encoder's existing special case exactly; no new CRC rule.
  static const uint8_t masks[8][2] = {
    {0x08U, 0x83U},
    {0x00U, 0x86U},
    {0x00U, 0xCFU},
    {0x00U, 0xCDU},
    {0x08U, 0x09U},
    {0x00U, 0x02U},
    {0x00U, 0x44U},
    {0x00U, 0x89U},
  };
  uint8_t checksum = 0U;
  if ((b0 != 0U) || (b5 != 128U)) {
    for (uint8_t bit = 0U; bit < 8U; bit++) {
      checksum |= (volvo_cma_parity8(b0 & masks[bit][0]) ^ volvo_cma_parity8(b5 & masks[bit][1])) << bit;
    }
  }
  return checksum;
}

static uint8_t volvo_cma_crc_lca2_second(uint8_t b0, uint8_t b1, uint8_t b3, uint8_t b4) {
  static const uint8_t masks[8][4] = {
    {0x01U, 0x3CU, 0x00U, 0x03U},
    {0x0AU, 0x69U, 0x02U, 0x07U},
    {0x09U, 0x7EU, 0x03U, 0x0DU},
    {0x03U, 0x51U, 0x03U, 0x19U},
    {0x0BU, 0x1EU, 0x00U, 0x30U},
    {0x02U, 0x2DU, 0x02U, 0x60U},
    {0x08U, 0x4BU, 0x01U, 0xC0U},
    {0x08U, 0x16U, 0x00U, 0x81U},
  };
  uint8_t checksum = 0U;
  for (uint8_t bit = 0U; bit < 8U; bit++) {
    checksum |= (volvo_cma_parity8(b0 & masks[bit][0]) ^ volvo_cma_parity8(b1 & masks[bit][1]) ^ volvo_cma_parity8(b3 & masks[bit][2]) ^ volvo_cma_parity8(b4 & masks[bit][3])) << bit;
  }
  return checksum;
}

static uint8_t volvo_cma_crc_pscm_first(uint8_t b1, uint8_t b2) {
  static const uint8_t masks[8][2] = {
    {0x41U, 0x00U},
    {0x82U, 0x00U},
    {0x55U, 0x80U},
    {0xF3U, 0x00U},
    {0xA7U, 0x80U},
    {0x46U, 0x00U},
    {0x94U, 0x80U},
    {0x20U, 0x80U},
  };
  uint8_t checksum = 0U;
  for (uint8_t bit = 0U; bit < 8U; bit++) {
    checksum |= (volvo_cma_parity8(b1 & masks[bit][0]) ^ volvo_cma_parity8(b2 & masks[bit][1])) << bit;
  }
  return checksum;
}

static uint8_t volvo_cma_crc_lca5(uint8_t b0, uint8_t b1, uint8_t b3, uint8_t b4, uint8_t b5) {
  static const uint8_t masks[8][5] = {
    {0xACU, 0x04U, 0x50U, 0x63U, 0x48U},
    {0x99U, 0x09U, 0xA0U, 0x44U, 0x90U},
    {0x1EU, 0x17U, 0x10U, 0x6AU, 0x6AU},
    {0x91U, 0x2AU, 0x70U, 0x37U, 0x9EU},
    {0x8EU, 0x50U, 0xB0U, 0x0EU, 0x74U},
    {0x1DU, 0xA0U, 0x60U, 0x1CU, 0xE8U},
    {0xBBU, 0x41U, 0xD0U, 0x39U, 0xD2U},
    {0x36U, 0x82U, 0xA0U, 0x71U, 0xA4U},
  };
  uint8_t checksum = 0U;
  for (uint8_t bit = 0U; bit < 8U; bit++) {
    checksum |= (volvo_cma_parity8(b0 & masks[bit][0]) ^ volvo_cma_parity8(b1 & masks[bit][1]) ^ volvo_cma_parity8(b3 & masks[bit][2]) ^ volvo_cma_parity8(b4 & masks[bit][3]) ^ volvo_cma_parity8(b5 & masks[bit][4])) << bit;
  }
  return checksum;
}

static uint8_t volvo_cma_crc_pscm_second(uint8_t b2) {
  static const uint8_t values[15] = {0xBFU, 0xF3U, 0x27U, 0x6BU, 0x92U, 0xDEU, 0x0AU, 0x46U, 0xE5U, 0xA9U, 0x7DU, 0x31U, 0xC8U, 0x84U, 0x50U};
  return ((b2 >= 0x80U) && (b2 <= 0x8EU)) ? values[b2 - 0x80U] : 0U;
}
static uint32_t volvo_cma_get_checksum(const CANPacket_t *msg) {
  uint32_t value = 0U;
  if (msg->addr == 0x69U) {
    value = (uint32_t)msg->data[6] | ((uint32_t)msg->data[2] << 8U);
  } else if (msg->addr == 0x17U) {
    value = (uint32_t)msg->data[0] | ((uint32_t)msg->data[3] << 8U);
  } else if (msg->addr == 0x67U) {
    value = msg->data[2];
  } else {
    // Unsupported checksum domains retain the zero result.
  }
  return value;
}
static uint32_t volvo_cma_compute_checksum(const CANPacket_t *msg) {
  const uint8_t *d = msg->data;
  uint32_t value = 0U;
  if (msg->addr == 0x69U) {
    value = (uint32_t)volvo_cma_crc_lca2_first(d[0], d[5]) |
      ((uint32_t)volvo_cma_crc_lca2_second(d[0], d[1], d[3], d[4]) << 8U);
  } else if (msg->addr == 0x17U) {
    value = (uint32_t)volvo_cma_crc_pscm_first(d[1], d[2]) |
      ((uint32_t)volvo_cma_crc_pscm_second(d[2]) << 8U);
  } else if (msg->addr == 0x67U) {
    value = volvo_cma_crc_lca5(d[0], d[1], d[3], d[4], d[5]);
  } else {
    // Unsupported checksum domains retain the zero result.
  }
  return value;
}
static bool volvo_cma_quality(const CANPacket_t *msg, bool allow_counter_lag) {
  bool valid = true;
  if (msg->addr == 0x17U) {
    // Captured source counters are equal or the replica lags one modulo 15.
    // Unknown status/checksum domains and unconstrained echo bit3 stay unsupported.
    const uint8_t counter = msg->data[1] >> 4U;
    const uint8_t replica = msg->data[2] & 0xFU;
    valid = ((msg->data[1] & 0x8U) == 0U) && (msg->data[2] >= 0x80U) && (msg->data[2] <= 0x8EU) &&
      (counter < 15U) && ((counter == replica) || (allow_counter_lag && (counter == ((replica + 1U) % 15U))));
  }
  return valid;
}
