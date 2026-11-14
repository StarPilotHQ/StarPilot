#pragma once

#include <cassert>
#include <cerrno>
#include <fcntl.h>
#include <unistd.h>
#include <memory>
#include <map>
#include <string>

#include "openpilot/cereal/messaging/messaging.h"
#include "common/util.h"
#include "common/hardware/hw.h"
#include "system/loggerd/zstd_writer.h"

// Persist ownership before any recording bytes can become visible.
inline bool logger_write_cloud_marker(const std::string &path, const std::string &provider) {
  if (provider != "comma" && provider != "konik" && provider != "custom" && provider != "offline") return false;
  const size_t slash = path.find_last_of('/');
  if (slash == std::string::npos) return false;
  const int directory = open(path.substr(0, slash).c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (directory < 0) return false;
  const int marker = open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
  if (marker < 0) {
    close(directory);
    return false;
  }
  size_t offset = 0;
  bool ok = true;
  while (offset < provider.size()) {
    const ssize_t written = write(marker, provider.data() + offset, provider.size() - offset);
    if (written < 0 && errno == EINTR) continue;
    if (written <= 0) {
      ok = false;
      break;
    }
    offset += static_cast<size_t>(written);
  }
  if (ok) ok = fsync(marker) == 0;
  if (close(marker) != 0) ok = false;
  if (ok) ok = fsync(directory) == 0;
  if (close(directory) != 0) ok = false;
  return ok;
}

constexpr int LOG_COMPRESSION_LEVEL = 10;

typedef cereal::Sentinel::SentinelType SentinelType;

class LoggerState {
public:
  LoggerState(const std::string& log_root = Path::log_root());
  ~LoggerState();
  bool next();
  void write(uint8_t* data, size_t size, bool in_qlog);
  inline int segment() const { return part; }
  inline const std::string& segmentPath() const { return segment_path; }
  inline const std::string& routeName() const { return route_name; }
  inline void write(kj::ArrayPtr<kj::byte> bytes, bool in_qlog) { write(bytes.begin(), bytes.size(), in_qlog); }
  inline void setExitSignal(int signal) { exit_signal = signal; }

protected:
  int part = -1, exit_signal = 0;
  std::string route_path, route_name, segment_path, lock_file;
  kj::Array<capnp::word> init_data;
  std::unique_ptr<ZstdFileWriter> rlog, qlog;
};

kj::Array<capnp::word> logger_build_init_data(bool route_log = false);
kj::Array<capnp::word> logger_model_init_data(kj::ArrayPtr<const capnp::word> original,
                                           const std::map<std::string, std::string> &metadata);
std::string logger_get_identifier(std::string key);
std::string zstd_decompress(const std::string &in);
