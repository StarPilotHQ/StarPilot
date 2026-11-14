#include "openpilot/starpilot/car/ford/aol_policy.h"
#include "openpilot/starpilot/car/nissan/aol_policy.h"
#include "selfdrive/pandad/pandad.h"
#include "selfdrive/pandad/aol_protocol.h"
#include "selfdrive/pandad/aol_wire.h"
#include "openpilot/starpilot/car/honda/aol_policy.h"
#include "openpilot/starpilot/car/mazda/aol_policy.h"
#include "openpilot/starpilot/car/rivian/aol_policy.h"
#include "openpilot/starpilot/car/volkswagen/aol_policy.h"
#include "openpilot/starpilot/car/chrysler/aol_policy.h"
#include "openpilot/starpilot/car/subaru/aol_policy.h"
#include "openpilot/starpilot/car/toyota/aol_policy.h"
#include "openpilot/starpilot/car/tesla/aol_policy.h"
#include "openpilot/starpilot/car/hyundai/aol_policy.h"
#include "starpilot/car/gm/aol_policy.h"

#include <algorithm>
#include <array>
#include <atomic>
#include "openpilot/selfdrive/pandad/panda_faults.h"
#include <cassert>
#include <cerrno>
#include <cstring>
#include <memory>
#include <thread>
#include <utility>

#include "openpilot/cereal/gen/cpp/car.capnp.h"
#include "openpilot/cereal/messaging/messaging.h"
#include "openpilot/cereal/services.h"
#include "common/ratekeeper.h"
#include "common/swaglog.h"
#include "common/timing.h"
#include "common/util.h"
#include "common/hardware/hw.h"

#define MAX_IR_PANDA_VAL 50
#define CUTOFF_IL 400
#define SATURATE_IL 1000

ExitHandler do_exit;

// One atomic pair keeps voltage/current from the same hwmon sample together.
std::atomic<uint64_t> hwmon_power{0};

void hwmon_thread() {
  if (Hardware::get_device_type() != cereal::InitData::DeviceType::TICI) return;
  util::set_thread_name("pandad_hwmon");
  while (!do_exit) {
    const uint32_t voltage = Hardware::get_voltage();
    const uint32_t current = Hardware::get_current();
    hwmon_power.store((uint64_t(voltage) << 32) | current);
    util::sleep_for(500);
  }
}

bool check_all_connected(const std::vector<Panda *> &pandas) {
  for (const auto& panda : pandas) {
    if (!panda->connected()) {
      do_exit = true;
      return false;
    }
  }
  return true;
}

Panda *connect(std::string serial, uint32_t index) {
  std::unique_ptr<Panda> panda;
  try {
    panda = std::make_unique<Panda>(serial, index * PANDA_BUS_OFFSET);
  } catch (std::exception &e) {
    return nullptr;
  }

  // common panda config
  if (getenv("BOARDD_LOOPBACK")) {
    panda->set_loopback(true);
  }
  //panda->enable_deepsleep();

  for (int i = 0; i < PANDA_CAN_CNT; i++) {
    panda->set_can_fd_auto(i, true);
  }

  if (!panda->up_to_date() && (!getenv("BOARDD_SKIP_FW_CHECK") ||
      getenv("STARPILOT_PANDA_FIRMWARE_MAP") || getenv("STARPILOT_PANDA_FIRMWARE_NAME"))) {
    throw std::runtime_error("Panda firmware out of date. Run pandad.py to update.");
  }

  return panda.release();
}

void can_send_thread(std::vector<Panda *> pandas, bool fake_send) {
  util::set_thread_name("pandad_can_send");

  AlignedBuffer aligned_buf;
  std::unique_ptr<SubSocket> subscriber(SubSocket::create("sendcan", false, services.at("sendcan").queue_size));
  assert(subscriber != NULL);
  subscriber->setTimeout(100);

  // run as fast as messages come in
  while (!do_exit && check_all_connected(pandas)) {
    std::unique_ptr<Message> msg(subscriber->receive());
    if (!msg) {
      continue;
    }

    capnp::FlatArrayMessageReader cmsg(aligned_buf.align(msg.get()));
    cereal::Event::Reader event = cmsg.getRoot<cereal::Event>();

    // Don't send if older than 1 second
    if ((nanos_since_boot() - event.getLogMonoTime() < 1e9) && !fake_send) {
      for (const auto& panda : pandas) {
        LOGT("sending sendcan to panda: %s", (panda->hw_serial()).c_str());
        panda->can_send(event.getSendcan());
        LOGT("sendcan sent to panda: %s", (panda->hw_serial()).c_str());
      }
    } else {
      LOGE("sendcan too old to send: %" PRIu64 ", %" PRIu64, nanos_since_boot(), event.getLogMonoTime());
    }

  }
}

