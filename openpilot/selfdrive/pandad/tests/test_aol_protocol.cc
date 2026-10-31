#include "openpilot/starpilot/car/ford/aol_policy.h"
#include "openpilot/starpilot/car/nissan/aol_policy.h"
#include "selfdrive/pandad/aol_protocol.h"
#include "openpilot/starpilot/car/honda/aol_policy.h"
#include "openpilot/starpilot/car/mazda/aol_policy.h"
#include "openpilot/starpilot/car/volkswagen/aol_policy.h"
#include "openpilot/starpilot/car/chrysler/aol_policy.h"
#include "openpilot/starpilot/car/subaru/aol_policy.h"
#include "openpilot/starpilot/car/toyota/aol_policy.h"
#include "openpilot/starpilot/car/tesla/aol_policy.h"
#include "openpilot/starpilot/car/hyundai/aol_policy.h"
#include "openpilot/starpilot/car/gm/aol_policy.h"

#include <cassert>
#include <cstring>
#include <deque>
#include <vector>

namespace {
constexpr uint8_t TEST_MODE = 7U;
constexpr uint8_t TEST_ALT_MODE = 8U;
constexpr uint16_t TEST_PARAM = 0x1234U;
constexpr uint64_t NOW = 1000000000ULL;

bool test_param(uint16_t param) { return param == TEST_PARAM; }
bool test_alt_param(uint16_t param) { return param == 0x2345U; }
constexpr AolSafetyProfile TEST_PROFILES[] = {
  {TEST_MODE, test_param, false}, {TEST_ALT_MODE, test_alt_param, false},
};
const AolProfileRegistry TEST_REGISTRY{TEST_PROFILES, std::size(TEST_PROFILES)};
constexpr AolSafetyProfile VEHICLE_PROFILES[] = {HONDA_AOL_PROFILE, HONDA_STOCK_AOL_PROFILE, HONDA_NIDEC_AOL_PROFILE, HYUNDAI_AOL_PROFILE, HYUNDAI_CLASSIC_AOL_PROFILE, HYUNDAI_LEGACY_AOL_PROFILE, GM_AOL_PROFILE, FORD_AOL_PROFILE, MAZDA_AOL_PROFILE, CHRYSLER_AOL_PROFILE, TESLA_PREAP_AOL_PROFILE, TESLA_SCREEN_AOL_PROFILE, TOYOTA_AOL_PROFILE, NISSAN_AOL_PROFILE, VOLKSWAGEN_STOCK_AOL_PROFILE, VOLKSWAGEN_LONG_AOL_PROFILE, VOLKSWAGEN_MEB_AOL_PROFILE, SUBARU_AOL_PROFILE};
const AolProfileRegistry VEHICLE_REGISTRY{VEHICLE_PROFILES, std::size(VEHICLE_PROFILES)};

aol_safety_health_t status(uint8_t request = 0U, uint8_t permission = 0U) {
  return {AOL_SAFETY_PROTOCOL_MAGIC, AOL_SAFETY_PROTOCOL_VERSION, request, permission,
          TEST_MODE, TEST_PARAM, 0x1U};
}

std::vector<unsigned char> bytes(const aol_safety_health_t &s) {
  std::vector<unsigned char> raw(sizeof(s));
  std::memcpy(raw.data(), &s, sizeof(s));
  return raw;
}

struct FakeTransport {
  std::deque<std::vector<unsigned char>> replies;
  bool write_ok = true;
  bool wrote = false;
  uint8_t last_write = 0xffU;

  std::optional<aol_safety_health_t> read() {
    if (replies.empty()) return std::nullopt;
    auto raw = replies.front();
    replies.pop_front();
    return parse_aol_status(raw.data(), static_cast<int>(raw.size()));
  }

  bool write(uint8_t request) {
    wrote = true;
    last_write = request;
    return write_ok;
  }
};

struct Step {
  AolWritePlan plan;
  AolOutcome outcome;
};

Step run(AolAxisNegotiator &negotiator, FakeTransport &transport, const AolAxisInput &axis,
         uint64_t now_ns = NOW, uint8_t expected_mode = TEST_MODE) {
  auto before = transport.read();
  auto plan = negotiator.prepare(before, axis, now_ns, expected_mode);
  bool write_ok = plan.capable && transport.write(plan.request_mask);
  auto after = write_ok ? transport.read() : std::nullopt;
  return {plan, negotiator.complete(plan, write_ok, after)};
}

AolAxisInput axis(const char *session = "drive-1", bool lat = true, bool lon = false) {
  return {true, true, session, NOW - 1000000ULL, NOW + 100000000ULL,
          NOW - 1000000ULL, lat, lon};
}

void queue(FakeTransport &transport, const aol_safety_health_t &before,
           const aol_safety_health_t &after) {
  transport.replies.push_back(bytes(before));
  transport.replies.push_back(bytes(after));
}
}  // namespace

