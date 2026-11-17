#include <getopt.h>

#include <cstdlib>
#include <algorithm>
#include <iomanip>
#include <iostream>
#include <map>
#include <string>
#include <vector>
#include <poll.h>
#include <fcntl.h>
#include <filesystem>
#include <regex>
#include <sys/stat.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif

#include "json11/json11.hpp"

#include "common/prefix.h"
#include "common/timing.h"
#include "tools/replay/consoleui.h"
#include "tools/replay/replay.h"
#include "tools/replay/util.h"

const std::string helpText =
R"(Usage: replay [options] [route]
Options:
  -a, --allow        Whitelist of services to send (comma-separated)
  -b, --block        Blacklist of services to send (comma-separated)
  -c, --cache        Cache <n> segments in memory. Default is 5
  -s, --start        Start from <seconds>
  -x, --playback     Playback <speed>
      --demo         Use a demo route instead of providing your own
      --auto         Auto load the route from the best available source (no video):
                     internal, openpilotci, comma_api, car_segments, testing_closet
  -d, --data_dir     Local directory with routes
  -p, --prefix       Set OPENPILOT_PREFIX
      --cabin        Load cabin camera (alias: --dcam)
      --wide-road    Load wide road camera (alias: --ecam)
      --no-loop      Stop at the end of the route
      --no-cache     Turn off local cache
      --qcam         Load qcamera
      --no-hw-decoder Disable HW video decoding
      --no-vipc      Do not output video
      --all          Output all messages including bookmarkButton, uiDebug, userBookmark
      --benchmark    Run in benchmark mode (process all events then exit with stats)
  -h, --help         Show this help message
      --machine      Isolated local replay controlled by newline JSON on stdin
      --start-segment Start machine replay at a recorded segment boundary
      --preload-seek Prepare the next cached segment before machine seeks resume
)";

struct ReplayConfig {
  std::string route;
  std::vector<std::string> allow;
  std::vector<std::string> block;
  std::string data_dir;
  std::string prefix;
  uint32_t flags = REPLAY_FLAG_NONE;
  bool auto_source = false;
  int start_seconds = 0;
  int start_segment = -1;
  int cache_segments = -1;
  float playback_speed = -1;
  bool machine = false;
};