void can_recv(std::vector<Panda *> &pandas, PubMaster *pm) {
  static std::vector<can_frame> raw_can_data;
  {
    bool comms_healthy = true;
    raw_can_data.clear();
    for (const auto& panda : pandas) {
      comms_healthy &= panda->can_receive(raw_can_data);
    }

    MessageBuilder msg;
    auto evt = msg.initEvent();
    evt.setValid(comms_healthy);
    auto canData = evt.initCan(raw_can_data.size());
    for (size_t i = 0; i < raw_can_data.size(); ++i) {
      canData[i].setAddress(raw_can_data[i].address);
      canData[i].setDat(kj::arrayPtr((uint8_t*)raw_can_data[i].dat.data(), raw_can_data[i].dat.size()));
      canData[i].setSrc(raw_can_data[i].src);
    }
    pm->send("can", msg);
  }
}

void fill_panda_state(cereal::PandaState::Builder &ps, cereal::PandaState::PandaType hw_type, const health_t &health) {
  ps.setVoltage(health.voltage_pkt);
  ps.setCurrent(health.current_pkt);
  ps.setUptime(health.uptime_pkt);
  ps.setSafetyTxBlocked(health.safety_tx_blocked_pkt);
  ps.setSafetyRxInvalid(health.safety_rx_invalid_pkt);
  ps.setIgnitionLine((health.flags_pkt & HEALTH_FLAG_IGNITION_LINE) != 0U);
  ps.setIgnitionCan((health.flags_pkt & HEALTH_FLAG_IGNITION_CAN) != 0U);
  ps.setControlsAllowed((health.flags_pkt & HEALTH_FLAG_CONTROLS_ALLOWED) != 0U);
  ps.setTxBufferOverflow(health.tx_buffer_overflow_pkt);
  ps.setRxBufferOverflow(health.rx_buffer_overflow_pkt);
  ps.setPandaType(hw_type);
  ps.setSafetyModel(cereal::CarParams::SafetyModel(health.safety_mode_pkt));
  ps.setSafetyParam(health.safety_param_pkt);
  ps.setFaultStatus(cereal::PandaState::FaultStatus(health.fault_status_pkt));
  ps.setPowerSaveEnabled((health.flags_pkt & HEALTH_FLAG_POWER_SAVE_ENABLED) != 0U);
  ps.setHeartbeatLost((health.flags_pkt & HEALTH_FLAG_HEARTBEAT_LOST) != 0U);
  ps.setAlternativeExperience(health.alternative_experience_pkt);
  ps.setHarnessStatus(cereal::PandaState::HarnessStatus(health.car_harness_status_pkt));
  ps.setInterruptLoad(health.interrupt_load_pkt / 255.0f);
  ps.setFanPower(health.fan_power);
  ps.setSafetyRxChecksInvalid((health.flags_pkt & HEALTH_FLAG_SAFETY_RX_CHECKS_INVALID) != 0U);
  ps.setNmiReset((health.flags_pkt & HEALTH_FLAG_NMI_RESET) != 0U);
  ps.setHardfaultReset((health.flags_pkt & HEALTH_FLAG_HARDFAULT_RESET) != 0U);
  ps.setSpiErrorCount(health.spi_error_count_pkt);
  ps.setSbu1Voltage(health.sbu1_voltage_mV / 1000.0f);
  ps.setSbu2Voltage(health.sbu2_voltage_mV / 1000.0f);
  ps.setSoundOutputLevel(health.sound_output_level_pkt);
}

