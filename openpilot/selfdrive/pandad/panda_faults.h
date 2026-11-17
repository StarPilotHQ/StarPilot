#pragma once

#include <bitset>
#include <cstdint>

#include "openpilot/cereal/gen/cpp/log.capnp.h"

inline void fill_panda_faults(cereal::PandaState::Builder &state, uint32_t mask) {
  std::bitset<32> bits(mask);
  static_assert(static_cast<size_t>(cereal::PandaState::FaultType::TIMER) < 32);
  for (size_t bit = static_cast<size_t>(cereal::PandaState::FaultType::TIMER) + 1; bit < bits.size(); ++bit) {
    bits.reset(bit);
  }
  auto faults = state.initFaults(bits.count());
  size_t index = 0;
  for (size_t bit = 0; bit < bits.size(); ++bit) {
    if (bits.test(bit)) {
      faults.set(index++, static_cast<cereal::PandaState::FaultType>(bit));
    }
  }
}