bool parseArgs(int argc, char *argv[], ReplayConfig &config) {
  const struct option cli_options[] = {
      {"allow", required_argument, nullptr, 'a'},
      {"block", required_argument, nullptr, 'b'},
      {"cache", required_argument, nullptr, 'c'},
      {"start", required_argument, nullptr, 's'},
      {"playback", required_argument, nullptr, 'x'},
      {"demo", no_argument, nullptr, 0},
      {"auto", no_argument, nullptr, 0},
      {"data_dir", required_argument, nullptr, 'd'},
      {"prefix", required_argument, nullptr, 'p'},
      {"cabin", no_argument, nullptr, 0},
      {"dcam", no_argument, nullptr, 0},  // deprecated alias
      {"wide-road", no_argument, nullptr, 0},
      {"ecam", no_argument, nullptr, 0},  // deprecated alias
      {"no-loop", no_argument, nullptr, 0},
      {"no-cache", no_argument, nullptr, 0},
      {"qcam", no_argument, nullptr, 0},
      {"no-hw-decoder", no_argument, nullptr, 0},
      {"no-vipc", no_argument, nullptr, 0},
      {"all", no_argument, nullptr, 0},
      {"benchmark", no_argument, nullptr, 0},
      {"machine", no_argument, nullptr, 0},
      {"start-segment", required_argument, nullptr, 0},
      {"preload-seek", no_argument, nullptr, 0},
      {"help", no_argument, nullptr, 'h'},
      {nullptr, 0, nullptr, 0},  // Terminating entry
  };

  const std::map<std::string, REPLAY_FLAGS> flag_map = {
      {"cabin", REPLAY_FLAG_CABIN_CAMERA},
      {"dcam", REPLAY_FLAG_CABIN_CAMERA},  // deprecated alias
      {"wide-road", REPLAY_FLAG_WIDE_ROAD},
      {"ecam", REPLAY_FLAG_WIDE_ROAD},  // deprecated alias
      {"no-loop", REPLAY_FLAG_NO_LOOP},
      {"no-cache", REPLAY_FLAG_NO_FILE_CACHE},
      {"qcam", REPLAY_FLAG_QCAMERA},
      {"no-hw-decoder", REPLAY_FLAG_NO_HW_DECODER},
      {"no-vipc", REPLAY_FLAG_NO_VIPC},
      {"all", REPLAY_FLAG_ALL_SERVICES},
      {"benchmark", REPLAY_FLAG_BENCHMARK},
      {"preload-seek", REPLAY_FLAG_PRELOAD_SEEK},
  };

  if (argc == 1) {
    std::cout << helpText;
    return false;
  }

  int opt, option_index = 0;
  while ((opt = getopt_long(argc, argv, "a:b:c:s:x:d:p:h", cli_options, &option_index)) != -1) {
    switch (opt) {
      case 'a': config.allow = split(optarg, ','); break;
      case 'b': config.block = split(optarg, ','); break;
      case 'c': config.cache_segments = std::atoi(optarg); break;
      case 's': config.start_seconds = std::atoi(optarg); break;
      case 'x': config.playback_speed = std::atof(optarg); break;
      case 'd': config.data_dir = optarg; break;
      case 'p': config.prefix = optarg; break;
      case 0: {
        std::string name = cli_options[option_index].name;
        if (name == "demo") config.route = DEMO_ROUTE;
        else if (name == "auto") config.auto_source = true;
        else if (name == "machine") config.machine = true;
        else if (name == "start-segment") {
          if (!std::regex_match(optarg, std::regex("[0-9]{1,6}"))) return false;
          config.start_segment = std::atoi(optarg);
        }
        else config.flags |= flag_map.at(name);
        break;
      }
      case 'h': std::cout << helpText; return false;
      default: return false;
    }
  }

  // Check for a route name (first positional argument)
  if (config.route.empty() && optind < argc) {
    config.route = argv[optind];
  }

  if (config.route.empty()) {
    std::cerr << "No route provided. Use --help for usage information.\n";
    return false;
  }
  if (config.start_segment >= 0 && !config.machine) return false;
  if ((config.flags & REPLAY_FLAG_PRELOAD_SEEK) && !config.machine) return false;

  return true;
}

bool privateDirectory(const char *path) {
  if (!path || path[0] != '/') return false;
  struct stat info = {};
  if (lstat(path, &info) || !S_ISDIR(info.st_mode) || info.st_uid != getuid() || (info.st_mode & 07777) != 0700) return false;
  std::error_code error;
  const auto resolved = std::filesystem::canonical(path, error).string();
  if (error) return false;
  return resolved != "/data/params" && resolved.rfind("/data/params/", 0) != 0;
}

bool machineContext(const ReplayConfig &config) {
  const std::string prefix = util::getenv("OPENPILOT_PREFIX", "");
  const std::string clock = util::getenv("SP_REPLAY_CLOCK_PATH", "");
  return util::getenv("SP_REPLAY_CONTEXT", "") == "1" &&
         std::regex_match(prefix, std::regex("replay-[A-Za-z0-9_-]{1,48}")) &&
         privateDirectory(getenv("PARAMS_ROOT")) && privateDirectory(getenv("COMMA_CACHE")) &&
         clock == "/dev/shm/msgq_" + prefix + "/display-clock" &&
         !config.data_dir.empty() && config.data_dir.front() == '/' && config.prefix.empty() &&
         !config.auto_source && config.allow.empty() && config.block.empty() &&
         !(config.flags & (REPLAY_FLAG_ALL_SERVICES | REPLAY_FLAG_BENCHMARK));
}

static std::atomic<bool> machine_exit = false;

