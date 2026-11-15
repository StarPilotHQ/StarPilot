#include "selfdrive/pandad/panda.h"
#include "selfdrive/pandad/aol_protocol.h"

#include <unistd.h>

#include <algorithm>
#include <cassert>
#include <stdexcept>
#include <vector>

#include "openpilot/cereal/messaging/messaging.h"
#include "common/hardware/hw.h"
#include "common/swaglog.h"
#include "common/util.h"

const bool PANDAD_MAXOUT = getenv("PANDAD_MAXOUT") != nullptr;

Panda::Panda(std::string serial, uint32_t bus_offset) : bus_offset(bus_offset) {
  handle = open_panda_handle(serial, [](const std::string &requested) {
    auto spi = std::make_unique<PandaSpiHandle>(requested);
    LOGW("connected to %s over SPI", requested.c_str());
    return spi;
  }, [](const std::string &requested) {
    auto usb = std::make_unique<PandaUsbHandle>(requested);
    LOGW("connected to %s over USB", requested.c_str());
    return usb;
  });

  hw_type = get_hw_type();
  can_reset_communications();
}

bool Panda::connected() {
  return handle->connected;
}

bool Panda::comms_healthy() {
  return handle->comms_healthy;
}

std::string Panda::hw_serial() {
  return handle->hw_serial;
}

std::vector<std::string> Panda::list() {
  auto serials = PandaUsbHandle::list();
  // C3's internal Panda uses USB; its SPI device node has no Panda.
  if (Hardware::get_device_type() == cereal::InitData::DeviceType::TICI) return serials;
  for (const auto &serial : PandaSpiHandle::list()) {
    if (std::find(serials.begin(), serials.end(), serial) == serials.end()) serials.push_back(serial);
  }
  return serials;
}

void Panda::set_safety_model(cereal::CarParams::SafetyModel safety_model, uint16_t safety_param) {
  handle->control_write(0xdc, (uint16_t)safety_model, safety_param);
}

void Panda::set_alternative_experience(uint16_t alternative_experience) {
  handle->control_write(0xdf, alternative_experience, 0);
}

std::string Panda::serial_read(int port_number) {
  std::string ret;
  char buffer[USBPACKET_MAX_SIZE] = {};

  // Firmware exposes one debug ring; bound each drain so CAN work keeps its cadence.
  (void)port_number;
  constexpr size_t max_debug_bytes = 1024;
  while (ret.size() < max_debug_bytes) {
    const uint16_t length = std::min<size_t>(USBPACKET_MAX_SIZE, max_debug_bytes - ret.size());
    int bytes_read = handle->control_read(0xb6, 0, 0, (unsigned char *)buffer, length);
    if (bytes_read <= 0 || bytes_read > length) {
      break;
    }
    ret.append(buffer, bytes_read);
  }

  return ret;
}

void Panda::set_uart_baud(int uart, int rate) {
  handle->control_write(0xe4, uart, int(rate / 300));
}

cereal::PandaState::PandaType Panda::get_hw_type() {
  unsigned char hw_query[1] = {0};

  handle->control_read(0xc1, 0, 0, hw_query, 1);
  return (cereal::PandaState::PandaType)(hw_query[0]);
}

void Panda::set_fan_speed(uint16_t fan_speed) {
  handle->control_write(0xb1, fan_speed, 0);
}

uint16_t Panda::get_fan_speed() {
  uint16_t fan_speed_rpm = 0;
  handle->control_read(0xb2, 0, 0, (unsigned char*)&fan_speed_rpm, sizeof(fan_speed_rpm));
  return fan_speed_rpm;
}

void Panda::set_ir_pwr(uint16_t ir_pwr) {
  handle->control_write(0xb0, ir_pwr, 0);
}

std::optional<health_t> Panda::get_state() {
  health_t health {0};
  int err = handle->control_read(0xd2, 0, 0, (unsigned char*)&health, sizeof(health));
  return err == static_cast<int>(sizeof(health)) ? std::make_optional(health) : std::nullopt;
}

std::optional<aol_safety_health_t> Panda::get_aol_safety_state() {
  aol_safety_health_t status {0};
  int count = handle->control_read(0xd5, 0, 0, (unsigned char*)&status, sizeof(status));
  return parse_aol_status(reinterpret_cast<const unsigned char *>(&status), count);
}

std::optional<can_health_t> Panda::get_can_state(uint16_t can_number) {
  can_health_t can_health {0};
  int err = handle->control_read(0xc2, can_number, 0, (unsigned char*)&can_health, sizeof(can_health));
  return err == static_cast<int>(sizeof(can_health)) ? std::make_optional(can_health) : std::nullopt;
}

void Panda::set_loopback(bool loopback) {
  handle->control_write(0xe5, loopback, 0);
}

