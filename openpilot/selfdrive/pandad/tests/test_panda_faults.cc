#include "openpilot/selfdrive/pandad/panda_faults.h"
#include "openpilot/selfdrive/pandad/aol_wire.h"

#include <cassert>

#ifdef NDEBUG
#error "Native fault tests require active assertions"
#endif

int main() {
  for (uint32_t mask : {0U, 1U << 27, 1U << 28, 1U << 29, 1U << 30, 1U << 31, 0xFFFFFFFFU}) {
    capnp::MallocMessageBuilder message;
    auto event = message.initRoot<cereal::Event>();
    auto state = event.initPandaStates(1)[0];
    fill_panda_faults(state, mask);
    auto bytes = capnp::messageToFlatArray(message);
    capnp::FlatArrayMessageReader reader(bytes.asPtr());
    auto faults = reader.getRoot<cereal::Event>().getPandaStates()[0].getFaults();
    uint32_t represented = 0;
    for (auto fault : faults) {
      const unsigned int bit = static_cast<unsigned int>(fault);
      assert(bit <= 29U);
      assert((represented & (1U << bit)) == 0U);
      represented |= 1U << bit;
    }
    assert(represented == (mask & 0x3FFFFFFFU));
    if ((mask & 1U) == 0U) {
      for (auto fault : faults) assert(fault != cereal::PandaState::FaultType::RELAY_MALFUNCTION);
    }
    AolSafetyWireFields wire = {1, true, 100, 200, 5, 34, true, false, true, false, "panda", "axis",
      {100, {{0, "panda", 5, 34, 0, true, false, false, mask}}}};
    auto raw = encode_aol_safety_wire(wire);
    assert(!raw.empty());
    auto aligned = kj::heapArray<capnp::word>(raw.size() / sizeof(capnp::word));
    std::memcpy(aligned.begin(), raw.data(), raw.size());
    capnp::FlatArrayMessageReader raw_reader(aligned.asPtr());
    assert(raw_reader.getRoot<cereal::AolAxisState::SafetyWire>().getPandaInventory()[0].getFaults() == mask);
  }
}