void fill_panda_can_state(cereal::PandaState::PandaCanState::Builder &cs, const can_health_t &can_health) {
  cs.setBusOff((bool)can_health.bus_off);
  cs.setBusOffCnt(can_health.bus_off_cnt);
  cs.setErrorWarning((bool)can_health.error_warning);
  cs.setErrorPassive((bool)can_health.error_passive);
  cs.setLastError(cereal::PandaState::PandaCanState::LecErrorCode(can_health.last_error));
  cs.setLastStoredError(cereal::PandaState::PandaCanState::LecErrorCode(can_health.last_stored_error));
  cs.setLastDataError(cereal::PandaState::PandaCanState::LecErrorCode(can_health.last_data_error));
  cs.setLastDataStoredError(cereal::PandaState::PandaCanState::LecErrorCode(can_health.last_data_stored_error));
  cs.setReceiveErrorCnt(can_health.receive_error_cnt);
  cs.setTransmitErrorCnt(can_health.transmit_error_cnt);
  cs.setTotalErrorCnt(can_health.total_error_cnt);
  cs.setTotalTxLostCnt(can_health.total_tx_lost_cnt);
  cs.setTotalRxLostCnt(can_health.total_rx_lost_cnt);
  cs.setTotalTxCnt(can_health.total_tx_cnt);
  cs.setTotalRxCnt(can_health.total_rx_cnt);
  cs.setTotalFwdCnt(can_health.total_fwd_cnt);
  cs.setCanSpeed(can_health.can_speed);
  cs.setCanDataSpeed(can_health.can_data_speed);
  cs.setCanfdEnabled(can_health.canfd_enabled);
  cs.setBrsEnabled(can_health.brs_enabled);
  cs.setCanfdNonIso(can_health.canfd_non_iso);
  cs.setIrq0CallRate(can_health.irq0_call_rate);
  cs.setIrq1CallRate(can_health.irq1_call_rate);
  cs.setIrq2CallRate(can_health.irq2_call_rate);
  cs.setCanCoreResetCnt(can_health.can_core_reset_cnt);
}

std::optional<bool> send_panda_states(PubMaster *pm, const std::vector<Panda *> &pandas, bool is_onroad, bool spoofing_started,
                                      AolPandaStateSnapshot *snapshot = nullptr) {
  if (snapshot) *snapshot = {};
  bool ignition_local = false;
  std::vector<health_t> states;
  std::vector<std::array<can_health_t, PANDA_CAN_CNT>> can_states;
  // build msg
  MessageBuilder msg;
  auto evt = msg.initEvent();
  auto pss = evt.initPandaStates(pandas.size());

  for (auto *panda : pandas) {
    auto health_opt = panda->get_state();
    if (!health_opt) {
      return std::nullopt;
    }

    health_t health = *health_opt;

    std::array<can_health_t, PANDA_CAN_CNT> can_health{};
    for (uint32_t i = 0; i < PANDA_CAN_CNT; i++) {
      auto can_health_opt = panda->get_can_state(i);
      if (!can_health_opt) {
        return std::nullopt;
      }
      can_health[i] = *can_health_opt;
    }

    if (spoofing_started) {
      health.flags_pkt |= HEALTH_FLAG_IGNITION_LINE;
    }

    if (pandas.size() == 2 && pandas[0]->hw_type == cereal::PandaState::PandaType::DOS &&
        pandas[1]->hw_type == cereal::PandaState::PandaType::RED_PANDA && panda == pandas[0]) {
      health.flags_pkt &= ~HEALTH_FLAG_IGNITION_LINE;
    }
    ignition_local |= (health.flags_pkt & (HEALTH_FLAG_IGNITION_LINE | HEALTH_FLAG_IGNITION_CAN)) != 0U;
    states.push_back(health);
    can_states.push_back(can_health);
  }

  for (size_t i = 0; i < pandas.size(); ++i) {
    auto *panda = pandas[i];
    const auto &health = states[i];
    const auto &can_health = can_states[i];

    // Make sure CAN buses are live: safety_setter_thread does not work if Panda CAN are silent and there is only one other CAN node
    if (health.safety_mode_pkt == (uint8_t)(cereal::CarParams::SafetyModel::SILENT)) {
      panda->set_safety_model(cereal::CarParams::SafetyModel::NO_OUTPUT);
    }

    bool power_save_desired = !ignition_local;
    if (((health.flags_pkt & HEALTH_FLAG_POWER_SAVE_ENABLED) != 0U) != power_save_desired) {
      panda->set_power_saving(power_save_desired);
    }

    // set safety mode to NO_OUTPUT when car is off or we're not onroad. ELM327 is an alternative if we want to leverage athenad/connect
    bool should_close_relay = !ignition_local || !is_onroad;
    if (should_close_relay && (health.safety_mode_pkt != (uint8_t)(cereal::CarParams::SafetyModel::NO_OUTPUT))) {
      panda->set_safety_model(cereal::CarParams::SafetyModel::NO_OUTPUT);
    }

    if (!panda->comms_healthy()) {
      evt.setValid(false);
    }

    auto ps = pss[i];
    fill_panda_state(ps, panda->hw_type, health);

    auto cs = std::array{ps.initCanState0(), ps.initCanState1(), ps.initCanState2()};
    for (uint32_t j = 0; j < PANDA_CAN_CNT; j++) {
      fill_panda_can_state(cs[j], can_health[j]);
    }

    fill_panda_faults(ps, health.faults_pkt);
  }
  if (snapshot && evt.getValid()) {
    snapshot->source_mono_time = evt.getLogMonoTime();
    for (size_t i = 0; i < pandas.size(); ++i) {
      const auto &health = states[i];
      snapshot->slots.push_back({static_cast<uint16_t>(i), pandas[i]->hw_serial(), health.safety_mode_pkt,
        health.safety_param_pkt, health.alternative_experience_pkt,
        (health.flags_pkt & HEALTH_FLAG_CONTROLS_ALLOWED) != 0U,
        (health.flags_pkt & HEALTH_FLAG_SAFETY_RX_CHECKS_INVALID) != 0U,
        (health.flags_pkt & HEALTH_FLAG_HEARTBEAT_LOST) != 0U, health.faults_pkt});
    }
  }
  pm->send("pandaStates", msg);
  return ignition_local;
}

