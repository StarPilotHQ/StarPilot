"""Compile the production retry loop with scripted transfer results and a clock."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def retry_binary(tmp_path_factory):
  source = (Path(__file__).resolve().parents[1] / "spi.cc").read_text()
  start = source.index("int PandaSpiHandle::spi_transfer_retry(")
  end = source.index("\nint PandaSpiHandle::lltransfer(", start)
  method = source[start:end]
  directory = tmp_path_factory.mktemp("spi-retry")
  cpp = directory / "retry.cc"
  cpp.write_text(r'''
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

enum SpiError { NACK = -2, ACK_TIMEOUT = -3, SILENT = -4 };
const unsigned int SPI_MIN_TIMEOUT = 100;
double now_ms = 0;
double millis_since_boot() { return now_ms; }
uint32_t random_id() { static uint32_t next = 0; return ++next; }
void fake_usleep(unsigned int us) { now_ms += us / 1000.0; }
#define usleep fake_usleep
#define SPILOG(...) ((void)0)
#define LOGW(...) ((void)0)

struct Reply { int result; double delay_ms; bool disconnect = false; };
class PandaSpiHandle {
public:
  bool connected = true;
  std::vector<Reply> replies;
  std::vector<uint32_t> ids;
  std::vector<double> deadlines;
  int spi_transfer(uint8_t, uint32_t id, uint8_t *, uint16_t,
                   uint8_t *, uint16_t, double deadline) {
    ids.push_back(id);
    deadlines.push_back(deadline);
    if (ids.size() > replies.size()) {
      std::cerr << "Unexpected extra retry\n";
      std::exit(2);
    }
    const auto &reply = replies[ids.size() - 1];
    now_ms += reply.delay_ms;
    connected = !reply.disconnect;
    return now_ms > deadline ? SpiError::ACK_TIMEOUT : reply.result;
  }
  int spi_transfer_retry(uint8_t, uint8_t *, uint16_t, uint8_t *, uint16_t, unsigned int);
};

@METHOD@

int main(int argc, char **argv) {
  if (argc != 2) return 2;
  const std::string name = argv[1];
  PandaSpiHandle handle;
  unsigned int timeout = 5;
  int expected = 0;
  if (name == "late_ack_after_busy") {
    handle.replies = {{SpiError::NACK, 80}, {0, 80}};
  } else if (name == "longer_timeout") {
    timeout = 500;
    handle.replies = {{SpiError::NACK, 400}, {0, 400}};
  } else if (name == "normal_reply") {
    handle.replies = {{12, 10}};
    expected = 12;
  } else if (name == "repeated_busy") {
    handle.replies.assign(8, {SpiError::NACK, 50});
    handle.replies.push_back({0, 50});
  } else if (name == "missing_reply") {
    handle.replies.assign(3, {SpiError::ACK_TIMEOUT, 50});
    expected = SpiError::ACK_TIMEOUT;
  } else if (name == "invalid_reply_after_busy") {
    handle.replies = {{SpiError::NACK, 80}, {SpiError::ACK_TIMEOUT, 80}, {SpiError::ACK_TIMEOUT, 80}};
    expected = SpiError::ACK_TIMEOUT;
  } else if (name == "retry_same_request") {
    handle.replies = {{SpiError::ACK_TIMEOUT, 40}, {0, 40}};
  } else if (name == "zero_timeout") {
    timeout = 0;
    handle.replies = {{SpiError::NACK, 1000}, {SpiError::NACK, 1000}, {0, 1000}};
  } else if (name == "disconnect") {
    handle.replies = {{SpiError::ACK_TIMEOUT, 1, true}};
    expected = SpiError::ACK_TIMEOUT;
  } else {
    return 2;
  }
  const int result = handle.spi_transfer_retry(3, nullptr, 0, nullptr, 0, timeout);
  if (result != expected || handle.ids.size() != handle.replies.size()) {
    std::cerr << name << ": result " << result << ", expected " << expected
              << ", attempts " << handle.ids.size() << "\n";
    return 1;
  }
  if (name == "late_ack_after_busy" && handle.ids[0] == handle.ids[1]) return 1;
  if (name == "retry_same_request" && handle.ids[0] != handle.ids[1]) return 1;
  if (name == "invalid_reply_after_busy" && handle.ids[1] != handle.ids[2]) return 1;
  if (name == "zero_timeout" && !std::isinf(handle.deadlines.back())) return 1;
  return 0;
}
'''.replace("@METHOD@", method))
  compiler = shutil.which("clang++") or shutil.which("g++")
  assert compiler, "SPI retry regression requires the repository C++ toolchain"
  binary = directory / "retry"
  subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(binary)], check=True)
  return binary


@pytest.mark.parametrize("case", [
  "late_ack_after_busy", "repeated_busy", "missing_reply", "invalid_reply_after_busy",
  "retry_same_request", "zero_timeout", "disconnect", "longer_timeout", "normal_reply",
])
def test_spi_retry_reply_deadline(retry_binary, case):
  subprocess.run([str(retry_binary), case], check=True, timeout=5)
