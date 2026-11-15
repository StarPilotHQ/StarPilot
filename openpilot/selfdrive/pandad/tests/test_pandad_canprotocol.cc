#include <climits>
#include <array>
#include <filesystem>
#include <fstream>
#include <cstdlib>

#include "common/tests/native_test.h"
#include "openpilot/cereal/messaging/messaging.h"
#include "selfdrive/pandad/panda.h"

struct PandaTest : public Panda {
  PandaTest(int can_list_size, cereal::PandaState::PandaType hw_type, uint32_t offset=0);
  void test_can_send();
  void test_can_recv(uint32_t chunk_size = 0);
  void test_chunked_can_recv();

  std::map<int, std::string> test_data;
  int can_list_size = 0;
  int total_pakets_size = 0;
  MessageBuilder msg;
  capnp::List<cereal::CanData>::Reader can_data_list;
};

PandaTest::PandaTest(int can_list_size_, cereal::PandaState::PandaType hw_type_, uint32_t offset) : can_list_size(can_list_size_), Panda(offset) {
  this->hw_type = hw_type_;
  int data_limit = ((hw_type == cereal::PandaState::PandaType::RED_PANDA) ? std::size(dlc_to_len) : 9);
  // prepare test data
  for (int i = 0; i < data_limit; ++i) {
    int data_len = dlc_to_len[i];
    std::string bytes(data_len, '\0');
    for (int j = 0; j < data_len; ++j) bytes[j] = static_cast<char>((i * 31 + j) & 0xff);
    test_data[data_len] = bytes;
  }

  // generate can messages for this panda
  auto can_list = msg.initEvent().initSendcan(can_list_size);
  for (uint8_t i = 0; i < can_list_size; ++i) {
    auto can = can_list[i];
    uint32_t id = i % data_limit;
    const std::string &dat = test_data[dlc_to_len[id]];
    can.setAddress(i);
    can.setSrc(i % 3 + bus_offset);
    can.setDat(kj::ArrayPtr((uint8_t *)dat.data(), dat.size()));
    total_pakets_size += sizeof(can_header) + dat.size();
  }

  can_data_list = can_list.asReader();
}

void PandaTest::test_can_send() {
  std::vector<uint8_t> unpacked_data;
  this->pack_can_buffer(can_data_list, [&](uint8_t *chunk, size_t size) {
    CHECK(size > 0 && size < USB_TX_SOFT_LIMIT + sizeof(can_header) + 64);
    unpacked_data.insert(unpacked_data.end(), chunk, &chunk[size]);
  });
  CHECK(unpacked_data.size() == total_pakets_size);

  int cnt = 0;
  for (int pos = 0, pckt_len = 0; pos < unpacked_data.size(); pos += pckt_len) {
    can_header header;
    memcpy(&header, &unpacked_data[pos], sizeof(can_header));
    const uint8_t data_len = dlc_to_len[header.data_len_code];
    pckt_len = sizeof(can_header) + data_len;

    CHECK(header.addr == cnt);
    CHECK(header.bus == cnt % 3);
    CHECK(!header.rejected && !header.returned && !header.extended);
    CHECK(calculate_checksum(&unpacked_data[pos], pckt_len) == 0);
    CHECK(test_data.find(data_len) != test_data.end());
    const std::string &dat = test_data[data_len];
    CHECK(memcmp(dat.data(), &unpacked_data[pos + sizeof(can_header)], dat.size()) == 0);
    ++cnt;
  }
  CHECK(cnt == can_list_size);
}

void PandaTest::test_can_recv(uint32_t rx_chunk_size) {
  std::vector<can_frame> frames;
  this->pack_can_buffer(can_data_list, [&](uint8_t *data, uint32_t size) {
    if (rx_chunk_size == 0) {
      CHECK(this->unpack_can_buffer(data, size, frames));
    } else {
      this->receive_buffer_size = 0;
      uint32_t pos = 0;

      while (pos < size) {
        uint32_t chunk_size = std::min(rx_chunk_size, size - pos);
        memcpy(&this->receive_buffer[this->receive_buffer_size], &data[pos], chunk_size);
        this->receive_buffer_size += chunk_size;
        pos += chunk_size;

        CHECK(this->unpack_can_buffer(this->receive_buffer, this->receive_buffer_size, frames));
      }
    }
  });

  CHECK(frames.size() == can_list_size);
  for (int i = 0; i < frames.size(); ++i) {
    CHECK(frames[i].address == i);
    CHECK(frames[i].src == i % 3 + bus_offset);
    CHECK(test_data.find(frames[i].dat.size()) != test_data.end());
    const std::string &dat = test_data[frames[i].dat.size()];
    CHECK(memcmp(dat.data(), frames[i].dat.data(), dat.size()) == 0);
  }
}