void send_peripheral_state(Panda *panda, PubMaster *pm) {
  auto health_opt = panda->get_state();
  if (!health_opt) {
    return;
  }

  // build msg
  MessageBuilder msg;
  auto evt = msg.initEvent();
  evt.setValid(panda->comms_healthy());

  auto ps = evt.initPeripheralState();
  ps.setPandaType(panda->hw_type);

  const health_t &health = *health_opt;
  // The original comma 3 measures input power with its SOM hwmon sensor.
  // Its DOS Panda ADC is not the source used by Dom. Keep slow sysfs reads
  // in the helper thread; newer devices continue to report Panda power.
  const uint64_t power = hwmon_power.load();
  ps.setVoltage(power ? uint32_t(power >> 32) : health.voltage_pkt);
  ps.setCurrent(power ? uint32_t(power) : health.current_pkt);

  uint16_t fan_speed_rpm = panda->get_fan_speed();
  ps.setFanSpeedRpm(fan_speed_rpm);

  pm->send("peripheralState", msg);
}

void process_panda_state(std::vector<Panda *> &pandas, PubMaster *pm, bool engaged, bool is_onroad,
                         bool spoofing_started, AolPandaStateSnapshot *snapshot = nullptr) {
  if (snapshot) *snapshot = {};
  std::vector<std::string> connected_serials;
  for (Panda *p : pandas) {
    connected_serials.push_back(p->hw_serial());
  }

  {
    auto ignition_opt = send_panda_states(pm, pandas, is_onroad, spoofing_started, snapshot);
    if (!ignition_opt) {
      LOGE("Failed to get ignition_opt");
      return;
    }

    // check if we should have pandad reconnect
    if (!ignition_opt.value()) {
      bool comms_healthy = true;
      for (const auto &panda : pandas) {
        comms_healthy &= panda->comms_healthy();
      }

      if (!comms_healthy) {
        LOGE("Reconnecting, communication to pandas not healthy");
        do_exit = true;

      } else {
        // check for new pandas
        for (std::string &s : Panda::list()) {
          if (!std::count(connected_serials.begin(), connected_serials.end(), s)) {
            LOGW("Reconnecting to new panda: %s", s.c_str());
            do_exit = true;
            break;
          }
        }
      }
    }

    for (const auto &panda : pandas) {
      panda->send_heartbeat(engaged);
    }
  }
}

