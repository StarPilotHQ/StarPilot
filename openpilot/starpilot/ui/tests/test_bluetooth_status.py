"""The compact Bluetooth glyph follows a fresh adapter state: off, on, or a device connected."""

from concurrent.futures import Future
import json
import subprocess
import unittest
from unittest.mock import patch

from openpilot.starpilot.ui.bluetooth_status import BluetoothStatusSource, read_bluetooth_details, read_bluetooth_state


class FakeExecutor:
  def __init__(self):
    self.future = Future()
    self.calls = 0
    self.closed = False

  def submit(self, _fn):
    self.calls += 1
    return self.future

  def shutdown(self, *, wait, cancel_futures):
    self.closed = True


class TestBluetoothStatus(unittest.TestCase):
  def test_bluez_objects_give_off_on_or_connected(self):
    def objects(powered, connected, powered_type="b"):
      data = {"/org/bluez/hci0": {"org.bluez.Adapter1": {"Powered": {"type": powered_type, "data": powered}}},
              "/org/bluez/hci0/dev_AA": {"org.bluez.Device1": {"Connected": {"type": "b", "data": connected}}}}
      return json.dumps({"type": "a{oa{sa{sv}}}", "data": [data]})
    for output, expected in ((objects(True, True), "connected"), (objects(True, False), "on"), (objects(False, True), "off"),
                             (objects("true", True, "s"), "off"), ("not json", "off"), ('{"data": []}', "off")):
      with self.subTest(output=output), patch("openpilot.starpilot.ui.bluetooth_status.subprocess.run",
                                               return_value=subprocess.CompletedProcess([], 0, output, "")) as run:
        self.assertEqual(read_bluetooth_state(), expected)
        self.assertEqual(run.call_args.kwargs["timeout"], 1.5)
    with patch("openpilot.starpilot.ui.bluetooth_status.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "")):
      self.assertEqual(read_bluetooth_state(), "off")
    with patch("openpilot.starpilot.ui.bluetooth_status.subprocess.run",
               side_effect=subprocess.TimeoutExpired(["busctl"], 1.5)):
      self.assertEqual(read_bluetooth_state(), "off")

  def test_bluez_details_return_connected_device_names(self):
    data = {
      "/org/bluez/hci0": {"org.bluez.Adapter1": {"Powered": {"type": "b", "data": True}}},
      "/org/bluez/hci0/dev_AA": {"org.bluez.Device1": {"Connected": {"type": "b", "data": True},
                                                       "Name": {"type": "s", "data": "iPhone 15"}}},
      "/org/bluez/hci0/dev_BB": {"org.bluez.Device1": {"Connected": {"type": "b", "data": True},
                                                       "Alias": {"type": "s", "data": "AirPods"}}},
      "/org/bluez/hci0/dev_CC": {"org.bluez.Device1": {"Connected": {"type": "b", "data": False},
                                                       "Name": {"type": "s", "data": "Old Phone"}}},
    }
    output = json.dumps({"type": "a{oa{sa{sv}}}", "data": [data]})
    with patch("openpilot.starpilot.ui.bluetooth_status.subprocess.run",
               return_value=subprocess.CompletedProcess([], 0, output, "")):
      self.assertEqual(read_bluetooth_details(), ("connected", ("iPhone 15", "AirPods")))

  def test_pending_failure_and_stale_result_never_show_icon(self):
    clock = [10_000_000_000]
    source = BluetoothStatusSource(clock=lambda: clock[0])
    source.executor.shutdown(wait=False, cancel_futures=True)
    executor = FakeExecutor()
    self.enterContext(patch.object(source, "executor", executor))
    try:
      self.assertEqual(source.snapshot(), "off")  # schedules work without blocking the render
      self.assertEqual(executor.calls, 1)
      executor.future.set_result(("connected", clock[0]))
      self.assertEqual(source.snapshot(), "connected")
      clock[0] += source.MAX_AGE_NS + 1
      self.assertEqual(source.snapshot(), "off")
      self.assertEqual(executor.calls, 2)
      source.close()
      self.assertEqual(source.snapshot(), "off")
      self.assertTrue(executor.closed)
    finally:
      source.close()

  def test_worker_rejects_failed_query(self):
    source = BluetoothStatusSource(request=lambda: (_ for _ in ()).throw(OSError("no adapter")), clock=lambda: 3)
    try:
      self.assertEqual(source._poll(), ("off", 3))
    finally:
      source.close()
    unknown = BluetoothStatusSource(request=lambda: True, clock=lambda: 3)  # ty: ignore[invalid-argument-type]  # unexpected reply type
    try:
      self.assertEqual(unknown._poll(), ("off", 3))
    finally:
      unknown.close()


if __name__ == "__main__":
  unittest.main()
