#include <cstring>
#include "openpilot/starpilot/car/ford/aol_policy.h"
#include <unistd.h>
#include <cstdlib>
#include <filesystem>
#include <memory>
#include <vector>
#include "common/tests/native_test.h"
#include "openpilot/cereal/messaging/messaging.h"
#include "selfdrive/pandad/pandad.h"
#include "selfdrive/pandad/aol_protocol.h"

class OwnerHandle : public PandaCommsHandle {
public:
  health_t health{};
  aol_safety_health_t aol{};
  std::vector<uint16_t> requests;
  explicit OwnerHandle(std::string serial) {
    hw_serial = serial;
    aol.magic = AOL_SAFETY_PROTOCOL_MAGIC;
    aol.version = AOL_SAFETY_PROTOCOL_VERSION;
  }
  int control_write(uint8_t request, uint16_t p1, uint16_t p2, unsigned int) override {
    if (request == 0xdc) {
      health.safety_mode_pkt = p1; health.safety_param_pkt = p2;
      aol.safety_mode = p1; aol.safety_param = p2;
    } else if (request == 0xdf) {
      health.alternative_experience_pkt = p1;
    } else if (request == 0xf5) {
      requests.push_back(p1); aol.request_mask = p1;
    }
    return 0;
  }
  int control_read(uint8_t request, uint16_t, uint16_t, unsigned char *data, uint16_t length, unsigned int) override {
    if (request == 0xd2 && length == sizeof(health)) { memcpy(data, &health, length); return length; }
    if (request == 0xd5 && length == sizeof(aol)) { memcpy(data, &aol, length); return length; }
    return -1;
  }
  int bulk_write(unsigned char, unsigned char *, int, unsigned int) override { return 0; }
  int bulk_read(unsigned char, unsigned char *, int, unsigned int) override { return 0; }
  void cleanup() override {}
};
class OwnerPanda : public Panda {
public:
  OwnerPanda(std::unique_ptr<OwnerHandle> h, uint32_t offset) : Panda(std::move(h), cereal::PandaState::PandaType::RED_PANDA, offset) {}
};