std::optional<std::vector<uint8_t>> Panda::get_firmware_version() {
  std::vector<uint8_t> fw_sig_buf(128);
  int read_1 = handle->control_read(0xd3, 0, 0, &fw_sig_buf[0], 64);
  int read_2 = handle->control_read(0xd4, 0, 0, &fw_sig_buf[64], 64);
  return ((read_1 == 64) && (read_2 == 64)) ? std::make_optional(fw_sig_buf) : std::nullopt;
}

std::optional<std::string> Panda::get_serial() {
  char serial_buf[17] = {'\0'};
  int err = handle->control_read(0xd0, 0, 0, (uint8_t*)serial_buf, 16);
  return err >= 0 ? std::make_optional(serial_buf) : std::nullopt;
}

bool Panda::up_to_date() {
  const char *filename = nullptr;
  switch (hw_type) {
    case cereal::PandaState::PandaType::DOS:
      filename = "panda.bin.signed";
      break;
    case cereal::PandaState::PandaType::RED_PANDA:
    case cereal::PandaState::PandaType::RED_PANDA_V2:
    case cereal::PandaState::PandaType::TRES:
    case cereal::PandaState::PandaType::CUATRO:
      filename = "panda_h7.bin.signed";
      break;
    default:
      return false;
  }
  // The wrapper's selection is per hardware serial; mixed MCUs cannot share an image name.
  if (getenv("STARPILOT_PANDA_FIRMWARE_NAME")) return false;
  if (const char *managed = getenv("STARPILOT_PANDA_FIRMWARE_MAP")) {
    if (std::string(managed) != "1") return false;
    const std::string serial = hw_serial();
    if (serial.size() != 24 || serial.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos) return false;
    const std::string key = "STARPILOT_PANDA_FIRMWARE_" + serial;
    const char *selected = getenv(key.c_str());
    if (!selected) return false;
    bool allowed = std::string(selected) == filename;
    // Match the wrapper's existing alternate-image hardware eligibility exactly.
    if (hw_type == cereal::PandaState::PandaType::DOS || hw_type == cereal::PandaState::PandaType::TRES ||
        hw_type == cereal::PandaState::PandaType::RED_PANDA) {
      const std::string prefix = std::string(filename).substr(0, std::string(filename).find(".bin.signed"));
      for (const char *suffix : {"_remote", "_hkg_remote", "_tesla_wake", "_can_ignition_only",
                                 "_remote_can_ignition_only", "_hkg_remote_can_ignition_only", "_tesla_wake_can_ignition_only"}) {
        allowed |= std::string(selected) == prefix + suffix + ".bin.signed";
      }
    }
    // CUATRO is qualified only for the Tesla wake image, not other alternates.
    if (hw_type == cereal::PandaState::PandaType::CUATRO) {
      allowed |= std::string(selected) == "panda_h7_tesla_wake.bin.signed";
    }
    if (!allowed) return false;
    filename = selected;
  }
  if (auto fw_sig = get_firmware_version()) {
    auto content = util::read_file(std::string("../../../panda/board/obj/") + filename);
    return content.size() >= fw_sig->size() &&
      memcmp(content.data() + content.size() - fw_sig->size(), fw_sig->data(), fw_sig->size()) == 0;
  }
  return false;
}

void Panda::set_power_saving(bool power_saving) {
  handle->control_write(0xe7, power_saving, 0);
}

void Panda::enable_deepsleep() {
  handle->control_write(0xfb, 0, 0);
}

void Panda::send_heartbeat(bool engaged) {
  handle->control_write(0xf3, engaged, 0);
}

bool Panda::matches_preap_pedal(const std::array<uint16_t, 24> &words) {
  std::array<uint8_t, 48> actual{}, expected{};
  for (size_t i = 0; i < words.size(); ++i) {
    expected[2 * i] = words[i] & 255;
    expected[2 * i + 1] = words[i] >> 8;
  }
  const int count = handle->control_read(0xb8, 0, 0, actual.data(), actual.size());
  return count == static_cast<int>(actual.size()) && actual == expected;
}

bool Panda::stage_preap_pedal(const std::array<uint16_t, 24> &words) {
  for (size_t i = 0; i < words.size(); ++i) {
    if (handle->control_write(0xb7, static_cast<uint16_t>(i), words[i]) < 0) return false;
  }
  if (handle->control_write(0xb7, 0xffff, 0xc051) < 0) return false;
  return matches_preap_pedal(words);
}

bool Panda::set_aol_axis_request(uint8_t axis_mask) {
  return handle->control_write(0xf5, axis_mask & 0x7U, 0) >= 0;
}

void Panda::set_can_speed_kbps(uint16_t bus, uint16_t speed) {
  handle->control_write(0xde, bus, (speed * 10));
}

void Panda::set_can_fd_auto(uint16_t bus, bool enabled) {
  handle->control_write(0xe8, bus, enabled);
}