class FirmwareHandle : public PandaCommsHandle {
public:
  explicit FirmwareHandle(unsigned char value) : value(value) { hw_serial = std::string(24, 'a'); }
  int control_read(uint8_t request, uint16_t, uint16_t, unsigned char *data, uint16_t length, unsigned int) override {
    if ((request != 0xd3 && request != 0xd4) || length != 64) return -1;
    memset(data, value, length);
    return length;
  }
  int control_write(uint8_t, uint16_t, uint16_t, unsigned int) override { return 0; }
  int bulk_write(unsigned char, unsigned char *, int, unsigned int) override { return 0; }
  int bulk_read(unsigned char, unsigned char *, int, unsigned int) override { return 0; }
  void cleanup() override {}
private:
  unsigned char value;
};

class FirmwarePanda : public Panda {
public:
  FirmwarePanda(cereal::PandaState::PandaType type, unsigned char signature)
    : Panda(std::make_unique<FirmwareHandle>(signature), type) {}
};

void test_firmware_selection() {
  namespace fs = std::filesystem;
  char directory[] = "/tmp/pandad-firmware-XXXXXX";
  CHECK(mkdtemp(directory) != nullptr);
  struct Restore {
    fs::path cwd = fs::current_path();
    fs::path directory;
    std::vector<std::pair<std::string, std::optional<std::string>>> environment;
    ~Restore() {
      for (const auto &[key, value] : environment) {
        if (value) setenv(key.c_str(), value->c_str(), 1);
        else unsetenv(key.c_str());
      }
      fs::current_path(cwd);
      fs::remove_all(directory);
    }
  } restore{fs::current_path(), directory, {}};
  const std::string key = "STARPILOT_PANDA_FIRMWARE_" + std::string(24, 'a');
  for (const auto &name : {std::string("STARPILOT_PANDA_FIRMWARE_MAP"), std::string("STARPILOT_PANDA_FIRMWARE_NAME"), key}) {
    const char *old = getenv(name.c_str());
    restore.environment.emplace_back(name, old ? std::make_optional(std::string(old)) : std::nullopt);
    unsetenv(name.c_str());
  }
  fs::create_directories(fs::path(directory) / "panda/board/obj");
  fs::create_directories(fs::path(directory) / "work/a/b");
  for (const auto &[filename, value] : std::vector<std::pair<std::string, unsigned char>>{
         {"panda.bin.signed", 0x11}, {"panda_h7.bin.signed", 0x22}}) {
    std::ofstream file(fs::path(directory) / "panda/board/obj" / filename, std::ios::binary);
    std::string content(256, value);
    file.write(content.data(), content.size());
  }
  fs::current_path(fs::path(directory) / "work/a/b");
  using Type = cereal::PandaState::PandaType;
  CHECK(FirmwarePanda(Type::DOS, 0x11).up_to_date());
  CHECK(!FirmwarePanda(Type::DOS, 0x22).up_to_date());
  for (auto type : {Type::RED_PANDA, Type::RED_PANDA_V2, Type::TRES, Type::CUATRO}) {
    CHECK(FirmwarePanda(type, 0x22).up_to_date());
    CHECK(!FirmwarePanda(type, 0x11).up_to_date());
  }
  CHECK(!FirmwarePanda(Type::UNKNOWN, 0x11).up_to_date());
  CHECK(!FirmwarePanda(Type::UNKNOWN, 0x22).up_to_date());
  setenv("STARPILOT_PANDA_FIRMWARE_MAP", "1", 1);
  for (auto type : {Type::DOS, Type::RED_PANDA, Type::TRES, Type::CUATRO}) {
    const std::string prefix = type == Type::DOS ? "panda" : "panda_h7";
    for (const char *suffix : {"_remote", "_hkg_remote", "_tesla_wake", "_can_ignition_only",
                               "_remote_can_ignition_only", "_hkg_remote_can_ignition_only", "_tesla_wake_can_ignition_only"}) {
      const std::string name = prefix + suffix + ".bin.signed";
      const auto path = fs::path(directory) / "panda/board/obj" / name;
      { std::ofstream file(path, std::ios::binary); file << std::string(256, 0x33); }
      setenv(key.c_str(), name.c_str(), 1);
      CHECK(FirmwarePanda(type, 0x33).up_to_date());
      CHECK(!FirmwarePanda(type, 0x22).up_to_date());
      CHECK(!FirmwarePanda(Type::UNKNOWN, 0x33).up_to_date());
      setenv("STARPILOT_PANDA_FIRMWARE_NAME", name.c_str(), 1);
      CHECK(!FirmwarePanda(type, 0x33).up_to_date());
      unsetenv("STARPILOT_PANDA_FIRMWARE_NAME");
      fs::remove(path);
      CHECK(!FirmwarePanda(type, 0x33).up_to_date());
    }
    setenv(key.c_str(), "../panda_h7.bin.signed", 1);
    CHECK(!FirmwarePanda(type, 0x22).up_to_date());
    unsetenv(key.c_str());
    CHECK(!FirmwarePanda(type, 0x22).up_to_date());
  }
  unsetenv("STARPILOT_PANDA_FIRMWARE_MAP");
  fs::remove(fs::path(directory) / "panda/board/obj/panda.bin.signed");
  CHECK(!FirmwarePanda(Type::DOS, 0x11).up_to_date());
}


