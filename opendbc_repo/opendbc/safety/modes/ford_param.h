#pragma once
// The Ford safety param: StarPilot's Ford profile in bits 0-8 (long, CAN FD, LKA steering, new port, and 256
// for AOL's manual transmission), the BluePilot lateral library's startup bits in 9-14.
#define FORD_BP_PARAM_SHIFT 9U
#define FORD_BP_PARAM_MASK 0x7E00U

static inline uint16_t ford_profile_word(uint16_t word) {
  return word & (uint16_t)~FORD_BP_PARAM_MASK;
}