int runMachine(Replay &replay, int output, int start_segment) {
  auto interrupted = [](int) { machine_exit = true; };
  std::signal(SIGTERM, interrupted);
  std::signal(SIGINT, interrupted);
  std::signal(SIGHUP, interrupted);
  replay.setLoadAbort(&machine_exit);
  std::signal(SIGPIPE, SIG_IGN);
  fcntl(output, F_SETFL, fcntl(output, F_GETFL) | O_NONBLOCK);
  std::string input, last_request, pending_request, command_error;
  uint64_t pending_epoch = 0;
  auto report = [&]() {
    json11::Json::array ranges, segment_ranges;
    for (const auto &range : replay.seekRanges()) {
      ranges.emplace_back(json11::Json::array{range.begin, range.end});
      segment_ranges.emplace_back(json11::Json::array{range.segment, range.begin, range.end});
    }
    json11::Json::array warnings;
    if (replay.legacyClock()) warnings.emplace_back("Legacy recording uses original timestamps; exact camera/HUD clock alignment is unavailable.");
    if (replay.timelineIncomplete()) warnings.emplace_back("Timeline history is incomplete; unavailable evidence remains neutral.");
    const auto value = json11::Json::object{
      {"event", "status"}, {"state", replay.state()}, {"position", replay.currentSeconds()},
      {"start", replay.minSeconds()}, {"end", replay.maxSeconds()}, {"rate", replay.getSpeed()},
      {"paused", replay.isPaused()}, {"epoch", double(replay.epoch())},
      {"routeStartNs", double(replay.routeStartNanos())}, {"wallMinusMonoNs", std::to_string(replay.wallMinusMonoNanos())},
      {"seekRanges", ranges}, {"segmentRanges", segment_ranges}, {"sourceWarnings", warnings}, {"lastRequestId", last_request},
      {"timelineState", replay.timelineState()},
      {"error", command_error.empty() ? replay.error() : command_error}};
    const auto line = json11::Json(value).dump() + "\n";
    return write(output, line.data(), line.size()) == ssize_t(line.size());
  };
  if (!report() || !replay.load()) { report(); return 2; }
  if (start_segment < 0) {
    replay.start();
  } else {
    const auto &ranges = replay.seekRanges();
    auto selected = std::find_if(ranges.begin(), ranges.end(), [start_segment](const auto &range) { return range.segment == start_segment; });
    if (selected == ranges.end()) {
      command_error = "Selected segment has no playable footage";
      report();
      return 2;
    }
    replay.seekTo(selected->begin, false);
  }
  while (!machine_exit) {
    if (!pending_request.empty() && replay.appliedEpoch() >= pending_epoch) {
      last_request = std::move(pending_request);
      pending_request.clear();
    }
    if (!report()) break;
    if (replay.state() == "failed") return 2;
    pollfd source = {.fd = STDIN_FILENO, .events = POLLIN};
    const int ready = poll(&source, 1, 50);
    if (ready < 0) { if (errno == EINTR) continue; break; }
    if (!ready) continue;
    if (source.revents & (POLLERR | POLLNVAL)) break;
    char chunk[4096];
    const auto count = read(STDIN_FILENO, chunk, sizeof(chunk));
    if (count <= 0) break;
    input.append(chunk, count);
    if (input.size() > 4096) { std::cerr << "Machine command exceeds limit\n"; return 2; }
    size_t newline;
    while ((newline = input.find('\n')) != std::string::npos) {
      std::string error;
      auto command = json11::Json::parse(input.substr(0, newline), error);
      input.erase(0, newline + 1);
      const auto op = command["op"].string_value(), request = command["requestId"].string_value();
      if (!error.empty() || !command.is_object() || !std::regex_match(request, std::regex("[a-fA-F0-9]{1,64}"))) return 2;
      const size_t expected = op == "stop" ? 2 : 3;
      if (command.object_items().size() != expected) return 2;
      command_error.clear();
      if (op == "stop") {
        replay.stop();
        replay.pause(true);
        last_request = request;
        report();
        return 0;
      } else if (op == "pause" && command["paused"].is_bool()) {
        replay.pause(command["paused"].bool_value());
        last_request = request;
      } else if (op == "rate" && command["rate"].is_number() &&
                 (command["rate"].number_value() == .5 || command["rate"].number_value() == 1 || command["rate"].number_value() == 2)) {
        replay.setSpeed(command["rate"].number_value());
        last_request = request;
      } else if (op == "seek" && command["position"].is_number()) {
        if (replay.canSeek(command["position"].number_value())) {
          replay.seekTo(command["position"].number_value(), false);
          pending_request = request;
          pending_epoch = replay.epoch();
        } else {
          command_error = "invalid_position";
          last_request = request;
        }
      } else return 2;
    }
  }
  replay.stop();
  return 0;
}