void process_peripheral_state(Panda *panda, PubMaster *pm, bool no_fan_control, bool is_onroad) {
  static Params params;
  static SubMaster sm({"deviceState", "cabinCameraState"});

  static uint64_t last_cabin_camera_t = 0;
  static uint16_t prev_fan_speed = 999;
  static int ir_pwr = 0;
  static int prev_ir_pwr = 999;
  static uint32_t prev_frame_id = UINT32_MAX;
  static bool driver_view = false;
  static bool not_car = false;
  static bool not_car_checked = false;

  // TODO: can we merge these?
  static FirstOrderFilter integ_lines_filter(0, 30.0, 0.05);
  static FirstOrderFilter integ_lines_filter_driver_view(0, 5.0, 0.05);

  {
    sm.update(0);
    if (sm.updated("deviceState") && !no_fan_control) {
      // Fan speed
      uint16_t fan_speed = sm["deviceState"].getDeviceState().getFanSpeedPercentDesired();
      if (fan_speed != prev_fan_speed || sm.frame % 100 == 0) {
        panda->set_fan_speed(fan_speed);
        prev_fan_speed = fan_speed;
      }
    }

    if (sm.updated("cabinCameraState")) {
      auto event = sm["cabinCameraState"];
      int cur_integ_lines = event.getCabinCameraState().getIntegLines();

      // reset the filter when camerad restarts
      if (event.getCabinCameraState().getFrameId() < prev_frame_id) {
        integ_lines_filter.reset(0);
        integ_lines_filter_driver_view.reset(0);
        driver_view = params.getBool("IsDriverViewEnabled");
      }
      prev_frame_id = event.getCabinCameraState().getFrameId();

      cur_integ_lines = (driver_view ? integ_lines_filter_driver_view : integ_lines_filter).update(cur_integ_lines);
      last_cabin_camera_t = event.getLogMonoTime();

      if (cur_integ_lines <= CUTOFF_IL) {
        ir_pwr = 0;
      } else if (cur_integ_lines > SATURATE_IL) {
        ir_pwr = 100;
      } else {
        ir_pwr = 100 * (cur_integ_lines - CUTOFF_IL) / (SATURATE_IL - CUTOFF_IL);
      }
    }

    // Disable IR on input timeout or when requested offroad.
    if (nanos_since_boot() - last_cabin_camera_t > 1e9 || (!is_onroad && params.getBool("DisableDriverCameraIR"))) {
      ir_pwr = 0;
    }

    // turn off IR leds if body
    if (!not_car_checked && is_onroad) {
      std::string cp_bytes = params.get("CarParams");
      if (cp_bytes.size() > 0) {
        AlignedBuffer aligned_buf;
        capnp::FlatArrayMessageReader cmsg(aligned_buf.align(cp_bytes.data(), cp_bytes.size()));
        cereal::CarParams::Reader CP = cmsg.getRoot<cereal::CarParams>();
        not_car = CP.getNotCar();
        not_car_checked = true;
      }
    }
    if (not_car) {
      ir_pwr = 0;
    }

    if (ir_pwr != prev_ir_pwr || sm.frame % 100 == 0) {
      int16_t ir_panda = util::map_val(ir_pwr, 0, 100, 0, MAX_IR_PANDA_VAL);
      panda->set_ir_pwr(ir_panda);
      Hardware::set_ir_power(ir_pwr);
      prev_ir_pwr = ir_pwr;
    }
  }
}