void Panda::set_data_speed_kbps(uint16_t bus, uint16_t speed) {
  handle->control_write(0xf9, bus, (speed * 10));
}

void Panda::set_canfd_non_iso(uint16_t bus, bool non_iso) {
  handle->control_write(0xfc, bus, non_iso);
}

static uint8_t len_to_dlc(uint8_t len) {
  if (len <= 8) {
    return len;
  }
  if (len <= 24) {
    return 8 + ((len - 8) / 4) + ((len % 4) ? 1 : 0);
  } else {
    return 11 + (len / 16) + ((len % 16) ? 1 : 0);
  }
}

void Panda::pack_can_buffer(const capnp::List<cereal::CanData>::Reader &can_data_list,
                            std::function<void(uint8_t *, size_t)> write_func) {
  int32_t pos = 0;
  uint8_t send_buf[2 * USB_TX_SOFT_LIMIT];

  for (const auto &cmsg : can_data_list) {
    // check if the message is intended for this panda
    uint8_t bus = cmsg.getSrc();
    if (bus < bus_offset || bus >= bus_offset + PANDA_BUS_OFFSET) {
      continue;
    }
    auto can_data = cmsg.getDat();
    uint8_t data_len_code = len_to_dlc(can_data.size());
    assert(can_data.size() <= 64);
    assert(can_data.size() == dlc_to_len[data_len_code]);

    can_header header = {};
    header.addr = cmsg.getAddress();
    header.extended = (cmsg.getAddress() >= 0x800) ? 1 : 0;
    header.data_len_code = data_len_code;
    header.bus = bus - bus_offset;
    header.checksum = 0;

    memcpy(&send_buf[pos], (uint8_t *)&header, sizeof(can_header));
    memcpy(&send_buf[pos + sizeof(can_header)], (uint8_t *)can_data.begin(), can_data.size());
    uint32_t msg_size = sizeof(can_header) + can_data.size();

    // set checksum
    ((can_header *) &send_buf[pos])->checksum = calculate_checksum(&send_buf[pos], msg_size);

    pos += msg_size;

    if (pos >= USB_TX_SOFT_LIMIT) {
      write_func(send_buf, pos);
      pos = 0;
    }
  }

  // send remaining packets
  if (pos > 0) write_func(send_buf, pos);
}

void Panda::can_send(const capnp::List<cereal::CanData>::Reader &can_data_list) {
  pack_can_buffer(can_data_list, [=](uint8_t* data, size_t size) {
    handle->bulk_write(3, data, size, 5);
  });
}

bool Panda::can_receive(std::vector<can_frame>& out_vec) {
  // Check if enough space left in buffer to store RECV_SIZE data
  assert(receive_buffer_size + RECV_SIZE <= sizeof(receive_buffer));

  int recv = handle->bulk_read(0x81, &receive_buffer[receive_buffer_size], RECV_SIZE);
  if (!comms_healthy()) {
    return false;
  }

  if (PANDAD_MAXOUT) {
    static uint8_t junk[RECV_SIZE];
    handle->bulk_read(0xab, junk, RECV_SIZE - recv);
  }

  bool ret = true;
  if (recv > 0) {
    receive_buffer_size += recv;
    ret = unpack_can_buffer(receive_buffer, receive_buffer_size, out_vec);
  }
  return ret;
}

void Panda::can_reset_communications() {
  handle->control_write(0xc0, 0, 0);
}

bool Panda::unpack_can_buffer(uint8_t *data, uint32_t &size, std::vector<can_frame> &out_vec) {
  int pos = 0;

  while (pos <= size - sizeof(can_header)) {
    can_header header;
    memcpy(&header, &data[pos], sizeof(can_header));

    const uint8_t data_len = dlc_to_len[header.data_len_code];
    if (pos + sizeof(can_header) + data_len > size) {
      // we don't have all the data for this message yet
      break;
    }

    if (calculate_checksum(&data[pos], sizeof(can_header) + data_len) != 0) {
      LOGE("Panda CAN checksum failed");
      size = 0;
      can_reset_communications();
      return false;
    }

    can_frame &canData = out_vec.emplace_back();
    canData.address = header.addr;
    canData.src = header.bus + bus_offset;
    if (header.rejected) {
      canData.src += CAN_REJECTED_BUS_OFFSET;
    }
    if (header.returned) {
      canData.src += CAN_RETURNED_BUS_OFFSET;
    }

    canData.dat.assign((char *)&data[pos + sizeof(can_header)], data_len);

    pos += sizeof(can_header) + data_len;
  }

  // move the overflowing data to the beginning of the buffer for the next round
  memmove(data, &data[pos], size - pos);
  size -= pos;

  return true;
}

uint8_t Panda::calculate_checksum(uint8_t *data, uint32_t len) {
  uint8_t checksum = 0U;
  for (uint32_t i = 0U; i < len; i++) {
    checksum ^= data[i];
  }
  return checksum;
}