void test_actual_config_slots_and_owner_handle(cereal::CarParams::SafetyModel mode, uint16_t word) {
  using Mode = cereal::CarParams::SafetyModel;
  const std::string prefix = "test-panda-owner-" + std::to_string(getpid());
  const char *old = getenv("OPENPILOT_PREFIX");
  const std::string old_prefix = old ? old : "";
  setenv("OPENPILOT_PREFIX", prefix.c_str(), 1);
  Params params;
  struct Restore {
    Params &params;
    std::string old;
    ~Restore() {
      for (auto key : {"CarParams", "FirmwareQueryDone", "ControlsReady", "ObdMultiplexingChanged"}) params.remove(key);
      if (old.empty()) unsetenv("OPENPILOT_PREFIX"); else setenv("OPENPILOT_PREFIX", old.c_str(), 1);
    }
  } restore{params, old_prefix};
  auto aux_handle = std::make_unique<OwnerHandle>("auxiliary");
  auto owner_handle = std::make_unique<OwnerHandle>("owner");
  auto extra_handle = std::make_unique<OwnerHandle>("extra");
  auto *aux = aux_handle.get(); auto *owner = owner_handle.get(); auto *extra = extra_handle.get();
  OwnerPanda auxiliary(std::move(aux_handle), 0), primary(std::move(owner_handle), 4), excess(std::move(extra_handle), 8);
  std::vector<Panda *> pandas{&auxiliary, &primary, &excess};
  PandaSafety safety(pandas);
  CHECK(safety.aolOwner() == nullptr);
  capnp::MallocMessageBuilder message;
  auto cp = message.initRoot<cereal::CarParams>();
  cp.setAlternativeExperience(32);
  auto slots = cp.initSafetyConfigs(2);
  slots[0].setSafetyModel(Mode::NO_OUTPUT); slots[0].setSafetyParam(0);
  slots[1].setSafetyModel(mode); slots[1].setSafetyParam(word);
  auto words = capnp::messageToFlatArray(message);
  auto bytes = words.asBytes();
  params.put("CarParams", reinterpret_cast<const char *>(bytes.begin()), bytes.size());
  params.putBool("FirmwareQueryDone", true); params.putBool("ControlsReady", true);
  safety.configureSafetyMode(true);
  CHECK(aux->health.safety_mode_pkt == (uint8_t)Mode::NO_OUTPUT);
  CHECK(owner->health.safety_mode_pkt == (uint8_t)mode);
  CHECK(owner->health.safety_param_pkt == word);
  CHECK(extra->health.safety_mode_pkt == (uint8_t)Mode::SILENT);
  CHECK(aux->health.alternative_experience_pkt == 32 && owner->health.alternative_experience_pkt == 32);
  CHECK(safety.aolOwner() == nullptr);
  std::vector<Panda *> exact{&auxiliary, &primary};
  PandaSafety owner_safety(exact); owner_safety.configureSafetyMode(true);
  CHECK(owner_safety.aolOwner() == &primary);
  CHECK(owner_safety.aolStatusMatches(&primary, owner->aol));
  auto foreign = owner->aol; foreign.safety_param ^= 1;
  CHECK(!owner_safety.aolStatusMatches(&primary, foreign));
  CHECK(!owner_safety.aolStatusMatches(&auxiliary, owner->aol));
  aux->hw_serial = "owner"; CHECK(owner_safety.aolOwner() == nullptr); aux->hw_serial = "auxiliary";
  for (auto *peer : {aux}) {
    peer->health.flags_pkt |= HEALTH_FLAG_CONTROLS_ALLOWED;
    CHECK(owner_safety.aolOwner() == nullptr);
    peer->health.flags_pkt = 0;
    peer->health.safety_param_pkt = word;
    CHECK(owner_safety.aolOwner() == nullptr);
    peer->health.safety_param_pkt = 0;
    peer->comms_healthy = false;
    CHECK(owner_safety.aolOwner() == nullptr);
    peer->comms_healthy = true;
    peer->connected = false;
    CHECK(owner_safety.aolOwner() == nullptr);
    peer->connected = true;
  }
  owner->health.safety_param_pkt = (word ^ 1U); CHECK(owner_safety.aolOwner() == nullptr);
  owner->health.safety_param_pkt = word;
  owner->health.alternative_experience_pkt = 0; CHECK(owner_safety.aolOwner() == nullptr);
  owner->health.alternative_experience_pkt = 32;
  slots[0].setSafetyParam(1);
  { auto changed = capnp::messageToFlatArray(message); auto data = changed.asBytes();
    params.put("CarParams", reinterpret_cast<const char *>(data.begin()), data.size()); }
  owner_safety.configureSafetyMode(false); owner_safety.configureSafetyMode(true);
  aux->health.safety_param_pkt = 0;
  CHECK(owner_safety.aolOwner() == nullptr);
  slots[0].setSafetyParam(0);
  { auto changed = capnp::messageToFlatArray(message); auto data = changed.asBytes();
    params.put("CarParams", reinterpret_cast<const char *>(data.begin()), data.size()); }
  owner_safety.configureSafetyMode(false); owner_safety.configureSafetyMode(true);
  CHECK(owner_safety.aolOwner() == &primary);
  const AolSafetyProfile profiles[] = {{(uint8_t)Mode::HYUNDAI_CANFD, [](uint16_t p) { return p == 0x809; }, true}, FORD_AOL_PROFILE};
  AolProfileRegistry registry{profiles, 2}; AolAxisNegotiator negotiator(registry);
  owner->aol.capability_flags = 1;
  aux->aol = owner->aol; aux->aol.permission_mask = 3; aux->aol.request_mask = 3;
  AolAxisInput axis{true, true, "session", 1, 200000001, 1, true, false};
  for (int i = 0; i < 2; ++i) {
    Panda *selected = owner_safety.aolOwner(); CHECK(selected == &primary);
    auto before = selected->get_aol_safety_state();
    auto plan = negotiator.prepare(before, axis, 2, before->safety_mode);
    CHECK(plan.request_mask == (i == 0 ? 0 : 1));
    CHECK(selected->set_aol_axis_request(plan.request_mask));
    auto outcome = negotiator.complete(plan, true, selected->get_aol_safety_state());
    CHECK(outcome.compatible); CHECK(outcome.status->permission_mask == 0);
    CHECK(selected->hw_serial() == "owner");
  }
  CHECK(aux->requests.empty() && extra->requests.empty());
  CHECK(owner->requests == std::vector<uint16_t>({0, 1}));
  owner_safety.configureSafetyMode(false); CHECK(owner_safety.aolOwner() == nullptr);
  PandaSafety single({&primary}); CHECK(single.aolOwner() == &primary);
}

void test_registered_owners() {
  test_actual_config_slots_and_owner_handle(cereal::CarParams::SafetyModel::HYUNDAI_CANFD, 0x809);
  for (uint16_t word : {0U, 1U, 2U, 3U, 8U, 9U, 10U, 11U, 12U, 13U}) {
    test_actual_config_slots_and_owner_handle(cereal::CarParams::SafetyModel::FORD, word);
    test_actual_config_slots_and_owner_handle(cereal::CarParams::SafetyModel::FORD, word | 256U);
  }
}

int main() { return run_native_test(test_registered_owners); }