void pandad_run(std::vector<Panda *> &pandas) {
  const bool no_fan_control = getenv("NO_FAN_CONTROL") != nullptr;
  const bool spoofing_started = getenv("STARTED") != nullptr;
  const bool fake_send = getenv("FAKESEND") != nullptr;
  const char *aol_env = getenv("AOL_REPLAY_RUNTIME");
  const bool aol_replay = aol_env != nullptr && strcmp(aol_env, "1") == 0;

  // Start helper thread for event-driven sendcan.
  std::thread send_thread(can_send_thread, pandas, fake_send);
  hwmon_power.store(0);
  std::thread hardware_thread;
  if (Hardware::get_device_type() == cereal::InitData::DeviceType::TICI) {
    hardware_thread = std::thread(hwmon_thread);
  }

  RateKeeper rk("pandad", 100);
  SubMaster sm({"selfdriveState", "deviceState", "aolAxisState", "aolIntentWire"});
  PubMaster pm({"can", "pandaStates", "peripheralState", "aolSafetyWire"});
  PandaSafety panda_safety(pandas);
  Panda *peripheral_panda = pandas[0];
  Panda *previous_aol_owner = nullptr;
  bool engaged = false;
  bool is_onroad = false;
  constexpr AolSafetyProfile aol_profiles[] = {HONDA_AOL_PROFILE, HONDA_STOCK_AOL_PROFILE, HONDA_NIDEC_AOL_PROFILE, HYUNDAI_AOL_PROFILE, HYUNDAI_CLASSIC_AOL_PROFILE, HYUNDAI_LEGACY_AOL_PROFILE, GM_AOL_PROFILE, FORD_AOL_PROFILE, MAZDA_AOL_PROFILE, RIVIAN_AOL_PROFILE, CHRYSLER_AOL_PROFILE, TESLA_PREAP_AOL_PROFILE, TESLA_SCREEN_AOL_PROFILE, TOYOTA_AOL_PROFILE, NISSAN_AOL_PROFILE, VOLKSWAGEN_STOCK_AOL_PROFILE, VOLKSWAGEN_LONG_AOL_PROFILE, VOLKSWAGEN_MEB_AOL_PROFILE, VOLKSWAGEN_PQ_AOL_PROFILE, SUBARU_AOL_PROFILE};
  const AolProfileRegistry aol_registry{aol_profiles, std::size(aol_profiles)};
  AolAxisNegotiator aol_negotiator(aol_registry);
  // Main loop: receive CAN first, then process lower priority panda and peripheral state.
  while (!do_exit) {
    if (!check_all_connected(pandas)) { break; }
    { can_recv(pandas, &pm); }

    // Process peripheral state at 20 Hz
    if (rk.frame() % 5 == 0) {
      { process_peripheral_state(peripheral_panda, &pm, no_fan_control, is_onroad); }
    }

    // Process panda state at 10 Hz
    if (rk.frame() % 10 == 0) {
      sm.update(0);
      engaged = sm.allAliveAndValid({"selfdriveState"}) && sm["selfdriveState"].getSelfdriveState().getEnabled();
      // AOL axis and intent timestamps come from Python CLOCK_MONOTONIC.
      // Keep the ordinary pandad/CAN BOOTTIME clock unchanged.
      uint64_t now_ns = aol_monotonic_ns();
      const bool axis_received = sm.allAliveAndValid({"aolAxisState"});
      Panda *panda = panda_safety.aolOwner();
      if (panda != previous_aol_owner) {
        aol_negotiator = AolAxisNegotiator(aol_registry);
        previous_aol_owner = panda;
      }
      std::optional<aol_safety_health_t> aol_status;
      { aol_status = (panda && (aol_replay || axis_received)) ? panda->get_aol_safety_state() : std::nullopt; }
      if (aol_status && !panda_safety.aolStatusMatches(panda, *aol_status)) aol_status.reset();
      const bool aol_runtime = aol_runtime_enabled(aol_replay, aol_status, aol_registry);
      if (sm.updated("deviceState")) {
        is_onroad = sm["deviceState"].getDeviceState().getStarted();
      }
      AolAxisInput axis_input;
      if (axis_received) {
        auto axis_event = sm["aolAxisState"];
        auto axis = axis_event.getAolAxisState();
        axis_input = {true, axis.getQualified(), axis.getSessionId().cStr(), axis.getObservedMonoTime(),
                      axis.getValidUntilMonoTime(), axis_event.getLogMonoTime(),
                      axis.getDesiredLateral(), axis.getDesiredLongitudinal()};

        if (is_onroad && sm.allAliveAndValid({"deviceState", "aolIntentWire"})) {
          auto intent_event = sm["aolIntentWire"];
          axis_input.retain_lateral_arm = aol_armed_intent(intent_event.getAolIntentWire(),
              intent_event.getLogMonoTime(), axis.getSourceCarStateMonoTime(), now_ns,
              false, axis.getIntentProducerSessionId().cStr());
          axis_input.optional_set_release = axis.getOptionalSetRelease() &&
            aol_armed_intent(intent_event.getAolIntentWire(), intent_event.getLogMonoTime(),
                             axis.getSourceCarStateMonoTime(), now_ns, true);
        }
      }
      const uint8_t aol_expected_mode = aol_status ? aol_status->safety_mode : 0U;
      auto plan = aol_negotiator.prepare(aol_runtime ? aol_status : std::nullopt, axis_input, now_ns, aol_expected_mode);
      bool aol_write_ok;
      { aol_write_ok = panda && plan.capable && panda->set_aol_axis_request(plan.request_mask); }
      if (aol_write_ok) {
        engaged = engaged || (plan.request_mask & 0x3U) != 0U || plan.retain_lateral_arm;
      }
      AolPandaStateSnapshot inventory;
      { process_panda_state(pandas, &pm, engaged, is_onroad, spoofing_started, &inventory); }
      std::optional<aol_safety_health_t> aol_after;
      { aol_after = (aol_write_ok && panda_safety.aolOwner() == panda) ? panda->get_aol_safety_state() : std::nullopt; }
      if (aol_after && !panda_safety.aolStatusMatches(panda, *aol_after)) aol_after.reset();
      auto outcome = aol_negotiator.complete(plan, aol_write_ok, aol_after);
      if (aol_runtime) {
        MessageBuilder axis_status_msg;
        auto axis_status_event = axis_status_msg.initEvent();
        axis_status_event.setLogMonoTime(now_ns);
        bool status_current = outcome.compatible;
        auto reported_status = outcome.status;
        const std::string serial = panda ? panda->hw_serial() : "";
        const AolSafetyWireFields fields = {
          static_cast<uint16_t>(status_current ? AOL_SAFETY_PROTOCOL_VERSION : 0U), status_current,
          now_ns, now_ns + 200000000ULL,
          static_cast<uint16_t>(reported_status ? reported_status->safety_mode : 0U),
          reported_status ? reported_status->safety_param : uint16_t{0},
          status_current && reported_status && ((reported_status->permission_mask & 0x1U) != 0U),
          status_current && reported_status && ((reported_status->permission_mask & 0x2U) != 0U),
          status_current && reported_status && ((reported_status->request_mask & 0x1U) != 0U),
          status_current && reported_status && ((reported_status->request_mask & 0x2U) != 0U), serial, outcome.session, inventory,
        };
        auto payload = encode_aol_safety_wire(fields);
        const bool wire_valid = status_current && !payload.empty();
        axis_status_event.setValid(wire_valid);
        if (wire_valid) {
          axis_status_event.setAolSafetyWire(kj::arrayPtr(payload.data(), payload.size()));
        } else {
          axis_status_event.initAolSafetyWire(0);
        }
        pm.send("aolSafetyWire", axis_status_msg);
      }
      { panda_safety.configureSafetyMode(is_onroad); }
    }

    // Send out peripheralState at 2Hz
    if (rk.frame() % 50 == 0) {
      { send_peripheral_state(peripheral_panda, &pm); }
    }

    // Forward logs from panda to cloudlog if available
    for (auto *panda : pandas) {
      std::string log;
      { log = panda->serial_read(); }
      if (!log.empty()) {
        if (log.find("Register 0x") != std::string::npos) {
          // Log register divergent faults as errors
          LOGE("%s", log.c_str());
        } else {
          LOGD("%s", log.c_str());
        }
      }
    }
    { rk.keepTime(); }
  }

  // Close relay on exit to prevent a fault
  if (is_onroad && !engaged) {
    for (auto *panda : pandas) {
      if (panda->connected()) panda->set_safety_model(cereal::CarParams::SafetyModel::NO_OUTPUT);
    }
  }

  send_thread.join();
  if (hardware_thread.joinable()) hardware_thread.join();
}

void pandad_main_thread(std::vector<std::string> serials) {
  if (serials.size() == 0) {
    serials = Panda::list();

    if (serials.size() == 0) {
      LOGW("no pandas found, exiting");
      return;
    }
    if (serials.size() > 1) {
      // CP slot order is owned by the Python wrapper's hardware/type/serial sort.
      // Enumeration order alone cannot determine multi-Panda bus ownership.
      LOGE("multiple pandas require ordered serial arguments from pandad wrapper");
      return;
    }
  }

  std::string serials_str;
  for (int i = 0; i < serials.size(); i++) {
    serials_str += serials[i];
    if (i < serials.size() - 1) serials_str += ", ";
  }
  LOGW("connecting to pandas: %s", serials_str.c_str());

  // connect to all provided serials
  std::vector<Panda *> pandas;
  for (int i = 0; i < serials.size() && !do_exit; /**/) {
    Panda *p = connect(serials[i], i);
    if (!p) {
      util::sleep_for(100);
      continue;
    }

    pandas.push_back(p);
    ++i;
  }

  if (!do_exit) {
    LOGW("connected to all pandas");
    pandad_run(pandas);
  }

  for (Panda *panda : pandas) {
    delete panda;
  }
}
