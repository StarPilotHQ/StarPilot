#include <cassert>

#include "selfdrive/pandad/pandad.h"
#include "common/swaglog.h"
#include "common/util.h"
#include "common/hardware/hw.h"

int main(int argc, char *argv[]) {
#if __COMMA_HARDWARE__
  LOGW("starting pandad (comma hardware)");
#else
  LOGW("starting pandad (PC)");
#endif

  if (!Hardware::PC()) {
    int err;
    err = util::set_realtime_priority(54);
    assert(err == 0);
    err = util::set_core_affinity({3});
    assert(err == 0);
  }

  std::vector<std::string> serials;
  for (int i = 1; i < argc; ++i) serials.emplace_back(argv[i]);
  pandad_main_thread(serials);
  return 0;
}