int main(int argc, char *argv[]) {
#ifdef __APPLE__
  // With all sockets opened, we might hit the default limit of 256 on macOS
  util::set_file_descriptor_limit(1024);
#endif

  // The vendored ncurses static library has a wrong compiled-in terminfo path.
  // Point it at the system terminfo database if not already set.
  setenv("TERMINFO_DIRS", "/usr/share/terminfo:/lib/terminfo:/usr/lib/terminfo", 0);

  ReplayConfig config;

  if (!parseArgs(argc, argv, config)) {
    return 1;
  }

  int machine_output = -1;
  if (config.machine) {
    machine_output = dup(STDOUT_FILENO);
    dup2(STDERR_FILENO, STDOUT_FILENO);  // Native libraries may print diagnostics to stdout.
    if (!machineContext(config)) {
      std::cerr << "Machine replay requires an owned private local replay context\n";
      return 2;
    }
#ifdef __linux__
    const auto parent = util::getenv("SP_REPLAY_PARENT_PID", "");
    if (parent != std::to_string(getppid()) || prctl(PR_SET_PDEATHSIG, SIGKILL) || parent != std::to_string(getppid())) {
      std::cerr << "Machine replay parent is unavailable\n";
      return 2;
    }
#else
    return 2;
#endif
    config.flags |= REPLAY_FLAG_LOCAL_MACHINE | REPLAY_FLAG_NO_LOOP | REPLAY_FLAG_NO_FILE_CACHE;
    config.allow = {"modelV2", "controlsState", "lateralDelay", "lateralTorqueParameters", "onroadEvents", "extrinsicsCalibration",
                    "radarState", "deviceState", "pandaStates", "carParams", "driverMonitoringState", "carState", "driverStateV2",
                    "narrowRoadCameraState", "wideRoadCameraState", "cabinCameraState", "managerState", "selfdriveState", "starpilotSelfdriveState",
                    "starpilotLateralState", "starpilotNavigation", "longitudinalPlan", "gpsLocationExternal", "carOutput", "carControl", "aolAxisState", "aolIntentWire",
                    "slcState", "vehicleParameters", "customReserved6", "spotMonitorState", "clocks", "narrowRoadEncodeIdx", "wideRoadEncodeIdx", "cabinEncodeIdx"};
    try {
      Replay replay(config.route, config.allow, {}, nullptr, config.flags, config.data_dir, false);
      if (config.cache_segments > 0) replay.setSegmentCacheLimit(config.cache_segments);
      return runMachine(replay, machine_output, config.start_segment);
    } catch (const std::exception &error) {
      std::cerr << "Machine replay failed: " << error.what() << '\n';
      return 2;
    }
  }

  std::unique_ptr<OpenpilotPrefix> op_prefix;
  if (!config.prefix.empty()) {
    op_prefix = std::make_unique<OpenpilotPrefix>(config.prefix);
  }

  Replay replay(config.route, config.allow, config.block, nullptr, config.flags, config.data_dir, config.auto_source);
  if (config.cache_segments > 0) {
    replay.setSegmentCacheLimit(config.cache_segments);
  }
  if (config.playback_speed > 0) {
    replay.setSpeed(std::clamp(config.playback_speed, ConsoleUI::speed_array.front(), ConsoleUI::speed_array.back()));
  }
  if (!replay.load()) {
    return 1;
  }

  if (config.flags & REPLAY_FLAG_BENCHMARK) {
    replay.start(config.start_seconds);
    replay.waitForFinished();

    const auto &stats = replay.getBenchmarkStats();
    uint64_t process_start = stats.process_start_ts;

    std::cout << "\n===== REPLAY BENCHMARK RESULTS =====\n";
    std::cout << "Route: " << replay.route().name() << "\n\n";

    std::cout << "TIMELINE:\n";
    std::cout << "  t=0 ms        process start\n";
    for (const auto &[ts, event] : stats.timeline) {
      double ms = (ts - process_start) / 1e6;
      std::cout << "  t=" << std::fixed << std::setprecision(0) << ms << " ms"
                << std::string(std::max(1, 8 - static_cast<int>(std::to_string(static_cast<int>(ms)).length())), ' ')
                << event << "\n";
    }

    return 0;
  }

  ConsoleUI console_ui(&replay);
  replay.start(config.start_seconds);
  return console_ui.exec();
}
