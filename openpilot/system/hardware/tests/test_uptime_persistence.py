import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.system.hardware import hardwared


@pytest.mark.parametrize("temperature", [60., 120.])
def test_uptime_disk_persistence_cannot_hold_temperature_updates(monkeypatch, temperature):
  ticks = 0
  saved = []
  published = []
  thermal_reads = []

  class SavedParams:
    def get(self, key, **kwargs):
      return {"UptimeOffroad": 10., "UptimeOnroad": 10., "HasAcceptedTerms": hardwared.terms_version,
              "CompletedTrainingVersion": hardwared.training_version}.get(key)

    def get_bool(self, key):
      return False

    def put(self, key, value, block=False):
      if key in ("UptimeOffroad", "UptimeOnroad"):
        assert not block, "Uptime persistence must not wait for disk in the health loop"
        saved.append((key, value))

    def put_bool(self, *args, **kwargs):
      pass

  class Subscriber:
    frame = 0

    def __init__(self):
      panda = SimpleNamespace(ignitionLine=True, ignitionCan=False, pandaType=hardwared.log.PandaState.PandaType.cuatro,
                              harnessStatus=hardwared.log.PandaState.HarnessStatus.normal, to_dict=dict)
      self.data = {"pandaStates": [panda], "peripheralState": SimpleNamespace(pandaType=hardwared.log.PandaState.PandaType.unknown, to_dict=dict),
                   "selfdriveState": SimpleNamespace(enabled=False), "chestnutState": None}
      self.updated = dict.fromkeys(self.data, True)
      self.alive = {"chestnutState": False, "gpsLocationExternal": False}
      self.valid = {"chestnutState": False}
      self.recv_time = {"pandaStates": 100.}

    def __getitem__(self, key):
      return self.data[key]

    def update(self, timeout):
      nonlocal ticks
      ticks += 1
      self.frame += 5

  sm = Subscriber()
  def thermal():
    thermal_reads.append(temperature)
    return SimpleNamespace(cpuTempC=[temperature], gpuTempC=[temperature], memoryTempC=temperature, pmicTempC=[])
  hardware = SimpleNamespace(initialize_hardware=lambda: None, get_thermal_config=lambda: SimpleNamespace(get_msg=thermal),
                             get_device_type=lambda: "mici", get_gpu_usage_percent=lambda: 0., get_screen_brightness=lambda: 50.,
                             set_power_save=lambda value: None, get_current_power_draw=lambda: 5., booted=lambda: True)
  publisher = SimpleNamespace(send=lambda name, msg: published.append(copy.copy(msg.deviceState)))
  def message(*args, **kwargs):
    return SimpleNamespace(deviceState=None, to_dict=dict)
  monkeypatch.setattr(hardwared, "Params", SavedParams)
  monkeypatch.setattr(hardwared, "HARDWARE", hardware)
  monkeypatch.setattr(hardwared, "PC", True)
  monkeypatch.setattr(hardwared, "DriveStateOwner", lambda *args: SimpleNamespace(snapshot=lambda: SimpleNamespace(mode=hardwared.Mode.AUTO)))
  monkeypatch.setattr(hardwared, "starpilot_storage_root", lambda: Path("/unused"))
  monkeypatch.setattr(hardwared, "LinuxSystemStats", lambda: SimpleNamespace(memory_usage_percent=lambda: 50., cpu_usage_percent=lambda: [10.]))
  monkeypatch.setattr(hardwared, "HardwareLoopTiming", lambda: SimpleNamespace(mark=lambda phase: None))
  monkeypatch.setattr(hardwared, "PowerMonitoring", lambda: SimpleNamespace(calculate=lambda *args: None, get_power_used=lambda: 0.,
                                                                          get_car_battery_capacity=lambda: 0., should_shutdown=lambda *args: False))
  monkeypatch.setattr(hardwared, "FanController", lambda hz: SimpleNamespace(update=lambda *args: 50))
  monkeypatch.setattr(hardwared, "Chestnut", lambda: SimpleNamespace(update=lambda *args: None, failed=False, flashing=False))
  monkeypatch.setattr(hardwared, "ChestnutStatus", lambda: SimpleNamespace(update=lambda *args: None))
  monkeypatch.setattr(hardwared, "get_usb_state", lambda: None)
  monkeypatch.setattr(hardwared, "set_usb_state", lambda *args: None)
  monkeypatch.setattr(hardwared, "get_available_percent", lambda **kwargs: 50.)
  monkeypatch.setattr(hardwared, "get_short_branch", lambda: "testing")
  monkeypatch.setattr(hardwared, "update_jetlink_alert", lambda: None)
  alerts = Mock()
  monkeypatch.setattr(hardwared, "set_offroad_alert_if_changed", alerts)
  monkeypatch.setattr(hardwared, "cloudlog", SimpleNamespace(event=lambda *args, **kwargs: None))
  monkeypatch.setattr(hardwared, "time", SimpleNamespace(monotonic=lambda: 100. + ticks * .5))
  monkeypatch.setattr(hardwared, "messaging", SimpleNamespace(SubMaster=lambda *args, **kwargs: sm,
                                                            PubMaster=lambda *args: publisher, new_message=message))
  monkeypatch.setattr(hardwared, "open", Mock(side_effect=OSError), raising=False)
  # Leave the persistence queue undrained for two scheduled counter saves.
  power_telemetry = Mock(spec=hardwared.PowerTelemetry)
  power_telemetry.value.return_value = 5.
  hardwared.hardware_thread(SimpleNamespace(is_set=lambda: ticks >= 123), SimpleNamespace(get_nowait=Mock(side_effect=hardwared.queue.Empty)),
                            power_telemetry)
  assert len(published) == 123
  assert thermal_reads == [temperature] * 123
  assert [key for key, _ in saved] == ["UptimeOffroad", "UptimeOnroad"] * 2
  assert saved[0][1] == 10.
  if temperature == 60.:
    assert not published[0].started
    assert all(sample.started for sample in published[1:])
    assert saved[1][1] == 10.
    assert saved[3][1] == 69.5
    assert all(sample.thermalStatus == hardwared.ThermalStatus.ok for sample in published)
  else:
    assert all(not sample.started for sample in published)
    assert all(sample.thermalStatus == hardwared.ThermalStatus.critical for sample in published)
    assert all(sample.fanSpeedPercentDesired == 100 for sample in published)
    alerts.assert_any_call("Offroad_TemperatureTooHigh", True, extra_text="120.0C")