int main() {
  for (uint32_t word = 0U; word <= 65535U; word++) {
    auto subaru = status();
    subaru.safety_mode = 11U;
    subaru.safety_param = static_cast<uint16_t>(word);
    subaru.capability_flags = 1U;
    const bool expected = (word == 16U) || (word == 0U) || (word == 1U) || (word == 17U) ||
                          (word == 209U) || (word == 465U) || (word == 145U) || (word == 1169U) || (word == 1425U) || (word == 401U) || (word == 177U) || (word == 8U) || (word == 520U) || (word == 512U) || (word == 2225U) ||
                          (word == 4096U) || (word == 4104U) || (word == 4608U) || (word == 4616U);
    assert(aol_capable(subaru, 11U, VEHICLE_REGISTRY) == expected);
    subaru.capability_flags = 0U;
    assert(!aol_capable(subaru, 11U, VEHICLE_REGISTRY));
  }

  for (uint32_t word = 0U; word <= 65535U; ++word) {
    const bool expected = word == 8U || word == 10U || word == 12U || word == 14U ||
                          word == 24U || word == 26U || word == 28U || word == 30U;
    auto meb = status();
    meb.safety_mode = 34U;
    meb.safety_param = static_cast<uint16_t>(word);
    assert(volkswagen_meb_aol_param(static_cast<uint16_t>(word)) == expected);
    assert(aol_capable(meb, 34U, VEHICLE_REGISTRY) == expected);
    meb.capability_flags = 0U;
    assert(!aol_capable(meb, 34U, VEHICLE_REGISTRY));
  }

  for (uint32_t word = 0U; word <= 65535U; ++word) {
    const bool stock = (word == 8U) || (word == 24U) || (word == 40U);
    const bool longitudinal = (word == 9U) || (word == 25U) || (word == 41U);
    const bool expected = stock || longitudinal;
    auto vw = status();
    vw.safety_mode = 15U;
    vw.safety_param = static_cast<uint16_t>(word);
    assert(volkswagen_stock_aol_param(static_cast<uint16_t>(word)) == stock);
    assert(volkswagen_long_aol_param(static_cast<uint16_t>(word)) == longitudinal);
    assert(aol_capable(vw, 15U, VEHICLE_REGISTRY) == expected);
    assert(aol_runtime_enabled(false, vw, VEHICLE_REGISTRY) == expected);
    assert(!aol_capable(vw, 25U, VEHICLE_REGISTRY));
  }
  for (const uint16_t word : {8U, 24U}) {
    AolAxisNegotiator negotiator(VEHICLE_REGISTRY);
    FakeTransport transport;
    auto vw = status();
    vw.safety_mode = 15U;
    vw.safety_param = word;
    queue(transport, vw, vw);
    auto neutral = run(negotiator, transport, axis("vw-current", false, false), NOW, 15U);
    assert(neutral.plan.capable && neutral.plan.request_mask == 0U && neutral.outcome.compatible);
    auto accepted = vw;
    accepted.request_mask = accepted.permission_mask = 1U;
    queue(transport, vw, accepted);
    auto active = run(negotiator, transport, axis("vw-current"), NOW, 15U);
    assert(active.plan.request_mask == 1U && active.outcome.compatible);
    assert(active.outcome.status && active.outcome.status->permission_mask == 1U);
    queue(transport, accepted, vw);
    auto expired = run(negotiator, transport, axis("vw-current"), NOW + 200000000ULL, 15U);
    assert(expired.plan.request_mask == 0U);
    queue(transport, vw, vw);
    auto replaced = run(negotiator, transport, axis("new-vw-session"), NOW, 15U);
    assert(replaced.plan.request_mask == 0U);
    transport.write_ok = false;
    transport.replies.push_back(bytes(vw));
    auto failed = run(negotiator, transport, axis("new-vw-session"), NOW, 15U);
    assert(!failed.outcome.compatible);
  }

  // Nissan admits only stock ICE, Altima and the distinct stock Leaf word.
  // Dom's disabled Leaf longitudinal bit and every unknown word stay denied.
  for (uint32_t word = 0U; word <= 65535U; ++word) {
    auto nissan = status();
    nissan.safety_mode = 14U;
    nissan.safety_param = static_cast<uint16_t>(word);
    const bool supported = word == 0U || word == 1U || word == 4U;
    assert(aol_capable(nissan, 14U, VEHICLE_REGISTRY) == supported);
    assert(aol_runtime_enabled(false, nissan, VEHICLE_REGISTRY) == supported);
    assert(!aol_capable(nissan, 2U, VEHICLE_REGISTRY));
    nissan.capability_flags = 0U;
    assert(!aol_capable(nissan, 14U, VEHICLE_REGISTRY));
    assert(!aol_runtime_enabled(false, nissan, VEHICLE_REGISTRY));
  }

  // Exact stock and guarded Hybrid Chrysler words, with every other word denied.
  for (uint32_t word = 0U; word <= 65535U; ++word) {
    auto chrysler = status();
    chrysler.safety_mode = 9U;
    chrysler.safety_param = static_cast<uint16_t>(word);
    assert(aol_capable(chrysler, 9U, VEHICLE_REGISTRY) == ((word == 0U) || (word == 1U) || (word == 4U) || (word == 8U)));
    chrysler.capability_flags = 0U;
    assert(!aol_capable(chrysler, 9U, VEHICLE_REGISTRY));
  }

  for (uint32_t word = 0U; word <= 65535U; ++word) {
    const bool expected = (word == 0x0A49U) || (word == 0x1849U) || (word == 0x5249U) || (word == 0x5349U) || (word == 0x5358U) || (word == 0x5364U) || (word == 0x564DU) || (word == 0x5749U) || (word == 0x6242U) || (word == 0x6249U) || (word == 0x3049U) || (word == 0x3249U) || (word == 0x3449U) || (word == 0x3649U) || (word == 0xA042U) || (word == 0xA049U) || (word == 0xA242U) || (word == 0xA249U) || (word == 0xA449U) || (word == 0xA649U) || (word == 0xA849U) || (word == 0xA949U) || (word == 0xA958U) || (word == 0xA964U) || (word == 0xAA49U) || (word == 0xAB49U) || (word == 0xAB58U) || (word == 0xAB64U) || (word == 0xB149U) || (word == 0xB158U) || (word == 0xB164U) || (word == 0xB349U) || (word == 0xB358U) || (word == 0xB364U) || (word == 0xB449U) || (word == 0xB549U) || (word == 0xB558U) || (word == 0xB564U) || (word == 0xB649U) || (word == 0xB749U) || (word == 0xB758U) || (word == 0xB764U) || (word == 0xB842U) || (word == 0xB849U) || (word == 0xBA42U) || (word == 0xBA49U) || (word == 0xE042U) || (word == 0xE049U) || (word == 0xE242U) || (word == 0xE249U) || (word == 0xE449U) || (word == 0xE649U) || (word == 0xE84DU) || (word == 0xE949U) || (word == 0xEA4DU) || (word == 0xEB49U) || (word == 0xF049U) || (word == 0xF04DU) || (word == 0xF149U) || (word == 0xF249U) || (word == 0xF24DU) || (word == 0xF349U) || (word == 0xF44DU) || (word == 0xF549U) || (word == 0xF64DU) || (word == 0xF749U) || (word == 0x8949U) || (word == 0x8B49U) || (word == 0x8849U) || (word == 0x8A49U) || (word == 0x9849U) || (word == 0x9A49U) || (word == 0x9842U) || (word == 0x9A42U) || (word == 0x8958U) || (word == 0x8B58U) || (word == 0x8964U) || (word == 0x8B64U) || (word == 0xC84DU) || (word == 0xCA4DU) || (word == 0xC949U) || (word == 0xCB49U) || (word == 0xB049U) || (word == 0xB249U) || (word == 0xD049U) || (word == 0xD249U) || (word == 0xC042U) || (word == 0xC242U) || (word == 0x8042U) || (word == 0x8242U) || (word == 0xC049U) || (word == 0xC249U) || (word == 73U) || (word == 585U) || (word == 856U) || (word == 4169U) || (word == 4681U) || (word == 2121U) || (word == 0x9049U) || (word == 0x9249U) || (word == 0x2449U) || (word == 0x2649U) || (word == 0x2049U) || (word == 0x2249U) || (word == 0x424DU) || (word == 0x449U) || (word == 0x649U) || (word == 0x1449U) || (word == 0x1649U) || (word == 0x8049U) || (word == 0x8249U) || (word == 0x8449U) || (word == 0x8649U) || (word == 0x9158U) || (word == 0x9358U) || (word == 0x9149U) || (word == 0x9349U) || (word == 0x9164U) || (word == 0x9364U) || (word == 0xd149U) || (word == 0xd349U) || (word == 0xd04dU) || (word == 0xd24dU) || (word == 0x9449U) || (word == 0x9549U) || (word == 0x9558U) || (word == 0x9564U) || (word == 0x9649U) || (word == 0x9749U) || (word == 0x9758U) || (word == 0x9764U) || (word == 0xD44DU) || (word == 0xD549U) || (word == 0xD64DU) || (word == 0xD749U);
    auto toyota = status();
    toyota.safety_mode = 2U;
    toyota.safety_param = static_cast<uint16_t>(word);
    assert(aol_capable(toyota, 2U, VEHICLE_REGISTRY) == expected);
    toyota.capability_flags = 0U;
    assert(!aol_capable(toyota, 2U, VEHICLE_REGISTRY));
  }


  {
    auto sienna = status();
    sienna.safety_mode = 2U;
    sienna.safety_param = 2121U;
    assert(aol_runtime_enabled(false, sienna, VEHICLE_REGISTRY));
    auto release = sienna;
    release.capability_flags = 0U;
    assert(!aol_runtime_enabled(false, release, VEHICLE_REGISTRY));
    for (const uint16_t word : {2120U, 2122U, 2633U, 3145U, 4169U}) {
      auto different = sienna;
      different.safety_param = word;
      assert(aol_capable(different, 2U, VEHICLE_REGISTRY) == ((word == 2633U) || (word == 4169U)));
    }
    AolAxisNegotiator negotiator(VEHICLE_REGISTRY);
    FakeTransport transport;
    auto input = axis("sienna-secoc", true, true);
    queue(transport, sienna, sienna);
    auto neutral = run(negotiator, transport, input, NOW, 2U);
    assert(neutral.plan.capable && neutral.plan.request_mask == 0U && neutral.outcome.compatible);
    assert(neutral.outcome.session == "sienna-secoc");
    auto active = sienna;
    active.request_mask = active.permission_mask = 3U;
    queue(transport, sienna, active);
    auto engaged = run(negotiator, transport, input, NOW, 2U);
    assert(engaged.plan.request_mask == 3U && engaged.outcome.compatible);
    assert(engaged.outcome.status && engaged.outcome.status->permission_mask == 3U);
    auto invalid = input;
    invalid.qualified = false;
    queue(transport, active, sienna);
    assert(run(negotiator, transport, invalid, NOW, 2U).plan.request_mask == 0U);
    auto expired = input;
    expired.valid_until_ns = NOW - 1U;
    queue(transport, sienna, sienna);
    assert(run(negotiator, transport, expired, NOW, 2U).plan.request_mask == 0U);
    auto future = input;
    future.observed_ns = NOW + 1U;
    queue(transport, sienna, sienna);
    assert(run(negotiator, transport, future, NOW, 2U).plan.request_mask == 0U);
    auto changed = sienna;
    changed.safety_param = 73U;
    queue(transport, sienna, changed);
    assert(!run(negotiator, transport, input, NOW, 2U).outcome.compatible);
    queue(transport, sienna, sienna);
    auto renegotiated = run(negotiator, transport, input, NOW, 2U);
    assert(renegotiated.plan.request_mask == 0U && renegotiated.outcome.compatible);
    queue(transport, sienna, active);
    transport.write_ok = false;
    assert(!run(negotiator, transport, input, NOW, 2U).outcome.compatible);
  }
  for (uint32_t word = 0U; word <= 65535U; word++) {
    assert(mazda_aol_param(static_cast<uint16_t>(word)) == (word == 0U));
    const bool preap_expected = (word == 0U) || (word == 1U) || (word == 2U) || (word == 3U) || (word == 6U) || (word == 7U);
    assert(tesla_preap_aol_param(static_cast<uint16_t>(word)) == preap_expected);
    auto preap = status();
    preap.safety_mode = 39U;
    preap.safety_param = static_cast<uint16_t>(word);
    assert(aol_capable(preap, 39U, VEHICLE_REGISTRY) == preap_expected);
    assert(!aol_capable(preap, 35U, VEHICLE_REGISTRY));
  }
  constexpr uint16_t ford_words[] = {0U, 1U, 2U, 3U, 8U, 9U, 10U, 11U, 12U, 13U};
  for (uint32_t word = 0U; word <= 65535U; ++word) {
    bool expected = false;
    // Bits 9-14 carry the BluePilot lateral library's startup bits, whatever they hold.
    const uint32_t profile = word & ~0x7E00U;
    for (const auto exact : ford_words) expected |= (profile == exact) || (profile == (exact | 256U));
    auto ford = status();
    ford.safety_mode = 6U;
    ford.safety_param = static_cast<uint16_t>(word);
    assert(aol_capable(ford, 6U, VEHICLE_REGISTRY) == expected);
  }
  constexpr uint16_t tesla_screen_words[] = {512U, 513U, 1536U, 1537U};
  for (uint32_t word = 0U; word <= 65535U; ++word) {
    bool expected = false;
    for (const auto exact : tesla_screen_words) expected |= word == exact;
    auto tesla = status();
    tesla.safety_mode = 10U;
    tesla.safety_param = static_cast<uint16_t>(word);
    assert(aol_capable(tesla, 10U, VEHICLE_REGISTRY) == expected);
  }
  for (const auto word : tesla_screen_words) {
    AolAxisNegotiator negotiator(VEHICLE_REGISTRY);
    FakeTransport transport;
    auto tesla = status();
    tesla.safety_mode = 10U;
    tesla.safety_param = word;
    queue(transport, tesla, tesla);
    auto neutral = run(negotiator, transport, axis("tesla-screen", false, false), NOW, 10U);
    assert(neutral.plan.capable && neutral.plan.request_mask == 0U && neutral.outcome.compatible);
    auto active = tesla;
    active.request_mask = 1U;
    active.permission_mask = 1U;
    queue(transport, tesla, active);
    auto engaged = run(negotiator, transport, axis("tesla-screen", true, false), NOW, 10U);
    assert(engaged.plan.request_mask == 1U && engaged.outcome.compatible);
    assert(engaged.outcome.status && engaged.outcome.status->safety_param == word && engaged.outcome.status->permission_mask == 1U);
  }
  AolAxisNegotiator ford_pause(VEHICLE_REGISTRY);
  FakeTransport ford_transport;
  auto ford_status = status();
  ford_status.safety_mode = 6U;
  ford_status.safety_param = 32U;
  auto ford_axis = axis("ford-session", false, false);
  ford_axis.retain_lateral_arm = true;
  queue(ford_transport, ford_status, ford_status);
  auto ford_step = run(ford_pause, ford_transport, ford_axis, NOW, 6U);
  assert(ford_step.plan.request_mask == 0U && !ford_step.plan.retain_lateral_arm);
  queue(ford_transport, ford_status, ford_status);
  ford_step = run(ford_pause, ford_transport, ford_axis, NOW, 6U);
  assert(ford_step.plan.request_mask == 0U && ford_step.plan.retain_lateral_arm);
  const bool ordinary_engaged = false;
  const bool heartbeat_engaged = ordinary_engaged || ford_step.plan.request_mask != 0U || ford_step.plan.retain_lateral_arm;
  assert(heartbeat_engaged);
  queue(ford_transport, ford_status, ford_status);
  ford_step = run(ford_pause, ford_transport, ford_axis, NOW + 201000000ULL, 6U);
  assert(ford_step.plan.request_mask == 0U && !ford_step.plan.retain_lateral_arm);

  static_assert(sizeof(aol_safety_health_t) == 11U);
  static_assert(offsetof(aol_safety_health_t, safety_param) == 8U);
  static_assert(offsetof(aol_safety_health_t, capability_flags) == 10U);
  for (uint32_t word = 0U; word <= 65535U; word++) {
    auto forte = status();
    forte.safety_mode = HYUNDAI_CLASSIC_AOL_PROFILE.mode;
    forte.safety_param = static_cast<uint16_t>(word);
    bool scc_expected = false;
    for (const uint16_t gas : {0U, 1U, 2U}) {
      for (const uint16_t limits : {0U, 64U, 512U}) {
        for (const uint16_t bus : {0U, 8U}) {
          for (const uint16_t lda : {0U, 2048U}) {
            scc_expected |= word == (0x0400U | gas | limits | bus | lda);
          }
        }
      }
    }
    scc_expected |= word == 0x8408U || word == 0x840AU || word == 0x8C08U || word == 0x8C0AU;
    auto legacy = forte;
    legacy.safety_mode = HYUNDAI_LEGACY_AOL_PROFILE.mode;
    assert(aol_capable(legacy, HYUNDAI_LEGACY_AOL_PROFILE.mode, VEHICLE_REGISTRY) ==
           (word == 0xE902U || word == 0xE903U || word == 0xE912U || word == 0xE913U ||
            (scc_expected && ((word & 8U) == 0U))));
    bool long_expected = false;
    for (const uint16_t gas : {0U, 1U, 2U}) {
      for (const uint16_t limits : {0U, 64U}) {
        for (const uint16_t lda : {0U, 2048U}) {
          long_expected |= word == (0x0404U | gas | limits | lda);
        }
      }
    }
    const bool expected = word == 0x2000U || word == 0x2004U || word == 0x2010U || word == 0x2014U || word == 0x0500U || word == 0x0D00U || long_expected || scc_expected || word == 0x1400U || word == 0x1C00U || word == 0x1440U || word == 0x1C40U ||
                          word == 0x1402U || word == 0x1C02U || word == 0x1441U || word == 0x1C41U;
    assert(aol_capable(forte, HYUNDAI_CLASSIC_AOL_PROFILE.mode, VEHICLE_REGISTRY) == expected);
    assert(aol_runtime_enabled(false, forte, VEHICLE_REGISTRY) == expected);
    forte.capability_flags = 0U;
    assert(!aol_capable(forte, HYUNDAI_CLASSIC_AOL_PROFILE.mode, VEHICLE_REGISTRY));
  }
  {
    AolAxisNegotiator negotiator(VEHICLE_REGISTRY);
    FakeTransport transport;
    auto forte = status();
    forte.safety_mode = HYUNDAI_CLASSIC_AOL_PROFILE.mode;
    forte.safety_param = 0x1C00U;
    queue(transport, forte, forte);
    const auto first = run(negotiator, transport, axis(), NOW, HYUNDAI_CLASSIC_AOL_PROFILE.mode);
    assert(first.plan.capable && first.plan.request_mask == 0U && first.outcome.compatible);
    auto allowed = forte;
    allowed.request_mask = 1U;
    allowed.permission_mask = 1U;
    queue(transport, forte, allowed);
    const auto active = run(negotiator, transport, axis(), NOW, HYUNDAI_CLASSIC_AOL_PROFILE.mode);
    assert(active.plan.request_mask == 1U && active.outcome.compatible);
    queue(transport, allowed, forte);
    const auto stale = run(negotiator, transport, axis(), NOW + 300000000ULL, HYUNDAI_CLASSIC_AOL_PROFILE.mode);
    assert(stale.plan.request_mask == 0U);
    forte.safety_mode = HYUNDAI_AOL_PROFILE.mode;
    assert(!aol_capable(forte, HYUNDAI_AOL_PROFILE.mode, VEHICLE_REGISTRY));
  }
  for (const uint16_t word : {0U, 16U, 0xC161U, 0xC162U, 0xC163U, 0x80U,
                            0xE100U, 0xE101U, 0xE102U, 0xE103U,
                            0xE110U, 0xE111U, 0xE112U, 0xE113U,
                            0xE200U, 0xE201U, 0xE202U, 0xE203U,
                            0xE210U, 0xE211U, 0xE212U, 0xE213U,
                            0xE220U, 0xE221U, 0xE222U, 0xE223U,
                            0xE240U, 0xE241U, 0xE242U, 0xE243U,
                            0xE260U, 0xE261U, 0xE262U, 0xE263U,
                            0xE300U, 0xE301U, 0xE302U, 0xE303U,
                            0xE310U, 0xE311U, 0xE312U, 0xE313U,
                            0xE320U, 0xE321U, 0xE322U, 0xE323U,
                            0xE340U, 0xE341U, 0xE342U, 0xE343U,
                            0xE360U, 0xE361U, 0xE362U, 0xE363U,
                            0xE400U, 0xE401U, 0xE402U, 0xE403U,
                            0xE410U, 0xE411U, 0xE412U, 0xE413U,
                            0xE420U, 0xE421U, 0xE422U, 0xE423U,
                            0xE440U, 0xE441U, 0xE442U, 0xE443U,
                            0xE460U, 0xE461U, 0xE462U, 0xE463U,
                            0xE500U, 0xE501U, 0xE502U, 0xE503U,
                            0xE510U, 0xE511U, 0xE512U, 0xE513U,
                            0xE520U, 0xE521U, 0xE522U, 0xE523U,
                            0xE540U, 0xE541U, 0xE542U, 0xE543U,
                            0xE560U, 0xE561U, 0xE562U, 0xE563U,
                            0xE600U, 0xE601U, 0xE602U, 0xE603U,
                            0xE604U, 0xE605U, 0xE606U, 0xE607U,
                            0xE610U, 0xE611U, 0xE612U, 0xE613U,
                            0xE614U, 0xE615U, 0xE616U, 0xE617U,
                            0xE800U, 0xE801U, 0xE802U, 0xE803U, 0xE804U, 0xE805U,
                            0xE700U, 0xE701U, 0xE702U, 0xE703U,
                            0xE710U, 0xE711U, 0xE712U, 0xE713U,
                            0xD100U, 0xD101U, 0xD102U, 0xD103U, 0xD104U, 0xD105U,
                            0xD106U, 0xD107U, 0xD108U, 0xD109U, 0xD10AU,
                            0xD110U, 0xD111U, 0xD112U, 0xD113U, 0xD114U, 0xD115U,
                            0xD116U, 0xD117U, 0xD118U, 0xD119U, 0xD11AU}) {
    AolAxisNegotiator negotiator(VEHICLE_REGISTRY);
    FakeTransport transport;
    auto gateway = status();
    gateway.safety_mode = GM_AOL_PROFILE.mode;
    gateway.safety_param = word;
    assert(gm_aol_param(word));
    assert(aol_runtime_enabled(false, gateway, VEHICLE_REGISTRY));
    queue(transport, gateway, gateway);
    const auto first = run(negotiator, transport, axis("gateway"), NOW, GM_AOL_PROFILE.mode);
    assert(first.plan.capable && first.plan.request_mask == 0U && first.outcome.compatible);
    assert(first.outcome.status && first.outcome.status->safety_param == word);
    auto active = gateway;
    active.request_mask = 3U;
    active.permission_mask = 1U;
    queue(transport, gateway, active);
    const auto second = run(negotiator, transport, axis("gateway", true, true), NOW, GM_AOL_PROFILE.mode);
    assert(second.plan.capable && second.plan.request_mask == 3U && second.outcome.compatible);
    assert(second.outcome.status && second.outcome.status->permission_mask == 1U && second.outcome.status->safety_param == word);
  }
  for (const uint16_t word : {0x40U, 0x81U, 0x82U, 0x84U, 0x180U, 0xD0FFU, 0xD10BU, 0xD10FU, 0xD11BU, 0xD11FU, 0xD120U,
                            0xE0FFU, 0xE104U, 0xE10FU, 0xE114U, 0xE11FU,
                            0xE1FFU, 0xE204U, 0xE214U, 0xE224U, 0xE230U, 0xE244U, 0xE250U, 0xE264U, 0xE270U,
                            0xE2FFU, 0xE304U, 0xE314U, 0xE324U, 0xE330U, 0xE344U, 0xE350U, 0xE364U, 0xE370U,
                            0xE3FFU, 0xE404U, 0xE414U, 0xE424U, 0xE430U, 0xE444U, 0xE450U, 0xE464U, 0xE470U,
                            0xE4FFU, 0xE504U, 0xE514U, 0xE524U, 0xE530U, 0xE544U, 0xE550U, 0xE564U, 0xE570U,
                            0xE5FFU, 0xE608U, 0xE60FU, 0xE618U, 0xE61FU, 0xE620U,
                            0xE806U, 0xE80FU, 0xE8FFU, 0xE6FFU, 0xE704U, 0xE70FU, 0xE714U, 0xE71FU, 0xE720U}) {
    assert(!gm_aol_param(word));
    auto unknown = status();
    unknown.safety_mode = GM_AOL_PROFILE.mode;
    unknown.safety_param = word;
    assert(!aol_capable(unknown, GM_AOL_PROFILE.mode, VEHICLE_REGISTRY));
    AolAxisNegotiator negotiator(VEHICLE_REGISTRY);
    FakeTransport transport;
    queue(transport, unknown, unknown);
    const auto rejected = run(negotiator, transport, axis(), NOW, GM_AOL_PROFILE.mode);
    assert(!rejected.plan.capable && rejected.plan.request_mask == 0U && !rejected.outcome.compatible);
  }
  for (uint32_t word = 0U; word <= 65535U; word++) {
    auto gm = status();
    gm.safety_mode = GM_AOL_PROFILE.mode;
    gm.safety_param = static_cast<uint16_t>(word);
    const auto decoded = parse_aol_status(bytes(gm).data(), sizeof(gm));
    assert(aol_capable(decoded, GM_AOL_PROFILE.mode, VEHICLE_REGISTRY) == gm_aol_param(gm.safety_param));
    assert(aol_runtime_enabled(false, decoded, VEHICLE_REGISTRY) == gm_aol_param(gm.safety_param));
    gm.capability_flags = 0U;
    const auto absent = parse_aol_status(bytes(gm).data(), sizeof(gm));
    assert(!aol_capable(absent, GM_AOL_PROFILE.mode, VEHICLE_REGISTRY));
  }

  // Ordinary PE transport and the separately qualified LONG/AOL profile
  // both require the native capability; adjacent unqualified words stay denied.
  auto pe = status();
  pe.safety_mode = HYUNDAI_AOL_PROFILE.mode;
  for (uint16_t admitted : {0x5491U, 0x5495U}) {
    pe.safety_param = admitted;
    assert(aol_capable(pe, HYUNDAI_AOL_PROFILE.mode, VEHICLE_REGISTRY));
    assert(aol_runtime_enabled(false, pe, VEHICLE_REGISTRY));
    pe.capability_flags = 0U;
    assert(!aol_capable(pe, HYUNDAI_AOL_PROFILE.mode, VEHICLE_REGISTRY));
    pe.capability_flags = 1U;
  }
  for (uint16_t denied : {0x5490U, 0x5493U, 0x5497U, 0x5411U, 0x54B1U, 0x5691U}) {
    pe.safety_param = denied;
    assert(!aol_capable(pe, HYUNDAI_AOL_PROFILE.mode, VEHICLE_REGISTRY));
  }

  auto ev9 = status();
  ev9.safety_mode = HYUNDAI_AOL_PROFILE.mode;
  // Stock and the separately qualified paired LONG profile are both published.
  for (uint16_t admitted : {0x5C91U, 0x5C95U}) {
    ev9.safety_param = admitted;
    assert(aol_capable(ev9, HYUNDAI_AOL_PROFILE.mode, VEHICLE_REGISTRY));
    assert(aol_runtime_enabled(false, ev9, VEHICLE_REGISTRY));
    ev9.capability_flags = 0U;
    assert(!aol_capable(ev9, HYUNDAI_AOL_PROFILE.mode, VEHICLE_REGISTRY));
    ev9.capability_flags = 1U;
  }
  for (uint16_t denied : {0x5C90U, 0x5C93U, 0x5C97U, 0x5C11U, 0x5CB1U, 0x5E91U}) {
    ev9.safety_param = denied;
    assert(!aol_capable(ev9, HYUNDAI_AOL_PROFILE.mode, VEHICLE_REGISTRY));
  }

  constexpr uint16_t stock_canfd_words[] = {
    0x0800U, 0x0801U, 0x0802U, 0x0808U, 0x0809U, 0x080AU,
    0x0810U, 0x0811U, 0x0812U, 0x0820U, 0x0821U, 0x0822U,
    0x0828U, 0x0829U, 0x082AU, 0x0890U, 0x0891U, 0x0892U,
    0x0C08U, 0x0C09U, 0x0C0AU, 0x0C28U, 0x0C29U, 0x0C2AU,
    0x2820U, 0x2822U, 0x2828U, 0x282AU, 0x2830U, 0x2832U,
    0x28B0U, 0x28B2U, 0x2C28U, 0x2C2AU,
  };
  constexpr uint16_t stock_angle_words[] = {
    0x4008U, 0x4010U, 0x4028U, 0x4090U, 0x4208U, 0x4228U, 0x4408U, 0x440AU,
    0x4410U, 0x4412U, 0x4428U, 0x442AU, 0x4490U, 0x4492U, 0x4608U, 0x460AU,
    0x4628U, 0x462AU, 0x4809U, 0x4811U, 0x4829U, 0x4891U, 0x4A09U, 0x4A29U,
    0x4C08U, 0x4C28U, 0x4E08U, 0x4E28U, 0x5008U, 0x5010U, 0x5028U, 0x5090U,
    0x5208U, 0x5228U, 0x5491U, 0x5809U, 0x5811U, 0x5829U, 0x5891U, 0x5A09U,
    0x5A29U, 0x5C91U, 0x6008U, 0x600AU, 0x6010U, 0x6012U, 0x6028U, 0x602AU,
    0x6090U, 0x6092U, 0x6208U, 0x620AU, 0x6228U, 0x622AU, 0x6808U, 0x680AU,
    0x6810U, 0x6812U, 0x6828U, 0x682AU, 0x6890U, 0x6892U, 0x6A08U, 0x6A0AU,
    0x6A28U, 0x6A2AU, 0x7008U, 0x700AU, 0x7010U, 0x7012U, 0x7028U, 0x702AU,
    0x7090U, 0x7092U, 0x7208U, 0x720AU, 0x7228U, 0x722AU, 0x7809U, 0x7829U,
  };
  constexpr uint16_t sportage_long_words[] = {0x0804U, 0x0806U, 0x080CU, 0x080EU, 0x0814U, 0x0816U,
                                              0x0894U, 0x0896U, 0x0824U, 0x0826U, 0x082CU, 0x082EU};
  for (uint32_t word = 0U; word <= 0xFFFFU; ++word) {
    bool expected = word == 0x2815U || word == 0x2895U || word == 0x0815U || word == 0x0895U || word == 0x8815U || word == 0x8895U || word == 0x5491U || word == 0x5C91U || word == 0x5C95U || word == 0x5495U;
    for (uint16_t stock_word : stock_canfd_words) {
      expected = expected || word == stock_word;
    }
    for (uint16_t stock_word : stock_angle_words) {
      expected = expected || word == stock_word;
    }
    for (uint16_t sportage_word : sportage_long_words) {
      expected = expected || word == sportage_word;
    }
    assert(hyundai_aol_param(static_cast<uint16_t>(word)) == expected);
  }

  const uint64_t sampled_mono = aol_monotonic_ns();
  assert(sampled_mono > 0U && aol_monotonic_ns() >= sampled_mono);

  auto valid = status();
  auto valid_bytes = bytes(valid);
  assert(!parse_aol_status(nullptr, 0));
  assert(!parse_aol_status(valid_bytes.data(), static_cast<int>(valid_bytes.size()) - 1));
  valid.magic = 0;
  auto wrong_magic = bytes(valid);
  assert(!parse_aol_status(wrong_magic.data(), static_cast<int>(wrong_magic.size())));
  valid = status();
  valid.version = AOL_SAFETY_PROTOCOL_VERSION + 1U;
  auto wrong_version = bytes(valid);
  assert(!parse_aol_status(wrong_version.data(), static_cast<int>(wrong_version.size())));

  auto unsupported = status();
  const AolProfileRegistry empty_registry{nullptr, 0U};
  assert(!aol_capable(unsupported, TEST_MODE, empty_registry));
  assert(!aol_runtime_enabled(false, unsupported, empty_registry));
  assert(aol_runtime_enabled(true, unsupported, empty_registry));
  const AolProfileRegistry null_storage{nullptr, 1U};
  assert(!aol_capable(unsupported, TEST_MODE, null_storage));
  const AolSafetyProfile null_predicate{TEST_MODE, nullptr, true};
  const AolProfileRegistry null_predicate_registry{&null_predicate, 1U};
  assert(!aol_capable(unsupported, TEST_MODE, null_predicate_registry));
  assert(!aol_runtime_enabled(false, unsupported, null_predicate_registry));
  unsupported.capability_flags = 0U;  // RELEASE/old firmware
  assert(!aol_capable(unsupported, TEST_MODE, TEST_REGISTRY));
  unsupported = status();
  unsupported.safety_mode = 1U;
  assert(!aol_capable(unsupported, TEST_MODE, TEST_REGISTRY));
  unsupported = status();
  unsupported.safety_param ^= 0x1U;
  assert(!aol_capable(unsupported, TEST_MODE, TEST_REGISTRY));

  for (uint16_t word = 0U; word < 4096U; ++word) {
    auto stock_honda = status();
    stock_honda.safety_mode = 20U;
    stock_honda.safety_param = word;
    const bool classic = word == 34U || word == 35U || word == 163U;
    const bool stock = word == 0U || word == 1U || word == 8U || word == 9U || word == 10U || word == 11U || word == 16U || word == 17U || word == 80U || word == 81U || word == 512U || word == 513U || word == 1058U || word == 1059U || word == 1570U || word == 1571U || word == 2082U || word == 2083U || word == 2211U;
    assert(aol_capable(stock_honda, 20U, VEHICLE_REGISTRY) == (classic || stock));
    assert(aol_runtime_enabled(false, stock_honda, VEHICLE_REGISTRY) == stock);
    stock_honda.safety_mode = 1U;
    const bool nidec = word == 0U || word == 4U || word == 260U || word == 544U || word == 548U || word == 804U;
    assert(aol_capable(stock_honda, 1U, VEHICLE_REGISTRY) == nidec);
    assert(aol_runtime_enabled(false, stock_honda, VEHICLE_REGISTRY) == nidec);
    stock_honda.capability_flags = 0U;
    assert(!aol_capable(stock_honda, 1U, VEHICLE_REGISTRY));
  }

  for (const uint16_t word : {1058U, 1059U, 1570U, 1571U, 2082U, 2083U, 2211U}) {
    auto marked_alt_honda = status();
    marked_alt_honda.safety_mode = 20U;
    marked_alt_honda.safety_param = word;
    assert(aol_capable(marked_alt_honda, 20U, VEHICLE_REGISTRY));
    assert(aol_runtime_enabled(false, marked_alt_honda, VEHICLE_REGISTRY));
    marked_alt_honda.capability_flags = 0U;
    assert(!aol_capable(marked_alt_honda, 20U, VEHICLE_REGISTRY));
  }
  auto honda = status();
  honda.safety_mode = HONDA_AOL_PROFILE.mode;
  honda.safety_param = 0x22U;
  assert(aol_capable(honda, HONDA_AOL_PROFILE.mode, VEHICLE_REGISTRY));
  assert(!aol_runtime_enabled(false, honda, VEHICLE_REGISTRY));
  honda.safety_param |= 0x8U;
  assert(!aol_capable(honda, HONDA_AOL_PROFILE.mode, VEHICLE_REGISTRY));

  auto ioniq = status();
  ioniq.safety_mode = HYUNDAI_AOL_PROFILE.mode;
  ioniq.safety_param = 0x8815U;
  assert(aol_capable(ioniq, HYUNDAI_AOL_PROFILE.mode, VEHICLE_REGISTRY));
  ioniq.safety_param = 0x8895U;
  assert(aol_capable(ioniq, HYUNDAI_AOL_PROFILE.mode, VEHICLE_REGISTRY));
  for (uint16_t raw : {0x0811U, 0x0891U}) {
    ioniq.safety_param = raw;
    assert(aol_capable(ioniq, HYUNDAI_AOL_PROFILE.mode, VEHICLE_REGISTRY));
    assert(aol_runtime_enabled(false, ioniq, VEHICLE_REGISTRY));
    AolAxisNegotiator stock_negotiator(VEHICLE_REGISTRY);
    FakeTransport stock_transport;
    queue(stock_transport, ioniq, ioniq);
    auto stock_result = run(stock_negotiator, stock_transport, axis("stock-session"), NOW,
                            HYUNDAI_AOL_PROFILE.mode);
    assert(stock_transport.last_write == 0U && stock_result.outcome.compatible);
    auto stock_ack = ioniq;
    stock_ack.request_mask = stock_ack.permission_mask = 1U;
    queue(stock_transport, ioniq, stock_ack);
    stock_result = run(stock_negotiator, stock_transport, axis("stock-session"), NOW,
                       HYUNDAI_AOL_PROFILE.mode);
    assert(stock_transport.last_write == 1U && stock_result.outcome.compatible);
    auto stock_paused = axis("stock-session", false, false);
    queue(stock_transport, stock_ack, ioniq);
    stock_result = run(stock_negotiator, stock_transport, stock_paused, NOW,
                       HYUNDAI_AOL_PROFILE.mode);
    assert(stock_transport.last_write == 0U && stock_result.outcome.compatible);
  }
  for (uint16_t raw : {0x0011U, 0x0091U,
                       0x0813U, 0x0831U, 0x0911U, 0x8015U, 0x8095U, 0x8814U, 0x8894U, 0x8817U}) {
    ioniq.safety_param = raw;
    assert(!aol_capable(ioniq, HYUNDAI_AOL_PROFILE.mode, VEHICLE_REGISTRY));
  }
  ioniq.safety_param = 0x8815U;
  assert(!aol_capable(ioniq, HONDA_AOL_PROFILE.mode, VEHICLE_REGISTRY));
  assert(aol_runtime_enabled(false, ioniq, VEHICLE_REGISTRY));
  assert(aol_runtime_enabled(true, ioniq, VEHICLE_REGISTRY));
  assert(aol_runtime_enabled(true, std::nullopt, VEHICLE_REGISTRY));
  assert(!aol_runtime_enabled(false, std::nullopt, VEHICLE_REGISTRY));
  auto stock_ioniq = ioniq;
  stock_ioniq.safety_param = 0x8095U;
  assert(!aol_runtime_enabled(false, stock_ioniq, VEHICLE_REGISTRY));
  assert(!aol_runtime_enabled(false, honda, VEHICLE_REGISTRY));
  ioniq.capability_flags = 0U;
  assert(!aol_capable(ioniq, HYUNDAI_AOL_PROFILE.mode, VEHICLE_REGISTRY));
  assert(!aol_runtime_enabled(false, ioniq, VEHICLE_REGISTRY));

  AolAxisNegotiator negotiator(TEST_REGISTRY);
  AolAxisNegotiator temporary_registry_negotiator(AolProfileRegistry{TEST_PROFILES, std::size(TEST_PROFILES)});
  FakeTransport temporary_registry_transport;
  queue(temporary_registry_transport, status(), status());
  const auto temporary_registry_result = run(temporary_registry_negotiator, temporary_registry_transport, axis());
  assert(temporary_registry_result.plan.capable && temporary_registry_result.outcome.compatible);
  FakeTransport t;
  t.replies.push_back({});  // absent/short first reply cannot authorize a write
  auto result = run(negotiator, t, axis());
  assert(!result.plan.capable && !result.outcome.compatible && !t.wrote);

  queue(t, status(0x3U, 0x3U), status());  // new process flushes old request first
  result = run(negotiator, t, axis());
  assert(result.plan.capable && t.last_write == 0U && result.outcome.compatible);
  assert(result.outcome.session == "drive-1");
  queue(t, status(), status(0x1U, 0x1U));
  result = run(negotiator, t, axis());
  assert(t.last_write == 0x1U && result.outcome.status->request_mask == 0x1U);

  queue(t, status(0x1U, 0x1U), status(0x1U, 0x1U));
  result = run(negotiator, t, axis("drive-1", true, true));
  assert(t.last_write == 0x3U && result.outcome.compatible);
  assert(result.outcome.status->request_mask == 0x1U);  // old long ack; lat remains usable
  queue(t, status(0x3U, 0x3U), status(0x3U, 0x3U));
  result = run(negotiator, t, axis("drive-1", false, true));
  assert(t.last_write == 0x2U && result.outcome.status->request_mask == 0x3U);

  queue(t, status(0x3U, 0x3U), status());
  result = run(negotiator, t, axis("drive-2"));
  assert(t.last_write == 0U && result.outcome.session == "drive-2");
  queue(t, status(), status(0x1U, 0x1U));
  result = run(negotiator, t, axis("drive-2"));
  assert(t.last_write == 0x1U);

  auto stale = axis("drive-2");
  stale.valid_until_ns = NOW - 1U;
  queue(t, status(0x1U, 0x1U), status());
  result = run(negotiator, t, stale);
  assert(t.last_write == 0U && result.outcome.status->request_mask == 0U);

  t.replies.push_back(bytes(status()));
  t.write_ok = false;
  result = run(negotiator, t, axis("drive-3"));
  assert(t.last_write == 0U && !result.outcome.compatible && result.outcome.session.empty());
  t.write_ok = true;
  queue(t, status(), status(0x1U, 0x1U));  // zero write without zero ack cannot finish reset
  result = run(negotiator, t, axis("drive-3"));
  assert(t.last_write == 0U && result.outcome.session.empty());
  queue(t, status(), status());
  result = run(negotiator, t, axis("drive-3"));
  assert(t.last_write == 0U && result.outcome.session == "drive-3");

  t.replies.push_back(bytes(status()));
  t.replies.push_back({});  // failed second read invalidates this tick and resets
  result = run(negotiator, t, axis("drive-3"));
  assert(t.last_write == 0x1U && !result.outcome.compatible);
  queue(t, status(), status());
  result = run(negotiator, t, axis("drive-3"));
  assert(t.last_write == 0U && result.outcome.session == "drive-3");

  auto wrong_identity = status();
  wrong_identity.safety_mode = 1U;
  queue(t, status(), wrong_identity);  // safety identity changed after the write
  result = run(negotiator, t, axis("drive-3"));
  assert(t.last_write == 0x1U && !result.outcome.compatible);
  queue(t, status(), status());
  result = run(negotiator, t, axis("drive-3"));
  assert(t.last_write == 0U && result.outcome.session == "drive-3");

  AolAxisNegotiator ioniq_negotiator(VEHICLE_REGISTRY);
  FakeTransport ioniq_transport;
  auto i6 = status();
  i6.safety_mode = HYUNDAI_AOL_PROFILE.mode;
  i6.safety_param = 0x8815U;
  auto i6_old = i6;
  i6_old.request_mask = i6_old.permission_mask = 0x3U;
  queue(ioniq_transport, i6_old, i6);
  auto i6_result = run(ioniq_negotiator, ioniq_transport, axis("ioniq-session", true, true), NOW,
                       HYUNDAI_AOL_PROFILE.mode);
  assert(i6_result.plan.capable && ioniq_transport.last_write == 0U &&
         i6_result.outcome.compatible && i6_result.outcome.session == "ioniq-session");
  auto i6_both = i6;
  i6_both.request_mask = i6_both.permission_mask = 0x3U;
  queue(ioniq_transport, i6, i6_both);
  i6_result = run(ioniq_negotiator, ioniq_transport, axis("ioniq-session", true, true), NOW,
                  HYUNDAI_AOL_PROFILE.mode);
  assert(ioniq_transport.last_write == 0x3U && i6_result.outcome.compatible &&
         i6_result.outcome.status->permission_mask == 0x3U);
  i6.safety_param = 0x8015U; // ordinary LONG may not retain this AOL session
  queue(ioniq_transport, i6_both, i6);
  i6_result = run(ioniq_negotiator, ioniq_transport, axis("ioniq-session", true, true), NOW,
                  HYUNDAI_AOL_PROFILE.mode);
  assert(ioniq_transport.last_write == 0x3U && !i6_result.outcome.compatible);

  AolAxisNegotiator mode_switch_negotiator(TEST_REGISTRY);
  FakeTransport switch_transport;
  queue(switch_transport, status(), status());
  result = run(mode_switch_negotiator, switch_transport, axis("same-session"));
  assert(switch_transport.last_write == 0U && result.outcome.compatible);
  queue(switch_transport, status(), status(0x1U, 0x1U));
  result = run(mode_switch_negotiator, switch_transport, axis("same-session"));
  assert(switch_transport.last_write == 0x1U);
  auto alternate = status();
  alternate.safety_mode = TEST_ALT_MODE;
  alternate.safety_param = 0x2345U;
  queue(switch_transport, alternate, alternate);
  result = run(mode_switch_negotiator, switch_transport, axis("same-session"), NOW, TEST_ALT_MODE);
  assert(switch_transport.last_write == 0U && result.outcome.compatible);

  // A fresh Python axis frame uses CLOCK_MONOTONIC. Treating BOOTTIME as the
  // same clock after a nine-second suspend leaves the native request at zero.
  AolAxisNegotiator mono_negotiator(TEST_REGISTRY);
  FakeTransport mono_transport;
  queue(mono_transport, status(), status());
  result = run(mono_negotiator, mono_transport, axis("clock-session"), NOW);
  assert(result.plan.request_mask == 0U && result.outcome.compatible);
  queue(mono_transport, status(), status(0x1U, 0x1U));
  result = run(mono_negotiator, mono_transport, axis("clock-session"), NOW + 1000000ULL);
  assert(result.plan.request_mask == 0x1U);
  queue(mono_transport, status(0x1U, 0x1U), status());
  result = run(mono_negotiator, mono_transport, axis("clock-session"), NOW + 201000000ULL);
  assert(result.plan.request_mask == 0U);  // genuine stale frame still revoked

  // Suspended axes keep only the already-negotiated physical arm. No actuator
  // request is added, and a fresh session or stale frame cannot retain it.
  AolAxisNegotiator paused_negotiator(TEST_REGISTRY);
  FakeTransport paused_transport;
  auto paused = axis("paused-session", false, false);
  paused.retain_lateral_arm = true;
  queue(paused_transport, status(), status());
  result = run(paused_negotiator, paused_transport, paused);
  assert(result.plan.request_mask == 0U && !result.plan.retain_lateral_arm);
  for (int i = 0; i < 5; ++i) {
    queue(paused_transport, status(), status());
    result = run(paused_negotiator, paused_transport, paused);
    assert(result.plan.request_mask == 0U && result.plan.retain_lateral_arm);
  }
  queue(paused_transport, status(), status(0x1U, 0x1U));
  result = run(paused_negotiator, paused_transport, axis("paused-session"));
  assert(result.plan.request_mask == 0x1U);
  queue(paused_transport, status(0x1U, 0x1U), status());
  result = run(paused_negotiator, paused_transport, paused, NOW + 201000000ULL);
  assert(result.plan.request_mask == 0U && !result.plan.retain_lateral_arm);
  paused.alive_valid = false;
  queue(paused_transport, status(), status());
  result = run(paused_negotiator, paused_transport, paused);
  assert(result.plan.request_mask == 0U && !result.plan.retain_lateral_arm);
  paused.alive_valid = true;
  paused.session = "replacement-session";
  queue(paused_transport, status(), status());
  result = run(paused_negotiator, paused_transport, paused);
  assert(result.plan.request_mask == 0U && !result.plan.retain_lateral_arm);
  paused.retain_lateral_arm = false;
  queue(paused_transport, status(), status());
  result = run(paused_negotiator, paused_transport, paused);
  assert(result.plan.request_mask == 0U && !result.plan.retain_lateral_arm);

  // Route 78511c37de32c375/0000002f--338c7a2e1f/1: the latest axis
  // expired 1.19 ms before pandad's tick while a newer Card arm was current.
  // Expire both commands, retain only the existing physical arm, and require
  // a new current axis before steering can resume. LONG never resumes here.
  AolAxisNegotiator gap_negotiator(TEST_REGISTRY);
  FakeTransport gap_transport;
  queue(gap_transport, status(), status());
  result = run(gap_negotiator, gap_transport, axis("gap-session"));
  assert(result.plan.request_mask == 0U && result.outcome.compatible);
  queue(gap_transport, status(), status(3U, 3U));
  result = run(gap_negotiator, gap_transport, axis("gap-session", true, true));
  assert(result.plan.request_mask == 3U);
  auto expired_axis = axis("gap-session", true, true);
  expired_axis.observed_ns = 91533516787ULL;
  expired_axis.message_ns = expired_axis.observed_ns;
  expired_axis.valid_until_ns = 91563516787ULL;
  expired_axis.retain_lateral_arm = true;
  constexpr uint64_t GAP_NOW = 91564707308ULL;
  queue(gap_transport, status(3U, 3U), status());
  result = run(gap_negotiator, gap_transport, expired_axis, GAP_NOW);
  assert(result.plan.capable && result.plan.request_mask == 0U && result.plan.retain_lateral_arm);
  for (int refusal = 0; refusal < 9; ++refusal) {
    auto denied = expired_axis;
    if (refusal == 0) denied.retain_lateral_arm = false;
    if (refusal == 1) denied.alive_valid = false;
    if (refusal == 2) denied.qualified = false;
    if (refusal == 3) denied.session = "replacement-session";
    if (refusal == 4) {
      denied.observed_ns = denied.message_ns = GAP_NOW - AOL_AXIS_MESSAGE_MAX_AGE_NS - 1U;
      denied.valid_until_ns = denied.observed_ns + 30000000ULL;
    }
    if (refusal == 5) {
      denied.observed_ns = denied.message_ns = GAP_NOW + 1U;
      denied.valid_until_ns = denied.observed_ns + 30000000ULL;
    }
    if (refusal == 6) denied.valid_until_ns = denied.observed_ns - 1U;
    if (refusal == 7) denied.observed_ns = 0U;
    if (refusal == 8) denied.message_ns -= 1U;
    queue(gap_transport, status(), status());
    result = run(gap_negotiator, gap_transport, denied, GAP_NOW);
    assert(result.plan.request_mask == 0U && !result.plan.retain_lateral_arm);
  }
  auto resumed = axis("gap-session", true, false);
  resumed.observed_ns = resumed.message_ns = GAP_NOW;
  resumed.valid_until_ns = GAP_NOW + 30000000ULL;
  queue(gap_transport, status(), status(1U, 1U));
  result = run(gap_negotiator, gap_transport, resumed, GAP_NOW);
  assert(result.plan.request_mask == 1U && (result.plan.request_mask & 2U) == 0U);
  gap_transport.write_ok = false;
  gap_transport.replies.push_back(bytes(status(1U, 1U)));
  result = run(gap_negotiator, gap_transport, expired_axis, GAP_NOW);
  assert(!result.outcome.compatible);
  gap_transport.write_ok = true;
  queue(gap_transport, status(), status());
  result = run(gap_negotiator, gap_transport, expired_axis, GAP_NOW);
  assert(result.plan.request_mask == 0U && !result.plan.retain_lateral_arm);

  AolAxisNegotiator boot_negotiator(TEST_REGISTRY);
  FakeTransport boot_transport;
  queue(boot_transport, status(), status());
  result = run(boot_negotiator, boot_transport, axis("clock-session"), NOW + 9000000000ULL);
  assert(result.plan.request_mask == 0U && result.outcome.compatible);
  queue(boot_transport, status(), status());
  result = run(boot_negotiator, boot_transport, axis("clock-session"), NOW + 9001000000ULL);
  assert(result.plan.request_mask == 0U);
}
