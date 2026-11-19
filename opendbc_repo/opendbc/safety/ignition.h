#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "opendbc/safety/can.h"
#include "opendbc/safety/helpers.h"

bool ignition_can = false;
uint32_t ignition_can_cnt = 0U;
bool wake_on_can = false;
uint32_t wake_on_can_cnt = 0U;
#ifdef PANDA_HKG_REMOTE_START
bool hkg_remote_climate_wake = false;
uint32_t hkg_remote_climate_wake_cnt = 0U;
#endif

void ignition_can_hook(const CANPacket_t *msg) {
  #ifdef PANDA_HKG_REMOTE_START
  if (msg_matches(msg, 0x384U, 1U, 8U)) {
    hkg_remote_climate_wake = msg->data[3] != 0U;
    hkg_remote_climate_wake_cnt = 0U;
  }
  #endif

  // GM exception
  #ifdef PANDA_GM_REMOTE_START_C9
  if (msg_matches(msg, 0xC9U, 0U, 8U)) {
    ignition_can = (msg->data[6] & 0x10U) != 0U;
    ignition_can_cnt = 0U;
  }
  #else
  if (msg_matches(msg, 0x1F1U, 0U, 8U)) {
    // SystemPowerMode (2=Run, 3=Crank Request)
    ignition_can = (msg->data[0] & 0x2U) != 0U;
    ignition_can_cnt = 0U;
  }

  #endif

  // Rivian R1S/T GEN1 exception
  if (msg_matches(msg, 0x152U, 0U, 8U)) {
    // 0x152 overlaps with Subaru pre-global which has this bit as the high beam
    int counter = msg->data[1] & 0xFU;  // max is only 14

    static int prev_counter_rivian = -1;
    if ((counter == ((prev_counter_rivian + 1) % 15)) && (prev_counter_rivian != -1)) {
      // VDM_OutputSignals->VDM_EpasPowerMode
      ignition_can = ((msg->data[7] >> 4U) & 0x3U) == 1U;  // VDM_EpasPowerMode_Drive_On=1
      ignition_can_cnt = 0U;
    }
    prev_counter_rivian = counter;
  }

  // Tesla Model 3/Y exception
  if (msg_matches(msg, 0x221U, 0U, 8U)) {
    // 0x221 overlaps with Rivian which has random data on byte 0
    int counter = msg->data[6] >> 4;

    static int prev_counter_tesla = -1;
    if ((counter == ((prev_counter_tesla + 1) % 16)) && (prev_counter_tesla != -1)) {
      // VCFRONT_LVPowerState->VCFRONT_vehiclePowerState
      int power_state = (msg->data[0] >> 5U) & 0x3U;
      ignition_can = power_state == 0x3;  // VEHICLE_POWER_STATE_DRIVE=3
      ignition_can_cnt = 0U;
    }
    prev_counter_tesla = counter;
    #ifdef PANDA_TESLA_WAKE_ON_CAN
    uint32_t checksum = (msg->addr & 0xFFU) + (msg->addr >> 8U);
    for (uint8_t i = 0U; i < 7U; i++) {
      checksum += msg->data[i];
    }
    static int prev_counter_tesla_wake = -1;
    if (!msg->extended && (msg->data[7] == (checksum & 0xFFU))) {
      if ((prev_counter_tesla_wake != -1) && (counter == ((prev_counter_tesla_wake + 1) % 16))) {
        wake_on_can = ((msg->data[0] >> 5U) & 0x3U) != 0U;
        wake_on_can_cnt = 0U;
      }
      prev_counter_tesla_wake = counter;
    } else {
      prev_counter_tesla_wake = -1;
    }
    #endif
  }

  // Tesla Model S pre-AP exception
  if (msg_matches(msg, 0x101U, 0U, 3U)) {
    // Validate Tesla checksum/counter to avoid false positives on overlapping frames.
    int counter = msg->data[1] & 0xFU;
    uint32_t checksum = (((msg->addr & 0xFFU) + ((msg->addr >> 8U) & 0xFFU) + msg->data[0] + msg->data[1]) & 0xFFU);

    static int prev_counter_tesla_preap = -1;
    if ((msg->data[2] == checksum) && (counter == ((prev_counter_tesla_preap + 1) % 16)) && (prev_counter_tesla_preap != -1)) {
      // GTW_epasPowerMode=1 is DRIVE_ON, which is the only ignition source on pre-AP cars.
      uint32_t power_mode = (msg->data[0] >> 3U) & 0xFU;
      ignition_can = power_mode == 0x1U;
      ignition_can_cnt = 0U;
    }
    prev_counter_tesla_preap = counter;
  }

  // Mazda exception
  if (msg_matches(msg, 0x9EU, 0U, 8U)) {
    ignition_can = (msg->data[0] >> 5) == 0x6U;
    ignition_can_cnt = 0U;
  }

  // Mazda 2019+ (GEN2) exception
  if (msg_matches(msg, 0x274U, 0U, 8U)) {
    ignition_can = (msg->data[5] & 0x4U) != 0U;
    ignition_can_cnt = 0U;
  }

  // Volkswagen MEB exception
  if (msg_matches(msg, 0x3C0U, 0U, 4U)) {
    int counter = msg->data[1] & 0xFU;

    static int prev_counter_vw_meb = -1;
    if ((counter == ((prev_counter_vw_meb + 1) % 16)) && (prev_counter_vw_meb != -1)) {
      // Klemmen_Status_01->ZAS_Kl_15
      ignition_can = ((msg->data[2] >> 1) & 1U) != 0U;
      ignition_can_cnt = 0U;
    }
    prev_counter_vw_meb = counter;
  }
}