class RoutingPanda : public Panda {
public:
  explicit RoutingPanda(uint32_t offset) : Panda(offset) {}
  using Panda::pack_can_buffer;
  using Panda::unpack_can_buffer;
  using Panda::calculate_checksum;
};

void test_disjoint_bus_blocks_and_return_flags() {
  MessageBuilder message;
  auto list = message.initEvent().initSendcan(12);
  for (uint32_t bus = 0; bus < 12; ++bus) {
    list[bus].setAddress(0x100 + bus);
    list[bus].setSrc(bus);
    const std::array<uint8_t, 8> data{(uint8_t)bus, 1, 2, 3, 4, 5, 6, 7};
    list[bus].setDat(kj::arrayPtr(data.data(), data.size()));
  }
  for (uint32_t offset : {0U, 4U, 8U}) {
    RoutingPanda panda(offset);
    std::vector<uint8_t> bytes;
    panda.pack_can_buffer(list.asReader(), [&](uint8_t *data, size_t size) { bytes.insert(bytes.end(), data, data + size); });
    const size_t packet_size = sizeof(can_header) + 8;
    CHECK(bytes.size() == 4 * packet_size);
    for (uint32_t local = 0; local < 4; ++local) {
      can_header header;
      memcpy(&header, bytes.data() + local * packet_size, sizeof(header));
      CHECK(header.bus == local && header.addr == 0x100 + offset + local);
    }
    for (uint32_t flags : {0U, CAN_RETURNED_BUS_OFFSET, CAN_REJECTED_BUS_OFFSET}) {
      auto encoded = bytes;
      for (uint32_t local = 0; local < 4; ++local) {
        can_header header;
        auto *packet = encoded.data() + local * packet_size;
        memcpy(&header, packet, sizeof(header));
        header.returned = flags == CAN_RETURNED_BUS_OFFSET;
        header.rejected = flags == CAN_REJECTED_BUS_OFFSET;
        header.checksum = 0;
        memcpy(packet, &header, sizeof(header));
        header.checksum = panda.calculate_checksum(packet, packet_size);
        memcpy(packet, &header, sizeof(header));
      }
      uint32_t size = encoded.size(); std::vector<can_frame> frames;
      CHECK(panda.unpack_can_buffer(encoded.data(), size, frames));
      CHECK(frames.size() == 4);
      for (uint32_t local = 0; local < 4; ++local) {
        CHECK(frames[local].src == offset + local + flags);
        CHECK(frames[local].address == 0x100 + offset + local);
        CHECK((uint8_t)frames[local].dat[0] == offset + local);
      }
    }
  }
}

void test_can_protocol() {
  test_disjoint_bus_blocks_and_return_flags();
  test_firmware_selection();
  for (auto hw_type : {cereal::PandaState::PandaType::DOS, cereal::PandaState::PandaType::RED_PANDA}) {
    for (uint32_t offset : {0U, 4U, 8U}) {
    for (int can_list_size : {1, 3, 5, 9, 10, 18, 19, 20, 30, 60, 100, 200}) {
      PandaTest send_test(can_list_size, hw_type, offset);
      send_test.test_can_send();

      PandaTest receive_test(can_list_size, hw_type, offset);
      receive_test.test_can_recv();

      PandaTest chunked_receive_test(can_list_size, hw_type, offset);
      for (uint32_t chunk_size : {1U, 63U, 64U, 65U}) chunked_receive_test.test_can_recv(chunk_size);
    }
  }
}

}

int main() {
  return run_native_test(test_can_protocol);
}
